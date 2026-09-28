"""POP position, model and type, the probe sweeps and the per-map arrival points."""
import os
from . import popself, zoneentry


#: Where to put it. Defaults to the PILOT's own position, because a unit
#: the camera is not looking at is indistinguishable from a unit that was
#: never created -- and the pilot position is the one place in this scene we
#: already choose. WARNING: ±327.67 is a hard wire limit, not a style guide.
#: WARNING: An EMPTY value is "unset", not "no position". compose passes
#: `${FMO_UDP_POP_POS:-}`, so a prod .env that does not set it hands us "" --
#: and the old `float("")` took the whole fmo service down at import, which
#: reads as the door being closed rather than a knob being blank.
POP_POS = tuple(float(x) for x in
                (os.environ.get("FMO_UDP_POP_POS", "").strip()
                 or ",".join(str(v) for v in zoneentry.PILOTPOS)
                 ).replace(" ", "").split(","))


def _parse_pop(spec):
    """`<unitid>[:<unittype>]` -> (unit_id, unit_type), or None.

    WARNING: A malformed value returns None and is LOGGED, never silently ignored: a
    probe that quietly does not run is indistinguishable from a probe that ran
    and had no effect, and this whole session exists because of a measurement
    nobody had taken."""
    if not spec:
        return None
    unit_type = 0
    if ":" in spec:
        spec, _, t = spec.partition(":")
        unit_type = int(t, 0)
    return int(spec, 0), unit_type


#: WARNING: UnitType SWEEP. `fmocrash --live` in MapNo 102 (2026-08-22) reported the
#: player's unit present, self-identified, and holding a VISUAL OBJECT -- and
#: the player could not see their character while the room around it rendered
#: fine. A visual that exists and draws nothing is what an empty model would
#: look like, and we have only ever sent **UnitType 0**, which is the value we
#: chose arbitrarily rather than one we decoded. Legal types are 0-6 and 30
#: (7..29 hit 0x611EB3F0 "no UnitType=%u UnitID=%x" and create NOTHING).
#:
#: FMO_UDP_POP_TYPE_SWEEP="1,2,3,4,5,6,30" walks them, ONE PER WORLD CHANNEL.
#: The point is the same as the PilotPos sweep: a Move re-enters the scene and
#: opens a new channel, so a single launch can try every type -- Move -> Change
#: Room, look, repeat. Without it each type costs a service restart and a
#: relaunch.
#:
#: WARNING: It overrides the type in FMO_UDP_POP, not the unit id. Unset, nothing
#: changes.
#: WARNING: THE MODEL SELECTOR, `body+0x8E` and `body+0x8B` -- see fmoworld's
#: POP_MODELFLAGS. The UnitType sweep moved the FALLBACK; this moves the thing
#: that actually chooses. `FMO_UDP_POP_MODEL="0x58:3"` sets flags then sub.
#: `FMO_UDP_POP_MODEL_SWEEP="0x58:0,0x58:1,0x58:2"` walks pairs, one per world
#: channel, so a Move takes the next -- same trick as the other sweeps.
#:
#: WARNING: NOTHING HERE IS DECODED. Which sub values are real model indices is
#: unknown; 0x58 is the only flags value with a reason behind it (bit 3 arms the
#: selector, nibble 5 picks the kind that reads a sub byte at all).
def _parse_model(spec):
    f, _, sub = spec.partition(":")
    return (int(f, 0) if f else None, int(sub, 0) if sub else None)


MODEL_SPEC = os.environ.get("FMO_UDP_POP_MODEL", "").strip()
POP_MODEL = _parse_model(MODEL_SPEC) if MODEL_SPEC else (None, None)
POP_MODEL_SWEEP = [_parse_model(x) for x in
                   os.environ.get("FMO_UDP_POP_MODEL_SWEEP", "")
                   .replace(" ", "").split(",") if x]
_model_n = [0]


def next_pop_model():
    """(flags, sub) for the next POP. Steps once per channel that pops."""
    if not POP_MODEL_SWEEP:
        return POP_MODEL, None
    i = _model_n[0] % len(POP_MODEL_SWEEP)
    _model_n[0] += 1
    return POP_MODEL_SWEEP[i], (i + 1, len(POP_MODEL_SWEEP))


POP_TYPE_SWEEP = [int(x, 0) for x in
                  os.environ.get("FMO_UDP_POP_TYPE_SWEEP", "")
                  .replace(" ", "").split(",") if x]
_pop_n = [0]


def next_pop_type(default):
    """The UnitType for the next POP. Steps once per channel that pops."""
    if not POP_TYPE_SWEEP:
        return default, None
    i = _pop_n[0] % len(POP_TYPE_SWEEP)
    _pop_n[0] += 1
    return POP_TYPE_SWEEP[i], (i + 1, len(POP_TYPE_SWEEP))


#: WARNING: SPAWN POSITION SWEEP. Measured 2026-08-24: zones render and
#: Move works, and the pilot sits at the POP's entity coords (0,5,0) = MAP
#: ORIGIN -- inside geometry in 121 ("a wall"), in empty space in 101 ("a
#: void"). The spawn is server-owned bytes and finding walkable ground is a
#: LOOK problem: FMO_UDP_POP_POS_SWEEP="x,y,z,w; x,y,z,w; ..." walks
#: candidates ONE PER WORLD CHANNEL -- Move -> Change Room takes the next, no
#: relaunch, the same trick as the UnitType and model sweeps. Unset,
#: FMO_UDP_POP_POS rules alone. Entries are range-checked HERE so a bad one
#: dies at startup instead of silently costing the player a Move.
POP_POS_SWEEP = []
for _q in os.environ.get("FMO_UDP_POP_POS_SWEEP", "").split(";"):
    _q = _q.replace(" ", "")
    if not _q:
        continue
    _v = tuple(float(x) for x in _q.split(","))
    if len(_v) != 4:
        raise SystemExit(f"FMO_UDP_POP_POS_SWEEP entry {_q!r} wants four "
                         f"comma-separated floats")
    if any(not -327.67 <= x <= 327.67 for x in _v):
        raise SystemExit(f"FMO_UDP_POP_POS_SWEEP entry {_q!r} is outside the "
                         f"±327.67 the wire can express")
    POP_POS_SWEEP.append(_v)
_pos_n = [0]

#: VERIFIED: THE PER-MAP SPAWN TABLE -- `FMO_UDP_POP_POS_MAP="101:x,y,z,w; 102:..."`.
#:
#: KEY: MEASURED 2026-08-28, and this is the knob the whole spawn hunt was for.
#: `FMO_UDP_POP_POS` is ONE GLOBAL POINT, and the maps do not share an origin or
#: a floor: 101/102 stand at y≈3.0, 121/122 at y=0.0, and map 161's extent is
#: `0..128` where every other lobby is `-128..128` (per the map headers). So a
#: single point is wrong by construction -- (0,5,0) is the centre of 121 and the
#: literal CORNER of 161, and (40,5,40) drops you ~2 units in 101.
#:
#: WARNING: AND THE POP POSITION IS WHAT PLACES THE PLAYER, not PilotPos. Measured
#: live 2026-08-28 across four maps with the two knobs set to DIFFERENT values:
#: `FMO_UDP_POP_POS=40,5,40,0` and `FMO_0153_PILOTPOS=0,5,0,0` -> the client
#: streamed (40,5,40) in all four. The 2026-08-22 "PilotPos IS the spawn point"
#: reading is refuted; PilotPos writes a different entity (0x82080010) on a path
#: the client does not take when we pop its unit for it.
#:
#: Values come from the client's own cmd-240 stream -- walk somewhere sensible
#: and read the `POSITION MapNo=` line. That is a MEASUREMENT, not a guess, and
#: it is in the same coordinate system this field is.
#: WARNING: Entries are range-checked HERE so a bad one dies at startup rather than
#: silently costing a Move, and an empty value is "unset" (the empty-env trap).
POP_POS_MAP = {}
for _q in os.environ.get("FMO_UDP_POP_POS_MAP", "").split(";"):
    _q = _q.replace(" ", "")
    if not _q:
        continue
    _mn, _, _rest = _q.partition(":")
    if not _rest:
        raise SystemExit(f"FMO_UDP_POP_POS_MAP entry {_q!r} wants "
                         f"`<mapno>:x,y,z,w`")
    try:
        _mn = int(_mn, 0)
    except ValueError:
        raise SystemExit(f"FMO_UDP_POP_POS_MAP entry {_q!r} has a MapNo that "
                         f"is not an integer")
    _v = tuple(float(x) for x in _rest.split(","))
    if len(_v) != 4:
        raise SystemExit(f"FMO_UDP_POP_POS_MAP entry {_q!r} wants four "
                         f"comma-separated floats after the MapNo")
    if any(not -327.67 <= x <= 327.67 for x in _v):
        raise SystemExit(f"FMO_UDP_POP_POS_MAP entry {_q!r} is outside the "
                         f"±327.67 the wire can express")
    POP_POS_MAP[_mn] = _v


def next_pop_pos(mapno=None):
    """(position, sweep_progress_or_None, source) for the next POP.

    Precedence: an ARMED SWEEP wins, then this map's row, then the global.
    The sweep is deliberately first -- it is the knob you arm to go LOOKING for
    a position, and a table quietly overriding the search would make the search
    untestable on any map already in the table."""
    if POP_POS_SWEEP:
        i = _pos_n[0] % len(POP_POS_SWEEP)
        _pos_n[0] += 1
        return POP_POS_SWEEP[i], (i + 1, len(POP_POS_SWEEP)), "sweep"
    if mapno is not None and mapno in POP_POS_MAP:
        return POP_POS_MAP[mapno], None, f"FMO_UDP_POP_POS_MAP[{mapno}]"
    return POP_POS, None, ("FMO_UDP_POP_POS (global -- no row for MapNo "
                           f"{mapno})" if POP_POS_MAP else
                           "FMO_UDP_POP_POS (global)")


try:
    POP = _parse_pop(popself.POP_SPEC)
except ValueError:
    POP = None
    print(f"[fmo] \WARNING: FMO_UDP_POP={popself.POP_SPEC!r} is not "
          f"'<unitid>[:<unittype>]' -- THE POP PROBE IS OFF")
