"""The battle result push (0x015A): what the result pays."""
import os
import struct
from .knobs import _env_int


# --------------------------------------------------------------------------- #
# 0x015A -- THE BATTLE RESULT. It is a PUSH, and it is the message that PAYS.
# --------------------------------------------------------------------------- #
#: VERIFIED:KEY: STATIC 2026-09-09.
#: The game-loop plan's stage 15 has read "0x0159 -> 0x015A ... never seen
#: inbound" since it was written, and that framing is why it never got built.
#: **0x015A is not inbound and never will be: it is an unsolicited SERVER PUSH**
#: -- arm 0x6117E94F of the lobby's queue dispatcher (0x6117DFBD, sequence
#: QUEUE_SEQ), the same road our 0x014E / 0x016C / 0x019A pushes already take.
#:
#: WARNING: AND THE REASON IT READ AS ABSENT: `LOBBY_PUSH_IDS` below listed SEVEN ids
#: and its comment called them "the seven unsolicited ids the lobby dispatcher
#: will even look at". Those are the seven live arms of the FIRST of TWO jump
#: tables. The real catalogue is THIRTY ids (a walk of the dispatcher's jump tables), and 0x015A
#: is one of the direct-compare arms that was never enumerated at all.
#:
#: THE LAYOUT. In the arm `ebp` enters as the frame and `add ebp,0x14` makes it
#: the PAYLOAD pointer, so everything past 0x6117E955 is a payload offset:
#:
#:   +0x000..0x597 (1432 B)  the mission record -- byte-for-byte the block the
#:                           client itself uploaded in 0x0159 from lobby+0x6E4E
#:     +0x004 u32  count of ITEMS GRANTED   (the buffer holds at most 32)
#:     +0x008 u32  count of ITEMS CONSUMED  (at most 32)
#:     +0x010      granted rows, 24 B each  -> 0x61177B30 appends to the owned
#:                 list at lobby+0x10D9, count lobby+0x10D5, CAP 400.
#:                 row+0x00/+0x04 = the 64-bit serial, +0x08 u16 id, +0x0A u8
#:                 kind -- the pair 0x61175840 turns into "[Debug]%sを取得しました".
#:     +0x310      consumed rows, 8 B each = a serial; 0x6117A640 finds it in
#:                 the owned list and spends it ("[Debug]%sを使用しました").
#:     +0x410 s32  MONEY delta        -> lobby+0x88C   "[Debug]お金 %d -> %d"
#:     +0x414 s32  CONTRIBUTION delta -> lobby+0xFC8   "[Debug]貢献値 %d -> %d"
#:     +0x418 u8 -> lobby+0x8BE   } three bytes read back ONLY by script-VM
#:     +0x419 u8 -> lobby+0x8BF   } natives (0x610F9AB0/0x610F9AE0/0x610F9B00,
#:     +0x41A u8 -> lobby+0xE18   } the `ret 8` ABI). +0x418 is a strict bool.
#:   +0x598..0xAD7 (1344 B)  the owned/equipment table -> lobby+0x8C8
#:                 WARNING: copied UNCONDITIONALLY (`rep movsd 0x150` at 0x6117E987,
#:                 BEFORE the deltas and BEFORE the +0xAD8 test), and it is the
#:                 SAME 1,344 B the 0x014A serves from payload+0x3C
#:                 (S14A_OWNED) -- which holds the kind-11 SCRIPT FLAG BITMAP
#:                 at lobby+0xB88 (S14A_FLAGS11). Zeros here WIPE the flags:
#:                 live 2026-09-11, every eject replayed the first-login
#:                 tutorial and left a lobby with no talkable NPC and a greyed
#:                 menu (byte 128 != 99, tutorial bits clear). Echo the 0x014A
#:                 slice for the pilot, never zeros.
#:   +0xAD8 u8   NONZERO = do NOT copy the record back into lobby+0x6E4E
#:
#: KEY: THE TWO DELTA TARGETS ARE CONFIRMED, not inferred from a displacement.
#: The 0x014A push arm (0x6117E2C6) copies payload+0x08 -> lobby+0x88C and
#: payload+0x68C -> lobby+0xFC8. +0x08 is the money the 09-03 live run put on
#: screen ("H$ 12345") and +0x68C is the contribution this index has listed as
#: unserved; the SAME arithmetic reproduces the already-proven rank offset
#: (lobby+0x8BB = payload+0x37) as a control. So the deltas land in fields we
#: already serve at login -- which is what makes them persist with no new wire.
MSG_RESULT_PUSH = 0x015A
S15A_N_GRANT = 0x004
S15A_N_SPEND = 0x008
S15A_GRANT = 0x010
S15A_SPEND = 0x310
S15A_MONEY = 0x410
S15A_CONTRIB = 0x414
S15A_B418 = 0x418
S15A_B419 = 0x419
S15A_B41A = 0x41A
S15A_OWNED = 0x598
S15A_KEEP_RECORD = 0xAD8        # the byte AFTER the owned table
S15A_BODY_LEN = 0xAD9           # the shortest body that carries every field
S15A_ITEM_LEN = 24              # `lea eax,[edi+edx*8+0x10D9]` with edx = n*3
S15A_MAX_ITEMS = 32             # (0x310 - 0x010) / 24 and (0x410 - 0x310) / 8
S15A_OWNED_LEN = 0x150 * 4      # 1344, the `rep movsd 0x150` at 0x6117E987
S15A_OWNED_CAP = 400            # 0x61177B3B `cmp eax, 0x190` -> grant refused

#: FMO_RESULT_PUSH: '0' (default) = never push a result. '1' = push one after a
#: battle WITHDRAW succeeds, carrying the deltas below.
#: WARNING: DEFAULT OFF ON PURPOSE. Nothing here has been confirmed in a live session, the withdraw
#: path currently WORKS, and a push the dispatcher does not like is dropped at
#: 0x6117F86D in total silence -- so an armed default could only ever turn a
#: working return-from-battle into a silent nothing or worse. One env line arms
#: it; see the FINDINGS doc for the one-launch oracle.
RESULT_PUSH = (os.environ.get("FMO_RESULT_PUSH", "").strip() or "0") != "0"
RESULT_MONEY = _env_int("FMO_RESULT_MONEY", "0")
RESULT_CONTRIB = _env_int("FMO_RESULT_CONTRIB", "0")


def result_push_body(record=b"", money=0, contribution=0, granted=(),
                     spent=(), owned=b"", b418=0, b419=0, b41a=0):
    """The 0x015A body. Every byte we do not author stays zero -- EXCEPT
    that `owned` must never be left empty on a real push: the arm copies
    +0x598..+0xAD7 onto lobby+0x8C8 unconditionally, and that range is the
    0x014A's owned table INCLUDING the script flag bitmap (lobby+0xB88). An
    empty `owned` is only for the selftest's shape checks.

    `record` is the client's OWN 1,432-byte 0x0159 block. When it is empty we
    set the +0xAD8 skip byte, and that is the whole safety argument for this
    function: the record copy is the ONLY part of the arm that writes a block
    we would have had to invent, and the arm checks that byte LAST -- after the
    money, the items and the owned table have already been applied. So
    "no record" costs us nothing except leaving lobby+0x6E4E alone, which is
    exactly right when we have nothing authoritative to put there.
    (a fixed-width field is only what you proved)
    """
    if record and len(record) != scriptcall.S159_BODY_LEN:
        raise ValueError(
            f"0x{MSG_RESULT_PUSH:04X}: the mission record is {len(record)}B; "
            f"the client's own copy out of lobby+0x6E4E is exactly "
            f"{scriptcall.S159_BODY_LEN}B (`rep movsd 0x166`). Pass the 0x0159 body "
            f"verbatim or pass nothing.")
    if len(granted) > S15A_MAX_ITEMS or len(spent) > S15A_MAX_ITEMS:
        raise ValueError(
            f"0x{MSG_RESULT_PUSH:04X}: {len(granted)} granted / {len(spent)} "
            f"spent, but the row areas hold {S15A_MAX_ITEMS} each "
            f"(+0x{S15A_GRANT:03X}..+0x{S15A_SPEND:03X} at "
            f"{S15A_ITEM_LEN}B and +0x{S15A_SPEND:03X}..+0x{S15A_MONEY:03X} "
            f"at 8B). More rows would run into the money delta.")
    if owned and len(owned) > S15A_OWNED_LEN:
        raise ValueError(
            f"0x{MSG_RESULT_PUSH:04X}: the owned table is {len(owned)}B, cap "
            f"{S15A_OWNED_LEN} (`rep movsd 0x150`)")
    b = bytearray(S15A_BODY_LEN)
    if record:
        b[0:scriptcall.S159_BODY_LEN] = record
    for i, row in enumerate(granted):
        if len(row) != S15A_ITEM_LEN:
            raise ValueError(f"granted row {i} is {len(row)}B, want "
                             f"{S15A_ITEM_LEN} (0x61177B30 copies six dwords)")
        b[S15A_GRANT + i * S15A_ITEM_LEN:
          S15A_GRANT + (i + 1) * S15A_ITEM_LEN] = row
    for i, serial in enumerate(spent):
        if len(serial) != 8:
            raise ValueError(f"spent row {i} is {len(serial)}B, want 8 "
                             f"(0x6117A640 matches row+0x00 and row+0x04)")
        b[S15A_SPEND + i * 8:S15A_SPEND + (i + 1) * 8] = serial
    struct.pack_into("<I", b, S15A_N_GRANT, len(granted))
    struct.pack_into("<I", b, S15A_N_SPEND, len(spent))
    struct.pack_into("<i", b, S15A_MONEY, int(money))
    struct.pack_into("<i", b, S15A_CONTRIB, int(contribution))
    b[S15A_B418] = b418 & 0xFF
    b[S15A_B419] = b419 & 0xFF
    b[S15A_B41A] = b41a & 0xFF
    if owned:
        b[S15A_OWNED:S15A_OWNED + len(owned)] = owned
    # 0 = "copy the record into lobby+0x6E4E"; anything else = leave it alone.
    b[S15A_KEEP_RECORD] = 0 if record else 1
    return bytes(b)


def result_push_packet(conn_id, pilot=None, **fields):
    """The 0x015A push on the queue sequence. Nothing polls for it.

    `pilot` = the character the push is for: its CURRENT penalty bytes
    (penalty.penalty_bytes) go in +0x418..+0x41A and win over any b418/b419/
    b41a passed. The tail 0x6117E958 stores all three on EVERY 0x015A, so a
    push that echoed zeros would hand a penalised pilot its clearance back."""
    if pilot is not None:
        from . import penalty
        fields.update(penalty.push_fields(pilot))
    return packet.build(MSG_RESULT_PUSH, result_push_body(**fields),
                        pushes.QUEUE_SEQ, conn_id)


# Called at run time only; imported last so that import cycles resolve.
from . import packet, pushes, scriptcall  # noqa: E402
