"""The link-death resume reply (0x0137 -> 0x0138)."""
import os
import struct
from .wirelog import log


#: Friendlier alias: the resource INDEX (0..40) rather than the raw dword.
#: Wins over FMO_STATUS_WFD4 when set, because it is the same field.
_res_idx = os.environ.get("FMO_RESUME_RES_INDEX", "").strip()
RESUME_RES_INDEX = int(_res_idx, 0) if _res_idx else None

RESUME_RES_BASE = 82159                # data\AI\F21\D59.DAT
RESUME_RES_COUNT = 41                  # D59..D99


def resume_resource_path(index):
    """`fmofile.py`'s mapping, inlined for the log: index -> data path."""
    q, r = divmod(RESUME_RES_BASE + index, 100)
    return "data\\%s%s\\F%02d\\D%02d.DAT" % (
        chr(ord("A") + q // 1000), chr(ord("A") + (q % 1000) // 100),
        q % 100, r)


# --------------------------------------------------------------------------- #
# THE LINK-DEATH RESUME REPLY -- 0x0137 -> 0x0138 (PLAN 2.3, decoded Brief 7,
# re-verified against the binary 2026-09-04)
# --------------------------------------------------------------------------- #
#: The client sends 0x0137 (8-byte body [resume_id, 1]) only from kycli_lobmain
#: state 8 -- the link-death / Colosseum-return resume, entered when
#: lobby+0x7604 (our FMO_STATUS_W7604) is non-zero. State 9 then polls for a
#: reply whose message id is 0x0138 (`cmp word[reply+6],0x138` at 0x6117BA34);
#: the body is NOT read. On match it advances into the 0x0139/0x013A sortie
#: handshake (SERVE_SORTIE); a NO match, or NO reply, leaves state 9 spinning --
#: the reconnect hang. So the contract is identical to withdraw's: message
#: 0x0138, EMPTY body, on the request's own seq.
MSG_RESUME_REQ = 0x0137
MSG_RESUME_REPLY = 0x0138
#: VERIFIED: DEFAULT ON. The resume path only fires after a link-death (or a served
#: W7604), so a handler is inert in normal play; when it DOES fire, answering is
#: the difference between "recoverable" and "hung at reconnect". Values mirror
#: FMO_ANSWER_013D: '1' = serve 0x0138, 'fail' = a wrong id (state 9's error
#: dialog, a graceful A/B), '0' = stay silent (the hang, on purpose).
ANSWER_0137 = os.environ.get("FMO_ANSWER_0137", "1").strip() or "1"


class SessionResume:
    """Session's answer to the link-death resume (0x0137)."""

    def on_resume(self, p):
        """0x0137 -- the link-death RESUME request. See MSG_RESUME_REQ.

        kycli_lobmain state 9 (0x6117BA18) polls this request's transaction and
        reads ONLY the reply's message id (`cmp word[reply+6],0x138`); on a
        0x0138 it advances into the ordinary 0x0139/0x013A sortie handshake, on
        anything else it raises a graceful error dialog (8:26), and on NO reply
        it spins in state 9 -- the reconnect hang. So an empty message 0x0138 on
        the request's own seq is the whole contract. The 8-byte body is
        [resume_id, 1]; we do not act on it."""
        rid = struct.unpack_from("<I", p["payload"], 0)[0] \
            if len(p["payload"]) >= 4 else 0
        log(f"{self.peer}   0x{MSG_RESUME_REQ:04X} = LINK-DEATH RESUME "
            f"({len(p['payload'])}B): resume_id {rid} at body+0x00 (from "
            f"lobby+0x4F02 <- W7604). kycli state 9 (0x6117BA18) is polling seq "
            f"0x{p['seq']:08X} for a reply whose message id is "
            f"0x{MSG_RESUME_REPLY:04X}.")
        if ANSWER_0137 == "0":
            log(f"{self.peer}   WARNING: FMO_ANSWER_0137=0: staying silent. State 9's "
                f"0x61173F40 keeps returning <= 0, the machine never leaves "
                f"state 9, and the client hangs at 'reconnecting to the battle "
                f"map' -- the resume hang, on purpose.")
            return []
        if ANSWER_0137 == "fail":
            log(f"{self.peer}   -> message 2 (FMO_ANSWER_0137=fail): a WRONG id, "
                f"so state 9's 0x6117BA40 arm raises the error dialog "
                f"(0xC008001A = 8:26) and abandons the resume gracefully -- "
                f"an A/B to see the failure path rather than the hang.")
            return [packet.build(2, b"", self.reply_seq(), p["conn"])]
        log(f"{self.peer}   -> message 0x{MSG_RESUME_REPLY:04X} = RESUME OK: "
            f"state 9 reads only the id and advances to state 10, which sends "
            f"0x0139 and awaits 0x013A -- the ordinary sortie handshake "
            f"(SERVE_SORTIE). With FMO_SORTIE off it lands on a clean 10:6 "
            f"failure back in the lobby, NOT the hang. Empty body: 0x6117BA34 "
            f"reads only the id.")
        return [packet.build(MSG_RESUME_REPLY, b"", self.reply_seq(), p["conn"])]


# Called at run time only; imported last so that import cycles resolve.
from . import packet  # noqa: E402
