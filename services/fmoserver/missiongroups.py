"""Mission groups (Playing Manual p.61): the issuer of a derived mission leads one, its takers are
members; the two group connections, /mgl and /mgm."""
import time
from .knobs import _env_int
from .wirelog import log


#: KEY: WHAT A MISSION GROUP IS ON THE WIRE (static 2026-10-01). Not a new
#: message: the same group-server connection as a battle group, in another
#: slot. 0x61177620(GroupID, type, endpoint, info88) switches on `type`
#: (jump table 0x611778B4) for the slot it tears down and rebuilds:
#:   0, 1, default  [0x613CA3F8]  the battle group
#:   2              [0x613CA3FC]  the mission group you are a MEMBER of
#:   3              [0x613CA400]  the mission group you LEAD
#: Every slot gets the same class (ctor 0x611E4E00, vtable 0x613446B0) and the
#: same LoginStart key, the literal "group" (0x61177803). What differs is the
#: manager id the connection registers and stamps on its own datagrams as
#: `kind`: type 1 passes 1 (0x61177707), type 2 passes 6 (0x61177778), any
#: other type 5 (0x611777C9). The battle group's pairing of that id with the
#: reply hid was proved live (udpconfig.scene_reply_hid: creator 1, joiner 5).
#: The client's receive loop 0x611FEC90 routes a reply by its +0x09 alone
#: (0x61072650), on ONE socket, so all of these share the source port the
#: lobby world uses.
#:
#: Only three callers build a group connection: the 0x0155 LoginGroup list
#: (0x6117B586), the 0x0174 push (0x6117EEFE) and the battle group CREATE
#: (0x611823DF, always type 1). Nothing in the mission accept or order paths
#: does, so SE's server must have attached both sides with 0x0174, type 3 to
#: the issuer and type 2 to each taker. That is what this module does.
#:
#: The chat commands (table at 0x611E1623): /mgl = "missiongroupleader",
#: handler 0x611DEDD0, refuses with 0xC0230040 while [0x613CA400] is empty and
#: otherwise sends channel 7; /mgm = "missiongroupmember", 0x611DEE70, refuses
#: with 0xC023003F while [0x613CA3FC] is empty and sends channel 8. 0x611D5830
#: then sends the cmd-115 line on that connection ONLY when the connection's
#: self peer carries a member blob: 0x611D4A80 / 0x611D4AA0 return
#: [peer+0x1AF9]+0x1C / +0x2D (the two names) and a NULL blob makes the send
#: return -1 (0x611D5AB1 / 0x611D5B3B). So the self cmd 0xBE blob the battle
#: group already gets (datagram.py, GROUP_POP) is what makes /mgl and /mgm
#: work at all. The receiving connection's cmd 18 arm 0x611E4A20 is shared by
#: every slot: channel 1 is a HUD banner, 3 and 6 have their own formats, and
#: any other channel, 7 and 8 included, is drawn as "%s: %s" on that channel
#: (0x611E4AB1). So a line is echoed on the channel the sender chose.
#:
#: The Tab key's group display (manual p.61) is the client's: 0x61006BD1 and
#: 0x61006C09 read [slot]->[+0x48]+0x10E0 in 3..4 for each slot, so a slot
#: shows once its connection is up. Nothing on the server selects it.
#:
#: WARNING: WHAT IS NOT PROVED. (1) No client has ever been sent a type 2 or 3
#: attach; the layout is the type-0 one, proved live, with +0x18 changed. (2)
#: Manager id 5 is shared by a battle-group JOINER (type 0) and a mission
#: group LEADER (type 3), and the client keeps one handler per id, so a pilot
#: cannot hold both: the leader attach is withheld while the pilot is in a
#: battle group it did not create (see mission_attach_due). (3) The other
#: members are not introduced on a mission connection (no cmd 190): the peer
#: link's tags are minted per channel and the client routes them by +0x09
#: across every connection on its socket, so a second set could collide with
#: the battle group's. Chat does not need them; the member list may.
#: (4) 0x0178, which closes a group window with 8:68..8:71, is BATTLE-gated
#: (pushes.py), so a mission group that ends while its pilots are in the lobby
#: stays attached until they relog. A live test settles all four.
#:
#: FMO_MISSION_GROUP: 0 (default) = no mission group is ever attached and
#: nothing here runs; 1 = attach them as above.
MISSION_GROUP = _env_int("FMO_MISSION_GROUP", "0") != 0
MG_TYPE_MEMBER, MG_TYPE_LEADER = 2, 3
#: the manager id (datagram `kind`) each type registers, from 0x61177620
MG_KIND = {MG_TYPE_MEMBER: 6, MG_TYPE_LEADER: 5}
MG_TYPE_OF_KIND = {k: t for t, k in MG_KIND.items()}
#: the chat channel each slot's command sends (0x611DEE4F / 0x611DEEEF)
MG_CHAT = {MG_TYPE_LEADER: 7, MG_TYPE_MEMBER: 8}
MG_ROLE = {MG_TYPE_LEADER: "leader", MG_TYPE_MEMBER: "member"}
#: The GroupIDs we mint. Ours: non-zero (0x6117EEE5 drops a zero id) and
#: apart from the battle groups' small counter. [slot+0xE0] keeps it, and a
#: later 0x0178 is matched against it.
MG_ID_BASE = 0x4D470000
#: How often a session's keepalive re-derives its groups, in seconds. Ours.
MG_POLL_S = 10

#: {issuer account: GroupID}, minted on first use, in this process only.
MG_IDS = {}
#: {GroupID: (leader account, [member accounts])} as last derived.
MG_GROUPS = {}
#: {(host, type): [(account, GroupID, monotonic)]}: an attach waiting for the
#: connection it builds; the first datagram of that kind from the host claims it.
_entries = {}


def mission_gid(issuer):
    gid = MG_IDS.get(issuer)
    if gid is None:
        gid = MG_IDS[issuer] = MG_ID_BASE + len(MG_IDS) + 1
    return gid


def _source_alive(acct, e, now=None, rosters=None, cache=None):
    """False when the issuer's own accept that order `e` was derived from
    (its from_key / from_at) has ended; True while it is active or when it
    cannot be found (an order stored before from_key, or no roster)."""
    key = e.get("from_key")
    if key is None:
        return True
    cache = {} if cache is None else cache
    ck = (acct, key, e.get("from_at"))
    if ck in cache:
        return cache[ck]
    if rosters is not None:
        pool = [r for a, r in rosters if a == acct]
    else:
        live = missionbook._live_roster(acct)
        pool = [live if live is not None else charstore.load_roster(acct)]
    got = True
    for roster in pool:
        for c in roster or ():
            for m in missionbook.accepted_missions(c):
                if m.get("derived") or missionbook.mission_key(m) != key:
                    continue
                if e.get("from_at") is not None and m.get("at") != e.get("from_at"):
                    continue
                got = missionbook.mission_status(m, now) in missionboard.MISSION_ACTIVE
    cache[ck] = got
    return got


def mission_groups(now=None, rosters=None):
    """{GroupID: (leader, [members])} from the ORDERS: the issuer of an order
    still ordered (open, unexpired) leads a group, and so does the issuer of
    a TAKEN order while its taker's accept is still active (open / met); that
    taker is a member. One group per issuer, however many orders.
    `rosters` is for a test.

    KEY: A GROUP ENDS WITH ITS MISSION (SE 28:0 / 28:1, the report dialog:
    "If the mission ends, the mission group is disbanded as well"). Until
    2026-10-07 a taken order kept its issuer a leader forever (an order's
    status stays "taken" after the taker reports), and an order outlived the
    issuer's own sector / area accept it was derived from. Now a taken order
    whose taker reported, failed or expired counts for nobody, and every
    order dies with its source accept."""
    out = {}
    src = {}
    for did, (acct, e) in list(missionbook.order_registry(rosters).items()):
        st = missionbook.order_status(e, now, rosters)
        if st not in ("ordered", "taken") or not acct:
            continue
        if not _source_alive(acct, e, now, rosters, src):
            continue
        taker = None
        if st == "taken":
            taker = missionbook.order_taker(did, rosters)
            a = missionbook._order_taker_accept(did, rosters) if taker else None
            if a is None or missionbook.mission_status(a, now) not in missionboard.MISSION_ACTIVE:
                continue
        gid = mission_gid(acct)
        members = out.setdefault(gid, (acct, []))[1]
        if taker and taker != acct and taker not in members:
            members.append(taker)
    return out


def refresh(now=None, rosters=None):
    got = mission_groups(now, rosters)
    MG_GROUPS.clear()
    MG_GROUPS.update(got)
    return got


def wanted(account, groups):
    """{type: GroupID} the client of `account` should hold."""
    out = {}
    for gid, (leader, members) in groups.items():
        if leader == account:
            out[MG_TYPE_LEADER] = gid
        elif account in members and MG_TYPE_MEMBER not in out:
            out[MG_TYPE_MEMBER] = gid
    return out


def _bg_joiner(account):
    """True while `account` holds a battle group it did not create: its
    connection registered manager id 5, the id a leader attach would take."""
    gid = groupchannel.GROUP_OF.get(account)
    return bool(gid and account in groupchannel.GROUP_MEMBERS.get(gid, [])
                and battlegroups.GROUP_CREATOR_ACCOUNT.get(gid) != account
                and groupchannel.group_member_live(account))


def queue_entry(host, gtype, account, gid):
    now = time.monotonic()
    q = [e for e in _entries.get((host, gtype), [])
         if now - e[2] <= worldchannel.WORLD_ENTRY_TTL and e[0] != account]
    q.append((account, gid, now))
    _entries[(host, gtype)] = q


def claim_entry(host, gtype):
    now = time.monotonic()
    q = [e for e in _entries.get((host, gtype), [])
         if now - e[2] <= worldchannel.WORLD_ENTRY_TTL]
    got = q.pop(0) if q else None
    _entries[(host, gtype)] = q
    return got


def mission_attach_due(sess, conn_id, now=None, rosters=None, force=False):
    """The 0x0174 pushes that attach `sess`'s pilot to the mission groups it
    now leads or belongs to, on its keepalive, at most every MG_POLL_S. A
    group already attached is not attached again (that would tear its
    connection down); one that ended is forgotten, so a new one is attached."""
    if not MISSION_GROUP:
        return []
    mono = time.monotonic()
    if not force and mono - getattr(sess, "mg_polled", -1e9) < MG_POLL_S:
        return []
    sess.mg_polled = mono
    try:
        acct = sess.account
    except Exception:
        return []
    if not acct:
        return []
    want = wanted(acct, refresh(now, rosters))
    have = getattr(sess, "mg_attached", None)
    if have is None:
        have = sess.mg_attached = {}
    for t in [t for t in have if have[t] != want.get(t)]:
        log(f"{sess.peer}   MISSION GROUP {have[t]} ({MG_ROLE[t]}) has ended for "
            f"{acct}; the client keeps that connection until it relogs "
            f"(0x0178 is BATTLE-gated, so the lobby cannot close it)")
        del have[t]
    outs = []
    for t, gid in sorted(want.items()):
        if have.get(t) == gid:
            continue
        if t == MG_TYPE_LEADER and _bg_joiner(acct):
            if not getattr(sess, "mg_joiner_said", False):
                sess.mg_joiner_said = True
                log(f"{sess.peer}   MISSION GROUP {gid}: {acct} leads it but is "
                    f"in a battle group it did not create; both connections "
                    f"would register manager id 5 (0x61177620), so the leader "
                    f"attach waits until the pilot leaves that group")
            continue
        try:
            pkt = grouplogin.group_attach_packet(
                conn_id, gid, host=addressing.host_for(addressing.GROUP_HOST, sess.ip),
                gtype=t)
        except ValueError as e:
            log(f"{sess.peer}   MISSION GROUP {gid} attach not sent: {e}")
            continue
        have[t] = gid
        queue_entry(sess.ip, t, acct, gid)
        leader, members = MG_GROUPS.get(gid, (None, []))
        log(f"{sess.peer}   -> 0x{grouplogin.MSG_GROUP_ATTACH:04X} MISSION GROUP "
            f"ATTACH: GroupID {gid}, type {t} ({MG_ROLE[t]}; slot "
            f"{'0x613CA400' if t == MG_TYPE_LEADER else '0x613CA3FC'}), leader "
            f"{leader}, {len(members)} member(s). The client should log "
            f"'LoginGroup GroupID={gid} Type={t}' and its connection should send "
            f"kind {MG_KIND[t]}; /{'mgl' if t == MG_TYPE_LEADER else 'mgm'} works "
            f"once that connection has its member blob")
        outs.append(pkt)
    return outs


def mission_channel_for(peers, addr, got):
    """The mission-group channel a "group"-keyed datagram belongs to, or None
    (it is the battle group's). Decided by the datagram's kind: 6 is always a
    member connection; 5 is a leader connection only when this host was sent
    a leader attach and no live battle-group channel of its own speaks 5."""
    if not MISSION_GROUP or not got:
        return None
    if room.PEER_LINK and got.get("hid", 0) >= room.PEER_TAG_BASE:
        return None                     # an alias stream; ours have none
    t = MG_TYPE_OF_KIND.get(got.get("kind"))
    if t is None:
        return None
    key = (addr[0], addr[1], "mgroup%d" % t)
    chan = peers.get(key)
    if t == MG_TYPE_LEADER:
        bg = peers.get((addr[0], addr[1], "group"))
        if (bg is not None and getattr(bg, "group_kind", None) == MG_KIND[t]
                and time.time() - (getattr(bg, "seen_at", 0) or 0) <= groupchannel.GROUP_LIVE_S):
            return None
    entry = claim_entry(addr[0], t)
    if chan is None:
        if entry is None:
            return None
        chan = peers[key] = worldchannel.WorldChannel(addr)
        chan.key, chan.tables, chan.char_id = groupchannel.GROUP_KEY, groupchannel.GROUP_TABLES, 0
        chan.peer_key = key
        chan.mission_type = t
        log(f"[udp {addr[0]}:{addr[1]}] first MISSION GROUP datagram, kind "
            f"{got.get('kind')} = the {MG_ROLE[t]} connection of group {entry[1]} "
            f"({entry[0]}); replies go out on hid {MG_KIND[t]}")
    if entry is not None:
        # a new attach (another group, or the same one after a relog) built a
        # new connection, so its self peer needs the member blob again
        chan.account, chan.mission_gid = entry[0], entry[1]
        chan.group_popped = False
    return chan


def mission_chat_route(chan, kind):
    """(listeners, cmd-18 channel, scope) for a cmd-115 line on a mission
    channel, or None. /mgl (7) only on a leader connection, to every member;
    /mgm (8) only on a member connection, to the other members and the
    leader. By account, through the group as last derived."""
    t = getattr(chan, "mission_type", None)
    if t is None or kind != MG_CHAT.get(t):
        return None
    gid = getattr(chan, "mission_gid", None)
    leader, members = MG_GROUPS.get(gid, (None, []))
    acct = getattr(chan, "account", None)
    if t == MG_TYPE_LEADER:
        if acct != leader:
            return [], kind, "mission group (not its leader)"
        want = {(MG_TYPE_MEMBER, a) for a in members}
    else:
        if acct not in members:
            return [], kind, "mission group (not a member)"
        want = {(MG_TYPE_MEMBER, a) for a in members if a != acct}
        if leader:
            want.add((MG_TYPE_LEADER, leader))
    wall = time.time()
    out = []
    for k, c in list(groupchannel.WORLD_PEERS.items()):
        if c is chan or not (isinstance(k, tuple) and len(k) == 3
                             and str(k[2]).startswith("mgroup")):
            continue
        if getattr(c, "mission_gid", None) != gid:
            continue
        if (getattr(c, "mission_type", None), getattr(c, "account", None)) not in want:
            continue
        if wall - (getattr(c, "seen_at", 0) or 0) > groupchannel.GROUP_LIVE_S:
            continue
        out.append(c)
    return out, kind, "mission group"


def mission_leader_byte(chan):
    """(blob+0x50, why) for a mission channel's own member blob."""
    t = getattr(chan, "mission_type", None)
    return (1 if t == MG_TYPE_LEADER else 0), f"mission group {MG_ROLE.get(t, '?')}"


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    addressing, battlegroups, charstore, groupchannel, grouplogin, missionboard, missionbook,
    room, worldchannel,
)
