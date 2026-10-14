"""Step "lists": data Xenvious reads from the game's scripts besides offsets and patches.

- prop_lists.json, the prop model lists (boosters, blacklisted props, ...): from the
  decompiled creator scripts (tools/extract_prop_lists.py); capture_object_models, the models a
  capture object keeps on load (tools/extract_capture_models.py).
- race_vehicles.json: the race creator's vehicle classes incl. the DLC (ADLC) vehicles
  (tools/extract_race_vehicles.py). A vehicle without a known name is a review item.
- mprops.json: the original prop-menu models per creator (tools/mprops_originals.py).
- [ENTITYHANDLES] in offsets.ini: where each creator keeps the handles of its placed entities
  (tools/extract_entity_handles.py), written straight into the work offsets.ini.

More extractors go into EXTRACTORS: a function (new_build, edition, work dir, Xenvious'
OfflineData/<edition> dir) -> (files to deploy {OfflineData-relative name: work file},
review items, report lines).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .common import ROOT, SCRIPTS, Item

sys.path.insert(0, str(ROOT))


def prop_lists(new_build: str, edition: str, work: Path, offline: Path):
    out = work / "prop_lists.json"
    from tools.extract_prop_lists import dump, refresh_lists
    try:
        current = json.loads((offline / "prop_lists.json").read_text(encoding="utf-8-sig"))
        lists, rows = refresh_lists(current, SCRIPTS / new_build)
    except (FileNotFoundError, ValueError) as e:
        return {}, [Item(id="list:prop_lists", kind="info", title="prop_lists.json",
                         question=f"Prop-Listen nicht gelesen: {e}", note="Die alte Datei bleibt.")], []
    items = []
    # The models a capture object keeps when the Capture Creator loads the job.
    from tools.extract_capture_models import capture_models
    key = "capture_object_models"
    try:
        lists[key] = capture_models(SCRIPTS / new_build)
    except (FileNotFoundError, ValueError) as e:
        if key in current:
            lists[key] = current[key]
        items.append(Item(id=f"list:{key}", kind="info", title=key,
                          question=f"Liste nicht gelesen: {e}", note="Die alte Liste bleibt."))
    out.write_text(dump(lists), encoding="utf-8")
    lines = []
    if key in lists and lists[key] != current.get(key):
        old, new = set(current.get(key, [])), set(lists[key])
        lines.append(f"{key}: +{len(new - old)} -{len(old - new)} (jetzt {len(new)})")
    for key, n_now, n_found, added, kept, fixed in rows:
        if n_now is None:
            lines.append(f"{key}: fehlt in prop_lists.json")
        elif added:
            lines.append(f"{key}: +{added} (jetzt {n_now + added})")
    return {"prop_lists.json": out}, items, lines or ["Prop-Listen unverändert"]


def race_vehicles(new_build: str, edition: str, work: Path, offline: Path):
    out = work / "race_vehicles.json"
    names = ROOT / "data" / "race_vehicle_names.json"
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "extract_race_vehicles.py"),
                          new_build, str(out)], cwd=str(ROOT), capture_output=True, text=True)
    if res.returncode != 0 or not out.is_file():
        return {}, [Item(id="list:race_vehicles", kind="info", title="race_vehicles.json",
                         question="Fahrzeugtabelle nicht gelesen: "
                                  + (res.stderr.strip().splitlines() or ["?"])[-1],
                         note="Die alte Datei bleibt.")], []
    data = json.loads(out.read_text(encoding="utf-8"))
    # Names Xenvious already has (the shipped file) fill what the public dump lacks.
    known = {}
    if (offline / "race_vehicles.json").is_file():
        for c in json.loads((offline / "race_vehicles.json").read_text(encoding="utf-8-sig")):
            for e in c["base"] + c["dlc"]:
                if e.get("model"):
                    known[e["hash"]] = e
    for c in data:
        for k, e in enumerate(c["base"] + c["dlc"]):
            if not e.get("model") and e["hash"] in known:
                e.update({x: known[e["hash"]][x] for x in ("model", "names") if x in known[e["hash"]]})
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    missing = [(c, e) for c in data for e in c["base"] + c["dlc"] if not e.get("model")]
    if len(missing) > 40:
        return {}, [Item(id="list:race_vehicles", kind="info", title="race_vehicles.json",
                         question=f"{len(missing)} Fahrzeuge ohne Namen: die Namensliste "
                                  "(DurtyFree gta-v-data-dumps) wurde wohl nicht geladen.",
                         note="Die alte Datei bleibt. Mit Internet noch einmal: --redo lists")], []
    items = []
    for c in data:
        for e in c["base"] + c["dlc"]:
            if not e.get("model"):
                items.append(Item(
                    id=f"vehicle:{e['hash']}", kind="vehicle",
                    title=f"Rennfahrzeug 0x{e['hash'] & 0xFFFFFFFF:08X} (Klasse {c['class']})",
                    question="Neues Fahrzeug ohne Namen. Modellname eintragen (z. B. buffalo5).",
                    note=f"Kommt in {names.relative_to(ROOT)}; Anzeigenamen dort ergänzen."))
    total = sum(len(c["base"]) + len(c["dlc"]) for c in data)
    return {"race_vehicles.json": out}, items, [f"race_vehicles.json: {len(data)} Klassen, {total} Fahrzeuge"]


def mprops(new_build: str, edition: str, work: Path, offline: Path):
    out = work / "mprops.json"
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "mprops_originals.py"),
                          "--build", new_build, "--out", str(out)],
                         cwd=str(ROOT), capture_output=True, text=True)
    if res.returncode != 0:
        return {}, [Item(id="list:mprops", kind="info", title="mprops.json",
                         question="Prop-Menü nicht gelesen: " + (res.stderr.strip().splitlines() or ["?"])[-1],
                         note="Die alte Datei bleibt.")], []
    return {"mprops.json": out}, [], ["mprops.json: " + "; ".join(res.stdout.strip().splitlines()[:6])]


EXTRACTORS = [prop_lists, race_vehicles, mprops]


def entity_handles(new_build: str, work: Path, name: str = "offsets.ini"):
    """[ENTITYHANDLES] into work/<name> -> (review items, report lines). A kind no create
    function leads to is a review item: Xenvious then rebuilds for that kind instead of moving."""
    from tools.extract_entity_handles import KINDS, set_section, value, values
    ini = work / name
    text = ini.read_text(encoding="utf-8")
    vals = values(SCRIPTS / new_build, text)
    ini.write_text(set_section(text, vals, new_build), encoding="utf-8")
    items, lines = [], []
    for script, found in vals.items():
        # The Race Creator has no teams; the Mission Creator draws no spawn point decals.
        missing = [k for k in KINDS if k not in found and not (k == "team" and script == "fm_race_creator")
                   and not (k == "spawn" and script == "public_mission_creator")]
        lines.append(f"[ENTITYHANDLES] {script}: {value(found)}")
        if missing:
            items.append(Item(id=f"handles:{script}", kind="info", title=f"[ENTITYHANDLES] {script}",
                              question=f"Keine Handle-Arrays gefunden für: {', '.join(missing)}.",
                              note="Xenvious baut für diese Arten neu auf, statt das Entity zu verschieben."))
    return items, lines


def name_vehicles(items: list[Item]) -> None:
    """Answered vehicle items go into data/race_vehicle_names.json (model as every name)."""
    path = ROOT / "data" / "race_vehicle_names.json"
    names = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for it in items:
        if it.kind != "vehicle" or not it.answer:
            continue
        h = it.id.split(":", 1)[1]
        model = it.answer.strip().lower()
        if h not in names:
            names[h] = {"model": model, "names": {k: model for k in
                                                   ("ger", "eng", "ru", "pl", "fr", "zh_cn")}}
            changed = True
    if changed:
        path.write_text(json.dumps(names, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
