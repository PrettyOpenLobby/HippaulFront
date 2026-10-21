"""A new pilot's starting money, MP and contribution, and the wallet as stored."""
import os
import time
from .deps import fmostore


#: WARNING: PLAN 1.5 -- THE PERSISTED-ECONOMY SCHEMA. These per-character keys in
#: fmo_characters.json carry the numbers a mission is supposed to change:
#: `rank`, `money`, `mp`, `contribution` (all ints). They are the schema the
#: result-write leg (PLAN 1.4) must target, fixed HERE so the writer and this
#: reader cannot disagree about names. `owned_parts` (the 1,344-byte owned
#: table) is deferred -- marking a bit CLAIMS a part and the tail echoes back
#: in 0x0170, so it stays zero until that round trip is understood.
#:
#: PRECEDENCE, per "0x014A serves from the store (knobs become seed/override)":
#:   1. an explicit call argument (the selftest / an override) -- top.
#:   2. the character's STORED value, when the key is present -- persistence.
#:   3. the FMO_STATUS_* / FMO_RANK knob -- the SEED for a char with no stored
#:      value, and today's behaviour byte-for-byte (no char carries these keys
#:      yet, so the knob still wins everywhere until the writer lands).
#: WARNING: Once persistence is proven, the economy knobs should LEAVE fmo-play.env:
#: a global knob value masks every character's stored value at step 2's expense.
ECON_STORE_KEYS = ("rank", "money", "mp", "contribution")

#: KEY: WHAT A NEW PILOT STARTS WITH -- the seed half of PLAN 1.5, 2026-09-08.
#:
#: A character is given its own copy of these AT CREATION, so from that moment
#: the pilot carries numbers rather than borrowing the server's. That is the
#: whole point of the change: today FMO_RANK=21 means "everyone on this server
#: is rank 21 for ever", and after it, it means "a pilot created while this was
#: set STARTS at 21" -- which is a starting condition, which is content.
#:
#: THE VALUES COME FROM THE KNOBS ON PURPOSE, not from a new table of numbers.
#: Whatever the deployment has armed today IS the starting state its players
#: have been playing with, so seeding from it makes this migration behaviour-preserving:
#: the first character created after this lands sees exactly what the one
#: before it saw. Change the starting state by changing the knob (or the row);
#: existing pilots are no longer affected either way, which is the point.
#:
#: WARNING: `flags` is seeded ONLY when FMO_STATUS_FLAGS is set. An all-zero stored
#: block and no stored block serve identical bytes, but the stored one MASKS a
#: later knob change -- so a server that has never configured flags must not
#: acquire a block that silently pins every future pilot to zero.
#:
#: KEY: THE NEW-PILOT SEED IS ITS OWN SET OF KNOBS (2026-09-30). FMO_RANK and
#: FMO_STATUS_MONEY / _MP / _CONTRIB stay what they have always been for a
#: record that carries NO value of its own -- the 0x014A fallback -- so a
#: pilot from before the 09-08 seed keeps reading them and is not demoted.
#: A character created from now on is seeded from FMO_SEED_RANK, FMO_SEED_MP,
#: FMO_SEED_CONTRIB and FMO_START_MONEY instead; each one unset falls back to
#: the old knob, so a server that sets none of them behaves as before.
#: The release values start a pilot where SE's did: rank 0 (Conscript,
#: 二等兵), no MP, no contribution. FMO_RANK=21 on prod had seeded every new
#: pilot as a Major, above the defection ceiling (Chief Warrant Officer) and
#: past every contribution promotion and the Private First Class pass.
SEED_RANK = os.environ.get("FMO_SEED_RANK", "").strip()
SEED_MP = os.environ.get("FMO_SEED_MP", "").strip()
SEED_CONTRIB = os.environ.get("FMO_SEED_CONTRIB", "").strip()
#: KEY: FMO_START_MONEY -- STARTING MONEY BY THE NATIONS' HEAD-COUNT GAP.
#: Manual p.38: "Your starting money at the beginning of the game changes
#: according to the difference in strength between the O.C.U. and the U.S.N.
#: (the difference in number of players)". SE never published the formula,
#: so this one is ours, to tune: "base[:per_pct[:cap]]". A pilot who joins
#: the SMALLER nation gets base + per_pct H$ for every percentage point the
#: larger nation leads by (the gap over the larger head count, as
#: charselect.nation_gap_pct counts it), at most cap extra; a pilot who joins
#: the larger nation, or either side of an even split, gets base. Applied once
#: at the creation submit (0x013E), the first message that names the nation.
#: Unset = the flat FMO_STATUS_MONEY seed, as before.
START_MONEY_RAW = os.environ.get("FMO_START_MONEY", "").strip()


def _int_or(raw, fallback):
    try:
        return int(raw, 0) if raw else int(fallback)
    except ValueError:
        return int(fallback)


def parse_start_money(spec):
    """'base[:per_pct[:cap]]' -> (base, per_pct, cap), or None when unset or
    unreadable (then the flat FMO_STATUS_MONEY seed applies)."""
    if not spec:
        return None
    try:
        parts = [int(x, 0) for x in spec.split(":")]
    except ValueError:
        return None
    base = parts[0]
    per = parts[1] if len(parts) > 1 else 0
    cap = parts[2] if len(parts) > 2 else per * 100
    return max(0, base), max(0, per), max(0, cap)


START_MONEY = parse_start_money(START_MONEY_RAW)


def start_money(nation=None, counts=None, spec=None):
    """(H$, why) a new pilot of `nation` starts with, given the head counts
    {1: n, 2: n} (charselect.nation_counts). Pure."""
    spec = START_MONEY if spec is None else spec
    if spec is None:
        return int(status.STATUS_MONEY), "FMO_STATUS_MONEY (flat)"
    base, per, cap = spec
    if nation not in (1, 2) or not counts:
        return base, f"FMO_START_MONEY base {base} (no nation or no head count)"
    mine, other = int(counts.get(nation, 0)), int(counts.get(3 - nation, 0))
    gap = charselect.nation_gap_pct(counts)
    if mine >= other or not gap:
        return base, (f"FMO_START_MONEY base {base}: nation {nation} has {mine} "
                      f"pilot(s) to {other}, not the smaller side")
    extra = min(cap, per * gap)
    return base + extra, (f"FMO_START_MONEY {base} + {per} x {gap}% gap (cap {cap}) = "
                          f"{base + extra}: nation {nation} is the smaller side, "
                          f"{mine} pilot(s) to {other}")


def apply_start_money(rec, nation, counts):
    """Set a NEW pilot's money from its nation (FMO_START_MONEY), once: the
    record is stamped `start_money_nation`, so a second 0x013E for the same
    slot or a later Change Nations does not pay it again. Returns the log
    line, or None when nothing was applied."""
    if START_MONEY is None or nation not in (1, 2) or rec.get("start_money_nation"):
        return None
    hs, why = start_money(nation, counts)
    rec["money"] = int(hs)
    rec["start_money_nation"] = int(nation)
    return why


def seed_new_character(rec):
    """Give a freshly created record its starting state. Returns `rec`.

    Only fills keys the record does not already have, so a client that starts
    carrying one of these itself is never overwritten. `born_at` (unix
    seconds) is the creation time the 24-hour delete lock reads.
    """
    money = START_MONEY[0] if START_MONEY is not None else status.STATUS_MONEY
    for key, val in (("rank", _int_or(SEED_RANK, status.START_RANK)), ("money", money),
                     ("mp", _int_or(SEED_MP, status.STATUS_MP)),
                     ("contribution", _int_or(SEED_CONTRIB, status.STATUS_CONTRIB))):
        rec.setdefault(key, int(val))
    rec.setdefault("born_at", int(time.time()))
    if fmostore and (status.STATUS_FLAGS or status.STATUS_FLAG_BYTES) and "flags" not in rec:
        rec["flags"] = fmostore.flags_hex(status.STATUS_FLAGS, status.STATUS_FLAG_BYTES)
    return rec


def seed_summary(rec):
    """The one-line description of a seed, for the STORED log line."""
    bits = ", ".join("%s %s" % (k, rec.get(k)) for k in ECON_STORE_KEYS)
    if rec.get("flags"):
        fl = fmostore.flags_bytes(rec["flags"]) if fmostore else b""
        marks = ",".join("%d=%d" % (i, v) for i, v in enumerate(fl) if v)
        bits += f", flags {marks}" if marks else ""
    return bits


def _econ_value(field, arg, knob_val, knob_name, char):
    """(value, source) for one economy field, by the precedence above.

    WARNING: THE MASKING CASE IS NAMED IN THE SOURCE STRING, and that is not
    decoration. Once a character carries its own numbers, setting the knob for
    a probe changes NOTHING for that pilot -- and a knob that is armed, visible
    in `docker exec … env`, and silently ignored is precisely the shape of
    reading that has burned this project before (stale-instrument-reads-as-a-
    result). The 0x014A log line now says which one won.
    """
    if arg is not None:
        return arg, f"call arg {field}={arg}"
    v = char.get(field)
    if isinstance(v, int) and not isinstance(v, bool):
        src = f"character store [{field}]"
        if knob_val and knob_val != v:
            src += (f" -- WARNING: {knob_name}={knob_val} IS SET AND MASKED; this "
                    f"pilot's stored value wins")
        return v, src
    return knob_val, knob_name


def wallet_money(char, arg=None):
    """(money, source) for a pilot, with the pre-2026-09-12 damage repaired.

    KEY: A NEGATIVE STORED BALANCE CAN ONLY HAVE COME FROM ONE BUG, so it can be
    undone exactly. Until 2026-09-12 the display resolved a missing `money` to
    FMO_STATUS_MONEY while credit_money resolved the same absence to 0, so the
    debits were taken from zero instead of from the balance the pilot was shown.
    The true balance is therefore `FMO_STATUS_MONEY + stored` -- prod's laptop
    pilot, shown 12,345, bought at 10 and sold at 7, stored -3, really has
    12,342. Repairing it here rather than by hand means every damaged pilot is
    fixed at their next 0x014A, and the number they see is the number they
    earned. Floored at 0 regardless: a negative must never reach the client,
    whose shop gate 0x611785D0 compares it SIGNED and would refuse every price,
    including 0."""
    v, src = _econ_value("money", arg, status.STATUS_MONEY, "FMO_STATUS_MONEY", char)
    if v < 0:
        v, was = max(0, status.STATUS_MONEY + v), v
        src += (f" -- REPAIRED: stored {was} is pre-2026-09-12 damage (the "
                f"debits were taken from 0 instead of from FMO_STATUS_MONEY="
                f"{status.STATUS_MONEY}); reading it as {v}")
    return v, src


def wallet_mp(char, arg=None):
    """(MP, source) for a pilot, resolved like the 0x014A shows it (the stored
    `mp`, else FMO_STATUS_MP) and floored at 0 -- the number a Fee is judged
    against must be the number on the pilot's screen."""
    v, src = _econ_value("mp", arg, status.STATUS_MP, "FMO_STATUS_MP", char or {})
    return max(0, int(v or 0)), src


def spend_mp(char, amount):
    """Take `amount` MP off the pilot's stored balance (a mission Fee).
    MUTATES char["mp"]; returns (before, after). The caller commits. Floored at
    0: the gate refuses a pilot short of the Fee, so a floor here means two
    accepts raced, and the log line says so."""
    was = wallet_mp(char)[0]
    char["mp"] = max(0, was - int(amount))
    return was, char["mp"]


# --------------------------------------------------------------------------- #
# THE HANGAR ECONOMY: the shop table, the remodel cost and the Battle Fee
# --------------------------------------------------------------------------- #
#: KEY: THE SHOP TABLE IS SE'S OWN (static 2026-10-08). Every master table's
#: fourth file is a 0x1C-byte row per id (0x611A4760 / 0x611A4B30): +0x00 the
#: BUY price, +0x04 the SELL price (75%, rounded down, on every priced row),
#: +0x08 the nation/tier restriction, +0x09 a category, +0x0C the maximum
#: total remodel rank, +0x0E + 2*i the per-stat remodel cap. Banked per
#: deployment as fmodata/fmo-part-shop.tsv by tools/fmodatagen/fmopartshop.py.
#: Absent = every rule below that needs a price falls back (stored purchase
#: price, then "unknown") and says so; nothing is invented.
_FMODATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata")
PART_SHOP_TSV = os.path.join(_FMODATA, "fmo-part-shop.tsv")


def load_part_shop(path=PART_SHOP_TSV):
    """{(kind, id): {level, buy, sell, restrict, cat, max_rank, caps}} from
    fmo-part-shop.tsv; {} when it is absent or unreadable."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            head = f.readline().rstrip("\r\n").split("\t")
            col = {h: i for i, h in enumerate(head)}
            for line in f:
                c = line.rstrip("\r\n").split("\t")
                if len(c) < len(head) - 1:
                    continue
                out[(int(c[col["kind"]], 0), int(c[col["id"]]))] = {
                    "level": int(c[col["level"]]), "buy": int(c[col["buy"]]),
                    "sell": int(c[col["sell"]]), "restrict": int(c[col["restrict"]]),
                    "cat": int(c[col["cat"]]), "max_rank": int(c[col["max_rank"]]),
                    "caps": tuple(int(c[col["cap%d" % i]]) for i in range(REMODEL_STATS))}
    except (OSError, ValueError, KeyError):
        return {}
    return out


#: the six remodel rank bytes of a 24-byte item record (+0x0C..+0x11): the
#: remodel window raises record+0x0C+i (0x610411A0), the Battle Fee walks
#: them (0x61175D03 `movzx ebx, byte [esi+edi+2]`, esi = record+0x0A)
REMODEL_RANKS_OFF = 0x0C
REMODEL_STATS = 6
PART_SHOP = load_part_shop()


def part_row(kind, item_id, table=None):
    """The shop row for (kind, id), or None when the table is absent or lacks it."""
    table = PART_SHOP if table is None else table
    return table.get((int(kind), int(item_id)))


def row_nation(row):
    """The nation a shop row is sold to: restriction % 10 (0x6102FCDF), 0 =
    both. None for no row."""
    return None if row is None else row["restrict"] % 10


def record_ranks(rec):
    """The six remodel ranks of a 24-byte item record."""
    return tuple(bytes(rec)[REMODEL_RANKS_OFF:REMODEL_RANKS_OFF + REMODEL_STATS])


#: KEY: SE'S REMODEL COST, read off the client (static 2026-10-08). The window
#: checks `0x6103FA70() <= lobby+0x88C` before it lets a rank be bought
#: (0x61036FFD) and builds the 0x017E with that same figure at pending+0x18
#: (0x6102F200 <- 0x61037213). 0x6103FA70 calls 0x611E1D80, which returns
#:     (price * 25 / 100000) * (to - from)      when to > from, else 0
#: with price = shop row +0x00 (0x6103FA98 `mov edi,[eax]`), 25 = `push 0x19`
#: at 0x611E1DBA and 100000 = `push 0x186A0` at 0x611E1DC9 (64-bit _allmul /
#: _alldiv), the division done once, before the multiply by the steps. So a
#: rank step costs a 4000th of the part's price; lowering a rank is free (SE
#: update 2005-09-06: 「ランクを上げる際には費用が発生します」).
REMODEL_RATE = 25
REMODEL_DIVISOR = 100000


def remodel_step_cost(price):
    """H$ for one remodel rank step of a part priced `price` (0x611E1D80)."""
    return int(price) * REMODEL_RATE // REMODEL_DIVISOR


def remodel_cost(price, old_ranks, new_ranks):
    """H$ the client charges for raising `old_ranks` to `new_ranks` (six
    each): the sum of the raises times remodel_step_cost. Pure."""
    raises = sum(max(0, int(n) - int(o)) for o, n in zip(old_ranks, new_ranks))
    return remodel_step_cost(price) * raises


def remodel_check(row, new_ranks):
    """(ok, why) for a record's new ranks against its shop row: each rank at
    most its stat's cap (+0x0E + 2*i), the total at most the part's maximum
    remodel rank (+0x0C; SE 2005-09-06 「要素ごとのランクの合計値が最大改造
    ランクを上回ることはできません」). A part whose maximum is 0 cannot be
    remodelled at all (17:183). No row = not checked (the table is absent)."""
    if row is None:
        return True, "no shop row to check against (fmo-part-shop.tsv absent)"
    ranks = [int(r) for r in new_ranks]
    if any(ranks) and row["max_rank"] <= 0:
        return False, "this part cannot be remodelled (maximum rank 0)"
    for i, r in enumerate(ranks):
        cap = row["caps"][i]
        if r > max(0, cap):
            return False, f"stat {i} rank {r} is over its cap {max(0, cap)}"
    if sum(ranks) > row["max_rank"]:
        return False, f"total rank {sum(ranks)} is over the maximum {row['max_rank']}"
    return True, "ok"


def part_price(kind, item_id, char=None, serial=None, table=None):
    """(price, source) of a part: the shop row's buy price, else the price the
    pilot paid for this serial (stored with the purchase), else (0, why)."""
    row = part_row(kind, item_id, table)
    if row is not None and row["buy"]:
        return row["buy"], "shop row"
    for it in inventory.stored_items(char) if char else ():
        if serial is not None and it["serial"] == int(serial) and it.get("price"):
            return int(it["price"]), "price paid"
    return 0, "unknown (no shop row, no purchase price)"


#: KEY: THE BATTLE FEE, read off the client (static 2026-10-01 / 2026-10-08).
#: 0x61175CB0 walks a setup's 21 item records and, for every non-zero rank
#: byte i of a record, adds
#:     price * rank * rate[type] / 100000                     (0x611E1F60)
#: price = the shop row +0x00 (0x61175CFF: the same 0x611A4760 row the shop
#: and the remodel cost read), type = 0x611E1C20(kind, i) from the per-kind
#: tables below (read out of 0x613998D0..0x61399960, -1 = that stat has no
#: fee), rate[type] = lobby+0x7E0D + type*4 = the 0x019F block +0x18
#: (squadron.BATTLE_FEE_RATES). The types follow the remodel help lines
#: 18:59..66: 0 HP, 1 weight, 2 defence, 3 top speed, 4 ammo, 5 damage,
#: 6 range, 7 recoil.
FEE_TYPES = {
    0x11: (0, 1, 2, 3, -1, -1),            # body: HP, weight, defence, speed
    0x21: (0, 1, 2, -1, -1, -1), 0x31: (0, 1, 2, -1, -1, -1),
    0x41: (1, -1, -1, -1, -1, -1),         # backpack: weight
    0x12: (4, 1, 5, 6, 7, -1), 0x22: (4, 1, 5, 6, 7, -1),
    0x32: (4, 1, 5, 6, 7, -1), 0x42: (4, 1, 5, 6, 7, -1),
    0xD2: (4, 1, 5, 6, 7, -1),
    0x52: (4, 1, 5, 6, -1, -1), 0x62: (4, 1, 5, 6, -1, -1),
    0x72: (4, 1, 5, 6, -1, -1),
    0x82: (1, 5, -1, -1, -1, -1), 0x92: (1, 5, -1, -1, -1, -1),
    0xA2: (1, 5, -1, -1, -1, -1), 0xB2: (1, 5, -1, -1, -1, -1),
    0xC2: (0, 1, -1, -1, -1, -1),
}
FEE_DIVISOR = 100000                      # 0x186A0 at 0x611E1F7D


def fee_type(kind, stat):
    """0x611E1C20(kind, stat): the Battle Fee rate index 0..7, or -1."""
    return FEE_TYPES.get(int(kind), (-1,) * REMODEL_STATS)[int(stat)]


def setup_battle_fee(block, setup_no, rates, char=None, table=None):
    """(fee, lines) for setup `setup_no` (1-based) of a 0x0166-shaped block:
    the client's own 0x61175CB0 sum over its 21 equipped records. `rates` =
    the eight 0x019F rates. A record whose price is unknown adds nothing and
    is named in `lines`. Pure apart from reading `char`'s stored items."""
    fee, lines = 0, []
    if not any(rates) or not 1 <= setup_no <= inventory.SETUP_SLOTS:
        return 0, lines
    base = (setup_no - 1) * inventory.SETUP_ENTRY_LEN
    for i in range(inventory.SETUP_ITEMS):
        off = base + inventory.SETUP_ITEM_OFF + i * inventory.INV_ENTRY_LEN
        rec = block[off:off + inventory.INV_ENTRY_LEN]
        if len(rec) < inventory.INV_ENTRY_LEN or not rec[inventory.ITEM_KIND]:
            continue
        ranks = record_ranks(rec)
        if not any(ranks):
            continue
        kind = rec[inventory.ITEM_KIND]
        iid = struct.unpack_from("<H", rec, inventory.ITEM_ID)[0]
        price, src = part_price(kind, iid, char, struct.unpack_from("<Q", rec, 0)[0], table)
        part = 0
        for stat, rank in enumerate(ranks):
            t = fee_type(kind, stat)
            if rank and t >= 0:
                part += int(price) * int(rank) * int(rates[t]) // FEE_DIVISOR
        fee += part
        lines.append(f"item {i} kind 0x{kind:02X} id {iid} ranks {list(ranks)} "
                     f"price {price} ({src}) -> {part}")
    return fee, lines


#: FMO_BATTLE_FEE_MIN_BG -- SE update 2005-09-06: 「最低BGコスト12以上のセクター
#: のみ改造武器・パーツで出撃可能です」. A battle in a sector whose minimum B.G.
#: cost is KNOWN and below this runs with the remodel ranks disabled: no fee,
#: and the client prints 8:75 instead of 8:74 (0x01A1 +0x08 = 1). SE lowered
#: the bar on 2005-10-31 without a number; 12 is the one they published.
#: The war state does not fill a sector's minimum yet (sortie.py), so today
#: this only applies where an operator has set one. 0 = never disable.
BATTLE_FEE_MIN_BG = int(os.environ.get("FMO_BATTLE_FEE_MIN_BG", "").strip() or 12)


def battle_fee_verdict(fee, in_arena=False, sector_min_bg=0, min_bg=None):
    """(charge, disabled, why) for a fee at the battle end. The Coliseum
    (OURS: every arena counts as one that forbids modified units, 88:11) and
    a sector below the B.G. bar disable the ranks: nothing is charged and
    0x01A1 says so (8:85 / 8:75). Pure."""
    min_bg = BATTLE_FEE_MIN_BG if min_bg is None else min_bg
    if fee <= 0:
        return 0, False, "no remodelled part on the setup"
    if in_arena:
        return 0, True, "an Arena battle: modified units are not allowed (8:85)"
    if min_bg and 0 < int(sector_min_bg or 0) < min_bg:
        return 0, True, (f"the sector's minimum B.G. cost {sector_min_bg} is below "
                         f"{min_bg} (8:75)")
    return int(fee), False, "charged (8:74)"


# Called at run time only; imported last so that import cycles resolve.
import struct  # noqa: E402
from . import charselect, inventory, status  # noqa: E402
