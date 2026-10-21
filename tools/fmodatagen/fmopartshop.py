#!/usr/bin/env python3
"""fmopartshop.py -- every part's SHOP ROW, out of the client's own master tables.

    python fmopartshop.py --client <install> [--out fmo-part-shop.tsv]

WHY THIS EXISTS. The hangar economy runs on numbers SE shipped in the client:
the buy and sell price of every part, weapon and item, the nation and job
tier a shop row is restricted to, and the remodel (upgrade) limits. The server
needs them to price a remodel (fmoserver/economy.remodel_cost), to compute the
Battle Fee of a modified setup (economy.battle_fee), to check a sale price and
to tell which stocked parts belong to the other nation (the 0x019F sale list).

WHERE THE ROW IS (static, FrontMissionOnline.dll). Each master table is built
by 0x611A3F70 from four file ids in Data/AG/F21/D97.DAT; the FOURTH file is a
0x1C-byte row per id, reached by 0x611A4760 (via 0x611A4B30 from a part):
    +0x00 u32  BUY price. The shop list (0x61033D76 -> 0x6102FCB0) returns it
               and drops a row whose price is 0; the remodel cost (0x6103FA70)
               and the Battle Fee (0x61175CFF) both take it as their base.
    +0x04 u32  SELL price (75% of the buy price on every row read so far).
    +0x08 u8   RESTRICTION: digit % 10 = the nation the row is sold to (0 =
               both; 0x6102FCB0 compares it with lobby+0x8B4, the pilot's
               nation, and lets another nation's row through only when the
               0x019F list at lobby+0x8505 names it), digit / 10 = a job tier
               (0x61174BA0). 100 or more never lists.
    +0x09 u8   the category the remodel B.G. cost table is indexed by.
    +0x0C u8   the MAXIMUM TOTAL REMODEL RANK (SE update 2005-10-21: level 43+
               parts 5, advanced models 8; level 38+ 4 / 7).
    +0x0E + 2*i  s8  the per-stat remodel cap for stat i (0..5), read by
               0x6103FA70 as `movsx [row + i*2 + 0xE]`.
The record file's first three bytes are checked on every row (kind, then the
id as u16 at +2), so a wrong file id fails instead of producing plausible
prices.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402
import fmopartlevels as PL                                         # noqa: E402

ROW_SIZE = 0x1C
#: kind -> (record file id, name file id, shop row file id), from the
#: constructor arguments 0x611A4140 passes 0x611A3F70 (fourth = the rows).
SHOP_FILES = {
    0x11: (0x16, 0x31, 0x61), 0x21: (0x19, 0x34, 0x64), 0x31: (0x11, 0x30, 0x60),
    0x41: (0x18, 0x33, 0x63), 0x13: (0x10, 0x2F, 0x5F),
    0x12: (0x20, 0x3A, 0x6A), 0x22: (0x27, 0x41, 0x71), 0x32: (0x23, 0x3D, 0x6D),
    0x42: (0x1B, 0x35, 0x65), 0x52: (0x1E, 0x38, 0x68), 0x62: (0x24, 0x3E, 0x6E),
    0x72: (0x21, 0x3B, 0x6B), 0x82: (0x1D, 0x37, 0x67), 0x92: (0x1F, 0x39, 0x69),
    0xA2: (0x25, 0x3F, 0x6F), 0xB2: (0x22, 0x3C, 0x6C), 0xC2: (0x26, 0x40, 0x70),
    0xD2: (0x1C, 0x36, 0x66),
}
HEADER = ["kind", "id", "level", "buy", "sell", "restrict", "cat", "max_rank",
          "cap0", "cap1", "cap2", "cap3", "cap4", "cap5", "name"]


def rows(raw):
    out = []
    for kind, (rec_id, name_id, row_id) in sorted(SHOP_FILES.items()):
        recs = PL.blob(raw, rec_id)
        names = PL.names_of(PL.blob(raw, name_id))
        shop = PL.blob(raw, row_id)
        n = len(recs) // PL.RECORD_SIZE
        if len(shop) < n * ROW_SIZE:
            raise SystemExit("kind %#04x: %d shop bytes for %d records" % (kind, len(shop), n))
        for i in range(1, n + 1):
            r = recs[(i - 1) * PL.RECORD_SIZE:i * PL.RECORD_SIZE]
            if r[0] != kind or struct.unpack_from("<H", r, 2)[0] != i:
                raise SystemExit("kind %#04x id %d: record reads kind %#04x id %d; "
                                 "the file id map is wrong for this client"
                                 % (kind, i, r[0], struct.unpack_from("<H", r, 2)[0]))
            s = shop[(i - 1) * ROW_SIZE:i * ROW_SIZE]
            buy, sell = struct.unpack_from("<II", s, 0)
            caps = [struct.unpack_from("<b", s, 0x0E + 2 * k)[0] for k in range(6)]
            out.append([kind, i, r[PL.LEVEL_OFF], buy, sell, s[8], s[9], s[0x0C], *caps,
                        names[i - 1] if i - 1 < len(names) else ""])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", "--install", dest="client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", default="fmo-part-shop.tsv")
    a = ap.parse_args()
    path = fmofile.data_path(a.client, PL.REL)
    if not os.path.exists(path):
        raise SystemExit(f"no part master archive at {path}")
    raw = open(path, "rb").read()
    if raw[:4] != b"htar":
        raise SystemExit(f"{path}: magic {raw[:4]!r}, expected b'htar'")
    got = rows(raw)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(HEADER)
        for row in got:
            w.writerow(["0x%02X" % row[0]] + row[1:])
    priced = sum(1 for r in got if r[3])
    print(f"{len(got)} rows ({priced} priced) -> {a.out}")


if __name__ == "__main__":
    main()
