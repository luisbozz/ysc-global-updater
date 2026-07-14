"""Two-pass assembler: symbolic ``.ysa`` text -> GTA5 YSC bytecode.

Pass 1 assigns a byte offset to every instruction and records label
positions (all instruction sizes are known without resolving labels).
Pass 2 encodes each instruction, resolving jump/switch/call labels.

Grammar (one item per line; ``;`` starts a comment):
    label:                     define a label at the current offset
    MNEMONIC operands          an instruction
Operands depend on the opcode's OPERAND_KIND (see opcodes.py), e.g.::
    ENTER 0, 3
    ENTER 2, 5, "myFunc"
    NATIVE 1, 1, 665           ; params, returns, native index
    GLOBAL_U24_LOAD 1826920
    JZ L_1A                    ; jump target is a label
    CALL 0x2296470             ; absolute callee address (or a label)
    PUSH_CONST_F 0x3F800000    ; raw float bits (or a decimal like 1.0)
    SWITCH 0=>L_A, 1=>L_B
"""

from __future__ import annotations

import struct

from .opcodes import NAME_TO_BYTE, OPERAND_KIND, DecodeError


class AsmError(ValueError):
    pass


def _pint(tok: str) -> int:
    return int(tok, 0)


def _split_args(s: str) -> list[str]:
    return [a.strip() for a in s.split(",") if a.strip()]


def _size(mnem: str, args: str) -> int:
    kind = OPERAND_KIND[mnem]
    if kind == "none":
        return 1
    if kind in ("u8",):
        return 2
    if kind in ("u8u8", "u16", "s16", "rel16", "leave"):
        return 3
    if kind in ("u8u8u8", "u24", "call", "native"):
        return 4
    if kind in ("u32", "f32"):
        return 5
    if kind == "enter":
        parts = _split_args(args)
        namelen = 0
        if len(parts) >= 3:
            third = parts[2].strip()
            if third.startswith("raw:"):
                namelen = len(bytes.fromhex(third[4:]))
            else:
                namelen = len(third.strip('"')) + 1  # include NUL terminator
        return 1 + 4 + namelen
    if kind == "switch":
        return 2 + 6 * len(_split_args(args))
    raise AsmError(f"unknown kind {kind} for {mnem}")


def _parse_float_bits(tok: str) -> int:
    if tok.lower().startswith("0x"):
        return int(tok, 16) & 0xFFFFFFFF
    return struct.unpack("<I", struct.pack("<f", float(tok)))[0]


class _Item:
    __slots__ = ("mnem", "args", "offset")

    def __init__(self, mnem: str, args: str, offset: int):
        self.mnem = mnem
        self.args = args
        self.offset = offset


def _tokenize(text: str):
    """Yield ('label', name) or ('insn', mnem, args) tuples."""
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        # optional leading "label:" (possibly followed by an instruction)
        while ":" in line.split(" ", 1)[0]:
            head, _, rest = line.partition(":")
            yield ("label", head.strip())
            line = rest.strip()
            if not line:
                break
        if not line:
            continue
        parts = line.split(None, 1)
        mnem = parts[0].upper()
        args = parts[1].strip() if len(parts) > 1 else ""
        yield ("insn", mnem, args)


def assemble_text(text: str, base: int = 0, natives: dict[str, int] | None = None,
                  funcs: dict[str, int] | None = None) -> bytes:
    # ---- pass 1: offsets + labels ----
    labels: dict[str, int] = {}
    items: list[_Item] = []
    off = base
    for tok in _tokenize(text):
        if tok[0] == "label":
            name = tok[1]
            if name in labels:
                raise AsmError(f"duplicate label {name!r}")
            labels[name] = off
        else:
            _, mnem, args = tok
            if mnem not in NAME_TO_BYTE:
                raise AsmError(f"unknown mnemonic {mnem!r}")
            items.append(_Item(mnem, args, off))
            off += _size(mnem, args)

    # ---- pass 2: encode ----
    out = bytearray()
    for it in items:
        out += _encode(it, labels, natives, funcs)
    return bytes(out)


def _resolve(tok: str, labels: dict[str, int]) -> int:
    return labels[tok] if tok in labels else _pint(tok)


def _encode(it: _Item, labels: dict[str, int], natives: dict[str, int] | None = None,
            funcs: dict[str, int] | None = None) -> bytes:
    mnem, args, offset = it.mnem, it.args, it.offset
    op = NAME_TO_BYTE[mnem]
    kind = OPERAND_KIND[mnem]

    if kind == "none":
        return bytes([op])
    if kind == "u8":
        return bytes([op, _pint(args) & 0xFF])
    if kind == "u8u8":
        a, b = _split_args(args)
        return bytes([op, _pint(a) & 0xFF, _pint(b) & 0xFF])
    if kind == "u8u8u8":
        a, b, c = _split_args(args)
        return bytes([op, _pint(a) & 0xFF, _pint(b) & 0xFF, _pint(c) & 0xFF])
    if kind in ("u16", "s16"):
        return bytes([op]) + (_pint(args) & 0xFFFF).to_bytes(2, "little")
    if kind == "u24":
        return bytes([op]) + (_pint(args) & 0xFFFFFF).to_bytes(3, "little")
    if kind == "u32":
        return bytes([op]) + (_pint(args) & 0xFFFFFFFF).to_bytes(4, "little")
    if kind == "f32":
        return bytes([op]) + (_parse_float_bits(args) & 0xFFFFFFFF).to_bytes(4, "little")
    if kind == "call":
        tok = args.strip()
        if tok.startswith("@"):
            name = tok[1:]
            if funcs and name in funcs:
                target = funcs[name]
            elif name in labels:
                target = labels[name]
            else:
                raise AsmError(f"unresolved call target @{name}")
        else:
            target = _resolve(tok, labels)
        return bytes([op]) + (target & 0xFFFFFF).to_bytes(3, "little")
    if kind == "rel16":
        target = _resolve(args.strip(), labels)
        rel = target - (offset + 3)
        if not -0x8000 <= rel <= 0x7FFF:
            raise AsmError(f"jump out of range ({rel}) at 0x{offset:X}")
        return bytes([op]) + (rel & 0xFFFF).to_bytes(2, "little")
    if kind == "native":
        parts = _split_args(args)
        p, r = _pint(parts[0]), _pint(parts[1])
        idxtok = parts[2].strip()
        if idxtok.startswith("@"):
            name = idxtok[1:]
            if natives is None or name not in natives:
                raise AsmError(f"unresolved native @{name}")
            idx = natives[name]
        else:
            idx = _pint(idxtok)
        return bytes([op, ((p & 0x3F) << 2) | (r & 0x3), (idx >> 8) & 0xFF, idx & 0xFF])
    if kind == "leave":
        p, r = (_pint(x) for x in _split_args(args))
        return bytes([op, p & 0xFF, r & 0xFF])
    if kind == "enter":
        parts = _split_args(args)
        p = _pint(parts[0]) & 0xFF
        v = _pint(parts[1]) & 0xFFFF
        name = b""
        if len(parts) >= 3:
            third = parts[2].strip()
            if third.startswith("raw:"):
                name = bytes.fromhex(third[4:])
            else:
                name = third.strip('"').encode("latin-1") + b"\x00"
        return bytes([op, p]) + v.to_bytes(2, "little") + bytes([len(name)]) + name
    if kind == "switch":
        cases = _split_args(args)
        body = bytearray([op, len(cases)])
        for i, case in enumerate(cases):
            if "=>" not in case:
                raise AsmError(f"bad switch case {case!r}")
            valstr, lbl = case.split("=>", 1)
            value = _pint(valstr.strip()) & 0xFFFFFFFF
            target = _resolve(lbl.strip(), labels)
            rel = target - (offset + 8 + 6 * i)
            if not -0x8000 <= rel <= 0x7FFF:
                raise AsmError(f"switch target out of range at 0x{offset:X}")
            body += value.to_bytes(4, "little") + (rel & 0xFFFF).to_bytes(2, "little")
        return bytes(body)
    raise AsmError(f"unhandled kind {kind} for {mnem}")
