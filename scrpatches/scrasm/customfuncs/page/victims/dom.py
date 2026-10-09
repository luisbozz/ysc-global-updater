import sys, json, pickle, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, '/opt/ysc-global-updater/scrpatches')
from scrasm.yscfull import YscFull
from scrasm.disasm import disassemble
from scrasm.functions import function_table

def load(build, s):
    cache = f'{HERE}/.cache/{build}_{s}.pkl'
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    y = YscFull.parse(f'/opt/ysc-global-updater/scrpatches/disasm/{build}/{s}.ysc.full')
    ins = disassemble(y.code); funcs = function_table(ins)
    starts = {f.start_offset: f.index for f in funcs}
    calls = {f.index: [] for f in funcs}
    strs = {f.index: set() for f in funcs}
    natives = {f.index: set() for f in funcs}
    for f in funcs:
        prev = None
        for k in range(f.start_index, f.end_index + 1):
            i = ins[k]
            if i.name == 'CALL':
                t = int.from_bytes(i.operands[:3], 'little')
                if t in starts: calls[f.index].append(starts[t])
            elif i.name.startswith('PUSH_CONST') and len(i.operands) in (2,3,4):
                v = int.from_bytes(i.operands, 'little')
                if v in starts: calls[f.index].append(starts[v])
            if i.name == 'STRING' and prev is not None and prev.name.startswith('PUSH_CONST'):
                if prev.operands:
                    v = int.from_bytes(prev.operands, 'little')
                else:
                    v = {'PUSH_CONST_0':0}.get(prev.name, None)
                if v is not None:
                    t = y.string_at(v)
                    if t: strs[f.index].add(t)
            prev = i
    data = dict(n=len(funcs), size=[f.end_offset - f.start_offset for f in funcs],
                start=[f.start_offset for f in funcs], params=[f.params for f in funcs],
                rets=[f.returns for f in funcs], calls=calls, strs={k: sorted(v) for k, v in strs.items()})
    pickle.dump(data, open(cache, 'wb'))
    return data

def dominators(d):
    n = d['n']; succ = d['calls']
    # reverse postorder from 0
    order = []; seen = [False]*n; stack = [(0, iter(succ[0]))]; seen[0] = True
    while stack:
        v, it = stack[-1]
        for w in it:
            if not seen[w]:
                seen[w] = True; stack.append((w, iter(succ[w]))); break
        else:
            stack.pop(); order.append(v)
    rpo = order[::-1]; num = {v: i for i, v in enumerate(rpo)}
    preds = {v: [] for v in rpo}
    for v in rpo:
        for w in succ[v]:
            if w in num: preds[w].append(v)
    idom = {0: 0}
    def inter(a, b):
        while a != b:
            while num[a] > num[b]: a = idom[a]
            while num[b] > num[a]: b = idom[b]
        return a
    changed = True
    while changed:
        changed = False
        for v in rpo[1:]:
            ps = [p for p in preds[v] if p in idom]
            if not ps: continue
            new = ps[0]
            for p in ps[1:]: new = inter(p, new)
            if idom.get(v) != new: idom[v] = new; changed = True
    return idom, preds

if __name__ == '__main__':
    build, s = sys.argv[1], sys.argv[2]
    top = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    d = load(build, s)
    idom, preds = dominators(d)
    kids = {}
    for v, p in idom.items():
        if v != p: kids.setdefault(p, []).append(v)
    sub = {}
    def tot(v):
        st = [v]; acc = 0; members = []
        while st:
            x = st.pop(); acc += d['size'][x]; members.append(x); st.extend(kids.get(x, []))
        return acc, members
    rows = []
    for v in idom:
        if v == 0: continue
        acc, mem = tot(v)
        rows.append((acc, v, mem))
    rows.sort(reverse=True)
    shown = set()
    for acc, v, mem in rows:
        if v in shown: continue
        if len(shown) >= top: break
        # only show roots whose idom is not already shown (to avoid nesting spam) - show anyway but mark
        strs = set()
        for m in mem: strs.update(d['strs'][m])
        callers = sorted(set(preds[v]))
        print(f"func_{v:<6} sub {acc:7d} B  own {d['size'][v]:6d}  funcs {len(mem):4d}  params {d['params'][v]} rets {d['rets'][v]}  callers {callers[:5]}  idom func_{idom[v]}")
        print("     strings:", ', '.join(sorted(strs)[:25]))
        shown.add(v)
