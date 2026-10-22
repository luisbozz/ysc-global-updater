"""Generates the job list query (.ysa): ul_main asks the cloud for one page of the player's own
jobs and hands it to Xenvious, so the My Jobs page is not limited to what the creator hub keeps.

The creator hub fills its own lists with UGC_QUERY_MY_CONTENT(offset, count, "gta5mission", kind,
-1, 0) (creator.c func_622: kind 1 published, 2 saved), but only from offset 0 and only 31 of each
(Global_995355.f_4, Global_1015489.f_33). Every creator script has the same natives in its table,
so the query runs in the open creator: Xenvious sets offset, count and kind, ul_main starts the
query once no other one runs, waits for it and copies each result into the data page, then clears
the results again (as the creator does once it has read them). Xenvious asks page by page (MAX per page: 0x800..0x2700 holds 20 entries).

Data page (string page 63, see drawer.py), in space the drawer no longer uses:
  0x2710  state     Xenvious writes 1 (request); 2 query running, 3 done, 4 failed
  0x2718  kind      1 published, 2 saved (UGC_QUERY_MY_CONTENT), 3 bookmarked, 4 recently played,
                    5 friends, 6 by category, 7 top rated, 8 newest, 9 one job by content id
  0x2720  offset
  0x2728  count     at most MAX
  0x2730  got       results copied
  0x2738  total     UGC_GET_CONTENT_TOTAL: all jobs of this kind
  0x2740  type      "gta5mission", NUL terminated; Xenvious writes it
  0x2750  category  for kind 6 (1 Rockstar created, 3 Rockstar verified)
  0x2758  request   the GET_* natives' out parameter
  0x2760  content id for kind 9 (24 bytes); Xenvious writes it
  0x0800  results   MAX entries of 0x180:
            +0x00 content id (24)   +0x18 root content id (24)   +0x30 name (64)
            +0x70 creator (32)      +0x90 file version           +0x98 language
            +0xA0 category          +0xA8 flags: 1 published, 2 verified, 4 bookmarked,
                                          8 has a record, 16 lo res photo, 32 hi res photo,
                                          64 made by this player
            +0xB0 rating (float)    +0xB8 rating count           +0xC0 positive ratings
            +0xC8 updated date (UGC_GET_CONTENT_UPDATED_DATE, up to 7 slots)
            +0x100 cloud path of file 0 (64)   +0x140 cloud path of file 1 (64)
Same layout as UgcList in Xenvious.
"""

DATA = 0xFC000
STATE, KIND, OFFSET, COUNT, GOT, TOTAL, TYPE = (DATA + o for o in (0x2710, 0x2718, 0x2720, 0x2728, 0x2730, 0x2738, 0x2740))
RESULTS, SIZE, MAX = DATA + 0x800, 0x180, 20
CATEGORY, REQUEST, CONTENT_ID = DATA + 0x2750, DATA + 0x2758, DATA + 0x2760
# KIND values and their branch: the creator hub's query kinds (creator.c func_622).
KINDS = ((1, "ul_q_mine"), (2, "ul_q_mine"), (3, "ul_q_bookmarked"), (4, "ul_q_played"), (5, "ul_q_friends"),
         (6, "ul_q_category"), (7, "ul_q_top"), (8, "ul_q_newest"), (9, "ul_q_id"))

N = "NETWORK::"
# Locals: 2 index, 3 results, 4 string index of the entry, 5 language.
I, CNT, E, LANG = 2, 3, 4, 5

FLAGS = (("UGC_GET_CONTENT_IS_PUBLISHED", 1), ("UGC_GET_CONTENT_IS_VERIFIED", 2),
         ("UGC_GET_CONTENT_HAS_PLAYER_BOOKMARKED", 4), ("UGC_GET_CONTENT_HAS_PLAYER_RECORD", 8),
         ("UGC_GET_CONTENT_HAS_LO_RES_PHOTO", 16), ("UGC_GET_CONTENT_HAS_HI_RES_PHOTO", 32),
         ("UGC_GET_CONTENT_CREATED_BY_LOCAL_PLAYER", 64))


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

    def field(off):
        # Pointer to the current entry + off.
        a(f"  LOCAL_U8_LOAD {E}")
        if off > 0xFF:
            a(f"  IADD_S16 {off}")
        elif off:
            a(f"  IADD_U8 {off}")
        a("  STRING")

    def put(off):
        field(off)
        a("  STORE")

    def text(native, off, size):
        a(f"  LOCAL_U8_LOAD {I}")
        a(f"  NATIVE 1, 1, @{N}{native}")
        field(off)
        a(f"  TEXT_LABEL_ASSIGN_STRING {size}")

    def state(v):
        a(f"  PUSH_CONST_U8 {v}")
        store(STATE)

    a("ul_main:")
    a("  ENTER 0, 8")
    load(STATE)
    a("  DUP")
    a("  PUSH_CONST_1")
    a("  IEQ_JZ ul_not_new")
    a("  DROP")
    a("  J ul_start")
    a("ul_not_new:")
    a("  PUSH_CONST_2")
    a("  IEQ_JZ ul_end")
    a("  J ul_wait")

    # A query of the creator's own may still run: start ours only after it.
    a("ul_start:")
    a(f"  NATIVE 0, 1, @{N}UGC_IS_GETTING")
    a("  JZ ul_go")
    a("  LEAVE 0, 0")
    a("ul_go:")
    # One branch per kind; each leaves the native's "started" result on the stack.
    for kind, label in KINDS:
        load(KIND)
        a(f"  PUSH_CONST_U8 {kind}")
        a(f"  IEQ_JZ ul_k{kind}_no")
        a(f"  J {label}")
        a(f"ul_k{kind}_no:")
    a("  J ul_fail")

    def common():
        load(OFFSET)
        load(COUNT)
        a(f"  PUSH_CONST_U24 0x{TYPE:X}")
        a("  STRING")

    def request():
        a(f"  PUSH_CONST_U24 0x{REQUEST:X}")
        a("  STRING")

    # Own jobs: published (1) or saved (2), the kind is the query's own argument.
    a("ul_q_mine:")
    common()
    load(KIND)
    a("  PUSH_CONST_M1")
    a("  PUSH_CONST_0")
    a(f"  NATIVE 6, 1, @{N}UGC_QUERY_MY_CONTENT")
    a("  J ul_started")
    # offset, count, type, request: bookmarked, recently played, friends, top rated.
    for label, native in (("ul_q_bookmarked", "UGC_GET_BOOKMARKED_CONTENT"), ("ul_q_played", "UGC_GET_MOST_RECENTLY_PLAYED_CONTENT"),
                          ("ul_q_friends", "UGC_GET_FRIEND_CONTENT"), ("ul_q_top", "UGC_GET_TOP_RATED_CONTENT")):
        a(f"{label}:")
        common()
        request()
        a(f"  NATIVE 4, 1, @{N}{native}")
        a("  J ul_started")
    # Category (1 Rockstar created, 3 Rockstar verified; creator.c func_435).
    a("ul_q_category:")
    load(CATEGORY)
    common()
    request()
    a(f"  NATIVE 5, 1, @{N}UGC_GET_GET_BY_CATEGORY")
    a("  J ul_started")
    a("ul_q_newest:")
    common()
    a("  PUSH_CONST_0")
    a(f"  NATIVE 4, 1, @{N}UGC_QUERY_MOST_RECENTLY_CREATED_CONTENT")
    a("  J ul_started")
    # One job by its content id, anyone's. UGC_QUERY_BY_CONTENT_ID finds only the player's
    # own jobs; the creators use it for those and this one for the rest (fm_lts_creator).
    a("ul_q_id:")
    a(f"  PUSH_CONST_U24 0x{CONTENT_ID:X}")
    a("  STRING")
    a(f"  PUSH_CONST_U24 0x{TYPE:X}")
    a("  STRING")
    a(f"  NATIVE 2, 1, @{N}UGC_GET_GET_BY_CONTENT_ID")
    a("ul_started:")
    a("  JZ ul_fail")
    state(2)
    a("  LEAVE 0, 0")

    a("ul_wait:")
    a(f"  NATIVE 0, 1, @{N}UGC_HAS_GET_FINISHED")
    a("  JZ ul_end")
    a(f"  NATIVE 0, 1, @{N}UGC_DID_GET_SUCCEED")
    a("  JZ ul_fail")
    a(f"  NATIVE 0, 1, @{N}UGC_GET_CONTENT_TOTAL")
    store(TOTAL)
    a(f"  NATIVE 0, 1, @{N}UGC_GET_CONTENT_NUM")
    a(f"  LOCAL_U8_STORE {CNT}")
    a(f"  LOCAL_U8_LOAD {CNT}")
    a(f"  PUSH_CONST_U8 {MAX}")
    a("  ILE_JZ ul_cap")
    a("  J ul_counted")
    a("ul_cap:")
    a(f"  PUSH_CONST_U8 {MAX}")
    a(f"  LOCAL_U8_STORE {CNT}")
    a("ul_counted:")
    a("  PUSH_CONST_0")
    a(f"  LOCAL_U8_STORE {I}")

    a("ul_next:")
    a(f"  LOCAL_U8_LOAD {I}")
    a(f"  LOCAL_U8_LOAD {CNT}")
    a("  ILT_JZ ul_done")
    a(f"  LOCAL_U8_LOAD {I}")
    a(f"  PUSH_CONST_S16 {SIZE}")
    a("  IMUL")
    a(f"  PUSH_CONST_U24 0x{RESULTS:X}")
    a("  IADD")
    a(f"  LOCAL_U8_STORE {E}")

    text("UGC_GET_CONTENT_ID", 0x00, 24)
    text("UGC_GET_ROOT_CONTENT_ID", 0x18, 24)
    text("UGC_GET_CONTENT_NAME", 0x30, 64)
    text("UGC_GET_CONTENT_USER_NAME", 0x70, 32)

    a(f"  LOCAL_U8_LOAD {I}")
    a(f"  NATIVE 1, 1, @{N}UGC_GET_CONTENT_LANGUAGE")
    a(f"  LOCAL_U8_STORE {LANG}")
    a(f"  LOCAL_U8_LOAD {LANG}")
    put(0x98)
    a(f"  LOCAL_U8_LOAD {I}")
    a(f"  LOCAL_U8_LOAD {LANG}")
    a(f"  NATIVE 2, 1, @{N}UGC_GET_CONTENT_FILE_VERSION")
    put(0x90)
    a(f"  LOCAL_U8_LOAD {I}")
    a(f"  NATIVE 1, 1, @{N}UGC_GET_CONTENT_CATEGORY")
    put(0xA0)

    # Flags: each bool (0 or 1) times its bit, added up.
    a("  PUSH_CONST_0")
    for native, bit in FLAGS:
        a(f"  LOCAL_U8_LOAD {I}")
        a(f"  NATIVE 1, 1, @{N}{native}")
        if bit > 1:
            a(f"  IMUL_U8 {bit}")
        a("  IADD")
    put(0xA8)

    for native, off in (("UGC_GET_CONTENT_RATING", 0xB0), ("UGC_GET_CONTENT_RATING_COUNT", 0xB8),
                        ("UGC_GET_CONTENT_RATING_POSITIVE_COUNT", 0xC0)):
        a(f"  LOCAL_U8_LOAD {I}")
        a("  PUSH_CONST_0")
        a(f"  NATIVE 2, 1, @{N}{native}")
        put(off)
    a(f"  LOCAL_U8_LOAD {I}")
    field(0xC8)
    a(f"  NATIVE 2, 0, @{N}UGC_GET_CONTENT_UPDATED_DATE")
    for k in (0, 1):
        a(f"  LOCAL_U8_LOAD {I}")
        a(f"  PUSH_CONST_{k}")
        a(f"  NATIVE 2, 1, @{N}UGC_GET_CONTENT_PATH")
        field(0x100 + k * 0x40)
        a("  TEXT_LABEL_ASSIGN_STRING 64")

    a(f"  LOCAL_U8_LOAD {I}")
    a("  IADD_U8 1")
    a(f"  LOCAL_U8_STORE {I}")
    a("  J ul_next")

    a("ul_done:")
    a(f"  LOCAL_U8_LOAD {CNT}")
    store(GOT)
    a(f"  NATIVE 0, 0, @{N}UGC_CLEAR_QUERY_RESULTS")
    state(3)
    a("  LEAVE 0, 0")
    a("ul_fail:")
    a(f"  NATIVE 0, 0, @{N}UGC_CLEAR_QUERY_RESULTS")
    state(4)
    a("ul_end:")
    a("  LEAVE 0, 0")
    return "\n".join(o) + "\n"
