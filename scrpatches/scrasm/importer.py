"""Importer: raw GTA5 bytecode -> readable ``.ysa`` symbolic source.

The emitted text is consumed 1:1 by :func:`scrasm.asm.assemble_text`, so
``assemble_text(to_ysa(code)) == code`` for any valid code stream. Jump and
switch targets become labels; scalar operands are printed literally; CALL
targets are printed as absolute addresses (symbolic function labels are a
later, optional layer).
"""

from __future__ import annotations

from .opcodes import OPERAND_KIND
from .disasm import disassemble, parse_hex
from .model import Instruction


def _label(offset: int) -> str:
    return f"L_{offset:X}"


def _enter_args(ins: Instruction) -> str:
    p, v, nl = ins.enter_params, ins.enter_vars, ins.enter_namelen
    if nl == 0:
        return f"{p}, {v}"
    nb = ins.operands[4:4 + nl]
    body = nb[:-1]
    if nb.endswith(b"\x00") and b"\x00" not in body and all(
        32 <= c < 127 for c in body
    ):
        return f'{p}, {v}, "{body.decode("latin-1")}"'
    return f"{p}, {v}, raw:{nb.hex().upper()}"


def _operand_text(ins: Instruction, label_names: dict[int, str],
                  resolver=None, funcs_by_addr: dict[int, str] | None = None) -> str:
    kind = OPERAND_KIND[ins.name]
    if kind == "none":
        return ""
    if kind == "u8":
        return str(ins.u8)
    if kind == "u8u8":
        return f"{ins.operands[0]}, {ins.operands[1]}"
    if kind == "u8u8u8":
        return f"{ins.operands[0]}, {ins.operands[1]}, {ins.operands[2]}"
    if kind == "u16":
        return str(ins.u16)
    if kind == "s16":
        return str(ins.s16)
    if kind == "u24":
        return str(ins.u24)
    if kind == "u32":
        return str(int.from_bytes(ins.operands, "little"))
    if kind == "f32":
        return f"0x{int.from_bytes(ins.operands, 'little'):08X}"
    if kind == "call":
        if funcs_by_addr and ins.call_target in funcs_by_addr:
            return f"@{funcs_by_addr[ins.call_target]}"
        return f"0x{ins.call_target:06X}"
    if kind == "rel16":
        return label_names[ins.jump_target]
    if kind == "native":
        if resolver is not None:
            return (f"{ins.native_param}, {ins.native_return}, "
                    f"@{resolver.name_at(ins.native_index)}")
        return f"{ins.native_param}, {ins.native_return}, {ins.native_index}"
    if kind == "leave":
        return f"{ins.leave_params}, {ins.leave_returns}"
    if kind == "enter":
        return _enter_args(ins)
    if kind == "switch":
        parts = [f"{v}=>{label_names[t]}" for v, t in ins.switch_cases()]
        return ", ".join(parts)
    return ""


def to_ysa(code: bytes, base: int = 0, comments: bool = False,
           resolver=None, funcs_by_addr: dict[int, str] | None = None,
           function_labels: bool = False) -> str:
    ins_list = disassemble(code, base=base)

    # collect all branch targets that need labels
    targets: set[int] = set()
    for ins in ins_list:
        if ins.is_jump:
            targets.add(ins.jump_target)
        elif ins.name == "SWITCH":
            for _v, t in ins.switch_cases():
                targets.add(t)
    label_names = {t: _label(t) for t in targets}

    # optionally label each contained function so internal CALLs become symbolic
    fbaddr = dict(funcs_by_addr or {})
    if function_labels:
        k = 0
        for ins in ins_list:
            if ins.name == "ENTER":
                fbaddr.setdefault(ins.offset, f"fn{k}")
                k += 1

    lines: list[str] = []
    for ins in ins_list:
        if ins.offset in label_names:
            lines.append(f"{label_names[ins.offset]}:")
        if function_labels and ins.name == "ENTER" and ins.offset in fbaddr:
            lines.append(f"{fbaddr[ins.offset]}:")
        args = _operand_text(ins, label_names, resolver, fbaddr)
        text = f"  {ins.name}" + (f" {args}" if args else "")
        if comments:
            text = f"{text:<44} ; 0x{ins.offset:X}  {ins.hexbytes()}"
        lines.append(text)
    return "\n".join(lines) + "\n"


def import_hex(hexstr: str, base: int = 0, comments: bool = False,
               resolver=None, funcs_by_addr: dict[int, str] | None = None,
               function_labels: bool = False) -> str:
    return to_ysa(parse_hex(hexstr), base=base, comments=comments,
                  resolver=resolver, funcs_by_addr=funcs_by_addr,
                  function_labels=function_labels)
