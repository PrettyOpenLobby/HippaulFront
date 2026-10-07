#!/usr/bin/env python3
"""fmospawns.py -- per-map BATTLE SPAWN POINTS, out of each battle map's placement table.

    python fmospawns.py --client <install or mirror Direct/> [--out DIR] [--check]
    python fmospawns.py --client ... --show 471 418 86      # one map's rows and why

WHY THIS EXISTS. Every battle self-POP was served at FMO_BATTLE_POS (64,5,64)
and the enemy squad stood in a line 120 units along +x from it, on every map.
On map 471 (Frontline 509 sector 32, 2026-10-06) the pilot started on a roof
and the three enemies stacked on one another. This tool picks, per battle map,
two open points far from every placed object (side A, side B) plus a short
line of clear slots round each, so teammates and enemies do not stack.

THE COORDINATE SPACE (proved live 2026-10-06, prod fmo.log): the battle
movement records (cmd 23 / cmd 24, decoder 0x6104C2F0) carry the unit position
as three int16 at state+8 with flag bit 0 set, scaled by 3.75 (x = s16 / 3.75).
Decoded, the pilot on map 471 walked between y 31.7 and 32.3 for 11 minutes,
x -101..1343, z -148..1477, and the placement table's buildings on 471 stand on
y 32.0. So the POP position, the movement records and the placement AABBs are
one space: same axes, same origin, same unit. The pilot's first record on 471
was (64.0, 46.4, 0.0), on top of the building at x 0..64, z -16..48 (top 47.5).

THE PLACEMENT TABLE. Decoded container (fmofmdt), section directory at +0x50
(u32 offset, u32 count) x 8. Section 4 is a table of u32 offsets relative to
its own base; 0xFFFFFFFF marks an unused slot (most maps pad the table, which
is why a strict reader missed half of them). Each live slot points at a
112-byte record:

    +0x00 f32[4] AABB min (w = 1.0)     +0x10 f32[4] AABB max (w = 1.0)
    +0x20 u32    model id               +0x54 f32 rotation (y)
    +0x60 f32[4] position (w = 1.0)

Records whose footprint is a 128 or 256 square on the 128 grid are GROUND
TILES (terrain pieces, walkable); everything else is an OBSTACLE. A record is
recognised by shape (both w's and the position's w exactly 1.0, min <= max,
finite), and the section is chosen by how many records have their position
inside their own box -- section 2 on some maps also passes the shape test,
but its boxes are model-local (centred on the origin).

WHAT IS NOT IN THE TABLE: terrain relief. Hills and rocks belong to the
terrain mesh (section 3), which is not decoded. Live on coliseum map 86 an
enemy popped at (184, 5, 64) settled at y 82.1 with no record under it. So a
point is preferred on a LOW-RELIEF ground tile when the map has tiles there.

Y. The client lifts a unit popped below the surface onto the highest surface
under it (every battle so far popped at y 5 and settled at the ground, 30..133,
or on a roof), so a point below the ground is the proven case. The row's y is
the estimated ground minus 4: a ground tile's origin y (at least its min y),
else the median base of the objects within 160 units, else the map's median
base. The estimate is not proved per map; it only has to be at or above the
true y by less than 4 for the unit not to fall.

EXTENT. The POP guard (fmoworld.record_pop) refuses any coordinate outside
+/-327.67, so every point and slot stays inside +/-300 even though the
movement records prove the battle world is wider.

ROW SHAPE (tab separated, fmo-battle-spawns.tsv):
  map      battle map id (type-1)
  ax az    side A's point (nation side 0, O.C.U.; arena team 0)
  bx bz    side B's point (side 1)
  y        the POP y for every slot (estimated ground - 4)
  a_slots  space separated x:z, 12 clear slots along side A's front line,
           slot 0 = the point; slots 0-3 are for pilots, 4-11 for the squad
  b_slots  the same for side B
  source   placement:<n objects>/<n tiles>, clearance, ground source
  notes    anything the reader should doubt
"""
import argparse
import math
import os
import statistics
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fmofile                                                     # noqa: E402
import fmofmdt                                                     # noqa: E402
import fmomap                                                      # noqa: E402

SERVICES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "services")
DATA_DIR = os.path.join(SERVICES, "fmodata")
OUT_NAME = "fmo-battle-spawns.tsv"
DEFAULT_CLIENT = r"C:/Program Files (x86)/PlayOnline/SquareEnix/FRONT MISSION ONLINE"

REC_LEN = 112
SENTINEL = 0xFFFFFFFF
#: every point and slot stays inside this box (the POP guard is +/-327.67)
EXTENT = 300.0
GRID = 8.0
#: the clearance a side point needs from every obstacle box, best first
CLEARANCES = (32.0, 24.0)
#: the clearance a slot needs, and how far apart two slots must be
SLOT_CLEAR = 16.0
SLOT_APART = 16.0
SLOT_STEP = 20.0
SLOTS = 12
#: the separation the two sides aim for, and the band it must fall in
SEP_AIM, SEP_MIN, SEP_MAX = 240.0, 160.0, 360.0
#: a ground tile whose top is this far above its ground is a hill or a cliff
RELIEF_OK = 12.0
Y_DROP = 4.0
#: the "no battle here" placeholders (fmosectors.py) and the arena selector
PLACEHOLDER_MAPS = (0, 2, 3, 4, 5)


def battle_maps():
    """Every battle map fmosectors names (selectors below 700; 700+ are all 0),
    plus the default sortie map 418."""
    sys.path.insert(0, SERVICES)
    import fmosectors                                              # noqa: E402
    maps = {418}
    for sel, rows in fmosectors.SECTORS.items():
        if sel >= 700:
            continue
        for _row, mapno in rows.values():
            if mapno not in PLACEHOLDER_MAPS:
                maps.add(mapno)
    return sorted(maps)


def _finite(vals):
    return all(v == v and abs(v) < 1e7 for v in vals)


def section_records(dd, i):
    """[record dict] of directory section `i`, or None when it is not a
    placement table. Sentinel slots are skipped; any other slot that fails
    the shape test makes the whole section fail."""
    if 0x50 + 8 * i + 8 > len(dd):
        return None
    base, cnt = struct.unpack_from("<II", dd, 0x50 + 8 * i)
    if not cnt or cnt > 20000 or base + 4 * cnt > len(dd):
        return None
    out = []
    for o in struct.unpack_from("<%dI" % cnt, dd, base):
        if o == SENTINEL:
            continue
        at = base + o
        if at + REC_LEN > len(dd):
            return None
        f = struct.unpack_from("<28f", dd, at)
        if f[3] != 1.0 or f[7] != 1.0 or f[27] != 1.0 or not _finite(f[:28]):
            return None
        lo, hi = f[0:3], f[4:7]
        if any(h < l for l, h in zip(lo, hi)):
            return None
        out.append({"lo": lo, "hi": hi, "pos": f[24:27], "rot": f[21],
                     "model": struct.unpack_from("<I", dd, at + 0x20)[0]})
    return out or None


def placement(dd):
    """(section index, records) for the placement table, or (None, None).
    The table is the section whose records sit at their own box: the model
    library's boxes are local and centred on the origin."""
    best = (0, None, None)
    for i in range(1, 8):
        recs = section_records(dd, i)
        if not recs:
            continue
        inside = sum(1 for r in recs
                     if r["lo"][0] - 1 <= r["pos"][0] <= r["hi"][0] + 1
                     and r["lo"][2] - 1 <= r["pos"][2] <= r["hi"][2] + 1)
        if inside * 10 < len(recs) * 7:
            continue
        # section 4 is where every map seen keeps it; prefer it on a tie
        score = (inside, i == 4)
        if best[1] is None or score > best[0]:
            best = (score, i, recs)
    return best[1], best[2]


def is_tile(r):
    sx, sz = r["hi"][0] - r["lo"][0], r["hi"][2] - r["lo"][2]
    def on_grid(v):
        return abs(v / 128.0 - round(v / 128.0)) * 128.0 <= 1.0
    return (any(abs(sx - s) <= 1.0 for s in (128.0, 256.0))
            and any(abs(sz - s) <= 1.0 for s in (128.0, 256.0))
            and on_grid(r["lo"][0]) and on_grid(r["lo"][2]))


def is_obstacle(r):
    """Not a tile, tall enough to block a wanzer, not a map-sized backdrop."""
    if is_tile(r):
        return False
    sx, sy, sz = (h - l for l, h in zip(r["lo"], r["hi"]))
    return sy > 2.0 and max(sx, sz) < 1000.0


def clearance(x, z, obstacles):
    """Distance in the xz plane from (x, z) to the nearest obstacle box (0
    when inside one)."""
    best = 1e9
    for r in obstacles:
        dx = max(r["lo"][0] - x, 0.0, x - r["hi"][0])
        dz = max(r["lo"][2] - z, 0.0, z - r["hi"][2])
        d = math.hypot(dx, dz)
        if d < best:
            best = d
            if best == 0.0:
                break
    return best


def tile_at(x, z, tiles):
    """The ground tile under (x, z) with the lowest top, or None."""
    under = [t for t in tiles if t["lo"][0] <= x <= t["hi"][0] and t["lo"][2] <= z <= t["hi"][2]]
    return min(under, key=lambda t: t["hi"][1]) if under else None


def tile_ground(t):
    return max(t["pos"][1], t["lo"][1])


class MapInfo:
    def __init__(self, mapno, sec, recs):
        self.mapno, self.sec, self.recs = mapno, sec, recs
        self.tiles = [r for r in recs if is_tile(r)]
        self.obstacles = [r for r in recs if is_obstacle(r)]
        bases = [r["lo"][1] for r in self.obstacles] or [r["lo"][1] for r in recs]
        self.median_base = statistics.median(bases) if bases else None
        xs = [v for r in recs for v in (r["lo"][0], r["hi"][0])]
        zs = [v for r in recs for v in (r["lo"][2], r["hi"][2])]
        self.extent = (min(xs), min(zs), max(xs), max(zs))

    def ground(self, x, z):
        """(estimated ground y, source, relief or None) at (x, z)."""
        t = tile_at(x, z, self.tiles)
        if t is not None:
            g = tile_ground(t)
            return g, "tile", t["hi"][1] - g
        near = [r["lo"][1] for r in self.obstacles
                if math.hypot((r["lo"][0] + r["hi"][0]) / 2 - x,
                              (r["lo"][2] + r["hi"][2]) / 2 - z) <= 160.0]
        if len(near) >= 3:
            return statistics.median(near), "local base", None
        if self.median_base is not None:
            return self.median_base, "map base", None
        return None, "none", None

    def on_map(self, x, z, margin=64.0):
        ex = self.extent
        return ex[0] + margin <= x <= ex[2] - margin and ex[1] + margin <= z <= ex[3] - margin


def candidates(mi, need):
    """[(x, z, clearance, ground, gsrc, relief)] for grid points with at least
    `need` clearance, on the map, inside EXTENT."""
    out = []
    n = int(EXTENT // GRID)
    reach = EXTENT + 2 * max(CLEARANCES)
    near = [r for r in mi.obstacles
            if r["hi"][0] >= -reach and r["lo"][0] <= reach
            and r["hi"][2] >= -reach and r["lo"][2] <= reach]
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            x, z = i * GRID, j * GRID
            if not mi.on_map(x, z):
                continue
            c = min(clearance(x, z, near), 1000.0)
            if c < need:
                continue
            g, gsrc, rel = mi.ground(x, z)
            if g is None:
                continue
            out.append((x, z, c, g, gsrc, rel))
    return out


def _cand_score(c):
    """Bigger is better: clearance (capped), a low-relief tile under it, and
    not out at the edge of the +/-EXTENT box (a corner far from every object
    is open ground only as far as the table knows)."""
    x, z, clr, _g, gsrc, rel = c
    s = min(clr, 64.0) - 0.05 * max(abs(x), abs(z))
    if gsrc == "tile":
        s += 16.0 if rel is not None and rel <= RELIEF_OK else -24.0
    return s


def pick_pair(cands, has_tiles):
    """(A, B) maximising min score with the separation near SEP_AIM, A the
    one with the smaller z (then x). None when no pair fits the band. On a
    map with ground tiles the flat-tile points are tried first."""
    if has_tiles:
        flat = [c for c in cands if c[4] == "tile" and c[5] is not None and c[5] <= RELIEF_OK]
        if len(flat) >= 2:
            pair = _best_pair(flat)
            if pair:
                return pair
    return _best_pair(cands)


def _best_pair(cands):
    # a coarser grid for the pairing, or the best 400 all sit in one block
    coarse = [c for c in cands if c[0] % 16 == 0 and c[1] % 16 == 0] or cands
    top = sorted(coarse, key=lambda c: -_cand_score(c))[:400]
    best = None
    for i, a in enumerate(top):
        sa = _cand_score(a)
        for b in top[i + 1:]:
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if not SEP_MIN <= d <= SEP_MAX:
                continue
            mid = math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            s = min(sa, _cand_score(b)) - abs(d - SEP_AIM) * 0.05 - mid * 0.05
            if best is None or s > best[0]:
                best = (s, a, b)
    if best is None:
        return None
    a, b = best[1], best[2]
    if (b[1], b[0]) < (a[1], a[0]):
        a, b = b, a
    return a, b


def front_slots(mi, p, toward):
    """SLOTS clear points round p, in a line abreast facing `toward`: rows
    back from the front, columns out along the line, each SLOT_CLEAR from
    every obstacle and SLOT_APART from every other slot. Slot 0 is p."""
    ux, uz = toward[0] - p[0], toward[1] - p[1]
    n = math.hypot(ux, uz) or 1.0
    ux, uz = ux / n, uz / n
    vx, vz = -uz, ux
    cols = [0]
    for k in range(1, 8):
        cols += [k, -k]
    out = [(p[0], p[1])]
    for row in (0, -1, 1, -2, -3):
        for col in cols:
            if len(out) >= SLOTS:
                return out
            x = p[0] + row * SLOT_STEP * ux + col * SLOT_STEP * vx
            z = p[1] + row * SLOT_STEP * uz + col * SLOT_STEP * vz
            x, z = round(x, 1), round(z, 1)
            if abs(x) > EXTENT or abs(z) > EXTENT or not mi.on_map(x, z, margin=32.0):
                continue
            if any(math.hypot(x - a, z - b) < SLOT_APART for a, b in out):
                continue
            if clearance(x, z, mi.obstacles) < SLOT_CLEAR:
                continue
            out.append((x, z))
    return out


def load_map(root, mapno):
    raw = fmomap.load(root, fmofile.index_of(1, mapno))
    if raw is None:
        return None, "the type-1 file is not on disk"
    try:
        dd = fmofmdt.decode(raw)
    except Exception as e:                    # a container we cannot decode
        return None, "decode failed: %r" % (e,)
    sec, recs = placement(dd)
    if recs is None:
        return None, "no placement section found"
    return MapInfo(mapno, sec, recs), None


def build_row(mi):
    """The TSV row dict for one map, or (None, why)."""
    for need in CLEARANCES:
        cands = candidates(mi, need)
        pair = pick_pair(cands, bool(mi.tiles))
        if pair:
            break
    else:
        return None, "no pair of points %g+ clear of every object inside +/-%g" % (
            CLEARANCES[-1], EXTENT)
    a, b = pair
    sa = front_slots(mi, a[:2], b[:2])
    sb = front_slots(mi, b[:2], a[:2])
    if len(sa) < 4 or len(sb) < 4:
        return None, "fewer than 4 clear slots at a side (%d/%d)" % (len(sa), len(sb))
    gy = min(a[3], b[3])
    y = round(gy - Y_DROP, 1)
    notes = []
    if a[4] != "tile" or b[4] != "tile":
        notes.append("no ground tile under %s: y from %s" % (
            "/".join(s for s, c in (("A", a), ("B", b)) if c[4] != "tile"),
            "/".join(sorted({c[4] for c in (a, b) if c[4] != "tile"}))))
    if abs(a[3] - b[3]) > 8:
        notes.append("the sides' ground differs by %.0f (y is the lower)" % abs(a[3] - b[3]))
    for s, c in (("A", a), ("B", b)):
        if c[4] == "tile" and c[5] is not None and c[5] > RELIEF_OK:
            notes.append("side %s tile relief %.0f (hill?)" % (s, c[5]))
    if len(sa) < SLOTS or len(sb) < SLOTS:
        notes.append("slots %d/%d" % (len(sa), len(sb)))
    notes.append("terrain relief off-table is not checked")
    return {
        "map": mi.mapno, "ax": a[0], "az": a[1], "bx": b[0], "bz": b[1], "y": y,
        "a_slots": " ".join("%g:%g" % s for s in sa),
        "b_slots": " ".join("%g:%g" % s for s in sb),
        "source": "placement sec%d:%d objects/%d tiles; clear %.0f/%.0f (need %g); ground %.1f (%s)" % (
            mi.sec, len(mi.obstacles), len(mi.tiles), a[2], b[2], need, gy,
            "/".join(sorted({a[4], b[4]}))),
        "notes": "; ".join(notes),
    }, None


COLUMNS = ("map", "ax", "az", "bx", "bz", "y", "a_slots", "b_slots", "source", "notes")


def build(root, maps=None):
    rows, skipped = [], []
    for m in (maps or battle_maps()):
        mi, why = load_map(root, m)
        if mi is None:
            skipped.append((m, why))
            continue
        row, why = build_row(mi)
        if row is None:
            skipped.append((m, why))
            continue
        rows.append(row)
    return rows, skipped


def check(rows, root):
    """Re-derive every row's clearance from the file; [] when all hold.
    The twin: a point inside an obstacle of the first row's map must FAIL the
    same test, or the test cannot fail at all."""
    fails = []
    twin_done = False
    for r in rows:
        mi, why = load_map(root, r["map"])
        if mi is None:
            fails.append("map %d: %s on re-read" % (r["map"], why))
            continue
        for side in "ab":
            px, pz = r[side + "x"], r[side + "z"]
            if clearance(px, pz, mi.obstacles) < CLEARANCES[-1]:
                fails.append("map %d side %s (%g,%g) is not clear" % (r["map"], side, px, pz))
            pts = [tuple(float(v) for v in s.split(":")) for s in r[side + "_slots"].split()]
            for i, (x, z) in enumerate(pts):
                if abs(x) > EXTENT or abs(z) > EXTENT:
                    fails.append("map %d slot %s%d outside +/-%g" % (r["map"], side, i, EXTENT))
                if clearance(x, z, mi.obstacles) < SLOT_CLEAR:
                    fails.append("map %d slot %s%d (%g,%g) not clear" % (r["map"], side, i, x, z))
                for j in range(i):
                    if math.hypot(x - pts[j][0], z - pts[j][1]) < SLOT_APART - 0.01:
                        fails.append("map %d slots %s%d/%s%d too close" % (r["map"], side, i, side, j))
        if not twin_done and mi.obstacles:
            o = mi.obstacles[0]
            cx, cz = (o["lo"][0] + o["hi"][0]) / 2, (o["lo"][2] + o["hi"][2]) / 2
            if clearance(cx, cz, mi.obstacles) >= SLOT_CLEAR:
                fails.append("TWIN: the centre of an obstacle on map %d passed the "
                             "clearance test" % r["map"])
            twin_done = True
    return fails


def write_tsv(rows, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in sorted(rows, key=lambda r: r["map"]):
            f.write("\t".join(("%g" % r[c]) if isinstance(r[c], float) else str(r[c])
                              for c in COLUMNS) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=DEFAULT_CLIENT,
                    help="the FRONT MISSION ONLINE install, or a mirror blob tree's Direct/")
    ap.add_argument("--out", default=DATA_DIR, help="directory for %s" % OUT_NAME)
    ap.add_argument("--check", action="store_true", help="build and check, write nothing")
    ap.add_argument("--show", type=int, nargs="*", help="print these maps' rows only")
    a = ap.parse_args()
    if a.show:
        rows, skipped = build(a.client, a.show)
        for r in rows:
            print("\n".join("  %-8s %s" % (c, r[c]) for c in COLUMNS))
            print()
        for m, why in skipped:
            print("map %d: no row (%s)" % (m, why))
        return 0
    maps = battle_maps()
    rows, skipped = build(a.client, maps)
    print("battle maps %d: %d rows, %d without" % (len(maps), len(rows), len(skipped)))
    for m, why in skipped:
        print("  map %d: %s" % (m, why))
    fails = check(rows, a.client)
    for f in fails:
        print("CHECK FAIL:", f)
    print("self-check: %s" % ("PASS" if not fails else "%d FAIL" % len(fails)))
    if fails:
        return 1
    if not a.check:
        path = os.path.join(a.out, OUT_NAME)
        write_tsv(rows, path)
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
