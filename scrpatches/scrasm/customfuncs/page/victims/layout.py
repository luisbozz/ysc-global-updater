"""Victim layout per Legacy creator: the vehicle preset table (string 47BQT444).

The payload replaces that function (see page/README.md). Per build and script this finds
the function, the 16 KB code page it covers (data page), the longest rest for the code,
and the string page array at the end of that rest. Xenvious reads the result from
[SCRIPTVICTIM] in offsets.ini; the stub patch "give up the vehicle preset table" uses
STUB_PATTERN.

    python3 victims/layout.py <build>      # prints and writes layout_<build>.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "..")))
from dom import load  # noqa: E402
from scrasm.yscfull import YscFull  # noqa: E402

P = 0x4000
CREATORS = ["fm_lts_creator", "fm_capture_creator", "fm_deathmatch_creator",
            "fm_survival_creator", "fm_race_creator", "public_mission_creator"]
# ENTER 4,7 then the table's first instructions. Byte 9 is a local index that differs between
# builds (0x67 on 1.73-3889), so it is a wildcard.
STUB_PATTERN = "2D 04 07 00 00 38 02 65 ? 5F A9 AC"
DISASM = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "disasm"))


def _count(code, pattern):
    import re
    rx = re.compile(b"".join(b"." if t == "?" else re.escape(bytes([int(t, 16)])) for t in pattern.split()), re.S)
    return [m.start() for m in rx.finditer(code)]


def layout(build: str, script: str) -> dict:
    d = load(build, script)
    y = YscFull.parse(os.path.join(DISASM, build, f"{script}.ysc.full"))
    found = [i for i in range(d["n"]) if "47BQT444" in d["strs"][i]]
    if len(found) != 1:
        raise ValueError(f"{script}: preset table found {len(found)} times")
    i = found[0]
    a, b = d["start"][i], d["start"][i] + d["size"][i]
    code = y.code
    if not (code[a] == 0x2D and code[a + 1] == 4 and d["rets"][i] == 0):
        raise ValueError(f"{script}: preset table does not start with ENTER 4 / returns something")
    pattern = STUB_PATTERN
    if _count(code, pattern) != [a]:
        # The fixed pattern no longer fits: the shortest prefix unique in the script.
        n = 12
        while code.count(code[a:a + n]) > 1:
            n += 1
        pattern = " ".join(f"{x:02X}" for x in code[a:a + n])
    stub = " ".join(f"{x:02X}" for x in code[a:a + 5]) + " 2E 04 00"
    # regions per page, the stub's 8 bytes excluded
    regions, p = [], a + 8
    while p < b:
        e = min(b, (p // P + 1) * P)
        regions.append((p, e))
        p = e
    full = [r for r in regions if r[1] - r[0] == P]
    if not full:
        raise ValueError(f"{script}: preset table covers no whole code page")
    data = full[0][0]
    rest = [r for r in regions if r[0] != data]
    cs, ce = max(rest, key=lambda r: r[1] - r[0])
    strings = (ce - 0x200) & ~7
    return dict(func=i, start=a, end=b, pattern=pattern, stub=stub, data=data, code=cs,
                code_room=strings - cs, strings=strings)


def ini_value(v: dict) -> str:
    """The [SCRIPTVICTIM] value: function start, payload, data page, string page array."""
    return ", ".join(f"{x:06X}" for x in (v["start"], v["code"], v["data"], v["strings"]))


if __name__ == "__main__":
    build = sys.argv[1]
    out = {}
    for s in CREATORS:
        v = out[s] = layout(build, s)
        print(f"{s:24s} func_{v['func']} {v['start']:#08x}-{v['end']:#08x} data {v['data']:#08x} "
              f"code {v['code']:#08x} room {v['code_room']} strings {v['strings']:#08x} pattern {v['pattern']}")
    json.dump(out, open(f"{HERE}/layout_{build}.json", "w"), indent=1)
