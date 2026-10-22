"""The lobby NPC editor hook (FMO_DEVTOOL_PORT)."""
import os
import time

import fmoarena
from .deps import fedevtool, fmodevtool, fmolayout, fmowar
from .knobs import _env_int
from .wirelog import log
from . import wirelog


def devtool_ctx():
    """What the lobby NPC editor needs from the live server, built per
    request: for each band, the map FMO_ZONE_MAPNO serves it on, that map's
    floor (the y of FMO_UDP_POP_POS_MAP -- a measured standing position) and
    the O.C.U. roster being served for it right now (env or layout, with its
    source); every popped world channel's last position; the facing sign.
    fmodevtool never imports this module -- this dict is the whole contract."""
    served = {}
    B = npcroster.band_rosters()
    for bid, _title, kinds, _cat, _desc, place_kind in fmolayout.BANDS:
        if place_kind:
            # a room / hangar: its map from FMO_ROOM_MAPS (the O.C.U. one for
            # the nation-split Briefing Room), its cast the layout file's band
            # or, for the zone-kind Room bands, the shipped roomcast default
            mapno = move.ROOM_MAPS.get(place_kind)
            _lr = npcroster.layout_band(bid)
            _dflt = roomcast.ROOM_DEFAULT_ROWS.get(bid)
            if _lr is None and _dflt:
                _lr = fmolayout.rows_to_roster(_dflt, npccast.FACE_SIGN)
                src = f"the shipped {bid} cast (roomcast.py); a layout {bid} band replaces it"
            else:
                src = (f"the layout file's {bid} band" if _lr is not None
                       else f"nothing -- no {move.PLACE_KIND_NAMES.get(place_kind)} cast until one is placed")
            roster, names = _lr if _lr is not None else ([], {})
        else:
            kind = kinds[0]
            mapno, _why = areachange.zone_mapno(kind * 100, zoneentry.MAPNO, "FMO_MAPNO")
            _name, ocu, _usn, src = B.get(kind, B[1])
            roster, names = ocu
        pp = popsweep.POP_POS_MAP.get(mapno)
        served[bid] = {"mapno": mapno, "floor_y": pp[1] if pp else None,
                       "roster": roster, "names": names, "source": src}
    live, seen = [], set()
    for ch in list(groupchannel.WORLD_PEERS.values()):
        if id(ch) in seen or not ch.popped or getattr(ch, "left", None) is not None:
            continue
        seen.add(id(ch))
        host = ch.addr[0] if isinstance(ch.addr, tuple) else str(ch.addr)
        p = ch.pos or (0.0, 0.0, 0.0)
        live.append({"host": host, "mapno": rooms.WORLD_MAPS.get(host),
                     "zone": rooms.WORLD_ZONES.get(host), "x": float(p[0]),
                     "y": float(p[1]), "z": float(p[2]), "rot": float(ch.rot or 0.0)})
    return {"layout": npccast.NPC_LAYOUT, "served": served, "live": live,
            "face_sign": npccast.FACE_SIGN, "walked": WALKED}


#: the widest range one calendar.json request may ask for, in seconds
CALENDAR_SPAN = 400 * 86400


def calendar(query, actor=None):
    """GET calendar.json?from=&to= (unix seconds) -> (status, body): what FMO
    has scheduled in [from, to) for the admin panel's Calendar. The war's
    phases and ceasefires on this war's own clock (the stored phase 1 start,
    fmowar.calendar), and the Coliseum's (fmoarena.calendar: the Clash of
    Iron seasons, the Saturday Heavy Mobile Weapon Orders, the hosted
    arenas). Reads only: it never creates the process's War or Coliseum."""
    try:
        lo, hi = int(query["from"][0]), int(query["to"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return 400, {"ok": False, "msg": "calendar.json needs from= and to=, in unix seconds."}
    if hi <= lo or hi - lo > CALENDAR_SPAN:
        return 400, {"ok": False, "msg": "Ask for a range of up to %d days." % (CALENDAR_SPAN // 86400)}
    events = []
    if fmowar is not None and warstate.WAR != "0":
        st = warstate._WAR_STATE
        phase1 = st.phase1() if st is not None else fmowar.War(autosave=False).phase1()
        events += fmowar.calendar(lo, hi, phase1)
    if coliseum.COLISEUM:
        col = coliseum._COLISEUM
        if col is None:
            col = coliseum.Coliseum()
        hosted = coliseumtool._copy(lambda: [dict(a) for a in col.hosted.values()])
        events += fmoarena.calendar(lo, hi, hosted=hosted)
    events.sort(key=lambda e: (e["start"], e["end"], e["id"]))
    return 200, {"ok": True, "from": lo, "to": hi, "now": int(time.time()), "events": events}


#: the walked-ground overlay's source: this server's own fmo.log, read
#: incrementally (fmolayout.WalkedGround) once the editor is up
WALKED = None


def devtool_start():
    """Start the lobby NPC editor (FMO_DEVTOOL_PORT / _BIND / _TOKEN) on its
    own thread. WARNING: A LAN bind without a token is REFUSED by fedevtool.start
    (SystemExit) -- caught here and logged, the world door still serves: a
    config slip on the panel must not crash-loop the whole title."""
    port = _env_int("FMO_DEVTOOL_PORT", "0")
    if not port:
        log("lobby NPC editor OFF (FMO_DEVTOOL_PORT unset)")
        return None
    if fedevtool is None or fmodevtool is None or fmolayout is None:
        log("WARNING: FMO_DEVTOOL_PORT is set but fedevtool/fmodevtool/fmolayout did "
            "not import -- the lobby NPC editor is OFF")
        return None
    bind = os.environ.get("FMO_DEVTOOL_BIND", "").strip() or "127.0.0.1"
    token = os.environ.get("FMO_DEVTOOL_TOKEN", "").strip()
    try:
        srv = fedevtool.start(
            port, bind, token,
            lambda band=None, qs=None: fmodevtool.build_state(
                devtool_ctx(), band, want_plan=bool(qs and qs.get("plan"))),
            lambda op: fmodevtool.apply_edit(devtool_ctx(), op),
            page=fmodevtool.PAGE, name="fmo-devtool",
            routes={**gatetool.routes(), **coliseumtool.routes(), **wartool.routes(),
                    ("GET", "/calendar.json"): calendar})
    except SystemExit as e:
        log(f"WARNING: lobby NPC editor REFUSED to start: {e} -- the world door "
            f"serves without it")
        return None
    except OSError as e:
        log(f"WARNING: lobby NPC editor could not bind {bind}:{port}: {e} -- the "
            f"world door serves without it")
        return None
    wirelog._DEVTOOL_NOTE = fedevtool.note
    global WALKED
    WALKED = fmolayout.WalkedGround(os.path.join(wirelog.LOG_DIR, "fmo.log"))
    bands = npccast.NPC_LAYOUT.bands()
    log(f"VERIFIED: lobby NPC editor ON: http://{bind}:{port}/"
        f"{'?t=<FMO_DEVTOOL_TOKEN>' if token else ''} -- layout file "
        f"{npccast.NPC_LAYOUT.path} ({', '.join(bands) if bands else 'no bands yet: every band is served from the env'}); "
        f"floor plans ship for maps {fmolayout.plans_available()}; story gates at /gates"
        f"{'' if gatetool.fmogates else ' UNAVAILABLE (fmogates did not import)'}; "
        f"the Coliseum at /coliseum, the war at /war"
        f"{'' if wartool.boardfmo else ' UNAVAILABLE (boardfmo did not import)'}")
    return srv


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    areachange, coliseum, coliseumtool, gatetool, groupchannel, move, npccast, npcroster, popsweep, roomcast, rooms, warstate,
    wartool, zoneentry,
)
