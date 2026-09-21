#!/usr/bin/env python3
"""Normalise the two spellings the GTA V script decompiler produces.

The decompiled corpora this repository compares are not all produced by the
same build of the decompiler. The Legacy dumps come from calamity-inc, the
Enhanced ones from a newer decompiler, and the newer one prints the same code
differently. Three differences matter, because every resolver in the pipeline
matches on source text:

    Legacy                                  Enhanced
    Global_4718592.f_121958 == 6            *Global_4718592.f_128458 == 6
    StringCopy(&(Global_X.f_Y), ...)        TEXT_LABEL_ASSIGN_STRING(&Global_X.f_Y, ...)
    iVar2 = DATADICT_GET_DICT(iVar1, ...)   dict = DATADICT_GET_DICT(fileDict, ...)

None of these is a game difference. Left alone they make an Enhanced corpus look
as if every global had moved and every anchor had vanished, which is how a
migration ends up rewriting offsets that were correct.

Only the first is normalised here, because only it is a pure deletion. The
newer decompiler models a global array access as a pointer and writes the
dereference explicitly; ``*Global_N.f_M`` and ``Global_N.f_M`` denote the same
storage. Dropping the star restores the older spelling without touching any
other structure.

The other two are handled where they are matched (see ``verified_anchors``):
removing parentheses is not a safe blanket edit, and local variable names carry
no meaning the resolvers depend on.

``*`` is only removed when it sits directly against ``Global_``. A real
multiplication is printed with spaces on both sides (``a * Global_5``), so it
cannot be confused with a dereference. Every occurrence of the tight form in the
Enhanced corpus was verified to be a dereference.
"""
from __future__ import annotations

import pathlib
import re

# A dereference star glued to a global. Multiplication is always spaced.
_DEREF_STAR = re.compile(r"\*(?=Global_\d)")


def normalize_source(text: str) -> str:
    """One decompiler dialect from either."""
    return _DEREF_STAR.sub("", text)


def read_source(path: pathlib.Path) -> str:
    """Read a decompiled ``.c`` file in the normalised dialect."""
    return normalize_source(
        pathlib.Path(path).read_text(encoding="utf-8", errors="ignore"))


def read_source_lines(path: pathlib.Path) -> list:
    return read_source(path).splitlines()
