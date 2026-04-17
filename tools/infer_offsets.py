#!/usr/bin/env python3
import argparse
import dataclasses
import difflib
import pathlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Iterable


OFFSET_RE = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]+)"', re.MULTILINE)
GLOBAL_VALUE_RE = re.compile(r"Global_\d+(?:\.f_\d+)*(?:\[[^\]]+\])?(?:\.f_\d+|\[[^\]]+\])*")
ARRAY_ADD_RE = re.compile(
    r"DATAFILE::DATAARRAY_ADD_[A-Z_]+\((?P<handle>[^,]+),\s*(?P<value>.+?)\);"
)
DICT_CREATE_RE = re.compile(
    r"(?P<handle>\w+->f_\d+(?:\[[^\]]+\])?)\s*=\s*DATAFILE::DATADICT_CREATE_(?P<kind>DICT|ARRAY)\((?P<parent>[^,]+),\s*\"(?P<name>[^\"]+)\"\);"
)


@dataclasses.dataclass
class OffsetEntry:
    name: str
    value: str
    start: int
    end: int


@dataclasses.dataclass
class SourceMatch:
    file: pathlib.Path
    line_no: int
    line: str
    value: str
    handle: str | None
    semantic_path: tuple[str, ...]


@dataclasses.dataclass
class Candidate:
    value: str
    confidence: float
    reason: str
    file: pathlib.Path | None = None
    line_no: int | None = None
    source_rank: int = 999


def canonicalize_global_value(value: str) -> str:
    return re.sub(r"\[[^\]]*?/\*(\d+)\*/\]", r"[i /*\1*/]", value.strip())


def canonicalize_line(line: str) -> str:
    return re.sub(
        r"\[[^\]]*?/\*(\d+)\*/\]",
        r"[i /*\1*/]",
        line.strip(),
    )


def normalize_context_line(line: str, target_value: str | None = None) -> str:
    normalized = canonicalize_line(line)
    if target_value:
        normalized = normalized.replace(canonicalize_global_value(target_value), "__TARGET__")
    normalized = re.sub(r"func_\d+", "func_N", normalized)
    normalized = re.sub(r"Global_\d+", "Global_N", normalized)
    normalized = re.sub(r"\.f_\d+", ".f_N", normalized)
    normalized = re.sub(r"\biVar\d+\b|\bbVar\d+\b|\buVar\d+\b|\bfVar\d+\b|\bVar\d+\b", "VarN", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def path_signature(value: str) -> str:
    signature = canonicalize_global_value(value)
    signature = re.sub(r"Global_\d+", "Global", signature)
    signature = re.sub(r"\[[^\]]+\]", "[idx]", signature)
    signature = re.sub(r"\.f_\d+", ".f", signature)
    return signature


def root_global_number(value: str) -> int | None:
    match = re.match(r"Global_(\d+)", value)
    return int(match.group(1)) if match else None


def root_proximity_bonus(old_value: str, new_value: str) -> float:
    old_root = root_global_number(old_value)
    new_root = root_global_number(new_value)
    if old_root is None or new_root is None:
        return 0.0
    diff = abs(old_root - new_root)
    if diff == 0:
        return 0.18
    if diff <= 5000:
        return 0.16
    if diff <= 50000:
        return 0.10
    if diff <= 250000:
        return 0.05
    return 0.0


def extract_global_values(line: str) -> list[str]:
    return [canonicalize_global_value(match.group(0)) for match in GLOBAL_VALUE_RE.finditer(line)]


def iter_c_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    yield from sorted(root.rglob("*.c"))


@lru_cache(maxsize=None)
def read_lines(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


@lru_cache(maxsize=None)
def parse_offsets_ini_cached(path: pathlib.Path) -> tuple[str, tuple[OffsetEntry, ...]]:
    text = path.read_text(encoding="utf-8")
    entries = tuple(
        OffsetEntry(name=m.group(1), value=m.group(2), start=m.start(2), end=m.end(2))
        for m in OFFSET_RE.finditer(text)
        if m.group(2).startswith("Global_")
    )
    return text, entries


def parse_offsets_ini(path: pathlib.Path) -> tuple[str, list[OffsetEntry]]:
    text, entries = parse_offsets_ini_cached(path)
    return text, list(entries)


def candidate_files_for_value(root: pathlib.Path, value: str) -> list[pathlib.Path]:
    global_match = re.match(r"(Global_\d+)", value)
    if not global_match:
        return list(iter_c_files(root))
    anchor = global_match.group(1)
    result = subprocess.run(
        ["rg", "-l", "-F", anchor, str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return list(iter_c_files(root))
    return [pathlib.Path(line) for line in result.stdout.splitlines() if line.strip()]


@lru_cache(maxsize=None)
def candidate_files_for_value_cached(root: pathlib.Path, value: str) -> tuple[pathlib.Path, ...]:
    return tuple(candidate_files_for_value(root, value))


def path_shape_regex(value: str) -> re.Pattern[str]:
    pattern = re.escape(value)
    pattern = re.sub(r"Global_\\d+", r"Global_\\d+", pattern)
    pattern = re.sub(r"\\\[[^\]]*?/\\\*(\d+)\\\*/\\\]", r"\\[[^\\]]*?/\\*\1\\*/\\]", pattern)
    pattern = pattern.replace(re.escape("[i"), r"\[[^\]]+")
    return re.compile(rf"{pattern}(?!\d)")


def build_symbol_table(lines: list[str]) -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    for line in lines:
        m = DICT_CREATE_RE.search(line)
        if not m:
            continue
        table[m.group("handle").strip()] = (m.group("parent").strip(), m.group("name"))
    return table


@lru_cache(maxsize=None)
def build_symbol_table_for_path(path: pathlib.Path) -> dict[str, tuple[str, str]]:
    return build_symbol_table(read_lines(path))


def resolve_semantic_path(handle: str | None, table: dict[str, tuple[str, str]]) -> tuple[str, ...]:
    if not handle:
        return ()
    parts: list[str] = []
    current = handle.strip()
    seen: set[str] = set()
    while current in table and current not in seen:
        seen.add(current)
        parent, name = table[current]
        parts.append(name)
        current = parent
    parts.reverse()
    return tuple(parts)


def find_old_matches(old_dir: pathlib.Path, value: str) -> list[SourceMatch]:
    results: list[SourceMatch] = []
    shape = path_shape_regex(value)
    for path in candidate_files_for_value_cached(old_dir, value):
        lines = read_lines(path)
        text = "\n".join(lines)
        if value not in text and not shape.search(text):
            continue
        table = build_symbol_table_for_path(path)
        for idx, line in enumerate(lines, start=1):
            match = shape.search(line)
            if not match:
                continue
            matched_value = canonicalize_global_value(match.group(0))
            handle = None
            semantic_path: tuple[str, ...] = ()
            m = ARRAY_ADD_RE.search(line)
            if m:
                handle = m.group("handle").strip()
                semantic_path = resolve_semantic_path(handle, table)
            results.append(
                SourceMatch(
                    file=path,
                    line_no=idx,
                    line=line.strip(),
                    value=matched_value,
                    handle=handle,
                    semantic_path=semantic_path,
                )
            )
    return results


@lru_cache(maxsize=None)
def find_old_matches_cached(old_dir: pathlib.Path, value: str) -> tuple[SourceMatch, ...]:
    return tuple(find_old_matches(old_dir, value))


def find_line_candidates_by_semantic_path(
    new_file: pathlib.Path, semantic_path: tuple[str, ...]
) -> list[Candidate]:
    lines = read_lines(new_file)
    table = build_symbol_table_for_path(new_file)
    candidates: list[Candidate] = []
    for idx, line in enumerate(lines, start=1):
        m = ARRAY_ADD_RE.search(line)
        if not m:
            continue
        handle = m.group("handle").strip()
        current_path = resolve_semantic_path(handle, table)
        if current_path != semantic_path:
            continue
        values = extract_global_values(line)
        if not values:
            continue
        candidates.append(
            Candidate(
                value=values[-1],
                confidence=1.0,
                reason=f"semantic path match: {'/'.join(semantic_path)}",
                file=new_file,
                line_no=idx,
            )
        )
    return candidates


def find_line_candidates_by_shape(new_file: pathlib.Path, value: str) -> list[Candidate]:
    regex = path_shape_regex(value)
    candidates: list[Candidate] = []
    for idx, line in enumerate(read_lines(new_file), start=1):
        m = regex.search(line)
        if not m:
            continue
        matched_value = canonicalize_global_value(m.group(0))
        is_exact = matched_value == canonicalize_global_value(value)
        confidence = 0.97 if is_exact else 0.8
        reason = "exact path still present" if is_exact else "same path shape in same file"
        candidates.append(
            Candidate(
                value=matched_value,
                confidence=confidence,
                reason=reason,
                file=new_file,
                line_no=idx,
            )
        )
    return candidates


def build_context_window(lines: list[str], line_no: int, target_value: str, radius: int = 6) -> str:
    start = max(0, line_no - 1 - radius)
    end = min(len(lines), line_no + radius)
    return "\n".join(normalize_context_line(lines[idx], target_value) for idx in range(start, end))


def get_context_lines(lines: list[str], line_no: int, radius: int = 6) -> list[str]:
    start = max(0, line_no - 1 - radius)
    end = min(len(lines), line_no + radius)
    return [lines[idx] for idx in range(start, end)]


def extract_anchor_tokens(lines: list[str], target_value: str) -> set[str]:
    anchors: set[str] = set()
    for line in lines:
        for match in re.finditer(r"Global_\d+(?:\.f_\d+)+", line):
            token = match.group(0)
            if canonicalize_global_value(token) != canonicalize_global_value(target_value):
                anchors.add(token)
        for match in re.finditer(r"\b[A-Z_]{4,}::[A-Z_]+\b", line):
            anchors.add(match.group(0))
        for match in re.finditer(r'"[^"]{2,32}"', line):
            anchors.add(match.group(0))
    return anchors


def context_similarity(
    old_lines: list[str],
    old_line_no: int,
    old_value: str,
    new_lines: list[str],
    new_line_no: int,
    new_value: str,
    radius: int = 6,
) -> float:
    old_window = build_context_window(old_lines, old_line_no, old_value, radius=radius)
    new_window = build_context_window(new_lines, new_line_no, new_value, radius=radius)
    return difflib.SequenceMatcher(a=old_window, b=new_window).ratio()


def prune_old_matches(old_matches: list[SourceMatch], new_dir: pathlib.Path, limit: int = 12) -> list[SourceMatch]:
    if len(old_matches) <= limit:
        return old_matches

    ranked: list[tuple[tuple[float, float, str, int], SourceMatch]] = []
    seen: set[tuple[str, str]] = set()
    for match in old_matches:
        target_file = new_dir / match.file.name
        if not target_file.exists():
            continue
        lines = read_lines(match.file)
        context_lines = get_context_lines(lines, match.line_no)
        anchors = extract_anchor_tokens(context_lines, match.value)
        normalized_line = normalize_context_line(match.line, match.value)
        dedupe_key = (match.file.name, normalized_line)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rank_key = (
            float(len(anchors)),
            float(len(match.semantic_path)),
            match.file.name,
            -match.line_no,
        )
        ranked.append((rank_key, match))

    if not ranked:
        return old_matches[:limit]

    ranked.sort(reverse=True)
    return [match for _, match in ranked[:limit]]


def find_line_candidates_by_context(new_file: pathlib.Path, old_match: SourceMatch) -> list[Candidate]:
    old_lines = read_lines(old_match.file)
    new_lines = read_lines(new_file)
    old_sig = path_signature(old_match.value)
    old_line_norm = normalize_context_line(old_match.line, old_match.value)
    old_is_assignment = "=" in old_match.line and "==" not in old_match.line
    old_is_return = old_match.line.strip().startswith("return ")
    anchor_tokens = extract_anchor_tokens(get_context_lines(old_lines, old_match.line_no), old_match.value)
    candidates: list[Candidate] = []
    for idx, line in enumerate(new_lines, start=1):
        if "Global_" not in line:
            continue
        if old_is_assignment and ("=" not in line or "==" in line):
            continue
        if old_is_return and not line.strip().startswith("return "):
            continue
        values = extract_global_values(line)
        if not values:
            continue
        context_lines = get_context_lines(new_lines, idx)
        context_blob = "\n".join(context_lines)
        anchor_hits = sum(1 for token in anchor_tokens if token in context_blob)
        if anchor_tokens and anchor_hits == 0:
            continue
        seen_values: set[str] = set()
        for value in values:
            if value in seen_values:
                continue
            seen_values.add(value)
            value_sig = path_signature(value)
            signature_bonus = 0.08 if value_sig == old_sig else 0.0
            same_depth_bonus = 0.04 if value.count(".f_") == old_match.value.count(".f_") else 0.0
            anchor_bonus = 0.12 * min(anchor_hits / max(len(anchor_tokens), 1), 1.0)
            proximity_bonus = root_proximity_bonus(old_match.value, value)
            new_line_norm = normalize_context_line(line, value)
            line_score = difflib.SequenceMatcher(a=old_line_norm, b=new_line_norm).ratio()
            if line_score < 0.58 and signature_bonus == 0.0:
                continue
            local_window_score = context_similarity(old_lines, old_match.line_no, old_match.value, new_lines, idx, value, radius=2)
            broad_window_score = context_similarity(old_lines, old_match.line_no, old_match.value, new_lines, idx, value, radius=6)
            score = min(
                (0.25 * line_score)
                + (0.45 * local_window_score)
                + (0.20 * broad_window_score)
                + signature_bonus
                + same_depth_bonus
                + anchor_bonus
                + proximity_bonus,
                0.99,
            )
            if score < 0.72:
                continue
            candidates.append(
                Candidate(
                    value=value,
                    confidence=score,
                    reason="context match in same file",
                    file=new_file,
                    line_no=idx,
                )
            )
    return candidates


def candidate_reason_rank(reason: str) -> int:
    if reason.startswith("semantic path match"):
        return 0
    if reason == "exact path still present":
        return 1
    if reason == "same path shape in same file":
        return 2
    return 3


def is_safe_candidate(current_value: str, candidate: Candidate) -> bool:
    if candidate.reason.startswith("semantic path match"):
        return True
    if candidate.reason == "exact path still present":
        return True
    if candidate.confidence < 0.995:
        return False
    if root_proximity_bonus(current_value, candidate.value) < 0.16:
        return False
    if candidate.reason != "context match in same file":
        return False
    if "[" in current_value or ".f_" in current_value:
        return False
    return True


def apply_source_bonus(candidates: list[Candidate], source_rank: int) -> list[Candidate]:
    if not candidates:
        return candidates
    adjusted: list[Candidate] = []
    for candidate in candidates:
        adjusted.append(
            Candidate(
                value=candidate.value,
                confidence=candidate.confidence,
                reason=candidate.reason,
                file=candidate.file,
                line_no=candidate.line_no,
                source_rank=source_rank,
            )
        )
    return adjusted


def infer_from_match(old_match: SourceMatch, new_dir: pathlib.Path, source_rank: int = 0) -> list[Candidate]:
    relative = old_match.file.relative_to(old_match.file.parents[1])
    same_name_target = new_dir / relative.name
    candidates: list[Candidate] = []
    if same_name_target.exists():
        if old_match.semantic_path:
            semantic_candidates = find_line_candidates_by_semantic_path(same_name_target, old_match.semantic_path)
            if semantic_candidates:
                shape_candidates = find_line_candidates_by_shape(same_name_target, old_match.value)
                candidates.extend(semantic_candidates)
                candidates.extend([c for c in shape_candidates if c.value == old_match.value])
                return apply_source_bonus(candidates, source_rank)
        candidates.extend(find_line_candidates_by_shape(same_name_target, old_match.value))
        candidates.extend(find_line_candidates_by_context(same_name_target, old_match))
    return apply_source_bonus(candidates, source_rank)


def infer_candidates(old_dir: pathlib.Path, new_dir: pathlib.Path, value: str) -> tuple[list[SourceMatch], list[Candidate]]:
    old_matches = list(find_old_matches_cached(old_dir, value))
    semantic_matches = [match for match in old_matches if match.semantic_path]
    if semantic_matches:
        old_matches = semantic_matches
    else:
        old_matches = prune_old_matches(old_matches, new_dir)
    all_candidates: list[Candidate] = []
    for idx, match in enumerate(old_matches):
        all_candidates.extend(infer_from_match(match, new_dir, source_rank=idx))

    if not all_candidates:
        return old_matches, []

    best_by_value: dict[str, Candidate] = {}
    counts = Counter(candidate.value for candidate in all_candidates)
    reasons = defaultdict(list)
    files = defaultdict(list)
    for candidate in all_candidates:
        reasons[candidate.value].append(candidate.reason)
        if candidate.file and candidate.line_no:
            files[candidate.value].append(f"{candidate.file.name}:{candidate.line_no}")
        current = best_by_value.get(candidate.value)
        bonus = 0.02 * min(counts[candidate.value] - 1, 3)
        merged = Candidate(
            value=candidate.value,
            confidence=min(candidate.confidence + bonus, 1.0),
            reason="; ".join(dict.fromkeys(reasons[candidate.value])),
            file=candidate.file,
            line_no=candidate.line_no,
            source_rank=min(candidate.source_rank, current.source_rank) if current else candidate.source_rank,
        )
        if current is None or (
            merged.confidence,
            -merged.source_rank,
            -candidate_reason_rank(merged.reason),
        ) > (
            current.confidence,
            -current.source_rank,
            -candidate_reason_rank(current.reason),
        ):
            best_by_value[candidate.value] = merged

    old_root = root_global_number(value)
    merged_candidates = sorted(
        best_by_value.values(),
        key=lambda item: (
            candidate_reason_rank(item.reason),
            item.source_rank,
            -item.confidence,
            abs((root_global_number(item.value) or 0) - (old_root or 0)),
            item.value,
        ),
    )
    return old_matches, merged_candidates


def update_ini_value(text: str, entry: OffsetEntry, new_value: str) -> str:
    return text[: entry.start] + new_value + text[entry.end :]


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer updated GTA offset globals from old/new decompiled scripts.")
    parser.add_argument("--ini", default="offsets.ini")
    parser.add_argument("--old-dir", default="old")
    parser.add_argument("--new-dir", default="new")
    parser.add_argument("--offset", action="append", help="Only inspect one or more OFFSET_* entries.")
    parser.add_argument("--apply", action="store_true", help="Write confident matches back into offsets.ini.")
    parser.add_argument("--apply-safe", action="store_true", help="Write only safe matches back into offsets.ini.")
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--top", type=int, default=2, help="How many candidates to print per offset.")
    args = parser.parse_args()

    ini_path = pathlib.Path(args.ini)
    old_dir = pathlib.Path(args.old_dir)
    new_dir = pathlib.Path(args.new_dir)
    if not ini_path.exists():
        print(f"missing ini file: {ini_path}", file=sys.stderr)
        return 1
    if not old_dir.exists():
        print(f"missing old directory: {old_dir}", file=sys.stderr)
        return 1
    if not new_dir.exists():
        print(f"missing new directory: {new_dir}", file=sys.stderr)
        return 1
    ini_text, entries = parse_offsets_ini(ini_path)

    if args.offset:
        requested = set(args.offset)
        entries = [entry for entry in entries if entry.name in requested]
        if not entries:
            print(f"requested offsets not found in {ini_path}", file=sys.stderr)
            return 1

    replacements: dict[str, str] = {}
    for entry in entries:
        old_matches, candidates = infer_candidates(old_dir, new_dir, entry.value)
        print(f"{entry.name}")
        print(f"  current: {entry.value}")
        if old_matches:
            example = old_matches[0]
            semantic = "/".join(example.semantic_path) if example.semantic_path else "-"
            print(f"  old hit: {example.file.name}:{example.line_no} semantic={semantic} matches={len(old_matches)}")
        else:
            print("  old hit: none")
        if not candidates:
            print("  candidate: none")
            continue
        top = candidates[0]
        location = f" @ {top.file.name}:{top.line_no}" if top.file and top.line_no else ""
        print(f"  candidate: {top.value} confidence={top.confidence:.2f}{location}")
        print(f"  reason: {top.reason}")
        if args.apply and top.confidence >= args.min_confidence and top.value != entry.value:
            replacements[entry.name] = top.value
        elif args.apply_safe and is_safe_candidate(entry.value, top) and top.value != entry.value:
            replacements[entry.name] = top.value
        for extra in candidates[1 : max(args.top, 1)]:
            print(f"  alt: {extra.value} confidence={extra.confidence:.2f}")

    if (args.apply or args.apply_safe) and replacements:
        updated_text = ini_text
        for entry in reversed(entries):
            if entry.name not in replacements:
                continue
            updated_text = update_ini_value(updated_text, entry, replacements[entry.name])
        ini_path.write_text(updated_text, encoding="utf-8")
        print(f"\nupdated {len(replacements)} entries in {ini_path}")
    elif args.apply or args.apply_safe:
        print("\nno entries met apply threshold")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
