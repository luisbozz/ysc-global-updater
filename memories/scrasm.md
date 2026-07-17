# ysc-global-updater / scrpatches / scrasm (repo facts)

## Commands (run from /opt/ysc-global-updater/scrpatches)
- Tests: `python3 -m unittest discover -s scrasm/tests -p 'test_*.py'` (19 tests, ~55s; NO pytest installed)
- Generate .ysa source: `python3 gen_customfuncs_src.py` (writes scrasm/customfuncs/src/*.ysa, round-trip verified)
- Repair customfuncs for new version: `python3 repair_scrpatches.py` (writes reports/scrpatches.repaired.json + customfuncs_repair.txt)
- Stage-1 health check (older tool): `python3 check_patches.py`
- Python 3.12, pure stdlib (no pip). Run modules with CWD=scrpatches so `scrasm` is top-level import.

## Key data locations
- data/scrpatches.json = SOURCE OF TRUTH, NEVER modify. Outputs go to reports/ (gitignored).
- disasm/*.ysc.full = decrypted dumps, 6 scripts x old/new (gitignored, ~50MB). old=cffba34 (1.70), new=senpai (1.72).
  scrpatches.json customfuncs are authored for OLD; repair migrates OLD->NEW.
- decompiler/GTA V Script Decompiler/Resources/{natives.json,crossmap.txt} = native name resolution data (scrasm reads these).

## Opcode table facts (GTA5)
- byte value == Opcode enum ordinal (0..130 valid). Only RDR2 shuffles (not us).
- NATIVE(2C): op0=param<<2|ret, op1-2=index BIG-ENDIAN. CALL(5D)/U24=3-byte LE. jump=2-byte signed rel, target=off+3+rel.
- ENTER(2D)=4+namelen operands. SWITCH=1+6*count. Native table: rotl64(raw,(code_len+idx)%64)->crossmap->natives.json.
- .ysc.full: RSC7Offset=0 (no RSC7 magic), ReadPointer=int32&0xFFFFFF, code in 0x4000 pages via CodeBlocksOffset@0x10, CodeLength@0x1C.

## Verified results (2026-07-14)
- Full disasm->asm round-trip byte-identical over all 12 dumps (millions of instr).
- customfuncs auto-repair: 3/3 payloads (lts/capture/race) fully repaired (natives, internal+external calls, strides
  26949->26968), 0 need review, final validation VALID (all refs valid in new script).
