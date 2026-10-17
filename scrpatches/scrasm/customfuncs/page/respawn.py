"""Generates the respawn in the creator test (.ysa): rs_main brings the player back after a death.

Online, freemode respawns a dead player; it runs during every job, but not in the creator. In
the creator test the player is invincible, so nothing there ever respawns anyone. With
Xenvious' "invincible in the test" off (onlinetest_mortal) a player who dies stays dead. tw_wrap
(teleport.py), which runs every frame of the LTS, Capture, Survival and DM test, calls rs_main:
dead for 2.5 seconds the screen fades out, at 3 seconds the player is resurrected where they
died, with a second of invincibility as the controller gives after its own resurrects, and the
screen fades in. The controller counts the death itself.
Only while the creator's controller runs: when the team is out of lives the controller ends the
test, and the game's own respawn takes over in the creator (a resurrect of ours on top of it
stood the player up just before the game sent them to the hospital).

Data page (string page 63, see drawer.py), after the teleport:
  0x3FA8  dead since   game timer when the death was seen, 0 while alive
  0x3FB0  respawns     +1 per respawn, for Xenvious and the log
"""

DATA = 0xFC000
DEAD, COUNT = DATA + 0x3FA8, DATA + 0x3FB0
WAIT_MS = 3000
FADE_AT_MS, FADE_MS = 2500, 500
NATIVE = "NETWORK::NETWORK_RESURRECT_LOCAL_PLAYER"
# The controller each creator starts for its test.
CONTROLLERS = {
    "fm_lts_creator": "fm_mission_controller", "fm_capture_creator": "fm_mission_controller",
    "fm_survival_creator": "fm_survival_controller", "fm_deathmatch_creator": "fm_deathmatch_controler",
}


def joaat(text: str) -> int:
    h = 0
    for ch in text.lower().encode():
        h = (h + ch) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    return (h + (h << 15)) & 0xFFFFFFFF


def gen(script: str) -> str:
    o = []
    a = o.append

    def load(at):
        a(f"  PUSH_CONST_U24 0x{at:X}")
        a("  STRING")
        a("  LOAD")

    def store(at):
        a(f"  PUSH_CONST_U24 0x{at:X}")
        a("  STRING")
        a("  STORE")

    a("rs_main:")
    a("  ENTER 0, 3")
    a(f"  PUSH_CONST_U32 0x{joaat(CONTROLLERS[script]):08X}")
    a("  NATIVE 1, 1, @SCRIPT::GET_NUMBER_OF_THREADS_RUNNING_THE_SCRIPT_WITH_THIS_HASH")
    a("  JZ rs_alive")
    a("  NATIVE 0, 1, @PLAYER::PLAYER_PED_ID")
    a("  NATIVE 1, 1, @PED::IS_PED_INJURED")
    a("  JZ rs_alive")
    load(DEAD)
    a("  JZ rs_first")
    a("  NATIVE 0, 1, @MISC::GET_GAME_TIMER")
    load(DEAD)
    a("  ISUB")
    a("  LOCAL_U8_STORE 2")
    a("  LOCAL_U8_LOAD 2")
    a(f"  PUSH_CONST_S16 {FADE_AT_MS}")
    a("  ILT_JZ rs_late")
    a("  LEAVE 0, 0")
    a("rs_late:")
    a("  LOCAL_U8_LOAD 2")
    a(f"  PUSH_CONST_S16 {WAIT_MS}")
    a("  ILT_JZ rs_go")
    # Between the two: fade out once.
    a("  NATIVE 0, 1, @CAM::IS_SCREEN_FADED_OUT")
    a("  NATIVE 0, 1, @CAM::IS_SCREEN_FADING_OUT")
    a("  IOR")
    a("  JZ rs_fade")
    a("  LEAVE 0, 0")
    a("rs_fade:")
    a(f"  PUSH_CONST_S16 {FADE_MS}")
    a("  NATIVE 1, 0, @CAM::DO_SCREEN_FADE_OUT")
    a("  LEAVE 0, 0")
    a("rs_first:")
    # A timer of 0 would read as alive; 1 ms later changes nothing.
    a("  NATIVE 0, 1, @MISC::GET_GAME_TIMER")
    a("  PUSH_CONST_1")
    a("  IOR")
    store(DEAD)
    a("  LEAVE 0, 0")
    a("rs_go:")
    a("  NATIVE 0, 1, @PLAYER::PLAYER_PED_ID")
    a("  PUSH_CONST_0")
    a("  NATIVE 2, 3, @ENTITY::GET_ENTITY_COORDS")
    a("  NATIVE 0, 1, @PLAYER::PLAYER_PED_ID")
    a("  NATIVE 1, 1, @ENTITY::GET_ENTITY_HEADING")
    a("  PUSH_CONST_S16 1000")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_M1")
    a("  PUSH_CONST_M1")
    a(f"  NATIVE 9, 0, @{NATIVE}")
    a(f"  PUSH_CONST_S16 {FADE_MS}")
    a("  NATIVE 1, 0, @CAM::DO_SCREEN_FADE_IN")
    load(COUNT)
    a("  IADD_U8 1")
    store(COUNT)
    a("rs_alive:")
    a("  PUSH_CONST_0")
    store(DEAD)
    a("  LEAVE 0, 0")
    return "\n".join(o) + "\n"
