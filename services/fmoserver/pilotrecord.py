"""Session's pilot record: money, class experience, flags, passes, salary, the ceasefire bonus
and the officer review."""
import time
from .deps import fmostore
from .wirelog import log


class SessionPilotRecord:
    """The pilot record half of Session: what a pilot has and is owed."""

    def credit_class_exp(self, why, rows):
        """Bank class experience: rows are (kind, amount). Returns the new
        {kind: exp} or None when there is no pilot. Same commit path as money."""
        char = self.playing_char() if charstore.CHAR_STORE else None
        if char is None:
            log(f"{self.peer}   WARNING: {why}: no pilot to attribute class experience "
                f"to -- nothing banked; the 0x014C rows still move the screen.")
            return None
        cur = classes.class_exp_of(char)
        for kind, amount in rows:
            cur[int(kind)] = cur.get(int(kind), 0) + int(amount)
        char["class_exp"] = {str(k): v for k, v in sorted(cur.items())}
        try:
            self.commit(f"{why}: class exp "
                        + ", ".join(f"{classes.CLASS_NAMES.get(k, k)} +{a} -> {cur[k]} "
                                    f"(Lv{classes.class_level(cur[k])})" for k, a in rows))
        except Exception as e:
            log(f"{self.peer}   WARNING: {why}: class exp NOT banked ({e!r})")
        return cur

    def accept_mission(self, mid, name, fee, reward=None, sector=None,
                       zone=None, cat=None, nation=None, wins=None,
                       control=None):
        """Record an accepted mission on the played character. Returns the new
        list, or None when there is no pilot. `zone` = the selector the
        battlefield tile lives in, `cat` the row's list, `nation` the pilot's,
        `wins` the category-2 target -- each stored only when given.

        Same commit path as the money -- and deliberately called AFTER the
        charge, so a fee that could not be banked cannot leave an accept behind
        claiming it was paid."""
        char = self.playing_char() if charstore.CHAR_STORE else None
        if char is None:
            log(f"{self.peer}   WARNING: accept of mission {mid} NOT recorded: no "
                f"pilot on this connection. The client shows it accepted; "
                f"nothing here survives the disconnect.")
            return None
        cur = missionbook.accepted_missions(char)
        _new = {"id": int(mid), "name": str(name), "fee": int(fee),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if reward:
            # Snapshotted AT ACCEPT, so re-authoring a row later cannot change
            # what an already-accepted mission pays.
            _new["reward_hs"], _new["reward_mp"] = int(reward[0]), int(reward[1])
        if sector:
            # The mission's BATTLEFIELD, snapshotted like the reward: only a
            # battle fought in this ARE tile can complete it.
            _new["sector"] = int(sector)
        if zone:
            _new["zone"] = int(zone)         # ...in THIS zone's grid
        if cat is not None:
            _new["cat"] = int(cat)
        if nation:
            _new["nation"] = int(nation)
        if cat == 2 and wins:
            _new["needed"] = int(wins)
        if cat == 3 and control:
            _new["control_needed"] = int(control)
        cur = missionbook.mission_prune(cur)
        _key = missionbook.mission_next_key(cur, mid)
        if _key is not None:
            _new["key"] = _key              # record+0x00 for THIS accept
        # a STORY mission (catalogue title or tile) also moves its own flag
        # byte 0 -> 1, as the NPC's srv_104 would (missionbook.story_accept_apply);
        # committed with the accept, pushed by the caller
        _sm, _swhat = missionbook.story_accept_apply(char, name, sector, nation)
        if _sm is not None:
            _new["story_byte"] = int(_sm["own"])
        log(f"{self.peer}      story mission: {_swhat}")
        cur = cur + [_new]
        char["missions"] = cur
        try:
            self.commit(f"accepted mission {mid} {name!r}"
                        + (f", fee {fee} MP" if fee and missionboard.MISSION_FEE_MP else f", fee H$ {fee}" if fee else "")
                        + (f", {_swhat}" if _sm is not None else "")
                        + f" -- {len(cur)} accepted mission(s) on file")
        except Exception as e:
            log(f"{self.peer}   WARNING: accept of mission {mid} NOT banked ({e!r})")
            return None
        return cur

    def credit_money(self, why, money=0, contribution=0):
        """Apply deltas to the played character's stored economy. Returns the
        new (money, contribution), or None when there is no pilot.

        KEY: ONE DEFINITION of "the player now has this much", shared by the
        battle result and the item counter -- these are the only two things on
        this server that pay anybody, and they must not drift apart on what
        counts as banked. Never raises: a store that will not write must not
        cost the client its reply, but it MUST say so, because a silent
        non-write is indistinguishable from a write that worked until the
        player relogs and the number is gone."""
        char = self.playing_char() if charstore.CHAR_STORE else None
        if char is None:
            log(f"{self.peer}   WARNING: {why}: no pilot to attribute it to "
                f"(storeless connection or an unnamed roster) -- nothing "
                f"banked, and nothing here will survive the disconnect.")
            return None
        # KEY: RESOLVE THE OPENING BALANCE THE SAME WAY 0x014A DISPLAYS IT.
        # `_econ_value` falls back to FMO_STATUS_MONEY when the record carries
        # no number, so a pilot with no stored money is SHOWN 12,345 and allowed
        # to spend it -- while this used to read the same absence as 0 and
        # subtract from there. That is how the test laptop's pilot bought a
        # 10 H$ weapon and landed at -10 on 2026-09-12: two defaults for one
        # field. The display's answer is the authoritative one, because it is
        # the number the client's own purchase gate was comparing against.
        was = (economy.wallet_money(char)[0],
               economy._econ_value("contribution", None, status.STATUS_CONTRIB,
                                   "FMO_STATUS_CONTRIB", char)[0])
        # WARNING:KEY: A NEGATIVE BALANCE IS A ONE-WAY TRAP, so it is floored HERE, at
        # the single definition, rather than at each caller. 0x014A packs money
        # with `& 0xFFFFFFFF`, so a stored -3 reaches the client as 0xFFFFFFFD
        # and its own shop gate `0x611785D0` compares it SIGNED
        # (`cmp eax,[esp+8]; jge`) -- so once the balance goes below zero that
        # pilot can never buy anything again, not even an item priced 0. Seen
        # live 2026-09-12: the test laptop's pilot sat at -3 and the free
        # insignias were refused. New pilots start at FMO_STATUS_MONEY, which
        # defaults to 0, so one debit is all it takes.
        _want = was[0] + int(money)
        char["money"] = max(0, _want)
        char["contribution"] = max(0, was[1] + int(contribution))
        if _want < 0:
            log(f"{self.peer}   WARNING: {why}: this debit would have left "
                f"{_want} -- FLOORED AT 0. The client has already taken the "
                f"full amount off its own display, so the two disagree until "
                f"the next 0x014A puts ours back on screen. A pilot reaching "
                f"here means something charged more than they had: the price "
                f"is the client's own figure, so look at what was bought.")
        now = (char["money"], char["contribution"])
        try:
            self.commit(f"{why}: money {was[0]} -> {now[0]}, contribution "
                        f"{was[1]} -> {now[1]}")
        except Exception as e:
            log(f"{self.peer}   WARNING: {why}: NOT banked ({e!r}). The in-memory "
                f"record has it, the disk does not -- so the numbers are right "
                f"on screen now and gone at the next login. Do not read the "
                f"screen as proof of persistence.")
        return now

    def pilot_flags(self):
        """The 256 progress-flag bytes this pilot holds -- the store's, else
        the same slice the 0x014A would serve -- or None with no pilot."""
        pc = self.playing_char() if charstore.CHAR_STORE else None
        if not pc:
            return None
        fl = b""
        if fmostore and pc.get("flags"):
            try:
                fl = fmostore.flags_bytes(pc.get("flags")) or b""
            except Exception:
                fl = b""
        if not fl:
            fl = status.reply_014a(char=pc)[status.S14A_FLAGS11:status.S14A_FLAGS11 + status.S14A_FLAGS11_LEN]
        return bytes(fl)

    def pilot_trained(self):
        """False only for a stored pilot whose byte 128 != 99; a storeless
        connection has nothing to gate on."""
        fl = self.pilot_flags()
        return True if fl is None else classes.trained(fl)

    def all_rosters(self):
        """[(account, roster)] across the whole store -- this session's live
        roster first, every other account's from disk."""
        out = [(self.account, self.roster)]
        if fmostore:
            try:
                for a in fmostore.store_accounts():
                    if a != self.account:
                        out.append((a, fmostore.load_roster(a)))
            except Exception as e:
                log(f"{self.peer}   WARNING: name check: other accounts unreadable "
                    f"({e!r}) -- checking this account only")
        return out

    def name_taken(self, first, last, char_id):
        return charstore.name_clash(self.all_rosters(), first, last, self.account, char_id)

    def spend_area_pass(self, char, zone):
        """Take one transit pass off the pilot when opening `zone` cost one.

        Mirrors the client's own verdict (area_permit_cost at the tier
        0x611E4000 makes of the pilot's class-12 level, permits.area_tier): a
        free move spends nothing. Returns the spent serials ([] or [serial])
        for the live 0x015A push.
        """
        nation, _nsrc = zoneentry.nation_for_session(char, status.STATUS_NATION, "FMO_STATUS_NATION")
        rows = zonecontrol.parse_zone_control(zonecontrol.ZONE_CONTROL) if zonecontrol.ZONE_CONTROL else []
        tier, _lv = permits.pilot_area_tier(char)
        cost, why = permits.area_permit_cost(zone, nation, tier, rows)
        why += f" (Pilot level {_lv})"
        if cost != 1:
            log(f"{self.peer}   area {zone}: no pass spent ({why}; 2 = the "
                f"client moved freely, 0 = it should not have offered it)")
            return []
        it = permits.area_pass_item(char, zone)
        if it is None:
            log(f"{self.peer}   area {zone} cost a permit ({why}) but the "
                f"pilot holds no pass for zone kind {int(zone) // 100} -- "
                f"nothing to spend (the client should have refused it)")
            return []
        inventory.remove_stored_item(char, it["serial"])
        log(f"{self.peer}   area {zone} cost a permit ({why}): spent pass id "
            f"{it['id']} serial 0x{it['serial']:016X}; "
            f"{sum(1 for x in inventory.stored_items(char) if x['kind'] == permits.PASS_KIND)} "
            f"pass(es) left")
        return [it["serial"]]

    def grant_hq_pass(self, conn_id, rank=None):
        """Mint the pilot's starter SECTOR TRANSIT PASS -- once, ever -- and,
        with FMO_PERMIT_RANKS, the passes the pilot's `rank` has earned.

        The Personnel Officer's script already tells the player he is handing
        one over (D64 215/216 + 217); SE's server backed that with an item and
        ours never did, so the promise was empty and Change Area had nothing
        to spend. Returns the 0x016B mint push, or None when there is nothing
        to give (knob off, no pilot, unknown nation, already held).
        """
        if not permits.PERMIT and not permits.PERMIT_RANKS:
            return None
        char = self.playing_char() if charstore.CHAR_STORE else None
        if not char:
            log(f"{self.peer}   FMO_PERMIT is on but there is no pilot in the "
                f"store to give a pass to -- nothing minted.")
            return None
        nation, src = zoneentry.nation_for_session(char, status.STATUS_NATION, "FMO_STATUS_NATION")
        wanted = (sorted(permits.PASS_ZONE_KIND) if permits.PERMIT_ALL
                  else ([permits.PASS_HQ[nation]] if nation in permits.PASS_HQ and permits.PERMIT
                        else []))
        # FMO_PERMIT_RANKS: "by promotion to a certain rank" (SE, news4567)
        _by_rank = permits.permit_rank_passes(nation, rank)
        wanted = wanted + [w for w in _by_rank if w not in wanted]
        item_id = wanted[0] if wanted else None
        if item_id is None and permits.PERMIT_RANKS and rank is not None:
            log(f"{self.peer}   FMO_PERMIT_RANKS={permits.PERMIT_RANKS_RAW!r}: rank "
                f"{rank} ({ranks.rank_name(rank)}) has earned no pass yet.")
            return None
        if item_id is None:
            log(f"{self.peer}   FMO_PERMIT: nation {nation} ({src}) is neither "
                f"1 nor 2, so there is no HQ pass to mint.")
            return None
        # A grant is once per pilot, ever: a pass the client has CONSUMED to
        # open an area is gone from the item list but stays in GRANTED_KEY, so
        # the next visit does not refill it (the re-mint bug before 09-30).
        given = permits.granted_passes(char)
        wanted = [w for w in wanted if w not in given]
        if not wanted:
            return None                      # already granted; say nothing
        recs, names = [], []
        for _w in wanted:
            lo, hi = shop.mint_serial()
            inventory.add_stored_item(char, lo | (hi << 32), _w, permits.PASS_KIND, 0)
            recs.append(inventory.item_record(lo | (hi << 32), _w, permits.PASS_KIND))
            names.append("id %d -> zone kind %d%s" % (
                _w, permits.PASS_ZONE_KIND[_w],
                " (FMO_PERMIT_RANKS, rank %s)" % rank if _w in _by_rank else ""))
        char[permits.GRANTED_KEY] = sorted(given | set(wanted))
        try:
            self.commit(f"Personnel Officer granted transit pass(es) "
                        f"(kind 0x{permits.PASS_KIND:02X} ids {wanted})")
        except Exception as e:
            log(f"{self.peer}   WARNING: the pass was NOT banked ({e!r}) -- the client "
                f"will hold it until it relogs and then it is gone.")
        log(f"{self.peer}   -> 0x{shop.MSG_ACQUIRE_REPLY:04X} MINT push on queue seq "
            f"0x{pushes.QUEUE_SEQ:08X}: {len(recs)} pass(es) of kind 0x{permits.PASS_KIND:02X} "
            f"[{', '.join(names)}] (nation {nation}: {src}). The officer's own "
            f"script says he grants this (D64 215/216 + 217); until today we "
            f"sent nothing, so no pilot ever held one. Bar: chat says 8:2 "
            f"'Obtained ...' and the pass is in the item list after a relog. "
            f"WARNING: FIRST TIME THIS PUSH HAS EVER BEEN SENT -- if the client dies, "
            f"set FMO_PERMIT=0 and say so.")
        return packet.build(shop.MSG_ACQUIRE_REPLY, shop.item_mint_payload(recs), pushes.QUEUE_SEQ, conn_id)

    def grant_reward_pass(self, conn_id, item_id, why):
        """Mint ONE transit pass `item_id` (kind 0x13) as a mission reward and
        bank it; -> the 0x016B mint push, or None (no pilot). Unlike the HQ
        pass this is not once-ever: SE's helicopter report hands over an OC
        pass each time the mission is reported, and a mission reports once."""
        char = self.playing_char() if charstore.CHAR_STORE else None
        if not char:
            log(f"{self.peer}   {why}: no pilot in the store, pass id {item_id} NOT minted")
            return None
        lo, hi = shop.mint_serial()
        inventory.add_stored_item(char, lo | (hi << 32), item_id, permits.PASS_KIND, 0)
        char[permits.GRANTED_KEY] = sorted(permits.granted_passes(char) | {item_id})
        try:
            self.commit(f"{why}: transit pass id {item_id} (kind 0x{permits.PASS_KIND:02X})")
        except Exception as e:
            log(f"{self.peer}   WARNING: {why}: the pass was NOT banked ({e!r})")
        log(f"{self.peer}   -> 0x{shop.MSG_ACQUIRE_REPLY:04X} MINT push: {why}, pass id "
            f"{item_id} -> zone kind {permits.PASS_ZONE_KIND.get(item_id)}")
        return packet.build(shop.MSG_ACQUIRE_REPLY,
                            shop.item_mint_payload([inventory.item_record(lo | (hi << 32), item_id,
                                                                          permits.PASS_KIND)]),
                            pushes.QUEUE_SEQ, conn_id)

    def sell_hangar_pass(self, conn_id, payload, ps):
        """The hangar mechanic's permit sale (script event 206, see
        permits.HANGAR_SALE_EVENT) -> the packets to send BEFORE the ack.

        A sale banks the debit and the pass, then answers the script with
        p2 = 1 through a 0x015A (record answered, money delta -price, owned
        table + flags refreshed), followed by the 0x016B mint that puts the
        pass in the item list -- the same order and the same two pushes a
        mission report's pass reward already uses. A refusal sends nothing:
        the ack echoes the record with p2 = 0 and the script prints its one
        refusal line."""
        char = self.playing_char() if charstore.CHAR_STORE else None
        choice = int(ps[0]) if ps else 0
        if char is None:
            log(f"{self.peer}   hangar permit sale (event 206, choice {choice}): no "
                f"pilot in the store -- refused; the script will say 'not enough money'")
            return []
        nation, _nsrc = zoneentry.nation_for_session(char, status.STATUS_NATION, "FMO_STATUS_NATION")
        sale = permits.hangar_sale(choice, nation, economy.wallet_money(char)[0])
        if not sale["ok"]:
            log(f"{self.peer}   hangar permit sale REFUSED: {sale['why']} -- the script "
                f"prints D94 103 'not enough money' whatever the reason")
            return []
        why = f"hangar permit sale: {sale['why']}"
        if self.credit_money(why, money=-sale["price"]) is None:
            return []
        mint = self.grant_reward_pass(conn_id, sale["pass_id"], why)
        params = list(ps) + [0] * (scriptcall.S159_NPARAMS - len(ps))
        params[1] = 1
        owned = bytearray(status.reply_014a(char=char)[
            status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN])
        fl = self.pilot_flags()
        if fl:
            o = status.S14A_FLAGS11 - status.S14A_OWNED
            owned[o:o + status.S14A_FLAGS11_LEN] = fl[:status.S14A_FLAGS11_LEN]
        outs = [resultpush.result_push_packet(
            conn_id, record=scriptcall.answered_0159(payload, params),
            money=-sale["price"], contribution=0, owned=bytes(owned), pilot=char)]
        log(f"{self.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} ANSWER push: event 206 "
            f"p2 = 1 (D94 102 'This is your transit permit'), money delta "
            f"-{sale['price']} H$, then the 0x{shop.MSG_ACQUIRE_REPLY:04X} pass mint")
        if mint:
            outs.append(mint)
        return outs

    def pay_salary(self, char, rank, now=None):
        """The paybook this visit pays, BANKED: money and MP credited to the
        pilot, `last_payday` moved to today. (rows, H$, MP, paydays); a
        connection with no pilot pays nothing and shows nothing."""
        if not char:
            return [], 0, 0, 0
        rows, today = servicerecord.paybook_rows(char, rank, now)
        # one header + one base row per payday, and a city row when it applies
        days = sum(1 for r in rows if r[0] == servicerecord.PAY_BASE)
        # KEY: 28:4 says "The reward will be paid by the Personnel.Officer" --
        # so a REPORTED mission's reward is paid HERE, as its own "Mission
        # bonus" line (kind 5), in whatever room the 20-row book has left.
        _mrows, _mpaid = missionbook.mission_pay_rows(char, rows)
        rows = rows + _mrows
        tm = sum(r[3] for r in rows)
        tmp = sum(r[4] for r in rows)
        if not rows:
            return rows, 0, 0, 0
        if days:
            char["last_payday"] = today
        why = (f"salary: {days} payday(s) at rank {rank} {ranks.rank_name(rank)} "
               f"({ranks.rank_pay(rank)[0]} H$ + {ranks.rank_pay(rank)[1]} MP each)"
               + (f" + {len(_mpaid)} mission reward(s): "
                  + ", ".join(f"{e.get('name')!r} H$ {e.get('hs', 0)} / MP "
                              f"{e.get('mp', 0)}" for e in _mpaid)
                  if _mpaid else ""))
        if tmp:
            _mp_was = int(economy._econ_value("mp", None, status.STATUS_MP, "FMO_STATUS_MP",
                                              char)[0] or 0)
            char["mp"] = _mp_was + tmp
        if tm:
            self.credit_money(why, money=tm)      # commits
        else:
            try:
                self.commit(why + " -- MP only")
            except Exception as e:
                log(f"{self.peer}   WARNING: {why}: NOT banked ({e!r})")
        return rows, tm, tmp, days

    def stored_money(self):
        """(money, contribution) from the played character, or None when there
        is no pilot to read -- the stores in 0x01A1 / 0x01C5 / 0x01A7 rewrite
        lobby+0x88C, so 'unknown' must mean 'do not send', never 0."""
        char = self.playing_char() if charstore.CHAR_STORE else None
        if char is None:
            return None
        # Floored: see reply_014a. A negative balance must never reach a
        # comparison, here or on the wire.
        return (economy.wallet_money(char)[0],
                max(0, int(char.get("contribution") or 0)))

    def fee_push(self, conn_id, cost):
        """The 0x01A1 sortie-cost debit, banked first. None when nothing can
        be sent honestly."""
        now = self.credit_money("sortie cost", money=-int(cost))
        if now is None:
            log(f"{self.peer}   WARNING: FMO_SORTIE_COST={cost}: no stored wallet to "
                f"debit, so no 0x{pushes.MSG_FEE_PUSH:04X} -- its +0x00 is a STORE and "
                f"a guessed balance would rewrite the client's money.")
            return None
        log(f"{self.peer}   -> 0x{pushes.MSG_FEE_PUSH:04X} FEE push, {pushes.S1A1_LEN}B on "
            f"queue seq 0x{pushes.QUEUE_SEQ:08X}: money := {now[0]} (payload+0x00 -> "
            f"lobby+0x88C, a store), sortie cost {cost} at +0x04 with +0x08 = "
            f"0 -> 8:74 \"Paid H$%d as the sortie cost for a modified unit\". "
            f"Bar: the Profile's H$ drops by {cost} and is still down after "
            f"a relog.")
        return pushes.fee_push_packet(conn_id, money=now[0], sortie_cost=cost)

    def insignia_push(self, conn_id, group_id, insignia):
        """The 0x01C5 that follows a stored insignia registration."""
        mc = self.stored_money()
        if mc is None:
            log(f"{self.peer}   WARNING: FMO_INSIGNIA_PUSH=1 but the wallet is unknown "
                f"(no pilot in the store): 0x{squadron.MSG_SQUADRON_INSIGNIA:04X} NOT "
                f"sent -- its +0x0C is a money STORE.")
            return None
        log(f"{self.peer}   -> 0x{squadron.MSG_SQUADRON_INSIGNIA:04X} SQUADRON INSIGNIA "
            f"push, {squadron.S1C5_LEN}B on queue seq 0x{pushes.QUEUE_SEQ:08X}: group "
            f"{group_id} -> its 0x613C1551 slot's +0x0A := {insignia}, money "
            f":= {mc[0]} (+0x0C -> lobby+0x88C, unconditional). Bar: row 7 "
            f"\"Set squadron insignia\" greys WITHOUT reopening the menu.")
        return packet.build(squadron.MSG_SQUADRON_INSIGNIA,
                            squadron.squadron_insignia_push_body(group_id, insignia, mc[0]),
                            pushes.QUEUE_SEQ, conn_id)

    def officer_review(self, char, now=None):
        """SE's periodic review above Captain (REVIEW). Runs at the Personnel
        Officer: the first visit starts the period, a visit after
        FMO_REVIEW_DAYS judges it and starts the next. Changes char["rank"]
        (and, on a demotion, "contribution") and commits. Returns the verdict
        or None."""
        if servicerecord.REVIEW == "0" or not char:
            return None
        now = int(servicerecord._now_unix(now))
        rank = int(economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char)[0] or 0)
        if not servicerecord.RANK_CAPTAIN <= rank <= servicerecord.RANK_COLONEL:
            return None
        since = char.get("review_at")
        if not isinstance(since, int):
            char["review_at"] = now
            self.commit(f"officer review: period opened for "
                        f"{ranks.rank_name(rank)} ({servicerecord.REVIEW_DAYS} days, FMO_REVIEW_DAYS)")
            return None
        if now - since < servicerecord.REVIEW_DAYS * servicerecord.DAY:
            return None
        wins = servicerecord.review_successes(char, rank, since, now)
        slot = True
        cap = servicerecord.REVIEW_CAPS.get(rank + 1)
        if cap is not None:
            nat = zoneentry.nation_for_session(char, status.STATUS_NATION, "FMO_STATUS_NATION")[0]
            held = sum(1 for _a, ro in self.all_rosters() for c in ro
                       if int(c.get("rank") or 0) == rank + 1
                       and zoneentry.nation_for_session(c, None, "")[0] == nat)
            slot = held < cap
        verdict = servicerecord.review_verdict(rank, wins, servicerecord.REVIEW, slot_free=slot)
        char["review_at"] = now
        what = ("sector" if rank <= servicerecord.RANK_CAPTAIN else "area") + " mission(s)"
        if verdict == "promote":
            char["rank"] = rank + 1
        elif verdict == "demote":
            char["rank"] = rank - 1
            char["contribution"] = servicerecord.review_demoted_contribution(rank - 1)
        self.commit(f"officer review: {ranks.rank_name(rank)}, {wins} {what} in "
                    f"{servicerecord.REVIEW_DAYS} days (promote at {servicerecord.REVIEW_PROMOTE}"
                    + ("" if slot else f"; {ranks.rank_name(rank + 1)} is FULL")
                    + f") -> {verdict.upper()}"
                    + (f" to {ranks.rank_name(char['rank'])}" if verdict != "keep" else ""))
        return verdict

    def pay_ceasefire(self, char):
        """Owe every judged phase's ceasefire bonus this pilot has not had
        (CEASEFIRE): a kind-7 paybook line for the H$/MP, the contribution
        banked now. Returns [(phase, (H$, contribution, MP))]."""
        if not servicerecord.CEASEFIRE or not char:
            return []
        war = warstate.war_state()
        if war is None:
            return []
        warstate._war_tick(war)
        had = isinstance(char.get("ceasefire_paid"), list)
        owed = servicerecord.ceasefire_owed(char, war.data.get("phases"))
        if not had:
            self.commit(f"ceasefire: {len(char['ceasefire_paid'])} phase(s) "
                        f"already judged recorded as paid (no back-pay)")
        if not owed:
            return []
        rank = int(economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char)[0] or 0)
        nat = zoneentry.nation_for_session(char, status.STATUS_NATION, "FMO_STATUS_NATION")[0]
        out = []
        for n, rec in owed:
            b = servicerecord.ceasefire_bonus(rank, nat, rec)
            char.setdefault("ceasefire_paid", []).append(n)
            if b is None:
                continue
            hs, contrib, mp = b
            if hs or mp:
                prev = char.get("mission_pay")
                char["mission_pay"] = (prev if isinstance(prev, list) else []) + [{
                    "id": 0, "name": f"Ceasefire bonus, phase {n}", "hs": hs,
                    "mp": mp, "at": int(servicerecord._now_unix()), "kind": servicerecord.PAY_CITY}]
            if contrib:
                self.credit_money(f"ceasefire bonus, phase {n}",
                                  contribution=contrib)
            out.append((n, b))
        self.commit("ceasefire bonus: " + ("; ".join(
            f"phase {n}: H$ {b[0]}, contribution {b[1]}, MP {b[2]}"
            for n, b in out) or "nothing owed at this rank"))
        return out

    def owe_kill_bonus(self, hs, kills):
        """Owe one paybook "Kill bonus" line (kind PAY_KILL) for this sortie's
        kills; the Personnel Officer pays it with the rest of the book."""
        char = self.playing_char() if charstore.CHAR_STORE else None
        if char is None:
            log(f"{self.peer}   WARNING: Kill bonus H$ {hs} for {kills} kill(s) NOT "
                f"owed: no pilot on this connection.")
            return
        owed = char.get("mission_pay")
        char["mission_pay"] = (owed if isinstance(owed, list) else []) + [{
            "id": 0, "name": "Kill bonus", "hs": int(hs), "mp": 0,
            "at": int(servicerecord._now_unix()), "kind": warmap.PAY_KILL}]
        try:
            self.commit(f"Kill bonus H$ {hs} owed for {kills} kill(s) "
                        f"(FMO_KILL_BONUS_HS)")
        except Exception as e:
            log(f"{self.peer}   WARNING: Kill bonus NOT banked ({e!r})")


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    charstore, classes, economy, inventory, missionboard, missionbook, packet, permits, pushes,
    ranks, resultpush, scriptcall, servicerecord, shop, squadron, status, warmap, warstate,
    zonecontrol, zoneentry,
)
