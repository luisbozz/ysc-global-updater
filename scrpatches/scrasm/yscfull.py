"""Parser for calamity-inc ``*.ysc.full`` dumps (decrypted in-memory scrProgram).

Layout is authoritative, ported 1:1 from the GTA-V-Script-Decompiler
(``ScriptHeaders.cs`` + ``IO.cs``):

* pointers are ``ReadInt32() & 0xFFFFFF`` (low 24 bits = file offset in the dump)
* for a ``.full`` dump the RSC7 magic is absent, so ``RSC7Offset == 0``
* code lives in ``0x4000``-byte pages; ``CodeBlocksOffset`` points at an array
  of ``CodeBlocks`` page pointers; concatenating them yields ``CodeLength`` bytes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


def _i32(b: bytes, o: int) -> int:
    return struct.unpack_from("<i", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _ptr(b: bytes, o: int) -> int:
    return _u32(b, o) & 0xFFFFFF


@dataclass
class YscFull:
    path: str
    name: str
    code_length: int
    natives_count: int
    statics_count: int
    code: bytes = field(repr=False)
    code_page_offsets: list[int] = field(default_factory=list, repr=False)
    natives_offset: int = 0
    native_raw: list[int] = field(default_factory=list, repr=False)
    strings_offset: int = 0
    strings_size: int = 0

    @classmethod
    def parse(cls, path: str | Path) -> "YscFull":
        data = Path(path).read_bytes()
        magic = _u32(data, 0)
        # 'RSC7' little-endian variants; a .full dump has neither.
        rsc7 = 0x10 if magic in (0x37435352, 0x38435352) else 0x0

        base = rsc7
        code_blocks_offset = _ptr(data, base + 0x10)
        code_length = _i32(data, base + 0x1C)
        statics_count = _i32(data, base + 0x24)
        natives_count = _i32(data, base + 0x2C)
        natives_offset = _ptr(data, base + 0x40)
        script_name_offset = _ptr(data, base + 0x60)
        strings_offset = _ptr(data, base + 0x68)
        strings_size = _i32(data, base + 0x70)

        code_blocks = (code_length + 0x3FFF) >> 14

        # read page-pointer table (8 bytes per entry: 4-byte ptr + 4-byte skip)
        page_offsets: list[int] = []
        p = code_blocks_offset + rsc7
        for _ in range(code_blocks):
            page_offsets.append((_u32(data, p) & 0xFFFFFF) + rsc7)
            p += 8

        # assemble the contiguous CodeTable (exactly code_length bytes)
        chunks = []
        for i in range(code_blocks):
            size = code_length % 0x4000 if (i + 1) * 0x4000 >= code_length else 0x4000
            off = page_offsets[i]
            chunks.append(data[off:off + size])
        code = b"".join(chunks)

        # script name (nul-terminated)
        name = ""
        q = script_name_offset + rsc7
        while q < len(data) and data[q] not in (0,):
            name += chr(data[q])
            q += 1

        # raw native-hash table (count * u64); still needs rotate+crossmap
        native_raw = []
        no = natives_offset + rsc7
        for i in range(natives_count):
            native_raw.append(struct.unpack_from("<Q", data, no + i * 8)[0])

        return cls(
            path=str(path),
            name=name,
            code_length=code_length,
            natives_count=natives_count,
            statics_count=statics_count,
            code=code,
            code_page_offsets=page_offsets,
            natives_offset=natives_offset,
            native_raw=native_raw,
            strings_offset=strings_offset,
            strings_size=strings_size,
        )


def _selftest(path: str) -> None:
    from .disasm import disassemble, assemble
    y = YscFull.parse(path)
    print(f"{Path(path).name}: name={y.name!r} code_len={y.code_length} "
          f"natives={y.natives_count} statics={y.statics_count} "
          f"code_bytes={len(y.code)}")
    ins = disassemble(y.code)
    back = assemble(ins)
    print(f"  instructions={len(ins)} first={ins[0].name} last={ins[-1].name} "
          f"roundtrip={'OK' if back == y.code else 'MISMATCH'}")


if __name__ == "__main__":
    import sys
    for a in sys.argv[1:]:
        _selftest(a)
