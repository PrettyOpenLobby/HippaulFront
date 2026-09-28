"""A new pilot's starting money, MP and contribution, and the wallet as stored."""
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
def seed_new_character(rec):
    """Give a freshly created record its starting state. Returns `rec`.

    Only fills keys the record does not already have, so a client that starts
    carrying one of these itself is never overwritten.
    """
    for key, val in (("rank", status.START_RANK), ("money", status.STATUS_MONEY),
                     ("mp", status.STATUS_MP), ("contribution", status.STATUS_CONTRIB)):
        rec.setdefault(key, int(val))
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


# Called at run time only; imported last so that import cycles resolve.
from . import status  # noqa: E402
