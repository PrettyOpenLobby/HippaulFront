#!/usr/bin/env python3
"""fmoobjectives.py -- the five DEMOLITION target areas per Frontline battle
map and per defending side, out of the map's collision ground and its spawn row.

    python fmoobjectives.py --client <install or mirror Direct/> [--out DIR] [--jobs N]
    python fmoobjectives.py --client ... --show 471 418     # print these maps only

Needs numpy, like fmospawns.py (whose ground reader this uses).

WHAT IT PICKS. SE's Demolition (topics 2006-09-20) has five target areas on
the battle map; the attacking army plants a time bomb in any one of them and
the defending army stops it. Where SE put them is not in any file we hold, so
the areas here are OURS: five points in the DEFENDING side's half, measured
from its spawn point towards the other side (t = the fraction of the A-B
distance, lat = the sideways offset as a fraction of it):

    (0.18, -0.10) (0.18, +0.10) (0.30, 0) (0.42, -0.10) (0.42, +0.10)

Each is moved to the nearest grid point on open, flat ground (the same clear
discs fmozones.py uses), at least SPAWN_KEEP from either spawn point and
POINT_APART from the other areas. The other objective modes (Capture & Hold,
Capture the Flag, Beacon Delivery) are placed from fmo-capture-zones.tsv and
the spawn row by the server; only Demolition needs points of its own.

OUTPUT: fmodata/fmo-demolition-points.tsv, one row per map:
  map      the battle map (type-1 id)
  def_a    five x:y:z centres when side A defends (A..E)
  def_b    five x:y:z centres when side B defends
  notes    anything to doubt (a point off the walkable component)
A side with no answer is written as '-' and the server falls back for it.
"""
import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fmospawns                                                   # noqa: E402
import fmozones                                                    # noqa: E402

DATA_DIR = fmospawns.DATA_DIR
OUT_NAME = "fmo-demolition-points.tsv"
#: (t, lat): along the defender -> attacker axis, and sideways, as fractions
#: of the spawn-to-spawn distance (OURS)
TARGETS = ((0.18, -0.10), (0.18, 0.10), (0.30, 0.0), (0.42, -0.10), (0.42, 0.10))
#: the sideways offset is capped so a long map keeps the areas near its axis
LAT_MAX = 320.0
SPAWN_KEEP = 150.0
POINT_APART = 120.0
SEARCH_FRAC = 0.15
COLUMNS = ("map", "def_a", "def_b", "notes")


def target_points(d, o):
    """[(x, z)] for a defender standing at d with the attacker at o. Pure."""
    dx, dz = o[0] - d[0], o[1] - d[1]
    dab = math.hypot(dx, dz) or 1.0
    ux, uz = dx / dab, dz / dab
    px, pz = -uz, ux
    out = []
    for t, lat in TARGETS:
        s = max(-LAT_MAX, min(LAT_MAX, lat * dab))
        out.append((d[0] + t * dx + s * px, d[1] + t * dz + s * pz))
    return out


def pick_points(mi, d, o):
    """[(x, y, z, how)] for the five areas of a defender at d, or (None, why)."""
    np = fmospawns._np()
    g = mi.ground
    lab = mi.labels()
    cd = g.cell(*d)
    walk = int(lab[cd]) if cd is not None else 0
    coord = -g.half + g.step * np.arange(g.n)
    on = (np.abs(np.mod(coord, fmospawns.GRID)) < 1e-6) & \
         (np.abs(coord) <= fmospawns.EXTENT + 1e-6)
    grid = on[:, None] & on[None, :]
    X = coord[:, None] + 0.0 * coord[None, :]
    Z = 0.0 * coord[:, None] + coord[None, :]
    dab = math.hypot(o[0] - d[0], o[1] - d[1])
    reach = max(120.0, SEARCH_FRAC * dab)
    far = (np.hypot(X - d[0], Z - d[1]) >= SPAWN_KEEP) & \
          (np.hypot(X - o[0], Z - o[1]) >= SPAWN_KEEP)
    masks = []
    for tier in fmozones.ZONE_TIERS:
        ok, relief = g.clear_mask(*tier)
        masks.append((tier, ok & grid & far, relief))
    out = []
    for tx, tz in target_points(d, o):
        best = None
        for same in (True, False):
            for tier, ok, relief in masks:
                m = ok.copy()
                if same and walk:
                    m &= lab == walk
                for (px, _py, pz, _h) in out:
                    m &= np.hypot(X - px, Z - pz) >= POINT_APART
                dist = np.where(m, np.hypot(X - tx, Z - tz), np.inf)
                k = np.unravel_index(int(np.argmin(dist)), dist.shape)
                if np.isfinite(dist[k]) and dist[k] <= reach:
                    best = (float(X[k]), float(g.top[k]), float(Z[k]),
                            "%.0f off, disc %g, relief %.1f%s" % (
                                float(dist[k]), tier[0], float(relief[k]),
                                "" if same else ", NOT in the defender's walkable component"))
                    break
            if best is not None:
                break
        if best is None:
            return None, "no open ground within %.0f of (%.0f, %.0f)" % (reach, tx, tz)
        out.append(best)
    return out, None


def _fmt(points):
    return " ".join("%g:%g:%g" % (round(x, 1), round(y, 1), round(z, 1))
                    for x, y, z, _h in points)


def _work(job):
    root, m, spawn = job
    mi, why = fmospawns.load_map(root, m)
    if mi is None:
        return m, None, why
    a, b = spawn[0], spawn[1]
    pa, wa = pick_points(mi, a, b)
    pb, wb = pick_points(mi, b, a)
    if pa is None and pb is None:
        return m, None, "side A: %s; side B: %s" % (wa, wb)
    notes = []
    for side, pts, why in (("A", pa, wa), ("B", pb, wb)):
        if pts is None:
            notes.append("def_%s: %s" % (side.lower(), why))
        else:
            notes += ["def_%s: %s" % (side.lower(), h) for *_p, h in pts if "NOT" in h]
    return m, {"map": m, "def_a": _fmt(pa) if pa else "-",
               "def_b": _fmt(pb) if pb else "-", "notes": "; ".join(notes)}, None


def build(root, maps, jobs=1):
    spawns = fmozones.load_spawns()
    work = [(root, m, spawns[m]) for m in maps if m in spawns]
    skipped = [(m, "no spawn row") for m in maps if m not in spawns]
    if jobs > 1:
        import multiprocessing
        with multiprocessing.Pool(jobs) as pool:
            done = pool.map(_work, work, chunksize=1)
    else:
        done = [_work(w) for w in work]
    rows = []
    for m, row, why in sorted(done, key=lambda d: d[0]):
        if row is None:
            skipped.append((m, why))
        else:
            rows.append(row)
    return rows, skipped


def write_tsv(rows, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in sorted(rows, key=lambda r: r["map"]):
            f.write("\t".join(str(r[c]) for c in COLUMNS) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=fmospawns.DEFAULT_CLIENT)
    ap.add_argument("--out", default=DATA_DIR)
    ap.add_argument("--show", type=int, nargs="*")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    a = ap.parse_args()
    maps = a.show or fmozones.pvp_maps()
    rows, skipped = build(a.client, maps, 1 if a.show else a.jobs)
    for r in (rows if a.show else []):
        print("\n".join("  %-7s %s" % (c, r[c]) for c in COLUMNS))
        print()
    print("maps %d: %d rows, %d without" % (len(maps), len(rows), len(skipped)))
    for m, why in skipped:
        print("  map %d: %s" % (m, why))
    if not a.show:
        path = os.path.join(a.out, OUT_NAME)
        write_tsv(rows, path)
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
