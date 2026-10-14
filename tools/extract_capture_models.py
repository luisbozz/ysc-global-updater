#!/usr/bin/env python3
"""The models a capture object may have (capture_object_models in Xenvious' prop_lists.json).

When the Capture Creator loads a job, every capture object whose model is not on a fixed list
becomes a security case:

    if (!func_5452(Global_4980736.f_7128[i /*668*/].f_16))
        Global_4980736.f_7128[i /*668*/].f_16 = joaat("prop_security_case_01");

(fm_capture_creator 1.73, func_5451). The list is that function: switch cases and
"iParam0 == ..." conditions, all of them returning 1. Xenvious warns on the capture object
page when a model is not on it, so the object does not silently turn into a case.

Values are written as signed decimal, as Xenvious compares them (model.ToString()).

Usage
-----
    python3 tools/extract_capture_models.py --scripts scripts/<build>
"""
from __future__ import annotations

import argparse
import pathlib
import re

SCRIPT = "fm_capture_creator.c"
CASE = "prop_security_case_01"
_VALUE = r'(?:joaat\("([^"]+)"\)|(-?\d+))'


def joaat(s: str) -> int:
    h = 0
    for c in s.lower().encode():
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h - (1 << 32) if h & 0x80000000 else h


def _signed(name: str | None, number: str | None) -> int:
    if name:
        return joaat(name)
    v = int(number) & 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def capture_models(scripts: pathlib.Path) -> list[str]:
    """The allowed models as signed decimal strings, sorted. ValueError when the check is not
    where it was or does not look like a plain list."""
    lines = (scripts / SCRIPT).read_text(encoding="utf-8", errors="replace").split("\n")
    func = None
    for k, line in enumerate(lines):
        if f'= joaat("{CASE}");' not in line:
            continue
        for back in lines[max(0, k - 3):k][::-1]:
            m = re.search(r"if \(!(func_\d+)\([^()]*\.f_\d+\)\)", back)
            if m:
                func = m.group(1)
                break
        if func:
            break
    if not func:
        raise ValueError(f'no "if (!func_N(model)) model = {CASE}" in {SCRIPT}')
    start = next((k for k, l in enumerate(lines) if re.match(rf"^\w[\w ]*\b{func}\(", l)), None)
    if start is None:
        raise ValueError(f"{func} not found")
    end = next(k for k in range(start + 1, len(lines)) if lines[k].startswith("}"))
    body = "\n".join(lines[start + 1:end])
    returns = re.findall(r"return (\w+);", body)
    # Every way out but the last says "allowed"; anything else is not a plain list.
    if not returns or any(r not in ("1", "true") for r in returns[:-1]) or returns[-1] not in ("0", "false"):
        raise ValueError(f"{func} is not a plain model list (returns {returns})")
    values = {_signed(a, b) for a, b in re.findall(rf"case {_VALUE}:", body)}
    values |= {_signed(a, b) for a, b in re.findall(rf"== {_VALUE}", body)}
    if not values:
        raise ValueError(f"{func} lists no models")
    return [str(v) for v in sorted(values)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scripts", required=True, type=pathlib.Path)
    a = ap.parse_args()
    models = capture_models(a.scripts)
    print(f"{len(models)} models")
    for m in models:
        print(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
