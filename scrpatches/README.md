# scrpatches — runtime bytecode patches

Xenvious patches the creator scripts' **bytecode** at runtime: dev mode, the cam
fix, the injected *custom functions*, and a dozen smaller behaviour tweaks. The
patch set lives in [`data/scrpatches.json`](data/scrpatches.json) — 43 entries
across 6 scripts.

Every GTA update moves the bytecode, so patterns stop matching. This directory
holds the tools to find out what broke, repair what can be repaired
automatically, and tell you exactly what is left.

> Separate problem from `offsets.ini`. Offsets are memory addresses the app
> reads; scrpatches are byte sequences the app writes. Same source dumps,
> independent toolchains. For offsets see the [main README](../README.md).

## After a game update

One command does the whole pass:

```bash
cd /opt/ysc-global-updater
./fetch_update.sh 1.73-3889                          # get the new dumps
python3 scrpatches/update_patches.py --new 1.73-3889
```

Five steps: check, repair what it can, drop markers that expired, repair the
injected payloads, list what needs a human. Nothing is written without asking,
and `data/scrpatches.json.bak` is written before the first change.

```bash
--dry-run      # show everything, write nothing
--yes          # accept every verified proposal without asking
--no-payloads  # skip the customfuncs repair
```

`--old` defaults to the build right before `--new`.

The individual tools stay available for detail work:

```bash
python3 scrpatches/check_patches.py --new 1.73-3889          # health check only
python3 scrpatches/doctor.py --new 1.73-3889                 # diagnose every break
python3 scrpatches/doctor.py --new 1.73-3889 --patch "cam fix"
python3 scrpatches/repair_scrpatches.py --new 1.73-3889      # payloads only
```

## What a patch entry looks like

```json
{
  "patch_name": "dev mode",
  "script_name": "fm_capture_creator",
  "pattern": "2D 00 02 00 00 71 2E ? ? 5D ? ? ?",
  "offset": 5,
  "bytes_to_patch": "72 2E 00 01",
  "dev": false
}
```

- `pattern` — an AOB signature, `?` matches any byte. It must match **exactly
  once** in the script, otherwise the app has no idea where to write.
- `offset` — how far past the match the write starts.
- `bytes_to_patch` — what gets written there. Its length also defines how far
  the patched **region** reaches, which matters when repairing.
- `values` — optional sub-patterns that *read* a value instead of writing one.
  A patch whose `values` entry breaks is broken too.
- `enabled` — set to `false` to park a patch; Xenvious skips it entirely.
  Absent means enabled.
- `derived_for` — bookkeeping written and removed by the tools, see below.

Wildcards are what make a pattern survive an update: operands (native indices,
jump targets, global offsets) shift constantly, opcodes do not.

## Three kinds of entry, three failure modes

| Kind | Count | Breaks when | Repair |
|---|---|---|---|
| Plain AOB patch | 32 | the instruction sequence changed | mostly automatic |
| `values` reader | 10 | same | manual |
| Injected `customfuncs` payload | 3 | always — it embeds native indices and call addresses | automatic |

## Reading the health check

| Status | Means | What you do |
|---|---|---|
| `OK` | 1 hit old, 1 hit new | nothing |
| `REDERIVED` | 1 hit new, marked `derived_for` | nothing |
| `DISABLED` | `"enabled": false` | nothing — parked on purpose |
| `BROKEN` | hits old, 0 hits new | re-derive the pattern |
| `AMBIG_NEW` | 1 old, several new | tighten it — the app would patch the wrong site |
| `AMBIG_OLD` | several old | the pattern was already fragile; tighten it |
| `NOT_IN_OLD` | 0 old | wrong `--old` build, or a dead pattern |

`AMBIG_NEW` is the dangerous one. `BROKEN` means the patch silently does
nothing; `AMBIG_NEW` means it may write to the wrong place.

### What the health check cannot tell you

It counts hits. It does not check *which* site a pattern hits.

A pattern can match exactly one site in each build and still point at unrelated
code, and that reads as a clean `OK`. Not hypothetical: while deriving the
`show stunt prop item cycle` fix below, an obvious-looking candidate scored
`old=1 new=1 OK` while matching `0x1DE1D5` in the old build instead of the
`0x1FF9F7` the original pattern meant.

`doctor.py` checks this, because it knows which site the original pattern hit.
`check_patches.py` cannot — it has no record of the intended site. A green run
means "every pattern is unique in both builds", never "every pattern is
correct".

## Repairing a broken pattern

`doctor.py` prints one page per break: where the match dies, what the pattern
pointed at, and candidate patterns it has already verified. There are two kinds
of break and it tells them apart.

### Case 1 — an operand moved

The common case. The instruction sequence is unchanged; a native index, a call
address or a field offset shifted. Wildcarding that operand fixes it, and the
tool finds the fix itself.

`cam fix`, all four broken scripts:

```
  Pattern   2D 00 02 00 00 28 E6 2E F0 96 2C 05 07 E7 2E 00 01
  Bricht ab 07   NATIVE (operand +2)

  Alter Block @0x106C4A
    +0   ENTER              00020000    stabil
    +5   PUSH_CONST_U32     E62EF096    stabil
    +10  NATIVE             0507E7      native index
    +14  LEAVE              0001        stabil

  Kandidaten
    ✓ 2D 00 02 00 00 28 E6 2E F0 96 2C ? ? ? 2E 00 01
      alt 1  neu 1  OK   trifft alt 0x106C4A (dieselbe Stelle)   offset 5 gueltig
```

The native *hash* (`E6 2E F0 96`) was already in the pattern and is stable
across builds; only the *index* moved.

Note what was verified: the candidate still hits `0x106C4A`, the site the
original pattern meant. Without that check the `OK` would be worth nothing.

### Case 2 — the instruction shape changed

The compiler emitted something structurally different. Wildcarding cannot help,
because the opcodes themselves differ. The tool then looks for the block by the
**strings it references**:

```
  Kandidaten
    ✗ kein Kandidat durch Wildcarding der Operanden.
    → Instruktionsform hat sich geaendert. Suche die Stelle ueber die Strings.

  Anker 'FMMC_PRP_PAOP' (String-Text ist versionsstabil, der Offset nicht)

  Neue Stelle @0x217286
    +0   PUSH_CONST_U8      33
    +2   GLOBAL_U24         000050
    +6   PUSH_CONST_U24     D28400
    +10  IOFFSET
    +11  IOFFSET_U8_LOAD    67
    +13  PUSH_CONST_U24     943E03

    ✓ 25 33 61 ? ? ? 64 ? ? ? 3F 41 ? 64 ? ? ?
      neu 1 (eindeutig), alt 0
      ACHTUNG: bytes_to_patch muss 36 -> 38 wachsen
      braucht derived_for: 1.73-3889
```

For `show stunt prop item cycle` the block did not disappear, it was rebuilt:

| | 1.71 | 1.73 |
|---|---|---|
| constant | `PUSH_CONST_U8 32` (50) | `PUSH_CONST_U8 33` (51) |
| field access | `IOFFSET_S16 5A7F` | `PUSH_CONST_U24 D28400` + `IOFFSET` |
| global | `GLOBAL_U24 000050` | `GLOBAL_U24 000050` (unchanged) |

The field offset outgrew a signed 16-bit immediate, so the compiler switched to
pushing it as a constant. One instruction became two: opcode **and** length
changed at the same position, which is why the pattern died there, and why the
region grew from 36 to 38 bytes.

### How the string anchor works

This is the mechanism behind Case 2, and it is the automated version of what a
human does in the decompiler — search for a label, not for bytes.

1. **Find a `PUSH_CONST_* ; STRING` pair** in the old block. That pair is the
   signature of a string reference: a value goes on the stack, then `STRING`
   says the value is an offset into the string table.
2. **Read the operand as a number.** `89 F1 02` little-endian = `0x02F189`.
3. **Resolve it in the old build.** The string table is paged like the code
   section; `scrasm/yscfull.py` assembles it and reads to the next nul byte:
   `FMMC_PRP_PAOP`.
4. **Find the same text in the new build.** It sits at `0x033E94` there. The
   offset moved by over 19,000 bytes; the text did not. That is the whole point
   — strings are game content, not compiler output.
5. **Build search bytes.** Same push opcode, the *new* offset, then `STRING`:
   `64 94 3E 03 66`. One hit in the new bytecode, at `0x217293`.
6. **Walk back to the block start.** Bytecode has no alignment, so many
   addresses before the hit decode cleanly by accident. Take the *nearest* one
   that lands exactly on the hit and starts with the same opcode as the old
   block — 13 bytes back, `0x217286`.

A text appearing more than once in the string table is not a usable anchor; the
tool moves on to the next string in the block.

Three inputs are needed for any of this: the **pattern** (locates the old site),
**`bytes_to_patch`** (how far the region reaches — the string reference is often
past the end of the pattern itself), and the **old dump** (the pattern has
wildcards; the real bytes only exist in the bytecode). If the old pattern no
longer matches either, there is no starting point and only the decompiler route
is left.

### Marking a re-derived pattern

A pattern built for the new shape cannot match the old build — the shapes are
incompatible. The check would report `NOT_IN_OLD`, or worse, a coincidental `OK`
against some unrelated old site.

```json
"pattern": "25 33 61 ? ? ? 64 ? ? ? 3F 41 ? 64 ? ? ?",
"derived_for": "1.73-3889"
```

`derived_for` makes the check judge that pattern on the new build alone: one hit
is `REDERIVED`, zero is `BROKEN`, several is `AMBIG_NEW`. Old hits are ignored
entirely, because they cannot mean anything for a pattern written against a
different shape.

**It cleans itself up.** `update_patches.py` writes the marker when it derives a
pattern, and removes it on a later run once two things hold: the marker no
longer names the build being checked, and the pattern now hits the old build
exactly once. That second condition is the real test — it means the normal rules
can classify the pattern again, so the marker is dead weight. You never write it
and never remove it.

A marker naming neither end of the current comparison is reported as `[WARN]`:
it silently does nothing, which usually means a typo or a skipped update.

It also works on a single `values` entry.

### Parking a patch you cannot fix

A broken patch is applied blind — Xenvious writes nothing, or writes to the
wrong place if the pattern became ambiguous. Until someone derives a new
pattern, park it:

```json
"enabled": false
```

Xenvious skips it in `ApplyPatches`, and the old pattern stays in the file to
derive from later. `update_patches.py` offers this for everything it could not
repair. The health check then reports `DISABLED` instead of `BROKEN`.

The C# side is `GTA.ScrPatches.enabled`, defaulting to `true` so the entries
without the field keep working. Changing it needs an **app rebuild** — the field
is read by the shipped binary, not by the JSON alone.

### Before you write it back

- Check `offset` still points at the intended byte. Widening a pattern at the
  front shifts everything after it.
- Check whether `bytes_to_patch` needs to change length. A shape change can grow
  or shrink the region; the tool says so when it can work it out.
- Prefer the general fix. A pattern that needed one new wildcard will usually
  need it again next build.

## The manual route — GTA V High Level Decompiler

When no candidate and no string anchor works, the block has to be found by hand.
The workflow, with screenshots, is documented at
<https://docs.xenvious.com/development/offsetupdates/#scrpatches>.

The short version:

1. Search the decompiled `.c` for a distinctive label the code uses — e.g.
   `MC_H_PRP_SLDO` in `fm_lts_creator`. Expect a single result.
2. Open that function in the GTA V High Level Decompiler and disassemble it.
3. Identify the block to patch, visually against the screenshot in the docs.
4. Read off the bytes and build the AOB: keep opcodes, wildcard operands.
5. Verify with `doctor.py` or `check_patches.py` before writing it back.

The `.c` files have no byte offsets — they are reconstructed source. They tell
you *which* code, never *which bytes*. The pattern always comes from
`disasm/<build>/*.ysc.full`.

## Repairing the injected payloads

```bash
python3 scrpatches/repair_scrpatches.py --new 1.73-3889
```

Per payload it updates native indices by name, relocates internal calls to the
new injection base, and re-resolves external R\* call addresses by function
fingerprinting (strict, positional, loose). Output:

- `reports/scrpatches.repaired.json` — the full patch set, payloads updated
- `reports/customfuncs_repair.txt` — what changed per payload

Read the report. Anything under `!! external calls UNRESOLVED`, `!! natives
missing` or `!! embedded strides needing review` needs a human before shipping.

The payloads also exist as readable assembly under
[`scrasm/customfuncs/src/*.ysa`](scrasm/customfuncs/src/) — regenerate with
`python3 gen_customfuncs_src.py`, which round-trip verifies that the source
re-assembles to the exact original bytes.

## Shipping

`reports/scrpatches.repaired.json` is a full drop-in replacement for the
`scrpatches.json` Xenvious ships — same entries, same order. Copy it once the
health check is clean and the repair report has no review items.

Unlike `offsets.ini` there is no merge step: the file has no hand-maintained
sections, so it is replaced wholesale.

It goes to `Xenvious/OfflineData/legacy/scrpatches.json`, next to the Enhanced
copy. `update_xenvious.py` does the copy as part of step 5; nothing takes
effect until the app is rebuilt, because OfflineData is compiled into the exe.

## Enhanced

Enhanced works the same way as Legacy now, but its bytecode dumps come from an
installed game instead of an upstream repository. Nobody publishes decrypted
`.ysc.full` for Enhanced; see the [main README](../README.md#enhanced-bytecode-dumps)
for `tools/extract_ysc.py` and the CodeWalker build it needs.

Each variant keeps its own patch set, so they can drift:

```text
data/scrpatches.json                     Legacy
data/scrpatches.enhanced.json            Enhanced
reports/scrpatches.repaired.json         Legacy, ready to ship
reports/scrpatches.enhanced.repaired.json  Enhanced, ready to ship
```

Every tool here takes `--patches` and `--old`/`--new`, so an Enhanced run is an
ordinary run against those paths:

```bash
python3 check_patches.py  --old 1.73-3889 --new enhanced-1.73-1158 \
    --patches data/scrpatches.enhanced.json
python3 update_patches.py --old 1.73-3889 --new enhanced-1.73-1158 \
    --patches data/scrpatches.enhanced.json
```

A variant's first build has no predecessor of its own, so the check compares
against the other game's newest build. That is not an approximation of the
bytecode — the two are compiled separately and differ throughout — but the two
games share their script *content*, which is enough to tell a pattern that
still matches from one that does not.

### What the first Enhanced run found

Most Legacy patterns survive the recompile untouched: **37 of 43 matched
Enhanced unchanged**, one needed an operand wildcarded (`cam fix` in
`fm_lts_creator`, the native index moved), and the five `precise templates`
were already parked on the Legacy side.

The injected payloads are the part that never survives. All three were rebuilt:
every native index changed (`IS_MODEL_VALID: 476 → 24`), every internal call was
relocated, and the four external calls were re-resolved.

That a pattern matches is not proof that it matches the *right* site — see
[Known gaps](#known-gaps). It is the same bar the Legacy set is held to.

## Known gaps

**Embedded struct field offsets are not migrated.** Injected payloads reference
globals directly, and the field offsets inside those references are the same
values `offsets.ini` migrates. The repair updates strides it can prove but not
these. The repair report lists every global a payload touches — cross-check them
against the offsets migration before shipping.

**`values` sub-patterns get no automatic repair.** Neither the operand path nor
the string anchor covers them; a broken `values` entry is always manual.

**The health check cannot verify intent.** It counts hits, so a pattern that
matches one wrong site per build reads as `OK`. See *What the health check
cannot tell you*.

**`fetch_update.sh` is the only source of `.ysc.full` dumps.** OpenIV gives raw
encrypted `.ysc`; decrypting them yourself is out of scope, so a build
calamity-inc has not published cannot be checked here.

## Status, 1.71-3586 → 1.73-3889

```
BROKEN     :   0
REDERIVED  :   2
DISABLED   :   5
OK         :  36
TOTAL      :  43
```

| Patch | Scripts | State |
|---|---|---|
| `cam fix` | capture, deathmatch, race, survival | repaired automatically, one pattern for all four |
| `show stunt prop item cycle` | capture, lts | re-derived via string anchor, `derived_for: 1.73-3889` |
| `precise templates` | all 5 | parked — main pattern is fine, `values#1` still needs deriving |

All 3 injected payloads repair cleanly, 0 need review.

## Tools

| File | Job |
|---|---|
| `update_patches.py` | the one entry point: check, repair, clean up, park |
| `check_patches.py` | health check: match every pattern against old and new bytecode |
| `doctor.py` | one diagnostic page per broken patch, with verified candidates |
| `repair_scrpatches.py` | migrate the injected customfuncs payloads to a new build |
| `gen_customfuncs_src.py` | payload bytecode → readable `.ysa`, round-trip verified |
| `scrasm/` | the YSC assembler/disassembler these build on — see [scrasm/README.md](scrasm/README.md) |
| `../tools/extract_ysc.py` | pulls decrypted dumps out of an installed game (Enhanced has no upstream) |

## Tests

```bash
cd scrpatches
python3 -m unittest discover -s scrasm/tests -p 'test_*.py'
```

19 tests: opcode round-trips, the assembler, `.ysc.full` parsing, native
resolution, payload repair.
