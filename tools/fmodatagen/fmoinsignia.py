#!/usr/bin/env python3
"""fmoinsignia.py -- the SQUADRON INSIGNIA catalogue, out of the client's own data.

    python fmoinsignia.py --client <install> [--out fmo-insignia.tsv]

WHY THIS EXISTS. The FMO Squadron menu's "Set squadron insignia" row crashed the
client on 2026-09-09 (ACCESS_VIOLATION at 0x611BE949, `mov ecx,[ecx+8]` with
ecx=NULL, ebp=0x100B). The fault is `rows[selected] == NULL` - the picker had
NO rows - and `0x611BE1F0` shows where rows come from:

    611be322  cmp dword [lobby+0x7E35], 0    ; the COUNT
    611be330  jle <bail>                     ; 0 -> the list stays EMPTY
    611be337  lea ebp, [lobby+0x7E3B]        ; the ROWS, stride 8
    611be340  movzx edx, byte [ebp-2]        ; row nation, vs byte[lobby+0x8B4]
    611be34e  movzx eax, word [ebp]          ; row INSIGNIA ID
                                             ; -> looked up in resource 0x1B2E3

`lobby+0x7DF5` is written by exactly one thing - message **0x019F**
(`0x6117F693`: `mov ecx,0x5AD; lea esi,[ebp+0x14]; lea edi,[ebx+0x7DF5];
rep movsd`) - so the list is SERVER-AUTHORED and we were sending nothing.

Resource **0x1B2E3 = 111331** resolves (fmofile.py) to `data/BB/F13/D31.DAT`,
an FMDT-wrapped `ITM\\0` container - the same shape fmoitm.py already reads for
accessories, one directory over from the item tables. It holds the ids the
client will accept, with SE's OWN ENGLISH NAMES already in the file.

RECORD (ITM stride 0x74, as fmoitm.py documents):
    +0x00 u32 id      +0x04 u32 nation flag    1 = O.C.U., 2 = U.S.N., 255 = any
    +0x08 char[0x20]  SE's English name        +0x30 char[] Japanese display name

WARNING: The nation flag is SE's categorisation of the INSIGNIA. The wire row
carries a nation byte that the client compares for EQUALITY against
`byte[lobby+0x8B4]`, so a 255 ("any") insignia has to be emitted with the
player's own nation, not with 255. That conversion belongs to the server, not
to this table.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402
import fmofmdt                                                     # noqa: E402

REL = "BB/F13/D31.DAT"          # = resource 0x1B2E3 (111331), see fmofile.py

NATION = {1: "OCU", 2: "USN", 255: "any"}


def records(dat):
    """[(id, nation_flag, english, japanese)] out of the decoded ITM container."""
    if dat[:4] != b"ITM\0":
        raise SystemExit(f"not an ITM container: {dat[:4]!r}")
    count = struct.unpack_from("<H", dat, 0x46)[0]
    table = struct.unpack_from("<I", dat, 0x48)[0]
    offs = [struct.unpack_from("<I", dat, table + 4 * i)[0] for i in range(count)]
    # The stride is MEASURED from the offset gaps, never assumed - fmoitm.py
    # found 0x74 for D27/D29 and 0x78 for D28, and this file is a fourth one.
    stride = offs[1] - offs[0] if count > 1 else 0x74
    out = []
    for off in offs:
        r = table + off
        rid, flag = struct.unpack_from("<II", dat, r)
        en = dat[r + 0x08:r + 0x28].split(b"\0")[0].decode("ascii", "replace")
        jp = dat[r + 0x30:r + stride].split(b"\0")[0].decode("cp932", "replace")
        if rid or en or jp:
            out.append((rid, flag, en, jp))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", "--install", dest="client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", default="fmo-insignia.tsv")
    a = ap.parse_args()

    path = fmofile.data_path(a.client, REL)
    if not os.path.exists(path):
        raise SystemExit(f"no insignia catalogue at {path}")
    dat = fmofmdt.decode(fmofmdt.pristine(path)[16:])   # +16 = the FMDT header
    rows = [r for r in records(dat) if r[2] or r[3]]

    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["id", "nation_flag", "nation", "english", "japanese"])
        for rid, flag, en, jp in rows:
            w.writerow([rid, flag, NATION.get(flag, str(flag)), en, jp])

    by = {}
    for _rid, flag, _en, _jp in rows:
        by[NATION.get(flag, flag)] = by.get(NATION.get(flag, flag), 0) + 1
    print(f"{len(rows)} insignia -> {a.out}")
    print("  by nation flag: " + ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    print(f"  id range {min(r[0] for r in rows)}..{max(r[0] for r in rows)}")


if __name__ == "__main__":
    main()
