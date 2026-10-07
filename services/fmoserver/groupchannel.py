"""Battle groups on the world channel: members, the Player List, group sorties and member blobs."""
import os
import socket
import struct
import time
from .deps import fmoworld
from .knobs import _env_int
from .wirelog import log


#: WARNING: THE WORLD CHANNELS, MODULE-LEVEL SO THE TCP HALF CAN INVALIDATE THEM.
#:
#: FMO dials the world channel from a FIXED source port (19155 in every capture),
#: so `(host, port)` is the SAME KEY across a client restart. A restarted client
#: therefore inherits the previous session's channel object -- `tx_base` already
#: advanced, `popped`/`said` already true -- while its own record indices start
#: again at 0.
#:
#: Measured 2026-08-21 19:47: `ACK 2 (ours 2+0 pending, their ack 0)` on a
#: process that had been running for thirty seconds. The POP was never sent
#: because `popped` was still True from the 19:17 session, the entity map read 0
#: keys, and the run looked exactly like cmd 7 failing. **It was our own state
#: outliving the peer it described.**
#:
#: WARNING: This is the same shape as the TM table-state invariant: server state must
#: not outlive the presence it describes. There it was tables, here it is a
#: send window.
WORLD_PEERS = {}
#: The battle-group channel's key: the literal "group" (see _serve_datagram).
GROUP_KEY = b"group"
GROUP_TABLES = fmoworld.bf_init(GROUP_KEY) if fmoworld else None
#: KEY: THE GROUP CHANNEL'S MEMBER RECORD IS A cmd-7 POP (static 2026-09-06, live
#: symptom: every Group Commands entry greyed after a successful Create). The
#: window updater 0x6116DA70 walks the group connection's peer map ([obj+0x1C])
#: for the peer whose +0x10 (UnitID) is the player's own id ([globals+0x1BC]),
#: takes [peer+0x1AF9] -- which the group/lobby peer class's POP handler
#: (0x611E580E: new(0x1B01), ctor 0x611E4870, then 0x611E48F0(body, len)) sets
#: to a COPY OF THE WHOLE POP BODY -- and reads body+0x50 == 1 as "I am the
#: leader" ([win+0x90]) and body+0x54 bits 1 / 2 / 8 as status ([win+0x88],
#: the ready flag that enables entry 8 when [obj+0xDC] & 3 == 2, [win+0x8C]).
#: No matching peer -> [win+0x90] stays -1 -> every entry greyed. We had never
#: popped anything on the group channel. Default ON: the channel only exists
#: after the player created a group, so the experiment is self-selecting.
GROUP_POP = (os.environ.get("FMO_UDP_GROUP_POP", "1").strip() or "1") != "0"
#: FMO_UDP_GROUP_LEADER: blob+0x50, the byte the battle-group menu reads to
#: decide which rows you get -- LEADER gets Change Leader / Kick / Disband /
#: Edit Comment (0x6116DBEB), NON-LEADER gets Sortie Setting / LEAVE
#: (0x6116DC5A). SE's design is that a leader disbands and a member leaves.
#:
#: WARNING: 'auto' (default) DERIVES IT: this host is the leader only if it is the one
#: that CREATED a group. A flat 1 -- what this served until 2026-09-09 -- tells
#: EVERY pilot they are the leader, which is harmless solo and actively wrong
#: the moment two people are in one group: both would be offered Disband and
#: neither Leave, and the 0x0172 LEAVE handler could never be exercised at all.
#: '1'/'0' force it for an A/B.
#: WARNING: The attribution is BY IP, because the group channel only knows the host.
#: Two pilots behind ONE address would both read as the creator -- the same
#: per-IP limit the lobby's own session binding already has. On
#: separate addresses it is exact.
GROUP_LEADER_MODE = os.environ.get("FMO_UDP_GROUP_LEADER", "auto").strip() or "auto"
GROUP_POP_LEADER = 1 if GROUP_LEADER_MODE == "auto" else int(GROUP_LEADER_MODE, 0)


#: KEY: WHO IS IN WHICH BATTLE GROUP, by ACCOUNT.
#: {GroupID: [account, ...]} in join order, and the reverse. Filled by 0x0156
#: CREATE and 0x0157 JOIN; the group channels and the 0x0158 player rows read it.
GROUP_MEMBERS = {}
GROUP_OF = {}
#: Offsets of the group peer ctor inside a cmd 190 body (0x611E4870): NOT the
#: world POP's -- key +0x0C, tag +0x3F, port +0x40 (u16), IPv4 +0x44.
G190_MODE, G190_GATE, G190_KEY = 0x04, 0x08, 0x0C
G190_TAG, G190_PORT, G190_IP = 0x3F, 0x40, 0x44
G190_VOICE = 0x58            # bit 0 = voice ON for this member (the send gate)
G190_STATUS_BIT0 = 0x01      # +0x54 bit 0: a 191 drops a member without it
#: The 0x0158 JOIN reply's Player List: u32 count at +0x80, 0x40-byte rows from
#: +0x84 (0x61181ABC). Row: name1 +0x00, name2 +0x11, nation +0x22 (must equal
#: the VIEWER's nation or "[NO PLAYER]", systext 10:48, is drawn).
S158_PLAYERS_N, S158_PLAYERS, S158_ROW_LEN = 0x80, 0x84, 0x40
S158_ROW_NATION = 0x22


#: {host: [(account, monotonic)]} -- a 0x0156 CREATE / 0x0157 JOIN is followed
#: seconds later by a NEW group channel from that host; it claims the oldest.
_group_entries = {}


def queue_group_entry(host, account):
    if not account:
        return
    now = time.monotonic()
    with worldchannel._world_entries_lock:
        q = [e for e in _group_entries.get(host, [])
             if now - e[1] <= worldchannel.WORLD_ENTRY_TTL and e[0] != account]
        q.append((account, now))
        _group_entries[host] = q


def claim_group_entry(host):
    now = time.monotonic()
    with worldchannel._world_entries_lock:
        q = [e for e in _group_entries.get(host, [])
             if now - e[1] <= worldchannel.WORLD_ENTRY_TTL]
        got = q.pop(0)[0] if q else None
        _group_entries[host] = q
    return got


#: {GroupID: {"map", "sector", "row", "at", "by"}} -- set when a member sorties
#: with the group flag; the other members' 0x0163 carries it (see reply_0163).
GROUP_SORTIE = {}
#: {account: (ready, cont)} from 0x0173 CHANGE SORTIE SETTING (+0x04 1 = Ready,
#: 2 = Standing By; +0x07 1 = Continue, 2 = Do not continue; 0 = unchanged).
GROUP_READY = {}
G191_BLOB_FROM = 0x4C       # cmd 191 copies body+0x08.. to blob+0x4C..
G_FLAG_LISTED, G_FLAG_READY, G_FLAG_CONT = 0x01, 0x02, 0x100
#: 0x0163 (the board's GROUP INFO reply, requested by 0x0162 on a row click):
S163_ON_SORTIE, S163_SECTOR, S163_MAPROW = 0x00, 0x10, 0x14
REPLY_0163_LEN = 0x668


def reply_0163(gid):
    """The 0x0163 body for group `gid`: its members at +0x80/+0x84 (the
    board's Player List, for creator, joiner and a pre-join viewer alike) and,
    if it has sortied, the on-sortie flag + sector + map row, which makes the
    client offer 9:10 and sortie to that map on YES."""
    b = bytearray(REPLY_0163_LEN)
    n, rows = group_player_rows(gid)
    struct.pack_into("<I", b, S158_PLAYERS_N, n)
    b[S158_PLAYERS:S158_PLAYERS + len(rows)] = rows
    so = GROUP_SORTIE.get(gid)
    if so and time.time() - so["at"] < max(missionblock.MISSION_TIME, 600):
        b[S163_ON_SORTIE] = 1
        struct.pack_into("<I", b, S163_SECTOR, so["sector"])
        row = so["row"][:0x6C]
        b[S163_MAPROW:S163_MAPROW + len(row)] = row
    return bytes(b)


def group_member_flags(account, gid):
    """+0x54 of `account`'s member blob: listed, plus ready / continue."""
    flags = G_FLAG_LISTED
    ready, cont = GROUP_READY.get(account, (0, 0))
    if ready == 1 or battlegroups.group_leader(gid) == account:
        flags |= G_FLAG_READY
    if cont == 1:
        flags |= G_FLAG_CONT
    return flags


def group_blob_update(uid, leader, flags, voice=True, listed=True):
    """A cmd 191 for member `uid`: the blob from +0x4C onward, full length (the
    handler copies body+0x08.. to blob+0x4C.., so a short body would copy
    whatever follows it). +0x54 bit 0 must stay set or the member is dropped;
    listed=False clears it on purpose, to drop a kicked member (group_delist)."""
    tail = bytearray(0x1C8 - G191_BLOB_FROM)
    struct.pack_into("<I", tail, GROUP_POP_LEADER_OFF - G191_BLOB_FROM, 1 if leader else 0)
    struct.pack_into("<I", tail, GROUP_POP_FLAGS_OFF - G191_BLOB_FROM,
                     (flags | G_FLAG_LISTED) if listed else (flags & ~G_FLAG_LISTED))
    tail[G190_VOICE - G191_BLOB_FROM] = 1 if voice else 0
    body = struct.pack("<I", uid & 0xFFFFFFFF) + bytes(4) + bytes(tail)
    return fmoworld.record(roomrelay.GROUP_BLOB_UPDATE_CMD, body)


def _group_chans():
    return [(k, c) for k, c in list(WORLD_PEERS.items())
            if isinstance(k, tuple) and len(k) == 3 and k[2] == "group"]


def group_member_by_alias(account, alias):
    """The account of the member `account`'s client knows as UnitID `alias`
    on its group connection, or None. That id is what the Group Commands
    member list hands to Kick / Change Leader: the list row's peer+0x10
    (0x6116F6F3..0x6116F6F9, posted with event 0x1003 / 0x1004), and every
    other member's peer there is an alias we minted in group_queue. The
    client's own id is refused by the client itself (event 0x13EA)."""
    for _k, c in _group_chans():
        if getattr(c, "account", None) != account:
            continue
        for okey, a in list(getattr(c, "alias_of", {}).items()):
            if a == alias:
                other = WORLD_PEERS.get(okey)
                if other is not None and getattr(other, "account", None):
                    return other.account
    return None


def group_delist(gid, account):
    """Take `account` off every other member's Group Commands list: a cmd
    191 for its alias WITHOUT +0x54 bit 0 (G190_STATUS_BIT0 -- a 191 drops a
    member without it), then forget the alias was introduced, so a later
    rejoin is introduced again by group_queue. Returns the channels told."""
    mine = [c for _k, c in _group_chans() if getattr(c, "account", None) == account]
    n = 0
    for _k, c in _group_chans():
        if getattr(c, "account", None) in (None, account):
            continue
        if getattr(c, "account", None) not in GROUP_MEMBERS.get(gid, []):
            continue
        for m in mine:
            alias = c.alias_of.get(getattr(m, "peer_key", m.addr))
            if not alias:
                continue
            c.pending.append(group_blob_update(alias, False, 0, listed=False))
            rs = c.remotes.get(alias)
            if rs is not None:
                rs.popped = False
            n += 1
    return n


def group_push_flags(account):
    """Queue a cmd 191 for `account` on every group channel of its group: its
    own (self id) and each other member's (the alias they know it by)."""
    gid = GROUP_OF.get(account)
    if not gid:
        return 0
    leader = battlegroups.group_leader(gid) == account
    flags = group_member_flags(account, gid)
    n = 0
    gchans = [c for k, c in list(WORLD_PEERS.items())
              if isinstance(k, tuple) and len(k) == 3 and k[2] == "group"
              and getattr(c, "account", None) in GROUP_MEMBERS.get(gid, [])]
    mine = next((c for c in gchans if c.account == account), None)
    for c in gchans:
        if c.account == account:
            uid = getattr(c, "group_uid", None)
        elif mine is not None:
            uid = c.alias_of.get(getattr(mine, "peer_key", mine.addr))
        else:
            uid = None
        if not uid:
            continue
        c.pending.append(group_blob_update(uid, leader, flags))
        n += 1
    return n


#: KEY: SE'S JOIN RULES (AH/F98/D92 218-220): "While you belong to a platoon
#: you can neither form a new battle group nor join another one. Leave your
#: battle group first." / "Nor can you join a platoon that has too many
#: members." Until 2026-09-30 group_join MOVED the member silently and had no
#: cap. The refusal codes come from the client's code -> message table at
#: 0x613955F0 (see battlegroups.CODE_BONUS_FUNDS); the JOIN failure arm
#: (event 0x10D0 -> 0x61181A4A) draws 9:3 "Failed to join the battle group."
#: with the code's own line under it:
JOIN_CODE_IN_GROUP = -14116      # 9:0 "You already belong to a battle group."
JOIN_CODE_FULL = -30018          # 5:33 "...the battle group is at its member limit."
JOIN_CODE_COST = -30023          # 5:37 "Your B.G.Cost is too low to join..."
#: FMO_GROUP_RULES=1 (default): refuse those joins; 0 = the old silent move.
GROUP_RULES = _env_int("FMO_GROUP_RULES", "1") != 0
#: FMO_GROUP_CAP: the member limit. SE never printed it; news/frontline/
#: mission.html:23 calls a battle-map mission "a platoon mission of about ten",
#: and 3:3 caps a sector sortie at 10 -- so 10, OURS to tune. 0 = no cap.
GROUP_CAP = _env_int("FMO_GROUP_CAP", "10")
#: WARNING: A MEMBERSHIP IS ONLY REAL WHILE THE CLIENT HOLDS IT. Our 0x0155
#: serves no LoginGroup entries, so a relogged client belongs to NO group while
#: GROUP_OF (in-process) still names the old one. Refusing on that stale row
#: would lock a pilot out of every group until a restart, with no menu to leave
#: from. So a membership counts only while that account's group channel was
#: heard in the last GROUP_LIVE_S seconds, or the join is younger than that
#: (the channel comes up a few seconds after the 0x0158). Ours.
GROUP_LIVE_S = 300
#: {account: monotonic of its last create/join}, for the grace above.
_joined_at = {}


def group_member_live(account, now=None):
    """True while `account`'s group membership is still held by its client."""
    now = time.monotonic() if now is None else now
    if now - _joined_at.get(account, -1e9) <= GROUP_LIVE_S:
        return True
    wall = time.time()
    return any(isinstance(k, tuple) and len(k) == 3 and k[2] == "group"
               and getattr(c, "account", None) == account
               and wall - (getattr(c, "seen_at", 0) or 0) <= GROUP_LIVE_S
               for k, c in list(WORLD_PEERS.items()))


#: KEY: A BATTLE GROUP OUTLIVES ITS MEMBERS' LOGOUTS (manual p.42: logging out
#: does not take you out of your battle group). Our 0x0155 serves no
#: LoginGroup entries, so a relogged client belongs to no group while
#: GROUP_MEMBERS still lists it. FMO_GROUP_REATTACH=1 (default): on the
#: session's first keepalive, a pilot whose group still exists on this
#: server is attached to it again with the same 0x0174 push a JOIN uses.
#: Groups live in this process only, so a server restart still ends them.
GROUP_REATTACH = _env_int("FMO_GROUP_REATTACH", "1") != 0


def group_reattach(sess, conn_id):
    """The 0x0174 that puts `sess`'s pilot back in its battle group after a
    relog, once per session, or None. The group must still exist (not
    disbanded) and still list the account."""
    if not GROUP_REATTACH or getattr(sess, "group_reattach_done", False):
        return None
    sess.group_reattach_done = True
    try:
        acct = sess.account
    except Exception:
        return None
    gid = GROUP_OF.get(acct) if acct else None
    if (not gid or acct not in GROUP_MEMBERS.get(gid, [])
            or battlegroups.group_state(gid) is None):
        return None
    if not grouplogin.GROUP_ATTACH:
        return None
    # A create/join in the last minute is this very session's: its client is
    # attached already, and a second attach would tear the connection down.
    if time.monotonic() - _joined_at.get(acct, -1e9) < 60:
        return None
    _joined_at[acct] = time.monotonic()        # live again while it reconnects
    queue_group_entry(sess.ip, acct)           # its new group channel claims it
    try:
        pkt = grouplogin.group_attach_packet(
            conn_id, gid, host=addressing.host_for(addressing.GROUP_HOST, sess.ip))
    except ValueError as e:
        log(f"{sess.peer}   GROUP RE-ATTACH to {gid} not sent: {e}")
        return None
    log(f"{sess.peer}   -> 0x{grouplogin.MSG_GROUP_ATTACH:04X} GROUP RE-ATTACH: "
        f"{acct} is still a member of group {gid} (relogged), attached again")
    return pkt


def group_chat_listeners(chan):
    """The OTHER members' live group channels for a /bg line sent on group
    channel `chan`: by ACCOUNT, through GROUP_OF / GROUP_MEMBERS, never by
    address or room. Empty when `chan` is bound to no account or its account
    is in no group (the sender still gets its own echo)."""
    acct = getattr(chan, "account", None)
    gid = GROUP_OF.get(acct) if acct else None
    if not gid or acct not in GROUP_MEMBERS.get(gid, []):
        return []
    others = set(GROUP_MEMBERS.get(gid, [])) - {acct}
    wall = time.time()
    return [c for k, c in list(WORLD_PEERS.items())
            if isinstance(k, tuple) and len(k) == 3 and k[2] == "group"
            and c is not chan and getattr(c, "account", None) in others
            and wall - (getattr(c, "seen_at", 0) or 0) <= GROUP_LIVE_S]


#: group_join_refusal's default: look the value up (battlegroups.account_bg_cost
#: for the joiner, the group's stored create form for Required B.G.Cost).
AUTO = object()


def group_join_refusal(gid, account, cost=AUTO, required=AUTO):
    """None when `account` may join group `gid`, else (code, why). `cost` /
    `required` are the joiner's B.G.Cost and the group's Required B.G.Cost;
    left out they are looked up, and None = unknown, which skips that rule
    (see battlegroups.pilot_bg_cost)."""
    if not GROUP_RULES or not gid or not account:
        return None
    if required is AUTO:
        required = int((battlegroups.group_state(gid) or {}).get("required") or 0)
    if cost is AUTO:
        # The leader's own form capped Required at its own cost (D92 227),
        # and a member already in is not joining: neither is judged again.
        own = (battlegroups.GROUP_CREATOR_ACCOUNT.get(gid) == account
               or account in GROUP_MEMBERS.get(gid, []))
        cost = battlegroups.account_bg_cost(account) if required and not own else None
    old = GROUP_OF.get(account)
    if (old and old != gid and account in GROUP_MEMBERS.get(old, [])
            and group_member_live(account)):
        return JOIN_CODE_IN_GROUP, (f"{account} already belongs to group {old} "
                                    f"(AH/F98/D92 218: leave it first)")
    mem = GROUP_MEMBERS.get(gid, [])
    if account not in mem and GROUP_CAP > 0:
        live = [a for a in mem if group_member_live(a)]
        if len(live) >= GROUP_CAP:
            return JOIN_CODE_FULL, (f"group {gid} already has {len(live)} "
                                    f"member(s), the limit is {GROUP_CAP} "
                                    f"(FMO_GROUP_CAP; AH/F98/D92 220)")
    if cost is not None and required and cost < required:
        return JOIN_CODE_COST, (f"B.G.Cost {cost} is below the group's Required "
                                f"B.G.Cost {required} (AH/F98/D92 223-225)")
    return None


def group_create_refusal(account):
    """None when `account` may create a group, else (code, why): D92 218, a
    member of a live group cannot form another."""
    if not GROUP_RULES or not account:
        return None
    old = GROUP_OF.get(account)
    if old and account in GROUP_MEMBERS.get(old, []) and group_member_live(account):
        return JOIN_CODE_IN_GROUP, (f"{account} already belongs to group {old} "
                                    f"(AH/F98/D92 218)")
    return None


def group_join(gid, account):
    """Record `account` as a member of battle group `gid` (and of no other).
    False (nothing changed) when group_join_refusal refuses; a STALE
    membership elsewhere (see GROUP_LIVE_S) is dropped, as before."""
    if not gid or not account:
        return False
    no = group_join_refusal(gid, account)
    if no is not None:
        log(f"   GROUP JOIN REFUSED: {account} -> group {gid}: {no[1]} (code {no[0]})")
        return False
    old = GROUP_OF.get(account)
    if old and old != gid and account in GROUP_MEMBERS.get(old, []):
        GROUP_MEMBERS[old].remove(account)
    mem = GROUP_MEMBERS.setdefault(gid, [])
    if account not in mem:
        mem.append(account)
    GROUP_OF[account] = gid
    _joined_at[account] = time.monotonic()
    return True


def group_leave(account):
    """0x0172 LEAVE / a kick: `account` belongs to no group any more.
    Returns the group it left, or None."""
    gid = GROUP_OF.pop(account, None)
    if gid is not None and account in GROUP_MEMBERS.get(gid, []):
        GROUP_MEMBERS[gid].remove(account)
    GROUP_READY.pop(account, None)
    _joined_at.pop(account, None)
    return gid


def group_member_row(account):
    """One 0x40-byte Player List row for `account`'s pilot, or None."""
    roster = charstore.load_roster(account) if account else []
    c = next((c for c in roster if c.get("first")), None)
    if c is None:
        return None
    row = bytearray(S158_ROW_LEN)
    for off, txt in ((0x00, c.get("first", "")), (0x11, c.get("last", ""))):
        s = (txt or "").encode("cp932", "replace")[:GROUP_NAME_LEN - 1]
        row[off:off + len(s)] = s
    row[S158_ROW_NATION] = (popnation.character_nation(c)[0] or 0) & 0xFF
    return bytes(row)


def group_player_rows(gid, limit=20):
    """(count, rows bytes) for the 0x0158 Player List of group `gid`."""
    rows = [r for r in (group_member_row(a) for a in GROUP_MEMBERS.get(gid, []))
            if r][:limit]
    return len(rows), b"".join(rows)


def group_remote_member_record(uid, first, last, leader, key, tag, host, port,
                               voice=True, flags=None):
    """A cmd 190 that makes the receiving client CREATE a group peer for
    another member (0x611E56B0 on an unknown body+0x00): the peer's key, tag
    and address are ours, like the world peer link; +0x54 bit 0 keeps it
    listed; +0x58 bit 0 lets that client SEND it voice (cmd 123)."""
    b = bytearray(0x1C8)
    struct.pack_into("<I", b, 0x00, uid & 0xFFFFFFFF)
    struct.pack_into("<I", b, G190_MODE, 0)       # 0 -> mode 0 (others crash)
    struct.pack_into("<I", b, G190_GATE, 0)       # create
    k = (key or b"")[:0x0F]
    b[G190_KEY:G190_KEY + len(k)] = k
    for off, txt in ((GROUP_POP_NAME1_OFF, first), (GROUP_POP_NAME2_OFF, last)):
        s = (txt or "").encode("cp932", "replace")[:GROUP_NAME_LEN - 1]
        b[off:off + len(s)] = s
    b[G190_TAG] = tag & 0xFF
    b[G190_PORT:G190_PORT + 2] = int(port).to_bytes(2, "big")
    b[G190_IP:G190_IP + 4] = socket.inet_aton(host)
    struct.pack_into("<I", b, GROUP_POP_LEADER_OFF, 1 if leader else 0)
    struct.pack_into("<I", b, GROUP_POP_FLAGS_OFF,
                     (flags or 0) | G190_STATUS_BIT0)
    b[G190_VOICE] = 1 if voice else 0
    return fmoworld.record(GROUP_POP_CMD, bytes(b))


def group_queue(chan):
    """Introduce every OTHER member of this client's battle group on its group
    self stream (cmd 190 only works there), once per member, as an alias with
    a peer link -- so the member list is real and voice has somewhere to go."""
    if not (room.PEER_LINK and chan.key == GROUP_KEY and chan.tables
            and getattr(chan, "group_popped", False)):
        return 0
    if getattr(chan, "mission_type", None):
        return 0                # a mission-group connection (missiongroups)
    gid = GROUP_OF.get(getattr(chan, "account", None))
    if not gid:
        return 0
    n = 0
    for acct in GROUP_MEMBERS.get(gid, []):
        if acct == chan.account:
            continue
        other = next((c for k, c in list(WORLD_PEERS.items())
                      if isinstance(k, tuple) and len(k) == 3 and k[2] == "group"
                      and getattr(c, "account", None) == acct), None)
        if other is None:
            continue
        okey = getattr(other, "peer_key", other.addr)
        alias = chan.alias_for(okey)
        rs = chan.remotes[alias]
        if rs.popped:
            continue
        row = group_member_row(acct)
        first = row[0x00:0x11].split(b"\0")[0].decode("cp932", "replace") if row else ""
        last = row[0x11:0x22].split(b"\0")[0].decode("cp932", "replace") if row else ""
        host = addressing.host_for(addressing.GROUP_HOST, chan.addr[0])
        try:
            host = socket.gethostbyname(host)
        except OSError:
            continue
        leader = battlegroups.group_leader(gid) == acct
        chan.pending.append(group_remote_member_record(
            alias, first, last, leader, GROUP_KEY, rs.tag, host, addressing.GROUP_PORT,
            flags=group_member_flags(acct, gid)))
        rs.popped = True
        n += 1
        log(f"[udp {chan.addr[0]}:{chan.addr[1]}] VERIFIED: GROUP: member {acct} "
            f"({first} {last}{', leader' if leader else ''}) introduced as "
            f"{alias:#x} (tag {rs.tag:#x}) by cmd 190 on the group self stream -- "
            f"its peer link carries voice (cmd 123)")
    return n


def group_leader_for(host_ip):
    """(leader byte, why) for the member blob this host should receive."""
    if GROUP_LEADER_MODE != "auto":
        return GROUP_POP_LEADER, f"FMO_UDP_GROUP_LEADER={GROUP_LEADER_MODE!r}"
    # By ACCOUNT when the creator's is known (account_for answers for the
    # channel being served -- a group channel shares its lobby socket); by host
    # only for a group recorded without one.
    me = identity.account_for(host_ip)
    made = [g for g in battlegroups.BATTLE_GROUPS_MADE
            if (battlegroups.GROUP_CREATOR_ACCOUNT.get(g[1]) == me
                if battlegroups.GROUP_CREATOR_ACCOUNT.get(g[1])
                else str(g[0]).split(":")[0] == host_ip)]
    if made:
        return 1, (f"auto: this host created group #{made[-1][1]}, so it is "
                   f"the LEADER -- Change Leader / Kick / Disband / Edit "
                   f"Comment un-grey, and Leave stays greyed BY DESIGN")
    return 0, ("auto: this host created no group, so it is a MEMBER -- "
               "Sortie Setting and LEAVE un-grey instead")
GROUP_POP_FLAGS = int(os.environ.get("FMO_UDP_GROUP_FLAGS", "0").strip() or "0", 0)
#: FMO_UDP_GROUP_CMD: the member-info record command (0xBE = the POP/set-blob path)
GROUP_POP_CMD = int(os.environ.get("FMO_UDP_GROUP_CMD", "0xBE").strip() or "0xBE", 0)
#: the leader / status offsets inside the copied blob (blob+N == body+N)
GROUP_POP_LEADER_OFF = int(os.environ.get("FMO_UDP_GROUP_LEADER_OFF", "0x50").strip() or "0x50", 0)
#: The member's two NAME fields in the same blob. Not a new decode: both were
#: already written down as the source chat arm 3 reads a battle sender's name
#: from (see chat_echo_name), and both were being served as zeros -- which is
#: why Member Details listed the pilot as "-" (live, 2026-09-09).
#: 0x11 apart, the same 17-byte name field the trade records use.
GROUP_POP_NAME1_OFF = _env_int("FMO_UDP_GROUP_NAME1_OFF", "0x1C")
GROUP_POP_NAME2_OFF = _env_int("FMO_UDP_GROUP_NAME2_OFF", "0x2D")
GROUP_NAME_LEN = 0x11
GROUP_POP_FLAGS_OFF = int(os.environ.get("FMO_UDP_GROUP_FLAGS_OFF", "0x54").strip() or "0x54", 0)
#: FMO_UDP_GROUP_KEY: force the member POP's body+0x00 / UnitID (0 = character id)
GROUP_POP_KEY = int(os.environ.get("FMO_UDP_GROUP_KEY", "0").strip() or "0", 0)


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    addressing, battlegroups, charstore, grouplogin, identity, missionblock, popnation, room,
    roomrelay, worldchannel,
)
