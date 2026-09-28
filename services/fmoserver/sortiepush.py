"""The auto-sortie push (0x014E) that battle-group members follow."""
import os
import struct
import time
from . import missionblock, sortie


# --------------------------------------------------------------------------- #
# THE AUTO-SORTIE PUSH -- 0x014E, then the client asks 0x014D and message 1
# is GO (sortie brief, 2026-09-04). The route battle-group MEMBERS ride: it
# needs NO 0x0139 from the client, so it cannot park the 0x0139 poller.
# --------------------------------------------------------------------------- #
# WARNING: ID CORRECTION to the 2026-08-27 entry, which called this push "0x014C
# (arm 0x6117E33B)". The dispatcher 0x6117DFEC maps 0x014C -> 0x6117E33B, and
# that arm never touches lobby+0x4F1A -- it is the BATTLE END push (SE's own
# debug string at 0x6133BC60: "戦闘終了。ロビーマップに戻ります。" = "battle
# over, returning to the lobby map"; see MSG_BATTLE_END, whose 0x5D-dword result
# block is what lands at lobby+0x69C6). The arm that matches every documented field
# is 0x6117EAE7, reached by `sub eax, 0x14E; je` -- so the auto-sortie push
# id is 0x014E. The 08-27 field decode was right; the id and the arm address
# were misattributed.
#
# 0x014E -- the arm 0x6117EAE7 (packet offset = payload offset + 0x14):
#     gate: 0x611734E0(lobby) -- [lobby+0x20]==4 && [lobby+0x24] not in
#           {0,3} -- and [lobby+0x24] != 9, else dropped without a trace.
#     +0x00  u32  id      -> lobby+0x4F1A (0x6117EB7A; 0x6117C10D later moves
#                            it to +0x4F08, the slot 0x0139's +0x00 fills)
#     +0x04  u32  time    -> lobby+0x4F1E (0x6117EB83). TWO consumers: the
#                            countdown banner, and 0x6117C0F3 stores
#                            time % 1e6 -> lobby+0x4F02 -> globals+0x1B8, THE
#                            BATTLE SEED (0x013A's hdr+0x0C twin).
#     +0x08  20 B endpoint-> lobby+0x4F22 (0x6117EB8E), handed to 0x61006270
#                            at scene entry -> globals+0x28, the battle UDP
#                            door.
#     +0x34  3400B block  -> lobby+0x5C7E via the SAME 0x61175600 the 0x013A
#                            arm uses (0x6117EBC7, arg = packet+0x48). MB_*
#                            offsets apply: MapNo at block+0x00 is the type-1
#                            id scene 4's phase-0 arm 0x61004C30 registers.
#     (+0xB0  u32, = block+0x7C: the arm reads it FIRST (0x6117EB00) as the
#            banner-text selector -- bit7 8:79 counterattack / bit8 8:78 /
#            bit6 8:77 mission battle / bit0 8:76 special mission; all clear
#            = 8:3 "Sortieing to the battle map automatically in %d seconds."
#            Served ZERO, so 8:3 is the banner.)
#     +0xD7C 88 B info    -> globals+0x78 (0x6117EBE7, 0x16 dwords from
#                            packet+0xD90). Served zero, like 0x013A's.
#     +0xDD4 sz   dest    -- the %s of 8:80 "Your destination is %s."
#                            (0x6117EB54 pushes packet+0xDE8 to the fmt call).
#                            cp932, NUL inside our 0x40-byte field so the
#                            read stays in the payload.
#   The arm also latches lobby+0x4F16 = 1 and stamps +0x4F12 with the ms tick
#   (0x613CACA0), and raises the banner flag [0x613B92B0]+0xED6.
#
# WHAT FIRES NEXT (read from the image; NONE of it has ever run live):
#   * the lobby tick 0x6117C1C0 (switch on [lobby+0x24]-1, table 0x6117DCC0)
#     checks the +0x4F16 latch in the NORMAL state's arm (0x6117CEE2, case 1
#     = state 2) and in cases 6/9..13: fires 5,000 ms after the stamp
#     (0x6117CEF5) -- WARNING: but ONLY while [globals+0x1C4] == 6, SCENE 6, THE
#     WARM SCENE (0x6117CF02). A cold login sits in scene 7 and the countdown
#     NEVER STARTS until a real Move re-enters the world warm. Firing sets
#     [lobby+0x24]=4, [lobby+0x2C]=0.
#   * the countdown machine 0x6117BEF0 (tick case 3, on [lobby+0x2C]):
#     state 0 shows the banner; state 1 SENDS 0x014D (empty; 0x6117C171
#     `push 0x14D`) once the clear-to-transmit guard 0x6119A380 allows, and
#     keeps its seq at [lobby+0x54]; state 2 polls that seq -- message 1 ->
#     0x6117C0F3 enters scene 4 (0x6117AC40(1,0), then 0x61006270 with the
#     latched seed + endpoint; the block is already in lobby+0x5C7E), any
#     other id -> 8:29 "Battle map login failed" and a clean reset
#     ([lobby+0x24]=2). AN UNANSWERED 0x014D PARKS STATE 2 FOREVER -- the
#     0x01AB shape -- so 0x014D is ALWAYS answered below, push or no push.
#
# WARNING: ORDERING HAZARD, from the reset 0x6117A2A8's own stosd ranges: a scene
# change zeroes lobby+0x5C7E (the block) but NOT +0x4F12..+0x4F36 -- so a
# push followed by a Move leaves the latch armed over a ZEROED block, and
# the countdown would register type-1 id 0 blind. That is why the push is
# sent ONCE per session, deferred behind the LATEST grant, and the live
# procedure says: stand still after it lands.
MSG_SORTIE_PUSH = 0x014E
MSG_SORTIE_GO = 0x014D
R14E_ID = 0x00                         # u32 -> lobby+0x4F1A (0x6117EB7A)
R14E_TIME = 0x04                       # u32 -> lobby+0x4F1E (0x6117EB83)
R14E_ENDPOINT = 0x08                   # 20 B -> lobby+0x4F22 (0x6117EB8E)
R14E_BLOCK = 0x34                      # 3,400 B -> lobby+0x5C7E (0x6117EBC7)
R14E_FLAGS = R14E_BLOCK + 0x7C         # 0xB0: the 8:76..79 selector (zero=8:3)
R14E_INFO88 = R14E_BLOCK + missionblock.M1C1_BLOCK_LEN    # 0xD7C -> globals+0x78
R14E_DEST = R14E_INFO88 + sortie.R13A_INFO88_LEN    # 0xDD4, the 8:80 %s
R14E_DEST_LEN = 0x40
REPLY_014E_LEN = R14E_DEST + R14E_DEST_LEN   # 0xE14 = 3,604 B

#: WARNING: OFF BY DEFAULT ('' = never sent). Set to a TYPE-1 map id to arm the
#: auto-sortie: ONE 0x014E rides the first keepalive FMO_SORTIE_PUSH_DELAY s
#: after the latest MOVE grant (0x016D -> 0x0153 -- the warm entry the
#: countdown gate needs; a cold login sits in scene 7 where the gate never
#: passes). Gated on TYPE1_ON_DISK exactly like the other two MapNo knobs.
#: NOT LIVE-TESTED; scene 4 has never run against this server.
SORTIE_PUSH_MAPNO = os.environ.get("FMO_SORTIE_PUSH", "").strip()
#: payload+0x04: the countdown seconds AND (% 1e6) the battle seed. WARNING: compose
#: passes "${FMO_SORTIE_PUSH_TIME:-}", so empty must read as the default --
#: int("") at import is the 2026-08-27 outage.
SORTIE_PUSH_TIME = int(
    os.environ.get("FMO_SORTIE_PUSH_TIME", "").strip() or "10", 0)
#: Seconds between the move grant and the push. Keepalives run every 15 s
#: (0x6117C2D3, 0x3A98 ms), so the default lands on the first one after the
#: scene rebuild -- after the reset that would wipe the block, before the
#: player wanders off.
SORTIE_PUSH_DELAY = float(
    os.environ.get("FMO_SORTIE_PUSH_DELAY", "").strip() or "12")
#: The %s of 8:80 "Your destination is %s." Empty = "Map <id>".
SORTIE_PUSH_DEST = os.environ.get("FMO_SORTIE_PUSH_DEST", "").strip()


def sortie_push_armed():
    """True when FMO_SORTIE_PUSH names anything at all (validity is checked
    where it is used, so a bad id is a loud refusal rather than silence)."""
    return SORTIE_PUSH_MAPNO != ""


def sortie_push_mapno(spec=None):
    """(type-1 id or None, source) for the PUSH knob -- sortie_mapno()'s gate
    (TYPE1_ON_DISK, the 0x611250A2 crash) with this knob's name on it."""
    s = SORTIE_PUSH_MAPNO if spec is None else spec
    if s is None or (isinstance(s, str) and s.strip() == ""):
        return None, ("REFUSED: FMO_SORTIE_PUSH is unset -- a zero block "
                      "would register type-1 id 0 blind; set an on-disk id")
    mn, src = sortie.sortie_mapno(s)
    return mn, src.replace("FMO_SORTIE_MAPNO", "FMO_SORTIE_PUSH")


def sortie_push_fields(mapno=None, time_s=None, dest=None, ep_enable=None,
                       host=None, port=None, **knobs):
    """(label, PAYLOAD offset, raw bytes, source) for every 0x014E field.

    The endpoint reuses the FMO_SORTIE_HOST/PORT/ENDPOINT knobs (same battle
    door, same EP_0153_NET flavour); the block fields reuse mission_fields()
    exactly as reply_013a() does, shifted to +0x34. A refused MapNo is
    returned as a note with empty bytes and reply_014e() then returns None."""
    out = []
    mn, src = sortie_push_mapno(mapno)
    out.append(("MapNo", R14E_BLOCK + missionblock.MB_MAPNO,
                struct.pack("<I", mn) if mn is not None else b"", src))
    t = SORTIE_PUSH_TIME if time_s is None else time_s
    out.append(("time", R14E_TIME, struct.pack("<I", t),
                f"FMO_SORTIE_PUSH_TIME={t}: the countdown seconds AND "
                f"(% 1e6) the battle seed lobby+0x4F02 -> globals+0x1B8"))
    ep_on = sortie.SORTIE_ENDPOINT if ep_enable is None else ep_enable
    if ep_on:
        h = host or sortie.SORTIE_HOST or addressing.BATTLE_HOST
        pt = port or sortie.SORTIE_PORT or addressing.BATTLE_PORT
        _ep = addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint
        out.append(("endpoint", R14E_ENDPOINT, _ep(h, pt),
                    f"{h}:{pt} -> lobby+0x4F22 -> globals+0x28 "
                    f"({'net' if addressing.EP_0153_NET else 'le'} order; "
                    f"FMO_SORTIE_HOST/PORT reused)"))
    d = SORTIE_PUSH_DEST if dest is None else dest
    if not d:
        d = f"Map {mn if mn is not None else '?'}"
    raw = d.encode("cp932", "replace")[:R14E_DEST_LEN - 1] + b"\0"
    out.append(("dest", R14E_DEST, raw,
                f"{d!r} -- the %s of 8:80 'Your destination is %s.'"))
    if missionblock.BATTLE_START_TIME and "start_time" not in knobs:
        knobs["start_time"] = int(time.time())
    for label, off, raw2, s in missionblock.mission_fields(mapno=0, **knobs):
        out.append((label, R14E_BLOCK + off, raw2, s))
    return out


def reply_014e(**knobs):
    """The 3,604-byte 0x014E payload, or None when the MapNo is refused."""
    fields = sortie_push_fields(**knobs)
    if any(not raw for label, _o, raw, _s in fields if label == "MapNo"):
        return None
    b = bytearray(REPLY_014E_LEN)
    for _label, off, raw, _src in fields:
        if raw:
            b[off:off + len(raw)] = raw
    return bytes(b)


def sortie_push_packet(conn_id, **knobs):
    """The full 0x014E push on the queue sequence, or None when refused.

    The dispatcher 0x6117DFEC takes it the way it takes our 0x016C and 0x019A
    pushes; nothing polls for it, so QUEUE_SEQ is correct here."""
    body = reply_014e(**knobs)
    if body is None:
        return None
    return packet.build(MSG_SORTIE_PUSH, body, pushes.QUEUE_SEQ, conn_id)


# Called at run time only; imported last so that import cycles resolve.
from . import addressing, packet, pushes  # noqa: E402
