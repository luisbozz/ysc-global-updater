#!/usr/bin/env python3
"""Write one int32 into a running game's global table. WSL -> PowerShell -> RPM.

Built for the customfuncs bit-isolation test: set custom_check to exactly the
value that runs one dispatcher function and suppresses the rest, with no
rebuild between rounds -- the dispatcher polarity is "bit NOT set -> runs" (see
tools/watch_globals.py's docstring for the block-safety half of this story),
so to run *only* function N, every bit except N must be set to 1.

    # run only fn3 (bit 1) in fm_lts_creator's dispatcher, suppress the rest
    python3 tools/poke_global.py --index 2884084 --value 29   # 0b11101, bit1=0

    # back to "everything runs" (the untouched default)
    python3 tools/poke_global.py --index 2884084 --value 0

Prints the value actually read back afterward, so a failed write is visible
immediately rather than assumed.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import watch_globals as wg  # noqa: E402

PS_POKE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class PK {{
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(int a, bool i, int p);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadProcessMemory(IntPtr h, IntPtr a, byte[] b, int s, out IntPtr r);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool WriteProcessMemory(IntPtr h, IntPtr a, byte[] b, int s, out IntPtr w);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
  static IntPtr h = IntPtr.Zero;
  public static void Open(int pid){{ h = OpenProcess(0x0010|0x0020|0x0400,false,pid); if(h==IntPtr.Zero) throw new Exception("OpenProcess "+Marshal.GetLastWin32Error()); }}
  public static void Close(){{ CloseHandle(h); }}
  public static byte[] R(long a,int n){{ byte[] b=new byte[n]; IntPtr r; ReadProcessMemory(h,(IntPtr)a,b,n,out r); return b; }}
  public static long P(long a){{ return BitConverter.ToInt64(R(a,8),0); }}
  public static bool W(long a,byte[] b){{ IntPtr w; return WriteProcessMemory(h,(IntPtr)a,b,b.Length,out w) && w.ToInt64()==b.Length; }}
}}
"@ -Language CSharp
$p = Get-Process -Name '{process}' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) {{ Write-Output 'NOPROC'; exit 1 }}
[PK]::Open($p.Id)
$table = $p.MainModule.BaseAddress.ToInt64() + {table_rva}
$blockPtr = [PK]::P($table + 8 * {block})
if ($blockPtr -le 0x10000) {{ Write-Output 'NOBLOCK'; [PK]::Close(); exit 1 }}
$slotAddr = $blockPtr + 8 * {slot}
$ok = [PK]::W($slotAddr, [BitConverter]::GetBytes([int]{value}))
Write-Output ("WROTE {{0}}" -f $ok)
$back = [BitConverter]::ToInt32([PK]::R($slotAddr, 4), 0)
Write-Output ("READBACK {{0}}" -f $back)
[PK]::Close()
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=(wg.versions.LEGACY, wg.versions.ENHANCED),
                    default=wg.versions.LEGACY)
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("--value", type=int, required=True,
                    help="int32 to write, e.g. 0 (all off / everything runs), "
                         "or a bitmask suppressing everything except one bit.")
    args = ap.parse_args()

    block, slot = args.index >> 18, args.index & 0x3FFFF
    with tempfile.TemporaryDirectory() as td:
        workdir = pathlib.Path(td)
        process = wg.PROCESS[args.variant]
        rva = wg.table_rva(process, workdir)
        if rva is None:
            print(f"global table not found -- is {process} running?", file=sys.stderr)
            return 1
        script = PS_POKE.format(process=process, table_rva=rva, block=block,
                                slot=slot, value=args.value)
        out = wg.run_ps(script, workdir)
        print(out.strip())
        if "NOPROC" in out or "NOBLOCK" in out:
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
