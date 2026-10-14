# Payloads of the custom funcs

Xenvious' `scrpatches.json` of each edition carries the customfuncs payload of each creator in one
entry ("custom funcs in extra page", `page: true`, `page_base`). The sources here are the only
readable form; `src/<edition>/BUILD` names the build they are written for.

- Enhanced: the payload is the first extra code page the creator gets when it loads (ScriptSpace,
  `[SCRIPTSPACE]` in offsets.ini).
- Legacy: resources cannot grow there, so the payload lives in a script function given up for it,
  the vehicle mod preset table (`[SCRIPTVICTIM]` in Xenvious' legacy offsets.ini: function, payload,
  data page, string page array). The patch "give up the vehicle preset table" makes the table return
  at once. The hook "run patched loop func without args" keeps the draw call it replaces (dl_hook
  makes it). `victims/layout.py <build>` prints those values (xupdate writes them into [SCRIPTVICTIM]) (call graph and dominator search in `victims/dom.py`; the table is found by its plate string
  "47BQT444").

Files:

- `src/<edition>/<script>.ysa`: the customfuncs of each creator (fn0 dispatch, dimensions, hovered
  model, and per creator areas, refresh, race tuning and garage). Their variables live in the data
  page (string page 63) instead of custom globals: variable n at `0xFC018 + n * 8`, numbered like
  the old `Global_2884084 + n` (layout in `drawer.py`).
- `drawer.py`: generates the shape drawer (dl_hook, dl_main, dl_quad, dl_entry) that `build_page.py`
  appends to every payload. Its labels draw in graphics order 1 behind the pause menu.
- `zonelabel.py`: generates zl_wrap/zl_label for the creators that draw zones (LTS, Capture, DM,
  Race): "(Zone n)  <type>" over each zone the game draws, in the zone's colour, fading with
  distance. The hook "zone labels" points the zone loop's draw CALL at zl_wrap.
- `datafile.py`: generates df_main for the creators that load and save job files (LTS, Capture,
  DM, Race, Survival). dl_main calls it every frame; on request it hands Xenvious
  `DATAFILE_GET_FILE_DICT(0)` and a dict "xv" with one node of every type, so Xenvious' job file
  export and import (JobDatafile) need no addresses from the game's exe.
- `teleport.py`: generates tp_main for every creator. dl_main (and in the race test fn13) calls it
  every frame; on request it moves the player, or the vehicle the player sits in, with
  `SET_ENTITY_COORDS_NO_OFFSET`, like YimMenuV2. Writing the position into memory does not hold in
  a test, where the physics run.
- `build_page.py`: assembles source + drawer + zone label + datafile probe + teleport at each `page_base`, checks every CALL into
  the page (and on Legacy that the payload ends before the string page array), and points the
  patches that CALL into the page (`HOOKS`) at their labels. Without `--write` it only reports;
  `--edition` picks the edition, `--target` the scrpatches.json (default: Xenvious' one).
- `relocate.py`: moves the sources to another build (CALL targets by function fingerprint, strings
  by text, statics/globals/enum constants by the votes of Rockstar's code at the aligned places,
  `; %key` by the offsets.ini delta, `; ~IOFFSET n` to let only uses next to that instruction vote).
  Checked Legacy 1.73-3889 <-> Enhanced 1.0.1158 both ways: all six sources come out as written by hand.
