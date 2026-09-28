#!/usr/bin/env python3
"""fmo_store_test.py -- the FMO character store, end to end, with no client.

Run from services/:  python ../tools/fmo_store_test.py

Covers the four things that have to hold for a character to survive a login:
create persists, a NEW session for the same account reads it back, the 0x012F
body built from it carries id/name/nation, a rename keeps the fields it does not
touch (the hangar password is the canary), and a delete removes it. Also pins
that an unknown address gets its OWN roster rather than sharing one -- a store
that silently merges two players is worse than one that loses them.

The 0x013E record here is the shape measured live on 2026-08-20; if the decode
in character_from_013e changes, this fails first.
"""
import importlib.util
import os
import struct
import sys
import tempfile
os.environ["POL_LOG_DIR"] = tempfile.gettempdir()
# KEY: ITS OWN DIRECTORY, its own DATABASE, and services/ ON THE PATH. All
# three matter: fmo.py keeps player state in the database and reaches it
# through a GUARDED `import fmostore`. Run from ../tools with only tools/ on
# sys.path that import fails, fmo.py degrades to the JSON store, and this test
# would have passed while testing nothing of the database -- which is exactly
# what it did on the first run. A fresh database per run (OpenLobby's
# tools/pgtest.py, never POL_DATABASE_URL) also stops a previous run's state
# being read as this run's.
_HOME = tempfile.mkdtemp(prefix="fmo_store_test")
sys.path.insert(0, os.path.abspath("."))
os.environ["FMO_CHAR_STORE"] = os.path.join(_HOME, "fmo_test_chars.json")
os.environ.pop("FMO_DB", None)
import fmodb  # noqa: E402
_TEST_DB = fmodb.test_database()
if _TEST_DB.__enter__() is None:
    print("SKIP -- no test database (Docker, or POL_TEST_DATABASE_URL)")
    _TEST_DB.__exit__(None, None, None)
    sys.exit(0)
s = importlib.util.spec_from_file_location("fmo", "fmo.py")
m = importlib.util.module_from_spec(s); sys.modules["fmo"] = m; s.loader.exec_module(m)

# the two REAL 0x013E payloads captured tonight
# The FourFour/FourFour character, USN, Missileer, hangar password 1111.
p = bytearray(80)
struct.pack_into("<I", p, 0, 1)
p[0x04:0x04+8] = b"FourFour"; p[0x15:0x15+8] = b"FourFour"
p[0x26] = 2; p[0x28] = 2; p[0x39] = 2
struct.pack_into("<H", p, 0x3A, 1111)
p[0x30:0x34] = bytes.fromhex("03056e00")
rec = m.character_from_013e(bytes(p))
print("decoded:", {k: v for k, v in rec.items() if k != "raw"})
assert rec["nation"] == 2 and rec["cls"] == 2 and rec["hangar_pw"] == 1111
assert rec["first"] == "FourFour" and rec["last"] == "FourFour"

sess = m.Session("203.0.113.5:1234")
m.remember_identity("203.0.113.5", "deadbeef")
print("account:", sess.account); assert sess.account == "deadbeef"
sess.apply_charsel(0x013E, bytes(p), 1)
assert len(sess.roster) == 1, sess.roster

# a second session for the same account must SEE it
s2 = m.Session("203.0.113.5:9999")
print("reloaded:", [(c["id"], c["first"], c["nation"]) for c in s2.roster])
assert len(s2.roster) == 1 and s2.roster[0]["hangar_pw"] == 1111

# the 0x012F body it produces
body = m.roster_payload(s2.roster)
assert len(body) == m.LIST_REPLY_LEN
cnt = struct.unpack_from("<I", body, 0)[0]
e = body[4:4+0x34]
print("list count", cnt, "id", struct.unpack_from("<I", e, 0)[0],
      "first", e[4:0x15].split(b"\x00")[0], "last", e[0x15:0x26].split(b"\x00")[0],
      "nation", e[0x26])
assert cnt == 1 and e[0x26] == 2 and e[4:12] == b"FourFour"

# rename, then delete
ren = bytearray(56); struct.pack_into("<I", ren, 0, 1)
ren[0x04:0x04+3] = b"Lex"; ren[0x15:0x15+5] = b"Arden"
s2.apply_charsel(0x0177, bytes(ren), 1)
s3 = m.Session("203.0.113.5:7777")
assert s3.roster[0]["first"] == "Lex" and s3.roster[0]["last"] == "Arden"
assert s3.roster[0]["hangar_pw"] == 1111, "rename must not lose the rest"
s3.apply_charsel(0x013F, b"\x01\x00\x00\x00", 1)
s4 = m.Session("203.0.113.5:6666")
assert s4.roster == [], s4.roster
print("empty payload count:", struct.unpack_from("<I", m.roster_payload([]), 0)[0])

# The field-A session token must correlate the two connections. This is the
# mechanism that replaces the address heuristic, so it gets pinned: an unminted
# token must NOT resolve, or the fallback would look like it was working.
tok = m.mint_token("cafebabe")
assert tok != 0, "zero is reserved for 'no token'"
assert m.account_for_token(tok) == "cafebabe"
assert m.account_for_token(0xDEAD) is None, "an unminted token must not resolve"
tsess = m.Session("203.0.113.11:5")
tsess.on_packet({"msg": m.MSG_GAME_HELLO, "seq": 0x1002, "conn": 0,
                 "payload": struct.pack("<I", tok)})
assert tsess.account == "cafebabe", tsess.account
print("token correlated:", hex(tok), "->", tsess.account)

# A refusal must report a REASON, so the caller can send a real failure reply
# instead of answering "done" for something that did not happen.
empty = m.Session("203.0.113.19:5")
why = empty.apply_charsel(0x013F, struct.pack("<I", 99), 99)
assert why, "deleting an id that is not there must report a reason"
print("refusal reason:", why)
assert empty.apply_charsel(0x013F, struct.pack("<I", 1), 1) is not None

# an unknown address must NOT share the roster
s5 = m.Session("203.0.113.99:1")
print("other account:", s5.account); assert s5.account == "addr:203.0.113.99"

# --------------------------------------------------------------------------- #
# KEY: THE PLAYER DATABASE (2026-09-08) -- PLAN 1.5's actual bar, minus a client.
#
# "Finish a mission, relog, the profile shows the increased money" is the first
# time anything a player does in FMO survives a login. The mission half is not
# built yet; the STORE half is, and this is what proves it: a character carries
# its own economy from creation, a write to it survives a fresh session, and
# the 0x014A body the next login receives carries the NEW number -- not the
# knob. Without this, "persisted" would rest on a log line saying STORED.
# --------------------------------------------------------------------------- #
assert m.fmostore is not None, (
    "fmo.py could not import fmostore -- every character's economy would "
    "silently fall back to the global knobs")
assert m.use_db(), "the database is not in use; this test would prove nothing"

m.remember_identity("203.0.113.7", "member:3")
econ = m.Session("203.0.113.7:1000")
p2 = bytearray(p)
struct.pack_into("<I", p2, 0, 1)
p2[0x04:0x04+3] = b"Eco"; p2[0x15:0x15+4] = b"Nomy"
econ.apply_charsel(0x013E, bytes(p2), 1)
born = econ.roster[0]
print("seeded:", {k: born.get(k) for k in m.ECON_STORE_KEYS})
for k in m.ECON_STORE_KEYS:
    assert k in born, f"a created character must carry its own {k}"

# THE WRITE. This is what a mission result / a garage purchase will do.
born["money"] = born.get("money", 0) + 5000
m.fmostore.set_flag_byte(born, 128, 99)     # "pilot registered"
econ.commit("test: mission result")

# THE RELOG. A brand-new Session, which re-reads the store from disk.
after = m.Session("203.0.113.7:2000")
got = after.roster[0]
assert got["money"] == born["money"], (got.get("money"), born["money"])
assert m.fmostore.flags_bytes(got["flags"])[128] == 99, "the flag must survive"
print("after relog:", got["first"], got["last"], "H$", got["money"],
      "flag128", m.fmostore.flags_bytes(got["flags"])[128])

# ...and the 0x014A body that login receives carries it, sourced from the
# STORE. The source string is asserted too: a body that happened to match the
# knob would otherwise pass while proving nothing.
fields = {lbl: (val, src) for lbl, _o, val, src in m.status_fields(char=got)}
assert fields["money"][0] == struct.pack("<I", born["money"]), fields["money"]
assert fields["money"][1].startswith("character store"), fields["money"][1]
body = m.reply_014a(char=got)
assert struct.unpack_from("<I", body, m.S14A_MONEY)[0] == born["money"]
assert body[m.S14A_FLAGS11 + 128] == 99
print("0x014A money at +0x%03X:" % m.S14A_MONEY,
      struct.unpack_from("<I", body, m.S14A_MONEY)[0],
      "| flag byte 128 =", body[m.S14A_FLAGS11 + 128])

# The economy is PER CHARACTER, not per account: a second pilot on the same
# account must not inherit the first one's money.
p3 = bytearray(p2); struct.pack_into("<I", p3, 0, 2)
p3[0x04:0x04+3] = b"Two"
after.apply_charsel(0x013E, bytes(p3), 2)
two = m.Session("203.0.113.7:3000").roster
assert len(two) == 2, two
assert two[1]["money"] != two[0]["money"], "two pilots share one wallet"
print("second pilot starts at H$", two[1]["money"], "not", two[0]["money"])

# --------------------------------------------------------------------------- #
# THE ACCEPTED MISSION -- the first thing a player CHOOSES that outlives them
# --------------------------------------------------------------------------- #
# Money and flags are state the server hands out. An accept is state the PLAYER
# creates, so this is the round trip that matters for missions: accept -> the
# record -> a relog -> the Accepted Mission screen still lists it, and the same
# row cannot be accepted (or charged) twice.
sess = m.Session("203.0.113.7:4000")
sess.roster                                    # load
char = sess.roster[0]
before = m.wallet_money(char)[0]
assert not m.mission_already_accepted(char, 7)
sess.playing = char.get("id")                  # attribute the accept
kept = sess.accept_mission(7, "Recon Alpha", 500)
assert kept and len(kept) == 1, kept

relog = m.Session("203.0.113.7:4001")
got = relog.roster[0]
assert m.mission_already_accepted(got, 7), "the accept must survive a relog"
assert not m.mission_already_accepted(got, 11)
kept2 = m.accepted_missions(got)
assert kept2[0]["name"] == "Recon Alpha" and kept2[0]["fee"] == 500, kept2
print("after relog: accepted", [(x["id"], x["name"]) for x in kept2])

# ...and the Accepted Mission screen (View B) is built from exactly that
body = m.reply_018e_short(rows=[(x["id"], x["name"]) for x in kept2])
rec = m.M18E_RECORDS
assert struct.unpack_from("<I", body, rec + m.ML_KEY)[0] == 7
assert body[rec + m.ML_NAME:rec + m.ML_NAME + 11] == b"Recon Alpha"
assert m.HDR + len(body) <= m.CLIENT_RX_BUFFER
print("0x018E row 0: id",
      struct.unpack_from("<I", body, rec + m.ML_KEY)[0],
      "name", body[rec + m.ML_NAME:rec + m.ML_NAME + 11].decode())

# ---- the POL member behind an address, and that member's POL groups ---------
# Both are read through OpenLobby's accounts functions (sessions_by_ip,
# member_groups) on this same database, and the rows are made with accounts'
# own functions, never by hand.
import accounts  # noqa: E402
os.environ.setdefault("POL_LOGIN_PW_KEY", "fmo-selftest")
c = accounts.connect()
mids = {}
for nm in ("FMOALPHA", "FMOBRAVO"):
    accounts.create_polid(c, nm, "Passw0rdTest")
    mids[nm] = accounts.add_member(c, nm, nm, "Passw0rdTest")
    accounts.set_handle(c, mids[nm], nm)
tok = accounts.open_session(c, mids["FMOALPHA"], nick="FMOALPHA",
                            peer_ip="198.51.100.9")
with c:                          # backdated: open_session always stamps "now"
    c.execute("UPDATE session SET created_at = %s WHERE token = %s",
              ("2000-01-01T00:00:00Z", tok))
accounts.open_session(c, mids["FMOALPHA"], nick="FMOALPHA", peer_ip="198.51.100.9")
accounts.open_session(c, mids["FMOBRAVO"], nick="FMOBRAVO", peer_ip="198.51.100.10")
accounts.open_session(c, mids["FMOALPHA"], nick="FMOALPHA", peer_ip="198.51.100.10")
found = m.member_for_ip("198.51.100.9")
assert found and found[0] == "member:%d" % mids["FMOALPHA"], found
assert "FMOALPHA" in found[1], found
two = m.member_for_ip("198.51.100.10")
assert two and two[0] in ("member:%d" % mids["FMOALPHA"],
                          "member:%d" % mids["FMOBRAVO"]), two
assert m.member_for_ip("198.51.100.77") is None
print("member_for_ip:", found[0], "/ two at one address:", two[0])

hid = accounts.primary_handle_row(c, mids["FMOBRAVO"])["id"]
for name, pending in (("Squad", 0), ("Invite", 1)):
    accounts.add_friend(c, hid, name, kind=accounts.KIND_GROUP)
    gid = accounts.group_id(c, hid, name)
    accounts.add_group_member(c, gid, "FMOBRAVO", member_handle=hid,
                              cls=accounts.GROUP_CLASS_MASTER, pending=pending)
squad = accounts.group_id(c, hid, "Squad")
c.close()
bravo = m.Session("198.51.100.20:4000")
bravo._account = "member:%d" % mids["FMOBRAVO"]
groups = bravo.pol_groups
assert set(groups) == {squad}, groups                     # the invite is left out
assert groups[squad]["class"] == accounts.GROUP_CLASS_MASTER, groups
assert groups[squad]["formed"] > 0, groups
print("pol_groups:", groups)

_TEST_DB.__exit__(None, None, None)
print("ALL OK")
