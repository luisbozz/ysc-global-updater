"""Generates the shape drawer (.ysa) that runs in a creator's extra code page.

The data lives in Xenvious' data page, which the script reaches as string page 63 of its own
string page array (ScriptSpace): PUSH_CONST_U24 0xFC000 + offset; STRING gives a pointer into
it, LOAD reads one 8 byte slot (ints and floats in the low 4 bytes).

Data page layout (byte offsets):
  0x000  "STRING\\0"      text label for BEGIN_TEXT_COMMAND_DISPLAY_TEXT
  0x008  count            entries to draw (capped at 47)
  0x010  heartbeat        +1 every frame the drawer runs
  0x018  variables        what the customfuncs share with Xenvious, one 8 byte slot each, in
                          the order of the custom globals the Legacy payloads use
                          (Global_2884084 + n is 0x018 + n * 8):
           0x018  check            dispatch bits (0..4 all creators, 5..7 race)
           0x020  hovered model
           0x028  dimension model, 0x030 min (vector), 0x048 max (vector)
           0x060  tune: slot, value, count, current, model, vehicle
           0x090  garage slot, 0x098 garage result
           0x0B0  garage list (100 models)
           0x3D8  garage vehicle (the creator's personal vehicle struct, up to 0x7FF)
  0x800  entries          0x100 bytes each, slot k at +k*8 (47 of them)
  0x3700 entity mover      state, kind, model, old/new position, rotation (mover.py)
  0x3800 zone type names  count, then 30 entries of 0x40: +0 type, +8 name (zonelabel.py)
  0x3F88 teleport         state, x, y, z (teleport.py)
  0x3FC8 datafile probe   state, root, probe, array, keys (datafile.py)

Entry slots:
  0 kind     0 off, 1 prism, 2 marker, 3 text only
  1 flags    1 fill sides, 2 outline, 4 fill top and bottom, 8 text
  2..9       prism: corners x0,y0 .. x3,y3 | marker: 2 type, 3..5 pos, 6..8 scale
  10, 11     prism: z bottom, z top
  12..16     r, g, b, fill alpha, line alpha
  17..19     text position
  20         text scale
  21..24     text r, g, b, a
  25..31     text, 56 bytes, NUL terminated
"""

DATA = 0xFC000
COUNT = DATA + 0x08
BEAT = DATA + 0x10
ENTRIES = DATA + 0x800
STRIDE = 0x100
MAX = 47           # the 48th entry's space holds the entity mover (mover.py)
COPY = 25          # slots 0..24 go into the frame; STORE_N takes at most 25
TEXT = 25 * 8


def L(k):          # frame local holding entry slot k (dl_entry: arg 0, then ret + fp)
    return 3 + k


SX, SY = 28, 29    # screen coordinates


def gen(fn0: int | None, keep: tuple[int, int, int] | None = None, calls: tuple[str, ...] = ()) -> str:
    """keep: (draw function, global, field) when the hook keeps the call it replaces (Legacy):
    dl_hook then takes that call's four arguments and makes it, as the script did, while
    global.field is not 6. calls: functions dl_main calls first, every frame (datafile.py)."""
    o = []
    a = o.append

    # ---- entry point: the old dispatcher, then the drawer ----
    if fn0 is not None:
        a("dl_hook:")
        if keep is None:
            a("  ENTER 0, 2")
        else:
            draw, glob, field = keep
            a("  ENTER 4, 6")
            a(f"  GLOBAL_U24 {glob}")
            a(f"  IOFFSET_U8_LOAD {field}")
            a("  PUSH_CONST_6")
            a("  INE_JZ dl_h_own")
            for i in range(4):
                a(f"  LOCAL_U8_LOAD {i}")
            a(f"  CALL 0x{draw:06X}")
            a("dl_h_own:")
        a(f"  CALL 0x{fn0:06X}")
        a("  CALL dl_main")
        a(f"  LEAVE {0 if keep is None else 4}, 0")

    # ---- dl_main: heartbeat, then every entry ----
    a("dl_main:")
    a("  ENTER 0, 4")
    for c in calls:
        a(f"  CALL {c}")
    a(f"  PUSH_CONST_U24 0x{BEAT:X}")
    a("  STRING")
    a("  LOAD")
    a("  IADD_U8 1")
    a(f"  PUSH_CONST_U24 0x{BEAT:X}")
    a("  STRING")
    a("  STORE")
    a(f"  PUSH_CONST_U24 0x{COUNT:X}")
    a("  STRING")
    a("  LOAD")
    a("  LOCAL_U8_STORE 3")
    a("  LOCAL_U8_LOAD 3")
    a(f"  PUSH_CONST_U8 {MAX}")
    a("  IGT_JZ dl_m_n")
    a(f"  PUSH_CONST_U8 {MAX}")
    a("  LOCAL_U8_STORE 3")
    a("dl_m_n:")
    a("  PUSH_CONST_0")
    a("  LOCAL_U8_STORE 2")
    a("dl_m_loop:")
    a("  LOCAL_U8_LOAD 2")
    a("  LOCAL_U8_LOAD 3")
    a("  ILT_JZ dl_m_end")
    a("  LOCAL_U8_LOAD 2")
    a(f"  PUSH_CONST_S16 {STRIDE}")
    a("  IMUL")
    a(f"  PUSH_CONST_U24 0x{ENTRIES:X}")
    a("  IADD")
    a("  CALL dl_entry")
    a("  LOCAL_U8_LOAD 2")
    a("  IADD_U8 1")
    a("  LOCAL_U8_STORE 2")
    a("  J dl_m_loop")
    a("dl_m_end:")
    a("  LEAVE 0, 0")

    # ---- dl_quad: two triangles, each in both windings (DRAW_POLY is one sided) ----
    a("dl_quad:")
    a("  ENTER 16, 18")
    A, B, C, D = (0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)
    for tri in ((A, B, C), (A, C, D), (C, B, A), (D, C, A)):
        for v in tri:
            for i in v:
                a(f"  LOCAL_U8_LOAD {i}")
        for i in (12, 13, 14, 15):
            a(f"  LOCAL_U8_LOAD {i}")
        a("  NATIVE 13, 0, @GRAPHICS::DRAW_POLY")
    a("  LEAVE 16, 0")

    # ---- dl_entry(e): e = string index of the entry ----
    a("dl_entry:")
    a("  ENTER 1, 30")
    a(f"  PUSH_CONST_U8 {COPY}")
    a("  LOCAL_U8_LOAD 0")
    a("  STRING")
    a("  LOAD_N")
    a(f"  PUSH_CONST_U8 {COPY}")
    a(f"  LOCAL_U8 {L(0)}")
    a("  STORE_N")

    def flag(bit, skip):
        a(f"  LOCAL_U8_LOAD {L(1)}")
        a(f"  PUSH_CONST_U8 {bit}")
        a("  IAND")
        a(f"  JZ {skip}")

    def corner(k, z):          # pushes x, y, z of prism corner k at z slot
        k &= 3
        a(f"  LOCAL_U8_LOAD {L(2 + 2 * k)}")
        a(f"  LOCAL_U8_LOAD {L(3 + 2 * k)}")
        a(f"  LOCAL_U8_LOAD {L(z)}")

    def colour(alpha):
        for k in (12, 13, 14, alpha):
            a(f"  LOCAL_U8_LOAD {L(k)}")

    a(f"  LOCAL_U8_LOAD {L(0)}")
    a("  PUSH_CONST_1")
    a("  IEQ_JZ dl_e_marker")
    # prism sides
    flag(1, "dl_e_caps")
    for k in range(4):
        corner(k, 10); corner(k + 1, 10); corner(k + 1, 11); corner(k, 11)
        colour(15)
        a("  CALL dl_quad")
    a("dl_e_caps:")
    flag(4, "dl_e_lines")
    for z in (10, 11):
        for k in range(4):
            corner(k, z)
        colour(15)
        a("  CALL dl_quad")
    a("dl_e_lines:")
    flag(2, "dl_e_text")
    for k in range(4):
        for p, q in (((k, 10), (k + 1, 10)), ((k, 11), (k + 1, 11)), ((k, 10), (k, 11))):
            corner(*p); corner(*q)
            colour(16)
            a("  NATIVE 10, 0, @GRAPHICS::DRAW_LINE")
    a("  J dl_e_text")

    a("dl_e_marker:")
    a(f"  LOCAL_U8_LOAD {L(0)}")
    a("  PUSH_CONST_2")
    a("  IEQ_JZ dl_e_text")
    flag(1, "dl_e_text")
    a(f"  LOCAL_U8_LOAD {L(2)}")                 # type
    for k in (3, 4, 5):
        a(f"  LOCAL_U8_LOAD {L(k)}")             # position
    for _ in range(6):
        a("  PUSH_CONST_F0")                    # direction, rotation
    for k in (6, 7, 8):
        a(f"  LOCAL_U8_LOAD {L(k)}")             # scale
    colour(15)
    a("  PUSH_CONST_0")                         # bob up and down
    a("  PUSH_CONST_0")                         # face camera
    a("  PUSH_CONST_2")
    a("  PUSH_CONST_0")                         # rotate
    a("  PUSH_CONST_0")                         # texture dictionary (NULL)
    a("  PUSH_CONST_0")                         # texture name (NULL)
    a("  PUSH_CONST_0")                         # draw on entities
    a("  NATIVE 24, 0, @GRAPHICS::DRAW_MARKER")

    a("dl_e_text:")
    flag(8, "dl_e_end")
    for k in (17, 18, 19):
        a(f"  LOCAL_U8_LOAD {L(k)}")
    a(f"  LOCAL_U8 {SX}")
    a(f"  LOCAL_U8 {SY}")
    a("  NATIVE 5, 1, @GRAPHICS::GET_SCREEN_COORD_FROM_WORLD_COORD")
    a("  JZ dl_e_end")
    # Graphics order 1 and behind the pause menu, like the mission creator's labels: the creator
    # menu (order 4) covers the text.
    a("  PUSH_CONST_1")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_ORDER")
    a("  PUSH_CONST_1")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_BEHIND_PAUSEMENU")
    a("  PUSH_CONST_0")
    a("  NATIVE 1, 0, @HUD::SET_TEXT_FONT")
    a(f"  LOCAL_U8_LOAD {L(20)}")
    a(f"  LOCAL_U8_LOAD {L(20)}")
    a("  NATIVE 2, 0, @HUD::SET_TEXT_SCALE")
    for k in (21, 22, 23, 24):
        a(f"  LOCAL_U8_LOAD {L(k)}")
    a("  NATIVE 4, 0, @HUD::SET_TEXT_COLOUR")
    a("  PUSH_CONST_1")
    a("  NATIVE 1, 0, @HUD::SET_TEXT_CENTRE")
    a("  NATIVE 0, 0, @HUD::SET_TEXT_OUTLINE")
    a(f"  PUSH_CONST_U24 0x{DATA:X}")
    a("  STRING")
    a("  NATIVE 1, 0, @HUD::BEGIN_TEXT_COMMAND_DISPLAY_TEXT")
    a("  LOCAL_U8_LOAD 0")
    a(f"  PUSH_CONST_U8 {TEXT}")
    a("  IADD")
    a("  STRING")
    a("  NATIVE 1, 0, @HUD::ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME")
    a(f"  LOCAL_U8_LOAD {SX}")
    a(f"  LOCAL_U8_LOAD {SY}")
    a("  PUSH_CONST_0")
    a("  NATIVE 3, 0, @HUD::END_TEXT_COMMAND_DISPLAY_TEXT")
    a("  PUSH_CONST_4")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_ORDER")
    a("  PUSH_CONST_0")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_BEHIND_PAUSEMENU")
    a("dl_e_end:")
    a("  LEAVE 1, 0")
    return "\n".join(o) + "\n"


if __name__ == "__main__":
    print(gen(0x2E0000))
