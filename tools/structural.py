#!/usr/bin/env python3
"""Struktureller Offset-Resolver fuer code-accessed Familien.

Viele Offset-Familien (veh, pa, kill, zones, tp, goto, player, ...) werden NICHT
per DATADICT serialisiert, sondern nur im Code ueber Getter/Setter angefasst.
Der semantische Pfad greift dort nicht, und das Kontext-Matching (infer) verliert
verschachtelte Array-Ebenen.

Diese Aufloesung nutzt aus, dass solche Werte eine feste Form haben:

    Global_G.f_ROOT[i /*S1*/]([j /*S2*/])?( .f_SUB[k /*S3*/] )?( .f_LEAF )*

Bei einem Update verschiebt sich das ROOT-Feld (Struct-Layout waechst), der Stride
(Element-Groesse) aendert sich, verschachtelte Sub-Arrays verschieben sich, aber
das Blattfeld bleibt meist erhalten. All das ist aus den Scripts ableitbar:

- ROOT-Verschiebung: die Array-Felder eines Globals werden per Kind-Feld-Signatur
  (welche Felder darunter zugegriffen werden) old->new gematcht, gleiche Signatur
  ordinal. Das ist stride-unabhaengig und damit robust, auch wenn der Stride sich
  aendert.
- Stride-Refresh: der neue Stride kommt direkt aus dem gematchten neuen Feld.
- Sub-Array-Verschiebung: analog per (innerer-Stride + Blatt-Signatur)-Gruppe
  ordinal.

Reines Read-only-Werkzeug; liefert einen migrierten Wert oder ``None``.
"""
from __future__ import annotations

import functools
import pathlib
import re
import subprocess
from collections import defaultdict

# Global_G.f_R[idx /*S*/] optional 2. index, optional .f_child
_ROOT_ACCESS = re.compile(
    r"\.f_(\d+)\[[A-Za-z0-9_]+ /\*(\d+)\*/\]"
    r"(?:\[[A-Za-z0-9_]+ /\*(\d+)\*/\])?"
    r"(?:\.f_(\d+))?"
)
_SUB_ACCESS_TMPL = (
    r"\.f_{root}\[[A-Za-z0-9_]+ /\*\d+\*/\]"
    r"(?:\[[A-Za-z0-9_]+ /\*\d+\*/\])?"
    r"\.f_(\d+)\[[A-Za-z0-9_]+ /\*(\d+)\*/\]"
    r"(?:\.f_(\d+))?"
)


@functools.lru_cache(maxsize=4096)
def _files_with(directory: str, needle: str) -> tuple:
    """Dateien, die needle enthalten (rg-vorgefiltert; Fallback: alle .c).

    Speicher-/Zeit-sicher: statt den kompletten Korpus (~6 GB) zu laden, liefert
    ripgrep die wenigen Dateien, die den Glob ueberhaupt erwaehnen (~Dutzende).
    Nur diese werden – einzeln – gelesen.
    """
    try:
        out = subprocess.run(
            ["rg", "-l", "--fixed-strings", needle, directory],
            capture_output=True, text=True, timeout=180,
        )
        if out.returncode in (0, 1):  # 0=Treffer, 1=keine Treffer
            return tuple(p for p in out.stdout.splitlines() if p.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return tuple(str(p) for p in sorted(pathlib.Path(directory).glob("*.c")))


def _iter_texts(directory: str, needle: str):
    """Streamt (einzeln, ohne Sammel-Cache) die Texte der needle-relevanten Dateien."""
    for path in _files_with(directory, needle):
        try:
            yield pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass


def collect_roots(directory: str, glob: str) -> dict:
    """{root_field: {"stride": int, "stride2": int|None, "children": set}} fuer glob."""
    pat = re.compile(re.escape(glob) + _ROOT_ACCESS.pattern)
    res: dict[int, dict] = {}
    for txt in _iter_texts(directory, glob):
        for m in pat.finditer(txt):
            R = int(m.group(1))
            e = res.setdefault(R, {"stride": int(m.group(2)), "stride2": None, "children": set()})
            if m.group(3) is not None:
                e["stride2"] = int(m.group(3))
            if m.group(4) is not None:
                e["children"].add(int(m.group(4)))
    return res


def collect_subarrays(directory: str, glob: str, root: int) -> dict:
    """{sub_field: {"stride": int, "leaves": set}} fuer glob.f_root[..].f_sub[..]."""
    pat = re.compile(re.escape(glob) + _SUB_ACCESS_TMPL.format(root=root))
    res: dict[int, dict] = {}
    for txt in _iter_texts(directory, glob):
        for m in pat.finditer(txt):
            sub = int(m.group(1))
            e = res.setdefault(sub, {"stride": int(m.group(2)), "leaves": set()})
            if m.group(3) is not None:
                e["leaves"].add(int(m.group(3)))
    return res


def _match_by_signature(old: dict, new: dict, sig_key: str) -> dict:
    """Match old-Felder -> new-Feld per exakter Signatur-Gruppe + ordinal,
    Rest per bester Jaccard-Aehnlichkeit (>=0.6), dann Identitaet fuer stabile
    Felder, dann delta-Overlap (verschobene Struct-Wurzel wie veh f_67545->f_68415)."""

    def jac(a: set, b: set) -> float:
        return len(a & b) / max(len(a | b), 1)

    og: dict = defaultdict(list)
    ng: dict = defaultdict(list)
    for R, e in sorted(old.items()):
        og[frozenset(e[sig_key])].append(R)
    for R, e in sorted(new.items()):
        ng[frozenset(e[sig_key])].append(R)

    mapping: dict[int, int] = {}
    used: set = set()
    for g, ol in og.items():
        if not g:
            continue
        nl = ng.get(g, [])
        for k, R in enumerate(ol):
            if k < len(nl):
                mapping[R] = nl[k]
                used.add(nl[k])
    for R, e in old.items():
        if R in mapping or not e[sig_key]:
            continue
        best, bs = None, 0.6
        for nR, ne in new.items():
            if nR in used:
                continue
            j = jac(e[sig_key], ne[sig_key])
            if j > bs:
                bs, best = j, nR
        if best is not None:
            mapping[R] = best
            used.add(best)
    # Identitaets-Fallback: ein Feld, das in new mit gleicher Nummer existiert und
    # noch nicht als Ziel vergeben ist, gilt als stabil (Struct-Wurzel unveraendert,
    # nur Kinder/Stride verschoben, z. B. pa f_3605).
    for R in old:
        if R not in mapping and R in new and R not in used:
            mapping[R] = R
            used.add(R)
    # delta-Overlap: verschobene Struct-Wurzel, deren Kinder ALLE um ein delta
    # wandern (z. B. veh f_67545->f_68415, Kinder +1..+5). Finde new-Wurzel, deren
    # Kinder maximal mit (old-Kindern + delta) ueberlappen.
    shifted: set = set()
    for R, e in old.items():
        if R in mapping or len(e[sig_key]) < 4:
            continue
        cold = e[sig_key]
        best, bs = None, 0.0
        for nR, ne in new.items():
            if nR in used or len(ne[sig_key]) < 4:
                continue
            cn = ne[sig_key]
            deltas: dict = defaultdict(int)
            for a in list(cold)[:40]:
                for b in cn:
                    if abs(b - a) <= 12:
                        deltas[b - a] += 1
            if not deltas:
                continue
            d = max(deltas, key=deltas.get)
            score = len({a + d for a in cold} & cn) / len(cold)
            if score > bs:
                bs, best = score, nR
        if best is not None and bs >= 0.6:
            mapping[R] = best
            used.add(best)
            if best != R:
                shifted.add(R)
    return mapping, shifted


def collect_top_fields(directory: str, glob: str) -> set:
    """Menge der Top-Level-Feldnummern N aus ``glob.f_N`` (Array-Wurzeln UND
    Skalare). Dient der lauf-basierten Ausrichtung der Skalar-Stufenfunktion."""
    pat = re.compile(re.escape(glob) + r"\.f_(\d+)")
    fields: set = set()
    for txt in _iter_texts(directory, glob):
        for m in pat.finditer(txt):
            fields.add(int(m.group(1)))
    return fields


def _align_runs(old: set, new: set) -> dict:
    """Monotone lauf-basierte Ausrichtung old->new fuer eine Stufenfunktion.

    old-Felder werden in zusammenhaengende Laeufe (Luecke <= 64) segmentiert; pro
    Lauf wird die haeufigste Verschiebung (nearest new-Feld nach vorne) bestimmt
    und angewandt. Robust gegen grosse Stufen (z. B. +76 dann +844)."""
    import bisect
    from collections import Counter

    if not old or not new:
        return {}
    old_s = sorted(old)
    new_s = sorted(new)
    newset = set(new)
    # Laeufe bilden
    runs: list = []
    cur = [old_s[0]]
    for o in old_s[1:]:
        if o - cur[-1] <= 64:
            cur.append(o)
        else:
            runs.append(cur)
            cur = [o]
    runs.append(cur)

    mp: dict = {}
    for run in runs:
        # Kandidaten-Verschiebungen aus (new - old)-Paaren im Fenster sammeln, dann
        # die Verschiebung waehlen, die die MEISTEN Lauf-Felder auf new abbildet
        # (Delta-Overlap). Robust gegen koinzidente Zwischenfelder (nearest-forward
        # wuerde +8 statt +76 waehlen, wenn zufaellig ein Feld bei +8 liegt).
        cand: Counter = Counter()
        for o in run:
            lo = bisect.bisect_left(new_s, o)
            hi = bisect.bisect_left(new_s, o + 2000)
            for n in new_s[lo:hi]:
                cand[n - o] += 1
        if not cand:
            continue
        best_shift, best_score = None, 0
        for s, _ in cand.most_common(60):
            score = sum(1 for o in run if o + s in newset)
            if score > best_score:
                best_score, best_shift = score, s
        if best_shift is None or best_score < max(1, len(run) // 2):
            continue  # kein dominanter Shift -> Lauf unsicher, ueberspringen
        for o in run:
            t = o + best_shift
            if t in newset:
                mp[o] = t
    return mp


class StructuralResolver:
    """Cached Resolver fuer ein (old_dir, new_dir)-Paar."""

    def __init__(self, old_dir: str, new_dir: str):
        self.old_dir = str(old_dir)
        self.new_dir = str(new_dir)
        self._root_shift: dict[str, dict] = {}
        self._root_shifted: dict[str, set] = {}
        self._root_new: dict[str, dict] = {}
        self._old_roots_cache: dict[str, dict] = {}
        self._sub_shift: dict[tuple, dict] = {}
        self._leaf_maps: dict[tuple, dict] = {}
        self._anchors: dict[str, list] = {}
        self._scalar_maps: dict[str, dict] = {}

    def _roots(self, glob: str):
        if glob not in self._root_shift:
            old = collect_roots(self.old_dir, glob)
            new = collect_roots(self.new_dir, glob)
            mapping, shifted = _match_by_signature(old, new, "children")
            self._root_shift[glob] = mapping
            self._root_shifted[glob] = shifted
            self._root_new[glob] = new
            self._old_roots_cache[glob] = old
        return self._root_shift[glob], self._root_new[glob]

    def _scalar_field(self, glob: str, field: int):
        """Neue Feldnummer fuer ein skalares Top-Level-Feld via lauf-basierter
        Ausrichtung aller ``glob.f_N``-Felder (old->new). Rekonstruiert die
        Stufenfunktion (z. B. Global_4718592-Skalare +76/+844)."""
        mp = self._scalar_maps.get(glob)
        if mp is None:
            old = collect_top_fields(self.old_dir, glob)
            new = collect_top_fields(self.new_dir, glob)
            mp = self._scalar_maps[glob] = _align_runs(old, new)
        return mp.get(field)

    def _interpolate(self, glob: str, root: int):
        """Feld-Verschiebung fuer ein skalares Feld aus den gematchten Array-Wurzeln
        interpolieren. Nur wenn die beiden benachbarten Anker (unter/ueber dem Feld)
        die GLEICHE Verschiebung haben (konstante Region) -> sicher; sonst None."""
        anchors = self._anchors.get(glob)
        if anchors is None:
            rs = self._root_shift[glob]
            anchors = sorted((o, n - o) for o, n in rs.items())
            self._anchors[glob] = anchors
        if not anchors:
            return None
        below = [a for a in anchors if a[0] <= root]
        above = [a for a in anchors if a[0] > root]
        # Nur INTERPOLIEREN (eingerahmt), nicht extrapolieren: sowohl ein Anker
        # unterhalb als auch oberhalb noetig, und beide muessen dieselbe
        # Verschiebung haben (konstante Region ohne Struct-Insert dazwischen).
        # Extrapolation weit ueber den letzten Anker hinaus (z. B. jobid f_131903
        # bei Ankern um f_3605) ist unzuverlaessig -> None (wird geflaggt).
        if not below or not above:
            return None
        shift = below[-1][1]
        if above[0][1] != shift:
            return None
        return root + shift

    def _leaf_map(self, glob: str, old_root: int, new_root: int) -> dict:
        """Greedy-monotone Ausrichtung der direkten Kinder old_root -> new_root.
        Liefert bei unveraendertem internem Layout die Identitaet (Blatt erhalten,
        z. B. kill), bei internen Einfuegungen die Feld-Stufenfunktion (z. B. veh)."""
        key = (glob, old_root, new_root)
        if key not in self._leaf_maps:
            old = self._old_roots_cache[glob].get(old_root, {}).get("children", set())
            new = self._root_new[glob].get(new_root, {}).get("children", set())
            newset = set(new)
            mp: dict[int, int] = {}
            shift = 0
            for o in sorted(old):
                t = o + shift
                gap = 0
                while t not in newset and gap < 25:
                    t += 1
                    gap += 1
                if t in newset:
                    mp[o] = t
                    shift = t - o
            self._leaf_maps[key] = mp
        return self._leaf_maps[key]

    def _subs(self, glob: str, new_root: int):
        key = (glob, new_root)
        if key not in self._sub_shift:
            # Sub-Array-Verschiebung wird auf Basis der ALTEN root-Sub-Arrays gegen
            # die NEUEN root-Sub-Arrays bestimmt (per Blatt-Signatur + ordinal).
            self._sub_shift[key] = None  # lazy pro alt-root gefuellt in migrate
        return self._sub_shift[key]

    def migrate(self, value: str, conservative: bool = False, scalar_only: bool = False) -> str | None:
        # scalar_only: nur bare Skalare (Global_G.f_N ohne Array) aufloesen; fuer
        # Familien ausserhalb der struct_families, wo Array-/Blatt-Rateversuche zu
        # unsicher sind, aber die Skalar-Stufenfunktion (90 % praezise) sicher ist.
        if scalar_only and "[" in value:
            return None
        out = self._migrate_inner(value)
        if out is None or not conservative:
            return out
        # Konservativ: nur akzeptieren, wenn sich AUSSCHLIESSLICH Strides aendern
        # (Feld-/Root-Nummern identisch). Das ist der sichere Stride-Refresh-Fall
        # (z. B. props_model f_1[i/*163*/].f_7 -> f_1[i/*165*/].f_7). Geratene
        # Feld-/Root-Verschiebungen werden verworfen (lieber flaggen als falsch).
        strip = lambda s: re.sub(r"/\*\d+\*/", "", s)
        return out if strip(value) == strip(out) else None

    def _migrate_inner(self, value: str) -> str | None:
        m = re.match(r"(Global_(\d+))\.f_(\d+)(.*)", value)
        if not m:
            return None
        glob, root, rest = m.group(1), int(m.group(3)), m.group(4)
        root_shift, root_new = self._roots(glob)
        if root not in root_shift:
            # Skalares Feld (kein Array). Zuerst lauf-basierte Ausrichtung aller
            # Top-Level-Felder (rekonstruiert die Stufenfunktion), sonst
            # Interpolation aus gematchten Array-Wurzeln.
            if not rest or rest[0] != "[":
                nf = self._scalar_field(glob, root)
                if nf is not None and nf != root:
                    return f"{glob}.f_{nf}{rest}"
                inter = self._interpolate(glob, root)
                if inter is not None:
                    return f"{glob}.f_{inter}{rest}"
            return None
        new_root = root_shift[root]
        new_entry = root_new.get(new_root, {})
        new_s1 = new_entry.get("stride")
        new_s2 = new_entry.get("stride2")
        # Blatt-Stufenfunktion NUR bei delta-verschobener Wurzel anwenden (internes
        # Layout wanderte, z. B. veh/obj). Bei Signatur-/Identitaets-Match bleibt das
        # Blatt erhalten (z. B. zones/kill), sonst wuerde die Greedy-Ausrichtung
        # korrekte Blaetter faelschlich verschieben.
        leaf_map = self._leaf_map(glob, root, new_root) if root in self._root_shifted.get(glob, set()) else {}

        # rest zerlegen: [idx /*S1*/]([idx /*S2*/])? ( .f_SUB[idx /*S3*/] )? ( .f_LEAF )*
        rm = re.match(
            r"\[([A-Za-z0-9_]+) /\*(\d+)\*/\]"
            r"(?:\[([A-Za-z0-9_]+) /\*(\d+)\*/\])?"
            r"(.*)",
            rest,
        )
        if rm:
            idx1, s1, idx2, s2, tail = rm.groups()
            out = f"{glob}.f_{new_root}[{idx1} /*{new_s1 or s1}*/]"
            if idx2 is not None:
                out += f"[{idx2} /*{new_s2 or s2}*/]"
        else:
            # skalarer Wert Global.f_root(.f_child)* (kein Array)
            out = f"{glob}.f_{new_root}"
            tail = rest

        # tail: optional .f_SUB[..]-Sub-Array (pa), sonst direkte Blatt-Felder.
        sub_m = re.match(r"\.f_(\d+)\[([A-Za-z0-9_]+) /\*(\d+)\*/\](.*)", tail)
        if sub_m:
            sub, sidx, sstride, sub_tail = sub_m.groups()
            new_sub, new_sstride = self._map_sub(glob, root, new_root, int(sub), int(sstride))
            out += f".f_{new_sub}[{sidx} /*{new_sstride or sstride}*/]{sub_tail}"
        else:
            cm = re.match(r"\.f_(\d+)(.*)", tail)
            if cm and leaf_map:
                child = int(cm.group(1))
                out += f".f_{leaf_map.get(child, child)}{cm.group(2)}"
            else:
                out += tail
        return out

    def _map_sub(self, glob: str, old_root: int, new_root: int, sub: int, sstride: int):
        key = (glob, old_root, new_root)
        cached = self._sub_shift.get(key)
        if cached is None:
            old_subs = collect_subarrays(self.old_dir, glob, old_root)
            new_subs = collect_subarrays(self.new_dir, glob, new_root)
            shift, _ = _match_by_signature(old_subs, new_subs, "leaves")
            cached = (shift, new_subs)
            self._sub_shift[key] = cached
        shift, new_subs = cached
        new_sub = shift.get(sub, sub)
        new_sstride = new_subs.get(new_sub, {}).get("stride", sstride)
        return new_sub, new_sstride


def migrate_value(value: str, old_dir: str, new_dir: str,
                  conservative: bool = False, scalar_only: bool = False,
                  _resolvers: dict = {}) -> str | None:
    key = (str(old_dir), str(new_dir))
    r = _resolvers.get(key)
    if r is None:
        r = _resolvers[key] = StructuralResolver(old_dir, new_dir)
    return r.migrate(value, conservative=conservative, scalar_only=scalar_only)
