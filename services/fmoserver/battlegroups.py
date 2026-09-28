"""Battle groups on the lobby side: create, join and comment (0x0156, 0x0157 -> 0x0158)."""
import os
from .knobs import _env_int


#: KEY: 0x0156 -> 0x0158: the Scramble Board's CREATE BATTLE GROUP (see the handler).
MSG_0156_REQ = 0x0156
MSG_0158_REPLY = 0x0158
#: VERIFIED:KEY: 0x0157 -> 0x0158: JOIN AN EXISTING BATTLE GROUP (static 2026-09-09).
#: Sibling of CREATE, in the same three-class cluster off the Scramble Board:
#: ctor 0x61180AD0 stamps vtable 0x6133C0E0, poll 0x61180B50. Named by SE's own
#: systext at the consumer of its success event 0x10CF -- 9:1 "Joining the
#: battle group.", 9:2 "You already belong to a battle group."
#: Body is 12 B and `[obj+0x48]` (= payload+0x00) is filled from the board
#: window's +0x19A: the u32 **GroupID being joined**.
#:
#: WARNING: AND IT IS NOT ITS SIBLING. CREATE's success handler (0x61182391) READS the
#: reply body and does a LoginGroup from it; JOIN's poll posts 0x10CF having
#: read nothing but the id. A joiner is therefore not attached to any group
#: server by the reply itself -- that is what MSG_GROUP_ATTACH is for.
#:
#: WARNING: BUT THE BODY IS NOT UNREAD, AND THE FIRST CUT OF THIS WAS WRONG.
#: "the poll reads only the id, so an EMPTY 0x0158 is the whole contract" is a
#: statement about the POLLER, and the consumer is downstream of it. The board's
#: event handler runs on the same reply at 0x61181AB3:
#:
#:     mov edx, [ebx + 0x196]     ; the 0x0157 request object
#:     mov esi, [edx + 0x3acc]    ; the received FRAME
#:     mov al,  [esi + 0x14]      ; = payload+0x00
#:     je  0x61181d6d             ; ZERO -> [board+0x270] = 0
#:                                ; NON-ZERO -> [board+0x270] = [frame+0x24]
#:
#: and `[board+0x270]` is exactly what `0x611816F0` tests to raise systext 9:10
#: **"The battle group is on a sortie. Sortie to the battle map immediately?"**
#: An empty body leaves payload+0x00 reading whatever was already in the
#: client's receive buffer, so a JOIN on a standing-by group announced a sortie
#: and -- when the player said yes -- sent a real 0x0139 and put them in
#: map 418 (live 2026-09-09T20:14:03Z).
#:
#: The same handler then fills the "Battle Map Information" panel (its title is
#: systext 9:33) from payload+0x14 and payload+0x590, which is why every number
#: on that panel read zero in the live screenshot.
#:
#: KEY: SAME MISTAKE AS 0x0159, ONE ROUND LATER: check the poller, stop, miss the
#: consumer. There the caller tested the id one instruction after the call I
#: read; here the reader is a different function entirely.
#: (open the function, not just the address)
MSG_0157_REQ = 0x0157
#: The largest offset the consumer READS: `lea esi,[frame+0x5A4] / rep movsd
#: 0x36` ends at frame+0x67C, i.e. payload+0x668. A shorter body leaves the
#: panel reading the buffer's previous contents.
#: WARNING: The client also SCRIBBLES 1,792 bytes into its own receive buffer at
#: frame+0x8F as scratch (0x61181D49). That is inside its fixed 15,000-byte RX
#: region whatever we send, so it does not set the length -- the reads do.
REPLY_0158_JOIN_LEN = 0x668
#: payload+0x00: non-zero = "this group is on a sortie". We send 0.
S158_ON_SORTIE = 0x00
#: KEY: payload+0x10 IS THE SECTOR ID, and it is read two ways in the same block:
#:   0x61181AEF  `div 0xF4240`  -- /1,000,000, the remainder keys a list walk
#:   0x61181B6D  `div 0x3E8`    -- %1000; <= 10 titles the window with systext
#:                                 9:32 "Training Sector", otherwise it fills
#:                                 from [globals+0x194]+0x76BC instead.
#: So it is a COMPOSITE id, and its low three digits decide training-vs-real.
#: VERIFIED: Serving 0 is why the live screenshot said "Training Sector": 0 % 1000
#: is 0, which is <= 10. That is a match between what we send and what the
#: screen showed, i.e. a small confirmation this offset is read as decoded.
S158_SECTOR = 0x10
#: FMO_JOIN_SECTOR: the sector id a JOIN reply claims. 0 (default) = the
#: training sector, which is what the board already shows and the only value
#: with any evidence behind it. A real sector id is an EXPERIMENT: nothing here
#: knows which ids exist, and `warmap_sector_for` is the table that would say.
JOIN_SECTOR = _env_int("FMO_JOIN_SECTOR", "0")
ANSWER_0156 = os.environ.get("FMO_ANSWER_0156", "1").strip() or "1"
#: peer -> the last create request's fields (leader, comment, total_battles, ...).
#: A record, not yet a served group: the 0x01AD block / board list are next.
BATTLE_GROUPS = {}
#: every group handed out by a 0x0158: (peer, GroupID, leader, when). GroupIDs
#: count from 1 per process; they are what the client's FmoGroup logs in with.
BATTLE_GROUPS_MADE = []
#: {GroupID: account key} of the pilot who CREATED it -- the leader test by
#: ACCOUNT, since two pilots behind one router share the host that
#: BATTLE_GROUPS_MADE records (both read as leader, live 2026-09-27).
GROUP_CREATOR_ACCOUNT = {}
#: THE IN-GROUP OPERATIONS -- seven small classes, each {send, poll}, sent from
#: the battle-group window. All but one want a bare message 1 on their own seq.
#:
#: WARNING: CORRECTED 2026-09-09 (static). This block used to say "0x0171/0x0173/
#: 0x0179 = leader/kick/sortie-setting". **All three names were wrong, and they
#: were wrong as a ROTATION**, which is what made it look self-consistent. The
#: pairing here is not adjacency -- it is each sender's OWN vtable stamp
#: (`mov dword [esi], <vtable>`), and every name below is SE's own success AND
#: failure systext, which agree:
#:
#:   req     sender fn   vtable      poll fn     accepts  what SE calls it
#:   0x0173  0x6116CFF0  0x6133ABF0  0x6116DD60  1        CHANGE SORTIE SETTING
#:                                   7:16 "Setting applied." /
#:                                   7:17 "Failed to change the sortie setting."
#:   0x0171  0x6116D0A0  0x6133AC00  0x6116DE50  1        KICK A MEMBER
#:                                   7:23 "The member has been kicked." /
#:                                   7:24 "Failed to kick the member."
#:   0x0179  0x6116D120  0x6133AC10  0x6116DF40  1        CHANGE LEADER
#:                                   7:25 "The leader has been changed."
#:   0x0172  0x6116D1A0  0x6133AC20  0x6116E030  1        LEAVE THE BATTLE GROUP
#:                                   7:20 "You have left the battle group." /
#:                                   7:22 "An error occurred while leaving ..."
#:   0x0190  0x6116D220  0x6133AC30  0x6116E120  **0x0191**  GET THE GROUP COMMENT
#:                                   7:32 "Failed to get the battle group comment."
#:   0x0192  0x6116D2A0  0x6133AC40  0x6116E220  1        comment confirm
#:   0x01A0  0x6116D330  0x6133AC50  0x6116E310  1        platoon bonus
#:
#: WARNING: 0x0172 (LEAVE) WAS NEVER IN THIS TUPLE, so a player could not leave a
#: battle group: the dialog hung on our silence exactly like the 0x0190 hang
#: hit live on 2026-09-06.
#:
#: WARNING:KEY: AND 0x0190 IS NOT AN ACK -- IT WANTS 0x0191. Its poll 0x6116E164 is
#: `cmp word [eax+6], 0x191 / jne`: on a match it posts UI event 0x13F3 and
#: opens the comment window; on ANYTHING ELSE it posts 0x13F4 with word[frame+8]
#: as a numeric error code. So the message 1 we have been sending since 09-06
#: un-hung the dialog and then failed it, every single time -- the SAME shape as
#: Play Time (0x0182 answered with 1 for two and a half weeks). Fixing the hang
#: is not fixing the feature. (an empty search is not an absence)
#: The 0x13F3 arm builds its window from `[[0x613AE664]+0x198]+0x4C` -- the
#: client's OWN group record -- and never reads our reply's body, so an EMPTY
#: 0x0191 is the whole contract. WARNING: Whether the comment then has any TEXT in it
#: depends on that local record, which this server does not fill; untested.
GROUP_ACK_IDS = (0x0171, 0x0172, 0x0173, 0x0179, 0x0192, 0x01A0)
#: 0x0190 -> 0x0191, alone: see above. Empty body.
MSG_GROUP_COMMENT_REQ = 0x0190
MSG_GROUP_COMMENT_REPLY = 0x0191
#: Named for the log line only -- the operation each id actually is.
GROUP_OP_NAMES = {
    0x0171: "KICK A MEMBER", 0x0172: "LEAVE THE BATTLE GROUP",
    0x0173: "CHANGE SORTIE SETTING", 0x0179: "CHANGE LEADER",
    0x0190: "GET THE BATTLE GROUP COMMENT", 0x0192: "comment confirm",
    0x01A0: "platoon bonus",
}
