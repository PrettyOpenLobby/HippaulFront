"""A pilot's accepted missions: keys, deadlines, status, battle results, reports and pay."""
import datetime
import struct
import time
from .deps import fmomsn, fmostore
from .knobs import _env_int


def mission_key(m):
    """The dword a stored accept is listed under at record+0x00 -- its own
    per-accept key, or the mission id for accepts stored before keys existed.
    KEY: The client only ROUND-TRIPS this number (0x611C95EA / 0x611C4CD0 copy
    record+0x00 into the 0x01C6 / 0x0196 body), so it can be unique per accept:
    live 2026-09-12 a re-accept listed the closed record and the new one under
    ONE id and Report/Cancel acted on the newest. key = id + (n << 16), n = the
    accept's ordinal for that id, so the id is still the low 16 bits."""
    k = (m or {}).get("key")
    try:
        return int(k) if k is not None else int((m or {}).get("id", 0))
    except (TypeError, ValueError):
        return int((m or {}).get("id", 0))


def mission_next_key(cur, mid):
    """A key no accept of `mid` in `cur` uses. Ids past 16 bits get none."""
    mid = int(mid)
    if mid >= 0x10000:
        return None
    used = [(mission_key(m) >> 16) for m in cur
            if int(m.get("id", -1)) == mid]
    return mid + ((max(used) + 1 if used else 1) << 16)


def mission_requirements():
    """{mission_id: {name, rank, fee, mp}} -- READ BACK OUT OF THE RECORD BYTES.

    KEY: Deliberately not a parallel table. The requirements a gate judges are the
    exact bytes the client was shown, unpacked from the record `msn_rows()`
    builds at the offsets the row renderer reads (fmomsn.MISSION_RANK/_FEE/_MP).
    Anything that authors a row -- FMO_MSN_FIELDS today, a catalogue tomorrow --
    is therefore automatically the source of truth, and a gate can never refuse
    a pilot for a number that was never on screen.

    WARNING: `rank` is a rank-table INDEX, not a quantity: the renderer draws it with
    `imul 0x7C` off [0x613CA3E8]+0x10, and the pilot's own rank byte indexes the
    same 0-based D15 table (21 = Major), so the two compare directly.
    """
    fields = community._msn_fields()
    zones = sectorwins._row_int_map(sectorwins.MSN_ZONES, "FMO_MSN_ZONES")
    wins = sectorwins._row_int_map(warmap.MSN_WINS, "FMO_MSN_WINS")
    out = {}
    for i, (mid, _cat, name) in enumerate(community.msn_row_table()):
        # category 0: these offsets do not depend on it.
        rec = fmomsn.mission_record(name, 0, fields=fields.get(i), mid=mid)
        out[mid] = {
            "name": name,
            # the row's own list (1 battle map / 2 sector / 3 area), None =
            # an uncategorised row that echoes every query
            "cat": _cat,
            # FMO_MSN_ZONES: the AREA the row is issued in, or None
            "zone": zones.get(i),
            # category 2: wins needed (FMO_MSN_WINS, else FMO_MISSION_WINS)
            "wins": wins.get(i, warmap.MISSION_WINS),
            "rank": struct.unpack_from("<I", rec, fmomsn.MISSION_RANK)[0],
            "fee": struct.unpack_from("<I", rec, fmomsn.MISSION_FEE)[0],
            # WARNING: NOT a requirement -- kept only so the log can say what the
            # mission PAYS. See the MP note in mission_accept_verdict().
            "reward_mp": struct.unpack_from(
                "<I", rec, fmomsn.MISSION_REWARD_MP)[0],
            "reward_hs": struct.unpack_from(
                "<I", rec, fmomsn.MISSION_REWARD_HS)[0],
            # the Distribution MP (22:9): bytes 0..2 of +0x1C8, whose top byte
            # is the category -- what each battle-group member is owed on a
            # win (missionboard.MISSION_SHARE)
            "share_mp": struct.unpack_from(
                "<I", rec, fmomsn.MISSION_DISTRIBUTION)[0] & 0xFFFFFF,
            # the target sector: an ARE tile (row*1000+col), 0 = none. For a
            # battle-map mission it is the BATTLEFIELD -- see
            # mission_battle_apply and the map selector's icon.
            "sector": struct.unpack_from(
                "<I", rec, fmomsn.MISSION_SECTOR)[0],
        }
    if missionboard.ORDER:
        out.update(order_requirements())    # open derived missions (orders)
    return out


def accepted_missions(char):
    """The pilot's accepted missions, oldest first.

    KEY: No schema change was needed: `missions` is not a `fmostore.COLUMNS` key,
    so it rides in the record's `extra` JSON blob and round-trips as a list of
    dicts. Each is `{id, name, fee, at}` -- the id is `record+0x00`, the one
    thing the client hands back, so it is what everything else keys on.
    """
    v = (char or {}).get("missions")
    if not isinstance(v, list):
        return []
    return [m for m in v if isinstance(m, dict)]


def _mission_iso(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(servicerecord._now_unix(now)))


def _mission_epoch(m, key="at"):
    """A stored stamp as a time_t, or None. Accepts are stored as ISO text."""
    v = (m or {}).get(key)
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.datetime.strptime(str(v), "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    except ValueError:
        return None


def mission_deadline(cat=None):
    """The deadline in force: FMO_MISSION_DEADLINE (a category-2 accept:
    FMO_MISSION_DEADLINE_SECTOR when set), but only while FMO_MISSION_REPORT
    is armed -- with it off nothing may expire, or an old accept would
    silently stop blocking a re-accept (and a second fee)."""
    if not missionboard.MISSION_REPORT:
        return 0
    if cat == 2 and warmap.MISSION_DEADLINE_SECTOR:
        return warmap.MISSION_DEADLINE_SECTOR
    if cat == 3 and areatargets.MISSION_DEADLINE_AREA:
        return areatargets.MISSION_DEADLINE_AREA
    return missionboard.MISSION_DEADLINE


def mission_status(m, now=None, limit=None, ledger=None, rosters=None):
    """The stored status with the DEADLINE applied: an "open" mission past
    at + limit is "expired" whether or not anything has written that down.
    A category-2 (sector) accept is "met" once the win ledger holds its
    `needed` wins by its nation on its (zone, sector) after its accept and
    inside its deadline -- whoever fought them (SE: the taker need not
    sortie). With FMO_SECTOR_OWN_WINS only the wins of the battle-map
    missions it ORDERED count (sector_order_wins; `rosters` is for a test)."""
    if (m or {}).get("derived"):
        return order_status(m, now)            # an ORDER: ordered/taken/...
    st = (m or {}).get("status") or "open"
    cat = (m or {}).get("cat")
    lim = mission_deadline(cat) if limit is None else int(limit)
    if st == "open":
        at = _mission_epoch(m)
        _now = servicerecord._now_unix(now)
        # SE, news7740: 「初期化に伴い、停戦時に実行中のエリアミッションはすべて自動的
        # に失敗となります」 -- an AREA accept still running when a new phase
        # reset the frontline has FAILED, met or not (FMO_AREA_RESET_FAIL).
        if cat == 3 and at is not None and missionboard.AREA_RESET_FAIL:
            _reset = warstate.frontline_reset_at()
            if _reset and at < _reset <= _now:
                return "failed"
        if cat == 2 and at is not None and int(m.get("needed") or 0) > 0:
            until = min(_now, at + lim) if lim else _now
            if missionboard.SECTOR_OWN_WINS:
                # Playing Manual p.60: only wins in the missions THIS accept
                # ordered count (FMO_SECTOR_OWN_WINS)
                got = sector_order_wins(m, at, until, rosters)
            elif _nation(m) is None:
                # KEY: only the HOLDER'S nation's wins count (Playing Manual
                # p.60). An accept with no nation on record would otherwise
                # read the ledger's nation-0 key, i.e. wins nobody owns.
                got = 0
            else:
                got = sectorwins.sector_wins_between(m.get("zone"), m.get("sector"),
                                                     _nation(m), at, until, ledger)
            if got >= int(m["needed"]):
                return "met"
        # an AREA accept (category 3) with a picked target: met while the
        # war state shows that sector ours at >= its control_needed, inside
        # the deadline. WARNING: The war keeps no history, so a sector taken and
        # lost again before the pilot reports reads open -- report promptly.
        if (cat == 3 and at is not None and m.get("sector")
                and int(m.get("control_needed") or 0) > 0
                and (not lim or _now <= at + lim)):
            s3 = areatargets.area_sector_state(m["sector"])
            if (s3 is not None and not s3[2] and m.get("nation")
                    and s3[0] == int(m["nation"])
                    and s3[1] >= int(m["control_needed"])):
                return "met"
        if lim and at is not None and _now > at + lim:
            return "expired"
    return st


def _nation(x):
    """A record's nation as 1 or 2, else None (unknown is never a nation)."""
    try:
        n = int((x or {}).get("nation") or 0)
    except (TypeError, ValueError):
        return None
    return n if n in (1, 2) else None


def mission_report_text(state, result):
    """What 0x611CA9B0 draws for a 0x018F carrying (state, result)."""
    if state == 4:
        return "28:3 'The mission has not been completed yet.'"
    if state == 5 and result == 2:
        return ("28:4 'Mission completion confirmed. The reward will be paid "
                "by the Personnel.Officer.'")
    if state == 5:
        return "28:5 'The mission ended in failure.'"
    if state == 6:
        return "28:6 'The mission's time limit has passed.'"
    return (f"NOTHING -- state {state} has no arm (0x611CAA4F falls through "
            f"with no dialog)")


def mission_row_fields(m, now=None, limit=None):
    """{offset: u32} for one accepted mission's View-B record."""
    state, result = missionboard.MISSION_STATE.get(mission_status(m, now, limit), (4, 0))
    lim = mission_deadline((m or {}).get("cat")) if limit is None else int(limit)
    if (m or {}).get("derived"):
        lim = int(m.get("limit") or 0)      # an order's own operation time
    out = {missionboard.MR_STATE: state, missionboard.MR_RESULT: result, missionboard.MR_LIMIT: lim,
           missionboard.MR_START: _mission_epoch(m) or 0}
    out.update(order_row_fields(m, now))
    return out


def accepted_list_rows(missions, now=None):
    """Rows for reply_018e_short. Knob OFF: [(id, name)], byte-identical to
    what the list has always served. ON: active missions first (oldest
    first), then closed ones newest first, each with its state fields -- so a
    pilot's live missions are never pushed past the 21-row cap by history."""
    if not missionboard.MISSION_REPORT:
        # an ORDER row still says "Ordered" (+0x118); an orderable accept
        # still carries its template id -- the Order path needs no report
        return [((m.get("id", 0), m.get("name", ""), mission_row_fields(m, now)
                  if m.get("derived") else order_row_fields(m, now))
                 if (m.get("derived") and missionboard.ORDER) or order_row_fields(m, now)
                 else (m.get("id", 0), m.get("name", "")))
                for m in missions]
    _live = tuple(missionboard.MISSION_ACTIVE) + ("ordered", "taken")
    act = [m for m in missions if mission_status(m, now) in _live]
    done = [m for m in missions if mission_status(m, now) not in _live]
    if missionboard.ORDER:
        # the Order dialog always reads ROW 0 (0x611C9850 / 0x611CCC5F)
        act.sort(key=lambda m: order_source_ok(m, now) is None)
    # record+0x00 = the accept's OWN key (mission_key), so two accepts of one
    # row are two rows the client can Report / Cancel apart
    return [(mission_key(m), m.get("name", ""), mission_row_fields(m, now))
            for m in act + done[::-1]]


def mission_find(char, mid):
    """The accept whose key is `mid` -- the number the client sent back from
    record+0x00 -- else the pilot's most recent accept of mission id `mid`
    (accepts stored before keys existed, or a row id), or None."""
    for m in reversed(accepted_missions(char)):
        if mission_key(m) == int(mid):
            return m
    for m in reversed(accepted_missions(char)):
        if int(m.get("id", -1)) == int(mid):
            return m
    return None


def mission_prune(cur, keep_closed=10):
    """Every active accept, plus the last `keep_closed` closed ones."""
    closed = [m for m in cur if mission_status(m) not in missionboard.MISSION_ACTIVE
              and not (m.get("derived") and mission_status(m) in ("ordered", "taken"))]
    drop = {id(m) for m in closed[:max(0, len(closed) - keep_closed)]}
    return [m for m in cur if id(m) not in drop]


def mission_battle_apply(char, won, now=None, limit=None, cats=None,
                         tile=None, zone=None):
    """One battle end against the pilot's accepts. MUTATES them; returns
    [(mission, new status)] for every one that moved. `zone` is the selector
    the battle's sector was picked in; an accept that remembers its zone is
    settled only by a battle in that zone (1,608 tiles are shared between
    selectors, so the tile alone is not a place).

    SE (AUDIT §D3): a battle-map mission succeeds only if the taker sorties
    AND WINS within the deadline; a loss fails it. The FIRST battle decides.
    Only category 1 (Battle Map) -- or a row we no longer serve, whose
    category is unknown -- is judged by one battle: sector missions are 4/8/16
    WIN tiers and area missions are the war's, neither of which a single
    battle end can settle. An open mission already past its deadline is
    written down as expired rather than met: a win at minute 31 is too late."""
    out = []
    for m in accepted_missions(char):
        st = mission_status(m, now, limit)
        if st == "expired" and (m.get("status") or "open") == "open":
            m["status"] = "expired"
            out.append((m, "expired"))
            continue
        if st != "open":
            continue
        if m.get("cat") not in (None, 1):
            continue                    # a sector/area accept: the ledger
        if (cats or {}).get(int(m.get("id", -1))) not in (None, 1):
            continue
        if (m.get("zone") and zone is not None
                and int(m["zone"]) != int(zone)):
            continue
        # KEY: THE BINDING. A mission with a battlefield (its row's +0x1F4, kept
        # at accept) is settled only by a battle fought THERE: `tile` is the
        # sector the war map's 0x015E named, which is also the map on_sortie
        # served. A mission without one keeps the any-battle rule, and a
        # sortie with no sector at all (FMO_SORTIE_MAPNO / resume) can only
        # settle those.
        if int(m.get("sector") or 0) and int(m["sector"]) != int(tile or -1):
            continue
        m["status"] = "met" if won else "failed"
        m["settled"] = _mission_iso(now)
        out.append((m, m["status"]))
    return out


def mission_contribution(cat, spec=None, sector_x=None):
    """The contribution a met mission of category `cat` pays on its report:
    FMO_MISSION_CONTRIB's figure for the category, times
    FMO_MISSION_CONTRIB_SECTOR_X for a sector mission (SE 050628:92, every
    sector mission pays double). 0 for an unknown category or an empty knob.
    Pure given its arguments."""
    spec = missionboard.MISSION_CONTRIB if spec is None else spec
    x = missionboard.MISSION_CONTRIB_SECTOR_X if sector_x is None else int(sector_x)
    try:
        cat = int(cat)
    except (TypeError, ValueError):
        return 0
    base = sectorwins._row_int_map(spec, "FMO_MISSION_CONTRIB").get(cat, 0)
    return max(0, int(base) * (x if cat == 2 else 1))


def mission_report_apply(char, mid, now=None, limit=None, rewards=None):
    """(mission, status before, owed pay or None) for one REPORT. MUTATES:
    "met" -> "complete" and its reward is appended to char["mission_pay"]
    ONCE; an open mission found past its deadline is written down "expired".
    `rewards` {id: (H$, MP)} backs accepts stored before rewards were
    snapshotted. (None, None, None) when there is no such accept.

    The CONTRIBUTION (pay["contrib"]) is banked on char["contribution"] here,
    once, with the status move: SE pays it at the report (guide/mission 102)
    and the paybook has no column for it. The accept's own snapshot
    (`reward_contrib`) wins; an accept without one is paid by its category
    (mission_contribution)."""
    m = mission_find(char, mid) if char else None
    if m is None:
        return None, None, None
    was = mission_status(m, now, limit)
    pay = None
    if was == "met":
        m["status"] = "complete"
        m["reported"] = _mission_iso(now)
        hs, mp = (m.get("reward_hs"), m.get("reward_mp"))
        if hs is None and mp is None:
            hs, mp = (rewards or {}).get(int(m.get("id", mid)), (0, 0))
        _c = m.get("reward_contrib")
        if _c is None:
            _c = mission_contribution(m.get("cat"))
        pay = {"id": int(m.get("id", mid)), "name": m.get("name", ""),
               "hs": int(hs or 0),
               "mp": int(mp or 0), "at": int(servicerecord._now_unix(now)),
               "contrib": max(0, int(_c or 0))}
        if pay["contrib"]:
            # the same opening balance 0x014A displays (pilotrecord.credit_money)
            _was_c = economy._econ_value("contribution", None, status.STATUS_CONTRIB,
                                         "FMO_STATUS_CONTRIB", char)[0]
            char["contribution"] = max(0, int(_was_c or 0) + pay["contrib"])
        if pay["hs"] or pay["mp"]:
            owed = char.get("mission_pay")
            # the paybook line carries H$ / MP only; the contribution is banked
            char["mission_pay"] = (owed if isinstance(owed, list) else []) + [
                {k: v for k, v in pay.items() if k != "contrib"}]
    elif was == "expired" and (m.get("status") or "open") == "open":
        m["status"] = "expired"
    return m, was, pay


def mission_cancel_apply(char, mid, now=None, limit=None):
    """(mission, status before) for one CANCEL; an active one -> cancelled.
    No fee refund and no penalty: SE's paybook has a "Mission cancellation"
    line (kind 6) but nothing here says what it charged."""
    m = mission_find(char, mid) if char else None
    if m is None:
        return None, None
    was = mission_status(m, now, limit)
    if was in missionboard.MISSION_ACTIVE:
        m["status"] = "cancelled"
        m["cancelled"] = _mission_iso(now)
    return m, was


def mission_pay_rows(char, rows_before):
    """(paybook rows, entries paid) for the owed mission rewards that fit in
    the 20-row book after `rows_before`: a kind-5 "Mission bonus" line under
    its day's header (reusing a salary header for the same day). MUTATES
    char["mission_pay"] down to what did NOT fit -- the book pays only what
    it shows, so the rest waits for the next visit."""
    owed = (char or {}).get("mission_pay")
    if not isinstance(owed, list) or not owed:
        return [], []
    room = servicerecord.S176_ROW_MAX - len(rows_before)
    have = {r[2] for r in rows_before if r[0] == servicerecord.PAY_HEADER}
    out, paid, keep = [], [], []
    for e in owed:
        if not isinstance(e, dict):
            continue
        st = int(e.get("at") or 0)
        day = st // servicerecord.DAY * servicerecord.DAY
        need = 1 + (day not in have)
        if need > room:
            keep.append(e)
            continue
        if day not in have:
            out.append((servicerecord.PAY_HEADER, 0, day, 0, 0))
            have.add(day)
        # kind 5 "Mission bonus" unless the entry names another paybook kind
        # (a counter-mission bonus is SE's kind 2, "part of the Kill bonus")
        out.append((int(e.get("kind") or missionboard.PAY_MISSION), 0, st,
                    int(e.get("hs") or 0), int(e.get("mp") or 0)))
        room -= need
        paid.append(e)
    char["mission_pay"] = keep
    return out, paid


def mission_share_entry(m, req, now=None, hs_pct=None):
    """The owed-pay entry ONE battle-group member gets when the battle-map
    mission `m` is met by a win (missionboard.MISSION_SHARE), or None when it
    pays nothing. `req` is the row's mission_requirements() entry (None = a
    row no longer served: its reward snapshot on `m` still sizes the H$).
    MP = the row's Distribution (+0x1C8 bytes 0..2); H$ = FMO_MISSION_SHARE_HS_PCT
    percent of the reward H$ (OURS). Paybook kind 3, 11:3 "Mission
    participation bonus". Pure."""
    pct = missionboard.MISSION_SHARE_HS_PCT if hs_pct is None else float(hs_pct)
    reward_hs = (m or {}).get("reward_hs")
    if reward_hs is None:
        reward_hs = (req or {}).get("reward_hs", 0)
    hs = int(round(int(reward_hs or 0) * pct / 100.0))
    mp = int((req or {}).get("share_mp") or 0)
    if not (hs or mp):
        return None
    return {"id": int((m or {}).get("id", 0)),
            "name": f"Mission share: {(m or {}).get('name', '')}",
            "hs": hs, "mp": mp, "at": int(servicerecord._now_unix(now)),
            "kind": missionboard.PAY_SHARE}


#: KEY: A BOARD ACCEPT OF A STORY MISSION (2026-09-30). The story missions are
#: the catalogue AI/F00/D16 (O.C.U.) / D18 (U.S.N.) -- fmoprogression.py reads
#: it as "the mission board's briefs" -- and fmo-missions.tsv carries each
#: one's own progress byte. The rows this server lists are AUTHORED
#: (FMO_MSN_ROWS: id, category, name; FMO_MSN_FIELDS for the sector), so the
#: only links from a board row to the catalogue are its NAME (a row authored
#: with a catalogue title) and its battlefield TILE (the row's +0x1F4 among the
#: mission's sortie tiles in fmo-gates.json). SE's scripts accept a story
#: mission through the NPC's srv_104 on byte 0 (fmo-events.tsv @step); a board
#: accept of the same mission must leave the byte where that would, 1, or the
#: NPC keeps offering it and LobbySally never matches its tiles.
#: FMO_MISSION_ACCEPT_STORY: 1 (default) = set the byte to 1 and push the flag
#: block at once (0x015A, the story gates tool's push); 2 = set it, served at
#: the next Start Game only; 0 = leave the byte alone.
MISSION_ACCEPT_STORY = _env_int("FMO_MISSION_ACCEPT_STORY", 1)


def _title_key(s):
    return " ".join(str(s or "").lower().replace("-", " ").split())


def story_mission_for(name, sector=None, nation=None, missions=None, tiles=None):
    """The catalogue mission a board row IS, or None: same title (case and
    spacing ignored) in the pilot's nation first, else the one mission of that
    nation whose sortie tiles hold the row's battlefield tile. Pure."""
    missions = progress.MISSIONS if missions is None else missions
    tiles = progress.MISSION_TILES if tiles is None else tiles
    nats = [int(nation)] if nation in (1, 2) else [1, 2]
    rows = [m for n in nats for m in missions.get(n, []) if m.get("own") is not None]
    by_name = [m for m in rows if _title_key(m["title"]) == _title_key(name)]
    if len(by_name) == 1 or (by_name and len({m["own"] for m in by_name}) == 1):
        return by_name[0]
    if sector:
        by_tile = [m for m in rows if int(sector) in tiles.get(m["own"], ())]
        if len(by_tile) == 1:
            return by_tile[0]
    return None


def story_accept_apply(char, name, sector=None, nation=None, missions=None, tiles=None):
    """Mark a board accept of a story mission on the pilot's flags: its own
    byte 0 -> 1. MUTATES char["flags"]. -> (mission or None, what); the
    mission is returned only when the byte moved."""
    if not MISSION_ACCEPT_STORY or not fmostore or char is None:
        return None, "FMO_MISSION_ACCEPT_STORY=0" if not MISSION_ACCEPT_STORY else "no pilot"
    m = story_mission_for(name, sector, nation, missions, tiles)
    if m is None:
        return None, "not a story mission (no catalogue title or tile matches %r)" % name
    fl = fmostore.flags_bytes(char.get("flags")).ljust(256, b"\0")
    was = fl[m["own"]]
    if was != 0:
        return None, "'%s' byte[%d] is already %d" % (m["title"], m["own"], was)
    fmostore.set_flag_byte(char, m["own"], 1)
    return m, "'%s' byte[%d] 0 -> 1 (accepted)" % (m["title"], m["own"])


def mission_already_accepted(char, mid):
    """Has this pilot already accepted mission `mid`?

    SE has a code for this exact state -- **-4**, systext 27:0 "You have
    already accepted this mission" -- and until accepts were stored there was
    no way to know it, which is also why charging a fee was unsafe: the same
    row could be bought twice.
    """
    # Only an ACTIVE accept blocks: a completed, failed, expired or cancelled
    # mission can be taken again. Every accept stored before statuses existed
    # reads as "open", so for those this is exactly the old test.
    return any(int(m.get("id", -1)) == int(mid)
               and mission_status(m) in missionboard.MISSION_ACTIVE
               for m in accepted_missions(char))


def mission_accept_verdict(req, char):
    """(code, why) for one accept. `code` None means ACCEPT.

    Two of the three gates SE documents, each answered with the code its own
    systext hangs off (0x611C4B30): rank +2, money +1.

    KEY: 2026-09-30, SE's own words settle what +0x1DC is: the Fee is MP
    (guide/mission 「Fee ： ミッションを受けるために必要なMP」; guide/addmanual:
    the condition changed from 「必要階級 + 必要金額」 to 「必要階級 + 必要MP」).
    With FMO_MISSION_FEE_MP (default) the second gate is MP, refused with -7;
    the money gate below survives only for FMO_MISSION_FEE_MP=0. The MP gate
    the next warning retires was a different mistake: it gated on the REWARD
    (+0x1E8), not on the Fee.

    WARNING: **THE MP GATE IS GONE, and its removal is the whole lesson here.** This
    function used to judge `+0x1E8` as a required MP. It is the **REWARD** MP:
    live 2026-09-12, serving 30 there drew *"Reward: MP30/H$1200"* in the
    Mission Info panel (22:8). Gating acceptance on what a mission PAYS is
    backwards, and it came from reading a COLUMN HEADER ("MP") as a meaning.
    The columns were bound correctly; the semantics were assumed. Code -7 and
    its sentence stay in MISSION_REFUSALS, ready for the day the field behind
    22:5 "Required MP" is actually found -- it is not `+0x1E8`.

    WARNING: And by the same standard the two that remain are the columns' own labels,
    not verified requirements: "Rank" (+0x1E4, drawn through the rank table) and
    "Fee" (+0x1DC) read as a requirement and a cost, and the panel's own
    22:4 "Required rank" line is the oracle that would settle it.

    WARNING: THE ORDER IS OURS. SE's client never evaluates these -- they are server
    verdicts -- so nothing establishes which
    refusal a retail server showed a pilot who failed two at once. Rank, then
    money, then MP is a choice, recorded here as one.

    WARNING: A requirement of ZERO is not a requirement. Rows authored without
    FMO_MSN_FIELDS carry zeros in all three, so an ungated row stays acceptable
    and this function is a no-op for every row served before it existed.
    """
    _pen = penalty.clearance_refusal(char, "mission")
    if _pen is not None:
        return _pen
    _iss = (req or {}).get("issuer")
    if (_iss and (req or {}).get("nation") and _nation(char)
            and _nation(char) != req["nation"]):
        # an ORDER is its issuer's nation's; 0 = 27:4 "The operation failed."
        return 0, (f"{req['name']!r} is a nation {req['nation']} order and this "
                   f"pilot is nation {_nation(char)}")
    if (_iss and char and _iss[1] is not None and _iss[1] == char.get("id")
            and _iss[2] == "%s.%s" % (char.get("first") or "", char.get("last") or "")):
        return -5, f"{req['name']!r} is this pilot's own order (27:5)"
    rank, rank_src = economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char)
    money, money_src = economy.wallet_money(char)
    if req["rank"] and rank < req["rank"]:
        return 2, (f"rank {rank} < the row's required {req['rank']} "
                   f"({ranks.rank_name(rank)} < {ranks.rank_name(req['rank'])}); "
                   f"pilot rank from {rank_src}")
    if missionboard.MISSION_FEE_MP:
        # KEY: SE's rule since MP: 「必要階級 + 必要MP」 (guide/addmanual), and the
        # Fee column IS that MP (guide/mission: 「Fee ： ミッションを受けるために
        # 必要なMP」). -7 = 27:7 "You do not have enough MP to accept this
        # mission." Money is not a gate at all any more.
        mp, mp_src = economy.wallet_mp(char)
        if req["fee"] and mp < req["fee"]:
            return -7, (f"MP {mp} < the row's Fee {req['fee']} MP "
                        f"(FMO_MISSION_FEE_MP=1); pilot MP from {mp_src}")
        return None, (f"rank {rank} >= {req['rank']} and MP {mp} >= Fee "
                      f"{req['fee']} -- every gate passed (the row PAYS MP "
                      f"{req['reward_mp']}/H$ {req['reward_hs']}, which is a "
                      f"reward and is never gated on)")
    if req["fee"] and money < req["fee"]:
        return 1, (f"H$ {money} < the row's fee {req['fee']}; "
                   f"pilot money from {money_src}")
    return None, (f"rank {rank} >= {req['rank']} and H$ {money} >= "
                  f"{req['fee']} -- every gate passed (the row PAYS MP "
                  f"{req['reward_mp']}/H$ {req['reward_hs']}, which is a "
                  f"reward and is never gated on)")


def mission_place_refusal(cat, zone, place, places_on=None):
    """(code, why) when a category-`cat` accept is made in the wrong place
    (FMO_MISSION_PLACE), else (None, why). `zone` is the zone id the pilot
    was last granted (rooms.WORLD_ZONES), `place` its (zone, kind, instance)
    (move.WORLD_PLACES). Pure given its arguments.

    Playing Manual p.59/60: Area missions only from the Senior Officer in the
    Strategy Room (the Briefing Room, place kind 2, which the client opens
    only in Frontline area 10 for rank >= Major); Battle Map and Sector
    missions from the Intelligence Officer in an Occupied or Contested Zone.
    A place or zone we do not know is not refused: a gate cannot judge what
    it cannot see, and the row's own rank gate still applies."""
    _on = move.PLACES if places_on is None else places_on
    code = missionboard.MISSION_PLACE_CODE
    if cat == 3:
        if not _on or not place:
            return None, ("no place on record (FMO_PLACES=0 or no 0x0153 yet), "
                          "so the Strategy Room rule cannot be judged")
        if int(place[1]) != 2:
            return code, (f"an Area mission is the Senior Officer's, in the "
                          f"Strategy Room (Briefing Room, place kind 2); the "
                          f"pilot stands in {move.place_name(place)}")
        return None, f"in {move.place_name(place)}, the Senior Officer's room"
    if cat in (1, 2):
        if zone is None:
            return None, "no zone on record, so the counter rule cannot be judged"
        if int(zone) // 100 in missionboard.MISSION_PLACE_NO_COUNTER:
            return code, (f"Battle Map / Sector missions are the Intelligence "
                          f"Officer's, in an Occupied or Contested Zone; zone "
                          f"{zone} is kind {int(zone) // 100} "
                          f"(1/3 Controlled, 6 Coliseum)")
        return None, f"zone {zone} has a mission counter"
    return None, f"category {cat!r}: no place rule"


# --------------------------------------------------------------------------- #
# ORDERED (DERIVED) MISSIONS -- see missionboard's ORDER block for the wire
# --------------------------------------------------------------------------- #
#: KEY: A DERIVED MISSION LIVES ON ITS ISSUER'S `missions` LIST, marked
#: "derived", so the issuer's own Accepted list (0x018E) shows it with state
#: 1 "Ordered" and a Cancel of it comes back with its key. Its status is
#: ordered -> taken (another pilot's accept of its id is on record) /
#: cancelled / expired; the Order MP comes back on cancel or expiry and only
#: while it is still "ordered" (guide/mission 154-155). ORDERS mirrors every
#: one for the All Mission List other pilots see and the accept gate:
#: {derived id: (issuer account, entry)}, rebuilt from the store on first use.
ORDERS = {}
_orders_loaded = []
#: {derived id: taker account}: a take is permanent, so it is cached.
ORDER_TAKEN = {}
ORDER_TAKER_NAME = {}


def _all_rosters():
    db = charstore.use_db() if charstore.CHAR_STORE else None
    accts = fmostore.store_accounts() if (db and fmostore is not None) else []
    return ((a, charstore.load_roster(a)) for a in accts)


def order_registry(rosters=None):
    """ORDERS, loaded from the store once (or from `rosters` for a test)."""
    if rosters is not None or not _orders_loaded:
        _orders_loaded.append(True)
        for acct, roster in (_all_rosters() if rosters is None else rosters):
            for c in roster or ():
                for m in accepted_missions(c):
                    if m.get("derived"):
                        ORDERS[int(m["derived"])] = (acct, m)
    return ORDERS


def order_template_id(cat_d, mid):
    return missionboard.ORDER_TPL_BASE | ((int(cat_d) & 0xFF) << 16) | (int(mid) & 0xFFFF)


def order_template_parse(tid):
    """(derived category, source mission id) of a template id, or None."""
    if (int(tid) & 0xFF000000) != missionboard.ORDER_TPL_BASE:
        return None
    return (int(tid) >> 16) & 0xFF, int(tid) & 0xFFFF


def order_source_ok(m, now=None):
    """The derived category an ACTIVE accept `m` may order (2 -> 1 battle
    map, 3 -> 2 sector; guide/mission 10 and 15), else None."""
    if not m or m.get("derived") or mission_status(m, now) not in missionboard.MISSION_ACTIVE:
        return None
    return {2: 1, 3: 2}.get(m.get("cat"))


def order_template(tid, reqs=None):
    """The template dict for template id `tid`, or None: name, category, op
    time, reward MP / H$ (FMO_ORDER_REWARD_PCT of the source's), base MP."""
    got = order_template_parse(tid)
    if got is None:
        return None
    cat_d, mid = got
    req = (mission_requirements() if reqs is None else reqs).get(mid)
    if req is None:
        return None
    pct = missionboard.ORDER_REWARD_PCT
    return {"tid": int(tid), "cat": cat_d, "src": mid, "name": req["name"],
            "op_time": missionboard.MISSION_DEADLINE,
            "reward_mp": int(req.get("reward_mp") or 0) * pct // 100,
            "reward_hs": int(req.get("reward_hs") or 0) * pct // 100,
            "base_mp": missionboard.ORDER_BASE_MP}


def order_template_records(ids, reqs=None):
    """The 524-B records for a kind-5 query's ids (unknown ids skipped)."""
    out = []
    for tid in ids:
        t = order_template(tid, reqs)
        if t is not None:
            out.append(fmomsn.order_template_record(
                t["tid"], t["name"], t["cat"], t["op_time"], t["reward_mp"],
                t["reward_hs"], t["base_mp"]))
    return out


def order_reward(tpl_reward, pct, chosen, base):
    """The derived mission's reward: the template's x percent / 100 x chosen
    / base Order MP ("calculated from the Order MP", guide/mission 123)."""
    if not base:
        return int(tpl_reward) * int(pct) // 100
    return int(tpl_reward) * int(pct) * int(chosen) // (100 * int(base))


def order_taker(did, rosters=None):
    """The account holding an accept of derived mission `did`, or None."""
    if did in ORDER_TAKEN:
        return ORDER_TAKEN[did]
    for acct, roster in (_all_rosters() if rosters is None else rosters):
        for c in roster or ():
            for m in accepted_missions(c):
                if not m.get("derived") and int(m.get("id", -1)) == int(did):
                    ORDER_TAKEN[did] = acct
                    ORDER_TAKER_NAME[did] = ("%s %s" % (c.get("first") or "",
                                                        c.get("last") or "")).strip()
                    return acct
    return None


def order_accept_refusal(did, account, now=None, rosters=None):
    """(code, why) when derived mission `did` can no longer be accepted, else
    None (also for an id that is no order). SE's own codes: -3 = 27:6 "This
    mission has already been accepted." for an order another pilot took, -4
    = 27:0 "You have already accepted this mission." when the taker is this
    account, 0 = 27:4 "The operation failed." for one cancelled or expired.

    KEY: WHY THIS IS NEEDED. An order leaves order_requirements() the moment
    it is taken, so a second accept of its id found no row and the gate's
    "no requirements to judge -- accepting" arm let it through: two pilots
    held one derived mission."""
    try:
        did = int(did)
    except (TypeError, ValueError):
        return None
    got = order_registry(rosters).get(did)
    if got is None:
        return None
    _acct, e = got
    st = order_status(e, now, rosters)
    if st == "ordered":
        return None
    if st == "taken":
        taker = order_taker(did, rosters)
        if taker is not None and account is not None and taker == account:
            return -4, (f"derived mission {did} {e.get('name')!r} is already "
                        f"this pilot's accept")
        return -3, (f"derived mission {did} {e.get('name')!r} was already "
                    f"accepted by "
                    f"{ORDER_TAKER_NAME.get(did) or taker or 'another pilot'}")
    return 0, f"derived mission {did} {e.get('name')!r} is {st}, not open"


def _order_taker_accept(did, rosters=None):
    """The taker's own accept of derived mission `did` (the non-derived entry
    with that id on the taker's roster), or None."""
    acct = order_taker(did, rosters)
    if acct is None:
        return None
    if rosters is not None:
        pool = [r for a, r in rosters if a == acct]
    else:
        live = _live_roster(acct)
        pool = [live if live is not None else charstore.load_roster(acct)]
    for roster in pool:
        for c in roster or ():
            for m in accepted_missions(c):
                if not m.get("derived") and int(m.get("id", -1)) == int(did):
                    return m
    return None


def _live_roster(acct):
    """The in-memory roster of a live session of `acct`, or None: it can be
    newer than the store between that session's commits."""
    try:
        from . import trade
        for s in list(trade.LIVE_SESSIONS.values()):
            if getattr(s, "account", None) == acct:
                r = getattr(s, "roster", None)
                if isinstance(r, list):
                    return r
    except Exception:
        pass
    return None


#: {derived id: (unix time it was won or None = settled without a win, the
#: taker's nation or None)}: a met / complete / failed / expired take never
#: changes, so it is cached.
ORDER_WON = {}


def sector_order_wins(m, since, until, rosters=None):
    """How many of the battle-map missions the sector accept `m` ORDERED were
    won inside [since, until] (FMO_SECTOR_OWN_WINS; Playing Manual p.60).
    An order is this accept's when its from_key is the accept's key and,
    where stored, its from_at is the accept's own stamp. A win = the taker's
    accept of the order reached met / complete, stamped by its `settled`."""
    key, at = mission_key(m), m.get("at")
    own = _nation(m)
    n = 0
    for did, (_acct, e) in list(order_registry(rosters).items()):
        if e.get("from_key") != key or int(e.get("cat") or 0) != 1:
            continue
        if (e.get("status") or "ordered") != "ordered":
            continue                    # cancelled / expired: nobody took it
        if e.get("from_at") is not None and e.get("from_at") != at:
            continue
        if rosters is None and did in ORDER_WON:
            t, tn = ORDER_WON[did]
        else:
            a = _order_taker_accept(did, rosters)
            st = (a or {}).get("status") or "open"
            t, tn = None, _nation(a)
            if st in ("met", "complete"):
                t = _mission_epoch(a, "settled") or _mission_epoch(a, "reported")
            if rosters is None and a is not None and st != "open":
                ORDER_WON[did] = (t, tn)
        # KEY: ONLY THE HOLDER'S NATION'S WINS (Playing Manual p.60). An order
        # taken by a pilot of the other nation (the list once showed every
        # nation's orders) is not this sector's progress, whatever it won.
        if own is not None and tn is not None and tn != own:
            continue
        if t is not None and int(since) <= t <= int(until):
            n += 1
    return n


def order_status(m, now=None, rosters=None):
    """ordered / taken / cancelled / expired for a derived entry."""
    st = m.get("status") or "ordered"
    if st != "ordered":
        return st
    if order_taker(int(m["derived"]), rosters) is not None:
        return "taken"
    at = _mission_epoch(m)
    lim = int(m.get("limit") or 0)
    if lim and at is not None and servicerecord._now_unix(now) > at + lim:
        return "expired"
    return "ordered"


def order_new_id(used=None):
    used = set(order_registry() if used is None else used)
    for did in range(missionboard.ORDER_ID_BASE, missionboard.ORDER_ID_END):
        if did not in used:
            return did
    return None


def order_create(char, account, fields, issuer="", comment="", now=None,
                 reqs=None, rosters=None):
    """One ORDER from `fields` {body offset: u32} (the 0x0194 body). Checks,
    takes the Order MP and puts the derived entry on `char`. MUTATES char.
    -> (entry, None, why) or (None, refusal code, why)."""
    src = mission_find(char, fields.get(0x004, 0)) if char else None
    if order_source_ok(src, now) is None:
        # the dialog always names row 0; take the pilot's newest orderable
        # accept when row 0 is not one
        src = next((m for m in reversed(accepted_missions(char))
                    if order_source_ok(m, now) is not None), None)
    if src is None:
        return None, missionboard.ORDER_CODE_FAIL, (
            "no active SECTOR or AREA mission on this pilot to order from "
            "(guide/mission: only their takers issue derived missions)")
    cat_d = order_source_ok(src, now)
    tpl = order_template(order_template_id(cat_d, src.get("id", 0)), reqs)
    if tpl is None:
        return None, missionboard.ORDER_CODE_FAIL, (
            f"{src.get('name')!r} (id {src.get('id')}) is no row we serve, so "
            f"there is no template to derive from")
    live = [e for _a, e in order_registry(rosters).values()
            if e.get("from_key") == mission_key(src)
            and order_status(e, now, rosters) in ("ordered", "taken")]
    if missionboard.ORDER_CAP and len(live) >= missionboard.ORDER_CAP:
        return None, missionboard.ORDER_CODE_FAIL, (
            f"{len(live)} live order(s) from {src.get('name')!r} already, the "
            f"cap is {missionboard.ORDER_CAP} (FMO_ORDER_CAP, ours)")
    base = tpl["base_mp"]
    pct = int(fields.get(0x144) or missionboard.ORDER_PCT)
    chosen = int(fields.get(0x140) or 0)
    lo, hi = max(1, base * pct // 200), max(1, base * pct * 3 // 200)
    if not lo <= chosen <= hi:
        # the dialog's slider runs from half to 1.5 x base x percent (0x611CA3DD)
        chosen = max(lo, min(hi, chosen or base * pct // 100))
    mp = economy.wallet_mp(char)[0]
    if mp < chosen:
        return None, missionboard.ORDER_CODE_MP, f"MP {mp} < the Order MP {chosen}"
    did = order_new_id()
    if did is None:
        return None, missionboard.ORDER_CODE_FAIL, "no derived mission id left"
    was, now_mp = economy.spend_mp(char, chosen)
    e = {"id": did, "key": did, "derived": did, "name": tpl["name"],
         "status": "ordered", "cat": cat_d, "at": _mission_iso(now),
         "limit": tpl["op_time"],
         "order_mp": chosen, "base_mp": base, "pct": pct,
         "reward_mp": order_reward(tpl["reward_mp"], pct, chosen, base),
         "reward_hs": order_reward(tpl["reward_hs"], pct, chosen, base),
         "from_key": mission_key(src), "from_id": int(src.get("id", 0)),
         # the source accept's own stamp: keys repeat across pilots (the
         # ordinal is per pilot), so key + stamp is what names the source
         # accept for sector_order_wins
         "from_at": src.get("at"),
         "issuer": issuer, "issuer_id": (char or {}).get("id"),
         "account": account, "comment": comment, "fee": 0}
    for k in ("sector", "zone", "nation"):
        if src.get(k):
            e[k] = int(src[k])
    char["missions"] = accepted_missions(char) + [e]
    ORDERS[did] = (account, e)
    return e, None, (f"derived mission {did} {tpl['name']!r} (category {cat_d}) "
                     f"from {src.get('name')!r}: Order MP {chosen} taken, MP "
                     f"{was} -> {now_mp}; pays MP {e['reward_mp']} / H$ "
                     f"{e['reward_hs']}")


def order_release(char, m, to, now=None):
    """Close derived entry `m` as `to` (cancelled / expired), REFUNDING the
    Order MP -- only from "ordered" (guide/mission 154-155). MUTATES.
    -> refunded MP (0 when nothing was due)."""
    if (m.get("status") or "ordered") != "ordered" \
            or order_taker(int(m["derived"])) is not None:
        return 0
    m["status"] = to
    m[to] = _mission_iso(now)
    n = int(m.get("order_mp") or 0)
    if n and not m.get("refunded"):
        char["mp"] = economy.wallet_mp(char)[0] + n
        m["refunded"] = n
        return n
    return 0


def order_find(char, did):
    return next((x for x in accepted_missions(char)
                 if x.get("derived") and int(x["derived"]) == int(did) & 0xFFFF), None)


def order_cancel_apply(char, did, now=None):
    """(entry, status before, refund) for a CANCEL of derived key `did`, or
    (None, None, 0) when it is not one of this pilot's orders."""
    m = order_find(char, did)
    if m is None:
        return None, None, 0
    was = order_status(m, now)
    return m, was, (order_release(char, m, "cancelled", now) if was == "ordered" else 0)


def order_expire_apply(char, now=None):
    """Refund every order of this pilot whose time ran out untaken.
    -> [(entry, refund)]; MUTATES."""
    return [(m, order_release(char, m, "expired", now))
            for m in accepted_missions(char)
            if m.get("derived") and (m.get("status") or "ordered") == "ordered"
            and order_status(m, now) == "expired"]


def order_requirements(now=None):
    """{derived id: requirements} for every order still open, in the shape of
    mission_requirements(): the accept gate and the All Mission List read it."""
    out = {}
    for did, (acct, e) in list(order_registry().items()):
        if order_status(e, now) != "ordered":
            continue
        out[did] = {"name": e.get("name", ""), "cat": e.get("cat"),
                    "zone": e.get("zone"), "wins": 1 if e.get("cat") == 1 else warmap.MISSION_WINS,
                    "rank": 0, "fee": int(e.get("fee") or 0),
                    "reward_mp": int(e.get("reward_mp") or 0),
                    "reward_hs": int(e.get("reward_hs") or 0),
                    "share_mp": 0, "sector": int(e.get("sector") or 0),
                    "nation": _nation(e),
                    "issuer": (acct, e.get("issuer_id"), e.get("issuer"))}
    return out


def order_list_records(category, zone=None, now=None, nation=None):
    """The 536-B All Mission List rows of the open orders in `category`.
    `nation` (1/2, the asking pilot's) leaves out the other nation's orders:
    a derived mission is its issuer's nation's (guide/mission: the Sector /
    Area taker orders for his own side)."""
    if not missionboard.ORDER:
        return []
    nation = nation if nation in (1, 2) else None
    out = []
    for did, req in sorted(order_requirements(now).items()):
        if req["cat"] != category:
            continue
        if zone is not None and req.get("zone") and int(req["zone"]) != int(zone):
            continue
        if nation is not None and req.get("nation") not in (None, nation):
            continue
        out.append(fmomsn.mission_record(req["name"], category, fields={
            fmomsn.MISSION_REWARD_MP: req["reward_mp"],
            fmomsn.MISSION_REWARD_HS: req["reward_hs"],
            fmomsn.MISSION_SECTOR: req["sector"]}, mid=did))
    return out


def order_row_fields(m, now=None):
    """Extra 692-B row fields for the Order path: on an orderable accept its
    template id (+0x08, which the kind-5 query sends) and percent (+0x38);
    on a derived row the commander (+0x44) and assignee (+0x74) names."""
    if not missionboard.ORDER or not m:
        return {}
    if m.get("derived"):
        f = {missionboard.ROW_COMMANDER: str(m.get("issuer") or "")}
        if order_taker(int(m["derived"])) is not None:
            f[missionboard.ROW_ASSIGNEE] = ORDER_TAKER_NAME.get(int(m["derived"]), "")
        return f
    cat_d = order_source_ok(m, now)
    if cat_d is None:
        return {}
    return {missionboard.ROW_ORDER_TPL: order_template_id(cat_d, m.get("id", 0)),
            missionboard.ROW_ORDER_PCT: missionboard.ORDER_PCT}


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    areatargets, community, economy, missionboard, progress, ranks, sectorwins, servicerecord,
    status, warmap, warstate,
)
from . import charstore  # noqa: E402  (the order registry reads every roster)
from . import move  # noqa: E402  (the place a mission is accepted in, FMO_MISSION_PLACE)
from . import penalty  # noqa: E402  (a penalised pilot cannot accept, D64 59-61)
