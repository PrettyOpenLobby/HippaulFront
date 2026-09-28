"""Rooms: who stands in the same zone, arrivals and departures, battle rooms."""
import os
import time
from .deps import fmoworld
from .wirelog import log


#: host -> the MapNo its last 0x0153 granted. The room only relays between
#: clients standing in the same zone, and the UDP half has no other way to know
#: which one that is: MapNo is chosen on the TCP side, per grant, and a Move
#: changes it. Keyed by HOST rather than by channel because the grant precedes
#: the channel it describes -- that ordering is the whole reason
#: reset_world_channel exists.
WORLD_MAPS = {}
#: host -> the MapKind (zone id) its last 0x0153 granted, the other half of
#: the room key (ROOM_SAME_ZONE). Recorded beside WORLD_MAPS by the same
#: reset_world_channel call, for the same ordering reason.
WORLD_ZONES = {}


def zone_of(host):
    """(zone id, kind) this host was last granted, or (None, None)."""
    z = WORLD_ZONES.get(host)
    return (z, z // 100) if z is not None else (None, None)


def reset_world_channel(host, why, mapno=None, mapkind=None, place=None,
                        account=None):
    """ARM a restart on every world channel for `host`, without dropping it.

    `account` is the entering game connection's; it is queued for the NEW
    channel this entry is about to open (queue_world_entry).

    THIS USED TO DROP THE CHANNEL OUTRIGHT, AND IT COST A LIVE RUN
    (2026-08-22). Serving a 0x0153 is not the moment the client restarts its
    scene -- it is a second or two BEFORE. Measured: the grant went out at
    07:20:23, the DYING scene sent one more datagram at 07:20:24 (records
    8..9, ack=2, hid=2), and the client did not restart its indices until
    07:20:26. With the channel already dropped, that trailing datagram from
    the old scene built a FRESH channel and armed the once-per-channel probes
    against it -- so the cmd-7 POP was spent on a scene that was ending, the
    new scene never got the player's own unit, and the screen stayed black in
    a way that read exactly like "terrain does not draw in this map".

    The backstop below had already printed the right sentence at 07:20:26 and
    nothing acted on it. A detector that only warns gets read as noise.

    So: mark, and let the CLIENT'S OWN index restart do the reset. That is the
    observable event, it still covers the relaunch case this hook was written
    for (a relaunched client restarts at 0 too), and it cannot fire early.

    Returns the number armed, so the caller can say nothing happened rather
    than log a reset that did not occur."""
    worldchannel.queue_world_entry(host, account, {k: v for k, v in
                                                   (("map", mapno), ("zone", mapkind),
                                                    ("place", place)) if v is not None})
    if mapno is not None:
        was = WORLD_MAPS.get(host)
        WORLD_MAPS[host] = mapno
        if was != mapno:
            log(f"[udp {host}] room: MapNo {was} -> {mapno}"
                + ("" if not room.ROOM else
                   f" -- relaying only with the other clients in {mapno}"
                   if room.ROOM_SAME_MAP else
                   " -- FMO_UDP_ROOM_SAME_MAP=0, so the map is IGNORED"))
    if place is not None:
        was_p = move.WORLD_PLACES.get(host)
        move.WORLD_PLACES[host] = place
        if was_p != place:
            log(f"[udp {host}] place: {move.place_name(was_p)} -> {move.place_name(place)}"
                + (" (FMO_PLACES: the relay room is this place)" if move.PLACES and room.ROOM
                   else ""))
    if mapkind is not None:
        was_z = WORLD_ZONES.get(host)
        WORLD_ZONES[host] = mapkind
        if was_z != mapkind:
            log(f"[udp {host}] room: zone {was_z} -> {mapkind}"
                + ("" if not room.ROOM else
                   f" -- relaying only with the other clients in zone "
                   f"{mapkind} (FMO_UDP_ROOM_SAME_ZONE)" if room.ROOM_SAME_ZONE else
                   " -- FMO_UDP_ROOM_SAME_ZONE=0, so the zone is IGNORED"))
    live = [a for a in groupchannel.WORLD_PEERS if a[0] == host]
    for a in live:
        chan = groupchannel.WORLD_PEERS[a]
        chan.expect_restart = True
        log(f"[udp {a[0]}:{a[1]}] restart ARMED ({why}): tx_base="
            f"{chan.tx_base}, {len(chan.pending)} pending, "
            f"popped={chan.popped}. NOT dropped -- the reset happens when the "
            f"client's own indices go back to 0, which is a second or two "
            f"later and is the only unambiguous sign the new scene has begun.")
    return len(live)


#: battle_key -> the MapNo its last served sortie (0x013A) sent it to.
SORTIE_MAP = {}


def room_mates(chan):
    """The other live channels standing in the same zone as `chan`.

    A channel counts as live while it is still sending -- ROOM_TTL, not the
    presence of a socket. WARNING: There is no depop (see ROOM_TTL): dropping a stale
    channel here stops us relaying its movement, it does NOT remove the unit
    from anyone's screen."""
    if not room.ROOM:
        return []
    now = time.time()
    mine, mine_zone, mine_place = worldchannel.chan_where(chan)
    out = []
    if chan.key == groupchannel.GROUP_KEY:
        return []                       # a group channel carries no world
    for a, other in groupchannel.WORLD_PEERS.items():
        if a == chan.addr or other.key is None or other.key == groupchannel.GROUP_KEY:
            continue
        if other.left or now - other.seen_at > room.ROOM_TTL:
            continue
        # WARNING: SAME SCENE KIND. A battle channel and a lobby channel share the
        # lobby's map/zone (battle rooms are grouped by the lobby the pilots
        # sortied from), so without this a lobby HUMAN was popped into a battle
        # scene -- live 2026-09-27 19:10: Fox sortied, Kai stood in the same
        # O.C.U. lobby, Kai's UnitType-4 body landed in Fox's scene 4 and the
        # client closed two seconds later.
        if _is_battle_chan(other) != _is_battle_chan(chan):
            continue
        # WARNING: ...and the SAME BATTLE MAP: the lobby grouping alone paired a
        # pilot on map 267 with one on 418 (live 22:33Z, each blinking on the
        # other's screen).
        if _is_battle_chan(chan):
            _sm, _so = (SORTIE_MAP.get(referee.chan_bkey(chan)),
                        SORTIE_MAP.get(referee.chan_bkey(other)))
            if _sm is not None and _so is not None and _sm != _so:
                continue
        theirs, theirs_zone, theirs_place = worldchannel.chan_where(other)
        if room.ROOM_SAME_MAP and theirs != mine:
            continue
        if room.ROOM_SAME_ZONE and theirs_zone != mine_zone:
            continue
        if move.PLACES and theirs_place != mine_place:
            continue
        out.append(other)
    return out


def room_left(host, why):
    """The TCP side says `host`'s player is leaving (0x0152). Mark every world
    channel of that host so room_mates drops them at once and room_prune tells
    each viewer, instead of waiting ROOM_TTL for the channel to go quiet.
    Returns how many were marked, so the caller can say 'none' honestly."""
    n = 0
    for a, ch in groupchannel.WORLD_PEERS.items():
        if a[0] == host and ch.left is None:
            ch.left = why
            n += 1
    if n:
        log(f"[udp {host}] room: {why} -- {n} world channel(s) marked LEFT; "
            f"every viewer that has them popped is told on its next datagram"
            + ("" if room.ROOM_DEPOP else
               " (FMO_UDP_ROOM_DEPOP=0: told in the LOG ONLY, the unit stays "
               "on screen -- see ROOM_DEPOP for why)"))
    return n


def room_prune(chan, now):
    """Tell THIS viewer about every room-mate that is no longer one.

    One sweep, three reasons -- 0x0152 LOG OUT (`other.left`), a Move to a
    different MapNo (WORLD_MAPS disagrees), ROOM_TTL silence (or the channel
    object gone) -- because to the viewer they are one event: the alias they
    were popped as has nobody behind it.

    With FMO_UDP_ROOM_DEPOP=1 a cmd 8 is queued on the SELF stream (the only
    peer 0x611D47A5 accepts it on) and the alias is forgotten, so a return is
    a fresh POP under a fresh UnitID -- never a second POP of an id whose
    entity the depop destroyed but whose PEER it left behind (0x611EAE36 only
    deletes a peer in state 3/4; a state-2 one would be double-inserted, and
    "ERR!!! pCli->Init()" is the arm's own name for that).

    With it OFF (the default -- the lobby session ignores cmd 8, see
    ROOM_DEPOP) the stream is KEPT: the unit is still standing on the screen
    under that alias, so when the player comes back the relay resumes onto
    it with the window it already has, and no second entity is created."""
    mine, mine_zone, mine_place = worldchannel.chan_where(chan)
    for alias, rs in list(chan.remotes.items()):
        if not rs.popped:
            continue
        other = groupchannel.WORLD_PEERS.get(rs.peer_addr)
        if other is not None:
            theirs, theirs_zone, theirs_place = worldchannel.chan_where(other)
        else:
            theirs = WORLD_MAPS.get(rs.peer_addr[0])
            theirs_zone = WORLD_ZONES.get(rs.peer_addr[0])
            theirs_place = move.WORLD_PLACES.get(rs.peer_addr[0])
        if other is None:
            why = "their world channel is gone"
        elif other.left:
            why = other.left
        elif now - other.seen_at > room.ROOM_TTL:
            why = (f"silent for {now - other.seen_at:.0f}s > ROOM_TTL "
                   f"{room.ROOM_TTL:g}s")
        elif _is_battle_chan(other) != _is_battle_chan(chan):
            why = ("went into a battle" if _is_battle_chan(other)
                   else "went back to the lobby")
        elif room.ROOM_SAME_MAP and theirs != mine:
            why = f"moved to MapNo {theirs} (we are in {mine})"
        elif room.ROOM_SAME_ZONE and theirs_zone != mine_zone:
            why = f"moved to zone {theirs_zone} (we are in zone {mine_zone})"
        elif move.PLACES and theirs_place != mine_place:
            why = (f"moved to {move.place_name(theirs_place)} "
                   f"(we are in {move.place_name(mine_place)})")
        else:
            if rs.gone:
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] room: "
                    f"{rs.peer_addr[0]} is BACK as {alias:#x} (was: {rs.gone})"
                    f" -- relay resumes on the alias they never stopped "
                    f"wearing; no second POP")
                rs.gone = None
            continue
        if rs.gone:
            continue                    # already told, once
        if room.ROOM_DEPOP:
            try:
                rec = fmoworld.record_depop(alias, from_id=0,
                                            status=room.ROOM_DEPOP_STATUS)
            except ValueError as e:
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] WARNING: ROOM DEPOP for "
                    f"{alias:#x} refused by our own guard: {e}")
                rs.gone = why
                continue
            chan.pending.append(rec)
            del chan.remotes[alias]
            chan.alias_of.pop(rs.peer_addr, None)
            log(f"[udp {chan.addr[0]}:{chan.addr[1]}] PARTIAL: ROOM DEPOP: "
                f"{rs.peer_addr[0]}:{rs.peer_addr[1]} left ({why}) -- cmd 8 "
                f"RecvDepop(UnitID={alias:#x} FromID=0 Status="
                f"{room.ROOM_DEPOP_STATUS}) queued on the SELF stream (record "
                f"{chan.tx_base + len(chan.pending) - 1}); alias forgotten, a "
                f"return re-POPs under a new id. WARNING: PREDICTED INERT on the "
                f"lobby session (0x611EBC44 maps cmd 8 to the default arm): "
                f"expect an ack and a unit that stays.")
        else:
            rs.gone = why
            log(f"[udp {chan.addr[0]}:{chan.addr[1]}] WARNING: ROOM: "
                f"{rs.peer_addr[0]}:{rs.peer_addr[1]} left ({why}) -- "
                f"relay for {alias:#x} STOPS, NO DEPOP SENT "
                f"(FMO_UDP_ROOM_DEPOP=0): their unit stays on this screen "
                f"where it stood; if they come back the same alias carries "
                f"them again.")


#: FMO_BATTLE_ROOM_WANZER: '1' (default) = a room-mate on a BATTLE channel is
#: popped with THEIR OWN battle POP (chan.pop_args: UnitType, nation at
#: body+0x7C, side, garage parts), alive (client_kind 0), instead of the
#: lobby's UnitType-4 human with no parts and no nation -- which is what two
#: pilots in one battle saw of each other until 2026-09-26, and which cannot
#: be fought (no wanzer, and nation 0 is nobody's enemy). Only fires when the
#: other pilot's own pop is a non-human UnitType, i.e. FMO_UDP_POP_BATTLE is
#: armed; '0' reverts to the human.
BATTLE_ROOM_WANZER = os.environ.get("FMO_BATTLE_ROOM_WANZER", "1") not in ("0", "")
#: How recent (seconds) a room-mate's last shot must be for a pilot's own
#: death to be credited to them as a kill (battle_room_killer).
BATTLE_KILL_WINDOW = 10.0


def _is_battle_chan(c):
    return bool(getattr(c, "key", None)) and c.key.endswith(b"battle")


def battle_room_pop_args(chan, other, name1, name2):
    """The pop args by which `chan`'s client should know room-mate `other` in
    a battle, or None to keep the lobby human (see BATTLE_ROOM_WANZER). Pure."""
    if not (BATTLE_ROOM_WANZER and _is_battle_chan(chan)
            and _is_battle_chan(other)):
        return None
    theirs = getattr(other, "pop_args", None)
    if not theirs or theirs.get("unit_type") == 4:
        return None
    args = dict(theirs, name1=name1, name2=name2, client_kind=0,
                pos=tuple(other.pos[:3]) + (0.0,))
    if args.get("parts"):
        args.pop("model_flags", None)      # body+0x8E is part 0's kind byte
        args.pop("model_sub", None)
    return args


def battle_room_hostile(chan, other):
    """True when the two pilots' battle pops carry different nations (the
    body+0x7C -> unit+0x80 compare the join banner and the hit test use)."""
    a = (getattr(chan, "pop_args", None) or {}).get("nation")
    b = (getattr(other, "pop_args", None) or {}).get("nation")
    return bool(a) and bool(b) and a != b


def battle_room_killer(victim, now, mates=None):
    """The room-mate most likely to have destroyed `victim`'s own unit: a
    hostile battle channel that fired within BATTLE_KILL_WINDOW, the most
    recent first. None when nobody qualifies.
    WARNING: A HEURISTIC. Under the P2P authority model the victim's client decides
    its own death and names no shooter; with two pilots it is exact, with more
    it credits whoever shot last."""
    best = None
    for o in (room_mates(victim) if mates is None else mates):
        if not (_is_battle_chan(o) and battle_room_hostile(victim, o)):
            continue
        t = getattr(o, "last_fire", None)
        if t is None or now - t > BATTLE_KILL_WINDOW:
            continue
        if best is None or t > best.last_fire:
            best = o
    return best


# Called at run time only; imported last so that import cycles resolve.
from . import groupchannel, move, referee, room, worldchannel  # noqa: E402
