"""Generates the teleport (.ysa): moves the player, or the vehicle the player sits in, with the
game's own native, like YimMenuV2's Ped::TeleportTo.

Xenvious used to write the position into the ped's (and vehicle's) matrices. That works while the
creator camera holds the ped frozen, but in a test the physics put it back on the next frame.
SET_ENTITY_COORDS_NO_OFFSET moves the physics too. dl_main calls tp_main every frame, and so
does fn13 in the race test, in case dl_main does not run there; the state makes it move once.

Data page (string page 63, see drawer.py), between the zone type names and the datafile probe:
  0x3F88  state     Xenvious writes 1 (request); tp_main writes 2 (done)
  0x3F90  x, 0x3F98 y, 0x3FA0 z   floats, one 8 byte slot each
Same layout as GTA.Teleport in Xenvious.
"""

DATA = 0xFC000
STATE, X, Y, Z = DATA + 0x3F88, DATA + 0x3F90, DATA + 0x3F98, DATA + 0x3FA0


def gen() -> str:
    o = []
    a = o.append

    def load(at):
        a(f"  PUSH_CONST_U24 0x{at:X}")
        a("  STRING")
        a("  LOAD")

    a("tp_main:")
    a("  ENTER 0, 4")
    load(STATE)
    a("  PUSH_CONST_1")
    a("  IEQ_JZ tp_end")
    a("  NATIVE 0, 1, @PLAYER::PLAYER_PED_ID")
    a("  LOCAL_U8_STORE 2")
    a("  LOCAL_U8_LOAD 2")
    a("  LOCAL_U8_STORE 3")
    a("  LOCAL_U8_LOAD 2")
    a("  PUSH_CONST_0")
    a("  NATIVE 2, 1, @PED::IS_PED_IN_ANY_VEHICLE")
    a("  JZ tp_set")
    a("  LOCAL_U8_LOAD 2")
    a("  NATIVE 1, 1, @PED::GET_VEHICLE_PED_IS_USING")
    a("  LOCAL_U8_STORE 3")
    a("tp_set:")
    a("  LOCAL_U8_LOAD 3")
    load(X)
    load(Y)
    load(Z)
    a("  PUSH_CONST_1")
    a("  PUSH_CONST_1")
    a("  PUSH_CONST_1")
    a("  NATIVE 7, 0, @ENTITY::SET_ENTITY_COORDS_NO_OFFSET")
    a("  PUSH_CONST_2")
    a(f"  PUSH_CONST_U24 0x{STATE:X}")
    a("  STRING")
    a("  STORE")
    a("tp_end:")
    a("  LEAVE 0, 0")
    return "\n".join(o) + "\n"
