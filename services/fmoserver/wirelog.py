"""The listening port, the log directory, and the log and hexdump helpers every module writes
through."""
import os
import sys
import threading
import time
from .knobs import _env_float, _env_int


PORT = _env_int("FMO_PORT", "61300", 10)
LOG_DIR = os.environ.get("POL_LOG_DIR", "/logs")
#: How long a LOBBY connection may stay SILENT before we close it (the Fantasy
#: Earth 54848 lesson: a bind that leaves the client waiting is worse than no
#: bind). Re-armed by every byte received -- see serve_client; it was a
#: lifetime ceiling until 2026-09-11 and hung up on live players. The community
#: connection (serve_mission_client) still treats it as a lifetime.
HOLD = _env_float("FMO_HOLD", "120")


#: the editor's log tail (fedevtool.note) once the panel is up -- so "did that
#: pop?" is answerable on the page, not only in the container's stdout
_DEVTOOL_NOTE = None

#: fmo.log is kept open between lines and rotated, the same shape as
#: srvcore._log_handle_locked: POL_LOG_MAX_MB per file (0 = append forever),
#: POL_LOG_KEEP older generations. It used to open, append and close once per
#: line with no cap, and a busy lobby logs several lines per message.
_LOG_LOCK = threading.Lock()
_LOG_FILE = None
_LOG_MAX = int(_env_float("POL_LOG_MAX_MB", "8") * 1024 * 1024)
_LOG_KEEP = _env_int("POL_LOG_KEEP", "3", 10)
#: Per-payload hexdumps in tcpserver: one line per 16 bytes of every packet,
#: most of the log by volume. Off unless FMO_WIRE_HEXDUMP=1.
WIRE_HEXDUMP = os.environ.get("FMO_WIRE_HEXDUMP", "").strip().lower() in ("1", "on", "yes", "true")


def _log_handle_locked():
    """The open fmo.log handle, rotated first if past the cap. Caller holds
    _LOG_LOCK. Size is checked on the first write too, so a log inherited
    oversized from the previous run rotates at once."""
    global _LOG_FILE
    f = _LOG_FILE
    path = os.path.join(LOG_DIR, "fmo.log")
    try:
        size = f.tell() if f is not None else os.path.getsize(path)
    except OSError:
        size = 0
    if _LOG_MAX > 0 and size >= _LOG_MAX:
        if f is not None:
            f.close()
            _LOG_FILE = None
        f = None
        for i in range(_LOG_KEEP, 0, -1):
            src = path if i == 1 else f"{path}.{i - 1}"
            dst = f"{path}.{i}"
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    break
    if f is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        f = open(path, "a", encoding="utf-8")
        _LOG_FILE = f
    return f


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}Z [fmo] {msg}"
    if _DEVTOOL_NOTE is not None:
        try:
            _DEVTOOL_NOTE(line)
        except Exception:
            pass
    # The container's stdout is UTF-8 but a Windows console is cp1252, and these
    # messages carry WARNING: and OK:. Printing must never be able to kill the responder
    # or the selftest, so degrade the characters instead of raising.
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "ascii")
        print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)
    try:
        with _LOG_LOCK:
            fh = _log_handle_locked()
            fh.write(line + "\n")
            fh.flush()
    except OSError:
        pass


def hexdump(b, indent="    "):
    out = []
    for i in range(0, len(b), 16):
        c = b[i:i + 16]
        txt = "".join(chr(x) if 32 <= x < 127 else "." for x in c)
        out.append(f"{indent}{i:04x}  {c.hex(' '):<47}  {txt}")
    return "\n".join(out)
