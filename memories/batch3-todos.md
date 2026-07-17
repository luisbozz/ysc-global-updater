# BATCH 3 TODOs (user feedback 2026-07-13)

Migration 1.70->1.72. offsets.ini=OLD, reports/offsets.migrated.ini=NEU.

## Probleme (user)
1. [ ] actor_loc/head/veh/number FALSCH: generische keys loc/head/veh/no kollidieren
       -> landen im PLAYER-struct f_197738 statt actor f_90320. alt actor_loc=f_89187[j /*1213*/]
       -> soll f_90320[i /*1269*/]. FAMILIEN-KONSENS: die meisten actor_ sind f_90320[i /*1269*/].
2. [ ] start FALSCH: soll f_3578's neuer wert (zeile 58050 old: func_636("start",&Global_4718592.f_3578)).
       migrated hat goto f_5[...]. "start" key mehrdeutig (goto + f_3578).
3. [ ] type: alt = "Global_4718592" (BARE ROOT!). tool haengt faelschlich .f_121628 an ("type" key). bare roots nie migrieren.
4. [ ] _NEXT offsets: OFFSET_<fam>_NEXT = <int stride>. neuer stride aus migriertem familien-root.
       bsp actor_NEXT 1213 -> 1269 (weil f_89187[1213]->f_90320[1269]).
5. [ ] REALISTIC-DELTA sanity check: f_89187->f_197738 (+108551) unrealistisch. f_89187->f_90320(+1133) ok.
       familien-konsens/delta nutzen um falsche cross-family matches zu erkennen.
6. [ ] current_creator_* LOCALS: fLocal_/uLocal_/iLocal_/f_N. per pattern matchen.
       XENVIOUS DOCS: launch_creator_local_*/transitionState = "search old, find new".
       current_creator_[worker/pre/test]_[mode] = "search old, find new".
       worker_offset_refresh/_menu = "search first WEAPON_PISTOL".

## Ansatz
- #1+#5: FAMILIEN-KONSENS post-pass. pro struct-family konsens-root ermitteln, ausreisser via structural neu.
- #3: bare-root globals (Global_N ohne .f_) nicht migrieren (skip).
- #2: start-key disambiguieren oder manuell.
- #4: _NEXT aus family stride ableiten.
- #6: locals via kontext-matching (infer_offsets?) old->new.

## Status
- ERLEDIGT #1 FAMILIEN-KONSENS: postprocess_families() in migrate_offsets.py. pro struct-family konsens-root/-stride;
  array-ausreisser (gleicher old-root, anderer new-root) -> konsens-root + leaf-interpolation aus siblings.
  _number per familien-root-delta. VERIFIZIERT: actor_loc=f_90320[1269], head=.f_3, veh=.f_108(+3 delta bestaetigt), number=f_90314.
- ERLEDIGT #3 BARE-ROOT: migrate_text skippt Global_N bzw Global_N[literal] (versions-stabil). fixt type=Global_4718592, goto_number=[0]. stat skipped_root=9.
- ERLEDIGT #4 _NEXT: postprocess setzt OFFSET_<fam>_NEXT auf neuen stride. actor=1269,veh=626,obj=648,weap=172,zones=192,kill=55.
- ERLEDIGT #2+#5 BASE-GUARD: _base(v)=Global_N. _emit gibt bool, lehnt cross-global ab (anderer Basis-Global=fehlmatch).
  main-path + alle fallback-tiers. fixt start=f_3578. NUR start wechselte je den base -> regel sicher.
  BONUS dadurch: mrd f_133077(VERIFIZIERT korrekt!), nrl .f_60, trcmn f_185956.
  ABER bmstd -> .f_3 (sollte .f_4, beast intern unveraendert; .f_4 unserialisiert) = leichter fehler, user verifiziert.
- test_fallback_used: ini auf Global_4980736.f_0 geaendert (war Global_999, base-guard-konflikt).
- STAND: migrated=901, family=4, skipped_root=9, review=26. 37 tests gruen.
- ERLEDIGT #6 LOCALS: tools/locals.py KONTEXT-MATCHER. migrate_local(old_value, offset_name, old_dir, new_dir).
  ALLE 30 current_creator_* locals gematcht, deltas MONOTON mit index (starke validierung).
  MODE->FILE: survival/capture/lts/dm/race->fm_*_creator; MISSION==LTS (alle local-werte identisch, teilt script).
  offsets.ini fLocal_7143 = decompiler Local_7143 (typ-praefix nur interpretation, NUMMER=stack-idx stimmt).
  4 STRATEGIEN (in migrate_local, reihenfolge):
   1. struct-init: struct<K> Local_N={...} identische init-liste in new. (test iLocal_145->147 VERIFIZIERT exakt)
   2. zeilen-anker (dist=0): strings/natives auf SELBER zeile wie local, in new local auf selber ankerzeile.
      (worker fLocal_7143->7208 via "SC_RESET_W"; pre iLocal_8045->8126). WICHTIG: window=0 sonst nachbar-local-verwechslung.
   2.5 switch-fingerprint: switch(iLocal_N) case-body-anker (natives/strings) mit new switch(Local_M) matchen.
      (refresh iLocal_40272->40562 via SET_OVERRIDE_WEATHER; fixt refresh_lts 1690->1695). Control-flow-locals ohne eigene anker.
   4. decl-ordinalzahl: TOTE locals (nur deklariert, cam_heading uLocal_41708) = position vom ende des
      global-decl-blocks vor void __EntryFunction__(). old lts letzte=41708 -> new lts letzte=42000. +REDIRECT
      auf declaring-file wenn idx nicht in mode-file (cam 41708 lebt nur in lts, survival hat 41312).
   3. fenster-anker (window=15): letzter fallback.
  BUG-FIX: _LOCTOK_RE muss [a-z]?Local_ (optional typ-praefix) sein, NICHT \bLocal_ (matcht iLocal_ nicht, kein wortgrenze i|Local).
  INTEGRIERT: migrate_text() hat local_resolver-param; main() baut ihn aus tools.locals.migrate_local (immer an).
   branch `not val.startswith("Global_")`: wenn ^[a-z]?Local_\d+$ -> local_resolver zuerst, stat migrated_local.
  TESTS: tests/test_locals.py (8, synthetische fixtures alle 4 strategien) GRUEN. gesamt 45 tests gruen (1 skip).
  FINALER LAUF: migrated_local=30 (alle!), migrated=814, family=4, structural=79, loose=3, helper=1, skipped_root=9.
   output -> reports/offsets.migrated.ini + reports/migrate-report.json.
  worker_offset_refresh/_menu (f_562/f_530), worker_heading f_1, pre_* f_N, cam_heading_offset f_7 sind FELDER
   im struct (KEINE locals, offset innerhalb) - NICHT von locals.py behandelt, bleiben wie in offsets.ini (skipped_non_global).
  BATCH-3 KOMPLETT (alle 6 TODOs). test_infer_offsets subtests failen datenabhaengig (old-wert nicht in new) = erwartet, nicht meine aenderung.
- PERFORMANCE (user: "script dauert zu lange, liegt das am memory kram?"): NEIN, locals=~20s. build_maps=145s (80%!).
  FIX: build_maps() plattencache reports/.cache/maps-<dirhash>.json (key=datei size+mtime, auto-invalidate) + ProcessPoolExecutor
  datei-parallel (serieller fallback). --no-cache flag. KALT 87s (war 180s), WARM 46s. output identisch. .gitignore hat .cache schon.
  _extract_one() top-level fuer pickling. main() cache_dir=root/reports/.cache.
- WORKER-FELDER (user: "worker_ vars bauen auf worker_capture/lts auf, also fLocal_8321.f_562"): f_562 etc = FELD relativ
  zum worker-local (worker.f_562). struct-umbau verschiebt feld: VERIFIZIERT f_562->565, f_530->533, f_3->4 (worker survival),
  f_1->1 stabil. tools/locals.py migrate_field(field_value, cont_old, cont_new, old_dir, new_dir, creator_file="fm_survival_creator.c").
  ANKER: _field_signatures() = zugriffskontext je feld: op+konstante (=94/!=94/==3), #switch, nat:NATIVE, str:STRING.
  container (7143->7208 worker, 8045->8126 pre) via migrate_local aufgeloest. voting nach token-seltenheit im new-struct.
  INTEGRIERT: migrate_text field_resolver-param; main() _container()+field_resolver (worker/pre via name, gated auf ^f_\d+$).
  stat migrated_field=4. pre-felder die via PARAM-indirektion (uParam0.f_534) zugegriffen werden = None (kein direkter Local_.f_N),
  bleiben unveraendert (f_534/f_759/f_756/f_982 kein direkter zugriff in survival). cam_heading_offset f_7 stabil (cam=toter local).
  TESTS: +2 (test_struct_field_by_constant/unknown), 47 gesamt gruen. output reports/, migrated_local=30 migrated_field=4 total=935.
- PRE-FELDER (user: "guck dir die pre_ variablen an"): DURCHBRUCH - pre_* felder verteilen sich auf ZWEI structs!
  f_759/760 (test1/test2) liegen im WORKER-struct (Local_7143), NICHT pre. f_981/982/1035/271/272/597/598 im pre.
  f_534/756 (menu_gm/publish) param-indirekt (nested f_1761.f_756, kein direkter Local_.f_N).
  migrate_field() UMGEBAUT: nimmt jetzt candidates=[(cont_old,cont_new),...] (worker UND pre), autodetektiert per
  DIREKTZUGRIFF-anzahl welcher container. dann pro container: 1) ALIGNMENT (_align_runs aus structural.py ueber alle
  direkt zugegriffenen feldnummern old vs new = layout-weite zuordnung inkl inserts) 2) KONTEXT (_context_field, alte
  signatur-logik) - uebereinstimmung=hohe konfidenz 3) STEP-interpolation (naechstes gemapptes feld <=n, KEIN extrapolieren
  ueber max(domain)). fallback path2: step auf name-gehintetem container (param-indirekt f_534/756).
  ERGEBNIS migrated_field=8: worker_offset_refresh f_562->565, offset_menu 530->533, pos 3->4, pre_test1 759->762,
  test2 760->763, pre_color 1035->1041, category_num 981->987, prop_num 982->988. STABIL(unchanged): heading f_1, 
  previous_menu f_271, current_menu f_272, idk f_598, place_object_type f_597, menu_gm f_534 (step, ≤598 stabil-zone).
  UNSICHER: pre_publish f_756 (step +0 -> f_756, liegt aber im insert-gap [632,806], koennte +6=f_762 sein) - user verifizieren.
  cam_heading_offset f_7 stabil (cam=toter local). worker-struct hat 2 inserts (+3 ab 530, +11 ab 762); pre +6 ab 806.
  field_resolver in migrate_offsets: order=name-hint (worker/pre), migrate_field waehlt echten container per direktzugriff.
  TESTS 48 gruen (+test_struct_field_container_autodetect). PERF unveraendert (warm ~50s). output reports/ total=939.
- NEUE OFFSETS HINZUFUEGEN (user: "wie funktioniert das wenn ich neue lokal/global hinzufuege? bspw Local_9223.f_808 / OFFSET_cmxdftms"):
  offsets.ini hatte KEINE kombinierten Local_N.f_M werte (locals bare + felder separat). Feature ergaenzt:
  1. migrate_local: cf=None (generischer name ohne _mode-suffix) -> _find_declaring_file fallback. bare local mit
     beliebigem namen aufloesbar (OFFSET_x="uLocal_9223" -> uLocal_9307).
  2. migrate_local_field(value, old_dir, new_dir) NEU: _LOCAL_FIELD_RE ^([a-z]?Local_(\d+))((?:\.f_\d+)+)$.
     findet datei via _find_declaring_file, loest local (creator_file explizit) + ERSTES feld (migrate_field candidates
     [(idx,new_idx)]) auf, nested folge-felder .f_A.f_B unveraendert. uLocal_9223.f_808 -> uLocal_9307.f_814 (verifiziert,
     survival-referenz f_808->814 stimmt ueberein).
  3. migrate_text: local_field_resolver-param, branch ^[a-z]?Local_\d+(?:\.f_\d+)+$ VOR bare-local check, stat migrated_local.
  VERIFIZIERT end-to-end mit test-ini: uLocal_9223.f_808->uLocal_9307.f_814, uLocal_9223->uLocal_9307, Global_993502.f_4.f_90 ok.
  README: neue sektion "Adding a new offset" (tabelle global/local/local-field/field). test_local_field_combined. 49 tests gruen.
  WORKFLOW fuer user: zeile OFFSET_name="wert" in offsets.ini (aktuelle version), updater laufen lassen, wert wird migriert.
- STRIDE/_NEXT-OFFSETS (user: "props_next/dprops_next/dhprop_NEXT/doors_NEXT hat nicht geklappt + loesung fuer next_settings/team_NEXT_settings/team_NEXT"):
  URSACHE: _NEXT_RE matcht nur GROSS _NEXT als suffix (props_next klein faellt durch); und postprocess_families (c)
  laeuft nur fuer struct_families -> props/dprops/dhprop/doors sind keine. team_NEXT/settings passen gar nicht ins schema.
  ERKENNTNIS: ein stride-offset (blanke ganzzahl) = der /*N*/ eines ARRAY-offsets mit gleichem wert.
  team_NEXT 26949=f_3605 team-stride->26968; team_NEXT_settings 4141 + next_settings 69 = die 2 DIMENSIONEN des player-arrays!
  OLD player_loc=f_192001[iVar1 /*4141*/][bVar0 /*69*/] -> NEW f_197738[i /*4201*/][j /*70*/]. also 4141->4201, 69->70.
  LOESUNG: postprocess_strides(migrated_text, ini_text) in migrate_offsets.py NEU. baut old->new stride-map aus
  positions-gezippten /*N*/ aller gepaarten array-offsets. blanke int-offsets (name matcht (?i)next|size|settings)
  werden uebersetzt (praefix-praeferenz >=4 + >=60% mehrheit). _INT_OFFSET_RE haelt bare+quoted+kommentar.
  in main() nach postprocess_families aufgerufen. stat migrated_stride=7.
  ERGEBNIS: props_next 163->165, dprops 255->258, dhprop 7->8, doors 51->53, team_NEXT 26949->26968 (169:2 mehrheit),
  team_NEXT_settings 4141->4201, next_settings 69->70. diff zeigt NUR die 7, struct-family _NEXT (veh 626/actor 1269) unveraendert.
  TESTS: PostprocessStridesTest (3: family/2d-dims/unmatched-untouched). 23 test_migrate+locals gruen. output reports/ total war 939+7.
- VERIFIKATION 4 pre-felder + current_team_test (user bat um pruefung): KEINE code-aenderung, nur verifiziert.
  previous_menu f_271: OLD Local_8045.f_271=Local_8045.f_272 -> NEW Local_8126.f_271=f_272 STABIL korrekt.
  current_menu f_272: OLD f_272==15||277 -> NEW f_272==15||282 (feld gleich, nur konstante) STABIL korrekt.
  place_object_type f_597: OLD func(&Local_8045,f_597,0,0) -> NEW func(&Local_8126,f_597,0,0) STABIL korrekt.
  publish f_756: NICHT bestimmbar - nie als pre.f_756 benannt (xenvious liest per roh-offset). pre-struct hat
    insert in [633,805] wo KEIN feld direkt zugegriffen wird (survival align: 632->632 +0, 806->812 +6).
    kandidaten f_756(+0)..f_762(+6), tool gibt f_756 (step). USER muss in-game verifizieren.
  current_team_test f_3553: OLD iVar0==Global_4718592.f_3553 / f_3553=func_1778(iParam2) -> NEW IDENTISCH
    iVar0==f_3553 / f_3553=func_1824(iParam2). STABIL korrekt. KOMMENTAR (1.70=f_3540) IST VERALTET/irrefuehrend,
    tatsaechliches feld ist f_3553 und wanderte nicht (region f_3535-3561 komplett stabil, num/min/tnum/trel unveraendert).
  FAZIT: 4/5 bereits korrekt im output; nur publish ist echte luecke (feld nie im script benannt).
