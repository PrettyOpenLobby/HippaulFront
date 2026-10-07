"""The player status block (0x014A): every field the lobby reads at start, and the knobs behind
them."""
import os
import struct
from .deps import fmostore
from .knobs import _env_int
from . import resume


#: WARNING: AND 0x0131 IS A VALUE THE CLIENT NEVER LOOKS AT (static, 2026-08-26). It was
#: picked for the one property that mattered at the time -- "not message 2" -- and
#: that is all it does. It is not built by the client (it is absent from
#: `fmomsg.py`'s outbound index), there is no `cmp` against 0x131 anywhere in the
#: image, and no lobby-API object stamps it as an expected reply. Start Game step 1
#: reads our reply, matches neither of the two ids it tests, and falls through to
#: the common tail that advances the state anyway. So it WORKS, and it carries
#: nothing.
#:
#: KEY: THE REPLY THE CLIENT ACTUALLY READS IS 0x014A -- THE PLAYER STATUS BLOCK.
#: Step 1 of the `[login+0x2c]` machine (table 0x6117BEB4, entry 1 = 0x6117B173):
#:
#:     0x6117B1A3  cmp word [rx+6], 2       ; == 2 -> the failure arm ([FM00000])
#:     0x6117B303  cmp word [rx+6], 0x14A   ; == 0x14A -> COPY THE STATUS BLOCK
#:     0x6117B3CB  ...                      ; common tail: inc [login+0x2c], return
#:
#: and the copies at 0x6117B30F..0x6117B391, with `rx+0x14` = payload+0x00:
#:
#:     payload+0x008  1660B (0x19F dwords) -> lobby+0x88C   <- RANK lives here
#:     payload+0x684  u32                  -> lobby+0x7604
#:     payload+0x688  u32                  -> lobby+0x7608
#:     payload+0x68C  u32                  -> lobby+0xFC8
#:     payload+0x6AC    96B (0x18 dwords)  -> lobby+0xF08
#:     payload+0x70C   256B (0x40 dwords)  -> lobby+0xFD4
#:
#: KEY: THE POLL MATCHES ON SEQUENCE, NOT ON MESSAGE ID, so 0x014A can be delivered
#: as the answer to 0x0130. `0x61199E30` requires `login+0x20 == 3`,
#: `login+0x28 == 4`, and `[login+0x7580]+0x10 == the value 0x0130 was sent with`
#: -- and `build()` already puts `seq` at +0x10. The id is tested afterwards,
#: inline, by the two `cmp`s above.
#:
#: WARNING: THIS IS THE EMPTY-FIXTURE SHAPE AGAIN, and it is the quietest instance yet:
#: the block is OPTIONAL. Miss it and there is no error, no hang and no log line --
#: the client simply advances with 1,660 bytes of zeros where its status is meant
#: to be. That is why the world renders and the player walks with rank 0.
MSG_START_STATUS = 0x014A
REPLY_014A_LEN = 0x80C                 # 2060 = 0x70C + 256, the last copy's end

#: Payload offsets of 0x014A, from the copies listed above.
S14A_BLOCK = 0x008                     # 1660B -> lobby+0x88C
S14A_FLAGS11 = S14A_BLOCK + (0xB88 - 0x88C)   # 0x304: script-flag kind 11 bitmap, 256B (FMO_STATUS_FLAGS)
S14A_FLAGS11_LEN = 0x100
S14A_BLOCK_LEN = 0x19F * 4
S14A_W7604 = 0x684                     # u32 -> lobby+0x7604
S14A_W7608 = 0x688                     # u32 -> lobby+0x7608
S14A_WFC8 = 0x68C                      # u32 -> lobby+0xFC8
S14A_BLOCK2 = 0x6AC                    # 96B -> lobby+0xF08
S14A_BLOCK2_LEN = 0x18 * 4
S14A_BLOCK3 = 0x70C                    # 256B -> lobby+0xFD4
S14A_BLOCK3_LEN = 0x40 * 4
#: KEY: +0x738 -> lobby+0x1000: the pilot's HANGAR PASSWORD, a NUL string the
#: client copies into the outbound 0x0170 at +0x10 (0x61190DC0). Served from
#: the character store so the client knows the password it already has.
S14A_HANGAR_PW = S14A_BLOCK3 + 0x2C
S14A_HANGAR_PW_LEN = 0x10

#: KEY: RANK, and the only field in the 2,060 bytes that is decoded. `lobby+0x8BB`
#: is `S14A_BLOCK + 0x2F`. It is an INDEX into a 124-byte-per-record master table
#: (`0x6109E8C0`: `[0x613CA3E8]+0x10 + rank*0x7C`), it is read by the dresser at
#: 0x61002FC3, and `0x6108B8A1` greys the Move menu's **Briefing Room** unless
#: `rank >= 21` AND `globals+0x1A4 == 509` (the MapKind we send in 0x0153 -- see
#: FMO_MAPKIND, which defaults to 100).
#: KEY: THE BYTE IS 0-BASED (static 2026-09-12): the D15.DAT parser
#: 0x611E4270 stores `object+0x20` -- the FIRST file row, Conscript -- at
#: `object+0x10`, and 0x6109E8C0 indexes that pointer by the raw byte. So byte
#: 0 = Conscript, 1 = Private, 20 = Captain, **21 = Major** -- and the Briefing
#: Room gate is "Major or above", SE's ORIGINAL area-mission rank (update
#: 050719qk2ld8:46; lowered to 2nd Lt on 2005-09-06 -- the client never moved).
#: fmodata/fmo-ranks.tsv's `rank` column IS this byte since 2026-09-12; before
#: that it was row+1 and every name the server logged was one rank too HIGH.
S14A_RANK = S14A_BLOCK + 0x2F          # u8, = lobby+0x8BB
#: u8 -> lobby+0x8BD, the HANGAR RANK (payload+0x39); see hangar.HANGAR_JOB_LEVEL.
S14A_HANGAR_RANK = S14A_BLOCK + 0x31

#: KEY: THE ACTIVE WANZER SETUP -- `lobby+0x8B7` = `S14A_BLOCK + 0x2B`. Decoded
#: 2026-09-08 from a live screenshot: the Select Wanzer list marked
#: **`-Empty-` as Active** while Setup1 held a complete Giza.
#: `lobby+0x3DF2` is the selected setup index (1-based: 0x6117A799 does
#: `movzx / dec / jns / xor` then `imul 0x220` to reach `lobby+0x3DF3 + i*0x220`),
#: and it has EXACTLY ONE writer in the image, `0x6117A782` inside
#: `0x6117A770(lobby, byte)`. Of that function's four callers, `0x6117B76A` is
#: the **0x0166 receive path**, and it passes `byte [lobby+0x8B7]` -- ours.
#: WARNING: Serving zero is not neutral: `0x6117A770(0)` sets `[lobby+0x3DF2] = 0` AND
#: writes 0 into setup 1's IN-USE byte (`[idx*0x220 + lobby+0x3DF4]`), i.e. it
#: un-marks the very setup we just filled. 1 selects Setup1 and marks it in use.
S14A_ACTIVE_SETUP = S14A_BLOCK + 0x2B  # u8, = lobby+0x8B7

#: Two neighbours of rank in the same block, read all over the UI and NOT decoded:
#: lobby+0x8B4 (also mirrored to the global 0x613C0BF0 at 0x6117B331), lobby+0x8B5,
#: and lobby+0xE1A, which 0x61175510 compares against rank together with
#: lobby+0x7E09. lobby+0xE1A is now SERVED (FMO_STATUS_ACKRANK, default = the
#: rank) because leaving it zero is what makes E316 refuse; 0x8B4/0x8B5 are
#: still deliberately zero.
S14A_B8B4 = S14A_BLOCK + 0x28
S14A_B8B5 = S14A_BLOCK + 0x29
S14A_BE1A = S14A_BLOCK + 0x58E

#: KEY: THE PROFILE FIELDS, NAMED BY SE'S OWN LABEL TABLE (static RE 2026-08-26,
#: status-block worker). `.data` 0x613939D0 pairs a systext id with a getter,
#: stride 0x14, and the getters read the 0x014A block at lobby+0x88C:
#:
#:     0xC010002B "First"   0x6109E880 -> lobby+0x890   (block+0x04, 17B NUL str)
#:     0xC010002C "Last"    0x6109E8A0 -> lobby+0x8A1   (block+0x15, 17B NUL str)
#:     0xC010002D "Rank"    0x6109E8C0 -> lobby+0x8BB   (block+0x2F, u8)  = S14A_RANK
#:     0xC010002E "Gender"  0x6109E8E0 -> lobby+0x8B2   (block+0x26, u8)  == 1 -> "Male"
#:                                                        (0xC0100032) else "Female"
#:     0xC010002F "Nation"  0x6109E920 -> lobby+0x8B4   (block+0x28, u8)  == 1 -> "O.C.U."
#:                                                        else "U.S.N."
#:     0xC0100030 "Funds"   0x6109E950 -> lobby+0x88C   (block+0x00, u32) "H$ %0d"
#:     0xC0100031 "MP"      0x6109E980 -> lobby+0xE0C   (block+0x580, u32) "MP %0d"
#:
#: and payload+0x68C -> lobby+0xFC8 is CONTRIBUTION (貢献値): the mission-pay
#: arm 0x6117E993 prints "[Debug]貢献値 %d -> %d" around it and 0x6117E9B4
#: prints "[Debug]お金 %d -> %d" around lobby+0x88C, the same two deltas the
#: pay screen (systext group 11: Base pay / Kill bonus / Contribution / Rank)
#: lists. lobby+0x8C8..+0xE08 (block+0x3C, 1344B) is the OWNED-ITEMS table
#: 0x611A37A0 walks per item kind (bitsets, two byte arrays) -- constant only,
#: no knob: a bit set here claims a part the player never acquired.
#:
#: WARNING: TWO ZEROS THAT DO NOT READ AS "NOTHING": gender 0 renders **Female** and
#: nation 0 renders **U.S.N.** (both are the `else` arm), which is what the
#: Profile window has said all along. WARNING: NATION IS ALSO A SCRIPT SELECTOR --
#: `script_id_for()` above: with FIELD_18 == 1 and MapKind outside 600..607, a
#: nation of 1/2 makes the client load script 98/99 INSTEAD of our MapKind.
#: Turn FMO_STATUS_NATION on alone, with the rig free, and expect that.
S14A_MONEY = S14A_BLOCK + 0x00         # u32 -> lobby+0x88C, "Funds", "H$ %0d"
S14A_FIRST = S14A_BLOCK + 0x04         # 17B -> lobby+0x890, "First"
S14A_LAST = S14A_BLOCK + 0x15          # 17B -> lobby+0x8A1, "Last"
S14A_NAME_LEN = 0x11                   # 16 chars + NUL; 0x610E384B refuses
                                       #   first+last > 16 ("name is too long")
S14A_SEX = S14A_BLOCK + 0x26           # u8 -> lobby+0x8B2, 1 = Male, else Female
S14A_NATION = S14A_B8B4                # u8 -> lobby+0x8B4, 1 = O.C.U., else U.S.N.
S14A_OWNED = S14A_BLOCK + 0x3C         # 1344B -> lobby+0x8C8, owned-items table
S14A_OWNED_LEN = 0x540
S14A_MP = S14A_BLOCK + 0x580           # u32 -> lobby+0xE0C, "MP", "MP %0d"
S14A_CONTRIB = S14A_WFC8               # u32 -> lobby+0xFC8, contribution points

#: Per-field knobs, EVERY ONE DEFAULT ZERO / OFF. One field per experiment, and
#: the bar is the Profile window (systext group 82 "Profile") on screen. Money,
#: MP and contribution are plain integers; gender/nation are the client's own
#: enum (1 = Male / O.C.U., 2 = Female / U.S.N.); names come from the character
#: store when FMO_STATUS_NAMES=1 -- the ONLY roster-driven field, because the
#: 17-byte First/Last fields are byte-for-byte the shape the roster already
#: stores from 0x013E/0x0177, so nothing is invented. The log names the source.
STATUS_MONEY = _env_int("FMO_STATUS_MONEY", "0")
STATUS_MP = _env_int("FMO_STATUS_MP", "0")
STATUS_CONTRIB = _env_int("FMO_STATUS_CONTRIB", "0")
#: KEY: THE PENDING-ORDERS GATE (`lobby+0xE1A` = S14A_BE1A = block+0x58E). LIVE
#: 2026-09-06: talking to `tag_senior` -- the war map's ONLY door --
#: printed AH/F98/D64 records 67/68/69, "Orders have
#: come down from Army Command / Please ask the Personnel Officer / You cannot
#: accept a mission at this time", and stopped there.
#:
#: That is the script's FIRST gate, `SYSCALL 0xE316` (`0x610FB4C0`), which
#: returns 0 -- refuse -- when `0x61175510(lobby)` returns non-zero. That
#: predicate reads three bytes, A = rank `lobby+0x8BB`, B = `lobby+0x7E09`,
#: C = `lobby+0xE1A`:
#:
#:     if (A < B)  goto L1
#:     if (A != C) return 1        <-- we landed here: rank set, C left zero
#:     L1: if (C < B) return 0
#:         if (A == C) return 0
#:         return 1
#:
#: So C is the ACKNOWLEDGED rank and A the current one: when they differ you
#: have un-collected orders, which is exactly the text SE prints. Serving
#: C == rank makes the predicate return 0 -> E316 returns 1 -> the script walks
#: on to its next gate. (B is served zero and nothing in our server writes it.)
#:
#: Default = mirror the rank = "no orders pending", the state a normal returning
#: pilot is in -- UNLESS the pilot has a pending promotion/demotion order
#: (2026-10-07, servicerecord.personnel_visit): then C is the ORDERED rank,
#: the desk refuses as SE's did, and the Personnel Officer's 0x0176 +0x0D
#: applies it (A := C). So C is the ordered rank, not an "acknowledged" one. Set FMO_STATUS_ACKRANK to a number to force a specific value, or
#: to a value != FMO_RANK to deliberately reproduce the refusal.
#: WARNING: UNPROVEN LIVE as of the commit that added it -- the oracle is `tag_senior`
#: getting past "Please ask the Personnel Officer."
STATUS_ACKRANK = os.environ.get("FMO_STATUS_ACKRANK", "").strip()
STATUS_ACKRANK = int(STATUS_ACKRANK, 0) if STATUS_ACKRANK else None

#: Which wanzer setup the client makes ACTIVE when it receives 0x0166 (see
#: S14A_ACTIVE_SETUP). Default 1 = Setup1, the one reply_0166 fills.
#: `FMO_ACTIVE_SETUP=0` restores the pre-2026-09-08 zero byte for byte.
STATUS_ACTIVE_SETUP = _env_int("FMO_ACTIVE_SETUP", "1")
#: FMO_STATUS_SEX=roster serves the character's own creation +0x26 (the byte SE's
#: menu titles call "Select Gender", 1 = Male / 2 = Female -- the same enum the
#: Profile's reader 0x6109E8E0 tests with `== 1`). Measured 2026-08-27: with the
#: zero, the Profile of a male character read "Female" (the else arm).
_status_sex = os.environ.get("FMO_STATUS_SEX", "0").strip().lower()
STATUS_SEX_ROSTER = _status_sex == "roster"
STATUS_SEX = 0 if STATUS_SEX_ROSTER else int(_status_sex or "0", 0)
STATUS_NATION = _env_int("FMO_STATUS_NATION", "0")
STATUS_NAMES = (os.environ.get("FMO_STATUS_NAMES", "").strip() or "0") != "0"

#: KEY: THE BATTLE-MAP LINK-DEATH RESUME PATH -- decoded 2026-09-03, found because
#: FMO_STATUS_MARK put a marker in +0x684 and the client sent an 0x0137 nobody
#: had ever seen, carrying that exact marker.
#:
#: `0x6117B8D0` tests `lobby+0x7604` against ZERO. Zero is the normal world
#: entry. NON-ZERO takes the resume branch:
#:
#:     value % 1000000        -> lobby+0x4F02, sent as 0x0137's first dword
#:     lobby+0x7608           -> lobby+0x4F08 verbatim
#:     lobby+0xFD4 / 10000    -> a 16-bit resource selector, and
#:                               0x61173FE0 loads resource 82159 + that
#:     lobby+0x7604 := 0      -- ONE-SHOT, the client clears it itself
#:     [lobby+0x44] := 1      -- printed elsewhere as "BMResumedFromLD=%d"
#:     inc byte [lobby+0x2C]  -- the state machine advances
#:
#: Resource **82159 = data\AI\F21\D59.DAT**, and that directory holds exactly
#: **41 files, D59..D99**, all 2,956 B but D69 (828) and all `FMDT`-magic
#: records -- so the selector is 0..40 and the space is small and enumerable.
#:
#: WARNING: SETTING W7604 NON-ZERO DIVERTS THE CLIENT AWAY FROM WORLD ENTRY. That is
#: the point of the knob and it is also how it hangs: a live client sat
#: at "connecting to the world server" for as long as it was set, because the
#: resume flow does not finish. Default 0 = untouched, and the field is
#: one-shot so nothing persists past a relaunch with the knob cleared.
#:
#: WARNING: CORRECTED 2026-09-04 (Brief 7 + re-verified against the binary here). The
#: old note said "0x0137 is FIRE-AND-FORGET, answering it advances nothing" --
#: WRONG, and it cost the resume path a handler. The low-level BUILDER
#: `0x61173B60` has no reply compare inside itself, but `kycli_lobmain` is a
#: tick-driven state machine: state 8 (`0x6117B9F7`) SENDS 0x0137 via
#: `0x61173B50` (message id 0x137, 8-byte body [resume_id, 1]) and saves the
#: transaction handle to `[lobby+0x54]`; state 9 (`0x6117BA18`) polls that
#: handle and, on a reply whose `word[+6] == 0x138`, jumps to `0x6117B16B`
#: which advances into the ordinary 0x0139/0x013A sortie handshake. An
#: UNanswered 0x0137 parks state 9 forever -- the reconnect HANG. So answering
#: 0x0137 with an empty message 0x0138 on its own seq is exactly what un-hangs
#: it. See MSG_RESUME_REQ / on_resume.
STATUS_W7604 = int(os.environ.get("FMO_STATUS_W7604", "").strip() or "0", 0)
STATUS_W7608 = int(os.environ.get("FMO_STATUS_W7608", "").strip() or "0", 0)
#: The raw dword at payload+0x70C -> lobby+0xFD4. The resource selector is
#: this // 10000, so index n means n * 10000.
STATUS_WFD4 = int(os.environ.get("FMO_STATUS_WFD4", "").strip() or "0", 0)
#: KEY: THE SCRIPT FLAG BITS (FMO_STATUS_FLAGS="173,183"). Static 2026-09-05: the
#: lobby script's "Entry Event" (D07.DAT file 0x0EC7A, called twice right before
#: LobbyEntry_geki's map-attach fork) tests script native 0xE067 on ids 0xAD=173
#: and 0xB7=183, and the caller's BRnz on each result is what skips or plays the
#: first-login TUTORIAL cutscene. 0xE067 = 0x610FD860 -> 0x611750F0(lobby, id)
#: -> 0x611A3950(lobby+0x8C8, kind 11, id): kind 11's block is lobby+0x8C8+0x2C0
#: = lobby+0xB88, 256 bytes, bit (id & 7) of byte (id >> 3) -- bounds-checked
#: against 0x100. lobby+0xB88 is inside the 0x014A status copy (payload+0x08 ->
#: lobby+0x88C), i.e. 0x014A payload +0x304. So 173 -> payload byte 0x319 bit 5,
#: 183 -> byte 0x31A bit 7. These are SERVER bits: the client has no other
#: writer. Every cold login here plays the tutorial because we send zeros.
#: Empty (default) = today's zeros. Live-unproven; the expected result is the
#: script's returning-player arm (file 0x12E70: E200 phase 0, then E285 places
#: of the wire NPCs) instead of the cutscene.
#: KEY: 2026-09-05 (settled-lobby round 2): THE SAME 256-BYTE BLOCK IS ALSO READ AS
#: BYTE VALUES. 0xE066 (0x610FD7D0) and the LEV row gate (0x610ED040) test ONE
#: BIT (byte id>>3, bit id&7); 0xE067 (0x610FD860) returns the WHOLE BYTE at
#: index id (`0x611750F0(lobby, id) & 0xFF`) and the counter scripts compare it
#: with a value. SCP 0x8000 (AH/F98/D63.DAT) `tag_mapslct` @0xFBB6 does
#: E067(0x80) == 0x63: **byte 128 must equal 99 = "pilot registered"**, else it
#: prints text 0x37 ("パイロット未登録のため使用不可能。登録手続きは先任軍曹が行って
#: くれます。" -- unregistered, see the senior sergeant = `tag_senior`
#: 0x82080110) and stops -- seen LIVE 2026-09-05 21:0xZ. The event-table row
#: gates use the same byte reads (`byte[157]==99`, `byte[179]==3`, ...): 99 is
#: SE's "done" marker. So an entry `idx=val` sets byte idx to val (0..255);
#: plain ids still set bits. A byte value wins over bits aimed at the same byte
#: (logged). E.g. FMO_STATUS_FLAGS=8,9,10,11,12,13,128=99
def _parse_status_flags(spec):
    """'8,9,128=99' -> ([8, 9], {128: 99}). Pure; the selftest drives it.

    ONE GRAMMAR, in fmostore, so the knob and `fmostore.py --seed` cannot drift
    apart -- what the knob seeds and what an operator types have to compile to
    the same 256-byte block or the seed would not reproduce the knob. The
    fallback below is the same code, kept for a deployment where the guarded
    import failed (in which case the flag block cannot be stored anyway).
    """
    if fmostore:
        try:
            return fmostore.parse_flag_spec(spec)
        except ValueError as e:
            raise SystemExit(f"FMO_STATUS_FLAGS: {e}")
    bits, bytes_ = [], {}
    for e in spec.replace(" ", "").split(","):
        if not e:
            continue
        if "=" in e:
            k, _, v = e.partition("=")
            idx, val = int(k, 0), int(v, 0)
            if not 0 <= idx < 256 or not 0 <= val <= 255:
                raise SystemExit(f"FMO_STATUS_FLAGS entry {e!r}: byte index "
                                 f"must be 0..255 and the value 0..255")
            bytes_[idx] = val
        else:
            bits.append(int(e, 0))
    return bits, bytes_


STATUS_FLAGS, STATUS_FLAG_BYTES = _parse_status_flags(
    os.environ.get("FMO_STATUS_FLAGS", ""))
if resume.RESUME_RES_INDEX is not None:
    STATUS_WFD4 = (resume.RESUME_RES_INDEX * 10000) & 0xFFFFFFFF

#: WARNING: OFF BY DEFAULT, AND IT MUST STAY THAT WAY UNTIL SOMEONE WATCHES IT.
#: Serving 0x014A replaces a reply the client demonstrably ignores with 2,060
#: bytes it copies straight into the lobby, of which we understand ONE byte. The
#: other 2,059 are zeros we would be asserting rather than declining to answer --
#: exactly the trap `served-zeros-launder-into-choices` names, and MapKind has
#: already shown that a wrong value in this protocol can take the client down.
#: The old 0x0131 remains the default so that turning this on is a single,
#: reversible A/B.
#:
#: WARNING: NOT LIVE-TESTED. Written 2026-08-26 from static RE alone.
SERVE_START_STATUS = (os.environ.get("FMO_START_STATUS", "").strip() or "0") != "0"

#: The rank to serve when FMO_START_STATUS is on. 21 (0x15) is the Briefing Room
#: threshold from 0x6108B8B5; 0 is the honest value and the control.
START_RANK = _env_int("FMO_RANK", "0")


def ack_for(char, rank):
    """(C, source): the ORDERED rank 0x014A +0x58E carries for a pilot whose
    rank byte is `rank` -- FMO_STATUS_ACKRANK when forced, else the pending
    order's rank (servicerecord.rank_order), else `rank` itself (no order:
    the E316 gate open)."""
    if STATUS_ACKRANK is not None:
        return STATUS_ACKRANK, "FMO_STATUS_ACKRANK"
    _o = servicerecord.rank_order(char)
    if _o is not None and _o["rank"] != rank:
        return _o["rank"], (f"PENDING ORDER: {_o.get('kind')} to {_o['rank']} "
                            f"({_o.get('why')}); the desk sends the pilot to the "
                            f"Personnel Officer")
    return rank, "mirrors rank"


def rank_and_ack(char):
    """(A, C): the rank byte and the ordered-rank byte a 0x014A for this
    pilot carries -- what the client's orders gate starts a session with."""
    r = rank_and_source(char)[0]
    return r, ack_for(char, r)[0]


def rank_and_source(char, rank=None):
    """The 0x014A rank byte and its source, as status_fields resolves it."""
    char = char or {}
    r, r_src = economy._econ_value("rank", rank, START_RANK, "FMO_RANK", char)
    _cc = char.get("contribution")
    if (ranks.RANK_FROM_CONTRIB and rank is None and isinstance(_cc, int)
            and not isinstance(_cc, bool) and ranks.RANK_LADDER):
        r = ranks.rank_for_contribution(_cc)
        r_src = (f"contribution {_cc} -> rank {r} {ranks.rank_name(r)} "
                 f"(FMO_RANK_FROM_CONTRIB; fmodata/fmo-ranks.tsv)")
    return int(r or 0) & 0xFF, r_src


def status_fields(rank=None, char=None, money=None, mp=None, contrib=None,
                  sex=None, nation=None, names=None, active_setup=None,
                  w7604=None, w7608=None, wfd4=None, nation_src=None,
                  **fields):
    """The 0x014A fields we know how to fill, as (label, offset, bytes, source).

    None means "the knob/store decides"; an explicit value wins (that is what
    the selftest uses). A field whose value is zero / empty is NOT listed, so
    the caller's log says exactly which bytes left the zero the client already
    had. rank/money/mp/contribution resolve through _econ_value (PLAN 1.5): a
    stored per-character value beats the knob, so a mission's result persists.
    """
    char = char or {}
    out = []
    r, r_src = rank_and_source(char, rank)
    if r:
        out.append(("rank", S14A_RANK, bytes([r & 0xFF]),
                    r_src + (f" = {ranks.rank_name(r)}" if ranks.RANK_LADDER and "->" not in r_src else "")))
    # KEY: The acknowledged rank must EQUAL the rank or every script that calls
    # E316 refuses with "Please ask the Personnel Officer" -- see STATUS_ACKRANK.
    # Mirrors rank by default; zero needs no byte (the client already has zero,
    # and rank 0 == ack 0 already satisfies the predicate).
    # 2026-10-07: +0x58E is the ORDERED rank. A pending promotion/demotion
    # order (servicerecord.personnel_visit) is served here, so the desk sends
    # the pilot to the Personnel Officer, whose 0x0176 applies it.
    _ack, _ack_src = ack_for(char, r)
    if _ack:
        out.append(("ack-rank (+0x58E -> lobby+0xE1A, the E316 orders gate)",
                    S14A_BE1A, bytes([_ack & 0xFF]), _ack_src))
    # PENALTY (penalty.py): +0x3A -> lobby+0x8BE (E0A1, nonzero = clearance
    # revoked, D63/D83) and +0x594 -> lobby+0xE18 (E0A3, retraining wins to
    # finish); the same bytes every 0x015A carries at +0x418 / +0x41A.
    from . import penalty
    out.extend(penalty.status_014a_fields(char))
    _act_setup = (STATUS_ACTIVE_SETUP if active_setup is None
                  else active_setup)
    if _act_setup:
        out.append(("active wanzer setup (+0x2B -> lobby+0x8B7; 0x6117B76A "
                    "passes it to the ONLY writer of lobby+0x3DF2)",
                    S14A_ACTIVE_SETUP,
                    bytes([_act_setup & 0xFF]), "FMO_ACTIVE_SETUP"))
    _money, _money_src = economy.wallet_money(char, money)
    if _money < 0:
        # WARNING: A pilot already carrying a negative balance from before the floor
        # went in is REPAIRED HERE, on the way out: the client's shop gate
        # compares SIGNED, so sending 0xFFFFFFFD would leave them unable to buy
        # anything, including items priced 0. Floored on the wire and said out
        # loud, rather than silently -- the stored number is still wrong until
        # something writes it.
        _money_src += (f" -- WARNING: STORED {_money} IS NEGATIVE, sent as 0; that "
                       f"pilot's shop is otherwise dead (signed compare at "
                       f"0x611785D0)")
        _money = 0
    _mp, _mp_src = economy._econ_value("mp", mp, STATUS_MP, "FMO_STATUS_MP", char)
    _contrib, _contrib_src = economy._econ_value("contribution", contrib,
                                                 STATUS_CONTRIB, "FMO_STATUS_CONTRIB",
                                                 char)
    for label, off, val, knob in (
            ("money", S14A_MONEY, _money, _money_src),
            ("mp", S14A_MP, _mp, _mp_src),
            ("contribution", S14A_CONTRIB, _contrib, _contrib_src),
            # The resume triad. Zero for all three is the normal world entry.
            # PER CHARACTER as of 2026-09-08: "was this pilot in a battle when
            # the link died" is a fact about ONE pilot, and while it was a
            # global knob it was a trap -- FMO_STATUS_W7604 armed diverted EVERY
            # login off world entry, which is why PLAN 0.1 lists disarming it as
            # hygiene. Stored, it can be written at sortie and cleared on
            # return, which is the only way it is ever true of the right player.
            ("resume-flag (+0x684 -> lobby+0x7604)", S14A_W7604) +
            economy._econ_value("resume_w7604", w7604, STATUS_W7604,
                                "FMO_STATUS_W7604", char),
            ("resume-aux (+0x688 -> lobby+0x7608)", S14A_W7608) +
            economy._econ_value("resume_w7608", w7608, STATUS_W7608,
                                "FMO_STATUS_W7608", char),
            ("resume-resource (+0x70C -> lobby+0xFD4)", S14A_BLOCK3) +
            economy._econ_value("resume_wfd4", wfd4, STATUS_WFD4,
                                "FMO_STATUS_WFD4", char)):
        if val:
            out.append((label, off, struct.pack("<I", val & 0xFFFFFFFF), knob))
    sex_src = "FMO_STATUS_SEX"
    if sex is None and STATUS_SEX_ROSTER:
        g = poplook._gender_byte(char) if char else None
        sex = g if g is not None else 0
        sex_src = "character store (+0x26 Select Gender, FMO_STATUS_SEX=roster)"
    for label, off, val, knob in (
            ("sex", S14A_SEX, STATUS_SEX if sex is None else sex, sex_src),
            ("nation", S14A_NATION, STATUS_NATION if nation is None else nation,
             "FMO_STATUS_NATION" if nation is None else
             (nation_src or "explicit"))):
        if val:
            out.append((label, off, bytes([val & 0xFF]), knob))
    # KEY: HANGAR RANK (+0x39 -> lobby+0x8BD, see hangar.HANGAR_JOB_LEVEL): the
    # rank the last battle end banked, never recomputed here -- SE applied the
    # change after a battle, and 0x014C +0x0F5 must carry the same value or it
    # resets the byte. Zero (a fresh pilot, or FMO_HANGAR_JOB_LEVEL=0) needs no byte.
    _hr = hangar.hangar_rank_stored(char)
    if _hr:
        _hw, _hc = hangar.hangar_capacity(_hr)
        out.append((f"hangar rank (+0x39 -> lobby+0x8BD; table 0x61399988: {_hw} "
                    f"wanzers, {_hc} items)", S14A_HANGAR_RANK, bytes([_hr & 0xFF]),
                    "character store [hangar_rank], banked at the last battle end"))
    _hpw = (char.get("hangar_password") or "") if char else ""
    if _hpw:
        out.append(("hangar password (+0x738 -> lobby+0x1000, the string the "
                    "client echoes in 0x0170 +0x10)", S14A_HANGAR_PW,
                    _hpw.encode("ascii", "replace")[:S14A_HANGAR_PW_LEN - 1] + b"\0",
                    "character store [hangar_password]"))
    if (STATUS_NAMES if names is None else names):
        for label, off, key in (("first", S14A_FIRST, "first"),
                                ("last", S14A_LAST, "last")):
            s = (char.get(key) or "").encode("ascii", "replace")
            s = s[:S14A_NAME_LEN - 1]           # 16 chars + the NUL
            if s:
                out.append((label, off, s + charselect.NUL, "character store"))
    # THE CLASS TABLE (see S14A_CLASS_TABLE above). An explicit
    # class_table=False in `fields` leaves the zeros (the selftest's control).
    if classes.CLASS_TABLE and fields.get("class_table") is not False:
        out.append(("class table (+0x6AC -> lobby+0xF08, 12 x {kind, level, exp}): "
                    + classes.class_table_summary(char)
                    + ("" if classes.CLASS_CURVE else " -- WARNING: fmodata/fmo-class-exp.tsv "
                       "MISSING, every level reads 1"),
                    classes.S14A_CLASS_TABLE, classes.class_table_block(char),
                    "character store [class_exp] + fmodata/fmo-class-exp.tsv"))
        if ranks.RANK_GROUP:
            out.append(("rank group (+0x29 -> lobby+0x8B5; the exp table's group, "
                        "1..4 all identical in the shipped file)",
                        classes.S14A_RANK_GROUP, bytes([ranks.RANK_GROUP & 0xFF]), "FMO_RANK_GROUP"))
    # KEY: THE AREA PERMITS -- why Change Area has never once let anyone move.
    # The list filler 0x61010C2B writes each zone's selectable state from
    # 0x611794A0(zoneId, &out), and that predicate needs BOTH a row in our
    # 0x016C table AND `out > 0`, where out is `0x611A3B30` =
    #     byte[lobby+0x8C8 + 0x280 + (zoneId/100 - 1)]
    # -- ONE BYTE PER ZONE KIND in the owned block, which is 0x014A's
    # payload+S14A_OWNED. We have served that block as zeros since the day it
    # was decoded, so every area read back `out == 0`, the filler stored -1 and
    # the screen answered 17:68 "That area cannot be selected right now."
    # That is exactly SE's permit: their item text says a 区間移動許可証 is
    # CONSUMED to open a yellow area, and the passes come one per zone kind
    # (fmoitems kind 0x13: 25 HQ-O.C.U. -> kind 1, 26 HQ-U.S.N. -> 3,
    # 27 OC-O.C.U. -> 2, 28 OC-U.S.N. -> 4, 29 FLZ -> 5; 30 DMZ / 31 X /
    # 32 XX are NOT mapped -- no zone kind is proved for them).
    # So the byte is derived from the passes the pilot actually holds: a pilot
    # with none gets the all-zero block this served before, byte for byte.
    for _pk, _zk in sorted(permits.PASS_ZONE_KIND.items()):
        if not any(int(_it.get("kind", 0)) == permits.PASS_KIND
                   and int(_it.get("id", 0)) == _pk
                   for _it in inventory.stored_items(char)):
            continue
        out.append(("area permit for zone kind %d (holds item kind 0x%02X id "
                    "%d) -> lobby+0x8C8+0x280+%d, the byte 0x611A3B30 returns "
                    "and Change Area needs > 0"
                    % (_zk, permits.PASS_KIND, _pk, _zk - 1),
                    S14A_OWNED + permits.AREA_PERMIT_OFF + (_zk - 1), b"\x01",
                    "the pilot's own transit passes"))
    # KEY: THE AREAS A USED PERMIT OPENED (see AREA_OPEN_OFF). Above the
    # stored-flags early return for the same reason the permits are.
    _open = [int(z) for z in (char.get("areas_open") or [])]
    if _open and zonecontrol.ZONE_CONTROL:
        _bm = permits.area_open_bitmap(_open, zonecontrol.parse_zone_control(zonecontrol.ZONE_CONTROL))
        for _bi, _bv in enumerate(_bm):
            if _bv:
                out.append(("opened areas, rows %d..%d of the 0x016C table "
                            "(owned+0x%02X, kind-1 bitmap; zones %s) -> no "
                            "permit prompt for them"
                            % (_bi * 8, _bi * 8 + 7, permits.AREA_OPEN_OFF + _bi,
                               ",".join(str(z) for z in _open)),
                            S14A_OWNED + permits.AREA_OPEN_OFF + _bi, bytes([_bv]),
                            "character store [areas_open]"))

    # KEY: THE PAINT THE PILOT OWNS (inventory.owned_paint_bits): the camo,
    # colour and insignia pickers list only owned ids, read from this block.
    # Above the stored-flags early return for the same reason the permits are.
    _pnat = nation or STATUS_NATION
    if not _pnat and char:
        from . import popnation          # here: a module-level import is a cycle
        _pnat = popnation.character_nation(char)[0]
    _pbits = inventory.owned_paint_bits(char, _pnat)
    if _pbits:
        _pids = inventory.owned_paint_ids(char, _pnat)
        for _po in sorted(_pbits):
            out.append(("owned paint byte owned+0x%03X = %#04x (camo %s, colours "
                        "%s, insignia %s; the colouring pickers list owned ids only)"
                        % (_po, _pbits[_po], _pids["camo"], _pids["colour"],
                           _pids["insignia"]),
                        S14A_OWNED + _po, bytes([_pbits[_po]]),
                        "nation %s starting paint + the stored setups' paint + paint "
                        "bought at the shop (0x01A4)" % _pnat))

    # PROGRESS FLAGS -- the kind-11 block at lobby+0xB88 (payload +0x304).
    #
    # KEY: STORED PER CHARACTER as of 2026-09-08, and this one mattered most.
    # These bits and bytes are the game's PROGRESS: byte 128 == 99 is SE's
    # "pilot registered" marker that gates the counters and the war map, and
    # the event-table rows gate on their own bytes. As a global knob it said
    # every pilot on the server had done the same tutorial -- which is not a
    # fact about a player at all. A stored block wins whole (it IS the block
    # the client keeps), and only a character with none falls back to the knob.
    _blk = fmostore.flags_bytes(char.get("flags")) if fmostore else b""
    _override = fields.get("flags") is not None or fields.get("flag_bytes") is not None
    if _blk and not _override:
        _knob_set = bool(STATUS_FLAGS or STATUS_FLAG_BYTES)
        _src = ("character store [flags]"
                + (" -- WARNING: FMO_STATUS_FLAGS IS SET AND MASKED for this pilot"
                   if _knob_set else ""))
        for _bi, _bv in enumerate(_blk):
            if _bv:
                out.append(("script flag byte %d = %d (kind 11, lobby+0xB88)"
                            % (_bi, _bv), S14A_FLAGS11 + _bi, bytes([_bv]),
                            _src))
        return out
    # FMO_STATUS_FLAGS: script flag bits (kind 11, lobby+0xB88). One byte per
    # touched byte so nothing else in the block is overwritten.
    _flags = STATUS_FLAGS if fields.get("flags") is None else fields["flags"]
    _bytes = {}
    for _fid in _flags or []:
        if not 0 <= _fid < S14A_FLAGS11_LEN * 8:
            raise ValueError("FMO_STATUS_FLAGS id %d is outside the 256-byte "
                             "kind-11 bitmap (0..2047)" % _fid)
        _bytes[_fid >> 3] = _bytes.get(_fid >> 3, 0) | (1 << (_fid & 7))
    for _bi in sorted(_bytes):
        out.append(("script flag byte %d = %#04x (ids %s; kind 11, lobby+0xB88)"
                    % (_bi, _bytes[_bi],
                       ",".join(str(f) for f in _flags if (f >> 3) == _bi)),
                    S14A_FLAGS11 + _bi, bytes([_bytes[_bi]]), "FMO_STATUS_FLAGS"))
    # `idx=val` entries: the whole byte (0xE067 reads bytes, not bits; byte 128
    # == 99 = "pilot registered", the counter scripts' gate). A value wins over
    # any bit entry aimed at the same byte.
    _fbytes = (STATUS_FLAG_BYTES if fields.get("flag_bytes") is None
               else fields["flag_bytes"])
    for _bi in sorted(_fbytes or {}):
        _clash = " (OVERRIDES the bit entries for this byte)" if _bi in _bytes else ""
        out.append(("script flag byte %d = %d (E067 byte value%s; kind 11, lobby+0xB88)"
                    % (_bi, _fbytes[_bi], _clash),
                    S14A_FLAGS11 + _bi, bytes([_fbytes[_bi] & 0xFF]), "FMO_STATUS_FLAGS"))

    return out


#: PROBE: FMO_STATUS_MARK=<base> -- fill the 0x014A status block with dword i =
#: base + i, so a number on any screen fed by it IS its own offset.
#:
#: WHY THIS AND NOT FMO_LOBAPI_MARK. Measured 2026-09-03: Job Status, Battle
#: Group and Change Area all send NOTHING when touched -- every one is a
#: client-local gate reading state delivered at login. FMO_LOBAPI_MARK fills
#: lobby-API reply bodies and 0x014A is the Start Game reply, so that
#: instrument is blind to precisely the block those screens read. This block is
#: 2,060 bytes; we author six fields; the rest is three windows
#: (1,660B -> lobby+0x88C, 96B -> +0xF08, 256B -> +0xFD4) that are almost
#: entirely undecoded, and it is the only thing both large enough and CONSTANT
#: enough to explain "every room says FZ-10: Freedom City".
#:
#: WARNING: THE SKIPS ARE NOT OPTIONAL AND THERE IS NO OVERRIDE. A set bit in the
#: owned-items table CLAIMS A PART, and the tail is echoed back to us in
#: 0x0170 -- marking either would corrupt state we then have to explain. Both
#: ranges stay zero, which is exactly what the client already holds.
#:
#: WARNING: AND THE SEMANTIC BYTES ARE FORCED BACK TO ZERO before the real fields are
#: applied. A marker byte landing on NATION would change the scene script
#: (1/2 -> script 98/99 via script_id_for), i.e. the probe would alter the very
#: scene it is trying to read. Rank, sex and the capacity tier get the same
#: treatment: a knob that serves them still wins, because status_fields() is
#: applied last.
STATUS_MARK = int(os.environ.get("FMO_STATUS_MARK", "").strip() or "0", 0)

#: (start, end) payload ranges the marker must leave alone.
STATUS_MARK_SKIP = (
    # 0x03C..0x584: the resource ids AND the 1,344-byte owned-items bitset.
    # Deliberately spans both readings of the field table -- the "resource ids"
    # note is payload-relative and the owned table is block-relative, they
    # abut, and a byte either way here is a claimed part.
    (0x03C, S14A_OWNED + S14A_OWNED_LEN),
    # The 256-byte tail: echoed straight back at us in 0x0170.
    (S14A_BLOCK3, REPLY_014A_LEN),
)

#: Single bytes with known side effects, zeroed after the fill.
STATUS_MARK_ZERO = (S14A_SEX, S14A_NATION, S14A_B8B5, S14A_RANK)


def status_mark_body(base):
    """The 0x014A payload as markers: dword i = base + i, except the skips
    (zero) and the semantic bytes (zero). Pure; selftested."""
    b = bytearray(REPLY_014A_LEN)
    for i in range(REPLY_014A_LEN // 4):
        struct.pack_into("<I", b, i * 4, (base + i) & 0xFFFFFFFF)
    for lo, hi in STATUS_MARK_SKIP:
        b[lo:hi] = bytes(hi - lo)
    for off in STATUS_MARK_ZERO:
        b[off] = 0
    return b


def reply_014a(rank=None, char=None, **fields):
    """The 2,060-byte 0x014A player-status payload.

    Zero except the fields status_fields() lists -- or markers, with
    FMO_STATUS_MARK set. That the default is zero is not laziness: the copies
    this payload feeds land 2,012 bytes into the lobby's own state, and a zero
    here is the same zero the client already has today (the block is
    zero-initialised at 0x6117A2F6), so with every knob at its default this
    reply changes precisely one thing -- rank. Each named field was traced to a
    reader that formats or compares it (see the S14A_* notes); nothing here is
    a shape guess.
    """
    return status_body(status_fields(rank=rank, char=char, **fields))


def status_body(fields_list):
    """The 0x014A payload from an ALREADY-RESOLVED status_fields() list.

    A caller that logs the field list must send THIS, built from that same
    list -- never a fresh reply_014a(char): that re-resolves every field and
    falls back to the knobs for anything the caller passed explicitly. Until
    2026-09-12 the Start Game path logged nation 2 (the pilot's) and sent
    FMO_STATUS_NATION's 1, so a U.S.N. pilot was O.C.U. to the client.
    """
    b = status_mark_body(STATUS_MARK) if STATUS_MARK else bytearray(REPLY_014A_LEN)
    for _label, off, raw, _src in fields_list:
        b[off:off + len(raw)] = raw
    return bytes(b)


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    charselect, classes, economy, hangar, inventory, permits, poplook, ranks, servicerecord,
    zonecontrol,
)
