# scrasm — GTA5 YSC bytecode assembler / disassembler

A tiny, pure-stdlib toolkit for reading, writing and **repairing** GTA5 (RAGE)
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
| `repair.py` | Repair a customfuncs payload for a new version (natives, calls, relocation). |

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

Generated (and round-trip verified) sources live in `customfuncs/src/*.ysa`.

## Usage

```bash
# from scrpatches/
python3 -m unittest discover -s scrasm/tests -p 'test_*.py'   # run all tests
python3 gen_customfuncs_src.py     # bytecode -> customfuncs/src/*.ysa (verified)
python3 repair_scrpatches.py       # migrate customfuncs to the new version -> reports/
```

`repair_scrpatches.py` writes `reports/scrpatches.repaired.json` (the patch set
with customfuncs updated for the new build) and `reports/customfuncs_repair.txt`
(what changed per payload). It never modifies `data/`.

## How repair works

A payload's byte layout is identical across versions (every volatile operand is
fixed-width), so operands are rewritten **in place**:

- **NATIVE** — old index → canonical hash → new index (per-script native tables).
- **internal CALL** — relocated to the new injection base (the unique anchor
  `2D 04 3A 00 00 38 03`).
- **external CALL** — new address via function fingerprinting:
  1. *strict* — params/returns + native hashes + globals + constants, unique both sides
  2. *loose* — params/returns + native-hash sequence, unique both sides
  3. *positional* — order-preserving gap fill between confident anchors (guarded
     by a structural similarity check)

## Validation

- Full round-trip (`disassemble` → `assemble`) is **byte-identical** over the
  entire code section of all 12 dumps (6 scripts × old/new) — millions of
  instructions.
- Native resolution matches the ground-truth annotations exactly (capture OLD).
- All 3 injected customfuncs payloads repair to the new version with 0 items
  needing manual review.

## Known limitations / next steps

- **Embedded strides**: payloads embed field offsets/array strides of the mission
  creator global (e.g. `ARRAY_U16 26949`). These are exactly the values migrated
  by `offsets.ini`; wiring that map in would make stride repair automatic too.
  They are currently *reported*, not rewritten.
- `natives.json` / `crossmap.txt` are bundled under `scrasm/data/`.
