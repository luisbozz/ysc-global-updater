#!/usr/bin/env python3
"""Watch script globals in a running game and report what changes them.

Built for one failure mode. GTA stores an array as a length word followed by
its elements: an array of four reads ``4 0 0 0 0``. Xenvious addresses an
element by summing the numbers in an offset path and adding one to step past
that length word. If an offset is wrong by a little, a write lands on some
other array's length word, the script that owns it then iterates zero (or far
too many) elements, and the creator fails to load with no error anywhere.

Reading the value once says nothing, because the damage happens at the moment
of the bad write. This polls a window of globals, keeps the first reading as a
baseline, and prints every slot that changes afterwards -- what it was, what it
became, and when. Reproduce the fault while it runs and the culprit names
itself: the clobbered address maps straight back to the offset that owns it.

    # watch the tuneables block around csttn, then enter the LTS creator
    python3 tools/watch_globals.py --index 4830409 --span 40

    # watch every offset the app resolves, at once
    python3 tools/watch_globals.py --ini /opt/Xenvious/Xenvious/OfflineData/legacy/offsets.ini

Runs from WSL, drives Windows PowerShell, reads the game's memory and writes
nothing to it.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import versions  # noqa: E402

PROCESS = {versions.LEGACY: "GTA5", versions.ENHANCED: "GTA5_Enhanced"}

# `48 8D 15 <rel> 4C 8B C0 E8 ...` -- the instruction that loads the global
# table. Same pattern the app scans for; the table address is rip-relative at +3.
GLOBAL_TABLE_AOB = "48 8D 15 ? ? ? ? 4C 8B C0 E8 ? ? ? ? 48 85 FF 48 89 1D"

PS_READ = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class M {{
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(int a, bool i, int p);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadProcessMemory(IntPtr h, IntPtr a, byte[] b, int s, out IntPtr r);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
  static IntPtr h = IntPtr.Zero;
  public static void Open(int pid){{ h = OpenProcess(0x0010|0x0400,false,pid); if(h==IntPtr.Zero) throw new Exception("OpenProcess "+Marshal.GetLastWin32Error()); }}
  public static void Close(){{ CloseHandle(h); }}
  public static byte[] R(long a,int n){{ byte[] b=new byte[n]; IntPtr r; ReadProcessMemory(h,(IntPtr)a,b,n,out r); return b; }}
  public static long P(long a){{ return BitConverter.ToInt64(R(a,8),0); }}
  public static int I(long a){{ return BitConverter.ToInt32(R(a,4),0); }}
}}
"@ -Language CSharp
$p = Get-Process -Name '{process}' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) {{ Write-Output 'NOPROC'; exit 1 }}
[M]::Open($p.Id)
$table = $p.MainModule.BaseAddress.ToInt64() + {table_rva}
foreach ($idx in [int[]]@({indices})) {{
    $blockPtr = [M]::P($table + 8 * (($idx -shr 18) -band 0x3F))
    if ($blockPtr -le 0x10000) {{ Write-Output "$idx NA"; continue }}
    Write-Output ("{{0}} {{1}}" -f $idx, [M]::I($blockPtr + 8 * ($idx -band 0x3FFFF)))
}}
[M]::Close()
"""

PS_DUMP = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class D {{
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(int a, bool i, int p);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadProcessMemory(IntPtr h, IntPtr a, byte[] b, int s, out IntPtr r);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
  public static byte[] Dump(int pid, IntPtr b, int size) {{
    IntPtr h = OpenProcess(0x0010|0x0400,false,pid);
    if (h == IntPtr.Zero) throw new Exception("OpenProcess failed");
    byte[] buf = new byte[size]; IntPtr read;
    ReadProcessMemory(h, b, buf, size, out read); CloseHandle(h); return buf;
  }}
}}
"@ -Language CSharp
$p = Get-Process -Name '{process}' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) {{ Write-Output 'NOPROC'; exit 1 }}
[IO.File]::WriteAllBytes('{out}', [D]::Dump($p.Id, $p.MainModule.BaseAddress, $p.MainModule.ModuleMemorySize))
Write-Output 'OK'
"""


def win_path(p) -> str:
    return subprocess.run(["wslpath", "-w", str(p)], capture_output=True,
                          text=True, check=True).stdout.strip()


def run_ps(script: str, workdir: pathlib.Path) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", dir=str(workdir),
                                     delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = pathlib.Path(fh.name)
    res = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", win_path(path)], capture_output=True, text=True)
    path.unlink(missing_ok=True)
    return res.stdout


def table_rva(process: str, workdir: pathlib.Path) -> int | None:
    """Find the global table by scanning the live module, as the app does."""
    dump = workdir / "module.bin"
    if run_ps(PS_DUMP.format(process=process, out=win_path(dump)),
              workdir).strip() != "OK":
        return None
    data = dump.read_bytes()
    pattern = b"".join(b"." if t == "?" else re.escape(bytes([int(t, 16)]))
                       for t in GLOBAL_TABLE_AOB.split())
    hits = [m.start() for m in re.finditer(pattern, data, re.DOTALL)]
    dump.unlink(missing_ok=True)
    if len(hits) != 1:
        return None
    import struct
    off = hits[0]
    return off + 7 + struct.unpack_from("<i", data, off + 3)[0]


def global_index(value: str) -> int | None:
    """The address Xenvious computes for an offset value: every number, summed."""
    value = re.sub(r"\[.*?\]", "", value)
    if not value.startswith("Global_"):
        return None
    return sum(int(x) for x in re.findall(r"\d+", value))


def offsets_from_ini(path: pathlib.Path) -> dict:
    out = {}
    for m in re.finditer(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]*)"',
                         path.read_text(encoding="utf-8", errors="replace"),
                         re.MULTILINE):
        i = global_index(m.group(2))
        if i is not None:
            out.setdefault(i, m.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=(versions.LEGACY, versions.ENHANCED),
                    default=versions.LEGACY)
    ap.add_argument("--index", type=int, help="Centre the window on this global index.")
    ap.add_argument("--span", type=int, default=40,
                    help="How many slots around --index to watch (default 40).")
    ap.add_argument("--ini", help="Watch every global an offsets.ini names.")
    ap.add_argument("--interval", type=float, default=1.0, help="Seconds between reads.")
    ap.add_argument("--seconds", type=float, default=120.0, help="How long to watch.")
    args = ap.parse_args()

    if not args.index and not args.ini:
        ap.error("give --index or --ini")

    names, indices = {}, []
    if args.ini:
        names = offsets_from_ini(pathlib.Path(args.ini))
        indices = sorted(names)
    else:
        half = args.span // 2
        indices = list(range(args.index - half, args.index + half + 1))

    with tempfile.TemporaryDirectory() as td:
        workdir = pathlib.Path(td)
        process = PROCESS[args.variant]
        rva = table_rva(process, workdir)
        if rva is None:
            print(f"global table not found -- is {process} running?", file=sys.stderr)
            return 1
        print(f"\n  {process}   global table at RVA 0x{rva:X}   "
              f"watching {len(indices)} slot(s)\n")

        script = PS_READ.format(process=process, table_rva=rva,
                                indices=",".join(str(i) for i in indices))
        baseline, deadline, reads = {}, time.time() + args.seconds, 0
        while time.time() < deadline:
            out = run_ps(script, workdir)
            if "NOPROC" in out:
                print("  game closed", file=sys.stderr)
                return 1
            current = {}
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] != "NA":
                    current[int(parts[0])] = int(parts[1])
            if not baseline:
                baseline = current
                print(f"  baseline taken, {len(baseline)} slot(s) readable."
                      f"  Reproduce the fault now.\n")
            else:
                for idx, value in current.items():
                    was = baseline.get(idx)
                    if was is not None and was != value:
                        who = names.get(idx, "")
                        label = f"  {who}" if who else ""
                        print(f"  [{time.strftime('%H:%M:%S')}] {idx}  "
                              f"{was} -> {value}{label}")
                        baseline[idx] = value
            reads += 1
            time.sleep(args.interval)
        print(f"\n  done, {reads} read(s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
