"""Assembles the page payloads (src/<edition>/<script>.ysa + the drawer) into a scrpatches.json.

Each creator's "custom funcs in extra page" entry gets src/<edition>/<script>.ysa followed by the drawer
(drawer.py), assembled at its page_base. The patches that CALL into the page get the address of
their label in the new payload (HOOKS), so a payload may change size freely.

Without --write it only reports. Default target: Xenvious' scrpatches.json of the edition.
  python3 build_page.py --edition enhanced|legacy [--write] [--target path] [--build name]
"""
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE))
from scrasm.yscfull import YscFull
from scrasm.natives import NativeResolver
from scrasm.asm import assemble_text, _tokenize, _size
from scrasm.disasm import disassemble
import drawer
import zonelabel

PAGE = 0x4000
DISASM = HERE.parents[2] / "disasm"
XENVIOUS = Path("/opt/xenvious/Xenvious/OfflineData")
# Build folder in disasm/ each edition's sources are written for.
BUILDS = {"enhanced": "enhanced-1.73-1158", "legacy": "1.73-3889"}

# Patches outside the page whose CALL goes to a label of the payload.
HOOKS = {
    ("fm_lts_creator", "run patched loop func without args"): "dl_hook",
    ("fm_lts_creator", "show hidden placement categories"): "fn7",
    ("fm_capture_creator", "run patched loop func without args"): "dl_hook",
    ("fm_capture_creator", "show hidden placement categories"): "fn7",
    ("fm_race_creator", "run patched loop func without args"): "dl_hook",
    ("fm_race_creator", "custom funcs in race test"): "fn13",
    ("fm_survival_creator", "run patched loop func without args"): "dl_hook",
    ("fm_deathmatch_creator", "run patched loop func without args"): "dl_hook",
    ("public_mission_creator", "run custom funcs from the main loop"): "hook",
    ("fm_lts_creator", "zone labels"): "zl_wrap",
    ("fm_capture_creator", "zone labels"): "zl_wrap",
    ("fm_deathmatch_creator", "zone labels"): "zl_wrap",
    ("fm_race_creator", "zone labels"): "zl_wrap",
}
# Legacy keeps the draw call its hook replaces (dl_hook makes it); the hook site, its CALL at +0x17.
LOOP_HOOK = "61 ? ? ? 41 ? 77 58 11 00"


def victims(edition):
    """Legacy: script -> (code pc, string array pc) from [SCRIPTVICTIM]; the payload must end before the array."""
    out = {}
    ini = (XENVIOUS / edition / "offsets.ini").read_text(encoding="utf-8-sig")
    m = re.search(r"^\[SCRIPTVICTIM\](.*?)(?=^\[|\Z)", ini, re.S | re.M)
    for line in (m.group(1) if m else "").splitlines():
        v = re.match(r'\s*(\w+)\s*=\s*"([^"]+)"', line)
        if v:
            stub, code, data, strings = (int(x.strip(), 16) for x in v.group(2).split(","))
            out[v.group(1)] = (code, strings)
    return out


# Scripts whose own source calls dl_main; the others get dl_hook (fn0, then dl_main).
OWN_HOOK = {"public_mission_creator"}


def labels(text, base):
    out, off = {}, base
    for tok in _tokenize(text):
        if tok[0] == "label":
            out[tok[1]] = off
        else:
            off += _size(tok[1], tok[2])
    return out


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=sorted(BUILDS), required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--target", type=Path)
    ap.add_argument("--build")
    a = ap.parse_args()
    a.target = a.target or XENVIOUS / a.edition / "scrpatches.json"
    a.build = a.build or BUILDS[a.edition]

    text = a.target.read_text(encoding="utf-8-sig")
    patches = json.loads(text)
    spans = [m.span(1) for m in re.finditer(r'"bytes_to_patch": "([^"]*)"', text)]
    assert len(spans) == len(patches), (len(spans), len(patches))

    room = victims(a.edition) if a.edition == "legacy" else {}
    new = {}
    for i, p in enumerate(patches):
        if not p.get("page"):
            continue
        s, base = p["script_name"], p["page_base"]
        src = (HERE / "src" / a.edition / f"{s}.ysa").read_text()
        full = YscFull.parse(DISASM / a.build / f"{s}.ysc.full")
        nat = NativeResolver.from_full(full).index_of_name()
        keep = None
        if a.edition == "legacy" and s not in OWN_HOOK:
            [at] = zonelabel.find(full.code, LOOP_HOOK)
            c = full.code
            assert c[at + 0x17] == 0x5D, s
            keep = (int.from_bytes(c[at + 0x18:at + 0x1B], "little"), int.from_bytes(c[at + 1:at + 4], "little"), c[at + 5])
        asm = src + "\n" + drawer.gen(None if s in OWN_HOOK else base, keep)
        zone = zonelabel.site(full)
        if zone:
            asm += "\n" + zonelabel.gen(zone)
        code = assemble_text(asm, base=base, natives=nat)
        lab = labels(asm, base)
        assert len(code) <= PAGE - base % PAGE, (s, len(code))
        if s in room:
            assert base == room[s][0] and base + len(code) <= room[s][1], (s, hex(base), len(code), hex(room[s][1]))
        # Every CALL into the page must land on an ENTER of the page.
        ins = disassemble(code, base=base)
        starts = {x.offset for x in ins if x.name == "ENTER"}
        for x in ins:
            if x.name == "CALL" and base <= x.call_target < base + PAGE - base % PAGE:
                assert x.call_target in starts, (s, hex(x.offset), hex(x.call_target))
        old = p["bytes_to_patch"].strip()
        new[i] = hexs(code)
        print(f"{s}: {len(old.split())} -> {len(code)} bytes" + ("" if new[i] != old else ", unchanged"))

        for j, h in enumerate(patches):
            label = HOOKS.get((h["script_name"], h["patch_name"].strip()))
            if h["script_name"] != s or label is None:
                continue
            b = bytearray.fromhex(h["bytes_to_patch"].replace(" ", ""))
            into = [x for x in disassemble(bytes(b)) if x.name == "CALL" and base <= x.call_target < base + PAGE - base % PAGE]
            assert len(into) == 1, (s, h["patch_name"])
            at = into[0].offset + 1
            b[at:at + 3] = lab[label].to_bytes(3, "little")
            assert lab[label] in starts, (s, label)
            if hexs(b) != h["bytes_to_patch"].strip():
                print(f"  {h['patch_name'].strip()}: CALL 0x{into[0].call_target:X} -> {label} 0x{lab[label]:X}")
            new[j] = hexs(b)

    for (s, name) in HOOKS:
        if not any(p["script_name"] == s and p["patch_name"].strip() == name for p in patches):
            print(f"  no patch {name!r} for {s}")

    if a.write:
        out, last = [], 0
        for i, (x, y) in enumerate(spans):
            if i in new:
                out.append(text[last:x])
                out.append(new[i])
                last = y
        out.append(text[last:])
        bom = a.target.read_bytes()[:3] == b"\xef\xbb\xbf"
        a.target.write_text("".join(out), encoding="utf-8-sig" if bom else "utf-8")
        json.loads(a.target.read_text(encoding="utf-8-sig"))
        print("written", a.target)


if __name__ == "__main__":
    main()
