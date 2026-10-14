#!/usr/bin/env python3
"""Refresh Xenvious' prop model lists (OfflineData/<edition>/prop_lists.json) from a build's creator scripts.

prop_lists.json carries seven lists (prop_model_booster, _slowdown,
_centitydef_whitelist, _stunt_with_color_option, _blacklisted, _raceonly,
dprop_model_activationtimer); Xenvious compiles them in (tools/builddata/gen.py). They come from the creator scripts, where each list
is written as a condition over model hashes:

    if (model == joaat("stt_prop_track_speedup") || model == joaat("...") || ...)

and new DLC props are added to those conditions with every game update.

How a list is refreshed
-----------------------
    result = base  ∪  every condition line that consists mostly of base hashes

"Mostly" is at least two hashes and at least half of the line. The base is
data/prop_lists_seed.json, not prop_lists.json: anchoring on the tool's own output made
every rerun grow the list, and after a few rounds it had drifted into unrelated
props (coke tables, trolleys in the stunt colour list). A fixed base keeps the
tool a pure function of (base, scripts), so reruns give the same result.

Entries from the base that no matching condition contains are kept and
reported: some were added by hand. Hand additions go into the base file.

First run on 1.73-3889 (Legacy) and enhanced-1.73-1158, same result for both:
booster +1, whitelist +3, stunt_with_color +196, blacklisted +27; slowdown and
activationtimer unchanged. 18 stunt and 33 blacklisted base entries are in no
matching condition and stay.

Format
------
Xenvious compares the first four lists as model.ToString("X") -- upper-case hex
without leading zeros -- and the last three as signed decimal. The old ini had entries
with a leading zero ("0516376C") and one with a stray space; they could never
match. Output is written in the canonical form for each list.

Usage
-----
    python3 tools/extract_prop_lists.py --json <prop_lists.json> --scripts scripts/<build>
    python3 tools/extract_prop_lists.py --json <prop_lists.json> --scripts scripts/<build> --write

Without --write it only reports.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

LISTS = {
    "prop_model_booster": "hex",
    "prop_model_slowdown": "hex",
    "prop_model_centitydef_whitelist": "hex",
    "prop_model_stunt_with_color_option": "hex",
    "prop_model_blacklisted": "dec",
    "prop_model_raceonly": "dec",
    "dprop_model_activationtimer": "dec",
}
CREATORS = ("fm_race_creator", "fm_lts_creator", "fm_capture_creator",
            "fm_deathmatch_creator", "fm_survival_creator")

_JOAAT = re.compile(r'joaat\("([^"]+)"\)')
_EQ_INT = re.compile(r"==\s*(-?\d{5,})\b")          # hashes the decompiler could not name


def joaat(s: str) -> int:
    h = 0
    for c in s.lower().encode():
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    return (h + (h << 15)) & 0xFFFFFFFF


def parse_value(raw: str, kind: str) -> set[int]:
    # the kind decides, not the spelling: "12345678" is a hex hash in a hex list
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if x:
            out.add(int(x, 16) if kind == "hex" else int(x) & 0xFFFFFFFF)
    return out


def fmt(values: set[int], kind: str) -> str:
    if kind == "hex":
        return ",".join(f"{v:X}" for v in sorted(values))
    return ",".join(str(v - (1 << 32) if v >= 1 << 31 else v) for v in sorted(values))


def condition_sets(scripts: pathlib.Path) -> list[frozenset[int]]:
    seen = set()
    for name in CREATORS:
        f = scripts / f"{name}.c"
        if not f.is_file():
            continue
        for line in f.read_text(errors="replace").splitlines():
            if "joaat(" not in line and "==" not in line:
                continue
            hs = {joaat(n) for n in _JOAAT.findall(line)}
            hs |= {int(n) & 0xFFFFFFFF for n in _EQ_INT.findall(line)}
            if len(hs) >= 2:
                seen.add(frozenset(hs))
    return list(seen)


def refresh(old: set[int], conds: list[frozenset[int]]) -> set[int]:
    found = set()
    for hs in conds:
        if len(hs & old) >= max(2, len(hs) / 2):
            found |= hs
    return found


# Lists that are one exact condition in the scripts, found by the first two models of it,
# instead of the seed heuristic. prop_model_blacklisted is IS_PROP_A_DEV_ONLY_PLACEABLE_PROP
# (FMMC_Cloud_loader.sch): when a user-made job loads, every prop on it is replaced by
# PROP_CONST_FENCE02B. The seed version had drifted into unrelated models (even peds).
# prop_model_raceonly is IS_PROP_A_RACE_ONLY_PROP: outside races they become the fence too.
# The whole function counts, not only the anchored condition: IS_PROP_A_DEV_ONLY_PLACEABLE_PROP
# has a second check (the UFO ships gr_prop_damship_01a / imp_prop_ship_01a, dev-only while
# a tunable bit is off).
ANCHORED = {
    "prop_model_blacklisted": ("prop_sec_gate_01d", "prop_vault_shutter"),
    "prop_model_raceonly": ("ch_prop_track_paddock_01", "sum_prop_ac_track_paddock_01"),
}


def anchored_set(scripts: pathlib.Path, first: str, second: str) -> set[int]:
    # The first anchor must open the condition (other lists contain it further in) and the
    # second must be in the same condition; the order behind the first changes between builds
    # (1.71: sec_gate, ship, vault_shutter; 1.73: sec_gate, vault_shutter).
    pattern = re.compile(rf'if \(+\w+ == joaat\("{re.escape(first)}"\) \|\|[^\n]*== joaat\("{re.escape(second)}"\)')
    found = set()
    for name in CREATORS:
        f = scripts / f"{name}.c"
        if not f.is_file():
            continue
        text = f.read_text(errors="replace")
        for m in pattern.finditer(text):
            # the function around the anchor: from its header line to the closing brace
            start = text.rfind("\n}\n", 0, m.start()) + 3
            end = text.find("\n}\n", m.start())
            body = text[start:end]
            found |= {joaat(n) for n in _JOAAT.findall(body)}
            found |= {int(n) & 0xFFFFFFFF for n in _EQ_INT.findall(body)}
    return found


SEED = pathlib.Path(__file__).resolve().parents[1] / "data" / "prop_lists_seed.json"


def refresh_lists(current: dict, scripts: pathlib.Path, seed_path: pathlib.Path = SEED):
    """Return ``(new_lists, rows)``; rows are ``(key, now, found, added, kept, fixed)``.

    ``current`` is prop_lists.json: list name -> entries (strings). ``added`` counts
    entries a list gains. A list missing from ``current`` yields a row of Nones and is
    left out. Raises FileNotFoundError without creator scripts, so a wrong --scripts
    never empties anything.
    """
    seed = json.loads(pathlib.Path(seed_path).read_text(encoding="utf-8"))
    conds = condition_sets(pathlib.Path(scripts))
    if not conds:
        raise FileNotFoundError(f"no creator scripts under {scripts}")
    rows, out = [], {}
    for key, kind in LISTS.items():
        if key not in current:
            rows.append((key, None, None, None, None, None))
            continue
        raw = current[key]
        old = parse_value(",".join(raw), kind)      # what the file has now, for the report only
        if key in ANCHORED:
            found = anchored_set(pathlib.Path(scripts), *ANCHORED[key])
            if not found:
                raise FileNotFoundError(f"{key}: the condition was not found under {scripts}")
            base, new = found, found
        else:
            base = parse_value(",".join(map(str, seed[key])), kind)
            found = refresh(base, conds)
            new = base | found
        # entries whose spelling could never match at runtime (leading zero, spaces)
        fixed = sum(1 for x in raw
                    if x != x.strip() or (kind == "hex" and len(x.strip()) > 1 and x.strip()[0] == "0"))
        rows.append((key, len(old), len(found), len(new - old), len(base - found), fixed))
        out[key] = fmt(new, kind).split(",") if new else []
    return out, rows


def dump(lists: dict) -> str:
    """prop_lists.json as Xenvious keeps it."""
    return json.dumps(lists, indent=1) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="Xenvious OfflineData/<edition>/prop_lists.json to refresh")
    ap.add_argument("--scripts", required=True, help="decompiled scripts of the target build")
    ap.add_argument("--write", action="store_true", help="write the refreshed lists into --json")
    ap.add_argument("--seed", default=str(SEED), help="reviewed base lists (default: data/prop_lists_seed.json)")
    args = ap.parse_args()

    path = pathlib.Path(args.json)
    text = path.read_text(encoding="utf-8-sig")
    try:
        lists, rows = refresh_lists(json.loads(text), pathlib.Path(args.scripts), pathlib.Path(args.seed))
        new_text = dump(lists)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    print(f"  {'list':<36} {'now':>5} {'found':>6} {'added':>6} {'kept*':>6} {'fixed':>6}")
    for key, n_now, n_found, added, kept, fixed in rows:
        if n_now is None:
            print(f"  {key:<36} missing in {path.name}")
        else:
            print(f"  {key:<36} {n_now:5} {n_found:6} {added:+6} {kept:6} {fixed:6}")
    print("\n  * kept: in the base but in no matching condition -- hand-added or stale; review")
    if args.write and new_text != text:
        path.write_text(new_text, encoding="utf-8")
        print(f"\nwritten: {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
