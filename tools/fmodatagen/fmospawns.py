#!/usr/bin/env python3
"""fmospawns.py -- per-map BATTLE SPAWN POINTS, out of each battle map's
placement table and collision meshes.

    python fmospawns.py --client <install or mirror Direct/> [--out DIR] [--check] [--jobs N]
    python fmospawns.py --client ... --show 471 418 86      # one map's rows and why
    python fmospawns.py --client ... --ground 471 64 0      # the surfaces under a point

Needs numpy (the ground is rasterised per map).

WHY THIS EXISTS. Every battle self-POP was served at FMO_BATTLE_POS (64,5,64)
and the enemy squad stood in a line 120 units along +x from it, on every map.
On map 471 (Frontline 509 sector 32, 2026-10-06) the pilot started on a roof
and the three enemies stacked on one another. This tool picks, per battle map,
two open, flat points (side A, side B) plus a line of clear slots round each.

THE COORDINATE SPACE (proved live 2026-10-06, prod fmo.log): the battle
movement records (cmd 23 / cmd 24, decoder 0x6104C2F0) carry the unit position
as three int16 at state+8 with flag bit 0 set, scaled by 3.75 (x = s16 / 3.75).
POP position, movement records, placement AABBs and the collision meshes below
are one space: same axes, same origin, same unit.

THE CONTAINER (fmofmdt-decoded, little-endian). Header +0x10/+0x20 world box
(-2048..2048), +0x40 the 32 x 32 cell grid size, +0x50 a directory of eight
(u32 offset, u32 count) pairs:

  section 0  the 32 x 32 cell grid (u32 per cell, 0xFFFFFFFF = empty)
  section 2  the model library; its COUNT is the number of collision entries
  section 3  COLLISION: a table of u32 offsets (relative to the section), one
             per model index, 0 = the model has no collision. Entry:
               +0x00 u32 sub-mesh count n   +0x30 u32[n] sub offsets (from +0x30)
             sub: +0x00 AABB (2 x vec4), +0x2C u32 mesh offset (from the sub,
             0 = none). Mesh: +0x00 AABB, +0x20 a 16x1x16 cell index,
             +0x30 u16 vertex count, u16 triangle count, +0x34 u32 vertex
             offset (vec4 each), +0x38 u32 triangle offset (3 x u16 each),
             all from the mesh start. Vertices are MODEL-LOCAL.
  section 4  PLACEMENT: a table of 8192 u32 offsets (relative to the section;
             0xFFFFFFFF = unused), each to a 112-byte record:
               +0x00 vec4 AABB min    +0x10 vec4 AABB max (world space)
               +0x20 u32 serial (= the record's ordinal; checked)
               +0x28 u32 model index (into section 3)
               +0x50 vec3 rotation (only y is ever non-zero: +0x54)
               +0x60 vec4 position
             world = pos + (x cos r + z sin r, y, -x sin r + z cos r). That
             convention puts every collision mesh inside its record's AABB on
             every map checked (5,600 of 5,600 on 471); the mirror does not.

KEY: THE PLACEMENT TABLE IS 8192 SLOTS, NOT THE DIRECTORY COUNT. Section 4's
directory count is (non-empty grid cells + 1), not the number of records.
The first version of this tool read only `count` slots and so saw 591 of
5,630 objects on 471, 145 of 248 on 86 and 580 of 6,930 on 418: most
buildings were invisible to it, and 122/169/445/446/448 had no usable table.

THE GROUND (proved live, 2026-10-07 against prod fmo.log): every decoded
cmd 23/24 position on the nine battle maps fought so far (25 36 44 48 58 77
267 418 471, 09-27..10-06), against the rasterised top surface below: 2,720
of 2,735 distinct positions inside +/-364 stood within 1.5 of it; 6 stood
~50 higher (on another unit), 9 lower (under an overhang). A unit popped
below the surface settles on the HIGHEST surface under it, not the first one
above the pop point (map 86, 16:16Z: popped at y 5, settled at 32.2 with
floors at 25.3 and 27.2 below), so a point is usable only where that highest
surface is the ground. Collision is one-sided: every triangle is a floor
(normal up) or a wall; none faces down.

WHAT A POINT MUST BE. On a 4-unit grid over the whole map box (+/-BOUNDS) the
tool rasterises the top surface and the highest surface more than 8 below it. A cell is RAISED when
that second surface is within 60 of the top: a roof, a bridge, a crate over
the ground (live 471: every sample on ground (y 32) read not raised, every
sample on a structure (41..48) read raised). A slot needs, within SLOT_CLEAR:
a surface at every cell, nothing raised, nothing more than STEP_UP above its
own ground, nothing more than DROP below it. A side point needs the same
within SIDE_CLEAR, and its relief (max - min over that disc) is part of its
score, so hills and slopes lose to flat ground.

UNIT SPACING. Live (map 86, 16:16Z) three units popped 40 apart in z at x 184
settled at 32, 82.1, 32: the middle one on top of the other two, 50 up (a
wanzer's height). So 40 apart is not enough; slots are SLOT_APART (56) apart.

Y. One y per row: the lowest ground of any slot minus Y_DROP. Every slot's
top surface is its ground, and the client lifts a low pop to the top surface.

EXTENT (2026-10-07: the whole map). The battle POP position is four plain
floats (0x611EB08F -> 0x6110F4B0 / 0x610FF0C0: 16-byte copies to char+0x44 /
+0x54, no compare), and fmoworld.record_pop now holds a battle POP (UnitType
!= 4) only to x/y/z +/-POP_BATTLE_POS_MAX (2048, the client's own map box,
resmgr+0x224); the +/-327.67 is the LOBBY move channel's (cmd 240, int16
hundredths). The play boundary we serve (missionblock BattleArea,
FMO_BATTLE_BOUNDS) is -2048..2048 on prod. So the ground is rasterised over
+/-BOUNDS and every point stays BOUND_MARGIN inside it; where the map has no
geometry the raster has no surface and nothing can stand there, so the
collision itself sets each map's real extent.

THE SIDES. Retail teams started at opposite ends of the field. The tool finds
the map's LONGEST OPEN AXIS: of AXIS_DIRS directions, the one along which the
open (slot-clear) ground spans furthest (2nd..98th percentile of the
projection). Side A comes from the open ground in the first POOL_FRAC of that
span, side B from the last, both in one WALKABLE component (4-adjacent cells
whose tops differ by at most CLIMB), so neither starts on a ledge or in a pen
the other cannot reach. A pair scores its weaker point plus SEP_WEIGHT per
unit of separation (up to SEP_CAP) less a quarter of the ground difference.

ROW SHAPE (tab separated, fmo-battle-spawns.tsv):
  map      battle map id (type-1)
  ax az    side A's point (nation side 0, O.C.U.; arena team 0)
  bx bz    side B's point (side 1)
  y        the POP y for every slot (lowest slot ground - Y_DROP)
  a_slots  space separated x:z, up to 12 clear slots along side A's front
           line, slot 0 = the point; slots 0-3 are for pilots, 4-11 the squad
  b_slots  the same for side B
  source   what was read: placed objects, collision models, ground per side
  notes    anything the reader should doubt
"""
import argparse
import math
import os
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
TABLE_SLOTS = 8192
SEC_LIBRARY, SEC_COLLISION, SEC_PLACEMENT = 2, 3, 4

#: the battle map box: record_pop's battle guard (fmoworld.POP_BATTLE_POS_MAX)
#: and the play boundary served (FMO_BATTLE_BOUNDS -2048,-2048,2048,2048)
BOUNDS = 2048.0
#: every point and slot stays this far inside BOUNDS
BOUND_MARGIN = 64.0
EXTENT = BOUNDS - BOUND_MARGIN
#: the rasterised ground covers the whole box
HALF = BOUNDS
STEP = 4.0
#: candidate side points sit on this grid
GRID = 16.0
#: POP y stays inside record_pop's battle bound (fmoworld.POP_BATTLE_POS_MAX)
Y_MAX = BOUNDS - BOUND_MARGIN
#: a cell is RAISED when another surface lies between 8 and 60 below its top
LAYER_GAP, LAYER_SPAN = 8.0, 60.0
#: the disc a slot needs clear, and what "clear" allows above / below its ground
SLOT_CLEAR = 24.0
#: the side point's disc, strict first, then the fallback
SIDE_TIERS = ((40.0, 6.0, 12.0), (32.0, 8.0, 16.0), (28.0, 10.0, 24.0))
SLOT_STEP_UP, SLOT_DROP = 8.0, 16.0
#: slots this far apart (live: 40 apart stacked one unit on two others)
SLOT_APART = 56.0
SLOTS = 12
MIN_SLOTS = 4
#: a slot stands within this of its side point's ground; a blocked spot is
#: nudged by up to this fraction of SLOT_APART before it is given up
SLOT_LEVEL = 12.0
SLOT_NUDGE = 0.25
#: the two sides' ground may differ by this much before the pair is refused
#: (the last pass lifts it)
SIDE_LEVEL = 24.0
#: score lost per unit a side point stands above the map's low ground
LOW_WEIGHT = 0.1
#: the longest open axis is chosen from this many directions over 180 degrees
AXIS_DIRS = 12
#: each side's candidates come from this fraction of the axis span at its end
POOL_FRAC = 0.35
#: per pool, this many best candidates are paired
POOL_KEEP = 250
#: score per unit of separation, counted up to SEP_CAP
SEP_WEIGHT, SEP_CAP = 0.004, 2400.0
#: the sides are at least this far apart (and at least half the axis span)
SEP_MIN = 200.0
#: one side point is offered in at most this many pairs (the slot test then
#: sees other points, not the same one with every partner)
PAIR_REUSE = 6
#: walkable: 4-adjacent cells whose tops differ by at most this
CLIMB = 4.0
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


def _np():
    try:
        import numpy
    except ImportError:
        sys.exit("fmospawns.py needs numpy (pip install numpy)")
    return numpy


def directory(dd):
    return [struct.unpack_from("<II", dd, 0x50 + 8 * i) for i in range(8)]


def _finite(vals):
    return all(v == v and abs(v) < 1e7 for v in vals)


def placement_records(dd):
    """[record] from section 4's 8192-slot table, or None when any live slot
    fails the record shape or its serial is not its ordinal."""
    sec = directory(dd)
    base, end = sec[SEC_PLACEMENT][0], sec[SEC_PLACEMENT + 1][0]
    if not base or base + 4 * TABLE_SLOTS > end or end > len(dd):
        return None
    out = []
    for o in struct.unpack_from("<%dI" % TABLE_SLOTS, dd, base):
        if o == SENTINEL:
            continue
        at = base + o
        if at + REC_LEN > end:
            return None
        f = struct.unpack_from("<28f", dd, at)
        serial, model = struct.unpack_from("<I4xI", dd, at + 0x20)
        if (f[3] != 1.0 or f[7] != 1.0 or f[27] != 1.0 or not _finite(f[:28])
                or serial != len(out)):
            return None
        lo, hi = f[0:3], f[4:7]
        if any(h < l for l, h in zip(lo, hi)):
            return None
        out.append({"lo": lo, "hi": hi, "pos": f[24:27], "rot": f[21], "model": model})
    return out or None


def collision_models(dd, skipped=None):
    """{model index: [((x,y,z) x 3)]} in model-local space, from section 3.
    A mesh whose offsets leave the file or whose triangles name a vertex it
    does not have is skipped (its model index appended to `skipped`): some
    entries carry a non-zero +0x08 (map 84 model 27) and a sub layout that
    is not the one above."""
    sec = directory(dd)
    base, n = sec[SEC_COLLISION][0], sec[SEC_LIBRARY][1]
    out = {}
    for mi, o in enumerate(struct.unpack_from("<%dI" % n, dd, base)):
        if not o:
            continue
        e = base + o
        tris = []
        try:
            nsub = struct.unpack_from("<I", dd, e)[0]
            if nsub > 64:
                raise ValueError("sub count %d" % nsub)
            for k in range(nsub):
                s = e + 0x30 + struct.unpack_from("<I", dd, e + 0x30 + 4 * k)[0]
                moff = struct.unpack_from("<I", dd, s + 0x2C)[0]
                if not moff:
                    continue
                m = s + moff
                nv, nt = struct.unpack_from("<HH", dd, m + 0x30)
                vo, to = struct.unpack_from("<II", dd, m + 0x34)
                vs = [struct.unpack_from("<3f", dd, m + vo + 16 * i) for i in range(nv)]
                idx = struct.unpack_from("<%dH" % (3 * nt), dd, m + to)
                if idx and max(idx) >= nv:
                    raise ValueError("vertex index past %d" % nv)
                if not _finite([c for v in vs for c in v]):
                    raise ValueError("vertex not finite")
                tris.extend((vs[idx[3 * t]], vs[idx[3 * t + 1]], vs[idx[3 * t + 2]])
                            for t in range(nt))
        except (struct.error, ValueError):
            if skipped is not None:
                skipped.append(mi)
            continue
        if tris:
            out[mi] = tris
    return out


def world_to_local(r, x, z):
    c, s = math.cos(r["rot"]), math.sin(r["rot"])
    dx, dz = x - r["pos"][0], z - r["pos"][2]
    return dx * c - dz * s, dx * s + dz * c


def surfaces_at(recs, models, x, z):
    """[(y, model)] of every collision surface under (x, z), lowest first --
    the slow exact reference the rasterised field is checked against."""
    out = []
    for r in recs:
        if not (r["lo"][0] - 1 <= x <= r["hi"][0] + 1 and r["lo"][2] - 1 <= z <= r["hi"][2] + 1):
            continue
        lx, lz = world_to_local(r, x, z)
        for v0, v1, v2 in models.get(r["model"], ()):
            e1 = (v1[0] - v0[0], v1[2] - v0[2])
            e2 = (v2[0] - v0[0], v2[2] - v0[2])
            den = e1[0] * e2[1] - e1[1] * e2[0]
            if abs(den) < 1e-9:
                continue
            px, pz = lx - v0[0], lz - v0[2]
            u = (px * e2[1] - pz * e2[0]) / den
            v = (e1[0] * pz - e1[1] * px) / den
            if u < -1e-4 or v < -1e-4 or u + v > 1 + 1e-4:
                continue
            out.append((round(r["pos"][1] + v0[1] + u * (v1[1] - v0[1]) + v * (v2[1] - v0[1]), 2),
                        r["model"]))
    return sorted(set(out))


class Ground:
    """The top collision surface and the RAISED flag on a STEP grid over
    +/-HALF. top is -inf where nothing is under the cell."""

    def __init__(self, recs, models, half=HALF, step=STEP):
        np = _np()
        self.half, self.step = half, step
        n = self.n = int(round(2 * half / step)) + 1
        ax = -half + step * np.arange(n)
        top = np.full((n, n), -np.inf)
        sec = np.full((n, n), -np.inf)
        marr = {k: np.array(v, dtype=np.float64) for k, v in models.items()}
        live = [r for r in recs if r["model"] in marr
                and r["hi"][0] >= -half and r["lo"][0] <= half
                and r["hi"][2] >= -half and r["lo"][2] <= half]

        def hits(r):
            i0 = max(0, int(math.ceil((r["lo"][0] - 0.5 + half) / step)))
            i1 = min(n - 1, int(math.floor((r["hi"][0] + 0.5 + half) / step)))
            j0 = max(0, int(math.ceil((r["lo"][2] - 0.5 + half) / step)))
            j1 = min(n - 1, int(math.floor((r["hi"][2] + 0.5 + half) / step)))
            if i1 < i0 or j1 < j0:
                return None
            X, Z = np.meshgrid(ax[i0:i1 + 1], ax[j0:j1 + 1], indexing="ij")
            c, s = math.cos(r["rot"]), math.sin(r["rot"])
            dx, dz = X - r["pos"][0], Z - r["pos"][2]
            lx, lz = (dx * c - dz * s).reshape(-1, 1), (dx * s + dz * c).reshape(-1, 1)
            T = marr[r["model"]]
            v0, e1, e2 = T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
            den = e1[:, 0] * e2[:, 2] - e1[:, 2] * e2[:, 0]
            ok = np.abs(den) > 1e-9
            if not ok.any():
                return None
            v0, e1, e2, den = v0[ok], e1[ok], e2[ok], den[ok]
            px, pz = lx - v0[:, 0], lz - v0[:, 2]
            u = (px * e2[:, 2] - pz * e2[:, 0]) / den
            v = (e1[:, 0] * pz - e1[:, 2] * px) / den
            y = r["pos"][1] + v0[:, 1] + u * e1[:, 1] + v * e2[:, 1]
            y = np.where((u >= -1e-4) & (v >= -1e-4) & (u + v <= 1 + 1e-4), y, -np.inf)
            return (slice(i0, i1 + 1), slice(j0, j1 + 1)), y, X.shape

        cache = []
        for r in live:
            h = hits(r)
            if h is None:
                continue
            (si, sj), y, shp = h
            top[si, sj] = np.maximum(top[si, sj], y.max(axis=1).reshape(shp))
            cache.append(h)
        for (si, sj), y, shp in cache:
            t = top[si, sj].reshape(-1, 1)
            below = np.where(y < t - LAYER_GAP, y, -np.inf).max(axis=1).reshape(shp)
            sec[si, sj] = np.maximum(sec[si, sj], below)
        self.top = top
        self.raised = np.isfinite(top) & (top - sec < LAYER_SPAN)
        self.placed, self.meshed = len(live), len(cache)

    def cell(self, x, z):
        i = int(round((x + self.half) / self.step))
        j = int(round((z + self.half) / self.step))
        if 0 <= i < self.n and 0 <= j < self.n:
            return i, j
        return None

    def at(self, x, z):
        """(top y or None, raised)."""
        c = self.cell(x, z)
        if c is None:
            return None, False
        t = float(self.top[c])
        return (t if math.isfinite(t) else None), bool(self.raised[c])

    def disc(self, radius):
        """Per cell: (max top, min top, any raised) over a disc of `radius`;
        a cell off the grid counts as a hole (min -inf)."""
        np = _np()
        k = int(radius // self.step)
        n = self.n
        pad = np.full((n + 2 * k, n + 2 * k), -np.inf)
        pad[k:k + n, k:k + n] = self.top
        rpad = np.ones((n + 2 * k, n + 2 * k), dtype=bool)
        rpad[k:k + n, k:k + n] = self.raised
        mx = np.full((n, n), -np.inf)
        mn = np.full((n, n), np.inf)
        ra = np.zeros((n, n), dtype=bool)
        for di in range(-k, k + 1):
            for dj in range(-k, k + 1):
                if di * di + dj * dj > k * k:
                    continue
                win = pad[k + di:k + di + n, k + dj:k + dj + n]
                mx = np.maximum(mx, win)
                mn = np.minimum(mn, win)
                ra |= rpad[k + di:k + di + n, k + dj:k + dj + n]
        return mx, mn, ra

    def clear_mask(self, radius, up, drop, allow_raised=False):
        """(ok mask, relief) for cells whose disc has ground everywhere,
        nothing raised, nothing more than `up` above or `drop` below."""
        np = _np()
        mx, mn, ra = self.disc(radius)
        g = self.top
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(g) & np.isfinite(mn) & (mx - g <= up) & (g - mn <= drop)
            if not allow_raised:
                ok &= ~self.raised & ~ra
            return ok, mx - mn


class MapInfo:
    def __init__(self, mapno, recs, models):
        self.mapno, self.recs, self.models = mapno, recs, models
        self.ground = Ground(recs, models)
        self.slot_strict, _ = self.ground.clear_mask(SLOT_CLEAR, SLOT_STEP_UP, SLOT_DROP)
        self.slot_ok = self.slot_strict
        self.relaxed = False
        self.skipped = []

    def relax(self, on):
        """LAST RESORT: let points stand on a RAISED top (a layer 8..60 under
        it). The client still lifts a unit onto that top; what is unknown is
        only whether the top is a roof. 250 253 254 414 437 439 467 135 need
        it: their ground has a second floor under most of it."""
        self.relaxed = on
        if on and not hasattr(self, "slot_loose"):
            self.slot_loose, _ = self.ground.clear_mask(SLOT_CLEAR, SLOT_STEP_UP, SLOT_DROP, True)
        self.slot_ok = self.slot_loose if on else self.slot_strict

    def low_ground(self):
        """The 10th percentile of the ground inside +/-EXTENT: the floor of
        a canyon map (169, 445, 446, 448 are a basin at ~24 under a 280..344
        rim), the plain under a town's roofs."""
        np = _np()
        g = self.ground
        k = int((HALF - EXTENT) / STEP)
        t = g.top[k:g.n - k, k:g.n - k]
        t = t[np.isfinite(t)]
        return float(np.percentile(t, 10)) if t.size else 0.0

    def slot_clear(self, x, z):
        c = self.ground.cell(x, z)
        return c is not None and bool(self.slot_ok[c])

    def labels(self):
        """Per cell: its WALKABLE component (1..), 0 where there is no
        surface. Two 4-adjacent cells join when their tops differ by at most
        CLIMB, so a cliff, a wall or a roof edge separates; a ramp does not."""
        if getattr(self, "_labels", None) is not None:
            return self._labels
        np = _np()
        g = self.ground
        n = g.n
        fin = np.isfinite(g.top).ravel().tolist()
        top = np.where(np.isfinite(g.top), g.top, 0.0).ravel().tolist()
        lab = [0] * (n * n)
        cur = 0
        for start in range(n * n):
            if not fin[start] or lab[start]:
                continue
            cur += 1
            lab[start] = cur
            stack = [start]
            while stack:
                c = stack.pop()
                t = top[c]
                i, j = divmod(c, n)
                for d, inside in ((c - n, i > 0), (c + n, i < n - 1),
                                  (c - 1, j > 0), (c + 1, j < n - 1)):
                    if inside and fin[d] and not lab[d] and abs(top[d] - t) <= CLIMB:
                        lab[d] = cur
                        stack.append(d)
        self._labels = np.array(lab, dtype=np.int32).reshape(n, n)
        return self._labels

    def in_box(self):
        """Cells inside +/-EXTENT."""
        np = _np()
        g = self.ground
        c = np.abs(-g.half + g.step * np.arange(g.n)) <= EXTENT + 1e-6
        return c[:, None] & c[None, :]

    def axis(self):
        """(span, ux, uz, lo, hi): THE LONGEST OPEN AXIS. Of AXIS_DIRS
        directions, the one along which the open (slot-clear) ground of the
        largest walkable component spans furthest, 2nd..98th percentile of
        the projection (lo..hi). None when nothing is open."""
        cache = self.__dict__.setdefault("_axis", {})
        if self.relaxed in cache:
            return cache[self.relaxed]
        np = _np()
        g = self.ground
        lab = self.labels()
        open_ = self.slot_ok & (lab > 0) & self.in_box()
        out = None
        if open_.any():
            main = int(np.bincount(lab[open_]).argmax())
            I, J = np.nonzero(open_ & (lab == main))
            x = -g.half + g.step * I
            z = -g.half + g.step * J
            for k in range(AXIS_DIRS):
                a = math.pi * k / AXIS_DIRS
                ux, uz = round(math.cos(a), 12), round(math.sin(a), 12)
                lo, hi = (float(v) for v in np.percentile(x * ux + z * uz, [2, 98]))
                if out is None or hi - lo > out[0] + 1e-6:
                    out = (hi - lo, ux, uz, lo, hi)
        cache[self.relaxed] = out
        return out


def candidates(mi, tier):
    """{x, z, g, relief, above, lab: arrays} for GRID points inside +/-EXTENT
    that pass the side test of `tier` (disc radius, step up, drop)."""
    np = _np()
    cache = mi.__dict__.setdefault("_cands", {})
    key = (tier, mi.relaxed)
    if key in cache:
        return cache[key]
    ok, relief = mi.ground.clear_mask(*tier, allow_raised=mi.relaxed)
    g = mi.ground
    coord = -g.half + g.step * np.arange(g.n)
    on = (np.abs(np.mod(coord, GRID)) < 1e-6) & (np.abs(coord) <= EXTENT + 1e-6)
    ok = ok & mi.slot_ok & on[:, None] & on[None, :]
    I, J = np.nonzero(ok)
    if getattr(mi, "_low", None) is None:
        mi._low = mi.low_ground()
    gr = g.top[I, J]
    cache[key] = {"x": coord[I], "z": coord[J], "g": gr, "relief": relief[I, J],
                  "above": np.maximum(0.0, gr - mi._low), "lab": mi.labels()[I, J]}
    return cache[key]


def _cand_score(c):
    """Bigger is better: flat (low relief over the disc) and low (height
    above the map's low ground)."""
    return -2.0 * c["relief"] - LOW_WEIGHT * c["above"]


def pick_pairs(mi, c, level=None, same_walk=True, keep=200):
    """[(A, B)] best first, each (x, z, ground, relief): A from the low end
    of the longest open axis, B from the high end (POOL_FRAC of the span
    each), at least max(SEP_MIN, half the span) apart, in one walkable
    component when `same_walk`, on similar ground (within `level` when
    given). Score: the weaker side's, plus SEP_WEIGHT per unit apart (to
    SEP_CAP), less a quarter of the ground difference. No point is offered
    in more than PAIR_REUSE pairs."""
    np = _np()
    ax = mi.axis()
    if ax is None or not len(c["x"]):
        return []
    span, ux, uz, lo, hi = ax
    t = c["x"] * ux + c["z"] * uz
    sc = _cand_score(c)
    pools = []
    for sel in (t <= lo + POOL_FRAC * span, t >= hi - POOL_FRAC * span):
        idx = np.nonzero(sel)[0]
        idx = idx[np.argsort(-sc[idx], kind="stable")][:POOL_KEEP]
        pools.append(idx)
    ia, ib = pools
    if not len(ia) or not len(ib):
        return []
    d = np.hypot(c["x"][ia][:, None] - c["x"][ib][None, :],
                 c["z"][ia][:, None] - c["z"][ib][None, :])
    dg = np.abs(c["g"][ia][:, None] - c["g"][ib][None, :])
    ok = d >= max(SEP_MIN, 0.5 * span)
    if same_walk:
        ok &= c["lab"][ia][:, None] == c["lab"][ib][None, :]
    if level is not None:
        ok &= dg <= level
    ps = (np.minimum(sc[ia][:, None], sc[ib][None, :])
          + SEP_WEIGHT * np.minimum(d, SEP_CAP) - 0.25 * dg)
    P, Q = np.nonzero(ok)
    if not len(P):
        return []
    order = np.argsort(-ps[P, Q], kind="stable")

    def pt(k):
        return (float(c["x"][k]), float(c["z"][k]), float(c["g"][k]), float(c["relief"][k]))
    out, used = [], {}
    for o in order:
        p, q = int(ia[P[o]]), int(ib[Q[o]])
        if used.get(("a", p), 0) >= PAIR_REUSE or used.get(("b", q), 0) >= PAIR_REUSE:
            continue
        used[("a", p)] = used.get(("a", p), 0) + 1
        used[("b", q)] = used.get(("b", q), 0) + 1
        out.append((pt(p), pt(q)))
        if len(out) >= keep:
            break
    return out


def front_slots(mi, p, toward):
    """Up to SLOTS clear points round p, in a line abreast facing `toward`:
    the front row first, then rows back, each SLOT_APART from every other and
    on p's level (within SLOT_LEVEL of its ground). A nominal spot that is not
    clear is nudged up to SLOT_NUDGE before it is given up. Slot 0 is p."""
    ux, uz = toward[0] - p[0], toward[1] - p[1]
    n = math.hypot(ux, uz) or 1.0
    ux, uz = ux / n, uz / n
    vx, vz = -uz, ux
    g0 = mi.ground.at(*p)[0]
    cols = [0]
    for k in range(1, 4):
        cols += [k, -k]
    nudges = [(0.0, 0.0)] + [(a * SLOT_NUDGE, b * SLOT_NUDGE)
                             for a, b in ((0, -1), (1, 0), (-1, 0), (0, 1),
                                          (1, -1), (-1, -1), (1, 1), (-1, 1))]
    out = [(p[0], p[1])]
    for row in (0, -1, 1, -2):
        for col in cols:
            if len(out) >= SLOTS:
                return out
            for nr, nc in nudges:
                r, c = row + nr, col + nc
                x = round(p[0] + (r * ux + c * vx) * SLOT_APART, 1)
                z = round(p[1] + (r * uz + c * vz) * SLOT_APART, 1)
                if abs(x) > EXTENT or abs(z) > EXTENT:
                    continue
                if any(math.hypot(x - a, z - b) < SLOT_APART - 0.01 for a, b in out):
                    continue
                if not mi.slot_clear(x, z):
                    continue
                if abs(mi.ground.at(x, z)[0] - g0) > SLOT_LEVEL:
                    continue
                out.append((x, z))
                break
    return out


def load_map(root, mapno):
    raw = fmomap.load(root, fmofile.index_of(1, mapno))
    if raw is None:
        return None, "the type-1 file is not on disk"
    try:
        dd = fmofmdt.decode(raw)
    except Exception as e:                    # a container we cannot decode
        return None, "decode failed: %r" % (e,)
    recs = placement_records(dd)
    if recs is None:
        return None, "section 4 is not a placement table"
    skipped = []
    models = collision_models(dd, skipped)
    if not models:
        return None, "section 3 holds no collision mesh"
    mi = MapInfo(mapno, recs, models)
    mi.skipped = skipped
    return mi, None


def _search(mi):
    """(a, b, slots a, slots b, level, tier, same_walk) for the first pass
    that yields a pair with MIN_SLOTS each, or None. Passes: open ground
    before a raised layer, one walkable component before any, sides on one
    level before any. Within a pass the best-scored pair with all SLOTS on
    both sides wins, the strict disc before the looser tiers; failing that,
    the pair (any tier) with the most slots on its shorter side, since a
    squad off the table stands on unchecked ground."""
    for relaxed in (False, True):
        mi.relax(relaxed)
        for same_walk in (True, False):
            for level in (SIDE_LEVEL, None):
                best = None
                for tier in SIDE_TIERS:
                    for a, b in pick_pairs(mi, candidates(mi, tier), level, same_walk):
                        sa = front_slots(mi, a[:2], b[:2])
                        sb = front_slots(mi, b[:2], a[:2])
                        n = min(len(sa), len(sb))
                        if n >= MIN_SLOTS and (best is None or n > best[0]):
                            best = (n, (a, b, sa, sb, level, tier, same_walk))
                            if n >= SLOTS:
                                break
                    if best is not None and best[0] >= SLOTS:
                        break
                if best is not None:
                    return best[1]
    mi.relax(False)
    return None


def build_row(mi):
    """The TSV row dict for one map, or (None, why)."""
    found = _search(mi)
    if found is None:
        return None, "no two flat, clear points >= %g apart inside +/-%g with %d slots each" % (
            SEP_MIN, EXTENT, MIN_SLOTS)
    a, b, sa, sb, level, tier, same_walk = found
    relaxed = mi.relaxed
    span, ux, uz = mi.axis()[:3]
    grounds = [mi.ground.at(x, z)[0] for x, z in sa + sb]
    y = round(min(min(grounds) - Y_DROP, Y_MAX), 1)
    notes = []
    if relaxed:
        notes.append("LAST RESORT, on a raised layer: no open ground; the points stand on "
                     "a top surface with another floor 8..60 under it (roof or deck, unproved)")
    if not same_walk:
        notes.append("the sides are not in one walkable component (steps over %g); "
                     "a path between them is unproved" % CLIMB)
    if level is None:
        notes.append("the sides' ground differs by %.0f" % abs(a[2] - b[2]))
    if tier is not SIDE_TIERS[0]:
        notes.append("side points from the fallback tier (disc %g, up %g, drop %g)" % tier)
    if max(grounds) - min(grounds) > 16:
        notes.append("slot ground spans %.0f..%.0f; the higher slots pop %.0f below theirs"
                     % (min(grounds), max(grounds), max(grounds) - y))
    if mi.skipped:
        notes.append("%d collision model(s) in an unread layout (%s), not in the ground"
                     % (len(mi.skipped), " ".join(str(m) for m in mi.skipped[:6])))
    if len(sa) < SLOTS or len(sb) < SLOTS:
        notes.append("slots %d/%d; the squad falls back to a line behind its point" % (len(sa), len(sb)))
    return {
        "map": mi.mapno, "ax": a[0], "az": a[1], "bx": b[0], "bz": b[1], "y": y,
        "a_slots": " ".join("%g:%g" % s for s in sa),
        "b_slots": " ".join("%g:%g" % s for s in sb),
        "source": ("collision: %d placed/%d meshed in +/-%g; axis %.0f deg, open span %.0f; "
                   "sides %.0f apart; ground A %.1f B %.1f; relief %.1f/%.1f (disc %g)") % (
            len(mi.recs), mi.ground.meshed, HALF, math.degrees(math.atan2(uz, ux)), span,
            math.hypot(a[0] - b[0], a[1] - b[1]), a[2], b[2], a[3], b[3], tier[0]),
        "notes": "; ".join(notes),
    }, None


COLUMNS = ("map", "ax", "az", "bx", "bz", "y", "a_slots", "b_slots", "source", "notes")


def build(root, maps=None, keep=False):
    rows, skipped, infos = [], [], {}
    for m in (maps or battle_maps()):
        mi, why = load_map(root, m)
        if mi is None:
            skipped.append((m, why))
            continue
        row, why = build_row(mi)
        if keep:
            infos[m] = mi
        if row is None:
            skipped.append((m, why))
            continue
        rows.append(row)
    return rows, skipped, infos


def check_row(r, mi):
    """(fails, twin) for one row against its map. Every slot must stand on
    clear ground (unraised unless the row says LAST RESORT), inside
    +/-EXTENT, SLOT_APART from its side's other slots, with y under its
    ground. `twin` is None when the map offers no raised cell and no wall to
    test, else the twin failures: a raised cell, and a cell with a rise of
    more than twice SLOT_STEP_UP within SLOT_CLEAR (found by a plain square
    scan, not by the mask), must FAIL the slot test."""
    fails = []
    loose = "LAST RESORT" in r["notes"]
    mi.relax(loose)
    for side in "ab":
        pts = [tuple(float(v) for v in s.split(":")) for s in r[side + "_slots"].split()]
        if pts[0] != (r[side + "x"], r[side + "z"]):
            fails.append("map %d side %s slot 0 is not the side point" % (r["map"], side))
        for i, (x, z) in enumerate(pts):
            if abs(x) > EXTENT or abs(z) > EXTENT:
                fails.append("map %d slot %s%d outside +/-%g" % (r["map"], side, i, EXTENT))
            if not mi.slot_clear(x, z):
                fails.append("map %d slot %s%d (%g,%g) not on clear ground" % (r["map"], side, i, x, z))
            g, raised = mi.ground.at(x, z)
            if g is None or (raised and not loose) or r["y"] > min(g - Y_DROP, Y_MAX) + 0.05:
                fails.append("map %d slot %s%d ground %s raised %s vs y %g"
                             % (r["map"], side, i, g, raised, r["y"]))
            for j in range(i):
                if math.hypot(x - pts[j][0], z - pts[j][1]) < SLOT_APART - 0.01:
                    fails.append("map %d slots %s%d/%s%d too close" % (r["map"], side, i, side, j))
    twin = None
    if not loose:
        gr = mi.ground
        k = int((HALF - EXTENT) / STEP)
        w = int(SLOT_CLEAR // STEP) // 2
        raised_seen = wall_seen = False
        tf = []
        for i in range(k, gr.n - k, 3):
            for j in range(k, gr.n - k, 3):
                t = gr.top[i, j]
                if not math.isfinite(t):
                    continue
                x, z = -HALF + i * STEP, -HALF + j * STEP
                if gr.raised[i, j] and not raised_seen:
                    raised_seen = True
                    if mi.slot_clear(x, z):
                        tf.append("TWIN: raised cell (%g,%g) on map %d passed" % (x, z, r["map"]))
                if not wall_seen:
                    win = gr.top[i - w:i + w + 1, j - w:j + w + 1]
                    if float(win.max()) - t > 2 * SLOT_STEP_UP:
                        wall_seen = True
                        if mi.slot_clear(x, z):
                            tf.append("TWIN: cell (%g,%g) by a %.0f rise on map %d passed"
                                      % (x, z, float(win.max()) - t, r["map"]))
                if raised_seen and wall_seen:
                    break
            if raised_seen and wall_seen:
                break
        if raised_seen and wall_seen:
            twin = tf
    mi.relax(False)
    return fails, twin


def check(rows, root, infos=None):
    """Re-derive every row from the file; [] when all hold (check_row), with
    the twins on the first row's map that offers them."""
    fails = []
    twin_done = False
    for r in rows:
        mi = (infos or {}).get(r["map"])
        if mi is None:
            mi, why = load_map(root, r["map"])
            if mi is None:
                fails.append("map %d: %s on re-read" % (r["map"], why))
                continue
        f, twin = check_row(r, mi)
        fails += f
        if not twin_done and twin is not None:
            fails += twin
            twin_done = True
    if rows and not twin_done:
        fails.append("TWIN: no map offered a raised cell and a wall to test")
    return fails


def _work(job):
    """One map, built and checked in a worker: (map, row, why, fails, twin)."""
    root, m = job
    mi, why = load_map(root, m)
    if mi is None:
        return m, None, why, [], None
    row, why = build_row(mi)
    if row is None:
        return m, None, why, [], None
    fails, twin = check_row(row, mi)
    return m, row, None, fails, twin


def build_checked(root, maps, jobs=1):
    """(rows, skipped, fails) for `maps`, each built and checked, `jobs` maps
    at a time; the twins come from the first map (in map order) offering them."""
    work = [(root, m) for m in maps]
    if jobs > 1:
        import multiprocessing
        with multiprocessing.Pool(jobs) as pool:
            done = pool.map(_work, work, chunksize=1)
    else:
        done = [_work(w) for w in work]
    rows, skipped, fails = [], [], []
    twin_done = False
    for m, row, why, f, twin in sorted(done, key=lambda d: d[0]):
        if row is None:
            skipped.append((m, why))
            continue
        rows.append(row)
        fails += f
        if not twin_done and twin is not None:
            fails += twin
            twin_done = True
    if rows and not twin_done:
        fails.append("TWIN: no map offered a raised cell and a wall to test")
    return rows, skipped, fails


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
    ap.add_argument("--ground", type=float, nargs=3, metavar=("MAP", "X", "Z"),
                    help="print every collision surface under one point")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                    help="maps built at a time (default half the CPUs)")
    a = ap.parse_args()
    if a.ground:
        mi, why = load_map(a.client, int(a.ground[0]))
        if mi is None:
            print(why)
            return 1
        x, z = a.ground[1], a.ground[2]
        print("surfaces (y, model):", surfaces_at(mi.recs, mi.models, x, z))
        print("field: top %s raised %s slot-clear %s" % (mi.ground.at(x, z) + (mi.slot_clear(x, z),)))
        return 0
    if a.show:
        rows, skipped, _ = build(a.client, a.show)
        for r in rows:
            print("\n".join("  %-8s %s" % (c, r[c]) for c in COLUMNS))
            print()
        for m, why in skipped:
            print("map %d: no row (%s)" % (m, why))
        return 0
    maps = battle_maps()
    rows, skipped, fails = build_checked(a.client, maps, a.jobs)
    print("battle maps %d: %d rows, %d without" % (len(maps), len(rows), len(skipped)))
    for m, why in skipped:
        print("  map %d: %s" % (m, why))
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
