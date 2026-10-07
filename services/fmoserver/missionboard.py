"""Mission accept, report and cancel: message ids, refusal codes and the knobs that gate them."""
import os
from .knobs import _env_float, _env_int


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
#: (The "KNOWN HOLE" above is closed: accepts are stored, and a second accept
#: of an active one is refused with -4 before anything is charged.)

#: KEY: FMO_MISSION_FEE_MP -- THE FEE IS MP, NOT H$ (default 1; 0 = the old
#: money gate and money charge). SE, guide/addmanual (MP):
#: 「MPの導入により、ミッションを受理するための条件が「必要階級 + 必要金額」から
#: 「必要階級 + 必要MP」に変更されています」, and NPC p54: 「※必要階級/必要MPがないと
#: 受理できません。」 guide/mission names the column: 「Fee ： ミッションを受けるために
#: 必要なMP」, and step 4: 「Fee(MP)を支払ってそのミッションを受けたことになります」.
#: So +0x1DC is the Required MP the Mission Info panel prints (22:5), and a
#: pilot short of it is refused with -7 (27:7 "You do not have enough MP to
#: accept this mission"), the code SE's client already maps. The charge
#: itself still waits on FMO_MISSION_FEE (with FMO_MISSION_ACCEPT=gate).
#: Refunds (guide/mission): 「ミッションを受けた時に支払ったFee(MP)は、ミッション
#: をキャンセルしても払い戻されません」 -- never. The ORDER MP a derived mission
#: costs is refunded only while it is still 指令済 (ordered, not accepted);
#: our 0x0194 ORDER is logged and kept but issues nothing and its MP field is
#: unbound, so there is no Order MP to take or give back yet.
MISSION_FEE_MP = _env_int("FMO_MISSION_FEE_MP", 1) != 0

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

#: paybook line kind 3 = 11:3 "Mission participation bonus", the line 8:62
#: 「ミッション参加ボーナスを入手しました。」 announces at the battle end.
PAY_SHARE = 3
#: KEY: FMO_MISSION_SHARE -- SE's ミッション分配ボーナス (default 1; 0 = only the
#: accepting pilot is paid, the old behaviour). guide/mission, battle-map
#: missions: 「戦闘で勝利した場合は、帰還後、Intelligence.Officerに結果を報告する
#: ことで成功報酬(H$,MP)と貢献値を受け取ることができます。また、戦闘に参加した
#: バトルグループのメンバー全員に「ミッション分配ボーナス」が支払われます。」 and
#: 「ミッション分配ボーナスは...Personnel.Officerから受け取ることができます」. So
#: on the WIN that meets a battle-map mission, every member of the taker's
#: battle group (the taker too) is owed a paybook line. The MP is the row's
#: own Distribution (+0x1C8 bytes 0..2, 22:9, bound live 2026-09-12); the
#: panel's Distribution H$ field is UNBOUND, so the H$ is OURS, below.
MISSION_SHARE = _env_int("FMO_MISSION_SHARE", 1) != 0
#: OURS, to tune: the share's H$ per member as a percent of the row's reward
#: H$ (+0x1EC). SE: 「報酬や貢献値は、ミッションの種類や対象セクターのBGコストなど
#: によって変動します」 -- no figure was published.
MISSION_SHARE_HS_PCT = _env_float("FMO_MISSION_SHARE_HS_PCT", 10)

#: KEY: FMO_AREA_RESET_FAIL -- an AREA mission running when the frontline is
#: reset by a new phase FAILS (default 1; 0 = it keeps running). SE, news7740:
#: 「初期化に伴い、停戦時に実行中のエリアミッションはすべて自動的に失敗となります」.
#: The moment is warstate.frontline_reset_at(); see mission_status().
AREA_RESET_FAIL = _env_int("FMO_AREA_RESET_FAIL", 1) != 0

#: KEY: FMO_MISSION_CONTRIB -- the CONTRIBUTION a mission pays on its report,
#: `<category>:<points>,...` (1 battle map / 2 sector / 3 area). Empty (the
#: code default) = none, the old behaviour. The manual says a mission's reward
#: is money, MP AND contribution (Playing Manual p.60 「MPはミッションの報酬として
#: ...貢献値と共に得られます」), and guide/mission 102: 「Report ： 進行状況が
#: 「条件達成」の場合、報酬と貢献値が与えられます」 -- banked at the REPORT, not
#: owed at the Personnel Officer (the paybook has no contribution column).
#: SE never published amounts (guide/mission 104: they vary with the mission
#: type and the sector's BG cost), so the release figures are OURS.
MISSION_CONTRIB = os.environ.get("FMO_MISSION_CONTRIB", "").strip()
#: FMO_MISSION_CONTRIB_SECTOR_X -- the multiplier on a SECTOR mission's
#: contribution. SE, update 050628gp4sc1:92: 「セクターミッション全般について、
#: 成功時の報酬貢献値を従来の2倍に引き上げました」 -- every sector mission, derived
#: ones included, pays twice the contribution it used to. Default 2.
MISSION_CONTRIB_SECTOR_X = _env_int("FMO_MISSION_CONTRIB_SECTOR_X", 2)

#: KEY: FMO_MISSION_PLACE -- WHERE each list may be accepted (code default 0 =
#: anywhere, the old behaviour; the release sets 1). Playing Manual p.59/60:
#: Battle Map and Sector missions come from the Intelligence Officer "in an
#: Occupied Zone or Contested Zone", Area missions only from the Senior Officer
#: in the Strategy Room (guide/mission 14: 「戦略室のSenior.Officerから受ける
#: ことができます」). With 1:
#:   * a category-3 accept is refused unless the pilot stands in a Briefing
#:     Room (move.WORLD_PLACES kind 2), the only place the Senior Officer is;
#:   * a category-1/2 accept is refused in a Controlled Zone (zone kinds 1/3)
#:     and in the Coliseum (kind 6), where SE had no mission counter.
#: The refusal code is MISSION_PLACE_CODE below.
MISSION_PLACE = _env_int("FMO_MISSION_PLACE", 0) != 0
#: KEY: 0 = 27:4 "The operation failed." -- chosen because NONE of SE's
#: specific accept sentences is about the place: 2 says the rank is too low
#: (a Major in the wrong room would read that as a lie), -9 / -8 are about the
#: picked target sector, and the < -500 arm (27:12 "...can only target sectors
#: in warzone %u") is only half transcribed and is about the TARGET too. The
#: client lists the Area rows anywhere, so this is the generic failure, which
#: is what SE's own client draws for every code it has no sentence for.
MISSION_PLACE_CODE = 0
#: Zone kinds (zone // 100) whose lobbies have no Intelligence Officer
#: mission counter: 1 / 3 Controlled Zones (O.C.U. / U.S.N.), 6 Coliseum.
MISSION_PLACE_NO_COUNTER = (1, 3, 6)

#: KEY: FMO_SECTOR_OWN_WINS -- what meets a SECTOR mission (code default 0 =
#: any win by the pilot's nation on the tile, the old behaviour; the release
#: sets 1). Playing Manual p.60: 「セクターミッションを受理したPCが他のPCにミッション
#: をオーダーし、他のPCがオーダーされたミッションを遂行し戦闘勝利を積み上げることで
#: 達成されるミッション」 -- the wins that count are the ones in the missions the
#: taker ORDERED from it. With 1, a sector accept is met once `needed` of its
#: own derived battle-map missions were won (the taker's accept of the order
#: reached met / complete inside the sector mission's deadline).
#: WARNING: on a quiet server that is much harder: the taker cannot take their
#: own order (-5), so a sector mission needs a second pilot. Set 0 to go back.
SECTOR_OWN_WINS = _env_int("FMO_SECTOR_OWN_WINS", 0) != 0


# --------------------------------------------------------------------------- #
# ORDERED (DERIVED) MISSIONS -- 0x0194 ORDER -> 0x0195, cancel 0x0196
# --------------------------------------------------------------------------- #
#: KEY: SE's rule (guide/mission, "派生ミッションを発行する場合"): a pilot who has
#: accepted a SECTOR mission may ORDER battle-map missions from it, one who
#: has accepted an AREA mission may order sector missions. The order is
#: placed from the Accepted Mission window (sub-menu "Order"), costs ORDER MP
#: (オーダーMP), and the derived mission's reward MP is "calculated from the
#: Order MP" and paid by the army; its H$ moves with the reward MP. Fee,
#: distribution, contribution, operation time and operation sector cannot be
#: changed (the sector is the source mission's). Cancel from the Ordered
#: Mission window REFUNDS the Order MP, and only a mission still 指令済
#: (Ordered, nobody has taken it) can be cancelled. The Fee a pilot paid to
#: ACCEPT a mission is never refunded (missionboard.MISSION_FEE_MP above).
#:
#: THE WIRE (static 2026-09-30, re-read with fmodis): the Order dialog
#: (handler 0x611CCBD0, case 1) calls the filler 0x611C4CF0 with
#: (row 0 = [0x613C15FC]+8, dialog+0x326 = the 524-B TEMPLATE, +0x53B base
#: Order MP, +0xEF the CHOSEN Order MP, +0x50A operation time, the comment,
#: +0x543 target sector, +0x537 percent) and the body lands at G+0x5BF8:
#:   +0x000 template id   +0x004 row 0 +0x00 (the source accept's key)
#:   +0x00C sector        +0x010 "%s.%s" issuer name   +0x034 row 0 +0x23C
#:   +0x038 op time (s)   +0x03C comment (255)
#:   +0x13C base MP       +0x140 CHOSEN MP             +0x144 percent
#: The dialog's init (0x611CA301..) copies 0x83 dwords of the template and
#: reads template +0x200 as the base Order MP (dialog+0x526 -> +0x53B) and
#: template +0x1F4 != 0 as "an MP reward"; the chosen MP starts at
#: base x percent / 100 and the slider runs from half to one and a half of it.
#: The templates are served by the SECOND server: job kind 5 (0x611AF670, op
#: 0xE, body {u32, u32 count, u32 ids}) built by 0x611C9850 from table
#: +0x10..+0x1C = row 0 +0x08..+0x14, answered with op 0x21 pages (0x20C
#: slots) whose callback 0x611C9160 copies each 524-B record and tolerates
#: the END's NULL (0x611C918D). The 0x0195 reply is stub-parsed; any reply
#: draws 21:27 "This mission has been ordered." (poll 0x611CD170). A
#: MESSAGE-2 reply takes that poll's failure arm: code -7 -> 27:10,
#: -4 -> 27:9, -3 -> 27:1, anything else 27:4 (0x611CD26F).
#: WARNING: the words of 27:9 / 27:10 / 27:1 are not transcribed here; -7 is
#: used for "not enough MP" by analogy with the accept's 27:7 (UNREAD).
MSG_ORDER_REPLY = 0x0195
ORDER_CODE_MP = -7
ORDER_CODE_FAIL = 0
#: FMO_ORDER=1 (default): serve the templates, take the Order MP, list the
#: derived mission for other pilots and refund it on cancel / expiry.
#: 0 = the old logged-and-kept stub.
ORDER = _env_int("FMO_ORDER", "1") != 0
#: Derived mission ids: 0xC000 + n, under 16 bits so mission_key's
#: per-accept keys still work. OURS.
ORDER_ID_BASE, ORDER_ID_END = 0xC000, 0x10000
#: Template ids for the kind-5 query: 0x7E000000 | derived category << 16 |
#: the source mission id. OURS (the client only round-trips it).
ORDER_TPL_BASE = 0x7E000000
#: Accepted Mission row offsets the Order path reads (0x611C9850 and the
#: dialog's percent argument): +0x08 the first template id, +0x38 percent.
ROW_ORDER_TPL, ROW_ORDER_PCT = 0x08, 0x38
#: Derived rows: +0x44 the commander's name, +0x74 the assignee's (the 692-B
#: record; strings, cut at 0x20). States 1/2 draw 25:6 "Ordered" (0x611CC49D).
ROW_COMMANDER, ROW_ASSIGNEE, ROW_NAME_MAX = 0x44, 0x74, 0x20
MISSION_STATE["ordered"] = (1, 0)     # 25:6  Progress: Ordered
MISSION_STATE["taken"] = (3, 0)       # 25:9  Progress: In progress
#: FMO_ORDER_BASE_MP: the template's base Order MP (+0x200). OURS.
ORDER_BASE_MP = _env_int("FMO_ORDER_BASE_MP", "20")
#: FMO_ORDER_PCT: the percent row +0x38 hands the dialog. OURS (100 = the
#: base as it is).
ORDER_PCT = _env_int("FMO_ORDER_PCT", "100")
#: FMO_ORDER_REWARD_PCT: the template's reward MP / H$ as a percent of the
#: source mission's reward, at the base Order MP. OURS.
ORDER_REWARD_PCT = _env_int("FMO_ORDER_REWARD_PCT", "25")
#: FMO_ORDER_CAP: live orders one accepted mission may have at once (SE's
#: 同時発行可能数 exists, its value was never published). OURS.
ORDER_CAP = _env_int("FMO_ORDER_CAP", "4")
