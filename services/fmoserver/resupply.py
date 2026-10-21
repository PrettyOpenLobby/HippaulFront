"""The Resupply Items counter (0x01A6 -> 0x01A7) and the item-used notice (0x0180)."""
import struct
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# READ OFF THE CLIENT (FrontMissionOnline.dll.unpacked, static 2026-10-08)
# --------------------------------------------------------------------------- #
# THE USED FLAG. An owned-item record's byte +0x12 is its "used" state:
# 0x61177BE0(serial lo, hi, v) finds the record by serial in the owned list
# (lobby+0x10D9), refuses when +0x12 already equals v ("item already used
# (%d)", 0x6133B838), else writes v there and walks the eight setups
# (lobby+0x3E1B..) for the same serial. Two callers: 0x61177D00 with v = 1,
# and the 0x01A7 arm with v = 0.
#
# 0x0180, "ITEM USED" (0x61177D00): gate [lobby+0x20] == 4 and [lobby+0x24]
# != 0 (in a battle); builder 3 (0x6119A080, queue sequence, nothing polls)
# with 40 bytes, the 24-byte record at payload+0x10, then 0x61177BE0(.., 1):
# the client marks the consumable used at the moment it tells us. Its callers
# are the battle item-use paths 0x611DF549 / 0x611DF82D and a menu 0x6115C12B.
#
# 0x01A6, "RESUPPLY ITEMS" (the hangar counter, resource 34): the window's
# object (lobby+0x74F4 is its request slot) is filled by 0x6119FC90 and sent
# by 0x611A03CF through 0x61173210 (508 bytes = 0x7F dwords):
#   +0x000 s32  row count: every NON-EMPTY consumable slot (setup records
#               11..20 of the selected setup, 0x611748E0(set, slot))
#   +0x004 u32  the TOTAL PRICE the window shows (34:10 "Total Price"):
#               for every used row with no stock match, 0x611A4760(id)'s
#               price, the shop row +0x00 (economy.part_price)
#   +0x008      the rows, 0x18 apart: the slot's 24-byte record verbatim, so
#               +0x12 == 1 marks a used one
#   +0x0F8      the STOCK rows, 0x18 apart, the same index as the row: an
#               owned record of the same kind and id that is not used and not
#               equipped (0x61174860 < 0), or zeros (34:8 "From Stock")
# The reply poll 0x61172250 takes message 1 as success (34:1 "All consumable
# items were replenished.", then the window rebuilds from the lobby's own
# lists) and anything else as failure (34:2).
#
# 0x01A7, "RESUPPLY" (arm 0x6117F796, gate [lobby+0x20] == 4 = logged in):
# copies the same 508 bytes; +0x1E8 u32 MONEY -> lobby+0x88C (a STORE); for
# each of `count` rows with +0x12 == 1, 0x61177BE0(serial, 0) clears the used
# flag (owned list and setups), then: the 8 bytes at row + 0xF0 (= the stock
# row's serial, +0xF8 + 0x18 i) non-zero -> 0x6117A640 removes that stock
# item from the owned list and prints 8:67 "Replenished %s from stock
# items."; zero -> 8:66 "Replenished %s.". The request and the push are ONE
# layout, which is why pushes.resupply_body caps the rows at 10.
#
# SE (guide pages): consumables are bought at the hangar; the counter refills
# every used one at once (34:0 "Replenish all consumable items?"), from stock
# first. No page gives a price rule beyond the shop's, so a refill costs the
# item's shop price (the figure the client itself totals) -- the server's own
# shop table decides, never the client's total.
#
# OURS: an item with no shop price (event items such as the Rebirth parts,
# shop row restrict 9, buy 0) is refilled only from stock; a row the server's
# ledger never saw used (its 0x0180 lost) is still refilled and paid for, and
# logged so.

MSG_ITEM_USED = 0x0180
MSG_RESUPPLY_REQ = 0x01A6
#: FMO_RESUPPLY (default 1): 0x01A6 is answered with the refill (0x01A7, the
#: debit) and message 1; 0 = the old bare message 1 (nothing refilled).
RESUPPLY = _env_int("FMO_RESUPPLY", "1") != 0
#: FMO_ITEM_USED (default 1): 0x0180 marks the item used on the pilot's record.
ITEM_USED = _env_int("FMO_ITEM_USED", "1") != 0
REQ_LEN = 0x1FC
REQ_COUNT = 0x000
REQ_PRICE = 0x004
REQ_ROWS = 0x008
REQ_STOCK = 0x0F8
ROW_LEN = 0x18
ROW_USED = 0x12
MAX_ROWS = 10
NOTICE_REC = 0x10


def _serial(rec):
    return struct.unpack_from("<Q", rec, 0)[0]


def parse_request(payload):
    """[(row record, stock record or None)] for the used rows of an 0x01A6
    body, and the client's total price. Pure."""
    b = bytes(payload or b"")
    if len(b) < REQ_LEN:
        b = b + bytes(REQ_LEN - len(b))
    n = max(0, min(MAX_ROWS, struct.unpack_from("<i", b, REQ_COUNT)[0]))
    price = struct.unpack_from("<I", b, REQ_PRICE)[0]
    rows = []
    for i in range(n):
        rec = b[REQ_ROWS + ROW_LEN * i:REQ_ROWS + ROW_LEN * (i + 1)]
        if rec[ROW_USED] != 1 or not _serial(rec):
            continue
        st = b[REQ_STOCK + ROW_LEN * i:REQ_STOCK + ROW_LEN * (i + 1)]
        rows.append((rec, st if _serial(st) else None))
    return rows, price


def _set_used(char, serial, value):
    """Write the used byte of every record carrying `serial`: the eight
    setups (char['setups']) and the stored items. Returns how many."""
    from . import inventory
    n = 0
    raw = (char or {}).get("setups")
    if raw:
        block = bytearray(bytes.fromhex(raw))
        for s in range(inventory.SETUP_SLOTS):
            base = s * inventory.SETUP_ENTRY_LEN + inventory.SETUP_ITEM_OFF
            for i in range(inventory.SETUP_ITEMS):
                at = base + i * ROW_LEN
                if at + ROW_LEN <= len(block) and _serial(block[at:at + ROW_LEN]) == serial:
                    block[at + ROW_USED] = value
                    n += 1
        if n:
            char["setups"] = bytes(block).hex()
    for it in (char or {}).get("items") or ():
        try:
            if int(it.get("serial", -1)) != serial:
                continue
            rec = bytearray(inventory.item_record(serial, int(it["id"]), int(it["kind"])))
            try:
                old = bytes.fromhex(it.get("rec") or "")
            except (TypeError, ValueError):
                old = b""
            if len(old) == ROW_LEN and old[:inventory.ITEM_KIND + 1] == bytes(rec[:inventory.ITEM_KIND + 1]):
                rec = bytearray(old)
            rec[ROW_USED] = value
            it["rec"], it["used"] = bytes(rec).hex(), int(value)
            n += 1
        except (KeyError, TypeError, ValueError):
            continue
    return n


def is_used(char, serial):
    """True when the server's ledger has this serial marked used."""
    from . import inventory
    raw = (char or {}).get("setups")
    if raw:
        block = bytes.fromhex(raw)
        for s in range(inventory.SETUP_SLOTS):
            base = s * inventory.SETUP_ENTRY_LEN + inventory.SETUP_ITEM_OFF
            for i in range(inventory.SETUP_ITEMS):
                at = base + i * ROW_LEN
                if _serial(block[at:at + ROW_LEN]) == serial and block[at + ROW_USED] == 1:
                    return True
    return any(int(it.get("serial", -1)) == serial and it.get("used")
               for it in (char or {}).get("items") or () if isinstance(it, dict))


def on_item_used(sess, p):
    """0x0180: the client used a consumable; mark it on the pilot's record.
    Nothing is owed back (builder 3, no poll)."""
    from . import charstore
    pl = bytes(p["payload"])
    if not ITEM_USED or len(pl) < NOTICE_REC + ROW_LEN:
        return False
    rec = pl[NOTICE_REC:NOTICE_REC + ROW_LEN]
    serial, iid, kind = _serial(rec), struct.unpack_from("<H", rec, 8)[0], rec[0x0A]
    char = sess.playing_char() if charstore.CHAR_STORE else None
    if char is None or not serial:
        log(f"{sess.peer}   0x{MSG_ITEM_USED:04X} = ITEM USED, serial {serial:#x} "
            f"(kind {kind:#04x} id {iid}): no pilot on file, nothing marked")
        return False
    n = _set_used(char, serial, 1)
    if n:
        try:
            sess.commit(f"item {serial:#x} (kind {kind:#04x} id {iid}) used in battle")
        except Exception as e:
            log(f"{sess.peer}   WARNING: item used NOT banked ({e!r})")
    log(f"{sess.peer}   0x{MSG_ITEM_USED:04X} = ITEM USED (0x61177D00): serial "
        f"{serial:#x} kind {kind:#04x} id {iid}; the client set its +0x12 = 1, "
        f"{n} record(s) on file marked used"
        + ("" if n else " -- WARNING: the pilot's record holds no such serial"))
    return True


def plan(char, rows, prices=None):
    """[(row, stock or None, price, why)] the server will refill, and the
    total. `prices` = {(kind, id): price} for the selftest, else the shop
    table. Pure apart from reading `char`."""
    from . import economy, inventory
    owned = {it["serial"]: it for it in inventory.stored_items(char)}
    out, total, taken = [], 0, set()
    for rec, stock in rows:
        iid, kind = struct.unpack_from("<H", rec, 8)[0], rec[0x0A]
        if stock is not None:
            ss = _serial(stock)
            it = owned.get(ss)
            if (it is not None and it["kind"] == kind and it["id"] == iid
                    and not it.get("used") and ss != _serial(rec) and ss not in taken):
                taken.add(ss)
                out.append((rec, stock, 0, f"from stock {ss:#x}"))
                continue
        if prices is not None:
            price = int(prices.get((kind, iid), 0))
        else:
            price = int(economy.part_price(kind, iid, char)[0] or 0)
        if price <= 0:
            out.append((rec, None, None, "no shop price and no stock: not refilled"))
            continue
        out.append((rec, None, price, f"H${price} (shop price)"))
        total += price
    return out, total


def on_request(sess, p):
    """0x01A6: refill every used consumable; returns the packets (the 0x01A7
    push, then message 1), or a refusal; None = leave it to the stub."""
    from . import charselect, charstore, inventory, packet, pushes
    if not RESUPPLY:
        return None
    char = sess.playing_char() if charstore.CHAR_STORE else None
    rows, client_total = parse_request(p["payload"])
    if char is None:
        log(f"{sess.peer}   0x{MSG_RESUPPLY_REQ:04X} = RESUPPLY ITEMS, {len(rows)} used "
            f"row(s): no pilot on file -- the bare message 1 (nothing refilled)")
        return None
    todo, total = plan(char, rows)
    money = (sess.stored_money() or (0, 0))[0]
    lines = [f"{struct.unpack_from('<H', r, 8)[0]}/{r[0x0A]:#04x} serial {_serial(r):#x}: "
             f"{why}{'' if is_used(char, _serial(r)) else ' (the ledger had it unused)'}"
             for r, _s, _pr, why in todo]
    if total > money:
        log(f"{sess.peer}   0x{MSG_RESUPPLY_REQ:04X} = RESUPPLY ITEMS: H${total} > "
            f"H${money} on file -> 0x{charselect.MSG_FAIL:04X} code "
            f"{charselect.FAIL_CODE} (34:2 'Failed to replenish'); rows: "
            + "; ".join(lines))
        return [packet.build(charselect.MSG_FAIL, b"", sess.reply_seq(), charselect.FAIL_CODE)]
    done = [(r, s) for r, s, pr, _w in todo if pr is not None]
    if rows and not done:
        # message 1 would draw 34:1 "All consumable items were replenished."
        # over a refill that did not happen
        log(f"{sess.peer}   0x{MSG_RESUPPLY_REQ:04X} = RESUPPLY ITEMS: none of the "
            f"{len(rows)} used row(s) can be refilled (no shop price on file, no "
            f"stock) -> 0x{charselect.MSG_FAIL:04X} (34:2); rows: " + "; ".join(lines))
        return [packet.build(charselect.MSG_FAIL, b"", sess.reply_seq(), charselect.FAIL_CODE)]
    for r, s in done:
        _set_used(char, _serial(r), 0)
        if s is not None:
            inventory.remove_stored_item(char, _serial(s))
    why = (f"resupply: {len(done)} item(s) refilled, H${total}"
           + (f" (the client showed H${client_total})" if client_total != total else ""))
    if total:
        now = sess.credit_money(why, money=-total)
        if now is None:
            return None
        money = now[0]
    elif done:
        try:
            sess.commit(why)
        except Exception as e:
            log(f"{sess.peer}   WARNING: {why}: NOT banked ({e!r})")
    # the arm reads only the stock row's serial (row + 0xF0, 8 bytes)
    body = pushes.resupply_body(money, rows=[(r, s[:8] if s is not None else None)
                                             for r, s in done])
    log(f"{sess.peer}   0x{MSG_RESUPPLY_REQ:04X} = RESUPPLY ITEMS ({len(rows)} used row(s), "
        f"client total H${client_total}): " + "; ".join(lines)
        + f" -> 0x{pushes.MSG_RESUPPLY:04X} RESUPPLY push ({len(done)} row(s), money "
        f":= {money}; the arm clears each +0x12 and prints 8:66 / 8:67 from stock) "
        f"then message 1 (34:1)")
    return [packet.build(pushes.MSG_RESUPPLY, body, pushes.QUEUE_SEQ, p["conn"]),
            packet.build(0x0001, b"", sess.reply_seq(), p["conn"])]
