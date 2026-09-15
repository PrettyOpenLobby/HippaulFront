#!/usr/bin/env python3
"""Build services/fmodata/ from your own FRONT MISSION ONLINE client install.

One command runs the whole extraction pipeline:

  python tools/fmodata_build.py --client "C:/Program Files (x86)/PlayOnline/SquareEnix/FRONT MISSION ONLINE"

The repository ships none of Square Enix's data. Everything the server reads
from services/fmodata/ (except the one authored file, fmo-events.tsv) is
regenerated here from the client's own tables by the readers in
tools/fmodatagen/. Steps (each continues on failure; the summary says what
was produced):

  1. class       AI/F32/D15.DAT (plaintext)   -> fmo-class-exp.tsv, fmo-ranks.tsv
  2. cosmetics   BB/F13/D27..D31 (ITM)        -> fmo-cosmetics.tsv
  3. insignia    BB/F13/D31 (ITM)             -> fmo-insignia.tsv
  4. progression AI/F00/D16+D18 (MSG), AI/F08/D15 + D39..D48
                                              -> fmo-missions.tsv, fmo-cutscenes.tsv
  5. floorplans  the twelve lobby MAP containers -> floorplans/<mapno>.json
  6. npc-keys    AI/F08/D39..D48 event tables -> fmo-npc-keys.tsv
  7. script-marks AI/F00/D07, D87, AH/F99/D47 (SCP) -> fmo-script-marks.json
  8. backdrop    AJ/F40/D54 (TIM2)            -> services/boardart/fmo/backdrop.png
                 (the City Control board's satellite image; needs Pillow)

The install directory is the one holding PolBoot.exe, FrontMissionOnline.dll
and Data/; the Data directory itself is accepted too. Nothing is decrypted:
the resources are plain files under an 8-byte-block obfuscation that
tools/fmodatagen/fmofmdt.py undoes in memory.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "fmodatagen")
OUT_DEFAULT = os.path.normpath(os.path.join(HERE, "..", "services", "fmodata"))
BOARDART_DEFAULT = os.path.normpath(os.path.join(HERE, "..", "services", "boardart", "fmo"))
PY = sys.executable
results = []

EXPECTED = ["fmo-class-exp.tsv", "fmo-ranks.tsv", "fmo-cosmetics.tsv",
            "fmo-insignia.tsv", "fmo-missions.tsv", "fmo-cutscenes.tsv",
            "fmo-npc-keys.tsv", "fmo-script-marks.json"]
LOBBY_MAPNOS = (101, 102, 121, 122, 123, 124, 141, 142, 143, 144, 151, 161)


def step(name, argv, **kw):
    print(f"--- {name}: {' '.join(str(a) for a in argv[1:])}", flush=True)
    try:
        r = subprocess.run(argv, timeout=3600, **kw)
        ok = r.returncode == 0
    except Exception as e:                       # noqa: BLE001
        print(f"    {name}: {e}")
        ok = False
    results.append((name, ok))
    print(f"    {name}: {'ok' if ok else 'FAILED (continuing)'}", flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="your FRONT MISSION ONLINE install directory (contains Data/)")
    ap.add_argument("--out", default=OUT_DEFAULT,
                    help="where the tables go (default: services/fmodata)")
    ap.add_argument("--boardart", default=BOARDART_DEFAULT,
                    help="where backdrop.png goes (default: services/boardart/fmo)")
    ap.add_argument("--no-backdrop", action="store_true",
                    help="skip the City Control board backdrop")
    args = ap.parse_args()

    data = None
    for cand in (os.path.join(args.client, "Data"), args.client):
        if os.path.isdir(os.path.join(cand, "AI")):
            data = cand
            break
    if data is None:
        sys.exit(f"no Data/AI under {args.client} - point --client at the "
                 "FRONT MISSION ONLINE install directory")
    os.makedirs(args.out, exist_ok=True)
    client = ["--client", args.client]

    step("class", [PY, os.path.join(GEN, "fmoclass.py"), *client, "--out", args.out])
    step("cosmetics", [PY, os.path.join(GEN, "fmocosmetics.py"), *client,
                       "--out", os.path.join(args.out, "fmo-cosmetics.tsv")])
    step("insignia", [PY, os.path.join(GEN, "fmoinsignia.py"), *client,
                      "--out", os.path.join(args.out, "fmo-insignia.tsv")])
    step("progression", [PY, os.path.join(GEN, "fmoprogression.py"), *client,
                         "--out", args.out])
    step("floorplans", [PY, os.path.join(HERE, "fmo_floorplans.py"), *client,
                        "--out", os.path.join(args.out, "floorplans")])
    step("npc-keys", [PY, os.path.join(HERE, "fmo_npc_keys.py"), *client,
                      "--out", os.path.join(args.out, "fmo-npc-keys.tsv")])
    step("script-marks", [PY, os.path.join(HERE, "fmo_script_marks.py"), *client,
                          "--out", os.path.join(args.out, "fmo-script-marks.json")])
    if not args.no_backdrop:
        step("backdrop", [PY, os.path.join(HERE, "fmo_boardart_bake.py"), *client,
                          "--out", args.boardart])

    print()
    for name, ok in results:
        print(f"  {name:12} {'ok' if ok else 'FAILED'}")
    missing = [f for f in EXPECTED if not os.path.exists(os.path.join(args.out, f))]
    missing += ["floorplans/%d.json" % m for m in LOBBY_MAPNOS
                if not os.path.exists(os.path.join(args.out, "floorplans", "%d.json" % m))]
    if missing:
        print("still missing:", ", ".join(missing))
        sys.exit(1)
    print("fmodata complete:", args.out)


if __name__ == "__main__":
    main()
