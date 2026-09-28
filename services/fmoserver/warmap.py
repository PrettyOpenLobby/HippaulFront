"""The war map (0x015E -> 0x015F, 0x0160 -> 0x0161): sector rows, the census and the counter
mission."""
import os
import struct
from .deps import fmosectors
from .wirelog import log


#: KEY: THE MAP SELECTOR'S MISSION ICON (static 2026-09-12; NOT LIVE).
#: The battle-map list item (0x893 B, vtable 0x6133D578) gets our 108-B 0x015F
#: row copied to item+0x8F (0x6118EA2C). Its draw method -- vtable slot 8,
#: 0x6118B450, `mov esi,ecx` -- reads `byte [esi+0x99]` = ROW +0x0A at
#: 0x6118B8D0: 0 draws nothing; 1 / 2 / 3 draw sprites 0x45 / 0x46 / 0x47
#: through 0x611FD380. SE added exactly three map-selector icons that day
#: (update/050628gp4sc1), listed in this order: a map where a MISSION is under
#: way, one where an EVENT BATTLE is, one where a COUNTER-MISSION is available
#: or under way. One byte, so one icon per map.
#: VERIFIED: BOUND LIVE 2026-09-12: **1 = SE's yellow boxed "M", the MISSION icon** --
#: a tester compared the entry against SE's own screenshot of this list
#: (playonline.com's fmo/topics/050704/img/mission_mark.jpg). 2/3 are
#: unobserved; hangeki-ms.jpg in the same folder is the COUNTER-MISSION arrow,
#: so one of them is that. 0 turns the icon off.
S15F_ICON = 0x0A
MISSION_MAP_ICON = int(os.environ.get("FMO_MISSION_MAP_ICON", "").strip()
                       or "1", 0)

#: KEY: THE REST OF THE BATTLE MAP LIST ENTRY (static 2026-09-12; NOT
#: LIVE). Same draw method 0x6118B450, item+0x8F = row+0x00. `own` =
#: byte[lobby+0x8B4] - 1 (0x61175B00: 1 = O.C.U. -> 0, 2 = U.S.N. -> 1) and
#: `other` = own ^ 1 (0x61175B20):
#:   +0x04 s16      the minutes bar's whole (0x6118B7A8: grey = +0x04 - +0x06)
#:   +0x06 s16      10:39 "%02dmin" and the bar's white part (0x6118B7A1)
#:   +0x08+own u8   the "%02d" at the left of the top line (0x6118B5C7)
#:   +0x4A u8, +0x58 u8, +0x5A+own / +0x5A+other s8 -- the top line's bar
#:                  (0x6118B5D4..0x6118B66B): blue +0x4A of max(+0x5A+own,
#:                  +0x4A), then pink +0x58 of max(+0x5A+other, +0x58); only
#:                  when [list+0x229] is set and both wholes are nonzero, else
#:                  one grey bar. Colours are PS2 ABGR (0x80b86c77 = blue,
#:                  0x807665ae = pink) -- SE's screenshot is blue-left pink-right.
#:   +0x60 set, +0x61 clear   10:70 "Incoming Enemies" (flashing) in place of
#:                  the minutes (0x6118B71D)
#:   +0x0B u8       a sprite id on the top line, drawn unconditionally (not served)
#: SE's own callouts for this list (guide/mapselector.html:112-124): 1 = "the
#: number of O.C.U. and U.S.N. wanzers sortied", 2 = "time elapsed since the
#: battle began"; one battle map holds 10 vs 10 (ibid. :60). So the minutes
#: are ELAPSED, not remaining, and SE's "01 ... 00min" (topics/050704/img/
#: mission_mark.jpg) is a map just created and still WAITING FOR OPPONENTS
#: (topics/060804: up to 20 min).
#: WARNING: UNBOUND: that +0x4A/+0x58 are the viewer's side and the enemy's (inferred
#: only from the capacity each is paired with), and what [list+0x229] is.
#: FMO_WARMAP_ENTRY=0 leaves all of these zero, exactly as before.
S15F_LIMIT = 0x04
S15F_ELAPSED = 0x06
S15F_COUNT = 0x08              #: +0 O.C.U., +1 U.S.N. (+2 is S15F_ICON)
S15F_BAR_OWN = 0x4A
S15F_BAR_OTHER = 0x58
S15F_CAP = 0x5A                #: +0 O.C.U., +1 U.S.N.
S15F_SIDE_CAP = 10             #: SE: "10vs10" per battle map
WARMAP_ENTRY = (os.environ.get("FMO_WARMAP_ENTRY", "").strip() or "1") != "0"

#: KEY: COUNTER-MISSIONS (static 2026-09-12, NOT LIVE). SE's guide
#: (guide/mission.html:152-158, topics/050719/mission.html.txt:54): an ARROW at
#: the right of a Battle Map list entry marks a battle map where an ENEMY
#: battle-map mission is RUNNING (実行中 = its taker has created a battle map on
#: the target sector); win there and every member of the battle group is paid a
#: 反撃ミッションボーナス, larger the sooner after the enemy created the map, and
#: it is paid "as part of the 撃破ボーナス" -- paybook kind 2, Kill bonus.
#: SE's map-selector icons were added in listing order mission / event battle /
#: counter-mission (update/050628gp4sc1) and row +0x0A = 1 is the bound MISSION
#: icon, so the arrow is 2 or 3 -- UNOBSERVED. FMO_COUNTER_MISSION is the byte
#: served for it: 0 (default) = off, 2 or 3 = a guess to compare against SE's
#: topics/050704/img/hangeki-ms.jpg. A viewer's OWN mission on the same map
#: wins the byte (one icon per map).
#: "Running" here = an enemy-side pilot is fighting on that map now
#: (warmap_census) AND that pilot holds an open battle-map mission whose
#: battlefield is that (zone, tile). The bonus is OURS to size: SE's amount is
#: not on the site. FMO_COUNTER_BONUS_HS (default 0 = log only) is owed to the
#: winning pilot as a kind-2 line at the Personnel Officer.
COUNTER_MISSION = int(os.environ.get("FMO_COUNTER_MISSION", "").strip()
                      or "0", 0)
COUNTER_BONUS_HS = int(os.environ.get("FMO_COUNTER_BONUS_HS", "").strip()
                       or "0", 0)
PAY_KILL = 2                   #: paybook kind 2 = "Kill bonus" (0x61191420)

#: KEY: SECTOR MISSIONS, category 2 (static 2026-09-12, NOT LIVE). SE
#: (guide/mission.html:73-78): "win a set number of battles in the named
#: sector within the time limit"; the taker need not sortie -- other battle
#: groups' wins count; SE's tiers were 4 / 8 / 16 wins. Wins are kept in an
#: in-process ledger keyed by (zone, tile, nation) and every open category-2
#: accept is judged against the wins recorded after its own accept time, so a
#: taker sitting in the lobby is met by someone else's battle without any
#: cross-session write. WARNING: The ledger does not survive a restart.
#: FMO_MISSION_WINS = wins needed (default 4, SE's lowest tier); a row can
#: override it with FMO_MSN_WINS="<row>:<n>,...". FMO_MISSION_DEADLINE_SECTOR
#: = the category-2 deadline in seconds, 0 (default) = FMO_MISSION_DEADLINE.
MISSION_WINS = int(os.environ.get("FMO_MISSION_WINS", "").strip() or "4", 0)
MISSION_DEADLINE_SECTOR = int(os.environ.get("FMO_MISSION_DEADLINE_SECTOR",
                                             "").strip() or "0", 0)
MSN_WINS = os.environ.get("FMO_MSN_WINS", "").strip()


def warmap_census(mapno, states, side_of, now, limit):
    """Who is fighting on battle map `mapno` now: ([O.C.U., U.S.N.] wanzer
    counts, the earliest sortie time or None, [(host, side)]).

    `states` is BATTLE_STATE. A sortie counts while it is not `ended` and is
    younger than the battle's time limit (an hour with none): a client that
    drops mid-battle never sends the end, and the limit is when that battle
    would have ended anyway. Pure apart from `side_of`."""
    stale = limit if limit > 0 else 3600
    counts, start, who = [0, 0], None, []
    for host, st in list(states.items()):
        if (not isinstance(st, dict) or st.get("mapno") != mapno
                or st.get("ended") or now - st.get("granted_at", 0) >= stale):
            continue
        side = side_of(host)
        if side not in (0, 1):
            continue
        counts[side] += 1
        who.append((host, side))
        start = st["granted_at"] if start is None else min(start, st["granted_at"])
    return counts, start, who


def warmap_entry_fill(row, counts, own, elapsed, limit):
    """Write a census into one 0x015F row (a bytearray; see S15F_COUNT).
    `own` is the VIEWER's side, 0 = O.C.U. Leaves +0x00 and +0x0A alone."""
    mins = max(0, int(elapsed)) // 60
    struct.pack_into("<hh", row, S15F_LIMIT,
                     min(max(limit // 60, mins), 0x7FFF), min(mins, 0x7FFF))
    row[S15F_COUNT] = min(counts[0], 99)
    row[S15F_COUNT + 1] = min(counts[1], 99)
    row[S15F_BAR_OWN] = min(counts[own], 0x7F)
    row[S15F_BAR_OTHER] = min(counts[own ^ 1], 0x7F)
    row[S15F_CAP] = row[S15F_CAP + 1] = S15F_SIDE_CAP
    return row


#: KEY: THE REAL WAR MAP'S TWO REQUESTS (static 2026-09-06).
#: WARNING: These are NOT 0x01F4/0x01F6 -- those belong to SE's DEBUG "Lobby
#: Menu" (see MSG_01F4_REQ's corrected note). The retail war-map SCREEN is the
#: 0x5A18-byte class ctor 0x6118CA00 (lobby window-factory id 0x31, opened by
#: script native 0xE318 from `tag_senior`), and the ONLY two requests its whole
#: module (0x61188000..0x6118F000) sends are:
#:
#:   0x015E (20 B, sender 0x611883E0, body+0x00 = a u32 arg) -> 0x015F
#:     = BATTLE MAP INFORMATION. Poller tick 0x6118A1C0 tests word[+6] == 0x15F
#:     and posts UI event 0x144E; any other id posts 0x144F with word[+8] as the
#:     code, and 0x144F shows systext 10:4 "Failed to get the battle map
#:     information." The 0x144E arm (0x6118E8D2) reads the payload as
#:         +0x00 u32 count
#:         +0x14 + i*0x6C   row i, 0x6C (108) B  (0x1b dwords, copied verbatim);
#:                          the row's first dword is its key/id
#:     and iterates while count > 0 (`jle` past the loop), so count 0 is the
#:     safe empty answer -- SE's own "NO PLAYER"/no-data state.
#:
#:   0x0160 (28 B, sender 0x61188480, body+0x00 and +0x04 = two u32 args) -> 0x0161
#:     = PLAYER INFORMATION for a battle map. Poller tick 0x6118A2A0 -> UI event
#:     0x1450 (success) / 0x1451 (failure, systext 10:5 "Failed to get the player
#:     information for the battle map."). The 0x1450 arm (0x6118EBA7) reads the
#:     SAME shape -- u32 count at +0x00, rows from +0x14 -- and hands (rows,
#:     count) to 0x6118D360. Row stride not yet read; count 0 needs none.
#:
#: WARNING: UNMEASURED: the request bodies (what the client is ASKING for) and the row
#: layouts. Both fall out of the first live open, exactly like 0x013E
#: -- which is why the default here is an ANSWERED-BUT-EMPTY reply: it keeps the
#: client off the silence-park (the 0x0156 / 0x0190 precedent) and makes the
#: request bytes appear in the log.
#: VERIFIED:KEY: FMO_WARMAP_SECTORS -- answer 0x015E with the battle map of the
#: SECTOR THE CLIENT ASKED FOR, out of fmosectors.py, instead of the flat
#: FMO_WARMAP_MAPS list. Default ON; `0` restores the flat list exactly.
#:
#: WHY. Measured live 2026-09-08, a player: *"no matter what sector I pick for
#: the battle, it always takes me to the same place."* Two war-map picks sent
#: 0x015E bodies whose +0x00 u32 was 0x000119B5 (72117) and 0x000115D1 (71121)
#: -- and both got map 418, because WARMAP_MAPS is a constant. Those numbers
#: are the `tile` column of the client's own ARE table: selector 200 row 20
#: "Sector 20" -> battle map 267, and row 17 "Sector 17" -> 232.
#:
#: WARNING: THE SELECTOR IS THE MapKind. 1,608 tiles are shared between selectors and
#: only the PAIR is unique, so the lookup is keyed on (selector, tile).
#: WARNING: IT IS THE PILOT'S OWN ZONE, NOT FMO_MAPKIND (fixed 2026-09-12). Zones went
#: per-pilot on 09-08 (WORLD_ZONES, Change Area) and this kept reading the
#: global 200, so outside 200 every pick missed: Molly in 207 at 20:50:38Z sent
#: tile 89135 -- selector 207's Sector 21 -> map 42 -- and got "NOT in selector
#: 200's 25 sectors", i.e. the FMO_WARMAP_MAPS fallback. The war map draws the
#: ARE of the MapKind our last 0x0153 granted, so WORLD_ZONES is the selector;
#: a zone with no sector table (none known today) falls back to MAPKIND.
#:
#: WARNING: A SECTOR MAY LEGITIMATELY HAVE NO BATTLE. 1,500 of the 3,108 rows name
#: map 2/3/4/5, none of which is on disk. Those answer count=0 -- SE's own
#: empty list, which the client's `count > 0` row loop handles -- and NOT a
#: refusal, because there is nothing wrong with an empty sector.
WARMAP_SECTORS = os.environ.get("FMO_WARMAP_SECTORS", "1").strip() != "0"

MSG_015E_REQ = 0x015E
MSG_015F_REPLY = 0x015F
MSG_0160_REQ = 0x0160
MSG_0161_REPLY = 0x0161
#: The count lives at +0x00 and the first row at +0x14, so 0x14 B is the whole
#: reply when count == 0. Rows are appended verbatim when we learn their layout.
S15F_HEAD_LEN = 0x14
#: '1' (default) = answer with count 0. 'fail' = reply message 2 (id != the
#: expected one), the client's graceful error arm -> systext 10:4 / 10:5. '0' =
#: stay silent, which parks the war map exactly as silence parked 0x0156.
ANSWER_015E = os.environ.get("FMO_ANSWER_015E", "1").strip() or "1"
ANSWER_0160 = os.environ.get("FMO_ANSWER_0160", "1").strip() or "1"

#: VERIFIED:KEY: AND THE WAR MAP *DOES* SORTIE -- static 2026-09-06, the chain read end
#: to end. An earlier plan's step 4 said "how the RETAIL war map sorties
#: is not yet traced" because a send-scan of the war-map module finds only
#: 0x015E and 0x0160. It does not send 0x0139 itself; it CONSTRUCTS the shared
#: SelectBattleMap async object (ctor 0x610F8E70, 5 args) and that object's tick
#: calls the same 0x61173BE0 the debug menu and kycli_lobmain state 10 call:
#:
#:   0x6118E110  the war map's UI-event handler, 0x13 arms (index table
#:               0x6118EC60, arms 0x6118EC44). Event 15 -> arm 3, events 16/18
#:               -> arm 4 (0x6118E172), events 17/19 -> arm 5.
#:   arm 3 (0x6118E3C7)  [warmap+0x5A10] = the battle-map list widget's SELECTED
#:               ITEM VALUE (vtable +0xA4 on [0x613B703C]+0x4A).
#:   arm 4 (0x6118E172)  new(0x48) -> 0x610F8E70:
#:               [ebp+0x10] == 0xF -> JOIN  (0x6118E1F6): id = [warmap+0x5A10]
#:               otherwise         -> CREATE(0x6118E27C): id = 0, arg3 = 1,
#:                                    arg4 = the selected ARE row's first dword
#:
#: and the object sends 0x61173BE0(id, 0, arg3, arg4, arg5) = 0x0139, 80-B body:
#:
#:     +0x00 u32  BATTLE MAP ID   (also stored to lobby+0x4F08)
#:     +0x04 u8   flag
#:     +0x08 u32  1 if lobby+0x68 > 0 and lobby+0x6C > 0, else 0
#:     +0x0C u32  arg4
#:     +0x10      arg3, then 47 B copied from <0x61165BA0>+0x38C
#:     +0x40 u32  arg5
#:
#: KEY: WHERE THE ID COMES FROM, and why this matters: the list widget's items are
#: built at 0x6118E9E0 straight out of OUR 0x015F reply --
#:
#:     esi = payload + 0x14 + i*0x6C ; 0x1B dwords copied verbatim
#:     item = 0x6118D190(...)        ; [item+0x8F] <- those 108 bytes
#:     [item+0x60] = row's FIRST DWORD   <-- the item's VALUE
#:
#: -- and arm 3 copies exactly that value into [warmap+0x5A10]. So **the first
#: dword of a 0x015F row is the battle-map id the client will sortie with.**
#: Serve one, let the player pick it, and FMO_SORTIE's 0x013A answers the
#: 0x0139 that follows. That is the game's OWN door into scene 4, with no push.
#:
#: WARNING: The grid the war map draws is NOT this: it is client data, the ARE resource
#: copied to lobby+0x771C by 0x61174030 ((count+1) x 0x70 rows) and memcpy'd
#: into the screen at 0x6118C2CF. Nothing we send moves the grid.
#: WARNING: Only the row's FIRST DWORD is earned. The other 104 bytes feed the list's
#: display and are UNREAD -- FMO_WARMAP_FIELDS poke them, nothing names them.
#: WARNING: NEVER LIVE-TESTED.
WARMAP_MAPS = [int(s, 0) for s in
               os.environ.get("FMO_WARMAP_MAPS", "").replace(" ", "").split(",")
               if s]
#: Raw pokes into a 0x015F row: "row:offset=value,...". Offsets are 0..0x6B.
WARMAP_FIELDS = os.environ.get("FMO_WARMAP_FIELDS", "")
S15F_ROW_LEN = 0x6C            #: 0x6118E9E8 `imul eax,eax,0x6C`, 0x1B dwords


def warmap_rows(ids=None):
    """The 0x015F rows: 108 bytes each, first dword = the battle-map id the
    client sorties with. Everything else is zero unless FMO_WARMAP_FIELDS
    says otherwise, because nothing else about the row is established."""
    ids = WARMAP_MAPS if ids is None else ids
    pokes = {}
    for clause in WARMAP_FIELDS.split(","):
        clause = clause.strip()
        if not clause:
            continue
        try:
            where, val = clause.split("=", 1)
            row, off = where.split(":", 1)
            pokes.setdefault(int(row, 0), {})[int(off, 0)] = int(val, 0)
        except ValueError:
            log(f"[fmo] WARNING: FMO_WARMAP_FIELDS: ignoring malformed clause "
                f"{clause!r} (want row:offset=value)")
    out = []
    for i, mid in enumerate(ids):
        row = bytearray(S15F_ROW_LEN)
        struct.pack_into("<I", row, 0, mid & 0xFFFFFFFF)
        for off, val in sorted(pokes.get(i, {}).items()):
            if 0 <= off <= S15F_ROW_LEN - 4:
                struct.pack_into("<I", row, off, val & 0xFFFFFFFF)
        out.append(bytes(row))
    return out


def chat_echo_name(name1, name2, host):
    """The name a cmd-18 echo should carry, given what the submit said.

    A cmd-115 submit from a BATTLE carries NO names: chat arm 3 (0x611D5A12)
    reads them out of the group member-info blob at blob+0x1C / blob+0x2D and
    ours is zeros there, so the record arrives `unnamed`. Echoing that back
    hands the client a cmd 18 with an empty name field and the line never
    appears -- measured live 2026-09-08, a player typing "YEAH NO CHAT
    STILL" while the server logged, echoed, and got an ACK for every word.

    We know who is on the channel whatever the record claims, so fall back to
    it. Returns (name, source) so the log can say which one was used."""
    _sub = name1 or " ".join(x for x in (name1, name2) if x)
    if _sub:
        return _sub, "the submit"
    known1, known2, ksrc = popnames.pop_names_for(host)
    if known1:
        return known1, f"the channel ({ksrc})"
    return "", "NOTHING KNOWN -- the line may not render"


def warmap_selector(host):
    """The ARE selector this host's war map draws: the zone its last 0x0153
    granted (WORLD_ZONES), or FMO_MAPKIND when that zone is unknown or has no
    sector table."""
    zone = rooms.WORLD_ZONES.get(host)
    try:
        if zone and fmosectors is not None and fmosectors.sector_count(zone):
            return zone
    except Exception:
        pass
    return zoneentry.MAPKIND


def warmap_sector_for(body, selector=None):
    """A 0x015E request body -> (tile, row, mapno, on_disk, note).

    `tile` is None when the body is too short to carry one. `row`/`mapno` are
    None when the (selector, tile) pair is not in SE's table -- which is a
    DIFFERENT answer from a pair whose map is 2/3/4/5, i.e. a real sector with
    no battlefield. `on_disk` separates that second case from a map we could
    actually serve, because a type-1 id with no file is the 0x611250A2 crash.
    Pure, so the selftest can drive it without a socket."""
    sel = zoneentry.MAPKIND if selector is None else selector
    if len(body) < 4:
        return None, None, None, False, (
            f"the request is {len(body)}B, too short to carry the u32 sector "
            f"tile at +0x00 -- falling back to FMO_WARMAP_MAPS")
    tile = struct.unpack_from("<I", body, 0)[0]
    if not WARMAP_SECTORS:
        return tile, None, None, False, (
            "FMO_WARMAP_SECTORS=0: the flat FMO_WARMAP_MAPS list is served for "
            "every sector, so every pick lands in the same battlefield")
    if fmosectors is None:
        return tile, None, None, False, (
            "fmosectors.py did not import -- falling back to FMO_WARMAP_MAPS")
    hit = fmosectors.battle_map_for(sel, tile)
    if hit is None:
        return tile, None, None, False, (
            f"tile {tile} (0x{tile:X}) is NOT in selector {sel}'s "
            f"{fmosectors.sector_count(sel)} sectors -- falling back to "
            f"FMO_WARMAP_MAPS. Either MAPKIND and the war map disagree, or "
            f"the ARE table needs regenerating")
    row, mapno = hit
    if mapno not in missionlist.TYPE1_ON_DISK:
        return tile, row, mapno, False, (
            f"selector {sel} sector {row} (tile {tile}) names battle map "
            f"{mapno}, which is NOT a type-1 map on disk -- one of SE's "
            f"2/3/4/5 placeholders, i.e. this sector HAS no battle. Answering "
            f"count=0, which is SE's own empty list")
    return tile, row, mapno, True, (
        f"selector {sel} sector {row} (tile {tile}) -> battle map {mapno} "
        f"(fmosectors.py, from the client's own ARE table)")


def reply_warmap_list(count=0, rows=None):
    """The 0x015F / 0x0161 body: u32 count at +0x00, rows from +0x14.

    count defaults to 0 = the empty, provably safe answer (both consumers guard
    their row loop with `count > 0`)."""
    rows = rows or []
    body = bytearray(S15F_HEAD_LEN + S15F_ROW_LEN * len(rows))
    struct.pack_into("<I", body, 0, len(rows) if rows else count)
    for i, row in enumerate(rows):
        body[S15F_HEAD_LEN + i * S15F_ROW_LEN:
             S15F_HEAD_LEN + (i + 1) * S15F_ROW_LEN] = row
    return bytes(body)


# Called at run time only; imported last so that import cycles resolve.
from . import missionlist, popnames, rooms, zoneentry  # noqa: E402
