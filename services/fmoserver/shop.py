"""Buying, selling and granting items: the item counter (0x0169, 0x017E) and the 0x016B mint
push."""
import os
import struct
import time
from . import inventory


#: VERIFIED: THE GARAGE, decoded 2026-08-24 live+static ("choose your wanzer" hung the
#: client on our silence -- `no handler for msg=0x0167`, 03:44:00Z).
#:
#: 0x0167 = SETUP SAVE. Sent by 0x611784C2 (and 0x61174BFC), payload 0x114C:
#:   +0x00 byte  lobby+0x3df2      +0x01 byte  (caller arg)
#:   +0x02 u16   lobby+0x4ef7      +0x04 u16   lobby+0x4ef5
#:   +0x08       the WHOLE 8 x 0x220 setup array (rep movsd 0x440 from
#:               lobby+0x3df3), then at +0x1108 a u32 + byte tail
#:               (lobby+0x3669/+0x366d)
#:   -- which is BYTE-FOR-BYTE the 0x0166 reply body (0x1100 + 5 = 4357).
#:   Before sending, 0x61178506 deletes any setup with missing parts (SE's own
#:   "Setup No.%d had missing parts" print). The sender then waits in state 2
#:   of the machine at lobby+0x73ee, whose poll arm 0x61178404 asserts
#:   kycli_lobmain.cpp:0x1812 and takes `word [rx+6] == 1` as success -- an
#:   EMPTY MESSAGE 1, same convention as the delete. So: store the block on
#:   the played character, serve it back verbatim in 0x0166, and the garage
#:   round-trips.
#:
#: 0x0168 = ACQUIRE PART. Built at 0x611788F6 (24B payload: u16 id, u8 kind,
#:   u32 -- copied from the pending-transaction object at lobby+0x73fa).
#:   Machine at lobby+0x73f6; its poll arm 0x611787C2 wants reply id 0x016B:
#:     +0x28 u32   gate -- zero takes the failure arm at 0x6117884C
#:     +0x2C 24B   an ITEM RECORD, copied into the transaction and APPENDED to
#:                 the client's inventory via 0x61177B30 ("[Debug] acquired %s")
#:     the client then re-reads id (+0x34 = record+0x08) and kind (+0x36 =
#:     record+0x0A) out of it.
#:   The record needs a UNIQUE 64-bit serial -- the client finds items by
#:   serial (0x61177BE0) and a duplicate is "one item seen twice".
MSG_SETUP_SAVE = 0x0167
MSG_ACQUIRE = 0x0168
MSG_ACQUIRE_REPLY = 0x016B
ACQ_GATE, ACQ_RECORD = 0x28, 0x2C
REPLY_016B_LEN = ACQ_RECORD + inventory.INV_ENTRY_LEN    # 0x44 -- covers every read

#: KEY: 0x016B HAS A SECOND SHAPE: the unsolicited MINT push (static 2026-09-12).
#: The same id reaches a different consumer depending on the sequence -- the
#: shop's own poller takes the reply above, and the LOBBY DISPATCHER's arm
#: 0x6117ED22 takes a queue-sequence push. That arm is the "the server gives
#: you an item" path, and it is why the client can say 8:2 "Obtained %s.
#: Adding to stock." without anyone buying anything. Read off the arm
#: (packet offset = payload offset + 0x14):
#:     +0x08 u32   how many records to ADD (packet+0x1C). The loop appends
#:                 each through 0x61177B30 -- the same append the acquire uses,
#:                 which caps the list at 400 -- then names it with
#:                 0x61175840(kind, id) and prints 8:2.
#:     +0x14 u32   the TOTAL (packet+0x28). Records from the ADD count up to
#:                 this are walked by a SECOND loop (0x6117ED90) that reads the
#:                 same id field; set it equal to the add count so that loop
#:                 does nothing, which is all we have earned.
#:     +0x18 + i*24  the 24-byte item records (packet+0x2C; the arm's own
#:                 `lea eax,[esi-8]` with esi at record+8 pins the base).
#: Gate: 0x611734E0 -- [lobby+0x20]==4 and [lobby+0x24] not in {0,3}, the same
#: in-world test every other push here rides. WARNING: NEVER BEEN ON A WIRE.
MINT_ADDS = 0x08
MINT_TOTAL = 0x14
MINT_RECORDS = 0x18


def item_mint_payload(records):
    """The 0x016B PUSH body granting `records` (24-byte item records)."""
    b = bytearray(MINT_RECORDS + len(records) * inventory.INV_ENTRY_LEN)
    struct.pack_into("<I", b, MINT_ADDS, len(records))
    struct.pack_into("<I", b, MINT_TOTAL, len(records))
    for i, r in enumerate(records):
        off = MINT_RECORDS + i * inventory.INV_ENTRY_LEN
        b[off:off + inventory.INV_ENTRY_LEN] = bytes(r)[:inventory.INV_ENTRY_LEN]
    return bytes(b)

#: Serial mint for acquired items: seconds in the high dword, a process
#: counter in the low -- monotonic across restarts at one-second granularity,
#: unique within a run regardless.
#:
#: WARNING: THE LOW DWORD STARTS ABOVE THE GARAGE'S OWN SERIALS, and that is the whole
#: point of the base. The counter reset to 0 on every container restart, so the
#: FIRST purchase of a run always minted low dword **1** -- and the synthesized
#: garage block hands its equipped parts serials 1..11 (`inventory_from_setups`).
#: Live 2026-09-11T23:39:42Z: the first ever shop purchase (Arco, kind 0x11)
#: was minted `6aa4913e:00000001` against an equipped body already holding
#: serial 1, and the client died one second later on a NULL read at 0x611A559D.
#: The find-by-serial at 0x61177BE0 does compare BOTH dwords, so that collision
#: is not provably the cause -- but a bought item sharing a low dword with an
#: equipped one is indefensible either way, and it was the only anomaly in what
#: we sent. 0x10000 is clear of anything the block can synthesize.
SERIAL_LOW_BASE = 0x10000
_serial_n = [0]


def mint_serial():
    """(low, high) for a new item. Low is `SERIAL_LOW_BASE + n` so it can never
    equal a garage serial; high is the clock, so two runs cannot repeat a pair
    unless they mint the same index inside the same second."""
    _serial_n[0] += 1
    return ((SERIAL_LOW_BASE + _serial_n[0]) & 0xFFFFFFFF,
            int(time.time()) & 0xFFFFFFFF)


# --------------------------------------------------------------------------- #
# THE ITEM COUNTER -- 0x0169 (SELL / DISCARD) and 0x017E (BUY)
# --------------------------------------------------------------------------- #
#: VERIFIED:KEY: STATIC 2026-09-09. Two sibling requests off the item counter, both
#: unserved until now, so both HUNG the client. They are near-identical on the
#: wire -- 40 bytes, a 24-byte item record at payload+0x10 copied from a pointer
#: in the lobby -- and they must be treated OPPOSITELY, for one measurable
#: reason:
#:
#:   0x0169 (record at lobby+0x7406) puts the PRICE ON THE WIRE at payload+0x0C.
#:   0x017E (record at lobby+0x743E) does NOT: its price lives at record+0x18,
#:          one dword PAST the 24 bytes the sender copies.
#:
#: So we can pay for a sale correctly and we cannot charge for a purchase at
#: all. That is not a preference, it is what the two senders do.
#:
#: 0x0169 -- SELL or DISCARD, decided by a byte the client sends us:
#:   0x6117A9AE `cmp byte [rec+0x12], 1` -- 1 = DISCARD (and payload+0x0C is
#:   forced to 0), anything else = SELL (payload+0x0C = the price). The reply
#:   handler 0x6117A893 wants **message 1**; on it the client calls 0x6117A640
#:   (find by 64-bit serial, remove from the owned list) and prints SE's own
#:   8:73 "Discarded %s." or 8:33 "Sold %s." on the same byte.
#:   KEY: THE CLIENT NEVER CREDITS ITSELF -- there is no `add [lobby+0x88C]`
#:   anywhere in that arm. The money for a sale is OURS to pay, which is exactly
#:   what the character store and the 0x015A money delta are for.
#:   Any other reply id takes 0x6117A90F: word[frame+8] -> lobby+0x7D5F, a
#:   numbered [FM...] box, state 3. Graceful, and the item is NOT removed.
#:
#: 0x017E -- BUY. Reply 0x017F, and on it 0x61179373 does
#:   `sub dword ptr [lobby+0x88C], eax` with eax = [rec+0x18]: **the client
#:   debits ITSELF, from a price we were never told.** The debit sits inside the
#:   0x017F-matched path, so any other id skips it entirely and takes the
#:   graceful numbered failure at 0x611793CA.
#:   WARNING: SO WE REFUSE IT BY DEFAULT. Answering 0x017F would take the player's
#:   money on screen for an item no server-side stock exists for, and our very
#:   next 0x014A would snap the wallet back to the stored value -- a desync that
#:   reads to a player as "the shop stole my money". A numbered refusal is
#:   honest and, either way, beats the hang they get today.
MSG_ITEM_SELL = 0x0169
MSG_ITEM_BUY = 0x017E
MSG_ITEM_BUY_REPLY = 0x017F
S169_PRICE = 0x0C                      # `mov [eax+0xc], [rec+0x18]`
S169_RECORD = 0x10                     # the 24-byte record, both messages
S169_REC_SERIAL, S169_REC_ID = 0x00, 0x08
S169_REC_KIND, S169_REC_MODE = 0x0A, 0x12
ITEM_RECORD_LEN = 24
#: The special code the client singles out on BOTH failure arms
#: (`cmp word [frame+8], 0xC922`) -- it picks between two return values. What it
#: MEANS is unread; we do not send it.
ITEM_FAIL_SPECIAL = 0xC922
#: FMO_ANSWER_0169: '1' (default) = accept a sell/discard and pay for a sell.
#: 'fail' = the graceful numbered refusal, '0' = silence (the hang, for an A/B).
ANSWER_0169 = os.environ.get("FMO_ANSWER_0169", "1").strip() or "1"
#: FMO_ANSWER_017E: 'fail' (default) = refuse a purchase gracefully -- see above.
#: '1' = answer 0x017F and let the client debit itself (a DESYNC: our next
#: 0x014A overwrites the wallet). '0' = silence (the hang).
ANSWER_017E = os.environ.get("FMO_ANSWER_017E", "fail").strip() or "fail"


def parse_item_request(payload):
    """(mode, price, serial, item_id, kind) out of an 0x0169 / 0x017E body.

    `mode` is the client's own discard/sell byte at record+0x12; 1 = DISCARD.
    Everything is read from what the CLIENT sent -- we author none of it."""
    price = struct.unpack_from("<I", payload, S169_PRICE)[0] \
        if len(payload) >= S169_PRICE + 4 else 0
    rec = payload[S169_RECORD:S169_RECORD + ITEM_RECORD_LEN]
    if len(rec) < ITEM_RECORD_LEN:
        return None, price, 0, 0, 0
    serial = struct.unpack_from("<Q", rec, S169_REC_SERIAL)[0]
    item_id = struct.unpack_from("<H", rec, S169_REC_ID)[0]
    return rec[S169_REC_MODE], price, serial, item_id, rec[S169_REC_KIND]
