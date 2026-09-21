#!/usr/bin/env python3
"""Resolve tuneables offsets the semantic path misses.

``Global_4718592`` is the tuneables root. Its fields *are* serialized -- they go
through ``DATADICT_SET_*`` with a plain-text key like ``"head"`` -- so in
principle the ordinary semantic path handles them. In practice it often does
not, because that path matches on the *qualified* name (``meta.head``), and the
container a write belongs to is inferred by tracing a function parameter. When
the decompiler renames that parameter between builds (``uParam1`` in one,
``iParam1`` in the other) the trace lands somewhere else, the qualified name
stops matching, and the offset falls into ``path_removed_in_new``: the old path
is gone from the new build, the old value is kept, and nothing is reported.

Two resolvers here, both deliberately narrow. Each one is applied only to an
offset whose value did not change *and* whose field no longer exists in the new
corpus at all -- a field that is still present is left alone, because "still
there" is the best evidence available that it is still right.

**By key neighbourhood.** A serialization key is game content and survives a
build, and so do the keys written immediately before and after it. The triple
``(previous key, key, next key)`` identifies a field without knowing which dict
it ends up in. Only triples that are unique in *both* corpora are used.

**By run shift.** Fields that have no key at all still move in blocks: a
contiguous run of field numbers shifts as a unit. Two conditions must hold
before a shift is accepted -- the nearest successfully migrated offsets above
and below bracket the possible range, and the whole run has to land on a run of
exactly the same length in the new corpus, made up entirely of fields that did
not exist in the old one. Where more than one shift satisfies that, nothing is
written.

The two agree wherever both apply, which is what makes them worth having
together: they share no inputs beyond the corpora themselves.
"""
from __future__ import annotations

import bisect
import collections
import pathlib
import re

from tools.dialect import read_source

ROOT_GLOBAL = "Global_4718592"

_ANY_VALUE = r"(?:Global_\d+)(?:\.f_\d+)+"

# Three ways a script writes a named value into a data file, all of which carry
# the key as a literal:
#
#   DATADICT_SET_INT(*dict, "head", Global_4718592.f_124916);
#   func_622("iplop2", &(Global_4718592.f_183569), dict, 0);
#   StringCopy(&scratch, "tmrph", 8);
#   StringIntConCat(&scratch, i, 8);
#   func_285(&scratch, &(Global_4718592.f_188047[i /*16*/]), dict);
#
# Only the first was matched originally, which is why a third of the tuneables
# looked keyless. The second hands the key to a helper; the third builds a
# per-index name from a literal prefix, and that prefix is the key.
_SET_CALL = re.compile(
    rf'DATADICT_SET_\w+\([^,]+,\s*"(\w+)",\s*({re.escape(ROOT_GLOBAL)}\.f_(\d+))\s*\)')
_HELPER_CALL = re.compile(rf'func_\d+\(\s*"(\w+)",\s*&\(?({_ANY_VALUE})\)?')
_BUILT_KEY = re.compile(
    rf'(?:StringCopy|TEXT_LABEL_ASSIGN_STRING)\(&(\w+),\s*"(\w+)",\s*\d+\);'
    rf'(?:[^;]{{0,120}};){{0,3}}?\s*'
    rf'func_\d+\(&\1,\s*&\(?({_ANY_VALUE})')
_FIELD = re.compile(rf"{re.escape(ROOT_GLOBAL)}\.f_(\d+)\b")
_PLAIN_VALUE = re.compile(rf"^{re.escape(ROOT_GLOBAL)}\.f_(\d+)$")


def _serialized_sequence(directory: pathlib.Path, merged: bool) -> list:
    """``(key, value)`` for named writes, in source order.

    Two views of the same code. ``merged=False`` sees only the direct
    DATADICT_SET_* writes; ``merged=True`` also weaves in the helper and
    built-key forms. Neither is strictly better: adding events gives a generic
    key like "chp" the neighbours that identify it, and takes away the ones that
    identified "cordmbs". Both are built, and a value is only used where the two
    views agree."""
    out = []
    for path in sorted(directory.glob("*.c")):
        text = read_source(path)
        events = [(m.start(), m.group(1), m.group(2)) for m in _SET_CALL.finditer(text)]
        if merged:
            events += [(m.start(), m.group(1), m.group(2))
                       for m in _HELPER_CALL.finditer(text)]
            events += [(m.start(), m.group(2), m.group(3))
                       for m in _BUILT_KEY.finditer(text)]
        out += [(k, g) for _, k, g in sorted(events)]
    return out


def _unique_neighbourhoods(sequence: list) -> dict:
    """``(prev key, key, next key) -> value``, only where that triple is unique."""
    seen = collections.defaultdict(set)
    for i, (key, value) in enumerate(sequence):
        prev = sequence[i - 1][0] if i else None
        nxt = sequence[i + 1][0] if i + 1 < len(sequence) else None
        seen[(prev, key, nxt)].add(value)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def _literal_keys(directory: pathlib.Path) -> dict:
    """``{key: value}`` for keys that name exactly one value in this corpus.

    These keys are specific enough to stand on their own -- unlike the generic
    ones ("head", "type") that the neighbourhood map exists for."""
    seen = collections.defaultdict(set)
    for path in sorted(directory.glob("*.c")):
        text = read_source(path)
        for m in _HELPER_CALL.finditer(text):
            seen[m.group(1)].add(m.group(2))
        for m in _BUILT_KEY.finditer(text):
            seen[m.group(2)].add(m.group(3))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def key_map(old_dir: pathlib.Path, new_dir: pathlib.Path) -> dict:
    """``{old value: new value}``, from literal keys and from neighbourhoods."""
    out = {}
    old_lit, new_lit = _literal_keys(old_dir), _literal_keys(new_dir)
    for k in set(old_lit) & set(new_lit):
        out[old_lit[k]] = new_lit[k]
    views = []
    for merged in (False, True):
        old_n = _unique_neighbourhoods(_serialized_sequence(old_dir, merged))
        new_n = _unique_neighbourhoods(_serialized_sequence(new_dir, merged))
        views.append({old_n[k]: new_n[k] for k in set(old_n) & set(new_n)})
    answers = collections.defaultdict(set)
    for view in views:
        for old_value, new_value in view.items():
            answers[old_value].add(new_value)
    for old_value, candidates in answers.items():
        if len(candidates) == 1:          # both views, or only one, and no conflict
            out.setdefault(old_value, next(iter(candidates)))
    return out


def field_counts(directory: pathlib.Path) -> dict:
    """``{field number: how often it appears}`` under the tuneables root."""
    counts = collections.Counter()
    for path in sorted(directory.glob("*.c")):
        counts.update(int(m.group(1)) for m in _FIELD.finditer(read_source(path)))
    return dict(counts)


def _run_around(field: int, present: dict) -> tuple | None:
    """The maximal run of consecutive present fields containing ``field``."""
    if field not in present:
        return None
    lo = hi = field
    while lo - 1 in present:
        lo -= 1
    while hi + 1 in present:
        hi += 1
    return lo, hi


def run_shift(field: int, old_counts: dict, new_counts: dict, anchors: list) -> int | None:
    """The one shift that moves this field's whole run onto a new run, or None.

    ``anchors`` are ``(old field, new field)`` pairs that were migrated by other
    means; they bound the search. Without a bracket on both sides there is
    nothing to constrain the shift, and nothing is returned."""
    run = _run_around(field, old_counts)
    if not run:
        return None
    lo, hi = run
    xs = [a for a, _ in anchors]
    by_old = dict(anchors)
    i = bisect.bisect_left(xs, field)
    if i == 0 or i >= len(xs):
        return None
    below, above = xs[i - 1], xs[i]
    lo_delta, hi_delta = by_old[below] - below, by_old[above] - above

    found = []
    for delta in range(min(lo_delta, hi_delta), max(lo_delta, hi_delta) + 1):
        start, end = lo + delta, hi + delta
        # The shifted run must be a *maximal* run in the new corpus...
        if (start - 1) in new_counts or (end + 1) in new_counts:
            continue
        # ...made up entirely of fields that are new.
        if any(f not in new_counts or f in old_counts for f in range(start, end + 1)):
            continue
        found.append(delta)
        if len(found) > 1:
            return None      # ambiguous; a guess here ships a wrong address
    return found[0] if found else None


_BASE_VALUE = re.compile(rf"^({_ANY_VALUE.replace('(?:', '(?:')})(?:\[.*)?$")
# The first .f_N level of any global value -- the part a rename moves.
_ANY_BASE = re.compile(r"^(Global_\d+\.f_\d+)(\..*)?$")


def base_values(directory: pathlib.Path) -> set:
    """Every ``Global_N.f_M`` that occurs in a corpus, base level only."""
    seen = set()
    for path in sorted(directory.glob("*.c")):
        seen.update(m.group(0) for m in
                    re.finditer(r"Global_\d+\.f_\d+", read_source(path)))
    return seen


def _resolved_bases(before: dict, after: dict) -> dict:
    """``{old base: new base}`` from offsets that did migrate.

    A base is only usable when every offset carrying it agrees on where it
    went. Two different answers mean the base is not actually identified, and
    re-basing on either would be a coin flip."""
    votes = collections.defaultdict(set)
    for name, old_value in before.items():
        new_value = after.get(name)
        if not new_value or new_value == old_value:
            continue
        mo, mn = _ANY_BASE.match(old_value), _ANY_BASE.match(new_value)
        if not (mo and mn):
            continue
        # Only when the suffix is untouched does the pair say something about
        # the base alone.
        if (mo.group(2) or "") != (mn.group(2) or ""):
            continue
        votes[mo.group(1)].add(mn.group(1))
    return {k: next(iter(v)) for k, v in votes.items() if len(v) == 1}


def resolve(ini_text: str, migrated_text: str,
            old_dir: pathlib.Path, new_dir: pathlib.Path) -> tuple:
    """``({offset: new value}, notes)`` for stale tuneables offsets.

    ``migrated_text`` supplies both the offsets still to fix (those whose value
    did not change) and the anchors used to bracket a run shift (those whose
    value did)."""
    value_re = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]*)"', re.MULTILINE)
    before = {m.group(1): m.group(2) for m in value_re.finditer(ini_text)}
    after = {m.group(1): m.group(2) for m in value_re.finditer(migrated_text)}

    old_counts, new_counts = field_counts(old_dir), field_counts(new_dir)
    new_bases = base_values(new_dir)

    anchors = []
    for name, old_value in before.items():
        new_value = after.get(name)
        if not new_value or new_value == old_value:
            continue
        mo, mn = _PLAIN_VALUE.match(old_value), _PLAIN_VALUE.match(new_value)
        if mo and mn:
            anchors.append((int(mo.group(1)), int(mn.group(1))))
    anchors.sort()

    by_key = key_map(old_dir, new_dir)

    fixes, notes = {}, []
    # Two passes. The first resolves whole values by key or by run shift; the
    # second re-bases sub-fields onto a base the first pass just pinned down.
    # A base fixed here is as good an anchor as one the main migration found,
    # and most of the leftovers are sub-fields of exactly those.
    for _pass in (1, 2):
        settled = dict(after)
        settled.update(fixes)
        by_base = _resolved_bases(before, settled)
        _resolve_pass(before, after, fixes, notes, by_key, by_base,
                      old_counts, new_counts, new_bases, anchors,
                      base_only=(_pass == 2))
    return fixes, notes


def _resolve_pass(before, after, fixes, notes, by_key, by_base,
                  old_counts, new_counts, new_bases, anchors, base_only):
    for name, old_value in before.items():
        if name in fixes:
            continue
        if after.get(name) != old_value:
            continue                      # already migrated
        m = _ANY_BASE.match(old_value)
        if not m:
            continue
        if m.group(1) in new_bases:
            continue                      # still exists; leave it alone
        field = (int(m.group(1).rsplit("_", 1)[1])
                 if m.group(1).startswith(ROOT_GLOBAL + ".") else None)
        candidate = None if base_only else by_key.get(old_value)
        how = "key"
        if not candidate and not base_only and field is not None:
            delta = run_shift(field, old_counts, new_counts, anchors)
            if delta is not None:
                candidate = f"{ROOT_GLOBAL}.f_{field + delta}"
                how = "run"
        if not candidate:
            # Sub-fields of a base another offset already pinned down: the
            # suffix is a struct layout, which the base carries with it.
            mb = _ANY_BASE.match(old_value)
            new_base = by_base.get(mb.group(1)) if mb else None
            if not new_base:
                continue
            candidate = new_base + (mb.group(2) or "")
            how = "base"
        if candidate != old_value:
            fixes[name] = candidate
            notes.append((name, old_value, candidate, how))
