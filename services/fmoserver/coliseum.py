"""The Coliseum: the arena desks (list, register, cancel, host, the bracket and streak
board), official and player-hosted arenas, and the pushes behind the waiting window."""
import json
import os
import struct
import time
from .knobs import _env_int
import fmoarena
from fmoarena import (  # noqa: F401 -- the coliseum's names, as before the move
    FORMAT_NORMAL, FORMAT_TOURNAMENT, OFFICIAL, OFFICIAL_SPEC, parse_official,
    official_rules, scheduled, read_state, WEAPON_SLOTS, BP_SLOTS, RULE_ALLOWED,
    RULE_BANNED, RULE_REQUIRED, MAIN_SEARCH_DESTROY, MAIN_SCORE, MAIN_HEAVY,
    SUB_NONE, SUB_SUDDEN, SUB_SUDDEN2)
from .wirelog import log


# --------------------------------------------------------------------------- #
# THE COLISEUM -- static 2026-10-01, every address in the unpacked client DLL
# --------------------------------------------------------------------------- #
# The Coliseum lobby (zone 600, map 161) has three desks and a board, and all
# of them ask the server through FIVE lobby-API objects (lobapi.LOBAPI) plus
# the mission block. The desks are queued by the async fetch object at vtable
# 0x6134306C (0x611CFDD0 starts it, kind at +0x14), built by:
#   0x611D0550 kind 1 -> 0x01B2 LIST      body+0x00 u8 = mode (0x61172CF0)
#   0x611D05B0 kind 2 -> 0x01B5 REGISTER  arena id + 8-byte password
#   0x611D0610 kind 3 -> 0x01B7 CANCEL    empty 32-byte body
#   0x611D0670 kind 4 -> 0x01BD HOST      the 128-byte arena record
#   0x611D06D0 kind 5 -> 0x01C2 BOARD     body+0x00 = arena id
#   0x611D0730 kind 6 -> 0x01C0 the MISSION BLOCK, with an arena id: SPECTATE
# plus 0x01BB (StartCOLReturnGroup, 0x61176C40 state 8), the streak's next
# battle, sent by the lobby tick itself after an arena battle.
#
# THE DESKS (the script syscalls at 0x610FB080..0x610FB113):
#   Registration.Officer  0x611B8F30: LIST mode 0; no rows -> 88:32 "There is
#       no Arena being held right now", else the arena list + 88:42.
#   Coliseum.Coordinator  0x611B6FF0: LIST mode 0; reply +0x0C < 1 -> 88:44
#       "Arena hosting is not being accepted right now", else the create
#       window (0x611B5250) + 88:43 (Second Lieutenant, 100 MP).
#   Delacroix (watch)     0x611B9000: LIST mode 1; no rows -> 88:46 "There's
#       no Arena to watch", else the list + 88:45.
#   the board             0x611B90D0: LIST mode 2, the list with no message.
# Every one of those lines has been seen on screen, live 2026-09-09, as the
# answer to our zero-filled replies. The zeros are what this module replaces.
#
# KEY: THE REPLY TO A FETCH IS JUDGED BY ITS ID, NOT ITS CONTENT. The poller
# 0x61172250 compares the reply id with the one the start function stamped;
# a different id is a failure and its conn field (+0x08) becomes the code the
# client prints as [FMxxxxx]. So a refusal is message 2 with our code there.
#
# WARNING: NOT LIVE-TESTED. Every layout below is read off the client; none of
# it has reached a screen. FMO_COLISEUM=0 (the default) keeps the zero stubs.
COLISEUM = (os.environ.get("FMO_COLISEUM", "").strip() or "0") != "0"

MSG_LIST_REQ, MSG_LIST_REPLY = 0x01B2, 0x01B3
MSG_REGISTER_REQ, MSG_REGISTER_REPLY = 0x01B5, 0x01B6
MSG_CANCEL_REQ, MSG_CANCEL_REPLY = 0x01B7, 0x01B8
MSG_RETURN_REQ, MSG_RETURN_REPLY = 0x01BB, 0x01BC
MSG_HOST_REQ, MSG_HOST_REPLY = 0x01BD, 0x01BE
MSG_BOARD_REQ, MSG_BOARD_REPLY = 0x01C2, 0x01C3
#: The three Coliseum pushes (lobby dispatcher table 2, all NO gate):
#:   0x01B9 COL_ADD_UPDATE (arm 0x6117F512): +0x00 s32 result (logged only),
#:          then the 128-byte arena record from +0x04 -> 0x611B70C0, which
#:          copies it to 0x6139749E and opens the WAITING window 0x611B3E50
#:          ("waiting to sortie", Cancel and Arena Info). Only once: the
#:          0x6139749D latch is cleared by a cancel dialog.
#:   0x01BA COL_CANCEL_UPDATE (arm 0x6117F59C): +0x00 s32 result. With
#:          lobby+0x6E42 == 0 it is handed to 0x611B03D0, and the waiting
#:          window's tick 0x611B7A10 shows a dialog: result -10 -> title 87:8
#:          "You Win" + 88:33 "You won the tournament."; any other -> 88:23
#:          "Canceled" with the text 0x611B0120 picks (CANCEL_TEXT).
#:   0x01BF COL_RET_UPDATE (arm 0x6117F542): +0x00 s32; < 0 only logs "Error
#:          occurred; stopping streak" in the client's debug channel.
MSG_ADD_UPDATE, MSG_CANCEL_UPDATE, MSG_RET_UPDATE = 0x01B9, 0x01BA, 0x01BF
HANDLED = (MSG_LIST_REQ, MSG_REGISTER_REQ, MSG_CANCEL_REQ, MSG_RETURN_REQ,
           MSG_HOST_REQ, MSG_BOARD_REQ)

#: 0x611B0120: the 0x01BA result -> the cancel dialog's text (group 88).
CANCEL_TEXT = {0: "88:24 'Your Arena registration was cancelled.'",
               -1: "88:24 'Your Arena registration was cancelled.'",
               -2: "88:25 'Your setup or BG cost breaks the rules.'",
               -3: "88:25 'Your setup or BG cost breaks the rules.'",
               -4: "88:26 'This Arena has ended.'",
               -5: "88:28 'Your BG headcount does not match the required headcount'",
               -6: "88:36 'The tournament is full.'",
               -10: "87:8 'You Win' / 88:33 'You won the tournament.'"}
CANCEL_DONE, CANCEL_ENDED, CANCEL_HEADCOUNT, CANCEL_FULL, CANCEL_WON = 0, -4, -5, -6, -10


# --------------------------------------------------------------------------- #
# THE ARENA RECORD -- 128 bytes, one layout everywhere
# --------------------------------------------------------------------------- #
# The list rows (0x01B3), the waiting window (0x01B9), the create request
# (0x01BD, built by 0x611B16F0 from the create window) and the bracket record
# (0x01C3) all carry the same 0x80 bytes. Read from the detail renderer
# 0x611B1CD0, the list row draw 0x611B7BE0 and its sort 0x611B0670:
#   +0x00 u32    arena id (the REGISTER / BOARD / spectate key)
#   +0x04 16 B   promoter, cp932 "First.Last" (strncpy 15) -- list column
#                87:2 "Promoter"
#   +0x14 32 B   arena name, cp932 (the create box takes 31) -- 87:1 "Arena"
#   +0x34 s8     required BG count for a tournament (4 / 8) -- the "BGNum"
#                column, "-" when < 1; "Req BG:%d" in the detail
#   +0x35 s8     required headcount (88:0 "Required headcount", 1..5)
#   +0x36 s8     BG cost a member may bring \  "(BG Cost: %d/%d)" prints
#   +0x37 s8     total BG cost (headcount x) /  +0x37 then +0x36
#   +0x39 u8     format: 1 normal, 2 tournament (87:10 / 87:11)
#   +0x3B u8     nonzero = hidden from the REGISTRATION list (0x611B7E40 shows
#                it only in the spectate / board lists): a closed tournament
#   +0x3C 13 B   weapon rule, one byte per 95:0..12 (Machine Gun .. Shield):
#                0 allowed, 1 banned, 2 required
#   +0x49 12 B   BP rule, one byte per 95:13..24 (Radio A .. Turbo): the same
#   +0x5C u32    target score; every window that shows a SERVER row prints it
#                /100 (only the client's own create preview prints 94:36..38)
#   +0x60 u8     main rule: 0 Search & Destroy, 1 Score Attack, 2 Heavy Mobile
#                Weapon Orders (89:5 / 89:0 / 89:1, described by 92:0..2)
#   +0x61 u8     sub-rule: 0 none, 1 Sudden Death, 2 Sudden Death 2
#   +0x62 u8     modified units: 0 banned 88:11, 1 allowed 88:12
#   +0x63 u8     weapon arms: 0 banned 88:9, 1 allowed 88:10
#   +0x64 u8     duplicate BP: 0 banned 88:40, 1 allowed 88:41
#   +0x65 u8     1 = a password prompt before REGISTER (0x611B71C0, 8 chars)
#                for a tournament; also prints "Leader entry fee: H$0"
#   +0x68 u32    START, unix seconds against the client clock 0x611E3B00
#                (ms / 1000): before it the row is greyed and shows %02d:%02d
#                (0x611E3D00 form 6), after it "---" in the normal colour
#   +0x6C u32    end, unix seconds (ours; no client reader found)
#   +0x70 u32    map = 903,000,000 + the selector-600 tile (the create
#                window's map list 0x611B85E0 takes ARE ids 30000..30999 and
#                adds 903,000,000; its default 903,030,001 is Special Field 01)
#   +0x74 u32    leader entry fee (88:3); the prize per win (88:4) is this
#                for a normal arena and twice it for a tournament
ROW_LEN = 0x80
R_ID, R_PROMOTER, R_NAME = 0x00, 0x04, 0x14
R_PROMOTER_LEN, R_NAME_LEN = 0x10, 0x20
R_REQ_BGS, R_HEADCOUNT, R_BG_COST, R_TOTAL_COST = 0x34, 0x35, 0x36, 0x37
R_FORMAT, R_CLOSED = 0x39, 0x3B
R_WEAPONS, R_BPS = 0x3C, 0x49
N_WEAPONS, N_BPS = 13, 12
R_SCORE, R_MAIN, R_SUB = 0x5C, 0x60, 0x61
R_MODS, R_ARMS, R_DUPBP, R_LOCKED = 0x62, 0x63, 0x64, 0x65
R_START, R_END, R_MAP, R_FEE = 0x68, 0x6C, 0x70, 0x74
MAP_BASE = 903000000
#: The arena maps: selector 600 of SE's sector table (fmosectors), tile ->
#: battle MapNo. The create window offers the same ARE rows.
ARENA_SELECTOR = 600


def _cp932(text, width):
    raw = str(text or "").encode("cp932", "replace")[:width - 1]
    return raw.ljust(width, b"\0")


def _uncp932(raw):
    return bytes(raw).split(b"\0", 1)[0].decode("cp932", "replace")


def arena_row(a):
    """The 128-byte record for arena dict `a`. A hosted arena keeps the bytes
    its creator's client sent (`raw`), so fields nobody has decoded go back
    exactly as they came; every field this server owns is written over them."""
    b = bytearray(bytes.fromhex(a["raw"]) if a.get("raw") else bytes(ROW_LEN))
    b = (b + bytes(ROW_LEN))[:ROW_LEN]
    struct.pack_into("<I", b, R_ID, int(a["id"]) & 0xFFFFFFFF)
    b[R_PROMOTER:R_PROMOTER + R_PROMOTER_LEN] = _cp932(a.get("promoter"), R_PROMOTER_LEN)
    b[R_NAME:R_NAME + R_NAME_LEN] = _cp932(a.get("name"), R_NAME_LEN)
    b[R_REQ_BGS] = int(a.get("req_bgs") or 0) & 0xFF
    b[R_HEADCOUNT] = int(a.get("headcount") or 1) & 0xFF
    b[R_BG_COST] = int(a.get("bg_cost") or 0) & 0xFF
    b[R_TOTAL_COST] = int(a.get("total_cost") or 0) & 0xFF
    b[R_FORMAT] = int(a.get("format") or FORMAT_NORMAL) & 0xFF
    b[R_CLOSED] = 1 if a.get("closed") else 0
    w = list(a.get("weapons") or ())[:N_WEAPONS]
    b[R_WEAPONS:R_WEAPONS + N_WEAPONS] = bytes(w + [0] * (N_WEAPONS - len(w)))
    p = list(a.get("bps") or ())[:N_BPS]
    b[R_BPS:R_BPS + N_BPS] = bytes(p + [0] * (N_BPS - len(p)))
    struct.pack_into("<I", b, R_SCORE, int(a.get("score") or 0) & 0xFFFFFFFF)
    b[R_MAIN] = int(a.get("main_rule") or 0) & 0xFF
    b[R_SUB] = int(a.get("sub_rule") or 0) & 0xFF
    b[R_MODS] = 1 if a.get("mods", 1) else 0
    b[R_ARMS] = 1 if a.get("arms", 1) else 0
    b[R_DUPBP] = 1 if a.get("dup_bp", 1) else 0
    b[R_LOCKED] = 1 if a.get("password") else 0
    struct.pack_into("<I", b, R_START, int(a.get("start") or 0) & 0xFFFFFFFF)
    struct.pack_into("<I", b, R_END, int(a.get("end") or 0) & 0xFFFFFFFF)
    struct.pack_into("<I", b, R_MAP, (MAP_BASE + int(a.get("tile") or 0)) & 0xFFFFFFFF)
    struct.pack_into("<I", b, R_FEE, int(a.get("fee") or 0) & 0xFFFFFFFF)
    return bytes(b)


def parse_arena_row(raw):
    """The fields of a 128-byte record (a 0x01BD body) as an arena dict.
    `raw` is kept so arena_row() can hand undecoded bytes back unchanged."""
    raw = (bytes(raw) + bytes(ROW_LEN))[:ROW_LEN]
    m = struct.unpack_from("<I", raw, R_MAP)[0]
    return {
        "id": struct.unpack_from("<I", raw, R_ID)[0],
        "promoter": _uncp932(raw[R_PROMOTER:R_PROMOTER + R_PROMOTER_LEN]),
        "name": _uncp932(raw[R_NAME:R_NAME + R_NAME_LEN]),
        "req_bgs": struct.unpack_from("<b", raw, R_REQ_BGS)[0],
        "headcount": struct.unpack_from("<b", raw, R_HEADCOUNT)[0],
        "bg_cost": struct.unpack_from("<b", raw, R_BG_COST)[0],
        "total_cost": struct.unpack_from("<b", raw, R_TOTAL_COST)[0],
        "format": raw[R_FORMAT],
        "closed": bool(raw[R_CLOSED]),
        "weapons": list(raw[R_WEAPONS:R_WEAPONS + N_WEAPONS]),
        "bps": list(raw[R_BPS:R_BPS + N_BPS]),
        "score": struct.unpack_from("<I", raw, R_SCORE)[0],
        "main_rule": raw[R_MAIN], "sub_rule": raw[R_SUB],
        "mods": raw[R_MODS], "arms": raw[R_ARMS], "dup_bp": raw[R_DUPBP],
        "start": struct.unpack_from("<I", raw, R_START)[0],
        "end": struct.unpack_from("<I", raw, R_END)[0],
        "tile": (m - MAP_BASE) if MAP_BASE <= m < MAP_BASE + 1000000 else 0,
        "fee": struct.unpack_from("<I", raw, R_FEE)[0],
        "raw": raw.hex(),
    }


#: 0x611B16F0: the leader entry fee the create window writes at +0x74, by
#: headcount (+0x35). A tournament charges the second column, halved when it
#: takes 4 BGs. SE's 2006-08-15 update: "the entry fee and prize of a player
#: arena are set automatically from the headcount" -- these are those numbers.
HOST_FEE = {1: (2000, 4000), 2: (3200, 6400), 3: (4500, 9000), 4: (5000, 10000),
            5: (6000, 12000)}


def host_fee(fmt, headcount, req_bgs=0):
    normal, tourney = HOST_FEE.get(int(headcount), (6000, 12000))
    if int(fmt) != FORMAT_TOURNAMENT:
        return normal
    return tourney // 2 if int(req_bgs) == 4 else tourney


def prize_per_win(a):
    """88:4 'Prize: per win' as the detail draws it (0x611B1CD0)."""
    fee = int(a.get("fee") or 0)
    return fee if int(a.get("format") or 1) == FORMAT_NORMAL else fee * 2


# --------------------------------------------------------------------------- #
# THE REPLIES
# --------------------------------------------------------------------------- #
#: 0x01B3 -- the LIST (parse 0x61172D20): into the fetch's out struct
#: (0x61396F6C, whose +0x00 is the mode the request sent):
#:   payload+0x0C u32 -> out+0x08 (0x61396F74): HOSTING SLOTS OPEN. The
#:                Coordinator's callback 0x611B6F10 opens the create window
#:                only when it is >= 1, else 88:44.
#:   payload+0x10 u32 -> out+0x04 (0x61396F70): ROW COUNT. The Officer's and
#:                Delacroix's callbacks need >= 1.
#:   payload+0x14 0x500 B -> out+0x0C: the rows, 0x80 apart, so TEN at most.
LIST_LEN = 1300
L_OPEN_SLOTS, L_COUNT, L_ROWS = 0x0C, 0x10, 0x14
L_MAX_ROWS = 10
LIST_MODES = {0: "registration / hosting desk", 1: "spectate desk (Delacroix)",
              2: "the arena board"}


def list_body(rows, open_slots=0):
    rows = list(rows)[:L_MAX_ROWS]
    b = bytearray(LIST_LEN)
    struct.pack_into("<I", b, L_OPEN_SLOTS, max(0, int(open_slots)))
    struct.pack_into("<I", b, L_COUNT, len(rows))
    for i, r in enumerate(rows):
        if len(r) != ROW_LEN:
            raise ValueError(f"list row {i} is {len(r)}B, want {ROW_LEN}")
        b[L_ROWS + i * ROW_LEN:L_ROWS + (i + 1) * ROW_LEN] = r
    return bytes(b)


#: 0x01C3 -- the BOARD (parse 0x61173070): payload+0x00 u32 -> out+0x04, the
#: status (the result window 0x611B5D90 draws 88:47 "Arena information could
#: not be obtained" when it is < 0); payload+0x04, 0xBC0 bytes -> out+0x08 =
#: 0x613975AF, the RECORD the bracket/streak renderer 0x611B5F50 reads:
#:   +0x00   the 128-byte arena record (+0x34 BG count, +0x39 format)
#:   +0x80   s8 entry count
#:   +0x88   entries, 0x58 apart: +0x01 u8 rounds won (a NORMAL arena's
#:           entry 0 puts the viewer's CURRENT streak here), +0x02 u8 state
#:           (0 = out, drawn dark; 3 = the viewer's own, highlighted), +0x03
#:           s8 win streak, +0x04 the name, cp932.
#: A normal arena's board (88:35 "Arena win streak record") lists entries
#: 1..count-1 with a streak > 0; the waiting window's form (88:34 "Current
#: win streak") reads entry 0's +0x01 and entry 1. A tournament draws the
#: bracket only once count >= the arena's BG count, else 94:39 "BGs
#: registered for the tournament: %d/%d".
BOARD_LEN = 3012
B_STATUS, B_RECORD = 0x00, 0x04
B_COUNT = B_RECORD + 0x80
B_ENTRIES = B_RECORD + 0x88
B_ENTRY_LEN = 0x58
B_E_ROUND, B_E_STATE, B_E_STREAK, B_E_NAME = 0x01, 0x02, 0x03, 0x04
B_MAX_ENTRIES = 16
STATE_OUT, STATE_IN, STATE_SELF = 0, 1, 3


def board_body(row, entries, status=0):
    """entries: [(name, rounds, state, streak)] in draw order."""
    entries = list(entries)[:B_MAX_ENTRIES]
    b = bytearray(BOARD_LEN)
    struct.pack_into("<i", b, B_STATUS, int(status))
    b[B_RECORD:B_RECORD + ROW_LEN] = row
    b[B_COUNT] = len(entries) & 0xFF
    for i, (name, rounds, state, streak) in enumerate(entries):
        at = B_ENTRIES + i * B_ENTRY_LEN
        b[at + B_E_ROUND] = max(0, min(255, int(rounds)))
        b[at + B_E_STATE] = int(state) & 0xFF
        b[at + B_E_STREAK] = max(0, min(127, int(streak)))
        b[at + B_E_NAME:at + B_ENTRY_LEN] = _cp932(name, B_ENTRY_LEN - B_E_NAME)
    return bytes(b)


def add_update_body(row, result=0):
    return struct.pack("<i", int(result)) + bytes(row)


def cancel_update_body(result):
    return struct.pack("<i", int(result))


#: 0x01B6 -- REGISTER's reply (parse 0x61172E00): one dword, read into the
#: fetch's out struct and not looked at. The callback 0x611B02C0 only asks
#: whether the reply id matched: yes = registered (it raises 0x6139748D /
#: 0x6139749C / 0x61398EE9 and the list window closes), no = 88:21
#: "Registration failed." with our conn field as the [FMxxxxx] number. The
#: waiting window is NOT opened by the reply -- only by 0x01B9.
#: Our refusal codes (the client's error table 0x613955F0 has no Arena rows,
#: so any number prints 88:21; these make the log and the screen agree):
CODE_GONE, CODE_ENDED, CODE_CLOSED, CODE_TWICE = 8801, 8802, 8803, 8804
CODE_HEADCOUNT, CODE_LEADER, CODE_COST, CODE_FULL = 8805, 8806, 8807, 8808
CODE_MONEY, CODE_PASSWORD = 8809, 8810
CODE_TEXT = {CODE_GONE: "no such arena", CODE_ENDED: "the arena has ended",
             CODE_CLOSED: "registration is closed", CODE_TWICE: "already registered",
             CODE_HEADCOUNT: "the BG headcount does not match",
             CODE_LEADER: "only the BG leader can register",
             CODE_COST: "a member's BG cost breaks the arena's limit",
             CODE_FULL: "the tournament is full", CODE_MONEY: "not enough money",
             CODE_PASSWORD: "wrong password"}
#: 0x01B8 -- CANCEL's reply (parse 0x61172FC0): payload+0x00 -> 0x6139748E.
#: The callback 0x611B02F0 raises the cancel dialog when it is -1 (or the
#: reply failed); any other value leaves the waiting window open.
CANCEL_REPLY_DONE, CANCEL_REPLY_KEPT = -1, 0
#: 0x01BE -- HOST's reply (parse 0x61172FC0): payload+0x00 -> 0x6139751E,
#: which 0x611B0DF0 reads: >= 0 -> 94:31 "The Arena was created"; -2 -> 94:35
#: "Arenas cannot be issued right now"; -3 -> 94:34 "Only one Arena can be
#: set"; any other negative -> 94:17 "The Arena could not be created".
HOST_REFUSED, HOST_CLOSED, HOST_ONE = -1, -2, -3
HOST_TEXT = {HOST_REFUSED: "94:17 'The Arena could not be created.'",
             HOST_CLOSED: "94:35 'Arenas cannot be issued right now.'",
             HOST_ONE: "94:34 'Only one Arena can be set.'"}
#: 0x01BC -- RETURN's reply (parse 0x61172FC0): payload+0x00 -> 0x613C0C00;
#: < 0 = "EndCOLReturnGroup failed" and 8:83, >= 0 = ColosseumLockUI (the
#: waiting window again, for the streak's next battle).
RETURN_OK, RETURN_REFUSED = 0, -1


# --------------------------------------------------------------------------- #
# THE ARENAS
# --------------------------------------------------------------------------- #
#: The official arenas (FMO_COLISEUM_OFFICIAL) and their weekday/hour schedule
#: live in fmoarena.py, shared with the City Control board.
#: FMO_COLISEUM_HOSTING -- player-hosted arenas: the Coordinator's slot count
#: per game day (SE: "at 18:00 every day the system sets how many arenas can
#: be held"). 0 = the Coordinator refuses (88:44).
HOST_CAP = _env_int("FMO_COLISEUM_HOSTING", "3")
#: The day turns at this UTC hour (9 = 18:00 JST, SE's time).
DAY_HOUR_UTC = _env_int("FMO_COLISEUM_DAY_HOUR", "9")
#: 88:43: "Only those ranked Second Lieutenant or above can host an Arena.
#: Hosting an Arena also costs 100MP." Rank byte 18 = Second Lieutenant.
HOST_RANK = _env_int("FMO_COLISEUM_HOST_RANK", "18")
HOST_MP = _env_int("FMO_COLISEUM_HOST_MP", "100")
#: A hosted arena opens for registration at once and its battles start
#: FMO_COLISEUM_HOST_LEAD seconds later; it closes FMO_COLISEUM_HOST_HOURS
#: after that (SE's own player tournaments and Clash of Iron ran 2 hours).
HOST_LEAD = _env_int("FMO_COLISEUM_HOST_LEAD", "600")
HOST_HOURS = _env_int("FMO_COLISEUM_HOST_HOURS", "2")
#: The create window's target score is a level (94:36..38 Few / Average /
#: Many, 0..2); the arena's rows carry the score itself.
TARGET_SCORES = tuple(int(x) for x in (
    os.environ.get("FMO_COLISEUM_SCORES", "").strip() or "3000,5000,8000").split(","))
#: How many win-streak records each arena keeps.
RECORDS_KEPT = 10

# --------------------------------------------------------------------------- #
# THE MATCHES
# --------------------------------------------------------------------------- #
# Two entries of one arena are paired and every member is sent into the
# arena's battle map by the AUTO-SORTIE push 0x014E (sortiepush.py; the
# waiting window needs no request of its own) -- its countdown runs, the
# client asks 0x014D, and on_sortie_go grants it for the match. The block
# carries:
#   +0x6C u32  6 = an ARENA battle: 0x61173AA0 puts the lobby in state 0xD
#              (1 -> 0xB, 7 -> 0xE), the state whose result asks for the
#              streak's next battle (0x61176C40 states 8..10 -> 0x01BB)
#   +0x4C u32  the time limit, seconds (MB_TIMELIMIT)
#   +0xC55 u8  this pilot's team (MB_BATTLE_SIDE), which 0x611B0410 also uses
#              to compare the two sides' Coliseum records (97:0..6)
# The team is also the pop's friend/foe byte (popnation.battle_side_for) and
# the room's hostility (rooms.battle_room_hostile), so two BGs of ONE nation
# fight each other, and the match id keeps two matches on one map apart
# (rooms.room_mates).
#
# JUDGING. Retail combat was peer to peer, and the only death the server sees
# is the victim's own: a hit record flagged DIED whose target is the sender's
# own unit (referee._note_battle_record -> battleend.PILOT_DEATHS). A team
# whose every member died, never sortied, or dropped loses; at the time limit
# the team with more pilots standing wins; equal is a draw (both lose). Only
# Search & Destroy is judged faithfully -- no score reaches the server.
ARENA_BATTLE_KIND = 6
B_BATTLE_KIND = 0x06C
#: FMO_COLISEUM_TIME -- an arena battle's time limit, seconds.
MATCH_TIME = _env_int("FMO_COLISEUM_TIME", "600")
#: FMO_COLISEUM_COUNTDOWN -- the 0x014E countdown (8:3 "Sortieing to the
#: battle map automatically in %d seconds"); also the battle seed, the same
#: for every pilot of a match.
COUNTDOWN = _env_int("FMO_COLISEUM_COUNTDOWN", "10")
#: A member who has not sortied (0x014D) this long after the countdown is out.
GO_WAIT = _env_int("FMO_COLISEUM_GO_WAIT", "90")
#: A normal-arena winner has this long to take the streak's next battle
#: (0x01BB) before the entry is dropped.
RETURN_WAIT = _env_int("FMO_COLISEUM_RETURN_WAIT", "180")

# --------------------------------------------------------------------------- #
# SPECTATING
# --------------------------------------------------------------------------- #
# Delacroix's list (0x01B2 mode 1) shows the arenas with a battle under way.
# Picking one asks the MISSION BLOCK request 0x01C0 with the arena id
# (0x611B1C90, fetch kind 6), and its reply 0x01C1 is read differently from
# the mission board's (parse 0x61173140 into the spectate struct 0x61398174):
#   payload+0x000 s32   status -> 0x61398178; < 0 = 88:51 "Failed to spectate"
#   payload+0x004 20 B  the battle endpoint -> 0x61398EC4
#   payload+0x018 3400B the battle's block -> lobby+0x5C7E (0x61175600)
#   payload+0xD60 u32   time -> 0x61398ED8, and % 1e6 -> lobby+0x4F02 (seed)
# Then 0x611B0380 calls 0x61006270(time % 1e6, endpoint) -- the same scene-4
# entry a sortie uses, WITHOUT 0x6117AC40(1, 0) first, so lobby+0x30 stays 0:
# the client is in the battle scene without a battle map of its own. The seed
# must equal the fighters' (their 0x014E +0x04, COUNTDOWN).
# A refusal is message 2 with a code from the client's table 0x613955F0:
#   -15851 -> 6:1 "The Arena spectating window has passed."
#   -15852 -> 6:2 "There are no spectator slots left in the Arena."
#   -14141 -> 2:127 "To spectate the Coliseum you must leave your battle group."
# SE (2005-12-20): one of the arena's battles is assigned automatically, 10
# spectators per battle, spectator mode, no chat.
#
# On the battle channel a spectator only RECEIVES: it shares its match's room
# (rooms.room_mates), is never popped into anyone's scene (roomrelay), and
# nothing it sends -- peer-link records, hits, field syncs, chat -- is relayed
# (peerlink, referee, datagram), nor credited a kill (rooms). A fighter's
# own-unit stream reaches it as a one-way copy (peerlink.spectator_copies).
MSG_SPECTATE_REQ, MSG_SPECTATE_REPLY = 0x01C0, 0x01C1
SPECTATE_LEN = 3428
SP_STATUS, SP_ENDPOINT, SP_BLOCK, SP_TIME = 0x000, 0x004, 0x018, 0xD60
SP_ENDPOINT_LEN, SP_BLOCK_LEN = 20, 3400
SPECTATE_LATE, SPECTATE_FULL, SPECTATE_IN_GROUP = -15851, -15852, -14141
SPECTATE_TEXT = {SPECTATE_LATE: "6:1 'The Arena spectating window has passed.'",
                 SPECTATE_FULL: "6:2 'There are no spectator slots left in the Arena.'",
                 SPECTATE_IN_GROUP: "2:127 'To spectate the Coliseum you must leave "
                                    "your battle group.'"}
#: FMO_COLISEUM_SPECTATORS -- spectators per battle (SE: 10).
SPECTATE_SLOTS = _env_int("FMO_COLISEUM_SPECTATORS", "10")
#: FMO_COLISEUM_SPECTATE_FEE -- H$ per spectate (8:84 "Paid H$%d as the
#: Coliseum spectator fee", 0x01A1 +0x10). SE's figure is unknown; 0 = free.
SPECTATE_FEE = _env_int("FMO_COLISEUM_SPECTATE_FEE", "0")
#: FMO_COLISEUM_SPECTATE_SELF -- 1 (default) = pop the spectator's own unit on
#: its battle channel like any pilot's (the lobby scene waits for the local
#: unit before it renders; whether the battle scene does for a spectator is
#: unmeasured), seen by nobody else and relaying nothing; 0 = no own unit.
SPECTATE_SELF = _env_int("FMO_COLISEUM_SPECTATE_SELF", "1") != 0
#: A 0x01C0 counts as a spectate request this long after the pilot was served
#: Delacroix's list; any other 0x01C0 is the mission board's.
SPECTATE_LIST_WINDOW = 180


def endpoint_for_peer(body, off, peer_ip):
    """(`body` with the battle endpoint at `off` written for the client at
    `peer_ip`, host). A match's 0x014E is built once per team, so its endpoint
    used to be the bare BATTLE_HOST (the tailnet address): live 2026-10-06 the
    first Coliseum match sent a public pilot to an address it cannot reach
    (black screen, back to the title) and a tailnet pilot in from a second
    source IP the battle channel could not bind (a nameless unit, a crash).
    Same rule as 0x013A and 0x0153: addressing.host_for per client."""
    if not sortie.SORTIE_ENDPOINT or not body:
        return body, None
    h = sortie.SORTIE_HOST or addressing.host_for(addressing.BATTLE_HOST, peer_ip)
    pt = sortie.SORTIE_PORT or addressing.BATTLE_PORT
    ep = (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)(h, pt)
    b = bytearray(body)
    b[off:off + len(ep)] = ep
    return bytes(b), h


def spectate_body(push_body, status=0):
    """The 0x01C1 a spectator gets, cut from the 0x014E body a fighter of the
    same match got: its endpoint, its block, its time (so the seed matches)."""
    b = bytearray(SPECTATE_LEN)
    struct.pack_into("<i", b, SP_STATUS, int(status))
    if push_body:
        e = sortiepush.R14E_ENDPOINT
        b[SP_ENDPOINT:SP_ENDPOINT + SP_ENDPOINT_LEN] = push_body[e:e + SP_ENDPOINT_LEN]
        k = sortiepush.R14E_BLOCK
        b[SP_BLOCK:SP_BLOCK + SP_BLOCK_LEN] = push_body[k:k + SP_BLOCK_LEN]
        t = sortiepush.R14E_TIME
        b[SP_TIME:SP_TIME + 4] = push_body[t:t + 4]
    return bytes(b)


#: Hosted arena ids start here, clear of the official ones.
HOSTED_BASE = 1000


def arena_maps():
    """{tile: battle MapNo} for the arena selector, from SE's sector table."""
    if fmosectors is None:
        return {}
    return {tile: mapno for tile, (_row, mapno)
            in (fmosectors.SECTORS.get(ARENA_SELECTOR) or {}).items()}


def arena_mapno(a):
    """The battle MapNo of arena `a`'s tile (SE's selector-600 table), or None."""
    if fmosectors is None:
        return None
    r = fmosectors.battle_map_for(ARENA_SELECTOR, int(a.get("tile") or 0))
    return int(r[1]) if r else None


def sortie_push_body(a, mapno, side, countdown=None, limit=None):
    """The 0x014E payload that sends one member of a match into the arena:
    sortiepush's block for `mapno`, then the arena's own fields (+0x6C = 6,
    the time limit, the team). None when sortiepush refuses the map."""
    body = sortiepush.reply_014e(mapno=str(int(mapno)),
                                 time_s=COUNTDOWN if countdown is None else countdown,
                                 dest=(a.get("name") or "Arena")[:40])
    if body is None:
        return None
    b = bytearray(body)
    base = sortiepush.R14E_BLOCK
    struct.pack_into("<I", b, base + B_BATTLE_KIND, ARENA_BATTLE_KIND)
    struct.pack_into("<I", b, base + missionblock.MB_TIMELIMIT,
                     int(MATCH_TIME if limit is None else limit))
    b[base + missionblock.MB_BATTLE_SIDE] = int(side) & 0xFF
    return bytes(b)


def game_day(now, hour=None):
    """The game day `now` falls in: it turns at DAY_HOUR_UTC."""
    hour = DAY_HOUR_UTC if hour is None else hour
    return int((float(now) - hour * 3600) // 86400)


def pilot_name(char):
    c = char or {}
    return ".".join(x for x in (c.get("first"), c.get("last")) if x) or "Pilot"


class Coliseum:
    """The arenas, who is registered where, and the streak records.

    The hosted arenas, the day's hosting count and the records are one JSON
    document (fmo_coliseum, written whole like fmo_war); registrations are
    held in memory, because a restart drops every client's waiting window
    with them."""

    def __init__(self, load=True):
        self.hosted = {}
        self.next_id = HOSTED_BASE
        self.day = None
        self.hosted_today = 0
        self.records = {}
        self.entries = {}               # arena id -> [entry]
        self.entry_of = {}              # account -> arena id
        #: account -> [(push id, body, what)], drained by that pilot's own
        #: keepalive (coliseum_pushes_due). Anything one pilot's action owes
        #: another (the waiting window, a withdrawn entry, an arena that
        #: ended) waits here, so it reaches them even if they relog first.
        self.notices = {}
        self.matches = {}               # match id -> match
        self.match_of = {}              # account -> match id, until its verdict is taken
        self.verdicts = {}              # account -> the verdict its keepalive delivers
        self.returning = {}             # account -> (winning entry, deadline)
        self.spectators = {}            # account -> the match it watches
        self.spectate_end = {}          # account -> a watched match that ended
        self.next_match = 1
        self.present = False
        if load:
            self.load()

    # -- persistence ------------------------------------------------------- #
    def document(self):
        return {"hosted": {str(k): v for k, v in self.hosted.items()},
                "next_id": self.next_id, "day": self.day,
                "hosted_today": self.hosted_today, "records": self.records}

    def adopt(self, d):
        d = d or {}
        self.hosted = {int(k): v for k, v in (d.get("hosted") or {}).items()}
        self.next_id = max(int(d.get("next_id") or HOSTED_BASE), HOSTED_BASE,
                           max(self.hosted, default=HOSTED_BASE - 1) + 1)
        self.day = d.get("day")
        self.hosted_today = int(d.get("hosted_today") or 0)
        self.records = {str(k): [list(r) for r in v]
                        for k, v in (d.get("records") or {}).items()}

    def load(self):
        data = read_state()
        self.present = data is not None
        self.adopt(data)

    def save(self):
        if not write_state(self.document()):
            log("WARNING: coliseum: the arena state could NOT be written (no "
                "database?) -- hosted arenas and records live until a restart")
            return False
        return True

    # -- the arenas -------------------------------------------------------- #
    def notify(self, account, mid, body, what, meta=None):
        self.notices.setdefault(account, []).append((mid, bytes(body), what, meta))

    def take_notices(self, account):
        return self.notices.pop(account, [])

    def tick(self, now):
        """Roll the game day and end hosted arenas whose time is up; every
        pilot still waiting in one is owed 0x01BA -4 (88:26 'This Arena has
        ended'). Returns the arenas that ended."""
        changed, ended = False, []
        d = game_day(now)
        if self.day != d:
            self.day, self.hosted_today, changed = d, 0, True
        for aid, a in list(self.hosted.items()):
            if a.get("end") and now >= a["end"]:
                del self.hosted[aid]
                ended.append(a)
                changed = True
                for e in self.entries.pop(aid, []):
                    for m in e["members"]:
                        self.entry_of.pop(m, None)
                        self.notify(m, MSG_CANCEL_UPDATE, cancel_update_body(CANCEL_ENDED),
                                    f"arena {aid} {a.get('name')!r} ended")
        if changed:
            self.save()
        return ended

    def arenas(self, now=None):
        now = time.time() if now is None else now
        return ([scheduled(a, now) for a in OFFICIAL]
                + [self.hosted[k] for k in sorted(self.hosted)])

    def find(self, aid, now=None):
        for a in self.arenas(now):
            if int(a["id"]) == int(aid):
                return a
        return None

    def view(self, a, now):
        """`a` as a row is served: a tournament whose battles have started is
        closed to registration (+0x3B)."""
        v = dict(a)
        if (int(a.get("format") or 1) == FORMAT_TOURNAMENT and a.get("start")
                and now >= a["start"]):
            v["closed"] = True
        return v

    def list_arenas(self, mode, now):
        """The arenas a LIST of `mode` shows (0 desks, 1 spectate, 2 board)."""
        self.tick(now)
        if mode == 1:
            # SPECTATE: arenas with a battle under way (someone has sortied)
            live = {m["arena"] for m in self.running_matches()}
            return [self.view(a, now) for a in self.arenas(now)
                    if int(a["id"]) in live][:L_MAX_ROWS]
        return [self.view(a, now) for a in self.arenas(now)][:L_MAX_ROWS]

    def open_slots(self, now):
        self.tick(now)
        return max(0, HOST_CAP - self.hosted_today) if HOST_CAP > 0 else 0

    # -- hosting ----------------------------------------------------------- #
    def host_verdict(self, account, char, req, now, rank=None, mp=None):
        """(code, why). code >= 0 is accepted; the negatives are HOST_*."""
        if HOST_CAP <= 0:
            return HOST_CLOSED, "FMO_COLISEUM_HOSTING=0: hosting is switched off"
        if self.open_slots(now) <= 0:
            return HOST_CLOSED, (f"today's {HOST_CAP} hosting slot(s) are taken "
                                 f"(the day turns at {DAY_HOUR_UTC:02d}:00 UTC)")
        mine = [a for a in self.hosted.values() if a.get("host") == account]
        if mine:
            return HOST_ONE, f"this pilot already hosts arena {mine[0]['id']}"
        if rank is not None and int(rank) < HOST_RANK:
            return HOST_REFUSED, (f"rank byte {rank} is below {HOST_RANK} "
                                  f"(Second Lieutenant)")
        if mp is not None and int(mp) < HOST_MP:
            return HOST_REFUSED, f"MP {mp} is below the {HOST_MP} hosting costs"
        if not (req.get("name") or "").strip():
            return HOST_REFUSED, "the arena has no name (94:27)"
        if not 1 <= int(req.get("headcount") or 0) <= 5:
            return HOST_REFUSED, f"headcount {req.get('headcount')} is not 1..5"
        if int(req.get("format") or 0) not in (FORMAT_NORMAL, FORMAT_TOURNAMENT):
            return HOST_REFUSED, f"format {req.get('format')} is neither 1 nor 2"
        if int(req.get("format")) == FORMAT_TOURNAMENT and int(req.get("req_bgs") or 0) not in (4, 8):
            return HOST_REFUSED, f"a tournament for {req.get('req_bgs')} BGs (4 or 8)"
        maps = arena_maps()
        if maps and int(req.get("tile") or 0) not in maps:
            return HOST_REFUSED, (f"map {req.get('tile')} is not one of SE's "
                                  f"{len(maps)} arena maps")
        return 0, "accepted"

    def host(self, account, char, req, now):
        """File an accepted create. Returns the stored arena."""
        a = dict(req)
        a.update({"id": self.next_id, "kind": "hosted", "host": account,
                  "promoter": pilot_name(char)[:R_PROMOTER_LEN - 1],
                  "name": (req.get("name") or "").strip(),
                  "total_cost": int(req["headcount"]) * int(req.get("bg_cost") or 0),
                  "fee": host_fee(req["format"], req["headcount"], req.get("req_bgs")),
                  "start": int(now) + HOST_LEAD,
                  "end": int(now) + HOST_LEAD + HOST_HOURS * 3600,
                  "created": int(now)})
        lvl = int(req.get("score") or 0)
        if int(req.get("main_rule") or 0) == 1:
            a["score"] = (TARGET_SCORES[lvl] if 0 <= lvl < len(TARGET_SCORES)
                          else TARGET_SCORES[-1])
        else:
            a["score"] = 0
        a.pop("closed", None)
        self.hosted[a["id"]] = a
        self.next_id += 1
        self.hosted_today += 1
        self.save()
        return a

    # -- registration ------------------------------------------------------ #
    def register_verdict(self, a, account, members, leader, now, password=b"",
                         costs=None, money=None):
        """(code, why); code 0 = accepted, else a CODE_*. `members` is the
        group the registration takes along ([account] for a solo pilot),
        `costs` {account: B.G.Cost or None}."""
        if a is None:
            return CODE_GONE, "no arena with that id"
        if a.get("end") and now >= a["end"]:
            return CODE_ENDED, "its time is up"
        if self.view(a, now).get("closed"):
            return CODE_CLOSED, "its tournament has started"
        taken = [m for m in members if m in self.entry_of]
        if account in self.entry_of or taken:
            return CODE_TWICE, (f"{taken or [account]} already registered in arena "
                                f"{self.entry_of.get((taken or [account])[0])}")
        if len(members) > 1 and leader != account:
            return CODE_LEADER, f"the BG leader is {leader}, not {account}"
        if len(members) != int(a.get("headcount") or 1):
            return CODE_HEADCOUNT, (f"{len(members)} pilot(s) in the BG, the arena "
                                    f"wants {a.get('headcount')}")
        cost_cap = int(a.get("bg_cost") or 0)
        if cost_cap and costs:
            over = {m: c for m, c in costs.items() if c is not None and c > cost_cap}
            if over:
                return CODE_COST, f"B.G.Cost over {cost_cap}: {over}"
            total = sum(c for c in costs.values() if c is not None)
            if a.get("total_cost") and total > int(a["total_cost"]):
                return CODE_COST, f"total B.G.Cost {total} over {a['total_cost']}"
        if (int(a.get("format") or 1) == FORMAT_TOURNAMENT
                and len(self.entries.get(int(a["id"]), [])) >= int(a.get("req_bgs") or 0)):
            return CODE_FULL, f"{a.get('req_bgs')} BGs are already registered"
        if a.get("password") and bytes(password).rstrip(b"\0") != str(a["password"]).encode("cp932", "replace"):
            return CODE_PASSWORD, "the password does not match"
        fee = int(a.get("fee") or 0)
        if fee and money is not None and int(money) < fee:
            return CODE_MONEY, f"H$ {money} is below the H$ {fee} entry fee"
        return 0, "accepted"

    def register(self, a, account, members, name, gid, now):
        e = {"arena": int(a["id"]), "leader": account, "members": list(members),
             "name": name, "gid": gid, "at": int(now), "streak": 0, "rounds": 0,
             "state": STATE_IN}
        self.entries.setdefault(int(a["id"]), []).append(e)
        for m in members:
            self.entry_of[m] = int(a["id"])
        return e

    def entry_for(self, account):
        aid = self.entry_of.get(account)
        if aid is None:
            return None
        return next((e for e in self.entries.get(aid, []) if account in e["members"]), None)

    def cancel(self, account):
        """Withdraw the entry `account` is in. Returns it, or None."""
        e = self.entry_for(account)
        if e is None:
            return None
        lst = self.entries.get(e["arena"], [])
        if e in lst:
            lst.remove(e)
        for m in e["members"]:
            self.entry_of.pop(m, None)
        return e

    # -- matches ----------------------------------------------------------- #
    def pair(self, now, online):
        """Start every match that can start: two entries of one open arena
        whose members are all `online` (a callable on an account). A normal
        arena pairs first come, first served; a tournament pairs entries of
        the same round, gives an odd one out a bye (SE 2006-08: "a BG with no
        first-round opponent wins by default") and crowns the last one left.
        Returns the matches started."""
        started = []
        for a in self.arenas(now):
            aid = int(a["id"])
            if (a.get("start") and now < a["start"]) or (a.get("end") and now >= a["end"]):
                continue
            ents = self.entries.get(aid, [])
            if int(a.get("format") or 1) == FORMAT_TOURNAMENT:
                pairs = self._tournament_pairs(a, ents, now)
            else:
                pool = [e for e in ents if not e.get("match")]
                pairs = list(zip(pool[0::2], pool[1::2]))
            for e1, e2 in pairs:
                if not all(online(m) for m in e1["members"] + e2["members"]):
                    continue
                m = self.start_match(a, e1, e2, now)
                if m is not None:
                    started.append(m)
        return started

    def _tournament_pairs(self, a, ents, now):
        live = [e for e in ents if e.get("state") != STATE_OUT]
        if not live:
            return []
        if len(live) == 1 and not live[0].get("match"):
            self.crown(a, live[0], now)
            return []
        low = min(e.get("rounds", 0) for e in live)
        tier = [e for e in live if e.get("rounds", 0) == low]
        pool = [e for e in tier if not e.get("match")]
        if len(pool) % 2 and not any(e.get("match") for e in tier):
            bye = pool.pop()
            bye["rounds"] = bye.get("rounds", 0) + 1
            log(f"coliseum: arena {a['id']} round {low + 1}: {bye['name']!r} has no "
                f"opponent -- a bye")
        return list(zip(pool[0::2], pool[1::2]))

    def crown(self, a, e, now):
        """The tournament's last BG standing: 0x01BA -10 ('You Win' / 88:33)
        to its members, the record kept, and the arena closed."""
        aid = int(a["id"])
        for m in e["members"]:
            self.entry_of.pop(m, None)
            self.notify(m, MSG_CANCEL_UPDATE, cancel_update_body(CANCEL_WON),
                        f"arena {aid} {a.get('name')!r}: tournament won")
        self.note_streak(aid, e["name"], e.get("rounds", 0))
        for x in self.entries.pop(aid, []):
            for m in x["members"]:
                if self.entry_of.get(m) == aid:
                    self.entry_of.pop(m, None)
        if aid in self.hosted:
            self.hosted[aid]["end"] = int(now)
            self.save()
        log(f"coliseum: arena {aid} {a.get('name')!r}: {e['name']!r} WON the tournament")

    def start_match(self, a, e1, e2, now):
        """Pair `e1` against `e2` and queue every member's 0x014E. None when
        the arena's map has no battle map on disk."""
        mapno = arena_mapno(a)
        if mapno is None:
            log(f"coliseum: arena {a['id']} tile {a.get('tile')}: no battle map in "
                f"SE's selector-{ARENA_SELECTOR} table -- not paired")
            return None
        bodies = {}
        for side in (0, 1):
            bodies[side] = sortie_push_body(a, mapno, side)
            if bodies[side] is None:
                log(f"coliseum: arena {a['id']} battle map {mapno} refused by "
                    f"sortiepush (not on disk?) -- not paired")
                return None
        mid = self.next_match
        self.next_match += 1
        aid = int(a["id"])
        m = {"id": mid, "arena": aid, "name": a.get("name"), "mapno": mapno,
             "format": int(a.get("format") or 1), "teams": [e1, e2], "sides": {},
             "pushed": float(now), "go": {}, "verdict": None, "spectators": set(),
             "watch_body": bodies[0]}
        for side, e in enumerate((e1, e2)):
            e["match"] = mid
            for acct in e["members"]:
                m["sides"][acct] = side
                self.match_of[acct] = mid
                self.notify(acct, sortiepush.MSG_SORTIE_PUSH, bodies[side],
                            f"arena {aid} match {mid}: {e1['name']!r} vs "
                            f"{e2['name']!r}, battle map {mapno}, team {side}",
                            meta={"match": mid, "mapno": mapno})
        if m["format"] != FORMAT_TOURNAMENT:
            lst = self.entries.get(aid, [])
            for e in (e1, e2):
                if e in lst:
                    lst.remove(e)
        self.matches[mid] = m
        log(f"coliseum: arena {aid} {a.get('name')!r}: MATCH {mid} {e1['name']!r} "
            f"{e1['members']} vs {e2['name']!r} {e2['members']} on battle map "
            f"{mapno}; 0x014E queued for every member")
        return m

    def judge(self, m, now, online, deaths):
        """(winning team 0/1 or None for a draw, why) once match `m` is
        decided, else None. `deaths` {account: time} (battleend.PILOT_DEATHS)."""
        go_by = m["pushed"] + COUNTDOWN + GO_WAIT
        standing = []
        gathering = False
        for e in m["teams"]:
            n = 0
            for acct in e["members"]:
                died = deaths.get(acct)
                if died is not None and died >= m["pushed"]:
                    continue
                if acct not in m["go"]:
                    if now <= go_by:
                        gathering = True
                        n += 1
                    continue
                if not online(acct):
                    continue
                n += 1
            standing.append(n)
        if standing[0] == 0 and standing[1] == 0:
            return None, "both teams are out"
        if standing[0] == 0 or standing[1] == 0:
            w = 1 if standing[0] == 0 else 0
            return w, (f"team {1 - w} has no pilot left standing "
                       f"({standing[0]} vs {standing[1]})")
        if gathering:
            return False
        began = min(m["go"].values()) if m["go"] else m["pushed"] + COUNTDOWN
        if now >= began + MATCH_TIME:
            if standing[0] == standing[1]:
                return None, f"time limit, {standing[0]} vs {standing[1]} standing: a draw"
            w = 0 if standing[0] > standing[1] else 1
            return w, f"time limit, {standing[0]} vs {standing[1]} standing"
        return False

    def tick_matches(self, now, online, deaths):
        """Judge every running match and settle the decided ones; drop
        winners who never took the next battle. Returns the matches settled."""
        done = []
        for m in list(self.matches.values()):
            if m["verdict"] is not None:
                continue
            v = self.judge(m, now, online, deaths)
            if v is False:
                continue
            self.finish(m, v[0], v[1], now)
            done.append(m)
        for acct, (e, deadline) in list(self.returning.items()):
            if now > deadline:
                self.returning.pop(acct, None)
                if self.entry_of.get(acct) == e["arena"] and e not in self.entries.get(e["arena"], []):
                    self.entry_of.pop(acct, None)
        for mid, m in list(self.matches.items()):
            if m["verdict"] is not None and not any(
                    self.match_of.get(a) == mid for e in m["teams"] for a in e["members"]
            ) and not any(self.spectate_end.get(s) == mid for s in m["spectators"]):
                del self.matches[mid]
        return done

    def running_matches(self):
        """Matches under way: undecided, at least one pilot sortied."""
        return [m for m in self.matches.values() if m["verdict"] is None and m["go"]]

    def spectate(self, account, aid, now):
        """(0, match) or (refusal code, why) for `account` watching arena
        `aid`: a running match there with a free seat, the emptiest first."""
        live = [m for m in self.running_matches() if m["arena"] == int(aid)]
        if not live:
            return SPECTATE_LATE, f"no battle under way in arena {aid}"
        free = [m for m in live if len(m["spectators"]) < SPECTATE_SLOTS]
        if not free:
            return SPECTATE_FULL, f"all {len(live)} battle(s) have {SPECTATE_SLOTS} spectators"
        m = min(free, key=lambda x: len(x["spectators"]))
        self.leave_spectate(account)
        m["spectators"].add(account)
        self.spectators[account] = m["id"]
        return 0, m

    def leave_spectate(self, account):
        mid = self.spectators.pop(account, None)
        self.spectate_end.pop(account, None)
        m = self.matches.get(mid) if mid is not None else None
        if m is not None:
            m["spectators"].discard(account)
        return mid

    def take_spectate_end(self, account):
        mid = self.spectate_end.pop(account, None)
        if mid is not None:
            self.spectators.pop(account, None)
            m = self.matches.get(mid)
            if m is not None:
                m["spectators"].discard(account)
        return mid

    def finish(self, m, winner, why, now):
        """Settle match `m`: every member's verdict for its keepalive, the
        prize to the winning leader, the streak, the bracket."""
        a = self.find(m["arena"])
        m["verdict"] = (winner, why, now)
        aid = m["arena"]
        for sp in m.get("spectators", ()):
            self.spectate_end[sp] = m["id"]
        for t, e in enumerate(m["teams"]):
            won = winner == t
            e["match"] = None
            if won:
                e["streak"] = e.get("streak", 0) + 1
            nxt = bool(won and m["format"] != FORMAT_TOURNAMENT and a is not None
                       and not (a.get("end") and now >= a["end"]))
            prize = prize_per_win(a) if (won and a is not None) else 0
            for acct in e["members"]:
                self.verdicts[acct] = {"match": m["id"], "arena": aid, "won": won,
                                       "why": why, "next": nxt, "team": t,
                                       "prize": prize if acct == e["leader"] else 0,
                                       "streak": e.get("streak", 0)}
            if m["format"] == FORMAT_TOURNAMENT:
                if won:
                    e["rounds"] = e.get("rounds", 0) + 1
                else:
                    e["state"] = STATE_OUT
                    for acct in e["members"]:
                        self.entry_of.pop(acct, None)
                continue
            if won:
                self.note_streak(aid, e["name"], e["streak"])
                for acct in e["members"]:
                    self.returning[acct] = (e, now + RETURN_WAIT)
            else:
                for acct in e["members"]:
                    self.entry_of.pop(acct, None)
        log(f"coliseum: MATCH {m['id']} (arena {aid}) decided: "
            + (f"{m['teams'][winner]['name']!r} WINS" if winner is not None else "a DRAW")
            + f" -- {why}")

    def take_verdict(self, account):
        v = self.verdicts.pop(account, None)
        if v is not None and self.match_of.get(account) == v["match"]:
            self.match_of.pop(account, None)
        return v

    def requeue(self, account, now):
        """0x01BB: the winner takes the streak's next battle. The entry goes
        back in its arena's queue. Returns it, or None."""
        r = self.returning.get(account)
        if r is None:
            return None
        e, _deadline = r
        a = self.find(e["arena"])
        if a is None or (a.get("end") and now >= a["end"]):
            for m in e["members"]:
                self.returning.pop(m, None)
                self.entry_of.pop(m, None)
            return None
        lst = self.entries.setdefault(e["arena"], [])
        if e not in lst:
            lst.append(e)
        for m in e["members"]:
            self.returning.pop(m, None)
            self.entry_of[m] = e["arena"]
        return e

    def side_of(self, account):
        mid = self.match_of.get(account)
        m = self.matches.get(mid) if mid is not None else None
        return m["sides"].get(account) if m else None

    # -- records ----------------------------------------------------------- #
    def note_streak(self, aid, name, streak):
        """Keep `name`'s streak in arena `aid`'s top RECORDS_KEPT."""
        rec = [r for r in self.records.get(str(aid), []) if r[0] != name]
        rec.append([name, int(streak)])
        rec.sort(key=lambda r: -r[1])
        self.records[str(aid)] = rec[:RECORDS_KEPT]
        self.save()

    def board_entries(self, a, account):
        """The 0x01C3 entries for arena `a` as `account` sees it."""
        aid = int(a["id"])
        if int(a.get("format") or 1) == FORMAT_TOURNAMENT:
            out = []
            for e in self.entries.get(aid, []):
                st = STATE_SELF if account in e["members"] else e.get("state", STATE_IN)
                out.append((e["name"], e.get("rounds", 0), st, e.get("streak", 0)))
            return out
        own = self.entry_for(account)
        out = [(own["name"] if own else "", own.get("streak", 0) if own else 0,
                STATE_SELF, own.get("streak", 0) if own else 0)]
        for name, streak in self.records.get(str(aid), []):
            out.append((name, 0, STATE_IN, streak))
        return out


def write_state(data, now=None):
    fdb = _fmodb()
    if fdb is None:
        return False
    try:
        fdb.ready()
        fdb.db.upsert("fmo_coliseum", {"id": 1,
                                       "data": json.dumps(data, sort_keys=True),
                                       "updated_at": float(time.time() if now is None else now)},
                      key="id")
        return True
    except fdb.ERRORS:
        return False


def _fmodb():
    try:
        import fmodb
        return fmodb
    except ImportError:
        return None


_COLISEUM = None


def _account_key(key):
    """A battle key (an account, or an address for an unbound channel) -> the
    account the match tables are keyed by."""
    if not key:
        return None
    if _COLISEUM is not None and (key in _COLISEUM.match_of or key in _COLISEUM.spectators):
        return key
    try:
        return identity.account_for(key) or key
    except Exception:
        return key


def match_key(key):
    """The arena match a battle key's pilot is fighting in, or None. Cheap and
    None when the Coliseum has never been touched in this process."""
    if _COLISEUM is None or not (_COLISEUM.match_of or _COLISEUM.spectators):
        return None
    acct = _account_key(key)
    return _COLISEUM.match_of.get(acct) or _COLISEUM.spectators.get(acct)


def side_for_key(key):
    """The pilot's arena team (0/1) -- the pop's friend/foe byte for an arena
    battle -- or None outside one."""
    if _COLISEUM is None or not _COLISEUM.match_of:
        return None
    return _COLISEUM.side_of(_account_key(key))


def _chan_key(c):
    try:
        return referee.chan_bkey(c)
    except Exception:                    # a partial channel object
        return getattr(c, "account", None)


def match_of_chan(c):
    """match_key for a world channel; None at once when no match exists."""
    if _COLISEUM is None or not (_COLISEUM.match_of or _COLISEUM.spectators):
        return None
    return match_key(_chan_key(c))


def spectator_of_chan(c):
    """The match a world channel's pilot is WATCHING, or None (a fighter, or
    nobody in the Coliseum)."""
    if _COLISEUM is None or not _COLISEUM.spectators:
        return None
    return _COLISEUM.spectators.get(_account_key(_chan_key(c)))


def spectators_of_chan(c):
    """The battle channels watching the match `c`'s pilot fights in."""
    if _COLISEUM is None or not _COLISEUM.spectators:
        return []
    mid = _COLISEUM.match_of.get(_account_key(_chan_key(c)))
    if mid is None:
        return []
    return [o for o in rooms.room_mates(c) if spectator_of_chan(o) == mid]


def spectator_feed(c, other):
    """True when the copy of fighter `c`'s own-unit stream it sent to `other`
    is the one its spectators get: `other` is the first fighter in its room
    (by address), so a three-pilot room does not feed them twice."""
    fighters = sorted((o for o in rooms.room_mates(c) if spectator_of_chan(o) is None),
                      key=lambda o: tuple(o.addr))
    return bool(fighters) and fighters[0] is other


def side_of_chan(c):
    """side_for_key for a world channel; None at once when no match exists."""
    if _COLISEUM is None or not _COLISEUM.match_of:
        return None
    return side_for_key(_chan_key(c))


def coliseum():
    """The one Coliseum for this process."""
    global _COLISEUM
    if _COLISEUM is None:
        _COLISEUM = Coliseum()
        if fmoarena.OFFICIAL_ERR:
            log(f"WARNING: coliseum: {fmoarena.OFFICIAL_ERR} -- NO official arenas")
    return _COLISEUM


# --------------------------------------------------------------------------- #
# THE DESKS, on the session
# --------------------------------------------------------------------------- #
class SessionColiseum:
    """The Coliseum requests, answered from coliseum()."""

    def _col_group(self):
        """(gid, leader, live members) for this pilot; (None, account, [account])
        when it is in no battle group."""
        acct = self.account
        gid = groupchannel.GROUP_OF.get(acct)
        if not gid or acct not in groupchannel.GROUP_MEMBERS.get(gid, []):
            return None, acct, [acct]
        members = [a for a in groupchannel.GROUP_MEMBERS.get(gid, [])
                   if a == acct or groupchannel.group_member_live(a)]
        return gid, battlegroups.group_leader(gid) or acct, members

    def _col_char(self):
        return self.playing_char() if charstore.CHAR_STORE else None

    def on_coliseum(self, p):
        """Answer one Coliseum request. [] = nothing to send."""
        col = coliseum()
        now = time.time()
        msg, body, conn = p["msg"], p["payload"], p["conn"]
        if msg == MSG_LIST_REQ:
            mode = body[0] if body else 0
            arenas = col.list_arenas(mode, now)
            if mode == 1:
                # the 0x01C0 that follows a pick here is a SPECTATE request
                self.col_watch_list_at = now
            slots = col.open_slots(now) if mode == 0 else 0
            log(f"{self.peer}   0x{MSG_LIST_REQ:04X} = COLISEUM LIST, mode {mode} "
                f"({LIST_MODES.get(mode, 'unknown')}) -> 0x{MSG_LIST_REPLY:04X} "
                f"{LIST_LEN}B: {len(arenas)} arena(s)"
                + (": " + ", ".join(f"{a['id']} {a['name']!r}" for a in arenas)
                   if arenas else
                   (" -- the Officer says 88:32 'no Arena being held'" if mode == 0 else
                    " -- Delacroix says 88:46 'no Arena to watch'" if mode == 1 else ""))
                + f"; hosting slots open {slots}"
                + (" (the Coordinator opens the create window)" if mode == 0 and slots
                   else " (the Coordinator says 88:44)" if mode == 0 else ""))
            return [packet.build(MSG_LIST_REPLY,
                                 list_body([arena_row(a) for a in arenas], slots),
                                 self.reply_seq(), conn)]

        if msg == MSG_REGISTER_REQ:
            aid = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0
            pw = bytes(body[4:12]) if len(body) >= 12 else b""
            a = col.find(aid)
            col.tick(now)
            char = self._col_char()
            gid, leader, members = self._col_group()
            costs = {m: battlegroups.account_bg_cost(m) for m in members}
            money = economy.wallet_money(char)[0] if char else None
            code, why = col.register_verdict(a, self.account, members, leader, now,
                                             password=pw, costs=costs, money=money)
            if code:
                log(f"{self.peer}   0x{MSG_REGISTER_REQ:04X} = COLISEUM REGISTER, arena "
                    f"{aid}: REFUSED ({CODE_TEXT.get(code)}: {why}) -> message "
                    f"{charselect.MSG_FAIL} code {code}: the client shows 88:21 "
                    f"'Registration failed.' [FM{code:05d}]")
                return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), code)]
            outs = [packet.build(MSG_REGISTER_REPLY, struct.pack("<I", 1),
                                 self.reply_seq(), conn)]
            fee = int(a.get("fee") or 0)
            if fee:
                now_money = self.credit_money(f"Coliseum entry fee, arena {aid}",
                                              money=-fee)
                if now_money is not None:
                    outs.append(pushes.fee_push_packet(conn, money=now_money[0],
                                                       coliseum_fee=fee))
            name = pilot_name(char)
            e = col.register(a, self.account, members, name, gid, now)
            row = arena_row(col.view(a, now))
            outs.append(packet.build(MSG_ADD_UPDATE, add_update_body(row),
                                     pushes.QUEUE_SEQ, conn))
            told = [m for m in members if m != self.account]
            for m in told:
                col.notify(m, MSG_ADD_UPDATE, add_update_body(row),
                           f"arena {aid}: your BG leader registered")
            log(f"{self.peer}   0x{MSG_REGISTER_REQ:04X} = COLISEUM REGISTER, arena {aid} "
                f"{a['name']!r}: ACCEPTED for {len(members)} pilot(s) {members}"
                + (f" (BG {gid})" if gid else " (solo)")
                + f" -> 0x{MSG_REGISTER_REPLY:04X} + 0x{MSG_ADD_UPDATE:04X} (the waiting "
                f"window)"
                + (f" + 0x{pushes.MSG_FEE_PUSH:04X} entry fee H$ {fee} (8:82)" if fee else "")
                + (f"; 0x{MSG_ADD_UPDATE:04X} queued for {told}" if told else "")
                + f". {len(col.entries.get(int(a['id']), []))} entr(y/ies) in this arena; "
                f"the next keepalive pairs it when an opponent is waiting.")
            return outs

        if msg == MSG_CANCEL_REQ:
            e = col.cancel(self.account)
            if e is None:
                log(f"{self.peer}   0x{MSG_CANCEL_REQ:04X} = COLISEUM CANCEL: this pilot "
                    f"is registered nowhere -> 0x{MSG_CANCEL_REPLY:04X} "
                    f"{CANCEL_REPLY_DONE} (the dialog closes the stale window)")
                return [packet.build(MSG_CANCEL_REPLY, struct.pack("<i", CANCEL_REPLY_DONE),
                                     self.reply_seq(), conn)]
            told = [m for m in e["members"] if m != self.account]
            for m in told:
                col.notify(m, MSG_CANCEL_UPDATE, cancel_update_body(CANCEL_DONE),
                           f"arena {e['arena']}: registration withdrawn")
            log(f"{self.peer}   0x{MSG_CANCEL_REQ:04X} = COLISEUM CANCEL: arena "
                f"{e['arena']} entry {e['name']!r} withdrawn -> 0x{MSG_CANCEL_REPLY:04X} "
                f"{CANCEL_REPLY_DONE} ({CANCEL_TEXT[0]})"
                + (f"; 0x{MSG_CANCEL_UPDATE:04X} 0 queued for {told}" if told else "")
                + ". The entry fee is not refunded.")
            return [packet.build(MSG_CANCEL_REPLY, struct.pack("<i", CANCEL_REPLY_DONE),
                                 self.reply_seq(), conn)]

        if msg == MSG_HOST_REQ:
            req = parse_arena_row(body[:ROW_LEN])
            char = self._col_char()
            rank = mp = None
            if char is not None:
                rank = economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char)[0]
                mp = economy.wallet_mp(char)[0]
            code, why = col.host_verdict(self.account, char, req, now, rank=rank, mp=mp)
            if code < 0:
                log(f"{self.peer}   0x{MSG_HOST_REQ:04X} = COLISEUM HOST {req['name']!r}: "
                    f"REFUSED ({why}) -> 0x{MSG_HOST_REPLY:04X} {code}: {HOST_TEXT[code]}")
                return [packet.build(MSG_HOST_REPLY, struct.pack("<i", code),
                                     self.reply_seq(), conn)]
            if char is not None and HOST_MP:
                _was, _now_mp = economy.spend_mp(char, HOST_MP)
                try:
                    self.commit(f"hosted an arena, MP {_was} -> {_now_mp}")
                except Exception as _e:
                    log(f"{self.peer}   WARNING: the hosting MP NOT banked ({_e!r})")
            a = col.host(self.account, char, req, now)
            log(f"{self.peer}   0x{MSG_HOST_REQ:04X} = COLISEUM HOST: arena {a['id']} "
                f"{a['name']!r} by {a['promoter']}, {'tournament of ' + str(a.get('req_bgs')) + ' BGs' if a['format'] == FORMAT_TOURNAMENT else 'normal'}, "
                f"{a['headcount']} pilot(s) at B.G.Cost {a.get('bg_cost')}, map tile "
                f"{a.get('tile')}, fee H$ {a['fee']}, opens "
                f"{time.strftime('%H:%M', time.gmtime(a['start']))}Z for "
                f"{HOST_HOURS} h; {HOST_MP} MP taken -> 0x{MSG_HOST_REPLY:04X} {a['id']} "
                f"(94:31 'The Arena was created'). Slots left today "
                f"{col.open_slots(now)}.")
            return [packet.build(MSG_HOST_REPLY, struct.pack("<i", a["id"]),
                                 self.reply_seq(), conn)]

        if msg == MSG_BOARD_REQ:
            aid = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0
            a = col.find(aid)
            if a is None:
                own = col.entry_for(self.account)
                a = col.find(own["arena"]) if own else None
            if a is None:
                log(f"{self.peer}   0x{MSG_BOARD_REQ:04X} = COLISEUM BOARD, arena {aid}: "
                    f"none -> 0x{MSG_BOARD_REPLY:04X} status -1 (88:47)")
                return [packet.build(MSG_BOARD_REPLY, board_body(bytes(ROW_LEN), [], -1),
                                     self.reply_seq(), conn)]
            ents = col.board_entries(a, self.account)
            log(f"{self.peer}   0x{MSG_BOARD_REQ:04X} = COLISEUM BOARD, arena {a['id']} "
                f"{a['name']!r}: {len(ents)} entr(y/ies) -> 0x{MSG_BOARD_REPLY:04X} "
                f"{BOARD_LEN}B ("
                + ("the bracket" if int(a.get('format') or 1) == FORMAT_TOURNAMENT
                   else "88:35 the win streak record") + ")")
            return [packet.build(MSG_BOARD_REPLY,
                                 board_body(arena_row(col.view(a, now)), ents),
                                 self.reply_seq(), conn)]

        if msg == MSG_RETURN_REQ:
            # The streak's next battle: a normal-arena winner said yes after
            # the battle (lobby state 0xD, lobby+0x6E42). Its entry goes back
            # in the queue; >= 0 relocks the client into the waiting window.
            e = col.requeue(self.account, now)
            if e is None:
                log(f"{self.peer}   0x{MSG_RETURN_REQ:04X} = COLISEUM RETURN GROUP (the "
                    f"streak's next battle) -> 0x{MSG_RETURN_REPLY:04X} {RETURN_REFUSED}: "
                    f"no winning entry waiting for this pilot (8:83)")
                return [packet.build(MSG_RETURN_REPLY, struct.pack("<i", RETURN_REFUSED),
                                     self.reply_seq(), conn)]
            log(f"{self.peer}   0x{MSG_RETURN_REQ:04X} = COLISEUM RETURN GROUP: "
                f"{e['name']!r} (streak {e.get('streak', 0)}) back in arena "
                f"{e['arena']}'s queue -> 0x{MSG_RETURN_REPLY:04X} {RETURN_OK} "
                f"(ColosseumLockUI, the waiting window)")
            return [packet.build(MSG_RETURN_REPLY, struct.pack("<i", RETURN_OK),
                                 self.reply_seq(), conn)]
        return []

    def in_arena_match(self):
        """True while this pilot's arena match has no verdict delivered."""
        try:
            acct = self.account
        except Exception:
            return False
        return bool(COLISEUM and _COLISEUM is not None
                    and (acct in _COLISEUM.match_of or acct in _COLISEUM.spectators))

    def is_spectating(self):
        try:
            acct = self.account
        except Exception:
            return False
        return bool(COLISEUM and _COLISEUM is not None and acct in _COLISEUM.spectators)

    def wants_spectate(self, p):
        """A 0x01C0 is Delacroix's (an arena id, soon after his list) rather
        than the mission board's."""
        if not COLISEUM or p["msg"] != MSG_SPECTATE_REQ:
            return False
        at = getattr(self, "col_watch_list_at", None)
        return at is not None and time.time() - at <= SPECTATE_LIST_WINDOW

    def on_spectate(self, p):
        """0x01C0 from Delacroix's list: watch arena `id`'s battle."""
        col = coliseum()
        now = time.time()
        self.col_watch_list_at = None
        aid = struct.unpack_from("<I", p["payload"], 0)[0] if len(p["payload"]) >= 4 else 0
        gid = groupchannel.GROUP_OF.get(self.account)
        if gid and self.account in groupchannel.GROUP_MEMBERS.get(gid, []):
            code, why = SPECTATE_IN_GROUP, f"this pilot is in battle group {gid}"
        else:
            code, m = col.spectate(self.account, aid, now)
            why = m if code else None
        if code:
            log(f"{self.peer}   0x{MSG_SPECTATE_REQ:04X} = COLISEUM SPECTATE, arena {aid}: "
                f"REFUSED ({why}) -> message {charselect.MSG_FAIL} code {code}: "
                f"{SPECTATE_TEXT[code]}")
            return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), code & 0xFFFF)]
        outs = []
        if SPECTATE_FEE:
            paid = self.credit_money(f"arena {aid} spectator fee", money=-SPECTATE_FEE)
            if paid is not None:
                outs.append(pushes.fee_push_packet(p["conn"], money=paid[0],
                                                   spectator_fee=SPECTATE_FEE))
        key = self.battle_key()
        rooms.SORTIE_MAP[key] = int(m["mapno"])
        self.sortie_pending = False
        self.sortie_granted_at = now
        self.battle_end_done = False
        self.battle_settlement = None
        st = referee.battle_state(key, reset=True)
        st["mapno"] = int(m["mapno"])
        st["arena_spectate"] = m["id"]
        log(f"{self.peer}   0x{MSG_SPECTATE_REQ:04X} = COLISEUM SPECTATE, arena {aid}: "
            f"match {m['id']} ({m['teams'][0]['name']!r} vs {m['teams'][1]['name']!r}), "
            f"seat {len(m['spectators'])}/{SPECTATE_SLOTS} -> 0x{MSG_SPECTATE_REPLY:04X} "
            f"{SPECTATE_LEN}B: the match's endpoint, block (battle map {m['mapno']}) and "
            f"seed; the client enters scene 4 without a battle map of its own "
            f"(0x611B0380 -> 0x61006270)"
            + (f"; spectator fee H$ {SPECTATE_FEE} (8:84)" if SPECTATE_FEE else ""))
        _sb, _h = endpoint_for_peer(spectate_body(m["watch_body"]), SP_ENDPOINT, self.ip)
        log(f"{self.peer}   spectate endpoint host {_h} (addressing.host_for this client)")
        outs.insert(0, packet.build(MSG_SPECTATE_REPLY, _sb, self.reply_seq(), p["conn"]))
        return outs

    def arena_sortie_go(self, p, ar):
        """0x014D for an arena match (the countdown of OUR 0x014E ran out):
        the battle bookkeeping the 0x013A grant does, then message 1 = GO."""
        self.arena_sortie = None
        col = coliseum()
        m = col.matches.get(ar["match"])
        if m is None or m["verdict"] is not None or self.account not in m["sides"]:
            log(f"{self.peer}   0x{sortiepush.MSG_SORTIE_GO:04X} = SORTIE GO for arena "
                f"match {ar['match']}: the match is gone or decided -> message "
                f"{charselect.MSG_FAIL} (8:29 'Battle map login failed')")
            return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), charselect.FAIL_CODE)]
        now = time.time()
        key = self.battle_key()
        rooms.SORTIE_MAP[key] = int(ar["mapno"])
        self.sortie_pending = False
        self.sortie_granted_at = now
        self.battle_end_done = False
        self.battle_settlement = None
        st = referee.battle_state(key, reset=True)
        st["mapno"] = int(ar["mapno"])
        st["arena_match"] = m["id"]
        m["go"][self.account] = now
        log(f"{self.peer}   0x{sortiepush.MSG_SORTIE_GO:04X} = SORTIE GO for arena "
            f"match {m['id']} ({m['name']!r}), team {m['sides'][self.account]}, "
            f"battle map {ar['mapno']} -> message 1 = GO (scene 4). "
            f"{len(m['go'])}/{len(m['sides'])} pilot(s) in.")
        return [packet.build(handshake.MSG_SESSION_START, b"", self.reply_seq(), p["conn"])]

    def arena_end_due(self, conn_id):
        """On the keepalive: this pilot's arena verdict, once -- the battle's
        0x014C (with +0x8D for a normal-arena winner, the streak prompt), the
        0x015A when result pushes pay, and the prize to the winning leader."""
        if not COLISEUM or _COLISEUM is None or not self.account:
            return []
        watched = _COLISEUM.take_spectate_end(self.account)
        if watched is not None:
            # a spectator's battle ends with its match: a bare 0x014C (no
            # verdict for it, no pay); its lobby+0x30 is 0, so the arm takes
            # the "battle ended while in the lobby map" path (lobby+0x3C)
            self.battle_end_done = True
            _bst = referee.BATTLE_STATE.get(self.battle_key())
            if _bst is not None:
                _bst["ended"] = True
            _hc = self.playing_char() if charstore.CHAR_STORE else None
            _hr = hangar.hangar_rank_at_battle_end(_hc)[0] if _hc is not None else 0
            log(f"{self.peer}   COLISEUM: the match this pilot watched ({watched}) is "
                f"over -> 0x{battleend.MSG_BATTLE_END:04X} with no result and no pay")
            return [battleend.battle_end_packet(conn_id, won=False, hangar_rank=_hr)]
        v = _COLISEUM.take_verdict(self.account)
        if v is None:
            return []
        outs = []
        why = f"arena match {v['match']}: {v['why']}"
        self.battle_end_done = True
        _bst = referee.BATTLE_STATE.get(self.battle_key())
        if _bst is not None:
            _bst["ended"] = True
        _rp = self.battle_result_push(conn_id, why, won=v["won"])
        if _rp:
            outs.append(_rp)
        _be = self.battle_end_push(conn_id, why=why, won=v["won"], next_battle=v["next"])
        if _be:
            outs.append(_be)
        if v["prize"]:
            now_money = self.credit_money(f"arena {v['arena']} prize", money=v["prize"])
            if now_money is not None:
                outs.append(pushes.fee_push_packet(conn_id, money=now_money[0]))
        log(f"{self.peer}   COLISEUM: arena match {v['match']} -> "
            f"{'WON' if v['won'] else 'LOST'} ({v['why']})"
            + (f", streak {v['streak']}, +0x8D set: the client offers the next "
               f"battle (0x01BB within {RETURN_WAIT}s)" if v["next"] else "")
            + (f", prize H$ {v['prize']} to the leader" if v["prize"] else ""))
        return outs

    def coliseum_pushes_due(self, conn_id, now=None):
        """On the keepalive: what the Coliseum owes this pilot -- the waiting
        window their leader opened (0x01B9), a withdrawn entry (0x01BA 0), an
        arena that ended (0x01BA -4)."""
        if not COLISEUM or not self.account:
            return []
        col = coliseum()
        now = time.time() if now is None else now
        for a in col.tick(now):
            log(f"{self.peer}   COLISEUM: arena {a['id']} {a['name']!r} ended; its "
                f"waiting pilots are owed 0x{MSG_CANCEL_UPDATE:04X} {CANCEL_ENDED} "
                f"({CANCEL_TEXT[CANCEL_ENDED]})")
        _online = lambda a: trade.session_for_account(a) is not None
        col.pair(now, _online)
        col.tick_matches(now, _online, battleend.PILOT_DEATHS)
        out = []
        for mid, body, what, meta in col.take_notices(self.account):
            if mid == sortiepush.MSG_SORTIE_PUSH:
                # the battle endpoint is per client (endpoint_for_peer)
                body, _h = endpoint_for_peer(body, sortiepush.R14E_ENDPOINT, self.ip)
                what += f", endpoint host {_h}"
            if mid == sortiepush.MSG_SORTIE_PUSH and meta:
                # the 0x014D this push's countdown sends is granted for it
                self.arena_sortie = dict(meta)
            out.append(packet.build(mid, body, pushes.QUEUE_SEQ, conn_id))
            log(f"{self.peer}   -> 0x{mid:04X} COLISEUM push, {len(body)}B on queue seq "
                f"0x{pushes.QUEUE_SEQ:08X}: {what}"
                + (f" ({CANCEL_TEXT.get(struct.unpack_from('<i', body, 0)[0], '?')})"
                   if mid == MSG_CANCEL_UPDATE else ""))
        return out


# Called at run time only; imported last so that import cycles resolve.
from .deps import fmosectors  # noqa: E402
from . import (  # noqa: E402
    addressing, battleend, battlegroups, charselect, charstore, economy, groupchannel,
    handshake, hangar, identity, missionblock, packet, pushes, referee, rooms, sortie,
    sortiepush, status, trade,
)
