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
import datafile
import drawer
import respawn
import ruleticker
import saveresult
import teleport
import ugclist
import mover
import zonelabel

PAGE = 0x4000
DISASM = HERE.parents[2] / "disasm"
XENVIOUS = Path("/opt/xenvious/Xenvious/OfflineData")
EDITIONS = ("enhanced", "legacy")


def authored_build(edition: str, src_dir: Path = None) -> str:
    """The build folder in disasm/ the sources of an edition are written for (src/<edition>/BUILD).
    xupdate rewrites the sources for a new build and this file with them."""
    return ((src_dir or HERE / "src" / edition) / "BUILD").read_text().strip()

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
    ("fm_lts_creator", "teleport in test"): "tw_wrap",
    ("fm_capture_creator", "teleport in test"): "tw_wrap",
    ("fm_survival_creator", "teleport in test"): "tw_wrap",
    ("fm_deathmatch_creator", "teleport in test"): "tw_wrap",
    ("fm_lts_creator", "zone labels"): "zl_wrap",
    ("fm_capture_creator", "zone labels"): "zl_wrap",
    ("fm_deathmatch_creator", "zone labels"): "zl_wrap",
    ("fm_race_creator", "zone labels"): "zl_wrap",
    ("fm_lts_creator", "save result"): "sv_result",
    ("fm_capture_creator", "save result"): "sv_result",
    ("fm_deathmatch_creator", "save result"): "sv_result",
    ("fm_survival_creator", "save result"): "sv_result",
    ("fm_race_creator", "save result"): "sv_result",
    ("fm_lts_creator", "publish result"): "sv_publish",
    ("fm_capture_creator", "publish result"): "sv_publish",
    ("fm_deathmatch_creator", "publish result"): "sv_publish",
    ("fm_survival_creator", "publish result"): "sv_publish",
    ("fm_race_creator", "publish result"): "sv_publish",
    ("fm_lts_creator", "update result"): "sv_update",
    ("fm_capture_creator", "update result"): "sv_update",
    ("fm_deathmatch_creator", "update result"): "sv_update",
    ("fm_survival_creator", "update result"): "sv_update",
    ("fm_race_creator", "update result"): "sv_update",
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


def loop_hook_keep(code: bytes):
    """Legacy: the draw call the loop hook replaces, as (target, global, field) for dl_hook."""
    at = zonelabel.find(code, LOOP_HOOK)
    if len(at) != 1:
        raise ValueError(f"loop hook site found {len(at)} times")
    at = at[0]
    if code[at + 0x17] != 0x5D:
        raise ValueError("loop hook site has no CALL at +0x17")
    return (int.from_bytes(code[at + 0x18:at + 0x1B], "little"),
            int.from_bytes(code[at + 1:at + 4], "little"), code[at + 5])


def build(edition: str, build_name: str, patches: list, src_dir: Path = None, room=None):
    """Assembles every page entry of ``patches`` for ``build_name``.

    Returns ``({index: new bytes_to_patch}, messages, problems)``. ``room`` maps a Legacy
    script to (payload pc, string array pc) from [SCRIPTVICTIM]; the payload must fit
    between them. A problem stops nothing; the entry keeps its old bytes."""
    src_dir = src_dir or HERE / "src" / edition
    room = room or {}
    new, msgs, problems = {}, [], []
    for i, p in enumerate(patches):
        if not p.get("page"):
            continue
        s, base = p["script_name"], p["page_base"]
        try:
            src = (Path(src_dir) / f"{s}.ysa").read_text()
            full = YscFull.parse(DISASM / build_name / f"{s}.ysc.full")
            nat = NativeResolver.from_full(full).index_of_name()
            keep = None
            if edition == "legacy" and s not in OWN_HOOK:
                keep = loop_hook_keep(full.code)
            probe = s in datafile.SCRIPTS
            asm = src + "\n" + drawer.gen(None if s in OWN_HOOK else base, keep,
                                          ("df_main", "tp_main", "mv_main", "ul_main") if probe else ("tp_main", "mv_main", "ul_main"))
            if probe:
                asm += "\n" + datafile.gen()
            asm += "\n" + teleport.gen()
            asm += "\n" + mover.gen()
            asm += "\n" + ugclist.gen()
            if s in saveresult.SCRIPTS:
                asm += "\n" + saveresult.gen(nat)
            test = teleport.test_site(full.code) if s in teleport.TEST_SCRIPTS else None
            if test:
                # The resurrect native has to be in the script's native table.
                rs = respawn.NATIVE in nat and s in respawn.CONTROLLERS
                rt = ruleticker.available(nat)
                asm += "\n" + teleport.gen_test(test, rs, rt)
                if rs:
                    asm += "\n" + respawn.gen(s)
                if rt:
                    asm += "\n" + ruleticker.gen()
            try:
                zone = zonelabel.site(full)
            except ValueError as e:
                zone = None
                problems.append(f"{s}: zone labels: {e}")
            if zone:
                asm += "\n" + zonelabel.gen(zone)
            code = assemble_text(asm, base=base, natives=nat)
        except Exception as e:  # noqa: BLE001 - reported, the entry keeps its old bytes
            problems.append(f"{s}: {type(e).__name__}: {e}")
            continue
        lab = labels(asm, base)
        if len(code) > PAGE - base % PAGE:
            problems.append(f"{s}: payload {len(code)} bytes does not fit the page")
            continue
        if s in room and not (base == room[s][0] and base + len(code) <= room[s][1]):
            problems.append(f"{s}: payload 0x{base:X}+{len(code)} does not fit before 0x{room[s][1]:X}")
            continue
        # Every CALL into the page must land on an ENTER of the page.
        ins = disassemble(code, base=base)
        starts = {x.offset for x in ins if x.name == "ENTER"}
        bad = [x for x in ins if x.name == "CALL" and base <= x.call_target < base + PAGE - base % PAGE
               and x.call_target not in starts]
        if bad:
            problems.append(f"{s}: CALL 0x{bad[0].call_target:X} lands on no ENTER")
            continue
        old = p["bytes_to_patch"].strip()
        new[i] = hexs(code)
        msgs.append(f"{s}: {len(old.split())} -> {len(code)} bytes" + ("" if new[i] != old else ", unchanged"))

        for j, h in enumerate(patches):
            label = HOOKS.get((h["script_name"], h["patch_name"].strip()))
            if h["script_name"] != s or label is None:
                continue
            if label not in lab:
                problems.append(f"{s}: {h['patch_name'].strip()}: label {label} not in the payload")
                continue
            b = bytearray.fromhex(h["bytes_to_patch"].replace(" ", ""))
            into = [x for x in disassemble(bytes(b))
                    if x.name == "CALL" and base <= x.call_target < base + PAGE - base % PAGE]
            if len(into) != 1:
                # The hook still calls into the old build's page position: take the one CALL.
                into = [x for x in disassemble(bytes(b)) if x.name == "CALL"]
            if len(into) != 1:
                problems.append(f"{s}: {h['patch_name'].strip()}: {len(into)} CALLs in the hook")
                continue
            at = into[0].offset + 1
            b[at:at + 3] = lab[label].to_bytes(3, "little")
            if hexs(b) != h["bytes_to_patch"].strip():
                msgs.append(f"  {h['patch_name'].strip()}: CALL 0x{into[0].call_target:X} -> {label} 0x{lab[label]:X}")
            new[j] = hexs(b)

    for (s, name) in HOOKS:
        if any(p["script_name"] == s and p.get("page") for p in patches) and \
                not any(p["script_name"] == s and p["patch_name"].strip() == name for p in patches):
            msgs.append(f"  no patch {name!r} for {s}")
    return new, msgs, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=EDITIONS, required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--target", type=Path)
    ap.add_argument("--build")
    a = ap.parse_args()
    a.target = a.target or XENVIOUS / a.edition / "scrpatches.json"
    a.build = a.build or authored_build(a.edition)

    text = a.target.read_text(encoding="utf-8-sig")
    patches = json.loads(text)
    spans = [m.span(1) for m in re.finditer(r'"bytes_to_patch": "([^"]*)"', text)]
    assert len(spans) == len(patches), (len(spans), len(patches))

    room = victims(a.edition) if a.edition == "legacy" else {}
    new, msgs, problems = build(a.edition, a.build, patches, room=room)
    print("\n".join(msgs))
    for x in problems:
        print("!!", x)

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
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
