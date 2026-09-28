"""The battle map list (0x0162, 0x01F4 -> 0x01F5)."""
import os
import struct


#: KEY: THE SCRAMBLE BOARD'S OTHER THREE REQUESTS (static 2026-09-06
#: -- the lobby Map Selector native E302 is a `ret 8`
#: STUB in this client build, so the Scramble Board window IS the road to a sector):
#:   0x0162 (20 B, sender 0x61180C40 = the board's TRAINING SECTOR entry) -> 0x0163.
#:     Its poller (0x61180D01) tests ONLY word[+6] == 0x163 and posts UI event
#:     0x10D1; any other id posts 0x10D2 with word[+8] as the code. Body unread.
#:   WARNING: CORRECTED 2026-09-06: 0x01F4 / 0x01F6 are
#:     NOT the retail war map. Their ONE sender in the whole image is the ctor
#:     0x61184AA0, and that object is constructed from exactly ONE place: command
#:     0x1008 in the message handler 0x61185D20 of the class SE names "Lobby
#:     Menu" (ctor 0x61183E80, table 0x61395DD0) -- a DEVELOPER menu whose rows
#:     read 'Play Game Normally', 'Setup', 'Select field', 'Go to lobby', 'Loot',
#:     'Trade', 'DEBUG Get weapon/body/legs/arms/bpack/item', 'Return'. It only
#:     builds when the lobby is in menu mode [lobby+0x34] == 0, which the retail
#:     flow never enters. The REAL war map is 0x015E / 0x0160 (see MSG_015E_REQ).
#:     Keep these arms anyway -- they are correct for what they answer and cost
#:     nothing -- but do NOT tune FMO_BATTLE_MAPS expecting the war map to change.
#:   0x01F4 (0 B) and 0x01F6 (136 B), BOTH sent by that ctor
#:     0x61184AA0 at construction. 0x01F4 -> 0x01F5: the parse 0x611857E1 copies
#:     0x961 dwords (9,604 B) from payload+0 into window+0xB5 = u32 COUNT, then
#:     4-byte rows {u16 id, u8 kind, u8 -}. The list builder 0x61185910 keeps a
#:     row when kind & 0xF (the whole kind once the window mode >= 0x10) equals
#:     the window's mode byte, and prints "%c[%d %2d %3d] %s" = owner letter
#:     (0x61174D00: N / S / ?), kind & 0xF, kind >> 4, id, 0x61175840(kind, id)
#:     = the name. Count 0 is SE's own "No Battle Map" (10:38).
#:     0x01F6's poller (0x61185A9E) wants message 1 on its seq.
#:   Silence parks every one of them (the 0x0156 precedent).
MSG_0162_REQ = 0x0162
MSG_0163_REPLY = 0x0163
MSG_01F4_REQ = 0x01F4
MSG_01F5_REPLY = 0x01F5
MSG_01F6_REQ = 0x01F6
S1F5_BODY_LEN = 0x961 * 4               # 9604
ANSWER_0162 = os.environ.get("FMO_ANSWER_0162", "1").strip() or "1"


def _parse_battle_maps(spec):
    """FMO_BATTLE_MAPS="id:kind[,id:kind...]" -> [(id, kind)] for the 0x01F5 rows.
    '' (the default) = no rows = count 0. Malformed -> SystemExit at import."""
    out = []
    for e in (spec or "").replace(" ", "").split(","):
        if not e:
            continue
        a, _, b = e.partition(":")
        try:
            rid, kind = int(a, 0), (int(b, 0) if b else 0)
        except ValueError:
            raise SystemExit(f"FMO_BATTLE_MAPS entry {e!r}: want id:kind integers")
        if not 0 <= rid <= 0xFFFF or not 0 <= kind <= 0xFF:
            raise SystemExit(f"FMO_BATTLE_MAPS entry {e!r}: id is u16, kind is u8")
        out.append((rid, kind))
    if len(out) > (S1F5_BODY_LEN - 4) // 4:
        raise SystemExit("FMO_BATTLE_MAPS: too many rows for the 9,604-B reply")
    return out


BATTLE_MAPS = _parse_battle_maps(os.environ.get("FMO_BATTLE_MAPS", ""))


def reply_01f5(rows=None):
    """The 9,604-B 0x01F5 body: u32 count + {u16 id, u8 kind, u8 0} rows, zero-padded."""
    rows = BATTLE_MAPS if rows is None else rows
    body = bytearray(S1F5_BODY_LEN)
    struct.pack_into("<I", body, 0, len(rows))
    for i, (rid, kind) in enumerate(rows):
        struct.pack_into("<HBB", body, 4 + 4 * i, rid, kind, 0)
    return bytes(body)
