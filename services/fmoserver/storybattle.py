"""Story missions' own battles: the enemies, the allied units, the objective and the verdict a
story mission sortie is fought with (fmodata/fmo-story-battles.tsv)."""
import os
import random
import struct
from .deps import fmoworld
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# KEY: SE'S RULES FOR A SPECIAL MISSION (特別任務, the story missions)
#   * BOOT CAMP Lesson 5 (pcd/topics/fmo/detail/1462, archived 2007-05-23):
#     "特別任務は、通常の戦闘とちごうて、出てくる敵ユニットとか勝利条件がちゃう
#     ことがあんねん" -- a special mission's enemy units and victory conditions
#     differ from an ordinary battle; some need a strong enough battle group to
#     sortie at all; success pays more exp and contribution. And: "バトルグループ
#     で特別任務を遂行する場合は、任務を受けているプレイヤーがリーダーである必要
#     があります ... リーダー以外に、同じ特別任務を受けているプレイヤーがいても、
#     そのプレイヤーの特別任務は遂行されたことになりません" -- in a battle group
#     only the LEADER's mission is carried out; a member with the same mission
#     accepted does not complete it (STORY_LEADER_ONLY).
#   * topics/060804 (the matching sortie, 2006-08): "BGのリーダーがそのセクターを
#     対象とした「特別任務」を受けている場合は待機状態にならず、すぐに特別任務の
#     戦闘が開始されます" -- when the leader has a special mission for that
#     sector, the battle map does not wait for opponents; the special mission's
#     battle starts at once (STORY_NO_WAIT; sortie.py skips the PvP room).
#   * The catalogue (AI/F00/D16, D18) prints each mission's 勝利条件 / 敗北条件
#     and its 任務詳細; tools/fmodatagen/fmostorybattles.py turns them into
#     fmo-story-battles.tsv. The unit counts, HP scales and the time limits the
#     details do not state are OURS (the tool says so row by row).
# --------------------------------------------------------------------------- #
#: FMO_STORY_BATTLES (default 1): a story mission sortie is fought with the
#: mission's own units and objective; 0 = the ordinary squad as before.
STORY_BATTLES = _env_int("FMO_STORY_BATTLES", "1") != 0
#: FMO_STORY_NO_WAIT (default 1): a story mission sortie that would CREATE a
#: Frontline matching room starts at once instead (topics/060804).
STORY_NO_WAIT = _env_int("FMO_STORY_NO_WAIT", "1") != 0
#: FMO_STORY_LEADER_ONLY (default 1): in a battle group only the leader's
#: story mission is carried out by the battle (Lesson 5). 0 = every member
#: who sortied on the tile with the mission accepted advances (OURS, the older rule).
STORY_LEADER_ONLY = _env_int("FMO_STORY_LEADER_ONLY", "1") != 0
#: FMO_STORY_BLOCK_KIND (default 1): serve the mission block's objective kind
#: (+0x50) and mark the target units, so the client's own opening line names
#: them; 0 = leave both zero.
STORY_BLOCK_KIND = _env_int("FMO_STORY_BLOCK_KIND", "1") != 0
#: KEY: THE TARGET FLAG (static 2026-10-08). The battle-start line for block
#: kind 1 (79:12 "Target unit %s confirmed. Destroy it.") and kind 2 (79:13
#: "Unit %s has been set as the enemy's target. Defend it.") is built at
#: 0x610E6915 -> 0x610E62F0, which walks the unit list and lists every unit
#: whose char+0x1C is 0 (an NPC, client_kind 1) and whose byte unit+0x11A is 1,
#: as "name1 name2" (unit+0x5C, +0x6D). The POP body is copied to unit+4
#: (0x611F1FD0; the brain at body+0x118 is read at unit+0x11C), so the flag is
#: POP body+0x116. WARNING: STATIC. Not yet seen on a screen.
POP_OBJECTIVE_UNIT = 0x116
#: the most units on the field at once (squad.parse_battle_enemies' cap)
MAX_ON_FIELD = 8
#: ally ids: the squad's first id + this + n (solo.py's convention)
ALLY_ID_OFFSET = 0x40
#: OURS: how far behind the pilot's drop point allies stand, and apart
ALLY_BEHIND, ALLY_GAP = 30.0, 14.0
BATTLES_TSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "fmodata", "fmo-story-battles.tsv")
#: unit kind -> (loadout kind, role filter or None, pick)
KINDS = {"wanzer": ("wanzer", ("WAP", "npc60", "zora_ev1"), "level"),
         "sniper": ("wanzer", ("SNPR",), "level"), "jammer": ("wanzer", ("JAMR",), "level"),
         "recon": ("wanzer", ("RECN",), "level"), "comms": ("wanzer", ("COMS",), "level"),
         "tank": ("tank", None, "level"), "heli": ("heli", None, "light"),
         "heli_heavy": ("heli", None, "heavy"), "boss": ("boss", None, "light"),
         "boss_big": ("boss", None, "heavy")}
OBJECTIVES = ("annihilate", "target", "spare", "count", "order", "survive")


def parse_units(spec):
    """'heli_heavy!=X heli*2 | wanzer*3' -> [[unit, ...], ...] (waves); a unit
    is {kind, hp, mark, name}. The grammar of tools/fmodatagen/fmostorybattles.py.
    Raises ValueError."""
    waves = []
    for wave in (spec or "").split("|"):
        units = []
        for tok in wave.split():
            name = ""
            if "=" in tok:
                tok, name = tok.split("=", 1)
            kind, n, hp, mark, i = "", 1, 100, "", 0
            while i < len(tok) and (tok[i].isalnum() or tok[i] == "_"):
                kind += tok[i]
                i += 1
            if kind not in KINDS or len(name) > 16:
                raise ValueError("unit %r: unknown kind or a name over 16" % tok)
            rest = tok[i:]
            while rest:
                c = rest[0]
                if c in "!~":
                    mark, rest = c, rest[1:]
                    continue
                if c in "*@":
                    j = 1
                    while j < len(rest) and rest[j].isdigit():
                        j += 1
                    if j == 1:
                        raise ValueError("unit %r: %s needs a number" % (tok, c))
                    if c == "*":
                        n = int(rest[1:j])
                    else:
                        hp = int(rest[1:j])
                    rest = rest[j:]
                    continue
                raise ValueError("unit %r: unexpected %r" % (tok, c))
            for _ in range(n):
                units.append({"kind": kind, "hp": hp, "mark": mark, "name": name})
        if units:
            waves.append(units)
    return waves


def parse_objective(text):
    """'count:25:4' -> ('count', 25, 4); 'target' -> ('target', None, None)."""
    parts = (text or "annihilate").strip().split(":")
    kind = parts[0]
    if kind not in OBJECTIVES:
        raise ValueError("objective %r" % text)
    if kind == "count":
        return kind, int(parts[1]), max(1, min(MAX_ON_FIELD, int(parts[2])))
    return kind, None, None


#: KEY: THE SPECIAL MISSION'S SORTIE SIZE (2026-10-08). Every mission's form
#: record in the client's catalogue (AI/F00/D16 / D18, our fmo-story-battles
#: and fmo-missions `size` column) prints 出撃人数制限(min/max), e.g. 2/10,
#: 5/5, 3/3. SE (BOOT CAMP Lesson 5, pcd 1462:16): without a strong enough
#: battle group a special mission cannot even sortie. The client carries the
#: refusals in its 0x0139 failure-code table (0x613955F0, read by 0x6116CE10
#: for 10:6's body): -31103 = 3:0 "Sortie was not authorized: insufficient
#: forces. Everyone joining the special mission must set Ready to Sortie." and
#: -31104 = 3:1 "over the transport limit for the special mission sector.
#: Everyone not joining must set Standing By." The count is the leader (or a
#: solo pilot) plus the battle group's live members set Ready: the ones the
#: sortie takes along (battlegroups.auto_remove_unready drops the rest).
#: Which of 3:0 and 3:10 (-31110, "fewer people than the mission requires")
#: SE used for a short group is our reading of the two texts: 3:0 names the
#: special mission. FMO_STORY_SIZE=0 lets any size sortie.
STORY_SIZE = _env_int("FMO_STORY_SIZE", "1") != 0
SIZE_DEFAULT = (1, 10)
CODE_SIZE_SHORT = -31103            # 3:0
CODE_SIZE_OVER = -31104             # 3:1


def parse_size(text):
    """'2/10' -> (2, 10); anything else -> SIZE_DEFAULT (1 to the sector's 10)."""
    try:
        lo, hi = (int(x) for x in str(text or "").strip().split("/"))
    except ValueError:
        return SIZE_DEFAULT
    lo = max(1, min(10, lo))
    hi = min(10, max(lo, hi))
    return lo, hi


def size_verdict(row, count):
    """(code, why) when `count` pilots may not sortie on story battle `row`,
    else None. Pure."""
    if not STORY_SIZE or not row:
        return None
    lo, hi = row.get("size") or SIZE_DEFAULT
    if count < lo:
        return CODE_SIZE_SHORT, (f"'{row.get('title')}' needs {lo}..{hi} pilots and "
                                 f"{count} are Ready (3:0: insufficient forces)")
    if count > hi:
        return CODE_SIZE_OVER, (f"'{row.get('title')}' takes at most {hi} pilots and "
                                f"{count} are Ready (3:1: over the special mission "
                                f"sector's transport limit)")
    return None


def load_battles(path=None):
    """{(nation 1/2, own byte): row} from fmo-story-battles.tsv; {} when absent.
    A row that does not parse is skipped and logged."""
    out = {}
    try:
        f = open(path or BATTLES_TSV, encoding="utf-8")
    except OSError:
        return out
    with f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            nat = {"O.C.U.": 1, "U.S.N.": 2}.get(r.get("faction"))
            try:
                row = {"nation": nat, "byte": int(r["own_byte"]), "title": r["title"],
                       "level": int(r.get("level") or 1),
                       "objective": parse_objective(r.get("objective")),
                       "waves": parse_units(r.get("enemies")),
                       "allies": [u for w in parse_units(r.get("allies")) for u in w],
                       "time_limit": int(r["time_limit"]) if r.get("time_limit") else 0,
                       "timeout": (r.get("timeout") or "loss").strip(),
                       "block_kind": int(r.get("block_kind") or 0),
                       "size": parse_size(r.get("size")),
                       "win": r.get("win", ""), "lose": r.get("lose", "")}
            except (KeyError, ValueError) as e:
                log(f"WARNING: fmo-story-battles.tsv row {r.get('own_byte')!r} skipped: {e}")
                continue
            if nat and row["waves"]:
                out[(nat, row["byte"])] = row
    return out


BATTLES = load_battles()


def battle_for(nation, byte, battles=None):
    """The story battle row for mission byte `byte` of `nation`, or None."""
    return (BATTLES if battles is None else battles).get((nation, byte))


def accepted_mission_on_tile(char, tile, missions=None, tiles=None):
    """The pilot's own-nation story mission ACCEPTED (byte 1 or 2) whose sortie
    tiles hold `tile`: the mission this sortie is for, as the LobbySally's
    tile compare sees it. None when there is none or several. Pure."""
    from . import progress
    missions = progress.MISSIONS if missions is None else missions
    tiles = progress.MISSION_TILES if tiles is None else tiles
    if char is None or tile is None or not fmostore:
        return None
    nat, _src = popnation.character_nation(char)
    fl = fmostore.flags_bytes(char.get("flags")).ljust(256, b"\0")
    hit = [m for m in missions.get(nat, [])
           if m["own"] is not None and fl[m["own"]] in (1, 2)
           and int(tile) in tiles.get(m["own"], ())]
    return hit[0] if len(hit) == 1 else None


def sortie_role(account, map_id, group_of=None, group_sortie=None, leader_of=None):
    """('follower', the group's sortie record) for a 9:10 follower onto its
    group's map, ('leader', gid) for a group leader, ('member', gid) for a
    member sortieing on its own, ('solo', None) without a group. Pure."""
    group_of = groupchannel.GROUP_OF if group_of is None else group_of
    group_sortie = groupchannel.GROUP_SORTIE if group_sortie is None else group_sortie
    leader_of = battlegroups.group_leader if leader_of is None else leader_of
    gid = group_of.get(account) if account else None
    if gid is None:
        return "solo", None
    gs = group_sortie.get(gid)
    if gs and map_id and map_id == gs.get("map") and gs.get("by") != account:
        return "follower", gs
    return ("leader" if leader_of(gid) == account else "member"), gid


def sortie_story_battle(char, tile, role, gs=None, battles=None):
    """(row or None, why) for one sortie: the leader's (or a solo pilot's)
    accepted mission on the sortie tile picks the row; a follower takes the
    one its group's sortie carries; a member sortieing on its own does not
    carry out a story mission (Lesson 5). Pure."""
    if not STORY_BATTLES:
        return None, "FMO_STORY_BATTLES=0"
    if role == "follower":
        row = (gs or {}).get("story")
        return row, ("the group's story battle" if row else "the group's sortie is no story mission")
    m = accepted_mission_on_tile(char, tile)
    if m is None:
        return None, "no accepted story mission on tile %s" % tile
    if role == "member" and STORY_LEADER_ONLY:
        return None, ("'%s' is accepted, but this pilot is not its battle group's "
                      "leader (only the leader carries out a special mission)" % m["title"])
    nat, _src = popnation.character_nation(char)
    row = battle_for(nat, m["own"], battles)
    if row is None:
        return None, "'%s' has no row in fmo-story-battles.tsv" % m["title"]
    return row, "'%s' (byte %d)" % (m["title"], m["own"])


def first_on_field(row):
    """How many enemies the battle opens with. Pure."""
    if not row:
        return None
    kind, _n, field = row["objective"]
    if kind == "count":
        return field
    return max(1, min(MAX_ON_FIELD, len(row["waves"][0])))


def _pick(rows, unit, level, nation, rnd):
    """One loadout row for a story unit: the kind's rows of `nation` or either,
    at the highest level <= `level` (the lowest when none is); 'heavy' takes
    the toughest of those, 'light' the weakest. None when nothing fits."""
    lk, roles, how = KINDS[unit["kind"]]
    cands = [r for r in rows if r["kind"] == lk and r["nation"] in (0, nation)
             and (roles is None or r["role"] in roles)]
    if not cands:
        return None
    below = [r["level"] for r in cands if r["level"] <= level]
    best = max(below) if below else min(r["level"] for r in cands)
    at = [r for r in cands if r["level"] == best]
    if how in ("heavy", "light"):
        key = lambda r: int(r.get("hp") or 0)
        pool = sorted(at, key=key)
        val = key(pool[-1] if how == "heavy" else pool[0])
        at = [r for r in pool if key(r) == val]
    return rnd.choice(at)


def plan_units(row, n_slots, enemy_nation, level, rows=None, rnd=None):
    """The enemy units to queue: [(unit, loadout)] in pop order (wave by wave;
    a count objective repeats the first unit to N). Pure apart from rnd."""
    rows = squad.NPC_LOADOUTS if rows is None else rows
    rnd = rnd or random.Random()
    kind, n, _field = row["objective"]
    units = [u for w in row["waves"] for u in w]
    if kind == "count":
        units = [dict(row["waves"][0][0]) for _ in range(n)]
    return [(u, _pick(rows, u, level, enemy_nation, rnd)) for u in units]


def setup_squad(sq, st, base, rows=None, rnd=None):
    """Make a freshly created squad the story battle `st["story"]` names: the
    opening units dressed from the mission's kinds, names and target flags,
    the allies, and the verdict state in sq["story"]. Idempotent; a squad
    with no story row is left alone. -> sq."""
    row = (st or {}).get("story")
    if not row or sq.get("story") is not None or sq.get("solo") is not None:
        return sq
    rows = squad.NPC_LOADOUTS if rows is None else rows
    rnd = rnd or random.Random()
    level = max(1, int(row["level"]))
    kind, n, field = row["objective"]
    queue = plan_units(row, len(sq["ids"]), sq["nation"], level, rows, rnd)
    first = first_on_field(row) or len(sq["ids"])
    slots = list(sq["pos"]) or [tuple(base or (0.0, 0.0, 0.0, 0.0))]
    uid0 = sq["uid0"] = sq["ids"][0]
    sq["ids"], sq["pos"], sq["loadouts"] = [], [], []
    sq["hp_pct"], sq["names"], sq["targets"] = {}, {}, set()
    story = {"row": row, "queue": queue, "next": 0, "slots": slots, "first": first,
             "wave_sizes": [len(w) for w in row["waves"]], "spared": set(),
             "target_ids": set(), "order": [], "end": None, "level": level,
             "protected": set()}
    sq["story"] = story
    sq["level"] = level
    for _ in range(min(first, len(queue))):
        _queue_next(sq)
    # the allies: the pilot's side, the same AI switch as solo.py's
    sq.setdefault("allies", [])
    sq.setdefault("ally_info", {})
    sq.setdefault("ally_dead", set())
    ally_nation = {1: 2, 2: 1}.get(sq["nation"], 1)
    b = list(base or (0.0, 0.0, 0.0, 0.0)) + [0.0] * 4
    lim = fmoworld.POP_BATTLE_POS_MAX - 1.0
    for j, u in enumerate(row["allies"]):
        uid = uid0 + ALLY_ID_OFFSET + j
        pos = (max(-lim, min(lim, float(b[0]) - ALLY_BEHIND)), float(b[1]),
               max(-lim, min(lim, float(b[2]) + (j - (len(row["allies"]) - 1) / 2.0) * ALLY_GAP)),
               float(b[3]))
        sq["allies"].append(uid)
        sq["ally_info"][uid] = {"role": u["name"] or "Ally", "pos": pos, "hp": u["hp"],
                                "loadout": _pick(rows, u, level, ally_nation, rnd),
                                "protected": u["mark"] == "!"}
        if u["mark"] == "!":
            story["protected"].add(uid)
    return sq


def _queue_next(sq):
    """Append the next queued unit to the squad: its id, slot, loadout, HP,
    name and marks. -> its index, or None when the queue is spent."""
    story = sq["story"]
    if story["next"] >= len(story["queue"]):
        return None
    unit, lo = story["queue"][story["next"]]
    story["next"] += 1
    i = len(sq["ids"])
    uid0 = sq["uid0"]
    sq["ids"].append(uid0 + i)
    sq["pos"].append(story["slots"][i % len(story["slots"])])
    sq["loadouts"].append(lo)
    if unit["hp"] != 100:
        sq["hp_pct"][i] = unit["hp"]
    kind = story["row"]["objective"][0]
    name = unit["name"] or (str(11 + i) if kind == "order" else "")
    if name:
        sq["names"][i] = name[:16]
    if unit["mark"] == "~":
        story["spared"].add(uid0 + i)
    if unit["mark"] == "!":
        story["target_ids"].add(uid0 + i)
        if STORY_BLOCK_KIND:
            sq["targets"].add(i)
    if kind == "order":
        story["order"].append(uid0 + i)
    return i


def _alive_needed(sq):
    """The enemy ids whose death the win still waits for."""
    story = sq["story"]
    dead = set(sq["dead"])
    return [u for u in sq["ids"] if u not in dead and u not in story["spared"]]


def on_death(sq, target, friendly_fire=False):
    """Squad unit or ally `target` DIED: advance the verdict and say what to
    pop next. -> [index of each enemy to pop]. Sets story['end'] = (why, won).
    Pure apart from `sq`."""
    story = sq.get("story")
    if not story or story.get("end"):
        return []
    row = story["row"]
    kind, n, _field = row["objective"]
    if target in story["protected"]:
        story["end"] = (f"'{row['title']}': the unit we were to protect was "
                        f"destroyed ({row['lose']})", False)
        return []
    if target not in sq["ids"]:
        return []
    if target in story["spared"] and not friendly_fire:
        story["end"] = (f"'{row['title']}': a unit that was not to be destroyed was "
                        f"destroyed ({row['lose']})", False)
        return []
    if kind == "order":
        dead_in_order = [u for u in story["order"] if u in sq["dead"] or u == target]
        expected = story["order"][len(dead_in_order) - 1] if dead_in_order else None
        if target != expected:
            story["end"] = (f"'{row['title']}': a unit was destroyed out of call-sign "
                            f"order ({row['lose']})", False)
            return []
    spawns = []
    down = len(set(sq["dead"]) | {target})
    if kind == "count":
        if down >= n:
            story["end"] = (f"'{row['title']}': {down} of {n} destroyed ({row['win']})", True)
            return []
        i = _queue_next(sq)
        return [] if i is None else [i]
    if kind == "target":
        if story["target_ids"] and story["target_ids"] <= (set(sq["dead"]) | {target}):
            story["end"] = (f"'{row['title']}': the target was destroyed ({row['win']})", True)
            return []
    # the next wave arrives when every unit on the field the win waits for is
    # down: the units from the next queued one to the end of its wave
    alive = [u for u in sq["ids"] if u not in sq["dead"] and u != target
             and u not in story["spared"]]
    if alive:
        return []
    if story["next"] < len(story["queue"]):
        wave_end = 0
        for size in story["wave_sizes"]:
            wave_end += size
            if wave_end > story["next"]:
                break
        wave_end = max(wave_end, story["next"] + 1)
        while story["next"] < min(wave_end, len(story["queue"])):
            spawns.append(_queue_next(sq))
        return [i for i in spawns if i is not None]
    if kind in ("annihilate", "spare", "order", "survive"):
        story["end"] = (f"'{row['title']}': every enemy unit destroyed ({row['win']})", True)
    return []


def verdict(st, now):
    """(why, won) once this battle's story mission is decided, else None: a
    death verdict (on_death), or the mission's own time limit (time_limit
    seconds from the battle start): a 'win' timeout is a win when every
    protected ally lives. Pure apart from st's squad."""
    sq = (st or {}).get("squad") or {}
    story = sq.get("story")
    if not story:
        return None
    if story.get("end"):
        return story["end"]
    row = story["row"]
    lim = row.get("time_limit") or 0
    t0 = (st or {}).get("started_at") or (st or {}).get("granted_at")
    if lim > 0 and t0 is not None and now - t0 >= lim:
        alive = not (story["protected"] & set(sq.get("ally_dead") or ()))
        won = row.get("timeout") == "win" and alive
        story["end"] = (f"'{row['title']}': the {lim} s time limit ran out ("
                        + (row["win"] if won else row["lose"]) + ")", won)
        return story["end"]
    return None


def ally_pop(sq, uid, owner, side, unit_type, brain):
    """The cmd-7 POP of story ally `uid`: client_kind 1 on the pilot's side
    (solo.ally_pop's friend switch, body+0x27), its name, its HP, and the
    target flag when it is the unit to protect."""
    info = sq["ally_info"][uid]
    lo = info.get("loadout")
    extra = {battlepop.POP_AI_OWNER: struct.pack("<I", owner),
             battlepop.POP_AI_BRAIN: struct.pack("<I", brain),
             fmoworld.POP_HP_SCALE: bytes([squad.enemy_hp_scale(info.get("hp") or 100)])}
    if info.get("protected") and STORY_BLOCK_KIND:
        extra[POP_OBJECTIVE_UNIT] = b"\x01"
    return fmoworld.record_pop(
        uid, unit_type=unit_type, pos=info["pos"], client_kind=1,
        name1=squad.NPC_NAME_BY_NATION.get({1: 2, 2: 1}.get(sq["nation"], 1), "Ally"),
        name2=info["role"][:16], nation={1: 2, 2: 1}.get(sq["nation"], 1), side=side,
        parts=(lo["parts"] if lo else (sq.get("parts") or None)), extra=extra)


def pop_allies(chan, sq, owner):
    """Queue a POP of every living story ally of `sq` onto `chan`, and the
    mission's opening banner. -> the ids popped."""
    story = sq.get("story")
    if not story:
        return []
    out = []
    side = popnation.battle_side_for(chan.addr[0])[0]
    for uid in sq.get("allies") or ():
        if uid in sq.get("ally_dead", ()) or uid not in sq["ally_info"]:
            continue
        try:
            chan.pending.append(ally_pop(sq, uid, owner, side, battlepop.BATTLE_DUMMY[1],
                                         battlepop.BATTLE_DUMMY_AI))
        except ValueError as e:
            log(f"[udp {chan.addr[0]}] WARNING: STORY ALLY POP {uid:#x} REFUSED BY OUR "
                f"OWN GUARD: {e}")
            continue
        out.append(uid)
    row = story["row"]
    # the generic FMO_OBJECTIVE opening line would misstate this mission
    referee.battle_state(referee.chan_bkey(chan))["objective_banner"] = True
    referee.hud_banner(chan, f"MISSION: {row['title']}"[:60])
    referee.hud_banner(chan, f"WIN: {row['win']}"[:60])
    return out


def unit_died(chan, addr, sq, target):
    """on_death, then pop the next units for every pilot holding the squad
    and tell the pilot. -> the indexes popped."""
    story = sq.get("story")
    if not story:
        return []
    ff = any(t == target for t, _s in sq.get("friendly_fire") or ())
    if target in (sq.get("allies") or ()):
        sq.setdefault("ally_dead", set()).add(target)
    spawns = on_death(sq, target, friendly_fire=ff)
    for i in spawns:
        for v, owner in solo._viewers(chan, sq):
            try:
                v.pending.append(squad.enemy_pop(
                    sq, i, sq["ids"][i], sq["pos"][i], owner,
                    popnation.enemy_side_for(v.addr[0])[0],
                    battlepop.BATTLE_DUMMY[1], battlepop.BATTLE_DUMMY_AI))
                referee.battle_state(referee.chan_bkey(v)).setdefault(
                    "enemies", set()).add(sq["ids"][i])
            except ValueError as e:
                log(f"[udp {addr[0]}:{addr[1]}] WARNING: STORY POP REFUSED BY OUR "
                    f"OWN GUARD: {e}")
    row = story["row"]
    if spawns:
        referee.hud_banner(chan, "Enemy reinforcements" if row["objective"][0] != "count"
                           else f"Destroyed {len(set(sq['dead']))}/{row['objective'][1]}")
    log(f"[udp {addr[0]}:{addr[1]}]   STORY BATTLE '{row['title']}': unit {target:#x} "
        f"down; {len(spawns)} more popped"
        + (f"; -> {'WIN' if story['end'][1] else 'LOSS'}: {story['end'][0]}"
           if story.get("end") else ""))
    return spawns


# Called at run time only; imported last so that import cycles resolve.
from .deps import fmostore  # noqa: E402
from . import (  # noqa: E402
    battlegroups, battlepop, groupchannel, popnation, referee, solo, squad,
)
