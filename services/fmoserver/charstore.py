"""The character store: the per-account roster in the database or the JSON file, and the 0x013E
creation record."""
import json
import os
import struct
import threading
import time
from .deps import fmostore, fmoworld
from .wirelog import log


def name_clash(rosters, first, last, account, char_id):
    """A reason string when (first, last) is already carried by another
    character in `rosters` = [(account, roster)], case-insensitive; None when
    free. The slot being named (this account, this id) never clashes with
    itself, so a rename to the same name passes. SE: 「名前はユニーク」."""
    key = ((first or "").strip().lower(), (last or "").strip().lower())
    if not any(key):
        return None
    for acct, roster in rosters:
        for c in roster or ():
            if acct == account and c.get("id") == char_id:
                continue
            ck = ((c.get("first") or "").strip().lower(),
                  (c.get("last") or "").strip().lower())
            if ck == key:
                return (f"the name {first!r} {last!r} is already in use "
                        f"(account {acct}, id {c.get('id')}) -- SE: names are "
                        f"unique; refused with code 0xC43B = 17:117")
    return None
#: KEY: TRAINING GATE (2026-09-12). SE: a new pilot greets the sergeant, is sent
#: on a training sortie, and only then is "recognised as a soldier and may
#: sortie to the battlefields" (intro/flow.html:4-8). The sergeant's script
#: event 205 sets progress byte 128 = 99 (fmo-events.tsv); a 0x0139 from a
#: pilot without it is refused. '0' = the door is open to everyone, as before.
TRAINING_GATE = (os.environ.get("FMO_TRAINING_GATE", "").strip() or "1") != "0"
#: KEY: NAME UNIQUENESS (2026-09-12). SE: 「名前はユニーク」 (caramake2:5-6). The
#: creation screen's name step (0x0177) failure arm keys on error code
#: 0xC43B (0x61178CDE `cmp di,0xC43B`), which its message table 0x613958E0
#: maps to 17:117 "That name is already in use. Please choose a different
#: name." -- the ONE refusal code on that screen that draws a real message.
NAME_UNIQUE = (os.environ.get("FMO_NAME_UNIQUE", "").strip() or "1") != "0"
NAME_TAKEN_CODE = 0xC43B
#: KEY: RESERVED NAMES (2026-09-30). Manual p.38: the name check answers
#: either "already in use" or "This name cannot be used". The second is SE's
#: code 0xC90E (also 0xC8F9), which the error table 0x613955F0 maps to 17:118
#: "That name cannot be used. Please choose a different name."; it is one of
#: the two codes the name step's failure arm tests by value (0x61178CDE).
#: Refused: any first/last pair a lobby NPC carries (the client's own person
#: catalogue, fmoworld.NPC_CATALOGUE, and the `=First.Last` labels of
#: FMO_UDP_POP_NPC), and any name whose first name, last name or the two run
#: together is one of FMO_NAME_RESERVED (comma list, case-insensitive) --
#: staff-like words, so no pilot can pass for a GM. FMO_NAME_RESERVED=0 turns
#: the whole check off.
NAME_RESERVED_CODE = 0xC90E
NAME_RESERVED_RAW = os.environ.get("FMO_NAME_RESERVED", "").strip() or (
    "gm,gamemaster,admin,administrator,moderator,staff,support,system,sysop,"
    "squareenix,squaresoft,playonline,official,server")
NAME_RESERVED_ON = NAME_RESERVED_RAW != "0"
NAME_RESERVED_WORDS = frozenset(w.strip().lower() for w in NAME_RESERVED_RAW.split(",")
                                if w.strip() and NAME_RESERVED_ON)


def _npc_name_pairs():
    """Every (first, last) a lobby NPC carries, lower-cased."""
    out = set()
    for r in (getattr(fmoworld, "NPC_CATALOGUE", None) or {}).values():
        if r and not str(r[0]).upper().startswith("NPC"):
            out.add((str(r[0]).lower(), str(r[1]).lower()))
    for tok in os.environ.get("FMO_UDP_POP_NPC", "").split(";"):
        _, _, label = tok.partition("=")
        first, dot, last = label.strip().partition(".")
        if dot and first and last:
            out.add((first.lower(), last.lower()))
    return out


NPC_NAME_PAIRS = _npc_name_pairs()


def name_reserved(first, last, words=None, pairs=None):
    """A reason string when (first, last) may not be used, else None. Pure
    apart from the module tables it defaults to."""
    if not NAME_RESERVED_ON and words is None:
        return None
    words = NAME_RESERVED_WORDS if words is None else words
    pairs = NPC_NAME_PAIRS if pairs is None else pairs
    f, l = (first or "").strip().lower(), (last or "").strip().lower()
    if (f, l) in pairs:
        return (f"{first!r} {last!r} is a lobby NPC's name -- refused with code "
                f"0xC90E = 17:118 'That name cannot be used'")
    for part in (f, l, f + l):
        if part and part in words:
            return (f"{first!r} {last!r}: {part!r} is reserved (FMO_NAME_RESERVED) "
                    f"-- refused with code 0xC90E = 17:118 'That name cannot be used'")
    return None


def name_refusal(first, last, taken):
    """(code, why) for a name a pilot may not take, or (None, None). `taken`
    is the uniqueness clash string (or None); the reserved check runs first,
    since an NPC's name is refused whether or not a pilot holds it."""
    why = name_reserved(first, last)
    if why:
        return NAME_RESERVED_CODE, why
    if taken and NAME_UNIQUE:
        return NAME_TAKEN_CODE, taken
    return None, None

# --------------------------------------------------------------------------- #
# THE CHARACTER STORE
# --------------------------------------------------------------------------- #
# Until 2026-08-20 every character the client created was acknowledged and
# thrown away: we answered the creation messages with an empty message 1 and
# wrote nothing down, so the hangar password, nation, class and appearance all
# reached us and evaporated. The client believed each operation succeeded --
# which it did, in the client's own copy of the list, until the next login
# re-requested 0x012E and got a synthesised stub back.
#
# This is the same shape as Fantasy Earth's felobby.py --char-store: a JSON file
# keyed by account, loaded at connect, saved after every mutation. JSON and not
# SQLite for the same reason it gives: a container restart must never be able to
# leave a half-written database, and this file is small and hand-readable when
# something looks wrong.
#
# WARNING: KEYING IT IS THE AWKWARD PART, and the history matters because the obvious
# key FAILED LIVE. The 0x0321 credentials carry a 16-byte "identity" (payload
# +0x38) that measured stable-per-install across two sessions of one machine --
# and then 2026-08-24 (02:04Z vs 02:20Z) the
# Steam Deck's FIRST-EVER login and the desktop PC presented THE SAME 16 BYTES
# (cc86b939...). The blob is copied from polcore state adjacent to the
# [polcore+0xFAC] session-key material, and our K=0 login leaves that material
# ZEROED for every client -- so against OUR server it is one constant, not an
# identity, and a store keyed by it is one roster shared by every machine.
#
# So the store keys by POL MEMBER instead: authsess writes a session row
# (member_id, nick, peer_ip, created_at) to the account database on every POL
# sign-in (accounts.sessions_by_ip reads it back), and an FMO launch can only follow a POL
# login from the same box -- so the freshest session row for this address names
# the member (member_for_ip below). The 0x0321 identity survives only as the
# fallback when no session row is found, and "addr:<ip>" after that.
#
# The second half of the old problem is unchanged: the account is learned on
# the FIRST connection (the POL login) while every character message rides the
# SECOND one, so the resolved key is carried across by source address for the
# milliseconds between them, and by the field-A session token (below) with no
# address heuristic at all. WARNING: Two POL accounts behind one address still
# collide -- that is the lobby's documented per-IP limit
# (lobby-session-binding-is-per-IP), not a new one; the field-A token is the
# eventual out, because it is minted per LOGIN, not per address.
#
# VERIFIED: THE ROBUST FIX IS KNOWN AND NOT DONE HERE: packet +0x20 of our own 0x0322
# ("field A") is copied to ctx+0x7664 and comes STRAIGHT BACK as the payload of
# 0x015B -- which is why the 0x015B we receive is four zero bytes: that is what
# we put there. Putting a session token in field A would correlate the two
# connections with no IP heuristic at all. It is not done yet because field A's
# other consumer sprintfs it into "/btlreview/%x/brdata%03d.dat", so a non-zero
# value changes a resource path, and that needs its own measurement rather than
# being smuggled in with a storage change.
# VERIFIED: THE SESSION TOKEN, and why it is safe -- measured 2026-08-20.
#
# Packet +0x20 of our 0x0322 ("field A") is copied to ctx+0x7664 and comes
# STRAIGHT BACK as the payload of 0x015B on the game connection. That makes it a
# correlator between the two sockets with no address heuristic at all.
#
# It was held back because field A also becomes the `%x` in
# "/btlreview/%x/brdata%03d.dat", so a non-zero value changes a resource path.
# Reading every consumer settles it -- ctx+0x7664 has exactly FOUR readers:
#
#   0x61179EBE   the 0x015B payload            <- the echo we want
#   0x61160C0A   \
#   0x611610EE    >  kycli_btlreview.cpp, all three sprintf the path above
#   0x6116142B   /
#
# WARNING: AND THE ONE THAT LOOKED DANGEROUS IS NOT FIELD A AT ALL. 0x0130's success
# arm (0x6117B28B) builds the same path on every Start Game -- but its `%x` is
# `lobby+0x1DC`, a different value. Field A never reaches it.
#
# The btlreview sites only sprintf a name and ask 0x6113CD40 for its size; zero
# just clears a flag (0x6117B2BC does exactly that on the Start Game path). A
# file that is not there is already the normal case here -- we serve none. So a
# token changes a filename in a module that runs only on a battle-review screen,
# and changes it from one missing file to another.
#
# KEY: THE TOKEN IS THE CHARACTER'S OWN WIRE ID (2026-10-07). The 08-20 note
# above missed what the Start Game path does with that file. Static reading of
# the 2004 PC DLL (dump FrontMissionOnline.dll.mem_61000000.bin):
#
#   0x611610E0  the RECORDER writes /btlreview/<field A>/brdata000.dat and sets
#               lobby+0x78 = 1 once the header is down (0x611613B3)
#   0x61160BE0  the VIEWER (called from 0x61162033) reads /btlreview/<field A>/
#   0x6117B279  Start Game's success arm sets lobby+0x78 from whether
#               /btlreview/<lobby+0x1DC>/brdata000.dat has a size
#   0x610FB460  the script predicate the Battle Review NPC asks: lobby+0x78 != 0
#   0x61161420  resets /btlreview/<field A>/ and clears lobby+0x78; called on
#               the final "Once deleted it cannot be restored" yes (char select,
#               0x61044CCB, msg 17:155) and on leaving character creation
#               (0x610C978C, after 17:126/17:127)
#
# lobby+0x1DC is the character id the client picked from OUR roster
# (0x61173800 stores it, 0x0130 sends it back): charlist.to_wire(store id), so
# 0x1001 for a first character -- the prod log shows "selected list id 4097"
# for every pilot. With a per-login counter in field A the recorder and the
# Start Game check name different folders, so after any relog lobby+0x78 is 0
# and the review is gone, and after a server restart the counter starts again
# at 0x5A000001 and one PC appends unrelated battles into one file.
#
# So field A = to_wire(the character this login will most likely play): the
# one this account last started (this process), else its first character,
# else the id a new first character gets. Then recorder == viewer == Start
# Game reader for that character, across relogs and restarts.
#
# WARNING: FIELD A IS STILL THE 0x015B CORRELATOR, and 0x1001 is everybody's
# value. Safe because the correlation only has to hold between the 0x0322 and
# its 0x015B (about one second; 653 of 653 echoes on prod came back from the
# minting address and never twice): a value already held by ANOTHER account
# falls back to a unique counter value (that session's review does not
# persist, exactly as before), an echo is consumed once, and an echo from a
# different address than the login is refused (address fallback). Two
# devices on ONE account may share the value; it names the same account.
#
# WARNING: field A is fixed at login, BEFORE the character select. A pilot who
# picks a different character than predicted records into the predicted
# character's folder for that session (logged at Start Game as a mismatch).
#
# FMO_SESSION_TOKEN: 0 = send 0 (the pre-08-20 wire); "counter" = the
# 08-20..10-07 per-login counter; anything else (the default) = per character.
_TOKEN_MODE = os.environ.get("FMO_SESSION_TOKEN", "").strip().lower() or "1"
SESSION_TOKEN = _TOKEN_MODE != "0"
STABLE_TOKEN = SESSION_TOKEN and _TOKEN_MODE != "counter"

#: {token: (account key, monotonic, ip or None)} -- one entry per login, held
#: from the 0x0322 until its 0x015B consumes it (or IDENTITY_TTL).
_token_account = {}
_next_token = [0x5A000000]
#: {account key: store id} -- the character this account last started (0x0130)
#: in this process. Lost on a restart, which falls back to the first character.
_last_played = {}


def note_played(account_key, store_id):
    """Remember the character an account just started, for its next token."""
    if account_key and store_id:
        with identity._store_lock:
            _last_played[account_key] = int(store_id)


def character_token(account_key, roster):
    """The field-A value for this account's most likely character: its wire
    id, which is also what the client puts in lobby+0x1DC when it is picked."""
    ids = [int(c["id"]) for c in roster or () if c.get("id")]
    with identity._store_lock:
        last = _last_played.get(account_key)
    sid = last if last in ids else (ids[0] if ids else next_free_id(roster or [], None))
    return charlist.to_wire(sid)


def _expire_tokens(now):
    for k, v in list(_token_account.items()):
        if now - v[1] > identity.IDENTITY_TTL:
            del _token_account[k]


def mint_token(account_key, ip=None, roster=None):
    """A non-zero field-A value for this login. Zero stays reserved for "no
    token". `account_key` is the RESOLVED store key (member:<id> when the POL
    session named one), so the game connection adopts the member binding.
    Per character when STABLE_TOKEN and no other account holds that value;
    otherwise a counter value no live login holds."""
    want = None
    if STABLE_TOKEN:
        if roster is None:
            roster = load_roster(account_key)
        want = character_token(account_key, roster)
    with identity._store_lock:
        now = time.monotonic()
        _expire_tokens(now)
        held = _token_account.get(want) if want else None
        if want and (held is None or held[0] == account_key):
            tok = want
        else:
            while True:
                _next_token[0] = (_next_token[0] + 1) & 0x7FFFFFFF or 1
                tok = _next_token[0]
                if tok not in _token_account and tok >= 0x10000:
                    break
        _token_account[tok] = (account_key, now, ip)
    return tok


def account_for_token(tok, ip=None, consume=False):
    """The account key a 0x015B's echoed token names, or None. With `ip`, an
    echo from another address than the login is refused; `consume` releases
    the value so the next login (any account) can hold it."""
    with identity._store_lock:
        got = _token_account.get(tok)
        if not got or time.monotonic() - got[1] > identity.IDENTITY_TTL:
            return None
        if ip and got[2] and ip != got[2]:
            return None
        if consume:
            del _token_account[tok]
        return got[0]


def _default_store():
    """`<repo>/data/fmo_characters.json`, the same file on host and in the
    container: services/ is bind-mounted at /app, so `_HERE/../data` resolves to
    `pol-server/data` either way. (Probing an absolute `/data` first is wrong on
    Windows, where it means `<current drive>/data` -- felobby.py learned that.)
    """
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        os.pardir, "data", "fmo_characters.json")


#: Empty disables the store completely and restores the synthetic LIST_COUNT /
#: LIST_NAME list -- the state every measurement before 2026-08-20 ran against.
CHAR_STORE = os.environ.get("FMO_CHAR_STORE", _default_store())

#: KEY: THE PLAYER DATABASE (2026-09-08) -- see fmostore.py for what moved into it.
#: Since 2026-09 it is the stack's PostgreSQL database (POL_DATABASE_URL), in
#: HippaulFront's own `fmo_` tables; before that it was the SQLite file fmo.db.
#:
#: `FMO_DB` is the switch: empty or 0 keeps using the JSON store, which is the
#: state every measurement before 2026-09-08 ran against. Any other value (the
#: release default, or an old `/data/fmo.db` path) uses the database. The JSON
#: file is never modified once the database is in use, so that switch is a
#: real rollback, not a hope. An empty FMO_CHAR_STORE still disables the
#: whole store, exactly as documented.
#:
#: WARNING: THE SWITCH IS ALSO THE MIGRATION. `use_db()` imports fmo_characters.json
#: the first time it finds an empty database, once, and refuses to do it again
#: while rows exist -- an import that overwrote live rows with a stale file
#: would be indistinguishable from data loss, and this server has already had
#: one screen that looked exactly like that (FMO_ACCOUNT_PIN's note).
if not fmostore:
    FMO_DB = ""
elif os.environ.get("FMO_DB") is not None:
    FMO_DB = "" if os.environ["FMO_DB"].strip() in ("", "0") else "1"
elif CHAR_STORE:
    FMO_DB = "1"
else:
    FMO_DB = ""                         # the store is off; so is the database
_db_ready = [False]
_db_lock = threading.Lock()


def use_db():
    """True to use the database, or None to stay on the JSON store.

    Does the one-shot JSON import on the first call that finds an empty
    database. Never raises: a database fault must degrade to the JSON store,
    not break a login -- but it is LOGGED, because a store that quietly does
    not run is indistinguishable from one that ran and found nothing.
    """
    if not (fmostore and FMO_DB):
        return None
    with _db_lock:
        if _db_ready[0]:
            return True
        _db_ready[0] = True             # once, whatever happens below
        try:
            fmostore.ready()
            n_a, n_c = fmostore.import_json(CHAR_STORE)
            if n_c:
                log(f"KEY: PLAYER DATABASE: imported {n_c} character(s) for "
                    f"{n_a} account(s) from {CHAR_STORE} into the database. The "
                    f"JSON file is UNTOUCHED and is now the backup -- set "
                    f"FMO_DB=0 to fall back to it.")
            else:
                log(f"player database: "
                    f"{fmostore.count()} character(s) on file")
        except Exception as e:                       # pragma: no cover
            log(f"WARNING: player database unusable ({e!r}) -- falling back "
                f"to the JSON store {CHAR_STORE}")
            return None
    return True


def load_roster(account):
    """This account's characters, oldest first. [] when there is no store."""
    db = use_db()
    if db:
        return fmostore.load_roster(account)
    if not CHAR_STORE:
        return []
    with identity._store_lock:
        try:
            with open(CHAR_STORE, encoding="utf-8") as fh:
                return json.load(fh).get(account, [])
        except (OSError, ValueError):
            return []


def save_roster(account, roster):
    """Replace this account's list. Read-modify-write so accounts cannot
    clobber each other; written to a temp file and renamed so a restart mid-save
    cannot leave a truncated store."""
    # WARNING: THE STORE SWITCH COMES FIRST. `FMO_CHAR_STORE=""` is documented as
    # disabling the whole store, and --selftest relies on that (it clears
    # CHAR_STORE at runtime around the creation exercises). use_db() computed
    # FMO_DB at import, so with this check BELOW it a selftest run inside the
    # prod container wrote two fixture pilots ('addr:selftest-create' and the
    # all-2s account) into /data/fmo.db on 2026-09-08 -- the exact hazard the
    # selftest's own comment describes. Deleted by hand; never again.
    if not CHAR_STORE:
        return
    db = use_db()
    if db:
        fmostore.save_roster(account, roster)
        return
    with identity._store_lock:
        try:
            with open(CHAR_STORE, encoding="utf-8") as fh:
                everyone = json.load(fh)
        except (OSError, ValueError):
            everyone = {}
        everyone[account] = roster
        try:
            os.makedirs(os.path.dirname(os.path.abspath(CHAR_STORE)),
                        exist_ok=True)
            tmp = CHAR_STORE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(everyone, fh, indent=1, sort_keys=True)
            os.replace(tmp, CHAR_STORE)
        except OSError as e:
            log(f"WARNING: character store {CHAR_STORE} not written: {e}")


def _name_at(payload, off):
    """One 17-byte name field, NUL-terminated."""
    return payload[off:off + 0x11].split(charselect.NUL)[0].decode("ascii", "replace")


def character_from_013e(payload):
    """The record the CREATE submit carries. Field names are the ones a
    differential named (see the 0x013E note); everything else is kept verbatim
    under `raw` so nothing the client sent is lost to our partial decode.

    KEY: STATIC READ OF THE BUILDER, 2026-08-26 (`0x61178D8F`, record R =
    creation scene+0x48, each menu named by its own title string 99:232..242
    via the getter `0x610144A0` and the descriptor table `0x61386468`):

        payload  <- scene   menu (title)                        values
        +0x26    <- +0x6E   99:235 Select Gender (17:102)       1 Male 2 Female
        +0x28    <- +0x6F   99:232 Select Nation (17:99)        1 OCU  2 USN
        +0x29    <- +0x70   99:242 Select Personality (17:107)  1..4 (17:140..143)
        +0x30    <- +0x74   99:237 Select Size / height (17:105) 1 Short 2 Avg 3 Tall
        +0x31    <- +0x75   99:241 Select Build (17:106)        1..5 (17:135..139)
        +0x32 u16<- +0x76   the FACE picker (17:104), a model id (101/103/107 seen)
        +0x34 u32<- +0x78   word 1, written at submit
        +0x39    <- +0x71   99:13 the Wanzer pick (17:113)      1-based
        +0x3A u16<- +0x72   hangar password

    WARNING: So the differential's `nation`/`sex` names are SWAPPED: its two runs were
    (OCU, male) and (USN, female), both bytes moved 1 -> 2 together, and a
    differential both models predict is not evidence. The old keys are kept
    (other code reads them and the wire is unchanged); the new keys carry the
    static names. `pop_sex_for` can be pointed at `gender` with
    FMO_UDP_POP_SEX_SOURCE=gender -- default OFF until seen on screen."""
    return {
        "id": struct.unpack_from("<I", payload, 0x00)[0],
        "first": _name_at(payload, 0x04),
        "last": _name_at(payload, 0x15),
        "nation": payload[0x26],
        "sex": payload[0x28],
        "cls": payload[0x39],
        "hangar_pw": struct.unpack_from("<H", payload, 0x3A)[0],
        "appearance": payload[0x30:0x34].hex(),
        # the static names (2026-08-26); see the docstring
        "gender": payload[0x26],
        "nation_byte": payload[0x28],
        "personality": payload[0x29],
        "size": payload[0x30],
        "build": payload[0x31],
        "face": struct.unpack_from("<H", payload, 0x32)[0],
        "raw": payload.hex(),
    }


def next_free_id(roster, wanted):
    """Honour the id the client picked when it is free -- it is the slot the
    client is already showing -- and otherwise take the lowest unused one."""
    taken = {c["id"] for c in roster}
    if wanted and wanted not in taken:
        return wanted
    i = 1
    while i in taken:
        i += 1
    return i


# Called at run time only; imported last so that import cycles resolve.
from . import charlist, charselect, identity  # noqa: E402
