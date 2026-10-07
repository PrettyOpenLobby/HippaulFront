"""The war state: per-sector control and the phase clock (fmowar), and its 216-byte sector
records."""
import os
from .deps import fmomsn, fmosectors, fmowar
from .wirelog import log


#: KEY: FMO_WAR -- how the kind-7 SECTOR / CITY STATE query (op 0x10) is
#: answered. This is the war map's and City Control's door to the war state
#: (fmomsn.py `SectorQuery`; prod's log holds 111 of them, never answered).
#:   '1'    (default) one 216-byte record per id, the id at +0xD0, the state
#:          written in only where FMO_WAR_MAP binds a field; then 0x1B END.
#:          Unbound = id-only records, which the client draws as "no data",
#:          but the job POPS instead of holding the connection for 900 s.
#:   'mark' every dword of every record is its own offset: read the war
#:          map's Change View overlays (10:19..23), "NPC Rank : %s" (10:31)
#:          and the O.C.U./U.S.N./Deadlock label (10:33..35), then bind them.
#:   'end'  0x1B only (the callbacks 0x6118C650 / 0x610E8130 tolerate NULL).
#:   '0'    silence -- the pre-2026-09-12 behaviour, kept for an A/B.
WAR = os.environ.get("FMO_WAR", "").strip() or "1"
#: FMO_WAR_MAP -- the binding of state fields to record offsets, found by the
#: mark launch: `nation=0x05:b,control=0x08:I,...` (fmowar.parse_binding).
#: Empty until someone has looked; no offset in here is a guess.
WAR_MAP = os.environ.get("FMO_WAR_MAP", "").strip()
#: FMO_WAR_FIELDS -- raw u32 pokes into EVERY record, `0x08=50,0x68=3`, for
#: a probe launch ("what does the overlay do with this byte").
WAR_FIELDS = os.environ.get("FMO_WAR_FIELDS", "").strip()
_WAR_STATE = None


def war_state():
    """The one War() for this process, or None without the module."""
    global _WAR_STATE
    if fmowar is None:
        return None
    if _WAR_STATE is None:
        _WAR_STATE = fmowar.War()
        # the opening map: every sector SE's table knows, by its zone kind
        # (FMO_WAR_SEED); a tile already on file is left alone
        if fmosectors is not None:
            n = _WAR_STATE.seed_from_sectors(fmosectors.SECTORS)
            if n:
                log(f"war state: seeded {n} sector(s) from fmosectors by zone "
                    f"kind ({fmowar.SEED_BY_KIND}); {_WAR_STATE.summary()}")
        _war_tick(_WAR_STATE)
    return _WAR_STATE


def load_at_start():
    """Load the war state when the service starts, so the table is filled
    (the one-shot import of the old fmowar.json) and the City Control board
    has a state to read before the first battle. Logs what was imported, or
    why it was not, and the state's sector count. Returns the War, or None."""
    if fmowar is None:
        log("WARNING: war state OFF: fmowar.py did not import")
        return None
    fmowar.import_legacy(log=lambda msg: log("war state: " + msg))
    st = war_state()
    if st.present:
        log(f"war state: loaded, {st.summary()}")
    else:
        log(f"WARNING: war state: nothing stored and nothing written (is the "
            f"database reachable?); {st.summary()}")
    return st


def _war_tick(st):
    """Judge any phase whose time has come; log each one."""
    try:
        for pn, rec in st.tick():
            log(f"war state: PHASE {pn} JUDGED -- O.C.U. {rec['ocu']} pts vs "
                f"U.S.N. {rec['usn']} pts -> "
                f"{ {1: 'O.C.U. wins', 2: 'U.S.N. wins'}.get(rec['winner'], 'a TIE, both rewarded') }"
                + (f"; Deadlock penalty on nation {rec['penalty']['nation']}'s "
                   f"fortress tile {rec['penalty']['tile']}" if rec.get("penalty") else "")
                + " (SE's phase page; the reward table is not served yet)")
        for pn, rec in getattr(st, "resets_now", ()):
            log(f"war state: PHASE {pn} STARTED -- the frontline is RESET to its "
                f"opening state ({rec['sectors']} sector(s); guide/phase, news7740)"
                + (f", nation {rec['penalty']['nation']}'s fortress tile "
                   f"{rec['penalty']['tile']} opens Deadlock" if rec.get("penalty") else
                   ", both fortresses held (a tie or no judgement)")
                + f". Area missions accepted before {rec['at']} are failed "
                f"(frontline_reset_at).")
    except Exception as e:                       # pragma: no cover
        log(f"war state: WARNING: phase tick failed ({e!r})")


def frontline_reset_at():
    """When the frontline was last reset by a new phase (epoch s), or 0 when
    it never was or there is no war state. SE (news7740): 「初期化に伴い、停戦時に
    実行中のエリアミッションはすべて自動的に失敗となります」 -- an area
    (category 3) accept made before this moment is FAILED. The mission book
    owns the missions and reads this; the war state only knows the moment."""
    if WAR == "0" or fmowar is None:
        return 0
    st = war_state()
    return st.frontline_reset_at() if st is not None else 0


def parse_war_fields(spec):
    """`0x08=50,0x68=3` -> {offset: u32}."""
    out = {}
    for tok in (spec or "").replace(" ", "").split(","):
        if tok:
            off, _, val = tok.partition("=")
            out[int(off, 0)] = int(val or "0", 0)
    return out


def war_sector_records(q):
    """([216-byte records], how) for one kind-7 query under FMO_WAR."""
    mark = "byte" if WAR == "mark8" else (WAR == "mark")
    pokes = parse_war_fields(WAR_FIELDS)
    binding = fmowar.parse_binding(WAR_MAP) if (fmowar and WAR_MAP) else {}
    st = war_state() if binding else None
    if st is not None:
        _war_tick(st)
    recs = []
    for sid, tile in zip(q.ids, q.tiles()):
        fields = dict(pokes)
        if binding and st is not None:
            fields.update(st.record_fields(tile, binding))
        recs.append(fmomsn.sector_record(sid, mark=mark, fields=fields))
    if mark == "byte":
        how = ("MARK8 mode: every BYTE of every record is its own offset (216 "
               "< 256, so each one is unique) -- a byte-sized column finally "
               "says which byte it is; bind them with FMO_WAR_MAP")
    elif mark:
        how = ("MARK mode: every dword of every record is its own offset -- "
               "read the war map's overlays and bind them with FMO_WAR_MAP. "
               "WARNING: three of every four bytes stay ZERO, so a byte-sized column "
               "reads 0 and tells you nothing: use FMO_WAR=mark8 for those")
    elif binding and st is not None:
        how = (f"fields {sorted(binding)} bound from the war state "
               f"({st.summary()})")
    else:
        how = ("id-only records (FMO_WAR_MAP is empty, nothing is bound): the "
               "client reads them as 'no data', and the job POPS")
    if pokes:
        how += "; FMO_WAR_FIELDS pokes at " + ", ".join("%#x" % o for o in sorted(pokes))
    return recs, how
