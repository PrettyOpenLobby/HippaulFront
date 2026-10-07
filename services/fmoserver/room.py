"""The room knobs: lobby multiplayer, the peer link and the room timers."""
import os
from .deps import fmoworld
from .knobs import _env_float, _env_int
from . import popsweep, zoneentry


#: The eight ids in the 0x0153 setup block. Defaults to the popped unit so the
#: POP and the lookup cannot disagree; `FMO_0153_UNIT_IDS` overrides for a run
#: that wants them to differ on purpose.
_ids_env = os.environ.get("FMO_0153_UNIT_IDS", "").replace(" ", "")
if _ids_env:
    SETUP_UNIT_IDS = [int(x, 0) for x in _ids_env.split(",") if x]
elif popsweep.POP:
    SETUP_UNIT_IDS = [popsweep.POP[0]]
else:
    SETUP_UNIT_IDS = []


# --------------------------------------------------------------------------- #
# WARNING: THE ROOM: LOBBY MULTIPLAYER.
#
# VERIFIED: THE MECHANISM IS DECODED, not guessed (2026-08-24, static + 296 captured
# records). Three facts, and the design falls out of them:
#
#   1. The datagram's `+0x00` PEER ID is a **UnitID**. `0x611E30BE` looks it up
#      in `[manager+0x1C]`, the PEER map, and `0x610729BA` shows that map's
#      nodes carry their key at `peer+0x10`. Peer id 0 means "me"
#      (`0x611E30CB` substitutes `[manager+0x2C]`).
#   2. **cmd 240 moves `peer+0x10`** -- the unit the DATAGRAM names, not one
#      named in the payload -- and `0x611EABF1` RETURNS if that unit is the
#      local player. So a second player walking on your screen is a second
#      RECORD STREAM, not a second kind of record. See fmoworld.CMD_MOVE.
#   3. **cmd 7 is what creates a peer.** Body `+0x00` in {0, 2, 3} allocates the
#      0x1E21-byte client object and inserts it under the popped UnitID; the
#      auto-create factory the receive path would otherwise use is
#      **`0x610012B0`, which is `xor eax,eax / ret 4`** -- a stub. WARNING: Therefore a
#      datagram naming a peer we have not POPped is DROPPED IN SILENCE, and the
#      POP must ride the player's OWN stream, which is the only one that exists
#      when it is sent.
#
# So: pop every other player into this client as a distinct UnitID, then relay
# their movement on a datagram stamped with that id. With one player connected
# none of this runs, which is why it is on by default -- a room of one is the
# behaviour we already shipped.
ROOM = os.environ.get("FMO_UDP_ROOM", "1") != "0"
#: Where the aliases start. A remote player needs a UnitID that is NOT the
#: local player's own -- and two players who each picked character id 1 both
#: believe they ARE 1, so the id a remote is popped as has to be minted per
#: VIEWER rather than taken from the remote's own character. These are those
#: minted ids; they are private to one client's entity map and mean nothing on
#: any other.
#: The peer stream key (fmoworld.POP_CLIENT_BLOB). ON by default because
#: sending zeros is MEASURED broken -- 2026-08-26, two machines: relayed
#: players appear and never move, because an empty key schedules a cipher
#: the client then fails its own MD5 against. Set FMO_UDP_ROOM_PEER_KEY=0
#: to restore the old behaviour for an A/B.
ROOM_PEER_KEY = os.environ.get("FMO_UDP_ROOM_PEER_KEY", "1") != "0"

ROOM_ALIAS_BASE = _env_int("FMO_UDP_ROOM_ALIAS_BASE", "0x200")
#: KEY: THE PEER LINK (2026-09-27). Retail FMO ran
#: movement, fire, damage and voice PEER-TO-PEER; we stand in for every peer.
#: Until now the room-mate POP left the peer's address zero, so the client never
#: sent the alias a byte (frozen pilot, [!] over them, no voice) -- and when it
#: did send, we filed it under the self stream, because the client stamps +0x00
#: with its OWN UnitID and names the peer only by the +0x09 TAG byte (POP
#: body+0x36 -> peer+0x111C). With this on: the POP carries our endpoint and a
#: per-alias tag, alias datagrams carry that tag at +0x0A, inbound traffic is
#: routed by tag, the client's cmd 3/16 hello gets cmd 4 (link state 2 -- the
#: client logs "(Operator)p2p成功" once every peer is up), pings are answered,
#: and its records for the peer are RELAYED to that player's client under the
#: alias they know the sender by. FMO_UDP_PEER_LINK=0 restores the old POP.
PEER_LINK = os.environ.get("FMO_UDP_PEER_LINK", "1") != "0"
#: First tag byte. Must never equal a manager hid the client uses for its OWN
#: stream (lobby 2, battle 0, group 1 / 5), or its self traffic would read as
#: an alias's; 0x40.. is clear of all of them.
PEER_TAG_BASE = _env_int("FMO_UDP_PEER_TAG_BASE", "0x40")
#: Keep each linked alias stream alive: the [!] also shows when a peer has been
#: silent > 1.5 s (0x611E2250), so an idle alias gets an ack-only datagram.
PEER_KEEPALIVE = _env_float("FMO_UDP_PEER_KEEPALIVE", "1.0")
#: The UnitType a remote player is popped as. 4 is the only type measured to
#: render a person (the UnitType selects class and control); it is a knob
#: because "what another player looks like" is not decoded, only "what I look
#: like" is.
ROOM_TYPE = _env_int("FMO_UDP_ROOM_TYPE", "4")
#: Only relay between clients the TCP half granted the same MapNo. Off makes
#: every channel one room, which is wrong but useful when the map bookkeeping
#: is the thing under suspicion.
ROOM_SAME_MAP = os.environ.get("FMO_UDP_ROOM_SAME_MAP", "1") != "0"
#: KEY: AND THE SAME ZONE (2026-09-08). MapNo is the ROOM'S FLOOR PLAN, not
#: the room: with one lobby map for every zone (FMO_MAPNO=102 for all), an
#: O.C.U. pilot granted zone 200 and a U.S.N. pilot granted zone 400 stood in
#: the same relay room and saw each other -- the live report "my OCU character
#: can be seen in the same lobby" (2026-09-08). SE's own Move dialog calls a
#: Lobby ID an INSTANCE number (fmo-game-process), so a room is (zone, map):
#: the zone kind of the last 0x0153 this host was granted (WORLD_ZONES) has to
#: agree as well. 0 = the old behaviour, MapNo alone.
ROOM_SAME_ZONE = os.environ.get("FMO_UDP_ROOM_SAME_ZONE", "1") != "0"
#: How long a silent channel still counts as a person standing in the room.
#: WARNING: THIS IS A PAPERING-OVER, NOT A DEPOP. Nothing here tells the client to
#: REMOVE a unit -- the POP kind arms 3/4/5 (`0x611EB44C`) are the
#: already-removed path and SE's `"RecvDepop(UnitID=%x FromID=%u Status=%u)"`
#: says a depop exists, but which record carries it is NOT decoded. So a player
#: who leaves goes still and stays on screen until the scene ends. Say so in
#: the log rather than pretending they left.
ROOM_TTL = _env_float("FMO_UDP_ROOM_TTL", "45")
#: KEY: 2026-10-06 the lobby HAS a depop: cmd 0xD3 (fmoworld.CMD_LOBBY_DEPOP);
#: room_prune sends it on lobby channels and cmd 8 on battle ones, ON by
#: default. The 2026-08-26 note below is the old reading, kept for the trail.
#: PARTIAL: THE DEPOP, decoded 2026-08-26 and OFF BY DEFAULT because it is decoded
#: as INERT on the session we serve. SE's "RecvDepop(UnitID=%x FromID=%u
#: Status=%u)" is **cmd 8**, body `{u32 UnitID; u32 FromID; u32 Status}`
#: (fmoworld.record_depop, every address there) -- and it is routed only by the
#: BATTLE peer class's dispatcher (0x611D4750, the hid-0 manager that prints
#: "Entering the battle map now"). The lobby peer class the relay talks to
#: (0x611EBA50) maps cmd 8 to `mov eax,1 / ret`: accepted, acked, ignored. No
#: other wire path removes a lobby unit (the four callers of the lobby entity
#: destructor 0x611EA8D0 are all local -- see fmoworld). So with this ON a
#: cmd 8 goes out on the viewer's SELF stream when a room-mate leaves, and the
#: prediction is "the client acks it and the unit stays". Leave it OFF unless
#: measuring exactly that; ON also re-mints a fresh alias for a returning
#: player (their old entity would have been destroyed by Status 3), OFF keeps
#: the alias so the relay resumes onto the unit that never left the screen.
ROOM_DEPOP = (os.environ.get("FMO_UDP_ROOM_DEPOP", "").strip() or "1") != "0"
#: Which Status to send: 3 = scene-list removal + entity destroyed (the full
#: removal), 0 = "left" (scene-list removal, entity kept, peer -> state 4),
#: 1 = "lost comms" (peer -> state 4 only), 2 = "destroyed" (wreck effect).
ROOM_DEPOP_STATUS = _env_int("FMO_UDP_ROOM_DEPOP_STATUS", "3")
#: Resend a remote's position at most this often, even if it keeps moving. The
#: client streams ~2/s per player; with N players in a room the relay is N^2
#: datagrams, so this is the only thing bounding it.
ROOM_MIN_INTERVAL = _env_float("FMO_UDP_ROOM_MIN_INTERVAL", "0.2")
#: How loudly the alias streams report. The first live test was unreadable
#: because they reported nothing at all.
ROOM_LOG_FIRST = _env_int("FMO_UDP_ROOM_LOG_FIRST", "5")
ROOM_LOG_EVERY = _env_int("FMO_UDP_ROOM_LOG_EVERY", "25")

#: WARNING: THE ROOM'S HEDGE ON THE SETUP BLOCK. `0x61003120` -- state 0 of the
#: scene-setup machine -- walks the 0x0153 block's eight unit ids and spawns
#: only the ones that are ALSO in the entity map. Our own unit is in both, and
#: that is measured. A player who joins the room LATER cannot be in a block
#: that was sent at scene entry, and whether a mid-scene POP renders without a
#: slot is **not known** -- the self unit has never been tested without one.
#:
#: So reserve the remaining slots for the ids the room will mint. Aliases are
#: handed out from ROOM_ALIAS_BASE in join order per viewer, so the block can
#: name them before anybody has joined; a slot whose id never gets popped is
#: skipped in silence, which is the behaviour we already ship eight of.
#: FMO_0153_ROOM_SLOTS=0 goes back to the pre-room block for an A/B -- and if
#: remotes turn out to render fine without it, that A/B is the measurement that
#: says so.
#:
#: WARNING: Gated on FMO_UDP_POP as well, so "the POP probe is off" still means an
#: all-zero block, byte-identical to every measurement taken before
#: 2026-08-21. That control is worth more than the hedge: without a self unit
#: the scene never leaves its wait, so there is nothing for a roommate to join.
ROOM_SETUP_SLOTS = _env_int("FMO_0153_ROOM_SLOTS", "7")
if popsweep.POP and ROOM and ROOM_SETUP_SLOTS and not _ids_env:
    SETUP_UNIT_IDS += [ROOM_ALIAS_BASE + i
                       for i in range(min(ROOM_SETUP_SLOTS,
                                          zoneentry.SU_UNIT_SLOTS - len(SETUP_UNIT_IDS)))]


#: Bumped when the wire behaviour changes. It exists so a log line can prove
#: WHICH code is running -- see the note in run(). "Shipping is not running."
BUILD = "room-4"


def _udp_key_hint():
    """The key this build will derive for character id 1, printed at startup so
    a live log answers 'is the key right?' without a capture. It is not a
    secret: it is a hash of the endpoint we publish."""
    if not fmoworld:
        return "n/a"
    try:
        ep = (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)(addressing.BATTLE_HOST, addressing.BATTLE_PORT)
        return fmoworld.key_for_endpoint(ep, 1).decode()
    except Exception as exc:                     # noqa: BLE001
        return f"<underivable: {exc}>"


# Called at run time only; imported last so that import cycles resolve.
from . import addressing  # noqa: E402
