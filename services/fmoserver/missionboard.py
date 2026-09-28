"""Mission accept, report and cancel: message ids, refusal codes and the knobs that gate them."""
import os


# --------------------------------------------------------------------------- #
# MISSION ACCEPT -- 0x018A, and the refusal codes SE's own client maps to words
# --------------------------------------------------------------------------- #
# Static 2026-09-12. The request is
# built at 0x61172430 off the API slot `lobby+0x7452`:
#
#     body +0x00  u32  = the selected row's record+0x00   (the MISSION ID)
#     body +0x04  0x16 dwords copied from window+0x1A2    (NOT decoded)
#
# KEY: Its reply parse is `0x6122B670 = xor eax,eax; ret` -- it reads NOTHING --
# and the dispatcher 0x61172250 compares only `word[frame+6]` against the id the
# slot waits for, with no length check anywhere. So an EMPTY 0x018B is a
# complete, correct success, and that is what the generic LOBAPI arm has always
# sent. Live 09-08 the accept dialog already closed cleanly. What was missing
# was never the handshake -- it was an id to key on and a verdict to give.
MSG_MISSION_ACCEPT = 0x018A
MSG_MISSION_ACCEPT_REPLY = 0x018B

#: KEY: THE REFUSAL VOCABULARY, and it is OURS. `0x611CADC3` reads
#: `word[lobby+0x7D5F]` -- written from `word[frame+8]` of a MESSAGE-2 reply
#: (0x6117230D), the same channel FMO_NAME_UNIQUE uses for 0xC43B -- sign-extends
#: it and maps it through `0x611C4B30`, a `jmp [(code+9)*4 + 0x611C4BA8]` over 12
#: entries. So SE's rank/money/MP accept gates were NEVER client-side checks
#: against the row: they are server verdicts, and every one of them is a
#: sentence the game already knows how to say.
#: WARNING: The code travels as a SIGNED 16-bit value; build() takes it as conn_id and
#: packs `<H`, so negatives go on the wire as `code & 0xFFFF`.
MISSION_REFUSALS = {
    2: "27:3 'Your rank is not high enough to accept this mission.'",
    1: "27:2 'You do not have enough money to accept this mission.'",
    -3: "27:6 'This mission has already been accepted.'",
    -4: "27:0 'You have already accepted this mission.'",
    -5: "27:5 'You cannot accept a mission you ordered yourself.'",
    -7: "27:7 'You do not have enough MP to accept this mission.'",
    -8: "27:13 'This sector is already the target of another area mission.'",
    -9: "27:11 'This sector cannot be targeted.'",
}


def mission_refusal_text(code):
    """What 0x611C4B30 will put on screen for `code`. Total -- every path in
    that function is covered, so this never has to say 'unknown'."""
    if code in MISSION_REFUSALS:
        return MISSION_REFUSALS[code]
    if code < -500:
        # 0x611C4B77: eax = 0xFFFFFE0D - code, formatted into 27:12.
        return (f"27:12 '...can only target sectors in warzone {-499 - code}' "
                f"(the < -500 arm)")
    return "27:4 'The operation failed.' (the default arm)"


#: WARNING: FMO_MISSION_ACCEPT -- what to answer a mission accept with.
#: '1' (DEFAULT): the empty 0x018B = SUCCESS -> 21:25 "This mission has been
#:     accepted." Byte-identical to what the generic LOBAPI arm already sent,
#:     so arming the knob alone changes nothing on the wire.
#: '<signed int>': REFUSE with that code -- message 2, code in +0x08. `2` is the
#:     rank refusal and is the cheapest end-to-end proof of the table above,
#:     because it is a sentence nobody could mistake for a coincidence.
#: 'gate': VERIFIED: THE REAL THING -- judge the pilot's own stored rank / money / MP
#:     against THE ROW'S OWN required rank (+0x1E4), fee (+0x1DC) and MP
#:     (+0x1E8), and refuse with the code that failure owns (2 / 1 / -7). A row
#:     whose requirement is 0 is not a requirement, so rows authored without
#:     FMO_MSN_FIELDS stay acceptable and this behaves exactly like '1'.
#:     See mission_accept_verdict(): the ORDER of the three checks is ours, and
#:     the requirements are read back out of the bytes the client was shown.
#: '0' is NOT silence here -- it is a real code (the generic "operation
#:     failed"). Silence is not offered: the sender polls, so saying nothing
#:     parks the dialog.
#: WARNING: DEFAULT IS '1', NOT 'gate', and that is deliberate: a gate that has never
#: been watched refuse anybody is a guess, and the accept path is now the one
#: thing in FMO's mission UI that demonstrably works. Arm 'gate' on purpose.
MISSION_ACCEPT = os.environ.get("FMO_MISSION_ACCEPT", "").strip() or "1"

#: WARNING: FMO_MISSION_FEE -- CHARGE the row's fee (+0x1DC) on a successful accept.
#: Default OFF. Only meaningful with FMO_MISSION_ACCEPT=gate, which is what
#: checks the pilot can afford it in the first place.
#:
#: VERIFIED: The Fee is real and it is a COST: live 2026-09-12 the Mission Info panel
#: showed our +0x1DC=500 as the fee to get in, next to "Required rank: Major
#: General" from +0x1E4. SE charges one too -- the audit's 降下作戦 (drop
#: operation) is "a sector mission with an ACCEPT FEE".
#:
#: WARNING: WHY THERE IS NO 0x01A1 PUSH BEHIND THIS. `fee_push()` would update the
#: client's displayed H$ immediately, and it is tempting -- but its +0x08 = 0
#: selects systext **8:74 "Paid H$%d as the sortie cost for a modified unit"**,
#: and 8:82 (the only other fee wording nearby) is the Coliseum entry fee.
#: Neither is a mission acceptance fee, and telling the player they paid for
#: something they did not is worse than a stale number. The debit is banked;
#: the display catches up at the next 0x014A.
#: **Bar: the Profile's H$ is lower after a relog.**
#:
#: WARNING: KNOWN HOLE, and it is why this is off by default: nothing records that a
#: mission was accepted, so accepting the same row twice charges twice. SE has
#: a code for exactly that state (-4, 27:0 "You have already accepted this
#: mission") and we cannot raise it until an accept is stored.
MISSION_FEE = (os.environ.get("FMO_MISSION_FEE", "").strip() or "0") != "0"

#: KEY: THE REPORT, THE CANCEL, AND THE STATE THEY SHARE (static 2026-09-12,
#: NOT LIVE; read against the client's own parser).
#:
#: WARNING: The report is **0x01C6 -> 0x018F**, NOT 0x0196 -> 0x0197 as the
#: earlier note said: the lobby-API map puts 0x01C6 at lobby+0x7464, and that
#: slot is the one 0x611CE619/0x611CE829 start and 0x611CA9B0 polls before it
#: draws group 28. **0x0196 is CANCEL** -- on its stub-parsed reply the client
#: itself writes state 5 / result 1 into the row (0x611CAC5B/0x611CAC6E) and
#: draws 21:24 "Cancelled.". Both requests are 36 B with the mission id at
#: body+0x00 (0x611724F1 / 0x61172711): the report's is [G+0x593C] = the
#: View-B row's own +0x00, the cancel's is [G+0x5BF4] = the selected row's.
#:
#: 0x018F's parse 0x61172500 copies ONE 692-B View-B record from body+0x20 to
#: [0x613C15FC]+0x5940, and the poll switches on that copy: record +0x118
#: (read as G+0x5A58) and +0x234 (read as G+0x5B74).
#: KEY: That "+0x5B74" was never the mission WINDOW's byte. `ecx` is reloaded
#: from [0x613C15FC] at 0x611CAA36, one instruction before the read, so the
#: feared overlap with the window's +0x5B75 UI state was two different objects
#: that happen to share an offset.
#:   state 4             -> 28:3 "The mission has not been completed yet."
#:   state 5, result 2   -> 28:4 "Mission completion confirmed. The reward
#:                          will be paid by the Personnel.Officer."
#:   state 5, result !=2 -> 28:5 "The mission ended in failure."
#:   state 6             -> 28:6 "The mission's time limit has passed..."
#:   anything else       -> NO dialog -- which is what the generic arm's 724
#:                          zeros have drawn every time anyone pressed Report.
#: The Accepted Mission renderer 0x611CC49D names the same two fields (25:6..12
#: "Progress: ..."): state 1/2 Ordered; 3/4 In progress, or Conditions met
#: (result 2) / Operation failed (3); 5 Operation complete, or Discarded (1) /
#: Operation failed (3); 6 Expired. For 3/4 it draws "End time" = +0x70 + +0x34
#: through polcore's time_t decompose ([0x613AE380]+0xAD0, the same call the
#: paybook's date goes through); for 1/2 "Time limit: %u min" = +0x34 / 60.
#: WARNING: +0x70 as a time_t is read off that shared decompose, NOT seen on a screen.
MSG_MISSION_REPORT = 0x01C6            # lobby+0x7464, 36 B, body+0x00 = id
MSG_MISSION_REPORT_REPLY = 0x018F      # parse 0x61172500: body+0x20, 692 B
MSG_MISSION_CANCEL = 0x0196            # lobby+0x749A, 36 B, body+0x00 = id
MSG_MISSION_CANCEL_REPLY = 0x0197      # parse = stub
REPLY_018F_LEN = 0x20 + 0x2B4          # 724 = LOBAPI[0x01C6][1]
MR_LIMIT = 0x034                       # u32 seconds ("Time limit: %u min")
MR_START = 0x070                       # u32 time_t  ("End time" = +0x70 + +0x34)
MR_STATE = 0x118                       # u32, the poll's G+0x5A58
MR_RESULT = 0x234                      # u32, the poll's G+0x5B74
#: stored status -> (+0x118 state, +0x234 result), per 0x611CC49D. "open" is
#: what every accept recorded before this existed reads as.
MISSION_STATE = {
    "open": (4, 0),         # 25:9  Progress: In progress
    "met": (4, 2),          # 25:7  Progress: Conditions met
    "complete": (5, 2),     # 25:11 Progress: Operation complete
    "failed": (5, 3),       # 25:8  Progress: Operation failed
    "cancelled": (5, 1),    # 25:10 Progress: Discarded
    "expired": (6, 0),      # 25:12 Progress: Expired
}
#: a mission in one of these still blocks a second accept (27:0 / -4)
MISSION_ACTIVE = ("open", "met")
#: paybook line kind 5 = 11:x "Mission bonus" (0x61191420)
PAY_MISSION = 5

#: KEY: FMO_MISSION_REPORT -- answer REPORT (0x01C6) and CANCEL (0x0196) from the
#: pilot's stored accepts, apply the deadline, give the Accepted Mission rows
#: their state / result / time fields, and settle open BATTLE-MAP missions on a
#: battle end (a win inside the deadline = "met", a loss = "failed" -- SE's
#: rule, AUDIT-fmo-wikipedia-2026-09-12 §D3). A met mission's report moves it
#: to "complete" and OWES its reward (the row's +0x1EC H$ / +0x1E8 MP,
#: snapshotted at accept), which the Personnel Officer's paybook pays as its
#: own "Mission bonus" line -- 28:4's own promise.
#: WARNING: DEFAULT OFF: every byte on the wire stays what it was (724 zeros, an
#: empty 0x0197, id+name-only rows) until this is armed on purpose.
#: WARNING: The battle binding is a COINCIDENCE binding: ANY battle end the pilot
#: fights inside the window settles every open battle-map mission they hold.
#: Making a sortie the mission's own sortie is the map selector's three flags
#: (PLAN step 3), which nothing serves yet.
MISSION_REPORT = (os.environ.get("FMO_MISSION_REPORT", "").strip()
                  or "0") != "0"
#: FMO_MISSION_DEADLINE -- seconds from accept until an unmet mission expires.
#: SE: 30 minutes. 0 = no deadline. (Not FMO_MISSION_TIME: that one is the
#: BATTLE's time limit, a different clock.)
MISSION_DEADLINE = int(os.environ.get("FMO_MISSION_DEADLINE", "").strip()
                       or "1800", 0)
