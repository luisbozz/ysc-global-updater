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


def merge(migrated_text: str, target_text: str) -> tuple[str, dict, list[str]]:
    """Merge migrated offsets into a full target ini. Returns (merged_text, stats, unmapped)."""
    migrated = parse_offset_map(migrated_text)
    split_values = {name: split_vector_xyz(val) for name, val in migrated.items() if name in VECTOR_SPLITS}

    stats = {"updated": 0, "unchanged": 0, "target_only_static": 0, "target_only_global": 0}
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
        else:
            stats["updated"] += 1
            out_lines.append(re.sub(r'"[^"]*"', f'"{new_val}"', line, count=1))

    unmapped = sorted(set(migrated) - seen_targets)
    return "\n".join(out_lines) + ("\n" if target_text.endswith("\n") else ""), stats, unmapped


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
        merged_text, stats, unmapped = merge(migrated_text, target_text)

        print(f"\n=== {target} ===")
        print(f"  updated={stats['updated']}  unchanged={stats['unchanged']}  "
              f"target_only_static={stats['target_only_static']}  target_only_global={stats['target_only_global']}")
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
