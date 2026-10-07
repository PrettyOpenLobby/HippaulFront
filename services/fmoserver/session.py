"""Session: one client's TCP connection, its cipher and the on_packet dispatcher for every lobby
message."""
import os
import struct
import time
from .deps import contentauth, fmomsn, fmostore
from .wirelog import hexdump, log
from . import coliseum, pilotrecord, pvproom, resume, settlement, sortie, trade, wirelog


# --------------------------------------------------------------------------- #
# the conversation
# --------------------------------------------------------------------------- #
class Session(
        sortie.SessionSortie,
        pilotrecord.SessionPilotRecord,
        settlement.SessionSettlement,
        resume.SessionResume,
        trade.SessionTrade,
        coliseum.SessionColiseum,
):
    """One client. `conn_id` is what we hand it in the handshake reply."""

    _next_conn = 0

    def __init__(self, peer):
        self.peer = peer
        #: The address alone. Only used to carry an identity from the LOGIN
        #: connection to the GAME connection -- see the character store note.
        self.ip = peer.rsplit(":", 1)[0]
        self._account = None
        self._roster = None
        #: The character id 0x0130 Start Game selected -- the one whose
        #: setups the garage messages operate on. 0 until a Start Game lands.
        self.playing = 0
        #: KEY: (tile, row, battle map) of the war-map SECTOR this connection
        #: last asked about with 0x015E, so the sortie that follows can serve
        #: THAT sector's battlefield. None until the war map is opened -- and
        #: the sortie falls back to FMO_SORTIE_MAPNO, which is what every
        #: sortie used before 2026-09-08 and what a kycli RESUME still uses.
        self.sector = None
        Session._next_conn = (Session._next_conn + 1) & 0xFFFF or 1
        self.conn_id = Session._next_conn
        self.seq = packet.SEQ_MIN
        #: The +0x10 of the request being answered. Replies echo it; see
        #: reply_seq(). Not a counter -- an independent one matches by accident.
        self.req_seq = packet.SEQ_MIN
        self.version = None
        self.handshake_sent = False
        #: KEY: WHICH BUILD IS ON THE SOCKET. Set from the 0x0065 version string,
        #: which BOTH the login and the game connection send, so it is known
        #: before any 0x0153. 'ps2jp_050324_1531' = the console, 'verwinjp_*'
        #: the PC. It is not cosmetic: the two builds disagree about message
        #: sizes (0x0321 is 52 bytes on the console, 80 on the PC) and about
        #: which maps their install ships -- see MAPNO_PS2.
        #: Have we already decided whether this connection is the POL lobby
        #: protocol or the community/mission server? One decision per
        #: connection, taken on the first whole frame -- see serve_client.
        self.identified = False
        #: Set once we push message 1. From then on an ENCRYPTED packet is the
        #: expected outcome, not a fault -- say which, so the log does not read
        #: like a bug when the model is working.
        self.expect_encrypted = False
        #: The two RC4 streams, armed at the moment the client arms its pair.
        #: Separate objects: our TX pairs with the client's RX and vice versa.
        #: Mode 1 makes each stream conversation-long, so they desynchronise
        #: PERMANENTLY if one packet is processed out of order, skipped, or
        #: encrypted at the wrong moment. There is no resync.
        self.tx = None
        self.rx = None
        #: Key tail to arm with once the CURRENT batch of replies has actually
        #: been WRITTEN TO THE SOCKET. See arm_cipher for why this is deferred.
        self.pending_arm = None
        #: Set by arm_cipher when content auth defers keying: our 0x0322 tail,
        #: waiting for resolve_key() to find the prefix on the first packet in.
        self.key_tail = None
        #: The last (group ids, our answer) we logged for 0x01AC. The client
        #: polls that message ~141 times a session, so the squadron lines are
        #: emitted only when something CHANGES -- otherwise the one line worth
        #: reading is buried under 140 copies of itself.
        self.squadron_seen = None
        #: {POL group id: class} for this member, read once from the account database.
        #: See the pol_groups property -- it is the squadron table's litter
        #: filter, not a convenience.
        self._pol_groups = None
        #: WARNING: WHEN THIS CONNECTION'S PLAY-TIME CLOCK STARTED, or None while it
        #: is not running. None UNTIL A 0x0130 START GAME LANDS ON THIS SOCKET,
        #: and that gate is the whole design: `serve_client` is entered by the
        #: LOGIN connection, the GAME connection and the community/mission
        #: connection alike, so a clock armed at CONNECT would bank the same
        #: wall-clock seconds two or three times over and Play Time would run
        #: at 2x-3x. Only the game connection sends Start Game.
        #: MONOTONIC, not wall clock: a wall-clock delta goes negative across
        #: an NTP step, and the client's arithmetic on the value is a SIGNED
        #: idiv.
        self.play_mark = None

    def arm_cipher(self, tail4):
        """Both directions, same 20-byte key, independent streams.

        WARNING: MUST be called after the arming batch is SENT, never while building it.
        on_packet() returns a list and the caller transmits afterwards, so arming
        inside on_packet() enciphers the very packets that have to go out in the
        clear -- which is a bug this file shipped once: both the 0x0322 reply and
        message 1 went out encrypted to a client that had not armed yet. Built
        and sent are different moments; only sent matters here.

        WITH CONTENT AUTH ON (2026-09-27) the streams are NOT built here: the
        16-byte prefix is whatever our lobby minted on this member's 4:5, and
        which member that is is the thing we are trying to learn. So only the
        tail is kept, and resolve_key() builds both streams from the client's
        first encrypted packet. Nothing is lost by waiting: after message 1 the
        server sends nothing until the client speaks.
        """
        self.expect_encrypted = True
        if packet.CONTENT_AUTH and contentauth is not None:
            self.key_tail = tail4
            self.tx = self.rx = None
            return
        self.tx = packet.RC4(packet.session_key(tail4))
        self.rx = packet.RC4(packet.session_key(tail4))

    def resolve_key(self, pkt):
        """Key the streams from the client's FIRST encrypted packet and, when the
        prefix is a minted one, adopt the member it was minted for. Returns the
        decrypted packet, or None if no candidate opens it (the caller drops the
        connection, exactly as for any undecryptable packet)."""
        tail4, self.key_tail = self.key_tail, None
        cands = [(v, m) for v, m, _ in
                 contentauth.candidates(self.ip, zone=packet.CONTENT_ID)]
        cands.append((None, None))            # the legacy zero prefix, last
        got = packet.trial_key(pkt, tail4, cands)
        if got is None:
            log(f"{self.peer} WARNING: content auth: NONE of {len(cands)} key "
                f"candidate(s) (minted values + the zero prefix) opens the first "
                f"encrypted packet -- the key model is wrong for this client")
            return None
        rc, prefix, member, plain = got
        self.rx = rc                          # already advanced past this packet
        self.tx = packet.RC4(packet.session_key(tail4, prefix))
        if member is None:
            log(f"{self.peer}   content auth: the client keyed with the ZERO "
                f"prefix -- no minted value reached it (no 4:5 zone "
                f"{packet.CONTENT_ID} since its POL login, or FMO_CONTENT_AUTH/"
                f"POL_CONTENT_AUTH_ZONES off); the account stays "
                f"{self._account or identity.account_for(self.ip)} (token/address)")
            return plain
        acct = f"member:{member}"
        was = self._account
        self._account = acct
        with identity._store_lock:
            identity._proven_by_ip[self.ip] = (acct, time.monotonic())
            identity._identity_by_ip[self.ip] = (acct, time.monotonic())
        rank = next(i for i, (v, _) in enumerate(cands) if v == prefix)
        log(f"{self.peer}   KEY: content auth: key prefix {prefix.hex()[:8]}.. "
            f"(candidate {rank + 1} of {len(cands)}) was minted for {acct} -- "
            f"PROVEN by the client's own checksum, not by address"
            + ("" if was in (None, acct) else
               f". WARNING: OVERRIDES {was} (the token/address guess was WRONG)"))
        return plain

    @property
    def account(self):
        if self._account is None:
            self._account = identity.account_for(self.ip)
        return self._account

    def wire_self_id(self):
        """The UnitID this client will believe is its own: the WIRE character
        id it selected in 0x0130 (CHAR_WIRE_BASE), else None to keep the fixed
        FMO_UDP_POP id in the 0x0153 setup block."""
        sel = getattr(self, "playing", 0)
        if charlist.CHAR_WIRE_BASE and sel and sel >= charlist.CHAR_WIRE_BASE:
            return sel
        return None

    def battle_key(self):
        """The BATTLE_STATE key for this pilot -- the SAME key its world
        channels use (chan_bkey): the account when a world channel is bound to
        it, else the address, exactly as before binding existed."""
        ip = getattr(self, "ip", None)
        try:
            acct = self.account
        except Exception:                    # a bare test session: no account
            return ip
        if acct and any(getattr(c, "account", None) == acct
                        for c in list(groupchannel.WORLD_PEERS.values())):
            return acct
        return ip

    @property
    def roster(self):
        if self._roster is None:
            self._roster = charstore.load_roster(self.account)
        return self._roster

    def commit(self, what):
        charstore.save_roster(self.account, self._roster)
        log(f"{self.peer}   STORED: {what} -- account {self.account}, "
            f"{len(self._roster)} character(s) now on file")

    def find(self, char_id):
        char_id = charlist.from_wire(char_id)          # the client names the WIRE id
        for c in self.roster:
            if c["id"] == char_id:
                return c
        return None

    def playtime_seconds(self, bank=True):
        """Total seconds of play time for the pilot on this socket.

        Banked in the character store under `play_seconds`. That key gets no
        column: fmostore keeps every name it does not know in the JSON `extra`
        blob and hands it straight back, so this needs no schema change and no
        migration -- a pilot with no `play_seconds` reads as 0, which is the
        honest answer for one created before the accounting existed.

        KEY: FOLD-AND-MOVE, not add. Every call adds the seconds since the last
        bank and then MOVES the mark, so a player who opens Play Time twice
        does not get the same seconds counted twice -- which is the shape of
        bug that makes a play-time counter run at 2x and look like a units
        mistake. The fractional remainder stays behind the mark on purpose.

        A connection whose clock never started (`play_mark is None` -- the
        login socket, the community socket, or a game socket that reached the
        lobby without a 0x0130) contributes ZERO seconds and banks nothing. It
        still REPORTS the banked total, so Play Time answers correctly on such
        a socket; it just under-counts that sitting rather than double-counting
        every other one.

        With FMO_PLAYTIME=0 nothing is banked and nothing is written; the
        answer is FMO_PLAYTIME_BASE alone.
        """
        if not move.PLAYTIME:
            return move.PLAYTIME_BASE
        elapsed = (0 if self.play_mark is None
                   else int(max(0.0, time.monotonic() - self.play_mark)))
        char = self.playing_char() if charstore.CHAR_STORE else None
        if char is None:
            # No pilot to attribute it to (storeless connection, or a roster
            # with nothing named yet). Report the session honestly and bank
            # nothing -- there is nowhere to put it.
            return move.PLAYTIME_BASE + elapsed
        total = int(char.get("play_seconds") or 0) + elapsed
        if bank and elapsed:
            # Both of these happen whether or not the WRITE succeeds, and they
            # happen together: the in-memory record now owns those seconds, so
            # the mark has to move past them or the next call adds them again.
            char["play_seconds"] = total
            self.play_mark += elapsed
            try:
                self.commit(f"play time {total}s (+{elapsed}s banked)")
            except Exception as e:
                # A store that will not write must not cost the client its
                # answer: the number is already computed and correct for this
                # request, it just will not survive the disconnect.
                log(f"{self.peer}   WARNING: play time NOT banked ({e!r}) -- the "
                    f"reply below is still right, but these {elapsed}s are "
                    f"lost when the connection closes")
        return move.PLAYTIME_BASE + total

    @property
    def pol_groups(self):
        """{POL group id: class} for the groups this member is a CONFIRMED
        member of (accounts.member_groups). Cached per session.

        KEY: This is what tells a real group id in the `0x01AC` table apart from
        the stack litter next to it -- see `reply_01ad`. `group_member` is
        keyed by HANDLE, so it joins through `handle.member_id`; the account
        this session resolved to is `member:<id>` (`member_for_ip`).

        Never raises: a DB fault must not break an FMO login. It IS logged --
        a lookup that quietly does not run is indistinguishable from one that
        ran and found nothing, and here the difference decides whether we
        annotate anything at all."""
        if self._pol_groups is not None:
            return self._pol_groups
        self._pol_groups = {}
        acct = self.account or ""
        if not identity.MEMBER_LOOKUP or not acct.startswith("member:"):
            return self._pol_groups
        try:
            mid = int(acct.split(":", 1)[1])
        except ValueError:
            return self._pol_groups
        try:
            acc, db = identity.accounts_conn()
            try:
                # [(group_id, class, formed_at)]
                rows = acc.member_groups(db, mid)
            finally:
                db.close()
        except Exception as e:
            log(f"{self.peer}   WARNING: POL group lookup for {acct} failed ({e!r}) "
                f"-- NO slot of the squadron table can be vouched for, so none "
                f"is annotated. FMO_SQUADRON_GROUPS overrides this by hand.")
            return self._pol_groups
        # The group's own formation time = the EARLIEST group_member row for
        # it, i.e. the owner's -- there is no `group` table, only members.
        # It feeds slot +0x0C, the client's "Formed on".
        self._pol_groups = {int(g): {"class": int(c),
                                     "formed": squadron._epoch(t)}
                            for g, c, t in rows}
        return self._pol_groups

    def log_squadron_request(self, payload):
        """KEY: THE MEASUREMENT: what the client actually put in its `0x01AC`.

        The four POL group ids at payload +0x00/+0x10/+0x20/+0x30 come from the
        client's own seeder `0x611BA320` -> `[0x613AE380]+0x648`, i.e. straight
        out of the POL group system. So this line answers, with no build and no
        knob, the question the whole Squadron menu hangs on: **is the POL side
        handing this client a group at all?**

        VERIFIED: ANSWERED LIVE 2026-09-09: **yes, and it is slot 0 only.** Member 3
        (Lex) at 127.0.0.1 sent `[0]=3`, and POL group **3** is a real row in
        accounts.db `group_member` -- nine members, Lex at class 5. Slots 1..3
        held stack litter that changed between polls (see `reply_01ad`). So
        "all four zero" is NOT the state we are in; the reply is the gap.

        Deduped: only logged when the ids or our answer change (the client polls
        0x01AC ~141 times a session)."""
        ids, mine = squadron.parse_01ac(payload)
        groups = self.pol_groups
        _body, why = squadron.reply_01ad(payload, groups,
                                         char=(self.playing_char() if charstore.CHAR_STORE else None))
        if (ids, mine, why) == self.squadron_seen:
            return
        self.squadron_seen = (ids, mine, why)
        shown = ", ".join(f"[{i}]=0x{g:016X}" for i, g in enumerate(ids))
        log(f"{self.peer}   0x01AC = the SQUADRON TABLE poll. The client sent "
            f"its own POL group ids: {shown}, my-slot-index={mine}")
        if not any(ids):
            log(f"{self.peer}      WARNING: ALL FOUR SLOTS ARE EMPTY. That is the "
                f"client's own seeder reporting NO POL GROUPS, so the Squadron "
                f"List has nothing to filter and 'Change squadron' can never "
                f"activate anything. Look at the POL group system (login "
                f"side), not at this reply.")
        else:
            hit = [i for i, g in enumerate(ids) if g and g in groups]
            log(f"{self.peer}      this member's POL groups (accounts."
                f"member_groups): {sorted(groups) or 'NONE'} -> slot(s) {hit} "
                f"carry a real one; the rest is the seeder's stack litter "
                f"(0x611BA320 scatters 8 dwords of a 32-byte local that "
                f"[0x613AE380]+0x648 only partly wrote).")
        log(f"{self.peer}   -> 0x01AD squadron table: {why}")

    def grant_nation(self):
        """(nation, source) a 0x0153 grant should be shaped by: what this
        session put in 0x014A +0x30 if it sent one, else the played character
        now, else the global knob -- so the zone band and the script nation
        can never disagree within one session."""
        got = getattr(self, "session_nation", None)
        if got and got[0] is not None:
            return got
        char = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
        return zoneentry.nation_for_session(char, status.STATUS_NATION,
                                            "FMO_STATUS_NATION (global knob)")

    def playing_char(self):
        """The character the garage messages operate on: the Start Game pick,
        else the first NAMED character (a fresh connection can reach the
        garage without a 0x0130 on this socket), else None."""
        c = self.find(self.playing) if self.playing else None
        if c is None:
            named = [x for x in self.roster
                     if x.get("first") or x.get("last")]
            c = named[0] if named else None
        return c

    def remember_zone(self, mapkind, mapno, why):
        """Store the zone (and lobby map) a 0x0153 just put this pilot in, on
        the pilot's own record -- the `mapkind`/`mapno` columns fmostore has
        carried since 09-08 and nothing ever wrote. The Viewer's FMO content
        profile reads its Zone (prof_004 slot 8, 1..6) as `mapkind // 100`
        (responders._content_game_fields), so until this every pilot's Zone
        was unset. Only a MapKind in the client's own bands is kept, and only
        a change is written. Returns True when it stored."""
        if not charstore.CHAR_STORE or mapkind is None or not zoneentry.in_mapkind_band(int(mapkind)):
            return False
        pc = self.playing_char()
        if pc is None or (pc.get("mapkind") == int(mapkind)
                          and pc.get("mapno") == mapno):
            return False
        was = pc.get("mapkind")
        pc["mapkind"] = int(mapkind)
        pc["mapno"] = mapno
        self.commit(f"zone {was} -> {int(mapkind)} (MapNo {mapno}, {why}); the "
                    f"FMO content profile's Zone now reads {int(mapkind) // 100}")
        return True

    def setups_block(self):
        """The 8-setup garage block this session would serve in 0x0166.

        KEY: SINGLE SOURCE ON PURPOSE. The inventory (0x0133) has to contain the
        very serials the setups (0x0166) equip -- the client joins them by the
        64-bit serial and an equipped serial with no inventory entry renders
        "-Nothing-". Deriving both from this one function is what stops them
        drifting apart; `--selftest` pins the invariant.
        Precedence is the garage's own: a client-authored 0x0167 save beats the
        synthesized starter."""
        pc = self.playing_char() if charstore.CHAR_STORE else None
        char = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
        nat, _src = popnation.character_nation(char)
        if charlist.NATION:
            nat = charlist.NATION
        if pc and pc.get("setups"):
            stored = bytes.fromhex(pc["setups"])
            if len(stored) == inventory.REPLY_0166_LEN:
                return inventory.fill_paint(stored, nat)[0]
        parts, _why = inventory.setup_parts_for(nat, char.get("cls"))
        return inventory.reply_0166(parts=parts, nation=nat)

    @property
    def is_ps2(self):
        """Is the PlayStation 2 build on this socket? From its own 0x0065."""
        return bool(self.version) and self.version.lower().startswith("ps2")

    def build_mapno(self, mn, why):
        """The MapNo to actually serve THIS build. Last word before 0x0153.

        A console session takes MAPNO_PS2, because the map the PC renders may
        be one the console's install has no file for -- and that renders as an
        empty scene the player can still walk around in. `why` names where the
        PC-side value came from, so the log says what was overridden."""
        if (not self.is_ps2 or not zoneentry.PS2_GATE or not zoneentry.MAPNO_PS2
                or mn in zoneentry.PS2_VALID_MAPNOS):
            return mn
        log(f"{self.peer}   WARNING: PS2 GATE: MapNo {mn} is not in FMO_PS2_MAPNOS "
            f"{zoneentry.PS2_VALID_MAPNOS} -- real hardware CRASHES loading a map it has "
            f"no file for (PCSX2 only draws a void). FMO_PS2_GATE=0 de-gates.")
        log(f"{self.peer}   MapNo {mn} -> {zoneentry.MAPNO_PS2}: this is the PS2 build "
            f"({self.version}), and {why} is the PC's map set. FMO_MAPNO_PS2 "
            f"picks what the CONSOLE ships"
            + ("" if mn in zoneentry.VALID_MAPNOS else
               f" (WARNING: {mn} is not in VALID_MAPNOS either)")
            + ". The console's set is read off midas.pex.")
        return zoneentry.MAPNO_PS2

    def ps2_mapkind_refusal(self, mk):
        """None when THIS build has a zone pack for MapKind `mk`, else why not.

        The caller decides what to do with it: a Change Area names the zone the
        player PICKED, so that one refuses (the client's own error arm); a world
        entry or Move carries a zone WE chose, so build_mapkind() substitutes."""
        if (not self.is_ps2 or not zoneentry.PS2_GATE or mk is None
                or int(mk) in zoneentry.PS2_VALID_MAPKINDS):
            return None
        return (f"PS2 GATE: zone {mk} has no type-3 pack on this build "
                f"({self.version}) -- the console ships "
                f"{len(zoneentry.PS2_VALID_MAPKINDS)} of the PC's 1000 "
                f"(FMO_PS2_MAPKINDS), and a missing resource is the same "
                f"0x611250A2 crash a missing map is")

    def build_mapkind(self, mk, why):
        """The MapKind to actually serve THIS build, for the grants whose zone
        comes from our own config rather than from the player's pick."""
        no = self.ps2_mapkind_refusal(mk)
        if no is None:
            return mk
        log(f"{self.peer}   WARNING: {no}. {why} -> {zoneentry.MAPKIND_PS2} "
            f"(FMO_MAPKIND_PS2). WARNING: THIS SHOULD NOT FIRE: every band we grant "
            f"is in the console's set, so a zone that lands here means the "
            f"zone table and the gate disagree -- fix the table, not this.")
        return zoneentry.MAPKIND_PS2

    def ps2_type1_refusal(self, mn):
        """None when THIS build may be sent to type-1 (battle) map `mn`, else
        the reason. There is no substitute for a battle map -- the sector picked
        it -- so a console is REFUSED rather than redirected (FMO_PS2_TYPE1)."""
        if (not self.is_ps2 or not zoneentry.PS2_GATE or zoneentry.PS2_TYPE1 is None
                or mn is None or mn in zoneentry.PS2_TYPE1):
            return None
        return (f"PS2 GATE: type-1 battle map {mn} is not one of the "
                f"{len(zoneentry.PS2_TYPE1)} in FMO_PS2_TYPE1 and this is the PS2 build "
                f"({self.version}) -- the console has no file for it "
                f"(hddata.pos + midas.pex 0x0038b7e0) and real "
                f"hardware crashes on a missing map. FMO_PS2_TYPE1=all de-gates")

    def reply_seq(self):
        """The correlation id a reply must carry: the REQUEST'S +0x10.

        Not a counter of ours. 0x61199E30 discards any packet whose +0x10 does
        not equal the handle the client is waiting on, and that handle is the
        sequence the client stamped on its own request. An independent counter
        matches only by accident -- which is exactly what happened, and why the
        0x0322 reply worked while message 1 was dropped.
        """
        return self.req_seq

    def apply_charsel(self, msg, payload, char_id):
        """Actually perform what these messages ask for.

        Returns None when it worked, or a reason string when it did not -- the
        caller turns that into a real failure reply rather than answering
        "done" for something that did not happen.
        """
        char_id = charlist.from_wire(char_id)          # store ids from here on
        if msg == 0x013E and len(payload) >= 0x3C:
            rec = charstore.character_from_013e(payload)
            rec["id"] = charlist.from_wire(rec["id"])
            # WARNING: COMPLETE the record the NAME step already made -- do not append.
            # Creation is TWO messages against ONE slot: 0x0177 carries the names
            # (and upserts the row), then the wanzer pick sends 0x013E with the
            # full record for the SAME id. `next_free_id` saw id 1 taken by that
            # first row and minted id 2, so one creation produced TWO identical
            # pilots. Measured live 2026-08-23 05:37:46 -> 05:38:00:
            #     STORED: created id 1 via the name step, Lex Arden
            #     STORED: created id 2 Lex Arden, nation 1, class 3 ... 2 on file
            # The client only ever thinks about the slot it picked, so the id in
            # the payload is authoritative when it names a row we hold.
            existing = self.find(rec["id"])
            # The names ride this message too. The name step (0x0177) has
            # already judged them when it made the row; judge them here when
            # they are new or changed (a client that skips the name step), so
            # a second "Henry Viduka" cannot come in through the side door.
            if existing is None or ((existing.get("first"), existing.get("last"))
                                    != (rec["first"], rec["last"])):
                _code, _why = charstore.name_refusal(
                    rec["first"], rec["last"],
                    self.name_taken(rec["first"], rec["last"], rec["id"]))
                if _code is not None:
                    self.fail_code = _code
                    return _why
            # FMO_START_MONEY: the first message that names the nation
            # (+0x28) is this one, so the head-count gap is judged here.
            _counts = (charselect.nation_counts(self.all_rosters())
                       if economy.START_MONEY is not None else None)
            if existing is not None:
                existing.update(rec)
                _money = economy.apply_start_money(existing, rec.get("nation_byte"), _counts)
                self.commit(f"completed id {rec['id']} {rec['first']} "
                            f"{rec['last']}, nation {rec['nation']}, class "
                            f"{rec['cls']}, hangar pw {rec['hangar_pw']}"
                            + (f"; starting money {existing['money']} ({_money})"
                               if _money else ""))
            else:
                rec["id"] = charstore.next_free_id(self.roster, rec["id"])
                economy.seed_new_character(rec)
                _money = economy.apply_start_money(rec, rec.get("nation_byte"), _counts)
                self._roster.append(rec)
                self.commit(f"created id {rec['id']} {rec['first']} "
                            f"{rec['last']}, nation {rec['nation']}, class "
                            f"{rec['cls']}, hangar pw {rec['hangar_pw']} -- "
                            f"starting {economy.seed_summary(rec)}"
                            + (f" ({_money})" if _money else ""))
        elif msg == 0x0177 and len(payload) >= 0x26:
            first = charstore._name_at(payload, 0x04)
            last = charstore._name_at(payload, 0x15)
            _code, _why = charstore.name_refusal(
                first, last,
                self.name_taken(first, last, char_id) if charstore.NAME_UNIQUE else None)
            if _code is not None:
                self.fail_code = _code
                return _why
            c = self.find(char_id)
            if c is None:
                # WARNING: THE NAME STEP OF CREATION, not a rename. The client is
                # naming a slot it holds -- the synthetic LIST_NAME entry that
                # FMO_LIST_COUNT serves while the stored roster is empty -- which
                # we never persisted. Failing find() here returned FM00001 and
                # trapped the player one step before the wanzer pick, retrying
                # every ~12s (measured 2026-08-22). So UPSERT: create the record
                # now, which both makes message 1 a truthful success and lets the
                # character survive the next login. 0x0177 carries only id and the
                # two names; nation / class / appearance / hangar password ride
                # 0x013E or 0x01AA and update THIS same record when they arrive.
                # WARNING: NOT under `raw`: _gender_byte / _look_field /
                # character_nation fall back to raw[+0x26..+0x33] for records
                # that predate the named keys, and a 0x0177 carries ONLY the id
                # and two names -- everything past +0x26 is whatever the
                # client's buffer held. A creation abandoned before its 0x013E
                # would otherwise wear buffer litter as its face and nation.
                c = economy.seed_new_character(
                    {"id": char_id, "first": first, "last": last, "nation": 0,
                     "sex": 1, "cls": 1, "hangar_pw": 0,
                     "appearance": "00000000", "raw_0177": payload.hex()})
                self._roster.append(c)
                self.commit(f"created id {char_id} via the name step, "
                            f"{first} {last} -- starting {economy.seed_summary(c)}")
            else:
                c["first"] = first
                c["last"] = last
                self.commit(f"renamed id {char_id} to {first} {last}")
        elif msg == 0x013F:
            c = self.find(char_id)
            if c is None:
                return (f"delete for id {char_id}, which is not in this "
                        f"account's roster")
            _locked = charselect.delete_locked(c, time.time())
            if _locked:
                self.fail_code = charselect.DELETE_LOCK_CODE
                return f"delete for id {char_id} refused: {_locked}"
            self._roster.remove(c)
            self.commit(f"deleted id {char_id} ({c.get('first', '')} "
                        f"{c.get('last', '')})")

    def on_packet(self, p):
        """Return a list of packets to send back."""
        # Every reply in this batch must echo THIS request's +0x10, or the
        # client's poll (0x61199E30) discards it and the state never advances.
        self.req_seq = p["seq"]

        if p["msg"] == handshake.MSG_VERSION:
            # payload = u32 0, then the version string in a 36-byte field
            self.version = p["payload"][4:].split(b"\x00")[0].decode(
                "ascii", "replace")
            log(f"{self.peer}   client version {self.version!r}")
            if self.version.startswith("W20_2026"):
                log(f"{self.peer}   WARNING: that stamp is OURS -- "
                    f"the W20-0004 patch mirror's latest_version, not SE's")
            if not handshake.SEND_HANDSHAKE_OK:
                log(f"{self.peer}   NOT answering the version -- the client does "
                    f"not wait for that reply, and it JAMS its single receive "
                    f"slot (FMO_SEND_HANDSHAKE_OK=1 to send it anyway)")
                return []
            if self.handshake_sent:
                return []
            self.handshake_sent = True
            # +0x06 = 2 and a connection id at +0x08 is what 0x61199c79 latches.
            # The payload it wants alongside is NOT known; empty is the first try.
            log(f"{self.peer}   -> handshake-ok, assigning conn=0x{self.conn_id:04X}")
            return [packet.build(handshake.MSG_HANDSHAKE_OK, b"", self.reply_seq(), self.conn_id)]

        if p["msg"] == timesync.MSG_LOCAL_HELLO:
            # See MSG_LOCAL_HELLO: the launcher-less boot's game hello. Same
            # poll as 0x015B (message 1 or 0x0184 on the request's sequence),
            # but NO key set follows it on the client, so no cipher arm here.
            _who = struct.unpack_from("<I", p["payload"], 0)[0]                 if len(p["payload"]) >= 4 else 0
            log(f"{self.peer}   0x{timesync.MSG_LOCAL_HELLO:04X} = the NON-POL game hello "
                f"([0x613CC494] boot string EMPTY -- this client was not "
                f"launched through PlayOnline), id 0x{_who:08X}. -> message 1 "
                f"on its sequence; NOT arming the cipher (0x61179F83 zeroes the "
                f"key block on this path). First sighting ever if you are "
                f"reading this on prod.")
            return [packet.build(handshake.MSG_SESSION_START, b"", p["seq"], p["conn"])]

        if p["msg"] == handshake.MSG_GAME_HELLO:
            log(f"{self.peer}   GAME hello 0x015B (payload {p['payload'].hex()}) "
                f"-- this connection is the GAME, not the POL login")
            # The payload IS field A of our own 0x0322 coming back.
            tok = struct.unpack_from("<I", p["payload"], 0)[0] \
                if len(p["payload"]) >= 4 else 0
            named = charstore.account_for_token(tok, ip=self.ip, consume=True)                 if tok else None
            self.field_a = tok
            if named:
                self._account = named
                log(f"{self.peer}   token 0x{tok:08X} names account {named} "
                    f"-- correlated by the client's own echo, not by address")
            elif tok:
                log(f"{self.peer}   WARNING: token 0x{tok:08X} is not one we minted "
                    f"(or it expired) -- falling back to the address")
            log(f"{self.peer}   -> message 1, the reply state 2 polls for; "
                f"arming the cipher once it is on the wire")
            self.pending_arm = packet.SERVER_KEY_TAIL
            return [packet.build(handshake.MSG_SESSION_START, b"", self.reply_seq(), p["conn"])]

        if p["msg"] == handshake.MSG_START_GAME:
            sel = struct.unpack_from("<I", p["payload"], 0)[0]                 if len(p["payload"]) >= 4 else 0
            self.playing = sel
            # THE PLAY-TIME CLOCK STARTS HERE, and only here -- see the
            # `play_mark` note in Session.__init__ for why arming it at
            # CONNECT would count every sitting two or three times.
            if self.play_mark is None:
                self.play_mark = time.monotonic()
            log(f"{self.peer}   START GAME: selected list id {sel} "
                f"(payload {p['payload'][:8].hex()}); play-time clock armed")
            _fa = getattr(self, "field_a", 0)
            if sel and _fa:
                log(f"{self.peer}   Battle Review: recorder/viewer folder "
                    f"/btlreview/{_fa:x}/ (field A), Start Game check "
                    f"/btlreview/{sel:x}/ (selected id) -- "
                    + ("MATCH, a recorded review survives the relog" if _fa == sel
                       else "MISMATCH, this session's review will not be "
                            "offered after a relog"))
            if sel:
                charstore.note_played(self.account, charlist.from_wire(sel))
            if status.SERVE_START_STATUS:
                # PLAN 1.5: the PLAYED character (Start Game just set
                # self.playing), so a per-character stored economy reaches
                # 0x014A. Falls back to roster[0], then {} -- the old behaviour
                # for a storeless or unnamed connection.
                _char = ((self.playing_char() or (self.roster or [{}])[0])
                         if charstore.CHAR_STORE else {})
                # The SCRIPT nation, per pilot (FMO_NATION_PER_CHARACTER).
                _snat, _snat_src = zoneentry.nation_for_session(
                    _char, None, "FMO_STATUS_NATION")
                self.session_nation = (_snat, _snat_src)
                _fields = status.status_fields(char=_char, nation=_snat,
                                               nation_src=_snat_src)
                if _snat is not None:
                    log(f"{self.peer}   0x014A nation byte (+0x30 -> "
                        f"lobby+0x8B4) = {_snat} from {_snat_src}")
                log(f"{self.peer}   -> 0x014A, {status.REPLY_014A_LEN}B: the player "
                    f"STATUS block, rank at payload+0x{status.S14A_RANK:X} "
                    f"(-> lobby+0x8BB; the value and its source are on the "
                    f"per-field line below, NOT the FMO_RANK knob). Briefing "
                    f"Room also needs MapKind 509 -- FMO_MAPKIND is {zoneentry.MAPKIND}")
                # One line per non-zero field, naming its SOURCE, so a wrong
                # value on screen can be traced to the knob that put it there.
                for _label, _off, _raw, _src in _fields:
                    if _label == "rank":
                        continue
                    log(f"{self.peer}      status {_label} at payload+0x{_off:03X}"
                        f" = {_raw!r} (source: {_src})")
                if len(_fields) <= 1:
                    log(f"{self.peer}      every other byte is zero, which is "
                        f"what the client already has (FMO_STATUS_* all off)")
                if status.STATUS_NATION:
                    log(f"{self.peer}      WARNING: nation {status.STATUS_NATION} also picks "
                        f"the scene SCRIPT: lobby+0x8B4 of 1/2 substitutes "
                        f"script 98/99 for MapKind {zoneentry.MAPKIND} when +0x18 is 1 "
                        f"(0x61005100)")
                # WARNING: SEND THE LIST WE LOGGED. `reply_014a(char=_char)` here
                # re-resolved the nation WITHOUT _snat and took the
                # FMO_STATUS_NATION knob instead: prod logged 2 and sent 1,
                # and the client hid U.S.N. HQ from a U.S.N. pilot (the
                # Change Area category filter 0x6101099B keys on lobby+0x8B4).
                return [packet.build(status.MSG_START_STATUS, status.status_body(_fields),
                                     self.reply_seq(), p["conn"])]
            log(f"{self.peer}   -> 0x0131. NOT message 2 -- that is the FAILURE "
                f"reply, and a zero +0x08 with it is exactly what printed "
                f"[FM00000]. WARNING: But 0x0131 is also a value the client never "
                f"tests: step 1 wants 2 or 0x014A and falls through on anything "
                f"else, so the status block stays zero (FMO_START_STATUS=1)")
            return [packet.build(handshake.MSG_START_GAME_OK, b"", self.reply_seq(), p["conn"])]

        if p["msg"] == grouplogin.MSG_0154_REQ:
            log(f"{self.peer}   0x0154 -> 0x0155, {grouplogin.REPLY_0155_LEN}B: "
                f"count={addressing.GROUP_COUNT} of {grouplogin.GROUP_SLOTS} LoginGroup slots x "
                f"{grouplogin.GROUP_ENTRY_LEN}B")
            if addressing.GROUP_COUNT == 0:
                log(f"{self.peer}   count 0 skips the client's entry loop -- "
                    f"this is the state the working sequence was measured in "
                    f"(FMO_GROUP_COUNT to serve a list instead)")
            else:
                log(f"{self.peer}   WARNING: a non-empty list makes 0x61177620 TEAR "
                    f"DOWN the type-{addressing.GROUP_TYPE} connection object and rebuild "
                    f"it against {addressing.GROUP_HOST}:{addressing.GROUP_PORT}. That is a live "
                    f"network action, not a passive probe.")
            return [packet.build(grouplogin.MSG_0154_REPLY, grouplogin.reply_0155(addressing.GROUP_COUNT),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == inventory.MSG_0132_REQ:
            # The setups' own records first (what the wanzer equips), then
            # the items the pilot ACQUIRED (stored_items) -- one list, first
            # serial wins, capped at 400.
            _pc_inv = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
            _inv = inventory.merge_inventory(
                inventory.inventory_from_setups(self.setups_block()) if inventory.INVENTORY_FROM_SETUPS else [],
                inventory.stored_item_records(_pc_inv))
            if inventory.stored_items(_pc_inv):
                log(f"{self.peer}   0x0132: {len(inventory.stored_items(_pc_inv))} acquired "
                    f"item(s) on the pilot ride this inventory after the setups' "
                    f"own records")
            if not _inv:
                log(f"{self.peer}   0x0132 -> 0x0133, {inventory.REPLY_0133_LEN}B "
                    f"= the INVENTORY: u32 count + {inventory.INV_MAX} x "
                    f"{inventory.INV_ENTRY_LEN}B item records, all zero (count 0 = "
                    f"empty, which is a state the client handles)"
                    + ("" if inventory.INVENTORY_FROM_SETUPS else
                       " -- FMO_INVENTORY=0, the pre-2026-09-08 behaviour"))
                if inventory.INVENTORY_FROM_SETUPS:
                    log(f"{self.peer}   WARNING: the garage block equips NOTHING, so "
                        f"there is nothing to own -- the wanzer will read "
                        f"'-Nothing-' in every slot, and that is the setup "
                        f"being empty, not this message")
                return [packet.build(inventory.MSG_0132_REPLY, inventory.reply_0133(),
                                     self.reply_seq(), p["conn"])]
            _desc = ", ".join(
                f"serial {struct.unpack_from('<Q', r, 0)[0]} "
                f"kind 0x{r[inventory.ITEM_KIND]:02X} "
                f"id {struct.unpack_from('<H', r, inventory.ITEM_ID)[0]}"
                for r in _inv[:8])
            log(f"{self.peer}   0x0132 -> 0x0133, {inventory.REPLY_0133_LEN}B = the "
                f"INVENTORY, count {len(_inv)}: the item records the garage "
                f"block EQUIPS, copied verbatim so the 64-bit serials match. "
                f"{_desc}" + (" ..." if len(_inv) > 8 else ""))
            log(f"{self.peer}   KEY: an equipped serial with no entry here is "
                f"exactly what renders '-Nothing-': the client resolves each "
                f"part by searching this list (0x61177BE0, miss prints SE's "
                f"'the modified item's SERIAL NUMBER was not found'). Before "
                f"2026-09-08 this reply was 9,608 zeros, which is why a "
                f"CORRECT setup still showed an empty wanzer.")
            return [packet.build(inventory.MSG_0132_REPLY, inventory.reply_0133(_inv),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == inventory.MSG_0165_REQ:
            # A garage save (0x0167) beats the synthesized starter: serve the
            # block the client itself authored, byte-identical shape.
            _pc = self.playing_char() if charstore.CHAR_STORE else None
            if _pc and _pc.get("setups"):
                stored = bytes.fromhex(_pc["setups"])
                if len(stored) == inventory.REPLY_0166_LEN:
                    in_use = [i + 1 for i in range(inventory.SETUP_SLOTS)
                              if stored[i * inventory.SETUP_ENTRY_LEN + inventory.SETUP_IN_USE]]
                    # A ZERO camo / armour / line is the zero our old starter
                    # carried (row 0 of D29/D30 is an empty placeholder), never
                    # a choice: it gets the nation's starting paint. Every
                    # non-zero paint field goes back exactly as saved.
                    _snat, _ = popnation.character_nation(_pc)
                    if charlist.NATION:
                        _snat = charlist.NATION
                    stored, _pfill = inventory.fill_paint(stored, _snat)
                    log(f"{self.peer}   0x0165 -> 0x0166, {len(stored)}B "
                        f"STORED GARAGE BLOCK for id {_pc['id']} "
                        f"({_pc.get('first', '')} {_pc.get('last', '')}), "
                        f"setups in use: {in_use or 'none'} -- saved by a "
                        f"0x0167, served back verbatim"
                        + (f" except the starting paint in the zero paint "
                           f"fields of setup(s) {_pfill} (nation {_snat})"
                           if _pfill else ""))
                    return [packet.build(inventory.MSG_0165_REPLY, stored,
                                         self.reply_seq(), p["conn"])]
                log(f"{self.peer}   WARNING: stored setups for id {_pc['id']} are "
                    f"{len(stored)}B, expected {inventory.REPLY_0166_LEN} -- ignoring "
                    f"them and serving the starter loadout instead")
            # WARNING: This used to say "All zero" unconditionally and kept saying
            # it after reply_0166 gained a builder -- a log line that survives
            # the change it describes reads as a MEASUREMENT and is not one.
            # Report what is actually in the payload.
            _char = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
            # WARNING: THE `nation` KEY IS THE SWAPPED ONE (the swapped-key hazard): it holds
            # creation +0x26 = GENDER. This handler read it until 2026-09-05,
            # so a U.S.N. man (gender 1) was served the O.C.U. starter and an
            # O.C.U. woman (gender 2) the U.S.N. one -- 2 of the 4 characters
            # on prod (Remy, Test Guy). character_nation() reads +0x28.
            _nat, _nsrc = popnation.character_nation(_char)
            if charlist.NATION:
                _nat, _nsrc = charlist.NATION, "FMO_NATION (forced)"
            _cls = _char.get("cls")
            _parts, _why = inventory.setup_parts_for(_nat, _cls)
            log(f"{self.peer}   character {_char.get('first', '')!r}: nation "
                f"{_nat} from {_nsrc}; class {_cls} from the record")
            log(f"{self.peer}   0x0165 -> 0x0166, {inventory.REPLY_0166_LEN}B "
                f"= the WANZER SETUPS: {inventory.SETUP_SLOTS} x {inventory.SETUP_ENTRY_LEN}B "
                f"(each 0x28 + {inventory.SETUP_ITEMS} equipped-item records), then a "
                f"u32 + a byte at +0x{inventory.SETUP_TAIL_OFF:04X}.")
            log(f"{self.peer}   source: {_why}")
            if not _parts:
                log(f"{self.peer}   -> all zero, so every setup reads 'Setup "
                    f"empty' (+0x01 = 0), which the client prints at "
                    f"0x6117B899 rather than erroring. WARNING: That is the state in "
                    f"which the pilot's wanzer does NOT draw.")
            else:
                _by_idx = {i: (k, v) for i, k, v in _parts}
                log(f"{self.peer}   setup 1 IN USE (+0x01 = 1) with "
                    f"{len(_parts)} equipped items: "
                    + ", ".join(f"item {i} kind 0x{k:02X} id {v}"
                                for i, k, v in _parts)
                    + f"; setups 2-{inventory.SETUP_SLOTS} stay empty")
                # WARNING: Report the PART SLOTS the world builder will actually fill,
                # not the item count -- items 8, 9 and 11-20 are never read, so
                # "6 items" and "6 parts drawn" are not the same claim.
                _filled = [s for s in inventory.WORLD_PART_SLOTS
                           if inventory.SLOT_TO_ITEM[s] in _by_idx]
                log(f"{self.peer}   0x61002FEB will populate part slots "
                    f"{_filled} of {inventory.WORLD_PART_SLOTS} (0x6138A2A0 maps slot -> "
                    f"item). The observable is the render node's AABB: "
                    f"anything other than (-5,-1,-5)/(5,5,5) is a real mesh.")
            if _parts:
                log(f"{self.peer}   paint: {inventory.starter_paint(_nat) or 'none'} "
                    f"(nation {_nat} starting paint, inventory.starter_paint)")
            return [packet.build(inventory.MSG_0165_REPLY,
                                 inventory.reply_0166(parts=_parts, nation=_nat),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == shop.MSG_SETUP_SAVE:
            pl = p["payload"]
            log(f"{self.peer}   0x0167 = GARAGE SETUP SAVE, {len(pl)}B "
                f"(hdr {pl[:8].hex() if len(pl) >= 8 else pl.hex()}) -- the "
                f"whole 8-setup array; the sender's machine (lobby+0x73ee "
                f"state 2, poll 0x61178404) takes an EMPTY MESSAGE 1 as "
                f"success. Staying silent here is the hang hit live "
                f"2026-08-24 03:44Z.")
            if len(pl) >= 8 + inventory.REPLY_0166_LEN and charstore.CHAR_STORE:
                c = self.playing_char()
                if c is None:
                    log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} FAILURE, code "
                        f"{charselect.FAIL_CODE}: no character to attach setups to")
                    return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), charselect.FAIL_CODE)]
                block = pl[8:8 + inventory.REPLY_0166_LEN]
                c["setups"] = block.hex()
                # +0x00 = lobby+0x3DF2, the SELECTED setup (1-based): the one
                # the battle dresses and paints (inventory.active_setup_no).
                if 1 <= pl[0] <= inventory.SETUP_SLOTS:
                    c["setup_sel"] = pl[0]
                _sel = c.get("setup_sel") or 1
                if 1 <= _sel <= inventory.SETUP_SLOTS:
                    _sb = (_sel - 1) * inventory.SETUP_ENTRY_LEN
                    log(f"{self.peer}   selected setup {_sel} (+0x00); its paint: "
                        f"{inventory.setup_paint(block[_sb:_sb + inventory.SETUP_ENTRY_LEN])}")
                in_use = [i + 1 for i in range(inventory.SETUP_SLOTS)
                          if block[i * inventory.SETUP_ENTRY_LEN + inventory.SETUP_IN_USE]]
                self.commit(f"setups saved for id {c['id']} "
                            f"({c.get('first', '')} {c.get('last', '')}), "
                            f"in use: {in_use or 'none'}")
                log(f"{self.peer}   the block is byte-identical in shape to "
                    f"the 0x0166 reply and will be served back verbatim on "
                    f"the next 0x0165 -- the garage round-trips now")
            elif not charstore.CHAR_STORE:
                log(f"{self.peer}   WARNING: FMO_CHAR_STORE is empty, so this save is "
                    f"ACKNOWLEDGED AND DISCARDED -- the edits live only in "
                    f"the client's own list and are gone at the next login.")
            else:
                log(f"{self.peer}   WARNING: payload is {len(pl)}B, expected >= "
                    f"{8 + inventory.REPLY_0166_LEN} -- storing nothing rather than "
                    f"guessing at a truncated block")
            return [packet.build(0x0001, b"", self.reply_seq(), p["conn"])]

        if p["msg"] == shop.MSG_ACQUIRE:
            pl = p["payload"]
            part_id = struct.unpack_from("<H", pl, 0)[0] if len(pl) >= 2 else 0
            kind = pl[2] if len(pl) >= 3 else 0
            extra = struct.unpack_from("<I", pl, 4)[0] if len(pl) >= 8 else 0
            lo, hi = shop.mint_serial()
            body = bytearray(shop.REPLY_016B_LEN)
            # +0x28 zero takes the failure arm at 0x6117884C; 1 = take the item.
            struct.pack_into("<I", body, shop.ACQ_GATE, 1)
            struct.pack_into("<II", body, shop.ACQ_RECORD + inventory.ITEM_SERIAL_LO, lo, hi)
            struct.pack_into("<H", body, shop.ACQ_RECORD + inventory.ITEM_ID, part_id)
            body[shop.ACQ_RECORD + inventory.ITEM_KIND] = kind
            # KEY: +0x04 IS THE PRICE: the builder 0x611788F6 copies it from the
            # pending transaction's +4, and the 0x016B match arm (0x6117886D)
            # subtracts that same [pending+4] from lobby+0x88C. The client is
            # about to debit itself; the store debits the same figure, and the
            # item is KEPT on the pilot so the next 0x0133 still lists it.
            price = extra
            log(f"{self.peer}   0x0168 = ACQUIRE PART: id {part_id} kind "
                f"0x{kind:02X}, price {price} (+0x04 = pending+4, the figure "
                f"0x6117886D debits after our reply)")
            _pc = (self.playing_char() or None) if charstore.CHAR_STORE else None
            if _pc is not None:
                inventory.add_stored_item(_pc, lo | (hi << 32), part_id, kind, price)
                if price:
                    _now = self.credit_money(f"acquired item {part_id} (kind "
                                             f"0x{kind:02X})", money=-int(price))
                    if _now is None:
                        log(f"{self.peer}   WARNING: price NOT debited (no wallet on "
                            f"file): the client's display will drop by {price} "
                            f"and the next 0x014A will put it back")
                else:
                    try:
                        self.commit(f"acquired item {part_id} (kind 0x{kind:02X}) "
                                    f"serial {hi:08x}:{lo:08x}, free")
                    except Exception as _e:
                        log(f"{self.peer}   WARNING: item NOT persisted ({_e!r})")
                log(f"{self.peer}   item KEPT on the pilot ({len(inventory.stored_items(_pc))} "
                    f"acquired item(s) on file): it rides the next 0x0133 after "
                    f"the setups' own records")
            else:
                log(f"{self.peer}   WARNING: no pilot in the store: the item lives only "
                    f"in the client's list and is gone at relog, and the price "
                    f"the client debits is not banked")
            log(f"{self.peer}   -> 0x016B, {shop.REPLY_016B_LEN}B: gate 1, item "
                f"record serial {hi:08x}:{lo:08x} -- the client appends it to "
                f"its inventory (0x61177B30) and re-reads id/kind from the "
                f"record. WARNING: No stock and no legality check on (id, kind); the "
                f"master tables clamp a too-large id to the WRONG NAME rather "
                f"than erroring.")
            return [packet.build(shop.MSG_ACQUIRE_REPLY, bytes(body),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == zoneentry.MSG_0150_REQ:
            want = struct.unpack_from("<H", p["payload"], 0)[0] \
                if len(p["payload"]) >= 2 else 0
            if getattr(self, "sortie_pending", False):
                self.sortie_pending = False
                if want == 0xFFFD:
                    log(f"{self.peer}   progression: 0x0150 word 0xFFFD is the "
                        f"withdraw's -- or, live 2026-09-10, the 0x014C battle end's -- "
                        f"follow-up, not a completed sortie")
                elif not progress.PROGRESS_ADVANCE:
                    log(f"{self.peer}   progression: back from a sortie, "
                        f"FMO_PROGRESS_ADVANCE=0 -- nothing recorded")
                elif not charstore.CHAR_STORE:
                    log(f"{self.peer}   progression: back from a sortie, but "
                        f"no character store -- nothing to record into")
                else:
                    _pc = self.playing_char()
                    if not _pc:
                        log(f"{self.peer}   progression: back from a sortie, "
                            f"no played character on this session")
                    else:
                        _m, _what, _ra = progress.advance_progress(
                            _pc, tile=self.sector[0] if self.sector else None)
                        if _m:
                            # the byte may be 3 (cleared, awaiting the report's
                            # 105) or 99 (done): say which, and push the flags
                            # at the next lobby keepalive rather than into the
                            # scene change this 0x0150 starts
                            self.flags_push_due = True
                            self.commit(f"progression: {_pc.get('first','')} "
                                        f"{_pc.get('last','')} "
                                        f"{'cleared' if ': cleared,' in _what else 'completed'} "
                                        f"'{_m['title']}' -> {_what}"
                                        + (f", rank -> {_ra} (FMO_PROGRESS_RANK_"
                                           f"FOLLOW: the successor's level)"
                                           if _ra else ""))
                            log(f"{self.peer}   KEY: progression: the next "
                                f"frontier is "
                                + (" | ".join(x["title"] for x in
                                              progress.progress_frontier(_pc)) or "(nothing)")
                                + ". The flags reach the client in a 0x015A at the "
                                  f"next lobby keepalive; rank and Pilot level at "
                                  f"the next Start Game (0x014A).")
                        else:
                            log(f"{self.peer}   progression: back from a "
                                f"sortie -- {_what}")
            log(f"{self.peer}   0x0150 JOIN: client asks MapKind 0x{want:04X}"
                + ("  (0xFFFF/0xFFFD are the client's sentinels, not a real "
                   "selector)" if want in (0xFFFF, 0xFFFD) else ""))
            if not zoneentry.FILL_0153:
                log(f"{self.peer}   -> 0x0153 (NOT N+1), {zoneentry.REPLY_0153_LEN}B ALL "
                    f"ZERO (FMO_0153_FILL=0). This is the control: zeros are "
                    f"the only 0x0153 whose effect has been observed, and the "
                    f"client took them and exited to the Viewer.")
            mk, mn, pp = zoneentry.next_mapkind(), zoneentry.next_mapno(), zoneentry.next_pilotpos()
            csn = zoneentry.next_client_script()   # BEFORE advance_sweep,
            # so every field in this reply comes from the SAME
            # sweep index. Read after it, ClientScriptNo would be
            # one candidate ahead of the MapNo beside it.
            zoneentry.advance_sweep()
            # KEY: THE ZONE FOLLOWS THE PILOT'S FACTION (FMO_NATION_PER_CHARACTER):
            # the same index in the pilot's own band, per the LEV table.
            _gnat, _gnat_src = self.grant_nation()
            _mk0, mk = mk, zoneentry.faction_mapkind(mk, _gnat)
            if mk != _mk0:
                log(f"{self.peer}   zone kind {_mk0} -> {mk}: nation {_gnat} "
                    f"({_gnat_src}) belongs in that band, per AI/F08/D15.DAT")
            # KEY: STAY IN THE ZONE THE PILOT IS IN (FMO_RESUME_ZONE, manual
            # p.42 "you start from the same place you were at when you logged
            # out"): remember_zone stores every grant's zone, so the stored
            # zone is where the pilot was -- at a login and at a lobby Move
            # alike. resume_mapkind says when that is safe; the MapNo below
            # then follows the zone.
            if zoneentry.RESUME_ZONE and charstore.CHAR_STORE:
                _rz, _rz_why = zoneentry.resume_mapkind(self.playing_char(), _gnat,
                                                        self.pilot_trained())
                if _rz is not None:
                    log(f"{self.peer}   zone {mk} -> {_rz}: {_rz_why}")
                    mk = _rz
                else:
                    log(f"{self.peer}   zone {mk} kept: {_rz_why}")
            # KEY: THE MAP FOLLOWS THE ZONE (FMO_ZONE_MAPNO). A sweep still wins:
            # it is the knob you arm to go LOOKING for a map.
            if not zoneentry.MAPNO_SWEEP:
                _mn0 = mn
                mn, _mn_why = areachange.zone_mapno(mk, mn, "FMO_MAPNO")
                if mn != _mn0:
                    log(f"{self.peer}   MapNo {_mn0} -> {mn}: {_mn_why} binds "
                        f"zone {mk} to that lobby map")
            # KEY: LAST WORD: the console ships a different map set (MAPNO_PS2),
            # and a different ZONE-PACK set (PS2_VALID_MAPKINDS).
            mn = self.build_mapno(mn, "the value we picked")
            mk = self.build_mapkind(mk, "the zone we picked")
            if zoneentry.PILOTPOS_SWEEP:
                log(f"{self.peer}   PilotPos sweep: spawning the player at "
                    f"{pp} (candidate "
                    f"{(zoneentry._sweep_n[0] - 1) % len(zoneentry.PILOTPOS_SWEEP) + 1} of "
                    f"{len(zoneentry.PILOTPOS_SWEEP)})")
            # WARNING: A 0x0153 IS THE START OF A WORLD CHANNEL. Whatever we still
            # hold for this host describes a session that is over -- see
            # reset_world_channel.
            rooms.reset_world_channel(self.peer.split(":")[0], "0x0153 served",
                                      mapno=mn, mapkind=mk,
                                      place=move.entry_place(mk) if move.PLACES else None,
                                      account=self.account)
            _opc = self.playing_char() if charstore.CHAR_STORE else None
            _f18 = zoneentry.opening_field18(_opc)        # before remember_zone
            if _f18 is not None:
                _opc["opening_seen"] = True
                self.commit(f"opening: {_opc.get('first', '')} {_opc.get('last', '')} "
                            f"enters the world for the first time -> this entry is a "
                            f"ROOM (+0x18 = 1), the nation's story script plays in "
                            f"MapNo {mn}")
            self.remember_zone(mk, mn, "world entry")
            if zoneentry.FILL_0153:
                log(f"{self.peer}   -> 0x0153 (NOT N+1), {zoneentry.REPLY_0153_LEN}B: "
                    f"MapKind={mk} MapNo={mn} "
                    f"ClientScriptNo={csn} "
                    f"MusicNo={zoneentry.MUSIC_NO} "
                    f"SeNo={zoneentry.SE_NO}, PilotPos={pp}, "
                    f"+0x18={zoneentry.FIELD_18 if _f18 is None else _f18} "
                    f"+0x1C={zoneentry.FIELD_1C}, "
                    f"endpoint {addressing.BATTLE_HOST}:{addressing.BATTLE_PORT}")
                log(f"{self.peer}   {zoneentry.describe_script_choice(mk, _gnat, _gnat_src)}")
                log(f"{self.peer}   WARNING: this reply makes the client LEAVE THE "
                    f"LOBBY: 0x61006350 switches globals+0x24 to scene 6 or 7. "
                    f"Whatever happens next is a game scene failing or working, "
                    f"not the lobby.")
                log(f"{self.peer}   WARNING: 0x61005100 registers type 2 = MapNo and "
                    f"type 3 = MapKind as RESOURCE IDS. Measured: the SCRIPT "
                    f"loads, the MAP allocates ZERO bytes, so slot 1 inherits "
                    f"slot 0's pointer and the fixup relocates one buffer twice "
                    f"-> 0x611250A2. MapNo is the id to get right.")
                if zoneentry.MAPNO_SWEEP or zoneentry.MAPKIND_SWEEP:
                    log(f"{self.peer}   sweep: MapNo{zoneentry.MAPNO_SWEEP or '=fixed'} "
                        f"MapKind{zoneentry.MAPKIND_SWEEP or '=fixed'} -- this run used "
                        f"MapNo={mn} MapKind={mk}; relaunch for the next")
                log(f"{self.peer}   check with the crash instrument: an 'alloc' "
                    f"of 0 on a slot names the id that does not exist")
            outs = [packet.build(zoneentry.MSG_0150_REPLY,
                           zoneentry.reply_0153(mapkind=mk, mapno=mn, pilotpos=pp,
                                                csn=csn, field18=_f18,
                                                host=addressing.host_for(addressing.BATTLE_HOST, self.ip),
                                     self_id=self.wire_self_id()),
                           self.reply_seq(), p["conn"])]
            # KEY: MEASURED TWICE, 2026-09-04. This is WORLD ENTRY, a different
            # grant from the Move handler's, and it runs the same lobby reset
            # 0x6117A2F6 -- so it wipes lobby+0x7724 exactly as a move does.
            # The first live run lost the table to this; the second lost it
            # AGAIN because the fix had only been applied to the move path.
            # Both 0x0153 emit sites need the push behind them.
            if zonecontrol.ZONE_CONTROL:
                zpush = zonecontrol.zone_control_push(p["conn"])
                if zpush:
                    log(f"{self.peer}   -> 0x{zonecontrol.MSG_ZONE_CONTROL:04X} ZONE-CONTROL "
                        f"push rides BEHIND this world-entry grant: the grant "
                        f"resets the lobby and zeroes lobby+0x7724, so the "
                        f"copy that rode the 0x01AC poll is already gone "
                        f"(and once more on the first keepalive 10s+ later: "
                        f"the arm is gated [lobby+0x20]==4 and the client is "
                        f"not in-world yet AT THIS INSTANT, so this copy can "
                        f"be dropped -- live 2026-09-12, every area grey).")
                    # WARNING: BOTH 0x0153 emit sites need the deferred resend too,
                    # for exactly the reason the comment above gives for the
                    # push itself. Arming only the Move path was the same
                    # mistake a second time (live 2026-09-12): a RELOG takes
                    # this path, not that one.
                    self.zone_push_pending = True
                    self.zone_push_grant_at = time.time()
                    outs.append(zpush)
            return outs

        if p["msg"] == move.MSG_LOGOUT_REQ:
            log(f"{self.peer}   0x0152 LOG OUT ({len(p['payload'])}B): "
                f"answering an EMPTY message 1. Unanswered this HANGS the "
                f"client on the one action that is meant to get you out "
                f"cleanly. WARNING: Nothing is torn down here -- the session, the "
                f"world channel and the store are untouched; the client closes "
                f"the connection itself and our close path handles that.")
            # The ROOM is told, though: every viewer that has this player
            # popped stops relaying them now (room_left -> room_prune), and
            # says in the log whether a depop went out or only the relay
            # stopped (FMO_UDP_ROOM_DEPOP).
            if room.ROOM and not rooms.room_left(self.peer.split(":")[0],
                                                 "0x0152 LOG OUT"):
                log(f"{self.peer}   room: no world channel to mark -- this "
                    f"host was not standing in a lobby")
            return [packet.build(handshake.MSG_SESSION_START, b"", self.reply_seq(), p["conn"])]

        if p["msg"] == move.MSG_MEMBER_CHECK_REQ:
            # KEY: /tell sends this same id WITH TEXT at +0x32 (trade.MSG_TELL);
            # the Login Check sends names only. A tell goes to its one
            # recipient and is never dumped to the log below.
            _tell = trade.parse_tell(p["payload"]) if trade.TELL else None
            if _tell is not None:
                return self.on_tell(p, *_tell)
            if not charselect.ANSWER_LOBAPI:
                log(f"{self.peer}   FMO_ANSWER_LOBAPI=0 -- 0x0181 unanswered, "
                    f"and the room-member screen will hang.")
                return []
            _nm = p["payload"][0x10:0x10 + 0x40].split(b"\0")[0]
            log(f"{self.peer}   0x0181 ROOM MEMBER CHECK, {len(p['payload'])}B, "
                f"about {_nm.decode('cp932', 'replace')!r} (payload+0x10, the "
                f"name its builder copies from lobby+0x744A). Answering an "
                f"EMPTY message 1 -- the same convention 0x0182 confirmed. "
                f"WARNING: That UNHANGS the screen and is NOT an answer: this asks "
                f"about another member and we say nothing about them.")
            log(f"{self.peer}   the request's own bytes are the clue to what it "
                f"wanted:\n{hexdump(p['payload'][:0x60])}")
            _conn = p["conn"]
            if popself.MEMBER_CHECK:
                _n1, _n2 = popnames.member_check_names(p["payload"])
                _live = popnames.online_players()
                _hit = (_n1.strip().lower(),
                        _n2.strip().lower()) in _live
                _conn = popself.MEMBER_CHECK_HIT if _hit else popself.MEMBER_CHECK_MISS
                log(f"{self.peer}   MEMBER CHECK ANSWERED: "
                    f"{_n1!r} {_n2!r} is "
                    f"{'IN A WORLD CHANNEL' if _hit else 'NOT in one'}"
                    f" -> message 1 with conn={_conn} (word [rx+8]); "
                    f"0x61179A36 turns that into arm {2 if _conn else 1} "
                    f"of two message boxes. WHICH ARM SAYS 'online' IS "
                    f"NOT DECODED -- if the box reads backwards, swap "
                    f"FMO_MEMBER_CHECK_HIT/MISS and change nothing else. "
                    f"In a world channel now: "
                    f"{sorted(' '.join(k) for k in _live)}")
            return [packet.build(handshake.MSG_SESSION_START, b"", self.reply_seq(), _conn)]

        if p["msg"] == move.MSG_PLAYTIME_REQ:
            if not charselect.ANSWER_LOBAPI:
                log(f"{self.peer}   FMO_ANSWER_LOBAPI=0 -- 0x0182 unanswered, "
                    f"and Play Time will load for ever.")
                return []
            _secs = self.playtime_seconds()
            _d, _h, _m = move.playtime_dhm(_secs)
            _char = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
            if _char:
                _who = " ".join(str(x) for x in (_char.get("first"),
                                                 _char.get("last")) if x).strip()
                _who = _who or f"the pilot in slot {_char.get('id')}"
            else:
                _who = "no pilot on file (nothing was banked)"
            log(f"{self.peer}   0x0182 PLAY TIME ({len(p['payload'])}B "
                f"payload) -> 0x0183, {move.REPLY_0183_LEN}B: {_secs}s at "
                f"payload+0x{move.S183_SECONDS:X} (frame+0x24) for {_who}. The "
                f"client divides that itself and the screen should read "
                f"'Play time {_d}d {_h}h {_m}m' (systext 1:6); the date half "
                f"of that line is ITS OWN clock, not ours.")
            if not move.PLAYTIME:
                log(f"{self.peer}      FMO_PLAYTIME=0 -- that is "
                    f"FMO_PLAYTIME_BASE alone; nothing was read from or "
                    f"written to the character store")
            elif move.PLAYTIME_BASE:
                log(f"{self.peer}      includes FMO_PLAYTIME_BASE="
                    f"{move.PLAYTIME_BASE}s on top of what is banked")
            # WARNING: 0x0183, NOT message 1. 0x6116338E is `cmp word [rx+6], 0x183`
            # and its `jne` goes straight to the [FMxxxxx] box -- which is what
            # every message-1 answer this server sent produced. See the block
            # comment at MSG_PLAYTIME_REPLY.
            return [packet.build(move.MSG_PLAYTIME_REPLY, move.reply_0183(_secs),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == move.MSG_MOVE_LIST_REQ:
            asked = struct.unpack_from("<I", p["payload"], 0)[0] \
                if len(p["payload"]) >= 4 else None
            if move.PLACES:
                _zone = rooms.zone_of(self.peer.split(":")[0])[0]
                if _zone is None:
                    _zone = zoneentry.faction_mapkind(zoneentry.MAPKIND, self.grant_nation()[0])
                rows = move.place_rows(_zone, asked)
                source = (f"FMO_PLACES registry for zone {_zone}, category "
                          f"{asked} ({move.MOVE_CATEGORY_NAMES.get(asked, 'unknown')}): "
                          f"ids are kind*1000+instance, People = live world channels there")
            else:
                rows, source = move.move_list_for(asked)
            log(f"{self.peer}   0x016E MOVE: the client wants the lobby/room "
                f"list. Its 16B payload carries one field, +0x00={asked} "
                f"(a CATEGORY: {move.MOVE_CATEGORY_NAMES.get(asked, 'unknown')}; "
                f"the same value 0x016D will send back); everything else in "
                f"it is whatever the compose buffer already held.")
            log(f"{self.peer}   MOVE LIST SOURCE: {source} -> {len(rows)} "
                f"row(s)")
            if not move.ANSWER_MOVE:
                log(f"{self.peer}   FMO_ANSWER_MOVE=0 -- staying silent. That "
                    f"reproduces the 0x01AB failure ON PURPOSE: the Move "
                    f"dialog hangs with no way back to the Viewer.")
                return []
            if rows:
                log(f"{self.peer}   -> 0x016F, {move.REPLY_016F_LEN}B, "
                    f"count={len(rows)}: {rows} -- from {source.split(' ')[0]}, "
                    f"a PROBE. PREDICTION to check on screen: these appear as "
                    f"rows under 'Lobby ID'/'Room ID' and 'People', at most "
                    f"{move.MOVE_UI_ROWS} of them. Swapped columns REFUTE the "
                    f"reading in the 0x016F note; nothing there proves it.")
            else:
                log(f"{self.peer}   -> 0x016F, {move.REPLY_016F_LEN}B, count=0 -- "
                    f"{source.split(' ')[0]} is empty for category {asked}, "
                    f"and 0 is NOT an error: 0x611907EF turns it into the "
                    f"Confirmation box \"There is nobody in that "
                    f"lobby/room/briefing room right now. Move there anyway?\" "
                    f"(which of the three is the client's own mode byte, not "
                    f"anything we send). Set FMO_MOVE_LIST=1:3 (categories "
                    f"0/1) or FMO_MOVE_LIST_BRIEFING=101:0 (category 2) to "
                    f"serve rows.")
            return [packet.build(move.MSG_MOVE_LIST_REPLY, move.reply_016f(rows),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == move.MSG_MOVE_REQ:
            log(f"{self.peer}   0x016D MOVE, {len(p['payload'])}B: "
                f"{move.describe_move_req(p['payload'])}")
            if not move.ANSWER_MOVE:
                log(f"{self.peer}   FMO_ANSWER_MOVE=0 -- staying silent; the "
                    f"client is now waiting on a reply it will never get.")
                return []
            mk, mn, pp = zoneentry.next_mapkind(), zoneentry.next_mapno(), zoneentry.next_pilotpos()
            csn = zoneentry.next_client_script()   # BEFORE advance_sweep,
            # so every field in this reply comes from the SAME
            # sweep index. Read after it, ClientScriptNo would be
            # one candidate ahead of the MapNo beside it.
            zoneentry.advance_sweep()
            # KEY: THE ZONE FOLLOWS THE PILOT'S FACTION (FMO_NATION_PER_CHARACTER):
            # the same index in the pilot's own band, per the LEV table.
            _gnat, _gnat_src = self.grant_nation()
            _mk0, mk = mk, zoneentry.faction_mapkind(mk, _gnat)
            if mk != _mk0:
                log(f"{self.peer}   zone kind {_mk0} -> {mk}: nation {_gnat} "
                    f"({_gnat_src}) belongs in that band, per AI/F08/D15.DAT")
            # KEY: STAY IN THE ZONE THE PILOT IS IN (FMO_RESUME_ZONE, manual
            # p.42 "you start from the same place you were at when you logged
            # out"): remember_zone stores every grant's zone, so the stored
            # zone is where the pilot was -- at a login and at a lobby Move
            # alike. resume_mapkind says when that is safe; the MapNo below
            # then follows the zone.
            if zoneentry.RESUME_ZONE and charstore.CHAR_STORE:
                _rz, _rz_why = zoneentry.resume_mapkind(self.playing_char(), _gnat,
                                                        self.pilot_trained())
                if _rz is not None:
                    log(f"{self.peer}   zone {mk} -> {_rz}: {_rz_why}")
                    mk = _rz
                else:
                    log(f"{self.peer}   zone {mk} kept: {_rz_why}")
            # KEY: THE MAP FOLLOWS THE ZONE (FMO_ZONE_MAPNO). A sweep still wins:
            # it is the knob you arm to go LOOKING for a map.
            if not zoneentry.MAPNO_SWEEP:
                _mn0 = mn
                mn, _mn_why = areachange.zone_mapno(mk, mn, "FMO_MAPNO")
                if mn != _mn0:
                    log(f"{self.peer}   MapNo {_mn0} -> {mn}: {_mn_why} binds "
                        f"zone {mk} to that lobby map")
            # KEY: LAST WORD: the console ships a different map set (MAPNO_PS2).
            mn = self.build_mapno(mn, "the value we picked")
            if zoneentry.PILOTPOS_SWEEP:
                log(f"{self.peer}   PilotPos sweep: this move spawns the "
                    f"player at {pp} (candidate "
                    f"{(zoneentry._sweep_n[0] - 1) % len(zoneentry.PILOTPOS_SWEEP) + 1} of "
                    f"{len(zoneentry.PILOTPOS_SWEEP)}). 0x61003340 writes these four "
                    f"floats over the unit body's +0x40 -- Move again for the "
                    f"next one, no relaunch needed.")
            # KEY: HONOUR THE DESTINATION THE CLIENT PICKED (2026-08-23, live).
            # 0x016D's +0x04 is NOT a row index -- it is the list entry's OWN
            # ID, echoed back from the 0x016F we served. Measured: with
            # FMO_MOVE_LIST ids 101,102,121..161 the client sent back exactly
            # 101, 124, 102 and 161 as it picked each one. So when the served
            # ids ARE MapNos, the client is naming the map it wants and we can
            # simply grant it.
            #
            # WARNING: GATED ON VALID_MAPNOS, and that gate is not cosmetic: a MapNo
            # with no file allocates zero bytes, aliases the script slot and
            # crashes the client at 0x611250A2. The picked value arrives from
            # the wire, so it is untrusted input -- never grant it unchecked.
            # FMO_MOVE_MAPNO still WINS when set, so the fixed-answer probe
            # stays available for an A/B.
            picked = category = None
            if len(p["payload"]) >= move.MOVE_REQ_LEN:
                category, picked = struct.unpack_from("<II", p["payload"],
                                                      move.M16D_FIELD_00)
            # KEY: THE CATEGORY DECIDES THE ZONE KIND OF THE GRANT (2026-08-26).
            # State 4 never tells the scene selector which submenu entry the
            # player used; the only thing that makes the destination a
            # briefing room is 0x0153 +0x18 == 2 (globals+0x1AC, the kind the
            # client's own name functions 0x611D8F83/0x611DE467 render as
            # "Briefing Room"). FMO_MOVE_KIND_BRIEFING=-1 is the A/B.
            kind = move.move_kind_for(category)
            if kind is not None:
                log(f"{self.peer}   MOVE CATEGORY {category} "
                    f"({move.MOVE_CATEGORY_NAMES.get(category, 'unknown')}): the "
                    f"grant's +0x18 zone kind will be {kind} "
                    f"(FMO_MOVE_KIND_BRIEFING) instead of FIELD_18={zoneentry.FIELD_18}. "
                    f"PREDICTION: the client's zone-kind name becomes "
                    f"'Briefing Room' (systext 35/52); -1 reverts.")
            served_ids = [i for i, _ in move.move_list_for(category)[0]]
            #: True once the client's own pick has become the granted MapNo.
            #: Read by the two log lines below -- see the 2026-09-03 note there.
            honoured = False
            _place = None
            _hangar_refused = None
            if move.PLACES and move.HANGAR and move.MOVE_MAPNO is None and picked == 0:
                # VERIFIED: THE HANGAR DOOR (2026-09-09). The Hangar menu sends a bare
                # 0x016D with row 0 and NO 0x016E before it: "My Hangar" with
                # empty names, "Another Player's Hangar" with the owner's names
                # at +0x18/+0x29 and the numeric password at +0x08. SE: one
                # hangar per pilot; others enter by name+password only while
                # the owner is inside (systext 2:55 wrong password, 2:56 not in
                # a hangar). Before this the pick fell through to "re-enter
                # the current place" -- the live report "My Hangar takes me
                # back to the Coliseum".
                _pw, _na, _nb = hangar.hangar_request(p["payload"])
                _host = self.peer.split(":")[0]
                if not _na and not _nb:
                    _pc = self.playing_char() if charstore.CHAR_STORE else None
                    _place = hangar.hangar_place(_pc.get("id", 1) if _pc else 1)
                    log(f"{self.peer}   VERIFIED: HANGAR: My Hangar -> {move.place_name(_place)} "
                        f"(instance = the pilot's own character id)")
                else:
                    _oh = popnames.online_players().get((_na.strip().lower(), _nb.strip().lower()))
                    _op = move.WORLD_PLACES.get(_oh) if _oh else None
                    if _oh is None:
                        _hangar_refused = f"{_na} {_nb} is not in any lobby (2:56)"
                    elif not _op or _op[1] != 5:
                        _hangar_refused = (f"{_na} {_nb} is in {move.place_name(_op)}, not "
                                           f"their hangar (2:56)")
                    elif (hangar.hangar_owner_password(_oh) or "") != _pw:
                        _hangar_refused = f"wrong password for {_na} {_nb}'s hangar (2:55)"
                    else:
                        _place = _op
                        log(f"{self.peer}   VERIFIED: HANGAR: Another Player's Hangar -> "
                            f"{move.place_name(_place)} ({_na} {_nb}, password matched)")
                if _place is not None:
                    mn, _pm_why = move.place_map(0, 5, mn)
                    kind, honoured = 5, True
                    _own = self.playing_char() if charstore.CHAR_STORE else None
                    _own_id = int(_own.get("id", 1)) if _own else 1
                    _is_owner = (_place[2] == _own_id and not _na and not _nb)
                    if hangar.HANGAR_OWNER_F18 and _is_owner:
                        # SE's own-hangar grant: +0x18 = the owner's id (see
                        # HANGAR_OWNER_F18), and the owner's wanzers go in the bays
                        kind = _own_id
                        try:
                            _slots = hangar.setups_in_use(self.setups_block())
                        except Exception as _e:
                            _slots = []
                            log(f"{self.peer}   WARNING: hangar: could not read the garage ({_e!r})")
                        hangar.HANGAR_RESIDENTS[_host] = {"owner": _own_id, "slots": _slots, "mapno": mn}
                        log(f"{self.peer}   VERIFIED: HANGAR OWNER: grant +0x18 = {kind} (your "
                            f"character id) -- E307/E308 open only when it equals your "
                            f"own unit id; setups in use {[i + 1 for i in _slots]} -> bay "
                            f"units {[hex(0x82080000 + i) for i in _slots]} (type "
                            f"{hangar.HANGAR_BAY_TYPE}) at {hangar.hangar_bays(mn)[:len(_slots)]}. The "
                            f"consoles also need {[hex(k) for k in hangar.HANGAR_REQUIRED_KEYS]} "
                            f"popped (the editor's Hangar tab).")
                    else:
                        hangar.HANGAR_RESIDENTS.pop(_host, None)
                    log(f"{self.peer}   -> hangar map MapNo {mn} ({_pm_why}); grant "
                        f"+0x18 = {kind} ({'the owner id' if kind != 5 else 'Hangar'})")
                elif _hangar_refused:
                    log(f"{self.peer}   WARNING: HANGAR REFUSED: {_hangar_refused} -- "
                        f"answering message 1, the client's own 'no' (0x61174400, "
                        f"back to the lobby)")
                    return [packet.build(handshake.MSG_SESSION_START, b"", p["seq"], p["conn"])]
            if move.PLACES and move.MOVE_MAPNO is None and _place is None:
                # KEY: THE PLACE REGISTRY: the pick is (kind, instance) in the
                # zone the player stands in; the map follows the KIND and
                # the grant's +0x18 names the kind on the client's HUD.
                _zone = rooms.zone_of(self.peer.split(":")[0])[0]
                if _zone is None:
                    _zone = zoneentry.faction_mapkind(mk, _gnat)
                _place, _pwhy = move.resolve_place_pick(_zone, category, picked)
                if _place is not None:
                    _pm, _pm_why = move.place_map(_zone, _place[1], mn, nation=_gnat)
                    log(f"{self.peer}   PLACE PICK: {move.place_name(_place)} -> MapNo "
                        f"{_pm} ({_pm_why}); grant +0x18 = {_place[1]} "
                        f"({move.PLACE_KIND_NAMES[_place[1]]}); people there now: "
                        f"{move.place_population().get(_place, 0)}")
                    mn, kind, honoured = _pm, _place[1], True
                else:
                    log(f"{self.peer}   WARNING: PLACE PICK REFUSED: {_pwhy} -- "
                        f"re-entering {move.place_name(move.WORLD_PLACES.get(self.peer.split(':')[0]))}")
                    _cur = move.WORLD_PLACES.get(self.peer.split(":")[0])
                    if _cur is not None:
                        _place = _cur
                        mn, _ = move.place_map(_zone, _cur[1], mn, nation=_gnat)
                        kind = _cur[1]
            if _place is not None:
                pass
            elif move.MOVE_MAPNO is None and picked is not None:
                if picked in zoneentry.VALID_MAPNOS:
                    log(f"{self.peer}   MOVE HONOURED: the client picked id "
                        f"{picked}, which is a MapNo on disk -- granting it "
                        f"instead of re-entering {mn}.")
                    mn = picked
                    honoured = True
                elif picked in served_ids:
                    log(f"{self.peer}   WARNING: the client picked id {picked}, which "
                        f"we SERVED but is not a MapNo on disk -- refusing to "
                        f"grant it (a missing map crashes the client at "
                        f"0x611250A2). Re-entering {mn}. Fix "
                        f"{move.move_list_for(category)[1].split(' ')[0]}.")
                else:
                    log(f"{self.peer}   the picked id {picked} is not one we "
                        f"served; re-entering {mn}.")
            if move.MOVE_MAPNO is not None:
                log(f"{self.peer}   FMO_MOVE_MAPNO={move.MOVE_MAPNO}: the move "
                    f"grants a DIFFERENT map from the one the 0x0150 join "
                    f"would ({mn}). That is the whole point of the knob -- "
                    f"without it Move re-enters the zone the player is in and "
                    f"proves nothing about terrain.")
                mn = move.MOVE_MAPNO
            elif honoured:
                # KEY: CORRECTED 2026-09-03. This arm used to be the only
                # one, and it said every move re-enters the zone the player is
                # already in. That stopped being true on 2026-08-23, when the
                # picked id was found to BE a MapNo and the grant started
                # honouring it -- so for a whole week this handler logged
                # `MOVE HONOURED: granting {picked}` and then, twelve lines
                # later, that the move went nowhere. Both lines were ours and
                # they contradicted each other on every single move.
                log(f"{self.peer}   FMO_MOVE_MAPNO is unset and it does not "
                    f"matter: the client's own pick ({mn}) is the destination, "
                    f"so this IS a real zone change and a real test of "
                    f"terrain.")
            else:
                log(f"{self.peer}   WARNING: FMO_MOVE_MAPNO is unset AND the pick was "
                    f"not granted, so this move grants MapNo {mn} -- THE ZONE "
                    f"THE PLAYER IS ALREADY IN. The scene will re-enter the "
                    f"same base. That is a valid test of the Move MENU and no "
                    f"test at all of terrain.")
            # WARNING: THE PS2 GATE, AGAIN AND LAST (2026-09-21). The call above runs
            # BEFORE the hangar / place / honoured-pick arms, and every one of
            # them reassigns `mn` -- so a console that entered on 121 was Moved
            # to the PC's 102 / 124 / 122 / 151. That is a crash on hardware.
            _mn_pc = mn
            mn = self.build_mapno(mn, "the Move destination")
            mk = self.build_mapkind(mk, "the Move's zone")
            if mn != _mn_pc and hangar.HANGAR_RESIDENTS.get(self.peer.split(":")[0]):
                hangar.HANGAR_RESIDENTS[self.peer.split(":")[0]]["mapno"] = mn
            # Same reason as the 0x0150 path: a 0x0153 STARTS a world channel,
            # so whatever we still hold for this host describes a dead session.
            rooms.reset_world_channel(self.peer.split(":")[0],
                                      "0x0153 served (0x016D)", mapno=mn, mapkind=mk,
                                      place=_place, account=self.account)
            self.remember_zone(mk, mn, "move")
            log(f"{self.peer}   -> 0x0153, {zoneentry.REPLY_0153_LEN}B: "
                f"ClientScriptNo={csn} PilotPos={pp} "
                f"MapKind={mk} "
                f"MapNo={mn} "
                f"ZoneKind(+0x18)={zoneentry.FIELD_18 if kind is None else kind}, "
                f"endpoint {addressing.BATTLE_HOST}:{addressing.BATTLE_PORT} -- the SAME "
                f"grant 0x0150 gets, because state 4 of the Move machine "
                f"(0x61190B50) consumes it with the same code. The client will "
                f"LEAVE THE LOBBY on this.")
            log(f"{self.peer}   {zoneentry.describe_script_choice(mk)}")
            # KEY: CORRECTED 2026-09-03, same staleness as the line above: the
            # unconditional "we have not honoured this" claim predates the
            # 2026-08-23 destination fix. Kept, but only on the arm where it is
            # actually true -- a pick we did not grant.
            if honoured or move.MOVE_MAPNO is not None:
                log(f"{self.peer}   (the refusal path, unused here: answering "
                    f"message 1 instead of 0x0153 is the client's own 'no' "
                    f"(0x61174400, back to the lobby) -- that is what to send "
                    f"the day a move needs to fail cleanly.)")
            else:
                log(f"{self.peer}   WARNING: AND IT IS A CLAIM WE HAVE NOT HONOURED: "
                    f"the MapNo above is FMO_MAPNO, not the destination in the "
                    f"request. Answering message 1 instead is the client's own "
                    f"'no' (0x61174400, back to the lobby) -- that is what to "
                    f"send the day this needs to fail cleanly.")
            outs = [packet.build(zoneentry.MSG_0150_REPLY,
                                 zoneentry.reply_0153(mapkind=mk, mapno=mn, pilotpos=pp,
                                                      csn=csn, field18=kind,
                                                      host=addressing.host_for(addressing.BATTLE_HOST, self.ip),
                                                      self_id=self.wire_self_id()),
                                 self.reply_seq(), p["conn"])]
            # KEY: CITY-TABLE ORDERING (measured 2026-08-27 03:56Z): the client's
            # 0x01AC came IN THE LOBBY, so the 0x019A push filled lobby+0x6C36
            # ten seconds BEFORE this grant -- and the move's lobby reset
            # (0x6117A2F6) wiped it again before 161's script ran 0xE30C. The
            # push must land AFTER the grant: once right behind the 0x0153 in
            # the same batch, and once more on the first packet after the move
            # (the 15s 0x0198 keepalive) in case the scene rebuild also wipes
            # what arrived mid-switch. Two pushes are the same 420 bytes twice.
            if kind is not None and citytable.city_rows():
                push = citytable.city_table_push(p["conn"])
                if push:
                    self.city_push_pending = True
                    #: 04:05Z run: a keepalive landed the SAME SECOND as the
                    #: grant and consumed the deferred copy mid-scene-switch.
                    #: Hold it until the scene has provably been up a while.
                    self.city_push_grant_at = time.time()
                    log(f"{self.peer}   -> 0x{citytable.MSG_CITY_TABLE:04X} CITY push "
                        f"rides BEHIND this grant (and once more on the first "
                        f"keepalive 10s+ later): the 03:56Z run pushed before "
                        f"the move and the lobby reset 0x6117A2F6 wiped it.")
                    outs.append(push)
            # KEY: MEASURED 2026-09-04, and it cost a launch: the zone push rode
            # ONLY the 0x01AC poll, the client polled 0x01AC exactly ONCE (in
            # the lobby, 0 s before this grant), and the grant's own lobby reset
            # 0x6117A2F6 -- which contains the `rep stosd 0x84` zero-init of
            # lobby+0x7724 at 0x6117A2A8 -- wiped it again before Change Area
            # could be opened. "Ride every poll so a wipe self-heals" is only
            # true if the client polls again, and in-world it may never.
            # So the zone push rides the grant as well, exactly as the city
            # push has since 2026-08-27.
            if zonecontrol.ZONE_CONTROL:
                zpush = zonecontrol.zone_control_push(p["conn"])
                if zpush:
                    log(f"{self.peer}   -> 0x{zonecontrol.MSG_ZONE_CONTROL:04X} ZONE-CONTROL "
                        f"push rides BEHIND this grant: the grant resets the "
                        f"lobby and zeroes lobby+0x7724, so a push that "
                        f"preceded it is already gone (and once more on the "
                        f"first keepalive 10s+ later -- the arm 0x6117F013 is "
                        f"gated [lobby+0x20]==4, and RIGHT HERE the client is "
                        f"still entering the world, so this copy can be "
                        f"dropped on the floor).")
                    self.zone_push_pending = True
                    self.zone_push_grant_at = time.time()
                    outs.append(zpush)
            # THE AUTO-SORTIE PUSH (0x014E) is armed HERE, not sent here. A
            # Move is the WARM entry (scene 6), and the countdown tick gate
            # only fires while [globals+0x1C4]==6 -- so this grant is the one
            # moment the auto-sortie can start. But the grant's own reset
            # zeroes lobby+0x5C7E (the block this push fills), so the push
            # must land LATER: deferred to a keepalive SORTIE_PUSH_DELAY s
            # out, one shot per session (see MSG_KEEPALIVE).
            if sortiepush.sortie_push_armed() and not getattr(self, "sortie_push_done",
                                                    False):
                self.sortie_push_pending = True
                self.sortie_push_grant_at = time.time()
                _mn, _src = sortiepush.sortie_push_mapno()
                log(f"{self.peer}   PROBE: AUTO-SORTIE ARMED (FMO_SORTIE_PUSH): a "
                    f"0x{sortiepush.MSG_SORTIE_PUSH:04X} push will ride the first "
                    f"keepalive ~{sortiepush.SORTIE_PUSH_DELAY:g}s after THIS warm move "
                    f"grant (scene 6, the only scene whose tick starts the "
                    f"countdown). Target: {_src}. WARNING: STAND STILL after it "
                    f"lands -- another Move zeroes the block but not the latch.")
            return outs

        if p["msg"] == timesync.MSG_TIME_REQ:
            now = time.time()
            sec = int(now)
            usec = int((now - sec) * 1_000_000)
            log(f"{self.peer}   time request -> {sec}.{usec:06d} "
                f"(seconds, microseconds)")
            return [packet.build(timesync.MSG_TIME_REPLY, struct.pack("<II", sec, usec),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == charlist.MSG_LIST_REQ:
            # WARNING: The SERVED count is the roster length when a store is set --
            # FMO_LIST_COUNT is ignored then (the store takes precedence below).
            # Printing LIST_COUNT here read as "count=1 TESTPILOT" while the wire
            # actually carried an EMPTY list, which cost real confusion chasing an
            # FM00000. Print what is actually sent.
            served = (len(charlist.with_free_slot(self.roster)) if charstore.CHAR_STORE
                      else charlist.LIST_COUNT)
            log(f"{self.peer}   0x12E -> 0x12F, count={served} of "
                f"{charlist.LIST_SLOTS} slots x {charlist.LIST_ENTRY_LEN}B"
                + ("  (count 0 -- ? the client shows the HAS-CHARACTER menu "
                   "for this, not Create; see with_free_slot)" if served == 0
                   else f"  (probe entries named {charlist.LIST_NAME!r}; the field "
                        f"meanings are NOT decoded)"))
            if charstore.CHAR_STORE:
                names = ", ".join(
                    f"{c['id']}:{c.get('first', '')} {c.get('last', '')}"
                    for c in self.roster) or "empty"
                log(f"{self.peer}   from the STORE, account {self.account}: "
                    f"{names}")
                if not charlist.has_named_character(self.roster):
                    log(f"{self.peer}   no character yet -> serving ONE "
                        f"EMPTY-NAMED slot (id "
                        f"{charlist.with_free_slot(self.roster)[0]['id']}) so the title "
                        f"screen picks its Create Character menu. An honest "
                        f"count of 0 selects the HAS-CHARACTER menu instead "
                        f"(0x61042CD0 / 0x61174E50) -- measured, not a guess.")
                log(f"{self.peer}   nations served: "
                    f"{[c.get('nation', 0) for c in self.roster[:charlist.LIST_SLOTS]]}"
                    f"{f' (FORCED to {charlist.NATION} by FMO_NATION)' if charlist.NATION else ''}"
                    f" -- 0 is not a nation any screen offers, and it is what "
                    f"switches OFF the client's script substitution at "
                    f"0x61005100 (see the FMO_NATION note)")
                return [packet.build(charlist.MSG_LIST_REPLY, charlist.roster_payload(self.roster),
                                     self.reply_seq(), p["conn"])]
            return [packet.build(charlist.MSG_LIST_REPLY, charlist.list_payload(charlist.LIST_COUNT),
                                 self.reply_seq(), p["conn"])]

        if p["msg"] == handshake.MSG_CREDENTIALS:
            c = packet.parse_credentials(p["payload"])
            if c is None:
                log(f"{self.peer}   WARNING: credentials payload is "
                    f"{len(p['payload'])}B, expected {packet.CRED_LEN} (PC) or "
                    f"{packet.CRED_LEN_PS2} (PS2) -- not decoding it rather than "
                    f"guessing")
                return []
            if c["form"] == "ps2":
                log(f"{self.peer}   credentials: PS2 form, 52B -- the console's "
                    f"sender (midas.pex 0x0030928c) builds the polcore auth "
                    f"blob and NOTHING else: no region field, no identity. "
                    f"Answered with the same 0x0322 the PC gets, because the "
                    f"console's parser at 0x003093a8 reads the identical "
                    f"layout (field A +0x0C, endpoints +0x14/+0x28, 0x3C long)")
            else:
                log(f"{self.peer}   credentials: region {c['region']} "
                    f"({c['region_name']}), identity {c['identity'].hex()}, "
                    f"{len(c['auth'])}B polcore auth blob")
            log(f"{self.peer}   WARNING: the auth blob is NOT verified -- nobody is "
                f"authenticated; we answer anyway to see where the client goes")
            # Logged VERBATIM for the member-binding hunt: the 52-byte blob
            # varies per session and is minted by polcore from its login
            # state. If it turns out to carry the session token our own
            # authserv issued, it names the member with no address heuristic
            # at all -- compare these lines across machines and accounts.
            log(f"{self.peer}   polcore auth blob (verbatim): "
                f"{c['auth'].hex()}")
            # The character store account. Resolved HERE, on the login
            # connection, because the member lookup and the identity both
            # live on this side; every character message rides the game
            # connection that follows -- so stash the RESOLVED key against
            # the address (and in the field-A token) for that one to adopt.
            found = identity.member_for_ip(self.ip)
            if found:
                acct, how = found
                log(f"{self.peer}   character store account = {acct} -- {how}")
                if not charstore.load_roster(acct):
                    legacy = [k for k in identity.store_accounts()
                              if not k.startswith("member:")]
                    if legacy:
                        log(f"{self.peer}   WARNING: {acct} has NO characters, but "
                            f"the store holds legacy roster(s) under "
                            f"{legacy} -- keying changed 2026-08-24 "
                            f"(0x0321 identity -> POL member) because the "
                            f"identity collided across machines. If one of "
                            f"those rosters is this player's, rename its key "
                            f"to '{acct}' in {charstore.CHAR_STORE} (hand-editable by "
                            f"design; it is re-read on every request).")
            elif c["identity"] is not None:
                acct = c["identity"].hex()
                log(f"{self.peer}   WARNING: no POL session row names {self.ip} -- "
                    f"keying the store by the 0x0321 identity {acct}, which "
                    f"is KNOWN to collide across machines against our K=0 "
                    f"login (one shared roster; see the keying note above "
                    f"member_for_ip)")
            else:
                # The console sends no identity, so the second tier does not
                # exist for it -- go straight to the documented third,
                # "addr:<ip>". One roster per address until a POL session row
                # names the member, which is the normal case: FMO can only be
                # launched from a Viewer that just signed in.
                acct = f"addr:{self.ip}"
                log(f"{self.peer}   WARNING: no POL session row names {self.ip} and "
                    f"the PS2 form carries no identity -- keying the store by "
                    f"{acct}. A roster made under this key belongs to the "
                    f"ADDRESS, not the account; it is what the console gets "
                    f"until authsess has a session row for this box")
            identity.remember_identity(self.ip, acct)
            log(f"{self.peer}   (held for {identity.IDENTITY_TTL:.0f}s against "
                f"{self.ip} for the GAME connection to adopt)")
            # 60 zero bytes. Structure known (0x61179cd4), semantics not.
            payload = bytearray(handshake.CRED_REPLY_LEN)
            # payload +0x08 == packet +0x1C == field D == key bytes 16..19.
            payload[0x08:0x0C] = packet.SERVER_KEY_TAIL
            next_host = addressing.host_for(addressing.NEXT_HOST, self.ip)
            ep = addressing.endpoint(next_host, addressing.NEXT_PORT)
            payload[addressing.EP1_OFF:addressing.EP1_OFF + addressing.ENDPOINT_LEN] = ep
            payload[addressing.EP2_OFF:addressing.EP2_OFF + addressing.ENDPOINT_LEN] = ep
            if charstore.SESSION_TOKEN:
                tok = charstore.mint_token(acct, ip=self.ip)
                struct.pack_into("<I", payload, handshake.FIELD_A_OFF, tok)
                _kind = ("the predicted CHARACTER's wire id"
                         if tok < 0x10000 else
                         "a per-login counter value" + (
                             " (the character's id is held by another login "
                             "right now; this session's Battle Review will "
                             "not survive a relog)" if charstore.STABLE_TOKEN
                             else " (FMO_SESSION_TOKEN=counter)"))
                log(f"{self.peer}   field A = session token 0x{tok:08X}, "
                    f"{_kind}. The client sends it straight back as the "
                    f"0x015B payload (correlates the GAME connection to this "
                    f"login without the address heuristic) and records the "
                    f"Battle Review to /btlreview/{tok:x}/, which the Start "
                    f"Game check finds only if it equals the selected id. "
                    f"FMO_SESSION_TOKEN=0 sends 0.")
            log(f"{self.peer}   -> cred-reply 0x0322: REDIRECT to "
                f"{next_host}:{addressing.NEXT_PORT} in both endpoints"
                f"{'' if next_host == addressing.NEXT_HOST else ' (per-client: FMO_NEXT_HOST is ' + addressing.NEXT_HOST + ')'}; key tail "
                f"{packet.SERVER_KEY_TAIL.hex()}")
            log(f"{self.peer}   WARNING: both endpoints get the same address on purpose "
                f"-- endpoint 2's role is NOT known. Pointing them here means a "
                f"working redirect shows up as a RECONNECT to this door.")
            out = [packet.build(handshake.MSG_CRED_REPLY, bytes(payload), self.reply_seq(),
                                self.conn_id)]
            if handshake.PUSH_SESSION_START:
                log(f"{self.peer}   -> session-start 0x0001 (UNPROMPTED -- the "
                    f"client only polls from here). WARNING: THIS ARMS ENCRYPTION "
                    f"client-side; expect its next packet to carry flags 0x0300 "
                    f"and a body we cannot read. That change IS the proof.")
                out.append(packet.build(handshake.MSG_SESSION_START, b"", self.reply_seq(),
                                        self.conn_id))
                # WARNING: ORDER. Both packets in this batch -- the 0x0322 reply and
                # message 1 -- go PLAINTEXT. The client has not armed when it
                # receives them and arms WHILE handling message 1. So request
                # arming here and let the sender do it once these are on the
                # wire; arming now would encipher the very packets that must not
                # be, and mode 1 keystreams never resync.
                self.pending_arm = packet.SERVER_KEY_TAIL
            else:
                log(f"{self.peer}   (FMO_PUSH_SESSION_START=0 -- stopping before "
                    f"the encryption switch)")
            return out

        if p["msg"] == move.MSG_HANGAR_PW:
            _new, _cur = move.parse_hangar_pw(p["payload"])
            log(f"{self.peer}   VERIFIED: 0x0170 = SET HANGAR PASSWORD, {len(p['payload'])}B: "
                f"new={_new!r} (+0x00), current={_cur!r} (+0x10, our 0x014A +0x738). "
                f"The move machine's state 101 polls seq 0x{p['seq']:08X} for message 1.")
            if not move.HANGAR:
                log(f"{self.peer}   FMO_HANGAR=0 -- staying silent (reproduces the "
                    f"2026-09-09 hang on purpose).")
                return []
            _pc = self.playing_char() if charstore.CHAR_STORE else None
            if _pc is not None:
                _pc["hangar_password"] = _new
                self.commit(f"hangar password for {_pc.get('first','')} "
                            f"{_pc.get('last','')} set ({len(_new)} chars)")
                log(f"{self.peer}   -> message 1 on the request's sequence: the "
                    f"client shows 15:5 'The hangar password has been set.' The "
                    f"next 0x014A serves it back at +0x738. WARNING: 'My Hangar' / "
                    f"'Another Player's Hangar' are still unserved doors -- watch "
                    f"for their ids.")
            else:
                log(f"{self.peer}   WARNING: no played character on this session -- "
                    f"acknowledging without storing")
            return [packet.build(handshake.MSG_SESSION_START, b"", p["seq"], p["conn"])]

        if p["msg"] == areachange.MSG_AREA_CHANGE_REQ:
            zone = struct.unpack_from("<H", p["payload"],
                                      areachange.AREA_CHANGE_ZONE_OFF)[0] \
                if len(p["payload"]) >= 2 else 0
            _kind, _idx = divmod(zone, 100)
            log(f"{self.peer}   VERIFIED:VERIFIED: 0x0151 = CHANGE AREA, "
                f"{len(p['payload'])}B: zone {zone} (kind {_kind}, index "
                f"{_idx} -- the D83 row the screen drew). This is the FIRST "
                f"message that screen has ever sent; it polls seq "
                f"0x{p['seq']:08X} and 0x611796EF wants a 0x0153 back.")
            if areachange.AREA_CHANGE == "0":
                log(f"{self.peer}   WARNING: FMO_AREA_CHANGE=0: staying "
                    f"silent. 0x61199E30 keeps returning <= 0 and the client "
                    f"polls forever -- reproduction only.")
                return []
            # \U0001f534 The 600..607 guard -- see AREA_CHANGE_STRICT. Refusing is
            # graceful: 0x611797DE takes any non-0x0153 reply as an error code
            # in our CONN field and advances the machine to state 3.
            if (areachange.AREA_CHANGE_STRICT
                    and areachange.UNSUBSTITUTED_BAND[0] <= zone <= areachange.UNSUBSTITUTED_BAND[1]
                    and zone not in areachange.area_change_destinations()
                    and areachange.zone_mapno(zone, None, "")[0] is None):
                log(f"{self.peer}   \U0001f534 REFUSING zone {zone}: it is in the "
                    f"{areachange.UNSUBSTITUTED_BAND[0]}..{areachange.UNSUBSTITUTED_BAND[1]} band "
                    f"where script_id_for does NOT substitute, so the client "
                    f"would load per-MapKind script {zone} against MapNo "
                    f"{zoneentry.next_mapno()} -- the pairing that CRASHED the client on "
                    f"2026-09-04. Give this zone a destination "
                    f"(FMO_AREA_CHANGE_MAPNO={zone}:<mapno>) or set "
                    f"FMO_AREA_CHANGE_STRICT=0 to grant it anyway.")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            # WARNING: THE PS2 ZONE-PACK GATE (2026-09-21). Unlike the two grants that
            # carry a zone WE chose, this one names the zone the PLAYER picked,
            # so substituting would move them somewhere they did not ask for.
            # Refuse instead, down the same graceful path AREA_CHANGE_STRICT
            # uses: 0x611797DE reads a non-0x0153 reply as an error code and
            # advances the machine to state 3, leaving the player where they are.
            _ps2_zone_no = self.ps2_mapkind_refusal(zone)
            if _ps2_zone_no:
                log(f"{self.peer}   WARNING: REFUSING zone {zone}: {_ps2_zone_no}")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            _host = self.peer.split(":")[0]
            # WARNING: "current" is the map the player is STANDING IN: the last 0x0153
            # this host was granted (WORLD_MAPS), NOT FMO_MAPNO. Until
            # 2026-09-05 this passed next_mapno(), so a player who had Moved to
            # another lobby and then picked an unpaired zone was "kept" in the
            # wrong map -- FMO_MAPNO's, not theirs.
            _cur = rooms.WORLD_MAPS.get(_host, zoneentry.next_mapno())
            _mn, _why = areachange.area_change_mapno(zone, _cur)
            # WARNING: the PS2 gate: this third emit site never had it (2026-09-21)
            _mn = self.build_mapno(_mn, "the Change Area destination")
            log(f"{self.peer}   -> 0x0153 granting MapKind={zone} MapNo={_mn} "
                f"({_why})"
                + f". WARNING: 0x61179716 writes "
                f"{zone} into lobby+0x4F06, so the HUD's 'Area %s' and Change "
                f"Area's own 'you are here' will read {zone}. The DESTINATION "
                f"is MapNo {_mn}, a type-2 lobby map -- the warzone's own "
                f"terrain is one of the 281 TYPE-1 files and is reached "
                f"through the sortie, not through this grant.")
            outs = [packet.build(zoneentry.MSG_0150_REPLY,
                                 zoneentry.reply_0153(mapkind=zone, mapno=_mn,
                                                      host=addressing.host_for(addressing.BATTLE_HOST, self.ip),
                                                      self_id=self.wire_self_id()),
                                 p["seq"], p["conn"])]
            # KEY: MEASURED 2026-09-05 (static, 0x611796EF): after copying the
            # block the Change Area arm DESTROYS the world manager
            # `[0x613CA470]` and the group connection `[0x613CA3F8]` (vtable
            # slot 1 with arg 1, then NULLs both) and only then stores the
            # zone into globals+0x1A0 -- on EVERY 0x0153 it accepts, not only
            # when the MapNo changes. So the world channel restarts on every
            # grant and the restart must be armed unconditionally; the MapNo
            # bookkeeping below is what changes with the destination.
            if True:
                # WARNING: THE THIRD 0x0153 EMIT SITE (found 2026-09-05). World entry
                # and Move both arm the world channel and record the granted
                # MapNo (reset_world_channel) so the self-POP's spawn row
                # (FMO_UDP_POP_POS_MAP) and the room relay follow the player.
                # This grant did neither: an `auto`/paired area change into a
                # DIFFERENT lobby map left WORLD_MAPS on the old map, so the
                # next self-POP took the OLD map's spawn row and the player was
                # grouped with the old room. WARNING: Whether the client re-enters
                # the scene on a 0x0151 grant is UNMEASURED (the poll arm
                # 0x611796EF copies the block; no scene switch was read in it):
                # the "CHANNEL RESTARTED" line is the oracle. With this armed a
                # restart reads as expected; without one the map is recorded a
                # grant early, exactly as the Move path already does.
                rooms.reset_world_channel(_host, "0x0153 served (0x0151 area change)",
                                          mapno=_mn, mapkind=zone,
                                          place=move.entry_place(zone) if move.PLACES else None,
                                          account=self.account)
                self.remember_zone(zone, _mn, "Change Area")
            if zonecontrol.ZONE_CONTROL:
                zpush = zonecontrol.zone_control_push(p["conn"])
                if zpush:
                    log(f"{self.peer}   -> 0x{zonecontrol.MSG_ZONE_CONTROL:04X} ZONE-CONTROL "
                        f"push rides BEHIND this grant too (the third emit "
                        f"site): if the client re-enters the scene on it, the "
                        f"lobby reset zeroes lobby+0x7724 and the NEXT Change "
                        f"Area would refuse every sector; if it does not, this "
                        f"is the same 544 bytes the 0x01AC poll re-fills.")
                    outs.append(zpush)
            # KEY: REMEMBER THE AREA (see AREA_OPEN_OFF): the client asked
            # for it and we granted it, so a permit was used or none was
            # needed -- either way the next 0x014A serves its bit and Change
            # Area moves straight there. The bit only reaches the client at
            # the next login block; this session keeps asking for a permit.
            _apc = (self.playing_char() or None) if charstore.CHAR_STORE else None
            if _apc is not None:
                _ao = [int(z) for z in (_apc.get("areas_open") or [])]
                if zone not in _ao:
                    _apc["areas_open"] = _ao + [zone]
                    # KEY: AND SPEND THE PASS the client just spent on screen,
                    # then push the new owned block (bit set, pass gone) once
                    # the scene is up -- see the keepalive's area push.
                    _sp = self.spend_area_pass(_apc, zone)
                    _pend = getattr(self, "area_push_pending", None) or {
                        "spent": [], "zones": []}
                    _pend["spent"] = _pend["spent"] + _sp
                    _pend["zones"] = _pend["zones"] + [zone]
                    _pend["at"] = time.time()
                    self.area_push_pending = _pend
                    try:
                        self.commit(f"area {zone} opened by Change Area")
                        log(f"{self.peer}   area {zone} is now OPEN for this "
                            f"pilot (areas_open {_apc['areas_open']}): from the "
                            f"next login its row bit in owned+0x00 is set and "
                            f"Change Area skips the permit prompt.")
                    except Exception as _e:
                        log(f"{self.peer}   WARNING: opened area NOT persisted ({_e!r})")
            return outs

        if p["msg"] in battlegroups.GROUP_ACK_IDS:
            # KEY: THE GROUP REQUEST FAMILY (LIVE 2026-09-05 22:26Z): right after
            # the 0x0158 group entry the client sent 0x0173 (13 B: u32 GroupID,
            # then bytes 00 02 02 00 ..) on the push sequence and we had no
            # handler. Its request object (0x6116CFF0, vtable 0x6133ABF0) is
            # one of a family -- 0x6116D0A0 sends 0x0171 (0x14 B), 0x6116D120
            # sends 0x0179 (0x10 B) -- whose shared update 0x6116DD60 sends via
            # 0x61173EC0, polls 0x61173F40 and requires the reply's word[+6] ==
            # **1** (a bare ack) before posting its done event through
            # vtable+0xA0; any other id posts the error event. So each is
            # answered with an empty message 1 on the client's own sequence,
            # exactly the 0x0159 / MSG_SESSION_START convention. Bodies are
            # logged raw: their meaning (ready state? member op?) is the next
            # decode, together with whatever the client does once acked.
            _b = p["payload"]
            _gid = struct.unpack_from("<I", _b, 0)[0] if len(_b) >= 4 else None
            log(f"{self.peer}   0x{p['msg']:04X} = "
                f"{battlegroups.GROUP_OP_NAMES.get(p['msg'], 'a GROUP request')} ({len(_b)}B, "
                f"u32[0]={_gid} = GroupID?, bytes {_b[:16].hex(' ')}) -> message 1 "
                f"on its seq (the object's poll wants word[+6] == 1).")
            if p["msg"] == 0x01A0 and len(_b) >= 8:
                # B.G.BONUS (battlegroups.bonus_request): leader only, raise
                # only, cap FMO_BG_BONUS_MAX; a refusal is 7:64 + the code's line.
                _amt = struct.unpack_from("<I", _b, battlegroups.Q1A0_AMOUNT)[0]
                _no = battlegroups.bonus_request(_gid, self.account, _amt)
                if _no is not None:
                    log(f"{self.peer}   B.G.BONUS H$ {_amt} REFUSED: {_no[1]} -> "
                        f"0x{charselect.MSG_FAIL:04X} code {_no[0]}")
                    return [packet.build(charselect.MSG_FAIL, b"", p["seq"], _no[0] & 0xFFFF)]
                log(f"{self.peer}   B.G.BONUS: group {_gid} bonus is now H$ {_amt}")
            if (p["msg"] in (0x0171, 0x0179) and len(_b) >= 8
                    and battlegroups.GROUP_MEMBER_OPS):
                # KICK / CHANGE LEADER: +0x00 = the chosen member as THIS
                # client knows it (our alias), +0x04 = the GroupID.
                _alias, _mgid = struct.unpack_from("<II", _b, battlegroups.Q171_TARGET)
                _tacct = groupchannel.group_member_by_alias(self.account, _alias)
                _op = (battlegroups.group_kick if p["msg"] == 0x0171
                       else battlegroups.group_change_leader)
                _no = _op(_mgid, self.account, _tacct)
                if _no is not None:
                    log(f"{self.peer}   {battlegroups.GROUP_OP_NAMES[p['msg']]} of "
                        f"UnitID {_alias:#x} ({_tacct or 'unresolved'}) in group "
                        f"{_mgid} REFUSED: {_no[1]} -> 0x{charselect.MSG_FAIL:04X} "
                        f"code {_no[0]}")
                    return [packet.build(charselect.MSG_FAIL, b"", p["seq"], _no[0] & 0xFFFF)]
            if p["msg"] == 0x0172:
                # LEAVE: a member leaves; the leader's leave disbands (D92 214-215).
                _lg = groupchannel.GROUP_OF.get(self.account)
                if _lg and battlegroups.group_leader(_lg) == self.account:
                    battlegroups.group_disband(_lg, f"its leader {self.account} left")
                else:
                    _lg = groupchannel.group_leave(self.account)
                    log(f"{self.peer}   LEAVE: {self.account} left group {_lg}")
            if p["msg"] == 0x0173 and len(_b) >= 8:
                # KEY: THE SORTIE SETTING (sender 0x6116CFF0): +0x04 1 = Ready,
                # 2 = Standing By; +0x07 1 = Continue, 2 = Do not continue;
                # 0 = unchanged. The client SHOWS it from its own member blob
                # (+0x54 bit 1 / bit 8), so it only changes once a cmd 191
                # rewrites that blob -- sent here to every member's channel.
                _old = groupchannel.GROUP_READY.get(self.account, (0, 0))
                _rd = _b[4] or _old[0]
                _ct = _b[7] or _old[1]
                groupchannel.GROUP_READY[self.account] = (_rd, _ct)
                _nq = groupchannel.group_push_flags(self.account)
                log(f"{self.peer}   SORTIE SETTING: {self.account} is "
                    f"{'READY' if _rd == 1 else 'STANDING BY' if _rd == 2 else 'unset'}"
                    f", continuation {'on' if _ct == 1 else 'off' if _ct == 2 else 'unset'} "
                    f"-> cmd 191 queued on {_nq} group channel(s)")
            return [packet.build(handshake.MSG_SESSION_START, b"", p["seq"], p["conn"])]

        if p["msg"] == battlegroups.MSG_GROUP_COMMENT_REQ:
            # WARNING: THE ONE IN THIS FAMILY THAT IS NOT AN ACK. See GROUP_ACK_IDS:
            # 0x6116E164 is `cmp word [eax+6], 0x191 / jne`, and the jne arm
            # posts the FAILURE event with word[frame+8] as its code. Message 1
            # here (what we sent from 2026-09-06 until now) un-hangs the dialog
            # and then fails it every time.
            _b = p["payload"]
            _gid = struct.unpack_from("<I", _b, 0)[0] if len(_b) >= 4 else None
            log(f"{self.peer}   0x{battlegroups.MSG_GROUP_COMMENT_REQ:04X} = "
                f"{battlegroups.GROUP_OP_NAMES[battlegroups.MSG_GROUP_COMMENT_REQ]} ({len(_b)}B, "
                f"u32[0]={_gid}) -> 0x{battlegroups.MSG_GROUP_COMMENT_REPLY:04X}, EMPTY, on "
                f"its own seq. 0x6116E164 tests the id and nothing reads the "
                f"body; the window it then opens takes its text from the "
                f"client's own group record at [[0x613AE664]+0x198]+0x4C, which "
                f"we do not fill -- so the dialog should OPEN, and whether it "
                f"has any text in it is a separate question.")
            return [packet.build(battlegroups.MSG_GROUP_COMMENT_REPLY, b"", p["seq"], p["conn"])]

        if p["msg"] in (shop.MSG_ITEM_SELL, shop.MSG_ITEM_BUY):
            # THE ITEM COUNTER. See MSG_ITEM_SELL for why these two siblings are
            # answered oppositely: the sell price is on the wire and the buy
            # price is not.
            _b = p["payload"]
            _mode, _price, _serial, _iid, _kind = shop.parse_item_request(_b)
            _buy = p["msg"] == shop.MSG_ITEM_BUY
            _what = ("BUY" if _buy else
                     ("DISCARD" if _mode == 1 else "SELL"))
            log(f"{self.peer}   0x{p['msg']:04X} = ITEM {_what} ({len(_b)}B): "
                f"serial 0x{_serial:016X}, id {_iid}, kind {_kind}, "
                f"mode byte {_mode} at record+0x{shop.S169_REC_MODE:02X} "
                f"(1 = discard), price field {_price} at "
                f"payload+0x{shop.S169_PRICE:02X}.")
            _mode_knob = shop.ANSWER_017E if _buy else shop.ANSWER_0169
            if _mode_knob == "0":
                log(f"{self.peer}   WARNING: FMO_ANSWER_{'017E' if _buy else '0169'}"
                    f"=0: staying silent -- the counter polls forever and the "
                    f"client hangs. That is what it did before today; kept for "
                    f"the A/B.")
                return []
            if _buy and _mode_knob != "1":
                log(f"{self.peer}   -> message 2 (a WRONG id, deliberately): "
                    f"0x61179383's jne takes 0x611793CA, which puts our "
                    f"word[+8] in lobby+0x7D5F as a numbered [FM...] code and "
                    f"leaves state 3. KEY: The debit at 0x61179373 lives INSIDE "
                    f"the 0x{shop.MSG_ITEM_BUY_REPLY:04X}-matched path, so no money "
                    f"moves. We refuse because the BUY price is never sent "
                    f"(it is [record+0x18], one dword past the 24 the client "
                    f"copies), so we cannot charge for this -- and a 0x017F "
                    f"here would debit the client's own display for an item we "
                    f"hold no stock of, then be undone by the next 0x014A. "
                    f"FMO_ANSWER_017E=1 to let it through anyway.")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            if _buy:
                log(f"{self.peer}   WARNING: FMO_ANSWER_017E=1 -> "
                    f"0x{shop.MSG_ITEM_BUY_REPLY:04X}: the client will subtract "
                    f"[record+0x18] from its OWN wallet, a figure we were never "
                    f"sent and do not store. Expect the wallet to snap back on "
                    f"the next 0x014A.")
                return [packet.build(shop.MSG_ITEM_BUY_REPLY, bytes(0x14),
                                     p["seq"], p["conn"])]
            if _mode_knob != "1":
                log(f"{self.peer}   -> message 2 (FMO_ANSWER_0169=fail): "
                    f"0x6117A90F, a numbered refusal; the item is NOT removed.")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            outs = [packet.build(handshake.MSG_SESSION_START, b"", p["seq"], p["conn"])]
            log(f"{self.peer}   -> message 1: 0x6117A893 accepts it, the client "
                f"calls 0x6117A640 to drop the serial from its owned list and "
                f"prints SE's "
                + ("8:73 'Discarded %s.'" if _mode == 1 else "8:33 'Sold %s.'")
                + ".")
            # The item leaves the pilot's stored list too (acquired items;
            # a serial that came from the setups block is not ours to drop).
            _pc_item = (self.playing_char() or None) if charstore.CHAR_STORE else None
            if _pc_item is not None:
                if inventory.remove_stored_item(_pc_item, _serial):
                    try:
                        self.commit(f"{'discarded' if _mode == 1 else 'sold'} "
                                    f"acquired item {_iid} (kind {_kind}) serial "
                                    f"0x{_serial:016X}")
                    except Exception as _e:
                        log(f"{self.peer}   WARNING: item removal NOT persisted ({_e!r})")
                    log(f"{self.peer}   the serial was an ACQUIRED item on the "
                        f"pilot -- removed; {len(inventory.stored_items(_pc_item))} left")
                else:
                    log(f"{self.peer}   the serial is not among the pilot's "
                        f"acquired items (a setups-block record, or minted "
                        f"before items were kept) -- nothing to remove here")
            if _mode != 1 and _price:
                # KEY: THE CLIENT NEVER CREDITS ITSELF on a sale -- no `add
                # [lobby+0x88C]` exists in that arm -- so the money is ours to
                # pay, and the price is one of the few figures the client
                # actually hands us.
                _paid = self.credit_money(f"sold item {_iid} (kind {_kind})",
                                          money=_price)
                if _paid is not None and resultpush.RESULT_PUSH:
                    # WARNING: the 0x015A arm copies +0x598 onto lobby+0x8C8 FIRST,
                    # and that is the owned table WITH the script flags: a
                    # money-only push must carry the pilot's own slice or it
                    # wipes them (the 09-11 tutorial-replay bug).
                    _owned = status.reply_014a(char=_pc_item or {})[status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN]
                    outs.append(resultpush.result_push_packet(
                        p["conn"], money=_price, contribution=0, owned=_owned,
                        pilot=_pc_item or {}))
                    log(f"{self.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} money-only "
                        f"push (+{_price}) so the wallet moves NOW rather than "
                        f"at the next login. Rides FMO_RESULT_PUSH because it "
                        f"is the same unproven transport.")
                elif _paid is not None:
                    log(f"{self.peer}   the {_price} is BANKED but not pushed "
                        f"(FMO_RESULT_PUSH=0): it appears at the next 0x014A, "
                        f"i.e. the next login, not on this screen.")
            return outs

        if p["msg"] == battlegroups.MSG_0157_REQ:
            # VERIFIED: JOIN AN EXISTING BATTLE GROUP -- see MSG_0157_REQ. Unserved
            # until 2026-09-09, so the join dialog hung on our silence exactly
            # like CREATE did before 09-05.
            _b = p["payload"]
            _gid = struct.unpack_from("<I", _b, 0)[0] if len(_b) >= 4 else 0
            # WARNING: NOT an empty body -- see MSG_0157_REQ. The poller reads only
            # the id, but the board's event handler reads payload+0x00 (the
            # "on a sortie" flag) and the Battle Map Information panel out of
            # the same reply, and an empty body left those reading the receive
            # buffer's previous contents.
            _j = bytearray(battlegroups.REPLY_0158_JOIN_LEN)
            if community.GROUP_BOARD_MARK:
                # Same instrument as the board row, same knob, because it is
                # the same screen: the "Battle Map Information" panel (9:33)
                # and the "Player List" (10:49) are both built on THIS reply,
                # from payload+0x14 (108 B) and payload+0x590 (216 B). Marking
                # makes each of 10:41..10:46 (Danger Level, B.G.Bonus, Total
                # B.G.Cost, Battles Left, Required B.G.Cost) name its own
                # offset instead of reading a plausible zero.
                for _o in range(0, battlegroups.REPLY_0158_JOIN_LEN - 3, 4):
                    struct.pack_into("<I", _j, _o, _o)
            # WARNING: ALWAYS zero, marked or not: a non-zero here is the phantom
            # sortie, and a marked run must not drag the player into a battle.
            _j[battlegroups.S158_ON_SORTIE] = 0
            struct.pack_into("<I", _j, battlegroups.S158_SECTOR, battlegroups.JOIN_SECTOR & 0xFFFFFFFF)
            # KEY: THE PLAYER LIST: u32 count at
            # +0x80, 0x40-byte rows from +0x84. Zeros drew "[NO PLAYER]"
            # (10:48) -- the rows are the group's members, joiner included.
            # SE's join rules (groupchannel.group_join_refusal): a member of
            # another live group, or a full group, is refused with the
            # client's own code (9:3 + 9:0 / 5:33), and a B.G.Cost below the
            # group's Required B.G.Cost (5:37) -- the PLAYING pilot's cost,
            # noted here so the rule and the platoon total read that pilot.
            battlegroups.note_pilot_cost(self.account, self.playing_char()
                                         if charstore.CHAR_STORE else None)
            _no = (penalty.clearance_refusal(self.playing_char() if charstore.CHAR_STORE
                                             else None, "platoon")
                   or groupchannel.group_join_refusal(_gid, self.account))
            if _no is not None:
                log(f"{self.peer}   JOIN group {_gid} REFUSED: {_no[1]} -> "
                    f"0x{charselect.MSG_FAIL:04X} code {_no[0]}")
                return [packet.build(charselect.MSG_FAIL, b"", p["seq"], _no[0] & 0xFFFF)]
            groupchannel.group_join(_gid, self.account)
            groupchannel.queue_group_entry(self.ip, self.account)   # its group channel claims it
            if not community.GROUP_BOARD_MARK:
                _pn, _prows = groupchannel.group_player_rows(_gid)
                struct.pack_into("<I", _j, groupchannel.S158_PLAYERS_N, _pn)
                _j[groupchannel.S158_PLAYERS:groupchannel.S158_PLAYERS + len(_prows)] = _prows
                log(f"{self.peer}   0x0158 Player List: {_pn} member(s) of group "
                    f"{_gid} at +0x80/+0x84 -> {groupchannel.GROUP_MEMBERS.get(_gid)}")
            log(f"{self.peer}   0x{battlegroups.MSG_0157_REQ:04X} = JOIN BATTLE GROUP "
                f"({len(_b)}B): GroupID {_gid} at payload+0x00 (the board "
                f"window's +0x19A). -> 0x{battlegroups.MSG_0158_REPLY:04X}, "
                f"{battlegroups.REPLY_0158_JOIN_LEN}B, on its own seq. payload+0x00 = 0 = "
                f"NOT on a sortie: 0x61181ABF reads it and a non-zero puts "
                f"[board+0x270] non-zero, which 0x611816F0 turns into systext "
                f"9:10 'The battle group is on a sortie' -- that is what an "
                f"EMPTY body did on 2026-09-09, and saying yes to it sent a "
                f"real 0x0139 into map 418. The rest is zeros, which is what "
                f"the Battle Map Information panel (9:33) already shows.")
            outs = [packet.build(battlegroups.MSG_0158_REPLY, bytes(_j), p["seq"], p["conn"])]
            # Without this the join is INERT: the dialog closes and the player
            # is attached to nothing, because -- unlike CREATE -- the join reply
            # carries no LoginGroup entry. 0x0174 is the push that does it.
            if grouplogin.GROUP_ATTACH and _gid:
                try:
                    # host_for: a GROUP_HOST on a private network is unreachable
                    # for an internet client, which then never sends a single
                    # group datagram, so its member list stays empty.
                    outs.append(grouplogin.group_attach_packet(
                        p["conn"], _gid, host=addressing.host_for(addressing.GROUP_HOST, self.ip)))
                    log(f"{self.peer}   -> 0x{grouplogin.MSG_GROUP_ATTACH:04X} GROUP "
                        f"ATTACH push, {grouplogin.G174_BODY_LEN}B on queue seq "
                        f"0x{pushes.QUEUE_SEQ:08X}: GroupID {_gid}, type {addressing.GROUP_TYPE}, "
                        f"endpoint {addressing.GROUP_HOST}:{addressing.GROUP_PORT} at payload+0x04 "
                        f"(NETWORK order -- FMO's datagram sender does not "
                        f"swap). The client should tear down [0x613CA3F8] and "
                        f"LoginStart() an FmoGroup at that endpoint, and say so "
                        f"in its own log as 'LoginGroup GroupID=%d ...'. "
                        f"FMO_GROUP_ATTACH=0 withdraws this and leaves the join "
                        f"answered but inert.")
                except ValueError as _e:
                    log(f"{self.peer}   WARNING: 0x{grouplogin.MSG_GROUP_ATTACH:04X} not sent: {_e}")
            elif grouplogin.GROUP_ATTACH:
                log(f"{self.peer}   WARNING: no GroupID in the join body, so no "
                    f"0x{grouplogin.MSG_GROUP_ATTACH:04X} attach: the arm bails on a zero "
                    f"id anyway. The join is answered but inert.")
            return outs

        if p["msg"] == battlegroups.MSG_0156_REQ:
            # KEY: 0x0156 -> 0x0158: CREATE BATTLE GROUP, from the Scramble Board
            # window the counter script opens (LIVE 2026-09-05 22:11Z, first
            # ever: 312 B, `no handler`, the client hung). Sender object
            # 0x61180960: body 0x124 B at obj+0x34, sent by 0x61173EC0; state 1
            # polls 0x61173F40 and on `word[+6] == 0x158` posts UI event 0x10CD
            # (created) reading NOTHING else; any other id posts 0x10CE with the
            # reply's word[+8] as the error code; a poll error posts 0x10CE/-1.
            # Body (live): +0x00 u32 0, +0x04 u32 (a client stamp), +0x08 the
            # leader's "first.last" (NUL-padded), +0x58 the comment text,
            # +0x110 u32 = Total Battles, +0x114 bytes = the create form's other
            # two fields (live 03 01: Required B.G. Cost 3? continue 1?), rest 0.
            # Nothing here stores or lists the group yet: the 0x01AD block and
            # the board list are the next decode; this only stops the hang and
            # records what was asked for.
            _b = p["payload"]
            _leader = _b[0x08:0x58].split(b"\0")[0].decode("ascii", "replace")
            _comment = _b[0x58:0x110].split(b"\0")[0].decode("cp932", "replace")
            # CORRECTED 2026-09-30: +0x110 is the voice flag; Total Battles is
            # the byte at +0x114 (battlegroups.parse_create_form).
            _form = battlegroups.parse_create_form(_b)
            _total = _form["total"]
            _f114 = _b[0x114:0x118].hex(" ") if len(_b) >= 0x118 else "?"
            battlegroups.BATTLE_GROUPS[self.peer] = {"leader": _leader, "comment": _comment,
                                                     "total_battles": _total, "f114": _f114,
                                                     "at": time.time()}
            # KEY: RE-SEND THE MEMBER-INFO ON A NEW GROUP. The group UDP channel
            # is keyed (host, 19155, "group") and 19155 is stable across client
            # restarts, so the channel object -- and its group_popped guard --
            # SURVIVES a relog (live 2026-09-06 02:37Z: a second CREATE got no
            # member-info re-send and the window stayed grey). Clear the flag and
            # drop any stale member-info so the next group datagram re-attaches
            # the blob to the fresh self-peer.
            for _gk, _gc in list(groupchannel.WORLD_PEERS.items()):
                if (isinstance(_gk, tuple) and len(_gk) == 3 and _gk[2] == "group"
                        and _gk[0] == self.ip):
                    _gc.group_popped = False
                    log(f"{self.peer}   reset group channel {_gk} for the new "
                        f"group -- member-info will re-send on its next datagram")
            log(f"{self.peer}   0x0156 CREATE BATTLE GROUP ({len(_b)}B): leader "
                f"{_leader!r}, comment {_comment!r}, total battles {_total}, "
                f"+0x114 = {_f114}. FMO_ANSWER_0156={battlegroups.ANSWER_0156!r}.")
            battlegroups.note_pilot_cost(self.account, self.playing_char()
                                         if charstore.CHAR_STORE else None)
            _no = (penalty.clearance_refusal(self.playing_char() if charstore.CHAR_STORE
                                             else None, "platoon")
                   or groupchannel.group_create_refusal(self.account))
            if _no is not None and battlegroups.ANSWER_0156 not in ("0", "fail", "ack"):
                log(f"{self.peer}   CREATE REFUSED: {_no[1]} -> "
                    f"0x{charselect.MSG_FAIL:04X} code {_no[0]} (9:5 + 9:0)")
                return [packet.build(charselect.MSG_FAIL, b"", p["seq"], _no[0] & 0xFFFF)]
            if battlegroups.ANSWER_0156 == "0":
                log(f"{self.peer}   WARNING: staying silent: the window polls forever (the hang).")
                return []
            if battlegroups.ANSWER_0156 == "fail":
                log(f"{self.peer}   -> message 2 (id != 0x158): the window posts UI event "
                    f"0x10CE with word[+8] = the error code.")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            if battlegroups.ANSWER_0156 == "ack":
                log(f"{self.peer}   -> 0x0158, EMPTY (FMO_ANSWER_0156=ack): the "
                    f"window posts 0x10CD and then reads a group entry out of the "
                    f"empty body -- LIVE 22:19Z: nothing followed on the wire.")
                return [packet.build(battlegroups.MSG_0158_REPLY, b"", p["seq"], p["conn"])]
            # KEY: THE REPLY BODY IS READ -- by the 0x10CD handler (0x61182391), not
            # by the poller: payload+0x00 (u32), +0x0C and +0x20 go to
            # 0x611778D0 = 0x61177620(id, type 1, endpoint, block88), the SAME
            # per-entry builder the 0x0155 LoginGroup list feeds (group_entry):
            # it tears down the group connection object [0x613CA3F8], creates an
            # FmoGroup, Init(), LoginStart() against the ENDPOINT (+0x0C: port
            # at +2, address at +4, byte-reversed), prints "LoginGroup GroupID=%d
            # Type=%d Addr=%s:%d" and registers the `/bg` chat command. So the
            # created group is a LOGIN to a group server -- SE ran one; we point
            # it at ourselves (FMO_GROUP_HOST:FMO_GROUP_PORT, default this door)
            # and the group connection's first message lands in this log as the
            # next decode. The 88-B block (+0x20 -> globals+0x128) is the same
            # unidentified structure 0x0153 carries at +0x124; zeros, like there.
            _gid = len(battlegroups.BATTLE_GROUPS_MADE) + 1
            battlegroups.BATTLE_GROUPS_MADE.append((self.peer, _gid, _leader, time.time()))
            battlegroups.GROUP_CREATOR_ACCOUNT[_gid] = self.account
            _pst = battlegroups.register_group(_gid, self.account, _form)
            log(f"{self.peer}   group {_gid}: Total Battles {_pst['total']}, Required "
                f"B.G.Cost {_pst['required']}, B.G.Bonus H$ {_pst['bonus']}")
            groupchannel.group_join(_gid, self.account)
            groupchannel.queue_group_entry(self.ip, self.account)   # its group channel claims it
            _entry = grouplogin.group_entry(_gid, addressing.host_for(addressing.GROUP_HOST, self.ip),
                                            addressing.GROUP_PORT, 1)
            log(f"{self.peer}   -> 0x0158, {len(_entry)}B = a LoginGroup entry: "
                f"GroupID={_gid} Type=1 endpoint {addressing.GROUP_HOST}:{addressing.GROUP_PORT} (FMO_GROUP_HOST/"
                f"_PORT), block88 zeros. The client now tears down [0x613CA3F8] and "
                f"LoginStart()s an FmoGroup against that endpoint -- whatever it sends "
                f"first on that connection is the group protocol's hello.")
            return [packet.build(battlegroups.MSG_0158_REPLY, _entry, p["seq"], p["conn"])]

        if p["msg"] == battlemaps.MSG_0162_REQ:
            # KEY: 0x0162 -> 0x0163: the Scramble Board's TRAINING SECTOR entry
            # (see MSG_0162_REQ). The reply body is not read; the id alone fires
            # UI event 0x10D1. What the board does on 0x10D1 is the next capture.
            _b = p["payload"]
            log(f"{self.peer}   0x0162 = TRAINING SECTOR (Scramble Board, sender "
                f"0x61180C40), {len(_b)}B: {_b.hex(' ')}. FMO_ANSWER_0162={battlemaps.ANSWER_0162!r}.")
            if battlemaps.ANSWER_0162 == "0":
                log(f"{self.peer}   staying silent: the board polls forever (the hang).")
                return []
            if battlemaps.ANSWER_0162 == "fail":
                log(f"{self.peer}   -> message 2 (id != 0x163): UI event 0x10D2, "
                    f"word[+8] = the code.")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            # KEY: CORRECTED 2026-09-27: 0x0162 is the
            # board's GET BATTLE GROUP INFO (row select 0x611812A0, payload+0x00
            # = the GroupID), not a training sector. 0x0163 feeds the Player
            # List ("[NO PLAYER]" when empty -- the creator's and a pre-join
            # viewer's), the Battle Map panel and the on-sortie flag that
            # offers 9:10. The client re-asks on every row click.
            _qg = struct.unpack_from("<I", _b, 0)[0] if len(_b) >= 4 else 0
            _gid = _qg or groupchannel.GROUP_OF.get(self.account) or 0
            _body = groupchannel.reply_0163(_gid)
            log(f"{self.peer}   -> 0x0163 GROUP INFO for group {_gid}: "
                f"{struct.unpack_from('<I', _body, groupchannel.S158_PLAYERS_N)[0]} member(s) "
                f"{groupchannel.GROUP_MEMBERS.get(_gid)}"
                + (f", ON A SORTIE to map {groupchannel.GROUP_SORTIE[_gid]['map']} (9:10 offers "
                   f"to follow)" if _body[groupchannel.S163_ON_SORTIE] else ", not on a sortie"))
            return [packet.build(battlemaps.MSG_0163_REPLY, _body, p["seq"], p["conn"])]

        if p["msg"] == battlemaps.MSG_01F4_REQ:
            # KEY: 0x01F4 -> 0x01F5: the war-map window's BATTLE MAP LIST (see
            # MSG_0162_REQ's note for the parse). Count 0 = "No Battle Map".
            _body = battlemaps.reply_01f5()
            _rows = ("(FMO_BATTLE_MAPS unset: SE shows 10:38 No Battle Map)" if not battlemaps.BATTLE_MAPS
                     else "rows " + ", ".join(f"{r}:{k:#x}" for r, k in battlemaps.BATTLE_MAPS))
            log(f"{self.peer}   0x01F4 = the WAR MAP's battle-map list request "
                f"({len(p['payload'])}B; ctor 0x61184AC0 sends it with 0x01F6) -> "
                f"0x01F5, {len(_body)}B: count {len(battlemaps.BATTLE_MAPS)} {_rows}. "
                f"Row = u16 id, u8 kind (& 0xF must equal the window mode to draw).")
            return [packet.build(battlemaps.MSG_01F5_REPLY, _body, p["seq"], p["conn"])]

        if p["msg"] == battlemaps.MSG_01F6_REQ:
            _b = p["payload"]
            log(f"{self.peer}   0x01F6 = the DEBUG Lobby Menu's 136-B detail/create "
                f"form (ctor 0x61184AA0, body window+0x2639), {len(_b)}B: {_b.hex(' ')} "
                f"-> message 1 on its seq (poller 0x61185A9E compares the id only).")
            return [packet.build(handshake.MSG_SESSION_START, b"", p["seq"], p["conn"])]

        if p["msg"] in (warmap.MSG_015E_REQ, warmap.MSG_0160_REQ):
            # KEY: THE REAL WAR MAP's two requests -- see MSG_015E_REQ. Answering
            # them EMPTY (count 0) is the deliberate first move: it keeps the
            # screen off the silence-park and puts the request bytes in the log,
            # which is where the body layout comes from.
            _b = p["payload"]
            _is5e = p["msg"] == warmap.MSG_015E_REQ
            _name = ("0x015E = BATTLE MAP INFORMATION (sender 0x611883E0)" if _is5e
                     else "0x0160 = PLAYER INFORMATION (sender 0x61188480)")
            _mode = warmap.ANSWER_015E if _is5e else warmap.ANSWER_0160
            _rep = warmap.MSG_015F_REPLY if _is5e else warmap.MSG_0161_REPLY
            _fail = "10:4 Failed to get the battle map information" if _is5e else \
                    "10:5 Failed to get the player information for the battle map"
            log(f"{self.peer}   {_name}, {len(_b)}B: {_b.hex(' ')}. "
                f"WARNING: the WAR MAP IS OPEN -- this is the first time this screen has "
                f"been reached. FMO_ANSWER_{'015E' if _is5e else '0160'}={_mode!r}.")
            if _mode == "0":
                log(f"{self.peer}   staying silent: the war map polls forever (the park).")
                return []
            if _mode == "fail":
                log(f"{self.peer}   -> message 2 (id != {_rep:#06x}): the client's "
                    f"graceful error arm, systext {_fail}.")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            # 0x015E only: FMO_WARMAP_MAPS puts real rows in the battle-map
            # list. The row's first dword becomes the list item's value
            # ([item+0x60] at 0x6118EA34), which arm 3 copies to
            # [warmap+0x5A10] and arm 4 sends as 0x0139's battle-map id -- so
            # this is the game's OWN door into scene 4. 0x0161's row stride is
            # NOT read, so that one stays empty whatever this is set to.
            # VERIFIED:KEY: THE SECTOR THE CLIENT ASKED FOR. See FMO_WARMAP_SECTORS.
            # The +0x00 u32 is the ARE table's `tile`; (MAPKIND, tile) names
            # one sector and its battlefield. A miss falls back to the flat
            # list, an empty sector answers count=0, and either way the reason
            # is logged -- "the same place every time" was invisible precisely
            # because this request was never read.
            _rows = warmap.warmap_rows() if _is5e else []
            _srcnote = None
            if _is5e:
                _tile, _srow, _smap, _ondisk, _srcnote = warmap.warmap_sector_for(
                    _b, selector=warmap.warmap_selector(self.ip))
                if _ondisk:
                    _rows = warmap.warmap_rows([_smap])
                    _sel = warmap.warmap_selector(self.ip)
                    self.sector = (_tile, _srow, _smap)
                    self.sector_zone = _sel      # the grid the tile is in
                    _icon = 0
                    if self.mission_map_icon(_tile, _sel):
                        _icon = warmap.MISSION_MAP_ICON      # SE's boxed "M" (live)
                    elif self.counter_mission_icon(_tile, _sel, _smap):
                        _icon = warmap.COUNTER_MISSION       # the arrow (a guess)
                    if _icon:
                        _rows = [bytearray(r) for r in _rows]
                        for _r in _rows:
                            _r[warmap.S15F_ICON] = _icon
                    if warmap.WARMAP_ENTRY:
                        _rows = [self.warmap_entry(bytearray(r)) for r in _rows]
                elif _srow is not None:
                    _rows = []                     # a real sector, no battle
                    self.sector = None
                log(f"{self.peer}   sector lookup: {_srcnote}")
            _body = warmap.reply_warmap_list(0, _rows)
            if _rows:
                log(f"{self.peer}   -> {_rep:#06x}, {len(_body)}B, count={len(_rows)}: "
                    f"battle map id(s) "
                    f"{', '.join(str(struct.unpack_from('<I', r)[0]) for r in _rows)} at "
                    f"each row's +0x00 (108-B rows from +0x14). Picking one sends "
                    f"0x0139 with that id (arm 3 -> [warmap+0x5A10] -> arm 4); "
                    f"FMO_SORTIE={'on' if sortie.SERVE_SORTIE else 'OFF -- it will be refused'}."
                    f" WARNING: only the first dword of a row is established; the other "
                    f"104 bytes are zero.")
            else:
                log(f"{self.peer}   -> {_rep:#06x}, {len(_body)}B, count=0 (u32 at +0x00; "
                    f"rows would start at +0x14). The row loop is guarded by count > 0, "
                    f"so this cannot walk off the end."
                    + (" Set FMO_WARMAP_MAPS=418 to put a pickable battle map in "
                       "the list -- that is the retail sortie door." if _is5e else ""))
            return [packet.build(_rep, _body, p["seq"], p["conn"])]

        if p["msg"] == servicerecord.MSG_0175_REQ:
            # KEY: 0x0175 -> 0x0176: THE SERVICE-RECORD / PROMOTION EXCHANGE (LIVE
            # 2026-09-05 21:44Z: a counter operator said "Checking the service
            # record data from your wanzer", the client sent an EMPTY 0x0175 and
            # hung on our silence -- `no handler for msg=0x0175`). Sender = the
            # mission-result machine 0x61192780 (state 0: 0x61199FC0(0x175),
            # state 1: polls 0x61173F40). On a reply whose word[+6] == 0x176 it
            # copies 476 B from packet+0x34 (= payload+0x20) into itself and
            # applies: +0x04 and +0x08 rank indices (each clamped below the rank
            # table's count [ranktable+0x1C]; +0x04 is shown, +0x08 is stored
            # at lobby+0xFC8), byte +0x0C -> status+0x30, byte +0x0D -> status
            # +0x2F = THE RANK (S14A_RANK), byte +0x0F -> systext 0xC0240000+n
            # = the rank NAME it prints to chat via 0x61175540/0x61184330, u32
            # +0x10 -> 0x611754D0 = a money amount formatted into lobby+0x7B7C.
            # Then state 3 builds the City Control window (0x610E86E0) and
            # state 5 the result screen (0x611924A0) from the same block. Any
            # OTHER reply id takes the graceful arm 0x61192A26 (an error box
            # with the reply's word[+8]); silence is the hang.
            #
            # WARNING: CORRECTED 2026-09-11 (the arm at 0x611928F5..0x61192A25):
            # +0x04 and +0x08 are NOT rank indices. Both are compared against
            # `[ranktable_row(byte[lobby+0x7E09]) + 0x1C]` -- the CONTRIBUTION
            # THRESHOLD column of a D15 rank row (the row layout is
            # "%d(28ci56ci4c7i)": 28 chars of name, then the threshold at
            # +0x1C) -- and clamped one below it; +0x04 lands in the machine's
            # +0x218 (the number it shows), +0x08 in lobby+0xFC8 = the
            # CONTRIBUTION. So the block is {contribution shown, contribution
            # stored, status+0x30 byte, RANK byte, rank-name index, money}:
            # SE's Personnel Officer read the service record and PROMOTED
            # from it. Until this fix the reply carried the GLOBAL FMO_RANK
            # knob in every slot -- a pilot whose stored rank differed had it
            # overwritten on the client by this exchange, and +0x08 put the
            # rank number on the contribution display. It now serves the
            # pilot's OWN rank and contribution (store, knob as the seed), and
            # applies the ladder the client itself ships: the highest rank
            # whose threshold the contribution meets, never lower than what
            # the pilot holds (a check-up promotes, it does not demote).
            # FMO_ANSWER_0175=fail sends message 2 (graceful refusal), 0
            # stays silent (the hang, on purpose).
            _char = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
            # SE's review above Captain and the phase-end ceasefire bonus are
            # both the Personnel Officer's (REVIEW / CEASEFIRE); the review
            # runs first so the reply carries the rank it leaves.
            try:
                _rv = self.officer_review(_char) if _char else None
                _cf = self.pay_ceasefire(_char) if _char else []
            except Exception as _e:
                _rv, _cf = None, []
                log(f"{self.peer}   WARNING: review / ceasefire skipped ({_e!r})")
            if _rv or _cf:
                log(f"{self.peer}   PERSONNEL: review {_rv or 'not due'}; "
                    f"ceasefire bonus {_cf or 'none owed'}")
            _blk, _sr = servicerecord.service_record_block(_char)
            log(f"{self.peer}   0x0175 = the SERVICE RECORD / promotion request "
                f"(mission-result machine 0x61192780, empty body). "
                f"FMO_ANSWER_0175={servicerecord.ANSWER_0175!r}.")
            if servicerecord.ANSWER_0175 == "0":
                log(f"{self.peer}   WARNING: staying silent: state 1 polls forever -- the hang.")
                return []
            if servicerecord.ANSWER_0175 == "fail":
                log(f"{self.peer}   -> message 2: the id != 0x176 arm shows an error "
                    f"box and the machine ends (graceful).")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            if _sr["promoted"]:
                if _char:
                    _char["rank"] = _sr["rank"]
                    try:
                        self.commit(f"service record: promoted {_sr['rank_was']} "
                                    f"{ranks.rank_name(_sr['rank_was'])} -> {_sr['rank']} "
                                    f"{ranks.rank_name(_sr['rank'])} at contribution "
                                    f"{_sr['contribution']}")
                    except Exception as _e:
                        log(f"{self.peer}   WARNING: promotion NOT banked ({_e!r}) -- "
                            f"the next 0x014A will serve the old rank again")
                else:
                    log(f"{self.peer}   WARNING: promotion computed but there is no pilot "
                        f"in the store to bank it on -- the next 0x014A will "
                        f"serve the old rank again")
            # KEY: THE PAYBOOK: SE's Personnel desk pays the banked days here.
            _body, _rows, _tm, _tmp, _days = bytes(_blk), [], 0, 0, 0
            if servicerecord.SALARY:
                _rows, _tm, _tmp, _days = self.pay_salary(_char or None, _sr["rank"])
                _body, _tm, _tmp = servicerecord.paybook_fill(_body, _rows)
            log(f"{self.peer}   -> 0x0176, {0x20 + servicerecord.S176_BODY_LEN}B: contribution "
                f"{_sr['contribution']} at +0x04/+0x08 ({_sr['contribution_src']}; "
                f"+0x08 -> lobby+0xFC8), rank {_sr['rank']} {ranks.rank_name(_sr['rank'])} "
                f"at +0x0D (-> lobby+0x8BB) and +0x0F (the outlook line). "
                + (f"PROMOTED from {_sr['rank_was']} {ranks.rank_name(_sr['rank_was'])}: "
                   f"the ladder's threshold {_sr['threshold']} is met. "
                   if _sr["promoted"] else
                   f"No promotion ({_sr['rank_src']}; next rank needs "
                   f"{_sr['next_threshold']}). ")
                + (f"PAYBOOK: {_days} payday(s), {len(_rows)} rows, total "
                   f"{_tm} H$ + {_tmp} MP (+0x00/+0x10/+0x14; banked on the "
                   f"pilot, last_payday moved to today)" if servicerecord.SALARY and _rows else
                   ("PAYBOOK: nothing owed today (paid already)" if servicerecord.SALARY and _char
                    else "PAYBOOK: empty (FMO_SALARY=0 or no pilot in the store)")))
            _outs = [packet.build(servicerecord.MSG_0176_REPLY, bytes(0x20) + _body, p["seq"], p["conn"])]
            # The same talk in which his script says he is handing over the
            # starter transit pass. The mint rides BEHIND the reply so the
            # machine has its block before an item lands.
            _pass = self.grant_hq_pass(p["conn"], rank=_sr["rank"])
            if _pass:
                _outs.append(_pass)
            return _outs

        if p["msg"] == charselect.MSG_LOG_UPLOAD:
            # 0x01A5 (0x61174510, up to 0x39D0 B): a TEXT LOG the client
            # uploads once the battle start (block+0x48) is nonzero and the
            # elapsed time passes lobby+0x7DF8 minutes (0x6115FAE3). Unread
            # until 2026-09-27; answered with message 1 like the other
            # upload-only requests, so a poller can never park, and kept.
            _pl = bytes(p["payload"])
            try:
                _cd = os.path.join(wirelog.LOG_DIR, "captures")
                os.makedirs(_cd, exist_ok=True)
                with open(os.path.join(_cd, "fmo-01a5-%s.bin" % time.strftime(
                        "%Y%m%dT%H%M%SZ", time.gmtime())), "wb") as _fh:
                    _fh.write(_pl)
            except OSError:
                pass
            _txt = _pl.split(b"\0")[0][:300].decode("cp932", "replace")
            log(f"{self.peer}   0x01A5 = CLIENT LOG UPLOAD, {len(_pl)}B (saved to "
                f"captures/): {_txt!r} -> message 1")
            return [packet.build(1, b"", self.reply_seq(), p["conn"])]

        if p["msg"] == scriptcall.MSG_0159_REQ:
            # THE SCRIPT'S SERVER CALL (S159_EVENT): syscall 0xE220's record --
            # the cutscene terminator was one caller of it. The sender polls
            # its own sequence and compares ONLY the reply's message id
            # against 1; what the script then READS is the record as it stands
            # in lobby+0x6E4E, which only a 0x015A (+0xAD8 = 0) can rewrite.
            log(f"{self.peer}   0x0159, {len(p['payload'])}B (the sender copies "
                f"{scriptcall.S159_BODY_LEN}B from lobby+0x6E4E) -- the state machine at "
                f"lobby+0x6E4A is in state 2 polling seq 0x{p['seq']:08X}. "
                f"0x61177AB5 compares the reply's message id against 1 and "
                f"nothing reads its body.")
            # KEY: 2026-09-05 (settled-lobby round 2, LIVE): the counter NPC event
            # scripts (nina_event / gunsou1_event, SCP 0x8071/0x8074) send ONE
            # 0x0159 per dialogue step -- three in three seconds, first dwords
            # 0x68, 0xC9, 0x66 -- so this block is the client committing its
            # lobby progress (event/flag state) after each event, i.e. SE's
            # persistence path for the kind-11 flag bytes the counters test
            # (byte 128 == 99 "pilot registered"). The log keeps 16 bytes; the
            # whole body goes to a capture so the block can be decoded and,
            # eventually, stored and served back in 0x014A instead of the
            # FMO_STATUS_FLAGS stand-in. Written raw (post-decrypt), best effort.
            try:
                _cd = os.path.join(wirelog.LOG_DIR, "captures")
                os.makedirs(_cd, exist_ok=True)
                _cp = os.path.join(_cd, "fmo-0159-%s-%08x.bin" % (
                    time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), p["seq"]))
                with open(_cp, "wb") as _fh:
                    _fh.write(p["payload"])
                log(f"{self.peer}   0x0159 body saved -> {_cp} (first dword "
                    f"0x{int.from_bytes(p['payload'][:4], 'little'):X}; decode "
                    f"against lobby+0x6E4E -- the flag bytes are the prize)")
            except OSError as _e:
                log(f"{self.peer}   0x0159 body NOT saved: {_e}")
            # Keep the block so a 0x015A result push can hand the client back
            # its OWN mission record instead of one we invented. Exact length
            # or nothing: result_push_body refuses a short one rather than
            # padding, because a padded record still gets copied into
            # lobby+0x6E4E and would blank whatever it does not cover.
            if len(p["payload"]) == scriptcall.S159_BODY_LEN:
                self.last_0159 = bytes(p["payload"])
            if scriptcall.ANSWER_0159 == "0":
                log(f"{self.peer}   WARNING: FMO_ANSWER_0159=0: staying silent. "
                    f"0x61199E30 keeps returning <= 0, the machine never "
                    f"leaves state 2, and the client hangs at the end of the "
                    f"cutscene -- this is the 2026-09-04 hang, on purpose.")
                return []
            if scriptcall.ANSWER_0159 == "fail":
                log(f"{self.peer}   -> message 2 (FMO_ANSWER_0159=fail): a "
                    f"WRONG id, so 0x61177AE2 sets lobby+0x73EA = -1 and the "
                    f"machine still advances to state 3. A graceful refusal, "
                    f"not a hang -- use it to see what the script does with a "
                    f"rejected commit.")
                return [packet.build(2, b"", p["seq"], p["conn"])]
            # KEY: THE SCRIPT'S SERVER CALL (see S159_EVENT): decode it, name
            # what it is about, persist it on the pilot, apply the rule table,
            # and -- only when a rule changed something -- hand the script its
            # answer through a 0x015A (+0xAD8 = 0) BEFORE the ack, which is the
            # order the client's machine copies the record out in.
            _ev, _ps = scriptcall.parse_0159(p["payload"])
            outs = []
            _pc = (self.playing_char() or None) if charstore.CHAR_STORE else None
            if _ev is None:
                log(f"{self.peer}   0x0159 body is {len(p['payload'])}B, not "
                    f"{scriptcall.S159_BODY_LEN}: not a script record -- acked, not decoded")
            elif _ev == 0 and not any(_ps):
                log(f"{self.peer}   0x0159 with event 0 and no params (an empty "
                    f"record -- the driven sweep sends one): acked, not persisted")
            else:
                log(f"{self.peer}   {scriptcall.describe_0159(_ev, _ps)}")
                if _pc is not None:
                    _se = dict(_pc.get("srv_events") or {})
                    _prev = _se.get(str(_ev)) or {}
                    _se[str(_ev)] = {"params": [v for v in _ps],
                                     "n": int(_prev.get("n", 0)) + 1,
                                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                         time.gmtime())}
                    _pc["srv_events"] = _se
                    try:
                        self.commit(f"script event {_ev} "
                                    f"({', '.join(f'p{i + 1}={v}' for i, v in enumerate(_ps) if v) or 'no params'})"
                                    f" x{_se[str(_ev)]['n']}")
                    except Exception as _e:
                        log(f"{self.peer}   WARNING: script event NOT persisted ({_e!r})")
                # PENALTY: event 211 = the retraining battle is over (D83
                # 0x2AD6); a counted win is pushed here, before the ack
                outs += penalty.on_script_event(self, p["payload"], _pc, _ev, _ps, p["conn"])
                # the hangar mechanic's permit sale answers itself (permits.HANGAR_SALE_EVENT)
                _rule = (None if _ev == permits.HANGAR_SALE_EVENT
                         else scriptcall.event_rule_for(_ev, _ps))
                if _ev == permits.HANGAR_SALE_EVENT:
                    outs += self.sell_hangar_pass(p["conn"], p["payload"], _ps)
                if _rule is not None:
                    _fl = fmostore.flags_bytes(_pc.get("flags")) if (fmostore and _pc) else b""
                    if not _fl:
                        _fl = status.reply_014a(char=_pc or {})[status.S14A_FLAGS11:status.S14A_FLAGS11 + status.S14A_FLAGS11_LEN]
                    _ans, _nfl, _what = scriptcall.apply_event_rule(_rule, _ps, _fl)
                    _reward_money, _reward_pushes = 0, []
                    if not _what:
                        log(f"{self.peer}   fmo-events.tsv row for event {_ev} "
                            f"(p1 {_rule['p1'] if _rule['p1'] is not None else '*'}) "
                            f"changes nothing for this pilot -- plain ack")
                    else:
                        log(f"{self.peer}   fmo-events.tsv row for event {_ev}: "
                            + "; ".join(_what)
                            + (f" -- {_rule['note']}" if _rule.get("note") else ""))
                        if _nfl != _fl:
                            if _pc is not None and fmostore:
                                _pc["flags"] = _nfl.hex()
                                # a mission the script just REPORTED (105, @report
                                # 3 -> 99) is complete: the Pilot level / rank
                                # follow the frontier advance applies
                                for _cw in progress.completions_follow(_pc, _fl, _nfl):
                                    _what.append(_cw)
                                    log(f"{self.peer}   progression: {_cw}")
                                # the report's reward (scriptcall.MISSION_REWARD):
                                # the script prints it because p2 = 1; pay it here
                                if scriptcall.MISSION_REWARD:
                                    for _rb, _rm, _ri in progress.rewards_due(_pc, _fl, _nfl):
                                        _why = f"mission byte {_rb} reported"
                                        if _rm:
                                            _reward_money += _rm
                                            self.credit_money(f"{_why}: reward {_rm} H$", money=_rm)
                                        _pid = progress.REWARD_PASS.get((_ri or {}).get("name"))
                                        if _pid:
                                            _mint = self.grant_reward_pass(p["conn"], _pid, _why)
                                            if _mint:
                                                _reward_pushes.append(_mint)
                                        elif _ri:
                                            log(f"{self.peer}   {_why}: {_ri.get('kind')} "
                                                f"{_ri.get('name')!r} needs no grant (the cosmetic "
                                                f"catalogue already offers it)")
                                try:
                                    self.commit(f"script event {_ev}: flags "
                                                + ", ".join(w for w in _what if w.startswith("flag")))
                                except Exception as _e:
                                    log(f"{self.peer}   WARNING: flags NOT persisted ({_e!r})")
                            else:
                                log(f"{self.peer}   WARNING: no pilot in the store: the flag "
                                    f"change rides this push only and is gone at relog")
                        _owned = bytearray(status.reply_014a(char=_pc or {})[status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN])
                        _owned[status.S14A_FLAGS11 - status.S14A_OWNED:status.S14A_FLAGS11 - status.S14A_OWNED + status.S14A_FLAGS11_LEN] = _nfl
                        _rec = scriptcall.answered_0159(p["payload"], _ans)
                        # +0x418..+0x41A = the pilot's penalty bytes (pilot=),
                        # not the record's own: the arm stores them either way
                        outs.append(resultpush.result_push_packet(
                            p["conn"], record=_rec, money=_reward_money, contribution=0,
                            owned=bytes(_owned), pilot=_pc or {}))
                        log(f"{self.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} ANSWER push "
                            f"({resultpush.S15A_BODY_LEN}B, queue seq 0x{pushes.QUEUE_SEQ:08X}) BEFORE "
                            f"the ack: the record with the answered params at "
                            f"+0x{scriptcall.S159_PARAMS:X} goes back into lobby+0x6E4E "
                            f"(+0xAD8 = 0), the owned table + flags refreshed, "
                            + (f"money delta +{_reward_money} H$ (mission reward). "
                               if _reward_money else "no money. ")
                            + "The ack below then copies it to "
                            f"the script, whose 0xE222 reads the answer.")
                        outs += _reward_pushes
            log(f"{self.peer}   -> message 1 on seq 0x{p['seq']:08X}: the match "
                f"arm copies lobby+0x6E4E out to the script and sets "
                f"lobby+0x73EA = 1 (FMO_ANSWER_0159=fail/0 to A/B it).")
            outs.append(packet.build(handshake.MSG_SESSION_START, b"", p["seq"], p["conn"]))
            if scriptcall.REGRANT_0159 and not getattr(self, "_regranted_0159", False):
                self._regranted_0159 = True
                log(f"{self.peer}   FMO_0159_REGRANT: the tutorial committed; "
                    f"pushing a fresh 0x0153 area grant (seq {pushes.QUEUE_SEQ:#x}) so "
                    f"the client re-enters the lobby with a map and a script "
                    f"instead of parking in the script-less scene 6. One-shot "
                    f"for this session.")
                fake = packet.parse(packet.build(zoneentry.MSG_0150_REQ, struct.pack("<H", 0xFFFD) + bytes(30),
                                                 seq=pushes.QUEUE_SEQ, conn_id=p["conn"]))
                outs += self.on_packet(fake)
            elif scriptcall.REGRANT_0159:
                log(f"{self.peer}   FMO_0159_REGRANT: a SECOND 0x0159 this session "
                    f"-- the re-entered script played the tutorial again; NOT "
                    f"re-granting (one-shot), so this cannot loop.")
            return outs

        if p["msg"] == charselect.MSG_KEEPALIVE:
            # 8 bytes: SECONDS and MICROSECONDS of the client's synced clock
            # (0x6117C304..0x6117C33F: clock_ms / 1000 and (clock_ms % 1000) *
            # 1000 -- this used to say "a counter"). Nothing polls for a reply;
            # the one thing that can answer it is the 0x0199 time-sync PUSH on
            # the queue sequence, and only in scene 4 (see MSG_TIME_SYNC).
            when, when_us = struct.unpack_from("<II", p["payload"], 0) \
                if len(p["payload"]) >= 8 else (0, 0)
            log(f"{self.peer}   keepalive 0x0198, client clock "
                f"{time.strftime('%H:%M:%SZ', time.gmtime(when))}.{when_us:06d}"
                + (" -- no reply expected, the client does not poll for one"
                   if not timesync.TIME_SYNC else
                   f" -> 0x{timesync.MSG_TIME_SYNC:04X} time sync on queue seq "
                   f"0x{pushes.QUEUE_SEQ:08X} (FMO_TIME_SYNC=1; scene 4 only by the "
                   f"arm's gate, dropped in the lobby)"))
            outs = []
            # the trade service finds a partner's session here, and a push
            # for this pilot (0x017D) rides the keepalive like 0x015A does
            trade.LIVE_SESSIONS[self.ip] = self
            outs += self.trade_pushes_due(p["conn"])
            # the story gates tool's queued edits land here, on this thread
            outs += gatetool.gate_ops_due(self, p["conn"])
            # spoils: a won group battle's loot round, and items won (loot.py)
            outs += loot.loot_pushes_due(self, p["conn"])
            # the Coliseum: a waiting window the leader opened, a withdrawn
            # entry, an arena that ended (coliseum.py; FMO_COLISEUM)
            outs += self.coliseum_pushes_due(p["conn"])
            if timesync.TIME_SYNC:
                outs.append(timesync.time_sync_packet(p["conn"], when, when_us))
            if pushes.PUSH_PROBE and not getattr(self, "push_probe_done", False):
                self.push_probe_done = True
                _pm, _pb = pushes.PUSH_PROBE
                outs.append(pushes.lobby_push_packet(_pm, _pb, p["conn"]))
                log(f"{self.peer}   -> 0x{_pm:04X} FMO_PUSH_PROBE, {len(_pb)}B "
                    f"on queue seq 0x{pushes.QUEUE_SEQ:08X}, one shot. "
                    + pushes.PUSH_NOTES.get(_pm, "(a built push, sent raw)"))
            elif pushes._PUSH_PROBE_ERR and not getattr(self, "push_probe_done", False):
                self.push_probe_done = True
                log(f"{self.peer}   WARNING: FMO_PUSH_PROBE ignored: {pushes._PUSH_PROBE_ERR}")
            _bst = referee.BATTLE_STATE.get(self.battle_key())
            if _bst is not None:
                _bst["clock"] = (when, when_us, time.time())
            if battleend._BATTLE_END_ERR and not getattr(self, "_be_err_said", False):
                self._be_err_said = True
                log(f"{self.peer}   WARNING: FMO_BATTLE_END ignored: {battleend._BATTLE_END_ERR}")
            if battleend._BATTLE_OBJECTIVE_ERR and not getattr(self, "_bo_err_said", False):
                self._bo_err_said = True
                log(f"{self.peer}   WARNING: FMO_BATTLE_OBJECTIVE ignored: "
                    f"{battleend._BATTLE_OBJECTIVE_ERR}")
            if battleend._OBJECTIVE_ERR and not getattr(self, "_obj_err_said", False):
                self._obj_err_said = True
                log(f"{self.peer}   WARNING: FMO_OBJECTIVE ignored: {battleend._OBJECTIVE_ERR}")
            if battleend.OBJECTIVE and _bst is not None and not _bst.get("objective_done"):
                for _bc in [c for c in groupchannel.WORLD_PEERS.values()
                            if c.addr[0] == self.ip and c.key
                            and c.key.endswith(b"battle")]:
                    for _b in referee.objective_tick(_bst, _bc, time.time()):
                        log(f"{self.peer}   objective: {_b}")
                        referee.hud_banner(_bc, _b)
            # THE COLISEUM: an arena match ends on its verdict (both teams
            # judged together), never on this pilot's own death or a timer
            outs += self.arena_end_due(p["conn"])
            # FRONTLINE PvP (pvproom.py): a matching room ends every pilot in
            # it on the ROOM's verdict, not on its own death or timer
            try:
                outs += pvproom.end_due(self, p["conn"])
            except Exception as e:          # a judge bug must not cost the keepalive
                log(f"{self.peer}   WARNING: PVP ROOM end check failed ({e!r})")
            if (battleend.BATTLE_END and _bst is not None
                    and not getattr(self, "battle_end_done", False)
                    and not self.in_arena_match()
                    and not pvproom.judged(self.battle_key())):
                _trig = referee.pilot_death_trigger(
                    battleend.PILOT_DEATHS.get(self.account),
                    getattr(self, "sortie_granted_at", None), time.time(),
                    battleend.BATTLE_DEATH_END)
                _own = _trig is not None     # this pilot only -- see below
                if _trig is None:
                    _trig = referee.battle_end_trigger(_bst, battleend.BATTLE_END, time.time(),
                                                       missionblock.MISSION_TIME)
                if _trig:
                    self.battle_end_done = True
                    # A death ends THIS session's battle; the address-wide
                    # "ended" flag would end a same-router partner's too.
                    if not _own:
                        _bst["ended"] = True
                    # VERIFIED: LIVE 2026-09-10 13:08Z: this ended a battle for the
                    # first time -- eject -> 0x014C -> the client's own "EXP
                    # Gain" screen -> its 0x0150 (0xFFFD) re-entry, no 0x013D.
                    # The PAY rides with it: 0x015A used to fire only on the
                    # withdraw path, so that first end paid nothing.
                    _rp = self.battle_result_push(
                        p["conn"], f"the battle end ({_trig[0]})", won=_trig[1])
                    if _rp:
                        outs.append(_rp)
                    # FRIENDLY FIRE: the 0x017B must land under the BATTLE
                    # gate (0x611734E0), i.e. before the 0x014C that ends it
                    _pr = self.penalty_report_push(p["conn"])
                    if _pr:
                        outs.append(_pr)
                    _be = self.battle_end_push(p["conn"], why=_trig[0],
                                               won=_trig[1])
                    if _be:
                        outs.append(_be)
            # FMO_ANNOUNCE -- one line into the message window, once per
            # session. Deliberately on the keepalive rather than at world
            # entry: the grant resets the lobby, and this is also the
            # POSITIVE CONTROL for the whole queue-push path, so it has to
            # land at the same point in the session 0x015A would.
            if lobbymessage.ANNOUNCE and not getattr(self, "announced", False):
                self.announced = True
                try:
                    outs.append(lobbymessage.lobby_message_packet(
                        p["conn"], lobbymessage.ANNOUNCE, kind=lobbymessage.ANNOUNCE_KIND))
                    log(f"{self.peer}   -> 0x{lobbymessage.MSG_LOBBY_MESSAGE:04X} ANNOUNCE "
                        f"(kind {lobbymessage.ANNOUNCE_KIND}, {lobbymessage.S14B_BODY_LEN}B on queue seq "
                        f"0x{pushes.QUEUE_SEQ:08X}): {lobbymessage.ANNOUNCE!r}. KEY: This doubles as "
                        f"the positive control for the push transport -- if "
                        f"this text does NOT appear, no queue push reaches "
                        f"this client and a silent 0x015A says nothing about "
                        f"its body.")
                except ValueError as e:
                    log(f"{self.peer}   WARNING: FMO_ANNOUNCE not sent: {e}")
            # WARNING: THE SAME FOR THE ZONE TABLE (live 2026-09-12): with only the
            # grant-side copy, Change Area drew EVERY area grey -- because grey
            # is what the filler stores when 0x611A3AA0 cannot find the zone in
            # lobby+0x7724 at all, and that block was still zero. The client
            # polls 0x01AC once, in the lobby, BEFORE the grant; the grant wipes
            # the block; and the copy riding the grant meets an in-world gate
            # the client has not satisfied yet. So resend it once the scene is
            # provably up, exactly as the city table has done since 08-27.
            if (getattr(self, "zone_push_pending", False) and zonecontrol.ZONE_CONTROL
                    and time.time() - getattr(self, "zone_push_grant_at", 0)
                    >= 10):
                self.zone_push_pending = False
                zpush = zonecontrol.zone_control_push(p["conn"])
                if zpush:
                    log(f"{self.peer}   -> 0x{zonecontrol.MSG_ZONE_CONTROL:04X} deferred "
                        f"ZONE-CONTROL push: first keepalive 10s+ after the "
                        f"grant, so the scene is up, [lobby+0x20] is 4 and the "
                        f"lobby reset is behind us. Without this the Change Area "
                        f"screen reads an EMPTY table and greys every area.")
                    outs.append(zpush)
            # KEY: THE AREA A PERMIT JUST OPENED, LIVE (2026-09-12). The unlock
            # bit (owned+0x00) and the pass live in blocks the client only
            # takes from the 0x014A -- or from 0x015A, whose arm copies the
            # owned table onto lobby+0x8C8 and spends each serial at +0x310
            # through 0x6117A640 (a serial it does not hold just logs). So
            # one money-0 0x015A, first keepalive 10s+ after the grant (the
            # scene is up, as for the zone table), makes the area free and
            # the pass gone without a relog.
            _ap = getattr(self, "area_push_pending", None)
            if _ap and time.time() - _ap.get("at", 0) >= 10:
                self.area_push_pending = None
                if not resultpush.RESULT_PUSH:
                    log(f"{self.peer}   areas {_ap['zones']} opened, but "
                        f"FMO_RESULT_PUSH=0: the client learns it at the next "
                        f"login block only")
                else:
                    _ach = (self.playing_char() or {}) if charstore.CHAR_STORE else {}
                    _aow = status.reply_014a(char=_ach)[status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN]
                    _asp = [struct.pack("<Q", int(s) & 0xFFFFFFFFFFFFFFFF)
                            for s in _ap["spent"]][:resultpush.S15A_MAX_ITEMS]
                    outs.append(resultpush.result_push_packet(
                        p["conn"], money=0, contribution=0, owned=_aow,
                        spent=_asp, pilot=_ach))
                    log(f"{self.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} AREA push: "
                        f"owned table with the opened-area bits for "
                        f"{_ap['zones']} and {len(_asp)} spent pass "
                        f"serial(s); money/contribution 0, no record. Change "
                        f"Area should now move there with no permit prompt.")
            if (getattr(self, "city_push_pending", False) and citytable.city_rows()
                    and time.time() - getattr(self, "city_push_grant_at", 0)
                    >= 10):
                self.city_push_pending = False
                push = citytable.city_table_push(p["conn"])
                if push:
                    log(f"{self.peer}   -> 0x{citytable.MSG_CITY_TABLE:04X} deferred "
                        f"CITY push: first keepalive after a briefing-room "
                        f"grant, so the scene is up and the lobby reset is "
                        f"behind us. If City Control still does not appear, "
                        f"the data is not the gate -- the next probe is the "
                        f"shim force-call of 0x610FB500.")
                    outs.append(push)
            # The deferred AUTO-SORTIE push (0x014E), armed by the Move
            # handler. One shot per session; the delay keeps it clear of the
            # grant's own lobby reset, which zeroes lobby+0x5C7E -- the very
            # place this push's 3,400-byte block lands.
            if (getattr(self, "sortie_push_pending", False)
                    and not getattr(self, "sortie_push_done", False)
                    and time.time() - getattr(self, "sortie_push_grant_at", 0)
                    >= sortiepush.SORTIE_PUSH_DELAY):
                self.sortie_push_pending = False
                _ps2_no = self.ps2_type1_refusal(sortiepush.sortie_push_mapno()[0])
                push = None if _ps2_no else sortiepush.sortie_push_packet(
                    p["conn"], host=addressing.host_for(sortie.SORTIE_HOST or addressing.BATTLE_HOST, self.ip))
                if _ps2_no:
                    log(f"{self.peer}   WARNING: 0x{sortiepush.MSG_SORTIE_PUSH:04X} AUTO-SORTIE "
                        f"push NOT sent: {_ps2_no}")
                elif push is None:
                    _mn, _src = sortiepush.sortie_push_mapno()
                    log(f"{self.peer}   WARNING: 0x{sortiepush.MSG_SORTIE_PUSH:04X} AUTO-SORTIE "
                        f"push NOT sent: {_src}")
                else:
                    self.sortie_push_done = True
                    log(f"{self.peer}   -> 0x{sortiepush.MSG_SORTIE_PUSH:04X} AUTO-SORTIE "
                        f"push, {sortiepush.REPLY_014E_LEN}B on queue seq "
                        f"0x{pushes.QUEUE_SEQ:08X} (arm 0x6117EAE7; its gate wants "
                        f"[lobby+0x20]==4 and [lobby+0x24] not in {{0,3,9}}):")
                    for _label, _off, _raw, _src in sortiepush.sortie_push_fields():
                        log(f"{self.peer}      {_label} payload+0x{_off:03X} = "
                            f"{_raw.hex() if _raw else '(zero)'}  "
                            f"source: {_src}")
                    log(f"{self.peer}   WARNING: PREDICTED, NEVER MEASURED: banner "
                        f"8:3 'Sortieing to the battle map automatically in "
                        f"%d seconds' ~5s after this lands (tick gate "
                        f"0x6117CEE9: 5,000ms elapsed AND [globals+0x1C4]==6, "
                        f"scene 6 -- a cold-logged client that never Moved "
                        f"sits in scene 7 and NOTHING fires); then an inbound "
                        f"0x{sortiepush.MSG_SORTIE_GO:04X}; our message 1; scene 4. If "
                        f"0x{sortiepush.MSG_SORTIE_GO:04X} never arrives, read "
                        f"[lobby+0x24] and [globals+0x1C4] before blaming "
                        f"the push. Tell the player to STAND STILL: a Move "
                        f"now wipes the block (0x6117A2A8 zeroes lobby+0x5C7E) "
                        f"but not the latch at +0x4F16.")
                    outs.append(push)
            return outs

        if p["msg"] in charselect.CHARSEL:
            reply, need, what = charselect.CHARSEL[p["msg"]]
            who = struct.unpack_from("<I", p["payload"], 0)[0]                 if len(p["payload"]) >= 4 else 0
            names = " ".join(
                s.split(charselect.NUL)[0].decode("ascii", "replace")
                for s in (p["payload"][0x04:0x15], p["payload"][0x15:0x26])
                if s.split(charselect.NUL)[0]) if len(p["payload"]) >= 0x26 else ""
            log(f"{self.peer}   0x{p['msg']:04X} = {what}, id {who}"
                + (f", name {names!r}" if names else ""))
            if not charselect.ANSWER_LOBAPI:
                log(f"{self.peer}   FMO_ANSWER_LOBAPI=0 -- staying silent, the "
                    f"screen will hang")
                return []
            if charstore.CHAR_STORE:
                why = self.apply_charsel(p["msg"], p["payload"], who)
                if why:
                    # WARNING: Answering SUCCESS for something we did not do is the
                    # habit this whole section exists to end. The dispatcher
                    # takes the failure arm for ANY id that is not the one it
                    # waits for, and the arm reads our +0x08 as the code -- so
                    # message 2 with a code is a real, displayable refusal.
                    _code = getattr(self, "fail_code", None) or charselect.FAIL_CODE
                    self.fail_code = None
                    log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} FAILURE, code "
                        f"0x{_code:04X}: {why}")
                    if _code == charstore.NAME_TAKEN_CODE:
                        log(f"{self.peer}   the creation screen maps code "
                            f"0xC43B to 17:117 'That name is already in use' "
                            f"(0x61178CDE, table 0x613958E0) -- a real message")
                    elif _code == charstore.NAME_RESERVED_CODE:
                        log(f"{self.peer}   code 0xC90E is the name step's other "
                            f"by-value code (0x61178CDE); table 0x613955F0 maps it "
                            f"to 17:118 'That name cannot be used'. Not yet seen "
                            f"on a screen")
                    elif _code == charselect.DELETE_LOCK_CODE:
                        log(f"{self.peer}   code 0xC8E6 is SE's delete lock; table "
                            f"0x613955F0 maps it to 2:97 'cannot be deleted until 24 "
                            f"hours after it was created'. The delete machine "
                            f"0x611849B0 hands it to UI event 0x1075; whether that "
                            f"event draws 2:97 or a bare [FMO%05d] is not yet seen")
                    else:
                        log(f"{self.peer}   WARNING: the CODE is only the %05d in "
                            f"'[%s%05d] %s'. The message text is a fixed "
                            f"CFmoSysText id per call site (0x0130's is "
                            f"0xC0080014), so the number does not choose the "
                            f"wording -- diagnostic only (0xC43B is the one "
                            f"exception this screen maps to a string).")
                    return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), _code)]
            log(f"{self.peer}   -> 0x{reply:04X}. These screens read `== 1` as "
                f"SUCCESS; anything else is the failure arm, which takes our "
                f"+0x08 as the error code. WARNING: Opposite polarity to 0x0130, "
                f"where message 2 is the failure.")
            # WARNING: Per-command, NOT shared. A log line that describes the delete
            # while answering the name step is a log that lies, and this file
            # is read back far more often than it is written.
            if p["msg"] == 0x013F and not charstore.CHAR_STORE:
                log(f"{self.peer}   WARNING: FMO_CHAR_STORE is empty, so the delete is "
                    f"CLIENT-LOCAL ONLY: 0x61173A10 edits the list it is "
                    f"holding, we write nothing, and the 0x012F list is "
                    f"synthesised fresh -- so the character comes back at the "
                    f"next login.")
            elif p["msg"] == 0x013E and not charstore.CHAR_STORE:
                log(f"{self.peer}   WARNING: FMO_CHAR_STORE is empty, so this CREATE "
                    f"submit -- nation, class, appearance, hangar password and "
                    f"all -- is ACKNOWLEDGED AND DISCARDED. The character "
                    f"exists only in the client's own list and is gone at the "
                    f"next login.")
            elif p["msg"] == 0x0177:
                log(f"{self.peer}   note: the client's success arm also walks "
                    f"its LOCAL list and writes these two names into the entry "
                    f"whose id matches. The nation and appearance are NOT in "
                    f"this message -- 0x013E carries those.")
            return [packet.build(reply, bytes(need), self.reply_seq(), p["conn"])]

        if p["msg"] == sortie.MSG_SORTIE_REQ:
            return self.on_sortie(p)

        if p["msg"] == penalty.MSG_PENALTY_GIVE:
            return self.on_penalty_give(p)

        if p["msg"] == sortiepush.MSG_SORTIE_GO:
            return self.on_sortie_go(p)

        if p["msg"] == withdraw.MSG_BATTLE_WITHDRAW:
            if getattr(self, "sortie_pending", False):
                log(f"{self.peer}   progression: WITHDRAW -- this sortie will "
                    f"not count as completed")
            self.sortie_pending = False
            return self.on_battle_withdraw(p)

        if p["msg"] == resume.MSG_RESUME_REQ:
            return self.on_resume(p)

        if self.wants_spectate(p):
            # Delacroix: the 0x01C0 that follows his list carries an ARENA id
            # and is a spectate request (coliseum.py), not the mission board's
            return self.on_spectate(p)

        if p["msg"] == missionblock.MSG_MISSION_REQ and charselect.ANSWER_LOBAPI and missionblock.SERVE_MISSION_BLOCK:
            body = missionblock.reply_01c1()
            fields = missionblock.mission_fields()
            log(f"{self.peer}   lobby API 0x01C0 = the MISSION BLOCK request "
                f"(entry 21); FMO_MISSION_BLOCK=1 so reply_01c1() answers")
            log(f"{self.peer}   -> 0x{missionblock.MSG_MISSION_REPLY:04X}, {len(body)}B: "
                f"3,400-byte block at payload+0x{missionblock.M1C1_BLOCK:X} -> lobby+0x5C7E"
                + ("" if fields else ". Every field knob is at its default, so "
                   "this is byte-identical to the generic arm's zeros"))
            for label, off, raw, src in fields:
                log(f"{self.peer}      {label} block+0x{off:03X} = "
                    f"{raw.hex() if raw else '(zero)'}  source: {src}")
            log(f"{self.peer}   WARNING: this fills the lobby's copy only. MapNo is "
                f"read at 0x61004C99 in scene 4's phase-0 arm, entered by the "
                f"SORTIE reply 0x013A (request 0x0139, not served); LeaderID at "
                f"0x610DC6B8 (Battle Group, script opcode); the cost tables at "
                f"0x610CB1BB, world phase 6 after tick 1800.")
            return [packet.build(missionblock.MSG_MISSION_REPLY, body, self.reply_seq(), p["conn"])]

        if p["msg"] == charselect.MSG_NATION_REQ and charselect.ANSWER_LOBAPI:
            reply = lobapi.LOBAPI[charselect.MSG_NATION_REQ][0]
            _counts = (charselect.nation_counts(self.all_rosters())
                       if charselect.NATION_POP_LIVE else None)
            body = charselect.reply_019d(_counts)
            log(f"{self.peer}   0x019C = the NATION SELECT screen's data "
                f"request (sent by 'Create character')")
            if _counts is not None:
                _closed = charselect.closed_nation(_counts)
                log(f"{self.peer}   FMO_NATION_POP=live: O.C.U. {_counts.get(1, 0)} / "
                    f"U.S.N. {_counts.get(2, 0)} pilot(s) at Pilot level "
                    f"{charselect.NATION_POP_LEVEL}+ (gap {charselect.nation_gap_pct(_counts)}%); "
                    + (f"nation {_closed} CLOSED (+0x{8 if _closed == 1 else 0xC:02X} = -1; "
                       f"FMO_NATION_CLOSE_PCT={charselect.NATION_CLOSE_PCT}, "
                       f"_MIN={charselect.NATION_CLOSE_MIN})" if _closed else
                       f"both open (FMO_NATION_CLOSE_PCT={charselect.NATION_CLOSE_PCT})"))
            log(f"{self.peer}   -> 0x{reply:04X}, {len(body)}B: populations "
                f"{charselect.NATION_POP}, ENABLE={charselect.NATION_ENABLE} at +0x10. WARNING: A "
                f"HYPOTHESIS about the [FM00000] refusal, not a proven cause: "
                f"all-zero left the screen with a zero denominator and a clear "
                f"ENABLE byte. If it still refuses, put FMO_NATION_POP=0,0 "
                f"back rather than tuning these.")
            return [packet.build(reply, body, self.reply_seq(), p["conn"])]

        if p["msg"] == 0x01AA and charstore.CHAR_STORE and len(p["payload"]) >= 0x27:
            # The "Change Nations" submit carries a whole 52-byte record, so it
            # can rename AND re-nation in one message. Handled before the
            # generic arm purely so the roster is updated; the reply is the
            # same empty message 1 the table would have sent.
            cid = struct.unpack_from("<I", p["payload"], 0)[0]
            c = self.find(cid)
            log(f"{self.peer}   0x01AA = CHANGE NATIONS submit, id {cid}")
            if c is not None:
                from . import defection
                nb = p["payload"][0x28] if len(p["payload"]) >= 0x29 else 0
                _was = popnation.character_nation(c)[0]
                _to = nb if nb in (1, 2) else (3 - _was if charselect.NATION_CHANGE_TOGGLE
                                               and _was in (1, 2) else None)
                # SE: "the character's name can be changed when defecting" --
                # under the same rules as creation: unique, and not a reserved
                # name. Judged first, so a refused name stores nothing. The
                # 0x01AA mismatch arm hands +0x08 to the 0x613955F0 formatter,
                # which draws 17:117 for 0xC43B and 17:118 for 0xC90E.
                _nf = charstore._name_at(p["payload"], 0x04)
                _nl = charstore._name_at(p["payload"], 0x15)
                if (_nf, _nl) != (c.get("first"), c.get("last")):
                    _code, _nwhy = charstore.name_refusal(
                        _nf, _nl, self.name_taken(_nf, _nl, c.get("id")))
                    if _code is not None:
                        log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} RENAME REFUSED, "
                            f"code 0x{_code:04X}: {_nwhy}; nothing stored")
                        return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), _code)]
                # KEY: DEFECTION RULES (SE update 050906 97-108, news5570, topics
                # 060227; see defection.py). Judged BEFORE anything is stored: a
                # refused defection must not keep the rename either. The refusal
                # is message 2 with SE's own code in +0x08, which the client's
                # table 0x613955F0 turns into systext 2:109..2:114.
                # FMO_DEFECTION=0 restores the unconditional switch.
                if defection.DEFECTION and _was in (1, 2) and _to in (1, 2) and _to != _was:
                    _code, _dwhy = defection.defection_verdict(c, _was, _to, self.all_rosters())
                    if _code is not None:
                        log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} DEFECTION REFUSED, "
                            f"code {_code} (wire 0x{_code & 0xFFFF:04X}): {_dwhy}. The client "
                            f"draws {defection.REFUSAL_TEXT.get(_code)}; nothing stored "
                            f"(FMO_DEFECTION=0 restores the unconditional switch)")
                        return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(),
                                             _code & 0xFFFF)]
                    log(f"{self.peer}   DEFECTION ALLOWED {_was} -> {_to}: {_dwhy}")
                    for _line in defection.apply_defection(c):
                        log(f"{self.peer}      defection: {_line}")
                    log(f"{self.peer}      defection: NOT applied here: buddy list and "
                        f"radio-voice registrations (not held by this service); nation "
                        f"insignia are per squadron, not on the pilot. The client's own "
                        f"copy of rank, money and items can only change at its next "
                        f"0x014A/0x0133 (not yet seen on a screen).")
                c["first"] = charstore._name_at(p["payload"], 0x04)
                c["last"] = charstore._name_at(p["payload"], 0x15)
                # WARNING: +0x26 is the GENDER (the swapped-key hazard); the nation is +0x28,
                # the same slot as creation's. Until 2026-09-08 this stored
                # only +0x26 under the swapped `nation` key, so a Change
                # Nations submit never reached nation_byte -- the byte every
                # nation consumer reads -- and Molly, switched to U.S.N.,
                # still spawned in the O.C.U. zone with the O.C.U. cast (the
                # log said "nation 2" only because she is female).
                # Manual p.38: defecting "cannot change your gender". A record
                # that already holds a gender (1/2) keeps it whatever the
                # submit carries; only a record without one (a creation that
                # never reached 0x013E) takes +0x26.
                _g_was = c.get("gender", c.get("nation"))
                _g_new = p["payload"][0x26]
                if _g_was in (1, 2) and _g_new != _g_was:
                    log(f"{self.peer}      WARNING: submit +0x26 (gender) = {_g_new} but the "
                        f"pilot is {_g_was}; gender cannot change on a defection, kept {_g_was}")
                else:
                    c["nation"] = _g_new
                    c["gender"] = _g_new
                # WARNING: THE SUBMIT DOES NOT NAME THE NEW NATION (live 2026-09-27):
                # +0x28 is 0 in every Change Nations submit on record, one
                # switch O.C.U. -> U.S.N. and one U.S.N. -> O.C.U. --
                # so "left alone" kept every player in the nation they had just
                # left, while the client showed the change. With two playable
                # nations the action IS the switch: take the other one.
                # FMO_NATION_CHANGE_TOGGLE=0 restores leave-alone.
                was = popnation.character_nation(c)[0]
                how = "named by the submit"
                if nb not in (1, 2) and charselect.NATION_CHANGE_TOGGLE and was in (1, 2):
                    nb, how = 3 - was, (f"the submit names none (+0x28 = 0): "
                                        f"switched {was} -> {3 - was}")
                if nb in (1, 2):
                    c["nation_byte"] = nb
                log(f"{self.peer}      submit +0x26 (gender) = "
                    f"{p['payload'][0x26]}, +0x28 (nation) = "
                    f"{p['payload'][0x28] if len(p['payload']) >= 0x29 else 0}"
                    + (f" -> nation {nb} ({how})" if nb in (1, 2)
                       else "  WARNING: no nation to switch from, nation_byte left alone"))
                self.commit(f"id {cid} is now {c['first']} {c['last']}, "
                            f"gender {c['nation']}, nation "
                            f"{c.get('nation_byte')} (+0x28)")
            else:
                log(f"{self.peer}   WARNING: id {cid} is not in this account's roster "
                    f"-- storing nothing")
            return [packet.build(lobapi.LOBAPI[0x01AA][0], b"", self.reply_seq(), p["conn"])]

        if p["msg"] in (trade.MSG_TRADE_START, trade.MSG_TRADE_ACCEPT, trade.MSG_TRADE_UPDATE,
                        trade.MSG_TRADE_OK, trade.MSG_TRADE_OFFER, trade.MSG_TRADE_POLL):
            return self.on_trade(p)

        if p["msg"] == 0x01AC and charselect.ANSWER_LOBAPI and (citytable.city_rows()
                                                                or zonecontrol.ZONE_CONTROL
                                                                or squadron.SERVE_SQUADRON):
            # 0x01AC (Battle Group) is a poll the client sends AFTER it is
            # in-scene -- observed live 2026-08-27 after every kind-2 grant --
            # so it is a proven in-world moment ([conn+0x20]==4 holds), the one
            # state the 0x019A arm 0x6117F5F9 requires. We answer 0x01AC as
            # usual AND ride whichever pushes are armed out with it. Both
            # default OFF (empty FMO_CITY_TABLE and FMO_ZONE_CONTROL): this arm
            # never runs and 0x01AC takes the generic path below.
            # WARNING: EVERY LOG LINE BELOW IS GATED ON ITS OWN KNOB. Two knobs share
            # this arm now, and an ungated line would announce a push that is
            # not being sent -- the failure the Move handler's own log made for
            # a week (2026-09-03).
            reply, need = lobapi.LOBAPI[0x01AC]
            self.log_squadron_request(p["payload"])
            outs = [packet.build(reply, lobapi.lobapi_payload(0x01AC, need, p["payload"],
                                                              self.pol_groups),
                                 self.reply_seq(), p["conn"])]
            _crows = citytable.city_rows()
            if _crows:
                log(f"{self.peer}   lobby API 0x01AC (Battle Group) -- "
                    f"{len(_crows)} city row(s) "
                    f"({'FMO_CITY_TABLE' if citytable.CITY_TABLE else 'SE''s economic cities, fmowar.CITIES'}), "
                    f"so a 0x{citytable.MSG_CITY_TABLE:04X} CITY-TABLE push rides this "
                    f"in-world poll on the queue sequence "
                    f"0x{pushes.QUEUE_SEQ:08X}.")
                log(f"{self.peer}   -> 0x{citytable.MSG_CITY_TABLE:04X}, "
                    f"{citytable.CITY_TABLE_LEN}B: count={len(_crows)} at "
                    f"payload+0x{citytable.CITY_TABLE_OFF:X}, 8B rows "
                    f"{{u16 selector, u32 903e6+tile, u16 0}} from "
                    f"+0x{citytable.CITY_ROW_OFF:X} -> lobby+0x6C36 (arm 0x6117F5F9, "
                    f"gated [conn+0x20]==4). City Control (the zone script's "
                    f"0xE30C) reads the selector for the city's ARE name and "
                    f"asks the second server about the u32 ids (kind 7) -- "
                    f"which is where its rows come from. Rows: "
                    + ", ".join(f"{s}:{t}" for s, t in _crows[:6])
                    + (" ..." if len(_crows) > 6 else ""))
                outs.append(citytable.city_table_push(p["conn"]))
            # The 0x016C ZONE-CONTROL push rides the same proven in-lobby
            # moment, for the same reason (its arm 0x6117F013 is gated on
            # [lobby+0x20]==4) -- and on EVERY poll, not once, because the
            # lobby reset 0x6117A2F6 zeroes lobby+0x7724 on a scene change.
            if zonecontrol.ZONE_CONTROL:
                _zrows = zonecontrol.parse_zone_control(zonecontrol.ZONE_CONTROL)
                _zdead = [z for z, _o, _u in _zrows
                          if 500 <= z <= 519 and z not in zonecontrol.ZONE_LIVE_WARZONES]
                log(f"{self.peer}   -> 0x{zonecontrol.MSG_ZONE_CONTROL:04X} ZONE-CONTROL "
                    f"push, {zonecontrol.ZONE_CONTROL_LEN}B: count={len(_zrows)} at "
                    f"payload+0x{zonecontrol.ZONE_COUNT_OFF:X}, {zonecontrol.ZONE_ROW_LEN}B rows "
                    f"{{s16 zoneId, u8 ocu, u8 usn}} from "
                    f"+0x{zonecontrol.ZONE_ROW_OFF:X} -> lobby+0x7724 (arm 0x6117F013, "
                    f"gated [lobby+0x20]==4). Rows: "
                    + ", ".join(f"{z}={o}/{u}" for z, o, u in _zrows))
                log(f"{self.peer}      this fills the block Change Area's "
                    f"predicate reads (0x611794A0). It does NOT open the "
                    f"screen -- the screen already opens -- and it cannot "
                    f"un-refuse a zone the CLIENT's own table refuses.")
                if _zdead:
                    log(f"{self.peer}      WARNING: {_zdead} are kind-5 rows "
                        f"whose D83 flag is ZERO, so gate (A) at 0x61010C8C "
                        f"refuses them before this table is consulted. Only "
                        f"{list(zonecontrol.ZONE_LIVE_WARZONES)} can ever be selectable.")
                _warn = zonecontrol.zone_control_nation_warning()
                if _warn:
                    log(f"{self.peer}      {_warn}")
                outs.append(zonecontrol.zone_control_push(p["conn"]))
            # PROBE: The 0x016A SHOP AVAILABILITY bitmap rides the same moment, and
            # for a different reason than the two above: its arm's gate
            # ([lobby+0x20]==4) has held since the character list, and the lobby
            # reset frees lobby+0x3665 only when LEAVING A BATTLE -- so this one
            # actually survives into the hangar and the setup screen. Sent on
            # every poll anyway; the arm frees the previous block first, so a
            # repeat costs one malloc and cannot accumulate.
            if partsstock.PARTS_STOCK:
                _pk = sorted(partsstock.PARTS_STOCK)
                _pn = sum(len(v) for v in partsstock.PARTS_STOCK.values())
                _pbody = partsstock.parts_stock_payload(partsstock.PARTS_STOCK)
                log(f"{self.peer}   -> 0x{partsstock.MSG_PARTS_STOCK:04X} SHOP STOCK "
                    f"bitmap, {len(_pbody)}B: {len(_pk)} section(s) "
                    + ", ".join(f"{k:#04x}x{len(partsstock.PARTS_STOCK[k])}" for k in _pk[:6])
                    + (" ..." if len(_pk) > 6 else "")
                    + f" ({_pn} ids) -> lobby+0x3665 (arm 0x6117EC6D). This is "
                    f"the ONLY writer of the block 0x61174D00 reads, and with "
                    f"it NULL every shop row is refused -- which is why the "
                    f"setup screen's shop has always been empty.")
                log(f"{self.peer}      the client still applies SE's own level "
                    f"window (0x61033BFE: max(shop level - 3, 1) .. shop "
                    f"level) and SE's own prices (master table file 4, buy at "
                    f"+0x00), so this says WHAT IS STOCKED, not what is shown "
                    f"or what it costs. WARNING: What SE keyed this bitmap on is NOT "
                    f"established: FMO_PARTS_STOCK={partsstock.PARTS_STOCK_SPEC!r} is our "
                    f"choice, not a measurement.")
                # the pilot's nation picks which victory series it sees (partsstock)
                _snat, _ = self.grant_nation()
                outs.append(partsstock.parts_stock_push(p["conn"], nation=_snat))
            if squadron.SERVE_SQUADRON:
                _inat, _isrc = zoneentry.script_nation(
                    self.playing_char() if charstore.CHAR_STORE else None)
                _irows = squadron.insignia_for(_inat)
                if not _irows:
                    log(f"{self.peer}   WARNING: no squadron insignia on offer for "
                        f"nation {_inat} (FMO_SQUADRON_INSIGNIA_LIST="
                        f"{squadron.INSIGNIA_OFFER!r}, catalogue {len(squadron.INSIGNIA)} rows) "
                        f"-- 'Set squadron insignia' would CRASH the client if "
                        f"it were reachable; it stays greyed because +0x0A is "
                        f"non-zero.")
                else:
                    log(f"{self.peer}   -> 0x{squadron.MSG_INSIGNIA_LIST:04X} SQUADRON "
                        f"INSIGNIA list, {squadron.INSIGNIA_LIST_LEN}B: count="
                        f"{len(_irows)} at payload+0x{squadron.INSIGNIA_COUNT_OFF:X}, "
                        f"{squadron.INSIGNIA_ROW_LEN}B rows {{u8 nation, u8 0, u16 id}} "
                        f"from +0x{squadron.INSIGNIA_ROW_OFF:X} -> lobby+0x7DF5 (arm "
                        f"0x6117F693). Nation byte {_inat} from {_isrc}; the "
                        f"client compares it for EQUALITY against "
                        f"byte[lobby+0x8B4]. First: "
                        + ", ".join(f"{i}={n}" for i, n in _irows[:3]))
                    log(f"{self.peer}      this is what the insignia picker "
                        f"reads. With count 0 the picker has no rows and "
                        f"0x611BE949 dereferences rows[selected] -- the crash "
                        f"hit live on 2026-09-09. Re-sent on EVERY "
                        f"poll because the lobby reset 0x6117A2E4 zeroes the "
                        f"block on each scene change, exactly like 0x016C.")
                    outs.append(squadron.insignia_push(p["conn"], _inat))
            return outs

        if p["msg"] == squadron.MSG_INSIGNIA_SET and charselect.ANSWER_LOBAPI:
            # VERIFIED: THE SQUADRON INSIGNIA REGISTRATION (live 2026-09-09). The ack
            # is unchanged -- the table already answers 0x01C4 with message 1
            # and the client believes it -- this only makes it SURVIVE, which
            # is the difference between the emblem being drawn once and the
            # squadron actually having one.
            _gid, _iid = squadron.parse_insignia_set(p["payload"])
            _known = dict((i, en) for i, en in squadron.insignia_for(
                zoneentry.script_nation(self.playing_char() if charstore.CHAR_STORE else None)[0]))
            _name = _known.get(_iid, "NOT IN THE CATALOGUE WE SERVED")
            log(f"{self.peer}   0x{squadron.MSG_INSIGNIA_SET:04X} = SET SQUADRON "
                f"INSIGNIA: POL group {_gid}, insignia {_iid} ({_name})")
            if not _gid or not _iid:
                log(f"{self.peer}      WARNING: group or insignia is 0 -- storing "
                    f"nothing. Body was {p['payload'][:12].hex(' ')}")
            elif fmostore is None:
                log(f"{self.peer}      WARNING: the character store is unavailable, so "
                    f"this registration CANNOT be persisted; the client will "
                    f"show it until the next relaunch.")
            elif _gid not in self.pol_groups:
                # Refuse to stamp an emblem on a group this member is not in.
                log(f"{self.peer}      WARNING: group {_gid} is NOT one of this "
                    f"member's POL groups {sorted(self.pol_groups)} -- storing "
                    f"nothing.")
            else:
                _stored, _prev = fmostore.set_squadron_insignia(
                    _gid, _iid, self.account)
                if _prev and _prev != _iid:
                    log(f"{self.peer}      WARNING: group {_gid} ALREADY had insignia "
                        f"{_prev} and SE says it cannot be changed (86:21), so "
                        f"the stored value STAYS {_stored}. The client asked "
                        f"for {_iid}; it will disagree with us after a "
                        f"relaunch, which is worth knowing about.")
                else:
                    log(f"{self.peer}   STORED: POL group {_gid} insignia = "
                        f"{_stored} ({_name}), by {self.account}. It now rides "
                        f"0x01AD slot +0x0A, so the menu row greys itself and "
                        f"Squadron Info draws it on every future login.")
            self.squadron_seen = None       # the +0x0A word just changed
            if (squadron.INSIGNIA_PUSH and _gid and _iid and fmostore is not None
                    and _gid in self.pol_groups):
                self._insignia_push = (_gid, fmostore.squadron_insignia(_gid)
                                       or _iid)

        if p["msg"] == missionboard.MSG_MISSION_ACCEPT and charselect.ANSWER_LOBAPI:
            # VERIFIED: THE MISSION ACCEPT. The body's first dword is the row's own
            # record+0x00 (0x611C95EA), so this is the first time anything a
            # player picks in FMO's mission UI arrives here identified.
            _mid = (struct.unpack_from("<I", p["payload"], 0)[0]
                    if len(p["payload"]) >= 4 else 0)
            # an Area Mission accept carries the sector the pilot picked on
            # the war map: +0x28 = 1, +0x2C = 903,000,000 + tile (0x611CE695)
            _tflag, _tsid = (struct.unpack_from("<II", p["payload"], areatargets.AREA_FLAG)
                             if len(p["payload"]) >= areatargets.AREA_TARGET + 4
                             else (0, 0))
            _area_tile = None
            _served = {mid: name for mid, _c, name in community.msn_row_table()}
            _name = _served.get(_mid)
            log(f"{self.peer}   0x{missionboard.MSG_MISSION_ACCEPT:04X} = ACCEPT MISSION, id "
                f"{_mid} ({'0x%X' % _mid}) -- "
                + (f"our row {_name!r}" if _name is not None else
                   ("WARNING: NOT a row we served. id 0 means the row carried no "
                    "MISSION ID: before 2026-09-12 every record+0x00 was zero, "
                    "so set FMO_MSN_ROWS='<id>:<name>' (or just a name, which "
                    "now takes its 1-based index)." if _mid == 0 else
                    f"WARNING: NOT one of the {len(_served)} row(s) we served "
                    f"({sorted(_served)}). Either the list is stale on the "
                    f"client or the id came from somewhere we do not author.")))
            _code, _why, _req = None, None, None
            if missionboard.MISSION_ACCEPT == "gate":
                _req = missionbook.mission_requirements().get(_mid)
                _char = self.playing_char() if charstore.CHAR_STORE else None
                # a derived mission (order) another pilot already took, or one
                # cancelled / expired: it is no longer in the requirements, so
                # without this the "no row" arm below accepted it a second time
                _taken = (missionbook.order_accept_refusal(_mid, getattr(self, "account", None))
                          if missionboard.ORDER else None)
                if community.MSN_MARK:
                    log(f"{self.peer}      WARNING: FMO_MSN=mark poisons every field "
                        f"of the record, so the requirements are offsets, not "
                        f"numbers. NOT gating on them -- accepting.")
                elif _taken is not None:
                    _code, _why = _taken
                elif _req is None:
                    log(f"{self.peer}      WARNING: no served row carries id {_mid}, "
                        f"so there are no requirements to judge -- accepting. "
                        f"A gate cannot refuse a pilot for a row it cannot see.")
                elif _char is None:
                    log(f"{self.peer}      WARNING: no stored character on this "
                        f"connection, so the pilot's rank/money/MP are the "
                        f"server-wide knobs, not this pilot's. Judging anyway; "
                        f"the source of each value is named below.")
                    _code, _why = missionbook.mission_accept_verdict(_req, {})
                elif missionbook.mission_already_accepted(_char, _mid):
                    # KEY: SE's own state, and the first refusal this server can
                    # raise from something it REMEMBERS rather than something
                    # it was configured with. Checked before rank/fee because
                    # it is a fact about what happened, not about what the
                    # pilot can do -- and because it is what stops a fee being
                    # charged twice for one mission.
                    _code, _why = -4, (f"this pilot accepted mission {_mid} "
                                       f"already -- it is on their record")
                else:
                    _code, _why = missionbook.mission_accept_verdict(_req, _char)
                if (missionboard.MISSION_PLACE and _code is None and _req is not None
                        and not community.MSN_MARK):
                    # FMO_MISSION_PLACE (Playing Manual p.59/60): Area missions
                    # only in the Strategy Room, Battle Map / Sector only where
                    # the Intelligence Officer is
                    _pcode, _pwhy = missionbook.mission_place_refusal(
                        _req.get("cat"), rooms.WORLD_ZONES.get(self.ip),
                        move.WORLD_PLACES.get(self.ip))
                    log(f"{self.peer}      PLACE: {_pwhy}"
                        + (f" -> refused, code {_pcode}" if _pcode is not None else ""))
                    if _pcode is not None:
                        _code, _why = _pcode, _pwhy
                if (areatargets.AREA_TARGETS and _code is None and _req is not None
                        and _req.get("cat") == 3 and _tflag == 1):
                    _zn = rooms.WORLD_ZONES.get(self.ip)
                    _na = (zoneentry.nation_for_session(_char, status.STATUS_NATION,
                                                        "FMO_STATUS_NATION")[0]
                           if _char else None)
                    _t = (int(_tsid) - fmomsn.SECTOR_ID_BASE) & 0xFFFFFFFF
                    _taken = (areatargets.area_target_taken(_zn, _t, self.account)
                              if _t in areatargets.area_target_tiles(_zn, _na) else None)
                    if _taken:
                        _code, _why = -8, (
                            f"tile {_t} in zone {_zn} is already the target "
                            f"of {_taken[1]!r}, an active area mission of "
                            f"{_taken[0]}")
                    elif _t in areatargets.area_target_tiles(_zn, _na):
                        _area_tile = _t
                        log(f"{self.peer}      AREA TARGET: the pilot picked "
                            f"sector id {_tsid} = tile {_t} in zone {_zn} "
                            f"(+0x28 = {_tflag}); it is on the list 0x01A9 "
                            f"served, so it becomes this accept's sector. Met "
                            f"when the war state shows it nation {_na} at >= "
                            f"{areatargets.AREA_CONTROL}% (FMO_AREA_CONTROL).")
                    else:
                        _code, _why = -9, (
                            f"the picked sector id {_tsid} (tile {_t}) is not "
                            f"a target this pilot may choose in zone {_zn}: "
                            f"not in the zone, no battle map, or already "
                            f"nation {_na}'s")
                if _req is not None and not community.MSN_MARK:
                    log(f"{self.peer}      GATE: row {_req['name']!r} wants "
                        f"rank {_req['rank']} ({ranks.rank_name(_req['rank'])}) "
                        f"and a Fee of {'MP' if missionboard.MISSION_FEE_MP else 'H$'} "
                        f"{_req['fee']}; it pays MP "
                        f"{_req['reward_mp']}/H$ {_req['reward_hs']} -- {_why}")
            if missionboard.MISSION_ACCEPT == "1" or (missionboard.MISSION_ACCEPT == "gate"
                                                      and _code is None):
                _fee = 0
                if (missionboard.MISSION_FEE and missionboard.MISSION_ACCEPT == "gate"
                        and _req and _req.get("fee")):
                    _fee = int(_req["fee"])
                    # KEY: FMO_MISSION_FEE_MP (default): SE's Fee is MP --
                    # 「Fee(MP)を支払ってそのミッションを受けたことになります」
                    # (guide/mission) -- and it is never refunded, cancel
                    # included. The gate above already refused a pilot short
                    # of it with -7. No push carries MP on its own, so the
                    # on-screen MP catches up at the next 0x014A.
                    _mpc = self.playing_char() if (missionboard.MISSION_FEE_MP
                                                   and charstore.CHAR_STORE) else None
                    if missionboard.MISSION_FEE_MP and _mpc is None:
                        log(f"{self.peer}      WARNING: FMO_MISSION_FEE=1 but there "
                            f"is no stored pilot to charge the Fee (MP {_fee}) "
                            f"-- accepting for FREE. A fee nobody paid must "
                            f"not read as paid.")
                        _fee = 0
                    elif missionboard.MISSION_FEE_MP:
                        _mpw, _mpn = economy.spend_mp(_mpc, _fee)
                        try:
                            self.commit(f"mission Fee for {_req['name']!r} (id "
                                        f"{_mid}): MP {_mpw} -> {_mpn}")
                        except Exception as _e:
                            log(f"{self.peer}      WARNING: the Fee (MP {_fee}) "
                                f"was NOT banked ({_e!r})")
                        log(f"{self.peer}      CHARGED the Fee, MP {_fee}: MP "
                            f"{_mpw} -> {_mpn} (FMO_MISSION_FEE_MP=1, SE's "
                            f"「必要階級 + 必要MP」). Not refunded on cancel. "
                            f"Bar: the Profile's MP is down {_fee} after a relog.")
                    # FMO_MISSION_FEE_MP=0: the old H$ charge
                    _now = (self.credit_money(
                        f"mission acceptance fee for {_req['name']!r} "
                        f"(id {_mid})", money=-_fee)
                        if not missionboard.MISSION_FEE_MP else None)
                    if missionboard.MISSION_FEE_MP:
                        pass                 # charged (or not) in MP above
                    elif _now is None:
                        log(f"{self.peer}      WARNING: FMO_MISSION_FEE=1 but there "
                            f"is no stored pilot to charge -- accepting for "
                            f"FREE. A fee nobody paid must not read as paid.")
                        _fee = 0
                    else:
                        log(f"{self.peer}      CHARGED H$ {_fee}: money := "
                            f"{_now[0]}. No 0x01A1 push -- its +0x08=0 says "
                            f"8:74 'sortie cost for a modified unit', which "
                            f"this is not -- so the on-screen H$ is STALE "
                            f"until the next 0x014A. Bar: the Profile is down "
                            f"{_fee} AFTER A RELOG. A second accept of an "
                            f"active row is refused with -4 before any charge.")
                _kept = None
                if missionboard.MISSION_ACCEPT == "gate" and _req is not None:
                    # the battlefield's ZONE: the row's own (FMO_MSN_ZONES)
                    # or the area the pilot is standing in -- SE's list is
                    # per area, and a tile is only a place inside one grid
                    _zone = _req.get("zone") or rooms.WORLD_ZONES.get(self.ip)
                    _nat = (zoneentry.nation_for_session(_char, status.STATUS_NATION,
                                                         "FMO_STATUS_NATION")[0]
                            if _char else None)
                    _kept = self.accept_mission(
                        _mid, _req["name"], _fee,
                        reward=(_req["reward_hs"], _req["reward_mp"]),
                        sector=_area_tile or _req.get("sector"), zone=_zone,
                        cat=_req.get("cat"), nation=_nat,
                        wins=_req.get("wins"),
                        control=areatargets.AREA_CONTROL if _area_tile else None)
                    if _kept:
                        _nm = _kept[-1]
                        # the CONTRIBUTION it pays on its report, snapshotted
                        # like the reward so a re-tuned FMO_MISSION_CONTRIB
                        # cannot change an accepted mission's pay
                        _rc = missionbook.mission_contribution(_nm.get("cat"))
                        if _rc:
                            _nm["reward_contrib"] = _rc
                            try:
                                self.commit(f"mission {_mid} pays contribution {_rc} "
                                            f"on its report (FMO_MISSION_CONTRIB)")
                            except Exception as _e:
                                log(f"{self.peer}      WARNING: contribution snapshot NOT "
                                    f"banked ({_e!r}); the report pays by category")
                        log(f"{self.peer}      SNAPSHOT: key {missionbook.mission_key(_nm)} "
                            f"(record+0x00 for this accept; mission id {_mid} "
                            f"in the low 16 bits), category {_nm.get('cat')}, "
                            f"battlefield tile {_nm.get('sector') or 0} in zone "
                            f"{_nm.get('zone') or '?'}, nation {_nat}"
                            + (f", needs {_nm.get('needed')} win(s) there "
                               f"(FMO_MISSION_WINS / FMO_MSN_WINS)"
                               if _nm.get("cat") == 2 else ""))
                log(f"{self.peer}   -> 0x{missionboard.MSG_MISSION_ACCEPT_REPLY:04X} empty = "
                    f"ACCEPTED (21:25 'This mission has been accepted.'). The "
                    f"parse 0x6122B670 reads nothing and the dispatcher checks "
                    f"only the id, so an empty reply is the whole contract. "
                    + (f"VERIFIED: RECORDED: {len(_kept)} accepted mission(s) on this "
                       f"pilot's record, so it survives a relog, it shows on "
                       f"Check Mission -> Accepted Mission, and accepting it "
                       f"again is refused with 27:0. WARNING: Still no deadline and "
                       f"no report path." if _kept else
                       "WARNING: Nothing is RECORDED: no accepted state, no deadline "
                       "-- the mission is accepted on screen only."))
                _acc = [packet.build(missionboard.MSG_MISSION_ACCEPT_REPLY, b"",
                                     self.reply_seq(), p["conn"])]
                # a story mission's byte moved 0 -> 1 (missionbook.story_accept_apply):
                # hand the client the flag block now, the story gates tool's push
                if (_kept and _kept[-1].get("story_byte") is not None
                        and missionbook.MISSION_ACCEPT_STORY == 1):
                    _acc.append(scriptcall.flags_push_packet(self, p["conn"], self.playing_char()))
                    log(f"{self.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} FLAGS push "
                        f"(queue seq 0x{pushes.QUEUE_SEQ:08X}): story byte "
                        f"{_kept[-1]['story_byte']} = 1 in the owned table, no record, "
                        f"no money (FMO_MISSION_ACCEPT_STORY=1)")
                return _acc
            if _code is not None:
                log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} REFUSED BY THE GATE, "
                    f"code {_code} (wire 0x{_code & 0xFFFF:04X}): the client "
                    f"draws {missionboard.mission_refusal_text(_code)}. This one IS about "
                    f"the pilot -- {_why}.")
                return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(),
                                     _code & 0xFFFF)]
            try:
                _code = int(missionboard.MISSION_ACCEPT, 0)
            except ValueError:
                log(f"{self.peer}   WARNING: FMO_MISSION_ACCEPT={missionboard.MISSION_ACCEPT!r} is "
                    f"neither '1' nor an integer -- accepting, which is the "
                    f"behaviour every build before this knob had.")
                return [packet.build(missionboard.MSG_MISSION_ACCEPT_REPLY, b"",
                                     self.reply_seq(), p["conn"])]
            log(f"{self.peer}   -> 0x{charselect.MSG_FAIL:04X} REFUSED, code {_code} "
                f"(on the wire as 0x{_code & 0xFFFF:04X}): the client's mapper "
                f"0x611C4B30 draws {missionboard.mission_refusal_text(_code)} under 21:22 "
                f"'Confirmation'. FMO_MISSION_ACCEPT={missionboard.MISSION_ACCEPT!r}; this "
                f"is a DELIBERATE refusal, not a gate on anything the pilot is.")
            return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(), _code & 0xFFFF)]

        if (p["msg"] == areatargets.MSG_ORDER_REQ and charselect.ANSWER_LOBAPI
                and missionboard.ORDER):
            # KEY: THE ORDER (0x0194 -> 0x0195): a sector / area mission's
            # taker issues a derived mission (missionboard's ORDER block).
            # Body = payload + 0x20; +0x000 template, +0x004 source key,
            # +0x038 op time, +0x13C base / +0x140 chosen Order MP, +0x144
            # percent (0x611C4CF0, re-read 2026-09-30).
            _ob = bytes(p["payload"][areatargets.ORDER_BODY:areatargets.ORDER_BODY + 0x14C])
            _char = self.playing_char() if charstore.CHAR_STORE else None
            if len(_ob) < 0x148 or _char is None:
                log(f"{self.peer}   0x0194 = ORDER: "
                    + ("too short" if len(_ob) < 0x148 else "no pilot on this connection")
                    + f" -> 0x0002 code {missionboard.ORDER_CODE_FAIL} (27:4)")
                return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(),
                                     missionboard.ORDER_CODE_FAIL & 0xFFFF)]
            self.log_order(p["payload"])       # every field named, kept on the pilot
            _of = {o: struct.unpack_from("<I", _ob, o)[0] for o in areatargets.ORDER_FIELDS}
            _oname = _ob[areatargets.ORDER_NAME:areatargets.ORDER_NAME + areatargets.ORDER_NAME_LEN
                         ].split(b"\0")[0].decode("cp932", "replace")
            _ocm = _ob[areatargets.ORDER_COMMENT:areatargets.ORDER_COMMENT + areatargets.ORDER_COMMENT_LEN
                       ].split(b"\0")[0].decode("cp932", "replace")
            for _m, _n in missionbook.order_expire_apply(_char):
                log(f"{self.peer}   ORDER {_m['derived']} {_m.get('name')!r} expired "
                    f"untaken: Order MP {_n} refunded")
            _e, _code, _why = missionbook.order_create(_char, self.account, _of,
                                                      issuer=_oname, comment=_ocm)
            if _e is None:
                log(f"{self.peer}   0x0194 = ORDER from key {_of[0x004]} (template "
                    f"{_of[0x000]:#x}, chosen MP {_of[0x140]}) REFUSED: {_why} -> "
                    f"0x0002 code {_code} (the poll 0x611CD170's failure arm)")
                return [packet.build(charselect.MSG_FAIL, b"", self.reply_seq(),
                                     _code & 0xFFFF)]
            try:
                self.commit(f"ORDER: {_why}")
            except Exception as _e2:
                log(f"{self.peer}   WARNING: the order was NOT banked ({_e2!r})")
            log(f"{self.peer}   0x0194 = ORDER by {_oname!r}, comment {_ocm!r}: {_why}. "
                f"-> 0x{missionboard.MSG_ORDER_REPLY:04X} empty: the client draws 21:27 "
                f"'This mission has been ordered.' It is listed for other pilots "
                f"(category {_e['cat']}) until taken; Cancel refunds the Order MP "
                f"while it is still Ordered.")
            return [packet.build(missionboard.MSG_ORDER_REPLY, b"", self.reply_seq(), p["conn"])]

        if (p["msg"] == missionboard.MSG_MISSION_CANCEL and charselect.ANSWER_LOBAPI
                and missionboard.ORDER and len(p["payload"]) >= 4):
            # a CANCEL of one of this pilot's own ORDERS (its key is the
            # derived id): refund the Order MP only while it is still
            # Ordered (guide/mission 154-155); anything else falls through
            _ok = struct.unpack_from("<I", p["payload"], 0)[0]
            _char = self.playing_char() if charstore.CHAR_STORE else None
            _m, _was, _ref = (missionbook.order_cancel_apply(_char, _ok)
                              if _char else (None, None, 0))
            if _m is not None:
                if _was == "ordered":
                    try:
                        self.commit(f"ORDER {_m['derived']} cancelled, Order MP "
                                    f"{_ref} refunded")
                    except Exception as _e2:
                        log(f"{self.peer}   WARNING: order cancel NOT banked ({_e2!r})")
                log(f"{self.peer}   0x0196 = CANCEL of ORDER {_m['derived']} "
                    f"{_m.get('name')!r}: was {_was}"
                    + (f" -> cancelled, Order MP {_ref} REFUNDED" if _was == "ordered"
                       else " -- only an Ordered mission can be cancelled; nothing "
                            "refunded")
                    + f" -> 0x{missionboard.MSG_MISSION_CANCEL_REPLY:04X} empty")
                return [packet.build(missionboard.MSG_MISSION_CANCEL_REPLY, b"",
                                     self.reply_seq(), p["conn"])]

        if (p["msg"] in (missionboard.MSG_MISSION_REPORT, missionboard.MSG_MISSION_CANCEL)
                and charselect.ANSWER_LOBAPI and missionboard.MISSION_REPORT):
            # KEY: REPORT (0x01C6) and CANCEL (0x0196) off the Accepted Mission
            # screen. Both bodies carry the mission id at +0x00.
            _mid = (struct.unpack_from("<I", p["payload"], 0)[0]
                    if len(p["payload"]) >= 4 else 0)
            _char = self.playing_char() if charstore.CHAR_STORE else None
            if p["msg"] == missionboard.MSG_MISSION_CANCEL:
                _m, _was = missionbook.mission_cancel_apply(_char, _mid)
                if _m is not None and _was in missionboard.MISSION_ACTIVE:
                    try:
                        self.commit(f"cancelled mission {_mid} "
                                    f"{_m.get('name')!r} (was {_was})")
                    except Exception as _e:
                        log(f"{self.peer}   WARNING: cancel of mission {_mid} NOT "
                            f"banked ({_e!r}) -- it will be back after a relog")
                log(f"{self.peer}   0x{missionboard.MSG_MISSION_CANCEL:04X} = CANCEL MISSION, "
                    f"key {_mid}"
                    + (f" = mission id {_m.get('id')}" if _m is not None else "")
                    + ": "
                    + (f"{_m.get('name')!r} {_was} -> cancelled (25:10 "
                       f"'Discarded'); it no longer blocks a re-accept. The "
                       f"fee is NOT refunded." if _m is not None
                       and _was in missionboard.MISSION_ACTIVE else
                       f"{_m.get('name')!r} is already {_was} -- nothing to "
                       f"cancel." if _m is not None else
                       "WARNING: no such accept on this pilot's record"
                       + ("" if _char else " (no pilot on this connection)"))
                    + f" -> 0x{missionboard.MSG_MISSION_CANCEL_REPLY:04X} empty. WARNING: The "
                    f"client draws 21:24 'Cancelled.' and marks its own row "
                    f"either way: its poll 0x611CAC3E only skips PENDING.")
                return [packet.build(missionboard.MSG_MISSION_CANCEL_REPLY, b"",
                                     self.reply_seq(), p["conn"])]
            _rw = {i: (r["reward_hs"], r["reward_mp"])
                   for i, r in missionbook.mission_requirements().items()}
            _before = None
            _m0 = missionbook.mission_find(_char, _mid) if _char else None
            if _m0 is not None:
                _before = _m0.get("status")
            _m, _was, _pay = missionbook.mission_report_apply(_char, _mid, rewards=_rw)
            if _m is None:
                log(f"{self.peer}   0x{missionboard.MSG_MISSION_REPORT:04X} = REPORT MISSION, "
                    f"id {_mid}: WARNING: no such accept on this pilot's record"
                    + ("" if _char else " (no pilot on this connection)")
                    + f". -> 0x{missionboard.MSG_MISSION_REPORT_REPLY:04X} {missionboard.REPLY_018F_LEN} "
                    f"zeros: state 0 has no arm, so NO dialog -- exactly what "
                    f"Report drew before this existed.")
                return [packet.build(missionboard.MSG_MISSION_REPORT_REPLY, bytes(missionboard.REPLY_018F_LEN),
                                     self.reply_seq(), p["conn"])]
            if _m.get("status") != _before or _pay:
                try:
                    self.commit(f"reported mission {_mid} {_m.get('name')!r}: "
                                f"{_was} -> {_m.get('status')}"
                                + (f", reward H$ {_pay['hs']}/MP {_pay['mp']} "
                                   f"owed at the Personnel Officer"
                                   if _pay else "")
                                + (f", contribution +{_pay['contrib']} banked "
                                   f"(FMO_MISSION_CONTRIB)"
                                   if _pay and _pay.get("contrib") else ""))
                except Exception as _e:
                    log(f"{self.peer}   WARNING: report of mission {_mid} NOT banked "
                        f"({_e!r})")
            _f = missionbook.mission_row_fields(_m)
            log(f"{self.peer}   0x{missionboard.MSG_MISSION_REPORT:04X} = REPORT MISSION, "
                f"key {_mid} = mission id {_m.get('id')} {_m.get('name')!r}: {_was}"
                + (f" -> {_m.get('status')}"
                   if (_m.get("status") or "open") != _was else "")
                + f". -> 0x{missionboard.MSG_MISSION_REPORT_REPLY:04X} {missionboard.REPLY_018F_LEN}B, "
                f"state {_f[missionboard.MR_STATE]} result {_f[missionboard.MR_RESULT]} at record "
                f"+0x{missionboard.MR_STATE:X}/+0x{missionboard.MR_RESULT:X}: the client draws "
                f"{missionbook.mission_report_text(_f[missionboard.MR_STATE], _f[missionboard.MR_RESULT])}"
                + (f" OWED H$ {_pay['hs']} / MP {_pay['mp']}: the Personnel "
                   f"Officer's paybook pays it as a 'Mission bonus' line"
                   + ("" if servicerecord.SALARY else " -- WARNING: but FMO_SALARY=0, so no "
                      "paybook is served and it stays owed")
                   if _pay else "")
                + (f"; contribution +{_pay['contrib']} BANKED now (shown at the "
                   f"next 0x014A)" if _pay and _pay.get("contrib") else "")
                + (". WARNING: Its time was up: the deadline is "
                   f"{missionbook.mission_deadline()}s from the accept." if _was == "expired"
                   else ""))
            return [packet.build(missionboard.MSG_MISSION_REPORT_REPLY,
                                 missionlist.reply_018f(_mid, _m.get("name", ""), _f),
                                 self.reply_seq(), p["conn"])]

        if (p["msg"] in coliseum.HANDLED and coliseum.COLISEUM
                and charselect.ANSWER_LOBAPI):
            # THE COLISEUM DESKS (coliseum.py): the arena list, register,
            # cancel, host, the bracket / streak board and the streak's
            # return. FMO_COLISEUM=0 leaves them to the zero stubs below.
            return self.on_coliseum(p)

        if p["msg"] in lobapi.LOBAPI:
            reply, need = lobapi.LOBAPI[p["msg"]]
            # WARNING: THE MISSION BOARD KILLS THE CLIENT (LIVE 2026-09-06, 2/2).
            # 0x018D -> our 22,872-zero 0x018E -> ~1s later the process dies with
            # an access violation at 0x61004361, `call [eax]` where eax is the
            # LOBBY's vtable pointer read from [lobby+0]: it held 0xEA338168, not
            # the real 0x6133B48C. Two crash dumps taken 107s apart (pol.exe
            # 189584 / 228832) have the SAME fault site, the SAME garbage value
            # and the SAME stack, at different heap bases -- deterministic, not
            # random heap damage. The caller 0x61005C56 is a whole-session
            # teardown, so this is the client bailing out over an already-broken
            # lobby. WARNING: This RETRACTS the note below (and the earlier
            # note) that an all-zero 0x018E is a harmless "No Missions Found":
            # it is the largest frame we ever put on the wire (22,892 B) and it
            # is not survivable. Cause not yet isolated between the SIZE and the
            # all-zero CONTENT -- FMO_LOBAPI_MARK=0x018D is the discriminating
            # probe, since it changes the content without changing the length.
            # Until then, refuse gracefully: a reply id the poller does not
            # expect takes the dispatcher's mismatch arm and shows [FMxxxxx]
            # instead of killing the session.
            if p["msg"] == missionlist.MSG_MISSION_LIST_REQ:
                if missionlist.ANSWER_018D == "short":
                    # VERIFIED: THE ACCEPTED MISSIONS. This screen is the one place a
                    # pilot's own accepts can be shown back to them, so it is
                    # served from the STORE, not from a knob.
                    _acc = missionbook.accepted_list_rows(missionbook.accepted_missions(
                        self.playing_char() if charstore.CHAR_STORE else None))
                    _sb = missionlist.reply_018e_short(rows=_acc)
                    log(f"{self.peer}   0x018D = the MISSION BOARD's list "
                        f"request. FMO_ANSWER_018D='short': -> 0x018E, "
                        f"{len(_sb)}B = 0x24 + {missionlist.S018E_ROWS} x 692-B records, "
                        f"which is the most that fits under the client's "
                        f"{packet.CLIENT_RX_BUFFER}-B RX buffer. "
                        + (f"VERIFIED: {len(_acc)} ACCEPTED mission(s) from this "
                           f"pilot's record: "
                           + ", ".join(f"{r[1]!r} (id {r[0]})" for r in _acc[:4])
                           if _acc else
                           "This pilot has accepted nothing, so every "
                           "record+0x00 is 0 and the rows draw systext 21:30 "
                           "'None'.")
                        + f" WARNING: rows past {missionlist.S018E_ROWS} read the connection "
                        f"object's own tail (the parse copies a fixed 22,836 B "
                        f"regardless), so a junk row there is expected, not a "
                        f"crash.")
                    return [packet.build(missionlist.MSG_MISSION_LIST_REPLY, _sb,
                                         self.reply_seq(), p["conn"])]
                if missionlist.ANSWER_018D == "0":
                    log(f"{self.peer}   0x018D = the MISSION BOARD's list "
                        f"request -- FMO_ANSWER_018D=0, staying silent (the "
                        f"board spins rather than crashing).")
                    return []
                log(f"{self.peer}   0x018D = the MISSION BOARD's list request. "
                    f"WARNING: FMO_ANSWER_018D={missionlist.ANSWER_018D!r}: REFUSING with message "
                    f"2 instead of the 22,872-zero 0x018E, which killed the "
                    f"client 2/2 on 2026-09-06 (AV at 0x61004361, smashed lobby "
                    f"vtable). The client shows [FMxxxxx] and stays alive. "
                    f"There is NO knob value that reproduces the crash: 'zeros' "
                    f"was removed and unknown values coerce to 'refuse'.")
                return [packet.build(2, b"", self.reply_seq(), p["conn"])]
            if not charselect.ANSWER_LOBAPI:
                log(f"{self.peer}   lobby API 0x{p['msg']:04X} -- "
                    f"FMO_ANSWER_LOBAPI=0, staying silent (the menu action "
                    f"will hang, which is the pre-2026-08-19 behaviour)")
                return []
            what = ("  = the \"Change Nations\" menu action, live-verified "
                    "2026-08-18" if p["msg"] == charselect.MSG_CHANGE_NATIONS else "")
            log(f"{self.peer}   lobby API 0x{p['msg']:04X}{what}")
            log(f"{self.peer}   -> 0x{reply:04X} with {need}B, the size its "
                f"parse method at the vtable's slot 1 READS. The dispatcher "
                f"0x61172250 only compares this id against word [this+0x0C]; "
                f"a WRONG id is not ignored, it becomes the number in "
                f"[FMxxxxx] because the mismatch arm reads our +0x08.")
            log(f"{self.peer}   WARNING: this is an ACKNOWLEDGEMENT ONLY. Nothing is "
                f"stored, deleted or changed here, so the client will act as "
                f"though an operation it asked for succeeded.")
            if p["msg"] in lobapi.MARK_LOBAPI:
                _base = lobapi.MARK_LOBAPI[p["msg"]]
                log(f"{self.peer}   PROBE: MARKED (FMO_LOBAPI_MARK): this body is "
                    f"NOT zeros -- dword i = {_base} + i, for {need // 4} "
                    f"dwords, so the values run {_base}..{_base + need // 4 - 1}. "
                    f"A number N in that range on any screen this reply feeds "
                    f"IS its own offset: dword = N - {_base}, payload byte = 4 "
                    f"x that. WARNING: PROBE: nonzero bytes here are counts, indices "
                    f"and set bits to the client -- clear the knob afterwards.")
            elif p["msg"] == 0x01AC:
                self.log_squadron_request(p["payload"])
            elif p["msg"] == missionlist.MSG_MISSION_LIST_REQ:
                log(f"{self.peer}   0x018D = the MISSION BOARD's list request "
                    f"(32 zero bytes -- 0x61172590 puts NO selector in it).")
                if missionlist.SERVE_MISSION_LIST:
                    log(f"{self.peer}   -> 0x018E mission list: "
                        f"{missionlist.mission_list_why()}")
                else:
                    log(f"{self.peer}   -> 0x018E {need} zeros (FMO_MISSION_LIST "
                        f"off). Every record+0x00 is 0, so every row draws "
                        f"systext 21:30 'None' and the board says 21:16 'No "
                        f"Missions Found'. This is data starvation, not a gate.")
            _cnat = 1
            if p["msg"] == 0x01A2:
                _cnat, _cwhy = zoneentry.script_nation(
                    self.playing_char() if charstore.CHAR_STORE else None)
                _screen = p["payload"][0] if p["payload"] else 0
                _crows = (cosmetics.cosmetics_for(_screen, _cnat)
                          if cosmetics.COSMETIC_SHOP else [])
                _cname = {cosmetics.A2_PILOT: "PILOT.LOCKER (accessories + pilot suits)",
                          cosmetics.A2_WANZER: "SETUP.CONSOLE (camo + colours + emblems)"
                          }.get(_screen, f"an UNKNOWN screen byte {_screen}")
                if _crows:
                    _ck = {}
                    for _k, _i, _e in _crows:
                        _ck[_k] = _ck.get(_k, 0) + 1
                    log(f"{self.peer}   0x01A2 byte {_screen} = {_cname} -> "
                        f"0x01A3 with {len(_crows)} row(s) "
                        + ", ".join(f"kind {_k}x{_v}" for _k, _v in
                                    sorted(_ck.items()))
                        + f" at payload+0x{cosmetics.A3_ROW_OFF:X}, "
                        f"{{u16 id, u8 kind, u8 0, u32 price}}. Nation "
                        f"{_cnat} from {_cwhy}; rows with nation flag 0 or "
                        f"{_cnat} only. First: "
                        + ", ".join(f"{_e}" for _k, _i, _e in _crows[:4]))
                    log(f"{self.peer}      WARNING: price {cosmetics.COSMETIC_PRICE} is OURS -- "
                        f"these five tables carry no money field (unlike the "
                        f"PART tables, where SE's buy price really is in the "
                        f"client). FMO_COSMETIC_SHOP=0 restores the zeros.")
                else:
                    log(f"{self.peer}   0x01A2 byte {_screen} = {_cname} -> "
                        f"0x01A3 with {need} ZEROS (count 0"
                        + ("" if cosmetics.COSMETIC_SHOP else ", FMO_COSMETIC_SHOP=0")
                        + f", catalogue {sum(len(v) for v in cosmetics.COSMETICS.values())} "
                        f"rows). An empty catalogue is what makes the outfit "
                        f"picker fall back to a default body.")
            _lp = lobapi.lobapi_payload(p["msg"], need, p["payload"],
                                        self.pol_groups if p["msg"] == 0x01AC else (),
                                        _cnat)
            if (p["msg"] == areatargets.MSG_AREA_TARGETS_REQ
                    and p["msg"] not in lobapi.MARK_LOBAPI):
                _lp = self.area_targets_reply(p["payload"], need, _cnat) or _lp
            if p["msg"] == areatargets.MSG_ORDER_REQ:
                self.log_order(p["payload"])
            _outs = [packet.build(reply, _lp, self.reply_seq(), p["conn"])]
            _ip = getattr(self, "_insignia_push", None)
            if _ip:
                self._insignia_push = None
                _ipk = self.insignia_push(p["conn"], *_ip)
                if _ipk:
                    _outs.append(_ipk)
            return _outs

        log(f"{self.peer}   no handler for msg=0x{p['msg']:04X}")
        return []


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    addressing, areachange, areatargets, battleend, battlegroups, battlemaps, charlist,
    charselect, charstore, citytable, community, cosmetics, economy, gatetool, groupchannel,
    grouplogin, handshake, hangar, identity, inventory, lobapi, loot, lobbymessage, missionblock, missionboard,
    missionbook, missionlist, move, packet, partsstock, penalty, permits, popnames, popnation, popself,
    progress, pushes, ranks, referee, resultpush, room, rooms, scriptcall, servicerecord, shop,
    sortiepush, squadron, status, timesync, warmap, withdraw, zonecontrol, zoneentry,
)
