"""The battle withdraw (0x013D)."""
import os


# ---------------------------------------------------------------------------
# THE BATTLE WITHDRAW -- 0x013D (battle withdraw worker, 2026-09-04)
# ---------------------------------------------------------------------------
#: 🆕 STATIC DECODE. The withdraw/retreat menu action drives the kycli_lobmain
#: state machine 0x61176630 -- one of the [lobby+0x24] tick states (table
#: 0x6117DCC0, index [lobby+0x24]-1; WITHDRAW is state 5, the SIBLING of the
#: sortie countdown 0x6117BEF0 at state 4). Its four sub-states switch on the
#: BYTE [lobby+0x2C] through the jump table 0x61176988:
#:
#:   state 0 (0x6117665E) -- SENDS 0x013D. Once the clear-to-transmit guard
#:       0x6119A380 allows, it builds the message with
#:       0x61199FC0(msg=0x13D, len=4, flags=0) and writes ONE byte,
#:       [lobby+0x2F], to body+0x00 (0x61176693 `mov [eax], dl`), latches
#:       [lobby+0x40]=1, saves the sent sequence 0x61199D40(1) -> [lobby+0x54],
#:       and advances [lobby+0x2C]. So on the wire the body is 4 bytes, body+0x00
#:       = the withdraw REASON byte, zero-extended -- which is why the live
#:       capture read a u32 = 3 in a 24-byte frame (20B header + 4B body).
#:   state 1 (0x611766DC) -- POLLS. 0x61199E30([lobby+0x54]) <= 0 keeps waiting
#:       (the HANG when nobody answers). On a reply it reads the receive slot
#:       [[globals+0x190]+0x7580] and compares ITS MESSAGE ID (+6) against 1
#:       (0x6117670D `cmp word [eax+6], 1`). Nothing reads the reply body.
#:         * id == 1 (SUCCESS): if [lobby+0x4F06] == 0xFFFE it self-terminates
#:           locally (0x611ED5A0 on [globals+0x188], sets [lobby+0x24]=2 and
#:           returns -- back to the lobby countdown state); otherwise (the
#:           normal battle case, 4F06 holds the current area code) it advances
#:           to state 2.
#:         * id != 1 (FAILURE): 0x61176758 tears down [lobby+0x1CC], sets
#:           [lobby+0x20]=5 and raises an error dialog (0x6116CF50, code
#:           0xC008001E) -- a graceful refusal, not a hang.
#:   state 2 (0x611767FD) -- sends 0x61173CE0(0xFFFD), which is the AREA-CHANGE
#:       request builder: msg 0x0150, len 0xC, body+0x00 = the word 0xFFFD (a
#:       "return/withdraw" sentinel), seq -> [lobby+0x54]; advances to state 3.
#:   state 3 (0x6117682D) -- polls for a reply whose id == 0x0153
#:       (`cmp word [eax+6], 0x153`), copies 0x3F dwords reply+0x28 ->
#:       lobby+0x6B3A, word reply+0x14 -> lobby+0x4F06, 0x16 dwords reply+0x124
#:       -> globals+0xD0, and calls 0x61006350(...) -- the scene transition that
#:       drops the player out of the battle onto the granted lobby map.
#:
#: KEY: SO THE SERVER CONTRACT IS: answer 0x013D with MESSAGE ID 1 (empty body) on
#: the request's own +0x10 seq. The follow-up 0x0150 (word 0xFFFD) that state 2
#: sends is ALREADY served by the MSG_0150_REQ handler (it recognises the
#: 0xFFFD/0xFFFF sentinels and returns a 0x0153), so no new handler is needed for
#: the second leg. This is the same "reply message 1" convention MSG_SESSION_START
#: / 0x0159 / the sortie GO (0x014D) already use, and the same poll shape
#: (0x61199E30 on a saved seq, id at reply+6) they were decoded from -- not a
#: chance match: the +6 message-id field and the +0x7580 receive slot are the
#: image-wide reader addresses, and three independent senders read id==1 here.
#:
#: THE REASON BYTE is variable: the initiator 0x61173F90(lobby, reason) writes it
#: to [lobby+0x2F] and its callers pass 0 (0x611600D1), 1 (0x610CAD15/0x610CAD52),
#: 4 (0x610BEAC4) and a runtime value (0x6115FB90). The live 3 is one such
#: withdraw-menu reason; the server neither needs nor is given a way to act on it.
MSG_BATTLE_WITHDRAW = 0x013D
WITHDRAW_TICK_STATE = 5                 # [lobby+0x24] value that runs 0x61176630
#: '1' (default) = reply message 1, the only id state 1 reads as success. 'fail'
#: = reply id 2, the client's graceful error-dialog arm (0x61176758). '0' = stay
#: silent, which is the HANG seen live on 2026-09-04. NEVER silent by
#: default: an unanswered 0x013D parks the poller in state 1 for good, the
#: 0x01AB/0x0159 shape.
ANSWER_013D = os.environ.get("FMO_ANSWER_013D", "1").strip() or "1"
