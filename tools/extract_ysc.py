#!/usr/bin/env python3
"""Extract decrypted script dumps straight from an installed GTA V.

The scrpatch toolchain needs ``*.ysc.full`` -- a script as it sits in memory:
decrypted, decompressed, page pointers intact. calamity-inc publishes those for
Legacy. Nobody publishes them for Enhanced, which is why the Enhanced patches
could not be checked at all.

They do not have to be published. A local game install has them, behind the
RPF archive encryption, and CodeWalker's library can open that. Extracting a
``.ysc`` resource yields exactly the layout ``scrasm.yscfull`` parses: the RSC7
container header is gone, so ``RSC7Offset == 0``, which is the same shape a
published ``.full`` has.

Requirements, all on the Windows side:

  * an installed GTA V (Legacy or Enhanced)
  * CodeWalker.Core.dll built from source -- the public master supports
    Enhanced ("gen9"), older released binaries do not:

        curl -sfL -o cw.tar.gz \\
            https://codeload.github.com/dexyfex/CodeWalker/tar.gz/refs/heads/master
        tar xzf cw.tar.gz
        dotnet build CodeWalker-master/CodeWalker.Core/CodeWalker.Core.csproj -c Release

This runs from WSL and drives Windows PowerShell, so paths are translated with
``wslpath``. It writes nothing outside the output directory.

    python3 tools/extract_ysc.py --variant enhanced --build 1.73-1158 \\
        --game 'E:/Grand Theft Auto V Enhanced' \\
        --codewalker 'C:/temp/CodeWalker/CodeWalker.Core/bin/Release/netstandard2.0/CodeWalker.Core.dll'
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import versions  # noqa: E402

# The six scripts the scrpatch toolchain patches at runtime.
DUMP_SCRIPTS = (
    "fm_capture_creator", "fm_deathmatch_creator", "fm_lts_creator",
    "fm_race_creator", "fm_survival_creator", "fmmc_launcher",
)
# The two extra ones the offset side reads, for a build the upstream has not
# published yet. These still need decompiling before the offset tools can use
# them; the dumps alone are not .c files.
EXTRA_SCRIPTS = ("public_mission_creator", "tuneables_processing")

# Where the scripts live inside the archives. Enhanced moved them into
# update2.rpf and renamed the inner archive.
ARCHIVES = ("update/update2.rpf", "update/update.rpf")

PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
[Reflection.Assembly]::LoadFrom('{dll}') | Out-Null
$game = '{game}'
$out  = '{out}'
[CodeWalker.GameFiles.GTA5Keys]::LoadFromPath($game, ${gen9}, $null)
$noop = [Action[string]]{{ param($s) }}
$want = @({want})

function Find-ScriptArchive($rpf) {{
    if (@($rpf.AllEntries | Where-Object {{ $_.Name -like '*.ysc' }}).Count -gt 0) {{ return $rpf }}
    foreach ($c in $rpf.Children) {{
        $r = Find-ScriptArchive $c
        if ($r) {{ return $r }}
    }}
    return $null
}}

$archive = $null
foreach ($rel in @({archives})) {{
    $full = Join-Path $game $rel
    if (-not (Test-Path $full)) {{ continue }}
    $rpf = New-Object CodeWalker.GameFiles.RpfFile($full, $rel)
    $rpf.ScanStructure($noop, $noop)
    $archive = Find-ScriptArchive $rpf
    if ($archive) {{ Write-Output "ARCHIVE $($archive.Path)"; break }}
}}
if (-not $archive) {{ Write-Output "ERROR no archive with .ysc entries found"; exit 1 }}

foreach ($n in $want) {{
    $e = $archive.AllEntries | Where-Object {{ $_.Name -eq "$n.ysc" }} | Select-Object -First 1
    if (-not $e) {{ Write-Output "MISSING $n"; continue }}
    $data = $archive.ExtractFile($e)
    if ($null -eq $data) {{ Write-Output "EMPTY $n"; continue }}
    [IO.File]::WriteAllBytes((Join-Path $out "$n.ysc"), $data)
    Write-Output "OK $n $($data.Length)"
}}
"""


def win_path(p: str) -> str:
    """A path PowerShell understands, whether given as WSL or Windows."""
    text = str(p)
    if ":" in text[:3] or text.startswith("\\\\"):
        return text.replace("/", "\\")
    return subprocess.run(["wslpath", "-w", text], capture_output=True,
                          text=True, check=True).stdout.strip()


def ps_list(items) -> str:
    return ", ".join("'" + str(i).replace("'", "''") + "'" for i in items)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=(versions.LEGACY, versions.ENHANCED),
                    default=versions.ENHANCED)
    ap.add_argument("--build", required=True,
                    help="Build label without the variant prefix, e.g. 1.73-1158.")
    ap.add_argument("--game", required=True, help="GTA V install folder.")
    ap.add_argument("--codewalker", required=True,
                    help="Path to a CodeWalker.Core.dll built from master.")
    ap.add_argument("--out", help="Output directory. Default: "
                                  "scrpatches/disasm/<label>/")
    ap.add_argument("--extra", action="store_true",
                    help="Also extract the two scripts only the offset side reads.")
    ap.add_argument("--keep-ysc", action="store_true",
                    help="Keep the raw .ysc next to the .ysc.full (for decompiling).")
    args = ap.parse_args()

    label = versions.label(args.variant, args.build)
    out_dir = pathlib.Path(args.out) if args.out else ROOT / "scrpatches" / "disasm" / label
    out_dir.mkdir(parents=True, exist_ok=True)

    scripts = list(DUMP_SCRIPTS) + (list(EXTRA_SCRIPTS) if args.extra else [])

    with tempfile.TemporaryDirectory(dir=str(out_dir)) as staging:
        script = PS_TEMPLATE.format(
            dll=win_path(args.codewalker),
            game=win_path(args.game),
            out=win_path(staging),
            gen9="true" if args.variant == versions.ENHANCED else "false",
            want=ps_list(scripts),
            archives=ps_list(ARCHIVES),
        )
        ps_file = pathlib.Path(staging) / "extract.ps1"
        ps_file.write_text(script, encoding="utf-8")

        print(f"  {label}  <-  {args.game}")
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", win_path(ps_file)],
            capture_output=True, text=True)
        if res.returncode != 0 and not res.stdout.strip():
            print(res.stderr.strip() or "powershell failed", file=sys.stderr)
            return 1

        extracted = 0
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("ARCHIVE "):
                print(f"  archive: {line[8:]}")
            elif line.startswith("OK "):
                _, name, size = line.split()
                src = pathlib.Path(staging) / f"{name}.ysc"
                shutil.copyfile(src, out_dir / f"{name}.ysc.full")
                if args.keep_ysc:
                    shutil.copyfile(src, out_dir / f"{name}.ysc")
                print(f"    {name:26s} {int(size):>10,} bytes")
                extracted += 1
            elif line:
                print(f"    {line}", file=sys.stderr)

    if not extracted:
        print("nothing extracted", file=sys.stderr)
        return 1
    print(f"\n  {extracted} dump(s) -> {out_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
