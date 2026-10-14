# scrasm — GTA5 YSC bytecode assembler / disassembler

A tiny, pure-stdlib toolkit for reading, writing and **moving** GTA5 (RAGE)
YSC bytecode. Built to keep Xenvious' injected *customfuncs* patches working
across game updates — the same idea as the `offsets.ini` updater, one level
deeper (the bytecode itself).

It is self-contained (no third-party deps). Native name resolution data
(`natives.json` + `crossmap.txt`) ships bundled under `scrasm/data/`.

## What it does

| Module | Purpose |
|---|---|
| `opcodes.py` | Authoritative GTA5 opcode table (byte = enum ordinal), operand kinds/lengths. Ported from the GTA-V-Script-Decompiler. |
| `model.py` | `Instruction` + typed operand decoders (native index, jump target, u24 …). |
| `disasm.py` | `disassemble(bytes)` / `assemble(instructions)` — lossless. |
| `asm.py` | `assemble_text(.ysa)` — two-pass assembler with labels, `@NATIVE` and `@func` symbols. |
| `importer.py` | `to_ysa(bytes)` — raw bytecode → readable symbolic source. |
| `yscfull.py` | Parser for calamity-inc `*.ysc.full` decrypted dumps (code pages, native table). |
| `functions.py` | Function segmentation (`ENTER..LEAVE`) + address lookup. |
| `natives.py` | Native **index ↔ hash ↔ name** (rotate + crossmap + natives.json). |
| `funcsig.py` | Cross-version function matching by content fingerprint (3 tiers). |
| `crossbuild.py` | One script in two builds: function pairs, instruction alignment, string lookup. |

## The maintainability win

Instead of hand-flattening annotated hex into a `.txt` and pasting it into
`scrpatches.json`, a payload is now **readable source** with native names, jump
labels and internal-function labels:

```asm
fn1:
  ENTER 0, 2
  GLOBAL_U24_LOAD 1826922
  NATIVE 1, 1, @STREAMING::IS_MODEL_VALID
  INOT
  JZ L_239C51
  LEAVE 0, 0
L_239C51:
  ...
```

Sources live in `customfuncs/page/src/<edition>/*.ysa`, written for the build in
`src/<edition>/BUILD`. `customfuncs/page/build_page.py` assembles them (plus the
generated shape drawer and zone labels) into the "custom funcs in extra page" entries
of Xenvious' `scrpatches.json`; `customfuncs/page/relocate.py` moves them to another
build. xupdate runs both on a game update.

## Usage

```bash
# from scrpatches/
python3 -m unittest discover -s scrasm/tests -p 'test_*.py'   # run all tests
python3 scrasm/customfuncs/page/build_page.py --edition legacy           # report
python3 scrasm/customfuncs/page/build_page.py --edition legacy --write   # into Xenvious
python3 scrasm/customfuncs/page/relocate.py --script fm_lts_creator \
    --from 1.73-3889 --to 1.74-4000 --src scrasm/customfuncs/page/src/legacy/fm_lts_creator.ysa
```

`crossbuild.py` pairs one script's functions and instructions across two builds;
relocate.py and `../carry.py` build on it.

## Moving a payload to a new build

`customfuncs/page/relocate.py` rewrites a source for another build:

- **CALL** into the script: the function's new address via fingerprinting
  (strict, loose, positional; then neighbours and instruction similarity).
- **STRING** pushes: the same text in the new string table.
- **statics, globals, enum constants**: each place Rockstar's code uses the value is
  aligned with the same function in the new build; the operand there is a vote.
- **`; %key`** comments: moved by as much as that offsets.ini key moved.
- `@NATIVE` names stay symbolic, so native indices are resolved per build anyway.

## Validation

- Full round-trip (`disassemble` → `assemble`) is **byte-identical** over the
  entire code section of all 12 dumps (6 scripts × old/new) — millions of
  instructions.
- Native resolution matches the ground-truth annotations exactly (capture OLD).
- relocate.py moves all six creator payloads Legacy 1.73-3889 <-> Enhanced
  1.0.1158 (both ways) to exactly the hand-written sources of the other edition.

## Known limitations / next steps

- **Embedded strides**: a payload's field offsets and strides follow Rockstar's
  code only where Rockstar uses the same number; mark the others with `; %key`
  so they move with offsets.ini.
- `natives.json` / `crossmap.txt` are bundled under `scrasm/data/`.
