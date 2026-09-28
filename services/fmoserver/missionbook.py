"""A pilot's accepted missions: keys, deadlines, status, battle results, reports and pay."""
import datetime
import struct
import time
from .deps import fmomsn


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
            # the target sector: an ARE tile (row*1000+col), 0 = none. For a
            # battle-map mission it is the BATTLEFIELD -- see
            # mission_battle_apply and the map selector's icon.
            "sector": struct.unpack_from(
                "<I", rec, fmomsn.MISSION_SECTOR)[0],
        }
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


def mission_status(m, now=None, limit=None, ledger=None):
    """The stored status with the DEADLINE applied: an "open" mission past
    at + limit is "expired" whether or not anything has written that down.
    A category-2 (sector) accept is "met" once the win ledger holds its
    `needed` wins by its nation on its (zone, sector) after its accept and
    inside its deadline -- whoever fought them (SE: the taker need not
    sortie)."""
    st = (m or {}).get("status") or "open"
    cat = (m or {}).get("cat")
    lim = mission_deadline(cat) if limit is None else int(limit)
    if st == "open":
        at = _mission_epoch(m)
        _now = servicerecord._now_unix(now)
        if cat == 2 and at is not None and int(m.get("needed") or 0) > 0:
            until = min(_now, at + lim) if lim else _now
            got = sectorwins.sector_wins_between(m.get("zone"), m.get("sector"),
                                                 m.get("nation"), at, until, ledger)
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
    return {missionboard.MR_STATE: state, missionboard.MR_RESULT: result, missionboard.MR_LIMIT: lim,
            missionboard.MR_START: _mission_epoch(m) or 0}


def accepted_list_rows(missions, now=None):
    """Rows for reply_018e_short. Knob OFF: [(id, name)], byte-identical to
    what the list has always served. ON: active missions first (oldest
    first), then closed ones newest first, each with its state fields -- so a
    pilot's live missions are never pushed past the 21-row cap by history."""
    if not missionboard.MISSION_REPORT:
        return [(m.get("id", 0), m.get("name", "")) for m in missions]
    act = [m for m in missions if mission_status(m, now) in missionboard.MISSION_ACTIVE]
    done = [m for m in missions if mission_status(m, now) not in missionboard.MISSION_ACTIVE]
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
    closed = [m for m in cur if mission_status(m) not in missionboard.MISSION_ACTIVE]
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


def mission_report_apply(char, mid, now=None, limit=None, rewards=None):
    """(mission, status before, owed pay or None) for one REPORT. MUTATES:
    "met" -> "complete" and its reward is appended to char["mission_pay"]
    ONCE; an open mission found past its deadline is written down "expired".
    `rewards` {id: (H$, MP)} backs accepts stored before rewards were
    snapshotted. (None, None, None) when there is no such accept."""
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
        pay = {"id": int(m.get("id", mid)), "name": m.get("name", ""),
               "hs": int(hs or 0),
               "mp": int(mp or 0), "at": int(servicerecord._now_unix(now))}
        if pay["hs"] or pay["mp"]:
            owed = char.get("mission_pay")
            char["mission_pay"] = (owed if isinstance(owed, list) else []) + [pay]
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
    rank, rank_src = economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char)
    money, money_src = economy.wallet_money(char)
    if req["rank"] and rank < req["rank"]:
        return 2, (f"rank {rank} < the row's required {req['rank']} "
                   f"({ranks.rank_name(rank)} < {ranks.rank_name(req['rank'])}); "
                   f"pilot rank from {rank_src}")
    if req["fee"] and money < req["fee"]:
        return 1, (f"H$ {money} < the row's fee {req['fee']}; "
                   f"pilot money from {money_src}")
    return None, (f"rank {rank} >= {req['rank']} and H$ {money} >= "
                  f"{req['fee']} -- every gate passed (the row PAYS MP "
                  f"{req['reward_mp']}/H$ {req['reward_hs']}, which is a "
                  f"reward and is never gated on)")


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    areatargets, community, economy, missionboard, ranks, sectorwins, servicerecord, status,
    warmap,
)
