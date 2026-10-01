"""Extract the race creator's per-class vehicle tables for Xenvious.

The race creator lists each class's vehicles in two tables, both nested
SWITCH statements on (race type, class, index) that return a model hash:
the base vehicles (bits of aveh[class]) and the DLC vehicles (bits of the
adlc words; index i is word i // 31, bit i % 31). Only the land race types
are read (the table's case 0). Names and display names come from
DurtyFree's gta-v-data-dumps vehicles.json; vehicles newer than the dump come
from data/race_vehicle_names.json (model and gameName from the game's
vehicles.meta, names from its global.gxt2, read with CodeWalker).

Reads the compiled script, not the decompiled .c: the decompiler loses the
base table's returns (Enhanced) or its class labels (Legacy).

    python3 tools/extract_race_vehicles.py <build> <out.json> [--dump vehicles.json]
"""
import argparse, json, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scrpatches"))
from scrasm import yscfull, disasm  # noqa: E402

DUMP_URL = "https://raw.githubusercontent.com/DurtyFree/gta-v-data-dumps/master/vehicles.json"
LANGS = {"ger": "German", "eng": "English", "ru": "Russian", "pl": "Polish", "fr": "French", "zh_cn": "SimplifiedChinese"}


def ins_at(code, addr):
    for n in range(1, 4000):
        try:
            return disasm.disassemble(code, start=addr, end=addr + n)[0]
        except Exception:
            continue
    raise ValueError(hex(addr))


def next_switch(code, addr):
    for _ in range(40):
        i = ins_at(code, addr)
        if i.name == "SWITCH":
            return i
        addr = i.jump_target if i.name == "J" else addr + i.length
    return None


def leaf_hash(code, addr):
    for _ in range(6):
        i = ins_at(code, addr)
        if i.name == "PUSH_CONST_U32":
            return int.from_bytes(bytes(i.operands[:4]), "little")
        addr = i.jump_target if i.name == "J" else addr + i.length
    return None


def table(code, func):
    land = dict(next_switch(code, func).switch_cases())[0]
    out = {}
    for cls, target in next_switch(code, land).switch_cases():
        idx = next_switch(code, target)
        if idx is not None:
            out[cls] = [leaf_hash(code, t) for _, t in sorted(idx.switch_cases())]
    return out


def find_func(code, first_hash, leave, enter):
    """The function whose first case returns first_hash (unique in 1.73)."""
    at = code.find(b"\x28" + first_hash.to_bytes(4, "little") + leave)
    if at < 0:
        raise SystemExit("table not found")
    return code.rfind(enter, 0, at)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("build")
    ap.add_argument("out")
    ap.add_argument("--dump")
    a = ap.parse_args()
    code = yscfull.YscFull.parse(str(ROOT / "scrpatches/disasm" / a.build / "fm_race_creator.ysc.full")).code
    adder, rhapsody = 0xB779A091, 0x322CF98F
    base = table(code, find_func(code, adder, b"\x2E\x04\x01", b"\x2D\x04"))
    dlc = table(code, find_func(code, rhapsody, b"\x2E\x03\x01", b"\x2D\x03"))
    dump = json.load(open(a.dump) if a.dump else urllib.request.urlopen(DUMP_URL))
    by_hash = {v["Hash"]: v for v in dump}

    extra = json.load(open(ROOT / "data/race_vehicle_names.json", encoding="utf-8"))

    def entry(h):
        v = by_hash.get(h)
        if not v and str(h) in extra:
            return {"hash": h, "model": extra[str(h)]["model"], "names": extra[str(h)]["names"]}
        e = {"hash": h, "model": v["Name"].lower() if v else None}
        if v and v.get("DisplayName"):
            e["names"] = {k: v["DisplayName"].get(src) or v["DisplayName"].get("English") for k, src in LANGS.items()}
        return e

    classes = sorted(set(base) | set(dlc))
    out = [{"class": c, "base": [entry(h) for h in base.get(c, [])], "dlc": [entry(h) for h in dlc.get(c, [])]} for c in classes]
    missing = [hex(e["hash"]) for c in out for e in c["base"] + c["dlc"] if not e["model"]]
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{a.build}: {len(classes)} classes, {sum(len(c['base']) + len(c['dlc']) for c in out)} vehicles, unresolved {len(missing)} {missing[:10]}")


if __name__ == "__main__":
    main()
