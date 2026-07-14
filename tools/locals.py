#!/usr/bin/env python3
"""Kontext-Matcher fuer lokale Variablen (``fLocal_``/``uLocal_``/``iLocal_``/``Local_``).

Locals sind FUNKTIONS-lokal und pro Creator-Script unterschiedlich nummeriert; ihr
Stack-Index wandert zwischen GTA-Versionen. Anders als Globals gibt es keinen
DATADICT-Key. Stabil sind aber **String-Konstanten**, **Native-Aufrufe** und
**Struct-Initialisierungen**, die rund um den Local stehen.

Ansatz ("search in old, find in new") mit drei Strategien:

1. **Struct-Init**: ``struct<47> Local_145 = { 0, …, -1, … }`` — die Init-Liste ist
   eine hochsignifikante Signatur; identische Liste in new -> neuer Index.
2. **Zeilen-Anker**: Strings/Natives auf DERSELBEN Zeile wie der Local
   (``func_1990(&Local_7143, "SC_RESET_W", …)``) — direkt, hohe Konfidenz.
3. **Fenster-Anker**: seltene Strings/Natives im Umkreis (+/- ``WINDOW`` Zeilen),
   z. B. ``switch (iLocal_40272)`` mit ``SET_OVERRIDE_WEATHER("CLEAR")`` in den
   case-Bodies — gewichtetes Voting auf den naechstgelegenen ``Local_M`` in new.
"""
import argparse
import pathlib
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.structural import _align_runs  # noqa: E402

# Offset-Mode-Suffix -> Creator-Datei. mission teilt SICH ALLE Local-Werte mit lts
# (worker/cam/pre/test/refresh identisch), nutzt also dasselbe Script.
CREATOR_FILES = {
    "survival": "fm_survival_creator.c",
    "capture": "fm_capture_creator.c",
    "lts": "fm_lts_creator.c",
    "mission": "fm_lts_creator.c",
    "dm": "fm_deathmatch_creator.c",
    "race": "fm_race_creator.c",
}

WINDOW = 15                 # +/- Zeilen fuer Fenster-Anker
_MAX_FREQ = 25              # Anker haeufiger als das gelten als unspezifisch

_LOCAL_RE = re.compile(r"^([a-z]*)Local_(\d+)$")
_STR_RE = re.compile(r'"([^"\n]{3,})"')
_NATIVE_RE = re.compile(r"\b([A-Z][A-Z0-9_]*::[A-Z0-9_]+)\b")
# Skalare Locals stehen mit Typ-Praefix (iLocal_/uLocal_/fLocal_), Structs bare
# als &Local_N — beide erfassen, nur die Nummer capturen.
_LOCTOK_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z]?Local_(\d+)\b")


def creator_file_for(offset_name: str) -> str | None:
    """Datei aus dem Mode-Suffix (…_survival/_capture/…); laengster Match zuerst."""
    for mode in sorted(CREATOR_FILES, key=len, reverse=True):
        if offset_name.endswith("_" + mode):
            return CREATOR_FILES[mode]
    return None


def _anchors_on_line(line: str) -> set:
    return set(_STR_RE.findall(line)) | set(_NATIVE_RE.findall(line))


def _file_state(directory, name, cache):
    """Liest Datei + baut (einmalig) Anker-/Local-Indizes fuer schnelles Matching."""
    key = (str(directory), name)
    if key in cache:
        return cache[key]
    p = pathlib.Path(directory) / name
    if not p.is_file():
        cache[key] = None
        return None
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    anchor_freq: Counter = Counter()
    anchor_lines: dict = defaultdict(list)
    local_pos: list = []             # sortierte (lineidx, localidx)
    for i, ln in enumerate(lines):
        for a in _anchors_on_line(ln):
            anchor_freq[a] += 1
            anchor_lines[a].append(i)
        for lm in _LOCTOK_RE.finditer(ln):
            local_pos.append((i, lm.group(1)))
    state = {
        "lines": lines, "text": "\n".join(lines), "anchor_freq": anchor_freq,
        "anchor_lines": anchor_lines, "local_pos": local_pos,
    }
    cache[key] = state
    return state


def _nearest_local(local_pos, center: int, window: int):
    """Naechster (localidx, distanz) zu Zeile ``center`` innerhalb ``window``."""
    best_m, best_d = None, window + 1
    for li, m in local_pos:
        d = abs(li - center)
        if d < best_d:
            best_d, best_m = d, m
        elif li > center + window:
            break
    return (best_m, best_d) if best_m is not None else (None, None)


def _struct_init_match(old_text: str, new_text: str, idx: str) -> str | None:
    """``struct<K> Local_idx = { … }`` -> gleicher Init im new -> neuer Index."""
    m = re.search(r"struct<(\d+)> Local_" + idx + r" = \{([^}]*)\}", old_text)
    if not m:
        return None
    size, body = m.group(1), m.group(2).strip()
    if body.count(",") < 5:          # zu kurze Init -> nicht signifikant
        return None
    sig = re.compile(r"struct<" + size + r"> Local_(\d+) = \{ "
                     + re.escape(body) + r" \}")
    hits = set(sig.findall(new_text))
    return hits.pop() if len(hits) == 1 else None


_SWITCH_SPAN = 40                # Zeilen case-Body fuer den Switch-Fingerprint


def _switch_body_anchors(lines, start: int) -> Counter:
    acc: Counter = Counter()
    for j in range(start + 1, min(len(lines), start + 1 + _SWITCH_SPAN)):
        for a in _anchors_on_line(lines[j]):
            acc[a] += 1
    return acc


def _switch_match(old, new, idx: str) -> str | None:
    """Control-Flow-Locals (``switch (iLocal_N)``) per case-Body-Fingerprint matchen.

    Skalare State-Machine-Locals haben keine Anker auf ihren eigenen Zeilen; ihr
    ``switch``-Rumpf aber schon. Wir vergleichen die (seltenen) Anker des alten
    Switch-Rumpfs mit jedem ``switch``-Rumpf in new und nehmen die groesste Deckung.
    """
    osw = re.compile(r"switch \([^)]*(?<![A-Za-z0-9_])[a-z]?Local_" + idx + r"\b")
    old_starts = [i for i, ln in enumerate(old["lines"]) if osw.search(ln)]
    if not old_starts:
        return None
    want: Counter = Counter()
    for s in old_starts:
        want += _switch_body_anchors(old["lines"], s)
    # nur seltene (distinktive) Anker behalten.
    want = Counter({a: c for a, c in want.items()
                    if old["anchor_freq"].get(a, 1) <= _MAX_FREQ})
    if not want:
        return None

    nsw = re.compile(r"switch \([^)]*(?<![A-Za-z0-9_])[a-z]?Local_(\d+)\b")
    best_m, best_score = None, 0.0
    for i, ln in enumerate(new["lines"]):
        mm = nsw.search(ln)
        if not mm:
            continue
        body = _switch_body_anchors(new["lines"], i)
        score = sum(1.0 / old["anchor_freq"].get(a, 1)
                    for a in want if a in body)
        if score > best_score:
            best_score, best_m = score, mm.group(1)
    return best_m if best_score > 0 else None


# Deklarations-Ordinalzahl fuer "tote" Locals (nur deklariert, nie referenziert),
# z. B. cam_heading. Der Block der Script-globalen Locals steht direkt vor
# ``void __EntryFunction__()``; wir matchen ueber die Position VOM BLOCKENDE.
_DECL_RE = re.compile(r"^\s*(?:[\w<>*]+\s+)*[a-z]?Local_(\d+)\b")


def _entry_line(state) -> int:
    for i, ln in enumerate(state["lines"]):
        if ln.startswith("void __EntryFunction__("):
            return i
    return len(state["lines"])


def _global_decls(state) -> list:
    """Zusammenhaengender Deklarations-Block der Script-Globals vor EntryFunction."""
    if "global_decls" in state:
        return state["global_decls"]
    entry = _entry_line(state)
    decls: list = []
    i = entry - 1
    while i >= 0:
        stripped = state["lines"][i].strip()
        if stripped == "" or stripped.startswith("#"):
            i -= 1
            continue
        m = _DECL_RE.match(state["lines"][i])
        if m and "=" in state["lines"][i]:
            decls.append(int(m.group(1)))
            i -= 1
            continue
        break                        # Ende des Blocks (z. B. `}` der letzten Funktion)
    decls.reverse()
    state["global_decls"] = decls
    return decls


def _decl_ordinal_match(old, new, idx: str) -> str | None:
    """Toter Local -> gleiche Position vom Ende des Global-Decl-Blocks in new."""
    od = _global_decls(old)
    n = int(idx)
    if n not in od:
        return None
    pos_from_end = len(od) - 1 - od.index(n)
    nd = _global_decls(new)
    if pos_from_end < len(nd):
        return str(nd[len(nd) - 1 - pos_from_end])
    return None


def _find_declaring_file(idx: str, directory, cache) -> str | None:
    """Datei, die ``xLocal_idx`` deklariert/nutzt (fuer geteilte Werte wie cam)."""
    pat = re.compile(r"(?<![A-Za-z0-9_])[a-z]?Local_" + idx + r"\b")
    for f in dict.fromkeys(CREATOR_FILES.values()):
        st = _file_state(directory, f, cache)
        if st and pat.search(st["text"]):
            return f
    return None


def migrate_local(old_value: str, offset_name: str, old_dir, new_dir,
                  creator_file: str | None = None, _cache: dict = {}):
    """``fLocal_7143`` (survival) -> ``fLocal_7208``. Gibt None zurueck ohne Treffer."""
    m = _LOCAL_RE.match(old_value.strip())
    if not m:
        return None
    prefix, idx = m.group(1), m.group(2)
    cf = creator_file or creator_file_for(offset_name)
    if not cf:
        # Generischer Offset-Name (kein …_survival/_capture-Suffix): die Datei
        # suchen, in der der Local vorkommt.
        cf = _find_declaring_file(idx, old_dir, _cache)
    if not cf:
        return None
    # Redirect: kommt der Index in cf gar nicht vor (geteilter Wert wie cam_heading,
    # der nur in EINEM Script deklariert ist), die tatsaechlich deklarierende Datei
    # verwenden.
    probe = _file_state(old_dir, cf, _cache)
    idx_re = re.compile(r"(?<![A-Za-z0-9_])[a-z]?Local_" + idx + r"\b")
    if probe is None or not idx_re.search(probe["text"]):
        alt = _find_declaring_file(idx, old_dir, _cache)
        if alt:
            cf = alt
    old = _file_state(old_dir, cf, _cache)
    new = _file_state(new_dir, cf, _cache)
    if not old or not new:
        return None

    # Strategie 1: Struct-Init (hoechste Konfidenz).
    hit = _struct_init_match(old["text"], new["text"], idx)
    if hit:
        return f"{prefix}Local_{hit}"

    # Strategie 2: ZEILEN-Anker (dist=0) — Strings/Natives auf derselben Zeile wie
    # der Local. Verlangt in new, dass der Local auf DERSELBEN Ankerzeile steht
    # (naechster Local mit dist 0). Hohe Konfidenz, keine Nachbar-Verwechslung.
    tok = re.compile(r"(?<![A-Za-z0-9_])[a-z]?Local_" + idx + r"\b")
    old_occ = [i for i, ln in enumerate(old["lines"]) if tok.search(ln)]
    if not old_occ:
        return None

    hit = _vote(old, new, old_occ, window=0)
    if hit:
        return f"{prefix}Local_{hit}"

    # Strategie 2.5: SWITCH-Fingerprint fuer Control-Flow-Locals (switch (iLocal_N)).
    hit = _switch_match(old, new, idx)
    if hit:
        return f"{prefix}Local_{hit}"

    # Strategie 4: Deklarations-Ordinalzahl fuer "tote" Locals (nur deklariert, nie
    # referenziert), z. B. cam_heading — Position vom Ende des Global-Decl-Blocks.
    hit = _decl_ordinal_match(old, new, idx)
    if hit:
        return f"{prefix}Local_{hit}"

    # Strategie 3: FENSTER-Anker (dist bis WINDOW) — nur als Fallback, wenn der
    # Local keine eigenen Zeilen-Anker hat (z. B. skalare Locals in State-Machines).
    hit = _vote(old, new, old_occ, window=WINDOW)
    return f"{prefix}Local_{hit}" if hit else None


def _vote(old, new, old_occ, window: int) -> str | None:
    """Sammelt Anker im Umkreis ``window`` und votet den naechsten new-Local."""
    my_anchors: dict = {}            # anker -> kleinste distanz zum local
    for i in old_occ:
        lo, hi = max(0, i - window), min(len(old["lines"]), i + window + 1)
        for j in range(lo, hi):
            for a in _anchors_on_line(old["lines"][j]):
                d = abs(i - j)
                if a not in my_anchors or d < my_anchors[a]:
                    my_anchors[a] = d

    votes: Counter = Counter()
    for a, dist in my_anchors.items():
        if old["anchor_freq"].get(a, 1) > _MAX_FREQ:
            continue
        hits = new["anchor_lines"].get(a, [])
        if not hits or len(hits) > _MAX_FREQ:
            continue
        weight = 1.0 / (old["anchor_freq"][a] * len(hits) * (1 + dist))
        for j in hits:
            mm, d2 = _nearest_local(new["local_pos"], j, window)
            if mm is not None:
                votes[mm] += weight / (1 + d2)

    return votes.most_common(1)[0][0] if votes else None


# ---------------------------------------------------------------------------
# Struct-FELDER innerhalb eines aufgeloesten Container-Locals (worker/pre).
# Offsets wie worker_offset_refresh (f_562) sind KEINE Locals, sondern ein Feld
# RELATIV zum worker-Local: `Local_7143.f_562`. Das Feld wandert, wenn der Struct
# umgebaut wird (f_562 -> f_565). Da der Container-Local bereits aufgeloest ist
# (Local_7143 -> Local_7208), matchen wir die Feldnummer ueber den STABILEN
# Zugriffskontext: die Zuweisungs-/Vergleichs-Konstante (`= 94`, `!= 94`, `= 69`),
# ob das Feld in einem `switch` steht, und begleitende Natives.
# ---------------------------------------------------------------------------
_CMP_RE = re.compile(r"\s*(==|!=|<=|>=|=|<|>)\s*(-?\d+)")


def _field_signatures(state, cont: str) -> dict:
    """``Local_<cont>.f_N`` -> Counter von Signatur-Tokens je Feldnummer N."""
    pat = re.compile(r"\bLocal_" + cont + r"\.f_(\d+)\b")
    sigs: dict = defaultdict(Counter)
    for ln in state["lines"]:
        for m in pat.finditer(ln):
            fld = m.group(1)
            cm = _CMP_RE.match(ln[m.end():])
            if cm:                                   # `= 94`, `!= 94`, `== 3` …
                sigs[fld][f"op{cm.group(1)}{cm.group(2)}"] += 1
            if "switch (" in ln[:m.start()]:
                sigs[fld]["#switch"] += 1
            for nat in _NATIVE_RE.findall(ln):
                sigs[fld][f"nat:{nat}"] += 1
            for s in _STR_RE.findall(ln):            # String-Argumente auf der Zeile
                sigs[fld][f"str:{s}"] += 1
    return sigs


def _context_field(old, new, container_old: str, container_new: str,
                   field_n: str) -> str | None:
    """Feldnummer per Zugriffskontext (Konstante/switch/Native/String) matchen."""
    old_sig = _field_signatures(old, container_old).get(field_n)
    if not old_sig:
        return None
    new_sigs = _field_signatures(new, container_new)
    if not new_sigs:
        return None
    # Token-Seltenheit ueber ALLE new-Felder -> distinktive Konstanten zaehlen mehr.
    token_freq: Counter = Counter()
    for sig in new_sigs.values():
        for tok in sig:
            token_freq[tok] += 1
    best_m, best_score = None, 0.0
    for cand, sig in new_sigs.items():
        score = 0.0
        for tok, cnt in old_sig.items():
            if tok in sig:
                score += min(cnt, sig[tok]) / token_freq[tok]
        if score > 0:                            # Bonus fuer Feldnummer-Naehe
            score += 0.001 / (1 + abs(int(cand) - int(field_n)))
        if score > best_score:
            best_score, best_m = score, cand
    return f"f_{best_m}" if best_m else None


def _field_map(old, new, cont_old: str, cont_new: str):
    """(Alignment old->new, old-Domain) der Feldnummern auf einem Container-Local.

    Richtet ALLE direkt zugegriffenen Feldnummern des Structs aus (wie bei den
    Globals) -> robuste, layout-weite Zuordnung inklusive Einfuege-Verschiebungen.
    """
    of = {int(x) for x in re.findall(r"\bLocal_" + cont_old + r"\.f_(\d+)", old["text"])}
    nf = {int(x) for x in re.findall(r"\bLocal_" + cont_new + r"\.f_(\d+)", new["text"])}
    return _align_runs(of, nf), of


def _step_interpolate(amap: dict, domain, n: int):
    """Step-Funktion: naechstes gemapptes old-Feld <= n, dessen Delta anwenden.

    Nur INNERHALB des Structs (kein Extrapolieren ueber das hoechste bekannte Feld
    hinaus) — sonst wuerde ein feld-fremder Wert wild fortgeschrieben.
    """
    mapped = [k for k in domain if amap.get(k) is not None]
    if not mapped or n > max(mapped):
        return None
    below = [k for k in mapped if k <= n]
    if not below:
        return None
    k = max(below)
    return n + (amap[k] - k)


def migrate_field(field_value: str, candidates, old_dir, new_dir,
                  creator_file: str = "fm_survival_creator.c",
                  _cache: dict = {}) -> str | None:
    """``f_562`` -> ``f_565``: Feldnummer relativ zu einem aufgeloesten Container.

    ``candidates`` = Liste von (container_old, container_new)-Indizes (worker
    7143/7208 UND pre 8045/8126) — die ``current_creator_pre_*``-Felder verteilen
    sich naemlich auf BEIDE Structs. Der Container mit DIREKTEM Zugriff auf das
    Feld gewinnt; darauf per Alignment + Zugriffskontext (Uebereinstimmung bevorzugt),
    sonst per Step-Interpolation aus dem Struct-Layout.
    """
    m = re.match(r"^f_(\d+)$", field_value.strip())
    if not m:
        return None
    n = int(m.group(1))
    old = _file_state(old_dir, creator_file, _cache)
    new = _file_state(new_dir, creator_file, _cache)
    if not old or not new:
        return None

    # Container nach Anzahl DIREKTER Zugriffe auf f_n ordnen (Name-Hint = Reihenfolge).
    ranked = []
    for co, cn in candidates:
        cnt = len(re.findall(r"\bLocal_" + co + r"\.f_" + str(n) + r"\b", old["text"]))
        ranked.append((cnt, co, cn))
    ranked.sort(key=lambda t: t[0], reverse=True)

    # 1) Container mit Direktzugriff: Alignment + Kontext (Uebereinstimmung = sicher).
    for cnt, co, cn in ranked:
        if cnt == 0:
            continue
        amap, domain = _field_map(old, new, co, cn)
        a = amap.get(n)
        ctx = _context_field(old, new, co, cn, str(n))
        if a and ctx and f"f_{a}" == ctx:
            return ctx
        if a:
            return f"f_{a}"
        if ctx:
            return ctx
        s = _step_interpolate(amap, domain, n)
        if s is not None:
            return f"f_{s}"

    # 2) Kein Direktzugriff (param-indirekt): Step-Interpolation aus dem
    #    name-gehinteten Container (erster Kandidat).
    for co, cn in candidates:
        amap, domain = _field_map(old, new, co, cn)
        s = _step_interpolate(amap, domain, n)
        if s is not None:
            return f"f_{s}"
    return None


# Kombiniertes ``<local>.f_<feld>`` (z. B. eine NEU eingetragene Offset-Adresse
# ``uLocal_9223.f_808``): Local UND Feld zusammen aufloesen.
_LOCAL_FIELD_RE = re.compile(r"^([a-z]?Local_(\d+))((?:\.f_\d+)+)$")


def migrate_local_field(value: str, old_dir, new_dir, _cache: dict = {}) -> str | None:
    """``uLocal_9223.f_808`` -> ``uLocal_9307.f_814``: Local + Feld kombiniert.

    Findet die Creator-Datei des Locals, loest den Local-Index (old->new) und die
    ERSTE Feldnummer relativ dazu auf. Verschachtelte Folgefelder (``.f_A.f_B``)
    bleiben unveraendert (sie zeigen in einen anderen Sub-Struct).
    """
    m = _LOCAL_FIELD_RE.match(value.strip())
    if not m:
        return None
    local_str, idx, fields = m.group(1), m.group(2), m.group(3)
    cf = _find_declaring_file(idx, old_dir, _cache)
    if not cf:
        return None
    new_local = migrate_local(local_str, "", old_dir, new_dir,
                              creator_file=cf, _cache=_cache)
    if not new_local:
        return None
    new_idx = re.search(r"(\d+)", new_local).group(1)
    first = re.match(r"\.(f_\d+)", fields)
    new_field = migrate_field(first.group(1), [(idx, new_idx)], old_dir, new_dir,
                              creator_file=cf, _cache=_cache)
    if not new_field:
        return None
    return f"{new_local}.{new_field}{fields[first.end():]}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-dir", default="old")
    p.add_argument("--new-dir", default="new")
    p.add_argument("--offset", required=True, help="OFFSET-Name (fuer Mode->Datei).")
    p.add_argument("--value", required=True, help="alter Local-Wert, z. B. fLocal_7143")
    p.add_argument("--file", help="Creator-Datei erzwingen (statt Mode->Datei).")
    args = p.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]

    def _resolve(d):
        pd = pathlib.Path(d)
        return pd if pd.is_absolute() else root / d

    nv = migrate_local(args.value, args.offset, _resolve(args.old_dir),
                       _resolve(args.new_dir), creator_file=args.file)
    print(f"{args.value} -> {nv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
