# SCRPATCHES (neues thema, baut auf offset-updater auf)

## Worum es geht
Xenvious patcht zur LAUFZEIT den YSC-script-BYTECODE im GTA-speicher, um creator-features
freizuschalten (80 actors, dev mode, precise templates, dont force plyl/kill-rule, caps-lock-switch...).
NOP-t instruktionen aus (00-bytes) oder aendert einzelne bytes.

## Bausteine (Xenvious C#)
- ScrProgramScanner.cs: bildet rage::scrProgram nach.
  GetScrProgramByName(name): script-registry ab GTA.Offsets.Editor.scrProgram_addy, count bei +0x18,
   eintraege 0x10 gross, hash bei +0x0C, programPtr=*entry. joaat(name)-match.
  scrProgram-struct (GTAV-Classes hpp, size 0x80): +0x10 m_code_blocks, +0x18 m_hash, +0x1C m_code_size.
   bytecode in 0x4000-byte seiten. num_pages=(code_size+0x3FFF)>>14. letzte seite=code_size&0x3FFF.
  ScanScrProgramForPattern: AOB-scan (pattern mit ? wildcards) ueber die seiten.
- ScrPatchesRunner.cs: endlosschleife, ApplyPatches. pro patch: script holen -> pattern scannen ->
   an fundadresse+offset bytes_to_patch schreiben (orig sichern, appliedPatches-set). dynamische values:
   {{ id }} platzhalter = an 2. pattern bytes_to_read bytes lesen + einsetzen.
- GTA.cs klassen: ScrPatches{patch_name,script_name,pattern,offset,bytes_to_patch,original_bytes,values[],dev}
   ScrPatchValue{id,pattern,offset,bytes_to_read}.

## Daten (kopiert nach ysc-global-updater/scrpatches/data/)
- scrpatches.json: 43 patches (10 mit values). ziele: lts13/capture12/race7/survival5/dm5/launcher1.
- scrpatchesdev.json: leer.
- scrcustomfuncs_capture.txt / _lts.txt: LEGENDE byte-seq -> bedeutung.
   z.b. "2C 05 02 99 = NATIVE STREAMING::IS_MODEL_VALID", "5D 96 0A 23 = CALL func_4345".
   opcodes: 2D=enter, 2C=native, 5D=call, 61=global, 46=array, 38/39=push, 2E=leave, 71/72=set/get.

## USER-METHODE (docs.xenvious.com/development/offsetupdates/#scrpatches) - VALIDIERT
Manuelles vorgehen pro patch:
1. SEMANTISCHER ANKER: eindeutiger STRING (MC_H_PRP_SLDO, WEAPON_PISTOL) im decompilierten script,
   ODER konkrete FUNKTION (race func_12094).
2. anker in NEW script finden -> enthaltende funktion.
3. funktion im "GTA V High Level Decompiler" (bytecode-disassembler) disassemblieren.
4. exakte instruktionen zum NOPen (screenshot) bzw byte-change identifizieren.
VALIDIERT gegen unsere scripts: MC_H_PRP_SLDO 1x in old+new lts (func_2588(...,45,"MC_H_PRP_SLDO",0)).
 WEAPON_PISTOL erste=func_4831(joaat("weapon_pistol")). caps-lock: pattern 5D ? ? ? 55 04 00 72 2E 01 01,
 offset29->D9 (doku: "offset still 29, replace D9 for CAPS LOCK"). screenshots NICHT im text-extrakt sichtbar.

## WARUM patterns brechen / ZIEL
wildcards (?) fangen operand-shifts (native/global/func-indizes) ab. patterns brechen wenn ROCKSTAR
die instruktions-SEQUENZ der funktion aendert (add/remove/reorder). dann muss der anker neu gefunden +
bytecode neu disassembliert werden. ZIEL: tool das scrpatches-patterns beim versionswechsel (halb)automatisch
migriert - via stabile anker (strings/natives/func-rollen) wie beim offset-updater.

## OFFENE FRAGE (an user gestellt)
patterns sind BYTECODE, wir haben nur DECOMPILED scripts (old/new). fuer AOB-neuableitung brauchen wir
entweder rohe YSC-BYTECODE-dumps (alt/neu) ODER bytecode-encoder. -> klaeren welche datenbasis.

## DATENLAGE (user-antwort 2026-07-14)
- NEUE .ysc: /mnt/c/Users/luisg/Downloads/scripts/*.ysc (10 files: capture/dm/lts/race/survival creator +
  mission_controller(_2020)/public_mission_controller/public_mission_creator/fm_mission_creator(137b stub)).
  FORMAT: RSC7 (magic 5253 4337) + version 0x0C. header 16 byte. payload VERSCHLUESSELT:
  entropie 7.997 bit/byte, 0 ECB-duplikate, (len-16)%16=11 (nicht block-aligned -> nur volle bloecke verschluesselt,
  tail plain) -> GTA5 NG/AES-verschluesselt, DAHINTER deflate. reines zlib.decompress(raw[16:]) schlaegt fehl.
- ALTE .ysc: EXISTIEREN NICHT. alt nur als DECOMPILIERT (.c) via calamity-inc github raw verfuegbar
  (raw.githubusercontent.com/calamity-inc/GTA-V-Decompiled-Scripts/<commit>/decompiled_scripts/<script>.c).
- habe schon: old/ 7 .c, new/ 8 .c decompiliert.
- KEIN pip/pycryptodome. entschluesselung braeuchte pure-python-AES + GTA5-keys (NG-tabellen falls title-update).
- USER-DECOMPILER ("GTA V High Level Decompiler") entschluesselt+disassembliert die .ysc bereits (-> new/.c kamen daher).
  -> sauberste eingabe waere BYTECODE/DISASSEMBLY-EXPORT aus dem tool statt selbst entschluesseln.

## PLAN (2-stufig, an user vorgeschlagen)
STUFE 1 (sofort, ohne entschluesselung): patch-health-check. pro patch anker(string/func) in ALT+NEU .c finden,
  umgebende funktion diffen -> melden welche der 43 patches strukturell brachen. wildcards ueberleben operand-shifts.
STUFE 2 (braucht bytecode): pattern-bytes neu erzeugen -> decompiler-disassembly-export ODER ich baue ysc-entschluesselung.

## DURCHBRUCH (2026-07-14): .ysc.full von calamity-inc = ENTSCHLUESSELTER bytecode!
KEINE eigene entschluesselung noetig. calamity-inc repo hat .ysc.full = full memory-dump (RSC7 entschluesselt+entpackt):
  ALT: github calamity-inc/GTA-V-Decompiled-Scripts commit cffba34.../scripts/<script>_ysc/<script>.ysc.full
  NEU: gleicher pfad aber branch "senpai".
  raw-url: raw.githubusercontent.com/calamity-inc/GTA-V-Decompiled-Scripts/<ref>/scripts/<script>_ysc/<script>.ysc.full
  ENTHAELT: klartext-strings (MC_H_PRP_SLDO 1x) UND kompilierten bytecode. binaer, ~3.5MB. header sieht aus wie
  in-memory scrProgram (pointer-werte am anfang). heruntergeladen nach scrpatches/disasm/.
AOB-MATCHING VALIDIERT (scrpatches/disasm test): pattern "? "=wildcard-byte, re.escape sonst, re.DOTALL.
  ALLE 12 capture-patterns matchen EXAKT 1x im alten .full. im neuen: 10 ueberleben (1x), 2 GEBROCHEN (0 treffer):
  "show stunt prop item cycle" + "cam fix". -> das IST der patch-health-check, funktioniert.
CAVEAT: senpai-branch muss == user's ziel-version (1.72) sein. sonst user's .ysc entschluesseln (blocker) oder
  senpai-.full gegen unsere new/.c gegenpruefen (gleiche strings/globals = gleiche version).
NAECHSTE SCHRITTE: alle .full (alt+neu) fuer die 6 gepatchten scripts (lts/capture/race/survival/dm/launcher) laden,
  health-check ueber alle 43 patches -> broken-liste. dann STUFE 2: broken patterns auto-neu-ableiten via string-anker
  (patch-location -> naechster string-ref -> in new finden -> neuen bytecode-bereich extrahieren -> neues pattern).

## STUFE 1 ERLEDIGT (2026-07-14): scrpatches/check_patches.py
tool: aob_to_regex(pattern) (? -> ., re.escape, DOTALL). _fetch(script,ref,tag) laedt .ysc.full von calamity-inc
  (curl) + cacht in scrpatches/disasm/. classify(n_old,n_new): OK(1/1)/BROKEN(>=1/0)/AMBIG_NEW(1/>1)/AMBIG_OLD(>1)/
  NOT_IN_OLD(0). prueft AUCH values-subpatterns (broken value -> patch BROKEN). refs: OLD=cffba34, NEW=senpai.
  --report-json, --only-issues. alle 12 .full (6 scripts x2) in disasm/ (~50MB, in .gitignore).
ERGEBNIS: 43 patches -> 34 OK, 9 BROKEN, 0 NOT_IN_OLD (=version stimmt, cffba34=alt/senpai=ziel bestaetigt).
  BROKEN: "show stunt prop item cycle" (capture+lts, haupt-pattern weg), "cam fix" (capture+survival, weg),
  "precise templates" (capture/lts/race/survival/dm = 5x, haupt-pattern lebt aber values[0] val#1 subpattern kaputt).
  cam fix lebt in lts/race/dm, bricht nur capture/survival. .gitignore hat scrpatches/disasm+reports.
STUFE 2 OFFEN: die 9 broken auto-neu-ableiten. ansatz: alte patch-stelle -> naechster stabiler string-ref davor/danach
  -> im neuen .full finden -> analogen bytecode-block extrahieren -> neues pattern (+ ggf value-pattern) erzeugen.

## STUFE-2 DIAGNOSE (2026-07-14): WARUM die 3 typen brechen
cam fix (fully-fixed pattern, KEINE wildcards): bricht weil NATIVE-INDEX sich verschob.
  alt "2D 00 02 00 00 28 E6 2E F0 96 2C 05 02 7E 2E 00 01" (2C 05 02 7E = NATIVE-aufruf, index 0x027E) -> 0 in neu.
  native-index gewildcardet "2C 05 ? ?" -> 1 treffer neu (eindeutig!). fix: native-idx wildcarden ODER via
  native-table hash aufloesen (0x027E -> hash -> neuer index). native-indizes wandern jedes update (table-reorder).
show stunt prop (capture): props-global 61 00 00 50 = Global_5242880 existiert (915x in neu), aber die instruktions-
  sequenz "25 32 61 .. 46 .. 41 .." aendert sich (props-STRIDE 163->165 aendert die ARRAY-opcode-kodierung 0x46).
  user hatte recht (global/array-teil). braucht disasm + stabilen anker (string/global) zum neu-ableiten.
precise templates value "64 ? ? ? 66 37 03 68 ? 38 00 47 ? ? 25 3A": auch mit heavy-wildcard 0 treffer -> instruktions-
  sequenz substantiell geaendert. braucht disasm + anker-neuableitung.
=> 3 bruch-klassen: (a) native-idx-shift (trivial: wildcard/native-table), (b) global-array-struct-change (stride),
   (c) instruktions-change. opcodes stabil, volatile operanden = native-idx/global-idx/func-addr -> die gehoeren gewildcardet.

## SC-CL (NativeFunction) EINORDNUNG
ist ein Clang-COMPILER (source->YSC bytecode) fuer GTA4/5/RDR/RDR2. hat -emit-asm (disasm) + opcode-encoding +
native-translation-tables. WERT fuer uns: referenz fuer OPCODE-TABELLE (instr->operand-laenge) + native hash<->index.
ABER: bauen braucht LLVM 14 (schwer, stunden) - NICHT noetig. GTA5-opcode-tabelle ist bekannt/klein (~127 opcodes),
  kann ich hardcoden. echter stufe-2-enabler = eigener python-DISASSEMBLER (opcode-tabelle) + native/string-tabellen
  aus dem .full lesen. dann: (a) native-idx-breaks trivial fixen, (b) struct-breaks via anker neu-ableiten,
  (c) optional ALLE patterns robuster machen (volatile operanden gewildcardet by construction).

## SCHLACHTPLAN STUFE 2 (2026-07-14, user schlaeft, autonom)
DECOMPILER: user's GTA-V-Script-Decompiler kopiert -> scrpatches/decompiler/ (ohne .git/packages/bin/obj).
  .NET SDK-style, UseWindowsForms=true + OutputType WinExe UND Exe (dual). kern-klassen (platform-neutral, OHNE GUI):
  ScriptFile.cs (ysc-parse), ScriptHeaders.cs (RSC7), Instruction.cs (OPCODE-tabelle/disasm), Disassembly.cs,
  NativeDB.cs/NativeTables.cs/Crossmap.cs (native hash<->index), StringTable.cs, Hashes.cs, IO.cs (decrypt/decompress),
  Program.cs (entry, Main da!), MainForm.cs (GUI - NICHT auf linux). WinForms laeuft NICHT auf linux, kern schon.
AUFGABE: decompiler headless/scriptbar unter WSL (dotnet) -> damit die 9 broken scrpatches auto-neu-ableiten.
SCHRITTE:
 1. dotnet SDK in WSL pruefen/installieren. projekt-framework bestaetigen.
 2. headless-console-projekt aus kern (ScriptFile/Instruction/Disassembly/NativeDB, OHNE WinForms/MainForm).
    .ysc rein -> disassembly + native-tabelle + string-tabelle als JSON raus. (evtl Program.cs hat schon batch-mode)
 3. build + smoke-test gegen user's NEUE .ysc (/mnt/c/Users/luisg/Downloads/scripts/*.ysc). gegen calamity-inc .full
    gegenpruefen (gleiche strings/opcodes) -> version + korrektheit.
 4. STUFE-2-REPARATEUR (python): pro broken-patch alten block disassemblieren -> stabiler anker (native-HASH via
    native-table / string-ref / global-nr) -> im neuen disasm finden -> neue sequenz -> pattern regenerieren,
    volatile operanden (native-idx/global-idx/func-addr) AUTO-gewildcardet. inkl value-subpatterns.
 5. reihenfolge: cam fix (native-idx, trivial) -> show stunt prop -> precise templates.
 6. verifikation: regenerierte patterns durch scrpatches/check_patches.py -> alle 9 wieder OK (1 treffer neu).
 7. output: neue scrpatches.json nach reports/ (original data/scrpatches.json unangetastet).
CONSTRAINTS: nicht committen. original scrpatches.json nicht aendern (nach reports/). .full/ysc in .gitignore.
BROKEN (9): show stunt prop(capture,lts), cam fix(capture,survival), precise templates value#1(capture/lts/race/survival/dm).

## TODO 4 (user 2026-07-14): "nop some func for custom funcs" (die langen bytes_to_patch) - BEWERTET
WAS: KEIN nop. INJIZIERT eigene xenvious-funktionen als YSC-bytecode (ueberschreibt opfer-funktion, pattern
  "2D 04 3A 00 00 38 03" = ENTER 4 params/58 vars, findet die sacrificial func). ~15 eintraege category=customfuncs.
  scrcustomfuncs_lts.txt/_capture.txt = der QUELLCODE dieser funktionen (annotierte disasm + kompilierte bytes +
  native/func-LEGENDE). funktionen: custom dimension(GET_MODEL_DIMENSIONS), team-color(clrovr), draw playzone/gang chase
  (HUD colors), get hovered model, check-if-run dispatcher.
WARUM SCHWERSTE: die injizierten bytes referenzieren 4 volatile dinge:
  (a) NATIVES per index (2C 05 02 99=IS_MODEL_VALID) -> index wandert jedes update.
  (b) SCRIPT-FUNCS per adresse (5D 96 0A 23=func_4345=addr 0x230A96) -> adresse wandert.
  (c) GLOBALS: xenvious-eigene (1826920-22) stabil; mission/creator-globals(4718592/4980736)+deren feld-offsets/strides
      wandern - UND das sind DIESELBEN werte die wir in offsets.ini migrieren! z.b. 49 45 69=ARRAY_U16 26949 = team-stride,
      den wir schon auf 26968 migriert haben!! -> assembler kann unsere offset-migration wiederverwenden.
  (d) INTERNE cross-refs zwischen den custom funcs (5D B1 DF 2A) -> haengen von injektions-adresse ab, neu berechnen.
BLIND SPOT stufe1: health-check sagt diese sind OK (find-pattern 2D 04 3A.. matcht noch), ABER das ist FALSCH-positiv -
  nur die FIND-stelle stimmt, die injizierten PAYLOAD-bytes sind stale. checker prueft payload nicht.
USER HAT RICHTIGE IDEE: template-notation in den files: "4F {{ LOCAL:Local_8321 }} .. {{ NATIVE:DOES_ENTITY_EXIST }}".
BEWERTUNG: eigener MINI-ASSEMBLER noetig: symbolische quelle (natives per name, funcs per name/hash, globals per nummer,
  cross-refs per label) -> beim update jedes symbol zur neuen byte-kodierung aufloesen -> neue bytes_to_patch. braucht
  native-tabelle + func-aufloesung aus decompiler (dieselbe infra wie TODO 1-2) + offset-migration fuer eingebettete strides.
  GROESSER als die 9, aber machbar. files haben "rumschwirrende gedanken"/experimente/falsche adressen, aber bytes+legende klar.
EMPFEHLUNG TIMING: TODO 4, NACH basis-reparatur (1-3) weil gleiche decompiler-infra + assembler obendrauf. eigener baustein.

## TODO 4 AUTHORING-IDEE (user 2026-07-14): "will es geiler maintainen, nicht in txt tackern"
IST-FORMAT der scrcustomfuncs_*.txt: hand-geschriebenes assembly-listing (links rohe hex-bytes, rechts
  mnemonic+kommentar) + darunter geflachte 1-zeilen-hex-blobs die in scrpatches.json bytes_to_patch kopiert werden.
5 SCHMERZPUNKTE: (1) manuelles flatten listing->1-zeile->JSON. (2) CALL-adressen (5D xx xx xx LE) per hand berechnet -
  wenn eine func laenger wird verschieben sich ALLE downstream-adressen. (3) native-idx per hand (2C 05 02 99).
  (4) jump-deltas per hand (56 5F 00=JZ+95, 55 68 FF=J-152). (5) DIESELBE logische func 3x geschrieben (race/lts/capture).
IDEE = MINI-YSC-ASSEMBLER: symbolische .ysa-quelle (labels, native BY NAME, call @label, global Nr, stride aus
  offsets.ini) -> 2-pass assembler (pass1 groessen+label-adressen, pass2 emit+resolve) -> bytes_to_patch nach reports/.
  KEIN RSC7-repack noetig (nur runtime-memory-patch an bekannter injektions-basis vom 2D 04 3A.. anchor).
RESOLVE-SPLIT: deterministisch(assembler): eigene-instr/labels/jumps/natives-by-name/globals/strides-aus-offsets.ini.
  needs-new-disasm(stufe2-tech): existierende R*-func-adressen (anker via deren natives/strings) + static-indizes.
BOOTSTRAP-KILLER: importer der die JETZIGEN roh-blobs + decompiler-tabellen -> initiale .ysa-quelle generiert
  (nicht 6 funcs x3 scripts neu tippen). + disasm-verify-loop (assemblierte bytes zurueck-disasm, gegen quelle diffen).
AMBITIONS-TIERS: C=nur {{NATIVE:}}/{{FUNC:}}/{{STRIDE:}} platzhalter in roh-hex (billig, fixt nur update nicht authoring).
  B=mnemonic-source (was sie eh schon in kommentar-spalte tippen) + assembler. A=voll: module + per-target-params,
  1 quelle kompiliert lts+capture+race + jede zukuenftige version. EMPFEHLUNG: B->A. evtl eigenes repo (scrasm).
STATUS: nur IDEE praesentiert, user entscheidet scope (eigenes projekt?) + tier. noch NICHT gebaut.

## OVERNIGHT-AUFTRAG (user 2026-07-14 "gehe schlafen, alles dir ueberlassen"): ZIELE fuer morgen frueh
AUTONOM bauen, verifiziert, offline. fundament das BEIDE freischaltet (9 broken + customfuncs-assembler).
BAUEN nach scrpatches/scrasm/ (self-contained, spaeter abtrennbar als eigenes repo). pure stdlib, KEIN pip.
M1: YSC-opcode-engine (python) DISASSEMBLER. opcode-tabelle AUTHORITATIV aus decompiler Instruction.cs ziehen
   (NICHT aus gedaechtnis!). scrpatches/decompiler/... Instruction.cs hat opcode->operand-laenge exakt.
M2: ROUND-TRIP beweis: jeder customfuncs bytes_to_patch blob (lts/capture/race aus data/scrpatches.json)
   disasm -> reasm BYTE-IDENTISCH. unittest gruen. harter korrektheitsbeweis auf echten daten.
M3: symbolische .ysa-sprache + 2-pass-assembler (labels, native BY-NAME, call @label, global Nr, stride<-offsets.ini).
M4: bootstrap-importer: die 6 funcs (model-dim/clrovr/playzone/gangchase/hovered/dispatcher) aus roh-blobs -> .ysa quelle.
M5: health-check blind-spot fix: customfuncs-payload ECHT pruefen (payload disasm, volatile refs aufloesen/flaggen)
   statt falsch-OK. report nach reports/.
M6 STRETCH zeitgeboxt: C#-decompiler headless WSL-build -> native-table/func-addr export. sonst fallback:
   tabellen aus .ysc.full in python lesen. NICHT festbeissen.
LEITPLANKEN: kein commit. data/scrpatches.json + offsets.ini unangetastet (output reports/ bzw scrpatches/scrasm/).
   .full/.ysc in .gitignore. scrasm self-contained (keine abhaengigkeit auf rest).
REIHENFOLGE: instruction.cs lesen -> opcode-tab -> disasm -> round-trip-test (M1/M2) -> asm+.ysa (M3) ->
   importer (M4) -> health-check (M5) -> stretch dotnet (M6). bei blocker: nicht bruteforcen, alternative.

## FORTSCHRITT (2026-07-14 nacht, autonom)
M1+M2 FERTIG & HART VERIFIZIERT. paket scrpatches/scrasm/ (pure stdlib):
  opcodes.py = AUTHORITATIVE opcode-tab (byte==enum-ordinal fuer GTA5, aus decompiler Instruction.cs).
    0..130 gueltig. operand-laengen aus ScriptFile.cs switch. ENTER=4+namelen, SWITCH=1+6*count.
    NATIVE: op0=param<<2|ret, op1-2=index BIG-ENDIAN. CALL/U24=3byte LE. jump=2byte signed rel, target=off+3+rel.
  model.py = Instruction dataclass (offset,op,name,operands) + decoder-properties + describe().
  disasm.py = disassemble(code,base)->list[Instruction], assemble(ins)->bytes (lossless), format_listing, parse_hex.
  yscfull.py = .ysc.full parser (PORT von ScriptHeaders.cs+IO.cs: ReadPointer=int32&0xFFFFFF, RSC7Offset=0 fuer .full,
    code in 0x4000-pages via CodeBlocksOffset@0x10, CodeLength@0x1C, code_blocks=(len+0x3FFF)>>14). parse()->YscFull.
  functions.py = iter_functions (ENTER-liste, funcs tilen back-to-back, letzte instr kann LEAVE/J/NOP sein;
    repraesentativer LEAVE nur fuer return-count). find_containing (bisect by byte-offset). CALL-target==ENTER-offset.
  tests/test_roundtrip.py: customfuncs-blobs disasm->asm BYTE-IDENTISCH (partielle overwrites wie D9 caps-lock geskippt).
  tests/test_yscfull.py: ALLE 12 .ysc.full (6 scripts x alt/neu) VOLL-round-trip byte-identisch + segmentierung.
VERIFIZIERT: capture.new 2913982 bytes=1196073 instr roundtrip OK; capture.old 2809736=1153791 OK. alle 12 gruen.
  = opcode-tab korrekt ueber MILLIONEN instruktionen alt+neu. LESSON: funcs NICHT auf LEAVE trimmen (letzte instr
  kann backward-J/loop sein); NOP-padding wird in letzte func absorbiert. RSC7Offset=0 fuer .full (magic!=RSC7).
NEXT: M3 asm.py (2-pass, labels/jumps aus OPERAND_KIND), M4 importer.py (blob->.ysa), symbolic round-trip test,
  M5 health-check payload, M6 stretch dotnet. natives.json + Crossmap.cs + NativeTables.cs da fuer native-resolution.

## DURCHBRUCH 2 (2026-07-14 nacht): M3+native-resolution FERTIG, AUTO-REPARATUR bewiesen
M3 asm.py: assemble_text(text,base,natives,funcs) 2-pass. OPERAND_KIND dict in opcodes.py = single source of truth
  (none/u8/u16/s16/u24/u32/f32/rel16/call/native/enter/leave/switch). importer.py to_ysa(code,resolver,funcs_by_addr)
  emit .ysa (jumps->labels L_xx, native->@NAME wenn resolver, call->0xADDR). symbol @NAME: assembler loest via natives-dict.
  SWITCH-encoding: rel=target-(off+8+6i). tests/test_asm.py: customfuncs + handwritten + 152 sampled real funcs (32 switch) ALLE OK.
natives.py: NativeResolver. rotl64(raw_u64, (code_length+index)%64) -> crossmap(older->newer) -> natives.json(hash->ns::name).
  crossmap.txt format "NEWER:OLDER". natives.json {ns:{hexhash:{name}}}. from_full(YscFull). name_at/index_of_name/index_of_hash.
  yscfull.py erweitert: native_raw (count*u64 @natives_offset). tests/test_natives.py: capture.OLD 6/6 ground-truth EXAKT.
KRITISCHE ERKENNTNIS: scrpatches.json customfuncs-bytes sind fuer ALTE version (cffba34)! native-indizes stimmen mit
  capture.OLD (665=IS_MODEL_VALID,14=GET_MODEL_DIMENSIONS,308=GET_HUD_COLOUR,1098=IS_ENTITY_AN_OBJECT,180=DOES_ENTITY_EXIST,
  70=GET_ENTITY_MODEL). in NEW verschoben: 665->476,14->172,308->296,1098->1143 (180,70 stabil). = der blind-spot LIVE.
AUTO-REPARATUR NATIVES BEWIESEN: to_ysa(oldblob, resolver=OLD) -> assemble_text(natives=NEW.index_of_name()) ->
  repaired bytes mit NEUEN indizes. semantic round-trip TRUE (repaired unter NEW-tabelle == old unter OLD-tabelle).
  689 bytes capture: 4 natives korrekt aktualisiert. DAS IST DIE CUSTOMFUNC-REPARATUR (native-teil).
NOCH OFFEN fuer VOLLE reparatur: (b) CALL->R*-func-adressen (5D xx xx xx) shiften auch -> brauchen func-fingerprint-
  matching old->new (native-hashes+strings die die func aufruft). (c) interne cross-func-CALLs (label im blob). (d) globals+
  embedded strides = offsets.ini-werte (schon migriert!). status: 14 tests gruen. NEXT: M4 .ysa-quellen generieren,
  funcsig matching fuer CALLs, M5 health-check payload-validierung (report reports/), repair_customfuncs.py -> reports/.
NOTE: python-engine macht C#-decompiler-build (M6) UNNOETIG fuer unseren zweck (disasm+natives+funcs alles in python).

## OVERNIGHT KOMPLETT (2026-07-14): scrasm gebaut, getestet, customfuncs-auto-reparatur VOLLSTAENDIG
ALLE ZIELE M1-M5 ERREICHT (M6 dotnet unnoetig). 19 tests gruen (python3 -m unittest discover -s scrasm/tests).
scrasm/ module: opcodes, model, disasm, yscfull, functions, natives, funcsig, asm, importer, repair (+tests + customfuncs/src + README).
FUNKTIONS-MATCHING funcsig.py 3-tier: strict(fp: params,returns,native-hashes,globals,consts) -> loose(params,returns,
  native-hashes) -> positional(reihenfolge-erhaltend zwischen ankern, guard: instr-laengen-aehnlichkeit). build_address_map
  ->(amap,tier). positional-matches verifiziert opcode-jaccard ~1.0.
STRIDE-MINING repair.mine_struct_strides(code): (global,ioffset)->dominante ARRAY_U16-stride aus R*s EIGENEM code
  (tausende zugriffe). SELF-CONTAINED, kein offsets.ini noetig. capture: (0x480000,3605) old=26949 new=26968 BESTAETIGT.
REPAIR repair.py: in-place operand-rewrite (blob-layout versions-stabil, alle volatilen operanden fix-breit):
  NATIVE old-idx->hash->new-idx; interne CALL: new_base+(t-old_base); externe CALL: addr_map(fingerprint);
  ARRAY_U16 stride: (global,ioffset)-kontext -> new stride. base = find_anchor(2D 04 3A 00 00 38 03) unique je version.
  ScriptContext.build(old_full,new_full) cached alles. repair_scrpatches.py -> reports/scrpatches.repaired.json + .txt.
ERGEBNIS ALLE 3 payloads (lts/capture/race): 3/3 OK, 0 review. natives updated, 6 interne calls relokiert,
  4 externe calls resolved (strict/loose/positional), 10 strides 26949->26968. FINALE VALIDIERUNG: repaired payload
  referenziert nur GUELTIGE ziele in new (natives_invalid=0, external landet auf ENTER-grenzen=0, internal_bad=0) = VALID.
GENERIERTE QUELLE scrasm/customfuncs/src/{lts,capture,race}.ysa (native-namen, @fn-labels, kommentare, round-trip-verifiziert).
  gen: python3 gen_customfuncs_src.py. reparatur: python3 repair_scrpatches.py.
OFFEN/NEXT: (1) offsets.ini old->new stride-map optional integrieren (aktuell self-mined, funktioniert). (2) die 9 broken
  BASIS-patterns (stage2, separate von customfuncs) mit demselben disasm/funcsig-tooling neu ableiten. (3) user-review der
  .ysa quellen + evtl rollen-namen statt fn0..fn6. (4) scrasm als eigenes repo abtrennen (self-contained, README da).
LEITPLANKEN eingehalten: KEIN commit, data/scrpatches.json + offsets.ini unberuehrt (output nur reports/), disasm/reports gitignored.

## 1.73-UPDATE-TEST + VERSIONIERUNG (2026-07-14, mit user)
5 signierte commits gemacht (offsets/scrpatches/scrasm). DANN echter 1.73-test.
calamity-inc repo GTA-V-Decompiled-Scripts: NUR branch "senpai" (kein master!). 1.73-3889.0 = commit 593b204.
  cffba34 = 1.71-3586 (NICHT 1.70!). repo hat decompiled_scripts/ (.c) + scripts/<n>_ysc/ (.ysc.full).
VERSIONIERUNG gebaut (user-idee, klasse): scripts/<build>/*.c + scrpatches/disasm/<build>/*.ysc.full.
  versions.py (root): resolve/list_versions/previous/sort_key. fetch_update.sh <build> [ref=senpai] laedt beides.
  run_pipeline.py/check_patches.py/repair_scrpatches.py: --old/--new <build> (+ alte --*-dir/--*-ref als fallback).
  --old default = previous build. gen_customfuncs_src.py + tests auf versionierte pfade umgestellt. .gitignore: scripts/.
KRITISCHER BEFUND (empirisch): offsets.ini ist 1.71-STAND, nicht 1.72!
  --old 1.72-3788 -> 90 migriert/790 review (MUELL). --old 1.71-3586 -> 852/34 (SAUBER).
  => --old auto-default (previous=1.72) ist HIER falsch weil offsets.ini eine version hinterherhinkt. explizit --old 1.71-3586.
1.73 HEALTH-CHECK: 11 broken (cam fix x4, precise templates x5, show stunt prop x2), 32 OK.
1.73 CUSTOMFUNCS-REPAIR: 3/3 OK. ABER: KNOWN GAP bestaetigt - IOFFSET 3605->3838 in 1.73 (creator-global feld verschoben),
  stride-mining kontext-keyed auf (global,ioffset) findet 3605 nicht mehr -> embedded offsets NICHT auto-migriert (false OK).
  = offsets.ini<->customfuncs verbindung, next milestone: offset-map in repair einhaengen.
IMPORTER-BUG gefixt (stress-test fand ihn): to_ysa emittiert jetzt END-LABEL fuer jump-to-exit (target==len(code));
  cross-func-sprung -> ValueError, sampled-test skippt die. 146 funcs 0 skipped. ALLE tests gruen (19).
DOKU (README.md) auf versionsmodell: TL;DR-karte, Step1=fetch_update.sh (option A) / OpenIV (B), Step2 --new + baseline-caveat,
  Step3 --new (cache-gotcha weg), Step4 deploy (2 ziele + merge). master->senpai gefixt. USER WILL DOKU NOCH BEWERTEN.
OFFEN: (1) embedded-offset-migration in repair (3605->3838 via offset-map). (2) 11 base-patterns auto-repair (stage2).
  (3) deploy-merge-tool. (4) baseline-auto-detect statt --old-default=previous. (5) noch NICHT committet (versionierung).
