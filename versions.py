#!/usr/bin/env python3
"""Version-folder resolution shared by the update tools.

Decompiled scripts and bytecode dumps are stored per game build:

    scripts/<build>/*.c                    (offset updater inputs)
    scrpatches/disasm/<build>/*.ysc.full   (scrpatches inputs)

where ``<build>`` is a label like ``1.73-3889``. Tools take ``--old``/``--new``
build specs; ``sort_key`` orders builds by their embedded numbers so ``previous``
and ``latest`` resolve automatically.

Since GTA V ships as two separate games, a build label also carries which one it
belongs to. Enhanced labels are prefixed (``enhanced-1.73-1158``); an unprefixed
label is Legacy, so every label written before the split keeps its meaning.

The two games are numbered independently: Enhanced build 1158 is newer than
Legacy build 3889 despite the smaller number. Ordering them against each other
is meaningless, so ``list_versions`` takes a ``variant`` and every caller that
compares builds passes one. Without it, ``previous('enhanced-1.73-1158')`` would
happily return a Legacy build and the migration would compare two different
games.
"""

from __future__ import annotations

import re
from pathlib import Path

LEGACY = "legacy"
ENHANCED = "enhanced"
VARIANTS = (LEGACY, ENHANCED)

# Enhanced labels carry this prefix; anything else is Legacy.
ENHANCED_PREFIX = "enhanced-"


def variant_of(name: str) -> str:
    """Which game a build label belongs to."""
    return ENHANCED if name.startswith(ENHANCED_PREFIX) else LEGACY


def label(variant: str, build: str) -> str:
    """Build a folder label for a variant, idempotently."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r} (have: {', '.join(VARIANTS)})")
    if variant == LEGACY:
        return build
    return build if build.startswith(ENHANCED_PREFIX) else ENHANCED_PREFIX + build


def sort_key(name: str) -> tuple:
    nums = tuple(int(x) for x in re.findall(r"\d+", name))
    return nums or (0,)


def list_versions(root, variant: str | None = None) -> list[str]:
    """Build folders under ``root``, oldest first.

    ``variant`` keeps the two games apart. ``None`` lists everything, which is
    only useful for reporting -- never for picking a build to compare against."""
    root = Path(root)
    if not root.is_dir():
        return []
    names = (d.name for d in root.iterdir()
             if d.is_dir() and not d.name.startswith("."))
    if variant is not None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}")
        names = (n for n in names if variant_of(n) == variant)
    return sorted(names, key=sort_key)


def resolve(root, spec: str | None, variant: str | None = None) -> str:
    """Resolve a build spec ('1.73', '1.73-3889', 'latest', None) to a folder name.

    A spec is matched inside its own variant. ``enhanced-1.73`` names the variant
    itself; a bare ``1.73`` means Legacy unless ``variant`` says otherwise."""
    root = Path(root)
    if spec and variant is None:
        variant = variant_of(spec)
    vers = list_versions(root, variant)
    if not vers:
        where = f" for {variant}" if variant else ""
        raise SystemExit(
            f"no build folders{where} in {root}/ -- fetch one first: "
            f"./fetch_update.sh <build>"
        )
    if spec in (None, "", "latest"):
        return vers[-1]
    if (root / spec).is_dir():
        return spec
    # A spec may be given with or without the variant prefix: 'enhanced-1.73'
    # and '1.73' both address an Enhanced build once the variant is known.
    stems = {spec}
    if variant == ENHANCED:
        stems.add(label(ENHANCED, spec))
        if spec.startswith(ENHANCED_PREFIX):
            stems.add(spec[len(ENHANCED_PREFIX):])
    matches = [v for v in vers if any(v.startswith(s) for s in stems)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches[-1]  # most recent build matching the prefix
    raise SystemExit(f"no build matches '{spec}' in {root}/ (have: {', '.join(vers)})")


def previous(root, new_name: str) -> str | None:
    """The highest build strictly below ``new_name``, within the same variant.

    Used as the ``--old`` default. Staying inside the variant matters: the two
    games' build numbers are unrelated, so the nearest number across the split
    is not the nearest build."""
    k = sort_key(new_name)
    below = [v for v in list_versions(root, variant_of(new_name)) if sort_key(v) < k]
    return below[-1] if below else None
