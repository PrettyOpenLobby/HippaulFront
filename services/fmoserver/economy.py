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


# Called at run time only; imported last so that import cycles resolve.
from . import charselect, status  # noqa: E402
