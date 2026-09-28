"""Who is playing from which address: login tokens, the POL member behind an IP, the account
key."""
import json
import os
import threading
import time
from .deps import fmostore
from .knobs import _env_float
from .wirelog import log


#: How long an identity learned from a 0x0321 stays available to a game
#: connection from the same address. The real gap is milliseconds; this is
#: generous only so a slow client does not silently fall back to an
#: address-keyed roster.
IDENTITY_TTL = _env_float("FMO_IDENTITY_TTL", "120")

_identity_by_ip = {}
#: {ip: (account key, monotonic)} -- set only when a game connection's key trial
#: (Session.resolve_key) names the member. Lives MEMBER_WINDOW, not
#: IDENTITY_TTL: the UDP world channel asks account_for() minutes after login.
_proven_by_ip = {}
_store_lock = threading.Lock()


def remember_identity(ip, identity_hex):
    """Called on the LOGIN connection so the GAME connection can find it."""
    with _store_lock:
        _identity_by_ip[ip] = (identity_hex, time.monotonic())


#: KEY: FMO_ACCOUNT_PIN -- `<ip>=<account>[,<ip>=<account>]`, an EXPLICIT binding
#: that beats the freshest-session-row heuristic. `<account>` is a bare member
#: id (`3`) or a full key (`member:3`).
#:
#: WHY. `member_for_ip` takes the newest POL session row for the address, and
#: the comment above already concedes the hole: two POL accounts behind one
#: address collide. It bit for real 2026-09-03 -- a PCSX2 client signed in as
#: member 15 twenty-three seconds after the account holder's own member-3 login from
#: the same box, so FMO resolved the store to member:15, found no characters,
#: and offered CREATE CHARACTER to a player who has one. The character was
#: never in danger (it sits under `member:3` in the store, which is only ever
#: read here) but the screen is indistinguishable from data loss, and closing
#: the other client does NOT help: the session rows persist, so the stale
#: winner keeps winning until a fresher row for the right member is written.
#:
#: This does not replace the heuristic; it overrides it for addresses an
#: operator has declared. A box with one POL account needs nothing.
def parse_account_pin(spec):
    """`FMO_ACCOUNT_PIN` -> {ip: account key}. Pure, so the selftest can drive
    the SET path without the environment (the lesson from FMO_LOBAPI_MARK:
    an unset knob never exercises the code that can fail)."""
    out = {}
    for q in (spec or "").split(","):
        q = q.strip()
        if not q:
            continue
        ip, sep, acct = q.partition("=")
        ip, acct = ip.strip(), acct.strip()
        if not sep or not ip or not acct:
            raise ValueError(f"FMO_ACCOUNT_PIN entry {q!r} wants `<ip>=<account>`")
        if acct.isdigit():
            acct = "member:" + acct
        if not (acct.startswith("member:") or acct.startswith("addr:")):
            raise ValueError(f"FMO_ACCOUNT_PIN entry {q!r}: the account must be "
                             f"a member id, `member:<id>` or `addr:<ip>`")
        out[ip] = acct
    return out


try:
    ACCOUNT_PIN = parse_account_pin(os.environ.get("FMO_ACCOUNT_PIN", ""))
except ValueError as _e:
    raise SystemExit(str(_e))


def account_for(ip):
    """The account key for a game connection from `ip`.

    Tries, in order: an operator PIN for this address, the key the login
    connection resolved (TTL-bound carry), a fresh POL-member lookup (so a
    late consumer -- the UDP world channel's POP names, minutes after login --
    does not degrade when the TTL lapses), and the address itself. Never a
    shared bucket: a roster that silently merges two players is worse than one
    that is merely inconvenient to find.
    """
    pinned = ACCOUNT_PIN.get(ip)
    if pinned:
        return pinned
    # Serving a world datagram: THAT channel's player, not the address's newest.
    _a = getattr(worldchannel._udp_ctx, "addr", None)
    if _a is not None and _a[0] == ip:
        _c = groupchannel.WORLD_PEERS.get(_a)
        if _c is not None and _c.account:
            return _c.account
    with _store_lock:
        got = _identity_by_ip.get(ip)
        if got and time.monotonic() - got[1] <= IDENTITY_TTL:
            return got[0]
        # The member the newest game connection from this address PROVED with
        # its key (Session.resolve_key). Outranks the freshest-POL-login guess:
        # with two devices behind one address, that guess can name the wrong member.
        got = _proven_by_ip.get(ip)
        if got and time.monotonic() - got[1] <= MEMBER_WINDOW:
            return got[0]
    found = member_for_ip(ip)          # a DB read -- deliberately not under
    if found:                          # _store_lock
        return found[0]
    return "addr:" + ip


#: FMO_MEMBER_LOOKUP=0 disables member keying entirely (the store falls back to
#: the 0x0321 identity -- the pre-2026-08-24 behaviour, one shared roster per
#: server). The lookup reads OpenLobby's account database (POL_DATABASE_URL)
#: through `accounts`; there is no file path to configure.
MEMBER_LOOKUP = os.environ.get("FMO_MEMBER_LOOKUP", "1").strip().lower() not in (
    "", "0", "off", "no", "false")


def accounts_conn():
    """(accounts module, a connection) to the stack's account database.
    Raises when OpenLobby's accounts module or the database is unavailable;
    every caller catches that and logs it."""
    from .deps import fmodb  # noqa: F401  -- puts OpenLobby's services/ on sys.path
    import accounts
    return accounts, accounts.connect()

#: How far back a POL session row still names the member at this address. The
#: FMO launch always follows a POL login from the same box, so the freshest
#: row is right by construction; the window only stops a months-old row in a
#: copied database from being read as "the player at this address".
MEMBER_WINDOW = _env_float("FMO_MEMBER_WINDOW", "86400")


def member_for_ip(ip):
    """The POL member last signed in from `ip`, as ("member:<id>", detail),
    or None. Never raises: a DB fault must not break an FMO login, but it is
    LOGGED -- a lookup that quietly does not run is indistinguishable from one
    that ran and found nothing.

    KEY: THE PIN IS CHECKED HERE, NOT ONLY IN account_for(). It was put in
    account_for() first and that MISSED the call that decides: the 0x0321
    credentials handler calls member_for_ip() DIRECTLY, because the account is
    resolved on the login connection and then stashed for the game connection
    to adopt. The pin armed, its banner printed, and the login still resolved
    to the wrong member -- a fix that looked applied and was not. Every caller
    gets the override from here."""
    pinned = ACCOUNT_PIN.get(ip)
    if pinned:
        return pinned, (f"FMO_ACCOUNT_PIN ({ip} is pinned to {pinned}; the "
                        f"freshest-POL-session lookup was NOT consulted)")
    if not MEMBER_LOOKUP:
        return None
    try:
        acc, db = accounts_conn()
        try:
            # freshest first: (member_id, nick, created_at)
            rows = acc.sessions_by_ip(db, ip, MEMBER_WINDOW, limit=8)
        finally:
            db.close()
    except Exception as e:
        log(f"WARNING: POL member lookup for {ip} failed ({e!r}) -- falling back "
            f"to the 0x0321 identity, which COLLIDES across machines against "
            f"our K=0 login")
        return None
    if not rows:
        return None
    mid, nick, at = rows[0]
    others = sorted({r[0] for r in rows} - {mid})
    if others:
        # WARNING: THIS LINE USED TO READ BACKWARDS. It opened with the members it
        # did NOT pick and named the winner in a parenthetical, so the obvious
        # reading was "we chose {others}". Winner first now, and it names the
        # knob that fixes it -- 2026-09-03, when this cost a live session.
        log(f"WARNING: {ip} RESOLVES TO member:{mid} ({nick!r}, session {at}) -- the "
            f"FRESHEST row wins, and this address ALSO has recent POL sessions "
            f"for member(s) {others}. Two POL accounts on one box DO collide "
            f"here (lobby-session-binding-is-per-IP). If member:{mid} is the "
            f"WRONG player, that is this collision: the loser's roster reads "
            f"as EMPTY and the client offers Create Character. Fix it with "
            f"FMO_ACCOUNT_PIN={ip}=<the right member id>; closing the other "
            f"client does NOT help, because its session rows persist.")
    return f"member:{mid}", f"POL login {nick!r} (member {mid}) at {at}"


def store_accounts():
    """Every account key in the store that has at least one character."""
    if charstore.use_db():
        return fmostore.store_accounts()
    if not charstore.CHAR_STORE:
        return []
    with _store_lock:
        try:
            with open(charstore.CHAR_STORE, encoding="utf-8") as fh:
                return [k for k, v in json.load(fh).items() if v]
        except (OSError, ValueError):
            return []


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, groupchannel, worldchannel  # noqa: E402
