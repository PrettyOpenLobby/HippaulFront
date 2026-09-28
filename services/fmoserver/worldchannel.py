"""The UDP world channel: WorldChannel, its alias streams, binding a channel to an account,
serve_udp."""
import threading
import time
from .deps import fmoworld
from .knobs import _env_float
from .wirelog import log


class RemoteStream:
    """Our outbound record stream for ONE other player's unit, on one client.

    A separate object because the client keeps a separate window per peer: the
    peer it created for the alias has its own `chan+0x1A` base, adopts our index
    once, and acknowledges independently. Reusing the self stream's counters for
    a second peer would reproduce, exactly, the drop-everything-after-the-first
    bug documented on WorldChannel -- with the added twist that the two streams
    would appear to work in alternation."""

    def __init__(self, alias, peer_addr):
        self.alias = alias          # the UnitID this client knows them by
        self.peer_addr = peer_addr  # the OTHER channel's (host, port)
        #: The PEER LINK tag (see PEER_LINK): POP body+0x36, our +0x0A on this
        #: stream, and the +0x09 the client stamps on what it sends this peer.
        self.tag = room.PEER_TAG_BASE + ((alias - room.ROOM_ALIAS_BASE) % 0x40)
        self.rx = 0                 # how far we consumed THEIR records (our ack)
        self.last_tx = 0.0          # when we last sent on this stream
        self.linked = False         # the client's cmd 3/16 got our cmd 4
        self.relayed = 0            # records passed on to the other client
        self.tx_base = 0
        self.pending = []
        self.adopted = False
        self.popped = False         # cmd 7 sent on the SELF stream (see below)
        self.sent_pos = None        # the last position we relayed
        self.sent_at = 0.0
        self.sent_n = 0         # datagrams sent on this stream, for the log
        #: WARNING: The deferred look for THIS remote's unit (see POP_LOOK_DEFER).
        #: The hazard is the same as the self POP's and the victim is worse:
        #: an undressed-then-dressed stranger walking in must not be able to
        #: take down the client that is watching them join.
        self.pop_args = None
        self.look_due = None
        self.look_sent = False
        #: Why this remote stopped being a room-mate, or None while they are
        #: one. With FMO_UDP_ROOM_DEPOP=0 the stream is KEPT after they leave
        #: (nothing removed their unit, so the same alias must carry them
        #: when they return) and this is what stops the log repeating.
        self.gone = None

    def retire(self, peer_ack):
        n = (peer_ack - self.tx_base) & 0xFFFF
        if 0 < n <= len(self.pending):
            del self.pending[:n]
            self.tx_base = peer_ack


class WorldChannel:
    """One UDP peer: the key it latched, and TWO separate indices.

    WARNING: THE TWO INDICES ARE NOT THE SAME NUMBER, and conflating them made every
    reply after the first one undeliverable. Found 2026-08-19 by re-reading the
    client's window test (0x61070922) rather than by a failed run, because from
    the client's side the symptom is identical to sending nothing at all.

      * `tx` is OUR record index -- what goes in +0x1E/+0x1C. The peer ADOPTS it
        from our first datagram (0x61070902, once, while its +0x106E is 0) into
        chan+0x1A, and thereafter advances chan+0x1A only as it CONSUMES OUR
        RECORDS. We send none, so ours must stay put.
      * `rx` is how far we have consumed THE CLIENT'S records -- what goes in
        +0x20, the acknowledgement that lets it retire its backlog.

    The first version put `rx` in both. So our FROM climbed 0 -> 2 -> 9 with the
    client's traffic while its chan+0x1A stayed 0, and
    `(chan+0x1A - our_FROM) & 0xFFFF` immediately exceeded mask/2 -- the client
    dropped every reply from the second one onward, silently, counting them at
    chan+0x1084. One ACK would have landed and then nothing, which looks exactly
    like the id in +0x09 being wrong. WARNING: **Two failures with one symptom is the
    normal case here, not the exception.**"""

    def __init__(self, addr):
        self.addr = addr
        #: The account this channel's player entered the world as, bound once at
        #: creation by claim_world_account() -- or None, when account_for() falls
        #: back to the per-address answer it always gave.
        self.account = None
        #: {"map", "zone", "place"} this channel's player was granted -- see
        #: chan_where(). None = use the per-address WORLD_* values.
        self.loc = None
        #: monotonic time this channel last consumed its OWN re-entry -- see
        #: rebind_on_restart(), which keeps the binding across a Move.
        self.settled_at = None
        self.key = None
        self.tables = None
        #: the first of OUR records the peer has not acknowledged. It moves
        #: only when the peer's +0x20 says so -- never because we sent.
        self.tx_base = 0
        self.pending = []          # our unacknowledged records, from tx_base
        self.popped = False        # the cmd-7 POP probe is once-per-channel
        self.npcs_popped = False   # the NPC source records (FMO_UDP_POP_NPC)
        self.dummy_popped = False  # the battle dress probe (FMO_BATTLE_DUMMY)
        self.dummy_id = None       # its UnitID, for FMO_BATTLE_DUMMY_KILL
        self.dummy_pos = None      # its world pos, for FMO_BATTLE_REFEREE
        self.dummy_kill_due = None # when to send the destroy DEPOP
        self.dummy_kill_sent = False
        self.referee_hits = 0      # qualifying shots on the enemy so far
        self.gate_popped = False   # the scene-4 gate pop (FMO_BATTLE_GATE_POP)
        self.npc_relook_due = None    # when to re-send the NPC pops (visual
        self.npc_relook_sent = False  # rebuild); None/False = nothing pending
        self.hello_acked = False   # the FMO_UDP_HELLO_ACK record, likewise
        self.desync_warned = False # say it once, not every datagram
        self.adopted = False
        self.rx = 0                # how far we have consumed THEIRS -> our +0x20
        #: WARNING: HOW FAR WE HAVE ACTED. `rx` is ACK bookkeeping and follows the
        #: last datagram's `to` wherever it goes; this is a high-water mark that
        #: only ever moves FORWARD, and it exists because a resent record must
        #: not be acted on twice. The client resends its whole backlog whenever
        #: it is not seeing our acks, and with the chat echo that turned one
        #: typed line into one line PER RESEND on every screen in the room
        #: (measured live 2026-09-08 with three players: 'Hai' eight times).
        #: Movement is idempotent and does not need this; chat is not.
        self.acted = 0
        self.seen = 0
        self.said = False
        #: how many of each inbound cmd we have seen, so the FIRST few of a
        #: command get dumped whole and the rest stay one line. See the
        #: truncation note at the record logger.
        self.cmd_seen = {}
        #: a 0x0153 has been served, so this scene is ending and the
        #: client is about to restart its record indices. See restart().
        self.expect_restart = False
        #: --- the room (see the ROOM block above) ---
        #: the character id the key latch proved (unlock() sets it). It is what
        #: the client put in globals+0x1BC, i.e. what it believes IS itself, so
        #: it is also `manager+0x2C` -- the id cmd 240 refuses to move.
        self.char_id = None
        #: this player's own position, from their cmd 240s. Seeded with the
        #: spawn we served so a player who has not moved yet is still somewhere
        #: rather than nowhere.
        self.pos = tuple(popsweep.POP_POS[:3])
        #: when we last PRINTED a position for this channel, so walking is
        #: sampled rather than transcribed. None = nothing printed yet, which
        #: is what makes the first one unconditional.
        self.pos_logged_at = None
        #: KEY: THIS PLAYER'S OWN `body+0x7A`, decided ONCE when their self POP is
        #: built, and reused verbatim when this channel is popped into everybody
        #: else's scene. Before 2026-08-26 the self POP called next_pop_sex() and
        #: the room relay passed the fixed POP_SEX, so a player's appearance was
        #: chosen independently in every scene it appeared in -- with the sweep
        #: armed that is round-robin, and two players each saw themselves as one
        #: model and the other as the other. Measured live: "I see them as a woman
        #: and me as a man, and vice versa." One player, one model, everywhere.
        self.type4_model = popself.POP_SEX
        #: KEY: AND THE SAME FOR THE OTHER FOUR FIELDS (size, build, face,
        #: uniform), for exactly the reason above: one player, one look, in
        #: every scene they appear in. Decided once when the self POP is built
        #: and reused verbatim by the room relay.
        self.type4_look = None
        #: WARNING: The DEFERRED LOOK (see POP_LOOK_DEFER). `pop_args` is what the
        #: create POP actually sent, kept verbatim so the follow-up rebuilds
        #: the SAME unit rather than re-deriving fields through a sweep that
        #: would have stepped on. `look_due` is when to send it, None when
        #: there is nothing to send.
        self.pop_args = None
        self.look_due = None
        self.look_sent = False
        #: FMO_UDP_POP_UNDESTROY: once we have re-popped the self with an ALIVE
        #: client_kind to clear the battle "destroyed" status, this latches so
        #: the un-poison POP is sent exactly once. See UNDESTROY_KIND.
        self.undestroyed = False
        self.undestroy_due = None
        self.rot = 0.0
        self.move_flags = 6
        self.moved_at = 0.0
        self.seen_at = time.time()
        #: Set by the TCP side (0x0152 LOG OUT) to the reason this channel's
        #: player is gone, so the room stops relaying them NOW rather than
        #: ROOM_TTL later. Cleared by restart(): a relaunched client dials
        #: from the same fixed source port and lands on this same object.
        self.left = None
        #: alias -> RemoteStream, and the other channel's addr -> alias.
        self.remotes = {}
        self.alias_of = {}
        #: VERIFIED: NPC unit id -> RemoteStream (2026-09-11): a lobby NPC's OWN
        #: outbound stream, so a cmd 240 stamped with its id can MOVE it --
        #: the only way the client moves a unit that is not the player
        #: (fmoworld's cmd-240 note: the moved unit is the datagram's peer,
        #: never in the body). Kept apart from `remotes` because the room
        #: relay walks that dict as "other players" and would prune these.
        self.npc_remotes = {}
        self.next_alias = room.ROOM_ALIAS_BASE

    def self_unit(self):
        """The UnitID this client believes is its own.

        The POP is what put it in the entity map, so the POP's id is the
        authority; the latched character id is the cross-check and the fallback,
        because it is the same number by construction (globals+0x1BC feeds both
        the key and `manager+0x2C`)."""
        # With wire ids (CHAR_WIRE_BASE) the client's own id is the character
        # id it selected, which the key latched -- the fixed FMO_UDP_POP id (1)
        # would be a unit the client does not own, and it is < 10 besides.
        if charlist.CHAR_WIRE_BASE and self.char_id and self.char_id >= charlist.CHAR_WIRE_BASE:
            return self.char_id
        if popsweep.POP:
            return popsweep.POP[0]
        return self.char_id

    def alias_for(self, other_addr):
        """Mint (once) the UnitID by which THIS client will know `other_addr`.

        WARNING: Never the other player's own character id: two players who each
        picked character 1 would then both be unit 1 here, and cmd 240 would
        refuse to move the remote because `0x611EABF1` would see the local
        player's own id."""
        a = self.alias_of.get(other_addr)
        if a is None:
            a = self.next_alias
            self.next_alias += 1
            if a == self.self_unit():          # cannot happen with the default
                a = self.next_alias            # base, but a knob can make it
                self.next_alias += 1
            self.alias_of[other_addr] = a
        #: WARNING: THE STREAM, EVERY TIME -- not only when the alias is minted.
        #: `alias_of` now survives restart() (so ids cannot shuffle) while
        #: `remotes` does not, so a returning remote has a mapping and NO send
        #: state. Creating the stream only in the `is None` arm above left it
        #: absent, and every caller then either KeyErrors or silently relays
        #: nothing -- which is the same frozen remote the stable mapping exists
        #: to prevent, arrived at from the other side. Caught by this file's
        #: own restart pin before it ever ran live.
        if a not in self.remotes:
            self.remotes[a] = RemoteStream(a, other_addr)
        return a

    def restart(self):
        """The client has begun a NEW scene on the SAME channel.

        Everything send-side and every once-per-channel probe goes back
        to zero; the KEY and cipher tables do not, because they are
        derived from the endpoint and the character id, and neither
        changed."""
        was = (self.tx_base, len(self.pending), self.popped)
        self.tx_base = 0
        self.pending = []
        self.rx = 0
        self.acted = 0
        self.seen = 0
        self.popped = False
        self.npcs_popped = False   # re-arm the NPC source POPs for the new scene
        self.dummy_popped = False  # and the battle dress probe
        self.dummy_id = None
        self.dummy_pos = None
        self.dummy_kill_due = None
        self.dummy_kill_sent = False
        self.referee_hits = 0
        self.gate_popped = False   # and the gate pop
        self.npc_relook_due = None
        self.npc_relook_sent = False
        #: KEY: and the deferred look with it -- a new scene is a new entity map,
        #: so the unit the follow-up would UPDATE no longer exists. It must be
        #: re-armed against the POP the new scene gets, not the old one.
        self.pop_args = None
        self.look_due = None
        self.look_sent = False
        self.undestroyed = False
        self.undestroy_due = None
        self.hello_acked = False
        self.said = False
        self.adopted = False
        self.desync_warned = False
        self.cmd_seen = {}
        #: a new scene is a new spawn, and the first position in it is the one
        #: worth printing unconditionally -- so the throttle starts over too.
        self.pos_logged_at = None
        self._sliced_said = False
        #: WARNING: AND THE ROOM'S SEND STATE GOES WITH IT. A new scene is a new
        #: entity map and a new peer map, so every alias we minted names a unit
        #: that no longer exists: each remote must be POPPED again before its
        #: movement means anything (keeping the send state would relay onto ids
        #: the client would look up, miss, and drop in silence, 0x611EAC06 --
        #: which reads exactly like the relay not working).
        self.remotes = {}
        self.npc_remotes = {}
        #: WARNING:KEY: BUT THE ADDRESS -> ALIAS MAPPING SURVIVES, and that is the whole
        #: point. It used to be cleared too, so aliases were re-minted in
        #: WHATEVER ORDER the remotes were next seen in. With one remote that
        #: is invisible -- it is always 0x200. With TWO it shuffles: measured
        #: live 2026-09-08 with three players, Fox was 0x200 on Patty's client
        #: and came back as 0x201 after a restart, so the unit at 0x200 stopped
        #: receiving movement and FROZE where it stood. The report: "Patty
        #: sees Dick as being in the same place he was minutes ago."
        #:
        #: A stable mapping cannot shuffle, and costs nothing: every remote is
        #: re-popped anyway because `remotes` is empty, so the new scene gets a
        #: fresh POP under the id it already had. It also means an id is never
        #: recycled onto a DIFFERENT player, which is the same ghost wearing
        #: somebody else's name.
        self.next_alias = max([room.ROOM_ALIAS_BASE]
                              + [a + 1 for a in self.alias_of.values()])
        self.left = None
        return was

    def retire(self, peer_ack):
        """Drop the records the peer has taken. `peer_ack` is its +0x20: the
        first of our records it has NOT consumed."""
        n = (peer_ack - self.tx_base) & 0xFFFF
        if 0 < n <= len(self.pending):
            del self.pending[:n]
            self.tx_base = peer_ack

    def candidates(self):
        # The host this datagram's sender was handed in 0x0153: the same
        # choice, made from the same address, so the keys agree.
        _h = addressing.host_for(addressing.BATTLE_HOST, self.addr[0] if self.addr else None)
        ep = (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)(_h, addressing.BATTLE_PORT)
        ids = udpconfig.UDP_KEY_IDS or list(range(1, max(charlist.LIST_COUNT, 1) + 1)) + [0]
        if charlist.CHAR_WIRE_BASE and not udpconfig.UDP_KEY_IDS:
            # The client keys with the id it SELECTED, i.e. the wire id
            # (to_wire). Tried first; the store ids stay as the fallback.
            ids = [charlist.to_wire(i) for i in range(1, charlist.LIST_SLOTS + 1)] + ids
        # Scene 4's battle UDP manager keys "%xbattle" from the SAME endpoint
        # struct (served again in the 0x014E push, FMO_SORTIE_HOST/PORT
        # reused), so the battle twin of every lobby key is a candidate too.
        # The client's own MD5 still decides -- a wrong twin cannot latch.
        # Measured 2026-09-04: the first scene 4 flooded 40..880B datagrams
        # that verified under NO lobby key and timed out on screen.
        # 2026-09-05: a BATTLE GROUP is a third channel of the same client class
        # (CFmoGroup -> CFmoConnectCliSys, 0x61177620 passes the literal "group"
        # to 0x611E4F40), keyed "%xgroup" from the 0x0158/0x0155 entry's
        # endpoint -- served as the same host:port, so the same sum.
        return [(cid, fmoworld.key_for_endpoint(ep, cid, kind))
                for cid in ids for kind in ("lobby", "battle", "group")]

    def unlock(self, dg):
        """Latch the key by trying every character id we served. The client's
        own MD5 decides, so this cannot latch a wrong key -- it can only fail to
        find one, which is itself the useful answer."""
        for cid, key in self.candidates():
            tables = fmoworld.bf_init(key)
            got = fmoworld.parse(*tables, dg)
            if got:
                self.key, self.tables, self.char_id = key, tables, cid
                log(f"[udp {self.addr[0]}:{self.addr[1]}] key {key.decode()} "
                    f"(character id {cid}) -- the client's own MD5 verifies it")
                return got
        return None


#: KEY: WHICH ACCOUNT IS BEHIND WHICH WORLD CHANNEL (2026-09-27). The world helpers
#: all ask account_for(host_ip), which is one answer per ADDRESS -- so with one
#: player's PC (Fox) and Steam Deck (Kai) in the world from one router, both
#: self-POPs named 'Kai'/'Test' even after the TCP side named each member
#: correctly by key. The UDP key cannot tell them apart ("%xlobby" over our own
#: endpoint + character id, and both characters are id 1), so the link is the
#: ENTRY: each game connection's 0x0153 queues its account here, and the next
#: NEW channel from that address claims the oldest one (a client opens its
#: channel seconds after its 0x0153). A re-entry by a player who already has a
#: bound channel (a Move, a Change Area) is consumed by that channel's own next
#: datagram (settle_world_entry), so it is never left for the OTHER device's
#: new channel to claim; a relaunch from a new NAT port still finds its entry.
WORLD_ENTRY_TTL = _env_float("FMO_WORLD_ENTRY_TTL", "60")
_world_entries = {}                  # {host: [(account, monotonic), ...]}
_world_entries_lock = threading.Lock()
#: The datagram being served right now, so account_for() can answer for THIS
#: channel. Thread-local: serve_udp is one thread; the TCP threads never see it.
_udp_ctx = threading.local()


class _as_world_channel:
    """`with _as_world_channel(addr):` -- account_for() answers for THAT
    channel for the duration, then the served channel again. Use it whenever
    code serving one channel computes something about ANOTHER (a room-mate's
    pop): the address alone cannot say which of two same-router players."""

    def __init__(self, addr):
        self.addr = addr

    def __enter__(self):
        self.saved = getattr(_udp_ctx, "addr", None)
        _udp_ctx.addr = self.addr
        return self

    def __exit__(self, *exc):
        _udp_ctx.addr = self.saved
        return False


def queue_world_entry(host, account, loc=None):
    """A game connection from `host` entered the world as `account`, at `loc`
    ({"map", "zone", "place"}, only the fields the 0x0153 set)."""
    if not account:
        return
    now = time.monotonic()
    with _world_entries_lock:
        q = [e for e in _world_entries.get(host, [])
             if now - e[1] <= WORLD_ENTRY_TTL and e[0] != account]
        q.append((account, now, dict(loc or {})))
        _world_entries[host] = q


def _bind_world_entry(chan, entry):
    chan.account = entry[0]
    chan.loc = dict(chan.loc or {}, **entry[2])


def claim_world_account(host, chan=None):
    """The account for a NEW world channel from `host`, or None. With `chan`,
    binds it (account AND the location that entry was granted)."""
    now = time.monotonic()
    with _world_entries_lock:
        q = [e for e in _world_entries.get(host, [])
             if now - e[1] <= WORLD_ENTRY_TTL]
        got = q.pop(0) if q else None
        _world_entries[host] = q
    if got is None:
        return None
    if chan is not None:
        _bind_world_entry(chan, got)
    return got[0]


def settle_world_entry(chan):
    """A bound channel is still talking: any entry queued for ITS account was
    its own re-entry (a Move, a Change Area), not a new device's. Take its
    location and drop it."""
    if not chan.account:
        return
    host = chan.addr[0]
    with _world_entries_lock:
        q = _world_entries.get(host)
        mine = [e for e in (q or []) if e[0] == chan.account]
        if mine:
            _world_entries[host] = [e for e in q if e[0] != chan.account]
    for e in mine:
        _bind_world_entry(chan, e)
    if mine:
        chan.settled_at = time.monotonic()


def rebind_on_restart(chan):
    """The client behind `chan` restarted its indices (a new scene -- or a NEW
    CLIENT on the same address:port). Returns the new account if the binding
    changed, else None.

    WARNING: LIVE 2026-09-27: the Steam Deck relaunched FMO as Kai from the SAME
    source port (19155) a channel bound to Fox still held, so the channel
    object -- and its binding -- was reused, and Kai wore Fox's name while
    Kai's own entry sat unclaimed. A restart is where the client is replaced,
    so it is where the binding is re-decided:
      * an entry for THIS account is pending -> same player, new location;
      * this account settled one recently -> a Move/Change Area, keep it;
      * otherwise an entry from ANOTHER account is waiting -> a different
        client took over the port: claim it."""
    settle_world_entry(chan)
    recent = getattr(chan, "settled_at", None)
    if recent is not None and time.monotonic() - recent <= WORLD_ENTRY_TTL:
        return None
    was = chan.account
    got = claim_world_account(chan.addr[0], chan)
    return got if got and got != was else None


def chan_where(chan):
    """(MapNo, zone, place) of THIS channel's player. A bound channel carries
    its own (2026-09-27: an O.C.U. PC and a U.S.N. Deck on one router were
    relayed into each other's lobby because WORLD_ZONES is per ADDRESS and the
    later entry overwrote the earlier). Unbound: the per-address values."""
    loc = getattr(chan, "loc", None) or {}
    host = chan.addr[0]
    return (loc.get("map", rooms.WORLD_MAPS.get(host)),
            loc.get("zone", rooms.WORLD_ZONES.get(host)),
            loc.get("place", move.WORLD_PLACES.get(host)))


def serve_udp(sock):
    peers = groupchannel.WORLD_PEERS
    while True:
        try:
            dg, addr = sock.recvfrom(2048)
        except OSError as exc:
            log(f"[udp] recv failed: {exc}")
            return
        _udp_ctx.addr = addr
        try:
            datagram._serve_datagram(sock, peers, dg, addr)
        except Exception as exc:                 # noqa: BLE001
            # This socket faces whatever the network sends it. A parse bug on
            # one datagram must not take the responder down and leave the TCP
            # half alive -- that combination would look exactly like the world
            # channel "not being served", which is the state we just left.
            log(f"[udp {addr[0]}:{addr[1]}] handler raised {exc!r} on a "
                f"{len(dg)}B datagram -- continuing")
        finally:
            _udp_ctx.addr = None


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    addressing, charlist, datagram, groupchannel, move, popself, popsweep, room, rooms,
    udpconfig,
)
