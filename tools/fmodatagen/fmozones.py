#!/usr/bin/env python3
"""fmozones.py -- three CAPTURE & HOLD zones per Frontline battle map, out of
the map's collision ground and its spawn row.

    python fmozones.py --client <install or mirror Direct/> [--out DIR] [--jobs N]
    python fmozones.py --client ... --show 471 418     # print these maps only
    python fmozones.py --client ... --all              # every battle map, not only 505/509/513

Needs numpy, like fmospawns.py (whose ground reader this uses).

WHAT IT PICKS. SE's Capture & Hold (topics 2006-07-28) has three target areas
on a battle map, the same for both armies. Where SE put them is not in any file
we hold (the mission data came from the battle server), so the zones here are
OURS: one a quarter of the way from side A's spawn point to side B's, one at
the middle, one three quarters along. Each target is moved to the nearest grid
point that stands on open, flat ground (a clear disc of ZONE_CLEAR, nothing
raised, in side A's walkable component when one is near), at least
SPAWN_KEEP from either spawn point and ZONE_APART from the other zones.

The row's y is the ground under the zone centre; the server builds the rect
from it (see fmoserver/pvproom.py, P4).

OUTPUT: fmodata/fmo-capture-zones.tsv, one row per map:
  map      the battle map (type-1 id)
  zones    three x:y:z centres, A B C in order from side A to side B
  source   how each was found (the search ring it came from, its relief)
  notes    anything to doubt (a zone off the walkable component, a fallback)
"""
import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fmospawns                                                   # noqa: E402

DATA_DIR = fmospawns.DATA_DIR
OUT_NAME = "fmo-capture-zones.tsv"
SPAWNS = os.path.join(DATA_DIR, fmospawns.OUT_NAME)
PVP_SELECTORS = (505, 509, 513)
#: where along A -> B the three zones aim
TARGETS = (0.25, 0.5, 0.75)
#: the disc a zone centre needs clear (strict first, then looser), with the
#: step-up and drop fmospawns allows a side point
ZONE_TIERS = ((40.0, 6.0, 12.0), (32.0, 8.0, 16.0), (24.0, 8.0, 16.0))
#: a zone may move at most this fraction of the A-B distance off its target
SEARCH_FRAC = 0.2
SPAWN_KEEP = 200.0
ZONE_APART = 240.0
COLUMNS = ("map", "zones", "source", "notes")


def pvp_maps(all_maps=False):
    if all_maps:
        return fmospawns.battle_maps()
    sys.path.insert(0, fmospawns.SERVICES)
    import fmosectors                                              # noqa: E402
    out = set()
    for sel in PVP_SELECTORS:
        for _row, mapno in fmosectors.SECTORS.get(sel, {}).values():
            if mapno not in fmospawns.PLACEHOLDER_MAPS:
                out.add(mapno)
    return sorted(out)


def load_spawns(path=SPAWNS):
    rows = {}
    with open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            try:
                rows[int(r["map"])] = ((float(r["ax"]), float(r["az"])),
                                       (float(r["bx"]), float(r["bz"])), float(r["y"]))
            except (KeyError, ValueError):
                continue
    return rows


def pick_zones(mi, a, b):
    """[(x, y, z, how)] for the three zones, or (None, why)."""
    np = fmospawns._np()
    g = mi.ground
    lab = mi.labels()
    ca = g.cell(*a)
    walk = int(lab[ca]) if ca is not None else 0
    coord = -g.half + g.step * np.arange(g.n)
    on = (np.abs(np.mod(coord, fmospawns.GRID)) < 1e-6) & \
         (np.abs(coord) <= fmospawns.EXTENT + 1e-6)
    grid = on[:, None] & on[None, :]
    X = coord[:, None] + 0.0 * coord[None, :]
    Z = 0.0 * coord[:, None] + coord[None, :]
    dab = math.hypot(b[0] - a[0], b[1] - a[1])
    reach = max(160.0, SEARCH_FRAC * dab)
    far = (np.hypot(X - a[0], Z - a[1]) >= SPAWN_KEEP) & (np.hypot(X - b[0], Z - b[1]) >= SPAWN_KEEP)
    masks = []
    for tier in ZONE_TIERS:
        ok, relief = g.clear_mask(*tier)
        masks.append((tier, ok & grid & far, relief))
    out = []
    for t in TARGETS:
        tx, tz = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        best = None
        for same in (True, False):
            for tier, ok, relief in masks:
                m = ok.copy()
                if same and walk:
                    m &= lab == walk
                for (px, pz, _y, _h) in out:
                    m &= np.hypot(X - px, Z - pz) >= ZONE_APART
                d = np.where(m, np.hypot(X - tx, Z - tz), np.inf)
                k = np.unravel_index(int(np.argmin(d)), d.shape)
                if np.isfinite(d[k]) and d[k] <= reach:
                    best = (float(X[k]), float(g.top[k]), float(Z[k]),
                            "t %.2f: %.0f off target, disc %g, relief %.1f%s" % (
                                t, float(d[k]), tier[0], float(relief[k]),
                                "" if same else ", NOT in side A's walkable component"))
                    break
            if best is not None:
                break
        if best is None:
            return None, "no open ground within %.0f of the %.2f point (%.0f, %.0f)" % (
                reach, t, tx, tz)
        out.append(best)
    return out, None


def _work(job):
    root, m, spawn = job
    mi, why = fmospawns.load_map(root, m)
    if mi is None:
        return m, None, why
    zones, why = pick_zones(mi, spawn[0], spawn[1])
    if zones is None:
        return m, None, why
    notes = [h for *_p, h in zones if "NOT" in h]
    return m, {"map": m,
               "zones": " ".join("%g:%g:%g" % (round(x, 1), round(y, 1), round(z, 1))
                                 for x, y, z, _h in zones),
               "source": "; ".join(h for *_p, h in zones),
               "notes": "; ".join(notes)}, None


def build(root, maps, jobs=1):
    spawns = load_spawns()
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
    ap.add_argument("--all", action="store_true", help="every battle map, not only the PvP selectors'")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    a = ap.parse_args()
    maps = a.show or pvp_maps(a.all)
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
