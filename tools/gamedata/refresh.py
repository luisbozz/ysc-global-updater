#!/usr/bin/env python3
"""Rebuilds the Xenvious data that comes from the game files instead of the scripts.

- OfflineData/weapon_components.json: weapons, their attachment slots and names
  (weapmeta.ps1 + gxt.ps1 + weapon_components.py),
- OfflineData/rule_option_texts.json: the Mission Creator's titles and help texts of the rule
  options, six languages (gxt.ps1; the labels are the ones the file already has),
- Images/pickups/radar_*.png: the pickup blip icons (pickup_icons.py).

Runs in WSL and calls Windows PowerShell with CodeWalker.Core (netstandard2.0). Run it after a DLC
adds weapons or texts; script updates do not need it.

Usage
-----
    python3 tools/gamedata/refresh.py --game '<GTA V folder>' \\
        --codewalker '<path to CodeWalker.Core.dll>' \\
        --xenvious /opt/xenvious --script scripts/1.73-3889/fm_deathmatch_creator.c [--icons]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from tools.gamedata import weapon_components as wc  # noqa: E402

LANGS = wc.LANGS


def win(path: pathlib.Path) -> str:
    return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def ps(script: str, **args) -> None:
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win(HERE / script)]
    for k, v in args.items():
        cmd += [f"-{k}", v]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(f"{script}: {(res.stdout.strip().splitlines() or ['?'])[-1]}")
    if res.returncode != 0:
        raise SystemExit(res.stderr)


def gxt(game: str, cw: str, labels: set[str], work: pathlib.Path, name: str) -> pathlib.Path:
    lab, out = work / f"{name}.labels.txt", work / f"{name}.tsv"
    lab.write_text("\n".join(sorted(labels)), encoding="utf-8")
    ps("gxt.ps1", Game=game, Labels=win(lab), Out=win(out), CodeWalker=cw)
    return out


def texts(tsv: pathlib.Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for line in tsv.read_text(encoding="utf-8").splitlines():
        label, where, text = (line.split("\t", 2) + ["", ""])[:3]
        lang = next((code for folder, code in LANGS.items() if folder in where.lower()), None)
        if lang and text:
            out.setdefault(label, {}).setdefault(lang, text)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game", required=True, help="Windows path of the game install")
    ap.add_argument("--codewalker", required=True, help="Windows path of CodeWalker.Core.dll")
    ap.add_argument("--xenvious", required=True, type=pathlib.Path)
    ap.add_argument("--script", required=True, type=pathlib.Path, help="decompiled fm_deathmatch_creator.c (names of the finishes)")
    ap.add_argument("--work", type=pathlib.Path, default=pathlib.Path("/mnt/c/temp/xenvious-gamedata"))
    ap.add_argument("--icons", action="store_true", help="also download the pickup blip icons")
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    data = a.xenvious / "Xenvious" / "OfflineData"

    metas = a.work / "weapmeta"
    ps("weapmeta.ps1", Game=a.game, Out=win(metas), CodeWalker=a.codewalker)
    weapons, comps = wc.read_metas(metas, a.script)
    labels = {c["label"] for c in comps.values() if c["label"] and c["label"] != "WCT_INVALID"}
    tsv = gxt(a.game, a.codewalker, labels, a.work, "weapons")
    built = wc.build(weapons, comps, wc.read_gxt(tsv))
    (data / "weapon_components.json").write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f'weapon_components.json: {len(built["weapons"])} weapons, {len(built["components"])} components')

    rule_file = data / "rule_option_texts.json"
    old = json.loads(rule_file.read_text(encoding="utf-8"))
    new = texts(gxt(a.game, a.codewalker, set(old), a.work, "rules"))
    merged = {k: new.get(k) or v for k, v in old.items()}
    rule_file.write_text(json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"rule_option_texts.json: {len(merged)} labels, {sum(1 for k in old if k in new)} found again")

    if a.icons:
        from tools.gamedata.pickup_icons import download
        download(a.xenvious / "Xenvious" / "Images" / "pickups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
