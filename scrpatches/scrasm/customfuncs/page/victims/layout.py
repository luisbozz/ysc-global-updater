"""Victim layout per Legacy creator: the vehicle preset table (string 47BQT444)."""
import sys, re, json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, '/opt/ysc-global-updater/scrpatches')
from dom import load
from scrasm.yscfull import YscFull
P = 0x4000
build = sys.argv[1]
out = {}
for s in ['fm_lts_creator','fm_capture_creator','fm_deathmatch_creator','fm_survival_creator','fm_race_creator','public_mission_creator']:
    d = load(build, s)
    y = YscFull.parse(f'/opt/ysc-global-updater/scrpatches/disasm/{build}/{s}.ysc.full')
    [i] = [i for i in range(d['n']) if '47BQT444' in d['strs'][i]]
    a, b = d['start'][i], d['start'][i] + d['size'][i]
    code = y.code
    assert code[a] == 0x2D and code[a+1] == 4 and d['rets'][i] == 0
    # stub pattern: shortest prefix unique in the script
    n = 12
    while code.count(code[a:a+n]) > 1: n += 1
    pattern = ' '.join(f'{x:02X}' for x in code[a:a+n])
    stub = ' '.join(f'{x:02X}' for x in code[a:a+5]) + ' 2E 04 00'
    # regions per page, the stub's 8 bytes excluded
    regions = []
    p = a + 8
    while p < b:
        e = min(b, (p // P + 1) * P)
        regions.append((p, e)); p = e
    full = [r for r in regions if r[1] - r[0] == P]
    data = full[0][0]
    rest = [r for r in regions if r[0] != data]
    cs, ce = max(rest, key=lambda r: r[1] - r[0])
    strings = (ce - 0x200) & ~7
    out[s] = dict(func=i, start=a, end=b, pattern=pattern, stub=stub, data=data, code=cs, code_room=strings - cs, strings=strings)
    print(f"{s:24s} func_{i} {a:#08x}-{b:#08x} data {data:#08x} code {cs:#08x} room {strings-cs} strings {strings:#08x} pattern[{n}] {pattern}")
json.dump(out, open(f'{HERE}/layout_{build}.json', 'w'), indent=1)
