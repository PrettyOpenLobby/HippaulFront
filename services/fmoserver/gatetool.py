"""The story gates tool's live side (fmogates.py is the pure half).

Which pilots exist, which are online, and how an edit reaches them:

  - OFFLINE: the record is read from the character store, edited, written
    back. The next login's 0x014A serves it.
  - ONLINE: the session holds its roster in memory and writes it back on
    every commit, so a store write from here would be overwritten. The edit
    is queued on the session instead and applied on the session's own thread
    at its next keepalive (0x0198, every 15 s): the record changes, the
    session commits, and a flag change goes out as a 0x015A whose owned
    table carries the new flag block (lobby+0x8C8, which holds the kind-11
    bitmap at lobby+0xB88) -- the same push the script-call answer and the
    battle result use. No mission record rides it (+0xAD8 = 1), and the
    three script bytes +0x418..+0x41A are the pilot's penalty bytes
    (penalty.py). Rank and the Pilot class exp are not in that
    table: they reach the client at the next Start Game (0x014A).
"""
import collections
import copy
import json
import os
import threading
import time

from .deps import fmogates, fmostore
from .wirelog import log

#: a session counts as online this long after its last keepalive
ONLINE_WINDOW = 45.0
_LOCK = threading.Lock()
#: recent changes, newest last: (pilot id, line)
_LOG = collections.deque(maxlen=300)


def _note(pid, line):
    with _LOCK:
        _LOG.append((pid, time.strftime("%H:%M:%SZ ", time.gmtime()) + line))


def _sentence(what):
    t = "; ".join(what)
    return t[:1].upper() + t[1:]


def _pid(account, char):
    return "%s|%s" % (account, char.get("id"))


def _faction(char):
    nat, _src = popnation.character_nation(char)
    return fmogates.FACTIONS.get(nat) if fmogates else None


def _name(char):
    n = ("%s %s" % (char.get("first") or "", char.get("last") or "")).strip()
    return n or "Pilot %s" % char.get("id")


def live_session(account):
    """The online session for an account, or None."""
    now = time.monotonic()
    for s in list(trade.LIVE_SESSIONS.values()):
        try:
            if s.account == account and now - getattr(s, "gate_seen", -1e9) < ONLINE_WINDOW:
                return s
        except Exception:
            continue
    return None


def _accounts():
    if charstore.use_db() and fmostore:
        return fmostore.store_accounts()
    if not charstore.CHAR_STORE:
        return []
    try:
        with open(charstore.CHAR_STORE, encoding="utf-8") as fh:
            return sorted(json.load(fh))
    except (OSError, ValueError):
        return []


def _roster(account):
    s = live_session(account)
    return (s.roster if s is not None else charstore.load_roster(account)), s


def pilots():
    out = []
    for acct in _accounts():
        roster, s = _roster(acct)
        for c in roster or []:
            if not (c.get("first") or c.get("last")):
                continue
            out.append({"id": _pid(acct, c), "name": _name(c), "account": acct,
                        "faction": _faction(c), "rank": int(c.get("rank") or 0),
                        "level": fmogates.pilot_level(c),
                        "online": s is not None,
                        "pending": len(getattr(s, "gate_ops", None) or []) if s else 0})
    return sorted(out, key=lambda p: (not p["online"], p["name"].lower()))


def _find(pid):
    acct, _, cid = (pid or "").rpartition("|")
    if not acct:
        return None, None, None, None
    roster, s = _roster(acct)
    for c in roster or []:
        if str(c.get("id")) == cid:
            return acct, roster, c, s
    return acct, roster, None, s


def state(query, actor=None):
    """GET gates.json?pilot=<id> -> (status, body)."""
    if fmogates is None:
        return 503, {"ok": False, "msg": "The story gates module is missing."}
    ps = pilots()
    want = (query.get("pilot") or [""])[0]
    if want not in {p["id"] for p in ps}:
        want = ps[0]["id"] if ps else ""
    body = {"pilots": ps, "pilot": None}
    if want:
        _acct, _roster_, char, _s = _find(want)
        if char is not None:
            body["pilot"] = next(p for p in ps if p["id"] == want)
            body["state"] = fmogates.pilot_state(char, _faction(char),
                                                 static=bool(query.get("static")))
            with _LOCK:
                body["log"] = [ln for pid, ln in _LOG if pid == want][-40:]
    return 200, body


def edit(body, actor=None):
    """POST gates/edit {pilot, op, ...} -> (status, body)."""
    if fmogates is None:
        return 503, {"ok": False, "msg": "The story gates module is missing."}
    pid = body.get("pilot")
    acct, roster, char, s = _find(pid)
    if char is None:
        return 404, {"ok": False, "msg": "No such pilot."}
    op = {k: v for k, v in body.items() if k != "pilot"}
    fac = _faction(char)
    # judged on a copy first, so a refused edit is refused here and now even
    # when the real one is applied later by the session
    try:
        _fc, what = fmogates.apply_op(copy.deepcopy(char), fac, op)
    except ValueError as e:
        return 200, {"ok": False, "msg": str(e)}
    who = actor or "tool"
    if s is not None:
        q = getattr(s, "gate_ops", None)
        if q is None:
            q = s.gate_ops = []
        q.append((char.get("id"), op, who))
        _note(pid, "%s: %s (waiting for the game)" % (who, "; ".join(what)))
        log(f"story gates: {who} queued for {acct} pilot {char.get('id')}: {'; '.join(what)}")
        return 200, {"ok": True, "queued": True,
                     "msg": "%s. Reaches the game within 15 seconds." % _sentence(what)}
    fmogates.apply_op(char, fac, op)
    charstore.save_roster(acct, roster)
    _note(pid, "%s: %s" % (who, "; ".join(what)))
    log(f"story gates: {who} set {acct} pilot {char.get('id')} (offline): {'; '.join(what)}")
    return 200, {"ok": True, "msg": "%s. Applies at the next login." % _sentence(what)}


def gate_ops_due(sess, conn_id):
    """Called from the session's keepalive: note it is online, apply what the
    tool queued for it, and return the pushes to send."""
    sess.gate_seen = time.monotonic()
    out = []
    # PENALTY POINTS a victim gave this pilot (0x017C, penalty.give_point):
    # applied here, on the session's own thread, like a story-gates edit; a
    # change of the three script bytes goes out in the flags push below.
    if penalty.apply_due(sess):
        sess.flags_push_due = True
    # a server-side flag change outside a script call (a mission cleared on
    # the return from a sortie): pushed here, once the pilot is in a lobby
    if getattr(sess, "flags_push_due", False):
        sess.flags_push_due = False
        _pc = sess.playing_char()
        if _pc is not None:
            out.append(scriptcall.flags_push_packet(sess, conn_id, _pc))
            log(f"{sess.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} progress flags "
                f"refreshed after the sortie return (owned table, no record)")
    ops = getattr(sess, "gate_ops", None)
    if not ops or fmogates is None:
        return out
    sess.gate_ops = []
    push = False
    for cid, op, who in ops:
        char = next((c for c in sess.roster if c.get("id") == cid), None)
        if char is None:
            log(f"{sess.peer}   story gates: pilot {cid} is gone from this roster; dropped {op}")
            continue
        pid = _pid(sess.account, char)
        try:
            fc, what = fmogates.apply_op(char, _faction(char), op)
        except ValueError as e:
            _note(pid, "%s: refused by the game session: %s" % (who, e))
            continue
        playing = sess.playing_char()
        push = push or (fc and playing is not None and playing.get("id") == cid)
        sess.commit("story gates by %s: %s" % (who, "; ".join(what)))
        _note(pid, "%s: %s (applied)" % (who, "; ".join(what)))
    if push:
        char = sess.playing_char()
        owned = bytearray(status.reply_014a(char=char)[
            status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN])
        fl = fmogates.flags_of(char)
        o = status.S14A_FLAGS11 - status.S14A_OWNED
        owned[o:o + status.S14A_FLAGS11_LEN] = bytes(fl[:status.S14A_FLAGS11_LEN])
        # +0x418..+0x41A = the pilot's CURRENT penalty bytes (pilot=), never
        # an echo: the arm stores them on every 0x015A (0x6117E958)
        out.append(resultpush.result_push_packet(
            conn_id, record=b"", money=0, contribution=0, owned=bytes(owned),
            pilot=char))
        nz = ", ".join("%d=%#04x" % (i, fl[i]) for i in range(len(fl)) if fl[i]) or "none"
        log(f"{sess.peer}   -> 0x{resultpush.MSG_RESULT_PUSH:04X} STORY GATES push "
            f"({resultpush.S15A_BODY_LEN}B, queue seq 0x{pushes.QUEUE_SEQ:08X}): owned "
            f"table with the edited flags (lobby+0x8C8), no record (+0xAD8 = 1), "
            f"no money; flag bytes now {nz}")
    return out


def routes():
    """The story gates' pages and endpoints on the FMO tool port."""
    page = lambda _q, _a: (200, fmogates.PAGE if fmogates else "missing", "text/html")
    return {("GET", "/gates"): page, ("GET", "/gates.json"): state,
            ("POST", "/gates/edit"): edit}


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    charstore, penalty, popnation, pushes, resultpush, scriptcall, status, trade,
)
