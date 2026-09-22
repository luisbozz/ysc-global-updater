#!/usr/bin/env python3
"""Turn script patches on and off in halves, to find the one that breaks things.

A patch writes into the running script's bytecode. When one of them is wrong the
symptom shows up somewhere else entirely -- a creator that will not load, values
that read back as nonsense -- with nothing pointing back at the cause. Bisection
is the shortest way there: disable half, test, and the half that still fails
holds the culprit.

Patches already parked with ``enabled: false`` stay out of the pool; they are
not running, so they cannot be the cause and halving them wastes rounds.

    python3 tools/bisect_patches.py --list
    python3 tools/bisect_patches.py --disable 1-19      # first half off
    python3 tools/bisect_patches.py --disable 1-9       # narrow
    python3 tools/bisect_patches.py --reset             # all back on

Writes the file the app compiles in, so a rebuild is needed for each round --
or use Mod -> Scr Patches in the app, which toggles the same flags live.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import tempfile

DEFAULT_TARGET = "/opt/Xenvious/Xenvious/OfflineData/legacy/scrpatches.json"


def load(path: pathlib.Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: pathlib.Path, patches: list) -> None:
    path.write_text(json.dumps(patches, indent=4) + "\n", encoding="utf-8")


def key_of(patch: dict) -> str:
    return f"{patch.get('script_name', '?')}\u0000{patch.get('patch_name', '?')}"


def baseline_path(target: pathlib.Path) -> pathlib.Path:
    """Where the pre-bisect state is remembered.

    Deliberately not next to the target: OfflineData is compiled into the exe
    file by file, and a stray file there is an unregistered embedded resource
    that trips the build checks."""
    return pathlib.Path(tempfile.gettempdir()) / (
        "bisect-baseline-" + hashlib.sha1(
            str(target.resolve()).encode()).hexdigest()[:12] + ".json")


def baseline(target: pathlib.Path, patches: list) -> dict:
    """What was enabled before any bisecting started.

    Written once, next to the file. Without it the first round's own changes
    become the new "normal", and a later reset would switch on patches that
    were deliberately parked -- exactly the ones known to be broken."""
    path = baseline_path(target)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    state = {key_of(p): bool(p.get("enabled", True)) for p in patches}
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


# The customfuncs patches of one script depend on each other, but only in one
# direction. One of them writes a whole function body into the script (its
# payload opens with ENTER); the others rewrite call sites to use it. A call
# site pointed at a function that was never injected crashes the game -- that
# is what happened here. The injection on its own is harmless: a function
# nobody calls does nothing.
#
# So they are not one block. The provider can be switched alone, and the
# dependents are only offered while it is on.
GROUPED_CATEGORY = "customfuncs"
ENTER_OPCODE = "2D"


def is_provider(patch: dict) -> bool:
    """Whether this patch injects a function body rather than redirecting to one."""
    if patch.get("category") != GROUPED_CATEGORY:
        return False
    first = (patch.get("bytes_to_patch") or "").split()
    return bool(first) and first[0].upper() == ENTER_OPCODE


def provider_for(patches: list, patch: dict):
    """The injection a dependent needs, or None."""
    if patch.get("category") != GROUPED_CATEGORY or is_provider(patch):
        return None
    for other in patches:
        if (is_provider(other)
                and other.get("script_name") == patch.get("script_name")):
            return other
    return None


def pool(patches: list, base: dict) -> list:
    """Indices of patches worth halving, in a stable order.

    Only the ones that were running to begin with: a parked patch is not being
    applied, so it cannot be the cause, and including it wastes a round.
    Sorted by script then name so a number means the same thing between runs --
    the order inside the file is not stable across a regeneration."""
    live = [i for i, p in enumerate(patches) if base.get(key_of(p), True)]
    live.sort(key=lambda i: (patches[i].get("script_name") or "",
                             patches[i].get("patch_name") or ""))
    return [[i] for i in live]


def parse_range(spec: str, size: int) -> set:
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    bad = [n for n in out if not 1 <= n <= size]
    if bad:
        raise SystemExit(f"out of range 1..{size}: {sorted(bad)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--list", action="store_true", help="Show the numbered pool.")
    ap.add_argument("--disable", help="Numbers to switch off, e.g. 1-19 or 3,7,12.")
    ap.add_argument("--enable", help="Numbers to switch on; every other candidate "
                                     "goes off. The inverse of --disable, for "
                                     "adding suspects back one at a time.")
    ap.add_argument("--reset", action="store_true",
                    help="Switch every candidate back on.")
    ap.add_argument("--only", help="Restrict the listing to a script name.")
    args = ap.parse_args()

    path = pathlib.Path(args.target)
    patches = load(path)
    base = baseline(path, patches)
    order = pool(patches, base)
    parked = [p for p in patches if not base.get(key_of(p), True)]
    grouped = sum(1 for g in order if provider_for(patches, patches[g[0]]) is not None)

    if args.list or not (args.disable or args.enable or args.reset):
        print(f"\n  {path}")
        print(f"  {len(order)} switch(es), {grouped} need an injection first, "
              f"{len(parked)} parked and left alone\n")
        for n, group in enumerate(order, 1):
            p = patches[group[0]]
            if args.only and args.only not in (p.get("script_name") or ""):
                continue
            state = "on " if p.get("enabled", True) else "OFF"
            if is_provider(p):
                suffix = "  [Injektion]"
            elif provider_for(patches, p) is not None:
                suffix = "  [braucht Injektion]"
            else:
                suffix = ""
            print(f"   {n:3d}  [{state}]  {p.get('patch_name', '?')[:44]:44s} "
                  f"{p.get('script_name', '?')}{suffix}")
        print()
        return 0

    if args.reset:
        off = set()
    elif args.enable:
        on = parse_range(args.enable, len(order))
        off = {n for n in range(1, len(order) + 1) if n not in on}
    else:
        off = parse_range(args.disable, len(order))
    for n, group in enumerate(order, 1):
        for i in group:
            patches[i]["enabled"] = n not in off

    # A call site rewritten to reach a function that was never injected crashes
    # the game, so that combination is never written out.
    forced = []
    for p in patches:
        if not p.get("enabled", True):
            continue
        provider = provider_for(patches, p)
        if provider is not None and not provider.get("enabled", True):
            p["enabled"] = False
            forced.append(p.get("patch_name", "?"))
    # Never resurrect something that was parked before this started.
    for p in patches:
        if not base.get(key_of(p), True):
            p["enabled"] = False
    save(path, patches)

    on_now = [n for n in range(1, len(order) + 1) if n not in off]
    print(f"\n  {path}")
    print(f"  on: {len(on_now)} of {len(order)}   off: {len(off)}")
    # Print whichever list is shorter; that is the one worth reading.
    if len(on_now) <= len(off):
        print("  enabled:")
        rows = on_now
    else:
        print("  disabled:")
        rows = sorted(off)
    for n in rows:
        group = order[n - 1]
        p = patches[group[0]]
        print(f"   {n:3d}  {p.get('patch_name', '?')[:44]:44s} "
              f"{p.get('script_name', '?')}")
    if forced:
        print(f"\n  auto-off, needs its injection: {', '.join(sorted(set(forced)))}")
    print("\n  rebuild, then test\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
