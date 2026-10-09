import sys
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dom import load, dominators
build = sys.argv[1]; needles = sys.argv[2:]
for s in ['fm_lts_creator','fm_capture_creator','fm_deathmatch_creator','fm_survival_creator','fm_race_creator','public_mission_creator']:
    d = load(build, s); idom, preds = dominators(d)
    for nd in needles:
        hits = [i for i in range(d['n']) if nd in d['strs'][i]]
        for i in hits:
            a, b = d['start'][i], d['start'][i] + d['size'][i]
            pages = []
            p = a
            while p < b:
                e = min(b, ((p >> 14) + 1) << 14)
                pages.append(f"{p>>14}:{e-p}")
                p = e
            print(f"{s:24s} {nd:10s} func_{i:<6} {a:#08x}-{b:#08x} {b-a:6d} B params {d['params'][i]} rets {d['rets'][i]} callers {sorted(set(preds.get(i,[])))} pages {' '.join(pages)}")
