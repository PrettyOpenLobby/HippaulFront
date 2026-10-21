"""Selftest pins: the Frontline objective modes (frontobj.py, pvproom.py) and the
Resupply Items counter (resupply.py). No client, no socket; every global a pin
touches is restored."""
import struct
from .deps import fmoworld


T0 = 1_900_000_000.0


class _Ch:
    """A battle channel as pvproom / frontobj read it."""

    def __init__(self, acct, port, uid, pos=None):
        self.addr, self.account, self.key = ("198.51.100.20", port), acct, b"1234battle"
        self.popped, self.pending, self.pos, self.pos_src = True, [], pos, "state"
        self.alias_of, self._uid = {}, uid

    def self_unit(self):
        return self._uid


def _recs(ch, cmd=None):
    out = [(struct.unpack_from("<I", r, 4)[0], r[0x10:]) for r in ch.pending]
    return [o for o in out if cmd is None or o[0] == cmd]


def _pairs(table):
    return [struct.unpack_from("<II", table, 8 * i) for i in range(4)]


class _War:
    def __init__(self, sectors=None):
        self.data, self.calls = {"sectors": sectors or {}}, []

    def settle(self, tile, nation, won=True, pvp=False, now=None):
        self.calls.append((tile, nation, won, pvp))
        return {"nation": nation, "control": 50}, "pinned"


def objective_pins():
    """The mode rotation (Capture & Hold on by default, every mode on some
    map), then one whole battle of Capture the Flag, Beacon Delivery and
    Demolition each through pvproom.tick_chan: the block, the POPs, cmd 140 /
    141 with each client's own ids, the late joiner's cmd 148 holder table,
    the D25 lines by side, and the verdicts."""
    from . import (battleend, battlepop, frontobj, missionblock, pvproom as _pv,
                   referee, trade, warstate)
    ok = True
    _knobs = ("MATCHING", "JUDGE", "WAR", "PVP_WAIT", "PVP_SELECTORS", "OBJECTIVE_KIND",
              "DEATH_WAIT", "NPC_VS_PILOTS", "CH_MAPS", "CH_KIND", "CH_HOLD", "CH_HALF",
              "CH_STILL", "CH_ZONE_ROWS", "MODES")
    _fk = ("OBJ_POP", "GRAB", "GRAB_DELAY", "POINT_HALF", "BEACON_SET", "DEMO_PLANT",
           "DEMO_FUSE", "DEMO_FIND", "DEMO_DEFUSE", "DEMO_HALF", "DEMO_ROWS", "BEACON_KIND")
    _sv = {k: getattr(_pv, k) for k in _knobs}
    _svf = {k: getattr(frontobj, k) for k in _fk}
    _sv_bs = dict(referee.BATTLE_STATE)
    _sv_mt = missionblock.MISSION_TIME
    _sv_ws = (warstate.war_state, warstate._war_tick, warstate.WAR)
    _sv_on = trade.session_for_account
    _sv_pd = dict(battleend.PILOT_DEATHS)
    _sv_sp = (battlepop.BATTLE_SPAWNS, battlepop.BATTLE_SPAWN_ROWS)
    try:
        _pv.MATCHING = _pv.JUDGE = _pv.WAR = True
        _pv.PVP_WAIT, _pv.PVP_SELECTORS, _pv.OBJECTIVE_KIND = 1200, {505, 509, 513}, 5
        _pv.DEATH_WAIT, _pv.NPC_VS_PILOTS = 300, False
        _pv.CH_KIND, _pv.CH_HOLD, _pv.CH_HALF, _pv.CH_STILL = 3, 20, 40, 12
        _pv.CH_ZONE_ROWS = {418: [(0.0, 30.0, 0.0), (500.0, 30.0, 0.0), (1000.0, 30.0, 0.0)]}
        frontobj.OBJ_POP, frontobj.GRAB, frontobj.GRAB_DELAY = True, 16, 2
        frontobj.POINT_HALF, frontobj.BEACON_SET, frontobj.BEACON_KIND = 40, 3, 11
        frontobj.DEMO_PLANT, frontobj.DEMO_FUSE, frontobj.DEMO_FIND = 10, 70, 5
        frontobj.DEMO_DEFUSE, frontobj.DEMO_HALF = 20, 32
        frontobj.DEMO_ROWS = {418: {
            0: [(-300.0, 30.0, -60.0), (-300.0, 30.0, 60.0), (-150.0, 30.0, 0.0),
                (0.0, 30.0, -60.0), (0.0, 30.0, 60.0)],
            1: [(1300.0, 30.0, -60.0), (1300.0, 30.0, 60.0), (1150.0, 30.0, 0.0),
                (1000.0, 30.0, -60.0), (1000.0, 30.0, 60.0)]}}
        battlepop.BATTLE_SPAWNS = True
        battlepop.BATTLE_SPAWN_ROWS = dict(battlepop.BATTLE_SPAWN_ROWS)
        battlepop.BATTLE_SPAWN_ROWS[418] = {"a": (-400.0, 0.0), "b": (1400.0, 0.0), "y": 26.0,
                                            "a_slots": [(-400.0, 0.0)],
                                            "b_slots": [(1400.0, 0.0)], "source": "pin"}
        missionblock.MISSION_TIME = 1800
        trade.session_for_account = lambda a: (object() if str(a).startswith("fo:")
                                               else _sv_on(a))
        _ws = _War({"94101": {"nation": 2}})
        warstate.war_state, warstate._war_tick, warstate.WAR = (lambda: _ws), (lambda st: None), "1"

        # --- the assignment: FMO_PVP_MODES parses; unset FMO_PVP_CH_MAPS = the
        # rotation over the Frontline maps in map order, every mode on 1/5 of
        # them; the old switch, when set, still wins
        _maps = [15, 16, 24, 25, 26, 29, 30]
        _pm = _pv.parse_modes("ch,ctf,471:demo")
        _asg = ([_pv.assigned_mode(m, _pv.parse_modes(""), _maps) for m in _maps]
                == ["ch", "ctf", "tdm", "demo", "beacon", "ch", "ctf"]
                and _pm == {"rotation": ("ch", "ctf"), "maps": {471: "demo"}}
                and _pv.parse_modes("off")["rotation"] == ("tdm",))
        try:
            _pv.parse_modes("ch,flag")
            _asg = False
        except ValueError:
            pass
        _pv.CH_MAPS, _pv.MODES = None, _pv.parse_modes("")
        _real = _pv.pvp_maps()
        _counts = {}
        for m in _real:
            _counts[_pv.assigned_mode(m)] = _counts.get(_pv.assigned_mode(m), 0) + 1
        _first_ch = next((m for m in _real if _pv.assigned_mode(m) == "ch"), None)
        _asg &= (len(_real) >= 5 and set(_counts) == set(_pv.MODE_NAMES)
                 and max(_counts.values()) - min(_counts.values()) <= 1
                 and _pv.mode_for(418, maps={418}) == "ch"
                 and _pv.mode_for(471, maps={418}) == "tdm"
                 and _pv.mode_for(471, maps=set()) == "tdm")
        _pv.MODES = _pv.parse_modes("418:ch")
        _kn = _pv.block_knobs(*_pv.peek(509, 418, T0), side=1, tile=94101)
        _asg &= (_kn.get("objective_kind") == 3 and len(_kn.get("rects", ())) == 3)
        print(f"  frontline modes: FMO_PVP_MODES parses (rotation, map:mode, off); with "
              f"FMO_PVP_CH_MAPS unset the {len(_real)} Frontline maps take "
              f"{'/'.join(_pv.DEFAULT_ROTATION)} in turn ({_counts}; the first "
              f"Capture & Hold map is {_first_ch}), so Capture & Hold is ON by default; "
              f"a Capture & Hold room's block carries kind 3 and three areas; the old "
              f"switch still wins: {'OK' if _asg else 'FAIL'}")
        ok &= _asg

        # --- the wire layouts: cmd 140 = obj, holder, four pairs (slot obj&3);
        # cmd 141 = obj, the pairs after, f32 x/y/z at +0x24; D25 line numbers
        _s = frontobj.record_seize(0x3A01, 0x205, [(0x3A01, 0x205), (0x3A00, 0x1001)])
        _d = frontobj.record_drop(0x3A00, (5.0, 30.0, -7.0), [])
        _lay = (struct.unpack_from("<II", _s, 0)[0] == 0x10 + 0x28
                and struct.unpack_from("<I", _s, 4)[0] == 140
                and struct.unpack_from("<II", _s, 0x10) == (0x3A01, 0x205)
                and _pairs(_s[0x18:0x38]) == [(0x3A00, 0x1001), (0x3A01, 0x205), (0, 0), (0, 0)]
                and struct.unpack_from("<I", _d, 4)[0] == 141 and len(_d) == 0x10 + 0x30
                and struct.unpack_from("<3f", _d, 0x10 + 0x24) == (5.0, 30.0, -7.0)
                and frontobj.demo_msg("plant", 2) == (12 << 16) | 3
                and frontobj.demo_msg("planted", 4) == (12 << 16) | 19
                and frontobj.demo_msg("count", left=5) == (12 << 16) | 9
                and frontobj.demo_msg("defuse", 0) == (12 << 16) | 20
                and frontobj.demo_msg("dcount", left=1) == (12 << 16) | 35
                and frontobj.demo_msg("defused") == (12 << 16) | 36
                and len({o & 3 for o in [frontobj.CONTAINER_ID, *frontobj.BEACON_IDS.values()]}) == 3)
        print(f"  frontline objects: cmd 140 = object/holder + the four-pair table (slot "
              f"object & 3, 0x28 B), cmd 141 = object + table + f32 x/y/z at +0x24 "
              f"(0x30 B), the D25 Demolition line numbers, object ids in distinct "
              f"slots: {'OK' if _lay else 'FAIL'}")
        ok &= _lay

        # --- CAPTURE THE FLAG on 418: O.C.U. al + cy, U.S.N. bo
        _pv.MODES = _pv.parse_modes("418:ctf")
        _pv.reset()
        _role, _plan = _pv.peek(509, 418, T0)
        _kn = _pv.block_knobs(_role, _plan, side=1, tile=94101)
        _rf = {r[0]: r for r in _kn.get("rects", ())}
        _blk = (_plan["mode"] == "ctf" and _kn.get("objective_kind") == 10
                and sorted(_rf) == [15, 16] and _rf[15][1] == 1 and _rf[16][1] == 2
                and struct.unpack_from("<6f", _rf[15], 4) == (-440, 22, -40, -360, 46, 40))
        _fd = missionblock.mission_fields(**_kn)
        _blk &= any(l == "ObjectiveKind" and r == struct.pack("<I", 10) for l, _o, r, _s2 in _fd)
        for acct, nat, t in (("fo:al", 1, 0), ("fo:cy", 1, 1), ("fo:bo", 2, 5)):
            _pv.join(acct, acct, 509, 418, 94101, nat, T0 + t)
        al = _Ch("fo:al", 7001, 0x1001, (-400.0, 31.0, 0.0))
        cy = _Ch("fo:cy", 7002, 0x1002, (-380.0, 31.0, 0.0))
        bo = _Ch("fo:bo", 7003, 0x1003, (1400.0, 31.0, 0.0))
        for c in (al, cy, bo):
            for o in (al, cy, bo):
                if o is not c:
                    c.alias_of[o.addr] = 0x200 + o._uid - 0x1000
        for c, t in ((al, 6), (cy, 7), (bo, 10)):
            _pv.tick_chan(c, c.addr, T0 + t)
        _room = _pv.room_of("fo:al")
        _pops = [b for _c, b in _recs(al, 7)]
        _pop_ok = (len(_pops) == 1 and struct.unpack_from("<I", _pops[0], 4)[0] == 0x3A00
                   and _pops[0][0x8E] == 0x58 and _pops[0][0x8B] == 2
                   and struct.unpack_from("<3f", _pops[0], 0x1C) == (500.0, 30.0, 0.0)
                   and _room["started"] is not None)
        for c in (al, cy, bo):
            c.pending.clear()
        al.pos = (505.0, 31.0, 6.0)                   # al walks onto the container
        _pv.tick_chan(al, al.addr, T0 + 20)
        _pv.tick_chan(bo, bo.addr, T0 + 21)
        _sa = [b for _c, b in _recs(al, 140)]
        _sb = [b for _c, b in _recs(bo, 140)]
        _seize = (len(_sa) == 1 and len(_sb) == 1
                  and struct.unpack_from("<II", _sa[0], 0) == (0x3A00, 0x1001)
                  and struct.unpack_from("<II", _sb[0], 0) == (0x3A00, 0x201)
                  and _pairs(_sb[0][8:0x28])[0] == (0x3A00, 0x201))
        # al carries it to the ENEMY's point: nothing; then he is destroyed: drop
        al.pos = (1400.0, 31.0, 0.0)
        _pv.tick_chan(al, al.addr, T0 + 30)
        _wrong = _room["verdict"] is None
        al.pos = (900.0, 31.0, 0.0)
        _pv.tick_chan(al, al.addr, T0 + 30.5)
        battleend.PILOT_DEATHS["fo:al"] = T0 + 31
        for c in (al, cy, bo):
            c.pending.clear()
        _pv.tick_chan(cy, cy.addr, T0 + 32)
        _pv.tick_chan(bo, bo.addr, T0 + 32)
        _db = [b for _c, b in _recs(bo, 141)]
        _drop = (len(_db) == 1 and struct.unpack_from("<I", _db[0], 0)[0] == 0x3A00
                 and _pairs(_db[0][4:0x24]) == [(0, 0)] * 4
                 and struct.unpack_from("<3f", _db[0], 0x24) == (900.0, 31.0, 0.0)
                 and _room["obj"]["objects"][0x3A00]["holder"] is None)
        # bo stands on it: not before FMO_PVP_GRAB_DELAY, then it is his
        bo.pos = (902.0, 31.0, 1.0)
        _pv.tick_chan(bo, bo.addr, T0 + 33)
        _held_early = _room["obj"]["objects"][0x3A00]["holder"]
        _pv.tick_chan(bo, bo.addr, T0 + 35)
        _regrab = _held_early is None and _room["obj"]["objects"][0x3A00]["holder"] == "fo:bo"
        # a late joiner (U.S.N. dy): its cmd 148 names bo by ITS alias for him
        _lr, _lroom = _pv.peek(509, 418, T0 + 36)
        _pv.join("fo:dy", "fo:dy", 509, 418, 94101, 2, T0 + 36)
        dy = _Ch("fo:dy", 7004, 0x1004, (1300.0, 31.0, 0.0))
        dy.alias_of[bo.addr] = 0x2A0
        _pv.tick_chan(dy, dy.addr, T0 + 37)
        _r148 = [b for _c, b in _recs(dy, 148)]
        _dpop = [b for _c, b in _recs(dy, 7)]
        _late = (_lr == "late" and len(_r148) == 1
                 and _pairs(_r148[0][0x94:0xB4])[0] == (0x3A00, 0x2A0)
                 and _r148[0][8:16] == bytes([15, 1, 2, 0, 16, 2, 2, 0])
                 and len(_dpop) == 1
                 and struct.unpack_from("<3f", _dpop[0], 0x1C) == (902.0, 31.0, 1.0))
        # bo walks it home: U.S.N. wins
        bo.pos = (1395.0, 31.0, 10.0)
        _pv.tick_chan(bo, bo.addr, T0 + 40)
        _v = _room.get("verdict") or {}
        _win = _v.get("winner") == 2 and "delivered the container" in _v.get("why", "")
        print(f"  capture the flag: kind 10, delivery points 15/16 owned by their nation; "
              f"the container POPs as class 6 sub 2 at the middle point; a pilot on it "
              f"seizes it (cmd 140, his own id on his client, the alias on the "
              f"other's); the ENEMY's point does not count; destroyed, he drops it "
              f"where he fell (cmd 141, empty table); the next pilot takes it after "
              f"the grab delay; a late joiner's cmd 148 holds the pair by its own "
              f"alias; carried home it wins: "
              f"{'OK' if all((_blk, _pop_ok, _seize, _wrong, _drop, _regrab, _late, _win)) else 'FAIL'}"
              + ("" if all((_blk, _pop_ok, _seize, _wrong, _drop, _regrab, _late, _win)) else
                 f" {(_blk, _pop_ok, _seize, _wrong, _drop, _regrab, _late, _win)}"))
        ok &= all((_blk, _pop_ok, _seize, _wrong, _drop, _regrab, _late, _win))
        battleend.PILOT_DEATHS.pop("fo:al", None)

        # --- BEACON DELIVERY on 418: bo (U.S.N.) carries the O.C.U. beacon into
        # the target area: O.C.U. wins (SE: setting the enemy's beacon)
        _pv.MODES = _pv.parse_modes("418:beacon")
        _pv.reset()
        _kn = _pv.block_knobs(*_pv.peek(509, 418, T0), side=2, tile=94101)
        _bk = (_kn.get("objective_kind") == 11 and [r[0] for r in _kn["rects"]] == [10]
               and _kn["rects"][0][1] == 0)
        for acct, nat, t in (("fo:al", 1, 0), ("fo:bo", 2, 5)):
            _pv.join(acct, acct, 509, 418, 94101, nat, T0 + t)
        al = _Ch("fo:al", 7011, 0x1001, (-400.0, 31.0, 0.0))
        bo = _Ch("fo:bo", 7012, 0x1003, (1400.0, 31.0, 0.0))
        _pv.tick_chan(al, al.addr, T0 + 6)
        _pv.tick_chan(bo, bo.addr, T0 + 10)
        _room = _pv.room_of("fo:al")
        _bp = {struct.unpack_from("<I", b, 4)[0]: b for _c, b in _recs(bo, 7)}
        _bk &= (sorted(_bp) == [0x3A01, 0x3A02] and _bp[0x3A01][0x7C] == 1
                and _bp[0x3A02][0x7C] == 2
                and struct.unpack_from("<3f", _bp[0x3A01], 0x1C) == (0.0, 30.0, 0.0))
        bo.pending.clear()
        bo.pos = (3.0, 31.0, 2.0)
        _pv.tick_chan(bo, bo.addr, T0 + 20)
        _s1 = [b for _c, b in _recs(bo, 140)]
        _bk &= (len(_s1) == 1 and struct.unpack_from("<II", _s1[0], 0) == (0x3A01, 0x1003)
                and _pairs(_s1[0][8:0x28])[1] == (0x3A01, 0x1003))
        bo.pos = (500.0, 31.0, 0.0)
        _pv.tick_chan(bo, bo.addr, T0 + 30)
        bo.pos = (520.0, 31.0, 0.0)                    # moved: the set restarts
        _pv.tick_chan(bo, bo.addr, T0 + 32)
        _pv.tick_chan(bo, bo.addr, T0 + 34)
        _mid = _room["verdict"] is None
        _pv.tick_chan(bo, bo.addr, T0 + 35)
        _v = _room.get("verdict") or {}
        _bk &= _mid and _v.get("winner") == 1 and "beacon was set" in _v.get("why", "")
        print(f"  beacon delivery: kind 11, one grey target area (id 10, 'A'); the two "
              f"beacons POP with their nation at body+0x7C (32:19 names the army); "
              f"U.S.N. takes O.C.U.'s (slot 1); moving inside the area restarts the "
              f"set; standing still {frontobj.BEACON_SET} s sets it and O.C.U. WINS (the "
              f"beacon's army, SE's rule): {'OK' if _bk else 'FAIL'}")
        ok &= _bk

        # --- DEMOLITION on 418: the sector is U.S.N.'s, so U.S.N. defends
        _pv.MODES = _pv.parse_modes("418:demo")
        _pv.reset()
        _role, _plan = _pv.peek(509, 418, T0)
        _ka = _pv.block_knobs(_role, _plan, side=1, tile=94101)
        _kd = _pv.block_knobs(_role, _plan, side=2, tile=94101)
        _dm = (_ka.get("objective_kind") == 3 and _kd.get("objective_kind") == 4
               and [r[0] for r in _ka["rects"]] == [10, 11, 12, 13, 14]
               and all(r[1] == 2 for r in _ka["rects"])
               and _ka["rect_groups"][0][:2] == bytes([1, 5])
               and struct.unpack_from("<3f", _ka["rects"][2], 4) == (1118.0, 22.0, -32.0)
               and _pv.defender_for(None, 1, _War())[0] == 2
               and _pv.defender_for(94101, 2, _War({"94101": {"nation": 1}}))[0] == 1)
        for acct, nat, t in (("fo:al", 1, 0), ("fo:bo", 2, 5)):
            _pv.join(acct, acct, 509, 418, 94101, nat, T0 + t)
        al = _Ch("fo:al", 7021, 0x1001, (-400.0, 31.0, 0.0))
        bo = _Ch("fo:bo", 7022, 0x1003, (1400.0, 31.0, 0.0))
        _pv.tick_chan(al, al.addr, T0 + 6)
        _pv.tick_chan(bo, bo.addr, T0 + 10)
        _room = _pv.room_of("fo:al")
        _dm &= _room["obj"]["defender"] == 2 and not _recs(al, 7)
        al.pending.clear()
        bo.pending.clear()
        al.pos = (1150.0, 31.0, 5.0)                  # area C
        for t in (20, 25, 30):
            _pv.tick_chan(al, al.addr, T0 + t)
        _pv.tick_chan(bo, bo.addr, T0 + 31)
        _la = [struct.unpack_from("<I", b, 8)[0] & 0xFFFF for _c, b in _recs(al, 133)]
        _owners = {b[0x0D] for _c, b in _recs(al, 133)}
        _plant = (_la == [3, 9, 17] and _owners == {2} and not _recs(bo, 133)
                  and (_room["obj"]["bomb"] or {}).get("area") == 2)
        # the defender finds it (5 s) and defuses it (20 s): U.S.N. wins
        bo.pos = (1150.0, 31.0, -5.0)
        for t in (40, 45, 50, 60, 64):
            _pv.tick_chan(bo, bo.addr, T0 + t)
        _mid = _room["verdict"] is None
        _pv.tick_chan(bo, bo.addr, T0 + 65)
        _lb = [struct.unpack_from("<I", b, 8)[0] & 0xFFFF for _c, b in _recs(bo, 133)]
        _v = _room.get("verdict") or {}
        _defuse = (_mid and _lb[0] == 22 and _lb[-1] == 36 and 29 in _lb
                   and _v.get("winner") == 2 and "defused" in _v.get("why", "")
                   and not any((struct.unpack_from("<I", b, 8)[0] & 0xFFFF) >= 20
                               for _c, b in _recs(al, 133)))
        # a second room: nobody defuses, the fuse ends it for the attacker
        _pv.reset()
        for acct, nat, t in (("fo:al", 1, 0), ("fo:bo", 2, 5)):
            _pv.join(acct, acct, 509, 418, 94101, nat, T0 + t)
        al.pending.clear()
        al.pos, bo.pos = (-400.0, 31.0, 0.0), (1400.0, 31.0, 0.0)
        _pv.tick_chan(al, al.addr, T0 + 6)
        _pv.tick_chan(bo, bo.addr, T0 + 10)
        _room = _pv.room_of("fo:al")
        al.pos = (1000.0, 31.0, 60.0)                 # area E
        _pv.tick_chan(al, al.addr, T0 + 20)
        _pv.tick_chan(al, al.addr, T0 + 30)
        _tv_bomb = frontobj.time_verdict(_room, {1: 1, 2: 1}, {1, 2}, T0 + 5000, 1800)
        _pv.tick_chan(bo, bo.addr, T0 + 99)
        _mid2 = _room["verdict"] is None
        _pv.tick_chan(bo, bo.addr, T0 + 100)
        _v = _room.get("verdict") or {}
        _boom = (_mid2 and _tv_bomb is None and _v.get("winner") == 1
                 and "went off" in _v.get("why", ""))
        _tv = frontobj.time_verdict({"obj": {"mode": "demo", "bomb": None, "defender": 2},
                                     "started": T0}, {1: 3, 2: 1}, {1, 2}, T0 + 1800, 1800)
        _boom &= _tv[0] == 2
        print(f"  demolition: the sector holder (U.S.N.) defends: kind 4 for it, 3 for "
              f"the attacker, five areas A..E owned by the defender; the attacker in C "
              f"plants in {frontobj.DEMO_PLANT} s with the D25 lines (start C, the 5 s "
              f"count, planted at C) to O.C.U. only, every cmd 133 carrying owner 2; "
              f"the defender finds it in {frontobj.DEMO_FIND} s and defuses it in "
              f"{frontobj.DEMO_DEFUSE} s: U.S.N. wins; undefused, it goes off after "
              f"{frontobj.DEMO_FUSE} s: O.C.U. wins; no plant by the limit: the "
              f"defender: {'OK' if _dm and _plant and _defuse and _boom else 'FAIL'}"
              + ("" if _dm and _plant and _defuse and _boom else
                 f" {(_dm, _plant, _defuse, _boom, _la, _lb)}"))
        ok &= _dm and _plant and _defuse and _boom
    finally:
        for k, v in _sv.items():
            setattr(_pv, k, v)
        for k, v in _svf.items():
            setattr(frontobj, k, v)
        _pv.reset()
        referee.BATTLE_STATE.clear()
        referee.BATTLE_STATE.update(_sv_bs)
        missionblock.MISSION_TIME = _sv_mt
        warstate.war_state, warstate._war_tick, warstate.WAR = _sv_ws
        trade.session_for_account = _sv_on
        battleend.PILOT_DEATHS.clear()
        battleend.PILOT_DEATHS.update(_sv_pd)
        battlepop.BATTLE_SPAWNS, battlepop.BATTLE_SPAWN_ROWS = _sv_sp
    return ok


def _arm_01a7(payload, owned, money):
    """The 0x01A7 arm 0x6117F796, in Python, over a list of owned records
    (bytearrays): money := +0x1E8; each of `count` rows with +0x12 == 1 clears
    the owned record's +0x12 (0x61177BE0(serial, 0)), then a non-zero 8 bytes
    at row + 0xF0 removes that stock record (0x6117A640). Returns (money,
    owned, lines)."""
    b = bytes(payload)[:0x1FC]
    money = struct.unpack_from("<I", b, 0x1E8)[0]
    lines = []
    for i in range(max(0, struct.unpack_from("<i", b, 0)[0])):
        row = 8 + 0x18 * i
        if b[row + 0x12] != 1:
            continue
        ser = b[row:row + 8]
        hit = next((o for o in owned if bytes(o[:8]) == ser), None)
        if hit is None or hit[0x12] == 0:
            continue
        hit[0x12] = 0
        st = b[row + 0xF0:row + 0xF8]
        if any(st):
            owned[:] = [o for o in owned if bytes(o[:8]) != st]
            lines.append("8:67")
        else:
            lines.append("8:66")
    return money, owned, lines


def resupply_pins():
    """0x0180 marks the item used on the record; 0x01A6's 508 bytes parse to
    the used rows and their stock; the server prices them from its own table,
    refills, debits and answers 0x01A7 then message 1, and the 0x01A7 arm
    (emulated from 0x6117F796) clears the flags, spends the stock and stores
    the new balance; too little money is refused with nothing changed."""
    from . import economy, inventory, packet, pushes, resupply
    ok = True
    _sv = (resupply.RESUPPLY, resupply.ITEM_USED, economy.part_price)

    class _Sess:
        def __init__(self, char):
            self.char, self.peer, self.commits, self._seq = char, "rs:pin", [], 0x300

        def playing_char(self):
            return self.char

        def commit(self, why):
            self.commits.append(why)

        def stored_money(self):
            return int(self.char.get("money", 0)), 0

        def credit_money(self, why, money=0, contribution=0):
            self.char["money"] = max(0, int(self.char.get("money", 0)) + int(money))
            self.commits.append(why)
            return self.char["money"], 0

        def reply_seq(self):
            self._seq += 1
            return self._seq

    def _rec(serial, iid, kind=0x13, used=0):
        r = bytearray(inventory.item_record(serial, iid, kind))
        r[0x12] = used
        return bytes(r)

    from . import charstore
    _sv_cs = charstore.CHAR_STORE
    try:
        charstore.CHAR_STORE = charstore.CHAR_STORE or "pin"
        resupply.RESUPPLY = resupply.ITEM_USED = True
        economy.part_price = lambda kind, iid, char=None, serial=None, table=None: (
            {(0x13, 2): (50, "shop row"), (0x13, 3): (100, "shop row")}.get((kind, iid), (0, "none")))
        # a setup whose consumable slots 11..13 hold Repair II, Repair III, Repair II
        block = bytearray(inventory.REPLY_0166_LEN)
        for i, (ser, iid) in enumerate(((0x7001, 2), (0x7002, 3), (0x7003, 2))):
            at = inventory.SETUP_ITEM_OFF + (11 + i) * 0x18
            block[at:at + 0x18] = _rec(ser, iid)
        char = {"money": 1000, "setups": bytes(block).hex(),
                "items": [{"serial": 0x7101, "id": 3, "kind": 0x13}]}
        s = _Sess(char)

        # 0x0180: Repair II (0x7001) and Repair III (0x7002) used in battle
        for ser, iid in ((0x7001, 2), (0x7002, 3)):
            pl = bytes(0x10) + _rec(ser, iid, used=1)
            resupply.on_item_used(s, {"payload": pl, "conn": 1})
        _used = (resupply.is_used(char, 0x7001) and resupply.is_used(char, 0x7002)
                 and not resupply.is_used(char, 0x7003) and len(s.commits) == 2)
        print(f"  item used 0x0180: the 24-B record at +0x10 marks +0x12 = 1 on the "
              f"pilot's setups (0x61177D00 -> 0x61177BE0(.., 1)), an unused one stays "
              f"clear: {'OK' if _used else 'FAIL'}")
        ok &= _used

        # 0x01A6 as the window builds it: three rows, two used, Repair III
        # with a stock match (0x7101), the client's own total H$50
        req = bytearray(resupply.REQ_LEN)
        struct.pack_into("<iI", req, 0, 3, 50)
        for i, (ser, iid, u) in enumerate(((0x7001, 2, 1), (0x7002, 3, 1), (0x7003, 2, 0))):
            req[8 + 0x18 * i:8 + 0x18 * (i + 1)] = _rec(ser, iid, used=u)
        req[0xF8 + 0x18:0xF8 + 0x30] = _rec(0x7101, 3)
        rows, ctot = resupply.parse_request(bytes(req))
        _plan, _tot = resupply.plan(char, rows)
        _parse = (ctot == 50 and len(rows) == 2 and rows[1][1] is not None
                  and _tot == 50 and _plan[1][2] == 0 and _plan[0][2] == 50)
        outs = resupply.on_request(s, {"payload": bytes(req), "conn": 1})
        _p = [packet.parse(o) for o in outs or ()]
        body = _p[0]["payload"] if _p else b""
        owned = [bytearray(_rec(0x7001, 2, used=1)), bytearray(_rec(0x7002, 3, used=1)),
                 bytearray(_rec(0x7003, 2)), bytearray(_rec(0x7101, 3))]
        m, owned, lines = _arm_01a7(body, owned, 1000)
        _arm = (len(_p) == 2 and _p[0]["msg"] == pushes.MSG_RESUPPLY
                and len(body) == pushes.S1A7_LEN and _p[1]["msg"] == 0x0001
                and m == 950 and lines == ["8:66", "8:67"]
                and [o[0x12] for o in owned] == [0, 0, 0]
                and len(owned) == 3 and char["money"] == 950
                and not resupply.is_used(char, 0x7001) and not resupply.is_used(char, 0x7002)
                and not any(it["serial"] == 0x7101 for it in char["items"]))
        print(f"  resupply 0x01A6: two used rows (one with stock) parse, the server "
              f"prices the other from its shop table (H$50, the client showed H$50); "
              f"0x01A7 then message 1; through the arm (0x6117F796): money := 950, "
              f"8:66 + 8:67, both flags clear, the stock record gone, and the same on "
              f"the pilot's record: {'OK' if _parse and _arm else 'FAIL'}")
        ok &= _parse and _arm

        # too little money: refused, nothing touched
        resupply.on_item_used(s, {"payload": bytes(0x10) + _rec(0x7003, 2, used=1), "conn": 1})
        char["money"] = 10
        req2 = bytearray(resupply.REQ_LEN)
        struct.pack_into("<iI", req2, 0, 1, 50)
        req2[8:0x20] = _rec(0x7003, 2, used=1)
        _o2 = [packet.parse(o) for o in resupply.on_request(s, {"payload": bytes(req2), "conn": 1})]
        _poor = (len(_o2) == 1 and _o2[0]["msg"] == 0x0002 and char["money"] == 10
                 and resupply.is_used(char, 0x7003))
        # an event item (no shop price) with no stock: message 2, not a 34:1 lie
        char["money"] = 1000
        req3 = bytearray(resupply.REQ_LEN)
        struct.pack_into("<iI", req3, 0, 1, 0)
        req3[8:0x20] = _rec(0x7009, 9, used=1)
        _o3 = [packet.parse(o) for o in resupply.on_request(s, {"payload": bytes(req3), "conn": 1})]
        _poor &= len(_o3) == 1 and _o3[0]["msg"] == 0x0002 and char["money"] == 1000
        resupply.RESUPPLY = False
        _off = resupply.on_request(s, {"payload": bytes(req2), "conn": 1}) is None
        print(f"  resupply 0x01A6: H$50 with H$10 on file -> message 2 (34:2), the "
              f"item stays used and the money untouched; an item with no shop price "
              f"and no stock is refused the same way; FMO_RESUPPLY=0 leaves the old "
              f"bare message 1: {'OK' if _poor and _off else 'FAIL'}")
        ok &= _poor and _off
    finally:
        resupply.RESUPPLY, resupply.ITEM_USED, economy.part_price = _sv
        charstore.CHAR_STORE = _sv_cs
    return ok


def run():
    ok = objective_pins()
    ok &= resupply_pins()
    return ok
