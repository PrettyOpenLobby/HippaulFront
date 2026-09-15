"""Write path for FMDT/MSG containers - take a container apart, replace record
text, put it back. The readers in this package use its record and text-run
enumeration (split_records, record_blocks); rebuild() is the inverse.

    python fmofmdtwrite.py --client <install> --selftest   # round-trip + a demo translation
    python fmofmdtwrite.py --apply SRC DST --tsv edits.tsv

PROVEN (2026-09-04):
  * every record re-serialises byte-exact: 29,198 / 29,198
  * every FMDT file round-trips byte-exact: 2,888 / 2,888
  * a demo edit of AI/F00/D08.DAT leaves all 218 untouched records identical

WARNING: Replacing text moves three things at once, and all three must agree
or the client reads garbage:
  * the record header's length byte  (0xFF ^ code == len(rec) - 8)
  * the u32 record-offset table
  * the container size field at +0x04, which equals the PAYLOAD length
WARNING: The length byte is ONE byte, so a record longer than 263 bytes cannot
express it; those records (4.4%) are left alone rather than corrupted.
WARNING: A record can hold SEVERAL text runs split by control tokens - record
133 is `私は、` + <name substitution> + `大尉だ。`. Replacing only the first run
leaves the rest of the Japanese in place, which is how this script's first
version produced "I am the Captain.大尉だ。".
WARNING: Installing a changed DAT into a client means rewriting `file.txt` too,
or the launcher's Check Files reverts it.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofmdt                                                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def parse_items(rec):
    """Record -> ordered [('c', bytes) | ('t', bytes)]; re-joining is exact."""
    i, items = 0, []

    def ctl(n):
        nonlocal i
        items.append(("c", rec[i:i + n]))
        i += n

    def header():
        """FE 00 | FD 00, optional 0F, two FF-pairs, then a LONE FF - at the
        start of EVERY sub-record, not just the record (menu records pack
        several sub-records into one offset-table entry).

        Emitted as ONE ('h', bytes) item so the rebuild can find sub-record
        boundaries without re-scanning for `fe 00`, which could also match
        inside a token."""
        nonlocal i
        st = i
        i += 2
        if i < len(rec) and rec[i] == 0x0F:
            i += 1
        pairs = 0
        while pairs < 2 and i + 1 < len(rec) and rec[i] == 0xFF:
            i += 2
            pairs += 1
        if i < len(rec) and rec[i] == 0xFF:
            i += 1
        items.append(("h", rec[st:i]))

    if rec[:2] in (b"\xfe\x00", b"\xfd\x00"):
        header()
    run = bytearray()
    while i < len(rec):
        c = rec[i]
        if rec[i:i + 2] in (b"\xfe\x00", b"\xfd\x00"):
            if run:
                items.append(("t", bytes(run)))
                run = bytearray()
            header()
            continue
        if c in (0xFD, 0xFE, 0xFF):
            if run:
                items.append(("t", bytes(run)))
                run = bytearray()
            # FF 0C is a KEY/ICON GLYPH and takes two more bytes (`FF 0C 00 id`
            # - 00 8A is [Tab], 00 87 is No.). Reading it as a 2-byte token left
            # the id's `00` and `8a` in the text, where `8a 82` decoded as 鰍:
            # "[Tab]で開く" came out as "鰍ﾅ開く". All 334 occurrences follow it.
            ctl(4 if c == 0xFF and rec[i:i + 2] == b"\xff\x0c" else 2)
            continue
        if (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xEF) and i + 1 < len(rec):
            run += rec[i:i + 2]
            i += 2
            continue
        run += rec[i:i + 1]
        i += 1
    if run:
        items.append(("t", bytes(run)))
    return items


def menu_split(rec):
    """A `FD 00` record is a MENU. Its wrapper carries the entries' offsets:

        FD 00 | 0F | FF AF | FF FF | <count-1> | u16 offsets, LE, ^0xFFFF

    Returns (wrapper, offsets, entries) or None. Verified on all 994 menu
    records in the tree.

    WARNING: Entries must NOT be found by scanning for `fe 00`: an offset >= 256
    stores its high byte as 0xFE, so `FE 00` inside the TABLE is
    indistinguishable from a sub-record header and a scanning parser splits the
    wrapper early (record 65's 34-byte wrapper read as 31 plus a bogus 3-byte
    entry)."""
    if rec[:2] != b"\xfd\x00" or len(rec) < 10:
        return None
    n = (rec[7] ^ 0xFF) + 1
    tbl = 8
    if tbl + 2 * n > len(rec):
        return None
    offs = [struct.unpack_from("<H", rec, tbl + 2 * i)[0] ^ 0xFFFF for i in range(n)]
    if offs != sorted(offs) or offs[0] < tbl + 2 * n or offs[-1] >= len(rec):
        return None
    parts = [rec[o:(offs[i + 1] if i + 1 < n else len(rec))]
             for i, o in enumerate(offs)]
    if not all(x[:2] == b"\xfe\x00" for x in parts):
        return None
    return rec[:offs[0]], offs, parts


def record_blocks(rec):
    """-> [(tag, bytes, items)]. For a menu the first block is the wrapper
    (tag 'wrap', no items); every other block is a sub-record."""
    m = menu_split(rec)
    if m:
        wrap, _offs, parts = m
        return [("wrap", wrap, [])] + [("sub", x, parse_items(x)) for x in parts]
    groups = []
    for kind, b in parse_items(rec):
        if kind == "h" or not groups:
            groups.append([])
        groups[-1].append((kind, b))
    return [("sub", b"".join(x for _, x in g), g) for g in groups]


def text_runs(rec):
    """The display-text runs of one record, in order, decoded and stripped:
    the enumeration the mission catalogue and the translation tables share."""
    return ["".join(c for c in b.decode("cp932", "replace") if c >= " ").strip()
            for kind, b in [it for _t, _b, its in record_blocks(rec) for it in its]
            if kind == "t"]


def _retable(wrap, entries):
    """Rewrite a menu wrapper's offset table for the rebuilt entries."""
    n = (wrap[7] ^ 0xFF) + 1
    out = bytearray(wrap)
    pos = len(wrap)
    for i in range(min(n, len(entries))):
        struct.pack_into("<H", out, 8 + 2 * i, pos ^ 0xFFFF)
        pos += len(entries[i])
    return bytes(out)


def _len_pos(sub):
    """Index of the length CODE byte inside a sub-record header, or None.

    header = FE 00 | FD 00, optional 0F, two FF-pairs, lone FF. The SECOND
    pair carries the length, so the code byte is at 5 or 6 depending on the 0F.
    """
    if len(sub) < 8 or sub[:2] not in (b"\xfe\x00", b"\xfd\x00"):
        return None
    i = 3 if sub[2] == 0x0F else 2
    if sub[i] != 0xFF or sub[i + 2] != 0xFF:
        return None
    return i + 3


def _fix_len(sub, orig, edited):
    """Rewrite a sub-record's length byte - but only where it was a correct
    length to begin with. Menu wrappers and 350-440 byte records carry a value
    one byte cannot express, so the client plainly does not read it there.

    WARNING: Does NOT pad. Individual sub-records may be ODD length in the
    shipped data (only the whole record is even), so padding each one adds
    bytes and breaks the identity rebuild - measured 65/254 before this was
    removed."""
    p = _len_pos(orig)
    if p is None or (orig[p] ^ 0xFF) != len(orig) - 8:
        return sub
    n = len(sub) - 8
    if n > 0xFF:
        if edited:
            raise ValueError("sub-record grew past its length byte (%d)" % n)
        return sub
    return sub[:p] + bytes([n ^ 0xFF]) + sub[p + 1:]


def split_records(dec):
    offs, i = [], 0x10
    while i + 4 <= len(dec):
        v = struct.unpack_from("<I", dec, i)[0]
        if v == 0xFFFFFFFF:
            break
        if v >= len(dec) or (offs and v < offs[-1]):
            return [], []
        offs.append(v)
        i += 4
    inv = bytes(b ^ 0xFF for b in dec)
    return offs, [inv[a:(offs[k + 1] if k + 1 < len(offs) else len(dec))]
                  for k, a in enumerate(offs)]


def rebuild(dec, edits):
    """edits: {record index: replacement display text}. All text runs in the
    record collapse into the first one; later runs are dropped, so a record with
    a substitution token keeps the token but not the Japanese around it."""
    offs, recs = split_records(dec)
    if not recs:
        raise ValueError("container has no usable record table")
    new_recs = []
    for k, rec in enumerate(recs):
        blocks = record_blocks(rec)
        edit = edits.get(k)
        segs = edit.split("{}") if isinstance(edit, str) else None
        run_no, seg_i, subs_new = 0, 0, []
        for tag, raw, its in blocks:
            if tag == "wrap":
                subs_new.append(raw)
                continue
            out = []
            for kind, b in its:
                if kind != "t":
                    out.append((kind, b))
                    continue
                lead = b[:len(b) - len(b.lstrip(b" :\x00\x0f"))]
                if isinstance(edit, dict):
                    if run_no in edit:
                        out.append(("t", lead + edit[run_no].encode("cp932")))
                    else:
                        out.append((kind, b))
                elif segs is not None:
                    # `{}` marks a SUBSTITUTION SLOT - a control token the
                    # client fills in (FF 0F is a name: record 133 is `私は、`
                    # FF0F `大尉だ。` = "I'm Captain <name>."), so the English is
                    # split AROUND the token. Surplus runs are dropped.
                    if seg_i < len(segs):
                        out.append(("t", (lead if seg_i == 0 else b"")
                                    + segs[seg_i].encode("cp932")))
                        seg_i += 1
                else:
                    out.append((kind, b))
                run_no += 1
            subs_new.append(b"".join(x for _, x in out))
        if segs is not None and seg_i < len(segs):
            raise ValueError("record %d has too few text runs for its English" % k)
        if sum(len(x) for x in subs_new) % 2:
            subs_new[-1] += b"\x00"        # keep the RECORD even, not each sub
        subs_new = [x if blocks[i][0] == "wrap"
                    else _fix_len(x, blocks[i][1], k in edits)
                    for i, x in enumerate(subs_new)]
        if blocks[0][0] == "wrap":
            # WARNING: The wrapper's offset table must follow the rebuilt
            # entries, or the client reads each row from the wrong place - on
            # screen that was a blank row and a row starting mid-word ("goes
            # the fighting?").
            subs_new[0] = _retable(subs_new[0], subs_new[1:])
        new_recs.append(b"".join(subs_new))

    # WARNING: Keep the ORIGINAL first-record offset and the original bytes
    # between the end of the offset table and it. Recomputing a minimal,
    # 4-aligned start silently compacted slack the shipped files carry (-74
    # bytes on AH/F99/D00.DAT) and broke the identity rebuild. The record
    # COUNT never changes, so the table is the same size and the gap is
    # byte-for-byte reusable.
    start = offs[0]
    table, pos = bytearray(), start
    for b in new_recs:
        table += struct.pack("<I", pos)
        pos += len(b)
    table += struct.pack("<I", 0xFFFFFFFF)
    pad = dec[0x10 + len(table):start]
    out = dec[:0x10] + bytes(table) + pad + bytes(b ^ 0xFF for b in b"".join(new_recs))
    return out[:4] + struct.pack("<I", len(out)) + out[8:]


def selftest(client):
    src = fmofmdt.fmofile.data_path(client, "AI/F00/D08.DAT")
    raw = fmofmdt.pristine(src)
    dec = fmofmdt.decode(raw[16:])
    assert raw[:16] + fmofmdt.encode(dec) == raw, "codec is not an identity"
    print("codec identity on D08: OK")

    exact = sum(1 for r in split_records(dec)[1]
                if b"".join(b for _, b in parse_items(r)) == r)
    total = len(split_records(dec)[1])
    print("record re-serialise: %d / %d exact" % (exact, total))

    before = {k: t for k, _, t in fmofmdt.msg_records(dec)}
    # 133 carries a FF 0F name slot between its two text runs, so `{}` splits
    # the English around it: "I'm Captain <name>."
    edits = {133: "I'm Captain {}.", 134: "This is Outpost Base 1, Frontline Zone."}
    new = rebuild(dec, edits)
    dec2 = fmofmdt.decode(fmofmdt.encode(new))
    after = {k: t for k, _, t in fmofmdt.msg_records(dec2)}
    for k in sorted(edits):
        print("  [%3d] %-28r -> %r" % (k, before[k], after.get(k)))
    kept = sum(1 for k in before if k not in edits and before[k] == after.get(k))
    print("untouched records preserved: %d / %d"
          % (kept, len(before) - len(edits)))
    return 0 if kept == len(before) - len(edits) else 1


def strip_speaker(s):
    """Records that are spoken lines carry a ' : ' separator before the text;
    a translation table is keyed on the sentence alone."""
    t = s.strip()
    return t[1:].strip() if t.startswith(":") else t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--apply", nargs=2, metavar=("SRC", "DST"))
    ap.add_argument("--tsv", help="index<TAB>english")
    a = ap.parse_args()
    if a.selftest:
        if not a.client:
            ap.error("--selftest needs --client <install>")
        return selftest(a.client)
    if a.apply:
        src, dst = a.apply
        edits = {}
        with open(a.tsv, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip():
                    edits[int(parts[0])] = parts[1]
        raw = open(src, "rb").read()
        new = rebuild(fmofmdt.decode(raw[16:]), edits)
        open(dst, "wb").write(raw[:16] + fmofmdt.encode(new))
        print("wrote %s (%d edits, %d bytes)" % (dst, len(edits), os.path.getsize(dst)))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
