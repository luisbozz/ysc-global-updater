#!/usr/bin/env python3
"""Bestandsaufnahme und Auswahl der relevanten dekompilierten .c-Files.

Der Scanner liest ein Quellverzeichnis (Default: ``new/``), misst pro Datei die
fuer Offset-Extraktion relevanten Signale (Global_-/Local_-Dichte,
DATADICT/DATAARRAY-Serializer-Aufrufe), verwirft Decompiler-Stubs und ordnet
jede Datei dem ersten passenden Tier aus ``sources.config.json`` zu.

Ergebnis ist ein deterministisches Manifest (JSON), das reviewbar und
update-fest festhaelt, welche Files warum behalten werden. Optional wird eine
reine Keep-Liste geschrieben, die z. B. den Extractor speisen kann.
"""
import argparse
import dataclasses
import fnmatch
import json
import pathlib
import re
import sys
from typing import Iterable


GLOBAL_RE = re.compile(r"Global_\d+")
LOCAL_RE = re.compile(r"(?:[fiu])?Local_\d+")
SERIALIZER_RE = re.compile(r"DATADICT_(?:CREATE|SET)_[A-Z_]+|DATAARRAY_ADD_[A-Z_]+")
EMPTY_ENTRY_RE = re.compile(r"^\s*void\s+__EntryFunction__\s*\(\s*\)\s*\{\s*\}\s*$")


@dataclasses.dataclass
class Metrics:
    size_bytes: int
    globals: int
    locals: int
    serializer_calls: int
    is_stub: bool


@dataclasses.dataclass
class Tier:
    name: str
    reason: str
    name_globs: tuple[str, ...]
    min_globals: int
    min_serializer_calls: int
    min_locals: int


@dataclasses.dataclass
class FileVerdict:
    name: str
    metrics: Metrics
    keep: bool
    tier: str | None
    reason: str


def load_tiers(config: dict) -> list[Tier]:
    tiers: list[Tier] = []
    for raw in config.get("tiers", []):
        tiers.append(
            Tier(
                name=raw["name"],
                reason=raw.get("reason", ""),
                name_globs=tuple(g.lower() for g in raw.get("name_globs", [])),
                min_globals=int(raw.get("min_globals", 0)),
                min_serializer_calls=int(raw.get("min_serializer_calls", 0)),
                min_locals=int(raw.get("min_locals", 0)),
            )
        )
    return tiers


def measure(path: pathlib.Path, stub_max_bytes: int) -> Metrics:
    size = path.stat().st_size
    text = path.read_text(encoding="utf-8", errors="ignore")

    globals_count = sum(1 for _ in GLOBAL_RE.finditer(text))
    locals_count = sum(1 for _ in LOCAL_RE.finditer(text))
    serializer_count = sum(1 for _ in SERIALIZER_RE.finditer(text))

    has_signal = bool(globals_count or locals_count or serializer_count)
    is_stub = bool(EMPTY_ENTRY_RE.match(text.strip())) or (size <= stub_max_bytes and not has_signal)

    return Metrics(
        size_bytes=size,
        globals=globals_count,
        locals=locals_count,
        serializer_calls=serializer_count,
        is_stub=is_stub,
    )


def matches_tier(name: str, metrics: Metrics, tier: Tier) -> bool:
    if tier.name_globs:
        lowered = name.lower()
        if not any(fnmatch.fnmatchcase(lowered, glob) for glob in tier.name_globs):
            return False
    if metrics.globals < tier.min_globals:
        return False
    if metrics.serializer_calls < tier.min_serializer_calls:
        return False
    if metrics.locals < tier.min_locals:
        return False
    return True


def classify(name: str, metrics: Metrics, tiers: Iterable[Tier]) -> FileVerdict:
    if metrics.is_stub:
        return FileVerdict(name, metrics, keep=False, tier=None, reason="stub (leerer __EntryFunction__)")

    for tier in tiers:
        if matches_tier(name, metrics, tier):
            return FileVerdict(name, metrics, keep=True, tier=tier.name, reason=tier.reason)

    return FileVerdict(name, metrics, keep=False, tier=None, reason="unter allen Tier-Schwellwerten")


def rank_key(verdict: FileVerdict) -> tuple:
    # Kept-Files zuerst, dann nach staerkstem Signal (Serializer, dann Globals).
    return (
        0 if verdict.keep else 1,
        -verdict.metrics.serializer_calls,
        -verdict.metrics.globals,
        verdict.name,
    )


def build_manifest(source_dir: pathlib.Path, config: dict) -> dict:
    stub_max_bytes = int(config.get("stub_max_bytes", 128))
    tiers = load_tiers(config)

    verdicts = [
        classify(path.name, measure(path, stub_max_bytes), tiers)
        for path in sorted(source_dir.glob("*.c"))
    ]
    verdicts.sort(key=rank_key)

    kept = [v for v in verdicts if v.keep]
    dropped = [v for v in verdicts if not v.keep]

    per_tier: dict[str, int] = {}
    for v in kept:
        per_tier[v.tier or "?"] = per_tier.get(v.tier or "?", 0) + 1

    return {
        "source_dir": source_dir.name,
        "summary": {
            "scanned": len(verdicts),
            "kept": len(kept),
            "dropped": len(dropped),
            "stubs": sum(1 for v in dropped if v.metrics.is_stub),
            "per_tier": per_tier,
        },
        "files": [
            {
                "name": v.name,
                "keep": v.keep,
                "tier": v.tier,
                "reason": v.reason,
                "size_bytes": v.metrics.size_bytes,
                "globals": v.metrics.globals,
                "locals": v.metrics.locals,
                "serializer_calls": v.metrics.serializer_calls,
            }
            for v in verdicts
        ],
    }


def print_summary(manifest: dict) -> None:
    summary = manifest["summary"]
    print(
        f"scanned={summary['scanned']} kept={summary['kept']} "
        f"dropped={summary['dropped']} stubs={summary['stubs']}"
    )
    for tier, count in summary["per_tier"].items():
        print(f"  {tier}: {count}")
    print("kept files:")
    for entry in manifest["files"]:
        if not entry["keep"]:
            continue
        print(
            f"  [{entry['tier']}] {entry['name']} "
            f"(globals={entry['globals']}, serializer={entry['serializer_calls']}, "
            f"locals={entry['locals']})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="new", help="Verzeichnis mit den dekompilierten .c-Files.")
    parser.add_argument("--config", default="sources.config.json", help="Auswahl-Policy.")
    parser.add_argument("--out", default="reports/sources.manifest.json", help="Ziel fuer das Manifest.")
    parser.add_argument("--keep-list", help="Optionale reine Keep-Liste (ein Dateiname pro Zeile).")
    parser.add_argument("--summary-only", action="store_true", help="Nur Zusammenfassung ausgeben.")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]
    source_dir = (root / args.source_dir) if not pathlib.Path(args.source_dir).is_absolute() else pathlib.Path(args.source_dir)
    config_path = (root / args.config) if not pathlib.Path(args.config).is_absolute() else pathlib.Path(args.config)

    if not source_dir.is_dir():
        print(f"[ERRO] Quellverzeichnis nicht gefunden: {source_dir}", file=sys.stderr)
        return 2
    if not config_path.is_file():
        print(f"[ERRO] Config nicht gefunden: {config_path}", file=sys.stderr)
        return 2

    config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = build_manifest(source_dir, config)

    out_path = (root / args.out) if not pathlib.Path(args.out).is_absolute() else pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if args.keep_list:
        keep_path = (root / args.keep_list) if not pathlib.Path(args.keep_list).is_absolute() else pathlib.Path(args.keep_list)
        keep_path.parent.mkdir(parents=True, exist_ok=True)
        keep_names = [entry["name"] for entry in manifest["files"] if entry["keep"]]
        keep_path.write_text("\n".join(keep_names) + "\n", encoding="utf-8")

    if not args.summary_only:
        print(f"Manifest geschrieben: {out_path}")
    print_summary(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
