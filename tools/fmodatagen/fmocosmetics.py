#!/usr/bin/env python3
"""fmocosmetics.py -- bank SE's own COSMETIC catalogues as a TSV the server can
serve in 0x01A3, without the container needing the client install.

    python fmocosmetics.py --client <install> --out services/fmodata/fmo-cosmetics.tsv
    python fmocosmetics.py --client <install> --dump 1   # print one kind, as the server will see it

WHY. The wanzer SETUP.CONSOLE and the PILOT.LOCKER both open with lobby-API
request 0x01A2 (measured in a live session 2026-09-11: the pilot screen sends
byte **1**, the wanzer screen byte **2**), and its reply 0x01A3 is a SHOP
CATALOGUE: `u32 count` at +0x00 and, from +0x44, up to 1800 entries of

    {u16 item id, u8 kind, u8 unused, u32 price}

We answered 14,468 zeros - count 0, an empty catalogue - so the pickers had
no rows, and the tester's outfit change fell back to a default body ("it
turns me back into the stewardess", preview only; nothing was stored).

THE KINDS ARE THE FIVE TABLES, in the order the screens' own name-table loads
resolve them (`fmofile.path_of` on the resource ids at 0x610286D5/0x61028615
and 0x6102D745/0x6102D805/0x6102D8C5):

    0  Data/BB/F13/D27.DAT   accessories   210 records
    1  Data/BB/F13/D28.DAT   pilot suits   600     -- request byte 1 (pilot)
    2  Data/BB/F13/D29.DAT   camouflage    300
    3  Data/BB/F13/D30.DAT   colours       372     -- request byte 2 (wanzer)
    4  Data/BB/F13/D31.DAT   emblems      1000

RECORD SHAPE (fmoitm.py, measured 2026-09-04): +0x00 u32 id, +0x04 u32 flag,
+0x08 char[0x20] SE's ENGLISH name, +0x28 u32, +0x2c u32, +0x30 the Japanese
display name. Stride 0x74, or 0x78 for D28.

KEY: `flag` IS THE NATION, not a gender. `Garrison Cap` and `Officer's Dress
Hat` each appear TWICE, once with flag 1 and once with flag 2; D31's rows carry
the same 1/2 split that `fmodata/fmo-insignia.tsv` already calls `nation_flag`,
and the client's insignia builder compares that byte for equality against
`byte[lobby+0x8B4]` (0x611BE344). Flag 0 (16 D27 rows: hats, glasses) is
nation-neutral. **flag 255 is a PLACEHOLDER record** - its name is an internal
tag (`HEAD_F91`) or a filler (`Pilot Suit 41`), and those are dropped here.

WARNING: THERE IS NO PRICE IN THESE TABLES. +0x28 and +0x2c are a small index
and a model id (D28 id 1 -> 17/102), neither of which behaves like money -
unlike the PART tables, whose fourth file really is 0x1C bytes per id with the
buy price at +0x00. So what SE charged for a hat is NOT in the client, and the
server has to supply it; `FMO_COSMETIC_PRICE` is that number and it is OURS,
not SE's. The one attested cosmetic price anywhere is the 2nd Huffman Conflict
service medal at H$10 (playonline.com/fmo topics/060428).
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmoitm  # noqa: E402

#: kind -> (file, label). The kind byte is what the 0x01A3 entry carries and
#: what the client's own list builders switch on.
KINDS = {
    0: ("BB/F13/D27.DAT", "accessories"),
    1: ("BB/F13/D28.DAT", "pilot suits"),
    2: ("BB/F13/D29.DAT", "camouflage"),
    3: ("BB/F13/D30.DAT", "colours"),
    4: ("BB/F13/D31.DAT", "emblems"),
}
PLACEHOLDER = 255


def rows_for(kind, client):
    """[(id, nation_flag, english)] for one kind, placeholders dropped."""
    path, _ = KINDS[kind]
    _raw, dec = fmoitm.load(path, client)
    recs, stride = fmoitm.parse(dec)
    out = []
    for off in recs:
        r = dec[off:off + stride]
        rid, flag = struct.unpack_from("<II", r, 0)
        if flag == PLACEHOLDER:
            continue
        en = fmoitm.cstr(r[0x08:0x28]).decode("cp932", "replace").strip()
        if not en:
            continue
        out.append((rid, flag, en))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--dump", type=int, help="print one kind instead of writing")
    ap.add_argument("--out", default="fmo-cosmetics.tsv")
    a = ap.parse_args()

    if a.dump is not None:
        for rid, flag, en in rows_for(a.dump, a.client):
            print(f"  kind {a.dump}  id={rid:<5d} nation={flag}  {en}")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    n = 0
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("kind\tid\tnation_flag\tenglish\n")
        for kind in sorted(KINDS):
            for rid, flag, en in rows_for(kind, a.client):
                f.write(f"{kind}\t{rid}\t{flag}\t{en}\n")
                n += 1
    print(f"{a.out}: {n} rows")
    for kind in sorted(KINDS):
        rs = rows_for(kind, a.client)
        print(f"  kind {kind} {KINDS[kind][1]:<12s} {len(rs):4d} real rows "
              f"(nation 0/1/2 = "
              f"{sum(1 for _, fl, _ in rs if fl == 0)}/"
              f"{sum(1 for _, fl, _ in rs if fl == 1)}/"
              f"{sum(1 for _, fl, _ in rs if fl == 2)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
