#!/usr/bin/env python3
"""fmoscriptcast.py -- extract the NPC cast table from a compiled FMO SCP.

    python fmoscriptcast.py --client <install> [--scp AI/F00/D07.DAT] [--cmd 0xE285 ...] [--all]

WHAT IT READS. The SCP encoding (decoded 2026-09-04):

    22 07 <u32 imm>     load an immediate
    57 04 <u16 local>   store it to a LOCAL (byte-addressed)
    c9 0a <u16 local>   point the command's parameter window at a local
    80 ae <u16 cmd>     SYSCALL

A syscall record is 8 bytes with `80 AE` at rec+4 and the id at rec+6. The
parameter window is BYTE-addressed into the locals: for 0xE200, window=0x0C
and param+0 is local 0x0C, param+8 is local 0x14 (proven against the DLL's
own defaults, 101/121).

THE COMMANDS DECODED HERE (handlers read from the live DLL image):

    0xE285  place NPC   0x611015F0: param+0 = script NPC id, param+0x1C/20/24
            = x,y,z as INTEGERS (the handler filds them to float)
    0xE291  face NPC    0x611032C0: param+0 = script NPC id, angle after it

Script NPC id -> entity key is 0x611142A0:

    id & 0xFFF == 0xFFF -> scene-dependent wildcard ([wm+0x152C] / 0x820C1FFF)
    id == 0x80          -> the player's derived NPC (entity+0x14B / [wm+0x1528])
    else                -> 0x820C1000 | (id & 0xEFFF)     e.g. 0x900 -> 0x820C1900

METHOD, HONESTLY STATED. This does NOT interpret the whole VM. For each
syscall of a wanted id it scans BACKWARD a bounded window collecting
`22 07 imm / 57 04 local` immediate-store pairs and the nearest `c9 0a`
window setter, keeping the LAST store per local before the call. A local fed
by arithmetic or by another opcode is reported as UNRESOLVED, never guessed
(a field is only what has been proved). Coverage is printed so a
confident-looking table with holes cannot pass as complete.
"""
import argparse
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

KEY = 0x15087948

OP_IMM = bytes([0x22, 0x07])
OP_STORE = bytes([0x57, 0x04])
OP_WINDOW = bytes([0xC9, 0x0A])
OP_SYSCALL = bytes([0x80, 0xAE])

BACK_WINDOW = 0x400          # how far back a syscall's params may be staged


def fmdt_decode(body, key=KEY):
    out = bytearray()
    for k in range(len(body) // 8):
        a = int.from_bytes(body[8 * k:8 * k + 4], "big")
        b = int.from_bytes(body[8 * k + 4:8 * k + 8], "big")
        out += ((b ^ key) & 0xFFFFFFFF).to_bytes(4, "little")
        out += a.to_bytes(4, "little")
    out += body[len(body) // 8 * 8:]
    return bytes(out)


def load_scp(client, rel):
    """The decoded SCP bytes of `rel` (relative to Data/) from an install."""
    with open(fmofile.data_path(client, rel), "rb") as fh:
        return fmdt_decode(fh.read()[16:])


def u16(b, o):
    return int.from_bytes(b[o:o + 2], "little")


def u32(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def s32(v):
    return v - 0x100000000 if v >= 0x80000000 else v


def coord(v):
    """Decode a scripted coordinate exactly as the 0xE285 handler does
    (0x611015F0): sign bit set -> the float's bit pattern is v<<1 (the
    script stores bits>>1 with the top bit forced); bit 30 set -> a negative
    INTEGER (the handler ors 0x80000000 back and filds); else a plain
    non-negative integer."""
    import struct
    if v is None:
        return None
    if v & 0x80000000:
        return struct.unpack("<f", struct.pack("<I", (v << 1) & 0xFFFFFFFF))[0]
    if v & 0x40000000:
        return float(s32(v | 0x80000000))
    return float(v)


def fco(v):
    return "?" if v is None else "%.2f" % coord(v)


def npc_key(sid):
    """0x611142A0, static arm only."""
    if (sid & 0xFFF) == 0xFFF:
        return None, "wildcard(0xFFF)"
    if sid == 0x80:
        return None, "PLAYER"
    return 0x820C1000 | (sid & 0xEFFF), None


def find_syscalls(scp, wanted=None):
    """Every offset whose bytes read as a syscall record: 80 AE at rec+4.
    Returns list of (rec_offset, cmd_id). rec_offset is the RECORD start
    (the live PC is rec-2)."""
    out = []
    i = 0
    while True:
        i = scp.find(OP_SYSCALL, i)
        if i < 0:
            break
        rec = i - 4
        if rec >= 0 and rec + 8 <= len(scp):
            cid = u16(scp, i + 2)
            if wanted is None or cid in wanted:
                out.append((rec, cid))
        i += 2
    return out


def stage_scan(scp, rec):
    """Params for the syscall record at rec.

    A syscall record is 8 bytes: `c9 0a <u16 window>` at rec+0 and
    `80 ae <u16 cmd>` at rec+4 - the window setter is PART of the record
    (proven by the raw stream at 0x936a in D07.DAT; an earlier cut that
    scanned backward for the nearest c9 0a picked up the PREVIOUS record's
    window and mis-read every parameter).

    Immediate stores are collected walking BACKWARD from the record; for
    each local the store NEAREST the call wins. A `22 07 imm` may be
    separated from its `57 04` store by a conversion opcode (e.g. `02 3c`),
    so a small gap between them is allowed."""
    lo = max(0, rec - BACK_WINDOW)
    stores = {}          # local -> (imm, offset)  nearest-to-call
    window = u16(scp, rec + 2) if scp[rec:rec + 2] == OP_WINDOW else None
    o = rec - 1
    while o >= lo:
        if scp[o:o + 2] == OP_STORE and o + 4 <= rec:
            loc = u16(scp, o + 2)
            if loc not in stores:
                for gap in (6, 8, 10, 12, 14):
                    if o >= gap and scp[o - gap:o - gap + 2] == OP_IMM:
                        stores[loc] = (u32(scp, o - gap + 2), o)
                        break
        o -= 1
    return window, rec, stores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--scp", default="AI/F00/D07.DAT")
    ap.add_argument("--cmd", action="append", default=None,
                    help="command ids to extract (default: 0xE285 0xE291)")
    ap.add_argument("--all", action="store_true",
                    help="list EVERY syscall id histogram first")
    ap.add_argument("--near", type=lambda s: int(s, 0), default=None,
                    help="only sites within --span of this file offset")
    ap.add_argument("--span", type=lambda s: int(s, 0), default=0x1000)
    ap.add_argument("--any", action="store_true",
                    help="dump EVERY syscall in the --near region, not just "
                         "the placement pair")
    args = ap.parse_args()

    scp = load_scp(args.client, args.scp)
    print("SCP %s  %d bytes decoded" % (args.scp, len(scp)))

    if args.all:
        hist = {}
        for rec, cid in find_syscalls(scp):
            hist[cid] = hist.get(cid, 0) + 1
        print("=== syscall id histogram (pattern hits, includes false "
              "positives from random 80 AE) ===")
        for cid in sorted(hist):
            print("  %#06x x%d" % (cid, hist[cid]))
        return 0

    wanted = None if args.any else (
        set(int(c, 0) for c in args.cmd) if args.cmd
        else {0xE280, 0xE285, 0xE291})
    sites = find_syscalls(scp, wanted)
    if args.near is not None:
        sites = [s for s in sites if abs(s[0] - args.near) < args.span]
    if wanted is None:
        wanted = set(c for _r, c in sites)
    print("%d syscall records match %s" %
          (len(sites), ", ".join("%#x" % w for w in sorted(wanted))))

    cast = OrderedDict()     # script id -> latest place/face
    unresolved = 0
    for rec, cid in sites:
        window, woff, stores = stage_scan(scp, rec)
        if window is None:
            print("  %#07x cmd %#06x  NO c9 0a window setter in %#x back - "
                  "UNRESOLVED" % (rec, cid, BACK_WINDOW))
            unresolved += 1
            continue

        def param(off, width=4):
            loc = window + off
            if loc in stores:
                return stores[loc][0]
            return None

        sid = param(0)
        if cid == 0xE285:
            x, y, z = param(0x1C), param(0x20), param(0x24)
            key, note = npc_key(sid) if sid is not None else (None, "id UNRESOLVED")
            miss = [n for n, v in
                    (("id", sid), ("x", x), ("y", y), ("z", z)) if v is None]
            line = ("  %#07x PLACE  id=%-8s key=%-12s pos=(%s, %s, %s)%s"
                    % (rec,
                       "%#x" % sid if sid is not None else "?",
                       "%#x" % key if key else (note or "?"),
                       fco(x), fco(y), fco(z),
                       ("  UNRESOLVED: " + ",".join(miss)) if miss else ""))
            print(line)
            if miss:
                unresolved += 1
            if sid is not None:
                e = cast.setdefault(sid, {})
                if not miss:
                    e["pos"] = tuple(round(coord(v), 2) for v in (x, y, z))
                e.setdefault("places", 0)
                e["places"] += 1
        elif cid == 0xE291:
            ang = param(0x5C)
            key, note = npc_key(sid) if sid is not None else (None, "id UNRESOLVED")
            print("  %#07x FACE   id=%-8s key=%-12s angle=%s"
                  % (rec,
                     "%#x" % sid if sid is not None else "?",
                     "%#x" % key if key else (note or "?"),
                     s32(ang) if ang is not None else "?"))
            if sid is not None:
                e = cast.setdefault(sid, {})
                e.setdefault("faces", 0)
                e["faces"] += 1
                if ang is not None:
                    e["angle"] = s32(ang)
        elif cid == 0xE280:
            tc = param(0x4C)
            key, note = npc_key(sid) if sid is not None else (None, "id UNRESOLVED")
            print("  %#07x CREATE id=%-8s key=%-12s typecode=%s"
                  % (rec,
                     "%#x" % sid if sid is not None else "?",
                     "%#x" % key if key else (note or "?"),
                     s32(tc) if tc is not None else "?"))
            if sid is not None:
                e = cast.setdefault(sid, {})
                e.setdefault("creates", 0)
                e["creates"] += 1
                if tc is not None:
                    e["typecode"] = s32(tc)
        else:
            keys = sorted(stores)
            print("  %#07x cmd %#06x window=%#x  locals: %s"
                  % (rec, cid, window,
                     " ".join("[%#x]=%#x" % (k, stores[k][0])
                              for k in keys[:12])))

    print("")
    print("=== cast summary (script id -> derived key, last-resolved pos) ===")
    for sid in sorted(cast):
        key, note = npc_key(sid)
        e = cast[sid]
        print("  id %#6x -> %-12s  created x%-2d (typecode %s)  placed x%-3d "
              "faced x%-3d  pos=%s angle=%s"
              % (sid, "%#x" % key if key else note,
                 e.get("creates", 0), e.get("typecode", "?"),
                 e.get("places", 0), e.get("faces", 0),
                 e.get("pos", "UNRESOLVED"), e.get("angle", "?")))
    print("%d/%d sites fully resolved; %d unresolved (arithmetic-fed or "
          "out-of-window - NOT guessed)"
          % (len(sites) - unresolved, len(sites), unresolved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
