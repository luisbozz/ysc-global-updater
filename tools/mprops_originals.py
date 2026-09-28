#!/usr/bin/env python3
"""Write the creators' original prop-menu models for Xenvious' Modded Props page.

Every creator script has one function that returns the model of a prop-menu slot:
``switch (category) { case N: switch (index) { case i: return joaat("..."); } }``.
Xenvious finds that table in memory as a run of ``PUSH_CONST_U32 <hash>; LEAVE`` entries and
reads a slot at each LEAVE that is followed by the next entry's PUSH, so a category shows
all its returns except the last, and a category with fewer than two returns shows none.
This tool reproduces exactly that list from the decompiled script, in bytecode order, so
Xenvious can tell what a slot held before anything was changed.

    python3 tools/mprops_originals.py --build 1.73-3889 --out ../xenvious/Xenvious/OfflineData/legacy/mprops.json
    python3 tools/mprops_originals.py --build enhanced-1.73-1158 --out ../xenvious/Xenvious/OfflineData/enhanced/mprops.json

A return that is not a constant (a function call) is written as null: its slot exists but
Xenvious cannot know the original.
"""
import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CREATORS = ["fm_race_creator", "fm_lts_creator", "fm_capture_creator", "fm_deathmatch_creator", "fm_survival_creator"]


def joaat(text):
    h = 0
    for c in text.lower().encode():
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    return (h + (h << 15)) & 0xFFFFFFFF


# The table function: two parameters, an outer switch on the first, and category 0 slot 0
# is the construction fence in every creator of both games.
HEAD = re.compile(
    r"\n\w+ (func_\d+)\(\w+ \w+, int iParam1\)[^\n]*\n\{\n\tswitch \(\w+\)\n\t\{\n\t\tcase 0:\n\t\t\tswitch \(iParam1\)\n\t\t\t\{\n"
    r"\t\t\t\tcase 0:\n\t\t\t\t\treturn joaat\(\"prop_const_fence02b\"\);")


def table(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    m = HEAD.search(src)
    if not m:
        raise SystemExit(f"{path.name}: prop table function not found")
    body = src[m.start():src.index("\n}\n", m.start())]
    groups, current = [], None
    for line in body.split("\n"):
        if line.startswith("\t\tcase ") or line.startswith("\t\tdefault"):
            current = []
            groups.append(current)
            continue
        r = re.match(r"\t\t\t\t\treturn (.*);", line)
        if r and current is not None:
            value = r.group(1)
            j = re.fullmatch(r'joaat\("([^"]+)"\)', value)
            if j:
                current.append(joaat(j.group(1)))
            elif re.fullmatch(r"-?\d+", value):
                current.append(int(value) & 0xFFFFFFFF)
            else:
                current.append(None)
    # Only categories with a following entry have slots, and the last return is never read.
    return [[None if h is None else f"{h:08X}" for h in g[:-1]] for g in groups if len(g) >= 2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, help="Folder under scripts/, e.g. 1.73-3889 or enhanced-1.73-1158.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    folder = ROOT / "scripts" / args.build
    data = {"build": args.build.replace("enhanced-", ""), "creators": {}}
    for creator in CREATORS:
        data["creators"][creator] = table(folder / f"{creator}.c")
        sizes = [len(g) for g in data["creators"][creator]]
        print(f"{creator}: {len(sizes)} categories, {sum(sizes)} slots")
    pathlib.Path(args.out).write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
