#!/usr/bin/env python3
"""Version-folder resolution shared by the update tools.

Decompiled scripts and bytecode dumps are stored per game build:

    scripts/<build>/*.c                    (offset updater inputs)
    scrpatches/disasm/<build>/*.ysc.full   (scrpatches inputs)

where ``<build>`` is a label like ``1.73-3889``. Tools take ``--old``/``--new``
build specs; ``sort_key`` orders builds by their embedded numbers so ``previous``
and ``latest`` resolve automatically.
"""

from __future__ import annotations

import re
from pathlib import Path


def sort_key(name: str) -> tuple:
    nums = tuple(int(x) for x in re.findall(r"\d+", name))
    return nums or (0,)


def list_versions(root) -> list[str]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        (d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=sort_key,
    )


def resolve(root, spec: str | None) -> str:
    """Resolve a build spec ('1.73', '1.73-3889', 'latest', None) to a folder name."""
    root = Path(root)
    vers = list_versions(root)
    if not vers:
        raise SystemExit(
            f"no build folders in {root}/ -- fetch one first: ./fetch_update.sh <build>"
        )
    if spec in (None, "", "latest"):
        return vers[-1]
    if (root / spec).is_dir():
        return spec
    matches = [v for v in vers if v.startswith(spec)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches[-1]  # most recent build matching the prefix
    raise SystemExit(f"no build matches '{spec}' in {root}/ (have: {', '.join(vers)})")


def previous(root, new_name: str) -> str | None:
    """The highest build strictly below ``new_name`` (used as the --old default)."""
    k = sort_key(new_name)
    below = [v for v in list_versions(root) if sort_key(v) < k]
    return below[-1] if below else None
