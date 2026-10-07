"""The self POP: the record that makes a client draw its own pilot, and its kind, name and model
knobs."""
import os
from .deps import fmoworld
from .knobs import _env_float, _env_int


#: WARNING: THE POP PROBE -- `FMO_UDP_POP=<unitid>[:<unittype>]`, e.g. `0x82080C00:0`.
#:
#: cmd 7 is the ONLY thing that puts an entity in the client's world; see
#: `fmoworld.record_pop`. Off by default because it is an experiment, and prod
#: carries facts.
#:
#: WARNING: `0x82080C00`, `0x82080C01` and `0x82080D00` are ids the CLIENT ITSELF
#: hardcodes lookups for (`0x61002BE7`), which is the only evidence we have
#: about the id SPACE -- they are not documented and not confirmed to be legal
#: for a pop. They are a starting guess with a reason behind it, which is worth
#: more than 1, 2, 3, and still a guess.
POP_SPEC = os.environ.get("FMO_UDP_POP", "")
POP_AFTER = _env_int("FMO_UDP_POP_AFTER", "3", 10)

#: KEY: THE BATTLE SELF-POP client_kind -- `FMO_UDP_POP_CLIENT_KIND`, default
#: "auto". The scene-4 state-3 handler (0x61006F44) advances on EITHER a lobby
#: object being non-null (path A, always NULL in a battle) OR the self peer's
#: +0x10DC == 2 (path B). +0x10DC is set to 2 by ONE site: the battle cmd-7 POP
#: handler 0x611D4281, in its self branch, ONLY when the POP body+0x00
#: (client_kind) == 3 (`cmp dword [esi], 3; jne skip; mov [selfpeer+0x10dc], 2`).
#: We shipped client_kind 0 all along -- it registers the entity (0x611D428B is
#: unconditional, which is why self is already a key in the battle entity map)
#: but never sets +0x10DC, so scene 4 sat at state 3 forever with conn state 2
#: and self in the map. Lobby scenes 6/7 advance on conn state alone and never
#: read +0x10DC, which is why lobby entry always worked with client_kind 0.
#: Decoded static + live-confirmed 2026-09-04 ([scene-4 state-3 worker],
#: ac562b5bf). "auto" = 3 on a battle channel (key suffix "battle"), else 0; a
#: number forces it. Only the SELF pop; the room/alias relay pops are remote
#: peers and stay on their own branch.
POP_CLIENT_KIND_KNOB = os.environ.get("FMO_UDP_POP_CLIENT_KIND", "auto").strip()


def pop_client_kind_for(chan):
    """The client_kind (POP body+0x00) for the self-pop on THIS channel."""
    if POP_CLIENT_KIND_KNOB.lower() in ("auto", ""):
        return 3 if (chan.key and chan.key.endswith(b"battle")) else 0
    return int(POP_CLIENT_KIND_KNOB, 0)


#: WARNING:KEY: THE BATTLE "DESTROYED / cannot speak" UN-POISON -- FMO_UDP_POP_UNDESTROY,
#: default OFF. Decoded 2026-09-04 (static; see fmoworld.POP_CLIENT_KIND_TO_CHAR-
#: STATUS). The scene-4 entry POP must carry client_kind 3 (the only value that
#: sets selfpeer+0x10DC=2, the state 3->4 gate) -- but client_kind 3 also makes
#: the battle connection-char's status field (char+0x1C) == 3, which 0x611F25D0
#: reads as DESTROYED, which is why the pilot spawns as a spectator and typing
#: returns systext 35:61 "A completely destroyed player cannot speak."
#:
#: WARNING: CORRECTED 2026-09-04 after the first attempt fired but did NOT clear the
#: status (live: the un-poison re-POP went out, the battle menu appeared, but
#: "cannot speak" stayed and acting wedged the scene). A bare re-POP CANNOT rewrite
#: char+0x1C: the cmd-7 handler's phase B looks the existing char up and dispatches
#: on **char+0x20** (0x611D42C0 -> table 0x611D4520); a freshly-created char has
#: char+0x20 == 0 (ctor 0x611E2020), whose arm 0x611D44AB is `mov eax,1; ret` --
#: it does NOT destruct+recreate, so char+0x1C is never touched. Only char+0x20 in
#: {1,2,3} takes the destruct+recreate arm.
#:
#: The field char+0x20 is set by exactly one thing on the wire: the battle **cmd 8
#: (depop)** handler 0x611D3050 -> 0x611E2070(char, N), which does `[char+0x20]=N`.
#: Depop **status 2 (DESTROYED)** sets char+0x20=1 and, crucially, does NOT touch
#: the CLI peer (the clisys +0x10E0=4 "finished" write at 0x611E21B0 fires only for
#: depop status 0/1), so selfpeer+0x10DC=2 (the scene gate) is preserved. Statuses
#: 0/1 also work for char+0x20 but mark the peer finished; status 3 skips the write.
#:
#: THE FIX IS THEREFORE TWO RECORDS, in order: cmd 8 depop (status 2) to set
#: char+0x20=1, then cmd 7 re-POP with an ALIVE client_kind to recreate the char
#: with a non-destroyed status. This is SE's own link-death RECONNECT flow
#: (depop then re-pop), so it is a supported sequence, not a hack. This knob is the
#: alive client_kind for the re-POP; EMPTY (unset) = OFF. Only an ALIVE value is
#: accepted -- 2 and 3 are the destroyed ones we are trying to leave.
#: WARNING: STATIC. Never armed live in the corrected form. It fires only on a battle-suffix
#: channel, only after a self-POP whose client_kind was itself destroyed, once. The
#: bar is the player typing in the battle and NOT getting the "destroyed" refusal;
#: whether the pilot can then ACT (move / fight) is a further question -- the
#: possess-own-unit path 0x611ED760 that grants control runs for wanzer UnitTypes,
#: not the UnitType-4 human, and is a separate brief.
_undestroy = os.environ.get("FMO_UDP_POP_UNDESTROY", "").strip()
# EMPTY (unset) is OFF; an explicit client_kind (0 or 1, both alive) ARMS it --
# note 0 arms with kind 0, it is not the off sentinel.
UNDESTROY_KIND = int(_undestroy, 0) if _undestroy else None
if UNDESTROY_KIND is not None and \
        fmoworld.client_kind_is_destroyed(UNDESTROY_KIND):
    raise SystemExit(
        f"FMO_UDP_POP_UNDESTROY={_undestroy!r}: client_kind {UNDESTROY_KIND} "
        f"maps to connection-char status "
        f"{fmoworld.POP_CLIENT_KIND_TO_CHARSTATUS.get(UNDESTROY_KIND)}, which "
        f"0x611F25D0 reads as DESTROYED -- the un-poison POP must carry an "
        f"ALIVE client_kind (0 or 1)")
UNDESTROY_AFTER = _env_float("FMO_UDP_POP_UNDESTROY_AFTER", "1.0")
#: The depop status the un-poison sends FIRST to set char+0x20 != 0 (so the re-POP
#: destruct+recreates). Default 2 (DESTROYED): the only one that leaves the peer
#: alone (0/1 mark the CLI finished via 0x611E21B0; 3 skips char+0x20 entirely).
UNDESTROY_DEPOP_STATUS = int(
    os.environ.get("FMO_UDP_POP_UNDESTROY_DEPOP_STATUS", "2"), 0)
if UNDESTROY_KIND is not None and UNDESTROY_DEPOP_STATUS not in (0, 1, 2):
    raise SystemExit(
        f"FMO_UDP_POP_UNDESTROY_DEPOP_STATUS={UNDESTROY_DEPOP_STATUS}: the battle "
        f"cmd-8 handler 0x611D3050 sets char+0x20 only for status 0/1/2 (3 skips "
        f"0x611E2070); 2 is the one that does not also finish the peer")


#: KEY: THE TRANSPORT HELLO-ACK -- `FMO_UDP_HELLO_ACK=4` (or 16, or 3). OFF by
#: default. Decoded statically 2026-08-26; NOT live-tested.
#:
#: The lobby NPC placement routine `0x61100100` (the ~35 hardcoded
#: `0x8208xxxx`/`0x820Cxxxx` ids, Personnel Officer and friends) is called from
#: exactly two sites, `0x610F5380` and `0x610F53A0`, and both are the STATE-3
#: arm of the scene-7 (cold entry) setup machines (`0x61008E5D` in
#: `0x61008B90`, `0x6100938F` in `0x61009120`; state = `globals+0x1C4`). That
#: arm runs every frame and does:
#:
#:     if 0x61001E90(mgr) == 2:            # [[mgr+0x48]+0x10E0], the SELF
#:         0x61100100(worldmgr)             #   peer's CONNECTION STATE, and
#:         state = 4                        #   only if self is in the entity map
#:     elif globals+0x1C0 == 300: send cmd 15 (own id)   # a one-shot retry
#:
#: `+0x10E0` is written to 2 by the transport dispatcher `0x611E30A0` on an
#: INBOUND cmd 3 or cmd 16 (`0x611E321E`: reply cmd 4 with own id, then
#: state=2) or cmd 4 (`0x611E323F`: state=2, no reply). We have never sent any
#: of the three, which is why every live run has parked at state 3 with the
#: world drawing (cmd 7 is enough for that) and the NPC arm never reached --
#: and why states 4..8 of the cold machine have never run either.
#:
#: The record is transport-level (cmd < 17 never reaches the game dispatcher
#: 0x611EBA50), one u32 body = a UnitID, sent ONCE per channel on the self
#: stream right after the cmd-7 POP has been queued (`FMO_UDP_HELLO_ACK_ON=15`
#: waits for the client's own cmd 15 instead). 16 makes the client answer with
#: a cmd 4 of its own, which is the one reading our log can take; 4 is silent.
#:
#: Measure it, do not believe it: `fmocrash.py --live` prints the conn state
#: and the NPC-arm verdict; in-game `/targetnpc 0x820C1080` hits once the arm
#: has run. WARNING: Bar per the brief: an entity-map key in `0x820C....`, or the
#: `/targetnpc` hit -- never "the selftest passes".
HELLO_ACK_SPEC = os.environ.get("FMO_UDP_HELLO_ACK", "").strip()
HELLO_ACK = int(HELLO_ACK_SPEC, 0) if HELLO_ACK_SPEC else 0
if HELLO_ACK not in (0, 3, 4, 16):
    raise SystemExit(f"FMO_UDP_HELLO_ACK={HELLO_ACK_SPEC!r}: the client's "
                     f"transport dispatcher 0x611E30A0 sets +0x10E0=2 on cmd "
                     f"3, 4 or 16 only (0 = off)")
HELLO_ACK_ON = _env_int("FMO_UDP_HELLO_ACK_ON", "0")
#: The two 17-byte names at body+0x58 and +0x69. They land at entity+0x10C
#: and +0x11D and are the only fields whose effect could be VISIBLE without
#: the renderer working, so they are worth setting to something obvious.
POP_NAME1 = os.environ.get("FMO_UDP_POP_NAME1", "SERVER")
POP_NAME2 = os.environ.get("FMO_UDP_POP_NAME2", "UNIT")

#: VERIFIED: MEASURED LIVE 2026-08-24: the client DISPLAYS these two POP names as the
#: character's own name -- a player's character showed as "SERVER UNIT" (the
#: two placeholders above) in-world and in chat. So the name the player sees is
#: OURS to serve, and the right value is the played character's stored name.
#: This resolves the roster for the popping channel's address and uses its
#: first NAMED character; the env placeholders remain the fallback (no roster,
#: identity TTL lapsed, or the store disabled). FMO_UDP_POP_ROSTER_NAME=0
#: restores the fixed placeholders for an A/B.
POP_ROSTER_NAME = os.environ.get("FMO_UDP_POP_ROSTER_NAME", "1") != "0"


#: WARNING: THE PLAYED CHARACTER'S LOOK -- why everyone is "a flight attendant".
#:
#: The model is chosen by `0x611ED660`, decoded 2026-08-24 end to end:
#:
#:     al = body+0x8E
#:     if (al & 8) == 0:  kind = body+0x89          <- the UnitType FALLBACK
#:     else: switch (al >> 4):  2->3  3->2  5->6
#:                              4->0x611FC0F0(UnitID)   (a per-UnitID path)
#:                              else->1
#:     if kind == 6: 0x611FCE50(6, body+0x8B, -1)   <- ONLY kind 6 passes a sub
#:     else:         0x611FCE50(kind, 0, -1)
#:
#: KEY: So with `body+0x8E = 0` we have always taken the fallback: kind = UnitType
#: = 4, **sub = 0**. One human model, the same one for everybody, and no field
#: in the record we send can vary it. **Kind 6 is the only arm that takes an
#: index at all.**
#:
#: And we DO have per-character appearance bytes: `0x013E` carries a dword at
#: payload+0x30 (from the creation record's +0x2C), and the store keeps it --
#: live values are `01036b00` (Lex), `03056700` (Remy), `02026500` (Test Guy).
#: Byte 2 of each is **0x6B / 0x67 / 0x65 = 107 / 103 / 101**, which is
#: index-shaped and is the reason this knob exists.
#:
#: `FMO_UDP_POP_MODEL_FROM_ROSTER="<flags>:<byte>"` -- e.g. `"0x58:2"` -- sets
#: `body+0x8E` to `<flags>` (0x58 = bit 3 set, high nibble 5, i.e. kind 6) and
#: `body+0x8B` to appearance byte `<byte>` of THAT channel's character. It
#: applies to the player's own POP and to every room POP, so each person wears
#: their own bytes.
#:
#: WARNING: THIS IS A PROBE AND IT MAY WELL DRAW NOTHING. Nothing says kind 6 is a
#: person: the arm is decoded, the meaning of the index is not. A blank or
#: wrong-looking character is a RESULT (kind 6 is not the human table) and is
#: worth as much as a right one. Off by default; FMO_UDP_POP_MODEL still wins
#: when this is unset.
def _parse_model_from_roster(spec):
    if not spec:
        return None
    f, _, b = spec.partition(":")
    return int(f, 0), int(b or "2", 0)


MODEL_FROM_ROSTER = _parse_model_from_roster(
    os.environ.get("FMO_UDP_POP_MODEL_FROM_ROSTER", "").strip())

#: WARNING: THE ONE MODEL BIT UnitType 4 ACTUALLY READS -- `body+0x7A`. See
#: fmoworld.POP_TYPE4_MODEL: `0x611E7190` hardcodes kind 4 and derives the sub
#: as `(body[0x7A] != 1)`, so the human has TWO models and we have always sent
#: 0. `FMO_UDP_POP_SEX=1` serves the other one. `FMO_UDP_POP_SEX_SWEEP="0,1"`
#: walks both, one per world channel, the same Move -> Change Room trick as the
#: other sweeps.
#:
#: WARNING: It is two models, not an appearance. Do not read "still not my character"
#: as this knob failing -- it can only ever choose between two people.
_sex = os.environ.get("FMO_UDP_POP_SEX", "").strip()
POP_SEX = int(_sex, 0) if _sex else None
POP_SEX_SWEEP = [int(x, 0) for x in
                 os.environ.get("FMO_UDP_POP_SEX_SWEEP", "")
                 .replace(" ", "").split(",") if x]
_sex_n = [0]


#: KEY: Take body+0x7A from the CHARACTER'S OWN `sex` when neither explicit
#: knob is set. On by default: the field has been captured at creation since
#: the store existed, and the alternative is a model chosen by a debug sweep.
#: FMO_UDP_POP_SEX_FROM_ROSTER=0 restores "send nothing" for an A/B.
POP_SEX_FROM_ROSTER = os.environ.get(
    "FMO_UDP_POP_SEX_FROM_ROSTER", "1") != "0"


def next_pop_sex():
    """body+0x7A for the next POP. Steps once per channel that pops."""
    if not POP_SEX_SWEEP:
        return POP_SEX, None
    i = _sex_n[0] % len(POP_SEX_SWEEP)
    _sex_n[0] += 1
    return POP_SEX_SWEEP[i], (i + 1, len(POP_SEX_SWEEP))


def pop_model_for(host_ip):
    """(model_flags, model_sub) for `host_ip`'s character, or (None, None).

    Falls back to the fixed FMO_UDP_POP_MODEL pair when the roster has no
    appearance -- and SAYS which it used via the third return value, because a
    model that came from a default is not evidence about the roster."""
    if not MODEL_FROM_ROSTER:
        return None, None, None
    flags, idx = MODEL_FROM_ROSTER
    acct = identity.account_for(host_ip)
    for c in charstore.load_roster(acct):
        raw = (c.get("appearance") or "")
        if len(raw) == 8 and raw != "00000000":
            try:
                b = bytes.fromhex(raw)
            except ValueError:
                break
            if 0 <= idx < len(b):
                return flags, b[idx], f"{raw} byte{idx}"
    return None, None, "no appearance on file"


#: WARNING: THE MEMBER CHECK, `0x0181` -- "Login Check" on a room member.
#:
#: Decoded 2026-08-24 from the state machine at `0x611799A0`
#: (`kycli_lobmain.cpp`, SE's own file name). State 1 sends `0x0181` with the
#: target's two names at payload +0x10 / +0x21; state 2 waits, and the reply
#: contract is four instructions:
#:
#:     61179a23  cmp  word [rx+6], 1      ; the message id MUST be 1
#:     61179a36  cmp  word [rx+8], 0      ; <- THE ANSWER
#:     61179a3b  setg al ; inc eax        ; -> 1 if that word is 0, else 2
#:
#: and `word [rx+8]` is the packet header's **conn** field, which `build()`
#: already takes as `conn_id`. The caller (`0x610E3ADD`) turns 1 and 2 into two
#: different message boxes. We have always echoed the client's own conn, which
#: is 0, so every Login Check has taken the same arm -- and SE ships
#: *"The specified player is not logged in."* (systext 2:50), which is what that
#: arm almost certainly says.
#:
#: WARNING: **WHICH ARM MEANS "ONLINE" IS NOT DECODED.** The disassembly says 0 -> 1
#: and >0 -> 2; it does not say which box is which, and `word [rx+8]` is also
#: the field a MISMATCHED reply id turns into an `[FMxxxxx]` number. So the
#: value is a knob and the polarity is an A/B, not a fact.
MEMBER_CHECK = os.environ.get("FMO_MEMBER_CHECK", "1") != "0"
MEMBER_CHECK_HIT = _env_int("FMO_MEMBER_CHECK_HIT", "1")
MEMBER_CHECK_MISS = _env_int("FMO_MEMBER_CHECK_MISS", "0")



#: KEY: THE PENALTY LEVEL (body+0x1C2 -> unit+0x1C6, read by 0x611F7475 on
#: the pilot's own battle unit): level L >= 2 cuts ammo and BP by
#: min(100, 12(L-1)) % and the client says 32:13. The level is the pilot's
#: penalty points (penalty.pop_level; FMO_PENALTY_BATTLE_CUT=0 sends none).
def penalty_pop_extra(chan):
    """The {offset: bytes} a battle self-POP adds for the penalty level; {}
    for a pilot with fewer than 2 points."""
    from . import penalty
    return penalty.pop_extra_for(chan)


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, identity  # noqa: E402
