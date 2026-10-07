#!/usr/bin/env python3
"""fmosectorranks.py -- each war-map sector's NPC RANK and home nation, out of the client's ARE tables.

    python fmosectorranks.py --client <install>      # prints the fmosectors.py _RANKS blob

WHY THIS EXISTS. The enemy squad's level was 5 x the sector's B.G.Cost from the
war state, and nothing ever wrote a B.G.Cost, so every battle on the map ran at
FMO_ENEMY_LEVEL (15) -- a rank-1 rear sector included. The war map has always
SHOWN the player a per-sector difficulty ("NPC Rank : %s" and the sector tint);
it comes from the client's own ARE row, not from us.

THE SOURCE. ARE container = resource 82159 + selector (loader 0x61173FE0), rows
of 0x70 from dec+0x8C, count at dec+0x20. Row +0x00 u32 tile, +0x04 u16 battle
map, and (static 2026-10-02):
  +0x68 u8  NPC RANK 0..5. The panel copies the row to panel+0x60 (0x61188BBA)
            and its drawer reads panel+0xC8 at 0x6118B1D0: >= 100 skips the
            line, 0..5 print "0".."5" (systext 10:31), 6..99 print "?". The
            map tint 0x61023E50 switches on the same byte (> 5 = one colour).
            Frontline zones (505/509/513) hold 100 in every row: no rank.
  +0x69 u8  home nation, 0 = either. 0x6118D9C0 refuses a sector whose byte
            has the OTHER nation's bit; 0x61023E6C greys it.
The client never turns the rank into a level; SE's 050719 note ("NPC level 25
= sector B.G.Cost 5") is the only link, and squad.py owns that step.

OUTPUT. One line per selector, `selector|tile:rank:nation ...`, only rows with a
battle map (the rows fmosectors keeps) and a rank 0..5. Paste over fmosectors._RANKS.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402
import fmofmdt                                                     # noqa: E402

ARE_BASE = 82159
ROW0, ROW = 0x8C, 0x70
R_RANK, R_NATION = 0x68, 0x69
MAX_RANK = 5
NO_MAP = (0, 1)        # fmosectors keeps every other map id, 2..5 included


def load_are(client, selector):
    """The decoded ARE container for `selector`, or None."""
    rel = fmofile.rel_of(ARE_BASE + selector)
    try:
        raw = fmofmdt.pristine(fmofile.data_path(client, rel))
    except OSError:
        return None
    if raw[:4] != b"FMDT":
        return None
    dec = fmofmdt.decode(raw[16:])
    return dec if dec[:4] == b"ARE\x00" else None


def ranks(dec):
    """[(tile, rank, nation)] for the rows with a battle map and a rank 0..5."""
    count = struct.unpack_from("<I", dec, 0x20)[0]
    out = []
    for i in range(count):
        o = ROW0 + i * ROW
        if o + ROW > len(dec):
            break
        tile, mapno = struct.unpack_from("<IH", dec, o)
        rank, nation = dec[o + R_RANK], dec[o + R_NATION]
        if mapno not in NO_MAP and rank <= MAX_RANK:
            out.append((tile, rank, nation))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", "--install", dest="client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--limit", type=int, default=1024)
    a = ap.parse_args()
    for sel in range(a.limit):
        dec = load_are(a.client, sel)
        if dec is None:
            continue
        rows = ranks(dec)
        if rows:
            print("%d|%s" % (sel, " ".join("%d:%d:%d" % r for r in rows)))


if __name__ == "__main__":
    main()
