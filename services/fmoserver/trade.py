"""Player trade: the trade messages, records and the trade service between two pilots."""
import os
import struct
import time
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# PLAYER TRADE -- decoded statically 2026-08-26, NOT YET SEEN ON A WIRE.
# --------------------------------------------------------------------------- #
# The Trade menu (0x6108B26C) opens scene 0x103F when `lobby+0x7B68 == 1` (an
# offer is pending) or when the target resolver 0x61182460 returns 1. The scene
# (ctor ~0x6119E68F) owns a 0x7574-byte request object (vtable 0x6133EC60,
# tick 0x6119F0D0) whose state machine is: 0 = build/send, 1 = handed to
# 0x61173EC0, 2 = POLL BY SEQUENCE (0x61173F40 on login+0x75A4) for message
# **0x0148**, or failure 2 (code 0xC8F5 -> 13:3 "The trade was cancelled.").
# These are NOT lobby-API objects -- they use the SECOND builder 0x61199F70
# fmomsg.py cannot see -- and the trade screen sends three of them:
#
#   0x0146   4B  u32 target UnitID  the OFFER (scene arm 0x6119EB18). WARNING: The
#                                   same id, 4 zero bytes, also rides the
#                                   periodic queue 0x7A0E from the dtor
#                                   0x6119B7A3 -- a fire-and-forget LEAVE
#                                   that nothing polls for, like 0x0198.
#   0x0147   0B                     a timer POLL from tick state 0 (0x6119F331)
#   0x0144  0xD0B  my 52-dword trade block (two arms, 0x6119EC1B/0x6119EE3D)
#
# All three wait on 0x0148 = the TRADE STATE record (parser 0x6119EEC0):
#
#   +0x000  208B  my block        -> [obj+0x193]+0x8A   (+0x04 byte = my confirm)
#   +0x0D0  208B  partner block   -> partner panel +0x8A (+0xD4 byte = theirs)
#   +0x198  u32   -> panel+0x8F
#   +0x1A0  u8    STATE -> [obj+0x17A]. 0/1/6 build NO partner panel; 2..5 do;
#                 5 and 6 put the tick in state -1 (terminal, no more sends);
#                 the dtor sends the 0x0146 leave only for states 1..3.
#   +0x1A1  sz    partner first name     +0x1B2  sz  partner last name
#
# And the flag itself is a PUSH: message **0x017D**, one of seven unsolicited
# ids the lobby's dispatcher 0x6117ECC8 accepts (window 0x016B..0x0188, byte
# table 0x6117F8DC -> 0x016B/0x016C/0x0174/0x0178/0x017B/0x017D/0x0188). Its
# arm 0x6117F04D copies 452B and switches on +0x1A0:
#   0 -> 8:7 "%s.%s has offered you a trade."     lobby+0x7B68 = 1
#   1 -> 8:8 "The trade with %s.%s was cancelled." lobby+0x7B68 = 0
#   2 -> 8:9 "...completed successfully."          lobby+0x7B68 = 0, then it
#        APPLIES the trade: 7 item slots of 0x18 at +0x08 (u32,u32,u16@+8,
#        u8 flag@+0xA -> 0x6117A640), 7 at +0xE2 (-> 0x61177B30, 8:10 "Traded
#        %s."), and lobby+0x88C (money, = 0x014A payload+0x08) += [+0x1A8] -
#        [+0xD8]. Names for the %s.%s are at +0x1A5 / +0x1B2.
# WARNING: Nothing here has been sent to a client. The knob below is a REFUSAL, not
# the service: with it OFF (the default) every trade request is answered with a
# 0x0148 whose state byte is FMO_TRADE_REFUSE_STATE, which the tick reads as
# terminal, so the screen cannot hang the way 0x01AB did. The real service (the
# 0x017D push to the OTHER machine, item relay, two-sided confirm) is NOT built
# and FMO_TRADE=1 only changes the log line saying so.
MSG_TRADE_START = 0x0142              # the real OFFER
MSG_TRADE_ACCEPT = 0x0143
MSG_TRADE_UPDATE = 0x0144
MSG_TRADE_OK = 0x0145
MSG_TRADE_OFFER = 0x0146
MSG_TRADE_POLL = 0x0147
MSG_TRADE_STATE = 0x0148
MSG_TRADE_PUSH = 0x017D
TRADE_STATE_LEN = 0x1C4                # 452 = the `rep movsd 0x71` at 0x6117F06F
TRADE_MY_BLOCK = 0x000
TRADE_PARTNER_BLOCK = 0x0D0
TRADE_BLOCK_LEN = 0x34 * 4             # 208
TRADE_STATE_OFF = 0x1A0
TRADE_PUSH_FIRST = 0x1A5
TRADE_REPLY_FIRST = 0x1A1
TRADE_LAST = 0x1B2
TRADE_NAME_LEN = 0x11
#: The trade service. 0 (default) = refuse every trade request with a terminal
#: state record. 1 = same refusal, different log line -- the service is unbuilt.
TRADE = (os.environ.get("FMO_TRADE", "").strip() or "0") != "0"
#: The +0x1A0 byte of the refusal. 6 because (a) 0x6119F16B..0x6119F171 treat
#: 5 and 6 as "done" (tick state -1), (b) 0x6119EF2A..0x6119EF34 exclude 0, 1
#: and 6 from building a partner panel, and (c) the dtor 0x6119B756 sends the
#: 0x0146 leave only for 1..3. Which of 5/6 is "cancelled" vs "completed" is
#: UNMEASURED; 6 is the one that is inert on all three counts.
TRADE_REFUSE_STATE = _env_int("FMO_TRADE_REFUSE_STATE", "6")


def trade_state_record(state, first="", last="", mine=None, partner=None,
                       partner_id=0):
    """A 0x0148 TRADE STATE record, 452 bytes, every undecoded byte zero."""
    b = bytearray(TRADE_STATE_LEN)
    if mine:
        b[TRADE_MY_BLOCK:TRADE_MY_BLOCK + TRADE_BLOCK_LEN] = \
            bytes(mine[:TRADE_BLOCK_LEN]).ljust(TRADE_BLOCK_LEN, b"\0")
    if partner:
        b[TRADE_PARTNER_BLOCK:TRADE_PARTNER_BLOCK + TRADE_BLOCK_LEN] = \
            bytes(partner[:TRADE_BLOCK_LEN]).ljust(TRADE_BLOCK_LEN, b"\0")
    if partner_id:
        # WARNING: +0x198 is the PARTNER BLOCK's +0xC8 (their money offer), not an
        # id: only written when asked for explicitly.
        struct.pack_into("<I", b, 0x198, partner_id & 0xFFFFFFFF)
    b[TRADE_STATE_OFF] = state & 0xFF
    for off, text in ((TRADE_REPLY_FIRST, first), (TRADE_LAST, last)):
        s = text.encode("cp932", "replace")[:TRADE_NAME_LEN - 1]
        b[off:off + len(s)] = s
    return bytes(b)


def trade_push_record(mode, first="", last="", give=None, receive=None):
    """A 0x017D TRADE PUSH record, 452 bytes: +0x1A0 mode (0 offered,
    1 cancelled, 2 completed), the PARTNER's names at +0x1A5 / +0x1B2.
    Mode 2 is per recipient: +0x000 = the block this
    client GIVES (removed by serial, its +0xC8 money debited), +0x0D0 = the
    block it RECEIVES (appended, +0xC8 = rec+0x198 credited)."""
    if mode not in (0, 1, 2):
        raise ValueError("0x017D mode %r is not one of 0x6117F07B's arms "
                         "(0 offered, 1 cancelled, 2 completed)" % (mode,))
    b = bytearray(TRADE_STATE_LEN)
    if give:
        b[TRADE_MY_BLOCK:TRADE_MY_BLOCK + TRADE_BLOCK_LEN] = \
            bytes(give[:TRADE_BLOCK_LEN]).ljust(TRADE_BLOCK_LEN, b"\0")
    if receive:
        b[TRADE_PARTNER_BLOCK:TRADE_PARTNER_BLOCK + TRADE_BLOCK_LEN] = \
            bytes(receive[:TRADE_BLOCK_LEN]).ljust(TRADE_BLOCK_LEN, b"\0")
    b[TRADE_STATE_OFF] = mode
    for off, text in ((TRADE_PUSH_FIRST, first), (TRADE_LAST, last)):
        s = text.encode("cp932", "replace")[:TRADE_LAST - TRADE_PUSH_FIRST - 1
                                             if off == TRADE_PUSH_FIRST
                                             else TRADE_NAME_LEN - 1]
        b[off:off + len(s)] = s
    return bytes(b)


# --------------------------------------------------------------------------- #
# THE TRADE SERVICE (FMO_TRADE=1). Built from a static decode of the client;
# not yet tested with two real clients.
#   A 0x0142 {B's alias}  -> A: 0x0148 state 1; B: 0x017D mode 0 (on its next
#                            keepalive -- the only cross-session delivery path)
#   B 0x0143              -> B: state 3; A's next 0x0147 poll: state 3
#   0x0144 {my block}     -> validated against the STORE, kept, both OKs clear
#   0x0145 {mine, theirs} -> OK only if both halves equal the server's pair
#   both OK               -> re-validate from the store, apply to both pilots,
#                            each gets its own 0x017D mode 2; state 5
#   0x0146 (payload junk) -> cancel: state 6 here, 6 on the partner's poll,
#                            0x017D mode 1 if the partner never joined
# KEY: THE ANTI-DUPE RULE (the Tetra Master lesson): the server is the trade
# SERVICE. A trade completes only when BOTH sides sent 0x0145 for the CURRENT
# pair with no 0x0144 since, and everything is re-checked from the character
# store at commit -- never from the client's blocks.
# --------------------------------------------------------------------------- #
TRADE_SLOT = 0x08                      # 7 inventory entries of 0x18
TRADE_SLOTS = 7
TRADE_OK = 0x04                        # u8 "OK pressed" (display only)
TRADE_MONEY = 0xC8                     # u32 money offered
TRADE_TTL = 600                        # an idle trade is dropped after this
#: ip -> the game Session, refreshed on every 0x0198 keepalive. The trade
#: needs the PARTNER's session (their store, their push queue).
LIVE_SESSIONS = {}
#: ip -> the trade dict both sides share (see trade_open).
TRADES = {}


def trade_block_slots(block):
    """[(slot, serial, id, kind)] for the non-empty slots of a 208-B block."""
    out = []
    for i in range(TRADE_SLOTS):
        at = TRADE_SLOT + i * inventory.INV_ENTRY_LEN
        rec = block[at:at + inventory.INV_ENTRY_LEN]
        if len(rec) < inventory.INV_ENTRY_LEN or not rec[inventory.ITEM_KIND]:
            continue
        lo, hi = struct.unpack_from("<II", rec, inventory.ITEM_SERIAL_LO)
        out.append((i, lo | (hi << 32),
                    struct.unpack_from("<H", rec, inventory.ITEM_ID)[0], rec[inventory.ITEM_KIND]))
    return out


def trade_block_money(block):
    if len(block) < TRADE_MONEY + 4:
        return 0
    return struct.unpack_from("<I", block, TRADE_MONEY)[0]


def trade_block_key(block):
    """The block as compared for a confirm: the OK byte does not count."""
    b = bytearray(bytes(block[:TRADE_BLOCK_LEN]).ljust(TRADE_BLOCK_LEN, b"\0"))
    b[TRADE_OK] = 0
    return bytes(b)


def serial_equipped(char, serial):
    """True when a stored wanzer setup equips this 64-bit serial."""
    try:
        blk = bytes.fromhex((char or {}).get("setups") or "")
    except ValueError:
        return False
    for s in range(inventory.SETUP_SLOTS):
        base = s * inventory.SETUP_ENTRY_LEN
        if len(blk) < base + inventory.SETUP_ENTRY_LEN or not blk[base + inventory.SETUP_IN_USE]:
            continue
        for i in range(inventory.SETUP_ITEMS):
            at = base + inventory.SETUP_ITEM_OFF + i * inventory.INV_ENTRY_LEN
            lo, hi = struct.unpack_from("<II", blk, at + inventory.ITEM_SERIAL_LO)
            if (lo | (hi << 32)) == int(serial) and blk[at + inventory.ITEM_KIND]:
                return True
    return False


def trade_validate(char, block, money_have):
    """(clean block, problems): `block` with every slot this pilot cannot
    give cleared and the money clamped to what they hold. A slot must name an
    item in the pilot's STORED items (id and kind matching) that no setup
    equips, once. Pure."""
    b = bytearray(bytes(block[:TRADE_BLOCK_LEN]).ljust(TRADE_BLOCK_LEN, b"\0"))
    owned = {it["serial"]: it for it in inventory.stored_items(char)}
    seen, problems = set(), []
    for slot, serial, iid, kind in trade_block_slots(b):
        it = owned.get(serial)
        if it is None:
            why = "not one of the pilot's stored items"
        elif (it["id"], it["kind"]) != (iid, kind):
            why = "id/kind differ from the store"
        elif serial_equipped(char, serial):
            why = "equipped on a setup"
        elif serial in seen:
            why = "offered twice"
        else:
            why = None
        if why:
            at = TRADE_SLOT + slot * inventory.INV_ENTRY_LEN
            b[at:at + inventory.INV_ENTRY_LEN] = bytes(inventory.INV_ENTRY_LEN)
            problems.append(f"slot {slot} serial {serial:#x} ({why})")
        else:
            seen.add(serial)
    m = trade_block_money(b)
    have = max(0, int(money_have))
    if m > have:
        struct.pack_into("<I", b, TRADE_MONEY, have)
        problems.append(f"money {m} > the {have} held")
    return bytes(b), problems


def trade_apply(char, give, receive, reserved=()):
    """Move one side of a completed trade on a character record, in place:
    remove what it gives, add what it receives. A received serial this pilot
    already holds (or one in `reserved`) is RE-MINTED; returns the receive
    block as the client must see it (with any new serials). Money is NOT
    touched here (credit_money does it). Raises ValueError, changing nothing,
    when a given item is gone or the 400 cap would overflow."""
    gives = trade_block_slots(give)
    owned = {it["serial"] for it in inventory.stored_items(char)}
    missing = [s for _i, s, _d, _k in gives if s not in owned]
    if missing:
        raise ValueError("no longer holds " + ", ".join(f"{s:#x}" for s in missing))
    recv = trade_block_slots(receive)
    if len(owned) - len(gives) + len(recv) > inventory.INV_MAX:
        raise ValueError(f"would hold more than {inventory.INV_MAX} items")
    for _i, serial, _d, _k in gives:
        inventory.remove_stored_item(char, serial)
    out = bytearray(bytes(receive[:TRADE_BLOCK_LEN]).ljust(TRADE_BLOCK_LEN, b"\0"))
    held = {it["serial"] for it in inventory.stored_items(char)} | set(reserved)
    for slot, serial, iid, kind in recv:
        if serial in held:
            lo, hi = shop.mint_serial()
            serial = lo | (hi << 32)
            struct.pack_into("<II", out, TRADE_SLOT + slot * inventory.INV_ENTRY_LEN,
                             lo, hi)
        inventory.add_stored_item(char, serial, iid, kind, price=0)
        held.add(serial)
    return bytes(out)


def trade_open(a_ip, b_ip, now=None):
    """A new trade A offered to B. Both hosts point at the same dict."""
    t = {"a": a_ip, "b": b_ip, "state": "offered",
         "touched": now or time.time(), "why": "",
         "blocks": {a_ip: bytes(TRADE_BLOCK_LEN), b_ip: bytes(TRADE_BLOCK_LEN)},
         "ok": {a_ip: False, b_ip: False}}
    TRADES[a_ip] = TRADES[b_ip] = t
    return t


def trade_for(ip, now=None):
    """The trade this host is in, or None. An idle open one is cancelled."""
    t = TRADES.get(ip)
    if t is not None and t["state"] in ("offered", "open") \
            and (now or time.time()) - t["touched"] > TRADE_TTL:
        t["state"], t["why"] = "cancelled", f"idle for {TRADE_TTL}s"
    return t


def trade_partner(t, ip):
    return t["b"] if ip == t["a"] else t["a"]


def trade_release(t, ip):
    """This host has been told the end of `t`: forget it for this host."""
    if TRADES.get(ip) is t:
        del TRADES[ip]


def trade_host_of_alias(ip, alias):
    """The host whose unit `ip`'s client knows as `alias` (the room relay's
    alias_of, on any of this host's world channels), or None."""
    for ch in list(groupchannel.WORLD_PEERS.values()):
        if ch.addr[0] != ip:
            continue
        for other_addr, a in getattr(ch, "alias_of", {}).items():
            if a == alias:
                return other_addr[0]
    return None


class SessionTrade:
    """Session's side of a player trade: the requests, the pushes and the exchange."""

    def on_trade(self, p):
        """The trade screen's requests (0x0142 offer, 0x0143 accept, 0x0144
        update, 0x0145 OK, 0x0146 cancel/leave, 0x0147 poll). See the TRADE
        SERVICE block.

        Every one is answered with a 0x0148 (the tick polls by sequence and
        has no timeout -- the 0x01AB lesson), except the dtor's fire-and-forget
        leave on the queue sequence. With FMO_TRADE off every answer is the
        terminal refusal it has always been."""
        msg, pl = p["msg"], p["payload"]
        if msg == MSG_TRADE_OFFER and p["seq"] == pushes.QUEUE_SEQ:
            t = trade_for(self.ip)
            log(f"{self.peer}   0x0146 on seq 0x{p['seq']:X} = the trade "
                f"screen's LEAVE notice (dtor 0x6119B7A3). Fire-and-forget: "
                f"not answered."
                + (" Cancels the open trade." if t and t["state"] in
                   ("offered", "open") else ""))
            if TRADE and t and t["state"] in ("offered", "open"):
                self.trade_cancel(t, "the screen was closed")
            return []
        if not TRADE:
            log(f"{self.peer}   0x{msg:04X} = a trade request, {len(pl)}B. "
                f"FMO_TRADE is off (default): refusing so the screen cannot "
                f"hang. Nothing was offered to anybody.")
            return [self.trade_state_packet(p, TRADE_REFUSE_STATE)]
        LIVE_SESSIONS.setdefault(self.ip, self)
        t = trade_for(self.ip)
        if msg == MSG_TRADE_START:
            target = struct.unpack_from("<I", pl, 0)[0] if len(pl) >= 4 else 0
            b_ip = trade_host_of_alias(self.ip, target)
            other = LIVE_SESSIONS.get(b_ip) if b_ip else None
            why = ("the target is not a relayed player this server knows"
                   if b_ip is None else
                   "the partner has no live game session" if other is None else
                   "the partner is already trading" if trade_for(b_ip) and
                   trade_for(b_ip)["state"] in ("offered", "open") else
                   "you are already trading" if t and t["state"] in
                   ("offered", "open") else None)
            if why:
                log(f"{self.peer}   0x0142 = TRADE OFFER to UnitID "
                    f"0x{target:X}: REFUSED, {why} -> state 6")
                return [self.trade_state_packet(p, 6)]
            t = trade_open(self.ip, b_ip)
            n1, n2 = self.trade_names()
            other.queue_trade_push(trade_push_record(0, n1, n2),
                                   f"offer from {n1}.{n2}")
            log(f"{self.peer}   0x0142 = TRADE OFFER to UnitID 0x{target:X} = "
                f"host {b_ip}: opened; 0x017D mode 0 queued for {b_ip}'s next "
                f"keepalive (8:7 '{n1}.{n2} has offered you a trade.'); "
                f"-> state 1 (waiting for the partner)")
            return [self.trade_state_packet(p, 1, t)]
        if t is None:
            log(f"{self.peer}   0x{msg:04X} = a trade request with NO trade "
                f"open for this host -> state 6 (cancelled)")
            return [self.trade_state_packet(p, 6)]
        t["touched"] = time.time()
        if msg == MSG_TRADE_ACCEPT:
            if t["state"] == "offered" and self.ip == t["b"]:
                t["state"] = "open"
                log(f"{self.peer}   0x0143 = TRADE ACCEPT: the trade with "
                    f"{t['a']} is OPEN -> state 3; the offerer gets 3 on its "
                    f"next poll")
            else:
                log(f"{self.peer}   0x0143 = TRADE ACCEPT in state "
                    f"{t['state']!r} (not the invited side of an offer): "
                    f"answered with the current state")
        elif msg == MSG_TRADE_UPDATE and t["state"] == "open":
            char = self.playing_char() if charstore.CHAR_STORE else None
            have = economy.wallet_money(char)[0] if char else 0
            clean, problems = trade_validate(char or {}, pl, have)
            t["blocks"][self.ip] = clean
            t["ok"] = {t["a"]: False, t["b"]: False}
            log(f"{self.peer}   0x0144 = TRADE UPDATE: "
                f"{len(trade_block_slots(clean))} item(s), H$ "
                f"{trade_block_money(clean)} offered; both OKs cleared"
                + (f". CLEARED by the store check: {'; '.join(problems)}"
                   if problems else ""))
        elif msg == MSG_TRADE_OK and t["state"] == "open":
            mine = pl[:TRADE_BLOCK_LEN]
            theirs = pl[TRADE_BLOCK_LEN:2 * TRADE_BLOCK_LEN]
            partner = trade_partner(t, self.ip)
            same = (trade_block_key(mine) == trade_block_key(t["blocks"][self.ip])
                    and trade_block_key(theirs) == trade_block_key(t["blocks"][partner]))
            if same:
                t["ok"][self.ip] = True
                b = bytearray(t["blocks"][self.ip])
                b[TRADE_OK] = 1
                t["blocks"][self.ip] = bytes(b)
            log(f"{self.peer}   0x0145 = TRADE OK: "
                + ("both halves match the server's pair -> this side is OK"
                   if same else "the halves DIFFER from the server's pair "
                   "(a stale screen) -> NOT OK; the client sees the current "
                   "pair on this reply"))
            if all(t["ok"].values()):
                self.trade_commit(t)
        elif msg == MSG_TRADE_OFFER:
            log(f"{self.peer}   0x0146 = TRADE CANCEL (payload ignored: "
                f"uninitialised stack)")
            if t["state"] in ("offered", "open"):
                self.trade_cancel(t, f"{self.ip} cancelled")
        state = {"offered": 1, "open": 3, "done": 5, "cancelled": 6}[t["state"]]
        if self.ip == t["b"] and t["state"] == "offered":
            state = 1
        if state in (5, 6):
            trade_release(t, self.ip)
        return [self.trade_state_packet(p, state, t)]

    def trade_names(self, ip=None):
        n1, n2, _src = popnames.pop_names_for(ip or self.ip)
        return n1 or "", n2 or ""

    def trade_state_packet(self, p, state, t=None):
        """A 0x0148 for this side: my block, the partner's block, the
        partner's names -- the client redraws BOTH panels from it."""
        mine = partner = None
        first = last = ""
        if t is not None:
            other = trade_partner(t, self.ip)
            mine, partner = t["blocks"].get(self.ip), t["blocks"].get(other)
            first, last = self.trade_names(other)
        body = trade_state_record(state, first, last, mine=mine,
                                  partner=partner)
        log(f"{self.peer}   -> 0x0148 TRADE STATE {state}, {len(body)}B")
        return packet.build(MSG_TRADE_STATE, body, self.reply_seq(), p["conn"])

    def queue_trade_push(self, body, what):
        """A 0x017D for THIS session, delivered on its next keepalive."""
        q = getattr(self, "trade_pushes", None)
        if q is None:
            q = self.trade_pushes = []
        q.append((body, what))

    def trade_pushes_due(self, conn_id):
        out = []
        for body, what in getattr(self, "trade_pushes", None) or ():
            out.append(packet.build(MSG_TRADE_PUSH, body, pushes.QUEUE_SEQ, conn_id))
            log(f"{self.peer}   -> 0x{MSG_TRADE_PUSH:04X} TRADE PUSH "
                f"(mode {body[TRADE_STATE_OFF]}) on queue seq "
                f"0x{pushes.QUEUE_SEQ:08X}: {what}")
        self.trade_pushes = []
        return out

    def trade_cancel(self, t, why):
        """End an open or offered trade as cancelled. The partner learns it
        on its next poll (state 6); a partner who never joined gets 0x017D
        mode 1 so their 'offered' flag clears."""
        joined = t["state"] == "open"
        t["state"], t["why"] = "cancelled", why
        other = trade_partner(t, self.ip)
        if not joined:
            o = LIVE_SESSIONS.get(other)
            if o is not None:
                n1, n2 = self.trade_names()
                o.queue_trade_push(trade_push_record(1, n1, n2),
                                   f"trade cancelled ({why})")
            trade_release(t, other)
        log(f"{self.peer}   TRADE with {other} CANCELLED: {why}")

    def trade_commit(self, t):
        """Both sides OK on the same pair: re-check everything from the
        STORE, apply to both pilots, push each its own 0x017D mode 2. On any
        failure the trade is cancelled and nothing moves."""
        a, b = t["a"], t["b"]
        sa, sb = LIVE_SESSIONS.get(a), LIVE_SESSIONS.get(b)
        ca = sa.playing_char() if (sa and charstore.CHAR_STORE) else None
        cb = sb.playing_char() if (sb and charstore.CHAR_STORE) else None
        if ca is None or cb is None:
            return self.trade_cancel(t, "a pilot has no stored character")
        ga, gb = t["blocks"][a], t["blocks"][b]
        for char, blk, who in ((ca, ga, a), (cb, gb, b)):
            _c, problems = trade_validate(char, blk, economy.wallet_money(char)[0])
            if problems:
                return self.trade_cancel(
                    t, f"{who} no longer holds what it offered: "
                    + "; ".join(problems))
        ma, mb = trade_block_money(ga), trade_block_money(gb)
        import copy as _copy
        ca2, cb2 = _copy.deepcopy(ca), _copy.deepcopy(cb)
        try:
            ra = trade_apply(ca2, ga, gb)
            rb = trade_apply(cb2, gb, ga)
        except ValueError as e:
            return self.trade_cancel(t, f"the store refused it: {e}")
        ca.clear(); ca.update(ca2)
        cb.clear(); cb.update(cb2)
        sa.credit_money("trade", money=mb - ma)
        sb.credit_money("trade", money=ma - mb)
        sa.commit(f"TRADE with {b}: gave {len(trade_block_slots(ga))} item(s) + "
                  f"H$ {ma}, received {len(trade_block_slots(gb))} + H$ {mb}")
        sb.commit(f"TRADE with {a}: gave {len(trade_block_slots(gb))} item(s) + "
                  f"H$ {mb}, received {len(trade_block_slots(ga))} + H$ {ma}")
        na, nb = sa.trade_names(), sb.trade_names()
        sa.queue_trade_push(trade_push_record(2, *nb, give=ga, receive=ra),
                            "trade COMPLETED (apply)")
        sb.queue_trade_push(trade_push_record(2, *na, give=gb, receive=rb),
                            "trade COMPLETED (apply)")
        t["state"], t["why"] = "done", "both sides OK"
        log(f"{self.peer}   VERIFIED: TRADE {a} <-> {b} COMPLETED: {a} gave "
            f"{len(trade_block_slots(ga))} item(s) + H$ {ma}, {b} gave "
            f"{len(trade_block_slots(gb))} + H$ {mb}; banked on both pilots, "
            f"0x017D mode 2 queued for each (per recipient: give / receive).")


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    charstore, economy, groupchannel, inventory, packet, popnames, pushes, shop,
)
