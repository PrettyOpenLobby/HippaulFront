#!/usr/bin/env python3
"""fmoclass.py -- FMO's rank ladder and class-experience curve, from Data/AI/F32/D15.DAT.

    python fmoclass.py --client <install>            # print both tables
    python fmoclass.py --client <install> --out DIR  # write fmo-class-exp.tsv + fmo-ranks.tsv into DIR

THE FILE (resource 0x1450F = 83215 -> AI/F32/D15.DAT, 19,184 B, PLAINTEXT, not FMDT):
The client loads it at 0x611E4440 and parses it at 0x611E4270 into the object at
0x613CA3E8, with the layout written in its own sprintf format strings:

    +0x00  u32  n_ranks   (48)          "%d(28ci56ci4c7i)" -> the rank rows
    +0x04  u32  n_classes (8)
    +0x08  u32  max_level - 1 (99)      "%di" -> the threshold ints
    +0x0C  u32  100
    +0x20  n_ranks x 0x7C  RANK ROW: 28c name, i CONTRIBUTION THRESHOLD (s32),
                           56c grade ("Enl", "NCO", "WO", "Co.Off", "Fld.Of",
                           "GenOff"), i (1000..73000: a cap, unread), 4c, 7i
                           (the last = display order)
    +0x1760  4 groups x 8 classes x 100 u32  EXP THRESHOLDS, [group][class][level-1]
    +0x4960  100 u32  the "%di" tail (unread)

THE LEVEL FUNCTION 0x611E40A0(group, class, exp) -> level:
    threshold(L) = table[group][class][L-1]; L = 1 is the floor and L in
    2..100 is the largest with threshold(L) <= exp (0x611E4040 returns
    INT_MAX past level 100). `group` is byte[lobby+0x8B5] - 1 (0x014A
    payload+0x29); `class` is kind-1 for kinds 1..11, and for kind 12 (the
    Pilot row) the class byte[0x613CA3E4 + group] picks. All four groups are
    BYTE-IDENTICAL in the shipped file and every class shares the curve above
    level 1 (level-1 thresholds 0/10/20/100 are below the floor), so ONE curve
    describes the game: Lv2 = 82,800, Lv3 = 182,160, ... Lv100 = 1,103,015,946.

THE KINDS (name tables 0x61395C74 / 0x61395CA4, systext 8:37..44 / 8:95..106):
    1 Assault  2 Missileer  3 Mechanic  4 Recon  5 Sniper  6 Comms  7 Jammer
    8 Joker  9..11 Reserved  12 Pilot
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402

REL = "AI/F32/D15.DAT"
ROW = 0x7C
KINDS = {1: "Assault", 2: "Missileer", 3: "Mechanic", 4: "Recon", 5: "Sniper",
         6: "Comms", 7: "Jammer", 8: "Joker", 9: "Reserved", 10: "Reserved",
         11: "Reserved", 12: "Pilot"}


def parse(raw):
    n_ranks, n_cls, maxm1, _c = struct.unpack_from("<4I", raw, 0)
    ranks = []
    for r in range(n_ranks):
        o = 0x20 + r * ROW
        name = raw[o:o + 28].split(b"\0")[0].decode("cp932", "replace")
        contrib = struct.unpack_from("<i", raw, o + 28)[0]
        grade = raw[o + 32:o + 88].split(b"\0")[0].decode("cp932", "replace").strip()
        cap = struct.unpack_from("<I", raw, o + 88)[0]
        ints = struct.unpack_from("<7I", raw, o + 96)
        # `r` IS the wire byte: the client's parser (0x611E4270) keeps a pointer
        # to file+0x20 (this row 0, Conscript) and 0x6109E8C0 indexes it by the
        # raw rank byte, so byte 0 = Conscript and byte 21 = Major. Until
        # 2026-09-12 this wrote r + 1 and every rank name was one row high.
        ranks.append((r, name, contrib, grade, cap, ints))
    thr = 0x20 + n_ranks * ROW
    stride = (maxm1 + 1) * n_cls * 4
    groups = []
    for g in range(4):
        cls = []
        for c in range(n_cls):
            base = thr + g * stride + (maxm1 + 1) * c * 4
            cls.append(struct.unpack_from(f"<{maxm1 + 1}I", raw, base))
        groups.append(cls)
    return ranks, groups, maxm1 + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", help="write fmo-class-exp.tsv and fmo-ranks.tsv into this directory")
    a = ap.parse_args()
    raw = open(fmofile.data_path(a.client, REL), "rb").read()
    ranks, groups, levels = parse(raw)
    same = all(groups[g] == groups[0] for g in range(4))
    curve = groups[0][0]
    out = a.out
    lines_exp = ["level\texp"] + [f"{i + 1}\t{v}" for i, v in enumerate(curve)]
    # `pay` = row+0x58 (the u32 once labelled "a cap, unread") and `mp` =
    # row+0x64 (ints[1]): the Personal Ratings screen (0x61191BC4) prints
    # them as 11:26 "H$ %d" and 11:27 ": MP" for the pilot's rank, and the
    # next rank's difference as "(+%d)" - a per-rank H$ / MP allowance, i.e.
    # SE's rank-scaled salary (guide: 給与は階級が上がると増える). Static 2026-09-12.
    lines_rank = ["rank\tname\tcontribution\tgrade\tpay\tmp\torder"] + [
        f"{r}\t{n}\t{c}\t{g}\t{cap}\t{ints[1]}\t{ints[6]}" for r, n, c, g, cap, ints in ranks]
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "fmo-class-exp.tsv"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines_exp) + "\n")
        with open(os.path.join(out, "fmo-ranks.tsv"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines_rank) + "\n")
        print(f"wrote {out}/fmo-class-exp.tsv ({levels} levels) and fmo-ranks.tsv "
              f"({len(ranks)} ranks); groups identical: {same}")
    else:
        print("\n".join(lines_rank))
        print("\n".join(lines_exp[:12]), "...")
        print("groups identical:", same)
    return 0


if __name__ == "__main__":
    sys.exit(main())
