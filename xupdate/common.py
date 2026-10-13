"""Shared pieces: folders, review items, and finding a value's place in the scripts."""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DISASM = ROOT / "scrpatches" / "disasm"
WORK = ROOT / "reports" / "xupdate"

# Order of the scripts searched for a value; the creators carry nearly every offset.
C_ORDER = ("fm_lts_creator", "fm_capture_creator", "fm_deathmatch_creator", "fm_race_creator",
           "fm_survival_creator", "public_mission_creator", "fmmc_launcher",
           "tuneables_processing")
CONTEXT_LINES = 6


@dataclass
class Snippet:
    file: str            # absolute path
    line: int            # 1-based line of the value
    lines: list          # the text around it
    first: int           # 1-based line number of lines[0]
    mark: str = ""       # text to highlight on the value's line


@dataclass
class Item:
    """One thing a person has to decide. ``answer`` stays None until then."""
    id: str
    kind: str            # offset, patch, page, vehicle, info
    title: str
    question: str
    old_value: str | None = None
    guess: str | None = None
    old: Snippet | None = None
    new: Snippet | None = None
    note: str = ""
    answer: str | None = None
    choices: list = field(default_factory=list)
    sure: bool = False   # the guess comes from identical code around it

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Item":
        d = dict(d)
        for k in ("old", "new"):
            if d.get(k):
                d[k] = Snippet(**d[k])
        return Item(**d)


def c_files(build: str) -> list[Path]:
    folder = SCRIPTS / build
    known = [folder / f"{s}.c" for s in C_ORDER if (folder / f"{s}.c").is_file()]
    return known


@lru_cache(maxsize=24)
def read_lines(path: str) -> tuple:
    return tuple(Path(path).read_text(encoding="utf-8", errors="replace").splitlines())


def snippet(path: Path | str, line: int, mark: str = "") -> Snippet:
    lines = read_lines(str(path))
    a = max(0, line - 1 - CONTEXT_LINES)
    b = min(len(lines), line + CONTEXT_LINES)
    return Snippet(str(path), line, list(lines[a:b]), a + 1, mark)


_PREFIX = re.compile(r"(Global|[a-z]?Local)_(\d+)((?:\.f_\d+)?)")
CACHE = WORK / ".cache"


def _prefix_key(head: str, base: str, field: str) -> str:
    return ("Local" if head.endswith("Local") else head) + "_" + base + field


@lru_cache(maxsize=24)
def prefix_index(path: str) -> dict:
    """"Global_N.f_M" and "Global_N" -> line numbers (0-based) that use them; locals without
    their type letter. Saves scanning millions of lines per key."""
    import pickle
    p = Path(path)
    st = p.stat()
    key = CACHE / f"idx-{p.parent.name}-{p.stem}-{st.st_size}-{int(st.st_mtime)}.pickle"
    if key.is_file():
        return pickle.loads(key.read_bytes())
    idx = {}
    for n, line in enumerate(read_lines(path)):
        seen = set()
        for m in _PREFIX.finditer(line):
            for k in (_prefix_key(m.group(1), m.group(2), ""), _prefix_key(*m.groups())):
                if k not in seen:
                    seen.add(k)
                    idx.setdefault(k, []).append(n)
    CACHE.mkdir(parents=True, exist_ok=True)
    key.write_bytes(pickle.dumps(idx))
    return idx


# A key's last word names the script its local belongs to.
SCRIPT_OF_SUFFIX = {"lts": "fm_lts_creator", "capture": "fm_capture_creator",
                    "dm": "fm_deathmatch_creator", "race": "fm_race_creator",
                    "survival": "fm_survival_creator", "mission": "public_mission_creator"}


def scripts_for(key: str | None, path: str) -> tuple | None:
    """The scripts to look in: a local only in its own script."""
    if "Local_" not in path.split(".")[0] or not key:
        return None
    suffix = key.lower().rsplit("_", 1)[-1]
    s = SCRIPT_OF_SUFFIX.get(suffix)
    return (s,) if s else None


def path_regex(path: str) -> re.Pattern | None:
    """A path as it is written in the decompiled source, any index inside the brackets.
    A trailing bare ``[i]`` (the array's start) is optional there."""
    from .paths import parse
    p = parse(path)
    if not p:
        return None
    head, base, toks = p
    while toks and toks[-1].kind == "var" and toks[-1].stride is None:
        toks = toks[:-1]
    rx = (r"\b[a-z]?Local_" if head.endswith("Local") else r"\bGlobal_") + str(base)
    for t in toks:
        rx += re.escape(f".f_{t.n}") if t.kind == "f" else r"\[[^\]]*\]"
    return re.compile(rx + r"(?![\d.\[])|" + rx + r"(?=[.\[])")


def find_path(path: str, build: str, limit: int = 1, only=None) -> list[tuple[Path, int, str]]:
    from .paths import parse
    rx, p = path_regex(path), parse(path)
    out = []
    if not rx:
        return out
    head, base, toks = p
    first = f".f_{toks[0].n}" if toks and toks[0].kind == "f" else ""
    pk = _prefix_key(head, str(base), first)
    for f in c_files(build):
        if only and f.stem not in only:
            continue
        lines = read_lines(str(f))
        for n in prefix_index(str(f)).get(pk, ()):
            m = rx.search(lines[n])
            if m:
                out.append((f, n + 1, m.group(0)))
                if len(out) >= limit:
                    return out
    return out


def snippet_for_path(path: str, build: str, key: str | None = None) -> Snippet | None:
    hit = find_path(path, build, only=scripts_for(key, path))
    return snippet(hit[0][0], hit[0][1], hit[0][2]) if hit else None


# -- guessing a value by the code around it -------------------------------------------

_NUM = re.compile(r"\d+")
_GLOBAL = re.compile(r"(?:Global|[a-z]?Local)_\d+(?:\.f_\d+|\[[^\]]*\])*")


def norm(line: str) -> str:
    return _NUM.sub("#", line.strip())


@lru_cache(maxsize=24)
def norm_index(path: str) -> dict:
    idx = {}
    for n, line in enumerate(read_lines(path)):
        idx.setdefault(norm(line), []).append(n)
    return idx


@dataclass
class Guess:
    value: str
    snippet: Snippet
    score: int
    how: str


def guess_by_context(old_path: str, old_build: str, new_build: str, tries: int = 6,
                     key: str | None = None) -> Guess | None:
    """See _guess; a vector's y/z is also looked for as ``.f_x.f_1``/``.f_x.f_2``."""
    from .paths import join_last, split_last
    only = scripts_for(key, old_path)
    g = _guess(old_path, old_build, new_build, tries, only)
    if g:
        return g
    for k in (1, 2):
        alt = split_last(old_path, k)
        g = alt and _guess(alt, old_build, new_build, tries, only)
        if g:
            from .paths import parse
            if parse(g.value):
                g.value = join_last(g.value)
            g.how += f" (als Vektor-Komponente .f_{k})"
            return g
    return None


WIDE = 30  # lines each side used to tell apart blocks that look alike up close


def _guess(old_path: str, old_build: str, new_build: str, tries: int, only=None) -> Guess | None:
    """Where the old path is used in the old script, find the same code in the new script
    (same line and neighbours with numbers ignored) and take the path at the same place.

    Each use (up to ``tries``) gives its own answer. The guess is sure only when the best
    place has (nearly) all close neighbours equal, stands out from the next best place,
    and no other good use points somewhere else. The review shows both sides, so a
    person sees at once whether the code around it changed."""
    from .paths import address, like, reshape
    found_any = []
    for f, n, found in find_path(old_path, old_build, limit=tries, only=only):
        new_file = SCRIPTS / new_build / f.name
        if not new_file.is_file():
            continue
        old_lines = read_lines(str(f))
        new_lines = read_lines(str(new_file))
        line = old_lines[n - 1]
        k = next((i for i, m in enumerate(_GLOBAL.finditer(line)) if found in m.group(0)), None)
        if k is None:
            continue
        cands = norm_index(str(new_file)).get(norm(line), [])
        if not cands or len(cands) > 3000:
            continue

        def score(j):
            close = wide = raw = 0
            for d in range(1, WIDE + 1):
                for a, b in ((n - 1 - d, j - d), (n - 1 + d, j + d)):
                    if 0 <= a < len(old_lines) and 0 <= b < len(new_lines):
                        if norm(old_lines[a]) == norm(new_lines[b]):
                            wide += 1
                            close += d <= CONTEXT_LINES
                        raw += old_lines[a].strip() == new_lines[b].strip()
            return close, wide, raw

        scored = sorted(((score(j), j) for j in cands), reverse=True)
        top, j = scored[0]
        if len(scored) > 1 and scored[1][0] == top:
            continue  # two places look exactly alike: no guess from this use
        clear = len(scored) == 1 or scored[1][0][:2] < top[:2]
        ms = list(_GLOBAL.finditer(new_lines[j]))
        if k >= len(ms):
            continue
        raw = ms[k].group(0)
        value = like(old_path, raw) or reshape(old_path, raw)
        if value is None:
            continue  # the code there has another shape: no guess from this use
        how = f"gleiche Stelle im neuen Skript, {top[0]}/{2 * CONTEXT_LINES} Nachbarzeilen gleich"
        if not clear:
            how += ", ähnliche Stelle in der Nähe"
        found_any.append((top[0] if clear else min(top[0], SURE_CLOSE - 1), top,
                          Guess(value, snippet(new_file, j + 1, raw), top[0], how)))
    if not found_any:
        return None
    found_any.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = found_any[0][2]
    best.score = found_any[0][0]
    others = {address(g.value) for sc, _, g in found_any[1:] if sc >= SURE_CLOSE}
    if others - {address(best.value)}:
        best.score = min(best.score, SURE_CLOSE - 1)
        best.how += "; andere Verwendungen zeigen woanders hin"
    return best


# Close neighbour lines (of 12) a context guess needs to count as sure.
SURE_CLOSE = 10


def vscode_link(path: str, line: int) -> str:
    """A link that opens the file at the line in VS Code; WSL paths go through the WSL remote."""
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro and path.startswith("/") and not path.startswith("/mnt/"):
        return f"vscode://vscode-remote/wsl+{distro}{path}:{line}"
    if path.startswith("/mnt/") and len(path) > 6:
        path = f"{path[5].upper()}:/{path[7:]}"
    return f"vscode://file/{path}:{line}"
