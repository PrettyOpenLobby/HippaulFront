#!/usr/bin/env python3
"""fmomap.py -- FRONT MISSION ONLINE's MAP CONTAINER header (resource types 1 and 2).

    python fmomap.py header <install>            # every lobby MapNo present
    python fmomap.py header <install> 121 161    # named ids
    python fmomap.py header <install> --type 1 38 107 200

`<install>` is the directory that CONTAINS `Data/` - either a PC install or a
patch-mirror blob tree. Mirror blobs are stored as `<name>.DAT.slc`: one lead
byte, then a raw zlib stream. The inflated length matches the installed .DAT
byte for byte (map 121 -> 911,984 B, the size measured off a PC install on
2026-08-23).

WARNING: Provenance: the 293/293 result below was taken against patch bundle
`20060823_0`, which is the only one carrying map data (9,474 blobs). The five
later bundles touch `AI/`, `AJ/` and `BB/` only - no map file is overridden,
so that bundle IS the current install for these resources.

THE HEADER, read BIG-ENDIAN (2026-08-28). The first floats decode cleanly as
-128.0 / 1.0 / 128.0 only in BE, and the payload-size field only balances in BE:

    +0x08  u32   a count, 20..56 across the twelve lobbies (unidentified)
    +0x10  f32   HEIGHT MIN      (+0x14 is 1.0f)
    +0x20  f32   HEIGHT MAX      (+0x24 is 1.0f)
    +0x30  f32   HEIGHT SPAN     (+0x34 is 1.0f)
    +0x88  u32   PAYLOAD SIZE; filesize - this = the header length

VERIFIED: `SPAN == MAX - MIN` in **all twelve** lobby maps, which is what
promotes the triple from "three floats" to a measured extent of SOMETHING.

KEY: **AND IT IS THE VERTICAL AXIS - corrected 2026-09-08 by a LIVE read.**
A live read of the client in map 267 printed its own map bounding box, the
one `0x6111E560` tests before indexing a terrain cell ([[resmgr+0x224]]):

    min = (-2048.0, 0.0, -2048.0, 1.0)      max = (2048.0, 256.0, 2048.0, 1.0)

`maxY` is **256**, which is exactly what this header reports for map 267, while
X and Z are +/-2048. So this triple is the map's HEIGHT, not its footprint.

That retires the reading below, which cost a round: the battle area was served
as 0..128 because map 418's triple says 0..128, and it penned the pilot into
**3.1%** of the battlefield - "the safe area is very small". It also explains
why the numbers never scaled with content: map 418 is 12.4 MB at 128, map 232
is 3.7 MB at 384. Those are ceilings, not sizes.

WARNING: SUPERSEDED, kept for the trail: "the maps do not share an origin -
most lobbies are -128..128 (centre 0), 143/144 are -128..256 and 161 is
0..128, so a single global spawn point is wrong by construction." The
conclusion may still hold for spawns, but NOT for this reason: these are
height ranges, and a map whose floor is at y=-128 says nothing about where its
origin is in X/Z.

WARNING: What this does NOT give you: a horizontal extent (see above), or a
spawn point. The player's position is server-authored (`0x0153` ->
`lobby+0x6B46`, see fmo.py's PILOTPOS) and the client holds no per-map default
- so this bounds a search, it does not answer one. The bulk of the container
past the header is packed data and is still undecoded.
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402

LOBBY_MAPNOS = (101, 102, 121, 122, 123, 124, 141, 142, 143, 144, 151, 161)

OFF_COUNT, OFF_MIN, OFF_MAX, OFF_SPAN, OFF_PAYLOAD = 0x08, 0x10, 0x20, 0x30, 0x88


def load(root, index):
    """The inflated container bytes, from a .DAT or a mirror .DAT.slc."""
    p = fmofile.exists(root, index)
    if p is None:
        return None
    raw = open(p, "rb").read()
    if p.endswith(".slc"):
        return zlib.decompress(raw[1:])
    return raw


def header(d):
    """The decoded fields, or None if the blob is too short to hold them."""
    if len(d) < OFF_PAYLOAD + 4:
        return None
    u32 = lambda o: struct.unpack_from(">I", d, o)[0]
    f32 = lambda o: struct.unpack_from(">f", d, o)[0]
    payload = u32(OFF_PAYLOAD)
    return {"size": len(d), "count": u32(OFF_COUNT),
            "min": f32(OFF_MIN), "max": f32(OFF_MAX), "span": f32(OFF_SPAN),
            "payload": payload, "hdr": len(d) - payload}


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "header":
        print(__doc__)
        return 2
    argv = sys.argv[2:]
    rtype = fmofile.TYPE_MAP
    if "--type" in argv:
        i = argv.index("--type")
        rtype = int(argv[i + 1], 0)
        del argv[i:i + 2]
    root, ids = argv[0], [int(x, 0) for x in argv[1:]]
    if not ids:
        ids = list(LOBBY_MAPNOS) if rtype == fmofile.TYPE_MAP else range(512)
    print("root: %s   resource type %d" % (root, rtype))
    print("%5s %10s %9s %8s %6s %9s %9s %9s %s"
          % ("id", "size", "payload", "hdr", "count", "min", "max", "span",
             "span==max-min"))
    for rid in ids:
        d = load(root, fmofile.index_of(rtype, rid))
        if d is None:
            continue
        h = header(d)
        if h is None:
            print("%5d  TOO SHORT (%d B) to hold the header" % (rid, len(d)))
            continue
        ok = abs((h["max"] - h["min"]) - h["span"]) < 1e-3
        print("%5d %10d %9d %8d %6d %9.1f %9.1f %9.1f %s"
              % (rid, h["size"], h["payload"], h["hdr"], h["count"],
                 h["min"], h["max"], h["span"], "OK" if ok else "MISMATCH"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# --------------------------------------------------------------------------- #
# THE OBJECT TABLE (2026-09-05). The container is FMDT-encoded like every other
# resource - fmomap.header() reads the RAW bytes big-endian and happens to land
# on the un-XORed halves, which is why it works without decoding. Decoded
# little-endian the header is:
#
#   +0x00 u32 magic 0x00900402      +0x0C u32 OBJECT COUNT (54 for map 102)
#   +0x10 f32[4] world min (-2048, -128, -2048, 1)
#   +0x20 f32[4] world max ( 2048,  128,  2048, 1)
#   +0x30 f32[4] (128, 256, 128, 1)     +0x40 two (32, 1) pairs
#   +0x50.. (u32 offset, u32 count) section directory
#
# Section 2 (offset 0x1090) is a fixed 148-slot table of u32 offsets RELATIVE to
# 0x1090. Exactly `object count` of them point at a chunk whose +0x18 is the tag
# `XTM3`; the rest are padding or zero. Chunk header is 0x18 bytes
# (u32 header size, u32 data size, 16 zero) then the XTM3 model.
#
# WARNING: STILL NOT DECODED: the XTM3 vertices are in LOCAL model space (54/54
# boxes centre on the origin), so an object's WORLD position is a transform
# inside the XTM3 block that has not been read. The other sections
# (0x257c28/66, 0x451e5c/85, 0x464e04/70) are further offset tables, not
# transforms.
# VERIFIED: That no longer blocks placement: world_boxes() below reads
# world-space AABBs straight out of the container and locates props without
# the transform. (An earlier version of this comment said the container
# "CANNOT tell you where a counter stands" - that was written before the
# AABBs were found, and is wrong.)


def objects(d):
    """[(chunk offset, length)] for the real XTM3 objects, in file order.

    WARNING: Map 102 returns 54 for a header count of 54, but map 101 returns
    52 for a header count of 53 - one object is not found by the `XTM3 at
    +0x18` test. Do not treat the result as complete until that one is
    explained (a zero offset that is really used, a second chunk layout, or a
    duplicate that the set() collapses)."""
    base = 0x1090
    raw = [struct.unpack_from("<I", d, base + 4 * i)[0] for i in range(148)]
    offs = sorted({base + o for o in raw
                   if o and d[base + o + 0x18:base + o + 0x1c] == b"XTM3"})
    offs.append(len(d))
    return [(offs[i], offs[i + 1] - offs[i]) for i in range(len(offs) - 1)]

# --------------------------------------------------------------------------- #
# VERIFIED: WORLD-SPACE BOUNDING BOXES (2026-09-05) - what finally gave up the
# prop positions, after the XTM3 chunks turned out to hold LOCAL-space vertices.
#
# Scattered through the decoded container are pairs of vec4 whose w is exactly
# 1.0f: an axis-aligned (min, max) box in WORLD coordinates. They are found by
# that shape alone - u32 at +3 and +7 both 0x3F800000, max >= min componentwise.
#
# KEY: VALIDATED against a tester's own feet on map 102 (2026-09-05). The two
# scramble consoles come out as identical 2.00 x 0.87 x 2.00 boxes at
# (0.00, 3.93, 0.00) and (0.00, 3.93, -20.00); the tester walked to each and
# stood at z = +2.30 and z = -17.70 - 2.30 m in front of BOTH, the same offset
# twice. Their platforms are 12 x 12 boxes centred (0, 3.31, 0) and
# (0, 3.31, -20) whose TOP is exactly y = 3.50, which is the y the client
# reports for a player standing on one. Two console stations exist in that room
# and no others, so a lobby's counters are countable from the file.
#
# This is the layer to read for placement. The lobby SCRIPTS (D07/D87 E285) only
# describe cutscene staging - the settled positions were SE server-side data -
# but the props those NPCs belong at are right here.


def world_boxes(d, limit=300.0):
    """[(file offset, min xyz, max xyz)] for every world-space AABB in the
    decoded container. `limit` rejects boxes outside the map extent."""
    out = []
    end = len(d) - 32
    o = 0
    while o < end:
        if (struct.unpack_from("<I", d, o + 12)[0] == 0x3F800000
                and struct.unpack_from("<I", d, o + 28)[0] == 0x3F800000):
            lo = struct.unpack_from("<3f", d, o)
            hi = struct.unpack_from("<3f", d, o + 16)
            if (all(l == l and h == h for l, h in zip(lo, hi))          # not NaN
                    and all(h >= l for l, h in zip(lo, hi))
                    and max(abs(v) for v in lo + hi) <= limit):
                out.append((o, lo, hi))
        o += 4
    return out


def props_on_platforms(d, top=3.50, tol=0.06):
    """Boxes resting on a platform whose top is `top` - i.e. the things a
    counter NPC would stand at. Returns [(offset, centre, size)]."""
    out = []
    for o, lo, hi in world_boxes(d):
        size = tuple(h - l for l, h in zip(lo, hi))
        if not (0.4 <= size[0] <= 4.5 and 0.4 <= size[2] <= 4.5):
            continue
        if not (0.3 <= size[1] <= 2.5):
            continue
        if abs(lo[1] - top) > 0.25:
            continue
        out.append((o, tuple((l + h) / 2 for l, h in zip(lo, hi)), size))
    return out
