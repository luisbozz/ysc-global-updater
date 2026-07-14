"""scrasm - a tiny GTA5 (RAGE) YSC bytecode assembler/disassembler.

Self-contained (pure stdlib) so it can later be lifted into its own repo.
Opcode table is authoritative: extracted from the GTA-V-Script-Decompiler
(``Instruction.cs`` enum + ``ScriptFile.cs`` operand-length switch). For GTA5
the opcode byte equals the enum ordinal (only RDR2 shuffles opcodes).
"""

from .opcodes import (
    OPCODE_NAMES,
    NAME_TO_BYTE,
    instruction_length,
    operand_length,
)
from .model import Instruction
from .disasm import disassemble, format_listing

__all__ = [
    "OPCODE_NAMES",
    "NAME_TO_BYTE",
    "instruction_length",
    "operand_length",
    "Instruction",
    "disassemble",
    "format_listing",
]
