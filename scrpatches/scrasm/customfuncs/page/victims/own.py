import sys, re, pickle, collections
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dom import load, dominators
build, s = sys.argv[1], sys.argv[2]
minsize = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
d = load(build, s)
idom, preds = dominators(d)
src = open(f'/opt/ysc-global-updater/scripts/{build}/{s}.c', encoding='utf-8', errors='replace').read()
heads = [(m.start(), int(m.group(1))) for m in re.finditer(r'^\S[^\n(]*\bfunc_(\d+)\(', src, re.M)]
body = {}
for k, (pos, idx) in enumerate(heads):
    end = heads[k+1][0] if k+1 < len(heads) else len(src)
    body[idx] = src[pos:end]
rows = sorted(range(d['n']), key=lambda i: -d['size'][i])
for i in rows:
    if d['size'][i] < minsize: break
    b = body.get(i, '')
    nat = collections.Counter(re.findall(r'([A-Z_]+::[A-Z0-9_]+)', b))
    ncallers = len(set(preds.get(i, [])))
    reach = i in idom
    print(f"func_{i:<6} own {d['size'][i]:6d} @{d['start'][i]:#08x} page {d['start'][i]>>14:3d} params {d['params'][i]:2d} rets {d['rets'][i]:2d} callers {ncallers:3d} {'' if reach else 'UNREACHED'}")
    print("    natives:", ', '.join(f'{k.split("::")[1]}x{v}' for k, v in nat.most_common(6)))
    print("    strings:", ', '.join(d['strs'][i][:8]))
