"""Generates the rule notes in the creator test (.ysa): rt_main draws the lines Xenvious hands over.

In the creator test nothing tells the tester that a team reached its next rule. Xenvious reads
the rule each team is on from the running controller (fm_mission_controller static 28722 + 1 +
team in Legacy 1.73, 29326 in Enhanced) and keeps up to MAX lines about the changes, each for a
few seconds. tw_wrap (teleport.py), which runs every frame of the LTS, Capture, Survival and DM
test, calls rt_main: it draws every line bottom left above the radar, the newest at the bottom,
each on a dark box as wide as its text, like the game's own tickers. Drawn, not posted to the
feed: the creators hide the feed in their test (THEFEED_HIDE_THIS_FRAME). The text keeps the
game's colour codes (~o~ ... ~s~).

Data page (string page 63, see drawer.py), after the entity mover:
  0x3780  count     lines to draw (0..MAX); Xenvious writes 0 first while it rewrites them
Drawer space (string pages 59 to 62, see drawer.py), after the labels:
  0xF800  lines     0x80 bytes each, NUL terminated (127 bytes of text)
"""

DATA = 0xFC000
EXTRA = 0xEC000
COUNT = DATA + 0x3780
LINES = EXTRA + 0xF800
LINE_BYTES = 0x80
MAX = 4
LABEL = DATA  # "STRING" at the start of the data page

# Screen units. The bottom line sits just above the radar; each line is LINE_H high.
X, BOTTOM, LINE_H = 0.016, 0.765, 0.034
PAD_X, BOX_H, TEXT_DY = 0.005, 0.030, 0.003
SCALE = 0.36
FONT = 0

NATIVES = ("GRAPHICS::DRAW_RECT", "HUD::SET_TEXT_FONT", "HUD::SET_TEXT_SCALE",
           "HUD::SET_TEXT_COLOUR", "HUD::SET_TEXT_DROP_SHADOW",
           "HUD::BEGIN_TEXT_COMMAND_DISPLAY_TEXT", "HUD::ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME",
           "HUD::END_TEXT_COMMAND_DISPLAY_TEXT", "HUD::BEGIN_TEXT_COMMAND_GET_SCREEN_WIDTH_OF_DISPLAY_TEXT",
           "HUD::END_TEXT_COMMAND_GET_SCREEN_WIDTH_OF_DISPLAY_TEXT")


def available(natives: dict) -> bool:
    """Every native rt_main calls is in the script's native table."""
    return all(n in natives for n in NATIVES)


def gen() -> str:
    o = []
    a = o.append
    # Frame locals after the return address and frame pointer (0, 1): line, count, text, y, width
    K, N, P, Y, W = 2, 3, 4, 5, 6

    def text_style():
        a(f"  PUSH_CONST_U8 {FONT}")
        a("  NATIVE 1, 0, @HUD::SET_TEXT_FONT")
        a(f"  PUSH_CONST_F {SCALE}")
        a(f"  PUSH_CONST_F {SCALE}")
        a("  NATIVE 2, 0, @HUD::SET_TEXT_SCALE")

    def with_text(begin):
        a(f"  PUSH_CONST_U24 0x{LABEL:X}")
        a("  STRING")
        a(f"  NATIVE 1, 0, @HUD::{begin}")
        a(f"  LOCAL_U8_LOAD {P}")
        a("  NATIVE 1, 0, @HUD::ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME")

    a("rt_main:")
    a("  ENTER 0, 7")
    a(f"  PUSH_CONST_U24 0x{COUNT:X}")
    a("  STRING")
    a("  LOAD")
    a(f"  LOCAL_U8_STORE {N}")
    a(f"  LOCAL_U8_LOAD {N}")
    a(f"  PUSH_CONST_U8 {MAX}")
    a("  IGT_JZ rt_count_ok")
    a(f"  PUSH_CONST_U8 {MAX}")
    a(f"  LOCAL_U8_STORE {N}")
    a("rt_count_ok:")
    a("  PUSH_CONST_0")
    a(f"  LOCAL_U8_STORE {K}")
    a("rt_loop:")
    a(f"  LOCAL_U8_LOAD {K}")
    a(f"  LOCAL_U8_LOAD {N}")
    a("  ILT_JZ rt_end")
    # text pointer: LINES + k * LINE_BYTES
    a(f"  LOCAL_U8_LOAD {K}")
    a(f"  PUSH_CONST_U8 {LINE_BYTES}")
    a("  IMUL")
    a(f"  PUSH_CONST_U24 0x{LINES:X}")
    a("  IADD")
    a("  STRING")
    a(f"  LOCAL_U8_STORE {P}")
    # y = BOTTOM - (count - k) * LINE_H  (line 0 on top, the newest at the bottom)
    a(f"  PUSH_CONST_F {BOTTOM}")
    a(f"  LOCAL_U8_LOAD {N}")
    a(f"  LOCAL_U8_LOAD {K}")
    a("  ISUB")
    a("  I2F")
    a(f"  PUSH_CONST_F {LINE_H}")
    a("  FMUL")
    a("  FSUB")
    a(f"  LOCAL_U8_STORE {Y}")
    # width of the text
    text_style()
    with_text("BEGIN_TEXT_COMMAND_GET_SCREEN_WIDTH_OF_DISPLAY_TEXT")
    a("  PUSH_CONST_1")
    a("  NATIVE 1, 1, @HUD::END_TEXT_COMMAND_GET_SCREEN_WIDTH_OF_DISPLAY_TEXT")
    a(f"  LOCAL_U8_STORE {W}")
    # box: centre x = X - PAD_X + (w + 2 PAD_X) / 2, centre y = y + BOX_H / 2
    a(f"  PUSH_CONST_F {X - PAD_X}")
    a(f"  LOCAL_U8_LOAD {W}")
    a(f"  PUSH_CONST_F {2 * PAD_X}")
    a("  FADD")
    a("  PUSH_CONST_F 0.5")
    a("  FMUL")
    a("  FADD")
    a(f"  LOCAL_U8_LOAD {Y}")
    a(f"  PUSH_CONST_F {BOX_H / 2}")
    a("  FADD")
    a(f"  LOCAL_U8_LOAD {W}")
    a(f"  PUSH_CONST_F {2 * PAD_X}")
    a("  FADD")
    a(f"  PUSH_CONST_F {BOX_H}")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_0")
    a("  PUSH_CONST_U8 170")
    a("  PUSH_CONST_0")
    a("  NATIVE 9, 0, @GRAPHICS::DRAW_RECT")
    # the text
    text_style()
    a("  PUSH_CONST_U8 255")
    a("  PUSH_CONST_U8 255")
    a("  PUSH_CONST_U8 255")
    a("  PUSH_CONST_U8 255")
    a("  NATIVE 4, 0, @HUD::SET_TEXT_COLOUR")
    a("  NATIVE 0, 0, @HUD::SET_TEXT_DROP_SHADOW")
    with_text("BEGIN_TEXT_COMMAND_DISPLAY_TEXT")
    a(f"  PUSH_CONST_F {X}")
    a(f"  LOCAL_U8_LOAD {Y}")
    a(f"  PUSH_CONST_F {TEXT_DY}")
    a("  FADD")
    a("  PUSH_CONST_0")
    a("  NATIVE 3, 0, @HUD::END_TEXT_COMMAND_DISPLAY_TEXT")
    a(f"  LOCAL_U8_LOAD {K}")
    a("  IADD_U8 1")
    a(f"  LOCAL_U8_STORE {K}")
    a("  J rt_loop")
    a("rt_end:")
    a("  LEAVE 0, 0")
    return "\n".join(o) + "\n"
