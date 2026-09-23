#!/usr/bin/env python3
"""Deploy a migrated offsets.ini into the production ini files Xenvious actually ships.

Both Xenvious targets are **not** a plain offsets.ini: they are a bigger,
multi-section ini ([AOB], [LINKS], ..., [OFFSETS], ...) and the [OFFSETS]
section itself uses a few OLDER, hand-written key names that the Xenvious C#
still reads directly (e.g. ``OFFSET_actor_locx``/``_locy``/``_locz`` instead of
this repo's simplified ``OFFSET_actor_loc``). A blind copy would silently drop
those and every other non-OFFSETS section (AOB patterns, vehicle lists, ...).

This tool instead **merges by name** into a copy of the target file:

1. Direct match: ``OFFSET_x`` exists with the same name in both -> value updated.
2. Known vector-split match (see ``VECTOR_SPLITS``): the target's three
   ``_x``/``_y``/``_z`` (or ``_locx``/``_locy``/``_locz``) keys are derived from
   this repo's one combined value.
3. Anything else in the target (AOB patterns, static hex/coord constants, a
   target-only key ysc-global-updater never tracked, ...) is left untouched.

Never writes in place unless ``--apply`` is given (a ``.bak`` is made first).
Default output goes to ``reports/deploy/<target-filename>`` for review.
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

OFFSET_RE = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]*)"')

# combined (this repo's) name -> the three split names the target ini actually
# uses. All four known cases follow the same "vector" shape: the combined value
# has no trailing bare field for the *_loc/_pos ones (x/y/z = value + .f_0/1/2),
# or already ends on the "x" field for the *_actv ones (x = value itself,
# y/z = last field + 1/+2). split_vector_xyz() below detects which shape applies.
VECTOR_SPLITS: dict[str, tuple[str, str, str]] = {
    "OFFSET_actor_loc": ("OFFSET_actor_locx", "OFFSET_actor_locy", "OFFSET_actor_locz"),
    "OFFSET_actor_actv": ("OFFSET_actor_actvx", "OFFSET_actor_actvy", "OFFSET_actor_actvz"),
    "OFFSET_dhprop_pos": ("OFFSET_dhprop_locx", "OFFSET_dhprop_locy", "OFFSET_dhprop_locz"),
    "OFFSET_weap_loc": ("OFFSET_weap_locx", "OFFSET_weap_locy", "OFFSET_weap_locz"),
}

_TRAILING_FIELD_RE = re.compile(r"^(.*\.f_)(\d+)$")


def split_vector_xyz(value: str) -> tuple[str, str, str]:
    """Derive the x/y/z split values from one combined vector value.

    Two shapes, both verified against the production ini (see repo memory):
    - no trailing ``.f_N`` (e.g. ``Global_X.f_Y[i /*S*/]``) -> x/y/z are that
      value plus ``.f_0``/``.f_1``/``.f_2`` (the "loc"/"pos" family).
    - trailing ``.f_N`` (e.g. ``...f_9.f_2``) -> x is the value unchanged, y/z
      bump the trailing field by +1/+2 (the "actv" family, x IS the combined
      value already).
    """
    m = _TRAILING_FIELD_RE.match(value)
    if m:
        prefix, n = m.group(1), int(m.group(2))
        return value, f"{prefix}{n + 1}", f"{prefix}{n + 2}"
    return f"{value}.f_0", f"{value}.f_1", f"{value}.f_2"


def parse_offset_map(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = OFFSET_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


# ---- array strides ---------------------------------------------------------
#
# ``OFFSET_props_next = 163`` is a plain integer, so the migrator skips it (it
# only rewrites ``Global_...`` paths) and the merge below files it under
# "static constant" and leaves it. It is not constant: it is the stride between
# two elements of the props array, and it moves with every build. Leaving it
# behind means element 0 reads correctly and every later one lands in the wrong
# place -- "only the first prop loads", and the same for actors and the rest.
#
# The migrated value is already in the file, in the sibling paths: the comment
# in ``OFFSET_props_loc = "Global_5242880.f_1[i /*170*/]"`` is that same stride,
# rewritten by the migration because that entry *is* a path. So it is copied
# from there rather than derived a second time.
#
# Checked against a hand-corrected offsets.ini: 18 of 27 entries resolve and all
# 18 agree with the hand-corrected value. The other 9 have no sibling paths and
# are reported rather than guessed.

_NEXT_NAME = re.compile(r"^OFFSET_\w+?_next$", re.IGNORECASE)
_STRIDE = re.compile(r"\[[ij]\s*/\*(\d+)\*/\]")


def family_strides(entries: dict) -> dict:
    """``OFFSET_<family>`` -> the stride its paths agree on, where they do.

    The innermost stride is the one that counts: ``pa`` walks
    ``[i /*26988*/] ... [j /*36*/]`` and its NEXT is 36, the inner step. A
    family needs at least two paths and a clear winner, else it is left alone.
    """
    per_family: dict = {}
    for name, value in entries.items():
        strides = _STRIDE.findall(value)
        if not strides:
            continue
        family = name.rsplit("_", 1)[0]
        while family.count("_") >= 1:
            per_family.setdefault(family, collections.Counter())[int(strides[-1])] += 1
            if family.count("_") == 1:
                break
            family = family.rsplit("_", 1)[0]

    out = {}
    for family, counts in per_family.items():
        if sum(counts.values()) < 2:
            continue
        ranked = counts.most_common()
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue
        out[family] = ranked[0][0]
    return out


def apply_strides(text: str) -> tuple:
    """Rewrite every ``*_next`` its family's paths disagree with.

    Returns ``(text, changed, unresolved)``.
    """
    entries = dict(re.findall(r"^(OFFSET_\w+)\s*=\s*(.+?)\s*$", text, re.M))
    strides = family_strides(entries)
    changed, unresolved = [], []

    def fix(line: str) -> str:
        m = re.match(r'^(OFFSET_\w+)(\s*=\s*)"?(\d+)"?(\s*)$', line)
        if not m or not _NEXT_NAME.match(m.group(1)):
            return line
        name, sep, value, tail = m.groups()
        want = strides.get(name.rsplit("_", 1)[0])
        if want is None:
            unresolved.append(name)
            return line
        if str(want) == value:
            return line
        changed.append((name, value, want))
        body = f'"{want}"' if '"' in line else str(want)
        return f"{name}{sep}{body}{tail}"

    return "\n".join(fix(l) for l in text.split("\n")), changed, unresolved


def merge(migrated_text: str, target_text: str,
          attested=None) -> tuple[str, dict, list[str], list[tuple[str, str, str]]]:
    """Merge migrated offsets into a full target ini.

    Returns ``(merged_text, stats, unmapped, kept)``.

    ``attested(path) -> bool`` says whether a path occurs literally in the new
    build's script corpus. When it is given, a replacement is **refused** if the
    new value is not attested while the value already in the target is. An
    offset the migration could not resolve keeps the value from 1.71, and a
    blind name merge would then overwrite a correct, previously deployed value
    with a stale one -- this is what happened to the whole ``OFFSET_SMS_*``
    family, which is unresolved against the Enhanced corpus while the deployed
    file already held the right root. Refusals are reported as ``kept``.
    """
    migrated = parse_offset_map(migrated_text)
    split_values = {name: split_vector_xyz(val) for name, val in migrated.items() if name in VECTOR_SPLITS}

    stats = {"updated": 0, "unchanged": 0, "target_only_static": 0,
             "target_only_global": 0, "kept_attested": 0}
    kept: list[tuple[str, str, str]] = []
    seen_targets: set[str] = set()
    out_lines: list[str] = []

    for line in target_text.splitlines():
        m = OFFSET_RE.match(line.strip())
        if not m:
            out_lines.append(line)
            continue
        name, cur_val = m.group(1), m.group(2)

        new_val = None
        if name in migrated:
            new_val = migrated[name]
            seen_targets.add(name)
        else:
            for combined, (nx, ny, nz) in VECTOR_SPLITS.items():
                if combined not in split_values:
                    continue
                x, y, z = split_values[combined]
                if name == nx:
                    new_val = x
                elif name == ny:
                    new_val = y
                elif name == nz:
                    new_val = z
                if new_val is not None:
                    seen_targets.add(combined)
                    break

        if new_val is None:
            stats["target_only_global" if cur_val.startswith("Global_") else "target_only_static"] += 1
            out_lines.append(line)
        elif new_val == cur_val:
            stats["unchanged"] += 1
            out_lines.append(line)
        elif (attested is not None and cur_val.startswith("Global_")
              and not attested(new_val) and attested(cur_val)):
            stats["kept_attested"] += 1
            kept.append((name, cur_val, new_val))
            out_lines.append(line)
        else:
            stats["updated"] += 1
            out_lines.append(re.sub(r'"[^"]*"', f'"{new_val}"', line, count=1))

    unmapped = sorted(set(migrated) - seen_targets)
    return ("\n".join(out_lines) + ("\n" if target_text.endswith("\n") else ""),
            stats, unmapped, kept)


# Since the offline-mode branch the app carries its own data. The old backend
# (xenvious_ctr/confFiles/web/resources/offsets.ini) is no longer a default
# target: pass it with --target if a legacy deploy is ever needed again.
DEFAULT_TARGETS = [
    "/opt/Xenvious/Xenvious/OfflineData/offsets.ini",
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--migrated", default="reports/offsets.migrated.ini",
                   help="Migrated offsets (source of new values). Default: reports/offsets.migrated.ini")
    p.add_argument("--target", action="append", dest="targets",
                   help="Production ini to merge into. Repeatable. Default: the two known Xenvious targets.")
    p.add_argument("--out-dir", default="reports/deploy",
                   help="Where to write the merged preview files (default: reports/deploy/).")
    p.add_argument("--corpus-dir",
                   help="Decompiled scripts of the NEW build (e.g. scripts/enhanced-1.73-1158). "
                        "When given, a replacement is refused if the new value does not occur in "
                        "that corpus while the value already in the target does -- unresolved "
                        "offsets then cannot overwrite a correct deployed value.")
    p.add_argument("--apply", action="store_true",
                   help="Write the merge directly into each --target (a .bak backup is made first). "
                        "Without this flag nothing outside reports/ is ever touched.")
    args = p.parse_args()

    def rel(pth: str) -> pathlib.Path:
        pp = pathlib.Path(pth)
        return pp if pp.is_absolute() else ROOT / pth

    migrated_path = rel(args.migrated)
    targets = [pathlib.Path(t) for t in (args.targets or DEFAULT_TARGETS)]
    migrated_text = migrated_path.read_text(encoding="utf-8")

    attested = None
    if args.corpus_dir:
        corpus_dir = rel(args.corpus_dir)
        corpus = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                           for f in sorted(corpus_dir.glob("*.c")))
        if not corpus:
            print(f"[SKIP] no *.c in corpus dir: {corpus_dir}", file=sys.stderr)
        else:
            flat = re.sub(r"\[[^\]]*\]", "", corpus)

            def attested(path: str, _flat=flat) -> bool:
                return re.sub(r"\[[^\]]*\]", "", path) in _flat

    out_dir = rel(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    any_missing = False
    # Preview names must depend only on this run's targets, never on files a
    # previous run left behind: an existence check would push the *first*
    # target to the "<parent>__<name>" form and leave the stale preview in
    # place, looking current.
    written: set[pathlib.Path] = set()
    for target in targets:
        if not target.is_file():
            print(f"[SKIP] target not found: {target}", file=sys.stderr)
            any_missing = True
            continue

        target_text = target.read_text(encoding="utf-8")
        merged_text, stats, unmapped, kept = merge(migrated_text, target_text, attested)

        print(f"\n=== {target} ===")
        print(f"  updated={stats['updated']}  unchanged={stats['unchanged']}  "
              f"target_only_static={stats['target_only_static']}  target_only_global={stats['target_only_global']}")
        if kept:
            print(f"  [KEPT] {len(kept)} offset(s) NOT overwritten: the migrated value does not occur "
                  f"in the new corpus while the deployed one does (unresolved migration):")
            for name, cur_val, new_val in kept:
                print(f"      {name}: kept {cur_val}  (migration offered {new_val})")
        if unmapped:
            print(f"  [REVIEW] {len(unmapped)} migrated offset(s) have no matching key in this target "
                  f"(new offset never added to production, or a naming mismatch):")
            for name in unmapped:
                print(f"      {name}")

        if args.apply:
            backup = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, backup)
            target.write_text(merged_text, encoding="utf-8")
            print(f"  [APPLIED] wrote {target}  (backup: {backup})")
        else:
            out_path = out_dir / target.name
            # keep multiple targets with the same filename apart
            if out_path in written:
                out_path = out_dir / f"{target.parent.name}__{target.name}"
            written.add(out_path)
            out_path.write_text(merged_text, encoding="utf-8")
            print(f"  preview: {out_path}")

    return 1 if any_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
