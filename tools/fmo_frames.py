#!/usr/bin/env python3
"""Parse and diff Front Mission Online world-protocol captures (TCP 61300).

FMO's world port was measured 2026-08-17 (STATUS "WORK IN FLIGHT"); this reads
what the tcp logger saves under logs/captures/tcp-61300-*.bin.

    python tools/fmo_frames.py                    # every capture, parsed
    python tools/fmo_frames.py --diff             # compare captures field by field
    python tools/fmo_frames.py a.bin b.bin        # specific files

THE PACKET -- read off the initializer at 0x61199fc0 in the unpacked DLL, which
zero-fills the buffer and then writes exactly these fields:

    +0x00  u16  total length = payload + 0x14  (the send-arm sends word[+0x00])
    +0x02  u16  flags       (0x0200 observed; +0x03 bit 0 set conditionally)
    +0x04  u16  CHECKSUM    (seeded 0xFE60 by the initializer)
    +0x06  u16  caller-supplied message field (0x0065 version, 0x0321 creds)
    +0x08  u16  caller-supplied (0 in every capture so far)
    +0x0A       zero
    +0x10  u32  packet SEQUENCE -- rolls 0x1000..0x1FFF, restarts per session.
                NOT an opcode: 0x61199d40 does inc / and 0xfff / or 0x1000.
    +0x14       commands (SE's own names: m_pRecvComamndHead->CommandID/->Size)

THE CHECKSUM -- cracked 2026-08-17, verified against every captured frame:

    u16 at +0x04 = sum of ALL packet bytes, with ONLY bytes +0x04..+0x05
                   zeroed, mod 0x10000.

    WARNING: THE TRAP THAT HID IT: read the field as a u32 and you zero +0x04..+0x07
    to compute it. That wipes +0x06 -- which IS part of the sum -- so every
    result is short by exactly bytesum(+0x06) and no candidate matches. A sweep
    over six ranges x three widths, plus CRC32 and Adler, missed for that one
    reason. The wrong structural assumption corrupted the measurement; the
    0xFE60 seed write is what exposed the real field boundary.

WHAT --diff IS FOR
    Before the crack, two samples could not separate a checksum from a nonce.
    --diff compares the same message across launches: the version packet's body
    is a constant string, so a checksum over it reproduces and a nonce does not.
    Kept, because that is the cheap first question to ask of any new field.
"""
import argparse
import glob
import os
import struct
import sys

CAPTURE_GLOB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir,
    "logs", "captures", "tcp-61300-*.bin")

HDR = 0x14
CHK = 4          # offset of the u16 checksum
MSGF = 6         # offset of the caller-supplied message field

#: Keyed on +0x06, the message field -- NOT on +0x10, which is a sequence.
MSGS = {0x0065: "version", 0x0321: "credentials"}


def checksum(packet):
    """The u16 at +0x04: every packet byte summed with ONLY +0x04..+0x05 zeroed.

    Verified against every captured frame. Zeroing four bytes instead of two
    silently omits +0x06 and is why this took a detour -- see the module docstring.
    """
    b = bytearray(packet)
    b[CHK:CHK + 2] = b"\x00\x00"
    return sum(b) & 0xFFFF


def seal(packet):
    """Write the correct checksum into a packet and return it.

    A server reply must be sealed with this or the client drops it.
    """
    out = bytearray(packet)
    struct.pack_into("<H", out, CHK, checksum(bytes(out)))
    return bytes(out)


def build(msg_field, payload, seq, flags=0x0200, field8=0):
    """Build a sealed packet the way 0x61199fc0 lays one out."""
    pkt = bytearray(HDR + len(payload))
    struct.pack_into("<HHHHH", pkt, 0,
                     HDR + len(payload), flags, 0, msg_field, field8)
    struct.pack_into("<I", pkt, 0x10, seq)
    pkt[HDR:] = payload
    return seal(bytes(pkt))


def parse(data):
    """Split a capture into frames. Returns (frames, trailing_junk)."""
    frames, off = [], 0
    while off + HDR <= len(data):
        ln = struct.unpack_from("<H", data, off)[0]
        # A length that does not fit is the signal that our framing is wrong --
        # say so rather than emitting a plausible-looking short frame.
        if ln < HDR or off + ln > len(data):
            break
        f = data[off:off + ln]
        frames.append({
            "off": off,
            "len": ln,
            "ver": struct.unpack_from("<H", f, 2)[0],
            "u04": struct.unpack_from("<H", f, CHK)[0],
            "msg": struct.unpack_from("<H", f, MSGF)[0],
            "f8": struct.unpack_from("<H", f, 8)[0],
            "op": struct.unpack_from("<I", f, 0x10)[0],
            "body": f[HDR:],
            "chk_ok": struct.unpack_from("<H", f, CHK)[0] == checksum(f),
        })
        off += ln
    return frames, data[off:]


def hexdump(b, indent="    "):
    out = []
    for i in range(0, len(b), 16):
        chunk = b[i:i + 16]
        txt = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        out.append(f"{indent}{i:04x}  {chunk.hex(' '):<47}  {txt}")
    return "\n".join(out)


def show(path, frames, junk):
    print(f"\n=== {os.path.basename(path)} -- {len(frames)} frame(s)")
    for f in frames:
        name = MSGS.get(f["msg"], "?")
        print(f"  msg=0x{f['msg']:04X} ({name})  len={f['len']}  "
              f"flags=0x{f['ver']:04X}  seq=0x{f['op']:04X}  +0x08=0x{f['f8']:04X}")
        print(f"    checksum 0x{f['u04']:04X} "
              f"{'OK' if f['chk_ok'] else 'MISMATCH -- framing or algorithm wrong'}")
        print(hexdump(f["body"]))
    if junk:
        print(f"  WARNING: {len(junk)} trailing byte(s) did not parse as a frame -- "
              f"the framing above is incomplete, not merely truncated:")
        print(hexdump(junk))


def diff(caps):
    """Compare each opcode's fields across captures. This is the nonce test."""
    by_op = {}
    for path, frames, _ in caps:
        for f in frames:
            by_op.setdefault(f["op"], []).append((os.path.basename(path), f))

    for op in sorted(by_op):
        rows = by_op[op]
        name = OPCODES.get(op, "?")
        print(f"\n=== op 0x{op:04X} ({name}) -- {len(rows)} sample(s)")
        if len(rows) < 2:
            print("  only one sample; nothing to compare. Launch FMO again.")
            continue
        bodies = {r[1]["body"] for r in rows}
        u04s = [r[1]["u04"] for r in rows]
        for src, f in rows:
            print(f"  {src}  +0x04=0x{f['u04']:08X}")

        body_same = len(bodies) == 1
        u04_same = len(set(u04s)) == 1
        print(f"  body identical across samples: {body_same}")
        print(f"  +0x04 identical across samples: {u04_same}")
        if body_same and u04_same:
            print("  => +0x04 is a FUNCTION OF THE MESSAGE (checksum/hash). "
                  "Crack it against these bytes.")
        elif body_same and not u04_same:
            print("  => +0x04 VARIES over an identical body: it is NOT a "
                  "checksum of the body. Nonce, tick, or sequence.")
            lo = [v & 0xFFFF for v in u04s]
            hi = [v >> 16 for v in u04s]
            print(f"     low u16  {lo}  deltas {[b - a for a, b in zip(lo, lo[1:])]}")
            print(f"     high u16 {hi}  deltas {[b - a for a, b in zip(hi, hi[1:])]}")
            print("     (monotonic => counter/tick; unrelated => random nonce)")
        else:
            print("  => bodies differ too; this opcode carries per-session "
                  "state, so it cannot settle the +0x04 question. Use 0x1001.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="captures (default: all under logs/captures)")
    ap.add_argument("--diff", action="store_true",
                    help="compare captures field by field (the nonce test)")
    args = ap.parse_args()

    paths = args.files or sorted(glob.glob(CAPTURE_GLOB))
    if not paths:
        sys.exit("no captures found; is 61300 bound and has FMO been launched?")

    caps = []
    for p in paths:
        with open(p, "rb") as fh:
            data = fh.read()
        frames, junk = parse(data)
        if not frames:
            # The 12-byte hold self-test and any stray probe land here. Named,
            # not silently dropped -- a capture we cannot parse is information.
            print(f"\n=== {os.path.basename(p)} -- {len(data)}B, no frames "
                  f"(probe or self-test, not FMO)")
            continue
        caps.append((p, frames, junk))
        if not args.diff:
            show(p, frames, junk)

    if args.diff:
        if len(caps) < 2:
            print(f"\n{len(caps)} parseable capture(s). The comparison needs at "
                  f"least two FMO launches.")
        diff(caps)


if __name__ == "__main__":
    main()
