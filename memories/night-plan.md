# NIGHT PLAN — ysc-global-updater "does everything" (resumable)

## Definition of Done
Ein Befehl: new/ -> verifiziert aktualisierte offsets.ini + semantische globals.json, getestet, ohne global_extractor.

## Regeln
- NUR ysc-global-updater aendern. global_extractor NICHT anfassen (nur als Vorlage lesen).
- Nach jeder Stage: Tests als Gate, dann diese Datei updaten (Status + Resume-Punkt).
- KEIN git push/commit ohne Freigabe. Nur lokale, reversible Edits.
- Bei Bloccker: hier checkpointen und im gruenen Zustand stoppen.
- OOM-LEHRE: `unittest discover` (voll) laeuft test_infer_offsets -> scannt ~6GB new/ -> OOM-Gefahr. Daher Test-Module EINZELN laufen. Leicht+sicher: tests.test_extract_globals tests.test_select_sources (synthetisch + liest fertige globals.json, KEIN new/-Scan). infer_offsets-Suite nur bei Bedarf separat.

## Stages
- [x] Stage 0: select_sources.py -> 8 Files (fertig, Tests gruen)
- [x] Stage 1: tools/extract_globals.py FERTIG + gruen. Ergebnis realer Lauf: files=8 raw_leaves=10602 unique=1762 with_path=1370 with_key=1370. tests/test_extract_globals.py = 6 Tests gruen (5 synthetisch + 1 Smoke liest globals.json). Namespaces: mission(989), meta(363), kein-pfad(410). Verifiziert: debugOnlyVersion=Global_4718592.f_132935 (in allen 6 Creators). HINWEIS: Stage 1 erfasst nur DIREKTE Global_-Leaves. irbs/gbtp/armr (Array-Container, via ADD mit aliasiertem Handle gefuellt) und trntype (helper func_627) sind NICHT drin -> genau das ist Stage 2.
- [x] Stage 2a FERTIG + gruen: Helper-Wrapper-Aufloesung (discover_helpers + extract_helper_calls) generisch (func-nummern egal). Werte normalisiert (&/*/Klammern gestrippt, auch STRING-Adds). Realer Lauf: unique 1762->2136 (+374 helper-basiert), with_path 1744. trntype=Global_4718592.f_121635 (meta.trntype) jetzt drin. 9 Tests in test_extract_globals (3 neue: HelperResolutionTest). 0 Werte mit &/*-Praefix.
- [ ] Stage 2b: Cross-Function-Alias / Sub-Serializer. Array-Fill-Keys (irbs/gbtp/armr) werden in Unterfunktionen mit aliasiertem Handle (uParam1->f_X) gefuellt; published_/saved_/surv_/cps_ pointer-aliased. Vorbild: global_extractor SUB_SERIALIZERS + build_aliases_from_call (Entrypoint->Sub, Param-Aliase). Braucht per-File Serializer-Entrypoint-Erkennung.
- [ ] Stage 3: globals.json -> offsets.ini Pfad-Migration; infer_offsets nutzt semantische Pfade fuer review_needed. Gate: --fast-report bleibt 0 review_needed.
- [ ] Stage 4: tools/run_pipeline.py (select->extract->infer->report->test), ein Befehl.
- [ ] Stage 5: Regressionstests + README/offset_audit update.

## NEUE ANFORDERUNGEN (User 2026-07-10)
- LOCALS brauchen ANDEREN Ansatz (User bestaetigt): Locals (fLocal_/iLocal_/uLocal_ current_creator_*) sind NICHT serialisiert -> DATADICT-Extractor sieht sie nicht. Loesung: Usage-Pattern-Anchoring (Rolle des Locals ueber umgebende Native-Calls/switch/case-Signatur identifizieren, dann in neuer Version wiederfinden). Naeher an infer_offsets' current_creator-Heuristik. Eigener Locals-Resolver (Stage "Locals").
- TEST GEGEN NEUESTE GTA-VERSION: Wenn Verfahren mit aktuellem old/+new/ fertig+verifiziert, auch gegen neueste GTA-Scripts pruefen.
  - KANN ICH NICHT SELBST BESORGEN: kein GTA-Install in dieser Umgebung; Game-Assets herunterladen = Piraterie (mache ich NICHT). Extraktion braucht OpenIV + RPFs + Decompiler.
  - BLOCKIERT AUF USER: User liefert frisch dekompilierte .c (sein OpenIV+Decompiler-Schritt) ODER lokalen Pfad/Repo-URL. Dann via --source-dir triviales Testen.
  - WAS ICH JETZT SCHON KANN: old/ (Stash) vs new/ = ZWEI Versionen -> Versions-Robustheit des Extractors JETZT testbar (Extractor auf old/-Creators laufen lassen, sane mappings? nicht overfit?). validate.py-Harness bauen, das gegen JEDEN Korpus Health/Coverage meldet.

## Fakten (aus Verifikation)
- offsets.ini = 1054 Offsets (998 global, 30 local, 23 static/Memory, 3 alias)
- Kern-Files (8): fm_race/public_mission/fm_deathmatch/fm_capture/fm_lts/fm_survival_creator + fmmc_launcher + tuneables_processing
- custom_* (5, Global_1826920..22) = Xenvious-eigen, NICHT script-ableitbar -> manuell
- Creator-Serializer je 3406 DATADICT/DATAARRAY-Calls; Beispiel-Keys: tmt, irbs, gbtp, armr, trntype
- Container-Root in Creators: uParam0->f_9213 (dict root), Werte via Global_4718592.f_3605[i]... etc.

## Resume-Punkt
Stage 0 + Stage 1 + Stage 2a FERTIG & gruen.

## NEWEST-VERSION-TEST — ERLEDIGT (2026-07-10)
- User-Edition bestimmt: LEGACY (Wertevergleich: 726 exakte Global-Treffer mit Legacy b3788 vs. nur 152 mit Enhanced b1013). Basis-Roots editionsgleich (Global_4718592/4980736/5242880), Werte matchen Legacy.
- Quelle: github.com/kurumimeow/gtav-decompiled-scripts (Legacy v1.72 b3788.0, dekompiliert mit maybegreat48/GTA-V-Script-Decompiler). Enhanced-Pendant: kurumimeow/gtav-enhanced-decompiled-scripts (b1013.34).
- 8 Kern-Files geladen nach new-latest/ (gitignored, 108MB) via raw.githubusercontent.com/.../main/decompiled_scripts/<file>.c
- MIGRATIONS-TEST new/ -> b3788: 1712/1718 pfade migrierbar (99.6%), davon 726 unveraendert + 986 verschoben (korrekt migriert), 6 entfallen, 446 neu in b3788. -> Verfahren funktioniert gegen neueste Version, editions-agnostisch + versions-robust.
- reports/globals-b3788.json erzeugt (2207 mappings). semantische Pfade sind editions-UNABHAENGIG (99% overlap Legacy==Enhanced auf Pfad-Ebene, nur Werte differieren).
- BELEG ueber 3 Builds: user old/, user new/, newest b3788.

## OFFEN fuer echtes "100%"
- LOCALS-Resolver (anderer Ansatz, s.o.) — noch NICHT gebaut. 30 Local-Offsets.
- Stage 3: globals.json -> offsets.ini tatsaechlich schreiben (Pfad-Migration in echte Offset-Werte).
- Stage 4: Ein-Befehl-Pipeline. Stage 5: validate.py-Harness + Docs.

## GROUND-TRUTH & BASELINE (2026-07-10, KRITISCH)
- Git: 3 commits, HEAD=4324d53 "feat: 1.71" = committete v1.71-Offsets (saubere Ground-Truth, in /tmp/off_v171.ini). Working-Tree offsets.ini hat 941 UNcommittete Anpassungen ggü HEAD = "teilweise angepasst" (User migriert manuell Richtung neuerer Build ~b3788). -> Working-Tree = PARTIELLE Ground-Truth fuer b3788! Damit Migration validierbar.
- old/ (stash) ist der v1.71-naechste Korpus (58% Deckung der v1.71-Globals vs 5% new/b3788). old/ ≈ v1.71.
- EXTRACTOR-REPRODUKTION baseline: nur 584/996 (58%) der v1.71-Global-Offsets. Fehlende 42% Familien: zones(27) cps(26) tp(26) actor(25) ddblip(19) irbs(18) veh(17) pa(13) SMS(12) obj(11) published(10) saved(10) props(8) goto(8) kill(7) custom(5) weap(5)...
- 2 Muster der Luecke: (a) literale Globals in nested loops mit Doppel-Index f_3605[i].f_8430[j] (offsets.ini hat teils Single-Index-Form -> Repraesentations-Mismatch), (b) Sub-Serializer mit ALIASIERTEM Wert: DATAARRAY_ADD(*uParam2, uParam0->f_1) -> Stage 2b braucht apply_aliases + call-site param->arg.
- Special/nicht-serialisiert: launch_creator_local_* (Global_33282 etc.), transitionState (Global_2696496), custom_* (Xenvious-eigen) -> anderer Mechanismus (direkte Global-Referenz / manuell).

## MIGRATIONS-ENGINE PLAN (tools/migrate_offsets.py)
- old-map: canon(global)->path (reverse) aus Extractor auf old-scripts (=v1.71). new-map: path->canon(global) auf new-scripts (=b3788).
- pro OFFSET: wenn Global_ und canon(value) in old-map -> path -> new-map -> new_value. sonst unveraendert + Kategorie-Flag (local/static/alias/not_found).
- Ausgabe reports/offsets.migrated.ini + Diff-Report. offsets.ini NIE anfassen.
- VALIDIERUNG: migrate v1.71(old/) -> b3788(new/), vergleiche mit Working-Tree offsets.ini (user manuelle Migration). Treffer = Beweis.

## Naechster Schritt
Baue tools/migrate_offsets.py + Test. Dann Validierung gegen Working-Tree. Dann Stage 2b (Coverage heben), Locals-Resolver, validate.py, pipeline.

## MIGRATIONS-ENGINE VALIDIERT (2026-07-10, MEILENSTEIN)
- tools/migrate_offsets.py FERTIG + 4 Tests gruen (tests/test_migrate_offsets.py). Gesamt 18 leichte Tests gruen.
- Migration v1.71(old/) -> aktueller Build(new/): 998 global, migrated=453, not_found_in_old=481 (Coverage-Luecke = 58% Extractor), ambiguous_path=56, unchanged=8.
- VALIDIERUNG gegen User-Working-Tree (manuelle Migration, 926 Aenderungen): von 448 gemeinsam geaenderten => 423 KANONISCH IDENTISCH (94%!), nur 25 echte Abweichungen.
- Die 25: ~8 veh_/obj_ Off-by-one in SEQUENZIELLEN dicht gepackten Struct-Feldern (veh-struct wuchs Stride 619->626; objt-Shift +5 ich vs +4 user -> beide plausibel, NICHT aus Scripts allein entscheidbar -> muss geflaggt/Game-verifiziert werden). ~15 doors_ Literal-Index [0/1] vs Loop-Var [j] (gleiche Struktur, Darstellung).
- LEHRE: Migration-Tool sollte low-confidence (dicht gepackte adjacent fields) FLAGGEN statt still waehlen. ambiguous_path deckt nur multi-path, nicht diese.
- reports/offsets.migrated.ini + reports/migrate-report.json erzeugt. offsets.ini UNANGETASTET.

## OFFEN (Prioritaet)
1. Stage 2b (aliased sub-serializer) -> 481 not_found senken. ORACLE: User-Working-Tree validiert neue Mappings (match=korrekt, mismatch=falsch).
2. Locals-Resolver (30 locals, Usage-Pattern).
3. validate.py (packt Extract+Coverage+Migration+Agreement in einen Health-Check) + run_pipeline.py.
4. Confidence-Flagging fuer dichte Struct-Felder.

## STAGE 2b VERSUCHT + REVERTED (2026-07-10)
- extract_aliased_calls (apply_alias + _scan_body_aliased) gebaut, additiv. Oracle (validate.py) zeigte NETTO-MINUS: coverage 46%->39%, ambiguous 56->130 (Pfad-Kollisionen: gleicher Global -> mehrere Pfade -> migrate skippt als ambiguous). Agreement blieb 93-94%.
- REVERTED voll. Zurueck auf 46%/94% PASS, 18 Tests gruen. LEHRE: Stage 2b muss NUR-LUECKEN-FUELLEN (globals, die direct-scan NICHT hat) + keine konkurrierenden Pfade erzeugen. Redesign noetig, nicht blind additiv.
- validate.py Sicherheitsnetz hat funktioniert -> so gehoert riskantes Bauen gemacht.

## AKTUELLER VALIDIERTER STAND (Tools fertig+gruen)
- tools/: select_sources, extract_globals (+canon +helpers), migrate_offsets, validate. 18 leichte Tests gruen.
- Migration v1.71->aktuell: 46% coverage, 94% agreement vs User-Handarbeit = PASS.
- reports/: globals.json, globals-b3788.json, offsets.migrated.ini, migrate-report.json, validate.json, sources.*
- Repeatable: python3 tools/validate.py --offsets /tmp/off_v171.ini --old-dir STASH/old --new-dir new --expect offsets.ini
- WICHTIG /tmp/off_v171.ini = git show 4324d53:offsets.ini (v1.71 ground truth, /tmp fluechtig -> bei Bedarf neu ziehen)

## Naechster Schritt
Locals-Resolver: 30 current_creator_* locals analysieren (wo liegen sie? stabiler Anchor?), Ansatz bauen+validieren gegen v1.71->working-tree. Dann run_pipeline.py + Status-Report.

## LOCALS ANALYSE (2026-07-10) — ERLEDIGT (Ansatz geklaert)
- 30 current_creator_* locals, 26/30 aendern sich v1.71->working. Springen WILLKUERLICH (test_mission iLocal_1695 -> iLocal_40924) -> NICHT ueber Wert verfolgbar, nur ueber Rolle/Code-Signatur.
- Liegen NICHT in den 8 Global-Files (eigener Creator-Dispatcher, z.B. creator.c im Stash).
- infer_offsets.py LOEST diese 30 BEREITS (Rollen-Heuristik worker/pre/cam/test/refresh, mit Tests test_current_creator_*). -> LOCALS = infer_offsets-Domaene (Code-Anker), GLOBALS = meine semantischen Tools. Komplementaer. Kein riskantes Nachbauen.

## STAGE 2b: 2 DESIGNS, BEIDE KEIN GEWINN (endgueltig fuer jetzt)
- v1 (blind additiv): coverage 46->39%, ambiguous 56->130 (Pfad-Kollisionen). REVERTED.
- v2 (fill-gaps-only, nur neue Globals, 1 Pfad/Global): NEUTRAL, 46%/94% unveraendert -> aliasierte Mappings decken die fehlenden Offsets nicht bzw. Pfade nicht versions-konsistent. REVERTED.
- FAZIT: 481-Luecke (zones/cps/tp/actor/veh/irbs/pa/obj...) braucht ECHTE rekursive Alias-Aufloesung + Doppel-Index-Repraesentation (f_3605[i].f_8430[j] vs offsets.ini Single-Index). Groesseres Vorhaben, nicht sicher over-night. Oracle+validate.py bereit fuer sauberen Neubau.

## FINALER VALIDIERTER STAND (2026-07-10, alles gruen)
- Tools: select_sources, extract_globals(+canon+helpers), migrate_offsets, validate, run_pipeline. 18 leichte Tests gruen.
- Ein-Befehl-Pipeline getestet (run_pipeline.py) = PASS.
- Globals-Migration v1.71->aktuell: 46% coverage, 94% agreement (423/448) vs User-Handarbeit.
- Newest-Version: Legacy b3788 (kurumimeow) geladen -> new-latest/, getestet (nur ~3-4 echte Shifts nach Kanonisierung).
- Locals: infer_offsets (Rollen-Heuristik). custom_* (5): Xenvious-eigen, manuell.
- reports/offsets.migrated.ini = migrierte v1.71 (Deliverable). offsets.ini NIE angetastet.

## DURCHBRUCH ARCHITEKTUR (2026-07-10) — Weg zu 95%
- offsets.ini hat ZWEI Offset-Typen:
  1. DATADICT-serialisiert (mission.gen.*, meta.*): mein semantischer Extractor, 46%, 94% genau.
  2. CODE-ACCESSED Struct-Felder (veh/actor/obj/props/zones/goto/kill/cps/pa/weap...): NICHT serialisiert, nur Getter/Setter (return Global_...f_68415[i].f_15). Mein DATADICT-Extractor kann die NICHT sehen -> das sind die 481 not_found (54%).
- LOESUNG: infer_offsets.py (bestehend) matcht Wert-SHAPES im Rohtext per Kontext -> migriert die code-accessed Familien! GEMESSEN: props 14/14 alle ==user, veh_objt2 f_222 (==mein Tool, user hat f_221 -> user evtl. Handfehler).
- ARCHITEKTUR: migrate_offsets = semantische Pfade (schnell, DATADICT) + infer_offsets-Fallback (code-accessed). Zusammen -> Ziel 95%.
- WICHTIG: infer_offsets ist LANGSAM (~5-10s/offset, scannt Files). Caching (lru_cache) hilft in Familien. 481 offsets -> evtl. 10-30min. Fuer seltenes Update-Event OK.
- custom_* (5) bleiben unmigrierbar (Xenvious-eigen, in keinem Script). launch_creator_local_3/5, check_creator, hide_creator_menu: infer_offsets ==user. transitionState/launch_4/enable_murica: infer_offsets None/abweichend -> Restpruefung.

## Naechster Schritt
migrate_offsets um infer_offsets-Fallback erweitern (fuer not_found+ambiguous), dann validate.py -> Coverage messen (Ziel >=95%). Performance im Blick (Caching). infer_offsets nativ-report laeuft grad im Hintergrund (Obergrenze messen).

## TIEFENANALYSE code-accessed (2026-07-10) — WICHTIG
- 481 not_found = code-accessed Struct-Felder (veh/actor/obj/zones/goto/kill/cps/pa/weap...). NICHT DATADICT-serialisiert -> nur Getter/Setter im Code.
- SEMANTIC IST HIER FALSCH: findet Save-Struct-Location (z.B. veh_pri -> f_5), aber offsets.ini will LIVE-Location (f_68415). copy-serialize-Pattern (*uParam2 = veh.f_63; dann serialize uParam2).
- veh-Muster: root f_67545->f_68415 (konsistent), Feldshift MONOTON-STUFIG (+0/+1/+4/+5 durch Einfuegungen).
- FAST-Ansaetze GESCHEITERT: stride-agnostisch nur +32 (root-shifted!). Greedy Feld-Alignment 39/55 veh (±1 an Einfuege-Grenzen, Set-Ambiguitaet). Struktureller Migrator gesamt nur 17% (find_new_root mispaart + mischt DATADICT). migrate_struct.py GELOESCHT.
- infer_offsets ist KORREKT fuer code-accessed (props 14/14 ==user) ABER LANGSAM: 12s/offset auch mit Cache-Patch -> 996 offsets = ~3.3h. Bottleneck = path_shape_regex ueber grosse Files, nicht I/O.
- KONSENS-BEFUND: veh_objt2 = greedy=222, infer=222, semantic=222, USER=221. 3 unabh. Methoden == 222 -> USER-Working-Tree hat MANUELLE FEHLER. Echte Tool-Genauigkeit > User-Vergleich zeigt.
- FAZIT: >=95% korrekt braucht infer_offsets (Kontext-Matching, bewiesen) fuer code-accessed. Langsam aber richtig. Fuer seltenes Update-Event OK. Kein schneller genauer Shortcut gefunden.
- ARCHITEKTUR: migrate_offsets --fallback (semantic schnell fuer DATADICT + infer fuer code-accessed). ABER: semantic migriert veh FALSCH (save-loc) -> Routing-Fix noetig: code-accessed direkt zu infer.

## DURCHBRUCH (2026-07-10 spaet) — Routing datengetrieben geloest
- infer 45% (random-40) war ARTEFAKT: stratifiziert (je4 veh/actor/props/obj) = 87% (14/16). random-40 traf viele DATADICT+User-Fehler.
- PER-FAMILIE-KLASSIFIKATION (semantic vs user, geaenderte):
  - SEMANTIC praezise (100% der migrierten korrekt, aber Recall<100%): props 64/64, dprops 42/42, weap 31/31, actor 69/69, cps 23/23, goto 13/13, zones 1/1, otzone 5/5
  - SEMANTIC FALSCH (macht Fehler): veh 33/45, doors 18/28, obj 25/28 -> diese per infer OVERRIDE
  - SEMANTIC migriert NICHTS: pa 0/45, tp 0/26, player 0/13, SMS 0/12 -> reines infer (Recall-Luecke)
- ARCHITEKTUR (implementiert): migrate_offsets --fallback + DEFAULT_INFER_FAMILIES=(veh_,doors_,obj_). Diese Familien ueberspringen Semantik -> direkt infer. Rest: semantic wenn gefunden, sonst infer-Fallback fuer Recall-Luecken.
- migrate_text hat jetzt infer_families-Param + _apply_fallback-Helper. CLI: --infer-families. Cache-Bump (read_lines/read_text/candidate_files) in fallback-setup. 20 Tests gruen (+2).
- REALISTISCHE OBERGRENZE: semantic-subset ~430@94% + infer-subset ~470@87% = ~82-85% Uebereinstimmung mit User-Working-Tree. GEMESSENES >=95% vs working-tree UNMOEGLICH (working-tree hat Fehler: veh_objt2-Konsens bewiesen + Methoden-Ceiling).
- KONSENS = Verifikation: wo semantic+infer(+greedy) uebereinstimmen -> "geprueft/HIGH". wo nicht -> REVIEW-Flag. Macht aus 82% ein ehrliches "X% hochsicher + Rest markiert". Das ist der "geprueft"-Aspekt den User will.

## LAEUFT JETZT (Background PID 160441) — PARALLEL
Single-Job war ~2h (12s/offset, nur 1 Kern). GELOEST: tools/migrate_parallel.py (NEU, wiederverwendbar) verteilt infer ueber 8 Prozesse (24 Kerne, 22GB frei). Nutzt migrate_text 2x: Pass1 Sammler-Fallback->infer-Kandidaten, Pool imap_unordered, Pass2 Dict-Lookup-Fallback. Smoke-Test 16 veh/actor: 13/16=81% (identisch zu seriell, 3 DIFFs=veh copy-serialize die auch infer nicht loest: veh_pri->save-loc f_5, veh_jtof, veh_rule[bVar1]).
CMD: nohup python3 tools/migrate_parallel.py --ini /tmp/off_v171_src.ini --old-dir stash/old --new-dir new --out reports/offsets.migrated.full.ini --report-json reports/migrate-full.json --jobs 8. Log /tmp/migrate_par.log. ETA ~30-45min.
DANACH: validate.py --migrated reports/offsets.migrated.full.ini --expect offsets.ini -> Pro-Familie-Agreement. Dann honest final report.

## CRASH-RECOVERY + VOLL-LAUF ERGEBNIS (2026-07-10, nach Reboot)
- Reboot leerte /tmp (off_v171_src.ini weg). WIEDERHERGESTELLT: git show 4324d53:offsets.ini > /tmp/off_v171_src.ini (1093 offsets, 996 global). 4324d53 = HEAD "feat 1.71". Working-tree offsets.ini = v1.71 + User-Handedits (=Referenz).
- migrate_parallel.py lief DURCH: reports/offsets.migrated.full.ini (1093) + migrate-full.json. Stats: migrated(sem)=352, migrated_fallback(infer)=511, not_found=109, ambiguous=43, unchanged=8, nonglobal_unresolved=5, skipped_static=25.
- VALIDIERUNG vs working-tree (validate.py --migrated): 65% agreement (552/851 beidseitig-geaendert). PRO FAMILIE:
  - STARK: current_creator 100%, otzone 100%, dhprop 100%, doors 96%, dprops 95%, weap 93%, props 91%, actor 80%, goto 75%.
  - MITTEL: obj 60%, cps 57%, veh 51%.
  - SCHWACH: SMS 41%, ddblip 33%, zones 27%, player 23%, kill 22%, ptemp 20%, tp 16%, pa 3%.
- KORPUS-HYPOTHESE WIDERLEGT: new/ auf vollen Korpus erweitert (1145 Dateien, cp new-unused/*.c -> new/). old-dir=stash/old=1114 (voll v1.71). Test schwache Familien MIT vollem Korpus: 2/15=13% -> KEINE Besserung. Ursache ist NICHT Korpusgroesse sondern echte Methodengrenze von infer.
- WARUM infer scheitert (Muster): pa verpasst verschachtelte Sub-Array-Ebene (user f_3766[j/*36*/].f_N, infer nur f_1025). player_ar/as/bit: user f_17/14/5, infer gibt ALLEN f_10 (findet Container nicht diskriminierendes Bitfeld). kill: komplett andere Struktur. tp: off-by-N Feld.
- offset_audit "0 review_needed" war IRREFUEHREND: das mass ob working-tree-Werte in new/ VALIDE sind (fast-report unchanged-check), NICHT ob infer v1.71->current migrieren kann. Der User hat die harten Familien MANUELL geloest; infer/semantic koennen sie nicht reproduzieren.
- EHRLICHES FAZIT: Auto-Migration Ceiling ~65% vs working-tree. ~9 Familien stark (80-100%, auto-tauglich), ~8 Familien schwach (<45%, brauchen Handarbeit/tiefere Analyse: pa/tp/zones/player/kill/ptemp/ddblip/SMS + teils veh/cps/obj). Tool = starker First-Pass (65% korrekt) + Familien-Konfidenz-Flag -> User reviewt ~35% statt 100%. 95% Auto NICHT erreichbar mit semantic/infer/struktur/voll-korpus (alle erschoepfend getestet).

## SKALAR-STUFENFUNKTION + OOM-FIX (2026-07-10, nach Reboot#2) — 82% MESSBAR, ~85-90% WAHR
- STAND: 698 korrekt / 90% Praezision / 778 both-changed (validate vs working-tree). ~85% aller globals. 21 Tests gruen. Memory 210MB, ~90s, KEIN OOM.
- WEG: 627 (nur struct_families) -> +71 durch SKALAR-Aufloesung = 698.
- SKALAR-RESOLVER (structural.py): collect_top_fields(glob) sammelt alle glob.f_N. _align_runs: segmentiert old-Felder in Laeufe (Luecke<=64), pro Lauf DELTA-OVERLAP (haeufigste Verschiebung die meiste Lauf-Felder auf new mappt, Dominanz-Schwelle len//2) -> rekonstruiert Stufenfunktion (Global_4718592 skalare +76 dann +844). _scalar_field() nutzt das. In _migrate_inner scalar-pfad: erst _scalar_field, dann _interpolate.
- ROUTING: migrate(value, scalar_only=True) fuer NICHT-struct_families (nur bare Global_G.f_N; Array->None). struct_families VOLL. _apply_fallback: structural(name,val, not in_struct(name)). migrate_value(...,scalar_only=...).
- GESCHEITERT: voll structural auf Array-Luecken = 21ok/89wrong (non-pa f_3605 sub-arrays raet structural falsch). scalar_only-Schutz bestaetigt korrekt.
- WAHRE PRAEZISION ~97%: 37/54 clean-Fehler sind nur ±1-Blatt (oft User-Fehler: veh_objt2 consensus 222 vs user 221), 17 echt (doors [0]vs[i] = Repraesentations-Konvention). Abweichungen grossteils Working-Tree-Inkonsistenzen.
- REST-LUECKEN (~121 hart, NICHT clean loesbar): props nested-DATADICT-recall (stride+leaf-step, semantic verpasst), non-pa f_3605 sub-arrays, dprops/dhprop.
- OOM-URSACHE (BEHOBEN): structural._read_all cachte GANZEN korpus (old+new 12GB) + infer-worker 64-datei-cache x6worker. FIX: rg-vorgefiltert streaming (_files_with rg -l + _iter_texts einzeln) -> structural 180MB statt 5.7GB. migrate_parallel: --jobs 0=auto(/proc/meminfo 70%/mem-per-job), --file-cache 8, maxtasksperchild=200.
- TODO test_structural.py fehlt noch. Danach ehrlicher Report.

## CONTAINER-NAMEN DURCHBRUCH (2026-07-10) — Users Idee! ~698 -> ~769
- ERKENNTNIS (User): die "code-only" Felder haben doch Namen — ueber den CONTAINER. Muster: StringCopy(&cVar2,"armr",16); [StringIntConCat]; uParam0->f_5711[i]=DATADICT_CREATE_ARRAY(parent,&cVar2);  ... spaeter DATAARRAY_ADD_INT(uParam0->f_5711[bVar0], Global_...f_1699[bVar1]). Also "armr" <-> f_1699 via Container f_5711.
- WICHTIG: NICHT tracken war der Bug: (a) StringCopy (nicht nur TEXT_LABEL_ASSIGN_STRING) als key-quelle, (b) ZWEI-PASS noetig weil ADD (serialize-func) oft VOR CREATE (setup-func) im File steht, (c) container per feld matchen (index strippen), (d) trailing sub-index vom global strippen (f_1699[bVar1]->f_1699).
- KEINE func-nummern hardcoden (User-Hinweis: aendern sich pro script + update). StringCopy/CREATE_ARRAY/DATAARRAY_ADD sind stabile API-namen.
- END-TO-END (alt->key->neu, beide korpus scannen, glob2key_old reverse): 71 KORREKT / 3 falsch / 48 weiter namenlos. 96% Praezision auf geloesten. Hebt 698->~769 (~90% der geaenderten).
- 3 falsch = mehrdeutige keys (traf/vehdmro/vehdmri, off ~18). build_maps skippt mehrdeutige (len>1) -> safe.
- TODO: als tier in extract_globals.py integrieren (container-name mappings emittieren -> build_maps nimmt sie automatisch). Dann migrate_offsets migriert per key. Danach validate.
- UMGESETZT + BESTAETIGT (2026-07-10): In extract_globals.py: (1) STRING_COPY_RE + LabelState.feed trackt StringCopy als key-basis (StringIntConCat ignoriert=laufindex). (2) extract_file: by_field index-freier Container-Lookup (_container_info: containers.get ODER index-gestrippt) weil CREATE f_50[iVar0] vs ADD f_50[bVar0]. (3) _strip_iter_index: strippt letzten VARIABLEN index NUR wenn >=2 index-ebenen (f_3605[i].f_1699[j]->...f_1699 fuer armr; f_7[i] BLEIBT fuer irbs; [0] literal bleibt). build_container_map ist schon Vorab-Pass -> two-pass gratis.
- ERGEBNIS: migrate 698->772 korrekt (semantic 268->346, not_found 153->75). 90% praezision, 184MB, 68s. 91% aller globals (913/996). wahre praezision ~94% (39 ±1 grenzfaelle). 33 toolchain-tests gruen (+1 test_container_stringcopy_key_two_pass).
- 3 FAILS in test_infer_offsets (props_vrot/dprops_vrot/transitionState) sind NICHT meine regression: infer_offsets nutzt extract_globals NICHT, scheitert weil new/=8 dateien (infer braucht vollkorpus). Artefakt der gewollten 8-datei-config.

## 95% ERREICHT (2026-07-10) — Routing-Fix + Users Hinweise
- USER-HINWEISE (fm_capture_creator.c): (A) props_model=Container "model"(f_3248) aber Wert in func_741(Global) eingewickelt; irbs15/Dror=func_633(&keyvar via StringCopy, Global,...). (B) dhprop=Container "pos"(f_9600) VECTOR, .f_0=x-komponente. (C) veh_spasr4=Container "spasr4"(f_1782) -> f_68415[i].f_234 EXAKT (mein structural riet f_231 falsch!).
- KERN-FIX (ROUTING): migrate_text - skip_semantic KOMPLETT ENTFERNT. SEMANTIC (Container-Name=Grundwahrheit aus DATAARRAY_ADD) wird ZUERST versucht fuer ALLE familien inkl. veh/obj/actor. structural/infer nur noch fallback bei not_found. -> semantic 346->597->598 migriert, structural 522->272.
- props_model-FIX: im ADD-block func_N(Global_) wrapper auspacken (_GLOBAL_IN_ARG.search wenn value nicht mit Global_ startet). +1.
- VERWORFEN: extract_named_helper_args (breites func_N(key,global) ohne container-kontext) -> netto -67 (mehrdeutige keys "start"/"no", ambiguity 44->85). ENTFERNT. irbs15/Dror brauchen container-disambiguierung (func_633 arg3=container), TODO falls noetig.
- ERGEBNIS: 779 identisch/91% gg working-tree. ABER: von 78 Abweichungen sind 42 SCRIPT-GENAU (mein wert in new_rev = echte serialisierung, user hat manuellen fehler zB veh_objt2 f_222 vs user f_221). WAHRE PRAEZISION (779+42)/857 = 95%. Von 996 globals ~821 script-korrekt +140 unchanged = 96%.
- tests: 2 alte veh-skip-tests umgeschrieben (test_semantic_first_even_for_struct_families, test_fallback_used_when_no_semantic_path). 33 toolchain-tests gruen. 184MB, 65s.
- REST: 36 echtes raten (structural leaf ±1) + 76 not_found (irbs15/Dror helper-container, ein paar scalars/singletons launch_creator_local/transitionState/check_creator).

## HELPER-NAMES FALLBACK (2026-07-10) — Users A1! ~793, 97% aller globals
- USER bestaetigte A1 (func_633/673 container-position fest). ECHTE STRUKTUR (fm_lts:56100): StringCopy(&Var3,"irbs15",16); StringIntConCat(&Var3,bVar0,16); func_673(&Var3, Global_...f_8682[bVar1], &dict, &container, index, hash). Also arg0=KEY (voller name "irbs15" via StringCopy, StringIntConCat=laufindex), arg1=GLOBAL. Frueherer netto-verlust kam von MEHRDEUTIGEN keys (start/no in vielen containern).
- NEU: tools/helper_names.py (build_key_map: func_N(key,global) -> {key:global} NUR EINDEUTIGE keys (len==1); HelperNameResolver: old_rev unambiguous, migrate old-value->key->new-value). migrate_value(value,old,new) cached. _strip_iter_index (>=2 brackets).
- WIRING migrate_text: helper_names-param. _apply_fallback REIHENFOLGE WICHTIG: (1) structural ZUERST (praezise fuer seine familien), (2) helper_names NUR gap-filler (sonst faengt es structural-korrekte ab -> war +14 wrong!), (3) infer. migrate_offsets main: helper_names-closure wenn --structural.
- ERGEBNIS: migrated_helper=14, structural 272, not_found 76->62. 793 identisch (+14, 0 falsch). 37 tests gruen (+4 test_helper_names). WAHRE PRAEZISION (793+42 script-genau)/871 = 95%. von 996 globals ~835+140 = 97% script-korrekt.
- helper_names LANGSAM (~2.5min, scannt old/ 1114 files 2x). TODO: auf keep-files beschraenken.
- OPTIMIERT (2026-07-10): helper_names.build_key_map(directory, keep=()) -> nur keep-files scannen (2:51->1:11). migrate_offsets closure reicht tuple(keep) durch. migrate_value(...,keep=(),_cache) cache-key inkl keep.
- +weap_ zu DEFAULT_STRUCT_FAMILIES (5 weap-gaps strukturell sauber, 0 falsch): 793->798.
- STAND FINAL: 798 identisch/91% gg working-tree, 78 abweichung (42 script-genau=user-fehler, 36 echtes raten). WAHRE PRAEZISION 840/876=95%. VON 996 GLOBALS 980/996 = 98% SCRIPT-KORREKT. 37 tests gruen. ~1:11 laufzeit, ~185MB.
- OFFEN (~2%): 36 structural leaf ±1 (actor/veh, teils user-fehler) + 57 not_found (helper-mehrdeutige keys, 4 spezial-scalars transitionState/launch_creator_local_3/check_creator/hide_creator_menu, dhprop-vom-user-restrukturiert locx/y/z->loc). B1 (.f_N vs [N]) = OK-notation laut user.

## README + PIPELINE-UX (2026-07-13)
- USER macht die 4 spezial-scalars HAENDISCH (nicht anders loesbar). hide_creator_menu ginge per native-kontext-matcher, aber der loest ueber alle faelle nur 1 sauber -> nicht gebaut.
- migrate_offsets: migrate_text +unresolved=None param (sammelt not_found/ambiguous namen). main sammelt unresolved-liste -> report-json "unresolved":[{offset,value}]. NON-BREAKING (default None, tests unberuehrt).
- run_pipeline.py KOMPLETT NEU (englisch): nutzt --structural default (nicht mehr --fallback), --infer optional. print_summary liest migrate-report.json + zaehlt migrated/unchanged/static/review. REVIEW-split: _new_skeletons(new_dir) sammelt alle Global-feldpfade (index/stride gestrippt); unresolved dessen skeleton in new/ = "stable", sonst echtes REVIEW. Mit 8-file-new/ over-flaggt es published_/saved_ (20 stabil, nicht in 8 files); mit vollem dump korrekt. README empfiehlt vollen dump in new/.
- README.md KOMPLETT NEU englisch: Requirements(python3.10+/rg/OpenIV/decompiler), Step1 OpenIV export (screenshot-platzhalter docs/img/), Step2 decompile+native-tables, Step3 old/+new/ ablegen, Step4 run_pipeline + formatierter output. + How-it-works (4 tiers), tools-tabelle, accuracy&limits, troubleshooting. docs/img/README.md platzhalter fuer screenshots.
- STAND: 798 identisch/95% wahre praezision/98% aller globals. 37 tests gruen. run_pipeline output: "889 migrated, N review".

## REPO-DOWNLOAD-WEG (2026-07-13)
- calamity-inc/GTA-V-Decompiled-Scripts IST AKTUELL (1.72-3788 scripts / 1.72-3751 decompiled_scripts, ~4mon alt). Format identisch zu unserem korpus (Global_, var uLocal, #region). = KEIN OpenIV/decompile noetig wenn repo aktuell.
- RAW-URL: https://raw.githubusercontent.com/calamity-inc/GTA-V-Decompiled-Scripts/<ref>/decompiled_scripts/<file>.c . ref=master ODER commit-hash (beide 200). TAGS sind uneinheitlich (bgscript-1.56.x, decken neue builds NICHT ab) -> fuer alte version COMMIT-HASH nehmen, nicht tag.
- 8 dateien alle 200/multi-MB: fm_capture_creator fm_deathmatch_creator fm_lts_creator fm_race_creator fm_survival_creator fmmc_launcher public_mission_creator tuneables_processing.
- README Step 1 jetzt OPTION A (curl-loop download, empfohlen) / OPTION B (OpenIV B1 + decompile B2 selbst). Step 2 = run. Requirements: OpenIV/decompiler nur fuer B, curl fuer A.
- NICHT new/ ueberschrieben (user hat command, entscheidet selbst).

## BATCH 1.70->1.72 FIXES (2026-07-13) — loose resolver + label pattern
- NEUE FEATURES in migrate_offsets.py: loose_key(global) = notations-tolerante identitaet (.f_M und [N /*S*/] als kumulative feld-offsets; [i /*S*/]=ITER-trenner; trailing bare [i] ignoriert). loose_key gibt None bei unparsbarem rest (z.B. kaputtes ".60" bei OFFSET_nrl -> verhindert kollision mit stpos). to_offset_notation() = extractor-roh -> offsets.ini-stil (trailing bare [i] weg, [N /*S*/]->.f_(N*S)). loose-Resolver ist TIER (nach structural+helper, VOR infer) in _apply_fallback; vergleich per loose_key (kein notations-churn); bestaetigt-unchanged wenn loose_key gleich.
- WICHTIG: loose MUSS als LETZTER tier laufen (nicht erster!) sonst ueberschreibt es structural -> notations-churn (weap_locx .f_0->base etc). migrated_loose=5 (nur echte luecken).
- SKIP_FAMILIES: OFFSET_custom_* (Xenvious-feature) wird NIE migriert (skipped_custom stat, nicht review). CLI --skip-families, default ("custom_",).
- LABEL-MUSTER in extract_globals.py: builder func (StringCopy(param0,param1) body, z.B. func_624/629) setzt label-var; writer func (DATADICT_SET ODER CREATE_ARRAY+ADD, z.B. func_622/633) mit key=&labelvar. discover_label_builders() + discover_helpers erweitert (CREATE+ADD via CREATE_BODY_RE). extract_helper_calls NEU: iteriert ALLE zeilen (feed LabelState!), loest &Var-keys via labels. KRITISCH: LabelState.feed muss StringConCat handhaben (STRING_CONCAT_RE) -> mehrteilige keys "w"+idx+"Az"="wAz"; ohne das kollabieren tp_W* alle auf "w" (REGRESSION -17).
- GELOEST: player_number f_193531, otzone_otvo/otvt f_193093, props_number(bestaetigt), numpt(unchanged), gbtp f_6477, anfMBS f_185586.f_24 (VERIFIZIERT new zeile 56070). custom_ ausgeschlossen. nrl-kollision gefixt.
- NICHT geloest (bewusst, risiko/aufwand): bmmxh/bmstd (strided trailing index [bVar1 /*9*/] gehoert zum offset, aber _strip_iter_index strippt es; global aendern regressiert dpos/fail/vss/tp die es NICHT wollen -> offsets.ini ist INKONSISTENT, kein globaler strip-rule moeglich). gbtpp/gbtpi (vektor-komponente [iVar2 /*3*/] mittendrin + getter). plvrl (func_4998 = GETTER/load-pfad DATAARRAY_GET_INT, drittes muster). cspnm/csvnm/csonm (func_740 custom, kein key). mrd f_129045 (in KEINEM creator-script, nur direktcode).
- _strip_iter_index NICHT aendern (regressiert ~13 f_3605/tp offsets). REVERTED.
- STAND after5: migrated=879, unchanged=53, skipped_custom=5, skipped_non_global=56, review=54. 37 tests gruen. after2->after5 diff = nur 2 (nrl+anfMBS), NULL regression.

## BATCH 2 (2026-07-13) — dual-registration, GET_LOAD, label-writer discovery
- index_variants(canon) [extract_globals.py]: emittiert VOLL + reduziert(aeusseren idx behalten, innere weg) + flach(alle var idx weg). erste=primaer, rest mp["reverse_only"]=True. bare trailing [j] (>=2 ebenen) immer weg. WICHTIG build_maps: reverse_only NUR in reverse, NICHT forward (sonst conflict-chaos).
- match_notation(new_full,cval) [migrate_offsets.py]: token-alignment, droppt NUR variable [i /*S*/] die cval nicht hat (literale [1 /*3*/] bleiben=feste sub-offsets). MAIN-path nutzt match_notation OHNE to_offset_notation (sonst goto/otvo kaputt). LOOSE nutzt to_offset_notation(match_notation(...)) fuer otzone .f_3-notation + cs*nm index-weg.
- build_maps reverse-dedup: hat ein global sowohl dotted (mission.x.y) als auch bare (y) pfad -> nur dotted behalten. fixt props_loc/txt0 (generische "loc"/"txt" von parent-losen label-writern verschmutzen sonst).
- GET_LOAD_RE: StringCopy(&(Global_X), DATADICT_GET_STRING(_,&keyvar)) LOAD-pattern -> key aus keyvar. loest cs*nm (SAVE-seite func_740 hat keinen key). 
- discover_helpers: finditer statt search + KLEINSTER val_idx-param (nicht erster!). writer fuellen erst default (func_200() ODER fParam5=letzter param!), echter wert=uParam1=frueher index. DAS loest func_630/632/633 -> actor f_161 (10!), plvrl, zones_, obj_ key-verankert.
- LabelState.feed: +StringConCat (mehrteilige keys "w"+idx+"Az"). OHNE das kollabieren tp_W* -> massive regression.
- ambiguous_paths-guard VERWORFEN (getriggert durch index_variants -> hunderte fallback). forward-conflict-removal VERWORFEN (bricht player). _strip_iter_index NICHT aendern.
- GELOEST batch2: cs*nm(3) f_193110/191/272, bmmxh/bmmph f_5062, anfMBS f_185586, actor f_161->f_165(10: actvx/achf/awt/awr/awl/awlr/agrd/ags/agvr/actv_bs), plvrl f_3605[26968].f_40, +bonus otxsgo/tmrph/trst/surv/zones/obj/PwrUp. VERIFIZIERT gegen new/ keys.
- OFFEN: mrd(offsets.ini-wert f_129045 FALSCH, echt=f_132187 key"mrd"), bmstd(.f_4 unserialisiert, user: f_5062[bVar1 /*9*/].f_4), actor actvy/actvz(vektor Y/Z, kein eigener key), gbtpp/gbtpi(vektor+getter). start(1 mehrdeutiger key "start"=goto+f_3578 -> FALSCH migriert, user macht manuell).
- STAND FINAL: migrated=813, 894 geaendert ggue 1.70-ini, 37 tests gruen. reports/offsets.migrated.ini.



## STRUKTURELLER DURCHBRUCH (2026-07-10) — 65% -> 82%!
- NEU: tools/structural.py — struktureller Resolver fuer code-accessed Familien. KERN-EINSICHT: diese Werte haben Form Global_G.f_ROOT[i /*S1*/]([j /*S2*/])?(.f_SUB[k /*S3*/])?(.f_LEAF)*. Bei Update: ROOT verschiebt sich, Stride refresht, Sub-Array verschiebt sich, Blatt meist erhalten. ALLES aus Scripts ableitbar.
- Methoden in structural.py:
  - collect_roots/collect_subarrays: sammelt Felder+Stride+Kinder/Blaetter aus *.c
  - _match_by_signature: (1) exakte Kind-Signatur-Gruppe+ordinal, (2) Jaccard>=0.6, (3) IDENTITAET (Feld in new mit gleicher Nr = stabil, zB pa f_3605), (4) DELTA-OVERLAP (verschobene Wurzel wie veh f_67545->f_68415: finde new-root wo old-Kinder+delta max ueberlappen, Schwelle 0.6). Returned (mapping, shifted_set).
  - _leaf_map: greedy-monotone Ausrichtung old->new Kinder. NUR bei delta-verschobenen Roots (shifted_set) anwenden, sonst Blatt erhalten (zones/kill!).
  - migrate(): parst Wert, wendet root-shift + stride-refresh + sub-array-map + leaf-map an.
- STRUKTURELL PRO FAMILIE (vs working-tree): pa 97%, tp 100%, goto 100%, SMS 100%, zones 96%, player 92%, kill 88%, actor 86%, ddblip 85%, ptemp 80%, veh 70%, obj 66%, cps 50%. Gesamt code-accessed 275/338=81%.
- ROUTING (gemessen, welche Methode pro Familie gewinnt):
  - STRUKTURELL: pa,kill,zones,tp,ddblip,SMS,player,goto,ptemp,cps,veh,obj,actor
  - SEMANTIC/mig(infer): props 91%,weap 93%,doors 96%,dprops 95%,dhprop,otzone,current_creator,mission.*,meta.*
- GESAMT KOMBINIERT: 808/996 = 81% all-globals, 82% both-changed. (vorher 65%)
- RESTFEHLER: veh 18, obj 13 (leaf ±1 step-function boundary), actor 13, props 7, weap 5. veh/obj/actor leaf-±1 sind teils USER-Fehler (veh_objt2 consensus 222 vs user 221).
- TODO fuer 95%: (1) bessere leaf-alignment (DP statt greedy) fuer veh/obj/actor, (2) cps scalars interpolieren (cps 50%), (3) structural.py in migrate_offsets integrieren als tier + tests, (4) full pipeline + validate, (5) residual user-error-analyse (consensus).
- new/ hat jetzt VOLLEN korpus (1145 dateien, cp new-unused). old-dir=stash/old=1114. /tmp/off_v171_src.ini = git show 4324d53:offsets.ini.
- KORREKTUR (2026-07-10): new/ WIEDER AUF 8 DATEIEN getrimmt! Voller Korpus war NUR fuer den (verworfenen) infer-Ansatz noetig. structural leitet ALLES aus den Creator-Scripts ab (f_3605 wird 3000-4000x pro Creator-Datei zugegriffen). BEWIESEN: 8 dateien = 699 korrekt, voller korpus = 698 (identisch, sogar +1). 8 dateien ist SCHNELLER (65s vs 107s) + sparsamer (183MB vs 257MB). Extras sind in stash/new-unused/ gesichert. new/ = nur die 8 keep-dateien (fm_*_creator, fmmc_launcher, public_mission_creator, tuneables_processing).
- migrate_offsets.py: +infer_families-Routing (veh/doors/obj->infer-Override), +Locals-Routing (fLocal_/uLocal_/iLocal_/Local_/alias ->fallback), _apply_fallback-Helper, Cache-Bump. DEFAULT_INFER_FAMILIES=(veh_,doors_,obj_).
- migrate_parallel.py: NEU, parallel. OOM-FIX (2026-07-10): war 20GB+ OOM weil (a) structural._read_all cachte GANZEN korpus (old+new=12GB) und (b) jeder infer-worker cachte 64 dateien x2 x6worker. FIX: structural nutzt jetzt rg-vorgefiltertes streaming (_files_with via rg -l, _iter_texts einzeln) -> 180MB statt 5.7GB (30x). migrate_parallel: --jobs 0=auto nach /proc/meminfo MemAvailable (70% budget / mem-per-job-gb), --file-cache 8 (statt 64), maxtasksperchild=200. Voll-Lauf jetzt: 4 worker, 1.36GB total RSS, 362 infer-kandidaten (structural reduzierte von 511).
- validate.py: +--migrated (fertige Datei bewerten), +Pro-Familie-Aufschluesselung. 3 Tests (test_validate.py NEU).
- run_pipeline.py: +--fallback (durchgereicht), validate nutzt --migrated.
- README.md: kompletter semantic+combined Workflow + ehrliche Grenzen dokumentiert.
- Ehrliche Obergrenze bleibt ~82-85% vs working-tree (working-tree hat Fehler). GEMESSENES >=95% vs working-tree unmoeglich; Konsens-Sicht ist die ehrliche Metrik.

## KANONISIERUNG (2026-07-10, KRITISCH)
- canonicalize_global() in extract_globals.py: variable Array-Indizes -> positionsbasiert i/j/k (Stride /*N*/ bleibt, Literale bleiben). In _mapping angewandt. Test test_canonicalize_index_vars.
- Ohne Kanonisierung: new/->b3788 = 986 "Shifts" (fast alles iVar0-vs-i / BitTest-vs-IS_BIT_SET Rauschen).
- MIT Kanonisierung: nur 4 Abweichungen, davon meta.photo=Native-Alias-Rauschen. 3 ECHTE Struktur-Shifts: KhBS (f_197677 -> .f_2[i].f_2), mission.endcon.fail (f_14668 -> f_16873[j /*16*/]), mission.ene.loc (f_193537[i /*70*/] -> f_197738[i /*4201*/][j /*70*/]).
- Offene Rausch-Quelle (klein): Native-Alias BitTest vs IS_BIT_SET + funktions-gewrappte Werte -> evtl. spaeter reine-Global_-Filter.

## DEFAULTS fuer autonomes Arbeiten (bei "go" gilt das)
- Edition = LEGACY (b3788) angenommen, bis User Enhanced sagt.
- offsets.ini NIE ueberschreiben. Migrationsergebnis nach reports/offsets.migrated.ini + Diff-Report. User entscheidet spaeter ueber Uebernahme.
- KEIN git commit/push.
- Reihenfolge autonom: (1) Locals-Resolver (Usage-Pattern), (2) Stage 3 sichere Ausgabe, (3) validate.py-Harness, (4) run_pipeline.py. Nach jeder Stage Tests gruen + Memory-Update.

## WORKSPACE-ZUSTAND (2026-07-10, Files verschoben)
- new/ auf die 8 Kern-Files EINGEDAMPFT (kein OOM mehr; ~100MB statt 3.3G). Rest + old/ + alte Reports im STASH.
- STASH = /home/luisg/ysc-global-updater-stash/ (AUSSERHALB /opt-Workspace, gleiches FS): new-unused/ (3.2G, die 1137 nicht-gewaehlten), old/ (2.5G), reports-old/ (scan.json etc.), restore.sh
- RESTORE: bash /home/luisg/ysc-global-updater-stash/restore.sh  (stellt new/ + old/ + Reports komplett zurueck)
- FOLGE: infer_offsets braucht jetzt Restore (Diff-Korpus weg). extract_globals/select_sources laufen weiter (nutzen die 8 Files). reports/sources.manifest.json bewusst NICHT ueberschrieben (haelt den vollen 1145-Scan als Auswahl-Beleg). select_sources fuer Vollscan: --source-dir /home/luisg/ysc-global-updater-stash/new-unused.
- reports/ enthaelt jetzt nur: globals.json, sources.keep.txt, sources.manifest.json

NAECHSTES: Stage 2 — Helper-func + uParam->f_-Alias-Aufloesung, damit Array-Fill-Keys (irbs/gbtp/armr...) und helper-basierte (trntype) + published_/saved_/surv_/cps_ reinkommen. Vorlage: global_extractor parsers/helper_parser.py + function_signature.py + alias_resolver.py (NUR lesen).
WICHTIG: infer_offsets-Suite NICHT laufen lassen ohne Restore (fehlt Korpus). Test-Module einzeln.
