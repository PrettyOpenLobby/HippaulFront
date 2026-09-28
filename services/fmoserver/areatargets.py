"""Area targets (0x01A9) and orders: the tiles a mission may be fought on."""
import os
import struct
from .deps import fmomsn, fmosectors, fmostore


#: KEY: AREA MISSIONS PICK THEIR OWN TARGET SECTOR (static 2026-09-12, NOT LIVE).
#: Accepting from the Area Mission list (UI cmd 0x1011 sets window+0x5B79 = 3,
#: 0x611C8EFA) does NOT go straight to 0x018A. The client first sends
#: 0x01A8 (12 B; body+0x00 = the row's mission id: picking the row copies its
#: whole 536-B record to table+0x63D4, 0x611C94D9, and the start at
#: 0x611CF1DB sends that dword) and waits for 0x01A9 (1,060 B, parse
#: 0x611727B0 copies it to table+0x5DA4):
#:     +0x1C u32   with count 0: -1 -> 27:6, else 28:8 "There was no target
#:                 sector." (0x611CAEF9) -- what our zeros have always drawn
#:     +0x20 u32   COUNT; > 0 -> 28:7 "Please select a target sector. Only
#:                 routes, cities and bases not under your army's control can
#:                 be chosen. The reward varies with the target sector's
#:                 control counter." (0x611CAE62), and the war map centres on
#:                 row 0 (0x611CE895 -> 0x611C4A70) as a sector picker
#:     +0x24 u32[] SECTOR IDS = 903,000,000 + row*1000 + col (the kind-7 id
#:                 space): the pick is accepted only if its id is in this list
#:                 (0x611C5F00, called at 0x611CF2FD with
#:                 (903000 + row) * 1000 + col). Up to 256 fit.
#: The pick then sends the ordinary ACCEPT (0x018A, 0x611CE799) from
#: table+0x5D44: body+0x00 = the mission id, body+0x28 = 1 (area mode,
#: 0x611CE695), body+0x2C = the picked sector id (0x611CE6C4). SE (guide/
#: mission.html:81-84): an area mission raises your side's control of ANY
#: sector to a set value within the period -- the taker chooses which.
#: FMO_AREA_TARGETS=1 serves the list: the pilot's zone's sectors with a
#: battle map on disk that the war state does not show held by the pilot's
#: nation (FMO_WAR off = every such sector). Default 0 = the zeros (28:8).
#: FMO_AREA_CONTROL = the control rate an area accept is met at (default 20,
#: fmowar's one step = the sector just flipped to us; SE's value is not on
#: the site). FMO_MISSION_DEADLINE_AREA = its deadline, 0 = the global one.
AREA_TARGETS = (os.environ.get("FMO_AREA_TARGETS", "").strip() or "0") != "0"
AREA_CONTROL = int(os.environ.get("FMO_AREA_CONTROL", "").strip() or "20", 0)
MISSION_DEADLINE_AREA = int(os.environ.get("FMO_MISSION_DEADLINE_AREA",
                                           "").strip() or "0", 0)
MSG_AREA_TARGETS_REQ, MSG_AREA_TARGETS_REPLY = 0x01A8, 0x01A9
#: KEY: THE ORDER SUBMIT, 0x0194 (364 B) -> 0x0195 (stub parse; any reply draws
#: 21:27 "This mission has been ordered.", poll 0x611CD170). Static 2026-09-12,
#: NOT LIVE: the send 0x61172650 copies 332 B from table+0x5BF8 to body+0x20,
#: filled by 0x611C4CF0 from the ORDER dialog (vtable slot at 0x61342F30,
#: handler 0x611CCBD0, call 0x611CCC64) and the Accepted Mission list's row 0.
#: Body offsets (payload = 0x20 + these):
ORDER_BODY = 0x20
ORDER_FIELDS = {
    0x000: "dialog+0x326 (u32)",
    0x004: "row 0 record+0x00 -- the accept KEY this order derives from",
    0x00C: "dialog+0x543 (u32)",
    0x034: "row 0 record+0x23C (u32)",
    0x038: "dialog+0x50A (u32)",
    0x13C: "dialog+0x53B (u32)",
    0x140: "dialog+0xEF (u32)",
    0x144: "dialog+0x537 (u32)",
}
ORDER_NAME, ORDER_NAME_LEN = 0x010, 0x24    #: "%s.%s" of lobby+0x890 / +0x8A1
ORDER_COMMENT, ORDER_COMMENT_LEN = 0x03C, 0x100   #: the text box, <= 255 chars
ORDER_KEEP = 20
MSG_ORDER_REQ = 0x0194
A9_ERROR, A9_COUNT, A9_ROWS = 0x1C, 0x20, 0x24
A9_ROW_MAX = (0x424 - A9_ROWS) // 4          # 256 -- the 0x109-dword copy
AREA_FLAG, AREA_TARGET = 0x28, 0x2C          # 0x018A body offsets


def area_sector_state(tile):
    """(nation, control, deadlock) the war state holds for `tile`, or None
    when there is no war state (FMO_WAR=0 / no module) or no record."""
    if warstate.WAR == "0":
        return None
    st = warstate.war_state()
    if st is None:
        return None
    s = st.data.get("sectors", {}).get(str(int(tile)))
    if not s:
        return None
    return (int(s.get("nation") or 0), int(s.get("control") or 0),
            bool(s.get("deadlock")))


def area_target_tiles(zone, nation, state_of=None):
    """SE's 28:7 rule as tiles: the sectors of selector `zone` with a battle
    map on disk that are NOT held by `nation`, in sector-row order, capped
    at A9_ROW_MAX. A sector the war state does not know counts as open."""
    if not zone or fmosectors is None:
        return []
    look = state_of or area_sector_state
    out = []
    for tile, (_row, mapno) in sorted(
            fmosectors.SECTORS.get(int(zone), {}).items(),
            key=lambda kv: kv[1][0]):
        if mapno not in missionlist.TYPE1_ON_DISK:
            continue
        s = look(tile)
        if s is not None and nation and s[0] == int(nation) and not s[2]:
            continue                          # already under our control
        out.append(int(tile))
    return out[:A9_ROW_MAX]


def area_target_taken(zone, tile, skip_account=None, rosters=None):
    """(account, mission name) of ANOTHER pilot's active area accept on
    (zone, tile) -- SE's -8, 27:13 "This sector is already the target of
    another area mission." -- or None. `rosters` [(account, [chars])] for
    the selftest; default every account in the character DB."""
    if rosters is None:
        db = charstore.use_db() if charstore.CHAR_STORE else None
        accts = (fmostore.store_accounts(db)
                 if (db and fmostore is not None) else [])
        rosters = ((a, charstore.load_roster(a)) for a in accts if a != skip_account)
    for acct, roster in rosters:
        if acct == skip_account:
            continue
        for c in roster or ():
            for m in missionbook.accepted_missions(c):
                if (m.get("cat") == 3 and int(m.get("sector") or 0) == int(tile)
                        and (not m.get("zone") or not zone
                             or int(m["zone"]) == int(zone))
                        and missionbook.mission_status(m) in missionboard.MISSION_ACTIVE):
                    return acct, m.get("name")
    return None


def reply_01a9(tiles, need=0x424):
    """The 0x01A9 body: count at +0x20, 903,000,000 + tile per row at +0x24."""
    b = bytearray(need)
    tiles = list(tiles)[:A9_ROW_MAX]
    struct.pack_into("<I", b, A9_COUNT, len(tiles))
    for i, t in enumerate(tiles):
        struct.pack_into("<I", b, A9_ROWS + 4 * i,
                         (fmomsn.SECTOR_ID_BASE + int(t)) & 0xFFFFFFFF)
    return bytes(b)


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, missionboard, missionbook, missionlist, warstate  # noqa: E402
