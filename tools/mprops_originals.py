#!/usr/bin/env python3
"""Write the creators' original prop-menu models for Xenvious' Modded Props page.

Each creator script returns the model of a prop-menu slot from one big switch, compiled to
a run of ``PUSH_CONST_U32 <hash>; LEAVE 2,1`` entries. Xenvious finds that run in the
script's bytecode in memory and splits it into categories (MainWindow.ModdedProps.cs:
IndexOfPlaceholderRun, loadMProps). This tool runs the same steps on the unmodified script
from ``scrpatches/disasm/<build>/<script>.ysc.full``, so Xenvious can tell what a slot held
before anything was changed. Checked against a running game (Legacy 1.73, LTS): identical.

    python3 tools/mprops_originals.py --build 1.73-3889 --out ../xenvious/Xenvious/OfflineData/legacy/mprops.json
    python3 tools/mprops_originals.py --build enhanced-1.73-1158 --out ../xenvious/Xenvious/OfflineData/enhanced/mprops.json
"""
import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CREATORS = ["fm_race_creator", "fm_lts_creator", "fm_capture_creator", "fm_deathmatch_creator", "fm_survival_creator"]

PLACEHOLDER = bytes([0x2E, 0x02, 0x01, 0x28])   # LEAVE 2,1 followed by the next PUSH_CONST_U32
RUN_LENGTH = 4                                    # MPropsRunLength
READ_SIZE = 50000                                 # bytes Xenvious reads from the table start
GROUP_GAP = 40                                    # a larger gap starts a new category


def run_start(data):
    """IndexOfPlaceholderRun: the first placeholder followed by three more 7-8 bytes apart."""
    i = data.find(PLACEHOLDER)
    while i >= 0:
        n, at = 1, i
        while n < RUN_LENGTH:
            if data[at + 8:at + 12] == PLACEHOLDER:
                at += 8
            elif data[at + 7:at + 11] == PLACEHOLDER:
                at += 7
            else:
                break
            n += 1
        if n >= RUN_LENGTH:
            return i
        i = data.find(PLACEHOLDER, i + 1)
    raise SystemExit("prop table not found")


def table(path):
    data = path.read_bytes()
    start = run_start(data) - 4
    buf = data[start:start + READ_SIZE]
    groups, current, previous = [], [], -len(PLACEHOLDER)
    i = buf.find(PLACEHOLDER)
    while i >= 0:
        if i >= 4:
            if i - previous > GROUP_GAP and current:
                groups.append(current)
                current = []
            previous = i
            current.append(f"{int.from_bytes(buf[i - 4:i], 'little'):08X}")
        i = buf.find(PLACEHOLDER, i + len(PLACEHOLDER))
    if current:
        groups.append(current)
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, help="Folder under scrpatches/disasm/, e.g. 1.73-3889 or enhanced-1.73-1158.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    folder = ROOT / "scrpatches" / "disasm" / args.build
    data = {"build": args.build.replace("enhanced-", ""), "creators": {}}
    for creator in CREATORS:
        groups = table(folder / f"{creator}.ysc.full")
        data["creators"][creator] = groups
        print(f"{creator}: {len(groups)} categories, {sum(len(g) for g in groups)} slots")
    pathlib.Path(args.out).write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
