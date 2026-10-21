"""Selftest pins for the retail features made default, the
training ground's pay, the shop gates, the 0x0161 loadout, the Emergency Escape
and /sos relays, the story sortie size, Clash of Iron, the war report and the
event battles. No client, no socket; every global a pin touches is restored."""
import calendar
import os
import struct
import time

import fmoarena
import fmowar

T0 = 1_900_000_000.0


def _p(label, ok, fails):
    if not ok:
        fails.append(label)
    return bool(ok)


def defaults_pins():
    """The retail knobs that are on by default, read as the code defaults (the env of
    a selftest run sets none of them) and as the release seed."""
    from . import (coliseum, defaults, events, missiongroups, servicerecord, squadron,
                   timesync, training, warmap)
    unset = lambda k: not os.environ.get(k, "").strip()   # noqa: E731
    f = []
    _p("training kind", training.KIND or not unset("FMO_TRAINING_KIND"), f)
    _p("training pay", (not training.TRAINING_PAY) or not unset("FMO_TRAINING_PAY"), f)
    _p("review full", servicerecord.REVIEW == "full" or not unset("FMO_REVIEW"), f)
    _p("release review", defaults.RELEASE_DEFAULTS["FMO_REVIEW"] == "full", f)
    _p("mission group", missiongroups.MISSION_GROUP or not unset("FMO_MISSION_GROUP"), f)
    _p("time sync", timesync.TIME_SYNC or not unset("FMO_TIME_SYNC"), f)
    _p("insignia push", squadron.INSIGNIA_PUSH or not unset("FMO_INSIGNIA_PUSH"), f)
    _p("coliseum", coliseum.COLISEUM or not unset("FMO_COLISEUM"), f)
    _p("medal window", events.MEDAL_DEFAULT == "05-15..06-05"
       and events.medal_window(events.MEDAL_DEFAULT, calendar.timegm((2027, 5, 20, 0, 0, 0)))
       and not events.medal_window(events.MEDAL_DEFAULT, calendar.timegm((2027, 7, 1, 0, 0, 0))), f)
    _p("counter bonus", (warmap.COUNTER_BONUS_HS == 1000 and warmap.COUNTER_MISSION == 3)
       or not (unset("FMO_COUNTER_BONUS_HS") and unset("FMO_COUNTER_MISSION")), f)
    _p("release prices", "0:1000" in defaults.RELEASE_DEFAULTS["FMO_COSMETIC_PRICES"], f)
    print(f"  defaults: training result window on + no training pay, review "
          f"'full', mission groups, 0x0199, 0x01C5, Coliseum on, medal 05-15..06-05, "
          f"counter bonus 1000: " + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def training_pay_pins():
    """A training battle pays nothing (SE BOOT CAMP Lesson 6) and banks no
    kills; the same battle off the training ground pays and counts kills."""
    from . import charstore, inventory, referee, session
    f = []
    saved = charstore.CHAR_STORE
    try:
        charstore.CHAR_STORE = charstore.CHAR_STORE or "selftest-not-written"
        out = {}
        for training in (True, False):
            key = "r3-train-%d" % training
            pc = {"setups": bytes(inventory.REPLY_0166_LEN).hex(), "money": 0,
                  "contribution": 0}
            paid = []
            referee.BATTLE_STATE[key] = {"enemies": {0x2222}, "kills": [(0x2222, T0)],
                                         "training": training}
            s = session.Session.__new__(session.Session)
            s.peer, s.ip = key, key
            s.battle_key = lambda _k=key: _k
            s.battle_settlement = None
            s.playing_char = lambda _c=pc: _c
            s.stored_money = lambda: (0, 0)
            s.credit_money = lambda why, money=0, contribution=0, _l=paid: (
                _l.append((money, contribution)) or (money, contribution))
            s.credit_class_exp = lambda why, rows, _l=paid: _l.append(("exp", rows)) or {}
            s.owe_kill_bonus = lambda hs, n, _l=paid: _l.append(("bonus", hs))
            st = s.settle_battle("selftest training pay", won=True)
            out[training] = (st, paid, pc)
            referee.BATTLE_STATE.pop(key, None)
        st_t, paid_t, pc_t = out[True]
        st_f, paid_f, pc_f = out[False]
        _p("training pays nothing", st_t["money"] == 0 and st_t["contribution"] == 0
           and not st_t["exp_rows"] and not paid_t and not pc_t.get("kill_count"), f)
        _p("field battle pays", (st_f["money"] > 0 or st_f["contribution"] > 0
                                 or st_f["exp_rows"]) and pc_f.get("kill_count") == 1, f)
    except Exception as e:
        f.append("raised %r" % (e,))
    finally:
        charstore.CHAR_STORE = saved
    print(f"  training ground: no exp / contribution / H$ / kill count from a "
          f"training battle, the field twin pays and counts one kill: "
          + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def shop_pins():
    """The service medal off the squadron picker; the accessory shop's gates
    in the list and at the buy; a locker purchase banks an owned bit."""
    from . import cosmetics as cz, events, inventory, squadron
    f = []
    ids = {i for i, _e in squadron.insignia_for(1)} | {i for i, _e in squadron.insignia_for(2)}
    _p("medal not a squadron insignia", not ({142, 447} & ids) and events.is_medal(4, 142), f)
    saved = (cz.COSMETIC_GATES, cz.COSMETIC_STOCK, cz.COSMETIC_PRICES)
    try:
        cz.COSMETIC_GATES = True
        cz.COSMETIC_STOCK = cz.parse_stock("")
        cz.COSMETIC_PRICES = {0: 1000, 1: 2000}
        lt = {"rank": 17, "money": 50000}
        lt2 = {"rank": 18, "money": 50000}
        maj = {"rank": 21, "money": 50000, "kill_count": 250}
        rows = lambda c: {(k, i) for k, i, _e in cz.cosmetics_for(cz.A2_PILOT, 1, c)}  # noqa: E731
        r_lt, r_lt2, r_maj, r_none = rows(lt), rows(lt2), rows(maj), rows(None)
        _p("garrison cap at 2nd Lt", (0, 20) not in r_lt and (0, 20) in r_lt2, f)
        _p("dress hat at Major", (0, 26) not in r_lt2 and (0, 26) in r_maj, f)
        _p("helmets by kills", (0, 94) in r_maj and (0, 95) not in r_maj
           and (0, 93) not in r_lt2, f)
        _p("no pilot no gate", (0, 96) in r_none, f)
        v = cz.buy_verdict(26, 0, 1, 50000, inventory.owned_paint_ids(lt2, 1), char=lt2)
        _p("gated buy refused", not v["ok"] and "rank" in v["why"], f)
        v2 = cz.buy_verdict(26, 0, 1, 50000, inventory.owned_paint_ids(maj, 1), char=maj)
        _p("ungated buy sold", v2["ok"] and v2["new"] and v2["price"] == 1000, f)
        ch = {"money": 1}
        inventory.add_owned_paint(ch, "accessory", 26)
        inventory.add_owned_paint(ch, "suit", 120)
        bits = inventory.owned_paint_bits(dict(ch, setups=""), None)
        _p("owned bits", bits.get(0x440 + (26 - 10) // 8) == 1 << ((26 - 10) & 7)
           and bits.get(0x250 + (120 - 101) // 8) == 1 << ((120 - 101) & 7), f)
        _p("starting item free", cz.buy_verdict(1, 1, 1, 0, {}, char=lt)["ok"], f)
    except Exception as e:
        f.append("raised %r" % (e,))
    finally:
        cz.COSMETIC_GATES, cz.COSMETIC_STOCK, cz.COSMETIC_PRICES = saved
    print(f"  shop: service medal kept off the squadron picker; Garrison Cap "
          f"at Second Lieutenant, Officer's Dress Hat at Major, helmets by kill count, "
          f"listed and sold alike; locker buys bank bits at +0x440 / +0x250: "
          + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def warmap_row_pins():
    """0x0161 rows carry the sortie setup's backpack, four weapons and their
    kinds, and the squadron insignia."""
    from . import inventory, warmap
    f = []
    blk = bytearray(inventory.REPLY_0166_LEN)
    base = 0                                      # setup 1
    blk[base + inventory.SETUP_IN_USE] = 1

    def put(slot, iid, kind):
        at = base + inventory.SETUP_ITEM_OFF + slot * inventory.INV_ENTRY_LEN
        struct.pack_into("<H", blk, at + inventory.ITEM_ID, iid)
        blk[at + inventory.ITEM_KIND] = kind
    put(4, 12, 0x12)
    put(5, 40, 0x22)
    put(10, 60, 0x41)
    lo = warmap.player_loadout({"setups": blk.hex()})
    row = warmap.player_row("Ann", "Lee", 1, lo, insignia=131)
    _p("loadout", lo == {"backpack": 60, "weapons": [(12, 0x12), (40, 0x22), (0, 0), (0, 0)]}, f)
    _p("row", len(row) == 0x40 and row[0x22] == 1
       and struct.unpack_from("<H", row, 0x24)[0] == 60
       and struct.unpack_from("<4H", row, 0x26) == (12, 40, 0, 0)
       and tuple(row[0x2E:0x32]) == (0x12, 0x22, 0, 0)
       and struct.unpack_from("<H", row, 0x32)[0] == 131, f)
    _p("empty", warmap.player_loadout({}) == {"backpack": 0, "weapons": [(0, 0)] * 4}
       and warmap.player_row("A", "B", 2)[0x24:0x34] == bytes(16), f)
    print(f"  0x0161: backpack id +0x24, weapon ids +0x26.., kinds +0x2E.., "
          f"insignia +0x32 from the sortie setup: " + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


class _Ch:
    def __init__(self, addr, uid, aliases=None, of=None):
        self.addr, self._uid, self.pending = addr, uid, []
        self._aliases = aliases or {}
        self.alias_of = of or {}

    def self_unit(self):
        return self._uid

    def alias_for(self, addr):
        return self._aliases.get(addr, 0)


def escape_sos_pins():
    """cmd 50 / 129 bodies renamed for the receiver; /sos echoed once to its
    sender on the self stream."""
    from . import peerlink, referee
    f = []
    a_addr, b_addr = ("198.51.100.1", 1), ("198.51.100.2", 2)
    a = _Ch(a_addr, 0x1001, {b_addr: 0x5002}, {b_addr: 0x5002})
    b = _Ch(b_addr, 0x1001, {a_addr: 0x5001}, {a_addr: 0x5001})
    req = struct.pack("<IIHH", 0x1001, 0x5002, 2, 0)      # A asks B (A's ids)
    got = peerlink.rename_body_units(50, req, a, b)
    _p("cmd 50 to B", struct.unpack_from("<II", got, 0) == (0x5001, 0x1001)
       and got[8:] == req[8:], f)
    ans = struct.pack("<IIHH", 0x5001, 0x1001, 2, 1)      # B answers (B's ids)
    back = peerlink.rename_body_units(50, ans, b, a)
    _p("cmd 50 back to A", struct.unpack_from("<II", back, 0) == (0x1001, 0x5002), f)
    rec = bytes(4) + struct.pack("<II", 0x1001, 0x2222) + bytes(8)
    r129 = peerlink.rename_body_units(129, rec, a, b)
    _p("cmd 129", struct.unpack_from("<II", r129, 4) == (0x5001, 0x2222), f)
    _p("other cmd verbatim", peerlink.rename_body_units(23, req, a, b) == req, f)
    plain = bytes(12) + struct.pack("<I", 0x00030000) + bytes(16)
    body = struct.pack("<III", 0, 0, 1) + bytes(4)
    peerlink.sos_echo(a, body, {"plain": plain}, 0, a_addr)
    peerlink.sos_echo(a, body, {"plain": plain}, 0, a_addr)
    _p("sos echo once", len(a.pending) == 1
       and struct.unpack_from("<I", a.pending[0], 4)[0] == peerlink.SOS_CMD
       and struct.unpack_from("<I", a.pending[0], 8)[0] == 0x1001, f)
    _p("escape done reason", referee.ESCAPE_DONE_REASON == 0x24360679, f)
    print(f"  escape and /sos: cmd 50 / 129 body ids renamed both ways, other "
          f"cmds verbatim, /sos echoed once on the self stream: "
          + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def story_size_pins():
    """The catalogue's min/max pilots: 3:0 below, 3:1 above."""
    from . import storybattle as sb
    f = []
    _p("parse", sb.parse_size("2/10") == (2, 10) and sb.parse_size("5/5") == (5, 5)
       and sb.parse_size("") == sb.SIZE_DEFAULT and sb.parse_size("12/30") == (10, 10), f)
    row = {"title": "Test", "size": (3, 3)}
    saved = sb.STORY_SIZE
    try:
        sb.STORY_SIZE = True
        _p("short", sb.size_verdict(row, 2)[0] == -31103, f)
        _p("over", sb.size_verdict(row, 4)[0] == -31104, f)
        _p("fits", sb.size_verdict(row, 3) is None, f)
        sb.STORY_SIZE = False
        _p("off", sb.size_verdict(row, 1) is None, f)
    finally:
        sb.STORY_SIZE = saved
    if sb.BATTLES:
        sizes = {r["size"] for r in sb.BATTLES.values()}
        _p("table sizes", (5, 5) in sizes and (2, 10) in sizes, f)
    print(f"  story size: min/max from the catalogue, -31103 (3:0) short, "
          f"-31104 (3:1) over: " + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def coi_pins():
    """Clash of Iron: calendar, nights, standings (ties by first to reach),
    qualifiers, the final arena for qualifiers only, H$400,000 a win."""
    from . import coliseum as co
    f = []
    seasons = fmoarena.parse_coi("T1;prelims=2030-01-03,2030-01-04;final=2030-01-12;reg=twoguns")
    n0 = seasons[0]["nights"][0][1]
    saved = (fmoarena.COI_SEASONS, co.Coliseum.save)
    try:
        fmoarena.COI_SEASONS = seasons
        co.Coliseum.save = lambda self: True
        _p("night", fmoarena.coi_night(n0 + 60, seasons)[1] == "2030-01-03"
           and fmoarena.coi_night(n0 - 60, seasons) is None
           and fmoarena.coi_night(n0 + 2 * 3600, seasons) is None, f)
        one = fmoarena.scheduled(fmoarena.OFFICIAL[0], n0 + 60)
        _p("regulation", one.get("coi") == "T1" and one["bg_cost"] == 4
           and one["weapons"][fmoarena.WEAPON_SLOTS.index("mg")] == fmoarena.RULE_REQUIRED
           and one["weapons"][fmoarena.WEAPON_SLOTS.index("bz")] == fmoarena.RULE_BANNED, f)
        c = co.Coliseum(load=False)
        for acct, t in (("a", 10), ("b", 20), ("b", 30), ("a", 40), ("c", 50)):
            c.coi_note_win(acct, acct.upper(), n0 + t)
        st = fmoarena.coi_standings(c.coi["T1"]["nights"]["2030-01-03"])
        _p("standings", [r[0] for r in st] == ["b", "a", "c"], f)
        n1 = seasons[0]["nights"][1][1]
        c.coi_note_win("b", "B", n1 + 10)          # already qualified: not recorded
        c.coi_note_win("a", "A", n1 + 20)
        _p("qualified skip", "b" not in c.coi["T1"]["nights"].get("2030-01-04", {}), f)
        q = c.coi_qualified("T1")
        _p("qualifiers", [x[0] for x in q] == ["b", "a"], f)
        fa = seasons[0]["final_at"]
        arena = next((x for x in c.arenas(fa - 600) if x.get("kind") == "coi"), None)
        _p("final listed", arena is not None and arena["format"] == fmoarena.FORMAT_TOURNAMENT
           and arena["req_bgs"] == 8, f)
        if arena is not None:
            code_x, _w = c.register_verdict(arena, "c", ["c"], "c", fa - 600)
            code_b, _w = c.register_verdict(arena, "b", ["b"], "b", fa - 600)
            _p("qualifiers only", code_x == co.CODE_PASSWORD and code_b == 0, f)
            m = {"id": 1, "arena": arena["id"], "format": arena["format"], "spectators": [],
                 "teams": [{"leader": "b", "members": ["b"], "name": "B"},
                           {"leader": "a", "members": ["a"], "name": "A"}]}
            c.finish(m, 0, "test", fa + 60)
            _p("prize", c.verdicts["b"]["prize"] == fmoarena.COI_PRIZE_PER_WIN
               and c.verdicts["a"]["prize"] == 0 and c.coi["T1"]["final"] == {"b": 1}, f)
    except Exception as e:
        f.append("raised %r" % (e,))
    finally:
        fmoarena.COI_SEASONS, co.Coliseum.save = saved
    _p("default calendar", [s["name"] for s in fmoarena.parse_coi("")] == ["S01", "S02"]
       and fmoarena.parse_coi("0") == [], f)
    print(f"  Clash of Iron: nights 21:00-23:00 JST, the regulation on the 1 vs 1, "
          f"standings tie to the first to reach, qualified pilots not counted again, the "
          f"final for qualifiers only, H$400,000 a win: "
          + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def war_report_pins():
    """The judgement keeps the city table; the report page shows it; SE's
    phase 3 and 4 rewards are in the table."""
    import fmoreport
    import fmosectors
    f = []
    _p("phase 3/4 rewards", fmowar.REWARD_TABLE[3] == {1: "vyzov", 2: "vyzov"}
       and fmowar.REWARD_TABLE[4] == {1: "vyzov", 2: "stork-varsa"}, f)
    w = fmowar.War(autosave=False, load=False)
    w.data = {"sectors": {}, "phases": {}, "log": [], "phase1": "2026-09-05"}
    w.seed_from_sectors(fmosectors.SECTORS, force=True)
    for _ in range(fmowar.COUNTER_CAP):
        w.settle(85102, fmowar.OCU, won=True, now=fmowar._ts(2026, 10, 1))
    j = w.tick(now=fmowar._ts(2026, 11, 1, 12))
    rec = j[0][1] if j else {}
    _p("cities kept", rec.get("cities", {}).get("85102") == fmowar.OCU
       and len(rec.get("cities", {})) == len(fmowar.CITIES), f)
    page = fmoreport.page(w, {"coi": {}}, now=fmowar._ts(2026, 11, 6))
    _p("page", "Phase 01" in page and "Maltaf" in page and "Arpeggio/Orgel" in page
       and "starts Phase 02 in Deadlock" in page and "Phase 02, in progress" in page
       and chr(0x2014) not in page, f)
    print(f"  war report: judgement keeps the 19-city holders, /report shows "
          f"the table, the Deadlock and the reward; SE's phase 3/4 rewards tabled: "
          + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def event_battle_pins():
    """The event table: windows (JST, New Year wrap), tiles from the ARE
    rows, the enemy level and the win bonus."""
    from . import events, settlement, squad, trade
    f = []
    rows = [{"name": "Test Event", "nation": 1, "selector": 100, "row": 20,
             "start": (12, 22, 12, 0), "end": (12, 28, 12, 0), "enemy_level": 25,
             "boss": True, "bonus_hs": 5000, "bonus_contrib": 100, "note": ""},
            {"name": "Wrap", "nation": 0, "selector": 100, "row": 5,
             "start": (12, 30, 0, 0), "end": (1, 2, 0, 0), "enemy_level": 7,
             "boss": False, "bonus_hs": 0, "bonus_contrib": 0, "note": ""}]
    sec = {100: {63126: (20, 70), 61125: (5, 182)}}
    on = calendar.timegm((2030, 12, 24, 0, 0, 0))
    off = calendar.timegm((2030, 12, 29, 0, 0, 0))
    ny = calendar.timegm((2031, 1, 1, 0, 0, 0))
    _p("window", events.event_for(63126, 1, on, rows, sec) is rows[0]
       and events.event_for(63126, 1, off, rows, sec) is None
       and events.event_for(63126, 2, on, rows, sec) is None
       and events.event_for(61125, 2, ny, rows, sec) is rows[1], f)
    _p("running", events.running_events(on, rows) == ["Test Event"], f)
    shipped = events.load_event_battles(events.EVENT_TSV)
    _p("shipped table", not shipped or all(events.event_tile(r) for r in shipped), f)
    saved = (events.EVENT_BATTLES, events.event_on)
    paid = []

    class _S:
        sector, peer = (63126, 20, 70), "r3-event"

        def in_arena(self):
            return False

        def credit_money(self, why, money=0, contribution=0):
            paid.append((money, contribution))
    try:
        events.EVENT_BATTLES = rows[:1]
        events.event_on = lambda ev, now=None: True
        trade.LIVE_SESSIONS["r3-event"] = _S()
        lvl, _why = squad.enemy_level_for("r3-event")
        _p("enemy level", lvl == 25, f)
        settlement.SessionSettlement.event_battle_settle(_S(), True)
        settlement.SessionSettlement.event_battle_settle(_S(), False)
        _p("win bonus", paid == [(5000, 100)], f)
    except Exception as e:
        f.append("raised %r" % (e,))
    finally:
        events.EVENT_BATTLES, events.event_on = saved
        trade.LIVE_SESSIONS.pop("r3-event", None)
    print(f"  event battles: JST windows (New Year wrap), tiles from the ARE "
          f"rows, the event's enemy level and its win bonus: "
          + ("OK" if not f else "FAIL " + ", ".join(f)))
    return not f


def run():
    ok = defaults_pins()
    ok &= training_pay_pins()
    ok &= shop_pins()
    ok &= warmap_row_pins()
    ok &= escape_sos_pins()
    ok &= story_size_pins()
    ok &= coi_pins()
    ok &= war_report_pins()
    ok &= event_battle_pins()
    return ok
