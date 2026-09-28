"""The TCP accept loop: one thread per client, the game and the community server told apart."""
import os
import socket
import struct
import time
from .deps import fmomsn
from .wirelog import hexdump, log
from . import wirelog


def serve_client(conn, addr):
    peer = f"{addr[0]}:{addr[1]}"
    sess = session.Session(peer)
    log(f"{peer} CONNECT (hold {wirelog.HOLD}s idle)")
    # WARNING: FMO_HOLD IS AN *IDLE* WINDOW, NOT A LIFETIME (2026-09-11). It used to
    # be `connect + HOLD`, never extended -- a dev-era ceiling from 08-17, when
    # no conversation lasted minutes. Once the lobby worked it hung up on LIVE
    # players 15 minutes after login: 21:50:09Z cut a player off 8 minutes
    # into the wanzer SETUP screen while their 0x0198 keepalive was still
    # arriving every 15 s (last one 21:49:58Z), and 18:50:52Z did the same to
    # the 18:36Z visit. The setup screen sends NOTHING on TCP until the 0x0167
    # save, so a ceiling guarantees the save never lands. Every byte the client
    # sends now re-arms the window; a client that goes silent is still closed.
    deadline = time.monotonic() + wirelog.HOLD
    buf = bytearray()
    total = bytearray()
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                log(f"{peer} hold expired -- silent for {wirelog.HOLD}s, closing "
                    f"so the client fails cleanly")
                break
            conn.settimeout(min(15, left))
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            if not data:
                log(f"{peer} client closed")
                break
            deadline = time.monotonic() + wirelog.HOLD
            buf += data
            total += data
            # KEY: IS THIS THE COMMUNITY/MISSION SERVER INSTEAD? The client opens
            # a SECOND connection to this same host:port -- our own 0x0322
            # endpoint sends it here -- and speaks a different protocol on it.
            # Decide once, on the first whole frame, and decide it by MD5:
            # `looks_like_frame` is only a sieve, `parse` is the proof, and a
            # POL packet cannot forge a 16-byte digest by accident.
            if fmomsn is not None and sess.rx is None and not sess.identified:
                want = fmomsn.looks_like_frame(bytes(buf))
                if want is not None and len(buf) >= want:
                    if fmomsn.parse(bytes(buf[:want])) is not None:
                        sess.identified = True
                        community.serve_mission_client(conn, peer, bytes(buf))
                        return
                if want is None or len(buf) >= want:
                    # Not ours, and never will be: stop testing every read.
                    sess.identified = True
            # Frame on the u16 length at +0x00, which the send-arm proves is the
            # byte count. A short read is normal; wait for the rest.
            while len(buf) >= packet.HDR:
                ln = struct.unpack_from("<H", buf, 0)[0]
                if ln < packet.HDR:
                    log(f"{peer} WARNING: length {ln} < header {packet.HDR}; framing is wrong. "
                        f"Dropping the connection rather than guessing:\n"
                        + hexdump(bytes(buf[:64])))
                    buf.clear()
                    break
                if len(buf) < ln:
                    break
                pkt = bytes(buf[:ln])
                del buf[:ln]
                # DECRYPT BEFORE ANYTHING ELSE -- the client checksums the
                # plaintext (0x61199c15 runs before 0x61199c34), so a checksum
                # verified on ciphertext is meaningless.
                flag_byte = (struct.unpack_from("<H", pkt, 2)[0] >> 8) & 0xFF
                if flag_byte & packet.FLAG_ENCRYPTED and sess.rx is None \
                        and sess.key_tail is not None:
                    # Content auth: the first packet in picks the key (and so
                    # the member). Already decrypted on success.
                    pkt = sess.resolve_key(pkt)
                    if pkt is None:
                        return
                    log(f"{peer} <- DECRYPTED {ln}B with the RX stream "
                        f"(keyed by this packet)")
                elif flag_byte & packet.FLAG_ENCRYPTED:
                    if sess.rx is None:
                        log(f"{peer} WARNING: encrypted packet before we armed a cipher "
                            f"-- we cannot read it and the stream cannot resync. "
                            f"Dropping the connection rather than guessing.")
                        return
                    body = sess.rx.crypt(pkt[packet.CRYPT_OFF:])
                    pkt = pkt[:packet.CRYPT_OFF] + body
                    log(f"{peer} <- DECRYPTED {ln}B with the RX stream")
                p = packet.parse(pkt)
                log(f"{peer} <- {packet.describe(p)}")
                if flag_byte & packet.FLAG_ENCRYPTED and not p["chk_ok"]:
                    log(f"{peer} WARNING: checksum FAILED after decryption -- the key or "
                        f"the cipher is wrong, or the streams are out of step. "
                        f"This is the signal that the RC4 model is broken; do not "
                        f"read it as a transient.")
                if p["payload"]:
                    log(hexdump(p["payload"]))
                if not p["chk_ok"]:
                    log(f"{peer} WARNING: checksum mismatch -- our reading of the "
                        f"packet is wrong, do NOT paper over this")
                for out in sess.on_packet(p):
                    q = packet.parse(out)
                    # Space message 1 away from the 0x0322 it follows, so the two
                    # cannot arrive in one segment while the client's state
                    # machine is still on the first. Per-connection thread, so
                    # this delays nobody else.
                    if q["msg"] == handshake.MSG_SESSION_START and handshake.SESSION_START_DELAY > 0:
                        log(f"{peer}   waiting {handshake.SESSION_START_DELAY}s before "
                            f"message 1 so it cannot coalesce with the 0x0322")
                        time.sleep(handshake.SESSION_START_DELAY)
                    log(f"{peer} -> {packet.describe(q)}")
                    # Encrypt only once the cipher is armed, and only packets
                    # built AFTER arming -- on_packet arms strictly after
                    # building message 1, so that one goes out in the clear.
                    if sess.tx is not None:
                        outb = bytearray(out)
                        outb[3] |= packet.FLAG_ENCRYPTED      # tell the client to decrypt
                        # The checksum is over the PLAINTEXT, flags included, so
                        # it must be recomputed with the flag set and only then
                        # enciphered.
                        struct.pack_into("<H", outb, packet.CHK_OFF, 0)
                        struct.pack_into("<H", outb, packet.CHK_OFF,
                                         packet.checksum(bytes(outb)))
                        out = bytes(outb[:packet.CRYPT_OFF]) + \
                            sess.tx.crypt(bytes(outb[packet.CRYPT_OFF:]))
                        log(f"{peer} -> ENCRYPTED with the TX stream")
                    conn.sendall(out)
                # Now that the whole batch is on the wire, arm if asked. Doing
                # this here rather than in on_packet is the entire point.
                if sess.pending_arm is not None:
                    sess.arm_cipher(sess.pending_arm)
                    if sess.key_tail is not None:
                        log(f"{peer}   cipher ARMED after sending that batch in "
                            f"the clear: RC4, key = <16-byte 4:5 content auth "
                            f"value, picked from the client's first packet> + "
                            f"{sess.pending_arm.hex()} (ours)")
                    else:
                        log(f"{peer}   cipher ARMED both directions AFTER sending "
                            f"that batch in the clear: RC4, key = 16 zero bytes "
                            f"(polcore, tapped) + {sess.pending_arm.hex()} (ours)")
                    sess.pending_arm = None
    except Exception as e:
        log(f"{peer} error: {e}")
    finally:
        # Bank the seconds this connection ran BEFORE anything else in the
        # teardown can raise. Play Time is the only reader, but it reads what
        # is on file -- a session that is never asked still has to count, or
        # the number only ever grows when the player looks at it.
        if sess.play_mark is not None:
            try:
                _pt = sess.playtime_seconds()
                log(f"{peer} play time now {_pt}s "
                    f"({'%dd %dh %dm' % move.playtime_dhm(_pt)})")
            except Exception as e:
                log(f"{peer} play time not banked at close: {e!r}")
        if total:
            try:
                os.makedirs(os.path.join(wirelog.LOG_DIR, "captures"), exist_ok=True)
                path = os.path.join(
                    wirelog.LOG_DIR, "captures",
                    "fmo-%s.bin" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
                with open(path, "wb") as fh:
                    fh.write(bytes(total))
                log(f"{peer} saved {len(total)}B -> {path}")
            except OSError as e:
                log(f"{peer} could not save capture: {e}")
        conn.close()
        log(f"{peer} CLOSE")


# Called at run time only; imported last so that import cycles resolve.
from . import community, handshake, move, packet, session  # noqa: E402
