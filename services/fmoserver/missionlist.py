"""The mission list (0x018D -> 0x018E) and the report reply (0x018F)."""
import os
import struct
from . import packet


#: The MISSION BOARD's list (static RE 2026-09-04). See the 0x018E block below.
MSG_MISSION_LIST_REQ = 0x018D
MSG_MISSION_LIST_REPLY = 0x018E

# --------------------------------------------------------------------------- #
# 0x018D -> 0x018E -- THE MISSION LIST (static RE 2026-09-04)
# --------------------------------------------------------------------------- #
# VERIFIED:KEY: The biggest reply in the game, and it is the MISSION BOARD's list. Ten of
# the twelve "unknown" LOBAPI commands are SE's Mission Menu (systext groups
# 21-28); every one has been answered with zeros since the table was built, and
# an all-zero 0x018E is precisely the input that renders 21:16 "No Missions
# Found". The full decode, with every address, was done statically.
#
# WARNING: THE REQUEST CARRIES NOTHING. The send method 0x61172590 pushes
# flags=0/len=0x20/id=0x18D and then writes nothing into the packet, so the
# 32-byte request is all zeros and there is NO selector to key a response on.
# Do not go looking for one.
#
# The parse 0x611725B0 does NOT write into the lobby struct -- like most of this
# table it writes to a buffer the CALLER supplies ([apiobj+0x0E]). Here the
# caller (0x611C8121) zeroes 0x164E dwords at [0x613C15FC]+4 and passes +4, so:
#
#     [0x613C15FC] + 0x04   u32 MODE       <- payload+0x20   (0x611725D8)
#                  + 0x08   33 x 692 B     <- payload+0x24   (0x611725D6)
#
# The stride is PROVED by the copy size, not inferred: 33 x 0x2B4 = 22,836 =
# 0x164D * 4 exactly, and 0x2B4 is read straight off `imul ..,0x2B4` at
# 0x611CC96D / 0x611CC2E8 / 0x611CCAA6.
REPLY_018E_LEN = 22872                 # = LOBAPI[0x018D][1] = 0x24 + 33*0x2B4
M18E_MODE = 0x20                       # u32 -> [0x613C15FC]+4
M18E_RECORDS = 0x24                    # records base -> [0x613C15FC]+8
M18E_REC_LEN = 0x2B4                   # 692 bytes per mission record
M18E_REC_COUNT = 33                    # 33 * 692 == 0x164D * 4, exactly
#: Record-relative offsets, read out of the list renderer. Only these two have a
#: MEANING we can defend; the other thirteen fields the renderer touches are
#: enumerated in the findings doc and are reachable through
#: FMO_MISSION_LIST_FIELDS rather than being given names we have not earned.
ML_KEY = 0x000                         # u32; ZERO -> the row draws 21:30 "None"
ML_NAME = 0x11C                        # the %s of "%s" / "(%u)%s" (0x611CC9A6)
ML_NAME_MAX = 0x134 - 0x11C            # 24 -- 0x134 is the next field read

#: WARNING: OFF BY DEFAULT. With it off the generic LOBAPI arm answers exactly as it
#: always has (22,872 zeros); on, reply_018e() answers instead, and with every
#: sub-knob at its default the two are BYTE-IDENTICAL (asserted in the
#: selftest). NOT LIVE-TESTED -- this reply has never been non-zero.
#: WARNING: EMPTY IS OFF, and that is deliberate: compose's `${FMO_MISSION_LIST:-}`
#: hands us "", and a bare `!= "0"` would read that as ON
#: (env-empty-string-is-not-the-default). Same reason every int below goes
#: through `.strip() or "0"` -- `int("", 0)` raises at import, which is a
#: crash-looping container, the empty-env trap.
SERVE_MISSION_LIST = (os.environ.get("FMO_MISSION_LIST", "").strip() or "0") != "0"
#: WARNING: FMO_ANSWER_018D -- how to answer the MISSION BOARD's list request.
#: 'refuse' (DEFAULT, confirmed in a live session 2026-09-06 13:27:04Z): reply message 2, which
#:     the dispatcher 0x61172250 treats as an id mismatch -> the client shows
#:     [FMxxxxx] and SURVIVES.
#: 'short': the CORRECT form of 0x018E, per the RE that closed the crash (see
#:     CLIENT_RX_BUFFER). The full-length 22,872-byte body was never the right
#:     answer -- it overran the client's 15,000-byte inline RX buffer. The parse
#:     0x611725B0 copies a FIXED 0x164D dwords from payload+0x24 whatever the
#:     frame length is, and the row consumer 0x611CC973 stops a row on
#:     `record+0x00 == 0`, so the right reply is as many 692-byte records as fit
#:     UNDER the cap. WARNING: UNTESTED LIVE. Rows past what we send read whatever is
#:     after the RX buffer (the connection object's own tail), so a few junk
#:     rows are possible -- that is a cosmetic risk, not the crash.
#: '0': stay silent; the board spins instead.
#: WARNING: 'zeros' (the historical body) is GONE: build() now refuses any frame over
#: the RX buffer, so it cannot be put on the wire at all. Nothing is lost -- the
#: size-vs-content question it existed to answer is settled.
ANSWER_018D = os.environ.get("FMO_ANSWER_018D", "").strip() or "refuse"
if ANSWER_018D not in ("refuse", "short", "0"):
    ANSWER_018D = "refuse"
#: The most 692-byte records that fit under the cap, so the FEWEST rows read
#: past our data.
S018E_ROWS = (packet.MAX_PAYLOAD - 0x24) // 0x2B4
S018E_SHORT_LEN = 0x24 + S018E_ROWS * 0x2B4


def reply_018e_short(mode=0, rows=None):
    """A correct-form, cap-safe 0x018E: u32 mode at +0x20, then whole records.

    `rows` is [(mission_id, name)] -- the pilot's ACCEPTED missions. Each gets
    its id at record+ML_KEY (non-zero, or 0x611CC973 draws the row as systext
    21:30 "None") and its name at +ML_NAME, cut to ML_NAME_MAX-1 because
    +0x134 is the next field the renderer reads.

    WARNING: Only S018E_ROWS (21) records fit under the client's RX buffer, and the
    parse 0x611725B0 copies a FIXED 0x164D dwords regardless -- so rows past
    ours are filled from whatever follows the buffer. That is the documented
    cosmetic risk; live 2026-09-12 it read as an empty list, i.e. those bytes
    happened to be zero. It is not guaranteed to stay that way."""
    body = bytearray(S018E_SHORT_LEN)
    struct.pack_into("<I", body, 0x20, mode)
    for i, row in enumerate(rows or []):
        if i >= S018E_ROWS:
            break
        mission_record_fill(body, M18E_RECORDS + i * M18E_REC_LEN, *row)
    return bytes(body)


def mission_record_fill(body, rec, mid, name, fields=None):
    """One 692-B View-B record at body[rec:]: the id at +ML_KEY, the name at
    +ML_NAME (cut before +0x134), then any {offset: u32} -- the report/list
    state fields MR_STATE / MR_RESULT / MR_LIMIT / MR_START."""
    struct.pack_into("<I", body, rec + ML_KEY, int(mid) & 0xFFFFFFFF)
    raw = str(name).encode("cp932", "replace")[:ML_NAME_MAX - 1]
    body[rec + ML_NAME:rec + ML_NAME + len(raw)] = raw
    for off, val in (fields or {}).items():
        if isinstance(val, str):
            # an order row's commander / assignee name (+0x44 / +0x74), cut
            # so the NUL stays inside its 0x20 bytes
            raw = val.encode("cp932", "replace")[:missionboard.ROW_NAME_MAX - 1]
            body[rec + off:rec + off + len(raw)] = raw
            continue
        struct.pack_into("<I", body, rec + off, int(val) & 0xFFFFFFFF)


def reply_018f(mid, name, fields=None):
    """The REPORT reply: 0x20 bytes the parse skips, then ONE View-B record,
    which the client copies over its row and switches on (0x611CA9B0)."""
    body = bytearray(missionboard.REPLY_018F_LEN)
    mission_record_fill(body, 0x20, mid, name, fields)
    return bytes(body)
#: The MODE word at payload+0x20. Tested `cmp ..,2 / ja` at 0x611CC9C0 and
#: 0x611CC335: <=2 draws the name with "%s", >2 draws it with "(%u)%s" and
#: enables an extra element. It is a FORMAT SELECTOR, not a row count -- the
#: rows come from ML_KEY.
MISSION_LIST_MODE = int(os.environ.get("FMO_MISSION_LIST_MODE", "").strip()
                        or "0", 0)
#: The rows: '|'-separated names, at most M18E_REC_COUNT of them. Each named row
#: gets a non-zero ML_KEY (or it would draw as "None") and its name at ML_NAME.
#: '' (the default) means no rows, i.e. the all-zero reply.
MISSION_LIST_ROWS = os.environ.get("FMO_MISSION_LIST_ROWS", "").strip()
#: Raw per-record u32 pokes, for the thirteen fields whose MEANING is unknown:
#: '<row>:<offset>=<value>[,...]', e.g. '0:0x20=5,0:0x2C=1200'. Applied AFTER
#: the row defaults, so it can also clear ML_KEY on purpose.
MISSION_LIST_FIELDS = os.environ.get("FMO_MISSION_LIST_FIELDS", "").strip()

#: Type-1 ids present under the install root on 2026-08-26 (281 of 512;
#: the resource path formula with BASE[1] = 53557). The three
#: an earlier note opened by hand -- 38, 107, 200 -- are all here.
TYPE1_ON_DISK = frozenset(
    [0] + list(range(14, 17)) + [18] + list(range(24, 37)) + list(range(38, 78))
    + list(range(80, 94)) + [95] + list(range(99, 105)) + list(range(107, 128))
    + list(range(130, 142)) + list(range(143, 165)) + list(range(169, 175))
    + [176] + list(range(180, 189)) + [191, 193, 200, 203, 204, 221]
    + list(range(231, 245)) + list(range(250, 256)) + [261, 263]
    + list(range(265, 275)) + list(range(281, 285)) + list(range(300, 318))
    + list(range(320, 327)) + list(range(330, 333)) + list(range(343, 348))
    + list(range(352, 357)) + list(range(363, 366)) + list(range(372, 376))
    + list(range(377, 384)) + [403, 404] + list(range(406, 415))
    + list(range(417, 421)) + [422, 423] + list(range(436, 440)) + [443, 445,
    446, 448, 453, 454, 460, 462, 463, 464, 467] + list(range(469, 474)))


def mission_list_rows(rows=None):
    """[(row index, name)] for every row FMO_MISSION_LIST_ROWS names.

    Pure, so the selftest can drive it. Rows past M18E_REC_COUNT are dropped --
    the client's buffer holds exactly 33 and there is nowhere to put a 34th."""
    spec = MISSION_LIST_ROWS if rows is None else rows
    if not spec:
        return []
    return [(i, name.strip())
            for i, name in enumerate(spec.split("|"))
            if i < M18E_REC_COUNT]


def mission_list_fields(fields=None):
    """{(row, record offset): u32} from FMO_MISSION_LIST_FIELDS.

    Pure, and it RAISES on an out-of-range row or offset rather than silently
    writing into the neighbouring record -- a poke that lands one record over is
    the kind of wrong that still looks plausible on screen."""
    spec = MISSION_LIST_FIELDS if fields is None else fields
    out = {}
    for item in (s.strip() for s in (spec or "").split(",")):
        if not item:
            continue
        where, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"FMO_MISSION_LIST_FIELDS: '{item}' has no '='")
        row, sep, off = where.partition(":")
        if not sep:
            raise ValueError(f"FMO_MISSION_LIST_FIELDS: '{item}' has no ':'")
        r, o, v = int(row, 0), int(off, 0), int(value, 0)
        if not 0 <= r < M18E_REC_COUNT:
            raise ValueError(f"FMO_MISSION_LIST_FIELDS: row {r} is not in "
                             f"0..{M18E_REC_COUNT - 1}")
        if not 0 <= o <= M18E_REC_LEN - 4:
            raise ValueError(f"FMO_MISSION_LIST_FIELDS: offset 0x{o:X} does not "
                             f"fit a u32 in a {M18E_REC_LEN}-byte record")
        out[(r, o)] = v & 0xFFFFFFFF
    return out


def reply_018e(mode=None, rows=None, fields=None):
    """The 22,872-byte 0x018E mission-list payload.

    Zero except the MODE word and the rows named below -- and with every knob at
    its default that is 22,872 zeros, i.e. byte-for-byte what the generic arm
    has always sent. Nothing here is a shape guess: the length, the records
    base, the 692-byte stride and the two named record fields all come out of
    the client's own parse and renderer (see the block comment above)."""
    b = bytearray(REPLY_018E_LEN)
    struct.pack_into("<I", b, M18E_MODE,
                     (MISSION_LIST_MODE if mode is None else mode) & 0xFFFFFFFF)
    for i, name in mission_list_rows(rows):
        rec = M18E_RECORDS + i * M18E_REC_LEN
        # Non-zero or 0x611CC973 draws the row as systext 21:30 "None".
        struct.pack_into("<I", b, rec + ML_KEY, i + 1)
        raw = name.encode("cp932", "replace")[:ML_NAME_MAX - 1]
        b[rec + ML_NAME:rec + ML_NAME + len(raw)] = raw
    for (r, o), v in sorted(mission_list_fields(fields).items()):
        struct.pack_into("<I", b, M18E_RECORDS + r * M18E_REC_LEN + o, v)
    return bytes(b)


def mission_list_why():
    """One line for the log: what reply_018e() is about to serve, and why."""
    rows = mission_list_rows()
    pokes = mission_list_fields()
    if not rows and not pokes and not MISSION_LIST_MODE:
        return (f"{REPLY_018E_LEN} zeros -- byte-identical to the generic arm; "
                f"every row's ML_KEY is 0, so the board draws 21:16 "
                f"'No Missions Found'")
    return (f"MODE={MISSION_LIST_MODE} "
            f"({'plain %s' if MISSION_LIST_MODE <= 2 else '(%u)%s'}), "
            f"{len(rows)} row(s) with a non-zero key at record+0x{ML_KEY:X} and "
            f"a name at +0x{ML_NAME:X}"
            + (f", {len(pokes)} raw field poke(s)" if pokes else "")
            + f"; stride 0x{M18E_REC_LEN:X} x {M18E_REC_COUNT}")


# Called at run time only; imported last so that import cycles resolve.
from . import missionboard  # noqa: E402
