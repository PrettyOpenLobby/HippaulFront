"""The rank ladder: rank names, contribution thresholds and the pay per rank."""
import os
from .knobs import _env_int


#: FMO_RANK_GROUP: the byte at 0x014A payload+0x29 (lobby+0x8B5), 1..4; served
#: only while FMO_CLASS_TABLE is on. 0 = leave the zero the client had.
RANK_GROUP = _env_int("FMO_RANK_GROUP", "1")


#: THE RANK LADDER -- the other table in Data/AI/F32/D15.DAT: 48 rows of
#: {name, CONTRIBUTION threshold, grade, cap, order}, shipped as
#: fmodata/fmo-ranks.tsv, whose `rank` column is THE WIRE BYTE (0-based, see
#: S14A_RANK): byte 20 = Captain needs 294,400 contribution; byte 21 = Major
#: (the Briefing Room's threshold at 0x6108B8B5) 340,000. Rows 29..47 are the
#: un-earnable officer/Expeditionary/Mercenary/GM rows with 1e9 thresholds.
#: KEY: CONTRIBUTION PROMOTES ONLY UP TO CAPTAIN (SE, update 050719qk2ld8:58
#: 「大尉」となった後は…昇格することはありません): from Captain on, SE decided
#: keep / promote / demote per period from sector-mission (Captain -> Major)
#: and area-mission (Major+) successes, with a headcount cap per rank (:61-71).
#: None of that is built; RANK_MAX_EARNABLE = Captain keeps the ladder honest.
#: FMO_RANK_FROM_CONTRIB: '0' (default) = rank comes from the store / FMO_RANK
#: as before. '1' = a pilot with a STORED contribution gets the highest rank
#: whose threshold it meets -- so the contribution 0x014C/0x015A credit moves
#: rank the way the game meant it to. WARNING: OFF by default because every pilot on
#: this server has contribution 0 today, which is Private (byte 1), and byte 21
#: is what opens the Briefing Room: turn it on together with a seeded
#: contribution (fmostore seed) or accept the demotion.
RANK_FROM_CONTRIB = (os.environ.get("FMO_RANK_FROM_CONTRIB", "").strip() or "0") != "0"


def load_rank_ladder(path=None, pay=None):
    """[(rank, name, contribution threshold)] from fmodata/fmo-ranks.tsv, or [].
    With `pay` (a dict) also fills {rank byte: (H$ pay, MP)} from the `pay` /
    `mp` columns -- row+0x58 / row+0x64 of D15.DAT, the figures the Personal
    Ratings screen prints as 11:26 "H$ %d" / 11:27 ": MP" (0x61191BC4)."""
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "fmodata", "fmo-ranks.tsv")
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [ln.rstrip("\n").split("\t") for ln in fh if ln.strip()]
    except OSError:
        return []
    out = []
    head = rows[0] if rows else []
    i_pay = head.index("pay") if "pay" in head else None
    i_mp = head.index("mp") if "mp" in head else None
    for r in rows[1:]:
        try:
            out.append((int(r[0]), r[1], int(r[2])))
            if pay is not None and i_pay is not None:
                pay[int(r[0])] = (int(r[i_pay]),
                                  int(r[i_mp]) if i_mp is not None else 0)
        except (ValueError, IndexError):
            continue
    return out


RANK_PAY = {}
RANK_LADDER = load_rank_ladder(pay=RANK_PAY)


def rank_pay(rank):
    """(H$, MP) one payday pays a rank byte; (0, 0) off the table."""
    return RANK_PAY.get(int(rank) & 0xFF, (0, 0))
RANK_MAX_EARNABLE = 20                 # Captain: SE's contribution ceiling (above it, period review)


def rank_name(rank):
    for r, name, _t in RANK_LADDER:
        if r == rank:
            return name
    return "?"


def rank_for_contribution(contrib, ladder=None):
    """The highest rank byte (0..RANK_MAX_EARNABLE) whose threshold <= contrib;
    0 (Conscript) if the ladder is missing or nothing is met."""
    ladder = RANK_LADDER if ladder is None else ladder
    best = 0
    for r, _name, thr in ladder:
        if r <= RANK_MAX_EARNABLE and thr <= int(contrib) and r > best:
            best = r
    return best


def rank_threshold(rank, ladder=None):
    """The contribution threshold of one ladder row, or None off the ladder."""
    for r, _name, thr in (RANK_LADDER if ladder is None else ladder):
        if r == rank:
            return thr
    return None
