#!/usr/bin/env python3
"""The pickup types a weapon keeps when a job loads (weapon_pickup_models in Xenvious' prop_lists.json).

Every creator checks each placed weapon when it loads a job:

    if (!func_5265(Global_4980736.f_60342[i /*176*/].f_16))
        if (... f_16 == joaat("pickup_weapon_marksmanrifle"))
            ... f_16 = joaat("pickup_weapon_sniperrifle");
        else
            ... f_16 = joaat("pickup_weapon_pistol");

(fm_lts_creator 1.73). The list is that function's switch cases. A last block of cases only
counts for Rockstar jobs (content category 1 or 2, e.g. the fertilizer can); those are left out,
since a player's own job is category 0. Xenvious warns on the weapon page when a pickup type is
not on the list, so it does not silently turn into a pistol.

Values are written as signed decimal, as Xenvious compares them (model.ToString()).

Usage
-----
    python3 tools/extract_pickup_models.py --scripts scripts/<build>
"""
from __future__ import annotations

import argparse
import pathlib
import re

from tools.extract_capture_models import _VALUE, _signed

SCRIPT = "fm_lts_creator.c"
ANCHOR = '= joaat("pickup_weapon_sniperrifle");'


def pickup_models(scripts: pathlib.Path, script: str = SCRIPT) -> list[str]:
    """The kept pickup types as signed decimal strings, sorted. ValueError when the check is not
    where it was or does not look like a plain list."""
    lines = (scripts / script).read_text(encoding="utf-8", errors="replace").split("\n")
    func = None
    for k, line in enumerate(lines):
        if ANCHOR not in line.lower():
            continue
        for back in lines[max(0, k - 8):k][::-1]:
            m = re.search(r"if \(!(\w+)\([^()]*\.f_16\)\)", back)
            if m:
                func = m.group(1)
                break
        if func:
            break
    if not func:
        raise ValueError(f"no \"if (!func_N(type)) ... {ANCHOR}\" in {script}")
    start = next((k for k, l in enumerate(lines) if re.match(rf"^\w[\w *]*\b{func}\(", l)), None)
    if start is None:
        raise ValueError(f"{func} not found")
    end = next(k for k in range(start + 1, len(lines)) if lines[k].startswith("}"))
    body = lines[start + 1:end]
    # Stop at the first condition on the function's own level: what follows only counts sometimes.
    plain = []
    for line in body:
        if re.match(r"^\tif \(", line):
            break
        plain.append(line)
    text = "\n".join(plain)
    returns = re.findall(r"return (\w+);", "\n".join(body))
    if not returns or any(r not in ("1", "true") for r in returns[:-1]) or returns[-1] not in ("0", "false"):
        raise ValueError(f"{func} is not a plain pickup list (returns {returns})")
    values = {_signed(a, b) for a, b in re.findall(rf"case {_VALUE}:", text)}
    if len(values) < 20:
        raise ValueError(f"{func} lists only {len(values)} pickup types")
    return [str(v) for v in sorted(values)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scripts", required=True, type=pathlib.Path)
    a = ap.parse_args()
    models = pickup_models(a.scripts)
    print(f"{len(models)} pickup types")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
