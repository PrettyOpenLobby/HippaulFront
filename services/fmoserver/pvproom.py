"""Frontline matching battles: WAITING FOR OPPONENTS, the start, the room judge and the war settle."""
import os
import struct
import threading
import time
from .deps import fmowar, fmoworld
from .knobs import _env_int
from .wirelog import hexdump, log


# --------------------------------------------------------------------------- #
# THE MATCHING SORTIE (static 2026-10-07, FrontMissionOnline.dll.unpacked)
# --------------------------------------------------------------------------- #
# SE (topics/060804): on a PvP map the battle map that a sortie CREATES waits
# up to 20 minutes for an enemy battle group ("WAITING FOR OPPONENTS", units
# frozen); it starts when an enemy group arrives, when a member picks "Start
# Battle", or when the wait runs out (then the NPCs fight). Joining stays open
# for 5 minutes after the start. Read in the client:
#
#   block+0x7C (lobby+0x5CFA) bit 0x200 = WAITING, bit 0x400 = STARTED.
#   0x61004300 "is the battle running": bit 0x200 clear -> yes; set -> no while
#     0x61001EE0 (a countdown of block+0x7D8 seconds from block+0x48, only when
#     both bits are set) is above 0, then bit 0x400. We serve +0x7D8 = 0, so a
#     started battle runs at once. The overlay 0x61237AE3 and the battle menu
#     (0x6115D71F: only the 0x613953E0 table, "Start Battle", while it is not
#     running) key off the same call.
#   cmd 138 (arm 0x611EFB11): +0x00 -> block+0x48, +0x04 reason -> the banner
#     (1 = 79:30 enemy forces arrived, 2 = 79:29 Start Battle was chosen, 3 =
#     79:31 the standby time ended), +0x08 -> 0x61001F40, which sets bit 0x400.
#     It does NOT clear 0x200; 0x61004300 then runs the battle.
#   cmd 148 (arm 0x611EFF04), the late joiner's resync, 0xB4 bytes:
#     +0x00 bit 0 -> block+0x7C bit 0x200, bit 1 -> bit 0x400
#     +0x04 -> block+0x48 (the start time)
#     +0x08 + 4*i, for i < RectDataCnt (block+0x30): rect i bytes +0x00/+0x01
#           copied, bits 0..1 of +0x02 copied (block+0x418, stride 0x1C)
#     +0x88 12 bytes -> block+0x798
#     +0x94 four {u32, u32} pairs -> 0x61002160, which CLEARS the four-slot
#           holder table at globals+0x218 and refills it (the container and
#           beacon holders of cmd 140/141); zeros = nobody holds anything
#   "Start Battle" (menu item 0:130, command 0x1055 in table 0x613953E0) ->
#     jump table 0x61160310 index 9 -> 0x611601C6 -> 0x611D36B0, which sends
#     BM cmd 137 (0x89) with 64 zero bytes, and only while 0x61004300 says the
#     battle is NOT running. That is its only caller, so cmd 137 is the client's
#     Start Battle request, not a second Emergency Escape.
#   block+0x50 (lobby+0x5CCE, u32) = the objective kind; 5 = Team Deathmatch
#     (79:25). The win radio line is 79:17 when it is nonzero.
#
# WARNING: what the client's AI does while frozen is NOT read. The server
# keeps the NPC squad out of a waiting room instead (squad_allowed), so the
# question does not come up.

#: FMO_PVP_MATCHING (P1, default 1): a sortie onto a PvP selector's battle map
#: is served WAITING (block+0x7C 0x200) and the room starts on the first
#: hostile pilot, Start Battle (cmd 137) or the wait running out. 0 = the old
#: immediate start (FMO_BATTLE_START) everywhere.
MATCHING = _env_int("FMO_PVP_MATCHING", "1") != 0
#: FMO_PVP_SELECTORS: the selectors (MapKinds) whose battle maps match. The
#: Frontline is 505/509/513; SE also matched on "some" 2xx/4xx Occupied areas,
#: which ones is not on any page we hold, so they are opt-in here.
PVP_SELECTORS_SPEC = os.environ.get("FMO_PVP_SELECTORS", "").strip() or "505,509,513"
#: FMO_PVP_WAIT: seconds a waiting room waits for an enemy (SE: 20 minutes).
PVP_WAIT = _env_int("FMO_PVP_WAIT", "1200")
#: FMO_PVP_NPC_VS_PILOTS: 0 (default) = a room started by an enemy pilot gets
#: no NPC squad (SE: the NPCs fight when nobody came); 1 = it gets one anyway.
NPC_VS_PILOTS = _env_int("FMO_PVP_NPC_VS_PILOTS", "0") != 0
#: FMO_PVP_JUDGE (P2, default 1): the room judges the battle for every pilot
#: in it (judge_counts) and block+0x50 is served FMO_PVP_OBJECTIVE_KIND.
JUDGE = _env_int("FMO_PVP_JUDGE", "1") != 0
OBJECTIVE_KIND = _env_int("FMO_PVP_OBJECTIVE_KIND", "5")
#: FMO_PVP_DEATH_WAIT: a destroyed or ejected pilot waits this many seconds for
#: the room's verdict, then ends with a loss (0 = wait for the verdict however
#: long). OURS: the client offers no escape once destroyed (live 2026-09-27).
DEATH_WAIT = _env_int("FMO_PVP_DEATH_WAIT", "300")
#: FMO_PVP_WAR (P3, default 1): the room settles the war ONCE with its verdict
#: for every side that had pilots in it; the per-pilot war_settle stands down.
WAR = _env_int("FMO_PVP_WAR", "1") != 0

MB_MATCH_FLAGS = 0x7C                  # u32 -> lobby+0x5CFA
FLAG_WAITING = 0x200
FLAG_STARTED = 0x400
MB_OBJECTIVE_KIND = 0x50               # u32 -> lobby+0x5CCE
CMD_BM_BATTLE_START = 138
CMD_BM_RESYNC = 148
RESYNC_LEN = 0xB4
CLI_START_BATTLE = 0x89                # 137, 0x611D36B0
START_REASONS = {1: "an enemy pilot entered the battlefield (79:30)",
                 2: "Start Battle was chosen (79:29)",
                 3: "the standby time ended (79:31)"}
#: Battle commands a client is known to send (the prod log's census, 2026-10-07,
#: plus the named ones); anything else on a battle channel is logged whole.
KNOWN_BATTLE_CMDS = frozenset({1, 3, 14, 15, 16, 21, 23, 24, 29, 30, 42, 43, 44, 100,
                               102, 111, 112, 115, 121, 122, 128, 129, 135, 136,
                               CLI_START_BATTLE, 139, 240, 300})


def parse_selectors(spec):
    """'505,509,513' -> {505, 509, 513}. Pure."""
    out = set()
    for piece in (spec or "").split(","):
        piece = piece.strip()
        if piece:
            out.add(int(piece, 0))
    return out


try:
    PVP_SELECTORS = parse_selectors(PVP_SELECTORS_SPEC)
except ValueError:
    PVP_SELECTORS = {505, 509, 513}
    log(f"WARNING: FMO_PVP_SELECTORS={PVP_SELECTORS_SPEC!r} is not a list of "
        f"selectors -- using 505,509,513")

_LOCK = threading.RLock()
#: room id -> room; OPEN (zone, mapno) -> the id of the room a sortie joins;
#: PILOT_ROOM battle key -> room id.
ROOMS = {}
OPEN = {}
PILOT_ROOM = {}
_NEXT = [1]


def active(zone):
    """Does a sortie in selector `zone` go into a judged/matching room?"""
    try:
        z = int(zone)
    except (TypeError, ValueError):
        return False
    return z in PVP_SELECTORS and (MATCHING or JUDGE or WAR)


def _open_room(zone, mapno):
    rid = OPEN.get((int(zone), int(mapno)))
    room = ROOMS.get(rid) if rid is not None else None
    if room is None or room.get("verdict") is not None:
        return None
    return room


def peek(zone, mapno, now=None):
    """(role, room) a sortie onto `mapno` in selector `zone` would take now:
    'create' (room None), 'wait' (the room is waiting), 'late' (it started),
    or (None, None) outside the matching selectors. Changes nothing."""
    if mapno is None or not active(zone):
        return None, None
    with _LOCK:
        room = _open_room(zone, mapno)
    if room is None:
        return "create", None
    return ("wait" if room["started"] is None else "late"), room


def block_knobs(role, room=None):
    """The mission_fields() keywords a sortie with `role` is served: the
    block+0x7C bits and the objective kind. {} outside a room. Pure."""
    if role is None:
        return {}
    out = {}
    if MATCHING:
        out["match_flags"] = (FLAG_WAITING | FLAG_STARTED) if role == "late" else FLAG_WAITING
    if JUDGE and OBJECTIVE_KIND:
        out["objective_kind"] = OBJECTIVE_KIND
    return out


def start_stamp(role, room=None):
    """block+0x48 for a late joiner: the room's own start, so its HUD clock and
    Battle Review agree with everyone else's. None = the sortie's own stamp."""
    if role == "late" and room is not None and room.get("started"):
        return int(room["started"])
    return None


def join_verdict(zone, mapno, nation, gid, now=None, cap=None, window=None):
    """The join-in rule for a matching room: (code, why, pct), or None when the
    sortie is not into one (sortie.join_verdict decides then). While the room
    waits anyone may join (10 a side); after the start the 5-minute window
    runs from the START, not from the creation (SE 060804)."""
    from . import sortie, warmap
    if not MATCHING or mapno is None or not active(zone):
        return None
    now = time.time() if now is None else now
    cap = warmap.S15F_SIDE_CAP if cap is None else cap
    window = sortie.JOIN_WINDOW if window is None else window
    with _LOCK:
        room = _open_room(zone, mapno)
        if room is None:
            return None, "creates the battle map (WAITING FOR OPPONENTS)", None
        mine = [m for m in room["members"].values()
                if m["side"] == nation and not m.get("out")]
        creator = nation == room["creator_side"]
        if cap > 0 and len(mine) >= cap:
            return sortie.JOIN_CODE_SIDE_FULL, (f"nation {nation} already has "
                                                f"{len(mine)} of {cap} in this room"), None
        if gid and any(m.get("gid") == gid for m in room["members"].values()):
            return None, "joins its own battle group in the room", None
        if room["started"] is None:
            return None, ("joins the waiting room" + ("" if creator else
                          " as the opponent: the battle starts when it pops in")), \
                (None if creator else 100)
        elapsed = now - room["started"]
        if window <= 0 or elapsed <= window:
            return None, (f"joins {int(elapsed)} s after the start"), \
                (None if creator else sortie.join_pct(elapsed, window))
        return sortie.JOIN_CODE_LATE, (f"{int(elapsed)} s since the battle started, "
                                       f"the join-in window is {window} s"), None


def join(bkey, account, zone, mapno, tile, nation, now=None, gid=None):
    """Put the pilot `bkey` into the room for (zone, mapno), creating it.
    Returns (role, room), or (None, None) outside the matching selectors."""
    if mapno is None or not active(zone):
        return None, None
    now = time.time() if now is None else now
    with _LOCK:
        _leave(bkey)
        room = _open_room(zone, mapno)
        role = "create"
        if room is None:
            rid = _NEXT[0]
            _NEXT[0] += 1
            room = {"id": rid, "zone": int(zone), "mapno": int(mapno), "tile": tile,
                    "created": now, "started": None if MATCHING else int(now),
                    "reason": None if MATCHING else 0, "npc": not MATCHING,
                    "creator_side": nation, "members": {}, "verdict": None,
                    "fielded": set(), "war_done": False, "pvp": False}
            ROOMS[rid] = room
            OPEN[(int(zone), int(mapno))] = rid
        else:
            role = "wait" if room["started"] is None else "late"
        room["members"][bkey] = {"account": account, "side": nation, "role": role,
                                 "joined": now, "gid": gid, "out": None,
                                 "out_at": None, "start_sent": False,
                                 "popped": None, "delivered": False}
        PILOT_ROOM[bkey] = room["id"]
        if len({m["side"] for m in room["members"].values()}) >= 2:
            room["pvp"] = True
    log(f"PVP ROOM {room['id']}: {account or bkey} (nation {nation}) {role}s the "
        f"room on selector {zone} map {mapno} (tile {tile}); "
        + ("WAITING FOR OPPONENTS, block+0x7C = 0x200" if role != "late" and MATCHING
           else f"started {int(now - room['started'])} s ago: block+0x7C = 0x600, "
                f"cmd 148 resync after the self-POP" if role == "late"
           else "FMO_PVP_MATCHING=0: no waiting")
        + f"; {len(room['members'])} pilot(s) in it")
    return role, room


def _leave(bkey):
    rid = PILOT_ROOM.pop(bkey, None)
    room = ROOMS.get(rid)
    if room is not None and bkey in room["members"]:
        m = room["members"][bkey]
        if not m["delivered"]:
            m["out"] = m["out"] or "sortied again"
            m["out_at"] = m["out_at"] or time.time()
            m["delivered"] = True
        _gc(room)


def _gc(room):
    """Forget a room once it has a verdict and every member has had it."""
    if room["verdict"] is not None and all(m["delivered"] for m in room["members"].values()):
        ROOMS.pop(room["id"], None)
        for k, rid in list(PILOT_ROOM.items()):
            if rid == room["id"]:
                PILOT_ROOM.pop(k, None)


def room_of(bkey):
    with _LOCK:
        rid = PILOT_ROOM.get(bkey)
        return ROOMS.get(rid) if rid is not None else None


def _chan_room(chan):
    from . import referee
    room = room_of(referee.chan_bkey(chan))
    if room is None:
        room = room_of(chan.addr[0])
    return room


def _chan_member(chan, room):
    from . import referee
    for k in (referee.chan_bkey(chan), chan.addr[0]):
        if k in room["members"]:
            return k, room["members"][k]
    acct = getattr(chan, "account", None)
    for k, m in room["members"].items():
        if acct and m.get("account") == acct:
            return k, m
    return None, None


def in_matching_room(chan):
    """True when this battle channel's start belongs to a matching room (the
    generic FMO_BATTLE_START stands down)."""
    return MATCHING and _chan_room(chan) is not None


def squad_allowed(chan):
    """May the NPC squad pop on this battle channel now? Never while the room
    waits; after the start only when the room fights NPCs (reason 2/3, or
    FMO_PVP_NPC_VS_PILOTS)."""
    if not MATCHING:
        return True
    room = _chan_room(chan)
    if room is None:
        return True
    return room["started"] is not None and room["npc"]


def start(room, reason, now=None, why=""):
    """Start a waiting room (once). Every member's channel is sent cmd 138 on
    its next datagram (tick_chan); a late joiner gets cmd 148 instead."""
    now = time.time() if now is None else now
    with _LOCK:
        if room["started"] is not None or room["verdict"] is not None:
            return False
        room["started"] = int(now)
        room["reason"] = reason
        room["npc"] = reason in (2, 3) or NPC_VS_PILOTS
    from . import referee
    for k in list(room["members"]):
        st = referee.BATTLE_STATE.get(k)
        if isinstance(st, dict):
            st["started_at"] = room["started"]
    log(f"PVP ROOM {room['id']}: BATTLE START, reason {reason} = "
        f"{START_REASONS.get(reason, '?')}{' -- ' + why if why else ''}; "
        f"{int(now - room['created'])} s after the room was created; NPC squad "
        f"{'pops now' if room['npc'] else 'stays out (pilots only)'}; cmd 138 "
        f"goes to every member's battle channel on its next datagram")
    return True


def resync_record(started, flags=0x3):
    """cmd 148, 0xB4 bytes: the bits, the start time, and zeros for the rects,
    +0x798 and the holder table (what the block we serve holds there)."""
    body = bytearray(RESYNC_LEN)
    struct.pack_into("<II", body, 0, flags & 0x3, int(started) & 0xFFFFFFFF)
    return fmoworld.record(CMD_BM_RESYNC, bytes(body))


def tick_chan(chan, addr, now=None):
    """On every battle-channel datagram once the self-POP is out: start the
    room on a hostile pilot's arrival or the wait running out, and queue this
    channel's cmd 138 (or cmd 148 for a late joiner) once."""
    if not MATCHING or not getattr(chan, "popped", False):
        return
    room = _chan_room(chan)
    if room is None:
        return
    now = time.time() if now is None else now
    k, m = _chan_member(chan, room)
    if m is None:
        return
    if m["popped"] is None:
        m["popped"] = now
    if room["started"] is None:
        if m["side"] != room["creator_side"]:
            start(room, 1, now, why=f"{m.get('account') or k} (nation {m['side']}) popped in")
        elif PVP_WAIT > 0 and now - room["created"] >= PVP_WAIT:
            start(room, 3, now, why=f"{PVP_WAIT} s without an opponent")
    if room["started"] is None or m["start_sent"]:
        return
    m["start_sent"] = True
    if m["role"] == "late":
        chan.pending.append(resync_record(room["started"]))
        log(f"[udp {addr[0]}:{addr[1]}] -> PVP ROOM {room['id']}: cmd {CMD_BM_RESYNC} "
            f"LATE-JOINER RESYNC (0x611EFF04): bits 0x3 -> block+0x7C 0x600, start "
            f"{room['started']} -> block+0x48, {RESYNC_LEN}B; joined "
            f"{int(m['joined'] - room['started'])} s after the start")
        return
    try:
        rec = fmoworld.record_battle_start(room["reason"], start_gametime=room["started"])
    except ValueError as e:
        log(f"[udp {addr[0]}:{addr[1]}] WARNING: PVP ROOM {room['id']}: cmd 138 refused: {e}")
        return
    chan.pending.append(rec)
    log(f"[udp {addr[0]}:{addr[1]}] -> PVP ROOM {room['id']}: BM cmd 138 BATTLE START, "
        f"reason {room['reason']} ({START_REASONS.get(room['reason'])}), start "
        f"{room['started']} -> block+0x48; 0x61001F40 sets block+0x7C 0x400 and the "
        f"waiting overlay should go")


def note_cmd(chan, addr, cmd, body, now=None):
    """Every record a battle channel carries: cmd 137 = Start Battle; any
    command not in KNOWN_BATTLE_CMDS is logged whole, once per channel."""
    if cmd == CLI_START_BATTLE:
        room = _chan_room(chan)
        if room is None or not MATCHING:
            if not getattr(chan, "_pvp_137_said", False):
                chan._pvp_137_said = True
                log(f"[udp {addr[0]}:{addr[1]}]   cmd 137 = START BATTLE (menu 0:130, "
                    f"0x611D36B0) outside a matching room -- nothing to start")
            return
        if room["started"] is None:
            log(f"[udp {addr[0]}:{addr[1]}]   cmd 137 = START BATTLE chosen in "
                f"PVP ROOM {room['id']}")
            start(room, 2, now, why="a member chose Start Battle (cmd 137)")
        return
    if cmd in KNOWN_BATTLE_CMDS:
        return
    seen = getattr(chan, "_pvp_unknown", None)
    if seen is None:
        seen = chan._pvp_unknown = set()
    if cmd in seen:
        return
    seen.add(cmd)
    room = _chan_room(chan)
    log(f"[udp {addr[0]}:{addr[1]}]   UNKNOWN BATTLE CMD {cmd} ({len(body)}B)"
        + (f" in PVP ROOM {room['id']} ({'waiting' if room['started'] is None else 'started'})"
           if room else "") + ", first time on this channel:" + os.linesep
        + hexdump(bytes(body), indent="      "))


# --------------------------------------------------------------------------- #
# P2: THE ROOM JUDGE (lifted from coliseum.Coliseum.judge)
# --------------------------------------------------------------------------- #
def judge_counts(counts, fielded, started, now, limit):
    """(winner, why) once a started room is decided, else None. `counts` =
    {nation: units standing}, `fielded` = the nations that ever had a unit in
    it. winner 0 = a draw. Pure.
    A side that has fielded units and has none standing loses. At the time
    limit the side with more units standing wins and a tie is a draw.
    WARNING: the time-limit rule (more standing wins, a tie draws) is OURS, a
    guess carried over from the Coliseum judge; SE only says a side wins by
    disabling every enemy."""
    if started is None:
        return None
    a, b = counts.get(1, 0), counts.get(2, 0)
    if 1 in fielded and 2 in fielded:
        if a == 0 and b == 0:
            return 0, "both sides are out"
        if a == 0 or b == 0:
            w = 2 if a == 0 else 1
            return w, (f"nation {3 - w} has no unit left standing "
                       f"(O.C.U. {a} vs U.S.N. {b})")
    elif fielded and all(counts.get(n, 0) == 0 for n in fielded):
        return 0, "the room emptied before an opponent fielded a unit"
    if limit and limit > 0 and now >= started + limit:
        if a == b:
            return 0, f"time limit, {a} vs {b} standing: a draw (OUR rule)"
        w = 1 if a > b else 2
        return w, f"time limit, O.C.U. {a} vs U.S.N. {b} standing (OUR rule)"
    return None


def member_out(m, bkey, now, online, deaths):
    """Why member `m` is no longer standing, or None."""
    from . import referee
    if m.get("out"):
        return m["out"]
    acct = m.get("account") or bkey
    died = deaths.get(acct)
    if died is not None and died >= m["joined"]:
        return "destroyed"
    st = referee.BATTLE_STATE.get(bkey) or {}
    esc = st.get("escaped")
    if esc and esc[2] >= m["joined"]:
        return "ejected (Emergency Escape)"
    if not online(acct):
        return "dropped (no game session)"
    return None


def units_standing(room, now, online, deaths):
    """({nation: units standing}, [detail], {nations fielded}) -- pilots not
    out plus the live units of every NPC squad the room's pilots were given,
    counted for the squad's nation. A nation is FIELDED once it has had a
    pilot in the room or a squad popped, dead or alive."""
    from . import referee
    counts, detail, squads = {1: 0, 2: 0}, [], []
    fielded = {m["side"] for m in room["members"].values() if m["side"] in (1, 2)}
    for k, m in room["members"].items():
        why = member_out(m, k, now, online, deaths)
        if why and not m.get("out_at") and not why.startswith("dropped"):
            # a drop is judged live (a keepalive gap is not a death); the rest stick
            m["out"] = m.get("out") or why
            m["out_at"] = now
        if why:
            detail.append(f"{m.get('account') or k} out ({why})")
            continue
        counts[m["side"]] = counts.get(m["side"], 0) + 1
    for k, m in room["members"].items():
        sq = (referee.BATTLE_STATE.get(k) or {}).get("squad")
        if sq and not any(s is sq for s in squads):
            squads.append(sq)
    for sq in squads:
        n = sq.get("nation")
        alive = [u for u in sq.get("ids", ()) if u not in sq.get("dead", ())]
        if n in (1, 2) and sq.get("ids"):
            fielded.add(n)
            counts[n] = counts.get(n, 0) + len(alive)
            detail.append(f"NPC squad nation {n}: {len(alive)}/{len(sq.get('ids', ()))} up")
    return counts, detail, fielded


def _limit():
    from . import missionblock
    return missionblock.MISSION_TIME


def tick(now=None, online=None, deaths=None):
    """Start waiting rooms whose wait ran out, close abandoned ones, judge the
    started ones. Returns the rooms decided by this call."""
    from . import battleend, trade
    now = time.time() if now is None else now
    online = online or (lambda a: trade.session_for_account(a) is not None)
    deaths = battleend.PILOT_DEATHS if deaths is None else deaths
    decided = []
    with _LOCK:
        rooms = [r for r in ROOMS.values() if r["verdict"] is None]
    for room in rooms:
        if room["started"] is None:
            if all(member_out(m, k, now, online, deaths) or m.get("delivered")
                   for k, m in room["members"].items()):
                _decide(room, 0, "everyone left while it waited (no battle)", now,
                        war=False)
                continue
            if MATCHING and PVP_WAIT > 0 and now - room["created"] >= PVP_WAIT:
                start(room, 3, now, why=f"{PVP_WAIT} s without an opponent")
            continue
        if not JUDGE:
            continue
        counts, detail, fielded = units_standing(room, now, online, deaths)
        room["fielded"] |= fielded
        v = judge_counts(counts, room["fielded"], room["started"], now, _limit())
        if v is not None:
            _decide(room, v[0], v[1] + (f" [{'; '.join(detail)}]" if detail else ""), now)
            decided.append(room)
    return decided


def _decide(room, winner, why, now, war=True):
    with _LOCK:
        if room["verdict"] is not None:
            return
        room["verdict"] = {"winner": winner, "why": why, "at": now}
        if OPEN.get((room["zone"], room["mapno"])) == room["id"]:
            OPEN.pop((room["zone"], room["mapno"]), None)
    log(f"PVP ROOM {room['id']} (selector {room['zone']} map {room['mapno']}): VERDICT "
        + ({1: "O.C.U. WINS", 2: "U.S.N. WINS"}.get(winner, "DRAW / no winner"))
        + f" -- {why}; each pilot's 0x014C follows on its keepalive")
    if war:
        settle_war(room)
    with _LOCK:
        _gc(room)


# --------------------------------------------------------------------------- #
# P3: THE WAR, ONCE PER BATTLE
# --------------------------------------------------------------------------- #
def settle_war(room, state=None):
    """Feed fmowar.settle the room's verdict once: every nation that had
    PILOTS in the room, won = it is the winner, pvp = both nations had pilots
    (fmowar weight 2). A draw moves nothing. Returns [(nation, what)]."""
    from . import referee, warstate
    if not WAR or room.get("war_done"):
        return []
    room["war_done"] = True
    v = room.get("verdict") or {}
    winner = v.get("winner")
    tile = room.get("tile")
    if not winner:
        log(f"PVP ROOM {room['id']}: WAR: no winner, nothing settled")
        return []
    if tile is None or (state is None and (warstate.WAR == "0" or fmowar is None)):
        log(f"PVP ROOM {room['id']}: WAR: no tile / FMO_WAR off, nothing settled")
        return []
    if int(room["zone"]) // 100 in (1, 3):
        log(f"PVP ROOM {room['id']}: WAR: selector {room['zone']} is a Controlled Zone")
        return []
    st = state if state is not None else warstate.war_state()
    if st is None:
        return []
    if state is None:
        warstate._war_tick(st)
    sides = sorted({m["side"] for m in room["members"].values() if m["side"] in (1, 2)})
    pvp = len(sides) >= 2
    for k in room["members"]:
        bs = referee.BATTLE_STATE.get(k)
        if isinstance(bs, dict):
            bs["pvp"] = pvp
    out = []
    for n in sides:
        s, what = st.settle(int(tile), n, won=(n == winner), pvp=pvp)
        out.append((n, what))
        log(f"PVP ROOM {room['id']}: WAR STATE tile {tile}, nation {n} "
            f"{'WON' if n == winner else 'LOST'}{' (PvP, weight 2)' if pvp else ''} -> {what}; "
            f"sector now nation {s.get('nation')} at {s.get('control')}%")
    return out


def war_by_room(bkey):
    """True when this pilot's room settles the war (its own war_settle must not)."""
    return WAR and room_of(bkey) is not None


def judged(bkey):
    """True when this pilot's battle end comes from its room's judge."""
    return JUDGE and room_of(bkey) is not None


def end_due(sess, conn_id, now=None, online=None, deaths=None):
    """On the keepalive: this pilot's 0x014C from the room verdict, once. A
    destroyed/ejected pilot that has waited FMO_PVP_DEATH_WAIT without a
    verdict ends with a loss. Returns the packets."""
    if not JUDGE:
        return []
    try:
        bkey = sess.battle_key()
    except Exception:
        return []
    room = room_of(bkey)
    if room is None or getattr(sess, "battle_end_done", False):
        return []
    now = time.time() if now is None else now
    tick(now, online, deaths)
    m = room["members"].get(bkey)
    if m is None or m["delivered"]:
        return []
    v = room.get("verdict")
    if v is not None:
        won = bool(v["winner"]) and v["winner"] == m["side"]
        why = f"PvP room {room['id']}: {v['why']}"
    elif (m.get("out") and m.get("out_at") and DEATH_WAIT > 0
          and now - m["out_at"] >= DEATH_WAIT):
        won = False
        why = (f"PvP room {room['id']}: {m['out']} and no verdict after "
               f"{DEATH_WAIT} s (FMO_PVP_DEATH_WAIT)")
    else:
        return []
    with _LOCK:
        m["delivered"] = True
    from . import referee
    outs = []
    sess.battle_end_done = True
    st = referee.BATTLE_STATE.get(bkey)
    if isinstance(st, dict):
        st["ended"] = True
    log(f"{sess.peer}   PVP ROOM {room['id']}: this pilot (nation {m['side']}) "
        f"{'WON' if won else 'LOST'}: {why}")
    rp = sess.battle_result_push(conn_id, f"the battle end ({why})", won=won)
    if rp:
        outs.append(rp)
    pr = sess.penalty_report_push(conn_id)
    if pr:
        outs.append(pr)
    be = sess.battle_end_push(conn_id, why=why, won=won)
    if be:
        outs.append(be)
    with _LOCK:
        _gc(room)
    return outs


def withdrew(bkey, why="withdrew (0x013D)"):
    """The pilot left the battle on its own: out of the room, result paid by
    the withdraw path."""
    room = room_of(bkey)
    if room is None:
        return
    with _LOCK:
        m = room["members"].get(bkey)
        if m is not None:
            m["out"] = m["out"] or why
            m["out_at"] = m["out_at"] or time.time()
            m["delivered"] = True
        _gc(room)
    log(f"PVP ROOM {room['id']}: {bkey} {why}; counted out of the room")


def reset():
    """Forget every room (the selftest)."""
    with _LOCK:
        ROOMS.clear()
        OPEN.clear()
        PILOT_ROOM.clear()
