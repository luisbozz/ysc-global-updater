#!/usr/bin/env python3
"""Check the [AOB] byte patterns against a running game.

Offsets and byte patterns are two different problems that the rest of this
repository does not mix, and this tool covers the second one.

An offset names a script global. The migration tools resolve those from the
decompiled scripts, and a wrong one is usually loud. A byte pattern names a
place in the *executable*, which the scripts say nothing about. Patterns break
whenever Rockstar rebuilds the game, and they break quietly: a pattern that
matches nothing makes the scan return zero, and the caller computes a pointer
from address zero -- a plausible-looking value that points nowhere.

The executable on disk is packed, so scanning the file finds nothing at all.
The code only exists in the running process, which is why this dumps the main
module out of the live game and scans that.

    python3 tools/check_aob.py --ini /opt/Xenvious/Xenvious/OfflineData/legacy/offsets.ini

Runs from WSL, drives Windows PowerShell. The game has to be running.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import versions  # noqa: E402

PROCESS = {versions.LEGACY: "GTA5", versions.ENHANCED: "GTA5_Enhanced"}

# Patterns the shipped C# never scans. Kept as a list rather than deleted from
# the tool: if one reappears in an ini, saying why it does nothing beats
# silently reporting it as broken and sending someone to re-derive it.
UNUSED = {}

# Patterns that legitimately match more than once. The scan takes the lowest
# address, and for these that is the right site.
AMBIGUOUS_OK = {
    "versionptr": "three identical call sites; the lowest holds the version string",
}

PS_DUMP = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class M {{
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(int a, bool i, int p);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadProcessMemory(IntPtr h, IntPtr a, byte[] b, int s, out IntPtr r);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
  public static byte[] Dump(int pid, IntPtr b, int size) {{
    IntPtr h = OpenProcess(0x0010 | 0x0400, false, pid);
    if (h == IntPtr.Zero) throw new Exception("OpenProcess failed: " + Marshal.GetLastWin32Error());
    byte[] buf = new byte[size]; IntPtr read;
    ReadProcessMemory(h, b, buf, size, out read); CloseHandle(h); return buf;
  }}
}}
"@ -Language CSharp
$p = Get-Process -Name '{process}' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) {{ Write-Output 'NOPROC'; exit 1 }}
$m = $p.MainModule
[IO.File]::WriteAllBytes('{out}', [M]::Dump($p.Id, $m.BaseAddress, $m.ModuleMemorySize))
Write-Output ("BASE 0x{{0:X}} SIZE {{1}}" -f $m.BaseAddress.ToInt64(), $m.ModuleMemorySize)
"""


def aob_section(text: str) -> dict:
    out, inside = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = s == "[AOB]"
            continue
        if not inside:
            continue
        m = re.match(r'^([A-Za-z0-9_]+)\s*=\s*(["\'])(.*)\2\s*$', s)
        if m:
            out[m.group(1)] = m.group(3)
    return out


def to_regex(pattern: str) -> "re.Pattern":
    out = b""
    for tok in pattern.split():
        out += b"." if tok == "?" else re.escape(bytes([int(tok, 16)]))
    return re.compile(out, re.DOTALL)


def win_path(p) -> str:
    return subprocess.run(["wslpath", "-w", str(p)], capture_output=True,
                          text=True, check=True).stdout.strip()


def dump_module(process: str, out_file: pathlib.Path) -> str | None:
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", dir=str(out_file.parent),
                                     delete=False, encoding="utf-8") as fh:
        fh.write(PS_DUMP.format(process=process, out=win_path(out_file)))
        script = pathlib.Path(fh.name)
    res = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", win_path(script)], capture_output=True, text=True)
    script.unlink(missing_ok=True)
    line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    if line == "NOPROC" or not out_file.is_file():
        return None
    return line


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ini", required=True, help="An offsets.ini with an [AOB] section.")
    ap.add_argument("--variant", choices=(versions.LEGACY, versions.ENHANCED),
                    default=versions.LEGACY)
    ap.add_argument("--dump", help="Scan this module dump instead of a running game.")
    ap.add_argument("--all", action="store_true",
                    help="Also report patterns the shipped C# never scans.")
    args = ap.parse_args()

    ini_path = pathlib.Path(args.ini)
    patterns = aob_section(ini_path.read_text(encoding="utf-8", errors="replace"))
    if not patterns:
        print(f"no [AOB] section in {ini_path}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as td:
        if args.dump:
            dump = pathlib.Path(args.dump)
            header = f"dump {dump}"
        else:
            dump = pathlib.Path(td) / "module.bin"
            header = dump_module(PROCESS[args.variant], dump)
            if header is None:
                print(f"{PROCESS[args.variant]} is not running -- start the game, or "
                      f"pass --dump", file=sys.stderr)
                return 1
        data = dump.read_bytes()

        print(f"\n  {args.variant}   {header}   {len(data):,} bytes")
        print(f"  {ini_path}\n")
        broken = []
        for name, pattern in patterns.items():
            skip = name in UNUSED and not args.all
            if not pattern.strip():
                if not skip:
                    print(f"  {name:18s} {'-':>5s}   leer, nicht abgeleitet")
                continue
            hits = len(to_regex(pattern).findall(data))
            if skip:
                continue
            if hits == 1:
                status = "OK"
            elif hits == 0:
                status = "KEIN TREFFER -- Pattern neu ableiten"
                if name not in UNUSED:
                    broken.append(name)
            elif name in AMBIGUOUS_OK:
                status = f"{hits} Treffer, erwartet ({AMBIGUOUS_OK[name]})"
            else:
                status = f"MEHRDEUTIG ({hits}) -- praezisieren"
                broken.append(name)
            print(f"  {name:18s} {hits:5d}   {status}")

        if args.all:
            print()
            for name, why in UNUSED.items():
                if name in patterns:
                    print(f"  {name:18s} {'':>5s}   ungenutzt: {why}")

        print()
        if broken:
            print(f"  {len(broken)} Pattern(s) brauchen Arbeit: {', '.join(broken)}\n")
            return 1
        print("  alle genutzten Patterns treffen\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
