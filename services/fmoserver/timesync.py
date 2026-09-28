"""Time requests and the time-sync push (0x013B, 0x0199)."""
import os
import struct
import time


#: TIME SYNC. The client sends 0x13B (empty) once the cipher is up and waits for
#: 0x13C. The handler at 0x6117A06E reads two dwords out of the reply payload:
#:
#:     edx = [rx+0x18]            ; payload +0x04
#:     eax = edx / 1000           ; (0x10624DD3 imul + sar 6)
#:     edx = [rx+0x14] * 1000     ; payload +0x00
#:     [0x613CACA0] = eax + edx   ; -> milliseconds
#:
#: so payload +0x00 is SECONDS and +0x04 MICROSECONDS -- a gettimeofday pair,
#: combined into a millisecond clock the game keeps at 0x613CACA0 and copies to
#: ctx+0x4EFE. Both halves are also passed raw to 0x611E3B50.
MSG_TIME_REQ = 0x013B
MSG_TIME_REPLY = 0x013C

#: 0x012D -- THE NON-POL GAME HELLO. The character-select machine 0x61179DA0
#: state 1 sends EITHER 0x015B (our normal game hello, payload = our 0x0322
#: token) OR 0x012D (payload = the u32 at [this+0x17C]) -- the switch is the
#: string at [0x613CC494], the boot argument 0x61009F56 copies "POL_BOOT" into:
#: non-empty = launched through PlayOnline = 0x015B. So 0x012D is the launcher-
#: less / debug boot, which no client of ours has ever taken. State 2 polls the
#: SAME way for both (message 1 or 0x0184), so the contract is an empty
#: message 1 on the request's sequence. WARNING: On this path the client SKIPS the
#: key set at 0x61179F2B (it zeroes [this+0x7640] instead), so we must NOT arm
#: the cipher after answering it. Answered, not because anyone needs it, but
#: so the driven sweep's "no handler" set is exactly the by-design five.
MSG_LOCAL_HELLO = 0x012D

#: 0x0199 -- TIME SYNC (arm 0x6117F25C). Gate: [lobby+0x20]==4 && [lobby+0x24]
#: != 6. Payload = four u32s handed to the clock object 0x613CA3D4's
#: 0x611E3BE0(a, b, c, d): t0 = a*1000 + b/1000 ms, t1 = c*1000 + d/1000 ms.
#: |t0 - clock| > 1500 ms -> "Time-sync packet Ping (%d) is too large" and
#: nothing changes; otherwise the clock becomes t1 folded with half the round
#: trip and 0x613CACA0 follows ("時刻合わせ: %5d(msec)" = "time adjust"). So
#: (a, b) is the ECHO of the client's own stamp and (c, d) is OUR time now.
#: KEY: The stamp to echo is the 0x0198 keepalive's body: 0x6117C304..0x6117C33F
#: writes dword0 = clock_ms / 1000 and dword1 = (clock_ms % 1000) * 1000 --
#: seconds and MICROSECONDS of the clock it last synced from our 0x013C, not
#: "a counter" as the keepalive handler used to say. This makes 0x0199 the
#: keepalive's reply in all but sequence number, which is why it goes out on
#: QUEUE_SEQ from that handler.
MSG_TIME_SYNC = 0x0199
S199_LEN = 0x10
#: FMO_TIME_SYNC: '0' (default) = keepalives stay unanswered, as they always
#: have. '1' = every 0x0198 gets a 0x0199 with its own stamp echoed. Scene 4
#: only by the arm's own gate; in the lobby it is dropped, so the risk is
#: confined to battles, where the clock is what mission timers run on.
TIME_SYNC = (os.environ.get("FMO_TIME_SYNC", "").strip() or "0") != "0"


def time_sync_body(echo_sec, echo_usec, now=None):
    """The 0x0199 body: the client's stamp back, then ours."""
    if now is None:
        now = time.time()
    sec = int(now)
    usec = int((now - sec) * 1_000_000)
    return struct.pack("<IIII", echo_sec & 0xFFFFFFFF, echo_usec & 0xFFFFFFFF,
                       sec & 0xFFFFFFFF, usec)


def time_sync_packet(conn_id, echo_sec, echo_usec, now=None):
    return packet.build(MSG_TIME_SYNC, time_sync_body(echo_sec, echo_usec, now),
                        pushes.QUEUE_SEQ, conn_id)


# Called at run time only; imported last so that import cycles resolve.
from . import packet, pushes  # noqa: E402
