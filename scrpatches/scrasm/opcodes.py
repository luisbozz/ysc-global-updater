"""Authoritative GTA5 YSC opcode table.

Source of truth: GTA-V-Script-Decompiler
  * ``Instruction.cs``  -> the ``Opcode`` enum (order == byte value for GTA5;
    ``MapOpcode`` returns ``(Opcode)opcode`` unless the RDR2 flag is set).
  * ``ScriptFile.cs``   -> the operand-length ``switch`` used while walking code.

Every entry here was cross-checked against real ``bytes_to_patch`` blobs in
``scrpatches/data/scrpatches.json`` (e.g. ``2C``=NATIVE, ``2D``=ENTER,
``5D``=CALL, ``49``=ARRAY_U16, ``82``=IS_BIT_SET).
"""

from __future__ import annotations

# Byte value -> mnemonic. Index == opcode byte (GTA5). 0..130 are the opcodes
# that actually occur in GTA5 scripts; the decompiler enum continues past this
# with RDR2/newer opcodes which never appear in GTA5 bytecode.
OPCODE_NAMES: list[str] = [
    "NOP", "IADD", "ISUB", "IMUL", "IDIV", "IMOD", "INOT", "INEG", "IEQ", "INE",
    "IGT", "IGE", "ILT", "ILE", "FADD", "FSUB", "FMUL", "FDIV", "FMOD", "FNEG",
    "FEQ", "FNE", "FGT", "FGE", "FLT", "FLE", "VADD", "VSUB", "VMUL", "VDIV",
    "VNEG", "IAND", "IOR", "IXOR", "I2F", "F2I", "F2V",
    "PUSH_CONST_U8", "PUSH_CONST_U8_U8", "PUSH_CONST_U8_U8_U8",
    "PUSH_CONST_U32", "PUSH_CONST_F", "DUP", "DROP", "NATIVE", "ENTER", "LEAVE",
    "LOAD", "STORE", "STORE_REV", "LOAD_N", "STORE_N",
    "ARRAY_U8", "ARRAY_U8_LOAD", "ARRAY_U8_STORE",
    "LOCAL_U8", "LOCAL_U8_LOAD", "LOCAL_U8_STORE",
    "STATIC_U8", "STATIC_U8_LOAD", "STATIC_U8_STORE",
    "IADD_U8", "IMUL_U8", "IOFFSET", "IOFFSET_U8", "IOFFSET_U8_LOAD",
    "IOFFSET_U8_STORE", "PUSH_CONST_S16", "IADD_S16", "IMUL_S16",
    "IOFFSET_S16", "IOFFSET_S16_LOAD", "IOFFSET_S16_STORE",
    "ARRAY_U16", "ARRAY_U16_LOAD", "ARRAY_U16_STORE",
    "LOCAL_U16", "LOCAL_U16_LOAD", "LOCAL_U16_STORE",
    "STATIC_U16", "STATIC_U16_LOAD", "STATIC_U16_STORE",
    "GLOBAL_U16", "GLOBAL_U16_LOAD", "GLOBAL_U16_STORE",
    "J", "JZ", "IEQ_JZ", "INE_JZ", "IGT_JZ", "IGE_JZ", "ILT_JZ", "ILE_JZ",
    "CALL", "LOCAL_U24", "LOCAL_U24_LOAD", "LOCAL_U24_STORE",
    "GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE", "PUSH_CONST_U24",
    "SWITCH", "STRING", "STRINGHASH",
    "TEXT_LABEL_ASSIGN_STRING", "TEXT_LABEL_ASSIGN_INT",
    "TEXT_LABEL_APPEND_STRING", "TEXT_LABEL_APPEND_INT", "TEXT_LABEL_COPY",
    "CATCH", "THROW", "CALLINDIRECT",
    "PUSH_CONST_M1", "PUSH_CONST_0", "PUSH_CONST_1", "PUSH_CONST_2",
    "PUSH_CONST_3", "PUSH_CONST_4", "PUSH_CONST_5", "PUSH_CONST_6",
    "PUSH_CONST_7", "PUSH_CONST_FM1", "PUSH_CONST_F0", "PUSH_CONST_F1",
    "PUSH_CONST_F2", "PUSH_CONST_F3", "PUSH_CONST_F4", "PUSH_CONST_F5",
    "PUSH_CONST_F6", "PUSH_CONST_F7", "IS_BIT_SET",
]

# mnemonic -> byte value
NAME_TO_BYTE: dict[str, int] = {name: i for i, name in enumerate(OPCODE_NAMES)}

# Fixed operand byte counts (opcode byte itself is NOT included).
# Anything not listed and not ENTER/SWITCH has zero operands.
_LEN1 = {
    "PUSH_CONST_U8", "ARRAY_U8", "ARRAY_U8_LOAD", "ARRAY_U8_STORE",
    "LOCAL_U8", "LOCAL_U8_LOAD", "LOCAL_U8_STORE",
    "STATIC_U8", "STATIC_U8_LOAD", "STATIC_U8_STORE",
    "IADD_U8", "IMUL_U8", "IOFFSET_U8", "IOFFSET_U8_LOAD", "IOFFSET_U8_STORE",
    "TEXT_LABEL_ASSIGN_STRING", "TEXT_LABEL_ASSIGN_INT",
    "TEXT_LABEL_APPEND_STRING", "TEXT_LABEL_APPEND_INT",
}
_LEN2 = {
    "PUSH_CONST_U8_U8", "PUSH_CONST_S16", "IADD_S16", "IMUL_S16",
    "IOFFSET_S16", "IOFFSET_S16_LOAD", "IOFFSET_S16_STORE",
    "ARRAY_U16", "ARRAY_U16_LOAD", "ARRAY_U16_STORE",
    "LOCAL_U16", "LOCAL_U16_LOAD", "LOCAL_U16_STORE",
    "STATIC_U16", "STATIC_U16_LOAD", "STATIC_U16_STORE",
    "GLOBAL_U16", "GLOBAL_U16_LOAD", "GLOBAL_U16_STORE",
    "J", "JZ", "IEQ_JZ", "INE_JZ", "IGT_JZ", "IGE_JZ", "ILT_JZ", "ILE_JZ",
    "LEAVE",
}
_LEN3 = {
    "PUSH_CONST_U8_U8_U8", "NATIVE", "CALL", "PUSH_CONST_U24",
    "LOCAL_U24", "LOCAL_U24_LOAD", "LOCAL_U24_STORE",
    "GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE",
}
_LEN4 = {"PUSH_CONST_U32", "PUSH_CONST_F"}

_FIXED: dict[str, int] = {}
for _n in _LEN1:
    _FIXED[_n] = 1
for _n in _LEN2:
    _FIXED[_n] = 2
for _n in _LEN3:
    _FIXED[_n] = 3
for _n in _LEN4:
    _FIXED[_n] = 4

# Jump opcodes: 2-byte signed relative operand. Target is relative to the
# address *after* the 3-byte instruction (see Instruction.GetJumpOffset:
#   int16(operands) + Offset + 3).
JUMP_OPCODES = {"J", "JZ", "IEQ_JZ", "INE_JZ", "IGT_JZ", "IGE_JZ", "ILT_JZ", "ILE_JZ"}


# Operand "kind" per opcode -- the single source of truth shared by the
# disassembler (emit) and assembler (parse) so the two never drift.
#   none    - no operands
#   u8/u16/s16/u24/u32/f32 - a single scalar operand of that width
#   u8u8 / u8u8u8          - two/three u8 immediates
#   rel16   - a jump; textual operand is a label
#   call    - CALL; textual operand is a label or absolute 0xADDR
#   native  - NATIVE param,ret,index
#   enter   - ENTER params,vars[,"name"]
#   leave   - LEAVE params,returns
#   switch  - SWITCH value=>label, ...
_KIND_GROUPS: dict[str, set[str]] = {
    "u8": {
        "PUSH_CONST_U8", "ARRAY_U8", "ARRAY_U8_LOAD", "ARRAY_U8_STORE",
        "LOCAL_U8", "LOCAL_U8_LOAD", "LOCAL_U8_STORE",
        "STATIC_U8", "STATIC_U8_LOAD", "STATIC_U8_STORE",
        "IADD_U8", "IMUL_U8", "IOFFSET_U8", "IOFFSET_U8_LOAD", "IOFFSET_U8_STORE",
        "TEXT_LABEL_ASSIGN_STRING", "TEXT_LABEL_ASSIGN_INT",
        "TEXT_LABEL_APPEND_STRING", "TEXT_LABEL_APPEND_INT",
    },
    "u8u8": {"PUSH_CONST_U8_U8"},
    "u8u8u8": {"PUSH_CONST_U8_U8_U8"},
    "s16": {
        "PUSH_CONST_S16", "IADD_S16", "IMUL_S16",
        "IOFFSET_S16", "IOFFSET_S16_LOAD", "IOFFSET_S16_STORE",
    },
    "u16": {
        "ARRAY_U16", "ARRAY_U16_LOAD", "ARRAY_U16_STORE",
        "LOCAL_U16", "LOCAL_U16_LOAD", "LOCAL_U16_STORE",
        "STATIC_U16", "STATIC_U16_LOAD", "STATIC_U16_STORE",
        "GLOBAL_U16", "GLOBAL_U16_LOAD", "GLOBAL_U16_STORE",
    },
    "u24": {
        "LOCAL_U24", "LOCAL_U24_LOAD", "LOCAL_U24_STORE",
        "GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE", "PUSH_CONST_U24",
    },
    "u32": {"PUSH_CONST_U32"},
    "f32": {"PUSH_CONST_F"},
    "rel16": set(JUMP_OPCODES),
    "call": {"CALL"},
    "native": {"NATIVE"},
    "enter": {"ENTER"},
    "leave": {"LEAVE"},
    "switch": {"SWITCH"},
}

OPERAND_KIND: dict[str, str] = {}
for _name in OPCODE_NAMES:
    OPERAND_KIND[_name] = "none"
for _kind, _members in _KIND_GROUPS.items():
    for _name in _members:
        OPERAND_KIND[_name] = _kind


class DecodeError(ValueError):
    """Raised when the byte stream cannot be decoded as valid GTA5 bytecode."""


def operand_length(name: str, code: bytes, pos: int) -> int:
    """Return the number of *operand* bytes for the instruction at ``pos``.

    ``pos`` points at the opcode byte. ``ENTER`` and ``SWITCH`` are
    variable-length and peek into ``code``.
    """
    if name == "ENTER":
        # pcount(1) + vcount(2) + namelen(1) + name(namelen)
        namelen = code[pos + 4]
        return 4 + namelen
    if name == "SWITCH":
        # count(1) + count * (case value u32 + jump u16) = 1 + 6*count
        count = code[pos + 1]
        return 1 + 6 * count
    return _FIXED.get(name, 0)


def instruction_length(code: bytes, pos: int) -> int:
    """Total length (opcode + operands) of the instruction at ``pos``."""
    op = code[pos]
    if op >= len(OPCODE_NAMES):
        raise DecodeError(f"invalid GTA5 opcode 0x{op:02X} at offset {pos}")
    name = OPCODE_NAMES[op]
    return 1 + operand_length(name, code, pos)
