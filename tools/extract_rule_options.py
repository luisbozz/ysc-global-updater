#!/usr/bin/env python3
"""Bit numbers of the per-rule options Xenvious shows on the Rules page (rule_options.json).

The Mission Creator's objective menu edits each yes/no option with one call per label, e.g.

    func_983("PMCT_GC_ALRTD", "PMCH_GC_ALRTD", &(Global_4718592.f_3838[t /*26988*/].f_8738[r]), 23, "PMC_SEL_YES", "PMC_SEL_NO", ...);
    func_1177("PMCT_ODO_RDAR", "PMCH_ODO_RDAR", &(... .f_8702[r]), 11, "PMCH_ODO_RDAR_E", "PMCH_ODO_RDAR_D", 0);

(public_mission_creator 1.73). For every label in OPTIONS this reads the field the call edits
and the bit number. Whether "on" means the bit is set: func_983 shows its 5th argument while
the bit is set, so a set bit reading Off / No / Disabled means the option is on while the bit
is clear; func_1177 with a last argument of 0 shows Disabled while the bit is set.

The field is written too: Xenvious' tools/builddata/gen.py checks it against the offsets.ini
key the option uses, so a field that moved without the ini following fails the build.

Usage
-----
    python3 tools/extract_rule_options.py --scripts scripts/<build>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

SCRIPT = "public_mission_creator.c"

# label -> offsets.ini key whose field the option's bit lives in.
OPTIONS = {
    "OL_TLSC": "rl_score", "OJT_ATL": "rl_score",
    "OL_WANT": "irbs", "OL_WANTF": "irbs", "LAW_NOLOSE": "irbs", "HO_TAS": "irbs", "HUD_HB": "irbs", "ODO_MCH": "irbs",
    "OJT_DOD": "rl_dod", "OJT_L_OUT": "rl_lout", "OJT_L_REQ": "rl_lreq",
    "LAW_HELI": "rl_heli",
    "GC_ALRTD": "rl_chase", "GC_ODIST": "rl_chase", "GC_FLEEOBJ": "rl_chase", "OJT_TXC": "rl_chase", "OL_MO": "rl_chase",
    "GC_AGGRO": "rl_aggro", "OB_CHASE": "rl_block",
    "ODO_NWEP": "rl_nwep", "ODO_RDAR": "rl_radar",
    "OMF_BOC": "rl_boc",
    # Team abilities (beast mode), per rule: func_4810 for "Active Objectives", func_5001 for the
    # abilities, func_4996 for "Invisibility Objective Overrides"; the field is the whole rule
    # array (&f_8504), the creator sets the bit on each rule.
    "AB_BSTS_ON": "irbs5", "BST_HELTH": "irbs5", "BST_DMG": "irbs5", "BST_MOV": "rl_boc",
    "BST_JMP": "irbs5", "BST_SPD": "irbs5", "BST_STELO": "irbs5",
    # Hint cam per rule: func_4810("PMCH_OOHCAM", &(... .f_8648), 0, "PMC_SEL_DBLED", ...), set = off.
    "OOHCAM": "rl_aggro",
}

OFF = {"PMC_SEL_OFF", "PMC_SEL_NO", "PMC_SEL_DBLED"}
_CALL = re.compile(r'(func_\d+)\("PMCT_(\w+)",\s*[^,]+,\s*&\(?Global_\d+\.f_\d+\[[^\]]+\]\.f_(\d+)\[[^\]]+\]\)?,\s*(\d+)\s*,\s*("[^"]*"|\d+|\w+)\s*,\s*("[^"]*"|\d+|\w+)\s*,\s*(-?\w+)\)')


# Bits edited per rule array (beast abilities, hint cam): func_5001("PMCT_BST_HELTH", "PMCH_BST_HELTH", 0, &(Global_4718592.f_3838[t /*26988*/].f_8504), 11);
# func_4996("PMCT_BST_STELO", "PMCT_BST_OSTEL", &(... .f_8504), 14); func_4810("PMCH_AB_BSTS_ON", &(... .f_8504), 7, ...).
_ARRAY = r'&\(Global_\d+\.f_\d+\[[^\]]+\]\.f_(\d+)\)'
_BEAST = [
    re.compile(r'func_\d+\("PMCT_(BST_\w+)",\s*"PMCH_\w+",\s*\d+,\s*' + _ARRAY + r',\s*(\d+)\)'),
    re.compile(r'func_\d+\("PMCT_(BST_STEL)O",\s*"\w+",\s*' + _ARRAY + r',\s*(\d+)\)'),
    re.compile(r'func_\d+\("PMCH_(\w+)",\s*' + _ARRAY + r',\s*(\d+),\s*("[^"]*")'),
]


def rule_options(scripts: pathlib.Path) -> dict:
    """label -> {"key", "field", "bit", "on_when_clear"}; labels the build does not have are left
    out (Xenvious hides those options). ValueError when a label edits two different bits or
    when none is found at all."""
    text = (scripts / SCRIPT).read_text(encoding="utf-8", errors="replace")
    found: dict = {}
    for m in _CALL.finditer(text):
        func, label, field, bit, set_label, clear_label, last = m.groups()
        if label not in OPTIONS:
            continue
        set_label = set_label.strip('"')
        if set_label in OFF:
            on_when_clear = True
        elif set_label.endswith("_E") and clear_label.strip('"').endswith("_D") and last in ("0", "false"):
            on_when_clear = True        # func_1177(..., helpE, helpD, 0): set = disabled
        else:
            on_when_clear = False
        entry = {"key": OPTIONS[label], "field": int(field), "bit": int(bit), "on_when_clear": on_when_clear}
        if label in found and found[label] != entry:
            raise ValueError(f"PMCT_{label} edits two different bits: {found[label]} / {entry}")
        found[label] = entry
    for pattern in _BEAST:
        for m in pattern.finditer(text):
            label, field, bit, *shown = m.groups()
            on_when_clear = bool(shown) and shown[0].strip('"') in OFF   # func_4810: label shown while set
            if label == "BST_STEL":
                label = "BST_STELO"
            if label not in OPTIONS:
                continue
            entry = {"key": OPTIONS[label], "field": int(field), "bit": int(bit), "on_when_clear": on_when_clear}
            if label in found and found[label] != entry:
                raise ValueError(f"PMCT_{label} edits two different bits: {found[label]} / {entry}")
            found[label] = entry
    if not found:
        raise ValueError(f"no objective menu option found in {SCRIPT}")
    return dict(sorted(found.items()))


def missing(found: dict) -> list[str]:
    return sorted(set(OPTIONS) - set(found))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scripts", required=True, type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args()
    data = rule_options(a.scripts)
    text = json.dumps(data, indent=1) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
