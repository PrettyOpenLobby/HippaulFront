"""The lobby push queue and the smaller pushes: fees, item announce, group ended, resupply."""
import os
import struct
from .knobs import _env_int


#: WARNING: CORRECTED 2026-09-09. This used to read "the seven unsolicited ids the
#: lobby dispatcher 0x6117ECC8 will even look at". It is not seven and 0x6117ECC8
#: is not the dispatcher: it is the FIRST OF TWO jump tables inside it. The
#: dispatcher is 0x6117DFBD -- a binary search with nine direct-compare arms
#: that bottoms out in table 1 (0x016B..0x0188, seven live arms, the tuple
#: below) and table 2 (0x019A..0x01C7, FOURTEEN live arms, never enumerated
#: here at all). a walk of both jump tables is the source of
#: this constant.
#:
#: This mattered: 0x015A (the battle RESULT, the message that pays the player)
#: is one of the direct-compare arms, and while this comment stood it read as
#: an id nobody sends -- so the game-loop plan's stage 15 recorded it as "never seen inbound"
#: and stayed unbuilt. (an empty search is not an absence)
#:
#: WARNING: An id NOT in LOBBY_PUSH_ALL is dropped at 0x6117F86D in total silence: no
#: error box, no log line, nothing back on the wire. Check membership before
#: inventing a push, because the failure is invisible from our side.
LOBBY_PUSH_TABLE1 = (0x016B, 0x016C, 0x0174, 0x0178, 0x017B, 0x017D, 0x0188)
LOBBY_PUSH_TABLE2 = (0x019A, 0x019B, 0x019E, 0x019F, 0x01A1, 0x01A7, 0x01AD,
                     0x01AF, 0x01B1, 0x01B9, 0x01BA, 0x01BF, 0x01C5, 0x01C7)
LOBBY_PUSH_DIRECT = (0x0002, 0x014A, 0x014B, 0x014C, 0x014E, 0x0153, 0x015A,
                     0x016A, 0x0199)
LOBBY_PUSH_ALL = tuple(sorted(LOBBY_PUSH_DIRECT + LOBBY_PUSH_TABLE1
                              + LOBBY_PUSH_TABLE2))
#: Kept under the old name: every existing reader means table 1.
LOBBY_PUSH_IDS = LOBBY_PUSH_TABLE1
#: The dtor's leave rides the periodic queue with this fixed sequence (same
#: path as 0x0198); nothing polls for its reply.
QUEUE_SEQ = 0x7FFFFFFE

# --------------------------------------------------------------------------- #
# THE REST OF THE PUSH CATALOGUE (static 2026-09-09, third pass)
# --------------------------------------------------------------------------- #
#: The dispatcher walk lists 30 ids the lobby dispatcher 0x6117DFBD acts on.
#: After 0x015A (the result) and 0x014B (the message window) landed, EIGHTEEN of
#: them still had no emitter anywhere in this file, and the two request-side
#: ids the driven sweep left open (0x012D, and the 0x0198 keepalive's missing
#: reply) sat next to them. This section reads every one of those arms, offset by
#: offset, and builds the bodies whose layout is FULLY read. What is only partly read
#: is written down in PUSH_NOTES and reachable through FMO_PUSH_PROBE, never
#: served by default.
#:
#: WARNING: EVERY KNOB HERE DEFAULTS OFF. Nothing in this section has been near a
#: live client. The bar for each is in the FINDINGS doc, one screen per push.
#:
#: KEY: TWO GATES recur across the arms and decide WHERE a push can land at all.
#: An arm whose gate fails drops the frame at 0x6117F86D in total silence:
#:   BATTLE gate  0x611734E0(lobby) = [lobby+0x20]==4 && [lobby+0x24] not in
#:                {0,3} -- i.e. the battle scene. Carried by 0x014C 0x016B
#:                0x0178 0x017B 0x0188 0x019B. In the LOBBY these do NOTHING.
#:   SCENE-4 gate [lobby+0x20]==4 alone: 0x016A 0x0199 (plus +0x24 != 6) 0x01A7
#:                0x019A.
#:   NO gate: 0x01A1 0x019E 0x01AF 0x01B1 0x01C5 0x01C7 0x01B9 0x01BA 0x01BF.
#: The payload offsets below are PAYLOAD offsets; the arms read the frame
#: (`ebp`) and payload = frame+0x14 throughout.

#: EVERY REQUEST THE CLIENT CAN BUILD, with its payload size -- both packet
#: builders (0x61199FC0 via sendscan.py: 55 sites; 0x61199F70 via
#: `callscan.py 0x61199f70`: 28 sites), 76 distinct ids. A size of None is a
#: site whose length is computed (the selftest drives those with 64 zeros).
#: The selftest drives every one of these through Session.on_packet and pins
#: the set that falls to "no handler" -- the instrument that found the 09-09
#: defects, made permanent so the list cannot silently grow again.
CLIENT_REQUESTS = {
    0x000A: 800, 0x0065: 40, 0x012D: 4, 0x012E: None, 0x0130: 20, 0x0132: None,
    0x0137: 8, 0x0139: 80, 0x013B: 1, 0x013D: 4, 0x013E: 80, 0x013F: 4,
    0x0140: 0, 0x0142: 0x28, 0x0143: 4, 0x0144: 0xD0, 0x0145: 0x1A0,
    0x0146: 4, 0x0147: 0, 0x014D: None, 0x0150: 12, 0x0151: 12, 0x0152: 4,
    0x0154: 12, 0x0156: 0x124, 0x0157: 0xC, 0x0159: 1432, 0x015B: 301,
    0x015E: 0x14, 0x0160: 0x1C, 0x0162: 0x14, 0x0165: 16, 0x0167: 4428,
    0x0168: 24, 0x0169: 40, 0x016D: 72, 0x016E: 16, 0x0170: 228,
    0x0171: 0x14, 0x0172: 0x14, 0x0173: 0xD, 0x0175: 0, 0x0177: 56,
    0x0179: 0x10, 0x017C: 20, 0x017E: 40, 0x0181: 0x41A, 0x0182: 0, 0x018A: 124,
    0x018D: 32, 0x0190: 0x14, 0x0192: 0xC8, 0x0194: 364, 0x0196: 36,
    0x019C: 0, 0x01A0: 0x18, 0x01A2: 8, 0x01A4: 24, 0x01A6: 508, 0x01A8: 12,
    0x01AA: 56, 0x01AB: 20, 0x01AC: 80, 0x01AE: 24, 0x01B0: 24, 0x01B2: 20,
    0x01B5: 36, 0x01B7: 32, 0x01BB: 32, 0x01BD: 160, 0x01C0: 36, 0x01C2: 36,
    0x01C4: 48, 0x01C6: 36, 0x01F4: 0, 0x01F6: 0x88, 0x0321: 80,
}
#: The requests that fall to "no handler" ON PURPOSE. 0x000A and 0x0140 are
#: fire-and-forget senders (0x61173E50 / 0x61173D70 send and `ret`, no poll);
#: 0x0142/0x0143/0x0145 were here until 2026-09-26: they are the trade
#: screen's FIRST requests and the tick waits for them with no timeout, so
#: they are answered now (on_trade). Anything else here is a regression.
UNSERVED_BY_DESIGN = frozenset({0x000A, 0x0140})


#: 0x01A1 -- THE FEE PUSH (arm 0x6117F6B9, NO gate). The DEBIT half of the
#: economy; 0x015A is the credit half. It is a STORE, not a delta:
#:   +0x00 u32  MONEY -> lobby+0x88C, unconditionally (send the NEW balance)
#:   +0x04 u32  sortie cost; 0 = say nothing. Nonzero and
#:   +0x08 u8   == 0 -> 8:74 "Paid H$%d as the sortie cost for a modified unit"
#:              != 0 -> the cost was REFUSED: 8:85 "This Arena forbids modified
#:              units..." when 0x610027E0 says arena, else 8:75 "The sector's
#:              minimum BG cost is below the modified unit's base cost..."
#:   +0x0C u32  Coliseum entry fee     -> 8:82 "Paid H$%d as the Coliseum entry fee."
#:   +0x10 u32  Coliseum spectator fee -> 8:84 "Paid H$%d as the Coliseum spectator fee."
#: Because +0x00 is a store, a fee push with the WRONG balance rewrites the
#: wallet; the emitter below therefore refuses to send when it cannot read the
#: pilot's stored money.
MSG_FEE_PUSH = 0x01A1
S1A1_LEN = 0x14
S1A1_MONEY = 0x00
S1A1_SORTIE_COST = 0x04
S1A1_MOD_REFUSED = 0x08
S1A1_COLISEUM_FEE = 0x0C
S1A1_SPECTATOR_FEE = 0x10
#: FMO_SORTIE_COST: 0 (default) = sorties are free and no 0x01A1 is sent. N>0
#: = every granted 0x013A also debits N from the stored wallet and pushes the
#: new balance with the 8:74 message. The one-screen check: the wallet on the
#: Profile drops by N and STAYS dropped after a relog.
SORTIE_COST = _env_int("FMO_SORTIE_COST", "0")


def fee_push_body(money, sortie_cost=0, modified_refused=0, coliseum_fee=0,
                  spectator_fee=0):
    b = bytearray(S1A1_LEN)
    struct.pack_into("<I", b, S1A1_MONEY, int(money) & 0xFFFFFFFF)
    struct.pack_into("<I", b, S1A1_SORTIE_COST, int(sortie_cost) & 0xFFFFFFFF)
    b[S1A1_MOD_REFUSED] = 1 if modified_refused else 0
    struct.pack_into("<I", b, S1A1_COLISEUM_FEE, int(coliseum_fee) & 0xFFFFFFFF)
    struct.pack_into("<I", b, S1A1_SPECTATOR_FEE, int(spectator_fee) & 0xFFFFFFFF)
    return bytes(b)


def fee_push_packet(conn_id, **fields):
    return packet.build(MSG_FEE_PUSH, fee_push_body(**fields), QUEUE_SEQ, conn_id)


#: 0x019B -- ITEM ANNOUNCE (arm 0x6117F2BC, BATTLE gate): 8:87 "%s.%s obtained
#: %s." for ANOTHER unit's pickup. +0x18 u32 unit id, compared with lobby+0x1DC
#: (self) -- equal = dropped, so this can never announce your own pickup.
#: +0x08 u16 item id and +0x0A u8 item kind go to the name lookup 0x61175840
#: (the same (kind, id) pair the owned-items rows carry at +0x08/+0x0A, so the
#: first 24 bytes are shaped like an item record). +0x1C first name, +0x2D last
#: name, 17 bytes each, cp932. Minimum body 0x3E.
MSG_ITEM_ANNOUNCE = 0x019B
S19B_LEN = 0x3E
S19B_ITEM_ID = 0x08
S19B_ITEM_KIND = 0x0A
S19B_UNIT = 0x18
S19B_FIRST = 0x1C
S19B_LAST = 0x2D
S19B_NAME_LEN = 0x11


def _cp932_field(text, width):
    raw = str(text or "").encode("cp932", "replace")[:width - 1]
    return raw.ljust(width, b"\0")


def item_announce_body(unit_id, item_id, kind, first, last):
    b = bytearray(S19B_LEN)
    struct.pack_into("<H", b, S19B_ITEM_ID, int(item_id) & 0xFFFF)
    b[S19B_ITEM_KIND] = int(kind) & 0xFF
    struct.pack_into("<I", b, S19B_UNIT, int(unit_id) & 0xFFFFFFFF)
    b[S19B_FIRST:S19B_FIRST + S19B_NAME_LEN] = _cp932_field(first, S19B_NAME_LEN)
    b[S19B_LAST:S19B_LAST + S19B_NAME_LEN] = _cp932_field(last, S19B_NAME_LEN)
    return bytes(b)


#: 0x0178 -- BATTLE GROUP MEMBERSHIP ENDED (arm 0x6117EF19, BATTLE gate). The
#: push that tells a MEMBER what the leader did. +0x00 u32 = the id of a
#: pending group WINDOW: 0x61175BF0 matches it against [win+0xE0] over the
#: three window slots 0x613CA3F8 (battle group) / 0x613CA3FC / 0x613CA400
#: (the two mission-group forms), and NO open window = dropped. +0x04 u32 =
#: the reason, jump table 0x6117F8FC, and the message depends on which slot
#: matched:
#:   0 LEFT            8:4  / 8:68 (mission group, leader) / 8:70 (member)
#:   1 DISBANDED       8:5  / 8:69 / 8:71
#:   2 KICKED          8:6  "You were kicked from the battle group."
#:   3 (no message)
#:   4 AUTO-DISBANDED  8:57 "The battle group was automatically disbanded."
#:   5 AUTO-REMOVED    8:59 "You were automatically removed from the battle group."
#: then the window's vtable slot 1 is called with 1 (close) and the slot is
#: cleared. KEY: [slot+0xE0] is the GroupID: the group window's own requests
#: send it as theirs (0x0173/0x01A0 +0x00, 0x0171/0x0179 +0x04), so +0x00 is
#: the GroupID (2026-09-30). Sent by battlegroups.notify_removed for a kick
#: (2) and the sortie auto-removal (5); not yet seen on a screen.
MSG_GROUP_ENDED = 0x0178
S178_LEN = 0x08
GROUP_ENDED_REASONS = {0: "LEFT", 1: "DISBANDED", 2: "KICKED", 3: "(silent)",
                       4: "AUTO-DISBANDED", 5: "AUTO-REMOVED"}


def group_ended_body(window_id, reason):
    if reason not in GROUP_ENDED_REASONS:
        raise ValueError(f"0x{MSG_GROUP_ENDED:04X}: reason {reason} is past the "
                         f"jump table 0x6117F8FC (0..5); 0x6117EF4E `cmp ebp,5; "
                         f"ja` skips the message and still closes the window")
    return struct.pack("<II", int(window_id) & 0xFFFFFFFF, reason)


#: 0x0188 -- KYTCP_COMMAND_CLI_REQ_LOGIN_NOTIFY (arm 0x6117EE25, BATTLE gate):
#: SE's own debug string at 0x6133BBA8 names it, and complains when "the
#: sector name is empty. MsnLSID(%d)". The arm posts 8:6 FIRST (yes, "You were
#: kicked from the battle group." -- read off the code, not understood), then
#: 8:50 "The battle group sortied for %s." with the cp932 string at +0x08 when
#: it is non-empty (+0x04 = MsnLSID, only printed by the compiled-out debug).
#: Then, only if 0x610027E0 is true and [lobby+0x24]==0xD: lobby+0x6E42 == 0
#: -> 0x611B03D0(0) (a two-global latch 0x61397493/0x61397494), else the
#: flag is cleared. Builder only -- the state-0xD path is unmapped.
MSG_GROUP_LOGIN_NOTIFY = 0x0188
S188_MIN_LEN = 0x30
S188_MSNLSID = 0x04
S188_SECTOR = 0x08


def group_login_notify_body(sector_name, msn_lsid=0, a=0):
    b = bytearray(S188_MIN_LEN)
    struct.pack_into("<II", b, 0, int(a) & 0xFFFFFFFF, int(msn_lsid) & 0xFFFFFFFF)
    b[S188_SECTOR:S188_MIN_LEN] = _cp932_field(sector_name, S188_MIN_LEN - S188_SECTOR)
    return bytes(b)


#: 0x01A7 -- RESUPPLY (arm 0x6117F796). Gate: [lobby+0x20]==4 and lobby+0x1DC
#: (own unit id) != 0. Copies 0x7F dwords = 508 B and walks it:
#:   +0x000 s32  row count
#:   +0x008      rows, 0x18 apart -- the 24-byte ITEM RECORD. Only rows with
#:               byte +0x12 == 1 are processed: 0x61177BE0(serial lo, hi, 0)
#:               finds the OWNED row with that serial and clears ITS +0x12
#:               ("%sを使用しました" = "used %s"; -1 = missing or already
#:               clear -> the row is skipped), then the name for (+0x0A kind,
#:               +0x08 id), then the 8 bytes at row+0xF0/+0xF4: nonzero = a
#:               STOCK serial consumed through 0x6117A640 -> 8:67 "Replenished
#:               %s from stock items."; zero -> 8:66 "Replenished %s."
#:   +0x1E8 u32  MONEY -> lobby+0x88C, a STORE (send the new balance)
#: WARNING: row+0xF0 is relative to the ROW, so with the 0x18 stride the stock serial
#: of row i sits at +0x0F8 + 0x18*i -- inside the row area once i >= 10. The
#: builder caps at 10 rows for that reason. Which client request precedes a
#: resupply is not read, so this is a builder without an emitter.
MSG_RESUPPLY = 0x01A7
S1A7_LEN = 0x1FC
S1A7_COUNT = 0x000
S1A7_ROWS = 0x008
S1A7_ROW_LEN = 0x18
S1A7_ROW_FLAG = 0x12
S1A7_STOCK_OFF = 0xF0
S1A7_MONEY = 0x1E8
S1A7_MAX_ROWS = 10


def resupply_body(money, rows=()):
    """rows: (record24, stock_serial8_or_None) pairs."""
    if len(rows) > S1A7_MAX_ROWS:
        raise ValueError(f"0x{MSG_RESUPPLY:04X}: {len(rows)} rows, cap "
                         f"{S1A7_MAX_ROWS} (row+0xF0 lands inside the row area)")
    b = bytearray(S1A7_LEN)
    struct.pack_into("<i", b, S1A7_COUNT, len(rows))
    for i, (rec, stock) in enumerate(rows):
        if len(rec) != S1A7_ROW_LEN:
            raise ValueError(f"row {i} is {len(rec)}B, want {S1A7_ROW_LEN}")
        at = S1A7_ROWS + i * S1A7_ROW_LEN
        b[at:at + S1A7_ROW_LEN] = rec
        b[at + S1A7_ROW_FLAG] = 1
        if stock:
            if len(stock) != 8:
                raise ValueError(f"row {i} stock serial is {len(stock)}B, want 8")
            b[at + S1A7_STOCK_OFF:at + S1A7_STOCK_OFF + 8] = stock
    struct.pack_into("<I", b, S1A7_MONEY, int(money) & 0xFFFFFFFF)
    return bytes(b)


#: READ BUT NOT BUILT -- the arms whose meaning is not settled. Reachable
#: through FMO_PUSH_PROBE for a one-shot live look; nothing serves them.
PUSH_NOTES = {
    0x016A: "arm 0x6117EC6D, scene-4 gate: +0x00 u32 = length, the bytes at +0x10 "
            "are malloc'd and held at lobby+0x3665 (the previous one is freed). "
            "Consumer unread (readers at 0x61173526 0x61174D0A 0x61176300 "
            "0x6117A4FD).",
    0x017B: "arm 0x6117ECE4, BATTLE gate: +0x00 u32 -> lobby+0x7934, then 0x8C "
            "dwords (560 B) from +0x24 -> lobby+0x7938. Readers 0x61191EB2 / "
            "0x61191F67 / 0x61191F9C, unread.",
    0x019E: "arm 0x6117F629, no gate: +0x00 u8 -> lobby+0x7DEF; >= 5 sets "
            "globals+0x31CA and +0x2EA8 to 1, else 0. Readers 0x611E4B05 / "
            "0x611E5B2B, unread.",
    0x01C7: "arm 0x6117F66D, no gate: 0x18 dwords (96 B) from +0x00 -> "
            "lobby+0x6DDA. No other reader of that offset found by displacement.",
    0x01B9: "KYTCP_COMMAND_CLI_COL_ADD_UPDATE (Coliseum): +0x00 result, the "
            "128-byte arena record from +0x04 to 0x611B70C0 (the waiting window). "
            "Built and served by coliseum.py (FMO_COLISEUM).",
    0x01BA: "KYTCP_PACKET_DATA_CLI_COL_CANCEL_UPDATE: +0x00 result -> "
            "0x611B03D0(result) when lobby+0x6E42 == 0, else clears it. Built by "
            "coliseum.py (coliseum.CANCEL_TEXT).",
    0x01BF: "KYTCP_COMMAND_CLI_COL_RET_UPDATE: +0x00 s32 result; < 0 -> 'Error "
            "occurred; stopping streak' with the systext for (u16)result.",
    0x017D: "the trade push -- see MSG_TRADE_PUSH; the trade service is unbuilt "
            "by design.",
}


def lobby_push_packet(mid, body, conn_id):
    """Any push on the queue sequence -- refused when the dispatcher would drop
    it, because that failure is otherwise invisible from this side."""
    if mid not in LOBBY_PUSH_ALL:
        raise ValueError(f"0x{mid:04X} is not one of the {len(LOBBY_PUSH_ALL)} ids "
                         f"the lobby dispatcher 0x6117DFBD acts on; it would be "
                         f"dropped at 0x6117F86D in silence")
    return packet.build(mid, bytes(body), QUEUE_SEQ, conn_id)


def parse_push_probe(spec):
    """FMO_PUSH_PROBE='0x019E:05' -> (0x019E, b'\\x05'); '' -> None. Raises
    ValueError for anything the dispatcher would drop or that is not hex."""
    spec = (spec or "").strip()
    if not spec:
        return None
    try:
        mid_s, hex_s = spec.split(":", 1)
        mid = int(mid_s, 0)
        body = bytes.fromhex(hex_s.strip() or "")
    except ValueError:
        raise ValueError(f"FMO_PUSH_PROBE={spec!r}: want <id>:<hex bytes>")
    if mid not in LOBBY_PUSH_ALL:
        raise ValueError(f"FMO_PUSH_PROBE: 0x{mid:04X} is not in the push "
                         f"catalogue; the client would drop it in silence")
    return mid, body


#: FMO_PUSH_PROBE: unset (default) = nothing. '<id>:<hex>' = ONE frame with
#: that id and body on the queue sequence, on this session's first keepalive
#: (the same point the ANNOUNCE positive control lands). For the PUSH_NOTES
#: arms and for any body experiment; the catalogue check is the only guard.
try:
    PUSH_PROBE = parse_push_probe(os.environ.get("FMO_PUSH_PROBE", ""))
except ValueError as _e:
    PUSH_PROBE = None
    _PUSH_PROBE_ERR = str(_e)
else:
    _PUSH_PROBE_ERR = ""


# Called at run time only; imported last so that import cycles resolve.
from . import packet  # noqa: E402
