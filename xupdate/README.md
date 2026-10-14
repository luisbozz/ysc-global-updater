# xupdate: Xenvious on a new GTA build

One command takes everything Xenvious needs per game build to the new build:
offsets, script patches, the custom funcs payloads, prop lists, race vehicles and
prop-menu originals. What it cannot decide alone it puts in front of you, old code on
the left and new code on the right, with its guess filled in.

```bash
./xupdate.sh 1.74-4012              # Legacy
./xupdate.sh enhanced-1.74-1200     # Enhanced
./xupdate.sh 1.74-4012 --terminal   # review in the terminal instead of the browser
```

The source is always Xenvious itself (`Xenvious/OfflineData/<edition>/`); its
`offsets.ini` says in `[BUILD] label` which build it is for. Nothing outside
`reports/xupdate/<build>/` is written before the last step asks.

## Before you start

The new build's scripts, in two forms:

| What | Where | Legacy | Enhanced |
|---|---|---|---|
| decompiled `.c` | `scripts/<build>/` | `./fetch_update.sh <build> <ref>` | `tools/extract_ysc.py`, then `tools/decompile_ysc.py` |
| decrypted `.ysc.full` | `scrpatches/disasm/<build>/` | `./fetch_update.sh` (same call) | `tools/extract_ysc.py` (CodeWalker, from your game) |

The old build (the label in Xenvious' `offsets.ini`) has to be there as well.
Step 1 says what is missing and prints the commands.

## The steps

| # | Step | What happens |
|---|---|---|
| 1 | scripts | checks the files above |
| 2 | offsets | `tools/run_pipeline.py` migrates every key; each result is checked against the code around its use in both builds (same lines with numbers ignored). Agreement: done. Disagreement, or no result: review item. The `[i]` and `[n /*s*/]` of Xenvious' notation are kept (`paths.reshape`). |
| 3 | patches | every patch is carried by its function, not its bytes (`scrpatches/carry.py`): the function is found by its fingerprint, aligned instruction by instruction, the pattern rebuilt at the same instruction, and operands the patch copied from the game's code (fields, CALL targets, strings) updated. Changed patches are review items. |
| 4 | payloads | Legacy: the victim function is found again (`victims/layout.py`), giving `[SCRIPTVICTIM]`, the payload base and the stub patch. Enhanced: the payload goes to the first page behind the code. The `.ysa` sources are moved to the new build (`relocate.py`). |
| 5 | lists | `[OTHER]` prop lists, `race_vehicles.json` (with the ADLC vehicles), `mprops.json` |
| 6 | review | the browser page (or `--terminal`) |
| 7 | build | answers applied, payloads assembled, hooks pointed at the new labels |
| 8 | deploy | writes into Xenvious, runs `tools/builddata/gen.py` there, copies the moved sources to `page/src/<edition>/` |

Every step keeps its result in `reports/xupdate/<build>/`; running the command again
continues where it stopped, answers included. `--redo offsets` (patches, payloads,
lists) runs one step again.

## The review

Each card shows what the tool is unsure about:

- **left**: the old build, at the line the old value is used,
- **right**: the new build, at the line where it thinks the value is now,
- **bisher / Vorschlag**: old value and guess.

"sicher" means the guess comes from code that is the same around it (10 of 12
neighbour lines for offsets; for patches: function found by fingerprint, nothing left
unexplained). "Alle sicheren übernehmen" takes all of those at once.

Buttons: *Vorschlag übernehmen* (Enter), your own value (e), *abschalten* (patches:
`enabled=false`; payload problems: the payload and every patch that calls it, or GTA
crashes), and *VS Code* / *öffnen* to look at the whole file (the patch listings are
full disassemblies under `reports/xupdate/disasm/`). Keys: j/k next/previous.

A patch answer of your own is `pattern | offset | bytes`.

## After the run

```bash
git -C /opt/xenvious diff --stat     # OfflineData + Generated/*.g.cs
git status --short                   # page/src/<edition>/*.ysa moved to the new build
```

Build Xenvious, test every creator in game, then commit both repositories.

## Adding something Xenvious needs per build

A new extractor is a function in `xupdate/extract.py` that returns
`({OfflineData file: work file}, review items, report lines)`; add it to
`EXTRACTORS`. Data that belongs into offsets.ini is a step on the ini text like
`prop_lists`. If Xenvious reads it at start, generate C# for it in Xenvious'
`tools/builddata/gen.py` instead of loading a file.

## Checked so far

Without a real new build, the tool was run on 1.73-3889 -> 1.72-3788 (backwards) and
Legacy -> Enhanced 1.73:

- offsets: 1.73 -> 1.72 -> 1.73 with every guess accepted blindly gives 1209 of 1209
  paths back except 5, and 4 of those 5 were review items.
- patches: carried Legacy -> Enhanced, the site was right for 95 of 100 patches; the
  other 5 have no single counterpart (the editions' patch sets differ there).
- payloads: Legacy -> Enhanced and back, all six creators come out byte for byte
  like the hand-written sources.

Nothing of this was tested in game.
