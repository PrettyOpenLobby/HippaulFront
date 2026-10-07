"""The pilot's hangar: its place, its bays and the wanzers parked in them."""
import os
from .deps import fmolayout
from .knobs import _env_int


def hangar_request(payload):
    """(password, first, last) from a 0x016D the HANGAR menu sent: +0x08 is the
    numeric password as a decimal string, +0x18/+0x29 the owner's names
    (empty for My Hangar). Pure."""
    if len(payload) < move.MOVE_REQ_LEN:
        return "", "", ""
    num = payload[move.M16D_NUMBER:move.M16D_NUMBER + 16].split(b"\0")[0].decode("ascii", "replace")
    a = payload[move.M16D_NAME_A:move.M16D_NAME_A + move.MOVE_NAME_LEN].split(b"\0")[0].decode("cp932", "replace")
    b = payload[move.M16D_NAME_B:move.M16D_NAME_B + move.MOVE_NAME_LEN].split(b"\0")[0].decode("cp932", "replace")
    return num, a, b


def hangar_place(owner_id):
    """A pilot's hangar is one place for everyone: zone 0 (hangars are not in
    an area), kind 5, instance = the owner's character id."""
    return (0, 5, int(owner_id))


#: VERIFIED:KEY: THE HANGAR IS AN OWNERSHIP CHECK (static 2026-09-11, live report: "both
#: pilot lockers immediately close dialogue"). The SETUP.CONSOLE / PILOT.LOCKER
#: rows run AH/F98/D63 `tag_wapsetup` / `tag_pltsetup`, whose natives are
#: E307 (0x610FB270 -> scene 8, wanzer setup) and E308 (0x610FB2E0 -> scene 9,
#: pilot setup). Both open ONLY when
#:     globals+0x1AC  ==  [[0x613CA470]+0x48]+0x10      (the self peer's UnitID)
#: and 0x61002BC0 passes; 0x61002BC0, when globals+0x1AC == globals+0x1BC (the
#: client's own id), demands that the entity map hold 0x82080D00 (the locker),
#: 0x82080C00 AND 0x82080C01 (both setup consoles), and a unit 0x82080000+i
#: for every IN-USE setup i (0x611747D0 = the setup's +0x01 byte, the same
#: 8 x 544 array this server stores and serves in 0x0166). So SE's grant put
#: the hangar OWNER'S id in 0x0153 +0x18 (globals+0x1AC), not a kind; the
#: owner could use their own consoles and a visitor could not; and SE's server
#: popped the owner's wanzers into the bays. We sent 5 ("Hangar" in the HUD's
#: kind-name enum), which fails the first compare -- the dialogue just closes.
#: WARNING: Our character ids are small (1 = your first pilot) where SE's were not, so
#: the HUD's kind-name switch (>4 = Hangar) reads an owner id of 1 as "Room".
#: A visitor whose own id equals the owner's would pass as the owner; they get
#: 5 instead. FMO_HANGAR_OWNER_F18=0 restores the flat 5.
HANGAR_OWNER_F18 = (os.environ.get("FMO_HANGAR_OWNER_F18", "").strip() or "1") != "0"
#: host -> {"owner", "slots", "mapno"}: the in-use wanzer setups of the pilot
#: now standing in their OWN hangar, recorded at the grant (the TCP session
#: owns the garage block; the UDP channel pops the bay units from this).
HANGAR_RESIDENTS = {}
#: the bay prop the four hangar maps repeat on a 20 m pitch (141 = 2 bays ..
#: 144 = 5), from the shipped floor plans: 5.4 x 3.2 x 9.5 m
HANGAR_BAY_SIZE = (5.4, 3.2, 9.5)
#: what a bay unit is popped as. The gate only asks that it EXISTS.
#: WARNING: NOT 30: a type-30 unit loads the model named by its KEY and is not
#: created at all when there is none (0x611EA980 returns 0 on a failed load),
#: and a wanzer is built from parts, not stored as one model -- so 0x82080000
#: as type 30 almost certainly never existed (live 18:07Z: all four units
#: sent, grant +0x18 = 1, consoles still shut). 4 always creates; it is parked
#: HANGAR_BAY_DEPTH metres under the bay so nobody sees a person there.
#: The faithful answer is SE's parked wanzer (a wanzer-class pop with the
#: setup's parts) -- a lobby-scene wanzer pop is untested, hence not default.
HANGAR_BAY_TYPE = _env_int("FMO_HANGAR_BAY_TYPE", "4")
HANGAR_BAY_DEPTH = float(os.environ.get("FMO_HANGAR_BAY_DEPTH", "").strip() or "0")
#: KEY: AND THE BAY UNIT'S BODY IS WHAT THE SETUP SCREEN SHOWS (static + live
#: 2026-09-11). The NPC arm 0x61100100 walks 0x82080000..07 (0x611009C6),
#: copies each found unit's whole 456-byte body (entity+0x147) -- POSITION
#: INCLUDED -- and creates a display unit 0x820C2000|n from it. Live: SETUP.
#: CONSOLE opened the wanzer setup scene and the wanzer sat "right up against
#: the camera" -- the copy of a unit we had parked 30 m under the floor. So
#: SE's bay unit stood ON the platform; depth is 0 now, and a hangar-band
#: layout row with the same key (the editor) overrides the computed spot.


def setups_in_use(block):
    """[i] for every wanzer setup whose +0x01 IN-USE byte is set (0x611747D0's
    own test). Pure."""
    return [i for i in range(inventory.SETUP_SLOTS)
            if len(block) >= (i + 1) * inventory.SETUP_ENTRY_LEN and block[i * inventory.SETUP_ENTRY_LEN + 1]]


def hangar_bays(mapno):
    """[(x, z)] bay centres for a hangar map, nearest the entrance (high z)
    first, from the shipped floor plan; [] when none ships. WARNING: Which setup SE
    parked in which bay is not known -- setup i takes the i-th bay."""
    if not fmolayout:
        return []
    plan = fmolayout.load_plan(mapno)
    if not plan:
        return []
    w, h, d = HANGAR_BAY_SIZE
    bays = {(round((b[0] + b[3]) / 2, 2), round((b[2] + b[5]) / 2, 2)) for b in plan["boxes"]
            if abs((b[3] - b[0]) - w) < 0.2 and abs((b[4] - b[1]) - h) < 0.3
            and abs((b[5] - b[2]) - d) < 0.2}
    return sorted(bays, key=lambda c: -c[1])


def hangar_resident_units(host_ip, place):
    """The bay units 0x82080000+i for the owner standing in their own hangar,
    as roster entries (uid, type, pos4, None); [] otherwise."""
    r = HANGAR_RESIDENTS.get(host_ip)
    if not r or place is None or place[1] != 5 or place[2] != r["owner"]:
        return []
    bays = hangar_bays(r["mapno"])
    pp = popsweep.POP_POS_MAP.get(r["mapno"])
    fy = pp[1] if pp else 0.0
    out = []
    for k, i in enumerate(r["slots"]):
        if k >= len(bays):
            break
        x, z = bays[k]
        y = fy - (HANGAR_BAY_DEPTH if HANGAR_BAY_TYPE == 4 else 0.0)
        out.append((0x82080000 + i, HANGAR_BAY_TYPE, (x, y, z, 0.0), None))
    return out


#: the entities 0x61002BC0 demands before the owner's consoles open
HANGAR_REQUIRED_KEYS = (0x82080D00, 0x82080C00, 0x82080C01)


#: KEY: HANGAR RANK (2026-09-30). SE, update 050628 lines 71-72:
#:     ハンガーランクとアイテム所持数が、規定のジョブレベルに達したジョブ数に応じて
#:     増加するようになりました。
#:     ※既に規定のジョブレベルに達したジョブがある方は、バージョンアップ後に一度
#:       戦闘を行った時点で変更が適用されます。
#: -- hangar rank and item capacity grow with the number of jobs at a set job
#: level, and the change lands after a battle. We served the byte as 0 in both
#: places the client reads it.
#:
#: THE BYTE is lobby+0x8BD = 0x014A payload+0x39 (block+0x31). The client never
#: uses it raw: 0x611E3F20 / 0x611E3F50 index a 16-row table at 0x61399988
#: (rank clamped to 15), {u16 wanzers, u16 item capacity}, read from the image:
#:     rank 0: 2/80   1: 2/100   2: 4/120   3: 4/140   4: 6/160   5: 6/180
#:     6: 6/200   7: 8/220   8: 8/240   9..15: 8/250
#: Rank 0 IS AI/F00/D94 record 3, 初期状態では、最大2つのヴァンツァーをセット
#: アップすることができます. The capacity is the shop gate's bar (0x611785D0:
#: item count lobby+0x10D5 >= cap refuses with -6, kind 0x14 exempt) and the
#: loot check at 0x6117EDEE (count > cap -> 2:104 "You are carrying more
#: items than the limit allows"); the wanzer count feeds the Select Wanzer
#: screen (0x611755D0 at 0x6109F966). All of it runs only while 0x611F1660()
#: returns 0, the header byte +4 of resource 0x14502 (AI/F32/D02.DAT), which
#: is 0 in both the shipped and the English file; otherwise the client uses
#: lobby+0xE24 instead.
#:
#: 0x014C +0x0F5 SETS the byte (0x6117E409 writes it whenever it differs) and
#: announces 8:63 "Your hangar rank is now %d." / 8:64 "Your maximum item
#: capacity is now %d." when the table's wanzer or capacity column changes --
#: the %d is the table value, so the screen says "hangar rank 4" at byte 2.
#: So both messages must carry the same stored rank or every battle end would
#: reset it.
#:
#: SE never published the job level. FMO_HANGAR_JOB_LEVEL (ours, to tune,
#: default 10): hangar rank = how many of jobs 1..8 are at that level or
#: above. 0 = the old behaviour, 0 everywhere.
HANGAR_JOB_LEVEL = _env_int("FMO_HANGAR_JOB_LEVEL", "10")
HANGAR_TABLE = ((2, 80), (2, 100), (4, 120), (4, 140), (6, 160), (6, 180), (6, 200),
                (8, 220), (8, 240), (8, 250), (8, 250), (8, 250), (8, 250), (8, 250),
                (8, 250), (8, 250))
HANGAR_JOBS = range(1, 9)                     # Assault .. Joker; 9-11 reserved, 12 Pilot


def hangar_capacity(rank):
    """(wanzers, item capacity) the client derives from a hangar rank byte."""
    return HANGAR_TABLE[min(max(int(rank), 0), 15)]


def hangar_rank_earned(char, level=None):
    """Jobs 1..8 at or above the job level; 0 when the knob is 0. Pure."""
    level = HANGAR_JOB_LEVEL if level is None else level
    if level <= 0:
        return 0
    exp = classes.class_exp_of(char)
    return sum(1 for k in HANGAR_JOBS if classes.class_level(exp.get(k, 0)) >= level)


def hangar_rank_stored(char):
    """The rank a battle end last banked (what 0x014A serves); 0 when none."""
    if HANGAR_JOB_LEVEL <= 0:
        return 0
    v = (char or {}).get("hangar_rank")
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def hangar_rank_at_battle_end(char):
    """(rank, line, changed): SE applies the change after a battle, so the
    battle end banks the earned rank on the character (the caller commits
    when `changed`) and serves it in 0x014C +0x0F5."""
    if char is None:
        return 0, "hangar rank: no pilot, +0x0F5 = 0", False
    if HANGAR_JOB_LEVEL <= 0:
        return 0, "hangar rank: FMO_HANGAR_JOB_LEVEL=0, +0x0F5 = 0 (the old behaviour)", False
    was, new = hangar_rank_stored(char), hangar_rank_earned(char)
    changed = char.get("hangar_rank") != new
    char["hangar_rank"] = new
    w, cap = hangar_capacity(new)
    return new, (f"hangar rank {was} -> {new} ({new} job(s) at Lv {HANGAR_JOB_LEVEL}+, "
                 f"FMO_HANGAR_JOB_LEVEL): {w} wanzers, {cap} items"
                 + (" -- the client announces 8:63/8:64 where the table value changed"
                    if hangar_capacity(was) != (w, cap) else "")), changed


#: KEY: ANOTHER PILOT'S HANGAR NEEDS THE OWNER'S FRIENDSHIP (2026-10-07).
#: Playing Manual p.46: "Besides your own hangar, you can enter another
#: player's hangar if that player (a friend (フレンド)) has given you
#: permission to enter", and "Other Player's Hangar" asks for the owner's
#: names and password. So three things: the owner is inside, the visitor is
#: on the owner's friend list, the password matches. FMO's Friend List is
#: PlayOnline's own (manual p.52), so "a friend" is a row in OpenLobby's
#: `friend` table: one of the owner member's handles holds an ACTIVE
#: KIND_FRIEND (0x0800) row whose peer handle belongs to the visitor's member.
#: A pending or invited request is not a friend yet.
#:
#: THE REFUSAL TEXT. 0x016D's reply is read by the Move controller's state 4
#: (0x61190B50): 0x0153 is the grant, message 1 goes quietly back to the
#: lobby (0x61174400), and ANY other id opens the failure box 0x6116CF50 with
#: 15:3 "The move failed." and the u16 at header +0x08 as the code. The code
#: picks its line from the table at 0x613955F0 (7-byte rows: s16 code, u8
#: error flag, u32 message id; walked by 0x6116CE10, read from the unpacked
#: image 2026-10-07):
#:     -14058  2:54 "There is no player with that name."
#:     -14059  2:55 "The hangar password is incorrect."
#:     -14060  2:56 "The player with that name is not in a hangar right now."
#: No row carries a friend message (40:10 "The target is not a friend." is
#: the friend window's own text, not in that table), so SE's server had no
#: separate friend refusal to show. Ours answers a non-friend with 2:56, the
#: same as an owner who is not inside, and checks friendship BEFORE the
#: password, so a stranger can neither probe the password nor learn whether
#: the owner is in. FMO_HANGAR_FRIEND_CODE picks another code (-14059 = say
#: "wrong password" instead).
#:
#: FMO_HANGAR_FRIEND=0 drops the friend check (password and presence only,
#: the behaviour before 2026-10-07). FMO_HANGAR_REFUSE_TEXT=0 answers every
#: refusal with the old silent message 1 instead of the failure box.
HANGAR_FRIEND = (os.environ.get("FMO_HANGAR_FRIEND", "").strip() or "1") != "0"
HANGAR_REFUSE_TEXT = (os.environ.get("FMO_HANGAR_REFUSE_TEXT", "").strip() or "1") != "0"
CODE_NO_PLAYER = -14058          # 2:54
CODE_WRONG_PASSWORD = -14059     # 2:55
CODE_NOT_IN_HANGAR = -14060      # 2:56
HANGAR_FRIEND_CODE = _env_int("FMO_HANGAR_FRIEND_CODE", str(CODE_NOT_IN_HANGAR))
#: accounts.KIND_FRIEND: another person (0x1400 is the handle itself, 1 a group)
POL_KIND_FRIEND = 0x0800
#: Does any handle of member %s (the owner) hold an active friend row naming
#: a handle of member %s (the visitor)?
HANGAR_FRIEND_SQL = ("SELECT 1 FROM friend f"
                     " JOIN handle o ON o.id = f.handle_id"
                     " JOIN handle v ON v.id = f.peer_handle"
                     " WHERE o.member_id = %s AND v.member_id = %s"
                     " AND f.status = 'active' AND f.kind = %s LIMIT 1")


def member_id_of(account):
    """The POL member id in an account key `member:<id>`, else None. Pure."""
    a = str(account or "")
    if a.startswith("member:") and a[7:].isdigit():
        return int(a[7:])
    return None


def owner_has_friend(owner_account, visitor_account, connect=None):
    """(answer, why): True when the owner's POL friend list holds the visitor,
    False when it does not, None when it cannot be told (a key that is no
    POL member, or no account database). The same member is its own friend.
    `connect` returns a DB connection (a test); the default is OpenLobby's."""
    o, v = member_id_of(owner_account), member_id_of(visitor_account)
    if o is None or v is None:
        return None, f"no POL member behind {owner_account!r} / {visitor_account!r}"
    if o == v:
        return True, f"member {o} visiting its own other pilot"
    try:
        db = connect() if connect is not None else identity.accounts_conn()[1]
        try:
            row = db.execute(HANGAR_FRIEND_SQL, (o, v, POL_KIND_FRIEND)).fetchone()
        finally:
            db.close()
    except Exception as e:
        return None, f"friend lookup failed ({e!r})"
    if row is not None:
        return True, f"member {v} is on member {o}'s POL friend list"
    return False, f"member {v} is NOT on member {o}'s POL friend list (active rows only)"


def hangar_visit_verdict(owner_host, owner_place, friend, password_ok, who=""):
    """None when a visitor may enter another pilot's hangar, else (code, why).
    `friend` is owner_has_friend's answer (None = unknown: let the password
    decide, and say so in the log). The order is presence, friendship,
    password. Pure."""
    if owner_host is None:
        return CODE_NOT_IN_HANGAR, f"{who} is not in any lobby (2:56)"
    if not owner_place or owner_place[1] != 5:
        return CODE_NOT_IN_HANGAR, f"{who} is not in their hangar (2:56)"
    if HANGAR_FRIEND and friend is False:
        return HANGAR_FRIEND_CODE, (f"the visitor is not on {who}'s friend list "
                                    f"(FMO_HANGAR_FRIEND; code {HANGAR_FRIEND_CODE})")
    if not password_ok:
        return CODE_WRONG_PASSWORD, f"wrong password for {who}'s hangar (2:55)"
    return None


def hangar_owner_password(host):
    """The stored hangar password of the character `host` is playing, or None."""
    n1, n2, _src = popnames.pop_names_for(host)
    for c in charstore.load_roster(identity.account_for(host)):
        if (c.get("first", "").strip().lower(), c.get("last", "").strip().lower()) == \
                (n1.strip().lower(), n2.strip().lower()):
            return c.get("hangar_password") or ""
    return None


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, classes, identity, inventory, move, popnames, popsweep  # noqa: E402
