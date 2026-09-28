"""The community / mission server ("Fshira") on the same port: mission rows and the Scramble
Board."""
import os
import socket
import time
from .deps import fmomsn
from .knobs import _env_int
from .wirelog import hexdump, log
from . import wirelog


# --------------------------------------------------------------------------- #
# THE COMMUNITY / MISSION SERVER ("Fshira") -- FMO's SECOND SERVER.
#
# KEY: The "All Mission List" the war map opens onto is NOT the lobby's
# 0x018D/0x018E table. It is streamed by a separate TCP service whose address
# the client takes from our own 0x0322 reply (payload +0x28 -> ctx+0x769C) --
# which fmo.py already fills with FMO_NEXT_HOST:FMO_NEXT_PORT, i.e. US. So the
# client has been opening a second connection to this very port, sending a
# 40-byte Blowfish+MD5 frame, and being dropped:
#
#     2026-09-06T13:27:13Z  127.0.0.1:54655 CONNECT (hold 900.0s)
#     ...                   msg=0xA0C5 ... chk=0x9346 MISMATCH
#     ...                   no handler for msg=0xA0C5
#     2026-09-06T13:34:00Z  client closed / saved 40B
#
# Eight such connections are in prod's log. `fmomsn.py` decodes the protocol
# (key "903094117gekisen", MD5-authenticated) and its --selftest verifies
# against one of those captures byte for byte. Everything ABOUT the wire is
# proved; nothing SERVED here has ever been on one.
#
# WARNING: Default is `1` = complete the handshake with an EMPTY list. That is a
# deliberate default change and the reasoning is: the on-screen result is
# identical to today (systext 21:16 "No Missions Found", drawn by 0x611CB9D0
# when the row count is 0), while the client's job queue -- which today never
# pops, because the manager only advances on a reply -- is released. `0`
# restores the old silence for an A/B.
#: WARNING: `.strip() or "1"`, not a plain default: compose passes "${FMO_MSN:-}",
#: so an unset prod .env hands us an EMPTY STRING, not a missing key --
#: `os.environ.get("FMO_MSN", "1")` would then read "" and silently turn
#: the feature OFF while every doc said it was on. Same trap as
#: FMO_SORTIE_PORT's int("") and FMO_ANSWER_015E's idiom above; caught
#: here by reading the knob back out of the RUNNING container.
MSN = os.environ.get("FMO_MSN", "").strip() or "1"

#: '|'-separated mission rows, each `[<id>:]<name>`. Each becomes one 536-byte
#: record with its name at +0x04, the category the client asked for echoed into
#: +0x1C8, and its MISSION ID at +0x00. Empty (the default) serves a truthful
#: empty list.
#:
#: KEY: THE ID IS WHAT AN ACCEPT CARRIES BACK. `record+0x00` is read into
#: `wrapper+0x08` (0x611C98A0) and sent as the 0x018A body's first dword
#: (0x611C95EA) -- so a row with id 0, which is every row this server served
#: before 2026-09-12, produces an accept keyed to nothing. An id is therefore
#: assigned even when the operator gives only a name: **the default is the row's
#: 1-based index**, never 0. Write `7:Recon Alpha` to choose one.
MSN_ROWS = [s for s in os.environ.get("FMO_MSN_ROWS", "").split("|") if s]

#: Raw pokes into the record, `row:offset=value` (offset and value accept 0x).
#: The six numeric columns are NOT named anywhere in this codebase because
#: their meaning is not earned -- see fmomsn.py. This is how you set them
#: without pretending to know which is which.
MSN_FIELDS = os.environ.get("FMO_MSN_FIELDS", "")

#: WARNING: How to answer a community op whose reply shape we have NOT earned.
#: 'silent' (default) = say nothing. 'end' = reply 0x1B, WHICH KILLED THE
#: CLIENT on 2026-09-06T17:45:57Z (op 6, the war map's mode-0 kind-0 job) and
#: is kept only as the reproduce switch. See msn_reply().
MSN_UNKNOWN = os.environ.get("FMO_MSN_UNKNOWN", "").strip() or "silent"
#: The code op 0x15 carries at payload+0x0C. 0 is the least presumptuous:
#: 0x611B0120 maps -10..0 to systext group 88 (the ARENA's strings) and
#: everything else to 0, and this screen has no business showing those.
MSN_REFUSE_CODE = _env_int("FMO_MSN_REFUSE_CODE", "0")
#: How many zero-filled 76-byte records FMO_MSN_UNKNOWN=probe sends.
MSN_PROBE_ROWS = _env_int("FMO_MSN_PROBE_ROWS", "1")

#: KEY: PER-OP END. Ops answered with 0x1B (END) even while FMO_MSN_UNKNOWN is
#: 'silent'. Comma list, hex ok: `FMO_MSN_END_OPS=0x08`.
#:
#: WHY THIS IS NOT THE 09-06 KILL. That crash was op **6**, the war map's
#: mode-0 KIND-0 job, whose callback does not tolerate a NULL record. It does
#: NOT generalise: each job kind passes its OWN completion callback, and the
#: kind-2 (op 8, the SECTOR MISSION LIST) job is created at 0x611C5596 with
#: callback **0x6123CAD0**, which opens with
#:     mov edi,[esp+0xC] ; test edi,edi ; je 0x6123CAF5
#: -- i.e. it BRANCHES ON A NULL RECORD and takes a finalise path
#: (0x6123BBE0, [obj+0xE4]=0, then 0x6123BD60 / 0x6123C700 / 0x6123C7C0 to
#: refresh the view). Handing THAT callback a NULL is how a list ENDS, not a
#: fault. So 0x1B is safe for op 8 and fatal for op 6, and a blanket
#: FMO_MSN_UNKNOWN=end cannot tell them apart -- hence per-op.
#: WARNING: STATIC. No client has been answered on op 8. Empty by default.
MSN_END_OPS = frozenset(
    int(x, 0) for x in os.environ.get("FMO_MSN_END_OPS", "").replace(" ", "")
    .split(",") if x)

#: FMO_MSN=mark fills every dword of every row with its own offset, so one
#: look at the Type/Name/MP/H$/Rank/Fee columns names each of them. The name
#: is written after the marker, so the Name column stays readable.
MSN_MARK = MSN == "mark"
MSN_ON = MSN not in ("0", "false", "")


def _msn_fields():
    """FMO_MSN_FIELDS -> {row_index: {offset: value}}. A malformed clause is
    dropped loudly rather than silently changing what a row means."""
    out = {}
    for clause in MSN_FIELDS.split(","):
        clause = clause.strip()
        if not clause:
            continue
        try:
            where, val = clause.split("=", 1)
            row, off = where.split(":", 1)
            out.setdefault(int(row, 0), {})[int(off, 0)] = int(val, 0)
        except ValueError:
            log(f"[fmo] WARNING: FMO_MSN_FIELDS: ignoring malformed clause {clause!r} "
                f"(want row:offset=value, e.g. 0:0x1E4=1200)")
    return out


def msn_row_spec(spec, index):
    """One FMO_MSN_ROWS entry -> (mission_id, category, name).

    `[<id>[/<category>]:]<name>`; a bare name takes the row's 1-BASED INDEX as
    its id. Zero is never handed out by default, because an accept keyed to 0 is
    an accept keyed to nothing (fmomsn.MISSION_ID). An explicit `0:Name` is
    honoured -- that is how you reproduce the pre-2026-09-12 behaviour.

    KEY: THE CATEGORY IS WHICH LIST THE MISSION IS IN. `0x611CC040` shows the
    board's refresh sending **the view's own mode** as the query category
    (`shl eax,0x18`), and the view then drops any record whose
    `+0x1C8 >> 24` is not that mode (`0x611C98E1`) -- except modes 3 and 4,
    which take the `0x611AF5E0` path with a hard-coded category 3 and keep
    everything. The menu entries are UI commands 0x100F "Battle Map Mission",
    0x1010 "Sector Mission" and 0x1011 "Area Mission".

    WARNING: `None` (the default) means ECHO whatever the client asked for, and that
    is what made **all three lists show the same two missions** -- a row that
    answers to every category is in every list. It is kept as the default only
    so an uncategorised row still appears somewhere; give a row a category the
    moment you know which list it belongs in.

    WARNING: Only a LEADING integer before the first ':' is an id, so a name that
    merely contains a colon is left whole."""
    head, sep, tail = spec.partition(":")
    if sep:
        idpart, slash, catpart = head.partition("/")
        try:
            mid = int(idpart, 0)
            cat = int(catpart, 0) if slash else None
            return mid, cat, tail
        except ValueError:
            pass
    return index + 1, None, spec


def msn_row_table():
    """[(mission_id, category, name)] for the authored rows, ids resolved.
    A category of None means "echo whatever the client asked for"."""
    return [msn_row_spec(spec, i) for i, spec in enumerate(MSN_ROWS)]


def msn_rows(category, mapkind=None):
    """The rows we serve, as 536-byte records. `mapkind` = the query's own
    zone: a row FMO_MSN_ZONES puts in another zone is left out (SE's list is
    per area); rows with no zone, or a query with no real zone, pass."""
    fields = _msn_fields()
    zones = sectorwins._row_int_map(sectorwins.MSN_ZONES, "FMO_MSN_ZONES")
    _zq = int(mapkind) if mapkind and zoneentry.in_mapkind_band(int(mapkind)) else None
    # WARNING: FILTER HERE, DO NOT LEAVE IT TO THE CLIENT. The view drops records
    # whose category is not its mode -- EXCEPT in modes 3 and 4, where it keeps
    # everything (0x611C98E1) and the query category is a hard-coded 3. So for
    # the Area Mission list the client filters nothing and the server is the
    # only thing that can: live 2026-09-12, Area showed a category-1 and a
    # category-2 row side by side because we sent them.
    # An UNCATEGORISED row still matches every query -- the legacy echo, kept
    # so a row nobody has classified does not silently vanish from every list.
    out = []
    for i, (mid, cat, name) in enumerate(msn_row_table()):
        if cat is not None and cat != category:
            continue
        if _zq is not None and zones.get(i) is not None and zones[i] != _zq:
            continue
        out.append(fmomsn.mission_record(name, category if cat is None else cat,
                                         mark=MSN_MARK, fields=fields.get(i),
                                         mid=mid))
    return out


def serve_mission_client(conn, peer, first):
    """One community-server connection, for its whole life.

    The client's own conversation, and nothing more: op 4 -> 0x12, op 9 ->
    pages of 0x1D -> 0x1B. Any other op gets 0x1B, which is the arm that ends
    a job and pops the next one -- so an op we have not decoded ends cleanly
    instead of parking the queue."""
    buf = bytearray(first)
    deadline = time.monotonic() + wirelog.HOLD
    log(f"{peer} KEY: COMMUNITY/MISSION SERVER connection (fmomsn) -- this is "
        f"the second server the 0x0322 endpoint at payload+0x28 points at. "
        f"FMO_MSN={MSN!r}, {len(MSN_ROWS)} row(s) authored.")
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            log(f"{peer} hold expired -- closing")
            return
        while True:
            total = fmomsn.looks_like_frame(bytes(buf))
            if total is None or len(buf) < total:
                break
            frame = bytes(buf[:total])
            del buf[:total]
            got = fmomsn.parse(frame)
            if got is None:
                log(f"{peer} WARNING: a {total}-byte frame did NOT verify (MD5 over "
                    f"+0x18, key '903094117gekisen'). The client refuses these "
                    f"too (0x611D0FF6), so this is our decode being wrong, not "
                    f"a transient:\n" + hexdump(frame[:64]))
                return
            op, body = got
            log(f"{peer} <- community op 0x{op:02X}, {len(body)}B body")
            if body:
                log(hexdump(body))
            for out in msn_reply(peer, op, body):
                log(f"{peer} -> community op 0x{out[0]:02X}, {len(out[1])}B")
                conn.sendall(out[1])
        conn.settimeout(min(15, max(0.1, deadline - time.monotonic())))
        try:
            data = conn.recv(4096)
        except socket.timeout:
            continue
        if not data:
            log(f"{peer} community client closed")
            return
        buf += data


#: FMO_GROUP_BOARD: answer the Scramble Board's battle-group list (community
#: op 0x07). 1 = yes (default), 0 = the old silence, which leaves the board
#: showing nothing and the job never popping.
#: WARNING: The op is DECODED, not guessed -- stride confirmed twice (the walker's
#: `add ebx,0x134` and the callback's `rep movsd 0x4D`) and every column mapped
#: from the board's own sort comparator against SE's headers and tooltips. But
#: it has never been served to a client, and this service has killed one before
#: (2026-09-06, answering an UNDECODED op with 0x1B). FMO_GROUP_BOARD=0
#: withdraws it with no deploy.
GROUP_BOARD = (os.environ.get("FMO_GROUP_BOARD", "1").strip() or "1") != "0"
#: FMO_GROUP_BOARD=mark fills every dword of the row with its own offset, so
#: each number on screen names the byte it came from. That is how the three
#: things the 2026-09-09 live run left open get finished WITHOUT guessing:
#:   * the detail view's member list is EMPTY -- where does it come from?
#:   * it offers JOIN on a group you own -- what marks a group as yours?
#:   * it said "the group is on a sortie" on a row we sent as state 0, and
#:     joining took the player into map 418 -- which field is that?
#: KEY: That last one is the useful shape of the negative: the row said state 0
#: and the client still read "sortied", so `state` is either not at +0x104 or
#: not the only input. Serving a DIFFERENT wrong value would have been a guess;
#: making the field name itself is a measurement (mark mode, below).
GROUP_BOARD_MARK = os.environ.get("FMO_GROUP_BOARD", "1").strip() == "mark"
#: What the last board answer described, for the log line only.
GROUP_BOARD_LOG = []


def group_board_rows():
    """The Scramble Board's rows, from the groups created on this server.

    WARNING: EVERY GROUP, no filtering. The client's query carries the player's
    MapKind and nation and SE's server presumably filtered on them; we do not,
    because a filter that silently drops the group you just made is exactly the
    symptom being fixed, and we have no evidence for which of the two fields is
    authoritative. Serving them all is wrong in the same direction as showing
    too much rather than too little, and it is visible rather than silent.
    """
    del GROUP_BOARD_LOG[:]
    rows = []
    for peer, gid, leader, when in battlegroups.BATTLE_GROUPS_MADE[:0xFF]:
        rec = battlegroups.BATTLE_GROUPS.get(peer) or {}
        name = rec.get("leader") or leader or ""
        comment = rec.get("comment") or ""
        GROUP_BOARD_LOG.append((gid, name))
        # KEY: THE NUMBERS COME FROM THE CREATE FORM. A 2026-09-09 live
        # screenshot of the board's Battle Map Information panel read
        # "B.G.Bonus H$ 0 / Total B.G.Cost 0 / Battles Left 0 / 0 / Required
        # B.G.Cost 0" -- every one of them zero because the first cut served
        # them as zero, not because the panel is unserved. The client told us
        # these in its own 0x0156: `total_battles` at +0x110 and the two form
        # bytes at +0x114 (live: `03 01`, read as Required B.G.Cost and the
        # continue flag). Echoing them back is the honest fill -- they are the
        # player's own choices, not invented numbers.
        _f114 = rec.get("f114") or ""
        _req = 0
        try:
            _req = int(_f114.split()[0], 16) if _f114 else 0
        except (ValueError, IndexError):
            _req = 0
        rows.append(fmomsn.group_record(
            gid, name=name, comment=comment,
            # State 0 = not sortied, which also makes column "T" read
            # +0x120 (the CURRENT cost) rather than the at-sortie one.
            # WARNING: UNSETTLED: a live client said "the group is on a
            # sortie" for a row sent with state 0, so either this is not the
            # field or it is not the only input. FMO_GROUP_BOARD=mark is how
            # that gets answered rather than guessed at.
            state=int(rec.get("state") or 0),
            members=max(1, int(rec.get("members") or 1)),
            sorties=int(rec.get("sorties") or rec.get("total_battles") or 0),
            cost_now=int(rec.get("cost_now") or 0),
            cost_required=int(rec.get("cost_required") or _req),
            bonus=int(rec.get("bonus") or 0),
            mark=GROUP_BOARD_MARK))
    return rows


def msn_reply(peer, op, body):
    """(op, frame) pairs for one request. Empty list = deliberate silence."""
    if not MSN_ON:
        log(f"{peer}   FMO_MSN=0: identified but NOT answered. The client will "
            f"hold this connection until its own timeout and its list job will "
            f"never pop -- that is the pre-2026-09-06 behaviour, kept for an "
            f"A/B.")
        return []
    if op == fmomsn.OP_HELLO:
        log(f"{peer}   op 4 = the connection HELLO (0x611AEC40). Answering "
            f"0x12, which makes the client send its queued job: kind 3 -> "
            f"op 9, the mission-list query.")
        return [(fmomsn.OP_GO, fmomsn.build(fmomsn.OP_GO))]
    if op == 0x07 and GROUP_BOARD:
        # KEY: THE SCRAMBLE BOARD'S GROUP LIST (static 2026-09-09). This is the
        # op behind the live report "I make a battle group and the board acts
        # like none exist": the board's row builder queues a KIND-1 job
        # (0x611AF380) keyed on the player's MapKind and nation, kind 1's page
        # op is 0x17, and we have never answered it. fmo.py's own comment on
        # 0x0156 said as much -- BATTLE_GROUPS_MADE is "not yet a served
        # group: the board list is next".
        rows = group_board_rows()
        log(f"{peer}   op 0x07 = THE SCRAMBLE BOARD'S BATTLE-GROUP LIST "
            f"(kind 1, 0x611AF380; the query carries the player's MapKind and "
            f"nation). Serving {len(rows)} row(s) of "
            f"{fmomsn.GROUP_RECORD_LEN}B"
            + (" -- empty, which is the truthful answer while no group has "
               "been created on this server, and it POPS the job instead of "
               "leaving the board waiting."
               if not rows else
               f": {', '.join('#%d %r' % (r[0], r[1]) for r in GROUP_BOARD_LOG[:4])}."))
        out = []
        per = fmomsn.max_group_records_per_page()
        for i in range(0, len(rows), per):
            out.append((fmomsn.OP_GROUPS, fmomsn.group_page(rows[i:i + per])))
        out.append((fmomsn.OP_END, fmomsn.build(fmomsn.OP_END)))
        return out
    if op == fmomsn.OP_LIST:
        q = fmomsn.ListQuery(body)
        rows = msn_rows(q.category, q.mapkind)
        log(f"{peer}   op 9 = THE MISSION-LIST QUERY: {q}. Serving "
            f"{len(rows)} row(s)"
            + (f" for zone {q.mapkind} (FMO_MSN_ZONES={sectorwins.MSN_ZONES!r} leaves "
               f"out rows issued in another area)" if sectorwins.MSN_ZONES else "")
            + (" in MARK mode (every dword is its own offset, so each column "
               "on screen names itself)" if MSN_MARK else "")
            + (". Empty is the truthful answer while FMO_MSN_ROWS is unset: "
               "the client draws systext 21:16 'No Missions Found', exactly "
               "what the screen already shows -- but the job POPS."
               if not rows else "."))
        if len(rows) > q.max_rows:
            log(f"{peer}   WARNING: trimming to the client's own cap of "
                f"{q.max_rows} (its list also stops at 100, 0x611C98B9)")
            rows = rows[:q.max_rows]
        out = []
        per = fmomsn.max_records_per_page()
        for i in range(0, len(rows), per):
            out.append((fmomsn.OP_PAGE, fmomsn.page(rows[i:i + per])))
        out.append((fmomsn.OP_END, fmomsn.build(fmomsn.OP_END)))
        return out
    if op == fmomsn.OP_SECTORS and warstate.WAR != "0":
        # KEY: KIND 7 = THE WAR STATE'S DOOR (static 2026-09-12 + prod's wire):
        # the war map (0x6118C670) asks for every sector of its zone plus the
        # zone's fortress, City Control (0x610E83A3) for the 0x019A cities;
        # each answer is a 216-byte record matched by the id at +0xD0.
        q = fmomsn.SectorQuery(body)
        recs, how = warstate.war_sector_records(q)
        log(f"{peer}   op 0x10 = KIND 7, THE SECTOR / CITY STATE QUERY (the war "
            f"map 0x6118C670 / City Control 0x610E83A3): {q}. {how}.")
        out = []
        if warstate.WAR != "end":
            per = fmomsn.max_sectors_per_page()
            for i in range(0, len(recs), per):
                out.append((fmomsn.OP_SECTOR_PAGE,
                            fmomsn.sector_page(recs[i:i + per])))
        out.append((fmomsn.OP_END, fmomsn.build(fmomsn.OP_END)))
        log(f"{peer}   -> {len(out) - 1} page(s) of op 0x21 ({len(recs)} x "
            f"{fmomsn.SECTOR_RECORD_LEN} B in 0x{fmomsn.SECTOR_SLOT_LEN:X} slots, "
            f"arm 0x611AFCD6) then 0x1B END -- both callbacks test the END's "
            f"NULL record (0x6118C650, 0x610E8130). FMO_WAR={warstate.WAR!r}; '0' is the "
            f"old silence. WARNING: NOT CONFIRMED IN A LIVE SESSION: no client has taken an 0x21 yet.")
        return out
    # The job's kind ([mgr+0x37D] = job[0]) picks the op, and the whole set is
    # known even though only kind 3's records are decoded. Naming them here is
    # what makes the log readable when a screen we have never served asks.
    _known = {
        0x07: "kind 1 (0x611AF380/0x611AF400, 0xDD B) -- the WAR MAP "
              "(0x6118EAFE/0x6118EB40) and a lobby window",
        0x08: "kind 2 (0x611AF4D0, 0x78 B) -- the mission module (0x611C5596)",
        0x0E: "kind 5 (0x611AF670, 0x348 B) -- the mission module, a count at "
              "payload+0x08 and that many u32 ids",
        0x10: "kind 7 (0x611AF6F0, 0x1B8 B) -- KEY: CITY CONTROL (0x610E83A3, "
              "systext group 30) and the war map (0x6118C6E9); a count at "
              "payload+0x08 and that many u32 ids",
    }
    _what = _known.get(op)
    log(f"{peer}   community op 0x{op:02X} = "
        + (_what if _what else "not decoded at all")
        + f". body {len(body)}B: {body[:48].hex(' ')}")
    # WARNING: LIVE 2026-09-06T17:45:57Z: ANSWERING AN UNDECODED OP WITH 0x1B KILLED
    # THE CLIENT. Talking to tag_search opens the war map in MODE 0, which
    # immediately queues a KIND-0 job -> op 6 (116 B). This arm answered 0x1B
    # (END), whose handler 0x611AFC14 calls the job's callback with a NULL
    # record -- and ~1 s later pol.exe was gone. Same shape as the 0x018E
    # death: an "honest empty answer" is only honest for a consumer that
    # tolerates empty, and this one does not.
    #
    # So the default is now SILENCE for any op whose reply shape we have not
    # earned. Silence is what every other unserved thing in this file gets,
    # it is the state the client survived for weeks (the 900 s hold), and it
    # is single-variable against the crash. FMO_MSN_UNKNOWN=end restores the
    # old behaviour deliberately -- it is how you reproduce the kill.
    if op in MSN_END_OPS:
        log(f"{peer}   -> op 0x1B END for op 0x{op:02X} (FMO_MSN_END_OPS): "
            f"this job's callback is expected to TOLERATE a NULL record and "
            f"finalise. For op 8 that callback is 0x6123CAD0, which branches "
            f"on NULL (0x6123CADA) into its finalise path -- that is how a "
            f"list ends here. The 2026-09-06 kill was op 6, a DIFFERENT job "
            f"kind whose callback does not. If the client dies on this, "
            f"remove 0x{op:02X} from FMO_MSN_END_OPS and say so.")
        return [(fmomsn.OP_END, fmomsn.build(fmomsn.OP_END))]
    if MSN_UNKNOWN == "refuse":
        # KEY: op 0x15 -- the client's OWN graceful refusal, and the only
        # completion in this protocol that does not hand a callback a NULL
        # record: 0x611AF210 stores our code at [mgr+0x3A5], then 0x611AED90
        # closes the connection and FREES the job. The screen does not get
        # its data, but the job is released instead of parked.
        log(f"{peer}   -> op 0x15 REFUSAL (FMO_MSN_UNKNOWN='refuse'): "
            f"0x611AF210 takes the code at payload+0x0C, 0x611AED90 then frees "
            f"the job and closes the connection. No callback runs, so this "
            f"cannot repeat the 0x1B kill. Code {MSN_REFUSE_CODE}.")
        return [(fmomsn.OP_STATUS, fmomsn.status(MSN_REFUSE_CODE))]
    if MSN_UNKNOWN == "probe":
        # WARNING: THE EXPERIMENT, AND IT MAY CRASH THE CLIENT AGAIN. The 09-06 kill
        # is consistent with "0x1B with an EMPTY list": arm 0x611AF780 links
        # 76-byte records onto [mgr+0x3AD], and the job's callback may walk
        # that list. One zero-filled record before the 0x1B tests it.
        _n = max(1, MSN_PROBE_ROWS)
        log(f"{peer}   WARNING: FMO_MSN_UNKNOWN='probe': sending op 0x14 with {_n} "
            f"zero-filled 76-byte record(s) THEN 0x1B. This tests whether the "
            f"09-06 kill was 0x1B on an EMPTY list. IT MAY CRASH THE CLIENT.")
        return [(fmomsn.OP_LIST14,
                 fmomsn.page14([fmomsn.record14() for _ in range(_n)])),
                (fmomsn.OP_END, fmomsn.build(fmomsn.OP_END))]
    if MSN_UNKNOWN != "end":
        log(f"{peer}   WARNING: NOT ANSWERING (FMO_MSN_UNKNOWN={MSN_UNKNOWN!r}). "
            f"Answering an undecoded op with 0x1B killed the client at "
            f"2026-09-06T17:45:57Z -- 0x1B's arm hands the job's callback a "
            f"NULL record and this consumer does not survive it. The "
            f"connection will hold and the screen will sit empty; that is "
            f"the safe state, not a bug. Decode the body above first.")
        return []
    log(f"{peer}   WARNING: FMO_MSN_UNKNOWN=end: answering 0x1B, which is what KILLED "
        f"the client on 2026-09-06. This is the reproduce switch.")
    return [(fmomsn.OP_END, fmomsn.build(fmomsn.OP_END))]


# Called at run time only; imported last so that import cycles resolve.
from . import battlegroups, sectorwins, warstate, zoneentry  # noqa: E402
