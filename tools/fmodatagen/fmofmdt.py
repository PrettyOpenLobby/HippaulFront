"""FMDT - the sixth FMO text store, and the reason no byte search ever found the
base NPC dialogue.

    python fmofmdt.py --client <install> --census      # what every FMDT file decodes to
    python fmofmdt.py --client <install> --text [--out T.tsv]  # every MSG container's records
    python fmofmdt.py --decode FILE [-o O]

HOW IT WAS FOUND (2026-09-04). The dialogue was carved out of a LIVE pol.exe as
a `MSG\\0` container (`npc-dialogue-carved.msg`) that matched nothing on disk.
`Data/AI/F00/D08.DAT` is 16,632 bytes = that container + 16, and its payload
turned out to be the container under a cheap obfuscation.

LAYOUT
    +0x00 'FMDT'   +0x04 u32 ver   +0x08 u32 count   +0x0c u32 ?
    payload: 8-byte blocks. Block k is two BIG-ENDIAN dwords {A, B} and decodes
    to two little-endian dwords  { B ^ KEY , A }: the pair is SWAPPED, stored
    big-endian, and the first is XORed with a GLOBAL constant.

WARNING: Why every sweep missed it: the swap plus the byte-order flip means no
run of plaintext longer than 4 bytes ever survives contiguously, so cp932 (and
every XOR-k / ADD-k of it) is invisible to a substring search. The obfuscation
is not compression - nothing is packed, which is why sizes match exactly.

SELF-CHECK, no key needed: BE(A of block 0) is the decoded container's own size
field, so a well-formed payload announces its length (1,179 of 2,888 files).
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
KEY = 0x15087948
MAGICS = {b"MSG\x00", b"SMG\x00", b"TXT\x00", b"NPT\x00", b"SFL\x00", b"htar",
          b"FMDT", b"TIM2", b"BGMS", b"KGR\x00", b"ARE\x00", b"SCP\x00",
          b"LEV\x00", b"RMG\x00", b"ITM\x00"}


def decode(body, key=KEY):
    """Payload -> container.

    WARNING: THE TAIL. Only 25 of 254 MSG payloads are a multiple of 8; the rest
    carry a 2/4/6-byte remainder that is stored VERBATIM (no swap, no XOR) and
    is the tail of the last record - `ff ff`, `00 fc 00 ff` etc. decode to the
    standard `FF 03 FF 00` terminator. The container's own size field equals
    len(payload) for all 254 files, so dropping the remainder truncates the
    last record and would shorten every file written back. Carry it.
    """
    out = bytearray()
    n = len(body) // 8
    for k in range(n):
        a = int.from_bytes(body[8 * k:8 * k + 4], "big")
        b = int.from_bytes(body[8 * k + 4:8 * k + 8], "big")
        out += ((b ^ key) & 0xFFFFFFFF).to_bytes(4, "little")
        out += a.to_bytes(4, "little")
    out += body[n * 8:]
    return bytes(out)


def encode(plain, key=KEY):
    """Container -> payload. Inverse of decode(); the tail rides verbatim."""
    out = bytearray()
    n = len(plain) // 8
    for k in range(n):
        lo = int.from_bytes(plain[8 * k:8 * k + 4], "little")
        hi = int.from_bytes(plain[8 * k + 4:8 * k + 8], "little")
        out += hi.to_bytes(4, "big")
        out += ((lo ^ key) & 0xFFFFFFFF).to_bytes(4, "big")
    out += plain[n * 8:]
    return bytes(out)


def fmdt_files(root):
    """Every FMDT container under `root` (a Data directory).

    WARNING: Skips `*.pre-fmdt` backups - they are FMDT files too, so a magic
    check alone counts each patched file twice (254 containers read as 333)."""
    out = []
    for base, dirs, fns in os.walk(root):
        for fn in fns:
            if fn.endswith(".pre-fmdt") or fn.endswith(".pre-en") \
                    or fn.endswith(".orig"):
                continue
            p = os.path.join(base, fn)
            try:
                with open(p, "rb") as f:
                    if f.read(4) != b"FMDT":
                        continue
            except OSError:
                continue
            out.append(p)
    return sorted(out)


def pristine(p):
    """The ORIGINAL bytes of a container: a `.pre-fmdt` backup beside the file
    if an English text patch has been installed over it, else the file itself.

    WARNING: The readers must never work from an already-translated install, or
    a second pass reads the patch's own output and the source of truth drifts.
    An untouched install has no backups and reads the file directly."""
    bak = p + ".pre-fmdt"
    return open(bak if os.path.exists(bak) else p, "rb").read()


def load(client, rel):
    """The decoded container of one FMDT resource, `rel` relative to Data/."""
    return decode(pristine(fmofile.data_path(client, rel))[16:])


def _lead(b):
    return 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xEF


def parse_record(rec):
    """One MSG record -> (display text, control tokens, header length field).

    rec is the record in the INVERTED domain (text reads as cp932 there).

      header:  FE 00 | FD 00     record start
               0F                (1)
               FF AF             (2)
               FF <len>          (2)   0xFF ^ code == len(rec) - 8   (95.6%)
               FF                (1)   LONE - "content starts"
      body:    FF <code> tokens with cp932 text between them; ends FF 03 FF 00.

    The lone FF is STRUCTURAL. Two earlier models failed, both measured over all
    29,198 records:
      * FF always introduces a 2-byte token -> desyncs on `ff 8d 76` and eats
        the lead byte of 貢 ("v献値"). This was the original bug.
      * fixed 2-byte units -> 44.7% clean; the 3-byte " : " speaker separator
        flips parity and shreds the rest of the record.
      * "lone FF iff the next byte is a cp932 lead" -> 59.1%; token codes
        FF E1/E3/E5/E7/ED/EF sit inside the lead-byte range and mis-split.
    Parsing the header structurally instead: 99.13% clean.
    """
    i, ctl, hdr_len = 0, [], None

    def header():
        """FE 00 | FD 00, optional 0F, two FF-pairs, then a LONE FF.

        WARNING: Menu records pack SEVERAL sub-records into one offset-table
        entry, each with its own header. Applying this only at the start of a
        record let a phantom token (`ff 89`) swallow the lead byte of 何 in
        every sub-record. Applying it at each: 99.16% -> 99.42% clean."""
        nonlocal i, hdr_len
        ctl.append(rec[i:i + 2])
        i += 2
        if i < len(rec) and rec[i] == 0x0F:
            ctl.append(rec[i:i + 1])
            i += 1
        pairs = 0
        while pairs < 2 and i + 1 < len(rec) and rec[i] == 0xFF:
            if pairs == 1 and hdr_len is None:
                hdr_len = rec[i + 1] ^ 0xFF
            ctl.append(rec[i:i + 2])
            i += 2
            pairs += 1
        if i < len(rec) and rec[i] == 0xFF:
            ctl.append(rec[i:i + 1])
            i += 1

    if rec[:2] in (b"\xfe\x00", b"\xfd\x00"):
        header()
    text = bytearray()
    while i < len(rec):
        c = rec[i]
        if rec[i:i + 2] in (b"\xfe\x00", b"\xfd\x00"):
            header()
            continue
        if c in (0xFD, 0xFE, 0xFF):
            # FF 0C is a key/icon glyph and takes two more bytes
            # (`FF 0C 00 id`; 00 8A = [Tab], 00 87 = No.). All 334 agree.
            n = 4 if rec[i:i + 2] == b"\xff\x0c" else 2
            ctl.append(rec[i:i + n])
            i += n
            continue
        if _lead(c) and i + 1 < len(rec):
            text += rec[i:i + 2]
            i += 2
            continue
        text += rec[i:i + 1]
        i += 1
    s = text.decode("cp932", "replace")
    return "".join(ch for ch in s if ch >= " "), ctl, hdr_len


def msg_records(dec):
    """MSG\0: u32 size(==len), u32, u32, then u32 record offsets to 0xFFFFFFFF."""
    offs, i = [], 0x10
    while i + 4 <= len(dec):
        v = struct.unpack_from("<I", dec, i)[0]
        if v == 0xFFFFFFFF:
            break
        if v >= len(dec) or (offs and v < offs[-1]):
            return []
        offs.append(v)
        i += 4
    inv = bytes(b ^ 0xFF for b in dec)
    out = []
    for k, a in enumerate(offs):
        b = offs[k + 1] if k + 1 < len(offs) else len(dec)
        out.append((k, a, parse_record(inv[a:b])[0].strip()))
    return out


def is_jp(s):
    return any("぀" <= c <= "ヿ" or "一" <= c <= "鿿" for c in s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--census", action="store_true")
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--decode")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()

    if a.decode:
        d = open(a.decode, "rb").read()
        dec = decode(d[16:])
        print("%s -> %d bytes, magic %r" % (a.decode, len(dec), dec[:4]))
        if a.out:
            open(a.out, "wb").write(dec)
            print("wrote", a.out)
        else:
            for k, o, t in msg_records(dec)[:40]:
                if t:
                    print("  [%3d] +%#06x %s" % (k, o, t[:120]))
        return 0

    if not (a.census or a.text):
        ap.print_help()
        return 1
    if not a.client:
        ap.error("--census and --text need --client <install>")
    root = fmofile.data_dir(a.client)
    files = fmdt_files(root)
    if a.census:
        c, ex = collections.Counter(), collections.defaultdict(list)
        for p in files:
            dec = decode(open(p, "rb").read()[16:])
            m = dec[:4]
            k = m.decode("latin1") if all(32 <= x < 127 or x == 0 for x in m) else m.hex()
            c[k] += 1
            if len(ex[k]) < 3:
                ex[k].append(os.path.relpath(p, root))
        print("FMDT files: %d" % len(files))
        for k, n in c.most_common(20):
            print("  %-12r x%-5d %s" % (k, n, ex[k]))
        return 0

    rows, nmsg, njp = [], 0, 0
    for p in files:
        dec = decode(open(p, "rb").read()[16:])
        if dec[:4] != b"MSG\x00":
            continue
        nmsg += 1
        rel = os.path.relpath(p, root).replace("\\", "/")
        for k, o, t in msg_records(dec):
            if t and is_jp(t):
                njp += 1
                rows.append((rel, k, t))
    print("MSG containers: %d   Japanese records: %d" % (nmsg, njp))
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="") as f:
            f.write("file\tindex\tjapanese\tenglish\n")
            for rel, k, t in rows:
                f.write("%s\t%d\t%s\t\n" % (rel, k, t.replace("\t", " ")))
        print("wrote", a.out)
    else:
        for r in rows[:40]:
            print("  %-22s [%3d] %s" % (r[0], r[1], r[2][:110]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
