"""Session's battle settlement: kill bonuses, mission and war results, the counter mission, the
battle end and result pushes."""
import struct
import time
from .deps import fmosectors, fmowar
from .knobs import _env_int
from .wirelog import log


#: KEY: FMO_ARENA_NO_PAY -- a Coliseum battle pays no contribution and no MP
#: (default 1; 0 = pay it like any battle). Playing Manual p.45: 「アリーナでの
#: 戦闘では、貢献値やMPは獲得できません」 ("battles in an Arena do not earn
#: contribution value or MP"). So in the Coliseum zones the settlement credits
#: no contribution (sortie, kill or win) and no mission is settled by the
#: battle (a met battle-map mission's share pays MP). H$ is not named by the
#: manual and is still paid.
ARENA_NO_PAY = _env_int("FMO_ARENA_NO_PAY", 1) != 0
#: The Coliseum zone band (zoneentry's 600..607, the arena MapKinds).
ARENA_ZONES = (600, 607)
#: FMO_ARENA_EXP_PCT -- class exp an arena battle pays, percent of a normal
#: battle's. SE 2005-12-20: "arenas now give experience, less than a normal
#: sector".
ARENA_EXP_PCT = _env_int("FMO_ARENA_EXP_PCT", 50)


def arena_exp_rows(rows, pct=None):
    """`rows` [(kind, amount)] cut to ARENA_EXP_PCT; a row that had exp keeps 1."""
    pct = ARENA_EXP_PCT if pct is None else pct
    return [(k, max(1, a * pct // 100) if a > 0 else a) for k, a in rows]


def arena_zone(zone):
    """Is `zone` (a zone id / MapKind) one of the Coliseum's? Pure."""
    try:
        return ARENA_ZONES[0] <= int(zone) <= ARENA_ZONES[1]
    except (TypeError, ValueError):
        return False


class SessionSettlement:
    """The battle-settlement half of Session: what a finished battle pays and pushes."""

    def settle_battle(self, why, won=None):
        """ONE settlement per sortie: what this battle pays, banked ONCE.

        WARNING: Until 2026-09-11 the two end-of-battle pushes each credited the
        store on their own: 0x014C added FMO_BATTLE_END_CONTRIB and 0x015A
        added FMO_RESULT_CONTRIB, and the trigger sends BOTH -- so with both
        knobs set a pilot was paid twice for one battle, and the two screens
        (the EXP Gain page reads 0x014C's +0x0E8/+0x0EC, the wallet reads
        0x015A's +0x410/+0x414) could show different totals for the same
        fight. There is one battle and one pay: the deltas are computed here,
        credited here, and both packets are built from the same numbers.

        The contribution delta is FMO_RESULT_CONTRIB, falling back to
        FMO_BATTLE_END_CONTRIB (two names for one knob; a disagreement is
        logged). Money is FMO_RESULT_MONEY. Class exp is FMO_BATTLE_END_EXP.
        KEY: That per-sortie contribution IS SE's 出撃貢献値 -- "sortie
        contribution, earned by sortieing at all" (intro/flow3.html:15-18);
        the other kind, 撃破貢献値 for destroying enemies, is FMO_KILL_CONTRIB
        per kill (battle_pay). So a battle with zero kills must still credit
        this delta.

        `won` is the verdict; None = FMO_BATTLE_END_WON. The FIRST caller in a
        sortie decides it, so every caller that knows the verdict passes it
        (the end trigger's, and False for a withdraw).
        Returns the cached dict on every later call in the same sortie."""
        cached = getattr(self, "battle_settlement", None)
        if cached is not None:
            return cached
        _won = battleend.BATTLE_END_WON if won is None else bool(won)
        try:
            rows = battleend.parse_exp_rows(battleend.BATTLE_END_EXP)
        except ValueError as e:
            log(f"{self.peer}   WARNING: FMO_BATTLE_END_EXP: {e} -- no class exp this battle")
            rows = []
        # FMO_EXP_PACE: exp sized to the pilot's Pilot level (battleend's note)
        if battleend.EXP_PACE > 0:
            _pc = self.playing_char() if charstore.CHAR_STORE else None
            _jobs = inventory.set_jobs(_pc) if _pc is not None else []
            _src = "the pilot's set jobs (main first)"
            if not _jobs:
                _src = "FMO_EXP_JOBS (no job set on this pilot)"
                try:
                    _jobs = battleend.parse_exp_jobs(battleend.EXP_JOBS)
                except ValueError as e:
                    log(f"{self.peer}   WARNING: FMO_EXP_JOBS: {e} -- no paced exp this battle")
                    _jobs = []
            if _pc is not None:
                _lv = progress.pilot_level(_pc)
                _paced = battleend.paced_exp_rows(_lv, _won, classes.CLASS_CURVE, _jobs)
                # FMO_EXP_SECTOR_PCT / FMO_EXP_FRONT_PCT: SE's exp grew with
                # the sector (battleend's note); the Arena has its own cut
                if _paced and not self.in_arena() and (
                        battleend.EXP_SECTOR_PCT != 100 or battleend.EXP_FRONT_PCT != 100):
                    _zone = getattr(self, "sector_zone", None)
                    try:
                        _nl, _nsrc = squad.enemy_level_for(getattr(self, "ip", None))
                        _pct = battleend.sector_exp_pct(_nl, _zone)
                    except Exception as e:      # never cost the pilot the battle's pay
                        _nl, _nsrc, _pct = None, f"lookup failed: {e!r}", 100
                    _paced = [(k, a * _pct // 100) for k, a in _paced]
                    _paced = [(k, a) for k, a in _paced if a > 0]
                    log(f"{self.peer}   sector exp {_pct}% (NPC level {_nl}: {_nsrc}; "
                        f"zone {_zone}; FMO_EXP_SECTOR_PCT={battleend.EXP_SECTOR_PCT}, "
                        f"FMO_EXP_FRONT_PCT={battleend.EXP_FRONT_PCT})")
                if _paced:
                    log(f"{self.peer}   paced exp (FMO_EXP_PACE={battleend.EXP_PACE}, Pilot "
                        f"Lv{_lv}, {'win' if _won else 'loss'}, to {_src}): "
                        + ", ".join(f"{classes.CLASS_NAMES.get(k, k)} +{a}" for k, a in _paced))
                rows = list(rows) + _paced
        contrib = resultpush.RESULT_CONTRIB or battleend.BATTLE_END_CONTRIB
        if resultpush.RESULT_CONTRIB and battleend.BATTLE_END_CONTRIB and resultpush.RESULT_CONTRIB != battleend.BATTLE_END_CONTRIB:
            log(f"{self.peer}   WARNING: FMO_RESULT_CONTRIB={resultpush.RESULT_CONTRIB} and "
                f"FMO_BATTLE_END_CONTRIB={battleend.BATTLE_END_CONTRIB} disagree -- they "
                f"name ONE delta; using {contrib}. Set one of them.")
        kills = battleend.battle_kills(referee.BATTLE_STATE.get(self.battle_key()))
        try:
            pay = battleend.battle_pay(len(kills), _won, money=resultpush.RESULT_MONEY,
                                       contrib=contrib, exp_rows=rows)
        except ValueError as e:
            log(f"{self.peer}   WARNING: FMO_KILL_EXP: {e} -- no kill exp this battle")
            pay = battleend.battle_pay(len(kills), _won, money=resultpush.RESULT_MONEY,
                                       contrib=contrib, exp_rows=rows, kill_exp=())
        money, contrib, rows = pay["money"], pay["contribution"], pay["exp_rows"]
        if contrib and self.in_arena():
            # Playing Manual p.45: no contribution in an Arena (FMO_ARENA_NO_PAY)
            log(f"{self.peer}   ARENA: zone {rooms.WORLD_ZONES.get(self.ip)} is the "
                f"Coliseum -- contribution {contrib:+d} NOT paid (FMO_ARENA_NO_PAY; "
                f"Playing Manual p.45)")
            contrib = 0
            pay["contribution"] = 0
        if rows and self.in_arena():
            rows = arena_exp_rows(rows)
            log(f"{self.peer}   ARENA: class exp cut to {ARENA_EXP_PCT}% "
                f"(FMO_ARENA_EXP_PCT): {rows}")
        # PLATOON (battle groups, 2026-09-30): exp bonus, B.G.Bonus share,
        # join-in percent, auto-disband -- see platoon_battle_settle.
        money, rows = self.platoon_battle_settle(_won, money, rows, pay)
        mc = self.stored_money()
        old_money = mc[0] if mc else 0
        old = mc[1] if mc else 0
        banked = False
        new_money, new = old_money + money, old + contrib
        if money or contrib:
            now = self.credit_money(why, money=money, contribution=contrib)
            if now is not None:
                new_money, new = now
                banked = True
        if pay["kill_bonus_hs"]:
            self.owe_kill_bonus(pay["kill_bonus_hs"], len(kills))
        if rows:
            # Banked BEFORE the push, like the money: the 0x014C rows are
            # deltas the client adds to its lobby+0xF08 copy, and the next
            # 0x014A serves the same totals back, so the screen and the store
            # cannot disagree after a relog.
            self.credit_class_exp(why, rows)
        self.battle_settlement = {
            "money": money, "contribution": contrib, "exp_rows": rows,
            "contrib_old": old, "contrib_new": new,
            "money_old": old_money, "money_new": new_money,
            "banked": banked, "why": why, "won": _won, "kills": kills,
            "kill_bonus_hs": pay["kill_bonus_hs"],
        }
        log(f"{self.peer}   BATTLE SETTLEMENT ({why}): "
            f"{'WON' if _won else 'LOST'}, {len(kills)} enemy kill(s)"
            + (f" ({', '.join(f'{k:#x}' for k in kills)})" if kills else "")
            + f" -> {'; '.join(pay['parts']) or 'nothing to pay'}. "
            f"Money {money:+d} "
            f"({old_money} -> {new_money}), contribution {contrib:+d} "
            f"({old} -> {new}), class exp {rows or 'none'} -- "
            f"{'BANKED on the pilot' if banked else 'NOT banked (no pilot in the store): the pushes move the display only'}"
            f"; both 0x014C and 0x015A are built from these numbers.")
        return self.battle_settlement

    def in_arena(self):
        """Is this connection's battle a Coliseum (Arena) one that pays no
        contribution or MP? The zone its last 0x0153 granted decides; the
        war map's sector_zone is not used, as a Coliseum sortie opens no war
        map and an older one would still be on the session."""
        # a Coliseum MATCH (coliseum.py) is an arena battle wherever the
        # pilot stood
        if not ARENA_NO_PAY:
            return False
        try:
            _st = referee.BATTLE_STATE.get(self.battle_key()) or {}
        except Exception:
            _st = {}
        return bool(_st.get("arena_match")) or arena_zone(
            rooms.WORLD_ZONES.get(getattr(self, "ip", None)))

    def platoon_battle_settle(self, won, money, rows, pay):
        """The battle-group half of one settlement (2026-09-30). Returns the
        (money, exp rows) to bank and sets self.platoon_end, the 0x014C
        fields battle_end_push adds:
          * platoon exp bonus: every exp row x battlegroups.platoon_exp_pct
            on a win (+0x0F2 -> 8:45 when > 100);
          * the B.G.Bonus share, win or lose (AH/F98/D92 235): paid into the
            wallet here, named by +0x2EC / +0x2E8 (8:61, or 8:72 for the
            leader, whose own share -- and the split's remainder -- comes back);
          * auto-disband: +0x0F4 (8:58) on the battle that leaves one to go,
            and the group is dropped after its last (AH/F98/D92 210);
          * join-in percent (update 050628 22-24): the kill bonus of a pilot
            who joined the enemy's map late scales with self.join_pct, and
            +0x2DF carries it (8:65 prints when > 100)."""
        _pc = self.playing_char() if charstore.CHAR_STORE else None
        _own = charlist.to_wire(_pc["id"]) if _pc and _pc.get("id") else None
        pl = battlegroups.platoon_settle(getattr(self, "account", None), won,
                                        own_id=_own)
        end = {}
        if pl:
            if pl["exp_pct"] != 100 and rows:
                _was = list(rows)
                rows = battleend.platoon_exp_rows(rows, pl["exp_pct"])
                log(f"{self.peer}   PLATOON EXP: {pl['n']} member(s) of group "
                    f"{pl['gid']} in this battle -> {pl['exp_pct']}% on every "
                    f"row (FMO_PLATOON_EXP_PER/_MAX, ours): {_was} -> {rows}")
            end["platoon_pct"] = pl["exp_pct"] if pl["exp_pct"] > 100 else 0
            if pl["share"]:
                money += pl["share"]
                end["platoon_money"] = pl["share"]
                end["platoon_payer"] = pl["payer_id"]
                log(f"{self.peer}   B.G.BONUS: H$ {pl['share']} to this pilot, "
                    f"its even share of group {pl['gid']}'s bonus among "
                    f"{pl['n']} (AH/F98/D92 235), {'WON' if won else 'LOST'}; "
                    f"+0x2E8 payer {pl['payer_id']:#x}")
            if pl["auto_disband"]:
                end["auto_disband"] = 1
                log(f"{self.peer}   group {pl['gid']}: one battle left before it "
                    f"auto-disbands -> +0x0F4 = 1 (8:58)")
        jp = getattr(self, "join_pct", None)
        if jp is not None and jp != 100:
            if pay.get("kill_bonus_hs"):
                _kb = pay["kill_bonus_hs"]
                pay["kill_bonus_hs"] = _kb * jp // 100
                log(f"{self.peer}   JOIN-IN TIME: kill bonus H$ {_kb} x {jp}% = "
                    f"H$ {pay['kill_bonus_hs']} (update 050628 22-24)")
            end["join_pct"] = jp
        self.platoon_end = end
        return money, rows

    def loot_battle_settle(self, won, now=None, rnd=None):
        """SPOILS (loot.py): on a WIN fought with the pilot's battle group,
        open that group battle's loot round -- once per battle, whichever
        member settles first. The drops are OURS (SE's tables are not in the
        client): FMO_LOOT_DROPS of them, from the parts the defeated squad
        wore (fmo-npc-loadouts.tsv, NPC-only frames excluded) and from any
        part in the FMO_LOOT_BAND levels up to the battle's NPC level
        (fmo-part-levels.tsv). Called from loot.loot_pushes_due on the
        keepalive after settle_battle, so no existing settlement changes.
        Returns the round, or None."""
        if not (loot.LOOT and won and loot.LOOT_DROPS):
            return None
        acct = getattr(self, "account", None)
        found = loot.group_battle_of(acct, now)
        if found is None:
            return None                      # a solo win: SE gives spoils to groups
        gid, rec = found
        sq = loot.battle_squad({self.battle_key(), self.ip})
        level = (sq or {}).get("level")
        src = "the squad's own level"
        if level is None:
            level, src = squad.enemy_level_for(self.ip)
        los = [lo for lo in (sq or {}).get("loadouts") or () if lo]
        enemy, near = loot.drop_pool(level, npc_names=loot.npc_only_names(squad.NPC_LOADOUTS),
                                     loadouts=los)
        items = loot.draw_drops(loot.LOOT_DROPS, enemy, near, rnd)
        rd = loot.open_round(gid, rec, items, now)
        log(f"{self.peer}   LOOT: group {gid} WON battle {rec.get('n')} with "
            f"{rec.get('joined')}; NPC level {level} ({src}); pools: {len(enemy)} "
            f"part(s) off {len(los)} defeated loadout(s), {len(near)} in the level "
            f"band -> "
            + (f"round open, {len(rd['items'])} drop(s): "
               + ", ".join(f"kind 0x{k:02X} id {i}" for k, i in rd["items"])
               if rd else "no round (nothing to drop, or this battle's round "
               "already closed)"))
        return rd

    def warmap_entry(self, row):
        """One 0x015F row's count, minutes and bar from who is fighting on
        its battle map now (FMO_WARMAP_ENTRY; see S15F_COUNT)."""
        mapno = struct.unpack_from("<I", row, 0)[0]
        now = time.time()
        counts, start, who = warmap.warmap_census(
            mapno, referee.BATTLE_STATE, lambda h: popnation.battle_side_for(h)[0], now,
            missionblock.MISSION_TIME)
        own, _osrc = popnation.battle_side_for(self.ip)
        own = own if own in (0, 1) else 0
        elapsed = now - start if start is not None else 0
        warmap.warmap_entry_fill(row, counts, own, elapsed, missionblock.MISSION_TIME)
        _lim, _min = struct.unpack_from("<hh", row, warmap.S15F_LIMIT)
        log(f"{self.peer}   BATTLE MAP ENTRY: map {mapno}: O.C.U. {counts[0]} / "
            f"U.S.N. {counts[1]} wanzer(s) fighting "
            f"({', '.join(f'{h} side {s}' for h, s in who) or 'nobody'}); "
            f"row +0x04/+0x06 = {_lim}/{_min} min (ELAPSED, SE's callout 2), "
            f"+0x08/+0x09 = {counts[0]}/{counts[1]} -- this viewer is side "
            f"{own} ({_osrc}), so the \"%02d\" should read {counts[own]:02d} "
            f"and the minutes {_min:02d}min; +0x4A/+0x58 = {row[warmap.S15F_BAR_OWN]}/"
            f"{row[warmap.S15F_BAR_OTHER]} of {warmap.S15F_SIDE_CAP} a side (blue/pink bar, "
            f"viewer-relative is UNBOUND).")
        return row

    def mission_map_icon(self, tile, selector=None):
        """This pilot's OPEN missions whose battlefield is `tile` in the
        `selector` zone -- the ones the map selector flags on that sector's
        battle map. An accept with no stored zone matches on the tile alone
        (accepts stored before zones were). [] when off."""
        if not (missionboard.MISSION_REPORT and warmap.MISSION_MAP_ICON and tile):
            return []
        char = self.playing_char() if charstore.CHAR_STORE else None
        hits = [m for m in missionbook.accepted_missions(char)
                if missionbook.mission_status(m) == "open"
                and int(m.get("sector") or 0) == int(tile)
                and (not m.get("zone") or selector is None
                     or int(m["zone"]) == int(selector))]
        if hits:
            log(f"{self.peer}   MISSION ICON: tile {tile} is the battlefield "
                f"of " + ", ".join(f"{m.get('name')!r} (id {m.get('id')})"
                                   for m in hits)
                + f" -> row +0x{warmap.S15F_ICON:02X} = {warmap.MISSION_MAP_ICON} "
                f"(0x6118B8D0 draws sprite 0x{0x44 + warmap.MISSION_MAP_ICON:02X}). "
                + ("SE's yellow boxed 'M', the MISSION icon (bound live "
                   "2026-09-12)." if warmap.MISSION_MAP_ICON == 1 else
                   "WARNING: NOT the bound mission icon (that is 1)."))
        return hits

    def log_order(self, payload):
        """Name every field of a 0x0194 ORDER the client sent, and keep it on
        the pilot (`orders`, the last ORDER_KEEP). The six dialog fields are
        UNBOUND: the first live order is the measurement that binds them.
        Returns the stored dict, or None."""
        b = bytes(payload[areatargets.ORDER_BODY:areatargets.ORDER_BODY + 0x14C])
        if len(b) < 0x148:
            log(f"{self.peer}   0x0194 = ORDER, only {len(payload)}B -- too "
                f"short for the 332-B body at +0x20; nothing decoded")
            return None
        u = {off: struct.unpack_from("<I", b, off)[0] for off in areatargets.ORDER_FIELDS}
        _nm = b[areatargets.ORDER_NAME:areatargets.ORDER_NAME + areatargets.ORDER_NAME_LEN].split(b"\0")[0]
        _cm = b[areatargets.ORDER_COMMENT:areatargets.ORDER_COMMENT + areatargets.ORDER_COMMENT_LEN].split(b"\0")[0]
        name = _nm.decode("cp932", "replace")
        comment = _cm.decode("cp932", "replace")
        char = self.playing_char() if charstore.CHAR_STORE else None
        src = missionbook.mission_find(char, u[0x004]) if char else None
        log(f"{self.peer}   0x0194 = ORDER (a derived mission), issuer "
            f"{name!r}, comment {comment!r}, from "
            + (f"accept key {u[0x004]} = {src.get('name')!r} (mission id "
               f"{src.get('id')}, category {src.get('cat')}, sector "
               f"{src.get('sector')})" if src else
               f"key {u[0x004]} (NOT one of this pilot's accepts)")
            + ". Fields: " + ", ".join(
                f"+0x{o:03X}={u[o]} [{areatargets.ORDER_FIELDS[o]}]" for o in sorted(u)
                if o != 0x004)
            + ". -> 0x0195 (empty): the client draws 21:27 'This mission has "
            "been ordered.' NOTHING is issued to other pilots yet -- bind "
            "the dialog fields from this line first.")
        rec = {"at": missionbook._mission_iso(), "key": u[0x004], "issuer": name,
               "comment": comment,
               "fields": {"0x%03X" % o: u[o] for o in sorted(u)}}
        if src:
            rec["from"] = {"id": src.get("id"), "name": src.get("name"),
                           "sector": src.get("sector"), "zone": src.get("zone")}
        if char is not None:
            prev = char.get("orders") if isinstance(char.get("orders"), list) else []
            char["orders"] = (prev + [rec])[-areatargets.ORDER_KEEP:]
            try:
                self.commit(f"ORDER from key {u[0x004]} kept on the pilot "
                            f"({len(char['orders'])} on file)")
            except Exception as _e:
                log(f"{self.peer}   WARNING: order NOT banked ({_e!r})")
        return rec

    def area_targets_reply(self, req, need, nation):
        """The 0x01A9 body for an Area Mission accept's 0x01A8, or None to
        keep the zeros (28:8). Logs what the picker will offer."""
        mid = struct.unpack_from("<I", req, 0)[0] if len(req) >= 4 else 0
        zone = rooms.WORLD_ZONES.get(self.ip)
        if not areatargets.AREA_TARGETS:
            log(f"{self.peer}   0x01A8 = AREA MISSION TARGET LIST for mission "
                f"{mid}: FMO_AREA_TARGETS=0 -> zeros, count 0 = 28:8 'There "
                f"was no target sector.' (what it has always said)")
            return None
        row = missionbook.mission_requirements().get(mid)
        if row is None or row.get("cat") != 3:
            log(f"{self.peer}   0x01A8 = AREA MISSION TARGET LIST for mission "
                f"{mid}: " + ("not a row we serve" if row is None else
                              f"row {row['name']!r} is category "
                              f"{row.get('cat')}, not 3")
                + " -> count 0 (28:8)")
            return None
        tiles = areatargets.area_target_tiles(zone, nation)
        body = areatargets.reply_01a9(tiles, need)
        _rows = fmosectors.SECTORS.get(int(zone or 0), {}) if fmosectors else {}
        _desc = []
        for t in tiles[:6]:
            s = areatargets.area_sector_state(t)
            _desc.append(f"Sector {_rows.get(t, ('?',))[0]} (tile {t}"
                         + (f", nation {s[0]} {s[1]}%" if s else ", no war record")
                         + ")")
        log(f"{self.peer}   0x01A8 = AREA MISSION TARGET LIST for {row['name']!r} "
            f"(id {mid}), zone {zone}, nation {nation} -> 0x01A9 count "
            f"{len(tiles)} at +0x20, ids 903,000,000 + tile from +0x24: "
            + (", ".join(_desc) + (" ..." if len(tiles) > 6 else "")
               if tiles else "NONE -- every sector with a battle map is "
               "already ours, or the zone has no table")
            + (". The client should draw 28:7 'Please select a target "
               "sector...' and open the war map as a picker." if tiles else
               ". The client draws 28:8."))
        return body

    def counter_mission_icon(self, tile, selector, mapno):
        """The ENEMY battle-map missions running on this sector's battle map
        (see COUNTER_MISSION): [] when off, when the viewer has no side, or
        when nobody of the other side is fighting there with an open mission
        for (selector, tile). Logs what the arrow stands for."""
        if not (warmap.COUNTER_MISSION and missionboard.MISSION_REPORT and tile):
            return []
        side, _ssrc = popnation.battle_side_for(self.ip)
        hits = popnation.enemy_missions_running(mapno, tile, selector, side)
        if hits:
            log(f"{self.peer}   COUNTER-MISSION: an enemy battle-map mission "
                f"is RUNNING on map {mapno} (tile {tile}, zone {selector}): "
                + ", ".join(f"{h} holds {m.get('name')!r} (id {m.get('id')})"
                            for h, m in hits)
                + f" -> row +0x{warmap.S15F_ICON:02X} = {warmap.COUNTER_MISSION} (sprite "
                f"0x{0x44 + warmap.COUNTER_MISSION:02X}). WARNING: Which of 2/3 is SE's "
                f"arrow (topics/050704/img/hangeki-ms.jpg) is UNOBSERVED.")
        return hits

    def counter_mission_settle(self, won, zone, tile, mapno, char):
        """SE's counter-mission bonus: this pilot WON on a battle map where an
        enemy battle-map mission was running. Owes FMO_COUNTER_BONUS_HS as a
        kind-2 Kill-bonus line (0 = log only). Returns the enemy hits."""
        if not (warmap.COUNTER_MISSION and missionboard.MISSION_REPORT and won and tile
                and char is not None):
            return []
        side, _ssrc = popnation.battle_side_for(self.ip)
        hits = popnation.enemy_missions_running(mapno, tile, zone, side)
        if not hits:
            return []
        _names = ", ".join(f"{m.get('name')!r} ({h})" for h, m in hits)
        if warmap.COUNTER_BONUS_HS > 0:
            owed = char.get("mission_pay")
            char["mission_pay"] = (owed if isinstance(owed, list) else []) + [{
                "id": 0, "name": "Counter-mission", "hs": int(warmap.COUNTER_BONUS_HS),
                "mp": 0, "at": int(servicerecord._now_unix()), "kind": warmap.PAY_KILL}]
            try:
                self.commit(f"counter-mission bonus H$ {warmap.COUNTER_BONUS_HS} owed "
                            f"for a win over {_names}")
            except Exception as _e:
                log(f"{self.peer}   WARNING: counter-mission bonus NOT banked ({_e!r})")
        log(f"{self.peer}   COUNTER-MISSION: WON on map {mapno} (tile {tile}, "
            f"zone {zone}) while the enemy's {_names} was running there -- "
            + (f"H$ {warmap.COUNTER_BONUS_HS} owed as a kind-{warmap.PAY_KILL} Kill bonus "
               f"line at the Personnel Officer (FMO_COUNTER_BONUS_HS; SE's "
               f"amount is not on the site, this one is OURS)."
               if warmap.COUNTER_BONUS_HS > 0 else
               "FMO_COUNTER_BONUS_HS=0, so nothing is owed; SE paid a "
               "反撃ミッションボーナス here, larger the sooner the intrusion."))
        return hits

    def mission_battle_settle(self, won):
        """Settle this pilot's open battle-map missions on a battle end (see
        mission_battle_apply). None when off or there is no pilot."""
        if not missionboard.MISSION_REPORT:
            return None
        if self.in_arena():
            # Playing Manual p.45: an Arena battle earns no MP, and a met
            # mission's share is MP -- so it settles no mission (FMO_ARENA_NO_PAY)
            log(f"{self.peer}   MISSION: an Arena battle (zone "
                f"{rooms.WORLD_ZONES.get(self.ip)}) settles no mission "
                f"(FMO_ARENA_NO_PAY; Playing Manual p.45)")
            return None
        char = self.playing_char() if charstore.CHAR_STORE else None
        if char is None:
            log(f"{self.peer}   WARNING: MISSION: battle ended "
                f"{'WON' if won else 'LOST'} but there is no pilot on this "
                f"connection -- no accepted mission can be settled.")
            return None
        cats = {mid: cat for mid, cat, _n in community.msn_row_table()}
        _sec = getattr(self, "sector", None)
        _tile = _sec[0] if _sec else None
        _zone = getattr(self, "sector_zone", None) or warmap.warmap_selector(self.ip)
        _nat, _nsrc = zoneentry.nation_for_session(char, status.STATUS_NATION,
                                                   "FMO_STATUS_NATION")
        _cat2_before = [m for m in missionbook.accepted_missions(char)
                        if m.get("cat") == 2 and missionbook.mission_status(m) == "open"]
        if won and _sec:
            # KEY: THE SECTOR-MISSION LEDGER: one win by this nation, here, now.
            # Every open category-2 accept on (zone, tile) -- this pilot's or
            # anyone's -- reads it through mission_status.
            sectorwins.sector_win_record(_zone, _tile, _nat)
            log(f"{self.peer}   SECTOR WINS: nation {_nat} ({_nsrc}) now has "
                f"{sectorwins.sector_wins_between(_zone, _tile, _nat, 0, servicerecord._now_unix())} "
                f"win(s) on (zone {_zone}, tile {_tile}) this process; a "
                f"category-2 accept there is met at its `needed` count.")
        self.counter_mission_settle(won, _zone, _tile,
                                    _sec[2] if _sec else None, char)
        moved = missionbook.mission_battle_apply(char, won, cats=cats, tile=_tile,
                                                 zone=_zone)
        for m in _cat2_before:
            if missionbook.mission_status(m) == "met":
                moved.append((m, "met (sector wins)"))
        _where = (f"in tile {_tile} (selector {_zone} sector {_sec[1]}, map "
                  f"{_sec[2]})" if _sec else
                  "with NO war-map sector (FMO_SORTIE_MAPNO / resume)")
        if not moved:
            _open = [m for m in missionbook.accepted_missions(char)
                     if missionbook.mission_status(m) == "open"]
            _else = [m for m in _open if int(m.get("sector") or 0)
                     and int(m["sector"]) != int(_tile or -1)]
            log(f"{self.peer}   MISSION: battle ended {'WON' if won else 'LOST'}"
                f" {_where}; no open battle-map mission to settle"
                + (f" -- {len(_else)} wait(s) for ANOTHER battlefield: "
                   + ", ".join(f"{m.get('name')!r} in tile {m['sector']}"
                               for m in _else) if _else else
                   f" ({len(_open)} open, none of category 1: sector and area "
                   f"missions are not judged by one battle)" if _open else "")
                + ".")
            return moved
        # KEY: THE MISSION SHARE BONUS (FMO_MISSION_SHARE). SE, guide/mission:
        # 「戦闘に参加したバトルグループのメンバー全員に「ミッション分配ボーナス」が
        # 支払われます」, paid by the Personnel Officer. A battle-map mission met
        # by THIS win owes every member of the taker's battle group one kind-3
        # line (11:3 "Mission participation bonus"); the taker's own rides the
        # commit below, the others' are written onto their own records.
        _shares = []
        if won and missionboard.MISSION_SHARE:
            _reqs = missionbook.mission_requirements()
            for m, s in moved:
                if s != "met" or m.get("cat") not in (None, 1):
                    continue
                e = missionbook.mission_share_entry(m, _reqs.get(int(m.get("id", -1))))
                if e is not None:
                    _shares.append(e)
        if _shares:
            prev = char.get("mission_pay")
            char["mission_pay"] = (prev if isinstance(prev, list) else []) + [
                dict(e) for e in _shares]
        try:
            self.commit("battle end settled mission(s): " + ", ".join(
                f"{m.get('id')} {m.get('name')!r} -> {s}" for m, s in moved)
                + (f"; mission share owed: " + ", ".join(
                    f"H$ {e['hs']}/MP {e['mp']}" for e in _shares) if _shares else ""))
        except Exception as e:
            log(f"{self.peer}   WARNING: mission settlement NOT banked ({e!r})")
        if _shares:
            from . import groupchannel, trade   # the group and its live sessions
            try:
                _me = self.account
            except AttributeError:
                _me = None                      # a bare test Session
            _gid = groupchannel.GROUP_OF.get(_me) if _me else None
            _others = [a for a in groupchannel.GROUP_MEMBERS.get(_gid, [])
                       if a and a != _me] if _gid else []
            _paid = []
            for _acct in _others:
                # a member with a LIVE session is written through it, so its
                # in-memory roster (which its next commit saves whole) holds
                # the line; anyone else is written on disk directly
                _live = next((s for s in list(trade.LIVE_SESSIONS.values())
                              if s is not self and getattr(s, "account", None) == _acct),
                             None)
                try:
                    if _live is not None:
                        _mc = _live.playing_char() if charstore.CHAR_STORE else None
                        _roster = None
                    else:
                        _roster = charstore.load_roster(_acct)
                        _mc = next((c for c in _roster if c.get("first") or c.get("last")),
                                   None)
                    if _mc is None:
                        log(f"{self.peer}   WARNING: MISSION SHARE: battle-group member "
                            f"{_acct} has no pilot on file -- not paid")
                        continue
                    _prev = _mc.get("mission_pay")
                    _mc["mission_pay"] = (_prev if isinstance(_prev, list) else []) + [
                        dict(e) for e in _shares]
                    _why = (f"mission share owed from {_me}'s win: "
                            + ", ".join(f"H$ {e['hs']}/MP {e['mp']}" for e in _shares))
                    if _live is not None:
                        _live.commit(_why)
                    else:
                        charstore.save_roster(_acct, _roster)
                    _paid.append(_acct)
                except Exception as _e:
                    log(f"{self.peer}   WARNING: MISSION SHARE for member {_acct} NOT "
                        f"banked ({_e!r})")
            log(f"{self.peer}   MISSION SHARE: "
                + "; ".join(f"{e['name']!r} H$ {e['hs']} / MP {e['mp']}" for e in _shares)
                + f" owed to the taker"
                + (f" and {len(_paid)} battle-group member(s) {_paid} (group {_gid})"
                   if _paid else " (no other battle-group member on file)")
                + f" as a kind-{missionboard.PAY_SHARE} paybook line at the Personnel "
                f"Officer. MP = the row's Distribution (+0x1C8), H$ = "
                f"{missionboard.MISSION_SHARE_HS_PCT:g}% of the reward "
                f"(FMO_MISSION_SHARE_HS_PCT, OURS). WARNING: paid to the whole "
                f"group; SE paid the members who fought, and this does not check "
                f"who sortied.")
        log(f"{self.peer}   MISSION: battle ended {'WON' if won else 'LOST'} -> "
            + "; ".join(f"{m.get('name')!r} (id {m.get('id')}) {s}"
                        for m, s in moved)
            + f", fought {_where}. KEY: A mission with a battlefield counts "
            "only there; one without (+0x1F4 = 0) still counts anywhere. Bar: "
            "Report draws 28:4 for a 'met' one.")
        return moved

    def war_settle(self, won):
        """Move the war state for the sector this connection sortied into
        (self.sector, set by the 0x015E that opened the war map). ONE call
        per battle end; a sortie with no sector (the FMO_SORTIE_MAPNO /
        resume path) moves nothing. A LOSS settles too: against NPCs it fills
        the enemy's counter (fmowar FMO_WAR_LOSS, AI/F00/D08 78: 「敗北する
        ことで制圧率が減少」). Returns the sector dict or None."""
        sector = getattr(self, "sector", None)
        if warstate.WAR == "0" or fmowar is None or not sector:
            return None
        if self.in_arena():
            # an arena battle is fought for no sector; a war-map sector this
            # session opened earlier must not be settled by it
            return None
        st = warstate.war_state()
        if st is None:
            return None
        if getattr(self, "war_settled", None) == sector:
            return None
        # SE (Map Selector help, AH/F98/D64 86): 「統制区は、戦局が安定しているため
        # セクターの制圧状況は変化しません」 -- in a Controlled Zone (zone kinds 1
        # and 3, selectors 1xx / 3xx) sector control never changes. Tiles are
        # shared between selectors, so settling one there moved a front tile.
        _zone = getattr(self, "sector_zone", None)
        if _zone is not None and int(_zone) // 100 in (1, 3):
            self.war_settled = sector
            log(f"{self.peer}   WAR STATE: selector {_zone} is a Controlled Zone -- "
                f"sector control does not change there; tile {sector[0]} untouched")
            return None
        self.war_settled = sector
        warstate._war_tick(st)
        char = self.playing_char() if charstore.CHAR_STORE else None
        n, src = zoneentry.nation_for_session(char, status.STATUS_NATION, "FMO_STATUS_NATION")
        tile = self.sector[0]
        # SE: a win over other PILOTS moves the front more than one over NPCs
        # (fmowar weight 2). Set when a hostile room-mate popped in this
        # battle (room_queue -> battle_room_hostile).
        pvp = bool((referee.BATTLE_STATE.get(self.battle_key()) or {}).get("pvp"))
        s, what = st.settle(tile, n, won=bool(won), pvp=pvp)
        log(f"{self.peer}   WAR STATE: tile {tile} (selector {zoneentry.MAPKIND} sector "
            f"{self.sector[1]}, map {self.sector[2]}), nation {n} ({src}) "
            f"{'WON' if won else 'LOST'}{' a PvP battle (counts double)' if pvp else ''}"
            f" -> {what}; the sector is now nation "
            f"{s['nation']} at {s['control']}% (counter {s['counter']}); "
            f"{st.summary()}. The war map sees it on its next kind-7 query "
            f"once FMO_WAR_MAP binds the fields.")
        return s

    def battle_end_push(self, conn_id, why="the FMO_BATTLE_END timer", won=None,
                        next_battle=False):
        """ONE 0x014C from the battle settlement (settle_battle), which is
        where the pay is banked -- this push only SHOWS it."""
        st = self.settle_battle(f"battle end ({why})", won=won)
        rows, old, new = st["exp_rows"], st["contrib_old"], st["contrib_new"]
        _won = st["won"]
        self.war_settle(_won)
        self.mission_battle_settle(_won)
        # HANGAR RANK (hangar.HANGAR_JOB_LEVEL): banked here, after the exp
        # above, and served in +0x0F5 -- the same value the next 0x014A serves.
        _hc = self.playing_char() if charstore.CHAR_STORE else None
        _hr, _hline, _hchanged = hangar.hangar_rank_at_battle_end(_hc)
        log(f"{self.peer}   {_hline}")
        if _hchanged:
            try:
                self.commit(_hline)
            except Exception as _e:
                log(f"{self.peer}   WARNING: {_hline} -- NOT banked ({_e!r}); the "
                    f"next 0x014A serves the old rank and the next battle end sets it again")
        pkt = battleend.battle_end_packet(conn_id, hangar_rank=_hr, contrib_new=new, contrib_old=old,
                                          exp_rows=rows, won=_won,
                                          victory=_won and bool(st["contribution"]),
                                          next_battle=next_battle,
                                          **(getattr(self, "platoon_end", None) or {}))
        log(f"{self.peer}   -> 0x{battleend.MSG_BATTLE_END:04X} BATTLE END push, "
            f"{battleend.S14C_LEN}B on queue seq 0x{pushes.QUEUE_SEQ:08X}, trigger: {why}: "
            f"{'WON' if _won else 'LOST'} "
            f"(+0x108 = {2 if _won else 0}), contribution "
            f"{old} -> {new} (+0x0EC / +0x0E8), experience rows {rows or 'none'}, "
            f"+0x0F0 = 1 so the arm copies the block and transitions. Gate: "
            f"[lobby+0x20]==4 and [lobby+0x24] not in {{0,3}}; +0x24 == 5 "
            f"skips the block. Bar: the reward lines, then the client is back "
            f"in the lobby with NO 0x013D on the wire.")
        return pkt

    def penalty_report_push(self, conn_id):
        """The FRIENDLY-FIRE report (0x017B) for this pilot's battle, or None.
        Sent just BEFORE the 0x014C: its arm 0x6117ECE4 only acts under the
        battle gate 0x611734E0, and after the battle the client asks 11:9 per
        row (0x61191E80). See penalty.report_push."""
        try:
            return penalty.report_push(self, conn_id)
        except Exception as e:           # a report must never cost the battle end
            log(f"{self.peer}   WARNING: 0x{penalty.MSG_PENALTY_REPORT:04X} not built ({e!r})")
            return None

    def on_penalty_give(self, p):
        """0x017C: YES on 11:9 -- one penalty point for the pilot at +0x00.
        Fire-and-forget (0x61174480 queues it and reads no reply)."""
        return penalty.on_give(self, p)

    def battle_result_push(self, conn_id, occasion, won=None):
        """A 0x015A RESULT push, or None when FMO_RESULT_PUSH is off.

        This is the only thing on this server that pays a player for playing.
        The deltas are applied to the CHARACTER STORE as well as sent, so the
        numbers survive the disconnect and come back in the next 0x014A --
        which is the difference between a probe and a game, and is the whole
        point of PLAN M1.

        WARNING: UNPROVEN END TO END. The wire half is read off the client's own arm
        (0x6117E94F) and the store half is the same fold-and-commit
        `playtime_seconds` uses, but no launch has confirmed either. Order
        matters and is deliberate: the store is written FIRST, so a push that
        the dispatcher silently drops still leaves a pilot who was paid --
        visible on the next login even if nothing changed on screen now. That
        turns an invisible failure into a distinguishable one, which is the
        only way this gets debugged without a live client.
        """
        if not resultpush.RESULT_PUSH:
            return None
        if not (resultpush.RESULT_MONEY or resultpush.RESULT_CONTRIB or battleend.BATTLE_END_CONTRIB
                or battleend.KILL_CONTRIB or battleend.WIN_MONEY or battleend.WIN_CONTRIB):
            log(f"{self.peer}   FMO_RESULT_PUSH=1 but FMO_RESULT_MONEY, "
                f"FMO_RESULT_CONTRIB / FMO_BATTLE_END_CONTRIB and the "
                f"performance knobs (FMO_KILL_CONTRIB, FMO_WIN_MONEY, "
                f"FMO_WIN_CONTRIB) are all 0 -- a result that pays nothing is "
                f"indistinguishable on screen from one that never arrived, so "
                f"nothing is sent. Set one of them.")
            return None
        record = getattr(self, "last_0159", b"")
        # ONE settlement per sortie (settle_battle): banked there, once,
        # whether 0x014C or this push asks first.
        st = self.settle_battle(f"battle result ({occasion})", won=won)
        _money, _contrib = st["money"], st["contribution"]
        if not (_money or _contrib):
            log(f"{self.peer}   0x{resultpush.MSG_RESULT_PUSH:04X} not sent: this battle "
                f"pays nothing ({'WON' if st['won'] else 'LOST'}, "
                f"{len(st['kills'])} kill(s)); the 0x014C still shows it.")
            return None
        if not st["banked"]:
            log(f"{self.peer}   WARNING: the push below therefore moves the display "
                f"only and will NOT survive a relog.")
        # WARNING: The owned table (+0x598, 1344 B) lands on lobby+0x8C8 BEFORE the
        # arm looks at anything else, and lobby+0x8C8..+0xE08 is exactly the
        # 0x014A's payload+0x3C..+0x57C -- the owned items AND the kind-11
        # script flag bitmap at lobby+0xB88. Every push before 2026-09-11
        # sent zeros there and WIPED the pilot's flags: the 0x0150 re-entry
        # then ran the first-login tutorial again (the LEV walk gates on a
        # flag bit) and the settled lobby came back with byte 128 != 99, so
        # no counter would talk and the menu stayed greyed. Serve the same
        # slice the login's 0x014A served for this pilot.
        _char = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
        owned = status.reply_014a(char=_char)[status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN]
        pkt = resultpush.result_push_packet(conn_id, record=record,
                                            money=_money,
                                            contribution=_contrib,
                                            owned=owned, pilot=_char)
        _fl = owned[status.S14A_FLAGS11 - status.S14A_OWNED:
                    status.S14A_FLAGS11 - status.S14A_OWNED + status.S14A_FLAGS11_LEN]
        _nz = ", ".join("%d=%#04x" % (i, _fl[i]) for i in range(len(_fl))
                        if _fl[i])
        _nz = _nz or ("NONE -- the flags bitmap the client holds will be "
                      "ZEROED by this push")
        log(f"{self.peer}   0x{resultpush.MSG_RESULT_PUSH:04X} +0x{resultpush.S15A_OWNED:03X} owned "
            f"table = the pilot's 0x014A payload+0x{status.S14A_OWNED:03X} slice "
            f"({len(owned)}B -> lobby+0x8C8, copied by the arm BEFORE the "
            f"deltas); script flag bytes carried: {_nz}")
        log(f"{self.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} BATTLE RESULT push, "
            f"{resultpush.S15A_BODY_LEN}B on queue seq 0x{pushes.QUEUE_SEQ:08X} after "
            f"{occasion}: money delta {_money:+d} (payload+0x410 -> "
            f"lobby+0x88C), contribution delta {_contrib:+d} "
            f"(payload+0x414 -> lobby+0xFC8), "
            + (f"+0xAD8 = 0, so the client's own {len(record)}B 0x0159 record "
               f"goes back into lobby+0x6E4E with only the fields above "
               f"authored over it (the deltas and the row counts live INSIDE "
               f"that 1,432 B, at +0x004..+0x41A -- the rest is echoed "
               f"untouched because we cannot author what we have not decoded)"
               if record else
               "no 0x0159 record captured this session, so +0xAD8 = 1 and "
               "lobby+0x6E4E is left alone -- the pay still applies, the arm "
               "checks that byte LAST")
            + ".")
        log(f"{self.peer}   WARNING: ORACLE: the money on screen must move by exactly "
            f"{_money:+d} AND still be there after a relog. A wrong id or "
            f"length is dropped at 0x6117F86D in SILENCE -- 'we sent it' is not "
            f"a result, the number on screen is.")
        return pkt


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    areatargets, battleend, charstore, classes, community, inventory, missionblock,
    missionboard,
    missionbook, popnation, progress, pushes, referee, resultpush, rooms, sectorwins, servicerecord, status, warmap,
    warstate, zoneentry,
)
from . import battlegroups, charlist  # noqa: E402  (platoon_battle_settle)
from . import hangar  # noqa: E402  (hangar rank at the battle end)
from . import penalty  # noqa: E402  (the friendly-fire report and vote)
from . import loot, squad  # noqa: E402  (loot_battle_settle)
