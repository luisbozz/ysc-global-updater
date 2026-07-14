"""Function segmentation over a disassembled GTA5 code section.

GTA5 scripts are a flat sequence of functions, each ``ENTER .. LEAVE`` (a
function may contain several ``LEAVE`` early-returns; the final one is its
epilogue). ``CALL`` targets are the byte offset of the callee's ``ENTER``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .model import Instruction


@dataclass
class Function:
    index: int          # sequential function number (0 == main)
    start_index: int    # instruction-list index of the ENTER
    end_index: int      # instruction-list index of the final LEAVE
    start_offset: int   # byte offset of the ENTER (== CALL target)
    end_offset: int     # byte offset just past the final LEAVE
    name: str
    params: int
    nvars: int
    returns: int


def iter_functions(instructions: list[Instruction]) -> Iterator[Function]:
    enters = [i for i, ins in enumerate(instructions) if ins.name == "ENTER"]
    n = len(instructions)
    for k, e in enumerate(enters):
        nxt = enters[k + 1] if k + 1 < len(enters) else n
        end = nxt - 1  # functions are packed back-to-back; last instr may be
        # LEAVE, a backward J (infinite loop), or absorbed NOP padding.
        leave_idx = None
        for j in range(end, e, -1):
            if instructions[j].name == "LEAVE":
                leave_idx = j
                break
        enter = instructions[e]
        end_ins = instructions[end]
        name = enter.enter_name or ("main" if e == 0 else f"func_{k}")
        yield Function(
            index=k,
            start_index=e,
            end_index=end,
            start_offset=enter.offset,
            end_offset=end_ins.offset + end_ins.length,
            name=name,
            params=enter.enter_params,
            nvars=enter.enter_vars,
            returns=instructions[leave_idx].leave_returns if leave_idx is not None else 0,
        )


def function_table(instructions: list[Instruction]) -> list[Function]:
    return list(iter_functions(instructions))


def by_offset(functions: list[Function]) -> dict[int, Function]:
    """Map ENTER byte offset -> Function (for resolving CALL targets)."""
    return {f.start_offset: f for f in functions}


def find_containing(functions: list[Function], offset: int) -> Function | None:
    """Return the function whose byte range contains ``offset`` (linear/bisect)."""
    lo, hi = 0, len(functions) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        f = functions[mid]
        if offset < f.start_offset:
            hi = mid - 1
        elif offset >= f.end_offset:
            lo = mid + 1
        else:
            return f
    return None
