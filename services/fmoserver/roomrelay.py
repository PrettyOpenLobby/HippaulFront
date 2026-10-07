"""The room relay: introducing room-mates to each other and flushing their records."""
import os
import struct
import time
from .deps import fmoworld
from .wirelog import log


def room_queue(chan):
    """Introduce, then relay. Called once per inbound datagram on `chan`.

    Two steps, in this order and for a reason:

      1. **POP each newcomer on the SELF stream.** cmd 7 is the only thing that
         creates the peer the next step needs, and it has to travel on a stream
         the client already has -- `0x610012B0`, the factory that would create
         one on demand, is a stub returning 0, so a datagram naming an unknown
         peer is dropped without a log line, a reply or a disconnect.
      2. **Relay position on the ALIAS stream**, because `0x611EABE9` takes the
         moved unit from `peer+0x10` and nothing in the payload names it.

    WARNING: THIS ONLY QUEUES. It must run BEFORE the self stream's reply is built,
    or the POP it produces waits for the datagram after -- half a second in
    which a newcomer exists on the server and not on the screen, and, worse, a
    window in which room_flush could send an alias datagram for a peer the
    client has not been told to create. The first version did exactly that and
    the selftest below caught it. **Queue, then send, in one pass.**"""
    if not room.ROOM or not chan.tables:
        return
    # WARNING: SELF FIRST. `manager+0x2C` is the local player's own UnitID and the
    # scene-setup state machine sits in a wait until that unit is in the entity
    # map (the self unit is what renders the world) -- so introducing a stranger
    # into a scene that has not yet been given its own player is, at best,
    # untested ordering against the one gate we know is load-bearing. A real
    # server pops the player as part of scene entry and everyone else after.
    if popsweep.POP and not chan.popped:
        return
    now = time.time()
    rooms.room_prune(chan, now)
    from . import coliseum               # late: coliseum imports half the package
    for other in rooms.room_mates(chan):
        if coliseum.spectator_of_chan(other) is not None:
            continue            # a Coliseum spectator is popped into nobody's scene
        alias = chan.alias_for(other.addr)
        rs = chan.remotes[alias]
        if not rs.popped:
            # WARNING: AS `other`, not as the channel being served: with two devices
            # behind one address, account_for(other's ip) inside this datagram
            # answered for `chan` -- so the room-mate wore the LISTENER's name
            # (live 2026-09-27, PC and Deck). The lookups must run in the
            # other channel's context.
            with worldchannel._as_world_channel(other.addr):
                n1, n2, _src = popnames.pop_names_for(other.addr[0])
                _rf, _rs, _ = popself.pop_model_for(other.addr[0])
            # Create bare and dress late here too -- same reasoning as the self
            # POP (POP_LOOK_DEFER), and the stakes are higher: this record runs
            # on somebody ELSE's client.
            _rlook = getattr(other, "type4_look", None)
            _rlook_now = _rlook
            if poplook.POP_LOOK_DEFER > 0:
                # KEY: ARM THE FOLLOW-UP EVEN WITH NO LOOK YET. Two clients
                # returning from a battle land seconds apart, so the first one
                # back is popped the second while the second is still in its
                # sortie and has no look pinned. Arming only when a look is
                # ALREADY known left that remote undressed for the rest of the
                # scene, because nothing re-pops an alias once rs.popped is
                # set. The follow-up at the elif below is a no-op until
                # other.type4_look exists, and fires the moment it does.
                _rlook_now = None
                rs.look_due = now + poplook.POP_LOOK_DEFER
                rs.look_sent = False
            rs.pop_args = dict(
                unit_type=room.ROOM_TYPE, name1=n1, name2=n2,
                pos=tuple(other.pos[:3]) + (0.0,),
                model_flags=_rf, model_sub=_rs,
                type4_model=getattr(other, "type4_model", popself.POP_SEX))
            if POP_PLAYER_TARGETABLE:
                # listable in /target and the Trade menu (see the knob)
                rs.pop_args["extra"] = {
                    fmoworld.POP_TARGETABLE: bytes([fmoworld.POP_TARGETABLE_BIT])}
            _bwz = rooms.battle_room_pop_args(chan, other, n1, n2)
            if _bwz is not None:
                # A room-mate in a BATTLE is their wanzer, not the lobby human.
                rs.pop_args = _bwz
                _rlook_now, rs.look_due = None, None     # the parts are the look
                _hostile = rooms.battle_room_hostile(chan, other)
                if _hostile:
                    _bs = referee.battle_state(referee.chan_bkey(chan))
                    _bs.setdefault("enemies", set()).add(alias)
                    _bs["pvp"] = True            # war_settle weighs it double
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] BATTLE ROOM: "
                    f"{other.addr[0]} pops here as UnitID {alias:#x} with THEIR "
                    f"battle POP (UnitType {_bwz.get('unit_type')}, nation "
                    f"{_bwz.get('nation')}, {len(_bwz.get('parts') or ())} "
                    f"part(s), client_kind 0 = alive) -> "
                    + ("an ENEMY (nations differ): destroying it pays "
                       "(battle_kills)" if _hostile else
                       "FRIENDLY (same nation)"))
            try:
                chan.pending.append(fmoworld.record_pop(
                    alias, look=_rlook_now, **(owned_by(rs.pop_args, alias)
                                               if rooms._is_battle_chan(chan) else rs.pop_args),
                    # KEY: THE PEER STREAM'S OWN BLOWFISH KEY. Every peer the
                    # client creates gets its own cipher, scheduled from this
                    # field (fmoworld.POP_CLIENT_BLOB). We send the key THIS
                    # channel's datagrams are encrypted with, because line
                    # ~4558 builds the alias stream with `chan.tables` -- so
                    # the two cannot drift apart. Sending zeros (which is what
                    # we did until 2026-08-26) schedules an EMPTY key, the
                    # client's MD5 fails, and every movement record on the
                    # alias stream is discarded before the window is consulted:
                    # measured live as "they appear but never move".
                    client_key=(chan.key if room.ROOM_PEER_KEY else None),
                    **peerlink.peer_link_pop_args(chan, rs)))
            except ValueError as e:
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] WARNING: ROOM POP "
                    f"REFUSED BY OUR OWN GUARD, nothing sent: {e}")
                rs.popped = True          # do not retry a record we cannot build
                continue
            rs.popped = True
            log(f"[udp {chan.addr[0]}:{chan.addr[1]}] VERIFIED: ROOM: "
                f"{other.addr[0]}:{other.addr[1]} joins as UnitID "
                f"{alias:#x} ({n1!r} {n2!r}) UnitType={room.ROOM_TYPE} at "
                f"{tuple(round(v, 2) for v in other.pos)} -- POP queued on the "
                f"SELF stream (record {chan.tx_base + len(chan.pending) - 1}); "
                f"movement will follow on a datagram stamped peer={alias:#x}")
        elif (rs.pop_args and not rs.look_sent and rs.look_due
              and now >= rs.look_due and getattr(other, "type4_look", None)):
            # The deferred dress for a room-mate: a second cmd 7 on the SAME
            # alias takes the already-present arm and rebuilds their unit
            # dressed. On the SELF stream, like the create POP.
            try:
                chan.pending.append(fmoworld.record_pop(
                    alias, look=other.type4_look, **(owned_by(rs.pop_args, alias)
                                                     if rooms._is_battle_chan(chan) else rs.pop_args),
                    client_key=(chan.key if room.ROOM_PEER_KEY else None),
                    **peerlink.peer_link_pop_args(chan, rs)))
            except ValueError as e:
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] WARNING: ROOM DEFERRED "
                    f"LOOK for {alias:#x} refused, the bare unit stands: {e}")
            else:
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] room: DEFERRED LOOK "
                    f"for {alias:#x} queued ({other.type4_look}) -- rebuilt "
                    f"dressed {poplook.POP_LOOK_DEFER:g}s after joining bare")
            rs.look_sent = True
        elif (other.pos != rs.sent_pos
              and getattr(other, "pos_src", None) == "state"):
            # A battle pilot placed by its own cmd 23/24: that motion state
            # already reaches this client verbatim (peerlink / squad relay),
            # and a cmd 240 copy cannot carry past +/-327.67 on a map that
            # runs to +/-2048. Nothing to re-encode.
            rs.sent_pos = other.pos
        elif (other.pos != rs.sent_pos
              and now - rs.sent_at >= room.ROOM_MIN_INTERVAL):
            try:
                rs.pending.append(fmoworld.record_move(
                    tuple(other.pos[:3]), other.rot, flags=other.move_flags))
            except ValueError as e:
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] WARNING: ROOM MOVE "
                    f"for {alias:#x} refused: {e}")
                rs.sent_pos = other.pos    # do not spin on a bad coordinate
                continue
            if rs.sent_n < room.ROOM_LOG_FIRST:
                log(f"[udp {chan.addr[0]}:{chan.addr[1]}] room: queued a move "
                    f"for {alias:#x} to "
                    f"({other.pos[0]:.2f}, {other.pos[1]:.2f}, "
                    f"{other.pos[2]:.2f}) rot {other.rot:+.3f}")
            rs.sent_pos, rs.sent_at = other.pos, now


def owned_by(pop_args, owner):
    """`pop_args` with body+0x2C (the OWNER) set to `owner`. A room-mate's unit
    is owned by its alias -- the peer whose link state 2 lets the freeze check
    0x61051BB0 run it; a battle mate's args are copied from THEIR self POP,
    whose owner is their own id, which must not leak into this client."""
    extra = dict(pop_args.get("extra") or {})
    extra[battlepop.POP_AI_OWNER] = struct.pack("<I", owner & 0xFFFFFFFF)
    return dict(pop_args, extra=extra)


def room_flush(sock, chan, got):
    """Send one datagram per alias stream that has records waiting.

    Separate from room_queue because the two belong on opposite sides of the
    self stream's own datagram: the POP that creates a peer has to be on the
    wire before anything addressed to that peer is."""
    if not room.ROOM or not chan.tables:
        return 0
    sent = 0
    _now = time.time()
    for alias, rs in list(chan.remotes.items()) + list(getattr(chan, "npc_remotes", {}).items()):
        _live = (room.PEER_LINK and getattr(rs, "linked", False) and not rs.gone
                 and _now - getattr(rs, "last_tx", 0.0) >= room.PEER_KEEPALIVE)
        if not rs.pending and not _live:
            continue
        # WARNING: Same window contract as the self stream, on its own counters: FROM
        # is the first record this peer has not acknowledged, the datagram
        # carries the whole unacknowledged tail, and flag 0 asks to be adopted
        # exactly once (0x61070902 only adopts while the peer's +0x106E is 0).
        _n = udpconfig.fit_records(rs.pending) if rs.pending else 0   # the 1,400-B wall
        dgm = fmoworld.build(*chan.tables, peer=alias,
                             hid=udpconfig.scene_reply_hid(chan),
                             # PEER LINK: +0x0A is the alias's TAG -- the client
                             # copies it to peer+0x111C and stamps it back at
                             # +0x09 on everything it sends this peer.
                             kind=(rs.tag if room.PEER_LINK and hasattr(rs, "tag")
                                   else got["kind"]),
                             ack=getattr(rs, "rx", 0) if room.PEER_LINK else 0,
                             flag=0 if not rs.adopted else 2,
                             frm=rs.tx_base,
                             to=(rs.tx_base + _n) & 0xFFFF,
                             body=b"".join(rs.pending[:_n]))
        rs.sent_n += 1
        # WARNING: SAY THAT WE SENT IT. The first live test (2026-08-24) could not be
        # read: both clients streamed cmd 240, both consumed the ROOM POP
        # ("ours 3+0 pending, their ack 3"), and NOTHING came back on the alias
        # stream -- and this function logged nothing, so "we never relayed" and
        # "we relayed and the client dropped it" were indistinguishable in our
        # own log. That is the failure mode the regression rules are about: an
        # instrument with a hole exactly where the answer is.
        if rs.sent_n <= room.ROOM_LOG_FIRST or rs.sent_n % room.ROOM_LOG_EVERY == 0:
            log(f"[udp {chan.addr[0]}:{chan.addr[1]}] room -> peer "
                f"{alias:#x} datagram #{rs.sent_n}: {_n} of {len(rs.pending)} record(s) "
                f"frm={rs.tx_base} to={(rs.tx_base + _n) & 0xFFFF} "
                f"flag={0 if not rs.adopted else 2} ack=0 hid={udpconfig.scene_reply_hid(chan)} "
                f"{len(dgm)}B -> {chan.addr[0]}:{chan.addr[1]}"
                + ("  WARNING: NOTHING HAS EVER BEEN ACKED ON THIS STREAM -- if "
                   "that holds, the client is dropping these (peer not in its "
                   "map, or the window test), NOT failing to receive them"
                   if rs.tx_base == 0 and rs.sent_n > 3 else ""))
        rs.adopted = True
        rs.last_tx = _now
        sock.sendto(dgm, chan.addr)
        sent += 1
    return sent


#: Voice chat on the battle group's peer links (sender 0x611E5150).
GROUP_VOICE_CMD = 123
#: cmd 191: update an existing group member's blob from +0x4C (0x611E5420).
GROUP_BLOB_UPDATE_CMD = 191
#: talkers (default) = relay voice only to members that have themselves sent
#: voice (their voice system is up); all = to every member.
GROUP_VOICE_TO = os.environ.get("FMO_GROUP_VOICE_TO", "talkers").strip() or "talkers"
#: KEY: FMO_UDP_POP_PLAYER_TARGETABLE=1 (default): a lobby room-mate's POP
#: carries body+0x48 bit 0x10, the "Select target" list gate
#: (fmoworld.POP_TARGETABLE; list builder 0x611824D6 admits an entity only
#: with dword[entity+0x18F] & 0x10). The NPC pops have set it since 09-05
#: (npcroster.POP_NPC_TARGETABLE); players never did, so the list (and the
#: Trade menu, which opens it in players-only mode 1) could not offer another
#: pilot (manual p.40: "you can target other players as well as NPCs").
#: Only room-mates: the self POP stays clear. The name-tag bit 0x01 in the
#: same byte is ORed in afterwards by record_pop (fmoworld.NAMETAG), so both
#: survive. 0 = the old POP.
POP_PLAYER_TARGETABLE = os.environ.get("FMO_UDP_POP_PLAYER_TARGETABLE", "1").strip() not in ("", "0")


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    battlepop, peerlink, poplook, popnames, popself, popsweep, referee, room, rooms, udpconfig,
    worldchannel,
)
