"""The battle referee: battle state, shots against the dummy, objectives, end triggers, kill
credit."""
import struct
import time
from .deps import fmoworld
from .wirelog import log


#: THE BATTLE STATE, per client HOST -- the bridge between the UDP battle
#: channel (which sees the eject, the hits, the objective traffic) and the TCP
#: session (which owns 0x014C, the only message that ENDS a battle). Keyed the
#: way everything else here is keyed, by the client's IP: WORLD_MAPS, the
#: session binding, the relay room. A TCP Session reads it on its keepalive.
BATTLE_STATE = {}


def referee_shot(chan, addr):
    """One qualifying weapon-fire (cmd 128 kind 3) against the dummy enemy:
    judge range/cone, count the hit, and on the threshold destroy the enemy and
    complete the destroy objective. Returns HUD banner strings to send.

    Server-refereed because the client sends no hit packet -- see FMO_BATTLE_REFEREE.
    """
    if not battlepop.REFEREE or not chan.dummy_id or chan.dummy_kill_sent:
        return []
    if chan.dummy_pos is None or getattr(chan, "pos", None) is None:
        return []
    dist = battlepop._xz_dist(chan.pos, chan.dummy_pos)
    if dist > battlepop.REFEREE["range"]:
        # Diagnostic: a fire that did not count, and why -- so a live test that
        # fails to kill shows the geometry rather than silence. Rate-limited.
        chan.referee_miss = getattr(chan, "referee_miss", 0) + 1
        if chan.referee_miss <= 6:
            log(f"[udp {addr[0]}:{addr[1]}]   REFEREE: shot NOT counted -- "
                f"player {tuple(round(v,1) for v in chan.pos[:3])} is "
                f"{dist:.1f} from enemy {chan.dummy_pos} (> range "
                f"{battlepop.REFEREE['range']:.0f}). Raise FMO_BATTLE_REFEREE range if the "
                f"coordinate frames differ.")
        return []
    if not battlepop._within_cone(chan.pos, getattr(chan, "rot", 0.0) or 0.0,
                                  chan.dummy_pos, battlepop.REFEREE["cone"]):
        return []
    chan.referee_hits += 1
    n, need = chan.referee_hits, battlepop.REFEREE["hits"]
    log(f"[udp {addr[0]}:{addr[1]}]   REFEREE: shot {n}/{need} on enemy "
        f"{chan.dummy_id:#x} (range {dist:.1f} <= {battlepop.REFEREE['range']:.0f})")
    if n < need:
        return [f"Enemy hit ({n}/{need})"]
    # THE KILL: complete the destroy objective (so FMO_BATTLE_END=objective wins)
    # and remove the enemy from the field.
    chan.dummy_kill_sent = True
    st = battle_state(bkey(addr[0]))
    st.setdefault("kills", []).append((chan.dummy_id, time.time()))
    try:
        chan.pending.append(fmoworld.record_depop(
            chan.dummy_id, status=fmoworld.DEPOP_REMOVE))
    except ValueError as e:
        log(f"[udp {addr[0]}:{addr[1]}]   REFEREE: DEPOP refused: {e}")
    log(f"[udp {addr[0]}:{addr[1]}]   REFEREE: enemy {chan.dummy_id:#x} "
        f"DESTROYED after {need} shots -- objective marked done, cmd 8 status "
        f"{fmoworld.DEPOP_REMOVE} sent. FMO_BATTLE_END={'objective in ' if 'objective' in battleend.BATTLE_END else 'MISSING objective; '}"
        f"the battle ends as a WIN on the next keepalive if 'objective' is armed.")
    banners = ["Enemy destroyed!"]
    banners += objective_tick(st, chan, time.time())
    return banners


def chan_bkey(c):
    """WHOSE battle a channel is: its bound account, else its host.

    WARNING: BATTLE_STATE / the squad / kill credit were keyed by the client's IP.
    Live 2026-09-27, PC and Deck on one router in one battle: BOTH were told
    they OWN the enemy squad (two AI brains per enemy), kills were credited to
    the address, the Deck took the 'all enemies destroyed' WIN for the PC's
    kills, and the shared 'ended' flag then stranded the PC in the battle with
    no way to end it. A bound channel names its player (claim_world_account),
    so that is the key; the address only for an unbound channel."""
    return (getattr(c, "account", None) or c.addr[0]) if c is not None else None


def bkey(ip):
    """chan_bkey for the channel being served (serve_udp's context), when it is
    `ip`'s; else the IP. Use inside a datagram handler, where `addr[0]` is all
    the code has."""
    a = getattr(worldchannel._udp_ctx, "addr", None)
    if a is not None and a[0] == ip:
        c = groupchannel.WORLD_PEERS.get(a)
        if c is not None and getattr(c, "account", None):
            return c.account
    return ip


def battle_state(host, reset=False):
    st = BATTLE_STATE.get(host)
    if st is None or reset:
        st = BATTLE_STATE[host] = {
            "granted_at": time.time(),   # the sortie grant (0x013A)
            "escaped": None,             # (reason code, name, when) from cmd 139/137
            "start_sent": False,         # BM cmd 138 queued on the battle channel
            "objective_sent": False,     # the cmd-129 probe
            "clock": None,               # (client sec, usec, our time) from 0x0198
            "ended": False,              # a 0x014C went out
            "objective_done": None,      # (what, when) once FMO_OBJECTIVE is met
            "hold_since": None,          # entered the hold zone at
            "hold_said": None,           # last banner threshold announced
            "kills": [],                 # (target, when) from DIED hit records
            "enemies": set(),            # UnitIDs this server popped as enemies
            "objective_banner": False,   # the opening banner went out
        }
    return st


def objective_zone_contains(rect, pos):
    x, z, w, h = rect
    return pos is not None and x <= pos[0] <= x + w and z <= pos[2] <= z + h


def objective_tick(st, chan, now, objective=None):
    """Advance the hold/destroy objective for one host from what the battle
    channel knows. Returns a list of HUD banner strings to send (may be empty)
    and sets st['objective_done'] when met. Pure apart from `st`."""
    obj = battleend.OBJECTIVE if objective is None else objective
    banners = []
    if not obj or st.get("objective_done") or st.get("ended"):
        return banners
    kind = obj[0]
    if not st.get("objective_banner"):
        st["objective_banner"] = True
        if kind == "hold":
            banners.append(f"OBJECTIVE: hold the zone for {obj[2]}s")
        else:
            banners.append("OBJECTIVE: destroy every enemy" if obj[1] == "all"
                           else f"OBJECTIVE: destroy unit {obj[1]:#x}")
    if kind == "hold":
        inside = objective_zone_contains(obj[1], getattr(chan, "pos", None))
        if inside and st.get("hold_since") is None:
            st["hold_since"], st["hold_said"] = now, None
            banners.append("Zone entered -- hold position")
        elif not inside and st.get("hold_since") is not None:
            st["hold_since"], st["hold_said"] = None, None
            banners.append("Zone lost -- return to the zone")
        if st.get("hold_since") is not None:
            left = obj[2] - (now - st["hold_since"])
            if left <= 0:
                st["objective_done"] = ("the zone was held for %ds" % obj[2], now)
                banners.append("OBJECTIVE COMPLETE")
            else:
                for mark in (30, 10):
                    if left <= mark and (st.get("hold_said") or 99) > mark:
                        st["hold_said"] = mark
                        banners.append(f"{mark}s to hold")
                        break
    elif kind == "destroy" and obj[1] == "all":
        sq = st.get("squad")
        if sq and sq["ids"] and set(sq["ids"]) <= sq["dead"]:
            st["objective_done"] = (f"all {len(sq['ids'])} enemies were "
                                    f"destroyed", now)
            banners.append("OBJECTIVE COMPLETE")
    elif kind == "destroy":
        if any(t == obj[1] for t, _w in st.get("kills", [])):
            st["objective_done"] = (f"unit {obj[1]:#x} was destroyed", now)
            banners.append("OBJECTIVE COMPLETE")
    return banners


def hud_banner(chan, text):
    """One BM cmd 18 kind-1 record onto `chan` (0x610E6420, the HUD ticker --
    the arm the 'battle begins' banner used). ASCII only; the HUD font is."""
    try:
        chan.pending.append(fmoworld.record_chat(text, "", channel=1))
    except Exception as e:                # a banner must never cost a datagram
        log(f"[udp {chan.addr[0]}] banner not queued: {e!r}")


def pilot_death_trigger(died_at, granted_at, now, delay):
    """(why, won=False) when this pilot's own unit died in THIS sortie at
    least `delay` seconds ago, else None. Pure. See BATTLE_DEATH_END."""
    if not delay or delay <= 0 or died_at is None or granted_at is None:
        return None
    if died_at < granted_at or now - died_at < delay:
        return None
    return ("this pilot's wanzer was DESTROYED (SE's 敗北条件: "
            "自分の機体が撃破されること)", False)


def battle_end_trigger(st, triggers, now, limit_secs):
    """Which FMO_BATTLE_END trigger fires for this state at `now`, as
    (why, won) -- or None. Pure, so the selftest can drive every arm."""
    if not st or st.get("ended") or not triggers:
        return None
    if "objective" in triggers and st.get("objective_done"):
        return f"the objective ({st['objective_done'][0]})", True
    if "escape" in triggers and st.get("escaped"):
        code, name, _when = st["escaped"]
        return (f"the client's EMERGENCY ESCAPE (cmd {fmoworld.CLI_ESCAPE}, reason "
                f"0x{code:08X} = {name})",
                battleend.BATTLE_END_WON if battleend.BATTLE_END_WON_SET else False)
    if "limit" in triggers and limit_secs > 0 \
            and now - st["granted_at"] >= limit_secs:
        return (f"the mission time limit ({limit_secs}s, block +0x4C) elapsed",
                battleend.BATTLE_END_WON if battleend.BATTLE_END_WON_SET else False)
    secs = [t for t in triggers if isinstance(t, int)]
    if secs and now - st["granted_at"] >= min(secs):
        return f"the FMO_BATTLE_END timer ({min(secs)}s after the grant)", battleend.BATTLE_END_WON
    return None


def _credit_room_kill(victim, addr):
    """`victim`'s client reported its OWN unit destroyed: credit the kill to
    the hostile room-mate who shot last (battle_room_killer), under the alias
    that pilot's client knows the victim by -- which is the id in its
    battle_state "enemies", so battle_kills pays it. Once per victim alias."""
    killer = rooms.battle_room_killer(victim, time.time())
    if killer is None:
        log(f"[udp {addr[0]}:{addr[1]}]   PvP: this pilot's own unit was "
            f"DESTROYED; no hostile room-mate fired in the last "
            f"{rooms.BATTLE_KILL_WINDOW:.0f}s, so no kill is credited.")
        return None
    alias = killer.alias_for(victim.addr)
    kst = battle_state(chan_bkey(killer))
    if any(t == alias for t, _w in kst.get("kills", [])):
        return None
    kst.setdefault("kills", []).append((alias, time.time()))
    log(f"[udp {addr[0]}:{addr[1]}]   PvP: this pilot's own unit was DESTROYED "
        f"-> kill credited to {killer.addr[0]} (their unit {alias:#x} for this "
        f"pilot; they fired last). WARNING: Heuristic: the victim's client names no "
        f"shooter.")
    return killer


def _note_battle_record(chan, addr, cmd, body):
    """Inbound records on the BATTLE channel that the loop should remember,
    not just log. Silent on the lobby channel."""
    if not (chan.key and chan.key.endswith(b"battle")):
        return
    if cmd == fmoworld.CLI_HIT_TARGET:
        log(f"[udp {addr[0]}:{addr[1]}]   cmd {fmoworld.CLI_HIT_TARGET} = CLI HIT "
            f"TARGET (0x611F0CD0): the client has LOCKED a unit and is firing at it "
            f"-- the enemy IS a valid, targetable battle-map unit of a different "
            f"side. {len(body)}B body. WARNING: The server does not yet run HP off "
            f"this; decode this record (target UnitID rides the record header) and "
            f"build the cmd-0x64 receiver + cmd-128 damage relay + cmd-8 kill to "
            f"make the player's own shots destroy it.")
        return
    if (cmd in (23, 24, 30) and battlepop.BATTLE_DUMMY_AI and getattr(chan, "dummy_id", None)
            and struct.pack("<I", chan.dummy_id) in bytes(body)
            and not getattr(chan, "ai_seen", None)):
        # VERIFIED: the CLIENT is running the enemy (FMO_BATTLE_DUMMY_AI): its
        # movement batch (24/23) or a fire (30) names the enemy's id
        chan.ai_seen = cmd
        log(f"[udp {addr[0]}:{addr[1]}]   VERIFIED: ENEMY AI RUNNING: cmd {cmd} "
            f"({'movement' if cmd != 30 else 'FIRE'}) carries the enemy "
            f"{chan.dummy_id:#x} -- the client's brain "
            f"{battlepop.BATTLE_DUMMY_AI} drives it. Logged "
            f"once per battle.")
    if cmd in (fmoworld.CLI_ESCAPE, fmoworld.CLI_ESCAPE_B):
        esc = fmoworld.parse_escape(body)
        st = battle_state(bkey(addr[0]))
        if esc and not st["escaped"]:
            st["escaped"] = (esc[0], esc[1], time.time())
        log(f"[udp {addr[0]}:{addr[1]}]   cmd {cmd} = CLI -> BM EMERGENCY ESCAPE"
            + (f", reason 0x{esc[0]:08X} ({esc[1]})" if esc else " (short body)")
            + (f". FMO_BATTLE_END has 'escape': the next keepalive on this host's "
               f"TCP session ends the battle with a 0x{battleend.MSG_BATTLE_END:04X}."
               if "escape" in battleend.BATTLE_END else
               ". Not a trigger (FMO_BATTLE_END lacks 'escape'); the client will "
               "withdraw on its own 0x013D as before."))
    elif cmd == fmoworld.CLI_FIELD_SYNC and len(body) >= 4:
        kind, off, ln = body[0], body[1], struct.unpack_from("<H", body, 2)[0]
        if chan.cmd_seen.get(cmd, 0) <= 3:
            log(f"[udp {addr[0]}:{addr[1]}]   cmd 128 = FIELD SYNC kind {kind} "
                f"offset 0x{off:02X} len {ln} (0x61053010; kind 3 = the unit's "
                f"per-part damage table, +0x04.. body, +0x14.. weapons)")
        # VERIFIED: kind 3 offset >=0x14 = a WEAPON-slot wear sync = the player FIRED
        # (the only server-visible fire event). Feed the shoot-to-kill referee.
        if kind == 3 and off >= 0x14:
            chan.last_fire = time.time()          # battle_room_killer
        if battlepop.REFEREE and kind == 3 and off >= 0x14:
            for _b in referee_shot(chan, addr):
                hud_banner(chan, _b)
        if battleend.BATTLE_HIT == "relay":
            _relay_battle_record(chan, addr, cmd, body, alias_stream=True)
    elif cmd in (fmoworld.CMD_BM_HIT, fmoworld.CMD_BM_HIT_BATCH):
        hits = ([fmoworld.parse_hit(body)] if cmd == fmoworld.CMD_BM_HIT
                else fmoworld.parse_hit_batch(body))
        hits = [h for h in hits if h]
        st = battle_state(bkey(addr[0]))
        _sq = st.get("squad")
        for h in hits:
            if h["died"]:
                if _sq and h["target"] in _sq["ids"]:
                    if _sq["owner"] == bkey(addr[0]):
                        _who = squad.squad_credit_kill(_sq, h["target"], chan)
                        if _who is None and h["target"] in _sq["dead"] and any(
                                t == h["target"] for t, _s in _sq.get("friendly_fire", [])):
                            log(f"[udp {addr[0]}:{addr[1]}]   SQUAD: enemy "
                                f"{h['target']:#x} DESTROYED BY ITS OWN SIDE "
                                f"(friendly fire) -- no kill credited; "
                                f"{len(_sq['dead'])}/{len(_sq['ids'])} down")
                        if _who:
                            log(f"[udp {addr[0]}:{addr[1]}]   SQUAD: enemy "
                                f"{h['target']:#x} DESTROYED, kill credited to "
                                f"{_who} (fired last); "
                                f"{len(_sq['dead'])}/{len(_sq['ids'])} down")
                    continue
                st["kills"].append((h["target"], time.time()))
                if h["target"] == chan.self_unit():
                    _credit_room_kill(chan, addr)
                    _dead = identity.account_for(addr[0])   # THIS channel's pilot
                    if _dead not in battleend.PILOT_DEATHS or \
                            time.time() - battleend.PILOT_DEATHS[_dead] > 60:
                        log(f"[udp {addr[0]}:{addr[1]}]   DEFEAT: {_dead}'s "
                            f"own wanzer was destroyed -- their battle ends as "
                            f"a LOSS {battleend.BATTLE_DEATH_END:g}s from now, on the "
                            f"next keepalive (FMO_BATTLE_DEATH_END); "
                            f"room-mates fight on")
                    battleend.PILOT_DEATHS[_dead] = time.time()
        log(f"[udp {addr[0]}:{addr[1]}]   cmd {cmd} = HIT"
            + (" BATCH" if cmd == fmoworld.CMD_BM_HIT_BATCH else "")
            + f": " + ("; ".join(f"target {h['target']:#x} part {h['part']} value "
                                f"{h['value']} flags 0x{h['flags']:X}"
                                + (" DIED" if h["died"] else "") for h in hits)
                       or "(short record)")
            + (". FMO_BATTLE_HIT=relay: relayed to the room (a client applies a "
               "received record only to its OWN unit)."
               if battleend.BATTLE_HIT == "relay" else
               ". Not relayed (FMO_BATTLE_HIT=off): the shooter already applied "
               "it locally; nobody else sees it."))
        if battleend.BATTLE_HIT == "relay":
            _relay_battle_record(chan, addr, cmd, body, alias_stream=False)
        for b in objective_tick(st, chan, time.time()):
            hud_banner(chan, b)
    elif cmd == fmoworld.CMD_MOVE:
        st = BATTLE_STATE.get(bkey(addr[0]))
        if st is not None and battleend.OBJECTIVE:
            for b in objective_tick(st, chan, time.time()):
                log(f"[udp {addr[0]}:{addr[1]}]   objective: {b}")
                hud_banner(chan, b)


def _relay_battle_record(chan, addr, cmd, body, alias_stream):
    """Send a client's battle record back to it and on to its room-mates.
    Hit records ride each listener's OWN stream (the target is named inside
    the record); a field sync must ride the SENDER's alias stream on the other
    clients, because the receive arm applies it to the unit the datagram
    names -- which on the self stream is the listener itself."""
    rec = fmoworld.record(cmd, body)
    chan.pending.append(rec)
    n = 0
    for other in rooms.room_mates(chan):
        if not (other.key and other.key.endswith(b"battle")):
            continue
        if alias_stream:
            alias = other.alias_for(chan.addr)
            rs = other.remotes.get(alias)
            if rs is None or not rs.popped:
                continue
            rs.pending.append(rec)
        else:
            other.pending.append(rec)
        n += 1
    log(f"[udp {addr[0]}:{addr[1]}]   -> cmd {cmd} relayed: to the sender as our "
        f"record {chan.tx_base + len(chan.pending) - 1}"
        + (f" and to {n} other battle client(s)" if n else ""))


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    battleend, battlepop, groupchannel, identity, rooms, squad, worldchannel,
)
