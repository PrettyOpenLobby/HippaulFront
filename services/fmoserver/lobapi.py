"""The lobby-API menu actions: the request objects and their marker replies."""
import os
import struct


#: PROBE: FMO_LOBAPI_MARK -- fill a lobby-API reply with DECODABLE MARKERS instead of
#: zeros, so one launch maps a whole block instead of bisecting it.
#:
#: WHY THIS EXISTS. Most LOBAPI replies are answered with `bytes(need)` and have
#: never been anything else, so every screen they feed renders zeros -- and a
#: field that renders 0 tells you nothing about WHICH dword it read. Fill dword
#: `i` with `base + i` instead and the number on screen IS its own offset:
#: read 1037 off a screen, subtract the base, and the field is dword 37 =
#: payload byte 0x94. That is the whole instrument.
#:
#: FORM: `FMO_LOBAPI_MARK=<id>[:<base>][,<id>[:<base>]...]`, where `<id>` is the
#: REQUEST id (a LOBAPI key, e.g. 0x01AC) or that request's REPLY id (0x01AD) --
#: both are accepted and resolved to the request. `<base>` defaults to
#: MARK_BASE_DEFAULT so a marked field can never be confused with a real zero.
#:
#: WARNING: IT TAKES AN EXPLICIT LIST, AND THAT IS DELIBERATE -- there is no "mark
#: everything". These bodies are 22,872 B (0x018E) and 14,468 B (0x01A2), and
#: nonzero bytes in them become counts, indices and SET BITS: a set bit in the
#: owned-items table claims a part, and a MapNo with no file crashes the client
#: at 0x611250A2. Marking one message at a time is also what keeps the reading
#: single-variable. Default is empty: nothing is marked and every body is bytes
#: for byte what it has always been.
#: WARNING: THE PARSE LIVES WITH THE `LOBAPI` TABLE, NOT HERE, AND THAT IS LOAD-BEARING.
#: It was written here first and `LOBAPI` is defined ~2,200 lines further down,
#: so the loop raised `NameError` at import the moment the knob was set -- an
#: env-parse that crash-loops the container, which is exactly the empty-env trap
#: (`int("", 0)` in a compose default). It selftested clean because an UNSET
#: knob never enters the loop. See `parse_lobapi_mark()` below the table.
MARK_BASE_DEFAULT = 1000
MARK_LOBAPI = {}                       # filled after LOBAPI; see parse_lobapi_mark


def mark_body(need, base):
    """`need` bytes of dword `i` = `base + i`, tail-padded. Pure; selftested."""
    b = bytearray()
    for i in range((need + 3) // 4):
        b += struct.pack("<I", (base + i) & 0xFFFFFFFF)
    return bytes(b[:need])


def lobapi_payload(msg, need, req=b"", groups=(), nation=1):
    """The reply body for a lobby-API message: markers if this message is armed,
    the built 0x01AD / 0x01A3 block, or zeros.

    `req` is the REQUEST body, `groups` the POL groups this member is in and
    `nation` the played character's nation byte. 0x01AC reads the first two (its
    reply annotates the ids the client sent, and only the ones `groups` vouches
    for); 0x01A2 reads `req` for the screen byte and `nation` to filter rows."""
    if msg in MARK_LOBAPI:
        # Marking WINS over the built block on purpose -- if you have armed the
        # probe for 0x01AC you are asking where its dwords land, not serving it.
        return mark_body(need, MARK_LOBAPI[msg])
    if msg == 0x01AC:
        body, _why = squadron.reply_01ad(req, groups)
        if len(body) == need:
            return body
    if msg == 0x01A2:
        body = cosmetics.reply_01a3(need, req, nation)
        if body is not None and len(body) == need:
            return body
    if msg == missionlist.MSG_MISSION_LIST_REQ and missionlist.SERVE_MISSION_LIST:
        body = missionlist.reply_018e()
        if len(body) == need:
            return body
    return bytes(need)


# --------------------------------------------------------------------------- #
# the LOBBY API -- the menu actions, decoded 2026-08-19
# --------------------------------------------------------------------------- #
# Every menu action in the character-select / lobby screens goes through one
# small C++ object per command. The lobby holds them contiguously, 18 bytes
# apart, from lobby+0x7452; their constructor is the run of
# `mov dword [esi], <vtable>` at 0x61175DB0, which is what makes the set
# enumerable. Each object's vtable is just {send, parse}:
#
#     send   pushes `flags / payload length / MESSAGE ID` and calls the packet
#            builder 0x61199FC0 -- so the request id and its size are literals.
#     parse  copies the reply's payload into the object's out-parameter, or is
#            the do-nothing stub 0x6122B670 when the reply carries nothing.
#
# THE REPLY ID IS NOT IN THAT CLASS. It is stamped by a START function --
# `mov word ptr [this+0x0C], <reply id>` -- and those are SHARED between
# commands and sit nowhere near the send method. 0x61173210 is a generic one
# that stamps 0x0001. WARNING: Pairing each store with the nearest send by address
# looks convincing and is wrong: it gives half the table its neighbour's id, and
# it made 0x01AB (below) look like it expects 0x01AD when it expects 0x0001.
# The pairing here comes from the CALL SITES instead --
# `lea ecx, lobby+<offset>` immediately before `call/jmp <start>` --
# which is what a walk of the image collects.
#
# THE DISPATCHER, listed as unfound in an earlier note, is 0x61172250:
#
#     movzx eax, word [rx+6]        ; the reply's message id
#     movsx ecx, word [this+0x0C]   ; the id this request is waiting for
#     cmp   eax, ecx
#     jne   0x61172301              ; MISMATCH -> lobby+0x7D5F = word [rx+8]
#     call  [vtable+4]              ; match -> parse, lobby+0x7D5F = 0xFFFF
#
# WARNING: So on a mismatch the client takes the reply's CONNECTION-ID FIELD as an
# error code -- that is where the number in "[FMxxxxx]" comes from. Answering
# with the wrong message id is therefore not inert: it raises a numbered error.
#
# VERIFIED: 0x01AB IS "Change Nations" -- live-verified 2026-08-18 (the user clicked it
# and this is what went out, then silence). Its object is lobby+0x7518, its
# start is the generic 0x61173210, so it waits for MESSAGE 1, and its parse is
# the STUB: an empty message 1 is the whole reply. Confirmed 2026-08-20 -- the
# ack landed and the screen advanced.
#
# WARNING: AND THE BUTTON IS NOT THE ACTION. 0x01AB only OPENS the flow: the client
# goes to a nation picker (OCU / USN), then a name entry, and the choice is
# submitted as 0x01AA. Its payload was `01 00 00 00` -- the character slot, not
# a nation. So do not read "0x01AB = change nation" as "0x01AB carries the new
# nation"; nothing about the nation is in it.
#
# WARNING: WHAT THESE COMMANDS *ARE* IS MOSTLY NOT KNOWN. Only 0x01AB is tied to a
# button by observation. Answering the rest is an ACKNOWLEDGEMENT and nothing
# more -- we do not delete, create, or change anything server-side, so a client
# that is told "done" will believe something happened that did not. That is
# still better than the hang it gets now, but it is why this is a switch.
#
#: request id -> (reply id, payload bytes the client's parse method READS).
#: The size is `rep movsd` count x 4 plus the largest scalar offset, measured in
#: each parse -- and per the 0x0166 lesson, read PAST the block copy: several of
#: these take a scalar from outside the copied range.
LOBAPI = {
    0x018A: (0x018B, 0),          # parse = stub
    0x018D: (0x018E, 22872),      # payload+0x24, 0x164D dwords, + a u32 at +0x20
    0x0194: (0x0195, 0),          # parse = stub
    0x0196: (0x0197, 0),          # parse = stub
    0x019C: (0x019D, 36),         # payload+0x00, 9 dwords
    0x01A2: (0x01A3, 14468),      # payload+0x44, 0xE10 dwords, + a u32 at +0x00
    0x01A4: (0x0001, 0),          # parse = stub
    0x01A6: (0x0001, 0),          # parse = stub
    0x01A8: (0x01A9, 1060),       # payload+0x00, 0x109 dwords
    #: VERIFIED: CREATE CHARACTER, live 2026-08-20 -- it carries the 52-byte record
    #: decoded above (id, first, last, nation). Its parse walks the LOCAL
    #: character list rather than the reply, so an empty message 1 is enough.
    0x01AA: (0x0001, 0),
    0x01AB: (0x0001, 0),          # "Change Nations" -- parse = stub
    0x01AC: (0x01AD, 68),         # payload+0x00, 0x11 dwords
    0x01AE: (0x0001, 0),          # parse = stub
    0x01B0: (0x0001, 0),          # parse = stub
    0x01B2: (0x01B3, 1300),       # payload+0x14, 0x140 dwords, + u32s at +0x0C/+0x10
    0x01B5: (0x01B6, 4),          # payload+0x00 only
    0x01B7: (0x01B8, 4),          # payload+0x00 only
    0x01BB: (0x01BC, 4),          # payload+0x00 only
    0x01BD: (0x01BE, 4),          # payload+0x00 only
    #: THE MISSION BLOCK -> lobby+0x5C7E. Served by reply_01c1() when
    #: FMO_MISSION_BLOCK=1 (see MB_* above); otherwise the zeros below, which
    #: is what it has always been. It does NOT start the battle scene -- the
    #: sortie is 0x0139 -> 0x013A, unanswered here.
    0x01C0: (0x01C1, 3428),       # payload+0x18, 0x352 dwords, + u32s at +0xD60
    0x01C2: (0x01C3, 3012),       # payload+0x04, 0x2F0 dwords, + a u32 at +0x00
    0x01C4: (0x0001, 0),          # parse = stub
    0x01C6: (0x018F, 724),        # payload+0x20, 0xAD dwords
}


def parse_lobapi_mark(spec):
    """`FMO_LOBAPI_MARK` -> {request id: base}. Pure, so the selftest can drive
    the ARMED path without touching the environment -- the armed path is the
    one that had the import-order bug, and an unset knob never exercises it.

    Accepts a request id (a LOBAPI key) or that request's reply id, because the
    reply id is what a log line shows you. Raises ValueError with a sentence
    that says what to type; the caller turns that into a SystemExit."""
    out = {}
    for q in (spec or "").split(","):
        q = q.strip()
        if not q:
            continue
        ident, _, base = q.partition(":")
        try:
            ident = int(ident, 0)
            base = int(base, 0) if base.strip() else MARK_BASE_DEFAULT
        except ValueError:
            raise ValueError(f"FMO_LOBAPI_MARK entry {q!r} wants "
                             f"`<id>[:<base>]`, both integers")
        if ident not in LOBAPI:
            back = [r for r, (rep, _n) in LOBAPI.items()
                    if rep == ident and rep != 1]
            if not back:
                raise ValueError(f"FMO_LOBAPI_MARK: 0x{ident:04X} is neither a "
                                 f"lobby-API request nor one of their reply ids")
            ident = back[0]
        if LOBAPI[ident][1] == 0:
            raise ValueError(f"FMO_LOBAPI_MARK: 0x{ident:04X} has a zero-length "
                             f"reply (its parse is a stub) -- nothing to mark")
        out[ident] = base
    return out


try:
    MARK_LOBAPI = parse_lobapi_mark(os.environ.get("FMO_LOBAPI_MARK", ""))
except ValueError as _e:
    raise SystemExit(str(_e))


# Called at run time only; imported last so that import cycles resolve.
from . import cosmetics, missionlist, squadron  # noqa: E402
