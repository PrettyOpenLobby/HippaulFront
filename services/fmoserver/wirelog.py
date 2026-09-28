"""The listening port, the log directory, and the log and hexdump helpers every module writes
through."""
import os
import sys
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
        with open(os.path.join(LOG_DIR, "fmo.log"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def hexdump(b, indent="    "):
    out = []
    for i in range(0, len(b), 16):
        c = b[i:i + 16]
        txt = "".join(chr(x) if 32 <= x < 127 else "." for x in c)
        out.append(f"{indent}{i:04x}  {c.hex(' '):<47}  {txt}")
    return "\n".join(out)
