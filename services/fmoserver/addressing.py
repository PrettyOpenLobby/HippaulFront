"""Which address and port to write into a reply for a given client, and the 20-byte endpoint
struct."""
import os
import socket
import struct
import ipaddress as _ipaddress
from .knobs import _env_int


#: The endpoint at payload+0x00. Pointed at our own door by default, exactly as
#: the 0x0322's endpoints are: nothing serves a separate battle port, so aiming
#: it here means a client that DOES dial it shows up as a connection in this
#: responder's log instead of vanishing into a refused connect.
BATTLE_HOST = os.environ.get("FMO_BATTLE_HOST", os.environ.get(
    "FMO_NEXT_HOST", "127.0.0.1"))
BATTLE_PORT = _env_int("FMO_BATTLE_PORT", "61300", 10)

#: How many 0x0155 LoginGroup entries to serve. 0 = the empty list that the
#: working sequence was measured with. WARNING: See the 0x0155 note: a non-zero count
#: makes the client tear down and rebuild a connection object, so this is a
#: deliberate experiment, not a default.
GROUP_COUNT = _env_int("FMO_GROUP_COUNT", "0")
GROUP_TYPE = _env_int("FMO_GROUP_TYPE", "0")
GROUP_HOST = os.environ.get("FMO_GROUP_HOST", BATTLE_HOST)
GROUP_PORT = _env_int("FMO_GROUP_PORT", "61300", 10)

_RFC1918_NETS = tuple(_ipaddress.ip_network(n) for n in
                      ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))  # generic RFC1918 example; polcheck: allow


def _ip_or_none(text):
    try:
        a = _ipaddress.ip_address(text)
    except (ValueError, TypeError):
        return None
    return getattr(a, "ipv4_mapped", None) or a


def _is_rfc1918(a):
    return a is not None and any(a in n for n in _RFC1918_NETS)


def _lan_client_nets():
    nets = []
    for c in (os.environ.get("POL_ADVERTISE_LAN_CLIENTS") or "").split(","):
        c = c.strip()
        if not c:
            continue
        try:
            nets.append(_ipaddress.ip_network(c, strict=False))
        except ValueError:
            pass
    return nets or list(_RFC1918_NETS)


def host_for(default, peer_ip):
    """The host to write into a reply for the client at `peer_ip`. Never raises."""
    peer = _ip_or_none(peer_ip)
    if peer is None or peer.is_loopback:
        return default
    # Rule 0 (2026-09-21, the edge VPS -- deploy/edge, and the same rule in
    # srvcore.advertise_for): the VPS forwards internet players with their own
    # source address, so a GLOBAL peer came through it and can only dial it.
    # Tailnet (127.0.0.1/10 is not global) and LAN peers fall through. The UDP
    # side stays in step because WorldChannel.candidates calls this too, with
    # the datagram's source, which the edge also leaves intact.
    public_ip = (os.environ.get("POL_ADVERTISE_PUBLIC") or "").strip()
    if public_ip and peer.is_global:
        return public_ip
    lan_ip = (os.environ.get("POL_ADVERTISE_LAN") or "").strip()
    if lan_ip and any(peer in n for n in _lan_client_nets()):
        return lan_ip
    if _is_rfc1918(peer) and not _is_rfc1918(_ip_or_none(default)):
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect((str(peer), 9))
                src = probe.getsockname()[0]
            finally:
                probe.close()
        except OSError:
            return default
        if _is_rfc1918(_ip_or_none(src)):
            return src
    return default

# --------------------------------------------------------------------------- #
# THE 0x0322 REPLY IS A REDIRECT -- proved live, not inferred.
#
# With the payload all zero the client tore the connection down and immediately
# dialled 0.0.0.0:0 (pol-shim's connect hook caught it). It was taking an address
# out of our reply. Two 20-byte ENDPOINT structs ride in that payload, and
# 0x61199830 -> 0x611999bc show their shape:
#
#     esi = word [endpoint+2]        ; the PORT
#     copy 20 bytes -> ctx+0x7588
#     lea eax,[esi+0x7588] / call [polcore+0x39C]     ; polcore connect
#
#     +0x00  u16  unused
#     +0x02  u16  PORT, host order (the config block stores 61300 as 0xEF74)
#     +0x04  u32  IPv4 ADDRESS, BINARY -- see below. NOT a string.
#
# WARNING: THE ADDRESS IS BINARY, AND IT GOES ON THE WIRE BYTE-REVERSED. The 16-byte
# field looked like room for an IPv4 literal, so the first cut wrote the ASCII
# "127.0.0.1" there. The client dialled 46.50.57.49:61300 -- which is
# 2E 32 39 31, our own "192." read backwards. It reads a little-endian u32 and
# htonl's it, so the bytes come back out reversed:
#
#     printed a.b.c.d == B3.B2.B1.B0 of what we wrote
#
# So write struct.pack("<I", int(IPv4Address(host))) and it lands correctly.
# The port was right all along, which is why only the address was wrong and the
# redirect otherwise fired perfectly.
#
# This is the SECOND title to do this: fe-llb-protocol records Fantasy Earth's
# lobby IP going on the wire byte-reversed too. Assume it, do not rediscover it.
#
# In PAYLOAD offsets (packet offset - 0x14):
#     +0x14  endpoint 1  (packet +0x28)
#     +0x28  endpoint 2  (packet +0x3C)
#
# WARNING: DO NOT CALL THIS A "REDIRECTOR" -- that reading was mine and it is NOT
# established. 0x61199830 refuses the endpoint outright unless the connection is
# IDLE:
#
#     mov eax,[ecx+0x20] / cmp eax,0 / je proceed / or eax,-1 / ret 4
#
# So the endpoint is only ever applied after a teardown. Every dial we observed
# (0.0.0.0:0 with a zero payload, 46.50.57.49 with an ASCII one) happened AFTER
# the client had already failed and torn down -- they were RECONNECT-after-error
# using stored endpoint data, not a redirect working. A healthy session does not
# tear down, so it does not dial, and the absence of a second connection is
# consistent with success rather than evidence against it.
#
# Read the endpoint as "where to reconnect if you lose me", and treat its role in
# the success path as UNKNOWN.
# WARNING: What the SECOND endpoint is for is unknown -- plausibly the UDP world
# channel, since FMO opens a SOCK_DGRAM socket before it ever dials here, but
# that is a guess and is not treated as known below.
ENDPOINT_LEN = 0x14
EP1_OFF = 0x14                 # payload offset of endpoint 1
EP2_OFF = 0x28                 # payload offset of endpoint 2

NEXT_HOST = os.environ.get("FMO_NEXT_HOST", "127.0.0.1")
NEXT_PORT = _env_int("FMO_NEXT_PORT", "61300", 10)


def ipv4_le(host):
    """The address as the client wants it: binary, little-endian, so that the
    client's own htonl puts the octets back in the right order on the wire."""
    parts = [int(x) for x in host.split(".")]
    if len(parts) != 4 or any(not 0 <= p <= 255 for p in parts):
        raise ValueError(f"not a dotted IPv4 literal: {host!r}")
    return struct.pack("<I", (parts[0] << 24) | (parts[1] << 16)
                       | (parts[2] << 8) | parts[3])


def endpoint(host, port):
    """The 20-byte struct 0x61199830 expects. Address is BINARY, not a string.

    WARNING: THIS IS THE polcore/TCP FLAVOUR, and it is not the only one -- see
    endpoint_net() directly below before reusing it for a new field."""
    b = bytearray(ENDPOINT_LEN)
    struct.pack_into("<H", b, 2, port & 0xFFFF)
    b[4:8] = ipv4_le(host)
    return bytes(b)


def endpoint_net(host, port):
    """The SAME 20-byte struct, in NETWORK byte order.

    WARNING: FMO HAS TWO ENDPOINT CONVENTIONS AND THEY ARE OPPOSITE. The TCP path
    goes through polcore, which byte-swaps, so `endpoint()` above stores the
    address and port little-endian and the client's own htonl/htons put them
    back -- that is measured, and it is why the 0x0322 redirect works.

    FMO's OWN datagram sender does not swap. `0x611FED70` builds its sockaddr by
    copying the struct verbatim:

        mov edx, [ep+4]            ->  sockaddr.sin_addr   (dword, as-is)
        mov ax,  [ep+2]            ->  sockaddr.sin_port   (word,  as-is)
        mov word [sockaddr+0], 2       AF_INET

    -- no htons, no htonl. So an endpoint bound for that consumer must ALREADY
    be in network order, and one written by `endpoint()` arrives byte-reversed.

    MEASURED, not deduced (2026-08-18, pol-shim's sendto hook, which prints the
    raw sockaddr correctly -- b[4..7] as dotted quad, (b[2]<<8)|b[3] as port):

        [sendto] -> 71.3.168.192:29935

    127.0.0.1 = 0xC0A80347; little-endian that is 47 03 A8 C0, which read as
    a network-order address IS 71.3.168.192. 61300 = 0xEF74; little-endian that
    is 74 EF, which read big-endian IS 0x74EF = 29935. **Both fields, exactly.**
    Every world datagram was going to a host that does not exist, nothing could
    answer, and the scene timed out at 60s -> FMO-13111."""
    b = bytearray(ENDPOINT_LEN)
    struct.pack_into(">H", b, 2, port & 0xFFFF)
    parts = [int(x) for x in host.split(".")]
    if len(parts) != 4 or any(not 0 <= p <= 255 for p in parts):
        raise ValueError(f"not a dotted IPv4 literal: {host!r}")
    b[4:8] = bytes(parts)
    return bytes(b)


#: Which convention the 0x0153 endpoint gets. Default NETWORK order, because the
#: little-endian one is measured to send the world channel into space -- but it
#: stays a switch, because WHICH endpoint feeds the datagram sender is inferred,
#: not proven: 0x611FED70 reads it out of an object field (+0x10E8), and the
#: 0x0153 endpoint is simply the one delivered at the moment the scene starts.
#: FMO_0153_EP_NET=0 restores the old encoding for a clean A/B.
EP_0153_NET = os.environ.get("FMO_0153_EP_NET", "1") not in ("0", "false", "")
