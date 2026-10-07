#!/usr/bin/env python3
"""fmopartlevels.py -- every wanzer PART's LEVEL, out of the client's own master tables.

    python fmopartlevels.py --client <install> [--out fmo-part-levels.tsv]

WHY THIS EXISTS. A pilot's B.G.Cost is computed by the client from the level
of each part on the active setup (0x611AB620 -> 0x611E1F20 -> 0x611E1E10, see
fmoserver/battlegroups.py). The server stores a setup as 24-byte item records
(serial, id, kind) and needs the (kind, id) -> level table to run the same
formula. SE shipped it in the part master tables.

WHERE THE LEVEL IS (static, FrontMissionOnline.dll):
    0x611A4530(table, id)   -> the id's 48-byte record in the table's RECORD
                               file ((id-1)*48, id 1-based)
    0x611A4D90 (0x11), 0x611A4F10 (0x21), 0x611A5030 (0x31), 0x611A3DA0
    (0x41), 0x611AE380 (every kind whose low nibble is 2) each copy
    `record+1` into `part+1`, and 0x611AB620 reads `part+1` as the level.
So the level is byte +1 of the master record. Byte +0 is the kind and the u16
at +2 the id, which this tool checks on every row: a wrong file id would fail
that check rather than produce plausible levels.

THE FILES. `Data/AG/F21/D97.DAT` (resource 0xF2F5) is an `htar`: u32 count at
+0x08, then 0x10-byte entries at +0x10 of {u32 len<<8|flags, u32 offset, 0, 0},
each a compressed sub-blob (codec 0x61048BC0). The record/name file ids per
kind are 0x611A4140's constructor arguments (`push d, c, b, a; mov ecx, tbl;
call 0x611A3F70`), transcribed below; 0x611A3F70 sizes a table as record
file / 48.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402

REL = "AG/F21/D97.DAT"           # = resource 0xF2F5, see fmofile.py
RECORD_SIZE = 48                 # 0x611A3F70: count = size / 48
LEVEL_OFF = 0x01                 # record+1 -> part+1 (the loaders above)
#: [0x6138D288] -> the literal-run XOR key, applied by OUTPUT index & 0xF
XOR_KEY = b"SquareEnixCo.,Lt"

#: kind -> (record file id, name file id), from 0x611A4140. Kind 0x13
#: (repair items) is left out: 0x611A5130 has no loader for it, so it never
#: contributes a level.
PART_FILES = {
    0x11: (0x16, 0x31), 0x21: (0x19, 0x34), 0x31: (0x11, 0x30),
    0x41: (0x18, 0x33), 0x12: (0x20, 0x3A), 0x22: (0x27, 0x41),
    0x32: (0x23, 0x3D), 0x42: (0x1B, 0x35), 0x52: (0x1E, 0x38),
    0x62: (0x24, 0x3E), 0x72: (0x21, 0x3B), 0x82: (0x1D, 0x37),
    0x92: (0x1F, 0x39), 0xA2: (0x25, 0x3F), 0xB2: (0x22, 0x3C),
    0xC2: (0x26, 0x40), 0xD2: (0x1C, 0x36),
}


def decompress(raw, offset):
    """0x61048BC0: a 2-bit-opcode byte LZ. Header u32 low 24 bits = the
    output size - 8. op 0 = literal run XORed with XOR_KEY by output index,
    op 1 = RLE, op 2 = back-reference (copied a dword at a time, like the
    client's `rep movsd`, so a short distance reads what the pass has not
    written yet)."""
    limit = struct.unpack_from("<I", raw, offset)[0] & 0xFFFFFF
    out = bytearray(limit + 16)
    sp, d = offset + 4, 0
    while d < limit:
        c = raw[sp]
        op = c & 3
        if op == 0:
            n = (c >> 2) + 1
            for i in range(n):
                out[d + i] = raw[sp + 1 + i] ^ XOR_KEY[i & 0xF]
            sp += n + 1
        elif op == 1:
            n = (c >> 2) + 3
            out[d:d + n] = bytes([raw[sp + 1]]) * n
            sp += 2
        elif op == 2:
            n = (c >> 2) + 3
            s = d - raw[sp + 1] - 3
            if s < 0:
                raise ValueError("back-reference before the start at out=%d" % d)
            for k in range(n >> 2):
                out[d + k * 4:d + k * 4 + 4] = out[s + k * 4:s + k * 4 + 4]
            for k in range((n >> 2) * 4, n):
                out[d + k] = out[s + k]
            sp += 2
        else:
            raise ValueError("opcode 3 at +%d: not a stream the client wrote" % sp)
        d += n
    return bytes(out[:limit + 8])


def blob(raw, fid):
    count = struct.unpack_from("<I", raw, 8)[0]
    if not 0 <= fid < count:
        raise SystemExit("file id %#x is outside the archive's %d entries" % (fid, count))
    return decompress(raw, struct.unpack_from("<4I", raw, 0x10 + fid * 0x10)[1])


def names_of(b):
    """0x611A45E0's string table: u32 offsets from the blob start; the first
    offset bounds the table."""
    if len(b) < 4:
        return []
    n = struct.unpack_from("<I", b, 0)[0] // 4
    out = []
    for i in range(n):
        o = struct.unpack_from("<I", b, i * 4)[0]
        e = b.find(b"\0", o)
        out.append(b[o:e if e >= 0 else len(b)].decode("cp932", "replace")
                   if o < len(b) else "")
    return out


def rows(raw):
    out = []
    for kind, (rec_id, name_id) in sorted(PART_FILES.items()):
        recs = blob(raw, rec_id)
        names = names_of(blob(raw, name_id))
        n = len(recs) // RECORD_SIZE
        for i in range(1, n + 1):
            r = recs[(i - 1) * RECORD_SIZE:i * RECORD_SIZE]
            if r[0] != kind or struct.unpack_from("<H", r, 2)[0] != i:
                raise SystemExit("kind %#04x id %d: record reads kind %#04x id %d; "
                                 "the file id map is wrong for this client"
                                 % (kind, i, r[0], struct.unpack_from("<H", r, 2)[0]))
            out.append((kind, i, r[LEVEL_OFF], names[i - 1] if i - 1 < len(names) else ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", "--install", dest="client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", default="fmo-part-levels.tsv")
    a = ap.parse_args()
    path = fmofile.data_path(a.client, REL)
    if not os.path.exists(path):
        raise SystemExit(f"no part master archive at {path}")
    raw = open(path, "rb").read()
    if raw[:4] != b"htar":
        raise SystemExit(f"{path}: magic {raw[:4]!r}, expected b'htar'")
    got = rows(raw)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["kind", "id", "level", "name"])
        for kind, i, lvl, name in got:
            w.writerow(["0x%02X" % kind, i, lvl, name])
    per = {}
    for kind, _i, lvl, _n in got:
        per.setdefault(kind, []).append(lvl)
    print(f"{len(got)} parts -> {a.out}")
    for kind, lv in sorted(per.items()):
        print(f"  kind 0x{kind:02X}: {len(lv)} ids, level {min(lv)}..{max(lv)}")


if __name__ == "__main__":
    main()
