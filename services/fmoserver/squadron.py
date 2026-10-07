"""Squadrons: the squadron table (0x01AC -> 0x01AD), insignia and the squadron pushes."""
import datetime
import os
import struct
from .deps import fmostore
from .knobs import _env_int


#: KEY: THE SQUADRON TABLE -- `0x01AC` -> `0x01AD`, 68 bytes, copied whole by
#: `0x61172BE0` (`mov ecx,0x11; rep movsd`).
#:
#: WARNING:KEY: **RETRACTION (static RE 2026-09-08).** This block
#: used to be documented here as "THE BATTLE GROUP BLOCK ... 17 unknown dwords
#: that have to be found by probing", and the 2026-09-03 live negatives were
#: read as "the enable comes from these 17 dwords". Both are wrong in the same
#: way: it is not 17 opaque dwords and it is not the Battle Group's enable. It
#: is the **squadron table** -- `FmoSquadron.cpp`, the global at `0x613C1551` --
#: and its layout is written down by SE's own debug print at `0x6117F3A7`,
#: `"%s Group=%d%d Fmo=%d Cty=%d %s\n"`:
#:
#:     slot i (i = 0..3), at +0x10 * i:
#:       +0x00  u32  POL group id, low       <- THE CLIENT FILLS THESE IN THE REQUEST
#:       +0x04  u32  POL group id, high      <-
#:       +0x08  u8   SE's "Fmo": 1 = this POL group is a registered FMO squadron
#:       +0x09  s8   SE's "Cty": the squadron's NATION; must equal byte[lobby+0x8B4]
#:       +0x0A  u16  THE REGISTERED INSIGNIA -- a presence flag, not an index
#:       +0x0C  u32  UNIX TIMESTAMP -- the squadron's "Formed on" date
#:     +0x40    s32  MY SLOT INDEX (0..3); NEGATIVE = "not in a squadron"
#:
#: KEY: **THE REQUEST IS NOT EMPTY -- the client tells us its four POL group ids.**
#: `0x611745B0` calls the local seeder `0x611BA320` (which pulls the ids from the
#: POL-side group interface `[0x613AE380]+0x648`) and `rep movsd`s the whole
#: 68-byte table into the 80-byte `0x01AC` body. So this reply is an ANNOTATION
#: of a table we were handed, not something we invent. The send is gated on
#: `[lobby+0x20] == 4`, which is why the poll only starts once you are in-world.
#:
#: KEY: **WHAT THE REPLY DRIVES.** `0x01AD` is case 6 of the lobby push switch
#: (`0x6117F333`, `msg - 0x19A` -> index table `0x6117F950` -> jump table
#: `0x6117F914`): it copies payload+0x14, 68 bytes, straight over `0x613C1551`
#: and then calls `0x611BBD40`, which AUTO-ACTIVATES my slot's squadron when its
#: `+0x09` nation equals `byte[lobby+0x8B4]`. Activation is what finally sets the
#: 64-bit "active squadron" global `0x613C1548/0x613C154C` -- and THAT is the
#: only thing standing between the player and 8 greyed rows: the Squadron menu
#: builder `0x611BBDC0` builds all ten rows ENABLED and then greys them from the
#: static mask `0x61398FA8` whenever that global is zero. The mask is
#: `01 01 00 01 01 01 01 01 01 00`, i.e. everything except "Change squadron" and
#: "Form squadron" -- exactly the screen seen live.
#:
#: The other path to the same 68 bytes is the LOBAPI parse `0x61172BE0` into the
#: caller-supplied `[this+0x0E]`, whose one caller `0x611BC795` passes
#: `0x613C1551` as the destination. Same struct, same address, two routes.
#:
#: WARNING: **WHAT THIS BLOCK CANNOT DO.** The per-row masks for the leader-only rows
#: (Change leader / Appoint + Dismiss sub-leader / Kick / Insignia) are selected
#: by a ROLE, `(POL group flags >> 1) & 7`, read from the POL-side interface
#: `[0x613AE380]+0x618` -- not from here. And the Squadron List's own filter
#: `0x611BA1E0` additionally requires `(POL group flags & 0xE) != 4`. Those come
#: from the POL group system (login side), so this reply can un-grey rows 0/1/6/8
#: and no more.
#:
#: KNOBS. `FMO_SQUADRON=1` serves the annotation (below). The two older probes
#: still win over it, because when they are armed you are asking where dwords
#: land, not serving a table: `FMO_01AD_FILL=<u32>` writes ONE value into all 17
#: dwords, `FMO_01AD_FIELDS="idx:value;..."` overrides individual ones.
#: WARNING: A field the client uses as a COUNT will be walked that many times, so
#: start SMALL. WARNING: Empty is "unset" (the empty-env trap), not "fill with 0".
Q1AD_DWORDS = 17                       # 0x11, the rep movsd count
Q1AD_LEN = Q1AD_DWORDS * 4             # 68 = 4 slots x 16 + the s32 slot index
SQ_SLOTS = 4                           # 0x611BA1B0's loop bound, 0x613C1551..0x613C1591
SQ_SLOT_LEN = 0x10
SQ_ID_LO = 0x00                        # u32  } the 64-bit POL group id the
SQ_ID_HI = 0x04                        # u32  } client sends us
SQ_IS_FMO = 0x08                       # u8   SE's "Fmo"  -- 0x611BA1E0 requires 1
SQ_NATION = 0x09                       # s8   SE's "Cty"  -- must equal lobby+0x8B4
#: WARNING:KEY: `+0x0A` IS THE REGISTERED SQUADRON INSIGNIA, AND SERVING 0 CRASHES THE
#: CLIENT. This comment used to say "per-slot state word (non-zero greys
#: 'Dismiss sub-leader')" -- the row was read off the wrong line of the mask
#: table. Mask `0x61398FCC` is `00 00 00 00 00 00 00 01 00 00`: the ONE row it
#: greys is **row 7, "Set squadron insignia"** (`0x611BBEFC`).
#:
#: SE's own strings say why: 86:21 *"%s will be registered as the squadron
#: insignia. **It cannot be changed afterwards.**"* and 96:4 *"Registered
#: insignia"*. So `+0x0A != 0` means "this squadron already has one", and SE
#: greys the row because it is a one-time action.
#:
#: WARNING: LIVE 2026-09-09: we served 0, the row stayed ENABLED, the player clicked
#: it and **the client died**. Nothing went out on the wire between the click
#: and the socket closing, so the insignia flow walks data it already holds --
#: an owned-items table we serve as zeros (86:21 wants an item name `%s` and a
#: cost `%d`). Serving a non-zero insignia is therefore both the correct
#: semantics AND the fix: the row greys itself, exactly as it does on a real
#: squadron that has registered one.
#:
#: WARNING: ANY non-zero value is safe: `0x611BB59C` (`cmp word [esi+0xa], 0; je`)
#: uses it only as a PRESENCE FLAG that gates whether the Squadron Info panel
#: draws its "Registered insignia" row -- the text comes from a different
#: buffer (`[esp+0x20]+0x74`), so our value indexes nothing.
SQ_INSIGNIA = 0x0A                     # u16  registered insignia; != 0 greys row 7
#: The insignia id stamped on every annotated slot. Default **1**, not 0, and
#: that is deliberate: 0 is the value that un-greys the row that crashes.
#: `FMO_SQUADRON_INSIGNIA=0` restores the crash for an A/B.
SQUADRON_INSIGNIA = _env_int("FMO_SQUADRON_INSIGNIA", 1)
#: KEY: `+0x0C` IS THE "FORMED ON" DATE, a 32-bit UNIX TIMESTAMP. This was
#: called "a name/string id (UNDECODED)" until 2026-09-09, off the debug
#: print's `0x611E3D00(4, v)` call alone. The Squadron Info panel passes the
#: SAME field with selector **3** (`0x611BB566`), and `0x611E3D00` hands it
#: straight to polcore's decompose call `[0x613AE380]+0xAD0` and renders
#: `"%02d/%02d/%02d %02d:%02d"` (yy/mm/dd hh:mm). Serving 0 is what put
#: **"69/12/31 19:00"** on the player's screen -- the epoch in UTC-5, which
#: is the tell that it is a time_t and not an id.
SQ_FORMED = 0x0C                       # u32  Unix time -> "Formed on"
#: Override the formed-on date (Unix seconds). -1 = use the group's own
#: creation time from the account database, the earliest `group_member` row
#: for that group -- the owner's, i.e. when the group was made.
SQUADRON_FORMED = _env_int("FMO_SQUADRON_FORMED", -1)
SQ_MY_SLOT = SQ_SLOTS * SQ_SLOT_LEN    # 0x40, s32 -> 0x613C1591; negative = none

#: PARTIAL: FMO_SQUADRON -- annotate the four POL group ids the client sends in
#: `0x01AC` as squadrons of the player's own nation, instead of answering 68
#: zeros. Default OFF: 68 zeros is the state every measurement to date was taken
#: in, and this is a STATIC decode that has never been run against a client.
SERVE_SQUADRON = _env_int("FMO_SQUADRON", 0)
#: Override the `+0x09` nation byte we stamp on each slot. -1 = use the byte the
#: client actually holds in `lobby+0x8B4`, i.e. `script_nation()`. They MUST
#: match or `0x611BA1E0` drops the row from the Squadron List and `0x611BBD40`
#: declines to auto-activate -- so an override is a probe, not a setting.
SQUADRON_NATION = _env_int("FMO_SQUADRON_NATION", -1)
#: PROBE: treat THESE POL group ids as squadrons instead of asking the accounts
#: which groups this member is in. Comma-separated. Only for a box where the
#: group lookup cannot run -- an id listed here is annotated for EVERY member,
#: which is wrong for anyone not actually in that group.
SQUADRON_GROUPS = [int(_g, 0) for _g in
                   os.environ.get("FMO_SQUADRON_GROUPS", "").replace(" ", "").split(",")
                   if _g]

FILL_01AD = int(os.environ.get("FMO_01AD_FILL", "").strip() or "0", 0)
FIELDS_01AD = {}
for _q in os.environ.get("FMO_01AD_FIELDS", "").split(";"):
    _q = _q.replace(" ", "")
    if not _q:
        continue
    _i, _, _v = _q.partition(":")
    if not _v:
        raise SystemExit(f"FMO_01AD_FIELDS entry {_q!r} wants `<index>:<value>`")
    try:
        _i, _v = int(_i, 0), int(_v, 0)
    except ValueError:
        raise SystemExit(f"FMO_01AD_FIELDS entry {_q!r} is not two integers")
    if not 0 <= _i < Q1AD_DWORDS:
        raise SystemExit(f"FMO_01AD_FIELDS index {_i} is outside 0..{Q1AD_DWORDS - 1} "
                         f"-- the block is {Q1AD_DWORDS} dwords, and a write past "
                         f"it would land outside the buffer the client copies")
    FIELDS_01AD[_i] = _v & 0xFFFFFFFF


def _epoch(stamp):
    """`"2026-08-18T19:11:30Z"` -> Unix seconds, 0 on anything unparseable.

    Feeds slot `+0x0C`. The client renders it in LOCAL time (polcore's
    decompose call `[0x613AE380]+0xAD0`), which is how we know it is a time_t
    at all: serving 0 drew "69/12/31 19:00", the epoch at UTC-5. So a UTC
    value is the right thing to send -- the client does the localising."""
    if not stamp:
        return 0
    try:
        return int(datetime.datetime.strptime(
            str(stamp), "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=datetime.timezone.utc).timestamp())
    except (ValueError, TypeError):
        return 0


def parse_01ac(payload):
    """The four POL group ids the client put in its `0x01AC` body, as a list of
    64-bit ints (0 = an empty slot), plus the slot index it claims is its own.

    Pure, so the selftest can drive it. A short or absent body reads as four
    empty slots -- the client only fills them from `0x611BA320`, and that seeder
    returns zeros when the POL side has handed it no groups, which is exactly
    the case this function must not disguise as an error."""
    ids, mine = [], -1
    for i in range(SQ_SLOTS):
        off = i * SQ_SLOT_LEN
        if len(payload) >= off + 8:
            lo, hi = struct.unpack_from("<II", payload, off)
            ids.append((hi << 32) | lo)
        else:
            ids.append(0)
    if len(payload) >= SQ_MY_SLOT + 4:
        mine = struct.unpack_from("<i", payload, SQ_MY_SLOT)[0]
    return ids, mine


#: VERIFIED:KEY: `0x01C4` -- **THE SQUADRON INSIGNIA REGISTRATION**, measured LIVE
#: 2026-09-09T19:45:52Z the moment a player picked one:
#:
#:     <- msg=0x01C4 (lobapi-req) len=68
#:        0000  03 00 00 00 00 00 00 00  83 00 00 00  00 ...
#:              ^^ u64 POL group id = 3  ^^ u32 id = 0x83 = 131 "Wild Apes"
#:
#: 48-byte body: `{u64 group id @+0x00, u32 insignia id @+0x08}`, rest zero.
#: The table already answered it with a bare message 1 (`0x01C4: (0x0001, 0)`,
#: parse = stub), which is why the client believed it and drew the insignia --
#: and why nothing survived a relaunch. The ack stays; we just STORE it now.
#:
#: WARNING: SE treats registration as ONE-TIME (86:21 "It cannot be changed
#: afterwards"), so `set_squadron_insignia` keeps the FIRST value and reports a
#: conflict rather than overwriting a squadron's emblem.
MSG_INSIGNIA_SET = 0x01C4
INSIGNIA_SET_GROUP = 0x00              # u64
INSIGNIA_SET_ID = 0x08                 # u32


def parse_insignia_set(payload):
    """(group_id, insignia_id) from a 0x01C4 body; (0, 0) if it is too short.
    Pure, so the selftest can drive it off the bytes a live client sent."""
    if len(payload) < INSIGNIA_SET_ID + 4:
        return 0, 0
    lo, hi = struct.unpack_from("<II", payload, INSIGNIA_SET_GROUP)
    return (hi << 32) | lo, struct.unpack_from("<I", payload, INSIGNIA_SET_ID)[0]


def squadron_insignia_word(nation, group_id=0):
    """The u16 for slot +0x0A: the squadron's registered insignia, or the crash
    guard when we cannot safely un-grey the row.

    0  -> "none registered yet". SE greys row 7 only when this is NON-zero
          (86:21: an insignia "cannot be changed afterwards"), so 0 makes the
          row clickable -- which is only safe once 0x019F has given the picker
          rows to show. See MSG_INSIGNIA_LIST.
    !=0 -> the row is greyed. Used both for a real registration and, when the
          offer list is empty, as the guard that keeps the 2026-09-09 crash
          out of reach.

    WARNING: Nothing persists a registration yet -- the message the picker sends on OK
    is undecoded, so this returns 0 whenever the picker is safe to open. When
    that message is decoded, this is where the stored id belongs."""
    if group_id and fmostore is not None:
        stored = fmostore.squadron_insignia(group_id)
        if stored:
            return stored                   # a REAL registration; SE greys row 7
    if not insignia_for(nation):
        return SQUADRON_INSIGNIA or 1      # guard: keep row 7 out of reach
    return 0                                # safe: let the player register one


#: KEY: THE SQUADRON'S NATION IS RECORDED, NOT RE-READ (2026-09-30). Slot
#: +0x09 used to be stamped with the nation of whoever was looking, so a
#: member who had defected still saw an active squadron of his NEW army.
#: SE (manual p.50): a squadron belongs to the army it was formed in, and a
#: member in the enemy army sees it inactive. A squadron is a POL group and
#: is formed outside FMO, so the nation is recorded the first time the
#: squadron is served to one of its members (fmo_squadron_nation) and served
#: from the record after that. FMO_SQUADRON_NATION_STORE=0 = the old stamp.
SQUADRON_NATION_STORE = _env_int("FMO_SQUADRON_NATION_STORE", "1") != 0


def squadron_nation(group_id, viewer_nation=None, who=None):
    """(nation, source) for squadron `group_id`: the recorded one, recording
    `viewer_nation` first when there is none yet and one is given; (0, why)
    when nothing is on file and nothing may be recorded. Never raises."""
    if not SQUADRON_NATION_STORE or fmostore is None or not charstore.FMO_DB:
        return 0, "no store (FMO_SQUADRON_NATION_STORE=0 or FMO_DB off)"
    try:
        got = fmostore.squadron_nation(group_id)
        if not got and viewer_nation in (1, 2):
            got = fmostore.set_squadron_nation(group_id, viewer_nation, who)
            if got:
                return got, f"recorded now from {who or 'a member'}'s pilot"
    except Exception as e:
        return 0, f"store error {e!r}"
    return (got, "recorded") if got in (1, 2) else (0, "not recorded yet")


def reply_01ad(req=b"", groups=(), char=None, who=None):
    """The 68-byte squadron table, and a one-line description of it.

    `char` is the pilot being served: its nation is what the client will
    compare our +0x09 against (byte[lobby+0x8B4], the 0x014A this pilot got),
    so it MUST come from the same resolver -- script_nation(char).

    `req` is the client's own `0x01AC` body -- the four POL group ids we are
    being asked to annotate. Everything here is derived from it; nothing is
    invented, because a group id we made up addresses no POL group and the
    client would hand it straight back to `[0x613AE380]+0x618`.

    WARNING:KEY: `groups` IS THE LITTER FILTER, AND IT IS NOT OPTIONAL POLISH.
    MEASURED LIVE 2026-09-09 off prod (member 3 / Lex at 127.0.0.1): only
    **slot 0** carries a real id. The client's seeder `0x611BA320` zeroes the
    table, calls `[0x613AE380]+0x648` into a 32-byte STACK local, and scatters
    all eight of that local's dwords into the four slots -- but the POL side
    wrote only the first two, so slots 1..3 are **the caller's own stack
    litter** and they change on every poll:

        03:22:36Z  [0]=3  [1]=0x0FBB78840FBBC074  [2]=0x611745C600000000  [3]=0x00000050000001AC
        03:35:10Z  [0]=3  [1]=0x3B8888893B5D2F1B  [2]=0x4416000044480000  [3]=0x001A00003F000000

    `0x611745C6` is an address INSIDE the 0x01AC sender itself, and
    `0x50`/`0x1AC` are literally the length and message id pushed at
    `0x611745B6` -- so "non-zero" is NOT a test for "a group". An earlier draft
    of this function annotated every non-zero slot and would have told the
    client that a code address is a squadron of its nation.

    So a slot is annotated **only when its id is a POL group this member is
    actually in** (`Session.pol_groups`, straight out of the account
    database's `group_member`, through accounts.member_groups). That is authoritative rather than heuristic, and litter
    cannot pass it except by colliding with a real group id -- in which case
    the id was real anyway. `groups` empty = annotate nothing."""
    if FILL_01AD or FIELDS_01AD:
        b = bytearray()
        for i in range(Q1AD_DWORDS):
            b += struct.pack("<I", FIELDS_01AD.get(i, FILL_01AD) & 0xFFFFFFFF)
        parts = []
        if FILL_01AD:
            parts.append(f"PROBE: all {Q1AD_DWORDS} dwords = {FILL_01AD}")
        if FIELDS_01AD:
            parts.append("overrides " + ", ".join(f"[{i}]={v}"
                                                  for i, v in sorted(FIELDS_01AD.items())))
        return bytes(b), "; ".join(parts)

    if not SERVE_SQUADRON:
        return bytes(Q1AD_LEN), ("68 ZEROS (FMO_SQUADRON=0) -- every slot's "
                                 "+0x08 is 0, so 0x611BA1E0 drops every row "
                                 "from the Squadron List, nothing activates, "
                                 "0x613C1548 stays 0 and mask 0x61398FA8 greys "
                                 "8 of the 10 Squadron rows")

    ids, _mine = parse_01ac(req)
    nation, nation_src = zoneentry.script_nation(char)
    if SQUADRON_NATION >= 0:
        nation, nation_src = SQUADRON_NATION, "FMO_SQUADRON_NATION (a PROBE)"
    known = set(SQUADRON_GROUPS) if SQUADRON_GROUPS else set(groups)
    where = ("FMO_SQUADRON_GROUPS (a PROBE -- the accounts were NOT consulted)"
             if SQUADRON_GROUPS else "accounts.member_groups")

    def formed_for(gid):
        """The "Formed on" Unix time for one group. The knob wins; otherwise
        the group's own creation time, if the caller passed the mapping
        pol_groups builds. 0 renders as the epoch (69/12/31), which is what
        the player saw before this field was decoded."""
        if SQUADRON_FORMED >= 0:
            return SQUADRON_FORMED
        info = groups.get(gid) if isinstance(groups, dict) else None
        return int(info.get("formed", 0)) if isinstance(info, dict) else 0

    b = bytearray(Q1AD_LEN)
    filled, litter = [], []
    for i, gid in enumerate(ids):
        off = i * SQ_SLOT_LEN
        # The id is echoed VERBATIM either way -- the client put it there and
        # 0x611BAAA0 hands it back to [0x613AE380]+0x618. Only the annotation
        # is withheld from a slot we cannot vouch for.
        struct.pack_into("<II", b, off + SQ_ID_LO,
                         gid & 0xFFFFFFFF, (gid >> 32) & 0xFFFFFFFF)
        if not gid:
            continue
        if gid not in known:
            litter.append(i)
            continue
        b[off + SQ_IS_FMO] = 1
        # +0x09 is the SQUADRON's nation, not the viewer's (squadron_nation):
        # a member now in the other army gets a row that will not activate.
        _sqn = (nation if SQUADRON_NATION >= 0 else
                squadron_nation(gid, nation if char is not None else None,
                                who)[0] or nation)
        b[off + SQ_NATION] = _sqn & 0xFF
        # +0x0A: the REGISTERED insignia, which also decides whether "Set
        # squadron insignia" is greyed (mask 0x61398FCC, row 7).
        #
        # KEY: THE GUARD LIFTS ITSELF. Until 0x019F was decoded we had to serve a
        # non-zero here unconditionally, because 0 leaves that row live and the
        # picker behind it had no rows -- 0x611BE949 then dereferences
        # rows[selected] and the client dies. Now that we serve the picker its
        # list, 0 is SAFE and correct: it means "this squadron has not
        # registered one yet", which is true, and it lets the player use the
        # screen. So the crash guard applies only when we could NOT populate
        # the picker -- an empty offer list is still the crashing state.
        struct.pack_into("<H", b, off + SQ_INSIGNIA,
                         squadron_insignia_word(nation, gid) & 0xFFFF)
        struct.pack_into("<I", b, off + SQ_FORMED, formed_for(gid) & 0xFFFFFFFF)
        filled.append(i)
    mine = filled[0] if filled else -1
    struct.pack_into("<i", b, SQ_MY_SLOT, mine)

    tail = (f"; slot(s) {litter} carry an id that is NOT a group this member "
            f"is in -- left UNANNOTATED (stack litter, see the docstring)"
            if litter else "")
    if not any(ids):
        return bytes(b), ("FMO_SQUADRON=1 but the client sent FOUR EMPTY GROUP "
                          "IDS -- there is nothing to annotate. The POL side "
                          "handed 0x611BA320 no groups, so this is a POL "
                          "group-system problem, NOT a 0x01AD one")
    if not filled:
        return bytes(b), (f"FMO_SQUADRON=1 but NONE of the ids the client sent "
                          f"is a POL group this member belongs to (checked "
                          f"against {where}), so nothing is annotated and the "
                          f"Squadron List stays empty{tail}")
    return bytes(b), (f"FMO_SQUADRON=1: slot(s) {filled} marked +0x08=1 "
                      f"(SE's \"Fmo\"), +0x09={nation} (\"Cty\", from "
                      f"{nation_src}), my-slot-index={mine}, groups from "
                      f"{where}. 0x611BBD40 should auto-activate slot "
                      f"{mine}{tail}")


#: VERIFIED:KEY: `0x019F` -- **THE SQUADRON INSIGNIA LIST**, and the reason "Set squadron
#: insignia" killed the client on 2026-09-09.
#:
#: The shim's VEH log (this box IS the client box) named the fault in one look:
#: `ACCESS_VIOLATION at 0x611BE949`, `mov ecx,[ecx+8]` with `ecx=0`, `READ from
#: 00000008`, and **`ebp=0000100B`** -- the picked menu command, i.e. that very
#: row. The instruction before it is `mov ecx,[edx+ecx*4]`, so the fault is
#: `rows[selected] == NULL`: **the picker had no rows and the client did not
#: check.** Not a table walk, not the owned-items bitset.
#:
#: `0x611BE1F0` builds those rows, and every input is ours:
#:
#:     611be322  cmp dword [lobby+0x7E35], 0   ; the COUNT
#:     611be330  jle <bail>                    ; 0 -> the list stays EMPTY
#:     611be337  lea ebp, [lobby+0x7E3B]       ; the ROWS, stride 8 (0x611BE3EC)
#:     611be340  movzx edx, byte [ebp-2]       ; row nation
#:     611be344  cmp edx, <byte[lobby+0x8B4]>  ; == MY nation, or the row is skipped
#:     611be34e  movzx eax, word [ebp]         ; the INSIGNIA ID, looked up in
#:                                             ; resource 0x1B2E3
#:
#: KEY: `lobby+0x7DF5` has exactly ONE writer in the image -- **message 0x019F**,
#: arm `0x6117F693` (`mov ecx,0x5AD; lea esi,[ebp+0x14]; lea edi,[ebx+0x7DF5];
#: rep movsd`), case 3 of the lobby push switch. So the list is SERVER-AUTHORED
#: and we had never sent it. `0x6117A2E4` zeroes the same 5,812 bytes on every
#: lobby reset, so it must be re-sent per scene, exactly like the zone table.
#:
#: THE IDS ARE THE CLIENT'S OWN. Resource `0x1B2E3` = 111331 resolves (fmofile.py)
#: to `data/BB/F13/D31.DAT`, an FMDT-wrapped `ITM\0` container one directory over
#: from the accessory tables -- 105 insignia, ids 1..453, **with SE's own English
#: names already in the file** ("Star Breakers", "Chasm Owls", "17th Mobile
#: Squadron"). Banked as `fmodata/fmo-insignia.tsv` by
#: the build step. An id NOT in that file is an id the
#: client cannot resolve, so we never invent one.
#:
#: WARNING: NATION. The catalogue's flag is 1 O.C.U. / 2 U.S.N. / 255 "any", but the
#: WIRE row is compared for EQUALITY against `byte[lobby+0x8B4]`, so a 255 row
#: has to go out carrying the PLAYER's nation or the client skips it. That
#: conversion is here, not in the table.
MSG_INSIGNIA_LIST = 0x019F
#: WARNING:KEY: THE HEADER IS NOT OURS TO ADD, AND COUNTING IT TWICE COST A SECOND
#: IDENTICAL CRASH (2026-09-09). The arm reads `lea esi,[ebp+0x14]` where `ebp`
#: is the RECEIVED RECORD, so the block starts at **record+0x14** -- and
#: `build()` already takes a PAYLOAD, i.e. **payload+0 IS record+0x14**. The
#: first cut defined the offsets as `0x14 + <block offset>`, which put the count
#: 20 bytes into the ROW area and left `lobby+0x7E35` reading the zero at
#: payload+0x40. Count 0 is exactly the state that crashes, so the fix looked
#: like a no-op and the client died at the same address.
#:
#: KEY: The precedent that settles it was already in this file: `0x01AD`'s body is
#: 68 bytes with NO header offset (`LOBAPI[0x01AC] = (0x01AD, 68)`) and its arm
#: copies from record+0x14 too -- and it works. Every offset below is therefore
#: BLOCK-relative, and the selftest pins it against that precedent rather than
#: against my own arithmetic (which is what the first version asserted).
INSIGNIA_BLOCK = 0x5AD * 4             # 5,812 B = the rep movsd count at 0x6117F693
INSIGNIA_LIST_LEN = INSIGNIA_BLOCK               # the payload IS the block
INSIGNIA_COUNT_OFF = 0x40              # -> lobby+0x7E35 (0x7E35 - 0x7DF5), u32
INSIGNIA_ROW_OFF = 0x44                # -> lobby+0x7E39, the rows
INSIGNIA_ROW_LEN = 8                             # 0x611BE3EC `add ebp, 8`
INSIGNIA_MAX = (INSIGNIA_BLOCK - INSIGNIA_ROW_OFF) // INSIGNIA_ROW_LEN
INSIGNIA_TSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "fmodata", "fmo-insignia.tsv")

#: Which insignia to offer: "all" (every id in the catalogue this nation can
#: use), "none", or an explicit comma-separated id list. Default "all" -- an
#: EMPTY list is the state that crashes, so "offer nothing" must be asked for
#: rather than fallen into.
INSIGNIA_OFFER = os.environ.get("FMO_SQUADRON_INSIGNIA_LIST", "").strip() or "all"


def load_insignia(path=INSIGNIA_TSV):
    """[(id, nation_flag, english)] from fmo-insignia.tsv, or [] with no file."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            try:
                out.append((int(r["id"]), int(r["nation_flag"]),
                            r.get("english", "")))
            except (KeyError, ValueError):
                continue
    return out


INSIGNIA = load_insignia()


def insignia_for(nation):
    """[(id, english)] this nation may register, honouring FMO_SQUADRON_INSIGNIA_LIST.

    A catalogue flag of 255 means "either nation", and those rows still have to
    go out stamped with the PLAYER's nation -- see the note above. Pure."""
    if INSIGNIA_OFFER.lower() == "none":
        return []
    if INSIGNIA_OFFER.lower() != "all":
        want = {int(x, 0) for x in INSIGNIA_OFFER.replace(" ", "").split(",") if x}
        rows = [(i, en) for i, _f, en in INSIGNIA if i in want]
    else:
        rows = [(i, en) for i, f, en in INSIGNIA if f == 255 or f == nation]
    return rows[:INSIGNIA_MAX]


#: KEY: THE BATTLE FEE RATES RIDE THIS BLOCK TOO (static 2026-10-01). The Battle
#: Fee the setup screen shows (Playing Manual p.62, "Battle Fee : 0") and the
#: sortie checks against the wallet is the CLIENT's sum, 0x61175CB0 over the
#: setup's 21 item records (setup+0x28, stride 0x18): for each of a record's
#: six modification bytes (+0x0C..+0x11; SE's own error string 0x6133B5B0
#: calls the index "ReconIdx" of "FmoComGetReconstType") that is non-zero,
#:     fee += base * level * rate[type] / 100000       (0x611E1F60, 64-bit)
#: where base is the first u32 of a record the item's catalogue entry
#: resolves (0x611A4760; most likely its price, NOT proved), and type =
#: 0x611E1C20(kind, modification index), the client's per-kind tables at
#: 0x613998D0..0x61399960, which only ever return 0..7. rate[type] is the u32
#: at lobby+0x7E0D + type*4 (0x61175D3E) = THIS block's +0x18 + type*4, so the
#: eight rates are block +0x18..+0x37, written by nothing but this push. We
#: served them as zeros, so every fee read 0, exactly as the manual's own
#: screenshot does. Callers: the setup screen (0x6103F6B5, "%6d"), the sortie
#: check (0x611B73E5, against lobby+0x88C) and 0x611B8287.
#: WARNING: The fee is computed and shown by the client. No decoded request
#: field carries it (0x0139 in sortie.py; its +0x10..+0x3F is 48 bytes from
#: [[globals+0x198]+0x2C]+0x388, not decoded), recomputing it here would need
#: the client's per-kind type tables and the item `base`, and this server
#: never authors a modified part (its item records carry zeros there), so
#: the debit SE made (0x01A1, 8:74 "Paid H$%d as the sortie cost for a
#: modified unit") is not built.
#: FMO_BATTLE_FEE_RATES: eight comma-separated integers, rate[0]..rate[7];
#: empty (default) = all zero, the block byte for byte as before. SE's values
#: were never found in any captured page, so there is no default to ship.
BATTLE_FEE_RATES_OFF = 0x18
BATTLE_FEE_RATES_N = 8
BATTLE_FEE_DIVISOR = 100000           # 0x186A0 at 0x611E1F7D


def parse_battle_fee_rates(text):
    """(rates tuple of 8, error or None). Empty or malformed = all zero."""
    text = (text or "").strip()
    if not text:
        return (0,) * BATTLE_FEE_RATES_N, None
    try:
        vals = [int(x, 0) for x in text.replace(" ", "").split(",") if x != ""]
    except ValueError as e:
        return (0,) * BATTLE_FEE_RATES_N, f"not integers ({e})"
    if len(vals) != BATTLE_FEE_RATES_N or any(v < 0 or v > 0x7FFFFFFF for v in vals):
        return (0,) * BATTLE_FEE_RATES_N, (f"needs exactly {BATTLE_FEE_RATES_N} values "
                                           f"in 0..2147483647, got {vals}")
    return tuple(vals), None


BATTLE_FEE_RATES, _BATTLE_FEE_ERR = parse_battle_fee_rates(
    os.environ.get("FMO_BATTLE_FEE_RATES", ""))


def battle_fee(base, level, rate):
    """One modification's share of the fee, as 0x611E1F60 computes it."""
    return int(base) * int(level) * int(rate) // BATTLE_FEE_DIVISOR


def insignia_payload(nation, rates=None):
    """The 0x019F body: a u32 count then 8-byte rows {u8 nation, u8 0, u16 id,
    u32 0}. The nation byte is the PLAYER's, because 0x611BE344 tests equality.
    +0x18..+0x37 are the eight Battle Fee rates (BATTLE_FEE_RATES)."""
    rows = insignia_for(nation)
    b = bytearray(INSIGNIA_LIST_LEN)
    for i, r in enumerate(BATTLE_FEE_RATES if rates is None else rates):
        struct.pack_into("<I", b, BATTLE_FEE_RATES_OFF + 4 * i, int(r) & 0xFFFFFFFF)
    struct.pack_into("<I", b, INSIGNIA_COUNT_OFF, len(rows))
    for i, (rid, _en) in enumerate(rows):
        off = INSIGNIA_ROW_OFF + i * INSIGNIA_ROW_LEN
        b[off] = nation & 0xFF
        struct.pack_into("<H", b, off + 2, rid & 0xFFFF)
    return bytes(b)


def insignia_push(conn_id, nation):
    """The 0x019F push, or None when nothing is on offer.

    A pure push like 0x016C: nothing in the image REQUESTS 0x019F, so it goes
    out on the queue sequence. It must ride a moment the client is already
    in-scene for, and it must REPEAT per scene -- 0x6117A2E4 zeroes the block
    on every lobby reset."""
    rows = insignia_for(nation)
    if not rows and not any(BATTLE_FEE_RATES):
        return None
    return packet.build(MSG_INSIGNIA_LIST, insignia_payload(nation), pushes.QUEUE_SEQ, conn_id)


#: THE SQUADRON TABLE PUSHES -- three arms over the 0x613C1551 table 0x01AD
#: fills (SQ_* above). All three match a slot by its 64-bit POL group id
#: (+0x00/+0x04) and have NO scene gate:
#:   0x01AF (arm 0x6117F3D3)  ACTIVATE: the matching slot's INDEX -> 0x613C1591
#:          ("my slot"). WARNING: The arm writes a REGISTER to 0x613C1591 before it
#:          searches, so an id that is not in the table leaves garbage there.
#:          Never send one the client did not hand us in 0x01AC.
#:   0x01B1 (arm 0x6117F430)  ROW UPDATE: the 16 bytes at +0x00 replace the
#:          matching slot (id, +0x08 is_fmo, +0x09 nation, +0x0A insignia,
#:          +0x0C formed-on).
#:   0x01C5 (arm 0x6117F4A4)  INSIGNIA + MONEY: +0x0C u32 -> lobby+0x88C FIRST
#:          and unconditionally (a STORE), then +0x08 u16 -> the matching
#:          slot's +0x0A. This is the wire half of "Set squadron insignia":
#:          the row greys itself the moment it lands, without the re-poll.
MSG_SQUADRON_ACTIVATE = 0x01AF
S1AF_LEN = 0x08
MSG_SQUADRON_ROW = 0x01B1
S1B1_LEN = 0x10
MSG_SQUADRON_INSIGNIA = 0x01C5
S1C5_LEN = 0x10
S1C5_INSIGNIA = 0x08
S1C5_MONEY = 0x0C
#: FMO_INSIGNIA_PUSH: '0' (default) = a 0x01C4 registration is acked with
#: message 1 and stored, as before. '1' = the ack is followed by a 0x01C5
#: carrying the stored insignia and the pilot's stored money -- skipped, with
#: a log line, when the money is unknown (a store of 0 would empty the wallet).
INSIGNIA_PUSH = (os.environ.get("FMO_INSIGNIA_PUSH", "").strip() or "0") != "0"


def _group_id_pair(group_id):
    g = int(group_id)
    return struct.pack("<II", g & 0xFFFFFFFF, (g >> 32) & 0xFFFFFFFF)


def squadron_activate_body(group_id):
    return _group_id_pair(group_id)


def squadron_row_body(group_id, is_fmo=1, nation=0, insignia=0, formed=0):
    return (_group_id_pair(group_id)
            + struct.pack("<Bb", is_fmo & 0xFF, int(nation))
            + struct.pack("<HI", int(insignia) & 0xFFFF, int(formed) & 0xFFFFFFFF))


def squadron_insignia_push_body(group_id, insignia, money):
    b = bytearray(S1C5_LEN)
    b[0:8] = _group_id_pair(group_id)
    struct.pack_into("<H", b, S1C5_INSIGNIA, int(insignia) & 0xFFFF)
    struct.pack_into("<I", b, S1C5_MONEY, int(money) & 0xFFFFFFFF)
    return bytes(b)


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, packet, pushes, zoneentry  # noqa: E402
