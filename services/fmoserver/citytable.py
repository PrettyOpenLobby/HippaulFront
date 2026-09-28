"""The City Control table push (0x019A)."""
import os
import struct
from .deps import fmowar


#: KEY: THE CITY CONTROL SCREEN (systext group 30) -- static RE 2026-08-27,
#: briefing-room-ui worker. The window class (ctor 0x610E86E0, vtable
#: 0x61334100, the only pusher of 0xC01E000E..0xC01E0015 "City Control / City
#: Name / Control / B.G.Cost / Rank / O.C.U. / U.S.N. / Deadlock") is built in
#: exactly TWO places in the image:
#:   (A) 0x610FB500 -- entry 12 of the scene-script NATIVE list 0x613BB7B8
#:       (directory 0x61334DC8 maps group 0xE300 -> that list; the dispatcher
#:       0x610F64D0 = manager sub-vtable 0x61335418 slot 18 reads `word [rec+6]`,
#:       group = &0xFFF0, index = &0xF). So the opener is native **0xE30C**,
#:       reached only through the scene SCRIPT interpreter (0x61110930, arms
#:       0x27/0x28 -> slot 6 thunk 0x61107BB0 -> slot 18). No menu, message
#:       or phase arm calls it: `xref 0x610FB500` is the table and nothing else.
#:   (B) 0x61192B46 -- state 3 of the mission-RESULT machine 0x61192780
#:       (0x0175 -> 0x0176 exchange, a 20-entry table at +0x6C), not our path.
#: What it DRAWS comes from lobby+0x6C36: a u32 city count and 8-byte rows from
#: lobby+0x6C4A/+0x6C4C (u16 city id at row+2, /0x14 = region bucket; the ctor
#: also lifts a u32 at row+4). That block has ONE network writer in the image:
#: the 0x019A arm 0x6117F5F9 (`rep movsd 0x69` from packet+0x24 = payload+0x10,
#: 420 B), guarded on the dispatcher's `[conn+0x20] == 4` and reached through
#: the fixed-sequence poll 0x6117DFBD -- i.e. 0x019A is a PUSH (seq 0x7FFFFFFE)
#: like 0x017D, not a reply to anything (nothing in the image sends 0x0199).
#: The renderer 0x610E7E00 prints "Please wait a moment..." (0xC01E0005) for
#: any row index >= that count, so a window opened over an empty block is the
#: "Please wait" screen; 0x610E7A10 draws "[Home] Refresh" (0xC01E000D).
#:
#: WARNING: WHAT THIS KNOB IS AND IS NOT. Serving 0x019A fills the block the screen
#: reads; it does NOT open the screen -- (A) is the script's call, and whether
#: MapKind 509's script (data\AJ\F40\D54.DAT) issues 0xE30C is UNDECIDED: the
#: on-disk bytecode does not carry native ids verbatim (no `0C E3` word follows
#: any opcode-0x27/0x28 word in 509, 100 or 98 beyond noise), so the script
#: route could not be settled statically.
#:
#: VERIFIED:KEY: SETTLED LIVE 2026-09-12 (a warzone lobby): the screen OPENS
#: on its own -- the zone script's 0xE30C -- and immediately queues a kind-7
#: job on the second server with `count = [lobby+0x6C36]` = 0, because this
#: block was never sent; with no record to receive its callback (0x610E8130)
#: never marks the screen ready, so it shows nothing and IGNORES ESCAPE (the
#: player: "can't hit escape, the game hasn't frozen"). The block IS the
#: city list, and its row (ctor 0x610E8934, per row `ebp`) is
#:     +0x00 u16  ZONE SELECTOR -- `movzx eax,[ebp]` -> ARE resource
#:                0x140EF + selector (82159 + 509 = 82668 = AI/F26/D68, FZ-10),
#:                bucketed by /0x14 into [screen+0x19C..] for the city NAME
#:     +0x02 u32  the KIND-7 ID the screen asks about (`mov ecx,[ebp+2]` ->
#:                [screen+0x194][i]) = 903,000,000 + tile, like the war map
#:     +0x06 u16  unread
#: so a city is a SECTOR: (selector, tile). Rows: `selector:tile`; empty =
#: fmowar.CITIES (SE's nineteen economic cities) whenever FMO_WAR != 0.
CITY_TABLE = [e for e in os.environ.get("FMO_CITY_TABLE", "")
              .replace(" ", "").split(",") if e]
MSG_CITY_TABLE = 0x019A
CITY_TABLE_BLOCK = 0x69 * 4            # 420, the `rep movsd 0x69` at 0x6117F60C
CITY_TABLE_OFF = 0x10                  # payload+0x10 -> lobby+0x6C36 (count)
#: WARNING: CORRECTED 2026-09-12, LIVE: the row base is lobby+**0x6C4C**, not 0x6C4A.
#: Both readers agree once you put it there -- the kind-7 queuer 0x610E8130
#: buckets on `word[lobby + 0x6C4C + i*8]` and the ctor 0x610E8934 walks from
#: the same place (`movzx eax,[ebp]` selector, `mov ecx,[ebp+2]` the u32 id,
#: `add ebp,8`). Anchored two bytes early, the client read our ID's LOW half as
#: the selector (ARE resource 0x140EF + 64558 -> nothing, so every City Name
#: drew EMPTY) and our id's HIGH half as the id: the player's screen asked
#: kind 7 for `13779, 13780, 13780 ...` = 0x35D3/0x35D4, the top halves of
#: 903,0xx,xxx. Measured 15:17:06Z.
CITY_ROW_OFF = CITY_TABLE_OFF + (0x6C4C - 0x6C36)   # payload+0x26 -> lobby+0x6C4C
CITY_ROW_LEN = 8
#: KEY: THE ENTRY IS 8 BYTES FROM lobby+0x6C4A (static 2026-09-12, City
#: Control's row draw 0x610E83C0): {u8 ?, u8 RANK, u16 selector, u32 kind-7
#: id}. The two readers above index selector and id from 0x6C4C = entry+2;
#: the Rank column prints byte entry+1 with "%d" (0x610E868E, via row+0x14 =
#: &[lobby+0x6C4A + i*8]). Rank is NOT in the kind-7 record, which is why no
#: record mark ever moved it. SE's war score is the sum of the held cities'
#: RANKS (「制圧下にある経済都市ランクの合計」), i.e. the phase page's control
#: points, so Rank = fmowar.CITIES points. Rank 0 leaves the old bytes.
CITY_ENTRY_OFF = CITY_ROW_OFF - 2            # payload+0x24 -> lobby+0x6C4A
CITY_RANK = 1
#: 50: the block minus the count and the 16 undecoded bytes before row 0.
CITY_ROW_MAX = (CITY_TABLE_BLOCK - (CITY_ROW_OFF - CITY_TABLE_OFF)) // CITY_ROW_LEN
CITY_TABLE_LEN = CITY_TABLE_OFF + CITY_TABLE_BLOCK


def parse_city_table(entries):
    """`selector:tile` rows -> [(selector, tile)], capped at what the block holds."""
    rows = []
    for e in entries:
        sel, _, tile = e.partition(":")
        rows.append((int(sel, 0) & 0xFFFF, int(tile or "0", 0)))
    return rows[:CITY_ROW_MAX]


CITY_ID_BASE = 903_000_000


def city_rows():
    """The city list to serve: FMO_CITY_TABLE's rows, else SE's nineteen
    economic cities from the war model while FMO_WAR is on, else nothing."""
    if CITY_TABLE:
        return parse_city_table(CITY_TABLE)
    if fmowar is not None and warstate.WAR != "0":
        return fmowar.city_rows()[:CITY_ROW_MAX]
    return []


def city_rank(tile):
    """A city's Rank column: its control points in SE's phase table
    (fmowar.CITIES), else 0."""
    c = fmowar.CITIES.get(int(tile)) if fmowar is not None else None
    return int(c[1]) if c else 0


def city_table_payload(rows):
    """The 0x019A payload: 0x10 undecoded bytes, then the 420-byte block the
    client copies to lobby+0x6C36 -- u32 count, 16 undecoded bytes, then
    8-byte rows {u16 selector, u32 903e6 + tile, u16 0} anchored at
    lobby+0x6C4A (the ctor 0x610E8934 reads the selector at row+0 and the
    kind-7 id at row+2)."""
    b = bytearray(CITY_TABLE_LEN)
    struct.pack_into("<I", b, CITY_TABLE_OFF, len(rows))
    for i, row in enumerate(rows):
        sel, tile = row[0], row[1]
        rank = row[2] if len(row) > 2 else city_rank(tile)
        struct.pack_into("<BBHI", b, CITY_ENTRY_OFF + i * CITY_ROW_LEN,
                         0, int(rank) & 0xFF, sel & 0xFFFF,
                         (CITY_ID_BASE + int(tile)) & 0xFFFFFFFF)
    return bytes(b)


def city_table_push(conn_id):
    """The 0x019A CITY-TABLE push, or None when FMO_CITY_TABLE is empty.

    0x019A is the FIRST id of the lobby dispatcher 0x6117F2A0 (`sub eax,0x19a;
    cmp eax,0x2d; ja default`), index 0 -> arm 0x6117F5F9, which `rep movsd 0x69`
    (420 B) from packet+0x24 into lobby+0x6C36 -- but ONLY while [conn+0x20]==4,
    i.e. the client is IN-WORLD. So this must ride an IN-SCENE poll, not the
    grant batch (which fires as the client is still leaving the lobby). Nothing
    in the image ever sends 0x0199, so 0x019A is a pure PUSH: it goes out on the
    fixed queue sequence, the same path 0x017D uses.

    It fills the block the City Control window (systext group 30) reads; it does
    NOT by itself open that window. The window's only opener on our path is the
    zone SCRIPT's native call 0xE30C (list 0x613BB7B8 entry 12, reached only
    through the script VM 0x61110930 -> dispatcher 0x610F64D0). Whether MapKind
    509's script (D54.DAT) GATES that call on this block being non-empty is the
    open question this push exists to answer: serve it, re-enter the briefing
    room, and see whether City Control appears."""
    rows = city_rows()
    if not rows:
        return None
    return packet.build(MSG_CITY_TABLE, city_table_payload(rows), pushes.QUEUE_SEQ, conn_id)


# Called at run time only; imported last so that import cycles resolve.
from . import packet, pushes, warstate  # noqa: E402
