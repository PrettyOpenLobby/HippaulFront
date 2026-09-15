#!/usr/bin/env python3
"""fmo_floorplans.py -- ship FMO's lobby floor plans as JSON for the NPC editor.

    python tools/fmo_floorplans.py --client <install>            # any install / mirror
    python tools/fmo_floorplans.py --client <install> --check    # regenerate to a temp
                                                                 # dir and diff

FMO has no minimap art. What its map containers DO carry is every prop's
world-space bounding box (fmodatagen/fmomap.world_boxes - validated twice
against a tester's feet on map 102: the two scramble consoles are 2 x 0.87 x 2
boxes whose platforms top out at exactly the y=3.50 the client reports for a
player standing on one). Drawn top-down those boxes ARE a floor plan, and the
box under a click gives the floor height a plan view otherwise lacks - which
is the thing that made offline editing possible for Fantasy Earth
(fe-capital-ground.json) and is the same thing here.

The server does not carry the game files, so this runs HERE, against a PC
install, and the result lands in services/fmodata/floorplans/<mapno>.json
exactly like FE's minimap PNGs land in fedata/minimaps. Regenerate when
fmomap.world_boxes changes; `--check` says whether the generated files are
current.

WHAT IS DROPPED. The raw scan returns every (min,max,w=1) pair in the file:
each box several times over (the same 12 x 12 platform appears four times)
and a handful of 200 m hulls (-100..100 on x and z, 5 m tall) that are not
props - drawn, they would paint the whole plan one colour. Duplicates are
collapsed and any box wider than FOOTPRINT_MAX on x or z is left out; the
counts of both are recorded in the file so the drop is visible, not silent.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fmodatagen"))

import fmofile  # noqa: E402
import fmomap  # noqa: E402
from fmoscriptcast import fmdt_decode  # noqa: E402

OUT_DIR = os.path.join(HERE, os.pardir, "services", "fmodata", "floorplans")
#: a box wider than this on x or z is a hull/volume, not a prop
FOOTPRINT_MAX = 60.0


def _rel(path):
    """path relative to this tools directory, or as given when it sits on
    another drive (os.path.relpath raises there on Windows)."""
    try:
        return os.path.relpath(path, HERE)
    except ValueError:
        return path


def floorplan(root, mapno):
    """The JSON-ready dict for one map, or None if the file is not there."""
    idx = fmofile.index_of(fmofile.TYPE_MAP, mapno)
    raw = fmomap.load(root, idx)
    if raw is None:
        return None
    boxes = fmomap.world_boxes(fmdt_decode(raw))
    seen, out, hulls = set(), [], 0
    for _o, lo, hi in boxes:
        b = tuple(round(v, 3) for v in (lo + hi))
        if b in seen:
            continue
        seen.add(b)
        if (b[3] - b[0]) > FOOTPRINT_MAX or (b[5] - b[2]) > FOOTPRINT_MAX:
            hulls += 1
            continue
        out.append(list(b))
    out.sort(key=lambda b: (b[1], b[0], b[2]))
    return {"mapno": mapno,
            "source": fmofile.path_of(idx).replace("\\", "/"),
            "generated": datetime.date.today().isoformat(),
            "tool": "tools/fmo_floorplans.py",
            "raw_boxes": len(boxes), "duplicates": len(boxes) - len(seen),
            "hulls_dropped": hulls, "footprint_max": FOOTPRINT_MAX,
            "boxes": out}


def write_all(root, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    done = []
    for mn in fmomap.LOBBY_MAPNOS:
        fp = floorplan(root, mn)
        if fp is None:
            print("%d: not in %s" % (mn, root))
            continue
        path = os.path.join(out_dir, "%d.json" % mn)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fp, fh, separators=(",", ":"))
        print("%d: %5d boxes (%d raw, %d dup, %d hulls) -> %s  %d KB"
              % (mn, len(fp["boxes"]), fp["raw_boxes"], fp["duplicates"],
                 fp["hulls_dropped"], _rel(path),
                 os.path.getsize(path) // 1024))
        done.append(mn)
    return done


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", "--root", dest="root", required=True,
                    help="the FRONT MISSION ONLINE install directory (contains Data/)")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--check", action="store_true",
                    help="regenerate into a temp dir and compare box lists "
                         "with what ships (exit 1 on a difference)")
    a = ap.parse_args(argv)
    if not a.check:
        write_all(a.root, a.out)
        return 0
    bad = 0
    for mn in fmomap.LOBBY_MAPNOS:
        fp = floorplan(a.root, mn)
        path = os.path.join(a.out, "%d.json" % mn)
        if fp is None or not os.path.isfile(path):
            print("%d: %s" % (mn, "no source" if fp is None else "NOT SHIPPED"))
            bad += 1
            continue
        with open(path, encoding="utf-8") as fh:
            have = json.load(fh)
        same = have.get("boxes") == fp["boxes"]
        print("%d: %s" % (mn, "current" if same else "STALE"))
        bad += not same
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
