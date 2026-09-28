"""The mission block (0x01C0 -> 0x01C1): map, battle area, time limit and leader of a mission."""
import math
import os
import struct
from .knobs import _env_int


# --------------------------------------------------------------------------- #
# THE MISSION BLOCK -- 0x01C0 -> 0x01C1 (mission-block worker, 2026-08-26)
# --------------------------------------------------------------------------- #
# 0x61173140 is the lobby-API parse for entry 21 (req 0x01C0, reply 0x01C1). It
# copies 0x352 dwords from payload+0x18 into lobby+0x5C7E via 0x61175600. The
# LOBAPI table has answered it with 3,428 zeros since it was built; the header
# is named by SE's own debug print at 0x611D3221 (strings 'MapNo=%u',
# 'LeaderID=%x', 'BGCostMax=%x %x', 'TotalBgCostTbl=%x %x',
# 'BattleTicketTbl=%x %x' at 0x6134337C / 0x61343274 / 0x61343264 / 0x6134324C
# / 0x61343234).
#
# KEY: WHO READS IT, statically (dispfind 0x5C7E and 0x5CEE, 2026-08-26):
#   * MapNo (+0x000): 0x61004C99 registers `type=1, id=[lobby+0x5C7E]` at
#     0x61004CC7 -- and that function (0x61004C30) is the PHASE-0 ARM of the
#     scene whose vtable is 0x61328A20 (ctor 0x61002C80, built by 0x610049A0,
#     which the dispatcher labels `[globals+0x24] = 4`). Scene 4 is entered by
#     0x61006270, whose callers are the SEVEN other block copiers: the reply
#     0x013A to request 0x0139 (kycli_lobmain 0x6117BB50 -> copy 0x6117BC95 ->
#     scene 4 at 0x6117BCD6; and the poller 0x61184710 -> UI event 0x106E ->
#     0x610FB842 -> copy 0x610FB8A6 -> scene 4 at 0x610FB8B6) and SE's own
#     "Create Battle Map" debug dialog (0x6118010D/0x61186887). So the type-1
#     map is loaded on the SORTIE (0x0139 -> 0x013A carries its own copy of the
#     block at reply +0x2C/+0x48), NOT on 0x01C1: a MapNo served here sits in
#     the lobby unread until scene 4 is entered. 0x611937E0 (the map picker
#     resource 0xC5C5 + MapNo/51, called from 0x61198760 inside the same
#     phase-0 arm) and 0x611612B8 (a u16 copy into an outbound struct) are the
#     only other readers. fmo.py does not answer 0x0139 at all.
#   * LeaderID (+0x070): 0x610DC6B8 only (plus the debug print). It is a
#     script-VM opcode (0x610DC670): pops a unit INDEX, 0x610D9330 walks the
#     entity map (0x613C1698+0x20) to the n-th entity, `eax = [entity+0x24]`
#     (the pFmoUnit attached at 0x611EB3B9), `edx = [unit+8]`, compared with
#     the block's LeaderID; equal -> pushes 1.0f. WARNING: The WRITER of [unit+8] was
#     NOT found: the ctor chain 0x611F9570/0x611F4E20 -> 0x611F29E0 ->
#     0x6105EC40 -> 0x611FB110 zeroes it and never sets it, and no
#     `[unit+8] = [entity+0x10]` site exists. It is a per-unit u32 that
#     0x611F0F00 serialises into a gameplay record (+0x20..+0x23) and that
#     0x61002070 looks up in a 4-entry {u32,u32} table at globals+0x218. So it
#     is NOT the 64-bit member id and NOT read from the character record; the
#     best candidate is the unit's own UnitID (the POP hash key, +0x04), which
#     is why FMO_MISSION_LEADER=self serves POP[0]. A candidate, stated as one.
#   * BGCostMax/TotalBgCost/BattleTicket (+0x074..+0x07B): 0x610CB1BB, in the
#     per-tick method 0x610CAFD0 (vtable 0x61332F9C slot 5), which runs only
#     when `[globals+0x1C4] == 6` and the tick `+0x1C0 >= 1800` -- i.e. in the
#     settled world scene -- and also reads NpcMax (+0x008, `0 -> 20` cap),
#     +0x05C, +0x060 and +0x07C.
#
# WARNING: A type-1 id with no file crashes the client at 0x611250A2 (double
# relocation). MB_MAPNO is therefore gated on TYPE1_ON_DISK, the 281 ids
# `fmofile.py`'s formula finds under the install root (scanned 2026-08-26,
# base 53557 -> data\AF\F35\D57.DAT is id 0, which IS on disk at 384,480 B --
# so today's all-zero block already registers a type-1 resource, id 0, on
# every scene-4 entry; the live bar is a type-1 allocation with a DIFFERENT
# id/size, not merely "a type-1 allocation").
MSG_MISSION_REQ = 0x01C0
MSG_MISSION_REPLY = 0x01C1
REPLY_01C1_LEN = 3428                  # = LOBAPI[0x01C0][1]; 0x18 + 3400 + u32 at +0xD60
M1C1_BLOCK = 0x18                      # payload offset of the block (0x611731BA)
M1C1_BLOCK_LEN = 0x352 * 4             # 3,400 bytes -> lobby+0x5C7E (0x61175615)
M1C1_TAIL_U32 = 0xD60                  # the extra u32 the parse reads past the block
#: Block-relative offsets, named by the debug print at 0x611D3221.
MB_MAPNO = 0x000                       # u32, a TYPE-1 map id (base 53557)
MB_CLIENTSCRIPTNO = 0x004              # u32
MB_NPCMAX = 0x008                      # u32, a CAP (0x610CB108: 0 -> 20), not a spawner
MB_MUSICNO = 0x00C                     # u32
MB_MAPINPLAYERNUM = 0x010              # u32
MB_NPCSCRIPTNUM = 0x014                # 4 x u8, read only by the print
MB_FLAGS = 0x018                       # bit 0 = IsNewBatteMap (SE's spelling)
MB_ARMYDIRECT = 0x01C                  # 4 x f32
MB_RECTGROUPCNT = 0x02C                # u32
MB_RECTDATACNT = 0x030                 # u32
MB_START_GAMETIME = 0x048              # u32
#: KEY: THE BATTLE START, Unix SECONDS -> lobby+0x5CC6 (static 2026-09-27). The
#: client records the Battle Review itself (kycli_btlreview.cpp, recorder
#: 0x613C0AAC -> /btlreview/<token>/brdata000.dat), stamping every 5 s frame
#: with clock_ms - start*1000 (0x61160F70, clamped at 0). We served 0, so each
#: frame carried the low 32 bits of the Unix-ms clock -- NEGATIVE from about
#: 09-09 to 10-02, i.e. every frame 0 -- and the reader (0x61161480), whose
#: bounds are the first and last frame times, stopped on its first tick (live
#: playback: it stops at once and the points do not move). A constant 0 also
#: made every battle's header match the old file (state 4 -> 8), so battles
#: APPENDED to one file. Stamped on every sortie block (FMO_BATTLE_START_TIME,
#: default on); never later than the real start or frames clamp back to 0.
#: Also feeds the HUD elapsed clock (0x61164600) and, once nonzero, can make
#: the client upload a text log as 0x01A5 (0x6115FAE3 -> 0x61174510).
BATTLE_START_TIME = os.environ.get("FMO_BATTLE_START_TIME", "1") not in ("0", "")
MB_LD = 0x05A                          # u8, 'ld=%u00ms'
MB_PENALTY = 0x05B                     # u8, 'penalty=%u000ms'
#: VERIFIED:KEY: THE BATTLE TIME LIMIT, decoded 2026-09-08 live. NOT in SE's own debug
#: dump (which stops at Start GameTime), but 0x612381B9 reads it as the mission
#: duration in SECONDS:
#:     612381a4  mov edx, [globals+0x194] ; +0x5c7e -> the block
#:     612381b2  mov edx, 0x14            ; a 20s fallback
#:     612381b9  mov edx, [block+0x4c]    ; THE LIMIT
#:     612381c2  lea ecx, [edx-0x12c]     ; limit-300 -> "5 MINUTES LEFT"
#:     612381e1  lea ecx, [edx-0x3c]      ; limit-60  -> "1 MINUTE LEFT"
#: WARNING: WE SERVE ZERO, and a battle whose limit is 0 is already over. Live
#: 2026-09-08 the player was withdrawn seconds into every battle regardless
#: of the on-screen countdown -- including the SPECTATOR sessions, before we
#: had a unit at all, which is why it never looked like a unit problem.
MB_TIMELIMIT = 0x04C                   # u32 SECONDS -> lobby+0x5CCA

#: WARNING: SUPERSEDED 2026-09-08 (same day): this said the rect list WAS the battle
#: area and that serving none of them is why "OUT OF BATTLE AREA" shows at
#: every coordinate. Serving one rect covering the whole map changed nothing.
#: The boundary is MB_AREA_* below; the geometry decode here is still correct,
#: it just describes the objective zones.
#:     610f1c51  cmp  eax, [lobby+0x5cae]     ; the index vs RectDataCnt
#:     610f1c5d  imul eax, eax, 0x1c          ; STRIDE 28
#:     610f1c61  lea  esi, [eax + ecx+0x6096] ; the ARRAY -> block +0x418
#: and the rect's own geometry, from the same decoder:
#:     610f1d05  fld [esi+0x10] / fadd [esi+0x04]   -> maxX = minX + sizeX
#:     610f1cf5  fld [esi+0x18] / fadd [esi+0x0c]   -> maxZ = minZ + sizeZ
#: so a rect is ORIGIN + SIZE, axis-aligned.
#: WARNING: +0x00..+0x03 are undecoded flag bytes (0x610F1C68 reads bit tests out of
#: +0x02) and +0x08 / +0x14 are the untested Y pair. We serve them ZERO, which
#: is a guess; the geometry above is not.
#: KEY: THE PLAY BOUNDARY -- **NOT** the rect list. Decoded 2026-09-08 by
#: opening the countdown itself, and it RETRACTS the two readings that framed
#: this problem (see the WARNING: notes on FMO_MISSION_AREA below).
#:
#: The boundary is FOUR SIGNED 16-BIT INTEGERS at block +0xC48, and
#: 0x6111DF00 is their ONLY reader in the whole image (dispfind 0xC40..0xC60):
#:     6111df01  mov   eax, [0x613ae664]
#:     6111df06  mov   ecx, [eax+0x194]
#:     6111df0c  add   ecx, 0x5c7e            ; THE MISSION BLOCK
#:     6111df4d  movsx edx, word [ecx+0xc4e]  -> box+0x18 = maxZ
#:     6111df57  movsx edx, word [ecx+0xc4c]  -> box+0x10 = maxX
#:     6111df7f  movsx edi, word [ecx+0xc4a]  -> box+0x08 = minZ
#:     6111df86  movsx ecx, word [ecx+0xc48]  -> box+0x00 = minX
#: `movsx` -> `fild` -> `fstp`: raw integers straight to float, NO scaling, so
#: these are WORLD UNITS, the same units as the unit position and the same
#: units fmomap.py's extent reports (map 418 = 0..128, span checks out).
#: box+0x04/+0x14 are forced 0 and box+0x0C/+0x1C forced 1.0f -- a homogeneous
#: xz pair, Y is not part of the test.
#:
#: WARNING: WE SERVE THIS AS ZEROS, so the box is the single POINT (0,0)--(0,0) and
#: the containment test 0x6111E5E0 (`minX<=x<=maxX && minZ<=z<=maxZ`, X and Z
#: only) is false EVERYWHERE, the map centre included. That is the whole of
#: "OUT OF BATTLE AREA", and the whole of the withdraw -- see 0x610CAC50.
MB_AREA_MINX = 0xC48                   # s16 -> lobby+0x68C6
#: KEY: THE LOCAL BATTLE SIDE (static 2026-09-27): u8 -> lobby+0x68D3, read by
#: 0x61175A90 (the enemy side is the other of {1, 2}, 0x61175AC0). The
#: debrief (0x61192780), radar and join banner compare each unit+0x80 (POP
#: body+0x7C, the nation) against it. We served 0, so neither side matched
#: and the Battle Review drew the enemy squad as FRIENDLY.
MB_BATTLE_SIDE = 0xC55
MB_AREA_MINZ = 0xC4A                   # s16 -> lobby+0x68C8
MB_AREA_MAXX = 0xC4C                   # s16 -> lobby+0x68CA
MB_AREA_MAXZ = 0xC4E                   # s16 -> lobby+0x68CC
MB_AREA_LIMIT = 0x7FFF                 # the field is int16; ±32767 is the wire

#: PARTIAL:KEY: RectData is the OBJECTIVE / CAPTURE zones, not the boundary. Kept
#: because serving one rect is what made the client start emitting cmd 128
#: (0x6105DB3F / 0x61060832 `jbe`-skip the emit while +0x30 is zero), i.e. it
#: gives the mission an objective -- and because the in-battle HUD strings for
#: it are 戦域Aの制圧開始 / 戦域Aの制圧まで30秒, "capture of combat area A",
#: areas A..E. It has never had anything to do with the play boundary.
MB_RECTDATA = 0x418                    # rect array -> lobby+0x6096
MB_RECT_STRIDE = 0x1C                  # 28 B (0x610F1C5D `imul eax,eax,0x1c`)
MB_RECT_MINX, MB_RECT_MINZ = 0x04, 0x0C
MB_RECT_SIZEX, MB_RECT_SIZEZ = 0x10, 0x18
MB_LEADERID = 0x070                    # u32 -> lobby+0x5CEE (0x610DC6B8)
MB_BGCOSTMAX = 0x074                   # 2 x u8 -> lobby+0x5CF2/0x5CF3 (0x610CB1BB)
MB_TOTALBGCOST = 0x078                 # 2 x u8
MB_BATTLETICKET = 0x07A                # 2 x u8

#: WARNING: OFF BY DEFAULT. With it off the generic LOBAPI arm answers exactly as it
#: always has (3,428 zeros); on, reply_01c1() answers instead, and with every
#: field knob at its default the two are BYTE-IDENTICAL (asserted in the
#: selftest). One field per run, a knob per field, the source in the log line
#: (served-zeros-launder-into-choices). NOT LIVE-TESTED.
SERVE_MISSION_BLOCK = (os.environ.get("FMO_MISSION_BLOCK", "").strip() or "0") != "0"
#: LeaderID: '' = leave zero; 'self' = the unit id we POP for the player
#: (POP[0], = SETUP_UNIT_IDS[0]); or any integer, e.g. '0x820C1080'.
MISSION_LEADER = os.environ.get("FMO_MISSION_LEADER", "").strip()
#: MapNo: 0 = leave zero; otherwise MUST be in TYPE1_ON_DISK or it is refused.
MISSION_MAPNO = _env_int("FMO_MISSION_MAPNO", "0")
#: The three byte pairs, each 'a,b' or '' (leave zero).
MISSION_BGCOSTMAX = os.environ.get("FMO_MISSION_BGCOSTMAX", "").strip()
MISSION_TOTALBGCOST = os.environ.get("FMO_MISSION_TOTALBGCOST", "").strip()
MISSION_BATTLETICKET = os.environ.get("FMO_MISSION_BATTLETICKET", "").strip()


def _u8_pair(spec):
    """'a,b' -> (a, b) or None for ''. Anything else raises ValueError."""
    if not spec:
        return None
    a, b = (int(x, 0) for x in spec.split(","))
    return a & 0xFF, b & 0xFF


def mission_leader_id(leader=None):
    """The LeaderID to serve: None (leave zero), or an int with its source."""
    spec = MISSION_LEADER if leader is None else leader
    if spec in ("", None, 0):
        return None, "unset"
    if isinstance(spec, str) and spec.lower() == "self":
        if not popsweep.POP:
            return None, "self, but FMO_UDP_POP is off -- no unit id to name"
        return popsweep.POP[0] & 0xFFFFFFFF, "self = POP[0], the unit id we pop"
    return int(spec, 0) & 0xFFFFFFFF, "FMO_MISSION_LEADER literal"


#: VERIFIED:KEY: FMO_MISSION_TIME -- the battle time limit in SECONDS (block +0x4C).
#: Unset = the old all-zero behaviour, i.e. a battle that is already over.
#: 1800 (30 min) is a normal-looking mission length; the value is ours to pick
#: because nothing in the client bounds it -- only the "5 MINUTES LEFT" /
#: "1 MINUTE LEFT" banners key off it.
MISSION_TIME = _env_int("FMO_MISSION_TIME", "0")

#: VERIFIED:KEY: FMO_MISSION_AREA -- the battle area, as ONE rectangle:
#: `<minX>,<minZ>,<sizeX>,<sizeZ>`, or `map` for the whole of a 0..128 map
#: (which is what fmomap.py reports for map 418: extent 0..128, span checks).
#:
#: It writes TWO independent things from the one spec, and only the second is
#: the play boundary:
#:   * RectDataCnt (+0x30) = 1 and rect 0 at +0x418, f32 ORIGIN+SIZE -- the
#:     OBJECTIVE zone. This is what made the client start emitting cmd 128.
#:   * MB_AREA_* at +0xC48, FOUR s16 min/max -- **THE PLAY BOUNDARY**, the one
#:     thing 0x6111DF00 reads and 0x6111E5E0 tests the player against.
#:
#: WARNING: TWO RETRACTIONS live here, both from 2026-09-08, both of which cost a
#: round: (a) the rect list was served as the boundary and changed nothing;
#: (b) `[hud+0x3C]` was called "the flag that makes the countdown read OUT OF
#: BATTLE AREA" -- it is a BLINK, toggled every 10 frames at 0x61237B80 and
#: shared with LOW HP / ENEMY EMP / every other flashing banner. Neither is
#: the boundary. The boundary was never read until 0x610CAC50 was opened.
#: WARNING: ONE rect only. The block has room for many and SE's own missions surely
#: use several (RectGroupCnt at +0x2C groups them, and its only reader in the
#: image is the debug print, so grouping is UNDECODED). One box covering the
#: map is a way to be inside the area, not a model of SE's data.
#: VERIFIED:KEY: FMO_BATTLE_BOUNDS -- the PLAY BOUNDARY on its own, as
#: `<minX>,<minZ>,<maxX>,<maxZ>` (min/max, because that is literally what the
#: four s16 at block +0xC48 are). Unset = derived from FMO_MISSION_AREA, which
#: is what shipped first.
#:
#: WHY IT IS SEPARATE. The boundary and the objective rect are different
#: things and they want different sizes: the boundary should be the whole
#: battlefield, the objective a capture zone inside it. Deriving both from one
#: spec meant widening the play area also made the capture zone map-sized --
#: and the client really does capture it (`cmd 128` tag 0x1403 counts 10..80
#: while the pilot stands in the rect).
#:
#: VERIFIED: MEASURED, NOT GUESSED. `fmocrash.py --live` in map 267, 2026-09-08:
#: the client's own map bounding box ([[resmgr+0x224]], the box 0x6111E560
#: tests before indexing a terrain cell) is **x -2048..2048, z -2048..2048**,
#: y 0..256. Our first box, 0..128, covered **3.1%** of it -- the live
#: report "the safe area is very small".
#:
#: WARNING: AND IT RETRACTS fmomap.py's "extent". That triple (0..128 for map 418,
#: 0..256 for 267) is the map's **HEIGHT**: the live box's maxY is 256 for
#: map 267, exactly what fmomap reports, while X and Z are +/-2048. So the
#: extent was never the horizontal size, which is why it did not scale with
#: file size (418 is 12.4 MB at 128, 232 is 3.7 MB at 384).
#:
#: WARNING: ONE MAP MEASURED. Whether +/-2048 is a fixed world grid for every FMO map
#: or per-map data is NOT established -- the box is assembled at load time and
#: is not a contiguous pattern in the .DAT, so it cannot be read off disk with
#: what we have. `--live` prints the map's bounds and what fraction of them the
#: battle area covers, so a map that disagrees announces itself.
BATTLE_BOUNDS_SPEC = os.environ.get("FMO_BATTLE_BOUNDS", "").strip()
if BATTLE_BOUNDS_SPEC:
    try:
        _bb = tuple(int(round(float(x))) for x in BATTLE_BOUNDS_SPEC.split(","))
    except ValueError:
        raise SystemExit(f"FMO_BATTLE_BOUNDS={BATTLE_BOUNDS_SPEC!r} is not "
                         f"minX,minZ,maxX,maxZ")
    if len(_bb) != 4:
        raise SystemExit(f"FMO_BATTLE_BOUNDS={BATTLE_BOUNDS_SPEC!r} wants four "
                         f"numbers: minX,minZ,maxX,maxZ")
    if _bb[2] <= _bb[0] or _bb[3] <= _bb[1]:
        raise SystemExit(f"FMO_BATTLE_BOUNDS={BATTLE_BOUNDS_SPEC!r}: max must "
                         f"exceed min on both axes -- an inside-out box is "
                         f"outside EVERYWHERE, which is the bug this field "
                         f"exists to stop")
    BATTLE_BOUNDS = _bb
else:
    BATTLE_BOUNDS = None

MISSION_AREA_SPEC = os.environ.get("FMO_MISSION_AREA", "").strip()
if MISSION_AREA_SPEC.lower() == "map":
    MISSION_AREA = (0.0, 0.0, 128.0, 128.0)
elif MISSION_AREA_SPEC:
    try:
        _ma = tuple(float(x) for x in MISSION_AREA_SPEC.split(","))
    except ValueError:
        raise SystemExit(f"FMO_MISSION_AREA={MISSION_AREA_SPEC!r} is not "
                         f"minX,minZ,sizeX,sizeZ (or 'map')")
    if len(_ma) != 4:
        raise SystemExit(f"FMO_MISSION_AREA={MISSION_AREA_SPEC!r} wants four "
                         f"floats: minX,minZ,sizeX,sizeZ")
    if _ma[2] <= 0 or _ma[3] <= 0:
        raise SystemExit(f"FMO_MISSION_AREA={MISSION_AREA_SPEC!r}: sizeX and "
                         f"sizeZ must be > 0 -- a zero-size rect is the "
                         f"degenerate area we are trying to stop serving")
    MISSION_AREA = _ma
else:
    MISSION_AREA = None


def battle_area_box(area, bounds=None):
    """(label, block offset, raw, source) for the s16 PLAY BOUNDARY at +0xC48.

    FMO_BATTLE_BOUNDS wins when it is set: it is already (minX, minZ, maxX,
    maxZ), the field's own shape. Otherwise `area` -- FMO_MISSION_AREA's
    (minX, minZ, sizeX, sizeZ) -- is converted, which is what shipped first
    and keeps the boundary and the objective rect in step when only one knob
    is set.

    The field is four int16s read `movsx` -> `fild` with NO scaling
    (0x6111DF00), so a fractional spec cannot be expressed. We round OUTWARD --
    floor the mins, ceil the maxes -- because the failure mode is asymmetric: a
    box one unit too big costs nothing, a box one unit too small puts the pilot
    outside the battle area at the edge of the map.

    A spec that will not fit int16 is REFUSED (a note, no bytes) rather than
    wrapped: a wrapped min/max is a box the client reads as inside-out, which
    is the zero-area bug again with a different cause."""
    src = "FMO_MISSION_AREA"
    b = BATTLE_BOUNDS if bounds is None else bounds
    if b:
        lo_x, lo_z, hi_x, hi_z = b
        src = "FMO_BATTLE_BOUNDS"
    else:
        minx, minz, sx, sz = area
        lo_x, lo_z = math.floor(minx), math.floor(minz)
        hi_x, hi_z = math.ceil(minx + sx), math.ceil(minz + sz)
    for v in (lo_x, lo_z, hi_x, hi_z):
        if not -MB_AREA_LIMIT - 1 <= v <= MB_AREA_LIMIT:
            return [("BattleArea", MB_AREA_MINX, b"",
                     f"REFUSED: the box {lo_x},{lo_z}..{hi_x},{hi_z} does not "
                     f"fit the int16 field at block+{MB_AREA_MINX:#x} "
                     f"(+/-{MB_AREA_LIMIT}) -- left zero")]
    if hi_x < lo_x or hi_z < lo_z:
        return [("BattleArea", MB_AREA_MINX, b"",
                 f"REFUSED: max < min ({lo_x},{lo_z}..{hi_x},{hi_z}) -- an "
                 f"inside-out box is outside everywhere, left zero")]
    return [("BattleArea", MB_AREA_MINX,
             struct.pack("<hhhh", lo_x, lo_z, hi_x, hi_z),
             f"THE PLAY BOUNDARY ({src}): x {lo_x}..{hi_x}, z {lo_z}..{hi_z} "
             f"as four s16 at block+{MB_AREA_MINX:#x} (0x6111DF00; tested by "
             f"0x6111E5E0, X and Z only, Y ignored)")]


def mission_area_fields(area=None, seconds=None):
    """(label, block offset, raw, source) for the area rect and the time limit.

    Separate from mission_fields() only so the selftest can drive it directly.
    Emits NOTHING when both knobs are unset, so the default reply stays
    byte-identical to the zeros we served before."""
    out = []
    a = MISSION_AREA if area is None else area
    if not a and BATTLE_BOUNDS:
        #: The boundary alone. A battlefield with no capture zone is a legal
        #: mission; a battlefield with no boundary is the zero-box bug.
        out.extend(battle_area_box(None))
    if a:
        minx, minz, sx, sz = a
        rect = bytearray(MB_RECT_STRIDE)
        struct.pack_into("<f", rect, MB_RECT_MINX, minx)
        struct.pack_into("<f", rect, MB_RECT_MINZ, minz)
        struct.pack_into("<f", rect, MB_RECT_SIZEX, sx)
        struct.pack_into("<f", rect, MB_RECT_SIZEZ, sz)
        out.append(("RectDataCnt", MB_RECTDATACNT, struct.pack("<I", 1),
                    "FMO_MISSION_AREA -- one rect"))
        out.append(("AreaRect[0]", MB_RECTDATA, bytes(rect),
                    f"x {minx}..{minx + sx}, z {minz}..{minz + sz} "
                    f"(origin+size, 0x610F1D05) -- the OBJECTIVE zone"))
        out.extend(battle_area_box(a))
    t = MISSION_TIME if seconds is None else seconds
    if t:
        out.append(("TimeLimit", MB_TIMELIMIT, struct.pack("<I", t),
                    f"FMO_MISSION_TIME={t}s (block+0x4C, read at 0x612381B9; "
                    f"zero = a battle that is already over)"))
    return out


def mission_fields(leader=None, mapno=None, bgcostmax=None, totalbgcost=None,
                   battleticket=None, side=None, start_time=None):
    """(label, block offset, raw bytes, source) for every field that is ON.

    Returns nothing with every knob at its default -- that is what makes the
    default reply byte-identical to the generic arm's zeros. A MapNo that is
    not on disk is REFUSED here (returned as a note, never as bytes): a type-1
    id with no file is the 0x611250A2 crash, same class as VALID_MAPNOS."""
    out = []
    lid, src = mission_leader_id(leader)
    if lid is not None:
        out.append(("LeaderID", MB_LEADERID, struct.pack("<I", lid), src))
    mn = MISSION_MAPNO if mapno is None else mapno
    if mn:
        if mn in missionlist.TYPE1_ON_DISK:
            out.append(("MapNo", MB_MAPNO, struct.pack("<I", mn),
                        f"FMO_MISSION_MAPNO, type-1 id {mn} IS on disk "
                        f"(index {53557 + mn})"))
        else:
            out.append(("MapNo", MB_MAPNO, b"",
                        f"REFUSED: type-1 id {mn} is NOT on disk (would crash "
                        f"the client at 0x611250A2) -- left zero"))
    out.extend(mission_area_fields())
    for label, off, spec in (("BGCostMax", MB_BGCOSTMAX,
                              MISSION_BGCOSTMAX if bgcostmax is None else bgcostmax),
                             ("TotalBgCostTbl", MB_TOTALBGCOST,
                              MISSION_TOTALBGCOST if totalbgcost is None else totalbgcost),
                             ("BattleTicketTbl", MB_BATTLETICKET,
                              MISSION_BATTLETICKET if battleticket is None else battleticket)):
        pair = _u8_pair(spec) if isinstance(spec, str) else spec
        if pair:
            out.append((label, off, bytes(pair), f"knob '{spec}'"))
    if start_time:
        out.append(("StartGameTime", MB_START_GAMETIME,
                    struct.pack("<I", int(start_time) & 0xFFFFFFFF),
                    f"Unix {int(start_time)} = the battle start -> lobby+0x5CC6; "
                    f"the Battle Review's frames are ms since it"))
    if side in (1, 2):
        out.append(("BattleSide", MB_BATTLE_SIDE, bytes([side]),
                    f"the pilot's nation {side} -> lobby+0x68D3 (0x61175A90): "
                    f"units whose POP nation equals it are friendly"))
    return out


def reply_01c1(**knobs):
    """The 3,428-byte 0x01C1 mission-block payload.

    Zero except the fields mission_fields() lists; the block itself starts at
    payload+0x18 (0x611731BA) and is copied whole into lobby+0x5C7E, so every
    zero here is the zero the client already holds today."""
    b = bytearray(REPLY_01C1_LEN)
    for _label, off, raw, _src in mission_fields(**knobs):
        if raw:
            b[M1C1_BLOCK + off:M1C1_BLOCK + off + len(raw)] = raw
    return bytes(b)


# Called at run time only; imported last so that import cycles resolve.
from . import missionlist, popsweep  # noqa: E402
