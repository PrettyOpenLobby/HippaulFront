#!/usr/bin/env python3
"""fmoworld.py -- FRONT MISSION ONLINE's UDP world channel: cipher, framing and
the reliable-delivery window, read off the client rather than guessed.

    python fmoworld.py --selftest

Every address is a live VA in the unpacked `FrontMissionOnline.dll`
(ImageBase 0x61000000, file offset == RVA). The research tool that cracked this
and can replay a capture is a separate, unshipped script; this module is the
serving half, and it is deliberately import-free so `fmo.py` can use it inside
the container.

STATE OF PROOF, stated plainly because this project has a documented habit of
promoting a mechanism to a fix:

  * VERIFIED: The cipher, the key and the frame are VERIFIED against 177 real captured
    datagrams: all 177 decrypt and pass the CLIENT'S OWN MD5, and re-encoding
    them reproduces the captured bytes exactly. That check is the client's, not
    ours -- the digest is over the client's plaintext and we compare rather than
    produce it.
  * PARTIAL: The reply contract below -- which byte goes in +0x09, what +0x00 should
    be, that an ACK alone retires the client's backlog -- is READ FROM THE
    CLIENT'S CODE but has never been put on a wire. Treat every "must" here as
    "the client's code says", not as "measured".

WHY THERE IS A PEER MAP AT ALL (SE's own words, 2026-09-12): retail FMO ran
movement, chat, VOICE and BATTLE DAMAGE peer-to-peer between clients -- the
server carried the lobby and the peers carried each other (SE's "[！]が表示され
る場合" page, work/webarchive/text/www.playonline.com/fmo/topics/050701/
network.html.txt: when peer-to-peer fails "the other pilot stands still, chat
and voice chat stop, and in battle neither side can damage the other"). This
server stands in for EVERY peer: one endpoint from 0x0153, one alias stream per
remote pilot, each with its own key. Two consequences:
  * a hit record is DELIVERED, never judged -- the shooter applies it to every
    unit but its own, the receiver only to its own (fmo.py _relay_battle_record);
    there was no server referee in retail either, so relaying verbatim is the
    faithful design and a PvP judge would be an addition.
  * the [！] marker the client draws over a pilot is SE's peer-to-peer FAILURE
    indicator. On this server it means THAT PILOT'S ALIAS STREAM failed (wrong
    key, MD5 mismatch at 0x610708AF, or a window drop below) -- never "the
    network". Look at the stream for that peer first.
(Source: SE's archived site, network.html.txt, audited 2026-09-12.)

THE FRAME (builder 0x61070520, validator 0x61070820)

    +0x00 u32  PEER ID       the handler (0x611E30BE) resolves this in the
                             manager's peer map; an id it does not know makes
                             it CREATE a peer rather than drop the datagram
    +0x04 u16  TOTAL LENGTH  four separate tests: nonzero, a multiple of 4, a
                             multiple of 8, and EQUAL to the bytes received;
                             then >= 0x28
    +0x06..+0x08             the builder never writes these -- on the client's
                             own datagrams they are stack leftovers
    +0x09 u8   HANDLER ID    looked up in the "UdpReqSys" map (0x611FECDF);
                             an unregistered id is dropped in SILENCE
    +0x0A u8   kind          the receiver stores this at peer+0x111C and the
                             sender puts it back -- it is echoed, both ways
    +0x0B u8   flag          0 lets the peer ADOPT our index base (see below)
    +0x0C 16B  MD5 of +0x1C..end
    +0x1C u16  TO      index one PAST the last record carried
    +0x1E u16  FROM    index of the first record carried
    +0x20 u16  ACK     the highest index the sender has taken FROM the peer
    +0x22 u16  MASK    0xFFFF; must be NONZERO (0x610708FC) -- the receiver
                       keeps it as the modulus for its window arithmetic
    +0x24 u16  the unpadded length        +0x26 u16 (not written)
    +0x28 ...  records: a 0x10-byte header then the payload (see REC_HDR)

WARNING: +0x1C / +0x1E are a RECORD INDEX RANGE, not a sequence and an ack. Verified
177/177: the number of records in a datagram is exactly TO - FROM. The real ack
is +0x20, and it read 0 in every captured datagram for the plainest possible
reason -- nothing had ever answered, so the client had nothing to acknowledge,
and it kept resending its whole backlog from FROM=0. **Do not re-label these as
seq/ack**; that reading survived one round here and explains nothing.

ORDER OF OPERATIONS: MD5 first, over +0x1C..end, into +0x0C; then encrypt from
+0x0C for (len - 0x0C) bytes. Receive is the mirror -- decrypt, then verify, and
a mismatch is dropped without a word. The cipher takes n>>3 WHOLE BLOCKS, so
with a length that is a multiple of 8 the LAST FOUR BYTES TRAVEL IN CLEAR.

THE CIPHER is Blowfish with two of its four S-boxes amputated to a single bit
(0x6106F8B0 encrypt, 0x6106FDD0 decrypt, 0x6106F4E0 schedule):

    F(x) = S0[x & 0xff] + S2[(x>>16) & 0xff]
         + ((S1[(x>>8) & 0xff] & 1) ^ 0x20)
         + ((S3[(x>>24) & 0xff] & 1) ^ 0x20)

Stock tables, stock 16 rounds, stock schedule, stock final swap and P[16]/P[17];
only F differs. WARNING: x is the LITTLE-ENDIAN dword read straight out of the buffer
and S0 takes its LOW byte -- reference Blowfish does both the other way round.
A right key with a wrong F is indistinguishable from a wrong key, which is how
this channel was once written down as "not Blowfish".

THE KEY is computed from data WE SEND (0x61002441):

    sprintf(key, "%xlobby", endpoint_port + endpoint_addr + character_id)

-- the port and address of the 20-byte endpoint the server puts in the 0x0153
join grant, each read as a little-endian word straight out of that struct, plus
the id of the selected character from the 0x012F list. WARNING: **Change the endpoint
or the character id and the key changes with it.** `fmo.py` derives it from the
same constants it serves, and the selftest pins that relation.
"""
import argparse
import hashlib
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
P_BIN = os.path.join(_HERE, "fmo_blowfish_P.bin")
S_BIN = os.path.join(_HERE, "fmo_blowfish_S.bin")

M32 = 0xFFFFFFFF

HDR_LEN = 0x28              # header + window fields; records start here
MIN_LEN = 0x28              # 0x61070876: cmp cx, 0x28 / jb -> drop
MD5_OFF = 0x0C
WIN_OFF = 0x1C
MASK_DEFAULT = 0xFFFF
MAX_RECORD = 0x800          # 0x61070A11: cmp eax, 0x800 / jae -> reject
MAX_COMMAND = 0x130         # 0x61070A1C: cmp [esi+4], 0x130 / jae -> reject

#: WARNING: THE RECORD HEADER IS 0x10 BYTES, NOT 8 -- the accept path says so itself:
#: 0x61070756 hands the handler `record + 0x10` as the payload and `size - 0x10`
#: as the length, and 0x61070778 passes the dword at +0x08 as a third argument.
#:
#:     +0x00 u32 SIZE     including this header; multiple of 4, < 0x800
#:     +0x04 u32 COMMAND  < 0x130
#:     +0x08 u32          handed to the handler (1 in every captured record)
#:     +0x0C u32          WARNING: ITS LOW 16 BITS MUST BE ZERO or the record is
#:                        REJECTED -- 0x61070B17 `test dword [rec+0xC], 0xFFFF`
#:                        / jne -> return 0, and the receive base never moves
#:     +0x10 ...          payload
#:
#: WARNING: In every captured record +0x0C is a FLOAT -- 1.0f on cmd 1 and 15,
#: 0x3E8E0000 on cmd 300 -- which is WHY its low word is zero. 91/91 of the
#: client's own records satisfy the rule, and that is what turned this from a
#: guess into a measurement.
REC_HDR = 0x10
REC_FLT_ONE = 0x3F800000    # 1.0f -- what the client puts at +0x0C

#: The two managers that register themselves in "UdpReqSys" (0x611FED20 takes
#: the id as a BYTE, which is why +0x09 is one). The world session is built by
#: 0x611E7810 with 2; the other manager (0x611D38B0) with 0.
HID_WORLD = 2
HID_OTHER = 0


def load_tables(p_path=None, s_path=None):
    """FMO's P/S, extracted from 0x6138D9E0 / 0x6138DA28 and shipped beside this
    module -- the same arrangement feblowfish.py uses, and for the same reason:
    a service must not reach into a build directory in another repo."""
    with open(p_path or P_BIN, "rb") as f:
        p_raw = f.read()
    with open(s_path or S_BIN, "rb") as f:
        s_raw = f.read()
    if len(p_raw) != 72 or len(s_raw) != 4096:
        raise ValueError("table size wrong: P=%d S=%d (want 72 / 4096)"
                         % (len(p_raw), len(s_raw)))
    P = list(struct.unpack("<18I", p_raw))
    S = [list(struct.unpack("<256I", s_raw[i * 1024:(i + 1) * 1024]))
         for i in range(4)]
    return P, S


def derive_key(addr_dword, port_word, char_id, kind="lobby"):
    """0x61002441. All three terms are values the SERVER chose.

    `kind` is the manager's literal suffix: the lobby world manager keys
    "%xlobby" from globals+0x3C's struct, and scene 4's battle UDP manager
    keys "%xbattle" from globals+0x28's (the 0x014E/0x013A endpoint) -- same
    sum, different tail (the fmo.py 0x013A note, measured live 2026-09-04:
    every scene-4 datagram failed to verify under every lobby key)."""
    return ("%x%s" % ((addr_dword + port_word + char_id) & M32, kind)).encode()


def key_for_endpoint(endpoint, char_id, kind="lobby"):
    """The key from the 20-byte endpoint struct AS SERVED, which is the only
    form that cannot drift out of step with it: the client reads exactly these
    bytes (globals+0x3E, globals+0x40) and never byte-swaps them.

    WARNING: Deriving from a host string and a port number instead would silently
    disagree the moment the endpoint's byte order changed -- and this protocol
    has already had one byte-order reversal (FMO_0153_EP_NET)."""
    if len(endpoint) < 8:
        raise ValueError("endpoint too short: %d bytes" % len(endpoint))
    port_word = struct.unpack_from("<H", endpoint, 2)[0]
    addr_dword = struct.unpack_from("<I", endpoint, 4)[0]
    return derive_key(addr_dword, port_word, char_id, kind)


def _f(S, x):
    return (S[0][x & 0xFF]
            + S[2][(x >> 16) & 0xFF]
            + ((S[1][(x >> 8) & 0xFF] & 1) ^ 0x20)
            + ((S[3][(x >> 24) & 0xFF] & 1) ^ 0x20)) & M32


def _enc_block(P, S, L, R):
    for i in range(16):
        L ^= P[i]
        R ^= _f(S, L)
        L, R = R, L
    L, R = R, L
    return L ^ P[17], R ^ P[16]


def _dec_block(P, S, L, R):
    for i in range(17, 1, -1):
        L ^= P[i]
        R ^= _f(S, L)
        L, R = R, L
    L, R = R, L
    return L ^ P[0], R ^ P[1]


def bf_init(key, tables=None):
    """0x6106F4E0. Stock schedule. WARNING: The key bytes are SIGN-EXTENDED into the
    P-array (movsx at 0x6106F530). No key this protocol produces has a byte
    >= 0x80 -- `%x` only emits 0-9a-f -- so the distinction is invisible today;
    it is implemented anyway so a future caller with a different key does not
    inherit a silent difference."""
    P, S = tables if tables else load_tables()
    P, S = list(P), [list(b) for b in S]
    if key:
        j = 0
        for i in range(18):
            data = 0
            for _ in range(4):
                b = key[j]
                data = ((data << 8) | (b - 0x100 if b >= 0x80 else b)) & M32
                j = (j + 1) % len(key)
            P[i] = (P[i] ^ data) & M32
    L = R = 0
    for i in range(0, 18, 2):
        L, R = _enc_block(P, S, L, R)
        P[i], P[i + 1] = L, R
    for box in range(4):
        for i in range(0, 256, 2):
            L, R = _enc_block(P, S, L, R)
            S[box][i], S[box][i + 1] = L, R
    return P, S


def crypt(P, S, buf, decrypt):
    """0x6106F8B0 / 0x6106FDD0 -- n>>3 WHOLE blocks; a trailing partial block is
    left in clear, deliberately, and that is not a bug to round away."""
    out = bytearray(buf)
    for off in range(0, (len(buf) // 8) * 8, 8):
        L, R = struct.unpack_from("<II", out, off)
        L, R = (_dec_block if decrypt else _enc_block)(P, S, L, R)
        struct.pack_into("<II", out, off, L, R)
    return bytes(out)


def records(plain):
    """The record area starts at +0x28 -- the receiver says so itself, parking
    its cursor at scratch+0x28 (0x610708D7). Stops at the UNPADDED length in
    +0x24, not at the end of the buffer, because the align-8 tail is garbage.
    WARNING: The header is 0x10 bytes, not 8 -- see REC_HDR. Yields the payload the
    handler is actually given (record+0x10), not the bytes after the size and
    command."""
    end = min(struct.unpack_from("<H", plain, 0x24)[0], len(plain))
    off = HDR_LEN
    while off + REC_HDR <= end:
        size, cmd = struct.unpack_from("<II", plain, off)
        if (size < REC_HDR or size % 4 or size >= MAX_RECORD
                or off + size > len(plain)):
            break
        yield off, size, cmd, bytes(plain[off + REC_HDR:off + size])
        off += size


def parse(P, S, dg):
    """Decrypt and validate EXACTLY as 0x61070820 does, and return None on any
    check the client would fail -- so what this module accepts is what the game
    accepts, rather than what is convenient to parse."""
    n = len(dg)
    if n < MIN_LEN or n % 8:
        return None
    ln = struct.unpack_from("<H", dg, 4)[0]
    if ln != n:                                  # 0x6107086E
        return None
    plain = bytes(dg[:MD5_OFF]) + crypt(P, S, dg[MD5_OFF:], decrypt=True)
    if hashlib.md5(plain[WIN_OFF:]).digest() != plain[MD5_OFF:WIN_OFF]:
        return None                              # 0x610708AF
    to, frm, ack, mask = struct.unpack_from("<HHHH", plain, WIN_OFF)
    if mask == 0:                                # 0x610708FC
        return None
    return {"peer": struct.unpack_from("<I", plain, 0)[0],
            "hid": plain[9], "kind": plain[0x0A], "flag": plain[0x0B],
            "to": to, "from": frm, "ack": ack, "mask": mask,
            "unpadded": struct.unpack_from("<H", plain, 0x24)[0],
            "records": list(records(plain)), "plain": plain}


def build(P, S, peer, hid, kind, ack, flag=0, frm=0, to=None, body=b"",
          mask=MASK_DEFAULT):
    """One datagram, sealed. `body` is pre-packed records; `to` defaults to
    `frm` + however many records `body` holds, because the client derives the
    record count from TO - FROM and a disagreement there is not detectable on
    the wire -- it just walks off the end of what we sent."""
    if to is None:
        cnt = 0
        off = 0
        while off + 8 <= len(body):
            size = struct.unpack_from("<I", body, off)[0]
            if size < 8 or off + size > len(body):
                break
            cnt += 1
            off += size
        to = (frm + cnt) & 0xFFFF
    raw = bytearray(HDR_LEN + len(body))
    raw[HDR_LEN:] = body
    unpadded = len(raw)
    if len(raw) % 8:                             # the client requires a multiple
        raw.extend(b"\0" * (8 - len(raw) % 8))   # of 8; +0x24 keeps the true end
    struct.pack_into("<I", raw, 0, peer)
    struct.pack_into("<H", raw, 4, len(raw))
    raw[9] = hid & 0xFF
    raw[0x0A] = kind & 0xFF
    raw[0x0B] = flag & 0xFF
    struct.pack_into("<HHHH", raw, WIN_OFF, to, frm, ack, mask)
    struct.pack_into("<H", raw, 0x24, unpadded)
    raw[MD5_OFF:WIN_OFF] = hashlib.md5(bytes(raw[WIN_OFF:])).digest()
    return bytes(raw[:MD5_OFF]) + crypt(P, S, bytes(raw[MD5_OFF:]), decrypt=False)


def seal(P, S, plain):
    """Re-apply the length, the MD5 and the cipher to a plaintext datagram,
    changing nothing else. This is the exact inverse of parse()'s crypto, and it
    is what the capture check uses -- build() cannot reproduce a captured
    datagram byte-for-byte, because the client leaves +0x06/+0x08/+0x26 as
    uninitialised stack and we write zeros there. That difference is deliberate
    (we are not going to forge stack litter) and it is exactly why the crypto
    has to be tested through this path instead."""
    b = bytearray(plain)
    struct.pack_into("<H", b, 4, len(b))
    b[MD5_OFF:WIN_OFF] = hashlib.md5(bytes(b[WIN_OFF:])).digest()
    return bytes(b[:MD5_OFF]) + crypt(P, S, bytes(b[MD5_OFF:]), decrypt=False)


class PeerWindow:
    """The client's side of the reliable window, as 0x61070820 implements it --
    so a test can ask "would the game have DROPPED this?" instead of only "does
    it parse?".

    WARNING: That distinction is the whole point. `parse()` is stateless and accepts a
    datagram the client would silently discard; a reply is only delivered if it
    ALSO survives this. The first version of the responder passed a full
    177-datagram loopback replay while sending indices this class rejects from
    the second datagram onward.

      adopt (0x61070902): while +0x106E is 0, a datagram with flag +0x0B == 0
                          sets base = its FROM, once.
      window (0x61070922): with mask = the datagram's +0x22,
                          (base - FROM) & mask <= mask/2   and
                          (TO - base) & mask <= mask/2,
                          or the datagram is dropped and a counter is bumped.
      base advances only as the peer CONSUMES records."""

    def __init__(self):
        self.base = 0
        self.adopted = False
        self.dropped = 0

    def accept(self, dg_fields, consume=True):
        """`consume=False` models a peer that takes the datagram but does NOT
        retire the records in it -- which is what the live client did with our
        first chat record, and the case that broke the channel. Its base does
        not move, so a sender that advanced on SEND immediately falls outside
        the window and is dropped for ever after."""
        frm, to = dg_fields["from"], dg_fields["to"]
        mask = dg_fields["mask"]
        if not self.adopted and dg_fields["flag"] == 0:
            self.base = frm
            self.adopted = True
        if ((self.base - frm) & mask) > mask // 2:
            self.dropped += 1
            return False
        if ((to - self.base) & mask) > mask // 2:
            self.dropped += 1
            return False
        if consume:
            self.base = (self.base + (to - frm)) & mask
        return True


#: The game-level command vocabulary, from the peer's own dispatcher
#: (0x611EBA50: `cmd - 7`, bounded at 0xE9, byte map 0x611EBC44, jump table
#: 0x611EBC24). WARNING: EIGHT commands exist and EVERYTHING ELSE IS SILENTLY IGNORED
#: -- 226 of the 234 ids in range return 1 without touching anything. The client
#: SENDS us cmd 1, 15 and 300, none of which are in this set, so the two
#: directions do not share a vocabulary and a captured id is not a sendable one.
CMD_POP = 7             # 0x611EBA73 -> 0x611EADC0: creates a unit. SE's own
                        # printf calls it a POP ("POP ERR %x ...")
CMD_UNK7 = CMD_POP      # the old name, kept so nothing silently breaks
CMD_DEPOP = 8           # WARNING: NOT in this dispatcher: 0x611EBC44 maps 8 to the
                        # default arm (`mov eax,1`). It is the sibling BATTLE
                        # peer class's command (0x611D4750 -> 0x611D3050 ->
                        # 0x611EE9E0 "RecvDepop"). See record_depop.
CMD_UNK9 = 9            # 0x611EBB8E -> 0x611E7670
CMD_DISCONNECT_A = 11   # 0x611EBBB1 -> set_state(4): 4 is the state the sender
CMD_DISCONNECT_B = 12   #   skips (0x611E27F0), i.e. this peer is finished
CMD_CHAT = 18           # 0x611EBBEF -> 0x611E7480
CMD_UNK115 = 115        # 0x611EBC04, into the object at [0x613C1C44]+0x2704
CMD_UNK211 = 211        # 0x611EBB6B
CMD_UNK240 = 240        # 0x611EBBDA

#: THE CLIENT'S OWN CHAT SUBMIT, cmd 115 -- decoded live 2026-08-22 with three
#: messages typed by the account holder, and it matches the static read of its
#: consumer 0x611D5C00 field for field:
#:
#:     +0x00 u32   2 in every sample (a channel or chat kind)
#:     +0x04 u32   0
#:     +0x08 u32   the SENDER'S UnitID -- 1, i.e. the player's own unit
#:     +0x0C u32   0
#:     +0x10 u8    0
#:     +0x11 17B   name1, NUL-terminated
#:     +0x22 17B   name2, NUL-terminated
#:     +0x33 ...   the TEXT, NUL-terminated; the record is sized to fit it
#:
#: The consumer proves the boundaries rather than the bytes suggesting them:
#: 0x611D5C18/1B write NUL to body+0x21 and body+0x32 (the two name field
#: terminators), 0x611D5C20 writes NUL to body+len-1 (the text), 0x611D5C15
#: takes the text as body+0x33, and 0x611D5C07 REFUSES anything shorter than
#: 0x34. It also refuses an all-blank message and any text starting "[GM]".
#:
#: WARNING: IT IS NOT cmd 18's LAYOUT. Chat up and chat down are different records --
#: 18 puts its name at +0x04 and text at +0x26 in a fixed 0x80 body, 115 packs
#: two names at +0x11/+0x22 and sizes itself to the text. Do not reuse offsets
#: across the two directions.
#:
#: VERIFIED: AND IT RETIRES THE NOTE ABOVE: the vocabulary is NOT one-directional.
#: cmd 115 is in the client's own receive dispatcher (CMD_UNK115) AND is sent
#: by it, so at least one id is genuinely bidirectional.
SUBMIT_MIN = 0x34
SUBMIT_KIND = 0x00          # u32
SUBMIT_UNITID = 0x08        # u32
SUBMIT_NAME1 = 0x11         # 17B
SUBMIT_NAME2 = 0x22         # 17B
SUBMIT_NAME_LEN = 17
SUBMIT_TEXT = 0x33


def parse_chat_submit(body):
    """A cmd-115 body -> {kind, unitid, name1, name2, text}, or None.

    None means the client itself would have refused it (0x611D5C07's length
    gate), so it is not something to answer -- saying so beats echoing a
    message the sender never successfully sent."""
    if len(body) < SUBMIT_MIN:
        return None
    kind = struct.unpack_from("<I", body, SUBMIT_KIND)[0]
    unitid = struct.unpack_from("<I", body, SUBMIT_UNITID)[0]

    def _s(off, n):
        return body[off:off + n].split(b"\0")[0].decode("cp932", "replace")

    return {"kind": kind, "unitid": unitid,
            "name1": _s(SUBMIT_NAME1, SUBMIT_NAME_LEN),
            "name2": _s(SUBMIT_NAME2, SUBMIT_NAME_LEN),
            "text": body[SUBMIT_TEXT:].split(b"\0")[0].decode("cp932",
                                                              "replace")}


#: 0x611E7480 formats "%s: %s" (0x6133DEA4) through the chat sink 0x611D57A0.
CHAT_NAME_OFF = 0x04    # strncpy(dst, body+4, 0x21) at 0x611E7497
CHAT_NAME_LEN = 0x21
CHAT_TEXT_OFF = 0x26    # tested for '/' at 0x611E74A7 -- a slash command
CHAT_BODY_LEN = 0x80    # covers the +0x7A dword the slash path reads


def record_chat(text, name="", channel=1):
    """One cmd-18 chat record.

    WARNING: `channel` is the body's FIRST dword and 0x611E7512 special-cases **6**
    onto a different format string; everything else formats as "<name>: <text>".

    WARNING: The body is padded to 0x80 whatever the text length, because the slash
    path reads a dword at +0x7A without ever checking the record's size. We do
    not send a leading '/' -- but a short record plus a client that reads past
    it is the same class of bug as the 0x0166 tail, from the other side."""
    body = bytearray(CHAT_BODY_LEN)
    struct.pack_into("<I", body, 0, channel)
    n = name.encode("cp932", "replace")[:CHAT_NAME_LEN - 1]
    body[CHAT_NAME_OFF:CHAT_NAME_OFF + len(n)] = n
    t = text.encode("cp932", "replace")[:CHAT_BODY_LEN - CHAT_TEXT_OFF - 8]
    body[CHAT_TEXT_OFF:CHAT_TEXT_OFF + len(t)] = t
    return record(CMD_CHAT, bytes(body))


#: --- cmd 240, the POSITION UPDATE -------------------------------------------
#:
#: VERIFIED: DECODED 2026-08-24, statically end to end AND against 296 of the client's
#: own records captured live on 2026-08-24. The dispatch arm `0x611EBBDA` hands
#: (body, size) to `0x611EABB0`, which is the whole contract:
#:
#:     611eabe3  ecx = [peer + 0x1AF5]        ; the manager
#:     611eabe9  eax = [peer + 0x10]          ; THE UNIT THIS STREAM IS ABOUT
#:     611eabec  edx = [manager + 0x2C]       ; the local player's own UnitID
#:     611eabf1  je  -> RETURN                ; "that is me" -- ignored outright
#:     611eabf7  ecx = [manager + 0x20]       ; the entity map
#:     611eabfd  call 0x61072650              ; look the unit up; a miss RETURNS
#:     611eac0c  size -= 0xC ; js -> RETURN   ; the 12-byte minimum
#:     611eac1c  eax = [body + 0x00]          ; a state/flags dword, kept
#:     611eac4f  ebp = body + 4
#:     611eac5a  call 0x611E6BB0              ; the position decoder
#:
#: WARNING: **THE MOVED UNIT IS NOT IN THE BODY.** It is `peer+0x10`, i.e. the unit
#: the DATAGRAM's own peer id names -- so one cmd-240 record can only ever move
#: one unit, and WHICH unit is chosen by the datagram header, not the payload.
#: That is the entire mechanism behind seeing another player walk, and it is why
#: relaying movement means opening a second record stream rather than sending a
#: second kind of record. See `fmo.py`'s room relay.
#:
#: `0x611E6BB0` reads four int16 from body+4: x, y, z at K1 = 0.01 and a fourth
#: at K2 = 0.001 that is rotation-shaped. The live capture agrees exactly -- the
#: first record of a session read `06 00 00 00 | 0000 f401 0000 f700`, i.e.
#: (0.00, 5.00, 0.00) which is the served spawn `FMO_UDP_POP_POS=0,5,0,0`.
#:
#: WARNING: The client's own records are 36 bytes, not 12. Bytes 0x0C..0x23 were zero
#: in all 296 captured, so they are UNDECODED, not absent: send the same 36 so
#: a size the client never produces is never the thing under test.
CMD_MOVE = CMD_UNK240
MOVE_MIN = 0x0C             # 0x611EAC0C's `sub size, 0xC / js -> return`
MOVE_BODY_LEN = 0x24        # what the client itself sends, 296/296
MOVE_FLAGS = 0x00           # u32; 6 and 0x26 both observed
MOVE_POS = 0x04             # int16 x, y, z   (K1 = 0.01, 0x6132A788)
MOVE_ROT = 0x0A             # int16           (K2 = 0.001, 0x6132DBF4)
MOVE_K_POS = 0.01
MOVE_K_ROT = 0.001
#: ±327.67 on each axis, and ±32.767 on the rotation, because int16 is int16.
MOVE_POS_MAX = 32767 * MOVE_K_POS
MOVE_ROT_MAX = 32767 * MOVE_K_ROT


def parse_move(body):
    """A cmd-240 body -> {flags, pos: (x, y, z), rot}, or None.

    None means the client's own handler would have dropped it on the 12-byte
    gate at `0x611EAC0C` -- which makes "too short to be a position" a distinct
    answer from "a position we could not read"."""
    if len(body) < MOVE_MIN:
        return None
    flags = struct.unpack_from("<I", body, MOVE_FLAGS)[0]
    x, y, z, rot = struct.unpack_from("<hhhh", body, MOVE_POS)
    return {"flags": flags,
            "pos": (x * MOVE_K_POS, y * MOVE_K_POS, z * MOVE_K_POS),
            "rot": rot * MOVE_K_ROT,
            "raw": bytes(body[:MOVE_MIN])}


def record_move(pos, rot=0.0, flags=6, body_len=MOVE_BODY_LEN):
    """One cmd-240 record: move THE UNIT THE DATAGRAM'S PEER ID NAMES to `pos`.

    WARNING: There is no unit id here and that is not an omission -- see the note
    above. Sending this on the wrong stream moves the wrong unit, and sending it
    on the player's OWN stream moves nothing at all (`0x611EABF1` returns).

    `flags` defaults to 6, the value the client sends most; 0x26 is the other
    one it produces and neither is decoded."""
    if len(pos) != 3:
        raise ValueError("pos is three floats (x, y, z); got %d" % len(pos))
    for axis, v in zip("xyz", pos):
        if not -MOVE_POS_MAX <= v <= MOVE_POS_MAX:
            raise ValueError("%s=%g is outside the ±%.2f cmd 240 can express"
                             % (axis, v, MOVE_POS_MAX))
    if not -MOVE_ROT_MAX <= rot <= MOVE_ROT_MAX:
        raise ValueError("rot=%g is outside the ±%.3f cmd 240 can express"
                         % (rot, MOVE_ROT_MAX))
    if body_len < MOVE_MIN:
        raise ValueError("a %d-byte cmd 240 is under the client's own %d-byte "
                         "minimum (0x611EAC0C)" % (body_len, MOVE_MIN))
    body = bytearray(body_len)
    struct.pack_into("<I", body, MOVE_FLAGS, flags & 0xFFFFFFFF)
    struct.pack_into("<hhhh", body, MOVE_POS,
                     *[int(round(v / MOVE_K_POS)) for v in pos],
                     int(round(rot / MOVE_K_ROT)))
    return record(CMD_MOVE, bytes(body))


def record(cmd, body=b"", arg8=1, flt=REC_FLT_ONE):
    """One record, with the 0x10-byte header the accept path requires.

    WARNING: `flt` MUST have a zero low word: 0x61070B17 does
    `test dword [rec+0x0C], 0xFFFF` and rejects the record outright otherwise,
    without advancing its receive base and without a word in any log. Our first
    chat record died there -- an 8-byte header put a name string on +0x0C -- and
    the only visible symptom was the client's +0x20 never moving.

    Padded to a multiple of 4 because 0x61070A0D rejects a size with either low
    bit set."""
    if flt & 0xFFFF:
        raise ValueError("record +0x0C must have a zero low word "
                         "(0x61070B17); got 0x%08X" % flt)
    body = body + b"\0" * ((-len(body)) % 4)
    return struct.pack("<IIII", REC_HDR + len(body), cmd, arg8, flt) + body


#: --- cmd 7, the POP (unit creation) ---------------------------------------
#:
#: WARNING: THIS IS THE ONLY THING THAT PUTS AN ENTITY IN THE WORLD. `0x61003120`
#: does not spawn the `0x0153` setup block's eight ids -- it LOOKS THEM UP in
#: `[[0x613CA470]+0x20]` and skips a miss silently. Measured live 2026-08-21:
#: map built, 100 buckets, **0 keys**, so nothing could ever spawn.
#:
#: The chain, all direct calls: `0x611EBA73` -> `0x611EADC0` -> `0x611EB088` ->
#: `0x611EA710` -> `0x611D2F40` -> `0x611E2020` (the entity registers ITSELF in
#: `manager->map[UnitID]`) -> `0x610729A0` (the hash insert).
#:
#: **SE named the fields**: `"no UnitType=%u UnitID=%x"` (`0x6134482C`, printed
#: at `0x611EB3F5`) and `"RecvDepop(UnitID=%x FromID=%u Status=%u)"`.
#:
#: VERIFIED: **THE BODY IS 0x1C8 BYTES, AND THAT IS CROSS-CHECKED, NOT ASSUMED.** Two
#: different arms copy it and both END at +0x1C8: the create arm at `0x611EB0FD`
#: takes 0x72 dwords from body+0, and the update arm at `0x611EB301` takes 0x5C
#: dwords from body+0x58. `0x58 + 0x5C*4 == 0x72*4 == 0x1C8`.
#:
#: WARNING: And the destination matters as much as the length: the create arm copies
#: the whole body to **entity+0x147**, which is exactly the block
#: `0x61003120` hands to the spawn at `0x611FCE50` (`lea edi, [eax+0x147]`).
#: So this body IS the unit description that gets rendered -- not a header that
#: points at one.
POP_BODY_LEN = 0x1C8
POP_UNITID = 0x04       # u32. THE HASH KEY. 0x611EADDD looks it up first; if it
                        # is already present the handler takes the UPDATE arm
                        # (0x611EB2CA) instead of creating.
POP_KIND = 0x18         # u32 -> 0x611D2FF0 (0->0, 2->2, 3->3, 4->4, else 1)
                        # -> the table at 0x611EB44C. 0/1/2 CREATE; 3/4/5 take
                        # the already-removed arm and need a flag we do not set.
POP_FLOAT38 = 0x38      # float, read at 0x611EB121 -- but ONLY for UnitType
                        # 4 and 30, which is why it is left alone by default.
#: VERIFIED: THE POSITION, and the reason the first POP created an invisible unit.
#: `0x611EB08F` does `lea esi, [body+0x1C]` and hands it to `0x6110F4B0`, which
#: copies FOUR DWORDS to `char+0x44`:
#:
#:     6110f4b5  mov esi, [edx]     ->  [char+0x44]
#:     6110f4bc  mov esi, [edx+4]   ->  [char+0x48]
#:     6110f4c2  mov esi, [edx+8]   ->  [char+0x4c]
#:     6110f4c8  mov edx, [edx+0xc] ->  [char+0x50]
#:
#: The same four are read back as floats by the scene's own code
#: (`0x611031ED` flds `blk+0x1C`, `0x611031F2` flds `blk+0x20`), so they are
#: x, y, z and a fourth component -- the same shape as the 0x0153 PilotPos.
#:
#: WARNING: THE WORLD IS ONLY ±327.67 UNITS ON EACH AXIS. cmd 240 carries positions as
#: int16 hundredths (`0x611E6BB0`, K1 = 0.01), so that is a hard bound on what
#: the wire can express -- a unit parked at 1000 is outside the representable
#: world, not merely far away.
POP_POS = 0x1C          # 4 floats -> char+0x44 via 0x6110F4B0
POP_NAME1 = 0x58        # 17B, memcpy'd to entity+0x10C (0x611EB0CF)
POP_NAME2 = 0x69        # 17B, memcpy'd to entity+0x11D (0x611EB0E1)
POP_UNITTYPE = 0x89     # byte -> entity+0x12E. Bounded at 0x611EB2B3.
#: VERIFIED:KEY: THE CAST-SWITCH NATION, decoded 2026-09-05 (fmoe280 shim trace + static).
#: The lobby script's cast create (0xE280) is gated behind an 0xE060 switch that
#: reads byte[self+0x1C3] and runs the OCU cast on ==1, the USN cast on ==2, and
#: SKIPS every cast block otherwise. entity+0x1C3 has no dedicated writer; it is
#: filled by the creator's bulk copy at 0x611EB10A: `mov esi,ebx; lea edi,
#: [ebp+0x147]; mov ecx,0x72; rep movsd` -> `char[0x147+k] = record[k]` (base 0;
#: the earlier `lea esi,[ebx+0x1C]` feeds the position call and is clobbered).
#: Proof in the same block: body+0x89 (UnitType) lands at char+0x1D0 = 0x147+0x89.
#: So entity+0x1C3 = body[0x1C3-0x147] = body[0x7C]. WARNING: First shipped as 0x98
#: (2026-09-05) from misreading the clobbered lea; live E060 stayed 0 -- the
#: byte went to an unused slot. Set body+0x7C to the character's nation on the
#: SELF pop and the client runs its own cast (the E280 NPCs are cloned from the
#: player, so they inherit it). Independent of the script-selector nation
#: lobby+0x8B4 (FMO_STATUS_NATION). Measured against the client's own parser.
POP_NATION = 0x7C       # byte -> entity+0x1C3, the 0xE060 cast-switch discriminant
#: body+0x27 -- THE SIDE (team) byte, static 2026-09-10: both battle unit
#: creators (0x6105DD77 wanzer, 0x6105E453 human) read `[edi+0x27]` off the
#: record they build from and pass it to the unit ctor 0x6105CD30, which
#: stores it at unit+0x80 -- the byte the objective/beacon arms compare between
#: units (`cmp dl, [eax+0x80]`) to tell friend from foe, and the human
#: creators also key the model on (== 1 -> 0x17, else 0x18). We have always
#: sent 0 here for every unit, so every unit is on the player's side. WARNING: Whether
#: `edi` there is the POP body itself or the entity's copy of it is not pinned
#: down; the offset is the same either way (entity+0x147 IS the body).
POP_SIDE = 0x27
#: WARNING: THE MODEL SELECTION, decoded 2026-08-22 from 0x611ED660 -- the function
#: that obtains the `pFmoUnit` whose absence prints "ERR!!! NULL=pFmoUnit".
#: The visual is built by `0x611FCE50(kind, sub, -1)` on the scene object, and
#: `kind` is chosen HERE:
#:
#:     al = byte [body+0x8E]
#:     test al, 8
#:     je   -> kind = byte [body+0x89]      ; bit 3 CLEAR: fall back to UnitType
#:     kind = switch (al >> 4):             ; bit 3 SET: the HIGH NIBBLE picks
#:              2 -> 3        3 -> 2
#:              4 -> a separate path (0x611FC0F0, by UnitID)
#:              5 -> 6        else -> 1
#:
#: and **kind 6 is the only one that passes a second byte**: `0x611ED72D` reads
#: `byte [body+0x8B]` and hands it to the creator as `sub`. That is the shape of
#: a model index, and it is a field we have never set.
#:
#: WARNING: WE HAVE ALWAYS SENT BOTH AS ZERO, so bit 3 was clear, the kind fell back to
#: UnitType, and the whole UnitType sweep of 2026-08-22 was moving the fallback
#: rather than the selector. That is why six legal types looked identical: they
#: ARE identical (0x611EB474 maps 0,1,2,3,5,6 to one arm), and the byte that
#: actually chooses was never touched.
#:
#: `body+0x8E = 0x58` (bit 3 set, high nibble 5) selects kind 6, which then
#: reads `body+0x8B`. That is the combination to try first.
POP_MODELFLAGS = 0x8E   # byte. bit 3 arms it; the high nibble selects the kind
POP_MODELSUB = 0x8B     # byte. only consulted when the kind resolves to 6
POP_NAME_LEN = 0x11

#: WARNING: THE HUMAN'S MODEL, and it is NOT the +0x8E/+0x8B selector.
#:
#: Measured wrong first, on 2026-08-24: `FMO_UDP_POP_MODEL=0x58:<n>` went out
#: live and changed nothing, because **UnitType 4 never reaches `0x611ED660`**.
#: The UnitType dispatch at `0x611EB2C3` sends type 4 to `0x611E7190`, type 30
#: to `0x611EA980` and EVERYTHING ELSE to `0x611ED760` -- and only that last
#: one consults the model selector. So +0x8E/+0x8B are real, and irrelevant to
#: the one type that renders a person.
#:
#: `0x611E7190` is four instructions of contract:
#:
#:     611e71ad  ebx = the POP body
#:     611e71b1  dl  = byte [ebx + 0x7A]
#:     611e71b6  cmp dl, 1
#:     611e71b9  setne al                 ; al = (body[0x7A] != 1)
#:     611e71bf  push 4                   ; the kind is HARDCODED
#:     611e71c1  call 0x611FCE50(4, al, -1)
#:
#: So the human has exactly **two** models and `body+0x7A` picks between them:
#: **1 gives one, anything else gives the other** -- and we have always sent 0,
#: which is why every character in the world is the same person.
#:
#: WARNING: Two models is not an appearance system. The creation record's four
#: appearance bytes cannot fit here. `0x611F1FD0` copies the WHOLE 0x1C8 body
#: into the model unit at +4, so the rest of the look is read from offsets in
#: that copy that are still unidentified -- this is one bit of it, not the
#: answer to "look like my character".
POP_TYPE4_MODEL = 0x7A  # byte. ==1 -> sub 0; anything else -> sub 1

#: VERIFIED:KEY: THE REST OF THE HUMAN'S LOOK -- decoded 2026-09-04, and it is the whole
#: reason a player wears an NPC. `0x611E7190` does NOT stop at the base model:
#: immediately after `0x611F1FD0` copies the WHOLE 0x1C8 body to `unit+4`, it
#: DRESSES the unit with two parts read back out of that copy:
#:
#:     611e71da  call  0x611f1fd0             ; body -> unit+4 (0x72 dwords)
#:     611e71df  movzx ecx, word [esi+0x18e]  ; == body+0x18A
#:     611e71e6  movzx ebx, word [esi+0x190]  ; == body+0x18C
#:     611e71f4  or    ecx, 0x24              ; (id << 16) | kind 0x24
#:     611e7202  call  0x611f5700(slot 0, ecx, 1, 0)
#:     611e71ff  or    ebx, 0x14              ; (id << 16) | kind 0x14
#:     611e7210  call  0x611f5700(slot 1, ebx, 1, 0)
#:
#: and two more bytes of the same copy are consumed at render time:
#:
#:     611f5c83  movzx eax, byte [esi+0x18c]  ; == body+0x188, and ONLY on a
#:               ; unit whose [esi+0x3b5] == 4 -- i.e. exactly UnitType 4.
#:               ; 1/2/3 pick 0.95 / 1.00 / 1.05 into unit+0x3b1 = the SCALE.
#:     61132252  movzx eax, byte [esi+0x18d]  ; == body+0x189. Valid 1..5, and
#:               ; 2 is the no-op; anything else runs the gender-keyed body
#:               ; morph 0x61151FA0(gender, 2*build-2).
#:
#: KEY: POSITIVE CONTROL, and it is why these are not "candidate offsets": the
#: CREATION PREVIEW dresses its mannequins the same way. `0x61012970` calls
#: `0x611FCE50(4, 0, -1)` and then the same two part kinds -- slot 0 =
#: `0x650024` (head id 101) and slot 1 = `[nation*4 + 0x613866C0]` (0/101/201)
#: -- and the size/build menu handlers `0x61012FC0` / `0x61013010` write the
#: player's picks to that preview unit's `+0x18C` / `+0x18D`, the same two
#: bytes. Same class, same fields; the world just reaches them through the POP
#: body instead of the creation scene record.
#:
#: KEY: AND THE FACE MENU IS ids 101..110. `0x61012E60` builds it by walking
#: `edi = 0x65` while `edi < 0x6F`, naming each id out of a GENDER-KEYED table
#: (`body+0x7A == 1` -> 0x613C12E8, else 0x613C0FCC) and storing the id ITSELF
#: as the row's value -- which is why the creation payload's +0x32 holds
#: 101/103/107. The same gender split is read back at `0x611F1FA1`, so a face
#: id only means anything beside the gender it was picked under: serve the two
#: from the same character or the head will not resolve.
#:
#: WARNING: WE HAVE ALWAYS SENT ALL FOUR AS ZERO. Head id 0 and body id 0 are not a
#: character -- they are whatever the part loader falls back to, which is why
#: every player wears an NPC and the only thing that has ever varied is the
#: base model the sex bit picks.
POP_TYPE4_SIZE = 0x188      # byte, 1..3 -> scale 0.95 / 1.00 / 1.05
POP_TYPE4_BUILD = 0x189     # byte, 1..5, 2 = no morph
POP_TYPE4_FACE = 0x18A      # u16, part kind 0x24 slot 0. 101..110, per gender
POP_TYPE4_UNIFORM = 0x18C   # u16, part kind 0x14 slot 1

#: `[nation*4 + 0x613866C0]` read verbatim -- the uniform the creation preview
#: dresses slot 1 with. The index is the creation record's +0x28 (`nation_byte`,
#: 1 OCU / 2 USN); entry 0 is 0, which is the same nothing we have been sending.
POP_UNIFORM_FOR_NATION = {1: 101, 2: 201}

#: The ranges above, as the guard uses them. Kept beside the constants so a
#: bound and the offset it belongs to cannot drift apart.
POP_LOOK_RANGES = {"size": (1, 3), "build": (1, 5), "face": (101, 110),
                   "uniform": (1, 0xFFFF)}
POP_LOOK_FIELDS = {"size": (POP_TYPE4_SIZE, 1), "build": (POP_TYPE4_BUILD, 1),
                   "face": (POP_TYPE4_FACE, 2),
                   "uniform": (POP_TYPE4_UNIFORM, 2)}

#: VERIFIED:KEY: THE WANZER'S PARTS -- the BATTLE dresser's input, decoded 2026-09-08.
#: This is the scene-4 answer to "0 of 12 part slots populated": the battle
#: unit is not dressed from the garage setup at all. It is dressed from ELEVEN
#: PART RECORDS CARRIED IN THIS POP BODY, and we have always sent them zero.
#:
#: The chain, every step read in FrontMissionOnline.dll:
#:
#:   0x611EB2C3  UnitType dispatch. The table at 0x611EB474 (index) + 0x611EB464
#:               (arms) maps type 4 -> 0x611E7190 (the human), 30 -> 0x611EA980,
#:               **0/1/2/3/5/6 -> 0x611ED760** and 7..29 -> the error arm.
#:   0x611ED830  0x611F1FD0(unit, body) = `rep movsd 0x72` -- the WHOLE 0x1C8
#:               body copied to unit+4. So unit+X == body+(X-4), which is the
#:               same mechanism the human's look block already rides.
#:   0x611ED845  call 0x611F70A0 -- THE BATTLE DRESSER.
#:   0x611F7150  for i in 0..10:
#:                 slot = byte [0x6139A6AC + i]        ; PART_ORDER below
#:                 id   = word [unit+0x90 + i*0xC]     ; == body+0x8C + i*0xC
#:                 kind = byte [unit+0x92 + i*0xC]     ; == body+0x8E + i*0xC
#:                 v    = (id << 16) | kind
#:                 if v == 0: continue                 ; KEY: THE SILENT SKIP
#:                 0x611F5700(slot, v, 1, &rec+4)
#:
#: KEY: THE RECORD IS THE ITEM RECORD'S TAIL. The lobby builder 0x61002FEB calls
#: the same writer as `0x611F5700(slot, (id<<16)|kind, 1, &item[+0x0C])` out of
#: a 24-byte item_record whose id is at +0x08 and kind at +0x0A. Here id is at
#: +0x00, kind at +0x02 and the 4th argument is &rec+0x04 -- the SAME three
#: relative offsets. So a POP part record is `item_record[0x08:0x14]`, and the
#: nine bytes we leave zero are the nine the lobby already leaves zero in an
#: item record that demonstrably draws (the Giza, live 2026-09-08).
#:
#: KEY: THE INDEX IS THE SETUP'S ITEM INDEX. The battle order 0x6139A6AC is
#: [1,0,3,2,5,4,7,6,9,8,10] and the lobby's slot->item map 0x6138A2A0 is
#: [1,0,3,2,5,4,7,6,4,4,10]. Both are the same involution on the nine slots the
#: lobby dresses, so record i here holds exactly what setup item index i holds
#: -- the starter blobs transpose without a remap. (Records 8 and 9 have no
#: lobby counterpart; the lobby aliases both of those slots to item 4.)
#:
#: WARNING: RECORD 0'S KIND BYTE **IS** POP_MODELFLAGS. body+0x8E is read twice: as
#: record 0's kind here, and by the model selector 0x611ED660 as "bit 3 arms
#: it, high nibble picks the kind". Those coexist by design -- NO valid item
#: kind has bit 3 set (0x11/0x13/0x21/0x31/0x41 and every 0xN2), so a part kind
#: always reads as "not armed, fall back to UnitType". But they cannot both be
#: set: `model_flags` and `parts` are refused together below, because
#: FMO_UDP_POP_MODEL=0x58 would also equip part kind 0x58 into slot 1.
#:
#: WARNING: UNITTYPE 4 NEVER READS THIS. The human goes to 0x611E7190, which dresses
#: two slots from the look block and never calls 0x611F70A0. Sending parts to a
#: type-4 POP writes 132 bytes the client will not read -- exactly the class of
#: change that black-screened lobby world entry (regression, 2026-09-04). The
#: caller must withhold them, and record_pop refuses the combination.
POP_PARTS = 0x8C            # 11 records, stride 0xC, ending at body+0x110
POP_PART_STRIDE = 0x0C
POP_PART_COUNT = 11         # 0x611F70A0's `cmp ebp,0xa / jle`
#: byte [0x6139A6AC + i] -- record index -> PART SLOT. Recorded for the record;
#: the client applies it, we do not.
POP_PART_ORDER = (1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 10)
#: The kinds 0x611758E0's jump-table map accepts; anything else lands on the
#: default arm and has no master table at all. Same set as fmo.py's
#: VALID_ITEM_KINDS -- duplicated here so record_pop can refuse a dud without
#: importing the lobby module.
POP_PART_KINDS = frozenset(
    [0x11, 0x13, 0x21, 0x31, 0x41] + [(n << 4) | 2 for n in range(1, 14)])


def pop_parts_block(parts):
    """[(index, kind, id)] -> the 132 bytes at body+0x8C, or ValueError.

    Refuses rather than clamps, for the reason the look block already documents:
    0x611F5700 skips a slot whose value is zero and the creator resolves nothing
    for an id with no master table, so a bad entry does not error anywhere in
    the client -- it produces the exact 'the part slot stayed empty' symptom
    this array exists to fix, and would read as the decode being wrong."""
    blk = bytearray(POP_PART_STRIDE * POP_PART_COUNT)
    seen = set()
    for idx, kind, item_id in parts:
        if not 0 <= idx < POP_PART_COUNT:
            raise ValueError(
                "part index %r is outside 0..%d; 0x611F70A0 reads exactly %d "
                "records" % (idx, POP_PART_COUNT - 1, POP_PART_COUNT))
        if idx in seen:
            raise ValueError("part index %d given twice" % idx)
        seen.add(idx)
        if kind not in POP_PART_KINDS:
            raise ValueError(
                "part kind %#04x has no master table (0x611758E0 sends it to "
                "the default arm), so slot %d would silently stay empty"
                % (kind, POP_PART_ORDER[idx]))
        if not 0 < item_id <= 0xFFFF:
            raise ValueError(
                "part id %r at index %d is not 1..65535; id 0 makes "
                "(id<<16)|kind non-zero with nothing behind it" % (item_id, idx))
        off = idx * POP_PART_STRIDE
        struct.pack_into("<H", blk, off, item_id)
        blk[off + 2] = kind
    return bytes(blk)

#: KEY: THE LOBBY "SELECT TARGET" GATE, body+0x48 bit 0x10 -- static 2026-09-05
#: (read off the client's own list builder). The client's only target-picking
#: UI in the lobby is the "Select target" list (systext 72:0; window class
#: vtable 0x6133BFE0, list builder 0x611824D6). It sweeps the entity map twice
#: -- class 0 first (NPCs: entity+0x1C == 0, drawn in their own colour), then
#: players -- and lists an entity only if it is within 5.0 units ([0x6132A7CC])
#: and +-45 deg ([0x61331D60]/[0x6132DC90]) of the player AND
#: `dword[entity+0x18F] & 0x10`. entity+0x18F is the creator's bulk copy of
#: BODY byte 0x48 (entity+0x147 = body+0, the same mapping that put the nation
#: at 0x7C), a byte every POP we have ever sent as ZERO -- so no entity, NPC or
#: player, has ever been listable there on this server. The list opens in mode 0
#: (NPCs + players) from the chat command `/target` with NO argument
#: (0x611DCEEF -> 0x61182880(0, 0)); the Trade menu opens it in mode 1
#: (players only). Confirming an entry runs the client's own `targetnpc <key>`
#: (class 0) or `target <key>` command, which writes the target lobby+0x2EAC
#: through 0x611D7E10. What the client does with an NPC TARGET afterwards is
#: NOT yet read -- this bit only gets the NPC INTO the list.
POP_TARGETABLE = 0x48        # byte; bit 0x10 -> entity+0x18F & 0x10
POP_TARGETABLE_BIT = 0x10
POP_NAMETAG_BIT = 0x01       # same byte, bit 0x01 -> the overhead name tag (0x611E84AE)
#: Set by fmo.py from FMO_UDP_POP_NAMES: OR the name-tag bit into every record.
NAMETAG = False
POP_NPC_NUMBER = 0x1B8       # u16 <- catalogue rec+0x40 (the E280 dresser's write)

#: KEY: THE LOBBY NPC CATALOGUE -- the 42 records the client's 0xE280 CREATE-NPC
#: dresser (0x61100C40) copies into a cast member's cloned body, keyed by the
#: script's typecode. Source: AI/F00/D07.DAT, FMDT-decoded offset 0x1555C,
#: stride 0x54, loaded live into [wm+0xAEA] (count [wm+0xAEE]); regenerate with
#: the FMDT decoder that produced it (not shipped). Columns:
#:   (name1 -> body+0x58, name2 -> body+0x69, body+0x7A = (rec+0x34 != 0) + 1,
#:    FACE -> body+0x18A u16, UNIFORM -> body+0x18C u16, SIZE -> body+0x188,
#:    BUILD -> body+0x189, rec+0x40 -> body+0x1B8 u16)
#: The lobby script (fmoscriptcast.py) creates id 0 with 30 (OCU) / 32 (USN),
#: id 0x13 with 31 / 33, the four operators ids 3..6 -- the row at the Map
#: Selector / Scramble Board, z = 5.46 -- with 34..37 (OCU) / 38..41 (USN), and
#: the recruit line ids 7..0x12 with 0..29. The names are SE's placeholders
#: ("NPC1"/"OPERATOR"); the dialogue's names come from the FMDT store.
#: WARNING: Faces 51..57 / 201..205, uniforms 102..104 / 201..205 and size 10 are
#: OUTSIDE the player look ranges above ON PURPOSE: they index the NPC part
#: tables, and they are exactly what SE's own dresser writes. npc_catalogue_dress
#: therefore returns them as raw `extra` pokes and bypasses POP_LOOK_RANGES.
NPC_CATALOGUE = {
     0: ('NPC12', 'OPERATOR', 1, 51, 104, 10, 0, 7),
     1: ('NPC13', 'OPERATOR', 2, 55, 103, 10, 0, 13),
     2: ('NPC5', 'OPERATOR', 1, 51, 103, 10, 0, 5),
     3: ('NPC0', 'OPERATOR', 1, 51, 102, 10, 0, 0),
     4: ('NPC8', 'OPERATOR', 2, 51, 103, 10, 0, 8),
     5: ('NPC10', 'OPERATOR', 2, 53, 102, 10, 0, 10),
     6: ('NPC2', 'OPERATOR', 1, 53, 103, 10, 0, 2),
     7: ('NPC9', 'OPERATOR', 2, 52, 104, 10, 0, 9),
     8: ('NPC4', 'OPERATOR', 1, 55, 102, 10, 0, 4),
     9: ('NPC3', 'OPERATOR', 1, 54, 104, 10, 0, 3),
    10: ('NPC0', 'OPERATOR', 1, 51, 102, 10, 0, 0),
    11: ('NPC2', 'OPERATOR', 1, 53, 103, 10, 0, 2),
    12: ('NPC12', 'OPERATOR', 2, 54, 103, 10, 0, 12),
    13: ('NPC18', 'OPERATOR', 2, 52, 103, 10, 0, 16),
    14: ('NPC1', 'OPERATOR', 1, 52, 103, 10, 0, 1),
    15: ('NPC12', 'OPERATOR', 1, 51, 201, 10, 0, 7),
    16: ('NPC13', 'OPERATOR', 2, 51, 205, 10, 0, 13),
    17: ('NPC4', 'OPERATOR', 1, 55, 202, 10, 0, 4),
    18: ('NPC0', 'OPERATOR', 1, 51, 202, 10, 0, 0),
    19: ('NPC8', 'OPERATOR', 2, 51, 205, 10, 0, 8),
    20: ('NPC10', 'OPERATOR', 2, 53, 202, 10, 0, 10),
    21: ('NPC2', 'OPERATOR', 1, 53, 205, 10, 0, 2),
    22: ('NPC9', 'OPERATOR', 2, 52, 201, 10, 0, 9),
    23: ('NPC5', 'OPERATOR', 1, 51, 203, 10, 0, 5),
    24: ('NPC3', 'OPERATOR', 1, 54, 201, 10, 0, 3),
    25: ('NPC0', 'OPERATOR', 1, 51, 202, 10, 0, 0),
    26: ('NPC2', 'OPERATOR', 1, 53, 205, 10, 0, 2),
    27: ('NPC12', 'OPERATOR', 2, 55, 203, 10, 0, 12),
    28: ('NPC18', 'OPERATOR', 2, 52, 205, 10, 0, 16),
    29: ('NPC1', 'OPERATOR', 1, 52, 203, 10, 0, 1),
    30: ('Colette', 'Dirac', 2, 53, 103, 2, 1, 0),
    31: ('Malcolm', "O'Brien", 1, 57, 104, 3, 4, 0),
    32: ('Aretha', 'Schechner', 2, 51, 202, 2, 1, 0),
    33: ('Daniel', 'Taft', 1, 51, 203, 3, 4, 0),
    34: ('NPC1', 'OPERATOR', 1, 51, 102, 10, 0, 1),
    35: ('NPC1', 'OPERATOR', 1, 52, 103, 10, 0, 1),
    36: ('NPC1', 'OPERATOR', 1, 54, 104, 10, 0, 1),
    37: ('NPC1', 'OPERATOR', 1, 55, 102, 10, 0, 1),
    38: ('NPC1', 'OPERATOR', 1, 52, 203, 10, 0, 1),
    39: ('NPC1', 'OPERATOR', 1, 53, 205, 10, 0, 1),
    40: ('NPC1', 'OPERATOR', 1, 54, 201, 10, 0, 1),
    41: ('NPC1', 'OPERATOR', 1, 55, 202, 10, 0, 1),
    # KEY: THE HQ SCRIPT'S OWN CATALOGUE, as typecodes 100+. The 42 rows above
    # are D07's (LobbyEntry_geki, the tutorial, MapKind 500..519). EVERY REAL
    # LOBBY -- OCU 100..109/200..209 and USN 300..309/400..409 -- runs
    # AI/F00/D87.DAT (SCP 0x8070; LEV table AI/F08/D15.DAT), whose catalogue
    # names the people the counter event scripts speak as: `gunsou1_event`
    # (0x82081020) is Henry Viduka, First Sergeant (Carter Goodwin on the U.S.N.
    # side); `nina_event` (0x82081010) is the operator Kwangsu Son (Flora Norman
    # for U.S.N.); `tag_senior` (0x82080110), who registers the pilot, is Edward
    # Miura (Richard McAllister).
    # WARNING: CORRECTED 2026-09-05: these rows previously carried uniform=20 for
    # every entry -- rec+0x3C (a constant 20 in both files) had been read instead
    # of rec+0x3E, the column the dresser 0x61100C40 actually copies. 20 is not a
    # uniform id, which is why they rendered as "just a head". Regenerate, never
    # hand-transcribe: `fmonpccat.py --scp AI/F00/D87.DAT --python` (it locates
    # the block through its own offset table and prints where it found it).
    # AI/F00/D87.DAT -- catalogue block at 0x10300, 38 records (fmonpccat.py)
    100: ('Kwangsu', 'Son', 2, 54, 103, 3, 2, 0),
    101: ('Flora', 'Norman', 2, 52, 202, 3, 2, 0),
    102: ('Henry', 'Viduka', 1, 52, 103, 7, 2, 0),
    103: ('Edward', 'Miura', 1, 51, 103, 3, 5, 0),
    104: ('Carter', 'Goodwin', 1, 55, 203, 4, 9, 0),
    105: ('Richard', 'McAllister', 1, 54, 203, 7, 2, 0),
    106: ('Ann', 'Fuller', 2, 52, 103, 2, 2, 0),
    107: ('Doria', 'Knox', 2, 54, 202, 2, 1, 0),
    108: ('NPC12', 'OPERATOR', 1, 51, 104, 10, 0, 7),
    109: ('NPC8', 'OPERATOR', 2, 51, 103, 10, 0, 8),
    110: ('NPC5', 'OPERATOR', 1, 51, 103, 10, 0, 5),
    111: ('NPC0', 'OPERATOR', 1, 51, 102, 10, 0, 0),
    112: ('NPC8', 'OPERATOR', 2, 51, 103, 10, 0, 8),
    113: ('NPC10', 'OPERATOR', 2, 53, 102, 10, 0, 10),
    114: ('NPC2', 'OPERATOR', 1, 53, 103, 10, 0, 2),
    115: ('NPC9', 'OPERATOR', 2, 52, 104, 10, 0, 9),
    116: ('NPC4', 'OPERATOR', 1, 55, 102, 10, 0, 4),
    117: ('NPC3', 'OPERATOR', 1, 54, 104, 10, 0, 3),
    118: ('NPC0', 'OPERATOR', 1, 51, 102, 10, 0, 0),
    119: ('NPC2', 'OPERATOR', 1, 53, 103, 10, 0, 2),
    120: ('NPC12', 'OPERATOR', 2, 54, 103, 10, 0, 12),
    121: ('NPC18', 'OPERATOR', 2, 52, 103, 10, 0, 16),
    122: ('NPC1', 'OPERATOR', 1, 52, 103, 10, 0, 1),
    123: ('NPC12', 'OPERATOR', 1, 51, 201, 10, 0, 7),
    124: ('NPC13', 'OPERATOR', 2, 51, 205, 10, 0, 13),
    125: ('NPC4', 'OPERATOR', 1, 55, 202, 10, 0, 4),
    126: ('NPC0', 'OPERATOR', 1, 51, 202, 10, 0, 0),
    127: ('NPC8', 'OPERATOR', 2, 51, 205, 10, 0, 8),
    128: ('NPC10', 'OPERATOR', 2, 53, 202, 10, 0, 10),
    129: ('NPC2', 'OPERATOR', 1, 53, 205, 10, 0, 2),
    130: ('NPC9', 'OPERATOR', 2, 52, 201, 10, 0, 9),
    131: ('NPC5', 'OPERATOR', 1, 51, 203, 10, 0, 5),
    132: ('NPC3', 'OPERATOR', 1, 54, 201, 10, 0, 3),
    133: ('NPC0', 'OPERATOR', 1, 51, 202, 10, 0, 0),
    134: ('NPC2', 'OPERATOR', 1, 53, 205, 10, 0, 2),
    135: ('NPC12', 'OPERATOR', 2, 55, 203, 10, 0, 12),
    136: ('NPC18', 'OPERATOR', 2, 52, 205, 10, 0, 16),
    137: ('NPC1', 'OPERATOR', 1, 52, 203, 10, 0, 1),
    # 200..235 = the COLISEUM's own people (2026-09-09): the tougi lobby
    # script AH/F99/D47.DAT creates id 0x9 with 0|1 (Son|Norman) and ids
    # 0x13..0x27 with 2..16 | 19..33 -- SE's pairing is 0->1, k->k+17.
    # Named: 4 Elliot Duff, 6 Cathy Willis, 13 Yuji Kinoshita, 16 Jeannette
    # Davy (O.C.U.); 21 Ivan Alcott, 23 Susan Inness, 30 Patrick Schneider,
    # 33 Agnes Kelly (U.S.N.). Uniforms 152/252 and 3 are arena looks.
    # AH/F99/D47.DAT -- catalogue block at 0x046E0, 36 records (fmonpccat.py)
    200: ('Kwangsu', 'Son', 2, 54, 103, 3, 2, 0),
    201: ('Flora', 'Norman', 2, 52, 202, 3, 2, 0),
    202: ('NPC12', 'OPERATOR', 1, 51, 104, 10, 0, 7),
    203: ('NPC8', 'OPERATOR', 2, 51, 103, 10, 0, 8),
    204: ('Elliot', 'Duff', 1, 60, 152, 3, 2, 0),
    205: ('NPC0', 'OPERATOR', 1, 51, 102, 10, 0, 0),
    206: ('Cathy', 'Willis', 2, 56, 152, 1, 2, 0),
    207: ('NPC10', 'OPERATOR', 2, 53, 102, 10, 0, 10),
    208: ('NPC2', 'OPERATOR', 1, 53, 103, 10, 0, 2),
    209: ('NPC9', 'OPERATOR', 2, 52, 104, 10, 0, 9),
    210: ('NPC4', 'OPERATOR', 1, 55, 102, 10, 0, 4),
    211: ('NPC3', 'OPERATOR', 1, 54, 104, 10, 0, 3),
    212: ('NPC0', 'OPERATOR', 1, 51, 102, 10, 0, 0),
    213: ('Yuji', 'Kinoshita', 1, 54, 3, 4, 9, 0),
    214: ('NPC12', 'OPERATOR', 2, 54, 103, 10, 0, 12),
    215: ('NPC18', 'OPERATOR', 2, 52, 103, 10, 0, 16),
    216: ('Jeannette', 'Davy', 2, 28, 102, 3, 6, 0),
    217: ('NPC0', 'OPERATOR', 1, 51, 102, 10, 0, 0),
    218: ('NPC8', 'OPERATOR', 2, 51, 103, 10, 0, 8),
    219: ('Richard', 'McAllister', 1, 54, 203, 7, 2, 0),
    220: ('NPC13', 'OPERATOR', 2, 51, 205, 10, 0, 13),
    221: ('Ivan', 'Alcott', 1, 60, 252, 3, 2, 0),
    222: ('NPC0', 'OPERATOR', 1, 51, 202, 10, 0, 0),
    223: ('Susan', 'Inness', 2, 56, 252, 1, 2, 0),
    224: ('NPC10', 'OPERATOR', 2, 53, 202, 10, 0, 10),
    225: ('NPC2', 'OPERATOR', 1, 53, 205, 10, 0, 2),
    226: ('NPC9', 'OPERATOR', 2, 52, 201, 10, 0, 9),
    227: ('NPC5', 'OPERATOR', 1, 51, 203, 10, 0, 5),
    228: ('NPC3', 'OPERATOR', 1, 54, 201, 10, 0, 3),
    229: ('NPC0', 'OPERATOR', 1, 51, 202, 10, 0, 0),
    230: ('Patrick', 'Schneider', 1, 55, 3, 4, 9, 0),
    231: ('NPC12', 'OPERATOR', 2, 55, 203, 10, 0, 12),
    232: ('NPC18', 'OPERATOR', 2, 52, 205, 10, 0, 16),
    233: ('Agnes', 'Kelly', 2, 28, 202, 3, 6, 0),
    234: ('NPC0', 'OPERATOR', 1, 51, 202, 10, 0, 0),
    235: ('NPC8', 'OPERATOR', 2, 51, 205, 10, 0, 8),
}


def npc_catalogue_dress(typecode):
    """(name1, name2, extra) that makes a wire POP body carry what the client's
    own 0xE280 dresser would have written for `typecode` -- the same face,
    uniform, size, build, selector and NPC number, byte for byte. The caller
    still sets unit_type=4 (the dresser forces body+0x89 = 4 too). Unknown
    typecodes raise: nothing is guessed in."""
    if typecode not in NPC_CATALOGUE:
        raise ValueError("NPC typecode %r is not in the catalogue (0..41 = D07's "
                         "42 records, 100..117 = D87's HQ people; see "
                         "NPC_CATALOGUE)" % (typecode,))
    n1, n2, sel, face, uniform, size, build, w40 = NPC_CATALOGUE[typecode]
    extra = {
        POP_TYPE4_MODEL: bytes([sel]),
        POP_TYPE4_SIZE: bytes([size]),
        POP_TYPE4_BUILD: bytes([build]),
        POP_TYPE4_FACE: struct.pack("<H", face),
        POP_TYPE4_UNIFORM: struct.pack("<H", uniform),
        POP_NPC_NUMBER: struct.pack("<H", w40),
    }
    return n1, n2, extra

#: WARNING: THE CLIENT OBJECT SELECTOR, body+0x00 -- decoded 2026-08-24 from
#: `0x611EAE86`, and it is the field that makes a POP into a PLAYER rather than
#: a prop. After the KIND dispatch, the create path switches on body+0x00:
#:
#:     0 -> 0x611EAF49   new CFmoClient(mode 0)   <- what we have always sent
#:     1 -> (nothing)    no client object at all
#:     2 -> 0x611EAF11   new CFmoClient(mode 1)
#:     3 -> 0x611EAE9D   new CFmoClient(mode 2)
#:
#: Each of the three allocates **0x1E21 bytes** (`0x6124C11A`), which is exactly
#: the peer class -- `[peer+0x1AF5]` and `[peer+0x1E1D]` are its last fields --
#: and hands it to `0x611E7400`, whose base `0x611E36E0` does the one thing that
#: matters here:
#:
#:     611e36fa  eax = [manager + 0x1C]      ; THE PEER MAP
#:     611e370d  call 0x610729A0             ; intrusive insert, key = UnitID
#:     610729ba    mov [node + 0x10], key    ; <- and THIS is `peer+0x10`
#:
#: SE's own error string for the arm is `"ERR!!! pCli->Init()"`, so SE called it
#: a **client**. `0x61109FE0` ("Init") is a stub that returns `[peer+0x14]`, the
#: `1` the same insert writes.
POP_CLIENT_KIND = 0x00  # u32. 0/2/3 build the peer; 1 builds none

#: The three fields the client object is constructed FROM (`0x611EAF6A`):
#: `0x611E7400(manager, UnitID, mode, &body[0x08], body[0x30], body[0x34] &
#: 0xFFFF, body[0x36])`. `body+0x36` lands at `peer+0x111C`, the datagram
#: `kind` byte the peer echoes. WARNING: The other two are NOT decoded -- they are
#: address-shaped and this is where a peer-to-peer design would put a remote
#: endpoint, but nothing traces them to a sockaddr, and the remote-peer arm of
#: `0x611E2E10` creates NO socket (only the self peer does), so a relay through
#: our own socket should not need them. Left zero; named so a sweep can move
#: them without hunting the offsets again.
#: KEY: AND IT IS THE PEER STREAM'S BLOWFISH KEY -- decoded 2026-08-26, and the
#: reason a relayed player appears but never moves. 0x611E3720 passes `&body[0x08]`
#: and the length 16 into 0x610702F0, which STRLENs it and hands it straight to the
#: key schedule:
#:     610703a0  <strlen loop over the blob>   ; NUL-terminated STRING
#:     610703a9  push eax                      ; key length
#:     610703aa  lea  edx, [esi + 0x1e]        ; the peer stream's cipher state
#:     610703ad  push ebp                      ; the KEY = &body[0x08]
#:     610703af  call 0x6106F4E0               ; bf_init
#: So every peer stream has its OWN cipher, keyed from this field. We sent sixteen
#: zero bytes -- strlen 0, unkeyed tables -- while encrypting the alias stream with
#: the receiver's "%xlobby" key, so the client's MD5 check at 0x610708B2 failed and
#: the datagram was discarded BEFORE the window was ever consulted. Measured live
#: 2026-08-26: peers appear (the POP rides the correctly-keyed SELF stream) and
#: never move (movement rides the alias stream).
#: WARNING: It must be the SAME key the datagrams for that peer are encrypted with.
POP_CLIENT_BLOB = 0x08  # 16B, NUL-terminated Blowfish key string
POP_CLIENT_BLOB_LEN = 0x10
POP_CLIENT_A = 0x30     # u32
POP_CLIENT_B = 0x34     # u16
POP_CLIENT_KINDBYTE = 0x36  # byte -> peer+0x111C

#: WARNING:KEY: THE BATTLE "DESTROYED / cannot speak" GATE -- decoded 2026-09-04 (static,
#: [live sortie / destroyed-status worker]). Once the pilot is IN battle scene 4,
#: typing returns systext 35:61 "A completely destroyed player cannot speak."
#: That message has exactly TWO call sites (0x611D5806, 0x611D5DC5), both gated on
#: the predicate 0x61052F80 == `[0x613b6a7c] != 0`. `[0x613b6a7c]` is the camera's
#: SPECTATOR-TARGET unit; `[0x613b6b24]` is the player's OWN unit (0x6115C880
#: returns it). The per-frame arm 0x61064720, run on the own unit, sets
#: `[0x613b6a7c] = own unit` (i.e. "you are now spectating your own wreck") when
#: **0x611F25D0(own unit)** returns non-zero -- and THAT is the destroyed test:
#:
#:     611f25d0  ecx = [0x613C1698]              ; the battle connection manager
#:     611f25dd  eax = [unit + 0x08]             ; the unit's owner/char id
#:     611f25e6  call 0x61072650                 ; look the char up in [mgr+0x20]
#:     611f25ef  edx = [char + 0x1c]             ; <-- the CHAR STATUS field
#:     611f25f4  return (edx == 2) || (edx == 3) ; DESTROYED if status in {2,3}
#:
#: The char (a 0x34-byte CFmoConnectChar, ctor 0x611E2020, status stored at
#: char+0x1C) is created by the battle cmd-7 POP handler 0x611D4070 phase B, and
#: its status is a pure function of the POP body+0x00 client_kind, chosen by the
#: 4-way jump table 0x611D4530 (each arm pushes a hardcoded status constant that
#: 0x611E2020 writes to char+0x1C):
#:
#:     client_kind 0 -> 0x611D4321 push 1 -> status 1   (ALIVE)
#:     client_kind 1 -> 0x611D4385 push 0 -> status 0   (ALIVE)
#:     client_kind 2 -> 0x611D434A push 2 -> status 2   (DESTROYED)
#:     client_kind 3 -> 0x611D43BC push 3 -> status 3   (DESTROYED)
#:
#: WARNING: THE TRAP: the scene-4 ENTRY fix (2026-09-04, the scene-4 state-3
#: gate) needs client_kind == 3 -- the ONLY value that sets
#: `[selfpeer+0x10DC] = 2` (0x611D4281, the state 3->4 gate). But client_kind 3
#: ALSO makes the connection-char status 3, which 0x611F25D0 reads as DESTROYED.
#: So the same field that gets the pilot INTO the battle marks it destroyed.
#: The two cannot be reconciled inside a SINGLE POP (body+0x00 drives both phases
#: and body+0x18/`kind` is fixed to {0,1,2} by record_pop's own guard, all of
#: which run phase B).
#:
#: WARNING: A BARE RE-POP DOES NOT UNDO IT (corrected 2026-09-04 after a live miss). Phase
#: B looks the existing char up and dispatches on **char+0x20** (0x611D42C0 -> table
#: 0x611D4520); a fresh char has char+0x20==0 (ctor 0x611E2020), whose arm 0x611D44AB
#: is `mov eax,1; ret` -- no destruct+recreate, so char+0x1C is untouched. Only
#: char+0x20 in {1,2,3} takes the recreate arm 0x611D42D9. char+0x20 is set on the
#: wire ONLY by the battle **cmd 8 (depop)** handler 0x611D3050 -> 0x611E2070, which
#: does `[char+0x20]=N`: depop status 2 (DESTROYED) -> char+0x20=1 WITHOUT touching
#: the peer (the clisys +0x10E0=4 write at 0x611E21B0 fires only for status 0/1). So
#: the un-poison is TWO records: cmd 8 depop (status 2), THEN cmd 7 re-POP with an
#: alive client_kind -- SE's own link-death reconnect flow. See fmo.py
#: FMO_UDP_POP_UNDESTROY. The NULL this refutes:
#: "0 HP is a POP integer we send as 0" -- there is NO HP integer in the battle
#: POP path; destroyed is the status enum char+0x1C in {2,3}, set deterministically
#: by client_kind, not a durability value.
POP_CLIENT_KIND_TO_CHARSTATUS = {0: 1, 1: 0, 2: 2, 3: 3}
POP_CHARSTATUS_DESTROYED = frozenset((2, 3))


def client_kind_is_destroyed(client_kind):
    """True if a battle self-POP with this client_kind spawns the pilot as a
    DESTROYED connection-char (status char+0x1C in {2,3}, per 0x611F25D0)."""
    return POP_CLIENT_KIND_TO_CHARSTATUS.get(client_kind) in POP_CHARSTATUS_DESTROYED

#: UnitType is checked `> 0x1E` and then indexed through the byte table at
#: `0x611EB474` into `0x611EB464`. Decoded: **0,1,2,3,5,6** take the main arm,
#: **4** and **30** each take their own, and **7..29 are the ERROR arm** that
#: prints "no UnitType=%u UnitID=%x" and creates nothing.
POP_UNITTYPES_MAIN = (0, 1, 2, 3, 5, 6)
POP_UNITTYPES_SPECIAL = (4, 30)
POP_UNITTYPES_OK = tuple(sorted(POP_UNITTYPES_MAIN + POP_UNITTYPES_SPECIAL))


def record_pop(unit_id, unit_type=0, kind=0, name1="", name2="",
               pos=(0.0, 0.0, 0.0, 0.0), extra=None,
               model_flags=None, model_sub=None, client_kind=0,
               type4_model=None, client_key=None, look=None, nation=None,
               parts=None, side=None):
    """One cmd-7 record: create unit `unit_id` in the client's entity map.

    WARNING: EVERY FIELD NOT LISTED IN THE POP_* CONSTANTS IS SENT AS ZERO, and that is
    a guess, not a decode. 0x1C8 bytes reach the client and only nine of them
    have a known meaning; the rest are copied wholesale into the block the
    renderer reads. **A unit that registers but does not draw is the expected
    first result**, and the map's key count is what says the record was
    accepted -- do not read a blank screen as a rejected record.

    `extra` is {offset: bytes} for probing those unknowns without editing code.
    `parts` is [(item index, kind, id)] -- the wanzer's eleven part records at
    body+0x8C. See POP_PARTS: only UnitTypes 0/1/2/3/5/6 read them.
    """
    if unit_type not in POP_UNITTYPES_OK:
        raise ValueError(
            "UnitType %d hits the error arm at 0x611EB3F0 (legal: %s)"
            % (unit_type, ", ".join(str(u) for u in POP_UNITTYPES_OK)))
    if kind not in (0, 1, 2):
        raise ValueError(
            "kind %d does not reach the CREATE arm; 0x611EB44C sends 3/4/5 to "
            "the already-removed path (legal: 0, 1, 2)" % kind)
    if client_kind not in (0, 1, 2, 3):
        raise ValueError(
            "client_kind %d is not one of 0x611EAE86's four arms "
            "(legal: 0, 2, 3 build a peer; 1 builds none)" % client_kind)
    if not unit_id:
        # 0 is the value the setup block already carries eight of, and it is
        # what every miss looks like. Refusing it keeps "we sent nothing" and
        # "we sent an id" distinguishable in the map.
        raise ValueError("UnitID 0 is indistinguishable from an empty slot")
    if len(pos) != 4:
        raise ValueError("pos is four floats (x, y, z, w); got %d" % len(pos))
    # WARNING: Refuse the unrepresentable rather than sending it. The world channel
    # cannot express a coordinate outside ±327.67, so a unit placed there could
    # never be moved or reported afterwards -- and "it did not appear" would be
    # indistinguishable from the record having been rejected.
    for axis, v in zip("xyzw", pos):
        if not -327.67 <= v <= 327.67:
            raise ValueError(
                "%s=%g is outside the ±327.67 the wire can express "
                "(cmd 240 is int16 hundredths, 0x611E6BB0)" % (axis, v))
    body = bytearray(POP_BODY_LEN)
    struct.pack_into("<I", body, POP_UNITID, unit_id & 0xFFFFFFFF)
    struct.pack_into("<ffff", body, POP_POS, *pos)
    struct.pack_into("<I", body, POP_KIND, kind)
    struct.pack_into("<I", body, POP_CLIENT_KIND, client_kind)
    body[POP_UNITTYPE] = unit_type & 0xFF
    if nation is not None:
        # entity+0x1C3, the 0xE060 cast-switch discriminant. Only 1/2 select a
        # cast; refuse anything else rather than silently skip every NPC.
        if nation not in (1, 2):
            raise ValueError(
                "nation %r is not 1 (O.C.U.) or 2 (U.S.N.); the 0xE060 cast "
                "switch skips every cast block for any other value" % (nation,))
        body[POP_NATION] = nation & 0xFF
    for off, text in ((POP_NAME1, name1), (POP_NAME2, name2)):
        b = text.encode("cp932", "replace")[:POP_NAME_LEN - 1]
        body[off:off + len(b)] = b
    if model_flags is not None:
        body[POP_MODELFLAGS] = model_flags & 0xFF
    if model_sub is not None:
        body[POP_MODELSUB] = model_sub & 0xFF
    if type4_model is not None:
        body[POP_TYPE4_MODEL] = type4_model & 0xFF
    if side is not None:
        body[POP_SIDE] = side & 0xFF
    #: `look` is {size, build, face, uniform} -- the four fields UnitType 4
    #: reads out of the body copy. Absent keys stay zero, which is the old
    #: behaviour, so a partial roster record still pops.
    #: WARNING: Out of range is REFUSED, not clamped. The client clamps nothing here:
    #: `0x611A4530` returns NULL for a face id outside the table and the head
    #: simply never attaches, which on screen is indistinguishable from "this
    #: offset is not the face". A caller that cannot vouch for a value must
    #: drop it and say so, rather than let a silent miss look like a decode.
    for _k, _v in (look or {}).items():
        if _v is None:
            continue
        if _k not in POP_LOOK_FIELDS:
            raise ValueError("look field %r is not one of %s"
                             % (_k, ", ".join(sorted(POP_LOOK_FIELDS))))
        _lo, _hi = POP_LOOK_RANGES[_k]
        if not _lo <= _v <= _hi:
            raise ValueError(
                "look %s=%r is outside %d..%d; the client resolves no part "
                "for it and the slot stays empty" % (_k, _v, _lo, _hi))
        _off, _w = POP_LOOK_FIELDS[_k]
        if _w == 1:
            body[_off] = _v
        else:
            struct.pack_into("<H", body, _off, _v)
    if client_key:
        # The peer stream's Blowfish key, NUL-terminated. Refuse one that will
        # not fit rather than truncating: a truncated key is a key, it schedules
        # cleanly, and every datagram on that stream would then be dropped for a
        # reason nothing reports.
        if len(client_key) + 1 > POP_CLIENT_BLOB_LEN:
            raise ValueError(
                "client_key %r is %d bytes; the field is %d including the NUL"
                % (client_key, len(client_key), POP_CLIENT_BLOB_LEN))
        if b"\0" in client_key:
            raise ValueError("client_key contains a NUL; strlen would cut it")
        body[POP_CLIENT_BLOB:POP_CLIENT_BLOB + len(client_key)] = client_key
    if parts:
        # WARNING: Both guards refuse rather than drop. A type-4 POP that quietly
        # carried 132 unread bytes, or a model_flags that quietly became a part
        # kind, would each produce a WRONG SCREEN with a clean log -- and this
        # file's ledger says that is how four days went last time.
        if unit_type == 4:
            raise ValueError(
                "UnitType 4 goes to 0x611E7190 and never calls the part "
                "dresser 0x611F70A0; %d part records would be 132 bytes the "
                "client copies and never reads" % len(parts))
        if model_flags:
            raise ValueError(
                "model_flags (body+0x8E) IS part record 0's kind byte; "
                "%#04x would also be equipped as a part kind into slot %d"
                % (model_flags, POP_PART_ORDER[0]))
        blk = pop_parts_block(parts)
        body[POP_PARTS:POP_PARTS + len(blk)] = blk
    for off, raw in (extra or {}).items():
        if off + len(raw) > POP_BODY_LEN:
            raise ValueError("extra at %#x overruns the %#x-byte body"
                             % (off, POP_BODY_LEN))
        body[off:off + len(raw)] = raw
    if NAMETAG:
        # KEY: THE OVERHEAD NAME TAG (static 2026-09-12): the drawer 0x611E8440
        # skips any entity whose +0x18F (this byte) lacks bit 0x01, then
        # honours the `/names` global ([globals+0x1F0] & 2) and prints
        # "%s %s" from +0x10C/+0x11D (POP_NAME1/2) above +0x18B. Never set
        # before today, so no NPC or player ever had a name over their head.
        body[POP_TARGETABLE] |= POP_NAMETAG_BIT
    return record(CMD_POP, bytes(body))


#: --- cmd 8, the DEPOP (unit removal) -- decoded 2026-08-26 -----------------
#:
#: SE's own format string "RecvDepop(UnitID=%x FromID=%u Status=%u)\n" is at
#: 0x613449C0 and is printed at 0x611EEA0A, inside 0x611EE9E0, straight out of
#: the record body -- `[body+0]`, `[body+4]`, `[body+8]` (0x611EE9FC..0x611EEA08).
#: So the body is three dwords:
#:
#:     +0x00 u32 UnitID   the entity-map key ([manager+0x20] via 0x61072650)
#:     +0x04 u32 FromID   only PRINTED; nothing in the handler consumes it
#:     +0x08 u32 Status   the switch at 0x611EEA23 (and 0x611D308C before it):
#:         0  "left"      -> "(Operator)[%s %s]は離脱しました。" (0x61343170),
#:                          entity state 2 (0x611E2070), its PEER -> state 4
#:                          (0x611E21B0 at 0x611D314D), and the unit is taken
#:                          out of the scene list [lobby+0x1DC] (0x611FAA70).
#:                          The entity RECORD stays in the map.
#:         1  "lost comms" -> "(Operator) Lost comms with [%s %s]." (0x61343194),
#:                          entity state 2, peer -> state 4, and NOTHING else.
#:         2  "destroyed" -> "...は撃破されました。" (0x613431C0), entity state 1,
#:                          wreck/explosion objects (0x610BE9B0 / 0x610CA530),
#:                          peer untouched, entity kept.
#:         3  remove      -> scene-list removal AND the entity is destroyed
#:                          (0x611D2F80 dtor + free at 0x611EEAA4). The full
#:                          removal. Peer untouched.
#:         other          -> printed, nothing happens.
#:
#: The chain: dispatcher 0x611D4750 (`cmd - 5`, bounded 0xD7, byte map
#: 0x611D4984, jump table 0x611D4950; index 2 = cmd 8) -> arm 0x611D479F, which
#: first requires the RECEIVING PEER to be the local player (`[peer+0x10] ==
#: [manager+0x2C]`, else 0xFFFF8ACC) -- so it rides the SELF stream, exactly
#: like the POP -> 0x611D3050 (Operator message, entity state, peer state 4)
#: -> 0x611EE9E0 (the RecvDepop print and the Status switch above).
#:
#: WARNING: AND THAT DISPATCHER IS NOT THE LOBBY'S. 0x611D4750 is slot 1 of vtable
#: 0x61343164 -- the peer class built (0x611D2F90 -> base 0x611E36E0) by the
#: hid-0 manager 0x611D38B0, whose constructor prints "(Operator) Entering the
#: battle map now" and whose cmd-7 POP is a different function (0x611D4070,
#: "POP ERR %x %x %u %u %u"). Every world channel we serve is the hid-2 lobby
#: session (0x611E7810); its peers are vtable 0x61344770 with dispatcher
#: 0x611EBA50, and 0x611EBC44 maps cmd 8 to the default arm (`mov eax,1 /
#: ret`). **In the lobby a cmd 8 is consumed and ignored.** The two vocabularies:
#: battle = 5, 7, 8, 9, 11, 12, 13, 19, 118, 119, 124, 220; lobby = 7, 9, 11,
#: 12, 18, 115, 211, 240. Each manager allocates its OWN CFmoConnectCliSys and
#: CFmoConnectCharSys (0x611E2410), so a hid-0 datagram cannot reach the
#: lobby's maps either.
#:
#: WARNING: NO LOBBY DEPOP WAS FOUND (static, 2026-08-26). The lobby entity destructor
#: 0x611EA8D0 has four callers: the POP error arm (0x611EB408), the tick's
#: `[entity+0x31B] == 1` arm (0x611EB5DB -- that dword is the POP handler's
#: LOCAL third argument, 1 only from the client's own NPC placer 0x61100202,
#: never from the wire, and read as "locally placed" at 0x610FF350 /
#: 0x61101540), the type-30 rebuild failure (0x611EB71D), and the vtable delete
#: 0x611EBA30 (teardown). cmd 11/12 on a remote peer returns 0xFFFF8ACC and the
#: receive path ignores the return (0x611E32C3). A POP UPDATE of an existing
#: UnitID tears its visual down (0x611E72E0) and the tick re-dresses it
#: (0x611EB6D4). Out-of-range units are merely not RE-dressed (0x611EB77D).
#: Nothing on the wire removes a lobby unit.
CMD_DEPOP_BODY_LEN = 0x0C
DEPOP_UNITID = 0x00
DEPOP_FROMID = 0x04
DEPOP_STATUS = 0x08
DEPOP_LEFT = 0          # scene-list removal + peer state 4, entity kept
DEPOP_LOST_COMMS = 1    # peer state 4 only
DEPOP_DESTROYED = 2     # wreck effect, entity kept
DEPOP_REMOVE = 3        # scene-list removal + entity destroyed
DEPOP_STATUSES = (DEPOP_LEFT, DEPOP_LOST_COMMS, DEPOP_DESTROYED, DEPOP_REMOVE)


def parse_depop(body):
    """A cmd-8 body -> {unit_id, from_id, status}, or None if short.

    0x611EE9FC reads the three dwords unconditionally, so a short body would
    be read as whatever follows it; refuse rather than invent."""
    if len(body) < CMD_DEPOP_BODY_LEN:
        return None
    unit_id, from_id, status = struct.unpack_from("<III", body, 0)
    return {"unit_id": unit_id, "from_id": from_id, "status": status,
            "raw": bytes(body[:CMD_DEPOP_BODY_LEN])}


def record_depop(unit_id, from_id=0, status=DEPOP_REMOVE):
    """One cmd-8 record: RecvDepop(UnitID, FromID, Status).

    WARNING: Rides the SELF stream (0x611D47A5 refuses it on any other peer), and
    WARNING: is handled ONLY by the battle peer class -- see the note above. On the
    lobby session it is accepted, acked and ignored. It exists so that the
    record is a real, pinned thing rather than a string in a comment, and so
    that a battle-map session (if we ever serve one) has it ready."""
    if not unit_id:
        raise ValueError("UnitID 0 is what every map miss looks like")
    if status not in DEPOP_STATUSES:
        raise ValueError(
            "Status %d is printed and then ignored by 0x611EEA23 (legal: %s)"
            % (status, ", ".join(str(s) for s in DEPOP_STATUSES)))
    body = struct.pack("<III", unit_id & M32, from_id & M32, status)
    return record(CMD_DEPOP, body)


# --------------------------------------------------------------------------- #
#: --- THE BATTLE MANAGER (static 2026-09-10) ---------------------------------
#:
#: Scene 4's peer class has its OWN receive dispatcher, 0x611D4750 (`cmd - 5`,
#: bounded 0xD7, byte map 0x611D4984, jump table 0x611D4950), and it is not
#: the lobby's eight-command table above. Twelve commands have arms of their
#: own -- 5 7 8 9 11 12 13 19 118 119 124 220 -- and EVERY OTHER id in range
#: falls to 0x611D4916, which copies the record to 0x613C16C0 and hands it to
#: **0x611EF2E0, the battle manager's message dispatcher** (`cmd - 0x12`,
#: bounded 0x82, byte map 0x611F02C0, jump table 0x611F0210). So "BM -> CLI" is
#: cmds 18..148, and an id the client SENDS us on the battle channel is not
#: automatically one it will TAKE: the two directions share the numbers 128 and
#: 129 (the field sync and the objective marker) and little else.
#:
#: The table, cmd -> handler, read out of the jump table. Names are what the
#: handler was READ to do; the ones in CAPS have SE's own strings behind them.
BM_RECV = {
    18:  (0x611EDB30, "BATTLE CHAT DOWN -- same 0x80 body as lobby cmd 18: +0x00 "
                      "kind (1 = HUD banner via 0x610E6420, 6 = system line, else "
                      "'%s: %s'), +0x04 name (0x21), +0x26 text; text starting "
                      "'/' takes the command path"),
    21:  (0x611EE900, "unit event (4-arg family)"),
    23:  (0x611EE5F0, "unit event (4-arg family)"),
    24:  (0x611EE730, "unit event (4-arg family)"),
    27:  (0x611EDCC0, "unit event (4-arg family)"),
    29:  (0x611EE830, "unit event (4-arg family) -- the client SENDS 29 as its HIT "
                      "record (0x611F0F71, 0x24 B)"),
    30:  (0x611EDC60, "unit event (4-arg family)"),
    31:  (0x611EE060, "unit event"), 32: (0x611EE0C0, "unit event"),
    33:  (0x611EF270, "unit event"), 34: (0x611EEC70, "unit event"),
    36:  (0x611EE3D0, "unit event"), 38: (0x611EE440, "unit event"),
    42:  (0x611EE8A0, "unit event"), 43: (0x611EEEA0, "unit event"),
    44:  (0x611EE260, "unit event"), 45: (0x611EE310, "unit event"),
    46:  (0x611EDF30, "unit event"), 47: (0x611EDFA0, "unit event"),
    48:  (0x611EE370, "unit event"), 49: (0x611EDD20, "unit event"),
    50:  (0x611EDD70, "unit event"), 51: (0x611EDE70, "unit event"),
    52:  (0x611EDE90, "unit event"), 53: (0x611EDFE0, "unit event"),
    110: (0x611EF7EB, "no-op (returns 1)"),
    118: (0x611EDC40, "unit event"),
    128: (0x611EDAD0, "FIELD SYNC applied to the SENDER's unit ([unit+0x24] -> "
                      "0x61052E10 -> 0x610672E0): <u8 kind><u8 offset><u16 len>"
                      "<bytes>. The client emits the same record from 0x61053010 "
                      "for its own objects -- kind 3 is its per-part DAMAGE table "
                      "(0x611F0FD2 / 0x611F1021: +0x04.. four body parts, +0x14.. "
                      "six weapon slots, each value = max - current)"),
    129: (0x611EDAA0, "OBJECTIVE MARKER -> 0x6105C780(body, 1): a 17-slot table at "
                      "0x613B6B38, 0x44 B each, indexed by body+0x00. Slots 0..3 "
                      "put a countdown marker on the HUD (0x610E6F20) at the s16 "
                      "position +0x0C/+0x0E/+0x10 x 4/15, deadline +0x20 in "
                      "client-clock ms; 4..7 use +0x18; 8..16 are other kinds"),
    130: (0x611EE970, "unit event"),
    131: (0x611EE150, "unit event"),
    132: (0x61051C60, "per-unit value: n rows of {u32 unit, u32 v, u32} -> "
                      "[unit+0xCC4] = v"),
    133: (0x6110B070, "ZONE STATE: +0x00 rect-group id (block +0xA8, stride 0x58), "
                      "+0x04 rect index (block +0x418, stride 0x1C), +0x0C flag, "
                      "+0x0D -> rect+0x01 when the group's +0x01 == 5"),
    134: (None,       "SIDE NAMES: cp932 string at +0x08 -> block+0xC60, string at "
                      "+0x58 -> block+0xCB0 (80 B each)"),
    135: (0x611AC590, "'chtr?' -- a 16-byte per-unit check against +0x04"),
    136: (0x611AC6A0, "'chtr dump' -- the reply side of 135"),
    138: (0x61001F40, "BATTLE START: +0x00 -> block+0x48 (Start GameTime), +0x04 "
                      "reason -> globals+0x214 and the banner (1 = 79:30 'Enemy "
                      "forces have entered the battlefield.', 2 = 79:29 'Start "
                      "Battle was chosen.', 3 = 79:31 'The standby time has "
                      "ended.' -- each '...The battle begins now.'), +0x08 -> "
                      "block+0x6456 with bit 0x400 at +0x5CFA"),
    140: (0x61002160, "32:17 '%s seized the classified container.' / 32:19 '%s "
                      "seized the %s army's guidance beacon.'"),
    141: (0x610020F0, "32:18 '%s dropped the classified container.' / 32:20 '%s "
                      "dropped the %s army's guidance beacon.'"),
    145: (0x61001FA0, "12 bytes -> block+0x6416 (a 3-dword item)"),
    146: (0x61001FF0, "clears block+0x6416 / +0x641A"),
    148: (None,       "flag bits 0x200/0x400 into block+0x7C from +0x00, +0x04 -> "
                      "block+0x48, then per-rect bit toggles"),
}
#: The client -> BM side, from the 58 callers of the send wrapper 0x611D45D0
#: (`(unitid, ?, cmd, ?, buf, len)`) and the 45 of 0x611E2750. Named where read.
CLI_HIT = 0x1D              # 29, 0x24 B, 0x611F0F71 -- the SHOOTER's OWN weapon
#: self-report (target +0x20 = itself); NOT a hit on an enemy (RE 2026-09-11).
CLI_HIT_TARGET = 0x64       # 100, 0x14 B, 0x611F0CD0 -> 0x611F0EBD: the client
#: names ANOTHER unit as a hit target. Emitted only when a target is LOCKED
#: (global 0x613BBB38) and its side (+0x80) DIFFERS from the shooter's and it is
#: in the battle-map table (0x611F05F0/0x611F5280). The record header carries the
#: target UnitID. The server does not yet run HP off it; logging it proves the
#: player can lock the enemy at all.
CLI_FIELD_SYNC = 0x80       # 128, <kind><off><len><bytes>, 0x61053010
CLI_OBJECTIVE_ACK = 0x66    # 102, 12 B, sent by the slot-0..3 marker arm 0x6105C985
CLI_ESCAPE_B = 0x89         # 137, 0x611D36B0 -- the other mode of 139
CLI_ESCAPE = 0x8B           # 139, 0x611D3730 "CLI -> BM EMERGENCY ESCAPE", 64 B
#: The two reason codes 0x611D3730 is called with (six sites, both seen live).
ESCAPE_REASONS = {0x49895963: "eject (five sites: 0x610548DA 0x61054908 0x6105DB48 "
                              "0x61060807 0x6106083F)",
                  0x24360679: "eject, the 0x6104C0F4 form"}

#: THE HIT RECORD -- cmd 29 (0x1D), 0x24 B, built by 0x611F0F00(buf, target):
#:   +0x04 u8   1 (both receive arms 0x611EE86B / 0x611EE8xx require it)
#:   +0x05 u8   -> hit+0x1A in the applied copy (0x61060F48)
#:   +0x08 s16  -> hit+0x10 (0x61060F1C..F2A; a damage/roll value)
#:   +0x0A u16  PART hit (0x61060F14; < 4 = a body part, else a weapon slot;
#:              the sender's own damage-table sync at 0x611F0FB3 branches on it)
#:   +0x0C u32  FLAGS -> hit+0x14; bit 0x400 = "died from this Hit packet"
#:              (the send wrapper's own debug print at 0x611D4649)
#:   +0x10 u32  -> hit+0x0C
#:   +0x1C u32  read at 0x61060F7C..
#:   +0x20 u32  TARGET UnitID (assembled byte-wise at 0x611F0F1A / 0x611EE840)
#: KEY: WHO APPLIES IT (0x611F0F3D..0x611F0F5B and the receive arm 0x611EE878),
#: with [unit+0x1340] pinned by its writers: the ctor sets 3 (0x6105CDAE /
#: 0x6105CE55 / 0x6105EA9F) and ONLY the local player's own-unit init sets 2
#: (0x6105ED1B, the routine that also fills the self-HUD globals 0x613B6Axx).
#: So 2 = MY OWN UNIT, 3 = everyone else's. The sender applies its record
#: LOCALLY to any target that is not its own unit (the dummy, an NPC, another
#: player -- client-side prediction), and a receiver applies an incoming cmd 29
#: ONLY to its own unit: damage TO a player arrives as somebody's record,
#: relayed by the BM. WARNING: This corrects a first reading that had the two arms
#: swapped ("the dummy cannot take damage until relayed") -- it can, and does,
#: with no server help; what needs the relay is the ROOM seeing it and a
#: player being HIT by an NPC or another player. cmd 42
#: (0x2A) is the batched form: 0x10-byte header + n x 0x24 (the wrapper counts
#: them from +0x0C/+0x0D), target at each record's +0x20 (0x611EE8B0 reads
#: +0x30 = header 0x10 + 0x20). cmd 128 kind 3 is the attacker's own damage
#: table published after a LOCAL hit; relayed on the sender's ALIAS stream it
#: updates that unit's copy on the other clients.
CMD_BM_HIT = 29
CMD_BM_HIT_BATCH = 42
HIT_LEN = 0x24
HIT_KIND = 0x04
HIT_VALUE = 0x08
HIT_PART = 0x0A
HIT_FLAGS = 0x0C
HIT_TARGET = 0x20
HIT_FLAG_DIED = 0x400
HIT_BATCH_HDR = 0x10


def parse_hit(body):
    """{kind, value, part, flags, died, target} from one 0x24-byte hit record;
    None when short."""
    if len(body) < HIT_LEN:
        return None
    flags = struct.unpack_from("<I", body, HIT_FLAGS)[0]
    return {"kind": body[HIT_KIND],
            "value": struct.unpack_from("<h", body, HIT_VALUE)[0],
            "part": struct.unpack_from("<H", body, HIT_PART)[0],
            "flags": flags, "died": bool(flags & HIT_FLAG_DIED),
            "target": struct.unpack_from("<I", body, HIT_TARGET)[0]}


def parse_hit_batch(body):
    """The records inside a cmd-42 body, as parse_hit dicts."""
    out = []
    at = HIT_BATCH_HDR
    while at + HIT_LEN <= len(body):
        h = parse_hit(body[at:at + HIT_LEN])
        if h:
            out.append(h)
        at += HIT_LEN
    return out




def record_hit(target, part=0, value=0, flags=0, died=False, extra=b""):
    """cmd 29 -- ONE 0x24-byte hit record authored by the server: the path an
    NPC (or the referee) damages a PLAYER by, since a client applies a received
    record only to its own unit. Fields per the layout above; `extra` overlays
    bytes we have not decoded (+0x10.., +0x1C..) for a probe."""
    b = bytearray(HIT_LEN)
    b[HIT_KIND] = 1
    struct.pack_into("<h", b, HIT_VALUE, int(value))
    struct.pack_into("<H", b, HIT_PART, int(part) & 0xFFFF)
    struct.pack_into("<I", b, HIT_FLAGS,
                     (int(flags) | (HIT_FLAG_DIED if died else 0)) & 0xFFFFFFFF)
    struct.pack_into("<I", b, HIT_TARGET, int(target) & 0xFFFFFFFF)
    if extra:
        b[0x10:0x10 + len(extra)] = extra[:HIT_LEN - 0x10]
    return record(CMD_BM_HIT, bytes(b))


CMD_BM_CHAT = 18
CMD_BM_OBJECTIVE = 129
CMD_BM_ZONE_STATE = 133
CMD_BM_SIDE_NAMES = 134
CMD_BM_BATTLE_START = 138
BATTLE_START_REASONS = {1: "79:30 Enemy forces have entered the battlefield.",
                        2: "79:29 Start Battle was chosen.",
                        3: "79:31 The standby time has ended."}
BATTLE_START_LEN = 0x0C
SIDE_NAMES_LEN = 0xA8
SIDE_NAME_A = 0x08
SIDE_NAME_B = 0x58
SIDE_NAME_LEN = 0x50
OBJECTIVE_LEN = 0x44
OBJECTIVE_SLOTS = 17
OBJECTIVE_SCALE = 4.0 / 15.0        # [0x6132D980] = 0.26666668f, s16 -> world
OBJ_INDEX = 0x00
OBJ_STATE = 0x03
OBJ_UNIT = 0x04
OBJ_UNIT2 = 0x08
OBJ_X = 0x0C
OBJ_Z = 0x0E
OBJ_Y = 0x10
OBJ_FLAG = 0x12
OBJ_DEADLINE_4_7 = 0x18
OBJ_DEADLINE_0_3 = 0x20


def parse_escape(body):
    """(reason code, SE's name for it) from a cmd-139/137 body; None if short."""
    if len(body) < 4:
        return None
    code = struct.unpack_from("<I", body, 0)[0]
    return code, ESCAPE_REASONS.get(code, "an unlisted reason code")


def record_battle_start(reason, start_gametime=0, arg=0):
    """cmd 138 -- BATTLE START. `reason` picks SE's banner (1..3); anything else
    is dropped by the arm at 0x611EFB6D, so it is refused here."""
    if reason not in BATTLE_START_REASONS:
        raise ValueError(f"battle start reason {reason}: the arm 0x611EFB63 "
                         f"accepts 1, 2 or 3 and drops the rest")
    return record(CMD_BM_BATTLE_START,
                  struct.pack("<III", int(start_gametime) & 0xFFFFFFFF, reason,
                              int(arg) & 0xFFFFFFFF))


def record_side_names(name_a, name_b):
    """cmd 134 -- the two 80-byte cp932 strings the arm copies to block+0xC60
    and +0xCB0. What draws them is unread; a probe, not a service."""
    b = bytearray(SIDE_NAMES_LEN)
    for off, text in ((SIDE_NAME_A, name_a), (SIDE_NAME_B, name_b)):
        raw = str(text or "").encode("cp932", "replace")[:SIDE_NAME_LEN - 1]
        b[off:off + len(raw)] = raw
    return record(CMD_BM_SIDE_NAMES, bytes(b))


def record_objective_marker(index, unit_id, pos, deadline_ms, unit2=0, flag=0):
    """cmd 129 -- ONE 0x44-byte objective slot. `pos` is (x, y, z) in world
    units; the record carries s16 x/z/y at 4/15 world units per unit. The
    deadline is in CLIENT-CLOCK milliseconds (0x613CACA0 -- the clock the
    keepalive reports), not ours. Slots 0..3 read +0x20, 4..7 read +0x18; this
    fills both. WARNING: Only slots 0..3's HUD path is read end to end; the rest of
    the table is a layout guess and this builder refuses them."""
    if not 0 <= index <= 3:
        raise ValueError(f"objective slot {index}: only 0..3 (the HUD marker arm "
                         f"0x6105C7C6) is read; 4..16 have their own arms")
    x, y, z = pos
    b = bytearray(OBJECTIVE_LEN)
    b[OBJ_INDEX] = index
    struct.pack_into("<I", b, OBJ_UNIT, int(unit_id) & 0xFFFFFFFF)
    struct.pack_into("<I", b, OBJ_UNIT2, int(unit2) & 0xFFFFFFFF)
    for off, v in ((OBJ_X, x), (OBJ_Z, z), (OBJ_Y, y)):
        s = int(round(float(v) / OBJECTIVE_SCALE))
        if not -32768 <= s <= 32767:
            raise ValueError(f"objective position {v} is past the s16 field at "
                             f"4/15 units per count")
        struct.pack_into("<h", b, off, s)
    b[OBJ_FLAG] = flag & 0xFF
    struct.pack_into("<I", b, OBJ_DEADLINE_4_7, int(deadline_ms) & 0xFFFFFFFF)
    struct.pack_into("<I", b, OBJ_DEADLINE_0_3, int(deadline_ms) & 0xFFFFFFFF)
    return record(CMD_BM_OBJECTIVE, bytes(b))


def selftest(capture=None):
    """No client and no socket. The capture check is the real one; without a
    capture the arithmetic checks still pin the framing rules."""
    ok = True

    P0, S0 = load_tables()
    stock = P0[0] == 0x243F6A88 and S0[0][0] == 0xD1310BA6
    print("  tables are the stock pi constants: %s" % ("OK" if stock else "FAIL"))
    ok &= stock

    # The same tables, reached from a completely different game's image. FE
    # stores them XOR 0x91 and +1 per byte; deriving the stock values back out
    # and comparing is a cross-check no typo in either extraction survives.
    try:
        import feblowfish
        feP, feS = feblowfish.minus_one_per_byte(*feblowfish.load_tables())
        agree = (feP, feS) == (P0, S0)
        print("  == Fantasy Earth's tables, de-obfuscated: %s"
              % ("OK" if agree else "FAIL"))
        ok &= agree
    except Exception as exc:                     # feblowfish is optional here
        print("  (skipped the FE cross-check: %s)" % exc)

    # The key derivation, against the one value proven on real bytes.
    ep = bytearray(20)
    struct.pack_into(">H", ep, 2, 61300)         # network order, as served
    ep[4:8] = bytes((192, 168, 3, 71))   # the capture's LAN address, 192.168.3.71 (polcheck: allow)
    got = key_for_endpoint(bytes(ep), 1)
    print("  key for the captured endpoint 192.168.3.71:61300 char 1 == 47041db0lobby: %s"  # (polcheck: allow)
          % ("OK" if got == b"47041db0lobby" else "FAIL (%s)" % got))
    ok &= got == b"47041db0lobby"

    # build() must satisfy every rule parse() enforces, which is every rule the
    # client enforces. A pure ACK and a datagram with records, both ways round.
    P, S = bf_init(b"47041db0lobby")
    for label, body, frm in (("pure ack", b"", 0),
                             ("two records", record(1, b"\1\0\0\0" * 4)
                              + record(15, b"\2\0\0\0"), 7)):
        dg = build(P, S, peer=1, hid=HID_WORLD, kind=2, ack=9, frm=frm, body=body)
        got = parse(P, S, dg)
        good = (got is not None
                and len(dg) % 8 == 0 and len(dg) >= MIN_LEN
                and got["ack"] == 9 and got["from"] == frm
                and got["to"] - got["from"] == len(got["records"])
                and got["hid"] == HID_WORLD and got["mask"] == MASK_DEFAULT)
        print("  build/parse round-trip, %-11s: %s" % (label, "OK" if good else "FAIL"))
        ok &= good

    # WARNING: The check that matters: real client bytes. Every datagram must decrypt,
    # VERIFIED: cmd 240 AGAINST THE CLIENT'S OWN BYTES. These four are real records
    # logged out of pol-server-fmo-1 on 2026-08-24, and the first one is the
    # decisive one: the player had just spawned at the served
    # FMO_UDP_POP_POS=0,5,0,0 and had not moved, so a decode that does not read
    # (0.00, 5.00, 0.00) out of it is wrong no matter how plausible it looks.
    # WARNING: The re-encode is half the test. Parsing bytes into numbers that seem
    # sensible is not evidence; reproducing the client's own bytes from those
    # numbers is.
    for hexs, want_flags, want_pos, want_rot in (
            ("06000000" "0000f401" "0000f700", 0x06, (0.0, 5.0, 0.0), 0.247),
            ("06000000" "25000000" "9200f700", 0x06, (0.37, 0.0, 1.46), 0.247),
            ("06000000" "c1000000" "90fff509", 0x06, (1.93, 0.0, -1.12), 2.549),
            ("26000000" "89fc0000" "55fc94fd", 0x26, (-8.87, 0.0, -9.39), -0.620)):
        raw = bytes.fromhex(hexs)
        m = parse_move(raw)
        near = (m is not None and m["flags"] == want_flags
                and all(abs(a - b) < 5e-3 for a, b in zip(m["pos"], want_pos))
                and abs(m["rot"] - want_rot) < 5e-4)
        back = record_move(m["pos"], m["rot"], flags=m["flags"])[REC_HDR:]
        exact = back[:MOVE_MIN] == raw
        print("  cmd 240 %s -> %-28s and re-encodes exactly: %s"
              % (hexs[:8] + "..",
                 "(%.2f, %.2f, %.2f) rot %+.3f" % (m["pos"] + (m["rot"],))
                 if m else "None",
                 "OK" if near and exact else
                 "FAIL (near=%s exact=%s)" % (near, exact)))
        ok &= near and exact

    # The client's own gate, not ours: 0x611EAC0C drops anything under 12 bytes
    # before it reads a coordinate, so parse_move must say None rather than
    # invent a position out of a short record.
    short = parse_move(bytes(MOVE_MIN - 1)) is None
    full = parse_move(bytes(MOVE_MIN)) is not None
    print("  cmd 240 honours the client's 12-byte minimum (0x611EAC0C): %s"
          % ("OK" if short and full else "FAIL"))
    ok &= short and full

    # A record we build has to be one the client would accept at all -- the
    # +0x0C low-word rule that silently ate the first chat record.
    r = record_move((1.0, 2.0, 3.0), 0.5)
    sz, cmd, _a8, flt = struct.unpack_from("<IIII", r, 0)
    good = (sz == len(r) and sz % 4 == 0 and sz < MAX_RECORD
            and cmd == CMD_MOVE and cmd < MAX_COMMAND and not (flt & 0xFFFF))
    print("  cmd 240 record passes the client's record validator: %s"
          % ("OK" if good else "FAIL"))
    ok &= good

    # POP body+0x00 is the client-object selector (0x611EAE86). 1 is the arm
    # that builds NO peer, so a room POP must never send it by accident, and 4+
    # is not an arm at all.
    sel = struct.unpack_from("<I", record_pop(9, client_kind=3)[REC_HDR:],
                             POP_CLIENT_KIND)[0] == 3
    refused = 0
    for bad in (4, 5, -1):
        try:
            record_pop(9, client_kind=bad)
        except ValueError:
            refused += 1
    print("  cmd 7 client_kind lands at body+0x00 and refuses non-arms: %s"
          % ("OK" if sel and refused == 3 else "FAIL"))
    ok &= sel and refused == 3

    # cmd 8, RecvDepop: the body is the three dwords 0x611EE9FC..0x611EEA08
    # read, in the order the format string names them, and the record must
    # pass the same validator as everything else. The round trip pins the
    # layout; the refusals pin the two values the handler would swallow.
    d = record_depop(0x200, from_id=1, status=DEPOP_LEFT)
    sz, cmd, _a8, flt = struct.unpack_from("<IIII", d, 0)
    back = parse_depop(d[REC_HDR:])
    good = (sz == len(d) and sz % 4 == 0 and cmd == CMD_DEPOP
            and not (flt & 0xFFFF) and back is not None
            and back["unit_id"] == 0x200 and back["from_id"] == 1
            and back["status"] == DEPOP_LEFT
            and d[REC_HDR:] == bytes.fromhex("00020000" "01000000" "00000000")
            and parse_depop(d[REC_HDR:REC_HDR + 11]) is None)
    refused = 0
    for kw in ({"unit_id": 0}, {"unit_id": 5, "status": 4},
               {"unit_id": 5, "status": -1}):
        try:
            record_depop(**kw)
        except ValueError:
            refused += 1
    print("  cmd 8 RecvDepop {UnitID, FromID, Status} round-trips, validates, "
          "refuses unit 0 / bad status: %s"
          % ("OK" if good and refused == 3 else
             "FAIL (good=%s refused=%d)" % (good, refused)))
    ok &= good and refused == 3

    # pass the client's own MD5, and RE-ENCODE to the captured bytes -- and the
    # record count must equal TO - FROM, which is what fixes the meaning of
    # those two fields.
    if capture:
        recs = _read_capture(capture)
        parsed = ranges = resealed = 0
        for dg in recs:
            got = parse(P, S, dg)
            if not got:
                continue
            parsed += 1
            if got["to"] - got["from"] == len(got["records"]):
                ranges += 1
            if seal(P, S, got["plain"]) == dg:
                resealed += 1
        n = len(recs)
        # WARNING: The record header rule, on real client bytes: every record must
        # have a zero low word at +0x0C or the client rejects it -- which is
        # exactly how our first chat record died.
        recs_ok = recs_tot = 0
        for dg in recs:
            got = parse(P, S, dg)
            if not got:
                continue
            for off, size, cmd, _ in got["records"]:
                recs_tot += 1
                flt = struct.unpack_from("<I", got["plain"], off + 0x0C)[0]
                if size >= REC_HDR and not (flt & 0xFFFF):
                    recs_ok += 1
        print("  capture, %-32s %d/%d: %s"
              % ("record +0x0C low word is zero", recs_ok, recs_tot,
                 "OK" if recs_ok == recs_tot and recs_tot else "FAIL"))
        ok &= recs_ok == recs_tot and recs_tot > 0

        for label, hit in (("decrypt + the client's own MD5", parsed),
                           ("record count == TO - FROM", ranges),
                           ("re-seal is byte-identical", resealed)):
            print("  capture, %-32s %d/%d: %s"
                  % (label, hit, n, "OK" if hit == n else "FAIL"))
            ok &= hit == n

    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _read_capture(path):
    """The capture tool writes <u32 len><f64 time><bytes>; captures taken
    before that tool learned to timestamp have no time field. WARNING: DETECT, do not
    assume -- the wrong header size still "parses", into garbage, and the first
    capture in the repo happens to be the old form."""
    with open(path, "rb") as fh:
        blob = fh.read()
    for hdr in (12, 4):
        out, off, sane = [], 0, True
        while off + hdr <= len(blob):
            ln = struct.unpack_from("<I", blob, off)[0]
            if not (8 <= ln <= 1400) or off + hdr + ln > len(blob):
                sane = False
                break
            out.append(blob[off + hdr:off + hdr + ln])
            off += hdr + ln
        if sane and out and off == len(blob):
            return out
    raise SystemExit("%s is not an fmoudp capture" % path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--capture", help="an fmoudp.py capture to verify against")
    a = ap.parse_args()
    sys.exit(selftest(a.capture) if a.selftest else ap.print_help())
