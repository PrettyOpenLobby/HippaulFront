#!/usr/bin/env python3
"""fmonpcloadouts.py -- the COM (NPC) enemies' loadouts, out of the client's own part tables.

    python fmonpcloadouts.py --client <install> [--out fmo-npc-loadouts.tsv ...]

WHY THIS EXISTS. The enemy squad (fmoserver/squad.py) used to pop every enemy
in the viewing pilot's own garage parts: a battle against copies of yourself.
The battle dresser 0x611F70A0 resolves each POP part record (u16 id, u8 kind
at body+0x8C, stride 0xC) straight against the part master tables with no
ownership check, so any id works, including the NPC-only sets SE shipped in
the same tables. This tool lists those sets, one wanzer per row, plus the
vehicles, so the server can dress an enemy for the battle's NPC level.

THE SOURCES (all in Data/AG/F21/D97.DAT, read through fmopartlevels.py):
  wanzer parts  kinds 0x11 body / 0x21 legs / 0x31 arms. One id is one SET
                across the three (id 196 is npc60-1 in all of them).
  NPC sets      body names npc60-*, zora_ev1_*, RECN/JAMR/SNPR/COMS<n>[SP]-OCU
                /-USN and WAP###. The -OCU/-USN suffix is the only nation
                marker; the rest serve either side.
  weapons       the generic-named NPC weapons: 'Machinegun' (0x12), 'Shotgun'
                (0x22), 'Rifle' (0x32), 'Missile' (0x72), levels 10..45 in
                steps of 5. The first run of each name is used.
  backpack      BP-C01..04 (0x41) by level.
  vehicles      record 0's kind with bit 3 set picks the model in 0x611ED660
                (hi nibble 1 -> 1, 2 -> 3, 3 -> 2, 5 -> 6). The frame is
                resolved by 0x611A4BC0 in its own tables, constructed in
                0x611A4140: 0x19 -> file 0x13 (48 records), 0x29 -> file 0x15
                (130 = 16 meshes x 8 tiers), 0x39 -> file 0x14 (195 = 24
                meshes x 8 tiers). These tables have no names.

ROW SHAPE (tab separated):
  kind    wanzer | tank | heli | boss
  level   the set's level (the body record's byte +1, or the frame's)
  nation  1 (O.C.U.), 2 (U.S.N.) or 0 (either)
  role    RECN / JAMR / SNPR / COMS / WAP / npc60 / zora, or the vehicle kind
  name    the body's name, or '<kind> mesh <n>' for a vehicle
  hp      the body's (frame's) record HP (+6), for reading the table only
  parts   space separated <item index>=<kind hex>:<id>, the POP records

WARNING: what stays inferred. Which model 0x29 and 0x39 are (tank /
helicopter) and which 0x19 frame is K.O.N.G. X-II or Algem was never seen on
a screen. Vehicle WEAPONS resolve (records 1..9 get kind | 8, 0x611F7187) in
a separate set (0x611A4080) that is not decoded, so vehicle rows carry no
weapon; the sensor backpack sensor_tank_N / sensor_heli_N follows the tier
by position only.
"""
import argparse
import csv
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402
import fmopartlevels as P                                          # noqa: E402

#: vehicle model kind -> (record file id, role name, id filter). The file ids
#: are 0x611A4140's `push -1, -1, -1, <file>; mov ecx, <table>` lines for the
#: tables 0x611A4BC0 dispatches to (0x613C1398 / 0x613C1448 / 0x613C0E14).
VEHICLE_FILES = {0x29: (0x15, "tank"), 0x39: (0x14, "heli"), 0x19: (0x13, "boss")}
#: The ids that make up the 8-tier grids. The records outside them are one-offs
#: at level 1 (tank 129-130, heli 25-27) and each grid's last row repeats some
#: meshes at level 1; both are left out by the level > 1 rule below.
VEHICLE_GRID = {0x29: range(1, 129), 0x39: range(1, 196), 0x19: range(1, 49)}
#: backpack name stem per vehicle role, tier 1..8 = sensor_<stem>_1..8
VEHICLE_BACKPACK = {"tank": "sensor_tank_", "heli": "sensor_heli_",
                    "boss": "sensor_boss_"}

NPC_SET = re.compile(r"^(npc60|zora_ev1|RECN|JAMR|SNPR|COMS|WAP)")
ROLE_WEAPON = {           # role -> [(item index, kind, generic name)]
    "SNPR": [(4, 0x32, "Rifle")],
    "JAMR": [(4, 0x22, "Shotgun")],
    "COMS": [(4, 0x12, "Machinegun"), (7, 0x72, "Missile")],
}
DEFAULT_WEAPON = [(4, 0x12, "Machinegun")]


def records(raw, fid):
    b = P.blob(raw, fid)
    return [b[i:i + P.RECORD_SIZE] for i in range(0, len(b) - P.RECORD_SIZE + 1, P.RECORD_SIZE)]


def named(raw, kind):
    """[(id, level, hp, mesh, name)] for one named part kind."""
    rec_id, name_id = P.PART_FILES[kind]
    names = P.names_of(P.blob(raw, name_id))
    out = []
    for i, r in enumerate(records(raw, rec_id), 1):
        out.append((i, r[1], struct.unpack_from("<H", r, 6)[0],
                    struct.unpack_from("<H", r, 4)[0],
                    names[i - 1] if i - 1 < len(names) else ""))
    return out


def weapon_tiers(raw, kind, name):
    """{level: id} for the FIRST run of a generic NPC weapon name."""
    out = {}
    for i, lvl, _hp, _m, n in named(raw, kind):
        if n.strip() == name and lvl not in out:
            out[lvl] = i
    return out


def at_or_below(tiers, level):
    """The value of the highest key <= level, else of the lowest key."""
    if not tiers:
        return None
    ks = sorted(tiers)
    best = [k for k in ks if k <= level]
    return tiers[best[-1] if best else ks[0]]


def grid_tier(kind, i):
    """1..8: the row of the 8-tier grid vehicle id `i` sits in (tank rows of
    16, heli rows of 24 after a first row of 27; the 0x19 frames are one
    level, 20, and use sensor_boss_1)."""
    if kind == 0x29:
        return min(8, (i - 1) // 16 + 1)
    if kind == 0x39:
        return 1 if i <= 27 else min(8, (i - 28) // 24 + 2)
    return 1


def fmt_parts(parts):
    return " ".join("%d=%02X:%d" % (i, k, d) for i, k, d in parts)


def build(raw):
    rows = []
    bodies = named(raw, 0x11)
    legs = {i: n for i, _l, _h, _m, n in named(raw, 0x21)}
    arms = {i: n for i, _l, _h, _m, n in named(raw, 0x31)}
    packs = {n: i for i, _l, _h, _m, n in named(raw, 0x41)}
    bp_c = {}
    for i, lvl, _h, _m, n in named(raw, 0x41):
        if re.match(r"^BP-C0\d$", n):
            bp_c[lvl] = i
    wtiers = {(k, n): weapon_tiers(raw, k, n)
              for spec in list(ROLE_WEAPON.values()) + [DEFAULT_WEAPON]
              for _i, k, n in spec}
    seen = set()
    for i, lvl, hp, mesh, name in bodies:
        m = NPC_SET.match(name)
        if not m:
            continue
        # One id is one set: the legs and arms at this id must be the same
        # set, or the row would dress a mixed wanzer.
        if legs.get(i) != name or arms.get(i) != name:
            raise SystemExit("id %d: body %r, legs %r, arms %r -- not one set"
                             % (i, name, legs.get(i), arms.get(i)))
        key = (name, lvl, hp, mesh)
        if key in seen:             # 207-210 are four identical RECN1-OCU
            continue
        seen.add(key)
        role = m.group(1)
        nation = 1 if name.endswith("-OCU") else 2 if name.endswith("-USN") else 0
        parts = [(0, 0x11, i), (1, 0x21, i), (2, 0x31, i), (3, 0x31, i)]
        for idx, k, wn in ROLE_WEAPON.get(role, DEFAULT_WEAPON):
            w = at_or_below(wtiers[(k, wn)], lvl)
            if w:
                parts.append((idx, k, w))
        bp = at_or_below(bp_c, lvl)
        if bp:
            parts.append((10, 0x41, bp))
        rows.append(("wanzer", lvl, nation, role, name, hp, fmt_parts(parts)))
    for kind, (fid, role) in sorted(VEHICLE_FILES.items()):
        recs = records(raw, fid)
        seen = set()
        for i in VEHICLE_GRID[kind]:
            r = recs[i - 1]
            if r[0] != kind or struct.unpack_from("<H", r, 2)[0] != i:
                raise SystemExit("vehicle kind %#04x id %d reads kind %#04x id %d"
                                 % (kind, i, r[0], struct.unpack_from("<H", r, 2)[0]))
            lvl, mesh, hp = r[1], struct.unpack_from("<H", r, 4)[0], struct.unpack_from("<H", r, 6)[0]
            if lvl <= 1 or (mesh, lvl) in seen:
                continue
            seen.add((mesh, lvl))
            parts = [(0, kind, i)]
            bp = packs.get(VEHICLE_BACKPACK[role] + str(grid_tier(kind, i)))
            if bp:
                parts.append((10, 0x41, bp))
            rows.append((role, lvl, 0, role, "%s mesh %d" % (role, mesh), hp, fmt_parts(parts)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", "--install", dest="client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", action="append",
                    help="output path (repeatable); default fmo-npc-loadouts.tsv")
    a = ap.parse_args()
    path = fmofile.data_path(a.client, P.REL)
    if not os.path.exists(path):
        raise SystemExit(f"no part master archive at {path}")
    raw = open(path, "rb").read()
    if raw[:4] != b"htar":
        raise SystemExit(f"{path}: magic {raw[:4]!r}, expected b'htar'")
    rows = build(raw)
    for out in a.out or ["fmo-npc-loadouts.tsv"]:
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["kind", "level", "nation", "role", "name", "hp", "parts"])
            w.writerows(rows)
        print(f"{len(rows)} loadouts -> {out}")
    per = {}
    for r in rows:
        per.setdefault(r[0], []).append(r[1])
    for k, lv in sorted(per.items()):
        print(f"  {k}: {len(lv)} rows, level {min(lv)}..{max(lv)}")


if __name__ == "__main__":
    main()
