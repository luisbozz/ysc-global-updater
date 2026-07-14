#!/usr/bin/env python3
"""Semantischer Global-Extractor fuer den ysc-global-updater.

Liest die per ``select_sources.py`` ausgewaehlten Creator-/Launcher-Files und
leitet eine semantische Zuordnung ``Name -> Global`` ab (``reports/globals.json``).

Ansatz (bewaehrt aus dem Prototyp, hier fuer mehrere Files zusammengefuehrt):
- ``DATADICT_CREATE_DICT/ARRAY`` definieren Container: ein Handle-Ausdruck
  (z. B. ``uParam0->f_9213``) bekommt einen String-Key und einen Parent.
- ``DATADICT_SET_*`` / ``DATAARRAY_ADD_*`` sind die Blaetter: sie binden einen
  semantischen Key an einen ``Global_``-Ausdruck (Typ aus dem Call-Namen).

Pro File zwei lineare Durchlaeufe: Pass 1 sammelt CREATE-Container (inkl.
``&label``-Keys via TextLabel-Status), Pass 2 sammelt die Blaetter und loest
Keys/Parent-Pfade auf. Ergebnisse werden ueber alle Files dedupliziert und mit
Herkunft (welche Files) versehen.

Stage 1 erfasst bewusst nur Blaetter mit direktem ``Global_``-Wert. Helper-Func-
und Pointer-Alias-Faelle (published_*/saved_*/surv_*/cps_*) folgen in Stage 2.
"""
import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict


CREATE_RE = re.compile(
    r"(?P<container>[A-Za-z_]\w*(?:->f_\d+)?(?:\[[^\]]+\])?)\s*=\s*"
    r"DATAFILE::DATADICT_CREATE_(?P<kind>DICT|ARRAY)\s*\(\s*"
    r"(?P<parent>[^,]+?)\s*,\s*(?P<key>\"[^\"]+\"|&\w+)\s*\)\s*;"
)
SET_RE = re.compile(r"DATAFILE::DATADICT_SET_(?P<kind>BOOL|INT|FLOAT|STRING|VECTOR)\s*\((?P<args>.*)\)\s*;")
ADD_RE = re.compile(r"DATAFILE::DATAARRAY_ADD_(?P<kind>BOOL|INT|FLOAT|STRING|VECTOR)\s*\((?P<args>.*)\)\s*;")
# LOAD-Seite: manche Strings/Werte werden nur beim LADEN mit ihrem Key sichtbar,
# z. B. StringCopy(&(Global_X), DATADICT_GET_STRING(dict, &keyvar), N) — die
# SAVE-Seite nutzt hier einen key-losen Helper. keyvar traegt den String-Key.
GET_LOAD_RE = re.compile(
    r"StringCopy\s*\(\s*&\(\s*(Global_\d+(?:\.f_\d+|\[[^\]]+\])*)\s*\)\s*,\s*"
    r"DATAFILE::DATADICT_GET_(?:STRING|INT|FLOAT|BOOL|VECTOR)\s*\([^,]+,\s*&(\w+)"
)
LABEL_ASSIGN_RE = re.compile(r"TEXT_LABEL_ASSIGN_STRING\s*\(\s*&(\w+)\s*,\s*\"([^\"]*)\"")
LABEL_APPEND_S_RE = re.compile(r"TEXT_LABEL_APPEND_STRING\s*\(\s*&(\w+)\s*,\s*\"([^\"]*)\"")
# Viele Creator setzen den Container-Key nicht per TEXT_LABEL_*, sondern per
# StringCopy(&var, "key", N) (+ StringIntConCat(&var, idx, N) fuer den Laufindex).
# Das ist ein stabiles API-Muster (keine versions-fragilen func_-Nummern) und
# muss als Key-Quelle mitgetrackt werden, sonst bleiben container-benannte
# (positions-serialisierte) Felder namenlos.
STRING_COPY_RE = re.compile(r"StringCopy\s*\(\s*&(\w+)\s*,\s*\"([^\"]*)\"")
# Mehrteilige Keys: nach StringCopy(&v,"w") + StringIntConCat(&v,idx) haengen
# viele Creator noch ein statisches Suffix an: StringConCat(&v,"Az") -> Key "wAz".
# Ohne das kollabieren alle Felder eines Blocks auf denselben Basis-Key.
STRING_CONCAT_RE = re.compile(r"StringConCat\s*\(\s*&(\w+)\s*,\s*\"([^\"]*)\"")

# Helper-Wrapper: viele Creator binden (key, global, container) nicht direkt,
# sondern reichen sie an eine kleine Forwarding-Funktion func_N(...) durch, die
# intern DATADICT_SET_* / DATAARRAY_ADD_ aufruft. Wir entdecken solche Wrapper
# generisch (funktionsnummern aendern sich pro Update) und lesen ihre Call-Sites.
FUNC_CALL_LINE_RE = re.compile(r"\b(func_\d+)\s*\((.*)\)\s*;")
HELPER_BODY_RE = re.compile(
    r"DATAFILE::(DATADICT_SET|DATAARRAY_ADD)_(BOOL|INT|FLOAT|STRING|VECTOR)\s*\(([^;]*)\)\s*;"
)
# Manche Writer erzeugen ihren Ziel-Array erst selbst: DATADICT_CREATE_ARRAY(parent,
# key) + DATAARRAY_ADD(value). Der CREATE-Call verraet Key- und Parent-Parameter.
CREATE_BODY_RE = re.compile(r"DATADICT_CREATE_(?:DICT|ARRAY)\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)")
# Label-Builder-Wrapper: func_N(&buf, "key", idx…) das intern StringCopy/TEXT_LABEL_
# ASSIGN(buf, key) macht. Der Key landet ueber die Puffer-Variable beim Value-Writer.
LABEL_BUILDER_BODY_RE = re.compile(
    r"(?:StringCopy|TEXT_LABEL_ASSIGN_STRING)\s*\(\s*&?(\w+)\s*,\s*&?(\w+)"
)

TYPE_BY_KIND = {"BOOL": "bool", "INT": "int", "FLOAT": "float", "STRING": "string", "VECTOR": "vector"}


def split_args(arg_string: str) -> list[str]:
    """Args auf oberster Ebene trennen (respektiert (), [] und Strings)."""
    args, cur = [], []
    depth_paren = depth_bracket = 0
    in_string = escape = False
    for ch in arg_string:
        if in_string:
            cur.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            cur.append(ch)
            continue
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1
        if ch == "," and depth_paren == 0 and depth_bracket == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        args.append("".join(cur).strip())
    return args


def normalize_ref(expr: str) -> str:
    expr = expr.strip()
    if expr.startswith("&(") and expr.endswith(")"):
        expr = expr[2:-1].strip()
    elif expr.startswith("&"):
        expr = expr[1:].strip()
    if expr.startswith("*"):
        expr = expr[1:].strip()
    if expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1].strip()
    return expr


def canonicalize_global(value: str) -> str:
    """Decompiler-Rauschen vereinheitlichen: variable Array-Indizes positions-
    basiert auf i/j/k... normalisieren (Stride-Kommentar /*N*/ bleibt), literale
    Indizes bleiben. Macht Werte decompiler-/versions-vergleichbar und
    offsets.ini-konform (dort steht z. B. [i /*255*/] bzw. verschachtelt [j /*36*/]).
    """
    letters = iter("ijklmnopq")

    def repl(m: re.Match) -> str:
        stride = m.group(2)
        idx = next(letters, "i")
        return "[" + idx + (" " + stride if stride else "") + "]"

    return re.sub(r"\[\s*([A-Za-z_]\w*)\s*(/\*\d+\*/)?\s*\]", repl, value)


def index_variants(canon: str) -> list[str]:
    """Registrier-Varianten eines Werts (VOLL zuerst) fuer den Reverse-Lookup.

    Die offsets.ini schreibt Serialisierungs-Indizes uneinheitlich: mal voll
    (``f_5061[j /*9*/]``), mal nur die Haupt-Iteration (``f_9[j /*27*/].f_2`` ->
    ``f_9.f_2``, aeusseres ``[..]`` bleibt), mal ganz ohne. Wir registrieren daher
    zusaetzlich (a) „nur aeussersten Index behalten, innere weg" und (b) „alle
    variablen Indizes weg". new_fwd behaelt die VOLLE Form; migrate.match_notation()
    bringt die Ausgabe in die jeweils gewaehlte Schreibweise."""
    # Reine Serialisierungs-Schleife (bare trailing [j] OHNE Stride, bei >=2 Ebenen)
    # gehoert nie zum Offset -> immer weg (f_1699[j] -> f_1699).
    if canon.count("[") >= 2:
        canon = re.sub(r"\[[ijklmnopq]\]$", "", canon)
    variants = [canon]
    idxs = list(re.finditer(r"\[[A-Za-z_]\w*(?: /\*\d+\*/)?\]", canon))
    if len(idxs) >= 2:  # innere variable Indizes entfernen, ersten behalten
        reduced = canon
        for mm in reversed(idxs[1:]):
            reduced = reduced[:mm.start()] + reduced[mm.end():]
        if reduced not in variants:
            variants.append(reduced)
    if idxs:  # alle variablen Indizes entfernen
        flat = re.sub(r"\[[A-Za-z_]\w*(?: /\*\d+\*/)?\]", "", canon)
        if flat not in variants:
            variants.append(flat)
    return variants


class LabelState:
    """Verfolgt TEXT_LABEL_ASSIGN/APPEND_STRING, um die Basis eines &key zu kennen."""

    def __init__(self) -> None:
        self.bases: dict[str, str] = {}

    def feed(self, line: str) -> None:
        m = LABEL_ASSIGN_RE.search(line)
        if m:
            self.bases[m.group(1)] = m.group(2)
            return
        m = STRING_COPY_RE.search(line)
        if m:
            # StringCopy setzt die Basis neu (wie ASSIGN). Ein nachfolgendes
            # StringIntConCat haengt nur den Laufindex an -> Basis-Key bleibt.
            self.bases[m.group(1)] = m.group(2)
            return
        m = LABEL_APPEND_S_RE.search(line)
        if m and m.group(1) in self.bases:
            self.bases[m.group(1)] += m.group(2)
            return
        m = STRING_CONCAT_RE.search(line)
        if m and m.group(1) in self.bases:
            # Statisches Suffix an den Key anhaengen (StringConCat(&v,"Az")).
            self.bases[m.group(1)] += m.group(2)

    def key_for(self, var: str) -> str | None:
        return self.bases.get(var)


def resolve_key(raw: str, labels: LabelState) -> tuple[str | None, bool]:
    """(key, dynamic). Literal -> fester Key; &var -> Label-Basis, dynamic=True."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1], False
    if raw.startswith("&"):
        return labels.key_for(raw[1:].strip()), True
    return None, False


def build_container_map(text: str) -> dict[str, dict]:
    """container-Ausdruck -> {key, parent, dynamic} aus allen CREATE-Calls."""
    labels = LabelState()
    containers: dict[str, dict] = {}
    for line in text.splitlines():
        labels.feed(line)
        m = CREATE_RE.search(line)
        if not m:
            continue
        container = normalize_ref(m.group("container"))
        key, dynamic = resolve_key(m.group("key"), labels)
        if key is None:
            continue
        containers[container] = {
            "key": key,
            "parent": normalize_ref(m.group("parent")),
            "dynamic": dynamic,
        }
    return containers


def parent_path(container: str, containers: dict[str, dict]) -> str | None:
    parts, cur, seen = [], container, set()
    while cur in containers and cur not in seen:
        seen.add(cur)
        info = containers[cur]
        parts.append(info["key"])
        cur = info["parent"]
    if not parts:
        return None
    parts.reverse()
    return ".".join(parts)


def _index_defs(text: str, wanted: set[str]) -> dict[str, tuple[str, list[str]]]:
    """Einmal-Scan: func_N -> (body, param-namen) fuer die gesuchten Funktionen."""
    out: dict[str, tuple[str, list[str]]] = {}
    if not wanted:
        return out
    for m in re.finditer(r"\b[\w<>*]+\s+(func_\d+)\s*\(([^)]*)\)\s*(?://[^\n]*)?\s*\{", text):
        name = m.group(1)
        if name not in wanted or name in out:
            continue
        params = [p.strip().split()[-1].replace("*", "").strip() for p in split_args(m.group(2)) if p.strip()]
        start, depth, i = m.end(), 1, m.end()
        while i < len(text):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    out[name] = (text[start:i], params)
                    break
            i += 1
        if len(out) == len(wanted):
            break
    return out


def discover_helpers(text: str, funcs: set[str]) -> dict[str, dict]:
    """Forwarding-Wrapper erkennen: func_N -> {key_idx, val_idx, container_idx, type}."""
    helpers: dict[str, dict] = {}
    for name, (body, params) in _index_defs(text, set(funcs)).items():
        if not params:
            continue

        def idx(param: str) -> int | None:
            return params.index(param) if param in params else None

        # ALLE SET/ADD-Calls im Body pruefen und den ersten nehmen, dessen WERT ein
        # Parameter ist. Manche Writer fuellen den Array erst mit Defaults
        # (DATAARRAY_ADD_VECTOR(*iParam3, func_200())) und haengen den echten Wert
        # (*uParam1) erst danach an — der erste Treffer waere sonst wertlos.
        # Bei mehreren ADDs den mit dem KLEINSTEN Wert-Param-Index nehmen: der echte
        # Wert ist per Konvention frueh (uParam1), die Default-Fuellung nutzt den
        # letzten Param (fParam5).
        best_add = None  # (val_idx, dict)
        set_found = False
        for m in HELPER_BODY_RE.finditer(body):
            call, kind = m.group(1), m.group(2)
            cargs = [normalize_ref(a) for a in split_args(m.group(3))]
            if call == "DATADICT_SET" and len(cargs) >= 3:
                ci, ki, vi = idx(cargs[0]), idx(cargs[1]), idx(cargs[2])
                if ki is None or vi is None:
                    continue
                helpers[name] = {"key_idx": ki, "val_idx": vi, "container_idx": ci, "type": TYPE_BY_KIND[kind]}
                set_found = True
                break
            if call == "DATAARRAY_ADD" and len(cargs) >= 2:
                hi, vi = idx(cargs[0]), idx(cargs[1])
                if vi is None:
                    continue
                # Key/Parent kommen bei CREATE+ADD-Writern (func_633-Art) aus dem
                # DATADICT_CREATE_ARRAY(parent, key)-Call im selben Body.
                ki, ci = None, hi
                cm = CREATE_BODY_RE.search(body)
                if cm:
                    ki2, ci2 = idx(normalize_ref(cm.group(2))), idx(normalize_ref(cm.group(1)))
                    if ki2 is not None:
                        ki = ki2
                    if ci2 is not None:
                        ci = ci2
                if best_add is None or vi < best_add[0]:
                    best_add = (vi, {"key_idx": ki, "val_idx": vi, "container_idx": ci, "type": TYPE_BY_KIND[kind]})
        if not set_found and best_add is not None:
            helpers[name] = best_add[1]
    return helpers


def discover_label_builders(text: str, funcs: set[str]) -> dict[str, dict]:
    """Label-Builder erkennen: func_N, dessen Body als erstes StringCopy/TEXT_LABEL_
    ASSIGN(buf_param, key_param) macht -> {name: {buf_idx, key_idx}}. Die Call-Site
    ``func_N(&Var, "key", …)`` bindet dann den Key an die Puffer-Variable ``Var``."""
    builders: dict[str, dict] = {}
    for name, (body, params) in _index_defs(text, set(funcs)).items():
        if not params:
            continue
        m = LABEL_BUILDER_BODY_RE.search(body)
        if not m:
            continue
        buf, key = m.group(1), m.group(2)
        if buf in params and key in params:
            builders[name] = {"buf_idx": params.index(buf), "key_idx": params.index(key)}
    return builders


_GLOBAL_IN_ARG = re.compile(r"\*?Global_\d+(?:\.f_\d+|\[[^\]]+\])+")


def extract_helper_calls(text: str, containers: dict[str, dict]) -> list[dict]:
    """Mappings aus Wrapper-Call-Sites. Der Key ist entweder ein Literal (``"kkey"``)
    ODER eine Puffer-Variable ``&Var``, die zuvor per Label-Builder-Helper
    (``func_629(&Var, "kkey", …)``) gesetzt wurde. Der Value ist ein direkter
    ``Global_``-Ausdruck. Deckt die label-serialisierten f_3605-Felder (plvrl,
    bmmxh, gbtpp, anfMBS, …) ab, die kein Inline-DATADICT_SET haben."""
    cand: list[tuple[str, str, str]] = []
    funcs: set[str] = set()
    for line in text.splitlines():
        m = FUNC_CALL_LINE_RE.search(line)
        if m and ("Global_" in m.group(2) or '"' in m.group(2)):
            funcs.add(m.group(1))
    if not funcs:
        return []

    helpers = discover_helpers(text, funcs)
    builders = discover_label_builders(text, funcs)
    if not helpers:
        return []

    labels = LabelState()
    out: list[dict] = []
    for line in text.splitlines():
        labels.feed(line)  # direkte StringCopy/TEXT_LABEL_ASSIGN-Zeilen (setzen Label-Basis)
        m = FUNC_CALL_LINE_RE.search(line)
        if not m:
            continue
        func, argstr = m.group(1), m.group(2)

        b = builders.get(func)
        if b is not None:
            args = split_args(argstr)
            if b["buf_idx"] < len(args) and b["key_idx"] < len(args):
                buf = re.sub(r"^&", "", args[b["buf_idx"]].strip())
                kraw = args[b["key_idx"]].strip()
                if kraw.startswith('"') and kraw.endswith('"'):
                    labels.bases[buf] = kraw[1:-1]
            continue

        h = helpers.get(func)
        if not h or h["key_idx"] is None:
            continue
        args = split_args(argstr)
        if h["key_idx"] >= len(args) or h["val_idx"] >= len(args):
            continue
        value = normalize_ref(args[h["val_idx"]])
        if "Global_" not in value:
            continue
        kraw = args[h["key_idx"]].strip()
        if kraw.startswith('"') and kraw.endswith('"'):
            key = kraw[1:-1]
        else:
            key = labels.key_for(re.sub(r"^&", "", kraw))
        if not key:
            continue
        parent = None
        ci = h["container_idx"]
        if ci is not None and ci < len(args):
            parent = parent_path(normalize_ref(args[ci]), containers)
        if not value.lstrip("*").startswith("Global_"):
            gm = _GLOBAL_IN_ARG.search(value)
            if gm:
                value = gm.group(0)
        for i, v in enumerate(index_variants(canonicalize_global(value))):
            mp = _mapping(key, v, h["type"], func, parent, False)
            if i:
                mp["reverse_only"] = True
            out.append(mp)
    return out


def extract_file(text: str) -> list[dict]:
    containers = build_container_map(text)
    # Container werden bei CREATE mit einem Laufindex geschrieben (f_5711[iVar0]),
    # beim ADD aber mit einem ANDEREN Indexnamen gelesen (f_5711[bVar0]). Fuer den
    # Abgleich zusaetzlich index-frei indizieren (f_5711).
    by_field: dict[str, dict] = {}
    for cref, info in containers.items():
        by_field.setdefault(re.sub(r"\[[^\]]*\]", "", cref), info)

    def _container_info(ref: str) -> dict | None:
        return containers.get(ref) or by_field.get(re.sub(r"\[[^\]]*\]", "", ref))

    def _strip_iter_index(value: str) -> str:
        # Den abschliessenden LAUF-Index (Variable) nur entfernen, wenn es einen
        # FRUEHEREN Index gibt (>=2 Ebenen) — dann ist er die Element-Iteration
        # beim Serialisieren (f_3605[i].f_1699[j] -> f_3605[i].f_1699). Bei nur
        # EINER Ebene (f_7[i]) gehoert der Index zum Offset und bleibt. Literale
        # wie [0] bleiben immer.
        if value.count("[") >= 2:
            return re.sub(r"\[[ijklmnopq](?: /\*\d+\*/)?\]$", "", value)
        return value

    labels = LabelState()
    out: list[dict] = []

    for line in text.splitlines():
        labels.feed(line)

        m = GET_LOAD_RE.search(line)
        if m:
            key = labels.key_for(m.group(2))
            if key:
                for i, v in enumerate(index_variants(canonicalize_global(m.group(1)))):
                    mp = _mapping(key, v, "string", "GET_LOAD", None, False)
                    if i:
                        mp["reverse_only"] = True
                    out.append(mp)
            continue

        m = SET_RE.search(line)
        if m:
            args = split_args(m.group("args"))
            if len(args) < 3:
                continue
            kind = m.group("kind")
            container = normalize_ref(args[0])
            key, dynamic = resolve_key(args[1], labels)
            if key is None:
                continue
            if kind == "VECTOR":
                value = ", ".join(normalize_ref(a) for a in args[2:5])
            else:
                value = normalize_ref(args[2])
            if "Global_" not in value:
                continue
            parent = parent_path(container, containers)
            out.append(_mapping(key, value, TYPE_BY_KIND[kind], f"DATADICT_SET_{kind}", parent, dynamic))
            continue

        m = ADD_RE.search(line)
        if m:
            args = split_args(m.group("args"))
            if len(args) < 2:
                continue
            kind = m.group("kind")
            handle = normalize_ref(args[0])
            value = normalize_ref(args[1])
            if "Global_" not in value:
                continue
            # Wert kann in einem Konverter-Helper stecken: DATAARRAY_ADD_INT(cont,
            # func_741(Global_…f_7)) -> inneren Global_-Ausdruck herausziehen.
            if not value.lstrip("*").startswith("Global_"):
                gm = _GLOBAL_IN_ARG.search(value)
                if gm:
                    value = gm.group(0)
            info = _container_info(handle)
            key = info["key"] if info else None
            parent = parent_path(info["parent"], containers) if info else None
            dyn = bool(info and info["dynamic"])
            # VOLL zuerst (bleibt primaer in forward) + index-reduzierte Varianten
            # als zusaetzliche Reverse-Keys (bmmxh behaelt Index, dpos/actor nicht).
            canon = canonicalize_global(value)
            for i, v in enumerate(index_variants(canon)):
                mp = _mapping(key, v, TYPE_BY_KIND[kind], f"DATAARRAY_ADD_{kind}", parent, dyn)
                if i:
                    mp["reverse_only"] = True  # nur Reverse-Lookup, nicht forward
                out.append(mp)

    out += extract_helper_calls(text, containers)
    return out


def _mapping(key, value, dtype, helper, parent, dynamic) -> dict:
    path = f"{parent}.{key}" if parent and key else key
    return {
        "key": key,
        "global": canonicalize_global(value),
        "type": dtype,
        "helper": helper,
        "parent": parent,
        "path": path,
        "dynamic": dynamic,
    }


def merge(per_file: dict[str, list[dict]]) -> list[dict]:
    """Dedupe ueber (path, global, type); Herkunfts-Files sammeln."""
    acc: dict[tuple, dict] = {}
    for fname, mappings in per_file.items():
        for m in mappings:
            k = (m["path"], m["global"], m["type"])
            if k not in acc:
                acc[k] = {**m, "sources": []}
            if fname not in acc[k]["sources"]:
                acc[k]["sources"].append(fname)
    return sorted(acc.values(), key=lambda m: (m["path"] or "", m["global"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="new")
    parser.add_argument("--keep-list", default="reports/sources.keep.txt")
    parser.add_argument("--out", default="reports/globals.json")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]

    def rel(p: str) -> pathlib.Path:
        return pathlib.Path(p) if pathlib.Path(p).is_absolute() else root / p

    source_dir = rel(args.source_dir)
    keep_path = rel(args.keep_list)
    if not keep_path.is_file():
        print(f"[ERRO] Keep-Liste fehlt: {keep_path} (erst select_sources.py laufen lassen)", file=sys.stderr)
        return 2

    files = [ln.strip() for ln in keep_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    per_file: dict[str, list[dict]] = {}
    for name in files:
        path = source_dir / name
        if not path.is_file():
            print(f"[WARN] fehlt: {path}", file=sys.stderr)
            continue
        per_file[name] = extract_file(path.read_text(encoding="utf-8", errors="ignore"))

    merged = merge(per_file)
    resolved = sum(1 for m in merged if m["path"])
    with_key = sum(1 for m in merged if m["key"])

    manifest = {
        "summary": {
            "files": len(per_file),
            "raw_leaves": sum(len(v) for v in per_file.values()),
            "unique_mappings": len(merged),
            "with_semantic_path": resolved,
            "with_key": with_key,
            "per_file": {k: len(v) for k, v in per_file.items()},
        },
        "mappings": merged,
    }

    out_path = rel(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    s = manifest["summary"]
    if not args.summary_only:
        print(f"globals.json geschrieben: {out_path}")
    print(
        f"files={s['files']} raw_leaves={s['raw_leaves']} unique={s['unique_mappings']} "
        f"with_path={s['with_semantic_path']} with_key={s['with_key']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
