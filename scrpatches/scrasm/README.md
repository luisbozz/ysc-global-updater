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

Sources live in `customfuncs/src/*.ysa` and the loop between them and the
shipped bytes runs both ways:

```
customfuncs/src/*.ysa  --build_customfuncs.py-->  data/scrpatches.json
                       <--gen_customfuncs_src.py--
```

Edit the source, build, deploy. `build_customfuncs.py --check` fails when the two
disagree, so the readable version cannot quietly stop being true.

## Usage

```bash
# from scrpatches/
python3 -m unittest discover -s scrasm/tests -p 'test_*.py'   # run all tests
python3 gen_customfuncs_src.py     # bytecode -> customfuncs/src/*.ysa (verified)
python3 build_customfuncs.py       # customfuncs/src/*.ysa -> report what differs
python3 build_customfuncs.py --check   # exit 1 on drift (runs in update_xenvious)
python3 build_customfuncs.py --write   # apply the sources to data/scrpatches.json
python3 build_customfuncs.py --check --target ../../Xenvious/Xenvious/OfflineData/legacy/scrpatches.json
                                    # check a DEPLOYED copy against the sources, not just data/
python3 repair_scrpatches.py       # migrate customfuncs to the new version -> reports/
```

`build_customfuncs.py` resolves the injection base from the target build's script
(the same unique anchor `repair_scrpatches.py` uses) and assembles against that
build's native table, so `@NS::NAME` stays symbolic in the source. Without
`--write` it touches nothing.

`--target` matters because `data/scrpatches.json` matching the sources does not
mean the deployed copy does. It once didn't: a same-build global rebase was
applied straight to `Xenvious/OfflineData/legacy/scrpatches.json`, fixed the
`GLOBAL_U24` operands, and left the payload's internal `CALL` targets pointing
tens of KB into unrelated code -- correct globals, corrupted control flow, and
a check against `data/` alone had no way to see it because `data/` was never
touched. `update_xenvious.py` now runs `--check --target` against the deploy
output as the last thing step 5 does, so that gap can't reopen silently. Any
edit made straight to a deployed `scrpatches.json` -- by hand, or with a
narrower one-off script -- should be followed by the same command before it's
trusted.

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
