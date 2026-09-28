"""Entering a zone (0x0150 -> 0x0153): MapNo, MapKind, the PS2 map sets, the pilot's start
position."""
import os
import struct
from .knobs import _env_int


#: 0x0150 -> 0x0153. WARNING: NOT N+1 -- the pairing genuinely varies, so read the
#: state's compare rather than assuming. State 12 sends 0x150 with 0xFFFF as its
#: first word (0x6117BD39 -> 0x61173CE0 -> 0x61173D1E, init(0x150, 0xC, 0)) and
#: state 13 checks for 0x153 at 0x6117BDCC (jne -> error).
#:
#: The success arm reads:
#:     word [payload+0x14]  -> ebp+0x4F06 (and later globals+0x1A0)
#:     dword [payload+0x18], dword [payload+0x1C] -> passed to 0x61006350
#:     payload+0x28  .. +0x124  (0x3F dwords = 252B) -> ebp+0x6B3A
#:     payload+0x124 .. +0x17C  (0x16 dwords =  88B) -> globals+0xD0
#: so the payload runs to at least 0x17C = 380 bytes.
#:
#: VERIFIED: WHAT THOSE FIELDS ARE (2026-08-18, static RE; every claim carries its
#: address, and anything not carrying one is not claimed).
#:
#: FIRST, WHAT THE EXCHANGE IS. The request is not a query -- it is a JOIN. The
#: builder 0x61173CE0 takes a u16 and stores it at lobby+0x4F06 before sending,
#: and 0x0153's word at payload+0x14 is written to THE SAME FIELD. So the client
#: asks for a MapKind and the reply grants one. State 12 asks with 0xFFFF; the
#: other three call sites pass 0xFFFD (0x61176804, 0x61176A31) or a u16 taken
#: from a UI record (0x61184894) -- so 0xFFFF/0xFFFD are sentinels and a real
#: selector is a small number.
#:
#: `word [payload+0x14]` IS "MapKind". Named by 0x61328AC4 "MapKind=%u MapNo=%u",
#: whose first argument is the same value this field feeds. It is BANDED, and
#: five predicate functions in a row test the bands -- which is the strongest
#: statement available about which values are legal:
#:     0x61003CE0   100..109  or  300..309
#:     0x61003D10   ==109     or  ==309
#:     0x61003D30   200..209  or  400..409
#:     0x61003D60   600..607
#:     0x61003E30   500..519, 207..209, 407..409
#: WARNING: ZERO IS IN NO BAND. That is the most likely reason an all-zero 0x0153 is the
#: first reply the client refuses -- but it is a MOTIVE, not a proof: nothing
#: read so far shows the fatal branch itself. Do not write it down as the cause.
#:
#: `dword [payload+0x18]` -> globals+0x1AC, and 0x61005105 / 0x61005547 test it
#: `== 1`. Its meaning is unknown; 1 is what the client's own compare prefers.
#: `dword [payload+0x1C]` -> globals+0x1B4. Nothing read so far consumes it.
#:
#: THEN THE CLIENT LEAVES THE LOBBY. 0x61006350 is a method of the singleton at
#: [0x613AE664] -- the object fmo.py's notes call "globals", pinned by
#: 0x61006398 loading it and taking +0x194, the lobby object. It stores the three
#: values (+0x1A4 with its previous value shadowed to +0x1A8, +0x1AC shadowed to
#: +0x1B0, +0x1B4), copies 20 bytes from THE PAYLOAD POINTER to globals+0x3C, and
#: then changes the top-level scene: globals+0x24 becomes 6 or 7 via the loaders
#: 0x61004A40 / 0x61004680 (the full scene table is the jump table at 0x61006240).
#: So 0x0153 is the message that takes the client out of the lobby and into a
#: game scene, which is exactly the shape of an exit-to-Viewer when it fails.
#:
#: `payload+0x00 .. +0x13` -- 20 bytes -> globals+0x3C. Almost certainly an
#: ENDPOINT: it is the same 20 bytes as the 0x0322's endpoints and as the one
#: inside a 0x0155 LoginGroup entry, and 0x61006350 copies it as five bare dwords
#: without looking inside. WARNING: NOT PROVEN -- no reader of globals+0x3C has been
#: found, because it is reached as `this+0x3C` inside the singleton's own methods
#: where a scan keyed on the .data pointer cannot see it.
#:
#: `payload+0x28 .. +0x123` -- 252 bytes -> lobby+0x6B3A. Two debug prints name
#: its head, and the offsets are the client's own:
#:     0x61328B88  "MapNo=%u ClientScriptNo=%u MusicNo=%u SeNo=%u"  (0x610050E6)
#:     0x61328A6C  "PilotPos %3.3f %3.3f %3.3f %3.3f"               (0x61003425)
#:     +0x00 u32 MapNo   +0x04 u32 ClientScriptNo   +0x08 u32 MusicNo
#:     +0x0C .. +0x1B    four FLOATS, PilotPos
#:     +0x1C u32 SeNo
#:     +0x5C .. +0x7B    EIGHT u32 -- the loop at 0x61003120 runs `cmp ebx, 8`
#:                       and prints each as "%d:%x", then looks each up with
#:                       0x61072650, so they are object/unit ids.
#: The rest of the 252 bytes is not decoded.
#:
#: `payload+0x124 .. +0x17B` -- 88 bytes -> globals+0xD0. THE SAME STRUCTURE a
#: 0x0155 LoginGroup entry carries at its +0x20; that one lands at globals+0x128,
#: 88 bytes further along. Contents unknown -- see the 0x0155 note.
MSG_0150_REQ = 0x0150
MSG_0150_REPLY = 0x0153
REPLY_0153_LEN = 0x17C                 # 380

#: Payload offsets of 0x0153, from the reads listed above.
R153_ENDPOINT = 0x00                   # 20B -> globals+0x3C
R153_MAPKIND = 0x14                    # u16 -> lobby+0x4F06, globals+0x1A0/+0x1A4
R153_FIELD_18 = 0x18                   # u32 -> globals+0x1AC, compared == 1
R153_FIELD_1C = 0x1C                   # u32 -> globals+0x1B4
R153_SETUP = 0x28                      # 252B -> lobby+0x6B3A
R153_INFO88 = 0x124                    # 88B -> globals+0xD0
SETUP_LEN = 0x3F * 4                   # 252, the `rep movsd` count

#: Offsets INSIDE the 252-byte setup block (add R153_SETUP for payload offsets).
SU_MAPNO, SU_CLIENTSCRIPTNO, SU_MUSICNO = 0x00, 0x04, 0x08
SU_PILOTPOS = 0x0C                     # 4 x float
SU_SENO = 0x1C
SU_UNITS = 0x5C                        # 8 x u32
SU_UNIT_SLOTS = 8

#: WARNING: EVERY VALUE BELOW IS A PROBE, NOT A DECODED CONSTANT. The layout above is
#: read out of the client; WHICH values are legal is not, because the map and
#: script ids live in the data containers (data/??/F??/D??.DAT), which are not
#: decoded. MapKind is the one exception -- the bands are the client's own
#: compares -- and 100 is simply the first legal value in the first band.
#:
#: Set FMO_0153_FILL=0 to go back to the all-zero 0x0153 for an A/B. Keeping that
#: switch matters: zeros are the only 0x0153 whose effect has actually been
#: observed, so it is the control, not a fallback.
#: WARNING: MAPKIND IS ALSO A RESOURCE ID, AND A WRONG ONE CRASHES THE CLIENT.
#: Measured 2026-08-18 with the crash instrument against a live crash. On entering
#: the scene, 0x61005100 registers three resources with 0x61125460(type, id, ..):
#:
#:     type 2  id = MapNo      the MAP
#:     type 3  id = MapKind    the SCRIPT
#:     type 4  id = 0
#:
#: With MapNo=1 / MapKind=100 the client's own resource table came back as:
#:
#:     slot 0   type 2  id 1     blob 0x27f60020   base 0x27ec6020  size 455360
#:     slot 1   type 3  id 100   blob 0x27f60020   base 0          size 2
#:
#: The MAP loaded (a real 445 KB terrain blob). The SCRIPT did not -- base 0,
#: no size -- but slot 1's blob pointer was left holding **the map's buffer**.
#: Both records are then initialised, the pointer-fixup pass at 0x61125080 runs
#: over that one buffer TWICE, and the second pass turns an already-absolute
#: pointer into 2*base + offset. That is the access violation at 0x611250A2.
#:
#: So the crash is not the map and not the protocol: **MapKind 100 has no script
#: resource**. A MapKind whose script exists should break the chain.
#:
#: WARNING: Which ones exist is NOT known -- the ids live in the data containers, which
#: are not decoded. 98 and 99 are the best leads in the image: 0x61004AE0 and
#: 0x61005100 both substitute exactly those two for this slot when
#: `byte [lobby+0x8B4]` is 1 or 2, which is the client hardcoding script ids it
#: expects to exist. They are outside the MapKind bands, and the bands gate other
#: predicates rather than this registration -- so they are worth trying, and
#: worth NOT assuming.
FILL_0153 = os.environ.get("FMO_0153_FILL", "1") != "0"

#: A comma-separated list to walk, one value per GAME connection, so a sweep
#: costs relaunches instead of edits. Empty = use FMO_MAPKIND. Each value is
#: logged with the reply, so the log says which one produced which outcome.
MAPKIND_SWEEP = [int(x, 0) for x in
                 os.environ.get("FMO_MAPKIND_SWEEP", "").replace(" ", "").split(",")
                 if x]
MAPKIND = _env_int("FMO_MAPKIND", "100")
#: VERIFIED: SOLVED 2026-08-18: **ONLY TWELVE MAPS EXIST, AND 1 IS NOT ONE OF THEM.**
#:
#: The client turns a resource id into a FILE PATH, and a path that is not on
#: disk yields size 0 -- which is the whole crash. From `0x611253C0` and the
#: table at `0x61337AC0`:
#:
#:     index = BASE[type] + id      BASE = [56117, 53557, 55605, 93545, 95593]
#:     type 2 = MAP,    id = MapNo    -> 55605 + MapNo    (512 slots)
#:     type 3 = SCRIPT, id = MapKind  -> 93545 + MapKind  (2048 slots)
#:
#: and from `0x6113DAE0`, which builds the name arithmetically:
#:
#:     q, r = divmod(index, 100)
#:     data\<'A'+q//1000><'A'+(q%1000)//100>\F<q%100:02d>\D<r:02d>.DAT
#:
#: A scan of the install with the resource path formula walks that over both id spaces. Against
#: the live install AND the patch mirror, identically:
#:
#:     type 2 MAP     12 of 512 present:  101-102, 121-124, 141-144, 151, 161
#:     type 3 SCRIPT  1000 of 2048:       0-999
#:
#: So MapNo 1 and 2 resolve to files that do not exist, the map allocates zero
#: bytes, the script slot inherits its pointer, and the fixup at 0x61125080
#: relocates one buffer twice -> ACCESS_VIOLATION at 0x611250A2. The default is
#: now a map that IS there.
#:
#: WARNING: MapNo and MapKind are DIFFERENT id spaces despite looking alike. MapNos
#: 121-124/141-144/151/161 fall in none of the MapKind bands, so do not assume
#: one value can serve as both.
VALID_MAPNOS = (101, 102, 121, 122, 123, 124, 141, 142, 143, 144, 151, 161)
MAPNO = _env_int("FMO_MAPNO", "101")
#: KEY: THE CONSOLE DOES NOT SHIP THE PC'S MAP SET, so the MapNo that renders on
#: the PC can be a map the PS2 has no file for -- and a MapNo with no resource
#: is a scene with no terrain. Observed live 2026-09-09: the PS2 reached the
#: world on MapNo 102 and the player got "a grey void with the NPCs in it" --
#: entities (ours, over UDP) drawing into a map that never loaded.
#:
#: WHAT IS ACTUALLY ESTABLISHED, and what is not:
#:   VERIFIED: The message is not the problem. midas.pex's 0x0153 parser (0x00305c9c)
#:      reads MapKind at payload+0x14, the 252-byte setup block at +0x28 and the
#:      88-byte block at +0x124 -- byte for byte the PC layout this file builds.
#:   VERIFIED: The PC's own MapNo 102 map file (data/AF/F57/D07.DAT) is dated
#:      2005-12-09, nine months AFTER this console build (ps2jp_050324_1531).
#:   PARTIAL: TWO readings of the console's shipped catalogue, and they DISAGREE:
#:      its resource index (hddata/hddata.pos + file.txt) puts entries at
#:      55605+{121,141,161..164} and none at 55605+{101,102,...}; its
#:      lobby-map directory analogue A5/D57 holds SIX map-sized files where the
#:      PC's data/AF/F57 holds twelve, which numbers as {101,121,141,142,143,
#:      144}. WARNING: NEITHER survived a control: the .pos model does not reproduce
#:      the PC's known id->path answers, and the PC<->PS2 path correspondence
#:      is inconsistent (deltas of 0, +-20, +-80 across directories). So the
#:      SET below is not proven -- only the INTERSECTION of the two readings is
#:      agreed, and that is {121, 141}.
#: Hence the default: 121, the one map both readings say the console has and
#: both size as a full map (1.79 MB / 2.0 MB). It is a single discriminating
#: launch, not a sweep: terrain proves the whole model, a second grey void
#: refutes "the console lacks the map" and sends the hunt to the loader.
MAPNO_PS2 = _env_int("FMO_MAPNO_PS2", "121")
#: The map set to gate a console session's Move/Change Area against, so a grant
#: cannot send it somewhere its install has no file for. Same caveat as above:
#: this is the union of the two readings, deliberately -- refusing a map the
#: console DOES have costs a menu row, granting one it does not costs a void.
PS2_MAPNOS_UNION = (101, 121, 141, 142, 143, 144, 161, 162, 163, 164)
#: VERIFIED: SETTLED 2026-09-21 FROM THE CONSOLE'S OWN MODULE (midas.pex, base
#: 0x280000) -- the three file-list derivations above are retired:
#:   0x0038b7e0  id -> path is ARITHMETIC, the PC's 0x6113DAE0 in other letters:
#:                 q, r = divmod(index, 100)
#:                 /<'A'+q//1000><(q%1000)//100>/D<q%100:02d>/F<r:02d>.BIN
#:   0x0038b950  existence = hddata.pos bands (u16 first WRAPS, count 0 ends)
#:   0x005672e0  BASE = 56137, 53577, 55625, 96665, 98713 (PC +20/+20/+20/+3120)
#: A resource walk over the console's install covers all three id spaces. Disk and hddata.pos agree
#: exactly (6/6 lobby, 211/211 battle), which is the control the old readings
#: never had:
#:   type 2 LOBBY   101, 121, 141-144      PC-only: 102, 122-124, 151, 161
#:   type 1 BATTLE  211 of the PC's 281    (PS2_TYPE1_050324 below)
#:   type 3 ZONE    every real zone row (0-10, 98-109, x00-x09/x19) is present
#: WARNING: THE COST: PCSX2 walks into a map with no file and
#: draws a void, REAL HARDWARE CRASHES loading it. So anything outside these
#: sets is substituted (lobby -> MAPNO_PS2) or refused (battle, 10:6).
#:
#:   FMO_PS2_GATE=0       de-gate: a console session is served exactly what a
#:                        PC is (the day FMO's patch data turns up)
#:   FMO_PS2_MAPNOS=a,b   the type-2 maps a console may be granted
#:   FMO_PS2_TYPE1=a-b,c  the type-1 maps a console may SORTIE to; `all`
#:                        trusts TYPE1_ON_DISK (the PC's set)
#: The build test is Session.is_ps2 (the 0x0065 version string), NOT
#: responders._peer_is_ps2() -- that one says PS2 for everybody.
PS2_GATE = (os.environ.get("FMO_PS2_GATE", "").strip() or "1") != "0"
PS2_TYPE1_050324 = (
    "0,14-16,18,24-36,38-76,99-104,107-127,150-156,170-174,176,180-188,191,"
    "193,200,203-204,221,231-244,265-272,281-282,300-317,320-322,343-347,"
    "352-356,363-365,372-375,377-383,403-404,406-414,417-420,422-423,436-439,"
    "453-454,462-464,470-473")


def _ps2_id_set(name, default):
    """A comma list of ids / a-b ranges from the environment. A typo must not
    take the service down: a bad entry is dropped and named."""
    spec = os.environ.get(name, "").strip() or default
    out = []
    for e in spec.replace(" ", "").split(","):
        lo, _, hi = e.partition("-")
        try:
            out.extend(range(int(lo, 0), int(hi or lo, 0) + 1))
        except ValueError:
            if e:
                print(f"fmo: WARNING: {name} entry {e!r} is not an id or a-b range "
                      f"-- dropped", flush=True)
    return tuple(out)


PS2_VALID_MAPNOS = _ps2_id_set("FMO_PS2_MAPNOS", "101,121,141-144")
if MAPNO_PS2 and MAPNO_PS2 not in PS2_VALID_MAPNOS:
    print(f"fmo: WARNING: FMO_MAPNO_PS2={MAPNO_PS2} is not in FMO_PS2_MAPNOS "
          f"{PS2_VALID_MAPNOS} -- the console has no file for it; using 121",
          flush=True)
    MAPNO_PS2 = 121
#: None = no type-1 gate (`all`); a tuple = the only battle maps granted.
PS2_TYPE1 = (None if os.environ.get("FMO_PS2_TYPE1", "").strip().lower() == "all"
             else _ps2_id_set("FMO_PS2_TYPE1", PS2_TYPE1_050324))
#: KEY: THE THIRD ID SPACE, gated 2026-09-21 for the same reason as the other two:
#: MapKind is resource type 3 (96665+MapKind on the console, 93545+ on the PC),
#: and the console ships **103 of the PC's 1000**. The set is read off
#: hddata.pos; it is every id SE actually authored a zone for (the tutorial
#: props 0-10, the nation scripts 98-109, and each zone band x00..x09, 500-519,
#: 700-719), so nothing we serve today falls outside it -- this is a backstop,
#: not a behaviour change.
#: WARNING: Only bites when the client USES our MapKind: with FMO_0153_F18=1
#: `script_id_for` substitutes the nation script (98/99, both present) and
#: throws ours away -- see the FIELD_18 note. A missing type-3 is the same
#: zero-length-resource crash as a missing map (0x611250A2).
PS2_VALID_MAPKINDS = _ps2_id_set(
    "FMO_PS2_MAPKINDS",
    "0-10,98-109,200-209,300-309,400-409,500-519,600-609,700-719")
#: What a console session falls back to when its zone has no pack. 200 is the
#: O.C.U. occupation band -- present on the console and FMO_MAPKIND's own
#: default -- but this should never fire: every band we grant is in the set.
MAPKIND_PS2 = _env_int("FMO_MAPKIND_PS2", "200")
#: Same shape as FMO_MAPKIND_SWEEP: one value per GAME connection, logged.
MAPNO_SWEEP = [int(x, 0) for x in
               os.environ.get("FMO_MAPNO_SWEEP", "").replace(" ", "").split(",")
               if x]
CLIENT_SCRIPT_NO = _env_int("FMO_CLIENT_SCRIPT_NO", "1")
#: WARNING: A SECOND SCRIPT ID, and we have sent 1 forever. SE's own debug print names
#: it beside MapNo ("MapNo=%u ClientScriptNo=%u MusicNo=%u SeNo=%u"), so it is
#: distinct from MapKind -- which was proven on 2026-08-22 NOT to drive the
#: camera: script 200 loaded (verified in the client's own resource table, a
#: different file of the same padded 459,520 bytes) and the view was identical.
#:
#: WARNING: Unlike MapKind this rides INSIDE the 252-byte setup block, and the move
#: path copies that block wholesale to lobby+0x6B3A -- so it transfers on a
#: Move and can be swept at one press per value. MapKind cannot: 0x61190BC3
#: passes the client's own lobby+0x4F06 and only the LOGIN consumer stores ours.
CLIENT_SCRIPT_SWEEP = [int(x, 0) for x in
                       os.environ.get("FMO_CLIENT_SCRIPT_SWEEP", "")
                       .replace(" ", "").split(",") if x]


def next_client_script():
    """The ClientScriptNo to send. Walks FMO_CLIENT_SCRIPT_SWEEP if set."""
    if not CLIENT_SCRIPT_SWEEP:
        return CLIENT_SCRIPT_NO
    return CLIENT_SCRIPT_SWEEP[_sweep_n[0] % len(CLIENT_SCRIPT_SWEEP)]
MUSIC_NO = _env_int("FMO_MUSIC_NO", "1")
SE_NO = _env_int("FMO_SE_NO", "1")
#: globals+0x1AC. 1 because that is the value 0x61005105 and 0x61005547 compare
#: for. KEY: DECODED 2026-08-26 (static): it is the ZONE KIND -- 0x611D8F83 and
#: 0x611DE467 both switch on it to pick systext group 35 ids 50-55: 0 Lobby,
#: 1 Room, 2 Briefing Room, 3 Room B, 4 Room C, >4 Hangar. The `== 1` compares
#: are the ROOM-only nation script substitution (0x61004AE0). A category-2 Move
#: is granted with MOVE_KIND_BRIEFING instead -- see the Move flow section.
FIELD_18 = _env_int("FMO_0153_F18", "1")
FIELD_1C = _env_int("FMO_0153_F1C", "0")
#: PilotPos, four floats. WARNING: Zeros are a GUESS that has never been varied, and
#: the world origin is a plausible place to render nothing from.
PILOTPOS = tuple(float(x) for x in
                 (os.environ.get("FMO_0153_PILOTPOS", "").strip() or "0,0,0,0").split(","))
if len(PILOTPOS) != 4:
    raise SystemExit("FMO_0153_PILOTPOS wants four comma-separated floats")

#: WARNING: PILOTPOS IS WHERE THE LOCAL PLAYER IS PUT. Decoded 2026-08-22 from
#: `0x61003340`, the local-player setup (state 0 of the second scene machine at
#: `0x61007D00`, called at `0x61007D39`). It:
#:
#:     * reads the self unit's 456-byte body back out of `unit+0x147`
#:     * overwrites `body+0x08 = 1` and `body+0x0C = 0x82080010`
#:     * prints SE's own "PilotPos %3.3f %3.3f %3.3f %3.3f" (`0x61003425`)
#:     * **overwrites `body+0x40..0x4C` with PilotPos[0..3]**
#:     * re-creates the unit through `0x611EADC0` -- the SAME creator the cmd-7
#:       POP uses -- and keeps the resulting visual as the player's
#:
#: So the four floats we send in the setup block ARE the player's spawn point,
#: and `fmocrash --live` reporting the unit at `(0.00, 5.00, 0.00, 0.00)` is
#: this write, not our POP's own position field.
#:
#: WARNING: Which means the default is not neutral, exactly as the note above feared:
#: at `(0,5,0)` the player stands at the MAP ORIGIN. On 2026-08-22 in MapNo 102
#: the player saw the room from a fixed odd viewpoint with no visible
#: character and no movement -- consistent with standing outside the room
#: rather than with a detached camera.
#:
#: FMO_0153_PILOTPOS_SWEEP walks candidates, one per 0x0153, as
#: `"0,5,0,0; 100,5,100,0; -100,5,-100,0"`. WARNING: **The point of the sweep is that
#: Move re-enters the scene**, so one launch can try several positions: each
#: Move -> Change Room takes the next candidate. Without it a guess costs a
#: relaunch.
PILOTPOS_SWEEP = []
for _q in os.environ.get("FMO_0153_PILOTPOS_SWEEP", "").split(";"):
    _q = _q.strip()
    if not _q:
        continue
    _v = tuple(float(x) for x in _q.split(","))
    if len(_v) != 4:
        raise SystemExit(f"FMO_0153_PILOTPOS_SWEEP entry {_q!r} wants four "
                         f"comma-separated floats; entries are ;-separated")
    PILOTPOS_SWEEP.append(_v)


#: The MapKind bands, transcribed from the five predicate functions listed in the
#: 0x0150 note. This is the client's own arithmetic, so it is the one thing here
#: that can be asserted rather than probed -- and its real job is to make an
#: out-of-band MapKind fail in the selftest instead of on the wire.
MAPKIND_BANDS = ((100, 109), (200, 209), (300, 309),
                 (400, 409), (500, 519), (600, 607))


def in_mapkind_band(kind):
    """True if the client has a compare that accepts this MapKind."""
    return any(lo <= kind <= hi for lo, hi in MAPKIND_BANDS)


def script_id_for(mapkind, nation):
    """The type-3 SCRIPT id the client will actually register, per 0x61005100.

    Not a guess: this is that function's arithmetic transcribed. Kept next to
    the band table so a log line can say what the client will DO with the
    MapKind we send, rather than only what we sent.
    """
    if FIELD_18 == 1 and not (0x258 <= mapkind <= 0x25F):
        if nation == 1:
            return 0x62
        if nation == 2:
            return 0x63
    return mapkind


#: KEY: FMO_NATION_PER_CHARACTER (default ON, 2026-09-08): the pilot's OWN
#: nation -- creation +0x28 via character_nation() -- feeds every place the
#: client reads a nation from, and the global knobs become the FALLBACK for a
#: record that has none:
#:   * 0x014A payload+0x30 -> lobby+0x8B4   (the SCRIPT nation: LobbyEntry's
#:     cast switch, the 98/99 script substitution, the Change Area walker)
#:   * the self-POP body+0x7C -> entity+0x1C3 (the 0xE060 cast gate)
#:   * the zone kind in every 0x0153 grant (OCU 100s/200s, USN 300s/400s --
#:     the LEV table AI/F08/D15.DAT binds nation to band, faction_mapkind())
#:   * which NPC roster is popped (roster_for())
#: Before this every one of those was a GLOBAL knob, and on prod they did not
#: even agree with each other (FMO_NATION=0, FMO_STATUS_NATION=1,
#: FMO_UDP_POP_NATION=1): a U.S.N. pilot was granted the O.C.U. controlled
#: zone, told by 0x014A that he was O.C.U., and met Kwangsu Son and Henry
#: Viduka at the counters. That is the live report "the game seems very confused
#: about who is OCU and who is USN" (2026-09-08). Set to 0 to get the old
#: behaviour back exactly.
NATION_PER_CHARACTER = (os.environ.get("FMO_NATION_PER_CHARACTER", "")
                        .strip() or "1") != "0"


#: KEY: THE CLIENT'S OWN HOME ZONE PER KIND, per nation -- read out of the
#: image at 0x613860D0 (2026-09-08): the Change Area filler 0x61010C2B walks
#: `{u32 ocu, u32 usn}[kind]` and puts the cursor on that row when the list is
#: not the pilot's current kind. Kind 1 (O.C.U. HQ) -> 100 for both, kind 2
#: (O.C.U. occupation) -> 200 / 207, kind 3 -> 300 / 300, kind 4 (U.S.N.
#: occupation) -> 407 / 400, kind 5 -> 509 / 509 (FZ-10 Freedom City, the live
#: warzone), kind 6+ -> 600. So SE's default occupation lobbies are exactly
#: 200 (O.C.U.) and 400 (U.S.N.), which is what FMO_MAPKIND=200 + the
#: faction band gives; and 207 / 407 ("OC-Area 08", flagged for BOTH nations
#: in D83) are the contested areas an enemy pilot is pointed at.
ZONE_HOME_BY_KIND = {1: (100, 100), 2: (200, 207), 3: (300, 300),
                     4: (407, 400), 5: (509, 509), 6: (600, 600)}


def faction_mapkind(mapkind, nation):
    """The zone kind a pilot of `nation` should be granted when the knob says
    `mapkind`: the same index in THEIR faction's band.

    The LEV table (AI/F08/D15.DAT) binds bands to nations -- 100..109 and
    200..209 are nation 1 (O.C.U., controlled / occupied), 300..309 and
    400..409 are nation 2 (U.S.N.); 500+ and 600+ are shared. So a U.S.N. pilot
    asked into 100 belongs in 300, an O.C.U. pilot asked into 400 belongs in
    200, and anything outside those four bands is left alone. Pure."""
    if nation not in (1, 2):
        return mapkind
    band, idx = divmod(mapkind, 100)
    if nation == 1 and band in (3, 4):
        band -= 2
    elif nation == 2 and band in (1, 2):
        band += 2
    return band * 100 + idx


def nation_for_session(char, fallback, fallback_src):
    """(nation, source) for one pilot: the character's own nation when
    FMO_NATION_PER_CHARACTER is on and the record has one, else `fallback`."""
    if NATION_PER_CHARACTER and char:
        n, src = popnation.character_nation(char)
        if n in (1, 2):
            return n, "character store: " + src
    return fallback, fallback_src


def script_nation(char=None):
    """(nation, source): the byte the client holds in lobby+0x8B4 when
    0x61005100 picks the scene SCRIPT (script_id_for).

    KEY: 2026-09-11: takes the CHARACTER. lobby+0x8B4 is written by the 0x014A
    this pilot was served, and that byte is per character since
    FMO_NATION_PER_CHARACTER (nation_for_session) -- so anything that has to
    AGREE with lobby+0x8B4 (the squadron table's +0x09 "Cty", the insignia
    catalogue's nation filter) must resolve the same way. Every caller that
    knows the pilot passes it; the global knob is only the fallback for a
    session with no character on file, which is exactly what 0x014A does.

    WARNING: CORRECTED 2026-09-05. Until then this was `served_nation(roster)`,
    which returned the 0x012F list entry's +0x26 -- a byte the client never
    copies into lobby+0x8B4. The ONLY writer of lobby+0x8B4 is the 0x014A
    status copy (payload+0x008 -> lobby+0x88C, 1,660 B; +0x8B4 = S14A_NATION
    = payload+0x30), i.e. FMO_STATUS_NATION -- proven live 2026-09-04, when
    FMO_STATUS_NATION=1 on its own made the client register script 98 and
    hang on that script's 0x0159. And the roster key the old code read was
    the SWAPPED `nation` (= GENDER, the swapped-key hazard). So the log line the
    note says to read before believing any MapKind result could name the
    wrong script, from the wrong source. With FMO_START_STATUS off no 0x014A
    goes out and the byte stays at its zero-init (0x6117A2F6)."""
    if not status.SERVE_START_STATUS:
        return 0, "FMO_START_STATUS=0, no 0x014A sent, lobby+0x8B4 stays 0"
    return nation_for_session(
        char, status.STATUS_NATION,
        "FMO_STATUS_NATION (0x014A payload+0x30 -> lobby+0x8B4)")


def describe_script_choice(mapkind, nation=None, nation_src=None):
    """THE line to read before believing any MapKind result: the type-3
    SCRIPT id 0x61005100 will register for a grant carrying `mapkind`, and
    why. Emitted at every proven scene-entry grant (world entry, Move),
    because each one re-registers the scene's resources. `nation` is the byte
    this session actually put in 0x014A +0x30 (per character since
    2026-09-08); None falls back to the global knob."""
    n, src = (nation, nation_src) if nation is not None else script_nation()
    sid = script_id_for(mapkind, n)
    return (f"-> the client will register SCRIPT id {sid} for this scene "
            f"(nation {n}: {src}; +0x18={FIELD_18}): "
            + (f"our MapKind {mapkind} is substituted away, per 0x61005100"
               if sid != mapkind else
               f"our MapKind is used AS IS -- the substitution did not fire, "
               f"so this run cannot tell MapKind values apart"))


def setup_block(mapno=None, pilotpos=None, csn=None, self_id=None):
    """The 252 bytes at 0x0153 +0x28, laid out as lobby+0x6B3A is read.

    Only the named fields are written; the rest stays zero, because writing
    plausible-looking bytes into offsets nobody has traced would make a later
    failure impossible to attribute."""
    b = bytearray(SETUP_LEN)
    struct.pack_into("<III", b, SU_MAPNO,
                     MAPNO if mapno is None else mapno,
                     CLIENT_SCRIPT_NO if csn is None else csn, MUSIC_NO)
    struct.pack_into("<I", b, SU_SENO, SE_NO)
    # PilotPos: four floats, named by SE's own printf at 0x61003425. Origin by
    # default -- the map's spawn is the server's to choose and we have no map
    # geometry -- but WARNING: **an untested zero is not a neutral value**: a pilot at
    # the world origin may be under the terrain or outside the map entirely,
    # which renders as exactly the black screen we have. FMO_0153_PILOTPOS
    # ("x,y,z,w") makes it movable without a rebuild, because the alternative is
    # a code change per guess.
    struct.pack_into("<ffff", b, SU_PILOTPOS,
                     *(PILOTPOS if pilotpos is None else pilotpos))
    # WARNING: THE EIGHT UNIT IDS. `0x61003120` feeds each to a lookup in the entity
    # map and SKIPS A MISS SILENTLY, so a zero here spawns nothing -- measured
    # live 2026-08-21, map built with 100 buckets and 0 keys.
    #
    # WARNING: They are only half of the pair. An id here does nothing unless a cmd-7
    # POP has already put that same id IN the map, and a POP is only visible in
    # the scene if its id is also here. FMO_UDP_POP defaults this list to the
    # unit it pops, so the two cannot drift apart by accident -- which is the
    # single most likely way to get a false negative out of this experiment.
    # WIRE IDS: this pilot's own unit is the character id it selected
    # (CHAR_WIRE_BASE), so it replaces the fixed POP id at the head of the list.
    ids = list(room.SETUP_UNIT_IDS[:8])
    if self_id:
        ids = [self_id] + [u for u in ids if u not in (self_id,) + (
            (popsweep.POP[0],) if popsweep.POP else ())]
    for i, uid in enumerate(ids[:8]):
        struct.pack_into("<I", b, SU_UNITS + i * 4, uid & 0xFFFFFFFF)
    return bytes(b)


#: How many GAME connections have asked for a 0x0153. Only used to step the
#: sweep, so a value is attributable to a run in the log.
_sweep_n = [0]


def next_mapkind():
    """The MapKind to send. Walks FMO_MAPKIND_SWEEP if one is set."""
    if not MAPKIND_SWEEP:
        return MAPKIND
    return MAPKIND_SWEEP[_sweep_n[0] % len(MAPKIND_SWEEP)]


def next_mapno():
    """The MapNo to send. Walks FMO_MAPNO_SWEEP if one is set."""
    if not MAPNO_SWEEP:
        return MAPNO
    return MAPNO_SWEEP[_sweep_n[0] % len(MAPNO_SWEEP)]


def next_pilotpos():
    """The PilotPos to send. Walks FMO_0153_PILOTPOS_SWEEP if one is set."""
    if not PILOTPOS_SWEEP:
        return PILOTPOS
    return PILOTPOS_SWEEP[_sweep_n[0] % len(PILOTPOS_SWEEP)]


def advance_sweep():
    """Step the sweeps, once per 0x0153.

    Kept separate from the getters on purpose: with the step inside a getter,
    asking for MapKind and MapNo in the same reply would advance twice and
    silently skip a candidate -- a sweep that lies about what it tried is worse
    than no sweep."""
    _sweep_n[0] += 1


def reply_0153(fill=None, mapkind=None, mapno=None, pilotpos=None, csn=None,
               field18=None, host=None, self_id=None):
    """The 380-byte 0x0153 payload. `fill=False` is the all-zero control.

    `field18` overrides the +0x18 zone kind (globals+0x1AC: 0 lobby, 1 room,
    2 briefing room, 3/4 room B/C, >4 hangar); None keeps FIELD_18."""
    if not (FILL_0153 if fill is None else fill):
        return bytes(REPLY_0153_LEN)
    b = bytearray(REPLY_0153_LEN)
    _ep = addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint
    # `host` is the per-client address (host_for); None keeps the global one,
    # which is what every selftest and tool caller wants.
    b[R153_ENDPOINT:R153_ENDPOINT + addressing.ENDPOINT_LEN] = _ep(host or addressing.BATTLE_HOST,
                                                             addressing.BATTLE_PORT)
    struct.pack_into("<H", b, R153_MAPKIND,
                     (MAPKIND if mapkind is None else mapkind) & 0xFFFF)
    struct.pack_into("<II", b, R153_FIELD_18,
                     (FIELD_18 if field18 is None else field18) & 0xFFFFFFFF,
                     FIELD_1C)
    b[R153_SETUP:R153_SETUP + SETUP_LEN] = setup_block(mapno, pilotpos, csn,
                                                       self_id=self_id)
    # The 88 bytes at +0x124 stay zero -- the structure is unidentified and it is
    # the same one a 0x0155 entry carries, so a guess here would be a guess twice.
    return bytes(b)


# Called at run time only; imported last so that import cycles resolve.
from . import addressing, popnation, popsweep, room, status  # noqa: E402
