"""Generates the entity mover (.ysa): puts one placed entity of the creator at a new position and
rotation in place, so a changed location, rotation or heading shows at once, without a rebuild.

A rebuild (creator state 7) deletes every placed entity and creates them again from the job data,
which streams every model and takes seconds. Here Xenvious writes the job data as before, reads the
entity's handle from the creator's own handle array ([ENTITYHANDLES] in offsets.ini) and asks the
payload to apply the new pose to that handle, with the natives the creator uses when it places the
entity (SET_ENTITY_COORDS or _NO_OFFSET, then SET_ENTITY_ROTATION order 2 or SET_ENTITY_HEADING).

The team start points are decals, not entities. For them Xenvious passes the decal and the payload
removes it; the creator adds a missing start point decal again on the next frame, at the position
and heading in the job data.

Mode 4 syncs a block of placed props at once (the Advanced Placement preview in GTA): for each
prop of the block it creates the entity when the creator has none in that handle slot (or one of
another model), stores the handle in the creator's array and applies position and rotation; after
the block it deletes the entities of props the preview no longer has. Xenvious passes absolute
pointers (handle slot and job data entry of the block's first prop), so the payload needs no
address of the creator's arrays. A model that is not loaded yet keeps the request open; the next
frame tries again.

dl_main calls mv_main every frame; the state makes it run once per request.

Data page (string page 63, see drawer.py), in the space of the 48th drawer entry:
  0x3700  state     Xenvious writes 1 (request); mv_main writes 2 (done) or 3 (no such entity)
  0x3708  mode      1 SET_ENTITY_COORDS, 2 SET_ENTITY_COORDS_NO_OFFSET, 3 remove the decal
  0x3710  handle    entity or decal
  0x3718  x, 0x3720 y, 0x3728 z
  0x3730  rot x, 0x3738 rot y, 0x3740 rot z
  0x3748  rotate    1: SET_ENTITY_ROTATION with rot; 0: SET_ENTITY_HEADING with heading
  0x3750  heading
Mode 4 (block of props) uses the slots after the mode differently:
  0x3710  pointer to the handle slot of the block's first prop
  0x3718  pointer to the job data entry of the block's first prop (its position)
  0x3720  slots per job data entry
  0x3728  props in the block
  0x3730  props after the block whose entities are deleted
  0x3738  rotation field (slots from the entry start)
  0x3740  model field
Same layout as EntityMover in Xenvious.
"""

DATA = 0xFC000
BASE = DATA + 0x3700
STATE, MODE, HANDLE = BASE, BASE + 0x08, BASE + 0x10
POS = (BASE + 0x18, BASE + 0x20, BASE + 0x28)
ROT = (BASE + 0x30, BASE + 0x38, BASE + 0x40)
ROTATE, HEADING = BASE + 0x48, BASE + 0x50
B_HANDLES, B_DATA, B_STRIDE, B_COUNT = BASE + 0x10, BASE + 0x18, BASE + 0x20, BASE + 0x28
B_DELETE, B_ROT, B_MODEL = BASE + 0x30, BASE + 0x38, BASE + 0x40


def gen() -> str:
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

    def done(value):
        a(f"  PUSH_CONST_{value}")
        store(STATE)
        a("  LEAVE 0, 0")

    a("mv_main:")
    a("  ENTER 0, 2")
    load(STATE)
    a("  PUSH_CONST_1")
    a("  IEQ_JZ mv_end")
    load(MODE)
    a("  PUSH_CONST_4")
    a("  IEQ_JZ mv_single")
    a("  CALL mv_block")
    a("  LEAVE 0, 0")
    a("mv_single:")
    load(MODE)
    a("  PUSH_CONST_3")
    a("  IEQ_JZ mv_entity")
    # Team start point: drop the decal, the creator adds it again.
    load(HANDLE)
    a("  NATIVE 1, 1, @GRAPHICS::IS_DECAL_ALIVE")
    a("  JZ mv_decal_gone")
    load(HANDLE)
    a("  NATIVE 1, 0, @GRAPHICS::REMOVE_DECAL")
    a("mv_decal_gone:")
    done(2)
    a("mv_entity:")
    load(HANDLE)
    a("  NATIVE 1, 1, @ENTITY::DOES_ENTITY_EXIST")
    a("  JZ mv_miss")
    load(MODE)
    a("  PUSH_CONST_2")
    a("  IEQ_JZ mv_offset")
    load(HANDLE)
    for at in POS:
        load(at)
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_1")
    a("  NATIVE 7, 0, @ENTITY::SET_ENTITY_COORDS_NO_OFFSET")
    a("  J mv_turn")
    a("mv_offset:")
    load(HANDLE)
    for at in POS:
        load(at)
    a("  PUSH_CONST_1")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_1")
    a("  NATIVE 8, 0, @ENTITY::SET_ENTITY_COORDS")
    a("mv_turn:")
    load(ROTATE)
    a("  JZ mv_heading")
    load(HANDLE)
    for at in ROT:
        load(at)
    a("  PUSH_CONST_2")
    a("  PUSH_CONST_1")
    a("  NATIVE 6, 0, @ENTITY::SET_ENTITY_ROTATION")
    done(2)
    a("mv_heading:")
    load(HANDLE)
    load(HEADING)
    a("  NATIVE 2, 0, @ENTITY::SET_ENTITY_HEADING")
    done(2)
    a("mv_miss:")
    done(3)
    a("mv_end:")
    a("  LEAVE 0, 0")
    block(a, load, store)
    return "\n".join(o) + "\n"


def block(a, load, store):
    """mv_block: mode 4. Frame locals after return address and frame pointer: 2 i, 3 handle slot
    pointer, 4 entry pointer, 5 handle, 6 model, 7 a model was not loaded yet."""
    I, HP, EP, H, M, WAIT = range(2, 8)

    def lget(k):
        a(f"  LOCAL_U8_LOAD {k}")

    def lset(k):
        a(f"  LOCAL_U8_STORE {k}")

    a("mv_block:")
    a("  ENTER 0, 8")
    a("  PUSH_CONST_0")
    lset(I)
    a("  PUSH_CONST_0")
    lset(WAIT)
    a("mb_loop:")
    lget(I)
    load(B_COUNT)
    a("  ILT_JZ mb_delete")
    load(B_HANDLES)
    lget(I)
    a("  IOFFSET")
    lset(HP)
    load(B_DATA)
    lget(I)
    load(B_STRIDE)
    a("  IMUL")
    a("  IOFFSET")
    lset(EP)
    lget(EP)
    load(B_MODEL)
    a("  IOFFSET")
    a("  LOAD")
    lset(M)
    # An entity of another model in the slot goes first.
    lget(HP)
    a("  LOAD")
    lset(H)
    lget(H)
    a("  NATIVE 1, 1, @ENTITY::DOES_ENTITY_EXIST")
    a("  JZ mb_create")
    lget(H)
    a("  NATIVE 1, 1, @ENTITY::GET_ENTITY_MODEL")
    lget(M)
    a("  IEQ_JZ mb_replace")
    a("  J mb_pose")
    a("mb_replace:")
    lget(HP)
    a("  NATIVE 1, 0, @OBJECT::DELETE_OBJECT")
    a("mb_create:")
    lget(M)
    a("  NATIVE 1, 0, @STREAMING::REQUEST_MODEL")
    lget(M)
    a("  NATIVE 1, 1, @STREAMING::HAS_MODEL_LOADED")
    a("  JZ mb_wait")
    lget(M)
    for k in range(3):
        lget(EP)
        a(f"  IOFFSET_U8_LOAD {k}")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_1")
    a("  NATIVE 7, 1, @OBJECT::CREATE_OBJECT")
    lset(H)
    lget(H)
    lget(HP)
    a("  STORE")
    lget(H)
    a("  PUSH_CONST_1")
    a("  NATIVE 2, 0, @ENTITY::FREEZE_ENTITY_POSITION")
    a("mb_pose:")
    lget(H)
    for k in range(3):
        lget(EP)
        a(f"  IOFFSET_U8_LOAD {k}")
    a("  PUSH_CONST_1")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_1")
    a("  NATIVE 8, 0, @ENTITY::SET_ENTITY_COORDS")
    lget(H)
    for k in range(3):
        lget(EP)
        load(B_ROT)
        a("  IOFFSET")
        a(f"  IOFFSET_U8_LOAD {k}")
    a("  PUSH_CONST_2")
    a("  PUSH_CONST_1")
    a("  NATIVE 6, 0, @ENTITY::SET_ENTITY_ROTATION")
    a("  J mb_next")
    a("mb_wait:")
    a("  PUSH_CONST_1")
    lset(WAIT)
    a("mb_next:")
    lget(I)
    a("  PUSH_CONST_1")
    a("  IADD")
    lset(I)
    a("  J mb_loop")
    # The props after the block: their entities go.
    a("mb_delete:")
    a("  PUSH_CONST_0")
    lset(I)
    a("mb_dloop:")
    lget(I)
    load(B_DELETE)
    a("  ILT_JZ mb_done")
    load(B_HANDLES)
    load(B_COUNT)
    lget(I)
    a("  IADD")
    a("  IOFFSET")
    lset(HP)
    lget(HP)
    a("  LOAD")
    a("  NATIVE 1, 1, @ENTITY::DOES_ENTITY_EXIST")
    a("  JZ mb_dnext")
    lget(HP)
    a("  NATIVE 1, 0, @OBJECT::DELETE_OBJECT")
    a("mb_dnext:")
    lget(I)
    a("  PUSH_CONST_1")
    a("  IADD")
    lset(I)
    a("  J mb_dloop")
    a("mb_done:")
    lget(WAIT)
    a("  JZ mb_ok")
    a("  LEAVE 0, 0")
    a("mb_ok:")
    a("  PUSH_CONST_2")
    store(STATE)
    a("  LEAVE 0, 0")
