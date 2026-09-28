"""The peer link: the server standing in as each client's peer for another pilot's unit."""
import socket
import struct
import time
from .deps import fmoworld
from .wirelog import log


def peer_link_pop_args(chan, rs):
    """The POP fields that make the client's peer for this alias SEND: our
    endpoint as THIS client reaches it (the one 0x0153 / 0x013A hand it) and the
    alias's tag. {} with FMO_UDP_PEER_LINK=0."""
    if not room.PEER_LINK:
        return {}
    host = addressing.host_for(addressing.BATTLE_HOST, chan.addr[0])
    try:
        host = socket.gethostbyname(host)
    except OSError:
        return {"client_tag": rs.tag}
    return {"client_addr": (host, addressing.BATTLE_PORT), "client_tag": rs.tag}


def peer_stream_for(chan, got):
    """The RemoteStream a client datagram belongs to, or None for the self
    stream. PEER LINK: by the +0x09 tag the client stamps for that peer -- its
    +0x00 is always its OWN UnitID, so the old +0x00 lookup could never match
    (and the traffic was misread as the self stream restarting). The +0x00
    lookup stays as the fallback for FMO_UDP_PEER_LINK=0."""
    if not got:
        return None
    if room.PEER_LINK and got["hid"] >= room.PEER_TAG_BASE:
        for rs in chan.remotes.values():
            if getattr(rs, "tag", None) == got["hid"]:
                return rs
    return (chan.remotes.get(got["peer"])
            or getattr(chan, "npc_remotes", {}).get(got["peer"]))
#: Records a peer link carries that are link housekeeping, not game state.
PEER_HELLO = (3, 16)        # the peer's hello / retry -> answer cmd 4
PEER_ACKED = 4              # the peer answering OUR hello
PEER_PING, PEER_PONG = 300, 301


def peer_link_serve(chan, rs, got, addr):
    """Consume one datagram the client sent to alias `rs`: link housekeeping
    here, everything else RELAYED to the player that alias stands for, under
    the alias THAT client knows the sender by."""
    rs.rx = got["to"]
    other = groupchannel.WORLD_PEERS.get(rs.peer_addr)
    for off, size, cmd, body in got["records"]:
        if cmd in PEER_HELLO:
            rs.pending.append(fmoworld.record(
                PEER_ACKED, (rs.alias & 0xFFFFFFFF).to_bytes(4, "little"),
                arg8=rs.alias))
            if not rs.linked:
                rs.linked = True
                log(f"[udp {addr[0]}:{addr[1]}] VERIFIED: PEER LINK: the client "
                    f"greeted alias {rs.alias:#x} (cmd {cmd}, tag {rs.tag:#x}) "
                    f"-> cmd 4, its peer state goes to 2. Watch its log for "
                    f"'(Operator)p2p成功' and the [!] clearing.")
            continue
        if cmd == PEER_ACKED:
            if not rs.linked:
                rs.linked = True
                log(f"[udp {addr[0]}:{addr[1]}] VERIFIED: PEER LINK: alias "
                    f"{rs.alias:#x} acked our hello (cmd 4)")
            continue
        if cmd == PEER_PING:
            tick = body[:4] if len(body) >= 4 else b"\0\0\0\0"
            rs.pending.append(fmoworld.record(
                PEER_PONG, tick + struct.pack("<II",
                                              int(time.time() * 1000) & 0xFFFFFFFF,
                                              0), arg8=rs.alias))
            continue
        if other is None or not other.tables:
            continue
        # THE RELAY. The record is about the sender's own unit (their UnitID,
        # at rec+0x08 and inside a cmd 24 batch); the other client knows that
        # unit by the alias IT minted for the sender.
        plain = got["plain"]
        src = struct.unpack_from("<I", plain, off + 8)[0]
        flt = struct.unpack_from("<I", plain, off + 12)[0]
        if chan.key == groupchannel.GROUP_KEY:
            # VOICE (cmd 123). Delivered VERBATIM
            # on the other member's group SELF stream: the receive arm
            # 0x611E5ADD takes it on any stream with no id check, and a body we
            # altered would fail the codec -- which deletes the client's voice
            # system until restart. Nothing else on a group link is relayed.
            if cmd == roomrelay.GROUP_VOICE_CMD:
                # WARNING: ONLY TO A CLIENT WHOSE VOICE SYSTEM IS UP. Live
                # 2026-09-27: a PC's voice reached a Steam Deck whose voice
                # system had failed to start (the red icon: no capture
                # device under Proton), and the Deck hung. The server cannot read that state, but a client
                # only SENDS cmd 123 when its voice system works -- so a
                # member that has never sent voice gets none.
                # FMO_GROUP_VOICE_TO=all relays to every member regardless.
                chan.voice_seen = True
                if roomrelay.GROUP_VOICE_TO != "all" and not getattr(other, "voice_seen", False):
                    if not getattr(other, "voice_withheld_said", False):
                        other.voice_withheld_said = True
                        log(f"[udp {addr[0]}:{addr[1]}] GROUP VOICE withheld "
                            f"from {other.addr[0]}:{other.addr[1]}: that client "
                            f"has never sent voice, so its voice system may not "
                            f"be up -- a client whose voice failed hung on "
                            f"receiving it. FMO_GROUP_VOICE_TO=all "
                            f"overrides.")
                    continue
                try:
                    other.pending.append(fmoworld.record(
                        cmd, body, arg8=src, flt=flt & 0xFFFF0000))
                except ValueError:
                    continue
                rs.relayed += 1
                if rs.relayed <= 3 or rs.relayed % 200 == 0:
                    log(f"[udp {addr[0]}:{addr[1]}] GROUP VOICE #{rs.relayed}: "
                        f"cmd {cmd} {len(body)}B -> {other.addr[0]}:{other.addr[1]} "
                        f"(their group self stream, verbatim)")
            continue
        mine = chan.self_unit()
        # Only the sender's OWN unit: a squad owner's records about the AI
        # enemies already reach the room through the squad relay, and a second
        # copy would move / fire / hit them twice.
        if src != mine and cmd != 24:     # a batch is filtered per entry below
            continue
        b_alias = other.alias_for(getattr(chan, "peer_key", chan.addr))
        _rsb = other.remotes.get(b_alias)
        if _rsb is None or not _rsb.popped:
            continue                # not introduced there yet: nothing to move
        new_src = b_alias if src == mine else src
        nb = bytearray(body)
        if cmd == 24:
            # u16 n, then n x (u32 id, motion state). A squad owner's batch also
            # carries the AI units, which the squad relay already delivers --
            # keep ONLY the sender's own entry (squad_batch_filter walks the
            # real entry lengths) and rename it to the alias.
            _own = squad.squad_batch_filter(bytes(nb), {mine})
            if _own is None:
                continue
            nb = bytearray(_own)
            struct.pack_into("<I", nb, 2, b_alias)
        try:
            rec = fmoworld.record(cmd, bytes(nb), arg8=new_src,
                                  flt=flt & 0xFFFF0000)
        except ValueError:
            continue
        _rsb.pending.append(rec)
        rs.relayed += 1
        if rs.relayed <= 5 or rs.relayed % 500 == 0:
            log(f"[udp {addr[0]}:{addr[1]}] PEER LINK relay #{rs.relayed}: "
                f"cmd {cmd} {len(body)}B for unit {src:#x} -> "
                f"{other.addr[0]}:{other.addr[1]} as {new_src:#x} (their alias "
                f"stream {b_alias:#x})")


# Called at run time only; imported last so that import cycles resolve.
from . import addressing, groupchannel, room, roomrelay, squad  # noqa: E402
