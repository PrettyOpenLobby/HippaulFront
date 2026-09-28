"""The login handshake on 61300: version, credentials (0x0321), the 0x0322 redirect and session
start."""
import os
from .knobs import _env_float


MSG_VERSION = 0x0065
MSG_CREDENTIALS = 0x0321
MSG_HANDSHAKE_OK = 0x0002      # 0x61199c79's `cmp word [eax+6], 2`
#: Requests and replies pair as N / N+1 -- 0x0321 -> 0x0322, and the same shape
#: shows up all over the dispatch table (0x104a/0x104b, 0x1053/0x1054, ...).
MSG_CRED_REPLY = 0x0322        # 0x61179cbe's `cmp word [eax+6], 0x322`
#: After 0x0322 the client ADVANCES AND ONLY POLLS (0x61179ed7) -- it sends
#: nothing and waits for the server to push message 1 unprompted. That is why a
#: correct 0x0322 looks like a hang: silence is the client working.
#:
#: WARNING: 0x0184 IS NOT AN ALTERNATIVE TO MESSAGE 1 -- it is the REJECTION. An earlier
#: note here read the dispatch as "message 1 or 0x184"; both ids are accepted by
#: that poll, but 0x61179fce shows 0x184's arm reads word[rx+8] as an ERROR CODE
#: and jumps straight to the reporter. Never send it as a success.
#:
#: After message 1 arms the cipher, the client's own next action is to SEND
#: message 0x13B with an empty payload (0x6117a003: init(0x13B, 0, 0) then the
#: send-arm) -- encrypted, since the cipher is up by then.
#:
#: WARNING: MESSAGE 1 ARMS SESSION ENCRYPTION. Its handler at 0x61179f0d calls
#: 0x61199eb0, which keys the RX/TX crypto contexts and sets ctx+0x7602 = 1.
#: The encryption path reads NOTHING out of the message-1 packet (it takes the
#: key from ctx+0x7640, polcore's), so a bare 20-byte packet is a valid one --
#: and sending it is the experiment that CONFIRMS the whole model: the client's
#: next packet should come back with byte +0x03 bit 0 SET, i.e. flags 0x0300
#: instead of 0x0200, and a body we can no longer read.
MSG_SESSION_START = 0x0001
#: The GAME's opening message on the SECOND connection. 0x61179EB4 builds it with
#: payload = ctx+0x7664, i.e. FIELD A of our own 0x0322 reply -- which is why the
#: one we receive is four zero bytes: that is what we put in field A.
#:
#: MESSAGE 1 IS THE REPLY TO THIS, not to the credentials. State 1 of the
#: 0x6117A274 table sends 0x15B and advances; state 2 polls for message 1. We had
#: been pushing message 1 after the credentials, on the FIRST connection, where
#: nothing was waiting for it.
MSG_GAME_HELLO = 0x015B

#: "Start Game" once the character list is non-empty. Built at 0x6117B144:
#:     push 0 / push 0x14 / push 0x130 / call init
#:     payload[0] = [ebp+0x1DC]
#: and the state that follows polls for a reply whose id is **2**
#: (0x6117B1A3: `cmp word [ecx+6], 2`). The success arm reads nothing out of the
#: payload -- it clears ctx+0x24 and moves on -- so an empty reply should do.
#:
#: WARNING: Message 2 is REUSED here. It is the transport handshake id on the POL login
#: connection and the reply to 0x130 on the game connection; same number,
#: different conversation. Do not unify the two handlers.
MSG_START_GAME = 0x0130
#: 0x130's SUCCESS reply. Per the N/N+1 pairing everywhere else in this protocol.
#:
#: WARNING: MESSAGE 2 IS THE FAILURE REPLY HERE, not the success one -- I had it exactly
#: backwards and produced the very error I was chasing. At 0x6117B1A3:
#:
#:     cmp word [ecx+6], 2
#:     jne 0x6117B279          ; NOT 2 -> the success arm, which formats
#:                             ;   "/btlreview/%x/brdata%03d.dat" and loads
#:     ...                     ; == 2 -> the FAILURE arm:
#:     cx = word [rx+8]        ;   the reply's +0x08 ...
#:     mov [ebp+0x7D5F], cx    ;   ... stored as the ERROR CODE
#:     call 0x611F18F0         ;   and an error string is looked up
#:
#: `ebp+0x7D5F` is the same field message 1's REJECTION path writes at
#: 0x61179A45. So answering 0x130 with message 2 and a zero +0x08 is precisely
#: how to make the client print [FM00000]. Same shape as 0x184 being message 1's
#: rejection: in this protocol the "2" reply means NO.
MSG_START_GAME_OK = 0x0131

#: Push message 1 after the 0x0322? On by default because it is the experiment
#: that proves the model. Set FMO_PUSH_SESSION_START=0 to stop before the
#: encryption switch and keep the conversation readable.
#: Push message 1 after the 0x0322, on the FIRST connection? Default NO now.
#: Message 1 is the reply to 0x15B, which arrives on the SECOND connection -- see
#: MSG_GAME_HELLO. Pushing it here was answering a question nobody had asked, and
#: it armed our cipher on a connection the client was about to drop.
PUSH_SESSION_START = (os.environ.get("FMO_PUSH_SESSION_START", "").strip() or "0") != "0"

#: Answer the VERSION packet at all? Default NO -- and this is the finding that
#: unblocked the login.
#:
#: MEASURED 2026-08-17, reading the client's own receive state through pol-shim:
#:     login_state=2 | rx_state=4 rx_msg=0x0002 rx_seq=0x1001
#: The buffer was still holding OUR HANDSHAKE-OK, the first reply of the session,
#: ready and unconsumed, while the login machine polled for 0x1002.
#:
#: The receive path has ONE buffer slot (ctx+0x7580) and one ready flag
#: (ctx+0x28). 0x61199C79 marks it ready and latches the connection id, but never
#: consumes it -- only a poll whose handle MATCHES does. The client pipelines
#: version and credentials without waiting, so it waits for exactly one reply,
#: the credentials'. A reply to the version jams the slot for ever and every
#: later packet lands on a full one.
#:
#: The corroboration was on the wire all along: every client packet carries
#: conn=0x0000, never the id we assigned -- it never consumed the message that
#: would have set it.
SEND_HANDSHAKE_OK = (os.environ.get("FMO_SEND_HANDSHAKE_OK", "").strip() or "0") != "0"

#: Seconds to wait between the 0x0322 reply and message 1.
#:
#: MEASURED 2026-08-17, not guessed: with both sent back-to-back the client parks
#: in login state 2 for ever. pol-shim's `[fmokey] watch_gate` read the state byte
#: out of the login object directly -- `login_state=2`, with the 0x13B gate OPEN --
#: so the gate was never the blocker and the machine simply never advanced.
#:
#: State 2 (0x61179ED7) polls 0x61199E30 with the handle of its OWN last send:
#: "has the reply to my request arrived", one message per tick. Two packets
#: written back-to-back coalesce into a single TCP segment, so message 1 can
#: arrive while the machine is still in the state that wanted 0x0322.
#:
#: A delay costs nothing and removes that whole class of failure. 0 restores the
#: back-to-back behaviour for an A/B.
SESSION_START_DELAY = _env_float("FMO_SESSION_START_DELAY", "0.75")

#: The 0x0322 handler at 0x61179cd4 copies these out of the packet, so the reply
#: must be at least 0x50 bytes -- 60 bytes of payload. Offsets here are PAYLOAD
#: offsets (packet offset - 0x14):
#:     packet +0x14/+0x18/+0x1C/+0x20  four dwords -> ctx +0x7668/+0x766C/
#:                                     +0x7670/+0x7664
#:     packet +0x28..+0x3B             20 bytes    -> ctx +0x7674
#:     packet +0x3C..+0x4F             20 bytes    -> ctx +0x769C
#: WARNING: FIELD D (packet +0x1C) IS KEY MATERIAL. Message 1 copies it to ctx+0x7650,
#: which is bytes 16..19 of the 20-byte session key at ctx+0x7640 -- [polcore+0xFAC]
#: fills 0..15 and the SERVER supplies the last four. Field A is a red herring:
#: its consumers sprintf it into "/btlreview/%x/brdata%03d.dat".
#: The other four are still unknown. We send zeros as an honest first probe:
#: the client's failure path only reads an error code when the id is NOT 0x322,
#: so a well-formed 0x0322 should advance its state machine and let it tell us
#: what it wants next. Do not dress these up as known fields.
CRED_REPLY_LEN = 0x3C
#: PAYLOAD offset of field A (packet +0x20). See the session-token note.
FIELD_A_OFF = 0x0C
