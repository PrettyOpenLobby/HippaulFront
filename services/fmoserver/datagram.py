"""_serve_datagram: one inbound world-channel datagram, decoded and answered."""
import os
import struct
import time
from .deps import fmoworld
from .wirelog import hexdump, log
from . import wirelog


def _serve_datagram(sock, peers, dg, addr):
    # KEY: THE BATTLE-GROUP CHANNEL (LIVE 2026-09-05 23:13Z, brute-forced against
    # the client's own MD5 from a capture): CFmoGroup's datagrams come from the
    # SAME client socket as the lobby world's (source port 19155), hid 0, kind
    # 1, and their key is the LITERAL string "group" -- not "%xgroup": the
    # strings table holds "%xlobby" and "%xbattle" and nothing else, and the
    # "group" 0x61177620 passes to 0x611E4F40 is the key itself. Two streams on
    # one address, each with its own reliable window, so they must be two
    # channel objects: a datagram that verifies under "group" is routed to a
    # pre-latched sibling keyed (host, port, "group"), never to the lobby's.
    if groupchannel.GROUP_TABLES is not None and len(dg) >= 0x1C \
            and fmoworld.parse(*groupchannel.GROUP_TABLES, dg):
        gkey = (addr[0], addr[1], "group")
        chan = peers.get(gkey)
        if chan is None:
            chan = peers[gkey] = worldchannel.WorldChannel(addr)
            chan.key, chan.tables, chan.char_id = groupchannel.GROUP_KEY, groupchannel.GROUP_TABLES, 0
            chan.peer_key = gkey          # its WORLD_PEERS key (not chan.addr)
            # Same client socket as its lobby channel, so the same player.
            _lobby = peers.get(addr)
            chan.account = getattr(_lobby, "account", None)
            # The CFmoGroup socket may not be the one the lobby channel was
            # bound on (live 09-27: the Deck's group channel came from an
            # unbound port) -- so the create/join that opened it queued its
            # account; claim that. It names the member whose group this is.
            _gacct = groupchannel.claim_group_entry(addr[0])
            if _gacct:
                chan.account = _gacct
            log(f"[udp {addr[0]}:{addr[1]}] first GROUP datagram, {len(dg)}B -- "
                f"key {groupchannel.GROUP_KEY.decode()!r} verifies it; a separate channel "
                f"(reply hid {udpconfig.UDP_HID_GROUP}, no world POP, hello-ack without a POP)")
        chan.seen_at = time.time()
        got = fmoworld.parse(*chan.tables, dg)
        if got and got.get("kind") is not None and \
                getattr(chan, "group_kind", None) != got["kind"]:
            log(f"[udp {addr[0]}:{addr[1]}] GROUP channel sends kind "
                f"{got['kind']} -> replies go out on hid {got['kind']} "
                f"(creator = 1, joiner = 5; see scene_reply_hid)")
            chan.group_kind = got["kind"]
    else:
        chan = peers.get(addr)
        if chan is not None:
            chan.seen_at = time.time()
            if chan.account:
                worldchannel.settle_world_entry(chan)
            else:
                # Created before any entry was queued (e.g. across a restart).
                worldchannel.claim_world_account(addr[0], chan)
        if chan is None:
            chan = peers[addr] = worldchannel.WorldChannel(addr)
            worldchannel.claim_world_account(addr[0], chan)
            log(f"[udp {addr[0]}:{addr[1]}] first datagram, {len(dg)}B"
                + (f" -- bound to {chan.account} at {chan.loc} (the oldest "
                   f"unclaimed world entry from this address)"
                   if chan.account else ""))
        got = chan.unlock(dg) if chan.key is None else \
            fmoworld.parse(*chan.tables, dg)
    # WARNING: A LATCHED KEY CAN STOP VERIFYING WHEN THE SCENE RE-KEYS. restart()
    # keeps the key on the theory the endpoint and character id never change --
    # true between lobby rooms, FALSE entering scene 4, whose battle UDP manager
    # keys "%xbattle" where the lobby keyed "%xlobby". With the stale lobby key
    # every scene-4 datagram fails parse, so the restart indices can never be
    # read and the key can never be re-derived: a deadlock that reads as "did
    # not verify" forever (measured live 2026-09-04). Re-running the MD5-gated
    # candidate search breaks it -- it can only find the new key or fail, never
    # latch a wrong one.
    if got is None and chan.key is not None:
        log(f"[udp {addr[0]}:{addr[1]}] latched key {chan.key.decode()} stopped "
            f"verifying -- re-unlocking (a scene change can re-key)")
        got = chan.unlock(dg)
    # WARNING: WHICH STREAM IS THIS? The datagram's +0x00 is a UnitID, and the client
    # keeps one window per peer. A datagram stamped with an alias we minted is
    # that REMOTE unit's stream acknowledging what we relayed -- it must not
    # touch the self stream's counters, and it must not be read as the client
    # restarting its scene (a fresh alias stream also starts at from=0, ack=0,
    # which is byte-identical to the restart the detector below exists to
    # catch). **Two streams sharing one set of counters is the bug
    # WorldChannel's own docstring is about, one level up.**
    alias_rs = peerlink.peer_stream_for(chan, got)
    # WARNING: THE BACKSTOP. If the reset above ever fails to fire, this is the shape
    # it leaves behind: we believe we have sent records the peer has never
    # acknowledged and is not asking for. Saying so costs one line and saves a
    # whole run being read as a protocol failure.
    # THE CLIENT HAS RESTARTED ITS SCENE. Its record indices going back to
    # 0 while it acknowledges none of ours is the event that
    # reset_world_channel arms for, and it is the only unambiguous one --
    # our own send is too early by a second or two, which is the bug
    # documented there.
    # WARNING: THE GUARD USED TO BE `chan.tx_base`, AND THAT WAS WRONG -- measured
    # 2026-08-22, and it cost a live run in exactly the way this detector exists
    # to prevent. A stale channel carried over from a previous session (FMO dials
    # from a FIXED source port, so a relaunched client hashes to the same key)
    # had `tx_base=0, 1 pending, popped=True`. The client restarted its indices
    # on schedule -- `records 0..2 ack=0` -- and the detector SAT THERE, because
    # tx_base happened to be zero. `popped` stayed True, the POP was never
    # re-queued, the entity map stayed EMPTY, the scene fell back to state 3, and
    # the screen was black.
    #
    # WARNING: The DESYNC backstop below was blind for the SAME reason: it is gated on
    # tx_base as well. One wrong guard took out both nets at once. **When a
    # detector and its backstop share a term, they are not two checks.**
    #
    # The right question is not "have we sent anything" but "do we hold ANY state
    # this peer has just thrown away" -- an unacked record, a fired one-shot
    # probe, or a send index. A genuinely fresh channel has none of those, so it
    # still cannot fire early.
    if (got and alias_rs is None and got.get("from") == 0 and got.get("ack") == 0
            and (chan.tx_base or chan.pending or chan.popped or chan.said)):
        was = chan.restart()
        log(f"[udp {addr[0]}:{addr[1]}] \U0001f7e2 CHANNEL RESTARTED: the "
            f"peer is sending from record 0 and acking 0, so the new "
            f"scene has begun. Dropped tx_base={was[0]}, {was[1]} "
            f"pending, popped={was[2]}"
            + ("" if chan.expect_restart else
               " -- and NO 0x0153 armed this, so the client restarted a "
               "scene we did not grant. Worth reading.")
            + ". The once-per-channel probes are re-armed for it.")
        chan.expect_restart = False
        _was_acct = chan.account
        if chan.key == groupchannel.GROUP_KEY:
            # A GROUP channel restarts a second after the 0x0156/0x0157 that
            # re-armed it (live 09-27 21:28Z: the channels survived from before
            # the create/join, so their CREATION claimed nothing). Its own
            # queued entry is the only one due -- claim it here, never on an
            # arbitrary datagram, or the other device's channel could take it.
            _g = groupchannel.claim_group_entry(addr[0])
            if _g:
                chan.account = _g
                chan.remotes.clear()
                chan.alias_of.clear()
                log(f"[udp {addr[0]}:{addr[1]}]   GROUP: channel bound to {_g} "
                    f"(the create/join that restarted it) -- members will be "
                    f"introduced by cmd 190")
        _new_acct = worldchannel.rebind_on_restart(chan) if chan.key != groupchannel.GROUP_KEY else None
        if _new_acct:
            log(f"[udp {addr[0]}:{addr[1]}]   room: this port was {_was_acct}; "
                f"a waiting world entry says the client behind it is now "
                f"{_new_acct} at {chan.loc} -- rebound (a relaunch from the "
                f"same source port reuses the channel object)")
    if (got and alias_rs is None and (chan.tx_base or chan.popped) and not chan.pending
            and got.get("ack") == 0 and not chan.desync_warned):
        chan.desync_warned = True
        log(f"[udp {addr[0]}:{addr[1]}] \U0001f534 DESYNC: our tx_base is "
            f"{chan.tx_base} with nothing pending but the peer's ack is 0. "
            f"This channel outlived its client -- any probe keyed on "
            f"once-per-channel state (the POP) HAS ALREADY BEEN SKIPPED. "
            f"If this appears AFTER a 0x0153, the restart detector above "
            f"did not fire and the POP is going nowhere: read why before "
            f"reading the screen.")
    if got is None:
        # WARNING: Not necessarily an error: this is also what a datagram for
        # ANOTHER key looks like. Say which, rather than calling it corrupt.
        log(f"[udp {addr[0]}:{addr[1]}] {len(dg)}B did not verify"
            + ("" if chan.key else " under ANY character id we served -- "
               "the endpoint or the character list must have changed"))
        if udpconfig.UDP_DUMP_NOKEY and udpconfig._udp_nokey_dumped < udpconfig.UDP_DUMP_NOKEY:
            try:
                os.makedirs(os.path.join(wirelog.LOG_DIR, "captures"), exist_ok=True)
                path = os.path.join(
                    wirelog.LOG_DIR, "captures",
                    "fmo-udp-nokey-%s-%dB.bin"
                    % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), len(dg)))
                with open(path, "wb") as fh:
                    fh.write(dg)
                udpconfig._udp_nokey_dumped += 1
                log(f"[udp {addr[0]}:{addr[1]}] saved unverified datagram "
                    f"{udpconfig._udp_nokey_dumped}/{udpconfig.UDP_DUMP_NOKEY} -> {path}")
            except OSError as e:
                log(f"[udp {addr[0]}:{addr[1]}] could not save datagram: {e}")
        return
    if alias_rs is not None:
        # Their ack retires what we relayed; the reply keeps the window open.
        # Nothing else on this stream is ours to interpret -- the client has no
        # reason to send game records "as" another player's unit, so if one ever
        # arrives the log line is the measurement.
        before = len(alias_rs.pending)
        alias_rs.retire(got["ack"])
        if room.PEER_LINK and hasattr(alias_rs, "tag"):
            # THE PEER LINK: hello -> cmd 4, ping -> pong, the rest relayed to
            # the player this alias stands for. Then answer on the alias
            # stream with our ack and whatever is queued for it.
            peerlink.peer_link_serve(chan, alias_rs, got, addr)
            _n = udpconfig.fit_records(alias_rs.pending) if alias_rs.pending else 0
            sock.sendto(fmoworld.build(*chan.tables, peer=alias_rs.alias,
                                       hid=udpconfig.scene_reply_hid(chan),
                                       kind=alias_rs.tag, ack=alias_rs.rx,
                                       flag=0 if not alias_rs.adopted else 2,
                                       frm=alias_rs.tx_base,
                                       to=(alias_rs.tx_base + _n) & 0xFFFF,
                                       body=b"".join(alias_rs.pending[:_n])),
                        addr)
            alias_rs.adopted = True
            alias_rs.last_tx = time.time()
            return
        if got["records"]:
            log(f"[udp {addr[0]}:{addr[1]}] room stream {got['peer']:#x} "
                f"carried {len(got['records'])} record(s) -- unexpected, and "
                f"worth reading: "
                + ", ".join(f"cmd {c} {sz}B"
                            for _o, sz, c, _b in got["records"]))
        elif before != len(alias_rs.pending):
            log(f"[udp {addr[0]}:{addr[1]}] room stream {got['peer']:#x}: peer "
                f"acked to {got['ack']}, {len(alias_rs.pending)} still pending "
                f"-- VERIFIED: THE CLIENT IS CONSUMING RELAYED RECORDS, which "
                f"is the one thing a screenshot cannot tell you")
        sock.sendto(fmoworld.build(*chan.tables, peer=got["peer"],
                                   hid=udpconfig.scene_reply_hid(chan),
                                   kind=got["kind"], ack=got["to"],
                                   flag=0 if not alias_rs.adopted else 2,
                                   frm=alias_rs.tx_base,
                                   to=(alias_rs.tx_base
                                       + len(alias_rs.pending)) & 0xFFFF,
                                   body=b"".join(alias_rs.pending)), addr)
        alias_rs.adopted = True
        return
    if rooms._is_battle_chan(chan):
        squad.BATTLE_SEEN[referee.chan_bkey(chan)] = time.time()   # see lobby_cast_paused
    chan.seen += 1
    if got["records"] or chan.seen <= 3:
        log(f"[udp {addr[0]}:{addr[1]}] peer={got['peer']} hid={got['hid']} "
            f"kind={got['kind']} flag={got['flag']} records {got['from']}.."
            f"{got['to']} ack={got['ack']} ({len(got['records'])} carried)")
        for off, size, cmd, body in got["records"]:
            n = chan.cmd_seen.get(cmd, 0) + 1
            chan.cmd_seen[cmd] = n
            referee._note_battle_record(chan, addr, cmd, body)
            _a8 = struct.unpack_from("<I", got["plain"], off + 8)[0] \
                if off + 12 <= len(got["plain"]) else None
            if rooms._is_battle_chan(chan):
                _nr = squad.squad_relay(chan, addr, cmd, body, _a8)
                if cmd == squad.CMD_BM_HITLIST:
                    squad.squad_note_hits(chan, body, _a8)
                    _ne = squad.hitlist_echo(chan, addr, body, _a8)
                    if chan.cmd_seen.get(cmd, 0) <= 3:
                        _hl = squad.parse_hitlist(body)
                        log(f"[udp {addr[0]}:{addr[1]}]   cmd 43 HIT LIST from "
                            f"{_a8:#x}: "
                            + (", ".join(f"{t:#x} dmg {d} part {pt:#x}"
                                         for t, d, _an, pt in _hl["hits"])
                               if _hl else "(short)")
                            + f" -> echoed to the shooter + {_ne} other "
                            f"pilot(s); the TARGET's owner applies it")
                if cmd == squad.CMD_BM_FIRE:
                    _nr += squad.fire_relay(chan, addr, body, _a8)
                if _nr and chan.cmd_seen.get(cmd, 0) <= 3:
                    log(f"[udp {addr[0]}:{addr[1]}]   -> cmd {cmd} (source "
                        f"{_a8:#x}) relayed to {_nr} other battle pilot(s)")
            log(f"[udp {addr[0]}:{addr[1]}]   +{off:03x} cmd {cmd} "
                f"{size}B src {(_a8 if _a8 is not None else 0):#x} "
                f"{body[:24].hex(' ')}")
            # WARNING: THE 24-BYTE LINE ABOVE ONCE HID A WHOLE MESSAGE. 2026-08-22:
            # the client sent cmd 115, 100 bytes, and all we recorded was its
            # first 24 -- enough to see the sender's NAME and nothing of what
            # was said. The text was in bytes 24..99 and is simply gone from
            # that run. A truncated dump of an UNKNOWN command is not a log
            # line, it is a lost measurement.
            #
            # So: the first UDP_DUMP_FIRST occurrences of each command on a
            # channel are dumped WHOLE, with ASCII -- which is the only reason
            # the name was legible at all -- and the rest stay one line. Full
            # every time would drown the log in cmd 300, which arrives forever;
            # short every time is what just cost us the message.
            if udpconfig.UDP_DUMP_FIRST and n <= udpconfig.UDP_DUMP_FIRST and len(body) > 24:
                log(f"[udp {addr[0]}:{addr[1]}]   cmd {cmd} body {len(body)}B "
                    f"in full (occurrence {n} of the first "
                    f"{udpconfig.UDP_DUMP_FIRST}):" + os.linesep.replace("\r", "")
                    + hexdump(body, indent="      "))
    # VERIFIED: WHERE THIS PLAYER IS. cmd 240 is the client streaming its own
    # position (~2/s while walking), and until now we only acked it. It is the
    # input to the room relay -- and it is also the first server-side knowledge
    # FMO has ever had of where anybody is standing.
    for _off, _size, _cmd, _body in (got["records"] if got else []):
        if _cmd != fmoworld.CMD_MOVE:
            continue
        _m = fmoworld.parse_move(_body)
        if _m is None:
            log(f"[udp {addr[0]}:{addr[1]}] cmd 240 is {len(_body)}B, under "
                f"the client's own {fmoworld.MOVE_MIN}-byte minimum "
                f"(0x611EAC0C) -- not reading a position out of it")
            continue
        _was = chan.pos
        chan.pos, chan.rot = _m["pos"], _m["rot"]
        chan.move_flags, chan.moved_at = _m["flags"], time.time()
        #: VERIFIED: STAMPED WITH THE MapNo, because a coordinate without its map is
        #: not a spawn point. `WORLD_MAPS` already holds it -- the 0x0153 grant
        #: put it there (reset_world_channel), so this needs no new plumbing.
        _first = chan.pos_logged_at is None
        _due = udpconfig.POS_LOG_EVERY > 0 and (
            _first or chan.moved_at - chan.pos_logged_at >= udpconfig.POS_LOG_EVERY)
        #: WARNING: `_first` is NOT gated on the position having CHANGED. `chan.pos`
        #: is seeded with the spawn we served, so a client confirming it stands
        #: exactly where we put it reports _was == chan.pos -- and that is the
        #: single most informative datagram of the session, not a no-op.
        if _due and (_first or _was != chan.pos):
            chan.pos_logged_at = chan.moved_at
            _mn = rooms.WORLD_MAPS.get(addr[0])
            log(f"[udp {addr[0]}:{addr[1]}] POSITION "
                f"MapNo={_mn if _mn is not None else '?'} "
                f"({chan.pos[0]:.2f}, {chan.pos[1]:.2f}, {chan.pos[2]:.2f}) "
                f"rot {chan.rot:+.3f} flags {_m['flags']:#x}"
                + ("  <- SPAWN: this is where the scene PUT the player, and "
                   "it is the readout for which knob placed them (PilotPos or "
                   "FMO_UDP_POP_POS -- they are only distinguishable when BOTH "
                   "are set, since POP_POS defaults to PILOTPOS)" if _first else
                   "  <- walked; FMO_0153_PILOTPOS for this MapNo"))

    # THE CLIENT SAID SOMETHING. cmd 115 is its chat submit; cmd 18 is how a
    # line gets onto its screen. Nothing joins the two but us.
    if udpconfig.UDP_CHAT_ECHO:
        for _i, (_off, _size, _cmd, _body) in enumerate(
                got["records"] if got else []):
            if _cmd != fmoworld.CMD_UNK115:
                continue
            # WARNING: ONCE PER RECORD, NOT ONCE PER ARRIVAL. Records are indexed
            # from..to and the client RESENDS the range until it sees an ack.
            # Echoing on arrival meant one typed line became one line per
            # resend, on the sender's screen and everybody else's -- live
            # 2026-09-08 with three players, 'Hai' echoed eight times.
            # `acted` only moves forward, unlike `rx`, which follows whatever
            # the last datagram claimed.
            _idx = (got["from"] + _i) & 0xFFFF
            if _idx < chan.acted:
                log(f"[udp {addr[0]}:{addr[1]}] cmd 115 record {_idx} is a "
                    f"RESEND (already acted on up to {chan.acted}) -- not "
                    f"echoed again")
                continue
            chan.acted = _idx + 1
            _c = fmoworld.parse_chat_submit(_body)
            if _c is None:
                log(f"[udp {addr[0]}:{addr[1]}] cmd 115 is {len(_body)}B, "
                    f"under the client's own {fmoworld.SUBMIT_MIN}-byte "
                    f"minimum (0x611D5C07) -- not echoing a message the "
                    f"sender would not have managed to send")
                continue
            _who = " ".join(x for x in (_c["name1"], _c["name2"]) if x)
            log(f"[udp {addr[0]}:{addr[1]}] CHAT from UnitID {_c['unitid']} "
                f"({_who or 'unnamed'}, kind {_c['kind']}): {_c['text']!r}")
            # WARNING: THE SENDER FIRST, THEN THE ROOM. Chat down (cmd 18) rides
            # each listener's OWN stream, not an alias stream: 0x611E7480
            # formats "<name>: <text>" straight into the chat sink and never
            # looks at which unit the datagram named. So unlike movement, chat
            # needs no peer at all -- which is why it worked as an echo long
            # before any of the peer machinery was understood.
            # WARNING: NEVER ECHO A BLANK NAME. In a BATTLE the client takes chat
            # arm 3 (0x611D5A12), which reads the sender's names out of the
            # GROUP member-info blob at blob+0x1C / blob+0x2D -- and our blob
            # (cmd 0xBE) leaves both zero, so the submit arrives `unnamed`.
            # Echoing that straight back gave the client a cmd 18 whose name
            # field is empty, and the line never appeared on screen: live
            # 2026-09-08 a player typed "YEAH NO CHAT STILL" while the
            # server logged it, echoed it, and the client ACKED the echo.
            # We know who is on this channel regardless of what the record
            # says, so use that when the record's name is blank.
            _name, _nsrc = warmap.chat_echo_name(_c["name1"], _c["name2"], addr[0])
            if _nsrc != "the submit":
                log(f"[udp {addr[0]}:{addr[1]}]   the submit carried NO name "
                    f"(chat arm 3 reads them from the group member-info blob "
                    f"at +0x1C/+0x2D, which we serve as zeros) -- echoing as "
                    f"{_name!r} from {_nsrc}")
            _line = fmoworld.record_chat(_c["text"], _name)
            chan.pending.append(_line)
            _heard = [o for o in rooms.room_mates(chan)]
            for _o in _heard:
                _o.pending.append(_line)
            log(f"[udp {addr[0]}:{addr[1]}]   -> cmd {fmoworld.CMD_CHAT} to the "
                f"sender as record {chan.tx_base + len(chan.pending) - 1}"
                + (f" and relayed to {len(_heard)} other client(s) in the room: "
                   + ", ".join(f"{o.addr[0]}:{o.addr[1]}" for o in _heard)
                   if _heard else
                   " -- nobody else is in this room, so echoing to the sender "
                   "IS the whole relay"))

    # A pure ACK: no records of our own (FROM == TO), and +0x20 carries the
    # client's own TO so it can retire its backlog. flag=0 is deliberate --
    # 0x61070902 lets the peer ADOPT our index base only when +0x0B is 0 and
    # it has not adopted one already.
    # WARNING: A PROBE, off by default. The first content we have ever sent this scene.
    # cmd 18 is the client's CHAT arm (0x611EBBEF -> 0x611E7480), which formats
    # "<name>: <text>" into the chat sink -- so if a line appears on screen, the
    # world channel is delivering game content, which nothing has yet shown.
    # It is deliberately ONE record, once: this is an experiment, and a repeated
    # unknown is harder to read than a single one.
    if udpconfig.UDP_SAY and not chan.said and chan.seen >= udpconfig.UDP_SAY_AFTER:
        chan.pending.append(fmoworld.record_chat(udpconfig.UDP_SAY, udpconfig.UDP_SAY_NAME))
        chan.said = True
        log(f"[udp {addr[0]}:{addr[1]}] -> PROBE queued: cmd "
            f"{fmoworld.CMD_CHAT} chat as our record {chan.tx_base} -- it is "
            f"RESENT every datagram until the client's ack (+0x20) passes it")

    # WARNING: THE POP. One record, once -- the same discipline as the chat probe: a
    # repeated unknown is harder to read than a single one, and this one MUTATES
    # client state (the entity map) rather than printing a line, so a duplicate
    # would take the UPDATE arm at 0x611EB2CA instead of creating.
    # KEY: THE GROUP CHANNEL'S ONE RECORD: a self-POP whose body IS the member
    # record the Group Commands window reads (see GROUP_POP). Sent once, before
    # the hello-ack, on the group channel only; the world channels never see it.
    if (groupchannel.GROUP_POP and chan.key == groupchannel.GROUP_KEY
            and not getattr(chan, "group_popped", False)):
        # KEY:KEY: THE MEMBER-INFO BLOB IS A cmd 0xBE RECORD, NOT cmd 7 (live probe
        # 2026-09-06 02:xx: fmogroupprobe.py showed the self-peer EXISTS with
        # key == character id 1 and MATCHES, but peer+0x1AF9 (the blob the Group
        # Commands updater reads for the leader flag) is NULL). In the GROUP
        # peer class the record dispatcher 0x611E5980 maps cmd 7 -> case 0
        # (0x611E5480, a movement/world record keyed by body+0x04 -- no blob),
        # cmd 0xBE -> case 7 (0x611E56B0, the POP handler that set-blobs), and
        # cmd 0xBF -> case 8 (update-blob, which FAILS on a NULL blob). So the
        # record that ATTACHES the blob is 0xBE: 0x611E56B0 looks the peer up by
        # body+0x00, and for the self-peer (body+0x00 == the manager's own id)
        # calls set-blob 0x611E48F0, which allocates and copies the record body
        # verbatim into peer+0x1AF9. The updater 0x6116DA70 then reads
        # blob+0x50 == 1 (leader) and blob+0x54 bits. blob+N == body+N, so the
        # leader byte is body+0x50 -- exactly the offset the first (cmd-7)
        # attempt used, under the wrong command.
        _cid = None
        for _k, _c in peers.items():
            if (isinstance(_k, tuple) and len(_k) == 2 and _k[0] == addr[0]
                    and getattr(_c, "char_id", None)):
                _cid = _c.char_id
                break
        _guid = groupchannel.GROUP_POP_KEY or _cid or (popsweep.POP[0] if popsweep.POP else 1)
        _body = bytearray(0x1C8)
        struct.pack_into("<I", _body, 0x00, _guid)             # peer key -> peer+0x10
        _lead, _lead_why = groupchannel.group_leader_for(addr[0])
        struct.pack_into("<I", _body, groupchannel.GROUP_POP_LEADER_OFF, _lead)
        struct.pack_into("<I", _body, groupchannel.GROUP_POP_FLAGS_OFF, groupchannel.GROUP_POP_FLAGS)
        # KEY: THE NAMES, and they are the same two fields the CHAT path already
        # had to work around. blob+0x1C / blob+0x2D are the member's names:
        # chat arm 3 (0x611D5A12) reads a battle sender's name out of them, and
        # `chat_echo_name` exists ONLY because we served zeros here, so an
        # in-battle line arrived `unnamed` and never rendered (live 2026-09-08).
        # WARNING: Serving them blank ALSO shows on screen: Member Details lists the
        # pilot as "-" (live, 2026-09-09). Same zeros, two symptoms, and the
        # chat workaround masked the second one for a day.
        # Filling them here is the fix at the source; chat_echo_name stays as
        # the belt-and-braces for records that genuinely carry no name.
        _n1, _n2, _nsrc = popnames.pop_names_for(addr[0])
        for _off, _txt in ((groupchannel.GROUP_POP_NAME1_OFF, _n1),
                           (groupchannel.GROUP_POP_NAME2_OFF, _n2)):
            _s = (_txt or "").encode("cp932", "replace")[:groupchannel.GROUP_NAME_LEN - 1]
            _body[_off:_off + len(_s)] = _s
        chan.pending.append(fmoworld.record(groupchannel.GROUP_POP_CMD, bytes(_body)))
        chan.group_uid = _guid          # its own member id (cmd 191 updates)
        chan.group_popped = True        # NOT chan.popped: the NPC pops key off that
        log(f"[udp {addr[0]}:{addr[1]}] -> GROUP MEMBER-INFO queued: cmd "
            f"{groupchannel.GROUP_POP_CMD:#x} body+0x00={_guid} (peer key == character id "
            f"{_cid!r}) body+0x{groupchannel.GROUP_POP_LEADER_OFF:X}={_lead} ({_lead_why}) "
            f"body+0x{groupchannel.GROUP_POP_FLAGS_OFF:X}={groupchannel.GROUP_POP_FLAGS:#x} (status), {len(_body)}B, "
            f"names +0x{groupchannel.GROUP_POP_NAME1_OFF:X}/{groupchannel.GROUP_POP_NAME2_OFF:X} = "
            f"{_n1!r}/{_n2!r} (from {_nsrc}), {len(_body)}B, "
            f"as record {chan.tx_base + len(chan.pending) - 1}. Handler 0x611E56B0 "
            f"set-blobs it to peer+0x1AF9; updater 0x6116DA70 reads blob+0x50==1. "
            f"KEY: blob+0x50 IS WHY 'Leave Battle Group' greys: 0x6116DBEB enables "
            f"Change Leader / Kick / Disband / Edit Comment for a LEADER and "
            f"0x6116DC5A enables Sortie Setting / LEAVE for a NON-leader -- SE's "
            f"design is that a leader DISBANDS. FMO_UDP_GROUP_LEADER=0 un-greys "
            f"Leave for a solo test. Bar: the member is named, not '-'.")
    # A battle-group channel ("...group") carries no world: no self-POP, and
    # therefore none of the NPC/relook pops that key off chan.popped.
    if (popsweep.POP and not chan.popped and chan.seen >= popself.POP_AFTER
            and not (chan.key and chan.key.endswith(b"group"))):
        uid, utype = popsweep.POP
        # WARNING: PLAN 1.2: the battle self-POP override (see POP_BATTLE above).
        # It bypasses the UnitType sweep on purpose -- the sweep is a lobby
        # search tool, and stepping it here would make the battle test
        # unrepeatable across sorties.
        _battle_pop = bool(battlepop.POP_BATTLE and chan.key
                           and chan.key.endswith(b"battle"))
        if _battle_pop:
            uid, utype = battlepop.POP_BATTLE
            _which = None
        # WIRE IDS: the self unit is the id the client selected (the latched
        # char id), not the fixed POP id -- see CHAR_WIRE_BASE.
        uid = chan.self_unit() if charlist.CHAR_WIRE_BASE and chan.char_id and \
            chan.char_id >= charlist.CHAR_WIRE_BASE else uid
        if _battle_pop:
            log(f"[udp {addr[0]}:{addr[1]}] BATTLE POP OVERRIDE "
                f"(FMO_UDP_POP_BATTLE): this battle channel pops "
                f"UnitID={uid:#010x} UnitType={utype} instead of FMO_UDP_POP's "
                f"{popsweep.POP[0]:#010x}:{popsweep.POP[1]}. Bar: a unit that DRAWS and takes "
                f"input; register-but-invisible points at the mission block "
                f"(PLAN 1.3), not at more POP bytes.")
        else:
            utype, _which = popsweep.next_pop_type(utype)
        if _which:
            log(f"[udp {addr[0]}:{addr[1]}] UnitType sweep: this channel pops "
                f"type {utype} (candidate {_which[0]} of {_which[1]}). "
                f"Move -> Change Room for the next one -- no relaunch. Look for "
                f"a CHARACTER appearing, not for the log to change.")
        try:
            (_mf, _ms), _mw = popsweep.next_pop_model()
            _rf, _rs, _rsrc = popself.pop_model_for(addr[0])
            if _rf is not None:
                _mf, _ms = _rf, _rs
                log(f"[udp {addr[0]}:{addr[1]}] MODEL FROM ROSTER: "
                    f"body+0x8E={_mf:#04x} body+0x8B={_ms} ({_rsrc}) -- "
                    f"0x611ED660 takes the kind from the high nibble and, for "
                    f"kind 6 ONLY, the sub from +0x8B. A blank or wrong "
                    f"character is a RESULT: it says kind 6 is not the human "
                    f"table.")
            elif _rsrc:
                log(f"[udp {addr[0]}:{addr[1]}] MODEL FROM ROSTER asked for but "
                    f"{_rsrc} -- falling back to FMO_UDP_POP_MODEL")
            if _mw or _mf is not None:
                log(f"[udp {addr[0]}:{addr[1]}] MODEL selector: "
                    f"body+0x8E={_mf if _mf is None else hex(_mf)} "
                    f"body+0x8B={_ms}"
                    + (f" (candidate {_mw[0]} of {_mw[1]})" if _mw else "")
                    + ". 0x611ED660 reads +0x8E's bit 3 to arm the selector and "
                    f"its high nibble to pick the kind; kind 6 then reads "
                    f"+0x8B. Zero means 'fall back to UnitType', which is what "
                    f"every run before this sent.")
            _pos, _pw, _possrc = popsweep.next_pop_pos(rooms.WORLD_MAPS.get(addr[0]))
            if battlepop.BATTLE_POS and chan.key and chan.key.endswith(b"battle"):
                # The map-keyed table has no mission-map row and WORLD_MAPS
                # still holds the LOBBY MapNo here, so without this the pilot
                # spawns at lobby coordinates inside a mission map -- live
                # 2026-09-08 that was (0.52, 3.11, 9.41) in a map whose extent
                # is 0..128, i.e. on the western edge, and the client withdrew
                # him for being out of the battle area.
                _possrc = (f"FMO_BATTLE_POS (battle channel; the map table's "
                           f"{_pos} came from the LOBBY MapNo "
                           f"{rooms.WORLD_MAPS.get(addr[0])!r} and is not a mission-"
                           f"map position)")
                _pos, _pw = battlepop.BATTLE_POS, None
            if not chan.moved_at:
                # WARNING: Seed this player's ROOM position with the spawn actually
                # served (the per-map POS_MAP row), not the global POP_POS the
                # channel was constructed with (2026-09-05). Until a cmd 240
                # arrives the room relay pops this player into other scenes at
                # chan.pos -- and with a spawn table that was the wrong map's
                # point (40,5,40 in every lobby).
                chan.pos = tuple(_pos[:3])
            log(f"[udp {addr[0]}:{addr[1]}] POP position {_pos} from "
                f"{_possrc}. KEY: THIS is what places the player -- measured "
                f"2026-08-28, not PilotPos.")
            if _pw:
                log(f"[udp {addr[0]}:{addr[1]}] POSITION sweep: this channel "
                    f"pops at {_pos} (candidate {_pw[0]} of {_pw[1]}). "
                    f"Move -> Change Room for the next one -- no relaunch. "
                    f"Look for the pilot standing somewhere LEGAL; (0,5,0) "
                    f"is the map origin, a wall in 121 and a void in 101.")
            _n1, _n2, _nsrc = popnames.pop_names_for(addr[0])
            _sx = _lk = None
            if utype != 4:
                # The sex byte (+0x7A) and the look block (+0x188..) are
                # TYPE-4 HUMAN offsets; what those bytes mean to any other
                # unit class is undecoded, so this POP sends none of them --
                # an unknown byte in a POP body is exactly how the look block
                # broke lobby world entry (seen 2026-09-04).
                #
                # WARNING: BUT IT MUST NOT CLEAR THE CHANNEL'S PINNED COPY. Those two
                # fields exist for the ROOM RELAY -- the comment three lines
                # below says so -- and they describe WHO THIS PLAYER IS, which
                # does not stop being true because they sortied. Wiping them
                # here meant a player who entered a battle was popped into
                # everybody else's lobby with NO appearance, and the client
                # drew its default human: measured live 2026-09-08, "after
                # Molly Test returns from combat, she shows as the stewardess
                # NPC". `_sx`/`_lk` stay None so THIS pop still withholds them.
                log(f"[udp {addr[0]}:{addr[1]}] UnitType {utype} != 4: the "
                    f"sex byte (+0x7A) and look block (+0x188..) are human "
                    f"offsets and are WITHHELD from this POP -- but the "
                    f"channel keeps its pinned copy (model="
                    f"{chan.type4_model!r}, look={'set' if chan.type4_look else 'none'}) "
                    f"for the room relay, which still has to dress this "
                    f"player in everybody else's scene.")
            else:
                _sx, _sxsrc = popnames.pop_sex_for(addr[0])
                # Pin it to the channel: the room relay pops THIS player into
                # other scenes and must use the same model, or appearance
                # disagrees.
                chan.type4_model = _sx
                if _sx is not None:
                    log(f"[udp {addr[0]}:{addr[1]}] body+0x7A={_sx}"
                        + f" from {_sxsrc}"
                        + " -- 0x611E7190 turns that into "
                        f"0x611FCE50(4, {0 if _sx == 1 else 1}, -1). TWO "
                        f"models exist for a human; this picks which.")
                # The other four appearance fields, pinned to the channel for
                # the same reason as the sex byte: the room relay pops THIS
                # player into everybody else's scene and must dress them
                # identically.
                _lk, _lksrc = poplook.pop_look_for(addr[0])
                chan.type4_look = _lk
                log(f"[udp {addr[0]}:{addr[1]}] look={_lk} from {_lksrc} -- "
                    f"body+0x188 size, +0x189 build, +0x18A face (part kind "
                    f"0x24 slot 0), +0x18C uniform (kind 0x14 slot 1). "
                    f"0x611E7190 dresses the human from these; all-zero is "
                    f"the NPC fallback.")
            # WARNING: CREATE BARE, DRESS LATE. See POP_LOOK_DEFER: a populated part
            # slot is what makes the client's model refresh dereference
            # `unit+0xEA2`, and during scene load that pointer may still be the
            # NULL its constructor left. So the create POP carries no look and
            # a second cmd 7 (the UPDATE arm) dresses the unit once the scene
            # has settled. WARNING: It narrows the window; it does not close it.
            _lk_now = _lk
            if _lk and poplook.POP_LOOK_DEFER > 0:
                _lk_now = None
                chan.look_due = time.time() + poplook.POP_LOOK_DEFER
                chan.look_sent = False
            _ck = popself.pop_client_kind_for(chan)
            if _ck:
                log(f"[udp {addr[0]}:{addr[1]}] self-pop client_kind={_ck} "
                    f"(POP body+0x00) -- battle scene 4's state-3 handler "
                    f"0x611D4281 sets selfpeer+0x10DC=2 only for kind 3; lobby "
                    f"scenes ignore it. FMO_UDP_POP_CLIENT_KIND={popself.POP_CLIENT_KIND_KNOB}")
            _nat, _natsrc = popnation.pop_nation_for(addr[0])
            if _nat:
                log(f"[udp {addr[0]}:{addr[1]}] self-pop nation={_nat} "
                    f"(POP body+0x7C -> entity+0x1C3) -- the 0xE060 cast switch "
                    f"runs the OCU cast on 1, USN on 2, skips all on anything "
                    f"else. {_natsrc}")
            elif popnation.POP_NATION_KNOB:
                log(f"[udp {addr[0]}:{addr[1]}] self-pop nation NOT set: "
                    f"{_natsrc} -- the cast will stay skipped (E060 reads 0)")
            # VERIFIED:KEY: THE WANZER'S PARTS (body+0x8C). Battle channels only, and
            # only for a UnitType that reaches the dresser -- see BATTLE_PARTS.
            _pt, _ptsrc = [], None
            if _battle_pop and utype != 4:
                _pt, _ptsrc = popparts.pop_parts_for(addr[0])
                if _pt and _mf:
                    # body+0x8E is BOTH record 0's kind and the model-selector
                    # flags; record_pop refuses the pair. Drop the selector, not
                    # the parts: the selector's kind falls back to UnitType
                    # anyway, which is where we already were.
                    log(f"[udp {addr[0]}:{addr[1]}] WARNING: MODEL selector "
                        f"body+0x8E={_mf:#04x} DROPPED: it is the same byte as "
                        f"part record 0's kind, and this POP is carrying parts. "
                        f"0x611ED660 falls back to UnitType {utype}, which is "
                        f"what every run before this did anyway.")
                    _mf = _ms = None
                if _pt:
                    log(f"[udp {addr[0]}:{addr[1]}] BATTLE PARTS: "
                        + ", ".join(f"item {i}={k:#04x}:{d}" for i, k, d in _pt)
                        + f" from {_ptsrc}. 0x611F70A0 reads eleven records at "
                        f"body+{fmoworld.POP_PARTS:#x} stride "
                        f"{fmoworld.POP_PART_STRIDE:#x} and equips record i "
                        f"into part slot {fmoworld.POP_PART_ORDER}[i]; a record "
                        f"of zero is SKIPPED SILENTLY, which is what "
                        f"`--wanzer` measured as 0/12. Bar: fmocrash.py "
                        f"--wanzer reports NON-ZERO part slots.")
                else:
                    log(f"[udp {addr[0]}:{addr[1]}] BATTLE PARTS: none sent "
                        f"({_ptsrc}) -- the wanzer will be created UNDRESSED, "
                        f"exactly as measured 2026-09-08.")
            elif _battle_pop:
                log(f"[udp {addr[0]}:{addr[1]}] BATTLE PARTS WITHHELD: "
                    f"UnitType 4 goes to the human dresser 0x611E7190 and "
                    f"never calls 0x611F70A0. Arm FMO_UDP_POP_BATTLE with a "
                    f"type in 0/1/2/3/5/6 for the parts to be read at all.")
            # The friend/foe byte (body+0x27 -> unit+0x80) follows the pilot's
            # nation on a BATTLE pop; lobby human pops keep it 0 because the
            # human creators key the model on it. See battle_side_for.
            _side, _sidesrc = (popnation.battle_side_for(addr[0]) if _battle_pop
                               else (None, "lobby pop, side stays 0"))
            if _battle_pop:
                log(f"[udp {addr[0]}:{addr[1]}] BATTLE SIDE: body+0x27 = "
                    f"{_side if _side is not None else 'unset (0)'} -- {_sidesrc}")
            chan.pop_args = dict(
                unit_type=utype, name1=_n1, name2=_n2, pos=_pos,
                model_flags=_mf, model_sub=_ms, type4_model=_sx, client_kind=_ck,
                nation=_nat, parts=(_pt or None), side=_side)
            # KEY: THE OWNER (body+0x2C -> unit+0x30). The freeze check 0x61051BB0
            # runs every unit tick: a UnitID >= 10 whose owner is not a
            # connected peer (state 2) is FROZEN -- no driving, no spawn init,
            # no cmd 23/24. Ids < 10 skip the check but are never networked
            # (0x6106659C), which is why UnitID 1 drove and never moved on
            # anyone else's screen, and why 0x1001 with owner 0 could not
            # drive (live 09-27). A pilot's own unit is owned by itself.
            if uid >= 10 and rooms._is_battle_chan(chan):     # battle units only
                chan.pop_args["extra"] = {
                    battlepop.POP_AI_OWNER: struct.pack("<I", uid & 0xFFFFFFFF)}
            chan.pending.append(fmoworld.record_pop(
                uid, look=_lk_now, **chan.pop_args))
        except ValueError as e:
            log(f"[udp {addr[0]}:{addr[1]}] \WARNING: POP REFUSED BY OUR OWN "
                f"GUARD, nothing sent: {e}")
            chan.popped = True
        else:
            chan.popped = True
            log(f"[udp {addr[0]}:{addr[1]}] -> PROBE queued: cmd "
                f"{fmoworld.CMD_POP} POP UnitID={uid:#010x} UnitType={utype} "
                f"at {_pos} "
                f"names {_n1!r}/{_n2!r} ({_nsrc}) "
                f"as our record {chan.tx_base}")
            log(f"[udp {addr[0]}:{addr[1]}]   the 0x0153 setup block carried "
                f"unit ids {[f'{i:#010x}' for i in room.SETUP_UNIT_IDS] or 'NONE'} "
                f"-- 0x61003120 only spawns an id that is in BOTH")
            log(f"[udp {addr[0]}:{addr[1]}]   read the result with "
                f"`fmocrash.py --live`: success is the entity map going to "
                f"1 key, NOT anything appearing on screen")

    # VERIFIED:KEY: THE BATTLE DRESS PROBE (FMO_BATTLE_DUMMY). Once per battle channel,
    # after the self-POP, pop ONE extra unit that is NOT the self unit. See
    # BATTLE_DUMMY: the self unit is forced to the wreck model (kind 40) by
    # 0x611ED660 because its battle-map char reads status 3, and a unit id the
    # battle map does not hold misses that lookup and keeps our UnitType. This
    # is the only way to exercise the dressing chain without first breaking the
    # client_kind circle.
    if (battlepop.BATTLE_DUMMY and not battlepop.BATTLE_DUMMY_AI and chan.popped
            and not chan.dummy_popped
            and chan.key and chan.key.endswith(b"battle")):
        chan.dummy_popped = True        # once, whatever happens below
        _duid, _dutype, _dpos = battlepop.BATTLE_DUMMY
        _selfuid = (battlepop.POP_BATTLE or popsweep.POP or (None,))[0]
        if _duid == _selfuid:
            log(f"[udp {addr[0]}:{addr[1]}] WARNING: BATTLE DUMMY REFUSED: id "
                f"{_duid:#010x} is the SELF unit's id, so 0x611ED660's "
                f"battle_map.find HITS, reads char+0x1C=3 and forces the wreck "
                f"model -- the probe would measure the exact thing it exists to "
                f"step around. Give it a DIFFERENT id.")
        else:
            _dp = _dpos or ((chan.pop_args or {}).get("pos")
                            or popsweep.next_pop_pos(rooms.WORLD_MAPS.get(addr[0]))[0])
            _dparts, _dpsrc = popparts.pop_parts_for(addr[0])
            try:
                _drec = fmoworld.record_pop(
                    _duid, unit_type=_dutype, pos=_dp,
                    client_kind=battlepop.BATTLE_DUMMY_KIND,
                    name1="Test", name2="Wanzer",
                    nation=(battlepop.BATTLE_DUMMY_NATION or None),
                    # the knob still wins when set; otherwise the dummy is an
                    # ENEMY: the other side of the pilot's own nation
                    side=(battlepop.BATTLE_DUMMY_SIDE or popnation.enemy_side_for(addr[0])[0]),
                    parts=(_dparts or None))
            except ValueError as e:
                log(f"[udp {addr[0]}:{addr[1]}] WARNING: BATTLE DUMMY REFUSED BY "
                    f"OUR OWN GUARD, nothing sent: {e}")
            else:
                chan.pending.append(_drec)
                chan.dummy_id = _duid
                chan.dummy_pos = tuple(_dp[:3]) if _dp else None
                # an enemy whose destruction pays (battle_kills)
                referee.battle_state(referee.bkey(addr[0])).setdefault("enemies", set()).add(_duid)
                if battlepop.BATTLE_DUMMY_KILL > 0:
                    chan.dummy_kill_due = time.time() + battlepop.BATTLE_DUMMY_KILL
                    chan.dummy_kill_sent = False
                    if battlepop.BATTLE_DUMMY_KIND != 0:
                        log(f"[udp {addr[0]}:{addr[1]}] WARNING: FMO_BATTLE_DUMMY_KILL "
                            f"is armed but FMO_BATTLE_DUMMY_CLIENT_KIND="
                            f"{battlepop.BATTLE_DUMMY_KIND}: the destroy arm 0x611EEA23 fires "
                            f"only when the unit's entity status +0x1C == 1 (ALIVE), "
                            f"which a POP gets ONLY from client_kind 0 (0x611D4530: "
                            f"0->1; {battlepop.BATTLE_DUMMY_KIND}->"
                            f"{ {0:1,1:0,2:2,3:3}.get(battlepop.BATTLE_DUMMY_KIND) }). The DEPOP "
                            f"will be sent and SILENTLY do nothing. Set "
                            f"FMO_BATTLE_DUMMY_CLIENT_KIND=0.")
                log(f"[udp {addr[0]}:{addr[1]}] -> BATTLE DUMMY queued: cmd "
                    f"{fmoworld.CMD_POP} POP UnitID={_duid:#010x} "
                    f"UnitType={_dutype} client_kind={battlepop.BATTLE_DUMMY_KIND} at "
                    f"{_dp}, parts from {_dpsrc}, nation "
                    f"{battlepop.BATTLE_DUMMY_NATION or 'unset (as before)'}"
                    f"{' = the ENEMY army: bar = it lists under Enemy Units' if battlepop.BATTLE_DUMMY_NATION else ''}, side "
                    f"{battlepop.BATTLE_DUMMY_SIDE or popnation.enemy_side_for(addr[0])[0]}"
                    f"{' (FMO_BATTLE_DUMMY_SIDE)' if battlepop.BATTLE_DUMMY_SIDE else ' (the other side of the pilot: ' + popnation.enemy_side_for(addr[0])[1] + ')'}"
                    f" (body+0x27 -> unit+0x80: the friend/foe byte), as our record "
                    f"{chan.tx_base + len(chan.pending) - 1}. This id is NOT in "
                    f"the battle map, so 0x611ED660 takes the `je 0x611ed728` "
                    f"miss arm and builds model kind {_dutype} instead of the "
                    f"wreck (0x28) the self unit gets. Bar: `--wanzer` shows a "
                    f"SECOND unit, model class {_dutype}, with NON-ZERO part "
                    f"slots. It is not drivable and is not meant to be.")

    # VERIFIED:KEY: THE ENEMY SQUAD (FMO_BATTLE_DUMMY_AI + FMO_BATTLE_ENEMIES): see
    # battle_squad_for. Popped once per battle channel, after the self-POP.
    if (battlepop.BATTLE_DUMMY and battlepop.BATTLE_DUMMY_AI and chan.popped
            and not getattr(chan, "squad_popped", False)
            and rooms._is_battle_chan(chan)):
        chan.squad_popped = True
        if squad._BATTLE_ENEMIES_ERR:
            log(f"[udp {addr[0]}:{addr[1]}] WARNING: {squad._BATTLE_ENEMIES_ERR} -- one enemy")
        _selfuid = (battlepop.POP_BATTLE or popsweep.POP or (None,))[0] or 0
        _base = (battlepop.BATTLE_DUMMY[2] or (chan.pop_args or {}).get("pos")
                 or popsweep.next_pop_pos(rooms.WORLD_MAPS.get(addr[0]))[0])
        _snat = battlepop.BATTLE_DUMMY_NATION or {1: 2, 2: 1}.get(
            popnation.pop_nation_for(addr[0])[0], 2)
        _sparts = popparts.pop_parts_for(addr[0])[0]
        _sq, _och = squad.battle_squad_for(chan, _base, _snat, _sparts)
        _mine = _sq["owner"] == referee.bkey(addr[0])
        _owner = _selfuid if _mine else (chan.alias_for(_och.addr) if _och else 0)
        _bst = referee.battle_state(referee.bkey(addr[0]))
        _bst["squad"] = _sq
        _bst.setdefault("enemies", set())
        for _i, (_eid, _epos) in enumerate(zip(_sq["ids"], _sq["pos"])):
            if _eid == _selfuid or _eid in _sq["dead"]:
                continue
            try:
                chan.pending.append(fmoworld.record_pop(
                    _eid, unit_type=battlepop.BATTLE_DUMMY[1], pos=_epos, client_kind=1,
                    name1="Enemy", name2=str(_i + 1), nation=_sq["nation"],
                    side=popnation.enemy_side_for(addr[0])[0],
                    parts=(_sq["parts"] or None),
                    extra={battlepop.POP_AI_OWNER: struct.pack("<I", _owner),
                           battlepop.POP_AI_BRAIN: struct.pack("<I", battlepop.BATTLE_DUMMY_AI)}))
            except ValueError as e:
                log(f"[udp {addr[0]}:{addr[1]}] WARNING: SQUAD POP {_eid:#x} REFUSED "
                    f"BY OUR OWN GUARD: {e}")
                continue
            _bst["enemies"].add(_eid)
        chan.dummy_id = _sq["ids"][0]
        chan.dummy_pos = tuple(_sq["pos"][0][:3])
        log(f"[udp {addr[0]}:{addr[1]}] -> ENEMY SQUAD: {len(_sq['ids'])} AI "
            f"wanzer(s) {', '.join(f'{i:#x}' for i in _sq['ids'])} (nation "
            f"{_sq['nation']}, brain {battlepop.BATTLE_DUMMY_AI}) round {tuple(_base[:3])}; "
            + ("THIS client OWNS them (body+0x2C = its own id "
               f"{_selfuid:#x}): it runs the brains, its cmd 23/24/30/29 about "
               "them are relayed to the rest of the room."
               if _mine else
               f"owned by {_sq['owner']} (body+0x2C = its alias {_owner:#x} "
               "here): network copies, moved by the owner's relayed records."))

    # VERIFIED: THE DUMMY KILL (FMO_BATTLE_DUMMY_KILL). Once, this many seconds after the
    # dummy was popped, DESTROY it with a cmd-8 DEPOP status 2 on the battle self
    # stream -> the client's wreck/explosion (0x611EEAB1 -> 0x610BE9B0). This is
    # the server proving it can kill an enemy on the battlefield; the destroy arm
    # needs the unit ALIVE (entity +0x1C == 1), which is client_kind 0.
    if (battlepop.BATTLE_DUMMY_KILL > 0 and chan.dummy_id and not chan.dummy_kill_sent
            and chan.dummy_kill_due and time.time() >= chan.dummy_kill_due
            and chan.key and chan.key.endswith(b"battle")):
        chan.dummy_kill_sent = True
        _st = battlepop.BATTLE_DUMMY_KILL_STATUS
        try:
            _krec = fmoworld.record_depop(chan.dummy_id, status=_st)
        except ValueError as e:
            log(f"[udp {addr[0]}:{addr[1]}] WARNING: DUMMY KILL REFUSED BY OUR "
                f"OWN GUARD, nothing sent: {e}")
        else:
            chan.pending.append(_krec)
            _what = ({fmoworld.DEPOP_DESTROYED: "wreck/explosion (0x610BE9B0) -- "
                      "GATED on [unit+0xE99]==0 (an attached effect); a dressed "
                      "wanzer has one, so this usually no-ops",
                      fmoworld.DEPOP_REMOVE: "REMOVE -- the unit leaves the scene "
                      "and is freed (VANISHES); no effect gate, only needs the "
                      "char found + char+0x24 set"}
                     .get(_st, f"status {_st}"))
            log(f"[udp {addr[0]}:{addr[1]}] -> DUMMY KILL queued: cmd "
                f"{fmoworld.CMD_DEPOP} DEPOP UnitID={chan.dummy_id:#010x} "
                f"status {_st}, {battlepop.BATTLE_DUMMY_KILL:g}s after the pop, as record "
                f"{chan.tx_base + len(chan.pending) - 1}: {_what}. Bar: the enemy "
                f"wanzer is destroyed (status 3 = vanishes, status 2 = explodes "
                f"IF effect-free). Enrollment is confirmed (count=2, id 0x2222).")

        # VERIFIED:KEY: THE GATE POP (FMO_BATTLE_GATE_POP). A second cmd-7 on the SAME unit
    # id carrying client_kind 3, queued right behind the alive self-POP so the
    # client processes them in order: record N creates the ALIVE char and the
    # dressed wanzer, record N+1 sets +0x10DC=2 and returns at the char lookup
    # (char+0x20 == 0 -> 0x611D4520[0] = 0x611D44AB, the `ret` tail). See
    # BATTLE_GATE_POP for why this is not the 09-04 un-poison.
    if (battlepop.BATTLE_GATE_POP and chan.popped and not chan.gate_popped
            and chan.key and chan.key.endswith(b"battle") and chan.pop_args):
        chan.gate_popped = True         # once, whatever happens below
        _guid = chan.self_unit() if popsweep.POP else (battlepop.POP_BATTLE or (None,))[0]
        _gargs = dict(chan.pop_args, client_kind=battlepop.BATTLE_GATE_KIND)
        if chan.pop_args.get("client_kind") == battlepop.BATTLE_GATE_KIND:
            log(f"[udp {addr[0]}:{addr[1]}] GATE POP SKIPPED: the self-POP "
                f"already carried client_kind {battlepop.BATTLE_GATE_KIND}, so it set "
                f"+0x10DC itself and this would only duplicate a peer. Set "
                f"FMO_UDP_POP_CLIENT_KIND=0 -- an ALIVE self-pop is the whole "
                f"point of the gate pop.")
        else:
            try:
                _grec = fmoworld.record_pop(_guid, **_gargs)
            except ValueError as e:
                log(f"[udp {addr[0]}:{addr[1]}] WARNING: GATE POP REFUSED BY OUR "
                    f"OWN GUARD, nothing sent: {e}")
            else:
                chan.pending.append(_grec)
                log(f"[udp {addr[0]}:{addr[1]}] -> GATE POP queued: a SECOND "
                    f"cmd {fmoworld.CMD_POP} on UnitID={_guid:#010x} with "
                    f"client_kind={battlepop.BATTLE_GATE_KIND} (the self-pop carried "
                    f"{chan.pop_args.get('client_kind')}), as our record "
                    f"{chan.tx_base + len(chan.pending) - 1}. 0x611D4281 sets "
                    f"+0x10DC=2 BEFORE the char lookup; the char already "
                    f"exists with +0x20 == 0, so 0x611D4520[0] returns at "
                    f"0x611D44AB without rewriting char+0x1C or rebuilding the "
                    f"visual. Bar: `--live` shows setup state +0x1C4 = 4 or 6 "
                    f"AND char+0x1C still 1 (ALIVE). If +0x1C4 stays 3 the new "
                    f"peer did not become bm+0x48 and this route is dead.")

    # PARTIAL: BM cmd 138 -- BATTLE START (FMO_BATTLE_START). Once per battle channel,
    # behind the self-POP (and the gate pop, if armed) so the scene is up when
    # the banner lands. See fmoworld.record_battle_start.
    if (battleend.BATTLE_START and chan.popped and chan.key
            and chan.key.endswith(b"battle")
            and not referee.battle_state(referee.bkey(addr[0]))["start_sent"]):
        _bst = referee.battle_state(referee.bkey(addr[0]))
        _bst["start_sent"] = True
        try:
            # WARNING: cmd 138's +0x00 is WRITTEN to block+0x48 (0x611EFB2B,
            # unconditionally). Sending 0 there wiped the sortie's battle
            # start right after the Battle Review recorder had written its
            # header, so every frame measured from 0 and clamped (seen in
            # the recorded file: header start correct, all 20 frame times 0).
            # Repeat the sortie's stamp; a sortie without one (the 0x014E
            # push) gets "now", slightly early rather than late.
            _stamp = ((_bst.get("start_unix") or int(time.time()) - 5)
                      if missionblock.BATTLE_START_TIME else 0)
            _bsr = fmoworld.record_battle_start(battleend.BATTLE_START,
                                                start_gametime=_stamp)
        except ValueError as e:
            log(f"[udp {addr[0]}:{addr[1]}] WARNING: FMO_BATTLE_START={battleend.BATTLE_START} "
                f"refused: {e}")
        else:
            chan.pending.append(_bsr)
            if battleend.OBJECTIVE:
                for _b in referee.objective_tick(_bst, chan, time.time()):
                    referee.hud_banner(chan, _b)
            log(f"[udp {addr[0]}:{addr[1]}] -> BM cmd "
                f"{fmoworld.CMD_BM_BATTLE_START} BATTLE START queued, reason "
                f"{battleend.BATTLE_START} = {fmoworld.BATTLE_START_REASONS[battleend.BATTLE_START]!r}, "
                f"as our record {chan.tx_base + len(chan.pending) - 1}. The battle "
                f"peer's dispatcher 0x611D4750 falls through to 0x611EF2E0 for "
                f"it; arm 0x611EFB11 stamps block+0x48 and posts the banner. "
                f"Bar: '...The battle begins now.' in the message window. If it "
                f"never appears, NO BM message reaches this client.")
    # WARNING: cmd 129 -- ONE objective marker (FMO_BATTLE_OBJECTIVE). Needs the
    # client's clock (from its keepalive) because the deadline is in that
    # clock's milliseconds; until a keepalive has been seen it waits.
    if (battleend.BATTLE_OBJECTIVE and chan.popped and chan.key
            and chan.key.endswith(b"battle")
            and not referee.battle_state(referee.bkey(addr[0]))["objective_sent"]
            and referee.battle_state(referee.bkey(addr[0]))["clock"]):
        _bst = referee.battle_state(referee.bkey(addr[0]))
        _bst["objective_sent"] = True
        _csec, _cusec, _cat = _bst["clock"]
        _cnow_ms = (_csec * 1000 + _cusec // 1000
                    + int((time.time() - _cat) * 1000))
        _opos, _osecs = battleend.BATTLE_OBJECTIVE
        _ouid = (battlepop.POP_BATTLE or popsweep.POP or (1,))[0]
        try:
            _orec = fmoworld.record_objective_marker(
                0, _ouid, _opos, _cnow_ms + _osecs * 1000)
        except ValueError as e:
            log(f"[udp {addr[0]}:{addr[1]}] WARNING: FMO_BATTLE_OBJECTIVE refused: {e}")
        else:
            chan.pending.append(_orec)
            log(f"[udp {addr[0]}:{addr[1]}] -> BM cmd "
                f"{fmoworld.CMD_BM_OBJECTIVE} OBJECTIVE MARKER queued: slot 0, "
                f"unit {_ouid:#x}, at {_opos}, deadline client-clock "
                f"{_cnow_ms + _osecs * 1000} ms ({_osecs}s from a clock "
                f"extrapolated {time.time() - _cat:.1f}s past the last "
                f"keepalive). WARNING: A LAYOUT GUESS past +0x20; the bar is a HUD "
                f"marker with a countdown, and the failure mode is unknown.")

    # WARNING:KEY: PLAN 3.1 -- THE NPC SOURCE RECORDS (FMO_UDP_POP_NPC). Once per LOBBY
    # channel, after the self-POP so the scene is already coming up, pop each
    # configured source id. The cutscene's derivation arm (0x61100100 block C)
    # looks these ids up and builds a visible NPC from each one it finds -- in
    # SCENE 7 (the cutscene) only; the settled lobby, scene 6, never calls the
    # arm (static 2026-09-05), so there these are plain human
    # POPs. Not on a battle channel (the arm's ranges are the lobby cast).
    # See POP_NPC.
    if (npcroster.npc_cast_configured() and chan.popped and not chan.npcs_popped
            and chan.key and not chan.key.endswith(b"battle")
            and not squad.lobby_cast_paused(referee.chan_bkey(chan))):
        _self_pos = ((chan.pop_args or {}).get("pos")
                     or popsweep.next_pop_pos(rooms.WORLD_MAPS.get(addr[0]))[0])
        _n_ok = 0
        _roster, _names, _rwhy = npcroster.roster_for(addr[0])
        chan.npc_roster = (_roster, _names)
        log(f"[udp {addr[0]}:{addr[1]}] NPC cast: {_rwhy}")
        for _uid, _utype, _npos, _cat in _roster:
            _p = _npos if _npos is not None else _self_pos
            try:
                # KEY: FULL BODY, not a stub (seen 2026-09-04: a minimal
                # body black-screened the world) -- _npc_pop_record reuses the
                # self-POP body and applies the per-entry catalogue dress and
                # the targetable bit on top. See its docstring.
                _rec = npcroster._npc_pop_record(_uid, _utype, _npos, _cat, chan,
                                                 _self_pos, _names, addr[0])
            except ValueError as e:
                log(f"[udp {addr[0]}:{addr[1]}] WARNING: NPC SOURCE {_uid:#010x} "
                    f"REFUSED by our own guard, not sent: {e}")
                continue
            chan.pending.append(_rec)
            _n_ok += 1
            _b = _rec[fmoworld.REC_HDR:]
            _bwp, _bwwhy = npcroster._bay_wanzer_parts(_uid, _utype, addr[0])
            _how = (f", catalogue typecode {_cat} = "
                    f"{fmoworld.NPC_CATALOGUE[_cat][0]}/{fmoworld.NPC_CATALOGUE[_cat][1]} "
                    f"face {fmoworld.NPC_CATALOGUE[_cat][3]} uniform "
                    f"{fmoworld.NPC_CATALOGUE[_cat][4]}" if _cat is not None
                    else (f", THE PARKED WANZER: {len(_bwp)} part(s) at "
                          f"body+0x{fmoworld.POP_PARTS:X} from {_bwwhy}"
                          if _bwp else ", full self-POP body (player clone)"))
            log(f"[udp {addr[0]}:{addr[1]}] -> NPC SOURCE queued: cmd "
                f"{fmoworld.CMD_POP} id={_uid:#010x} type={_utype} at {_p} "
                f"(client_kind "
                f"{struct.unpack_from('<I', _b, fmoworld.POP_CLIENT_KIND)[0]}"
                f"{' = NO peer: an entity without a player behind it' if npcroster.POP_NPC_CLIENT_KIND == 1 else ''}"
                f"{_how}"
                f"{', body+0x48 |= 0x10 = listable in /target' if npcroster.POP_NPC_TARGETABLE else ''}) "
                f"as record {chan.tx_base + len(chan.pending) - 1}. In the cutscene "
                f"(scene 7) the state-3 arm 0x61100100 derives an NPC from it; in the "
                f"settled lobby (scene 6) nothing derives -- it stands as a plain "
                f"human POP.")
        chan.npcs_popped = True
        if _n_ok and (poplook.POP_NPC_RELOOK > 0 or poplook.POP_NPC_REPOP > 0):
            chan.npc_relook_due = time.time() + (poplook.POP_NPC_RELOOK or poplook.POP_NPC_REPOP)
            chan.npc_relook_sent = False
        if _n_ok:
            log(f"[udp {addr[0]}:{addr[1]}]   {_n_ok} NPC source(s) sent. If none "
                f"are derived in the CUTSCENE, the scene-7 arm's one state-3 tick "
                f"may have run before these landed (POP_AFTER is {popself.POP_AFTER}), or "
                f"the id is outside its ranges (0x82001000..07, 0x82080500..07, "
                f"0x82080600..). In the SETTLED lobby (scene 6) nothing derives from "
                f"these BY DESIGN -- scene 6 never calls 0x61100100 (static "
                f"2026-09-05); there they are plain human POPs.")

    # VERIFIED: THE CAST-NPC DEFERRED RE-POP (FMO_UDP_POP_NPC_RELOOK). Re-send each cast
    # record once, POP_NPC_RELOOK s after the initial pop, so the update arm
    # 0x611EB2CA rebuilds the type-4 VISUAL in a settled scene. The initial pop
    # registers the entity (script places it, dialogue attributes) but builds no
    # actor at state 3; this is the rebuild. Rebuilt fresh from POP_NPC (same as
    # the initial loop) so a reused record object cannot be re-framed twice.
    if ((poplook.POP_NPC_RELOOK > 0 or poplook.POP_NPC_REPOP > 0) and chan.npcs_popped
            and not chan.npc_relook_sent
            and chan.npc_relook_due and time.time() >= chan.npc_relook_due
            and chan.key and not chan.key.endswith(b"battle")
            and not squad.lobby_cast_paused(referee.chan_bkey(chan))):
        _rl_self = ((chan.pop_args or {}).get("pos")
                    or popsweep.next_pop_pos(rooms.WORLD_MAPS.get(addr[0]))[0])
        _rl_n = 0
        # KEY: RE-RESOLVED, not the roster captured at the first pop: the layout
        # file (the lobby NPC editor) can change between re-pops, and this
        # timer is how an edit reaches a player who is already standing in
        # the lobby. Same host, same zone, so the band cannot change here --
        # only its rows can, and a changed row set is said in the log.
        _roster, _names, _rwhy = npcroster.roster_for(addr[0])
        _prev = getattr(chan, "npc_roster", None)
        if _prev is not None and _prev[0] != _roster:
            log(f"[udp {addr[0]}:{addr[1]}] NPC cast CHANGED since the last pop "
                f"({len(_prev[0])} -> {len(_roster)} rows): {_rwhy}")
            # a moved row: the re-pop below will NOT move a present body
            # (entity+0x44 is outside what the update arm copies) -- a cmd 240
            # on the NPC's own stream does. Both go out; the pop keeps the
            # record set whole for a wiped key, the move relocates a present one.
            _mv = npcroster.npc_moves(_prev[0], _roster)
            if _mv:
                npcroster.npc_move_queue(chan, _mv)
        chan.npc_roster = (_roster, _names)
        for _uid, _utype, _npos, _cat in _roster:
            _p = _npos if _npos is not None else _rl_self
            try:
                _rec = npcroster._npc_pop_record(_uid, _utype, _npos, _cat, chan,
                                                 _rl_self, _names, addr[0])
            except ValueError:
                continue
            chan.pending.append(_rec)
            _rl_n += 1
            log(f"[udp {addr[0]}:{addr[1]}] -> NPC RE-POP queued: cmd "
                f"{fmoworld.CMD_POP} id={_uid:#010x} -- a WIPED key is re-created, "
                f"a present one takes the update arm 0x611EB2CA (visual rebuild)"
                f"{f'; next in {poplook.POP_NPC_REPOP:g}s (FMO_UDP_POP_NPC_REPOP)' if poplook.POP_NPC_REPOP > 0 else ''}.")
        chan.npc_relook_sent = True
        if poplook.POP_NPC_REPOP > 0:
            # periodic: re-arm the same timer (see POP_NPC_REPOP)
            chan.npc_relook_due = time.time() + poplook.POP_NPC_REPOP
            chan.npc_relook_sent = False
        if _rl_n:
            log(f"[udp {addr[0]}:{addr[1]}]   {_rl_n} NPC re-pop(s) sent. If the "
                f"speaker now DRAWS in the cutscene, timing was the wall; if "
                f"still invisible, the cutscene scene cannot build the type-4 "
                f"visual and the fix is a model approach, not timing.")

    # WARNING: THE DEFERRED LOOK. A SECOND cmd 7 on the same UnitID, which
    # `0x611EAE0B` sends to the already-present arm `0x611EB2CA`: it tears the
    # current visual down (`0x611E72E0`), re-copies `body[0x58..0x1C8)` to
    # `entity+0x19F`, then falls back into the UnitType dispatch so
    # `0x611E7190` rebuilds AND DRESSES the unit -- this time in a settled
    # scene. See POP_LOOK_DEFER for why that is a probability and not a fix.
    if (popsweep.POP and chan.popped and chan.pop_args and chan.type4_look
            and not chan.look_sent and chan.look_due
            and time.time() >= chan.look_due):
        uid = chan.self_unit()
        try:
            chan.pending.append(fmoworld.record_pop(
                uid, look=chan.type4_look, **chan.pop_args))
        except ValueError as e:
            log(f"[udp {addr[0]}:{addr[1]}] WARNING: DEFERRED LOOK REFUSED BY "
                f"OUR OWN GUARD, nothing sent (the bare unit stands): {e}")
        else:
            log(f"[udp {addr[0]}:{addr[1]}] -> DEFERRED LOOK queued: a second "
                f"cmd {fmoworld.CMD_POP} on UnitID={uid:#010x} carrying "
                f"{chan.type4_look} as our record "
                f"{chan.tx_base + len(chan.pending) - 1}. It takes the "
                f"already-present arm 0x611EB2CA, so the unit is rebuilt and "
                f"DRESSED {poplook.POP_LOOK_DEFER:g}s after it was created bare.")
        # Either way, once. A refused record must not be retried every
        # datagram, and a sent one must not be duplicated.
        chan.look_sent = True

    # WARNING:KEY: THE "DESTROYED" UN-POISON, corrected. TWO records in order: a cmd 8
    # DEPOP (status 2) to set char+0x20 != 0, then a cmd 7 re-POP with an ALIVE
    # client_kind. A bare re-POP is a NO-OP on the status (phase B dispatches on
    # char+0x20, which is 0 on a fresh char -> the skip arm 0x611D44AB); the depop
    # is what arms the destruct+recreate arm so the re-POP actually rewrites
    # char+0x1C. Depop status 2 leaves the peer's +0x10DC=2 (the scene gate) alone.
    # Sent only on a battle channel, only after a primary POP whose client_kind was
    # itself destroyed (2/3), once, after a short settle. See UNDESTROY_KIND.
    if (popself.UNDESTROY_KIND is not None and popsweep.POP and chan.popped and chan.pop_args
            and not chan.undestroyed and chan.key and chan.key.endswith(b"battle")
            and fmoworld.client_kind_is_destroyed(chan.pop_args.get("client_kind"))):
        if chan.undestroy_due is None:
            chan.undestroy_due = time.time() + popself.UNDESTROY_AFTER
        elif time.time() >= chan.undestroy_due:
            uid = chan.self_unit()
            _alive_args = dict(chan.pop_args, client_kind=popself.UNDESTROY_KIND)
            try:
                # ORDER MATTERS: depop first (sets char+0x20), then the re-POP
                # (finds char+0x20 != 0 -> destruct+recreate -> char+0x1C alive).
                _depop = fmoworld.record_depop(
                    uid, from_id=0, status=popself.UNDESTROY_DEPOP_STATUS)
                _repop = fmoworld.record_pop(
                    uid, look=chan.type4_look, **_alive_args)
            except ValueError as e:
                log(f"[udp {addr[0]}:{addr[1]}] WARNING: UN-POISON REFUSED BY OUR "
                    f"OWN GUARD, nothing sent (self stays destroyed): {e}")
            else:
                chan.pending.append(_depop)
                chan.pending.append(_repop)
                log(f"[udp {addr[0]}:{addr[1]}] -> UN-POISON queued (2 records): "
                    f"cmd {fmoworld.CMD_DEPOP} DEPOP status "
                    f"{popself.UNDESTROY_DEPOP_STATUS} on UnitID={uid:#010x} (sets "
                    f"char+0x20 via 0x611E2070) as record "
                    f"{chan.tx_base + len(chan.pending) - 2}, then cmd "
                    f"{fmoworld.CMD_POP} re-POP client_kind={popself.UNDESTROY_KIND} "
                    f"(char status "
                    f"{fmoworld.POP_CLIENT_KIND_TO_CHARSTATUS.get(popself.UNDESTROY_KIND)}"
                    f", ALIVE) as record {chan.tx_base + len(chan.pending) - 1}. "
                    f"The re-POP now takes the destruct+recreate arm 0x611D42D9, "
                    f"rewrites char+0x1C, so 0x611F25D0 stops flagging the self; "
                    f"+0x10DC persists (depop status 2 spares the peer).")
            chan.undestroyed = True

    # KEY: THE HELLO-ACK (see HELLO_ACK above). Transport-level, once per
    # channel, AFTER the POP so the entity-map half of 0x61001E90's predicate
    # is already true when +0x10E0 flips to 2. With HELLO_ACK_ON=15 it waits
    # for the client's own cmd-15 hello instead and echoes the id it carried.
    if popself.HELLO_ACK and not chan.hello_acked and (chan.popped or not popsweep.POP
                                                       or chan.key == groupchannel.GROUP_KEY):
        _hello = [b for _o, _s, _c, b in got["records"] if _c == popself.HELLO_ACK_ON]
        if popself.HELLO_ACK_ON == 0 or _hello:
            _uid = chan.self_unit() or got["peer"]
            if _hello and len(_hello[0]) >= 4:
                _uid = int.from_bytes(_hello[0][:4], "little")
            chan.pending.append(fmoworld.record(
                popself.HELLO_ACK, (_uid & 0xFFFFFFFF).to_bytes(4, "little")))
            chan.hello_acked = True
            log(f"[udp {addr[0]}:{addr[1]}] -> HELLO-ACK queued: cmd "
                f"{popself.HELLO_ACK} UnitID={_uid:#010x} as our record "
                f"{chan.tx_base + len(chan.pending) - 1} "
                + ("(answering the client's cmd 15)" if _hello else
                   "(right after the POP; FMO_UDP_HELLO_ACK_ON=15 waits for "
                   "the client's hello instead)")
                + f". 0x611E30A0 should set the self peer's +0x10E0 to 2"
                + (" and the client should send us a cmd 4 back -- THAT line "
                   "is the proof it landed" if popself.HELLO_ACK in (3, 16) else
                   " silently (cmd 4 sends nothing back)")
                + "; state 3 then advances globals+0x1C4 to 4 (in scene 7 / "
                  "scene 5 it runs the NPC arm 0x61100100 first; scene 6, the "
                  "settled lobby, does not). Read it with fmocrash.py --live "
                  "(NPC ARM block) or /targetnpc 0x820C1080 in-game.")

    # WARNING: THIS IS A RETRANSMITTING CHANNEL AND WE ARE A SENDER ON IT. Our FROM is
    # the first record the peer has NOT acknowledged -- its `+0x20` -- and every
    # datagram carries the whole unacknowledged tail, exactly as the client's
    # own builder does (its ring-copy loop between chan+0x14 and chan+0x18).
    #
    # WARNING: The first version advanced our index the moment it SENT a record. The
    # peer's base only moves when it CONSUMES one, so a record it did not take
    # left our FROM permanently ahead of its base and every later datagram fell
    # outside the window -- the same silent-drop as the bug before it, self
    # inflicted, and invisible until we had a record to send at all.
    # Introduce any newcomers BEFORE the reply is built, so their cmd-7 POP
    # rides this datagram rather than the next one -- see room_queue.
    roomrelay.room_queue(chan)
    groupchannel.group_queue(chan)          # a group channel: the OTHER members, by cmd 190
    chan.retire(got["ack"])
    # WARNING: Only as many records as fit the client's 1,400-B recv buffer (see
    # UDP_MAX_DATAGRAM). TO covers the slice, not the tail; the rest waits for
    # the peer's ack to move tx_base and rides the next exchange.
    _n = udpconfig.fit_records(chan.pending)
    body = b"".join(chan.pending[:_n])
    reply = fmoworld.build(*chan.tables, peer=got["peer"],
                           hid=udpconfig.scene_reply_hid(chan),
                           kind=got["kind"], ack=got["to"],
                           # flag 0 lets the peer ADOPT our base, once. After
                           # that we mirror what the client itself sends (2 =
                           # its own +0x106E), rather than keep asking to be
                           # adopted on every datagram.
                           flag=0 if chan.tx_base == 0 and not chan.adopted else 2,
                           frm=chan.tx_base,
                           to=(chan.tx_base + _n) & 0xFFFF,
                           body=body)
    if _n < len(chan.pending) and not getattr(chan, "_sliced_said", False):
        chan._sliced_said = True
        log(f"[udp {addr[0]}:{addr[1]}] window: {len(chan.pending)} records "
            f"pending, sending {_n} per datagram ({len(reply)} B <= "
            f"FMO_UDP_MAX_DATAGRAM={udpconfig.UDP_MAX_DATAGRAM}; the client's recvfrom "
            f"buffer is {udpconfig.CLIENT_RECV_BUF} B) -- the rest follow as the peer acks")
    chan.adopted = True
    sock.sendto(reply, addr)
    # WARNING: AND THE ROOM, AFTER the self stream's datagram. Order matters on the
    # FIRST exchange with a newcomer: the cmd-7 POP that creates their peer is
    # queued on the self stream above, and a datagram naming a peer the client
    # has not built yet is dropped in silence (0x610012B0 is a stub). Sending
    # the alias stream first would spend its adoption on a peer that does not
    # exist -- a failure whose only symptom is a player who never moves.
    roomrelay.room_flush(sock, chan, got)
    if got["to"] != chan.rx:
        # WARNING: If the client RESENDS a range we have already acked, the reply is
        # not reaching it -- that is the shape of every failure this channel
        # has had (wrong hid, wrong index, nothing bound), and the log is the
        # only place it is visible. hid=2 and this ACK are both VERIFIED LIVE
        # (2026-08-19: the client retired exactly what we acknowledged).
        log(f"[udp {addr[0]}:{addr[1]}] -> ACK {got['to']} "
            f"(ours {chan.tx_base}+{len(chan.pending)} pending, {_n} carried, "
            f"{len(reply)} B, their ack {got['ack']}, hid={udpconfig.scene_reply_hid(chan)})"
            + ("  WARNING: RESEND: it did not take our last ACK"
               if got["to"] <= chan.rx and chan.rx else ""))
        chan.rx = got["to"]


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    battleend, battlepop, charlist, groupchannel, missionblock, npcroster, peerlink, poplook,
    popnames, popnation, popparts, popself, popsweep, referee, room, roomrelay, rooms, squad,
    udpconfig, warmap, worldchannel,
)
