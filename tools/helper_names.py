#!/usr/bin/env python3
"""Namens-Fallback ueber Serialisierungs-Helper-Call-Sites.

Manche positions-serialisierten Felder bekommen ihren Namen nur ueber einen
Serialisierungs-Helper: ``func_N(<key>, <global>, …)`` mit arg0 = Key (Literal
ODER ``&label``/StringCopy-Variable) und arg1 = Global. Das ist versions-robust
(die konkreten ``func_``-Nummern sind egal).

Anders als der semantische Hauptpfad wird das hier NUR als Fallback fuer sonst
unaufloesbare Offsets genutzt und ausschliesslich mit EINDEUTIGEN Keys (ein Key,
der in einem Script-Satz auf mehr als einen Global zeigt, wird verworfen). So
entstehen keine Mehrdeutigkeiten, die den Hauptpfad stoeren wuerden.

Migration: alter Wert -> (eindeutiger) Key aus den ALTEN Scripts -> neuer Wert
aus den NEUEN Scripts. Reines Read-only-Werkzeug; liefert Wert oder ``None``.
"""
from __future__ import annotations

import pathlib
import re
from collections import defaultdict

from tools.extract_globals import (  # noqa: E402
    FUNC_CALL_LINE_RE,
    LabelState,
    canonicalize_global,
    resolve_key,
    split_args,
)

_GLOBAL = re.compile(r"\*?Global_\d+(?:\.f_\d+|\[[^\]]+\])+")


def _strip_iter_index(value: str) -> str:
    """Element-Iterations-Index (letzter variabler Index) nur bei >=2 Ebenen weg."""
    if value.count("[") >= 2:
        return re.sub(r"\[[ijklmnopq](?: /\*\d+\*/)?\]$", "", value)
    return value


def build_key_map(directory: str, keep: tuple = ()) -> dict[str, str]:
    """{key -> global} aus func_N(key, global, …)-Call-Sites; nur EINDEUTIGE Keys.

    ``keep`` (optional): nur diese Dateinamen scannen (Serialisierung passiert in
    den Creator-Scripts) — sonst der ganze Ordner. Das spart bei grossen alten
    Korpora viel Zeit.
    """
    if keep:
        files = [pathlib.Path(directory) / n for n in keep]
    else:
        files = sorted(pathlib.Path(directory).glob("*.c"))
    acc: dict[str, set] = defaultdict(set)
    for path in files:
        if not path.is_file():
            continue
        labels = LabelState()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            labels.feed(line)
            m = FUNC_CALL_LINE_RE.search(line)
            if not m or "Global_" not in m.group(2):
                continue
            args = split_args(m.group(2))
            if len(args) < 2:
                continue
            key, _ = resolve_key(args[0], labels)
            if key is None:
                continue
            gm = _GLOBAL.search(args[1])  # Wert = arg1
            if not gm:
                continue
            acc[key].add(_strip_iter_index(canonicalize_global(gm.group(0))))
    return {k: next(iter(s)) for k, s in acc.items() if len(s) == 1}


class HelperNameResolver:
    def __init__(self, old_dir: str, new_dir: str, keep: tuple = ()):
        self.new = build_key_map(str(new_dir), keep)             # key -> neuer Global
        old_map = build_key_map(str(old_dir), keep)              # key -> alter Global
        # alter Global -> Key; nur eindeutige Rueckrichtung behalten.
        rev: dict[str, set] = defaultdict(set)
        for k, g in old_map.items():
            rev[g].add(k)
        self.old_rev = {g: next(iter(ks)) for g, ks in rev.items() if len(ks) == 1}

    def migrate(self, value: str) -> str | None:
        key = self.old_rev.get(canonicalize_global(value))
        if key is not None:
            return self.new.get(key)
        return None


def migrate_value(value: str, old_dir: str, new_dir: str, keep: tuple = (), _cache: dict = {}) -> str | None:
    ck = (str(old_dir), str(new_dir), tuple(keep))
    r = _cache.get(ck)
    if r is None:
        r = _cache[ck] = HelperNameResolver(old_dir, new_dir, keep)
    return r.migrate(value)
