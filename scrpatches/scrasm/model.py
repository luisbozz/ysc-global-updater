"""Instruction model + operand decoders for GTA5 YSC bytecode."""

from __future__ import annotations

from dataclasses import dataclass

from .opcodes import OPCODE_NAMES, JUMP_OPCODES


def _u16(b: bytes, i: int = 0) -> int:
    return b[i] | (b[i + 1] << 8)


def _s16(b: bytes, i: int = 0) -> int:
    v = _u16(b, i)
    return v - 0x10000 if v & 0x8000 else v


def _u24(b: bytes, i: int = 0) -> int:
    return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16)


def _u32(b: bytes, i: int = 0) -> int:
    return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)


@dataclass
class Instruction:
    """A single decoded instruction.

    ``operands`` holds the raw operand bytes exactly as they appear in the
    stream, so ``to_bytes()`` is always a lossless round-trip.
    """

    offset: int
    op: int
    name: str
    operands: bytes

    # ---- serialization -------------------------------------------------
    def to_bytes(self) -> bytes:
        return bytes([self.op]) + self.operands

    @property
    def length(self) -> int:
        return 1 + len(self.operands)

    # ---- typed operand views ------------------------------------------
    @property
    def is_jump(self) -> bool:
        return self.name in JUMP_OPCODES

    @property
    def jump_target(self) -> int:
        # int16(operands) + Offset + 3   (see Instruction.GetJumpOffset)
        return _s16(self.operands) + self.offset + 3

    @property
    def native_param(self) -> int:
        return self.operands[0] >> 2

    @property
    def native_return(self) -> int:
        return self.operands[0] & 0x3

    @property
    def native_index(self) -> int:
        # SwapEndian(u16 LE at operand[1]) -> big-endian value
        return (self.operands[1] << 8) | self.operands[2]

    @property
    def call_target(self) -> int:
        return _u24(self.operands)

    @property
    def u24(self) -> int:
        return _u24(self.operands)

    @property
    def u16(self) -> int:
        return _u16(self.operands)

    @property
    def s16(self) -> int:
        return _s16(self.operands)

    @property
    def u8(self) -> int:
        return self.operands[0]

    # ENTER fields
    @property
    def enter_params(self) -> int:
        return self.operands[0]

    @property
    def enter_vars(self) -> int:
        return _u16(self.operands, 1)

    @property
    def enter_namelen(self) -> int:
        return self.operands[3]

    @property
    def enter_name(self) -> str:
        n = self.enter_namelen
        if n == 0:
            return ""
        return self.operands[4:4 + n].split(b"\x00", 1)[0].decode("latin-1")

    # LEAVE fields
    @property
    def leave_params(self) -> int:
        return self.operands[0]

    @property
    def leave_returns(self) -> int:
        return self.operands[1]

    # SWITCH
    @property
    def switch_count(self) -> int:
        return self.operands[0]

    def switch_cases(self) -> list[tuple[int, int]]:
        """Return list of (case_value, jump_target)."""
        out = []
        base = self.offset
        for i in range(self.switch_count):
            o = 1 + i * 6
            value = _u32(self.operands, o)
            rel = _s16(self.operands, o + 4)
            # GetSwitchOffset (GTA5): Offset + 8 + i*6 + int16(operand[5+i*6])
            target = base + 8 + i * 6 + rel
            out.append((value, target))
        return out

    # ---- human-readable operand text ----------------------------------
    def describe(self) -> str:
        n = self.name
        if n == "NATIVE":
            return f"{self.native_param}, {self.native_return}, {self.native_index}"
        if n == "ENTER":
            nm = self.enter_name
            base = f"{self.enter_params}, {self.enter_vars}"
            return f"{base}  ; {nm}" if nm else base
        if n == "LEAVE":
            return f"{self.leave_params}, {self.leave_returns}"
        if n == "CALL":
            return f"0x{self.call_target:06X}  ; func@{self.call_target}"
        if n in ("GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE",
                 "LOCAL_U24", "LOCAL_U24_LOAD", "LOCAL_U24_STORE",
                 "PUSH_CONST_U24"):
            return str(self.u24)
        if n in ("GLOBAL_U16", "GLOBAL_U16_LOAD", "GLOBAL_U16_STORE",
                 "LOCAL_U16", "LOCAL_U16_LOAD", "LOCAL_U16_STORE",
                 "STATIC_U16", "STATIC_U16_LOAD", "STATIC_U16_STORE",
                 "ARRAY_U16", "ARRAY_U16_LOAD", "ARRAY_U16_STORE"):
            return str(self.u16)
        if n in ("IOFFSET_S16", "IOFFSET_S16_LOAD", "IOFFSET_S16_STORE",
                 "PUSH_CONST_S16", "IADD_S16", "IMUL_S16"):
            return str(self.s16)
        if n in ("PUSH_CONST_U8", "ARRAY_U8", "ARRAY_U8_LOAD", "ARRAY_U8_STORE",
                 "LOCAL_U8", "LOCAL_U8_LOAD", "LOCAL_U8_STORE",
                 "STATIC_U8", "STATIC_U8_LOAD", "STATIC_U8_STORE",
                 "IADD_U8", "IMUL_U8", "IOFFSET_U8", "IOFFSET_U8_LOAD",
                 "IOFFSET_U8_STORE", "TEXT_LABEL_ASSIGN_STRING",
                 "TEXT_LABEL_ASSIGN_INT", "TEXT_LABEL_APPEND_STRING",
                 "TEXT_LABEL_APPEND_INT"):
            return str(self.u8)
        if n == "PUSH_CONST_U8_U8":
            return f"{self.operands[0]}, {self.operands[1]}"
        if n == "PUSH_CONST_U8_U8_U8":
            return f"{self.operands[0]}, {self.operands[1]}, {self.operands[2]}"
        if n == "PUSH_CONST_U32":
            return str(_u32(self.operands))
        if n == "PUSH_CONST_F":
            import struct
            return str(struct.unpack("<f", self.operands)[0])
        if self.is_jump:
            return f"-> 0x{self.jump_target:04X}"
        if n == "SWITCH":
            parts = [f"{v}=>0x{t:04X}" for v, t in self.switch_cases()]
            return f"[{self.switch_count}] " + " ".join(parts)
        return ""

    def hexbytes(self) -> str:
        return " ".join(f"{b:02X}" for b in self.to_bytes())
