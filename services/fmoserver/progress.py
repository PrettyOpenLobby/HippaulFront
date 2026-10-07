"""Mission progress flags: what a pilot has completed and what the scripts may offer next."""
import json
import os
from .deps import fmostore
from .knobs import _env_int


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
#:
#: KEY: 2026-09-30 (fmo-gates.json, tools/fmodatagen/fmogates.py): a mission's
#: "level" and every event row's level window are the PILOT level -- the
#: Pilot class's (12) level from its exp (0x611782B0, the D15 curve) -- NOT the
#: rank byte. So the frontier compares the Pilot level, and after a completion
#: FMO_PROGRESS_LEVEL_FOLLOW (default on) raises the Pilot level to the
#: successors' level: without it no pilot ever leaves level 1 (nothing else
#: pays Pilot exp yet) and every story row past the first stays shut.
#: FMO_PROGRESS_RANK_FOLLOW keeps its old meaning; no event row reads rank.
#:
#: WHICH mission a sortie completed: the client's own LobbySally compares the
#: sortie's tile with each mission's tiles (fmo-gates.json byte_map
#: sortie_tiles), so when the sortie came through a picked sector (0x015E)
#: the tile names the mission: of the frontier, only missions whose tiles
#: contain it can complete, and a tile that is no open mission's completes
#: nothing. With no tile (a sortie not through the Map Selector) the old rule
#: stands: exactly one frontier mission, or nothing.
#:
#: KEY: 2026-09-30, THE IN-PROGRESS STATES (static, the SCP bytecode; fmogates'
#: decoder, code offsets). A story mission's own byte walks 0 -> 1 -> 2 -> 3 -> 99
#: and the scripts tell each step apart:
#:   * Son's nina_event (SCP 0x8071, AI/F00/D89) switches on byte 130 at
#:     0x393a: 0 plays the offer and then calls srv_104(130) at 0x3a48;
#:     1 AND 2 both run the same reminder (0x2fdc); 3 plays the clear scene and
#:     calls srv_105(130) at 0x3b9e; 99 runs the epilogue (0x3708). The same
#:     shape for byte 137 at 0x53c6 (0 -> 104, 3 -> 105), and every room /
#:     hangar script picks 104 or 105 on the pilot's nation (D99 0x28d6/0x2916).
#:   * LobbySally (SCP 0x8070) calls srv_104(byte) when byte == 1 and the
#:     picked sortie tile (syscall 0xE30B) is one of the mission's tiles
#:     (0xd7c2..0xd81c for byte 130).
#:   * No script writes a mission byte: E066/E067 only read, and the server
#:     calls carry the byte index. So 1 -> 2 -> 3 -> 99 is server state.
#: INFERRED from that: 104 at byte 0 is the ACCEPT (-> 1), 104 at byte 1 from
#: the LobbySally is "this sortie is the mission" (-> 2), a sortie back on the
#: mission's tile is the clear (-> 3, which is what opens the clear scene and
#: its 105), and the 105 is the REPORT (-> 99). A win and a loss are still not
#: told apart on return (see above), so any return on the tile counts.
#: FMO_MISSION_CLEAR_REPORT (default 1): a return sets 3 when some script
#: reports that byte with 105 (fmo-gates.json), and 99 only for the four bytes
#: no script reports (155/156 Destroy the Rebels, 165/166 Mock Combat Test),
#: which would otherwise sit at 3 for good. 0 = straight to 99 on return.
PROGRESS_ADVANCE = (os.environ.get("FMO_PROGRESS_ADVANCE", "").strip() or "1") != "0"
MISSION_CLEAR_REPORT = _env_int("FMO_MISSION_CLEAR_REPORT", 1) != 0
#: the in-progress values of a mission byte, and the one a clear leaves
IN_PROGRESS = (1, 2, 3)
CLEARED = 3
DONE = 99
PROGRESS_RANK_FOLLOW = (os.environ.get("FMO_PROGRESS_RANK_FOLLOW", "").strip()
                        or "1") != "0"
PROGRESS_LEVEL_FOLLOW = (os.environ.get("FMO_PROGRESS_LEVEL_FOLLOW", "").strip()
                         or "1") != "0"
PILOT_CLASS = 12
_FMODATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata")
MISSIONS_TSV = os.path.join(_FMODATA, "fmo-missions.tsv")
GATES_JSON = os.path.join(_FMODATA, "fmo-gates.json")


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


def load_mission_tiles(path=GATES_JSON):
    """{own byte: set(sortie tiles)} from fmo-gates.json's byte_map. Missing
    file -> {} (then no sortie can name its mission and the old rule holds)."""
    try:
        with open(path, encoding="utf-8") as f:
            bm = json.load(f).get("byte_map") or []
    except (OSError, ValueError):
        return {}
    return {int(b["byte"]): {int(t) for t in b.get("sortie_tiles") or []}
            for b in bm if b.get("byte") is not None and b.get("sortie_tiles")}


MISSION_TILES = load_mission_tiles()


def load_report_bytes(path=GATES_JSON):
    """set(byte): every mission byte some script REPORTS with a server call
    105 [byte] (fmo-gates.json scripts[*].server_calls). A byte in here has a
    clear scene that ends in the report, so a clear may stop at 3. Missing
    file -> empty (then every clear goes straight to 99)."""
    try:
        with open(path, encoding="utf-8") as f:
            scripts = json.load(f).get("scripts") or []
    except (OSError, ValueError):
        return set()
    return {int(c["params"][0]) for s in scripts for c in s.get("server_calls") or []
            if c.get("event") == 105 and c.get("params")
            and isinstance(c["params"][0], int)}


REPORT_BYTES = load_report_bytes()


def load_rewards(path=GATES_JSON):
    """{byte: {"money": H$, "items": {nation or "any": {kind, name}}}}: what a
    reported mission pays, as SE's reward routine prints it (fmo-gates.json
    `rewards`, fmogates.mission_rewards). Missing file -> {} (nothing pays)."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f).get("rewards") or {}
    except (OSError, ValueError):
        return {}
    return {int(b): v for b, v in raw.items()}


REWARDS = load_rewards()
#: the transit pass a reward "ticket" line names (permits.PASS_KEYS "oc")
REWARD_PASS = {"Pass: OC-O.C.U.": 27, "Pass: OC-U.S.N.": 28}


def mission_reward(byte, nation=None, rewards=None):
    """(money, item or None) for reporting mission byte `byte`, item picked
    for `nation` (1 O.C.U., 2 U.S.N.); None when the byte pays nothing."""
    r = (REWARDS if rewards is None else rewards).get(int(byte))
    if not r or not (r.get("money") or r.get("items")):
        return None
    items = r.get("items") or {}
    item = items.get({1: "O.C.U.", 2: "U.S.N."}.get(nation)) or items.get("any") \
        or (next(iter(items.values())) if items else None)
    return int(r.get("money") or 0), item


def rewards_due(char, before, after, rewards=None):
    """[(byte, money, item)] for every story byte that went 3 -> 99 between
    two flag blocks: the reports this script call just made."""
    nat, _src = popnation.character_nation(char)
    out = []
    for i in sorted(story_bytes()):
        if i < len(after) and after[i] == DONE and i < len(before) and before[i] == CLEARED:
            rw = mission_reward(i, nat, rewards)
            if rw:
                out.append((i, rw[0], rw[1]))
    return out


def story_bytes(missions=None):
    """set(byte): every catalogue mission's own byte, both nations. What a
    104/105 must name before the server moves anything (byte 128, the
    registration, also comes through 104/105 and is NOT a mission)."""
    missions = MISSIONS if missions is None else missions
    return {m["own"] for rows in missions.values() for m in rows if m.get("own") is not None}


def mission_by_own(char, own, missions=None):
    """The pilot's own-nation mission whose byte is `own`, or None."""
    missions = MISSIONS if missions is None else missions
    nat, _src = popnation.character_nation(char)
    return next((m for m in missions.get(nat, []) if m["own"] == own), None)


def completion_follow(char, m, missions=None):
    """After mission `m` went to 99: raise the Pilot level (and the rank) to
    its successors' level, as the frontier advance always has.
    -> (text to append, rank_after or None). Mutates `char`."""
    missions = MISSIONS if missions is None else missions
    nat, _ = popnation.character_nation(char)
    succ = [x["level"] for x in missions.get(nat, [])
            if x["pre"] == m["title"] and x["level"] is not None]
    what = ""
    if PROGRESS_LEVEL_FOLLOW and succ and pilot_level(char) < min(succ):
        lv = set_pilot_level(char, min(succ))
        what += (", Pilot level -> %d (FMO_PROGRESS_LEVEL_FOLLOW)" % lv if lv
                 else ", Pilot level NOT raised: no class exp curve")
    rank_after = None
    if PROGRESS_RANK_FOLLOW:
        rank = int(char.get("rank") or status.START_RANK)
        if succ and rank < min(succ):
            char["rank"] = rank_after = min(succ)
    return what, rank_after


def completions_follow(char, before, after, missions=None):
    """completion_follow for every own-nation mission byte that is 99 in
    `after` and was not in `before` (two flag blocks). -> [text]."""
    out = []
    for i in sorted(story_bytes(missions)):
        if i < len(after) and after[i] == DONE and (i >= len(before) or before[i] != DONE):
            m = mission_by_own(char, i, missions)
            if m:
                what, ra = completion_follow(char, m, missions)
                out.append("'%s' complete%s%s" % (m["title"], what,
                                                  ", rank -> %d" % ra if ra else ""))
    return out


def pilot_level(char):
    """The Pilot class's level: what a mission's level and an event row's
    level window test."""
    return classes.class_level(classes.class_exp_of(char).get(PILOT_CLASS, 0))


def set_pilot_level(char, level):
    """Raise the Pilot class's exp to the start of `level` (never lowers it).
    -> the level now, or None when the curve is missing."""
    curve = classes.CLASS_CURVE
    if not curve or not 1 <= level <= len(curve):
        return None
    exp = classes.class_exp_of(char)
    want = curve[level - 1] if level > 1 else 0
    if exp.get(PILOT_CLASS, 0) < want:
        exp[PILOT_CLASS] = want
        char["class_exp"] = {str(k): v for k, v in sorted(exp.items())}
    return pilot_level(char)


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


def progress_frontier(char, missions=None, tile=None, tiles=None):
    """[mission] a pilot can complete NEXT: own byte known and the mission
    not done, level within the Pilot level, prerequisite none or done --
    where "done" is progress_done(): own byte 99, or implied by a later
    mission's 99. With a sortie `tile`, only missions whose sortie tiles
    contain it. Pure."""
    missions = MISSIONS if missions is None else missions
    tiles = MISSION_TILES if tiles is None else tiles
    nat, _src = popnation.character_nation(char)
    if nat not in (1, 2):
        return []
    level = pilot_level(char)
    done = progress_done(char, missions)
    out = []
    for m in missions[nat]:
        if m["own"] is None or m["title"] in done:
            continue
        if m["level"] is not None and level < m["level"]:
            continue
        if tile is not None and tiles and int(tile) not in tiles.get(m["own"], ()):
            continue
        if m["pre"] and m["pre"] not in done:
            continue
        out.append(m)
    return out


def advance_progress(char, missions=None, tile=None, tiles=None):
    """Mark the frontier mission done in `char` (the record, not the store --
    the caller commits). `tile` is the sortie's sector tile when it is known.
    -> (mission or None, what happened, rank_after)."""
    missions = MISSIONS if missions is None else missions
    tiles = MISSION_TILES if tiles is None else tiles
    if not fmostore:
        return None, "no character store", None
    by_tile = tile is not None and bool(tiles)
    if by_tile:
        # an ACCEPTED mission (byte 1..3) whose tiles hold the sortie's tile is
        # the one this sortie was for: LobbySally only calls 104 on byte == 1
        # and one of the mission's own tiles (SCP 0x8070 0xd7c2..0xd81c)
        m, what, rank_after = _clear_in_progress(char, missions, int(tile), tiles)
        if m is not None or what:
            return m, what, rank_after
    fr = progress_frontier(char, missions, tile if by_tile else None, tiles)
    if not fr:
        return None, ("the sortie's tile %s is no open mission's -- nothing completed" % tile
                      if by_tile else
                      "nothing at the frontier (no mission with a known byte is open "
                      "at this Pilot level)"), None
    if len(fr) > 1:
        return None, ("ambiguous frontier, refusing to guess: "
                      + " | ".join(m["title"] for m in fr)), None
    m = fr[0]
    fmostore.set_flag_byte(char, m["own"], 99)
    what = "byte[%d]=99" % m["own"] + (" (the sortie's tile %s)" % tile if by_tile else "")
    more, rank_after = completion_follow(char, m, missions)
    return m, what + more, rank_after


def _clear_in_progress(char, missions, tile, tiles):
    """The return step for an accepted mission: the own-nation mission whose
    byte is 1..3 and whose tiles hold `tile` moves to 3 (cleared, the NPC's
    clear scene and its 105 report come next) or, when no script reports
    it or FMO_MISSION_CLEAR_REPORT=0, to 99. -> (mission or None, what,
    rank_after); (None, "", None) when no accepted mission owns the tile."""
    nat, _src = popnation.character_nation(char)
    if nat not in (1, 2):
        return None, "", None
    fl = fmostore.flags_bytes(char.get("flags")).ljust(256, b"\0")
    hit = [m for m in missions.get(nat, [])
           if m["own"] is not None and fl[m["own"]] in IN_PROGRESS
           and tile in tiles.get(m["own"], ())]
    if not hit:
        return None, "", None
    if len(hit) > 1:
        return None, ("the sortie's tile %s belongs to several accepted missions, "
                      "refusing to guess: " % tile + " | ".join(m["title"] for m in hit)), None
    m = hit[0]
    was = fl[m["own"]]
    to = CLEARED if (MISSION_CLEAR_REPORT and m["own"] in REPORT_BYTES) else DONE
    if was == to:
        # nothing moved, so nothing to commit: the caller logs this as text
        return None, ("'%s' byte[%d] is already %d (cleared, waiting for its report)"
                      % (m["title"], m["own"], was)), None
    fmostore.set_flag_byte(char, m["own"], to)
    what = "byte[%d] %d -> %d (the sortie's tile %s)" % (m["own"], was, to, tile)
    if to == CLEARED:
        return m, what + ": cleared, the report (105) completes it", None
    more, rank_after = completion_follow(char, m, missions)
    return m, what + more, rank_after


# Called at run time only; imported last so that import cycles resolve.
from . import classes, popnation, status  # noqa: E402
