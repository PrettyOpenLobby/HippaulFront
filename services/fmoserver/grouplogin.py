"""The group-server login entries (0x0154 -> 0x0155) and the 0x0174 push."""
import os
import struct


#: After Start Game succeeds the client asks 0x0154 and waits for 0x0155.
#: Polarity is the NORMAL way round here -- 0x6117B49E `cmp word [ecx+6], 0x155`
#: / `je` success, anything else takes the error arm (string 0xC0080016) -- unlike
#: 0x130, where 2 meant NO. Do not assume either way; read the je/jne.
#:
#: The success arm at 0x6117B546:
#:     esi = payload + 0x0C
#:     edi = ebp + 0x3672
#:     ecx = 0x1E0 / rep movsd      ; 1920 bytes copied out
#:     cl  = byte [payload+0x00]    ; a COUNT
#:     if (cl <= 0) skip the entry loop
#: so the payload is at least 0x0C + 1920 = 1932 bytes.
#:
#: VERIFIED: THE ENTRY IS DECODED (2026-08-18, static RE of the unpacked
#: image). The entry loop at 0x6117B56D walks from payload+0x0C with
#: `add esi, 0x78` -- STRIDE 120 -- and 1920/120 = 16 entries exactly. Each entry
#: is handed to 0x61177620, and that function prints its own arguments:
#:
#:     0x6133B7C0  "LoginGroup GroupID=%d Type=%d Addr=%s:%d\n"
#:
#: which names three of the four. Matching the pushes to the format:
#:
#:     +0x00  u32   GroupID
#:     +0x04  u8    Type -- a switch (0x611778B4) selecting WHICH connection
#:                  object this group belongs to: 0/1/default -> [0x613CA3F8],
#:                  2 -> [0x613CA3FC], 3 -> [0x613CA400]
#:     +0x05  u8    a gate. The loop tests it FIRST (`cmp byte [esi-0x1b], 0`);
#:                  zero means "skip", and zero with +0x04 also zero takes the
#:                  error arm (string 0xC0080016).
#:     +0x0C  20B   an ENDPOINT -- 0x6117767A reads `word [edi+2]` as the PORT and
#:                  0x61177694 `dword [edi+4]` as the ADDRESS, tested non-zero
#:                  before the group is used. Same 20-byte struct as the 0x0322's,
#:                  so the address is BINARY AND BYTE-REVERSED here too.
#:     +0x20  88B   copied to globals+0x128 by `rep movsd` of 0x16 dwords.
#:     +0x78 = 120 -- 0x0C + 20 = 0x20 and 0x20 + 88 = 0x78, so the entry is
#:                  ACCOUNTED FOR EXACTLY, with no unexplained padding.
#:
#: The count also lands at ebp+0x366E, and the block itself at ebp+0x3672.
#:
#: WARNING: THAT 88-BYTE FIELD IS THE SAME STRUCTURE 0x0153 CARRIES. 0x0153's tail goes
#: to globals+0xD0 and this one to globals+0x128, which are 0x58 = 88 apart --
#: two adjacent slots of one type. Neither slot has a decoded reader yet: both are
#: read as `this+offset` inside the singleton's own methods, where a scan keyed on
#: the .data pointer is blind. WHAT THE 88 BYTES MEAN IS STILL UNKNOWN.
#:
#: WARNING: COUNT 0 IS STILL THE DEFAULT, ON PURPOSE. A non-empty list is not a free
#: experiment: 0x61177620 TEARS DOWN the connection object its Type selects and
#: builds a new one against the endpoint in the entry. That is a live network
#: action on the path that currently works. FMO_GROUP_COUNT opts in.
MSG_0154_REQ = 0x0154
MSG_0154_REPLY = 0x0155
REPLY_0155_LEN = 0x0C + 0x1E0 * 4      # 1932
GROUP_ENTRY_LEN = 0x78                 # 120, from the loop's `add esi, 0x78`
GROUP_SLOTS = 16                       # 1920 / 120, exactly
GROUP_OFF = 0x0C                       # where the entries start in the payload
GRP_ID, GRP_TYPE, GRP_GATE = 0x00, 0x04, 0x05
GRP_ENDPOINT, GRP_INFO88 = 0x0C, 0x20
INFO88_LEN = 0x16 * 4                  # 88, the `rep movsd` count


def group_entry(group_id, host, port, gtype=0):
    """One 120-byte LoginGroup entry, laid out as 0x6117B570 walks it."""
    b = bytearray(GROUP_ENTRY_LEN)
    struct.pack_into("<I", b, GRP_ID, group_id)
    b[GRP_TYPE] = gtype & 0xFF
    b[GRP_GATE] = 1                      # non-zero, or the loop skips the entry
    # WARNING: NETWORK ORDER, like the 0x0153's (EP_0153_NET). LIVE 2026-09-05 22:52Z
    # with the little-endian `endpoint()` here: the client's own log said
    # "LoginGroup GroupID=2 Type=1 Addr=0.0.0.0:61300" and its FmoGroup dialed
    # the byte-reversed host address, port 29935 -- nothing ever reached us and the
    # group timed out. The group channel is CFmoConnectCliSys, the same UDP
    # datagram client as the battle map, whose sender copies [ep+2]/[ep+4]
    # verbatim into the sockaddr (endpoint_net's docstring). FMO-13111 again.
    b[GRP_ENDPOINT:GRP_ENDPOINT + addressing.ENDPOINT_LEN] = \
        (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)(host, port)
    return bytes(b)


# --------------------------------------------------------------------------- #
# 0x0174 -- THE PUSHED GROUP-SERVER LOGIN (static 2026-09-09)
# --------------------------------------------------------------------------- #
#: Arm 0x6117EED3 of the queue dispatcher. It calls **0x61177620(GroupID, type,
#: endpoint, block88)** -- the very builder the 0x0158 CREATE entry reaches
#: through 0x611778D0 -- so this is "log in to this group server", pushed
#: rather than answered. It is how a JOINER gets attached: 0x0157's own reply
#: carries nothing.
#:
#: WARNING: THE OFFSETS ARE NOT THE 0x0158 ENTRY'S. The create path hands a 120-byte
#: LoginGroup list entry (GRP_ID/GRP_TYPE/GRP_ENDPOINT above, endpoint at
#: +0x0C); this arm reads a FLAT struct straight off the payload:
#:     +0x00 u32  GroupID   -- `mov eax,[ebp+0x14]`, and ZERO makes the arm bail
#:     +0x04      endpoint  -- `lea ecx,[ebp+0x18]`  (port at +2, addr at +4)
#:     +0x18 u8   type      -- `mov dl,[ebp+0x2c]`
#:     +0x24      88 bytes  -- `lea edx,[ebp+0x38]`
#: Reusing group_entry() here would put the endpoint 8 bytes early and the
#: client would dial nothing. (a fixed-width field is only what you proved)
MSG_GROUP_ATTACH = 0x0174
G174_ID, G174_ENDPOINT, G174_TYPE, G174_INFO88 = 0x00, 0x04, 0x18, 0x24
G174_BODY_LEN = G174_INFO88 + 88        # 0x7C = 124
#: FMO_GROUP_ATTACH: push a 0x0174 after a JOIN so the joiner actually attaches
#: to the group server. 1 = yes (default -- without it a join closes its dialog
#: and nothing whatever happens); 0 = answer the join and attach nothing.
GROUP_ATTACH = (os.environ.get("FMO_GROUP_ATTACH", "1").strip() or "1") != "0"


def group_attach_body(group_id, host, port, gtype=0):
    """A 0x0174 group-server login push. See MSG_GROUP_ATTACH for the offsets.

    Same network-byte-order rule as the 0x0153 endpoint: the consumer is FMO's
    own datagram sender (0x611FED70), which copies the struct into a sockaddr
    verbatim -- no htons, no htonl -- so a little-endian endpoint arrives
    byte-reversed and the client dials a nonsense address. That was measured
    live on 2026-09-05 for the group channel specifically.
    """
    if not group_id:
        raise ValueError(
            f"0x{MSG_GROUP_ATTACH:04X}: GroupID 0 -- 0x6117EEE5 is "
            f"`test eax,eax / je <bail>`, so a zero id is dropped in silence")
    b = bytearray(G174_BODY_LEN)
    struct.pack_into("<I", b, G174_ID, group_id)
    b[G174_TYPE] = gtype & 0xFF
    b[G174_ENDPOINT:G174_ENDPOINT + addressing.ENDPOINT_LEN] = \
        (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)(host, port)
    return bytes(b)


def group_attach_packet(conn_id, group_id, host=None, port=None, gtype=None):
    """The 0x0174 push on the queue sequence. Nothing polls for it."""
    return packet.build(MSG_GROUP_ATTACH,
                        group_attach_body(group_id,
                                          addressing.GROUP_HOST if host is None else host,
                                          addressing.GROUP_PORT if port is None else port,
                                          addressing.GROUP_TYPE if gtype is None else gtype),
                        pushes.QUEUE_SEQ, conn_id)


def reply_0155(count):
    """u8 count at +0x00, then 16 fixed-size entries from +0x0C."""
    b = bytearray(REPLY_0155_LEN)
    b[0] = min(count, GROUP_SLOTS) & 0xFF
    for i in range(min(count, GROUP_SLOTS)):
        off = GROUP_OFF + i * GROUP_ENTRY_LEN
        b[off:off + GROUP_ENTRY_LEN] = group_entry(i + 1, addressing.GROUP_HOST,
                                                   addressing.GROUP_PORT, addressing.GROUP_TYPE)
    return bytes(b)


# Called at run time only; imported last so that import cycles resolve.
from . import addressing, packet, pushes  # noqa: E402
