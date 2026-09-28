"""The lobby NPC cast: FMO_UDP_POP_NPC parsing, face angles, HQ marks and the U.S.N.
counterparts."""
import math
import os
from .deps import fmolayout, fmoworld
from .knobs import _env_int


#: WARNING:KEY: PLAN 3.1 -- NPC SOURCE RECORDS. `FMO_UDP_POP_NPC` pops the "source"
#: units the client's NPC-derivation arm turns into visible NPCs. Decoded from
#: 0x61100100 block C (the scene 6/7 path the lobby cutscene runs, 2026-09-04):
#: it walks FIXED source-id ranges and, for each id it FINDS in the entity map
#: (lookup 0x61072650, matching entity+0x10 == id), copies that source's
#: 456-byte body (entity+0x147 == our cmd-7 POP body) and position (entity+0x44)
#: and CREATES the derived NPC via 0x611eadc0. The ranges block C enumerates:
#:     0x82001000..0x82001007   -> NPCs 0x820c2xxx
#:     0x82080500..0x82080507   -> NPCs 0x820c19xx
#:     0x82080600..              -> NPCs 0x820c19xx   (further ranges follow)
#: The live map has NONE of these, so nothing is derived and the cutscene has an
#: empty cast. This pops one cmd-7 record per spec, ONCE per channel, alongside
#: the self-POP -- so the source is present before the arm runs (~scene state 3,
#: 1.2s after entry). Only on a LOBBY channel.
#: WARNING: WHICH SCENES HAVE THAT ARM (static, 2026-09-05): 0x61100100 is called ONLY by the scene-7 (cold-entry cutscene)
#: and scene-5 setup machines at their state 3. The SETTLED lobby, scene 6, has
#: its own machine (0x61007340) that never calls it, so after the cutscene ends
#: NOTHING derives from these records -- there they stand as plain human POPs
#: (they do get actors; the census shows all eight with entity+0x24 set). The
#: earlier reading that scene 6 derived at one tick and our re-pops lost a race
#: was RETRACTED the same day; do not tune POP timing to chase it.
#:
#: Format: "id[:type][@x,y,z];..." -- entries are ';'-separated (the position's
#: own commas is why, as FMO_UDP_POP_POS_MAP does). id = the SOURCE id (hex),
#: type = a legal UnitType (default 4 = human), @x,y,z = the derived NPC's
#: position (default = the self-POP position for this map). A minimal body
#: (id/type/pos only, no look block) is deliberate: an undecoded appearance byte
#: is what broke world entry before, and step 1 is just "does an NPC appear".
#: Example: FMO_UDP_POP_NPC=0x82001000:4@-20,3,-30;0x82080500
POP_NPC_SPEC = os.environ.get("FMO_UDP_POP_NPC", "").strip()


def _parse_pop_npc(spec):
    """`id[:type][@x,y,z][#typecode];...` -> [(id, type, pos, typecode), ...]
    with pos / typecode None when absent.

    `#typecode` dresses the NPC from the client's own lobby NPC catalogue
    (fmoworld.NPC_CATALOGUE: face, uniform, size, build, selector, names -- the
    fields the 0xE280 dresser writes), so the popped human is the cast member's
    model and not a clone of the player. Quote the value in .env: an unquoted
    `#` after a space starts a comment there.

    KEY: WHICH BLOCK: 0..41 are AI/F00/D07.DAT's, the TUTORIAL's people (MapKind
    500..519 -- Colette Dirac, Malcolm O'Brien). 100..137 are AI/F00/D87.DAT's,
    and D87 is the script EVERY REAL LOBBY runs (LEV table AI/F08/D15.DAT: OCU
    100..109/200..209, USN 300..309/400..409), so its people are the ones the
    counter event scripts speak as: 100 Kwangsu Son = `nina_event`, 102 Henry
    Viduka = `gunsou1_event`, 103 Edward Miura = `tag_senior`, 108..122 the
    OCU operators (101/104/105/123..137 are the USN halves). Dressing a real
    lobby from the 0..41 block puts the tutorial's cast behind the HQ counters.

    Pure; the selftest drives it. A malformed entry raises SystemExit at import
    (a probe that silently does not run is indistinguishable from one that ran
    and had no effect)."""
    out = []
    POP_NPC_NAMES.clear()
    for e in spec.replace(" ", "").split(";"):
        if not e:
            continue
        e, _, catstr = e.partition("#")
        # `=name1.name2` after the typecode: the names the Select-target list
        # prints ("%s.%s", entity+0x10C/+0x11D = body+0x58/+0x69). The
        # catalogue gives every operator "NPC1.OPERATOR", so a row of four is
        # unpickable (LIVE 2026-09-05: "I wind up talking to people at
        # random"). 16 chars each (17-byte fields with the NUL).
        catstr, _, namestr = catstr.partition("=")
        body, _, posstr = e.partition("@")
        idstr, _, typestr = body.partition(":")
        try:
            uid = int(idstr, 0)
        except ValueError:
            raise SystemExit(f"FMO_UDP_POP_NPC entry {e!r}: id {idstr!r} is "
                             f"not an integer")
        utype = int(typestr, 0) if typestr else 4
        pos = None
        if posstr:
            pos = tuple(float(x) for x in posstr.split(","))
            if len(pos) == 3:
                pos = pos + (0.0,)
            if len(pos) != 4:
                raise SystemExit(f"FMO_UDP_POP_NPC entry {e!r}: @pos wants "
                                 f"three or four floats")
        cat = None
        if catstr:
            try:
                cat = int(catstr, 0)
            except ValueError:
                raise SystemExit(f"FMO_UDP_POP_NPC entry {e!r}: #typecode "
                                 f"{catstr!r} is not an integer")
            if cat not in fmoworld.NPC_CATALOGUE:
                raise SystemExit(f"FMO_UDP_POP_NPC entry {e!r}: #typecode "
                                 f"{cat} is not in the catalogue (0..41 = the "
                                 f"tutorial's D07 table, 100..137 = D87's, the "
                                 f"script every real lobby runs)")
            if utype != 4:
                raise SystemExit(f"FMO_UDP_POP_NPC entry {e!r}: #typecode "
                                 f"dresses a HUMAN; UnitType must be 4")
        if namestr:
            n1, _, n2 = namestr.partition(".")
            if not n1 or len(n1) > 16 or len(n2) > 16:
                raise SystemExit(f"FMO_UDP_POP_NPC entry {e!r}: =name1.name2 "
                                 f"wants 1..16 chars each")
            POP_NPC_NAMES[uid] = (n1, n2)
        out.append((uid, utype, pos, cat))
    return out


#: uid -> (name1, name2) from the `=name1.name2` suffix; applied after the
#: catalogue dress so a function name can replace "NPC1.OPERATOR".
POP_NPC_NAMES = {}
POP_NPC = _parse_pop_npc(POP_NPC_SPEC)

#: KEY: SE'S OWN HQ SLOTS (FMO_HQ_MARKS, 2026-09-09). AI/F00/D87 PLACES ids
#: 0x18 / 0x20..0x23 / 0x24 / 0x28 / 0x29 and never CREATEs them -- SIX distinct
#: positions once the doubled ids collapse (0x20+0x21 and 0x22+0x23 are 0.05 m
#: apart, i.e. one body each), FIVE carrying a FACING, which we have never
#: served for anybody. Two of them sit at the counter wall (z ~ 1.1) looking at
#: each other; three make a knot in the far corner by the entrance.
#:
#: Our own layout (FMO_UDP_POP_NPC) is a WALKED SURVEY of the console platform
#: and is confirmed in a live session -- the player talks to these people and the Map
#: Selector opens the sortie door -- so this knob is an A/B, not a correction.
#: Only the PEOPLE move. The four consoles stay where the survey put them,
#: because a terminal is furniture: it belongs on the prop the map actually
#: has, not on a mark meant for staff.
#:
#: WARNING: TWO GUESSES, both cheap to flip:
#:   * WHICH person stands on WHICH mark. SE's server chose that; the roles
#:     below are read off the furniture (counter wall = the counters, corner =
#:     the sergeant and the senior officer).
#:   * THE ANGLE CONVENTION. The script's FACE is degrees; the wire's 4th POP
#:     component is radians (int16/1000). Neither a shared zero nor a shared
#:     direction is proved. If everyone faces backwards, set FMO_FACE_SIGN=1.
HQ_SE_MARKS = {
    0x82080100: (0.45, 3.05, 1.15, 270),    # mission counter, at the counter wall
    0x82080500: (3.60, 3.05, 1.19, 90),     # personnel officer, facing it
    0x82081010: (3.32, 3.00, 28.14, None),  # nina_event, mid-hall, no facing
    0x82081020: (22.49, 3.05, 37.50, 225),  # the sergeant, corner group
    0x82080110: (20.35, 3.05, 38.50, 180),  # tag_senior, same group
}
#: (23.50, 3.05, 35.35) facing 270 is the SIXTH mark and is left EMPTY: five
#: people, six slots, and inventing a sixth body would be the guess this whole
#: exercise is trying to remove.
HQ_MARKS = (os.environ.get("FMO_HQ_MARKS", "").strip() or "0") != "0"
FACE_SIGN = _env_int("FMO_FACE_SIGN", "-1")


def face_radians(deg):
    """The wire's 4th POP component from a script FACE angle in degrees,
    wrapped to +-pi and signed by FMO_FACE_SIGN. Pure."""
    if deg is None:
        return 0.0
    r = math.radians(deg % 360)
    if r > math.pi:
        r -= 2 * math.pi
    return round(FACE_SIGN * r, 3)


def se_marked(roster):
    """`roster` with every entry SE left a slot for moved onto that slot,
    facing included. Everything else is untouched. Pure."""
    out = []
    for uid, utype, pos, cat in roster:
        m = HQ_SE_MARKS.get(uid)
        out.append((uid, utype, pos, cat) if m is None else
                   (uid, utype, (m[0], m[1], m[2], face_radians(m[3])), cat))
    return out


if HQ_MARKS:
    POP_NPC = se_marked(POP_NPC)

#: KEY: THE CAST PER ZONE BAND, PER FACTION (2026-09-08). `FMO_UDP_POP_NPC` was
#: written for the O.C.U. HQ lobby on map 102. Three things the client's own
#: tables say about the other lobbies:
#:
#:   * the NPC EVENT TABLES (AI/F08/D39..D48, fmoprogression) key the same
#:     entities in every band -- the counters 0x820801xx..0x82080Dxx are
#:     everywhere -- but the PEOPLE differ: the HQ (100s/300s) has `nina_event`
#:     at 0x82081010, the sergeant `gunsou1_event` at 0x82081020 and
#:     `tag_senior` at 0x82080110; the OCCUPATION band (200s/400s) has
#:     `nina_event` at BOTH 0x82081010 and 0x82081020 (mission briefings, "the
#:     Operator") and no sergeant; the FRONTLINE band (500s) adds
#:     `tag_secretary` at 0x82080120 and its 0x82081010 is `ope_event`.
#:   * the LEV table runs D87 for HQ and occupation lobbies and D07 -- the
#:     TUTORIAL's script, whose cast is Colette Dirac / Malcolm O'Brien
#:     (O.C.U.) and Aretha Schechner / Daniel Taft (U.S.N.) -- for the 500s.
#:   * D87's two 0xE060 branches CREATE the two factions' casts at the same
#:     entity ids: id 1 = typecode 0|1 (Son|Norman), id 2 = 2|4
#:     (Viduka|Goodwin), id 3 = 3|5 (Miura|McAllister), ids 5..0x17 = 8..20 |
#:     23..35 (operator k | operator k+15). D07 pairs 30|32, 31|33, 0..14 |
#:     15..29 and 34..37 | 38..41 the same way. That pairing is SE's, and it
#:     is what usn_counterpart() applies.
#:
#: So: FMO_UDP_POP_NPC is the HQ roster; the occupation and frontline rosters
#: are DERIVED from it by recasting the story keys (FMO_UDP_POP_NPC_OCC /
#: FMO_UDP_POP_NPC_FZ override them outright, same grammar); and each band's
#: U.S.N. roster is derived by SE's pairing (FMO_UDP_POP_NPC_USN overrides the
#: HQ one). Positions are shared across bands and factions ON PURPOSE: one
#: script, one map (FMO_ZONE_MAPNO), one set of marks. WARNING: Ann Fuller (106) /
#: Doria Knox (107) as the occupation operators is an ASSUMPTION -- they are
#: the two D87 people no HQ event row names, and the occupation rows speak as
#: "the Operator"; nothing in the data ties them to a key.
_USN_STAFF = {100: 101, 102: 104, 103: 105, 106: 107,   # D87, per its E060 branches
              30: 32, 31: 33}                            # D07, per its E060 branches


def usn_counterpart(typecode):
    """The U.S.N. catalogue record SE's own script creates in place of an
    O.C.U. one: the named staff by table, an attendant by the same branch
    offset (D87 108..122 -> 123..137; D07 0..14 -> 15..29, 34..37 -> 38..41).
    A U.S.N. or unknown typecode is returned as is, so deriving twice is
    harmless."""
    if typecode in _USN_STAFF:
        return _USN_STAFF[typecode]
    if 108 <= typecode <= 122 or 0 <= typecode <= 14:
        return typecode + 15
    if 34 <= typecode <= 37:
        return typecode + 4
    # the Coliseum catalogue (200..235, AH/F99/D47): 0|1, then k|k+17
    if typecode == 200:
        return 201
    if 202 <= typecode <= 216:
        return typecode + 17
    return typecode


_PERSON_NAMES = {(r[0], r[1]) for r in fmoworld.NPC_CATALOGUE.values()
                 if not r[0].startswith("NPC")} if fmoworld else set()


def is_person_name(names):
    """True when a `=name1.name2` override is a catalogue PERSON's name (Henry
    Viduka) rather than a function label (Map.Selector)."""
    return tuple(names) in _PERSON_NAMES


def usn_roster(ocu_roster, ocu_names):
    """(roster, names) for the U.S.N. lobby, derived from the O.C.U. one.
    Function labels (`=Map.Selector`) carry over; a personal name does not
    when the part is recast, because the catalogue supplies the U.S.N.
    person's own."""
    roster, names = [], {}
    for uid, utype, pos, cat in ocu_roster:
        ncat = usn_counterpart(cat) if cat is not None else None
        roster.append((uid, utype, pos, ncat))
        if uid in ocu_names and not (ncat != cat and is_person_name(ocu_names[uid])):
            names[uid] = ocu_names[uid]
    return roster, names


def derive_band_roster(hq_roster, hq_names, recast, rekey=None):
    """A band's roster from the HQ one: `recast` = {key: typecode or
    (typecode, 'Label.Text')} replaces the person at a key (an old name
    override is dropped -- the catalogue names the new person), `rekey` =
    {old_key: new_key} moves an entry to a different entity key. Pure."""
    rekey = rekey or {}
    roster, names = [], {}
    for uid, utype, pos, cat in hq_roster:
        nuid = rekey.get(uid, uid)
        if uid in recast or nuid in recast:
            spec = recast.get(uid, recast.get(nuid))
            ncat, label = (spec if isinstance(spec, tuple) else (spec, None))
            roster.append((nuid, utype, pos, ncat))
            if label:
                n1, _, n2 = label.partition(".")
                names[nuid] = (n1, n2)
        else:
            roster.append((nuid, utype, pos, cat))
            if uid in hq_names:
                names[nuid] = hq_names[uid]
    return roster, names


def _parse_spec_keep_names(spec):
    """_parse_pop_npc without its side effect on POP_NPC_NAMES."""
    saved = dict(POP_NPC_NAMES)
    try:
        roster = _parse_pop_npc(spec)
        return roster, dict(POP_NPC_NAMES)
    finally:
        POP_NPC_NAMES.clear()
        POP_NPC_NAMES.update(saved)


#: Occupation band: the Operator (Fuller) at the first briefing key, an
#: attendant at the second (the sergeant's key plays `nina_event` there).
OCC_RECAST = {0x82081010: 106, 0x82081020: (112, "Mission.Briefing")}
#: Frontline band: the tutorial's people -- Dirac at `ope_event`, O'Brien as
#: the senior sergeant -- and the sergeant's key becomes `tag_secretary`.
FZ_RECAST = {0x82081010: 30, 0x82080110: 31, 0x82080120: (36, "Secretary")}
FZ_REKEY = {0x82081020: 0x82080120}

POP_NPC_USN_SPEC = os.environ.get("FMO_UDP_POP_NPC_USN", "").strip()
POP_NPC_OCC_SPEC = os.environ.get("FMO_UDP_POP_NPC_OCC", "").strip()
POP_NPC_FZ_SPEC = os.environ.get("FMO_UDP_POP_NPC_FZ", "").strip()
#: VERIFIED: THE COLISEUM CAST (2026-09-09). A player stood on 161's tier at
#: (22.45, 16.05, 34.45) -- "well, that's a coliseum" -- so the tougi script's
#: marks are walkable. SE (2005-10): "the Coliseum has a dedicated lobby where
#: you recruit battle-group members for the arena and register at the
#: reception". Its event table AI/F08/D44 (D48 U.S.N.) keys EIGHT
#: `tag_registration` desks 0x82080900..907, four `tag_colisum` 0x82080910..913,
#: `tag_kansen` (spectate) 0x82080920, four `tag_debriefing` 0x82080710..713,
#: plus the usual counters. POSITIONS are the script's own PLACE marks for its
#: ambient cast (ids 0x13..0x28, on the 16.0 / 16.73 / 19.0 / 19.13 / 20.0
#: tiers); WHICH desk stands at WHICH mark was SE-server data and is a
#: labelled guess, exactly as the HQ roster was. Typecodes 200..235 are the
#: Coliseum's own catalogue (fmoworld). FMO_UDP_POP_NPC_COL overrides.
#: KEY: THE DESK MARKS, third reading (live, 2026-09-09: "this guy's body
#: is in the desk"). Two rules, both learned the hard way:
#:
#:  (1) ONLY a PLACED-BUT-NEVER-CREATED mark is a server slot. The tougi
#:      script's ambient cast (ids 0x13..0x28) is created AND placed by the
#:      script itself, so popping our own body on one of those marks puts two
#:      people in one spot -- which is what happened to Map.Selector (mark of
#:      script id 0x26) and Ranking.Board (id 0x18) at the y 19 counter.
#:      Both are DROPPED here rather than moved to a guessed spot.
#:  (2) One mark = ONE body (2026-09-09 earlier). N ids on a mark are cutscene
#:      actors, not N desks.
#:
#: What the event table says these desks ARE (AI/F08/D44, every row ungated
#: with an identical script -- so they are SEPARATE STATIONS of one function,
#: not one entity with a menu): 8x tag_registration, 4x tag_colisum,
#: tag_kansen, 4x tag_debriefing, 4x tag_battle_ranking, 6x tag_wapcon,
#: tag_battle_gate, 4x tag_scramble. We serve ONE of each function, on a
#: server mark, because the marks are the only positions SE left behind.
#: WARNING: STILL UNSERVED for want of a mark: tag_battle_gate (0x82080930),
#: tag_battle_ranking (0x8208094x), tag_wapcon (0x8208095x).
POP_NPC_COL_DEFAULT = (
    "0x82080900@29.40,16.05,28.70,-1.571#206=Registration.Desk;"
    "0x82080901@22.45,16.05,34.45,-1.571#203=Registration.Desk;"
    "0x82080910@21.30,16.00,17.20#204=Coliseum.Desk;"
    "0x82080710@1.30,19.00,8.25#216=Debriefing;"
    "0x82080711@8.25,19.00,1.30#218=Debriefing;"
    "0x82080110@42.00,22.20,0.00#200")
POP_NPC_COL_SPEC = os.environ.get("FMO_UDP_POP_NPC_COL", "").strip()

if POP_NPC_USN_SPEC:
    POP_NPC_USN, POP_NPC_USN_NAMES = _parse_spec_keep_names(POP_NPC_USN_SPEC)
else:
    POP_NPC_USN, POP_NPC_USN_NAMES = usn_roster(POP_NPC, POP_NPC_NAMES)
if POP_NPC_OCC_SPEC:
    POP_NPC_OCC, POP_NPC_OCC_NAMES = _parse_spec_keep_names(POP_NPC_OCC_SPEC)
else:
    POP_NPC_OCC, POP_NPC_OCC_NAMES = derive_band_roster(POP_NPC, POP_NPC_NAMES,
                                                        OCC_RECAST)
if POP_NPC_FZ_SPEC:
    POP_NPC_FZ, POP_NPC_FZ_NAMES = _parse_spec_keep_names(POP_NPC_FZ_SPEC)
else:
    POP_NPC_FZ, POP_NPC_FZ_NAMES = derive_band_roster(POP_NPC, POP_NPC_NAMES,
                                                      FZ_RECAST, FZ_REKEY)
POP_NPC_OCC_USN, POP_NPC_OCC_USN_NAMES = usn_roster(POP_NPC_OCC, POP_NPC_OCC_NAMES)
POP_NPC_FZ_USN, POP_NPC_FZ_USN_NAMES = usn_roster(POP_NPC_FZ, POP_NPC_FZ_NAMES)
POP_NPC_COL, POP_NPC_COL_NAMES = _parse_spec_keep_names(POP_NPC_COL_SPEC or POP_NPC_COL_DEFAULT)
POP_NPC_COL_USN, POP_NPC_COL_USN_NAMES = usn_roster(POP_NPC_COL, POP_NPC_COL_NAMES)

#: VERIFIED: THE LAYOUT FILE (FMO_NPC_LAYOUT, 2026-09-11) -- what the lobby NPC
#: editor writes. A band the file carries REPLACES that band's env roster
#: below (U.S.N. still derived by SE's pairing); a band it lacks is untouched.
#: Re-read on its mtime by fmolayout.Layout, so band_rosters() -- built on
#: every call -- sees an edit at once, and the NPC re-pop timer is what
#: carries it to a player already in the lobby.
NPC_LAYOUT = fmolayout.Layout() if fmolayout else None
_LAYOUT_ERR = [None]
#: key -> client_kind, from layout rows that carry `ckind` (the editor's
#: name-tag test: 0 = a peer object, what every NPC was sent as before
#: 2026-09-05). Rebuilt by band_rosters(); consulted by _npc_pop_record. Keyed
#: by entity key alone, so a key that appears in two bands shares one override.
NPC_CKIND_OVERRIDE = {}
