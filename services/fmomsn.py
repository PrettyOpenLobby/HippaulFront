#!/usr/bin/env python3
"""fmomsn.py -- FRONT MISSION ONLINE's SECOND SERVER: the community/mission
service SE's own source calls "Fshira".

THIS IS A WHOLE SERVER WE HAVE NEVER ANSWERED. Everything the game's Mission
Menu shows -- the "All Mission List" the war map opens onto -- comes from here,
NOT from the lobby's 0x018D/0x018E. Serving 0x018E can never fill that list.
The two are different UIs over different tables:

    View A  "All Mission List" (0x613C1600, ctor 0x611CCEF0, columns
            21:10..21:15 Type|Name|MP|H$|Rank|Fee)  <- THIS FILE.
            Rows are 536-byte records streamed by THIS service. Empty ->
            systext 21:16 "No Missions Found" (0x611CB9D0).
    View B  the board's own 33 x 692-byte table at [0x613C15FC], filled by
            the lobby's 0x018D -> 0x018E.  (fmo.py's reply_018e)

HOW THE CLIENT GETS HERE (static, every link checked in fmodis.py):

  * The 20-byte endpoint at ctx+0x769C is where it dials. TWO writers, both
    ours: the 0x0322 credentials reply's payload +0x28 (0x61179D22) and a
    server push 0x0184 (0x61179F5F). fmo.py already fills BOTH 0x0322
    endpoints with FMO_NEXT_HOST:FMO_NEXT_PORT -- so the client has been
    dialling our own lobby port for this service since the endpoint work
    landed, and we have been dropping it.
  * The list refresh 0x611CC040 queues a job on manager [0x613C1510]; the job
    start 0x611AEC40 copies ctx+0x769C into a fresh connection object
    (0x3311 B, ctor 0x611D0Bxx) and sends the 40-byte HELLO below.

  MEASURED LIVE 2026-09-06: prod's fmo.log has EIGHT of these connections
  (13:27:13Z port 54655 held 900 s and closed), each logged as
  `msg=0xA0C5 ... checksum MISMATCH / no handler`. The 40 bytes were captured
  to logs/captures/fmo-*.bin. HELLO_CAPTURE below is one of them verbatim, and
  it is what --selftest checks against.

THE FRAMING (0x611D1060 send, 0x611D0FC0 receive, 0x611D1167 reader):

    +0x00  u32   TOTAL length, including these 4 bytes.  PLAINTEXT.
    +0x04  u32   tag; the client sends 1. Never read on the way in.
    +0x08  16 B  MD5 of frame[0x18:len]      (0x61071760 / 0x610717D0)
    +0x18  payload:
             +0x00 u32  payload length (= total - 0x18)
             +0x04 u32  OP  -- the dispatch key (0x611D0FFB reads frame+0x1C)
             +0x08 ...  body

    Everything from +0x04 is Blowfish, key "903094117gekisen" (0x611D0B41,
    schedule 0x6106F4E0), the SAME amputated-F Blowfish as the UDP world
    channel -- fmoworld.py owns the cipher and this module reuses it.
    n>>3 WHOLE blocks only: a frame whose (total-4) is not a multiple of 8
    leaves its last bytes in CLEAR. The 40-byte hello does exactly that, which
    is why its trailing `01 00 00 00` is readable in the raw capture -- that
    is not a decode error, it is the protocol.

  RECEIVE BOUND: the client reads into conn+0x1054 and the next field is at
    conn+0x2054, so its receive buffer is 0x1000 = 4096 BYTES and the read
    loop (0x611D11E0) has no cap. A longer frame overruns it -- the same class
    of bug as the 15,000-byte lobby RX buffer that killed the client on
    0x018E. build() refuses anything larger. 7 mission records fit per frame.

THE CONVERSATION (client -> server -> client), all static:

    ->  op 4    HELLO, 40 B, body (u8 2, u32 1). Sent by the job starter.
    <-  op 0x12 "go ahead". Arm 0x611AF897 switches on the job's kind
                ([mgr+0x37D] = job[0]) and sends the job's own frame:
                kind 0->op 6, 1->7, 2->8, 3->9, 4->0xD, 5->0xE, 6->0xF, 7->0x10.
    ->  op 9    THE MISSION-LIST QUERY, 60 B. Job kind 3, built by
                0x611AF550 / 0x611AF5E0:
                  payload +0x0C  submode (1, 2 or 4)
                          +0x10  max rows, always 100
                          +0x14  nation      (lobby+0x8B4)
                          +0x18  MapKind     (globals+0x1A4)
                          +0x1C  category << 24
                          +0x20  caller arg
    <-  op 0x1D one PAGE: payload +0x0C = count, +0x10 = count x 536-byte
                records. Arm 0x611AFBA1 calls the job callback once per record.
    <-  op 0x1B (or 0x1E) END: callback(NULL), job popped. Arm 0x611AFC14.

    Sibling arms, same shape, other record sizes: 0x1A = 0x3C-byte rows,
    0x1F = 0x4C-byte rows (callback [mgr+0x391]). Registered ops are
    0x12,0x14,0x15,0x17,0x18,0x1A,0x1B,0x1D,0x1E,0x1F,0x20..0x26 (0x611AFF10).

THE 536-BYTE MISSION RECORD -- what is PROVED, and nothing else:

    +0x004  char[]  NAME. The list's Name column sorts it with a byte-wise
                    strcmp (0x611C6A54), so it is a NUL-terminated string.
    +0x1C8  u32     packed. byte 3 (>>24, 0x611F0350) is the CATEGORY the view
                    filters on (0x611C98E1: keep if it equals the view's mode,
                    or the mode is 3 or 4). byte 2 (>>16, 0x611F0360) is the
                    TYPE column's value.
    +0x000  u32     VERIFIED: THE MISSION ID -- the dword an ACCEPT carries back.
                    0x611C98A0 stores it at wrapper+0x08 (via 0x611A0F60) and
                    0x611C95EA reads exactly that into the 0x018A body's +0x00.
                    LIVE 2026-09-12: a row served as id 7 came back as
                    `0x018A = ACCEPT MISSION, id 7 -- our row 'Recon Alpha'`.
                    WARNING: NOT a presence flag -- that is View B's rule, not this
                    table's. See MISSION_ID below.
    +0x1DC  u32     VERIFIED: FEE -- the ACCEPTANCE FEE, a COST (21:15, comparator
                    arm 5). LIVE 2026-09-12: 500 here showed as the fee to get
                    in. The gate charges it (FMO_MISSION_FEE).
    +0x1E0  u32     packed like +0x1C8; the second view's Type column
    +0x1E4  u32     VERIFIED: RANK -- the REQUIRED rank (21:14), an INDEX into the
                    rank table, not a number: the renderer does `imul 0x7C` off
                    [0x613CA3E8]+0x10 (comparator arm 4). LIVE 2026-09-12: 25
                    showed as "restricted to Major General".
    +0x1E8  u32     VERIFIED: REWARD MP (the 21:12 "MP" column, shared tail arm)
    +0x1EC  u32     VERIFIED: REWARD H$ (the 21:13 "H$" column, comparator arm 3)
                    WARNING: THESE TWO ARE WHAT THE MISSION PAYS, NOT WHAT IT COSTS.
                    Live 2026-09-12 they drew "Reward: MP30/H$1200" (22:8).
                    The gate read them as requirements for one build because
                    the column headers say "MP" and "H$" -- a header is a label,
                    not a meaning. The fields behind 22:5 "Required MP" and
                    22:6 "Required members" are still UNBOUND.
    +0x1F0  u32     a numeric column -- the SECOND layout's, not this one's
    +0x1F4  u32     TARGET SECTOR, as the ARE tile id row*1000+col --
                    0x611C4A70 decomposes it exactly that way to light the
                    sector on the war map (gated on MapKind > 500).
    +0x1F8  u32     a numeric column -- the second layout's

    VERIFIED: THE SIX COLUMNS ARE BOUND (static 2026-09-12, and no longer a guess).
    The row renderer 0x611CBAB6 -- the `[view+0x247] == 1` layout -- draws six
    cells in header order: Type +0x1CA, Name +0x04, MP +0x1E8, H$ +0x1EC,
    Rank +0x1E4, Fee +0x1DC. +0x1E4 proves itself (the rank-table indexing
    lands on the column 21:14 "Rank" names, which pins the order), the sort
    comparators agree, and live 09-08 "Rank 0 renders as Conscript" matches.
    The `!= 1` layout (0x611CBC17) reads [0x613C15FC]+8 -- View B's table -- so
    one display mode JOINS both tables.

    NEVER: DO NOT USE `mark` TO FIND THEM. This docstring used to prescribe exactly
    that; it is a CLIENT-KILLER (three crashes in four minutes, 2026-09-08 --
    it fills every dword with ids the client follows) and it is now pointless.
    `mark` survives only as the reproduce switch.

VERIFIED: LIVE 2026-09-12: a served row was ACCEPTED and the accept was refused with
code 2, drawing SE's own 27:3 "Your rank is not high enough to accept this
mission." So the list, the row identity and the refusal channel are all proved
on a real client. What is still unproved here is every reply shape this file
invents beyond those; --selftest proves the codec, not the semantics.
"""
import argparse
import hashlib
import struct
import sys

import fmoworld

#: 0x611D0B41, `push 0x10 / push "903094117gekisen"` into the schedule.
KEY = b"903094117gekisen"

HDR = 0x18                 #: length + tag + MD5
PAYLOAD_HDR = 0x08         #: payload length + op
MD5_OFF = 0x08
#: conn+0x1054..conn+0x2054 -- the client's receive buffer, and the read loop
#: does not bound itself. See the module docstring.
CLIENT_RX = 0x1000
RECORD_LEN = 0x218         #: 536; 0x611C9933 `push 0x218`, 0x611AFBEB `add ebx,0x218`

OP_HELLO = 0x04
OP_LIST = 0x09
OP_GO = 0x12
OP_END = 0x1B
OP_PAGE = 0x1D

# --------------------------------------------------------------------------- #
# op 0x17 -- THE SCRAMBLE BOARD'S BATTLE-GROUP LIST (static 2026-09-09)
# --------------------------------------------------------------------------- #
#: KEY: This is why a group you create never appears on the board: the board's
#: list is a job on THIS service, not anything the lobby serves, and we have
#: never answered it. `BATTLE_GROUPS_MADE` in fmo.py was only ever a local
#: record -- its own comment says "not yet a served group: the board list is
#: next". This is that.
#:
#: THE CHAIN, every link read: the board's row builder calls the job ctor
#: `0x611AF380` (`[job+0x00] = 1` = kind 1, callback `0x61180DF0`) with the
#: player's MapKind (`globals+0x1A4`) and nation (`lobby+0x8B4`), so the query
#: is per zone + faction. Kind 1's page op is **0x17** (`0x611AF780`'s table at
#: `0x611AFE9C`, entry for 0x17 -> arm `0x611AFAC1`), and that arm reads a
#: **BYTE** count at payload+0x0C -- not the u32 op 0x1D uses -- then walks
#: records from payload+0x10 at a stride of **0x134**.
#:
#: WARNING: THE STRIDE IS 308, NOT THE 221 THE JOB CTOR STORES. `[job+0x04] = 0xDD` is
#: NOT the record size (fmo.py's community-op note called it "0xDD B" and that
#: is wrong): the walker's `add ebx, 0x134` and the callback's own
#: `rep movsd 0x4D` both say 308, and the field offsets below run to +0x130,
#: which 221 could not hold.
GROUP_RECORD_LEN = 0x134   #: 308; 0x611AFB0C `add ebx,0x134`, callback rep movsd 0x4D
OP_GROUPS = 0x17           #: arm 0x611AFAC1 -- byte count at +0x0C, records at +0x10

#: THE RECORD, from the board's own sort comparator (`0x61180370`, one arm per
#: column through the table at `0x611804A8`) matched against SE's column
#: headers (systext 9:22..9:29) and their tooltips (9:11..9:18):
#:
#:   +0x000 u32  GroupID   -- `0x61183A4A` puts it in board+0x19A, which is
#:                            exactly what a JOIN (0x0157) then sends. Without
#:                            it a row cannot be joined.
#:   +0x00C str  Comment          (col 7; also string-copied to board+0x1A4)
#:   +0x0C0 str  Creator name     (col 5, "Name")
#:   +0x104 u32  State            (col 0, "S"; 9:11 sortied/distributing/standby)
#:   +0x110 u32  Members          (col 2, "Mbr"; 9:13 says logged-in/total)
#:   +0x118 u8   Sorties left     (col 1, "B"; 9:12 before auto-disband)
#:   +0x11C u32  Total B.G.Cost AT THE SORTIE   } col 3 "T" picks between these
#:   +0x120 u32  Total B.G.Cost NOW             } on STATE BIT 0
#:   +0x124 u32  Required B.G.Cost (col 4, "R")
#:   +0x130 u32  Platoon bonus     (col 6, "BG(H$)")
#:
#: KEY: The col-3 pair is the check that the whole map is right, and it was not
#: put there by us: the comparator picks +0x11C when state bit 0 is set and
#: +0x120 otherwise, and SE's tooltip 9:14 says "when the text is dimmed it
#: shows the Total B.G.Cost AT THE TIME OF THE SORTIE". A state bit meaning
#: "sortied" selecting an at-sortie cost is two independent sources agreeing.
G_ID, G_COMMENT, G_NAME = 0x000, 0x00C, 0x0C0
G_STATE, G_MEMBERS, G_SORTIES = 0x104, 0x110, 0x118
G_COST_AT_SORTIE, G_COST_NOW, G_COST_REQ = 0x11C, 0x120, 0x124
G_BONUS = 0x130
G_COMMENT_MAX = G_NAME - G_COMMENT - 1      #: 179 + the NUL
G_NAME_MAX = G_STATE - G_NAME - 1           #: 67 + the NUL
#: State bit 0 = SORTIED; it is the bit col 3 switches on.
G_STATE_SORTIED = 0x1


def group_record(group_id, name="", comment="", state=0, members=1,
                 sorties=0, cost_now=0, cost_at_sortie=0, cost_required=0,
                 bonus=0, mark=False):
    """One 308-byte Scramble Board row. Every byte we do not author is zero.

    `mark=True` fills every dword with its OWN OFFSET first, so each number on
    screen names the byte it came from -- the same trick `mission_record` uses,
    and the fastest way to finish this record without guessing. It is what
    answers the three things one live run left open: which field the client
    reads as "this group is on a sortie", where the member list for the detail
    view comes from, and what marks a group as YOURS so it offers Disband
    rather than Join. The authored fields below still overwrite their own
    offsets, so a marked row is still a JOINABLE row with a real GroupID.
    """
    r = bytearray(GROUP_RECORD_LEN)
    if mark:
        for off in range(0, GROUP_RECORD_LEN - 3, 4):
            struct.pack_into("<I", r, off, off)
    struct.pack_into("<I", r, G_ID, group_id & 0xFFFFFFFF)
    for off, text, cap in ((G_COMMENT, comment, G_COMMENT_MAX),
                           (G_NAME, name, G_NAME_MAX)):
        s = (text or "").encode("cp932", "replace")[:cap]
        r[off:off + len(s)] = s
    if not mark:
        # WARNING: In mark mode these are deliberately NOT authored: writing a value
        # over a marker is exactly what would stop the field naming itself,
        # and every one of them is a field we are trying to READ. The GroupID
        # and the two strings above are still authored, because the row has to
        # stay identifiable and joinable to be worth looking at.
        struct.pack_into("<I", r, G_STATE, state & 0xFFFFFFFF)
        struct.pack_into("<I", r, G_MEMBERS, members & 0xFFFFFFFF)
        r[G_SORTIES] = sorties & 0xFF
        struct.pack_into("<I", r, G_COST_AT_SORTIE, cost_at_sortie & 0xFFFFFFFF)
        struct.pack_into("<I", r, G_COST_NOW, cost_now & 0xFFFFFFFF)
        struct.pack_into("<I", r, G_COST_REQ, cost_required & 0xFFFFFFFF)
        struct.pack_into("<I", r, G_BONUS, bonus & 0xFFFFFFFF)
    return bytes(r)


def group_page(records):
    """op 0x17 -- one page of Scramble Board rows.

    WARNING: The count is a BYTE at payload+0x0C (`movzx eax, byte [esi+0xC]`), unlike
    op 0x1D's u32. It is written as a little-endian u32 so the low byte lands
    where the arm reads it and the three bytes above it stay zero -- which is
    the same thing op 0x14's byte count does. More than 255 rows cannot be
    expressed and are refused rather than silently wrapping to a short list."""
    if len(records) > 0xFF:
        raise ValueError(
            "%d rows: op 0x%02X's count at payload+0x0C is a BYTE "
            "(0x611AFB04), so this would wrap" % (len(records), OP_GROUPS))
    body = struct.pack("<II", 0, len(records)) + b"".join(records)
    return build(OP_GROUPS, body)


def max_group_records_per_page():
    """How many 308-byte rows fit under the client's 4,096-byte buffer."""
    return (CLIENT_RX - HDR - PAYLOAD_HDR - 8) // GROUP_RECORD_LEN

#: One of prod's own captures, 2026-09-06T13:34:00Z, port 54655
#: (logs/captures/fmo-20260906T133400Z.bin). Kept here so the codec has a
#: fixture that came off a real client rather than out of this file.
HELLO_CAPTURE = bytes.fromhex(
    "280000004693c5a0ccfd796d5764b5a772e3fb49b2aa16ab"
    "b6b01aa9cc802213c5f78bf501000000")

#: WARNING: THE OP-6 BODY THAT PRECEDED A CLIENT DEATH, captured live
#: 2026-09-06T17:45:57Z. Talking to `tag_search` opens the war map in MODE 0,
#: which immediately queues a KIND-0 job -> op 6. fmo.py answered 0x1B (END)
#: and pol.exe was gone ~1 s later. Kept as a fixture so the arm that must
#: NOT answer it can be pinned, and so the body can be decoded offline.
#: Readable so far: +0x04 = 0x88 (136), +0x08 = 0x14 (20), +0x40 = 100,
#: +0x50 = 1, +0x60 = 100 -- two counts of 100 and a size, i.e. the same
#: "cap 100" shape the op-9 list query has.
OP6_BODY_LIVE = bytes.fromhex(
    "00000000" "88000000" "14000000" "00000000"
    "00000000" "00000000" "00000000" "00000000"
    "00000000" "00000000" "00000000" "00000000"
    "00000000" "00000000" "00000000" "00000000"
    "64000000" "00000000" "00000000" "00000000"
    "01000000" "00000000" "00000000" "00000000"
    "64000000" "00000000" "00000000" "00000000"
    "00000000")

_tables = None


def _bf():
    """The schedule is expensive and the key never changes, so do it once."""
    global _tables
    if _tables is None:
        _tables = fmoworld.bf_init(KEY)
    return _tables


def build(op, body=b"", tag=1):
    """One frame, ready for the wire.

    Refuses anything over the client's 4,096-byte receive buffer instead of
    corrupting it -- the lobby channel already cost us two client deaths by
    overrunning the equivalent buffer there."""
    payload = struct.pack("<II", PAYLOAD_HDR + len(body), op) + body
    total = HDR + len(payload)
    if total > CLIENT_RX:
        raise ValueError(
            "frame is %d B; the client's receive buffer at conn+0x1054 is only "
            "%d B and its read loop does not bound itself, so this would smash "
            "it (op 0x%X, %d B of body)" % (total, CLIENT_RX, op, len(body)))
    frame = bytearray(struct.pack("<II", total, tag) + b"\0" * 16 + payload)
    frame[MD5_OFF:MD5_OFF + 16] = hashlib.md5(bytes(frame[HDR:])).digest()
    P, S = _bf()
    return bytes(frame[:4]) + fmoworld.crypt(P, S, bytes(frame[4:]),
                                             decrypt=False)


def parse(frame):
    """(op, body) for a frame that verifies, or None. Returns None exactly
    where the client would refuse it (0x611D0FF6 `cmp eax,1 / jne`), so what
    this accepts is what the game accepts."""
    if len(frame) < HDR + PAYLOAD_HDR:
        return None
    total = struct.unpack_from("<I", frame, 0)[0]
    if total != len(frame):
        return None
    P, S = _bf()
    plain = bytes(frame[:4]) + fmoworld.crypt(P, S, bytes(frame[4:]),
                                              decrypt=True)
    if hashlib.md5(plain[HDR:]).digest() != plain[MD5_OFF:MD5_OFF + 16]:
        return None
    plen, op = struct.unpack_from("<II", plain, HDR)
    if plen < PAYLOAD_HDR or HDR + plen > len(plain):
        return None
    return op, plain[HDR + PAYLOAD_HDR:HDR + plen]


def looks_like_frame(head):
    """Cheap first sieve for a connection that has not identified itself: is
    this plausibly one of our frames rather than a POL packet? The real
    decision is parse()'s MD5, which cannot be faked by accident."""
    if len(head) < 4:
        return None
    total = struct.unpack_from("<I", head, 0)[0]
    return total if HDR + PAYLOAD_HDR <= total <= CLIENT_RX else None


#: The op-9 bodies of the first two real client queries ever seen
#: (2026-09-06T17:01:39Z and :41Z, prod). Two back-to-back refreshes, category
#: 1 then 2 -- which is what proved the category lives in the LAST dword.
LIST_QUERY_LIVE_1 = bytes.fromhex(
    "00000000" "02000000" "64000000" "01000000" "64000000" "00000000" "00000001")
LIST_QUERY_LIVE_2 = bytes.fromhex(
    "00000000" "02000000" "64000000" "01000000" "64000000" "00000000" "00000002")


class ListQuery(object):
    """The op-9 body, named. Field meanings are 0x611AF550 / 0x611AF5E0.

    WARNING: THE CATEGORY IS THE LAST DWORD, NOT THE ONE BEFORE IT. The first read of
    0x611AF550 mapped the builder's arguments a slot short, so `category` came
    out of payload+0x1C (the caller's own arg) instead of payload+0x20. It was
    caught by the FIRST LIVE CLIENT (2026-09-06T17:01:39Z / :41Z), whose two
    back-to-back refreshes ended `... 00 00 00 00 | 00 00 00 01` and
    `... 00 00 00 00 | 00 00 00 02` -- a category stepping 1, 2 in the slot the
    decode called `arg`, and 0 in the slot it called `category`.

    Re-read and confirmed at BOTH call sites: `0x611CC0A2 push 0x3000000` is
    ARG4, not arg3, so the builder puts `category << 24` at job+0x3C ->
    frame+0x38 -> payload+0x20.

    WARNING: This mattered. mission_record() echoes `category` into the record's
    +0x1C8 byte 3, and the view DROPS every record whose byte does not match
    (0x611C98E1) -- so serving rows under the wrong category would have drawn
    an empty list that looked exactly like serving no rows at all. A decode
    that selftests clean against a body this file invented is not a decode."""

    __slots__ = ("submode", "max_rows", "nation", "mapkind", "category", "arg")

    def __init__(self, body):
        f = list(struct.unpack_from("<7I", body.ljust(28, b"\0"), 0))
        # f[0] is the dword the client never initialises (job+0x24).
        self.submode = f[1]
        self.max_rows = f[2]
        self.nation = f[3]
        self.mapkind = f[4]
        self.arg = f[5]                       # payload+0x1C, the caller's own
        self.category = (f[6] >> 24) & 0xFF   # payload+0x20, arg4 = mode << 24

    def __str__(self):
        return ("submode=%d max=%d nation=%d MapKind=%d category=%d arg=0x%X"
                % (self.submode, self.max_rows, self.nation, self.mapkind,
                   self.category, self.arg))


#: KEY: THE ROW'S IDENTITY. `record+0x00` is the dword the client sends back when
#: the player ACCEPTS the row -- static 2026-09-12, read off the client's accept path:
#: the callback 0x611C98A0 stores it at `wrapper+0x08` (via 0x611A0F60) and the
#: accept path 0x611C95EA reads exactly that into the 0x018A body's +0x00.
#: WARNING: NOT a presence flag. `record+0x00 != 0` is the row test on the OTHER
#: mission table (the 692-byte 0x018E records); here +0x00 is the mission ID,
#: and serving it as zero -- which every row did until this knob existed --
#: means every accept arrives keyed to nothing.
MISSION_ID = 0x000

#: The six columns, bound statically off the row renderer 0x611CBAB6 (the
#: `[view+0x247] == 1` layout, six cells drawn in header order). `+0x1E4` proves
#: itself: it is indexed into the RANK TABLE at stride 0x7C off
#: `[0x613CA3E8]+0x10` and lands on the column the header 21:14 "Rank" names,
#: which pins the rest of the order; the sort comparators agree (arm 3/4/5 =
#: 0x1EC/0x1E4/0x1DC), and live 09-08 "Rank 0 renders as Conscript" matches.
#: Named here so FMO_MSN_FIELDS pokes stop being blind.
#: VERIFIED: BOTH LIVE-CONFIRMED 2026-09-12 on the Mission Info panel: serving 25
#: and 500 drew "restricted to Major General" and a fee of 500 (that line is
#: CLIPPED on screen -- an overlay-width problem, not a wrong value). So unlike
#: the MP/H$ columns below, these two really are the REQUIREMENT and the COST.
MISSION_FEE = 0x1DC        #: 21:15 Fee   "%u"  = the acceptance fee (a COST)
MISSION_RANK = 0x1E4       #: 21:14 Rank  "%s"  = the REQUIRED rank, an INDEX
                           #:                     into the rank table
#: VERIFIED: LIVE-NAMED 2026-09-12 by the Mission Info panel: serving 30 and 1200 here
#: drew **"Reward: MP30/H$1200"** (22:8, format "%s:MP%d/H$%d" at 0x61342810).
#: So the list's MP and H$ columns show what the mission PAYS, not what it
#: costs -- WARNING: these are NOT requirements, and anything gating on them is
#: gating on the reward. The fields behind 22:5 "Required MP" and 22:6
#: "Required members" are still UNBOUND.
MISSION_REWARD_MP = 0x1E8  #: 21:12 MP  "%u"  = Reward MP
MISSION_REWARD_HS = 0x1EC  #: 21:13 H$  "%u"  = Reward H$
MISSION_MP = MISSION_REWARD_MP   #: back-compat alias; prefer the REWARD names
MISSION_HS = MISSION_REWARD_HS
MISSION_SECTOR = 0x1F4     #: the ARE tile id, row*1000+col (0x611C4A70)
#: VERIFIED: LIVE 2026-09-12: bytes 0..2 of +0x1C8 are the **Distribution MP** the
#: panel prints (22:9) -- the screen read `Distribution: MP33554432/H$0` for a
#: category-2 row, and 33,554,432 is 0x02000000, our category byte in the high
#: byte. Authoring a real distribution means writing bytes 0..2 only.
MISSION_DISTRIBUTION = 0x1C8

#: VERIFIED:KEY: THE THREE LISTS, NAMED LIVE 2026-09-12. The board's refresh
#: `0x611CC040` sends THE VIEW'S OWN MODE as the query category; a tester
#: served one row as category 1 and one as category 2 and reported exactly:
#: Battle Map showed only the first, Sector Mission only the second, and Area
#: Mission showed BOTH.
#:
#: WARNING: AND THAT LAST ONE IS THE RULE THAT MATTERS. Modes 3 and 4 take the
#: `0x611AF5E0` path with a HARD-CODED query category of 3, and the view's own
#: filter (`0x611C98E1`) keeps EVERY record when the mode is 3 or 4. So for the
#: Area list the client does no filtering at all -- **the server is the only
#: thing that can**, which is why msn_rows() filters by the asked-for category
#: rather than leaving it to the client.
CATEGORY_BATTLE_MAP = 1    #: UI cmd 0x100F, view mode 1 -- 21:3 Battle Map Mission
CATEGORY_SECTOR = 2        #: UI cmd 0x1010, view mode 2 -- 21:4 Sector Mission
CATEGORY_AREA = 3          #: UI cmd 0x1011, view mode 3/4 -- 21:5 Area Mission
CATEGORY_NAMES = {1: "Battle Map Mission", 2: "Sector Mission",
                  3: "Area Mission"}


def mission_record(name, category, mark=False, fields=None, mid=0):
    """One 536-byte row.

    `mid` is the mission ID at `record+0x00` -- the value an accept carries
    back; see MISSION_ID. `mark` writes each dword's own offset into it;
    WARNING: it is a CLIENT-KILLER (three crashes in four minutes, 2026-09-08) and
    survives only as the reproduce switch, because the columns it existed to
    name are bound above without it.
    The NAME is written last so neither the marker nor `mid` can eat it."""
    rec = bytearray(RECORD_LEN)
    if mark:
        for off in range(0, RECORD_LEN, 4):
            struct.pack_into("<I", rec, off, off)
    struct.pack_into("<I", rec, MISSION_ID, mid & 0xFFFFFFFF)
    # The view drops a record whose category byte does not match its own mode
    # (0x611C98E1), so echo the category the client asked for.
    struct.pack_into("<I", rec, 0x1C8, (category & 0xFF) << 24)
    struct.pack_into("<I", rec, 0x1E0, (category & 0xFF) << 24)
    for off, val in sorted((fields or {}).items()):
        struct.pack_into("<I", rec, off, val & 0xFFFFFFFF)
    nm = name.encode("cp932", "replace")[:0x40]
    rec[0x04:0x04 + len(nm) + 1] = nm + b"\0"
    return bytes(rec)


OP_STATUS = 0x15           #: arm 0x611AFA8E -- the graceful refusal
OP_LIST14 = 0x14           #: arm 0x611AFA58 -- 76-byte records into [mgr+0x3AD]
RECORD14_LEN = 0x4C        #: 76; 0x611AF84B `add ebp, 0x4c`


def status(code=0):
    """op 0x15 -- THE CLIENT'S OWN GRACEFUL REFUSAL, and the only completion
    path in this protocol that does NOT hand a callback a NULL record.

    Arm `0x611AFA8E` calls `0x611AF210(payload, len)`, which requires
    `len >= 0x10` and stores the dword at **payload+0x0C** into `[mgr+0x3A5]`
    with `[mgr+0x3B9] = 1`; it then calls `0x611AED90`, which closes the
    connection and FREES the job at `[mgr+0x3BA]`. So the job is released
    without the callback ever running -- which is exactly what 0x1B could not
    do safely.

    WARNING: The error code's meaning is per-screen. `0x611B0120` maps -10..0 to
    systext group 88 (the ARENA's strings) and anything else to 0, so 0 is
    the least presumptuous value: it is in range but is not one of the Arena
    messages this screen would have no business showing."""
    return build(OP_STATUS, struct.pack("<II", 0, code & 0xFFFFFFFF))


def page14(records):
    """op 0x14 -- arm `0x611AF780`: a BYTE count at payload+0x0C and that many
    **76-byte** records from payload+0x10, each copied into a fresh 0x58-byte
    node linked onto `[mgr+0x3AD]`, with `[mgr+0x3A1] += count`.

    count 0 is provably inert (the loop is `jbe`-skipped and the add is +0),
    but inert is not the same as useful: the job still needs a terminator, and
    the only terminators call the callback.

    WARNING: UNPROVEN. This exists because the 2026-09-06 kill is consistent with
    "0x1B with an EMPTY list" -- the callback may walk `[mgr+0x3AD]` and
    null-deref. Sending one zero-filled record before the 0x1B is the
    experiment that tests it, and it may crash the client again."""
    body = struct.pack("<II", 0, len(records) & 0xFF) + b"".join(records)
    return build(OP_LIST14, body)


def record14(fill=b""):
    """One 76-byte op-0x14 record. Zero-filled unless given bytes."""
    r = bytearray(RECORD14_LEN)
    r[:len(fill)] = fill[:RECORD14_LEN]
    return bytes(r)


def page(records):
    """op 0x1D -- one page of rows. Arm 0x611AFBA1 reads the count at
    payload+0x0C and walks 536-byte records from payload+0x10."""
    body = struct.pack("<II", 0, len(records)) + b"".join(records)
    return build(OP_PAGE, body)


def max_records_per_page():
    """How many rows fit under the client's receive buffer."""
    return (CLIENT_RX - HDR - PAYLOAD_HDR - 8) // RECORD_LEN


#: KEY: KIND 7 -- THE SECTOR / CITY STATE QUERY (op 0x10): the WAR STATE's door.
#: Static 2026-09-12 (fmodis) + prod's own log (111 such requests from real
#: clients, every one unanswered). Job ctor 0x611AF6F0: [job+0]=7, frame
#: 0x1B8, [job+0x28]=count, [job+0x2C..]=u32 ids, callback [job+0x34C], ctx
#: [job+0x350]. On the wire the BODY is {u32 uninitialised, u32 count, u32
#: ids[count]} -- live 2026-09-08: `00000000 19000000 bebdd335 ...` = 25 ids
#: starting 0x35D3BDBE = 903,069,118 = 903,000,000 + tile 69118 (selector
#: 200 "Sector 01"). Who asks: the WAR MAP (0x6118C670: every ARE row's
#: dword0 + 903,000,000, plus one fortress id per FZ zone -- 505: 903094099,
#: 509: 903092103, 513: 903103100) and CITY CONTROL (0x610E83A3: the 0x019A
#: city table's ids, count from lobby+0x6C36).
#:
#: THE RECORD IS 216 BYTES (0xD8). Both callbacks copy 0x36 dwords of it:
#: the war map (0x6118A020) matches record+0xD0 % 1e6 against the ARE row's
#: tile and copies it into the per-sector state ([warmap+0x59E8], stride
#: 0xD8), then sets +0xBF = 1 ("filled"); City Control's row ctor
#: (0x610E80D0) copies the same 0xD8. The same 216 bytes ALSO arrive through
#: the lobby channel: UI event 0x144C copies 0x015F payload+0x10 into a
#: sector's state (0x6118E855) -- nothing in the image posts 0x144C, so the
#: second server is the live door. What the overlays draw from the record
#: (10:19 terrain category, 10:20 B.G.Cost, 10:21 control rate, 10:22/23
#: supply rates, 10:31 "NPC Rank : %s" from a 0..5 byte, 10:33..35 O.C.U. /
#: U.S.N. Control / Deadlock from a signed byte) is NOT yet bound to
#: offsets -- that is what `mark` mode is for: one look at the war map's
#: Change View overlays names every field.
#:
#: WHICH REPLY OP: the client does not check the op against the kind; any
#: record arm whose stride holds 0xD8 works. 0x21 (arm 0x611AFCD6: u32 count
#: at payload+0x0C, 0x20C-byte slots from payload+0x10, callback(record,
#: ctx) per slot) is used here -- 0x1D's 0x218 stride would also do, but it
#: is the mission list's and a log reader should not have to guess. The
#: callbacks tolerate the NULL a 0x1B END hands them (0x6118C650 tests it,
#: 0x610E8130 tests it), so END is a safe terminator for THIS kind.
OP_SECTORS = 0x10
OP_SECTOR_PAGE = 0x21
SECTOR_SLOT_LEN = 0x20C        #: 0x611AFD1C `add ebx, 0x20c`
SECTOR_RECORD_LEN = 0xD8       #: the `rep movsd 0x36` in both callbacks
SECTOR_ID_OFF = 0xD0           #: u32; 0x6118A03F reads it, matches % 1,000,000
SECTOR_ID_BASE = 903_000_000   #: 0x6118C690 `add eax, 0x35D2AFC0`
SECTOR_CLIENT_FLAG = 0xBF      #: the client's own "filled" byte, set after the copy


class SectorQuery:
    """One kind-7 body: `{u32 arg (uninitialised), u32 count, u32 ids[]}`."""

    __slots__ = ("arg", "count", "ids")

    def __init__(self, body):
        b = bytes(body).ljust(8, b"\0")
        self.arg = struct.unpack_from("<I", b, 0)[0]
        self.count = struct.unpack_from("<I", b, 4)[0]
        n = min(self.count, max(0, (len(b) - 8) // 4))
        self.ids = list(struct.unpack_from("<%dI" % n, b, 8)) if n else []

    def tiles(self):
        """The ids as ARE tiles (id - 903,000,000); an id below the base is
        returned as it is (City Control's city ids are not tiles)."""
        return [i - SECTOR_ID_BASE if i >= SECTOR_ID_BASE else i
                for i in self.ids]

    def __str__(self):
        t = self.tiles()
        return ("count=%d (%d carried) ids %s%s"
                % (self.count, len(self.ids),
                   ", ".join(str(x) for x in t[:6]),
                   " ..." if len(t) > 6 else ""))


def sector_record(sid, mark=False, fields=None):
    """One 216-byte sector/city record for id `sid` (the id as the client
    sent it, base included).

    `mark` names the unknown fields on screen: True writes each DWORD's own
    offset, `"byte"` writes each BYTE's own offset. Dword-mark leaves three
    of every four bytes ZERO, so a byte-sized field reads 0 and says nothing
    -- which is exactly what the City Control screen showed on 2026-09-12
    (B.G.Cost 0/0, control "Deadlock"). Byte-mark makes every byte speak, and
    216 < 256 so each offset is unique.

    `fields` = {offset: int | bytes} pokes are applied after the mark; the
    id is written last so nothing eats it, and the client's flag byte is
    left 0."""
    rec = bytearray(SECTOR_RECORD_LEN)
    if mark == "byte":
        for off in range(SECTOR_RECORD_LEN):
            rec[off] = off & 0xFF
    elif mark:
        for off in range(0, SECTOR_RECORD_LEN, 4):
            struct.pack_into("<I", rec, off, off)
    for off, val in sorted((fields or {}).items()):
        if isinstance(val, (bytes, bytearray)):
            rec[off:off + len(val)] = val[:SECTOR_RECORD_LEN - off]
        else:
            struct.pack_into("<I", rec, off, int(val) & 0xFFFFFFFF)
    struct.pack_into("<I", rec, SECTOR_ID_OFF, int(sid) & 0xFFFFFFFF)
    rec[SECTOR_CLIENT_FLAG] = 0
    return bytes(rec)


def sector_page(records):
    """op 0x21 -- one page of 0x20C-byte slots, each holding a 216-byte
    record (zero-padded). u32 count at payload+0x0C, slots from +0x10."""
    slots = b"".join(bytes(r).ljust(SECTOR_SLOT_LEN, b"\0")[:SECTOR_SLOT_LEN]
                     for r in records)
    body = struct.pack("<II", 0, len(records)) + slots
    return build(OP_SECTOR_PAGE, body)


#: KEY: KIND 5 -- THE ORDER TEMPLATES (op 0xE), static 2026-09-30. The job
#: (0x611AF670: [job]=5, op 0xE, [job+0x28] count, [job+0x2C..] ids) is
#: queued by 0x611C9850 from the Accepted Mission table's row 0 +0x08..+0x14,
#: with callback 0x611C9160: it copies each 524-byte (0x83-dword) record into
#: the list, keyed by record+0x00, and branches on the END's NULL
#: (0x611C918D) -- so op 0x21 pages then 0x1B END are safe. The body has the
#: kind-7 shape, {u32, u32 count, u32 ids}: SectorQuery parses it.
#: The Order dialog reads the record (0x611CA301..): +0x1E4 operation time
#: (-> order body +0x038), +0x1F4 reward MP (non-zero = an MP reward),
#: +0x1F8 reward H$, +0x200 the base Order MP. The list's second layout
#: (0x611CBC17) draws +0x1E2 (type), +0x04 (name), +0x1F4, +0x1F8, +0x1F0,
#: +0x1E8.
OP_ORDER_TEMPLATES = 0x0E
TEMPLATE_LEN = 0x20C
TPL_OP_TIME, TPL_REWARD_MP, TPL_REWARD_HS, TPL_BASE_MP = 0x1E4, 0x1F4, 0x1F8, 0x200


def order_template_record(tid, name, category, op_time, reward_mp, reward_hs,
                          base_mp):
    """One 524-byte order template (see OP_ORDER_TEMPLATES)."""
    rec = bytearray(TEMPLATE_LEN)
    struct.pack_into("<I", rec, 0x000, tid & 0xFFFFFFFF)
    struct.pack_into("<I", rec, 0x1C8, (category & 0xFF) << 24)
    struct.pack_into("<I", rec, 0x1E0, (category & 0xFF) << 24)
    for off, val in ((TPL_OP_TIME, op_time), (TPL_REWARD_MP, reward_mp),
                     (TPL_REWARD_HS, reward_hs), (TPL_BASE_MP, base_mp)):
        struct.pack_into("<I", rec, off, int(val) & 0xFFFFFFFF)
    nm = str(name).encode("cp932", "replace")[:0x40]
    rec[0x04:0x04 + len(nm) + 1] = nm + b"\0"
    return bytes(rec)


def max_sectors_per_page():
    """How many 0x20C slots fit under the client's receive buffer (7)."""
    return (CLIENT_RX - HDR - PAYLOAD_HDR - 8) // SECTOR_SLOT_LEN


#: The first 48 bytes of a live kind-7 body (prod 2026-09-08T16:01:03Z): the
#: uninitialised dword, count 25, then ids 903,069,118..903,069,122 and
#: 903,070,117..: zone 200's sectors 01..05 and 11..15.
SECTORS_CAPTURE = bytes.fromhex(
    "00000000 19000000 bebdd335 bfbdd335 c0bdd335 c1bdd335 c2bdd335 a5c1d335"
    " a6c1d335 a7c1d335 a8c1d335 a9c1d335".replace(" ", ""))


def selftest():
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        print(("  OK   " if cond else "  FAIL ") + label
              + (("  -- " + detail) if detail and not cond else ""))
        ok = ok and cond

    print("fmomsn --selftest")

    got = parse(HELLO_CAPTURE)
    check("the captured hello decrypts and its MD5 verifies", got is not None)
    if got:
        op, body = got
        check("its op is 4 (HELLO)", op == OP_HELLO, "got 0x%X" % op)
        # The static read of 0x611AEC40: byte 2 at payload+0x08, dword 1 at
        # payload+0x0C. payload+0x08's top three bytes are uninitialised stack
        # in the client, so only the low byte is a contract.
        check("hello body byte 0 is 2 (0x611AEC40 `mov byte [esp+0x30],2`)",
              len(body) >= 8 and body[0] == 2)
        check("hello body dword 1 is 1 (`mov [esp+0x34],1`)",
              len(body) >= 8 and struct.unpack_from("<I", body, 4)[0] == 1)

    for op, blen in ((OP_GO, 0), (OP_END, 0), (OP_PAGE, 8 + RECORD_LEN)):
        f = build(op, b"\0" * blen)
        r = parse(f)
        check("op 0x%02X round-trips" % op, r is not None and r[0] == op)
        check("op 0x%02X frame length field matches the frame" % op,
              struct.unpack_from("<I", f, 0)[0] == len(f))

    f = build(OP_GO)
    check("the body is enciphered (not accidentally plaintext)",
          f[4:8] != struct.pack("<I", 1))
    check("(total-4) % 8 leftover travels in clear, as 0x6106F8B0 does",
          len(HELLO_CAPTURE) == 40
          and HELLO_CAPTURE[0x24:] == struct.pack("<I", 1))

    rec = mission_record("Recon Alpha", 3)
    check("a record is exactly 536 bytes", len(rec) == RECORD_LEN)
    check("the name lands at +0x04, NUL-terminated",
          rec[0x04:0x0F] == b"Recon Alpha" and rec[0x0F] == 0)
    check("the category byte lands in +0x1C8's byte 3",
          struct.unpack_from("<I", rec, 0x1C8)[0] >> 24 == 3)
    marked = mission_record("X", 3, mark=True)
    check("mark mode writes each dword's own offset",
          struct.unpack_from("<I", marked, 0x1E4)[0] == 0x1E4)
    check("mark mode still leaves a readable name",
          marked[0x04:0x05] == b"X" and marked[0x05] == 0)

    n = max_records_per_page()
    check("7 records fit in a page", n == 7, "got %d" % n)
    pg = page([rec] * n)
    check("a full page stays under the client's 4,096-byte buffer",
          len(pg) <= CLIENT_RX, "%d B" % len(pg))
    r = parse(pg)
    check("a full page round-trips", r is not None and r[0] == OP_PAGE)
    if r:
        cnt = struct.unpack_from("<I", r[1], 4)[0]
        check("the page's count is at payload+0x0C", cnt == n)
    try:
        page([rec] * (n + 1))
        check("an oversized page is REFUSED, not sent", False)
    except ValueError:
        check("an oversized page is REFUSED, not sent", True)

    q = ListQuery(struct.pack("<7I", 0, 1, 100, 1, 509, 0xABCD, 3 << 24))
    check("the op-9 query decodes",
          (q.submode, q.max_rows, q.nation, q.mapkind, q.category, q.arg)
          == (1, 100, 1, 509, 3, 0xABCD))
    # WARNING: THE LIVE FIXTURES. Two real op-9 bodies from the first client that ever
    # spoke this protocol. They are here because they are what caught the
    # category being read one slot short -- see ListQuery's docstring.
    for _raw, _want in ((LIST_QUERY_LIVE_1, 1), (LIST_QUERY_LIVE_2, 2)):
        lq = ListQuery(_raw)
        check("live op-9 body decodes: submode 2, max 100, nation 1, "
              "MapKind 100, category %d" % _want,
              (lq.submode, lq.max_rows, lq.nation, lq.mapkind, lq.category)
              == (2, 100, 1, 100, _want),
              "got submode=%d max=%d nation=%d MapKind=%d category=%d"
              % (lq.submode, lq.max_rows, lq.nation, lq.mapkind, lq.category))
    check("a served row echoes the LIVE category, or 0x611C98E1 drops it",
          struct.unpack_from(
              "<I", mission_record("x", ListQuery(LIST_QUERY_LIVE_2).category),
              0x1C8)[0] >> 24 == 2)

    # op 0x17 -- the Scramble Board's group list. The stride is the thing to
    # pin: the job ctor stores 0xDD and that is NOT the record size, so a
    # future reader who trusts it would build 221-byte rows and the board
    # would walk garbage from the second row on.
    check("a group row is 308 bytes, NOT the 0xDD the job ctor stores",
          len(group_record(1)) == GROUP_RECORD_LEN == 0x134 != 0xDD)
    _gr = group_record(7, name="Lex Arden", comment="All welcome", state=0,
                       members=3, sorties=5, cost_now=1200, cost_at_sortie=99,
                       cost_required=800, bonus=50)
    check("every column sits where the board's comparator reads it",
          struct.unpack_from("<I", _gr, G_ID)[0] == 7
          and _gr[G_COMMENT:G_COMMENT + 11] == b"All welcome"
          and _gr[G_NAME:G_NAME + 9] == b"Lex Arden"
          and struct.unpack_from("<I", _gr, G_MEMBERS)[0] == 3
          and _gr[G_SORTIES] == 5
          and struct.unpack_from("<I", _gr, G_COST_NOW)[0] == 1200
          and struct.unpack_from("<I", _gr, G_COST_AT_SORTIE)[0] == 99
          and struct.unpack_from("<I", _gr, G_COST_REQ)[0] == 800
          and struct.unpack_from("<I", _gr, G_BONUS)[0] == 50)
    # The strings must not run into the field that follows them -- the name
    # field ends where the STATE dword begins, and an overlong comment would
    # otherwise overwrite the creator's name.
    _long = group_record(1, name="N" * 400, comment="C" * 400)
    check("overlong name/comment are clipped inside their own fields",
          _long[G_NAME - 1] == 0 and _long[G_STATE - 1] == 0
          and struct.unpack_from("<I", _long, G_STATE)[0] == 0)
    # The count is a BYTE at payload+0x0C; op 0x1D's is a u32. Getting this
    # wrong shows a short list rather than failing.
    _pg = group_page([_gr, group_record(8)])
    _op, _body = parse(_pg)
    check("op 0x17 carries its count as a byte at payload+0x0C",
          _op == OP_GROUPS == 0x17 and _body[4] == 2
          and _body[8:8 + GROUP_RECORD_LEN] == _gr)
    check("a page of rows fits the client's receive buffer",
          len(group_page([group_record(i) for i in
                          range(max_group_records_per_page())])) <= CLIENT_RX)
    _over = False
    try:
        group_page([group_record(1)] * 256)
    except ValueError:
        _over = True
    check("more than 255 rows is REFUSED, not wrapped", _over)
    # mark mode: every dword names its own offset, and the fields that make a
    # row identifiable and JOINABLE survive -- a marked row nobody can select
    # would measure nothing.
    _mk = group_record(42, name="Zed", comment="Hi", members=9, sorties=9,
                       cost_now=9, cost_required=9, bonus=9, mark=True)
    check("mark mode makes every unknown dword name its own offset",
          struct.unpack_from("<I", _mk, G_STATE)[0] == G_STATE
          and struct.unpack_from("<I", _mk, G_MEMBERS)[0] == G_MEMBERS
          and struct.unpack_from("<I", _mk, G_COST_REQ)[0] == G_COST_REQ
          and struct.unpack_from("<I", _mk, G_BONUS)[0] == G_BONUS)
    check("mark mode still leaves the row joinable and named",
          struct.unpack_from("<I", _mk, G_ID)[0] == 42
          and _mk[G_NAME:G_NAME + 3] == b"Zed"
          and _mk[G_COMMENT:G_COMMENT + 2] == b"Hi")

    # KIND 7 (op 0x10): the live body parses to zone 200's tiles, the record
    # carries the id where the war map matches it, a page is op 0x21 with
    # 0x20C slots, and seven of them fit the client's buffer.
    _sq = SectorQuery(SECTORS_CAPTURE)
    check("live kind-7 body: count 25, 10 ids carried, ids are 903e6 + tile",
          _sq.count == 25 and len(_sq.ids) == 10
          and _sq.ids[0] == 903_069_118 and _sq.tiles()[0] == 69118
          and _sq.tiles()[5] == 70117)
    _s8 = sector_record(903_069_118, mark="byte")
    check("mark8: every byte is its own offset, the id and the client flag survive",
          _s8[0x01] == 0x01 and _s8[0x65] == 0x65 and _s8[0xD7] == 0xD7
          and struct.unpack_from("<I", _s8, SECTOR_ID_OFF)[0] == 903_069_118
          and _s8[SECTOR_CLIENT_FLAG] == 0
          and all(_s8[o] != 0 for o in (0x05, 0x0A, 0x63, 0x66)))
    _sr = sector_record(903_069_118, mark=True, fields={0x08: 5, 0x10: b"ab"})
    check("sector record: 216 B, id at +0xD0 survives the mark, pokes land, "
          "flag byte 0xBF is 0",
          len(_sr) == SECTOR_RECORD_LEN
          and struct.unpack_from("<I", _sr, SECTOR_ID_OFF)[0] == 903_069_118
          and struct.unpack_from("<I", _sr, 0x0C)[0] == 0x0C
          and struct.unpack_from("<I", _sr, 0x08)[0] == 5
          and _sr[0x10:0x12] == b"ab" and _sr[SECTOR_CLIENT_FLAG] == 0)
    _sp = sector_page([_sr, sector_record(903_069_119)])
    _spo, _spb = parse(_sp)
    check("sector page: op 0x21, u32 count 2 at payload+0x0C, 0x20C slots, "
          "record bytes at slot start",
          _spo == OP_SECTOR_PAGE == 0x21
          and struct.unpack_from("<I", _spb, 4)[0] == 2
          and _spb[8:8 + SECTOR_RECORD_LEN] == _sr
          and struct.unpack_from("<I", _spb, 8 + SECTOR_SLOT_LEN + SECTOR_ID_OFF)[0]
          == 903_069_119
          and len(_spb) == 8 + 2 * SECTOR_SLOT_LEN)
    check("seven sector slots fit the client's receive buffer",
          max_sectors_per_page() == 7
          and len(sector_page([sector_record(i) for i in range(7)])) <= CLIENT_RX)

    check("looks_like_frame accepts the capture",
          looks_like_frame(HELLO_CAPTURE) == 40)
    check("looks_like_frame rejects a POL packet header",
          looks_like_frame(struct.pack("<HHHH", 0x14, 0x0300, 0x0321, 0))
          is None)

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--decode", metavar="FILE",
                    help="decode a captured frame (logs/captures/*.bin)")
    a = ap.parse_args()
    if a.decode:
        raw = open(a.decode, "rb").read()
        r = parse(raw)
        if not r:
            print("does not verify as a community-server frame (%d B)"
                  % len(raw))
            sys.exit(1)
        op_, body_ = r
        print("op 0x%02X, %d B of body" % (op_, len(body_)))
        for i in range(0, len(body_), 16):
            print("  %04x  %s"
                  % (i, " ".join("%02x" % b for b in body_[i:i + 16])))
        if op_ == OP_LIST:
            print("  ->", ListQuery(body_))
        sys.exit(0)
    sys.exit(selftest())
