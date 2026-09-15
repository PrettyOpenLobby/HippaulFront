#!/usr/bin/env python3
"""fmo_script_marks.py -- SE's own placement marks from the lobby scripts, shipped
for the NPC editor's "SE's marks" overlay.

    python tools/fmo_script_marks.py --client <install>            # writes services/fmodata/fmo-script-marks.json
    python tools/fmo_script_marks.py --client <install> --check    # exit 1 if the generated file is stale

Each lobby band runs ONE client script (the LEV table, AI/F08/D15): the HQ and
occupation lobbies run AI/F00/D87, the frontline lobbies the tutorial's
AI/F00/D07, the Coliseum AH/F99/D47. A script PLACES its cast with syscall
0xE285 (id, x, y, z), FACES it with 0xE291 (id, degrees) and CREATES it with
0xE280 - and a mark that is placed but NEVER created is a slot the script
expects the SERVER to have filled: SE's design showing through. Those are the
interesting ones for placement; the created ones are cutscene staging and
still say where a person can stand.

Decoded by fmodatagen/fmoscriptcast.py's own parser (stage_scan), nothing
re-implemented here. Coordinates are in the client's world frame, proved
against a tester's feet (0.29 m) on map 102.
"""
import argparse
import json
import os
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fmodatagen"))

import fmoscriptcast as C  # noqa: E402

OUT = os.path.join(HERE, os.pardir, "services", "fmodata", "fmo-script-marks.json")
SCRIPTS = (("hq", "AI/F00/D87.DAT"), ("occ", "AI/F00/D87.DAT"),
           ("fz", "AI/F00/D07.DAT"), ("col", "AH/F99/D47.DAT"))


def marks_of(client, rel):
    scp = C.load_scp(client, rel)
    cast = OrderedDict()
    created = set()
    for rec, cid in C.find_syscalls(scp, {0xE280, 0xE285, 0xE291}):
        window, _w, stores = C.stage_scan(scp, rec)
        if window is None:
            continue

        def param(off):
            loc = window + off
            return stores[loc][0] if loc in stores else None
        sid = param(0)
        if sid is None:
            continue
        if cid == 0xE280:
            created.add(sid)
        elif cid == 0xE285:
            x, y, z = param(0x1C), param(0x20), param(0x24)
            if None in (x, y, z):
                continue
            e = cast.setdefault(sid, {"places": 0})
            e["pos"] = [round(C.coord(v), 2) for v in (x, y, z)]
            e["places"] += 1
        elif cid == 0xE291:
            ang = param(0x5C)
            if ang is not None:
                cast.setdefault(sid, {"places": 0})["face"] = C.s32(ang) % 360
    out = []
    for sid, e in cast.items():
        if "pos" not in e:
            continue
        key, note = C.npc_key(sid)
        out.append({"sid": sid, "key": key, "note": note,
                    "x": e["pos"][0], "y": e["pos"][1], "z": e["pos"][2],
                    "face": e.get("face"), "places": e["places"],
                    "created": sid in created})
    return out


def build(client):
    data = {}
    for band, rel in SCRIPTS:
        data[band] = {"script": rel, "marks": marks_of(client, rel)}
    return data


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    data = build(a.client)
    text = json.dumps(data, indent=0, sort_keys=True)
    if a.check:
        try:
            have = open(a.out, encoding="utf-8").read()
        except OSError:
            have = None
        print("current" if have == text else "STALE")
        return 0 if have == text else 1
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    for band, d in data.items():
        ms = d["marks"]
        print("%-4s %-16s %3d marks, %2d never created (server slots)"
              % (band, d["script"], len(ms), sum(1 for m in ms if not m["created"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
