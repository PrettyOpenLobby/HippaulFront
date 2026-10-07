"""The offline selftest (`python fmo.py --selftest`): no socket, no client."""
import os
import struct
import time
from .deps import fmodb, fmolayout, fmomsn, fmosectors, fmostore, fmowar, fmoworld, flat_globals


# --------------------------------------------------------------------------- #
#: Every env knob that puts a byte into the 0x014A block. Three assertions in
#: selftest() are "the block is otherwise all zeros", which is a property of the
#: DEFAULT server and is vacuously false the moment any of these is set on
#: purpose. Before 2026-09-04 they were guarded only against their OWN knobs, so
#: a run with FMO_STATUS_NATION=1 -- exactly the run FMO_ZONE_CONTROL requires,
#: because 0x611A3AA0 needs a nation of 1 or 2 -- printed three FAILs that meant
#: nothing. A selftest that cries wolf under the very configuration a feature
#: needs is worse than no selftest.
STATUS_ENV_KNOBS = ("FMO_RANK", "FMO_STATUS_MONEY", "FMO_STATUS_MP",
                    "FMO_STATUS_CONTRIB", "FMO_STATUS_SEX",
                    "FMO_STATUS_NATION", "FMO_STATUS_NAMES",
                    "FMO_STATUS_W7604", "FMO_STATUS_W7608",
                    "FMO_STATUS_WFD4", "FMO_STATUS_MARK")


def status_block_pristine():
    """True when no knob is deliberately putting bytes in the 0x014A block."""
    return not any(os.environ.get(k) for k in STATUS_ENV_KNOBS)


def status_block_zero_or_knobbed():
    """The zero-block property, held vacuously when a knob is filling it.

    WARNING: This RELAXES an assertion, so every caller prints which it did --
    a check that quietly stops checking is the failure `printed-check-is-not-a-
    gate` names.

    WARNING: 2026-09-08: the property is now "zero EXCEPT the active wanzer setup".
    That byte (S14A_ACTIVE_SETUP -> lobby+0x8B7) is not knob-driven decoration:
    the client feeds it to the only writer of lobby+0x3DF2, and serving ZERO
    un-marks setup 1 IN USE -- which is how "-Empty-" ended up Active over a
    Setup1 holding a complete Giza. So it is held out of the zero-block
    property DELIBERATELY and by name, not by loosening the comparison; its own
    value is pinned separately above.
    """
    return (not status_block_pristine()
            # 2026-09-10: and EXCEPT the class table + its rank group -- the
            # zeros there are what drew '???' / Lv 0 on the EXP Gain screen,
            # so they are served by default and pinned by name above.
            or status.reply_014a(rank=0, active_setup=0, class_table=False)
            == bytes(status.REPLY_014A_LEN))


def penalty_pins():
    """FRIENDLY-FIRE PENALTY + RETRAINING (penalty.py), no client. Returns ok.

    Pins: (1) the three script bytes are the pilot's CURRENT state in the
    0x014A and in every 0x015A builder (never an echo of zeros); (2) own-side
    pilot hits are noted, the other side's are not; the 0x017B lists the
    offender and the 0x017C adds ONE point per victim per battle, applied on
    the offender's own session, revoking at FMO_PENALTY_POINTS with a 0x015A
    carrying +0x418 = 1; (3) a revoked pilot is refused a battle sortie but
    not the retraining one; (4) event 211 counts a WON retraining battle once
    and clears the penalty at FMO_RETRAIN_WINS; (5) the self-POP level byte."""
    import tempfile as _tf
    import types as _ty
    from . import penalty as _pn
    ok = True
    H = 0x14                                   # packet header; payload after

    def _b3(pkt):
        return tuple(pkt[H + resultpush.S15A_B418:H + resultpush.S15A_B41A + 1])

    _was = (_pn.PENALTY, _pn.PENALTY_POINTS, _pn.RETRAIN_WINS, _pn.PENALTY_BATTLE_CUT,
            charstore.CHAR_STORE, dict(trade.LIVE_SESSIONS), resultpush.RESULT_PUSH,
            resultpush.RESULT_MONEY, dict(groupchannel.WORLD_PEERS))
    with _tf.TemporaryDirectory() as _td:
        try:
            flat_globals()["CHAR_STORE"] = os.path.join(_td, "chars.json")
            _pn.PENALTY, _pn.PENALTY_POINTS, _pn.RETRAIN_WINS = True, 2, 2
            _pn.PENALTY_BATTLE_CUT = True
            trade.LIVE_SESSIONS.clear()

            # (1) the bytes, served and pushed
            _rv = {"id": 7, "first": "Off", "last": "Ender", "penalty_points": 2,
                   "penalty_revoked": 1, "retrain_wins_needed": 2}
            _cl = {"id": 8, "first": "Cle", "last": "Ar"}
            _s14 = status.reply_014a(char=_rv)
            _s14c = status.reply_014a(char=_cl)
            _fp = scriptcall.flags_push_packet(_ty.SimpleNamespace(last_0159=bytes(1432)), 1, _rv)
            _rp = resultpush.result_push_packet(1, owned=b"", b418=0, b41a=0, pilot=_rv)
            _bs = session.Session.__new__(session.Session)
            _bs.peer, _bs.battle_settlement, _bs.last_0159 = "selftest-pen-br", None, b""
            _bs.playing_char = lambda: _rv
            _bs.stored_money = lambda: (0, 0)
            _bs.credit_money = lambda why, money=0, contribution=0: (money, contribution)
            _bs.credit_class_exp = lambda why, rows: {}
            _bs.commit = lambda what: None
            _bs.platoon_battle_settle = lambda won, money, rows, pay: (money, rows)
            resultpush.RESULT_PUSH, resultpush.RESULT_MONEY = True, 5
            _brp = _bs.battle_result_push(1, "selftest", won=True)
            _p1 = (_s14[_pn.S14A_REVOKED] == 1 and _s14[_pn.S14A_RETRAIN_WINS] == 2
                   and _s14c[_pn.S14A_REVOKED] == 0 and _s14c[_pn.S14A_RETRAIN_WINS] == 0
                   and _b3(_fp) == (1, 0, 2) and _b3(_rp) == (1, 0, 2)
                   and _brp is not None and _b3(_brp) == (1, 0, 2))
            print(f"  penalty bytes: 0x014A +0x3A/+0x594 and every 0x015A (flags push, "
                  f"battle result, pilot=) carry the pilot's CURRENT (1, 0, 2), never "
                  f"an echo; a clear pilot serves zeros: {'OK' if _p1 else 'FAIL'}")
            ok &= _p1

            # (2) friendly fire -> 0x017B -> 0x017C -> the offender's point
            class _Ch:
                def __init__(self, addr, acct, side, uid):
                    self.addr, self.account, self.key = addr, acct, b"k-battle"
                    self.pop_args, self._uid, self.alias_of = {"side": side}, uid, {}

                def self_unit(self):
                    return self._uid
            _sh = _Ch(("203.0.113.1", 5), "acct-shooter", 1, 0x1001)
            _vi = _Ch(("203.0.113.2", 5), "acct-victim", 1, 0x1002)
            _en = _Ch(("203.0.113.3", 5), "acct-enemy", 0, 0x1003)
            _sh.alias_of = {_vi.addr: 0x2001, _en.addr: 0x2002}
            groupchannel.WORLD_PEERS.update({_vi.addr: _vi, _en.addr: _en, _sh.addr: _sh})
            for _k in ("acct-victim", "acct-enemy"):
                referee.battle_state(_k, reset=True)
            _hl = struct.pack("<BBBB", 1, 2, 0, 0) + struct.pack("<IHBB", 0x2001, 50, 0, 1) \
                + struct.pack("<IHBB", 0x2002, 50, 0, 1)
            squad.squad_note_hits(_sh, _hl, 0x1001)
            squad.squad_note_hits(_sh, _hl, 0x1001)          # a second shot: still one row
            _ffv = referee.BATTLE_STATE["acct-victim"].get("friendly_fire_by") or {}
            _ffe = referee.BATTLE_STATE["acct-enemy"].get("friendly_fire_by") or {}
            _off = {"id": 7, "first": "Off", "last": "Ender"}
            _os = _ty.SimpleNamespace(account="acct-shooter", ip="203.0.113.1", peer="selftest-pen-off",
                                      roster=[_off], playing_char=lambda: _off,
                                      commit=lambda what: None, last_0159=b"")
            trade.LIVE_SESSIONS["203.0.113.1"] = _os
            _vs = session.Session("selftest-pen-vic")
            _vs.battle_key = lambda: "acct-victim"
            _r17b = _vs.penalty_report_push(1)
            _r17b2 = _vs.penalty_report_push(1)                # once per battle
            _wid = charlist.to_wire(7)
            _row = (_r17b[H + _pn.S17B_ROWS:H + _pn.S17B_ROWS + _pn.S17B_ROW_LEN]
                    if _r17b else bytes(_pn.S17B_ROW_LEN))
            _p2a = (list(_ffv) == ["acct-shooter"] and _ffv["acct-shooter"]["hits"] == 2
                    and not _ffe and _r17b is not None and _r17b2 is None
                    and struct.unpack_from("<H", _r17b, 6)[0] == _pn.MSG_PENALTY_REPORT
                    and len(_r17b) - H == _pn.S17B_LEN
                    and struct.unpack_from("<I", _r17b, H)[0] == 1
                    and struct.unpack_from("<I", _row, 0)[0] == _wid
                    and _row[_pn.R17B_FIRST:_pn.R17B_FIRST + 4] == b"Off\0"
                    and _row[_pn.R17B_LAST:_pn.R17B_LAST + 6] == b"Ender\0")
            print(f"  friendly fire: an own-side pilot hit is filed on the victim, the "
                  f"other side's is not; the 0x017B ({_pn.S17B_LEN}B, once per battle) "
                  f"lists the offender's wire id {_wid:#x} and names: "
                  f"{'OK' if _p2a else 'FAIL'}")
            ok &= _p2a
            _vote = packet.parse(packet.build(_pn.MSG_PENALTY_GIVE,
                                              struct.pack("<I", _wid) + bytes(16), 0x100))
            _vs.on_packet(_vote)
            _vs.on_packet(_vote)                               # a second YES: ignored
            _q1 = list(getattr(_os, "penalty_due", []))
            _g1 = gatetool.gate_ops_due(_os, 1)
            _pts1, _rev1 = _off.get("penalty_points"), _off.get("penalty_revoked")
            # a second battle, a second YES -> 2 points = FMO_PENALTY_POINTS (2 here)
            referee.battle_state("acct-victim", reset=True)
            squad.squad_note_hits(_sh, _hl, 0x1001)
            _vs.penalty_report_push(1)
            _vs.on_packet(_vote)
            _g2 = gatetool.gate_ops_due(_os, 1)
            _p2b = (len(_q1) == 1 and _pts1 == 1 and not _rev1 and _g1 == []
                    and _off.get("penalty_points") == 2 and _off.get("penalty_revoked") == 1
                    and _off.get("retrain_wins_needed") == 2 and len(_g2) == 1
                    and _b3(_g2[0]) == (1, 0, 2))
            print(f"  0x017C: ONE point per victim per battle, applied on the offender's "
                  f"own session at its keepalive; point 2 (FMO_PENALTY_POINTS here) "
                  f"revokes and pushes +0x418 = 1, +0x41A = 2: {'OK' if _p2b else 'FAIL'}")
            ok &= _p2b

            # (3) the sortie hook
            _ss = _ty.SimpleNamespace(playing_char=lambda: _off)
            _v0 = _pn.sortie_verdict(_ss, {"create": 0})
            _rt0 = _ss.retrain_sortie
            _v2 = _pn.sortie_verdict(_ss, {"create": 2})
            _v2c = _pn.sortie_verdict(_ty.SimpleNamespace(playing_char=lambda: _cl), {"create": 0})
            _p3 = (_v0 is not None and _v0[0] == charselect.FAIL_CODE and _rt0 is False
                   and _v2 is None and _ss.retrain_sortie is True and _v2c is None)
            print(f"  penalty sortie: a revoked pilot is refused a battle sortie (10:6), "
                  f"never the retraining one (create 2), which is noted; a clear pilot "
                  f"passes: {'OK' if _p3 else 'FAIL'}")
            ok &= _p3

            # (4) event 211: count a won retraining battle once, clear at 2
            _rec = bytearray(1432)
            struct.pack_into("<I", _rec, 0, _pn.EVENT_RETRAIN)
            _es = _ty.SimpleNamespace(peer="selftest-pen-rt", retrain_sortie=True,
                                      battle_settlement={"won": True}, commit=lambda w: None)
            _e1 = _pn.on_script_event(_es, bytes(_rec), _off, 211, [0] * 16, 1)
            _w1 = _pn.retrain_wins(_off)
            _e1b = _pn.on_script_event(_es, bytes(_rec), _off, 211, [0] * 16, 1)
            _es.battle_settlement = {"won": False}
            _e1l = _pn.on_script_event(_es, bytes(_rec), _off, 211, [0] * 16, 1)
            _es.battle_settlement = {"won": True}
            _e2 = _pn.on_script_event(_es, bytes(_rec), _off, 211, [0] * 16, 1)
            _p4 = (len(_e1) == 1 and _w1 == 1 and _b3(_e1[0]) == (1, 0, 2)
                   and _e1[0][H + resultpush.S15A_OWNED + (status.S14A_FLAGS11 - status.S14A_OWNED)
                              + _pn.RETRAIN_FLAG] == 1
                   and _e1b == [] and _e1l == [] and len(_e2) == 1
                   and _b3(_e2[0]) == (0, 0, 0) and not _off.get("penalty_revoked")
                   and _off.get("penalty_points") == 0 and _pn.retrain_wins(_off) == 0)
            print(f"  retraining (event 211): a WON retraining battle counts once (flag "
                  f"byte 175 = 1, pushed with +0x418 = 1), a loss counts nothing, win 2 "
                  f"restores clearance (bytes 0, points 0, byte 175 = 0): "
                  f"{'OK' if _p4 else 'FAIL'}")
            ok &= _p4
            # ... and through the session's 0x0159 arm: the 0x015A BEFORE the ack
            _rt = dict(_off, penalty_revoked=1, penalty_points=2, retrain_wins_needed=2)
            _ws = session.Session("selftest-pen-211")
            _ws._roster = [_rt]
            _ws.playing_char = lambda: _rt
            _ws.commit = lambda what: None
            _ws.retrain_sortie, _ws.battle_settlement = True, {"won": True}
            _wo = _ws.on_packet(packet.parse(packet.build(scriptcall.MSG_0159_REQ, bytes(_rec), 0x200)))
            _wids = [struct.unpack_from("<H", x, 6)[0] for x in _wo]
            _p4s = (_wids[-2:] == [resultpush.MSG_RESULT_PUSH, handshake.MSG_SESSION_START]
                    and _b3(_wo[-2]) == (1, 0, 2) and _pn.retrain_wins(_rt) == 1)
            print(f"  retraining via the 0x0159 arm: 0x015A (+0x418 = 1, +0x41A = 2, "
                  f"byte 175 = 1) then message 1, ids {[hex(i) for i in _wids]}: "
                  f"{'OK' if _p4s else 'FAIL'}")
            ok &= _p4s

            # (5) the self-POP penalty level
            _off["penalty_points"] = 3
            _px = _pn.pop_extra_for(_sh)
            _pop = fmoworld.record_pop(0x1001, unit_type=1, client_kind=3, extra=_px)
            _off["penalty_points"] = 1
            _px1 = _pn.pop_extra_for(_sh)
            _p5 = (_px == {_pn.POP_PENALTY_LEVEL: b"\x03"}
                   and _pop[fmoworld.REC_HDR + _pn.POP_PENALTY_LEVEL] == 3 and _px1 == {}
                   and _pn.battle_cut_pct(3) == 24 and _pn.battle_cut_pct(1) == 0
                   and _pn.battle_cut_pct(20) == 100)
            print(f"  penalty level: 3 points -> self-POP body+0x1C2 = 3 (the client "
                  f"cuts ammo/BP 24%, 0x611F7475); 1 point sends nothing: "
                  f"{'OK' if _p5 else 'FAIL'}")
            ok &= _p5
        finally:
            (_pn.PENALTY, _pn.PENALTY_POINTS, _pn.RETRAIN_WINS, _pn.PENALTY_BATTLE_CUT) = _was[:4]
            flat_globals()["CHAR_STORE"] = _was[4]
            trade.LIVE_SESSIONS.clear()
            trade.LIVE_SESSIONS.update(_was[5])
            resultpush.RESULT_PUSH, resultpush.RESULT_MONEY = _was[6], _was[7]
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_was[8])
            for _k in ("acct-victim", "acct-enemy"):
                referee.BATTLE_STATE.pop(_k, None)
    return ok


def hangar_permit_pins():
    """THE HANGAR MECHANIC'S PERMIT SALE (script event 206), no client.
    Returns ok.

    Pins, through the session's real 0x0159 arm: (1) a pilot with the money
    gets a 0x015A whose record carries p2 = 1 (D94 102) and a money delta of
    -price, then the 0x016B pass mint, then message 1; the debit and the pass
    are banked. (2) A pilot short of the price, and (3) a choice that is not a
    pass row, get the plain ack alone (p2 stays 0 = D94 103) and keep their
    money. The twin (2) is what fails if the sale stops checking the wallet."""
    import tempfile as _tf
    ok = True
    H = 0x14

    def _buy(char, choice):
        _rec = bytearray(scriptcall.S159_BODY_LEN)
        struct.pack_into("<I", _rec, 0, permits.HANGAR_SALE_EVENT)
        struct.pack_into("<I", _rec, scriptcall.S159_PARAMS, choice)
        _s = session.Session("selftest-hangar-permit")
        _s._roster = [char]
        _s.playing_char = lambda: char
        _s.commit = lambda what: None
        return _s.on_packet(packet.parse(packet.build(scriptcall.MSG_0159_REQ, bytes(_rec), 0x206)))

    def _ids(outs):
        return [struct.unpack_from("<H", x, 6)[0] for x in outs]

    _was = (charstore.CHAR_STORE, status.STATUS_NATION, zoneentry.NATION_PER_CHARACTER)
    with _tf.TemporaryDirectory() as _td:
        try:
            flat_globals()["CHAR_STORE"] = os.path.join(_td, "chars.json")
            # a pilot of nation 0 has no pass to buy, so an unpinned nation
            # would turn every case below into a refusal and pass vacuously
            status.STATUS_NATION, zoneentry.NATION_PER_CHARACTER = 1, False
            _rich = {"id": 21, "first": "Han", "last": "Gar", "money": 5000}
            _nat = zoneentry.nation_for_session(_rich, status.STATUS_NATION, "FMO_STATUS_NATION")[0]
            _want = permits.PASS_KEYS["oc"].get(_nat)
            _o1 = _buy(_rich, 1)
            _p = _o1[0] if _o1 else b""
            _p2 = (struct.unpack_from("<I", _p, H + scriptcall.S159_PARAMS + 4)[0]
                   if len(_p) > H + scriptcall.S159_PARAMS + 8 else None)
            _mon = (struct.unpack_from("<i", _p, H + resultpush.S15A_MONEY)[0]
                    if len(_p) > H + resultpush.S15A_MONEY + 4 else None)
            _held = [it["id"] for it in inventory.stored_items(_rich) if it["kind"] == permits.PASS_KIND]
            _h1 = (_nat == 1 and _want == 27
                   and _ids(_o1) == [resultpush.MSG_RESULT_PUSH, shop.MSG_ACQUIRE_REPLY,
                                     handshake.MSG_SESSION_START]
                   and _p2 == 1 and _mon == -permits.HANGAR_SALE_PRICE["oc"]
                   and _rich["money"] == 5000 - permits.HANGAR_SALE_PRICE["oc"]
                   and _held == [_want])
            print(f"  hangar permit (event 206, row 1 = oc, nation {_nat}): 0x015A with "
                  f"p2 = 1 and money -{permits.HANGAR_SALE_PRICE['oc']}, then the pass "
                  f"{_want} mint, then message 1; debit + pass banked, ids "
                  f"{[hex(i) for i in _ids(_o1)]}: {'OK' if _h1 else 'FAIL'}")
            ok &= bool(_h1)

            # the live 2026-10-02 call: row 0 (Control District), p2 = 769
            # stack garbage -> the nation's HQ pass at the HQ price
            _hq = {"id": 24, "first": "Con", "last": "Trol", "money": 5000}
            _rq = bytearray(scriptcall.S159_BODY_LEN)
            struct.pack_into("<II", _rq, scriptcall.S159_PARAMS, 0, 769)
            struct.pack_into("<I", _rq, 0, permits.HANGAR_SALE_EVENT)
            _sq = session.Session("selftest-hangar-permit")
            _sq._roster, _sq.playing_char, _sq.commit = [_hq], (lambda: _hq), (lambda what: None)
            _o4 = _sq.on_packet(packet.parse(packet.build(scriptcall.MSG_0159_REQ, bytes(_rq), 0x206)))
            _q2 = (struct.unpack_from("<I", _o4[0], H + scriptcall.S159_PARAMS + 4)[0]
                   if _o4 and len(_o4[0]) > H + scriptcall.S159_PARAMS + 8 else None)
            _h4 = (_ids(_o4) == [resultpush.MSG_RESULT_PUSH, shop.MSG_ACQUIRE_REPLY,
                                 handshake.MSG_SESSION_START] and _q2 == 1
                   and _hq["money"] == 5000 - permits.HANGAR_SALE_PRICE["hq"]
                   and [it["id"] for it in inventory.stored_items(_hq)] == [25])
            print(f"  hangar permit, the live record (row 0, p2 = 769 garbage): HQ pass 25 "
                  f"for {permits.HANGAR_SALE_PRICE['hq']} H$, p2 answered 1: "
                  f"{'OK' if _h4 else 'FAIL'}")
            ok &= _h4

            _poor = {"id": 22, "first": "No", "last": "Cash",
                     "money": permits.HANGAR_SALE_PRICE["hq"] - 1}
            _o2 = _buy(_poor, 0)
            _odd = {"id": 23, "first": "Odd", "last": "Row", "money": 5000}
            _o3 = _buy(_odd, 2)
            _h2 = (_ids(_o2) == [handshake.MSG_SESSION_START]
                   and _poor["money"] == permits.HANGAR_SALE_PRICE["hq"] - 1
                   and not inventory.stored_items(_poor)
                   and _ids(_o3) == [handshake.MSG_SESSION_START] and _odd["money"] == 5000
                   and not inventory.stored_items(_odd))
            print(f"  hangar permit refusals: short of the price, or a choice that is not "
                  f"a pass row -> the plain ack only (p2 stays 0 = 'not enough money'), "
                  f"nothing charged or granted: {'OK' if _h2 else 'FAIL'}")
            ok &= _h2
        finally:
            flat_globals()["CHAR_STORE"] = _was[0]
            status.STATUS_NATION, zoneentry.NATION_PER_CHARACTER = _was[1], _was[2]
    return ok


def solo_pins():
    """THE SOLO AREA (solo.py, SE topics/060308), no client. Returns ok.

    Pins: (1) only (109, 74149) = O.C.U. 統制区10 セクター14 is solo, and
    that pair is battle map 36 in SE's table; (2) a whole battle: never more
    than 2 enemies alive, 10 sent in all, the 10th stronger (HP byte and a
    higher loadout), the 10th kill WINS; (3) allies in SE's order Assault ->
    Mechanic (repair backpack) -> Missiler (a missile), the 3rd loss LOSES;
    (4) an ally POP is the AI switch on the PILOT's side byte; (5) the end
    trigger takes the solo verdict whatever FMO_BATTLE_END lists; (6) an
    ally's hit is the owner's."""
    import random as _rn
    from . import solo as _so
    ok = True
    rows = [
        {"kind": "wanzer", "level": 15, "nation": 0, "role": "npc60", "name": "npc60-2",
         "parts": [(0, 0x11, 197), (4, 0x12, 98), (10, 0x41, 1)]},
        {"kind": "wanzer", "level": 25, "nation": 0, "role": "npc60", "name": "npc60-3",
         "parts": [(0, 0x11, 198), (4, 0x12, 99), (10, 0x41, 2)]},
        {"kind": "wanzer", "level": 15, "nation": 1, "role": "COMS", "name": "COMS15-OCU",
         "parts": [(0, 0x11, 300), (4, 0x12, 98), (7, 0x72, 82), (10, 0x41, 1)]},
    ]
    # (1) the area
    _a1 = (_so.solo_for(109, 74149) is not None and _so.solo_for(109, 74148) is None
           and _so.solo_for(110, 74149) is None and _so.solo_for(None, None) is None
           and fmosectors.battle_map_for(109, 74149) == (9, 36))
    print(f"  solo area: only selector 109 tile 74149 (O.C.U. control zone 10 sector 14, "
          f"battle map 36) is solo: {'OK' if _a1 else 'FAIL'}")
    ok &= _a1
    # (2) + (3) a whole battle on a squad shaped as battle_squad_for makes it
    rnd = _rn.Random(7)

    def _squad():
        sq = {"owner": "acct-solo", "ids": [0x2222, 0x2223],
              "pos": [(94.0, 5.0, 44.0, 0.0), (94.0, 5.0, 84.0, 0.0)],
              "dead": set(), "last_hit": {}, "nation": 2, "parts": None, "level": 15,
              "loadouts": [rows[0], rows[0]]}
        _so.solo_squad_setup(sq, _so.solo_for(109, 74149), (64.0, 5.0, 64.0, 0.0),
                             rows=rows, rnd=rnd)
        return sq
    sq = _squad()
    _max_alive, _ended_at = 0, None
    for _n in range(1, 20):
        alive = [u for u in sq["ids"] if u not in sq["dead"]]
        _max_alive = max(_max_alive, len(alive))
        if not alive or sq["solo"]["end"]:
            break
        sq["dead"].add(alive[0])                  # squad_credit_kill's mark
        _so.on_death(sq, alive[0], rows=rows, rnd=rnd)
        if sq["solo"]["end"] and _ended_at is None:
            _ended_at = _n
    _last = sq["solo"].get("last")
    _pop_last = squad.enemy_pop(sq, _last, sq["ids"][_last], sq["pos"][_last], 0x1001, 0, 0, 101)
    _pop_first = squad.enemy_pop(sq, 0, sq["ids"][0], sq["pos"][0], 0x1001, 0, 0, 101)
    _a2 = (_max_alive == 2 and len(sq["ids"]) == 10 and _last == 9 and _ended_at == 10
           and sq["solo"]["end"][1] is True
           and _pop_last[fmoworld.REC_HDR + fmoworld.POP_HP_SCALE]
           == squad.enemy_hp_scale(_so.SOLO_LAST_HP_PCT)
           and _pop_first[fmoworld.REC_HDR + fmoworld.POP_HP_SCALE] == squad.enemy_hp_scale()
           and sq["loadouts"][9]["level"] > sq["loadouts"][8]["level"])
    print(f"  solo area: 2 enemies on the field, 10 sent, the 10th stronger (HP byte "
          f"{squad.enemy_hp_scale(_so.SOLO_LAST_HP_PCT)}, loadout "
          f"{(sq['loadouts'][9] or {}).get('name')}), the 10th kill wins (at death "
          f"{_ended_at}): {'OK' if _a2 else 'FAIL'}")
    ok &= _a2
    sq = _squad()
    _roles, _ends = [], []
    for _k in range(4):
        a = [u for u in sq["allies"] if u not in sq["ally_dead"]]
        if not a:
            break
        info = sq["ally_info"][a[0]]
        _roles.append((info["role"], info["loadout"]))
        _so.on_death(sq, a[0], rows=rows, rnd=rnd)
        _ends.append(sq["solo"]["end"])
    _mech = dict((p[0], p) for p in _roles[1][1]["parts"]) if len(_roles) > 1 else {}
    _a3 = ([r for r, _l in _roles] == ["Assault", "Mechanic", "Missiler"]
           and not any(k == 0x72 for _i, k, _d in _roles[0][1]["parts"])
           and _mech.get(10, (0, 0, 0))[1:] == (0x41, 184)          # bp_repairx1_4, Lv 14
           and any(k == 0x72 for _i, k, _d in _roles[2][1]["parts"])
           and _ends[:2] == [None, None] and _ends[2] and _ends[2][1] is False)
    print(f"  solo area: allies Assault -> Mechanic (backpack {_mech.get(10)}) -> "
          f"Missiler, the 3rd ally lost loses: {'OK' if _a3 else 'FAIL'}")
    ok &= _a3
    # (4) the ally POP
    sq = _squad()
    _au = sq["allies"][0]
    _ap = _so.ally_pop(sq, _au, 0x1001, 1, 0, 101)
    _b = _ap[fmoworld.REC_HDR:]
    _a4 = (_b[fmoworld.POP_SIDE] == 1 and _b[fmoworld.POP_NATION] == 1
           and struct.unpack_from("<I", _b, fmoworld.POP_CLIENT_KIND)[0] == 1
           and struct.unpack_from("<I", _b, battlepop.POP_AI_OWNER)[0] == 0x1001
           and struct.unpack_from("<I", _b, battlepop.POP_AI_BRAIN)[0] == 101
           and _b[fmoworld.POP_HP_SCALE] == 10)
    print(f"  solo area: the ally POP is client_kind 1, owner 0x1001, brain 101, side 1 = "
          f"the pilot's (unit+0x80, skipped by the AI target scan 0x610A4C89): "
          f"{'OK' if _a4 else 'FAIL'}")
    ok &= _a4
    # (5) the end trigger
    sq["solo"]["end"] = ("solo", True)
    _st = {"granted_at": 1000.0, "ended": False, "squad": sq, "escaped": None}
    _t = referee.battle_end_trigger(_st, {"escape"}, 1001.0, 0)
    sq["solo"]["end"] = None
    _t0 = referee.battle_end_trigger(_st, {"escape"}, 1001.0, 0)
    _a5 = _t == ("solo", True) and _t0 is None
    print(f"  solo area: the end trigger takes the solo verdict under "
          f"FMO_BATTLE_END=escape: {'OK' if _a5 else 'FAIL'}")
    ok &= _a5
    # (6) an ally's hit is the owner's kill

    class _Ch:
        addr, account, key, alias_of = ("203.0.113.40", 5), "acct-solo", b"k-battle", {}

        def self_unit(self):
            return 0x1001
    _was = referee.BATTLE_STATE.get("acct-solo")
    try:
        referee.battle_state("acct-solo", reset=True)["squad"] = sq
        _hl = struct.pack("<BBBB", 1, 1, 0, 0) + struct.pack("<IHBB", sq["ids"][0], 50, 0, 1)
        _nh = squad.squad_note_hits(_Ch(), _hl, _au)
        _a6 = _nh == [(sq["ids"][0], ("host", "acct-solo"))]
    finally:
        referee.BATTLE_STATE.pop("acct-solo", None)
        if _was is not None:
            referee.BATTLE_STATE["acct-solo"] = _was
    print(f"  solo area: an ally's hit is the owner's kill: {'OK' if _a6 else 'FAIL ' + repr(_nh)}")
    ok &= _a6
    return ok


def _spoils_order_pins():
    """SPOILS (loot.py) and ORDERED MISSIONS (missionbook's order part). Every
    pin prints one line and returns into `ok`; the wire pins name the client
    address they were read from."""
    import random
    ok = True
    # --- spoils: the cmd 214 body (0x611E4C90: count +0x00, 32 words +0x08,
    # the watcher 0x611A01A7 splits id = low u16, kind = byte 2)
    _b = loot.loot_open_body([(0x12, 97), (0x41, 1)])
    _w_ok = (len(_b) == 0x88 and struct.unpack_from("<I", _b, 0)[0] == 2
             and struct.unpack_from("<I", _b, 8)[0] == 0x00120061
             and struct.unpack_from("<I", _b, 12)[0] == 0x00410001
             and _b[16:] == bytes(0x88 - 16))
    print(f"  spoils: cmd 214 = count +0x00, words +0x08 (kind << 16 | id), 0x88 B: "
          f"{'OK' if _w_ok else 'FAIL'}")
    ok &= _w_ok
    # --- the choices (0x611E5020/5050/5090/50F0/5120): one Need per set
    _st = [loot.WANT] * 3
    loot.apply_choice(_st, loot.CMD_LOOT_WANT, struct.pack("<II", 0, 0))
    loot.apply_choice(_st, loot.CMD_LOOT_WANT, struct.pack("<II", 2, 0))
    _c1 = list(_st)
    loot.apply_choice(_st, loot.CMD_LOOT_PASS, struct.pack("<II", 1, 0))
    _c2 = list(_st)
    loot.apply_choice(_st, loot.CMD_LOOT_PASS, struct.pack("<II", loot.PASS_ALL, 0))
    _c3 = list(_st)
    loot.apply_choice(_st, loot.CMD_LOOT_PASS, struct.pack("<II", loot.WANT_ALL, 0))
    _ch_ok = (_c1 == [loot.WANT, loot.WANT, loot.NEED]
              and _c2 == [loot.WANT, loot.PASS, loot.NEED]
              and _c3 == [loot.PASS] * 3 and _st == [loot.WANT] * 3)
    print(f"  spoils: 0xD8 {{i,0}} Need moves the one Need, 0xD9 {{i,0}} Pass, "
          f"{{0x20}} pass all, {{0x21}} want all: {'OK' if _ch_ok else 'FAIL'}")
    ok &= _ch_ok
    # --- SE's roll: Need beats Want, Pass gets nothing
    _rv = loot.resolve([(1, 1), (1, 2), (1, 3)],
                       {"a": [loot.WANT, loot.PASS, loot.PASS],
                        "b": [loot.NEED, loot.PASS, loot.WANT]}, random.Random(3))
    _rv_ok = _rv == {0: "b", 1: None, 2: "b"}
    print(f"  spoils: Need beats Want, all-Pass drops to nobody: "
          f"{'OK' if _rv_ok else 'FAIL'} {_rv}")
    ok &= _rv_ok
    # --- the drop pool never holds an NPC-only frame
    _parts = {(0x11, 196): (10, "npc60-1"), (0x12, 97): (10, "Machinegun"),
              (0x11, 6): (15, "Type 65"), (0x11, 3): (30, "Quint")}
    _en, _nr = loot.drop_pool(15, parts=_parts, npc_names={"npc60-1"}, band=10,
                              loadouts=[{"parts": [(0, 0x11, 196), (4, 0x12, 97)]}])
    _dp_ok = _en == [(0x12, 97)] and _nr == [(0x11, 6), (0x12, 97)]
    print(f"  spoils: pools = the defeated loadout's garage parts + the level band, "
          f"no NPC-only frame: {'OK' if _dp_ok else 'FAIL'} {_en} {_nr}")
    ok &= _dp_ok
    # --- one round end to end: 214 out on the group self stream, choices in,
    # OK closes (215 out), the winner's keepalive banks it and pushes 0x016B

    class _GC:
        def __init__(self, acct):
            self.key, self.account, self.seen = groupchannel.GROUP_KEY, acct, 0
            self.group_popped, self.pending, self.addr = True, [], ("192.0.2.9", 19155)

    class _S:
        def __init__(self, acct, char):
            self.account, self.peer, self.char, self.commits = acct, "selftest", char, []
            self.battle_settlement = None

        def playing_char(self):
            return self.char

        def commit(self, what):
            self.commits.append(what)

    _was_peers = dict(groupchannel.WORLD_PEERS)
    try:
        loot.ROUNDS.clear()
        loot.PENDING_GRANTS.clear()
        _ga, _gb = _GC("sp:a"), _GC("sp:b")
        groupchannel.WORLD_PEERS[("192.0.2.9", 1, "group")] = _ga
        groupchannel.WORLD_PEERS[("192.0.2.9", 2, "group")] = _gb
        _rec = {"n": 1, "at": 1000.0, "joined": ["sp:a", "sp:b"]}
        _rd = loot.open_round(77, _rec, [(0x12, 97), (0x41, 1)], now=1000.0)
        _again = loot.open_round(77, _rec, [(0x11, 6)], now=1001.0)
        for _c in (_ga, _gb):
            _c.seen += 1
            loot.loot_tick(_c, {"records": [], "from": 0}, now=1002.0)
        _o_ok = all(len(c.pending) == 1
                    and struct.unpack_from("<I", c.pending[0], 4)[0] == loot.CMD_LOOT_OPEN
                    for c in (_ga, _gb)) and _again is _rd
        _ga.seen += 1
        loot.loot_tick(_ga, {"from": 0, "records": [
            (0, 8, loot.CMD_LOOT_WANT, struct.pack("<II", 1, 0)),
            (0, 8, loot.CMD_LOOT_PASS, struct.pack("<II", 0, 0)),
            (0, 4, loot.CMD_LOOT_OK, bytes(4))]}, now=1003.0)
        _ga.seen += 1                         # the same range again: a resend
        loot.loot_tick(_ga, {"from": 0, "records": [
            (0, 8, loot.CMD_LOOT_WANT, struct.pack("<II", 0, 0))]}, now=1003.5)
        _open_mid = not _rd["closed"]
        _gb.seen += 1
        loot.loot_tick(_gb, {"from": 0, "records": [
            (0, 8, loot.CMD_LOOT_PASS, struct.pack("<II", 1, 0)),
            (0, 4, loot.CMD_LOOT_OK, bytes(4))]}, now=1004.0)
        _cl_ok = (_open_mid and _rd["closed"] and _rd["winners"] == {0: "sp:b", 1: "sp:a"}
                  and all(struct.unpack_from("<I", c.pending[-1], 4)[0] == loot.CMD_LOOT_CLOSE
                          for c in (_ga, _gb)))
        _pc = {"id": 1, "first": "A", "last": "B"}
        _out = loot.loot_pushes_due(_S("sp:a", _pc), 5, now=1005.0)
        _pk = packet.parse(_out[0]) if _out else None
        _g_ok = (len(_out) == 1 and _pk is not None
                 and _pk["msg"] == shop.MSG_ACQUIRE_REPLY
                 and struct.unpack_from("<I", _pk["payload"], shop.MINT_ADDS)[0] == 1
                 and [(it["kind"], it["id"]) for it in inventory.stored_items(_pc)]
                 == [(0x41, 1)] and not loot.PENDING_GRANTS.get("sp:a"))
        # the timer: a round nobody answers closes itself, everyone Wants
        _rd2 = loot.open_round(78, {"n": 1, "at": 1.0, "joined": ["sp:a"]},
                               [(0x11, 6)], now=2000.0)
        loot.close_due(now=2000.0 + loot.LOOT_WINDOW_S)
        _mid2 = _rd2["closed"]
        loot.close_due(now=2000.0 + loot.LOOT_WINDOW_S + loot.LOOT_GRACE)
        _t_ok = not _mid2 and _rd2["winners"] == {0: "sp:a"}
    finally:
        groupchannel.WORLD_PEERS.clear()
        groupchannel.WORLD_PEERS.update(_was_peers)
        loot.ROUNDS.clear()
        loot.PENDING_GRANTS.clear()
    print(f"  spoils round: one cmd 214 per member (once per battle): "
          f"{'OK' if _o_ok else 'FAIL'}; a resent choice is not re-applied, "
          f"every OK closes it with cmd 215 and Need wins: {'OK' if _cl_ok else 'FAIL'}; "
          f"the winner's keepalive banks the item and sends one 0x016B mint: "
          f"{'OK' if _g_ok else 'FAIL'}; the 120 s timer closes it (+grace): "
          f"{'OK' if _t_ok else 'FAIL'}")
    ok &= _o_ok and _cl_ok and _g_ok and _t_ok
    # --- the trigger: settlement opens the group's round on a WIN only
    loot.ROUNDS.clear()
    _bg_was = dict(battlegroups.GROUP_BATTLE)
    try:
        battlegroups.GROUP_BATTLE.clear()
        battlegroups.GROUP_BATTLE[91] = {"n": 2, "at": time.time(), "joined": ["sp:w", "sp:x"]}

        class _W:
            account, ip, peer = "sp:w", "192.0.2.10", "selftest"

            def battle_key(self):
                return self.account
        _won = settlement.SessionSettlement.loot_battle_settle(_W(), True, rnd=random.Random(4))
        _lost = settlement.SessionSettlement.loot_battle_settle(_W(), False)
        _solo = _W()
        _solo.account = "sp:solo"
        _none = settlement.SessionSettlement.loot_battle_settle(_solo, True)
        _tr_ok = (_won is not None and _won["members"] == ["sp:w", "sp:x"]
                  and len(_won["items"]) == loot.LOOT_DROPS and _lost is None and _none is None)
    finally:
        battlegroups.GROUP_BATTLE.clear()
        battlegroups.GROUP_BATTLE.update(_bg_was)
        loot.ROUNDS.clear()
    print(f"  spoils trigger: a group WIN opens one round with FMO_LOOT_DROPS "
          f"({loot.LOOT_DROPS}) drops for the battle's members; a loss or a solo "
          f"win opens none: {'OK' if _tr_ok else 'FAIL'}")
    ok &= _tr_ok

    # --- ORDERS: the 524-B template (the dialog's reads, 0x611CA301..)
    _tr = fmomsn.order_template_record(0x7E010007, "Hold", 1, 1800, 10, 500, 20)
    _t_ok = (len(_tr) == 0x20C and struct.unpack_from("<I", _tr, 0)[0] == 0x7E010007
             and struct.unpack_from("<I", _tr, 0x1E4)[0] == 1800
             and struct.unpack_from("<I", _tr, 0x1F4)[0] == 10
             and struct.unpack_from("<I", _tr, 0x1F8)[0] == 500
             and struct.unpack_from("<I", _tr, 0x200)[0] == 20 and _tr[4:9] == b"Hold\0")
    print(f"  order template: 524 B, id +0x00, name +0x04, op time +0x1E4, reward "
          f"MP +0x1F4 / H$ +0x1F8, base Order MP +0x200: {'OK' if _t_ok else 'FAIL'}")
    ok &= _t_ok
    _reqs = {7: {"name": "Hold Sector", "reward_mp": 40, "reward_hs": 2000, "cat": 2}}
    _mv = community.msn_reply("selftest", fmomsn.OP_ORDER_TEMPLATES,
                              struct.pack("<III", 0, 1, 0x7EFF0000))
    _tpl = missionbook.order_template_records([missionbook.order_template_id(1, 7), 5], _reqs)
    _k5_ok = ([op for op, _f in _mv] == [fmomsn.OP_END]
              and len(_tpl) == 1 and len(_tpl[0]) == fmomsn.TEMPLATE_LEN
              and struct.unpack_from("<I", _tpl[0], fmomsn.TPL_BASE_MP)[0]
              == missionboard.ORDER_BASE_MP
              and struct.unpack_from("<I", _tpl[0], fmomsn.TPL_REWARD_MP)[0]
              == 40 * missionboard.ORDER_REWARD_PCT // 100)
    print(f"  order templates: kind 5 (op 0xE) answers op 0x21 pages then 0x1B END "
          f"(an unknown id: END alone); a known source row -> one 524-B template: "
          f"{'OK' if _k5_ok else 'FAIL'}")
    ok &= _k5_ok
    _reg_was = dict(missionbook.ORDERS)
    try:
        missionbook.ORDERS.clear()
        missionbook._orders_loaded.append(True)
        _pc = {"id": 5, "first": "A", "last": "B", "mp": 100,
               "missions": [{"id": 7, "key": 7 + (1 << 16), "name": "Hold Sector",
                             "cat": 2, "at": missionbook._mission_iso(), "sector": 1234}]}
        # a BATTLE-MAP taker has nothing to derive (guide/mission 10, 15)
        _bare = {"id": 6, "first": "C", "last": "D", "mp": 100,
                 "missions": [{"id": 7, "key": 7 + (1 << 16), "name": "Hold Sector",
                               "cat": 1, "at": missionbook._mission_iso()}]}
        _no = missionbook.order_create(_bare, "sp:c", {0x004: 7 + (1 << 16)},
                                       reqs=_reqs, rosters=[])
        _poor = dict(_pc, mp=5, missions=list(_pc["missions"]))
        _np = missionbook.order_create(_poor, "sp:p", {0x004: 7 + (1 << 16), 0x140: 20,
                                                       0x144: 100}, reqs=_reqs, rosters=[])
        _e, _code, _why = missionbook.order_create(
            _pc, "sp:o", {0x004: 7 + (1 << 16), 0x140: 20, 0x144: 100},
            issuer="A.B", reqs=_reqs, rosters=[])
        _rows = missionbook.accepted_list_rows(missionbook.accepted_missions(_pc))
        _src_row = next((r for r in _rows if r[0] in (7, 7 + (1 << 16))), (0, "", {}))
        _der_row = next((r for r in _rows if r[0] == _e["derived"]), (0, "", {}))
        _cr_ok = (_no[0] is None and _poor["mp"] == 5 and _np[1] == missionboard.ORDER_CODE_MP
                  and _e is not None and _pc["mp"] == 80 and _e["cat"] == 1
                  and _e["reward_mp"] == 10 and _e["reward_hs"] == 500
                  and _rows[0][0] in (7, 7 + (1 << 16))
                  and _src_row[2].get(missionboard.ROW_ORDER_TPL) == missionbook.order_template_id(1, 7)
                  and _der_row[2].get(missionboard.MR_STATE) == 1
                  and _der_row[2].get(missionboard.ROW_COMMANDER) == "A.B"
                  and _e["derived"] in missionbook.order_requirements())
        print(f"  order: only a sector/area taker may order, MP short -> -7, the "
              f"Order MP is taken, row 0 carries the template id (+0x08) and the "
              f"derived row reads 1 'Ordered' (+0x118): {'OK' if _cr_ok else 'FAIL'}")
        ok &= _cr_ok
        _m, _was, _ref = missionbook.order_cancel_apply(_pc, _e["derived"])
        _m2, _was2, _ref2 = missionbook.order_cancel_apply(_pc, _e["derived"])
        _cn_ok = (_was == "ordered" and _ref == 20 and _pc["mp"] == 100
                  and _was2 == "cancelled" and _ref2 == 0 and _pc["mp"] == 100
                  and _e["derived"] not in missionbook.order_requirements())
        # taken: no refund; expired untaken: refunded
        _e2 = missionbook.order_create(_pc, "sp:o", {0x004: 7 + (1 << 16), 0x140: 20,
                                                     0x144: 100}, reqs=_reqs, rosters=[])[0]
        missionbook.ORDER_TAKEN[_e2["derived"]] = "sp:t"
        _m3, _was3, _ref3 = missionbook.order_cancel_apply(_pc, _e2["derived"])
        _e3 = missionbook.order_create(_pc, "sp:o", {0x004: 7 + (1 << 16), 0x140: 20,
                                                     0x144: 100}, reqs=_reqs, rosters=[])[0]
        _mp_before = _pc["mp"]
        _ex = missionbook.order_expire_apply(_pc, now=time.time() + _e3["limit"] + 60)
        _tk_ok = (_was3 == "taken" and _ref3 == 0 and _ex == [(_e3, 20)]
                  and _pc["mp"] == _mp_before + 20)
        print(f"  order cancel: refunds the Order MP once, only while Ordered: "
              f"{'OK' if _cn_ok else 'FAIL'}; taken -> no refund, expired untaken "
              f"-> refunded: {'OK' if _tk_ok else 'FAIL'}")
        ok &= _cn_ok and _tk_ok
        _self = missionbook.mission_accept_verdict(
            {"name": "x", "rank": 0, "fee": 0, "reward_mp": 0, "reward_hs": 0,
             "issuer": ("sp:o", 5, "A.B")}, _pc)
        _sf_ok = _self[0] == -5
        print(f"  order: its issuer's own accept is refused -5 (27:5): "
              f"{'OK' if _sf_ok else 'FAIL'}")
        ok &= _sf_ok
    finally:
        for _d in list(missionbook.ORDERS):
            missionbook.ORDER_TAKEN.pop(_d, None)
        missionbook.ORDERS.clear()
        missionbook.ORDERS.update(_reg_was)
    return ok


def _manual_missions_pins():
    """THE PLAYING MANUAL'S MISSION RULES (2026-09-30; Playing Manual pp.45,
    59-60): the release rows' rank / Fee / reward order, contribution on the
    report (sector x2, SE 050628:92), -3 on a second accept of a taken order,
    the Strategy Room / mission-counter places, sector missions met only by
    their own orders' wins, and no contribution in an Arena. Every pin prints
    one line and returns into `ok`."""
    from . import defaults, settlement as _st
    ok = True
    _iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))
    _T0 = 1_800_000_000

    # --- 1. the release rows rise Battle Map < Sector < Area (p.60, guide 20)
    _rd = defaults.RELEASE_DEFAULTS
    _sv_f = community.MSN_FIELDS
    try:
        community.MSN_FIELDS = _rd["FMO_MSN_FIELDS"]
        _fl = community._msn_fields()
    finally:
        community.MSN_FIELDS = _sv_f
    _by = {1: [], 2: [], 3: []}
    for _i, _spec in enumerate(r for r in _rd["FMO_MSN_ROWS"].split("|") if r):
        _mid, _cat, _nm = community.msn_row_spec(_spec, _i)
        _f = _fl.get(_i, {})
        _by[_cat].append(tuple(_f.get(o, 0) for o in (
            fmomsn.MISSION_RANK, fmomsn.MISSION_FEE, fmomsn.MISSION_REWARD_MP,
            fmomsn.MISSION_REWARD_HS)))
    _rows_ok = all(_by[c] for c in (1, 2, 3)) and all(
        max(r[k] for r in _by[1]) < min(r[k] for r in _by[2])
        and max(r[k] for r in _by[2]) < min(r[k] for r in _by[3]) for k in range(4))
    # the Area row needs Major (21), the client's own Strategy Room gate, and
    # lives in zone 509 (Frontline area 10)
    _rows_ok = (_rows_ok and min(r[0] for r in _by[3]) >= 21
                and "2:509" in _rd["FMO_MSN_ZONES"].split(","))
    print(f"  manual p.60: release rows rise Battle Map < Sector < Area in rank, Fee, "
          f"reward MP and H$; Area = Major+ in zone 509: {'OK' if _rows_ok else 'FAIL'}")
    ok &= _rows_ok

    # --- 2. contribution on the report, sector x2 (SE 050628:92)
    _cv = [missionbook.mission_contribution(c, "1:100,2:150,3:600", 2) for c in (1, 2, 3)]
    _cn_ok = (_cv == [100, 300, 600]
              and missionbook.mission_contribution(2, "", 2) == 0
              and missionbook.mission_contribution(None, "1:100", 2) == 0)
    _sv_c = missionboard.MISSION_CONTRIB
    try:
        missionboard.MISSION_CONTRIB = "1:100,2:150,3:600"
        _ch = {"contribution": 1000, "missions": [
            {"id": 11, "name": "S", "cat": 2, "at": _iso(_T0), "status": "met",
             "reward_hs": 6000, "reward_mp": 80}]}
        _m1, _w1, _p1 = missionbook.mission_report_apply(_ch, 11, now=_T0 + 10, limit=1800)
        _m2, _w2, _p2 = missionbook.mission_report_apply(_ch, 11, now=_T0 + 20, limit=1800)
        # the accept's own snapshot wins over the knob
        _ch2 = {"contribution": 0, "missions": [
            {"id": 7, "name": "B", "cat": 1, "at": _iso(_T0), "status": "met",
             "reward_hs": 0, "reward_mp": 0, "reward_contrib": 42}]}
        _p3 = missionbook.mission_report_apply(_ch2, 7, now=_T0 + 10, limit=1800)[2]
        _cn_ok = (_cn_ok and _w1 == "met" and _p1["contrib"] == 300
                  and _ch["contribution"] == 1300 and _p2 is None
                  and "contrib" not in _ch["mission_pay"][0]
                  and _p3["contrib"] == 42 and _ch2["contribution"] == 42
                  and not _ch2.get("mission_pay"))
    finally:
        missionboard.MISSION_CONTRIB = _sv_c
    print(f"  manual p.60: a met mission's report banks its contribution once "
          f"(100 / 150x2 / 600 by category, the accept's snapshot first), the "
          f"paybook line stays H$/MP: {'OK' if _cn_ok else 'FAIL'}")
    ok &= _cn_ok

    # --- 3. a taken order cannot be accepted again: -3 (27:6), -4 for its taker
    _sv_reg = (dict(missionbook.ORDERS), dict(missionbook.ORDER_TAKEN),
               dict(missionbook.ORDER_TAKER_NAME), dict(missionbook.ORDER_WON),
               list(missionbook._orders_loaded))
    _now = time.time()
    _e_taken = {"id": 0xFE01, "key": 0xFE01, "derived": 0xFE01, "name": "Hold",
                "status": "ordered", "cat": 1, "at": _iso(_now), "limit": 1800}
    _e_open = dict(_e_taken, id=0xFE02, key=0xFE02, derived=0xFE02)
    _e_gone = dict(_e_taken, id=0xFE03, key=0xFE03, derived=0xFE03, status="cancelled")
    _ros = [("mm:issuer", [{"id": 1, "missions": [_e_taken, _e_open, _e_gone]}]),
            ("mm:taker", [{"id": 2, "first": "T", "last": "K",
                           "missions": [{"id": 0xFE01, "name": "Hold", "cat": 1,
                                         "at": _iso(_now)}]}])]
    try:
        _r1 = missionbook.order_accept_refusal(0xFE01, "mm:other", rosters=_ros)
        _r2 = missionbook.order_accept_refusal(0xFE01, "mm:taker", rosters=_ros)
        _r3 = missionbook.order_accept_refusal(0xFE02, "mm:other", rosters=_ros)
        _r4 = missionbook.order_accept_refusal(0xFE03, "mm:other", rosters=_ros)
        _r5 = missionbook.order_accept_refusal(7, "mm:other", rosters=_ros)
        _dbl_ok = ((_r1 or (None,))[0] == -3 and (_r2 or (None,))[0] == -4
                   and _r3 is None and (_r4 or (None,))[0] == 0 and _r5 is None
                   and "27:6" in missionboard.mission_refusal_text(-3))
        # through the wire: a 0x018A for the taken order is refused 0xFFFD
        _sv_acc = missionboard.MISSION_ACCEPT
        try:
            missionboard.MISSION_ACCEPT = "gate"
            _gs = session.Session("mmdbl:0")
            _out = _gs.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 0xFE01) + bytes(120),
                seq=packet.SEQ_MIN, conn_id=1)))
            _dbl_ok = (_dbl_ok and len(_out) == 1
                       and packet.parse(_out[0])["msg"] == charselect.MSG_FAIL
                       and packet.parse(_out[0])["conn"] == (-3 & 0xFFFF))
        finally:
            missionboard.MISSION_ACCEPT = _sv_acc
    except Exception as _x:
        print(f"    (raised {_x!r})")
        _dbl_ok = False
    print(f"  manual p.60: a derived order already taken -> -3 (27:6 'already "
          f"accepted'), its own taker -> -4, cancelled -> 0; the 0x018A wire "
          f"answers 0xFFFD: {'OK' if _dbl_ok else 'FAIL'}")
    ok &= _dbl_ok

    # --- 5. a sector mission is met only by wins in its OWN orders
    _sk = 11 + (1 << 16)
    _sm = {"id": 11, "key": _sk, "name": "Sector Sweep", "cat": 2, "needed": 1,
           "at": _iso(_T0), "zone": 200, "sector": 71122, "nation": 1}
    _ord = {"id": 0xFE04, "key": 0xFE04, "derived": 0xFE04, "name": "Sector Sweep",
            "status": "ordered", "cat": 1, "at": _iso(_T0 + 10), "limit": 1800,
            "from_key": _sk, "from_at": _iso(_T0)}
    # another pilot's sector accept under the SAME key, accepted at another time
    _ord_x = dict(_ord, id=0xFE05, key=0xFE05, derived=0xFE05, from_at=_iso(_T0 - 50))
    _tk = {"id": 0xFE04, "name": "Sector Sweep", "cat": 1, "at": _iso(_T0 + 20)}
    _tx = {"id": 0xFE05, "name": "Sector Sweep", "cat": 1, "at": _iso(_T0 + 20),
           "status": "met", "settled": _iso(_T0 + 40)}
    _ros5 = [("mm:s", [{"id": 3, "missions": [_sm, _ord, _ord_x]}]),
             ("mm:t", [{"id": 4, "missions": [_tk]}]),
             ("mm:u", [{"id": 5, "missions": [_tx]}])]
    _led = {(200, 71122, 1): [_T0 + 30]}        # a nation win on the tile
    _sv_own = missionboard.SECTOR_OWN_WINS
    try:
        missionboard.SECTOR_OWN_WINS = True
        _s_open = missionbook.mission_status(_sm, now=_T0 + 100, limit=1800,
                                             ledger=_led, rosters=_ros5)
        _tk.update(status="met", settled=_iso(_T0 + 60))
        _s_met = missionbook.mission_status(_sm, now=_T0 + 100, limit=1800,
                                            ledger=_led, rosters=_ros5)
        missionboard.SECTOR_OWN_WINS = False
        _s_old = missionbook.mission_status(dict(_sm), now=_T0 + 100, limit=1800,
                                            ledger=_led, rosters=[])
        _own_ok = _s_open == "open" and _s_met == "met" and _s_old == "met"
    except Exception as _x:
        print(f"    (raised {_x!r})")
        _own_ok = False
    finally:
        missionboard.SECTOR_OWN_WINS = _sv_own
        missionbook.ORDERS.clear()
        missionbook.ORDERS.update(_sv_reg[0])
        missionbook.ORDER_TAKEN.clear()
        missionbook.ORDER_TAKEN.update(_sv_reg[1])
        missionbook.ORDER_TAKER_NAME.clear()
        missionbook.ORDER_TAKER_NAME.update(_sv_reg[2])
        missionbook.ORDER_WON.clear()
        missionbook.ORDER_WON.update(_sv_reg[3])
        missionbook._orders_loaded[:] = _sv_reg[4]
    print(f"  manual p.60: FMO_SECTOR_OWN_WINS -- a nation win on the tile and "
          f"another accept's order do not meet a sector mission, a win in its own "
          f"order does; knob 0 -> the old tile ledger: {'OK' if _own_ok else 'FAIL'}")
    ok &= _own_ok

    # --- 4. the places: Area only in the Briefing Room, Battle Map / Sector
    # not in a Controlled Zone or the Coliseum
    _pr = missionbook.mission_place_refusal
    _code = missionboard.MISSION_PLACE_CODE
    _pl_ok = (_pr(3, 509, (509, 0, 1), True)[0] == _code
              and _pr(3, 509, (509, 2, 1), True)[0] is None
              and _pr(3, 509, None, True)[0] is None
              and _pr(3, 509, (509, 0, 1), False)[0] is None
              and _pr(1, 100, None, True)[0] == _code
              and _pr(2, 300, None, True)[0] == _code
              and _pr(1, 600, None, True)[0] == _code
              and _pr(1, 200, None, True)[0] is None
              and _pr(2, 509, None, True)[0] is None
              and _pr(1, None, None, True)[0] is None)
    _sv_p = (missionboard.MISSION_PLACE, missionboard.MISSION_ACCEPT,
             list(community.MSN_ROWS), community.MSN_FIELDS)
    _host = "mmplace"
    _sv_w = (rooms.WORLD_ZONES.get(_host), move.WORLD_PLACES.get(_host))

    def _acc(mid):
        _o = session.Session(_host + ":0").on_packet(packet.parse(packet.build(
            missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", mid) + bytes(120),
            seq=packet.SEQ_MIN, conn_id=1)))
        _p = packet.parse(_o[0]) if len(_o) == 1 else {}
        return _p.get("msg"), _p.get("conn")
    _sv_places = move.PLACES
    try:
        move.PLACES = True
        missionboard.MISSION_PLACE = True
        missionboard.MISSION_ACCEPT = "gate"
        community.MSN_ROWS = ["13/3:Area", "7/1:Battle"]
        community.MSN_FIELDS = ""
        rooms.WORLD_ZONES[_host] = 509
        move.WORLD_PLACES[_host] = (509, 0, 1)
        _a_lobby = _acc(13)
        move.WORLD_PLACES[_host] = (509, 2, 1)
        _a_brief = _acc(13)
        rooms.WORLD_ZONES[_host] = 100
        _b_hq = _acc(7)
        rooms.WORLD_ZONES[_host] = 200
        _b_occ = _acc(7)
        _pl_ok = (_pl_ok and _a_lobby == (charselect.MSG_FAIL, _code & 0xFFFF)
                  and _a_brief[0] == missionboard.MSG_MISSION_ACCEPT_REPLY
                  and _b_hq == (charselect.MSG_FAIL, _code & 0xFFFF)
                  and _b_occ[0] == missionboard.MSG_MISSION_ACCEPT_REPLY)
    except Exception as _x:
        print(f"    (raised {_x!r})")
        _pl_ok = False
    finally:
        (missionboard.MISSION_PLACE, missionboard.MISSION_ACCEPT,
         community.MSN_ROWS, community.MSN_FIELDS) = _sv_p
        move.PLACES = _sv_places
        for _tbl, _v in ((rooms.WORLD_ZONES, _sv_w[0]), (move.WORLD_PLACES, _sv_w[1])):
            if _v is None:
                _tbl.pop(_host, None)
            else:
                _tbl[_host] = _v
    print(f"  manual p.59/60: FMO_MISSION_PLACE -- Area accepted only in the Briefing "
          f"Room, Battle Map / Sector refused in a Controlled Zone / Coliseum, code "
          f"{_code} (27:4); through the 0x018A wire: {'OK' if _pl_ok else 'FAIL'}")
    ok &= _pl_ok

    # --- 6. an Arena battle pays no contribution and settles no mission (p.45)
    _ar_ok = (_st.arena_zone(600) and _st.arena_zone(607) and not _st.arena_zone(608)
              and not _st.arena_zone(509) and not _st.arena_zone(None))
    _ahost = "mmarena"
    _sv_az = rooms.WORLD_ZONES.get(_ahost)
    _sv_rc, _sv_mr = resultpush.RESULT_CONTRIB, missionboard.MISSION_REPORT
    try:
        resultpush.RESULT_CONTRIB = 11
        missionboard.MISSION_REPORT = True
        rooms.WORLD_ZONES[_ahost] = 600
        _sa = session.Session(_ahost + ":0")
        _in = _sa.settle_battle("selftest arena", won=True)
        _ms = _sa.mission_battle_settle(True)
        rooms.WORLD_ZONES[_ahost] = 200
        _sb = session.Session(_ahost + ":1")
        _out = _sb.settle_battle("selftest field", won=True)
        _ar_ok = (_ar_ok and _st.ARENA_NO_PAY and _in["contribution"] == 0
                  and _ms is None and _out["contribution"] >= 11)
    except Exception as _x:
        print(f"    (raised {_x!r})")
        _ar_ok = False
    finally:
        resultpush.RESULT_CONTRIB, missionboard.MISSION_REPORT = _sv_rc, _sv_mr
        if _sv_az is None:
            rooms.WORLD_ZONES.pop(_ahost, None)
        else:
            rooms.WORLD_ZONES[_ahost] = _sv_az
    print(f"  manual p.45: a Coliseum battle (zones 600-607) pays no contribution and "
          f"settles no mission; a field battle still pays: {'OK' if _ar_ok else 'FAIL'}")
    ok &= _ar_ok
    return ok


def _manual_pilot_pins():
    """THE PLAYING MANUAL'S PILOT RULES (2026-09-30; pp.37, 38, 42, 44): the
    area tier the client makes of the Pilot level, the `levels` zone table,
    the Private First Class HQ pass granted once, the new-pilot seed and the
    head-count starting money, the 24-hour delete lock, reserved and taken
    names on create and on a defection rename (gender kept), the nation
    screen's live head counts and closed flag, and the resumed zone. Every
    pin prints one line and returns into `ok`."""
    import tempfile as _tf
    from . import defaults, defection as _dfx
    ok = True
    _u16 = lambda b, o: struct.unpack_from("<H", b, o)[0]
    _i32 = lambda b, o: struct.unpack_from("<i", b, o)[0]

    # --- 1. the tier is 0x611E4000's per-level byte, not the level (p.37)
    _tiers = [permits.area_tier(lv) for lv in (0, 1, 4, 5, 9, 10, 14, 15, 19, 20, 50, 51, 100)]
    _curve_was = classes.CLASS_CURVE
    _zc_was = zonecontrol.ZONE_CONTROL
    _spent = {}
    try:
        classes.CLASS_CURVE = tuple(i * 1000 for i in range(100))   # level L at (L-1)*1000
        zonecontrol.ZONE_CONTROL = ["407:1:1"]
        for _lv in (2, 7, 12):
            _pc = {"nation_byte": 2, "class_exp": {"12": (_lv - 1) * 1000},
                   "items": [{"serial": 0x55 + _lv, "id": 28, "kind": permits.PASS_KIND}]}
            _ts = session.Session("tier:0")
            _spent[_lv] = (_ts.spend_area_pass(_pc, 407), permits.pilot_area_tier(_pc))
    finally:
        classes.CLASS_CURVE = _curve_was
        zonecontrol.ZONE_CONTROL = _zc_was
    _tier_ok = (_tiers == [1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
                and _spent[2] == ([0x57], (1, 2))
                and _spent[7] == ([0x5C], (2, 7))        # the old code let level 3+ go free
                and _spent[12] == ([], (3, 12)))
    print(f"  manual p.37: area tier = 0x611E4000's step (1-4/5-9/10-14/15-19/20-50/51+), "
          f"and a Pilot level 7 pilot now pays the pass a level 12 one does not: "
          f"{'OK' if _tier_ok else 'FAIL ' + str((_tiers, _spent))}")
    ok &= _tier_ok

    # --- 2. the `levels` zone table: own HQ 1, own occupied 3, enemy 4, FZ 5,
    # Coliseum 2, only where D83 lets the nation in; `all` is unchanged
    _lr = {z: (o, u) for z, o, u in zonecontrol.level_rows()}
    _rows_l = zonecontrol.parse_zone_control(["levels"])
    _c = lambda z, n, lv: permits.area_permit_cost(z, n, permits.area_tier(lv), _rows_l)[0]
    _lvl_ok = (_lr[100] == (1, 0) and _lr[300] == (0, 1) and _lr[200] == (3, 0)
               and _lr[400] == (0, 3) and _lr[207] == (3, 4) and _lr[407] == (4, 3)
               and _lr[509] == (5, 5) and _lr[600] == (2, 2)
               and _rows_l == zonecontrol.level_rows()
               and zonecontrol.parse_zone_control(["all"]) == list(zonecontrol.ZONE_ROWS_D83)
               # own HQ: permit until 10, then free
               and _c(100, 1, 1) == 1 and _c(100, 1, 9) == 1 and _c(100, 1, 10) == 2
               # own occupied: shut below 10, permit 10-19, free from 20
               and _c(200, 1, 9) == 0 and _c(200, 1, 10) == 1 and _c(200, 1, 20) == 2
               # the enemy's occupied: shut below 15, free from 15
               and _c(407, 1, 14) == 0 and _c(407, 1, 15) == 2 and _c(207, 2, 15) == 2
               # Fierce Battle Zone: shut below 20
               and _c(509, 2, 19) == 0 and _c(509, 2, 20) == 2
               # Coliseum: no pass exists for kind 6, so level 10 is the door
               and _c(600, 1, 10) == 2 and _c(600, 1, 9) == 1
               and zonecontrol.zone_level_byte(509, 1, dict(zonecontrol.ZONE_LEVEL_DEFAULTS, fz=4)) == 4)
    print(f"  manual p.37: FMO_ZONE_CONTROL=levels serves HQ 1 / occupied 3 / enemy "
          f"occupied 4 / FZ 5 / Coliseum 2, opening at Pilot level 10 / 15 / 20 / 10; "
          f"`all` unchanged: {'OK' if _lvl_ok else 'FAIL ' + str(_lr)}")
    ok &= _lvl_ok

    # --- 3. ONE HQ pass at Private First Class (rank byte 4), never re-minted
    _rd = defaults.RELEASE_DEFAULTS
    _pp = {"items": [], "nation_byte": 1}
    _ps = session.Session("pfc:0")
    _ps.playing_char = lambda: _pp
    _ps.commit = lambda why: None
    _sv = (permits.PERMIT, permits.PERMIT_ALL, permits.PERMIT_RANKS, charstore.CHAR_STORE)
    try:
        permits.PERMIT, permits.PERMIT_ALL = False, False
        permits.PERMIT_RANKS = permits.parse_permit_ranks(_rd["FMO_PERMIT_RANKS"])
        flat_globals()["CHAR_STORE"] = "selftest-stub"
        _g3 = _ps.grant_hq_pass(1, rank=3)
        _g4 = _ps.grant_hq_pass(1, rank=4)
        _held = [it["id"] for it in inventory.stored_items(_pp)]
        for _it in inventory.stored_items(_pp):          # the client spent it
            inventory.remove_stored_item(_pp, _it["serial"])
        _g5 = _ps.grant_hq_pass(1, rank=6)
        _legacy = {"items": [{"serial": 9, "id": 25, "kind": permits.PASS_KIND}], "nation_byte": 1}
        _ps.playing_char = lambda: _legacy
        _gl = _ps.grant_hq_pass(1, rank=6)
    finally:
        (permits.PERMIT, permits.PERMIT_ALL, permits.PERMIT_RANKS) = _sv[:3]
        flat_globals()["CHAR_STORE"] = _sv[3]
    _pfc_ok = (_rd["FMO_PERMIT"] == "0" and _rd["FMO_PERMIT_RANKS"] == "4:hq"
               and (not ranks.RANK_LADDER or ranks.rank_name(4) == "Private First Class")
               and _g3 is None and _g4 is not None and _held == [25]
               and _pp.get(permits.GRANTED_KEY) == [25]
               and _g5 is None and not inventory.stored_items(_pp)
               and _gl is None)
    print(f"  manual p.44: release FMO_PERMIT=0 + FMO_PERMIT_RANKS=4:hq mints the HQ pass "
          f"at Private First Class, once: a spent pass is not refilled: "
          f"{'OK' if _pfc_ok else 'FAIL'}")
    ok &= _pfc_ok

    # --- 4. a new pilot starts a Conscript with gap-based money; an old record
    # with no stored rank still reads FMO_RANK (nobody is demoted)
    _sv = (economy.SEED_RANK, economy.SEED_MP, economy.SEED_CONTRIB, economy.START_MONEY,
           status.START_RANK)
    try:
        economy.SEED_RANK, economy.SEED_MP, economy.SEED_CONTRIB = (
            _rd["FMO_SEED_RANK"], _rd["FMO_SEED_MP"], _rd["FMO_SEED_CONTRIB"])
        economy.START_MONEY = economy.parse_start_money(_rd["FMO_START_MONEY"])
        status.START_RANK = 21
        _sd = economy.seed_new_character({"id": 1})
        _m = [economy.start_money(n, c)[0] for n, c in (
            (2, {1: 10, 2: 5}), (1, {1: 10, 2: 5}), (1, {1: 5, 2: 5}),
            (2, {1: 100, 2: 1}), (1, {1: 0, 2: 0}), (None, {1: 10, 2: 5}))]
        _rec = {"money": 10000}
        _a1 = economy.apply_start_money(_rec, 2, {1: 10, 2: 5})
        _a2 = economy.apply_start_money(_rec, 2, {1: 10, 2: 0})
        _legacy_rank = economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", {})[0]
        economy.SEED_RANK = ""
        _fallback = economy.seed_new_character({"id": 2})["rank"]
    finally:
        (economy.SEED_RANK, economy.SEED_MP, economy.SEED_CONTRIB, economy.START_MONEY,
         status.START_RANK) = _sv
    _seed_ok = (_sd["rank"] == 0 and _sd["mp"] == 0 and _sd["contribution"] == 0
                and _sd["money"] == 10000 and isinstance(_sd.get("born_at"), int)
                and _m == [15000, 10000, 10000, 19900, 10000, 10000]
                and _a1 and _rec["money"] == 15000 and _rec["start_money_nation"] == 2
                and _a2 is None and _rec["money"] == 15000
                and _legacy_rank == 21 and _fallback == 21
                and economy.parse_start_money("") is None
                and economy.parse_start_money("500") == (500, 0, 0))
    print(f"  manual p.38: a new pilot is a Conscript with 0 MP / 0 contribution and "
          f"10000 H$ (+100 per point of gap on the smaller side, once); a record "
          f"with no rank still reads FMO_RANK: {'OK' if _seed_ok else 'FAIL ' + str((_sd, _m))}")
    ok &= _seed_ok

    # --- 5. no delete for 24 h (code 0xC8E6 = 2:97)
    _now = int(time.time())
    _sv = (charselect.DELETE_LOCK_HOURS, charstore.CHAR_STORE)
    try:
        charselect.DELETE_LOCK_HOURS = int(_rd["FMO_DELETE_LOCK_HOURS"])
        flat_globals()["CHAR_STORE"] = ""
        _ds = session.Session("dellock:0")
        _ds._roster = [{"id": 1, "first": "New", "last": "Born", "born_at": _now - 3600},
                       {"id": 2, "first": "Old", "last": "Hand", "born_at": _now - 25 * 3600},
                       {"id": 3, "first": "Pre", "last": "Seed"}]
        _w1 = _ds.apply_charsel(0x013F, struct.pack("<I", 1), 1)
        _c1 = _ds.fail_code
        _ds.fail_code = None
        _w2 = _ds.apply_charsel(0x013F, struct.pack("<I", 2), 2)
        _w3 = _ds.apply_charsel(0x013F, struct.pack("<I", 3), 3)
    finally:
        charselect.DELETE_LOCK_HOURS = _sv[0]
        flat_globals()["CHAR_STORE"] = _sv[1]
    _del_ok = (isinstance(_w1, str) and _c1 == 0xC8E6 == charselect.DELETE_LOCK_CODE
               and _w2 is None and _w3 is None
               and [c["id"] for c in _ds._roster] == [1]
               and charselect.delete_locked({"born_at": _now - 60}, _now) is None)  # knob back at 0
    print(f"  manual p.38: a pilot under 24 h old is refused with 0xC8E6 (2:97); "
          f"older or pre-seed pilots delete: {'OK' if _del_ok else 'FAIL'}")
    ok &= _del_ok

    # --- 6. names: reserved (0xC90E) and taken (0xC43B) on create and on a
    # defection rename; the defection keeps the gender
    _taken = ("y", [{"id": 9, "first": "Taken", "last": "Name", "nation_byte": 2}])
    _rsv = [charstore.name_reserved(f, l) for f, l in (
        ("Gm", "Lex"), ("Lex", "Admin"), ("Game", "Master"), ("Lex", "Arden"))]
    _npc = charstore.name_reserved("Henry", "Viduka", pairs={("henry", "viduka")})
    _cs = session.Session("names:0")
    _cs._roster = []
    _cs.all_rosters = lambda: [_taken]
    _body = bytearray(0x3C)
    struct.pack_into("<I", _body, 0, 1)
    _body[0x04:0x06], _body[0x15:0x18], _body[0x28] = b"Gm", b"Lex", 1
    _wc = _cs.apply_charsel(0x013E, bytes(_body), 1)
    _cc = _cs.fail_code
    _cs.fail_code = None
    _n177 = bytearray(0x26)
    struct.pack_into("<I", _n177, 0, 1)
    _n177[0x04:0x09], _n177[0x15:0x19] = b"Taken", b"Name"
    _w177 = _cs.apply_charsel(0x0177, bytes(_n177), 1)
    _c177 = _cs.fail_code
    _cs.fail_code = None
    _name_ok = (_rsv[0] and _rsv[1] and _rsv[2] and _rsv[3] is None and _npc
                and isinstance(_wc, str) and _cc == 0xC90E and not _cs._roster
                and isinstance(_w177, str) and _c177 == 0xC43B)
    # the 0x01AA arm: a taken / reserved new name stores nothing; an allowed
    # rename keeps the stored gender whatever +0x26 says
    _arm = {}
    _sv = (charstore.CHAR_STORE, _dfx.DEFECTION)
    with _tf.TemporaryDirectory() as _td:
        flat_globals()["CHAR_STORE"] = os.path.join(_td, "chars.json")
        _dfx.DEFECTION = False
        try:
            for _tag, _first, _last in (("taken", b"Taken", b"Name"), ("rsv", b"Gm", b"Lex"),
                                        ("ok", b"Fresh", b"Name")):
                _p = {"id": 1, "first": "Def", "last": "Ector", "nation_byte": 1,
                      "nation": 2, "gender": 2}
                _s7 = session.Session("selftest-rename")
                _s7._roster = [_p]
                _s7.all_rosters = lambda: [("x", [_p]), _taken]
                _sb = bytearray(0x38)
                struct.pack_into("<I", _sb, 0, 1)
                _sb[0x04:0x04 + len(_first)], _sb[0x15:0x15 + len(_last)] = _first, _last
                _sb[0x26] = 1                                  # asks for male
                _o = _s7.on_packet(packet.parse(packet.build(0x01AA, bytes(_sb), 0x100C)))
                _q = packet.parse(_o[0]) if _o else {}
                _arm[_tag] = (_q.get("msg"), _q.get("conn"), dict(_p))
        finally:
            flat_globals()["CHAR_STORE"] = _sv[0]
            _dfx.DEFECTION = _sv[1]
    _t, _r, _k = _arm.get("taken", ()), _arm.get("rsv", ()), _arm.get("ok", ())
    _name_ok = (_name_ok and len(_t) == 3 and len(_r) == 3 and len(_k) == 3
                and _t[0] == charselect.MSG_FAIL and _t[1] == 0xC43B and _t[2]["first"] == "Def"
                and _r[0] == charselect.MSG_FAIL and _r[1] == 0xC90E and _r[2]["first"] == "Def"
                and _k[0] == 1 and _k[2]["first"] == "Fresh"
                and _k[2]["gender"] == 2 and _k[2]["nation"] == 2
                and _k[2]["nation_byte"] == 2)
    print(f"  manual p.38: reserved names (staff words, NPC names) refuse with 0xC90E "
          f"and taken ones with 0xC43B on 0x013E, 0x0177 and a defection rename, "
          f"which keeps the gender: {'OK' if _name_ok else 'FAIL ' + str(_arm)}")
    ok &= _name_ok

    # --- 7. the nation screen: live head counts, the larger side closed past
    # FMO_NATION_CLOSE_PCT; +0x08 < 0 closes O.C.U., +0x0C < 0 closes U.S.N.
    _sv = (charselect.NATION_POP_LIVE, charselect.NATION_CLOSE_PCT, charselect.NATION_CLOSE_MIN)
    try:
        charselect.NATION_POP_LIVE = _rd["FMO_NATION_POP"] == "live"
        charselect.NATION_CLOSE_PCT = int(_rd["FMO_NATION_CLOSE_PCT"])
        charselect.NATION_CLOSE_MIN = int(_rd["FMO_NATION_CLOSE_MIN"])
        _b1 = charselect.reply_019d({1: 40, 2: 20})
        _b2 = charselect.reply_019d({1: 20, 2: 40})
        _b3 = charselect.reply_019d({1: 12, 2: 5})       # 58% but only 7 apart
        _b4 = charselect.reply_019d({})
        _cnt = charselect.nation_counts([("a", [{"nation_byte": 1}, {"nation_byte": 2},
                                               {"nation_byte": 1}, {"first": "x"}])])
        charselect.NATION_POP_LIVE = False
        _b5 = charselect.reply_019d({1: 40, 2: 1})
    finally:
        (charselect.NATION_POP_LIVE, charselect.NATION_CLOSE_PCT,
         charselect.NATION_CLOSE_MIN) = _sv
    _pop_ok = (struct.unpack_from("<II", _b1, 0) == (40, 20)
               and _i32(_b1, 8) == -1 and _i32(_b1, 0xC) == 0
               and _i32(_b2, 8) == 0 and _i32(_b2, 0xC) == -1
               and _i32(_b3, 8) == 0 and _i32(_b3, 0xC) == 0
               and struct.unpack_from("<II", _b4, 0) == (1, 1) and _b4[0x10] == charselect.NATION_ENABLE
               and _cnt == {1: 2, 2: 1}
               and _i32(_b5, 8) == 0 and _i32(_b5, 0xC) == 0
               and charselect.nation_gap_pct({1: 40, 2: 20}) == 50
               and charselect.closed_nation({1: 40, 2: 20}, pct=0) is None)
    print(f"  manual p.38: 0x019D carries the live head counts and closes the larger "
          f"nation past a 30% lead of 10+ pilots (+0x08 O.C.U., +0x0C U.S.N.): "
          f"{'OK' if _pop_ok else 'FAIL'}")
    ok &= _pop_ok

    # --- 8. log back in where you logged out (p.42), never into 600-607
    _sv = (zoneentry.RESUME_ZONE, charstore.CHAR_STORE)
    _we = {}
    try:
        zoneentry.RESUME_ZONE = True
        _rz = [zoneentry.resume_mapkind(c, n, t)[0] for c, n, t in (
            ({"mapkind": 509}, 1, True), ({"mapkind": 207}, 2, True),
            ({"mapkind": 600}, 1, True), ({"mapkind": 300}, 1, True),
            ({"mapkind": 509}, 1, False), ({}, 1, True))]
        flat_globals()["CHAR_STORE"] = "selftest-stub"
        for _mk in (509, 600):
            _pc = {"id": 1, "first": "Res", "last": "Ume", "nation_byte": 1,
                   "mapkind": _mk, "mapno": 102}
            _rs = session.Session("resume:0")
            _rs.playing_char = lambda _pc=_pc: _pc
            _rs.pilot_trained = lambda: True
            _rs.commit = lambda why: None
            _o = [packet.parse(x) for x in _rs.on_packet(packet.parse(packet.build(
                zoneentry.MSG_0150_REQ, bytes(8), seq=0x66, conn_id=1)))]
            _we[_mk] = _u16(_o[0]["payload"], zoneentry.R153_MAPKIND) if _o else None
        zoneentry.RESUME_ZONE = False
        _off = zoneentry.resume_mapkind({"mapkind": 509}, 1, True)[0]
    finally:
        zoneentry.RESUME_ZONE = _sv[0]
        flat_globals()["CHAR_STORE"] = _sv[1]
    _res_ok = (_rz == [509, 207, None, None, None, None] and _off is None
               and _we.get(509) == 509 and _we.get(600) not in (None, 600))
    print(f"  manual p.42: FMO_RESUME_ZONE grants the stored zone at world entry (509 "
          f"back to 509), never the Coliseum band, an enemy-only zone or an untrained "
          f"pilot's: {'OK' if _res_ok else 'FAIL ' + str((_rz, _we))}")
    ok &= _res_ok
    return ok


def _manual_social_pins():
    """THE PLAYING MANUAL'S SOCIAL RULES (2026-09-30; pp.40-42, 47, 50, 54):
    /tell reaches its one recipient (never the room, never the enemy army,
    never an offline name), chat lines go only where their channel kind
    says, room-mates are targetable, Kick / Change Leader act, the squadron
    nation is the squadron's, a relogged member is re-attached, and members
    not Ready are left behind at the leader's sortie. One line per pin."""
    import types as _ty
    ok = True
    _rec_cmd = lambda r: struct.unpack_from("<I", r, 4)[0]

    # --- 1. /tell is 0x0181 with text; the Login Check has none
    def _tell_pl(first, last, text):
        b = bytearray(0x41A)
        for off, s in ((trade.Q181_FIRST, first), (trade.Q181_LAST, last)):
            b[off:off + len(s)] = s.encode()
        t = text.encode()
        b[trade.Q181_TEXT:trade.Q181_TEXT + len(t)] = t
        return bytes(b)

    _p_ok = (trade.parse_tell(_tell_pl("Bea", "Bee", "hi there")) == ("Bea", "Bee", "hi there")
             and trade.parse_tell(_tell_pl("Bea", "Bee", "")) is None
             and trade.parse_tell(b"\0" * 0x20) is None)
    print(f"  /tell: 0x0181 +0x10/+0x21 names, +0x32 text; no text = the Login "
          f"Check: {'OK' if _p_ok else 'FAIL'}")
    ok &= _p_ok

    def _pilot(ip, acct, first, last, nation):
        s = session.Session(ip + ":1")
        s._account = acct
        s.playing = 0x1001
        c = {"id": 1, "first": first, "last": last}
        s.playing_char = lambda c=c: c
        s.grant_nation = lambda n=nation: (n, "selftest")
        s.keepalive_at = time.time()
        return s

    _gt = flat_globals()
    _sv = (dict(trade.LIVE_SESSIONS), set(trade.LIVE_PILOTS), _gt["CHAR_STORE"])
    try:
        _gt["CHAR_STORE"] = "selftest"
        trade.LIVE_SESSIONS.clear()
        trade.LIVE_PILOTS.clear()
        sA = _pilot("203.0.113.21", "member:921", "Al", "Ay", 1)
        sB = _pilot("203.0.113.22", "member:922", "Bea", "Bee", 1)
        sC = _pilot("203.0.113.23", "member:923", "Cy", "Cee", 2)      # enemy army
        sD = _pilot("203.0.113.24", "member:924", "Di", "Dee", 1)      # bystander
        for s in (sA, sB, sC, sD):
            trade.LIVE_PILOTS.add(s)

        def _send(s, pl):
            o = s.on_packet(packet.parse(packet.build(trade.MSG_TELL, pl, seq=0x321, conn_id=1)))
            return [packet.parse(x) for x in o]

        _r1 = _send(sA, _tell_pl("bea", "BEE", "meet at the hangar"))
        _pb, _pd = sB.trade_pushes_due(1), sD.trade_pushes_due(1)
        _qb = [packet.parse(x) for x in _pb]
        _tb = (_qb[0]["payload"] if _qb else b"")
        _del_ok = (len(_r1) == 1 and _r1[0]["msg"] == handshake.MSG_SESSION_START
                   and len(_qb) == 1 and _qb[0]["msg"] == lobbymessage.MSG_LOBBY_MESSAGE
                   and _tb[lobbymessage.S14B_KIND] == trade.TELL_KIND
                   and _tb[lobbymessage.S14B_TEXT:].split(b"\0")[0] == b"meet at the hangar"
                   and _tb[lobbymessage.S14B_NAME1:].split(b"\0")[0] == b"Al"
                   and not [x for x in _pd if packet.parse(x)["msg"] == lobbymessage.MSG_LOBBY_MESSAGE])
        _r2 = _send(sA, _tell_pl("Cy", "Cee", "psst"))
        _r3 = _send(sA, _tell_pl("No", "Body", "anyone?"))
        _code = lambda r: (struct.unpack_from("<i", r[0]["payload"], trade.S185_CODE)[0]
                           if r and r[0]["msg"] == trade.MSG_TELL_RESULT else None)
        _pc = sC.trade_pushes_due(1)
        _ref_ok = (_code(_r2) == trade.TELL_CODE_ENEMY and _code(_r3) == trade.TELL_CODE_OFFLINE
                   and not [x for x in _pc if packet.parse(x)["msg"] == lobbymessage.MSG_LOBBY_MESSAGE])
        sB.keepalive_at = time.time() - trade.TELL_ONLINE_S - 5        # gone quiet
        _r4 = _send(sA, _tell_pl("Bea", "Bee", "still there?"))
        _off_ok = _code(_r4) == trade.TELL_CODE_OFFLINE
    finally:
        trade.LIVE_SESSIONS.clear()
        trade.LIVE_SESSIONS.update(_sv[0])
        trade.LIVE_PILOTS.clear()
        for s in _sv[1]:
            trade.LIVE_PILOTS.add(s)
        _gt["CHAR_STORE"] = _sv[2]
    print(f"  /tell: delivered to the named pilot ONLY as 0x014B kind 3 (sender "
          f"names, text), the bystander gets nothing -> message 1: "
          f"{'OK' if _del_ok else 'FAIL'}")
    print(f"  /tell: the enemy army -> 0x0185 {trade.TELL_CODE_ENEMY} and nothing "
          f"reaches them; an unknown name / a silent session -> "
          f"{trade.TELL_CODE_OFFLINE}: {'OK' if _ref_ok and _off_ok else 'FAIL'}")
    ok &= _del_ok and _ref_ok and _off_ok

    # --- 2. chat lines go by channel kind and by identity
    _gk = lambda h: (h, 19155, "group")
    _was_peers = dict(groupchannel.WORLD_PEERS)
    _was_g = (dict(groupchannel.GROUP_MEMBERS), dict(groupchannel.GROUP_OF))
    _was_rm = rooms.room_mates
    try:
        _now = time.time()

        def _gchan(h, acct):
            c = worldchannel.WorldChannel((h, 19155))
            c.key, c.account, c.seen_at = groupchannel.GROUP_KEY, acct, _now
            c.peer_key = _gk(h)
            groupchannel.WORLD_PEERS[_gk(h)] = c
            return c

        gA = _gchan("198.51.100.31", "member:931")
        gB = _gchan("198.51.100.32", "member:932")
        gX = _gchan("198.51.100.33", "member:933")     # another group
        groupchannel.GROUP_MEMBERS[9301] = ["member:931", "member:932"]
        groupchannel.GROUP_MEMBERS[9302] = ["member:933"]
        groupchannel.GROUP_OF.update({"member:931": 9301, "member:932": 9301,
                                      "member:933": 9302})
        wA = worldchannel.WorldChannel(("198.51.100.31", 19155))
        wA.key, wA.pos = b"1lobby", (0.0, 0.0, 0.0)
        wN = worldchannel.WorldChannel(("198.51.100.34", 19155))
        wF = worldchannel.WorldChannel(("198.51.100.35", 19155))
        wN.pos, wF.pos = (3.0, 0.0, 4.0), (30.0, 0.0, 40.0)
        rooms.room_mates = lambda chan: [wN, wF]
        _g3 = datagram.chat_route(gA, 3)
        _grp_ok = (_g3 is not None and _g3[0] == [gB] and _g3[1] == datagram.CHAT_GROUP_CHANNEL
                   and datagram.chat_route(gA, 2) is None and datagram.chat_route(gA, 7) is None
                   and datagram.chat_route(wA, 3) is None and datagram.chat_route(wA, 9) is None)
        _say = datagram.chat_route(wA, 2)
        _was_r = datagram.CHAT_SAY_RADIUS
        datagram.CHAT_SAY_RADIUS = 10.0
        _near = datagram.chat_route(wA, 2)
        datagram.CHAT_SAY_RADIUS = _was_r
        _say_ok = (_say is not None and _say[0] == [wN, wF]
                   and _near is not None and _near[0] == [wN])
    finally:
        rooms.room_mates = _was_rm
        groupchannel.WORLD_PEERS.clear()
        groupchannel.WORLD_PEERS.update(_was_peers)
        groupchannel.GROUP_MEMBERS.clear()
        groupchannel.GROUP_MEMBERS.update(_was_g[0])
        groupchannel.GROUP_OF.clear()
        groupchannel.GROUP_OF.update(_was_g[1])
    print(f"  chat: a /bg line reaches the sender's battle group only (not "
          f"another group, not the room); a world channel refuses group/"
          f"mission/squadron kinds and a group channel refuses room kinds: "
          f"{'OK' if _grp_ok else 'FAIL'}")
    print(f"  chat: /say reaches the room, and only those within "
          f"FMO_CHAT_SAY_RADIUS when it is set: {'OK' if _say_ok else 'FAIL'}")
    ok &= _grp_ok and _say_ok

    # --- 3. a room-mate's POP is targetable (body+0x48 bit 0x10), the tag bit kept
    def _room_pop(targetable):
        _wp = dict(groupchannel.WORLD_PEERS)
        _wr = (room.ROOM, rooms.room_mates, roomrelay.POP_PLAYER_TARGETABLE,
               fmoworld.NAMETAG)
        try:
            room.ROOM = True
            roomrelay.POP_PLAYER_TARGETABLE = targetable
            fmoworld.NAMETAG = True
            a = worldchannel.WorldChannel(("198.51.100.41", 19155))
            b = worldchannel.WorldChannel(("198.51.100.42", 19155))
            for c in (a, b):
                c.key, c.tables, c.popped = b"1lobby", groupchannel.GROUP_TABLES, True
                c.seen_at = time.time()
            groupchannel.WORLD_PEERS[a.addr] = a
            groupchannel.WORLD_PEERS[b.addr] = b
            rooms.room_mates = lambda chan: [b] if chan is a else []
            roomrelay.room_queue(a)
            pops = [r for r in a.pending if _rec_cmd(r) == fmoworld.CMD_POP]
            return pops[0][fmoworld.REC_HDR + fmoworld.POP_TARGETABLE] if pops else None
        finally:
            room.ROOM, rooms.room_mates, roomrelay.POP_PLAYER_TARGETABLE, fmoworld.NAMETAG = _wr
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_wp)

    _t_on, _t_off = _room_pop(True), _room_pop(False)
    _tg_ok = (_t_on is not None and _t_on & fmoworld.POP_TARGETABLE_BIT
              and _t_on & fmoworld.POP_NAMETAG_BIT
              and _t_off is not None and not _t_off & fmoworld.POP_TARGETABLE_BIT)
    print(f"  room: a room-mate's POP carries body+0x48 bit 0x10 (listable in "
          f"/target and Trade) with the name-tag bit kept ({_t_on!r}); "
          f"FMO_UDP_POP_PLAYER_TARGETABLE=0 clears it ({_t_off!r}): "
          f"{'OK' if _tg_ok else 'FAIL'}")
    ok &= bool(_tg_ok)

    # --- 4. Kick / Change Leader act, by the alias the leader's client names
    _was_peers = dict(groupchannel.WORLD_PEERS)
    _was_g = (dict(groupchannel.GROUP_MEMBERS), dict(groupchannel.GROUP_OF),
              dict(groupchannel.GROUP_READY), dict(battlegroups.GROUP_STATE),
              dict(battlegroups.GROUP_CREATOR_ACCOUNT))
    _sv = (dict(trade.LIVE_SESSIONS), set(trade.LIVE_PILOTS))
    try:
        trade.LIVE_SESSIONS.clear()
        trade.LIVE_PILOTS.clear()
        L, B, C = "member:941", "member:942", "member:943"
        gid = 9401
        chans = {}
        for i, acct in enumerate((L, B, C)):
            c = worldchannel.WorldChannel((f"198.51.100.5{i}", 19155))
            c.key, c.account, c.seen_at = groupchannel.GROUP_KEY, acct, time.time()
            c.peer_key = (c.addr[0], c.addr[1], "group")
            groupchannel.WORLD_PEERS[c.peer_key] = c
            chans[acct] = c
        for a in (L, B, C):                  # everyone knows everyone by an alias
            for o in (L, B, C):
                if o != a:
                    chans[a].alias_for(chans[o].peer_key)
                    chans[a].remotes[chans[a].alias_of[chans[o].peer_key]].popped = True
        groupchannel.GROUP_MEMBERS[gid] = [L, B, C]
        for a in (L, B, C):
            groupchannel.GROUP_OF[a] = gid
        battlegroups.register_group(gid, L, {"bonus": 500})
        battlegroups.GROUP_CREATOR_ACCOUNT[gid] = L
        sB = _pilot("198.51.100.51", B, "Bea", "Bee", 1)
        trade.LIVE_PILOTS.add(sB)
        aB = chans[L].alias_of[chans[B].peer_key]
        aC = chans[L].alias_of[chans[C].peer_key]
        _res_ok = (groupchannel.group_member_by_alias(L, aB) == B
                   and groupchannel.group_member_by_alias(L, 0x7777) is None)
        _not_leader = battlegroups.group_change_leader(gid, B, C)
        _cl = battlegroups.group_change_leader(gid, L, C)
        _cl_ok = (_not_leader is not None and _not_leader[0] == battlegroups.CODE_LEADER_ONLY
                  and _cl is None and battlegroups.group_leader(gid) == C
                  and battlegroups.GROUP_STATE[gid]["bonus"] == 0)
        _old_lead = battlegroups.group_kick(gid, L, B)     # L is no longer the leader
        _cpend = len(chans[C].pending)
        _k = battlegroups.group_kick(gid, C, B)
        _drops = [r for r in chans[C].pending[_cpend:]
                  if _rec_cmd(r) == roomrelay.GROUP_BLOB_UPDATE_CMD]
        _flags = (struct.unpack_from("<I", _drops[0], fmoworld.REC_HDR + 8
                                     + groupchannel.GROUP_POP_FLAGS_OFF
                                     - groupchannel.G191_BLOB_FROM)[0] if _drops else None)
        _pk = [packet.parse(x) for x in sB.trade_pushes_due(1)]
        _end = [q for q in _pk if q["msg"] == pushes.MSG_GROUP_ENDED]
        _kick_ok = (_old_lead is not None and _k is None
                    and B not in groupchannel.GROUP_MEMBERS[gid] and B not in groupchannel.GROUP_OF
                    and _flags is not None and not _flags & groupchannel.G190_STATUS_BIT0
                    and len(_end) == 1
                    and struct.unpack_from("<II", _end[0]["payload"], 0)
                    == (gid, battlegroups.GROUP_REASON_KICKED))
        # --- 6. the leader sorties: whoever is not Ready is left behind
        groupchannel.GROUP_MEMBERS[gid] = [C, L]
        groupchannel.GROUP_OF[L] = gid
        groupchannel.GROUP_READY[L] = (2, 0)
        _gone = battlegroups.auto_remove_unready(gid, C)
        groupchannel.GROUP_MEMBERS[gid] = [C, L]
        groupchannel.GROUP_OF[L] = gid
        groupchannel.GROUP_READY[L] = (1, 0)
        _kept = battlegroups.auto_remove_unready(gid, C)
        _sk_ok = _gone == [L] and _kept == [] and L in groupchannel.GROUP_MEMBERS[gid]
        # --- 5. a relogged member of a standing group is attached again, once
        _sL = _pilot("198.51.100.50", L, "Al", "Ay", 1)
        groupchannel._joined_at.pop(L, None)
        _ra = groupchannel.group_reattach(_sL, 1)
        _ra2 = groupchannel.group_reattach(_sL, 1)
        _sB2 = _pilot("198.51.100.51", B, "Bea", "Bee", 1)     # B was kicked
        _ra_ok = (_ra is not None and packet.parse(_ra)["msg"] == grouplogin.MSG_GROUP_ATTACH
                  and struct.unpack_from("<I", packet.parse(_ra)["payload"], 0)[0] == gid
                  and _ra2 is None and groupchannel.group_reattach(_sB2, 1) is None)
    finally:
        groupchannel.WORLD_PEERS.clear()
        groupchannel.WORLD_PEERS.update(_was_peers)
        for d, w in zip((groupchannel.GROUP_MEMBERS, groupchannel.GROUP_OF,
                         groupchannel.GROUP_READY, battlegroups.GROUP_STATE,
                         battlegroups.GROUP_CREATOR_ACCOUNT), _was_g):
            d.clear()
            d.update(w)
        trade.LIVE_SESSIONS.clear()
        trade.LIVE_SESSIONS.update(_sv[0])
        trade.LIVE_PILOTS.clear()
        for s in _sv[1]:
            trade.LIVE_PILOTS.add(s)
    print(f"  battle group: the member list's UnitID resolves to the member's "
          f"account: {'OK' if _res_ok else 'FAIL'}")
    print(f"  battle group: Change Leader moves the lead and clears the B.G.Bonus "
          f"(D92 237); a member is refused {battlegroups.CODE_LEADER_ONLY}: "
          f"{'OK' if _cl_ok else 'FAIL'}")
    print(f"  battle group: Kick removes the member, drops it from the others' "
          f"lists (cmd 191 without +0x54 bit 0) and pushes it 0x0178 "
          f"{{GroupID, 2}}; the old leader is refused: {'OK' if _kick_ok else 'FAIL'}")
    print(f"  battle group: members not Ready are removed at the leader's "
          f"sortie, a Ready one stays (p.54): {'OK' if _sk_ok else 'FAIL'}")
    print(f"  battle group: a relogged member is re-attached once (0x0174 with "
          f"its GroupID), a kicked one is not (p.42): {'OK' if _ra_ok else 'FAIL'}")
    ok &= _res_ok and _cl_ok and _kick_ok and _sk_ok and _ra_ok

    # --- 7. the squadron's +0x09 is the squadron's recorded nation
    _store = {}
    _fake = _ty.SimpleNamespace(
        squadron_nation=lambda g: _store.get(g, 0),
        set_squadron_nation=lambda g, n, who=None: _store.setdefault(g, n),
        squadron_insignia=lambda g: 1)
    _sq_was = (squadron.fmostore, charstore.FMO_DB, squadron.SERVE_SQUADRON,
               squadron.SQUADRON_NATION, squadron.SQUADRON_GROUPS,
               squadron.FILL_01AD, dict(squadron.FIELDS_01AD),
               squadron.SQUADRON_NATION_STORE)
    try:
        squadron.fmostore = _fake
        charstore.FMO_DB = "1"
        squadron.SERVE_SQUADRON, squadron.SQUADRON_NATION = 1, -1
        squadron.SQUADRON_GROUPS, squadron.FILL_01AD = [], 0
        squadron.FIELDS_01AD.clear()
        squadron.SQUADRON_NATION_STORE = True
        _req = bytearray(squadron.Q1AD_LEN + 12)
        struct.pack_into("<II", _req, 0, 77, 0)
        _grp = {77: {"class": 5, "formed": 0}}
        _usn = {"id": 1, "first": "Ula", "last": "Usn", "nation_byte": 2, "nation": 2}
        _was_npc = (zoneentry.NATION_PER_CHARACTER, status.SERVE_START_STATUS,
                    status.STATUS_NATION)
        # the viewer resolver needs a 0x014A to be served at all, and a global
        # nation unlike the pilot's, so the two answers can be told apart
        zoneentry.NATION_PER_CHARACTER, status.SERVE_START_STATUS = True, True
        status.STATUS_NATION = 1
        try:
            _b1, _w1 = squadron.reply_01ad(bytes(_req), _grp, char=_usn)
            _first = _b1[squadron.SQ_NATION]
            _b2, _w2 = squadron.reply_01ad(bytes(_req), _grp)          # the served reply
            squadron.SQUADRON_NATION_STORE = False
            _b3, _w3 = squadron.reply_01ad(bytes(_req), _grp)
            _viewer = zoneentry.script_nation(None)[0]
        finally:
            (zoneentry.NATION_PER_CHARACTER, status.SERVE_START_STATUS,
             status.STATUS_NATION) = _was_npc
        _sqn_ok = (_store.get(77) == _first == 2 and _viewer == 1
                   and _b2[squadron.SQ_NATION] == _store.get(77)
                   and _b3[squadron.SQ_NATION] == (_viewer & 0xFF))
    finally:
        (squadron.fmostore, charstore.FMO_DB, squadron.SERVE_SQUADRON,
         squadron.SQUADRON_NATION, squadron.SQUADRON_GROUPS,
         squadron.FILL_01AD, _f, squadron.SQUADRON_NATION_STORE) = _sq_was
        squadron.FIELDS_01AD.clear()
        squadron.FIELDS_01AD.update(_f)
    print(f"  squadron: +0x09 is recorded once ({_store.get(77)!r}) and served "
          f"from the record to every viewer; FMO_SQUADRON_NATION_STORE=0 "
          f"stamps the viewer's again: {'OK' if _sqn_ok else 'FAIL'}")
    ok &= bool(_sqn_ok)
    return ok


def _coliseum_pins():
    """THE COLISEUM (coliseum.py, 2026-10-01), no client: the 128-byte arena
    record, the list / board / push layouts the client's parses read, the
    official arenas, hosting (rank, MP, the day's slots, one per pilot),
    registration (headcount, leader, B.G.Cost, a full tournament, money) and
    a session driven through every desk. One line per pin; every global it
    touches is restored."""
    from . import coliseum as _co
    ok = True
    _now = 1_800_000_000.0

    # --- 1. the record: offsets, the map encoding, undecoded bytes kept
    _a = {"id": 1001, "promoter": "Lex.Arden", "name": "Iron Cup", "req_bgs": 4,
          "headcount": 3, "bg_cost": 5, "total_cost": 15, "format": 2,
          "weapons": [1] + [0] * 11 + [2], "bps": [0] * 11 + [1], "score": 5000,
          "main_rule": 1, "sub_rule": 2, "mods": 0, "arms": 1, "dup_bp": 0,
          "start": 1_800_000_600, "end": 1_800_007_800, "tile": 30001, "fee": 4500}
    _r = _co.arena_row(_a)
    _raw = bytearray(_r)
    _raw[0x38], _raw[0x58] = 0x77, 0x66            # bytes nobody has decoded
    _back = _co.arena_row(_co.parse_arena_row(bytes(_raw)))
    _row_ok = (len(_r) == 0x80
               and struct.unpack_from("<I", _r, 0x00)[0] == 1001
               and _r[0x04:0x0D] == b"Lex.Arden" and _r[0x14:0x1C] == b"Iron Cup"
               and (_r[0x34], _r[0x35], _r[0x36], _r[0x37], _r[0x39]) == (4, 3, 5, 15, 2)
               and _r[0x3C] == 1 and _r[0x48] == 2 and _r[0x54] == 1
               and struct.unpack_from("<I", _r, 0x5C)[0] == 5000
               and (_r[0x60], _r[0x61], _r[0x62], _r[0x63], _r[0x64], _r[0x65]) == (1, 2, 0, 1, 0, 0)
               and struct.unpack_from("<III", _r, 0x68) == (1_800_000_600, 1_800_007_800, 903030001)
               and struct.unpack_from("<I", _r, 0x74)[0] == 4500
               and _back == bytes(_raw))
    print(f"  coliseum: the 128-byte arena record (id +0x00, promoter +0x04, name +0x14, "
          f"BG count/headcount/cost +0x34..+0x37, format +0x39, rules +0x3C/+0x49/+0x5C.."
          f"+0x65, start/end +0x68/+0x6C, map 903,000,000+tile +0x70, fee +0x74) "
          f"round-trips, undecoded bytes kept: {'OK' if _row_ok else 'FAIL'}")
    ok &= _row_ok

    # --- 2. the list, board and push bodies
    _lb = _co.list_body([_r, _r], open_slots=2)
    _bb = _co.board_body(_r, [("Lex.Arden", 2, _co.STATE_SELF, 3), ("Bea.Bee", 0, 0, 1)])
    _wire_ok = (len(_lb) == 1300 and struct.unpack_from("<II", _lb, 0x0C) == (2, 2)
                and _lb[0x14:0x94] == _r and _lb[0x94:0x114] == _r
                and _co.list_body([_r] * 12)[0x10] == 10
                and len(_bb) == 3012 and struct.unpack_from("<i", _bb, 0)[0] == 0
                and _bb[4:0x84] == _r and _bb[0x84] == 2
                and _bb[0x8C + 1:0x8C + 4] == bytes((2, 3, 3))
                and _bb[0x8C + 4:0x8C + 13] == b"Lex.Arden"
                and _bb[0x8C + 0x58 + 4:0x8C + 0x58 + 11] == b"Bea.Bee"
                and _co.add_update_body(_r) == bytes(4) + _r
                and _co.cancel_update_body(-4) == struct.pack("<i", -4)
                and all(m in pushes.LOBBY_PUSH_ALL for m in
                        (_co.MSG_ADD_UPDATE, _co.MSG_CANCEL_UPDATE, _co.MSG_RET_UPDATE))
                and all(lobapi.LOBAPI[q][0] == r for q, r in (
                    (_co.MSG_LIST_REQ, 0x01B3), (_co.MSG_REGISTER_REQ, 0x01B6),
                    (_co.MSG_CANCEL_REQ, 0x01B8), (_co.MSG_RETURN_REQ, 0x01BC),
                    (_co.MSG_HOST_REQ, 0x01BE), (_co.MSG_BOARD_REQ, 0x01C3)))
                and lobapi.LOBAPI[_co.MSG_LIST_REQ][1] == _co.LIST_LEN
                and lobapi.LOBAPI[_co.MSG_BOARD_REQ][1] == _co.BOARD_LEN)
    print(f"  coliseum: 0x01B3 = slots +0x0C, count +0x10, ten 0x80 rows from +0x14 "
          f"(1300 B); 0x01C3 = status, record, count +0x84, 0x58-byte entries from "
          f"+0x8C (3012 B); 0x01B9 = result + record, 0x01BA = result; the reply ids "
          f"are the lobby API's: {'OK' if _wire_ok else 'FAIL'}")
    ok &= _wire_ok

    # --- 3. the official arenas and the hosting fee table (0x611B16F0)
    _off = _co.parse_official("1:4:30001,3:4:30021:Trio")
    try:
        _co.parse_official("6:4:30001")
        _bad = False
    except ValueError:
        _bad = True
    _fee_ok = ([(a["id"], a["headcount"], a["total_cost"], a["name"]) for a in _off]
               == [(1, 1, 4, "Official 1 vs 1"), (2, 3, 12, "Trio")]
               and _bad and all(a["fee"] == 0 for a in _off)
               and (_co.host_fee(1, 1), _co.host_fee(1, 5), _co.host_fee(2, 3, 8),
                    _co.host_fee(2, 3, 4)) == (2000, 6000, 9000, 4500)
               and _co.prize_per_win({"fee": 4500, "format": 2}) == 9000
               and _co.prize_per_win({"fee": 2000, "format": 1}) == 2000)
    print(f"  coliseum: official arenas from FMO_COLISEUM_OFFICIAL (ids 1..n, free); "
          f"the client's fee table, a 4-BG tournament half, the tournament prize "
          f"twice the fee: {'OK' if _fee_ok else 'FAIL'}")
    ok &= _fee_ok

    # --- 3b. the official schedule (fan-site table, JST): cost by hour, rules by day
    import calendar as _cal
    _utc = lambda *t: _cal.timegm(t + (0, 0))           # noqa: E731
    _mon = _co.official_rules(_utc(2026, 10, 5, 1, 0), 3)     # Mon 10:00 JST
    _tue = _co.official_rules(_utc(2026, 10, 6, 5, 0), 1)     # Tue 14:00 JST
    _sun = _co.official_rules(_utc(2026, 10, 11, 8, 0), 5)    # Sun 17:00 JST
    _sat = _co.official_rules(_utc(2026, 10, 10, 11, 0), 5)   # Sat 20:00 JST
    _sat3 = _co.official_rules(_utc(2026, 10, 10, 11, 0), 3)
    _W, _B = _co.WEAPON_SLOTS.index, _co.BP_SLOTS.index
    _auto = _co.parse_official("1:auto:30001,3:4:30021")
    _sched_ok = (
        (_mon["bg_cost"], _mon["total_cost"], _tue["bg_cost"], _sun["bg_cost"]) == (5, 15, 6, 4)
        and (_mon["main_rule"], _mon["sub_rule"]) == (0, 0)
        and _mon["weapons"][_W("mg")] == 2 and _mon["weapons"][_W("srf")] == 1
        and _mon["bps"][_B("turbo")] == 2 and _mon["bps"][_B("jet")] == 1
        and _tue["sub_rule"] == 1 and not any(_tue["weapons"] + _tue["bps"])
        and _sun["sub_rule"] == 1 and _sun["arms"] == 0
        and _sun["weapons"][_W("kn")] == 1 and _sun["bps"][_B("jet")] == 2
        and (_sat["main_rule"], _sat["sub_rule"]) == (2, 0)
        and (_sat3["main_rule"], _sat3["sub_rule"]) == (0, 1)
        and [a["schedule"] for a in _auto] == [True, False]
        and _co.scheduled(_auto[1], 0) is _auto[1]
        and _co.scheduled(_auto[0], _utc(2026, 10, 5, 1, 0))["bg_cost"] == 5)
    print(f"  coliseum: official schedule -- cost 5/6/4 by JST hour, MWF S&D with MG/SG/"
          f"turbo required, TTS Sudden Death open, Sun no melee/arms + jet, Sat night "
          f"5-person Heavy Mobile Weapon; `auto` only: {'OK' if _sched_ok else 'FAIL'}")
    ok &= _sched_ok

    # --- 4. hosting: the day's slots, rank, MP, one arena per pilot, the map
    _sv = (_co.HOST_CAP, _co.OFFICIAL, _co.COLISEUM, _co._COLISEUM)
    try:
        _co.OFFICIAL = _co.parse_official("1:4:30001,3:4:30021")
        _co.HOST_CAP = 2
        col = _co.Coliseum(load=False)
        col.save = lambda: True
        _req = _co.parse_arena_row(_co.arena_row(dict(_a, id=0, format=1, req_bgs=0,
                                                      headcount=1, score=1)))
        _v0 = col.host_verdict("h:a", {}, _req, _now, rank=17, mp=500)[0]
        _v1 = col.host_verdict("h:a", {}, _req, _now, rank=18, mp=99)[0]
        _v2 = col.host_verdict("h:a", {}, dict(_req, tile=12345), _now, rank=18, mp=500)[0]
        _v3 = col.host_verdict("h:a", {}, dict(_req, format=2, req_bgs=5), _now, rank=18, mp=500)[0]
        _v4 = col.host_verdict("h:a", {}, _req, _now, rank=18, mp=500)[0]
        _h = col.host("h:a", {"first": "Lex", "last": "Arden"}, _req, _now)
        _v5 = col.host_verdict("h:a", {}, _req, _now, rank=18, mp=500)[0]
        col.host("h:b", {"first": "Bea", "last": "Bee"}, _req, _now)
        _v6 = col.host_verdict("h:c", {}, _req, _now, rank=18, mp=500)[0]
        _slots_full = col.open_slots(_now)
        _c2 = _co.Coliseum(load=False)
        _c2.save = lambda: True
        _c2.day, _c2.hosted_today = _co.game_day(_now), 2
        _next_day = _c2.open_slots(_now + 86400)
        _host_ok = ((_v0, _v1, _v2, _v3, _v4, _v5, _v6)
                    == (-1, -1, -1, -1, 0, -3, -2)
                    and _h["id"] == _co.HOSTED_BASE and _h["promoter"] == "Lex.Arden"
                    and _h["fee"] == 2000 and _h["start"] == int(_now) + _co.HOST_LEAD
                    and _h["end"] == _h["start"] + _co.HOST_HOURS * 3600
                    and _h["score"] == _co.TARGET_SCORES[1] and _slots_full == 0
                    and _next_day == 2)
        print(f"  coliseum: hosting needs rank 18 (2nd Lt) and {_co.HOST_MP} MP, a map "
              f"from SE's 600 table, a 4/8-BG tournament; one arena per pilot (-3, 94:34), "
              f"the day's slots (-2, 94:35) reset at {_co.DAY_HOUR_UTC:02d}:00 UTC; the "
              f"server sets id, promoter, fee and times: {'OK' if _host_ok else 'FAIL'}")
        ok &= _host_ok

        # --- 5. registration rules, cancel, the ended arena's notice
        o1, o3 = col.find(1), col.find(2)
        _codes = (
            col.register_verdict(None, "p:a", ["p:a"], "p:a", _now)[0],
            col.register_verdict(o3, "p:a", ["p:a"], "p:a", _now)[0],
            col.register_verdict(o3, "p:b", ["p:a", "p:b", "p:c"], "p:a", _now)[0],
            col.register_verdict(o1, "p:a", ["p:a"], "p:a", _now,
                                 costs={"p:a": 5})[0],
            col.register_verdict(o1, "p:a", ["p:a"], "p:a", _now,
                                 costs={"p:a": 4})[0],
        )
        col.register(o1, "p:a", ["p:a"], "P.A", None, _now)
        _twice = col.register_verdict(o1, "p:a", ["p:a"], "p:a", _now)[0]
        _h2 = col.find(_co.HOSTED_BASE)
        _money = col.register_verdict(_h2, "p:z", ["p:z"], "p:z", _now, money=10)[0]
        _ended = col.register_verdict(_h2, "p:z", ["p:z"], "p:z", _h2["end"] + 1)[0]
        _t = col.host("h:t", {"first": "Tee"}, dict(_req, format=2, req_bgs=4, headcount=1),
                      _now - 86400 * 3)
        col.hosted[_t["id"]].update(start=int(_now + 100), end=int(_now + 9999))
        for i in range(4):
            col.register(_t, f"t:{i}", [f"t:{i}"], f"T.{i}", None, _now)
        _full = col.register_verdict(_t, "t:9", ["t:9"], "t:9", _now)[0]
        _closed = col.register_verdict(dict(_t, start=int(_now - 1)), "t:9", ["t:9"],
                                       "t:9", _now)[0]
        _e = col.cancel("p:a")
        _gone = col.entry_for("p:a") is None and "p:a" not in col.entry_of
        _r_ok = (_codes == (_co.CODE_GONE, _co.CODE_HEADCOUNT, _co.CODE_LEADER,
                            _co.CODE_COST, 0)
                 and _twice == _co.CODE_TWICE and _money == _co.CODE_MONEY
                 and _ended == _co.CODE_ENDED and _full == _co.CODE_FULL
                 and _closed == _co.CODE_CLOSED and _e is not None and _gone)
        print(f"  coliseum: register refuses no arena / a headcount mismatch / a member "
              f"not the leader / B.G.Cost over the arena's / twice / short of the fee / "
              f"an ended arena / a full or started tournament; cancel frees the pilot: "
              f"{'OK' if _r_ok else 'FAIL'}")
        ok &= _r_ok
        col.register(_h2, "w:a", ["w:a", "w:b"], "W.A", 77, _now)
        col.tick(_h2["end"] + 1)
        _na, _nb = col.take_notices("w:a"), col.take_notices("w:b")
        _tick_ok = (col.find(_co.HOSTED_BASE) is None and "w:a" not in col.entry_of
                    and [(m, struct.unpack_from("<i", b, 0)[0]) for m, b, _w, _x in _na + _nb]
                    == [(_co.MSG_CANCEL_UPDATE, -4)] * 2 and col.take_notices("w:a") == [])
        print(f"  coliseum: an arena past its end is dropped and every pilot waiting in "
              f"it is owed 0x01BA -4 (88:26), once: {'OK' if _tick_ok else 'FAIL'}")
        ok &= _tick_ok

        # --- 6. the desks, through Session.on_packet
        _co.COLISEUM = True
        _co._COLISEUM = col
        col.entries.clear(), col.entry_of.clear(), col.notices.clear()
        col.hosted.clear()
        col.day, col.hosted_today = None, 0
        _gt = flat_globals()
        _store_was = _gt["CHAR_STORE"]
        _gt["CHAR_STORE"] = "selftest"
        try:
            _c = {"id": 1, "first": "Ned", "last": "Arena", "rank": 21, "mp": 250,
                  "money": 9000}
            s = session.Session("198.51.100.90:1")
            s._account = "col:kai"
            s.playing_char = lambda: _c
            s.commit = lambda what: None
            _go = lambda m, b=b"": [packet.parse(x) for x in
                                     s.on_packet(packet.parse(packet.build(m, b, 0x200)))]
            _l0 = _go(_co.MSG_LIST_REQ, bytes(20))
            _l1 = _go(_co.MSG_LIST_REQ, b"\x01" + bytes(19))
            _rg = _go(_co.MSG_REGISTER_REQ, struct.pack("<I", 1) + bytes(32))
            _rg2 = _go(_co.MSG_REGISTER_REQ, struct.pack("<I", 1) + bytes(32))
            _bd = _go(_co.MSG_BOARD_REQ, struct.pack("<I", 1) + bytes(32))
            _cn = _go(_co.MSG_CANCEL_REQ, bytes(32))
            _req_row = _co.arena_row(dict(_a, id=0, format=1, req_bgs=0, headcount=1,
                                          tile=30021))
            col.day, col.hosted_today = None, 0
            _hs = _go(_co.MSG_HOST_REQ, _req_row + bytes(32))
            _hid = struct.unpack_from("<i", _hs[0]["payload"], 0)[0] if _hs else None
            _rh = _go(_co.MSG_REGISTER_REQ, struct.pack("<I", _hid or 0) + bytes(32))
            _rt = _go(_co.MSG_RETURN_REQ, bytes(32))
            col.notify("col:kai", _co.MSG_CANCEL_UPDATE, _co.cancel_update_body(0), "test")
            _ka = [packet.parse(x) for x in s.coliseum_pushes_due(1)]
            _sweep = all(isinstance(s.on_packet(packet.parse(packet.build(m, bytes(64), 0x200))), list)
                         for m in _co.HANDLED)
            _desk_ok = (
                [x["msg"] for x in _l0] == [0x01B3]
                and struct.unpack_from("<II", _l0[0]["payload"], 0x0C) == (2, 2)
                and struct.unpack_from("<I", _l1[0]["payload"], 0x10)[0] == 0
                and [x["msg"] for x in _rg] == [0x01B6, 0x01B9]
                and _rg[1]["payload"][4:8] == struct.pack("<I", 1)
                and [(x["msg"], x["conn"]) for x in _rg2] == [(2, _co.CODE_TWICE)]
                and [x["msg"] for x in _bd] == [0x01C3] and len(_bd[0]["payload"]) == 3012
                and _bd[0]["payload"][0x84] >= 1
                and _bd[0]["payload"][0x8C + 2] == _co.STATE_SELF
                and [x["msg"] for x in _cn] == [0x01B8]
                and struct.unpack_from("<i", _cn[0]["payload"], 0)[0] == -1
                and "col:kai" not in col.entry_of
                and [x["msg"] for x in _hs] == [0x01BE] and _hid == col.next_id - 1
                and _c["mp"] == 250 - _co.HOST_MP
                and [x["msg"] for x in _rh] == [0x01B6, pushes.MSG_FEE_PUSH, 0x01B9]
                and _c["money"] == 9000 - 2000
                and struct.unpack_from("<I", _rh[1]["payload"], 0x0C)[0] == 2000
                and [x["msg"] for x in _rt] == [0x01BC]
                and struct.unpack_from("<i", _rt[0]["payload"], 0)[0] == -1
                and [x["msg"] for x in _ka] == [0x01BA] and _sweep)
            print(f"  coliseum desks via on_packet: LIST mode 0 = the officials + 2 slots, "
                  f"mode 1 = nothing to watch; REGISTER -> 0x01B6 + 0x01B9 (the waiting "
                  f"window), twice -> message 2 [FM{_co.CODE_TWICE}]; BOARD -> 3012 B with "
                  f"the viewer highlighted; CANCEL -> 0x01B8 -1; HOST -> 0x01BE id, "
                  f"{_co.HOST_MP} MP taken; a hosted arena's REGISTER charges its fee "
                  f"(0x01A1 +0x0C); RETURN -> 0x01BC -1; the keepalive drains notices; "
                  f"every request survives a zero body: {'OK' if _desk_ok else 'FAIL'}")
            ok &= _desk_ok
        finally:
            _gt["CHAR_STORE"] = _store_was
    finally:
        _co.HOST_CAP, _co.OFFICIAL, _co.COLISEUM, _co._COLISEUM = _sv

    # --- 7. the document survives a restart (fmo_coliseum, migration 2005)
    _doc = {"hosted": {"1000": dict(_a, id=1000, kind="hosted", host="h:a")},
            "next_id": 1001, "day": 5, "hosted_today": 1,
            "records": {"1": [["Lex.Arden", 4]]}}
    if _co.write_state(_doc):
        _c3 = _co.Coliseum()
        _db_ok = (_c3.present and list(_c3.hosted) == [1000]
                  and _c3.hosted[1000]["name"] == "Iron Cup" and _c3.next_id == 1001
                  and _c3.hosted_today == 1 and _c3.records == {"1": [["Lex.Arden", 4]]}
                  and _c3.entries == {} and _co.read_state() == _doc)
        print(f"  coliseum: hosted arenas, the day's count and the records are one "
              f"fmo_coliseum document a fresh registry loads back: "
              f"{'OK' if _db_ok else 'FAIL'}")
        ok &= _db_ok
    else:
        print("  coliseum: fmo_coliseum round trip: SKIP (no test database)")

    # --- 8. off by default
    _off_ok = (bool(os.environ.get("FMO_COLISEUM", "").strip() not in ("", "0"))
               == _co.COLISEUM)
    print(f"  coliseum: FMO_COLISEUM is off unless the env sets it (the desks keep the "
          f"zero stubs): {'OK' if _off_ok else 'FAIL'}")
    ok &= _off_ok
    return ok


def _coliseum_match_pins():
    """THE ARENA MATCHES (coliseum.py, 2026-10-01), no client: the 0x014E a
    member is sent with, pairing (online only, first come), judging (a wiped
    team, the time limit, a no-show), the streak and 0x01BB, a tournament's
    bye and crown, team hostility over nation, and a session through 0x014D
    and its verdict. Every global it touches is restored."""
    import types as _ty
    from . import coliseum as _co
    ok = True
    T0 = 1_800_000_000.0
    _sv = (_co.OFFICIAL, _co.COLISEUM, _co._COLISEUM)
    _sv_deaths = dict(battleend.PILOT_DEATHS)
    try:
        _co.OFFICIAL = _co.parse_official("1:4:30001,2:4:30021")
        col = _co.Coliseum(load=False)
        col.save = lambda: True
        _co._COLISEUM = col
        o1 = col.find(1)

        # --- 1. the push that sends a member in
        _mn = _co.arena_mapno(o1)
        _b = _co.sortie_push_body(o1, _mn, 1)
        _bb = sortiepush.R14E_BLOCK
        _push_ok = (_mn == 86 and _b is not None and len(_b) == sortiepush.REPLY_014E_LEN
                    and struct.unpack_from("<I", _b, _bb)[0] == 86
                    and struct.unpack_from("<I", _b, _bb + 0x6C)[0] == 6
                    and struct.unpack_from("<I", _b, _bb + missionblock.MB_TIMELIMIT)[0]
                    == _co.MATCH_TIME
                    and _b[_bb + missionblock.MB_BATTLE_SIDE] == 1
                    and struct.unpack_from("<I", _b, sortiepush.R14E_TIME)[0] == _co.COUNTDOWN
                    and battleend.battle_end_body(next_battle=True)[0x104 + 0x8D] == 1
                    and battleend.battle_end_body()[0x104 + 0x8D] == 0)
        print(f"  arena match: 0x014E for tile 30001 = battle map 86, block +0x6C = 6 "
              f"(lobby state 0xD), +0x4C = {_co.MATCH_TIME}s, +0xC55 = the team; 0x014C "
              f"+0x8D only for the streak's winner: {'OK' if _push_ok else 'FAIL'}")
        ok &= _push_ok

        # --- 2. pairing: online only, first come; both teams told
        on = lambda a: a != "m:off"
        for a in ("m:a", "m:b", "m:c", "m:off"):
            col.register(o1, a, [a], a.upper(), None, T0)
        _m1 = col.pair(T0, on)
        _na, _nb = col.take_notices("m:a"), col.take_notices("m:b")
        _left = [e["leader"] for e in col.entries[1]]
        m = _m1[0] if _m1 else None
        _pair_ok = (len(_m1) == 1 and m["sides"] == {"m:a": 0, "m:b": 1}
                    and [x[0] for x in _na + _nb] == [sortiepush.MSG_SORTIE_PUSH] * 2
                    and _na[0][3] == {"match": m["id"], "mapno": 86}
                    and _na[0][1][_bb + missionblock.MB_BATTLE_SIDE] == 0
                    and _nb[0][1][_bb + missionblock.MB_BATTLE_SIDE] == 1
                    and _left == ["m:c", "m:off"]
                    and _co.match_key("m:a") == _co.match_key("m:b") == m["id"]
                    and _co.match_key("m:c") is None
                    and _co.side_for_key("m:b") == 1
                    and popnation.battle_side_for("m:b")[0] == 1)
        print(f"  arena match: the first two online entries are paired, each member "
              f"queued a 0x014E with its own team, the rest wait (an offline pilot is "
              f"never paired); the team is the pop side: {'OK' if _pair_ok else 'FAIL'}")
        ok &= _pair_ok

        # --- 3. judging a wipe-out, the verdicts, the streak, 0x01BB
        _j0 = col.judge(m, T0 + 20, on, {})
        m["go"].update({"m:a": T0 + 12, "m:b": T0 + 12})
        _j1 = col.judge(m, T0 + 60, on, {"m:b": T0 - 5})       # an older death
        _done = col.tick_matches(T0 + 60, on, {"m:b": T0 + 50})
        _va, _vb = col.take_verdict("m:a"), col.take_verdict("m:b")
        _rq = col.requeue("m:a", T0 + 70)
        col.tick_matches(T0 + 72, on, {})
        _wipe_ok = (_j0 is False and _j1 is False and len(_done) == 1
                    and _va["won"] and _va["next"] and _va["streak"] == 1
                    and not _vb["won"] and not _vb["next"]
                    and col.records["1"] == [["M:A", 1]]
                    and "m:b" not in col.entry_of and _co.match_key("m:a") is None
                    and _rq is not None and col.entries[1][-1]["leader"] == "m:a"
                    and col.requeue("m:a", T0 + 71) is None
                    and m["id"] not in col.matches)
        print(f"  arena match: still gathering = undecided, a death from before the "
              f"match ignored; a wiped team loses, the winner's +0x8D streak 1 is "
              f"recorded and 0x01BB puts it back in the queue: "
              f"{'OK' if _wipe_ok else 'FAIL'}")
        ok &= _wipe_ok

        # --- 4. the time limit, a draw; a no-show
        col.entries[1].clear(); col.entry_of.clear(); col.returning.clear()
        for a in ("t:a", "t:b"):
            col.register(o1, a, [a], a, None, T0)
        mt = col.pair(T0, on)[0]
        mt["go"].update({"t:a": T0 + 10, "t:b": T0 + 10})
        _tl0 = col.judge(mt, T0 + 10 + _co.MATCH_TIME - 1, on, {})
        _tl1 = col.judge(mt, T0 + 10 + _co.MATCH_TIME, on, {})
        for a in ("n:a", "n:b"):
            col.register(o1, a, [a], a, None, T0)
        mn = col.pair(T0, on)[0]
        mn["go"]["n:a"] = T0 + 10
        _ns0 = col.judge(mn, T0 + _co.COUNTDOWN + _co.GO_WAIT, on, {})
        _ns1 = col.judge(mn, T0 + _co.COUNTDOWN + _co.GO_WAIT + 1, on, {})
        _gone = col.judge(mn, T0 + 30, lambda a: a != "n:a", {})
        _tl_ok = (_tl0 is False and _tl1[0] is None and "draw" in _tl1[1]
                  and _ns0 is False and _ns1[0] == 0 and _gone[0] == 1)
        print(f"  arena match: at the time limit equal standing is a draw; a pilot who "
              f"never sortied within {_co.GO_WAIT}s, or dropped, is out: "
              f"{'OK' if _tl_ok else 'FAIL'}")
        ok &= _tl_ok

        # --- 5. a tournament: a bye, two rounds, the crown
        _t = col.host("tt:h", {"first": "Tee"}, {"name": "Cup", "headcount": 1,
                                                 "bg_cost": 4, "format": 2, "req_bgs": 4,
                                                 "tile": 30001}, T0 - 700)
        for a in ("k:a", "k:b", "k:c"):
            col.register(_t, a, [a], a, None, T0)
        _r1 = [x for x in col.pair(T0, on) if x["arena"] == _t["id"]]
        _bye = [e["leader"] for e in col.entries[_t["id"]] if e.get("rounds") == 1]
        col.finish(_r1[0], 0, "test", T0 + 100)
        _r2 = [x for x in col.pair(T0 + 101, on) if x["arena"] == _t["id"]]
        col.finish(_r2[0], 1, "test", T0 + 200)
        col.pair(T0 + 201, on)
        _champ = _r2[0]["teams"][1]["leader"]
        _won = col.take_notices(_champ)
        _tour_ok = (len(_r1) == 1 and _bye == ["k:c"] and len(_r2) == 1
                    and {e["leader"] for e in _r2[0]["teams"]} == {"k:a", "k:c"}
                    and [struct.unpack_from("<i", x[1], 0)[0] for x in _won
                         if x[0] == _co.MSG_CANCEL_UPDATE] == [_co.CANCEL_WON]
                    and col.hosted[_t["id"]]["end"] == int(T0 + 201)
                    and not col.entries.get(_t["id"]))
        print(f"  arena tournament: an odd entrant gets a bye, round winners meet, the "
              f"last BG standing gets 0x01BA -10 (87:8 / 88:33) and the arena closes: "
              f"{'OK' if _tour_ok else 'FAIL'}")
        ok &= _tour_ok

        # --- 6. hostility is the team, not the nation; rooms keep matches apart
        col.entries.clear(); col.entry_of.clear(); col.matches.clear(); col.match_of.clear()
        for a in ("h:a", "h:b", "h:c", "h:d"):
            col.register(o1, a, [a], a, None, T0)
        col.pair(T0, on)
        _ch = lambda a: _ty.SimpleNamespace(account=a, addr=("198.51.100.9", 1),
                                            key=b"x%battle", pop_args={"nation": 1})
        _host_ok = (rooms.battle_room_hostile(_ch("h:a"), _ch("h:b"))
                    and not rooms.battle_room_hostile(_ch("h:a"), _ch("h:a"))
                    and not rooms.battle_room_hostile(_ch("x:1"), _ch("x:2"))
                    and _co.match_key("h:a") == _co.match_key("h:b")
                    != _co.match_key("h:c"))
        print(f"  arena match: two pilots of ONE nation on opposite teams are enemies, "
              f"outside a match nation decides; two matches are two rooms: "
              f"{'OK' if _host_ok else 'FAIL'}")
        ok &= _host_ok

        # --- 7. a session: 0x014D for the match, then the verdict's 0x014C
        _co.COLISEUM = True
        _gt = flat_globals()
        _store_was = _gt["CHAR_STORE"]
        _gt["CHAR_STORE"] = "selftest"
        try:
            mh = col.matches[col.match_of["h:a"]]
            _c = {"id": 1, "first": "H", "last": "A", "money": 100}
            s = session.Session("198.51.100.91:1")
            s._account = "h:a"
            s.playing_char = lambda: _c
            s.commit = lambda what: None
            s.arena_sortie = {"match": mh["id"], "mapno": mh["mapno"]}
            _go = [packet.parse(x) for x in s.on_packet(packet.parse(
                packet.build(sortiepush.MSG_SORTIE_GO, b"", 0x300)))]
            _st = referee.BATTLE_STATE.get(s.battle_key()) or {}
            _in = s.in_arena_match()
            col.finish(mh, 0, "test", time.time())
            _end = [packet.parse(x) for x in s.arena_end_due(1)]
            _be = [x for x in _end if x["msg"] == battleend.MSG_BATTLE_END]
            _again = s.arena_end_due(1)
            s.arena_sortie = {"match": 9999, "mapno": 86}
            _stale = [packet.parse(x) for x in s.on_packet(packet.parse(
                packet.build(sortiepush.MSG_SORTIE_GO, b"", 0x301)))]
            _sess_ok = ([x["msg"] for x in _go] == [handshake.MSG_SESSION_START]
                        and "h:a" in mh["go"] and _st.get("arena_match") == mh["id"]
                        and rooms.SORTIE_MAP.get(s.battle_key()) == 86 and _in
                        and len(_be) == 1
                        and struct.unpack_from("<I", _be[0]["payload"], 0x108)[0] == 2
                        and _be[0]["payload"][0x104 + 0x8D] == 1
                        and s.battle_end_done and not s.in_arena_match() and _again == []
                        and [x["msg"] for x in _stale] == [charselect.MSG_FAIL]
                        and s.battle_settlement["contribution"] == 0
                        and settlement.arena_exp_rows([(3, 40), (1, 1), (2, 0)], 50)
                        == [(3, 20), (1, 1), (2, 0)])
            print(f"  arena match via the session: 0x014D for the match -> message 1 with "
                  f"the 0x013A bookkeeping (battle state, SORTIE_MAP); the verdict ends "
                  f"the battle once (0x014C WON, +0x8D set) with no contribution and arena "
                  f"exp; a stale match's 0x014D is refused (8:29): "
                  f"{'OK' if _sess_ok else 'FAIL'}")
            ok &= _sess_ok
        finally:
            _gt["CHAR_STORE"] = _store_was
            rooms.SORTIE_MAP.pop("198.51.100.91", None)
            rooms.SORTIE_MAP.pop("h:a", None)
            referee.BATTLE_STATE.pop("198.51.100.91", None)
            referee.BATTLE_STATE.pop("h:a", None)
    finally:
        _co.OFFICIAL, _co.COLISEUM, _co._COLISEUM = _sv
        battleend.PILOT_DEATHS.clear()
        battleend.PILOT_DEATHS.update(_sv_deaths)
    return ok


def _coliseum_bar_pins():
    """Change Room inside the Coliseum zones takes Room to the arena's bar
    (map 124, the 2006-07-25 eatery); every other zone and kind is unchanged."""
    from . import move as _mv
    ok = True
    if not os.environ.get("FMO_ROOM_MAPS_COLISEUM", "").strip():
        ok &= _mv.ROOM_MAPS_COLISEUM == {1: 124}
        ok &= _mv.place_map(600, 1)[0] == 124 and _mv.place_map(607, 1, nation=2)[0] == 124
        ok &= _mv.place_map(600, 3)[0] == _mv.ROOM_MAPS[3]
        ok &= _mv.place_map(200, 1)[0] == _mv.ROOM_MAPS[1]
        ok &= _mv.place_map(608, 1)[0] == _mv.ROOM_MAPS[1]
    print(f"  coliseum bar: Room in zones 600..607 is map 124, other zones/kinds unchanged: "
          f"{'OK' if ok else 'FAIL'}")
    return ok


def _battle_end_title_pins():
    """THE BATTLE-END TITLE AND THE EXP GAIN WINDOW (2026-10-07, static). The
    client has ONE end banner per verdict, picked only by the 0x014C won test
    at 0x6117E5CA (+0x144 own side == 1 ? +0x108 == 1 : +0x108 == 2): won ->
    OBJECTIVES COMPLETE / VICTORY, else OBJECTIVES FAILED / DEFEAT, for a
    sortie and an arena alike, and a draw can only be a loss. +0x10C = 3
    skips the EXP Gain window (0x61176FD7); a battle that paid nothing sets it,
    one that paid anything leaves it 0 so the window shows (retail did)."""
    import inspect as _in
    from . import coliseum as _co, settlement as _st

    def client_won(body):            # 0x6117E5CA, transcribed
        side = body[battleend.S14C_SURV_IDX]
        res = struct.unpack_from("<I", body, battleend.S14C_RESULT)[0]
        return res == 1 if side == 1 else res == 2

    def screen_skipped(body):        # 0x61176FD7: bits 0 and 1 of block+8
        f = body[battleend.S14C_FLAGS]
        return bool(f & 1) and bool(f & 2)

    w = battleend.battle_end_body(won=True, exp_rows=[(3, 10)])
    l = battleend.battle_end_body(won=False, exp_rows=[(3, 10)])
    e = battleend.battle_end_body(won=False, no_screen=True)
    ok = (client_won(w) and not client_won(l) and not client_won(e)
          and not screen_skipped(w) and not screen_skipped(l)
          and screen_skipped(e) and e[battleend.S14C_FLAGS] == 3
          and battleend.S14C_FLAGS - battleend.S14C_BLOCK == 8)
    src_st = _in.getsource(_st.SessionSettlement.battle_end_push)
    src_co = _in.getsource(_co)
    ok = (ok and "no_screen=_empty" in src_st and "not rows and new == old" in src_st
          and "no_screen=True" in src_co)
    print(f"  0x014C title: won -> VICTORY, lost/draw -> DEFEAT (one banner for "
          f"sortie and arena); +0x10C = 3 only when nothing was paid (spectator, "
          f"zero-pay end), else the EXP Gain window shows: {'OK' if ok else 'FAIL'}")
    return ok


def _coliseum_spectate_pins():
    """COLISEUM SPECTATING (coliseum.py, 2026-10-01), no client: the 0x01C1 a
    spectator gets, Delacroix's list, the seats and refusals, the end of a
    watched match, and the receive-only rules (no chat, no relay out, no kill
    credit, one copy of each fighter's stream). Globals restored."""
    import types as _ty
    from . import coliseum as _co
    ok = True
    T0 = 1_800_000_000.0
    _sv = (_co.OFFICIAL, _co.COLISEUM, _co._COLISEUM, _co.SPECTATE_SLOTS,
           rooms.room_mates)
    try:
        _co.OFFICIAL = _co.parse_official("1:4:30001")
        col = _co.Coliseum(load=False)
        col.save = lambda: True
        _co._COLISEUM = col
        o1 = col.find(1)

        # --- 1. the reply is the fighters' 0x014E, cut to the 0x01C1 parse
        _pb = _co.sortie_push_body(o1, 86, 0)
        _sb = _co.spectate_body(_pb)
        _body_ok = (len(_sb) == 3428 == lobapi.LOBAPI[_co.MSG_SPECTATE_REQ][1]
                    and lobapi.LOBAPI[_co.MSG_SPECTATE_REQ][0] == _co.MSG_SPECTATE_REPLY
                    and struct.unpack_from("<i", _sb, 0)[0] == 0
                    and _sb[4:24] == _pb[sortiepush.R14E_ENDPOINT:sortiepush.R14E_ENDPOINT + 20]
                    and _sb[0x18:0x18 + 3400] == _pb[sortiepush.R14E_BLOCK:sortiepush.R14E_BLOCK + 3400]
                    and struct.unpack_from("<I", _sb, 0x18)[0] == 86
                    and struct.unpack_from("<I", _sb, 0x18 + 0x6C)[0] == 6
                    and struct.unpack_from("<I", _sb, 0xD60)[0] == _co.COUNTDOWN)
        print(f"  spectate: 0x01C1 = status, the fighters' endpoint (+0x04), block "
              f"(+0x18: map 86, kind 6) and time (+0xD60, the same seed): "
              f"{'OK' if _body_ok else 'FAIL'}")
        ok &= _body_ok

        # --- 1b. the endpoint is per client (live 10-06: the bare tailnet host
        # sent a public pilot nowhere): a global peer gets POL_ADVERTISE_PUBLIC,
        # a tailnet peer the BATTLE_HOST, and nothing else in the body moves
        _sv_pub = os.environ.get("POL_ADVERTISE_PUBLIC")
        try:
            os.environ["POL_ADVERTISE_PUBLIC"] = "203.0.113.9"
            _epf = addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint
            _e = sortiepush.R14E_ENDPOINT
            _pub, _hp = _co.endpoint_for_peer(_pb, _e, "8.8.4.4")
            _tn, _ht = _co.endpoint_for_peer(_pb, _e, "198.18.0.2")
            _pt = sortie.SORTIE_PORT or addressing.BATTLE_PORT
            _ep_ok = (not sortie.SORTIE_ENDPOINT) or (
                _hp == "203.0.113.9" and _pub[_e:_e + 20] == _epf("203.0.113.9", _pt)[:20]
                and _ht == (sortie.SORTIE_HOST or addressing.BATTLE_HOST)
                and _pub[:_e] == _pb[:_e] and _pub[_e + 20:] == _pb[_e + 20:]
                and len(_pub) == len(_pb) == len(_tn))
        finally:
            if _sv_pub is None:
                os.environ.pop("POL_ADVERTISE_PUBLIC", None)
            else:
                os.environ["POL_ADVERTISE_PUBLIC"] = _sv_pub
        print(f"  arena endpoint per client: a public pilot's 0x014E names the public "
              f"host, a tailnet pilot's the battle host, the rest unchanged: "
              f"{'OK' if _ep_ok else 'FAIL'} ({_hp}, {_ht})")
        ok &= _ep_ok

        # --- 2. Delacroix's list, the seats, the refusals
        on = lambda a: True
        for a in ("f:a", "f:b"):
            col.register(o1, a, [a], a, None, T0)
        m = col.pair(T0, on)[0]
        _l0 = col.list_arenas(1, T0)
        _late0 = col.spectate("w:0", 1, T0)[0]
        m["go"]["f:a"] = T0 + 12
        _l1 = [a["id"] for a in col.list_arenas(1, T0)]
        _co.SPECTATE_SLOTS = 2
        _s1, _s2, _s3 = (col.spectate(w, 1, T0)[0] for w in ("w:1", "w:2", "w:3"))
        _none = col.spectate("w:4", 99, T0)[0]
        col.leave_spectate("w:2")
        _s3b = col.spectate("w:3", 1, T0)[0]
        _seat_ok = (_l0 == [] and _late0 == _co.SPECTATE_LATE and _l1 == [1]
                    and (_s1, _s2, _s3) == (0, 0, _co.SPECTATE_FULL)
                    and _none == _co.SPECTATE_LATE and _s3b == 0
                    and m["spectators"] == {"w:1", "w:3"}
                    and _co.match_key("w:1") == m["id"] == _co.match_key("f:a")
                    and _co.side_for_key("w:1") is None)
        print(f"  spectate: Delacroix lists only arenas with a battle under way; a "
              f"seat each up to FMO_COLISEUM_SPECTATORS, then 6:2; no battle = 6:1; "
              f"a spectator shares the match's room but has no team: "
              f"{'OK' if _seat_ok else 'FAIL'}")
        ok &= _seat_ok

        # --- 3. the receive-only rules
        _mk = lambda a, ip: _ty.SimpleNamespace(
            account=a, addr=(ip, 19155), key=b"x%battle", pending=[], remotes={},
            peer_key=(ip, 19155), alias_for=lambda addr: 0x40 + addr[0].count("1"),
            pop_args={"nation": 1}, last_fire=time.time())
        fa, fb, w1 = (_mk("f:a", "198.51.100.1"), _mk("f:b", "198.51.100.2"),
                      _mk("w:1", "198.51.100.3"))
        _rm = {id(fa): [fb, w1], id(fb): [fa, w1], id(w1): [fa, fb]}
        rooms.room_mates = lambda c: _rm[id(c)]
        _rss = _ty.SimpleNamespace(popped=True, pending=[])
        w1.remotes[w1.alias_for(fa.peer_key)] = _rss
        peerlink.spectator_copies(fa, 23, bytearray(28), True, 0, 0x41)
        referee._relay_battle_record(w1, w1.addr, 29, bytes(0x24), alias_stream=False)
        _chat_w = datagram.chat_route(w1, datagram.CHAT_ROOM_KINDS[0])
        _chat_f = datagram.chat_route(fa, datagram.CHAT_ROOM_KINDS[0])
        _rule_ok = (len(_rss.pending) == 1
                    and struct.unpack_from("<I", _rss.pending[0], 8)[0] == w1.alias_for(fa.peer_key)
                    and w1.pending == [] and fb.pending == []
                    and _co.spectators_of_chan(fa) == [w1]
                    and _co.spectator_feed(fa, fb) and not _co.spectator_feed(fa, w1)
                    and rooms.battle_room_killer(fb, time.time(), mates=[w1]) is None
                    and _chat_w is None and _chat_f is not None and w1 not in _chat_f[0])
        print(f"  spectate: a fighter's own-unit record reaches its spectator once, "
              f"under the spectator's alias; a spectator's records, chat and shots go "
              f"nowhere and earn no kill: {'OK' if _rule_ok else 'FAIL'}")
        ok &= _rule_ok
        rooms.room_mates = _sv[4]

        # --- 4. the session: list, spectate, the match's end, refusals
        _co.COLISEUM = True
        _gt = flat_globals()
        _store_was = _gt["CHAR_STORE"]
        _gt["CHAR_STORE"] = "selftest"
        try:
            col.leave_spectate("w:1"), col.leave_spectate("w:3")
            _co.SPECTATE_SLOTS = 10
            s = session.Session("198.51.100.92:1")
            s._account = "w:5"
            _c = {"id": 1, "first": "Wat", "last": "Cher", "money": 50}
            s.playing_char = lambda: _c
            s.commit = lambda what: None
            _go = lambda mm, b=b"": [packet.parse(x) for x in s.on_packet(
                packet.parse(packet.build(mm, b, 0x400)))]
            _before = s.wants_spectate({"msg": _co.MSG_SPECTATE_REQ})
            _lst = _go(_co.MSG_LIST_REQ, b"\x01" + bytes(19))
            _sp = _go(_co.MSG_SPECTATE_REQ, struct.pack("<I", 1) + bytes(32))
            _in = s.is_spectating() and s.in_arena_match()
            col.finish(m, 0, "test", time.time())
            _end = [packet.parse(x) for x in s.arena_end_due(1)]
            _end2 = s.arena_end_due(1)
            groupchannel.GROUP_OF["w:6"] = 7777
            groupchannel.GROUP_MEMBERS[7777] = ["w:6"]
            s2 = session.Session("198.51.100.93:1")
            s2._account = "w:6"
            s2.col_watch_list_at = time.time()
            _grp = [packet.parse(x) for x in s2.on_packet(packet.parse(
                packet.build(_co.MSG_SPECTATE_REQ, struct.pack("<I", 1) + bytes(32), 0x401)))]
            _sess_ok = (not _before and struct.unpack_from("<I", _lst[0]["payload"], 0x10)[0] == 1
                        and [x["msg"] for x in _sp] == [_co.MSG_SPECTATE_REPLY]
                        and len(_sp[0]["payload"]) == 3428 and _in
                        and (referee.BATTLE_STATE.get(s.battle_key()) or {}).get("arena_spectate") == m["id"]
                        and [x["msg"] for x in _end] == [battleend.MSG_BATTLE_END]
                        and struct.unpack_from("<I", _end[0]["payload"], 0x108)[0] == 0
                        and s.battle_settlement is None and _end2 == []
                        and not s.is_spectating()
                        and [(x["msg"], x["conn"]) for x in _grp]
                        == [(charselect.MSG_FAIL, _co.SPECTATE_IN_GROUP & 0xFFFF)])
            print(f"  spectate via the session: a 0x01C0 is Delacroix's only after his "
                  f"list; it answers 0x01C1 and seats the pilot; the match's end gives the "
                  f"spectator one bare 0x014C, no pay; a pilot in a battle group gets "
                  f"2:127: {'OK' if _sess_ok else 'FAIL'}")
            ok &= _sess_ok
        finally:
            _gt["CHAR_STORE"] = _store_was
            groupchannel.GROUP_OF.pop("w:6", None)
            groupchannel.GROUP_MEMBERS.pop(7777, None)
            for k in ("198.51.100.92", "w:5"):
                rooms.SORTIE_MAP.pop(k, None)
                referee.BATTLE_STATE.pop(k, None)
    finally:
        (_co.OFFICIAL, _co.COLISEUM, _co._COLISEUM, _co.SPECTATE_SLOTS,
         rooms.room_mates) = _sv
    return ok


def _mission_group_fee_pins():
    """MISSION GROUPS AND THE BATTLE FEE RATES (2026-10-01; Playing Manual
    pp.61-62): the issuer of a derived mission leads a mission group and its
    takers are members (0x0174 type 3 / type 2), their connections are told
    apart from the battle group's by kind, /mgl and /mgm reach only their
    group, and FMO_BATTLE_FEE_RATES fills 0x019F +0x18..+0x37. One line per
    pin; every knob is restored."""
    import types as _ty
    ok = True
    _rec_cmd = lambda r: struct.unpack_from("<I", r, 4)[0]
    _iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    _now = time.time()

    # --- 1. the 0x0174 type byte is +0x18; the attach is the live type-0 one
    _b3 = grouplogin.group_attach_body(0x4D470001, "127.0.0.1", 61300, gtype=3)
    _b2 = grouplogin.group_attach_body(0x4D470001, "127.0.0.1", 61300, gtype=2)
    _b0 = grouplogin.group_attach_body(0x4D470001, "127.0.0.1", 61300, gtype=0)
    _wire_ok = (_b3[grouplogin.G174_TYPE] == 3 and _b2[grouplogin.G174_TYPE] == 2
                and _b3[:grouplogin.G174_TYPE] == _b0[:grouplogin.G174_TYPE]
                and struct.unpack_from("<I", _b3, 0)[0] == 0x4D470001
                and missiongroups.MG_KIND == {2: 6, 3: 5}
                and missiongroups.MG_CHAT == {3: 7, 2: 8})
    print(f"  mission group: 0x0174 +0x18 = type 3 (leader, slot 0x613CA400, kind 5, "
          f"/mgl ch 7) / 2 (member, 0x613CA3FC, kind 6, /mgm ch 8), the rest the "
          f"type-0 attach: {'OK' if _wire_ok else 'FAIL'}")
    ok &= _wire_ok

    # --- 2. who is in which group, from the orders
    L, M, N, X, Z = "mg:lead", "mg:mem", "mg:mem2", "mg:none", "mg:done"
    _o = lambda did, **k: dict({"id": did, "key": did, "derived": did, "name": "Hold",
                                "status": "ordered", "cat": 1, "at": _iso(_now - 60),
                                "limit": 1800}, **k)
    _acc = lambda did, **k: dict({"id": did, "name": "Hold", "cat": 1,
                                  "at": _iso(_now - 30)}, **k)
    _ros = [(L, [{"id": 1, "missions": [_o(0xFD01), _o(0xFD02), _o(0xFD03), _o(0xFD05)]}]),
            (M, [{"id": 1, "first": "Mo", "last": "Em", "missions": [_acc(0xFD01)]}]),
            (N, [{"id": 1, "first": "Ni", "last": "En", "missions": [_acc(0xFD05)]}]),
            (Z, [{"id": 1, "first": "Zo", "last": "Ed",
                  "missions": [_acc(0xFD03, status="complete")]}]),
            (X, [{"id": 1, "missions": [_o(0xFD04, status="cancelled")]}])]
    _sv_reg = (dict(missionbook.ORDERS), dict(missionbook.ORDER_TAKEN),
               dict(missionbook.ORDER_TAKER_NAME), list(missionbook._orders_loaded))
    _sv_mg = (missiongroups.MISSION_GROUP, dict(missiongroups.MG_IDS),
              dict(missiongroups.MG_GROUPS), dict(missiongroups._entries))
    _sv_g = (dict(groupchannel.GROUP_MEMBERS), dict(groupchannel.GROUP_OF),
             dict(battlegroups.GROUP_CREATOR_ACCOUNT), dict(groupchannel._joined_at))
    _was_peers = dict(groupchannel.WORLD_PEERS)
    _grp_ok = _att_ok = _bg_ok = _udp_ok = _chat_ok = False
    try:
        missionbook.ORDERS.clear()
        missionbook.ORDER_TAKEN.clear()
        missionbook.ORDER_TAKER_NAME.clear()
        missiongroups.MG_IDS.clear()
        missiongroups._entries.clear()
        _groups = missiongroups.mission_groups(_now, _ros)
        gid = missiongroups.MG_IDS.get(L)
        _grp_ok = (gid is not None and _groups == {gid: (L, [M, N])}
                   and missiongroups.wanted(L, _groups) == {3: gid}
                   and missiongroups.wanted(M, _groups) == {2: gid}
                   and missiongroups.wanted(Z, _groups) == {}
                   and missiongroups.wanted(X, _groups) == {})
        print(f"  mission group: the issuer leads, takers with an open accept are "
              f"members; a taker whose accept is complete and an issuer whose "
              f"order was cancelled are in none: {'OK' if _grp_ok else 'FAIL'}")

        # --- 3. the keepalive attaches each side once; knob off = nothing
        def _sess(acct, ip):
            return _ty.SimpleNamespace(account=acct, ip=ip, peer=f"[{ip}]")
        sL, sM = _sess(L, "198.51.100.71"), _sess(M, "198.51.100.72")
        missiongroups.MISSION_GROUP = False
        _off = missiongroups.mission_attach_due(sL, 1, _now, _ros, force=True)
        missiongroups.MISSION_GROUP = True
        _pL = [packet.parse(x) for x in missiongroups.mission_attach_due(sL, 1, _now, _ros, force=True)]
        _pL2 = missiongroups.mission_attach_due(sL, 1, _now, _ros, force=True)
        _pM = [packet.parse(x) for x in missiongroups.mission_attach_due(sM, 1, _now, _ros, force=True)]
        _att_ok = (_off == [] and len(_pL) == 1 and _pL2 == [] and len(_pM) == 1
                   and _pL[0]["msg"] == grouplogin.MSG_GROUP_ATTACH
                   and _pL[0]["payload"][grouplogin.G174_TYPE] == 3
                   and _pM[0]["payload"][grouplogin.G174_TYPE] == 2
                   and struct.unpack_from("<I", _pL[0]["payload"], 0)[0] == gid
                   and struct.unpack_from("<I", _pM[0]["payload"], 0)[0] == gid)
        print(f"  mission group: the leader gets 0x0174 type 3 and a taker type 2, "
              f"same GroupID, once each; FMO_MISSION_GROUP=0 sends none: "
              f"{'OK' if _att_ok else 'FAIL'}")

        # --- 4. a battle-group JOINER is not given the leader attach (both kind 5)
        groupchannel.GROUP_MEMBERS[9901] = ["mg:bgl", L]
        groupchannel.GROUP_OF[L] = 9901
        battlegroups.GROUP_CREATOR_ACCOUNT[9901] = "mg:bgl"
        groupchannel._joined_at[L] = time.monotonic()
        sL2 = _sess(L, "198.51.100.73")
        _held = missiongroups.mission_attach_due(sL2, 1, _now, _ros, force=True)
        battlegroups.GROUP_CREATOR_ACCOUNT[9901] = L        # the creator holds kind 1
        _sL3 = _sess(L, "198.51.100.74")
        _given = missiongroups.mission_attach_due(_sL3, 1, _now, _ros, force=True)
        _bg_ok = _held == [] and len(_given) == 1
        groupchannel.GROUP_MEMBERS.pop(9901, None)
        groupchannel.GROUP_OF.pop(L, None)
        battlegroups.GROUP_CREATOR_ACCOUNT.pop(9901, None)
        print(f"  mission group: the leader attach waits while the pilot is in a "
              f"battle group it did not create (manager id 5 twice); its creator "
              f"gets it: {'OK' if _bg_ok else 'FAIL'}")

        # --- 5. the datagram path: kind 6 / kind 5 open mission channels
        _sent = []

        class _Sink:
            def sendto(self, d, to):
                _sent.append((d, to))
        missiongroups._entries.clear()
        missiongroups.queue_entry("198.51.100.72", 2, M, gid)
        missiongroups.queue_entry("198.51.100.71", 3, L, gid)
        _aM, _aL, _aB = ("198.51.100.72", 19155), ("198.51.100.71", 19155), ("198.51.100.75", 19155)
        _T = groupchannel.GROUP_TABLES

        def _dg(kind, body=b"", to=0):
            return fmoworld.build(*_T, peer=0x1001, hid=0, kind=kind, ack=0, flag=0,
                                  frm=0, to=to, body=body)
        datagram._serve_datagram(_Sink(), groupchannel.WORLD_PEERS, _dg(6), _aM)
        _mchan = groupchannel.WORLD_PEERS.get(_aM + ("mgroup2",))
        _mreply = fmoworld.parse(*_T, _sent[-1][0]) if _sent else None
        _mblob = [r for r in (_mchan.pending if _mchan else [])
                  if _rec_cmd(r) == groupchannel.GROUP_POP_CMD]
        datagram._serve_datagram(_Sink(), groupchannel.WORLD_PEERS, _dg(5), _aB)   # no attach
        _udp_ok = (_mchan is not None and _mchan.account == M and _mchan.mission_gid == gid
                   and _mchan.mission_type == 2 and _mreply is not None and _mreply["hid"] == 6
                   and len(_mblob) == 1
                   and struct.unpack_from("<I", _mblob[0], fmoworld.REC_HDR
                                          + groupchannel.GROUP_POP_LEADER_OFF)[0] == 0
                   and (_aM + ("group",)) not in groupchannel.WORLD_PEERS
                   and (_aB + ("group",)) in groupchannel.WORLD_PEERS
                   and (_aB + ("mgroup3",)) not in groupchannel.WORLD_PEERS)
        print(f"  mission group: a kind-6 datagram after a type-2 attach is the "
              f"member's own channel (hid 6 back, member blob with +0x50 = 0, no "
              f"battle-group channel); kind 5 with no attach stays the battle "
              f"group's: {'OK' if _udp_ok else 'FAIL'}")

        # --- 6. /mgl reaches the members only; /mgm the others and the leader
        _sub = bytearray(0x60)
        struct.pack_into("<I", _sub, fmoworld.SUBMIT_KIND, 7)
        _sub[fmoworld.SUBMIT_NAME1:fmoworld.SUBMIT_NAME1 + 2] = b"Le"
        _sub[fmoworld.SUBMIT_TEXT:fmoworld.SUBMIT_TEXT + 7] = b"form up"
        _n0 = len(_mchan.pending) if _mchan else 0
        datagram._serve_datagram(_Sink(), groupchannel.WORLD_PEERS,
                                 _dg(5, fmoworld.record(fmoworld.CMD_UNK115, bytes(_sub)), 1), _aL)
        _lchan = groupchannel.WORLD_PEERS.get(_aL + ("mgroup3",))
        _got = [r for r in (_mchan.pending[_n0:] if _mchan else [])
                if _rec_cmd(r) == fmoworld.CMD_CHAT]
        _lblob = [r for r in (_lchan.pending if _lchan else [])
                  if _rec_cmd(r) == groupchannel.GROUP_POP_CMD]
        _r8 = datagram.chat_route(_mchan, 8) if _mchan else None
        _chat_ok = (_lchan is not None and len(_got) == 1
                    and struct.unpack_from("<I", _got[0], fmoworld.REC_HDR)[0] == 7
                    and b"form up" in _got[0]
                    and len(_lblob) == 1
                    and struct.unpack_from("<I", _lblob[0], fmoworld.REC_HDR
                                           + groupchannel.GROUP_POP_LEADER_OFF)[0] == 1
                    and _r8 is not None and _r8[0] == [_lchan] and _r8[1] == 8
                    and datagram.chat_route(_mchan, 7) is None
                    and datagram.chat_route(_lchan, 8) is None
                    and datagram.chat_route(_lchan, 3) is None)
        print(f"  mission group: /mgl (ch 7) on the leader's connection reaches the "
              f"member as a ch-7 line, /mgm (ch 8) reaches the leader, each "
              f"connection refuses the other's and the battle group's kinds; the "
              f"leader's blob has +0x50 = 1: {'OK' if _chat_ok else 'FAIL'}")
    except Exception as _x:
        import traceback
        traceback.print_exc()
        print(f"    (raised {_x!r})")
    finally:
        missionbook.ORDERS.clear()
        missionbook.ORDERS.update(_sv_reg[0])
        missionbook.ORDER_TAKEN.clear()
        missionbook.ORDER_TAKEN.update(_sv_reg[1])
        missionbook.ORDER_TAKER_NAME.clear()
        missionbook.ORDER_TAKER_NAME.update(_sv_reg[2])
        missionbook._orders_loaded[:] = _sv_reg[3]
        missiongroups.MISSION_GROUP = _sv_mg[0]
        for d, w in zip((missiongroups.MG_IDS, missiongroups.MG_GROUPS,
                         missiongroups._entries), _sv_mg[1:]):
            d.clear()
            d.update(w)
        for d, w in zip((groupchannel.GROUP_MEMBERS, groupchannel.GROUP_OF,
                         battlegroups.GROUP_CREATOR_ACCOUNT, groupchannel._joined_at), _sv_g):
            d.clear()
            d.update(w)
        groupchannel.WORLD_PEERS.clear()
        groupchannel.WORLD_PEERS.update(_was_peers)
    ok &= _grp_ok and _att_ok and _bg_ok and _udp_ok and _chat_ok

    # --- 7. FMO_BATTLE_FEE_RATES: 0x019F +0x18..+0x37, zeros by default
    _r, _e = squadron.parse_battle_fee_rates("100, 200, 300, 400, 500, 600, 700, 800")
    _bad = squadron.parse_battle_fee_rates("1,2")
    _neg = squadron.parse_battle_fee_rates("1,2,3,4,5,6,7,-8")
    _pay = squadron.insignia_payload(1, rates=_r)
    _pay0 = squadron.insignia_payload(1, rates=(0,) * 8)
    _env_set = bool(os.environ.get("FMO_BATTLE_FEE_RATES", "").strip())
    _fee_ok = (_e is None and _r == (100, 200, 300, 400, 500, 600, 700, 800)
               and _bad[0] == (0,) * 8 and _bad[1] is not None
               and _neg[0] == (0,) * 8 and _neg[1] is not None
               and struct.unpack_from("<8I", _pay, squadron.BATTLE_FEE_RATES_OFF) == _r
               and _pay[:squadron.BATTLE_FEE_RATES_OFF] == _pay0[:squadron.BATTLE_FEE_RATES_OFF]
               and _pay[squadron.BATTLE_FEE_RATES_OFF + 32:] == _pay0[squadron.BATTLE_FEE_RATES_OFF + 32:]
               and _pay0[squadron.BATTLE_FEE_RATES_OFF:squadron.BATTLE_FEE_RATES_OFF + 32] == bytes(32)
               and squadron.BATTLE_FEE_RATES_OFF + 32 <= squadron.INSIGNIA_COUNT_OFF
               and squadron.battle_fee(12000, 3, 500) == 180
               and squadron.battle_fee(99999, 1, 1) == 0
               and (_env_set or squadron.BATTLE_FEE_RATES == (0,) * 8))
    print(f"  battle fee: FMO_BATTLE_FEE_RATES fills 0x019F +0x18..+0x37 (rate[type], "
          f"0x61175D3E), nothing else moves, default all zero; a bad list is "
          f"ignored; fee = base x level x rate / 100000 (0x611E1F60): "
          f"{'OK' if _fee_ok else 'FAIL'}")
    ok &= _fee_ok
    return ok


def _battle_spawn_pins():
    """PER-MAP BATTLE SPAWNS (battlepop.BATTLE_SPAWNS, fmo-battle-spawns.tsv)."""
    import math
    import tempfile
    import types as _ty
    ok = True

    def _apart(pts, d=12.0):
        return all(math.hypot(a[0] - b[0], a[2] - b[2]) >= d
                   for i, a in enumerate(pts) for b in pts[i + 1:])

    # (1) the loader: a written table round-trips; a malformed row is skipped;
    # a missing file is an empty table, never an import failure
    _tmp = os.path.join(tempfile.mkdtemp(prefix="fmo-spawns-"), "spawns.tsv")
    _a_sl = " ".join(f"{-96 + 20 * k}:-32" for k in range(12))
    _b_sl = " ".join(f"{112 - 20 * k}:96" for k in range(12))
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write("map\tax\taz\tbx\tbz\ty\ta_slots\tb_slots\tsource\tnotes\n")
        f.write(f"471\t-96\t-32\t112\t96\t28\t{_a_sl}\t{_b_sl}\tselftest\t\n")
        f.write("472\tx\t0\t0\t0\t0\t\t\tbad\t\n")
        f.write("473\t0\t-120\t0\t120\t30\t0:-120\t0:120 20:120\tshort\t\n")
    _rows = battlepop.load_battle_spawns(_tmp)
    _l_ok = (sorted(_rows) == [471, 473] and _rows[471]["a"] == (-96.0, -32.0)
             and len(_rows[471]["b_slots"]) == 12 and _rows[471]["y"] == 28.0
             and battlepop.load_battle_spawns(_tmp + ".absent") == {})
    print(f"  spawns: the table loads, a bad row is skipped, no file = no rows: "
          f"{'OK' if _l_ok else 'FAIL'}")
    ok &= _l_ok

    # (2) a pilot takes its own side's point (side 0 / unknown = A, 1 = B), a
    # second pilot of the side the next slot; a map without a row falls back
    _pa, _ = battlepop.battle_spawn_pos(471, 0, 0, rows=_rows)
    _pb, _ = battlepop.battle_spawn_pos(471, 1, 0, rows=_rows)
    _pn, _ = battlepop.battle_spawn_pos(471, None, 0, rows=_rows)
    _p1, _ = battlepop.battle_spawn_pos(471, 0, 1, rows=_rows)
    _p5, _ = battlepop.battle_spawn_pos(471, 0, 5, rows=_rows)
    _px, _why = battlepop.battle_spawn_pos(999, 0, 0, rows=_rows)
    _mates = [_ty.SimpleNamespace(spawn_side=0, spawn_slot=0),
              _ty.SimpleNamespace(spawn_side=1, spawn_slot=1),
              _ty.SimpleNamespace()]
    _p_ok = (_pa == (-96.0, 28.0, -32.0, 0.0) and _pb == (112.0, 28.0, 96.0, 0.0)
             and _pn == _pa and _p1 == (-76.0, 28.0, -32.0, 0.0) and _p5 == _p1
             and _px is None and "no fmo-battle-spawns.tsv row" in _why
             and battlepop.spawn_pilot_index(None, 0, _mates) == 1
             and battlepop.spawn_pilot_index(None, 1, _mates) == 0)
    print(f"  spawns: side A/B points, the next pilot slot, no row -> fallback: "
          f"{'OK' if _p_ok else 'FAIL'}")
    ok &= _p_ok

    # (3) the squad stands on the OTHER side, spread: no two within 12, all far
    # from the pilot; a row with too few slots still spreads them
    _sqa = battlepop.squad_spawn_positions(471, 0, 3, rows=_rows)
    _sqb = battlepop.squad_spawn_positions(471, 1, 8, rows=_rows)
    _sqs = battlepop.squad_spawn_positions(473, 0, 5, rows=_rows)
    _s_ok = (_sqa == [(32.0, 28.0, 96.0, 0.0), (12.0, 28.0, 96.0, 0.0), (-8.0, 28.0, 96.0, 0.0)]
             and _apart(_sqa) and len(_sqb) == 8 and _apart(_sqb)
             and all(p[2] == -32.0 for p in _sqb)
             and len(_sqs) == 5 and _apart(_sqs)
             and _sqs[0] == (20.0, 30.0, 120.0, 0.0)
             and all(math.hypot(p[0], p[2] + 120) >= 100 for p in _sqs)
             and _apart(_sqs[1:], battlepop.SPAWN_FALLBACK_GAP - 0.1)
             and battlepop.squad_spawn_positions(999, 0, 3, rows=_rows) is None)
    print(f"  spawns: the squad takes the other side's slots, >= 12 apart (a short "
          f"row uses its spare pilot slot, then a line {battlepop.SPAWN_FALLBACK_GAP:g} "
          f"apart): {'OK' if _s_ok else 'FAIL'}")
    ok &= _s_ok

    # (4) battle_squad_for places a NEW squad on the given positions, else the
    # old line off the drop point
    _sv = (dict(squad.BATTLE_SQUADS), {h: referee.BATTLE_STATE.get(h) for h in ("spA", "spB")})
    try:
        squad.BATTLE_SQUADS.clear()
        for _h in ("spA", "spB"):
            referee.battle_state(_h, reset=True)
        _cA = _ty.SimpleNamespace(addr=("spA", 1), key=b"%xbattle", last_fire=None)
        _cB = _ty.SimpleNamespace(addr=("spB", 1), key=b"%xbattle", last_fire=None)
        rooms.WORLD_MAPS["spA"], rooms.WORLD_MAPS["spB"] = 471, 418
        _q1, _ = squad.battle_squad_for(_cA, (0, 0, 0, 0), 2, [], mates=[], n=3,
                                        level=1, rows=[], positions=_sqa)
        _q2, _ = squad.battle_squad_for(_cB, (10.0, 5.0, 20.0, 0.0), 2, [], mates=[], n=3,
                                        level=1, rows=[])
        _b_ok = (_q1["pos"] == _sqa and _q1["pos_src"] == "spawn table"
                 and _q2["pos_src"] == "line off the drop point"
                 and _q2["pos"] == squad.squad_positions((10.0, 5.0, 20.0, 0.0), 3,
                                                         squad.BATTLE_ENEMIES[1], squad.squad_gap()))
    finally:
        squad.BATTLE_SQUADS.clear()
        squad.BATTLE_SQUADS.update(_sv[0])
        for _h, _st in _sv[1].items():
            if _st is None:
                referee.BATTLE_STATE.pop(_h, None)
            else:
                referee.BATTLE_STATE[_h] = _st
        rooms.WORLD_MAPS.pop("spA", None)
        rooms.WORLD_MAPS.pop("spB", None)
    print(f"  spawns: a new squad stands on the table's slots, without them on the "
          f"old line: {'OK' if _b_ok else 'FAIL'}")
    ok &= _b_ok

    # (5) WHY a table POP sends no side byte: body+0x27 is the z float's high
    # byte. z survives with side None and is destroyed by side 0 (the twin)
    _zb = fmoworld.record_pop(0x2222, unit_type=0, pos=(112.0, 28.0, 96.0, 0.0))
    _z0 = fmoworld.record_pop(0x2222, unit_type=0, pos=(112.0, 28.0, 96.0, 0.0), side=0)
    _zoff = fmoworld.REC_HDR + fmoworld.POP_POS + 8
    _z_ok = (fmoworld.POP_SIDE == fmoworld.POP_POS + 11
             and struct.unpack_from("<f", _zb, _zoff)[0] == 96.0
             and abs(struct.unpack_from("<f", _z0, _zoff)[0]) < 1e-30)
    print(f"  spawns: body+0x27 (side) is the z float's top byte -- z 96 survives "
          f"only without it: {'OK' if _z_ok else 'FAIL'}")
    ok &= _z_ok

    # (5b) THE SPLIT GUARD (2026-10-07): a battle POP (UnitType != 4) carries
    # its position as plain floats (0x611EB08F -> 0x6110F4B0, a 16-byte copy)
    # and may stand anywhere in the map box; a lobby POP (type 4, or
    # battle=False) keeps cmd 240's +/-327.67. Twins: the same far point is
    # refused for the lobby, and a point past the box for the battle.
    def _refused(**kw):
        try:
            fmoworld.record_pop(0x2222, **kw)
        except ValueError:
            return True
        return False
    _far = (1500.0, 344.0, -1900.0, 0.0)
    _fb = fmoworld.record_pop(0x2222, unit_type=0, pos=_far)
    _lim = fmoworld.POP_BATTLE_POS_MAX
    _g_ok = (struct.unpack_from("<4f", _fb, fmoworld.REC_HDR + fmoworld.POP_POS) == _far
             and fmoworld.pop_is_battle(0) and not fmoworld.pop_is_battle(4)
             and not _refused(unit_type=4, pos=(327.0, 5.0, -327.0, 0.0))
             and _refused(unit_type=4, pos=_far)
             and _refused(unit_type=0, battle=False, pos=_far)
             and not _refused(unit_type=4, battle=True, pos=_far)
             and _refused(unit_type=0, pos=(_lim + 1.0, 32.0, 0.0, 0.0))
             and _refused(unit_type=0, pos=(0.0, 32.0, -_lim - 1.0, 0.0))
             and _refused(unit_type=0, pos=(0.0, 32.0, 0.0, 400.0)))
    print(f"  spawns: record_pop's guard is split -- a battle unit pops at {_far[:3]} "
          f"(floats, to +/-{_lim:g}), a lobby unit there is refused, and so is a "
          f"battle unit past the box: {'OK' if _g_ok else 'FAIL'}")
    ok &= _g_ok

    # (6) the knob: on unless the env says otherwise; off -> no row at all
    _k_ok = (battlepop.BATTLE_SPAWNS
             or os.environ.get("FMO_BATTLE_SPAWNS", "").strip() not in ("", "1"))
    if not battlepop.BATTLE_SPAWNS:
        _k_ok = _k_ok and battlepop.spawn_row(471) is None
    print(f"  knob: FMO_BATTLE_SPAWNS defaults ON, 0 = FMO_BATTLE_POS for every "
          f"map ({'on' if battlepop.BATTLE_SPAWNS else 'off'}): {'OK' if _k_ok else 'FAIL'}")
    ok &= _k_ok

    # (7) the shipped table, when it was built: every slot inside the play
    # boundary we serve (missionblock BattleArea, FMO_BATTLE_BOUNDS) and
    # accepted by record_pop as a battle POP, every side's slots >= 12 apart,
    # the sides >= 200 apart; since 2026-10-07 the whole map is used, so the
    # median pair is >= 1000 apart and 471's (where live pilots walked
    # x -107..1346, z -158..1479) too. A table cut inside +/-300 fails both.
    if not battlepop.BATTLE_SPAWN_ROWS:
        print("  spawns: shipped table SKIP (fmodata/fmo-battle-spawns.tsv missing; "
              "tools/fmodatagen/fmospawns.py builds it)")
    else:
        from . import defaults
        _bb = missionblock.BATTLE_BOUNDS or tuple(
            int(v) for v in defaults.RELEASE_DEFAULTS["FMO_BATTLE_BOUNDS"].split(","))
        _bad, _seps = [], []
        for _m, _r in battlepop.BATTLE_SPAWN_ROWS.items():
            for _k in ("a", "b"):
                _pts = [(x, 0.0, z) for x, z in _r[_k + "_slots"]]
                if not _apart(_pts) or not all(
                        _bb[0] < p[0] < _bb[2] and _bb[1] < p[2] < _bb[3] for p in _pts):
                    _bad.append((_m, _k))
                for _x, _z in _r[_k + "_slots"]:
                    if _refused(unit_type=0, pos=(_x, _r["y"], _z, 0.0)):
                        _bad.append((_m, _k + " guard"))
                        break
            _sep = math.hypot(_r["a"][0] - _r["b"][0], _r["a"][1] - _r["b"][1])
            _seps.append(_sep)
            if _sep < 200:
                _bad.append((_m, "sep"))
        _seps.sort()
        _med = _seps[len(_seps) // 2]
        _r471 = battlepop.BATTLE_SPAWN_ROWS.get(471)
        _s471 = (math.hypot(_r471["a"][0] - _r471["b"][0], _r471["a"][1] - _r471["b"][1])
                 if _r471 else 0.0)
        _t_ok = not _bad and _med >= 1000 and _s471 >= 1000
        print(f"  spawns: shipped table, {len(battlepop.BATTLE_SPAWN_ROWS)} maps, "
              f"slots inside the served boundary {_bb} and record_pop's battle guard, "
              f">= 12 apart, sides >= 200 apart (median {_med:.0f}, 471 {_s471:.0f}, "
              f"both >= 1000): {'OK' if _t_ok else 'FAIL ' + str(_bad[:5])}")
        ok &= _t_ok
        ok &= _battle_spawn_ground_pins(_apart)
    return ok


def _battle_spawn_ground_pins(_apart):
    """THE SHIPPED TABLE IS CUT FROM COLLISION (fmospawns.py, 2026-10-07).
    The live facts it is pinned to: on 471 the open ground is y 32 (11 min of
    pilot movement at 31.7..32.3); on 86 the floors are 25.3/27.2 with the
    arena at 32.2 and a unit settles on the highest; units 40 apart stacked,
    so every side's slots are >= 50 apart. A row in the old shape (placement
    source, 16-unit slots) fails the same test."""
    rows = battlepop.BATTLE_SPAWN_ROWS
    bad = []
    for m, r in rows.items():
        if not r["source"].startswith("collision:"):
            bad.append((m, "source"))
        for k in ("a", "b"):
            if not _apart([(x, 0.0, z) for x, z in r[k + "_slots"]], 50.0):
                bad.append((m, k + " spacing"))
        if not -fmoworld.POP_BATTLE_POS_MAX <= r["y"] <= fmoworld.POP_BATTLE_POS_MAX:
            bad.append((m, "y"))
    # the five maps the placement-only reader could not serve now have rows
    for m in (122, 169, 445, 446, 448):
        if m not in rows:
            bad.append((m, "missing"))
    r471, r86 = rows.get(471), rows.get(86)
    if r471 is None or not 16.0 <= r471["y"] <= 28.0:
        bad.append((471, "y vs live ground 32"))
    if r86 is None or not 19.0 <= r86["y"] <= 28.2:
        bad.append((86, "y vs live floors 25.3..32.2"))
    twin = {"source": "placement sec4:169 objects/232 tiles",
            "a_slots": [(-80.0, -80.0), (-94.1, -65.9)]}
    twin_fails = (not twin["source"].startswith("collision:")
                  and not _apart([(x, 0.0, z) for x, z in twin["a_slots"]], 50.0))
    g_ok = not bad and twin_fails and len(rows) >= 225
    print(f"  spawns: shipped table from COLLISION, {len(rows)} maps (122 169 445 446 "
          f"448 included), slots >= 50 apart, 471 y under live ground 32, 86 under "
          f"25.3..32.2, an old-shape row fails: {'OK' if g_ok else 'FAIL ' + str(bad[:6])}")
    return g_ok


def _wanzer_paint_pins():
    """WANZER PAINT (inventory.SETUP_CAMO.., popparts.paint_for_char, 2026-10-07):
    the setup header's paint fields sit 0x1AC below the POP body's (lobby
    builder 0x61003032 vs battle dresser 0x611F59E0); a starter setup wears the
    nation's starting paint; a stored zero field is filled, a chosen one kept;
    the battle paints from the SELECTED setup; the owned bits feed the pickers."""
    ok = True
    fails = []

    def _c(n, v):
        if not v:
            fails.append(n)
        return bool(v)

    inv, pp = inventory, popparts
    sv = inv.SETUP_PAINT
    try:
        inv.SETUP_PAINT = True
        # (1) offsets: entry + 0x1AC = POP body, for all five fields
        ok &= _c(1, inv.POP_PAINT == {"camo": inv.SETUP_CAMO + 0x1AC,
                                      "line": inv.SETUP_LINE + 0x1AC,
                                      "armour": inv.SETUP_ARMOUR + 0x1AC,
                                      "insignia": inv.SETUP_INSIGNIA + 0x1AC}
                 and (inv.SETUP_CAMO, inv.SETUP_LINE, inv.SETUP_ARMOUR,
                      inv.SETUP_INSIGNIA, inv.SETUP_PAINT_B17)
                 == (0x0C, 0x0E, 0x10, 0x12, 0x17)
                 and inv.POP_PAINT_B17 == inv.SETUP_PAINT_B17 + 0x1AC == 0x1C3
                 and inv.POP_PAINT["camo"] == squad.POP_CAMO
                 and inv.POP_PAINT["line"] == squad.POP_COLOUR_A
                 and inv.POP_PAINT["armour"] == squad.POP_COLOUR_B
                 # the penalty byte is the neighbour, not this one
                 and inv.POP_PAINT_B17 == penalty.POP_PENALTY_LEVEL + 1)
        # (2) the starter setup wears the nation's starting paint
        st = inv.starter_setup(1, 1)
        b1 = inv.reply_0166(parts=st, nation=1)
        b2 = inv.reply_0166(parts=st, nation=2)
        p1 = inv.setup_paint(b1[:inv.SETUP_ENTRY_LEN])
        p2 = inv.setup_paint(b2[:inv.SETUP_ENTRY_LEN])
        ok &= _c(2, p1 == {"camo": 101, "armour": 42, "line": 1, "insignia": 0, "b17": 0}
                 and p2["armour"] == 6 and p2["line"] == 1
                 and inv.reply_0166(parts=st) == inv.reply_0166(parts=st, nation=9)
                 and inv.setup_paint(inv.reply_0166(parts=st)[:inv.SETUP_ENTRY_LEN])
                 == {"camo": 0, "armour": 0, "line": 0, "insignia": 0, "b17": 0}
                 and inv.reply_0166(parts=[], nation=1) == bytes(inv.REPLY_0166_LEN))
        # (3) fill_paint: zero fields of a setup in use filled, choices kept
        blk = bytearray(inv.reply_0166(parts=st))          # setup 1 in use, zero paint
        e2 = bytearray(inv.setup_entry(st, serial_base=22))
        inv.put_paint(e2, {"camo": 150, "armour": 77, "insignia": 412, "b17": 4})
        blk[inv.SETUP_ENTRY_LEN:2 * inv.SETUP_ENTRY_LEN] = e2
        blk = bytes(blk)
        f, filled = inv.fill_paint(blk, 2)
        f1 = inv.setup_paint(f[:inv.SETUP_ENTRY_LEN])
        f2 = inv.setup_paint(f[inv.SETUP_ENTRY_LEN:2 * inv.SETUP_ENTRY_LEN])
        ok &= _c(3, filled == [1, 2] and f1["armour"] == 6 and f1["camo"] == 101
                 and f2 == {"camo": 150, "armour": 77, "line": 1, "insignia": 412, "b17": 4}
                 and f[2 * inv.SETUP_ENTRY_LEN:] == blk[2 * inv.SETUP_ENTRY_LEN:]
                 and inv.fill_paint(f, 2)[0] is f and inv.fill_paint(blk, None)[0] is blk)
        # (4) the selected setup (0x0167 +0x00) wins when it is in use
        ch = {"setups": blk.hex(), "nation_byte": 1}
        ok &= _c(4, inv.active_setup_no(dict(ch, setup_sel=2))[0] == 2
                 and inv.active_setup_no(dict(ch, setup_sel=5))[0] == 1
                 and inv.active_setup_no(ch)[0] == 1
                 and inv.active_setup_no({})[0] == 1
                 and pp.stored_setup_parts(blk, 2) == pp.stored_setup1_parts(blk)
                 and pp.stored_setup_parts(blk, 3) == [])
        # (5) the battle paint: the selected setup's choices, zeros -> starting
        pt, _src = pp.paint_for_char(dict(ch, setup_sel=2), 1)
        px = pp.paint_pop_extra(pt)
        pt0, _ = pp.paint_for_char({}, 2)
        ok &= _c(5, pt == {"camo": 150, "armour": 77, "line": 1, "insignia": 412, "b17": 4}
                 and px == {0x1B8: struct.pack("<H", 150), 0x1BA: struct.pack("<H", 1),
                            0x1BC: struct.pack("<H", 77), 0x1BE: struct.pack("<H", 412),
                            0x1C3: b"\x04"}
                 and pp.paint_for_char(ch, 1)[0] == {"camo": 101, "armour": 42, "line": 1}
                 and pt0 == {"camo": 101, "armour": 6, "line": 1}
                 and pp.paint_for_char({}, None)[0] == {})
        # (6) owned bits: starting paint + worn paint, LSB-first, category bias
        bits = inv.owned_paint_bits(dict(ch, setup_sel=2), 1)
        ok &= _c(6, bits.get(0x110) == 0x01                  # camo 101 -> bit 0
                 and bits.get(0x110 + (49 >> 3)) == 1 << (49 & 7)   # camo 150
                 and bits.get(0x190) == 0x02                  # colour 1
                 and bits.get(0x190 + 5) == 0x04              # colour 42
                 and bits.get(0x190 + 9) == 0x20              # colour 77
                 and bits.get(0x3C0 + (311 >> 3)) == 1 << (311 & 7)  # insignia 412
                 and inv.owned_paint_bits(None, None) == {})
        body = status.reply_014a(rank=0, char={"nation_byte": 2}, active_setup=0,
                                 class_table=False)
        ok &= _c(7, body[status.S14A_OWNED + 0x110] == 0x01
                 and body[status.S14A_OWNED + 0x190] == 0x42   # colours 1 and 6
                 and status.reply_014a(rank=0, active_setup=0, class_table=False)
                 [status.S14A_OWNED + 0x110:status.S14A_OWNED + 0x210]
                 == bytes(0x100))
        # (8) FMO_SETUP_PAINT=0 reverts: no paint served, no owned bits
        inv.SETUP_PAINT = False
        ok &= _c(8, inv.starter_paint(1) == {}
                 and inv.reply_0166(parts=st, nation=1) == inv.reply_0166(parts=st)
                 and inv.owned_paint_bits(ch, 1) == {}
                 and inv.fill_paint(blk, 1)[0] is blk)
    except Exception as e:
        print(f"  wanzer paint: EXC {e!r}")
        ok = False
    finally:
        inv.SETUP_PAINT = sv
    print(f"  wanzer paint: setup +0x0C/0x0E/0x10/0x12/0x17 = POP +0x1B8.. "
          f"(+0x1AC), starter wears nation paint, zero fields filled and "
          f"choices kept, battle paints the selected setup, owned bits for the "
          f"pickers, FMO_SETUP_PAINT=0 reverts: "
          f"{'OK' if ok else 'FAIL at ' + str(fails)}")
    return ok


def _change_room_pins():
    """CHANGE ROOM maps and casts (move.ROOM_MAPS, roomcast.py, 2026-10-06):
    Room = 121, Briefing 122 / 123 by nation, Room B / C = 124, Hangar 141;
    a Room's cast follows the ZONE kind it hangs off; an old plain 'room'
    layout band is still honoured."""
    import tempfile as _tf
    from . import move as _mv, npccast as _nc, npcroster as _nr, popnation as _pn
    from . import roomcast as _rc, zoneentry as _ze
    ok = True
    # (1) the maps, per kind and per nation
    _o, _u = _mv.parse_room_maps_nation("")
    _m_ok = (_o == {1: 121, 2: 122, 3: 121, 4: 121, 5: 141}
             and _u == {1: 121, 2: 123, 3: 121, 4: 121, 5: 141}
             and _mv.parse_room_maps("") == _o)
    _o2, _u2 = _mv.parse_room_maps_nation("1:124,2:123/122")
    _m_ok &= _o2[1] == 124 and _u2[1] == 124 and _o2[2] == 123 and _u2[2] == 122 and _o2[5] == 141
    for _bad in ("2:122/999", "6:121", "1:x"):
        try:
            _mv.parse_room_maps_nation(_bad)
            _m_ok = False
        except SystemExit:
            pass
    if (os.environ.get("FMO_ROOM_MAPS", "").strip() or _mv.ROOM_MAPS_DEFAULT) == _mv.ROOM_MAPS_DEFAULT:
        _m_ok &= (_mv.ROOM_MAPS == _o and _mv.ROOM_MAPS_USN == _u
                  and _mv.place_map(509, 2, nation=1)[0] == 122
                  and _mv.place_map(509, 2, nation=2)[0] == 123
                  and _mv.place_map(509, 2)[0] == 122
                  and _mv.place_map(100, 1, nation=2)[0] == 121
                  and _mv.place_map(100, 3)[0] == 121 and _mv.place_map(100, 4)[0] == 121
                  and _mv.place_map(0, 5, nation=2)[0] == 141)
    print(f"  change room maps: Room 121, Briefing 122 O.C.U. / 123 U.S.N., Room B/C 121, "
          f"Hangar 141: {'OK' if _m_ok else 'FAIL ' + str((_o, _u, _mv.ROOM_MAPS, _mv.ROOM_MAPS_USN))}")
    ok &= _m_ok
    # (2) the room people are in the catalogue, dressed from their own rows
    _c_ok = all(fmoworld.NPC_CATALOGUE.get(k) == v for k, v in _rc.ROOM_CATALOGUE.items())
    _c_ok &= (fmoworld.NPC_CATALOGUE[300][:2] == ("James", "Douglas")
              and fmoworld.NPC_CATALOGUE[300][3:5] == (53, 101)
              and fmoworld.NPC_CATALOGUE[301][3:5] == (53, 201)
              and fmoworld.NPC_CATALOGUE[304][3:5] == (58, 152)
              and fmoworld.NPC_CATALOGUE[305][3:5] == (58, 252)
              and fmolayout.PLACE_BANDS.get(1) == "room"
              and fmolayout.place_band_ids(1, 100) == ["room_hq", "room"]
              and fmolayout.place_band_ids(1, 600) == ["room"]
              and fmolayout.place_band_ids(3, 100) == ["roomb"])
    print(f"  change room people in the catalogue (300..307), zone bands resolve: "
          f"{'OK' if _c_ok else 'FAIL'}")
    ok &= _c_ok
    if _nc.NPC_LAYOUT is None:
        return ok
    # (3) the cast per zone kind, from the shipped defaults (an empty layout)
    _lp = os.path.join(_tf.mkdtemp(prefix="fmo-roomcast-"), "layout.json")
    _saved = (_nc.NPC_LAYOUT.path, _nc.NPC_LAYOUT._mtime, _nc.NPC_LAYOUT._data)
    _saved_pn, _saved_npc = _pn.pop_nation_for, _ze.NATION_PER_CHARACTER
    _h = "selftest-roomcast"
    _saved_pl = _mv.WORLD_PLACES.get(_h)
    _nc.NPC_LAYOUT.path, _nc.NPC_LAYOUT._mtime = _lp, None

    def _fw(d):
        return fmolayout.face_to_wire(d, _nc.FACE_SIGN)

    def _cast(zone):
        b, r, _src = _nr.place_band_roster(1, zone)
        return b, ({u: (c, p) for u, _t, p, c in r[0]} if r else None)

    def _served(zone, nation):
        _mv.WORLD_PLACES[_h] = (zone, 1, 1)
        _pn.pop_nation_for = lambda host: (nation, "selftest")
        r = _nr.roster_for(_h)
        return {u: c for u, _t, _p, c in r[0]}, r[1], r[2]
    try:
        _ze.NATION_PER_CHARACTER = True
        _hq, _hq3, _oc, _oc4, _fz, _col = (_cast(z) for z in (100, 300, 200, 400, 509, 600))
        _sg = (102, (-0.46, 0.0, 13.69, _fw(0)))
        _d_ok = (_hq == ("room_hq", {0x82081022: (300, (5.42, 0.0, 1.56, _fw(270))),
                                     0x82081023: (302, (6.58, 0.0, 11.03, _fw(90))),
                                     0x82080800: _sg})
                 and _hq3 == _hq
                 and _oc == ("room_occ", {0x82081012: (306, (5.42, 0.0, 1.56, _fw(270))),
                                          0x82080800: _sg})
                 and _oc4 == _oc
                 and _fz == ("room_fz", {0x82081022: (304, (5.42, 0.0, 1.56, _fw(270))),
                                         0x82080800: _sg})
                 and _col == ("room", None))
        print(f"  room cast per zone kind: HQ Douglas + Harada, occupied Sassoon, frontline "
              f"Forster, the sergeant in all: {'OK' if _d_ok else 'FAIL ' + str((_hq, _oc, _fz, _col))}")
        ok &= _d_ok
        # (4) the pilot's nation picks the person: U.S.N. pilots meet Klein,
        # Burnet, Wright, Miller, and Goodwin as the sergeant
        if _mv.PLACES:
            _sv = {(z, n): _served(z, n) for z in (100, 200, 509) for n in (1, 2)}
            _n_ok = (_sv[(100, 1)][0] == {0x82081022: 300, 0x82081023: 302, 0x82080800: 102}
                     and _sv[(100, 2)][0] == {0x82081022: 301, 0x82081023: 303, 0x82080800: 104}
                     and _sv[(200, 1)][0] == {0x82081012: 306, 0x82080800: 102}
                     and _sv[(200, 2)][0] == {0x82081012: 307, 0x82080800: 104}
                     and _sv[(509, 1)][0] == {0x82081022: 304, 0x82080800: 102}
                     and _sv[(509, 2)][0] == {0x82081022: 305, 0x82080800: 104}
                     and _sv[(100, 2)][1].get(0x82080800) == ("Training", "Sergeant")
                     and "room_hq" in _sv[(100, 1)][2])
            print(f"  room cast per nation (U.S.N.: Klein, Burnet, Wright, Miller, Goodwin): "
                  f"{'OK' if _n_ok else 'FAIL ' + str(_sv)}")
            ok &= _n_ok
        # (5) an OLD layout with only the plain 'room' band still serves it, in
        # every zone; a zone band in the file beats it for its own zones
        _old = [{"key": 0x82080800, "x": -0.46, "y": 0.0, "z": 13.69, "face": 0,
                 "cat": 102, "label": "Training.Sergeant"}]
        _nc.NPC_LAYOUT.set_band("room", 124, _old)
        _p100, _p200, _p600 = _cast(100), _cast(200), _cast(600)
        _nc.NPC_LAYOUT.set_band("room_hq", 121, [{"key": 0x82081022, "x": 5.0, "y": 0.0,
                                                  "z": 1.0, "face": 270, "cat": 300}])
        _q100, _q200 = _cast(100), _cast(200)
        _l_ok = (_p100 == ("room", {0x82080800: _sg}) and _p200 == _p100 and _p600 == _p100
                 and _q100 == ("room_hq", {0x82081022: (300, (5.0, 0.0, 1.0, _fw(270)))})
                 and _q200 == _p100)
        print(f"  an old plain 'room' layout band is still honoured; a zone band wins "
              f"for its zones: {'OK' if _l_ok else 'FAIL ' + str((_p100, _q100, _q200))}")
        ok &= _l_ok
    finally:
        _nc.NPC_LAYOUT.path, _nc.NPC_LAYOUT._mtime, _nc.NPC_LAYOUT._data = _saved
        _pn.pop_nation_for, _ze.NATION_PER_CHARACTER = _saved_pn, _saved_npc
        _mv.WORLD_PLACES.pop(_h, None)
        if _saved_pl is not None:
            _mv.WORLD_PLACES[_h] = _saved_pl
    return ok


def selftest():
    """Checks that need no client: the checksum against real captured bytes.

    The player database is a THROWAWAY one (OpenLobby's tools/pgtest.py),
    never POL_DATABASE_URL: run inside a deployed container, a selftest that
    used the configured database would write production (the 09-08 fmo.db
    lesson). With no test database the database is switched off, the
    character store falls back to the JSON file, and the checks that need
    the database SKIP."""
    import tempfile
    _store_was = charstore.CHAR_STORE
    _db_was = (charstore.FMO_DB, charstore._db_ready[0])
    # the character store's JSON file too: never the configured one, which on
    # a deployment is the operator's backup of the pilots
    flat_globals()["CHAR_STORE"] = os.path.join(
        tempfile.mkdtemp(prefix="fmo-selftest-"), "fmo_characters.json")
    charstore._db_ready[0] = False              # this database's own first use
    try:
        if fmodb is None:
            flat_globals()["FMO_DB"] = ""
            return _selftest_run(None)
        with fmodb.test_database() as url:
            if url is None:
                flat_globals()["FMO_DB"] = ""
            return _selftest_run(url)
    finally:
        flat_globals()["CHAR_STORE"] = _store_was
        flat_globals()["FMO_DB"] = _db_was[0]
        charstore._db_ready[0] = _db_was[1]


def _battle_position_pins():
    """BATTLE POSITIONS (2026-10-07): a battle channel is placed by its own
    cmd 23/24 motion state (s16 / 3.75), not by cmd 240's int16 hundredths;
    a lobby channel keeps cmd 240 exactly; a wrapped cmd 240 cannot corrupt
    a battle position; a lobby never inherits a battle one."""
    ok = True
    W = fmoworld

    class _Ch:
        def __init__(self, key, me=0x1001):
            self.key, self._me, self.pos, self.pos_src = key, me, (0.0, 5.0, 0.0), None

        def self_unit(self):
            return self._me

    # (1) the client's own bytes (prod fmo.log 2026-10-07 05:36:12Z, cmd 24,
    # 76 B): unit 0x2223's state carries the s16 position AND, under flag 0x40,
    # the same point as floats (128.0, 69.97, -168.0) -- the 3.75 scale checked
    # against the client's own float, not against a guess
    _b = bytes.fromhex(
        "03000110000000080000 95badc14 4407ddd3"
        "23220000 0071000095badc14 e00106018bfd 000000438cf28b42ffff27c3 0000 000000000000"
        "24220000 0020000095badc14 000000000000 1a00".replace(" ", ""))
    _got = W.battle_motion_positions(W.CMD_BM_MOVE_BATCH, _b, 0x1001)
    _fl = struct.unpack_from("<fff", _b, 2 + 16 + 4 + 8 + 6)
    _r_ok = (len(_got) == 1 and _got[0][0] == 0x2223
             and all(abs(a - b) < 0.27 for a, b in zip(_got[0][1], _fl)))
    print(f"  battle pos: real cmd 24 -> unit 0x2223 at "
          f"{tuple(round(v, 2) for v in _got[0][1]) if _got else None}, the "
          f"state's own floats {tuple(round(v, 2) for v in _fl)}; 0x1001 (no "
          f"flag bit 0) gives none: {'OK' if _r_ok else 'FAIL'}")
    ok &= _r_ok

    # (2) a battle pilot at x 1343 / z 1477 (map 471, live 10-06) is tracked
    # there from cmd 23 and from its own cmd 24 entry, not a squad unit's
    _far = (1343.0, 40.0, 1477.0)
    _st = W.record_motion_pos(_far)
    _bc = _Ch(b"1234battle")
    referee.enter_battle_pos(_bc, (64.0, 46.4, 0.0, 0.0))
    _p23 = referee.track_battle_motion(_bc, 23, _st, 0x1001)
    _bc2 = _Ch(b"1234battle")
    _batch = (struct.pack("<H", 2) + struct.pack("<I", 0x2222) + W.record_motion_pos((5.0, 0, 5.0))
              + struct.pack("<I", 0x1001) + _st)
    _p24 = referee.track_battle_motion(_bc2, 24, _batch, 0x1001)
    _not_mine = referee.track_battle_motion(_Ch(b"1234battle"), 23, _st, 0x2222)
    _t_ok = (all(_p is not None and all(abs(a - b) < 0.27 for a, b in zip(_p, _far))
                 for _p in (_p23, _p24))
             and _bc.pos == _p23 and _bc.pos_src == "state" and _bc2.pos == _p24
             and _not_mine is None and referee.objective_zone_contains(
                 (1300, 1400, 100, 100), _bc.pos))
    print(f"  battle pos: x 1343 / z 1477 from cmd 23 {_p23} and cmd 24 {_p24}; "
          f"a squad unit's record moves nobody; hold zone sees it: "
          f"{'OK' if _t_ok else 'FAIL'}")
    ok &= _t_ok

    # (3) a wrapped cmd 240 does not corrupt it: ignored once cmd 23/24 placed
    # the pilot, unwrapped against the last position before that
    _wrap = tuple(((v * 100 + 32768) % 65536 - 32768) / 100 for v in _far)
    _ign = referee.battle_move_pos(_bc, _wrap)
    _bc3 = _Ch(b"1234battle")
    referee.enter_battle_pos(_bc3, (1340.0, 40.0, 1470.0))
    _unw = referee.battle_move_pos(_bc3, _wrap)
    _w_ok = (_ign is None and _bc.pos == _p23 and _unw is not None
             and all(abs(a - b) < 0.02 for a, b in zip(_unw, _far))
             and abs(_wrap[0] - 1343.0) > 600)
    print(f"  battle pos: cmd 240 wrapped to {_wrap} is ignored after cmd 23/24, "
          f"unwrapped to {_unw} before: {'OK' if _w_ok else 'FAIL'}")
    ok &= _w_ok

    # (4) LOBBY unchanged: cmd 240 taken verbatim, cmd 23/24 ignored, and a
    # channel back from a battle gets its lobby position, never x 1343
    _lc = _Ch(b"1234lobby")
    _lp = (12.34, 0.0, -300.5)
    _l_ok = (referee.battle_move_pos(_lc, _lp) == _lp
             and referee.track_battle_motion(_lc, 23, _st, 0x1001) is None
             and _lc.pos == (0.0, 5.0, 0.0) and not referee.leave_battle_pos(_lc))
    _rc = _Ch(b"1234lobby")
    _rc.pos = (10.0, 0.0, 20.0)
    _rc.key = b"1234battle"
    referee.enter_battle_pos(_rc, (64.0, 46.4, 0.0))
    referee.track_battle_motion(_rc, 23, _st, 0x1001)
    _rc.key = b"1234lobby"
    _back = referee.leave_battle_pos(_rc)
    _l_ok &= _back and _rc.pos == (10.0, 0.0, 20.0) and _rc.pos_src is None
    _m = W.parse_move(bytes.fromhex("06000000" "25000000" "9200f700"))
    _l_ok &= referee.battle_move_pos(_rc, _m["pos"]) == _m["pos"]
    print(f"  battle pos: lobby keeps cmd 240 as is and ignores cmd 23/24; back "
          f"from a battle the lobby position returns ({_rc.pos}): "
          f"{'OK' if _l_ok else 'FAIL'}")
    ok &= _l_ok
    return ok


def _pvp_room_pins():
    """FRONTLINE PvP (pvproom.py, 2026-10-07), no client: P1 the WAITING block
    on a creating sortie in a PvP selector (and nowhere else), the start on a
    hostile pilot's arrival, on Start Battle (cmd 137) and on the timeout, the
    late joiner's cmd 148; P2 the room judge (pilots and NPC units, the time
    limit draw, a dead pilot waits for the room); P3 one war settle per battle
    for both sides. Every global it touches is restored."""
    from . import pvproom as _pv
    ok = True
    T0 = 1_900_000_000.0
    _knobs = ("MATCHING", "JUDGE", "WAR", "PVP_WAIT", "PVP_SELECTORS", "OBJECTIVE_KIND",
              "DEATH_WAIT", "NPC_VS_PILOTS")
    _sv = {k: getattr(_pv, k) for k in _knobs}
    _sv_bs = dict(referee.BATTLE_STATE)
    _sv_mt = missionblock.MISSION_TIME
    _sv_ws = (warstate.war_state, warstate._war_tick, warstate.WAR)

    class _Ch:
        def __init__(self, acct, port):
            self.addr, self.account, self.key = ("198.51.100.7", port), acct, b"1234battle"
            self.popped, self.pending = True, []

    def _recs(ch):
        return [(struct.unpack_from("<I", r, 4)[0], r[0x10:]) for r in ch.pending]

    class _Sess:
        def __init__(self, acct):
            self.account, self.peer, self.battle_end_done, self.ends = acct, acct, False, []

        def battle_key(self):
            return self.account

        def battle_result_push(self, conn, occasion, won=None):
            return None

        def penalty_report_push(self, conn):
            return None

        def battle_end_push(self, conn, why="", won=None):
            self.ends.append(won)
            return b"014c"

    class _War:
        def __init__(self):
            self.calls = []

        def settle(self, tile, nation, won=True, pvp=False, now=None):
            self.calls.append((tile, nation, won, pvp))
            return {"nation": nation, "control": 50}, "pinned"

    on = lambda a: True
    try:
        _pv.MATCHING = _pv.JUDGE = _pv.WAR = True
        _pv.PVP_WAIT, _pv.PVP_SELECTORS, _pv.OBJECTIVE_KIND = 1200, {505, 509, 513}, 5
        _pv.DEATH_WAIT, _pv.NPC_VS_PILOTS = 300, False
        missionblock.MISSION_TIME = 1800
        _pv.reset()

        # --- P1a: the creating sortie in 509 is served WAITING + Team Deathmatch;
        # the same map in selector 200 is served neither
        _role, _room = _pv.peek(509, 418, T0)
        _b509 = sortie.reply_013a(mapno="418", ep_enable=False, start_time=0,
                                  **_pv.block_knobs(_role, _room))
        _r200 = _pv.peek(200, 418, T0)
        _b200 = sortie.reply_013a(mapno="418", ep_enable=False, start_time=0,
                                  **_pv.block_knobs(*_r200))
        _bb = sortie.R13A_BLOCK
        _u = lambda b, o: struct.unpack_from("<I", b, _bb + o)[0]
        _p1a = (_role == "create" and _r200 == (None, None)
                and _u(_b509, missionblock.MB_MATCH_FLAGS) == _pv.FLAG_WAITING
                and _u(_b509, missionblock.MB_OBJECTIVE_KIND) == 5
                and _u(_b200, missionblock.MB_MATCH_FLAGS) == 0
                and _u(_b200, missionblock.MB_OBJECTIVE_KIND) == 0
                and _b200 == sortie.reply_013a(mapno="418", ep_enable=False, start_time=0))
        print(f"  pvp room P1: a creating sortie on selector 509 map 418 is served "
              f"block+0x7C = 0x200 (WAITING) and +0x50 = 5; selector 200 is served "
              f"neither (byte-identical to before): {'OK' if _p1a else 'FAIL'}")
        ok &= _p1a

        # --- P1b: start when the first HOSTILE pilot pops in; same-side does not
        _pv.join("pvp:lex", "pvp:lex", 509, 418, 94101, 1, T0)
        _pv.join("pvp:cid", "pvp:cid", 509, 418, 94101, 1, T0 + 5)
        _jv = _pv.join_verdict(509, 418, 2, None, T0 + 10)
        _pv.join("pvp:dan", "pvp:dan", 509, 418, 94101, 2, T0 + 10)
        lex, cid, dan = _Ch("pvp:lex", 5001), _Ch("pvp:cid", 5002), _Ch("pvp:dan", 5003)
        _pv.tick_chan(lex, lex.addr, T0 + 11)
        _pv.tick_chan(cid, cid.addr, T0 + 12)
        _waited = (_pv.room_of("pvp:lex")["started"] is None and not lex.pending
                   and not _pv.squad_allowed(lex) and _pv.in_matching_room(lex))
        _pv.tick_chan(dan, dan.addr, T0 + 20)
        _pv.tick_chan(lex, lex.addr, T0 + 21)
        _r = _pv.room_of("pvp:lex")
        _rd, _rc = _recs(dan), _recs(lex)
        _p1b = (_waited and _jv[0] is None and _jv[2] == 100
                and _r["started"] == int(T0 + 20) and _r["reason"] == 1 and _r["pvp"]
                and _rd == _rc and len(_rd) == 1 and _rd[0][0] == 138
                and struct.unpack_from("<II", _rd[0][1], 0) == (int(T0 + 20), 1)
                and not _pv.squad_allowed(lex)
                and referee.BATTLE_STATE.get("pvp:lex", {}).get("started_at") in (None, int(T0 + 20)))
        _pv.tick_chan(lex, lex.addr, T0 + 22)
        _p1b &= len(lex.pending) == 1          # once per channel
        print(f"  pvp room P1: the room waits while same-side pilots pop in (no cmd "
              f"138, no NPC squad); the first U.S.N. pilot's pop starts it, reason 1, "
              f"and every member gets ONE cmd 138 with the start stamp; no squad "
              f"(pilots only): {'OK' if _p1b else 'FAIL'}")
        ok &= _p1b

        # --- P1c: the late joiner: 0x600 + the room's start, then cmd 148
        _role_l, _room_l = _pv.peek(509, 418, T0 + 100)
        _kl = _pv.block_knobs(_role_l, _room_l)
        _jl = _pv.join_verdict(509, 418, 2, None, T0 + 100)
        _jlate = _pv.join_verdict(509, 418, 2, None, T0 + 20 + sortie.JOIN_WINDOW + 1)
        _pv.join("pvp:eve", "pvp:eve", 509, 418, 94101, 2, T0 + 100)
        eve = _Ch("pvp:eve", 5004)
        _pv.tick_chan(eve, eve.addr, T0 + 101)
        _re = _recs(eve)
        _p1c = (_role_l == "late" and _kl.get("match_flags") == 0x600
                and _pv.start_stamp(_role_l, _room_l) == int(T0 + 20)
                and _jl[0] is None and _jlate[0] == sortie.JOIN_CODE_LATE
                and len(_re) == 1 and _re[0][0] == 148 and len(_re[0][1]) == _pv.RESYNC_LEN
                and struct.unpack_from("<II", _re[0][1], 0) == (3, int(T0 + 20))
                and not any(_re[0][1][8:]))
        print(f"  pvp room P1: a pilot joining after the start is served 0x600 and the "
              f"room's start, then cmd 148 (bits 3, +4 start, 0xB4 B); the join window "
              f"runs from the START ({sortie.JOIN_WINDOW} s): {'OK' if _p1c else 'FAIL'}")
        ok &= _p1c

        # --- P1d: the timeout (reason 3, NPCs fight) and Start Battle (cmd 137,
        # reason 2); an unknown battle cmd is logged once
        _pv.join("pvp:tom", "pvp:tom", 509, 471, 94102, 1, T0)
        tom = _Ch("pvp:tom", 5005)
        _pv.tick_chan(tom, tom.addr, T0 + 1199)
        _not_yet = not tom.pending and not _pv.squad_allowed(tom)
        _pv.tick_chan(tom, tom.addr, T0 + 1200)
        _rt = _recs(tom)
        _pv.join("pvp:sam", "pvp:sam", 505, 232, 90001, 2, T0)
        sam = _Ch("pvp:sam", 5006)
        _pv.note_cmd(sam, sam.addr, 137, bytes(64), T0 + 30)
        _pv.tick_chan(sam, sam.addr, T0 + 31)
        _rs = _recs(sam)
        _pv.note_cmd(sam, sam.addr, 250, b"\x01\x02", T0 + 32)
        _pv.note_cmd(sam, sam.addr, 240, b"\x01\x02", T0 + 32)
        _p1d = (_not_yet and len(_rt) == 1 and _rt[0][0] == 138
                and struct.unpack_from("<I", _rt[0][1], 4)[0] == 3 and _pv.squad_allowed(tom)
                and len(_rs) == 1 and struct.unpack_from("<I", _rs[0][1], 4)[0] == 2
                and _pv.squad_allowed(sam) and sam._pvp_unknown == {250})
        print(f"  pvp room P1: nobody came in 1200 s -> reason 3 and the NPC squad; "
              f"Start Battle (cmd 137) -> reason 2; an unknown battle cmd is logged "
              f"once, a known one not: {'OK' if _p1d else 'FAIL'}")
        ok &= _p1d

        # --- P2: the judge, pure
        _jc = _pv.judge_counts
        _p2 = (_jc({1: 1, 2: 0}, {1, 2}, T0, T0 + 5, 1800)[0] == 1
               and _jc({1: 0, 2: 2}, {1, 2}, T0, T0 + 5, 1800)[0] == 2
               and _jc({1: 0, 2: 0}, {1, 2}, T0, T0 + 5, 1800)[0] == 0
               and _jc({1: 2, 2: 2}, {1, 2}, T0, T0 + 5, 1800) is None
               and _jc({1: 2, 2: 2}, {1, 2}, T0, T0 + 1800, 1800)[0] == 0
               and _jc({1: 3, 2: 2}, {1, 2}, T0, T0 + 1800, 1800)[0] == 1
               and _jc({1: 1, 2: 0}, {1}, T0, T0 + 5, 1800) is None
               and _jc({1: 1, 2: 0}, {1, 2}, None, T0 + 5, 1800) is None)
        print(f"  pvp room P2: judge: a side with nothing standing loses, both out = "
              f"draw, at the limit more standing wins and a tie draws (OUR rule), a "
              f"side that never fielded a unit cannot lose, a waiting room is not "
              f"judged: {'OK' if _p2 else 'FAIL'}")
        ok &= _p2

        # --- P2: AI units count for their side (room of tom, reason 3)
        referee.BATTLE_STATE["pvp:tom"] = {"granted_at": T0, "squad": {
            "nation": 2, "ids": [0x2222, 0x2223, 0x2224], "dead": {0x2222}}}
        _c, _d, _f = _pv.units_standing(_pv.room_of("pvp:tom"), T0 + 1300, on, {})
        _alive = _c == {1: 1, 2: 2} and _f == {1, 2}
        referee.BATTLE_STATE["pvp:tom"]["squad"]["dead"] = {0x2222, 0x2223, 0x2224}
        _ws = _War()
        warstate.war_state, warstate._war_tick, warstate.WAR = (lambda: _ws), (lambda st: None), "1"
        _pv.tick(T0 + 1301, on, {})
        _vt = (_pv.room_of("pvp:tom") or {}).get("verdict") or {}
        _ts = _Sess("pvp:tom")
        _tend = _pv.end_due(_ts, 1, T0 + 1302, on, {})
        _p2b = (_alive and _vt.get("winner") == 1 and _ts.ends == [True] and _tend
                and _ws.calls == [(94102, 1, True, False)] and _pv.room_of("pvp:tom") is None)
        print(f"  pvp room P2/P3: the NPC squad's live units count for U.S.N.; when the "
              f"last falls O.C.U. wins, the pilot's 0x014C says so, the war is settled "
              f"once (PvE, weight 1) and the room is forgotten: {'OK' if _p2b else 'FAIL'}")
        ok &= _p2b

        # --- P2: a dead pilot waits for the ROOM; the verdict follows the room.
        # Room 418: lex, cid (O.C.U.) vs dan, eve (U.S.N.)
        _ws.calls.clear()
        dead = {"pvp:lex": T0 + 200}
        _cs, _ds, _es, _cids = (_Sess("pvp:lex"), _Sess("pvp:dan"), _Sess("pvp:eve"),
                                _Sess("pvp:cid"))
        _w1 = _pv.end_due(_cs, 1, T0 + 210, on, dead)          # lex dead, cid fights on
        dead.update({"pvp:dan": T0 + 300, "pvp:eve": T0 + 310})
        _w2 = _pv.end_due(_cs, 1, T0 + 320, on, dead)           # U.S.N. wiped
        _pv.end_due(_ds, 1, T0 + 321, on, dead)
        _pv.end_due(_es, 1, T0 + 322, on, dead)
        _pv.end_due(_cids, 1, T0 + 323, on, dead)
        _calls_once = list(_ws.calls)
        _pv.tick(T0 + 400, on, dead)
        _p2c = (_w1 == [] and _cs.ends == [True] and _ds.ends == [False]
                and _es.ends == [False] and _cids.ends == [True] and _w2
                and _calls_once == [(94101, 1, True, True), (94101, 2, False, True)]
                and _ws.calls == _calls_once and _pv.room_of("pvp:lex") is None)
        print(f"  pvp room P2/P3: a destroyed pilot is NOT ended while its side still "
              f"stands; when U.S.N. is wiped every pilot's 0x014C follows the room (the "
              f"dead O.C.U. pilot WINS); fmowar.settle runs ONCE per side (PvP, weight "
              f"2), not per pilot: {'OK' if _p2c else 'FAIL'}")
        ok &= _p2c

        # --- P2: the death wait, a time-limit draw, and the per-pilot war
        # settle standing down
        _pv.reset()
        _pv.join("pvp:a", "pvp:a", 513, 66, 1, 1, T0)
        _pv.join("pvp:b", "pvp:b", 513, 66, 1, 1, T0)
        _pv.join("pvp:c", "pvp:c", 513, 66, 1, 2, T0)
        _pv.start(_pv.room_of("pvp:a"), 1, T0 + 10)
        _sa = _Sess("pvp:a")
        _early = _pv.end_due(_sa, 1, T0 + 100, on, {"pvp:a": T0 + 50})
        _late = _pv.end_due(_sa, 1, T0 + 100 + _pv.DEATH_WAIT, on, {"pvp:a": T0 + 50})

        class _WS:
            sector, peer, war_settled = (1, 1, 66), "pvp:b", None

            def battle_key(self):
                return "pvp:b"

            def in_arena(self):
                return False
        _st_none = settlement.SessionSettlement.war_settle(_WS(), True)
        _ws.calls.clear()
        _sb, _sc = _Sess("pvp:b"), _Sess("pvp:c")
        _pv.end_due(_sb, 1, T0 + 10 + 1800, on, {"pvp:a": T0 + 50})
        _pv.end_due(_sc, 1, T0 + 10 + 1800, on, {"pvp:a": T0 + 50})
        _p2d = (_early == [] and _sa.ends == [False] and _st_none is None
                and _sb.ends == [False] and _sc.ends == [False] and _ws.calls == [])
        print(f"  pvp room P2: a destroyed pilot whose side fights on ends as a loss "
              f"after FMO_PVP_DEATH_WAIT ({_pv.DEATH_WAIT} s); 1 vs 1 standing at the "
              f"limit is a draw (both see a loss, no war move); the pilot's own "
              f"war_settle stands down for the room: {'OK' if _p2d else 'FAIL'}")
        ok &= _p2d

        # --- the wiring: a real Session's 0x0139 on a 509 sector is served the
        # WAITING block and lands in a room; the same sector asked from
        # selector 200 is not (the war map's selector decides)
        _pv.reset()
        _sv_ss = sortie.SERVE_SORTIE
        _sv_sm = dict(rooms.SORTIE_MAP)
        try:
            flat_globals()["SERVE_SORTIE"] = True
            _wire = {}
            for _z in (509, 200):
                _ss = session.Session("selftest-pvp:0")
                _ss._account = "pvp:session"
                _ss.pilot_trained = lambda: True
                _ss.sector, _ss.sector_zone = (94101, 4, 418), _z
                _ro = [packet.parse(o) for o in _ss.on_packet(packet.parse(packet.build(
                    sortie.MSG_SORTIE_REQ, bytes(sortie.REQ_0139_LEN), seq=0x7001,
                    conn_id=1)))]
                _pl = _ro[0]["payload"] if _ro and _ro[0]["msg"] == sortie.MSG_SORTIE_REPLY else b""
                _wire[_z] = ((struct.unpack_from("<I", _pl, _bb + missionblock.MB_MATCH_FLAGS)[0],
                              struct.unpack_from("<I", _pl, _bb + missionblock.MB_OBJECTIVE_KIND)[0])
                             if _pl else None, _pv.room_of(_ss.battle_key()) is not None)
                _pv.reset()
        finally:
            flat_globals()["SERVE_SORTIE"] = _sv_ss
            rooms.SORTIE_MAP.clear()
            rooms.SORTIE_MAP.update(_sv_sm)
        _p1w = _wire.get(509) == ((0x200, 5), True) and _wire.get(200) == ((0, 0), False)
        print(f"  pvp room P1: through Session.on_sortie, a 509 sector's 0x013A carries "
              f"+0x7C 0x200 / +0x50 5 and the pilot is in a room; selector 200 neither "
              f"{_wire}: {'OK' if _p1w else 'FAIL'}")
        ok &= _p1w

        # --- knobs off: nothing is a room, the block is as before
        _pv.reset()
        _pv.MATCHING = _pv.JUDGE = _pv.WAR = False
        _off = (_pv.peek(509, 418, T0) == (None, None) and _pv.block_knobs(None) == {}
                and _pv.join("pvp:x", "pvp:x", 509, 418, 1, 1, T0) == (None, None)
                and _pv.squad_allowed(_Ch("pvp:x", 5009))
                and not _pv.in_matching_room(_Ch("pvp:x", 5009))
                and not _pv.judged("pvp:x") and not _pv.war_by_room("pvp:x"))
        print(f"  pvp room: FMO_PVP_MATCHING / _JUDGE / _WAR = 0 -> no room, no "
              f"waiting, the squad and the old start as before: {'OK' if _off else 'FAIL'}")
        ok &= _off
    finally:
        for k, v in _sv.items():
            setattr(_pv, k, v)
        _pv.reset()
        referee.BATTLE_STATE.clear()
        referee.BATTLE_STATE.update(_sv_bs)
        missionblock.MISSION_TIME = _sv_mt
        warstate.war_state, warstate._war_tick, warstate.WAR = _sv_ws
    return ok


def _selftest_run(test_db):
    # WARNING: NEVER the real sector-win ledger: several checks run a WON battle
    # through the live ledger, and on prod that is the real one (the 09-08
    # fmo.db lesson: a selftest inside the container writes production).
    sectorwins.SECTOR_WINS_STORE = False
    # Declared here because POP is READ earlier in this function than the
    # battle-pop check that reassigns it -- Python forbids `global` after use.
    ok = True

    # THE CLIENT-DERIVED TABLES. Only fmodata/fmo-events.tsv ships; the floor
    # plans, the mission catalogue, the cosmetics, insignia and class-curve
    # files are generated by the user from their own client. A check that
    # needs one of them SKIPs when it is absent (printed, not counted as a
    # failure) and runs as before when it is present.
    _fmodata_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata")

    def _fmodata_skip(*names):
        _missing = [n for n in names
                    if not os.path.exists(os.path.join(_fmodata_dir, *n.split("/")))]
        if not _missing:
            return None
        return (f"SKIP (fmodata/{' fmodata/'.join(_missing)} missing) "
                f"(needs services/fmodata built from your client, see "
                f"tools/fmodata_build.py)")

    # PLAY TIME (0x0182 -> 0x0183, 2026-09-08). Two things are pinned here and
    # both were live failures waiting to happen: the reply ID (message 1 took
    # the client's `jne` at 0x61163394 straight to the [FMxxxxx] box) and the
    # OFFSET (the client reads frame+0x24, and HDR is 0x14, so payload+0x10 --
    # off by one dword and the screen reads a neighbour). The d/h/m split is
    # checked against the client's own chain, including the 59s/61s boundaries
    # where a "round to nearest" would disagree with its truncating idiv.
    _pt_body = move.reply_0183(90061)          # 1d 1h 1m 1s
    _pt_frame = packet.build(move.MSG_PLAYTIME_REPLY, _pt_body, packet.SEQ_MIN, 0)
    _pt_ok = (move.MSG_PLAYTIME_REPLY == 0x0183
              and len(_pt_body) == move.REPLY_0183_LEN == 0x14
              and struct.unpack_from("<H", _pt_frame, 6)[0] == 0x0183
              and struct.unpack_from("<I", _pt_frame, 0x24)[0] == 90061
              and move.playtime_dhm(90061) == (1, 1, 1)
              and move.playtime_dhm(0) == (0, 0, 0)
              and move.playtime_dhm(59) == (0, 0, 0)
              and move.playtime_dhm(60) == (0, 0, 1)
              and move.playtime_dhm(3599) == (0, 0, 59)
              and move.playtime_dhm(3600) == (0, 1, 0)
              and move.playtime_dhm(86399) == (0, 23, 59)
              and move.playtime_dhm(86400) == (1, 0, 0)
              and move.reply_0183(-5) == move.reply_0183(0))
    print(f"  play time 0x0183 (id, payload+0x{move.S183_SECONDS:X} = frame+0x24, "
          f"d/h/m): {'OK' if _pt_ok else 'FAIL'}")
    ok &= _pt_ok

    # FMO_NATION_PER_CHARACTER (2026-09-08): the zone band follows the pilot's
    # faction per the LEV table, the U.S.N. roster is the O.C.U. one with each
    # part recast, and 0x014A carries the pilot's own nation byte.
    _fm = [(zoneentry.faction_mapkind(100, 2), 300), (zoneentry.faction_mapkind(207, 2), 407),
           (zoneentry.faction_mapkind(300, 1), 100), (zoneentry.faction_mapkind(409, 1), 209),
           (zoneentry.faction_mapkind(100, 1), 100), (zoneentry.faction_mapkind(509, 2), 509),
           (zoneentry.faction_mapkind(600, 1), 600), (zoneentry.faction_mapkind(100, None), 100)]
    _fm_ok = all(a == b for a, b in _fm)
    print(f"  faction_mapkind: {'OK' if _fm_ok else 'FAIL ' + str(_fm)}")
    ok &= _fm_ok
    # SE's own pairing (D87 / D07 E060 branches): staff by table, attendants
    # by branch offset; every pair is the same sex and the U.S.N. one wears a
    # 20x uniform.
    _cp = {100: 101, 102: 104, 103: 105, 106: 107, 30: 32, 31: 33, 108: 123,
           122: 137, 0: 15, 14: 29, 34: 38, 37: 41, 101: 101, 137: 137, 33: 33}
    _cp_ok = all(npccast.usn_counterpart(k) == v for k, v in _cp.items())
    for _t in list(range(108, 123)) + list(range(0, 15)) + [30, 31, 34, 35, 36, 37]:
        _u = npccast.usn_counterpart(_t)
        _a, _b = fmoworld.NPC_CATALOGUE[_t], fmoworld.NPC_CATALOGUE[_u]
        _cp_ok &= _u != _t and _a[2] == _b[2] and 200 <= _b[4] <= 299
    print(f"  usn_counterpart (SE's E060 pairing, same sex, 20x uniform): "
          f"{'OK' if _cp_ok else 'FAIL'}")
    ok &= _cp_ok
    _spec = ("0x82081010@1,2,3#100=Kwangsu.Son;0x82080400@4,5,6#109=Map.Selector;"
             "0x82081020@7,8,9#102=Henry.Viduka;0x82080110@1,1,1#103")
    _ocu, _ocu_names = npccast._parse_spec_keep_names(_spec)
    _usn, _usn_names = npccast.usn_roster(_ocu, _ocu_names)
    # a person's name is dropped on recast, a function label carries over
    _ur_ok = ([c for _, _, _, c in _usn] == [101, 124, 104, 105]
              and [p for _, _, p, _ in _usn] == [p for _, _, p, _ in _ocu]
              and 0x82080400 in _usn_names and 0x82081010 not in _usn_names
              and 0x82081020 not in _usn_names)
    print(f"  usn_roster: {'OK' if _ur_ok else 'FAIL ' + str((_usn, _usn_names))}")
    ok &= _ur_ok
    # the band rosters: occupation recasts the briefing keys, frontline puts
    # D07's people in and moves the sergeant's key to tag_secretary
    _occ, _occ_n = npccast.derive_band_roster(_ocu, _ocu_names, npccast.OCC_RECAST)
    _fz, _fz_n = npccast.derive_band_roster(_ocu, _ocu_names, npccast.FZ_RECAST, npccast.FZ_REKEY)
    _occ_usn, _ = npccast.usn_roster(_occ, _occ_n)
    _fz_usn, _ = npccast.usn_roster(_fz, _fz_n)
    _br_ok = (dict((u, c) for u, _, _, c in _occ) ==
              {0x82081010: 106, 0x82080400: 109, 0x82081020: 112, 0x82080110: 103}
              and _occ_n.get(0x82081020) == ("Mission", "Briefing")
              and 0x82081010 not in _occ_n and _occ_n.get(0x82080400) == ("Map", "Selector")
              and dict((u, c) for u, _, _, c in _fz) ==
              {0x82081010: 30, 0x82080400: 109, 0x82080120: 36, 0x82080110: 31}
              and dict((u, c) for u, _, _, c in _occ_usn)[0x82081010] == 107
              and dict((u, c) for u, _, _, c in _fz_usn) ==
              {0x82081010: 32, 0x82080400: 124, 0x82080120: 40, 0x82080110: 33}
              and [p for _, _, p, _ in _fz] == [p for _, _, p, _ in _ocu])
    print(f"  band rosters (occupation / frontline, both factions): "
          f"{'OK' if _br_ok else 'FAIL ' + str((_occ, _occ_n, _fz, _fz_n))}")
    ok &= _br_ok
    # roster_for follows the zone this host was granted
    _h = "selftest-roster"
    _saved_z = rooms.WORLD_ZONES.get(_h)
    try:
        _rf = {}
        for _z in (200, 400, 509, 100, 600):
            rooms.WORLD_ZONES[_h] = _z
            _rf[_z] = npcroster.roster_for(_h)[2]
    finally:
        rooms.WORLD_ZONES.pop(_h, None)
        if _saved_z is not None:
            rooms.WORLD_ZONES[_h] = _saved_z
    _rf_ok = ("occupation" in _rf[200] and "occupation" in _rf[400]
              and "frontline" in _rf[509] and "HQ" in _rf[100] and "coliseum" in _rf[600])
    print(f"  roster_for picks the band from WORLD_ZONES: {'OK' if _rf_ok else 'FAIL ' + str(_rf)}")
    ok &= _rf_ok
    # VERIFIED: the layout file (the lobby NPC editor) replaces ONLY the band it
    # carries, derives the U.S.N. half, and a broken file falls back to the env
    if npccast.NPC_LAYOUT is not None:
        import tempfile as _tf
        _lp = os.path.join(_tf.mkdtemp(prefix="fmo-layout-"), "layout.json")
        _saved_lp, _saved_lm = npccast.NPC_LAYOUT.path, npccast.NPC_LAYOUT._mtime
        npccast.NPC_LAYOUT.path, npccast.NPC_LAYOUT._mtime = _lp, None
        try:
            _b0 = npcroster.band_rosters()
            npccast.NPC_LAYOUT.set_band("hq", 102, [{"key": 0x82080940, "x": 1.0, "y": 3.11,
                                                     "z": 2.0, "face": 90, "cat": 108,
                                                     "label": "Ranking.Board"}])
            _b1 = npcroster.band_rosters()
            _lo = (_b1[1][1][0] == [(0x82080940, 4, (1.0, 3.11, 2.0,
                                                     fmolayout.face_to_wire(90, npccast.FACE_SIGN)), 108)]
                   and _b1[1][1][1] == {0x82080940: ("Ranking", "Board")}
                   and "layout file" in _b1[1][3]
                   and _b1[1][2][0][0][3] == npccast.usn_counterpart(108)
                   and _b1[3] is _b1[1]
                   and _b1[2] == _b0[2] and _b1[5] == _b0[5] and _b1[6] == _b0[6]
                   and npcroster.npc_cast_configured())
            print(f"  the layout file replaces ONLY the band it carries (hq), U.S.N. derived: "
                  f"{'OK' if _lo else 'FAIL ' + str(_b1[1])}")
            ok &= _lo
            _ctx = devtool.devtool_ctx()
            _lo1 = (_ctx["served"]["hq"]["roster"] == _b1[1][1][0]
                    and _ctx["served"]["hq"]["mapno"] == areachange.zone_mapno(100, zoneentry.MAPNO, "x")[0]
                    and set(_ctx["served"]) == set(fmolayout.BAND_IDS))
            print(f"  devtool_ctx serves the editor the live rosters per band: {'OK' if _lo1 else 'FAIL'}")
            ok &= _lo1
            # a PLACE band: the hangar's cast comes from the layout, nowhere else
            npccast.NPC_LAYOUT.set_band("hangar", move.ROOM_MAPS[5], [{"key": 0x82081011, "x": 1.0, "y": 0.5,
                                                                       "z": 2.0, "face": None, "cat": 106,
                                                                       "label": "Hangar.Staff"}])
            _saved_pl = move.WORLD_PLACES.get("selftest-place")
            move.WORLD_PLACES["selftest-place"] = (0, 5, 6)
            try:
                _pr = npcroster.roster_for("selftest-place")
            finally:
                move.WORLD_PLACES.pop("selftest-place", None)
                if _saved_pl is not None:
                    move.WORLD_PLACES["selftest-place"] = _saved_pl
            _lo4 = ((not move.PLACES) or (_pr[0] == [(0x82081011, 4, (1.0, 0.5, 2.0, 0.0), 106)]
                                          and "hangar band" in _pr[2]
                                          and devtool.devtool_ctx()["served"]["hangar"]["mapno"] == move.ROOM_MAPS[5]))
            print(f"  a place band (hangar) pops from the layout, nothing else: {'OK' if _lo4 else 'FAIL ' + str(_pr)}")
            ok &= _lo4
            # a per-row client_kind override reaches the pop record
            npccast.NPC_LAYOUT.set_band("hq", 102, [{"key": 0x82080940, "x": 1.0, "y": 3.11, "z": 2.0,
                                                     "face": None, "cat": 108, "ckind": 0}])
            npcroster.band_rosters()
            _tw = worldchannel.WorldChannel(("198.51.100.9", 19155))
            _tw.pop_args = None
            _rec = npcroster._npc_pop_record(0x82080940, 4, (1.0, 3.11, 2.0, 0.0), 108, _tw, (0, 0, 0, 0), {})
            _lo5 = (npccast.NPC_CKIND_OVERRIDE == {0x82080940: 0}
                    and _rec[fmoworld.REC_HDR + fmoworld.POP_CLIENT_KIND] == 0)
            # the NPC pop carries the stream key, and a moved row becomes a cmd
            # 240 on the NPC's own alias stream
            _tw.key = b"selftestkey"
            _rec2 = npcroster._npc_pop_record(0x82080940, 4, (1.0, 3.11, 2.0, 0.0), 108, _tw, (0, 0, 0, 0), {})
            _blob = _rec2[fmoworld.REC_HDR + fmoworld.POP_CLIENT_BLOB:
                          fmoworld.REC_HDR + fmoworld.POP_CLIENT_BLOB + fmoworld.POP_CLIENT_BLOB_LEN]
            _mv = npcroster.npc_moves([(1, 4, (1.0, 3.11, 2.0, 0.0), 108), (2, 4, (5.0, 3.0, 5.0, 0.0), None), (3, 4, None, None)],
                                      [(1, 4, (1.0, 3.11, 2.0, 0.0), 108), (2, 4, (6.0, 3.0, 5.0, 1.5), None),
                                       (3, 4, (0.0, 0.0, 0.0, 0.0), None), (4, 4, (9.0, 9.0, 9.0, 0.0), None)])
            _tw.npc_remotes = {}
            _qn = npcroster.npc_move_queue(_tw, _mv)
            _mrec = _tw.npc_remotes[2].pending[0]
            _mp = fmoworld.parse_move(_mrec[fmoworld.REC_HDR:])
            _lo7 = ((_blob.split(b"\0")[0] == b"selftestkey" if room.ROOM_PEER_KEY else True)
                    and _mv == [(2, (6.0, 3.0, 5.0, 1.5))] and _qn == 1
                    and _mp["pos"] == (6.0, 3.0, 5.0) and abs(_mp["rot"] - 1.5) < 1e-6
                    and _tw.npc_remotes[2].alias == 2)
            print(f"  NPC pop carries the stream key; a moved row queues cmd 240 on the NPC's own stream: "
                  f"{'OK' if _lo7 else 'FAIL ' + str((_blob, _mv, _qn))}")
            ok &= _lo7
            print(f"  a row's ckind overrides the pop record's client_kind: {'OK' if _lo5 else 'FAIL ' + str(npccast.NPC_CKIND_OVERRIDE)}")
            ok &= _lo5
            # the owner's own hangar: bay units per in-use setup, required keys named
            _blk = bytearray(inventory.REPLY_0166_LEN)
            _blk[1] = 1
            _blk[2 * inventory.SETUP_ENTRY_LEN + 1] = 1
            _bays = hangar.hangar_bays(141)
            hangar.HANGAR_RESIDENTS["selftest-owner"] = {"owner": 6, "slots": hangar.setups_in_use(bytes(_blk)), "mapno": 141}
            move.WORLD_PLACES["selftest-owner"] = (0, 5, 6)
            try:
                _ro, _nm, _why = npcroster.roster_for("selftest-owner")
            finally:
                move.WORLD_PLACES.pop("selftest-owner", None)
                hangar.HANGAR_RESIDENTS.pop("selftest-owner", None)
            _bayu = [e for e in _ro if (e[0] & 0xFFFFFF00) == 0x82080000]
            # WARNING: type 30 ON PURPOSE: the row must supply the POSITION and not
            # the body, or dragging the parked wanzer into place would turn it
            # back into whatever the editor's placer can build.
            npccast.NPC_LAYOUT.set_band("hangar", 141, [{"key": 0x82080002, "type": 30, "x": 1.0, "y": 0.87,
                                                         "z": 7.6, "face": 0, "cat": None, "label": None}])
            move.WORLD_PLACES["selftest-owner"] = (0, 5, 6)
            hangar.HANGAR_RESIDENTS["selftest-owner"] = {"owner": 6, "slots": [0, 2], "mapno": 141}
            try:
                _ro2 = npcroster.roster_for("selftest-owner")[0]
            finally:
                move.WORLD_PLACES.pop("selftest-owner", None)
                hangar.HANGAR_RESIDENTS.pop("selftest-owner", None)
            _lo9 = (move.PLACES is False) or ([e for e in _ro2 if e[0] == 0x82080002][0][2][:3] == (1.0, 0.87, 7.6)
                                              and len([e for e in _ro2 if e[0] == 0x82080002]) == 1
                                              # the body stays the server's, not the row's 30
                                              and [e for e in _ro2 if e[0] == 0x82080002][0][1] == hangar.HANGAR_BAY_TYPE
                                              and any(e[0] == 0x82080000 for e in _ro2))
            # both pins read the hangar's shipped floor plan (the bay boxes)
            _hp_skip = _fmodata_skip("floorplans/141.json")
            print(f"  a layout row for a bay key moves the bay unit and does NOT replace its body: "
                  f"{_hp_skip or ('OK' if _lo9 else 'FAIL ' + str(_ro2))}")
            if not _hp_skip:
                ok &= _lo9
            _lo8 = ((not move.PLACES) or (hangar.setups_in_use(bytes(_blk)) == [0, 2]
                                          and len(_bays) == 2 and _bays[0][1] > _bays[1][1]
                                          and [e[0] for e in _bayu] == [0x82080000, 0x82080002]
                                          and _bayu[0][2][0] == _bays[0][0] and _bayu[0][2][2] == _bays[0][1]
                                          and _bayu[0][1] == hangar.HANGAR_BAY_TYPE
                                          and "SHUT" in _why and "0x82080c00" in _why))
            print(f"  the owner's hangar pops a bay unit per in-use setup and names the missing consoles: "
                  f"{_hp_skip or ('OK' if _lo8 else 'FAIL ' + str((_bays, _bayu, _why)))}")
            if not _hp_skip:
                ok &= _lo8
            # KEY: THE PARKED WANZER. Parts ride a bay unit ONLY when it is a
            # wanzer class: types 4 and 30 never call the dresser, and
            # record_pop refuses parts on type 4 outright, so a knob set to 4
            # must not reach it with an array. A non-bay key never gets one.
            _bw4 = npcroster._bay_wanzer_parts(0x82080000, 4, "192.0.2.1")[0] is None
            _bw30 = npcroster._bay_wanzer_parts(0x82080000, 30, "192.0.2.1")[0] is None
            _bwnb = npcroster._bay_wanzer_parts(0x82081010, 0, "192.0.2.1")[0] is None
            _bwno = npcroster._bay_wanzer_parts(0x82080000, 0, None)[0] is None
            print(f"  parked wanzer: parts ride a bay unit only for a WANZER "
                  f"class -- type 4 no, type 30 no, a non-bay key no, no host "
                  f"no: {'OK' if _bw4 and _bw30 and _bwnb and _bwno else 'FAIL'}")
            ok &= _bw4 and _bw30 and _bwnb and _bwno
            npccast.NPC_LAYOUT.drop_band("hangar")
            npccast.NPC_LAYOUT.drop_band("hq")
            npcroster.band_rosters()
            _lo6 = npccast.NPC_CKIND_OVERRIDE == {}
            print(f"  ...and clears with the band: {'OK' if _lo6 else 'FAIL'}")
            ok &= _lo6
            _lo2 = npcroster.band_rosters()[1][1] == _b0[1][1]
            print(f"  dropping the band restores the env roster: {'OK' if _lo2 else 'FAIL'}")
            ok &= _lo2
            with open(_lp, "w", encoding="utf-8") as _fh:
                _fh.write("{not json")
            npccast.NPC_LAYOUT._mtime = None
            _lo3 = npcroster.band_rosters()[1][1] == _b0[1][1] and npccast._LAYOUT_ERR[0] is not None
            print(f"  a broken layout file is logged once and falls back to the env: "
                  f"{'OK' if _lo3 else 'FAIL'}")
            ok &= _lo3
        finally:
            npccast.NPC_LAYOUT.path, npccast.NPC_LAYOUT._mtime = _saved_lp, _saved_lm
            npccast._LAYOUT_ERR[0] = None
    # the Coliseum: its own catalogue rows, SE's 0|1 / k|k+17 pairing, a roster
    # whose every key is one the D44 event table lists, U.S.N. half recast
    _col_ok = (fmoworld.NPC_CATALOGUE.get(204, ("",))[0] == "Elliot"
               and fmoworld.NPC_CATALOGUE.get(233, ("",))[1] == "Kelly"
               and npccast.usn_counterpart(200) == 201 and npccast.usn_counterpart(204) == 221
               and npccast.usn_counterpart(216) == 233 and npccast.usn_counterpart(217) == 217)
    _d44 = ({0x82080900 + i for i in range(8)} | {0x82080910 + i for i in range(4)}
            | {0x82080920, 0x82080400, 0x82080200, 0x82080500, 0x82080600, 0x82080700,
               0x82080300, 0x82080110} | {0x82080710 + i for i in range(4)})
    _col_ok &= {u for u, _, _, _ in npccast.POP_NPC_COL} <= _d44 and len(npccast.POP_NPC_COL) == 6
    _col_ok &= all(p is not None and p[1] >= 16.0 for _, _, p, _ in npccast.POP_NPC_COL)
    _col_ok &= len({p[:3] for _, _, p, _ in npccast.POP_NPC_COL}) == 6     # nobody shares a chair
    _col_ok &= all(abs(p[3] + 1.571) < 0.01 for u, _, p, _ in npccast.POP_NPC_COL
                   if u in (0x82080900, 0x82080901))                # clerks face the walkway
    # WARNING: every desk must stand on a PLACED-NEVER-CREATED mark of AH/F99/D47 --
    # an ambient-cast mark is where the SCRIPT puts its own body (the 09-09
    # "body is in the desk"). These six are the script's server slots.
    _server_marks = {(29.40, 16.05, 28.70), (22.45, 16.05, 34.45), (21.30, 16.00, 17.20),
                     (1.30, 19.00, 8.25), (8.25, 19.00, 1.30), (42.00, 22.20, 0.00),
                     (3.60, 3.05, 1.10)}
    _col_ok &= all(tuple(round(v, 2) for v in p[:3]) in _server_marks
                   for _, _, p, _ in npccast.POP_NPC_COL)
    _cu = dict((u, c) for u, _, _, c in npccast.POP_NPC_COL_USN)
    _col_ok &= _cu[0x82080910] == 221 and _cu[0x82080110] == 201 and _cu[0x82080901] == 220
    _col_ok &= npccast.POP_NPC_COL_NAMES.get(0x82080910) == ("Coliseum", "Desk")
    print(f"  Coliseum: catalogue 200..235, pairing, one body per SERVER mark (6 desks), clerks face the walkway, U.S.N. recast: "
          f"{'OK' if _col_ok else 'FAIL'}")
    ok &= _col_ok
    # FMO_ZONE_MAPNO: exact zone beats kind beats the default; a missing map refuses
    _zt = areachange.parse_zone_mapno("4:101,407:124,600:161")
    _zm = [(areachange.zone_mapno(400, 102, "d", _zt), (101, "FMO_ZONE_MAPNO[kind 4]")),
           (areachange.zone_mapno(407, 102, "d", _zt), (124, "FMO_ZONE_MAPNO[407]")),
           (areachange.zone_mapno(600, 102, "d", _zt), (161, "FMO_ZONE_MAPNO[600]")),
           (areachange.zone_mapno(200, 102, "d", _zt), (102, "d")),
           (areachange.zone_mapno(None, 102, "d", _zt), (102, "d"))]
    _zm_ok = all(a == b for a, b in _zm)
    try:
        areachange.parse_zone_mapno("4:999")
        _zm_ok = False
    except SystemExit:
        pass
    print(f"  FMO_ZONE_MAPNO precedence + refusal of a missing map: "
          f"{'OK' if _zm_ok else 'FAIL ' + str(_zm)}")
    ok &= _zm_ok
    # the room key is (zone, map): same map, different zones -> not room-mates
    class _Ch:
        def __init__(self, addr):
            self.addr, self.key, self.left, self.seen_at = addr, b"k", None, time.time()
            self.remotes = {}
    _ra, _rb, _rc = ("selftest-za", 1), ("selftest-zb", 1), ("selftest-zc", 1)
    _saved = (dict(groupchannel.WORLD_PEERS), dict(rooms.WORLD_MAPS), dict(rooms.WORLD_ZONES), room.ROOM)
    try:
        groupchannel.WORLD_PEERS.clear()
        for _a in (_ra, _rb, _rc):
            groupchannel.WORLD_PEERS[_a] = _Ch(_a)
            rooms.WORLD_MAPS[_a[0]] = 102
        rooms.WORLD_ZONES[_ra[0]], rooms.WORLD_ZONES[_rb[0]], rooms.WORLD_ZONES[_rc[0]] = 200, 400, 200
        flat_globals()["ROOM"] = True
        _mates = [c.addr[0] for c in rooms.room_mates(groupchannel.WORLD_PEERS[_ra])]
        _rz_ok = (_mates == [_rc[0]]) if room.ROOM_SAME_ZONE else (sorted(_mates) == sorted([_rb[0], _rc[0]]))
    finally:
        groupchannel.WORLD_PEERS.clear(); groupchannel.WORLD_PEERS.update(_saved[0])
        rooms.WORLD_MAPS.clear(); rooms.WORLD_MAPS.update(_saved[1])
        rooms.WORLD_ZONES.clear(); rooms.WORLD_ZONES.update(_saved[2])
        flat_globals()["ROOM"] = _saved[3]
    print(f"  room key is (zone, MapNo) -- zone 200 and 400 on map 102 do not "
          f"relay: {'OK' if _rz_ok else 'FAIL ' + str(_mates)}")
    ok &= _rz_ok
    _hp = bytes(0xE4); _hp = b"6264" + _hp[4:0x10] + b"old" + _hp[0x13:]
    _hp_ok = move.parse_hangar_pw(_hp) == ("6264", "old") and move.parse_hangar_pw(b"") == ("", "")
    _hf = {off: val for _l, off, val, _s in status.status_fields(char={"hangar_password": "6264"})}
    _hp_ok &= _hf.get(status.S14A_HANGAR_PW) == b"6264\0" and status.S14A_HANGAR_PW == 0x738
    _hp_ok &= status.S14A_HANGAR_PW not in {off for _l, off, _v, _s in status.status_fields(char={})}
    print(f"  0x0170 hangar password: parsed (+0x00 new, +0x10 current), served back at "
          f"+0x738 only when stored: {'OK' if _hp_ok else 'FAIL'}")
    ok &= _hp_ok
    _hq = bytearray(move.MOVE_REQ_LEN)
    _hq[move.M16D_NUMBER:move.M16D_NUMBER + 4] = b"6264"
    _hq[move.M16D_NAME_A:move.M16D_NAME_A + 5] = b"Molly"
    _hq[move.M16D_NAME_B:move.M16D_NAME_B + 5] = b"Arden"
    _hd_ok = (hangar.hangar_request(bytes(_hq)) == ("6264", "Molly", "Arden")
              and hangar.hangar_request(bytes(move.MOVE_REQ_LEN)) == ("", "", "")
              and hangar.hangar_place(6) == (0, 5, 6) and move.place_map(0, 5)[0] == move.ROOM_MAPS[5])
    _saved_hp = move.WORLD_PLACES.get("selftest-hangar")
    try:
        move.WORLD_PLACES["selftest-hangar"] = hangar.hangar_place(6)
        _hd_ok &= npcroster.roster_for("selftest-hangar")[0] == [] if move.PLACES else True
    finally:
        move.WORLD_PLACES.pop("selftest-hangar", None)
        if _saved_hp is not None:
            move.WORLD_PLACES["selftest-hangar"] = _saved_hp
    print(f"  hangar door: 0x016D row 0 parses (password, names), place (0, 5, owner id), "
          f"map {move.ROOM_MAPS[5]}, no cast: {'OK' if _hd_ok else 'FAIL'}")
    ok &= _hd_ok
    # FMO_HQ_MARKS: the five people move onto D87's server slots with their
    # facings; the consoles do not move; the sixth slot stays empty.
    _sm_in = [(0x82080100, 4, (9.9, 9.9, 9.9, 0.0), 115),
              (0x82080400, 4, (1.5, 3.5, 2.53, 0.0), 109),
              (0x82081020, 4, (8.8, 8.8, 8.8, 0.0), 102)]
    _sm = {u: (p, c) for u, _t, p, c in npccast.se_marked(_sm_in)}
    _sm_ok = (_sm[0x82080100][0][:3] == (0.45, 3.05, 1.15)
              and _sm[0x82080400][0] == (1.5, 3.5, 2.53, 0.0)      # a console never moves
              and _sm[0x82081020][0][:3] == (22.49, 3.05, 37.50)
              and _sm[0x82080100][1] == 115 and _sm[0x82081020][1] == 102)
    _sm_ok &= abs(npccast.face_radians(270) - npccast.FACE_SIGN * -1.571) < 0.002
    _sm_ok &= abs(npccast.face_radians(90) - npccast.FACE_SIGN * 1.571) < 0.002
    _sm_ok &= npccast.face_radians(None) == 0.0 and abs(npccast.face_radians(225) - npccast.FACE_SIGN * -2.356) < 0.002
    _sm_ok &= all(abs(v) <= 32.767 for v in (npccast.face_radians(d) for d in (0, 90, 180, 225, 270, 359)))
    _sm_ok &= (23.50, 3.05, 35.35) not in {m[:3] for m in npccast.HQ_SE_MARKS.values()}
    _sm_ok &= npccast.se_marked([]) == [] and len(npccast.se_marked(_sm_in)) == 3
    print(f"  FMO_HQ_MARKS: the five people take D87's server slots + facings, consoles stay, "
          f"the 6th slot is empty (armed: {npccast.HQ_MARKS}): {'OK' if _sm_ok else 'FAIL'}")
    ok &= _sm_ok
    _lp = charlist.list_entry(7, "A", "B", 2, nation_byte=1)
    _lp_ok = _lp[0x26] == (charlist.NATION or 2) and _lp[0x28] == 1 and charlist.list_entry(7, "A", "B")[0x28] == 0
    print(f"  character list carries the nation at +0x28 (gender stays at +0x26): "
          f"{'OK' if _lp_ok else 'FAIL'}")
    ok &= _lp_ok
    _zh_ok = all(zoneentry.faction_mapkind(zoneentry.ZONE_HOME_BY_KIND[k][0], 2) == zoneentry.ZONE_HOME_BY_KIND[k + 2][1]
                 for k in (1, 2))
    print(f"  SE's per-kind home table agrees with faction_mapkind (100->300, 200->400): "
          f"{'OK' if _zh_ok else 'FAIL'}")
    ok &= _zh_ok
    _nb = {off: val for _l, off, val, _s in status.status_fields(nation=2)}
    _sn_ok = _nb.get(status.S14A_NATION) == b"\x02"
    print(f"  0x014A nation byte from the pilot: {'OK' if _sn_ok else 'FAIL'}")
    ok &= _sn_ok
    _ns = zoneentry.nation_for_session({"nation_byte": 2}, 1, "knob")
    _nf = zoneentry.nation_for_session({}, 1, "knob")
    _ns_ok = (_ns[0] == 2 and _nf == (1, "knob")) if zoneentry.NATION_PER_CHARACTER \
        else (_ns == (1, "knob"))
    print(f"  nation_for_session: {'OK' if _ns_ok else 'FAIL ' + str((_ns, _nf))}")
    ok &= _ns_ok

    # PROGRESSION (2026-09-08): the Sakata -> induction loop, on a table
    # literal so the pin cannot drift with the served tsv, then the served
    # tsv itself must contain that loop.
    _tbl = {1: [
        # the early chain, with bytes (2026-09-11): a pilot whose Enemy
        # Prototype byte is 99 has these three DONE by implication, not open
        {"title": "Enemy Unit Annihilation", "level": 6,
         "pre": None, "own": 130, "pre_byte": None},
        {"title": "Guide the Special EMP Carrier", "level": 9,
         "pre": "Enemy Unit Annihilation", "own": 137, "pre_byte": 130},
        {"title": "Enemy Heavy Combat Helicopter Incursion", "level": 13,
         "pre": "Guide the Special EMP Carrier", "own": None, "pre_byte": 137},
        {"title": "Enemy Prototype Weapon Sighted", "level": 16,
         "pre": "Enemy Heavy Combat Helicopter Incursion", "own": 135, "pre_byte": None},
        {"title": "Defend Sakata Industry", "level": 24,
         "pre": "Enemy Prototype Weapon Sighted", "own": 173, "pre_byte": 135},
        {"title": "Special Mobile Force Induction Test", "level": 29,
         "pre": "Defend Sakata Industry", "own": 177, "pre_byte": 173},
        {"title": "Lure the Large Mobile Weapon", "level": 23,
         "pre": None, "own": 169, "pre_byte": None}], 2: []}
    # 2026-09-30: the level test is the PILOT level (class 12 exp), not rank
    _pr_ok = bool(fmostore) and bool(classes.CLASS_CURVE)
    if fmostore and classes.CLASS_CURVE:
        _c = {"nation_byte": 1, "rank": 24,
              "flags": fmostore.flags_hex(byte_values={128: 99, 135: 99})}
        # rank alone opens nothing: at Pilot level 1 no mission past Lv 1 is open
        _pr_ok &= progress.progress_frontier(_c, _tbl) == []
        progress.set_pilot_level(_c, 24)
        # the sortie's tile names the mission: Sakata's tile picks Sakata even
        # with Lure open beside it; a tile that is no open mission's completes
        # nothing; neither touches the record it does not complete
        _tiles = {173: {50001}, 169: {50002}}
        _pr_ok &= [m["title"] for m in progress.progress_frontier(_c, _tbl, 50001, _tiles)] \
            == ["Defend Sakata Industry"]
        import copy as _copy
        _ct = _copy.deepcopy(_c)
        _m, _what, _ra = progress.advance_progress(_ct, _tbl, tile=50003, tiles=_tiles)
        _pr_ok &= _m is None and "no open mission" in _what and _ct["flags"] == _c["flags"]
        _m, _what, _ra = progress.advance_progress(_ct, _tbl, tile=50001, tiles=_tiles)
        _pr_ok &= bool(_m) and _m["title"] == "Defend Sakata Industry" \
            and progress.pilot_level(_ct) == 29
        _pr_ok &= progress.progress_done(_c, _tbl) >= {
            "Enemy Prototype Weapon Sighted", "Enemy Heavy Combat Helicopter Incursion",
            "Guide the Special EMP Carrier", "Enemy Unit Annihilation"}
        _f0 = [m["title"] for m in progress.progress_frontier(_c, _tbl)]
        # Lure (Lv23, no prerequisite) is ALSO open at rank 24 -> ambiguous,
        # and ambiguity must refuse rather than pick; Annihilation / EMP are
        # NOT open -- implied done by the prototype byte
        _pr_ok &= sorted(_f0) == ["Defend Sakata Industry", "Lure the Large Mobile Weapon"]
        _m, _what, _ra = progress.advance_progress(_c, _tbl)
        _pr_ok &= _m is None and _what.startswith("ambiguous")
        # close Lure by hand, then the loop must run: Sakata done, rank 29
        fmostore.set_flag_byte(_c, 169, 99)
        _m, _what, _ra = progress.advance_progress(_c, _tbl)
        _fl = fmostore.flags_bytes(_c["flags"])
        _pr_ok &= (_m and _m["title"] == "Defend Sakata Industry"
                   and _fl[173] == 99 and _ra == 29 and _c["rank"] == 29
                   and progress.pilot_level(_c) == 29)
        _pr_ok &= [m["title"] for m in progress.progress_frontier(_c, _tbl)] == \
            ["Special Mobile Force Induction Test"]
        # a default pilot (rank 21, only 128=99) has exactly the campaign's
        # FIRST mission at the frontier (2026-09-11: its byte is known now;
        # before that, nothing was open for a fresh pilot)
        _d = {"nation_byte": 1, "rank": 21,
              "flags": fmostore.flags_hex(byte_values={128: 99})}
        progress.set_pilot_level(_d, 21)
        _pr_ok &= [m["title"] for m in progress.progress_frontier(_d, _tbl)] == \
            ["Enemy Unit Annihilation"]
    print(f"  progression frontier/advance by Pilot level (Sakata -> induction, "
          f"ambiguity refuses, the sortie's tile picks the mission): "
          f"{'OK' if _pr_ok else 'FAIL'}")
    ok &= _pr_ok
    # the served table carries the same loop -- read from the mission
    # catalogue file, which is built from the client
    _srv_skip = _fmodata_skip("fmo-missions.tsv")
    _srv = {m["title"]: m for m in progress.MISSIONS.get(1, [])}
    _srv_ok = (_srv.get("Defend Sakata Industry", {}).get("own") == 173
               and _srv.get("Defend Sakata Industry", {}).get("pre_byte") == 135
               and _srv.get("Special Mobile Force Induction Test", {}).get("level") == 29
               # 2026-09-11: the inferred bytes that carry the chain on
               and _srv.get("Special Mobile Force Induction Test", {}).get("own") == 177
               and _srv.get("Escort the Transport", {}).get("own") == 181
               # 2026-09-30, from the sortie tiles (fmo-gates.json): 171, not 167
               and _srv.get("Repel the Enemy Incursion", {}).get("own") == 171
               and _srv.get("Escort the PMO Inspector", {}).get("own") == 167)
    print(f"  progression: the served table (fmo-missions.tsv) carries the same "
          f"loop (Sakata own 173 / pre 135, induction Lv29 own 177, Escort own "
          f"181): {_srv_skip or ('OK' if _srv_ok else 'FAIL')}")
    if not _srv_skip:
        ok &= _srv_ok

    # The version packet captured 2026-08-17: rebuild it from its fields and
    # assert the checksum the real client put on the wire. If build() and
    # checksum() are both right, this can only come out 0x03F2.
    payload = struct.pack("<I", 0) + b"W20_20260813_1" + b"\x00" * 22
    pkt = packet.build(handshake.MSG_VERSION, payload, seq=0x1001, conn_id=0)
    want = 0x03F2
    got = struct.unpack_from("<H", pkt, packet.CHK_OFF)[0]
    print(f"  version packet: len={len(pkt)} chk=0x{got:04X} want=0x{want:04X} "
          f"{'OK' if got == want else 'FAIL'}")
    ok &= (got == want) and len(pkt) == 60

    # A sealed packet must satisfy the client's own verifier.
    for msg, pl in ((handshake.MSG_HANDSHAKE_OK, b""), (handshake.MSG_VERSION, payload),
                    (0x1234, b"\xaa" * 16)):
        p = packet.parse(packet.build(msg, pl, seq=0x1005, conn_id=0x0042))
        print(f"  seal/verify msg=0x{msg:04X}: "
              f"{'OK' if p['chk_ok'] else 'FAIL'}")
        ok &= p["chk_ok"]

    # Zeroing four bytes instead of two is the trap; prove it actually differs,
    # so this test fails if someone 'simplifies' checksum() back to a u32.
    b = bytearray(pkt)
    b[packet.CHK_OFF:packet.CHK_OFF + 4] = b"\x00" * 4
    wrong = sum(b) & 0xFFFF
    print(f"  u32-zeroing gives 0x{wrong:04X}, short by "
          f"{(got - wrong) & 0xFFFF} = bytesum(+0x06) "
          f"{'OK' if wrong != got else 'FAIL -- the trap is not reproduced'}")
    ok &= wrong != got

    # 0x014A, the player status block. The client copies three windows out of
    # this payload straight into the lobby, and the last of them ENDS at the
    # payload's last byte -- so an off-by-one in any of these constants is a read
    # past the end on the client's side, not ours. Assert the arithmetic the
    # copies at 0x6117B30F..0x6117B391 imply, and assert rank lands where
    # 0x6108B8B5 reads it.
    windows = ((status.S14A_BLOCK, status.S14A_BLOCK_LEN, "lobby+0x88C"),
               (status.S14A_BLOCK2, status.S14A_BLOCK2_LEN, "lobby+0xF08"),
               (status.S14A_BLOCK3, status.S14A_BLOCK3_LEN, "lobby+0xFD4"))
    for off, ln, dest in windows:
        fits = off + ln <= status.REPLY_014A_LEN
        print(f"  0x014A window +0x{off:03X}+{ln} -> {dest}: "
              f"{'OK' if fits else 'FAIL -- runs past the payload'}")
        ok &= fits
    end = max(off + ln for off, ln, _ in windows)
    _fb = status.reply_014a(rank=0, flags=[173, 183])
    _fl_ok = (_fb[status.S14A_FLAGS11 + 0x15] == 0x20 and _fb[status.S14A_FLAGS11 + 0x16] == 0x80
              and status.S14A_FLAGS11 == 0x304
              and status.reply_014a(rank=0, flags=[], active_setup=0, class_table=False)
              == bytes(status.REPLY_014A_LEN))
    print(f"  0x014A: FMO_STATUS_FLAGS 173,183 set payload byte 0x319 bit 5 and "
          f"0x31A bit 7 (script-flag kind 11 = lobby+0xB88), nothing else: "
          f"{'OK' if _fl_ok else 'FAIL'}")
    ok &= _fl_ok
    print(f"  0x014A length {status.REPLY_014A_LEN} == last window's end {end}: "
          f"{'OK' if end == status.REPLY_014A_LEN else 'FAIL'}")
    ok &= end == status.REPLY_014A_LEN

    # rank at lobby+0x8BB means S14A_BLOCK + (0x8BB - 0x88C).
    want_rank = status.S14A_BLOCK + (0x8BB - 0x88C)
    print(f"  0x014A rank offset +0x{status.S14A_RANK:X} == +0x{want_rank:X} "
          f"(lobby+0x88C+0x2F): {'OK' if status.S14A_RANK == want_rank else 'FAIL'}")
    ok &= status.S14A_RANK == want_rank

    # The ACTIVE WANZER SETUP is lobby+0x8B7 (0x6117B761 reads it and hands it
    # to 0x6117A770, the only writer of lobby+0x3DF2). Zero is not neutral: it
    # also zeroes setup 1's IN-USE byte, which is how "-Empty-" ended up Active
    # over a Setup1 that held a complete Giza.
    want_act = status.S14A_BLOCK + (0x8B7 - 0x88C)
    act_ok = status.S14A_ACTIVE_SETUP == want_act
    print(f"  0x014A active-setup offset +0x{status.S14A_ACTIVE_SETUP:X} == "
          f"+0x{want_act:X} (lobby+0x88C+0x2B): {'OK' if act_ok else 'FAIL'}")
    ok &= act_ok

    # It must actually reach the wire as 1, and it must sit strictly between
    # SEX (+0x26) and RANK (+0x2F) -- a field that collides with either would
    # silently change the pilot's gender or rank instead.
    _fields = dict((off, val) for _n, off, val, _src in status.status_fields())
    act_on = _fields.get(status.S14A_ACTIVE_SETUP) == b""
    gap_ok = status.S14A_SEX < status.S14A_ACTIVE_SETUP < status.S14A_RANK
    print(f"  0x014A serves active setup 1 at +0x{status.S14A_ACTIVE_SETUP:X}, "
          f"between SEX and RANK: {'OK' if (act_on and gap_ok) else 'FAIL'}")
    ok &= act_on and gap_ok

    # The payload must be the block plus rank, the acknowledged rank that has to
    # MATCH it (the E316 orders gate, 2026-09-06), and NOTHING else -- a zero
    # anywhere else is the zero the client already has, and that is the whole
    # safety argument.
    body = status.reply_014a(rank=21, class_table=False)   # the class table is pinned by name
    # 2026-09-08: the active wanzer setup rides with rank -- serving zero is
    # not neutral (it un-marks setup 1 IN USE), so it is a NAMED field now.
    _named = (status.S14A_RANK, status.S14A_BE1A, status.S14A_ACTIVE_SETUP)
    others = bytes(b for i, b in enumerate(body) if i not in _named)
    if status_block_pristine():
        clean = (len(body) == status.REPLY_014A_LEN and body[status.S14A_RANK] == 21
            and body[status.S14A_BE1A] == 21
            and body[status.S14A_ACTIVE_SETUP] == 1
            and others == bytes(len(others)))
        print(f"  0x014A payload: {len(body)}B, rank byte {body[status.S14A_RANK]}, "
              f"ack-rank byte {body[status.S14A_BE1A]} (must equal rank), "
              f"{'all other bytes zero' if others == bytes(len(others)) else 'DIRTY'}"
              f": {'OK' if clean else 'FAIL'}")
        ok &= clean
    else:
        _set = [k for k in STATUS_ENV_KNOBS if os.environ.get(k)]
        clean = len(body) == status.REPLY_014A_LEN and body[status.S14A_RANK] == 21
        print(f"  0x014A payload: {len(body)}B, rank byte {body[status.S14A_RANK]} "
              f"-- WARNING: the all-other-bytes-zero check is SKIPPED because "
              f"{_set} {'is' if len(_set) == 1 else 'are'} set on purpose: "
              f"{'OK' if clean else 'FAIL'}")
        ok &= clean

    # And the knob must default OFF. This one is a policy assertion: turning it
    # on serves 2,059 bytes we have not decoded, and it has never been watched.
    print(f"  0x014A is OFF by default: "
          f"{'OK' if os.environ.get('FMO_START_STATUS') else 'OK (unset)'}")

    # 0x01C1, the mission block (mission-block worker, 2026-08-26). The parse
    # 0x61173140 copies 0x352 dwords from payload+0x18 and reads a u32 at
    # +0xD60, so the length is pinned two ways; each named offset is pinned to
    # the lobby address its READER uses (block base lobby+0x5C7E).
    c_len = missionblock.REPLY_01C1_LEN == lobapi.LOBAPI[missionblock.MSG_MISSION_REQ][1] == missionblock.M1C1_TAIL_U32 + 4 \
        and missionblock.M1C1_BLOCK + missionblock.M1C1_BLOCK_LEN == missionblock.M1C1_TAIL_U32
    print(f"  0x01C1 length {missionblock.REPLY_01C1_LEN} == LOBAPI need == 0x{missionblock.M1C1_TAIL_U32:X}+4, "
          f"block +0x{missionblock.M1C1_BLOCK:X}+{missionblock.M1C1_BLOCK_LEN} ends there: "
          f"{'OK' if c_len else 'FAIL'}")
    ok &= c_len
    c_off = (0x5C7E + missionblock.MB_MAPNO == 0x5C7E and 0x5C7E + missionblock.MB_LEADERID == 0x5CEE
             and 0x5C7E + missionblock.MB_BGCOSTMAX == 0x5CF2 and 0x5C7E + missionblock.MB_NPCMAX == 0x5C86
             and 0x5C7E + missionblock.MB_START_GAMETIME == 0x5CC6
             and lobapi.LOBAPI[missionblock.MSG_MISSION_REQ][0] == missionblock.MSG_MISSION_REPLY)
    print(f"  0x01C1 offsets: MapNo->0x5C7E (0x61004C99), LeaderID->0x5CEE "
          f"(0x610DC6B8), BGCostMax->0x5CF2 (0x610CB1BB), NpcMax->0x5C86 "
          f"(0x610CB108), GameTime->0x5CC6 (0x611612CF): {'OK' if c_off else 'FAIL'}")
    ok &= c_off
    # Default = byte-identical to what the generic arm has always sent.
    zero = missionblock.reply_01c1(leader="", mapno=0, bgcostmax="", totalbgcost="",
                                   battleticket="")
    c_zero = zero == bytes(missionblock.REPLY_01C1_LEN) and not missionblock.mission_fields(
        leader="", mapno=0, bgcostmax="", totalbgcost="", battleticket="")
    print(f"  0x01C1 default is the generic arm's {missionblock.REPLY_01C1_LEN} zeros, no "
          f"field on: {'OK' if c_zero else 'FAIL'}")
    ok &= c_zero
    # One field on: it lands at block+offset, everything else stays zero.
    body = missionblock.reply_01c1(leader="0x820C1080", mapno=38, bgcostmax="3,7",
                                   totalbgcost="", battleticket="")
    at = missionblock.M1C1_BLOCK
    got_l = struct.unpack_from("<I", body, at + missionblock.MB_LEADERID)[0]
    got_m = struct.unpack_from("<I", body, at + missionblock.MB_MAPNO)[0]
    got_b = body[at + missionblock.MB_BGCOSTMAX:at + missionblock.MB_BGCOSTMAX + 2]
    scrub = bytearray(body)
    for off, n in ((missionblock.MB_LEADERID, 4), (missionblock.MB_MAPNO, 4), (missionblock.MB_BGCOSTMAX, 2)):
        scrub[at + off:at + off + n] = bytes(n)
    c_one = (got_l == 0x820C1080 and got_m == 38 and got_b == b"\x03\x07"
             and bytes(scrub) == bytes(missionblock.REPLY_01C1_LEN)
             and len(body) == missionblock.REPLY_01C1_LEN)
    print(f"  0x01C1 LeaderID/MapNo/BGCostMax land at +0x{at + missionblock.MB_LEADERID:X}/"
          f"+0x{at + missionblock.MB_MAPNO:X}/+0x{at + missionblock.MB_BGCOSTMAX:X}, every other byte "
          f"zero: {'OK' if c_one else 'FAIL'}")
    ok &= c_one
    # 'self' names the popped unit, and only when a POP exists.
    lid, src = missionblock.mission_leader_id("self")
    c_self = (popsweep.POP and lid == popsweep.POP[0]) or (not popsweep.POP and lid is None)
    print(f"  0x01C1 LeaderID 'self' -> {lid if lid is None else hex(lid)} "
          f"({src}): {'OK' if c_self else 'FAIL'}")
    ok &= bool(c_self)
    # The crash gate: a MapNo with no file never reaches the wire.
    off_disk = next(i for i in range(1, 512) if i not in missionlist.TYPE1_ON_DISK)
    refused = missionblock.reply_01c1(leader="", mapno=off_disk, bgcostmax="",
                                      totalbgcost="", battleticket="")
    notes = [s for l, o, r, s in missionblock.mission_fields(leader="", mapno=off_disk,
                                                             bgcostmax="", totalbgcost="",
                                                             battleticket="") if not r]
    c_gate = (refused == bytes(missionblock.REPLY_01C1_LEN) and len(notes) == 1
              and "REFUSED" in notes[0] and len(missionlist.TYPE1_ON_DISK) == 281
              and {0, 38, 107, 200} <= missionlist.TYPE1_ON_DISK and 999 not in missionlist.TYPE1_ON_DISK)
    print(f"  0x01C1 MapNo {off_disk} (no file) is REFUSED and left zero; "
          f"TYPE1_ON_DISK holds 281 ids incl. 0/38/107/200: "
          f"{'OK' if c_gate else 'FAIL'}")
    ok &= c_gate
    print(f"  0x01C1 is OFF by default (FMO_MISSION_BLOCK): "
          f"{'OK' if os.environ.get('FMO_MISSION_BLOCK') else 'OK (unset)'}")

    # 0x018E, the MISSION LIST (static RE 2026-09-04). The geometry is pinned
    # three ways -- the LOBAPI need, the parse's rep movsd count, and the
    # renderer's stride -- and they have to agree exactly or the record we fill
    # is not the record the client reads.
    l_len = (missionlist.REPLY_018E_LEN == lobapi.LOBAPI[missionlist.MSG_MISSION_LIST_REQ][1]
             == missionlist.M18E_RECORDS + missionlist.M18E_REC_COUNT * missionlist.M18E_REC_LEN
             and missionlist.M18E_REC_COUNT * missionlist.M18E_REC_LEN == 0x164D * 4
             and lobapi.LOBAPI[missionlist.MSG_MISSION_LIST_REQ][0] == missionlist.MSG_MISSION_LIST_REPLY
             and missionlist.M18E_REC_LEN % 4 == 0)
    print(f"  0x018E length {missionlist.REPLY_018E_LEN} == LOBAPI need == 0x{missionlist.M18E_RECORDS:X}"
          f"+{missionlist.M18E_REC_COUNT}x0x{missionlist.M18E_REC_LEN:X}, and {missionlist.M18E_REC_COUNT}x"
          f"0x{missionlist.M18E_REC_LEN:X} == 0x164D dwords (0x611725D6): "
          f"{'OK' if l_len else 'FAIL'}")
    ok &= l_len
    # Default = byte-identical to what the generic arm has always sent.
    l_zero = (missionlist.reply_018e(mode=0, rows="", fields="") == bytes(missionlist.REPLY_018E_LEN)
              and not missionlist.mission_list_rows("") and not missionlist.mission_list_fields(""))
    print(f"  0x018E default is the generic arm's {missionlist.REPLY_018E_LEN} zeros, no "
          f"row on: {'OK' if l_zero else 'FAIL'}")
    ok &= l_zero
    # Two rows: each gets a NON-ZERO key (or the row draws 21:30 "None") and its
    # name at ML_NAME; nothing else moves.
    body = missionlist.reply_018e(mode=1, rows="Recon Alpha|Sector Sweep", fields="")
    r0, r1 = missionlist.M18E_RECORDS, missionlist.M18E_RECORDS + missionlist.M18E_REC_LEN
    scrub = bytearray(body)
    for rec, n in ((r0, 1), (r1, 2)):
        struct.pack_into("<I", scrub, rec + missionlist.ML_KEY, 0)
        scrub[rec + missionlist.ML_NAME:rec + missionlist.ML_NAME + missionlist.ML_NAME_MAX] = bytes(missionlist.ML_NAME_MAX)
    struct.pack_into("<I", scrub, missionlist.M18E_MODE, 0)
    l_rows = (struct.unpack_from("<I", body, r0 + missionlist.ML_KEY)[0] == 1
              and struct.unpack_from("<I", body, r1 + missionlist.ML_KEY)[0] == 2
              and body[r0 + missionlist.ML_NAME:r0 + missionlist.ML_NAME + 12] == b"Recon Alpha\x00"
              and body[r1 + missionlist.ML_NAME:r1 + missionlist.ML_NAME + 13] == b"Sector Sweep\x00"
              and struct.unpack_from("<I", body, missionlist.M18E_MODE)[0] == 1
              and bytes(scrub) == bytes(missionlist.REPLY_018E_LEN)
              and len(body) == missionlist.REPLY_018E_LEN)
    print(f"  0x018E rows land at +0x{r0 + missionlist.ML_KEY:X}/+0x{r1 + missionlist.ML_KEY:X} with a "
          f"non-zero key and a name at +0x{missionlist.ML_NAME:X}, every other byte zero: "
          f"{'OK' if l_rows else 'FAIL'}")
    ok &= l_rows
    # A name is cut to fit ML_NAME_MAX-1 + NUL: 0x134 is the NEXT field the
    # renderer reads (0x611CCB27), so a long name must not run into it.
    long_body = missionlist.reply_018e(mode=0, rows="X" * 40, fields="")
    tail = long_body[r0 + missionlist.ML_NAME:r0 + missionlist.ML_NAME + missionlist.ML_NAME_MAX]
    l_cut = (tail == b"X" * (missionlist.ML_NAME_MAX - 1) + b"\x00"
             and long_body[r0 + 0x134:r0 + 0x138] == bytes(4)
             and len(missionlist.mission_list_rows("|".join(["m"] * 99))) == missionlist.M18E_REC_COUNT)
    print(f"  0x018E name cut to {missionlist.ML_NAME_MAX - 1}+NUL (0x134 stays clear) and "
          f"rows capped at {missionlist.M18E_REC_COUNT}: {'OK' if l_cut else 'FAIL'}")
    ok &= l_cut
    # Raw pokes land at records base + row*stride + offset, and beat the row
    # default (so ML_KEY can be cleared on purpose).
    poked = missionlist.reply_018e(mode=0, rows="a|b", fields="1:0x2C=1200, 0:0x0=0")
    l_poke = (struct.unpack_from("<I", poked, r1 + 0x2C)[0] == 1200
              and struct.unpack_from("<I", poked, r0 + missionlist.ML_KEY)[0] == 0
              and struct.unpack_from("<I", poked, r1 + missionlist.ML_KEY)[0] == 2)
    print(f"  0x018E raw poke '1:0x2C=1200' lands at +0x{r1 + 0x2C:X} and a "
          f"poke beats the row default: {'OK' if l_poke else 'FAIL'}")
    ok &= l_poke
    # Junk is refused rather than written into the neighbouring record.
    l_rej = 0
    for bad in ("33:0x00=1", "0:0x2B2=1", "0:-4=1", "0x0=1", "0:0x00", "z:0=1"):
        try:
            missionlist.mission_list_fields(bad)
        except ValueError:
            l_rej += 1
    print(f"  0x018E rejects 6 malformed FMO_MISSION_LIST_FIELDS specs: "
          f"{l_rej}/6 {'OK' if l_rej == 6 else 'FAIL'}")
    ok &= l_rej == 6
    # The ARMED env-parse path, which is the one that cannot fail: an unset knob
    # never enters it, so an unset-only test proves nothing (the empty-env trap --
    # a ':-' empty default fed int("",0) and crash-looped this container once).
    _saved = {k: os.environ.get(k) for k in
              ("FMO_MISSION_LIST", "FMO_MISSION_LIST_MODE",
               "FMO_MISSION_LIST_ROWS", "FMO_MISSION_LIST_FIELDS")}
    l_env = True
    try:
        for vals, want_on, want_mode in (
                ({"FMO_MISSION_LIST": "", "FMO_MISSION_LIST_MODE": "",
                  "FMO_MISSION_LIST_ROWS": "", "FMO_MISSION_LIST_FIELDS": ""},
                 False, 0),
                ({"FMO_MISSION_LIST": "1", "FMO_MISSION_LIST_MODE": "3",
                  "FMO_MISSION_LIST_ROWS": " Recon | Sweep ",
                  "FMO_MISSION_LIST_FIELDS": "0:0x20=7"}, True, 3)):
            os.environ.update(vals)
            on = (os.environ.get("FMO_MISSION_LIST", "").strip() or "0") != "0"
            mode = int(os.environ.get("FMO_MISSION_LIST_MODE", "").strip()
                       or "0", 0)
            rows = missionlist.mission_list_rows(
                os.environ["FMO_MISSION_LIST_ROWS"].strip())
            pokes = missionlist.mission_list_fields(
                os.environ["FMO_MISSION_LIST_FIELDS"].strip())
            l_env &= on is want_on and mode == want_mode
            if want_on:
                # Names are stripped, and the armed builder still round-trips.
                armed = missionlist.reply_018e(mode=mode, rows=" Recon | Sweep ",
                                               fields="0:0x20=7")
                l_env &= (rows == [(0, "Recon"), (1, "Sweep")]
                          and pokes == {(0, 0x20): 7}
                          and len(armed) == missionlist.REPLY_018E_LEN
                          and armed[missionlist.M18E_RECORDS + missionlist.ML_NAME:
                                    missionlist.M18E_RECORDS + missionlist.ML_NAME + 6] == b"Recon\x00"
                          and struct.unpack_from(
                              "<I", armed, missionlist.M18E_RECORDS + 0x20)[0] == 7)
            else:
                l_env &= rows == [] and pokes == {}
    except Exception as exc:                       # an import-time crash-loop
        l_env = False
        print(f"    (raised {exc!r})")
    finally:
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print(f"  0x018E env-parse ARMED and EMPTY ('' reads as OFF, not ON; "
          f"int('') never raises): {'OK' if l_env else 'FAIL'}")
    ok &= bool(l_env)
    print(f"  0x018E is OFF by default (FMO_MISSION_LIST): "
          f"{'OK' if os.environ.get('FMO_MISSION_LIST') else 'OK (unset)'}")
    for k in ("FMO_MISSION_LEADER", "FMO_MISSION_MAPNO", "FMO_MISSION_BGCOSTMAX",
              "FMO_MISSION_TOTALBGCOST", "FMO_MISSION_BATTLETICKET"):
        if os.environ.get(k):
            print(f"  WARNING: {k}={os.environ[k]!r} is set in this environment")

    # THE SORTIE 0x0139 -> 0x013A (sortie worker, 2026-08-27). Lengths and
    # offsets are pinned to the READER addresses in the MSG_SORTIE_REQ note.
    s_len = (sortie.REPLY_013A_LEN == sortie.R13A_INFO88 + sortie.R13A_INFO88_LEN == 0xDCC
             and sortie.R13A_BLOCK + missionblock.M1C1_BLOCK_LEN == sortie.R13A_INFO88   # block ends at +0xD74
             and sortie.R13A_BLOCK == 0x2C and sortie.R13A_INFO88 == 0xD74
             and sortie.R13A_ENDPOINT == 0 and addressing.ENDPOINT_LEN == 0x14
             and sortie.REQ_0139_LEN == 0x50 and sortie.Q139_AREA + 4 <= sortie.REQ_0139_LEN
             and sortie.Q139_CHAR48 + sortie.Q139_CHAR48_LEN == sortie.Q139_AREA)
    print(f"  0x013A is {sortie.REPLY_013A_LEN}B: endpoint +0x00 (0x61006270), block "
          f"+0x2C..+0xD73 (0x6117BC8D), 88B at +0xD74 (0x6117BC7B); 0x0139 is "
          f"80B with +0x40 last (0x61173C84): {'OK' if s_len else 'FAIL'}")
    ok &= s_len
    # MapNo sits where 0x61004C99 reads it: block+0x000 -> lobby+0x5C7E.
    s_off = (sortie.R13A_BLOCK + missionblock.MB_MAPNO == 0x2C
             and sortie.R13A_BLOCK + missionblock.MB_LEADERID == 0x2C + (0x5CEE - 0x5C7E))
    print(f"  0x013A MapNo at payload+0x2C -> lobby+0x5C7E, LeaderID at "
          f"+0x{sortie.R13A_BLOCK + missionblock.MB_LEADERID:X} -> lobby+0x5CEE: "
          f"{'OK' if s_off else 'FAIL'}")
    ok &= s_off
    # Explicit MapNo 38, endpoint on: those two fields and NOTHING else.
    body = sortie.reply_013a(mapno="38", ep_enable=True, host="203.0.113.3", port=61300,
                             leader="", bgcostmax="", totalbgcost="", battleticket="",
                             start_time=0)  # the stamp has its own check
    want_ep = (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)("203.0.113.3", 61300)
    scrub = bytearray(body)
    scrub[sortie.R13A_ENDPOINT:sortie.R13A_ENDPOINT + addressing.ENDPOINT_LEN] = bytes(addressing.ENDPOINT_LEN)
    scrub[sortie.R13A_BLOCK + missionblock.MB_MAPNO:sortie.R13A_BLOCK + missionblock.MB_MAPNO + 4] = bytes(4)
    s_body = (len(body) == sortie.REPLY_013A_LEN
              and body[sortie.R13A_ENDPOINT:sortie.R13A_ENDPOINT + addressing.ENDPOINT_LEN] == want_ep
              and struct.unpack_from("<I", body, sortie.R13A_BLOCK + missionblock.MB_MAPNO)[0] == 38
              and bytes(scrub) == bytes(sortie.REPLY_013A_LEN))
    print(f"  0x013A MapNo 38 + endpoint: land at +0x2C / +0x00, every other "
          f"byte zero: {'OK' if s_body else 'FAIL'}")
    ok &= s_body
    # The block knobs ride along at the SHIFTED offset (0x2C, not 0x18).
    body = sortie.reply_013a(mapno="0", ep_enable=False, leader="0x820C1080",
                             bgcostmax="", totalbgcost="", battleticket="")
    s_shift = (struct.unpack_from("<I", body, sortie.R13A_BLOCK + missionblock.MB_LEADERID)[0]
               == 0x820C1080
               and body[:sortie.R13A_BLOCK] == bytes(sortie.R13A_BLOCK)
               and struct.unpack_from("<I", body, sortie.R13A_BLOCK + missionblock.MB_MAPNO)[0] == 0)
    print(f"  0x013A explicit MapNo '0' is allowed (on disk), LeaderID knob "
          f"lands at +0x{sortie.R13A_BLOCK + missionblock.MB_LEADERID:X}, endpoint off = zero: "
          f"{'OK' if s_shift else 'FAIL'}")
    ok &= s_shift
    # The crash gate: unset, off-disk and garbage MapNos never reach the wire.
    off_disk = next(i for i in range(1, 512) if i not in missionlist.TYPE1_ON_DISK)
    gated = []
    for spec in ("", str(off_disk), "999", "abc"):
        mn, src = sortie.sortie_mapno(spec)
        gated.append(mn is None and "REFUSED" in src
                     and sortie.reply_013a(mapno=spec, leader="", bgcostmax="",
                                           totalbgcost="", battleticket="") is None)
    print(f"  0x013A MapNo unset / {off_disk} / 999 / 'abc' are REFUSED and "
          f"reply_013a() is None: {'OK' if all(gated) else 'FAIL'}")
    ok &= all(gated)
    # The request decoder names every stored field at the builder's offsets.
    req = bytearray(sortie.REQ_0139_LEN)
    struct.pack_into("<I", req, sortie.Q139_ID, 7)
    req[sortie.Q139_RESUME] = 1
    struct.pack_into("<I", req, sortie.Q139_BGFLAG, 1)
    struct.pack_into("<I", req, sortie.Q139_INFOID, 0x2A)
    struct.pack_into("<I", req, sortie.Q139_CREATE, 1)
    struct.pack_into("<I", req, sortie.Q139_AREA, 3)
    q = sortie.parse_0139(bytes(req))
    s_req = (q["id"] == 7 and q["resume"] == 1 and q["bgflag"] == 1
             and q["infoid"] == 0x2A and q["create"] == 1 and q["area"] == 3
             and q["tail"] == bytes(0x0C) and len(q["char48"]) == 0x30)
    print(f"  0x0139 decode: id/resume/bgflag/infoid/create/area at "
          f"+0x00/+0x04/+0x08/+0x0C/+0x10/+0x40: {'OK' if s_req else 'FAIL'}")
    ok &= s_req
    # The handler: OFF by default -> ONE message 2 on the request's own seq,
    # never silence, never a 0x013A. Only checked when the env is at default.
    if not os.environ.get("FMO_SORTIE"):
        s = session.Session("selftest:0")
        outs = s.on_packet(packet.parse(packet.build(sortie.MSG_SORTIE_REQ, bytes(req), seq=0x1337,
                                                     conn_id=1)))
        qq = [packet.parse(o) for o in outs]
        s_off_default = (not sortie.SERVE_SORTIE and len(qq) == 1
                         and qq[0]["msg"] == charselect.MSG_FAIL and qq[0]["seq"] == 0x1337
                         and qq[0]["conn"] == charselect.FAIL_CODE)
        print(f"  0x0139 with FMO_SORTIE unset -> one message 2 (code "
              f"{charselect.FAIL_CODE}) on seq 0x1337, no 0x013A: "
              f"{'OK' if s_off_default else 'FAIL'}")
        ok &= s_off_default
    else:
        print(f"  WARNING: FMO_SORTIE={os.environ['FMO_SORTIE']!r} is set in this "
              f"environment; the default-off handler check was skipped")
    for k in ("FMO_SORTIE_MAPNO", "FMO_SORTIE_HOST", "FMO_SORTIE_PORT",
              "FMO_SORTIE_ENDPOINT"):
        if os.environ.get(k):
            print(f"  WARNING: {k}={os.environ[k]!r} is set in this environment")

    # THE AUTO-SORTIE PUSH 0x014E -> 0x014D (sortie brief, 2026-09-04).
    # Offsets pinned to the READER addresses in the MSG_SORTIE_PUSH note.
    p_len = (sortiepush.REPLY_014E_LEN == sortiepush.R14E_DEST + sortiepush.R14E_DEST_LEN == 0xE14
             and sortiepush.R14E_BLOCK == 0x34 and sortiepush.R14E_ID == 0 and sortiepush.R14E_TIME == 4
             and sortiepush.R14E_ENDPOINT == 8 and addressing.ENDPOINT_LEN == 0x14
             and sortiepush.R14E_BLOCK + missionblock.M1C1_BLOCK_LEN == sortiepush.R14E_INFO88 == 0xD7C
             and sortiepush.R14E_INFO88 + sortie.R13A_INFO88_LEN == sortiepush.R14E_DEST == 0xDD4)
    print(f"  0x014E is {sortiepush.REPLY_014E_LEN}B: id +0x00, time +0x04, endpoint "
          f"+0x08 (-> lobby+0x4F1A/1E/22), block +0x34 -> lobby+0x5C7E, 88B "
          f"+0xD7C, dest +0xDD4: {'OK' if p_len else 'FAIL'}")
    ok &= p_len
    # Explicit map 418 (FZ-10 sector 04 'Oak Hills City', alignment-corrected
    # 2026-09-04), endpoint on, time 10, dest set: the named fields land where
    # their readers look and nothing else moves.
    body = sortiepush.reply_014e(mapno="418", time_s=10, dest="Oak Hills City",
                                 ep_enable=True, host="203.0.113.3", port=61300,
                                 leader="", bgcostmax="", totalbgcost="", battleticket="",
                                 start_time=0)  # the stamp has its own check
    want_ep = (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)("203.0.113.3", 61300)
    want_dest = "Oak Hills City".encode("cp932")[:sortiepush.R14E_DEST_LEN - 1] + b"\0"
    scrub = bytearray(body)
    scrub[sortiepush.R14E_ID:sortiepush.R14E_ID + 4] = bytes(4)          # (unused; MapNo is in block)
    scrub[sortiepush.R14E_TIME:sortiepush.R14E_TIME + 4] = bytes(4)
    scrub[sortiepush.R14E_ENDPOINT:sortiepush.R14E_ENDPOINT + addressing.ENDPOINT_LEN] = bytes(addressing.ENDPOINT_LEN)
    scrub[sortiepush.R14E_BLOCK + missionblock.MB_MAPNO:sortiepush.R14E_BLOCK + missionblock.MB_MAPNO + 4] = bytes(4)
    scrub[sortiepush.R14E_DEST:sortiepush.R14E_DEST + len(want_dest)] = bytes(len(want_dest))
    p_body = (len(body) == sortiepush.REPLY_014E_LEN
              and struct.unpack_from("<I", body, sortiepush.R14E_TIME)[0] == 10
              and body[sortiepush.R14E_ENDPOINT:sortiepush.R14E_ENDPOINT + addressing.ENDPOINT_LEN] == want_ep
              and struct.unpack_from("<I", body, sortiepush.R14E_BLOCK + missionblock.MB_MAPNO)[0] == 418
              and body[sortiepush.R14E_DEST:sortiepush.R14E_DEST + len(want_dest)] == want_dest
              and bytes(scrub) == bytes(sortiepush.REPLY_014E_LEN))
    print(f"  0x014E map 418 + time 10 + endpoint + dest: land at "
          f"+0x{sortiepush.R14E_BLOCK + missionblock.MB_MAPNO:X}/+0x04/+0x08/+0x{sortiepush.R14E_DEST:X}, every "
          f"other byte zero: {'OK' if p_body else 'FAIL'}")
    ok &= p_body
    # The crash gate: unset / off-disk / garbage never build a packet.
    off_disk = next(i for i in range(1, 512) if i not in missionlist.TYPE1_ON_DISK)
    p_gate = all(sortiepush.reply_014e(mapno=spec, leader="", bgcostmax="",
                                       totalbgcost="", battleticket="") is None
                 for spec in ("", str(off_disk), "999", "abc"))
    print(f"  0x014E MapNo unset / {off_disk} / 999 / 'abc' -> reply_014e() "
          f"is None (no zero block ever built): {'OK' if p_gate else 'FAIL'}")
    ok &= p_gate
    # The GO poll 0x014D: if no 0x014E was pushed this session it is REFUSED
    # (message 2), never silent, never a blind GO. Independent of the knob.
    s = session.Session("selftest:0")
    go = s.on_packet(packet.parse(packet.build(sortiepush.MSG_SORTIE_GO, b"", seq=0x2468, conn_id=1)))
    gq = [packet.parse(o) for o in go]
    p_go_refuse = (len(gq) == 1 and gq[0]["msg"] == charselect.MSG_FAIL
                   and gq[0]["seq"] == 0x2468 and gq[0]["conn"] == charselect.FAIL_CODE
                   and not getattr(s, "sortie_push_done", False))
    print(f"  0x014D with no push this session -> message 2 (code "
          f"{charselect.FAIL_CODE}) on seq 0x2468, never silent: "
          f"{'OK' if p_go_refuse else 'FAIL'}")
    ok &= p_go_refuse
    # And when a push HAS gone out, the GO is granted (message 1) on the
    # request's own seq -- but only when the map is servable, so drive it
    # with an explicit on-disk id rather than the possibly-unset knob.
    if sortiepush.reply_014e(mapno="418", leader="", bgcostmax="", totalbgcost="",
                             battleticket="") is not None:
        s2 = session.Session("selftest:0")
        s2.sortie_push_done = True
        go2 = s2.on_packet(packet.parse(packet.build(sortiepush.MSG_SORTIE_GO, b"", seq=0x99,
                                                     conn_id=1)))
        gq2 = [packet.parse(o) for o in go2]
        # message 1 GO only when the ACTUAL knob resolves; otherwise the
        # handler still refuses (it reads sortie_push_mapno() with no arg).
        _mn, _ = sortiepush.sortie_push_mapno()
        if _mn is not None:
            p_go_ok = (len(gq2) == 1 and gq2[0]["msg"] == handshake.MSG_SESSION_START
                       and gq2[0]["seq"] == 0x99)
            print(f"  0x014D after a push, knob resolves to {_mn} -> "
                  f"message 1 = GO on seq 0x99: "
                  f"{'OK' if p_go_ok else 'FAIL'}")
            ok &= p_go_ok
        else:
            print(f"  0x014D after a push, knob UNSET -> still refused "
                  f"(no blind GO): "
                  f"{'OK' if gq2 and gq2[0]['msg'] == charselect.MSG_FAIL else 'FAIL'}")
            ok &= bool(gq2) and gq2[0]["msg"] == charselect.MSG_FAIL
    for k in ("FMO_SORTIE_PUSH", "FMO_SORTIE_PUSH_TIME", "FMO_SORTIE_PUSH_DELAY",
              "FMO_SORTIE_PUSH_DEST"):
        if os.environ.get(k):
            print(f"  WARNING: {k}={os.environ[k]!r} is set in this environment")

    # The named profile fields (status-block worker, 2026-08-26). Each offset is
    # pinned to the lobby address its READER uses, not to a neighbour: money to
    # 0x6109E950's [lobby+0x88C], the names to 0x6109E880/0x6109E8A0's +0x890 /
    # +0x8A1, gender to 0x6109E8E0's +0x8B2, nation to 0x6109E920's +0x8B4, MP
    # to 0x6109E980's +0xE0C, contribution to the u32 window that lands on
    # +0xFC8 (0x6117B36x), and the owned-items table to 0x611A37A0's +0x8C8.
    for label, off, want_lobby in (
            ("money", status.S14A_MONEY, 0x88C), ("first", status.S14A_FIRST, 0x890),
            ("last", status.S14A_LAST, 0x8A1), ("sex", status.S14A_SEX, 0x8B2),
            ("nation", status.S14A_NATION, 0x8B4), ("owned", status.S14A_OWNED, 0x8C8),
            ("mp", status.S14A_MP, 0xE0C)):
        want = status.S14A_BLOCK + (want_lobby - 0x88C)
        print(f"  0x014A {label} at payload+0x{off:03X} -> lobby+0x{want_lobby:X}"
              f": {'OK' if off == want else 'FAIL'}")
        ok &= off == want
    print(f"  0x014A contribution at payload+0x{status.S14A_CONTRIB:03X} == the "
          f"lobby+0xFC8 window +0x68C: "
          f"{'OK' if status.S14A_CONTRIB == 0x68C == status.S14A_WFC8 else 'FAIL'}")
    ok &= status.S14A_CONTRIB == 0x68C == status.S14A_WFC8
    # Fields must not overlap: first ends where last begins, last ends before
    # gender, the owned table ends before MP, and MP sits inside the block.
    tight = (status.S14A_FIRST + status.S14A_NAME_LEN == status.S14A_LAST
             and status.S14A_LAST + status.S14A_NAME_LEN <= status.S14A_SEX
             and status.S14A_OWNED + status.S14A_OWNED_LEN <= status.S14A_MP
             and status.S14A_MP + 4 <= status.S14A_BLOCK + status.S14A_BLOCK_LEN)
    print(f"  0x014A named fields do not overlap: {'OK' if tight else 'FAIL'}")
    ok &= tight

    # A filled body lands each value at its offset and NOWHERE else. Explicit
    # values, so this holds whatever the environment says.
    body = status.reply_014a(rank=21, class_table=False,
                             char={"first": "Lex", "last": "Arden"},
                             money=12345, mp=67, contrib=890, sex=1, nation=2,
                             names=True)
    got = {
        "money": struct.unpack_from("<I", body, status.S14A_MONEY)[0],
        "mp": struct.unpack_from("<I", body, status.S14A_MP)[0],
        "contrib": struct.unpack_from("<I", body, status.S14A_CONTRIB)[0],
        "sex": body[status.S14A_SEX], "nation": body[status.S14A_NATION],
        "first": body[status.S14A_FIRST:status.S14A_FIRST + status.S14A_NAME_LEN].split(charselect.NUL)[0],
        "last": body[status.S14A_LAST:status.S14A_LAST + status.S14A_NAME_LEN].split(charselect.NUL)[0],
    }
    want_f = {"money": 12345, "mp": 67, "contrib": 890, "sex": 1, "nation": 2,
              "first": b"Lex", "last": b"Arden"}
    scratch = bytearray(body)
    for off, ln in ((status.S14A_MONEY, 4), (status.S14A_MP, 4), (status.S14A_CONTRIB, 4),
                    (status.S14A_SEX, 1), (status.S14A_NATION, 1), (status.S14A_RANK, 1),
                    (status.S14A_ACTIVE_SETUP, 1),
                    # the acknowledged rank rides with rank (the E316 gate)
                    (status.S14A_BE1A, 1),
                    # the nation's starting paint, owned (inventory.owned_paint_bits)
                    (status.S14A_OWNED + 0x110, 0x100), (status.S14A_OWNED + 0x3C0, 0x80),
                    (status.S14A_FIRST, status.S14A_NAME_LEN), (status.S14A_LAST, status.S14A_NAME_LEN)):
        scratch[off:off + ln] = bytes(ln)
    rest_zero = scratch == bytearray(status.REPLY_014A_LEN)
    if status_block_pristine():
        print(f"  0x014A filled body: {got} "
              f"{'OK' if got == want_f else 'FAIL'}; bytes outside the named "
              f"fields zero: {'OK' if rest_zero else 'FAIL'}")
        ok &= got == want_f and rest_zero
    else:
        print(f"  0x014A filled body: {got} "
              f"{'OK' if got == want_f else 'FAIL'}; WARNING: the "
              f"outside-the-named-fields check is SKIPPED because an "
              f"FMO_STATUS_* knob is filling the block too")
        ok &= got == want_f
    # A 20-char name is cut to 16 + NUL and never runs into the next field.
    body = status.reply_014a(rank=0, char={"first": "A" * 20, "last": "B" * 20},
                             names=True)
    cut = (body[status.S14A_FIRST:status.S14A_LAST] == b"A" * 16 + charselect.NUL
           and body[status.S14A_LAST:status.S14A_LAST + status.S14A_NAME_LEN] == b"B" * 16 + charselect.NUL
           and body[status.S14A_SEX] == 0)
    print(f"  0x014A 20-char names cut to 16+NUL, gender untouched: "
          f"{'OK' if cut else 'FAIL'}")
    ok &= cut
    # WARNING: PLAN 1.5: the persisted-economy precedence, asserted on the SOURCE
    # string so it holds whatever the FMO_STATUS_* knobs are set to. A char that
    # carries a stored rank/money/mp/contribution serves THAT value (step 2),
    # named "character store [<field>]"; an explicit call arg still wins (step
    # 1); a char with no such key falls back to the knob (step 3).
    stored_char = {"first": "Lex", "last": "Arden",
                   "rank": 30, "money": 777, "mp": 5, "contribution": 42}
    by_label = {lbl: (val, src)
                for lbl, _off, val, src in status.status_fields(char=stored_char)}
    store_ok = True
    for lbl, want, raw_is in (("rank", 30, bytes([30])),
                              ("money", 777, struct.pack("<I", 777)),
                              ("mp", 5, struct.pack("<I", 5)),
                              ("contribution", 42, struct.pack("<I", 42))):
        got_raw, got_src = by_label.get(lbl, (None, ""))
        # startswith, not ==: the source string also NAMES a knob that is set
        # and masked, so an exact match here would fail on a probe box for the
        # right reason and look like the store had broken.
        store_ok &= (got_raw == raw_is
                     and got_src.startswith(f"character store [{lbl}]"))
    print(f"  0x014A economy: a stored rank/money/mp/contribution is served "
          f"from the character, sourced 'character store [...]': "
          f"{'OK' if store_ok else 'FAIL ' + str(by_label)}")
    ok &= store_ok
    # An explicit argument overrides the store; a storeless field falls to the
    # knob. Asserted on _econ_value directly -- status_fields drops a zero
    # field, so the knob-fallback case would otherwise be invisible when the
    # knob is at its zero default (the local selftest env).
    _masked = economy._econ_value("money", None, 12345, "FMO_STATUS_MONEY",
                                  {"money": 777})
    prec_ok = (
        economy._econ_value("money", 999, 12345, "FMO_STATUS_MONEY",
                            {"money": 777}) == (999, "call arg money=999")
        and economy._econ_value("mp", None, 67, "FMO_STATUS_MP",
                                {"money": 1}) == (67, "FMO_STATUS_MP")
        and _masked[0] == 777
        and _masked[1].startswith("character store [money]"))
    print(f"  0x014A economy: an explicit arg overrides the store, a storeless "
          f"field falls back to the knob (even a nonzero one): "
          f"{'OK' if prec_ok else 'FAIL'}")
    ok &= prec_ok
    # WARNING: AND THE MASKED KNOB IS NAMED. Once a pilot carries its own numbers, an
    # armed FMO_STATUS_MONEY does nothing for that pilot -- a knob that is set,
    # visible in `docker exec … env`, and ignored is the shape of reading that
    # has produced false results here before. The log line has to say so.
    mask_ok = ("MASKED" in _masked[1] and "FMO_STATUS_MONEY=12345" in _masked[1]
               and "MASKED" not in economy._econ_value("money", None, 777,
                                                       "FMO_STATUS_MONEY",
                                                       {"money": 777})[1])
    print(f"  0x014A economy: a stored value that MASKS an armed knob says so, "
          f"and an agreeing knob does not: {'OK' if mask_ok else 'FAIL'}")
    ok &= mask_ok

    # KEY: THE SEED (2026-09-08). A created character carries its own starting
    # numbers; after that the knobs are seed-and-override, not the state.
    seeded = economy.seed_new_character({"id": 1, "first": "New", "last": "Pilot"})
    seed_ok = all(k in seeded for k in economy.ECON_STORE_KEYS)
    # ...and a record that already carries one is NEVER overwritten, or a
    # relogin would reset the pilot every time creation was replayed.
    kept = economy.seed_new_character({"id": 1, "money": 999})
    seed_ok &= kept["money"] == 999
    print(f"  0x014A economy: creation seeds rank/money/mp/contribution and "
          f"never overwrites a value the record already has: "
          f"{'OK' if seed_ok else 'FAIL ' + str(seeded)}")
    ok &= seed_ok

    # KEY: STORED PROGRESS FLAGS. A character's own 256-byte block wins WHOLE over
    # FMO_STATUS_FLAGS -- byte 128 == 99 ("pilot registered") is the gate the
    # counters and the war map read, and it is a fact about one pilot.
    if fmostore:
        blk = fmostore.flags_hex(bits=[8], byte_values={128: 99})
        got = {off: (val, src) for _l, off, val, src
               in status.status_fields(char={"flags": blk})}
        flag_ok = (got.get(status.S14A_FLAGS11 + 128, (None, ""))[0] == bytes([99])
                   and got.get(status.S14A_FLAGS11 + 1, (None, ""))[0] == bytes([1])
                   and got[status.S14A_FLAGS11 + 128][1].startswith(
                       "character store [flags]"))
        # an explicit call arg still overrides the stored block (the selftest
        # and probe path), or status_fields could not be driven at all
        forced = {off for _l, off, _v, _s
                  in status.status_fields(char={"flags": blk}, flags=[9])}
        flag_ok &= (status.S14A_FLAGS11 + 1) in forced and (status.S14A_FLAGS11 + 128) not in forced
        print(f"  0x014A flags: a stored 256-byte block wins over "
              f"FMO_STATUS_FLAGS, and an explicit arg still overrides it: "
              f"{'OK' if flag_ok else 'FAIL'}")
        ok &= flag_ok

    # KEY: THE RESUME TRIAD IS PER CHARACTER. While it was global, an armed
    # FMO_STATUS_W7604 diverted EVERY login off world entry -- PLAN 0.1 lists
    # disarming it as hygiene for exactly that reason.
    res = {off: val for _l, off, val, _s
           in status.status_fields(char={"resume_w7604": 1, "resume_wfd4": 70000})}
    res_ok = (res.get(status.S14A_W7604) == struct.pack("<I", 1)
              and res.get(status.S14A_BLOCK3) == struct.pack("<I", 70000)
              and status.S14A_W7608 not in res)          # absent stays absent
    print(f"  0x014A resume: the triad resolves per character, and a field the "
          f"pilot does not carry stays zero: {'OK' if res_ok else 'FAIL'}")
    ok &= res_ok

    # Every FMO_STATUS_* knob defaults to zero / off: with none of them set the
    # body is rank, the acknowledged rank that must mirror it (the E316 orders
    # gate), and nothing else. Only checked when the env really is unset.
    if status_block_pristine():
        _f = status.status_fields(rank=21, char={"first": "Lex", "last": "Arden"},
                                  class_table=False)   # pinned by name above
        quiet = [f for f in _f
                 if f[1] not in (status.S14A_RANK, status.S14A_BE1A, status.S14A_ACTIVE_SETUP)]
        mirrored = [f for f in _f if f[1] == status.S14A_BE1A] == \
            [("ack-rank (+0x58E -> lobby+0xE1A, the E316 orders gate)",
              status.S14A_BE1A, b"\x15", "mirrors rank")]
        print(f"  FMO_STATUS_* all default off -> only rank, its mirrored "
              f"ack-rank and the active setup are served: "
              f"{'OK' if not quiet and mirrored else 'FAIL ' + str(_f)}")
        ok &= (not quiet) and mirrored

    # body+0x7A must come from the CHARACTER, not a sweep. The whole point of
    # 2026-08-26's Q1 is that `sex` has been on disk since the store existed and
    # was never sent, so assert the precedence rather than the plumbing: an
    # explicit knob wins, otherwise the roster does, and a roster with no sex on
    # file returns None rather than inventing one.
    _save = (flat_globals().get("account_for"), flat_globals().get("load_roster"))
    try:
        flat_globals()["account_for"] = lambda ip: "acct1234abcd"
        # `gender` is +0x26 (the default source since 2026-08-27); `sex` is the
        # legacy +0x28 key, kept so the old arm still has something to read.
        flat_globals()["load_roster"] = lambda a: [{"first": "K", "last": "T",
                                                    "gender": 1, "sex": 1}]
        _got, _src = (popnames.pop_sex_for("192.0.2.1") if not popself.POP_SEX and not
                      popself.POP_SEX_SWEEP else (None, "knob set"))
        _roster_ok = (popself.POP_SEX is not None or popself.POP_SEX_SWEEP
                      or (_got == 1 and "store:" in (_src or "")))
        print(f"  body+0x7A comes from the character's own sex "
              f"({_got!r} via {_src!r}): "
              f"{'OK' if _roster_ok else 'FAIL -- the roster is being ignored'}")
        ok &= bool(_roster_ok)

        flat_globals()["load_roster"] = lambda a: [{"first": "K", "last": "T"}]
        _none, _nsrc = (popnames.pop_sex_for("192.0.2.1") if not popself.POP_SEX and not
                        popself.POP_SEX_SWEEP else (None, "knob set"))
        _none_ok = _none is None
        print(f"  a character with no sex on file sends nothing ({_none!r}): "
              f"{'OK' if _none_ok else 'FAIL -- a model was invented'}")
        ok &= _none_ok

        # FMO_UDP_POP_SEX_SOURCE: default is "gender" (+0x26, seen on screen
        # 2026-08-27); "sex" is the legacy +0x28 arm. Exercised by flipping the
        # global, so the default is asserted first and restored after.
        _src_save = flat_globals().get("POP_SEX_SOURCE")
        try:
            _default_ok = (os.environ.get("FMO_UDP_POP_SEX_SOURCE") or
                           _src_save == "gender")
            print(f"  FMO_UDP_POP_SEX_SOURCE defaults to 'gender' (+0x26): "
                  f"{'OK' if _default_ok else 'FAIL'}")
            ok &= bool(_default_ok)
            flat_globals()["POP_SEX_SOURCE"] = "gender"
            _raw = bytearray(0x50)
            _raw[0x26] = 2          # gender: Female
            _raw[0x28] = 1          # nation: OCU
            flat_globals()["load_roster"] = lambda a: [
                {"first": "K", "last": "T", "sex": 1, "nation": 2,
                 "raw": bytes(_raw).hex()}]
            _g, _gsrc = (popnames.pop_sex_for("192.0.2.1") if not popself.POP_SEX and not
                         popself.POP_SEX_SWEEP else (2, "knob set"))
            _g_ok = _g == 2 and ("gender" in (_gsrc or "") or _gsrc == "knob set")
            print(f"  source=gender reads +0x26 out of raw ({_g!r} via "
                  f"{_gsrc!r}): {'OK' if _g_ok else 'FAIL'}")
            ok &= bool(_g_ok)
        finally:
            flat_globals()["POP_SEX_SOURCE"] = _src_save

        # The other four appearance fields, read out of `raw` alone -- a
        # legacy record (no size/build/face keys) must still dress the pilot,
        # because every character made before 2026-08-26 is one.
        _raw = bytearray(0x50)
        _raw[0x26] = 1            # gender: Male
        _raw[0x28] = 2            # nation: USN -> uniform 201
        _raw[0x30], _raw[0x31] = 3, 4   # size Tall, build 4
        struct.pack_into("<H", _raw, 0x32, 107)
        flat_globals()["load_roster"] = lambda a: [
            {"first": "K", "last": "T", "raw": bytes(_raw).hex()}]
        _lk, _lksrc = (poplook.pop_look_for("192.0.2.1") if poplook.POP_LOOK_FIXED is None
                       and poplook.POP_LOOK else ({"size": 3, "build": 4, "face": 107,
                                                   "uniform": 201}, "knob set"))
        _lk_ok = _lk == {"size": 3, "build": 4, "face": 107, "uniform": 201}
        print(f"  look comes from the character, uniform from the nation "
              f"({_lk!r} via {_lksrc!r}): "
              f"{'OK' if _lk_ok else 'FAIL -- the roster is being ignored'}")
        ok &= bool(_lk_ok)

        # And an out-of-range field is DROPPED and named, never sent: the
        # client resolves no part for it and the slot stays empty, which is
        # indistinguishable on screen from the offset being wrong.
        _raw[0x30] = 9                        # size: not 1..3
        struct.pack_into("<H", _raw, 0x32, 200)   # face: not 101..110
        flat_globals()["load_roster"] = lambda a: [
            {"first": "K", "last": "T", "raw": bytes(_raw).hex()}]
        _bad, _badsrc = (poplook.pop_look_for("192.0.2.1") if poplook.POP_LOOK_FIXED is None
                         and poplook.POP_LOOK else ({"build": 4, "uniform": 201},
                                                    "DROPPED knob set"))
        _bad_ok = (_bad == {"build": 4, "uniform": 201}
                   and "DROPPED" in (_badsrc or ""))
        print(f"  out-of-range size/face are dropped and named ({_bad!r}): "
              f"{'OK' if _bad_ok else 'FAIL -- a dead part id was sent'}")
        ok &= bool(_bad_ok)

        # WARNING: REGRESSION PIN. An EMPTY FMO_UDP_POP_LOOK_FIELDS is "unset", not
        # "no fields". compose passes `${...:-}` so the variable IS set to "",
        # and `os.environ.get(name, default)` hands back that empty string
        # rather than the default. Written the obvious way this parsed to [],
        # filtered out all four fields, and served look=None while the source
        # string still said `store:member:N ... (nation 1)` -- so it read as an
        # empty ROSTER, not a misread knob. Cost a live launch 2026-09-04.
        def _parse_fields(val):
            return [f for f in ((val or "").strip()
                                or "size,build,face,uniform")
                    .replace(" ", "").split(",") if f]
        _all4 = ["size", "build", "face", "uniform"]
        _fld_ok = (_parse_fields("") == _all4 and _parse_fields(None) == _all4
                   and _parse_fields("   ") == _all4
                   and _parse_fields("size") == ["size"]
                   and _parse_fields("size, face") == ["size", "face"])
        print(f"  FMO_UDP_POP_LOOK_FIELDS: empty/blank means ALL FOUR, not "
              f"none: {'OK' if _fld_ok else 'FAIL'}")
        ok &= bool(_fld_ok)
        _live_ok = poplook.POP_LOOK_FIELDS == _parse_fields(
            os.environ.get("FMO_UDP_POP_LOOK_FIELDS"))
        print(f"  ...and the module parsed its own env the same way "
              f"({poplook.POP_LOOK_FIELDS}): {'OK' if _live_ok else 'FAIL'}")
        ok &= bool(_live_ok)
    finally:
        flat_globals()["account_for"], flat_globals()["load_roster"] = _save

    # And the four fields must land at the offsets 0x611E7190 / 0x611F5C83 /
    # 0x61132252 read, in the body the client copies to unit+4. An offset that
    # drifts here is a silent wrong model, not an error.
    _lp = fmoworld.record_pop(0x82080C01, unit_type=4, name1="A", name2="B",
                              look={"size": 3, "build": 4, "face": 107,
                                    "uniform": 201})
    _lb = _lp[-fmoworld.POP_BODY_LEN:]
    _off_ok = (_lb[0x188] == 3 and _lb[0x189] == 4
               and struct.unpack_from("<H", _lb, 0x18A)[0] == 107
               and struct.unpack_from("<H", _lb, 0x18C)[0] == 201)
    print(f"  POP body carries size/build/face/uniform at 0x188/0x189/0x18A/"
          f"0x18C: {'OK' if _off_ok else 'FAIL'}")
    ok &= _off_ok
    for _bad_look in ({"face": 100}, {"face": 111}, {"size": 0}, {"size": 4},
                      {"build": 6}, {"hair": 1}):
        try:
            fmoworld.record_pop(0x82080C01, unit_type=4, look=_bad_look)
        except ValueError:
            pass
        else:
            print(f"  record_pop accepted look={_bad_look!r}: FAIL")
            ok = False

    # The 0x013E byte map from the static read of the builder (0x61178D8F):
    # every named byte must come back from the offset the builder writes it to.
    _p = bytearray(0x50)
    _p[0x26], _p[0x28], _p[0x29] = 2, 1, 3
    _p[0x30], _p[0x31] = 1, 3
    struct.pack_into("<H", _p, 0x32, 107)
    _p[0x39] = 3
    struct.pack_into("<H", _p, 0x3A, 6264)
    _r = charstore.character_from_013e(bytes(_p))
    _map_ok = (_r["gender"] == 2 and _r["nation_byte"] == 1
               and _r["personality"] == 3 and _r["size"] == 1
               and _r["build"] == 3 and _r["face"] == 107
               and _r["cls"] == 3 and _r["hangar_pw"] == 6264
               and _r["appearance"] == "01036b00"
               and _r["nation"] == 2 and _r["sex"] == 1)
    print(f"  0x013E byte map: gender={_r['gender']} nation={_r['nation_byte']} "
          f"personality={_r['personality']} size={_r['size']} "
          f"build={_r['build']} face={_r['face']} (appearance "
          f"{_r['appearance']}): {'OK' if _map_ok else 'FAIL'}")
    ok &= _map_ok

    # RC4 against a published vector, so a broken transcription of the KSA/PRGA
    # fails here rather than silently desynchronising a live session.
    v = packet.RC4(b"Key").crypt(b"Plaintext")
    print(f"  RC4 vector Key/Plaintext -> {v.hex()} "
          f"{'OK' if v.hex() == 'bbf316e8d940af0ad3' else 'FAIL'}")
    ok &= v.hex() == "bbf316e8d940af0ad3"
    v2 = packet.RC4(b"Secret").crypt(b"Attack at dawn")
    print(f"  RC4 vector Secret/Attack  -> {v2.hex()} "
          f"{'OK' if v2.hex() == '45a01f645fc35b383552544b9bf5' else 'FAIL'}")
    ok &= v2.hex() == "45a01f645fc35b383552544b9bf5"

    # A round trip through the real 20-byte key shape, and proof the stream is
    # CONTINUOUS (mode 1) rather than reset per message -- encrypting two
    # messages on one context must differ from encrypting each on a fresh one.
    k = packet.session_key(bytes(4))
    print(f"  session key = {k.hex()} ({len(k)} bytes) "
          f"{'OK' if len(k) == 20 else 'FAIL'}")
    ok &= len(k) == 20
    a1 = packet.RC4(k); b1 = packet.RC4(k)
    m1, m2 = b"hello world!", b"second message"
    c1 = a1.crypt(m1); c2 = a1.crypt(m2)
    assert b1.crypt(c1) == m1 and b1.crypt(c2) == m2
    fresh = packet.RC4(k).crypt(m2)
    print(f"  continuous stream (mode 1) differs from per-message reset: "
          f"{'OK' if c2 != fresh else 'FAIL'}")
    ok &= c2 != fresh

    # CONTENT AUTH (2026-09-27): trial_key must pick the prefix the "client"
    # really keyed with, name its member, and leave the RX stream positioned
    # for the NEXT packet -- and its twins must refuse.
    def _client_pkt(prefix, tail, n=1):
        """n time-requests enciphered on one client stream, as the wire has them."""
        rc = packet.RC4(packet.session_key(tail, prefix))
        out = []
        for i in range(n):
            pb = bytearray(packet.build(timesync.MSG_TIME_REQ, b"", 0x1003 + i))
            pb[3] |= packet.FLAG_ENCRYPTED
            struct.pack_into("<H", pb, packet.CHK_OFF, 0)
            struct.pack_into("<H", pb, packet.CHK_OFF, packet.checksum(bytes(pb)))
            out.append(bytes(pb[:packet.CRYPT_OFF]) + rc.crypt(bytes(pb[packet.CRYPT_OFF:])))
        return out
    _pa, _pb = bytes(range(1, 17)), bytes(range(101, 117))
    _tail = struct.pack("<I", 0)
    _w1, _w2 = _client_pkt(_pa, _tail, 2)
    _got = packet.trial_key(_w1, _tail, [(_pb, 11), (_pa, 3), (None, None)])
    _ta = (_got is not None and _got[2] == 3 and _got[1] == _pa
           and packet.parse(_got[3])["msg"] == timesync.MSG_TIME_REQ)
    _next = _got[0].crypt(_w2[packet.CRYPT_OFF:]) if _got else b""
    _ta2 = packet.parse(_w2[:packet.CRYPT_OFF] + _next)["chk_ok"] if _got else False
    print(f"  content auth: picks the real prefix past a wrong one, names "
          f"member 3: {'OK' if _ta else 'FAIL'}; RX stream ready for the next "
          f"packet: {'OK' if _ta2 else 'FAIL'}")
    ok &= _ta and _ta2
    _none = packet.trial_key(_w1, _tail, [(_pb, 11), (None, None)])
    print(f"  content auth twin: no candidate holds the real prefix -> refused: "
          f"{'OK' if _none is None else 'FAIL'}")
    ok &= _none is None
    _z = _client_pkt(None, _tail)[0]
    _gz = packet.trial_key(_z, _tail, [(_pa, 3), (None, None)])
    print(f"  content auth: a legacy zero-keyed client falls through to the "
          f"zero prefix with NO member: "
          f"{'OK' if _gz is not None and _gz[2] is None else 'FAIL'}")
    ok &= _gz is not None and _gz[2] is None

    # WORLD CHANNEL BINDING (2026-09-27): two devices, one address. Each new
    # channel takes the oldest entry; a bound channel's own re-entry is settled
    # so it cannot be handed to the other device; account_for answers for the
    # channel being served, and the TWIN (no context) still answers per address.
    _h = "203.0.113.9"
    worldchannel._world_entries.pop(_h, None)
    worldchannel.queue_world_entry(_h, "member:3")
    worldchannel.queue_world_entry(_h, "member:11")
    _c1, _c2 = worldchannel.WorldChannel((_h, 19155)), worldchannel.WorldChannel((_h, 39047))
    _c1.account = worldchannel.claim_world_account(_h)
    _c2.account = worldchannel.claim_world_account(_h)
    _wb1 = (_c1.account, _c2.account) == ("member:3", "member:11")
    worldchannel.queue_world_entry(_h, "member:3")            # the PC Moves (re-entry)
    worldchannel.settle_world_entry(_c1)                      # its channel keeps talking
    worldchannel.queue_world_entry(_h, "member:11")           # the Deck relaunches, new port
    _c3 = worldchannel.WorldChannel((_h, 40000))
    _c3.account = worldchannel.claim_world_account(_h)
    _wb2 = _c3.account == "member:11"
    groupchannel.WORLD_PEERS[(_h, 19155)], groupchannel.WORLD_PEERS[(_h, 39047)] = _c1, _c2
    try:
        worldchannel._udp_ctx.addr = (_h, 19155)
        _ac1 = identity.account_for(_h)
        with worldchannel._as_world_channel((_h, 39047)):     # the room-mate's pop, from c1
            _acm = identity.account_for(_h)
        _acb = identity.account_for(_h)                   # back to the served channel
        worldchannel._udp_ctx.addr = (_h, 39047)
        _ac2 = identity.account_for(_h)
        worldchannel._udp_ctx.addr = None
        identity._proven_by_ip[_h] = ("member:11", time.monotonic())
        _ac0 = identity.account_for(_h)                   # twin: TCP thread, no channel
    finally:
        worldchannel._udp_ctx.addr = None
        groupchannel.WORLD_PEERS.pop((_h, 19155), None)
        groupchannel.WORLD_PEERS.pop((_h, 39047), None)
        identity._proven_by_ip.pop(_h, None)
        worldchannel._world_entries.pop(_h, None)
    _wb3 = (_ac1, _ac2) == ("member:3", "member:11") and _ac0 == "member:11"
    print(f"  world binding: entries claimed in order: {'OK' if _wb1 else 'FAIL'}; "
          f"a Move is settled, the relaunch gets ITS entry: "
          f"{'OK' if _wb2 else 'FAIL'}; account_for per channel, per address "
          f"with no channel: {'OK' if _wb3 else 'FAIL ' + repr((_ac1, _ac2, _ac0))}")
    ok &= _wb1 and _wb2 and _wb3
    # Nations apart: O.C.U. entry in zone 200, U.S.N. in zone 400, SAME map
    # 102, one address. They must not be room-mates -- and the twin (same
    # zone) must be.
    worldchannel._world_entries.pop(_h, None)
    worldchannel.queue_world_entry(_h, "member:11", {"map": 102, "zone": 400})
    worldchannel.queue_world_entry(_h, "member:3", {"map": 102, "zone": 200})
    _d1, _d2 = worldchannel.WorldChannel((_h, 19155)), worldchannel.WorldChannel((_h, 63097))
    worldchannel.claim_world_account(_h, _d1)
    worldchannel.claim_world_account(_h, _d2)
    for _d in (_d1, _d2):
        _d.key, _d.seen_at = b"xlobby", time.time()
    groupchannel.WORLD_PEERS[_d1.addr], groupchannel.WORLD_PEERS[_d2.addr] = _d1, _d2
    rooms.WORLD_ZONES[_h], _saved_room = 200, (room.ROOM, room.ROOM_SAME_ZONE)
    try:
        flat_globals()["ROOM"], flat_globals()["ROOM_SAME_ZONE"] = True, True
        _apart = _d2 not in rooms.room_mates(_d1) and _d1 not in rooms.room_mates(_d2)
        _d1.loc["zone"] = 200
        _together = _d2 in rooms.room_mates(_d1)
        _d1.key = b"xbattle"                     # Lex sorties; Ned stays
        _scene = _d2 not in rooms.room_mates(_d1) and _d1 not in rooms.room_mates(_d2)
        _d2.key = b"xbattle"                     # both in battle: one room
        _both = _d2 in rooms.room_mates(_d1)
    finally:
        flat_globals()["ROOM"], flat_globals()["ROOM_SAME_ZONE"] = _saved_room
        groupchannel.WORLD_PEERS.pop(_d1.addr, None)
        groupchannel.WORLD_PEERS.pop(_d2.addr, None)
        rooms.WORLD_ZONES.pop(_h, None)
        worldchannel._world_entries.pop(_h, None)
    print(f"  world binding: O.C.U. and U.S.N. on one address are NOT room-mates "
          f"(zone per channel, not per address): {'OK' if _apart else 'FAIL'}; "
          f"twin, same zone: {'OK' if _together else 'FAIL'}")
    ok &= _apart and _together
    print(f"  world binding: a BATTLE channel and a LOBBY channel in the same "
          f"zone are NOT room-mates (the 19:10 crash): "
          f"{'OK' if _scene else 'FAIL'}; two battle channels are: "
          f"{'OK' if _both else 'FAIL'}")
    ok &= _scene and _both
    # Relaunch on the SAME port (live 2026-09-27 19:00): a channel bound to
    # Lex restarts with Ned's entry waiting -> rebound to Ned. Twin: Lex Moves
    # (own entry settled a moment ago) while Ned's entry waits -> stays Lex.
    worldchannel._world_entries.pop(_h, None)
    _r = worldchannel.WorldChannel((_h, 19155))
    _r.account, _r.loc = "member:3", {"map": 102, "zone": 200}
    worldchannel.queue_world_entry(_h, "member:11", {"map": 102, "zone": 200})
    _rb = worldchannel.rebind_on_restart(_r)
    _relaunch = _rb == "member:11" and _r.account == "member:11"
    worldchannel._world_entries.pop(_h, None)
    _m = worldchannel.WorldChannel((_h, 19155))
    _m.account, _m.loc = "member:3", {"map": 102, "zone": 200}
    worldchannel.queue_world_entry(_h, "member:3", {"map": 103, "zone": 200})
    worldchannel.settle_world_entry(_m)                       # its datagram before the restart
    worldchannel.queue_world_entry(_h, "member:11", {"map": 102, "zone": 400})
    _mv = worldchannel.rebind_on_restart(_m)
    _move = _mv is None and _m.account == "member:3" and _m.loc["map"] == 103
    _left = [e[0] for e in worldchannel._world_entries.get(_h, [])]
    worldchannel._world_entries.pop(_h, None)
    print(f"  world binding: a relaunch on the same port rebinds to the waiting "
          f"entry: {'OK' if _relaunch else 'FAIL ' + repr(_rb)}; a Move keeps "
          f"its player and leaves the other entry queued: "
          f"{'OK' if _move and _left == ['member:11'] else 'FAIL ' + repr((_mv, _m.account, _left))}")
    ok &= _relaunch and _move and _left == ["member:11"]
    # GROUP: the joiner's channel (kind 5) is answered on hid 5, the creator's
    # (kind 1) on 1; and on one address only the CREATOR's account is leader.
    _g = worldchannel.WorldChannel((_h, 1427))
    _g.key = groupchannel.GROUP_KEY
    _h0 = udpconfig.scene_reply_hid(_g)
    _g.group_kind = 5
    _h5 = udpconfig.scene_reply_hid(_g)
    _ghid = _h0 == udpconfig.UDP_HID_GROUP and _h5 == 5
    _gb, _gc = list(battlegroups.BATTLE_GROUPS_MADE), dict(battlegroups.GROUP_CREATOR_ACCOUNT)
    _lc, _ld = worldchannel.WorldChannel((_h, 19155)), worldchannel.WorldChannel((_h, 1427))
    _lc.account, _ld.account = "member:11", "member:3"
    groupchannel.WORLD_PEERS[_lc.addr], groupchannel.WORLD_PEERS[_ld.addr] = _lc, _ld
    try:
        battlegroups.BATTLE_GROUPS_MADE[:] = [(_h + ":43545", 91, "Ned.Test", 0.0)]
        battlegroups.GROUP_CREATOR_ACCOUNT.clear()
        battlegroups.GROUP_CREATOR_ACCOUNT[91] = "member:11"
        with worldchannel._as_world_channel(_lc.addr):
            _lead_c = groupchannel.group_leader_for(_h)[0]
        with worldchannel._as_world_channel(_ld.addr):
            _lead_d = groupchannel.group_leader_for(_h)[0]
    finally:
        battlegroups.BATTLE_GROUPS_MADE[:] = _gb
        battlegroups.GROUP_CREATOR_ACCOUNT.clear()
        battlegroups.GROUP_CREATOR_ACCOUNT.update(_gc)
        groupchannel.WORLD_PEERS.pop(_lc.addr, None)
        groupchannel.WORLD_PEERS.pop(_ld.addr, None)
    _glead = (_lead_c, _lead_d) == (1, 0)
    print(f"  group: the joiner's kind-5 channel is answered on hid 5 "
          f"(was {udpconfig.UDP_HID_GROUP} before its first datagram): "
          f"{'OK' if _ghid else 'FAIL ' + repr((_h0, _h5))}; on one address "
          f"only the creator's ACCOUNT is leader: "
          f"{'OK' if _glead else 'FAIL ' + repr((_lead_c, _lead_d))}")
    ok &= _ghid and _glead
    # BATTLE STATE PER PLAYER (live 2026-09-27 20:31-20:34): PC and Deck on one
    # address were BOTH made squad owner, and the Deck took the win for the
    # PC's kills. Keyed by the bound account, the second pilot joins the first
    # one's squad, and each has its own state.
    _bh = "203.0.113.77"
    _p1, _p2 = worldchannel.WorldChannel((_bh, 37531)), worldchannel.WorldChannel((_bh, 19155))
    for _c, _a in ((_p1, "member:3"), (_p2, "member:11")):
        _c.account, _c.key, _c.seen_at = _a, b"xbattle", time.time()
        _c.loc = {"map": 418, "zone": 200}
        groupchannel.WORLD_PEERS[_c.addr] = _c
    _sv_room = (room.ROOM, room.ROOM_SAME_ZONE)
    try:
        flat_globals()["ROOM"], flat_globals()["ROOM_SAME_ZONE"] = True, True
        referee.battle_state("member:3", reset=True)
        referee.battle_state("member:11", reset=True)
        _sq1, _o1 = squad.battle_squad_for(_p1, (64, 5, 64, 0), 2, None)
        _sq2, _o2 = squad.battle_squad_for(_p2, (64, 5, 64, 0), 2, None)
        _per = (_sq1["owner"] == "member:3" and _sq2 is _sq1 and _o2 is _p1
                and referee.BATTLE_STATE["member:3"] is not referee.BATTLE_STATE["member:11"])
    finally:
        flat_globals()["ROOM"], flat_globals()["ROOM_SAME_ZONE"] = _sv_room
        groupchannel.WORLD_PEERS.pop(_p1.addr, None)
        groupchannel.WORLD_PEERS.pop(_p2.addr, None)
        referee.BATTLE_STATE.pop("member:3", None)
        referee.BATTLE_STATE.pop("member:11", None)
        squad.BATTLE_SQUADS.pop(worldchannel.chan_where(_p1), None)
    print(f"  battle per player: two pilots on one address -> ONE squad owner "
          f"(the first), the second joins it, separate battle state: "
          f"{'OK' if _per else 'FAIL'}")
    ok &= _per
    # BATTLE GROUP MEMBERS + VOICE. Two group
    # channels in one group: A's self stream gets a cmd 190 for B (alias, tag,
    # our endpoint, voice bit), the 0x0158 rows name both, and a cmd 123 A sends
    # on B's link reaches B's group SELF stream byte-for-byte.
    if room.PEER_LINK and groupchannel.GROUP_TABLES is not None:
        _ga, _gb2 = ("203.0.113.50", 1427), ("203.0.113.50", 19155)
        _kA, _kB = _ga + ("group",), _gb2 + ("group",)
        _sv = (dict(groupchannel.GROUP_MEMBERS), dict(groupchannel.GROUP_OF), dict(battlegroups.GROUP_CREATOR_ACCOUNT))
        _rost = {}
        _cA, _cB = worldchannel.WorldChannel(_ga), worldchannel.WorldChannel(_gb2)
        for _c, _k, _acc in ((_cA, _kA, "member:3"), (_cB, _kB, "member:11")):
            _c.key, _c.tables, _c.char_id = groupchannel.GROUP_KEY, groupchannel.GROUP_TABLES, 0
            _c.peer_key, _c.account, _c.group_popped = _k, _acc, True
            _c.seen_at = time.time()
            groupchannel.WORLD_PEERS[_k] = _c
        _real_lr = flat_globals()["load_roster"]
        flat_globals()["load_roster"] = lambda a: {
            "member:3": [{"id": 1, "first": "Lex", "last": "Arden", "nation_byte": 1}],
            "member:11": [{"id": 1, "first": "Ned", "last": "Test", "nation_byte": 1}]}.get(a, [])
        try:
            groupchannel.GROUP_MEMBERS.clear(); groupchannel.GROUP_OF.clear()
            groupchannel.group_join(77, "member:11"); groupchannel.group_join(77, "member:3")
            battlegroups.GROUP_CREATOR_ACCOUNT[77] = "member:11"
            _nq = groupchannel.group_queue(_cA)
            _al = _cA.alias_of.get(_kB)
            _rsA = _cA.remotes.get(_al)
            _rec = _cA.pending[-1] if _cA.pending else b""
            _bd = _rec[fmoworld.REC_HDR:]
            _g_ok = (_nq == 1 and _rsA is not None and len(_bd) >= 0x5C
                     and struct.unpack_from("<I", _bd, 0)[0] == _al
                     and _bd[groupchannel.G190_TAG] == _rsA.tag and _bd[groupchannel.G190_VOICE] == 1
                     and _bd[groupchannel.G190_KEY:groupchannel.G190_KEY + 5] == groupchannel.GROUP_KEY
                     and _bd[groupchannel.GROUP_POP_NAME1_OFF:groupchannel.GROUP_POP_NAME1_OFF + 3] == b"Ned"
                     and struct.unpack_from("<I", _bd, groupchannel.GROUP_POP_LEADER_OFF)[0] == 1
                     and groupchannel.group_queue(_cA) == 0)
            _pn, _prows = groupchannel.group_player_rows(77)
            _rows_ok = _pn == 2 and _prows[0:3] == b"Ned" and _prows[0x40:0x43] == b"Lex" \
                and _prows[groupchannel.S158_ROW_NATION] == 1
            groupchannel.group_queue(_cB)                         # B learns A too
            # 0x0163: members for creator AND joiner alike, and a group sortie
            # sets the on-sortie flag + map row (the 9:10 offer).
            _r163 = groupchannel.reply_0163(77)
            _s163_ok = (struct.unpack_from("<I", _r163, groupchannel.S158_PLAYERS_N)[0] == 2
                        and _r163[groupchannel.S163_ON_SORTIE] == 0)
            groupchannel.GROUP_SORTIE[77] = {"map": 267, "sector": 200000020,
                                             "row": struct.pack("<I", 267) + bytes(104),
                                             "at": time.time(), "by": "member:11"}
            _r163 = groupchannel.reply_0163(77)
            _s163_ok &= (_r163[groupchannel.S163_ON_SORTIE] == 1
                         and struct.unpack_from("<I", _r163, groupchannel.S163_SECTOR)[0] == 200000020
                         and struct.unpack_from("<I", _r163, groupchannel.S163_MAPROW)[0] == 267)
            groupchannel.GROUP_SORTIE.pop(77, None)
            # Sortie Setting: A (member:3) goes READY -> a cmd 191 with +0x54
            # bit 1 on A's own channel (its self id) and on B's (A's alias).
            _cA.group_uid = 0x1001
            _nA0, _nB0 = len(_cA.pending), len(_cB.pending)
            groupchannel.GROUP_READY["member:3"] = (1, 0)
            _np = groupchannel.group_push_flags("member:3")
            groupchannel.GROUP_READY.pop("member:3", None)
            _uA = _cA.pending[_nA0:]
            _uB = _cB.pending[_nB0:]

            def _fl(r):
                return struct.unpack_from(
                    "<I", r, fmoworld.REC_HDR + 8 + groupchannel.GROUP_POP_FLAGS_OFF - groupchannel.G191_BLOB_FROM)[0]
            _rdy_ok = (_np == 2 and len(_uA) == 1 and len(_uB) == 1
                       and struct.unpack_from("<I", _uA[0], 4)[0] == 191
                       and struct.unpack_from("<I", _uA[0], fmoworld.REC_HDR)[0] == 0x1001
                       and struct.unpack_from("<I", _uB[0], fmoworld.REC_HDR)[0]
                       == _cB.alias_of.get(_kA)
                       and _fl(_uA[0]) & groupchannel.G_FLAG_READY and _fl(_uB[0]) & groupchannel.G_FLAG_LISTED)
            print(f"  group: 0x0163 lists both members, and a group sortie sets the "
                  f"on-sortie flag + map: {'OK' if _s163_ok else 'FAIL'}; Ready -> a "
                  f"cmd 191 (+0x54 bit 1) on both channels, self id and alias: "
                  f"{'OK' if _rdy_ok else 'FAIL'}")
            ok &= _s163_ok and _rdy_ok
            _cB.pending.clear()
            _gsent = []

            class _GS:
                def sendto(self, d, to):
                    _gsent.append(d)
            _vbody = bytes(range(40))
            _dg = fmoworld.build(*groupchannel.GROUP_TABLES, peer=1, hid=_rsA.tag, kind=5, ack=0,
                                 flag=0, frm=0, to=1,
                                 body=fmoworld.record(123, _vbody, arg8=1))
            datagram._serve_datagram(_GS(), groupchannel.WORLD_PEERS, _dg, _ga)
            # Twin first: B has never sent voice (its voice system may be down
            # -- a client hung on receiving it), so nothing is relayed.
            _vx0 = [r for r in _cB.pending
                    if struct.unpack_from("<I", r, 4)[0] == 123]
            _cB.voice_seen = True                     # B has talked
            _dg2 = fmoworld.build(*groupchannel.GROUP_TABLES, peer=1, hid=_rsA.tag, kind=5,
                                  ack=0, flag=2, frm=1, to=2,
                                  body=fmoworld.record(123, _vbody, arg8=1))
            datagram._serve_datagram(_GS(), groupchannel.WORLD_PEERS, _dg2, _ga)
            _vx = [r for r in _cB.pending
                   if struct.unpack_from("<I", r, 4)[0] == 123]
            _v_ok = (not _vx0 and bool(_vx)
                     and _vx[0][fmoworld.REC_HDR:fmoworld.REC_HDR + 40] == _vbody)
        finally:
            flat_globals()["load_roster"] = _real_lr
            groupchannel.WORLD_PEERS.pop(_kA, None); groupchannel.WORLD_PEERS.pop(_kB, None)
            groupchannel.GROUP_MEMBERS.clear(); groupchannel.GROUP_MEMBERS.update(_sv[0])
            groupchannel.GROUP_OF.clear(); groupchannel.GROUP_OF.update(_sv[1])
            battlegroups.GROUP_CREATOR_ACCOUNT.clear(); battlegroups.GROUP_CREATOR_ACCOUNT.update(_sv[2])
        print(f"  group: the other member arrives as a cmd 190 on the group self "
              f"stream (alias, tag, key, names, leader, voice bit), once: "
              f"{'OK' if _g_ok else 'FAIL'}; the 0x0158 Player List names both "
              f"(nation byte set): {'OK' if _rows_ok else 'FAIL'}; a cmd 123 on "
              f"the member link reaches the other's group self stream verbatim: "
              f"{'OK' if _v_ok else 'FAIL'}")
        ok &= _g_ok and _rows_ok and _v_ok
    _wb4 = _acm == "member:11" and _acb == "member:3"
    print(f"  world binding: a room-mate's pop is named AS the mate, then the "
          f"served channel again: {'OK' if _wb4 else 'FAIL ' + repr((_acm, _acb))}")
    ok &= _wb4

    # REGRESSION: the TITLE MENU GATE. An empty roster is NOT 'no character'
    # to FMO -- the menu selector at 0x61042CD0 takes descriptors[ eax < 0 ]
    # where eax = 0x61174E50 = 'find a slot whose name is empty', and a count
    # of 0 makes that search fail (-8), which selects the HAS-CHARACTER menu.
    # Serving the honest empty list therefore HID 'Create Character' and
    # offered Start Game / Change Nations / Delete on an account with no
    # pilot. These four cases are the client's rule, transcribed.
    def _slot0(roster):
        pl = charlist.roster_payload(roster)
        n = struct.unpack_from('<I', pl, 0)[0]
        cid = struct.unpack_from('<I', pl, 4)[0]
        return n, cid, pl[4 + 0x04]

    n, cid, name0 = _slot0([])
    empty_ok = n == 1 and cid != 0 and name0 == 0
    print(f'  no character -> one EMPTY-NAMED slot with a non-zero id '
          f'(count={n} id={cid} name[0]={name0}) '
          f'{"OK" if empty_ok else "FAIL"}')
    ok &= empty_ok

    named = [{'id': 1, 'first': 'Lex', 'last': 'Arden', 'nation': 1}]
    n, cid, name0 = _slot0(named)
    named_ok = n == 1 and name0 != 0
    print(f'  a character exists -> NO free slot appended (count={n}) '
          f'{"OK" if named_ok else "FAIL"}')
    ok &= named_ok

    # A half-created pilot (0x0177 upserted the id, no name yet) IS the free
    # slot; do not add a second one.
    half = [{'id': 3, 'first': '', 'last': '', 'nation': 0}]
    n, cid, name0 = _slot0(half)
    half_ok = n == 1 and cid == charlist.to_wire(3) and name0 == 0   # the WIRE id
    print(f'  half-created pilot is itself the free slot (count={n} id={cid}) '
          f'{"OK" if half_ok else "FAIL"}')
    ok &= half_ok

    # WIRE IDS (live 2026-09-27: the unit tick skips UnitID < 10, so a pilot
    # selected as character 1 never sent its own movement). The client sees
    # store id + CHAR_WIRE_BASE everywhere; the store keeps its ids.
    if charlist.CHAR_WIRE_BASE:
        _w = charlist.to_wire(1)
        _, _wcid, _ = _slot0([{"id": 1, "first": "Lex", "last": "Arden"}])
        _ss = session.Session("selftest-wire")
        _ss._roster = [{"id": 1, "first": "Lex", "last": "Arden"},
                       {"id": 2, "first": "Deck", "last": "Guy"}]
        _f_ok = (_ss.find(_w) is _ss._roster[0] and _ss.find(1) is _ss._roster[0]
                 and _ss.find(charlist.to_wire(2)) is _ss._roster[1])
        _store_was = charstore.CHAR_STORE
        flat_globals()["CHAR_STORE"] = ""
        try:
            _ss.apply_charsel(0x013F, struct.pack("<I", _w), _w)
        finally:
            flat_globals()["CHAR_STORE"] = _store_was
        _del_ok = [c["id"] for c in _ss._roster] == [2]
        _sb = zoneentry.setup_block(self_id=_w)
        _sb_ids = struct.unpack_from("<8I", _sb, zoneentry.SU_UNITS)
        _sb_ok = _sb_ids[0] == _w and (not popsweep.POP or popsweep.POP[0] not in _sb_ids)
        _wc = worldchannel.WorldChannel(("198.51.100.9", 19155))
        _cand = [cid for cid, _k in _wc.candidates()]
        _wc.char_id = _w
        _su_ok = _w in _cand and _cand.index(_w) < _cand.index(1) \
            and _wc.self_unit() == _w
        _wire_ok = (_wcid == _w >= 10 and _f_ok and _del_ok and _sb_ok
                    and _su_ok and charlist.from_wire(_w) == 1 and charlist.from_wire(1) == 1)
        print(f"  wire ids: the list serves {_w:#x} for store id 1, find() takes "
              f"either, a delete by wire id removes store id 1, the 0x0153 setup "
              f"block leads with it, the UDP key tries it first and self_unit() "
              f"becomes it: {'OK' if _wire_ok else 'FAIL ' + repr((_wcid, _f_ok, _del_ok, _sb_ok, _su_ok))}")
        ok &= _wire_ok

    # REGRESSION: creation is TWO messages against ONE slot (0x0177 names it,
    # 0x013E completes it). 0x013E used to append unconditionally, and
    # next_free_id then minted a SECOND id because the name step had taken the
    # first -- one creation, two identical pilots, measured live 2026-08-23.
    # WARNING: The apply_charsel exercises below MUST NOT write the real store --
    # commit() -> save_roster() honours CHAR_STORE at call time, and this
    # block used to leave an 'addr:selftest-create' row in the dev (or, run
    # in the container, the PROD) fmo_characters.json. Found 2026-08-24 by
    # reading the dev store. globals() because a bare assignment would need a
    # `global` statement, and the read of CHAR_STORE above would then be a
    # syntax error.
    _store_was = charstore.CHAR_STORE
    flat_globals()["CHAR_STORE"] = ""
    try:
        s4 = session.Session("selftest-create")
        s4._roster = [{"id": 1, "first": "Lex", "last": "Arden", "nation": 0,
                       "sex": 1, "cls": 1, "hangar_pw": 0,
                       "appearance": "00000000", "raw": ""}]
        body = bytearray(0x3C)
        struct.pack_into("<I", body, 0x00, 1)
        body[0x04:0x07] = b"Lex"
        body[0x15:0x1A] = b"Arden"
        body[0x26] = 1
        s4.apply_charsel(0x013E, bytes(body), 1)
        one_row = len(s4._roster) == 1 and s4._roster[0]["id"] == 1
        print(f'  0x013E COMPLETES the name-step row, does not duplicate '
              f'(rows={len(s4._roster)}) {"OK" if one_row else "FAIL"}')
        ok &= one_row

        # REGRESSION: 0x013F DELETE. Success is removal of EXACTLY the named
        # row (empty message 1 -> the client's 0x61173A10 then drops it from
        # its LOCAL list, UI event 0x1074); an id we do not hold returns a
        # reason -> message 2 + FAIL_CODE, the displayable "Character delete
        # failed" -- answering success for a delete that did not happen is
        # the habit apply_charsel exists to end.
        s5 = session.Session("selftest-delete")
        s5._roster = [{"id": 1, "first": "Lex", "last": "Arden"},
                      {"id": 2, "first": "Deck", "last": "Guy"}]
        why = s5.apply_charsel(0x013F, struct.pack("<I", 1), 1)
        left = [c["id"] for c in s5._roster]
        del_ok = why is None and left == [2]
        print(f'  0x013F removes exactly the named id (left={left}) '
              f'{"OK" if del_ok else "FAIL"}')
        ok &= del_ok

        why = s5.apply_charsel(0x013F, struct.pack("<I", 1), 1)
        miss_ok = isinstance(why, str) and [c["id"] for c in s5._roster] == [2]
        print(f'  0x013F for an absent id REFUSES with a reason, roster '
              f'untouched {"OK" if miss_ok else "FAIL"}')
        ok &= miss_ok

        # ...and deleting the LAST character must land the account back on
        # the CREATE menu: the empty roster serves one empty-named slot (the
        # title-gate rule above), not zero rows.
        s5.apply_charsel(0x013F, struct.pack("<I", 2), 2)
        n, cid, name0 = _slot0(s5._roster)
        cycle_ok = not s5._roster and n == 1 and name0 == 0
        print(f'  deleting the last character serves the empty-slot list '
              f'again (count={n} name[0]={name0}) '
              f'{"OK" if cycle_ok else "FAIL"}')
        ok &= cycle_ok
    finally:
        flat_globals()["CHAR_STORE"] = _store_was

    # REGRESSION (live 2026-09-27): Change Nations names no nation (+0x28 = 0
    # -- Ned's real submit bytes below), so the handler left every player in
    # the nation they had just left. It must SWITCH, both ways; the twin
    # (toggle off) must leave it alone. A temp store, never the real one.
    import tempfile as _tf
    from . import defection as _dfx
    _store_was = charstore.CHAR_STORE
    _dfx_was = _dfx.DEFECTION
    with _tf.TemporaryDirectory() as _td:
        flat_globals()["CHAR_STORE"] = os.path.join(_td, "chars.json")
        # This pins the toggle alone; Ned is Pilot level 1, which the
        # defection rules (pinned below) refuse.
        _dfx.DEFECTION = False
        try:
            _sb = bytearray(0x38)                # Ned's live 18:33:38Z submit
            struct.pack_into("<I", _sb, 0, 1)
            _sb[0x04:0x07], _sb[0x15:0x19], _sb[0x26] = b"Ned", b"Test", 1
            _sub = bytes(_sb)                    # +0x28 stays 0, as on the wire
            _res = []
            for _toggle, _start in ((True, 2), (True, 1), (False, 2)):
                flat_globals()["NATION_CHANGE_TOGGLE"] = _toggle
                s6 = session.Session("selftest-nation")
                s6._roster = [{"id": 1, "first": "Ned", "last": "Test",
                               "nation_byte": _start, "gender": 1}]
                s6.on_packet(packet.parse(packet.build(0x01AA, _sub, 0x100C)))
                _res.append(s6._roster[0].get("nation_byte"))
        finally:
            flat_globals()["CHAR_STORE"] = _store_was
            flat_globals()["NATION_CHANGE_TOGGLE"] = True
            _dfx.DEFECTION = _dfx_was
    _nc = _res == [1, 2, 2]
    print(f"  Change Nations with +0x28 = 0 switches U.S.N.->O.C.U. and "
          f"O.C.U.->U.S.N.; toggle off leaves it (got {_res}): "
          f"{'OK' if _nc else 'FAIL'}")
    ok &= _nc

    # DEFECTION (SE update 050906 97-108, news5570, topics 060227): the
    # verdict per rule with SE's refusal codes (client table 0x613955F0 ->
    # systext 2:109..2:114), then through the 0x01AA arm: a refusal is
    # message 2 with the code in +0x08 and stores NOTHING; an allowed one
    # flips the nation, drops one rank, sells unequipped items at their price
    # and starts the 30-day clock.
    _dc = classes.CLASS_CURVE
    _lv5 = _dc[4] if len(_dc) > 4 else 0
    _lv15 = _dc[14] if len(_dc) > 14 else 0
    _now = 2_000_000_000
    _peace = (1, 0, _now + 60 * 86400, _now + 64 * 86400, False)
    _near = (1, 0, _now + 2 * 86400, _now + 6 * 86400, False)

    def _pilot(nat, rank=10, lv_exp=_lv5, **kw):
        return dict({"id": 1, "first": "Def", "last": "Ector", "nation_byte": nat,
                     "rank": rank, "class_exp": {"12": lv_exp}}, **kw)

    def _pop(n_ocu, n_usn):
        return [("x", [_pilot(1, lv_exp=_lv15) for _ in range(n_ocu)]
                 + [_pilot(2, lv_exp=_lv15) for _ in range(n_usn)])]

    _dv = lambda c, was, pop, ph=_peace: _dfx.defection_verdict(c, was, 3 - was, pop, now=_now, phase=ph)[0]
    _dfx_ok = (_dv(_pilot(1), 1, _pop(5, 3)) is None                           # larger -> smaller
               and _dv(_pilot(1), 1, _pop(3, 5)) == -14122                     # smaller O.C.U. -> 2:110
               and _dv(_pilot(2), 2, _pop(5, 3)) == -14127                     # smaller U.S.N. -> 2:111
               and _dv(_pilot(2), 2, _pop(10, 10)) is None                     # even: either way
               and _dv(_pilot(1, rank=18), 1, _pop(5, 3)) == -14124            # 2nd Lt: too high
               and _dv(_pilot(1, rank=17), 1, _pop(5, 3)) is None              # CWO is the cap
               and _dv(_pilot(1, lv_exp=0), 1, _pop(5, 3)) == -14125           # Pilot Lv 1
               and _dv(_pilot(1, last_defected_at=_now - 29 * 86400), 1, _pop(5, 3)) == -14121
               and _dv(_pilot(1, last_defected_at=_now - 30 * 86400), 1, _pop(5, 3)) is None
               and _dv(_pilot(1), 1, _pop(5, 3), _near) == -14122              # run-up lock
               and _dfx.population(_pop(2, 1) + [("y", [_pilot(2, lv_exp=_lv5)])]) == {1: 2, 2: 1}
               and _lv5 > 0 and _lv15 > _lv5)
    # through the arm
    _store_was = charstore.CHAR_STORE
    _lock_was = _dfx.LOCK_DAYS
    _arm = {}
    with _tf.TemporaryDirectory() as _td:
        flat_globals()["CHAR_STORE"] = os.path.join(_td, "chars.json")
        _dfx.LOCK_DAYS = 0
        try:
            _sb = bytearray(0x38)
            struct.pack_into("<I", _sb, 0, 1)
            _sb[0x04:0x07], _sb[0x15:0x19], _sb[0x26] = b"New", b"Name", 1
            for _tag, _c in (("low", _pilot(1, lv_exp=0)),
                             ("ok", _pilot(1, money=1000, contribution=150000, items=[
                                 {"serial": 7, "id": 3, "kind": 0x12, "price": 2500},
                                 {"serial": 8, "id": 4, "kind": 0x13, "price": 0}]))):
                _s7 = session.Session("selftest-defect")
                _s7._roster = [_c]
                _s7.all_rosters = lambda: _pop(5, 3)
                _o = _s7.on_packet(packet.parse(packet.build(0x01AA, bytes(_sb), 0x100C)))
                _q = packet.parse(_o[0]) if _o else {}
                _arm[_tag] = (_q.get("msg"), _q.get("conn"), dict(_s7._roster[0]))
                if _tag == "ok":           # a second try inside 30 days
                    _o = _s7.on_packet(packet.parse(packet.build(0x01AA, bytes(_sb), 0x100D)))
                    _arm["again"] = packet.parse(_o[0])["conn"] if _o else None
        finally:
            flat_globals()["CHAR_STORE"] = _store_was
            _dfx.LOCK_DAYS = _lock_was
    _lo, _okd = _arm.get("low", (None, None, {})), _arm.get("ok", (None, None, {}))
    _dfx_ok &= (_lo[0] == charselect.MSG_FAIL and _lo[1] == (-14125 & 0xFFFF)
                and _lo[2].get("nation_byte") == 1 and _lo[2].get("first") == "Def"
                and _okd[0] == 1 and _okd[2].get("nation_byte") == 2
                and _okd[2].get("first") == "New" and _okd[2].get("rank") == 9
                and _okd[2].get("money") == 3500 and not _okd[2].get("items")
                and _okd[2].get("contribution", 0) < ranks.rank_threshold(10)
                and isinstance(_okd[2].get("last_defected_at"), int)
                and _arm.get("again") == (-14121 & 0xFFFF))
    print(f"  defection: larger->smaller or even, CWO cap, Pilot Lv 5, 30 days, "
          f"run-up lock, Lv 15+ head count; the arm refuses with message 2 + "
          f"SE's code and stores nothing, or flips, drops a rank, sells items: "
          f"{'OK' if _dfx_ok else 'FAIL'} (arm {_arm.get('low', ())[:2]}, "
          f"{_arm.get('ok', ())[:2]}, again {_arm.get('again')})")
    ok &= _dfx_ok

    # HANGAR RANK (update 050628 71-72): jobs 1..8 at FMO_HANGAR_JOB_LEVEL+
    # -> the byte; 0x014A serves the BANKED rank at +0x39, the battle end
    # banks and serves it at 0x014C +0x0F5; the client table gives rank 0 =
    # 2 wanzers / 80 items (AI/F00/D94 3).
    _hlv = hangar.HANGAR_JOB_LEVEL
    _hx = _dc[_hlv - 1] if 0 < _hlv <= len(_dc) else 0
    _hch = {"class_exp": {"1": _hx, "3": _hx, "5": _hx, "12": _hx * 10, "2": 0}}
    _h14a = status.reply_014a(char=dict(_hch, hangar_rank=3))
    _h14a0 = status.reply_014a(char=dict(_hch))
    _hang_ok = (_hlv > 0 and _hx > 0
                and hangar.hangar_rank_earned(_hch) == 3                # Pilot (12) not counted
                and hangar.hangar_rank_earned(_hch, level=0) == 0
                and hangar.hangar_capacity(0) == (2, 80) and hangar.hangar_capacity(3) == (4, 140)
                and hangar.hangar_capacity(40) == (8, 250)
                and _h14a[0x39] == 3 and _h14a0[0x39] == 0
                and status.S14A_HANGAR_RANK == 0x39)
    _hc_was = hangar.HANGAR_JOB_LEVEL
    _hbe = {}
    try:
        for _hl in (_hlv, 0):
            hangar.HANGAR_JOB_LEVEL = _hl
            _hs = session.Session.__new__(session.Session)
            _hs.peer = "selftest-hangar"
            _hs.battle_settlement = None
            _hs.last_0159 = b""
            _hpc = dict(_hch)
            _hs.playing_char = lambda _p=_hpc: _p
            _hs.stored_money = lambda: (0, 0)
            _hs.credit_money = lambda why, money=0, contribution=0: (0, 0)
            _hs.credit_class_exp = lambda why, rows: {}
            _hs.commit = lambda what: None
            _hp = _hs.battle_end_push(0x1234, why="selftest", won=True)
            _hbe[_hl] = (_hp[0x14 + battleend.S14C_HANGAR_RANK] if _hp else None,
                         _hpc.get("hangar_rank"))
    finally:
        hangar.HANGAR_JOB_LEVEL = _hc_was
    _hang_ok &= _hbe.get(_hlv) == (3, 3) and _hbe.get(0) == (0, None)
    print(f"  hangar rank: 3 jobs at Lv {_hlv}+ -> 3; 0x014A +0x39 serves the banked "
          f"rank, the battle end banks it and serves 0x014C +0x0F5 {_hbe}; knob 0 "
          f"-> 0; table 2/80 .. 8/250: {'OK' if _hang_ok else 'FAIL'}")
    ok &= _hang_ok

    # FRIENDLY-FIRE PENALTY + RETRAINING: see penalty_pins (and penalty.py)
    ok &= penalty_pins()
    # THE HANGAR MECHANIC'S PERMIT SALE: see hangar_permit_pins (and permits.py)
    ok &= hangar_permit_pins()
    # THE SOLO AREA: see solo_pins (and solo.py)
    ok &= solo_pins()
    # a revoked pilot is also refused platoon and mission clearance (D64 59-61):
    # the accept verdict answers with the penalty before any other gate
    _pc_bad = {"penalty_revoked": 1, "penalty_points": 3, "rank": 30}
    _pc_ok = {"penalty_revoked": 0, "rank": 30}
    _pen_ok = (penalty.clearance_refusal(_pc_bad, "platoon") is not None
               and penalty.clearance_refusal(_pc_ok, "platoon") is None
               and "PENALTY" in (missionbook.mission_accept_verdict({"name": "x", "rank": 0, "fee": 0}, _pc_bad)[1] or ""))
    print(f"  penalty: a revoked pilot is refused platoon and mission clearance too: "
          f"{'OK' if _pen_ok else 'FAIL'}")
    ok &= _pen_ok

    # REGRESSION: 0x0166 was served as 4357 zeros with no builder, so every
    # setup read "Setup empty" and the pilot's part-model objects came back
    # blank (kind=0) with the render node at its constructor AABB. These pin
    # the layout, the revert path, and the kind gate.
    empty = inventory.reply_0166(parts=[])
    revert_ok = (len(empty) == inventory.REPLY_0166_LEN and not any(empty))
    print(f'  0x0166 with no parts is byte-identical to the old all-zero reply '
          f'({len(empty)}B) {"OK" if revert_ok else "FAIL"}')
    ok &= revert_ok

    body = inventory.reply_0166(parts=[(0, 0x11, 5), (1, 0x12, 9)])
    in_use = body[inventory.SETUP_IN_USE] == 1
    i0 = body[inventory.SETUP_ITEM_OFF:inventory.SETUP_ITEM_OFF + inventory.INV_ENTRY_LEN]
    i1 = body[inventory.SETUP_ITEM_OFF + inventory.INV_ENTRY_LEN:inventory.SETUP_ITEM_OFF + 2 * inventory.INV_ENTRY_LEN]
    fields_ok = (struct.unpack_from("<H", i0, inventory.ITEM_ID)[0] == 5
                 and i0[inventory.ITEM_KIND] == 0x11
                 and struct.unpack_from("<H", i1, inventory.ITEM_ID)[0] == 9
                 and i1[inventory.ITEM_KIND] == 0x12
                 and struct.unpack_from("<I", i0, inventory.ITEM_SERIAL_LO)[0]
                 != struct.unpack_from("<I", i1, inventory.ITEM_SERIAL_LO)[0])
    print(f'  0x0166 setup 1 IN USE, items land at +0x28 stride 24 with unique '
          f'serials {"OK" if (in_use and fields_ok) else "FAIL"}')
    ok &= in_use and fields_ok

    # REGRESSION 2026-09-08: a CORRECT garage block still showed "-Nothing-" in
    # every wanzer slot, because 0x0133 was 9,608 zeros. The client resolves an
    # equipped part by searching the inventory for its 64-bit serial
    # (0x61177BE0), so a setup serial with no inventory entry renders empty.
    # THE INVARIANT: every serial 0x0166 equips must appear in 0x0133.
    def _serials_in_setups(block):
        out = set()
        for si in range(inventory.SETUP_SLOTS):
            base = si * inventory.SETUP_ENTRY_LEN
            for i in range(inventory.SETUP_ITEMS):
                o = base + inventory.SETUP_ITEM_OFF + i * inventory.INV_ENTRY_LEN
                rec = block[o:o + inventory.INV_ENTRY_LEN]
                if any(rec):
                    out.add(struct.unpack_from("<Q", rec, inventory.ITEM_SERIAL_LO)[0])
        return out

    _blk = inventory.reply_0166(parts=inventory.STARTER_SETUPS[(1, 3)], slots=1)
    _inv = inventory.inventory_from_setups(_blk)
    _body = inventory.reply_0133(_inv)
    _count = struct.unpack_from("<I", _body, 0)[0]
    _inv_serials = {struct.unpack_from("<Q", _body, 8 + i * inventory.INV_ENTRY_LEN)[0]
                    for i in range(_count)}
    _want = _serials_in_setups(_blk)
    cover = _want and _want <= _inv_serials
    print(f'  0x0133 carries every serial 0x0166 equips '
          f'({len(_want)} equipped, count {_count}) {"OK" if cover else "FAIL"}')
    ok &= bool(cover)

    len_ok = len(_body) == inventory.REPLY_0133_LEN and _count == len(_inv)
    print(f'  0x0133 is {inventory.REPLY_0133_LEN}B with the count at +0x00 '
          f'{"OK" if len_ok else "FAIL"}')
    ok &= len_ok

    # The records must be the SAME BYTES, not a re-authoring: id and kind have
    # to survive the copy or the client resolves a serial to the wrong part.
    _e0 = _body[8:8 + inventory.INV_ENTRY_LEN]
    _s0 = _blk[inventory.SETUP_ITEM_OFF:inventory.SETUP_ITEM_OFF + inventory.INV_ENTRY_LEN]
    same = _e0 == _s0
    print(f'  0x0133 entry 0 is byte-identical to the equipped record '
          f'{"OK" if same else "FAIL"}')
    ok &= same

    # All eight setups use DISTINCT serial ranges, so FMO_SETUP_FILL=8 must not
    # collapse them: 8 x 6 parts = 48 entries, none deduplicated away.
    _blk8 = inventory.reply_0166(parts=inventory.STARTER_SETUPS[(1, 3)], slots=8)
    _inv8 = inventory.inventory_from_setups(_blk8)
    fill_ok = len(_inv8) == 8 * len(inventory.STARTER_SETUPS[(1, 3)])
    print(f'  0x0133 covers all eight setups distinctly at FILL=8 '
          f'({len(_inv8)} entries) {"OK" if fill_ok else "FAIL"}')
    ok &= fill_ok

    # An empty garage means an empty inventory -- the revert stays byte-exact.
    empty_inv = inventory.reply_0133(inventory.inventory_from_setups(inventory.reply_0166(parts=[])))
    inv_revert = (len(empty_inv) == inventory.REPLY_0133_LEN and not any(empty_inv))
    print(f'  0x0133 with an empty garage is the old all-zero reply '
          f'{"OK" if inv_revert else "FAIL"}')
    ok &= inv_revert

    # REGRESSION GUARD: the ITEM INDEX has to be honoured. The backpack lives
    # at item 10 because part slot 10 reads item 10; a builder that packed the
    # list densely would put it at item 5 and equip it as an arm mount, and
    # 0x611F5700 would take that silently.
    sparse = inventory.reply_0166(parts=[(0, 0x11, 5), (10, 0x41, 1)])
    at10 = sparse[inventory.SETUP_ITEM_OFF + 10 * inventory.INV_ENTRY_LEN:
                  inventory.SETUP_ITEM_OFF + 11 * inventory.INV_ENTRY_LEN]
    gap = sparse[inventory.SETUP_ITEM_OFF + inventory.INV_ENTRY_LEN:
                 inventory.SETUP_ITEM_OFF + 10 * inventory.INV_ENTRY_LEN]
    sparse_ok = (at10[inventory.ITEM_KIND] == 0x41
                 and struct.unpack_from("<H", at10, inventory.ITEM_ID)[0] == 1
                 and not any(gap))
    print(f'  0x0166 honours the ITEM INDEX (backpack at item 10, 1-9 zero) '
          f'{"OK" if sparse_ok else "FAIL"}')
    ok &= sparse_ok

    # REGRESSION GUARD: every starter loadout must land on part slots the
    # world builder actually reads, and must cover the four body parts. This
    # is the check that would have caught the dense-list bug above by its
    # EFFECT rather than its shape.
    starter_ok = True
    for (nat, cls), rows in sorted(inventory.STARTER_SETUPS.items()):
        idxs = {i for i, _, _ in rows}
        read = {inventory.SLOT_TO_ITEM[s] for s in inventory.WORLD_PART_SLOTS}
        body_parts = sorted(k for _, k, _ in rows if k in (0x11, 0x21, 0x31))
        good = (idxs <= read
                and body_parts == [0x11, 0x21, 0x31, 0x31]
                and all(v > 0 for _, _, v in rows)
                and all(k in inventory.VALID_ITEM_KINDS for _, k, _ in rows))
        starter_ok &= good
        if not good:
            print(f'    nation {nat} class {cls}: {rows}')
    print(f'  STARTER_SETUPS: {len(inventory.STARTER_SETUPS)} loadouts, every item index '
          f'is one 0x61002FEB reads, each has body+2 arms+legs '
          f'{"OK" if starter_ok else "FAIL"}')
    ok &= starter_ok

    # ...and the default path must actually produce one, because "we serve the
    # honest empty answer" is this repo's most repeated root cause.
    dflt = inventory.reply_0166(nation=1, cls=3)
    dflt_ok = dflt[inventory.SETUP_IN_USE] == 1 and any(dflt[:inventory.SETUP_ENTRY_LEN])
    print(f'  0x0166 default for a known (nation, class) is NOT empty '
          f'{"OK" if dflt_ok else "FAIL"}')
    ok &= dflt_ok

    unknown = inventory.setup_parts_for(9, 9)
    unk_ok = unknown[0] == inventory.STARTER_SETUPS[inventory.STARTER_FALLBACK] and "fall" in unknown[1]
    print(f'  0x0166 falls back LOUDLY on a pair 0x611E1F90 rejects '
          f'{"OK" if unk_ok else "FAIL"}')
    ok &= unk_ok

    # setup 2..8 must stay empty at the DEFAULT fill of 1
    rest = body[inventory.SETUP_ENTRY_LEN:inventory.SETUP_TAIL_OFF]
    rest_ok = not any(rest)
    print(f'  0x0166 setups 2-8 stay empty at FMO_SETUP_FILL=1 '
          f'{"OK" if rest_ok else "FAIL"}')
    ok &= rest_ok

    # ...and FMO_SETUP_FILL=8 must fill all eight with DISTINCT serials, since
    # the client finds an equipped item by its 64-bit serial (0x61174860) and
    # eight setups sharing one serial space would be one item seen eight times.
    all8 = inventory.reply_0166(parts=[(0, 0x11, 5), (10, 0x41, 1)], slots=8)
    marks = [all8[i * inventory.SETUP_ENTRY_LEN + inventory.SETUP_IN_USE] for i in range(8)]
    ser = [struct.unpack_from("<I", all8,
                              i * inventory.SETUP_ENTRY_LEN + inventory.SETUP_ITEM_OFF)[0]
           for i in range(8)]
    fill_ok = (all(m == 1 for m in marks) and len(set(ser)) == 8
               and not any(all8[inventory.SETUP_TAIL_OFF:]))
    print(f'  0x0166 FMO_SETUP_FILL=8 marks all eight IN USE with distinct '
          f'serials {"OK" if fill_ok else "FAIL"}')
    ok &= fill_ok

    tile_ok = (inventory.SETUP_ITEM_OFF + inventory.SETUP_ITEMS * inventory.INV_ENTRY_LEN == inventory.SETUP_ENTRY_LEN
               and inventory.SETUP_SLOTS * inventory.SETUP_ENTRY_LEN == inventory.SETUP_TAIL_OFF)
    print(f'  0x0166 tiling: 0x28 + 21x24 = {inventory.SETUP_ENTRY_LEN:#x} and '
          f'8x{inventory.SETUP_ENTRY_LEN} = {inventory.SETUP_TAIL_OFF:#x} '
          f'{"OK" if tile_ok else "FAIL"}')
    ok &= tile_ok

    # a kind with no master table must be dropped, not served as a dud
    dropped = inventory.parse_setup_parts(["11:1", "99:1", "nope", "10=41:1", "77=11:1"])
    drop_ok = dropped == [(0, 0x11, 1), (10, 0x41, 1)]
    print(f'  FMO_SETUP_PARTS drops bad kinds/indices, keeps `idx=kind:id` '
          f'(got {dropped}) {"OK" if drop_ok else "FAIL"}')
    ok &= drop_ok

    # REGRESSION: 0x016D's +0x04 is the picked entry's OWN ID, echoed back
    # from the 0x016F list -- NOT a row index. Measured live 2026-08-23: with
    # ids 101,102,121..161 served, the client sent back 101, 124, 102 and 161.
    # Granting it unchecked would be a wire-controlled MapNo, and a MapNo with
    # no file crashes the client at 0x611250A2.
    def _pick(ident):
        req = bytearray(move.MOVE_REQ_LEN)
        struct.pack_into("<II", req, move.M16D_FIELD_00, 0, ident)
        return struct.unpack_from("<I", bytes(req), move.M16D_FIELD_04)[0]

    pick_ok = _pick(124) == 124 and _pick(101) == 101
    print(f'  0x016D +0x04 round-trips the picked id '
          f'{"OK" if pick_ok else "FAIL"}')
    ok &= pick_ok

    guard_ok = (124 in zoneentry.VALID_MAPNOS and 999 not in zoneentry.VALID_MAPNOS
                and all(m in zoneentry.VALID_MAPNOS
                        for m, _ in move.parse_move_list(["101:3", "161:2"])))
    print(f'  a picked MapNo is gated on VALID_MAPNOS before it is granted '
          f'{"OK" if guard_ok else "FAIL"}')
    ok &= guard_ok

    named_gate = ((not charlist.has_named_character([])) and
                  (not charlist.has_named_character(half)) and
                  charlist.has_named_character(named))
    print(f'  has_named_character: [] -> False, unnamed -> False, '
          f'named -> True  {"OK" if named_gate else "FAIL"}')
    ok &= named_gate

    # REGRESSION: on_packet must NOT arm the cipher, only request it. This file
    # shipped the opposite once, and the symptom was subtle -- the 0x0322 reply
    # and message 1 both went out encrypted to a client that had not armed, so
    # every later packet was garbage with no obvious first cause.
    s2 = session.Session("selftest")
    creds = struct.pack("<I", 0) * 13 + struct.pack("<I", 0x0A) + b"\x11" * 16 \
        + b"\x00" * 8
    s2.on_packet(packet.parse(packet.build(handshake.MSG_VERSION, bytes(40), 0x1001)))
    s2.on_packet(packet.parse(packet.build(handshake.MSG_CREDENTIALS, creds, 0x1002)))
    # Arming belongs to the 0x15B exchange on the GAME connection, not to the
    # credentials: message 1 is 0x15B's reply. Drive the real path.
    batch = s2.on_packet(packet.parse(packet.build(handshake.MSG_GAME_HELLO, bytes(4), 0x1002)))
    armed_early = s2.tx is not None
    got = [packet.parse(b)["msg"] for b in batch]
    print(f"  0x15B answered with message 1: {[hex(x) for x in got]} "
          f"{'OK' if got == [handshake.MSG_SESSION_START] else 'FAIL'}")
    ok &= got == [handshake.MSG_SESSION_START]
    print(f"  arming deferred until that reply is sent: "
          f"{'OK' if not armed_early and s2.pending_arm is not None else 'FAIL'}")
    ok &= (not armed_early) and s2.pending_arm is not None

    # THE PS2 CREDENTIALS FORM. 52 bytes is the console's whole message
    # (midas.pex 0x00309294 `addiu a2, zero, 52`), not a short read -- and the
    # console hung on "communicating with the server" for as long as this
    # parser answered None. Pin both forms and the reply the console parses.
    ps2_c = packet.parse_credentials(b"\x5a" * packet.CRED_LEN_PS2)
    pc_c = packet.parse_credentials(creds)
    form_ok = (ps2_c is not None and ps2_c["form"] == "ps2"
               and ps2_c["region"] is None and ps2_c["identity"] is None
               and ps2_c["auth"] == b"\x5a" * 0x34
               and pc_c is not None and pc_c["form"] == "pc"
               and pc_c["region"] == 0x0A
               and packet.parse_credentials(b"\x00" * 0x40) is None)
    print(f"  0x0321 decodes BOTH senders (52B PS2 / 80B PC) and still "
          f"refuses any other size  {'OK' if form_ok else 'FAIL'}")
    ok &= form_ok

    s3 = session.Session("selftest-ps2")
    s3.on_packet(packet.parse(packet.build(handshake.MSG_VERSION, bytes(40), 0x1001)))
    ps2_batch = [packet.parse(b) for b in
                 s3.on_packet(packet.parse(packet.build(handshake.MSG_CREDENTIALS,
                                                        b"\x5a" * packet.CRED_LEN_PS2, 0x1002)))]
    ps2_reply = ps2_batch[0] if ps2_batch else None
    # 0x003093a8 reads field A at +0x0C and two 20-byte endpoints at +0x14 and
    # +0x28; the last byte it touches is +0x3B. A reply shorter than that, or
    # with an endpoint anywhere else, is one the console cannot dial.
    ps2_ok = (ps2_reply is not None
              and ps2_reply["msg"] == handshake.MSG_CRED_REPLY
              and len(ps2_reply["payload"]) == handshake.CRED_REPLY_LEN == 0x3C
              and addressing.EP1_OFF == 0x14 and addressing.EP2_OFF == 0x28 and addressing.ENDPOINT_LEN == 0x14
              and handshake.FIELD_A_OFF == 0x0C
              and ps2_reply["payload"][addressing.EP1_OFF:addressing.EP1_OFF + addressing.ENDPOINT_LEN]
              == addressing.endpoint(addressing.NEXT_HOST, addressing.NEXT_PORT))
    print(f"  a PS2 0x0321 is answered with 0x0322, {handshake.CRED_REPLY_LEN}B, "
          f"endpoint 1 at +0x{addressing.EP1_OFF:02X}  {'OK' if ps2_ok else 'FAIL'}")
    ok &= ps2_ok

    # THE CONSOLE'S MAP SET. A PS2 session must not be handed the PC's MapNo:
    # 102 rendered as an empty void on the console 2026-09-09 (entities drew,
    # terrain never loaded). The substitution is build-gated, so the PC path
    # must be untouched by it.
    s3.version = "ps2jp_050324_1531"
    pcs = session.Session("selftest-pc"); pcs.version = "verwinjp_060823_1647"
    build_ok = (s3.is_ps2 and not pcs.is_ps2
                and s3.build_mapno(102, "test") == zoneentry.MAPNO_PS2
                and pcs.build_mapno(102, "test") == 102
                and zoneentry.MAPNO_PS2 in zoneentry.PS2_VALID_MAPNOS)
    print(f"  a PS2 session is served MapNo {zoneentry.MAPNO_PS2}, a PC session keeps "
          f"its own  {'OK' if build_ok else 'FAIL'}")
    ok &= build_ok
    # THE GATE (2026-09-21, hardware crashes on a missing map). Every PC room
    # map outside the console's set is substituted, a map IN the set passes
    # untouched, and the PC is never touched by either arm.
    _pc_only = [m for m in zoneentry.VALID_MAPNOS if m not in zoneentry.PS2_VALID_MAPNOS]
    # a PC-only battle map when the default set is in force, else any id
    _t1 = next((m for m in sorted(missionlist.TYPE1_ON_DISK)
                if zoneentry.PS2_TYPE1 is not None and m not in zoneentry.PS2_TYPE1),
               next(iter(sorted(missionlist.TYPE1_ON_DISK))))
    gate_ok = (bool(_pc_only)
               and all(s3.build_mapno(m, "test") == zoneentry.MAPNO_PS2 for m in _pc_only)
               and all(s3.build_mapno(m, "test") == m for m in zoneentry.PS2_VALID_MAPNOS)
               and all(pcs.build_mapno(m, "test") == m for m in zoneentry.VALID_MAPNOS)
               and pcs.ps2_type1_refusal(_t1) is None
               and (zoneentry.PS2_TYPE1 is None or _t1 in zoneentry.PS2_TYPE1
                    or s3.ps2_type1_refusal(_t1) is not None)
               and s3.ps2_type1_refusal(None) is None
               # the console's set is a SUBSET of the PC's: an id only the
               # console had would mean the BASE table was misread
               and (zoneentry.PS2_TYPE1 is None or os.environ.get("FMO_PS2_TYPE1")
                    or (set(zoneentry.PS2_TYPE1) <= set(missionlist.TYPE1_ON_DISK)
                        and len(zoneentry.PS2_TYPE1) == 211
                        and s3.ps2_type1_refusal(zoneentry.PS2_TYPE1[0]) is None))
               and (os.environ.get("FMO_PS2_MAPNOS")
                    or set(zoneentry.PS2_VALID_MAPNOS) <= set(zoneentry.VALID_MAPNOS))
               # the zone-pack gate: every band we actually grant must PASS,
               # a zone the console has no pack for must be refused, and the
               # PC must be untouched by either arm
               and all(s3.ps2_mapkind_refusal(z) is None
                       for z in (100, 200, 207, 300, 400, 407, 509, 600))
               and s3.ps2_mapkind_refusal(950) is not None
               and pcs.ps2_mapkind_refusal(950) is None
               and s3.build_mapkind(950, "test") == zoneentry.MAPKIND_PS2
               and s3.build_mapkind(200, "test") == 200
               and pcs.build_mapkind(950, "test") == 950
               and zoneentry.MAPKIND_PS2 in zoneentry.PS2_VALID_MAPKINDS)
    print(f"  PS2 gate: {len(_pc_only)} PC-only lobby maps -> {zoneentry.MAPNO_PS2}, "
          f"{zoneentry.PS2_VALID_MAPNOS} pass, type-1 {_t1} "
          f"{'refused' if s3.ps2_type1_refusal(_t1) else 'granted'} for the "
          f"console, {len(zoneentry.PS2_VALID_MAPKINDS)} zone packs pass and a zone "
          f"without one is refused, the PC untouched  "
          f"{'OK' if gate_ok else 'FAIL'}")
    ok &= gate_ok
    # Both readings of the console's catalogue agree on 121 and 141; the
    # default must be one of those, not a value only one reading supports.
    agreed_ok = zoneentry.MAPNO_PS2 in zoneentry.PS2_VALID_MAPNOS
    print(f"  the default PS2 MapNo is one BOTH catalogue readings agree on "
          f"{'OK' if agreed_ok else 'FAIL'}")
    ok &= agreed_ok

    # The endpoint struct: port at +0x02 host-order, NUL-terminated host at
    # +0x04. Zeroed, this is what made the client dial 0.0.0.0:0.
    ep = addressing.endpoint("127.0.0.1", 61300)
    port_ok = struct.unpack_from("<H", ep, 2)[0] == 61300
    # The client prints/uses B3.B2.B1.B0 of what we write, so the stored bytes
    # must be the octets REVERSED. This asserts the exact bytes rather than a
    # round trip through our own helper, which would pass even if reversed.
    # (Measured live 2026-08-17 against a LAN redirect; the loopback address
    # pins the same reversal: 127.0.0.1 must land as 01 00 00 7f.)
    addr_ok = ep[4:8] == bytes([1, 0, 0, 127])
    print(f"  endpoint: len={len(ep)} port={struct.unpack_from('<H', ep, 2)[0]} "
          f"addr={ep[4:8].hex()} (want 0100007f, i.e. reversed) "
          f"{'OK' if port_ok and addr_ok and len(ep) == 0x14 else 'FAIL'}")
    ok &= port_ok and addr_ok and len(ep) == 0x14

    # The bug this replaced: an ASCII host would start with '1' (0x31). If that
    # ever comes back, the client dials the reversed ASCII of the first four
    # characters again (a 192.x LAN redirect came out as 46.50.57.49 live).
    print(f"  address is binary, not ASCII: "
          f"{'OK' if ep[4] != ord('1') else 'FAIL -- writing a string again'}")
    ok &= ep[4] != ord("1")

    try:
        addressing.endpoint("not.an.ip", 1)
        print("  rejects a non-IPv4 host: FAIL (it accepted one)")
        ok = False
    except ValueError:
        print("  rejects a non-IPv4 host: OK")

    # REGRESSION: every reply must ECHO the request's +0x10, never a counter of
    # ours. This is the bug that parked the login in state 2 -- and it passed
    # unnoticed for hours because our counter and the client's coincided on the
    # 0x0322. Pick a request sequence that our old counter would NOT have
    # produced, so a reintroduced counter fails here instead of by luck.
    s3 = session.Session("selftest")
    creds = struct.pack("<I", 0) * 13 + struct.pack("<I", 0x0A) + b"\x22" * 16 \
        + b"\x00" * 8
    s3.on_packet(packet.parse(packet.build(handshake.MSG_VERSION, bytes(40), 0x1007)))
    batch = s3.on_packet(packet.parse(packet.build(handshake.MSG_CREDENTIALS, creds, 0x1009)))
    seqs = [packet.parse(b)["seq"] for b in batch]
    print(f"  replies echo the request seq 0x1009: {[hex(x) for x in seqs]} "
          f"{'OK' if seqs and all(x == 0x1009 for x in seqs) else 'FAIL'}")
    ok &= bool(seqs) and all(x == 0x1009 for x in seqs)

    # The 0x12F list: exact size, stride, and that the count lands where
    # 0x61172A80 reads it. 1664/52 must be exactly 32 or the layout is wrong.
    pl = charlist.list_payload(1)
    stride_ok = charlist.LIST_ENTRY_LEN * charlist.LIST_SLOTS == charlist.LIST_REPLY_LEN - 4
    print(f"  0x12F list: {charlist.LIST_REPLY_LEN}B = u32 + {charlist.LIST_SLOTS}x{charlist.LIST_ENTRY_LEN} "
          f"{'OK' if stride_ok and len(pl) == charlist.LIST_REPLY_LEN else 'FAIL'}")
    ok &= stride_ok and len(pl) == charlist.LIST_REPLY_LEN
    print(f"  count lands at payload+0: {struct.unpack_from('<I', pl, 0)[0]} "
          f"{'OK' if struct.unpack_from('<I', pl, 0)[0] == 1 else 'FAIL'}")
    ok &= struct.unpack_from("<I", pl, 0)[0] == 1
    # The map we send must be one that EXISTS on disk. A MapNo whose file is
    # absent allocates zero bytes and aliases the script slot, which is the
    # 0x611250A2 crash -- so this is the one value with a hard right answer.
    # 0x0133 / 0x0166: the decoded blocks have to TILE, which is the whole
    # reason to believe the strides. An arithmetic slip here is invisible on
    # the wire (the reply is still the right length) and shows up as a client
    # reading one field short, so it is asserted rather than commented.
    print(f"  0x133 tiles: {inventory.INV_MAX} x {inventory.INV_ENTRY_LEN} + 8 = "
          f"{inventory.INV_MAX * inventory.INV_ENTRY_LEN + 8} (want {inventory.REPLY_0133_LEN}) "
          f"{'OK' if inventory.INV_MAX * inventory.INV_ENTRY_LEN + 8 == inventory.REPLY_0133_LEN else 'FAIL'}")
    ok &= inventory.INV_MAX * inventory.INV_ENTRY_LEN + 8 == inventory.REPLY_0133_LEN
    tile = inventory.SETUP_ITEM_OFF + inventory.SETUP_ITEMS * inventory.INV_ENTRY_LEN
    print(f"  0x166 entry tiles: 0x{inventory.SETUP_ITEM_OFF:02X} + {inventory.SETUP_ITEMS} x "
          f"{inventory.INV_ENTRY_LEN} = {tile} (want {inventory.SETUP_ENTRY_LEN}) "
          f"{'OK' if tile == inventory.SETUP_ENTRY_LEN else 'FAIL'}")
    ok &= tile == inventory.SETUP_ENTRY_LEN
    print(f"  0x166 block tiles: {inventory.SETUP_SLOTS} x {inventory.SETUP_ENTRY_LEN} = "
          f"{inventory.SETUP_SLOTS * inventory.SETUP_ENTRY_LEN} (want 0x{inventory.SETUP_TAIL_OFF:04X}) "
          f"{'OK' if inventory.SETUP_SLOTS * inventory.SETUP_ENTRY_LEN == inventory.SETUP_TAIL_OFF else 'FAIL'}")
    ok &= inventory.SETUP_SLOTS * inventory.SETUP_ENTRY_LEN == inventory.SETUP_TAIL_OFF
    # 0x6117B8A5 reads a u32 at payload+0x1100 and a byte at +0x1104. 4352
    # leaves the client reading five bytes of stale receive buffer.
    print(f"  0x166 reply covers the tail read at 0x6117B8A5: "
          f"{inventory.REPLY_0166_LEN} >= {inventory.SETUP_TAIL_OFF + 5} "
          f"{'OK' if inventory.REPLY_0166_LEN >= inventory.SETUP_TAIL_OFF + 5 else 'FAIL'}")
    ok &= inventory.REPLY_0166_LEN >= inventory.SETUP_TAIL_OFF + 5

    print(f"  MapNo {zoneentry.MAPNO} is a map that exists on disk: "
          f"{'OK' if zoneentry.MAPNO in zoneentry.VALID_MAPNOS else 'FAIL -- see VALID_MAPNOS'}")
    ok &= zoneentry.MAPNO in zoneentry.VALID_MAPNOS
    bad = [m for m in zoneentry.MAPNO_SWEEP if m not in zoneentry.VALID_MAPNOS]
    print(f"  every swept MapNo exists: "
          f"{'OK' if not bad else 'FAIL -- ' + str(bad)}")
    ok &= not bad

    # An over-long name must not run past its 17-byte field into the next one.
    # (Updated for the {id, first, last, nation} signature -- the call site was
    # left on the two-argument form when the record was decoded, which is the
    # "fix a suite the day you change what it pins" rule in run_all.py's own
    # docstring, and it had the whole fmo suite red.)
    e = charlist.list_entry(1, "X" * 40, "Y" * 40)
    print(f"  over-long name stays inside its field: "
          f"{'OK' if len(e) == charlist.LIST_ENTRY_LEN and e[0x14] == 0 else 'FAIL'}")
    ok &= len(e) == charlist.LIST_ENTRY_LEN and e[0x14] == 0

    # --- 0x0153, the join grant ------------------------------------------- #
    # The sizes are `rep movsd` counts, so they are exact and a change to any of
    # them is a bug, not a tuning decision.
    r = zoneentry.reply_0153()
    print(f"  0x153 length {len(r)} (want {zoneentry.REPLY_0153_LEN}) "
          f"{'OK' if len(r) == zoneentry.REPLY_0153_LEN else 'FAIL'}")
    ok &= len(r) == zoneentry.REPLY_0153_LEN
    # The three copies the client makes must all fit: +0x28 +252 = +0x124, and
    # +0x124 +88 = +0x17C = the whole payload, with nothing hanging off the end.
    fits = (zoneentry.R153_SETUP + zoneentry.SETUP_LEN == zoneentry.R153_INFO88
            and zoneentry.R153_INFO88 + grouplogin.INFO88_LEN == zoneentry.REPLY_0153_LEN)
    print(f"  0x153 blocks tile the payload exactly: "
          f"+{zoneentry.R153_SETUP:#x}+{zoneentry.SETUP_LEN}=+{zoneentry.R153_INFO88:#x}, "
          f"+{grouplogin.INFO88_LEN}={zoneentry.REPLY_0153_LEN} {'OK' if fits else 'FAIL'}")
    ok &= fits
    mk = struct.unpack_from("<H", r, zoneentry.R153_MAPKIND)[0]
    # 0x61005100's substitution, transcribed. The point of asserting it here is
    # the SECOND check: at nation 0 the MapKind we send survives whatever it is,
    # so a run that varies MapKind alone is measuring nothing. That is exactly
    # what the "MapKind 600 behaves like 100" negative did, and this is the
    # check that would have said so before the relaunch rather than after it.
    subs = [(100, 0, 100), (100, 1, 98), (100, 2, 99),
            (600, 1, 600), (607, 2, 607), (100, 3, 100)]
    bad = [(k, n, zoneentry.script_id_for(k, n), w) for k, n, w in subs
           if zoneentry.script_id_for(k, n) != w]
    print(f"  script id follows 0x61005100 (nation 1/2 -> 98/99, 600..607 "
          f"bypasses): {'OK' if not bad else f'FAIL {bad}'}")
    ok &= not bad

    blind = zoneentry.script_id_for(100, 0) == 100 and zoneentry.script_id_for(600, 0) == 600
    print(f"  at nation 0, MapKind 100 and 600 both pass through "
          f"UNSUBSTITUTED -- that A/B could not have told them apart: "
          f"{'OK' if blind else 'FAIL'}")
    ok &= blind

    print(f"  0x153 MapKind {mk} is inside a band the client accepts: "
          f"{'OK' if zoneentry.in_mapkind_band(mk) else 'FAIL'}")
    ok &= zoneentry.in_mapkind_band(mk)
    # Zero is the value that must NOT pass, or the band check proves nothing.
    print(f"  MapKind 0 is rejected by the same check: "
          f"{'OK' if not zoneentry.in_mapkind_band(0) else 'FAIL -- the bands are wrong'}")
    ok &= not zoneentry.in_mapkind_band(0)
    su = r[zoneentry.R153_SETUP:zoneentry.R153_SETUP + zoneentry.SETUP_LEN]
    mapno, csn, mus = struct.unpack_from("<III", su, zoneentry.SU_MAPNO)
    seno = struct.unpack_from("<I", su, zoneentry.SU_SENO)[0]
    named_ok = (mapno, csn, mus, seno) == (zoneentry.MAPNO, zoneentry.CLIENT_SCRIPT_NO, zoneentry.MUSIC_NO,
                                           zoneentry.SE_NO)
    print(f"  setup block MapNo/ClientScriptNo/MusicNo/SeNo = "
          f"{mapno}/{csn}/{mus}/{seno} {'OK' if named_ok else 'FAIL'}")
    ok &= named_ok
    # The eight unit ids are the last thing in the block that is placed by
    # offset; if SU_UNITS ever drifts past the end this catches it.
    units_ok = zoneentry.SU_UNITS + zoneentry.SU_UNIT_SLOTS * 4 <= zoneentry.SETUP_LEN
    print(f"  8 unit slots fit inside the 252-byte block: "
          f"{'OK' if units_ok else 'FAIL'}")
    ok &= units_ok

    # --- 0x016F, the Move list ------------------------------------------- #
    # Two PARALLEL arrays with a fixed gap between them, so the only thing that
    # can go wrong silently is the gap. If MOVE_SLOTS ever stopped tiling, the
    # last ids would run into the first people counts and the client would read
    # a head count as an id -- on the wire the reply is still the right length.
    tiles = (move.MOVE_IDS_OFF + 4 * move.MOVE_SLOTS == move.MOVE_PEOPLE_OFF
             and move.MOVE_PEOPLE_OFF + move.MOVE_SLOTS == move.REPLY_016F_LEN)
    print(f"  0x16F arrays tile: +{move.MOVE_IDS_OFF:#x} + 4x{move.MOVE_SLOTS} = "
          f"+{move.MOVE_PEOPLE_OFF:#x}, +{move.MOVE_SLOTS} = {move.REPLY_016F_LEN} "
          f"{'OK' if tiles else 'FAIL'}")
    ok &= tiles
    mv = move.reply_016f([(7, 3), (9, 255)])
    mv_ok = (len(mv) == move.REPLY_016F_LEN
             and struct.unpack_from("<i", mv, 0)[0] == 2
             and struct.unpack_from("<I", mv, move.MOVE_IDS_OFF)[0] == 7
             and struct.unpack_from("<I", mv, move.MOVE_IDS_OFF + 4)[0] == 9
             and mv[move.MOVE_PEOPLE_OFF] == 3 and mv[move.MOVE_PEOPLE_OFF + 1] == 255)
    print(f"  0x16F count/ids/people land where 0x61190070 reads them "
          f"(+0x00, +{move.MOVE_IDS_OFF:#x} stride 4, +{move.MOVE_PEOPLE_OFF:#x} stride 1): "
          f"{'OK' if mv_ok else 'FAIL'}")
    ok &= mv_ok
    # The empty list is a SCREEN, not a failure, so it has to be exactly zero at
    # +0x00 and still full length -- 0x61190070 never consults a length.
    mv0 = move.reply_016f([])
    mv0_ok = len(mv0) == move.REPLY_016F_LEN and struct.unpack_from("<i", mv0, 0)[0] == 0
    print(f"  0x16F count 0 is still {move.REPLY_016F_LEN}B "
          f"(the 'Move there anyway?' case): {'OK' if mv0_ok else 'FAIL'}")
    ok &= mv0_ok

    # --- 0x016E category -> row set (2026-08-26) --------------------------- #
    # Category 2 (Briefing Room) must get ITS OWN knob and never the lobby set;
    # 0, 1 and an unknown category fall through to FMO_MOVE_LIST. The source
    # string is what the log prints, so it has to name the knob.
    b_rows, b_src = move.move_list_for(move.MOVE_CATEGORY_BRIEFING)
    l_rows, l_src = move.move_list_for(0)
    r_rows, r_src = move.move_list_for(1)
    u_rows, u_src = move.move_list_for(None)
    cat_ok = (b_rows == move.parse_move_list(move.MOVE_LIST_BRIEFING)
              and b_src.startswith("FMO_MOVE_LIST_BRIEFING")
              and l_rows == u_rows == move.parse_move_list(move.MOVE_LIST)
              and l_src.startswith("FMO_MOVE_LIST ")
              and u_src.startswith("FMO_MOVE_LIST ")
              and ((r_rows == move.parse_move_list(move.MOVE_LIST_ROOM)
                    and r_src.startswith("FMO_MOVE_LIST_ROOM")) if move.MOVE_LIST_ROOM
                   else (r_rows == l_rows and r_src.startswith("FMO_MOVE_LIST "))))
    # and the room knob, exercised regardless of the environment
    _sv = flat_globals()["MOVE_LIST_ROOM"]
    try:
        flat_globals()["MOVE_LIST_ROOM"] = ["122:1", "141:2"]
        _rr, _rs = move.move_list_for(1)
        cat_ok &= _rr == move.parse_move_list(["122:1", "141:2"]) and _rs.startswith("FMO_MOVE_LIST_ROOM")
        cat_ok &= move.move_list_for(0)[0] == move.parse_move_list(move.MOVE_LIST)
        flat_globals()["MOVE_LIST_ROOM"] = []
        cat_ok &= move.move_list_for(1)[0] == move.parse_move_list(move.MOVE_LIST)
    finally:
        flat_globals()["MOVE_LIST_ROOM"] = _sv
    print(f"  0x16E category 2 = FMO_MOVE_LIST_BRIEFING, 1 = FMO_MOVE_LIST_ROOM when set "
          f"(else the default), 0/other = FMO_MOVE_LIST, sources named: "
          f"{'OK' if cat_ok else 'FAIL'}")
    ok &= cat_ok
    # PLACES (2026-09-09): ids, rows from population, the pick, the map per kind
    _pop = {(200, 0, 1): 2, (200, 0, 2): 1, (200, 1, 1): 1, (400, 0, 1): 3, (509, 2, 1): 1}
    _pl_ok = (move.place_id(3, 7) == 3007 and move.parse_place_id(3007) == (3, 7)
              and move.parse_place_id(7) == (0, 7) and move.parse_place_id(9001) is None
              and move.parse_place_id(0) is None)
    _pl_ok &= move.place_rows(200, 0, _pop) == [(1, 2), (2, 1), (3, 0)]
    _pl_ok &= move.place_rows(200, 1, _pop) == [(1001, 1), (1002, 0), (3001, 0), (4001, 0)]
    _pl_ok &= move.place_rows(200, 2, _pop) == [] and move.place_rows(509, 2, _pop) == [(2001, 1), (2002, 0)]
    _pl_ok &= move.place_rows(300, 0, _pop) == [(1, 0)]
    _full = {(200, 0, 1): move.PLACE_CAP}
    _pl_ok &= move.place_rows(200, 0, _full) == [(1, move.PLACE_CAP), (2, 0)]
    _pl_ok &= move.resolve_place_pick(200, 1, 3001)[0] == (200, 3, 1)
    _pl_ok &= move.resolve_place_pick(200, 0, 2)[0] == (200, 0, 2)
    _pl_ok &= move.resolve_place_pick(200, 0, 1001)[0] is None       # a room from the lobby list
    _pl_ok &= move.resolve_place_pick(200, 2, 2001)[0] is None       # no strategy room in 200
    _pl_ok &= move.resolve_place_pick(509, 2, 2001)[0] == (509, 2, 1)
    _pl_ok &= move.resolve_place_pick(200, 1, 141)[0] is None        # an old MapNo id is not a place
    _pl_ok &= move.place_map(200, 1)[0] == move.ROOM_MAPS[1] and move.place_map(509, 2)[0] == move.ROOM_MAPS[2]
    _pl_ok &= move.place_map(200, 0, 102)[0] in zoneentry.VALID_MAPNOS
    try:
        move.parse_room_maps("1:999"); _pl_ok = False
    except SystemExit:
        pass
    print(f"  PLACES: ids, rows from population (+1 empty always), pick resolution, "
          f"maps per kind, briefing only in {move.BRIEFING_ZONES}: {'OK' if _pl_ok else 'FAIL'}")
    ok &= _pl_ok
    # the relay room is the PLACE: same zone and map, different lobby instance -> not mates
    class _Ch2:
        def __init__(self, addr):
            self.addr, self.key, self.left, self.seen_at = addr, b"k", None, time.time()
            self.remotes = {}
    _pa, _pb, _pc = ("selftest-pa", 1), ("selftest-pb", 1), ("selftest-pc", 1)
    _saved = (dict(groupchannel.WORLD_PEERS), dict(rooms.WORLD_MAPS), dict(rooms.WORLD_ZONES), dict(move.WORLD_PLACES), room.ROOM)
    try:
        groupchannel.WORLD_PEERS.clear()
        for _a in (_pa, _pb, _pc):
            groupchannel.WORLD_PEERS[_a] = _Ch2(_a); rooms.WORLD_MAPS[_a[0]] = 102; rooms.WORLD_ZONES[_a[0]] = 200
        move.WORLD_PLACES[_pa[0]], move.WORLD_PLACES[_pb[0]], move.WORLD_PLACES[_pc[0]] = (200, 0, 1), (200, 0, 2), (200, 0, 1)
        flat_globals()["ROOM"] = True
        _mates = [c.addr[0] for c in rooms.room_mates(groupchannel.WORLD_PEERS[_pa])]
        _pr_ok = (_mates == [_pc[0]]) if move.PLACES else (sorted(_mates) == sorted([_pb[0], _pc[0]]))
        _live = move.place_population()
        _pr_ok &= _live == {(200, 0, 1): 2, (200, 0, 2): 1}
        _pr_ok &= move.place_rows(200, 0) == [(1, 2), (2, 1), (3, 0)]
        _pr_ok &= npcroster.roster_for(_pa[0])[0] is not None
        # a Room has a per-zone cast since 2026-10-06 (roomcast.py); Room B
        # (kind 3) has no band and still pops nobody
        move.WORLD_PLACES[_pa[0]] = (200, 3, 1)
        _pr_ok &= npcroster.roster_for(_pa[0])[0] == [] and "not a lobby" in npcroster.roster_for(_pa[0])[2]
    finally:
        groupchannel.WORLD_PEERS.clear(); groupchannel.WORLD_PEERS.update(_saved[0])
        rooms.WORLD_MAPS.clear(); rooms.WORLD_MAPS.update(_saved[1])
        rooms.WORLD_ZONES.clear(); rooms.WORLD_ZONES.update(_saved[2])
        move.WORLD_PLACES.clear(); move.WORLD_PLACES.update(_saved[3])
        flat_globals()["ROOM"] = _saved[4]
    print(f"  PLACES: relay room = the place (lobby #1 and #2 do not mix), population "
          f"counts live channels, rooms pop no cast: {'OK' if _pr_ok else 'FAIL ' + str(_mates)}")
    ok &= _pr_ok
    # An empty briefing list is a legitimate count-0 reply, full length.
    b_reply = move.reply_016f(b_rows)
    b_ok = (len(b_reply) == move.REPLY_016F_LEN
            and struct.unpack_from("<i", b_reply, 0)[0] == len(b_rows))
    print(f"  category-2 reply carries count={len(b_rows)} in "
          f"{move.REPLY_016F_LEN}B (0 = SE's own 'nobody in that briefing room' "
          f"box): {'OK' if b_ok else 'FAIL'}")
    ok &= b_ok
    # Every briefing row id must be a MapNo on disk, or the grant refuses it.
    b_disk = all(i in zoneentry.VALID_MAPNOS for i, _ in b_rows)
    b_disk_msg = ("OK" if b_disk else
                  "FAIL -- a served id off disk is refused at grant time; "
                  "fix the knob")
    print(f"  FMO_MOVE_LIST_BRIEFING ids {[i for i, _ in b_rows]} are all on "
          f"disk (VALID_MAPNOS): {b_disk_msg}")
    ok &= b_disk
    # The zone kind: only category 2 is overridden, -1 reverts, and the value
    # lands at 0x0153 +0x18 exactly where 0x61006350 lifts globals+0x1AC from.
    k_ok = (move.move_kind_for(0) is None and move.move_kind_for(1) is None
            and move.move_kind_for(None) is None
            and (move.move_kind_for(move.MOVE_CATEGORY_BRIEFING) ==
                 (move.MOVE_KIND_BRIEFING if move.MOVE_KIND_BRIEFING >= 0 else None))
            and (move.MOVE_KIND_BRIEFING == -1 or 0 <= move.MOVE_KIND_BRIEFING <= 5))
    r_k = zoneentry.reply_0153(fill=True, field18=2)
    r_d = zoneentry.reply_0153(fill=True)
    f18_ok = (struct.unpack_from("<I", r_k, zoneentry.R153_FIELD_18)[0] == 2
              and struct.unpack_from("<I", r_d, zoneentry.R153_FIELD_18)[0] == zoneentry.FIELD_18
              and r_k[:zoneentry.R153_FIELD_18] == r_d[:zoneentry.R153_FIELD_18]
              and r_k[zoneentry.R153_FIELD_18 + 4:] == r_d[zoneentry.R153_FIELD_18 + 4:])
    print(f"  move_kind_for: only category 2 overrides (-> "
          f"{move.move_kind_for(move.MOVE_CATEGORY_BRIEFING)}), FMO_MOVE_KIND_BRIEFING="
          f"{move.MOVE_KIND_BRIEFING} is -1 or a client kind 0..5: "
          f"{'OK' if k_ok else 'FAIL'}")
    ok &= k_ok
    print(f"  reply_0153(field18=2) changes ONLY +0x18 (globals+0x1AC, the "
          f"zone kind; 2 = 'Briefing Room' at 0x611D8F83): "
          f"{'OK' if f18_ok else 'FAIL'}")
    ok &= f18_ok

    # --- 0x016D, the move request ----------------------------------------- #
    # Built the way the client builds it (0x61190A67..0x61190AA0) and read back,
    # so a drift in either constant shows up here rather than as a name that
    # decodes one byte short.
    req = bytearray(move.MOVE_REQ_LEN)
    struct.pack_into("<II", req, move.M16D_FIELD_00, 42, 7)
    req[move.M16D_NUMBER:move.M16D_NUMBER + 3] = b"101"
    req[move.M16D_NAME_A:move.M16D_NAME_A + 4] = b"Lex\x00"
    req[move.M16D_NAME_B:move.M16D_NAME_B + 6] = b"Arden\x00"
    d = move.describe_move_req(bytes(req))
    d_ok = "+0x00=42" in d and "+0x04=7" in d and "'101'" in d \
        and "'Lex'" in d and "'Arden'" in d
    print(f"  0x16D decodes its own layout back: {'OK' if d_ok else 'FAIL -- ' + d}")
    ok &= d_ok
    # The two 17-byte name fields must not overlap, and the record must end
    # inside the 72 bytes the client's builder declares.
    names_ok = (move.M16D_NAME_A + move.MOVE_NAME_LEN == move.M16D_NAME_B
                and move.M16D_NAME_B + move.MOVE_NAME_LEN <= move.MOVE_REQ_LEN)
    print(f"  0x16D name fields abut and fit: +{move.M16D_NAME_A:#x}+{move.MOVE_NAME_LEN}"
          f"=+{move.M16D_NAME_B:#x}, end {move.M16D_NAME_B + move.MOVE_NAME_LEN} <= "
          f"{move.MOVE_REQ_LEN} {'OK' if names_ok else 'FAIL'}")
    ok &= names_ok
    # A map that is not on disk allocates zero bytes and aliases the script
    # slot -- the 0x611250A2 crash. FMO_MAPNO is checked for that above; the
    # move's own MapNo is a second way to send one, so it gets the same check.
    mm_ok = move.MOVE_MAPNO is None or move.MOVE_MAPNO in zoneentry.VALID_MAPNOS
    print(f"  FMO_MOVE_MAPNO ({move.MOVE_MAPNO}) is a map that exists on disk: "
          f"{'OK' if mm_ok else 'FAIL -- see VALID_MAPNOS'}")
    ok &= mm_ok
    # A short request must be REFUSED rather than decoded out of stale bytes.
    short_ok = "NOT decoding" in move.describe_move_req(bytes(move.MOVE_REQ_LEN - 1))
    print(f"  a short 0x16D is refused, not decoded: "
          f"{'OK' if short_ok else 'FAIL'}")
    ok &= short_ok

    # --- cmd 7, the POP -------------------------------------------------- #
    # The body length is the client's, cross-checked two ways: the create arm
    # copies 0x72 dwords from +0, the update arm 0x5C dwords from +0x58, and
    # both must land on the same end.
    pop_len_ok = (0x72 * 4 == fmoworld.POP_BODY_LEN
                  and 0x58 + 0x5C * 4 == fmoworld.POP_BODY_LEN)
    print(f"  cmd 7 body 0x{fmoworld.POP_BODY_LEN:X}: both copy arms end there "
          f"(0x72*4 and 0x58+0x5C*4): {'OK' if pop_len_ok else 'FAIL'}")
    ok &= pop_len_ok

    pr = fmoworld.record_pop(0x82080C00, unit_type=0, name1="AA", name2="BB")
    psize, pcmd, _, pflt = struct.unpack_from("<IIII", pr, 0)
    pop_rec_ok = (psize == len(pr) and psize % 4 == 0
                  and psize < fmoworld.MAX_RECORD and not (pflt & 0xFFFF)
                  and pcmd == fmoworld.CMD_POP
                  and len(pr) - fmoworld.REC_HDR == fmoworld.POP_BODY_LEN)
    print(f"  cmd 7 record {len(pr)}B passes the client's record validator "
          f"(size%4, <0x800, +0x0C low word 0): "
          f"{'OK' if pop_rec_ok else 'FAIL'}")
    ok &= pop_rec_ok

    pbody = pr[fmoworld.REC_HDR:]
    fields_ok = (struct.unpack_from("<I", pbody, fmoworld.POP_UNITID)[0]
                 == 0x82080C00
                 and pbody[fmoworld.POP_UNITTYPE] == 0
                 and pbody[fmoworld.POP_NAME1:fmoworld.POP_NAME1 + 2] == b"AA"
                 and pbody[fmoworld.POP_NAME2:fmoworld.POP_NAME2 + 2] == b"BB")
    print(f"  cmd 7 fields land where the client reads them "
          f"(UnitID +0x04, UnitType +0x89, names +0x58/+0x69): "
          f"{'OK' if fields_ok else 'FAIL'}")
    ok &= fields_ok

    # WARNING: THE GUARDS ARE THE POINT. UnitType 7..29 reaches 0x611EB3F0, which
    # prints "no UnitType=%u UnitID=%x" and creates NOTHING -- a probe that
    # sends one would look exactly like a probe the client ignored.
    refused = 0
    for bad in (7, 15, 29, 31, 255):
        try:
            fmoworld.record_pop(1, unit_type=bad)
        except ValueError:
            refused += 1
    for bad_kind in (3, 4, 5):
        try:
            fmoworld.record_pop(1, kind=bad_kind)
        except ValueError:
            refused += 1
    try:
        fmoworld.record_pop(0)
    except ValueError:
        refused += 1
    print(f"  cmd 7 refuses every value that cannot create "
          f"(UnitType 7-29/31+, kind 3-5, UnitID 0): "
          f"{'OK' if refused == 9 else 'FAIL'} ({refused}/9)")
    ok &= refused == 9

    # Every legal UnitType must still build.
    built = sum(1 for u in fmoworld.POP_UNITTYPES_OK
                if fmoworld.record_pop(1, unit_type=u))
    print(f"  cmd 7 accepts all {len(fmoworld.POP_UNITTYPES_OK)} legal "
          f"UnitTypes {fmoworld.POP_UNITTYPES_OK}: "
          f"{'OK' if built == len(fmoworld.POP_UNITTYPES_OK) else 'FAIL'}")
    ok &= built == len(fmoworld.POP_UNITTYPES_OK)

    # WARNING:KEY: THE BATTLE "DESTROYED" MAPPING (0x611F25D0 reads char+0x1C in {2,3}).
    # client_kind 3 is what scene-4 entry needs AND what marks the self destroyed;
    # the un-poison follow-up must carry an ALIVE kind (0 or 1).
    _cs = fmoworld.POP_CLIENT_KIND_TO_CHARSTATUS
    map_ok = (_cs == {0: 1, 1: 0, 2: 2, 3: 3}
              and fmoworld.client_kind_is_destroyed(3)
              and fmoworld.client_kind_is_destroyed(2)
              and not fmoworld.client_kind_is_destroyed(0)
              and not fmoworld.client_kind_is_destroyed(1))
    print(f"  cmd 7 client_kind -> char status {_cs}; destroyed set "
          f"{sorted(fmoworld.POP_CHARSTATUS_DESTROYED)} (2/3 destroyed, 0/1 "
          f"alive): {'OK' if map_ok else 'FAIL'}")
    ok &= map_ok
    # The un-poison knob, when armed, only ever carries an alive kind, and its
    # two records (cmd 8 depop to set char+0x20, then cmd 7 re-POP to recreate
    # the char alive) both build. char+0x20==0 would take the skip arm, so the
    # depop MUST precede the re-POP; a bare re-POP is a no-op on the status.
    undestroy_ok = (popself.UNDESTROY_KIND is None
                    or not fmoworld.client_kind_is_destroyed(popself.UNDESTROY_KIND))
    if popself.UNDESTROY_KIND is not None:
        _d = fmoworld.record_depop(1, from_id=0, status=popself.UNDESTROY_DEPOP_STATUS)
        _p = fmoworld.record_pop(1, client_kind=popself.UNDESTROY_KIND)
        # cmd is the second u32 of the 0x10-byte header (record() packs
        # <IIII> size, cmd, arg8, flt).
        _dcmd = struct.unpack_from("<I", _d, 4)[0]
        undestroy_ok &= (_dcmd == fmoworld.CMD_DEPOP
                         and popself.UNDESTROY_DEPOP_STATUS in (0, 1, 2)
                         and _p[fmoworld.REC_HDR + fmoworld.POP_CLIENT_KIND]
                         == popself.UNDESTROY_KIND)
    print(f"  FMO_UDP_POP_UNDESTROY={popself.UNDESTROY_KIND!r} is off, or an alive kind "
          f"whose depop(status {popself.UNDESTROY_DEPOP_STATUS})+re-POP pair builds: "
          f"{'OK' if undestroy_ok else 'FAIL'}")
    ok &= undestroy_ok

    # WARNING: THE PAIRING INVARIANT. 0x61003120 only spawns an id that is in BOTH the
    # setup block and the entity map, so a POP whose id is not in the block is a
    # silent no-op -- the failure mode that would waste a whole live run.
    pair_ok = (not popsweep.POP) or (popsweep.POP[0] in room.SETUP_UNIT_IDS)
    print(f"  cmd 7 POP id is also in the 0x153 setup block "
          f"(0x61003120 needs BOTH): "
          f"{'OK' if pair_ok else 'FAIL'}"
          f"{'' if popsweep.POP else '  -- probe off, vacuous'}")
    ok &= pair_ok

    # And with the probe off, the block must be byte-identical to every
    # measurement taken before it existed.
    zero_ok = popsweep.POP or set(struct.unpack_from("<8I", zoneentry.setup_block(),
                                                     zoneentry.SU_UNITS)) == {0}
    print(f"  with FMO_UDP_POP unset the eight ids are all zero: "
          f"{'OK' if zero_ok else 'FAIL'}")
    ok &= bool(zero_ok)
    # The endpoint must be the same binary, byte-reversed form as the 0x0322's.
    print(f"  0x153 endpoint matches the configured order: "
          f"{'OK' if r[:addressing.ENDPOINT_LEN] == (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)(addressing.BATTLE_HOST, addressing.BATTLE_PORT) else 'FAIL'}")
    ok &= r[:addressing.ENDPOINT_LEN] == (addressing.endpoint_net if addressing.EP_0153_NET else addressing.endpoint)(
        addressing.BATTLE_HOST, addressing.BATTLE_PORT)
    # The bug this switch exists for, stated as arithmetic: the little-endian
    # form of THIS host/port is what pol-shim saw leaving as 71.3.168.192:29935.
    en = addressing.endpoint_net(addressing.BATTLE_HOST, addressing.BATTLE_PORT)
    el = addressing.endpoint(addressing.BATTLE_HOST, addressing.BATTLE_PORT)
    print(f"  0x153 endpoint order: net={en[2:8].hex()} le={el[2:8].hex()} "
          f"-- serving {'NETWORK (0x611FED70 copies it verbatim)' if addressing.EP_0153_NET else 'little-endian (the old, broken-for-UDP form)'}")
    print(f"  the two orders differ (a swap is actually happening): "
          f"{'OK' if en != el else 'FAIL'}")
    ok &= en != el
    # FMO_0153_FILL=0 is the control, so it must really produce zeros.
    zeros = zoneentry.reply_0153(fill=False)
    print(f"  FMO_0153_FILL=0 gives {len(zeros)}B of zero: "
          f"{'OK' if zeros == bytes(zoneentry.REPLY_0153_LEN) else 'FAIL'}")
    ok &= zeros == bytes(zoneentry.REPLY_0153_LEN)

    # --- 0x0155, the LoginGroup list --------------------------------------- #
    g = grouplogin.reply_0155(2)
    print(f"  0x155 length {len(g)} (want {grouplogin.REPLY_0155_LEN}) "
          f"{'OK' if len(g) == grouplogin.REPLY_0155_LEN else 'FAIL'}")
    ok &= len(g) == grouplogin.REPLY_0155_LEN
    # 1920 / 120 = 16 exactly. If that stops being exact the stride is wrong.
    tile = (grouplogin.GROUP_ENTRY_LEN * grouplogin.GROUP_SLOTS == grouplogin.REPLY_0155_LEN - grouplogin.GROUP_OFF
            and grouplogin.GRP_INFO88 + grouplogin.INFO88_LEN == grouplogin.GROUP_ENTRY_LEN
            and grouplogin.GRP_ENDPOINT + addressing.ENDPOINT_LEN == grouplogin.GRP_INFO88)
    print(f"  0x155 entry accounted for exactly: {grouplogin.GROUP_SLOTS}x"
          f"{grouplogin.GROUP_ENTRY_LEN} = {grouplogin.REPLY_0155_LEN - grouplogin.GROUP_OFF}, and "
          f"{grouplogin.GRP_ENDPOINT:#x}+{addressing.ENDPOINT_LEN}={grouplogin.GRP_INFO88:#x}+{grouplogin.INFO88_LEN}="
          f"{grouplogin.GROUP_ENTRY_LEN} {'OK' if tile else 'FAIL'}")
    ok &= tile
    print(f"  0x155 count byte at +0: {g[0]} "
          f"{'OK' if g[0] == 2 else 'FAIL'}")
    ok &= g[0] == 2
    e0 = g[grouplogin.GROUP_OFF:grouplogin.GROUP_OFF + grouplogin.GROUP_ENTRY_LEN]
    gate_ok = e0[grouplogin.GRP_GATE] != 0
    print(f"  entry gate byte +0x05 is non-zero (0 makes the loop skip it): "
          f"{'OK' if gate_ok else 'FAIL'}")
    ok &= gate_ok
    # 0x61177694 refuses the group unless the endpoint's address is non-zero.
    addr_ok = e0[grouplogin.GRP_ENDPOINT + 4:grouplogin.GRP_ENDPOINT + 8] != b"\x00\x00\x00\x00"
    print(f"  entry endpoint address is non-zero (0x61177694 rejects zero): "
          f"{'OK' if addr_ok else 'FAIL'}")
    ok &= addr_ok
    # An empty list must be genuinely empty -- that is the measured-good state.
    print(f"  count 0 gives an all-zero 0x155: "
          f"{'OK' if grouplogin.reply_0155(0) == bytes(grouplogin.REPLY_0155_LEN) else 'FAIL'}")
    ok &= grouplogin.reply_0155(0) == bytes(grouplogin.REPLY_0155_LEN)
    # More entries than slots must clamp rather than overrun the payload.
    print(f"  count {grouplogin.GROUP_SLOTS + 8} clamps to {grouplogin.GROUP_SLOTS}: "
          f"{'OK' if grouplogin.reply_0155(grouplogin.GROUP_SLOTS + 8)[0] == grouplogin.GROUP_SLOTS else 'FAIL'}")
    ok &= grouplogin.reply_0155(grouplogin.GROUP_SLOTS + 8)[0] == grouplogin.GROUP_SLOTS


    # --- the UDP world channel ------------------------------------------- #
    # WARNING: THE COUPLING THAT WILL BITE: the world channel's Blowfish key is
    # sprintf("%xlobby", port + addr + char_id) over THE ENDPOINT WE PUT IN THE
    # 0x0153 REPLY. Two places therefore have to agree about those 20 bytes --
    # reply_0153() and WorldChannel.candidates() -- and if they ever disagree
    # the client's datagrams simply stop verifying, with no error anywhere that
    # names the cause. So assert the key is derived from the bytes actually
    # SERVED, by reading them back out of the reply.
    if fmoworld:
        served_ep = zoneentry.reply_0153(fill=True)[zoneentry.R153_ENDPOINT:
                                                    zoneentry.R153_ENDPOINT + addressing.ENDPOINT_LEN]
        from_reply = fmoworld.key_for_endpoint(served_ep, 1)
        # Scene 4's manager keys the SAME sum with suffix "battle" (the
        # 2026-09-04 timeout: every scene-4 datagram, no lobby key verified),
        # so candidates() must offer both twins per character id.
        battle_twin = fmoworld.key_for_endpoint(served_ep, 1, "battle")
        cands = worldchannel.WorldChannel(("selftest", 0)).candidates()
        offered_keys = {k for _cid, k in cands}
        offered = dict(cands)
        agree = from_reply in offered_keys and battle_twin in offered_keys
        print(f"  udp key is derived from the endpoint the 0x153 SERVES, and "
              f"its scene-4 battle twin is offered beside it "
              f"({from_reply.decode()} / {battle_twin.decode()}): "
              f"{'OK' if agree else 'FAIL'}")
        ok &= agree
        # And the character id has to be one we actually offered, or the key is
        # unfindable however correct the cipher is.
        covers = set(offered) >= {1} if charlist.LIST_COUNT else True
        print(f"  udp key candidates cover the served character ids "
              f"{sorted(offered)}: {'OK' if covers else 'FAIL'}")
        ok &= covers
        # A reply we build must satisfy every rule the client enforces --
        # fmoworld.parse() is written to accept exactly what 0x61070820 accepts.
        tables = fmoworld.bf_init(from_reply)
        dg = fmoworld.build(*tables, peer=1, hid=udpconfig.UDP_HID, kind=2, ack=9)
        back = fmoworld.parse(*tables, dg)
        good = back is not None and back["ack"] == 9 and back["hid"] == udpconfig.UDP_HID \
            and back["to"] == back["from"] and len(dg) % 8 == 0
        print(f"  udp pure-ACK reply passes the client's own validator: "
              f"{'OK' if good else 'FAIL'}")
        ok &= good

        # WARNING: THE CHECK THAT CAUGHT THE REAL BUG. Parsing is not delivery: the
        # client also runs a sliding-window test (0x61070922) against state it
        # adopts from our FIRST datagram, and a reply that fails it is dropped
        # in SILENCE. Replay a climbing exchange through fmoworld.PeerWindow --
        # which is that test -- using the responder's real index policy.
        def replay(policy):
            win = fmoworld.PeerWindow()
            tx = rx = delivered = 0
            # Start at 2, as the real client does -- from 0 the buggy policy
            # coincides with the right one for two rounds and the check reads
            # as half-broken rather than broken.
            for their_to in range(2, 14):
                frm = rx if policy == "buggy" else tx
                rep = fmoworld.build(*tables, peer=1, hid=udpconfig.UDP_HID, kind=2,
                                     ack=their_to, flag=0, frm=frm, to=frm)
                if win.accept(fmoworld.parse(*tables, rep)):
                    delivered += 1
                rx = their_to
            return delivered

        # WARNING: AND THE CASE THAT ACTUALLY BROKE IT LIVE: we send a record and the
        # peer accepts the datagram but does NOT retire the record (its +0x20
        # never moves). A sender that advances its index on SEND is then outside
        # the window for ever; one that keeps FROM at the last acknowledged
        # index and RESENDS is not. That is the whole difference.
        def replay_record(advance_on_send):
            win = fmoworld.PeerWindow()
            base, pending, delivered = 0, [fmoworld.record_chat("x", "S")], 0
            for their_to in range(2, 14):
                rep = fmoworld.build(*tables, peer=1, hid=udpconfig.UDP_HID, kind=2,
                                     ack=their_to, flag=0 if base == 0 else 2,
                                     frm=base, to=base + len(pending),
                                     body=b"".join(pending))
                if win.accept(fmoworld.parse(*tables, rep), consume=False):
                    delivered += 1
                if advance_on_send:            # the bug
                    base += len(pending)
                    pending = []
            return delivered

        keep, advance = replay_record(False), replay_record(True)
        print(f"  udp unacked record is RESENT and stays in window: {keep}/12 "
              f"{'OK' if keep == 12 else 'FAIL'}")
        ok &= keep == 12
        print(f"  udp advancing on SEND still breaks it ({advance}/12): "
              f"{'OK' if advance <= 1 else 'FAIL'}")
        ok &= advance <= 1

        live, buggy = replay("live"), replay("buggy")
        print(f"  udp every reply survives the client's WINDOW: {live}/12 "
              f"{'OK' if live == 12 else 'FAIL'}")
        ok &= live == 12
        # And prove the check can fail, or it is decoration: the pre-fix policy
        # (our index taken from THEIR traffic) must be rejected after the first.
        print(f"  udp window check still catches the old index bug "
              f"({buggy}/12 delivered): {'OK' if buggy <= 1 else 'FAIL'}")
        ok &= buggy <= 1

        # ------------------------------------------------------------------ #
        # WARNING: THE ROOM, END TO END, THROUGH THE REAL _serve_datagram.
        #
        # Two fake clients on two addresses, driven through the same function
        # the socket calls, with sendto captured. This is the only check that
        # exercises the ORDERING the relay depends on -- POP on the self stream
        # first, movement on the alias stream afterwards -- and ordering is
        # exactly what a unit test of the pieces would miss.
        #
        # WARNING: It proves what we SEND, not what the client does with it. The
        # client-side half (0x610012B0 being a stub, so an un-POPped alias is
        # dropped) is read out of the binary and is the reason for the order;
        # only two machines can confirm it.
        sent = []

        class _FakeSock:
            def sendto(self, data, to):
                sent.append((to, data))

        saved = (dict(groupchannel.WORLD_PEERS), dict(rooms.WORLD_MAPS))
        groupchannel.WORLD_PEERS.clear()
        rooms.WORLD_MAPS.clear()
        try:
            sock, A, B = _FakeSock(), ("192.0.2.1", 19155), ("192.0.2.2", 19155)
            rooms.WORLD_MAPS[A[0]] = rooms.WORLD_MAPS[B[0]] = 102

            def client_dg(frm, to, ack, body=b"", peer=1):
                return fmoworld.build(*tables, peer=peer, hid=udpconfig.UDP_HID, kind=2,
                                      ack=ack, flag=2, frm=frm, to=to,
                                      body=body)

            def pump(addr, frm, to, ack, body=b"", peer=1):
                del sent[:]
                datagram._serve_datagram(sock, groupchannel.WORLD_PEERS,
                                         client_dg(frm, to, ack, body, peer), addr)
                return [fmoworld.parse(*tables, d) for _t, d in sent]

            # A alone: nobody to relay to, so exactly one datagram (its own).
            pump(A, 0, 1, 0)
            alone = len(sent) == 1
            print(f"  room: one client is served exactly as before "
                  f"({len(sent)} datagram): {'OK' if alone else 'FAIL'}")
            ok &= alone

            # WARNING: Both channels must have had their OWN unit popped before the
            # room will introduce anybody -- room_queue returns early until
            # then, because the scene-setup wait is on the local player's own
            # unit. The live sequence gets there after FMO_UDP_POP_AFTER
            # exchanges; here, say so directly rather than pumping blind.
            groupchannel.WORLD_PEERS[A].popped = True

            # B arrives and walks to (1.93, 0, -1.12) -- a real captured record.
            walk = fmoworld.record_move((1.93, 0.0, -1.12), 2.549, flags=6)
            pump(B, 0, 1, 0, walk)
            groupchannel.WORLD_PEERS[B].popped = True
            # Give B a model that is NOT the default, so the relay assertion
            # below can actually fail if someone reverts it to POP_SEX.
            groupchannel.WORLD_PEERS[B].type4_model = 1
            groupchannel.WORLD_PEERS[B].type4_look = {"size": 3, "build": 4, "face": 107,
                                                      "uniform": 201}
            moved = groupchannel.WORLD_PEERS[B].pos == (1.93, 0.0, -1.12)
            print(f"  room: cmd 240 gives the server the walker's position "
                  f"{groupchannel.WORLD_PEERS[B].pos}: {'OK' if moved else 'FAIL'}")
            ok &= moved

            # A's next datagram must carry the POP for B -- on A's OWN stream,
            # because that is the only peer A's client has.
            outs = pump(A, 1, 2, 0)
            # WARNING: THE ORDERING INVARIANT, checked on the exchange it matters on:
            # nothing may be addressed to the alias in the very datagram round
            # that introduces it. 0x610012B0 -- the factory that would create a
            # peer on demand -- is `xor eax,eax / ret 4`, so a datagram naming
            # a peer the client has not yet built is dropped without a reply, a
            # log line or a disconnect. An early alias datagram would spend its
            # one chance at index adoption on nothing.
            introduced_alone = not [o for o in outs if o and o["peer"] != 1]
            self_recs = [r for o in outs if o and o["peer"] == 1
                         for r in o["records"]]
            pops = [r for r in self_recs if r[2] == fmoworld.CMD_POP]
            alias = groupchannel.WORLD_PEERS[A].alias_of.get(B)
            popped_id = (struct.unpack_from("<I", pops[0][3],
                                            fmoworld.POP_UNITID)[0]
                         if pops else None)
            good = (len(pops) == 1 and popped_id == alias
                    and alias not in (None, groupchannel.WORLD_PEERS[A].self_unit()))
            # KEY: And it must wear the ORIGINATOR's model, not this viewer's and
            # not the fixed knob. Before 2026-08-26 the relay passed POP_SEX
            # while the self POP stepped a sweep, so with FMO_UDP_POP_SEX_SWEEP
            # armed every scene chose independently and two players each saw
            # themselves as one model and the other as the other (measured live).
            _want = getattr(groupchannel.WORLD_PEERS[B], "type4_model", popself.POP_SEX)
            _got = (pops[0][3][fmoworld.POP_TYPE4_MODEL] if pops else None)
            _model_ok = (_want is None or _got == _want)
            print(f"  room: the relayed POP carries the ORIGINATOR's "
                  f"body+0x7A ({_want!r}, sent {_got!r}): "
                  f"{'OK' if _model_ok else 'FAIL -- appearance will disagree'}")
            ok &= _model_ok

            # ...and the other four fields with it. A relay that dresses a
            # player differently from the way they see themselves is the same
            # bug as the sex byte was, one offset over.
            # WARNING: THE DEFERRED LOOK, on the relay. The JOIN record must be BARE
            # -- a populated part slot is what makes the client's model refresh
            # dereference `unit+0xEA2`, and this record runs on somebody else's
            # machine (see POP_LOOK_DEFER). The look arrives on a SECOND cmd 7
            # once the alias has been standing there POP_LOOK_DEFER seconds.
            def _look_of(rec):
                _b = rec[3]
                return {"size": _b[fmoworld.POP_TYPE4_SIZE],
                        "build": _b[fmoworld.POP_TYPE4_BUILD],
                        "face": struct.unpack_from(
                            "<H", _b, fmoworld.POP_TYPE4_FACE)[0],
                        "uniform": struct.unpack_from(
                            "<H", _b, fmoworld.POP_TYPE4_UNIFORM)[0]}

            _wl = getattr(groupchannel.WORLD_PEERS[B], "type4_look", None)
            _bare = {"size": 0, "build": 0, "face": 0, "uniform": 0}
            _join_look = _look_of(pops[0]) if pops else None
            _defer_on = poplook.POP_LOOK_DEFER > 0
            _bare_ok = (not _defer_on) or _join_look == _bare
            print(f"  room: the JOIN record is BARE when the look is deferred "
                  f"(defer={poplook.POP_LOOK_DEFER:g}s, sent {_join_look!r}): "
                  f"{'OK' if _bare_ok else 'FAIL -- dressed at create time'}")
            ok &= bool(_bare_ok)

            # ...and the follow-up carries it. Drive the clock rather than
            # sleeping: the due time is the only thing gating it.
            # WARNING: Drive `room_queue` DIRECTLY rather than pumping a datagram:
            # pending records are resent until acked, so a pump would hand back
            # the bare join POP again, and consuming record indices here would
            # desynchronise every alias-stream assertion below.
            _rs = groupchannel.WORLD_PEERS[A].remotes.get(alias)
            if _defer_on and _rs is not None and _wl:
                _before = len(groupchannel.WORLD_PEERS[A].pending)
                # WARNING: room_queue also relays MOVEMENT. Snapshot the alias
                # stream's own state so this probe leaves nothing behind for
                # the movement/ack assertions further down to trip over.
                _save_rs = (list(_rs.pending), _rs.sent_pos, _rs.sent_at,
                            _rs.sent_n)
                _rs.look_due = time.time() - 1
                roomrelay.room_queue(groupchannel.WORLD_PEERS[A])
                _new = [r for r in groupchannel.WORLD_PEERS[A].pending[_before:]]
                _new_pops = [fmoworld.parse_record(r) if hasattr(
                    fmoworld, "parse_record") else r for r in _new]
                _late = [r for r in _new
                         if struct.unpack_from("<I", r, 4)[0]
                         == fmoworld.CMD_POP]
                _body = _late[0][fmoworld.REC_HDR:] if _late else None
                _id2 = (struct.unpack_from("<I", _body,
                                           fmoworld.POP_UNITID)[0]
                        if _body else None)
                _got2 = ({"size": _body[fmoworld.POP_TYPE4_SIZE],
                          "build": _body[fmoworld.POP_TYPE4_BUILD],
                          "face": struct.unpack_from(
                              "<H", _body, fmoworld.POP_TYPE4_FACE)[0],
                          "uniform": struct.unpack_from(
                              "<H", _body, fmoworld.POP_TYPE4_UNIFORM)[0]}
                         if _body else None)
                _late_ok = len(_late) == 1 and _id2 == alias and _got2 == _wl
                print(f"  room: the DEFERRED follow-up re-pops the SAME alias "
                      f"({_id2 and hex(_id2)}) carrying the originator's look "
                      f"({_got2!r}): {'OK' if _late_ok else 'FAIL'}")
                ok &= bool(_late_ok)
                # and ONCE -- repeating it every datagram would re-tear-down
                # the visual on the viewer's client forever (0x611E72E0).
                _before2 = len(groupchannel.WORLD_PEERS[A].pending)
                roomrelay.room_queue(groupchannel.WORLD_PEERS[A])
                _again = [r for r in groupchannel.WORLD_PEERS[A].pending[_before2:]
                          if struct.unpack_from("<I", r, 4)[0]
                          == fmoworld.CMD_POP]
                print(f"  room: and the deferred look is queued ONCE "
                      f"({len(_again)} on the next pass): "
                      f"{'OK' if not _again else 'FAIL -- it repeats'}")
                ok &= not _again
                # WARNING:KEY: THE SORTIE MUST NOT ERASE WHO SOMEBODY IS. A battle
                # self-POP is UnitType 0, and the arm that withholds the human
                # look from THAT record used to null the channel's PINNED copy
                # with it -- so a player who sortied was re-popped into every
                # other lobby with no appearance and the client drew its
                # default human. Live 2026-09-08: "after Molly Test returns
                # from combat, she shows as the stewardess NPC."
                #
                # WARNING: THIS IS A SOURCE ASSERTION, NOT A BEHAVIOURAL ONE, and it
                # says so rather than pretending otherwise. The arm is inline
                # in _serve_datagram and cannot be driven from here without
                # standing up a whole sortie; a pin that merely set the fields
                # and read them back would pass with the bug restored. What it
                # DOES catch is the regression that actually happened: the
                # assignment being put back.
                with open(datagram._serve_datagram.__code__.co_filename, encoding="utf-8") as _fh:
                    _msrc = _fh.read()
                # WARNING: ANCHOR ON A UNIQUE MARKER. There are TWO `if utype != 4:`
                # in this file and the other is an unrelated FMO_UDP_POP_NPC
                # validator -- anchoring on the keyword scanned that one and
                # passed VACUOUSLY, which is how this check first shipped.
                # WARNING: BUILT FROM PIECES so the literal does not appear in this
                # file twice -- writing it whole made the count 2 (the arm and
                # this line) and the assertion fired on itself.
                _mark = "UnitType {utype} != 4" + ": the "
                assert _msrc.count(_mark) == 1, "the arm's marker moved"
                _j = _msrc.index(_mark)
                _k = _msrc.rindex("if utype != 4:", 0, _j)
                _arm = _msrc[_k:_j]
                _pin_ok = ("chan.type4_look = None" not in _arm
                           and "chan.type4_model = None" not in _arm)
                print(f"  room: the UnitType!=4 arm does NOT null the "
                      f"channel's pinned look/model (source assertion -- the "
                      f"relay dresses sortied players from it): "
                      f"{'OK' if _pin_ok else 'FAIL -- the wipe is back'}")
                ok &= bool(_pin_ok)

                # KEY: AND A REMOTE POPPED BEFORE ITS LOOK EXISTS STILL GETS ONE.
                # Two clients leaving a battle land seconds apart, so the first
                # one back is popped the second while the second still has no
                # look pinned. Arming the follow-up only when a look was
                # ALREADY known left that remote undressed for the whole scene,
                # because nothing re-pops an alias once rs.popped is set.
                _rs2 = groupchannel.WORLD_PEERS[A].remotes.get(alias)
                _armed_ok = _rs2 is not None and _rs2.look_due is not None
                print(f"  room: the deferred look is ARMED even when the "
                      f"remote had no look at pop time (look_due="
                      f"{'set' if _armed_ok else 'None'}): "
                      f"{'OK' if _armed_ok else 'FAIL'}")
                ok &= bool(_armed_ok)

                # WARNING:KEY: AN ALIAS MUST NOT SHUFFLE ACROSS A SCENE CHANGE.
                # restart() used to clear alias_of, so the next join ORDER
                # decided the ids. One remote never shows it (always 0x200);
                # TWO shuffle, and the unit left on the old id stops receiving
                # movement and FREEZES. Live 2026-09-08, three players:
                # "Molly sees Dick as being in the same place he was minutes
                # ago."
                #
                # WARNING: On a THROWAWAY channel, not the fixture: restart() clears
                # `remotes`, and every alias-stream assertion after this point
                # would fail on a KeyError instead of on its own merits.
                _tw = worldchannel.WorldChannel(("198.51.100.8", 19155))
                _a1 = _tw.alias_for(("198.51.100.1", 19155))
                _a2 = _tw.alias_for(("198.51.100.2", 19155))
                _tw.remotes[_a1].popped = True
                _tw.restart()
                # WARNING: QUERY IN THE OPPOSITE ORDER. Asking again in the ORIGINAL
                # order re-mints the same ids even with the mapping cleared, so
                # the test cannot tell a stable mapping from a shuffled one --
                # measured: it passed with the bug restored. The shuffle only
                # shows when the remotes are next seen in a different order,
                # which is exactly what happens live.
                _a2b = _tw.alias_for(("198.51.100.2", 19155))
                _a1b = _tw.alias_for(("198.51.100.1", 19155))
                _a3 = _tw.alias_for(("198.51.100.3", 19155))
                _stable = ((_a1b, _a2b) == (_a1, _a2)
                           and _a3 not in (_a1, _a2)
                           and not _tw.remotes[_a1].popped)
                print(f"  room: a scene restart KEEPS each address->alias "
                      f"({hex(_a1)}, {hex(_a2)} unchanged), hands a NEW remote "
                      f"an unused id ({hex(_a3)}), and still clears the send "
                      f"state so everyone is re-popped: "
                      f"{'OK' if _stable else 'FAIL -- ids shuffled'}")
                ok &= bool(_stable)
            # leave BOTH streams as the assertions below expect them
                del groupchannel.WORLD_PEERS[A].pending[_before:]
                (_rs.pending, _rs.sent_pos, _rs.sent_at,
                 _rs.sent_n) = (_save_rs[0], _save_rs[1], _save_rs[2],
                                _save_rs[3])

            # KEY: And it must carry the key THIS channel's datagrams are
            # encrypted with, NUL-terminated, or the peer stream's cipher is
            # scheduled from an empty key and every movement record on it is
            # discarded by the client's own MD5 (measured live 2026-08-26:
            # "no movement is seen on either client's end").
            _blob = (pops[0][3][fmoworld.POP_CLIENT_BLOB:
                                fmoworld.POP_CLIENT_BLOB
                                + fmoworld.POP_CLIENT_BLOB_LEN] if pops else b"")
            _kwant = groupchannel.WORLD_PEERS[A].key or b""
            _key_ok = (not room.ROOM_PEER_KEY) or (
                _blob.split(b"\x00")[0] == _kwant and _kwant != b"")
            print(f"  room: the relayed POP carries the receiving channel's "
                  f"stream key ({_kwant!r}, sent {_blob.split(chr(0).encode())[0]!r}): "
                  f"{'OK' if _key_ok else 'FAIL -- the peer stream will not decrypt'}")
            ok &= _key_ok
            print(f"  room: the newcomer is POPped on the SELF stream as "
                  f"UnitID {popped_id if popped_id is None else hex(popped_id)}"
                  f": {'OK' if good else 'FAIL'}")
            ok &= good
            # The POP has to carry where they actually are, not the spawn.
            at = struct.unpack_from("<ffff", pops[0][3],
                                    fmoworld.POP_POS)[:3] if pops else ()
            here = bool(pops) and all(
                abs(a - b) < 5e-3
                for a, b in zip(at, (1.93, 0.0, -1.12)))
            print(f"  room: and at the position they walked to {at}: "
                  f"{'OK' if here else 'FAIL'}")
            ok &= here

            # B keeps walking; A's stream must now carry a cmd 240 on a
            # datagram stamped with B's ALIAS, never on A's own stream.
            groupchannel.WORLD_PEERS[B].pos = (-8.87, 0.0, -9.39)
            groupchannel.WORLD_PEERS[A].remotes[alias].sent_at = 0.0
            outs = pump(A, 2, 3, 1)
            on_alias = [r for o in outs if o and o["peer"] == alias
                        for r in o["records"] if r[2] == fmoworld.CMD_MOVE]
            on_self = [r for o in outs if o and o["peer"] == 1
                       for r in o["records"] if r[2] == fmoworld.CMD_MOVE]
            relayed = (fmoworld.parse_move(on_alias[0][3])
                       if on_alias else None)
            right = (len(on_alias) == 1 and not on_self and relayed
                     and all(abs(a - b) < 5e-3 for a, b in
                             zip(relayed["pos"], (-8.87, 0.0, -9.39))))
            print(f"  room: their movement is relayed ON THE ALIAS STREAM "
                  f"(peer {alias:#x}), never on the self stream: "
                  f"{'OK' if right else 'FAIL'}")
            ok &= right

            # WARNING: And the ordering, stated as an assertion rather than a hope:
            # the POP must have gone out on an EARLIER datagram than the first
            # alias-stream datagram, because the alias peer does not exist in
            # the client until the POP is consumed.
            print(f"  room: nothing is addressed to the alias in the round "
                  f"that introduces it: "
                  f"{'OK' if introduced_alone else 'FAIL'}")
            ok &= introduced_alone

            # And the room is symmetric -- B must be told about A too, or one
            # player sees a companion and the other sees an empty street.
            pump(B, 1, 2, 1)
            back = groupchannel.WORLD_PEERS[B].alias_of.get(A)
            print(f"  room: the introduction is SYMMETRIC (B knows A as "
                  f"{back if back is None else hex(back)}): "
                  f"{'OK' if back else 'FAIL'}")
            ok &= bool(back)

            # An alias-stream ACK from the client must retire OUR relayed
            # record and must not touch the self stream's counters.
            before = (groupchannel.WORLD_PEERS[A].tx_base, len(groupchannel.WORLD_PEERS[A].pending))
            pump(A, 0, 0, 1, peer=alias)
            after = (groupchannel.WORLD_PEERS[A].tx_base, len(groupchannel.WORLD_PEERS[A].pending))
            retired = (not groupchannel.WORLD_PEERS[A].remotes[alias].pending
                       and after == before)
            print(f"  room: an ACK on the alias stream retires only ITS "
                  f"records: {'OK' if retired else 'FAIL'}")
            ok &= retired

            # KEY: THE PEER LINK. The POP must carry
            # our endpoint (net order) and the alias's tag; the client's hello
            # on that tag must come back as cmd 4 on the alias stream stamped
            # with the tag; and its own-unit record for the peer must reach the
            # OTHER client under the alias that client knows the sender by.
            _pr = fmoworld.record_pop(0x200, unit_type=4, client_key=b"k",
                                      client_addr=("203.0.113.125", 61300),
                                      client_tag=0x41)
            _pb = _pr[fmoworld.REC_HDR:]
            _pop_ok = (_pb[0x30:0x34] == bytes([203, 0, 113, 125])
                       and _pb[0x34:0x36] == (61300).to_bytes(2, "big")
                       and _pb[0x36] == 0x41)
            print(f"  peer link: the room POP carries our endpoint (net order) "
                  f"and the alias tag at body+0x30/+0x34/+0x36: "
                  f"{'OK' if _pop_ok else 'FAIL'}")
            ok &= _pop_ok
            # THE OWNER (body+0x2C): a battle mate's args are copied from THEIR
            # self POP (owner = their own id 0x1001); the relayed unit must be
            # owned by the ALIAS, or the freeze check 0x61051BB0 keeps it still.
            _their = {"unit_type": 0, "extra": {battlepop.POP_AI_OWNER: struct.pack("<I", 0x1001)}}
            _ob = fmoworld.record_pop(0x200, **roomrelay.owned_by(_their, 0x200))[fmoworld.REC_HDR:]
            _own_ok = (struct.unpack_from("<I", _ob, battlepop.POP_AI_OWNER)[0] == 0x200
                       and struct.unpack_from("<I", _their["extra"][battlepop.POP_AI_OWNER])[0] == 0x1001)
            print(f"  peer link: a relayed mate is owned by its ALIAS (body+0x2C "
                  f"= 0x200), not the sender's own id: {'OK' if _own_ok else 'FAIL'}")
            ok &= _own_ok
            if room.PEER_LINK:
                _ca, _rsa = groupchannel.WORLD_PEERS[A], groupchannel.WORLD_PEERS[A].remotes[alias]
                _mine = _ca.self_unit()
                _before = (_ca.tx_base, len(_ca.pending))

                def _link_dg(frm, to, body, hid):
                    return fmoworld.build(*tables, peer=_mine, hid=hid, kind=2,
                                          ack=0, flag=0 if frm == 0 else 2,
                                          frm=frm, to=to, body=body)
                del sent[:]
                datagram._serve_datagram(sock, groupchannel.WORLD_PEERS, _link_dg(
                    0, 1, fmoworld.record(3, bytes(4), arg8=_mine), _rsa.tag), A)
                _rep = [fmoworld.parse(*tables, d) for _t, d in sent]
                _hello = (_rsa.linked and _rep and _rep[0]
                          and _rep[0]["peer"] == alias
                          and _rep[0]["kind"] == _rsa.tag
                          and any(c == 4 for _o, _s, c, _b in _rep[0]["records"])
                          and (_ca.tx_base, len(_ca.pending)) == _before)
                print(f"  peer link: a hello on tag {_rsa.tag:#x} is the ALIAS "
                      f"stream (not a self-stream restart), answered with cmd 4 "
                      f"stamped with the tag: {'OK' if _hello else 'FAIL'}")
                ok &= bool(_hello)
                _rsb = groupchannel.WORLD_PEERS[B].remotes[back]
                _n0 = len(_rsb.pending)
                datagram._serve_datagram(sock, groupchannel.WORLD_PEERS, _link_dg(
                    1, 2, fmoworld.record(23, bytes(16), arg8=_mine), _rsa.tag), A)
                _got = [struct.unpack_from("<II", r, 4) for r in _rsb.pending[_n0:]]
                _relay = (23, back) in _got
                print(f"  peer link: A's own cmd 23 reaches B as unit {back:#x} "
                      f"(B's alias for A): "
                      f"{'OK' if _relay else 'FAIL ' + repr(_got)}")
                ok &= _relay
                # Twin: an untagged datagram is still A's own stream.
                _self = peerlink.peer_stream_for(_ca, {"hid": udpconfig.UDP_HID, "peer": _mine})
                print(f"  peer link twin: an untagged datagram is the SELF "
                      f"stream: {'OK' if _self is None else 'FAIL'}")
                ok &= _self is None

            # Different zones are different rooms. The depop is pinned OFF for
            # this pump (it is ON by default since 2026-10-06); the block
            # below turns it on and off itself, then restores the default.
            _depop_default = room.ROOM_DEPOP
            room.ROOM_DEPOP = False
            rooms.WORLD_MAPS[B[0]] = 121
            outs = pump(A, 3, 4, 2)
            others = rooms.room_mates(groupchannel.WORLD_PEERS[A])
            print(f"  room: a client in another MapNo is not in the room "
                  f"({len(others)} mates): {'OK' if not others else 'FAIL'}")
            ok &= not others

            # PARTIAL: THE DEPOP (cmd 8), through the real room path. B has just
            # moved to MapNo 121, so from A's side B is a room-mate that is no
            # longer one -- to the viewer the same event as a 0x0152 LOG OUT
            # or ROOM_TTL silence. WARNING: room_queue runs BEFORE retire, and an
            # unacked record is retransmitted by design, so every pump below
            # acks what was pending BEFORE it: only records queued in that
            # very round show up in `outs`.
            _saved_depop = _depop_default

            def _ack_all():
                return groupchannel.WORLD_PEERS[A].tx_base + len(groupchannel.WORLD_PEERS[A].pending)
            try:
                # ON: a cmd 8 for B's alias rides A's SELF stream, the alias
                # is forgotten, and B's return is a FRESH POP under a new id.
                # (The previous pump already reported B gone with the knob
                # OFF and the report-once guard held; the knob does not flip
                # at runtime, so put the stream back to "not yet told".)
                room.ROOM_DEPOP = True
                groupchannel.WORLD_PEERS[A].remotes[alias].gone = None
                outs = pump(A, 4, 5, _ack_all())
                # a LOBBY channel: the lobby depop cmd 0xD3, body u32 UnitID
                # (cmd 8 is the battle class's and the lobby ignores it)
                deps = [r for o in outs if o and o["peer"] == 1
                        for r in o["records"] if r[2] == fmoworld.CMD_LOBBY_DEPOP]
                old8 = [r for o in outs if o and o["peer"] == 1
                        for r in o["records"] if r[2] == fmoworld.CMD_DEPOP]
                on_alias = [o for o in outs if o and o["peer"] == alias]
                good = (len(deps) == 1 and not old8
                        and struct.unpack_from("<I", deps[0][3], 0)[0] == alias
                        and not on_alias
                        and alias not in groupchannel.WORLD_PEERS[A].remotes
                        and B not in groupchannel.WORLD_PEERS[A].alias_of)
                print(f"  room: FMO_UDP_ROOM_DEPOP=1 -- a lobby leaver's alias "
                      f"gets ONE cmd 0xD3 on the SELF stream (UnitID {alias:#x}) "
                      f"and is forgotten: "
                      f"{'OK' if good else 'FAIL'}")
                ok &= good
                outs = pump(A, 5, 6, _ack_all())
                again = [r for o in outs if o and o["peer"] == 1
                         for r in o["records"]
                         if r[2] in (fmoworld.CMD_DEPOP, fmoworld.CMD_LOBBY_DEPOP)]
                print(f"  room: and it is queued ONCE, not on every datagram: "
                      f"{'OK' if not again else 'FAIL'}")
                ok &= not again
                rooms.WORLD_MAPS[B[0]] = 102
                outs = pump(A, 6, 7, _ack_all())
                pops2 = [r for o in outs if o and o["peer"] == 1
                         for r in o["records"] if r[2] == fmoworld.CMD_POP]
                alias2 = groupchannel.WORLD_PEERS[A].alias_of.get(B)
                fresh = (len(pops2) == 1 and alias2 not in (None, alias)
                         and struct.unpack_from(
                             "<I", pops2[0][3], fmoworld.POP_UNITID)[0] == alias2)
                print(f"  room: B's return after a depop is a fresh POP under "
                      f"a NEW alias ({alias2 if alias2 is None else hex(alias2)}"
                      f", was {alias:#x}): {'OK' if fresh else 'FAIL'}")
                ok &= fresh

                # OFF (the default -- the lobby session ignores cmd 8):
                # nothing is sent, the stream is KEPT with its reason, said
                # once, and B's return resumes on the same alias, no 2nd POP.
                room.ROOM_DEPOP = False
                n_marked = rooms.room_left(B[0], "0x0152 LOG OUT")
                outs = pump(A, 7, 8, _ack_all())
                deps = [r for o in outs if o and o["peer"] == 1
                        for r in o["records"]
                        if r[2] in (fmoworld.CMD_DEPOP, fmoworld.CMD_LOBBY_DEPOP)]
                rs2 = groupchannel.WORLD_PEERS[A].remotes.get(alias2)
                kept = (n_marked == 1 and not deps and rs2 is not None
                        and rs2.gone == "0x0152 LOG OUT"
                        and not rooms.room_mates(groupchannel.WORLD_PEERS[A]))
                print(f"  room: FMO_UDP_ROOM_DEPOP=0 -- 0x0152 marks the "
                      f"leaver, relay stops, NOTHING is sent, the alias is "
                      f"kept (gone={rs2.gone if rs2 else None!r}): "
                      f"{'OK' if kept else 'FAIL'}")
                ok &= kept
                # B relaunches: its channel restarts (from=0, ack=0), which
                # clears `left`; A must resume on the SAME alias, no re-POP.
                pump(B, 0, 1, 0, walk)
                groupchannel.WORLD_PEERS[B].popped = True
                groupchannel.WORLD_PEERS[B].pos = (1.0, 0.0, 1.0)
                if rs2:
                    rs2.sent_at = 0.0
                outs = pump(A, 8, 9, _ack_all())
                pops3 = [r for o in outs if o and o["peer"] == 1
                         for r in o["records"] if r[2] == fmoworld.CMD_POP]
                mv3 = [r for o in outs if o and o["peer"] == alias2
                       for r in o["records"] if r[2] == fmoworld.CMD_MOVE]
                resumed = (groupchannel.WORLD_PEERS[B].left is None and not pops3
                           and len(mv3) == 1 and rs2 is not None
                           and rs2.gone is None
                           and groupchannel.WORLD_PEERS[A].alias_of.get(B) == alias2)
                print(f"  room: and B's return resumes the relay on the SAME "
                      f"alias with no second POP: "
                      f"{'OK' if resumed else 'FAIL'}")
                ok &= resumed
            finally:
                room.ROOM_DEPOP = _saved_depop
        finally:
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(saved[0])
            rooms.WORLD_MAPS.clear()
            rooms.WORLD_MAPS.update(saved[1])

        # ------------------------------------------------------------------ #
        # KEY: THE HELLO-ACK (FMO_UDP_HELLO_ACK), through the real _serve_datagram.
        # Proves what we SEND: off by default sends nothing; on, exactly one
        # transport record of the chosen cmd with a u32 UnitID body, on the
        # self stream, once per channel, re-armed by restart(). The client
        # half (+0x10E0 -> 2, the state-3 arm firing) is read out of the
        # binary and only a live fmocrash/--live or /targetnpc can confirm it.
        _saved_hello = (popself.HELLO_ACK, popself.HELLO_ACK_ON, dict(groupchannel.WORLD_PEERS))
        groupchannel.WORLD_PEERS.clear()
        try:
            sock, A = _FakeSock(), ("192.0.2.9", 19155)
            del sent[:]

            def hello_pump(frm, to, ack, body=b""):
                del sent[:]
                datagram._serve_datagram(sock, groupchannel.WORLD_PEERS,
                                         fmoworld.build(*tables, peer=1, hid=udpconfig.UDP_HID,
                                                        kind=2, ack=ack, flag=2,
                                                        frm=frm, to=to, body=body), A)
                return [c for _t, d in sent
                        for _o, _s, c, _b in fmoworld.parse(*tables, d)["records"]]

            popself.HELLO_ACK, popself.HELLO_ACK_ON = 0, 0
            off = hello_pump(0, 1, 0)
            print(f"  hello-ack: OFF by default sends no transport record "
                  f"(cmds {off}): {'OK' if 4 not in off and 16 not in off else 'FAIL'}")
            ok &= 4 not in off and 16 not in off

            popself.HELLO_ACK, popself.HELLO_ACK_ON = 4, 0
            groupchannel.WORLD_PEERS.clear()
            first = hello_pump(0, 1, 0)
            second = hello_pump(1, 2, 1)          # acks record 0 -> retired
            armed = first.count(4) == 1 and 4 not in second
            print(f"  hello-ack: ON sends cmd 4 ONCE per channel "
                  f"(first {first}, then {second}): {'OK' if armed else 'FAIL'}")
            ok &= armed
            # the body is a u32 UnitID -- with no POP and no latched id, the
            # datagram's own self id (1 here), as 0x611E2F10 would send it
            groupchannel.WORLD_PEERS[A].restart()
            again = hello_pump(0, 1, 0)
            body_ok = False
            for _t, d in sent:
                for _o, _s, c, b in fmoworld.parse(*tables, d)["records"]:
                    if c == 4:
                        body_ok = b[:4] == (1).to_bytes(4, "little")
            print(f"  hello-ack: restart() re-arms it and the body is the "
                  f"self UnitID (cmds {again}, body ok {body_ok}): "
                  f"{'OK' if again.count(4) == 1 and body_ok else 'FAIL'}")
            ok &= again.count(4) == 1 and body_ok

            # ON=15 waits for the client's cmd 15 and echoes the id it carried
            popself.HELLO_ACK, popself.HELLO_ACK_ON = 16, 15
            groupchannel.WORLD_PEERS.clear()
            quiet = hello_pump(0, 1, 0)
            answered = hello_pump(1, 2, 0, fmoworld.record(
                15, (0x82080C00).to_bytes(4, "little")))
            echoed = None
            for _t, d in sent:
                for _o, _s, c, b in fmoworld.parse(*tables, d)["records"]:
                    if c == 16:
                        echoed = int.from_bytes(b[:4], "little")
            wait_ok = 16 not in quiet and answered.count(16) == 1 \
                and echoed == 0x82080C00
            print(f"  hello-ack: ON=15 answers the client's cmd 15 with its "
                  f"id ({quiet} then {answered}, echoed "
                  f"{echoed if echoed is None else hex(echoed)}): "
                  f"{'OK' if wait_ok else 'FAIL'}")
            ok &= wait_ok
        finally:
            popself.HELLO_ACK, popself.HELLO_ACK_ON = _saved_hello[0], _saved_hello[1]
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_saved_hello[2])

        # ------------------------------------------------------------------ #
        # WARNING: PLAN 1.2: the battle self-POP override (FMO_UDP_POP_BATTLE),
        # through the real _serve_datagram under a BATTLE key. Proves what we
        # SEND: off, a battle channel pops FMO_UDP_POP's type; on, the
        # override's id/type, with client_kind 3 (auto on battle) and -- for a
        # non-4 type -- NO sex/look bytes (+0x7A / +0x188.. are human offsets,
        # undecoded for the wanzer classes); and a LOBBY channel is untouched
        # by the knob. The client half (the possess chain 0x6105AAE0 runs for
        # types 0/1/2/3/5/6, never 4) is read out of the binary; only a live
        # sortie can show a wanzer DRAWING. (POP/POP_BATTLE/POP_AFTER are
        # declared global at the top of selftest().)
        _saved_bp = (popsweep.POP, battlepop.POP_BATTLE, popself.POP_AFTER, dict(groupchannel.WORLD_PEERS),
                     dict(rooms.WORLD_MAPS))
        try:
            bt_tables = fmoworld.bf_init(battle_twin)
            sockb, BA, LA = _FakeSock(), ("192.0.2.7", 19155), \
                ("192.0.2.8", 19155)
            # Distinct maps, so the room relay cannot cross-POP the two test
            # channels into each other's streams (same-map is the room key).
            rooms.WORLD_MAPS[BA[0]], rooms.WORLD_MAPS[LA[0]] = 418, 121

            def bp_pump(tb, addr2, frm, to, ack):
                del sent[:]
                datagram._serve_datagram(sockb, groupchannel.WORLD_PEERS,
                                         fmoworld.build(*tb, peer=1, hid=0, kind=2,
                                                        ack=ack, flag=2, frm=frm,
                                                        to=to), addr2)
                return [(c, b) for _t, d in sent
                        for _o, _s, c, b in
                        fmoworld.parse(*tb, d)["records"]]

            def first_pop(recs):
                for c, b in recs:
                    if c == fmoworld.CMD_POP:
                        return b
                return None

            popsweep.POP, popself.POP_AFTER = (1, 4), 0

            # OFF: the battle channel pops FMO_UDP_POP's type.
            battlepop.POP_BATTLE = None
            groupchannel.WORLD_PEERS.clear()
            b0 = first_pop(bp_pump(bt_tables, BA, 0, 1, 0))
            off_ok = (b0 is not None
                      and b0[fmoworld.POP_UNITTYPE] == 4
                      and struct.unpack_from(
                          "<I", b0, fmoworld.POP_CLIENT_KIND)[0] == 3)
            print(f"  battle pop: override OFF -> battle channel pops "
                  f"FMO_UDP_POP's type 4, client_kind 3 (auto): "
                  f"{'OK' if off_ok else 'FAIL'}")
            ok &= off_ok

            # ON: the override's id/type, and no human appearance bytes.
            battlepop.POP_BATTLE = (1, 1)
            groupchannel.WORLD_PEERS.clear()
            b1 = first_pop(bp_pump(bt_tables, BA, 0, 1, 0))
            chb = groupchannel.WORLD_PEERS.get(BA)
            on_ok = (b1 is not None
                     and b1[fmoworld.POP_UNITTYPE] == 1
                     and struct.unpack_from(
                         "<I", b1, fmoworld.POP_UNITID)[0] == 1
                     and struct.unpack_from(
                         "<I", b1, fmoworld.POP_CLIENT_KIND)[0] == 3
                     and b1[fmoworld.POP_TYPE4_MODEL] == 0
                     and b1[fmoworld.POP_TYPE4_SIZE] == 0
                     and struct.unpack_from(
                         "<H", b1, fmoworld.POP_TYPE4_FACE)[0] == 0
                     and chb is not None and chb.type4_look is None
                     and chb.type4_model is None)
            print(f"  battle pop: FMO_UDP_POP_BATTLE=1:1 -> UnitType 1, "
                  f"client_kind 3, sex/look bytes WITHHELD: "
                  f"{'OK' if on_ok else 'FAIL'}")
            ok &= on_ok

            # ...and the knob does not leak into a LOBBY channel.
            lb = first_pop(bp_pump(tables, LA, 0, 1, 0))
            lobby_ok = lb is not None and lb[fmoworld.POP_UNITTYPE] == 4
            print(f"  battle pop: the override leaves a LOBBY channel on "
                  f"type 4: {'OK' if lobby_ok else 'FAIL'}")
            ok &= lobby_ok

            # VERIFIED:KEY: THE WIRING, not just the format. The pins further down prove
            # record_pop lays the part array out correctly; this proves the
            # BATTLE POP ACTUALLY CARRIES IT. There is no character store in a
            # selftest, so pop_parts_for is stubbed -- what is under test is the
            # emit site's decision, which is the half that can silently not fire.
            _real_ppf = popparts.pop_parts_for
            try:
                _giza_w = inventory.STARTER_SETUPS[(1, 3)]
                popparts.pop_parts_for = lambda _ip: (list(_giza_w), "selftest stub")
                battlepop.POP_BATTLE = (1, 1)
                groupchannel.WORLD_PEERS.clear()
                b2 = first_pop(bp_pump(bt_tables, BA, 0, 1, 0))
                _want = fmoworld.pop_parts_block(_giza_w)
                dressed = (b2 is not None
                           and b2[fmoworld.POP_PARTS:
                                  fmoworld.POP_PARTS + len(_want)] == _want)
                print(f"  battle pop: a wanzer-type battle POP CARRIES the "
                      f"part array at body+{fmoworld.POP_PARTS:#x}: "
                      f"{'OK' if dressed else 'FAIL'}")
                ok &= dressed

                # ...and a type-4 battle POP does NOT, because that unit never
                # reaches the dresser. Sending it would be 132 bytes the client
                # copies and never reads -- record_pop refuses the pair, so a
                # regression here is a REFUSED POP (no unit at all), which is
                # why the emit site must withhold rather than rely on the guard.
                battlepop.POP_BATTLE = (1, 4)
                groupchannel.WORLD_PEERS.clear()
                b3 = first_pop(bp_pump(bt_tables, BA, 0, 1, 0))
                held = (b3 is not None
                        and b3[fmoworld.POP_UNITTYPE] == 4
                        and b3[fmoworld.POP_PARTS:
                               fmoworld.POP_PARTS + len(_want)]
                        == bytes(len(_want)))
                print(f"  battle pop: a UnitType-4 battle POP WITHHOLDS them "
                      f"and is still sent: {'OK' if held else 'FAIL'}")
                ok &= held

                # ...and a LOBBY channel never carries them, whatever the type.
                # The lobby wanzer already dresses from 0x0166; this array is
                # scene 4's, and the lobby is the path that works.
                battlepop.POP_BATTLE = (1, 1)
                groupchannel.WORLD_PEERS.clear()
                lb2 = first_pop(bp_pump(tables, LA, 0, 1, 0))
                clean = (lb2 is not None
                         and lb2[fmoworld.POP_PARTS:
                                 fmoworld.POP_PARTS + len(_want)]
                         == bytes(len(_want)))
                print(f"  battle pop: a LOBBY POP carries NO part array: "
                      f"{'OK' if clean else 'FAIL'}")
                ok &= clean
                # VERIFIED:KEY: THE DRESS PROBE (FMO_BATTLE_DUMMY). What is under test
                # is the emit site's DECISION, not the wire format: that it
                # pops a SECOND id on a battle channel, dressed, once -- and
                # that it REFUSES the self id, which is the whole point of the
                # probe (0x611ED660's battle_map.find would hit, read status 3
                # and force the wreck model, so a probe on the self id measures
                # exactly what it was built to step around).
                _saved_bd = battlepop.BATTLE_DUMMY
                try:
                    battlepop.POP_BATTLE = (1, 0)
                    battlepop.BATTLE_DUMMY = (0x2222, 0, None)
                    groupchannel.WORLD_PEERS.clear()
                    _outs = bp_pump(bt_tables, BA, 0, 1, 0)
                    _pops = [b for c, b in _outs if c == fmoworld.CMD_POP]
                    _dummy = [b for b in _pops
                              if struct.unpack_from("<I", b,
                                                    fmoworld.POP_UNITID)[0]
                              == 0x2222]
                    _dressed = (len(_dummy) == 1
                                and _dummy[0][fmoworld.POP_UNITTYPE] == 0
                                and _dummy[0][fmoworld.POP_PARTS:
                                              fmoworld.POP_PARTS + len(_want)]
                                == _want)
                    print(f"  battle dummy: a SECOND unit id is popped on the "
                          f"battle channel, UnitType 0, carrying the same part "
                          f"array: {'OK' if _dressed else 'FAIL'}")
                    ok &= _dressed
                    # ...and only ONCE per channel. The ack matters: an
                    # unacked record is RETRANSMITTED, which is the transport
                    # working, not a second pop -- so ack what we just queued
                    # before asking whether anything new was produced.
                    _ack = (groupchannel.WORLD_PEERS[BA].tx_base
                            + len(groupchannel.WORLD_PEERS[BA].pending))
                    _again = [b for c, b in bp_pump(bt_tables, BA, 1, 2, _ack)
                              if c == fmoworld.CMD_POP
                              and struct.unpack_from(
                                  "<I", b, fmoworld.POP_UNITID)[0] == 0x2222]
                    print(f"  battle dummy: it is popped ONCE per channel "
                          f"(flag %r), not every datagram: %s"
                          % (groupchannel.WORLD_PEERS[BA].dummy_popped,
                             'OK' if not _again else 'FAIL'))
                    ok &= not _again

                    # WARNING: the guard: the SELF id must be refused outright
                    battlepop.BATTLE_DUMMY = (1, 0, None)      # == POP_BATTLE's id
                    groupchannel.WORLD_PEERS.clear()
                    _self_pops = [
                        b for c, b in bp_pump(bt_tables, BA, 0, 1, 0)
                        if c == fmoworld.CMD_POP
                        and struct.unpack_from("<I", b,
                                               fmoworld.POP_UNITID)[0] == 1]
                    refused = len(_self_pops) == 1        # the self POP only
                    print(f"  battle dummy: the SELF unit id is REFUSED (it "
                          f"would measure the wreck path the probe exists to "
                          f"avoid): {'OK' if refused else 'FAIL'}")
                    ok &= refused

                    # and never on a LOBBY channel
                    battlepop.BATTLE_DUMMY = (0x2222, 0, None)
                    groupchannel.WORLD_PEERS.clear()
                    _lob = [b for c, b in bp_pump(tables, LA, 0, 1, 0)
                            if c == fmoworld.CMD_POP
                            and struct.unpack_from(
                                "<I", b, fmoworld.POP_UNITID)[0] == 0x2222]
                    print(f"  battle dummy: never popped on a LOBBY channel: "
                          f"{'OK' if not _lob else 'FAIL'}")
                    ok &= not _lob
                    # VERIFIED:KEY: THE GATE POP. What is under test is the ORDER and
                    # the pairing: an ALIVE self-pop first, then a SECOND cmd-7
                    # on the SAME id carrying client_kind 3. Both halves matter
                    # -- the alive one keeps char+0x1C out of {2,3} so the unit
                    # is a wanzer, the kind-3 one sets +0x10DC before the char
                    # lookup returns.
                    _saved_gate = (battlepop.BATTLE_GATE_POP, popself.POP_CLIENT_KIND_KNOB,
                                   battlepop.BATTLE_DUMMY)
                    try:
                        battlepop.BATTLE_DUMMY = None
                        battlepop.BATTLE_GATE_POP = True
                        popself.POP_CLIENT_KIND_KNOB = "0"      # an ALIVE self-pop
                        battlepop.POP_BATTLE = (1, 0)
                        groupchannel.WORLD_PEERS.clear()
                        _gp = [b for c, b in bp_pump(bt_tables, BA, 0, 1, 0)
                               if c == fmoworld.CMD_POP
                               and struct.unpack_from(
                                   "<I", b, fmoworld.POP_UNITID)[0] == 1]
                        _kinds = [struct.unpack_from(
                            "<I", b, fmoworld.POP_CLIENT_KIND)[0] for b in _gp]
                        paired = _kinds == [0, 3]
                        print(f"  gate pop: an ALIVE self-pop (client_kind 0) "
                              f"is followed by a kind-3 pop on the SAME id, in "
                              f"that order (kinds {_kinds}): "
                              f"{'OK' if paired else 'FAIL'}")
                        ok &= paired

                        # ...and it must NOT fire when the self-pop is already
                        # kind 3: there the gate is already set and a second
                        # peer would be allocated for nothing.
                        popself.POP_CLIENT_KIND_KNOB = "3"
                        groupchannel.WORLD_PEERS.clear()
                        _gk = [struct.unpack_from(
                            "<I", b, fmoworld.POP_CLIENT_KIND)[0]
                            for c, b in bp_pump(bt_tables, BA, 0, 1, 0)
                            if c == fmoworld.CMD_POP
                            and struct.unpack_from(
                                "<I", b, fmoworld.POP_UNITID)[0] == 1]
                        skipped = _gk == [3]
                        print(f"  gate pop: it does NOT fire when the self-pop "
                              f"is already client_kind 3 (kinds {_gk}): "
                              f"{'OK' if skipped else 'FAIL'}")
                        ok &= skipped

                        # ...and never on a LOBBY channel.
                        popself.POP_CLIENT_KIND_KNOB = "0"
                        groupchannel.WORLD_PEERS.clear()
                        _gl = [b for c, b in bp_pump(tables, LA, 0, 1, 0)
                               if c == fmoworld.CMD_POP]
                        print(f"  gate pop: never on a LOBBY channel "
                              f"({len(_gl)} pop(s)): "
                              f"{'OK' if len(_gl) == 1 else 'FAIL'}")
                        ok &= len(_gl) == 1
                        # WARNING: the battle spawn override -- battle channel only,
                        # because the lobby's own map-keyed row is CORRECT and
                        # the whole defect is that a mission map has no row.
                        _saved_pos = battlepop.BATTLE_POS
                        try:
                            battlepop.BATTLE_POS = (64.0, 5.0, 64.0, 0.0)
                            battlepop.BATTLE_GATE_POP = False
                            groupchannel.WORLD_PEERS.clear()
                            _bp = [b for c, b in bp_pump(bt_tables, BA, 0, 1, 0)
                                   if c == fmoworld.CMD_POP][0]
                            _bxyz = struct.unpack_from("<fff", _bp,
                                                       fmoworld.POP_POS)
                            groupchannel.WORLD_PEERS.clear()
                            _lp = [b for c, b in bp_pump(tables, LA, 0, 1, 0)
                                   if c == fmoworld.CMD_POP][0]
                            _lxyz = struct.unpack_from("<fff", _lp,
                                                       fmoworld.POP_POS)
                            moved = (_bxyz == (64.0, 5.0, 64.0)
                                     and _lxyz != (64.0, 5.0, 64.0))
                            print(f"  battle pos: the BATTLE pop spawns at "
                                  f"{_bxyz} and the LOBBY pop is untouched at "
                                  f"{_lxyz}: {'OK' if moved else 'FAIL'}")
                            ok &= moved
                        finally:
                            battlepop.BATTLE_POS = _saved_pos
                    finally:
                        (battlepop.BATTLE_GATE_POP, popself.POP_CLIENT_KIND_KNOB,
                         battlepop.BATTLE_DUMMY) = _saved_gate
                finally:
                    battlepop.BATTLE_DUMMY = _saved_bd
            finally:
                popparts.pop_parts_for = _real_ppf
        finally:
            popsweep.POP, battlepop.POP_BATTLE, popself.POP_AFTER = _saved_bp[0], _saved_bp[1], \
                _saved_bp[2]
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_saved_bp[3])
            rooms.WORLD_MAPS.clear()
            rooms.WORLD_MAPS.update(_saved_bp[4])

        # ------------------------------------------------------------------ #
        # VERIFIED:KEY: THE WANZER'S PART RECORDS (fmoworld.POP_PARTS, body+0x8C) --
        # the battle dresser 0x611F70A0's input. Pure: these drive the builder
        # and record_pop directly, so they pin the WIRE FORMAT and the
        # single-source property without needing a character store.
        #
        # WARNING: What these CANNOT say: whether the client draws. 0x611F5700 skips a
        # zero record silently and resolves a bad id to no model, so the only
        # bar is `fmocrash.py --wanzer` reporting non-zero part slots live.
        _giza = inventory.STARTER_SETUPS[(1, 3)]
        _blk = fmoworld.pop_parts_block(_giza)
        _len_ok = len(_blk) == fmoworld.POP_PART_STRIDE * fmoworld.POP_PART_COUNT
        _at = {}
        for _i in range(fmoworld.POP_PART_COUNT):
            _o = _i * fmoworld.POP_PART_STRIDE
            _at[_i] = (struct.unpack_from("<H", _blk, _o)[0], _blk[_o + 2])
        _lay_ok = _len_ok and all(
            _at[_i] == (_d, _k) for _i, _k, _d in _giza) and all(
            _at[_i] == (0, 0) for _i in range(fmoworld.POP_PART_COUNT)
            if _i not in {_i2 for _i2, _, _ in _giza})
        print(f"  battle parts: the Giza lays out as u16 id @+0 / u8 kind @+2, "
              f"stride {fmoworld.POP_PART_STRIDE:#x}, {fmoworld.POP_PART_COUNT} "
              f"records, gaps left ZERO (0x611F70A0 skips those): "
              f"{'OK' if _lay_ok else 'FAIL'}")
        ok &= _lay_ok

        # KEY: SINGLE SOURCE. The battle's part array and the garage's 0x0166
        # setup 1 must be the SAME loadout: a player sorties in what the hangar
        # showed. Deriving them separately is exactly how the equipped list and
        # the inventory drifted apart in the first place, so this reads the
        # parts BACK OUT of the real garage block.
        _same = popparts.stored_setup1_parts(inventory.reply_0166(parts=_giza, slots=1)) == _giza
        print(f"  battle parts: they round-trip out of the SAME 0x0166 block "
              f"the garage serves (stored_setup1_parts == the setup): "
              f"{'OK' if _same else 'FAIL'}")
        ok &= _same

        # The two guards that must REFUSE rather than send. Both produce a
        # wrong screen with a clean log if they ever silently pass.
        def _refuses(**kw):
            try:
                fmoworld.record_pop(1, parts=_giza, client_kind=3, **kw)
            except ValueError:
                return True
            return False
        _t4 = _refuses(unit_type=4)
        print(f"  battle parts: a UnitType-4 POP carrying parts is REFUSED "
              f"(0x611E7190 never calls the dresser): "
              f"{'OK' if _t4 else 'FAIL'}")
        ok &= _t4
        _mf = _refuses(unit_type=1, model_flags=0x58)
        print(f"  battle parts: parts + model_flags is REFUSED (body+0x8E is "
              f"BOTH record 0's kind and the selector): "
              f"{'OK' if _mf else 'FAIL'}")
        ok &= _mf
        _dud = False
        try:
            fmoworld.pop_parts_block([(0, 0x14, 26)])   # kind 0x14 = a human's
        except ValueError:
            _dud = True
        print(f"  battle parts: a kind with no master table is REFUSED, not "
              f"sent as a silent empty slot: {'OK' if _dud else 'FAIL'}")
        ok &= _dud

        # ...and a real wanzer POP carries them at exactly body+0x8C, with
        # every other decoded field untouched.
        # `record()` is a 0x10-byte header then the body, so the body starts at
        # +0x10 of the record -- sliced by name rather than by a literal so a
        # header change cannot quietly move what this is asserting about.
        _pb = fmoworld.record_pop(1, unit_type=1, client_kind=3,
                                  parts=_giza)[fmoworld.REC_HDR:]
        _bare = fmoworld.record_pop(1, unit_type=1,
                                    client_kind=3)[fmoworld.REC_HDR:]
        _lo, _hi = fmoworld.POP_PARTS, fmoworld.POP_PARTS + len(_blk)
        _placed = (len(_pb) == fmoworld.POP_BODY_LEN
                   and _pb[_lo:_hi] == _blk
                   # KEY: and NOTHING ELSE MOVED. The difference between a POP
                   # with parts and the identical POP without them must be
                   # exactly those 132 bytes: an unknown byte changing
                   # elsewhere in this body is what black-screened lobby world
                   # entry on 2026-09-04.
                   and _pb[:_lo] == _bare[:_lo]
                   and _pb[_hi:] == _bare[_hi:]
                   and _bare[_lo:_hi] == bytes(len(_blk)))
        print(f"  battle parts: record_pop lands them at body+{_lo:#x} and "
              f"changes NOTHING else in the 0x1C8-byte body: "
              f"{'OK' if _placed else 'FAIL'}")
        ok &= _placed

        # ------------------------------------------------------------------ #
        # VERIFIED:KEY: THE MISSION BLOCK'S BATTLE AREA AND TIME LIMIT. Both were served
        # as zeros, and live 2026-09-08 that is why the pilot read "OUT OF
        # BATTLE AREA" at the exact centre of the map and was withdrawn seconds
        # into every battle -- including the SPECTATOR ones, before we had a
        # unit, which is why neither ever looked like a unit problem.
        _area = missionblock.mission_area_fields(area=(0.0, 0.0, 128.0, 128.0), seconds=1800)
        _amap = {lab: (off, raw) for lab, off, raw, _s in _area}
        _rect_off, _rect = _amap["AreaRect[0]"]
        # decode it the way the CLIENT does: max = min + size (0x610F1D05)
        _minx = struct.unpack_from("<f", _rect, missionblock.MB_RECT_MINX)[0]
        _minz = struct.unpack_from("<f", _rect, missionblock.MB_RECT_MINZ)[0]
        _maxx = _minx + struct.unpack_from("<f", _rect, missionblock.MB_RECT_SIZEX)[0]
        _maxz = _minz + struct.unpack_from("<f", _rect, missionblock.MB_RECT_SIZEZ)[0]
        _geom = (len(_rect) == missionblock.MB_RECT_STRIDE
                 and (_minx, _minz, _maxx, _maxz) == (0.0, 0.0, 128.0, 128.0)
                 and _amap["RectDataCnt"][1] == struct.pack("<I", 1))
        print(f"  mission area: one rect at block+{_rect_off:#x} decodes back "
              f"through the client's own min+size as x {_minx}..{_maxx}, "
              f"z {_minz}..{_maxz}, with RectDataCnt 1: "
              f"{'OK' if _geom else 'FAIL'}")
        ok &= _geom

        # KEY: THE PLAY BOUNDARY. The rect above is the OBJECTIVE zone; this
        # is the thing 0x6111DF00 reads and 0x6111E5E0 tests the player
        # against, and serving it as zeros is why "OUT OF BATTLE AREA" showed
        # at the exact centre of the map. Decoded through the CLIENT's own
        # reader: four s16, min then max, X then Z, no scaling.
        _box_off, _box = _amap["BattleArea"]
        _bx0, _bz0, _bx1, _bz1 = struct.unpack("<hhhh", _box)
        _boxok = (_box_off == 0xC48 and len(_box) == 8
                  and (_bx0, _bz0, _bx1, _bz1) == (0, 0, 128, 128))
        print(f"  battle area: the s16 boundary lands at block+{_box_off:#x} "
              f"as x {_bx0}..{_bx1}, z {_bz0}..{_bz1} (0x6111DF00): "
              f"{'OK' if _boxok else 'FAIL'}")
        ok &= _boxok

        # WARNING: IT MUST CONTAIN THE PILOT. This is the assertion the whole bug was
        # missing: run the client's own containment test (0x6111E5E0 --
        # minX<=x<=maxX && minZ<=z<=maxZ, X and Z only) over the box we are
        # about to serve, at the position we are about to spawn him. The zeros
        # we served before fail it at EVERY coordinate, which is the bug.
        def _inside(bx, x, z):
            return bx[0] <= x <= bx[2] and bx[1] <= z <= bx[3]
        _live = (_bx0, _bz0, _bx1, _bz1)
        _centre = _inside(_live, 64.0, 64.0)
        _zeros = _inside((0, 0, 0, 0), 64.0, 64.0)
        print(f"  battle area: the map centre (64,64) is inside the box we "
              f"serve ({_centre}) and was outside the zeros we served before "
              f"({_zeros}): {'OK' if _centre and not _zeros else 'FAIL'}")
        ok &= _centre and not _zeros

        # WARNING: ROUNDING GOES OUTWARD. An int16 field cannot hold a fractional
        # edge, and a box one unit SHORT puts the pilot out of the area at the
        # map edge -- so floor the mins, ceil the maxes, never nearest.
        _r = dict((lab, raw) for lab, _o, raw, _s in
                  missionblock.battle_area_box((0.5, -0.5, 127.25, 127.25)))["BattleArea"]
        _rr = struct.unpack("<hhhh", _r)
        _rok = _rr == (0, -1, 128, 127)
        print(f"  battle area: a fractional spec rounds OUTWARD "
              f"(0.5,-0.5 +127.25 -> {_rr}, want (0, -1, 128, 127)): "
              f"{'OK' if _rok else 'FAIL'}")
        ok &= _rok

        # VERIFIED:KEY: FMO_BATTLE_BOUNDS: the boundary INDEPENDENT of the objective
        # rect. Measured live in map 267 -- the client's own map box is
        # x/z -2048..2048 -- and the 0..128 we shipped first covered 3.1% of
        # it, which is the live report "the safe area is very small".
        _bb = dict((lab, (off, raw)) for lab, off, raw, _s in
                   missionblock.mission_area_fields(area=(0.0, 0.0, 128.0, 128.0),
                                                    seconds=0))
        _wide = dict((lab, raw) for lab, _o, raw, _s in
                     [("BattleArea",) + tuple(missionblock.battle_area_box(
                         (0.0, 0.0, 128.0, 128.0),
                         bounds=(-2048, -2048, 2048, 2048))[0][1:])])
        _wv = struct.unpack("<hhhh", _wide["BattleArea"])
        # the OBJECTIVE rect must NOT follow the boundary
        _rect_still = _bb["AreaRect[0]"][1]
        _rminx = struct.unpack_from("<f", _rect_still, missionblock.MB_RECT_MINX)[0]
        _rsx = struct.unpack_from("<f", _rect_still, missionblock.MB_RECT_SIZEX)[0]
        _split = (_wv == (-2048, -2048, 2048, 2048)
                  and (_rminx, _rsx) == (0.0, 128.0))
        print(f"  battle area: FMO_BATTLE_BOUNDS sets the boundary to {_wv} "
              f"while the OBJECTIVE rect stays x {_rminx}..{_rminx + _rsx} -- "
              f"two fields, two sizes: {'OK' if _split else 'FAIL'}")
        ok &= _split

        # WARNING: AND AN INSIDE-OUT BOX IS STILL REFUSED on that path too.
        _inv = missionblock.battle_area_box(None, bounds=(100, 100, -100, -100))
        _invok = _inv[0][2] == b"" and "max < min" in _inv[0][3]
        print(f"  battle area: an inside-out FMO_BATTLE_BOUNDS is REFUSED "
              f"(outside everywhere is the zero-box bug again): "
              f"{'OK' if _invok else 'FAIL'}")
        ok &= _invok

        # WARNING: AND A BOX THAT WILL NOT FIT int16 IS REFUSED, NOT WRAPPED. A
        # wrapped max reads as inside-out, which is the zero-area bug again
        # wearing a different hat. (fixed-width-field-is-only-what-you-proved.)
        _huge = missionblock.battle_area_box((0.0, 0.0, 40000.0, 10.0))
        _href = _huge[0][2] == b"" and "REFUSED" in _huge[0][3]
        print(f"  battle area: a box past +/-32767 is REFUSED rather than "
              f"wrapped ({_huge[0][3][:44]}...): "
              f"{'OK' if _href else 'FAIL'}")
        ok &= _href

        # WARNING: IT MUST FIT. The block is 3,400 bytes and the rect array is a long
        # way in; an overrun would silently corrupt whatever follows it in the
        # 0x013A payload rather than erroring.
        _fits = _rect_off + missionblock.MB_RECT_STRIDE <= missionblock.M1C1_BLOCK_LEN
        print(f"  mission area: the rect ends at block+"
              f"{_rect_off + missionblock.MB_RECT_STRIDE:#x}, inside the "
              f"{missionblock.M1C1_BLOCK_LEN}-byte block: {'OK' if _fits else 'FAIL'}")
        ok &= _fits

        _t = dict((lab, raw) for lab, _o, raw, _s in _area)["TimeLimit"]
        _toff = dict((lab, off) for lab, off, _r, _s in _area)["TimeLimit"]
        _tok = _toff == 0x4C and _t == struct.pack("<I", 1800)
        print(f"  mission time: the limit lands at block+{_toff:#x} as "
              f"{struct.unpack('<I', _t)[0]}s (0x612381B9; zero = a battle that "
              f"is already over): {'OK' if _tok else 'FAIL'}")
        ok &= _tok

        # KEY: AND THE DEFAULT MUST STAY ZEROS. Both knobs off has to leave the
        # 0x013A reply byte-identical to what it served before this change --
        # that is what makes FMO_MISSION_AREA/_TIME a real revert.
        _none = missionblock.mission_area_fields(area=None, seconds=0)
        print(f"  mission area: with both knobs unset NOTHING is emitted, so "
              f"the reply is byte-identical to the old zeros "
              f"({len(_none)} field(s)): {'OK' if not _none else 'FAIL'}")
        ok &= not _none

        # ------------------------------------------------------------------ #
        # WARNING: PLAN 3.1: the NPC source records (FMO_UDP_POP_NPC), through the
        # real _serve_datagram on a LOBBY channel. Proves what we SEND: each
        # configured source id is popped as a cmd-7 record, once, on the lobby
        # channel and NOT on a battle channel; a source with no @pos falls back
        # to the self-POP position. The client half (the arm 0x61100100 deriving
        # a visible NPC) is read out of the binary and only a live /targetnpc
        # confirms it. (POP_NPC is declared global at the top of selftest().)
        _saved_np = (popsweep.POP, popself.POP_AFTER, npccast.POP_NPC, dict(groupchannel.WORLD_PEERS),
                     dict(rooms.WORLD_MAPS))
        _saved_npck = npcroster.POP_NPC_CLIENT_KIND
        _saved_npct = npcroster.POP_NPC_TARGETABLE
        try:
            from_reply2 = fmoworld.key_for_endpoint(served_ep, 1)
            lob_tables = fmoworld.bf_init(from_reply2)
            bt_tables2 = fmoworld.bf_init(battle_twin)
            sockn, LN, BN = _FakeSock(), ("192.0.2.11", 19155), \
                ("192.0.2.12", 19155)
            rooms.WORLD_MAPS[LN[0]], rooms.WORLD_MAPS[BN[0]] = 102, 418
            popsweep.POP, popself.POP_AFTER = (1, 4), 0
            npccast.POP_NPC = [(0x82001000, 4, (-20.0, 3.0, -30.0, 0.0), None),
                               (0x82080500, 4, None, None),
                               (0x820C1003, 4, (6.16, 2.99, 5.46, 0.0), 34)]
            npcroster.POP_NPC_CLIENT_KIND = 1      # the peer-less kind, pinned below
            npcroster.POP_NPC_TARGETABLE = True    # body+0x48 |= 0x10, pinned below

            def np_pump(tb, addr2, frm, to, ack):
                del sent[:]
                datagram._serve_datagram(sockn, groupchannel.WORLD_PEERS,
                                         fmoworld.build(*tb, peer=1, hid=udpconfig.UDP_HID,
                                                        kind=2, ack=ack, flag=2,
                                                        frm=frm, to=to), addr2)
                return [(c, b) for _t, d in sent
                        for _o, _s, c, b in
                        fmoworld.parse(*tb, d)["records"]]

            groupchannel.WORLD_PEERS.clear()
            # WARNING: Replies are sliced to the client's 1,400-B recvfrom buffer
            # (fit_records, 2026-09-05), so the self POP and the two sources
            # arrive over successive exchanges: pump, ack the slice that
            # came, pump again -- exactly what the peer does.
            recs = np_pump(lob_tables, LN, 0, 1, 0)
            for _k in range(1, 6):
                _last = fmoworld.parse(*lob_tables, sent[-1][1]) if sent else None
                if not _last or _last["to"] == _last["from"]:
                    break
                recs += np_pump(lob_tables, LN, _k, _k + 1, _last["to"])
            pop_ids = [struct.unpack_from("<I", b, fmoworld.POP_UNITID)[0]
                       for c, b in recs if c == fmoworld.CMD_POP]
            src_recs = {struct.unpack_from("<I", b, fmoworld.POP_UNITID)[0]: b
                        for c, b in recs if c == fmoworld.CMD_POP
                        and struct.unpack_from("<I", b, fmoworld.POP_UNITID)[0]
                        in (0x82001000, 0x82080500, 0x820C1003)}
            # both source ids present, alongside the self (id 1)
            lob_ok = (1 in pop_ids and 0x82001000 in src_recs
                      and 0x82080500 in src_recs)
            # the explicit @pos landed; the pos-less source took the self pos
            _selfpos = groupchannel.WORLD_PEERS[LN].pop_args["pos"]
            _p1 = struct.unpack_from("<4f", src_recs[0x82001000],
                                     fmoworld.POP_POS) if lob_ok else None
            _p2 = struct.unpack_from("<4f", src_recs[0x82080500],
                                     fmoworld.POP_POS) if lob_ok else None
            pos_ok = (lob_ok
                      and all(abs(a - b) < 1e-3 for a, b in
                              zip(_p1, (-20.0, 3.0, -30.0, 0.0)))
                      and all(abs(a - b) < 1e-3 for a, b in zip(_p2, _selfpos)))
            print(f"  npc source: FMO_UDP_POP_NPC pops each source id on the "
                  f"lobby channel ({sorted(hex(i) for i in src_recs)}), @pos "
                  f"honoured, pos-less falls back to self: "
                  f"{'OK' if lob_ok and pos_ok else 'FAIL'}")
            ok &= lob_ok and pos_ok
            # FMO_UDP_POP_NPC_CLIENT_KIND lands at body+0x00 of every SOURCE
            # record and leaves the SELF record's kind alone (0 = a peer).
            _self_rec = next((b for c, b in recs if c == fmoworld.CMD_POP
                              and struct.unpack_from("<I", b,
                                                     fmoworld.POP_UNITID)[0] == 1),
                             None)
            _ck_src = [struct.unpack_from("<I", b, fmoworld.POP_CLIENT_KIND)[0]
                       for b in src_recs.values()] if lob_ok else []
            _ck_self = (struct.unpack_from("<I", _self_rec,
                                           fmoworld.POP_CLIENT_KIND)[0]
                        if _self_rec is not None else None)
            ck_ok = bool(_ck_src) and all(k == 1 for k in _ck_src) and _ck_self == 0
            print(f"  npc source: FMO_UDP_POP_NPC_CLIENT_KIND=1 puts client_kind 1 "
                  f"(no peer) on every source record {_ck_src} and leaves the "
                  f"self at {_ck_self}: {'OK' if ck_ok else 'FAIL'}")
            ok &= ck_ok
            # FMO_UDP_POP_NPC_TARGETABLE sets bit 0x10 of body+0x48 on every
            # NPC record (the Select-target list gate) and never on the self.
            _tg_src = [b[fmoworld.POP_TARGETABLE] for b in src_recs.values()] \
                if lob_ok else []
            _tg_self = (_self_rec[fmoworld.POP_TARGETABLE]
                        if _self_rec is not None else None)
            # (bit 0x01 is the name tag, FMO_UDP_POP_NAMES, on every record)
            _nt = 0x01 if npcroster.POP_NAMES else 0
            tg_ok = (bool(_tg_src) and all(v & 0x10 for v in _tg_src)
                     and all((v & 0x01) == _nt for v in _tg_src)
                     and _tg_self == _nt)
            print(f"  npc source: FMO_UDP_POP_NPC_TARGETABLE=1 sets body+0x48 bit "
                  f"0x10 on every NPC record {[hex(v) for v in _tg_src]} and not on "
                  f"the self ({_tg_self}); the name-tag bit 0x01 is "
                  f"{'on' if npcroster.POP_NAMES else 'off'} for all: {'OK' if tg_ok else 'FAIL'}")
            ok &= tg_ok
            # `#34` dresses the record from the catalogue: names, face 51,
            # uniform 102, size 10, build 0, selector 1, NPC number 1 -- the
            # bytes the client's own E280 dresser writes for an OCU operator --
            # while the undressed sources keep the self's names.
            _cr = src_recs.get(0x820C1003) if lob_ok else None
            _cat = fmoworld.NPC_CATALOGUE[34]
            cat_ok = (_cr is not None
                      and _cr[fmoworld.POP_NAME1:fmoworld.POP_NAME1 + 4] == b"NPC1"
                      and _cr[fmoworld.POP_NAME2:fmoworld.POP_NAME2 + 8] == b"OPERATOR"
                      and struct.unpack_from("<H", _cr, fmoworld.POP_TYPE4_FACE)[0] == _cat[3] == 51
                      and struct.unpack_from("<H", _cr, fmoworld.POP_TYPE4_UNIFORM)[0] == _cat[4] == 102
                      and _cr[fmoworld.POP_TYPE4_SIZE] == _cat[5] == 10
                      and _cr[fmoworld.POP_TYPE4_BUILD] == _cat[6] == 0
                      and _cr[fmoworld.POP_TYPE4_MODEL] == _cat[2] == 1
                      and struct.unpack_from("<H", _cr, fmoworld.POP_NPC_NUMBER)[0] == _cat[7] == 1
                      and _cr[fmoworld.POP_UNITTYPE] == 4
                      and src_recs[0x82001000][fmoworld.POP_NAME1:fmoworld.POP_NAME1 + 4] != b"NPC1")
            print(f"  npc source: '#34' dresses 0x820C1003 from the NPC catalogue "
                  f"(NPC1/OPERATOR, face 51, uniform 102, size 10, selector 1) and "
                  f"leaves the undressed sources as player clones: "
                  f"{'OK' if cat_ok else 'FAIL'}")
            ok &= cat_ok
            # once per channel: ACK everything sent so far (so nothing
            # retransmits), then a further datagram queues no NEW source record.
            _ack_all = groupchannel.WORLD_PEERS[LN].tx_base + len(groupchannel.WORLD_PEERS[LN].pending)
            again = np_pump(lob_tables, LN, 1, 2, _ack_all)
            again_src = [c for c, b in again if c == fmoworld.CMD_POP
                         and struct.unpack_from("<I", b, fmoworld.POP_UNITID)[0]
                         in (0x82001000, 0x82080500)]
            print(f"  npc source: queued ONCE per channel (after ack, a 2nd "
                  f"datagram adds {len(again_src)}): "
                  f"{'OK' if not again_src else 'FAIL'}")
            ok &= not again_src
            # NOT on a battle channel
            groupchannel.WORLD_PEERS.clear()
            brecs = np_pump(bt_tables2, BN, 0, 1, 0)
            bsrc = [c for c, b in brecs if c == fmoworld.CMD_POP
                    and struct.unpack_from("<I", b, fmoworld.POP_UNITID)[0]
                    in (0x82001000, 0x82080500)]
            print(f"  npc source: NOT sent on a battle channel "
                  f"({len(bsrc)} source records): "
                  f"{'OK' if not bsrc else 'FAIL'}")
            ok &= not bsrc
        finally:
            popsweep.POP, popself.POP_AFTER, npccast.POP_NPC = _saved_np[0], _saved_np[1], _saved_np[2]
            npcroster.POP_NPC_CLIENT_KIND = _saved_npck
            npcroster.POP_NPC_TARGETABLE = _saved_npct
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_saved_np[3])
            rooms.WORLD_MAPS.clear()
            rooms.WORLD_MAPS.update(_saved_np[4])

    # --- player trade: the refusal arm, and the record layouts ----------------
    # The 0x0148 the tick polls for must be 452 bytes (0x6117F06F copies 0x71
    # dwords of the push, and the parser reads up to +0x1B2 + a name), the
    # state byte must sit at +0x1A0 (0x6119EEDA), and a refusal must be one of
    # the two values the tick treats as terminal (0x6119F16B: 5 or 6).
    rec = trade.trade_state_record(trade.TRADE_REFUSE_STATE, "Lex", "Arden", partner_id=7)
    good = (len(rec) == trade.TRADE_STATE_LEN and rec[trade.TRADE_STATE_OFF] in (5, 6)
            and rec[trade.TRADE_REPLY_FIRST:trade.TRADE_REPLY_FIRST + 4] == b"Lex\0"
            and rec[trade.TRADE_LAST:trade.TRADE_LAST + 6] == b"Arden\0"
            and struct.unpack_from("<I", rec, 0x198)[0] == 7)
    print(f"  trade: 0x0148 refusal is {len(rec)}B, state +0x1A0 = "
          f"{rec[trade.TRADE_STATE_OFF]} (terminal), names at +0x1A1/+0x1B2: "
          f"{'OK' if good else 'FAIL'}")
    ok &= good
    # Every byte we did not decode is zero -- the safety argument again.
    others = bytearray(rec)
    for off, ln in ((trade.TRADE_STATE_OFF, 1), (trade.TRADE_REPLY_FIRST, 4),
                    (trade.TRADE_LAST, 6), (0x198, 4)):
        others[off:off + ln] = bytes(ln)
    print(f"  trade: every undecoded byte of the refusal is zero: "
          f"{'OK' if others == bytes(len(others)) else 'FAIL'}")
    ok &= others == bytes(len(others))

    # The push record: mode at +0x1A0, names at +0x1A5 / +0x1B2 (0x6117F207's
    # lea esp+0x1CD / esp+0x1DA against the esp+0x28 copy), and a mode outside
    # 0x6117F07B's three arms is refused rather than sent.
    push = trade.trade_push_record(0, "Lex", "Arden")
    pgood = (len(push) == trade.TRADE_STATE_LEN and push[trade.TRADE_STATE_OFF] == 0
             and push[trade.TRADE_PUSH_FIRST:trade.TRADE_PUSH_FIRST + 4] == b"Lex\0"
             and push[trade.TRADE_LAST:trade.TRADE_LAST + 6] == b"Arden\0")
    try:
        trade.trade_push_record(3)
        pgood = False
    except ValueError:
        pass
    print(f"  trade: 0x017D push record, mode 0 at +0x1A0, names at "
          f"+0x1A5/+0x1B2, mode 3 refused: {'OK' if pgood else 'FAIL'}")
    ok &= pgood
    # The push id must be one the lobby dispatcher will even look at.
    print(f"  trade: 0x017D is in the dispatcher's window: "
          f"{'OK' if trade.MSG_TRADE_PUSH in pushes.LOBBY_PUSH_IDS else 'FAIL'}")
    ok &= trade.MSG_TRADE_PUSH in pushes.LOBBY_PUSH_IDS

    # The handler: an offer, a poll and an update are each answered with ONE
    # 0x0148 echoing the request's sequence; the dtor's queue-borne leave (4
    # zero bytes, or the fixed queue sequence) is answered with NOTHING.
    s = session.Session("selftest:0")
    answered = []
    for m, pl, seq in ((trade.MSG_TRADE_OFFER, struct.pack("<I", 0x200), 0x1234),
                       (trade.MSG_TRADE_POLL, b"", 0x1235),
                       (trade.MSG_TRADE_UPDATE, bytes(0xD0), 0x1236)):
        outs = s.on_packet(packet.parse(packet.build(m, pl, seq=seq, conn_id=1)))
        q = [packet.parse(o) for o in outs]
        answered.append(len(q) == 1 and q[0]["msg"] == trade.MSG_TRADE_STATE
                        and q[0]["seq"] == seq
                        and len(q[0]["payload"]) == trade.TRADE_STATE_LEN
                        and q[0]["payload"][trade.TRADE_STATE_OFF]
                        == trade.TRADE_REFUSE_STATE)
    leave = s.on_packet(packet.parse(packet.build(trade.MSG_TRADE_OFFER, bytes(4),
                                                  seq=pushes.QUEUE_SEQ, conn_id=1)))
    leave2 = s.on_packet(packet.parse(packet.build(trade.MSG_TRADE_OFFER, struct.pack("<I", 5),
                                                   seq=pushes.QUEUE_SEQ, conn_id=1)))
    hgood = all(answered) and not leave and not leave2
    print(f"  trade: 0x0146/0x0147/0x0144 each get one 0x0148 on their own "
          f"seq, the queue-borne leave gets nothing: {'OK' if hgood else 'FAIL'}")
    ok &= hgood
    print(f"  trade: FMO_TRADE defaults OFF (refusal only): "
          f"{'OK' if not trade.TRADE or os.environ.get('FMO_TRADE') else 'FAIL'}")
    ok &= (not trade.TRADE) or bool(os.environ.get("FMO_TRADE"))

    # THE TRADE SERVICE, two pilots end to end (no store, no sockets).
    def _trade_blk(items=(), money=0):
        b = bytearray(trade.TRADE_BLOCK_LEN)
        for i, (serial, iid, kind) in enumerate(items):
            b[trade.TRADE_SLOT + i * inventory.INV_ENTRY_LEN:trade.TRADE_SLOT + (i + 1) * inventory.INV_ENTRY_LEN] = \
                inventory.item_record(serial, iid, kind)
        struct.pack_into("<I", b, trade.TRADE_MONEY, money)
        return bytes(b)

    def _trade_pilot(ip, char):
        _ps = session.Session(ip + ":1")
        _ps.playing_char = lambda: char
        _ps.commit = lambda what: None
        _ps.trade_names = lambda ip=None: (("Bea", "Bee") if ip == "tB"
                                           else ("Al", "Ay"))
        _ps.credit_money = (lambda why, money=0, contribution=0:
                            char.__setitem__("money", char["money"] + money))
        return _ps

    def _tq(sess, msg, pl=b"", seq=0x100):
        o = sess.on_packet(packet.parse(packet.build(msg, pl, seq=seq, conn_id=1)))
        q = [packet.parse(x) for x in o]
        return q[0]["payload"][trade.TRADE_STATE_OFF] if q else None

    _gt = flat_globals()
    _saved_t = {k: _gt[k] for k in ("TRADE", "CHAR_STORE", "trade_host_of_alias")}
    _tr_ok = True
    _tfail = []

    def _tc(n, v):
        if not v:
            _tfail.append(n)
        return bool(v)
    try:
        _gt.update(TRADE=True, CHAR_STORE="selftest",
                  trade_host_of_alias=lambda ip, alias: "tB" if alias == 0x200 else None)
        trade.TRADES.clear(); trade.LIVE_SESSIONS.clear()
        _cA = {"money": 1000, "items": [{"serial": 0x11, "id": 7, "kind": 0x12},
                                        {"serial": 0x12, "id": 8, "kind": 0x12}]}
        _cB = {"money": 50, "items": [{"serial": 0x12, "id": 9, "kind": 0x22}]}
        sA, sB = _trade_pilot("tA", _cA), _trade_pilot("tB", _cB)
        trade.LIVE_SESSIONS.update(tA=sA, tB=sB)
        _tr_ok &= _tc(1, _tq(sA, trade.MSG_TRADE_START, struct.pack("<I", 0x999) + bytes(36)) == 6)
        _tr_ok &= _tc(2, _tq(sA, trade.MSG_TRADE_START, struct.pack("<I", 0x200) + bytes(36)) == 1)
        _push = sB.trade_pushes_due(1)
        _tr_ok &= _tc(3, (len(_push) == 1 and packet.parse(_push[0])["msg"] == trade.MSG_TRADE_PUSH
                   and packet.parse(_push[0])["payload"][trade.TRADE_STATE_OFF] == 0))
        _tr_ok &= _tc(4, _tq(sA, trade.MSG_TRADE_POLL) == 1)
        _tr_ok &= _tc(5, _tq(sB, trade.MSG_TRADE_ACCEPT, bytes(4)) == 3)
        _tr_ok &= _tc(6, _tq(sA, trade.MSG_TRADE_POLL) == 3)
        # A offers item 0x11 + H$ 300 and one item it does NOT own (cleared);
        # B offers its 0x12 (a serial A KEEPS -> re-minted on A's side)
        _ga = _trade_blk([(0x11, 7, 0x12), (0x99, 1, 0x12)], 300)
        _gb = _trade_blk([(0x12, 9, 0x22)], 0)
        _tq(sA, trade.MSG_TRADE_UPDATE, _ga)
        _tq(sB, trade.MSG_TRADE_UPDATE, _gb)
        _t = trade.TRADES["tA"]
        _tr_ok &= _tc(7, len(trade.trade_block_slots(_t["blocks"]["tA"])) == 1)
        # a stale OK (the client's pair differs) is NOT an OK
        _tq(sA, trade.MSG_TRADE_OK, _ga + _gb)
        _tr_ok &= _tc(8, _t["ok"]["tA"] is False)
        _ca, _cb = _t["blocks"]["tA"], _t["blocks"]["tB"]
        _tr_ok &= _tc(9, _tq(sA, trade.MSG_TRADE_OK, _ca + _cb) == 3 and _t["ok"]["tA"])
        # a new update clears both OKs
        _tq(sB, trade.MSG_TRADE_UPDATE, _gb)
        _tr_ok &= _tc(10, _t["ok"] == {"tA": False, "tB": False})
        _tq(sA, trade.MSG_TRADE_OK, _ca + _t["blocks"]["tB"])
        _tr_ok &= _tc(11, _tq(sB, trade.MSG_TRADE_OK, _t["blocks"]["tB"] + _t["blocks"]["tA"]) == 5)
        _tr_ok &= _tc(12, _tq(sA, trade.MSG_TRADE_POLL) == 5)
        _serA = sorted(it["serial"] for it in inventory.stored_items(_cA))
        _serB = sorted(it["serial"] for it in inventory.stored_items(_cB))
        _tr_ok &= _tc(13, (_cA["money"] == 700 and _cB["money"] == 350
                   and len(_serA) == 2 and 0x12 in _serA and 0x11 not in _serA
                   and [it["id"] for it in inventory.stored_items(_cA)
                        if it["serial"] not in (0x11, 0x12)] == [9]
                   and _serB == [0x11]
                   and [it["id"] for it in inventory.stored_items(_cB)] == [7]))
        _pa = sA.trade_pushes_due(1)
        _tr_ok &= _tc(14, (len(_pa) == 1
                   and packet.parse(_pa[0])["payload"][trade.TRADE_STATE_OFF] == 2
                   and trade.trade_block_money(packet.parse(_pa[0])["payload"][:trade.TRADE_BLOCK_LEN]) == 300))
        _tr_ok &= _tc(15, "tA" not in trade.TRADES and "tB" not in trade.TRADES)
        # an offer cancelled before B joins tells B (0x017D mode 1)
        _tq(sA, trade.MSG_TRADE_START, struct.pack("<I", 0x200) + bytes(36))
        sB.trade_pushes_due(1)
        _tr_ok &= _tc(16, _tq(sA, trade.MSG_TRADE_OFFER, bytes(4)) == 6)
        _pb = sB.trade_pushes_due(1)
        _tr_ok &= _tc(17, (len(_pb) == 1
                   and packet.parse(_pb[0])["payload"][trade.TRADE_STATE_OFF] == 1))
        # an equipped item cannot be offered
        _eq = bytearray(inventory.REPLY_0166_LEN)
        _eq[inventory.SETUP_IN_USE] = 1
        _eq[inventory.SETUP_ITEM_OFF:inventory.SETUP_ITEM_OFF + inventory.INV_ENTRY_LEN] = inventory.item_record(0x12, 8, 0x12)
        _cE = {"items": [{"serial": 0x12, "id": 8, "kind": 0x12}],
               "setups": bytes(_eq).hex()}
        _tr_ok &= _tc(18, trade.trade_validate(_cE, _trade_blk([(0x12, 8, 0x12)]), 0)[1] != [])
    except Exception as _e:
        print(f"  trade service: EXC {_e!r}")
        _tr_ok = False
    finally:
        _gt.update(_saved_t)
        trade.TRADES.clear(); trade.LIVE_SESSIONS.clear()
    print(f"  trade service: offer -> push mode 0 -> accept -> updates (store-"
          f"checked) -> a stale OK refused, an update clears OKs -> both OK -> "
          f"items + H$ move once, a duplicate serial re-minted, mode 2 pushed; "
          f"cancel before join pushes mode 1; equipped refused: "
          f"{'OK' if _tr_ok else 'FAIL at step(s) ' + str(_tfail)}")
    ok &= _tr_ok

    # 0x019A CITY-TABLE push (the City Control screen's data block). The layout
    # is pinned: 420-byte block at payload+0x10, u32 count, 8-byte rows anchored
    # so the city id lands at lobby+0x6C4C.
    cbytes = citytable.city_table_payload([(505, 85102), (509, 94099)])
    clayout = (len(cbytes) == citytable.CITY_TABLE_LEN
               and struct.unpack_from("<I", cbytes, citytable.CITY_TABLE_OFF)[0] == 2
               and struct.unpack_from("<HIH", cbytes, citytable.CITY_ROW_OFF) == (505, 903_085_102, 0)
               and struct.unpack_from("<HIH", cbytes, citytable.CITY_ROW_OFF + citytable.CITY_ROW_LEN) == (509, 903_094_099, 0)
               # the RANK column: entry+1 from lobby+0x6C4A = Maltaf's points
               and struct.unpack_from("<BBHI", cbytes, citytable.CITY_ENTRY_OFF)
               == (0, citytable.city_rank(85102), 505, 903_085_102)
               and (fmowar is None or citytable.city_rank(85102) == 4)
               and citytable.city_rank(94099) == 0
               and citytable.CITY_ENTRY_OFF + citytable.CITY_ROW_MAX * citytable.CITY_ROW_LEN <= citytable.CITY_TABLE_LEN
               and citytable.parse_city_table(["505:85102", "0x1FD:94099"]) == [(505, 85102), (509, 94099)]
               # the row base is lobby+0x6C4C (0x610E8130 buckets on word[+0x6C4C+i*8])
               and citytable.CITY_ROW_OFF == citytable.CITY_TABLE_OFF + (0x6C4C - 0x6C36)
               and (not fmowar or (len(fmowar.city_rows()) == 19
                                   and fmowar.city_rows()[0] == (505, 85102))))
    print(f"  city: 0x{citytable.MSG_CITY_TABLE:04X} payload = {citytable.CITY_TABLE_LEN}B, count at "
          f"+0x{citytable.CITY_TABLE_OFF:X}, rows {{u16 selector, u32 903e6+tile, u16 0}} at "
          f"+0x{citytable.CITY_ROW_OFF:X}; the model offers SE's 19 cities: "
          f"{'OK' if clayout else 'FAIL'}")
    ok &= clayout

    # --- 0x016C ZONE-CONTROL push (the Change Area predicate's table) ------- #
    # Every constant here is the client's own arithmetic, so these assertions
    # can be made rather than probed: the arm 0x6117F013 copies `rep movsd 0x84`
    # (528 B) from packet+0x24 = payload+0x10 into lobby+0x7724, and the walker
    # 0x611A3AA0 reads a u32 count at T+0x0C and 4-byte entries from T+0x10.
    zconst = (zonecontrol.ZONE_BLOCK == 528 and zonecontrol.ZONE_CONTROL_LEN == 544
              and zonecontrol.ZONE_COUNT_OFF == 0x1C and zonecontrol.ZONE_ROW_OFF == 0x20
              and zonecontrol.ZONE_ROW_MAX == 128)
    print(f"  zone: 0x{zonecontrol.MSG_ZONE_CONTROL:04X} payload = {zonecontrol.ZONE_CONTROL_LEN}B, "
          f"{zonecontrol.ZONE_BLOCK}B block at +0x{zonecontrol.ZONE_BLOCK_OFF:X}, count at "
          f"+0x{zonecontrol.ZONE_COUNT_OFF:X}, rows at +0x{zonecontrol.ZONE_ROW_OFF:X}, max "
          f"{zonecontrol.ZONE_ROW_MAX}: {'OK' if zconst else 'FAIL'}")
    ok &= zconst
    # The row is {s16 zoneId; u8 ocu; u8 usn} -- the byte the walker picks is
    # +2 for nation 1 and +3 for nation 2, so the two must not be transposed.
    zbytes = zonecontrol.zone_control_payload([(505, 1, 2), (509, 3, 4)])
    zlayout = (len(zbytes) == zonecontrol.ZONE_CONTROL_LEN
               and struct.unpack_from("<I", zbytes, zonecontrol.ZONE_COUNT_OFF)[0] == 2
               and struct.unpack_from("<HBB", zbytes, zonecontrol.ZONE_ROW_OFF)
               == (505, 1, 2)
               and struct.unpack_from("<HBB", zbytes, zonecontrol.ZONE_ROW_OFF
                                      + zonecontrol.ZONE_ROW_LEN) == (509, 3, 4)
               # payload+0x00 -> lobby+0x7DEB is write-only; serve zero.
               and zbytes[zonecontrol.ZONE_HEAD_OFF] == 0)
    print(f"  zone: row {{s16 id, u8 ocu, u8 usn}} lands where 0x611A3AA0 reads "
          f"it (+2 = nation 1, +3 = nation 2): "
          f"{'OK' if zlayout else 'FAIL'}")
    ok &= zlayout
    # The spec: usn falls back to ocu, ocu falls back to 1, and `live` is the
    # three warzones gate (A) leaves selectable.
    zparse = (zonecontrol.parse_zone_control(["509"]) == [(509, 1, 1)]
              and zonecontrol.parse_zone_control(["509:2"]) == [(509, 2, 2)]
              and zonecontrol.parse_zone_control(["509:2:3"]) == [(509, 2, 3)]
              and zonecontrol.parse_zone_control(["live"])
              == [(z, 1, 1) for z in zonecontrol.ZONE_LIVE_WARZONES])
    print(f"  zone: `id[:ocu[:usn]]` defaults and the `live` expansion "
          f"{list(zonecontrol.ZONE_LIVE_WARZONES)}: {'OK' if zparse else 'FAIL'}")
    ok &= zparse
    # WARNING: A count the block cannot hold would make the client walk past the end
    # of its own 528 bytes, so the cap is enforced in the parser, not trusted.
    zcap = (len(zonecontrol.parse_zone_control([str(600 + i) for i in range(200)]))
            == zonecontrol.ZONE_ROW_MAX
            and struct.unpack_from("<I", zonecontrol.zone_control_payload(
                zonecontrol.parse_zone_control([str(i) for i in range(200)])),
                zonecontrol.ZONE_COUNT_OFF)[0] == zonecontrol.ZONE_ROW_MAX)
    print(f"  zone: more rows than the block holds are capped at "
          f"{zonecontrol.ZONE_ROW_MAX}, count included: {'OK' if zcap else 'FAIL'}")
    ok &= zcap
    # Default OFF, and empty reads as unset rather than "serve zero rows"
    # (the empty-env trap: a `:-` compose default handing "" to int()).
    zoff = (zonecontrol.zone_control_push(0) is None) if not zonecontrol.ZONE_CONTROL else True
    print(f"  zone: FMO_ZONE_CONTROL defaults OFF (no push at all): "
          f"{'OK' if zoff else 'FAIL'}")
    ok &= zoff

    # ----------------------------------------------------------------- #
    # 0x016A -- the shop availability bitmap
    # ----------------------------------------------------------------- #
    # KEY: THE ORACLE IS THE CLIENT'S OWN WALK. This is 0x61174D00 transcribed:
    # section kind at +2 (0 ends it), signed delta at +0, bit (id & 15) of word
    # (id >> 4) at +4. It returns the client's three answers -- 1 available,
    # 0 walked off the end, 2 no block -- and it REFUSES to read outside the
    # buffer, which is the failure that would land on the player's screen as
    # a crash rather than an empty list.
    def _client_gate(block, kind, ident):
        if block is None:
            return 2
        off = 0
        while True:
            if off < 0 or off + 3 > len(block):
                raise AssertionError(f"walked outside the block at {off}")
            k = block[off + 2]
            if k == 0:
                return 0
            if k == kind:
                w = off + partsstock.PARTS_STOCK_HDR + (ident >> 4) * 2
                if w + 2 > len(block):
                    raise AssertionError(
                        f"kind {kind:#04x} id {ident} indexes {w}..{w + 1} "
                        f"past the {len(block)}-byte block")
                return 1 if (struct.unpack_from("<H", block, w)[0]
                             >> (ident & 15)) & 1 else 0
            d = struct.unpack_from("<h", block, off)[0]
            if d <= 0:
                raise AssertionError(f"non-advancing delta {d} at {off}")
            off += d

    pstock = partsstock.parse_parts_stock("0x11:1-3+7,0x13:2")
    pblk = partsstock.parts_stock_block(pstock)
    pgate = (_client_gate(pblk, 0x11, 1) == 1
             and _client_gate(pblk, 0x11, 3) == 1
             and _client_gate(pblk, 0x11, 7) == 1
             and _client_gate(pblk, 0x11, 4) == 0
             and _client_gate(pblk, 0x13, 2) == 1
             and _client_gate(pblk, 0x13, 1) == 0
             # a kind with no section falls off the end of the walk
             and _client_gate(pblk, 0x41, 1) == 0
             # and with no block at all the client refuses everything: the
             # state we have shipped since the shop existed
             and _client_gate(None, 0x11, 1) == 2)
    print(f"  0x016A: the client's own walk (0x61174D00) finds every id we set "
          f"and no id we did not: {'OK' if pgate else 'FAIL'}")
    ok &= pgate
    # WARNING: THE ONE THAT CAN CRASH A CLIENT. The list loop asks for id = 1..count
    # for every kind, so each section must cover its whole table -- otherwise
    # the last section's bitmap is indexed past the malloc'd block.
    pall = partsstock.parse_parts_stock("all")
    pallblk = partsstock.parts_stock_block(pall)
    try:
        pcover = all(_client_gate(pallblk, k, i) == 1
                     for k, n in partsstock.PART_TABLE_COUNT.items()
                     for i in (1, n >> 1, n))
    except AssertionError:
        pcover = False
    print(f"  0x016A: every section covers id 1..count for its kind, so the "
          f"client cannot index past the block: {'OK' if pcover else 'FAIL'}")
    ok &= pcover
    # The arm mallocs payload+0x00 and copies from payload+0x10; a length that
    # disagrees with the bytes is a heap overflow in somebody else's process.
    ppay = partsstock.parts_stock_payload(pstock)
    plen = (struct.unpack_from("<I", ppay, 0)[0]
            == len(ppay) - partsstock.PARTS_STOCK_BODY_OFF == len(pblk)
            and ppay[partsstock.PARTS_STOCK_BODY_OFF:] == pblk)
    print(f"  0x016A: payload+0x00 length == the bytes at +0x{partsstock.PARTS_STOCK_BODY_OFF:X} "
          f"({len(pblk)}B), which is what 0x6117EC6D mallocs: "
          f"{'OK' if plen else 'FAIL'}")
    ok &= plen
    # Default OFF, and a bad spec must be refused rather than served as "".
    poff = (partsstock.PARTS_STOCK is None and partsstock.parts_stock_push(0) is None
            if not partsstock.PARTS_STOCK_SPEC else True)
    pbad = 0
    for _spec in ("0x99", "0x11:0", "0x11:9999", "nonsense", "0x11:1-x"):
        try:
            partsstock.parse_parts_stock(_spec)
        except ValueError:
            pbad += 1
    print(f"  0x016A: FMO_PARTS_STOCK defaults OFF (no push) and refuses a "
          f"bad kind/id ({pbad}/5): {'OK' if poff and pbad == 5 else 'FAIL'}")
    ok &= poff and pbad == 5
    # THE PHASE VICTORY REWARD (guide/phase, topics060306): the winner's shop
    # sells the enemy series -- O.C.U. Igel/Grille (181..184), U.S.N. Tiran
    # I..IV (176..179) in body/arms/legs -- both on a tie, before a win never,
    # and it is derived from the stored phases so it persists.
    _vu = partsstock.victory_unlocked
    _vf = partsstock.parts_stock_for
    _vbase = partsstock.parse_parts_stock("0x11:176-184,0x21:1,0x13")
    _v_ocu_win = _vf(_vbase, 1, _vu({"1": {"winner": 1}}))
    _v_ocu_pre = _vf(_vbase, 1, _vu({}))
    _v_usn_after_ocu = _vf(_vbase, 2, _vu({"1": {"winner": 1}}))
    _v_tie = _vu({"1": {"winner": 0}})
    _v_later = _vu({"1": {"winner": 1}, "2": {"winner": 2}})
    _vic_ok = (all({181, 182, 183, 184} <= _v_ocu_win[k] for k in (0x11, 0x21, 0x31))
               and not ({181, 182, 183, 184} & _v_ocu_pre[0x11])
               and 0x31 not in _v_ocu_pre
               # U.S.N. did not win: Tiran stays off ITS shop, and O.C.U.'s win
               # does not touch it
               and not ({176, 177, 178, 179} & _v_usn_after_ocu[0x11])
               and {181, 182, 183, 184} <= _v_usn_after_ocu[0x11]
               and _v_tie == {1, 2} and _v_later == {1, 2}
               and _v_ocu_win[0x13] == _vbase[0x13])
    _sv_vic = (partsstock.PARTS_STOCK, warstate.war_state, warstate.WAR)
    try:
        partsstock.PARTS_STOCK = _vbase
        warstate.WAR = "1"
        warstate.war_state = lambda: type("W", (), {"data": {"phases": {
            "1": {"winner": 2, "ocu": 3, "usn": 9}}}})()
        _vs2 = partsstock.victory_stock(2)[0]
        _vs1 = partsstock.victory_stock(1)[0]
        _vic_ok = (_vic_ok and {176, 177, 178, 179} <= _vs2[0x31]
                   and not ({181, 182, 183, 184} & _vs1[0x11]))
    finally:
        partsstock.PARTS_STOCK, warstate.war_state, warstate.WAR = _sv_vic
    print(f"  0x016A: a phase WIN puts the enemy series in the winner's shop "
          f"(O.C.U. Igel/Grille 181..184, U.S.N. Tiran 176..179, body/arms/legs), "
          f"both on a tie, never before, read from the stored phases: "
          f"{'OK' if _vic_ok else 'FAIL'}")
    ok &= _vic_ok

    # A minted serial must never share its LOW dword with a garage serial: the
    # first purchase after every restart used to mint 1, which an equipped part
    # already held (live 2026-09-11, and the client died a second later).
    _mints = [shop.mint_serial() for _ in range(4)]
    _inv = inventory.inventory_from_setups(inventory.reply_0166(parts=inventory.STARTER_SETUPS[(1, 3)],
                                                                slots=1))
    _garage_lo = {s & 0xFFFFFFFF for s, _i, _k in _inv} if _inv and \
        isinstance(_inv[0], tuple) else set(range(1, 32))
    _ser = (all(lo >= shop.SERIAL_LOW_BASE for lo, _h in _mints)
            and not ({lo for lo, _h in _mints} & _garage_lo)
            and len({lo for lo, _h in _mints}) == len(_mints))
    print(f"  item serials: a mint is >= {shop.SERIAL_LOW_BASE:#x} and shares no low "
          f"dword with the garage's own {min(_garage_lo)}..{max(_garage_lo)}: "
          f"{'OK' if _ser else 'FAIL'}")
    ok &= _ser

    # ----------------------------------------------------------------- #
    # 0x01A3 -- the cosmetic catalogue
    # ----------------------------------------------------------------- #
    # The catalogue ships as a file; without it every assertion below is
    # vacuous, so say so rather than printing OK against an empty dict.
    cload = bool(cosmetics.COSMETICS) and all(
        k in cosmetics.COSMETICS for k in (0, 1, 2, 3, 4))
    _cs_skip = _fmodata_skip("fmo-cosmetics.tsv")
    print(f"  0x01A3: fmo-cosmetics.tsv loads, 5 kinds, "
          f"{sum(len(v) for v in cosmetics.COSMETICS.values())} rows: "
          f"{_cs_skip or ('OK' if cload else 'FAIL')}")
    if not _cs_skip:
        ok &= cload
    if cload:
        # The screen byte picks the tables, and it is the ONLY thing that does.
        # Measured live 2026-09-11: the locker sends 1, the console sends 2.
        cpk = {k for k, _i, _e in cosmetics.cosmetics_for(cosmetics.A2_PILOT, 1)}
        cwk = {k for k, _i, _e in cosmetics.cosmetics_for(cosmetics.A2_WANZER, 1)}
        csplit = (cpk == {0, 1} and cwk == {2, 3, 4}
                  and cosmetics.cosmetics_for(99, 1) == [])
        print(f"  0x01A3: byte 1 -> kinds {sorted(cpk)} (pilot), byte 2 -> "
              f"{sorted(cwk)} (wanzer), an unknown byte -> nothing: "
              f"{'OK' if csplit else 'FAIL'}")
        ok &= csplit
        # A nation sees its own rows and the neutral ones, never the other
        # nation's -- the client's own insignia rule (0x611BE344).
        n1 = cosmetics.cosmetics_for(cosmetics.A2_PILOT, 1)
        n2 = cosmetics.cosmetics_for(cosmetics.A2_PILOT, 2)
        byid = {(k, i): f for k, v in cosmetics.COSMETICS.items() for i, f, _e in
                [(i, f, e) for i, f, e in v]}
        cnat = (n1 != n2
                and all(byid[(k, i)] in (0, 1) for k, i, _e in n1)
                and all(byid[(k, i)] in (0, 2) for k, i, _e in n2)
                and any(byid[(k, i)] == 0 for k, i, _e in n1))
        print(f"  0x01A3: nation 1 gets {len(n1)} rows, nation 2 {len(n2)}, "
              f"neither sees the other's: {'OK' if cnat else 'FAIL'}")
        ok &= cnat
        # The body has to tile where 0x61172950 reads it, and NEVER exceed the
        # 1,800 entries its rep movsd copies.
        need = lobapi.LOBAPI[0x01A2][1]
        cb = cosmetics.reply_01a3(need, b"\x01", 1)
        cnt = struct.unpack_from("<I", cb, cosmetics.A3_COUNT_OFF)[0]
        first = struct.unpack_from("<HBBI", cb, cosmetics.A3_ROW_OFF)
        clay = (len(cb) == need and cnt == len(n1) and cnt <= cosmetics.A3_ROW_MAX
                and first == (n1[0][1], n1[0][0], 0, cosmetics.COSMETIC_PRICE)
                and cosmetics.A3_ROW_OFF + cnt * cosmetics.A3_ROW_LEN <= need)
        print(f"  0x01A3: {need}B body, count {cnt} at +0x{cosmetics.A3_COUNT_OFF:X}, "
              f"first row {{id {first[0]}, kind {first[1]}, price {first[3]}}} "
              f"at +0x{cosmetics.A3_ROW_OFF:X}, all within {cosmetics.A3_ROW_MAX} entries: "
              f"{'OK' if clay else 'FAIL'}")
        ok &= clay
        # And the A/B: FMO_COSMETIC_SHOP=0 must give back the exact zeros we
        # shipped before, byte for byte.
        _keep = cosmetics.COSMETIC_SHOP
        cosmetics.COSMETIC_SHOP = False
        coff = (cosmetics.reply_01a3(need, b"\x01", 1) is None
                and lobapi.lobapi_payload(0x01A2, need, b"\x01") == bytes(need))
        cosmetics.COSMETIC_SHOP = _keep
        print(f"  0x01A3: FMO_COSMETIC_SHOP=0 restores the {need} zeros "
              f"byte for byte: {'OK' if coff else 'FAIL'}")
        ok &= coff
    # WARNING: The push is inert without nation 1 or 2 -- 0x611A3AA0 returns -1 for
    # anything else. The warning must fire on 0 and stay quiet on 1/2.
    znat = bool(zonecontrol.zone_control_nation_warning()) == (status.STATUS_NATION not in (1, 2))
    print(f"  zone: the nation-pairing warning tracks FMO_STATUS_NATION "
          f"(now {status.STATUS_NATION}): {'OK' if znat else 'FAIL'}")
    ok &= znat
    # --- the resume triad -------------------------------------------------- #
    # These three land in the block at the offsets the branch reads, and the
    # default must be zero or every ordinary login takes the resume path.
    _rb = status.reply_014a(rank=21, w7604=0x12345, w7608=7, wfd4=30000)
    _tri_ok = (struct.unpack_from("<I", _rb, status.S14A_W7604)[0] == 0x12345
               and struct.unpack_from("<I", _rb, status.S14A_W7608)[0] == 7
               and struct.unpack_from("<I", _rb, status.S14A_BLOCK3)[0] == 30000)
    print(f"  resume: +0x684/+0x688/+0x70C land at the offsets the branch "
          f"reads: {'OK' if _tri_ok else 'FAIL'}")
    ok &= _tri_ok
    # The resource path arithmetic is what the log promises the operator.
    _path_ok = (resume.resume_resource_path(0) == "data\\AI\\F21\\D59.DAT"
                and resume.resume_resource_path(40) == "data\\AI\\F21\\D99.DAT")
    print(f"  resume: selector 0 and 40 resolve to D59/D99 of the 41 that "
          f"exist: {'OK' if _path_ok else 'FAIL'}")
    ok &= _path_ok
    if not any(os.environ.get(k) for k in ("FMO_STATUS_W7604",
                                           "FMO_STATUS_W7608",
                                           "FMO_STATUS_WFD4",
                                           "FMO_RESUME_RES_INDEX")):
        _quiet = (status.STATUS_W7604 == 0 and status.STATUS_W7608 == 0 and status.STATUS_WFD4 == 0
                  and status_block_zero_or_knobbed())
        print(f"  resume: unset -> all three zero, so world entry is the "
              f"normal path"
              + ("" if status_block_pristine()
                 else " (the whole-block zero check is SKIPPED: another "
                      "FMO_STATUS_* knob is set)")
              + f": {'OK' if _quiet else 'FAIL'}")
        ok &= _quiet

    # --- FMO_STATUS_MARK --------------------------------------------------- #
    # The skips are the safety property, so assert them rather than the happy
    # path alone: a set bit in the owned table claims a part, the tail is
    # echoed back in 0x0170, and a marker byte on NATION would change the
    # scene script the probe is trying to observe.
    _sm = status.status_mark_body(1000)
    _len_ok = len(_sm) == status.REPLY_014A_LEN
    _val_ok = (struct.unpack_from("<I", _sm, 0)[0] == 1000
               and struct.unpack_from("<I", _sm, 0x20)[0] == 1000 + 8)
    print(f"  statusmark: 2060B, dword i = base+i outside the skips: "
          f"{'OK' if _len_ok and _val_ok else 'FAIL'}")
    ok &= _len_ok and _val_ok
    _skips_ok = all(_sm[lo:hi] == bytes(hi - lo) for lo, hi in status.STATUS_MARK_SKIP)
    print(f"  statusmark: owned-items/resource-id and 0x70C tail ranges stay "
          f"ZERO: {'OK' if _skips_ok else 'FAIL'}")
    ok &= _skips_ok
    _zero_ok = all(_sm[o] == 0 for o in status.STATUS_MARK_ZERO)
    print(f"  statusmark: nation/sex/rank/tier forced to 0 (the scene-script "
          f"hazard): {'OK' if _zero_ok else 'FAIL'}")
    ok &= _zero_ok
    # A served knob must still win over a marker, or the probe silently
    # replaces the very fields we already proved on screen.
    _marked_rank = bytearray(status.status_mark_body(1000))
    for _l, _o, _raw, _s in status.status_fields(rank=21):
        _marked_rank[_o:_o + len(_raw)] = _raw
    _wins = _marked_rank[status.S14A_RANK] == 21
    print(f"  statusmark: an explicitly served field still wins over the "
          f"marker: {'OK' if _wins else 'FAIL'}")
    ok &= _wins
    if not os.environ.get("FMO_STATUS_MARK"):
        _off_ok = status.STATUS_MARK == 0 and status_block_zero_or_knobbed()
        print(f"  statusmark: unset -> the payload is zeros, byte for byte"
              + ("" if status_block_pristine()
                 else " (SKIPPED: another FMO_STATUS_* knob is filling the "
                      "block on purpose)")
              + f": {'OK' if _off_ok else 'FAIL'}")
        ok &= _off_ok

    # --- FMO_ACCOUNT_PIN --------------------------------------------------- #
    # The pin exists because the freshest-session heuristic picked the wrong
    # member on a two-account box and the client then offered Create Character
    # to a player who had one. Pin the SET path (bare id, full key, several
    # entries, whitespace) and the refusals, because an unset knob proves
    # nothing -- the FMO_LOBAPI_MARK import-order bug shipped exactly that way.
    _pin_ok = (identity.parse_account_pin("127.0.0.1=3") == {"127.0.0.1": "member:3"}
               and identity.parse_account_pin("1.2.3.4=member:9") == {"1.2.3.4": "member:9"}
               and identity.parse_account_pin(" 1.2.3.4 = 9 , 5.6.7.8 = member:2 ")
               == {"1.2.3.4": "member:9", "5.6.7.8": "member:2"}
               and identity.parse_account_pin("") == {})
    print(f"  pin: bare id, member: key, multiple entries and whitespace parse: "
          f"{'OK' if _pin_ok else 'FAIL'}")
    ok &= _pin_ok
    _pin_bad = 0
    for _b in ("noequals", "1.2.3.4=", "=member:3", "1.2.3.4=bogus"):
        try:
            identity.parse_account_pin(_b)
        except ValueError:
            _pin_bad += 1
    print(f"  pin: malformed entries all refuse rather than bind wrongly: "
          f"{'OK' if _pin_bad == 4 else 'FAIL ' + str(_pin_bad) + '/4'}")
    ok &= _pin_bad == 4
    # A pin must BEAT the lookup, and an unpinned address must be untouched.
    _saved_pin = dict(identity.ACCOUNT_PIN)
    try:
        identity.ACCOUNT_PIN.clear()
        identity.ACCOUNT_PIN["9.9.9.9"] = "member:77"
        _beats = identity.account_for("9.9.9.9") == "member:77"
    finally:
        identity.ACCOUNT_PIN.clear()
        identity.ACCOUNT_PIN.update(_saved_pin)
    print(f"  pin: a pinned address wins over the session lookup: "
          f"{'OK' if _beats else 'FAIL'}")
    ok &= _beats
    # KEY: AND THE LOGIN PATH SPECIFICALLY. The first version of this knob
    # only guarded account_for(), while the 0x0321 credentials handler calls
    # member_for_ip() directly -- so the pin armed, logged its banner, and
    # changed nothing. Pin the call the login actually makes.
    _saved_pin2 = dict(identity.ACCOUNT_PIN)
    try:
        identity.ACCOUNT_PIN.clear()
        identity.ACCOUNT_PIN["9.9.9.9"] = "member:77"
        _login = identity.member_for_ip("9.9.9.9")
        _login_ok = _login is not None and _login[0] == "member:77"
        identity.ACCOUNT_PIN.clear()
        _unpinned_ok = identity.member_for_ip("9.9.9.9") in (None,) or True
    finally:
        identity.ACCOUNT_PIN.clear()
        identity.ACCOUNT_PIN.update(_saved_pin2)
    print(f"  pin: the LOGIN path (member_for_ip, what 0x0321 calls) honours "
          f"it: {'OK' if _login_ok else 'FAIL'}")
    ok &= _login_ok

    # --- FMO_LOBAPI_MARK ------------------------------------------------- #
    # The marker body is the instrument the whole probe rests on, so pin its
    # arithmetic rather than trusting it: dword i must be base+i, the length
    # must be exact (including a non-multiple-of-4 tail), and with the env
    # unset NOTHING may be marked -- a probe that is on by accident would put
    # counts and set bits in front of the client on an ordinary launch.
    _mb = lobapi.mark_body(68, 1000)
    _ok_len = len(_mb) == 68
    _ok_vals = all(struct.unpack_from("<I", _mb, i * 4)[0] == 1000 + i
                   for i in range(17))
    print(f"  mark: 68B body is 17 dwords of base+i: "
          f"{'OK' if _ok_len and _ok_vals else 'FAIL'}")
    ok &= _ok_len and _ok_vals
    _tail = lobapi.mark_body(10, 0)
    _ok_tail = (len(_tail) == 10
                and struct.unpack_from("<I", _tail, 4)[0] == 1
                and _tail[8:] == struct.pack("<I", 2)[:2])
    print(f"  mark: a non-multiple-of-4 length is tail-cut, not padded: "
          f"{'OK' if _ok_tail else 'FAIL'}")
    ok &= _ok_tail
    if not os.environ.get("FMO_LOBAPI_MARK"):
        _quiet = (not lobapi.MARK_LOBAPI
                  and lobapi.lobapi_payload(0x019C, 36) == bytes(36))
        print(f"  mark: FMO_LOBAPI_MARK unset -> nothing marked, bodies are "
              f"zeros: {'OK' if _quiet else 'FAIL'}")
        ok &= _quiet
    # KEY: DRIVE THE ARMED PATH WITHOUT THE ENV. The check above only ever ran the
    # UNSET case, and that is precisely how the import-order NameError shipped
    # a selftest PASS: the loop that referenced LOBAPI before it existed was
    # never entered. parse_lobapi_mark() is pure so the armed path is testable.
    _armed = (lobapi.parse_lobapi_mark("0x01AC") == {0x01AC: lobapi.MARK_BASE_DEFAULT}
              and lobapi.parse_lobapi_mark("0x01AD:5000") == {0x01AC: 5000}
              and lobapi.parse_lobapi_mark(" 0x01AC : 7 , ") == {0x01AC: 7}
              and lobapi.parse_lobapi_mark("") == {})
    print(f"  mark: request id, REPLY id, custom base and whitespace all parse: "
          f"{'OK' if _armed else 'FAIL'}")
    ok &= _armed
    _rejects = 0
    for _bad in ("0x9999", "0x018A", "nope", "0x01AC:xyz"):
        try:
            lobapi.parse_lobapi_mark(_bad)
        except ValueError:
            _rejects += 1
    print(f"  mark: unknown id / stub reply / non-integers all refuse to arm: "
          f"{'OK' if _rejects == 4 else 'FAIL ' + str(_rejects) + '/4'}")
    ok &= _rejects == 4

    # ---- the squadron INSIGNIA list (0x019F) ----
    # The arithmetic has to agree with the client's rep movsd or the copy walks
    # off the block: 0x14 header + 0x5AD dwords, count at block+0x40, rows at
    # block+0x44 with stride 8.
    # WARNING: THE TEST THAT WOULD HAVE CAUGHT THE 2026-09-09 SECOND CRASH. The first
    # version asserted my own arithmetic (0x14 + block offset) and so passed on
    # a body whose count was 20 bytes into the row area. Pin it to the rule the
    # working 0x01AD already proves: a payload starts AT the block, because the
    # client's record+0x14 IS payload+0. LOBAPI[0x01AC][1] == 68 is that rule
    # expressed on a message we have watched work.
    _in_len = (squadron.INSIGNIA_LIST_LEN == 0x5AD * 4 == 5812
               and squadron.INSIGNIA_COUNT_OFF == 0x7E35 - 0x7DF5 == 0x40
               and squadron.INSIGNIA_ROW_OFF == 0x7E39 - 0x7DF5 == 0x44
               and squadron.INSIGNIA_ROW_LEN == 8
               # the precedent, asserted so nobody re-adds the header
               and lobapi.LOBAPI[0x01AC][1] == 68)
    print(f"  insignia: offsets are BLOCK-relative (payload+0 == record+0x14, "
          f"as 0x01AD proves): {'OK' if _in_len else 'FAIL'}")
    ok &= _in_len
    _in_cat = len(squadron.INSIGNIA) > 0 and all(1 <= i <= 0xFFFF for i, _f, _e in squadron.INSIGNIA)
    _in_skip = _fmodata_skip("fmo-insignia.tsv")
    print(f"  insignia: the catalogue loads and every id fits the u16 the "
          f"client reads: "
          f"{_in_skip or ('OK' if _in_cat else 'FAIL (fmodata/fmo-insignia.tsv?)')}")
    if not _in_skip:
        ok &= _in_cat
    if squadron.INSIGNIA:
        # A 255 ("any") row must go out carrying the PLAYER's nation, because
        # 0x611BE344 compares for EQUALITY against byte[lobby+0x8B4]. Serving
        # 255 there would make the client skip every shared insignia.
        _any = [i for i, f, _e in squadron.INSIGNIA if f == 255]
        _o1, _o2 = squadron.insignia_for(1), squadron.insignia_for(2)
        _in_nat = (all(i in {x for x, _n in _o1} for i in _any)
                   and all(i in {x for x, _n in _o2} for i in _any)
                   and {x for x, _n in _o1} != {x for x, _n in _o2})
        print(f"  insignia: 'any' rows are offered to BOTH nations and the "
              f"nation-specific ones are not: {'OK' if _in_nat else 'FAIL'}")
        ok &= _in_nat
        _b = squadron.insignia_payload(1)
        _cnt = struct.unpack_from("<I", _b, squadron.INSIGNIA_COUNT_OFF)[0]
        _n0 = _b[squadron.INSIGNIA_ROW_OFF]
        _id0 = struct.unpack_from("<H", _b, squadron.INSIGNIA_ROW_OFF + 2)[0]
        _in_body = (len(_b) == squadron.INSIGNIA_LIST_LEN
                    and _cnt == len(_o1) and _cnt > 0
                    and _n0 == 1 and _id0 == _o1[0][0]
                    # every row must fit inside the block the client copies
                    and squadron.INSIGNIA_ROW_OFF + _cnt * squadron.INSIGNIA_ROW_LEN <= squadron.INSIGNIA_LIST_LEN)
        print(f"  insignia: the body carries count={_cnt} and stamps the "
              f"player's nation on row 0: {'OK' if _in_body else 'FAIL'}")
        ok &= _in_body
        # "none" must be reachable but is NOT the default: an empty list is the
        # state that crashed the client.
        _sv_off = squadron.INSIGNIA_OFFER
        try:
            flat_globals()["INSIGNIA_OFFER"] = "none"
            _in_none = squadron.insignia_for(1) == [] and squadron.insignia_push(1, 1) is None
        finally:
            flat_globals()["INSIGNIA_OFFER"] = _sv_off
        print(f"  insignia: FMO_SQUADRON_INSIGNIA_LIST=none serves nothing and "
              f"pushes nothing: {'OK' if _in_none else 'FAIL'}")
        ok &= _in_none
        _in_dflt = squadron.INSIGNIA_OFFER.lower() != "none"
        print(f"  insignia: the DEFAULT is not 'none' (an empty picker is the "
              f"crash): {'OK' if _in_dflt else 'FAIL'}")
        ok &= _in_dflt

    # ---- the insignia REGISTRATION (0x01C4) ----
    # KEY: The literal body a live client sent at 2026-09-09T19:45:52Z,
    # kept verbatim so this test is the WIRE and not my reading of it.
    _set_live = bytes.fromhex("03000000" "00000000" "83000000") + bytes(36)
    _sg, _si = squadron.parse_insignia_set(_set_live)
    _set_ok = (_sg == 3 and _si == 131
               and squadron.parse_insignia_set(b"") == (0, 0)
               and squadron.parse_insignia_set(bytes(4)) == (0, 0))
    print(f"  insignia: the LIVE 0x01C4 body decodes to group 3 / insignia 131, "
          f"and a short body is (0,0): {'OK' if _set_ok else 'FAIL'}")
    ok &= _set_ok
    # 131 must be an id we actually offered -- if the client can register
    # something outside the list we serve, the list is not the whole story.
    _off1 = {i for i, _n in squadron.insignia_for(1)}
    _set_in = (131 in _off1) if squadron.INSIGNIA else True
    print(f"  insignia: the id the client registered was one WE offered: "
          f"{'OK' if _set_in else 'FAIL -- the picker has another source'}")
    ok &= _set_in
    if fmostore is not None and not test_db:
        print("  insignia: a registration persists: SKIP (no test database)")
    if fmostore is not None and test_db:
        _a, _b0 = fmostore.set_squadron_insignia(3, 131, "selftest")
        _c, _d = fmostore.set_squadron_insignia(3, 999, "selftest")
        _persist = (_a == 131 and _b0 == 0 and _c == 131 and _d == 131
                    and fmostore.squadron_insignia(3) == 131
                    and fmostore.squadron_insignia(45) == 0)
        print(f"  insignia: a registration persists and a SECOND one cannot "
              f"overwrite it (86:21): {'OK' if _persist else 'FAIL'}")
        ok &= _persist
    if fmostore is not None:
        # And the +0x0A word must report the stored value, which is what greys
        # the row -- the whole point of persisting it.
        _word = squadron.squadron_insignia_word(1, 0)
        _wordless = (_word == 0) if squadron.insignia_for(1) else (_word != 0)
        print(f"  insignia: +0x0A is 0 for a group with no registration: "
              f"{'OK' if _wordless else 'FAIL'}")
        ok &= _wordless

    # ---- the squadron table (0x01AC -> 0x01AD) ----
    # The block is 4 slots x 16 + the s32 slot index, and that arithmetic has to
    # agree with the LOBAPI length or the client's rep movsd reads past it.
    _sq_len = (squadron.SQ_MY_SLOT + 4 == squadron.Q1AD_LEN == lobapi.LOBAPI[0x01AC][1] == 68
               and squadron.SQ_SLOTS * squadron.SQ_SLOT_LEN == squadron.SQ_MY_SLOT)
    print(f"  squadron: 4 slots x 16 + s32 index == 68 == the LOBAPI length: "
          f"{'OK' if _sq_len else 'FAIL'}")
    ok &= _sq_len
    _req = bytearray(squadron.Q1AD_LEN)
    struct.pack_into("<II", _req, 0 * squadron.SQ_SLOT_LEN, 0x11112222, 0x33334444)
    struct.pack_into("<II", _req, 2 * squadron.SQ_SLOT_LEN, 0xAAAABBBB, 0)
    struct.pack_into("<i", _req, squadron.SQ_MY_SLOT, -1)
    _ids, _mine = squadron.parse_01ac(bytes(_req))
    _sq_parse = (_ids == [0x3333444411112222, 0, 0xAAAABBBB, 0] and _mine == -1)
    print(f"  squadron: the client's four POL group ids parse out of 0x01AC: "
          f"{'OK' if _sq_parse else 'FAIL'}")
    ok &= _sq_parse
    # A 4-byte body is what several older selftests send, and it must read as
    # four EMPTY slots rather than raise -- "no POL groups" is a real state.
    _ep_ok = (squadron._epoch("2026-08-18T19:11:30Z") == 1787080290
              and squadron._epoch(None) == 0 and squadron._epoch("not a date") == 0)
    print(f"  squadron: the group's created_at parses to a time_t for +0x0C, "
          f"and junk is 0 not a raise: {'OK' if _ep_ok else 'FAIL'}")
    ok &= _ep_ok
    _sq_short = squadron.parse_01ac(bytes(4)) == ([0, 0, 0, 0], -1)
    print(f"  squadron: a short/absent 0x01AC body reads as four empty slots: "
          f"{'OK' if _sq_short else 'FAIL'}")
    ok &= _sq_short
    if not squadron.SERVE_SQUADRON and not squadron.FILL_01AD and not squadron.FIELDS_01AD:
        _b, _w = squadron.reply_01ad(bytes(_req))
        _sq_off = _b == bytes(squadron.Q1AD_LEN) and "68 ZEROS" in _w
        print(f"  squadron: FMO_SQUADRON default OFF -- 68 zeros even when the "
              f"client sends real group ids: {'OK' if _sq_off else 'FAIL'}")
        ok &= _sq_off
    # KEY: DRIVE THE ARMED PATH WITHOUT THE ENV, for the reason the mark test
    # above spells out: an unset knob never enters the code that can be wrong.
    _sv = (squadron.SERVE_SQUADRON, squadron.SQUADRON_NATION, squadron.SQUADRON_GROUPS,
           squadron.FILL_01AD, squadron.FIELDS_01AD)
    # WARNING: The insignia default is a CRASH GUARD, not a preference: 0 un-greys
    # "Set squadron insignia" and that row killed the client live 2026-09-09.
    # The guard must still fire when the picker CANNOT be populated, and must
    # step aside when it can -- that pairing is the whole safety argument.
    _sv_off2 = squadron.INSIGNIA_OFFER
    try:
        flat_globals()["INSIGNIA_OFFER"] = "none"
        _guard_on = squadron.squadron_insignia_word(1) != 0
        flat_globals()["INSIGNIA_OFFER"] = "all"
        _guard_off = squadron.squadron_insignia_word(1) == 0 if squadron.INSIGNIA else True
    finally:
        flat_globals()["INSIGNIA_OFFER"] = _sv_off2
    _ins_ok = _guard_on and _guard_off
    print(f"  squadron: +0x0A greys row 7 when the picker has NO rows and "
          f"steps aside when it does: {'OK' if _ins_ok else 'FAIL -- the crash guard is wrong'}")
    ok &= _ins_ok
    try:
        flat_globals()["SERVE_SQUADRON"] = 1
        flat_globals()["SQUADRON_NATION"] = 2
        flat_globals()["SQUADRON_GROUPS"] = []
        flat_globals()["FILL_01AD"] = 0
        flat_globals()["FIELDS_01AD"] = {}
        # groups vouches for slot 0's id only; slot 2's is a stranger.
        _grp = {0x3333444411112222: {"class": 5, "formed": 1787080290}}
        _b, _w = squadron.reply_01ad(bytes(_req), _grp)
        _armed_ok = (
            len(_b) == squadron.Q1AD_LEN
            # every id comes back verbatim -- we annotate, we never invent one,
            # and we never blank a slot the client filled
            and _b[0:8] == bytes(_req[0:8])
            and _b[0x20:0x28] == bytes(_req[0x20:0x28])
            # slot 0 is marked as this nation's squadron ...
            and _b[squadron.SQ_IS_FMO] == 1 and _b[squadron.SQ_NATION] == 2
            # ... slot 2's id is real-looking but is NOT one of this member's
            # groups, so it stays unannotated (the live litter case)
            and _b[0x20 + squadron.SQ_IS_FMO] == 0 and _b[0x20 + squadron.SQ_NATION] == 0
            # ... and the EMPTY slots stay empty: a flag on a zero id would
            # send the client to [0x613AE380]+0x618 with a group that is nobody
            and _b[0x10 + squadron.SQ_IS_FMO] == 0 and _b[0x30 + squadron.SQ_IS_FMO] == 0
            # WARNING: +0x0A must NOT be 0: mask 0x61398FCC greys row 7 "Set
            # squadron insignia" when it is set, and 0 leaves that row live --
            # a player clicked it on 2026-09-09 and the client died
            and (struct.unpack_from("<H", _b, squadron.SQ_INSIGNIA)[0]
                 == squadron.squadron_insignia_word(2))
            # +0x0C is the "Formed on" time_t -- 0 draws the epoch (69/12/31)
            and struct.unpack_from("<I", _b, squadron.SQ_FORMED)[0] == 1787080290
            # my slot index = the first ANNOTATED slot, and it must NOT be
            # negative or 0x611BBD40 refuses to auto-activate
            and struct.unpack_from("<i", _b, squadron.SQ_MY_SLOT)[0] == 0)
        print(f"  squadron: armed -- only a slot whose id is one of THIS "
              f"member's POL groups is stamped: {'OK' if _armed_ok else 'FAIL'}")
        ok &= _armed_ok
        # KEY: THE REGRESSION THIS FILTER EXISTS FOR, built from the bytes prod
        # actually logged at 2026-09-09T03:22:36Z (member 3 / Lex). Slot 0 is
        # POL group 3; slots 1..3 are the seeder's stack litter -- slot 2 holds
        # a CODE ADDRESS inside the 0x01AC sender and slot 3 holds the length
        # and message id it pushed. Annotating those was the earlier bug.
        _live = bytearray(squadron.Q1AD_LEN)
        for _i, _g in enumerate((3, 0x0FBB78840FBBC074,
                                 0x611745C600000000, 0x00000050000001AC)):
            struct.pack_into("<II", _live, _i * squadron.SQ_SLOT_LEN,
                             _g & 0xFFFFFFFF, (_g >> 32) & 0xFFFFFFFF)
        _bl, _wl = squadron.reply_01ad(bytes(_live), {3: {"class": 5, "formed": 1787080290}})
        _live_ok = (_bl[squadron.SQ_IS_FMO] == 1
                    and _bl[0x10 + squadron.SQ_IS_FMO] == 0
                    and _bl[0x20 + squadron.SQ_IS_FMO] == 0
                    and _bl[0x30 + squadron.SQ_IS_FMO] == 0
                    and struct.unpack_from("<i", _bl, squadron.SQ_MY_SLOT)[0] == 0
                    and "litter" in _wl)
        print(f"  squadron: the LIVE 09-09 poll -- group 3 stamped, the three "
              f"litter slots refused: {'OK' if _live_ok else 'FAIL'}")
        ok &= _live_ok
        # The same bytes with NO group knowledge must annotate nothing at all,
        # not fall back to "non-zero means a squadron".
        _bn, _wn = squadron.reply_01ad(bytes(_live), {})
        _none_ok = (all(_bn[_i * squadron.SQ_SLOT_LEN + squadron.SQ_IS_FMO] == 0 for _i in range(squadron.SQ_SLOTS))
                    and struct.unpack_from("<i", _bn, squadron.SQ_MY_SLOT)[0] == -1
                    and "NONE of the ids" in _wn)
        print(f"  squadron: no group knowledge -> nothing stamped, index -1: "
              f"{'OK' if _none_ok else 'FAIL'}")
        ok &= _none_ok
        # No groups at all: the answer must SAY so rather than quietly serving
        # a table that claims slot 0 is a squadron with a zero id.
        _b0, _w0 = squadron.reply_01ad(bytes(squadron.Q1AD_LEN), _grp)
        _empty_ok = (_b0 == bytes(squadron.Q1AD_LEN)[:squadron.SQ_MY_SLOT] + struct.pack("<i", -1)
                     and "FOUR EMPTY GROUP IDS" in _w0)
        print(f"  squadron: armed but no POL groups -> index -1 and the log "
              f"names the POL side: {'OK' if _empty_ok else 'FAIL'}")
        ok &= _empty_ok
        # The by-hand override answers when accounts.db cannot.
        flat_globals()["SQUADRON_GROUPS"] = [3]
        _bo, _wo = squadron.reply_01ad(bytes(_live), {})
        _ovr_ok = _bo[squadron.SQ_IS_FMO] == 1 and "PROBE" in _wo
        print(f"  squadron: FMO_SQUADRON_GROUPS overrides the DB lookup: "
              f"{'OK' if _ovr_ok else 'FAIL'}")
        ok &= _ovr_ok
        flat_globals()["SQUADRON_GROUPS"] = []
        # The probes still win: an armed FMO_01AD_FILL means you are asking
        # where dwords land, not serving a table.
        flat_globals()["FILL_01AD"] = 7
        _bp, _wp = squadron.reply_01ad(bytes(_req), _grp)
        _probe_ok = _bp == struct.pack("<I", 7) * squadron.Q1AD_DWORDS and "PROBE" in _wp
        print(f"  squadron: FMO_01AD_FILL still overrides the built table: "
              f"{'OK' if _probe_ok else 'FAIL'}")
        ok &= _probe_ok
    finally:
        (flat_globals()["SERVE_SQUADRON"], flat_globals()["SQUADRON_NATION"],
         flat_globals()["SQUADRON_GROUPS"],
         flat_globals()["FILL_01AD"], flat_globals()["FIELDS_01AD"]) = _sv

    # Default OFF: empty FMO_CITY_TABLE means no push is built, and a 0x01AC
    # poll takes the generic one-frame path. Set: the poll answers with the
    # 0x01AD reply AND a 0x019A push on the queue sequence.
    # WARNING: FMO_ZONE_CONTROL rides the SAME poll, so every frame count here is
    # the city expectation plus that push when it is armed.
    _zextra = 1 if zonecontrol.ZONE_CONTROL else 0
    if not citytable.city_rows():
        cpush_off = citytable.city_table_push(1) is None
        s2 = session.Session("selftest:0")
        outs = s2.on_packet(packet.parse(packet.build(0x01AC, bytes(4), seq=0x99, conn_id=1)))
        cgen = len(outs) == 1 + _zextra
        print(f"  city: no city rows (FMO_CITY_TABLE empty and FMO_WAR=0) -- no "
              f"push, 0x01AC is one frame{' + the zone push' if _zextra else ''}: "
              f"{'OK' if cpush_off and cgen else 'FAIL'}")
        ok &= cpush_off and cgen
    else:
        s2 = session.Session("selftest:0")
        outs = [packet.parse(o) for o in
                s2.on_packet(packet.parse(packet.build(0x01AC, bytes(4), seq=0x99,
                                                       conn_id=1)))]
        con = (len(outs) == 2 + _zextra
               and outs[0]["msg"] == lobapi.LOBAPI[0x01AC][0]
               and outs[1]["msg"] == citytable.MSG_CITY_TABLE
               and outs[1]["seq"] == pushes.QUEUE_SEQ
               and len(outs[1]["payload"]) == citytable.CITY_TABLE_LEN)
        print(f"  city: FMO_CITY_TABLE set -- 0x01AC answers 0x01AD + a 0x019A "
              f"push on the queue seq: {'OK' if con else 'FAIL'}")
        ok &= con
        # The post-move ordering (2026-08-27): a category-2 grant carries the
        # push BEHIND the 0x0153 and arms one repeat on the first keepalive;
        # a second keepalive carries nothing.
        s3 = session.Session("selftest:0")
        req = bytearray(move.MOVE_REQ_LEN)
        struct.pack_into("<II", req, move.M16D_FIELD_00, move.MOVE_CATEGORY_BRIEFING, 0)
        mv = [packet.parse(o) for o in
              s3.on_packet(packet.parse(packet.build(move.MSG_MOVE_REQ, bytes(req), seq=0x77,
                                                     conn_id=1)))]
        ka0 = s3.on_packet(packet.parse(packet.build(charselect.MSG_KEEPALIVE, bytes(8), seq=pushes.QUEUE_SEQ,
                                                     conn_id=1)))
        s3.city_push_grant_at -= 11        # as if 11s had passed
        ka1 = [packet.parse(o) for o in
               s3.on_packet(packet.parse(packet.build(charselect.MSG_KEEPALIVE, bytes(8), seq=pushes.QUEUE_SEQ,
                                                      conn_id=1)))]
        ka2 = s3.on_packet(packet.parse(packet.build(charselect.MSG_KEEPALIVE, bytes(8), seq=pushes.QUEUE_SEQ,
                                                     conn_id=1)))
        cmove = (len(mv) == 2 + _zextra and mv[0]["msg"] == zoneentry.MSG_0150_REPLY
                 and mv[1]["msg"] == citytable.MSG_CITY_TABLE
                 and not ka0
                 and len(ka1) == 1 and ka1[0]["msg"] == citytable.MSG_CITY_TABLE
                 and not ka2)
        print(f"  city: a briefing-room grant pushes 0x019A behind the 0x0153, "
              f"HOLDS through an immediate keepalive (04:05Z lesson), and "
              f"repeats once 10s+ later: {'OK' if cmove else 'FAIL'}")
        ok &= cmove

    # The zone push as it actually leaves: the last frame of the same 0x01AC
    # poll, on the queue sequence (the arm 0x6117F013 is reached through the
    # unsolicited dispatcher, not a reply slot), carrying the full block and
    # the row count the knob asked for.
    if zonecontrol.ZONE_CONTROL:
        s4 = session.Session("selftest:0")
        zouts = [packet.parse(o) for o in
                 s4.on_packet(packet.parse(packet.build(0x01AC, bytes(4), seq=0x99,
                                                        conn_id=1)))]
        zwire = (zouts[-1]["msg"] == zonecontrol.MSG_ZONE_CONTROL
                 and zouts[-1]["seq"] == pushes.QUEUE_SEQ
                 and len(zouts[-1]["payload"]) == zonecontrol.ZONE_CONTROL_LEN
                 and struct.unpack_from("<I", zouts[-1]["payload"],
                                        zonecontrol.ZONE_COUNT_OFF)[0]
                 == len(zonecontrol.parse_zone_control(zonecontrol.ZONE_CONTROL)))
        print(f"  zone: FMO_ZONE_CONTROL set -- 0x01AC carries a "
              f"0x{zonecontrol.MSG_ZONE_CONTROL:04X} push on the queue seq, "
              f"{zonecontrol.ZONE_CONTROL_LEN}B: {'OK' if zwire else 'FAIL'}")
        ok &= zwire

    # FMO_0159_REGRANT: the ack is followed by exactly one 0x0153 on the push
    # sequence, and a second 0x0159 in the same session gets the ack alone.
    _sv_rg = scriptcall.REGRANT_0159
    try:
        scriptcall.REGRANT_0159 = True
        srg = session.Session("selftest:regrant")
        if srg is not None:
            o1 = srg.on_packet(packet.parse(packet.build(scriptcall.MSG_0159_REQ, bytes(scriptcall.S159_BODY_LEN),
                                                         seq=0x1234, conn_id=1)))
            o2 = srg.on_packet(packet.parse(packet.build(scriptcall.MSG_0159_REQ, bytes(scriptcall.S159_BODY_LEN),
                                                         seq=0x1235, conn_id=1)))
            m1 = [packet.parse(x)["msg"] for x in o1]
            m2 = [packet.parse(x)["msg"] for x in o2]
            rg_ok = (m1[0] == handshake.MSG_SESSION_START and zoneentry.MSG_0150_REPLY in m1
                     and packet.parse(o1[m1.index(zoneentry.MSG_0150_REPLY)])["seq"] == pushes.QUEUE_SEQ
                     and m2 == [handshake.MSG_SESSION_START])
            print(f"  0159: FMO_0159_REGRANT pushes ONE 0x0153 (seq {pushes.QUEUE_SEQ:#x}) after "
                  f"the first ack {[hex(m) for m in m1]} and none after the second "
                  f"{[hex(m) for m in m2]}: {'OK' if rg_ok else 'FAIL'}")
            ok &= rg_ok
        else:
            print("  0159: FMO_0159_REGRANT pin SKIPPED (no Session class in scope)")
    finally:
        scriptcall.REGRANT_0159 = _sv_rg
    # 2026-09-06: the Scramble Board's Training Sector / war-map requests each
    # get exactly one frame on the CLIENT's own sequence, with the id its poller
    # compares (0x0163 / 0x01F5 / 1) and the 0x01F5 body at its copied length.
    s6 = session.Session("selftest:0")
    r62 = [packet.parse(o) for o in s6.on_packet(packet.parse(packet.build(battlemaps.MSG_0162_REQ, bytes(20),
                                                       seq=0x5161, conn_id=1)))]
    rf4 = [packet.parse(o) for o in s6.on_packet(packet.parse(packet.build(battlemaps.MSG_01F4_REQ, b"",
                                                       seq=0x5162, conn_id=1)))]
    rf6 = [packet.parse(o) for o in s6.on_packet(packet.parse(packet.build(battlemaps.MSG_01F6_REQ, bytes(136),
                                                       seq=0x5163, conn_id=1)))]
    sb_ok = (len(r62) == 1 and r62[0]["msg"] == battlemaps.MSG_0163_REPLY
             and r62[0]["seq"] == 0x5161
             and len(r62[0]["payload"]) == groupchannel.REPLY_0163_LEN   # the GROUP INFO body
             and len(rf4) == 1 and rf4[0]["msg"] == battlemaps.MSG_01F5_REPLY
             and rf4[0]["seq"] == 0x5162 and len(rf4[0]["payload"]) == battlemaps.S1F5_BODY_LEN
             and struct.unpack_from("<I", rf4[0]["payload"])[0] == len(battlemaps.BATTLE_MAPS)
             and len(rf6) == 1 and rf6[0]["msg"] == handshake.MSG_SESSION_START
             and rf6[0]["seq"] == 0x5163)
    print(f"  board: 0x0162 -> {groupchannel.REPLY_0163_LEN}-B 0x0163 GROUP INFO, 0x01F4 -> {battlemaps.S1F5_BODY_LEN}-B 0x01F5 "
          f"(count {len(battlemaps.BATTLE_MAPS)}), 0x01F6 -> message 1, each on the client's "
          f"seq: {'OK' if sb_ok else 'FAIL'}")
    ok &= sb_ok
    # 2026-09-06: THE REAL WAR MAP (script native 0xE318 -> screen 0x6118CA00)
    # sends 0x015E and 0x0160 and NOTHING else. Each must come back on the
    # CLIENT's own sequence with the id its poller compares (0x015F / 0x0161)
    # and a body whose u32 count is 0 -- the row loops are guarded by count > 0,
    # so an empty answer is the one that provably cannot walk off the payload.
    s7 = session.Session("selftest:0")
    r5e = [packet.parse(o) for o in s7.on_packet(packet.parse(packet.build(warmap.MSG_015E_REQ, bytes(20),
                                                                           seq=0x5164, conn_id=1)))]
    r60 = [packet.parse(o) for o in s7.on_packet(packet.parse(packet.build(warmap.MSG_0160_REQ, bytes(28),
                                                                           seq=0x5165, conn_id=1)))]
    wm_ok = (len(r5e) == 1 and r5e[0]["msg"] == warmap.MSG_015F_REPLY
             and r5e[0]["seq"] == 0x5164
             and len(r5e[0]["payload"]) == warmap.S15F_HEAD_LEN
             and struct.unpack_from("<I", r5e[0]["payload"])[0] == 0
             and len(r60) == 1 and r60[0]["msg"] == warmap.MSG_0161_REPLY
             and r60[0]["seq"] == 0x5165
             and len(r60[0]["payload"]) == warmap.S15F_HEAD_LEN
             and struct.unpack_from("<I", r60[0]["payload"])[0] == 0)
    print(f"  war map: 0x015E -> 0x015F and 0x0160 -> 0x0161, {warmap.S15F_HEAD_LEN}-B "
          f"bodies with count 0, each on the client's seq: "
          f"{'OK' if wm_ok else 'FAIL'}")
    ok &= wm_ok
    # 2026-09-06: FMO_WARMAP_MAPS -- the retail sortie door. The row's FIRST
    # DWORD becomes the list item's value ([item+0x60], 0x6118EA34) and arm 3
    # copies exactly that into [warmap+0x5A10], which arm 4 sends as 0x0139's
    # battle-map id. If the row stride or that offset drifts, the pick sorties
    # to the wrong map -- or to nothing -- so both are pinned here.
    _sv_wm = list(warmap.WARMAP_MAPS)
    try:
        flat_globals()["WARMAP_MAPS"] = [418, 66]
        s7b = session.Session("selftest:0")
        r5f = [packet.parse(o) for o in s7b.on_packet(
            packet.parse(packet.build(warmap.MSG_015E_REQ, bytes(20), seq=0x5166, conn_id=1)))]
        _pl = r5f[0]["payload"] if r5f else b""
        wmr_ok = (len(r5f) == 1 and r5f[0]["msg"] == warmap.MSG_015F_REPLY
                  and len(_pl) == warmap.S15F_HEAD_LEN + 2 * warmap.S15F_ROW_LEN
                  and struct.unpack_from("<I", _pl, 0)[0] == 2
                  and struct.unpack_from("<I", _pl, warmap.S15F_HEAD_LEN)[0] == 418
                  and struct.unpack_from(
                      "<I", _pl, warmap.S15F_HEAD_LEN + warmap.S15F_ROW_LEN)[0] == 66
                  # and the 0x0160 half must stay empty: its row stride is unread
                  and struct.unpack_from("<I", [packet.parse(o) for o in s7b.on_packet(
                      packet.parse(packet.build(warmap.MSG_0160_REQ, bytes(28), seq=0x5167,
                                                conn_id=1)))][0]["payload"], 0)[0] == 0)
    finally:
        flat_globals()["WARMAP_MAPS"] = _sv_wm
    print(f"  war map: FMO_WARMAP_MAPS=418,66 puts two {warmap.S15F_ROW_LEN}-B rows at "
          f"+{warmap.S15F_HEAD_LEN:#x} with the ids at each row's +0x00 (the value arm 3 "
          f"sorties with), and leaves 0x0161 empty: "
          f"{'OK' if wmr_ok else 'FAIL'}")
    ok &= wmr_ok
    # WARNING:KEY: 2026-09-08: A BATTLE CHAT SUBMIT CARRIES NO NAME, and echoing
    # that blank straight back is why the line never rendered. Arm 3 reads the
    # names from the group member-info blob (blob+0x1C / +0x2D) and we serve
    # that blob as zeros, so `unnamed` is what arrives -- while the server
    # logged, echoed and was ACKED for every word the player typed.
    _en = [
        (("Lex", "Arden"), "Lex", "a named submit is used verbatim"),
        ((None, None), None, "a nameless submit falls back to the channel"),
        (("", ""), None, "empty strings are nameless too, not a name"),
    ]
    # WARNING: ASCII in the FAILURE prints. The pass line can carry emoji; a
    # failure line printed to a cp1252 console cannot -- measured: the
    # mutation reached this branch and died on UnicodeEncodeError instead
    # of saying FAIL, which turns a working pin into a traceback.
    _enok = True
    for (_n1, _n2), _want, _what in _en:
        _got, _src = warmap.chat_echo_name(_n1, _n2, "192.0.2.1")
        if _want is not None and _got != _want:
            print(f"  FAIL chat echo name wrong for {_what}: {_got!r}")
            _enok = False
        if _want is None and _src == "the submit":
            print(f"  FAIL chat echo name took a BLANK submit as a name "
                  f"({_what}): {_got!r} from {_src}")
            _enok = False
    #: and it must never invent one -- an unknown host stays blank, loudly,
    #: rather than echoing somebody else's name onto their line.
    _blank, _bsrc = warmap.chat_echo_name(None, None, "203.0.113.99")
    if _blank and _bsrc == "the submit":
        print("  FAIL chat echo name invented a name for an unknown host")
        _enok = False
    print(f"  chat: a nameless battle submit echoes under the CHANNEL's name "
          f"instead of blank, a named one is untouched, and an unknown host "
          f"stays blank rather than borrowing: {'OK' if _enok else 'FAIL'}")
    ok &= _enok

    # VERIFIED:KEY: 2026-09-08: THE SECTOR THE CLIENT ASKED FOR. Live report: "no matter
    # what sector I pick for the battle, it always takes me to the same place."
    # It did, because 0x015E's +0x00 u32 -- the ARE table's `tile` -- was never
    # read and every sector got the same flat FMO_WARMAP_MAPS list. These pins
    # drive the whole path the two live picks took.
    if fmosectors is not None:
        _sv_mk, _sv_ss, _sv_sm = zoneentry.MAPKIND, sortie.SERVE_SORTIE, sortie.SORTIE_MAPNO
        try:
            flat_globals()["MAPKIND"] = 200
            # The two tiles measured live, 0x000115D1 and 0x000119B5.
            _s17 = warmap.warmap_sector_for(struct.pack("<I", 71121))
            _s20 = warmap.warmap_sector_for(struct.pack("<I", 72117))
            _pairs = ((_s17, 17, 232), (_s20, 20, 267))
            _lk = all(g[1] == r and g[2] == m and g[3]
                      for g, r, m in _pairs)
            print(f"  war map: the live tiles 71121/72117 resolve to selector "
                  f"200 sectors {_s17[1]}/{_s20[1]} -> battle maps "
                  f"{_s17[2]}/{_s20[2]} (want 17->232, 20->267): "
                  f"{'OK' if _lk else 'FAIL'}")
            ok &= _lk

            # WARNING: THE DISCRIMINATOR. Two DIFFERENT sectors must give two
            # DIFFERENT maps -- a lookup that returned one constant would pass
            # every other check here and reproduce the exact bug.
            _diff = _s17[2] != _s20[2]
            print(f"  war map: two different sectors give two different maps "
                  f"({_s17[2]} != {_s20[2]}) -- the bug was one constant for "
                  f"all of them: {'OK' if _diff else 'FAIL'}")
            ok &= _diff

            # A real sector with no battlefield is count=0, NOT a refusal:
            # 1,500 of the 3,108 rows name SE's 2/3/4/5 placeholders.
            _empty = next((t for t, (r, m) in
                           fmosectors.SECTORS.get(200, {}).items()
                           if m not in missionlist.TYPE1_ON_DISK), None)
            if _empty is None:
                _empty = next(t for sel in fmosectors.SECTORS.values()
                              for t, (r, m) in sel.items()
                              if m not in missionlist.TYPE1_ON_DISK)
                _eg = warmap.warmap_sector_for(struct.pack("<I", _empty), selector=next(
                    s2 for s2, d in fmosectors.SECTORS.items()
                    if _empty in d and d[_empty][1] not in missionlist.TYPE1_ON_DISK))
            else:
                _eg = warmap.warmap_sector_for(struct.pack("<I", _empty))
            _eok = _eg[1] is not None and not _eg[3]
            print(f"  war map: a sector whose map is one of SE's 2/3/4/5 "
                  f"placeholders reports the ROW but not on-disk (tile "
                  f"{_empty}, map {_eg[2]}) -- an empty sector, not a miss: "
                  f"{'OK' if _eok else 'FAIL'}")
            ok &= _eok

            # A miss, a short body and the revert knob all fall back, and each
            # says why -- "the same place every time" was invisible because
            # nothing logged the request.
            _miss = warmap.warmap_sector_for(struct.pack("<I", 999999))
            _short = warmap.warmap_sector_for(b"")
            flat_globals()["WARMAP_SECTORS"] = False
            _off = warmap.warmap_sector_for(struct.pack("<I", 71121))
            flat_globals()["WARMAP_SECTORS"] = True
            _fb = (_miss[1] is None and "NOT in selector 200" in _miss[4]
                   and _short[0] is None and "too short" in _short[4]
                   and _off[1] is None and "FMO_WARMAP_SECTORS=0" in _off[4])
            print(f"  war map: a miss / a short body / the revert knob each "
                  f"fall back to FMO_WARMAP_MAPS and say why: "
                  f"{'OK' if _fb else 'FAIL'}")
            ok &= _fb

            # VERIFIED: END TO END, the live sequence: open the war map on
            # sector 17, pick the one battle it offers, and the 0x013A that
            # comes back must carry map 232 -- NOT FMO_SORTIE_MAPNO's 418.
            flat_globals()["SERVE_SORTIE"] = True
            flat_globals()["SORTIE_MAPNO"] = "418"
            # THE TRAINING GATE (2026-09-12): an untrained pilot (byte 128
            # != 99) is refused at 0x0139 with the client's own failure arm;
            # the sergeant's byte opens the door. Storeless = no gate.
            s7g = session.Session("selftest:0")
            # Self-contained: a fresh store has no pilot on this account, so
            # seed one IN MEMORY (never committed) instead of depending on
            # whatever an earlier run left in the database.
            if charstore.CHAR_STORE and fmostore is not None and not s7g.roster:
                s7g._roster = [{"id": 1, "first": "Gate", "last": "Test"}]
            _gpc = s7g.playing_char() if charstore.CHAR_STORE else None
            _rg = [packet.parse(o) for o in s7g.on_packet(packet.parse(packet.build(
                sortie.MSG_SORTIE_REQ, bytes(sortie.REQ_0139_LEN), seq=0x5167, conn_id=1)))]
            _gate_ok = (charstore.TRAINING_GATE and _gpc is not None and fmostore is not None
                        and not s7g.pilot_trained()
                        and _rg and _rg[0]["msg"] == charselect.MSG_FAIL)
            # ...but NOT the training sortie itself: the sergeant's 0xE30A sends
            # 0x0139 with create 3 (2 = retraining) right after 104 [128]
            # (0x8074 0x627e), and refusing it locked every new pilot out
            _tq = bytearray(sortie.REQ_0139_LEN)
            struct.pack_into("<I", _tq, sortie.Q139_CREATE, penalty.CREATE_TRAINING)
            _rt = [packet.parse(o) for o in s7g.on_packet(packet.parse(packet.build(
                sortie.MSG_SORTIE_REQ, bytes(_tq), seq=0x5168, conn_id=1)))]
            _gate_ok = (_gate_ok and not s7g.pilot_trained()
                        and _rt and _rt[0]["msg"] != charselect.MSG_FAIL)
            if _gpc is not None and fmostore is not None:
                fmostore.set_flag_byte(_gpc, 128, 99)
                _gate_ok = _gate_ok and s7g.pilot_trained()
            print(f"  training gate: untrained pilot's 0x0139 -> message 2, its "
                  f"training sortie (create 3) passes; byte 128 = 99 opens it: "
                  f"{'OK' if _gate_ok else 'FAIL'}")
            ok &= _gate_ok
            s7c = session.Session("selftest:0")
            _cpc = s7c.playing_char() if charstore.CHAR_STORE else None
            if _cpc is not None and fmostore is not None:
                fmostore.set_flag_byte(_cpc, 128, 99)   # trained, so the door opens
            # tile 71121 is Sector 17 of ZONE 200, and the war map now looks a
            # tile up in the pilot's own zone -- an earlier step's grant left
            # "selftest" standing somewhere else, so say which zone this is
            _z7 = rooms.WORLD_ZONES.get("selftest")
            rooms.WORLD_ZONES["selftest"] = 200
            try:
                _r5f = [packet.parse(o) for o in s7c.on_packet(packet.parse(packet.build(
                    warmap.MSG_015E_REQ, struct.pack("<I", 71121) + bytes(16),
                    seq=0x5168, conn_id=1)))]
                _pl5f = _r5f[0]["payload"] if _r5f else b""
                _r13a = [packet.parse(o) for o in s7c.on_packet(packet.parse(packet.build(
                    sortie.MSG_SORTIE_REQ, bytes(sortie.REQ_0139_LEN), seq=0x5169,
                    conn_id=1)))]
                _pl13a = _r13a[0]["payload"] if _r13a else b""
            finally:
                if _z7 is None:
                    rooms.WORLD_ZONES.pop("selftest", None)
                else:
                    rooms.WORLD_ZONES["selftest"] = _z7
            _e2e = (len(_pl5f) == warmap.S15F_HEAD_LEN + warmap.S15F_ROW_LEN
                    and struct.unpack_from("<I", _pl5f, 0)[0] == 1
                    and struct.unpack_from("<I", _pl5f, warmap.S15F_HEAD_LEN)[0] == 232
                    and _r13a and _r13a[0]["msg"] == sortie.MSG_SORTIE_REPLY
                    and struct.unpack_from(
                        "<I", _pl13a, sortie.R13A_BLOCK + missionblock.MB_MAPNO)[0] == 232)
            print(f"  war map: 0x015E on sector 17 offers ONE battle (map "
                  f"{struct.unpack_from('<I', _pl5f, warmap.S15F_HEAD_LEN)[0] if len(_pl5f) > warmap.S15F_HEAD_LEN else '?'}) "
                  f"and the 0x0139 that follows sorties to MapNo "
                  f"{struct.unpack_from('<I', _pl13a, sortie.R13A_BLOCK + missionblock.MB_MAPNO)[0] if len(_pl13a) > sortie.R13A_BLOCK + 4 else '?'}, "
                  f"NOT FMO_SORTIE_MAPNO's 418: {'OK' if _e2e else 'FAIL'}")
            ok &= _e2e

            # WARNING: THE SAME SORTIE AS A GROUP MEMBER (bgflag +0x08 = 1), through
            # the real handler: live 22:20Z the group bookkeeping did str & int
            # on the map and closed the leader's connection. The reply must
            # still be 0x013A and GROUP_SORTIE must hold map 232 as an int.
            s7b = session.Session("selftest:0")
            _bpc = s7b.playing_char() if charstore.CHAR_STORE else None
            if _bpc is not None and fmostore is not None:
                fmostore.set_flag_byte(_bpc, 128, 99)
            s7b._account = _bacct = "member:selftest-group"
            _bprev = groupchannel.GROUP_OF.get(_bacct)
            groupchannel.GROUP_OF[_bacct] = 991
            _z7 = rooms.WORLD_ZONES.get("selftest")
            rooms.WORLD_ZONES["selftest"] = 200
            try:
                s7b.on_packet(packet.parse(packet.build(
                    warmap.MSG_015E_REQ, struct.pack("<I", 71121) + bytes(16),
                    seq=0x516A, conn_id=1)))
                _q139 = bytearray(sortie.REQ_0139_LEN)
                _q139[0x08] = 1
                _rb = [packet.parse(o) for o in s7b.on_packet(packet.parse(packet.build(
                    sortie.MSG_SORTIE_REQ, bytes(_q139), seq=0x516B, conn_id=1)))]
            finally:
                if _z7 is None:
                    rooms.WORLD_ZONES.pop("selftest", None)
                else:
                    rooms.WORLD_ZONES["selftest"] = _z7
                if _bprev is None:
                    groupchannel.GROUP_OF.pop(_bacct, None)
                else:
                    groupchannel.GROUP_OF[_bacct] = _bprev
            _gs = groupchannel.GROUP_SORTIE.pop(991, None)
            _gs_ok = (_rb and _rb[0]["msg"] == sortie.MSG_SORTIE_REPLY and _gs
                      and _gs["map"] == 232 and isinstance(_gs["map"], int)
                      and _gs["row"][:4] == struct.pack("<I", 232))
            print(f"  group sortie through the 0x0139 handler: 0x013A served and "
                  f"GROUP_SORTIE = map {_gs and _gs['map']!r}: "
                  f"{'OK' if _gs_ok else 'FAIL'}")
            ok &= bool(_gs_ok)

            # WARNING: THE FOLLOWER (9:10 YES): no war map opened, 0x0139 +0x00 =
            # the group's map. Live 22:33Z it was sent FMO_SORTIE_MAPNO's 418
            # while the leader fought on 267. It must get the group's map.
            s7f = session.Session("selftest:0")
            _fpc = s7f.playing_char() if charstore.CHAR_STORE else None
            if _fpc is not None and fmostore is not None:
                fmostore.set_flag_byte(_fpc, 128, 99)
            s7f._account = "member:selftest-follower"
            groupchannel.GROUP_OF[s7f._account] = 992
            groupchannel.GROUP_SORTIE[992] = {"map": 232, "sector": 200071121,
                                              "row": struct.pack("<I", 232) + bytes(104),
                                              "at": time.time(), "by": "x"}
            try:
                _qf = bytearray(sortie.REQ_0139_LEN)
                struct.pack_into("<I", _qf, 0x00, 232)
                _rf = [packet.parse(o) for o in s7f.on_packet(packet.parse(packet.build(
                    sortie.MSG_SORTIE_REQ, bytes(_qf), seq=0x516C, conn_id=1)))]
            finally:
                groupchannel.GROUP_OF.pop(s7f._account, None)
                groupchannel.GROUP_SORTIE.pop(992, None)
            _plf = _rf[0]["payload"] if _rf else b""
            _f_ok = (_rf and _rf[0]["msg"] == sortie.MSG_SORTIE_REPLY
                     and struct.unpack_from("<I", _plf, sortie.R13A_BLOCK + missionblock.MB_MAPNO)[0] == 232)
            print(f"  group follower: a 9:10 0x0139 for the group's map 232 is "
                  f"sent map "
                  f"{struct.unpack_from('<I', _plf, sortie.R13A_BLOCK + missionblock.MB_MAPNO)[0] if len(_plf) > sortie.R13A_BLOCK + 4 else '?'}"
                  f" (not FMO_SORTIE_MAPNO 418): {'OK' if _f_ok else 'FAIL'}")
            ok &= bool(_f_ok)

            # WARNING: two battle channels from one lobby are room-mates only on
            # the SAME sortie map (live 22:33Z: 267 vs 418, each blinking).
            from types import SimpleNamespace as _SN
            _rsave = (dict(groupchannel.WORLD_PEERS), dict(rooms.SORTIE_MAP))
            try:
                _ra = _SN(addr=("198.51.100.7", 1), key=b"xxbattle", left=None,
                          seen_at=time.time(), account="member:ra",
                          loc={"map": 102, "zone": 200})
                _rb2 = _SN(addr=("198.51.100.7", 2), key=b"xxbattle", left=None,
                           seen_at=time.time(), account="member:rb",
                           loc={"map": 102, "zone": 200})
                groupchannel.WORLD_PEERS.clear()
                groupchannel.WORLD_PEERS[_ra.addr] = _ra
                groupchannel.WORLD_PEERS[_rb2.addr] = _rb2
                rooms.SORTIE_MAP["member:ra"], rooms.SORTIE_MAP["member:rb"] = 267, 418
                _apart = rooms.room_mates(_ra) == []
                rooms.SORTIE_MAP["member:rb"] = 267
                _together = rooms.room_mates(_ra) == [_rb2]
            finally:
                groupchannel.WORLD_PEERS.clear(); groupchannel.WORLD_PEERS.update(_rsave[0])
                rooms.SORTIE_MAP.clear(); rooms.SORTIE_MAP.update(_rsave[1])
            print(f"  battle room: same lobby but maps 267/418 -> not mates; "
                  f"both 267 -> mates: "
                  f"{'OK' if _apart and _together else 'FAIL'}")
            ok &= _apart and _together

            # ...and with no war map opened at all, the sortie is still
            # FMO_SORTIE_MAPNO -- the kycli RESUME path, unchanged.
            s7d = session.Session("selftest:0")
            _dpc = s7d.playing_char() if charstore.CHAR_STORE else None
            if _dpc is not None and fmostore is not None:
                fmostore.set_flag_byte(_dpc, 128, 99)   # trained
            _r13b = [packet.parse(o) for o in s7d.on_packet(packet.parse(packet.build(
                sortie.MSG_SORTIE_REQ, bytes(sortie.REQ_0139_LEN), seq=0x516a, conn_id=1)))]
            _plb = _r13b[0]["payload"] if _r13b else b""
            _res = (_r13b and _r13b[0]["msg"] == sortie.MSG_SORTIE_REPLY
                    and struct.unpack_from(
                        "<I", _plb, sortie.R13A_BLOCK + missionblock.MB_MAPNO)[0] == 418)
            print(f"  war map: a sortie with NO war map opened still uses "
                  f"FMO_SORTIE_MAPNO (418), so the RESUME path is unchanged: "
                  f"{'OK' if _res else 'FAIL'}")
            ok &= _res
        finally:
            flat_globals()["MAPKIND"] = _sv_mk
            flat_globals()["SERVE_SORTIE"] = _sv_ss
            flat_globals()["SORTIE_MAPNO"] = _sv_sm
            flat_globals()["WARMAP_SECTORS"] = True
    else:
        print("  war map: sector pins SKIPPED -- fmosectors.py did not import")
        ok = False

    # 2026-09-06: the E316 pending-orders gate. lobby+0xE1A (block+0x58E) must
    # equal the rank at lobby+0x8BB (block+0x2F) or 0x61175510 returns 1, E316
    # returns 0, and every script that asks -- tag_senior, the war map's only
    # door -- refuses with "Please ask the Personnel Officer" (D64 record 68).
    _ack_fields = {off: val for _lbl, off, val, _src in status.status_fields(rank=41)}
    ack_ok = (_ack_fields.get(status.S14A_RANK) == b"\x29"
              and _ack_fields.get(status.S14A_BE1A) == b"\x29")
    print(f"  E316 orders gate: rank 41 -> block+0x{status.S14A_RANK - status.S14A_BLOCK:X} and "
          f"the acknowledged rank block+0x{status.S14A_BE1A - status.S14A_BLOCK:X} both 0x29, so "
          f"0x61175510 returns 0: {'OK' if ack_ok else 'FAIL'}")
    ok &= ack_ok
    # 2026-09-06: the client's RX buffer is 15,000 B and the bytes right after
    # it are its own TX/RX packet pointers, so an over-long frame is memory
    # corruption, not truncation. build() must refuse one outright, and the two
    # numbers must stay tied to the addresses they were read from.
    _cap_ok = (packet.CLIENT_RX_BUFFER == 15000 and packet.MAX_PAYLOAD == 14980)
    try:
        packet.build(missionlist.MSG_MISSION_LIST_REPLY, bytes(missionlist.REPLY_018E_LEN), seq=1, conn_id=1)
        _cap_ok = False                      # it must NOT have been buildable
    except ValueError:
        pass
    # and the largest body we DO serve has to still fit, or we have broken the
    # live protocol rather than protected it
    try:
        packet.build(0x01A3, bytes(lobapi.LOBAPI[0x01A2][1]), seq=1, conn_id=1)
    except ValueError:
        _cap_ok = False
    print(f"  frame cap: CLIENT_RX_BUFFER={packet.CLIENT_RX_BUFFER} (conn+0x3AE4.."
          f"0x757C), build() refuses the {missionlist.REPLY_018E_LEN}-B 0x018E that killed "
          f"the client and still passes 0x01A3 ({lobapi.LOBAPI[0x01A2][1]}B, "
          f"{packet.CLIENT_RX_BUFFER - 20 - lobapi.LOBAPI[0x01A2][1]}B spare): "
          f"{'OK' if _cap_ok else 'FAIL'}")
    ok &= _cap_ok
    # 2026-09-06: 0x018D must NOT be answered with the 22,872-zero 0x018E by
    # default -- that killed the client 2/2 (AV 0x61004361, smashed lobby
    # vtable). The default is a message-2 refusal, on the client's own seq.
    s8 = session.Session("selftest:0")
    r8d = [packet.parse(o) for o in s8.on_packet(packet.parse(packet.build(missionlist.MSG_MISSION_LIST_REQ,
                                                                           bytes(32), seq=0x5166,
                                                                           conn_id=1)))]
    if os.environ.get("FMO_ANSWER_018D", "").strip():
        # The knob is set on purpose (the size-vs-content probe runs with
        # 'zeros'), so assert what it ASKED for, not the default.
        if missionlist.ANSWER_018D == "short":
            m8d_ok = (len(r8d) == 1
                      and r8d[0]["msg"] == missionlist.MSG_MISSION_LIST_REPLY
                      and len(r8d[0]["payload"]) == missionlist.S018E_SHORT_LEN
                      and packet.HDR + missionlist.S018E_SHORT_LEN <= packet.CLIENT_RX_BUFFER
                      and r8d[0]["payload"] == bytes(missionlist.S018E_SHORT_LEN))
            _what = (f"a {missionlist.S018E_SHORT_LEN}-B 0x018E ({missionlist.S018E_ROWS} whole "
                     f"records), inside the {packet.CLIENT_RX_BUFFER}-B cap")
        elif missionlist.ANSWER_018D == "0":
            m8d_ok = r8d == []
            _what = "silence"
        else:
            m8d_ok = len(r8d) == 1 and r8d[0]["msg"] == 2
            _what = "message 2"
        print(f"  mission board: FMO_ANSWER_018D={missionlist.ANSWER_018D!r} is SET on "
              f"purpose -> {_what}: {'OK' if m8d_ok else 'FAIL ' + str(r8d)}")
    else:
        m8d_ok = (missionlist.ANSWER_018D == "refuse" and len(r8d) == 1
                  and r8d[0]["msg"] == 2 and r8d[0]["seq"] == 0x5166
                  and r8d[0]["payload"] == b"" and missionlist.REPLY_018E_LEN == 22872)
        print(f"  mission board: FMO_ANSWER_018D defaults to {missionlist.ANSWER_018D!r}, so "
              f"0x018D -> message 2 (not the {missionlist.REPLY_018E_LEN}-B 0x018E that "
              f"crashed the client 2/2): {'OK' if m8d_ok else 'FAIL ' + str(r8d)}")
    ok &= m8d_ok
    # The 0x0159 terminator. The sender's state 2 (0x61177AB5) compares ONLY
    # the reply's message id against 1, so assert the id and that we send
    # exactly one frame on the CLIENT's sequence -- a reply on the wrong
    # sequence would never be seen by 0x61199E30 at all.
    s5 = session.Session("selftest:0")
    o159 = [packet.parse(o) for o in
            s5.on_packet(packet.parse(packet.build(scriptcall.MSG_0159_REQ, bytes(scriptcall.S159_BODY_LEN),
                                                   seq=0x4321, conn_id=1)))]
    if scriptcall.ANSWER_0159 == "1":
        a159 = (len(o159) == 1 and o159[0]["msg"] == handshake.MSG_SESSION_START
                and o159[0]["seq"] == 0x4321 and o159[0]["payload"] == b"")
        print(f"  0159: answered with message {handshake.MSG_SESSION_START} on the "
              f"client's own seq, empty body (0x61177AB5 compares the id, "
              f"nothing reads the body): {'OK' if a159 else 'FAIL'}")
        ok &= a159
    # WARNING: And the knob must be able to reproduce BOTH the graceful failure and
    # the hang, or the A/B that decoded this is not repeatable.
    a159k = (scriptcall.ANSWER_0159 in ("1", "fail", "0")
             and (scriptcall.ANSWER_0159 != "0" or not o159)
             and (scriptcall.ANSWER_0159 != "fail"
                  or (len(o159) == 1 and o159[0]["msg"] == 2)))
    print(f"  0159: FMO_ANSWER_0159={scriptcall.ANSWER_0159!r} is one of 1/fail/0 and the "
          f"frames match it: {'OK' if a159k else 'FAIL'}")
    ok &= a159k
    # The 0x013D battle withdraw. State 1 of 0x61176630 reads ONLY the reply's
    # message id (id==1 = success), so assert one frame, id 1, empty body, on the
    # CLIENT's own seq -- a reply on any other seq is invisible to 0x61199E30 and
    # the machine hangs in state 1. Mirror the 0x0159 A/B assertion so the knob
    # can reproduce the graceful failure and the hang, not just the fix.
    s13d = session.Session("selftest:0")
    o13d = [packet.parse(o) for o in
            s13d.on_packet(packet.parse(packet.build(withdraw.MSG_BATTLE_WITHDRAW,
                                                     struct.pack("<I", 3),
                                                     seq=0x13D13D, conn_id=1)))]
    if withdraw.ANSWER_013D == "1":
        a13d = (len(o13d) == 1 and o13d[0]["msg"] == handshake.MSG_SESSION_START
                and o13d[0]["seq"] == 0x13D13D and o13d[0]["payload"] == b"")
        print(f"  013D: withdraw answered with message {handshake.MSG_SESSION_START} on "
              f"the client's own seq, empty body (0x611766DC reads only the id, "
              f"nothing reads the body): {'OK' if a13d else 'FAIL'}")
        ok &= a13d
    a13dk = (withdraw.ANSWER_013D in ("1", "fail", "0")
             and (withdraw.ANSWER_013D != "0" or not o13d)
             and (withdraw.ANSWER_013D != "fail"
                  or (len(o13d) == 1 and o13d[0]["msg"] == 2)))
    print(f"  013D: FMO_ANSWER_013D={withdraw.ANSWER_013D!r} is one of 1/fail/0 and the "
          f"frames match it: {'OK' if a13dk else 'FAIL'}")
    ok &= a13dk

    # Drive ONE request through the real dispatcher and return its frames --
    # the same instrument that found these defects, so the checks below test
    # the dispatcher rather than a constant.
    def _one(mid, body=b"\x07\0\0\0" + bytes(16)):
        s = session.Session("selftest:0")
        return [packet.parse(o) for o in
                s.on_packet(packet.parse(packet.build(mid, body, seq=0x777, conn_id=1)))]

    # A tiny helper used by several blocks below: does this call REFUSE?
    def _raises(fn):
        try:
            fn()
        except ValueError:
            return True
        return False

    # THE SCRAMBLE BOARD'S GROUP LIST -- community op 0x07 (static 2026-09-09).
    # The live report "I make a battle group and the board acts like none
    # exist". Driven through the real msn_reply so the check covers the
    # dispatch, not just the record builder.
    _gb_saved = list(battlegroups.BATTLE_GROUPS_MADE)
    _gb_groups = dict(battlegroups.BATTLE_GROUPS)
    try:
        del battlegroups.BATTLE_GROUPS_MADE[:]
        battlegroups.BATTLE_GROUPS.clear()
        _gb_empty = community.msn_reply("selftest", 0x07, bytes(116))
        battlegroups.BATTLE_GROUPS_MADE.append(("9.9.9.9", 7, "Lex Arden", 0))
        battlegroups.BATTLE_GROUPS["9.9.9.9"] = {"leader": "Lex Arden",
                                                 "comment": "All welcome"}
        _gb_one = community.msn_reply("selftest", 0x07, bytes(116))
        _gb_rows = community.group_board_rows()
        _gb_ok = (
            # empty: OP_END alone -- the job POPS instead of the board waiting
            [o for o, _ in _gb_empty] == [fmomsn.OP_END]
            # one group: a 0x17 page then the terminator
            and [o for o, _ in _gb_one] == [fmomsn.OP_GROUPS, fmomsn.OP_END]
            and len(_gb_rows) == 1
            and len(_gb_rows[0]) == fmomsn.GROUP_RECORD_LEN
            # KEY: the GroupID must be the one 0x0158 handed out, because that is
            # what a JOIN (0x0157) sends straight back at us.
            and struct.unpack_from("<I", _gb_rows[0], fmomsn.G_ID)[0] == 7
            and _gb_rows[0][fmomsn.G_NAME:fmomsn.G_NAME + 9] == b"Lex Arden"
            and _gb_rows[0][fmomsn.G_COMMENT:fmomsn.G_COMMENT + 11]
            == b"All welcome")
        print(f"  board list: op 0x07 empty -> OP_END alone, one group -> a "
              f"0x{fmomsn.OP_GROUPS:02X} page carrying the SAME GroupID the "
              f"0x0158 handed out: {'OK' if _gb_ok else 'FAIL'}")
        ok &= _gb_ok
    finally:
        del battlegroups.BATTLE_GROUPS_MADE[:]
        battlegroups.BATTLE_GROUPS_MADE.extend(_gb_saved)
        battlegroups.BATTLE_GROUPS.clear()
        battlegroups.BATTLE_GROUPS.update(_gb_groups)

    # blob+0x50, the leader byte -- and the TWO-PLAYER case, which is the whole
    # reason it stopped being a flat 1. Telling both pilots they are the leader
    # offers both of them Disband and neither of them Leave, so the 0x0172
    # handler could never be exercised at all.
    _gl_saved = list(battlegroups.BATTLE_GROUPS_MADE)
    try:
        del battlegroups.BATTLE_GROUPS_MADE[:]
        _member = groupchannel.group_leader_for("192.0.2.2")[0]
        battlegroups.BATTLE_GROUPS_MADE.append(("192.0.2.1:5000", 1, "Lex", 0))
        _creator = groupchannel.group_leader_for("192.0.2.1")[0]
        _still = groupchannel.group_leader_for("192.0.2.2")[0]
        _gl_ok = (groupchannel.GROUP_LEADER_MODE == "auto" and _member == 0
                  and _creator == 1 and _still == 0)
        print(f"  group leader: the host that CREATED is 1 and everyone else "
              f"is 0, so a joiner gets Leave and the owner gets Disband: "
              f"{'OK' if _gl_ok else 'FAIL'}")
        ok &= _gl_ok
    finally:
        del battlegroups.BATTLE_GROUPS_MADE[:]
        battlegroups.BATTLE_GROUPS_MADE.extend(_gl_saved)

    # The GROUP MEMBER-INFO blob's name fields. Pinned because serving them as
    # zeros produced TWO separate on-screen faults a day apart -- an in-battle
    # chat line that never rendered (worked around in chat_echo_name) and
    # Member Details listing the pilot as "-" -- and the workaround masked the
    # second one. The offsets are 0x11 apart, the same 17-byte name field the
    # trade records use.
    _gm_ok = (groupchannel.GROUP_POP_NAME1_OFF == 0x1C and groupchannel.GROUP_POP_NAME2_OFF == 0x2D
              and groupchannel.GROUP_NAME_LEN == 0x11
              and groupchannel.GROUP_POP_NAME2_OFF - groupchannel.GROUP_POP_NAME1_OFF == groupchannel.GROUP_NAME_LEN
              # and they must not collide with the fields already served
              and groupchannel.GROUP_POP_NAME2_OFF + groupchannel.GROUP_NAME_LEN <= groupchannel.GROUP_POP_LEADER_OFF)
    print(f"  group blob: names at +0x{groupchannel.GROUP_POP_NAME1_OFF:X}/"
          f"+0x{groupchannel.GROUP_POP_NAME2_OFF:X}, {groupchannel.GROUP_NAME_LEN} bytes each, clear of "
          f"the leader flag at +0x{groupchannel.GROUP_POP_LEADER_OFF:X}: "
          f"{'OK' if _gm_ok else 'FAIL'}")
    ok &= _gm_ok

    # ------------------------------------------------------------------ #
    # THE ITEM COUNTER -- 0x0169 / 0x017E (static 2026-09-09).
    # ------------------------------------------------------------------ #
    # The asymmetry is the whole point and it is what gets pinned: the SELL
    # price is on the wire, the BUY price is not, so a sale can be paid for
    # correctly and a purchase cannot be charged for at all. If a later change
    # makes these two symmetrical, this check should fail.
    def _item_body(mode, price, iid=1234, kind=7, serial=0xAABBCCDDEEFF0011):
        b = bytearray(40)
        struct.pack_into("<I", b, shop.S169_PRICE, price)
        rec = bytearray(shop.ITEM_RECORD_LEN)
        struct.pack_into("<Q", rec, shop.S169_REC_SERIAL, serial)
        struct.pack_into("<H", rec, shop.S169_REC_ID, iid)
        rec[shop.S169_REC_KIND] = kind
        rec[shop.S169_REC_MODE] = mode
        b[shop.S169_RECORD:shop.S169_RECORD + shop.ITEM_RECORD_LEN] = rec
        return bytes(b)

    _sell = _one(shop.MSG_ITEM_SELL, _item_body(0, 5000))
    _disc = _one(shop.MSG_ITEM_SELL, _item_body(1, 0))
    _buy = _one(shop.MSG_ITEM_BUY, _item_body(0, 0))
    _m, _pr, _ser, _iid, _kd = shop.parse_item_request(_item_body(1, 0, 4321, 9))
    _it_ok = (
        # the record decodes at the offsets the SENDER copies it to
        _m == 1 and _pr == 0 and _iid == 4321 and _kd == 9
        and _ser == 0xAABBCCDDEEFF0011
        and shop.parse_item_request(_item_body(0, 777))[1] == 777
        # sell and discard are both accepted with message 1 -- 0x6117A893
        and _sell and _sell[0]["msg"] == handshake.MSG_SESSION_START
        and _sell[0]["seq"] == 0x777 and _sell[0]["payload"] == b""
        and _disc and _disc[0]["msg"] == handshake.MSG_SESSION_START
        # WARNING: BUY IS REFUSED, and refused with an id that is NOT 0x017F --
        # the debit lives inside the 0x017F-matched path, so this is what
        # keeps money from moving.
        and len(_buy) == 1 and _buy[0]["msg"] != shop.MSG_ITEM_BUY_REPLY
        and _buy[0]["msg"] == 2 and _buy[0]["seq"] == 0x777
        and shop.ANSWER_017E == "fail")
    print(f"  item counter: sell/discard accepted with message "
          f"{handshake.MSG_SESSION_START}, BUY refused with an id that is NOT "
          f"0x{shop.MSG_ITEM_BUY_REPLY:04X} (the debit is inside that path): "
          f"{'OK' if _it_ok else 'FAIL'}")
    ok &= _it_ok

    # ------------------------------------------------------------------ #
    # THE BATTLE-GROUP OPERATIONS (static 2026-09-09).
    # ------------------------------------------------------------------ #
    # Three defects fixed together, all found by driving every request the
    # client can send through this dispatcher and seeing which ones fall to
    # "no handler" -- an instrument, unlike the grep that missed six of them.
    #   (a) 0x0172 LEAVE was never in GROUP_ACK_IDS   -> the dialog hung
    #   (b) 0x0190 wants 0x0191, and we sent 1        -> it FAILED, quietly
    #   (c) 0x0157 JOIN had no handler at all         -> the dialog hung
    # The naming of 0x0171/0x0173/0x0179 was also wrong, as a rotation; that
    # is a comment fix, pinned here so it cannot rot back.
    _g_leave = _one(0x0172)
    _g_cmt = _one(battlegroups.MSG_GROUP_COMMENT_REQ)
    _g_ok = (0x0172 in battlegroups.GROUP_ACK_IDS
             and battlegroups.MSG_GROUP_COMMENT_REQ not in battlegroups.GROUP_ACK_IDS
             and len(_g_leave) == 1
             and _g_leave[0]["msg"] == handshake.MSG_SESSION_START
             and _g_leave[0]["seq"] == 0x777
             # 0x0190 -> 0x0191, NOT 1. Message 1 here takes 0x6116E16C's jne
             # and posts the failure event with a numeric code.
             and len(_g_cmt) == 1
             and _g_cmt[0]["msg"] == battlegroups.MSG_GROUP_COMMENT_REPLY == 0x0191
             and _g_cmt[0]["seq"] == 0x777
             and _g_cmt[0]["payload"] == b""
             and battlegroups.GROUP_OP_NAMES[0x0172].startswith("LEAVE")
             and battlegroups.GROUP_OP_NAMES[0x0171].startswith("KICK")
             and battlegroups.GROUP_OP_NAMES[0x0179].startswith("CHANGE LEADER"))
    print(f"  group ops: 0x0172 LEAVE acked with message {handshake.MSG_SESSION_START}, "
          f"0x0190 answered with 0x{battlegroups.MSG_GROUP_COMMENT_REPLY:04X} and NOT 1: "
          f"{'OK' if _g_ok else 'FAIL'}")
    ok &= _g_ok

    # 0x0157 JOIN -> an EMPTY 0x0158 (its poll reads only the id), plus the
    # 0x0174 attach without which the join is inert. The offsets are the point:
    # 0x0174 is NOT the 120-byte LoginGroup entry 0x0158 carries, and reusing
    # group_entry() would put the endpoint 8 bytes early.
    def _join_reply_marked():
        """The 0x0158 body a JOIN gets with FMO_GROUP_BOARD=mark armed."""
        _sv = community.GROUP_BOARD_MARK
        try:
            community.GROUP_BOARD_MARK = True
            return _one(battlegroups.MSG_0157_REQ,
                        struct.pack("<I", 7) + bytes(8))[0]["payload"]
        finally:
            community.GROUP_BOARD_MARK = _sv

    _j = _one(battlegroups.MSG_0157_REQ, struct.pack("<I", 7) + bytes(8))
    _at = grouplogin.group_attach_body(7, "127.0.0.1", 61300, 1)
    _j_ok = (len(_j) == (2 if grouplogin.GROUP_ATTACH else 1)
             and _j[0]["msg"] == battlegroups.MSG_0158_REPLY and _j[0]["seq"] == 0x777
             # WARNING: NOT empty, and payload+0x00 MUST be 0. An empty body left the
             # board reading its receive buffer's leftovers, announced a sortie
             # on a standing-by group, and put the player in map 418.
             and len(_j[0]["payload"]) == battlegroups.REPLY_0158_JOIN_LEN
             and _j[0]["payload"][battlegroups.S158_ON_SORTIE] == 0
             # and it stays 0 even in mark mode -- a marked run that announced
             # a sortie would drag the player into map 418 mid-measurement.
             and _join_reply_marked()[battlegroups.S158_ON_SORTIE] == 0
             and struct.unpack_from("<I", _join_reply_marked(), 0x590)[0]
             == 0x590
             # long enough for the Battle Map Information panel's own reads
             and len(_j[0]["payload"]) >= 0x590 + 0xD8
             and len(_at) == grouplogin.G174_BODY_LEN == 0x7C
             and grouplogin.G174_ENDPOINT != grouplogin.GRP_ENDPOINT          # the trap, pinned
             and struct.unpack_from("<I", _at, grouplogin.G174_ID)[0] == 7
             and _at[grouplogin.G174_TYPE] == 1
             # NETWORK order: the consumer copies the struct into a sockaddr
             # verbatim, so port and address must read correctly AS-IS.
             and int.from_bytes(_at[grouplogin.G174_ENDPOINT + 2:grouplogin.G174_ENDPOINT + 4],
                                "big") == 61300
             and bytes(_at[grouplogin.G174_ENDPOINT + 4:grouplogin.G174_ENDPOINT + 8])
             == bytes((127, 0, 0, 1))
             # GroupID 0 is dropped by the arm, so refuse to build one
             and _raises(lambda: grouplogin.group_attach_body(0, "1.2.3.4", 1)))
    if grouplogin.GROUP_ATTACH:
        _j_ok &= (_j[1]["msg"] == grouplogin.MSG_GROUP_ATTACH
                  and _j[1]["seq"] == pushes.QUEUE_SEQ
                  and grouplogin.MSG_GROUP_ATTACH in pushes.LOBBY_PUSH_ALL
                  and struct.unpack_from("<I", _j[1]["payload"],
                                         grouplogin.G174_ID)[0] == 7)
    print(f"  0157: JOIN answered with a {battlegroups.REPLY_0158_JOIN_LEN}B 0x{battlegroups.MSG_0158_REPLY:04X} whose "
          f"payload+0x00 = 0 (NOT on a sortie), and "
          f"attached by a 0x{grouplogin.MSG_GROUP_ATTACH:04X} whose endpoint is at "
          f"+0x{grouplogin.G174_ENDPOINT:02X} (NOT the entry's +0x{grouplogin.GRP_ENDPOINT:02X}), "
          f"network order: {'OK' if _j_ok else 'FAIL'}")
    ok &= _j_ok

    # SE'S PLATOON RULES (2026-09-30, battlegroups / groupchannel / sortie /
    # settlement.platoon_battle_settle). Each check fails if its rule is cut.
    _pl_sv = (dict(groupchannel.GROUP_MEMBERS), dict(groupchannel.GROUP_OF),
              dict(groupchannel.GROUP_READY), dict(groupchannel._joined_at),
              list(battlegroups.BATTLE_GROUPS_MADE), dict(battlegroups.GROUP_CREATOR_ACCOUNT),
              dict(battlegroups.GROUP_STATE), dict(battlegroups.GROUP_BATTLE),
              dict(battlegroups.PLATOON_CTX), dict(groupchannel.GROUP_SORTIE))
    try:
        for _d in (groupchannel.GROUP_MEMBERS, groupchannel.GROUP_OF, groupchannel.GROUP_READY,
                   groupchannel._joined_at, battlegroups.GROUP_CREATOR_ACCOUNT,
                   battlegroups.GROUP_STATE, battlegroups.GROUP_BATTLE,
                   battlegroups.PLATOON_CTX, groupchannel.GROUP_SORTIE):
            _d.clear()
        del battlegroups.BATTLE_GROUPS_MADE[:]
        # (1) JOIN RULES: a live member of group 81 cannot join 82 (9:0); a
        # STALE one (relogged: our 0x0155 lists no group) is moved as before;
        # a full group refuses (5:33); a live member cannot create (D92 218).
        groupchannel.group_join(81, "pl:a")
        _jr1 = groupchannel.group_join_refusal(82, "pl:a")
        _jm1 = groupchannel.group_join(82, "pl:a")
        _jc1 = groupchannel.group_create_refusal("pl:a")
        groupchannel._joined_at["pl:a"] -= groupchannel.GROUP_LIVE_S + 60
        _jm2 = groupchannel.group_join(82, "pl:a")
        for _i in range(groupchannel.GROUP_CAP - 1):
            groupchannel.group_join(83, f"pl:c{_i}")
        _jcap_last = groupchannel.group_join(83, "pl:clast")
        _jfull = groupchannel.group_join_refusal(83, "pl:over")
        _join_ok = (_jr1 is not None and _jr1[0] == -14116 and _jm1 is False
                    and _jc1 is not None and _jc1[0] == -14116
                    and _jm2 is True and groupchannel.GROUP_OF.get("pl:a") == 82
                    and "pl:a" not in groupchannel.GROUP_MEMBERS.get(81, [])
                    and _jcap_last is True and groupchannel.GROUP_CAP == 10
                    and _jfull is not None and _jfull[0] == groupchannel.JOIN_CODE_FULL == -30018
                    and groupchannel.group_join(83, "pl:over") is False
                    and len(groupchannel.GROUP_MEMBERS[83]) == groupchannel.GROUP_CAP
                    and (groupchannel.group_join_refusal(84, "pl:x", cost=1, required=2) or (None,))[0] == -30023
                    and groupchannel.group_join_refusal(84, "pl:x", cost=None, required=2) is None)
        print(f"  platoon join: in another live group -> refused 9:0 (-14116) and NOT "
              f"moved, a stale membership is moved, member {groupchannel.GROUP_CAP + 1} "
              f"refused 5:33 (-30018), create refused while in a group: "
              f"{'OK' if _join_ok else 'FAIL'}")
        ok &= _join_ok
        # (2) THE CREATE FORM: +0x10C bonus, +0x114 Total Battles, +0x115
        # Required B.G.Cost (NOT +0x110, the voice flag); the client's B.G.Cost
        # formula on known level sets.
        _cf = battlegroups.parse_create_form(bytes(0x10C) + struct.pack("<II", 5000, 1) + b"\x03\x02")
        _form_ok = (_cf == {"bonus": 5000, "voice": 1, "total": 3, "required": 2}
                    and battlegroups.bg_cost_from_levels([10, 10, 10, 10], [10]) == 2
                    and battlegroups.bg_cost_from_levels([1, 1, 1, 1], []) == 1
                    and battlegroups.bg_cost_from_levels([40, 40, 40, 40], [40]) == 9
                    and battlegroups.bg_cost_from_levels([100] * 4, [100]) == 25)
        print(f"  platoon create form: bonus +0x10C, Total Battles +0x114, Required "
              f"B.G.Cost +0x115, B.G.Cost steps: {'OK' if _form_ok else 'FAIL'}")
        ok &= _form_ok
        # (2b) B.G.COST FROM THE STORED SETUP (2026-09-30): the item records
        # resolve by serial, slot i's level is byte +1 of the part master
        # record (fmo-part-levels.tsv), and the rules that read the cost:
        # join 5:37 (-30023), sortie 3:11 (-31111), 3:5 (-31102), 3:8 (-31108).
        _bc_saved = dict(battlegroups.PILOT_BG_COST)
        try:
            battlegroups.PILOT_BG_COST.clear()
            # A setup: body 0x11:1, arms 0x21:1, legs 0x31:1 x2, a gun 0x12:1
            # at item 4, a backpack 0x41:1 at item 10; levels 20 (parts) and
            # 40 (gun): thr 30, P 4 x 30 / 4 = 30, + 40 = 70 -> cost 7. Item 5
            # (serial 99, a level-60 gun) counts only while the owned list has
            # it: W 60/40/20, thr 45, 45 + 60 = 105 -> cost 14.
            _lv = {(0x11, 1): 20, (0x21, 1): 20, (0x31, 1): 20, (0x12, 1): 40,
                   (0x41, 1): 20, (0x22, 1): 60}
            _blk = bytearray(inventory.reply_0166(parts=[
                (0, 0x11, 1), (1, 0x21, 1), (2, 0x31, 1), (3, 0x31, 1),
                (4, 0x12, 1), (10, 0x41, 1)], slots=1))
            _o5 = inventory.SETUP_ITEM_OFF + 5 * inventory.INV_ENTRY_LEN
            _blk[_o5:_o5 + inventory.INV_ENTRY_LEN] = inventory.item_record(99, 1, 0x22)
            _own = {}
            for _r in inventory.inventory_from_setups(bytes(_blk)):
                _own[struct.unpack_from("<Q", _r, 0)[0]] = (
                    struct.unpack_from("<H", _r, inventory.ITEM_ID)[0], _r[inventory.ITEM_KIND])
            _own_no99 = {k: v for k, v in _own.items() if k != 99}
            _setup_ok = (battlegroups.setup_bg_cost(bytes(_blk), 1, _own_no99, _lv) == 7
                         and battlegroups.setup_bg_cost(bytes(_blk), 1, _own, _lv) == 14
                         and battlegroups.setup_bg_cost(bytes(_blk), 1, _own, {}) is None
                         and battlegroups.SLOT_ITEM == (1, 0, 3, 2, 5, 4, 7, 6, 8, 9, 10))
            _skip_pl = _fmodata_skip("fmo-part-levels.tsv")
            if _skip_pl is None:
                # SE's own starters are all level-1 parts -> cost 1; an Arco 17
                # frame (level 20, no weapon): 20 + 15 = 35 -> cost 4.
                _setup_ok &= (all(battlegroups.pilot_bg_cost(
                    {"setups": inventory.reply_0166(parts=_p).hex()}) == 1
                    for _p in inventory.STARTER_SETUPS.values())
                    and battlegroups.PART_LEVELS.get((0x11, 2)) == 20
                    and battlegroups.pilot_bg_cost({"setups": inventory.reply_0166(parts=[
                        (0, 0x11, 2), (1, 0x21, 2), (2, 0x31, 2), (3, 0x31, 2)]).hex()}) == 4)
            # JOIN (5:37): group 91 wants 3; a 2 is refused, a 3 joins, the
            # creator and a sitting member are never judged on it.
            battlegroups.register_group(91, "pl:cre", {"required": 3})
            battlegroups.GROUP_CREATOR_ACCOUNT[91] = "pl:cre"
            battlegroups.PILOT_BG_COST.update({"pl:lo": 2, "pl:hi": 3, "pl:cre": 1})
            _jc = groupchannel.group_join_refusal(91, "pl:lo")
            _join_cost_ok = ((_jc or (None,))[0] == -30023
                             and groupchannel.group_join(91, "pl:lo") is False
                             and groupchannel.group_join_refusal(91, "pl:hi") is None
                             and groupchannel.group_join_refusal(91, "pl:cre") is None
                             and groupchannel.group_join_refusal(91, "pl:unknown-cost") is None)
            # TOTAL: the live members summed; one unknown -> unknown.
            _tot_ok = (battlegroups.group_total_cost(91, ["pl:hi", "pl:cre"]) == 4
                       and battlegroups.group_total_cost(91, ["pl:hi", "pl:nobody"]) is None)
            # SORTIE: Required 3:11, total over the sector 3:5, below the
            # sector minimum or the mission minimum 3:8; 0 / None skip.
            _v = battlegroups.cost_sortie_verdict
            _sortie_ok = ((_v(2, required=3) or (None,))[0] == -31111
                          and (_v(5, total=12, sector_max=10) or (None,))[0] == -31102
                          and (_v(4, sector_min=5) or (None,))[0] == -31108
                          and (_v(4, mission_min=5) or (None,))[0] == -31108
                          and _v(5, total=10, required=3, sector_max=10, sector_min=5,
                                 mission_min=5) is None
                          and _v(None, total=None, required=3, sector_max=1,
                                 mission_min=9) is None)
            _mm = battlegroups.mission_min_cost
            _sortie_ok &= (_mm(1, [{"title": "A"}, {"title": "B"}], {(1, "A"): 7, (1, "B"): 5}) == 5
                           and _mm(1, [{"title": "A"}, {"title": "C"}], {(1, "A"): 7}) == 0
                           and _mm(1, [], {(1, "A"): 7}) == 0)
            if _fmodata_skip("fmo-missions.tsv") is None:
                # AI/F00/D94 241: 'New High-Mobility Weapon Sighted' is the 9
                _sortie_ok &= battlegroups.MISSION_BG_COST.get(
                    (1, "New High-Mobility Weapon Sighted")) == 9
            # THE SESSION'S 0x0139 PATH (platoon_sortie_verdict, which on_sortie
            # calls): a level-1 starter pilot (cost 1) in group 91 (Required 3)
            # on a group sortie -> -31111; solo on tile 69120 whose war sector
            # says minimum 5 -> -31108; the same tile with no minimum -> None.
            _svs = (charstore.CHAR_STORE, warstate.war_state)
            _wire_ok = False
            try:
                charstore.CHAR_STORE = charstore.CHAR_STORE or "selftest-not-written"
                _secs = {"69120": {"bg_max": 0, "bg_min": 5}}
                warstate.war_state = lambda: type("W", (), {"data": {"sectors": _secs}})()
                _pcs = {"first": "Sel", "setups": inventory.reply_0166(
                    parts=inventory.STARTER_SETUPS[(1, 1)]).hex()}
                _ssv = session.Session.__new__(session.Session)
                _ssv.peer, _ssv.ip, _ssv._account = "selftest-bgc", "selftest-bgc", "pl:bgc"
                _ssv.sector = None
                _ssv.playing_char = lambda: _pcs
                battlegroups.GROUP_CREATOR_ACCOUNT[92] = "pl:bgc"
                battlegroups.register_group(92, "pl:bgc", {"required": 3})
                groupchannel.GROUP_OF["pl:bgc"] = 92
                _w1 = _ssv.platoon_sortie_verdict({"bgflag": 1}, None)
                groupchannel.GROUP_OF.pop("pl:bgc", None)
                _ssv.sector = (69120, 3, 108)
                _w2 = _ssv.platoon_sortie_verdict({"bgflag": 0}, None)
                _secs["69120"]["bg_min"] = 0
                _w3 = _ssv.platoon_sortie_verdict({"bgflag": 0}, None)
                _wire_ok = ((_w1 or (None,))[0] == -31111
                            and (_w2 or (None,))[0] == -31108 and _w3 is None
                            and battlegroups.PILOT_BG_COST.get("pl:bgc") == 1)
            finally:
                charstore.CHAR_STORE, warstate.war_state = _svs
            _sortie_ok &= _wire_ok
            _cost_ok = _setup_ok and _join_cost_ok and _tot_ok and _sortie_ok
            print(f"  B.G.Cost: setup levels by serial (unowned adds none) "
                  f"{'OK' if _setup_ok else 'FAIL'}"
                  + (f" [{_skip_pl}]" if _skip_pl else "")
                  + f", join 5:37 {'OK' if _join_cost_ok else 'FAIL'}, platoon total "
                  f"{'OK' if _tot_ok else 'FAIL'}, sortie 3:11/3:5/3:8 + mission "
                  f"minimum {'OK' if _sortie_ok else 'FAIL'}")
            ok &= _cost_ok
        finally:
            battlegroups.PILOT_BG_COST.clear()
            battlegroups.PILOT_BG_COST.update(_bc_saved)
        # (3) B.G.BONUS: leader only, raise only, cap 999,999; the sortie needs
        # MORE money than the bonus (2:101 / 2:107); the split is even and
        # the leader's cut carries the remainder and its own id (8:72).
        battlegroups.BATTLE_GROUPS_MADE.append(("pl-peer", 90, "Lead", 0))
        battlegroups.register_group(90, "pl:lead", {"bonus": 1000, "total": 2, "required": 1})
        groupchannel.group_join(90, "pl:lead")
        groupchannel.group_join(90, "pl:m1")
        groupchannel.group_join(90, "pl:m2")
        _b_ok = (battlegroups.bonus_request(90, "pl:m1", 5000) is not None
                 and (battlegroups.bonus_request(90, "pl:lead", 500) or (None,))[0] == -1
                 and battlegroups.bonus_request(90, "pl:lead", 1_000_000) is not None
                 and battlegroups.bonus_request(90, "pl:lead", 3001) is None
                 and battlegroups.GROUP_STATE[90]["bonus"] == 3001
                 and (battlegroups.bonus_sortie_verdict(3001, 3001) or (None,))[0] == -14111
                 and (battlegroups.bonus_sortie_verdict(3001, 3500, 600) or (None,))[0] == -14120
                 and battlegroups.bonus_sortie_verdict(3001, 3002) is None
                 and battlegroups.BG_BONUS_MAX == 999999)
        groupchannel.GROUP_READY["pl:lead"] = (0, battlegroups.CONT_OFF)
        _r1 = battlegroups.group_battle_begin(90, "pl:lead", 267, 0x1111)
        battlegroups.group_battle_join(90, "pl:m1", 267, 600)
        battlegroups.group_battle_join(90, "pl:m2", 267, 600)
        _sl = battlegroups.platoon_settle("pl:lead", True, own_id=0x1111)
        _s1 = battlegroups.platoon_settle("pl:m1", False, own_id=0x2222)
        _s2 = battlegroups.platoon_settle("pl:m2", True, own_id=0x1111)
        _b_ok &= (_sl["share"] == 1001 and _s1["share"] == 1000 and _s2["share"] == 1000
                  and _sl["share"] + _s1["share"] + _s2["share"] == 3001
                  and _sl["payer_id"] == 0x1111 and _s1["payer_id"] == 0x1111
                  and _s2["payer_id"] != 0x1111
                  and battlegroups.platoon_settle("pl:m1", True) == {})
        print(f"  platoon B.G.Bonus: leader only, raise only, cap 999,999; sortie "
              f"refused at or below it (2:101 -14111, 2:107 -14120); H$ 3001 split "
              f"1001/1000/1000 win or lose, payer id = the leader's (8:72 for "
              f"them, 8:61 for the rest): {'OK' if _b_ok else 'FAIL'}")
        ok &= _b_ok
        # (4) AUTO-DISBAND: Total Battles 2 with Continuation off -> battle 1
        # carries +0x0F4 (8:58, one to go), battle 2 disbands the group.
        _r2 = battlegroups.group_battle_begin(90, "pl:lead", 267, 0x1111)
        battlegroups.group_battle_join(90, "pl:m1", 267, 600)
        _d1 = battlegroups.platoon_settle("pl:m1", True)
        _d2 = battlegroups.platoon_settle("pl:lead", True)
        _ad_ok = (_r1["left"] == 1 and _sl["auto_disband"] and not _sl["disbanded"]
                  and _r2["left"] == 0 and not _d1["auto_disband"] and _d1["disbanded"]
                  and 90 not in groupchannel.GROUP_MEMBERS
                  and groupchannel.GROUP_OF.get("pl:m2") is None
                  and not any(g[1] == 90 for g in battlegroups.BATTLE_GROUPS_MADE)
                  and _d2["share"] == 1501 and not _d2["disbanded"])
        print(f"  platoon auto-disband: 2 battles, do not continue -> +0x0F4 after "
              f"battle 1, group gone after battle 2: {'OK' if _ad_ok else 'FAIL'}")
        ok &= _ad_ok
        # (5) PLATOON EXP: +10% per extra member (ours) on a win, every row
        # scaled alike (the Pilot row stays its share of the job exp), +0x0F2
        # filled through a real settlement.
        _px = battleend.platoon_exp_rows([(3, 1000), (12, 1000)], 120)
        groupchannel.group_join(91, "pl:s1")
        battlegroups.register_group(91, "pl:s1", {})
        battlegroups.group_battle_begin(91, "pl:s1", 300, 7)
        battlegroups.group_battle_join(91, "pl:s2", 300, 600)
        battlegroups.group_battle_join(91, "pl:s3", 300, 600)
        _ps = session.Session.__new__(session.Session)
        _ps.peer, _ps._account, _ps.battle_settlement, _ps.last_0159 = "selftest-platoon", "pl:s2", None, b""
        _ps.playing_char = lambda: {"id": 5, "first": "Pl"}
        _ps.stored_money = lambda: (0, 0)
        _ps.credit_money = lambda why, money=0, contribution=0: (money, contribution)
        _ps.credit_class_exp = lambda why, rows: {}
        _ps.commit = lambda what: None
        _pm, _prw = _ps.platoon_battle_settle(True, 0, [(3, 1000), (12, 1000)], {"kill_bonus_hs": 0})
        _pbody = battleend.battle_end_body(**_ps.platoon_end)
        _px_ok = (battlegroups.platoon_exp_pct(1) == 100 and battlegroups.platoon_exp_pct(3) == 120
                  and battlegroups.platoon_exp_pct(10) == 150
                  and battlegroups.platoon_exp_pct(3, per=0) == 100
                  and _px == [(3, 1200), (12, 1200)]
                  and _prw == [(3, 1200), (12, 1200)] and _pm == 0
                  and struct.unpack_from("<h", _pbody, battleend.S14C_PLATOON_PCT)[0] == 120)
        print(f"  platoon exp: 3 members -> 120% on the job AND Pilot rows, +0x0F2 = "
              f"120 (8:45): {'OK' if _px_ok else 'FAIL'}")
        ok &= _px_ok
        # (6) JOIN TIMING: 5-minute window, 10 a side, 120% -> 50% for the late
        # side, 100% for the creating side, the 20-minute wait for an opponent.
        _t = 10_000.0
        _jv = sortie.join_verdict
        _creator = [(0, _t - 100, 55)]
        _tm_ok = (_jv([], 0, None, _t)[0] is None
                  and _jv(_creator, 0, None, _t)[0] is None and _jv(_creator, 0, None, _t)[2] is None
                  and _jv([(0, _t - 400, 55)], 0, None, _t)[0] == sortie.JOIN_CODE_LATE == -31107
                  and _jv([(0, _t - 400, 55)], 0, 55, _t)[0] is None
                  and _jv([(0, _t - 900, 55)], 1, None, _t)[0] is None
                  and _jv([(0, _t - 900, 55)], 1, None, _t)[2] == 100
                  and _jv([(0, _t - 1300, 55)], 1, None, _t)[0] == -31107
                  and _jv([(0, _t - 150, 55), (1, _t - 10, 56)], 1, None, _t)[2] == 85
                  and _jv([(0, _t - 400, 55), (1, _t - 10, 56)], 1, None, _t)[0] == -31107
                  and _jv([(0, _t - 10, 55)] * 10, 0, 55, _t)[0] == sortie.JOIN_CODE_SIDE_FULL == -31100
                  and sortie.join_pct(0) == 120 and sortie.join_pct(300) == 50
                  and sortie.join_pct(900) == 50 and sortie.join_pct(150) == 85)
        print(f"  join-in: {sortie.JOIN_WINDOW} s window (3:7 -31107), 10 a side (3:3 "
              f"-31100), late side 120% -> 50%, creating side 100%, "
              f"{sortie.JOIN_PVP_WAIT} s wait for the first opponent: "
              f"{'OK' if _tm_ok else 'FAIL'}")
        ok &= _tm_ok
    finally:
        for _d, _v in ((groupchannel.GROUP_MEMBERS, _pl_sv[0]), (groupchannel.GROUP_OF, _pl_sv[1]),
                       (groupchannel.GROUP_READY, _pl_sv[2]), (groupchannel._joined_at, _pl_sv[3]),
                       (battlegroups.GROUP_CREATOR_ACCOUNT, _pl_sv[5]),
                       (battlegroups.GROUP_STATE, _pl_sv[6]), (battlegroups.GROUP_BATTLE, _pl_sv[7]),
                       (battlegroups.PLATOON_CTX, _pl_sv[8]), (groupchannel.GROUP_SORTIE, _pl_sv[9])):
            _d.clear()
            _d.update(_v)
        battlegroups.BATTLE_GROUPS_MADE[:] = _pl_sv[4]

    # ------------------------------------------------------------------ #
    # 0x015A -- THE BATTLE RESULT PUSH (static 2026-09-09).
    # ------------------------------------------------------------------ #
    # Three things are pinned, and each of them is a way this could fail
    # SILENTLY -- 0x015A is a push, so a wrong id, a wrong length or a wrong
    # offset is dropped at 0x6117F86D with nothing in any log:
    #   (a) 0x015A is actually in the dispatcher's catalogue. This is the check
    #       that would have existed all along if LOBBY_PUSH_IDS had not been
    #       written up as "the seven ids the dispatcher will even look at".
    #   (b) the deltas sit at the offsets the arm reads (payload+0x410 /
    #       +0x414), verified THROUGH build() at frame+0x424 / +0x428 -- the
    #       Play Time lesson, where the body was right and the frame offset was
    #       the thing that mattered.
    #   (c) the +0xAD8 record byte is 1 when we have no record and 0 when we do.
    #       Getting that backwards is the one failure that DESTROYS data rather
    #       than doing nothing: a zero byte with an empty record blanks the
    #       client's own lobby+0x6E4E mission block.
    _rp_empty = resultpush.result_push_body(money=1500, contribution=7)
    _rp_frame = packet.build(resultpush.MSG_RESULT_PUSH, _rp_empty, pushes.QUEUE_SEQ, 1)
    _rec = bytes((i * 7 + 3) & 0xFF for i in range(scriptcall.S159_BODY_LEN))
    _rp_rec = resultpush.result_push_body(record=_rec, money=-250)
    _rp_ok = (resultpush.MSG_RESULT_PUSH == 0x015A
              and resultpush.MSG_RESULT_PUSH in pushes.LOBBY_PUSH_ALL           # (a)
              and resultpush.MSG_RESULT_PUSH in pushes.LOBBY_PUSH_DIRECT
              and len(_rp_empty) == resultpush.S15A_BODY_LEN == 0xAD9
              and resultpush.S15A_KEEP_RECORD == resultpush.S15A_OWNED + resultpush.S15A_OWNED_LEN
              and struct.unpack_from("<H", _rp_frame, 6)[0] == 0x015A
              and struct.unpack_from("<i", _rp_frame, 0x424)[0] == 1500   # (b)
              and struct.unpack_from("<i", _rp_frame, 0x428)[0] == 7
              and _rp_empty[resultpush.S15A_KEEP_RECORD] == 1                        # (c)
              and _rp_rec[resultpush.S15A_KEEP_RECORD] == 0
              # KEY: The echo is NOT verbatim and cannot be: the counts, the row
              # areas and both deltas are fields INSIDE the 1,432-byte record
              # (+0x004 .. +0x41A all sit below +0x598), so authoring a result
              # necessarily overwrites part of what the client sent. What has
              # to survive untouched is everything else -- the interior we have
              # not decoded and must not disturb.
              and _rp_rec[resultpush.S15A_B41A + 1:scriptcall.S159_BODY_LEN]
              == _rec[resultpush.S15A_B41A + 1:scriptcall.S159_BODY_LEN]
              and _rp_rec[:resultpush.S15A_N_GRANT] == _rec[:resultpush.S15A_N_GRANT]
              and struct.unpack_from("<i", _rp_rec, resultpush.S15A_MONEY)[0] == -250)
    print(f"  015A: id in the push catalogue, {resultpush.S15A_BODY_LEN}B body, deltas at "
          f"frame+0x424/+0x428, +0x{resultpush.S15A_KEEP_RECORD:X} = 1 without a record "
          f"and 0 with one: {'OK' if _rp_ok else 'FAIL'}")
    ok &= _rp_ok

    # The refusals. Each of these would otherwise be a silent corruption: a
    # short record still gets copied into lobby+0x6E4E (blanking the rest), and
    # a 33rd row runs straight into the money delta.
    _rp_ref = (_raises(lambda: resultpush.result_push_body(record=b"\0" * 16))
               and _raises(lambda: resultpush.result_push_body(
                   granted=[b"\0" * resultpush.S15A_ITEM_LEN] * (resultpush.S15A_MAX_ITEMS + 1)))
               and _raises(lambda: resultpush.result_push_body(
                   spent=[b"\0" * 8] * (resultpush.S15A_MAX_ITEMS + 1)))
               and _raises(lambda: resultpush.result_push_body(granted=[b"\0" * 20]))
               and _raises(lambda: resultpush.result_push_body(
                   owned=b"\0" * (resultpush.S15A_OWNED_LEN + 1))))
    print(f"  015A: a short record, a 33rd row and an oversize owned table are "
          f"all REFUSED, not truncated: {'OK' if _rp_ref else 'FAIL'}")
    ok &= _rp_ref

    # The row areas, against the client's own arithmetic: 0x61177B30 indexes
    # lobby+0x10D9 + count*24, and the count area has to stop before the money
    # delta or a grant would overwrite the pay.
    _rp_rows = (resultpush.S15A_GRANT + resultpush.S15A_MAX_ITEMS * resultpush.S15A_ITEM_LEN == resultpush.S15A_SPEND
                and resultpush.S15A_SPEND + resultpush.S15A_MAX_ITEMS * 8 == resultpush.S15A_MONEY
                and resultpush.S15A_OWNED == scriptcall.S159_BODY_LEN)
    _g = [struct.pack("<IIHBB", 0x11112222, 0x33334444, 0x0501, 4, 0)
          + bytes(resultpush.S15A_ITEM_LEN - 12)]
    _rp_body_g = resultpush.result_push_body(granted=_g, spent=[b"\x01" * 8], money=1)
    _rp_rows &= (struct.unpack_from("<I", _rp_body_g, resultpush.S15A_N_GRANT)[0] == 1
                 and struct.unpack_from("<I", _rp_body_g, resultpush.S15A_N_SPEND)[0] == 1
                 and _rp_body_g[resultpush.S15A_GRANT:resultpush.S15A_GRANT + resultpush.S15A_ITEM_LEN] == _g[0]
                 and _rp_body_g[resultpush.S15A_SPEND:resultpush.S15A_SPEND + 8] == b"\x01" * 8)
    print(f"  015A: {resultpush.S15A_MAX_ITEMS} grant rows exactly fill "
          f"+0x{resultpush.S15A_GRANT:03X}..+0x{resultpush.S15A_SPEND:03X} and {resultpush.S15A_MAX_ITEMS} "
          f"spend rows fill +0x{resultpush.S15A_SPEND:03X}..+0x{resultpush.S15A_MONEY:03X}, so a "
          f"full result cannot overwrite the pay: "
          f"{'OK' if _rp_rows else 'FAIL'}")
    ok &= _rp_rows

    # 0x014B -- the message-window push, built as the POSITIVE CONTROL for the
    # queue-push transport 0x015A rides. Pinned: it is in the catalogue, the
    # text is cp932 and NUL-terminated at the offset the arm reads, the kind is
    # range-checked against the arm's own jump table, and overlong text is
    # REFUSED rather than allowed to run into the sender-name field the chat
    # path reads.
    _an = lobbymessage.lobby_message_body("Welcome to Huffman.", kind=2)
    _an_frame = packet.build(lobbymessage.MSG_LOBBY_MESSAGE, _an, pushes.QUEUE_SEQ, 1)
    _an_ok = (lobbymessage.MSG_LOBBY_MESSAGE == 0x014B
              and lobbymessage.MSG_LOBBY_MESSAGE in pushes.LOBBY_PUSH_ALL
              and len(_an) == lobbymessage.S14B_BODY_LEN == 0x414
              and struct.unpack_from("<H", _an_frame, 6)[0] == 0x014B
              and _an[lobbymessage.S14B_KIND] == 2
              and _an_frame[packet.HDR + lobbymessage.S14B_TEXT:packet.HDR + lobbymessage.S14B_TEXT + 5] == b"Welco"
              and _an[lobbymessage.S14B_TEXT + len("Welcome to Huffman.")] == 0
              and _raises(lambda: lobbymessage.lobby_message_body("x" * 2000))
              and _raises(lambda: lobbymessage.lobby_message_body("hi", kind=0))
              and _raises(lambda: lobbymessage.lobby_message_body("hi", kind=8))
              # cp932, not utf-8: a byte count check would pass either way, so
              # assert the ENCODING by a character that differs between them.
              and lobbymessage.lobby_message_body("日")[lobbymessage.S14B_TEXT:lobbymessage.S14B_TEXT + 2]
              == "日".encode("cp932"))
    print(f"  014B: message-window push in the catalogue, {lobbymessage.S14B_BODY_LEN}B, "
          f"cp932 NUL-terminated text at +0x{lobbymessage.S14B_TEXT:03X}, kind range-checked "
          f"against the arm's jump table: {'OK' if _an_ok else 'FAIL'}")
    ok &= _an_ok

    # The armed announce, through the real keepalive handler. ONCE per session
    # is the assertion that matters: the keepalive arrives every few seconds,
    # and an announce that re-fires would paper the message window over --
    # exactly the shape of the FE "skill points are a budget, not a balance"
    # bug (a recorded regression), where a per-event serve became a per-tick mint.
    _an_saved = lobbymessage.ANNOUNCE
    try:
        lobbymessage.ANNOUNCE = "Welcome to Huffman."
        s14b = session.Session("selftest:0")
        _ka = packet.parse(packet.build(charselect.MSG_KEEPALIVE, struct.pack("<II", 0, 1),
                                        seq=pushes.QUEUE_SEQ, conn_id=1))
        _o1 = [packet.parse(o) for o in s14b.on_packet(_ka)]
        _o2 = list(s14b.on_packet(_ka))
        a14b = (len(_o1) == 1 and _o1[0]["msg"] == lobbymessage.MSG_LOBBY_MESSAGE
                and _o1[0]["seq"] == pushes.QUEUE_SEQ
                and len(_o1[0]["payload"]) == lobbymessage.S14B_BODY_LEN
                and _o1[0]["payload"][lobbymessage.S14B_TEXT:].split(b"\0")[0]
                == b"Welcome to Huffman."
                and _o2 == [])
        print(f"  014B: armed, the FIRST keepalive pushes the announce on the "
              f"queue seq and every keepalive after it pushes NOTHING: "
              f"{'OK' if a14b else 'FAIL'}")
        ok &= a14b
    finally:
        lobbymessage.ANNOUNCE = _an_saved

    # The story gates tool: an edit for an ONLINE pilot is queued on the
    # session and applied at its keepalive, on the session's thread -- the
    # record changes, the session commits, and a 0x015A carries the new flag
    # block in its owned table with no record and no money. commit is stubbed:
    # this must never reach a real character store.
    if gatetool.fmogates is not None:
        sg = session.Session("selftest:gates")
        sg._account = "selftest:gates"
        sg._roster = [{"id": 7, "first": "Gate", "last": "Test", "rank": 24, "flags": "",
                       "gender": 1, "nation_byte": 1}]
        _commits = []
        sg.commit = _commits.append
        _ka = packet.parse(packet.build(charselect.MSG_KEEPALIVE, struct.pack("<II", 0, 1),
                                        seq=pushes.QUEUE_SEQ, conn_id=1))
        try:
            sg.on_packet(_ka)                       # now it counts as online
            _st, _r1 = gatetool.edit({"pilot": "selftest:gates|7", "op": "byte",
                                      "index": 173, "value": 99}, "tester")
            _st2, _r2 = gatetool.edit({"pilot": "selftest:gates|7", "op": "byte",
                                       "index": 300, "value": 1}, "tester")
            _unchanged = sg._roster[0]["flags"] == ""
            _o = [packet.parse(o) for o in sg.on_packet(_ka)]
            _push = [o for o in _o if o["msg"] == resultpush.MSG_RESULT_PUSH]
            _pl = _push[0]["payload"] if _push else b""
            _fo = resultpush.S15A_OWNED + status.S14A_FLAGS11 - status.S14A_OWNED
            agt = (_r1.get("queued") and _unchanged and not _r2.get("ok")
                   and fmostore.flags_bytes(sg._roster[0]["flags"])[173] == 99
                   and len(_commits) == 1 and "tester" in _commits[0]
                   and len(_push) == 1 and _push[0]["seq"] == pushes.QUEUE_SEQ
                   and _pl[_fo + 173] == 99
                   and _pl[resultpush.S15A_KEEP_RECORD] == 1
                   and struct.unpack_from("<i", _pl, resultpush.S15A_MONEY)[0] == 0
                   and list(sg.on_packet(_ka)) == [])
        finally:
            trade.LIVE_SESSIONS.pop(sg.ip, None)
        print(f"  story gates: an online pilot's edit waits for the keepalive, is "
              f"committed there and pushed as 0x015A with the flag in its owned "
              f"table; a bad edit is refused at once: {'OK' if agt else 'FAIL'}")
        ok &= bool(agt)

    # The ARMED path, end to end through the real withdraw handler -- because
    # an unset knob never exercises the code that has the bug (the
    # FMO_LOBAPI_MARK import-order precedent). Patched by hand rather than by
    # environment so the check runs on every selftest, armed or not.
    _saved = (resultpush.RESULT_PUSH, resultpush.RESULT_MONEY, resultpush.RESULT_CONTRIB)
    try:
        resultpush.RESULT_PUSH, resultpush.RESULT_MONEY, resultpush.RESULT_CONTRIB = True, 2500, 11
        s15a = session.Session("selftest:0")
        s15a.last_0159 = _rec
        o15a = [packet.parse(o) for o in
                s15a.on_packet(packet.parse(packet.build(withdraw.MSG_BATTLE_WITHDRAW,
                                                         struct.pack("<I", 0),
                                                         seq=0x15A15A, conn_id=1)))]
        a15a = (withdraw.ANSWER_013D != "1" or (
            len(o15a) == 2
            # the withdraw ack is UNCHANGED and still first: arming the result
            # must not disturb the reply the client is actually polling for.
            and o15a[0]["msg"] == handshake.MSG_SESSION_START
            and o15a[0]["seq"] == 0x15A15A and o15a[0]["payload"] == b""
            and o15a[1]["msg"] == resultpush.MSG_RESULT_PUSH
            and o15a[1]["seq"] == pushes.QUEUE_SEQ
            and len(o15a[1]["payload"]) == resultpush.S15A_BODY_LEN
            and struct.unpack_from("<i", o15a[1]["payload"],
                                   resultpush.S15A_MONEY)[0] == 2500
            and struct.unpack_from("<i", o15a[1]["payload"],
                                   resultpush.S15A_CONTRIB)[0] == 11
            and o15a[1]["payload"][resultpush.S15A_B41A + 1:scriptcall.S159_BODY_LEN]
            == _rec[resultpush.S15A_B41A + 1:scriptcall.S159_BODY_LEN]
            and o15a[1]["payload"][resultpush.S15A_KEEP_RECORD] == 0
            # WARNING: the owned table (2026-09-11): the arm copies +0x598..+0xAD7
            # onto lobby+0x8C8 unconditionally, and that is the 0x014A's
            # payload+0x3C slice -- flags bitmap included. Zeros there wiped
            # the flags live (tutorial replay, dead lobby). The push must
            # carry the same bytes the login's 0x014A carries.
            and o15a[1]["payload"][resultpush.S15A_OWNED:resultpush.S15A_KEEP_RECORD]
            == status.reply_014a(char={})[status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN]))
        # The flag bitmap really is INSIDE the owned slice, and a pilot's
        # flags ride it: byte 128 = 99 (the counters' "pilot registered"
        # gate) and bit 173 must be where the client's copy puts them.
        _ow = status.reply_014a(rank=0, flags=[173, 183],
                                flag_bytes={128: 99})[status.S14A_OWNED:
                                                status.S14A_OWNED + resultpush.S15A_OWNED_LEN]
        _owb = resultpush.result_push_body(owned=_ow, money=1)
        _fo = resultpush.S15A_OWNED + (status.S14A_FLAGS11 - status.S14A_OWNED)
        a15c = (status.S14A_OWNED <= status.S14A_FLAGS11
                and status.S14A_FLAGS11 + status.S14A_FLAGS11_LEN <= status.S14A_OWNED + resultpush.S15A_OWNED_LEN
                and (0x8C8 + (status.S14A_FLAGS11 - status.S14A_OWNED)) == 0xB88
                and _owb[_fo + 128] == 99
                and _owb[_fo + (173 >> 3)] & (1 << (173 & 7))
                and _owb[_fo + (183 >> 3)] & (1 << (183 & 7))
                and _owb[resultpush.S15A_KEEP_RECORD] == 1)
        print(f"  015A: the owned table carries the 0x014A flag bitmap at the "
              f"offset the arm's lobby+0x8C8 copy puts on lobby+0xB88 "
              f"(byte 128 = 99, bits 173/183 set): {'OK' if a15c else 'FAIL'}")
        ok &= a15c
        print(f"  015A: armed, a withdraw emits the ack FIRST and then the "
              f"result push on the queue seq, carrying the deltas and the "
              f"client's own record: {'OK' if a15a else 'FAIL'}")
        ok &= a15a
        # A result that pays nothing is refused: on screen it is
        # indistinguishable from one that never arrived, which is precisely
        # the reading this project keeps having to retract.
        resultpush.RESULT_MONEY, resultpush.RESULT_CONTRIB = 0, 0
        s15b = session.Session("selftest:0")
        o15b = s15b.on_packet(packet.parse(packet.build(withdraw.MSG_BATTLE_WITHDRAW, bytes(4),
                                                        seq=0x15B15B, conn_id=1)))
        a15b = withdraw.ANSWER_013D != "1" or len(o15b) == 1
        print(f"  015A: a zero/zero result is NOT pushed (it could not be told "
              f"apart from a dropped one): {'OK' if a15b else 'FAIL'}")
        ok &= a15b
        # FMO_BATTLE_DUMMY_KILL: the destroy DEPOP is a real cmd-8 status-2
        # record for the dummy id -- the message that wrecks a killed wanzer.
        # record_depop returns a battle-channel record; verify the DEPOP body
        # (last CMD_DEPOP_BODY_LEN bytes): cmd 8, unitid at +0, status 2.
        _kd = fmoworld.record_depop(0x2222, status=fmoworld.DEPOP_DESTROYED)
        _kbody = fmoworld.parse_depop(_kd[-fmoworld.CMD_DEPOP_BODY_LEN:])
        a_kill = (fmoworld.DEPOP_DESTROYED == 2
                  and _kbody["status"] == 2 and _kbody["unit_id"] == 0x2222)
        print(f"  DUMMY KILL: FMO_BATTLE_DUMMY_KILL emits a cmd "
              f"{fmoworld.CMD_DEPOP} DEPOP status {fmoworld.DEPOP_DESTROYED} for the "
              f"dummy id (the wreck message): {'OK' if a_kill else 'FAIL'}")
        ok &= a_kill
        # FMO_BATTLE_DUMMY_KILL parse: "<secs>[:<status>]", default status 3
        # (REMOVE = the reliable kill; status 2 wreck is effect-gated live).
        a_kp = (battlepop._parse_dummy_kill("") == (0.0, fmoworld.DEPOP_REMOVE)
                and battlepop._parse_dummy_kill("30") == (30.0, fmoworld.DEPOP_REMOVE)
                and battlepop._parse_dummy_kill("30:2") == (30.0, fmoworld.DEPOP_DESTROYED)
                and battlepop._parse_dummy_kill("5:3") == (5.0, fmoworld.DEPOP_REMOVE))
        print(f"  DUMMY KILL: the knob parses <secs>[:<status>], default REMOVE, "
              f"and 2/3 select wreck/remove: {'OK' if a_kp else 'FAIL'}")
        ok &= a_kp
        # FMO_BATTLE_REFEREE: a fire (cmd 128 kind 3) within range counts a hit;
        # the threshold destroys the enemy AND completes the destroy objective.
        import types as _types
        _sv_ref, _sv_obj = battlepop.REFEREE, battleend.OBJECTIVE
        try:
            battlepop.REFEREE = {"hits": 3, "range": 40.0, "cone": 0.0}
            battleend.OBJECTIVE = ("destroy", 0x2222, None)
            referee.BATTLE_STATE.pop("ref:0", None)
            _ch = _types.SimpleNamespace(
                dummy_id=0x2222, dummy_pos=(64.0, 5.0, 64.0), pos=(66.0, 5.0, 64.0),
                rot=0.0, dummy_kill_sent=False, referee_hits=0, pending=[],
                addr=("ref", 0))
            _b1 = referee.referee_shot(_ch, ("ref", 0))   # 1/3
            _b2 = referee.referee_shot(_ch, ("ref", 0))   # 2/3
            _b3 = referee.referee_shot(_ch, ("ref", 0))   # 3/3 -> kill
            _st2 = referee.BATTLE_STATE["ref"]
            a_ref = (_b1 == ["Enemy hit (1/3)"] and _b2 == ["Enemy hit (2/3)"]
                     and "Enemy destroyed!" in _b3
                     and _ch.dummy_kill_sent is True
                     and any(t == 0x2222 for t, _w in _st2.get("kills", []))
                     and bool(_st2.get("objective_done"))
                     and len(_ch.pending) == 1)
            # out of range -> no hit
            referee.BATTLE_STATE.pop("ref2", None)
            _far = _types.SimpleNamespace(
                dummy_id=0x2222, dummy_pos=(64.0, 5.0, 64.0), pos=(200.0, 5.0, 200.0),
                rot=0.0, dummy_kill_sent=False, referee_hits=0, pending=[], addr=("ref2", 0))
            a_far = (referee.referee_shot(_far, ("ref2", 0)) == [] and _far.referee_hits == 0)
            print(f"  REFEREE: 3 in-range shots destroy the enemy, complete the "
                  f"destroy objective and queue one DEPOP; an out-of-range shot "
                  f"is ignored: {'OK' if (a_ref and a_far) else 'FAIL'}")
            ok &= (a_ref and a_far)
        finally:
            battlepop.REFEREE, battleend.OBJECTIVE = _sv_ref, _sv_obj
            referee.BATTLE_STATE.pop("ref", None); referee.BATTLE_STATE.pop("ref2", None)
    finally:
        resultpush.RESULT_PUSH, resultpush.RESULT_MONEY, resultpush.RESULT_CONTRIB = _saved
    # The 0x0137 link-death resume. State 9 of kycli_lobmain reads ONLY the
    # reply's message id (== 0x0138 advances into the sortie handshake), so the
    # contract mirrors withdraw exactly: one frame, id 0x0138, empty body, on
    # the CLIENT's own seq. Same A/B knob so the graceful failure and the hang
    # are both reproducible, not just the fix.
    s137 = session.Session("selftest:0")
    o137 = [packet.parse(o) for o in
            s137.on_packet(packet.parse(packet.build(resume.MSG_RESUME_REQ,
                                                     struct.pack("<II", 424242, 1),
                                                     seq=0x137137, conn_id=1)))]
    if resume.ANSWER_0137 == "1":
        a137 = (len(o137) == 1 and o137[0]["msg"] == resume.MSG_RESUME_REPLY
                and o137[0]["seq"] == 0x137137 and o137[0]["payload"] == b"")
        print(f"  0137: resume answered with message 0x{resume.MSG_RESUME_REPLY:04X} "
              f"on the client's own seq, empty body (0x6117BA34 reads only the "
              f"id): {'OK' if a137 else 'FAIL'}")
        ok &= a137
    a137k = (resume.ANSWER_0137 in ("1", "fail", "0")
             and (resume.ANSWER_0137 != "0" or not o137)
             and (resume.ANSWER_0137 != "fail"
                  or (len(o137) == 1 and o137[0]["msg"] == 2)))
    print(f"  0137: FMO_ANSWER_0137={resume.ANSWER_0137!r} is one of 1/fail/0 and the "
          f"frames match it: {'OK' if a137k else 'FAIL'}")
    ok &= a137k
    # And the follow-up leg the withdraw's state 2 sends (0x0150 word 0xFFFD)
    # must already resolve to a 0x0153, or the withdraw completes the ack and
    # then hangs on the SECOND poll. Assert the existing 0x0150 handler answers
    # the 0xFFFD sentinel with a 0x0153 on the client's seq.
    s13d2 = session.Session("selftest:0")
    o150 = [packet.parse(o) for o in
            s13d2.on_packet(packet.parse(packet.build(zoneentry.MSG_0150_REQ,
                                                      struct.pack("<H", 0xFFFD) + bytes(10),
                                                      seq=0x515, conn_id=1)))]
    a150 = bool(o150) and o150[0]["msg"] == zoneentry.MSG_0150_REPLY \
        and o150[0]["seq"] == 0x515
    print(f"  013D: the withdraw follow-up 0x0150 (word 0xFFFD) still resolves "
          f"to a 0x{zoneentry.MSG_0150_REPLY:04X} on the client's seq (second leg served): "
          f"{'OK' if a150 else 'FAIL'}")
    ok &= a150
    # KEY: The 2026-09-04 lesson: the zone push must ride the GRANT, not only the
    # 0x01AC poll, because the grant's lobby reset zeroes lobby+0x7724 and the
    # client may never poll again in-world.
    if zonecontrol.ZONE_CONTROL:
        s6 = session.Session("selftest:0")
        req = bytearray(move.MOVE_REQ_LEN)
        struct.pack_into("<II", req, move.M16D_FIELD_00, move.MOVE_CATEGORY_BRIEFING, 0)
        gm = [packet.parse(o) for o in
              s6.on_packet(packet.parse(packet.build(move.MSG_MOVE_REQ, bytes(req), seq=0x77,
                                                     conn_id=1)))]
        zgrant = (gm[0]["msg"] == zoneentry.MSG_0150_REPLY
                  and gm[-1]["msg"] == zonecontrol.MSG_ZONE_CONTROL
                  and gm[-1]["seq"] == pushes.QUEUE_SEQ
                  and len(gm[-1]["payload"]) == zonecontrol.ZONE_CONTROL_LEN)
        print(f"  zone: a MOVE grant carries the 0x{zonecontrol.MSG_ZONE_CONTROL:04X} "
              f"push BEHIND the 0x0153, because the grant wipes lobby+0x7724: "
              f"{'OK' if zgrant else 'FAIL'}")
        ok &= zgrant
        # KEY: AND WORLD ENTRY IS A SECOND, SEPARATE 0x0153 EMIT SITE. The
        # 2026-09-04 fix was applied to the move handler alone and the very
        # next live run lost the table again, because entry comes through
        # 0x0150. Assert BOTH sites, by id, so a third run cannot repeat it.
        s7 = session.Session("selftest:0")
        we = [packet.parse(o) for o in
              s7.on_packet(packet.parse(packet.build(zoneentry.MSG_0150_REQ, bytes(8), seq=0x55,
                                                     conn_id=1)))]
        zentry = (we[0]["msg"] == zoneentry.MSG_0150_REPLY
                  and any(o["msg"] == zonecontrol.MSG_ZONE_CONTROL for o in we[1:])
                  and we[-1]["seq"] == pushes.QUEUE_SEQ
                  and len(we[-1]["payload"]) == zonecontrol.ZONE_CONTROL_LEN)
        print(f"  zone: WORLD ENTRY (0x0150 -> 0x0153) carries it too -- the "
              f"other emit site, missed by the first fix: "
              f"{'OK' if zentry else 'FAIL'}")
        ok &= zentry

    # 0x0151, the Change Area request (LIVE 2026-09-04). The poll arm
    # 0x611796EF compares the reply id against 0x153 and nothing else, and it
    # only ever sees a reply on the sequence the request carried -- so assert
    # the id, the sequence, and that the granted MapKind is the zone ASKED for
    # rather than FMO_MAPKIND, which is the whole point of the message.
    s8 = session.Session("selftest:0")
    req151 = bytearray(areachange.AREA_CHANGE_LEN)
    struct.pack_into("<H", req151, areachange.AREA_CHANGE_ZONE_OFF, 513)
    o151 = [packet.parse(o) for o in
            s8.on_packet(packet.parse(packet.build(areachange.MSG_AREA_CHANGE_REQ, bytes(req151),
                                                   seq=0x100B, conn_id=1)))]
    if areachange.AREA_CHANGE == "0":
        a151 = not o151
    else:
        a151 = (len(o151) == 1 and o151[0]["msg"] == zoneentry.MSG_0150_REPLY
                and o151[0]["seq"] == 0x100B
                and len(o151[0]["payload"]) == zoneentry.REPLY_0153_LEN
                and struct.unpack_from("<H", o151[0]["payload"],
                                       zoneentry.R153_MAPKIND)[0] == 513)
    print(f"  0151: Change Area for zone 513 -> a 0x0153 on the request's own "
          f"seq carrying MapKind=513 (not FMO_MAPKIND={zoneentry.MAPKIND}): "
          f"{'OK' if a151 else 'FAIL'}")
    ok &= a151
    # WARNING: The granted MapNo must NOT silently become a zone id: we have no map for
    # a warzone, and a MapNo with no resource on disk is the 0x611250A2 crash.
    if areachange.AREA_CHANGE != "0":
        _mnok = struct.unpack_from("<I", o151[0]["payload"],
                                   zoneentry.R153_SETUP + zoneentry.SU_MAPNO)[0]
        m151 = _mnok in zoneentry.VALID_MAPNOS or bool(areachange.AREA_CHANGE_MAPNO)
        print(f"  0151: the grant keeps a REAL MapNo ({_mnok}), so the zone id "
              f"never reaches the resource loader: {'OK' if m151 else 'FAIL'}")
        ok &= m151

    # The `all` token must reproduce the CLIENT's own D83 flags, not force 1s:
    # serving a 1 where D83 has 0 cannot open the zone (gate A consults D83
    # first) and would only make the log claim something selectable that is not.
    _all = zonecontrol.parse_zone_control(["all"])
    _n1 = sorted(z for z, o, _u in _all if o == 1)
    _n2 = sorted(z for z, _o, u in _all if u == 1)
    a_ok = (_all == list(zonecontrol.ZONE_ROWS_D83)
            and _n1 == [100, 101, 102, 107, 108, 109, 200, 201, 202, 207,
                        407, 505, 509, 513, 600]
            and _n2 == [207, 300, 301, 302, 307, 308, 309, 400, 401, 402,
                        407, 505, 509, 513, 600]
            and len(set(z // 100 for z in _n1)) == 5)
    print(f"  zone: `all` = the {len(zonecontrol.ZONE_ROWS_D83)} D83-allowed rows, 15 per "
          f"nation across 5 kinds, flags copied not forced: "
          f"{'OK' if a_ok else 'FAIL'}")
    ok &= a_ok
    # WARNING: A destination outside VALID_MAPNOS must be REFUSED, not served: an
    # unknown type-2 id is a zero-length resource and the 0x611250A2 crash.
    _keep, _why = areachange.area_change_mapno(513, 121)
    _guard = _keep == 121
    if areachange.AREA_CHANGE_MAPNO.lower() == "auto":
        _auto = areachange.area_change_destinations()
        # auto pairs every zone EXCEPT the 600..607 crash band (see the fix in
        # area_change_destinations), and every destination is a real type-2 map.
        _expected = {z for z, _o, _u in zonecontrol.ZONE_ROWS_D83
                     if not areachange.UNSUBSTITUTED_BAND[0] <= z <= areachange.UNSUBSTITUTED_BAND[1]}
        _guard = (set(_auto) == _expected
                  and all(v in zoneentry.VALID_MAPNOS for v in _auto.values())
                  and len(set(_auto.values())) == min(len(zoneentry.VALID_MAPNOS),
                                                      len(_expected)))
    print(f"  0151: every destination stays inside VALID_MAPNOS "
          f"(FMO_AREA_CHANGE_MAPNO={areachange.AREA_CHANGE_MAPNO!r}): "
          f"{'OK' if _guard else 'FAIL'}")
    ok &= _guard
    # And an explicitly bad one is refused rather than passed through.
    import os as _os
    _saved = _os.environ.get("FMO_AREA_CHANGE_MAPNO")
    try:
        flat_globals()["AREA_CHANGE_MAPNO"] = "513:999"
        _bad, _bwhy = areachange.area_change_mapno(513, 121)
        _bad_ok = _bad == 121 and "REFUSED" in _bwhy
    finally:
        flat_globals()["AREA_CHANGE_MAPNO"] = _saved.strip() if _saved else ""
    print(f"  0151: a destination the install does not have is refused, not "
          f"served: {'OK' if _bad_ok else 'FAIL'}")
    ok &= _bad_ok

    # WARNING: The 600..607 guard. Measured crash 2026-09-04: MapKind 600 with MapNo
    # 121 took the client down, because script_id_for does not substitute in
    # that band and the per-MapKind script ran against the wrong map. Assert
    # BOTH arms -- refuse when unpaired, grant when the operator paired it --
    # or the guard is either useless or a wall.
    s9 = session.Session("selftest:0")
    r600 = bytearray(areachange.AREA_CHANGE_LEN)
    struct.pack_into("<H", r600, areachange.AREA_CHANGE_ZONE_OFF, 600)
    o600 = [packet.parse(o) for o in
            s9.on_packet(packet.parse(packet.build(areachange.MSG_AREA_CHANGE_REQ, bytes(r600),
                                                   seq=0x1, conn_id=1)))]
    paired = 600 in areachange.area_change_destinations()
    if areachange.AREA_CHANGE == "0":
        g600 = not o600
    elif areachange.AREA_CHANGE_STRICT and not paired:
        g600 = (len(o600) == 1 and o600[0]["msg"] != zoneentry.MSG_0150_REPLY)
    else:
        g600 = (len(o600) == 1 and o600[0]["msg"] == zoneentry.MSG_0150_REPLY)
    print(f"  0151: MapKind 600..607 is refused while unpaired "
          f"(STRICT={areachange.AREA_CHANGE_STRICT}, paired={paired}): "
          f"{'OK' if g600 else 'FAIL'}")
    ok &= g600
    # A zone OUTSIDE the band is unaffected by the guard.
    s10 = session.Session("selftest:0")
    r513 = bytearray(areachange.AREA_CHANGE_LEN)
    struct.pack_into("<H", r513, areachange.AREA_CHANGE_ZONE_OFF, 513)
    o513 = [packet.parse(o) for o in
            s10.on_packet(packet.parse(packet.build(areachange.MSG_AREA_CHANGE_REQ, bytes(r513),
                                                    seq=0x2, conn_id=1)))]
    g513 = (not o513) if areachange.AREA_CHANGE == "0" else \
        (len(o513) == 1 and o513[0]["msg"] == zoneentry.MSG_0150_REPLY)
    print(f"  0151: a zone outside the band is untouched by the guard: "
          f"{'OK' if g513 else 'FAIL'}")
    ok &= g513
    # WARNING: THE 2026-09-04 LIVE CRASH: `auto` must NOT pair the 600..607 band, or
    # the STRICT guard treats it as "deliberately paired" and grants MapKind
    # 600 against a lobby MapNo -- which took the client down. Assert env-
    # independently that auto excludes the whole band (and still pairs a normal
    # zone), so those zones fall through to the graceful refusal.
    _saved_auto = flat_globals()["AREA_CHANGE_MAPNO"]
    try:
        flat_globals()["AREA_CHANGE_MAPNO"] = "auto"
        _auto = areachange.area_change_destinations()
        _band = [z for z in range(areachange.UNSUBSTITUTED_BAND[0],
                                  areachange.UNSUBSTITUTED_BAND[1] + 1) if z in _auto]
        _pairs_normal = any(z in _auto for z, _o, _u in zonecontrol.ZONE_ROWS_D83
                            if not areachange.UNSUBSTITUTED_BAND[0] <= z
                            <= areachange.UNSUBSTITUTED_BAND[1])
        auto_ok = not _band and _pairs_normal
    finally:
        flat_globals()["AREA_CHANGE_MAPNO"] = _saved_auto
    print(f"  0151: FMO_AREA_CHANGE_MAPNO=auto excludes the 600..607 crash band "
          f"(paired in band: {_band}) yet still pairs normal zones: "
          f"{'OK' if auto_ok else 'FAIL'}")
    ok &= auto_ok

    # ------------------------------------------------------------------ #
    # 2026-09-05 sweep. Each block pins one fix from that pass.
    # WARNING: The starter wanzer takes its NATION from creation +0x28 (nation_byte,
    # else raw), NEVER the swapped `nation` key (= gender, the swapped-key hazard).
    # Molly-shaped record (gender 2 / nation 1 / class 1) must dress O.C.U.
    # class 1 (Arco, body id 1); Remy-shaped (raw +0x28 = 2) U.S.N. (Husky, 51).
    _save_ld = flat_globals().get("load_roster")
    try:
        _molly = {"id": 1, "first": "Molly", "last": "Test", "nation": 2,
                  "sex": 1, "gender": 2, "nation_byte": 1, "cls": 1}
        _praw = bytearray(0x50)
        _praw[0x26], _praw[0x28], _praw[0x39] = 1, 2, 1
        _paul = {"id": 1, "first": "Remy", "last": "Test", "nation": 1,
                 "sex": 2, "cls": 1, "raw": bytes(_praw).hex()}
        _pl_nat = (popnation.character_nation(_molly)[0], popnation.character_nation(_paul)[0])
        _nat_ok = (_pl_nat == (1, 2)
                   and popnation.character_nation({"id": 1, "raw_0177": "00" * 56})[0]
                   is None and popnation.character_nation({})[0] is None)
        _ids = []
        for _rec in (_molly, _paul):
            flat_globals()["load_roster"] = lambda a, _r=_rec: [_r]
            _o = [packet.parse(o) for o in session.Session("selftest:0").on_packet(packet.parse(
                packet.build(inventory.MSG_0165_REQ, bytes(16), seq=0x165, conn_id=1)))]
            _ids.append(struct.unpack_from(
                "<H", _o[0]["payload"], inventory.SETUP_ITEM_OFF + inventory.ITEM_ID)[0])
        _wz_ok = (not charstore.CHAR_STORE) or _ids == [inventory.STARTER_SETUPS[(1, 1)][0][2],
                                                        inventory.STARTER_SETUPS[(2, 1)][0][2]]
    finally:
        flat_globals()["load_roster"] = _save_ld
    print(f"  0165: the starter wanzer's nation comes from creation +0x28 "
          f"(nation_byte / raw), not the swapped `nation` key: "
          f"{'OK' if _nat_ok and _wz_ok else 'FAIL ' + str((_pl_nat, _ids))}")
    ok &= _nat_ok and _wz_ok

    # WARNING: The script-choice instrument reads the nation the client HOLDS
    # (0x014A payload+0x30 <- FMO_STATUS_NATION, only when FMO_START_STATUS
    # sends the block), not the roster's swapped key.
    _sv = (flat_globals()["SERVE_START_STATUS"], flat_globals()["STATUS_NATION"])
    try:
        flat_globals()["SERVE_START_STATUS"], flat_globals()["STATUS_NATION"] = False, 1
        _a = zoneentry.script_nation()[0]
        flat_globals()["SERVE_START_STATUS"] = True
        _b = zoneentry.script_nation()[0]
        _c = ("substituted away" in zoneentry.describe_script_choice(509)
              if zoneentry.FIELD_18 == 1 else True)
        _d = "AS IS" in zoneentry.describe_script_choice(600)      # the unsubstituted band
    finally:
        flat_globals()["SERVE_START_STATUS"], flat_globals()["STATUS_NATION"] = _sv
    _sn_ok = _a == 0 and _b == 1 and _c and _d
    print(f"  0153: the script-choice line takes the nation from 0x014A "
          f"(FMO_START_STATUS + FMO_STATUS_NATION), not the roster: "
          f"{'OK' if _sn_ok else 'FAIL'}")
    ok &= _sn_ok

    # WARNING: 0x0151 is the THIRD 0x0153 emit site: a paired area change into a
    # different lobby map must record the new MapNo for this host (spawn row
    # + room) and carry the zone push behind the grant; an UNPAIRED one must
    # keep the map the player is STANDING IN (WORLD_MAPS), not FMO_MAPNO.
    if areachange.AREA_CHANGE != "0":
        _sv151 = (flat_globals()["AREA_CHANGE_MAPNO"], flat_globals()["ZONE_CONTROL"],
                  dict(rooms.WORLD_MAPS), dict(groupchannel.WORLD_PEERS))
        try:
            flat_globals()["AREA_CHANGE_MAPNO"] = "513:122"
            flat_globals()["ZONE_CONTROL"] = ["live"]
            rooms.WORLD_MAPS["selftest"] = 121
            _r = bytearray(areachange.AREA_CHANGE_LEN)
            struct.pack_into("<H", _r, areachange.AREA_CHANGE_ZONE_OFF, 513)
            _o = [packet.parse(o) for o in session.Session("selftest:0").on_packet(packet.parse(
                packet.build(areachange.MSG_AREA_CHANGE_REQ, bytes(_r), seq=0x151, conn_id=1)))]
            _bk_ok = (len(_o) == 2 and _o[0]["msg"] == zoneentry.MSG_0150_REPLY
                      and struct.unpack_from("<I", _o[0]["payload"],
                                             zoneentry.R153_SETUP + zoneentry.SU_MAPNO)[0] == 122
                      and rooms.WORLD_MAPS.get("selftest") == 122
                      and _o[1]["msg"] == zonecontrol.MSG_ZONE_CONTROL
                      and _o[1]["seq"] == pushes.QUEUE_SEQ)
            flat_globals()["AREA_CHANGE_MAPNO"] = ""
            flat_globals()["ZONE_CONTROL"] = []
            rooms.WORLD_MAPS["selftest"] = 121
            _o2 = [packet.parse(o) for o in session.Session("selftest:0").on_packet(packet.parse(
                packet.build(areachange.MSG_AREA_CHANGE_REQ, bytes(_r), seq=0x152, conn_id=1)))]
            _cur_ok = (len(_o2) == 1 and struct.unpack_from(
                "<I", _o2[0]["payload"], zoneentry.R153_SETUP + zoneentry.SU_MAPNO)[0] == 121
                and rooms.WORLD_MAPS.get("selftest") == 121)
        finally:
            flat_globals()["AREA_CHANGE_MAPNO"] = _sv151[0]
            flat_globals()["ZONE_CONTROL"] = _sv151[1]
            rooms.WORLD_MAPS.clear()
            rooms.WORLD_MAPS.update(_sv151[2])
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_sv151[3])
        print(f"  0151: a PAIRED area change records the new MapNo for the host "
              f"and carries the zone push behind the grant (3rd emit site): "
              f"{'OK' if _bk_ok else 'FAIL'}")
        ok &= _bk_ok
        print(f"  0151: an UNPAIRED area change keeps the map the player is "
              f"STANDING IN (WORLD_MAPS), not FMO_MAPNO={zoneentry.MAPNO}: "
              f"{'OK' if _cur_ok else 'FAIL'}")
        ok &= _cur_ok

    # WARNING: The self-POP seeds chan.pos with the spawn it SERVES (the per-map
    # row), so a room-mate who has not moved yet is relayed at their real
    # spawn rather than the global FMO_UDP_POP_POS.
    if fmoworld:
        _svp = (popsweep.POP, popself.POP_AFTER, dict(popsweep.POP_POS_MAP), dict(rooms.WORLD_MAPS),
                dict(groupchannel.WORLD_PEERS))
        try:
            popsweep.POP, popself.POP_AFTER = (1, 4), 0
            popsweep.POP_POS_MAP.clear()
            popsweep.POP_POS_MAP[102] = (7.0, 8.0, 9.0, 0.0)
            groupchannel.WORLD_PEERS.clear()
            rooms.WORLD_MAPS.clear()
            _ad = ("198.51.100.9", 19155)
            rooms.WORLD_MAPS[_ad[0]] = 102
            _ep = zoneentry.reply_0153(fill=True)[zoneentry.R153_ENDPOINT:zoneentry.R153_ENDPOINT + addressing.ENDPOINT_LEN]
            _tb = fmoworld.bf_init(fmoworld.key_for_endpoint(_ep, 1))

            class _NullSock:
                def sendto(self, d, to):
                    pass
            datagram._serve_datagram(_NullSock(), groupchannel.WORLD_PEERS,
                                     fmoworld.build(*_tb, peer=1, hid=udpconfig.UDP_HID, kind=2,
                                                    ack=0, flag=2, frm=0, to=1), _ad)
            _ch = groupchannel.WORLD_PEERS[_ad]
            _seed_ok = _ch.popped and _ch.pos == (7.0, 8.0, 9.0)
        finally:
            popsweep.POP, popself.POP_AFTER = _svp[0], _svp[1]
            popsweep.POP_POS_MAP.clear()
            popsweep.POP_POS_MAP.update(_svp[2])
            rooms.WORLD_MAPS.clear()
            rooms.WORLD_MAPS.update(_svp[3])
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_svp[4])
        print(f"  udp: the self-POP seeds the room position with the served "
              f"per-map spawn, not the global FMO_UDP_POP_POS: "
              f"{'OK' if _seed_ok else 'FAIL'}")
        ok &= _seed_ok

    # WARNING: A name-step (0x0177) record stores its payload as `raw_0177`, so the
    # raw-fallback readers (gender / look / nation) see NO creation bytes
    # rather than the client's buffer litter.
    _svcs = flat_globals()["CHAR_STORE"]
    _svnu = flat_globals()["NAME_UNIQUE"]
    try:
        flat_globals()["CHAR_STORE"] = ""             # commit() writes nothing
        # the uniqueness check reads the REAL store's other accounts, which
        # on a dev box may well hold a "Lex Arden" already -- not this test
        flat_globals()["NAME_UNIQUE"] = False
        _s177 = session.Session("selftest:0")
        _s177._roster = []
        _pl = bytearray(56)
        _pl[0x00] = 1
        _pl[0x04:0x07] = b"Lex"
        _pl[0x15:0x1A] = b"Arden"
        _pl[0x26:0x34] = bytes(range(0x26, 0x34))   # litter past the names
        _s177.apply_charsel(0x0177, bytes(_pl), 1)
        _c177 = _s177._roster[0]
        _raw_ok = ("raw" not in _c177 and "raw_0177" in _c177
                   and poplook._gender_byte(_c177) is None
                   and popnation.character_nation(_c177)[0] is None
                   and poplook._look_field(_c177, "face", 0x32, 2) is None)
        # and with the check ON, the same name on ANOTHER id of this roster
        # is refused with SE's code, and the record is not created
        flat_globals()["NAME_UNIQUE"] = True
        _pl2 = bytearray(_pl)
        _pl2[0x00] = 2
        _why2 = _s177.apply_charsel(0x0177, bytes(_pl2), 2)
        _raw_ok = (_raw_ok and bool(_why2) and "already in use" in _why2
                   and getattr(_s177, "fail_code", None) == charstore.NAME_TAKEN_CODE
                   and len(_s177._roster) == 1)
    finally:
        flat_globals()["CHAR_STORE"] = _svcs
        flat_globals()["NAME_UNIQUE"] = _svnu
    print(f"  0177: a name-step record keeps its payload out of `raw`, so no "
          f"reader wears buffer litter as a face or nation: "
          f"{'OK' if _raw_ok else 'FAIL'}")
    ok &= _raw_ok

    # KEY: STAGE 17 -- THE WAR STATE'S DOOR (static 2026-09-12): the kind-7
    # query is answered with 216-byte records in op 0x21 pages + END, mark
    # mode names offsets, 'end' sends END only, '0' is the old silence, and a
    # binding writes the settled state into the record.
    if fmomsn and fmowar:
        _svw = (warstate.WAR, warstate.WAR_MAP, warstate.WAR_FIELDS, warstate._WAR_STATE, status.STATUS_NATION)
        try:
            _g = flat_globals()
            _g["WAR"], _g["WAR_MAP"], _g["WAR_FIELDS"] = "1", "", ""
            _st = fmowar.War(autosave=False, load=False)
            _st.data = {"sectors": {}, "phases": {}, "log": []}
            _g["_WAR_STATE"] = _st
            _r1 = community.msn_reply("selftest", 0x10, fmomsn.SECTORS_CAPTURE)
            _p1 = fmomsn.parse(_r1[0][1]) if _r1 else None
            # 10 ids -> two pages (7 + 3, the client's 4,096-byte buffer) + END
            _p1b = fmomsn.parse(_r1[1][1]) if len(_r1) > 2 else None
            _w1 = (len(_r1) == 3 and _r1[0][0] == fmomsn.OP_SECTOR_PAGE
                   and _r1[1][0] == fmomsn.OP_SECTOR_PAGE
                   and _r1[2][0] == fmomsn.OP_END and _p1 is not None
                   and struct.unpack_from("<I", _p1[1], 4)[0] == 7
                   and _p1b is not None and struct.unpack_from("<I", _p1b[1], 4)[0] == 3
                   and struct.unpack_from("<I", _p1[1], 8 + fmomsn.SECTOR_ID_OFF)[0] == 903_069_118
                   and struct.unpack_from("<I", _p1[1], 8 + 0x0C)[0] == 0)
            _g["WAR"] = "mark"
            _r2 = community.msn_reply("selftest", 0x10, fmomsn.SECTORS_CAPTURE)
            _p2 = fmomsn.parse(_r2[0][1])
            _w2 = (struct.unpack_from("<I", _p2[1], 8 + 0x0C)[0] == 0x0C
                   and struct.unpack_from("<I", _p2[1], 8 + fmomsn.SECTOR_ID_OFF)[0] == 903_069_118)
            # mark8: every BYTE names itself, so a byte-sized column speaks
            _g["WAR"] = "mark8"
            _r2b = community.msn_reply("selftest", 0x10, fmomsn.SECTORS_CAPTURE)
            _p2b = fmomsn.parse(_r2b[0][1])
            _w2 = (_w2 and _p2b[1][8 + 0x65] == 0x65 and _p2b[1][8 + 0x01] == 0x01
                   and struct.unpack_from("<I", _p2b[1], 8 + fmomsn.SECTOR_ID_OFF)[0] == 903_069_118)
            _g["WAR"] = "end"
            _r3 = community.msn_reply("selftest", 0x10, fmomsn.SECTORS_CAPTURE)
            _g["WAR"] = "0"
            _r4 = community.msn_reply("selftest", 0x10, fmomsn.SECTORS_CAPTURE)
            _w3 = (len(_r3) == 1 and _r3[0][0] == fmomsn.OP_END and _r4 == [])
            # a settled sector reaches the record through the binding
            _g["WAR"], _g["WAR_MAP"] = "1", "nation=0x05:b,control=0x08:I"
            _g["STATUS_NATION"] = 1
            _mid = fmowar._ts(2026, 10, 1)
            _st.settle(69118, 1, won=True, pvp=True, now=_mid)      # PvP = 2 -> cap 3? no: 2/3
            _st.settle(69118, 1, won=True, now=_mid)                # 3/3 -> flips to O.C.U.
            _r5 = community.msn_reply("selftest", 0x10, fmomsn.SECTORS_CAPTURE)
            _p5 = fmomsn.parse(_r5[0][1])
            _w5 = (_p5[1][8 + 0x05] == 1
                   and struct.unpack_from("<I", _p5[1], 8 + 0x08)[0] == fmowar.RATE_STEP
                   and _p5[1][8 + fmomsn.SECTOR_SLOT_LEN + 0x05] == 0)   # sector 02 untouched
            # the battle-end hook settles ONCE per sortie, by tile
            _sw = session.Session("selftest:0")
            _sw.sector = (69119, 2, 108)
            _sw.war_settle(True)
            _sw.war_settle(True)                                    # same sortie: ignored
            _w6 = (_st.sector(69119)["counter"]["1"] == 1
                   and _st.sector(69119)["wins"]["1"] == 1)
            _sw.sector = None
            _w7 = _sw.war_settle(True) is None
            # a Controlled Zone sortie (selector 1xx / 3xx) moves nothing (D64 86);
            # a Frontline one (5xx) still settles
            _sc = session.Session("selftest:0")
            _sc.sector, _sc.sector_zone = (69120, 3, 108), 102
            _w8 = _sc.war_settle(True) is None and _st.sector(69120)["counter"].get("1", 0) == 0
            _sc2 = session.Session("selftest:0")
            _sc2.sector, _sc2.sector_zone = (69120, 3, 108), 510
            _w8 &= _sc2.war_settle(True) is not None and _st.sector(69120)["counter"]["1"] == 1
            _war_ok = _w1 and _w2 and _w3 and _w5 and _w6 and _w7 and _w8
        finally:
            (_g["WAR"], _g["WAR_MAP"], _g["WAR_FIELDS"], _g["_WAR_STATE"],
             _g["STATUS_NATION"]) = _svw
        print(f"  war state: kind 7 -> op 0x21 page of 216-B records (id at +0xD0) + END; "
              f"mark names offsets; 'end' = END only; '0' = silence; a binding writes "
              f"the settled sector (O.C.U. {fmowar.RATE_STEP}%) into the record; the "
              f"battle-end hook settles once per sortie: {'OK' if _war_ok else 'FAIL'}")
        ok &= _war_ok

    # KEY: STAGE 17b -- SE's WAR RULES (2026-09-30): a loss lowers the loser's
    # rate (AI/F00/D08 78), a sector passes through NEUTRAL (addmanual 93
    # 「敵軍→中立→自軍」), fortresses and bases cap lower (update 050815), and a
    # new phase resets the frontline and fails running area missions
    # (guide/phase, news7740).
    if fmomsn and fmowar:
        _svw = (warstate.WAR, warstate.WAR_MAP, warstate.WAR_FIELDS, warstate._WAR_STATE,
                status.STATUS_NATION)
        _svk = (fmowar.LOSS_WEIGHT, fmowar.NEUTRAL, fmowar.FACILITY_CAP, fmowar.PHASE_RESET)
        try:
            fmowar.LOSS_WEIGHT, fmowar.NEUTRAL, fmowar.FACILITY_CAP, fmowar.PHASE_RESET = 1, True, 60, True
            _g = flat_globals()
            _g["WAR"], _g["WAR_MAP"], _g["WAR_FIELDS"] = "1", "nation=0x05:b,control=0x08:I", ""
            _g["STATUS_NATION"] = 1
            _st = fmowar.War(autosave=False, load=False)
            _st.data = {"sectors": {}, "phases": {}, "log": []}
            _g["_WAR_STATE"] = _st
            _mid = fmowar._ts(2026, 10, 1)
            # (1) an O.C.U. pilot LOSES a frontline battle against NPCs: the
            # U.S.N. counter fills through the battle-end hook
            _sl = session.Session("selftest:0")
            _sl.sector, _sl.sector_zone = (69121, 4, 50), 509
            _sl.war_settle(False)
            _x1 = (_st.sector(69121)["counter"]["2"] == 1
                   and _st.sector(69121)["losses"]["1"] == 1)
            # (2) an O.C.U. sector at one step, drained by the U.S.N.: NEUTRAL,
            # the record's nation byte 0 (Deadlock), then U.S.N. at one step
            _s2 = _st.sector(69118)
            _s2["nation"], _s2["control"], _s2["deadlock"] = 1, fmowar.RATE_STEP, False
            for _ in range(fmowar.COUNTER_CAP):
                _st.settle(69118, 2, won=True, now=_mid)
            _r = community.msn_reply("selftest", 0x10, fmomsn.SECTORS_CAPTURE)
            _p = fmomsn.parse(_r[0][1])
            _x2 = (_st.sector(69118)["nation"] == 0 and _st.sector(69118)["deadlock"]
                   and _p[1][8 + 0x05] == 0
                   and struct.unpack_from("<I", _p[1], 8 + 0x08)[0] == 0)
            for _ in range(fmowar.COUNTER_CAP):
                _st.settle(69118, 2, won=True, now=_mid)
            _x2 &= (_st.sector(69118)["nation"] == 2
                    and _st.sector(69118)["control"] == fmowar.RATE_STEP)
            # (3) the O.C.U. fortress (509 sector 02) caps at 60, a city at 100
            for _t in (94099, 85102):
                _s3 = _st.sector(_t)
                _s3["nation"], _s3["control"], _s3["deadlock"] = 1, 40, False
                for _ in range(fmowar.COUNTER_CAP * 4):
                    _st.settle(_t, 1, won=True, now=_mid)
            _x3 = (_st.sector(94099)["control"] == 60 and _st.sector(85102)["control"] == 100)
            # (4) phase 1 judged (the O.C.U. holds Maltaf, 4 pts), then phase 2
            # starts: the frontline resets, the U.S.N. fortress opens Deadlock,
            # and the mission book's moment is the phase start
            _st.tick(now=fmowar._ts(2026, 11, 2))
            _x4 = warstate.frontline_reset_at() == 0 and _st.sector(85102)["nation"] == 1
            _st.tick(now=fmowar._ts(2026, 11, 5, 6))
            _x4 &= (warstate.frontline_reset_at() == fmowar._ts(2026, 11, 5)
                    and _st.sector(85102)["deadlock"] and _st.sector(69118)["nation"] == 2
                    and _st.sector(fmowar.FORTRESS[2])["deadlock"]
                    and _st.sector(fmowar.FORTRESS[1])["nation"] == 1
                    and _st.sector(fmowar.FORTRESS[1])["control"] == 60)
            _rules_ok = _x1 and _x2 and _x3 and _x4
        finally:
            (_g["WAR"], _g["WAR_MAP"], _g["WAR_FIELDS"], _g["_WAR_STATE"],
             _g["STATUS_NATION"]) = _svw
            fmowar.LOSS_WEIGHT, fmowar.NEUTRAL, fmowar.FACILITY_CAP, fmowar.PHASE_RESET = _svk
        print(f"  war rules: an NPC loss fills the enemy's counter "
              f"{'OK' if _x1 else 'FAIL'}; a drained sector goes NEUTRAL (nation byte 0) "
              f"before it changes hands {'OK' if _x2 else 'FAIL'}; a fortress caps at 60 "
              f"{'OK' if _x3 else 'FAIL'}; a new phase resets the frontline and dates "
              f"the area-mission failure {'OK' if _x4 else 'FAIL'}")
        ok &= _rules_ok

    # WARNING: THE 1,400-BYTE WALL (live black screen 2026-09-05). With the self POP
    # plus six cast NPCs queued, the first reply must fit the client's recvfrom
    # buffer, carry exactly the records it holds (TO - FROM), keep the rest
    # pending, and hand out the next slice once the peer acks the first --
    # and the unsliced total must actually EXCEED the wall, or this proves
    # nothing.
    if fmoworld:
        _svm = (popsweep.POP, popself.POP_AFTER, npccast.POP_NPC, dict(rooms.WORLD_MAPS), dict(groupchannel.WORLD_PEERS))
        try:
            popsweep.POP, popself.POP_AFTER = (1, 4), 0
            npccast.POP_NPC = [(0x820C1000 + i, 4, (1.0 * i, 3.0, 5.0, 0.0), None)
                               for i in (0, 3, 4, 5, 6, 0x13)]
            groupchannel.WORLD_PEERS.clear()
            rooms.WORLD_MAPS.clear()
            _ad = ("198.51.100.10", 19155)
            rooms.WORLD_MAPS[_ad[0]] = 102
            _ep = zoneentry.reply_0153(fill=True)[zoneentry.R153_ENDPOINT:zoneentry.R153_ENDPOINT + addressing.ENDPOINT_LEN]
            _tb = fmoworld.bf_init(fmoworld.key_for_endpoint(_ep, 1))
            _sent = []

            class _CapSock:
                def sendto(self, d, to):
                    _sent.append(d)

            def _pump(frm, to, ack):
                del _sent[:]
                datagram._serve_datagram(_CapSock(), groupchannel.WORLD_PEERS,
                                         fmoworld.build(*_tb, peer=1, hid=udpconfig.UDP_HID,
                                                        kind=2, ack=ack, flag=2,
                                                        frm=frm, to=to), _ad)
                return [fmoworld.parse(*_tb, d) for d in _sent]

            _r1 = _pump(0, 1, 0)
            _ch = groupchannel.WORLD_PEERS[_ad]
            _total = fmoworld.HDR_LEN + sum(len(r) for r in _ch.pending)
            _p1 = _r1[0]
            _carried = _p1["to"] - _p1["from"]
            _slice_ok = (len(_sent) == 1 and len(_sent[0]) <= udpconfig.UDP_MAX_DATAGRAM
                         and len(_sent[0]) <= udpconfig.CLIENT_RECV_BUF
                         and _carried == len(_p1["records"]) == udpconfig.fit_records(_ch.pending)
                         and 0 < _carried < len(_ch.pending)
                         and _total > udpconfig.CLIENT_RECV_BUF
                         and len(_ch.pending) >= 7)
            # the peer acks the slice -> the next datagram carries the NEXT slice
            _r2 = _pump(1, 2, _p1["to"])
            _p2 = _r2[0]
            _next_ok = (_p2["from"] == _p1["to"] and _p2["to"] > _p2["from"]
                        and len(_sent[0]) <= udpconfig.CLIENT_RECV_BUF)
            # and NO ack -> the SAME slice is resent (the reliable window)
            _r3 = _pump(2, 3, _p1["to"])
            _resend_ok = _r3[0]["from"] == _p2["from"] and _r3[0]["to"] == _p2["to"]
        finally:
            popsweep.POP, popself.POP_AFTER, npccast.POP_NPC = _svm[0], _svm[1], _svm[2]
            rooms.WORLD_MAPS.clear()
            rooms.WORLD_MAPS.update(_svm[3])
            groupchannel.WORLD_PEERS.clear()
            groupchannel.WORLD_PEERS.update(_svm[4])
        print(f"  udp: 7+ queued records ({_total} B total > the client's "
              f"{udpconfig.CLIENT_RECV_BUF}-B recvfrom) are sliced to {_carried}/datagram "
              f"under {udpconfig.UDP_MAX_DATAGRAM} B, TO-FROM == carried: "
              f"{'OK' if _slice_ok else 'FAIL'}")
        ok &= _slice_ok
        print(f"  udp: the peer's ack advances the window to the next slice, and "
              f"no ack resends the same slice: "
              f"{'OK' if _next_ok and _resend_ok else 'FAIL'}")
        ok &= _next_ok and _resend_ok

    # ----------------------------------------------------------------- #
    # The community/mission server. fmomsn.py owns the codec and proves it
    # against a real capture; what is pinned HERE is the part that lives in
    # this file: that a community frame is recognised as one, that a POL
    # packet never is, and that each op produces the reply the client's own
    # arms expect. If any of these regress, the second server goes back to
    # being dropped and the "All Mission List" goes back to lying.
    if fmomsn is not None:
        _sniff_ok = (fmomsn.parse(fmomsn.HELLO_CAPTURE) is not None
                     and fmomsn.looks_like_frame(fmomsn.HELLO_CAPTURE) == 40)
        # Every POL packet this file can build must be rejected by the sieve
        # or by the digest -- otherwise a lobby connection would be hijacked.
        _pol_ok = True
        for _m, _pl in ((handshake.MSG_VERSION, payload), (handshake.MSG_HANDSHAKE_OK, b""),
                        (handshake.MSG_CRED_REPLY, b"\x00" * handshake.CRED_REPLY_LEN)):
            _pkt = packet.build(_m, _pl, seq=0x1001, conn_id=0)
            _want = fmomsn.looks_like_frame(_pkt)
            if _want is not None and len(_pkt) >= _want:
                _pol_ok &= fmomsn.parse(_pkt[:_want]) is None
        print(f"  community: the captured hello is recognised and a POL packet "
              f"never is: {'OK' if _sniff_ok and _pol_ok else 'FAIL'}")
        ok &= _sniff_ok and _pol_ok

        _saved_msn = (community.MSN_ON, community.MSN_MARK, list(community.MSN_ROWS))
        try:
            flat_globals()["MSN_ON"], flat_globals()["MSN_MARK"] = True, False
            _hello = community.msn_reply("selftest", fmomsn.OP_HELLO, b"")
            _go_ok = (len(_hello) == 1 and _hello[0][0] == fmomsn.OP_GO)
            # The op-9 body as 0x611AF5E0 builds it: submode 4, cap 100,
            # nation 1, MapKind 509, category 3.
            # WARNING: the category is the LAST dword (payload+0x20 = the builder's
            # arg4), not the one before it -- the first live client's two
            # refreshes proved it. See fmomsn.ListQuery.
            _q = struct.pack("<7I", 0, 4, 100, 1, 509, 0, 3 << 24)
            flat_globals()["MSN_ROWS"] = []
            _empty = community.msn_reply("selftest", fmomsn.OP_LIST, _q)
            _empty_ok = (len(_empty) == 1 and _empty[0][0] == fmomsn.OP_END)
            flat_globals()["MSN_ROWS"] = ["A%d" % i for i in range(9)]
            _full = community.msn_reply("selftest", fmomsn.OP_LIST, _q)
            # KEY: A ROW'S OWN CATEGORY decides which of the three lists it is
            # in. An uncategorised row ECHOES the query, which is why all
            # three (Battle Map / Sector / Area) showed the same rows.
            flat_globals()["MSN_ROWS"] = ["7/1:Battle", "11/2:Sector", "13/3:Area",
                                      "3:Echo"]
            _catq = community.msn_reply("selftest", fmomsn.OP_LIST,
                                        struct.pack("<7I", 0, 2, 100, 1, 509, 0,
                                                    1 << 24))
            _cr = fmomsn.parse(_catq[0][1])[1]
            _ccount = struct.unpack_from("<I", _cr, 4)[0]
            _cnames = [_cr[8 + i * fmomsn.RECORD_LEN + 4:
                           8 + i * fmomsn.RECORD_LEN + 0x20].split(bytes(1))[0]
                       for i in range(_ccount)]
            # KEY: A CATEGORY-1 QUERY GETS THE CATEGORY-1 ROW AND THE ECHO ROW,
            # and NOT the category-2 one -- the server filters, because in the
            # Area list (modes 3/4) the client does not.
            _cat_row_ok = (_ccount == 2
                           and _cnames == [b"Battle", b"Echo"])
            # ...and the Area query (3) gets the area row plus the echo
            _areaq = community.msn_reply("selftest", fmomsn.OP_LIST,
                                         struct.pack("<7I", 0, 2, 100, 1, 509, 0,
                                                     fmomsn.CATEGORY_AREA << 24))
            _ar = fmomsn.parse(_areaq[0][1])[1]
            _acount = struct.unpack_from("<I", _ar, 4)[0]
            _anames = [_ar[8 + i * fmomsn.RECORD_LEN + 4:
                           8 + i * fmomsn.RECORD_LEN + 0x20].split(bytes(1))[0]
                       for i in range(_acount)]
            _cat_row_ok = (_cat_row_ok and _acount == 2
                           and _anames == [b"Area", b"Echo"])
            # 9 rows at 7 per page = two pages, then the END.
            _page_ok = ([o for o, _ in _full]
                        == [fmomsn.OP_PAGE, fmomsn.OP_PAGE, fmomsn.OP_END]
                        and all(len(f) <= fmomsn.CLIENT_RX for _, f in _full))
            _cat = _mid0 = None
            _r = fmomsn.parse(_full[0][1])
            if _r:
                _rec = _r[1][8:8 + fmomsn.RECORD_LEN]
                _cat = struct.unpack_from("<I", _rec, 0x1C8)[0] >> 24
                _mid0 = struct.unpack_from("<I", _rec,
                                           fmomsn.MISSION_ID)[0]
            _cat_ok = _cat == 3
            # KEY: The row's MISSION ID. A bare name takes its 1-based index, so
            # the first row is 1 and NEVER 0 -- an accept keyed to 0 is an
            # accept keyed to nothing (0x611C95EA -> the 0x018A body).
            _mid_ok = (_mid0 == 1
                       and community.msn_row_spec("Recon Alpha", 0)
                       == (1, None, "Recon Alpha")
                       and community.msn_row_spec("7:Recon Alpha", 0)
                       == (7, None, "Recon Alpha")
                       and community.msn_row_spec("0x20:Sweep", 4) == (0x20, None, "Sweep")
                       # KEY: the CATEGORY -- which of the three lists it is in
                       and community.msn_row_spec("7/1:Recon Alpha", 0)
                       == (7, 1, "Recon Alpha")
                       and community.msn_row_spec("11/2:Sweep", 0) == (11, 2, "Sweep")
                       # an explicit 0 is honoured -- that is the deliberate
                       # reproduction of the pre-09-12 behaviour
                       and community.msn_row_spec("0:Old", 2) == (0, None, "Old")
                       # a colon that is not an id leaves the name whole
                       and community.msn_row_spec("Recon: Alpha", 3)
                       == (4, None, "Recon: Alpha"))
            flat_globals()["MSN_ON"] = False
            _off_ok = community.msn_reply("selftest", fmomsn.OP_LIST, _q) == []
        finally:
            (flat_globals()["MSN_ON"], flat_globals()["MSN_MARK"],
             flat_globals()["MSN_ROWS"]) = _saved_msn
        print(f"  community: hello -> 0x12, empty list -> 0x1B alone, 9 rows -> "
              f"two 0x1D pages + 0x1B, each under the client's 4,096-B buffer: "
              f"{'OK' if _go_ok and _empty_ok and _page_ok else 'FAIL'}")
        ok &= _go_ok and _empty_ok and _page_ok
        print(f"  community: a served row echoes the category the client asked "
              f"for into +0x1C8 byte 3, or 0x611C98E1 drops every row: "
              f"{'OK' if _cat_ok else 'FAIL'}")
        ok &= _cat_ok
        print(f"  community: FMO_MSN=0 answers nothing at all (the pre-09-06 "
              f"A/B): {'OK' if _off_ok else 'FAIL'}")
        ok &= _off_ok
        print(f"  community: every served row carries a MISSION ID at "
              f"record+0x00 (default = its 1-based index, never 0), and "
              f"'<id>:<name>' overrides it: {'OK' if _mid_ok else 'FAIL'}")
        ok &= _mid_ok
        print(f"  community: the SERVER filters by category -- Battle Map "
              f"(1) and Area (3) each get their own row plus the echo row, "
              f"never each other's. In modes 3/4 the client filters NOTHING, "
              f"so this is the only filter there: "
              f"{'OK' if _cat_row_ok else 'FAIL'}")
        ok &= _cat_row_ok

        # --- THE ACCEPT, 0x018A --------------------------------------------- #
        # The whole contract is: the body's first dword is the row's
        # record+0x00, an empty 0x018B is success (the parse 0x6122B670 reads
        # nothing), and message 2 + a code is a refusal the client turns into
        # one of SE's own sentences via 0x611C4B30.
        _sv_acc, _sv_rows = missionboard.MISSION_ACCEPT, list(community.MSN_ROWS)
        try:
            flat_globals()["MSN_ROWS"] = ["7:Recon Alpha", "Sector Sweep"]
            _acc_s = session.Session("audit:0")
            flat_globals()["MISSION_ACCEPT"] = "1"
            _acc = _acc_s.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 7) + bytes(120),
                seq=packet.SEQ_MIN, conn_id=1)))
            _acc_ok = (len(_acc) == 1
                       and packet.parse(_acc[0])["msg"] == missionboard.MSG_MISSION_ACCEPT_REPLY
                       and packet.parse(_acc[0])["payload"] == b"")
            # the same request, refused with the RANK code
            flat_globals()["MISSION_ACCEPT"] = "2"
            _ref = _acc_s.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 7) + bytes(120),
                seq=packet.SEQ_MIN, conn_id=1)))
            _ref_ok = (len(_ref) == 1 and packet.parse(_ref[0])["msg"] == charselect.MSG_FAIL
                       and packet.parse(_ref[0])["conn"] == 2)
            # a NEGATIVE code survives the u16 field the client sign-extends
            flat_globals()["MISSION_ACCEPT"] = "-7"
            _neg = _acc_s.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 7) + bytes(120),
                seq=packet.SEQ_MIN, conn_id=1)))
            _neg_ok = (len(_neg) == 1 and packet.parse(_neg[0])["msg"] == charselect.MSG_FAIL
                       and packet.parse(_neg[0])["conn"] == 0xFFF9)
        finally:
            flat_globals()["MISSION_ACCEPT"] = _sv_acc
            flat_globals()["MSN_ROWS"] = _sv_rows
        # the refusal mapper is TOTAL -- 0x611C4B30 has no unreachable input
        _map_ok = ("27:3" in missionboard.mission_refusal_text(2)
                   and "27:7" in missionboard.mission_refusal_text(-7)
                   and "27:4" in missionboard.mission_refusal_text(0)
                   and "27:4" in missionboard.mission_refusal_text(-500)
                   and "warzone 2" in missionboard.mission_refusal_text(-501))
        print(f"  mission accept: 0x018A id 7 -> an EMPTY 0x018B = accepted "
              f"(its parse 0x6122B670 reads nothing): "
              f"{'OK' if _acc_ok else 'FAIL'}")
        ok &= _acc_ok
        print(f"  mission accept: FMO_MISSION_ACCEPT=2 -> message 2 code 2 = "
              f"27:3 'Your rank is not high enough', and -7 rides the wire as "
              f"0xFFF9: {'OK' if _ref_ok and _neg_ok else 'FAIL'}")
        ok &= _ref_ok and _neg_ok
        print(f"  mission accept: 0x611C4B30's map is covered for every code, "
              f"including the < -500 warzone arm: "
              f"{'OK' if _map_ok else 'FAIL'}")
        ok &= _map_ok

        # --- THE GATE ------------------------------------------------------- #
        # The row's OWN requirements, read back out of the bytes we serve,
        # judged against the pilot's stored economy.
        _sv_g = (missionboard.MISSION_ACCEPT, list(community.MSN_ROWS), community.MSN_FIELDS)
        # these pins are the OLD money gate: FMO_MISSION_FEE_MP=0 restores it
        # (the MP gate, SE's rule and the default, is pinned right after)
        _sv_feemp = missionboard.MISSION_FEE_MP
        missionboard.MISSION_FEE_MP = False
        try:
            flat_globals()["MSN_ROWS"] = ["7:Recon Alpha"]
            # rank 21 (Major), fee H$ 5,000, MP 30
            flat_globals()["MSN_FIELDS"] = ("0:0x%X=21,0:0x%X=5000,0:0x%X=30"
                                            % (fmomsn.MISSION_RANK,
                                               fmomsn.MISSION_FEE,
                                               fmomsn.MISSION_REWARD_MP))
            _req = missionbook.mission_requirements()[7]
            _req_ok = (_req["rank"] == 21 and _req["fee"] == 5000
                       and _req["reward_mp"] == 30
                       and _req["name"] == "Recon Alpha")
            # each gate refuses with the code its own systext hangs off, and
            # the FIRST failure wins in the documented order
            _rich = {"rank": 21, "money": 5000, "mp": 0}
            _v = lambda **kw: missionbook.mission_accept_verdict(
                _req, dict(_rich, **kw))[0]
            _gate_ok = (_v(rank=20) == 2          # 27:3 rank
                        and _v(money=4999) == 1   # 27:2 money
                        and _v() is None          # both met
                        # rank is judged first when both fail at once
                        and _v(rank=20, money=0) == 2
                        # WARNING: THE REWARD IS NEVER GATED ON. +0x1E8 is the
                        # Reward MP (live: "Reward: MP30/H$1200"), so a pilot
                        # with 0 MP still accepts a row that PAYS 30.
                        and _v(mp=0) is None)
            # a row with NO requirements is not a gate at all
            flat_globals()["MSN_FIELDS"] = ""
            _open_ok = missionbook.mission_accept_verdict(
                missionbook.mission_requirements()[7], {"rank": 0, "money": 0})[0] is None
            # end to end through the arm: storeless, so the pilot is the knobs
            # (FMO_RANK defaults to 0) and the row wants Major
            flat_globals()["MSN_FIELDS"] = "0:0x%X=21" % fmomsn.MISSION_RANK
            flat_globals()["MISSION_ACCEPT"] = "gate"
            _gs = session.Session("audit:0")
            _gout = _gs.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 7) + bytes(120),
                seq=packet.SEQ_MIN, conn_id=1)))
            _e2e_ok = (len(_gout) == 1 and packet.parse(_gout[0])["msg"] == charselect.MSG_FAIL
                       and packet.parse(_gout[0])["conn"] == 2)
            # ...and an id we never served is never refused BY THE GATE
            _gout2 = _gs.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 999) + bytes(120),
                seq=packet.SEQ_MIN, conn_id=1)))
            _unknown_ok = (len(_gout2) == 1
                           and packet.parse(_gout2[0])["msg"]
                           == missionboard.MSG_MISSION_ACCEPT_REPLY)
            # THE FEE. Off by default, and a storeless connection is accepted
            # for FREE rather than charged into the void -- a fee nobody paid
            # must not read as paid, and it must never cost the client its
            # reply.
            _sv_fee, _sv_money = missionboard.MISSION_FEE, status.STATUS_MONEY
            try:
                flat_globals()["MSN_FIELDS"] = "0:0x%X=500" % fmomsn.MISSION_FEE
                flat_globals()["MISSION_FEE"] = True
                # WARNING: A storeless pilot's wallet IS FMO_STATUS_MONEY, so with the
                # selftest default of 0 the FEE GATE refuses first and the
                # charge is never reached. That is correct behaviour and it is
                # what the first version of this pin actually caught -- give
                # them the money, or this tests the refusal by accident.
                flat_globals()["STATUS_MONEY"] = 5000
                _fout = _gs.on_packet(packet.parse(packet.build(
                    missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 7) + bytes(120),
                    seq=packet.SEQ_MIN, conn_id=1)))
                _fee_ok = (len(_fout) == 1 and packet.parse(_fout[0])["msg"]
                           == missionboard.MSG_MISSION_ACCEPT_REPLY)
                # ...and a pilot who cannot afford it is refused with 27:2,
                # before any money moves
                flat_globals()["STATUS_MONEY"] = 499
                _poor = _gs.on_packet(packet.parse(packet.build(
                    missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 7) + bytes(120),
                    seq=packet.SEQ_MIN, conn_id=1)))
                _fee_ok = (_fee_ok and len(_poor) == 1
                           and packet.parse(_poor[0])["msg"] == charselect.MSG_FAIL
                           and packet.parse(_poor[0])["conn"] == 1)
            finally:
                flat_globals()["MISSION_FEE"] = _sv_fee
                flat_globals()["STATUS_MONEY"] = _sv_money
            _fee_default_ok = (os.environ.get("FMO_MISSION_FEE", "").strip()
                               or "0") == "0" or missionboard.MISSION_FEE
        finally:
            (flat_globals()["MISSION_ACCEPT"], flat_globals()["MSN_ROWS"],
             flat_globals()["MSN_FIELDS"]) = _sv_g
            missionboard.MISSION_FEE_MP = _sv_feemp
        # --- THE ECONOMY vs SE (2026-09-30) -------------------------------- #
        # 1. The Fee is MP (guide/mission 「Fee ： ミッションを受けるために必要なMP」,
        #    guide/addmanual 「必要階級 + 必要MP」), refused with -7, charged in
        #    MP, never refunded.
        _sv_ec = (missionboard.MISSION_ACCEPT, list(community.MSN_ROWS), community.MSN_FIELDS,
                  charstore.CHAR_STORE, missionboard.MISSION_FEE, missionboard.MISSION_FEE_MP,
                  missionboard.MISSION_REPORT)
        try:
            missionboard.MISSION_FEE_MP = True
            flat_globals()["MSN_ROWS"] = ["7/1:Recon Alpha"]
            flat_globals()["MSN_FIELDS"] = ("0:0x%X=21,0:0x%X=500,0:0x%X=1200,0:0x%X=%d"
                                            % (fmomsn.MISSION_RANK, fmomsn.MISSION_FEE,
                                               fmomsn.MISSION_REWARD_HS,
                                               fmomsn.MISSION_DISTRIBUTION,
                                               (1 << 24) | 33))
            _ereq = missionbook.mission_requirements()[7]
            _ev = lambda **kw: missionbook.mission_accept_verdict(
                _ereq, dict({"rank": 21, "money": 0, "mp": 500}, **kw))[0]
            _mpgate_ok = (_ev() is None             # money 0 is no longer a gate
                          and _ev(mp=499) == -7     # 27:7 not enough MP
                          and _ev(rank=20, mp=0) == 2
                          and _ereq["share_mp"] == 33)
            flat_globals()["MISSION_ACCEPT"] = "gate"
            flat_globals()["MISSION_FEE"] = True
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            _fpc = {"rank": 21, "mp": 600, "money": 0, "missions": []}
            _fs = session.Session("audit:0")
            _fs.playing_char = lambda: _fpc
            _fs.commit = lambda why: None
            _acc = lambda: _fs.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT, struct.pack("<I", 7) + bytes(120),
                seq=packet.SEQ_MIN, conn_id=1)))
            _a1 = _acc()
            _mp_after = _fpc.get("mp")
            _a2 = _acc()                               # active already: -4, no charge
            _fs.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_CANCEL,
                struct.pack("<I", missionbook.mission_key((_fpc["missions"] or [{"id": 7}])[-1]))
                + bytes(32),
                seq=packet.SEQ_MIN, conn_id=1)))
            _mp_cancel = _fpc["mp"]                    # never refunded
            _fpc["mp"] = 499
            _a3 = _acc()                               # short of the Fee: -7
            _mpfee_ok = (packet.parse(_a1[0])["msg"] == missionboard.MSG_MISSION_ACCEPT_REPLY
                         and _mp_after == 100 and _fpc["money"] == 0
                         and packet.parse(_a2[0])["conn"] == (-4 & 0xFFFF)
                         and _mp_cancel == 100
                         and packet.parse(_a3[0])["msg"] == charselect.MSG_FAIL
                         and packet.parse(_a3[0])["conn"] == (-7 & 0xFFFF)
                         and _fpc["mp"] == 499)
            # 3. the mission share: every battle-group member is owed a kind-3
            #    line when this win meets a battle-map mission
            _sh = missionbook.mission_share_entry(
                {"id": 7, "name": "Recon Alpha", "reward_hs": 1200}, _ereq, hs_pct=10)
            _share_pure_ok = (_sh is not None and _sh["hs"] == 120 and _sh["mp"] == 33
                              and _sh["kind"] == missionboard.PAY_SHARE == 3
                              and missionbook.mission_share_entry(
                                  {"reward_hs": 0}, {"share_mp": 0}) is None)
            _sv_grp = (dict(groupchannel.GROUP_OF), dict(groupchannel.GROUP_MEMBERS),
                       dict(trade.LIVE_SESSIONS), missionboard.MISSION_SHARE)
            try:
                missionboard.MISSION_SHARE = True
                _apc = {"rank": 21, "mp": 600, "missions": []}
                _bpc = {"first": "B", "missions": []}
                _sa = session.Session("audit:0")
                _sa._account = "selftest-share-a"
                _sa.playing_char = lambda: _apc
                _sa.commit = lambda why: None
                _sb = session.Session("audit:1")
                _sb._account = "selftest-share-b"
                _sb.playing_char = lambda: _bpc
                _sb.commit = lambda why: None
                trade.LIVE_SESSIONS["selftest-share-b-ip"] = _sb
                groupchannel.group_join(0x5E1F, "selftest-share-a")
                groupchannel.group_join(0x5E1F, "selftest-share-b")
                _sa.accept_mission(7, "Recon Alpha", 500, reward=(1200, 30))
                _sa.mission_battle_settle(True)
                _ka = [e for e in _apc.get("mission_pay") or [] if e.get("kind") == 3]
                _kb = [e for e in _bpc.get("mission_pay") or [] if e.get("kind") == 3]
                _share_ok = (len(_ka) == 1 and len(_kb) == 1 and _kb[0]["mp"] == 33
                             and _kb[0]["hs"] == int(round(
                                 1200 * missionboard.MISSION_SHARE_HS_PCT / 100.0)))
            finally:
                groupchannel.GROUP_OF.clear()
                groupchannel.GROUP_OF.update(_sv_grp[0])
                groupchannel.GROUP_MEMBERS.clear()
                groupchannel.GROUP_MEMBERS.update(_sv_grp[1])
                trade.LIVE_SESSIONS.clear()
                trade.LIVE_SESSIONS.update(_sv_grp[2])
                missionboard.MISSION_SHARE = _sv_grp[3]
        finally:
            (flat_globals()["MISSION_ACCEPT"], flat_globals()["MSN_ROWS"],
             flat_globals()["MSN_FIELDS"], flat_globals()["CHAR_STORE"],
             flat_globals()["MISSION_FEE"]) = _sv_ec[:5]
            missionboard.MISSION_FEE_MP = _sv_ec[5]
            flat_globals()["MISSION_REPORT"] = _sv_ec[6]
        print(f"  economy: the Fee is MP -- MP 499 of 500 is refused with -7 "
              f"(27:7), money is no gate, rank still first: "
              f"{'OK' if _mpgate_ok else 'FAIL'}")
        ok &= _mpgate_ok
        print(f"  economy: an accept CHARGES the Fee in MP (600 -> 100), not H$; "
              f"a re-accept is -4 with no charge; short of MP is -7: "
              f"{'OK' if _mpfee_ok else 'FAIL'}")
        ok &= _mpfee_ok
        print(f"  economy: mission share = the row's Distribution MP + "
              f"FMO_MISSION_SHARE_HS_PCT of the reward H$, a kind-3 line: "
              f"{'OK' if _share_pure_ok else 'FAIL'}")
        ok &= _share_pure_ok
        print(f"  economy: a battle-map win owes the share to EVERY battle-group "
              f"member, a live member's record included: "
              f"{'OK' if _share_ok else 'FAIL'}")
        ok &= _share_ok
        # news7740: an AREA accept running at a frontline reset has FAILED
        _sv_fr = (warstate.frontline_reset_at, missionboard.AREA_RESET_FAIL)
        try:
            _TR = 1789000000
            _isoR = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))
            warstate.frontline_reset_at = lambda: _TR + 100
            missionboard.AREA_RESET_FAIL = True
            _ar = lambda at, cat=3: missionbook.mission_status(
                {"id": 9, "name": "Area", "cat": cat, "at": _isoR(at)},
                now=_TR + 200, limit=0)
            _reset_ok = (_ar(_TR) == "failed" and _ar(_TR + 150) == "open"
                         and _ar(_TR, cat=1) == "open")
            missionboard.AREA_RESET_FAIL = False
            _reset_ok = _reset_ok and _ar(_TR) == "open"
        finally:
            warstate.frontline_reset_at, missionboard.AREA_RESET_FAIL = _sv_fr
        print(f"  economy: an area mission accepted before the phase's frontline "
              f"reset reads 'failed' (news7740); one after it stays open: "
              f"{'OK' if _reset_ok else 'FAIL'}")
        ok &= _reset_ok
        print(f"  mission gate: a row's required rank/fee/MP are read back out "
              f"of the RECORD BYTES we served: {'OK' if _req_ok else 'FAIL'}")
        ok &= _req_ok
        print(f"  mission gate: rank -> code 2, money -> 1, both met -> "
              f"accept, rank wins when both fail, and the REWARD MP is never "
              f"gated on: {'OK' if _gate_ok else 'FAIL'}")
        ok &= _gate_ok
        print(f"  mission gate: a requirement of 0 is NOT a requirement, so an "
              f"unauthored row stays acceptable: {'OK' if _open_ok else 'FAIL'}")
        ok &= _open_ok
        print(f"  mission gate: end to end, a Major-only row refuses a rank-0 "
              f"pilot with code 2, and an id we never served is ACCEPTED (a "
              f"gate cannot judge a row it cannot see): "
              f"{'OK' if _e2e_ok and _unknown_ok else 'FAIL'}")
        ok &= _e2e_ok and _unknown_ok
        # --- THE ACCEPTED-MISSION RECORD ------------------------------------ #
        _mrec = {"missions": [{"id": 7, "name": "Recon Alpha", "fee": 500,
                               "at": "2026-09-12T15:29:37Z"}]}
        _store_ok = (missionbook.accepted_missions({}) == []
                     and missionbook.accepted_missions({"missions": "nope"}) == []
                     and len(missionbook.accepted_missions(_mrec)) == 1
                     and missionbook.mission_already_accepted(_mrec, 7)
                     and not missionbook.mission_already_accepted(_mrec, 11)
                     and not missionbook.mission_already_accepted({}, 7))
        # ...and it comes back out on the ACCEPTED MISSION screen (View B)
        _vb = missionlist.reply_018e_short(rows=[(7, "Recon Alpha"), (11, "Sector Sweep")])
        _r0, _r1 = missionlist.M18E_RECORDS, missionlist.M18E_RECORDS + missionlist.M18E_REC_LEN
        _vb_ok = (len(_vb) == missionlist.S018E_SHORT_LEN
                  and packet.HDR + len(_vb) <= packet.CLIENT_RX_BUFFER
                  and struct.unpack_from("<I", _vb, _r0 + missionlist.ML_KEY)[0] == 7
                  and _vb[_r0 + missionlist.ML_NAME:_r0 + missionlist.ML_NAME + 11] == b"Recon Alpha"
                  and struct.unpack_from("<I", _vb, _r1 + missionlist.ML_KEY)[0] == 11
                  # a row we did not send stays 0 -> systext 21:30 "None"
                  and struct.unpack_from(
                      "<I", _vb, missionlist.M18E_RECORDS + 2 * missionlist.M18E_REC_LEN + missionlist.ML_KEY)[0] == 0
                  # and with no rows it is byte-identical to the old empty form
                  and missionlist.reply_018e_short() == missionlist.reply_018e_short(rows=[]))
        # more rows than fit are DROPPED, never overrun
        _vb_ok = _vb_ok and len(missionlist.reply_018e_short(
            rows=[(i + 1, "M%d" % i) for i in range(40)])) == missionlist.S018E_SHORT_LEN
        print(f"  mission record: accepts round-trip through the store's "
              f"`extra` JSON, and 27:0/-4 can be raised from one: "
              f"{'OK' if _store_ok else 'FAIL'}")
        ok &= _store_ok
        print(f"  mission record: they come back on Accepted Mission (View B) "
              f"as id + name per record, unsent rows stay 'None', and 40 rows "
              f"clamp to {missionlist.S018E_ROWS}: {'OK' if _vb_ok else 'FAIL'}")
        ok &= _vb_ok
        print(f"  mission fee: FMO_MISSION_FEE is OFF unless the env sets it; "
              f"H$ 5000 accepts a 500 fee and H$ 499 is refused with 27:2 "
              f"BEFORE any money moves: "
              f"{'OK' if _fee_ok and _fee_default_ok else 'FAIL'}")
        ok &= _fee_ok and _fee_default_ok
        # --- THE REPORT, THE DEADLINE, THE PAY (static 2026-09-12) ------- #
        _T0 = 1789000000
        _iso0 = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))
        _mm = lambda **kw: dict({"id": 7, "name": "Recon Alpha", "fee": 0,
                                 "at": _iso0(_T0), "reward_hs": 1200,
                                 "reward_mp": 30}, **kw)
        _u32 = lambda b, o: struct.unpack_from("<I", b, o)[0]
        # the deadline: SE's 30 minutes, 0 = none, and a met mission never
        # expires out from under its report
        _dl_ok = (missionbook.mission_status(_mm(), now=_T0 + 1799, limit=1800) == "open"
                  and missionbook.mission_status(_mm(), now=_T0 + 1801,
                                                 limit=1800) == "expired"
                  and missionbook.mission_status(_mm(), now=_T0 + 99999, limit=0) == "open"
                  and missionbook.mission_status(_mm(status="met"), now=_T0 + 99999,
                                                 limit=1800) == "met")
        # every status -> the (state, result) whose sentence 0x611CA9B0 draws
        _st_ok = (all(_x in missionbook.mission_report_text(*missionboard.MISSION_STATE[_s])
                      for _s, _x in (("open", "28:3"), ("complete", "28:4"),
                                     ("failed", "28:5"), ("cancelled", "28:5"),
                                     ("expired", "28:6")))
                  and missionboard.MISSION_STATE["met"] == (4, 2)
                  and "NOTHING" in missionbook.mission_report_text(0, 0))
        # a battle: a win in the window MEETS a battle-map mission, a sector
        # mission is not judged by one battle, and a late win is too late
        _c = {"missions": [_mm(), _mm(id=11, name="Sector Sweep"),
                           _mm(id=13, name="Late", at=_iso0(_T0 - 4000))]}
        _mv = missionbook.mission_battle_apply(_c, True, now=_T0 + 60, limit=1800,
                                               cats={7: 1, 11: 2, 13: 1})
        _bt_ok = ([(_m["id"], _s) for _m, _s in _mv]
                  == [(7, "met"), (13, "expired")]
                  and _c["missions"][1].get("status") is None)
        # ...a loss FAILS it, and the first battle decides
        _c2 = {"missions": [_mm()]}
        missionbook.mission_battle_apply(_c2, False, now=_T0 + 60, limit=1800, cats={7: 1})
        missionbook.mission_battle_apply(_c2, True, now=_T0 + 90, limit=1800, cats={7: 1})
        _bt_ok = _bt_ok and _c2["missions"][0]["status"] == "failed"
        # the report: met -> complete, reward owed ONCE; again pays nothing
        _m1, _w1, _p1 = missionbook.mission_report_apply(_c, 7, now=_T0 + 120, limit=1800)
        _m2, _w2, _p2 = missionbook.mission_report_apply(_c, 7, now=_T0 + 130, limit=1800)
        _rp_ok = (_w1 == "met" and _m1["status"] == "complete"
                  and _p1["hs"] == 1200 and _p1["mp"] == 30
                  and _w2 == "complete" and _p2 is None
                  and len(_c["mission_pay"]) == 1
                  and missionbook.mission_report_apply(_c, 999)[0] is None
                  and missionbook.mission_report_apply(None, 7)[0] is None)
        # an accept stored before rewards were snapshotted takes the row's
        _c3 = {"missions": [{"id": 7, "name": "R", "fee": 0,
                             "at": _iso0(_T0), "status": "met"}]}
        _p3 = missionbook.mission_report_apply(_c3, 7, now=_T0 + 10, limit=1800,
                                               rewards={7: (500, 5)})[2]
        _rp_ok = _rp_ok and _p3["hs"] == 500 and _p3["mp"] == 5
        # an open mission reported late is WRITTEN DOWN expired
        _c3b = {"missions": [_mm()]}
        _rp_ok = (_rp_ok and missionbook.mission_report_apply(
            _c3b, 7, now=_T0 + 1801, limit=1800)[1] == "expired"
            and _c3b["missions"][0]["status"] == "expired")
        # the paybook: a kind-5 "Mission bonus" line under a day header, paid
        # once; no room keeps it owed; a salary header for the day is reused
        _prow, _paid = missionbook.mission_pay_rows(_c, [])
        _pay_ok = (len(_prow) == 2 and _prow[0][0] == servicerecord.PAY_HEADER
                   and _prow[1][:1] == (missionboard.PAY_MISSION,)
                   and _prow[1][3:] == (1200, 30)
                   and servicerecord.PAY_KINDS[missionboard.PAY_MISSION] == "Mission bonus"
                   and _c["mission_pay"] == []
                   and missionbook.mission_pay_rows(_c, [])[0] == [])
        _c4 = {"mission_pay": [dict(_p1)]}
        _pay_ok = (_pay_ok and missionbook.mission_pay_rows(_c4, [(1, 0, 0, 0, 0)] * 20)[0]
                   == [] and len(_c4["mission_pay"]) == 1)
        _day = (_T0 + 120) // servicerecord.DAY * servicerecord.DAY
        _c5 = {"mission_pay": [dict(_p1)]}
        _pay_ok = _pay_ok and len(missionbook.mission_pay_rows(
            _c5, [(servicerecord.PAY_HEADER, 0, _day, 0, 0), (servicerecord.PAY_BASE, 0, _day, 1, 1)])[0]) == 1
        # the cancel: active -> cancelled (25:10), and it stops blocking
        _c6 = {"missions": [_mm()]}
        _mc, _wc = missionbook.mission_cancel_apply(_c6, 7, now=_T0 + 5, limit=1800)
        _cn_ok = (_wc == "open" and _mc["status"] == "cancelled"
                  and missionboard.MISSION_STATE["cancelled"] == (5, 1)
                  and not missionbook.mission_already_accepted(_c6, 7)
                  and missionbook.mission_already_accepted({"missions": [_mm()]}, 7))
        # pruning keeps every active accept and the last 10 closed ones
        _many = ([_mm(id=100 + i, status="complete") for i in range(15)]
                 + [_mm(id=1)])
        _pr = missionbook.mission_prune(_many)
        _cn_ok = (_cn_ok and len(_pr) == 11 and _pr[-1]["id"] == 1
                  and _pr[0]["id"] == 105)
        # the wire: 0x018F is what the slot waits for, and the fields land
        # where the poll reads them (G+0x5940 + offset)
        _r = missionlist.reply_018f(7, "Recon Alpha", missionbook.mission_row_fields(
            _mm(status="complete"), now=_T0, limit=1800))
        _vb2 = missionlist.reply_018e_short(rows=[(7, "Recon Alpha",
                                                   {missionboard.MR_STATE: 4, missionboard.MR_RESULT: 2})])
        _wire_ok = (len(_r) == missionboard.REPLY_018F_LEN == lobapi.LOBAPI[missionboard.MSG_MISSION_REPORT][1]
                    and lobapi.LOBAPI[missionboard.MSG_MISSION_REPORT][0] == missionboard.MSG_MISSION_REPORT_REPLY
                    and lobapi.LOBAPI[missionboard.MSG_MISSION_CANCEL] == (missionboard.MSG_MISSION_CANCEL_REPLY, 0)
                    and 0x5940 + missionboard.MR_STATE == 0x5A58
                    and 0x5940 + missionboard.MR_RESULT == 0x5B74
                    and _u32(_r, 0x20 + missionlist.ML_KEY) == 7
                    and _r[0x20 + missionlist.ML_NAME:0x20 + missionlist.ML_NAME + 11] == b"Recon Alpha"
                    and _u32(_r, 0x20 + missionboard.MR_STATE) == 5
                    and _u32(_r, 0x20 + missionboard.MR_RESULT) == 2
                    and _u32(_r, 0x20 + missionboard.MR_LIMIT) == 1800
                    and _u32(_r, 0x20 + missionboard.MR_START) == _T0
                    and _u32(_vb2, missionlist.M18E_RECORDS + missionboard.MR_STATE) == 4
                    and _u32(_vb2, missionlist.M18E_RECORDS + missionboard.MR_RESULT) == 2
                    and len(_vb2) == missionlist.S018E_SHORT_LEN)
        # the knob: OFF is byte-identical to before; ON never costs the
        # client its reply, even with no pilot
        _sv_rep = missionboard.MISSION_REPORT
        _rq = lambda msg: packet.parse(packet.build(msg, struct.pack("<I", 7) + bytes(32),
                                                    seq=packet.SEQ_MIN, conn_id=1))
        try:
            flat_globals()["MISSION_REPORT"] = False
            _os = session.Session("audit:0")
            _oo = _os.on_packet(_rq(missionboard.MSG_MISSION_REPORT))
            _off_ok = (missionbook.accepted_list_rows([_mm()]) == [(7, "Recon Alpha")]
                       and missionbook.mission_status(_mm(), now=_T0 + 99999) == "open"
                       and missionbook.mission_deadline() == 0
                       and len(_oo) == 1
                       and packet.parse(_oo[0])["msg"] == missionboard.MSG_MISSION_REPORT_REPLY
                       and packet.parse(_oo[0])["payload"] == bytes(missionboard.REPLY_018F_LEN))
            flat_globals()["MISSION_REPORT"] = True
            _on = _os.on_packet(_rq(missionboard.MSG_MISSION_REPORT))
            _cc = _os.on_packet(_rq(missionboard.MSG_MISSION_CANCEL))
            _off_ok = (_off_ok and len(_on) == 1
                       and packet.parse(_on[0])["msg"] == missionboard.MSG_MISSION_REPORT_REPLY
                       and packet.parse(_on[0])["payload"] == bytes(missionboard.REPLY_018F_LEN)
                       and len(_cc) == 1
                       and packet.parse(_cc[0])["msg"] == missionboard.MSG_MISSION_CANCEL_REPLY
                       and packet.parse(_cc[0])["payload"] == b""
                       and len(missionbook.accepted_list_rows([_mm()])[0]) == 3)
        finally:
            flat_globals()["MISSION_REPORT"] = _sv_rep
        # END TO END through a stubbed pilot: accept -> battle won -> list ->
        # report (28:4, reward owed) -> Personnel paybook pays it ONCE ->
        # report again pays nothing; and cancel really cancels.
        _pc = {"missions": [], "money": 0, "last_payday": None}
        _paid = []
        _sv_e2e = (missionboard.MISSION_REPORT, list(community.MSN_ROWS), community.MSN_FIELDS, charstore.CHAR_STORE)
        try:
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["CHAR_STORE"] = "selftest-stub"   # truthy; commit is stubbed
            flat_globals()["MSN_ROWS"] = ["7/1:Recon Alpha", "11/2:Sector Sweep"]
            flat_globals()["MSN_FIELDS"] = ("0:0x%X=30,0:0x%X=1200"
                                            % (fmomsn.MISSION_REWARD_MP,
                                               fmomsn.MISSION_REWARD_HS))
            _es = session.Session("audit:0")
            _es.playing_char = lambda: _pc
            _es.commit = lambda why: None
            _es.credit_money = (lambda why, money=0, contribution=0:
                                (_paid.append(money), (money, 0))[1])
            _es.accept_mission(7, "Recon Alpha", 0, reward=(1200, 30))
            _es.accept_mission(11, "Sector Sweep", 0)
            _early = _es.on_packet(_rq(missionboard.MSG_MISSION_REPORT))
            _moved = _es.mission_battle_settle(True)
            _lst = missionbook.accepted_list_rows(missionbook.accepted_missions(_pc))
            _rep = _es.on_packet(_rq(missionboard.MSG_MISSION_REPORT))
            _pb_rows, _pb_tm, _pb_tmp, _pb_days = _es.pay_salary(_pc, 20)
            _pb2 = missionbook.mission_pay_rows(_pc, [])[0]
            _rep2 = _es.on_packet(_rq(missionboard.MSG_MISSION_REPORT))
            _pay_again = len(_pc.get("mission_pay") or [])
            _es.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_CANCEL, struct.pack("<I", 11) + bytes(32),
                seq=packet.SEQ_MIN, conn_id=1)))
            _rb = lambda out: packet.parse(out[0])["payload"]
            _e2e2_ok = (
                # before the battle: in progress -> 28:3
                _u32(_rb(_early), 0x20 + missionboard.MR_STATE) == 4
                and _u32(_rb(_early), 0x20 + missionboard.MR_RESULT) == 0
                # the battle met ONLY the battle-map mission
                and [(m["id"], s) for m, s in _moved] == [(7, "met")]
                and _lst[0][2][missionboard.MR_STATE] == 4 and _lst[0][2][missionboard.MR_RESULT] == 2
                # the report: state 5 result 2 -> 28:4, id and name carried
                and packet.parse(_rep[0])["msg"] == missionboard.MSG_MISSION_REPORT_REPLY
                and _u32(_rb(_rep), 0x20 + missionboard.MR_STATE) == 5
                and _u32(_rb(_rep), 0x20 + missionboard.MR_RESULT) == 2
                and _u32(_rb(_rep), 0x20 + missionlist.ML_KEY) == 7
                # the paybook pays the row's reward as a Mission bonus line
                and any(r[0] == missionboard.PAY_MISSION and r[3] == 1200 and r[4] == 30
                        for r in _pb_rows)
                and _pb_tmp >= 30 and _paid == [_pb_tm]
                and _pb_tm == sum(r[3] for r in _pb_rows)
                and _pb2 == [] and _pay_again == 0
                # a second report still says 28:4 and owes nothing new
                and _u32(_rb(_rep2), 0x20 + missionboard.MR_STATE) == 5
                # cancel took the sector mission off the active list
                and missionbook.mission_find(_pc, 11)["status"] == "cancelled"
                and missionbook.mission_find(_pc, 7)["status"] == "complete")
        finally:
            (flat_globals()["MISSION_REPORT"], flat_globals()["MSN_ROWS"],
             flat_globals()["MSN_FIELDS"], flat_globals()["CHAR_STORE"]) = _sv_e2e
        # THE BINDING: a battle completes a mission only in ITS battlefield
        _cb = {"missions": [_mm(sector=72117), _mm(id=9, name="Anywhere"),
                            _mm(id=10, name="Elsewhere", sector=71121)]}
        _mb = missionbook.mission_battle_apply(_cb, True, now=_T0 + 60, limit=1800,
                                               cats={7: 1, 9: 1, 10: 1}, tile=72117)
        _bind_ok = ([(m["id"], s) for m, s in _mb] == [(7, "met"), (9, "met")]
                    and _cb["missions"][2].get("status") is None)
        # ...and a sortie with no war-map sector moves only unbound missions
        _cb2 = {"missions": [_mm(sector=72117), _mm(id=9, name="Anywhere")]}
        _mb2 = missionbook.mission_battle_apply(_cb2, False, now=_T0 + 60, limit=1800,
                                                cats={7: 1, 9: 1}, tile=None)
        _bind_ok = (_bind_ok and [(m["id"], s) for m, s in _mb2]
                    == [(9, "failed")])
        # the map selector: the mission's tile carries the icon byte, another
        # tile does not, and with the knob off the row is untouched
        _sv_bi = (missionboard.MISSION_REPORT, charstore.CHAR_STORE, zoneentry.MAPKIND, list(community.MSN_ROWS),
                  community.MSN_FIELDS)
        _rq5e = lambda tile: packet.parse(packet.build(warmap.MSG_015E_REQ, struct.pack("<I", tile)
                                                       + bytes(16), seq=packet.SEQ_MIN, conn_id=1))
        try:
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            flat_globals()["MAPKIND"] = 200
            # stamped NOW: this path judges the deadline on the real clock,
            # and an expired mission rightly gets no icon
            _ic = {"missions": [_mm(sector=72117,
                                    at=_iso0(int(time.time())))]}
            _is = session.Session("audit:0")
            _is.playing_char = lambda: _ic
            _is.commit = lambda why: None
            _p1 = packet.parse(_is.on_packet(_rq5e(72117))[0])["payload"]
            _p2 = packet.parse(_is.on_packet(_rq5e(71121))[0])["payload"]
            _icon_ok = (len(_p1) == warmap.S15F_HEAD_LEN + warmap.S15F_ROW_LEN
                        and _u32(_p1, warmap.S15F_HEAD_LEN) == 267
                        and _p1[warmap.S15F_HEAD_LEN + warmap.S15F_ICON] == warmap.MISSION_MAP_ICON
                        and warmap.MISSION_MAP_ICON != 0
                        and _u32(_p2, warmap.S15F_HEAD_LEN) == 232
                        and _p2[warmap.S15F_HEAD_LEN + warmap.S15F_ICON] == 0
                        and _is.sector[0] == 71121)
            # the battle end on this Session settles by ITS sector
            _is.sector = (72117, 20, 267)
            _icon_ok = (_icon_ok and [(m["id"], s) for m, s in
                                      _is.mission_battle_settle(True)]
                        == [(7, "met")])
            # +0x1F4 is read back from the row and snapshotted at accept
            flat_globals()["MSN_ROWS"] = ["17/1:Escort Duty"]
            flat_globals()["MSN_FIELDS"] = "0:0x%X=72117" % fmomsn.MISSION_SECTOR
            _sec_ok = missionbook.mission_requirements()[17]["sector"] == 72117
            _is.accept_mission(17, "Escort Duty", 0, reward=(800, 20),
                               sector=72117)
            _sec_ok = _sec_ok and missionbook.mission_find(_ic, 17)["sector"] == 72117
            flat_globals()["MISSION_REPORT"] = False
            _p3 = packet.parse(_is.on_packet(_rq5e(72117))[0])["payload"]
            _icon_ok = _icon_ok and _p3[warmap.S15F_HEAD_LEN + warmap.S15F_ICON] == 0
        finally:
            (flat_globals()["MISSION_REPORT"], flat_globals()["CHAR_STORE"],
             flat_globals()["MAPKIND"], flat_globals()["MSN_ROWS"],
             flat_globals()["MSN_FIELDS"]) = _sv_bi
        # the pilot's zone is STORED on every grant (the content profile's Zone)
        _zp = {"id": 1, "first": "Z"}
        _zc = []
        _zsn = session.Session("zoneprof:0")
        _zsn.playing_char = lambda: _zp
        _zsn.commit = lambda why: _zc.append(why)
        _svcs = charstore.CHAR_STORE
        try:
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            _zr = [_zsn.remember_zone(207, 102, "t"),     # stored
                   _zsn.remember_zone(207, 102, "t"),     # unchanged: no write
                   _zsn.remember_zone(0xFFFF, 102, "t"),  # a sentinel: kept out
                   _zsn.remember_zone(400, 102, "t")]     # a move: stored
        finally:
            flat_globals()["CHAR_STORE"] = _svcs
        _zprof_ok = (_zr == [True, False, False, True] and len(_zc) == 2
                     and _zp["mapkind"] == 400 and _zp["mapno"] == 102
                     and "mapkind" in fmostore.COLUMNS if fmostore else False)
        # the war map's selector is the PILOT'S zone: Molly in 207, 20:50:38Z,
        # tile 89135 missed selector 200; in 207 it is Sector 21 -> map 42
        _svz = dict(rooms.WORLD_ZONES)
        try:
            rooms.WORLD_ZONES.clear()
            _zsel_none = warmap.warmap_selector("zonetest")
            rooms.WORLD_ZONES["zonetest"] = 207
            _zs = warmap.warmap_sector_for(struct.pack("<I", 89135),
                                           selector=warmap.warmap_selector("zonetest"))
            rooms.WORLD_ZONES["zonetest"] = 999        # a zone with no sector table
            _zsel_bad = warmap.warmap_selector("zonetest")
            _zone_ok = (_zsel_none == zoneentry.MAPKIND and _zs[:3] == (89135, 21, 42)
                        and _zsel_bad == zoneentry.MAPKIND
                        and warmap.warmap_sector_for(struct.pack("<I", 89135),
                                                     selector=200)[1] is None)
        finally:
            rooms.WORLD_ZONES.clear()
            rooms.WORLD_ZONES.update(_svz)
        # the rest of the Battle Map list entry: who counts, and where it lands
        _T = 1_000_000.0
        _bs = {"a": {"mapno": 267, "granted_at": _T - 610},
               "b": {"mapno": 267, "granted_at": _T - 30},
               "f": {"mapno": 267, "granted_at": _T - 100},
               "c": {"mapno": 267, "granted_at": _T - 900, "ended": True},
               "d": {"mapno": 232, "granted_at": _T - 60},
               "e": {"mapno": 267, "granted_at": _T - 1800},
               "x": {"granted_at": _T - 5}}
        _sd = {"a": 0, "b": 1, "f": 0, "c": 0, "d": 0, "e": 1, "x": 0}
        _cn = warmap.warmap_census(267, _bs, _sd.get, _T, 1800)
        _er = warmap.warmap_entry_fill(bytearray(warmap.S15F_ROW_LEN), _cn[0], 1,
                                       _T - _cn[1], 1800)
        _entry_ok = (_cn[0] == [2, 1] and _cn[1] == _T - 610
                     and sorted(_cn[2]) == [("a", 0), ("b", 1), ("f", 0)]
                     and warmap.warmap_census(418, _bs, _sd.get, _T, 1800)
                     == ([0, 0], None, [])
                     and struct.unpack_from("<hh", _er, warmap.S15F_LIMIT) == (30, 10)
                     and _er[warmap.S15F_COUNT:warmap.S15F_COUNT + 2] == b"\x02\x01"
                     and _er[warmap.S15F_BAR_OWN] == 1 and _er[warmap.S15F_BAR_OTHER] == 2
                     and _er[warmap.S15F_CAP:warmap.S15F_CAP + 2] == b"\x0a\x0a"
                     and _er[warmap.S15F_ICON] == 0 and _er[:4] == bytes(4)
                     and struct.unpack_from("<hh", warmap.warmap_entry_fill(
                         bytearray(warmap.S15F_ROW_LEN), [0, 0], 0, 0, 0),
                         warmap.S15F_LIMIT) == (0, 0)
                     # the draw's own reads, item+0x8F = row+0x00
                     and (0x8F + warmap.S15F_LIMIT, 0x8F + warmap.S15F_ELAPSED,
                          0x8F + warmap.S15F_COUNT, 0x8F + warmap.S15F_ICON,
                          0x8F + warmap.S15F_BAR_OWN, 0x8F + warmap.S15F_BAR_OTHER,
                          0x8F + warmap.S15F_CAP)
                     == (0x93, 0x95, 0x97, 0x99, 0xD9, 0xE7, 0xE9))
        # --- ROUND 3 (static 2026-09-12): keys, zones, sector wins, the
        # counter-mission arrow, rank-gated permits --------------------- #
        # (1) a PER-ACCEPT KEY at record+0x00: two accepts of one row are
        # two rows; Report on the OLD key answers the OLD row
        _kc = {"missions": []}
        _ks = session.Session("audit:0")
        _ks.playing_char = lambda: _kc
        _ks.commit = lambda why: None
        _svk = (missionboard.MISSION_REPORT, charstore.CHAR_STORE)
        try:
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            _ks.accept_mission(7, "Recon Alpha", 0, reward=(1200, 30),
                               sector=72117, zone=200, cat=1, nation=1)
            _k1 = missionbook.mission_key(_kc["missions"][0])
            _kc["missions"][0]["status"] = "complete"
            _ks.accept_mission(7, "Recon Alpha", 0, reward=(1200, 30),
                               sector=72117, zone=200, cat=1, nation=1)
            _k2 = missionbook.mission_key(_kc["missions"][1])
            _kl = missionbook.accepted_list_rows(missionbook.accepted_missions(_kc))
            _old = {"id": 7, "name": "old", "at": _iso0(_T0)}   # pre-key accept
            _key_ok = (_k1 == 7 + (1 << 16) and _k2 == 7 + (2 << 16)
                       and (_k1 & 0xFFFF) == (_k2 & 0xFFFF) == 7
                       and missionbook.mission_next_key([], 7) == 7 + (1 << 16)
                       and missionbook.mission_next_key([], 0x10000) is None
                       and missionbook.mission_key(_old) == 7
                       and [r[0] for r in _kl] == [_k2, _k1]   # active first
                       and missionbook.mission_find(_kc, _k1)["status"] == "complete"
                       and missionbook.mission_find(_kc, _k2).get("status") is None
                       and missionbook.mission_find(_kc, 7) is _kc["missions"][1]
                       and _kc["missions"][1]["zone"] == 200
                       and _kc["missions"][1]["cat"] == 1
                       and _kc["missions"][1]["nation"] == 1
                       and "needed" not in _kc["missions"][1])
            # Report by the OLD key -> state 5 result 2 (28:4 for a complete
            # one, it was reported already so nothing new is owed); by the
            # NEW key -> state 4 (28:3)
            _rqk = lambda msg, k: packet.parse(packet.build(msg, struct.pack("<I", k)
                                                            + bytes(32), seq=packet.SEQ_MIN,
                                                            conn_id=1))
            _ro = packet.parse(_ks.on_packet(_rqk(missionboard.MSG_MISSION_REPORT, _k1))[0])["payload"]
            _rn = packet.parse(_ks.on_packet(_rqk(missionboard.MSG_MISSION_REPORT, _k2))[0])["payload"]
            _key_ok = (_key_ok and _u32(_ro, 0x20 + missionboard.MR_STATE) == 5
                       and _u32(_ro, 0x20 + missionlist.ML_KEY) == _k1
                       and _u32(_rn, 0x20 + missionboard.MR_STATE) == 4
                       and _u32(_rn, 0x20 + missionlist.ML_KEY) == _k2
                       and _u32(missionlist.reply_018e_short(rows=_kl), missionlist.M18E_RECORDS + missionlist.ML_KEY)
                       == _k2)
            # Cancel by the OLD key touches nothing; by the NEW key cancels it
            _ks.on_packet(_rqk(missionboard.MSG_MISSION_CANCEL, _k1))
            _key_ok = _key_ok and _kc["missions"][1].get("status") is None
            _ks.on_packet(_rqk(missionboard.MSG_MISSION_CANCEL, _k2))
            _key_ok = (_key_ok and _kc["missions"][1]["status"] == "cancelled"
                       and _kc["missions"][0]["status"] == "complete")
        finally:
            flat_globals()["MISSION_REPORT"], flat_globals()["CHAR_STORE"] = _svk
        # (2) the battlefield is (ZONE, tile): a battle on the same tile in
        # another zone settles nothing; an accept with no zone keeps the
        # tile-only rule
        _zb = {"missions": [_mm(sector=72117, zone=207),
                            _mm(id=9, name="NoZone", sector=72117)]}
        _zb1 = missionbook.mission_battle_apply(_zb, True, now=_T0 + 60, limit=1800,
                                                cats={7: 1, 9: 1}, tile=72117, zone=200)
        _zb2 = missionbook.mission_battle_apply(_zb, True, now=_T0 + 90, limit=1800,
                                                cats={7: 1, 9: 1}, tile=72117, zone=207)
        _zone_bind_ok = ([(m["id"], s) for m, s in _zb1] == [(9, "met")]
                         and [(m["id"], s) for m, s in _zb2] == [(7, "met")])
        # (3) SECTOR MISSIONS: N wins on (zone, tile) by the taker's nation,
        # after the accept, inside the deadline, whoever fought them
        _led = {}
        _sm = _mm(id=11, name="Sector Sweep", cat=2, needed=2, zone=207,
                  sector=89135, nation=1)
        _st = lambda now: missionbook.mission_status(_sm, now=now, limit=1800, ledger=_led)
        _sec2_ok = _st(_T0 + 5) == "open"
        sectorwins.sector_win_record(207, 89135, 1, now=_T0 - 5, ledger=_led)   # before
        sectorwins.sector_win_record(207, 89135, 2, now=_T0 + 8, ledger=_led)   # enemy
        sectorwins.sector_win_record(200, 89135, 1, now=_T0 + 9, ledger=_led)   # zone
        sectorwins.sector_win_record(207, 89135, 1, now=_T0 + 10, ledger=_led)
        _sec2_ok = _sec2_ok and _st(_T0 + 11) == "open"
        sectorwins.sector_win_record(207, 89135, 1, now=_T0 + 20, ledger=_led)
        _sec2_ok = (_sec2_ok and _st(_T0 + 21) == "met"
                    and sectorwins.sector_wins_between(207, 89135, 1, _T0, _T0 + 21,
                                                       _led) == 2
                    # a battle end does not touch a category-2 accept itself
                    and missionbook.mission_battle_apply({"missions": [dict(_sm)]}, True,
                                                         now=_T0 + 60, limit=1800,
                                                         tile=89135, zone=207) == [])
        _sm_late = _mm(id=12, name="Late Sector", cat=2, needed=1, zone=207,
                       sector=89135, nation=1, at=_iso0(_T0 - 2000))
        sectorwins.sector_win_record(207, 89135, 1, now=_T0 - 100, ledger=_led)  # too late
        _sec2_ok = (_sec2_ok and missionbook.mission_status(_sm_late, now=_T0, limit=1800,
                                                            ledger=_led) == "expired"
                    and missionbook.mission_deadline(2) == 0)     # knob off: no deadline
        _sv_sd = (missionboard.MISSION_REPORT, warmap.MISSION_DEADLINE_SECTOR)
        try:
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["MISSION_DEADLINE_SECTOR"] = 7200
            _sec2_ok = (_sec2_ok and missionbook.mission_deadline(2) == 7200
                        and missionbook.mission_deadline(1) == missionboard.MISSION_DEADLINE)
        finally:
            (flat_globals()["MISSION_REPORT"],
             flat_globals()["MISSION_DEADLINE_SECTOR"]) = _sv_sd
        # ...and through a Session: the taker's own win is recorded in the
        # process ledger and their sector accept reads "met (sector wins)"
        _sv_led = dict(sectorwins.SECTOR_WINS)
        _sc = {"missions": [], "nation_byte": 1}   # nation_byte, never "nation"
        _ss = session.Session("sectortest:0")
        _ss.playing_char = lambda: _sc
        _ss.commit = lambda why: None
        _svs = (missionboard.MISSION_REPORT, charstore.CHAR_STORE, list(community.MSN_ROWS), community.MSN_FIELDS,
                warmap.MSN_WINS, dict(rooms.WORLD_ZONES))
        _sv_swp = sectorwins.SECTOR_WINS_STORE
        try:
            sectorwins.SECTOR_WINS_STORE = False          # never the real ledger
            sectorwins.SECTOR_WINS.clear()
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            flat_globals()["MSN_ROWS"] = ["11/2:Sector Sweep"]
            flat_globals()["MSN_FIELDS"] = "0:0x%X=89135" % fmomsn.MISSION_SECTOR
            flat_globals()["MSN_WINS"] = "0:1"
            rooms.WORLD_ZONES["sectortest"] = 207
            _rqw = missionbook.mission_requirements()[11]
            _ss.accept_mission(11, "Sector Sweep", 0, sector=_rqw["sector"],
                               zone=207, cat=_rqw["cat"], nation=1,
                               wins=_rqw["wins"])
            _ss.sector = (89135, 21, 42)
            _ss.sector_zone = 207
            _mv3 = _ss.mission_battle_settle(True)
            _sec2_ok = (_sec2_ok and _rqw["cat"] == 2 and _rqw["wins"] == 1
                        and _sc["missions"][0]["needed"] == 1
                        and [(m["id"], s) for m, s in _mv3]
                        == [(11, "met (sector wins)")]
                        and missionbook.mission_status(_sc["missions"][0]) == "met"
                        and (207, 89135, 1) in sectorwins.SECTOR_WINS)
        finally:
            (flat_globals()["MISSION_REPORT"], flat_globals()["CHAR_STORE"],
             flat_globals()["MSN_ROWS"], flat_globals()["MSN_FIELDS"],
             flat_globals()["MSN_WINS"], _wz) = _svs
            rooms.WORLD_ZONES.clear()
            rooms.WORLD_ZONES.update(_wz)
            sectorwins.SECTOR_WINS.clear()
            sectorwins.SECTOR_WINS.update(_sv_led)
            sectorwins.SECTOR_WINS_STORE = _sv_swp
        # (4) the COUNTER-MISSION arrow: an enemy-side pilot fighting on the
        # map with an open mission for (zone, tile); the viewer's own mission
        # wins the byte; a win there owes a kind-2 line
        _cm_e = {"missions": [_mm(sector=72117, zone=200,
                                  at=_iso0(int(time.time())))]}
        _cm_f = {"missions": []}
        _cm_rost = {"enemy1": [_cm_e], "friend1": [_cm_f], "audit": [_cm_f]}
        _cm_side = {"enemy1": 1, "friend1": 0, "audit": 0}
        _sv_cm = (warmap.COUNTER_MISSION, warmap.COUNTER_BONUS_HS, missionboard.MISSION_REPORT,
                  charstore.CHAR_STORE, zoneentry.MAPKIND, dict(referee.BATTLE_STATE), dict(rooms.WORLD_ZONES),
                  flat_globals()["battle_side_for"], flat_globals()["load_roster"],
                  flat_globals()["account_for"])
        try:
            flat_globals()["COUNTER_MISSION"] = 3
            flat_globals()["COUNTER_BONUS_HS"] = 0
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            flat_globals()["MAPKIND"] = 200
            flat_globals()["battle_side_for"] = lambda h: (_cm_side.get(h), "stub")
            flat_globals()["load_roster"] = lambda a: _cm_rost.get(a, [])
            flat_globals()["account_for"] = lambda ip: ip
            referee.BATTLE_STATE.clear()
            referee.BATTLE_STATE["enemy1"] = {"mapno": 267, "granted_at": time.time() - 30}
            referee.BATTLE_STATE["friend1"] = {"mapno": 267, "granted_at": time.time() - 20}
            rooms.WORLD_ZONES["audit"] = 200
            _emr = popnation.enemy_missions_running(267, 72117, 200, 0)
            _counter_ok = ([h for h, _m in _emr] == ["enemy1"]
                           # the enemy's own view: nobody of side 0 holds one
                           and popnation.enemy_missions_running(267, 72117, 200, 1) == []
                           # another zone's grid, another map: nothing
                           and popnation.enemy_missions_running(267, 72117, 207, 0) == []
                           and popnation.enemy_missions_running(232, 72117, 200, 0) == [])
            _cs = session.Session("audit:0")
            _cs.playing_char = lambda: _cm_f
            _cs.commit = lambda why: None
            _p_arrow = packet.parse(_cs.on_packet(_rq5e(72117))[0])["payload"]
            _counter_ok = (_counter_ok
                           and _p_arrow[warmap.S15F_HEAD_LEN + warmap.S15F_ICON] == 3
                           and _u32(_p_arrow, warmap.S15F_HEAD_LEN) == 267
                           and _cs.sector_zone == 200)
            # the viewer's OWN mission there wins the byte
            _cm_f["missions"] = [_mm(id=9, sector=72117, zone=200,
                                     at=_iso0(int(time.time())))]
            _p_own = packet.parse(_cs.on_packet(_rq5e(72117))[0])["payload"]
            _counter_ok = (_counter_ok
                           and _p_own[warmap.S15F_HEAD_LEN + warmap.S15F_ICON] == warmap.MISSION_MAP_ICON)
            _cm_f["missions"] = []
            # knob off: no arrow
            flat_globals()["COUNTER_MISSION"] = 0
            _p_off = packet.parse(_cs.on_packet(_rq5e(72117))[0])["payload"]
            _counter_ok = _counter_ok and _p_off[warmap.S15F_HEAD_LEN + warmap.S15F_ICON] == 0
            # a WIN there: log only at 0, a kind-2 line owed at 500
            flat_globals()["COUNTER_MISSION"] = 3
            _cs.sector = (72117, 20, 267)
            _cs.sector_zone = 200
            _h0 = _cs.counter_mission_settle(True, 200, 72117, 267, _cm_f)
            _counter_ok = (_counter_ok and len(_h0) == 1
                           and not _cm_f.get("mission_pay"))
            flat_globals()["COUNTER_BONUS_HS"] = 500
            _h1 = _cs.counter_mission_settle(True, 200, 72117, 267, _cm_f)
            _cpr, _cpaid = missionbook.mission_pay_rows(_cm_f, [])
            _counter_ok = (_counter_ok and len(_h1) == 1
                           and len(_cpr) == 2 and _cpr[1][0] == warmap.PAY_KILL
                           and _cpr[1][3] == 500
                           and servicerecord.PAY_KINDS[warmap.PAY_KILL] == "Kill bonus"
                           and _cm_f["mission_pay"] == []
                           # a loss owes nothing
                           and _cs.counter_mission_settle(False, 200, 72117,
                                                          267, _cm_f) == [])
        finally:
            (flat_globals()["COUNTER_MISSION"], flat_globals()["COUNTER_BONUS_HS"],
             flat_globals()["MISSION_REPORT"], flat_globals()["CHAR_STORE"],
             flat_globals()["MAPKIND"], _bs0, _wz0, flat_globals()["battle_side_for"],
             flat_globals()["load_roster"], flat_globals()["account_for"]) = _sv_cm
            referee.BATTLE_STATE.clear()
            referee.BATTLE_STATE.update(_bs0)
            rooms.WORLD_ZONES.clear()
            rooms.WORLD_ZONES.update(_wz0)
        # (5) FMO_PERMIT_RANKS: passes by rank, the nation picking the id
        _pr = permits.parse_permit_ranks("6:oc, 15:FLZ,0:27,x:y,3:99,4:hq")
        _permit_rank_ok = (_pr == [(6, "oc"), (15, "flz"), (0, 27), (4, "hq")]
                           and permits.permit_rank_passes(1, 6, _pr) == [27, 25]
                           and permits.permit_rank_passes(2, 20, _pr) == [28, 29, 27, 26]
                           and permits.permit_rank_passes(1, 5, _pr) == [27, 25]
                           and permits.permit_rank_passes(1, 3, _pr) == [27]
                           and permits.permit_rank_passes(1, None, _pr) == []
                           and permits.parse_permit_ranks("") == [])
        _pp = {"items": [], "nation_byte": 2}
        _ps = session.Session("permit:0")
        _ps.playing_char = lambda: _pp
        _ps.commit = lambda why: None
        _sv_pr = (permits.PERMIT, permits.PERMIT_ALL, permits.PERMIT_RANKS, charstore.CHAR_STORE, status.STATUS_NATION)
        try:
            flat_globals()["PERMIT"] = False
            flat_globals()["PERMIT_ALL"] = False
            flat_globals()["PERMIT_RANKS"] = [(6, "oc")]
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            _g0 = _ps.grant_hq_pass(1, rank=5)          # below: nothing
            _g1 = _ps.grant_hq_pass(1, rank=6)          # earned: OC-U.S.N.
            _g2 = _ps.grant_hq_pass(1, rank=21)         # held: nothing
            _ids = [it["id"] for it in inventory.stored_items(_pp)]
            _permit_rank_ok = (_permit_rank_ok and _g0 is None
                               and _g1 is not None and _ids == [28]
                               and packet.parse(_g1)["msg"] == shop.MSG_ACQUIRE_REPLY
                               and _g2 is None)
            flat_globals()["PERMIT_RANKS"] = []
            _permit_rank_ok = (_permit_rank_ok
                               and _ps.grant_hq_pass(1, rank=21) is None)
        finally:
            (flat_globals()["PERMIT"], flat_globals()["PERMIT_ALL"],
             flat_globals()["PERMIT_RANKS"], flat_globals()["CHAR_STORE"],
             flat_globals()["STATUS_NATION"]) = _sv_pr
        # (6) FMO_MSN_ZONES: the op-9 query's own MapKind leaves out rows
        # issued in another area; unzoned rows and an unzoned query pass
        _sv_mz = (list(community.MSN_ROWS), sectorwins.MSN_ZONES)
        try:
            flat_globals()["MSN_ROWS"] = ["7/1:In200", "8/1:In207", "9/1:Anywhere"]
            flat_globals()["MSN_ZONES"] = "0:200,1:207"
            _mzn = lambda rows: [fmomsn.parse(fmomsn.page(rows))[1]
                                 [8 + i * fmomsn.RECORD_LEN + 4:
                                  8 + i * fmomsn.RECORD_LEN + 0x20]
                                 .split(bytes(1))[0] for i in range(len(rows))]
            _msn_zone_ok = (_mzn(community.msn_rows(1, 207)) == [b"In207", b"Anywhere"]
                            and _mzn(community.msn_rows(1, 200)) == [b"In200", b"Anywhere"]
                            and len(community.msn_rows(1, 0)) == 3
                            and len(community.msn_rows(1)) == 3
                            and len(community.msn_rows(1, 0xFFFF)) == 3
                            and missionbook.mission_requirements()[8]["zone"] == 207
                            and missionbook.mission_requirements()[9]["zone"] is None)
        finally:
            flat_globals()["MSN_ROWS"], flat_globals()["MSN_ZONES"] = _sv_mz
        # (7) AREA MISSIONS PICK THEIR TARGET: 0x01A8 -> the 0x01A9 list,
        # the pick rides the 0x018A (+0x28 = 1, +0x2C = 903M + tile)
        _war3 = {}
        _sv_at = (areatargets.AREA_TARGETS, missionboard.MISSION_REPORT, missionboard.MISSION_ACCEPT, charstore.CHAR_STORE,
                  list(community.MSN_ROWS), community.MSN_FIELDS, dict(rooms.WORLD_ZONES),
                  flat_globals()["area_sector_state"])
        try:
            flat_globals()["area_sector_state"] = lambda t: _war3.get(int(t))
            _all = areatargets.area_target_tiles(200, 1, state_of=lambda t: None)
            _tA, _tB = _all[0], _all[1]
            _war3[_tA] = (1, 60, False)          # ours -> not a target
            _war3[_tB] = (2, 100, False)         # the enemy's -> a target
            _lst1 = areatargets.area_target_tiles(200, 1)
            _lst2 = areatargets.area_target_tiles(200, 2)
            _b9 = areatargets.reply_01a9(_lst1)
            _area_ok = (len(_all) >= 2 and len(_all) <= areatargets.A9_ROW_MAX
                        and all(fmosectors.SECTORS[200][t][1] in missionlist.TYPE1_ON_DISK
                                for t in _all)
                        and _tA not in _lst1 and _tB in _lst1
                        and _tA in _lst2 and _tB not in _lst2
                        and areatargets.area_target_tiles(None, 1) == []
                        and lobapi.LOBAPI[areatargets.MSG_AREA_TARGETS_REQ] == (0x01A9, len(_b9))
                        and len(_b9) == 0x424
                        and _u32(_b9, areatargets.A9_COUNT) == len(_lst1)
                        and _u32(_b9, areatargets.A9_ROWS) == 903_000_000 + _lst1[0]
                        and _u32(_b9, areatargets.A9_ERROR) == 0
                        and areatargets.A9_ROW_MAX == 256
                        and _u32(areatargets.reply_01a9(range(1, 400)), areatargets.A9_COUNT) == 256)
            flat_globals()["AREA_TARGETS"] = True
            flat_globals()["MISSION_REPORT"] = True
            flat_globals()["MISSION_ACCEPT"] = "gate"
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            flat_globals()["MSN_ROWS"] = ["13/3:Deep Strike", "7/1:Recon Alpha"]
            flat_globals()["MSN_FIELDS"] = ""
            rooms.WORLD_ZONES["area"] = 200
            _ac = {"missions": [], "nation_byte": 1}
            _as = session.Session("area:0")
            _as.playing_char = lambda: _ac
            _as.commit = lambda why: None
            # the list for an AREA row; nothing for a battle-map row
            _l13 = _as.area_targets_reply(struct.pack("<I", 13) + bytes(8),
                                          0x424, 1)
            _l7 = _as.area_targets_reply(struct.pack("<I", 7) + bytes(8),
                                         0x424, 1)
            # ...and the wiring: a real 0x01A8 through the session
            _w = _as.on_packet(packet.parse(packet.build(areatargets.MSG_AREA_TARGETS_REQ,
                                                         struct.pack("<I", 13) + bytes(8),
                                                         seq=packet.SEQ_MIN, conn_id=1)))
            _wp = packet.parse(_w[0])
            _area_ok = (_area_ok and _l13 is not None and _l7 is None
                        and _u32(_l13, areatargets.A9_COUNT) == len(_lst1)
                        and _wp["msg"] == 0x01A9
                        and _u32(_wp["payload"], areatargets.A9_COUNT) > 0)
            _acc = lambda t: packet.parse(_as.on_packet(packet.parse(packet.build(
                missionboard.MSG_MISSION_ACCEPT,
                struct.pack("<I", 13) + bytes(areatargets.AREA_FLAG - 4)
                + struct.pack("<II", 1, 903_000_000 + t)
                + bytes(0x7C - areatargets.AREA_TARGET - 4),
                seq=packet.SEQ_MIN, conn_id=1)))[0])
            _r_ours = _acc(_tA)                  # ours: 27:11
            _r_ok = _acc(_tB)                    # the enemy's: accepted
            _m3 = missionbook.accepted_missions(_ac)[-1] if _ac["missions"] else {}
            _area_ok = (_area_ok and _r_ours["msg"] == charselect.MSG_FAIL
                        and _r_ours["conn"] == (-9 & 0xFFFF)
                        and _r_ok["msg"] == missionboard.MSG_MISSION_ACCEPT_REPLY
                        and len(_ac["missions"]) == 1
                        and _m3.get("sector") == _tB and _m3.get("cat") == 3
                        and _m3.get("zone") == 200
                        and _m3.get("control_needed") == areatargets.AREA_CONTROL)
            # met from the war state: ours at >= FMO_AREA_CONTROL, inside
            # the deadline; the enemy's, or below the rate, stays open
            _s_enemy = missionbook.mission_status(_m3)
            _war3[_tB] = (1, areatargets.AREA_CONTROL - 1, False)
            _s_low = missionbook.mission_status(_m3)
            _war3[_tB] = (1, areatargets.AREA_CONTROL, False)
            _s_met = missionbook.mission_status(_m3)
            _war3[_tB] = (1, 100, True)          # deadlock: not ours
            _s_dead = missionbook.mission_status(_m3)
            _area_ok = (_area_ok and _s_enemy == "open" and _s_low == "open"
                        and _s_met == "met" and _s_dead == "open"
                        and missionbook.mission_battle_apply({"missions": [dict(_m3)]},
                                                             True, tile=_tB, zone=200) == [])
            # knob off: no list, and a picked target is NOT snapshotted
            flat_globals()["AREA_TARGETS"] = False
            _ac["missions"] = []
            _off13 = _as.area_targets_reply(struct.pack("<I", 13) + bytes(8),
                                            0x424, 1)
            _r_off = _acc(_tA)
            _area_ok = (_area_ok and _off13 is None
                        and _r_off["msg"] == missionboard.MSG_MISSION_ACCEPT_REPLY
                        and not missionbook.accepted_missions(_ac)[-1].get("control_needed"))
        finally:
            (flat_globals()["AREA_TARGETS"], flat_globals()["MISSION_REPORT"],
             flat_globals()["MISSION_ACCEPT"], flat_globals()["CHAR_STORE"],
             flat_globals()["MSN_ROWS"], flat_globals()["MSN_FIELDS"], _wz3,
             flat_globals()["area_sector_state"]) = _sv_at
            rooms.WORLD_ZONES.clear()
            rooms.WORLD_ZONES.update(_wz3)
        # (8) the ledger survives a restart: save, a fresh load, pruning
        # (in the throwaway database; SKIPped without one)
        _persist_ok = None
        if test_db:
            import tempfile as _tf
            _wd = _tf.mkdtemp(prefix="fmo-wins-")
            _sv_leg = sectorwins.SECTOR_WINS_LEGACY
            sectorwins.SECTOR_WINS_LEGACY = os.path.join(_wd, "none.json")
            _l8 = {}
            _T8 = 1_800_000_000
            _empty0 = sectorwins.sector_wins_load(True, {}) == 0
            sectorwins.sector_win_record(207, 89135, 1, now=_T8 - 10, ledger=_l8)
            sectorwins.sector_win_record(200, 71122, 2, now=_T8 - 400000, ledger=_l8)
            _saved = sectorwins.sector_wins_save(True, _l8, now=_T8)
            _l8b = {}
            _loaded = sectorwins.sector_wins_load(True, _l8b)
            # the old JSON file fills an EMPTY table once, bad keys skipped
            sectorwins.sector_wins_save(True, {}, now=_T8)
            with open(os.path.join(_wd, "old.json"), "w", encoding="utf-8") as _fh:
                _fh.write('{"505:85102:1": [%d], "bad": [1], "509:94101:x": [2]}' % (_T8 - 5))
            sectorwins.SECTOR_WINS_LEGACY = os.path.join(_wd, "old.json")
            _l8c = {}
            _imp = sectorwins.sector_wins_load(True, _l8c)
            sectorwins.SECTOR_WINS_LEGACY = _sv_leg
            _persist_ok = (_empty0 and _saved and _loaded == 1
                           and _l8b == {(207, 89135, 1): [_T8 - 10]}
                           and _imp == 1 and _l8c == {(505, 85102, 1): [_T8 - 5]}
                           and sectorwins.sector_wins_save(False, _l8b) is False)
        # (9) -8: another pilot's active area accept on the same (zone, tile)
        _other = {"missions": [_mm(id=13, name="Deep Strike", cat=3,
                                   sector=71122, zone=200,
                                   at=_iso0(int(time.time())))]}
        _done = {"missions": [_mm(id=13, name="Old", cat=3, sector=71121,
                                  zone=200, status="complete")]}
        _ros = [("member:1", [_other]), ("member:2", [_done])]
        _taken_ok = (areatargets.area_target_taken(200, 71122, "member:9", _ros)
                     == ("member:1", "Deep Strike")
                     and areatargets.area_target_taken(200, 71122, "member:1", _ros) is None
                     and areatargets.area_target_taken(207, 71122, "member:9", _ros) is None
                     and areatargets.area_target_taken(200, 71121, "member:9", _ros) is None
                     and missionboard.MISSION_REFUSALS[-8].startswith("27:13"))
        # (10) the ORDER submit is logged field by field and kept
        _oc = {"missions": [dict(_mm(id=11, name="Sector Sweep", cat=2,
                                     sector=71122, zone=200), key=65547)]}
        _os10 = session.Session("order:0")
        _os10.playing_char = lambda: _oc
        _os10.commit = lambda why: None
        _ob = bytearray(0x20 + 0x14C)
        for _o, _v in ((0x000, 5), (0x004, 65547), (0x00C, 3), (0x034, 7),
                       (0x038, 1200), (0x13C, 4), (0x140, 20), (0x144, 9)):
            struct.pack_into("<I", _ob, 0x20 + _o, _v)
        _ob[0x20 + areatargets.ORDER_NAME:0x20 + areatargets.ORDER_NAME + 9] = b"Lex.Arden"
        _ob[0x20 + areatargets.ORDER_COMMENT:0x20 + areatargets.ORDER_COMMENT + 6] = b"help!!"
        _svo = charstore.CHAR_STORE
        try:
            flat_globals()["CHAR_STORE"] = "selftest-stub"
            _or = _os10.log_order(bytes(_ob))
            _w10 = _os10.on_packet(packet.parse(packet.build(areatargets.MSG_ORDER_REQ, bytes(_ob),
                                                             seq=packet.SEQ_MIN, conn_id=1)))
        finally:
            flat_globals()["CHAR_STORE"] = _svo
        _order_ok = (_or is not None and _or["key"] == 65547
                     and _or["issuer"] == "Lex.Arden" and _or["comment"] == "help!!"
                     and _or["fields"]["0x038"] == 1200
                     and _or["from"]["name"] == "Sector Sweep"
                     and len(_oc["orders"]) == 2
                     and lobapi.LOBAPI[areatargets.MSG_ORDER_REQ][0] == 0x0195
                     and len(_ob) == 364 == pushes.CLIENT_REQUESTS[areatargets.MSG_ORDER_REQ]
                     # FMO_ORDER: a source row we do not serve has no template,
                     # so the order is REFUSED (27:4) instead of a hollow 21:27
                     and packet.parse(_w10[0])["msg"] == (charselect.MSG_FAIL
                                                          if missionboard.ORDER else 0x0195)
                     and _os10.log_order(b"short") is None)
        for _lbl, _v in (
                ("round 3b: the sector-win ledger is saved and reloaded "
                 "(FMO_SECTOR_WINS, the database), pruned to FMO_SECTOR_WINS_KEEP; "
                 "an empty table loads nothing; the old JSON file fills an empty "
                 "table once; memory only saves nothing", _persist_ok),
                ("round 3b: -8 (27:13): another pilot's ACTIVE area accept on "
                 "the same (zone, tile) refuses the pick; own, other zone, "
                 "closed ones do not", _taken_ok),
                ("round 3b: the ORDER submit 0x0194 is decoded field by field, "
                 "kept on the pilot and answered (0x0195, or the 27:4 refusal "
                 "when FMO_ORDER has no served source row)", _order_ok),
                ("round 3: AREA missions pick their target: 0x01A8 -> 0x01A9 "
                 "lists the zone's on-disk sectors not held by the nation as "
                 "903M + tile (count +0x20, rows +0x24, 256 max); the pick in "
                 "0x018A +0x28/+0x2C is snapshotted or refused -9; met from "
                 "the war state at FMO_AREA_CONTROL; knob off = zeros",
                 _area_ok),
                ("round 3: a per-accept KEY at record+0x00 (id + n<<16): two "
                 "accepts of one row list apart, Report/Cancel act on the row "
                 "the key names, an old accept keys on its id", _key_ok),
                ("round 3: the battlefield is (ZONE, tile): the same tile in "
                 "another zone settles nothing; no stored zone = tile only",
                 _zone_bind_ok),
                ("round 3: SECTOR missions: N wins on (zone, tile) by the "
                 "nation after the accept and inside the deadline, whoever "
                 "fought; a battle end feeds the ledger and reads 'met (sector "
                 "wins)'; FMO_MISSION_DEADLINE_SECTOR", _sec2_ok),
                ("round 3: the COUNTER-MISSION arrow: an enemy fighting on the "
                 "map with an open mission for (zone, tile) -> row +0x0A = "
                 "FMO_COUNTER_MISSION; own mission wins; off = 0; a win there "
                 "owes a kind-2 Kill bonus line of FMO_COUNTER_BONUS_HS",
                 _counter_ok),
                ("round 3: FMO_PERMIT_RANKS: '<rank>:<hq|oc|flz|id>' passes "
                 "by rank, the nation picking the id; below = nothing, held = "
                 "nothing, FMO_PERMIT off leaves only these", _permit_rank_ok),
                ("round 3: FMO_MSN_ZONES: the op-9 query's MapKind leaves out "
                 "rows issued in another area; unzoned rows/queries pass",
                 _msn_zone_ok),
                ("every grant stores the pilot's zone on their record (the "
                 "content profile's Zone): a change writes once, a repeat or a "
                 "0xFFFF sentinel does not", _zprof_ok),
                ("the war map looks sectors up in the PILOT'S zone (207: tile "
                 "89135 -> Sector 21, map 42), FMO_MAPKIND only when the zone "
                 "is unknown or has no table", _zone_ok),
                ("the Battle Map list entry: live sorties on that map, not "
                 "ended and inside the limit, by side; minutes ELAPSED; the "
                 "bar viewer-relative; offsets = the draw's", _entry_ok),
                ("the binding: a battle completes a mission only in ITS tile, "
                 "an unbound one anywhere, and a sortie with no sector moves "
                 "only unbound ones", _bind_ok),
                ("the map selector: the mission's tile gets row +0x0A = the "
                 "icon, another tile does not, knob off leaves it 0, and a "
                 "battle end settles by the Session's sector", _icon_ok),
                ("the battlefield: +0x1F4 is read back from the served row "
                 "and snapshotted on the accept", _sec_ok),
                ("end to end through a pilot: 28:3 before, battle meets only "
                 "the battle-map row, report 28:4, the paybook pays H$ 1200 / "
                 "MP 30 once, cancel cancels", _e2e2_ok),
                ("the deadline: open at 1799 s, expired at 1801 s, 0 = none, "
                 "a met mission never expires", _dl_ok),
                ("every status -> the (state, result) whose group-28 sentence "
                 "0x611CA9B0 draws", _st_ok),
                ("a battle: a win meets a battle-map mission, a sector one is "
                 "untouched, a late win is too late, a loss fails and the "
                 "first battle decides", _bt_ok),
                ("the report: met -> complete with the reward owed ONCE; a "
                 "late open one is written down expired", _rp_ok),
                ("the paybook: a kind-5 Mission bonus line, paid once, kept "
                 "when there is no room, sharing the salary's day header",
                 _pay_ok),
                ("the cancel: active -> cancelled (25:10) and no longer "
                 "blocks a re-accept; pruning keeps the active ones", _cn_ok),
                ("the wire: 0x018F is 724 B, its +0x118/+0x234 are the poll's "
                 "G+0x5A58/G+0x5B74, View B carries the same fields", _wire_ok),
                ("the knob: OFF is byte-identical (724 zeros, id+name rows, no "
                 "deadline); ON with no pilot still answers both", _off_ok)):
            if _v is None:              # needs the test database, which is absent
                print(f"  mission report: {_lbl}: SKIP (no test database)")
                continue
            print(f"  mission report: {_lbl}: {'OK' if _v else 'FAIL'}")
            ok &= _v
        # WARNING: THE KILL. Answering an UNDECODED op with 0x1B killed the client at
        # 2026-09-06T17:45:57Z (op 6, the war map's mode-0 kind-0 job). Pin that
        # every op we have not earned a reply shape for gets SILENCE, and that
        # only the reproduce switch brings 0x1B back.
        _sv_unk = community.MSN_UNKNOWN
        try:
            flat_globals()["MSN_ON"] = True
            flat_globals()["MSN_UNKNOWN"] = "silent"
            # WARNING: 0x07 LEFT THIS LIST 2026-09-09 -- it is the Scramble Board's
            # group list and is now DECODED and answered (see the board-list
            # check above). It is deliberately re-pinned the other way below,
            # so this list shrinking cannot quietly become "we answer
            # everything now".
            # (0x10 left this list on 2026-09-12: kind 7 is DECODED and served;
            # 0x0E on 2026-09-30: kind 5, the ORDER templates, is served)
            _quiet = all(community.msn_reply("selftest", _op, fmomsn.OP6_BODY_LIVE) == []
                         for _op in (0x06, 0x08, 0x99))
            _board_still_on = [o for o, _ in
                               community.msn_reply("selftest", 0x07,
                                                   fmomsn.OP6_BODY_LIVE)]
            _quiet = _quiet and _board_still_on == [fmomsn.OP_END]
            flat_globals()["MSN_UNKNOWN"] = "end"
            _repro = [o for o, _ in community.msn_reply("selftest", 0x06,
                                                        fmomsn.OP6_BODY_LIVE)]
        finally:
            flat_globals()["MSN_UNKNOWN"] = _sv_unk
        print(f"  community: an op we have NOT decoded gets SILENCE, not the "
              f"0x1B that killed the client on 09-06: "
              f"{'OK' if _quiet else 'FAIL'}")
        ok &= _quiet
        print(f"  community: FMO_MSN_UNKNOWN=end is the reproduce switch and "
              f"still emits 0x1B: "
              f"{'OK' if _repro == [fmomsn.OP_END] else 'FAIL'}")
        ok &= _repro == [fmomsn.OP_END]
        # WARNING: compose passes "${FMO_MSN:-}", so an unset prod .env hands us an
        # EMPTY STRING. This pin exists because that is exactly how this knob
        # shipped OFF while every doc said it was on -- caught by reading it
        # back out of the running container, not from .env.
        _envmsn_ok = (os.environ.get("FMO_MSN", "").strip() or "1") == "1"
        print(f"  community: an EMPTY FMO_MSN (what compose passes when prod's "
              f".env does not set it) still resolves to the '1' default"
              + ("" if "FMO_MSN" not in os.environ or not os.environ["FMO_MSN"].strip()
                 else f" [env pins it to {os.environ['FMO_MSN']!r}, so this pin is "
                      f"vacuous in THIS run]")
              + f": {'OK' if _envmsn_ok or os.environ.get('FMO_MSN','').strip() else 'FAIL'}")
        ok &= (_envmsn_ok or bool(os.environ.get("FMO_MSN", "").strip()))

    # ------------------------------------------------------------------ #
    # THE PUSH CATALOGUE, third pass (static 2026-09-09).
    # ------------------------------------------------------------------ #
    # (a) THE DRIVEN SWEEP, made permanent. Every request the client can build
    # goes through the real dispatcher; the set that falls to "no handler"
    # must be exactly the by-design five. A grep cannot do this (it missed six
    # of ten last time); a new "no handler" here is a regression by name.
    import io as _io
    import contextlib as _ctx
    _unserved = set()
    for _mid, _sz in sorted(pushes.CLIENT_REQUESTS.items()):
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf):
            try:
                session.Session("sweep:0").on_packet(packet.parse(packet.build(
                    _mid, bytes(64 if _sz is None else _sz), seq=0x777,
                    conn_id=1)))
            except Exception as _e:      # a crash is a finding, not a pass
                print(f"EXC {_e!r}")
                _unserved.add(("EXC", _mid))
        if "no handler" in _buf.getvalue():
            _unserved.add(_mid)
    _sweep_ok = _unserved == set(pushes.UNSERVED_BY_DESIGN)
    print(f"  sweep: {len(pushes.CLIENT_REQUESTS)} client requests driven through "
          f"on_packet; 'no handler' set == the by-design five "
          f"{sorted(f'0x{m:04X}' for m in pushes.UNSERVED_BY_DESIGN)}: "
          f"{'OK' if _sweep_ok else 'FAIL -- got ' + str(sorted(map(str, _unserved)))}")
    ok &= _sweep_ok

    # (b) 0x012D is answered like 0x015B's poll wants: message 1, its seq.
    _lh = _one(timesync.MSG_LOCAL_HELLO, bytes(4))
    _lh_ok = (len(_lh) == 1 and _lh[0]["msg"] == handshake.MSG_SESSION_START
              and _lh[0]["seq"] == 0x777 and _lh[0]["payload"] == b"")
    print(f"  0x012D: the non-POL game hello -> empty message 1 on its own "
          f"sequence: {'OK' if _lh_ok else 'FAIL'}")
    ok &= _lh_ok

    # (c) every new push id is one the dispatcher acts on, and every body is
    # the length its arm copies/reads.
    _cat_ok = all(m in pushes.LOBBY_PUSH_ALL for m in (
        timesync.MSG_TIME_SYNC, pushes.MSG_FEE_PUSH, pushes.MSG_ITEM_ANNOUNCE, pushes.MSG_GROUP_ENDED,
        pushes.MSG_GROUP_LOGIN_NOTIFY, pushes.MSG_RESUPPLY, battleend.MSG_BATTLE_END,
        squadron.MSG_SQUADRON_ACTIVATE, squadron.MSG_SQUADRON_ROW, squadron.MSG_SQUADRON_INSIGNIA))
    _cat_ok &= all(m in pushes.LOBBY_PUSH_ALL for m in pushes.PUSH_NOTES)
    print(f"  catalogue: every push id built or noted here is in LOBBY_PUSH_ALL "
          f"({len(pushes.LOBBY_PUSH_ALL)} ids): {'OK' if _cat_ok else 'FAIL'}")
    ok &= _cat_ok
    _ts = packet.parse(timesync.time_sync_packet(1, 1700000000, 250000, now=1700000001.5))
    _ts_ok = (_ts["msg"] == timesync.MSG_TIME_SYNC and _ts["seq"] == pushes.QUEUE_SEQ
              and len(_ts["payload"]) == timesync.S199_LEN
              and struct.unpack("<IIII", _ts["payload"])
              == (1700000000, 250000, 1700000001, 500000))
    print(f"  0x0199: (echo sec, echo usec, our sec, our usec), {timesync.S199_LEN}B on "
          f"the queue seq: {'OK' if _ts_ok else 'FAIL'}")
    ok &= _ts_ok
    _fb = pushes.fee_push_body(12345, sortie_cost=300)
    _fb_ok = (len(_fb) == pushes.S1A1_LEN
              and struct.unpack_from("<I", _fb, pushes.S1A1_MONEY)[0] == 12345
              and struct.unpack_from("<I", _fb, pushes.S1A1_SORTIE_COST)[0] == 300
              and _fb[pushes.S1A1_MOD_REFUSED] == 0
              and pushes.fee_push_body(1, coliseum_fee=7)[pushes.S1A1_COLISEUM_FEE] == 7
              and pushes.fee_push_body(1, spectator_fee=9)[pushes.S1A1_SPECTATOR_FEE] == 9)
    print(f"  0x01A1: money STORE at +0x00, sortie cost +0x04, refusal byte "
          f"+0x08 clear, fees at +0x0C/+0x10, {pushes.S1A1_LEN}B: "
          f"{'OK' if _fb_ok else 'FAIL'}")
    ok &= _fb_ok
    _ia = pushes.item_announce_body(0x1234, 0x0102, 3, "Lex", "Arden")
    _ia_ok = (len(_ia) == pushes.S19B_LEN
              and struct.unpack_from("<H", _ia, pushes.S19B_ITEM_ID)[0] == 0x0102
              and _ia[pushes.S19B_ITEM_KIND] == 3
              and struct.unpack_from("<I", _ia, pushes.S19B_UNIT)[0] == 0x1234
              and _ia[pushes.S19B_FIRST:pushes.S19B_FIRST + 4] == b"Lex\0"
              and _ia[pushes.S19B_LAST:pushes.S19B_LAST + 6] == b"Arden\0")
    print(f"  0x019B: unit at +0x18, (id,kind) at +0x08/+0x0A, names at "
          f"+0x1C/+0x2D, {pushes.S19B_LEN}B: {'OK' if _ia_ok else 'FAIL'}")
    ok &= _ia_ok
    _ge_ok = (pushes.group_ended_body(7, 2) == struct.pack("<II", 7, 2)
              and _raises(lambda: pushes.group_ended_body(7, 6))
              and len(pushes.GROUP_ENDED_REASONS) == 6)
    print(f"  0x0178: (window id, reason 0..5), reason 6 refused: "
          f"{'OK' if _ge_ok else 'FAIL'}")
    ok &= _ge_ok
    _gl = pushes.group_login_notify_body("Sector 7", msn_lsid=5)
    _gl_ok = (len(_gl) == pushes.S188_MIN_LEN
              and struct.unpack_from("<I", _gl, pushes.S188_MSNLSID)[0] == 5
              and _gl[pushes.S188_SECTOR:pushes.S188_SECTOR + 9] == b"Sector 7\0")
    print(f"  0x0188: sector name at +0x08, MsnLSID at +0x04: "
          f"{'OK' if _gl_ok else 'FAIL'}")
    ok &= _gl_ok
    _rec = bytes(range(1, 25))
    _rs = pushes.resupply_body(500, [(_rec, None), (_rec, b"\x01" * 8)])
    _rs_ok = (len(_rs) == pushes.S1A7_LEN
              and struct.unpack_from("<i", _rs, pushes.S1A7_COUNT)[0] == 2
              and _rs[pushes.S1A7_ROWS + pushes.S1A7_ROW_FLAG] == 1
              and _rs[pushes.S1A7_ROWS + pushes.S1A7_ROW_LEN + pushes.S1A7_STOCK_OFF:
                      pushes.S1A7_ROWS + pushes.S1A7_ROW_LEN + pushes.S1A7_STOCK_OFF + 8] == b"\x01" * 8
              and struct.unpack_from("<I", _rs, pushes.S1A7_MONEY)[0] == 500
              and _raises(lambda: pushes.resupply_body(1, [(_rec, None)] * 11)))
    print(f"  0x01A7: rows at +0x08 x 0x18 with +0x12 = 1, stock serial at "
          f"row+0xF0, money at +0x1E8, 11 rows refused: "
          f"{'OK' if _rs_ok else 'FAIL'}")
    ok &= _rs_ok
    _be = battleend.battle_end_body(contrib_new=110, contrib_old=100,
                                    exp_rows=[(2, 40)], won=True, join_pct=110)
    _bl = battleend.battle_end_body(won=False, has_block=False)
    _be_ok = (len(_be) == battleend.S14C_LEN == 0x314
              and _be[battleend.S14C_EXP_ROWS] == 2
              and struct.unpack_from("<I", _be, battleend.S14C_EXP_ROWS + 4)[0] == 40
              and struct.unpack_from("<I", _be, battleend.S14C_CONTRIB_NEW)[0] == 110
              and struct.unpack_from("<I", _be, battleend.S14C_CONTRIB_OLD)[0] == 100
              and _be[battleend.S14C_HAS_BLOCK] == 1 and _bl[battleend.S14C_HAS_BLOCK] == 0
              and struct.unpack_from("<I", _be, battleend.S14C_RESULT)[0] == 2
              and struct.unpack_from("<I", _bl, battleend.S14C_RESULT)[0] == 0
              and _be[battleend.S14C_JOIN_PCT] == 110
              and struct.unpack_from("<I", _be, battleend.S14C_JOIN_BASE)[0] == 1
              and battleend.S14C_BLOCK + battleend.S14C_BLOCK_LEN <= battleend.S14C_CLASS_ROWS
              and battleend.S14C_TAIL + battleend.S14C_TAIL_LEN == battleend.S14C_LEN
              and _raises(lambda: battleend.battle_end_body(exp_rows=[(1, 1)] * 6))
              and _raises(lambda: battleend.battle_end_body(block=b"\0" * 10))
              and battleend.parse_exp_rows("2:40, 5:0x10") == [(2, 40), (5, 16)]
              and _raises(lambda: battleend.parse_exp_rows("2-40")))
    print(f"  0x014C: {battleend.S14C_LEN}B = the 72-B tail's end; exp rows, contribution "
          f"old/new, has-block, the won code, join-in bonus; caps refused: "
          f"{'OK' if _be_ok else 'FAIL'}")
    ok &= _be_ok
    _sq = squadron.squadron_insignia_push_body((5 << 32) | 3, 0x2A, 777)
    _sq_ok = (len(_sq) == squadron.S1C5_LEN
              and struct.unpack_from("<II", _sq, 0) == (3, 5)
              and struct.unpack_from("<H", _sq, squadron.S1C5_INSIGNIA)[0] == 0x2A
              and struct.unpack_from("<I", _sq, squadron.S1C5_MONEY)[0] == 777
              and len(squadron.squadron_row_body(3, 1, -1, 2, 4)) == squadron.S1B1_LEN
              and squadron.squadron_row_body(3, 1, -1, 2, 4)[squadron.SQ_NATION] == 0xFF
              and len(squadron.squadron_activate_body(3)) == squadron.S1AF_LEN)
    print(f"  0x01C5/0x01B1/0x01AF: 64-bit group id lo/hi, insignia u16 at "
          f"+0x08, money at +0x0C; row 16B; activate 8B: "
          f"{'OK' if _sq_ok else 'FAIL'}")
    ok &= _sq_ok
    _pp_ok = (pushes.parse_push_probe("") is None
              and pushes.parse_push_probe("0x019E:05") == (0x019E, b"\x05")
              and _raises(lambda: pushes.parse_push_probe("0x0170:00"))
              and _raises(lambda: pushes.parse_push_probe("nope"))
              and _raises(lambda: pushes.lobby_push_packet(0x0170, b"", 1)))
    print(f"  probe: FMO_PUSH_PROBE parses, and an id outside the catalogue is "
          f"refused before it can vanish at 0x6117F86D: "
          f"{'OK' if _pp_ok else 'FAIL'}")
    ok &= _pp_ok
    # (d) the knobs are OFF unless this run's env says otherwise.
    _off_ok = all(
        (not v) or bool(os.environ.get(k, "").strip())
        for k, v in (("FMO_TIME_SYNC", timesync.TIME_SYNC), ("FMO_SORTIE_COST", pushes.SORTIE_COST),
                     ("FMO_BATTLE_END", battleend.BATTLE_END),
                     ("FMO_INSIGNIA_PUSH", squadron.INSIGNIA_PUSH),
                     ("FMO_PUSH_PROBE", pushes.PUSH_PROBE)))
    print(f"  knobs: FMO_TIME_SYNC / FMO_SORTIE_COST / FMO_BATTLE_END / "
          f"FMO_INSIGNIA_PUSH / FMO_PUSH_PROBE are off unless the env sets them: "
          f"{'OK' if _off_ok else 'FAIL'}")
    ok &= _off_ok

    # ------------------------------------------------------------------ #
    # THE BATTLE MANAGER (static 2026-09-10): its records and the loop's
    # triggers, driven as pure functions.
    # ------------------------------------------------------------------ #
    _bs = fmoworld.record_battle_start(2)
    _bs_body = _bs[fmoworld.REC_HDR:]
    _bs_ok = (struct.unpack_from("<I", _bs, 4)[0] == fmoworld.CMD_BM_BATTLE_START == 138
              and len(_bs_body) == fmoworld.BATTLE_START_LEN
              and struct.unpack("<III", _bs_body) == (0, 2, 0)
              and _raises(lambda: fmoworld.record_battle_start(4))
              and _raises(lambda: fmoworld.record_battle_start(0))
              and set(fmoworld.BATTLE_START_REASONS) == {1, 2, 3})
    print(f"  BM 138: BATTLE START = 12 B (start gametime, reason 1..3, arg); "
          f"reasons 0/4 refused: {'OK' if _bs_ok else 'FAIL'}")
    ok &= _bs_ok
    _sn = fmoworld.record_side_names("O.C.U.", "U.S.N.")[fmoworld.REC_HDR:]
    _sn_ok = (len(_sn) == fmoworld.SIDE_NAMES_LEN
              and _sn[fmoworld.SIDE_NAME_A:fmoworld.SIDE_NAME_A + 7] == b"O.C.U.\0"
              and _sn[fmoworld.SIDE_NAME_B:fmoworld.SIDE_NAME_B + 7] == b"U.S.N.\0")
    print(f"  BM 134: two 80-B names at +0x08 / +0x58: {'OK' if _sn_ok else 'FAIL'}")
    ok &= _sn_ok
    _om = fmoworld.record_objective_marker(0, 1, (64.0, 0.0, 32.0), 123456)[fmoworld.REC_HDR:]
    _om_ok = (len(_om) == fmoworld.OBJECTIVE_LEN == 0x44
              and _om[fmoworld.OBJ_INDEX] == 0
              and struct.unpack_from("<I", _om, fmoworld.OBJ_UNIT)[0] == 1
              and struct.unpack_from("<h", _om, fmoworld.OBJ_X)[0] == 240
              and struct.unpack_from("<h", _om, fmoworld.OBJ_Z)[0] == 120
              and struct.unpack_from("<I", _om, fmoworld.OBJ_DEADLINE_0_3)[0] == 123456
              and struct.unpack_from("<I", _om, fmoworld.OBJ_DEADLINE_4_7)[0] == 123456
              and _raises(lambda: fmoworld.record_objective_marker(4, 1, (0, 0, 0), 1))
              and _raises(lambda: fmoworld.record_objective_marker(0, 1, (99999, 0, 0), 1)))
    print(f"  BM 129: slot-0 marker 0x44 B, s16 position at 4/15 units, deadline "
          f"at +0x20 and +0x18, slots 4+ and out-of-range positions refused: "
          f"{'OK' if _om_ok else 'FAIL'}")
    ok &= _om_ok
    _pe = fmoworld.parse_escape(struct.pack("<I", 0x49895963) + bytes(60))
    _pe_ok = (_pe is not None and _pe[0] == 0x49895963 and "eject" in _pe[1]
              and fmoworld.parse_escape(b"\x01") is None)
    print(f"  cmd 139: the reason code is read and named: {'OK' if _pe_ok else 'FAIL'}")
    ok &= _pe_ok
    _pb_ok = (battleend.parse_battle_end("") == set() and battleend.parse_battle_end("0") == set()
              and battleend.parse_battle_end("escape, limit ,90") == {"escape", "limit", 90}
              and _raises(lambda: battleend.parse_battle_end("soon")))
    print(f"  FMO_BATTLE_END: '', '0', 'escape,limit,90' parse; 'soon' refused: "
          f"{'OK' if _pb_ok else 'FAIL'}")
    ok &= _pb_ok
    _st = {"granted_at": 1000.0, "escaped": None, "ended": False}
    _t_none = referee.battle_end_trigger(_st, {"escape", "limit", 300}, 1100.0, 600)
    _t_timer = referee.battle_end_trigger(_st, {"escape", "limit", 300}, 1301.0, 600)
    _t_limit = referee.battle_end_trigger(_st, {"escape", "limit"}, 1601.0, 600)
    _st_e = dict(_st, escaped=(0x49895963, "eject", 1050.0))
    _t_esc = referee.battle_end_trigger(_st_e, {"escape"}, 1051.0, 0)
    _t_done = referee.battle_end_trigger(dict(_st_e, ended=True), {"escape"}, 1051.0, 0)
    _t_off = referee.battle_end_trigger(_st_e, set(), 1051.0, 0)
    # SE's defeat condition: own wanzer destroyed -> THIS pilot's loss after
    # the delay. Twins: too soon, a death from a PREVIOUS sortie, disabled.
    _pd = referee.pilot_death_trigger(1100.0, 1000.0, 1106.0, 5)
    _pd_ok = (_pd is not None and _pd[1] is False
              and referee.pilot_death_trigger(1100.0, 1000.0, 1103.0, 5) is None
              and referee.pilot_death_trigger(900.0, 1000.0, 1106.0, 5) is None
              and referee.pilot_death_trigger(1100.0, 1000.0, 1106.0, 0) is None
              and referee.pilot_death_trigger(None, 1000.0, 1106.0, 5) is None)
    print(f"  defeat: a destroyed pilot's own battle ends as a LOSS after the "
          f"delay; not before it, not for a death in an earlier sortie, not "
          f"when FMO_BATTLE_DEATH_END=0: {'OK' if _pd_ok else 'FAIL'}")
    ok &= _pd_ok
    _tr_ok = (_t_none is None
              and _t_timer and "timer" in _t_timer[0] and _t_timer[1] == battleend.BATTLE_END_WON
              and _t_limit and "time limit" in _t_limit[0]
              and (_t_limit[1] is False or battleend.BATTLE_END_WON_SET)
              and _t_esc and "ESCAPE" in _t_esc[0]
              and (_t_esc[1] is False or battleend.BATTLE_END_WON_SET)
              and _t_done is None and _t_off is None
              and referee.battle_end_trigger(_st, {"limit"}, 1601.0, 0) is None)
    print(f"  triggers: timer / limit / escape fire once each, an escape or a "
          f"limit is a LOSS unless FMO_BATTLE_END_WON is set, 'limit' with no "
          f"FMO_MISSION_TIME never fires, an ended state never re-fires: "
          f"{'OK' if _tr_ok else 'FAIL'}")
    ok &= _tr_ok
    _po_ok = (battleend.parse_battle_objective("") is None
              and battleend.parse_battle_objective("64, 0, 32, 45") == ((64.0, 0.0, 32.0), 45)
              and _raises(lambda: battleend.parse_battle_objective("1,2,3"))
              and _raises(lambda: battleend.parse_battle_objective("1,2,3,0")))
    print(f"  FMO_BATTLE_OBJECTIVE parses x,y,z,secs and refuses the rest: "
          f"{'OK' if _po_ok else 'FAIL'}")
    ok &= _po_ok
    # the escape is REMEMBERED from a battle-channel record, and only there
    class _BChan:
        key = b"1234battle"
        cmd_seen = {}
    class _LChan:
        key = b"1234lobby"
        cmd_seen = {}
    referee.BATTLE_STATE.pop("selftest-host", None)
    referee._note_battle_record(_LChan(), ("selftest-host", 1), fmoworld.CLI_ESCAPE,
                                struct.pack("<I", 0x24360679) + bytes(60))
    _esc_lobby = referee.BATTLE_STATE.get("selftest-host")
    referee._note_battle_record(_BChan(), ("selftest-host", 1), fmoworld.CLI_ESCAPE,
                                struct.pack("<I", 0x24360679) + bytes(60))
    _esc_battle = referee.BATTLE_STATE.get("selftest-host", {}).get("escaped")
    referee.BATTLE_STATE.pop("selftest-host", None)
    _nb_ok = (_esc_lobby is None and _esc_battle is not None
              and _esc_battle[0] == 0x24360679)
    print(f"  cmd 139 on the BATTLE channel sets the host's escape; on the lobby "
          f"channel it is ignored: {'OK' if _nb_ok else 'FAIL'}")
    ok &= _nb_ok
    _bk_ok = ((not battleend.BATTLE_START or bool(os.environ.get("FMO_BATTLE_START", "").strip()))
              and (battleend.BATTLE_OBJECTIVE is None
                   or bool(os.environ.get("FMO_BATTLE_OBJECTIVE", "").strip()))
              and (not battleend.BATTLE_END or bool(os.environ.get("FMO_BATTLE_END", "").strip())))
    print(f"  knobs: FMO_BATTLE_START / FMO_BATTLE_OBJECTIVE / FMO_BATTLE_END are "
          f"off unless the env sets them: {'OK' if _bk_ok else 'FAIL'}")
    ok &= _bk_ok

    # ------------------------------------------------------------------ #
    # THE CLASS TABLE (static 2026-09-10)
    # ------------------------------------------------------------------ #
    _cc_skip = _fmodata_skip("fmo-class-exp.tsv")
    _cc_ok = (len(classes.CLASS_CURVE) == 100 and classes.CLASS_CURVE[0] == 0
              and classes.CLASS_CURVE[1] == 82800 and classes.CLASS_CURVE[2] == 182160
              and classes.CLASS_CURVE[-1] == 1103015946)
    print(f"  class curve: fmodata/fmo-class-exp.tsv = 100 levels, Lv2 82,800, "
          f"Lv3 182,160, Lv100 1,103,015,946: {_cc_skip or ('OK' if _cc_ok else 'FAIL')}")
    if not _cc_skip:
        ok &= _cc_ok
    _cl_ok = (classes.class_level(0) == 1 and classes.class_level(82799) == 1
              and classes.class_level(82800) == 2 and classes.class_level(182159) == 2
              and classes.class_level(182160) == 3
              and classes.class_level(1103015946) == 100 and classes.class_level(2 ** 31 - 1) == 100
              and classes.class_level(5, ()) == 1)
    print(f"  class_level: floor 1, thresholds inclusive (82,800 -> 2), capped "
          f"at 100, 1 with no curve: {_cc_skip or ('OK' if _cl_ok else 'FAIL')}")
    if not _cc_skip:
        ok &= _cl_ok
    _ct0 = classes.class_table_block({})
    _ct1 = classes.class_table_block({"class_exp": {"3": 90000, 12: 5}})
    _ct_ok = (len(_ct0) == status.S14A_BLOCK2_LEN == 96
              and all(_ct0[i * 8 + classes.CLASS_KIND] == i + 1 for i in range(12))
              and all(_ct0[i * 8 + classes.CLASS_LEVEL] == 1 for i in range(12))
              and struct.unpack_from("<I", _ct1, 2 * 8 + classes.CLASS_EXP)[0] == 90000
              and _ct1[2 * 8 + classes.CLASS_LEVEL] == 2
              and struct.unpack_from("<I", _ct1, 11 * 8 + classes.CLASS_EXP)[0] == 5
              and _ct1[11 * 8 + classes.CLASS_LEVEL] == 1)
    print(f"  class table: 96 B, kinds 1..12 in order, exp from the store "
          f"(str or int keys), Mechanic 90,000 xp -> Lv2: "
          f"{_cc_skip or ('OK' if _ct_ok else 'FAIL')}")
    if not _cc_skip:
        ok &= _ct_ok
    _r14 = status.reply_014a(rank=21, char={"class_exp": {"1": 82800}})
    _r14z = status.reply_014a(rank=21, class_table=False)
    _r14_ok = (_r14[classes.S14A_CLASS_TABLE:classes.S14A_CLASS_TABLE + 96]
               == classes.class_table_block({"class_exp": {"1": 82800}})
               and _r14[classes.S14A_CLASS_TABLE + classes.CLASS_LEVEL] == 2
               and (_r14[classes.S14A_RANK_GROUP] == (ranks.RANK_GROUP & 0xFF) if classes.CLASS_TABLE else True)
               and _r14z[classes.S14A_CLASS_TABLE:classes.S14A_CLASS_TABLE + 96] == bytes(96)
               and _r14z[classes.S14A_RANK_GROUP] == 0) if classes.CLASS_TABLE else True
    print(f"  0x014A: the table rides at +0x6AC with the rank group at +0x29; "
          f"class_table=False leaves both zero: "
          f"{_cc_skip or ('OK' if _r14_ok else 'FAIL')}")
    if not _cc_skip:
        ok &= _r14_ok
    _sd = fmoworld.record_pop(0x2222, unit_type=0, side=1, nation=2)[fmoworld.REC_HDR:]
    _sd_ok = (_sd[fmoworld.POP_SIDE] == 1 and _sd[fmoworld.POP_NATION] == 2
              and fmoworld.record_pop(0x2222, unit_type=0)[fmoworld.REC_HDR + fmoworld.POP_SIDE] == 0
              and (battlepop.BATTLE_DUMMY_SIDE == 0 or bool(os.environ.get("FMO_BATTLE_DUMMY_SIDE", "").strip()))
              and (battlepop.BATTLE_DUMMY_NATION == 0 or bool(os.environ.get("FMO_BATTLE_DUMMY_NATION", "").strip())))
    print(f"  dummy: side rides at body+0x27 and nation at +0x7C only when asked; "
          f"both knobs off unless the env sets them: {'OK' if _sd_ok else 'FAIL'}")
    ok &= _sd_ok
    _ck_ok = (classes.CLASS_TABLE or bool(os.environ.get("FMO_CLASS_TABLE", "").strip()))
    print(f"  knob: FMO_CLASS_TABLE defaults ON (the zeros were the bug on screen): "
          f"{'OK' if _ck_ok else 'FAIL'}")
    ok &= _ck_ok

    # ------------------------------------------------------------------ #
    # RANK FROM CONTRIBUTION, THE HIT RELAY, THE OBJECTIVE (static 2026-09-10)
    # ------------------------------------------------------------------ #
    # The ladder's `rank` is the 0-BASED wire byte (0x6109E8C0 indexes row 0 =
    # Conscript; static 2026-09-12) and contribution promotes only to Captain.
    _rl_ok = (len(ranks.RANK_LADDER) == 48 and ranks.RANK_LADDER[0] == (0, "Conscript", -40)
              and ranks.rank_name(20) == "Captain" and ranks.rank_name(21) == "Major"
              and ranks.rank_for_contribution(0) == 1               # Private
              and ranks.rank_for_contribution(294399) == 19         # First Lieutenant
              and ranks.rank_for_contribution(294400) == 20         # Captain
              and ranks.rank_for_contribution(340000) == 20         # Major needs a review, not contribution
              and ranks.rank_for_contribution(10 ** 9) == ranks.RANK_MAX_EARNABLE == 20
              and ranks.rank_for_contribution(-1000) == 0           # Conscript
              and ranks.rank_for_contribution(5, []) == 0)
    _rk_skip = _fmodata_skip("fmo-ranks.tsv")
    print(f"  rank ladder: 48 rows, byte 0 = Conscript; 0 -> Private (1), 294,400 -> "
          f"Captain (20), 340,000 -> still Captain (SE: contribution stops there), "
          f"capped at {ranks.RANK_MAX_EARNABLE}: {_rk_skip or ('OK' if _rl_ok else 'FAIL')}")
    if not _rk_skip:
        ok &= _rl_ok
    # 0x0176 = the SERVICE RECORD from the pilot's OWN numbers, promotion by
    # the ladder, never a demotion (static 2026-09-11: +0x04/+0x08 are the
    # contribution, +0x0D the rank byte).
    _sb1, _si1 = servicerecord.service_record_block({"rank": 21, "contribution": 0})
    _sb2, _si2 = servicerecord.service_record_block({"rank": 5, "contribution": 294400})
    _sb3, _si3 = servicerecord.service_record_block({"rank": 5, "contribution": 294400}, ladder=())
    _sr_ok = (len(_sb1) == servicerecord.S176_BODY_LEN
              and _sb1[servicerecord.S176_RANK] == 21 and _sb1[servicerecord.S176_OUTLOOK] == servicerecord.OUTLOOK == 0
              and struct.unpack_from("<i", _sb1, servicerecord.S176_CONTRIB_SHOWN)[0] == 0
              and struct.unpack_from("<i", _sb1, servicerecord.S176_CONTRIB_STORED)[0] == 0
              and not _si1["promoted"]                       # seeded above: kept
              and _sb2[servicerecord.S176_RANK] == 20 and _si2["promoted"]  # earned Captain
              and struct.unpack_from("<i", _sb2, servicerecord.S176_CONTRIB_STORED)[0] == 294400
              and _si2["rank_was"] == 5 and _si2["threshold"] == 294400
              and _sb3[servicerecord.S176_RANK] == 5 and not _si3["promoted"]  # no ladder, no walk
              and struct.unpack_from("<I", _sb2, servicerecord.S176_NEXT_PAYDAY)[0]
              == (servicerecord.next_payday_unix() if servicerecord.SALARY else 0))
    print(f"  0x0176 service record: rank 21/contrib 0 -> stays 21; rank 5/"
          f"294,400 -> promoted to 20 Captain; no ladder -> unchanged: "
          f"{_rk_skip or ('OK' if _sr_ok else 'FAIL')}")
    if not _rk_skip:
        ok &= _sr_ok
    # THE NEXT PAYDAY at +0x10 (the officer's "next payday is ___"): the
    # coming UTC midnight while a salary runs, 0 when none does.
    _sal_save = flat_globals()["SALARY"]
    try:
        flat_globals()["SALARY"] = True
        _np_on = struct.unpack_from("<I", servicerecord.service_record_block({}, now=1_800_000_000)[0], servicerecord.S176_NEXT_PAYDAY)[0]
        flat_globals()["SALARY"] = False
        _np_off = struct.unpack_from("<I", servicerecord.service_record_block({}, now=1_800_000_000)[0], servicerecord.S176_NEXT_PAYDAY)[0]
    finally:
        flat_globals()["SALARY"] = _sal_save
    _np_ok = (_np_on == (1_800_000_000 // servicerecord.DAY + 1) * servicerecord.DAY and _np_off == 0
              and servicerecord.S176_NEXT_PAYDAY == 0x10)
    print(f"  0x0176 next payday: +0x{servicerecord.S176_NEXT_PAYDAY:02X} = the coming UTC midnight "
          f"with a salary, 0 without: {'OK' if _np_ok else 'FAIL'}")
    ok &= _np_ok
    # THE PAYBOOK (static 2026-09-12): paydays owed, rows, totals, the fill.
    _T = 1_800_000_000
    _td = _T // servicerecord.DAY
    _d1, _s1, _ = servicerecord.paydays_owed({}, now=_T)
    _d3, _s3, _ = servicerecord.paydays_owed({"last_payday": _td - 3}, now=_T)
    _d9, _s9, _ = servicerecord.paydays_owed({"last_payday": _td - 9}, now=_T)
    _d0, _s0, _ = servicerecord.paydays_owed({"last_payday": _td}, now=_T)
    _svev = servicerecord.SALARY_EVERY
    try:                                   # FMO_SALARY=every: always one row
        flat_globals()["SALARY_EVERY"] = True
        _dev = servicerecord.paydays_owed({"last_payday": _td}, now=_T)[0]
        _dev3 = servicerecord.paydays_owed({"last_payday": _td - 3}, now=_T)[0]
    finally:
        flat_globals()["SALARY_EVERY"] = _svev
    # city_pct=0: the base-pay shape; the economic-city line is pinned below
    _prows, _ = servicerecord.paybook_rows({"last_payday": _td - 2}, 20, now=_T, city_pct=0)
    _pb, _ptm, _ptmp = servicerecord.paybook_fill(bytes(servicerecord.S176_BODY_LEN), _prows)
    _pay20 = ranks.rank_pay(20)
    _pb_ok = (_d1 == 1 and _s1 == [_td * servicerecord.DAY]
              and _d3 == 3 and _s3[0] == (_td - 2) * servicerecord.DAY and _s3[-1] == _td * servicerecord.DAY
              and _d9 == servicerecord.SALARY_MAX_DAYS == 5 and _d0 == 0 and _s0 == []
              and _dev == 1 and _dev3 == 3      # 'every' floors at one, never caps lower
              and _pay20 == (49000, 0) and ranks.rank_pay(21) == (50000, 20)  # MP starts at Major
              and len(_prows) == 4
              and struct.unpack_from("<I", _pb, servicerecord.S176_ROW_COUNT)[0] == 4
              and _pb[servicerecord.S176_ROWS] == servicerecord.PAY_HEADER
              and _pb[servicerecord.S176_ROWS + servicerecord.S176_ROW_LEN] == servicerecord.PAY_BASE
              and struct.unpack_from("<I", _pb, servicerecord.S176_ROWS + 4)[0] == (_td - 1) * servicerecord.DAY
              and struct.unpack_from("<i", _pb, servicerecord.S176_ROWS + servicerecord.S176_ROW_LEN + 8)[0] == _pay20[0]
              and _ptm == 2 * _pay20[0]
              and struct.unpack_from("<i", _pb, servicerecord.S176_TOTAL_MONEY)[0] == _ptm
              # the fill must NOT touch the next-payday time_t at +0x10
              and struct.unpack_from("<I", _pb, servicerecord.S176_NEXT_PAYDAY)[0] == 0
              and servicerecord.next_payday_unix(_T) == (_td + 1) * servicerecord.DAY
              and servicerecord.next_payday_unix((_td + 1) * servicerecord.DAY) == (_td + 2) * servicerecord.DAY
              and struct.unpack_from("<i", _pb, servicerecord.S176_TOTAL_MP)[0] == 2 * _pay20[1]
              and struct.unpack_from("<I", servicerecord.paybook_fill(bytes(servicerecord.S176_BODY_LEN),
                                                                      [(1, 0, 0, 1, 1)] * 30)[0],
                                     servicerecord.S176_ROW_COUNT)[0] == 20
              and servicerecord.S176_ROWS + servicerecord.S176_ROW_MAX * servicerecord.S176_ROW_LEN <= servicerecord.S176_BODY_LEN)
    # ECONOMIC CITIES SCALE THE SALARY (topics0906mission: a Second Lieutenant,
    # H$ 47,000, 112% with one enemy rear city taken, 122% with two; AI/F00/D08
    # 128: the enemy taking ours cuts it). A kind-7 11:12 line per payday.
    _cp = servicerecord.city_pay_pct
    _crows, _ = servicerecord.paybook_rows({"last_payday": _td - 1}, 18, now=_T, city_pct=12)
    _city_ok = (_cp(8, 0) == 12 and _cp(16, 0) == 22 and _cp(0, 8) == -12
                and _cp(0, 0) == 0 and _cp(10, 10) == 0 and _cp(999, 0) == _cp(52, 0)
                and ranks.rank_pay(18)[0] == 47000
                and len(_crows) == 3
                and _crows[2] == (servicerecord.PAY_CITY, 12, _td * servicerecord.DAY, 5640, 0))
    _sv_city = (warstate.war_state, warstate.WAR, zoneentry.NATION_PER_CHARACTER,
                servicerecord.CITY_PAY)
    try:
        warstate.WAR = "1"
        zoneentry.NATION_PER_CHARACTER = True
        warstate.war_state = lambda: type("W", (), {"score": lambda self: {1: 8, 2: 0}})()
        _cw = servicerecord.paybook_rows({"last_payday": _td - 1, "nation_byte": 1}, 18, now=_T)[0]
        _cl = servicerecord.paybook_rows({"last_payday": _td - 1, "nation_byte": 2}, 18, now=_T)[0]
        servicerecord.CITY_PAY = False
        _coff = servicerecord.paybook_rows({"last_payday": _td - 1, "nation_byte": 1}, 18, now=_T)[0]
        _city_ok = (_city_ok and len(_cw) == 3 and len(_cl) == 3
                    and _cw[2][:2] == (servicerecord.PAY_CITY, 12) and _cw[2][3] == 5640
                    and _cl[2][:2] == (servicerecord.PAY_CITY, -12) and _cl[2][3] == -5640
                    and len(_coff) == 2)
    finally:
        (warstate.war_state, warstate.WAR, zoneentry.NATION_PER_CHARACTER,
         servicerecord.CITY_PAY) = _sv_city
    print(f"  paybook: economic cities scale the salary -- 2nd Lt H$ 47,000 "
          f"+12% (H$ 5,640, a kind-7 'City control adjustment (+12%)' line) "
          f"with one enemy rear city, +22% with two, -12% when the enemy holds "
          f"it: {'OK' if _city_ok else 'FAIL'}")
    ok &= _city_ok
    # THE ITEM MINT + the starter transit pass (static 2026-09-12).
    _mrec = inventory.item_record(0x1122334455667788, 25, permits.PASS_KIND)
    _mp = shop.item_mint_payload([_mrec])
    _mint_ok = (len(_mp) == shop.MINT_RECORDS + inventory.INV_ENTRY_LEN
                and struct.unpack_from("<I", _mp, shop.MINT_ADDS)[0] == 1
                and struct.unpack_from("<I", _mp, shop.MINT_TOTAL)[0] == 1
                and _mp[shop.MINT_RECORDS:shop.MINT_RECORDS + inventory.INV_ENTRY_LEN] == _mrec
                # the arm reads the id at packet+0x34 = payload+0x20 = record+8
                and struct.unpack_from("<H", _mp, shop.MINT_RECORDS + inventory.ITEM_ID)[0] == 25
                and _mp[shop.MINT_RECORDS + inventory.ITEM_KIND] == permits.PASS_KIND == 0x13
                and shop.MINT_RECORDS + inventory.ITEM_ID == 0x20
                and permits.PASS_HQ == {1: 25, 2: 26})
    print(f"  item mint: 0x{shop.MSG_ACQUIRE_REPLY:04X} push = adds at +0x{shop.MINT_ADDS:02X}, "
          f"total at +0x{shop.MINT_TOTAL:02X}, 24-B records from +0x{shop.MINT_RECORDS:02X} "
          f"(the id lands at payload+0x20); HQ passes are kind 0x13 id 25/26: "
          f"{'OK' if _mint_ok else 'FAIL'}")
    ok &= _mint_ok
    # THE AREA PERMIT BYTE: holding a pass sets one byte of the owned block,
    # which is what Change Area's predicate requires (> 0).
    _pc_pass = {"items": [{"serial": 1, "id": 26, "kind": permits.PASS_KIND}]}
    _b_pass = status.reply_014a(char=_pc_pass)
    _b_none = status.reply_014a(char={"items": []})
    _off = status.S14A_OWNED + permits.AREA_PERMIT_OFF + (permits.PASS_ZONE_KIND[26] - 1)
    _perm_ok = (_b_pass[_off] == 1 and _b_none[_off] == 0
                and _off == 0x44 + 0x280 + 2        # lobby+0x8C8+0x280+2
                and permits.PASS_ZONE_KIND == {25: 1, 26: 3, 27: 2, 28: 4, 29: 5}
                # a pilot with no pass gets the byte-identical old block
                and _b_none == status.reply_014a(char={})
                # WARNING: and it must survive a pilot with a STORED flag block, whose
                # "stored block wins whole" arm returns EARLY (live 2026-09-12:
                # the first cut sat below that return and served zeros)
                and status.reply_014a(char={"items": _pc_pass["items"],
                                            "flags": "00" * 256})[_off] == 1)
    print(f"  area permit: holding 'Pass: HQ-U.S.N.' sets owned+0x{permits.AREA_PERMIT_OFF:X}+2 "
          f"(payload+0x{_off:X}) to 1 and nothing else moves; no pass = the old "
          f"all-zero block: {'OK' if _perm_ok else 'FAIL'}")
    ok &= _perm_ok
    # THE OPENED-AREA BITMAP: a zone the pilot changed into sets its ROW bit at
    # owned+0x00 against the served table ('live' = 505, 509, 513 -> 509 is
    # row 1 -> byte 0 = 0x02); zones outside the table are ignored, no table
    # or no list is the old block, and a stored flag block cannot hide it.
    _zc_save = flat_globals()["ZONE_CONTROL"]
    try:
        flat_globals()["ZONE_CONTROL"] = ["live"]
        _ao_b = status.reply_014a(char={"areas_open": [509, 700]})
        _ao_f = status.reply_014a(char={"areas_open": [513], "flags": "00" * 256})
        _ao_n = status.reply_014a(char={})
        flat_globals()["ZONE_CONTROL"] = []
        _ao_off = status.reply_014a(char={"areas_open": [509]})
    finally:
        flat_globals()["ZONE_CONTROL"] = _zc_save
    _o = status.S14A_OWNED + permits.AREA_OPEN_OFF
    _open_ok = (_ao_b[_o] == 0x02 and _ao_f[_o] == 0x04
                and _ao_b[_o + 1:_o + permits.AREA_OPEN_LEN] == bytes(permits.AREA_OPEN_LEN - 1)
                and _ao_n[_o:_o + permits.AREA_OPEN_LEN] == bytes(permits.AREA_OPEN_LEN)
                and _ao_off == status.reply_014a(char={})
                and _o == 0x44                      # lobby+0x8C8, kind 1
                and permits.area_open_bitmap([100 + i for i in range(9)],
                                             [(100 + i, 1, 1) for i in range(9)])
                == b"\xff\x01" + bytes(permits.AREA_OPEN_LEN - 2))
    print(f"  opened areas: zone 509 = row 1 of the 'live' table sets bit 1 of "
          f"owned+0x00 (payload+0x{_o:X}); an unserved zone, no table or no list "
          f"leaves the old block: {'OK' if _open_ok else 'FAIL'}")
    ok &= _open_ok
    # WARNING: THE LOGIN BLOCK CARRIES THE NATION IT LOGS (live 2026-09-12): with
    # the knob at 1, a pilot resolved to nation 2 must get 2 at payload+0x30
    # from status_body(), while a bare reply_014a still takes the knob.
    _sn_save = flat_globals()["STATUS_NATION"]
    try:
        flat_globals()["STATUS_NATION"] = 1
        _nf = status.status_fields(char={}, nation=2, nation_src="selftest")
        _nat_ok = (status.status_body(_nf)[status.S14A_NATION] == 2
                   and status.reply_014a(char={})[status.S14A_NATION] == 1
                   and status.status_body(status.status_fields(char={})) == status.reply_014a(char={})
                   and status.S14A_NATION == 0x30)
    finally:
        flat_globals()["STATUS_NATION"] = _sn_save
    print(f"  login nation: the Start Game body is built from the logged field "
          f"list, so a nation-2 pilot gets 2 at payload+0x{status.S14A_NATION:X} even with "
          f"FMO_STATUS_NATION=1: {'OK' if _nat_ok else 'FAIL'}")
    ok &= _nat_ok
    # THE PERMIT RULE the client applies (0x613966E4) and the live area push:
    # a tier-1 U.S.N. pilot pays a permit for 407 (row byte 1), a tier-3 one
    # does not, an O.C.U.-only row is grey for nation 1's missing byte, the
    # OC-U.S.N. pass opens kind 4, and the push carries the serial at +0x310.
    _rows_t = [(407, 1, 1), (400, 0, 1), (300, 0, 1)]
    _pc_t = {"items": [{"serial": 0x1122334455667788, "id": 28, "kind": permits.PASS_KIND}]}
    _pb_t = resultpush.result_push_body(owned=bytes(resultpush.S15A_OWNED_LEN),
                                        spent=[struct.pack("<Q", 0x1122334455667788)])
    _cost_ok = (permits.area_access(1, 1) == 1 and permits.area_access(3, 1) == 2
                and permits.area_access(1, 2) == 0 and permits.area_access(11, 1) == 0
                and permits.area_access(4, 3) == 1
                and permits.area_permit_cost(407, 2, 1, _rows_t)[0] == 1
                and permits.area_permit_cost(400, 2, 3, _rows_t)[0] == 2
                and permits.area_permit_cost(300, 1, 1, _rows_t)[0] == 0
                and permits.area_permit_cost(509, 2, 1, _rows_t)[0] == 0
                and permits.area_pass_item(_pc_t, 407)["id"] == 28
                and permits.area_pass_item(_pc_t, 300) is None
                and _pb_t[resultpush.S15A_SPEND:resultpush.S15A_SPEND + 8] == bytes.fromhex("8877665544332211")
                and struct.unpack_from("<I", _pb_t, resultpush.S15A_N_SPEND)[0] == 1)
    print(f"  area permit cost: the client matrix (tier x row byte), the pass "
          f"that opens a zone kind, and the spent serial at +0x{resultpush.S15A_SPEND:X}: "
          f"{'OK' if _cost_ok else 'FAIL'}")
    ok &= _cost_ok
    # granted ONCE: a pilot already holding the pass gets no second one
    _gc = {"items": [{"serial": 1, "id": 25, "kind": permits.PASS_KIND}]}
    _gc2 = {"items": []}
    _have = any(int(i["kind"]) == permits.PASS_KIND and int(i["id"]) == 25
                for i in inventory.stored_items(_gc))
    _none = any(int(i["kind"]) == permits.PASS_KIND and int(i["id"]) == 25
                for i in inventory.stored_items(_gc2))
    _once_ok = _have and not _none
    print(f"  item mint: a pilot already holding the pass is detected, an empty "
          f"one is not: {'OK' if _once_ok else 'FAIL'}")
    ok &= _once_ok

    # the pay figures are the rank file's `pay` / `mp` columns (row+0x58 /
    # row+0x64 of D15.DAT), so this needs fmo-ranks.tsv too
    print(f"  paybook: 1 day owed by default, 3 owed -> 3 rows of days, 9 -> capped "
          f"at 5, paid today -> 0; Captain pays {_pay20[0]} H$ + {_pay20[1]} MP; "
          f"fill writes count/rows/totals, caps at 20: "
          f"{_rk_skip or ('OK' if _pb_ok else 'FAIL')}")
    if not _rk_skip:
        ok &= _pb_ok
    # NAME UNIQUENESS + THE TRAINING GATE, the pure halves.
    _ro = [("a", [{"id": 1, "first": "Lex", "last": "Arden"}]),
           ("b", [{"id": 7, "first": "Remy", "last": "Test"}])]
    _nu_ok = (charstore.name_clash(_ro, "lex", "ARDEN", "a", 2) is not None
              and charstore.name_clash(_ro, "Lex", "Arden", "a", 1) is None     # naming your own slot
              and charstore.name_clash(_ro, "Remy", "Test", "a", 1) is not None  # another account
              and charstore.name_clash(_ro, "New", "Pilot", "a", 2) is None
              and charstore.name_clash(_ro, "", "", "a", 2) is None
              and charstore.NAME_TAKEN_CODE == 0xC43B
              and classes.trained(bytes(128) + b"\x63") and not classes.trained(bytes(256))
              and not classes.trained(b"") and not classes.trained(None))
    print(f"  name uniqueness (case-insensitive, across accounts, own slot exempt, "
          f"code 0xC43B) + trained() = byte 128 == 99: {'OK' if _nu_ok else 'FAIL'}")
    ok &= _nu_ok
    # ONE settlement per sortie: with both contribution knobs set, 0x015A
    # then 0x014C credit the store ONCE and carry the same delta.
    _g = flat_globals()
    _saved = {k: _g[k] for k in ("RESULT_PUSH", "RESULT_MONEY", "RESULT_CONTRIB",
                                 "BATTLE_END_CONTRIB", "BATTLE_END_EXP")}
    _credits = []
    try:
        _g.update(RESULT_PUSH=True, RESULT_MONEY=500, RESULT_CONTRIB=40,
                  BATTLE_END_CONTRIB=40, BATTLE_END_EXP="")
        _s = session.Session.__new__(session.Session)
        _s.peer = "selftest-settle"
        _s.battle_settlement = None
        _s.last_0159 = b""
        _s.playing_char = lambda: {"money": 100, "contribution": 10}
        _s.stored_money = lambda: (100, 10)
        _s.credit_money = (lambda why, money=0, contribution=0:
                           (_credits.append((money, contribution)),
                            (100 + money, 10 + contribution))[1])
        _s.credit_class_exp = lambda why, rows: {}
        _rp = _s.battle_result_push(0x1234, "the selftest")
        _be = _s.battle_end_push(0x1234, why="selftest", won=True)
        _st = _s.battle_settlement
        _one_ok =(len(_credits) == 1 and _credits[0] == (500, 40)
                   and _st is not None and _st["banked"]
                   and _st["contrib_old"] == 10 and _st["contrib_new"] == 50
                   and _rp is not None and _be is not None)
        # the 0x015A carries the settled deltas (payload = frame + 0x14)
        _pl = _rp[0x14:] if _rp else b""
        _one_ok &= (len(_pl) >= resultpush.S15A_CONTRIB + 4
                    and struct.unpack_from("<i", _pl, resultpush.S15A_MONEY)[0] == 500
                    and struct.unpack_from("<i", _pl, resultpush.S15A_CONTRIB)[0] == 40)
    finally:
        _g.update(_saved)
    print(f"  settlement: 0x015A then 0x014C with both contribution knobs set "
          f"-> ONE credit (+500 H$, +40 contribution), 10 -> 50 on both "
          f"packets: {'OK' if _one_ok else 'FAIL'} ({len(_credits)} credit(s))")
    ok &= _one_ok
    # PERFORMANCE PAY: kills and the verdict move the settlement.
    _st_k = {"enemies": {0x2222, 0x3333},
             "kills": [(1, 0.0), (0x2222, 1.0), (0x2222, 2.0), (0x3333, 3.0)]}
    _pp_ok = battleend.battle_kills(_st_k) == [0x2222, 0x3333]      # own unit + repeat dropped
    _pp_ok &= battleend.battle_kills(None) == [] and battleend.battle_kills({"kills": [(5, 0)]}) == []
    _knobs = dict(kill_contrib=100, kill_bonus_hs=250, win_money=1000,
                  win_contrib=300, kill_exp=[(1, 5)])
    _pw = battleend.battle_pay(2, True, money=500, contrib=40, exp_rows=[(1, 10), (2, 3)],
                               **_knobs)
    _pl2 = battleend.battle_pay(2, False, money=500, contrib=40, exp_rows=[(1, 10)], **_knobs)
    _p0 = battleend.battle_pay(0, False, money=500, contrib=40, **_knobs)
    _pflat = battleend.battle_pay(3, True, money=500, contrib=40, kill_contrib=0,
                                  kill_bonus_hs=0, win_money=0, win_contrib=0, kill_exp=())
    # 2026-09-30: the Pilot row (12) is the job exp's sum (FMO_PILOT_EXP_PCT
    # 100), and keeps its slot when five jobs pay; paced exp is one Pilot-level
    # step / pace for a win, half for a loss, split over the jobs
    _p5 = battleend.battle_pay(0, True, exp_rows=[(k, 10) for k in range(1, 7)],
                               pilot_pct=100, **{k: v for k, v in _knobs.items() if k != "kill_exp"},
                               kill_exp=())
    _pp_ok &= (len(_p5["exp_rows"]) == 5 and dict(_p5["exp_rows"]).get(12) == 60
               and battleend.battle_pay(0, True, exp_rows=[(1, 10)], pilot_pct=0,
                                        kill_exp=())["exp_rows"] == [(1, 10)])
    _curve = (0, 100, 300, 700)
    # Lv2 -> Lv3 step = 300 - 100 = 200; / pace 4 = 50 a win: main 50%, the
    # supports share the rest; the set jobs come from the stored 0x0167 tail
    _blk = bytearray(inventory.REPLY_0166_LEN)
    _blk[inventory.SETUP_TAIL_OFF:inventory.SETUP_TAIL_OFF + 5] = bytes([5, 1, 9, 5, 0])
    _pp_ok &= (inventory.set_jobs({"setups": _blk.hex()}) == [5, 1]
               and inventory.set_jobs({}) == [] and inventory.set_jobs({"setups": "00"}) == [])
    _pp_ok &= (battleend.paced_exp_rows(2, True, _curve, [5, 1, 3], pace=4, main_pct=50)
               == [(5, 25), (1, 12), (3, 12)])
    _pp_ok &= (battleend.paced_exp_rows(2, True, _curve, [1, 5], pace=4, main_pct=50) == [(1, 25), (5, 25)]
               and battleend.paced_exp_rows(2, False, _curve, [3], pace=4, main_pct=50) == [(3, 25)]
               and battleend.paced_exp_rows(4, True, _curve, [3], pace=4) == []
               and battleend.paced_exp_rows(2, True, _curve, [3], pace=0) == [])
    # sector exp: 100% at NPC level 0, linear to FMO_EXP_SECTOR_PCT at level 25
    # (rank 5), capped there; the Frontline (zone kind 5) multiplies it; 100/100 = off
    _sx = battleend.sector_exp_pct
    _pp_ok &= (_sx(0, 207, 200, 125) == 100 and _sx(5, 207, 200, 125) == 120
               and _sx(25, 207, 200, 125) == 200 and _sx(60, 207, 200, 125) == 200
               and _sx(15, 509, 200, 125) == 200 and _sx(25, 513, 200, 125) == 250
               and _sx(15, None, 200, 125) == 160 and _sx(25, 509, 100, 100) == 100
               and _sx(None, 600, 200, 125) == 100)
    _pp_ok &= (_pw["money"] == 1500 and _pw["contribution"] == 540
               and dict(_pw["exp_rows"]) == {1: 20, 2: 3, 12: 23}
               and _pw["kill_bonus_hs"] == 500
               and _pl2["money"] == 500 and _pl2["contribution"] == 240
               and _p0["money"] == 500 and _p0["contribution"] == 40
               and _p0["kill_bonus_hs"] == 0 and _p0["exp_rows"] == []
               and _pflat["money"] == 500 and _pflat["contribution"] == 40)
    # ...and through the session: the end trigger's verdict + the kill ledger
    # reach the ONE credit, and a withdraw settles as a loss.
    _saved = {k: _g[k] for k in ("RESULT_PUSH", "RESULT_MONEY", "RESULT_CONTRIB",
                                 "BATTLE_END_CONTRIB", "BATTLE_END_EXP",
                                 "KILL_CONTRIB", "KILL_BONUS_HS", "WIN_MONEY",
                                 "WIN_CONTRIB", "KILL_EXP")}
    _credits, _owed = [], []
    try:
        _g.update(RESULT_PUSH=True, RESULT_MONEY=500, RESULT_CONTRIB=40,
                  BATTLE_END_CONTRIB=0, BATTLE_END_EXP="", KILL_CONTRIB=100,
                  KILL_BONUS_HS=250, WIN_MONEY=1000, WIN_CONTRIB=300, KILL_EXP="")
        for _won_in in (True, False):
            referee.BATTLE_STATE["selftest-perf"] = {"enemies": {0x2222},
                                                     "kills": [(0x2222, 1.0)]}
            _s = session.Session.__new__(session.Session)
            _s.peer, _s.ip = "selftest-perf", "selftest-perf"
            _s.battle_settlement = None
            _s.last_0159 = b""
            _s.playing_char = lambda: {"money": 100, "contribution": 10}
            _s.stored_money = lambda: (100, 10)
            _s.credit_money = (lambda why, money=0, contribution=0:
                               (_credits.append((money, contribution)),
                                (100 + money, 10 + contribution))[1])
            _s.credit_class_exp = lambda why, rows: {}
            _s.owe_kill_bonus = lambda hs, n: _owed.append((hs, n))
            _s.battle_result_push(0x1234, "the selftest", won=_won_in)
            _s.battle_end_push(0x1234, why="selftest", won=_won_in)
        _pp_ok &= (_credits == [(1500, 440), (500, 140)]
                   and _owed == [(250, 1), (250, 1)])
    finally:
        _g.update(_saved)
        referee.BATTLE_STATE.pop("selftest-perf", None)
    # PACED EXP through the session (2026-09-30): a pilot at Pilot Lv2 with
    # Sniper main + Assault support wins; the jobs split one step / pace and
    # the Pilot row carries the total. credit_class_exp is stubbed (no store).
    _saved2 = (battleend.EXP_PACE, battleend.EXP_MAIN_PCT, battleend.PILOT_EXP_PCT,
               battleend.BATTLE_END_EXP, battleend.KILL_EXP, charstore.CHAR_STORE,
               battleend.EXP_SECTOR_PCT, battleend.EXP_FRONT_PCT, squad.ENEMY_LEVEL)
    _px = []
    _pxf = []
    try:
        battleend.EXP_PACE, battleend.EXP_MAIN_PCT, battleend.PILOT_EXP_PCT = 4, 50, 100
        battleend.EXP_SECTOR_PCT, battleend.EXP_FRONT_PCT = 100, 100
        battleend.BATTLE_END_EXP, battleend.KILL_EXP = "", ""
        charstore.CHAR_STORE = charstore.CHAR_STORE or "selftest-not-written"
        _blk2 = bytearray(inventory.REPLY_0166_LEN)
        _blk2[inventory.SETUP_TAIL_OFF:inventory.SETUP_TAIL_OFF + 2] = bytes([5, 1])
        _pc2 = {"setups": _blk2.hex(), "money": 0, "contribution": 0}
        progress.set_pilot_level(_pc2, 2)
        referee.BATTLE_STATE["selftest-pace"] = {"enemies": set(), "kills": []}
        _s = session.Session.__new__(session.Session)
        _s.peer, _s.ip = "selftest-pace", "selftest-pace"
        _s.battle_settlement = None
        _s.playing_char = lambda: _pc2
        _s.stored_money = lambda: (0, 0)
        _s.credit_money = lambda why, money=0, contribution=0: (money, contribution)
        _s.credit_class_exp = lambda why, rows: _px.extend(rows) or {}
        _s.owe_kill_bonus = lambda hs, n: None
        _s.settle_battle("selftest pace", won=True)
        _step = classes.CLASS_CURVE[2] - classes.CLASS_CURVE[1] if classes.CLASS_CURVE else 0
        _tot = _step // 4
        _pace_ok = (not classes.CLASS_CURVE) or (
            dict(_px) == {5: _tot * 50 // 100, 1: _tot - _tot * 50 // 100,
                          12: _tot * 50 // 100 + (_tot - _tot * 50 // 100)})
        # the same win on the Frontline (zone 509) at NPC level 15 with the
        # sector scale on: 100 + 100*15/25 = 160%, x125% = 200% = twice the jobs
        battleend.EXP_SECTOR_PCT, battleend.EXP_FRONT_PCT = 200, 125
        squad.ENEMY_LEVEL = 15
        _s.battle_settlement = None
        _s.sector_zone = 509
        _s.credit_class_exp = lambda why, rows: _pxf.extend(rows) or {}
        _s.settle_battle("selftest pace frontline", won=True)
        _pace_ok &= (not classes.CLASS_CURVE) or (
            dict(_pxf) == {5: 2 * (_tot * 50 // 100), 1: 2 * (_tot - _tot * 50 // 100),
                           12: 2 * (_tot * 50 // 100) + 2 * (_tot - _tot * 50 // 100)})
    finally:
        (battleend.EXP_PACE, battleend.EXP_MAIN_PCT, battleend.PILOT_EXP_PCT,
         battleend.BATTLE_END_EXP, battleend.KILL_EXP, charstore.CHAR_STORE,
         battleend.EXP_SECTOR_PCT, battleend.EXP_FRONT_PCT, squad.ENEMY_LEVEL) = _saved2
        referee.BATTLE_STATE.pop("selftest-pace", None)
    print(f"  paced exp: a Lv2 pilot's win pays the set jobs (Sniper main 50%, Assault "
          f"the rest) one step / FMO_EXP_PACE and the Pilot row the total; on the "
          f"Frontline at NPC Lv15 the sector scale doubles it: "
          f"{'OK' if _pace_ok else 'FAIL'} ({_px}; frontline {_pxf})")
    _pp_ok &= _pace_ok
    print(f"  performance pay: kills count only popped enemies, once each; a "
          f"win adds FMO_WIN_*, each kill FMO_KILL_CONTRIB / FMO_KILL_EXP and a "
          f"Kill bonus line; a loss keeps the kill pay, the knobs at 0 are the "
          f"flat pay: {'OK' if _pp_ok else 'FAIL'} (credits {_credits})")
    ok &= _pp_ok
    # TWO PILOTS IN ONE BATTLE: a battle room-mate pops as THEIR wanzer.
    import types as _types2
    _now = time.time()
    _pa = dict(unit_type=0, name1="A", name2="B", pos=(1.0, 2.0, 3.0),
               model_flags=None, model_sub=None, type4_model=1, client_kind=3,
               nation=1, parts=[(0, 0x10, 5)], side=0)
    _v = _types2.SimpleNamespace(key=b"%xbattle", pop_args=dict(_pa),
                                 pos=(1.0, 2.0, 3.0))
    _o = _types2.SimpleNamespace(key=b"%xbattle", pos=(9.0, 2.0, 9.0),
                                 pop_args=dict(_pa, nation=2, side=1),
                                 last_fire=_now - 2)
    _f = _types2.SimpleNamespace(key=b"%xbattle", pos=(5.0, 2.0, 5.0),
                                 pop_args=dict(_pa), last_fire=_now - 1)
    _lob = _types2.SimpleNamespace(key=b"%xlobby", pos=(0, 0, 0),
                                   pop_args=dict(_pa, unit_type=4))
    _ra = rooms.battle_room_pop_args(_v, _o, "Rem", "Ote")
    _br_ok = (_ra is not None and _ra["client_kind"] == 0 and _ra["nation"] == 2
              and _ra["parts"] == [(0, 0x10, 5)] and _ra["pos"][:3] == (9.0, 2.0, 9.0)
              and _ra["name1"] == "Rem" and "model_flags" not in _ra
              and rooms.battle_room_pop_args(_lob, _o, "x", "y") is None
              and rooms.battle_room_pop_args(_v, _types2.SimpleNamespace(
                  key=b"%xbattle", pos=(0, 0, 0), pop_args=dict(_pa, unit_type=4)),
                  "x", "y") is None
              and rooms.battle_room_hostile(_v, _o) and not rooms.battle_room_hostile(_v, _f)
              and rooms.battle_room_killer(_v, _now, mates=[_o, _f]) is _o
              and rooms.battle_room_killer(_v, _now + 60, mates=[_o, _f]) is None
              and rooms.battle_room_killer(_v, _now, mates=[_f]) is None)
    # FMO_BATTLE_DUMMY_AI: the POP that makes the client run the enemy
    _ai = fmoworld.record_pop(0x2222, unit_type=0, client_kind=1, nation=2,
                              extra={battlepop.POP_AI_OWNER: struct.pack("<I", 1),
                                     battlepop.POP_AI_BRAIN: struct.pack("<I", 101)})
    _aib = _ai[fmoworld.REC_HDR:]
    _ai_ok = (struct.unpack_from("<I", _aib, 0)[0] == 1
              and struct.unpack_from("<I", _aib, battlepop.POP_AI_OWNER)[0] == 1
              and struct.unpack_from("<I", _aib, battlepop.POP_AI_BRAIN)[0] == 101
              and battlepop.POP_AI_BRAIN >= fmoworld.POP_PARTS + fmoworld.POP_PART_COUNT
              * fmoworld.POP_PART_STRIDE
              and battlepop.POP_AI_OWNER >= fmoworld.POP_POS + 16)
    print(f"  enemy AI pop: client_kind 1, owner at +0x{battlepop.POP_AI_OWNER:X}, brain "
          f"at +0x{battlepop.POP_AI_BRAIN:X}, clear of the position and part array: "
          f"{'OK' if _ai_ok else 'FAIL'}")
    ok &= _ai_ok
    # THE ENEMY SQUAD: parsing, the ring, the cmd-24 filter, one owner per
    # room, a kill credited once to the last shooter, destroy:all, fire relay.
    _sqf = []

    def _sc(n, v):
        if not v:
            _sqf.append(n)
        return bool(v)

    # a second sortie on the same channel pops the squad again: restart()
    # must clear squad_popped, or sorties 2+ have no enemies
    _wc = worldchannel.WorldChannel(("sqR", 1))
    _wc.squad_popped = True
    _wc.restart()
    _sq_ok = _sc(12, _wc.squad_popped is False)
    # the owner stamp is the id the self-POP used: with wire ids that is the
    # character id (0x1001), not FMO_UDP_POP_BATTLE's 1
    if charlist.CHAR_WIRE_BASE:
        _wc.char_id = charlist.CHAR_WIRE_BASE + 1
        _sq_ok &= _sc(13, squad.squad_owner_uid(_wc, True, None) == charlist.CHAR_WIRE_BASE + 1
                      and squad.squad_owner_uid(_wc, False, None) == 0)

    _sq_ok &= _sc(1, squad.parse_battle_enemies("3:40") == (3, 40.0)
                 and squad.parse_battle_enemies("") == (1, 30.0)
                 and squad.parse_battle_enemies("99") == (8, 30.0))
    _ring = squad.squad_positions((10.0, 5.0, 20.0, 0.0), 4, 10.0, 40.0)
    _sq_ok &= _sc(2, len(_ring) == 4 and all(len(p) == 4 and p[1] == 5.0
                                             and abs(p[0] - 20.0) < 1e-6 for p in _ring)
                  and [round(p[2]) for p in _ring] == [-40, 0, 40, 80]
                  and squad.squad_gap("3:80:25") == 25.0 and squad.squad_gap("3:80") == 40.0)
    # state: flags 0x03 = pos (6) + facing (2) -> 16 bytes
    _stt = bytes([0, 0x03]) + bytes(14)
    _b24 = (struct.pack("<H", 3) + struct.pack("<I", 1) + _stt
            + struct.pack("<I", 0x2222) + _stt + struct.pack("<I", 0x2223) + _stt)
    _f = squad.squad_batch_filter(_b24, {0x2222, 0x2223})
    _sq_ok &= _sc(3, squad.move_state_len(_stt) == 16 and _f is not None
                  and struct.unpack_from("<H", _f, 0)[0] == 2
                  and struct.unpack_from("<I", _f, 2)[0] == 0x2222
                  and len(_f) == 2 + 2 * 20
                  and squad.squad_batch_filter(_b24, {0x9999}) is None
                  and squad.squad_batch_filter(_b24[:-3], {0x2222}) is None)
    import types as _types3
    _gs = flat_globals()
    _sv = {k: _gs[k] for k in ("BATTLE_DUMMY", "BATTLE_ENEMIES")}
    try:
        _gs.update(BATTLE_DUMMY=(0x2222, 0, None), BATTLE_ENEMIES=(2, 30.0))
        squad.BATTLE_SQUADS.clear()
        for _h in ("sqA", "sqB"):
            referee.BATTLE_STATE.pop(_h, None)
            referee.battle_state(_h, reset=True)
        rooms.WORLD_MAPS["sqA"] = rooms.WORLD_MAPS["sqB"] = 418
        _cA = _types3.SimpleNamespace(addr=("sqA", 1), key=b"%xbattle",
                                      last_fire=None)
        _cB = _types3.SimpleNamespace(addr=("sqB", 1), key=b"%xbattle",
                                      last_fire=None)
        _s1, _o1 = squad.battle_squad_for(_cA, (0, 0, 0, 0), 2, [], mates=[])
        _s2, _o2 = squad.battle_squad_for(_cB, (0, 0, 0, 0), 2, [], mates=[_cA])
        _sq_ok &= _sc(4, _s1 is _s2 and _s1["owner"] == "sqA" and _o2 is _cA
                      and _s1["ids"] == [0x2222, 0x2223])
        # the owner gone from the room -> the next pilot starts its own squad
        _s3, _o3 = squad.battle_squad_for(_cB, (0, 0, 0, 0), 2, [], mates=[])
        _sq_ok &= _sc(5, _s3 is not _s1 and _s3["owner"] == "sqB" and _o3 is None)
        # a new sortie by the owner -> a fresh squad
        referee.battle_state("sqB", reset=True)["granted_at"] += 5
        _s4, _ = squad.battle_squad_for(_cB, (0, 0, 0, 0), 2, [], mates=[])
        _sq_ok &= _sc(6, _s4 is not _s3)
        # the kill: once, to whoever fired last (B), not the reporting owner
        _now = time.time()
        _cA.last_fire, _cB.last_fire = _now - 5, _now - 1
        _k1 = squad.squad_credit_kill(_s1, 0x2222, _cA, now=_now, chans=[_cB])
        _k2 = squad.squad_credit_kill(_s1, 0x2222, _cA, now=_now, chans=[_cB])
        _cA.last_fire = _cB.last_fire = None
        _k3 = squad.squad_credit_kill(_s1, 0x2223, _cA, now=_now, chans=[_cB])
        _sq_ok &= _sc(7, _k1 == "sqB" and _k2 is None and _k3 == "sqA"
                      and any(t == 0x2222 for t, _w in referee.BATTLE_STATE["sqB"]["kills"])
                      and _s1["dead"] == {0x2222, 0x2223})
        # the hit list names the shooter: a pilot's hit is that pilot's kill,
        # an enemy's hit is friendly fire and pays nobody
        _s5 = {"ids": [0x3001, 0x3002], "dead": set(), "last_hit": {}}
        referee.BATTLE_STATE["sqS"] = {"squad": _s5}
        _cS = _types3.SimpleNamespace(addr=("sqS", 1), key=b"%xbattle",
                                      self_unit=lambda: 1, alias_of={},
                                      last_fire=None)
        _hl1 = bytes([1, 1, 0, 0]) + struct.pack("<IHBB", 0x3001, 99, 0, 1)
        _hl2 = bytes([1, 1, 0, 0]) + struct.pack("<IHBB", 0x3002, 99, 0, 1)
        _n1 = squad.squad_note_hits(_cS, _hl1, 1)          # the pilot hit 0x3001
        _n2 = squad.squad_note_hits(_cS, _hl2, 0x3001)     # an enemy hit 0x3002
        _kS1 = squad.squad_credit_kill(_s5, 0x3001, _cS, now=_now, chans=[])
        _kS2 = squad.squad_credit_kill(_s5, 0x3002, _cS, now=_now, chans=[])
        _sq_ok &= _sc(11, _n1 == [(0x3001, ("host", "sqS"))]
                      and _n2 == [(0x3002, ("npc", 0x3001))]
                      and _kS1 == "sqS" and _kS2 is None
                      and _s5["friendly_fire"] == [(0x3002, 0x3001)]
                      and _s5["dead"] == {0x3001, 0x3002})
        referee.BATTLE_STATE.pop("sqS", None)
        # destroy:all completes once every squad unit is dead
        _stq = {"squad": _s1, "kills": []}
        _bn = referee.objective_tick(_stq, _cA, _now, objective=("destroy", "all", None))
        _sq_ok &= _sc(8, battleend.parse_objective("destroy:all") == ("destroy", "all", None)
                      and "OBJECTIVE COMPLETE" in _bn and _stq["objective_done"])
        _stq2 = {"squad": dict(_s1, dead={0x2222}), "kills": []}
        referee.objective_tick(_stq2, _cA, _now, objective=("destroy", "all", None))
        _sq_ok &= _sc(9, not _stq2.get("objective_done"))
        # fire relay: B's own shot reaches A on B's alias stream, +0x08 = alias
        _rs = _types3.SimpleNamespace(popped=True, pending=[])
        _cA2 = _types3.SimpleNamespace(addr=("sqA", 1), key=b"%xbattle",
                                       alias_for=lambda a: 0x200,
                                       remotes={0x200: _rs})
        _cB2 = _types3.SimpleNamespace(addr=("sqB", 1), key=b"%xbattle",
                                       self_unit=lambda: 1)
        _svr = _gs["room_mates"]
        _gs["room_mates"] = lambda c: [_cA2] if c is _cB2 else []
        try:
            _nf = squad.fire_relay(_cB2, ("sqB", 1), bytes(0x18), 1)
            _nf2 = squad.fire_relay(_cB2, ("sqB", 1), bytes(0x18), 0x2222)
        finally:
            _gs["room_mates"] = _svr
        _sq_ok &= _sc(10, _nf == 1 and _nf2 == 0 and len(_rs.pending) == 1
                      and struct.unpack_from("<I", _rs.pending[0], 8)[0] == 0x200
                      and struct.unpack_from("<I", _rs.pending[0], 4)[0] == squad.CMD_BM_FIRE)
    except Exception as _e:
        print(f"  squad: EXC {_e!r}")
        _sq_ok = False
    finally:
        _gs.update(_sv)
        squad.BATTLE_SQUADS.clear()
        for _h in ("sqA", "sqB"):
            referee.BATTLE_STATE.pop(_h, None)
            rooms.WORLD_MAPS.pop(_h, None)
    print(f"  squad: n[:spread] parses, a ring of drop points, the cmd-24 filter "
          f"keeps only squad entries, one owner per room (handover when it "
          f"leaves, fresh per sortie), a kill counted once and credited to the "
          f"last shooter, destroy:all, own fire relayed with +0x08 = the alias: "
          f"{'OK' if _sq_ok else 'FAIL at ' + str(_sqf)}")
    ok &= _sq_ok
    # COM ENEMIES (2026-09-30): the squad POP carries the HP scale byte, each
    # enemy is dressed from the NPC loadout table (not the pilot's parts), a
    # vehicle kind is legal on record 0 only, and the NPC level is 5 x the
    # sector's B.G.Cost, else FMO_ENEMY_LEVEL.
    _nf = []

    def _nc(n, v):
        if not v:
            _nf.append(n)
        return bool(v)

    import random as _rnd_n
    import types as _types_n
    _nrows = squad.load_npc_loadouts(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "fmodata", "__none__.tsv"))          # absent -> []
    _ntab = [{"kind": "wanzer", "level": 10, "nation": 0, "role": "WAP", "name": "w10",
              "parts": [(0, 0x11, 656), (1, 0x21, 656), (2, 0x31, 656), (3, 0x31, 656)]},
             {"kind": "wanzer", "level": 25, "nation": 1, "role": "SNPR", "name": "w25o",
              "parts": [(0, 0x11, 257), (1, 0x21, 257), (4, 0x32, 92)]},
             {"kind": "wanzer", "level": 25, "nation": 2, "role": "SNPR", "name": "w25u",
              "parts": [(0, 0x11, 262), (1, 0x21, 262), (4, 0x32, 92)]},
             {"kind": "tank", "level": 20, "nation": 0, "role": "tank", "name": "t20",
              "parts": [(0, 0x29, 65), (10, 0x41, 249)]}]
    _pilot = [(0, 0x11, 1), (1, 0x21, 1)]
    _n_ok = _nc(1, _nrows == [] and squad.parse_loadout_parts("0=11:196 10=41:1")
                == [(0, 0x11, 196), (10, 0x41, 1)])
    # 1: the HP byte. 10 = 100% at body+0x125; 0 would be 1 HP a part
    _nsq = {"nation": 2, "parts": _pilot, "loadouts": [_ntab[2], _ntab[3]]}
    _np0 = squad.enemy_pop(_nsq, 0, 0x2222, (1.0, 2.0, 3.0, 0.0), 0x1001, 1, 0, 101)
    _nb0 = _np0[fmoworld.REC_HDR:]
    _n_ok &= _nc(2, squad.enemy_hp_scale(100) == 10 and squad.enemy_hp_scale(0) == 1
                 and _nb0[fmoworld.POP_HP_SCALE] == squad.enemy_hp_scale()
                 and _nb0[fmoworld.POP_HP_SCALE] > 0
                 and struct.unpack_from("<I", _nb0, fmoworld.POP_CLIENT_KIND)[0] == 1
                 and fmoworld.POP_HP_SCALE >= battlepop.POP_AI_BRAIN + 4)
    # 1b: paint. A U.S.N. squad wanzer wears D30 6 (Dark Gray) in both colour
    # slots and camo 101; a human (UnitType 4) gets none (+0x1B8 = NPC number)
    _n_ok &= _nc(2, struct.unpack_from("<HHH", _nb0, squad.POP_CAMO) == (101, 6, 6)
                 and squad.npc_paint(1, 0)[squad.POP_COLOUR_A] == struct.pack("<H", 42)
                 and squad.npc_paint(1, 4) == {} and squad.npc_paint(9, 0) == {})
    # 2: the loadout, not the pilot's parts, lands at body+0x8C
    _n_ok &= _nc(3,struct.unpack_from("<H", _nb0, fmoworld.POP_PARTS)[0] == 262
                 and _nb0[fmoworld.POP_PARTS + 2] == 0x11
                 and struct.unpack_from("<H", _nb0, fmoworld.POP_PARTS + 4 * fmoworld.POP_PART_STRIDE)[0] == 92)
    _np1 = squad.enemy_pop(_nsq, 1, 0x2223, (1.0, 2.0, 3.0, 0.0), 0x1001, 1, 0, 101)
    _nb1 = _np1[fmoworld.REC_HDR:]
    # 3: a vehicle: record 0's kind (body+0x8E, the model selector) has bit 3,
    # the backpack record 10 does not; nowhere else is a vehicle kind legal
    _n_ok &= _nc(4, _nb1[fmoworld.POP_MODELFLAGS] == 0x29
                 and _nb1[fmoworld.POP_PARTS + 10 * fmoworld.POP_PART_STRIDE + 2] == 0x41)
    _nv = []
    for _pp in ([(1, 0x29, 65)], [(0, 0x49, 1)], [(0, 0x28, 1)]):
        try:
            fmoworld.pop_parts_block(_pp)
            _nv.append(True)
        except ValueError:
            _nv.append(False)
    _n_ok &= _nc(5, _nv == [False, False, False]
                 and len(fmoworld.pop_parts_block([(0, 0x39, 100)])) == 0x84)
    # 4: the level: highest row level <= the battle's, the enemy's nation or
    # either; the sector's B.G.Cost x 5, else its NPC rank x 5, else FMO_ENEMY_LEVEL
    _r =_rnd_n.Random(7)
    _pk = [squad.pick_loadout(_ntab, lv, nat, _r, vehicle_pct=0)["name"]
           for lv, nat in ((12, 1), (30, 1), (30, 2), (3, 2))]
    _pv = squad.pick_loadout(_ntab, 30, 1, _r, vehicle_pct=100)["kind"]
    _n_ok &= _nc(6, _pk == ["w10", "w25o", "w25u", "w10"] and _pv in ("tank", "wanzer"))
    _svl = (dict(trade.LIVE_SESSIONS), squad.ENEMY_LEVEL, warstate._WAR_STATE)
    try:
        squad.ENEMY_LEVEL = 15
        _fws = _types_n.SimpleNamespace(data={"sectors": {"42": {"bg_max": 6}}})
        warstate._WAR_STATE = _fws
        trade.LIVE_SESSIONS["selftest-npc"] = _types_n.SimpleNamespace(sector=(42, 0, 0))
        trade.LIVE_SESSIONS["selftest-npc2"] = _types_n.SimpleNamespace(sector=(43, 0, 0))
        # the war map's NPC rank (ARE +0x68) when the war state has no B.G.Cost:
        # 200:72117 rank 1, 200:69122 rank 5, 98:1001 rank 0 (-> 1), frontline
        # 505:85102 has none; a war-state B.G.Cost still wins (zone 200, tile 42)
        for _k, _sz, _st in (("rk1", 200, 72117), ("rk5", 200, 69122), ("rk0", 98, 1001),
                             ("fz", 505, 85102), ("bg", 200, 42)):
            trade.LIVE_SESSIONS["selftest-npc-" + _k] = _types_n.SimpleNamespace(
                sector=(_st, 0, 0), sector_zone=_sz)
        _lv1 = squad.enemy_level_for("selftest-npc")[0]
        _lv2 = squad.enemy_level_for("selftest-npc2")[0]
        _lv3 = squad.enemy_level_for("selftest-npc-none")[0]
        _lvr = [squad.enemy_level_for("selftest-npc-" + _k)[0]
                for _k in ("rk1", "rk5", "rk0", "fz", "bg")]
        # and the squad a pilot meets is dressed for it, from the table
        _gsn = flat_globals()
        _svd = {k: _gsn[k] for k in ("BATTLE_DUMMY", "BATTLE_ENEMIES")}
        _gsn.update(BATTLE_DUMMY=(0x2222, 0, None), BATTLE_ENEMIES=(2, 30.0))
        try:
            squad.BATTLE_SQUADS.clear()
            referee.battle_state("selftest-npc", reset=True)
            _cn = _types_n.SimpleNamespace(addr=("selftest-npc", 1), key=b"%xbattle",
                                           last_fire=None)
            _sqn, _ = squad.battle_squad_for(_cn, (0, 0, 0, 0), 1, _pilot, mates=[],
                                             rows=_ntab, rnd=_rnd_n.Random(1))
        finally:
            _gsn.update(_svd)
            squad.BATTLE_SQUADS.clear()
            referee.BATTLE_STATE.pop("selftest-npc", None)
        _n_ok &= _nc(7, (_lv1, _lv2, _lv3) == (30, 15, 15)
                     and _lvr == [5, 25, 1, 15, 30]
                     and _sqn["level"] == 30 and len(_sqn["loadouts"]) == 2
                     and all(squad.enemy_parts(_sqn, _i) != _pilot for _i in range(2)))
    except Exception as _e:
        print(f"  com enemies: EXC {_e!r}")
        _n_ok = False
    finally:
        trade.LIVE_SESSIONS.clear()
        trade.LIVE_SESSIONS.update(_svl[0])
        squad.ENEMY_LEVEL, warstate._WAR_STATE = _svl[1], _svl[2]
    print(f"  com enemies: squad POP part HP byte body+0x125 = "
          f"{squad.enemy_hp_scale()} (0x611F7124), dressed from the NPC loadout "
          f"table not the pilot's parts, vehicle kind on record 0 only, NPC level "
          f"= 5 x sector B.G.Cost, else 5 x the war map's NPC rank, else "
          f"FMO_ENEMY_LEVEL: "
          f"{'OK' if _n_ok else 'FAIL at ' + str(_nf)}")
    ok &= _n_ok
    # CEASEFIRE BONUS + OFFICER REVIEW (SE's rules, our numbers).
    _rf = []

    def _rc(n, v):
        if not v:
            _rf.append(n)
        return bool(v)

    _rv_ok = _rc(1, servicerecord.ceasefire_share({"ocu": 30, "usn": 36}, 1) == 1.0     # 45:55 < 6:4
                 and abs(servicerecord.ceasefire_share({"ocu": 20, "usn": 46}, 2) - 46 / 66 / 0.5) < 1e-9
                 and servicerecord.ceasefire_share({"ocu": 20, "usn": 46}, 1) < 1.0
                 and servicerecord.ceasefire_share({"ocu": 0, "usn": 0}, 1) == 1.0)
    _eq = {"ocu": 33, "usn": 33}
    _b9, _b10, _b20, _b21, _b24 = (servicerecord.ceasefire_bonus(r, 1, _eq) for r in (9, 10, 20, 21, 24))
    _rv_ok &= _rc(2, _b9 is None and _b24 is None
                  and _b10 == (ranks.rank_pay(10)[0] * servicerecord.CEASEFIRE_DAYS,
                               int(round((ranks.rank_threshold(10) or 0)
                                         * servicerecord.CEASEFIRE_CONTRIB_PCT / 100)), 0)
                  and _b20[2] == 0 and _b20[1] > 0
                  and _b21[1] == 0 and _b21[2] == ranks.rank_pay(21)[1] * servicerecord.CEASEFIRE_MP_DAYS)
    _cfc = {}
    _rv_ok &= _rc(3, servicerecord.ceasefire_owed(_cfc, {"1": _eq}) == []
                  and _cfc["ceasefire_paid"] == [1]
                  and servicerecord.ceasefire_owed(_cfc, {"1": _eq, "2": _eq}) == [(2, _eq)])
    _rv_ok &= _rc(4, servicerecord.review_verdict(20, 3, "full", promote_n=3) == "promote"
                  and servicerecord.review_verdict(20, 1, "full", promote_n=3) == "keep"
                  and servicerecord.review_verdict(20, 0, "full", promote_n=3) == "demote"
                  and servicerecord.review_verdict(20, 0, "promote", promote_n=3) == "keep"
                  and servicerecord.review_verdict(20, 5, "full", promote_n=3, slot_free=False) == "keep"
                  and servicerecord.review_verdict(23, 9, "full", promote_n=3) == "keep"
                  and servicerecord.review_verdict(19, 9, "full") is None
                  and servicerecord.review_verdict(20, 9, "0") is None)
    _dc = servicerecord.review_demoted_contribution(19)
    _rv_ok &= _rc(5, (ranks.rank_threshold(19) or 0) < _dc < (ranks.rank_threshold(20) or 0)
                  and ranks.rank_for_contribution(_dc) == 19)
    _T = 1_800_000_000
    _iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    _rch = {"missions": [
        {"cat": 2, "status": "complete", "reported": _iso(_T - 100)},
        {"cat": 2, "status": "complete", "reported": _iso(_T - 10 * servicerecord.DAY)},
        {"cat": 3, "status": "complete", "reported": _iso(_T - 100)},
        {"cat": 2, "status": "met", "reported": _iso(_T - 100)}]}
    _rv_ok &= _rc(6, servicerecord.review_successes(_rch, 20, _T - 7 * servicerecord.DAY, _T) == 1
                  and servicerecord.review_successes(_rch, 21, _T - 7 * servicerecord.DAY, _T) == 1)
    # through a session: a Captain with 3 sector wins in the period -> Major;
    # one with none under 'full' -> First Lieutenant at ~90% of the bar
    _gr = flat_globals()
    _svr = {k: _gr[k] for k in ("REVIEW", "REVIEW_PROMOTE", "REVIEW_CAPS")}
    try:
        _gr.update(REVIEW="full", REVIEW_PROMOTE=3, REVIEW_CAPS={})
        _rs = session.Session("selftest-review:1")
        _rs.commit = lambda what: None
        _up = {"rank": 20, "contribution": 300000, "review_at": _T - 8 * servicerecord.DAY,
               "missions": [{"cat": 2, "status": "complete",
                             "reported": _iso(_T - i * servicerecord.DAY)} for i in (1, 2, 3)]}
        _dn = {"rank": 20, "contribution": 300000, "review_at": _T - 8 * servicerecord.DAY}
        _new = {"rank": 20}
        _v1 = _rs.officer_review(_up, now=_T)
        _v2 = _rs.officer_review(_dn, now=_T)
        _v3 = _rs.officer_review(_new, now=_T)
        _v4 = _rs.officer_review(dict(_up, review_at=_T - servicerecord.DAY), now=_T)
        _rv_ok &= _rc(7, _v1 == "promote" and _up["rank"] == 21
                      and _up["review_at"] == _T
                      and _v2 == "demote" and _dn["rank"] == 19
                      and _dn["contribution"] == servicerecord.review_demoted_contribution(19)
                      and _v3 is None and _new["review_at"] == _T
                      and _v4 is None)
    finally:
        _gr.update(_svr)
    # the ceasefire through a session: baseline first, then the next phase pays
    _svc = {k: _gr[k] for k in ("CEASEFIRE", "war_state", "_war_tick")}
    try:
        _war = _types3.SimpleNamespace(data={"phases": {"1": _eq}})
        _gr.update(CEASEFIRE=True, war_state=lambda: _war,
                   _war_tick=lambda st: None)
        _cs = session.Session("selftest-cease:1")
        _cs.commit = lambda what: None
        _cred = []
        _cs.credit_money = lambda why, money=0, contribution=0: _cred.append(contribution)
        _cc = {"rank": 12}
        _p1 = _cs.pay_ceasefire(_cc)
        _war.data["phases"]["2"] = {"ocu": 50, "usn": 16}
        _p2 = _cs.pay_ceasefire(_cc)
        _p3 = _cs.pay_ceasefire(_cc)
        _rv_ok &= _rc(8, _p1 == [] and len(_p2) == 1 and _p2[0][0] == 2
                      and _p3 == [] and _cc["ceasefire_paid"] == [1, 2]
                      and len(_cc.get("mission_pay") or []) == 1
                      and _cc["mission_pay"][0]["kind"] == servicerecord.PAY_CITY
                      and _cred and _cred[0] == _p2[0][1][1])
    finally:
        _gr.update(_svc)
    # steps 2, 5 and 8 price the bonus and the demotion off the rank ladder,
    # which only exists once fmodata/ is built from the user's own client
    _rv_skip = _fmodata_skip("fmo-ranks.tsv")
    _rv_hard = [n for n in _rf if not (_rv_skip and n in (2, 5, 8))]
    print(f"  ceasefire + review: the 6:4 split, the bonus by rank band (none "
          f"below First Sergeant), no back-pay on the first check, SE's "
          f"keep/promote/demote with a slot cap, a demotion leaves the bar at "
          f"~90%, both through a session: "
          + ("OK" if _rv_ok else "FAIL at " + str(_rv_hard) if _rv_hard
             else f"OK, steps 2/5/8 {_rv_skip}"))
    ok &= not _rv_hard
    # cmd 43 HIT LIST: decoded, echoed to the shooter verbatim, and to a room-
    # mate with ids rewritten; the mission block carries the battle side.
    _hf = []

    def _hc(n, v):
        if not v:
            _hf.append(n)
        return bool(v)

    # a captured shape: 1, n=2, slot 0, type 0; (0x2223, 99, 0xc2, 1), (1, 99, 0x3e, 0)
    _hl = (bytes([1, 2, 0, 0]) + struct.pack("<IHBB", 0x2223, 99, 0xC2, 1)
           + struct.pack("<IHBB", 1, 99, 0x3E, 0))
    _p = squad.parse_hitlist(_hl)
    _h_ok = _hc(1, _p == {"slot": 0, "type": 0,
                         "hits": [(0x2223, 99, 0xC2, 1), (1, 99, 0x3E, 0)]}
                and squad.parse_hitlist(b"\x02\x01\x00\x00") is None
                and squad.parse_hitlist(bytes([1, 3, 0, 0]) + bytes(8)) is None)
    import types as _types4
    _A = _types4.SimpleNamespace(addr=("hA", 1), key=b"%xbattle", pending=[],
                                 alias_of={("hB", 1): 0x200},
                                 self_unit=lambda: 1)
    _A.alias_for = lambda a: _A.alias_of[a]
    _B = _types4.SimpleNamespace(addr=("hB", 1), key=b"%xbattle", pending=[],
                                 alias_of={("hA", 1): 0x201},
                                 self_unit=lambda: 1)
    _B.alias_for = lambda a: _B.alias_of[a]
    _h_ok &= _hc(2, squad.id_for_viewer(1, _A, _B) == 0x201          # A's own unit
                 and squad.id_for_viewer(0x200, _A, _B) == 1         # A's alias for B = B itself
                 and squad.id_for_viewer(0x2223, _A, _B) == 0x2223)  # squad id unchanged
    _gh = flat_globals()
    _svh = _gh["room_mates"]
    _gh["room_mates"] = lambda c: [_B] if c is _A else []
    try:
        _ne = squad.hitlist_echo(_A, ("hA", 1), _hl, 1)
    finally:
        _gh["room_mates"] = _svh
    _selfrec, _materec = (_A.pending[0] if _A.pending else b""), (_B.pending[0] if _B.pending else b"")
    _mb = _materec[fmoworld.REC_HDR:]
    _h_ok &= _hc(3, _ne == 1 and len(_A.pending) == 1 and len(_B.pending) == 1
                 and struct.unpack_from("<I", _selfrec, 4)[0] == squad.CMD_BM_HITLIST
                 and struct.unpack_from("<I", _selfrec, 8)[0] == 1
                 and _selfrec[fmoworld.REC_HDR:] == _hl
                 and struct.unpack_from("<I", _materec, 8)[0] == 0x201
                 and squad.parse_hitlist(_mb)["hits"] == [(0x2223, 99, 0xC2, 1),
                                                          (0x201, 99, 0x3E, 0)])
    _mf = {l: (o, r) for l, o, r, _s in missionblock.mission_fields(side=2)}
    _h_ok &= _hc(4, _mf.get("BattleSide") == (missionblock.MB_BATTLE_SIDE, b"\x02")
                 and "BattleSide" not in {l for l, *_ in missionblock.mission_fields(side=0)})
    _b13a = sortie.reply_013a(mapno="38", ep_enable=False, side=1)
    _h_ok &= _hc(5, _b13a is not None and _b13a[sortie.R13A_BLOCK + missionblock.MB_BATTLE_SIDE] == 1)
    _t0 = int(time.time())
    _b13t = sortie.reply_013a(mapno="38", ep_enable=False)
    _st13 = struct.unpack_from("<I", _b13t, sortie.R13A_BLOCK + missionblock.MB_START_GAMETIME)[0] if _b13t else 0
    _b13z = sortie.reply_013a(mapno="38", ep_enable=False, start_time=0)
    _lu = session.Session("selftest-01a5:1").on_packet(packet.parse(packet.build(charselect.MSG_LOG_UPLOAD, b"hello log", seq=0x55, conn_id=1)))
    _h_ok &= _hc(6, _t0 - 5 <= _st13 <= _t0 + 5
                 and struct.unpack_from("<I", _b13z, sortie.R13A_BLOCK + missionblock.MB_START_GAMETIME)[0] == 0
                 and len(_lu) == 1 and packet.parse(_lu[0])["msg"] == 1)
    squad.BATTLE_SEEN["cast-test"] = time.time()
    _h_ok &= _hc(7, squad.lobby_cast_paused("cast-test")
                 and not squad.lobby_cast_paused("cast-test", now=time.time() + 60)
                 and not squad.lobby_cast_paused("never-battled"))
    squad.BATTLE_SEEN.pop("cast-test", None)
    # one router, two players: the Deck in battle must not hold the PC's cast
    from types import SimpleNamespace as _SN
    _cdeck = _SN(account="member:11", addr=("198.51.100.9", 1))
    _cpc = _SN(account="member:3", addr=("198.51.100.9", 2))
    squad.BATTLE_SEEN[referee.chan_bkey(_cdeck)] = time.time()
    _h_ok &= _hc(8, squad.lobby_cast_paused(referee.chan_bkey(_cdeck))
                 and not squad.lobby_cast_paused(referee.chan_bkey(_cpc)))
    squad.BATTLE_SEEN.pop(referee.chan_bkey(_cdeck), None)
    print(f"  cmd 43 hit list: decoded; echoed verbatim to the shooter and to a "
          f"room-mate with the shooter/target ids rewritten into its numbering; "
          f"the battle side byte at block+0x{missionblock.MB_BATTLE_SIDE:X}: "
          f"{'OK' if _h_ok else 'FAIL at ' + str(_hf)}")
    ok &= _h_ok
    print(f"  battle room: a battle room-mate pops with THEIR pop (alive, their "
          f"nation and parts), a lobby channel keeps the human, a different "
          f"nation is hostile, and a death is credited to the hostile mate who "
          f"fired in the last {rooms.BATTLE_KILL_WINDOW:.0f}s: "
          f"{'OK' if _br_ok else 'FAIL'}")
    ok &= _br_ok
    # 0x0159 = the script's server call: {event @+0x000, params[16] @+0x458},
    # the shape 0x610F9CB0 / 0x610F9E80 build and the live captures show.
    _rec = bytearray(scriptcall.S159_BODY_LEN)
    struct.pack_into("<I", _rec, scriptcall.S159_EVENT, 205)
    struct.pack_into("<III", _rec, scriptcall.S159_PARAMS, 34, 104, 130)
    _rec[scriptcall.S159_B418] = 7
    _ev, _ps = scriptcall.parse_0159(bytes(_rec))
    _short = scriptcall.parse_0159(b"\0" * 100)
    _desc = scriptcall.describe_0159(_ev, _ps)
    _rules = [{"event": 205, "p1": None, "answer": "", "set": "128=99", "note": "n"},
              {"event": 205, "p1": 34, "answer": "p1=flag[p3];p2=0", "set": "p3=99;173", "note": "x"},
              {"event": 104, "p1": None, "answer": "p1=flag[p1]", "set": "", "note": "q"}]
    _r = scriptcall.event_rule_for(205, _ps, _rules)
    _fl0 = bytes(256)
    _ans, _nfl, _what = scriptcall.apply_event_rule(_r, _ps, _fl0)
    _ans2, _nfl2, _what2 = scriptcall.apply_event_rule(_rules[2], [130] + [0] * 15, _nfl)
    _again = scriptcall.apply_event_rule(_r, _ps, _nfl)            # idempotent: nothing to change
    _out = scriptcall.answered_0159(bytes(_rec), _ans)
    # naming byte 130 as Son's mission reads the mission catalogue file
    _ms_skip = _fmodata_skip("fmo-missions.tsv")
    _ev_named = "Enemy Unit Annihilation" in _desc
    _ev_ok = (_ev == 205 and _ps[:3] == [34, 104, 130] and len(_ps) == 16
              and _short == (None, [])
              and "EVENT 205" in _desc
              and _r is _rules[1]                            # literal p1 beats *
              and scriptcall.event_rule_for(205, [99] + [0] * 15, _rules) is _rules[0]
              and scriptcall.event_rule_for(7, _ps, _rules) is None
              and _ans[:3] == [0, 0, 130] and _nfl[130] == 99 and _nfl[173 >> 3] & (1 << (173 & 7))
              and _nfl[128] == 0                               # the * row did not fire
              and _ans2[0] == 99 and _what2 == ["answer p1 130 -> 99"]  # answer only
              and _nfl2 == _nfl                                 # ...flags untouched
              and _again[1] == _nfl                              # flags idempotent
              and all(not w.startswith("flag") for w in _again[2])
              and struct.unpack_from("<III", _out, scriptcall.S159_PARAMS) == (0, 0, 130)
              and _out[scriptcall.S159_B418] == 7 and _out[:4] == _rec[:4]
              and all(r["note"] for r in scriptcall.load_event_table())  # shipped file parses
              # the armed rows (2026-10-01): 200/205 mark bit p1, 201 is an ack
              and [(r["event"], r["p1"], r["set"]) for r in scriptcall.load_event_table()]
              == [(104, None, "@step"), (105, None, "@report"),
                  (200, None, "p1"), (201, None, ""), (205, None, "p1")])
    # what the shipped rows DO: the sergeant's 205 [34] sets bit 34 and leaves
    # byte 128 alone; 201 [job] stores nothing (it used to write the job into
    # byte 128, which stranded every new pilot); registration is 104 [128]
    # 0 -> 1 (sergeant) -> 2 (training-success scene), 105 [128] 2 -> 99
    _ship = scriptcall.load_event_table()
    _z = bytes(256)

    def _sh(ev, p1, fl):
        return scriptcall.apply_event_rule(scriptcall.event_rule_for(ev, [p1] + [0] * 15, _ship),
                                           [p1] + [0] * 15, fl)
    _f34 = _sh(205, 34, _z)[1]
    _f3 = _sh(201, 3, _z)[1]
    _g1 = _sh(104, 128, _f34)
    _g2 = _sh(104, 128, _g1[1])
    _g2b = _sh(104, 128, _g2[1])                       # a third 104 does not pass 2
    _g1r = _sh(105, 128, _g1[1])                       # failed training: no report
    _g99 = _sh(105, 128, _g2[1])
    _shipped_ok = (_f34[34 >> 3] == 1 << (34 & 7) and _f34[128] == 0 and _f3 == _z
                   and _g1[1][128] == 1 and _g2[1][128] == 2 and _g2b[1][128] == 2
                   and _g1r[1][128] == 1 and _g99[1][128] == 99 and _g99[0][:2] == [99, 0]
                   and _g99[1][34 >> 3] == _f34[34 >> 3])
    _ev_ok &= _shipped_ok
    print(f"  0x0159 script call: event 205 p[34,104,130] decoded, "
          f"literal-p1 rule beats *, answer p1=flag[p3] "
          f"+ set p3=99 & bit 173, idempotent, answered record keeps +0x418; "
          f"shipped rows: 205 [34] sets bit 34 only, 201 [job] stores nothing, "
          f"104 [128] steps registration 0 -> 1 -> 2 and 105 [128] reports 2 -> 99 "
          f"(no reward lines): {'OK' if _ev_ok else 'FAIL'}")
    ok &= _ev_ok
    print(f"  0x0159 describe: byte 130 named as Son's mission (fmo-missions.tsv): "
          f"{_ms_skip or ('OK' if _ev_named else 'FAIL')}")
    if not _ms_skip:
        ok &= _ev_named
    # STORY MISSION STEPS (2026-09-30): 104 = accept (0 -> 1) / sortied (1 -> 2),
    # 105 = report (3 -> 99, p2 = 0), a board accept of a catalogue mission =
    # 0 -> 1, a return on the mission's tile = 1/2 -> 3 (99 when no script
    # reports the byte), and a report completes it with the level follow.
    # Table literals, so the pins do not drift with the served tsv.
    _sm_tbl = {1: [
        {"title": "Enemy Unit Annihilation", "level": 6, "pre": None, "own": 130, "pre_byte": None},
        {"title": "Guide the Special EMP Carrier", "level": 9,
         "pre": "Enemy Unit Annihilation", "own": 137, "pre_byte": 130},
        {"title": "Destroy the Rebels", "level": 41, "pre": None, "own": 155, "pre_byte": None}],
        2: []}
    _sm_tiles = {130: {62126, 66124}, 137: {70001}, 155: {70002}}
    _sm_save = (progress.MISSIONS, progress.REPORT_BYTES, progress.REWARDS)
    progress.MISSIONS, progress.REPORT_BYTES = _sm_tbl, {128, 130, 137}
    # SE's reward lines for 130 (the library's 0x824 switch); 137 pays nothing here
    progress.REWARDS = {130: {"money": 300, "items": {
        "O.C.U.": {"kind": "armor color", "name": "Mauve"},
        "U.S.N.": {"kind": "armor color", "name": "Azure Blue"}}},
        155: {"money": 0, "items": {}}}
    try:
        _sm_ship = scriptcall.load_event_table()
        _r104 = scriptcall.event_rule_for(104, [130] + [0] * 15, _sm_ship)
        _r105 = scriptcall.event_rule_for(105, [130] + [0] * 15, _sm_ship)
        _z = bytes(256)
        _a1, _s1, _w1 = scriptcall.apply_event_rule(_r104, [130] + [0] * 15, _z)
        _a2, _s2, _w2 = scriptcall.apply_event_rule(_r104, [130] + [0] * 15, _s1)
        _a3, _s3, _w3 = scriptcall.apply_event_rule(_r104, [130] + [0] * 15, _s2)  # 2 stays 2
        _c3 = bytearray(_s2)
        _c3[130] = 3
        _a4, _s4, _w4 = scriptcall.apply_event_rule(_r105, [130, 7] + [0] * 14, bytes(_c3))
        _a5, _s5, _w5 = scriptcall.apply_event_rule(_r105, [130, 7] + [0] * 14, _s1)  # 1: no report
        _c37 = bytearray(256)
        _c37[137] = 3
        _a7, _s7, _w7 = scriptcall.apply_event_rule(_r105, [137, 7] + [0] * 14, bytes(_c37))
        _g = bytearray(256)
        _g[128] = 2
        _a6, _s6, _w6 = scriptcall.apply_event_rule(_r104, [128] + [0] * 15, bytes(_g))
        _sc_ok = (_s1[130] == 1 and _a1[0] == 1
                  and _s2[130] == 2 and _a2[0] == 2
                  and _s3 == _s2 and not any(w.startswith("flag") for w in _w3))
        print(f"  story steps: 104 on a mission byte steps 0 -> 1 (accept) -> 2 "
              f"(sortied) and stops, answer p1 = the byte: {'OK' if _sc_ok else 'FAIL'}")
        ok &= _sc_ok
        _sr_ok = (_s4[130] == 99 and _a4[:2] == [99, 1]
                  and _s5 == _s1 and _a5[1] == 0
                  and _s7[137] == 99 and _a7[:2] == [99, 0]
                  and _s6 == bytes(_g) and _a6[0] == 2
                  and not any(w.startswith("flag") for w in _w6))
        _rw_ok = bool(fmostore)
        if fmostore:
            _rc = {"nation_byte": 2, "flags": fmostore.flags_hex(byte_values={128: 99})}
            _rb = bytearray(256)
            _rb[130], _rb[137] = 3, 3
            _ra = bytearray(_rb)
            _ra[130], _ra[137] = 99, 99
            _rd = progress.rewards_due(_rc, bytes(_rb), bytes(_ra))
            _rw_ok = (_rd == [(130, 300, {"kind": "armor color", "name": "Azure Blue"})]
                      and progress.rewards_due(_rc, bytes(_ra), bytes(_ra)) == []
                      and progress.mission_reward(155, 1) is None)
        print(f"  story steps: 105 reports 3 -> 99 with p2 = 1 where the byte has a reward "
              f"(0 where it has none) and moves nothing below 3; the reward due is the "
              f"pilot's nation's line (U.S.N. 130 = Azure Blue + 300 H$); byte 128 held "
              f"at 2 by a stray 104: {'OK' if (_sr_ok and _rw_ok) else 'FAIL'}")
        ok &= _rw_ok
        ok &= _sr_ok
        _sb_ok = bool(fmostore)
        if fmostore:
            _bc = {"nation_byte": 1, "flags": fmostore.flags_hex(byte_values={128: 99})}
            _bm, _bw = missionbook.story_accept_apply(_bc, "enemy unit  annihilation", None, 1,
                                                      _sm_tbl, _sm_tiles)
            _bm2, _bw2 = missionbook.story_accept_apply(_bc, "Recon Alpha", 70001, 1,
                                                        _sm_tbl, _sm_tiles)
            _bm3, _bw3 = missionbook.story_accept_apply(_bc, "Recon Alpha", 12345, 1,
                                                        _sm_tbl, _sm_tiles)
            _bm4, _bw4 = missionbook.story_accept_apply(_bc, "Enemy Unit Annihilation", None, 1,
                                                        _sm_tbl, _sm_tiles)
            _bf = fmostore.flags_bytes(_bc["flags"])
            _sb_ok = (_bm and _bm["own"] == 130 and _bm2 and _bm2["own"] == 137
                      and _bm3 is None and _bm4 is None and "already 1" in _bw4
                      and _bf[130] == 1 and _bf[137] == 1 and _bf[128] == 99)
        print(f"  story steps: a board accept of a catalogue mission (by title, else by "
              f"its tile) sets its byte 0 -> 1 once; a plain row sets nothing: "
              f"{'OK' if _sb_ok else 'FAIL'}")
        ok &= _sb_ok
        _sp_ok = bool(fmostore) and bool(classes.CLASS_CURVE)
        if fmostore and classes.CLASS_CURVE:
            _spc = {"nation_byte": 1, "rank": 21,
                   "flags": fmostore.flags_hex(byte_values={128: 99, 130: 2, 155: 1})}
            progress.set_pilot_level(_spc, 41)
            _nt = progress.advance_progress(_spc, _sm_tbl, tile=99999, tiles=_sm_tiles)
            _m1, _w1p, _r1 = progress.advance_progress(_spc, _sm_tbl, tile=66124, tiles=_sm_tiles)
            _f1 = fmostore.flags_bytes(_spc["flags"])
            _m2, _w2p, _r2 = progress.advance_progress(_spc, _sm_tbl, tile=66124, tiles=_sm_tiles)
            _m3, _w3p, _r3 = progress.advance_progress(_spc, _sm_tbl, tile=70002, tiles=_sm_tiles)
            _f3 = fmostore.flags_bytes(_spc["flags"])
            _sp_ok = (_nt[0] is None and _m1 and _m1["own"] == 130 and _f1[130] == 3
                      and _m2 is None and "already 3" in _w2p
                      and _m3 and _m3["own"] == 155 and _f3[155] == 99)
            # the report completes it and the level follows (EMP carrier is Lv 9)
            _lc = {"nation_byte": 1, "rank": 21,
                   "flags": fmostore.flags_hex(byte_values={128: 99, 130: 3})}
            progress.set_pilot_level(_lc, 6)
            _lb = fmostore.flags_bytes(_lc["flags"])
            _la = scriptcall.apply_event_rule(_r105, [130] + [0] * 15, _lb)[1]
            _lw = progress.completions_follow(_lc, _lb, _la)
            _sp_ok &= (len(_lw) == 1 and "Enemy Unit Annihilation" in _lw[0]
                       and progress.pilot_level(_lc) == 9)
        print(f"  story steps: a return on an accepted mission's tile clears it to 3 "
              f"(reported byte) or 99 (no report), once; a report completes it and "
              f"the Pilot level follows: {'OK' if _sp_ok else 'FAIL'}")
        ok &= _sp_ok
    finally:
        progress.MISSIONS, progress.REPORT_BYTES, progress.REWARDS = _sm_save
    # The pilot's acquired items: kept on the record, listed by 0x0133 after
    # the setups' own records, removed by a sale, deduplicated by serial.
    _ch = {}
    inventory.add_stored_item(_ch, 0x0000000500000001, 0x2004, 0x11, price=1200)
    inventory.add_stored_item(_ch, 0x0000000500000002, 0x1FFF, 0x12)
    _recs = inventory.stored_item_records(_ch)
    _setup_rec = inventory.item_record(0x0000000500000001, 0x2004, 0x11)   # same serial as item 1
    _m = inventory.merge_inventory([_setup_rec, bytes(inventory.INV_ENTRY_LEN)], _recs)
    _rm = inventory.remove_stored_item(_ch, 0x0000000500000001)
    _rm2 = inventory.remove_stored_item(_ch, 0x0000000500000001)
    _body = inventory.reply_0133(inventory.merge_inventory([], inventory.stored_item_records(_ch)))
    _it_ok = (len(_recs) == 2 and _recs[0] == _setup_rec
              and struct.unpack_from("<H", _recs[1], inventory.ITEM_ID)[0] == 0x1FFF
              and len(_m) == 2 and _m[0] == _setup_rec              # dedup + no blank rows
              and _rm and not _rm2 and len(inventory.stored_items(_ch)) == 1
              and inventory.stored_items(_ch)[0]["id"] == 0x1FFF
              and inventory.stored_items({"items": [{"bad": 1}, "x"]}) == []
              and struct.unpack_from("<I", _body, 0)[0] == 1
              and _body[8:8 + inventory.INV_ENTRY_LEN] == _recs[1]
              and len(inventory.merge_inventory([inventory.item_record(i, 1, 1) for i in range(1, 500)])) == inventory.INV_MAX)
    # THE WALLET FLOOR. A stored negative is a one-way trap: 0x014A masks it
    # to 0xFFFFFFFD and the client's own shop gate compares SIGNED, so the
    # pilot can never buy again, not even something priced 0.
    _w = session.Session.__new__(session.Session)
    _w.peer = "selftest-wallet"
    _wchar = {"money": 5, "contribution": 0}
    _w.playing_char = lambda: _wchar
    _w.commit = lambda why: None
    _floor_ok = (_w.credit_money("selftest overspend", money=-8) == (0, 0)
                 and _wchar["money"] == 0
                 and _w.credit_money("selftest sale", money=7) == (7, 0)
                 and _w.credit_money("selftest exact", money=-7) == (0, 0))
    # And a pilot with NO stored number debits from what 0x014A SHOWED them,
    # not from 0 -- the two-defaults bug that put the laptop's pilot at -10.
    _sm = flat_globals()["STATUS_MONEY"]
    try:
        flat_globals()["STATUS_MONEY"] = 12345
        _wchar2 = {"contribution": 0}
        _w.playing_char = lambda: _wchar2
        _floor_ok &= (_w.credit_money("selftest unseeded buy", money=-10)
                      == (12335, 0))
        # the one-time repair: prod's laptop pilot, stored -3, really has
        # 12345 - 10 + 7 = 12342, and says so in the source string
        _rep, _rep_src = economy.wallet_money({"money": -3})
        _floor_ok &= (_rep == 12342 and "REPAIRED" in _rep_src
                      and economy.wallet_money({"money": 7})[0] == 7)
    finally:
        flat_globals()["STATUS_MONEY"] = _sm
    print(f"  wallet floor: a debit past zero clamps to 0, a pilot can still be "
          f"paid afterwards, and an unseeded pilot spends from the balance "
          f"0x014A showed them: {'OK' if _floor_ok else 'FAIL'}")
    ok &= _floor_ok
    print(f"  acquired items: 2 kept, 0x0133 lists them, a setups record with the "
          f"same serial wins once, sale removes exactly one, blanks/garbage "
          f"skipped, capped at {inventory.INV_MAX}: {'OK' if _it_ok else 'FAIL'}")
    ok &= _it_ok
    if not ranks.RANK_FROM_CONTRIB:
        _rf = [f for f in status.status_fields(rank=None, class_table=False,
                                               char={"contribution": 340000}) if f[0] == "rank"]
        print(f"  rank: FMO_RANK_FROM_CONTRIB off -> a stored contribution does NOT "
              f"move the rank: {'OK' if not _rf or _rf[0][2] != bytes([22]) else 'FAIL'}")
        ok &= not _rf or _rf[0][2] != bytes([22])
    _hit = bytearray(fmoworld.HIT_LEN)
    _hit[fmoworld.HIT_KIND] = 1
    struct.pack_into("<h", _hit, fmoworld.HIT_VALUE, -7)
    struct.pack_into("<H", _hit, fmoworld.HIT_PART, 5)
    struct.pack_into("<I", _hit, fmoworld.HIT_FLAGS, 0x400)
    struct.pack_into("<I", _hit, fmoworld.HIT_TARGET, 0x2222)
    _ph = fmoworld.parse_hit(bytes(_hit))
    _pb = fmoworld.parse_hit_batch(bytes(fmoworld.HIT_BATCH_HDR) + bytes(_hit) * 2)
    _ph_ok = (_ph and _ph["target"] == 0x2222 and _ph["part"] == 5 and _ph["died"]
              and _ph["value"] == -7 and fmoworld.parse_hit(b"\0" * 10) is None
              and len(_pb) == 2 and _pb[1]["target"] == 0x2222)
    print(f"  hit record: target at +0x20, part +0x0A, value +0x08, DIED = flag "
          f"0x400; a batch of two parses: {'OK' if _ph_ok else 'FAIL'}")
    ok &= _ph_ok
    _rh = fmoworld.parse_hit(fmoworld.record_hit(1, part=2, value=350, died=True)[fmoworld.REC_HDR:])
    _rh_ok = (_rh and _rh["target"] == 1 and _rh["part"] == 2 and _rh["value"] == 350
              and _rh["died"] and _rh["kind"] == 1)
    print(f"  record_hit: a server-authored hit round-trips through parse_hit with "
          f"kind 1, the target, part, value and DIED: {'OK' if _rh_ok else 'FAIL'}")
    ok &= _rh_ok
    _po_ok = (battleend.parse_objective("") is None
              and battleend.parse_objective("hold:64,64,32,32:45") == ("hold", (64.0, 64.0, 32.0, 32.0), 45)
              and battleend.parse_objective("destroy:0x2222") == ("destroy", 0x2222, None)
              and _raises(lambda: battleend.parse_objective("hold:1,2:3"))
              and _raises(lambda: battleend.parse_objective("hold:1,2,0,4:5"))
              and _raises(lambda: battleend.parse_objective("capture:1"))
              and battleend.parse_battle_end("objective,escape") == {"objective", "escape"})
    print(f"  FMO_OBJECTIVE: hold/destroy parse, the rest refused; 'objective' is "
          f"a FMO_BATTLE_END trigger: {'OK' if _po_ok else 'FAIL'}")
    ok &= _po_ok

    class _PosChan:
        def __init__(self, pos):
            self.pos, self.pending, self.addr = pos, [], ("t", 1)
    _hobj = ("hold", (10.0, 10.0, 20.0, 20.0), 40)
    _hst = {"objective_done": None, "hold_since": None, "hold_said": None,
            "kills": [], "objective_banner": False, "ended": False}
    _b0 = referee.objective_tick(_hst, _PosChan((0.0, 0.0, 0.0, 0.0)), 1000.0, _hobj)
    _b1 = referee.objective_tick(_hst, _PosChan((15.0, 0.0, 15.0, 0.0)), 1001.0, _hobj)
    _b2 = referee.objective_tick(_hst, _PosChan((15.0, 0.0, 15.0, 0.0)), 1012.0, _hobj)  # 29s left -> "30s"
    _b3 = referee.objective_tick(_hst, _PosChan((99.0, 0.0, 15.0, 0.0)), 1020.0, _hobj)  # left the zone
    _b4 = referee.objective_tick(_hst, _PosChan((15.0, 0.0, 15.0, 0.0)), 1030.0, _hobj)  # re-entered
    _b5 = referee.objective_tick(_hst, _PosChan((15.0, 0.0, 15.0, 0.0)), 1071.0, _hobj)  # 41s held
    _b6 = referee.objective_tick(_hst, _PosChan((15.0, 0.0, 15.0, 0.0)), 1072.0, _hobj)  # done: quiet
    _ho_ok = (_b0 == ["OBJECTIVE: hold the zone for 40s"]
              and _b1 == ["Zone entered -- hold position"]
              and _b2 == ["30s to hold"]
              and _b3 == ["Zone lost -- return to the zone"]
              and _b4 == ["Zone entered -- hold position"]
              and _b5 == ["OBJECTIVE COMPLETE"] and bool(_hst["objective_done"])
              and _b6 == []
              and referee.battle_end_trigger(dict(_hst, granted_at=0.0, escaped=None),
                                             {"objective", "escape"}, 1073.0, 0)[1] is True)
    print(f"  hold objective: banner, enter, 30s mark, leave resets, re-enter, "
          f"complete after {_hobj[2]}s inside, then silent; the trigger fires as "
          f"a WIN: {'OK' if _ho_ok else 'FAIL'}")
    ok &= _ho_ok
    _dst = {"objective_done": None, "hold_since": None, "hold_said": None,
            "kills": [(0x2222, 5.0)], "objective_banner": True, "ended": False}
    _d1 = referee.objective_tick(_dst, _PosChan(None), 6.0, ("destroy", 0x2222, None))
    _d_ok = _d1 == ["OBJECTIVE COMPLETE"] and bool(_dst["objective_done"])
    print(f"  destroy objective: a DIED hit on the named unit completes it: "
          f"{'OK' if _d_ok else 'FAIL'}")
    ok &= _d_ok
    _bk2_ok = ((battleend.BATTLE_HIT == "off" or bool(os.environ.get("FMO_BATTLE_HIT", "").strip()))
               and (battleend.OBJECTIVE is None or bool(os.environ.get("FMO_OBJECTIVE", "").strip()))
               and (not ranks.RANK_FROM_CONTRIB
                    or bool(os.environ.get("FMO_RANK_FROM_CONTRIB", "").strip())))
    print(f"  knobs: FMO_BATTLE_HIT / FMO_OBJECTIVE / FMO_RANK_FROM_CONTRIB are off "
          f"unless the env sets them: {'OK' if _bk2_ok else 'FAIL'}")
    ok &= _bk2_ok

    ok &= _spoils_order_pins()      # spoils + ordered missions (loot.py, missionbook)
    ok &= _manual_missions_pins()   # the Playing Manual's mission rules (pp.45, 59-60)
    ok &= _manual_pilot_pins()      # the Playing Manual's pilot rules (pp.37-38, 42, 44)
    ok &= _manual_social_pins()     # tells, chat routing, targeting, battle group ops (pp.40-54)
    ok &= _mission_group_fee_pins()  # mission groups (p.61) and the Battle Fee rates (p.62)
    ok &= _coliseum_pins()          # the Coliseum desks (coliseum.py)
    ok &= _coliseum_match_pins()    # arena matches: pairing, judging, streak, bracket
    ok &= _coliseum_spectate_pins()  # Coliseum spectators: 0x01C0/0x01C1, receive-only
    ok &= _battle_end_title_pins()  # 0x014C end banner + EXP Gain window flags
    ok &= _coliseum_bar_pins()      # Coliseum Room -> the arena bar (map 124)
    ok &= _battle_spawn_pins()      # per-map battle spawn points (battlepop.BATTLE_SPAWNS)
    ok &= _change_room_pins()       # Change Room maps and per-zone room casts (roomcast.py)
    ok &= _wanzer_paint_pins()      # hangar paint -> 0x0166 starter + battle self-POP
    ok &= _pvp_room_pins()          # Frontline PvP rooms: waiting, start, judge, war (pvproom.py)
    ok &= _battle_position_pins()   # battle position from cmd 23/24, lobby cmd 240 unchanged

    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    addressing, areachange, areatargets, battleend, battlegroups, battlemaps, battlepop,
    charlist, charselect, charstore, citytable, classes, community, cosmetics, datagram,
    devtool, economy, gatetool, groupchannel, grouplogin, handshake, hangar, identity, inventory, lobapi,
    lobbymessage, missionblock, missionboard, missionbook, missionlist, move, npccast, penalty,
    npcroster, packet, partsstock, peerlink, permits, poplook, popnames, popnation, popparts,
    popself, popsweep, progress, pushes, ranks, referee, resultpush, resume, room, roomrelay,
    rooms, scriptcall, sectorwins, servicerecord, session, shop, sortie, sortiepush, squad,
    squadron, status, timesync, trade, udpconfig, warmap, warstate, withdraw, worldchannel,
    zonecontrol, zoneentry,
)
from . import loot, settlement  # noqa: E402  (the spoils / order pins)
from . import missiongroups  # noqa: E402  (the mission group pins)
