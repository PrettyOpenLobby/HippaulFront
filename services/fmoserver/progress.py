"""Mission progress flags: what a pilot has completed and what the scripts may offer next."""
import os
from .deps import fmostore


#: KEY: PROGRESSION (2026-09-08). The campaign is data now --
#: fmodata/fmo-missions.tsv, generated from the mission catalogue, the NPC
#: event tables and the LEV zone table (generated offline from the client)
#: -- and this is the one server-side step that makes it MOVE: when a pilot
#: comes back from a sortie, the mission at their FRONTIER is marked done.
#:
#: The frontier is state, not a message. A 0x0139's infoid is a sector-table
#: row, not a story-mission id, so the sortie cannot name the mission; but the
#: pilot's flags and rank can. A mission is at the frontier when its own
#: progress byte is known and not yet 99, its level is within the pilot's
#: rank, and its prerequisite is either none or a mission whose byte is
#: known and already 99. Exactly one such mission -> set its byte to 99;
#: none -> nothing; several -> nothing, logged (the table marks 28 rows as
#: ambiguous and this refuses to guess between them).
#:
#: FMO_PROGRESS_RANK_FOLLOW (default on): a completed mission also raises
#: the pilot's rank to the lowest level among its successors, because the
#: event rows gate on rank and there is no economy yet to raise it -- without
#: this the very next cutscene refuses on rank and continuity stops after one
#: step. It is a stand-in for the Personnel Officer's promotions, and it says
#: so in the log.
#:
#: What counts as "back from a sortie": a 0x0139 this server actually
#: GRANTED, followed by this session's next 0x0150 world-entry join -- except
#: a 0x013D withdraw (its follow-up 0x0150 carries word 0xFFFD), which never
#: counts. A win and a loss are not told apart here; nothing decoded yet says
#: which one it was. FMO_PROGRESS_ADVANCE=0 turns the whole step off.
#:
#: WARNING: STATIC. The first loop to watch is Defend Sakata Industry -> Special Mobile
#: Force Induction Test: rank 24, byte[135]=99 (O.C.U.) / byte[134]=99
#: (U.S.N.), byte[173]=0; sortie; on return byte[173] becomes 99 and rank 29,
#: and the next talk to the operator should play the induction-test scene.
PROGRESS_ADVANCE = (os.environ.get("FMO_PROGRESS_ADVANCE", "").strip() or "1") != "0"
PROGRESS_RANK_FOLLOW = (os.environ.get("FMO_PROGRESS_RANK_FOLLOW", "").strip()
                        or "1") != "0"
MISSIONS_TSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "fmodata", "fmo-missions.tsv")


def load_missions(path=MISSIONS_TSV):
    """{nation: [{title, level, pre, own, pre_byte}]} from fmo-missions.tsv.
    Missing file -> empty (the step logs that it has no table)."""
    out = {1: [], 2: []}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            nat = {"O.C.U.": 1, "U.S.N.": 2}.get(r.get("faction"))
            if not nat:
                continue
            asint = lambda k: int(r[k]) if r.get(k, "").strip() else None
            out[nat].append({"title": r["title"], "level": asint("level"),
                             "pre": r.get("prerequisite") or None,
                             "own": asint("own_byte"),
                             "pre_byte": asint("prereq_byte")})
    return out


MISSIONS = load_missions()


def progress_done(char, missions=None):
    """set(title): what this pilot has COMPLETED -- every mission whose own
    byte is 99, plus (transitively) every prerequisite of one of those,
    because a mission cannot have been completed without them.

    2026-09-11: the table now knows the bytes of the EARLY missions (Enemy
    Unit Annihilation 130, the EMP Carrier 137, ...). Read literally, a pilot
    seeded straight to "Enemy Prototype Weapon Sighted done" (135=99) has
    those at 0 and therefore OPEN, and the frontier refuses as ambiguous
    where it used to walk. Their completion is implied by the chain; this is
    where that implication lives. Pure."""
    missions = MISSIONS if missions is None else missions
    nat, _src = popnation.character_nation(char)
    if nat not in (1, 2):
        return set()
    fl = fmostore.flags_bytes(char.get("flags")) if fmostore else b""
    fl = fl.ljust(256, b"\0")
    by_title = {m["title"]: m for m in missions[nat]}
    done = {m["title"] for m in missions[nat]
            if m["own"] is not None and fl[m["own"]] == 99}
    todo = list(done)
    while todo:
        m = by_title.get(todo.pop())
        if m and m["pre"] and m["pre"] not in done:
            done.add(m["pre"])
            todo.append(m["pre"])
    return done


def progress_frontier(char, missions=None):
    """[mission] a pilot can complete NEXT: own byte known and the mission
    not done, level within rank, prerequisite none or done -- where "done"
    is progress_done(): own byte 99, or implied by a later mission's 99.
    Pure."""
    missions = MISSIONS if missions is None else missions
    nat, _src = popnation.character_nation(char)
    if nat not in (1, 2):
        return []
    rank = char.get("rank")
    rank = int(rank) if rank is not None else int(status.START_RANK)
    done = progress_done(char, missions)
    out = []
    for m in missions[nat]:
        if m["own"] is None or m["title"] in done:
            continue
        if m["level"] is not None and rank < m["level"]:
            continue
        if m["pre"] and m["pre"] not in done:
            continue
        out.append(m)
    return out


def advance_progress(char, missions=None):
    """Mark the frontier mission done in `char` (the record, not the store --
    the caller commits). -> (mission or None, what happened, rank_after)."""
    missions = MISSIONS if missions is None else missions
    if not fmostore:
        return None, "no character store", None
    fr = progress_frontier(char, missions)
    if not fr:
        return None, "nothing at the frontier (no mission with a known byte is open at this rank)", None
    if len(fr) > 1:
        return None, ("ambiguous frontier, refusing to guess: "
                      + " | ".join(m["title"] for m in fr)), None
    m = fr[0]
    fmostore.set_flag_byte(char, m["own"], 99)
    nat, _ = popnation.character_nation(char)
    rank_after = None
    if PROGRESS_RANK_FOLLOW:
        succ = [x["level"] for x in missions[nat]
                if x["pre"] == m["title"] and x["level"] is not None]
        rank = int(char.get("rank") or status.START_RANK)
        if succ and rank < min(succ):
            char["rank"] = rank_after = min(succ)
    return m, "byte[%d]=99" % m["own"], rank_after


# Called at run time only; imported last so that import cycles resolve.
from . import popnation, status  # noqa: E402
