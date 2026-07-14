# Offset Audit

Stand: 2026-04-22

## Reststatus

- Offene Restmenge nach der breiten Pruefung: `0` Offsets
- Davon `0` im grossen `team`-Block auf altem Stride `/*26949*/`
- Davon `0` im Block `current_creator_*`
- Zusaetzlich verbleiben `7` quoted Werte ausserhalb des Inferenzpfads; das sind statische Layout-/Teleport-/Hex-Konstanten und keine dynamischen Script-Mappings
- Im RAM-schonenden `--fast-report` verbleiben aktuell `0 review_needed`-Eintraege; die schnelle Triage ist fuer den aktuellen `old/`- und `new/`-Stand damit vollstaendig sauber
- Fuer die bereits aktualisierten Familien `dhprop`, `zones`, `goto`, `pa`, `player`, `launch_creator_local`, `veh`, `obj`, `weap`, `props`, `dprops`, `doors`, `otzone`, `actor`, `cps`, `kill` ist aktuell keine weitere Script-Implementierung noetig

## Ausserhalb des Inferenzpfads

- `OFFSET_creator_index = "20,58"`
- `OFFSET_tp_player_loc = "8,30,50"`
- `OFFSET_tp_playerveh_loc = "8,DD0,30,50"`
- `OFFSET_tp_playervehcam_loc = "8,DD0,90"`
- `OFFSET_tp_cam_loc = "8,90"`
- `OFFSET_isplayerinveh = "8,14A2"`
- `OFFSET_creator_cam_loc = "0x60"`

## Unveraendert / bestaetigt

- `OFFSET_launch_creator_local_1 = "1574589"`
- `OFFSET_launch_creator_local_2 = "1574589.f_2"`
- `OFFSET_launch_creator_local_3 = "Global_33776"`
- `OFFSET_launch_creator_local_4 = "Global_1574943"`
- `OFFSET_launch_creator_local_5 = "Global_1575013"`
- `OFFSET_check_creator = "Global_1925601"`
- `OFFSET_hide_creator_menu = "Global_24529.f_9243"`

## Bereits aktualisiert

- `OFFSET_current_creator_worker_survival = "fLocal_7208"`
- `OFFSET_current_creator_worker_lts = "fLocal_8752"`
- `OFFSET_current_creator_worker_mission = "fLocal_7338"`
- `OFFSET_current_creator_worker_dm = "fLocal_43005"`
- `OFFSET_current_creator_worker_race = "fLocal_50788"`
- `OFFSET_current_creator_worker_capture = "fLocal_8389"`
- `OFFSET_current_creator_worker_offset_menu = "f_533"`
- `OFFSET_current_creator_pre_survival = "uLocal_8126"`
- `OFFSET_current_creator_pre_lts = "uLocal_9670"`
- `OFFSET_current_creator_pre_mission = "uLocal_8256"`
- `OFFSET_current_creator_pre_dm = "uLocal_35201"`
- `OFFSET_current_creator_pre_race = "uLocal_42984"`
- `OFFSET_current_creator_pre_capture = "uLocal_9307"`
- `OFFSET_current_creator_cam_heading_capture = "uLocal_41598"`
- `OFFSET_current_creator_cam_heading_lts = "uLocal_41961"`
- `OFFSET_current_creator_cam_heading_dm = "uLocal_45136"`
- `OFFSET_current_creator_cam_heading_race = "uLocal_42901"`
- `OFFSET_current_creator_cam_heading_mission = "uLocal_7133"`
- `OFFSET_current_creator_cam_heading_survival = "uLocal_7003"`
- `OFFSET_current_creator_test_capture = "iLocal_1315"`
- `OFFSET_current_creator_test_mission = "iLocal_40924"`
- `OFFSET_current_creator_test_dm = "iLocal_45661"`
- `OFFSET_current_creator_test_race = "iLocal_53837"`
- `OFFSET_current_creator_refresh_capture = "iLocal_1305"` bleibt unveraendert
- `OFFSET_current_creator_refresh_mission = "iLocal_41504"`
- `OFFSET_current_creator_refresh_dm = "iLocal_4683"`
- `OFFSET_current_creator_refresh_race = "iLocal_8761"`
- `OFFSET_current_creator_refresh_survival = "iLocal_40817"`

## Current Creator

- Der Block `current_creator_*` ist nach aktuellem Stand vollstaendig aktualisiert
- Bereits aktuell / bestaetigt:
  `test_survival`, `test_capture`, `test_lts`, `refresh_lts`, `refresh_capture`

## Team Block

- Der Block `team` ist nach aktuellem Stand vollstaendig aktualisiert
- Quellseitig verifiziert und in `offsets.ini` uebernommen:
  `base_status`, `inventory`, `irbs`, `team_boosts`, `race_layout`, `match_config`, `speed_damage`, `armor_spawn`, `gbmeta`
- Bereits zuvor uebernommen und weiterhin bestaetigt:
  `capture_delivery`, `team_NEXT`, `team_NEXT_settings`, `bmm`, `gbtp`, `bdprt`, `bdpst`, `txt0`, `armr`
- Aktuell ist fuer den bekannten `team`-Block keine weitere Script-Implementierung noetig

## Script-Status

- `tools/infer_offsets.py` kann jetzt Globals, Local-Werte und bare Global-Aliase verarbeiten
- `tools/infer_offsets.py` kann jetzt auch verifizierte `current_creator`-Relativfelder wie `f_533`, `f_598` oder `f_982` verarbeiten
- `tools/infer_offsets.py` hat jetzt einen RAM-schonenden `--fast-report`-Modus, der unveraenderte Werte batchweise erkennt und Restfaelle nur noch als `review_needed` markiert
- `tools/infer_offsets.py` ueberspringt im `--fast-report` jetzt unnoetige Einzelpruefungen fuer bereits batch-gepruefte Globals; der schnelle Report liegt damit auf dem aktuellen Stand bei `1047 unchanged`, `0 review_needed`, `0 unresolved`
- `tools/infer_offsets.py` erkennt im Batch-Scan jetzt auch Parent-Pfade von tieferen Global-Zugriffen, wodurch Root- und Vektor-Familien deutlich seltener faelschlich auf `review_needed` fallen
- `tools/infer_offsets.py` kann jetzt mit `--offset-file` gezielt eine Review-Liste oder Whitelist in Batches tief pruefen
- Die Dateivorfilterung nutzt jetzt spezifischere Anker wie `Global_4718592.f_3605` statt nur `Global_4718592`
- Fuer `current_creator_*` kann das Script jetzt sichere `worker_*`- und `pre_*`-Roots direkt aus den `new/`-Creator-Dateien ableiten
- Fuer `current_creator_*` erzwingt das Script fuer diese Root-Typen jetzt die korrekten Prefixe `fLocal_` und `uLocal_`
- Fuer `current_creator_*` kann das Script jetzt auch sichere `cam_heading_*`-Helper fuer `capture`, `lts`, `dm` und `race` direkt aus den `new/`-Creator-Dateien ableiten
- Fuer `current_creator_*` kann das Script jetzt auch sichere `cam_heading_*`-Helper fuer `mission` und `survival` direkt aus den `new/`-Creator-Dateien ableiten
- Fuer `current_creator_*` kann das Script jetzt auch sichere `test_*`-Status-Locals fuer `mission`, `dm` und `race` sowie `refresh_*`-Gates fuer `dm`, `race` und `survival` ableiten
- Fuer `current_creator_*` kann das Script jetzt auch `refresh_mission` ueber eine missionspezifische Network-Gate-Heuristik zuordnen
- Fuer `current_creator_*` behandelt das Script jetzt auch die verifizierte Feldverschiebung `worker_offset_menu: f_530 -> f_533`; die restlichen verifizierten Relativfelder bleiben fuer diesen Stand stabil
- Fuer `launch_creator_local_3`, `check_creator` und `hide_creator_menu` nutzt das Script jetzt verifizierte Spezialmappings statt unscharfer Kontexttreffer
- Fuer den `team`-Block kann das Script jetzt verifizierte stride-only- und `+1`-Mappings fuer `base_status`, `inventory`, `irbs`, `team_boosts`, `race_layout`, `match_config`, `speed_damage`, `armor_spawn` und `gbmeta` direkt ableiten
- Fuer die derzeit bekannte `offsets.ini` ist keine weitere `team`-spezifische Script-Arbeit offen

## Fast-Report Restliste

- Der aktuelle schnelle Vollscan liegt in `reports/scan-fast.json`
- `reports/review-needed.txt` ist fuer den aktuellen Stand leer; es gibt derzeit keine dynamischen Restfaelle fuer einen gezielten `--offset-file`-Nachlauf
- Der schnelle Report schliesst auf diesem Stand mit `1047 unchanged`, `0 review_needed`, `0 unresolved`, `7 unsupported quoted`

## Alte Haupt-Roots

Die zuvor grossen alten Haupt-Roots wie `veh`, `obj`, `weap`, `props`, `dprops`, `doors`, `otzone`, `dhprop`, `zones`, `goto`, `pa`, `player`, `kill`, `cps` sind nach aktuellem Stand nicht mehr in `offsets.ini` vorhanden.
