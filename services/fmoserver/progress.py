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
#: FMO_PROGRESS_RANK_FOLLOW (default OFF since 2026-10-08): a completed
#: mission also raised the pilot's rank to the lowest level among its
#: successors. That was written when the event rows were thought to gate on
#: rank; they gate on the Pilot level (below), and SE promoted only through
#: the Personnel Officer by contribution (update 050719qk2ld8: contribution
#: promotes up to Captain, rank byte 20). Left in as a test lever: when on,
#: the value it writes is clamped to ranks.RANK_MAX_EARNABLE, because the
#: mission levels (9, 24, 41, 50) are not rank rows and 50 is past the
#: 48-row ladder the client indexes by the raw byte.
#:
#: What counts as "back from a sortie" (2026-10-08): the BATTLE VERDICT.
#: Retail advanced the mission on the battle result, so the step runs from
#: the battle end (settlement.story_battle_settle, where won/lost is known),
#: with the tile the sortie was TAKEN on (sortie.py snapshots it at the
#: 0x013A grant; the pilot may have opened another sector since). A loss
#: leaves the byte where it is (1 or 2: the scripts play the same re-brief
#: for both, so the pilot simply sorties again); a 0x013D withdraw never
#: reaches the battle end and so never counts. Until 10-08 the step hung off
#: the post-battle 0x0150 word, which is always 0xFFFD after a 0x014C, so it
#: had never fired on a deployment. FMO_PROGRESS_ADVANCE=0 turns it off.
#:
#: WARNING: STATIC. The first loop to watch is Defend Sakata Industry -> Special Mobile
#: Force Induction Test: rank 24, byte[135]=99 (O.C.U.) / byte[134]=99
#: (U.S.N.), byte[173]=0; sortie; on return byte[173] becomes 99 and rank 29,
#: and the next talk to the operator should play the induction-test scene.
#:
#: KEY: 2026-09-30 (fmo-gates.json, tools/fmodatagen/fmogates.py): a mission's
#: "level" and every event row's level window are the PILOT level -- the
#: Pilot class's (12) level from its exp (0x611782B0, the D15 curve) -- NOT the
#: rank byte. So the frontier compares the Pilot level. FMO_PROGRESS_LEVEL_FOLLOW
#: (default off since 2026-10-08) raised the Pilot level to the successors'
#: level after a completion, from when nothing paid Pilot exp.
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
#: its 105), and the 105 is the REPORT (-> 99). Only a WON battle on the tile
#: counts (see above); a lost one leaves the byte for the retry.
#: FMO_MISSION_CLEAR_REPORT (default 1): a return sets 3 when some script
#: reports that byte with 105 (fmo-gates.json), and 99 only for the four bytes
#: no script reports (155/156 Destroy the Rebels, 165/166 Mock Combat Test),
#: which would otherwise sit at 3 for good. 0 = straight to 99 on return.
PROGRESS_ADVANCE = (os.environ.get("FMO_PROGRESS_ADVANCE", "").strip() or "1") != "0"
#: KEY: FMO_PROGRESS_NEED_ACCEPT (default 1): a battle
#: verdict completes only a mission the pilot ACCEPTED (byte 1..3) whose
#: sortie tiles hold the sortie's tile. Retail: the LobbySally calls 104 only
#: on byte 1 and one of the mission's tiles, and the clear scene with its 105
#: report needs byte 3. The older frontier rule (any open mission at the
#: Pilot level, byte 0 included, by tile or the one open mission with no tile)
#: dates from before the accept was tracked: a Lv6 pilot winning an ordinary
#: battle on Ctrl.01 S12 without ever talking to Son had byte 130 set to 99,
#: skipping the offer, the mission's own battle and its reward. 0 = that
#: older rule, a test lever.
PROGRESS_NEED_ACCEPT = _env_int("FMO_PROGRESS_NEED_ACCEPT", 1) != 0
MISSION_CLEAR_REPORT = _env_int("FMO_MISSION_CLEAR_REPORT", 1) != 0
#: the in-progress values of a mission byte, and the one a clear leaves
IN_PROGRESS = (1, 2, 3)
CLEARED = 3
DONE = 99
PROGRESS_RANK_FOLLOW = (os.environ.get("FMO_PROGRESS_RANK_FOLLOW", "").strip()
                        or "0") != "0"
#: KEY: FMO_PROGRESS_LEVEL_FOLLOW, default OFF.
#: Retail raised the Pilot level only by battle exp; the follow was written
#: when nothing paid Pilot exp, and it jumped a pilot to the successor's level
#: on completion: Destroy the Enemy Remnants (Lv8) -> Assault the Bomb
#: Carriers (Lv27) took a Lv8 pilot to Lv27, past the Occupied and Frontline
#: tiers and Son's Lv6-16 arc. With the exp formula (battleend.EXP_FORMULA)
#: every row's window is reached by battle (level_plan below). 1 = the old
#: jump, a test lever.
PROGRESS_LEVEL_FOLLOW = (os.environ.get("FMO_PROGRESS_LEVEL_FOLLOW", "").strip()
                         or "0") != "0"
PILOT_CLASS = 12
#: KEY: BIT 17 = "transferred to the Frontline Zone" (2026-10-08). The only
#: client writer is script 0x8035, which no event row reaches; the First
#: Sergeant (0x8074 0x8b6e, 0x8075 0x899a) reads it for one line ("So your
#: transfer to the Frontline Zone has been decided"). SE's tutorial puts the
#: transfer at Pilot level 10 in every job plus 170,000 H$ (0x8035 text),
#: which this client never runs. OURS: the bit is set the first time the
#: pilot enters a Frontline zone (MapKind 5xx), which is when the transfer
#: has in fact happened (the Change Area gate already needs Pilot level 20).
FRONTLINE_BIT = 17
FRONTLINE_ZONE_KIND = 5
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
#: KEY: THE PAINT A REWARD LINE NAMES (2026-10-08): (kind, name) as SE's
#: reward routine prints it -> (owned-paint category, the client's row id).
#: Read off the client's own tables Data/BB/F13/D29 (camouflage), D30
#: (colours) and D31 (emblems) by name; every one of these rows carries
#: nation flag 255, which is why fmo-cosmetics.tsv (the shop catalogue,
#: placeholders dropped) does not list them and the shop cannot sell them:
#: they are reward-only, so the report has to grant them. The colour table
#: holds each name twice (19 and 264 Azure Blue, 46 and 291 Mauve); the
#: starting colours (1, 6, 42) and every sold colour (2..126) are in the
#: lower half, so the lower id is granted (OURS: which half the colour
#: pickers draw is unread; SE's server may have granted both). Granted as the
#: owned bit inventory.add_owned_paint sets (0x014A's owned block, the 0x015A
#: answer push's copy of it), the same path a paint-shop purchase takes.
REWARD_PAINT = {
    ("armor color", "Mauve"): ("colour", 46),
    ("armor color", "Azure Blue"): ("colour", 19),
    ("camo color", "Blue 1"): ("camo", 132),
    ("camo color", "Dark Green 1"): ("camo", 114),
    ("camo color", "Purple Ribbon"): ("camo", 284),
    ("camo color", "Myth"): ("camo", 269),
    ("squadron insignia", "Chasm Owls"): ("insignia", 110),
    ("insignia", "17th Mobile Squadron"): ("insignia", 417),
    ("squadron insignia", "Iron Hearts"): ("insignia", 111),
    ("squadron insignia", "Proud Eagles"): ("insignia", 422),
}
#: KEY: THE BASE SQUADRON INSIGNIA (2026-10-08, static, SCP 0x8074 0x737e..
#: 0x7436 and its U.S.N. twin): the First Sergeant's "cleared to sortie"
#: scene prints "Obtained the squadron insignia 'Squadron Insignia NN'" by
#: the MapKind of the talk (0xE064): O.C.U. HQ 100..109 -> 81..90, U.S.N. HQ
#: 300..309 -> 101..110, the Occupied bases 203/204 -> 81/82 and 401/402 ->
#: 101/102, anything else -> 81. The names are D31 rows 115..124 (O.C.U.
#: 81st..90th Defense Squadron) and 401..410 (U.S.N. 101st..110th), flag 255
#: like the mission rewards. The script only prints; SE's server granted.
BASE_INSIGNIA_FIRST = {1: 115, 3: 401}
BASE_INSIGNIA_OCC = {203: 115, 204: 116, 401: 401, 402: 402}
REGISTRATION_BYTE = 128
REGISTRATION_CLEARED = 2
#: KEY: CONTRIBUTION FOR A REPORT (2026-10-08). srv_105's library routine
#: prints "Contribution points earned." when the contribution (E06F,
#: lobby+0xFC8) rose across the call, so SE's answer push carried a
#: contribution delta with the report; the sergeant's "cleared to sortie"
#: (105 [128]) has the same line. The amount is on no page we hold and the
#: decoded reward table (fmo-gates.json rewards) has no contribution column.
#: OURS: FMO_MISSION_REPORT_CONTRIB, default "win" = what one WON battle pays
#: in sortie plus victory contribution (FMO_RESULT_CONTRIB, falling back to
#: FMO_BATTLE_END_CONTRIB, plus FMO_WIN_CONTRIB); an integer pays that
#: amount; 0 pays nothing (the pre-10-08 behaviour, and the line stays off).
REPORT_CONTRIB_RAW = os.environ.get("FMO_MISSION_REPORT_CONTRIB", "").strip() or "win"


def report_contribution(spec=None):
    """The contribution one report (105) credits, from FMO_MISSION_REPORT_CONTRIB."""
    spec = (REPORT_CONTRIB_RAW if spec is None else spec).strip().lower()
    if spec == "win":
        return max(0, (resultpush.RESULT_CONTRIB or battleend.BATTLE_END_CONTRIB)
                   + battleend.WIN_CONTRIB)
    try:
        return max(0, int(spec, 0))
    except ValueError:
        return 0


def reward_paint(item):
    """(category, id) for a reward item dict {kind, name}, or None when the
    item is not a paint this table knows (a ticket, or an unknown name)."""
    if not item:
        return None
    return REWARD_PAINT.get((str(item.get("kind") or ""), str(item.get("name") or "")))


def base_insignia_for_zone(mapkind):
    """The D31 insignia id the sergeant's registration scene names for a
    talk in zone `mapkind` (SCP 0x8074 switch). Pure."""
    try:
        mk = int(mapkind)
    except (TypeError, ValueError):
        return BASE_INSIGNIA_FIRST[1]
    if mk in BASE_INSIGNIA_OCC:
        return BASE_INSIGNIA_OCC[mk]
    first = BASE_INSIGNIA_FIRST.get(mk // 100)
    if first is not None and 0 <= mk % 100 <= 9:
        return first + mk % 100
    return BASE_INSIGNIA_FIRST[1]


def registration_reported(before, after):
    """True when byte 128 went REGISTRATION_CLEARED -> DONE between two flag
    blocks: the sergeant's 105 [128] that hands over the base insignia."""
    i = REGISTRATION_BYTE
    return (i < len(before) and i < len(after)
            and before[i] == REGISTRATION_CLEARED and after[i] == DONE)


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
        # a test lever (see the header): the successor's LEVEL is not a rank
        # row, so it is clamped to the highest rank contribution can earn
        rank = int(char.get("rank") or status.START_RANK)
        want = min(min(succ), ranks.RANK_MAX_EARNABLE) if succ else None
        if want is not None and rank < want:
            char["rank"] = rank_after = want
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


def advance_progress(char, missions=None, tile=None, tiles=None, won=True):
    """Mark the frontier mission done in `char` (the record, not the store --
    the caller commits). `tile` is the sortie's sector tile when it is known;
    `won` is the battle verdict: a LOST battle moves nothing, the mission
    byte stays in progress and the pilot sorties again.
    -> (mission or None, what happened, rank_after)."""
    missions = MISSIONS if missions is None else missions
    tiles = MISSION_TILES if tiles is None else tiles
    if not fmostore:
        return None, "no character store", None
    if not won:
        return None, ("the battle was LOST: the mission byte stays in progress "
                      "(the scripts re-brief on 1 and 2 alike) and the pilot sorties "
                      "again"), None
    by_tile = tile is not None and bool(tiles)
    if by_tile:
        # an ACCEPTED mission (byte 1..3) whose tiles hold the sortie's tile is
        # the one this sortie was for: LobbySally only calls 104 on byte == 1
        # and one of the mission's own tiles (SCP 0x8070 0xd7c2..0xd81c)
        m, what, rank_after = _clear_in_progress(char, missions, int(tile), tiles)
        if m is not None or what:
            return m, what, rank_after
    if PROGRESS_NEED_ACCEPT:
        return None, ("no ACCEPTED story mission (byte 1..3) has the sortie's tile %s "
                      "among its sortie tiles -- a mission is accepted at its NPC first "
                      "(104: 0 -> 1); FMO_PROGRESS_NEED_ACCEPT" % tile), None
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


def wins_to_level(level, curve=None, kills=3):
    """How many solo WON battles with `kills` kills (sector scale 100%, no
    platoon bonus, no story bonus) the exp formula needs to take a fresh pilot
    from Lv1 to Pilot `level`. Pure. -> int, or None with no curve."""
    curve = classes.CLASS_CURVE if curve is None else curve
    if not curve or level > len(curve):
        return None
    wins = 0
    for lv in range(1, max(1, int(level))):
        per = battleend.formula_exp_total(lv, True, kills, 0, curve)
        if per <= 0:
            return None
        need = curve[lv] - curve[lv - 1]
        wins += -(-need // per)
    return wins


def level_plan(nation, missions=None, curve=None, cap=None):
    """[(title, byte, level, prerequisite, exp to reach the level, solo 3-kill
    wins to reach it)] for one nation's catalogue, in level order, and the
    problems: a row whose level is past the Pilot cap, or one whose
    prerequisite needs a higher level than the row itself (a window that
    opens before its chain can reach it). Pure. -> (rows, problems)."""
    missions = MISSIONS if missions is None else missions
    curve = classes.CLASS_CURVE if curve is None else curve
    cap = classes.PILOT_LEVEL_CAP if cap is None else cap
    rows, problems = [], []
    by_title = {m["title"]: m for m in missions.get(nation, [])}
    for m in sorted(missions.get(nation, []), key=lambda x: (x["level"] or 0, x["own"] or 0)):
        lv = m["level"] or 1
        exp = curve[lv - 1] if curve and lv <= len(curve) else None
        rows.append((m["title"], m["own"], lv, m["pre"], exp, wins_to_level(lv, curve)))
        if lv > cap:
            problems.append("%s: Lv%d is past the Pilot cap Lv%d" % (m["title"], lv, cap))
        pre = by_title.get(m["pre"]) if m["pre"] else None
        if pre and (pre["level"] or 0) > lv:
            problems.append("%s: Lv%d, but its prerequisite %s needs Lv%d"
                            % (m["title"], lv, pre["title"], pre["level"]))
    return rows, problems


def note_frontline_entry(char, mapkind):
    """Set FRONTLINE_BIT on the record the first time `mapkind` is a
    Frontline zone (5xx). -> True when the bit was just set (the caller
    commits and pushes the flags), else False. Mutates `char`."""
    if not fmostore or char is None or mapkind is None:
        return False
    try:
        if int(mapkind) // 100 != FRONTLINE_ZONE_KIND:
            return False
    except (TypeError, ValueError):
        return False
    fl = fmostore.flags_bytes(char.get("flags")).ljust(256, b"\0")
    if fl[FRONTLINE_BIT >> 3] & (1 << (FRONTLINE_BIT & 7)):
        return False
    fmostore.set_flag_bit(char, FRONTLINE_BIT)
    return True


# Called at run time only; imported last so that import cycles resolve.
from . import battleend, classes, popnation, ranks, resultpush, status  # noqa: E402
