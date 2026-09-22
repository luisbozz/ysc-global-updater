#!/usr/bin/env python3
"""Decompile exported ``.ysc`` scripts into the versioned ``scripts/<build>/``.

Needed when calamity-inc has not published your build yet, and for Enhanced,
which they do not publish at all.

Why this exists rather than "just run the decompiler": the migration compares
the old build's decompiled source against the new one, line by line and pattern
by pattern. If the two corpora were produced with different decompiler settings
they differ everywhere for no real reason, and the migration's output is
garbage. The settings are therefore pinned here to calamity-inc's defaults --
the ones their published corpus was built with -- and written out fresh before
every run, so a stale ``config.ini`` next to the decompiler cannot quietly
change the dialect.

The decompiler takes one file per invocation, writes ``<input>.c`` beside the
input, and has no command line options at all; everything is read from
``config.ini``. See ``Program.cs`` in calamity-inc/GTA-V-Script-Decompiler.

    python3 tools/decompile_ysc.py --build enhanced-1.73-1158 \\
        --src enhanced-scripts/raw --decompiler /mnt/c/tools/Decompiler.exe

By default it only decompiles the scripts the toolchain actually reads -- the
creators plus the launcher and tuneables -- because the full corpus is 1154
files and several hours. ``--all`` does everything.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import versions  # noqa: E402

# calamity-inc's defaults, from Program.cs. The published corpus is built with
# these; anything else produces a source dialect the migration cannot compare.
CONFIG = """[Base]
IntStyle=int
Show_Array_Size=True
Reverse_Hashes=True
Declare_Variables=True
Shift_Variables=True
Show_Func_Pointer=False
Use_MultiThreading=False
Include_Function_Position=False
Uppercase_Natives=False
Hex_Index=False

[View]
Show_Nat_Namespace=True
Line_Numbers=True
"""

# What the offset migration and the scrpatch tooling actually read.
WANTED = [
    "fm_capture_creator", "fm_deathmatch_creator", "fm_lts_creator",
    "fm_race_creator", "fm_survival_creator", "fmmc_launcher",
    "public_mission_creator", "tuneables_processing",
]


def win_path(p: pathlib.Path) -> str:
    return subprocess.run(["wslpath", "-w", str(p)],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", required=True,
                    help="Build label, e.g. 1.74-4012 or enhanced-1.73-1200.")
    ap.add_argument("--src", required=True,
                    help="Folder of exported .ysc (or .ysc.full) files.")
    ap.add_argument("--decompiler", required=True,
                    help="Path to the built Decompiler.exe (Windows side).")
    ap.add_argument("--all", action="store_true",
                    help="Decompile every script, not just the ones we read.")
    ap.add_argument("--force", action="store_true",
                    help="Redo scripts whose .c already exists.")
    args = ap.parse_args()

    src = pathlib.Path(args.src)
    if not src.is_absolute():
        src = ROOT / src
    exe = pathlib.Path(args.decompiler)
    if not src.is_dir():
        raise SystemExit(f"no such folder: {src}")
    if not exe.is_file():
        raise SystemExit(f"no decompiler at {exe}")

    out = ROOT / "scripts" / args.build
    out.mkdir(parents=True, exist_ok=True)

    # Pin the dialect. A config.ini left over from someone's GUI session is the
    # difference between a corpus that diffs cleanly and one that does not.
    cfg = exe.parent / "config.ini"
    if cfg.is_file() and cfg.read_text() != CONFIG:
        backup = cfg.with_suffix(".ini.bak")
        shutil.copy(cfg, backup)
        print(f"[config] existing config.ini differed -- backed up to {backup.name}")
    cfg.write_text(CONFIG)
    print(f"[config] pinned calamity-inc defaults in {cfg}")

    names = sorted({p.name.split(".")[0] for p in src.glob("*.ysc*")})
    if not args.all:
        missing = [n for n in WANTED if n not in names]
        if missing:
            print(f"[warn] not in {src}: {', '.join(missing)}")
        names = [n for n in WANTED if n in names]
    print(f"[scripts] {len(names)} to do -> {out.relative_to(ROOT)}/\n")

    done, skipped, failed = 0, 0, []
    for n in names:
        target = out / f"{n}.c"
        if target.exists() and not args.force:
            skipped += 1
            continue
        source = next((src / f"{n}{e}" for e in (".ysc.full", ".ysc")
                       if (src / f"{n}{e}").is_file()), None)
        if source is None:
            failed.append((n, "no .ysc or .ysc.full"))
            continue
        res = subprocess.run([str(exe), win_path(source)],
                             capture_output=True, text=True)
        produced = source.with_name(source.name + ".c")
        if res.returncode != 0 or not produced.is_file():
            failed.append((n, (res.stdout + res.stderr).strip().splitlines()[-1:] or ["no output"]))
            continue
        shutil.move(str(produced), target)
        # The decompiler drops a native table beside each input; the toolchain
        # reads its own tables out of the .ysc.full, so this is just litter.
        table = source.with_name(source.name + " native table.txt")
        table.unlink(missing_ok=True)
        done += 1
        print(f"  ok   {n}")

    print(f"\n{done} decompiled, {skipped} already present, {len(failed)} failed")
    for n, why in failed:
        print(f"  !! {n}: {why}")
    if done or skipped:
        print(f"\nNext: python3 update_xenvious.py --new {args.build}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
