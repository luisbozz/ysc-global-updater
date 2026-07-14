"""Native index <-> hash <-> name resolution.

Ported from the GTA-V-Script-Decompiler (``NativeTables.cs`` + ``Crossmap.cs``):
a script's native table stores 64-bit values that must be

    hash = rotate_left(raw_u64, (code_length + index) % 64)

then run through the crossmap (build-specific hash -> canonical hash) before
looking the canonical hash up in ``natives.json`` (hash -> namespace::name).

This is what makes custom functions *maintainable across updates*: we store a
native by NAME, and re-resolve its (volatile) per-build index every version.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Native data ships bundled under scrasm/data/ (keeps scrasm self-contained);
# fall back to the decompiler's Resources folder if the bundle is absent.
_BUNDLED = Path(__file__).resolve().parent / "data"
_DECOMP = Path(__file__).resolve().parents[1] / "decompiler" / "GTA V Script Decompiler" / "Resources"


def _resource(name: str) -> Path:
    for base in (_BUNDLED, _DECOMP):
        p = base / name
        if p.exists():
            return p
    return _BUNDLED / name


_NATIVES_JSON = _resource("natives.json")
_CROSSMAP_TXT = _resource("crossmap.txt")

_MASK64 = (1 << 64) - 1


def _rotl64(value: int, rotate: int) -> int:
    rotate %= 64
    if rotate == 0:
        return value & _MASK64
    return ((value << rotate) | (value >> (64 - rotate))) & _MASK64


@lru_cache(maxsize=1)
def load_hash_names(path: str | None = None) -> dict[int, str]:
    """hash(int) -> 'NAMESPACE::NAME' from natives.json."""
    p = Path(path) if path else _NATIVES_JSON
    doc = json.loads(p.read_text())
    out: dict[int, str] = {}
    for namespace, entries in doc.items():
        for hash_str, entry in entries.items():
            try:
                h = int(hash_str, 16)
            except ValueError:
                continue
            out[h] = f"{namespace}::{entry.get('name', hash_str)}"
    return out


@lru_cache(maxsize=1)
def load_crossmap(path: str | None = None) -> dict[int, int]:
    """older_hash(int) -> newer/canonical hash(int) from crossmap.txt."""
    p = Path(path) if path else _CROSSMAP_TXT
    table: dict[int, int] = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if len(line) <= 1:
            continue
        # format: <newer><sep><older>, sep in :,=  (see Crossmap.cs)
        for sep in (":", "=", ","):
            if sep in line:
                newer_s, older_s = line.split(sep, 1)
                break
        else:
            continue
        try:
            newer = int(newer_s.strip().removeprefix("0x"), 16)
            older = int(older_s.strip().removeprefix("0x"), 16)
        except ValueError:
            continue
        table[older] = newer
    return table


class NativeResolver:
    """Resolve native indices for a specific script/version (a YscFull)."""

    def __init__(self, native_raw: list[int], code_length: int,
                 names: dict[int, str] | None = None,
                 crossmap: dict[int, int] | None = None):
        self._raw = native_raw
        self._code_length = code_length
        self._names = names if names is not None else load_hash_names()
        self._crossmap = crossmap if crossmap is not None else load_crossmap()

    @classmethod
    def from_full(cls, full) -> "NativeResolver":
        return cls(full.native_raw, full.code_length)

    def __len__(self) -> int:
        return len(self._raw)

    def hash_at(self, index: int) -> int:
        h = _rotl64(self._raw[index], self._code_length + index)
        return self._crossmap.get(h, h)

    def name_at(self, index: int) -> str:
        if index < 0 or index >= len(self._raw):
            return f"native_{index}"
        h = self.hash_at(index)
        return self._names.get(h, f"unk_0x{h:016X}")

    def index_of_hash(self) -> dict[int, int]:
        """canonical hash -> index (for re-resolving a name to a new index)."""
        return {self.hash_at(i): i for i in range(len(self._raw))}

    def index_of_name(self) -> dict[str, int]:
        return {self.name_at(i): i for i in range(len(self._raw))}
