"""The sortie (0x0139 -> 0x013A): the battle map a pilot enters and the endpoint it dials."""
import os
import struct
import time
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# THE SORTIE -- 0x0139 (SelectBattleMap) -> 0x013A (sortie worker, 2026-08-27)
# --------------------------------------------------------------------------- #
# STATIC RE of the unpacked client DLL; NOT live-tested. This
# is the message that ENTERS SCENE 4 (the battle-map scene whose phase-0 arm
# 0x61004C30 registers the type-1 map at 0x61004C99) -- the door the
# mission-block worker found and fmo.py had never answered.
#
# 0x0139 -- built ONLY at 0x61173C59 by 0x61173BE0(this=lobby, a1..a5), 80 B:
#     gate:   [lobby+0x20]==4 && [lobby+0x24] in {1,2}, else -1 (not sent);
#             a2==0 && [lobby+0x6E3A]!=0 -> -1 (a resume is already pending)
#     +0x00 u32  a1  (also stored to lobby+0x4F08)        Q139_ID
#     +0x04 u8   a2  (kycli passes [lobby+0x6E3A]; the UI passes 0)  Q139_RESUME
#     +0x08 u32  ([lobby+0x68] > 0 && [lobby+0x6C] > 0) ? 1 : 0     Q139_BGFLAG
#     +0x0C u32  a4                                       Q139_INFOID
#     +0x10..+0x3F  48 B from [[globals+0x198]+0x2C]+0x388 (only when that
#                object exists, 0x61165BA0), then +0x10 is OVERWRITTEN by a3
#     +0x40 u32  a5                                       Q139_AREA
#     +0x44..+0x4F  never written (zero from the builder's alloc)
#   Two senders: (1) the poller object 0x61184710 (vtable 0x61335128, ctor
#   0x610F8E70) which every UI builds -- ctor args map a2->+0x00, a3->+0x10,
#   a4->+0x0C, a5->+0x40 -- and (2) kycli_lobmain state 10 (0x6117BB07,
#   table 0x6117BEB4[10]) with (+0x00=[lobby+0x4F08], +0x04=[lobby+0x6E3A]),
#   reached from the Colosseum-return arm 0x61177480 ("EndCOLReturnGroup",
#   "BMResumedFromLD=%d"): that is the RESUME path, whose success text is
#   0xC0080019 = systext 8:25 "You did not log out cleanly last time, so you
#   are being reconnected to the battle map".
#   The retail UI sender is the MISSION screen: command main menu UI event
#   0x1045 (table 0x6117DD4C, event = 0x1041+i) -> a 0x5B81-byte screen
#   (ctor 0x611CA720, vtable 0x61342518; systext 21:28 "Accept this
#   mission?", 21:25 "This mission has been accepted", 28:7 "Please select a
#   target sector") which embeds the sortie UI (ctor 0x6118C190, vtables
#   0x6133DD08/0x6133DD60, event handler 0x6118E110). Its confirmation
#   dialog is 0xC00A0002 = 10:2 "Sortieing to the battle map." (10:18 for the
#   training ground; 10:1 "Unavailable: the battle group is on a sortie");
#   on dialog result 0xF it sends +0x00 = [ui+0x5A10] (the map-list widget's
#   selection, vt slot 0xA4 of [[0x613B703C]+0x4A]), on result 1 it sends
#   +0x0C = row[selected].dword0 of the 0x70-byte table at lobby+0x771C with
#   +0x10 = 1 (a CREATE). That table is loaded from an on-disk "ARE" file by
#   0x61173FE0 (count at file+0x20, (count+1)*0x70 bytes), not from us.
#   WARNING: Which of +0x00 / +0x0C names a type-1 MapNo is UNKNOWN -- both are
#   server-side ids; the reply's block carries the MapNo, so the server maps
#   them. Log both, decide nothing from them.
#
# 0x013A -- what the client READS (poller 0x61184710 -> UI event 0x106E ->
# 0x6118E6D5 / 0x610FB842; kycli state 11 0x6117BB50):
#     hdr+0x0C u32  if nonzero: % 1000000 -> lobby+0x4F02 -> globals+0x1B8
#                   (kycli path only; the UI path passes its own seed)
#     +0x00  20 B   -> globals+0x28 via 0x61006270's 5-dword copy = the
#                   BATTLE UDP endpoint ("%xbattle" = word[globals+0x2A] +
#                   dword[globals+0x2C] + char_id; the lobby's twin is our
#                   0x0153 +0x00 -> globals+0x3C). Same 20-byte struct.
#     +0x14..+0x2B  not read by either consumer
#     +0x2C  3400 B -> lobby+0x5C7E via 0x61175600 (0x6117BC8D `add eax,0x2c`;
#                   0x610FB8A2 `lea edx,[edi+0x2c]`) -- THE SAME BLOCK 0x01C1
#                   carries at its +0x18 (MB_* offsets apply): MapNo at +0x2C
#                   is the type-1 id 0x61004C99 registers.
#     +0xD74  88 B  -> globals+0x78 (0x6117BC7B / 0x6118E700, 0x16 dwords) --
#                   the third copy of the unidentified 88-byte "info" struct
#                   (0x0153 +0x124 -> globals+0xD0; 0x0155 entry +0x20 ->
#                   globals+0x128). Served zero.
#   so the payload is 0xDCC = 3,532 bytes. Then 0x6117AC40(1, 0) (enter
#   battle mode: lobby+0x30 = 1, "BMResumedFromLD"), lobby+0x6E3A = 0, and
#   0x61006270(globals, seed, payload): globals+0x1B8 = seed, globals+0x28 =
#   endpoint, and -- if [globals+0x24] != 0 and the current scene [+0x1C4] !=
#   8 and no scene is pending -- [globals+0x1D0] = 4: SCENE 4 IS ENTERED on
#   the next dispatcher tick. The UI then sets [ui+0xAC] = 1; kycli sets
#   [lobby+0x24] = 2 and shows 8:25.
#   FAILURE arm: any id other than 0x013A. The poller posts UI event 0x106F
#   with word[reply+8] (the client shows 0xC00A0006 = 10:6 "Failed to select
#   the battle map" and stays in the lobby); kycli shows 0xC008001B = 8:27
#   "Connection failed (SelectBattleMap error)" and resets ([lobby+0x24] = 2).
#   An UNANSWERED 0x0139 parks the poller in state 1 forever (0x61184780:
#   `jge` back on 0) -- the 0x01AB hang -- so this file never stays silent.
#
# WARNING: CRASH-SAFETY GATE. Scene 4's phase-0 arm registers `type 1, id = MapNo`
# unconditionally; an id with no file is the 0x611250A2 double relocation.
# Type-1 id 0 IS on disk (384,480 B) but has never been loaded either. So the
# MapNo is REQUIRED and gated on TYPE1_ON_DISK exactly like FMO_MISSION_MAPNO:
# unset or off-disk = a real refusal (message 2), never a zero block.
#
# NOTE: A THIRD ROUTE, NOT BUILT: server push 0x014C (handler 0x6117E33B in the
# lobby push dispatcher 0x6117DFEC; systext 8:76-80 "Sortieing to the mission
# battle automatically in %d seconds" / "Your destination is %s") carries
# payload+0x00 id -> lobby+0x4F1A, +0x04 time -> +0x4F1E, +0x08 endpoint ->
# +0x4F22, +0x34 the 3400-byte block, and kycli state 2 (0x6117BF26) then
# waits for a message 1 and enters scene 4 at 0x6117C132 from those fields.
# That is how battle-group MEMBERS follow the leader. Separate brief.
MSG_SORTIE_REQ = 0x0139
MSG_SORTIE_REPLY = 0x013A
REQ_0139_LEN = 0x50                    # 0x61173C52 `push 0x50`
Q139_ID = 0x00                         # u32 a1 (0x61173C60)
Q139_RESUME = 0x04                     # u8 a2 (0x61173C63)
Q139_BGFLAG = 0x08                     # u32 (0x61173C81)
Q139_INFOID = 0x0C                     # u32 a4 (0x61173CB3)
Q139_CREATE = 0x10                     # u32 a3 (0x61173CB1), over the 48 B
Q139_CHAR48 = 0x10                     # 48 B (0x61173CAB, 0xC dwords)
Q139_CHAR48_LEN = 0x30
Q139_AREA = 0x40                       # u32 a5 (0x61173C84)
REPLY_013A_LEN = 0xDCC                 # 3532 = R13A_INFO88 + 88
R13A_ENDPOINT = 0x00                   # 20 B -> globals+0x28 (0x61006270)
R13A_BLOCK = 0x2C                      # 3400 B -> lobby+0x5C7E (0x6117BC8D)
R13A_INFO88 = 0xD74                    # 88 B -> globals+0x78 (0x6117BC7B)
R13A_INFO88_LEN = 0x16 * 4
HDR_TIME = 0x0C                        # packet header u32 (0x6117BCAE)

#: WARNING: OFF BY DEFAULT. Off: 0x0139 is answered with message 2 (the client's own
#: failure arm -- 10:6 on screen, back to the lobby, no scene change), which
#: is the first time this request is answered at all. On: reply_013a() serves
#: the endpoint + the block, and the client ENTERS SCENE 4 -- a scene this
#: project has never seen run. NOT LIVE-TESTED.
SERVE_SORTIE = (os.environ.get("FMO_SORTIE", "").strip() or "0") != "0"
#: The type-1 map to load, REQUIRED: '' = refuse; an int MUST be in
#: TYPE1_ON_DISK or it is refused. '0' is explicit and allowed (on disk).
SORTIE_MAPNO = os.environ.get("FMO_SORTIE_MAPNO", "").strip()
#: The battle endpoint at +0x00. Default = the same door 0x0153 advertises
#: (BATTLE_HOST:BATTLE_PORT, EP_0153_NET flavour); '0' leaves it zero as a
#: control ("does the scene dial at all?").
SORTIE_ENDPOINT = os.environ.get("FMO_SORTIE_ENDPOINT", "1") != "0"
SORTIE_HOST = os.environ.get("FMO_SORTIE_HOST", "").strip()
#: WARNING: compose passes "${FMO_SORTIE_PORT:-}", so an unset prod .env hands us ""
#: -- and int("", 0) at import took the whole fmo service down on 2026-08-27
#: (the exact float("") trap the FMO_UDP_POP_POS note documents). Empty = 0.
SORTIE_PORT = int(os.environ.get("FMO_SORTIE_PORT", "0").strip() or "0", 0)


def sortie_mapno(spec=None):
    """(type-1 id or None, source). None means REFUSE, and says why."""
    s = SORTIE_MAPNO if spec is None else spec
    if s is None or (isinstance(s, str) and s.strip() == ""):
        return None, ("REFUSED: FMO_SORTIE_MAPNO is unset -- a zero block "
                      "would register type-1 id 0 blind; set an on-disk id")
    try:
        mn = int(s, 0) if isinstance(s, str) else int(s)
    except ValueError:
        return None, f"REFUSED: FMO_SORTIE_MAPNO={s!r} is not an integer"
    if mn not in missionlist.TYPE1_ON_DISK:
        return None, (f"REFUSED: type-1 id {mn} is NOT on disk (would crash "
                      f"the client at 0x611250A2)")
    return mn, (f"FMO_SORTIE_MAPNO, type-1 id {mn} IS on disk "
                f"(index {53557 + mn})")


def sortie_fields(mapno=None, ep_enable=None, host=None, port=None, **knobs):
    """(label, PAYLOAD offset, raw bytes, source) for every field that is ON.

    The block fields other than MapNo come from mission_fields() -- the same
    FMO_MISSION_* knobs, the same LeaderID candidate -- shifted from 0x01C1's
    +0x18 to 0x013A's +0x2C. A refused MapNo is returned as a note with empty
    bytes, and reply_013a() then returns None (the caller refuses)."""
    out = []
    ep_on = SORTIE_ENDPOINT if ep_enable is None else ep_enable
    if ep_on:
        h = host or SORTIE_HOST or addressing.BATTLE_HOST
        pt = port or SORTIE_PORT or addressing.BATTLE_PORT
        _ep = addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint
        out.append(("endpoint", R13A_ENDPOINT, _ep(h, pt),
                    f"{h}:{pt} -> globals+0x28, the BATTLE UDP endpoint "
                    f"({'net' if addressing.EP_0153_NET else 'le'} order, as 0x0153's)"))
    mn, src = sortie_mapno(mapno)
    out.append(("MapNo", R13A_BLOCK + missionblock.MB_MAPNO,
                struct.pack("<I", mn) if mn is not None else b"", src))
    if missionblock.BATTLE_START_TIME and "start_time" not in knobs:
        knobs["start_time"] = int(time.time())
    for label, off, raw, s in missionblock.mission_fields(mapno=0, **knobs):
        out.append((label, R13A_BLOCK + off, raw, s))
    return out


def reply_013a(**knobs):
    """The 3,532-byte 0x013A payload, or None when the MapNo is refused."""
    fields = sortie_fields(**knobs)
    if any(not raw for label, _o, raw, _s in fields if label == "MapNo"):
        return None
    b = bytearray(REPLY_013A_LEN)
    for _label, off, raw, _src in fields:
        if raw:
            b[off:off + len(raw)] = raw
    return bytes(b)


def parse_0139(payload):
    """Every field 0x61173BE0 stores, by name. Short payloads decode to 0."""
    p = bytes(payload) + bytes(max(0, REQ_0139_LEN - len(payload)))
    u32 = lambda o: struct.unpack_from("<I", p, o)[0]
    return {
        "id": u32(Q139_ID), "resume": p[Q139_RESUME],
        "bgflag": u32(Q139_BGFLAG), "infoid": u32(Q139_INFOID),
        "create": u32(Q139_CREATE),
        "char48": p[Q139_CHAR48:Q139_CHAR48 + Q139_CHAR48_LEN],
        "area": u32(Q139_AREA), "tail": p[Q139_AREA + 4:REQ_0139_LEN],
        "len": len(payload),
    }


#: KEY: JOINING A BATTLE MAP (乱入), SE's rules (2026-09-30):
#:   update 050628 lines 22-25: "As the join-in limit approaches, the kill and
#:     counterattack bonuses of the side that joined decrease. Joining just
#:     before the limit roughly halves them (about 50%); joining right after
#:     the map was created pays more (up to about 120%). The group that
#:     CREATED the map gets the normal bonus whatever the enemy's timing."
#:   topics/060804: "until now ... enemy BGs could sortie in for only 5
#:     minutes"; PvP maps now wait up to 20 minutes for an enemy BG ("WAITING
#:     FOR OPPONENTS"), both sides starting fresh when it arrives; "once the
#:     battle has started, joining is possible for 5 minutes as before."
#:   guide/mapselector.html:60 and warmap.S15F_SIDE_CAP: 10 vs 10 per map.
#: The client's own refusals (code -> message table 0x613955F0; the sortie
#: failure arm 0x611818EE draws 10:6 with the code's line under it):
JOIN_CODE_LATE = -31107       # 3:7 "This battle map is past its join-in window"
JOIN_CODE_SIDE_FULL = -31100  # 3:3 "...At most 10 people can sortie to one sector."
#: FMO_JOIN_WINDOW: seconds after a map's first sortie that others may still
#: join (SE: 5 minutes = 300). 0 = no window and no side cap (the pre-09-30
#: behaviour). FMO_JOIN_PVP_WAIT: how long a map nobody of the other side has
#: reached yet stays open to that side (SE: 20 minutes = 1200); WARNING: SE did
#: this on PvP-capable areas only, and this server does not know which areas
#: those are, so it applies to every map (ours). FMO_JOIN_PCT_EARLY /
#: FMO_JOIN_PCT_LATE: the late side's bonus percent just after creation and at
#: the limit (SE: "about" 120 / 50); straight-line between them, OURS.
JOIN_WINDOW = _env_int("FMO_JOIN_WINDOW", "300")
JOIN_PVP_WAIT = _env_int("FMO_JOIN_PVP_WAIT", "1200")
JOIN_PCT_EARLY = _env_int("FMO_JOIN_PCT_EARLY", "120")
JOIN_PCT_LATE = _env_int("FMO_JOIN_PCT_LATE", "50")


def join_pct(elapsed, window=None, early=None, late=None):
    """The late side's bonus percent for joining `elapsed` s after the map
    was created: linear from `early` at 0 to `late` at `window`. Pure."""
    window = JOIN_WINDOW if window is None else window
    early = JOIN_PCT_EARLY if early is None else early
    late = JOIN_PCT_LATE if late is None else late
    if window <= 0:
        return 100
    f = min(1.0, max(0.0, float(elapsed) / window))
    return int(round(early + (late - early) * f))


def join_verdict(entries, my_side, my_gid, now, cap=None, window=None,
                 pvp_wait=None):
    """Whether a pilot of side `my_side` (0/1), in battle group `my_gid`, may
    sortie onto a battle map whose current fighters are `entries` =
    [(side, granted_at, gid)] (not counting this pilot). Returns (code, why,
    pct): code None = allowed; pct = its join-in bonus percent, or None for
    the creating side (always 100%, SE). Pure."""
    cap = warmap.S15F_SIDE_CAP if cap is None else cap
    window = JOIN_WINDOW if window is None else window
    pvp_wait = JOIN_PVP_WAIT if pvp_wait is None else pvp_wait
    entries = [e for e in entries if e[0] in (0, 1)]
    if window <= 0 or not entries:
        return None, "creates the map" if entries == [] else "FMO_JOIN_WINDOW=0", None
    first = min(entries, key=lambda e: e[1])
    elapsed = now - first[1]
    mine = [e for e in entries if e[0] == my_side]
    if cap > 0 and len(mine) >= cap:
        return JOIN_CODE_SIDE_FULL, (f"side {my_side} already has {len(mine)} of "
                                     f"{cap} on this map (10 vs 10)"), None
    if my_gid and any(e[2] == my_gid for e in entries):
        return None, "joins its own battle group on the map", None
    if my_side == first[0]:
        if elapsed <= window:
            return None, f"the creating side, {int(elapsed)} s in", None
        return JOIN_CODE_LATE, (f"{int(elapsed)} s since the map was created, "
                                f"the join-in window is {window} s"), None
    if not mine and elapsed <= pvp_wait:
        return None, (f"the first of its side, {int(elapsed)} s in (within the "
                      f"{pvp_wait} s wait for opponents): both start fresh"), 100
    if elapsed <= window:
        return None, f"joins the enemy's map {int(elapsed)} s in", join_pct(elapsed, window)
    return JOIN_CODE_LATE, (f"{int(elapsed)} s since the enemy created the map, "
                            f"the join-in window is {window} s"), None


class SessionSortie:
    """Session's sortie requests: 0x0139, the group sortie GO and the battle withdraw."""

    def map_fighters(self, mapno, now=None):
        """[(side, granted_at, gid)] of everyone else fighting on `mapno`
        now, from BATTLE_STATE (the census warmap_census counts)."""
        now = time.time() if now is None else now
        stale = missionblock.MISSION_TIME if missionblock.MISSION_TIME > 0 else 3600
        me = self.battle_key()
        out = []
        for host, st in list(referee.BATTLE_STATE.items()):
            if (host == me or not isinstance(st, dict) or st.get("mapno") != mapno
                    or st.get("ended") or now - st.get("granted_at", 0) >= stale):
                continue
            out.append((popnation.battle_side_for(host)[0], st["granted_at"],
                        groupchannel.GROUP_OF.get(host)))
        return out

    def platoon_sortie_verdict(self, q, mapno):
        """(code, why) when this sortie must be refused by a battle-group or
        join-in rule, else None. Sets self.join_pct for the settlement."""
        self.join_pct = None
        gid = groupchannel.GROUP_OF.get(getattr(self, "account", None))
        leader = bool(q.get("bgflag") and gid
                      and battlegroups.group_leader(gid) == self.account)
        st = battlegroups.group_state(gid) if gid else None
        bonus = int((st or {}).get("bonus") or 0) if battlegroups.BG_BONUS else 0
        if leader and bonus > 0:
            mc = self.stored_money()
            if mc is not None:
                no = battlegroups.bonus_sortie_verdict(bonus, mc[0], pushes.SORTIE_COST)
                if no is not None:
                    return no
        no = self.bg_cost_sortie_verdict(q, gid, st)
        if no is not None:
            return no
        if JOIN_WINDOW <= 0 or mapno is None:
            return None
        fighters = self.map_fighters(mapno)
        _pc = self.playing_char() if charstore.CHAR_STORE else None
        side = (popnation.NATION_SIDE.get(popnation.character_nation(_pc)[0])
                if _pc else popnation.battle_side_for(self.battle_key())[0])
        if leader and bonus > 0 and any(f[0] == side for f in fighters):
            # 2:99: a bonus group may not join a map its own side is on
            return battlegroups.CODE_BONUS_ALLIES, (
                f"group {gid} has a H$ {bonus} platoon bonus set and map {mapno} "
                f"already has pilots of this side (2:99)")
        if side not in (0, 1):
            return None
        # KEY: A MATCHING ROOM (pvproom.py, Frontline PvP): open to both sides
        # while it waits; the 5-minute window runs from the START
        _pv = pvproom.join_verdict(getattr(self, "sector_zone", None), mapno, side + 1,
                                   gid, time.time())
        code, why, pct = _pv if _pv is not None else join_verdict(
            fighters, side, gid, time.time())
        log(f"{self.peer}   JOIN-IN: map {mapno}, side {side}, "
            f"{len(fighters)} other pilot(s) on it: {why}"
            + (f" -> bonus {pct}%" if pct is not None else ""))
        if code is not None:
            return code, why
        self.join_pct = pct
        return None

    def bg_cost_sortie_verdict(self, q, gid, st):
        """(code, why) when a B.G.Cost rule refuses this sortie, else None
        (battlegroups.cost_sortie_verdict). The pilot's own cost against the
        group's Required B.G.Cost (3:11), the platoon total against the
        sector's B.G.Cost (3:5), and the pilot against the sector minimum and
        the minimum of the mission this sector's tile completes (3:8)."""
        if not battlegroups.BG_COST:
            return None
        _pc = self.playing_char() if charstore.CHAR_STORE else None
        cost = battlegroups.note_pilot_cost(getattr(self, "account", None), _pc)
        if cost is None:
            return None
        tile = int(self.sector[0]) if self.sector else None
        # WARNING: the war state carries bg_max / bg_min per sector (FMO_WAR_MAP
        # serves them at +0x31 / +0x55), but nothing fills them yet: SE says
        # they follow the supply rate (D08 102) and the formula is unknown.
        # 0 = unknown, so these two rules wait for data.
        sec = {}
        ws = warstate.war_state() if tile is not None else None
        if ws is not None:
            sec = (ws.data.get("sectors") or {}).get(str(tile)) or {}
        total = cost
        if q.get("bgflag") and gid:
            # the platoon's live members, this pilot always among them
            mem = [a for a in groupchannel.GROUP_MEMBERS.get(gid, [])
                   if a != self.account and groupchannel.group_member_live(a)]
            total = battlegroups.group_total_cost(gid, mem + [self.account])
        mission_min = 0
        # only when the tile can name a mission (fmo-gates.json); without it
        # progress_frontier would return every open mission
        if tile is not None and _pc and progress.MISSION_TILES:
            nat = popnation.character_nation(_pc)[0]
            mission_min = battlegroups.mission_min_cost(
                nat, progress.progress_frontier(_pc, tile=tile))
        no = battlegroups.cost_sortie_verdict(
            cost, total=total,
            required=int((st or {}).get("required") or 0) if gid else 0,
            sector_max=int(sec.get("bg_max") or 0),
            sector_min=int(sec.get("bg_min") or 0), mission_min=mission_min)
        log(f"{self.peer}   B.G.COST: pilot {cost}, total {total}, tile {tile}, "
            f"sector {sec.get('bg_max') or '?'} / min {sec.get('bg_min') or '?'}, "
            f"mission min {mission_min or '-'}"
            + (f" -> REFUSED: {no[1]}" if no else " -> ok"))
        return no

    def platoon_sortie_record(self, q, mapno, conn_id):
        """After a GRANTED sortie: the leader's group sortie takes the
        B.G.Bonus from the leader's wallet (10:27) and counts the battle; a
        member sortieing onto its group's map joins that battle. Returns the
        packets to send (a 0x01A1 wallet store for the debit)."""
        gid = groupchannel.GROUP_OF.get(getattr(self, "account", None))
        if not gid or mapno is None:
            return []
        outs = []
        if q.get("bgflag") and battlegroups.group_leader(gid) == self.account:
            _pc = self.playing_char() if charstore.CHAR_STORE else None
            pid = charlist.to_wire(_pc["id"]) if _pc and _pc.get("id") else 0
            rec = battlegroups.group_battle_begin(gid, self.account, mapno, pid)
            # the Scramble Board rule: members not Ready are left behind
            # and removed (manual p.54; battlegroups.auto_remove_unready)
            battlegroups.auto_remove_unready(gid, self.account)
            if rec["bonus"] > 0:
                now = self.credit_money(f"B.G.Bonus for group {gid}",
                                        money=-rec["bonus"])
                if now is None:
                    rec["bonus"] = 0
                    log(f"{self.peer}   WARNING: B.G.Bonus NOT taken: no stored "
                        f"wallet, so nothing will be shared out either")
                else:
                    outs.append(pushes.fee_push_packet(conn_id, money=now[0]))
                    log(f"{self.peer}   B.G.BONUS: H$ {rec['bonus']} taken from "
                        f"the leader at sortie (10:27) -> wallet {now[0]}, "
                        f"0x{pushes.MSG_FEE_PUSH:04X} store with no fee line")
            log(f"{self.peer}   GROUP BATTLE: group {gid} battle {rec['n']}"
                + (f" of {rec['n'] + rec['left']}" if rec["left"] is not None else "")
                + f" on map {mapno}"
                + (", Continuation = do not continue" if rec["cont_off"] else ""))
        else:
            rec = battlegroups.group_battle_join(
                gid, self.account, mapno, max(JOIN_WINDOW, JOIN_PVP_WAIT, 600))
            if rec is not None:
                log(f"{self.peer}   GROUP BATTLE: joined group {gid}'s battle "
                    f"{rec['n']} on map {mapno} ({len(rec['joined'])} member(s) in it)")
        return outs

    def on_sortie(self, p):
        """0x0139 SelectBattleMap -> 0x013A (scene 4) or message 2 (refuse).

        See MSG_SORTIE_REQ for the decode. NEVER silent: an unanswered 0x0139
        parks the poller 0x61184710 in state 1 for good. Off (the default),
        or a MapNo that is unset / not on disk, is answered with the client's
        own failure arm, which shows 10:6 and leaves the lobby scene alone."""
        q = parse_0139(p["payload"])
        log(f"{self.peer}   0x0139 = SORTIE (SelectBattleMap, 0x61173BE0), "
            f"{q['len']}B{'' if q['len'] == REQ_0139_LEN else ' WARNING: not 80'}: "
            f"+0x00 id={q['id']} (-> lobby+0x4F08)  +0x04 resume={q['resume']} "
            f"({'kycli RESUME path, [lobby+0x6E3A]' if q['resume'] else 'UI path'})  "
            f"+0x08 bgflag={q['bgflag']} (lobby+0x68>0 && +0x6C>0)  "
            f"+0x0C infoid={q['infoid']}  +0x10 create={q['create']}"
            + ("  (row.dword0 at +0x0C with create=1 = the mission table's "
               "CREATE form)" if q["create"] == 1 else "")
            + f"  +0x40 area={q['area']}")
        log(f"{self.peer}      +0x14..+0x3F (48B from [[globals+0x198]+0x2C]"
            f"+0x388, +0x10 overwritten): {q['char48'][4:].hex()}"
            f"{'' if q['tail'] == bytes(len(q['tail'])) else '  WARNING: tail nonzero ' + q['tail'].hex()}")
        if not charselect.ANSWER_LOBAPI:
            log(f"{self.peer}   FMO_ANSWER_LOBAPI=0 -- staying silent; the "
                f"sortie poller 0x61184710 waits in state 1 forever")
            return []
        why = None
        #: None means "use FMO_SORTIE_MAPNO" all the way down to
        #: sortie_mapno(), so the not-served arm below cannot leave it unbound.
        _mn = None
        if not SERVE_SORTIE:
            why = "FMO_SORTIE=0 (default): the sortie is not served"
        elif (charstore.TRAINING_GATE and not self.pilot_trained()
              and q["create"] not in (penalty.CREATE_TRAINING, penalty.CREATE_RETRAIN)):
            # the training sortie itself (0xE30A: create 3, 2 = retraining) is
            # the door out of this gate; the sergeant fires it right after
            # 104 [128] (0x8074 0x627e). Refusing it locked new pilots out.
            why = ("FMO_TRAINING_GATE=1: this pilot has not finished training "
                   "(progress byte 128 != 99). SE's flow: greet the sergeant, "
                   "do the training sortie, THEN sortie to the battlefields "
                   "(intro/flow.html); 105 [128] after the training sets the byte")
        else:
            # KEY: THE SECTOR THE WAR MAP ASKED ABOUT WINS. self.sector is set
            # by the 0x015E that opened this screen; without one (a kycli
            # RESUME, or the war map never opened) this is FMO_SORTIE_MAPNO,
            # exactly as every sortie before 2026-09-08.
            _mn = str(self.sector[2]) if self.sector else None
            # KEY: A GROUP FOLLOWER (the 9:10 YES) never opened the war map: its
            # 0x0139 +0x00 is the map our 0x0163 offered. Live 2026-09-27 a follower
            # asked for 267 and was sent FMO_SORTIE_MAPNO's 418 -- two maps.
            _gs = groupchannel.GROUP_SORTIE.get(groupchannel.GROUP_OF.get(self.account))
            if _gs and q.get("id") and q["id"] == _gs.get("map"):
                if _mn != str(_gs["map"]):
                    log(f"{self.peer}   sortie map {_gs['map']} = the battle "
                        f"group's (followed via 9:10), not "
                        f"{_mn or 'FMO_SORTIE_MAPNO'}")
                _mn = str(_gs["map"])
            _side = zoneentry.nation_for_session(self.playing_char() if charstore.CHAR_STORE
                                                 else None, status.STATUS_NATION,
                                                 "FMO_STATUS_NATION")[0]
            # FRONTLINE MATCHING (pvproom.py): the room this sortie would
            # enter decides block+0x7C (WAITING / started) and +0x50
            try:
                _pv_map = int(sortie_mapno(_mn)[0])
            except (TypeError, ValueError):
                _pv_map = None
            _pv_role, _pv_room = (pvproom.peek(getattr(self, "sector_zone", None), _pv_map)
                                  if self.sector else (None, None))
            _pv_knobs = pvproom.block_knobs(_pv_role, _pv_room)
            # ONE stamp: block+0x48 AND cmd 138 (a late joiner: the room's start)
            _t0 = pvproom.start_stamp(_pv_role, _pv_room) or int(time.time())
            body = reply_013a(mapno=_mn, host=addressing.host_for(
                SORTIE_HOST or addressing.BATTLE_HOST, self.ip), side=_side,
                start_time=(_t0 if missionblock.BATTLE_START_TIME else 0), **_pv_knobs)
            if body is None:
                why = next(s for l, _o, r, s in sortie_fields(mapno=_mn)
                           if l == "MapNo")
            else:
                # WARNING: the PS2 gate: the console's type-1 set is unknown
                why = self.ps2_type1_refusal(sortie_mapno(_mn)[0])
        if not why:
            # JOIN-IN + PLATOON RULES (2026-09-30): the 5-minute window, 10 a
            # side, and the leader's B.G.Bonus cover -- refused with the
            # client's OWN code so 10:6 carries SE's reason under it.
            try:
                _pmap = int(sortie_mapno(_mn)[0])
            except (TypeError, ValueError):
                _pmap = None
            _pno = self.platoon_sortie_verdict(q, _pmap)
            # PENALTY: revoked clearance leaves only the (re)training sortie;
            # also notes whether this is the retraining battle (create 2)
            _pno = penalty.sortie_verdict(self, q) or _pno
            if _pno is not None:
                log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} FAILURE, code "
                    f"{_pno[0]}: {_pno[1]}. 0x611818EE draws 10:6 with the "
                    f"code's own line (table 0x613955F0).")
                return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(),
                                     _pno[0] & 0xFFFF)]
        if why:
            log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} FAILURE, code {charselect.FAIL_CODE}: "
                f"{why}. The poller posts UI event 0x106F (10:6 \"Failed to "
                f"select the battle map\"), kycli shows 8:27; NO scene change.")
            return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), charselect.FAIL_CODE)]
        fields = sortie_fields(mapno=_mn, side=locals().get("_side"),
                               start_time=(locals().get("_t0") or 0)
                               if missionblock.BATTLE_START_TIME else 0,
                               **(locals().get("_pv_knobs") or {}))
        if self.sector:
            log(f"{self.peer}   sortie map comes from the SECTOR this "
                f"connection picked: selector {zoneentry.MAPKIND} sector "
                f"{self.sector[1]} (tile {self.sector[0]}) -> map "
                f"{self.sector[2]}, not FMO_SORTIE_MAPNO")
        log(f"{self.peer}   -> 0x{MSG_SORTIE_REPLY:04X}, {len(body)}B "
            f"(FMO_SORTIE=1): endpoint at +0x00 -> globals+0x28, the 3,400-byte "
            f"block at +0x{R13A_BLOCK:X} -> lobby+0x5C7E, 88B at +0x{R13A_INFO88:X} "
            f"-> globals+0x78 (zero). hdr+0x0C = 0, so lobby+0x4F02 is untouched.")
        for label, off, raw, src in fields:
            log(f"{self.peer}      {label} payload+0x{off:03X} = "
                f"{raw.hex() if raw else '(zero)'}  source: {src}")
        # KEY: A GROUP SORTIE (bgflag): remember where the group went, so the
        # other members' 0x0163 carries the on-sortie flag + map and their
        # client offers 9:10 "The battle group is on a sortie" -- whose YES
        # sends a 0x0139 for that same map.
        # WARNING: _mn is the STRING sortie_mapno() takes; the row wants the int
        # (live 22:20Z: a str & int here closed the leader's connection).
        # Bookkeeping only, so it must never cost the player the sortie.
        if body is not None:
            try:
                rooms.SORTIE_MAP[self.battle_key()] = int(sortie_mapno(_mn)[0])
            except Exception:
                pass
        _gid = groupchannel.GROUP_OF.get(self.account)
        if q.get("bgflag") and _gid:
            try:
                _map = int(sortie_mapno(_mn)[0])
                _tile = int(self.sector[0]) if self.sector else 0
                _zone = int(getattr(self, "sector_zone", None) or 0)
                groupchannel.GROUP_SORTIE[_gid] = {
                    "map": _map,
                    "sector": (_zone * 1_000_000 + _tile) & 0xFFFFFFFF,
                    "row": (warmap.warmap_rows([_map]) or [b""])[0],
                    "at": time.time(), "by": self.account}
                log(f"{self.peer}   GROUP SORTIE: group {_gid} is on a sortie "
                    f"to map {_map} (sector {groupchannel.GROUP_SORTIE[_gid]['sector']}) -- "
                    f"the other members' 0x0163 now offers them 9:10 to follow")
            except Exception as e:
                log(f"{self.peer}   WARNING: GROUP SORTIE not recorded ({e!r}); the "
                    f"sortie itself goes ahead")
        log(f"{self.peer}   WARNING: THIS ENTERS SCENE 4: 0x61006270 sets "
            f"[globals+0x1D0]=4; 0x61004C30 then registers type-1 id "
            f"{next(struct.unpack_from('<I', r)[0] for l, _o, r, _s in fields if l == 'MapNo')} "
            f"(0x61004CC7), reads the picker 0x61198760, and the battle UDP "
            f"manager (id 0, key %xbattle) has an address for the first time. "
            f"Bar: a type-1 row with THAT id and its file's size in "
            f"fmocrash.py --live, not a screenshot.")
        # PROGRESSION: this sortie was GRANTED; the next world-entry join on
        # this session is the return, and the frontier mission advances then.
        self.sortie_pending = True
        self.sortie_granted_at = time.time()
        self.battle_end_done = False
        self.battle_settlement = None          # one settlement per sortie
        # the battle map this sortie went to: the war map's census key
        _bsr0 = referee.battle_state(self.battle_key(), reset=True)
        _bsr0["mapno"] = next(
            struct.unpack_from("<I", r)[0] for l, _o, r, _s in fields
            if l == "MapNo")
        # the block+0x48 stamp, so BM cmd 138 can repeat it (see below)
        _bsr0["start_unix"] = next(
            (struct.unpack_from("<I", r)[0] for l, _o, r, _s in fields
             if l == "StartGameTime" and r), None)
        # SOLO AREA (solo.py, SE's Festa 2006 rules): a sortie to the solo
        # sector (FMO_SOLO_AREA, default O.C.U. 統制区10 セクター14 =
        # selector 109 tile 74149) runs the solo squad in this battle
        # FRONTLINE MATCHING: into the room (created when this sortie makes the map)
        if locals().get("_pv_role"):
            try:
                _pv_got, _ = pvproom.join(
                    self.battle_key(), self.account, getattr(self, "sector_zone", None),
                    _bsr0["mapno"], int(self.sector[0]), _side, time.time(),
                    gid=groupchannel.GROUP_OF.get(self.account))
                if _pv_got != _pv_role:
                    log(f"{self.peer}   WARNING: PVP ROOM: served as '{_pv_role}' but "
                        f"joined as '{_pv_got}' (another sortie landed in between)")
            except Exception as e:      # bookkeeping must never cost the sortie
                log(f"{self.peer}   WARNING: PVP ROOM join failed ({e!r})")
        _bsr0["solo"] = solo.solo_for(getattr(self, "sector_zone", None),
                                      self.sector[0] if self.sector else None)
        if _bsr0["solo"]:
            log(f"{self.peer}   SOLO AREA: selector {_bsr0['solo']['area'][0]} tile "
                f"{_bsr0['solo']['area'][1]} -- one NPC ally at a time (Assault, "
                f"Mechanic, Missiler), {solo.ON_FIELD} enemies on the field, win at "
                f"{solo.SOLO_KILLS} kills, lose at {solo.SOLO_ALLY_LOSSES} allies lost "
                f"or the pilot's death")
        if progress.PROGRESS_ADVANCE:
            _pc = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
            _fr = progress.progress_frontier(_pc) if _pc else []
            log(f"{self.peer}   progression armed for the return: frontier = "
                + (" | ".join(m["title"] for m in _fr) if _fr else "(nothing)"))
        # TRAINING (training.py): note a training sortie; FMO_TRAINING_KIND=1
        # marks its block kind 1 so the Training Result window opens after it
        body = training.on_sortie_granted(self, q, body, R13A_BLOCK)
        outs = [packet.build(MSG_SORTIE_REPLY, body, self.reply_seq(), p["conn"])]
        # PLATOON: the B.G.Bonus debit and the group battle record. Never
        # costs the pilot the sortie it was just granted.
        try:
            outs += self.platoon_sortie_record(q, _bsr0["mapno"], p["conn"])
        except Exception as e:
            log(f"{self.peer}   WARNING: platoon record failed ({e!r}); the "
                f"sortie goes ahead without it")
        if pushes.SORTIE_COST > 0:
            _fee = self.fee_push(p["conn"], pushes.SORTIE_COST)
            if _fee:
                outs.append(_fee)
        return outs

    def on_sortie_go(self, p):
        """0x014D -- the auto-sortie GO poll (state 1 of 0x6117BEF0 sends it
        once the countdown our 0x014E started has run). NEVER silent: an
        unanswered 0x014D parks state 2 of 0x6117BEF0 forever, the 0x01AB
        shape. Message 1 = GO (0x6117C0F3 enters scene 4 from the latched
        lobby fields); any other id = 8:29 'Battle map login failed' and a
        clean reset to [lobby+0x24]=2."""
        _arena = getattr(self, "arena_sortie", None)
        if _arena:
            # the Coliseum's 0x014E (coliseum.py): this pilot's arena match
            return self.arena_sortie_go(p, _arena)
        mn, src = sortiepush.sortie_push_mapno()
        # GO is granted ONLY when a 0x014E actually went out this session AND
        # the map still resolves -- both, so a stale latch or a knob cleared
        # mid-session refuses cleanly instead of entering scene 4 blind.
        sent = getattr(self, "sortie_push_done", False) and mn is not None
        log(f"{self.peer}   0x{sortiepush.MSG_SORTIE_GO:04X} = SORTIE GO "
            f"({len(p['payload'])}B): the countdown machine 0x6117BEF0 "
            f"fired -- its state 2 now polls seq 0x{p['seq']:X} for "
            f"message 1."
            + ("" if sent else "  WARNING: REFUSING: "
               + ("no 0x014E was pushed on this session -- the latch was set "
                  "by something we did not serve"
                  if not getattr(self, "sortie_push_done", False)
                  else f"the push went out but the map no longer resolves "
                       f"({src})")))
        if not sent:
            log(f"{self.peer}   -> message 2 (code {charselect.FAIL_CODE}): the client "
                f"shows 8:29 'Battle map login failed' and resets cleanly. "
                f"Granting GO for fields we never served would enter scene 4 "
                f"blind.")
            return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), charselect.FAIL_CODE)]
        log(f"{self.peer}   -> message 1 = GO: 0x6117C0F3 stores "
            f"time%1e6 -> lobby+0x4F02 (the battle seed), id -> +0x4F08, "
            f"calls 0x6117AC40(1,0) and 0x61006270(seed, lobby+0x4F22) -- "
            f"SCENE 4 on the next dispatcher tick, registering type-1 id "
            f"{mn} ({src}).")
        log(f"{self.peer}   WARNING: SCENE 4 HAS NEVER RUN AGAINST THIS SERVER. "
            f"Bar: fmocrash.py --live shows a type-1 row with id {mn} and "
            f"its file's size -- not a screenshot. Expect the battle UDP "
            f"manager (key %xbattle) to dial the endpoint we served; whether "
            f"our world responder answers that hello is UNKNOWN.")
        return [packet.build(handshake.MSG_SESSION_START, b"", self.reply_seq(), p["conn"])]

    def on_battle_withdraw(self, p):
        """0x013D -- the battle WITHDRAW/retreat request. See MSG_BATTLE_WITHDRAW.

        The kycli_lobmain state machine 0x61176630 (lobby tick state 5) sends it
        and polls its own sequence; state 1 (0x611766DC) reads the reply's
        message id and treats id==1 as success and anything else as a graceful
        error dialog. NEVER silent by default: an unanswered 0x013D parks the
        poller in state 1 forever (the 0x01AB/0x0159 hang seen live).

        Success (message 1) hands control back to the client's OWN completion
        arm, which -- unless [lobby+0x4F06] is the 0xFFFE sentinel -- sends a
        follow-up 0x0150 (word 0xFFFD) that the MSG_0150_REQ handler already
        answers with a 0x0153, dropping the player out of the battle onto the
        granted lobby map. The reply body is never read, so an empty message 1
        on the request's own seq is the whole contract."""
        reason = struct.unpack_from("<I", p["payload"], 0)[0] \
            if len(p["payload"]) >= 4 else 0
        log(f"{self.peer}   0x{withdraw.MSG_BATTLE_WITHDRAW:04X} = BATTLE WITHDRAW "
            f"({len(p['payload'])}B): reason byte {reason} at body+0x00 "
            f"(from lobby+0x2F; the withdraw-menu code, variable 0/1/4/runtime "
            f"-- we do not act on it). The machine 0x61176630 is in state 1 "
            f"polling seq 0x{p['seq']:08X} for a reply whose message id is 1.")
        if withdraw.ANSWER_013D == "0":
            log(f"{self.peer}   WARNING: FMO_ANSWER_013D=0: staying silent. "
                f"0x61199E30 keeps returning <= 0, the machine never leaves "
                f"state 1, and the client hangs on the withdraw -- this is the "
                f"2026-09-04 endless-load, on purpose.")
            return []
        if withdraw.ANSWER_013D == "fail":
            log(f"{self.peer}   -> message 2 (FMO_ANSWER_013D=fail): a WRONG id, "
                f"so state 1's 0x61176758 arm tears down [lobby+0x1CC], sets "
                f"[lobby+0x20]=5 and raises the error dialog (0x6116CF50, code "
                f"0xC008001E). A graceful refusal, not a hang -- use it to see "
                f"what the battle screen does with a rejected withdraw.")
            return [packet.build(2, b"", self.reply_seq(), p["conn"])]
        log(f"{self.peer}   -> message 1 = WITHDRAW OK: state 1 reads the id, "
            f"and unless [lobby+0x4F06]==0xFFFE the completion arm sends a "
            f"follow-up 0x0150 (word 0xFFFD) that our MSG_0150_REQ handler "
            f"answers with a 0x0153 -- the client leaves the battle for the "
            f"granted lobby map. Empty body: 0x611766DC reads only the id.")
        outs = [packet.build(handshake.MSG_SESSION_START, b"", self.reply_seq(), p["conn"])]
        if self.is_spectating():
            # a Coliseum spectator leaving: no battle of its own, no pay
            coliseum.coliseum().leave_spectate(self.account)
            log(f"{self.peer}   COLISEUM: a spectator left the arena battle -- no pay")
            return outs
        # a matching room counts this pilot out (pvproom.py)
        pvproom.withdrew(self.battle_key())
        # a withdraw is never a win: sortie pay only, whatever was destroyed
        push = self.battle_result_push(p["conn"], "the battle withdraw", won=False)
        if push is not None:
            outs.append(push)
        return outs


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    addressing, charselect, charstore, groupchannel, handshake, missionblock, missionlist,
    packet, progress, pushes, referee, rooms, sortiepush, status, warmap, withdraw, zoneentry,
)
from . import battlegroups, charlist, popnation, warstate  # noqa: E402  (the join-in / platoon checks)
from . import penalty  # noqa: E402  (the penalty refusal hook)
from . import solo  # noqa: E402  (the solo area)
from . import coliseum  # noqa: E402  (Coliseum spectators)
from . import pvproom  # noqa: E402  (Frontline matching rooms)
from . import training  # noqa: E402  (the training sortie and its ranking)
