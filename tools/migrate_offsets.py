#!/usr/bin/env python3
"""Migriert offsets.ini-Werte per semantischem Pfad zwischen zwei Script-Versionen.

Prinzip: Der semantische Pfad (z. B. ``meta.trntype``) ist versions-stabil, der
rohe ``Global_``-Offset wandert. Wir bauen aus den ALTEN Scripts eine Umkehr-
Karte ``Global -> Pfad`` und aus den NEUEN Scripts ``Pfad -> Global``. Fuer jeden
Offset in der offsets.ini, dessen aktueller Wert in den alten Scripts als Pfad
wiedergefunden wird, liefern die neuen Scripts den migrierten Wert.

Sicher: liest nur, schreibt ein SEPARATES Ergebnis (Default reports/offsets.migrated.ini)
und fasst die Original-offsets.ini nie an. Werte, die nicht eindeutig per Pfad
migrierbar sind, bleiben unveraendert und werden im Report kategorisiert.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.extract_globals import canonicalize_global, extract_file  # noqa: E402

OFFSET_RE = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]+)"')

_LOOSE_TOKEN = re.compile(r"\.f_(\d+)|\[\s*(\d+|[A-Za-z_]\w*)\s*(?:/\*(\d+)\*/)?\s*\]")


def loose_key(global_expr: str):
    """Notations-tolerante Identitaet eines Global-Ausdrucks.

    Fasst aequivalente Schreibweisen DESSELBEN Speicherorts zusammen, damit der
    Lookup robust ist, wenn offsets.ini und Script denselben Offset unterschiedlich
    notieren:
    - ``.f_M`` und literal ``[N /*S*/]`` sind beide feste Sub-Offsets -> als
      kumulative Feld-Offsets aufaddiert (``[1 /*3*/]`` == ``.f_3``, ``[0 /*3*/]``
      == Basis).
    - variable ``[i /*S*/]`` ist eine echte Element-Iteration -> Trenner ``I(S)``.
    - trailing bare ``[i]`` (ohne Stride) ist eine Serialisierungs-Schleife und
      gehoert NICHT zum Offset -> ignoriert (``f_187854[i]`` == ``f_187854``).
    """
    m = re.match(r"(Global_\d+)(.*)$", global_expr)
    if not m:
        return None
    rest = m.group(2)
    tokens = [m.group(1)]
    pending = 0
    pos = 0
    for tk in _LOOSE_TOKEN.finditer(rest):
        if tk.start() != pos:
            return None  # unparsbarer Rest (z. B. kaputtes ".60") -> keine sichere Identitaet
        pos = tk.end()
        if tk.group(1) is not None:              # .f_M
            pending += int(tk.group(1))
            continue
        idx, stride = tk.group(2), tk.group(3)
        if idx.isdigit():                        # literal [N] / [N /*S*/]
            pending += int(idx) * (int(stride) if stride else 1)
        elif stride:                             # [i /*S*/] echte Element-Iteration
            tokens.append(("F", pending))
            pending = 0
            tokens.append(("I", int(stride)))
        # else: bare [i] -> Serialisierungs-Schleife, ignorieren
    if pos != len(rest):
        return None  # nicht vollstaendig parsbar -> nicht loose-aufloesen
    tokens.append(("F", pending))
    return tuple(tokens)


def to_offset_notation(global_expr: str) -> str:
    """Extractor-Rohform -> offsets.ini-Stil: trailing bare ``[i]`` entfernen und
    literal ``[N /*S*/]`` (Vektor-Komponente) zu ``.f_(N*S)`` machen (``.f_0``
    entfaellt). Bringt neu aufgeloeste Werte in die uebliche Offset-Schreibweise
    (``f_193531[i]`` -> ``f_193531``, ``[1 /*3*/]`` -> ``.f_3``)."""
    m = re.match(r"(Global_\d+)(.*)$", global_expr)
    if not m:
        return global_expr
    rest = re.sub(r"\[[A-Za-z_]\w*\]$", "", m.group(2))

    def repl(mm: re.Match) -> str:
        off = int(mm.group(1)) * int(mm.group(2))
        return "" if off == 0 else f".f_{off}"

    rest = re.sub(r"\[(\d+) /\*(\d+)\*/\]", repl, rest)
    return m.group(1) + rest


def match_notation(new_full: str, cval: str) -> str:
    """Die ``[idx]``-Schreibweise des neuen Werts an die offsets.ini-Form (cval)
    angleichen. old_rev kennt jeden Wert voll UND index-reduziert; der neue Wert
    traegt die volle Serialisierungs-Form (alle Element-/Vektor-Indizes). Per
    Token-Alignment werden genau die ``[idx]``-Tokens entfernt, die cval an der
    entsprechenden Stelle NICHT hat:
    ``f_5061[j /*9*/]`` (bmmxh, Index behalten) vs ``f_7181`` (dpos, trailing weg)
    vs ``f_9[j /*27*/].f_2`` -> ``f_9.f_2`` (actor, mittleren Index weg)."""
    tok = re.compile(r"\.f_\d+|\[[^\]]*\]")
    mn = re.match(r"(Global_\d+)(.*)$", new_full)
    mc = re.match(r"(Global_\d+)(.*)$", cval)
    if not mn or not mc:
        return new_full
    nt = tok.findall(mn.group(2))
    ct = tok.findall(mc.group(2))
    out, ci = [], 0

    def is_var(t: str) -> bool:  # nur VARIABLE Indizes [i /*S*/] sind droppbar;
        return t.startswith("[") and bool(re.match(r"\[[A-Za-z_]", t))  # literale [1 /*3*/] sind feste Sub-Offsets

    for t in nt:
        if ci < len(ct):
            if is_var(t) and not is_var(ct[ci]):
                continue  # variablen Index weglassen, den cval hier nicht hat
            out.append(t)
            ci += 1
        elif is_var(t):
            continue      # cval erschoepft -> restliche variable Indizes weg
        else:
            out.append(t)
    return mn.group(1) + "".join(out)


_VAL_ONLY_RE = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]+)"')
_NEXT_RE = re.compile(r"^(OFFSET_([A-Za-z0-9_]+)_NEXT)\s*=\s*(\d+)\s*$")
_ARR_RE = re.compile(r"^(Global_\d+\.f_\d+)\[[A-Za-z_]\w*(?: /\*(\d+)\*/)?\](.*)$")


def _root_field(g: str):
    m = re.match(r"Global_\d+\.f_(\d+)", g)
    return int(m.group(1)) if m else None


def _base(v: str):
    """Der Basis-Global (Global_N). Bleibt versions-stabil — ein Offset wandert
    nie in einen ANDEREN Basis-Global; ein Wechsel ist ein Cross-Global-Fehlmatch."""
    m = re.match(r"Global_\d+", v or "")
    return m.group(0) if m else None


def postprocess_families(migrated_text: str, ini_text: str, struct_families: tuple):
    """Nachkorrektur cross-family-kontaminierter Offsets ueber Familien-KONSENS.

    Generische Keys (loc/head/veh/no) koennen einen struct-family-Offset in die
    FALSCHE Struktur migrieren (z. B. actor_loc -> player-Struct f_197738 statt
    actor f_90320). Da die restliche Familie einen einheitlichen Root teilt, werden
    Ausreisser (gleicher OLD-Root wie die Familie, aber anderer NEW-Root) auf den
    Konsens-Root/-Stride zurueckgefuehrt (Blatt per Sibling-Interpolation), das
    ``_number`` per Familien-Root-Delta, und ``_NEXT`` auf den neuen Stride gesetzt.
    """
    old = {m.group(1): m.group(2) for ln in ini_text.splitlines() if (m := _VAL_ONLY_RE.match(ln))}
    new = {m.group(1): m.group(2) for ln in migrated_text.splitlines() if (m := _VAL_ONLY_RE.match(ln))}
    fixes: dict[str, str] = {}     # name -> korrigierter Wert
    next_fixes: dict[str, int] = {}  # OFFSET_..._NEXT -> neuer stride
    notes: list[tuple[str, str, str]] = []

    for fam in struct_families:
        members = [n for n in new if n.startswith("OFFSET_" + fam)]
        arr = []  # (name, old_root, old_stride, old_leaf, new_root, new_stride, new_leaf)
        for n in members:
            om, nm = _ARR_RE.match(old.get(n, "")), _ARR_RE.match(new.get(n, ""))
            if om and nm and om.group(2) and nm.group(2):
                arr.append((n, om.group(1), int(om.group(2)), om.group(3),
                            nm.group(1), int(nm.group(2)), nm.group(3)))
        if len(arr) < 3:
            continue
        cons_old_root = Counter(a[1] for a in arr).most_common(1)[0][0]
        cons_new_root = Counter(a[4] for a in arr).most_common(1)[0][0]
        cons_new_stride = Counter(a[5] for a in arr).most_common(1)[0][0]

        # Blatt-Remap aus den KONSENS-Mitgliedern (erste .f_N-Ebene).
        pairs = []
        for _n, orr, _os, oleaf, nrr, _ns, nleaf in arr:
            if orr == cons_old_root and nrr == cons_new_root:
                lo, ln_ = re.match(r"\.f_(\d+)", oleaf or ""), re.match(r"\.f_(\d+)", nleaf or "")
                if lo and ln_:
                    pairs.append((int(lo.group(1)), int(ln_.group(1))))
        pairs = sorted(set(pairs))

        def interp(leaf: str) -> str:
            lm = re.match(r"\.f_(\d+)(.*)$", leaf)
            if not lm or not pairs:
                return leaf
            L = int(lm.group(1))
            below = [p for p in pairs if p[0] <= L]
            ref = below[-1] if below else pairs[0]
            return f".f_{L + (ref[1] - ref[0])}{lm.group(2)}"

        # (a) Array-Ausreisser: gleicher OLD-Root, aber anderer NEW-Root -> Konsens.
        for n, orr, _os, oleaf, nrr, _ns, _nl in arr:
            if orr == cons_old_root and (nrr != cons_new_root):
                corrected = f"{cons_new_root}[i /*{cons_new_stride}*/]{interp(oleaf or '')}"
                fixes[n] = corrected
                notes.append((n, new[n], corrected))

        # (b) <fam>_number / <fam>_NEXT etc.: Skalar mit Familien-Root-Delta.
        of, nf = _root_field(cons_old_root), _root_field(cons_new_root)
        if of is not None and nf is not None:
            delta = nf - of
            for n in members:
                if not n.endswith("_number"):
                    continue
                ov, nv = old.get(n, ""), new.get(n, "")
                mo = re.match(r"^(Global_\d+)\.f_(\d+)$", ov)
                # nur korrigieren, wenn der neue Wert nicht schon in der Naehe liegt
                if mo and _root_field(nv) is not None and abs((_root_field(nv) or 0) - (int(mo.group(2)) + delta)) > 200:
                    corrected = f"{mo.group(1)}.f_{int(mo.group(2)) + delta}"
                    fixes[n] = corrected
                    notes.append((n, nv, corrected))

        # (c) OFFSET_<fam>_NEXT = neuer Stride.
        for n in new_next_names(migrated_text):
            base = n[len("OFFSET_"):-len("_NEXT")]
            if base == fam.rstrip("_"):
                next_fixes[n] = cons_new_stride

    # Text neu schreiben.
    out = []
    for line in migrated_text.splitlines():
        m = _VAL_ONLY_RE.match(line)
        if m and m.group(1) in fixes:
            out.append(re.sub(r'"[^"]+"', f'"{fixes[m.group(1)]}"', line, count=1))
            continue
        nm = _NEXT_RE.match(line)
        if nm and nm.group(1) in next_fixes:
            out.append(re.sub(r"=\s*\d+", f"= {next_fixes[nm.group(1)]}", line, count=1))
            continue
        out.append(line)
    return "\n".join(out) + "\n", notes


def new_next_names(text: str) -> list[str]:
    return [m.group(1) for ln in text.splitlines() if (m := _NEXT_RE.match(ln))]


# Stride-/Groessen-Offsets sind eine BLANKE Ganzzahl (Array-Element-Groesse), z. B.
# ``OFFSET_props_next = 163`` oder ``OFFSET_next_settings = 69``. Ihr Wert ist der
# ``/*N*/``-Stride eines Array-Offsets derselben INI; die Feld-Nummer ist egal.
_INT_OFFSET_RE = re.compile(r'^(OFFSET_([A-Za-z0-9_]+)\s*=\s*)("?)(\d+)("?)(.*)$')
_STRIDE_NAME_RE = re.compile(r"(?i)next|size|settings")


def _common_prefix(a: str, b: str) -> int:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return i


def postprocess_strides(migrated_text: str, ini_text: str):
    """Stride-/``_NEXT``-Offsets (blanke Ganzzahl = Array-Element-Groesse) auf den
    NEUEN Stride setzen.

    Ein Stride-Wert entspricht dem ``/*N*/`` eines Array-Offsets. Aus den gepaarten
    alt/neu-Array-Offsets (positions-gezippte ``/*N*/``) wird eine old->new-Stride-
    Karte gebaut; die blanke Zahl wird darueber uebersetzt (Praefix-Praeferenz +
    klare Mehrheit). Deckt props/dprops/dhprop/doors_NEXT ebenso wie die geteilten
    team_NEXT (f_3605), team_NEXT_settings/next_settings (player-Array-Dimensionen)."""
    old = {m.group(1): m.group(2) for ln in ini_text.splitlines() if (m := _VAL_ONLY_RE.match(ln))}
    new = {m.group(1): m.group(2) for ln in migrated_text.splitlines() if (m := _VAL_ONLY_RE.match(ln))}

    # old_stride -> {new_stride -> [quell-offset-namen]}
    smap: dict = defaultdict(lambda: defaultdict(list))
    for name in old:
        if name not in new:
            continue
        os_ = re.findall(r"/\*(\d+)\*/", old[name])
        ns_ = re.findall(r"/\*(\d+)\*/", new[name])
        if os_ and len(os_) == len(ns_):
            for o, n in zip(os_, ns_):
                smap[int(o)][int(n)].append(name)

    def resolve(base_name: str, v: int):
        cand = smap.get(v)
        if not cand:
            return None
        # Quellen mit gemeinsamem Namens-Praefix (>=4) bevorzugen (Familien-Bezug).
        pref = {ns: srcs for ns, srcs in cand.items()
                if any(_common_prefix(s, base_name) >= 4 for s in srcs)}
        pool = pref or cand
        total = sum(len(s) for s in pool.values())
        best_ns, best_srcs = max(pool.items(), key=lambda kv: len(kv[1]))
        return best_ns if len(best_srcs) >= 0.6 * total else None

    notes: list[tuple[str, str, str]] = []
    out = []
    for line in migrated_text.splitlines():
        m = _INT_OFFSET_RE.match(line)
        if m and _STRIDE_NAME_RE.search(m.group(2)):
            v = int(m.group(4))
            nv = resolve(m.group(2), v)
            if nv is not None and nv != v:
                out.append(f"{m.group(1)}{m.group(3)}{nv}{m.group(5)}{m.group(6)}")
                notes.append((m.group(1), str(v), str(nv)))
                continue
        out.append(line)
    return "\n".join(out) + "\n", notes


def _maps_cache_key(source_dir: pathlib.Path, keep: list[str]) -> str:
    """Content-Key aus dir-Pfad + (name, size, mtime) jeder keep-Datei."""
    h = hashlib.sha1(str(pathlib.Path(source_dir).resolve()).encode())
    for name in keep:
        p = pathlib.Path(source_dir) / name
        try:
            st = p.stat()
            h.update(f"|{name}:{st.st_size}:{int(st.st_mtime)}".encode())
        except OSError:
            h.update(f"|{name}:missing".encode())
    return h.hexdigest()


def _maps_cache_file(cache_dir, source_dir: pathlib.Path) -> pathlib.Path:
    tag = hashlib.sha1(str(pathlib.Path(source_dir).resolve()).encode()).hexdigest()[:12]
    return pathlib.Path(cache_dir) / f"maps-{tag}.json"


def _maps_load(cache_dir, source_dir, key: str):
    f = _maps_cache_file(cache_dir, source_dir)
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("key") != key:
        return None                  # Scripts geaendert -> Cache verworfen
    reverse = defaultdict(set, {g: set(paths) for g, paths in data["reverse"].items()})
    return data["forward"], reverse


def _maps_save(cache_dir, source_dir, key: str, forward: dict, reverse: dict) -> None:
    f = _maps_cache_file(cache_dir, source_dir)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "key": key,
            "forward": forward,
            "reverse": {g: sorted(p) for g, p in reverse.items()},
        }), encoding="utf-8")
    except OSError:
        pass                         # Cache ist rein optional


def _extract_one(args) -> list:
    """Eine Datei lesen + parsen (Top-Level fuer ProcessPoolExecutor-Pickling)."""
    source_dir, name = args
    path = pathlib.Path(source_dir) / name
    if not path.is_file():
        return []
    return list(extract_file(path.read_text(encoding="utf-8", errors="ignore")))


def build_maps(source_dir: pathlib.Path, keep: list[str],
               cache_dir=None, parallel: bool = True) -> tuple[dict, dict]:
    """(forward path->global, reverse global->set(paths)) aus einem Script-Satz.

    Der teure Parse-Scan (~1-2 min ueber alle Creator-Scripts) wird optional auf
    Platte gecacht (Key = Datei-Groesse+mtime), sodass Re-Laeufe ihn ueberspringen;
    der Kalt-Lauf laeuft datei-parallel.
    """
    if cache_dir is not None:
        key = _maps_cache_key(source_dir, keep)
        hit = _maps_load(cache_dir, source_dir, key)
        if hit is not None:
            return hit

    forward: dict[str, str] = {}
    reverse: dict[str, set] = defaultdict(set)

    # Datei-parallele Extraktion (ex.map erhaelt die keep-Reihenfolge -> stabile
    # setdefault-Semantik "erste Datei gewinnt"); serieller Fallback bei Fehlern.
    results = None
    if parallel and len(keep) > 1:
        try:
            with ProcessPoolExecutor() as ex:
                results = list(ex.map(_extract_one, [(str(source_dir), n) for n in keep]))
        except Exception:
            results = None
    if results is None:
        results = [_extract_one((str(source_dir), n)) for n in keep]

    for mps in results:
        for mp in mps:
            g = mp["global"]
            if not mp["path"] or not g.startswith("Global_"):
                continue
            g = canonicalize_global(g)
            if not mp.get("reverse_only"):
                forward.setdefault(mp["path"], g)
            reverse[g].add(mp["path"])
    # Generische bare Keys (ohne '.', z. B. "loc"/"txt" aus parent-losen Label-
    # Writern) machen sonst eindeutige Offsets mehrdeutig: gibt es fuer denselben
    # Global auch einen spezifischeren, parent-behafteten Pfad (mit '.'), nur diesen
    # behalten. Rein-bare Globals (echte Einzel-Keys wie "actv") bleiben unberuehrt.
    for g, paths in reverse.items():
        dotted = {p for p in paths if "." in p}
        if dotted and len(dotted) < len(paths):
            reverse[g] = dotted

    if cache_dir is not None:
        _maps_save(cache_dir, source_dir, key, forward, reverse)
    return forward, reverse


# Familien, deren Werte NICHT direkt DATADICT-serialisiert werden, sondern nur
# im Code ueber Getter/Setter angefasst werden (copy-serialize / Repraesentations-
# Divergenz). Fuer sie ist der semantische Pfad unzuverlaessig (findet die SAVE-
# statt der LIVE-Struktur); daher immer per Kontext-Matching (infer) migrieren.
# Datengetrieben aus der v1.71->current Analyse: semantische Praezision < 90 %.
DEFAULT_INFER_FAMILIES = ("veh_", "doors_", "obj_")

# Familien, die der strukturelle Resolver (tools/structural.py) am besten migriert:
# code-accessed Struct-Felder mit ableitbarer Root-/Stride-/Blatt-Verschiebung.
# Datengetrieben (strukturell >= semantic/infer): siehe validate --per-family.
DEFAULT_STRUCT_FAMILIES = (
    "pa_", "kill_", "zones_", "tp_", "ddblip_", "SMS_", "player_",
    "goto_", "ptemp_", "cps_", "veh_", "obj_", "actor_", "weap_",
)

# Offset-Familien, die NICHT migriert werden sollen: sie zeigen nicht auf ein
# Script-Global, sondern auf ein Feature der Anwendung selbst (Xenvious custom_*),
# das versions-unabhaengig ist. Werden unveraendert durchgereicht und NICHT als
# "review" gezaehlt.
DEFAULT_SKIP_FAMILIES = ("custom_",)


def migrate_text(ini_text: str, old_rev: dict, new_fwd: dict, fallback=None,
                 infer_families: tuple = (), structural=None,
                 struct_families: tuple = (), helper_names=None,
                 unresolved: list = None, loose=None, skip_families: tuple = (),
                 local_resolver=None, field_resolver=None,
                 local_field_resolver=None, anchor_map: dict = None) -> tuple[str, dict, list]:
    out: list[str] = []
    stats: dict[str, int] = defaultdict(int)
    changes: list[tuple[str, str, str]] = []

    in_struct = lambda name: bool(struct_families and name.startswith("OFFSET_") and
                                  any(name[len("OFFSET_"):].startswith(p) for p in struct_families))

    def _emit(name, val, nv, line, stat):
        if _base(nv) != _base(val):
            return False  # Cross-Global-Match (anderer Basis-Global) -> ablehnen
        stats[stat] += 1
        changes.append((name, val, nv))
        out.append(re.sub(r'"[^"]+"', f'"{nv}"', line, count=1))
        return True

    def _apply_fallback(name, val, cval, line, reason):
        # 1) Struktureller Resolver: fuer struct_families VOLL (Root-/Blatt-/Sub-
        #    Array); fuer alle anderen nur SKALAR (Stufenfunktion) — riskante
        #    Array-/Blatt-Rateversuche bleiben so aus.
        if structural:
            nv = structural(name, val, not in_struct(name))
            if nv and canonicalize_global(nv) != cval and _emit(name, val, nv, line, "migrated_structural"):
                return
        # 2) Namens-Fallback ueber Serialisierungs-Helper (func_N(key, global, …));
        #    namens-verankert, nur EINDEUTIGE Keys -> fuellt die verbleibenden
        #    (nicht-struct) Array-Luecken wie irbs/Dror praezise.
        if helper_names:
            nv = helper_names(name, val)
            if nv and canonicalize_global(nv) != cval and _emit(name, val, nv, line, "migrated_helper"):
                return
        # 3) Notations-toleranter semantischer Resolver (loose_key + Agreement):
        #    letzte semantik-verankerte Chance, BEVOR das fuzzy infer greift. Faengt
        #    Faelle, in denen offsets.ini und Script denselben Speicherort ANDERS
        #    schreiben (f_187854 vs f_187854[i]; .f_3 vs [1 /*3*/]; Vektor-Basis
        #    [0 /*3*/]) oder mehrere alte Pfade auf EINEN neuen Wert zeigen
        #    (props_number). Vergleich per loose_key -> keine reine Notations-Aenderung.
        if loose:
            nv = loose(cval)
            if nv:
                if loose_key(nv) != loose_key(cval):
                    if _emit(name, val, nv, line, "migrated_loose"):
                        return
                else:
                    # semantisch identisch (Offset unveraendert) -> Originalzeile behalten
                    stats["unchanged"] += 1
                    out.append(line)
                    return
        # 4) infer-Kontext-Matching (optional, langsam).
        nv = fallback(name, val) if fallback else None
        if nv and canonicalize_global(nv) != cval and _emit(name, val, nv, line, "migrated_fallback"):
            return
        stats[reason] += 1
        if unresolved is not None:
            unresolved.append((name, val))
        out.append(line)

    for line in ini_text.splitlines():
        m = OFFSET_RE.match(line)
        if not m:
            out.append(line)
            continue
        name, val = m.group(1), m.group(2)
        if anchor_map and name in anchor_map:
            # Verified anchor resolver (tools/verified_anchors.py): a handful of
            # scalar/code-accessed offsets with no semantic key, pinned via a
            # structurally distinctive source anchor instead of a raw Global_
            # number. Takes priority over the bare-root skip / generic pipeline.
            nv = anchor_map[name]
            if nv != val:
                stats["migrated_anchor"] += 1
                changes.append((name, val, nv))
                out.append(re.sub(r'"[^"]+"', f'"{nv}"', line, count=1))
            else:
                stats["unchanged"] += 1
                out.append(line)
            continue
        if skip_families and name.startswith("OFFSET_") and \
                any(name[len("OFFSET_"):].startswith(p) for p in skip_families):
            # Anwendungs-eigenes Feature (z. B. Xenvious custom_*) -> nie migrieren.
            stats["skipped_custom"] += 1
            out.append(line)
            continue
        if re.match(r"^Global_\d+(?:\[\d+(?: /\*\d+\*/)?\])?$", val):
            # Bare Root-Global (Global_N bzw. Global_N[literal]) = versions-stabile
            # Basis-Adresse; nur die FELDER darin wandern. Ein "type" = Global_4718592
            # darf nie ein Feld angehaengt bekommen -> unveraendert lassen.
            stats["skipped_root"] += 1
            out.append(line)
            continue
        if not val.startswith("Global_"):
            # Nicht-Global (Local_*, typisierte Locals fLocal_/uLocal_/iLocal_,
            # bare Alias "4718592.f_2", …). Statische Konstanten (Hex/Zahlen-
            # Listen) sind kein Script-Pattern und bleiben. Locals/Aliase kann
            # das Kontext-Matching (infer) aufloesen -> Fallback.
            # Dedizierter Local-Kontext-Matcher (tools/locals.py): loest die
            # current_creator_* Locals per String-/Native-/Struct-/Switch-Anker
            # UND Deklarations-Ordinalzahl praezise auf (schnell, zuverlaessig).
            if local_field_resolver and re.match(r"^[a-z]?Local_\d+(?:\.f_\d+)+$", val.strip()):
                # Kombiniertes <local>.f_<feld> (z. B. neu eingetragenes
                # uLocal_9223.f_808): Local + Feld zusammen aufloesen.
                nv = local_field_resolver(name, val)
                if nv and nv != val:
                    stats["migrated_local"] += 1
                    changes.append((name, val, nv))
                    out.append(re.sub(r'"[^"]+"', f'"{nv}"', line, count=1))
                    continue
                if nv == val:
                    stats["unchanged"] += 1
                    out.append(line)
                    continue
            if local_resolver and re.match(r"^[a-z]?Local_\d+$", val.strip()):
                nv = local_resolver(name, val)
                if nv and nv != val:
                    stats["migrated_local"] += 1
                    changes.append((name, val, nv))
                    out.append(re.sub(r'"[^"]+"', f'"{nv}"', line, count=1))
                    continue
                if nv == val:
                    stats["unchanged"] += 1
                    out.append(line)
                    continue
                # nv is None -> generischer Fallback unten
            # Struct-FELD relativ zum worker/pre-Container (f_N = worker.f_N). Der
            # Container-Local ist bereits aufgeloest; die Feldnummer wandert per
            # Struct-Umbau -> Kontext-Match (Zuweisungs-Konstante/switch/Native).
            if field_resolver and re.match(r"^f_\d+$", val.strip()):
                nv = field_resolver(name, val)
                if nv and nv != val:
                    stats["migrated_field"] += 1
                    changes.append((name, val, nv))
                    out.append(re.sub(r'"[^"]+"', f'"{nv}"', line, count=1))
                    continue
                if nv == val:
                    stats["unchanged"] += 1
                    out.append(line)
                    continue
                # nv is None -> generischer Fallback unten
            looks_dynamic = bool(re.match(r"^[a-z]?Local_\d+", val) or re.match(r"^\d+\.f_\d+", val))
            if fallback and looks_dynamic:
                _apply_fallback(name, val, canonicalize_global(val), line, "nonglobal_unresolved")
            else:
                stats["skipped_non_global"] += 1
                out.append(line)
            continue

        cval = canonicalize_global(val)

        # SEMANTIK ZUERST (auch fuer code-accessed Familien): der Container-Name
        # (DATAARRAY_ADD/DATADICT_SET) ist die Grundwahrheit aus dem Script und
        # praeziser als das strukturelle Raten. Nur wenn kein eindeutiger Pfad
        # existiert, greift der strukturelle/infer-Fallback (via _apply_fallback).
        paths = old_rev.get(cval)
        if not paths or len(paths) > 1:
            # Semantik greift nicht (nicht gefunden oder mehrdeutig) -> optionaler
            # Fallback (infer_offsets Kontext-Matching) fuer code-accessed Felder.
            reason = "not_found_in_old" if not paths else "ambiguous_path"
            _apply_fallback(name, val, cval, line, reason)
            continue

        new_val = new_fwd.get(next(iter(paths)))
        if not new_val:
            stats["path_removed_in_new"] += 1
            out.append(line)
            continue

        # new_fwd fuehrt die VOLLE Form (mit Element-Index); Notation an cval spiegeln.
        new_val = match_notation(new_val, cval)
        if _base(new_val) != _base(cval):
            # Cross-Global-Match (z. B. "start" -> goto in anderem Basis-Global)
            # ist ein Fehlmatch -> semantik verwerfen, Fallback probieren.
            _apply_fallback(name, val, cval, line, "ambiguous_path")
            continue
        if new_val == cval:
            stats["unchanged"] += 1
            out.append(line)
        else:
            stats["migrated"] += 1
            changes.append((name, val, new_val))
            out.append(re.sub(r'"[^"]+"', f'"{new_val}"', line, count=1))

    return "\n".join(out) + "\n", dict(stats), changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ini", default="offsets.ini", help="Quell-offsets.ini (wird NICHT veraendert).")
    parser.add_argument("--old-dir", required=True, help="Scripts der Version, zu der die Quell-offsets.ini passt.")
    parser.add_argument("--new-dir", required=True, help="Scripts der Zielversion.")
    parser.add_argument("--keep-list", default="reports/sources.keep.txt")
    parser.add_argument("--out", default="reports/offsets.migrated.ini")
    parser.add_argument("--report-json", help="Optionaler JSON-Report der Aenderungen.")
    parser.add_argument("--fallback", action="store_true", help="Fuer semantisch nicht aufloesbare Offsets infer_offsets-Kontext-Matching nutzen (langsamer, hoehere Coverage).")
    parser.add_argument("--infer-families", default=",".join(DEFAULT_INFER_FAMILIES),
                        help="Kommaliste von OFFSET-Praefixen (ohne 'OFFSET_'), die immer per infer statt Semantik migriert werden (code-accessed/copy-serialize). Leer = aus.")
    parser.add_argument("--structural", action="store_true",
                        help="Strukturellen Resolver (tools/structural.py) fuer code-accessed Familien nutzen (Root-/Stride-/Blatt-Verschiebung aus Scripts). Schnell, kein Kontext-Scan.")
    parser.add_argument("--struct-families", default=",".join(DEFAULT_STRUCT_FAMILIES),
                        help="Kommaliste von OFFSET-Praefixen fuer den strukturellen Resolver. Leer = aus.")
    parser.add_argument("--skip-families", default=",".join(DEFAULT_SKIP_FAMILIES),
                        help="Kommaliste von OFFSET-Praefixen, die NIE migriert werden (App-eigene Features wie Xenvious custom_*). Leer = aus.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Den build_maps-Plattencache (reports/.cache) ignorieren und neu scannen.")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]

    def rel(p: str) -> pathlib.Path:
        return pathlib.Path(p) if pathlib.Path(p).is_absolute() else root / p

    ini_path = rel(args.ini)
    old_dir, new_dir = rel(args.old_dir), rel(args.new_dir)
    keep = [ln.strip() for ln in rel(args.keep_list).read_text(encoding="utf-8").splitlines() if ln.strip()]

    # build_maps ist der teuerste Schritt (~1-2 min); Ergebnis auf Platte cachen,
    # damit Re-Laeufe bei unveraenderten Scripts sofort weiterlaufen.
    cache_dir = None if args.no_cache else root / "reports" / ".cache"
    _, old_rev = build_maps(old_dir, keep, cache_dir)
    new_fwd, _ = build_maps(new_dir, keep, cache_dir)

    # Notations-tolerante Umkehr-Karte (loose_key -> Pfade), abgeleitet aus old_rev
    # (kein zweiter Scan). Deckt Schreibweisen-Divergenzen und "mehrere alte Pfade,
    # ein neuer Wert"-Faelle ab. Immer an (rein in-memory, guenstig).
    loose_old_rev: dict = defaultdict(set)
    for _g, _paths in old_rev.items():
        _lk = loose_key(_g)
        if _lk is not None:
            loose_old_rev[_lk] |= _paths

    def loose(cval: str):
        lk = loose_key(cval)
        if lk is None:
            return None
        paths = loose_old_rev.get(lk)
        if not paths:
            return None
        outs = {to_offset_notation(match_notation(canonicalize_global(nv), cval))
                for nv in (new_fwd.get(p) for p in paths) if nv}
        return next(iter(outs)) if len(outs) == 1 else None

    fallback = None
    if args.fallback:
        from tools.infer_offsets import infer_candidates, present_value_for_ini  # noqa: E402
        # Datei-Caches vergroessern -> infer wiederholte File-Reads innerhalb einer
        # Familie deutlich schneller (WSL-sicher, nur In-Memory).
        import functools
        from tools import infer_offsets as _io
        for _fn, _sz in (("read_lines", 64), ("read_text_cached", 64),
                         ("candidate_files_for_value_cached", 4096)):
            _f = getattr(_io, _fn, None)
            if _f is not None and hasattr(_f, "__wrapped__"):
                setattr(_io, _fn, functools.lru_cache(maxsize=_sz)(_f.__wrapped__))
        _seen = {"n": 0}

        def fallback(name: str, val: str):  # noqa: F811
            _seen["n"] += 1
            if _seen["n"] % 25 == 0:
                print(f"  ...fallback {_seen['n']} offsets geprueft", flush=True)
            try:
                _, cands = infer_candidates(old_dir, new_dir, val, name)
                return present_value_for_ini(val, cands[0].value, name) if cands else None
            except Exception:
                return None

    structural = None
    helper_names = None
    if args.structural:
        from tools.structural import migrate_value as _struct_migrate  # noqa: E402
        from tools.helper_names import migrate_value as _helper_migrate  # noqa: E402

        def structural(name: str, val: str, scalar_only: bool = False):  # noqa: F811
            try:
                return _struct_migrate(val, old_dir, new_dir, scalar_only=scalar_only)
            except Exception:
                return None

        def helper_names(name: str, val: str):  # noqa: F811
            try:
                return _helper_migrate(val, old_dir, new_dir, tuple(keep))
            except Exception:
                return None

    infer_families = tuple(p for p in args.infer_families.split(",") if p) if args.fallback else ()
    struct_families = tuple(p for p in args.struct_families.split(",") if p) if args.structural else ()
    skip_families = tuple(p for p in args.skip_families.split(",") if p)

    # Verifizierte Anker (immer an, sehr schnell): eine kleine, versionsrobuste
    # Liste code-accessed Scalars ohne semantischen Key (z. B. check_creator,
    # hide_creator_menu), ueber strukturell eindeutige Quelltext-Anker aufgeloest
    # statt ueber eine veraltete Hardcode-Zuordnung.
    from tools.verified_anchors import build_anchor_map as _build_anchor_map  # noqa: E402
    anchor_map = _build_anchor_map(old_dir, new_dir)

    # Dedizierter Local-Kontext-Matcher (immer an, schnell): loest die
    # current_creator_* Locals (fLocal_/uLocal_/iLocal_) per Anker-Matching.
    from tools.locals import migrate_local as _migrate_local  # noqa: E402
    from tools.locals import migrate_field as _migrate_field  # noqa: E402
    from tools.locals import migrate_local_field as _migrate_local_field  # noqa: E402
    _local_cache: dict = {}

    def local_resolver(name: str, val: str):
        try:
            return _migrate_local(val, name, old_dir, new_dir, _cache=_local_cache)
        except Exception:
            return None

    def local_field_resolver(name: str, val: str):
        # Kombiniertes <local>.f_<feld> (z. B. neu eingetragenes uLocal_9223.f_808).
        try:
            return _migrate_local_field(val, old_dir, new_dir, _cache=_local_cache)
        except Exception:
            return None

    # Struct-FELDER (f_N) der worker-/pre-Offsets sind relativ zum jeweiligen
    # Container-Local. Container einmal aufloesen (Referenz-Creator survival), dann
    # die Feldnummer per Zugriffskontext migrieren.
    _ini_text = ini_path.read_text(encoding="utf-8")
    _ref_file = "fm_survival_creator.c"

    def _container(off_name: str):
        m = re.search(r'OFFSET_' + off_name + r'\s*=\s*"([a-z]*Local_(\d+))"', _ini_text)
        if not m:
            return None
        nv = local_resolver(off_name, m.group(1))
        nm = re.search(r"(\d+)", nv) if nv else None
        return (m.group(2), nm.group(1)) if nm else None

    _worker_cont = _container("current_creator_worker_survival")
    _pre_cont = _container("current_creator_pre_survival")

    def field_resolver(name: str, val: str):
        if not re.match(r"^f_\d+$", val.strip()):
            return None
        nl = name.lower()
        # Reihenfolge = Name-Hint; migrate_field waehlt per Direktzugriff den ECHTEN
        # Container (manche current_creator_pre_*-Felder liegen im worker-Struct).
        if "worker" in nl:
            order = (_worker_cont, _pre_cont)
        elif "_pre_" in nl:
            order = (_pre_cont, _worker_cont)
        else:
            return None
        conts = [c for c in order if c]
        if not conts:
            return None
        try:
            return _migrate_field(val, conts, old_dir, new_dir, _ref_file,
                                  _cache=_local_cache)
        except Exception:
            return None

    unresolved: list = []
    migrated_text, stats, changes = migrate_text(
        ini_path.read_text(encoding="utf-8"), old_rev, new_fwd, fallback, infer_families,
        structural, struct_families, helper_names, unresolved, loose, skip_families,
        local_resolver, field_resolver, local_field_resolver, anchor_map)

    # Nachkorrektur: Familien-Konsens (cross-family-Ausreisser) + _NEXT-Strides.
    struct_fams_all = tuple(p for p in args.struct_families.split(",") if p)
    migrated_text, family_notes = postprocess_families(migrated_text, ini_path.read_text(encoding="utf-8"), struct_fams_all)
    if family_notes:
        stats["migrated_family"] = stats.get("migrated_family", 0) + len(family_notes)

    # Nachkorrektur: Stride-/_NEXT-Offsets (blanke Ganzzahl = Array-Element-Groesse)
    # ueber die alt->neu-Stride-Karte. Faengt auch Nicht-struct-Familien (props/
    # dprops/dhprop/doors) und geteilte Strides (team_NEXT, *_settings).
    migrated_text, stride_notes = postprocess_strides(migrated_text, ini_path.read_text(encoding="utf-8"))
    if stride_notes:
        stats["migrated_stride"] = stats.get("migrated_stride", 0) + len(stride_notes)

    out_path = rel(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(migrated_text, encoding="utf-8")

    total_global = sum(v for k, v in stats.items() if k != "skipped_non_global")
    print(f"geschrieben: {out_path}")
    print(f"Global-Offsets: {total_global} | " + " | ".join(f"{k}={v}" for k, v in sorted(stats.items())))

    if args.report_json:
        rp = rel(args.report_json)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({
            "stats": stats,
            "changes": [{"offset": n, "old": o, "new": nw} for n, o, nw in changes],
            "unresolved": [{"offset": n, "value": o} for n, o in unresolved],
        }, indent=2) + "\n", encoding="utf-8")
        print(f"report: {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
