"""The cosmetics shop screen (0x01A2 -> 0x01A3)."""
import os
import struct
from .knobs import _env_int


#: KEY: `0x01A2 -> 0x01A3` -- **THE COSMETIC CATALOGUE**, and the reason the pilot
#: locker turned the player into a stewardess.
#:
#: Both hangar terminals open by sending 0x01A2, and the one byte at payload+0x00
#: says WHICH catalogue (measured live 2026-09-11, both screens on one session):
#:   byte 1  the PILOT.LOCKER   -> accessories (kind 0) + pilot suits (kind 1)
#:   byte 2  the SETUP.CONSOLE  -> camo (2) + colours (3) + emblems (4)
#: The reply's parse 0x61172950 takes `u32 count` from payload+0x00 and copies
#: 0x3840 bytes from payload+0x44 as up to 1800 entries of
#: `{u16 id, u8 kind, u8 unused, u32 price}` (the six list builders read only
#: +0, +2 and +4; the buy functions copy the row's two dwords into the pending
#: purchase at scene+0xF8C5, which 0x01A4 sends -- see MSG_COSMETIC_BUY).
#:
#: We answered 14,468 ZEROS -- count 0 -- so every picker had no rows, and an
#: outfit change fell back to a default body. Same failure shape as the 09-09
#: insignia crash: an EMPTY list is not a neutral answer.
#:
#: The rows are the client's OWN tables, banked from
#: Data/BB/F13/D27..D31.DAT with SE's English names. `nation_flag` is the
#: record's +0x04: 1 and 2 are the two nations (`Garrison Cap` and `Officer's
#: Dress Hat` each exist twice, once per nation; the splits are 26/27, 88/84,
#: 49/46, 33/33), 0 is nation-neutral, and 255 marks a placeholder record
#: (`HEAD_F91`, `Pilot Suit 41`) which is dropped at extraction. The client
#: applies the same equality test itself for insignia (0x611BE344 against
#: byte[lobby+0x8B4]), so we filter the same way.
#:
#: WARNING: THE PRICE IS OURS AND SE'S IS NOT KNOWN. Unlike the PART tables -- whose
#: fourth file really is 0x1C bytes per id with the buy price at +0x00 -- these
#: five carry no money field at all (+0x28/+0x2c are an index and a model id).
#: FMO_COSMETIC_PRICE is therefore an invention, and it defaults to 0 so that it
#: cannot take money a player should have kept. The only attested cosmetic price
#: anywhere is the 2nd Huffman Conflict medal at H$10 (topics/060428).
A2_PILOT, A2_WANZER = 1, 2
A2_KINDS = {A2_PILOT: (0, 1), A2_WANZER: (2, 3, 4)}
A3_COUNT_OFF = 0x00
A3_ROW_OFF = 0x44                      # the `lea esi,[eax+0x44]` at 0x6117296B
A3_ROW_LEN = 8
A3_ROW_MAX = 0xE10 * 4 // A3_ROW_LEN   # 1,800 -- the rep movsd count
COSMETIC_TSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "fmodata", "fmo-cosmetics.tsv")
#: 0 = serve the 14,468 zeros we always have (the stewardess state, kept as an
#: A/B); 1 = serve the catalogue.
COSMETIC_SHOP = (os.environ.get("FMO_COSMETIC_SHOP", "").strip() or "1") != "0"
COSMETIC_PRICE = _env_int("FMO_COSMETIC_PRICE", "0")


# --------------------------------------------------------------------------- #
# BUYING PAINT -- the SETUP.CONSOLE shop (Setup > Coloring > R2), 0x01A4
# --------------------------------------------------------------------------- #
#: KEY: STATIC 2026-10-07. THE PAINT SHOP DOES NOT USE 0x0168. The chain:
#:   list   R2 flips scene+0xBFA0 (0x61032170) from the OWNED picker to the
#:          SHOP list. The shop lists (camo near 0x61031FDD, colours x2 near
#:          0x610324CD / 0x610329DD, insignia near 0x61032EF3) walk THIS
#:          catalogue (0x01A3 rows at scene+0xC085, count scene+0xC081), keep
#:          the rows of their kind (2 / 3 / 4) and DROP the ones already owned
#:          (0x61174FD0 cat 4 / 0x61174FF0 cat 5 / 0x61175010 cat 12, on the
#:          D29/D30/D31 record's +0x2C, which equals the row id in all three
#:          files). No 0x016A stock gate and no price-0 drop here: the
#:          catalogue IS the stock, and its u32 is the price shown and sent.
#:   buy    0x6102F430 (camo), 0x6102F5F0.. / 0x6102F7B0.. (colours),
#:          0x6102E8F0.. (insignia): find the row by (kind, id), money gate
#:          0x611785D0(price, 0x14) -- the client refuses on its own when
#:          lobby+0x88C < price -- copy the row's {u16 id, u8 kind, u8, u32
#:          price} to scene+0xF8C5 and open a confirm dialog in MODE 5
#:          (0x6103FBB0 with 5 as its 4th argument).
#:   wire   mode 5 = jmp 0x61173210 on the lobby-API object at lobby+0x74E2
#:          (vtable 0x6133B414): its send 0x61172990 is msg 0x01A4, 0x18 B,
#:          payload+0x00 = the 8 pending bytes. Its parse is the stub 0x6122B670
#:          and the generic start stamps reply id 0x0001. PROVEN on the wire
#:          2026-09-11 23:57Z (prod fmo.log): `83 00 04 00 00 00 00 00` = id 131
#:          kind 4 price 0, and `66 00 04 00 ..` = id 102 kind 4.
#:   grant  NOTHING on the client side: the only owned-bit setter (0x611A39F0)
#:          is called from the 0x0168 poller alone, and the only purchase debits
#:          of lobby+0x88C are the 0x016B / 0x017F arms. So a message 1 here
#:          neither charges nor grants -- before 2026-10-07 every paint "bought"
#:          was free and stayed unowned. We bank the id on the pilot
#:          (inventory.add_owned_paint -> 0x014A's owned block at the next login)
#:          and, with FMO_RESULT_PUSH, send the 0x015A push, whose arm copies
#:          its +0x598 owned table onto lobby+0x8C8 and adds the money delta,
#:          so the picker can list the paint and the wallet drops this session.
MSG_COSMETIC_BUY = 0x01A4
BUY_ID, BUY_KIND, BUY_PRICE = 0x00, 0x02, 0x04
#: catalogue kind -> inventory.OWNED_PAINT_CATS name (category 4 / 5 / 12).
BUY_KIND_CAT = {2: "camo", 3: "colour", 4: "insignia"}
#: FMO_COSMETIC_BUY: '1' (default) = the verdict below; '0' = the old blind
#: acknowledgement (message 1, nothing charged, nothing granted).
COSMETIC_BUY = (os.environ.get("FMO_COSMETIC_BUY", "").strip() or "1") != "0"


def parse_stock(spec):
    """FMO_COSMETIC_STOCK -> {kind: None (every row) or set of ids}, for the
    paint kinds 2/3/4 only. '' / 'all' = every catalogue row (the TSV already
    dropped SE's flag-255 records, which include the starting colours, so it
    is everything SE could have sold); '0' / 'none' = nothing; otherwise
    comma entries `<kind>`, `<kind>:<id>` or `<kind>:<lo>-<hi>`.
    Raises ValueError with a sentence on a bad entry."""
    spec = (spec or "").strip().lower()
    if spec in ("", "all"):
        return {k: None for k in BUY_KIND_CAT}
    if spec in ("0", "none"):
        return {}
    out = {}
    for ent in spec.split(","):
        ent = ent.strip()
        if not ent:
            continue
        k, _, ids = ent.partition(":")
        try:
            k = int(k, 0)
            if k not in BUY_KIND_CAT:
                raise ValueError
            if not ids:
                out[k] = None
                continue
            lo, _, hi = ids.partition("-")
            lo = int(lo, 0)
            hi = int(hi, 0) if hi else lo
        except ValueError:
            raise ValueError(f"FMO_COSMETIC_STOCK entry {ent!r} wants <kind>, "
                             f"<kind>:<id> or <kind>:<lo>-<hi> with kind 2, 3 or 4")
        if k in out and out[k] is None:
            continue
        out.setdefault(k, set()).update(range(lo, hi + 1))
    return out


def parse_prices(spec):
    """FMO_COSMETIC_PRICES -> {kind: price}: comma entries `<kind>:<H$>`,
    overriding FMO_COSMETIC_PRICE for that kind. Raises ValueError."""
    out = {}
    for ent in (spec or "").split(","):
        ent = ent.strip()
        if not ent:
            continue
        k, _, v = ent.partition(":")
        try:
            k, v = int(k, 0), int(v, 0)
        except ValueError:
            raise ValueError(f"FMO_COSMETIC_PRICES entry {ent!r} wants <kind>:<price>")
        if v < 0:
            raise ValueError(f"FMO_COSMETIC_PRICES entry {ent!r}: a price is >= 0")
        out[k] = v
    return out


try:
    COSMETIC_STOCK = parse_stock(os.environ.get("FMO_COSMETIC_STOCK", ""))
    COSMETIC_PRICES = parse_prices(os.environ.get("FMO_COSMETIC_PRICES", ""))
except ValueError as _e:
    raise SystemExit(str(_e))


def price_for(kind):
    """The H$ a row of `kind` costs. OURS, never SE's (see the WARNING above):
    FMO_COSMETIC_PRICES per kind, else FMO_COSMETIC_PRICE."""
    return max(0, int(COSMETIC_PRICES.get(kind, COSMETIC_PRICE)))


def in_stock(kind, rid):
    """True when the paint shop sells (kind, rid). Kinds 0/1 (the pilot
    locker) are not paint and are never filtered here."""
    if kind not in BUY_KIND_CAT:
        return True
    if kind not in COSMETIC_STOCK:
        return False
    ids = COSMETIC_STOCK[kind]
    return ids is None or rid in ids


def parse_buy(payload):
    """(id, kind, price) out of a 0x01A4 body -- the client's pending row."""
    pl = bytes(payload or b"")
    rid = struct.unpack_from("<H", pl, BUY_ID)[0] if len(pl) >= 2 else 0
    kind = pl[BUY_KIND] if len(pl) > BUY_KIND else 0
    price = struct.unpack_from("<I", pl, BUY_PRICE)[0] if len(pl) >= 8 else 0
    return rid, kind, price


def buy_verdict(rid, kind, nation, money, owned_ids):
    """Decide a paint purchase. Pure. `owned_ids` = inventory.owned_paint_ids()
    for the pilot. Returns {ok, why, cat, price, name, new}; ok with `new`
    False means the pilot already owns it (granted again, charged nothing)."""
    cat = BUY_KIND_CAT.get(kind)
    v = {"ok": False, "cat": cat, "price": 0, "name": "", "new": False}
    if cat is None:
        v["why"] = f"kind {kind} is not a paint kind (2 camo, 3 colour, 4 insignia)"
        return v
    row = next((r for r in COSMETICS.get(kind, ()) if r[0] == rid), None)
    if row is None:
        v["why"] = f"kind {kind} id {rid} is not in the catalogue (fmo-cosmetics.tsv)"
        return v
    v["name"] = row[2]
    if row[1] not in (0, nation):
        v["why"] = (f"{row[2]!r} belongs to nation {row[1]} and the pilot is "
                    f"nation {nation} (the catalogue never offered it)")
        return v
    if rid in (owned_ids or {}).get(cat, ()):
        v.update(ok=True, why=f"{row[2]!r} is already owned: granted again, free")
        return v
    if not in_stock(kind, rid):
        v["why"] = f"{row[2]!r} is not stocked (FMO_COSMETIC_STOCK)"
        return v
    price = price_for(kind)
    v["price"] = price
    if money < price:
        v["why"] = f"{row[2]!r} costs {price} H$ and the pilot has {money}"
        return v
    v.update(ok=True, new=True, why=f"{row[2]!r} sold for {price} H$")
    return v


def on_buy(sess, p):
    """The 0x01A4 arm for paint (kinds 2/3/4): verdict, debit, bank, push.
    Returns the packets, or None to leave the message to the generic
    lobby-API acknowledgement (another kind, or FMO_COSMETIC_BUY=0)."""
    from . import charstore, economy, inventory, packet, resultpush, status, zoneentry
    from .wirelog import log
    rid, kind, wire_price = parse_buy(p["payload"])
    if not COSMETIC_BUY or kind not in BUY_KIND_CAT:
        return None
    refuse = [packet.build(2, b"", sess.reply_seq(), p["conn"])]
    char = sess.playing_char() if charstore.CHAR_STORE else None
    if char is None:
        log(f"{sess.peer}   0x01A4 PAINT BUY kind {kind} id {rid}: no pilot in the "
            f"store -- REFUSED (message 2, a numbered [FM] box, nothing moves)")
        return refuse
    nation = zoneentry.script_nation(char)[0]
    money = economy.wallet_money(char)[0]
    v = buy_verdict(rid, kind, nation, money, inventory.owned_paint_ids(char, nation))
    log(f"{sess.peer}   0x01A4 = PAINT BUY: kind {kind} ({v['cat']}) id {rid} "
        f"{v['name']!r}, client price {wire_price}, ours {v['price']}, wallet "
        f"{money}, nation {nation}: {'OK' if v['ok'] else 'REFUSED'} - {v['why']}")
    if not v["ok"]:
        return refuse
    if v["new"]:
        inventory.add_owned_paint(char, v["cat"], rid)
    why = f"bought paint {v['cat']} {rid} {v['name']!r}"
    if v["price"]:
        if sess.credit_money(why, money=-v["price"]) is None:
            return refuse
    else:
        try:
            sess.commit(why + ", free")
        except Exception as e:
            log(f"{sess.peer}   WARNING: {why}: NOT banked ({e!r})")
    outs = []
    if resultpush.RESULT_PUSH:
        owned = bytearray(status.reply_014a(char=char)[
            status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN])
        fl = sess.pilot_flags()
        if fl:
            o = status.S14A_FLAGS11 - status.S14A_OWNED
            owned[o:o + status.S14A_FLAGS11_LEN] = fl[:status.S14A_FLAGS11_LEN]
        outs.append(resultpush.result_push_packet(
            p["conn"], money=-v["price"], contribution=0, owned=bytes(owned),
            pilot=char))
        log(f"{sess.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} push: owned table "
            f"with the new bit (lobby+0x8C8) and money delta -{v['price']}, so "
            f"the picker can list it this session")
    else:
        log(f"{sess.peer}   FMO_RESULT_PUSH=0: banked, but the picker lists it and "
            f"the wallet shows the debit only from the next login (0x014A)")
    outs.append(packet.build(0x0001, b"", sess.reply_seq(), p["conn"]))
    return outs


def load_cosmetics(path=COSMETIC_TSV):
    """{kind: [(id, nation_flag, english)]} from fmo-cosmetics.tsv, or {}."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            try:
                out.setdefault(int(r["kind"]), []).append(
                    (int(r["id"]), int(r["nation_flag"]), r.get("english", "")))
            except (KeyError, ValueError):
                continue
    return out


COSMETICS = load_cosmetics()


def cosmetics_for(screen, nation):
    """[(kind, id, english)] this screen offers this nation, in table order.

    A row is offered when its nation flag is 0 (both) or equals the character's
    nation -- the client's own rule for insignia. Capped at A3_ROW_MAX."""
    rows = []
    for kind in A2_KINDS.get(screen, ()):
        for rid, flag, en in COSMETICS.get(kind, ()):
            if flag in (0, nation) and in_stock(kind, rid):
                rows.append((kind, rid, en))
    return rows[:A3_ROW_MAX]


def reply_01a3(need, req, nation):
    """The 0x01A3 body for this 0x01A2 request, or None to fall back to zeros."""
    screen = req[0] if req else 0
    if not COSMETIC_SHOP or screen not in A2_KINDS or not COSMETICS:
        return None
    rows = cosmetics_for(screen, nation)
    if not rows:
        return None
    b = bytearray(need)
    struct.pack_into("<I", b, A3_COUNT_OFF, len(rows))
    for i, (kind, rid, _en) in enumerate(rows):
        struct.pack_into("<HBBI", b, A3_ROW_OFF + i * A3_ROW_LEN,
                         rid & 0xFFFF, kind & 0xFF, 0, price_for(kind))
    return bytes(b)
