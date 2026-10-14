"""Generates the datafile probe (.ysa): hands Xenvious the job file of datafile 0 and one node of
every type, so JobDatafile needs no module offsets of the game.

The creators keep a job file as a tree of nodes in datafile 0 (DATAFILE_GET_FILE_DICT(0)).
Xenvious reads that tree for the export and lays its own tree under it for the import; both need
the node vtables. Instead of fixed addresses from the exe, the script asks the game: on request
it creates the dict "xv" in the file with a bool, int, float, string, vector and array in it,
the game's own nodes, and Xenvious reads their vtables. Without a file it creates datafile 0
first, as the creator does before it saves; while that fails (a delete still pending) it tries
again every frame.

Data page (string page 63, see drawer.py), behind the zone type names:
  0x3FC8  state     Xenvious writes 1 (request); the probe writes 2 (done)
  0x3FD0  root      DATAFILE_GET_FILE_DICT(0)
  0x3FD8  probe     the dict "xv"
  0x3FE0  array     the array "a" in it
  0x3FE8  keys      "xv", "b", "i", "f", "s", "v", "a", NUL terminated; Xenvious writes them
Same layout as JobDatafile.Probe in Xenvious.
"""

DATA = 0xFC000
STATE, ROOT, PROBE, ARRAY = DATA + 0x3FC8, DATA + 0x3FD0, DATA + 0x3FD8, DATA + 0x3FE0
KEYS = {"xv": 0x3FE8, "b": 0x3FEB, "i": 0x3FED, "f": 0x3FEF, "s": 0x3FF1, "v": 0x3FF3, "a": 0x3FF5}
# The creators that load and save job files (JobDatafile).
SCRIPTS = {"fm_lts_creator", "fm_capture_creator", "fm_race_creator", "fm_survival_creator",
           "fm_deathmatch_creator"}


def gen() -> str:
    o = []
    a = o.append

    def key(k):
        a(f"  PUSH_CONST_U24 0x{DATA + KEYS[k]:X}")
        a("  STRING")

    def store(at):
        a(f"  PUSH_CONST_U24 0x{at:X}")
        a("  STRING")
        a("  STORE")

    # Called every frame from dl_main; does nothing until Xenvious asks.
    a("df_main:")
    a("  ENTER 0, 4")
    a(f"  PUSH_CONST_U24 0x{STATE:X}")
    a("  STRING")
    a("  LOAD")
    a("  PUSH_CONST_1")
    a("  IEQ_JZ df_end")
    a("  PUSH_CONST_0")
    a("  NATIVE 1, 1, @DATAFILE::DATAFILE_GET_FILE_DICT")
    a("  LOCAL_U8_STORE 2")
    a("  LOCAL_U8_LOAD 2")
    a("  PUSH_CONST_0")
    a("  IEQ_JZ df_have")
    a("  PUSH_CONST_0")
    a("  NATIVE 1, 0, @DATAFILE::DATAFILE_CREATE")
    a("  PUSH_CONST_0")
    a("  NATIVE 1, 1, @DATAFILE::DATAFILE_GET_FILE_DICT")
    a("  LOCAL_U8_STORE 2")
    a("df_have:")
    a("  LOCAL_U8_LOAD 2")
    a("  PUSH_CONST_0")
    a("  IEQ_JZ df_go")
    # Still no file: a DATAFILE_DELETE of this frame is not done yet (the creator, too, only
    # creates the file a frame after it deleted it). The request stays; next frame again.
    a("  LEAVE 0, 0")
    a("df_go:")
    a("  LOCAL_U8_LOAD 2")
    store(ROOT)
    a("  LOCAL_U8_LOAD 2")
    key("xv")
    a("  NATIVE 2, 1, @DATAFILE::DATADICT_CREATE_DICT")
    a("  LOCAL_U8_STORE 3")
    a("  LOCAL_U8_LOAD 3")
    store(PROBE)
    for k, value, native in (("b", "PUSH_CONST_1", "DATADICT_SET_BOOL"),
                             ("i", "PUSH_CONST_1", "DATADICT_SET_INT"),
                             ("f", "PUSH_CONST_F1", "DATADICT_SET_FLOAT")):
        a("  LOCAL_U8_LOAD 3")
        key(k)
        a(f"  {value}")
        a(f"  NATIVE 3, 0, @DATAFILE::{native}")
    a("  LOCAL_U8_LOAD 3")
    key("s")
    key("xv")
    a("  NATIVE 3, 0, @DATAFILE::DATADICT_SET_STRING")
    a("  LOCAL_U8_LOAD 3")
    key("v")
    a("  PUSH_CONST_F0")
    a("  PUSH_CONST_F0")
    a("  PUSH_CONST_F0")
    a("  NATIVE 5, 0, @DATAFILE::DATADICT_SET_VECTOR")
    a("  LOCAL_U8_LOAD 3")
    key("a")
    a("  NATIVE 2, 1, @DATAFILE::DATADICT_CREATE_ARRAY")
    store(ARRAY)
    a("  PUSH_CONST_2")
    store(STATE)
    a("df_end:")
    a("  LEAVE 0, 0")
    return "\n".join(o) + "\n"
