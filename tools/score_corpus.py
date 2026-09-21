#!/usr/bin/env python3
"""Score an offsets.ini against a script corpus by literal presence.

A migrated offset should name a path that actually exists in the build it was
migrated to. This counts how many of the ``Global_*`` values in an ini can be
found verbatim in a corpus, which gives a cheap, assumption-free measure of
whether a migration moved the file towards the new build or away from it.

It is a floor, not a score out of 100: offsets.ini also stores simplified forms
(a 2D array flattened to 1D, a leaf of a block that is only ever written as a
whole) that are correct but never appear literally in any corpus. Those drag
the absolute number down in *every* corpus equally, so the number is only
meaningful when compared against another ini scored the same way.

    python3 tools/score_corpus.py --ini reports/offsets.migrated.enhanced.ini \\
        --corpus scripts/enhanced-1.73-1158 --corpus scripts/1.73-3889
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

OFFSET_RE = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]*)"', re.MULTILINE)

# offsets.ini writes an array index as the positional name it chose (``[i
# /*642*/]``); the corpus writes whatever the decompiler named that local
# (``[iVar3 /*642*/]``). The /*N*/ comment is the stable part, so the index
# expression is wildcarded and the comment is matched exactly.
_INDEX_IN_PATTERN = re.compile(r"\\\[[^\]]*?(/\\\*\d+\\\*/)\\\]")


def globals_in(ini_text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in OFFSET_RE.finditer(ini_text)
            if m.group(2).startswith("Global_")]


def present(value: str, corpus: str) -> bool:
    """Whether this offset value names a path that exists verbatim in a corpus."""
    if not value:
        return False
    if "[" not in value:
        return value in corpus
    pattern = _INDEX_IN_PATTERN.sub(r"\\[[^\\]]*\1\\]", re.escape(value))
    try:
        return re.search(pattern, corpus) is not None
    except re.error:
        return False


def read_corpus(directory: pathlib.Path) -> str:
    return " ".join(p.read_text(errors="ignore") for p in sorted(directory.glob("*.c")))


def score(ini_text: str, corpus: str) -> tuple[int, int]:
    entries = globals_in(ini_text)
    return sum(1 for _, v in entries if present(v, corpus)), len(entries)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ini", required=True)
    ap.add_argument("--corpus", action="append", required=True,
                    help="A scripts/<build>/ directory. Repeatable.")
    args = ap.parse_args()

    ini_text = pathlib.Path(args.ini).read_text(encoding="utf-8")
    print(f"\n  {args.ini}")
    for d in args.corpus:
        directory = pathlib.Path(d)
        if not directory.is_dir():
            print(f"    [SKIP] no such corpus: {d}", file=sys.stderr)
            continue
        hit, total = score(ini_text, read_corpus(directory))
        pct = 100.0 * hit / total if total else 0.0
        print(f"    {directory.name:26s} {hit:4d}/{total:<4d} ({pct:4.1f}%)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
