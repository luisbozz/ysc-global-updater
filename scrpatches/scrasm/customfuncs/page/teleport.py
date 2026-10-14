"""Generates the teleport (.ysa): moves the player, or the vehicle the player sits in, with the
game's own native, like YimMenuV2's Ped::TeleportTo.

Xenvious used to write the position into the ped's (and vehicle's) matrices. That works while the
creator camera holds the ped frozen, but in a test the physics put it back on the next frame.
SET_ENTITY_COORDS_NO_OFFSET moves the physics too. dl_main calls tp_main every frame, and so
does fn13 in the race test, in case dl_main does not run there; the state makes it move once.

In the LTS, Capture, Survival and DM test a controller script runs the job, and the creator only
waits for it to end; dl_main does not run then. The function that waits is called from main every
frame, also while editing (LTS 1.73: "if (fLocal_9185.f_565 != 7) func_1();", DM: case 4 of the
main switch, "if (Global_262145.f_7452) func_1682(); else func_1389();"). The hook "teleport in
test" points that call at tw_wrap, which runs tp_main and then the original call.

Data page (string page 63, see drawer.py), between the zone type names and the datafile probe:
  0x3F88  state     Xenvious writes 1 (request); tp_main writes 2 (done)
  0x3F90  x, 0x3F98 y, 0x3FA0 z   floats, one 8 byte slot each
Same layout as GTA.Teleport in Xenvious.
"""

import re

DATA = 0xFC000
STATE, X, Y, Z = DATA + 0x3F88, DATA + 0x3F90, DATA + 0x3F98, DATA + 0x3FA0

# LTS, Capture, Survival: STATIC_U16, IOFFSET_S16_LOAD f_565, PUSH_CONST_7, INE_JZ +4, CALL wait.
TEST_CALL = "4F ? ? 47 ? ? 78 58 04 00 5D ? ? ?"
TEST_CALL_AT = 10
# DM: GLOBAL_U24, IOFFSET_S16_LOAD, JZ +7, CALL a, J +4, CALL b. The whole block moves into tw_wrap.
TEST_BRANCH = "61 ? ? ? 47 ? ? 56 07 00 5D ? ? ? 55 04 00 5D ? ? ?"
# The creators whose test runs in a controller script.
TEST_SCRIPTS = {"fm_lts_creator", "fm_capture_creator", "fm_survival_creator", "fm_deathmatch_creator"}


def _find(code: bytes, pattern: str) -> list[int]:
    rx = re.compile(b"".join(b"." if t == "?" else re.escape(bytes([int(t, 16)])) for t in pattern.split()), re.S)
    return [m.start() for m in rx.finditer(code)]


def test_site(code: bytes) -> dict | None:
    """The call main makes every frame to the function that waits for the test; None if absent."""
    def u24(at):
        return int.from_bytes(code[at:at + 3], "little")
    one, branch = _find(code, TEST_CALL), _find(code, TEST_BRANCH)
    if len(one) + len(branch) > 1:
        raise ValueError(f"test call found {len(one) + len(branch)} times")
    if one:
        return {"call": u24(one[0] + TEST_CALL_AT + 1)}
    if branch:
        at = branch[0]
        return {"glob": u24(at + 1), "field": int.from_bytes(code[at + 5:at + 7], "little"),
                "a": u24(at + 11), "b": u24(at + 18)}
    return None


def gen_test(site: dict) -> str:
    """tw_wrap: tp_main, then what the hooked site did."""
    o = ["tw_wrap:", "  ENTER 0, 2", "  CALL tp_main"]
    if "call" in site:
        o.append(f"  CALL 0x{site['call']:06X}")
    else:
        o += [f"  GLOBAL_U24 0x{site['glob']:X}",
              f"  IOFFSET_S16_LOAD {site['field']}",
              "  JZ tw_b",
              f"  CALL 0x{site['a']:06X}",
              "  J tw_end",
              "tw_b:",
              f"  CALL 0x{site['b']:06X}",
              "tw_end:"]
    o.append("  LEAVE 0, 0")
    return "\n".join(o) + "\n"


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
