"""Battle groups on the lobby side: create, join and comment (0x0156, 0x0157 -> 0x0158)."""
import os
import struct
import time
from .knobs import _env_int
from .wirelog import log


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


# --------------------------------------------------------------------------- #
# SE'S PLATOON RULES (2026-09-30): the create form, the B.G.Bonus, the battle
# count and auto-disband. Sources: the Scramble Board tutorial AH/F98/D92
# 195-237, update 050906, systext groups 2/5/7/8/9.
# --------------------------------------------------------------------------- #
#: KEY: THE CREATE FORM, DECODED (static 2026-09-30). The 0x0156 sender
#: (0x611821CD -> ctor 0x61180960) fills the body from the form window's
#: controls; payload = obj+0x48 (the 0x14 header sits at obj+0x34):
#:   +0x04  u32  0x611E3B00()                       (a client id)
#:   +0x08       leader "[squadron]" or name text   (obj+0x50)
#:   +0x58       comment                            (obj+0xA0)
#:   +0x10C u32  [[form+0x68]+0x8F] = the numeric box labelled 9:42
#:               " B.G. Bonus :  H$" (created only when [lobby+0x7E05] != 0,
#:               its limit read from the wallet at lobby+0x88C), else 0
#:   +0x110 u32  0x6122EA20(0) = sqVOICEInit()'s result (1 = voice up).
#:               WARNING: NOT Total Battles, which is what session.py has
#:               stored as `total_battles` since 09-05. The live `01` there
#:               was the voice flag.
#:   +0x114 u8   [[form+0x6C]+0x99] = the combo labelled 9:37 "Total
#:               Battles", items 1..5 (0x611830B6 loop, value = the number),
#:               or one item of value 0 when 0x610027E0 (arena) is true
#:   +0x115 u8   [[form+0x70]+0x99] = the combo labelled 9:41 "Required
#:               B.G.Cost", items 1..N with N = 0x61174B00() = the leader's
#:               OWN B.G.Cost (D92 227: "you can only set a Required
#:               B.G.Cost below your own")
#: The live create (2026-09-05) sent `03 01` at +0x114: 3 battles, cost 1.
CREATE_BONUS, CREATE_VOICE = 0x10C, 0x110
CREATE_TOTAL, CREATE_REQUIRED = 0x114, 0x115


def parse_create_form(body):
    """The 0x0156 form's numbers by name; short bodies read 0."""
    b = bytes(body) + bytes(max(0, CREATE_REQUIRED + 1 - len(body)))
    return {"bonus": struct.unpack_from("<I", b, CREATE_BONUS)[0],
            "voice": struct.unpack_from("<I", b, CREATE_VOICE)[0],
            "total": b[CREATE_TOTAL], "required": b[CREATE_REQUIRED]}


#: KEY: THE CLIENT'S B.G.COST (static, re-read 2026-09-30). 0x61174B00 builds
#: the SELECTED setup (lobby+0x3DF2) through 0x610A16B0 -> 0x610A12E0 and
#: returns obj+0x24 = 0x611AB620's result. 0x611AB620 walks part slots 0..3
#: (table 0x61396C2C) into list P and 4..10 (0x61396C10) into list W, taking
#: the level byte +1 of each part whose +0 & 0xF0 is set; both lists are
#: zero-terminated. Then 0x611E1F20 = 0x611E1E10(P, W, &lvl, 75):
#:   m = max(maxP, maxW); lvl = sum over P of max(p, m*75/100), /4 (toward
#:   zero), + max(maxW, maxP*75/100), capped at 200;
#:   cost = 0x61344268[i] for the first step 12, 0x61344214[..] = 24, 34,
#:   44, ..., 194, 200 that lvl does not exceed: <=12:1, 24:2, 34:3, 44:4,
#:   54:5, 64:6, 74:7, 84:9, 94:10, 104:12, 114:14, 124:15, 134:16, 144:17,
#:   154:18, 164:19, 174:20, 184:22, 194:25, 200:25.
#: bg_cost_from_levels is that function.
BG_COST_STEPS = (12, 24, 34, 44, 54, 64, 74, 84, 94, 104, 114, 124, 134, 144,
                 154, 164, 174, 184, 194, 200)
BG_COST_VALUES = (1, 2, 3, 4, 5, 6, 7, 9, 10, 12, 14, 15, 16, 17, 18, 19, 20,
                  22, 25, 25)


def _until_zero(xs):
    out = []
    for x in xs:
        if not x:
            break
        out.append(x)
    return out


def bg_cost_from_levels(parts, weapons):
    """The client's B.G.Cost for a setup whose present parts have these level
    bytes (0x611E1E10 with 75, then the step table). Levels are u8, so the
    client's signed divisions are plain floor divisions here. A 0 ends a
    list, as the client's zero-terminated walk does. Pure."""
    parts, weapons = _until_zero(parts), _until_zero(weapons)
    mp, mw = max(parts, default=0), max(weapons, default=0)
    thr = max(mp, mw) * 75 // 100
    total = sum(max(p, thr) for p in parts)
    lvl = min(200, total // 4 + max(mw, mp * 75 // 100))
    for step, cost in zip(BG_COST_STEPS, BG_COST_VALUES):
        if lvl <= step:
            return cost
    return BG_COST_VALUES[-1]


#: KEY: WHERE THE LEVELS COME FROM. Each part loader (0x611A4D90 body 0x11,
#: 0x611A4F10 0x21, 0x611A5030 0x31, 0x611A3DA0 0x41, 0x611AE380 every x2
#: weapon kind) copies byte +1 of the part's 48-byte MASTER RECORD
#: (0x611A4530(table, id), Data/AG/F21/D97.DAT) into part+1, the byte
#: 0x611AB620 reads. fmodata/fmo-part-levels.tsv is that byte for every
#: (kind, id), written by tools/fmodatagen/fmopartlevels.py from the client.
_FMODATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata")
PART_LEVELS_TSV = os.path.join(_FMODATA, "fmo-part-levels.tsv")
MISSIONS_TSV = os.path.join(_FMODATA, "fmo-missions.tsv")


def load_part_levels(path=PART_LEVELS_TSV):
    """{(kind, id): level} from fmo-part-levels.tsv; {} when it is absent."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            f.readline()
            for line in f:
                c = line.rstrip("\r\n").split("\t")
                if len(c) >= 3:
                    out[(int(c[0], 0), int(c[1]))] = int(c[2])
    except (OSError, ValueError):
        return {}
    return out


PART_LEVELS = load_part_levels()
#: FMO_BG_COST=1 (default): compute each pilot's B.G.Cost from its stored
#: setup and enforce the rules that read it (join 5:37, sortie 3:5 / 3:8 /
#: 3:11). 0 = every cost is unknown and those rules are skipped, as before
#: 2026-09-30.
BG_COST = _env_int("FMO_BG_COST", "1") != 0
#: 0x610A12E0 walks setup items 0..10 and puts item i in part slot
#: 0x6139397C[i]; this is that table read the other way (slot -> item).
#: Slots 0..3 feed list P, 4..10 list W (0x611AB620).
SLOT_ITEM = (1, 0, 3, 2, 5, 4, 7, 6, 8, 9, 10)
P_SLOTS, W_SLOTS = range(0, 4), range(4, 11)


def setup_bg_cost(block, setup_no=1, owned=None, levels=None):
    """The client's B.G.Cost for setup `setup_no` (1-based) of a 0x0166-shaped
    garage block, or None when a part's level is unknown. Each equipped
    record is resolved by its 64-bit serial against `owned` ({serial: (id,
    kind)}, the 0x0133 list), as 0x610A12E0 does with 0x61175170; a serial
    the list lacks loads no part and adds no level. Pure."""
    levels = PART_LEVELS if levels is None else levels
    if not levels or not 1 <= setup_no <= inventory.SETUP_SLOTS:
        return None
    base = (setup_no - 1) * inventory.SETUP_ENTRY_LEN
    if len(block) < base + inventory.SETUP_ENTRY_LEN:
        return None
    lv = {}
    for slot, item in enumerate(SLOT_ITEM):
        off = base + inventory.SETUP_ITEM_OFF + item * inventory.INV_ENTRY_LEN
        serial = struct.unpack_from("<Q", block, off)[0]
        if owned is None:
            iid = struct.unpack_from("<H", block, off + inventory.ITEM_ID)[0]
            kind = block[off + inventory.ITEM_KIND]
            got = (iid, kind) if serial else None
        else:
            got = owned.get(serial) if serial else None
        if not got or not (got[1] & 0xF0):
            continue
        level = levels.get((got[1], got[0]))
        if level is None:
            return None
        lv[slot] = level
    return bg_cost_from_levels([lv[s] for s in P_SLOTS if s in lv],
                               [lv[s] for s in W_SLOTS if s in lv])


def pilot_bg_cost(char, setup_no=None):
    """A stored pilot's B.G.Cost: its garage block's active setup, resolved
    against the same owned list 0x0132 serves (the setups' records first,
    then the acquired items; first serial wins). None = unknown (no block,
    no level table, FMO_BG_COST=0). The active setup is the one 0x014A names
    (FMO_ACTIVE_SETUP, default 1), the byte the client copies to
    lobby+0x3DF2; an empty one falls back to the first setup in use."""
    if not BG_COST or not char:
        return None
    try:
        block = bytes.fromhex(char.get("setups") or "")
    except ValueError:
        return None
    if len(block) < inventory.SETUP_TAIL_OFF:
        return None
    owned = {}
    for rec in inventory.merge_inventory(inventory.inventory_from_setups(block),
                                         inventory.stored_item_records(char)):
        owned.setdefault(struct.unpack_from("<Q", rec, 0)[0],
                         (struct.unpack_from("<H", rec, inventory.ITEM_ID)[0],
                          rec[inventory.ITEM_KIND]))
    from . import status          # here: a module-level import is a cycle
    n = status.STATUS_ACTIVE_SETUP if setup_no is None else setup_no
    used = [i + 1 for i in range(inventory.SETUP_SLOTS)
            if block[i * inventory.SETUP_ENTRY_LEN + inventory.SETUP_IN_USE]]
    if n not in used and used:
        n = used[0]
    return setup_bg_cost(block, n or 1, owned)


#: {account: B.G.Cost} of the pilot each account is PLAYING, noted by its own
#: session (the join, the create, the sortie). Another member's cost is read
#: here first; without an entry, the account's first named pilot (the same
#: pick the Player List rows make, groupchannel.group_member_row).
PILOT_BG_COST = {}


def note_pilot_cost(account, char):
    """Record `account`'s playing pilot's B.G.Cost; returns it (or None)."""
    cost = pilot_bg_cost(char)
    if account:
        if cost is None:
            PILOT_BG_COST.pop(account, None)
        else:
            PILOT_BG_COST[account] = cost
    return cost


def account_bg_cost(account):
    """`account`'s B.G.Cost, or None when unknown."""
    if not BG_COST or not account:
        return None
    if account in PILOT_BG_COST:
        return PILOT_BG_COST[account]
    try:
        roster = charstore.load_roster(account)
    except Exception:
        return None
    c = next((c for c in roster or () if c.get("first")), None)
    return pilot_bg_cost(c)


def group_total_cost(gid, members=None):
    """The platoon's Total B.G.Cost: the sum of its live members' costs
    (AI/F00/D08 103), or None when any member's is unknown."""
    if members is None:
        members = [a for a in groupchannel.GROUP_MEMBERS.get(gid, [])
                   if groupchannel.group_member_live(a)]
    total = 0
    for a in members:
        c = account_bg_cost(a)
        if c is None:
            return None
        total += c
    return total


#: KEY: THE SORTIE'S B.G.COST RULES (AI/F00/D08 102-104): "The sector B.G.Cost
#: and sector minimum B.G.Cost change with the sector's supply rate. If the
#: platoon's Total B.G.Cost is above the sector B.G.Cost, or a member's own
#: B.G.Cost is below the sector minimum, it cannot sortie to that sector."
#: The per-mission minimums (AI/F00/D94 228-243, 'the sector minimum
#: B.G.Cost of the destination is N') are the catalogue's 'BGコスト：N', the
#: bg_cost column of fmo-missions.tsv. Codes from the table at 0x613955F0
#: (the sortie failure arm 0x611818EE draws 10:6 with the code's line):
SORTIE_CODE_TOTAL = -31102       # 3:5  "Your Total B.G.Cost is too high to sortie."
SORTIE_CODE_SECTOR_MIN = -31108  # 3:8  "You are below the sector's minimum B.G.Cost"
SORTIE_CODE_REQUIRED = -31111    # 3:11 "You are below the Required B.G.Cost ..."


def cost_sortie_verdict(cost, total=None, required=0, sector_max=0,
                        sector_min=0, mission_min=0):
    """None when a pilot of B.G.Cost `cost` (its platoon totalling `total`)
    may sortie, else (code, why). A limit of 0 and a cost of None are
    unknown and skip their rule. Pure."""
    if cost is not None and required and cost < required:
        return SORTIE_CODE_REQUIRED, (f"B.G.Cost {cost} is below the battle "
                                      f"group's Required B.G.Cost {required} (3:11)")
    if total is not None and sector_max and total > sector_max:
        return SORTIE_CODE_TOTAL, (f"Total B.G.Cost {total} is above the sector "
                                   f"B.G.Cost {sector_max} (AI/F00/D08 103, 3:5)")
    floor = max(sector_min or 0, mission_min or 0)
    if cost is not None and floor and cost < floor:
        return SORTIE_CODE_SECTOR_MIN, (f"B.G.Cost {cost} is below the sector "
                                        f"minimum B.G.Cost {floor} (AI/F00/D08 "
                                        f"104 / D94 228-243, 3:8)")
    return None


def load_mission_costs(path=MISSIONS_TSV):
    """{(nation, title): minimum B.G.Cost} from fmo-missions.tsv's bg_cost
    column ('-' = none). {} when the file is absent."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            cols = f.readline().rstrip("\r\n").split("\t")
            for line in f:
                r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
                nat = {"O.C.U.": 1, "U.S.N.": 2}.get(r.get("faction"))
                v = (r.get("bg_cost") or "").strip()
                if nat and v.isdigit() and int(v) > 0:
                    out[(nat, r.get("title"))] = int(v)
    except OSError:
        return {}
    return out


MISSION_BG_COST = load_mission_costs()


def mission_min_cost(nation, missions, costs=None):
    """The minimum B.G.Cost a sortie that may complete one of `missions`
    must meet: the LOWEST of their catalogue minimums, so an ambiguous tile
    never refuses a pilot some open mission would take. 0 = none. Pure."""
    costs = MISSION_BG_COST if costs is None else costs
    vals = [costs.get((nation, m.get("title"))) for m in missions or ()]
    if not vals or any(v is None for v in vals):
        return 0
    return min(vals)


#: KEY: THE B.G.BONUS (小隊参加ボーナス), SE's rules (AH/F98/D92 234-237):
#:   234 "When forming a platoon you can set a platoon bonus paid after each
#:       battle."
#:   235 "After the battle the amount is split evenly among the members. If the
#:       bonus is more than the leader's money, the leader cannot sortie."
#:   236 "The bonus can be raised after forming, but not lowered."
#:   237 "Changing leader or disbanding clears it."
#: update 050906 line 84: the input cap went from H$10,000 to H$999,999.
#: 10:27 (the sortie confirm) "Platoon bonus: H$ %d / When you sortie, that
#: amount is deducted from your funds." and 2:101 / 2:107 are the refusals.
#: FMO_BG_BONUS=1 (default) runs these rules; 0 = the 0x01A0 is only
#: acknowledged and no money moves, as before 2026-09-30.
BG_BONUS = _env_int("FMO_BG_BONUS", "1") != 0
BG_BONUS_MAX = _env_int("FMO_BG_BONUS_MAX", "999999")
#: KEY: 0x01A0, DECODED (static 2026-09-30). Sender 0x6116D330, called only
#: from the group window at 0x6116FEAA with (window, [group conn+0xE0],
#: [[window+0x60]+0x8F]): payload+0x00 u32 = the GroupID, +0x04 u32 = the
#: amount typed into the 7:60 "Edit Battle Group Bonus" box (7:63 "B.G.Bonus:
#: H$"). 24 B, the rest zero. Its poll wants message 1; anything else shows
#: 7:64 "Failed to update the platoon bonus." with our code's text under it.
Q1A0_GID, Q1A0_AMOUNT = 0x00, 0x04
#: The refusal codes, from the client's code -> message table at 0x613955F0
#: (7-byte rows: s16 code, u8 error flag, u32 message id; walked by
#: 0x6116CE10, which 0x6116CF50 calls for every "[FMO%05d]" failure box):
CODE_BONUS_FUNDS = -14111        # 2:101 funds at or below the platoon bonus
CODE_BONUS_FUNDS_COST = -14120   # 2:107 funds at or below sortie cost + bonus
CODE_BONUS_ALLIES = -30025       # 2:99  bonus set: no joining a map with allies
#: No row names "the bonus cannot be lowered"; a code absent from the table
#: draws 5:32 "Other error." under 7:64, which is the honest thing to show.
CODE_BONUS_REFUSED = -1

#: {GroupID: {"total", "required", "bonus", "battles", "leader"}}: the
#: platoon's own numbers. Filled by register_group (from the create form);
#: group_state() builds one from the stored form for a group made before.
GROUP_STATE = {}


def register_group(gid, account, form):
    """Record a new group's form numbers (see parse_create_form)."""
    bonus = min(max(0, int(form.get("bonus") or 0)), max(0, BG_BONUS_MAX))
    GROUP_STATE[gid] = {"total": int(form.get("total") or 0),
                        "required": int(form.get("required") or 0),
                        "bonus": bonus if BG_BONUS else 0,
                        "battles": 0, "leader": account}
    return GROUP_STATE[gid]


def group_state(gid):
    """The platoon record for `gid`, built from the stored 0x0156 form (its
    +0x114 bytes, kept as `f114`) when register_group never ran."""
    st = GROUP_STATE.get(gid)
    if st is not None:
        return st
    made = next((g for g in BATTLE_GROUPS_MADE if g[1] == gid), None)
    if made is None:
        return None
    f114 = (BATTLE_GROUPS.get(made[0]) or {}).get("f114") or ""
    try:
        raw = bytes.fromhex(f114.replace(" ", ""))
    except ValueError:
        raw = b""
    raw += bytes(2)
    GROUP_STATE[gid] = {"total": raw[0], "required": raw[1], "bonus": 0,
                        "battles": 0,
                        "leader": GROUP_CREATOR_ACCOUNT.get(gid)}
    return GROUP_STATE[gid]


def group_leader(gid):
    st = GROUP_STATE.get(gid) or {}
    return st.get("leader") or GROUP_CREATOR_ACCOUNT.get(gid)


def bonus_request(gid, account, amount):
    """0x01A0 EDIT BATTLE GROUP BONUS: None = accepted (the new amount is
    stored), else (code, why). SE: only the leader, only upward (D92 236),
    at most BG_BONUS_MAX (update 050906). Whether the leader can COVER it is
    judged at the sortie (2:101), as SE's text says."""
    if not BG_BONUS:
        return None
    st = group_state(gid)
    if st is None:
        return CODE_BONUS_REFUSED, f"group {gid} does not exist on this server"
    if group_leader(gid) != account:
        return CODE_BONUS_REFUSED, (f"{account} is not the leader of group {gid} "
                                    f"(SE: the bonus is the leader's, D92 234)")
    amount = int(amount)
    if amount > BG_BONUS_MAX:
        return CODE_BONUS_REFUSED, (f"H$ {amount} is over the H$ {BG_BONUS_MAX} "
                                    f"cap (update 050906; FMO_BG_BONUS_MAX)")
    if amount < st["bonus"]:
        return CODE_BONUS_REFUSED, (f"H$ {amount} would LOWER the bonus from H$ "
                                    f"{st['bonus']}; SE: it can be raised, never "
                                    f"lowered (D92 236)")
    st["bonus"] = amount
    return None


def bonus_sortie_verdict(bonus, money, sortie_cost=0):
    """None when a leader holding `money` may sortie with `bonus` set, else
    (code, why). SE's wording is 'at or below' (2:101 / 2:107), so equal
    money refuses too. Pure."""
    if bonus <= 0:
        return None
    need = bonus + max(0, sortie_cost)
    if money > need:
        return None
    if sortie_cost > 0:
        return CODE_BONUS_FUNDS_COST, (f"funds H$ {money} are at or below the "
                                       f"sortie cost {sortie_cost} + platoon "
                                       f"bonus {bonus} (2:107)")
    return CODE_BONUS_FUNDS, (f"funds H$ {money} are at or below the platoon "
                              f"bonus H$ {bonus} (2:101)")


def bonus_share(amount, n, payer=False):
    """One participant's cut of a platoon bonus split evenly among `n`
    (D92 235); the payer also takes the remainder, so nothing is lost to
    rounding. Pure."""
    if amount <= 0 or n <= 0:
        return 0
    return amount // n + (amount % n if payer else 0)


#: KEY: AUTO-DISBAND (AH/F98/D92 210-211): "With Continuation set to
#: 『継続しません』 the platoon disbands automatically after the number of
#: battles set when it was formed. Set 『継続します』 if you do not want that."
#: The count is the create form's Total Battles (+0x114). The setting is 0x0173
#: +0x07 (1 = continue, 2 = do not continue; sender 0x6116CFF0, the two menu
#: arms at 0x6117197B / 0x611719DF), stored per account in
#: groupchannel.GROUP_READY. WARNING: which member's byte decides is inferred:
#: 7:36/7:37 read as a statement about the whole group, so the LEADER's is
#: used. 8:58 "The battle group will be automatically disbanded after the next
#: sortie" is 0x014C +0x0F4 -- a warning one battle AHEAD, so it is set on the
#: battle that leaves one to go, and the group is disbanded after the battle
#: that leaves none. FMO_GROUP_AUTO_DISBAND=0 counts but never disbands (the
#: pre-09-30 behaviour).
GROUP_AUTO_DISBAND = _env_int("FMO_GROUP_AUTO_DISBAND", "1") != 0
CONT_ON, CONT_OFF = 1, 2

#: {GroupID: the current group battle}: {"n", "left", "cont_off", "map", "at",
#: "joined" (accounts that sortied into it), "bonus", "payer", "payer_id",
#: "disbanded"}. One per group sortie; each participant's PLATOON_CTX points at
#: the same dict, so a disband cannot orphan a member not yet settled.
GROUP_BATTLE = {}
#: {account: {"gid", "battle"}} -- set when a member sorties with the group,
#: consumed once by that member's battle settlement.
PLATOON_CTX = {}


def group_battle_begin(gid, account, mapno, payer_id, now=None):
    """The leader's group sortie: count the battle and open its record (the
    bonus is copied in; the caller has taken the leader's money)."""
    st = group_state(gid) or register_group(gid, account, {})
    st["battles"] += 1
    cont = groupchannel.GROUP_READY.get(group_leader(gid) or account, (0, 0))[1]
    rec = {"n": st["battles"],
           "left": (st["total"] - st["battles"]) if st["total"] else None,
           "cont_off": cont == CONT_OFF, "map": mapno,
           "at": time.time() if now is None else now, "joined": [account],
           "bonus": st["bonus"] if BG_BONUS else 0, "payer": account,
           "payer_id": payer_id, "disbanded": False}
    GROUP_BATTLE[gid] = rec
    PLATOON_CTX[account] = {"gid": gid, "battle": rec}
    return rec


def group_battle_join(gid, account, mapno, window, now=None):
    """A member sortieing onto its group's current battle map joins that
    battle's record (its exp bonus and bonus share). None when there is no
    such battle, it is for another map, or it is older than `window` s."""
    rec = GROUP_BATTLE.get(gid)
    now = time.time() if now is None else now
    if (rec is None or rec["map"] != mapno or rec["disbanded"]
            or now - rec["at"] > window):
        return None
    if account not in rec["joined"]:
        rec["joined"].append(account)
    PLATOON_CTX[account] = {"gid": gid, "battle": rec}
    return rec


#: KEY: THE PLATOON EXP BONUS (systext 8:45 "経験値を入手しました（小隊ボーナス
#: %d%%）", 0x014C +0x0F2, printed on a WIN when > 100). SE never published the
#: rule; polnews 8696 only says fighting together adds a platoon bonus so you
#: gain exp faster. OURS, TO TUNE: +FMO_PLATOON_EXP_PER percent (default 10)
#: for each group member in the battle beyond the first, capped at
#: FMO_PLATOON_EXP_MAX percent (default 150). Paid on a win, the only time the
#: client says so. FMO_PLATOON_EXP_PER=0 = off (100%, +0x0F2 = 0 as before).
PLATOON_EXP_PER = _env_int("FMO_PLATOON_EXP_PER", "10")
PLATOON_EXP_MAX = _env_int("FMO_PLATOON_EXP_MAX", "150")


def platoon_exp_pct(n, per=None, cap=None):
    """The exp percent for a battle fought by `n` members of one group. Pure."""
    per = PLATOON_EXP_PER if per is None else per
    cap = PLATOON_EXP_MAX if cap is None else cap
    if per <= 0 or n <= 1:
        return 100
    return max(100, min(cap, 100 + per * (n - 1)))


def group_disband(gid, why):
    """Drop group `gid` everywhere this server keeps it: members, the board,
    its sortie, its record. Returns the accounts that were in it."""
    gone = list(groupchannel.GROUP_MEMBERS.pop(gid, []))
    for acct in gone:
        if groupchannel.GROUP_OF.get(acct) == gid:
            groupchannel.GROUP_OF.pop(acct, None)
        groupchannel.GROUP_READY.pop(acct, None)
    groupchannel.GROUP_SORTIE.pop(gid, None)
    BATTLE_GROUPS_MADE[:] = [g for g in BATTLE_GROUPS_MADE if g[1] != gid]
    GROUP_CREATOR_ACCOUNT.pop(gid, None)
    GROUP_STATE.pop(gid, None)
    rec = GROUP_BATTLE.get(gid)
    if rec is not None:
        rec["disbanded"] = True
    log(f"   GROUP {gid} DISBANDED ({why}): members {gone} released and the "
        f"Scramble Board row is gone")
    return gone


#: KEY: KICK (0x0171) AND CHANGE LEADER (0x0179), DECODED (static 2026-09-30).
#: Both senders (0x6116D0A0 / 0x6116D120) are called from the group window at
#: 0x61170D3E / 0x61170D8F with (window, [group conn+0xE0], [window+0x98]):
#:   +0x00 u32  the chosen member: [window+0x98], set from the member list's
#:              event 0x1003 / 0x1004, whose argument is that row's peer+0x10
#:              (0x6116F6F9) -- the UnitID this client knows the member by,
#:              i.e. the alias groupchannel.group_queue minted
#:   +0x04 u32  the GroupID ([group conn+0xE0])
#: Their polls want message 1; anything else shows 7:24 "Failed to kick the
#: member." (or the change-leader failure) with the code's line.
#: And [group conn+0xE0] is also what the 0x0178 push's +0x00 is matched
#: against (0x61175BF0), so a kicked member's notice carries the GroupID.
Q171_TARGET, Q171_GID = 0x00, 0x04
CODE_LEADER_ONLY = -15406        # 2:131 "Only the leader can change this."
CODE_NOT_MEMBER = -14058         # 2:54  "There is no player with that name."
#: FMO_GROUP_MEMBER_OPS=1 (default): Kick and Change Leader act. 0 = they are
#: acknowledged and change nothing, as before 2026-09-30.
GROUP_MEMBER_OPS = _env_int("FMO_GROUP_MEMBER_OPS", "1") != 0


def member_op_refusal(gid, account, target):
    """None when `account` may kick / hand the lead to `target` in group
    `gid`, else (code, why)."""
    if not gid or group_state(gid) is None:
        return CODE_NOT_MEMBER, f"group {gid} does not exist on this server"
    if group_leader(gid) != account:
        return CODE_LEADER_ONLY, f"{account} is not the leader of group {gid}"
    if not target or target == account:
        return CODE_NOT_MEMBER, "the chosen member could not be resolved"
    if target not in groupchannel.GROUP_MEMBERS.get(gid, []):
        return CODE_NOT_MEMBER, f"{target} is not a member of group {gid}"
    return None


def group_change_leader(gid, account, target):
    """0x0179 CHANGE LEADER: `target` leads `gid` from now on, and the
    B.G.Bonus is cleared (AH/F98/D92 237 "Changing leader or disbanding
    clears it"; manual p.47). Both members' blobs are re-sent (cmd 191) so
    the leader-only menu rows follow. None = done, else (code, why)."""
    no = member_op_refusal(gid, account, target)
    if no is not None:
        return no
    st = group_state(gid)
    st["leader"] = target
    st["bonus"] = 0
    GROUP_CREATOR_ACCOUNT[gid] = target
    n = groupchannel.group_push_flags(account) + groupchannel.group_push_flags(target)
    log(f"   GROUP {gid}: LEADER {account} -> {target}; B.G.Bonus cleared "
        f"(D92 237); cmd 191 queued on {n} group channel(s)")
    return None


def group_kick(gid, account, target):
    """0x0171 KICK: `target` leaves `gid`. It is taken off every other
    member's list (cmd 191 without the listed bit) and its client is told by
    0x0178 reason 2 (8:6 "You were kicked from the battle group."), which
    also closes its group connection. None = done, else (code, why)."""
    no = member_op_refusal(gid, account, target)
    if no is not None:
        return no
    told = groupchannel.group_delist(gid, target)
    groupchannel.group_leave(target)
    pushed = notify_removed(target, gid, GROUP_REASON_KICKED)
    log(f"   GROUP {gid}: {account} KICKED {target}; delisted on {told} "
        f"channel(s); 0x0178 {'queued' if pushed else 'NOT sent (no live session)'}")
    return None


GROUP_REASON_KICKED = 2
GROUP_REASON_AUTO_REMOVED = 5
#: KEY: THE SCRAMBLE BOARD'S SORTIE RULE (manual p.54): a member joins on
#: Standing By, and "if a sortie is launched while you are not set to Ready
#: to Sortie (for example while you have gone to the Hangar), you will be
#: kicked automatically." FMO_GROUP_SORTIE_KICK=1 (default): when the leader
#: sorties with the group, every other member whose last 0x0173 was not
#: Ready (+0x04 == 1) is removed and told by 0x0178 reason 5 (8:59 "You were
#: automatically removed from the battle group."). 0 = nobody is removed.
GROUP_SORTIE_KICK = _env_int("FMO_GROUP_SORTIE_KICK", "1") != 0


def auto_remove_unready(gid, leader):
    """The leader of `gid` sortied: remove the members not set Ready.
    Returns the accounts removed."""
    if not GROUP_SORTIE_KICK or not gid:
        return []
    gone = [a for a in list(groupchannel.GROUP_MEMBERS.get(gid, []))
            if a != leader and groupchannel.GROUP_READY.get(a, (0, 0))[0] != 1]
    for a in gone:
        groupchannel.group_delist(gid, a)
        groupchannel.group_leave(a)
        notify_removed(a, gid, GROUP_REASON_AUTO_REMOVED)
    if gone:
        log(f"   GROUP {gid}: the leader sortied; {gone} were not Ready and "
            f"were removed automatically (manual p.54, 8:59)")
    return gone


def notify_removed(account, gid, reason):
    """Queue a 0x0178 (pushes.group_ended_body) for `account`'s live session:
    +0x00 = the GroupID its group window holds, +0x04 = the reason. False
    when it has no live session to carry it."""
    s = trade.session_for_account(account)
    if s is None:
        return False
    s.queue_push(pushes.MSG_GROUP_ENDED, pushes.group_ended_body(gid, reason),
                 f"battle group {gid}: {pushes.GROUP_ENDED_REASONS[reason]}")
    return True


def platoon_settle(account, won, own_id=None):
    """This member's platoon result for the battle it just finished, once.
    {} when it did not sortie with its group. Else {"gid", "n" (members in
    the battle), "exp_pct", "share", "payer_id", "auto_disband",
    "disbanded"}; the group is disbanded after its last battle."""
    ctx = PLATOON_CTX.pop(account, None)
    if not ctx:
        return {}
    rec, gid = ctx["battle"], ctx["gid"]
    n = len(rec["joined"])
    payer = rec["payer"] == account
    share = bonus_share(rec["bonus"], n, payer=payer)
    pid = rec["payer_id"] or 0
    if payer and own_id is not None:
        pid = own_id              # == lobby+0x1DC -> 8:72 "refunded"
    elif own_id is not None and pid == own_id:
        pid = 0xFFFFFFFF          # two rosters' ids can match; 8:61 wants != self
    out = {"gid": gid, "n": n, "exp_pct": platoon_exp_pct(n) if won else 100,
           "share": share, "payer_id": pid,
           "auto_disband": bool(GROUP_AUTO_DISBAND and rec["cont_off"]
                                and rec["left"] == 1),
           "disbanded": False}
    if (GROUP_AUTO_DISBAND and rec["cont_off"] and rec["left"] is not None
            and rec["left"] <= 0 and not rec["disbanded"]):
        group_disband(gid, f"battle {rec['n']} of the {rec['n']} set at "
                           f"formation with Continuation = do not continue "
                           f"(AH/F98/D92 210)")
        out["disbanded"] = True
    return out


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, groupchannel, inventory, pushes, trade  # noqa: E402
