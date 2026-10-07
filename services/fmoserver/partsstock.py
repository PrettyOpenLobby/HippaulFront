"""The parts-stock push (0x016A): which parts the shop has in stock."""
import os
import struct
from .knobs import _env_int


#: KEY: `0x016A` -- **THE SHOP'S AVAILABILITY BITMAP**, and the reason the wanzer
#: setup screen's shop is empty no matter what else we serve.
#:
#: Every shop row -- parts AND kind-0x13 items -- is listed only if
#: `0x61174D00(kind, id)` returns **1**:
#:
#:     61174d00  mov eax,[lobby+0x7b78]   ; a blanket override; non-zero -> 1
#:     61174d08  mov edx,[lobby+0x3665]   ; THE BLOCK
#:     61174d1a  je  0x61174d6c           ; NULL -> return 2  (= not listed)
#:     61174d1c  mov al,[edx+2]           ; section kind; 0 ends the walk -> 0
#:     61174d27  cmp al,cl                ; want this kind?
#:     61174d2b  movsx eax,word [edx]     ; else edx += (s16)delta, next section
#:     61174d5b  movzx ecx,word [edx+esi*2+4]  ; bit (id & 15) of word (id >> 4)
#:
#: `lobby+0x3665` has exactly ONE writer: the 0x016A arm `0x6117EC6D`, which
#: frees the previous block, mallocs `payload+0x00` bytes and copies them from
#: `payload+0x10`. We have never sent it, so that pointer is NULL, so the gate
#: returns 2 for every id and **the shop cannot list anything**. The callers:
#: 0x61033CF5 (the parts list), 0x61031AA3 (the kind-0x13 item list) and
#: 0x61185940 (a status label: 0 -> 'N', 1 -> 'S', anything else -> '?').
#:
#: VERIFIED: THE GATE ON THE ARM IS NOT "THE BATTLE SCENE". `[lobby+0x20]==4` is set by
#: the 0x012E -> 0x012F handler at 0x6117A178 (`mov [lobby+0x20],4` right after
#: it copies 0x1A0 dwords of the 0x012F body), i.e. at the CHARACTER LIST, and
#: nothing in the lobby path puts it back. So 0x016A is deliverable for the
#: whole logged-in session, hangar included. This corrects the "SCENE-4 gate ...
#: i.e. the battle scene" reading in the push catalogue above.
#:
#: KEY: AND IT SURVIVES SCENE CHANGES, unlike 0x016C / 0x019F. The lobby reset
#: 0x6117A4B0 frees lobby+0x3665 only down the arm it takes when
#: `[+0x20]==4 && [+0x24] not in {0,3}` -- leaving a BATTLE. An ordinary
#: lobby -> hangar -> setup-screen change keeps the block, so one push per
#: session is enough (it is re-sent per poll anyway, which is free).
#:
#: WARNING: THE BITMAP MUST COVER THE WHOLE TABLE. The list loop at 0x61033CE0 walks
#: `id = 1..count` where count is the master table's own record count
#: (descriptor +0x20), and the gate indexes `word[section+4 + (id>>4)*2]`
#: unconditionally. A section shorter than its kind's table lets the client read
#: past it -- into the next section for an inner one, and past the ALLOCATION
#: for the last. So every section here is sized from PART_TABLE_COUNT, whatever
#: is actually on offer.
#:
#: WARNING: PRICES ARE NOT OURS TO INVENT, and do not need to be: each master table's
#: fourth file is 0x1C bytes per id with the BUY price at +0x00 and the sell
#: price at +0x04 (measured: Arco 10/7, Arco 17 58,600/43,950, Quint
#: 257,000/192,750, Repair I 10/7). The client reads them itself and puts the
#: buy price into the 0x0168 payload, so what we debit is SE's own number.
#: The client also applies SE's own level window itself -- 0x61033BFE computes
#: `lo = max(shop level - 3, 1)` against the record's level byte -- so a bitmap
#: that offers a kind's whole table still shows only level-appropriate rows.
#: What this bitmap is FOR (per-character unlocks? faction? event parts?) is
#: NOT established, which is why "all" is a knob and not the default.
MSG_PARTS_STOCK = 0x016A

#: kind -> ids in that kind's master table. MEASURED from the shipped
#: `Data\AG\F21\D97.DAT` by an offline decoder (it reads the
#: descriptors and divides the record file by 48). Only used to SIZE a section.
PART_TABLE_COUNT = {
    0x11: 741, 0x12: 176, 0x13: 104, 0x21: 805, 0x22: 168, 0x31: 869,
    0x32: 160, 0x41: 666, 0x42: 168, 0x52: 144, 0x62: 136, 0x72: 168,
    0x82: 32, 0x92: 96, 0xA2: 96, 0xB2: 88, 0xC2: 120, 0xD2: 56,
}
PARTS_STOCK_HDR = 4                    # s16 delta, u8 kind, u8 pad
PARTS_STOCK_BODY_OFF = 0x10            # where the arm copies the block from


def parse_parts_stock(spec):
    """`FMO_PARTS_STOCK` -> {kind: frozenset(ids)}, or None for "send nothing".

    Forms: '' / unset  -> None (today's behaviour: no push, empty shop)
           'all'       -> every id of every kind in PART_TABLE_COUNT
           '0x11,0x13' -> those kinds, whole table
           '0x11:1-50,0x31:7' -> explicit id ranges
    Pure, so the selftest drives it without touching the environment."""
    spec = (spec or "").strip()
    if not spec:
        return None
    if spec.lower() == "all":
        return {k: frozenset(range(1, n + 1))
                for k, n in PART_TABLE_COUNT.items()}
    out = {}
    for e in spec.split(","):
        e = e.strip()
        if not e:
            continue
        ks, _, ids = e.partition(":")
        try:
            kind = int(ks, 0)
        except ValueError:
            raise ValueError(f"FMO_PARTS_STOCK entry {e!r}: {ks!r} is not a kind")
        if kind not in PART_TABLE_COUNT:
            raise ValueError(f"FMO_PARTS_STOCK: kind {kind:#04x} is not one of "
                             f"the client's 18 master tables "
                             f"{sorted(PART_TABLE_COUNT)}")
        n = PART_TABLE_COUNT[kind]
        want = set()
        if not ids.strip() or ids.strip() == "*":
            want = set(range(1, n + 1))
        else:
            for r in ids.split("+"):
                lo, _, hi = r.partition("-")
                try:
                    lo = int(lo, 0)
                    hi = int(hi, 0) if hi.strip() else lo
                except ValueError:
                    raise ValueError(f"FMO_PARTS_STOCK entry {e!r}: {r!r} is "
                                     f"not an id or id range")
                if lo < 1 or hi > n or hi < lo:
                    raise ValueError(
                        f"FMO_PARTS_STOCK entry {e!r}: ids must be within "
                        f"1..{n} for kind {kind:#04x} -- an id past the table "
                        f"resolves to NULL in the client and the row is "
                        f"silently skipped")
                want |= set(range(lo, hi + 1))
        out.setdefault(kind, set()).update(want)
    return {k: frozenset(v) for k, v in out.items()}


def parts_stock_block(stock):
    """The chained bitmap the client holds at lobby+0x3665.

    One section per kind -- `{s16 delta to the next, u8 kind, u8 pad, u16
    bitmap[]}`, bit `id & 15` of word `id >> 4` -- then a 4-byte terminator
    whose kind byte is 0, which is what stops the walk at 0x61174D1F.
    Sections are sized from PART_TABLE_COUNT so the client can never index past
    one; see the warning on MSG_PARTS_STOCK."""
    b = bytearray()
    for kind in sorted(stock):
        ids = stock[kind]
        words = (PART_TABLE_COUNT[kind] >> 4) + 1
        sec = bytearray(PARTS_STOCK_HDR + words * 2)
        sec[2] = kind & 0xFF
        for i in ids:
            w = PARTS_STOCK_HDR + (i >> 4) * 2
            struct.pack_into("<H", sec, w,
                             struct.unpack_from("<H", sec, w)[0] | (1 << (i & 15)))
        struct.pack_into("<h", sec, 0, len(sec))
        b += sec
    return bytes(b + b"\x00\x00\x00\x00")


def parts_stock_payload(stock):
    """The 0x016A payload: `u32 length` at +0x00 and the block at +0x10, which
    is exactly what the arm 0x6117EC6D reads (`malloc(payload+0x00)`, then
    `rep movsd` that many bytes from `payload+0x10`)."""
    blk = parts_stock_block(stock)
    b = bytearray(PARTS_STOCK_BODY_OFF + len(blk))
    struct.pack_into("<I", b, 0, len(blk))
    b[PARTS_STOCK_BODY_OFF:] = blk
    return bytes(b)


#: Default EMPTY = no push at all, i.e. exactly today's behaviour. A bad spec is
#: fatal at import on purpose: a shop knob that silently parses to "nothing" is
#: indistinguishable from the bug it is meant to fix.
PARTS_STOCK_SPEC = os.environ.get("FMO_PARTS_STOCK", "").strip()
try:
    PARTS_STOCK = parse_parts_stock(PARTS_STOCK_SPEC)
except ValueError as _e:
    raise SystemExit(str(_e))


#: KEY: FMO_VICTORY_PARTS -- THE PHASE VICTORY REWARD (default 1; 0 = the stock
#: is FMO_PARTS_STOCK alone, the old behaviour). SE, guide/phase: 「勝利すると、
#: それまで購入できなかった相手陣営のヴァンツァー1シリーズが、ハンガーから購入できる
#: ようになります」, 「※勝利報酬のヴァンツァーは、以降のフェイズでも継続的に販売され
#: ます」, 「※制圧ポイントの合計が同点の場合は、両陣営に勝利報酬が与えられます」.
#: topics060306 names the series (「敵陣営パーツ販売開始」, by Level 10/20/30/40):
#: O.C.U. wins -> Igel Eins / Igel Sechs / Grille Eins / Grille Zwei, U.S.N.
#: wins -> Tiran / Tiran II / Tiran III / Tiran IV. The Level column is the
#: parts' own level: the client's level window (0x61033BFE) already applies it.
#: IDS read out of the client's own master tables (Data\AG\F21\D97.DAT via
#: 2026-09-30): body 0x11, arms 0x21, legs
#: 0x31 each hold Tiran..Tiran IV at 176..179 (the legs are named "Tiran M"
#: .. "Tiran IV M") and Igel Eins, Igel Sechs, Grille Eins, Grille Zwei at
#: 181..184. The "H" variants (551..555) and Tiran V / 180 are not SE's
#: reward list and are left alone.
#: THE UNLOCK PERSISTS because it is DERIVED from the war's judged phases
#: (fmowar phases {n: {"winner"}}, stored in the war state), never cached:
#: a nation that has won any phase, or tied one, keeps its series.
VICTORY_PARTS = _env_int("FMO_VICTORY_PARTS", 1) != 0
#: winning nation -> the ENEMY series its shop starts selling
VICTORY_SERIES = {1: (181, 182, 183, 184),     # O.C.U. win: Igel / Grille
                  2: (176, 177, 178, 179)}     # U.S.N. win: Tiran I..IV
VICTORY_KINDS = (0x11, 0x21, 0x31)             # body, arms, legs: the series


def victory_unlocked(phases):
    """The nations whose victory series is unlocked by the judged `phases`
    ({n: {"winner": 0/1/2}}): every winner, and both nations for a tie
    (winner 0). Pure."""
    out = set()
    for rec in (phases or {}).values():
        if not isinstance(rec, dict) or "winner" not in rec:
            continue
        w = int(rec.get("winner") or 0)
        out |= {w} if w in VICTORY_SERIES else set(VICTORY_SERIES)
    return out


def parts_stock_for(stock, nation, unlocked):
    """{kind: frozenset(ids)}: `stock` (FMO_PARTS_STOCK, may be None) with the
    victory series applied. A known nation (1/2) gets its reward series when
    it is `unlocked`, and has it REMOVED when not -- SE: 「それまで購入できなかった
    相手陣営のヴァンツァー」, the enemy series was not on sale before the win.
    `nation` None (no pilot to ask) adds every unlocked series and removes
    nothing. None when the result is empty. Pure."""
    out = {k: set(v) for k, v in (stock or {}).items()}
    series = ([nation] if nation in VICTORY_SERIES
              else sorted(n for n in unlocked if n in VICTORY_SERIES))
    for n in series:
        ids = set(VICTORY_SERIES[n])
        for kind in VICTORY_KINDS:
            if n in unlocked:
                out.setdefault(kind, set()).update(ids)
            elif kind in out:
                out[kind] -= ids
    out = {k: frozenset(v) for k, v in out.items() if v}
    return out or None


def victory_stock(nation=None):
    """(stock to serve, why): FMO_PARTS_STOCK with the war's victory rewards
    applied for `nation` (FMO_VICTORY_PARTS)."""
    if not VICTORY_PARTS:
        return PARTS_STOCK, "FMO_VICTORY_PARTS=0"
    if PARTS_STOCK is None:
        # unset = no push at all (the empty shop); a block holding ONLY the
        # reward series would be a different shop, not a reward
        return None, "FMO_PARTS_STOCK unset"
    war = warstate.war_state() if warstate.WAR != "0" else None
    phases = (war.data.get("phases") if war is not None else None) or {}
    unl = victory_unlocked(phases)
    got = parts_stock_for(PARTS_STOCK, nation, unl)
    return got, (f"victory series unlocked for nation(s) {sorted(unl) or 'none'} "
                 f"after {len(phases)} judged phase(s); served for nation {nation}")


def parts_stock_push(conn_id, nation=None):
    """The 0x016A push, or None when there is nothing in stock.

    A pure push (nothing in the image requests it), so it rides the queue
    sequence like 0x016C and 0x019F. `nation` = the pilot's, so the victory
    reward can be exact (see parts_stock_for); without it every unlocked
    series is added."""
    stock, why = victory_stock(nation)
    if not stock:
        return None
    if stock != PARTS_STOCK:
        log(f"   0x{MSG_PARTS_STOCK:04X} stock differs from FMO_PARTS_STOCK: {why}")
    return packet.build(MSG_PARTS_STOCK, parts_stock_payload(stock),
                        pushes.QUEUE_SEQ, conn_id)


# Called at run time only; imported last so that import cycles resolve.
from . import packet, pushes, warstate  # noqa: E402
from .wirelog import log  # noqa: E402
