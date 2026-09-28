"""The UDP world channel's knobs: handler ids, key ids, datagram size and logging."""
import os
from .deps import fmoworld
from .knobs import _env_int


# --------------------------------------------------------------------------- #
# The UDP world channel.
#
# The game scene talks over UDP to the endpoint the 0x0153 join grant names, and
# until now nothing bound it: the client resent its whole backlog for 60 s and
# gave up with FMO-13111. The cipher, the key and the framing are in
# `fmoworld.py` (verified against 177 captured datagrams); what is UNVERIFIED and
# lives here is the reply contract -- read from the client's code, never yet put
# on a wire. Every guess is an env var so a live run can move one at a time.
UDP_ENABLE = os.environ.get("FMO_UDP", "1") not in ("0", "false", "")
#: +0x09 names a manager registered in the client's "UdpReqSys" map. 0x611FED20
#: takes that id as a BYTE; the world session (0x611E7810) registers 2 and the
#: other manager (0x611D38B0) registers 0. WARNING: An id the client has not registered
#: is dropped without a log line, a reply, or a disconnect -- so if a live run
#: shows the client still resending, THIS is the first thing to sweep.
UDP_HID = int(os.environ.get("FMO_UDP_HID", "").strip()
              or (str(fmoworld.HID_WORLD) if fmoworld else "2"))
#: KEY: CONFIRMED LIVE 2026-09-04: the client's receive loop (0x611FEC90) routes
#: each REPLY we send by its +0x09 handler id and DROPS one whose id is not
#: registered on the receiving manager, before the reliable window ever runs.
#: The lobby world manager (0x611E7810) receives on hid 2; scene 4's battle
#: manager (0x611D38B0) receives on hid 0. Answering scene 4 with the hardwired
#: hid 2 hit no manager, so every reply was discarded and the client's ack
#: stayed pinned at 0 (the black-screen wall).
#: WARNING: The hid we must send is the CLIENT'S RECEIVE manager id, which is NOT the
#: +0x09 the client puts on its OUTBOUND datagrams: in the lobby the client
#: SENDS hid 0 but RECEIVES on hid 2, so echoing its outbound hid broke lobby
#: entry live (FMO-13111, 2026-09-04). The reliable scene discriminator is the
#: latched KEY's suffix -- "...battle" is scene 4 (hid 0), "...lobby" is the
#: lobby world (hid 2). FMO_UDP_HID_ECHO=0 forces the plain hardwired UDP_HID
#: everywhere (the pre-scene-4 behaviour) for an A/B.
UDP_HID_ECHO = os.environ.get("FMO_UDP_HID_ECHO", "1") != "0"
UDP_HID_BATTLE = int(os.environ.get("FMO_UDP_HID_BATTLE", "0") or "0")
#: The battle-group channel's receive hid. 0x61177620 creates the group's
#: connection client with `1` in the slot where the type-2 group passes 6 and
#: any other type 5 (0x61177707/0x61177760/0x611777C9) -- read as the manager
#: id, UNVERIFIED live; FMO_UDP_HID_GROUP to sweep if the client keeps resending.
UDP_HID_GROUP = int(os.environ.get("FMO_UDP_HID_GROUP", "1") or "1")


def scene_reply_hid(chan):
    """The handler id the client's manager for THIS channel's scene receives on.
    Battle map (key suffix 'battle') = UDP_HID_BATTLE (0); battle group
    ('group') = UDP_HID_GROUP (1); everything else = UDP_HID (2, the lobby)."""
    if UDP_HID_ECHO and chan.key and chan.key.endswith(b"battle"):
        return UDP_HID_BATTLE
    if UDP_HID_ECHO and chan.key and chan.key.endswith(b"group"):
        # KEY: The group manager's id FOLLOWS THE GROUP'S TYPE, and the client
        # stamps it on its own datagrams as `kind` (live 2026-09-27): the
        # CREATOR (0x0158 entry type 1) sends kind 1 and took hid 1 at once;
        # the JOINER (0x0174 attach type 0) sends kind 5, never acked a hid-1
        # reply, looped every 5 s and crashed ~37 s after joining -- twice.
        # Lobby (kind 2 / hid 2) and battle (kind 0 / hid 0) already pair the
        # same way. Answer on the kind the channel sends; UDP_HID_GROUP only
        # until its first datagram is read.
        k = getattr(chan, "group_kind", None)
        return UDP_HID_GROUP if k is None else k
    return UDP_HID
#: The character ids to try when deriving the key. The key needs the id the
#: client SELECTED, which it never tells us over TCP -- but a wrong key fails
#: the client's own MD5, so we can simply try each id we served and keep the one
#: that verifies. That is a measurement, not a guess.
UDP_KEY_IDS = [int(x) for x in
               os.environ.get("FMO_UDP_KEY_IDS", "").split(",") if x.strip()]
#: WARNING: DIAGNOSTIC, 0 by default. When >0, the first N datagrams that verify under
#: NO key we offer are written raw to logs/captures/fmo-udp-nokey-*.bin so the
#: key can be brute-forced offline (suffix + char_id) instead of guessed one
#: redeploy at a time. Set FMO_UDP_DUMP_NOKEY=8 for a scene-4 capture.
UDP_DUMP_NOKEY = int(os.environ.get("FMO_UDP_DUMP_NOKEY", "0") or "0")
_udp_nokey_dumped = 0
#: WARNING: EXPERIMENT, empty by default. Sends ONE cmd-18 chat record into the game
#: scene. If the text appears on screen, the world channel is delivering
#: content -- the first time anything has. Nothing else in the game's inbound
#: vocabulary (7, 9, 11, 12, 18, 115, 211, 240 -- everything else is silently
#: ignored) has a visible, harmless effect: 11 and 12 are DISCONNECT.
UDP_SAY = os.environ.get("FMO_UDP_SAY", "")
UDP_SAY_NAME = os.environ.get("FMO_UDP_SAY_NAME", "SERVER")

#: How many occurrences of each inbound world-channel command get dumped WHOLE.
#: 0 restores the old 24-byte-only behaviour. See the note at the record logger:
#: a truncated dump of a command we cannot name is a lost measurement, not a
#: tidy log -- cmd 115's message text was destroyed exactly that way.
UDP_DUMP_FIRST = _env_int("FMO_UDP_DUMP_FIRST", "3")

#: WARNING:KEY: THE CLIENT RECEIVES INTO A 1,400-BYTE BUFFER -- `0x611FEC96..A9`, the
#: world-channel receive loop, does `push 0x578` straight into recvfrom (static
#: 2026-09-05). A datagram longer than that is truncated by the socket, its
#: +0x04 length no longer equals the bytes received (0x6107086E), and it is
#: DROPPED IN SILENCE: the client's ack never moves, our whole tail is resent
#: every exchange, the scene never gets its records and times out into the
#: "CHANNEL RESTARTED ... NO 0x0153 armed this" loop every ~5 s -- a BLACK
#: SCREEN. Measured live 2026-09-05: six cast NPCs + the self POP + the
#: hello-ack = 8 records = 3,364 B in one datagram, ack pinned at 0, restart
#: loop; the single-NPC configuration (≈1 KB) had worked all week.
#:
#: WARNING: AND IT RETRACTS THE 2026-09-04 DIAGNOSIS. "A minimal source body breaks
#: world entry (the arm copies the whole body)" was read off a run that popped
#: FIVE bodies at once: 6 x 472 B + 40 = ~2.9 KB, over the same 1,400-B wall.
#: The bodies were never shown to be invalid; the datagram was too long. The
#: ONE-source retry "worked" because it fit. (an RE retraction.)
#:
#: So every outbound datagram is capped here, and the reliable window carries
#: the rest: FROM..TO covers only the slice sent, the peer acks that slice,
#: retire() advances tx_base, and the next exchange carries the next slice.
#: That is exactly what the window protocol is FOR; sending the whole tail
#: was only ever right while the tail was small. 1,200 stays under the
#: client's 1,400 AND under a WireGuard tunnel's 1,280-byte MTU (players on
#: the private deployment arrive through one), so no IP fragmentation either
#: -- two 472-B POP records per
#: datagram. FMO_UDP_MAX_DATAGRAM raises/lowers it for an A/B; it can never
#: send less than one record.
UDP_MAX_DATAGRAM = _env_int("FMO_UDP_MAX_DATAGRAM", "1200")
CLIENT_RECV_BUF = 0x578          # 1400, the recvfrom length at 0x611FECA2


def fit_records(pending, limit=None):
    """How many LEADING records of `pending` fit in one datagram of at most
    `limit` bytes (header included). Always at least one, so a single
    oversize record is sent (and fails loudly at the client) rather than
    starving the channel for ever."""
    limit = UDP_MAX_DATAGRAM if limit is None else limit
    total = fmoworld.HDR_LEN if fmoworld else 0x28
    n = 0
    for rec in pending:
        if n and total + len(rec) > limit:
            break
        total += len(rec)
        n += 1
    return n

#: Echo a cmd-115 chat submit back as a cmd-18 line, so the sender sees their
#: own message. Without it chat is one-way into a void: the client SENDS fine
#: (three messages arrived verbatim on 2026-08-22) and nothing ever comes back,
#: because a real server relays it to everyone in the zone and we relay it to
#: nobody. With one player, echoing to the sender IS the whole relay.
#:
#: WARNING: It is a relay, not a chat system: no history, no channels, no other
#: recipients, and `kind` is echoed rather than understood.
UDP_CHAT_ECHO = os.environ.get("FMO_UDP_CHAT_ECHO", "1") != "0"

#: VERIFIED: THE SPAWN-TABLE ORACLE. cmd 240 is the client telling us where it is,
#: ~2/s while walking, in the SAME coordinate system PilotPos is expressed in
#: -- so a player who walks to a sensible spot and reads this log has MEASURED
#: a spawn point for that map instead of guessing one.
#:
#: WARNING: Until 2026-08-28 the position line was gated on `chan.seen <= 6`, i.e. it
#: reported the SPAWN and then went blind exactly when the player walked
#: somewhere worth recording. That is why twelve maps' worth of walking
#: (2026-08-24, every lobby map, on foot) produced no table: the measurement
#: was thrown away, not missing.
#:
#: Seconds between position lines while the player keeps moving. The first
#: position of every scene is ALWAYS logged regardless (that one is the spawn
#: we served, and it is the A/B readout -- see the PilotPos note). 0 = off.
#: WARNING: compose passes `${FMO_UDP_POS_LOG_EVERY:-}`, so an unset prod .env hands
#: us "" -- `float("")` at import is the trap that took fmo down on 2026-08-27
#: (the empty-env trap) and killed a live run before that. Empty = the default.
POS_LOG_EVERY = float(os.environ.get("FMO_UDP_POS_LOG_EVERY", "").strip()
                      or "5")
#: Wait a few exchanges so the peer has adopted our index base first.
UDP_SAY_AFTER = _env_int("FMO_UDP_SAY_AFTER", "3", 10)
