"""run(): the listeners, the startup log and the background threads."""
import socket
import threading
from .deps import fmodb, fmoworld
from .wirelog import log
from . import wirelog


def run():
    if udpconfig.UDP_ENABLE and fmoworld:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        u.bind(("0.0.0.0", wirelog.PORT))
        threading.Thread(target=worldchannel.serve_udp, args=(u,), daemon=True).start()
    elif udpconfig.UDP_ENABLE:
        log("WARNING: fmoworld.py did not import -- the UDP world channel is NOT "
            "served, and the game scene will time out at 60s (FMO-13111)")
    devtool.devtool_start()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", wirelog.PORT))
    s.listen(8)
    # WARNING: SAY WHICH BUILD THIS IS, UNAMBIGUOUSLY. The first version of this line
    # printed "(TCP only; the UDP channel is not served)" when fmoworld was
    # missing -- BYTE-IDENTICAL to what the pre-UDP build printed, so the log
    # could not tell "the new code, degraded" from "the old code". A stack
    # restart on 2026-08-19 ran the 08-18 image for two minutes and the log line
    # was indistinguishable; only `docker run ... ls /app/fmoworld.py` settled
    # it. A build stamp is the cheapest possible fix and the log is the only
    # place anyone looks. (a rule learned the hard way, twice.)
    if charstore.CHAR_STORE:
        log(f"character store ON: {charstore.CHAR_STORE} -- 0x012F is served from it, "
            f"and 0x013E/0x0177/0x01AA/0x013F write to it")
        if charlist.LIST_COUNT:
            log(f"WARNING: FMO_LIST_COUNT={charlist.LIST_COUNT} IS BEING IGNORED. The store "
                f"takes precedence in the 0x012E handler, so the synthetic "
                f"{charlist.LIST_NAME!r} entry is NOT served while FMO_CHAR_STORE is "
                f"set. To go back to the synthetic list, clear FMO_CHAR_STORE "
                f"-- setting FMO_LIST_COUNT alone does nothing.")
    else:
        log(f"WARNING: character store OFF (FMO_CHAR_STORE empty): every character "
            f"the client creates is acknowledged and DISCARDED, and 0x012F "
            f"serves {charlist.LIST_COUNT} synthetic entr(y/ies)")
    # THE DATABASE: apply CrystalFront's migrations (the fmo_* tables) before
    # the first login, then let the character store make its first use (the
    # one-shot JSON import, and the log line saying where the pilots are),
    # load the sector-win ledger a restart must not lose, and load the war
    # state (its one-shot fmowar.json import), which the City Control board
    # reads from the table.
    if fmodb is None:
        log("WARNING: DATABASE OFF: polcore (OpenLobby) is not importable -- the "
            "pilots stay in the JSON character store and the sector-win ledger "
            "is memory only")
    else:
        db_ok = True
        try:
            fmodb.ready()
            log("database: CrystalFront's migrations applied (the fmo_* tables)")
        except fmodb.ERRORS as e:
            db_ok = False
            log(f"WARNING: database unusable at start ({e!r}) -- every store "
                f"call retries it; until it answers, pilots read as empty and "
                f"writes are refused (logged)")
        charstore.use_db()
        sectorwins._sw_ensure_loaded()
        if db_ok:
            warstate.load_at_start()
        else:
            # a War made now would start empty and later write that over
            # the stored state
            log("WARNING: war state not loaded at start (the database is "
                "unusable); it loads on first use")
    log(f"listening on {wirelog.PORT} -- FMO world door [build {room.BUILD}] "
        + (f"TCP + UDP world channel (hid={udpconfig.UDP_HID}, "
           f"endpoint {addressing.BATTLE_HOST}:{addressing.BATTLE_PORT}, key {room._udp_key_hint()})"
           if udpconfig.UDP_ENABLE and fmoworld
           else "TCP ONLY -- no UDP world channel" +
                (" (FMO_UDP=0)" if not udpconfig.UDP_ENABLE else
                 " (fmoworld.py MISSING FROM THIS IMAGE)")))
    # WARNING: THE RUN'S CONFIGURATION BELONGS IN THE RUN'S LOG. Twice now a live
    # measurement has been read against the wrong assumption about what was
    # being served -- the PilotPos that never appeared in a log line, and the
    # nation the store had been carrying for a day. The POP mutates client
    # state, so its absence has to be as visible as its presence.
    if popsweep.POP:
        log(f"\U0001f534 POP PROBE ARMED: cmd {fmoworld.CMD_POP} will create "
            f"UnitID={popsweep.POP[0]:#010x} UnitType={popsweep.POP[1]} at {popsweep.POP_POS} "
            f"({'main arm' if popsweep.POP[1] in fmoworld.POP_UNITTYPES_MAIN else 'its own arm'} "
            f"at 0x611EB2C3), names {popself.POP_NAME1!r}/{popself.POP_NAME2!r}, after "
            f"{popself.POP_AFTER} exchanges, ONCE per channel")
        log(f"   the 0x0153 setup block will carry unit ids "
            f"{[f'{i:#010x}' for i in room.SETUP_UNIT_IDS]} -- 0x61003120 spawns "
            f"only an id present in BOTH the block and the entity map")
        log(f"   read the result with the crash instrument (--live): SUCCESS IS "
            f"THE ENTITY MAP GOING TO 1 KEY. A black screen with 1 key is a "
            f"different (and better) finding than a black screen with 0.")
    else:
        log("POP probe OFF (FMO_UDP_POP unset): the 0x0153 setup block carries "
            "eight ZERO unit ids, byte-identical to every measurement before "
            "2026-08-21, and the client's entity map stays empty.")
    if room.ROOM:
        log(f"VERIFIED: ROOM RELAY ON (FMO_UDP_ROOM=1): every world channel is "
            f"popped into every other one in the same MapNo as UnitType "
            f"{room.ROOM_TYPE}, aliases from {room.ROOM_ALIAS_BASE:#x}, movement relayed "
            f"on a per-alias datagram stream at most every "
            f"{room.ROOM_MIN_INTERVAL}s, chat relayed to the room"
            + ("" if room.ROOM_SAME_MAP else
               ", WARNING: MapNo IGNORED (FMO_UDP_ROOM_SAME_MAP=0)"))
        log(f"   with ONE client connected this changes nothing -- there is "
            f"nobody to relay to, which is why it is on by default. A second "
            f"client is the whole test.")
        log(f"   WARNING: A PLAYER WHO LEAVES STAYS ON SCREEN. SE's \"RecvDepop(UnitID"
            f"=%x FromID=%u Status=%u)\" is cmd 8 {{u32 UnitID, FromID, "
            f"Status}} (decoded 2026-08-26) -- and the LOBBY peer class "
            f"ignores it (0x611EBC44 -> default arm); only the battle class "
            f"(0x611D4750) handles it. No other wire removal exists. "
            + (f"FMO_UDP_ROOM_DEPOP=1: sending it anyway on 0x0152 / map "
               f"change / {room.ROOM_TTL:g}s silence, Status={room.ROOM_DEPOP_STATUS}, "
               f"PREDICTED INERT -- a measurement, not a fix."
               if room.ROOM_DEPOP else
               f"FMO_UDP_ROOM_DEPOP=0: on 0x0152 / map change / "
               f"{room.ROOM_TTL:g}s silence the relay stops and the log says so; "
               f"nothing is sent."))
    else:
        log("ROOM RELAY OFF (FMO_UDP_ROOM=0): each world channel is served in "
            "isolation, chat echoes only to its sender, and a second client is "
            "invisible to the first.")
    # The Move menu is answered, and BOTH of its knobs are probes -- so say so
    # here, where an operator reading the log can see what is armed without
    # inspecting the container. A probe that does not announce itself is one
    # you cannot tell apart from the default afterwards.
    if not move.ANSWER_MOVE:
        log("MOVE menu NOT answered (FMO_ANSWER_MOVE=0): 0x016E will get "
            "silence, and FMO's UI hangs on an unanswered menu action with no "
            "way back to the Viewer. That is the 0x01AB failure, on purpose.")
    else:
        _rows = move.parse_move_list(move.MOVE_LIST)
        log(f"MOVE menu answered: 0x016E -> 0x016F ({move.REPLY_016F_LEN}B), "
            f"count={len(_rows)}"
            + (f" {_rows} -- a PROBE; check on screen that the ids land under "
               f"'Lobby ID'/'Room ID' and the counts under 'People', which is "
               f"the one inference in the decode"
               if _rows else
               " -- no lobby/room registry exists, so the client will show "
               "\"There is nobody in that lobby right now. Move there "
               "anyway?\" rather than a list. That is a screen, not an error."))
        _brows = move.parse_move_list(move.MOVE_LIST_BRIEFING)
        _bdesc = (str(_brows) if _brows
                  else "(empty -> SE's own nobody-in-that-briefing-room box)")
        if move.MOVE_KIND_BRIEFING >= 0:
            _kdesc = f"{move.MOVE_KIND_BRIEFING} (FMO_MOVE_KIND_BRIEFING)"
        else:
            _kdesc = f"{zoneentry.FIELD_18} (FMO_MOVE_KIND_BRIEFING=-1: unchanged)"
        log(f"   BRIEFING ROOM (0x016E category 2): FMO_MOVE_LIST_BRIEFING "
            f"count={len(_brows)} {_bdesc}; a category-2 move is granted "
            f"with 0x0153 +0x18 zone kind {_kdesc}")
        if move.MOVE_MAPNO is None:
            log(f"   FMO_MOVE_MAPNO unset: a move grants THE MapNo THE CLIENT "
                f"PICKED (0x016D +0x04 is the list entry's own id, live "
                f"2026-08-23), falling back to {zoneentry.MAPNO} only for a pick that is "
                f"not a map on disk. Set the knob to force one destination "
                f"regardless of the pick.")
        else:
            log(f"   WARNING: MOVE MAPNO PROBE ARMED: a move grants MapNo "
                f"{move.MOVE_MAPNO} instead of {zoneentry.MAPNO}, so Move is a real zone "
                f"change. {move.MOVE_MAPNO} is on disk; a MapNo that is not "
                f"allocates zero bytes and crashes the client at 0x611250A2.")
    # WARNING: These banners used to sit inside the `else` of `if not ANSWER_MOVE`
    # above (an indentation slip), so FMO_ANSWER_MOVE=0 silently hid the
    # resume / mark / zone / sortie / pin / lobapi-mark announcements -- the
    # run's configuration was missing from the run's log exactly when a
    # reproduction knob was armed. Function level since 2026-09-05.
    if status.STATUS_W7604 or status.STATUS_W7608 or status.STATUS_WFD4:
        _q = (status.STATUS_WFD4 // 10000) & 0xFFFF
        _known = _q < resume.RESUME_RES_COUNT
        log(f"WARNING: BATTLE-MAP RESUME ARMED: 0x014A +0x684 = {status.STATUS_W7604} "
            f"(non-zero DIVERTS the client off world entry into the "
            f"link-death resume branch at 0x6117B8D0 and sets "
            f"BMResumedFromLD=1), +0x688 = {status.STATUS_W7608}, +0x70C = "
            f"{status.STATUS_WFD4}. The client will send 0x0137 carrying "
            f"{status.STATUS_W7604 % 1000000} (= value % 1000000) and load "
            f"resource {resume.RESUME_RES_BASE + _q} = "
            f"{resume.resume_resource_path(_q)} (selector {_q} of "
            f"0..{resume.RESUME_RES_COUNT - 1})"
            + ("" if _known else
               f" WARNING: WHICH IS OUTSIDE THE {resume.RESUME_RES_COUNT} FILES THAT "
               f"EXIST (D59..D99) -- a missing resource resolves to size 0")
            + ". WARNING: EXPECT IT TO HANG at 'connecting to the world server': "
            f"the resume flow is not known to finish. The field is "
            f"ONE-SHOT, so clearing the knob and relaunching recovers.")
    if status.STATUS_MARK:
        _n = status.REPLY_014A_LEN // 4
        log(f"PROBE: STATUS BLOCK MARKED (FMO_STATUS_MARK={status.STATUS_MARK}): the "
            f"0x014A payload is dword i = {status.STATUS_MARK} + i, {_n} dwords, "
            f"values {status.STATUS_MARK}..{status.STATUS_MARK + _n - 1}. A number in "
            f"that range ON ANY SCREEN is its own offset: dword = "
            f"N - {status.STATUS_MARK}, payload byte = 4 x that. Read Job "
            f"Status and Profile -- WARNING: NOT Change Area's "
            f"current-area line, which is MapKind read out of the D83 "
            f"zone table (0x611D8EB5) and is not in this block at "
            f"all. SKIPPED "
            f"(left zero): the resource ids + owned-items bitset "
            f"0x03C..0x{status.S14A_OWNED + status.S14A_OWNED_LEN:X} (a set bit claims a "
            f"part) and the 0x{status.S14A_BLOCK3:X}.. tail (echoed back in "
            f"0x0170); nation/sex/rank/tier are forced to 0 so the probe "
            f"cannot change the scene script it is reading. WARNING: Clear it "
            f"when the reading is taken.")
    if zonecontrol.ZONE_CONTROL:
        _zr = zonecontrol.parse_zone_control(zonecontrol.ZONE_CONTROL)
        log(f"PROBE: ZONE CONTROL ARMED (FMO_ZONE_CONTROL): a "
            f"0x{zonecontrol.MSG_ZONE_CONTROL:04X} push of {zonecontrol.ZONE_CONTROL_LEN}B rides "
            f"every 0x01AC poll, carrying {len(_zr)} row(s) "
            + ", ".join(f"{z}={o}/{u}" for z, o, u in _zr)
            + f" into lobby+0x7724 -- the table Change Area's "
            f"selectability predicate 0x611794A0 walks, which is zero "
            f"today because nothing has ever sent this message. WARNING: "
            f"Gate (A) is client data: of the kind-5 rows only "
            f"{list(zonecontrol.ZONE_LIVE_WARZONES)} (FZ-06/FZ-10/FZ-14) have a "
            f"non-zero flag in D83, so the other seventeen refuse no "
            f"matter what is served here. WARNING: The tier operand of the "
            f"0x613966E4 matrix comes from lobby+0x8B5 and payload+0x6AC, "
            f"both served ZERO and both UNMEASURED. Oracle: FZ-06 and "
            f"FZ-14 stop saying 'That area cannot be selected right now.' "
            f"and FZ-10 becomes 'That is the area you are in.'; if it "
            f"opens, watch for an inbound 0x0170.")
        _bwarn = zonecontrol.zone_control_nation_warning()
        if _bwarn:
            log(_bwarn)
    if sortiepush.sortie_push_armed():
        _mn, _src = sortiepush.sortie_push_mapno()
        if _mn is None:
            log(f"WARNING: AUTO-SORTIE ARMED BUT REFUSED (FMO_SORTIE_PUSH): "
                f"{_src}. Nothing will be pushed. Set an on-disk type-1 "
                f"id (fmoare.py --zone <MapKind> lists them; FZ-10 sector "
                f"04 'Oak Hills City' = map 418, corrected 2026-09-04).")
        else:
            _ep = "off" if not sortie.SORTIE_ENDPOINT else (
                f"{sortie.SORTIE_HOST or addressing.BATTLE_HOST}:{sortie.SORTIE_PORT or addressing.BATTLE_PORT}")
            log(f"PROBE: AUTO-SORTIE ARMED (FMO_SORTIE_PUSH={sortiepush.SORTIE_PUSH_MAPNO}): "
                f"one 0x{sortiepush.MSG_SORTIE_PUSH:04X} push of {sortiepush.REPLY_014E_LEN}B "
                f"rides the first keepalive ~{sortiepush.SORTIE_PUSH_DELAY:g}s after "
                f"a WARM Move grant, carrying type-1 MapNo {_mn} to "
                f"lobby+0x5C7E, seed/countdown {sortiepush.SORTIE_PUSH_TIME}s to "
                f"+0x4F1E, endpoint {_ep} to +0x4F22, destination "
                f"{sortiepush.SORTIE_PUSH_DEST or f'Map {_mn}'!r}. The client then "
                f"asks 0x{sortiepush.MSG_SORTIE_GO:04X} and we answer message 1 = GO "
                f"-> SCENE 4. WARNING: SCENE 4 HAS NEVER RUN AGAINST THIS "
                f"SERVER. WARNING: THREE things gate it and all are UNTESTED: "
                f"(1) the countdown tick only starts in scene 6 -- the "
                f"player MUST Move (Change Area) at least once, a cold "
                f"login sits in scene 7 and nothing fires; (2) the arm's "
                f"gate wants [lobby+0x24] not in {{0,3,9}}; (3) a second "
                f"Move after the push zeroes the block under the latch. "
                f"Oracle: 8:3 countdown banner, then fmocrash.py --live "
                f"shows a type-1 row id {_mn} -- NOT a screenshot.")
    if identity.ACCOUNT_PIN:
        for _ip, _acct in sorted(identity.ACCOUNT_PIN.items()):
            log(f"KEY: ACCOUNT PIN: {_ip} is {_acct}, overriding the "
                f"freshest-POL-session lookup for that address. Set this "
                f"only for a box that runs more than one POL account.")
    if lobapi.MARK_LOBAPI:
        for _m, _b in sorted(lobapi.MARK_LOBAPI.items()):
            _rep, _need = lobapi.LOBAPI[_m]
            log(f"PROBE: LOBAPI MARK ARMED: 0x{_m:04X} -> 0x{_rep:04X} answers "
                f"with {_need}B of MARKERS, not zeros -- dword i = {_b} + i "
                f"({_need // 4} dwords, so {_b}..{_b + _need // 4 - 1}). "
                f"Any number in that range on a screen IS its own offset: "
                f"dword = N - {_b}, payload byte = 4 x that. WARNING: PROBE ONLY: "
                f"to the client these bytes are counts, indices and set "
                f"bits. Clear FMO_LOBAPI_MARK when the reading is taken.")
    # Report live FMO world sessions to the deploy gate so a push does not
    # bounce fmo mid-sortie. Each session is one thread; name it so the count
    # is a thread-name scan (naming a thread changes no behaviour).
    try:
        import live_sessions
        live_sessions.start_heartbeat(
            "fmo", lambda: live_sessions.thread_count("fmo-session-"))
    except Exception as _e:
        log(f"live-session heartbeat not started: {_e!r}")
    while True:
        conn, addr = s.accept()
        threading.Thread(target=tcpserver.serve_client, args=(conn, addr),
                         name="fmo-session-%s-%d" % addr,
                         daemon=True).start()


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    addressing, charlist, charstore, devtool, identity, lobapi, move, popself, popsweep, resume,
    room, sectorwins, sortie, sortiepush, status, tcpserver, udpconfig, warstate,
    worldchannel, zonecontrol, zoneentry,
)
