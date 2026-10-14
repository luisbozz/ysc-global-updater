"""Generates the zone label (.ysa): the zone's number and type over every zone the creator draws.

The creators that place zones (LTS, Capture, DM, Race) draw them in one loop that calls a draw
function per zone with a pointer to the zone (LTS 1.73 func_1965(&Global_4718592.f_223789[i], ...)).
The hook ("zone labels") points that CALL at zl_wrap, which runs the original draw and then
zl_label, so a label shows exactly while the game draws its zone. Rockstar left the label call in
release builds empty.

The text is like the creator's own zone list: "(Zone <n>)  <type>". The list takes the type from
the game label FMMC_ZN_TY<type>, but the game has that label for six types only and shows "NULL"
for the rest. So Xenvious writes the names of the types the job uses into the data page (NAMES,
in the language of Xenvious), and a type without a name there shows its number. Colour from the game's zone colour function, so the
label matches its zone. Like the mission creator's entity labels it is drawn in graphics order 1 and
behind the pause menu, so the creator menu (order 4) covers it, and it fades out with distance.
"""
import re

# &Global_N.f_M[i /*stride*/], six PUSH_CONST_F0, -1.0f, ..., CALL draw. The zone stride (ARRAY_U8
# operand) is read from the site: it grows when Rockstar adds zone fields.
HOOK = "61 ? ? ? 64 ? ? ? 3F 34 ? 7A 7A 7A 7A 7A 7A 28 00 00 80 BF 38 ? 71 38 ? 5D ? ? ?"
HOOK_CALL = 27          # offset of the CALL in HOOK
HOOK_STRIDE = 10        # offset of the ARRAY_U8 operand in HOOK
NEAR, FAR = 60.0, 90.0  # metres: full alpha up to NEAR, gone at FAR
SCALE = 0.3
STRINGS = ("(Zone ", ")  ", "STRING")
# Zone type names in the data page (string page 63, see drawer.py): 0x3800 count, then entries of
# 0x40 bytes, +0 type, +8 name (NUL terminated). Same layout as ScriptDrawer.SetZoneNames.
NAMES = 0xFC000 + 0x3800
NAME_ENTRY = 0x40


def find(code: bytes, pattern: str) -> list[int]:
    rx = re.compile(b"".join(b"." if t == "?" else re.escape(bytes([int(t, 16)])) for t in pattern.split()), re.S)
    return [m.start() for m in rx.finditer(code)]


def site(full) -> dict | None:
    """Where the hook goes and what the label needs from this script; None without zones."""
    from scrasm.disasm import disassemble
    hits = find(full.code, HOOK)
    if not hits:
        return None
    if len(hits) != 1:
        raise ValueError(f"zone loop found {len(hits)} times")
    at = hits[0]
    c = full.code
    glob = int.from_bytes(c[at + 1:at + 4], "little")
    field = int.from_bytes(c[at + 5:at + 8], "little")
    stride = c[at + HOOK_STRIDE]
    draw = int.from_bytes(c[at + HOOK_CALL + 1:at + HOOK_CALL + 4], "little")
    if not (c[draw] == 0x2D and c[draw + 1] == 11):
        raise ValueError("zone draw function does not take 11 arguments")
    # The draw function gets its colour from func(zone type, &r, &g, &b):
    # IOFFSET_U8_LOAD 11, LOCAL_U8 r, LOCAL_U8 g, LOCAL_U8 b, CALL colour.
    end = draw + 0x400
    while True:  # cut on an instruction border
        try:
            ins = disassemble(c[:end], start=draw)
            break
        except Exception:
            end += 1
    colour = None
    for k in range(len(ins) - 4):
        a = ins[k:k + 5]
        if (a[0].name == "IOFFSET_U8_LOAD" and a[0].operands[0] == 11
                and all(x.name == "LOCAL_U8" for x in a[1:4]) and a[4].name == "CALL"):
            colour = int.from_bytes(a[4].operands[:3], "little")
            break
    if colour is None:
        raise ValueError("zone colour function not found")
    strings = {}
    for t in STRINGS:
        o = full.string_offsets(t)
        if not o:
            raise ValueError(f"string {t!r} missing")
        strings[t] = o[0]
    return dict(at=at, glob=glob, field=field, stride=stride, draw=draw, colour=colour, strings=strings)


DEBUG = False
DBG = 0xFC700   # debug slots in the data page (temporary)


def gen(s: dict) -> str:
    o = []
    a = o.append

    def count(slot):
        if DEBUG:
            a(f"  PUSH_CONST_U24 0x{DBG + slot * 8:X}")
            a("  STRING")
            a("  LOAD")
            a("  IADD_U8 1")
            a(f"  PUSH_CONST_U24 0x{DBG + slot * 8:X}")
            a("  STRING")
            a("  STORE")

    def keep(slot, local):
        if DEBUG:
            a(f"  LOCAL_U8_LOAD {local}")
            a(f"  PUSH_CONST_U24 0x{DBG + slot * 8:X}")
            a("  STRING")
            a("  STORE")
    st = s["strings"]
    # zl_wrap: same 11 arguments as the draw function (zone, two vectors, four values).
    a("zl_wrap:")
    a("  ENTER 11, 13")
    for i in range(11):
        a(f"  LOCAL_U8_LOAD {i}")
    a(f"  CALL 0x{s['draw']:06X}")
    a("  LOCAL_U8_LOAD 0")
    a("  CALL zl_label")
    a("  LEAVE 11, 0")

    # zl_label(zone): locals 3 type, 4-6 position, 7/8 screen, 9-11 colour, 12 number,
    # 13 alpha, 14-16 offset to the camera, 17 squared distance (no SQRT in the native tables), 18 name
    # entry, 20-27 text.
    T, P, SX, SY, R, IDX, AL, DV, DIST, K, TXT = 3, 4, 7, 8, 9, 12, 13, 14, 17, 18, 20
    a("zl_label:")
    a("  ENTER 1, 28")
    count(0)
    a("  LOCAL_U8_LOAD 0")
    a("  IOFFSET_U8_LOAD 11")
    a(f"  LOCAL_U8_STORE {T}")
    # The zone starts with its two points as a script array (size slot, then 2 x 3 values), so
    # the points sit at +1 and +4. A sphere (shape 1) is drawn around its first point; boxes and
    # angled areas between both.
    a("  PUSH_CONST_3")
    a("  LOCAL_U8_LOAD 0")
    a("  IOFFSET_U8 1")
    a("  LOAD_N")
    a("  LOCAL_U8_LOAD 0")
    a("  IOFFSET_U8_LOAD 10")
    a("  PUSH_CONST_1")
    a("  IEQ_JZ zl_mid")
    a("  J zl_pos")
    a("zl_mid:")
    a("  PUSH_CONST_3")
    a("  LOCAL_U8_LOAD 0")
    a("  IOFFSET_U8 4")
    a("  LOAD_N")
    a("  VADD")
    a("  PUSH_CONST_F 0.5")
    a("  F2V")
    a("  VMUL")
    a("zl_pos:")
    a("  PUSH_CONST_3")
    a(f"  LOCAL_U8 {P}")
    a("  STORE_N")

    # Distance to the camera; nothing beyond FAR, fading from NEAR (linear in the squared distance).
    for k in range(3):
        a(f"  LOCAL_U8_LOAD {P + k}")
    a("  NATIVE 0, 3, @CAM::GET_FINAL_RENDERED_CAM_COORD")
    a("  VSUB")
    a("  PUSH_CONST_3")
    a(f"  LOCAL_U8 {DV}")
    a("  STORE_N")
    for k in range(3):
        a(f"  LOCAL_U8_LOAD {DV + k}")
        a(f"  LOCAL_U8_LOAD {DV + k}")
        a("  FMUL")
    a("  FADD")
    a("  FADD")
    a(f"  LOCAL_U8_STORE {DIST}")
    keep(4, DIST)
    keep(10, P)
    keep(11, P + 1)
    keep(12, P + 2)
    a(f"  LOCAL_U8_LOAD {DIST}")
    a(f"  PUSH_CONST_F {FAR * FAR}")
    a("  FLT")
    a("  JZ zl_end")
    a("  PUSH_CONST_U8 255")
    a(f"  LOCAL_U8_STORE {AL}")
    a(f"  LOCAL_U8_LOAD {DIST}")
    a(f"  PUSH_CONST_F {NEAR * NEAR}")
    a("  FGT")
    a("  JZ zl_near")
    a(f"  PUSH_CONST_F {FAR * FAR}")
    a(f"  LOCAL_U8_LOAD {DIST}")
    a("  FSUB")
    a(f"  PUSH_CONST_F {255.0 / (FAR * FAR - NEAR * NEAR)}")
    a("  FMUL")
    a("  F2I")
    a(f"  LOCAL_U8_STORE {AL}")
    a("zl_near:")
    count(1)

    for k in range(3):
        a(f"  LOCAL_U8_LOAD {P + k}")
    a(f"  LOCAL_U8 {SX}")
    a(f"  LOCAL_U8 {SY}")
    a("  NATIVE 5, 1, @GRAPHICS::GET_SCREEN_COORD_FROM_WORLD_COORD")
    a("  JZ zl_end")

    a(f"  LOCAL_U8_LOAD {T}")
    for k in range(3):
        a(f"  LOCAL_U8 {R + k}")
    a(f"  CALL 0x{s['colour']:06X}")
    count(2)
    keep(5, SX)
    keep(6, SY)
    keep(7, R)
    keep(8, R + 1)
    keep(9, AL)

    # Number from the zone's place in the array; pointers differ by whole elements.
    a("  LOCAL_U8_LOAD 0")
    a("  PUSH_CONST_0")
    a(f"  GLOBAL_U24 {s['glob']}")
    a(f"  PUSH_CONST_U24 {s['field']}")
    a("  IOFFSET")
    a(f"  ARRAY_U8 {s['stride']}")
    a("  ISUB")
    a(f"  PUSH_CONST_S16 {s['stride'] * 8}")
    a("  IDIV")
    a(f"  LOCAL_U8_STORE {IDX}")

    # "(Zone <n>)  ", then the type's name from NAMES, or its number without one.
    a(f"  PUSH_CONST_U24 {st['(Zone ']}")
    a("  STRING")
    a(f"  LOCAL_U8 {TXT}")
    a("  TEXT_LABEL_ASSIGN_STRING 64")
    a(f"  LOCAL_U8_LOAD {IDX}")
    a(f"  LOCAL_U8 {TXT}")
    a("  TEXT_LABEL_APPEND_INT 64")
    a(f"  PUSH_CONST_U24 {st[')  ']}")
    a("  STRING")
    a(f"  LOCAL_U8 {TXT}")
    a("  TEXT_LABEL_APPEND_STRING 64")
    a("  PUSH_CONST_0")
    a(f"  LOCAL_U8_STORE {K}")
    a("zl_find:")
    a(f"  LOCAL_U8_LOAD {K}")
    a(f"  PUSH_CONST_U24 0x{NAMES:X}")
    a("  STRING")
    a("  LOAD")
    a("  ILT_JZ zl_number")
    a(f"  LOCAL_U8_LOAD {K}")
    a(f"  PUSH_CONST_U8 {NAME_ENTRY}")
    a("  IMUL")
    a(f"  PUSH_CONST_U24 0x{NAMES + 8:X}")
    a("  IADD")
    a("  STRING")
    a("  LOAD")
    a(f"  LOCAL_U8_LOAD {T}")
    a("  IEQ_JZ zl_next")
    a(f"  LOCAL_U8_LOAD {K}")
    a(f"  PUSH_CONST_U8 {NAME_ENTRY}")
    a("  IMUL")
    a(f"  PUSH_CONST_U24 0x{NAMES + 16:X}")
    a("  IADD")
    a("  STRING")
    a(f"  LOCAL_U8 {TXT}")
    a("  TEXT_LABEL_APPEND_STRING 64")
    a("  J zl_text")
    a("zl_next:")
    a(f"  LOCAL_U8_LOAD {K}")
    a("  IADD_U8 1")
    a(f"  LOCAL_U8_STORE {K}")
    a("  J zl_find")
    a("zl_number:")
    a(f"  LOCAL_U8_LOAD {T}")
    a(f"  LOCAL_U8 {TXT}")
    a("  TEXT_LABEL_APPEND_INT 64")
    a("zl_text:")

    a("  PUSH_CONST_1")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_ORDER")
    a("  PUSH_CONST_1")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_BEHIND_PAUSEMENU")
    a("  PUSH_CONST_0")
    a("  NATIVE 1, 0, @HUD::SET_TEXT_FONT")
    a(f"  PUSH_CONST_F {SCALE}")
    a(f"  PUSH_CONST_F {SCALE}")
    a("  NATIVE 2, 0, @HUD::SET_TEXT_SCALE")
    for k in range(3):
        a(f"  LOCAL_U8_LOAD {R + k}")
    a(f"  LOCAL_U8_LOAD {AL}")
    a("  NATIVE 4, 0, @HUD::SET_TEXT_COLOUR")
    a("  PUSH_CONST_1")
    a("  NATIVE 1, 0, @HUD::SET_TEXT_CENTRE")
    a("  NATIVE 0, 0, @HUD::SET_TEXT_OUTLINE")
    a(f"  PUSH_CONST_U24 {st['STRING']}")
    a("  STRING")
    a("  NATIVE 1, 0, @HUD::BEGIN_TEXT_COMMAND_DISPLAY_TEXT")
    a(f"  LOCAL_U8 {TXT}")
    a("  NATIVE 1, 0, @HUD::ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME")
    a(f"  LOCAL_U8_LOAD {SX}")
    a(f"  LOCAL_U8_LOAD {SY}")
    a("  PUSH_CONST_0")
    a("  NATIVE 3, 0, @HUD::END_TEXT_COMMAND_DISPLAY_TEXT")
    count(3)
    if DEBUG:
        a("  PUSH_CONST_U8 8")
        a(f"  LOCAL_U8 {TXT}")
        a("  LOAD_N")
        a("  PUSH_CONST_U8 8")
        a(f"  PUSH_CONST_U24 0x{DBG + 0x80:X}")
        a("  STRING")
        a("  STORE_N")
    a("  PUSH_CONST_4")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_ORDER")
    a("  PUSH_CONST_0")
    a("  NATIVE 1, 0, @GRAPHICS::SET_SCRIPT_GFX_DRAW_BEHIND_PAUSEMENU")
    a("zl_end:")
    a("  LEAVE 1, 0")
    return "\n".join(o) + "\n"
