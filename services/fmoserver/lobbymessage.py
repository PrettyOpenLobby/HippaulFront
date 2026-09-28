"""Text into the client's message window (0x014B) and the lobby announcement."""
import os
import struct
from .knobs import _env_int


# --------------------------------------------------------------------------- #
# 0x014B -- TEXT INTO THE CLIENT'S MESSAGE WINDOW (static 2026-09-09)
# --------------------------------------------------------------------------- #
#: Another arm of the same queue dispatcher (0x6117E03E), and the reason it is
#: built here rather than filed away: **it is the cheapest possible POSITIVE
#: CONTROL for the push transport itself.** 0x015A is unproven and its failure
#: mode is silence, so a launch where nothing happens cannot tell "the id/length
#: is wrong" from "the whole queue-push path does not reach this client". One
#: line of text on screen separates those two, and costs a single env var.
#:
#: THE LAYOUT, read off the arm (`ebp` is the frame; payload = frame+0x14):
#:   +0x000 u8   KIND. `dec ecx / cmp ecx,5 / ja` then jump table 0x6117F8A4:
#:               1 and 4 -> 0x611732D0(flag=1, text); 2 -> 0x611732D0(flag=0);
#:               3, 5, 6 -> the CHAT-window path 0x611D4EF0. Kinds 4 and 7 are
#:               ALSO handed to 0x610E6460 / 0x61164850 before the table.
#:   +0x001 u8   picks between poster forms (0 -> channel 1/colour 4;
#:               nonzero -> 5/8) on the chat path.
#:   +0x004      the TEXT, NUL-terminated, cp932. 0x611732D0 splits it on '\n'
#:               into a bounded 0x145-byte line buffer, so the CLIENT truncates
#:               rather than overruns -- but the room to +0x3EC is 1000 bytes.
#:   +0x3EC      sender first name    } used by the chat path's 0x611D4EF0
#:   +0x3FD      sender last name     }
#:   +0x410 u32  sender id -- passed to the ignore/filter check 0x610DE8C0,
#:               which DROPS the message when it returns nonzero.
#: Minimum body 0x414 = 1044 bytes.
#:
#: WARNING: WHAT IS NOT ESTABLISHED: which kind is which channel on screen. The
#: dispatch is read exactly, the LABELS are not, so this serves kind 2 -- the
#: plainest of the three (the message-window poster with flag 0, no name
#: fields, no filter interaction). Do not read "kind 2" here as "say".
MSG_LOBBY_MESSAGE = 0x014B
S14B_KIND = 0x000
S14B_FLAG = 0x001
S14B_TEXT = 0x004
S14B_NAME1 = 0x3EC
S14B_NAME2 = 0x3FD
S14B_SENDER = 0x410
S14B_BODY_LEN = 0x414
S14B_TEXT_MAX = S14B_NAME1 - S14B_TEXT - 1      # 999 + the NUL

#: FMO_ANNOUNCE: one line of text pushed to a pilot shortly after world entry,
#: once per session. Empty (default) = nothing is sent.
ANNOUNCE = os.environ.get("FMO_ANNOUNCE", "").strip()
ANNOUNCE_KIND = _env_int("FMO_ANNOUNCE_KIND", "2")


def lobby_message_body(text, kind=2, flag=0, first="", last="", sender=0):
    """A 0x014B message-window push. cp932, NUL-terminated, refuses overlong."""
    raw = (text or "").encode("cp932", "replace")
    if len(raw) > S14B_TEXT_MAX:
        raise ValueError(
            f"0x{MSG_LOBBY_MESSAGE:04X}: {len(raw)} bytes of text, cap "
            f"{S14B_TEXT_MAX} -- the field runs +0x{S14B_TEXT:03X}.."
            f"+0x{S14B_NAME1:03X} and the sender name follows it. Longer text "
            f"would run into the name the chat path reads.")
    if kind not in (1, 2, 3, 4, 5, 6, 7):
        raise ValueError(
            f"0x{MSG_LOBBY_MESSAGE:04X}: kind {kind} is outside the arm's "
            f"jump table (1..6, plus 7 which is special-cased before it). "
            f"0x6117E07A does `dec ecx / cmp ecx,5 / ja` -- an out-of-range "
            f"kind falls through and posts nothing.")
    b = bytearray(S14B_BODY_LEN)
    b[S14B_KIND] = kind & 0xFF
    b[S14B_FLAG] = flag & 0xFF
    b[S14B_TEXT:S14B_TEXT + len(raw)] = raw
    for off, name, cap in ((S14B_NAME1, first, S14B_NAME2 - S14B_NAME1),
                           (S14B_NAME2, last, S14B_SENDER - S14B_NAME2)):
        s = (name or "").encode("cp932", "replace")[:cap - 1]
        b[off:off + len(s)] = s
    struct.pack_into("<I", b, S14B_SENDER, sender & 0xFFFFFFFF)
    return bytes(b)


def lobby_message_packet(conn_id, text, **fields):
    """The 0x014B push on the queue sequence. Nothing polls for it."""
    return packet.build(MSG_LOBBY_MESSAGE, lobby_message_body(text, **fields),
                        pushes.QUEUE_SEQ, conn_id)


# Called at run time only; imported last so that import cycles resolve.
from . import packet, pushes  # noqa: E402
