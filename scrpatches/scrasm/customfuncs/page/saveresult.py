"""Generates the cloud result catch (.ysa): sv_result, sv_publish and sv_update hand the cloud's
answer to a save or publish to Xenvious.

When the cloud refuses a new job (UGC_CREATE_MISSION), a publish (UGC_PUBLISH) or the update of a
saved job (UGC_UPDATE_MISSION, then UGC_PUBLISH), the creator reads UGC_GET_CREATE_RESULT or
UGC_GET_MODIFY_RESULT into a local and drops it: every refusal shows the same "The Rockstar cloud
servers are unavailable" message (FMMC_ENDPUBF4), whatever the cloud said; only the result of an
update (UGC_GET_MODIFY_RESULT 15 profanity, 18 locked) gets its own text. The patches "save
result", "publish result" and "update result" each replace that one NATIVE with a CALL here, which
makes the same call, keeps the result in the data page for Xenvious and returns it, so the creator
goes on exactly as before.

Data page (string page 63, see drawer.py), after the rule notes:
  0x37A0  create result   the last UGC_GET_CREATE_RESULT of a failed create
  0x37A8  create count    +1 per failed create, so Xenvious sees a new one
  0x37B0  publish result  the last UGC_GET_MODIFY_RESULT of a failed publish
  0x37B8  publish count
  0x37C0  update result   the last UGC_GET_MODIFY_RESULT of a failed update (or its publish)
  0x37C8  update count
"""

DATA = 0xFC000
BASE = DATA + 0x37A0
CREATE_NATIVE = "NETWORK::UGC_GET_CREATE_RESULT"
MODIFY_NATIVE = "NETWORK::UGC_GET_MODIFY_RESULT"
NATIVE = CREATE_NATIVE
SCRIPTS = ("fm_lts_creator", "fm_capture_creator", "fm_deathmatch_creator", "fm_survival_creator", "fm_race_creator")
# label, native, slot (result at BASE + slot, count 8 after it), patch: pattern, NATIVE at +6.
# Create: after the creator's "did the create succeed" branch, before its local store.
# Publish and update: the else branch of "did the modify succeed", told apart by the frame size
# of their function (LEAVE 4 publish, LEAVE 6 update); each matches once in all five creators,
# Legacy 1.73 and Enhanced 1.73.
SITES = (
    ("sv_result", CREATE_NATIVE, 0x00, "save result",
     "2E 83 01 55 11 00 2C 01 ? ? 39 ? 38 00 76 5D ? ? ? 2C 00"),
    ("sv_publish", MODIFY_NATIVE, 0x10, "publish result",
     "2E 04 01 55 12 00 2C 01 ? ? 39 06 38 00 25 09 5D"),
    ("sv_update", MODIFY_NATIVE, 0x20, "update result",
     "2E 06 01 55 12 00 2C 01 ? ? 39 08 38 00 25 09 5D"),
)
OFFSET = 6


def gen(natives: dict) -> str:
    """The functions whose native is in the script's native table."""
    o = []
    for label, native, slot, _, _ in SITES:
        if native not in natives:
            continue
        result, count = BASE + slot, BASE + slot + 8
        o += [
            f"{label}:",
            "  ENTER 0, 2",
            f"  NATIVE 0, 1, @{native}",
            "  DUP",
            f"  PUSH_CONST_U24 0x{result:X}",
            "  STRING",
            "  STORE",
            f"  PUSH_CONST_U24 0x{count:X}",
            "  STRING",
            "  LOAD",
            "  IADD_U8 1",
            f"  PUSH_CONST_U24 0x{count:X}",
            "  STRING",
            "  STORE",
            "  LEAVE 0, 1",
        ]
    return "\n".join(o) + "\n"
