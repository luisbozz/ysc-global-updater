#!/usr/bin/env python3
import argparse
import dataclasses
import difflib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Iterable


OFFSET_RE = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]+)"', re.MULTILINE)
GLOBAL_VALUE_RE = re.compile(r"Global_\d+(?:\.f_\d+)*(?:\[[^\]]+\])?(?:\.f_\d+|\[[^\]]+\])*")
GLOBAL_PATH_TOKEN_RE = re.compile(r"Global_\d+|\.f_\d+|\[[^\]]+\]")
LOCAL_VALUE_RE = re.compile(r"(?:[fiu]?Local|Local)_\d+(?:\.f_\d+)*(?:\[[^\]]+\])?(?:\.f_\d+|\[[^\]]+\])*")
GLOBAL_ALIAS_RE = re.compile(r"^\d+(?:\.f_\d+)*(?:\[[^\]]+\])?(?:\.f_\d+|\[[^\]]+\])*$")
LOCAL_PATH_RE = re.compile(r"^(?:[fiu]?Local|Local)_\d+(?:\.f_\d+)*(?:\[[^\]]+\])?(?:\.f_\d+|\[[^\]]+\])*$")
RELATIVE_FIELD_VALUE_RE = re.compile(r"^f_\d+$")
STRUCT_DECL_RE = re.compile(r"struct<(?P<size>\d+)>\s+(?P<name>(?:[fiu]?Local|Local)_\d+)")
LOCAL_FIELD_ACCESS_RE = re.compile(r"\b(Local_\d+)\.f_(\d+)")
LOCAL_REF_TOKEN_RE = re.compile(r"&(?P<name>(?:[fiu]?Local|Local)_\d+)")
LOCAL_SWITCH_RE = re.compile(r"switch \((iLocal_\d+)\)")
CASE_LABEL_RE = re.compile(r"^\s*case\s+(-?\d+)\s*:")
FUNCTION_START_RE = re.compile(r"^\s*(?:[\w<>]+\s+)*?(func_\d+)\s*\(")
FUNCTION_CALL_RE = re.compile(r"\b(func_\d+)\s*\(")
ARRAY_ADD_RE = re.compile(
    r"DATAFILE::DATAARRAY_ADD_[A-Z_]+\((?P<handle>[^,]+),\s*(?P<value>.+?)\);"
)
DICT_CREATE_RE = re.compile(
    r"(?P<handle>\w+->f_\d+(?:\[[^\]]+\])?)\s*=\s*DATAFILE::DATADICT_CREATE_(?P<kind>DICT|ARRAY)\((?P<parent>[^,]+),\s*\"(?P<name>[^\"]+)\"\);"
)


# The decompiled creator files are multi-gigabyte across old/new. Keep the hot
# caches intentionally small so a full scan cannot retain too many parsed files
# at once and exhaust WSL memory.
READ_LINES_CACHE_SIZE = 2
READ_TEXT_CACHE_SIZE = 2
PARSE_CACHE_SIZE = 4
SYMBOL_TABLE_CACHE_SIZE = 2
STRUCT_DECL_CACHE_SIZE = 2
CANDIDATE_FILE_CACHE_SIZE = 128
OLD_MATCH_CACHE_SIZE = 32


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


CURRENT_CREATOR_RELATIVE_FIELD_OVERRIDES = {
    "OFFSET_current_creator_worker_offset_menu": "f_533",
}

VERIFIED_SPECIAL_OFFSET_OVERRIDES = {
    "OFFSET_launch_creator_local_3": "Global_33776",
    "OFFSET_check_creator": "Global_1925601",
    "OFFSET_hide_creator_menu": "Global_24529.f_9243",
}

CURRENT_CREATOR_RELATIVE_FIELD_STABLE = {
    "OFFSET_current_creator_worker_offset_refresh",
    "OFFSET_current_creator_worker_heading",
    "OFFSET_current_creator_worker_pos",
    "OFFSET_current_creator_cam_heading_offset",
    "OFFSET_current_creator_pre_menu_gm",
    "OFFSET_current_creator_pre_test1",
    "OFFSET_current_creator_pre_test2",
    "OFFSET_current_creator_pre_color",
    "OFFSET_current_creator_pre_category_num",
    "OFFSET_current_creator_pre_prop_num",
    "OFFSET_current_creator_pre_publish",
    "OFFSET_current_creator_pre_previous_menu",
    "OFFSET_current_creator_pre_current_menu",
    "OFFSET_current_creator_pre_idk",
    "OFFSET_current_creator_pre_place_object_type",
}

FAST_REPORT_TRUSTED_PREFIXES: dict[str, tuple[str, ...]] = {
    "actor": ("Global_4980736.f_90320[i /*1269*/].",),
    "cps": (
        "Global_4718592.f_114994[i /*65*/].",
        "Global_4718592.f_113952",
        "Global_4718592.f_119442",
        "Global_4718592.f_113926",
    ),
    "custom": (
        "Global_1826920",
        "Global_1826921",
        "Global_1826922",
    ),
    "ddblip": ("Global_4980736.f_214914[i /*118*/].",),
    "emp": ("Global_4718592.f_202474",),
    "goto": ("Global_4980736.f_5[i /*336*/].",),
    "kill": (
        "Global_4718592.f_114052[i /*55*/].",
        "Global_4718592.f_114988",
    ),
    "pa": (
        "Global_4718592.f_3605[i /*26968*/].f_3153",
        "Global_4718592.f_3605[i /*26968*/].f_3766",
        "Global_4718592.f_3605[i /*26968*/].f_19565",
    ),
    "ptemp": (
        "Global_4718592.f_190242[i /*248*/]",
        "Global_4718592.f_192723",
    ),
    "published": ("Global_993502.f_4",),
    "PwrUp": ("Global_4718592.f_203764",),
    "saved": ("Global_1011388.f_33",),
    "SMS": ("Global_4718592.f_112337[i /*44*/].",),
    "surv": ("Global_4718592.f_192213",),
    "tp": ("Global_4718592.f_197978[i /*124*/].",),
    "obj": ("Global_4980736.f_7065[i /*648*/].",),
    "veh": ("Global_4980736.f_68415[i /*626*/].",),
    "weap": ("Global_4980736.f_57917[i /*172*/].",),
    "zones": ("Global_4718592.f_218946[i /*192*/].",),
}


def is_relative_field_value(value: str) -> bool:
    return bool(RELATIVE_FIELD_VALUE_RE.fullmatch(value.strip()))


def is_current_creator_relative_field_offset(offset_name: str | None, value: str) -> bool:
    if not offset_name or not offset_name.startswith("OFFSET_current_creator_"):
        return False
    if not is_relative_field_value(value):
        return False
    return (
        offset_name in CURRENT_CREATOR_RELATIVE_FIELD_OVERRIDES
        or offset_name in CURRENT_CREATOR_RELATIVE_FIELD_STABLE
    )


def is_trusted_fast_report_offset(offset_name: str, value: str) -> bool:
    family = offset_family(offset_name)
    prefixes = FAST_REPORT_TRUSTED_PREFIXES.get(family)
    if not prefixes:
        return False
    normalized = normalize_offset_value_for_search(value)
    return any(normalized.startswith(prefix) for prefix in prefixes)


def is_global_alias_value(value: str) -> bool:
    return bool(GLOBAL_ALIAS_RE.fullmatch(value.strip()))


def is_local_value(value: str) -> bool:
    return bool(LOCAL_PATH_RE.fullmatch(value.strip()))


def is_supported_offset_value(value: str, offset_name: str | None = None) -> bool:
    stripped = value.strip()
    return (
        stripped.startswith("Global_")
        or is_local_value(stripped)
        or is_global_alias_value(stripped)
        or is_current_creator_relative_field_offset(offset_name, stripped)
    )


def canonicalize_global_value(value: str) -> str:
    return re.sub(r"\[[^\]]*?/\*(\d+)\*/\]", r"[i /*\1*/]", value.strip())


def canonicalize_local_value(value: str) -> str:
    canonical = re.sub(r"\b(?:[fiu]?Local|Local)_(\d+)", r"Local_\1", value.strip())
    return re.sub(r"\[[^\]]*?/\*(\d+)\*/\]", r"[i /*\1*/]", canonical)


def normalize_offset_value_for_search(value: str) -> str:
    stripped = value.strip()
    if is_relative_field_value(stripped):
        return stripped
    if is_global_alias_value(stripped):
        return canonicalize_global_value(f"Global_{stripped}")
    if is_local_value(stripped):
        return canonicalize_local_value(stripped)
    return canonicalize_global_value(stripped)


def current_creator_kind(offset_name: str | None) -> str | None:
    if not offset_name or not offset_name.startswith("OFFSET_current_creator_"):
        return None
    suffix = offset_name[len("OFFSET_current_creator_") :]
    for kind in ("worker", "cam_heading", "pre", "test", "refresh"):
        if suffix.startswith(f"{kind}_"):
            return kind
    return None


def current_creator_mode(offset_name: str | None) -> str | None:
    if not offset_name or not offset_name.startswith("OFFSET_current_creator_"):
        return None
    for mode in ("capture", "survival", "lts", "dm", "race", "mission"):
        token = f"_{mode}"
        if offset_name.endswith(token) or token in offset_name:
            return mode
    return None


def current_creator_prefix(offset_name: str | None) -> str | None:
    kind = current_creator_kind(offset_name)
    if kind == "worker":
        return "fLocal_"
    if kind in {"pre", "cam_heading"}:
        return "uLocal_"
    return None


TEAM_VERIFIED_ZERO_SHIFT = {
    "OFFSET_stpos",
    "OFFSET_sia",
    "OFFSET_inv",
    "OFFSET_invsw",
    "OFFSET_plyl",
    "OFFSET_plvrl",
    "OFFSET_mts",
    "OFFSET_mslr",
    "OFFSET_ppk",
}
TEAM_VERIFIED_ZERO_SHIFT.update({f"OFFSET_inv{i}" for i in range(2, 6)})

TEAM_VERIFIED_PLUS_ONE = {
    "OFFSET_irbs",
    "OFFSET_irfbs",
    "OFFSET_tmbts",
    "OFFSET_tmbt2",
    "OFFSET_tmbt3",
    "OFFSET_tmbt4",
    "OFFSET_rloft",
    "OFFSET_rloftv",
    "OFFSET_mrtl",
    "OFFSET_mpaumxscr",
    "OFFSET_boud",
    "OFFSET_bla",
    "OFFSET_patm",
    "OFFSET_bla2",
    "OFFSET_bnd2",
    "OFFSET_vehrsp",
    "OFFSET_fail",
    "OFFSET_fail_txt",
    "OFFSET_pcbd",
    "OFFSET_tmt",
    "OFFSET_tms",
    "OFFSET_trafpb",
    "OFFSET_vss",
    "OFFSET_shdtxt",
    "OFFSET_minspd",
    "OFFSET_mspdlp",
    "OFFSET_mspdsv",
    "OFFSET_fsdtmr",
    "OFFSET_frndf",
    "OFFSET_mspdmx",
    "OFFSET_vehdmro",
    "OFFSET_vehdmri",
    "OFFSET_minv",
    "OFFSET_spar",
    "OFFSET_gbnum",
    "OFFSET_gbngn",
    "OFFSET_gblgn",
    "OFFSET_gbcol",
    "OFFSET_gbdel",
    "OFFSET_gbaie",
    "OFFSET_gbmax",
    "OFFSET_gbngm",
    "OFFSET_gblgm",
    "OFFSET_gbvhl",
    "OFFSET_gacc",
    "OFFSET_gfld",
    "OFFSET_gbat",
    "OFFSET_gbfnr",
    "OFFSET_gbv1",
    "OFFSET_gbv2",
    "OFFSET_gbaw",
}
TEAM_VERIFIED_PLUS_ONE.update({f"OFFSET_irbs{i}" for i in range(2, 19)})
TEAM_VERIFIED_PLUS_ONE.update({f"OFFSET_tblpv{i}" for i in range(1, 5)})
TEAM_VERIFIED_PLUS_ONE.update({f"OFFSET_minv{i}" for i in range(2, 6)})

TEAM_VERIFIED_SPECIAL_VALUES = {
    "OFFSET_nrl": "Global_4718592.f_3605[i /*26968*/].f_60",
}


def rewrite_team_value(value: str, field_delta: int) -> str | None:
    if "Global_4718592.f_3605" not in value or "/*26949*/" not in value:
        return None

    updated = value.replace("/*26949*/", "/*26968*/")
    if field_delta == 0:
        return updated

    matches = list(re.finditer(r"\.f_(\d+)", updated))
    if not matches:
        return None
    target = matches[-1]
    shifted = str(int(target.group(1)) + field_delta)
    return updated[: target.start(1)] + shifted + updated[target.end(1) :]


def infer_team_block_candidates(new_dir: pathlib.Path, value: str, offset_name: str | None) -> list[Candidate]:
    if not offset_name:
        return []

    if offset_name in TEAM_VERIFIED_SPECIAL_VALUES:
        candidate_value = TEAM_VERIFIED_SPECIAL_VALUES[offset_name]
    elif offset_name in TEAM_VERIFIED_ZERO_SHIFT:
        candidate_value = rewrite_team_value(value, 0)
    elif offset_name in TEAM_VERIFIED_PLUS_ONE:
        candidate_value = rewrite_team_value(value, 1)
    else:
        return []

    if not candidate_value:
        return []

    source_file = new_dir / "fm_capture_creator.c"
    return [
        Candidate(
            value=candidate_value,
            confidence=0.999,
            reason="team block verified mapping",
            file=source_file if source_file.exists() else None,
            line_no=1 if source_file.exists() else None,
        )
    ]


def present_value_for_ini(original_value: str, candidate_value: str, offset_name: str | None = None) -> str:
    if is_relative_field_value(original_value):
        return candidate_value
    if is_global_alias_value(original_value):
        return re.sub(r"^Global_", "", candidate_value)
    if is_local_value(original_value):
        forced_prefix = current_creator_prefix(offset_name)
        candidate_match = re.match(r"^Local_(\d+)(.*)$", canonicalize_local_value(candidate_value))
        if forced_prefix and candidate_match:
            return f"{forced_prefix}{candidate_match.group(1)}{candidate_match.group(2)}"
        prefix_match = re.match(r"^((?:[fiu]?Local|Local)_)\d+", original_value)
        if prefix_match and candidate_match:
            return f"{prefix_match.group(1)}{candidate_match.group(1)}{candidate_match.group(2)}"
    return candidate_value


def canonicalize_line(line: str) -> str:
    normalized = re.sub(
        r"\[[^\]]*?/\*(\d+)\*/\]",
        r"[i /*\1*/]",
        line.strip(),
    )
    return re.sub(r"\b(?:[fiu]?Local|Local)_(\d+)", r"Local_\1", normalized)


def normalize_context_line(line: str, target_value: str | None = None) -> str:
    normalized = canonicalize_line(line)
    if target_value:
        normalized = normalized.replace(normalize_offset_value_for_search(target_value), "__TARGET__")
    normalized = re.sub(r"func_\d+", "func_N", normalized)
    normalized = re.sub(r"Global_\d+", "Global_N", normalized)
    normalized = re.sub(r"Local_\d+", "Local_N", normalized)
    normalized = re.sub(r"\.f_\d+", ".f_N", normalized)
    normalized = re.sub(r"\biVar\d+\b|\bbVar\d+\b|\buVar\d+\b|\bfVar\d+\b|\bVar\d+\b", "VarN", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def path_signature(value: str) -> str:
    signature = normalize_offset_value_for_search(value)
    signature = re.sub(r"Global_\d+", "Global", signature)
    signature = re.sub(r"Local_\d+", "Local", signature)
    signature = re.sub(r"\[[^\]]+\]", "[idx]", signature)
    signature = re.sub(r"\.f_\d+", ".f", signature)
    return signature


def root_value_info(value: str) -> tuple[str, int] | None:
    normalized = normalize_offset_value_for_search(value)
    match = re.match(r"Global_(\d+)", normalized)
    if match:
        return ("global", int(match.group(1)))
    match = re.match(r"Local_(\d+)", normalized)
    if match:
        return ("local", int(match.group(1)))
    return None


def root_proximity_bonus(old_value: str, new_value: str) -> float:
    old_root = root_value_info(old_value)
    new_root = root_value_info(new_value)
    if old_root is None or new_root is None or old_root[0] != new_root[0]:
        return 0.0
    diff = abs(old_root[1] - new_root[1])
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


@lru_cache(maxsize=512)
def global_parent_values(value: str) -> tuple[str, ...]:
    normalized = canonicalize_global_value(value)
    tokens = GLOBAL_PATH_TOKEN_RE.findall(normalized)
    if not tokens or not tokens[0].startswith("Global_"):
        return (normalized,)
    current = ""
    parents: list[str] = []
    for token in tokens:
        current += token
        parents.append(current)
    return tuple(reversed(parents))


def extract_local_values(line: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for match in LOCAL_VALUE_RE.finditer(line):
        token = canonicalize_local_value(match.group(0))
        if token not in seen:
            values.append(token)
            seen.add(token)
        root_match = re.match(r"(Local_\d+)", token)
        if root_match and root_match.group(1) not in seen:
            values.append(root_match.group(1))
            seen.add(root_match.group(1))
    return values


def extract_values_for_kind(line: str, value: str) -> list[str]:
    normalized = normalize_offset_value_for_search(value)
    if normalized.startswith("Local_"):
        return extract_local_values(line)
    return extract_global_values(line)


def iter_c_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    yield from sorted(root.rglob("*.c"))


@lru_cache(maxsize=READ_LINES_CACHE_SIZE)
def read_lines(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


@lru_cache(maxsize=READ_TEXT_CACHE_SIZE)
def read_text_cached(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


@lru_cache(maxsize=PARSE_CACHE_SIZE)
def parse_quoted_offsets_cached(path: pathlib.Path) -> tuple[str, tuple[OffsetEntry, ...]]:
    text = path.read_text(encoding="utf-8")
    entries = tuple(
        OffsetEntry(name=m.group(1), value=m.group(2), start=m.start(2), end=m.end(2))
        for m in OFFSET_RE.finditer(text)
    )
    return text, entries


@lru_cache(maxsize=PARSE_CACHE_SIZE)
def parse_offsets_ini_cached(path: pathlib.Path) -> tuple[str, tuple[OffsetEntry, ...]]:
    text, all_entries = parse_quoted_offsets_cached(path)
    entries = tuple(
        entry
        for entry in all_entries
        if is_supported_offset_value(entry.value, entry.name)
    )
    return text, entries


def parse_offsets_ini(path: pathlib.Path) -> tuple[str, list[OffsetEntry]]:
    text, entries = parse_offsets_ini_cached(path)
    return text, list(entries)


def parse_quoted_offsets(path: pathlib.Path) -> tuple[str, list[OffsetEntry]]:
    text, entries = parse_quoted_offsets_cached(path)
    return text, list(entries)


def offset_family(offset_name: str) -> str:
    suffix = offset_name.removeprefix("OFFSET_")
    for prefix in ("current_creator_", "launch_creator_local_"):
        if suffix.startswith(prefix):
            return prefix.rstrip("_")
    return suffix.split("_", 1)[0]


def candidate_search_anchors(value: str) -> list[str]:
    search_value = normalize_offset_value_for_search(value)
    anchors: list[str] = []

    if search_value.startswith("Global_"):
        prefix = re.split(r"\[", search_value, maxsplit=1)[0]
        anchors.append(prefix)
        if "." in prefix:
            anchors.append(prefix.rsplit(".", 1)[0])
        root_match = re.match(r"Global_\d+", search_value)
        if root_match:
            anchors.append(root_match.group(0))
    elif search_value.startswith("Local_"):
        root_match = re.match(r"Local_\d+", search_value)
        if root_match:
            anchors.append(root_match.group(0))
    deduped: list[str] = []
    for anchor in anchors:
        if anchor and anchor not in deduped:
            deduped.append(anchor)
    return deduped


def rg_files_for_anchor(root: pathlib.Path, anchor: str) -> list[pathlib.Path] | None:
    result = subprocess.run(
        ["rg", "-l", "-F", anchor, str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return None
    return [pathlib.Path(line) for line in result.stdout.splitlines() if line.strip()]


def preferred_creator_mode_files(root: pathlib.Path, offset_name: str | None) -> list[pathlib.Path]:
    mode_to_files = {
        "capture": ["fm_capture_creator.c"],
        "survival": ["fm_survival_creator.c"],
        "lts": ["fm_lts_creator.c"],
        "dm": ["fm_deathmatch_creator.c"],
        "race": ["fm_race_creator.c"],
        "mission": ["fm_mission_creator.c", "public_mission_creator.c"],
    }
    matched_mode = current_creator_mode(offset_name)
    if matched_mode is None:
        return []

    files = [root / name for name in mode_to_files[matched_mode]]
    return [path for path in files if path.exists()]


def preferred_creator_files(root: pathlib.Path, offset_name: str | None, value: str) -> list[pathlib.Path]:
    if not is_local_value(value):
        return []
    return preferred_creator_mode_files(root, offset_name)


def vector_component_parent_value(value: str, offset_name: str | None) -> str | None:
    if not offset_name:
        return None
    suffix = offset_name.lower()
    if not suffix.endswith(("locx", "locy", "locz", "posx", "posy", "posz")):
        return None
    normalized = normalize_offset_value_for_search(value)
    match = re.fullmatch(r"(.+)\.f_[012]", normalized)
    if not match:
        return None
    return match.group(1)


def candidate_files_for_value(root: pathlib.Path, value: str) -> list[pathlib.Path]:
    anchors = candidate_search_anchors(value)
    if not anchors:
        return list(iter_c_files(root))

    for anchor in anchors:
        matches = rg_files_for_anchor(root, anchor)
        if matches is None:
            return list(iter_c_files(root))
        if matches:
            return matches
    return []


@lru_cache(maxsize=CANDIDATE_FILE_CACHE_SIZE)
def candidate_files_for_value_cached(root: pathlib.Path, value: str) -> tuple[pathlib.Path, ...]:
    return tuple(candidate_files_for_value(root, value))


@lru_cache(maxsize=256)
def path_shape_regex(value: str) -> re.Pattern[str]:
    return re.compile(rf"{path_shape_pattern(value)}(?!\d)")


@lru_cache(maxsize=256)
def path_shape_pattern(value: str) -> str:
    search_value = normalize_offset_value_for_search(value)
    pattern = re.escape(search_value)
    pattern = re.sub(r"Global_\\d+", r"Global_\\d+", pattern)
    pattern = re.sub(r"Local_\\d+", r"(?:[fiu]?Local|Local)_\\d+", pattern)
    pattern = re.sub(r"\\\[[^\]]*?/\\\*(\d+)\\\*/\\\]", r"\\[[^\\]]*?/\\*\1\\*/\\]", pattern)
    pattern = pattern.replace(re.escape("[i"), r"\[[^\]]+")
    return pattern


def current_value_present_in_new(new_dir: pathlib.Path, value: str, offset_name: str | None = None) -> bool:
    if is_relative_field_value(value):
        return False
    regex = path_shape_regex(value)
    files = preferred_creator_files(new_dir, offset_name, value) or list(candidate_files_for_value_cached(new_dir, value))
    fallback_parent = vector_component_parent_value(value, offset_name)
    parent_regex = path_shape_regex(fallback_parent) if fallback_parent else None
    for path in files:
        text = read_text_cached(path)
        if regex.search(text):
            return True
        if parent_regex is not None and parent_regex.search(text):
            return True
    return False


def find_present_entries_in_new(new_dir: pathlib.Path, entries: list[OffsetEntry]) -> set[str]:
    requested_global_by_value: defaultdict[str, set[str]] = defaultdict(set)
    requested_local_entries: list[OffsetEntry] = []
    anchor_patterns: list[str] = []
    for entry in entries:
        if is_relative_field_value(entry.value):
            continue
        normalized = normalize_offset_value_for_search(entry.value)
        if normalized.startswith("Local_"):
            requested_local_entries.append(entry)
            continue
        requested_global_by_value[normalized].add(entry.name)
        anchors = candidate_search_anchors(entry.value)
        if anchors:
            anchor_patterns.append(anchors[0])
        fallback_parent = vector_component_parent_value(entry.value, entry.name)
        if fallback_parent:
            normalized_parent = normalize_offset_value_for_search(fallback_parent)
            requested_global_by_value[normalized_parent].add(entry.name)
            parent_anchors = candidate_search_anchors(fallback_parent)
            if parent_anchors:
                anchor_patterns.append(parent_anchors[0])

    if not requested_global_by_value and not requested_local_entries:
        return set()

    found_names: set[str] = set()
    if requested_global_by_value and anchor_patterns:
        unique_patterns = list(dict.fromkeys(anchor_patterns))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as pattern_file:
            pattern_path = pathlib.Path(pattern_file.name)
            for pattern in unique_patterns:
                pattern_file.write(pattern)
                pattern_file.write("\n")
        try:
            cmd = [
                "rg",
                "--fixed-strings",
                "--no-filename",
                "--no-line-number",
                "-f",
                str(pattern_path),
                str(new_dir),
            ]
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as process:
                assert process.stdout is not None
                for matched_line in process.stdout:
                    for value in extract_global_values(matched_line):
                        for candidate_value in global_parent_values(value):
                            names = requested_global_by_value.get(candidate_value)
                            if names:
                                found_names.update(names)
                stderr = process.stderr.read() if process.stderr is not None else ""
                return_code = process.wait()
            if return_code not in (0, 1):
                raise RuntimeError(stderr.strip() or f"rg failed with code {return_code}")
        except (FileNotFoundError, RuntimeError):
            found_names.clear()
            remaining_global_names: set[str] = {
                name for names in requested_global_by_value.values() for name in names
            }
            for path in iter_c_files(new_dir):
                with path.open(encoding="utf-8", errors="ignore") as handle:
                    for raw_line in handle:
                        for value in extract_global_values(raw_line):
                            for candidate_value in global_parent_values(value):
                                names = requested_global_by_value.get(candidate_value)
                                if names:
                                    found_names.update(names)
                        if len(found_names) >= len(remaining_global_names):
                            break
        finally:
            pattern_path.unlink(missing_ok=True)

    for entry in requested_local_entries:
        if current_value_present_in_new(new_dir, entry.value, entry.name):
            found_names.add(entry.name)

    return found_names


def build_symbol_table(lines: list[str]) -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    for line in lines:
        m = DICT_CREATE_RE.search(line)
        if not m:
            continue
        table[m.group("handle").strip()] = (m.group("parent").strip(), m.group("name"))
    return table


@lru_cache(maxsize=SYMBOL_TABLE_CACHE_SIZE)
def build_symbol_table_for_path(path: pathlib.Path) -> dict[str, tuple[str, str]]:
    return build_symbol_table(read_lines(path))


def creator_struct_declarations(lines: list[str]) -> dict[str, int]:
    declarations: dict[str, int] = {}
    for line in lines:
        match = STRUCT_DECL_RE.search(line)
        if not match:
            continue
        name = canonicalize_local_value(match.group("name"))
        declarations[re.match(r"Local_\d+", name).group(0)] = int(match.group("size"))
    return declarations


@lru_cache(maxsize=STRUCT_DECL_CACHE_SIZE)
def creator_struct_declarations_for_path(path: pathlib.Path) -> dict[str, int]:
    return creator_struct_declarations(read_lines(path))


def local_root_from_name(name: str) -> str | None:
    canonical = canonicalize_local_value(name)
    match = re.match(r"Local_\d+", canonical)
    if not match:
        return None
    return match.group(0)


def infer_current_creator_root_candidates(new_dir: pathlib.Path, offset_name: str) -> list[Candidate]:
    kind = current_creator_kind(offset_name)
    if kind not in {"worker", "pre"}:
        return []

    if kind == "worker":
        field_weights = {562: 4.0, 530: 4.0, 533: 1.5, 565: 1.5, 25: 0.5, 26: 0.5}
        min_size = 800
        max_size = 1500
    else:
        field_weights = {598: 4.0, 271: 3.5, 272: 4.0, 597: 1.5, 534: 1.5, 759: 0.5, 760: 0.5, 981: 0.5, 982: 0.5, 756: 0.5}
        min_size = 5000
        max_size = 9000

    candidates: list[Candidate] = []
    for preferred in preferred_creator_mode_files(new_dir, offset_name):
        lines = read_lines(preferred)
        declarations = creator_struct_declarations_for_path(preferred)
        scores: defaultdict[str, float] = defaultdict(float)
        field_hits: defaultdict[str, set[int]] = defaultdict(set)
        for line_no, raw_line in enumerate(lines, start=1):
            normalized = canonicalize_line(raw_line)
            for root, field_text in LOCAL_FIELD_ACCESS_RE.findall(normalized):
                size = declarations.get(root)
                if size is None or size < min_size or size > max_size:
                    continue
                field = int(field_text)
                if field not in field_weights:
                    continue
                scores[root] += field_weights[field]
                field_hits[root].add(field)

        if not scores:
            continue

        ranked = sorted(
            scores,
            key=lambda root: (
                -scores[root],
                -len(field_hits[root]),
                -declarations.get(root, 0),
                root,
            ),
        )
        best = ranked[0]
        confidence = min(0.92 + (scores[best] / 20.0) + (0.02 * len(field_hits[best])), 0.999)
        candidates.append(
            Candidate(
                value=best,
                confidence=confidence,
                reason=f"current_creator {kind} root heuristic",
                file=preferred,
                line_no=1,
            )
        )

    best_by_value: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best_by_value.get(candidate.value)
        if current is None or candidate.confidence > current.confidence:
            best_by_value[candidate.value] = candidate
    return sorted(best_by_value.values(), key=lambda item: (-item.confidence, item.value))


def count_single_arg_local_calls(lines: list[str], root: str) -> int:
    pattern = re.compile(rf"\bfunc_\d+\(&{re.escape(root)}\);\s*$")
    count = 0
    for raw_line in lines:
        if pattern.search(canonicalize_line(raw_line)):
            count += 1
    return count


def has_local_field_or_index_access(lines: list[str], root: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(root)}(?:\.f_|\[)")
    return any(pattern.search(canonicalize_line(raw_line)) for raw_line in lines)


def find_current_creator_main_call(
    lines: list[str],
    pre_root: str,
    worker_root: str,
) -> tuple[int, list[tuple[str, str]], str] | None:
    best_match: tuple[int, list[tuple[str, str]], str] | None = None
    best_key: tuple[int, int, int] | None = None
    for line_no, raw_line in enumerate(lines, start=1):
        normalized = canonicalize_line(raw_line)
        if f"&{pre_root}" not in normalized or f"&{worker_root}" not in normalized:
            continue
        refs: list[tuple[str, str]] = []
        for match in LOCAL_REF_TOKEN_RE.finditer(raw_line):
            actual = match.group("name")
            root = local_root_from_name(actual)
            if root is not None:
                refs.append((actual, root))
        if not refs:
            continue
        roots = [root for _, root in refs]
        if pre_root in roots and worker_root in roots:
            ulocal_count = sum(1 for actual, _ in refs if actual.startswith("uLocal_"))
            if ulocal_count == 0:
                continue
            key = (ulocal_count, len(refs), line_no)
            if best_key is None or key > best_key:
                best_key = key
                best_match = (line_no, refs, normalized)
    return best_match


def count_root_pair_lines(
    lines: list[str],
    candidate_root: str,
    pre_root: str,
    worker_root: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> int:
    if end_line is None:
        end_line = len(lines)
    count = 0
    for raw_line in lines[start_line - 1 : end_line]:
        normalized = canonicalize_line(raw_line)
        if f"&{candidate_root}" in normalized and f"&{pre_root}" in normalized and f"&{worker_root}" in normalized:
            count += 1
    return count


def count_near_pair_reuse(
    lines: list[str],
    candidate_root: str,
    pre_root: str,
    worker_root: str,
    line_no: int,
    window: int,
) -> int:
    return count_root_pair_lines(lines, candidate_root, pre_root, worker_root, start_line=line_no + 1, end_line=min(len(lines), line_no + window))


def select_cam_heading_root_from_main_call(
    mode: str,
    refs: list[tuple[str, str]],
    worker_root: str,
) -> str | None:
    worker_index = next((idx for idx, (_, root) in enumerate(refs) if root == worker_root), None)
    if worker_index is None:
        return None

    tail = refs[worker_index + 1 :]
    if not tail:
        return None

    if mode in {"capture", "lts"}:
        local_run = 0
        for actual, root in tail:
            if actual.startswith("uLocal_"):
                if local_run >= 5:
                    return root
                return None
            if actual.startswith("Local_"):
                local_run += 1
                continue
            break
        return None

    if mode == "dm":
        local_run = 0
        ulocals_after_run: list[str] = []
        for actual, root in tail:
            if actual.startswith("Local_") and not ulocals_after_run:
                local_run += 1
                continue
            if actual.startswith("uLocal_"):
                ulocals_after_run.append(root)
                continue
            break
        if local_run >= 4 and len(ulocals_after_run) >= 2:
            return ulocals_after_run[1]
        return None

    if mode == "race":
        last_u_local: str | None = None
        for actual, root in tail:
            if actual.startswith("uLocal_"):
                last_u_local = root
        return last_u_local

    if mode == "survival":
        last_u_local: str | None = None
        for actual, root in tail:
            if actual.startswith("uLocal_"):
                last_u_local = root
        return last_u_local

    if mode == "mission":
        for actual, root in reversed(refs[:worker_index]):
            if actual.startswith("uLocal_"):
                return root
        return None

    return None


def infer_current_creator_cam_heading_candidates(new_dir: pathlib.Path, offset_name: str) -> list[Candidate]:
    mode = current_creator_mode(offset_name)
    if mode not in {"capture", "lts", "dm", "race", "survival", "mission"}:
        return []

    pre_name = f"OFFSET_current_creator_pre_{mode}"
    worker_name = f"OFFSET_current_creator_worker_{mode}"
    pre_candidates = infer_current_creator_root_candidates(new_dir, pre_name)
    worker_candidates = infer_current_creator_root_candidates(new_dir, worker_name)
    if not pre_candidates or not worker_candidates:
        return []
    pre_root = pre_candidates[0].value
    worker_root = worker_candidates[0].value

    candidates: list[Candidate] = []
    for preferred in preferred_creator_mode_files(new_dir, offset_name):
        lines = read_lines(preferred)
        main_call = find_current_creator_main_call(lines, pre_root, worker_root)
        if main_call is None:
            continue
        line_no, refs, _ = main_call
        candidate_root = select_cam_heading_root_from_main_call(mode, refs, worker_root)
        if candidate_root is None:
            continue

        near_pair_reuse = count_near_pair_reuse(lines, candidate_root, pre_root, worker_root, line_no, 700)
        global_pair_reuse = count_root_pair_lines(lines, candidate_root, pre_root, worker_root)
        single_arg_calls = count_single_arg_local_calls(lines, candidate_root)
        has_field_access = has_local_field_or_index_access(lines, candidate_root)

        safe = False
        confidence = 0.0
        if mode in {"capture", "lts"} and near_pair_reuse >= 4 and not has_field_access:
            safe = True
            confidence = min(0.985 + (0.002 * min(near_pair_reuse - 4, 3)), 0.995)
        elif mode == "dm" and near_pair_reuse >= 3 and not has_field_access:
            safe = True
            confidence = min(0.985 + (0.002 * min(near_pair_reuse - 3, 3)), 0.993)
        elif mode == "race" and global_pair_reuse >= 4 and single_arg_calls >= 4 and not has_field_access:
            safe = True
            confidence = min(0.984 + (0.002 * min(single_arg_calls - 4, 3)), 0.992)
        elif mode == "survival" and near_pair_reuse >= 3 and global_pair_reuse >= 6 and not has_field_access:
            safe = True
            confidence = min(0.985 + (0.001 * min(global_pair_reuse - 6, 4)), 0.991)
        elif mode == "mission" and global_pair_reuse >= 3 and single_arg_calls >= 5 and not has_field_access:
            safe = True
            confidence = min(0.985 + (0.001 * min(single_arg_calls - 5, 5)), 0.99)

        if not safe:
            continue

        candidates.append(
            Candidate(
                value=candidate_root,
                confidence=confidence,
                reason=f"current_creator cam_heading {mode} helper heuristic",
                file=preferred,
                line_no=line_no,
            )
        )

    best_by_value: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best_by_value.get(candidate.value)
        if current is None or candidate.confidence > current.confidence:
            best_by_value[candidate.value] = candidate
    return sorted(best_by_value.values(), key=lambda item: (-item.confidence, item.value))


def find_enclosing_function_bounds(lines: list[str], line_index: int) -> tuple[int, int]:
    start = 0
    end = len(lines)
    for idx in range(line_index, -1, -1):
        if FUNCTION_START_RE.match(lines[idx]):
            start = idx
            break
    for idx in range(line_index + 1, len(lines)):
        if FUNCTION_START_RE.match(lines[idx]):
            end = idx
            break
    return start, end


def find_enclosing_function_name(lines: list[str], line_index: int) -> str | None:
    for idx in range(line_index, -1, -1):
        match = FUNCTION_START_RE.match(lines[idx])
        if match:
            return match.group(1)
    return None


def find_local_setter_functions(lines: list[str], local_name: str) -> set[str]:
    setters: set[str] = set()
    for idx, raw_line in enumerate(lines):
        if f"{local_name} = iParam0;" not in raw_line:
            continue
        for prev in range(idx, -1, -1):
            match = FUNCTION_START_RE.match(lines[prev])
            if match:
                setters.add(match.group(1))
                break
    return setters


def functions_containing_local(lines: list[str], local_name: str) -> set[str]:
    functions: set[str] = set()
    current_function = "__global__"
    for raw_line in lines:
        match = FUNCTION_START_RE.match(raw_line)
        if match:
            current_function = match.group(1)
        if local_name in raw_line:
            functions.add(current_function)
    return functions


def extract_local_state_values(lines: list[str], local_name: str, setter_functions: set[str]) -> set[int]:
    states: set[int] = set()
    assign_re = re.compile(rf"\b{re.escape(local_name)}\s*=\s*(\d+);")
    setter_res = [re.compile(rf"\b{re.escape(name)}\((\d+)\);") for name in setter_functions]
    for raw_line in lines:
        for match in assign_re.finditer(raw_line):
            states.add(int(match.group(1)))
        for setter_re in setter_res:
            for match in setter_re.finditer(raw_line):
                states.add(int(match.group(1)))
    return states


def find_switch_variables(lines: list[str]) -> list[tuple[str, int]]:
    switches: list[tuple[str, int]] = []
    for idx, raw_line in enumerate(lines):
        match = LOCAL_SWITCH_RE.search(raw_line)
        if match:
            switches.append((match.group(1), idx))
    return switches


def find_worker_case_called_functions(
    lines: list[str],
    worker_root: str,
    case_values: tuple[int, ...] = (6, 7),
) -> set[str]:
    switch_line_index = None
    switch_pattern = f"switch ({worker_root}.f_565)"
    for idx, raw_line in enumerate(lines):
        if switch_pattern in canonicalize_line(raw_line):
            switch_line_index = idx
            break
    if switch_line_index is None:
        return set()

    called: set[str] = set()
    for case_value in case_values:
        case_block = find_case_block(lines, switch_line_index, case_value)
        if case_block is None:
            continue
        for raw_line in lines[case_block[0] : case_block[1] + 1]:
            called.update(FUNCTION_CALL_RE.findall(raw_line))
    return called


def find_case_block(lines: list[str], switch_line_index: int, case_value: int) -> tuple[int, int] | None:
    brace_depth = 0
    in_switch = False
    case_start: int | None = None

    for idx in range(switch_line_index, len(lines)):
        raw_line = lines[idx]
        if not in_switch:
            brace_depth += raw_line.count("{") - raw_line.count("}")
            if "{" in raw_line:
                in_switch = True
            continue

        case_match = CASE_LABEL_RE.match(raw_line)
        if case_start is None:
            if brace_depth == 1 and case_match and int(case_match.group(1)) == case_value:
                case_start = idx
        else:
            if brace_depth == 1 and case_match:
                return case_start, idx - 1
            if brace_depth <= 0:
                return case_start, idx - 1

        brace_depth += raw_line.count("{") - raw_line.count("}")
        if in_switch and brace_depth <= 0:
            if case_start is not None:
                return case_start, idx - 1
            break

    if case_start is None:
        return None
    return case_start, len(lines) - 1


def block_contains_local_gate(block_lines: list[str], local_name: str) -> bool:
    normalized = "\n".join(block_lines)
    checks = (
        f"if ({local_name} == 0)" in normalized
        or f"if (!{local_name})" in normalized
    )
    return checks and f"{local_name} = 1;" in normalized and f"{local_name} = 0;" in normalized


def infer_current_creator_test_candidates(new_dir: pathlib.Path, offset_name: str) -> list[Candidate]:
    mode = current_creator_mode(offset_name)
    if mode not in {"lts", "dm", "race", "mission"}:
        return []

    candidates: list[Candidate] = []
    for preferred in preferred_creator_mode_files(new_dir, offset_name):
        lines = read_lines(preferred)
        worker_name = f"OFFSET_current_creator_worker_{mode}"
        worker_candidates = infer_current_creator_root_candidates(new_dir, worker_name)
        worker_root = worker_candidates[0].value if worker_candidates else None
        preferred_test_functions = (
            find_worker_case_called_functions(lines, worker_root) if worker_root else set()
        )
        ranked: list[tuple[float, str, int, int]] = []
        for local_name, line_index in find_switch_variables(lines):
            setter_functions = find_local_setter_functions(lines, local_name)
            states = extract_local_state_values(lines, local_name, setter_functions)
            if len(states) < 8:
                continue
            _, func_end = find_enclosing_function_bounds(lines, line_index)
            function_name = find_enclosing_function_name(lines, line_index)
            body_lines = lines[line_index:func_end]
            case_count = sum(1 for line in body_lines if CASE_LABEL_RE.match(line))
            occurrences = sum(1 for line in body_lines if local_name in line)
            score = (
                len(states) * 5.0
                + case_count * 1.5
                + min(occurrences, 25) * 0.15
                + max(states) * 0.01
            )
            if function_name and function_name in preferred_test_functions:
                score += 250.0
            ranked.append((score, local_name, len(states), max(states)))

        if not ranked:
            continue
        ranked.sort(reverse=True)
        score, local_name, state_count, max_state = ranked[0]
        confidence = 0.985
        if state_count >= 10 and max_state >= 10:
            confidence = 0.993
        candidates.append(
            Candidate(
                value=local_name,
                confidence=confidence,
                reason=f"current_creator test {mode} state-machine heuristic",
                file=preferred,
                line_no=ranked[0][1] and next(idx + 1 for idx, line in enumerate(lines) if f"switch ({local_name})" in line),
            )
        )

    best_by_value: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best_by_value.get(candidate.value)
        if current is None or candidate.confidence > current.confidence:
            best_by_value[candidate.value] = candidate
    return sorted(best_by_value.values(), key=lambda item: (-item.confidence, item.value))


def infer_current_creator_refresh_candidates(new_dir: pathlib.Path, offset_name: str) -> list[Candidate]:
    mode = current_creator_mode(offset_name)
    if mode not in {"capture", "lts", "dm", "race", "survival", "mission"}:
        return []

    if mode == "mission":
        candidates: list[Candidate] = []
        for preferred in preferred_creator_mode_files(new_dir, offset_name):
            if preferred.name != "public_mission_creator.c":
                continue

            lines = read_lines(preferred)
            candidate_names: set[str] = set()
            local_ref_re = re.compile(r"\b(iLocal_\d+)\b")
            for idx, raw_line in enumerate(lines):
                if "if (NETWORK::NETWORK_IS_GAME_IN_PROGRESS())" not in raw_line:
                    continue
                window_lines = lines[idx : min(len(lines), idx + 25)]
                window_text = "\n".join(window_lines)
                for local_name in local_ref_re.findall(window_text):
                    if (
                        f"if ({local_name} == 0)" in window_text or f"if (!{local_name})" in window_text
                    ) and f"{local_name} = 1;" in window_text:
                        candidate_names.add(local_name)

            ranked: list[tuple[float, str]] = []
            for local_name in sorted(candidate_names):
                set0 = sum(1 for line in lines if f"{local_name} = 0;" in line)
                has_active_check = any(f"if ({local_name} == 1)" in line for line in lines)
                function_count = len(functions_containing_local(lines, local_name))
                if not has_active_check or set0 == 0:
                    continue
                score = 4.0 + min(set0, 3) + min(function_count, 3)
                ranked.append((score, local_name))

            if not ranked:
                continue

            ranked.sort(reverse=True)
            score, local_name = ranked[0]
            if score < 7.0:
                continue
            line_no = next(idx + 1 for idx, line in enumerate(lines) if f"if ({local_name} == 0)" in line)
            candidates.append(
                Candidate(
                    value=local_name,
                    confidence=0.984,
                    reason="current_creator refresh mission network gate heuristic",
                    file=preferred,
                    line_no=line_no,
                )
            )

        best_by_value: dict[str, Candidate] = {}
        for candidate in candidates:
            current = best_by_value.get(candidate.value)
            if current is None or candidate.confidence > current.confidence:
                best_by_value[candidate.value] = candidate
        return sorted(best_by_value.values(), key=lambda item: (-item.confidence, item.value))

    worker_name = f"OFFSET_current_creator_worker_{mode}"
    worker_candidates = infer_current_creator_root_candidates(new_dir, worker_name)
    if not worker_candidates:
        return []
    worker_root = worker_candidates[0].value

    candidates: list[Candidate] = []
    for preferred in preferred_creator_mode_files(new_dir, offset_name):
        lines = read_lines(preferred)
        switch_line_index = None
        switch_pattern = f"switch ({worker_root}.f_565)"
        for idx, raw_line in enumerate(lines):
            if switch_pattern in canonicalize_line(raw_line):
                switch_line_index = idx
                break
        if switch_line_index is None:
            continue

        case5 = find_case_block(lines, switch_line_index, 5)
        case91 = find_case_block(lines, switch_line_index, 91)
        if case5 is None or case91 is None:
            continue

        block5 = lines[case5[0] : case5[1] + 1]
        block91 = lines[case91[0] : case91[1] + 1]
        candidate_names: set[str] = set()
        local_ref_re = re.compile(r"\b(iLocal_\d+)\b")
        for block in (block5, block91):
            candidate_names.update(local_ref_re.findall("\n".join(block)))

        ranked: list[tuple[int, str]] = []
        for local_name in sorted(candidate_names):
            score = 0
            if block_contains_local_gate(block5, local_name):
                score += 3
            if block_contains_local_gate(block91, local_name):
                score += 3
            if score:
                ranked.append((score, local_name))

        if not ranked:
            continue
        ranked.sort(reverse=True)
        score, local_name = ranked[0]
        if score < 6:
            continue
        candidates.append(
            Candidate(
                value=local_name,
                confidence=0.992,
                reason=f"current_creator refresh {mode} case-5-91 gate heuristic",
                file=preferred,
                line_no=case5[0] + 1,
            )
        )

    best_by_value: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best_by_value.get(candidate.value)
        if current is None or candidate.confidence > current.confidence:
            best_by_value[candidate.value] = candidate
    return sorted(best_by_value.values(), key=lambda item: (-item.confidence, item.value))


def infer_current_creator_relative_field_candidates(offset_name: str, value: str) -> list[Candidate]:
    if not is_current_creator_relative_field_offset(offset_name, value):
        return []

    candidate_value = CURRENT_CREATOR_RELATIVE_FIELD_OVERRIDES.get(offset_name, value)
    reason = "current_creator relative field verified mapping"
    confidence = 0.999 if candidate_value != value else 0.998
    return [
        Candidate(
            value=candidate_value,
            confidence=confidence,
            reason=reason,
        )
    ]


def infer_verified_special_offset_candidates(offset_name: str | None, value: str) -> list[Candidate]:
    if not offset_name:
        return []
    candidate_value = VERIFIED_SPECIAL_OFFSET_OVERRIDES.get(offset_name)
    if not candidate_value:
        return []
    confidence = 0.999 if candidate_value != value else 0.998
    return [
        Candidate(
            value=candidate_value,
            confidence=confidence,
            reason="verified special offset mapping",
        )
    ]


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


def find_old_matches(old_dir: pathlib.Path, value: str, offset_name: str | None = None) -> list[SourceMatch]:
    results: list[SourceMatch] = []
    shape = path_shape_regex(value)
    preferred_files = preferred_creator_files(old_dir, offset_name, value)
    candidate_paths = preferred_files or list(candidate_files_for_value_cached(old_dir, value))
    for path in candidate_paths:
        lines = read_lines(path)
        text = "\n".join(lines)
        if not shape.search(text):
            continue
        table = build_symbol_table_for_path(path)
        for idx, line in enumerate(lines, start=1):
            match = shape.search(line)
            if not match:
                continue
            if normalize_offset_value_for_search(value).startswith("Local_"):
                matched_value = canonicalize_local_value(match.group(0))
            else:
                matched_value = canonicalize_global_value(match.group(0))
            handle = None
            semantic_path: tuple[str, ...] = ()
            m = ARRAY_ADD_RE.search(line)
            if m and matched_value.startswith("Global_"):
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


@lru_cache(maxsize=OLD_MATCH_CACHE_SIZE)
def find_old_matches_cached(old_dir: pathlib.Path, value: str, offset_name: str | None = None) -> tuple[SourceMatch, ...]:
    return tuple(find_old_matches(old_dir, value, offset_name))


def clear_runtime_caches() -> None:
    read_lines.cache_clear()
    read_text_cached.cache_clear()
    build_symbol_table_for_path.cache_clear()
    find_old_matches_cached.cache_clear()


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
    local_kind = normalize_offset_value_for_search(value).startswith("Local_")
    for idx, line in enumerate(read_lines(new_file), start=1):
        m = regex.search(line)
        if not m:
            continue
        if local_kind:
            matched_value = canonicalize_local_value(m.group(0))
            is_exact = matched_value == canonicalize_local_value(value)
            confidence = 0.88 if is_exact else 0.76
            reason = "exact local token still present" if is_exact else "same local shape in same file"
        else:
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
    target = normalize_offset_value_for_search(target_value)
    for line in lines:
        for match in re.finditer(r"Global_\d+(?:\.f_\d+)+", line):
            token = match.group(0)
            if canonicalize_global_value(token) != target:
                anchors.add(token)
        for match in LOCAL_VALUE_RE.finditer(line):
            token = canonicalize_local_value(match.group(0))
            if token != target:
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
    normalized_old_value = normalize_offset_value_for_search(old_match.value)
    local_kind = normalized_old_value.startswith("Local_")
    old_root_only = "[" not in normalized_old_value and ".f_" not in normalized_old_value
    for idx, line in enumerate(new_lines, start=1):
        if local_kind:
            if "Local_" not in line:
                continue
        elif "Global_" not in line:
            continue
        if old_is_assignment and ("=" not in line or "==" in line):
            continue
        if old_is_return and not line.strip().startswith("return "):
            continue
        values = extract_values_for_kind(line, old_match.value)
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
            if old_root_only and ("[" in value or ".f_" in value):
                continue
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
    if reason.startswith("team block verified"):
        return 1
    if reason.startswith("current_creator relative field"):
        return 2
    if reason.startswith("verified special offset"):
        return 3
    if reason == "exact path still present":
        return 4
    if reason == "same path shape in same file":
        return 5
    if reason == "context match in same file":
        return 6
    if reason == "exact local token still present":
        return 7
    if reason == "same local shape in same file":
        return 8
    return 9


def is_safe_candidate(current_value: str, candidate: Candidate) -> bool:
    if candidate.reason.startswith("verified special offset") and candidate.confidence >= 0.99:
        return True
    if candidate.reason.startswith("current_creator ") and candidate.confidence >= 0.98:
        return True
    if candidate.reason.startswith("current_creator relative field") and candidate.confidence >= 0.99:
        return True
    if candidate.reason.startswith("team block verified") and candidate.confidence >= 0.99:
        return True
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


def infer_from_match(
    old_match: SourceMatch,
    new_dir: pathlib.Path,
    offset_name: str | None = None,
    source_rank: int = 0,
) -> list[Candidate]:
    relative = old_match.file.relative_to(old_match.file.parents[1])
    candidates: list[Candidate] = []
    target_files: list[pathlib.Path] = []
    same_name_target = new_dir / relative.name
    if same_name_target.exists():
        target_files.append(same_name_target)
    for preferred in preferred_creator_files(new_dir, offset_name, old_match.value):
        if preferred not in target_files:
            target_files.append(preferred)

    for target_file in target_files:
        if old_match.semantic_path:
            semantic_candidates = find_line_candidates_by_semantic_path(target_file, old_match.semantic_path)
            if semantic_candidates:
                shape_candidates = find_line_candidates_by_shape(target_file, old_match.value)
                candidates.extend(semantic_candidates)
                candidates.extend([c for c in shape_candidates if c.value == old_match.value])
                return apply_source_bonus(candidates, source_rank)
        candidates.extend(find_line_candidates_by_shape(target_file, old_match.value))
        candidates.extend(find_line_candidates_by_context(target_file, old_match))
    return apply_source_bonus(candidates, source_rank)


def infer_candidates(
    old_dir: pathlib.Path,
    new_dir: pathlib.Path,
    value: str,
    offset_name: str | None = None,
) -> tuple[list[SourceMatch], list[Candidate]]:
    special_offset_candidates = infer_verified_special_offset_candidates(offset_name, value)
    if special_offset_candidates:
        return [], special_offset_candidates

    if offset_name and offset_name.startswith("OFFSET_current_creator_"):
        creator_relative_field_candidates = infer_current_creator_relative_field_candidates(offset_name, value)
        if creator_relative_field_candidates:
            return [], creator_relative_field_candidates

        creator_kind = current_creator_kind(offset_name)

        creator_root_candidates = infer_current_creator_root_candidates(new_dir, offset_name)
        if creator_root_candidates and creator_kind in {"worker", "pre"}:
            return [], creator_root_candidates

        creator_cam_heading_candidates = infer_current_creator_cam_heading_candidates(new_dir, offset_name)
        if creator_cam_heading_candidates and creator_kind == "cam_heading":
            return [], creator_cam_heading_candidates

        creator_test_candidates = infer_current_creator_test_candidates(new_dir, offset_name)
        if creator_test_candidates and creator_kind == "test":
            return [], creator_test_candidates

        creator_refresh_candidates = infer_current_creator_refresh_candidates(new_dir, offset_name)
        if creator_refresh_candidates and creator_kind == "refresh":
            return [], creator_refresh_candidates

    team_candidates = infer_team_block_candidates(new_dir, value, offset_name)
    if team_candidates:
        return [], team_candidates

    old_matches = list(find_old_matches_cached(old_dir, value, offset_name))
    semantic_matches = [match for match in old_matches if match.semantic_path]
    if semantic_matches:
        old_matches = semantic_matches
    else:
        old_matches = prune_old_matches(old_matches, new_dir)
    all_candidates: list[Candidate] = []
    for idx, match in enumerate(old_matches):
        all_candidates.extend(infer_from_match(match, new_dir, offset_name=offset_name, source_rank=idx))

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

    old_root = root_value_info(value)
    merged_candidates = sorted(
        best_by_value.values(),
        key=lambda item: (
            candidate_reason_rank(item.reason),
            item.source_rank,
            -item.confidence,
            abs((root_value_info(item.value) or ("", 0))[1] - (old_root or ("", 0))[1]),
            item.value,
        ),
    )
    return old_matches, merged_candidates


def update_ini_value(text: str, entry: OffsetEntry, new_value: str) -> str:
    return text[: entry.start] + new_value + text[entry.end :]


def build_report_summary(
    records: list[dict[str, object]],
    quoted_entries: list[OffsetEntry],
) -> dict[str, object]:
    supported_names = {record["name"] for record in records}
    unsupported_quoted = [
        {
            "name": entry.name,
            "value": entry.value,
            "family": offset_family(entry.name),
        }
        for entry in quoted_entries
        if entry.name not in supported_names
    ]
    status_counts = Counter(str(record["status"]) for record in records)
    pending_counts = Counter(
        str(record["family"])
        for record in records
        if str(record["status"]) in {"review_needed", "unresolved"}
    )
    return {
        "totals": {
            "quoted_entries": len(quoted_entries),
            "supported_entries": len(records),
            "unsupported_quoted_entries": len(unsupported_quoted),
            "unchanged": status_counts["unchanged"],
            "safe_updates": status_counts["safe_update"],
            "review_needed": status_counts["review_needed"],
            "unresolved": status_counts["unresolved"],
            "apply_ready": sum(1 for record in records if bool(record["will_apply"])),
        },
        "pending_families": dict(sorted(pending_counts.items(), key=lambda item: (-item[1], item[0]))),
        "unsupported_quoted": unsupported_quoted,
        "entries": records,
    }


def print_report_summary(summary: dict[str, object]) -> None:
    totals = summary["totals"]
    pending_families = summary["pending_families"]
    print("\nsummary")
    print(f"  quoted: {totals['quoted_entries']}")
    print(f"  supported: {totals['supported_entries']}")
    print(f"  unsupported quoted: {totals['unsupported_quoted_entries']}")
    print(f"  unchanged: {totals['unchanged']}")
    print(f"  safe updates: {totals['safe_updates']}")
    print(f"  review needed: {totals['review_needed']}")
    print(f"  unresolved: {totals['unresolved']}")
    print(f"  apply ready: {totals['apply_ready']}")
    if pending_families:
        top_pending = ", ".join(f"{family}={count}" for family, count in list(pending_families.items())[:10])
        print(f"  pending families: {top_pending}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer updated GTA offset globals from old/new decompiled scripts.")
    parser.add_argument("--ini", default="offsets.ini")
    parser.add_argument("--old-dir", default="old")
    parser.add_argument("--new-dir", default="new")
    parser.add_argument("--offset", action="append", help="Only inspect one or more OFFSET_* entries.")
    parser.add_argument("--offset-file", help="Read one OFFSET_* entry per line and only inspect that subset.")
    parser.add_argument("--apply", action="store_true", help="Write confident matches back into offsets.ini.")
    parser.add_argument("--apply-safe", action="store_true", help="Write only safe matches back into offsets.ini.")
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--top", type=int, default=2, help="How many candidates to print per offset.")
    parser.add_argument("--summary-only", action="store_true", help="Suppress per-offset output and print only the summary.")
    parser.add_argument("--report", action="store_true", help="Print an aggregate report after scanning.")
    parser.add_argument("--report-json", help="Write the aggregate report as JSON.")
    parser.add_argument(
        "--fast-report",
        action="store_true",
        help="Skip expensive inference for values not currently present in new and mark them for review instead.",
    )
    parser.add_argument("--fail-on-unresolved", action="store_true", help="Exit non-zero when unresolved offsets remain.")
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
    if args.fast_report and (args.apply or args.apply_safe):
        print("--fast-report cannot be combined with --apply or --apply-safe", file=sys.stderr)
        return 1
    ini_text, entries = parse_offsets_ini(ini_path)
    _, quoted_entries = parse_quoted_offsets(ini_path)

    requested: set[str] = set(args.offset or [])
    if args.offset_file:
        offset_file = pathlib.Path(args.offset_file)
        if not offset_file.exists():
            print(f"missing offset file: {offset_file}", file=sys.stderr)
            return 1
        for raw_line in offset_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            requested.add(line)

    if requested:
        entries = [entry for entry in entries if entry.name in requested]
        quoted_entries = [entry for entry in quoted_entries if entry.name in requested]
        if not entries:
            print(f"requested offsets not found in {ini_path}", file=sys.stderr)
            return 1

    batch_present_names: set[str] = set()
    if (args.summary_only or args.report or args.report_json) and len(entries) >= 25:
        batch_present_names = find_present_entries_in_new(new_dir, entries)

    replacements: dict[str, str] = {}
    report_records: list[dict[str, object]] = []
    for entry in entries:
        try:
            normalized_value = normalize_offset_value_for_search(entry.value)
            batch_checked_global = (
                bool(batch_present_names)
                and not is_relative_field_value(entry.value)
                and not normalized_value.startswith("Local_")
            )
            is_present = entry.name in batch_present_names
            if not is_present and not batch_checked_global:
                is_present = current_value_present_in_new(new_dir, entry.value, entry.name)
            if is_present:
                if not args.summary_only:
                    print(f"{entry.name}")
                    print(f"  current: {entry.value}")
                    print("  old hit: skipped")
                    print(f"  candidate: {entry.value} confidence=1.00")
                    print("  reason: current value present in new")
                report_records.append(
                    {
                        "name": entry.name,
                        "family": offset_family(entry.name),
                        "current": entry.value,
                        "status": "unchanged",
                        "candidate": entry.value,
                        "confidence": 1.0,
                        "reason": "current value present in new",
                        "safe": True,
                        "will_apply": False,
                        "old_match_count": 0,
                    }
                )
                continue

            if args.fast_report:
                fast_candidates = []
                if entry.name.startswith("OFFSET_current_creator_"):
                    fast_candidates = infer_current_creator_relative_field_candidates(entry.name, entry.value)
                elif is_trusted_fast_report_offset(entry.name, entry.value):
                    fast_candidates = [
                        Candidate(
                            value=entry.value,
                            confidence=0.997,
                            reason="trusted fast-report family mapping",
                        )
                    ]
                if fast_candidates:
                    top = fast_candidates[0]
                    top_display = present_value_for_ini(entry.value, top.value, entry.name)
                    status = "unchanged" if top_display == entry.value else "safe_update"
                    if not args.summary_only:
                        print(f"{entry.name}")
                        print(f"  current: {entry.value}")
                        print("  old hit: skipped")
                        print(f"  candidate: {top_display} confidence={top.confidence:.2f}")
                        print(f"  reason: {top.reason}")
                    report_records.append(
                        {
                            "name": entry.name,
                            "family": offset_family(entry.name),
                            "current": entry.value,
                            "status": status,
                            "candidate": top_display,
                            "confidence": round(top.confidence, 6),
                            "reason": top.reason,
                            "safe": True,
                            "will_apply": False,
                            "old_match_count": 0,
                        }
                    )
                    continue
                if not args.summary_only:
                    print(f"{entry.name}")
                    print(f"  current: {entry.value}")
                    print("  old hit: skipped")
                    print("  candidate: skipped")
                    print("  reason: not present in new (fast report)")
                report_records.append(
                    {
                        "name": entry.name,
                        "family": offset_family(entry.name),
                        "current": entry.value,
                        "status": "review_needed",
                        "candidate": None,
                        "confidence": None,
                        "reason": "not present in new (fast report)",
                        "safe": False,
                        "will_apply": False,
                        "old_match_count": 0,
                    }
                )
                continue

            old_matches, candidates = infer_candidates(old_dir, new_dir, entry.value, entry.name)
            if not candidates:
                if not args.summary_only:
                    print(f"{entry.name}")
                    print(f"  current: {entry.value}")
                    if old_matches:
                        example = old_matches[0]
                        semantic = "/".join(example.semantic_path) if example.semantic_path else "-"
                        print(f"  old hit: {example.file.name}:{example.line_no} semantic={semantic} matches={len(old_matches)}")
                    else:
                        print("  old hit: none")
                    print("  candidate: none")
                report_records.append(
                    {
                        "name": entry.name,
                        "family": offset_family(entry.name),
                        "current": entry.value,
                        "status": "unresolved",
                        "candidate": None,
                        "confidence": None,
                        "reason": None,
                        "safe": False,
                        "will_apply": False,
                        "old_match_count": len(old_matches),
                    }
                )
                continue
            top = candidates[0]
            location = f" @ {top.file.name}:{top.line_no}" if top.file and top.line_no else ""
            top_display = present_value_for_ini(entry.value, top.value, entry.name)
            safe_candidate = is_safe_candidate(entry.value, top)
            will_apply = False
            if args.apply and top.confidence >= args.min_confidence and top_display != entry.value:
                replacements[entry.name] = top_display
                will_apply = True
            elif args.apply_safe and safe_candidate and top_display != entry.value:
                replacements[entry.name] = top_display
                will_apply = True

            if top_display == entry.value:
                status = "unchanged"
            elif safe_candidate:
                status = "safe_update"
            else:
                status = "review_needed"

            if not args.summary_only:
                print(f"{entry.name}")
                print(f"  current: {entry.value}")
                if old_matches:
                    example = old_matches[0]
                    semantic = "/".join(example.semantic_path) if example.semantic_path else "-"
                    print(f"  old hit: {example.file.name}:{example.line_no} semantic={semantic} matches={len(old_matches)}")
                else:
                    print("  old hit: none")
                print(f"  candidate: {top_display} confidence={top.confidence:.2f}{location}")
                print(f"  reason: {top.reason}")
                for extra in candidates[1 : max(args.top, 1)]:
                    extra_display = present_value_for_ini(entry.value, extra.value, entry.name)
                    print(f"  alt: {extra_display} confidence={extra.confidence:.2f}")

            report_records.append(
                {
                    "name": entry.name,
                    "family": offset_family(entry.name),
                    "current": entry.value,
                    "status": status,
                    "candidate": top_display,
                    "confidence": round(top.confidence, 6),
                    "reason": top.reason,
                    "safe": safe_candidate,
                    "will_apply": will_apply,
                    "old_match_count": len(old_matches),
                }
            )
        finally:
            clear_runtime_caches()

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

    summary = build_report_summary(report_records, quoted_entries)
    if args.summary_only or args.report or args.report_json:
        print_report_summary(summary)

    if args.report_json:
        report_path = pathlib.Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    if args.fail_on_unresolved and summary["totals"]["unresolved"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
