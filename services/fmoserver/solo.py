"""The solo area (SE's Festa 2006 solo-area battle): the pilot and one NPC ally at a time
against two enemies at a time; ten kills win, the pilot's death or three allies lost lose."""
import os
import random
import struct
from .deps import fmoworld
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# KEY: SE'S RULES, topics/060308 (PlayOnline Festa 2006, Necca Akihabara,
# 2006-03-25, "フロントミッション オンライン ソロエリアバトル"; archived text:
# work/webarchive/text/www.playonline.com/fmo/topics/060308/). Quoted:
#   "使用するマップはO.C.U.統制区エリア10 セクター14です。"
#   "同時に出撃するのは自分の操る機体と、NPCの操る機体の計2機です。"
#   "NPCが撃破されると、次のNPC機体が出現します。"
#   "敵のNPCも常に2機出撃する仕組みになっており、1機撃破すると、新たな機体が出現します。"
#   勝利条件 "敵のNPCを10機撃破すること"
#   敗北条件 "自分の機体が撃破されること" / "味方のNPCが3機撃破されること"
#   "味方のNPCは、アサルト→メカニック→ミサイラーの順番で出撃します。"
#   "プレイヤーが操縦する機体には、回復アイテムが2つ用意されています。"
#   "ヘリを優先して破壊しましょう。"
#   "最後の敵はそれまでの敵に比べて、強くなっています。"
# The page gives no NPC level, no loadouts, no HP figure and no pay. Those
# are OURS below, and each says so.
#
# NOT BUILT: (1) the two repair items. Consumables reach the battle only
# through the pilot's setup (0x0166 at login -> lobby+0x3DF3, kind-0x13 item
# records at setup+0x28, inventory.py); the battle POP carries eleven PART
# records (body+0x8C, 0x611F70A0) and nothing for items, and no per-battle
# message that grants an item is known. (2) Orders to the ally ("～へ行け",
# "停止しろ"): client UI; the leader field unit+0xE81 is written by the
# script native 0x610CE3A0, not by the POP.
# --------------------------------------------------------------------------- #
#: FMO_SOLO: 1 (default) = a sortie to the solo area runs these rules.
SOLO = _env_int("FMO_SOLO", "1") != 0
#: FMO_SOLO_AREA="selector:tile". Default O.C.U. 統制区１０ セクター１４ =
#: selector 109, tile 74149 (work/fmo/fmo-are-sectors.tsv row 109/9, battle
#: map 36; fmosectors.SECTORS[109][74149] == (9, 36)).
SOLO_AREA_SPEC = os.environ.get("FMO_SOLO_AREA", "").strip() or "109:74149"
#: FMO_SOLO_KILLS: SE's 勝利条件, 10 enemies destroyed.
#: At most 0x3F, so enemy ids (+0..) never reach the ally ids (+0x40..).
SOLO_KILLS = max(1, min(0x3F, _env_int("FMO_SOLO_KILLS", "10")))
#: FMO_SOLO_ALLY_LOSSES: SE's 敗北条件, 3 allies destroyed.
SOLO_ALLY_LOSSES = max(1, _env_int("FMO_SOLO_ALLY_LOSSES", "3"))
#: FMO_SOLO_LAST_HP_PCT: the last enemy's part HP percent (body+0x125 x 10,
#: 0x611F7124). OURS: SE says only "強くなっています".
SOLO_LAST_HP_PCT = _env_int("FMO_SOLO_LAST_HP_PCT", "200")
#: SE: "敵のNPCも常に2機出撃する".
ON_FIELD = 2
#: SE: "アサルト→メカニック→ミサイラーの順番". With FMO_SOLO_ALLY_LOSSES > 3
#: the order repeats (ours).
ALLY_ROLES = ("Assault", "Mechanic", "Missiler")
#: ally ids: the squad's first id + this + n, clear of the enemies' +0..+N.
ALLY_ID_OFFSET = 0x40
#: OURS: the last enemy's loadout is picked this many NPC levels above the
#: battle's (and never a vehicle), on top of SOLO_LAST_HP_PCT.
LAST_LEVEL_STEP = 10
#: OURS: an ally pops this many world units BEHIND the drop point (-x; the
#: enemy line stands at +x, squad_positions), out of the enemies' first volley.
ALLY_BEHIND = 30.0
#: weapon kinds, from the NPC loadout generator (fmonpcloadouts.py
#: ROLE_WEAPON): 0x12 Machinegun, 0x22 Shotgun, 0x32 Rifle, 0x72 Missile;
#: item 4 is the right hand, item 10 the backpack (kind 0x41).
KIND_MISSILE = 0x72
ASSAULT_WEAPONS = (0x12, 0x22)
ITEM_WEAPON, ITEM_BACKPACK, KIND_BACKPACK = 4, 10, 0x41
PART_LEVELS_TSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata",
    "fmo-part-levels.tsv")


def parse_solo_area(spec):
    """'109:74149' -> (109, 74149). ValueError on anything else."""
    sel, _, tile = (spec or "").partition(":")
    try:
        return int(sel, 0), int(tile, 0)
    except ValueError:
        raise ValueError(f"FMO_SOLO_AREA={spec!r}: want <selector>:<tile>")


try:
    SOLO_AREA = parse_solo_area(SOLO_AREA_SPEC)
    _SOLO_AREA_ERR = ""
except ValueError as _e:
    SOLO_AREA, _SOLO_AREA_ERR = (109, 74149), str(_e)


def load_repair_backpacks(path=None):
    """{level: (id, name)} of the NPC repair backpacks (kind 0x41 names
    bp_repairx*: 181..188 at levels 1..41) from fmo-part-levels.tsv. The
    Mechanic wears one. {} when the file is absent."""
    out = {}
    try:
        with open(path or PART_LEVELS_TSV, encoding="utf-8") as f:
            f.readline()
            for line in f:
                c = line.rstrip("\r\n").split("\t")
                if len(c) >= 4 and c[0].lower() == "0x41" and c[3].startswith("bp_repairx"):
                    out.setdefault(int(c[2]), (int(c[1]), c[3]))
    except (OSError, ValueError):
        return {}
    return out


REPAIR_BACKPACKS = load_repair_backpacks()


def solo_for(selector, tile):
    """A fresh solo state for a sortie to (selector, tile), or None when it
    is not the solo area (or FMO_SOLO=0)."""
    if not SOLO or selector is None or tile is None:
        return None
    try:
        if (int(selector), int(tile)) != SOLO_AREA:
            return None
    except (TypeError, ValueError):
        return None
    return {"area": SOLO_AREA, "kills": SOLO_KILLS, "ally_losses": SOLO_ALLY_LOSSES,
            "spawned": 0, "allies_lost": 0, "ally_next": 0, "counted": set(),
            "end": None}


def solo_on_field(solo):
    """How many enemies a solo squad opens with (None = not solo)."""
    return min(ON_FIELD, solo["kills"]) if solo else None


def _best_level(cands, level):
    below = [r["level"] for r in cands if r["level"] <= level]
    return max(below) if below else min(r["level"] for r in cands)


def ally_loadout(rows, role, level, nation, rnd):
    """One loadout row for an ally of `role`, or None. SE names the roles
    and nothing else; the picks are OURS, by weapon: Missiler = a set with a
    Missile (kind 0x72, the COMS sets); Assault = no missile and a Machinegun
    or Shotgun in hand; Mechanic = an Assault set with its backpack swapped
    for an NPC repair backpack (bp_repairx*) at or below the level."""
    wz = [r for r in rows if r["kind"] == "wanzer" and r["nation"] in (0, nation)]
    if role == "Missiler":
        cands = [r for r in wz if any(k == KIND_MISSILE for _i, k, _d in r["parts"])]
    else:
        cands = [r for r in wz
                 if not any(k == KIND_MISSILE for _i, k, _d in r["parts"])
                 and any(i == ITEM_WEAPON and k in ASSAULT_WEAPONS for i, k, _d in r["parts"])]
    if not cands:
        return None
    best = _best_level(cands, level)
    row = dict(rnd.choice([r for r in cands if r["level"] == best]), solo_role=role)
    if role == "Mechanic" and REPAIR_BACKPACKS:
        lv = max([k for k in REPAIR_BACKPACKS if k <= level] or [min(REPAIR_BACKPACKS)])
        bp, bpname = REPAIR_BACKPACKS[lv]
        row["parts"] = ([p for p in row["parts"] if p[0] != ITEM_BACKPACK]
                        + [(ITEM_BACKPACK, KIND_BACKPACK, bp)])
        row["name"] = f"{row['name']}+{bpname}"
    return row


def _level(sq):
    return sq.get("level") if sq.get("level") is not None else squad.ENEMY_LEVEL


def _ally_nation(sq):
    return {1: 2, 2: 1}.get(sq.get("nation"), 1)


def solo_squad_setup(sq, solo, base, rows=None, rnd=None):
    """Make a freshly created squad a solo one: its opening enemies count as
    spawned, the last one is marked when the opening line already holds it,
    and the first ally (Assault) is made. Idempotent."""
    if sq.get("solo") is not None:
        return sq
    sq["solo"] = solo
    solo["slots"] = list(sq["pos"])
    solo["spawned"] = len(sq["ids"])
    solo["base"] = tuple(base) if base else (0.0, 0.0, 0.0, 0.0)
    if solo["spawned"] >= solo["kills"]:
        _make_last(sq, solo["spawned"] - 1, rows, rnd)
    sq.setdefault("allies", [])
    sq.setdefault("ally_info", {})
    sq.setdefault("ally_dead", set())
    spawn_ally(sq, rows, rnd)
    return sq


def _make_last(sq, i, rows=None, rnd=None):
    """Squad enemy `i` is the last one: more HP, a loadout LAST_LEVEL_STEP up."""
    sq.setdefault("hp_pct", {})[i] = SOLO_LAST_HP_PCT
    if sq.get("loadouts") is not None:
        rows = squad.NPC_LOADOUTS if rows is None else rows
        lo = squad.pick_loadout(rows, _level(sq) + LAST_LEVEL_STEP, sq["nation"],
                                rnd or random.Random(), vehicle_pct=0)
        while len(sq["loadouts"]) <= i:
            sq["loadouts"].append(None)
        sq["loadouts"][i] = lo or sq["loadouts"][i]
    sq["solo"]["last"] = i


def spawn_enemy(sq, rows=None, rnd=None):
    """Add the next enemy to a solo squad: returns its index, or None once
    SE's ten have all been sent."""
    solo = sq["solo"]
    if solo["spawned"] >= solo["kills"]:
        return None
    i = len(sq["ids"])
    sq["ids"].append(sq["ids"][0] + i)
    sq["pos"].append(solo["slots"][i % len(solo["slots"])])
    if sq.get("loadouts") is not None:
        rows = squad.NPC_LOADOUTS if rows is None else rows
        sq["loadouts"].append(squad.pick_loadout(rows, _level(sq), sq["nation"],
                                                 rnd or random.Random()))
    solo["spawned"] += 1
    if solo["spawned"] == solo["kills"]:
        _make_last(sq, i, rows, rnd)
    return i


def ally_position(base):
    """ALLY_BEHIND units back along -x from the drop point, inside the battle
    box record_pop accepts for a battle unit (x/y/z to +-POP_BATTLE_POS_MAX; the
    drop point may stand anywhere on the map since the spawn table reached
    past +-327.67, 2026-10-07)."""
    p = list(base) + [0.0] * (4 - len(base))
    _lim = fmoworld.POP_BATTLE_POS_MAX - 1.0
    p[0] = max(-_lim, min(_lim, float(p[0]) - ALLY_BEHIND))
    return tuple(float(v) for v in p[:4])


def spawn_ally(sq, rows=None, rnd=None):
    """Add the next ally (SE's order) to a solo squad: returns its id."""
    solo = sq["solo"]
    j = solo["ally_next"]
    solo["ally_next"] += 1
    role = ALLY_ROLES[j % len(ALLY_ROLES)]
    uid = sq["ids"][0] + ALLY_ID_OFFSET + j
    rows = squad.NPC_LOADOUTS if rows is None else rows
    lo = ally_loadout(rows, role, _level(sq), _ally_nation(sq), rnd or random.Random())
    sq["allies"].append(uid)
    sq["ally_info"][uid] = {"role": role, "loadout": lo,
                            "pos": ally_position(solo["base"])}
    return uid


def on_death(sq, target, rows=None, rnd=None):
    """The squad owner reported unit `target` DIED. Returns what to pop:
    [("enemy", index) | ("ally", id)], and sets solo['end'] = (why, won) on
    SE's win or loss. Once per unit. Pure apart from `sq`."""
    solo = sq.get("solo")
    if not solo or solo.get("end") or target in solo["counted"]:
        return []
    if target in sq["ids"]:
        solo["counted"].add(target)
        down = len(set(sq["ids"]) & (set(sq["dead"]) | {target}))
        if down >= solo["kills"]:
            solo["end"] = (f"the solo area was won: {down} enemies destroyed "
                           f"(SE's 勝利条件: 敵のNPCを10機撃破すること)", True)
            return []
        i = spawn_enemy(sq, rows, rnd)
        return [] if i is None else [("enemy", i)]
    if target in sq.get("allies", ()):
        solo["counted"].add(target)
        sq["ally_dead"].add(target)
        solo["allies_lost"] += 1
        if solo["allies_lost"] >= solo["ally_losses"]:
            solo["end"] = (f"the solo area was lost: {solo['allies_lost']} allies "
                           f"destroyed (SE's 敗北条件: 味方のNPCが3機撃破されること)", False)
            return []
        return [("ally", spawn_ally(sq, rows, rnd))]
    return []


def solo_verdict(st):
    """(why, won) once the solo squad of this battle state has ended, else None."""
    sq = (st or {}).get("squad") or {}
    return (sq.get("solo") or {}).get("end")


def ally_pop(sq, uid, owner, side, unit_type, brain):
    """The cmd-7 POP of ally `uid`: the SAME AI switch as an enemy
    (client_kind 1, owner body+0x2C, brain body+0x118)
    but on the PILOT'S side. KEY: WHAT MAKES IT FRIENDLY is body+0x27 ->
    unit+0x80: the AI controller's target scan skips any unit whose +0x80
    equals its own (0x610A4C83 `mov al,[edi+0x80]` / 0x610A4C89 `cmp al,
    [ecx+0x80]` / 0x610A4C8F `je` skip; the same compare at 0x610A6329,
    0x610A7045, 0x610AA3A7 ...), so it hunts the enemy side and leaves the
    pilot alone. Nation (+0x7C) is the pilot's too."""
    info = sq["ally_info"][uid]
    lo = info["loadout"]
    return fmoworld.record_pop(
        uid, unit_type=unit_type, pos=info["pos"], client_kind=1,
        name1=info["role"], name2="Ally", nation=_ally_nation(sq), side=side,
        parts=(lo["parts"] if lo else (sq.get("parts") or None)),
        extra={battlepop.POP_AI_OWNER: struct.pack("<I", owner),
               battlepop.POP_AI_BRAIN: struct.pack("<I", brain),
               fmoworld.POP_HP_SCALE: bytes([squad.enemy_hp_scale(100)])})


def pop_allies(chan, sq, owner):
    """Queue a POP of every living ally of `sq` onto `chan`. Returns the ids."""
    out = []
    side = popnation.battle_side_for(chan.addr[0])[0]
    for uid in sq.get("allies") or ():
        if uid in sq.get("ally_dead", ()):
            continue
        try:
            chan.pending.append(ally_pop(sq, uid, owner, side, battlepop.BATTLE_DUMMY[1],
                                         battlepop.BATTLE_DUMMY_AI))
        except ValueError as e:
            log(f"[udp {chan.addr[0]}] WARNING: SOLO ALLY POP {uid:#x} REFUSED BY OUR "
                f"OWN GUARD: {e}")
            continue
        out.append(uid)
    return out


def _viewers(chan, sq):
    """(viewer, owner uid there) for the owner `chan` and every battle pilot
    in its room holding the same squad."""
    out = [(chan, squad.squad_owner_uid(chan, True, None))]
    for o in rooms.room_mates(chan):
        if rooms._is_battle_chan(o) and (referee.BATTLE_STATE.get(
                referee.chan_bkey(o)) or {}).get("squad") is sq:
            out.append((o, squad.squad_owner_uid(o, False, chan)))
    return out


def unit_died(chan, addr, sq, target, rows=None, rnd=None):
    """on_death, then pop what it spawned for every pilot holding the squad
    and tell the pilot on the HUD. Returns on_death's list."""
    spawns = on_death(sq, target, rows, rnd)
    solo = sq.get("solo")
    if not solo:
        return spawns
    for what, x in spawns:
        for v, owner in _viewers(chan, sq):
            try:
                if what == "enemy":
                    v.pending.append(squad.enemy_pop(
                        sq, x, sq["ids"][x], sq["pos"][x], owner,
                        popnation.enemy_side_for(v.addr[0])[0],
                        battlepop.BATTLE_DUMMY[1], battlepop.BATTLE_DUMMY_AI))
                    referee.battle_state(referee.chan_bkey(v)).setdefault(
                        "enemies", set()).add(sq["ids"][x])
                else:
                    v.pending.append(ally_pop(
                        sq, x, owner, popnation.battle_side_for(v.addr[0])[0],
                        battlepop.BATTLE_DUMMY[1], battlepop.BATTLE_DUMMY_AI))
            except ValueError as e:
                log(f"[udp {addr[0]}:{addr[1]}] WARNING: SOLO POP REFUSED BY OUR "
                    f"OWN GUARD: {e}")
    down = len(set(sq["ids"]) & set(sq["dead"]))
    if target in sq["ids"]:
        referee.hud_banner(chan, f"Enemies destroyed {down}/{solo['kills']}")
    else:
        referee.hud_banner(chan, f"Ally lost {solo['allies_lost']}/{solo['ally_losses']}")
    for what, x in spawns:
        if what == "enemy":
            lo = (sq.get("loadouts") or [None] * (x + 1))[x]
            last = solo.get("last") == x
            if last:
                referee.hud_banner(chan, "The last enemy is coming - it is stronger")
            log(f"[udp {addr[0]}:{addr[1]}]   SOLO: enemy {sq['ids'][x]:#x} "
                f"({x + 1}/{solo['kills']}) arrives, "
                f"{lo['name'] if lo else 'pilot parts'}"
                + (f", LAST: HP {SOLO_LAST_HP_PCT}%" if last else ""))
        else:
            info = sq["ally_info"][x]
            referee.hud_banner(chan, f"{info['role']} ally arriving")
            log(f"[udp {addr[0]}:{addr[1]}]   SOLO: ally {x:#x} ({info['role']}, "
                f"{info['loadout']['name'] if info['loadout'] else 'pilot parts'}) arrives")
    if solo.get("end"):
        log(f"[udp {addr[0]}:{addr[1]}]   SOLO: {solo['end'][0]} -> the battle "
            f"ends as a {'WIN' if solo['end'][1] else 'LOSS'} on the next keepalive")
    return spawns


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    battlepop, popnation, referee, rooms, squad,
)
