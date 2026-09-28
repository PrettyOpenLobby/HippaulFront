"""The TCP packet: header layout, the RC4 session cipher and its key, checksum, build and parse."""
import os
import struct
from .knobs import _env_int


HDR = 0x14
#: WARNING:KEY: The client's RX buffer is 15,000 B and is INLINE in the connection
#: object -- see the full derivation at build(). Defined here because the
#: mission-board reply sizing needs it long before build() is reached.
CLIENT_RX_BUFFER = 0x757C - 0x3AE4      # 15,000, from ctor 0x6119A580
MAX_PAYLOAD = CLIENT_RX_BUFFER - HDR    # 14,980
CHK_OFF = 4

FLAG_ENCRYPTED = 0x01          # byte +0x03 bit 0
FLAG_CHECKSUM = 0x02           # byte +0x03 bit 1
FLAGS_PLAIN_SUMMED = 0x0200    # what every observed packet carries

SEQ_MIN, SEQ_MASK, SEQ_BIT = 0x1000, 0xFFF, 0x1000


# --------------------------------------------------------------------------- #
# the packet
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# the cipher -- plain RC4, read off 0x611a3bb0 (KSA) and 0x611a3c60 (PRGA)
# --------------------------------------------------------------------------- #
# The context is 0x204 bytes: S at +0x000 (the KSA result, kept pristine), the
# WORKING S at +0x100, i at +0x200, j at +0x201, and a MODE byte at +0x202 which
# is the 4th argument to the init. 0x61199eb0 passes 1.
#
#     mode 0  transform disabled
#     mode 1  CONTINUOUS keystream -- state carries across messages
#     other   the working S is restored from the pristine copy on every call,
#             i.e. a fresh keystream per message
#
# Mode 1 is what FMO uses, so the two streams are conversation-long and every
# packet must be processed in order or they desynchronise. (Compare the lobby,
# where the keystream is per MESSAGE, and GM, where the IV re-seeds per datagram
# -- same family, different choice, and mixing them up costs a day.)
#
# THE KEY IS 20 BYTES: 16 from polcore + 4 from our own 0x0322 reply. Tapped live
# 2026-08-17 with pol-shim's [fmokey], polcore's 16 are ALL ZERO, because our
# auth stack never populates its session-key field -- the same fact as
# the PC crypto RE's "login works end to end with K=0". WARNING: That is a property of OUR
# login. Against real SE those 16 bytes would presumably not be zero.
KEY_POLCORE_LEN = 16
#: What we put in the 0x0322 reply at payload +0x08 (packet +0x1C), which the
#: client copies to ctx+0x7650 = key bytes 16..19.
SERVER_KEY_TAIL = struct.pack("<I", _env_int("FMO_KEY_TAIL", "0"))


class RC4:
    """Exactly 0x611a3bb0 / 0x611a3c60. Standard RC4; no tweaks to look for."""

    def __init__(self, key):
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) & 0xFF
            s[i], s[j] = s[j], s[i]
        self.s = s
        self.i = 0
        self.j = 0

    def crypt(self, data):
        s, i, j = self.s, self.i, self.j
        out = bytearray(data)
        for n in range(len(out)):
            i = (i + 1) & 0xFF
            j = (j + s[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
            out[n] ^= s[(s[i] + s[j]) & 0xFF]
        self.i, self.j = i, j
        return bytes(out)


def session_key(tail4, prefix=None):
    """The 20-byte key: polcore's 16 then our 4. polcore's 16 are the value our
    lobby put in the 4:5 reply (contentauth.py) -- zero when it minted none."""
    return (prefix or b"\x00" * KEY_POLCORE_LEN) + tail4


#: FMO's content id = the 4:5 zone its launch reports (measured 2026-09-27:
#: `4:5 zone=4` five seconds before the 0x0321 login).
CONTENT_ID = 4

#: 0 = never trial a minted value; the key is the old zero prefix and the
#: account comes from the token/address as before. The A/B lever.
CONTENT_AUTH = os.environ.get("FMO_CONTENT_AUTH", "1") != "0"


def trial_key(pkt, tail4, cands):
    """Find the prefix the client keyed with by decrypting its FIRST encrypted
    packet under each candidate, best first.

    `cands` is [(prefix16, member_id or None)]. Returns (rx RC4 already past
    this packet, prefix, member_id, plaintext packet) or None. A candidate is
    accepted only if the plaintext passes the client's own checksum AND names a
    message id in the protocol's range: a wrong key gives random bytes, so the
    pair is a ~1-in-4-million false positive per candidate -- a measurement,
    not an address guess."""
    for prefix, member in cands:
        rc = RC4(session_key(tail4, prefix))
        plain = pkt[:CRYPT_OFF] + rc.crypt(pkt[CRYPT_OFF:])
        p = parse(plain)
        if p["chk_ok"] and p["msg"] < 0x400:
            return rc, prefix, member, plain
    return None


#: Encryption covers `len - 4` bytes from packet +0x04 -- everything except the
#: length and flags words. From the receive path: sub edx,4 / add eax,4.
CRYPT_OFF = 4


def checksum(packet):
    """0x61199c34's algorithm: sum every byte with ONLY +0x04..+0x05 zeroed."""
    b = bytearray(packet)
    b[CHK_OFF:CHK_OFF + 2] = b"\x00\x00"
    return sum(b) & 0xFFFF


def seal(packet):
    out = bytearray(packet)
    struct.pack_into("<H", out, CHK_OFF, checksum(bytes(out)))
    return bytes(out)


#: WARNING:KEY: THE CLIENT'S RECEIVE BUFFER IS 15,000 BYTES, AND IT IS INLINE IN THE
#: CONNECTION OBJECT. Read off the ctor 0x6119A580, which does
#:
#:     lea eax, [esi + 0x004C]        ; the TX packet buffer
#:     lea ecx, [esi + 0x3AE4]        ; the RX packet buffer
#:     mov [esi + 0x757C], eax        ; -> the TX packet pointer
#:     mov [esi + 0x7580], ecx        ; -> the RX packet pointer
#:
#: so the RX buffer runs 0x3AE4..0x757C = 0x3A98 = 15,000 B, and **the very next
#: bytes after it are the object's own TX and RX pointers**. A frame longer than
#: that does not "get truncated" -- it writes over those pointers and everything
#: after them, and the client dies later on the wreckage. The object itself is
#: 0x7A16 = 31,254 B (allocated at 0x6100698F).
#:
#: That is exactly what killed the client on 2026-09-06: `0x018E` at 22,892 B
#: overran this buffer by 7,892 B, and the process died in teardown with a
#: smashed vtable pointer (AV at 0x61004361), identically in two dumps at
#: different heap bases. It is the SIZE, not the all-zero content -- the row
#: consumer 0x611CC973 explicitly treats `record+0x00 == 0` as "empty row", and
#: the parse's destination (the 26,104-B mission global) is big enough.
#: WARNING: The parse 0x611725B0 copies a FIXED 0x164D dwords from payload+0x24 no
#: matter how long the frame is, so a SHORT 0x018E is the correct form: the
#: over-READ past the object is what SE's own client always did and is harmless,
#: while the over-WRITE is what is fatal.
#: HDR (20 B) counts toward the limit -- the header is in the same buffer.
def build(msg, payload=b"", seq=SEQ_MIN, conn_id=0, flags=FLAGS_PLAIN_SUMMED):
    """Lay out a packet the way 0x61199fc0 does, then seal it.

    WARNING: Frames over CLIENT_RX_BUFFER are a MEMORY-CORRUPTING BUG, not a slow
    path: they overrun the client's inline RX buffer and smash the connection
    object's own pointers. We refuse to put one on the wire."""
    if HDR + len(payload) > CLIENT_RX_BUFFER:
        raise ValueError(
            f"msg 0x{msg:04X}: {HDR + len(payload)}-byte frame exceeds the "
            f"client's {CLIENT_RX_BUFFER}-byte RX buffer (conn+0x3AE4..0x757C) "
            f"by {HDR + len(payload) - CLIENT_RX_BUFFER} bytes. Sending it "
            f"overwrites conn+0x757C/0x7580 -- the TX and RX packet pointers -- "
            f"and kills the client (live 2026-09-06, 0x018E). Shorten the body.")
    pkt = bytearray(HDR + len(payload))
    struct.pack_into("<HHHHH", pkt, 0,
                     HDR + len(payload), flags, 0, msg, conn_id)
    struct.pack_into("<I", pkt, 0x10, seq)
    pkt[HDR:] = payload
    return seal(bytes(pkt))


def parse(packet):
    """Decode one packet. `chk_ok` is the client's own verification, run on us."""
    ln, flags, chk, msg, conn = struct.unpack_from("<HHHHH", packet, 0)
    return {
        "len": ln,
        "flags": flags,
        "flag_byte": (flags >> 8) & 0xFF,
        "encrypted": bool(((flags >> 8) & 0xFF) & FLAG_ENCRYPTED),
        "summed": bool(((flags >> 8) & 0xFF) & FLAG_CHECKSUM),
        "chk": chk,
        "chk_calc": checksum(packet),
        "chk_ok": chk == checksum(packet),
        "msg": msg,
        "conn": conn,
        "seq": struct.unpack_from("<I", packet, 0x10)[0],
        "payload": packet[HDR:ln],
    }


#: Region enum, from the config block at 0x611760xx -- it picks the world host
#: AND rides in the credentials payload at +0x34.
REGIONS = {0x0A: "W20 -> fmo01", 0x0B: "DWJ -> fmo02",
           0x0C: "W2U -> fmo03", 0x0D: "DWU -> fmo04"}
CRED_LEN = 0x50
#: VERIFIED: THE PS2 CONSOLE SENDS A SHORTER CREDENTIALS PAYLOAD -- 52 bytes, and that
#: is the WHOLE message, not a truncation. `ps2jp_050324_1531` predates the PC
#: build by 17 months and its sender has no region and no identity field:
#:
#:     midas.pex 0x0030928c   addiu a1, zero, 0x321   ; message id
#:               0x00309294   addiu a2, zero, 52      ; payload size, hard-coded
#:               0x0030929c   jal   0x002fb8d0        ; init(msg, size) -> ptr
#:               0x003092d8   jal   0x00100ad8(dst=payload, src=s2+0x114f4, 52)
#:
#: One memcpy of one 52-byte blob and the packet goes. So the console's payload
#: is exactly the PC's `+0x00 52B polcore auth blob` with the three later fields
#: (region, identity, zero tail) never appended -- which is why refusing to
#: decode it was right in 2026-08-23 and is wrong now that the sender has been
#: read. `midas.pex` is the plaintext module off the PS2 build drive image's
#: partition PP.SLPM-65981.0004.FMO, base 0x280000 (the build drive holds the
#: modules decrypted).
#:
#: KEY: AND THE REPLY NEEDS NO PS2 FLAVOUR. The console's 0x0322 handler
#: (0x003093a8) reads, off payload base `s0 = packet + 0x14`:
#:     +0x00  8B  -> ctx+0x11540      +0x08  u32 -> ctx+0x11548  (our key tail)
#:     +0x0C  u32 -> ctx+0x1153C      (field A -- the session token)
#:     +0x14  20B -> ctx+0x1154C      endpoint 1, then handed straight to the
#:                                    connect arm 0x002fb9e0 (port at +0x02,
#:                                    address at +0x04 -- `endpoint()`'s struct)
#:     +0x28  20B -> ctx+0x11574      endpoint 2
#: The last byte it touches is +0x3B, i.e. exactly CRED_REPLY_LEN. Every offset
#: matches the PC's, so cred_reply() is already correct for the console.
CRED_LEN_PS2 = 0x34


def parse_credentials(payload):
    """Decode the 0x0321 payload. None if it is not a size a sender builds.

    Two senders: the PC's 80-byte form and the PS2's 52-byte one. `form` says
    which; `region` and `identity` are None for the console because they are
    not fields of its message -- do not read them as zero."""
    if len(payload) == CRED_LEN_PS2:
        return {
            "form": "ps2",
            "auth": payload[0:0x34],        # polcore's, opaque to FMO
            "region": None,
            "region_name": "not sent (PS2 build)",
            "identity": None,
            "tail": b"",
        }
    if len(payload) != CRED_LEN:
        return None
    return {
        "form": "pc",
        "auth": payload[0:0x34],            # polcore's, opaque to FMO
        "region": struct.unpack_from("<I", payload, 0x34)[0],
        "region_name": REGIONS.get(
            struct.unpack_from("<I", payload, 0x34)[0], "unknown"),
        "identity": payload[0x38:0x48],     # stable per install
        "tail": payload[0x48:],
    }


def describe(p):
    name = msgnames.MSG_NAMES.get(p["msg"], "unknown")
    return (f"msg=0x{p['msg']:04X} ({name}) len={p['len']} "
            f"flags=0x{p['flags']:04X}"
            f"{' ENCRYPTED' if p['encrypted'] else ''} "
            f"seq=0x{p['seq']:04X} conn=0x{p['conn']:04X} "
            f"chk=0x{p['chk']:04X} "
            + ("OK" if p["chk_ok"] else
               f"MISMATCH (computed 0x{p['chk_calc']:04X})"))


# Called at run time only; imported last so that import cycles resolve.
from . import msgnames  # noqa: E402
