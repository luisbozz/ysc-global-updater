#!/usr/bin/env python3
"""Builds Xenvious' OfflineData/weapon_components.json from the game's weapon metas.

A weapon pickup in a job carries up to six weapon components (comps0..5 in the job file,
Global_4980736.f_60342[i].f_17 in memory). The mission controller gives each of them to the
player's weapon when the pickup is collected (GIVE_WEAPON_COMPONENT_TO_PED). Which components
fit which weapon, and their names, only exist in the game data:

Run tools/gamedata/refresh.py, which calls weapmeta.ps1 and gxt.ps1 (CodeWalker, Windows) and
then this module, for all game-file data of Xenvious at once.

Later files override earlier ones (update.rpf and the dlc_patch folders over the base game), the
same order the game loads them.

Usage
-----
    python3 tools/gamedata/refresh.py --help
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

LANGS = {"american": "en", "german": "de", "russian": "ru", "polish": "pl", "french": "fr", "chinesesimp": "zh_cn"}


def joaat(s: str) -> int:
    h = 0
    for c in s.lower().encode():
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h - (1 << 32) if h & 0x80000000 else h


def _tag(block: str, name: str) -> str | None:
    m = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return m.group(1).strip() if m else None


def _value(block: str, name: str) -> int | None:
    m = re.search(rf'<{name} value="(-?\d+)"', block)
    return int(m.group(1)) if m else None


def _order(path: pathlib.Path):
    # Base game first, then DLC packs, then update.rpf (incl. its dlc_patch folders), which wins.
    n = path.name
    return (n.startswith("update__update.rpf"), n)


def read_metas(folder: pathlib.Path, script: pathlib.Path | None = None):
    weapons, comps = {}, {}
    for f in sorted(folder.iterdir(), key=_order):
        text = f.read_text(encoding="utf-8", errors="replace")
        for block in re.split(r'<Item type="CWeaponInfo">', text)[1:]:
            name = _tag(block, "Name")
            if not name:
                continue
            slots = {}
            ap = re.search(r"<AttachPoints>(.*?)</AttachPoints>", block, re.S)
            if ap:
                for pm in re.finditer(r"<AttachBone>([^<]+)</AttachBone>\s*<Components>(.*?)</Components>", ap.group(1), re.S):
                    slot = slots.setdefault(pm.group(1).strip(), {"items": [], "default": None})
                    for c in re.finditer(r'<Name>([^<]+)</Name>\s*<Default value="(\w+)"', pm.group(2)):
                        comp = c.group(1).strip().upper()
                        if comp not in slot["items"]:
                            slot["items"].append(comp)
                        if c.group(2) == "true":
                            slot["default"] = comp
            weapons[name.upper()] = {"label": _tag(block, "HumanNameHash"), "clip": _value(block, "ClipSize"), "slots": slots}
        parts = re.split(r'<Item type="(CWeaponComponent\w*)">', text)
        for kind, block in zip(parts[1::2], parts[2::2]):
            name = _tag(block, "Name")
            if name:
                comps[name.upper()] = {"kind": kind, "label": _tag(block, "LocName"), "clip": _value(block, "ClipSize")}
    if script:
        for name, label in script_labels(script).items():
            if name in comps and comps[name]["label"] in (None, "WCT_INVALID"):
                comps[name]["label"] = label
    return weapons, comps


def read_gxt(path: pathlib.Path) -> dict[str, dict[str, str]]:
    texts: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        label, where, text = (line.split("\t", 2) + ["", ""])[:3]
        lang = next((code for folder, code in LANGS.items() if folder in where.lower()), None)
        if lang and text:
            texts.setdefault(label, {})[lang] = text
    return texts


def script_labels(script: pathlib.Path) -> dict[str, str]:
    """Component -> label from the DM Creator's component name function (func_2196 in 1.73),
    for the finishes (VARMOD) whose meta says WCT_INVALID."""
    labels, pending = {}, []
    for line in script.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'\s*case joaat\("(component_[a-z0-9_]+)"\):', line)
        if m:
            pending.append(m.group(1).upper())
            continue
        m = re.match(r'\s*return "(W[CT]T?_[A-Z0-9_]+)";', line)
        if m and pending:
            for c in pending:
                labels.setdefault(c, m.group(1))
        if line.strip() and not line.strip().startswith("case "):
            pending = []
    return labels


def build(weapons, comps, texts):
    used = set()
    out_weapons = {}
    for name, w in sorted(weapons.items()):
        slots = [{"bone": bone, "default": s["default"], "items": s["items"]} for bone, s in w["slots"].items() if s["items"]]
        for s in slots:
            used.update(s["items"])
        if slots or w["clip"]:
            out_weapons[name] = {"clip": w["clip"] or 0, "slots": slots}
    out_comps = {}
    for name in sorted(used):
        c = comps.get(name, {})
        label = c.get("label")
        names = texts.get(label, {}) if label else {}
        if not names.get("en"):
            names = {**names, "en": name.replace("COMPONENT_", "").replace("_", " ").title()}
        out_comps[name] = {"hash": joaat(name), "clip": c.get("clip") or 0, "kind": c.get("kind", ""), "name": names}
    return {"weapons": out_weapons, "components": out_comps}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--metas", required=True, type=pathlib.Path)
    ap.add_argument("--script", type=pathlib.Path, help="decompiled fm_deathmatch_creator.c: names of the finishes")
    ap.add_argument("--labels", type=pathlib.Path, help="write the labels to look up for weapgxt.ps1")
    ap.add_argument("--gxt", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args()
    weapons, comps = read_metas(a.metas, a.script)
    if a.labels:
        labels = {c["label"] for c in comps.values() if c["label"] and c["label"] != "WCT_INVALID"} | {w["label"] for w in weapons.values() if w["label"]}
        a.labels.write_text("\n".join(sorted(labels)), encoding="utf-8")
        print(f"{len(labels)} labels")
    if a.gxt and a.out:
        data = build(weapons, comps, read_gxt(a.gxt))
        a.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        named = sum(1 for c in data["components"].values() if "de" in c["name"])
        print(f'{len(data["weapons"])} weapons, {len(data["components"])} components, {named} with game names')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
