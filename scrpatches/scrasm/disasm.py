"""Linear disassembler for GTA5 YSC bytecode."""

from __future__ import annotations

from .opcodes import OPCODE_NAMES, instruction_length, operand_length, DecodeError
from .model import Instruction


def disassemble(code: bytes, base: int = 0, start: int = 0, end: int | None = None) -> list[Instruction]:
    """Decode ``code[start:end]`` into a list of :class:`Instruction`.

    ``base`` is the virtual address assigned to ``code[0]`` (so instruction
    offsets and jump targets are expressed in the same address space as the
    original script). Raises :class:`DecodeError` on invalid/truncated input.
    """
    if end is None:
        end = len(code)
    out: list[Instruction] = []
    pos = start
    while pos < end:
        op = code[pos]
        if op >= len(OPCODE_NAMES):
            raise DecodeError(f"invalid opcode 0x{op:02X} at 0x{base + pos:X}")
        name = OPCODE_NAMES[op]
        oplen = operand_length(name, code, pos)
        nxt = pos + 1 + oplen
        if nxt > end:
            raise DecodeError(
                f"truncated {name} at 0x{base + pos:X} "
                f"(need {oplen} operand bytes, {end - pos - 1} available)"
            )
        out.append(Instruction(base + pos, op, name, bytes(code[pos + 1:nxt])))
        pos = nxt
    return out


def assemble(instructions: list[Instruction]) -> bytes:
    """Concatenate instructions back to bytes (lossless inverse of disassemble)."""
    return b"".join(ins.to_bytes() for ins in instructions)


def format_listing(instructions: list[Instruction], addr: bool = True) -> str:
    """Produce an annotated disassembly listing (hex column + mnemonic)."""
    lines = []
    for ins in instructions:
        hexcol = ins.hexbytes()
        desc = ins.describe()
        mnem = ins.name + (f" {desc}" if desc else "")
        if addr:
            lines.append(f"0x{ins.offset:06X}  {hexcol:<26} {mnem}")
        else:
            lines.append(f"{hexcol:<26} {mnem}")
    return "\n".join(lines)


def parse_hex(text: str) -> bytes:
    """Parse a space-separated hex byte string (e.g. '2D 00 03') into bytes."""
    text = text.strip()
    if not text:
        return b""
    return bytes(int(tok, 16) for tok in text.split())
