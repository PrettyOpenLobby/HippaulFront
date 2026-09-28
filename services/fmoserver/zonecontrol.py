"""The zone-control push (0x016C): which nation holds each zone."""
import os
import struct


#: PROBE: THE ZONE-CONTROL PUSH -- `0x016C`, and it is why every sector in Change
#: Area refuses. Static RE 2026-09-04, **NOT LIVE-TESTED**.
#:
#: THE SCREEN'S TWO GATES. Picking a sector reads
#: `[screen + (zoneId % 100)*4 + 0xF8]` (0x610110A3); a negative is the refusal,
#: -1 raises systext 17:68 "That area cannot be selected right now." and -2
#: raises 17:69 "That is the area you are in.". That state has ONE writer, the
#: list filler 0x61010C2B, and it sits behind two gates:
#:
#:   (A) CLIENT DATA, immovable: `ZoneTable.Flag(kind*100 + idx, nation) == 1`,
#:       read out of Data/AI/F31/D83.DAT -- 77 records of 0x10 (+0x00 u16 kind,
#:       +0x02 u16 id, +0x04/+0x06 the per-nation flag words, +0x08/+0x0C the
#:       short/long name pointers; loader 0x61083DB0, accessor 0x61083EA0).
#:       In the shipped file the only kind-5 rows with a non-zero flag are
#:       505 / 509 / 513 -- FZ-06, FZ-10 and FZ-14, the same three warzones
#:       whose sector names exist in AJ/F33/D85.DAT. The other seventeen
#:       `FZ:Area NN` rows are placeholders and NOTHING WE SERVE CAN OPEN THEM.
#:   (B) SERVER DATA, and this is the wall: 0x611794A0 walks a table at
#:       lobby+0x7724 through 0x611A3AA0 -- u32 count at +0x0C, then
#:       `{s16 zoneId; u8 ocu; u8 usn}` from +0x10. That address has exactly
#:       three sites in the whole image: that reader, the zero-init
#:       `rep stosd 0x84` in the lobby ctor (0x6117A2A8), and ONE writer --
#:       the 0x016C arm at 0x6117F013. We have never sent 0x016C, so the count
#:       is 0, every lookup returns -1, and every area refuses.
#:
#: KEY: AND THE LABEL WAS NEVER A BUG. "FZ-10: Freedom City" in every room is this
#: same table read with OUR OWN MapKind: 0x611D8EB5 renders NameA(globals+0x1A4)
#: under systext 14:41 "Area %s ...", and row 509 -- FMO_MAPKIND, picked so
#: 0x6108B8A1 un-greys Briefing Room -- IS that string. Do not "fix" the label
#: by moving MapKind; it is the same knob.
#:
#: THE VALUE, AND WHY THE DEFAULT IS 1. 0x611794A0 takes the entry byte for the
#: player's nation and runs it through the 10x10 matrix at 0x613966E4
#: (0x611A3B00): `m = matrix[v + tier*10]`, both operands 1..10 or it returns 0.
#: m == 2 is "selectable outright", m == 0 refuses, anything else is the
#: confirmation arm. Read out of the image, the v = 1 column is the ONLY one
#: that is non-zero for every tier 1..10, and it is 2 for every tier >= 3 -- so
#: 1 is not a guess, it is the value that maximises the chance of "selectable"
#: with no information about the tier. That is why it is the default.
#:
#: WARNING: AND TWO WAYS THIS STILL REFUSES, both outside this table:
#:   * `tier` derives from lobby+0x8B5 (payload +0x29) and the 96-byte window at
#:     payload +0x6AC, BOTH SERVED AS ZERO, so it is UNMEASURED. If it resolves
#:     OUTSIDE 1..10 the matrix returns 0 for every v and NO value here can open
#:     the screen.
#:   * tiers 1 and 2 can only ever reach the CONFIRMATION arm (m == 1), and the
#:     filler then keeps the refusal unless the cost byte 0x611A3B30 reads --
#:     `byte[lobby+0x8C8 + 0x280 + kind-1]`, i.e. 0x014A payload +0x2BC+kind-1,
#:     inside the owned-items range we deliberately keep zero -- is > 0
#:     (0x61010CB7 `jle`). So tier 1/2 refuses too, for a different reason.
#: If the push is confirmed on the wire and the screen still refuses, the tier
#: is the operand to chase, not this table.
#:
#: WARNING: THE SECOND NECESSARY CONDITION. 0x611A3AA0 reads the entry byte only when
#: `byte [lobby+0x8B4]` -- S14A_NATION, payload +0x28 -- is EXACTLY 1 or 2; any
#: other value falls through both compares and returns -1. We serve 0 by
#: default, so THIS PUSH ALONE CANNOT OPEN THE SCREEN. Pair it with
#: FMO_STATUS_NATION=1 (or 2). WARNING: That is not free: script_id_for() shows a
#: nation of 1/2 substitutes script 98/99 for MapKind 509, so such a run is NOT
#: single-variable. This knob logs the pairing loudly rather than setting the
#: nation itself -- a knob that quietly sets a second knob is exactly how this
#: project earns unattributable readings.
#:
#: WARNING: AND A MOVE WIPES IT. The zero-init above lives in the same lobby reset
#: (0x6117A2F6) that wiped the city block on 2026-08-27, so the table does not
#: survive a scene change. That is why this rides the REPEATED 0x01AC poll
#: instead of being sent once: every poll re-fills it, so a wipe self-heals and
#: no deferred-resend bookkeeping is needed.
#:
#: Rows are `id[:ocu[:usn]]`; usn defaults to ocu, ocu defaults to 1. The bare
#: token `live` expands to the three warzones gate (A) leaves selectable, so an
#: operator does not spend a launch listing ids the client will refuse anyway.
#: Default OFF, and empty reads as UNSET, not as "serve zero rows"
#: (the empty-env trap).
ZONE_CONTROL = [e for e in os.environ.get("FMO_ZONE_CONTROL", "")
                .replace(" ", "").split(",") if e]
MSG_ZONE_CONTROL = 0x016C
ZONE_BLOCK = 0x84 * 4                  # 528, the `rep movsd 0x84` at 0x6117F02A
ZONE_BLOCK_OFF = 0x10                  # payload+0x10 -> lobby+0x7724 (msg+0x24)
ZONE_COUNT_OFF = ZONE_BLOCK_OFF + 0x0C  # payload+0x1C -> T+0x0C, the u32 count
ZONE_ROW_OFF = ZONE_BLOCK_OFF + 0x10   # payload+0x20 -> T+0x10, the entries
ZONE_ROW_LEN = 4                       # {s16 zoneId; u8 ocu; u8 usn}
#: 128: the block minus the 0x10 bytes ahead of entry 0, over the 4-byte stride.
ZONE_ROW_MAX = (ZONE_BLOCK - 0x10) // ZONE_ROW_LEN
ZONE_CONTROL_LEN = ZONE_BLOCK_OFF + ZONE_BLOCK      # 544
#: The three kind-5 rows gate (A) leaves selectable: FZ-06, FZ-10, FZ-14.
ZONE_LIVE_WARZONES = (505, 509, 513)
#: payload+0x00 -> lobby+0x7DEB. WARNING: Write-only: nothing in the image reads that
#: address, so it is served zero rather than guessed at.
ZONE_HEAD_OFF = 0x00


#: KEY: THE ROWS THE CLIENT'S OWN TABLE ALLOWS, transcribed from D83 so the
#: `all` token does not have to guess. Gate (A) in the filler 0x61010C8C is
#: `ZoneTable.Flag(kind*100 + idx, nation) == 1`.
#: WARNING: CORRECTED 2026-09-12 by dumping the file: D83 is BIG-ENDIAN with an
#: 8-byte header and 16-byte records, and record+0x04 / +0x06 are the ZONE ID
#: and the KIND, not the flags -- the per-nation flags are the two u16s at
#: record+0x00 (O.C.U.) and +0x02 (U.S.N.), which differ only in their low bit
#: (0x4879/0x4878, 0x0814/0x0815). The ZONE_ROWS_D83 sets below were read off
#: the file and ARE right; only the field names were wrong. Reading Data/AI/F31/D83.DAT
#: (77 records of 0x10 from +0x08) and keeping every row whose id really is
#: `kind*100 + id%100` gives exactly these, 15 per nation across FIVE kinds:
#:
#:   nation 1 (O.C.U.): 100 101 102 107 108 109 | 200 201 202 207 | 407 |
#:                      505 509 513 | 600
#:   nation 2 (U.S.N.): 300 301 302 307 308 309 | 400 401 402 407 | 207 |
#:                      505 509 513 | 600
#:
#: WARNING: This is SHIPPED CLIENT DATA, not a choice of ours. Serving a zone that
#: is not here cannot open it; serving one that is here is necessary but not
#: sufficient (gate B, the served table, still has to say yes).
#: Verify with a D83 reader against the unpacked image.
ZONE_ROWS_D83 = (
    # (zoneId, ocu, usn) -- ocu/usn are the D83 flag words, 1 = selectable
    (100, 1, 0), (101, 1, 0), (102, 1, 0), (107, 1, 0), (108, 1, 0),
    (109, 1, 0),
    (200, 1, 0), (201, 1, 0), (202, 1, 0), (207, 1, 1),
    (300, 0, 1), (301, 0, 1), (302, 0, 1), (307, 0, 1), (308, 0, 1),
    (309, 0, 1),
    (400, 0, 1), (401, 0, 1), (402, 0, 1), (407, 1, 1),
    (505, 1, 1), (509, 1, 1), (513, 1, 1),
    (600, 1, 1),
)


def parse_zone_control(entries):
    """`id[:ocu[:usn]]` rows -> [(zone_id, ocu, usn)], capped at the block.

    usn defaults to ocu and ocu defaults to 1 (the matrix value that is
    "selectable" for every tier >= 3). The bare token `live` expands to
    ZONE_LIVE_WARZONES at 1/1.
    """
    rows = []
    for e in entries:
        if e.lower() == "live":
            rows.extend((z, 1, 1) for z in ZONE_LIVE_WARZONES)
            continue
        if e.lower() == "all":
            # Every row gate (A) can accept, with the client's OWN per-nation
            # flags copied across rather than forced to 1 -- serving a 1 where
            # D83 has a 0 cannot help (the filler consults D83 first) and would
            # only make the log lie about what is selectable.
            rows.extend(ZONE_ROWS_D83)
            continue
        zid, _, rest = e.partition(":")
        ocu, _, usn = rest.partition(":")
        rows.append((int(zid, 0) & 0xFFFF,
                     int(ocu or "1", 0) & 0xFF,
                     int(usn or ocu or "1", 0) & 0xFF))
    return rows[:ZONE_ROW_MAX]


def zone_control_payload(rows):
    """The 0x016C payload: 544 bytes, of which the client copies 528 from
    payload+0x10 into lobby+0x7724 (`rep movsd 0x84` at 0x6117F02A).

    Only the count and the entries are written. Everything else stays zero,
    including payload+0x00 -- the byte the arm stores to lobby+0x7DEB, which no
    instruction in the image reads back.
    """
    b = bytearray(ZONE_CONTROL_LEN)
    struct.pack_into("<I", b, ZONE_COUNT_OFF, len(rows))
    for i, (zid, ocu, usn) in enumerate(rows):
        struct.pack_into("<HBB", b, ZONE_ROW_OFF + i * ZONE_ROW_LEN,
                         zid, ocu, usn)
    return bytes(b)


def zone_control_push(conn_id):
    """The 0x016C ZONE-CONTROL push, or None when FMO_ZONE_CONTROL is empty.

    0x016C is the second id of the lobby dispatcher 0x6117ECC8
    (`sub eax,0x16b; cmp eax,0x1d; ja default`, byte table 0x6117F8DC -> case 1
    -> arm 0x6117F013), which is gated on [lobby+0x20]==4 like every other
    unsolicited arm. Nothing in the image ever REQUESTS it, so it is a pure
    push and goes out on the queue sequence, the same path 0x019A and 0x017D
    use.

    It fills the block Change Area's selectability predicate reads; it does not
    open the screen (the screen already opens) and it cannot un-refuse the
    seventeen placeholder warzones, which gate (A) refuses from client data.
    """
    if not ZONE_CONTROL:
        return None
    return packet.build(MSG_ZONE_CONTROL,
                        zone_control_payload(parse_zone_control(ZONE_CONTROL)),
                        pushes.QUEUE_SEQ, conn_id)


def zone_control_nation_warning():
    """The WARNING: line to print when the push is armed but the nation is not 1/2,
    which makes 0x611A3AA0 return -1 no matter what this table holds. Empty
    string when the pairing is right, so the caller can print unconditionally.
    """
    if status.STATUS_NATION in (1, 2):
        return ""
    # WARNING: Only claim the script substitution when script_id_for()
    # actually performs it -- that needs FIELD_18 == 1 AND a MapKind outside
    # 600..607. Announcing "this also changes the scene" when it does not is
    # the same over-claim as staying quiet when it does.
    _sub = zoneentry.script_id_for(zoneentry.MAPKIND, 1)
    _cav = (f" (WARNING: which ALSO substitutes script {_sub} for MapKind "
            f"{zoneentry.MAPKIND} via script_id_for, so that run is NOT "
            f"single-variable)" if _sub != zoneentry.MAPKIND else
            f" (script_id_for leaves MapKind {zoneentry.MAPKIND} as it is in this "
            f"configuration, so the nation would be a single variable)")
    return (f"WARNING: FMO_ZONE_CONTROL IS ARMED BUT FMO_STATUS_NATION IS "
            f"{status.STATUS_NATION} -- 0x611A3AA0 reads the entry byte only for a "
            f"nation of EXACTLY 1 or 2 and returns -1 for anything else, so "
            f"this push CANNOT open Change Area on its own. Set "
            f"FMO_STATUS_NATION=1" + _cav + ".")


# Called at run time only; imported last so that import cycles resolve.
from . import packet, pushes, status, zoneentry  # noqa: E402
