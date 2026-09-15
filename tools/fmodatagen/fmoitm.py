"""ITM - the FMDT item-table container (accessories, pilot suits, camouflage,
colours, insignia), read from a client install.

    python fmoitm.py --client <install> --scan [--tsv pairs.tsv]   # pairs + occupancy

THE SHAPE (measured 2026-09-04, then confirmed by a neighbour-pairing analysis
of the decoded container: inline, delta -0x28 - exactly JP+0x30 -> EN+0x08).
Each file is FMDT-obfuscated (fmofmdt.decode) around an `ITM\0` container:

    +0x44 u16 ?      +0x46 u16 record count      +0x48 u32 table offset T
    T: count u32 offsets, RELATIVE TO T, ascending, stride-spaced
    record: +0x00 u32 id   +0x04 u32 flag
            +0x08 char[0x20]  SE's ENGLISH name ('Wool Hat', 'Urban 1')
            +0x28 u32  +0x2c u32
            +0x30 char[stride-0x30]  JAPANESE display name - what the JP
                   client shows; EMPTY on most records
    stride: D27/D29 0x74, D28 0x78 (from the offset gaps, never assumed)

WHAT THE COUNTS MEAN (an earlier survey said 232/658/181 "strings"; the truth):
    D27 accessories : 210 records, all 210 EN-filled, 24 with a JP twin
    D28 pilot suits : 600 records, all 600 EN-filled, 58 with a JP twin
    D29 camo/colours: 300 records, 181 EN-filled, ZERO Japanese anywhere
The survey's numbers were EN+JP string totals (234/658/181). The en-only
records carry internal tags ('HEAD_F91') or SE's placeholders ('Pilot Suit
24') - there is no Japanese behind them.

WARNING: The FMDT 16-byte outer header rides verbatim, +0x0c included: over
400 FMDT files that dword takes only 127 distinct values, so it repeats across
files with different content and cannot be a content checksum.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402
import fmofmdt                                                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FILES = ["BB/F13/D27.DAT", "BB/F13/D28.DAT", "BB/F13/D29.DAT"]
EN_OFF, EN_W, JP_OFF = 0x08, 0x20, 0x30


def cstr(b):
    i = b.find(b"\x00")
    return b[:i] if i >= 0 else b


def parse(dec):
    """-> (record offsets within dec, stride). Offsets in the table are
    relative to the table's own offset (+0x48), verified: record 0 of D27
    lands at 0x39c = T + offs[0], and the last record ends exactly at EOF
    in all three files."""
    assert dec[:4] == b"ITM\x00", "not an ITM container"
    assert struct.unpack_from("<I", dec, 4)[0] == len(dec), "size field mismatch"
    count = struct.unpack_from("<H", dec, 0x46)[0]
    tab = struct.unpack_from("<I", dec, 0x48)[0]
    offs = struct.unpack_from("<%dI" % count, dec, tab)
    gaps = {b - a for a, b in zip(offs, offs[1:])}
    assert len(gaps) == 1, "records are not stride-spaced: %r" % sorted(gaps)[:5]
    stride = gaps.pop()
    recs = [tab + o for o in offs]
    assert recs[-1] + stride == len(dec), "last record does not end at EOF"
    return recs, stride


def pairs(dec):
    """The bilingual records: (record index, record offset, en bytes, jp bytes)."""
    recs, stride = parse(dec)
    out = []
    for k, r in enumerate(recs):
        en = cstr(dec[r + EN_OFF:r + EN_OFF + EN_W])
        jp = cstr(dec[r + JP_OFF:r + stride])
        if en and jp:
            out.append((k, r, en, jp))
    return out


def load(rel, client):
    """(raw file bytes, decoded container) for `rel` (relative to Data/).
    Reads the pristine bytes (a `.pre-fmdt` backup wins over a patched file)."""
    raw = fmofmdt.pristine(fmofile.data_path(client, rel))
    assert raw[:4] == b"FMDT", "%s is not FMDT" % rel
    dec = fmofmdt.decode(raw[16:])
    # the codec must be an identity on the pristine file before we trust it
    assert raw[:16] + fmofmdt.encode(dec) == raw, "codec round-trip failed on %s" % rel
    return raw, dec


def scan(client, out_tsv=None):
    rows, unreadable = [], 0
    for rel in FILES:
        try:
            raw, dec = load(rel, client)
        except OSError as e:
            print("CANNOT READ %s: %s" % (rel, e))
            unreadable += 1
            continue
        recs, stride = parse(dec)
        pp = pairs(dec)
        en_n = sum(1 for r in recs if cstr(dec[r + EN_OFF:r + EN_OFF + EN_W]))
        jp_n = sum(1 for r in recs if cstr(dec[r + JP_OFF:r + stride]))
        print("%s: %d records (stride %#x), %d EN-filled, %d JP-filled, "
              "%d bilingual" % (rel, len(recs), stride, en_n, jp_n, len(pp)))
        for k, r, en, jp in pp:
            rows.append((rel, k, r, jp.decode("cp932", "replace"),
                         en.decode("latin1")))
    if out_tsv:
        with open(out_tsv, "w", encoding="utf-8", newline="") as f:
            f.write("file\trecord\toffset\tjapanese\tenglish\n")
            for rel, k, r, j, e in rows:
                f.write("%s\t%d\t%#x\t%s\t%s\n" % (rel, k, r, j, e))
        print("wrote %s (%d pairs)" % (out_tsv, len(rows)))
    print("unreadable files: %d" % unreadable)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--tsv", help="write the bilingual pairs here")
    a = ap.parse_args()
    if a.scan:
        scan(a.client, a.tsv)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
