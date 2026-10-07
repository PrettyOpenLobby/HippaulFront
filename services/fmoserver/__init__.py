"""The Front Mission Online world server, one module per concern.

    deps.py           Imports shared by the package modules: the standard library and the
                      optional sibling services.
    defaults.py       The release defaults: every FMO_* knob's shipped value, applied before any
                      module reads the environment.
    knobs.py          Reading an environment knob, where an empty value counts as unset.
    wirelog.py        The listening port, the log directory, and the log and hexdump helpers
                      every module writes through.
    packet.py         The TCP packet: header layout, the RC4 session cipher and its key,
                      checksum, build and parse.
    handshake.py      The login handshake on 61300: version, credentials (0x0321), the 0x0322
                      redirect and session start.
    addressing.py     Which address and port to write into a reply for a given client, and the
                      20-byte endpoint struct.
    msgnames.py       MSG_NAMES: the message id to name table the log lines use.
    charlist.py       The character-select list (0x012E -> 0x012F): roster slots and the ids the
                      client is served.
    charstore.py      The character store: the per-account roster in fmo.db or the JSON file,
                      and the 0x013E creation record.
    identity.py       Who is playing from which address: login tokens, the POL member behind an
                      IP, the account key.
    charselect.py     The character-select screen's own requests and the nation select (0x019C
                      -> 0x019D).
    resume.py         The link-death resume reply (0x0137 -> 0x0138).
    status.py         The player status block (0x014A): every field the lobby reads at start,
                      and the knobs behind them.
    classes.py        The class table (0x014A +0x6AC): class levels and the experience curve.
    ranks.py          The rank ladder: rank names, contribution thresholds and the pay per rank.
    economy.py        A new pilot's starting money, MP and contribution, and the wallet as
                      stored.
    servicerecord.py  The service record (0x0175 -> 0x0176): paydays, salary, the ceasefire
                      bonus and the officer review.
    progress.py       Mission progress flags: what a pilot has completed and what the scripts
                      may offer next.
    zoneentry.py      Entering a zone (0x0150 -> 0x0153): MapNo, MapKind, the PS2 map sets, the
                      pilot's start position.
    move.py           The Move menu (0x016E -> 0x016F, 0x016D) and places, and the small in-
                      scene requests beside it: play time, member check, log out, hangar
                      password.
    hangar.py         The pilot's hangar: its place, its bays and the wanzers parked in them.
    areachange.py     Area change: which map a zone change grants.
    zonecontrol.py    The zone-control push (0x016C): which nation holds each zone.
    citytable.py      The City Control table push (0x019A).
    partsstock.py     The parts-stock push (0x016A): which parts the shop has in stock.
    inventory.py      Inventory and wanzer setups (0x0132 -> 0x0133, 0x0165 -> 0x0166): item
                      records and starter setups.
    shop.py           Buying, selling and granting items: the item counter (0x0169, 0x017E) and
                      the 0x016B mint push.
    permits.py        Area passes and permits: which zones a pilot may open, and at what cost.
    cosmetics.py      The cosmetics shop screen (0x01A2 -> 0x01A3).
    squadron.py       Squadrons: the squadron table (0x01AC -> 0x01AD), insignia and the
                      squadron pushes.
    lobapi.py         The lobby-API menu actions: the request objects and their marker replies.
    missionblock.py   The mission block (0x01C0 -> 0x01C1): map, battle area, time limit and
                      leader of a mission.
    missionlist.py    The mission list (0x018D -> 0x018E) and the report reply (0x018F).
    missionboard.py   Mission accept, report and cancel: message ids, refusal codes and the
                      knobs that gate them.
    missionbook.py    A pilot's accepted missions: keys, deadlines, status, battle results,
                      reports and pay.
    sectorwins.py     The sector-win ledger: wins per nation per war-map tile, kept on disk.
    areatargets.py    Area targets (0x01A9) and orders: the tiles a mission may be fought on.
    warmap.py         The war map (0x015E -> 0x015F, 0x0160 -> 0x0161): sector rows, the census
                      and the counter mission.
    warstate.py       The war state: per-sector control and the phase clock (fmowar), and its
                      216-byte sector records.
    sortie.py         The sortie (0x0139 -> 0x013A): the battle map a pilot enters and the
                      endpoint it dials.
    sortiepush.py     The auto-sortie push (0x014E) that battle-group members follow.
    battlemaps.py     The battle map list (0x0162, 0x01F4 -> 0x01F5).
    battlegroups.py   Battle groups on the lobby side: create, join and comment (0x0156, 0x0157
                      -> 0x0158).
    grouplogin.py     The group-server login entries (0x0154 -> 0x0155) and the 0x0174 push.
    withdraw.py       The battle withdraw (0x013D).
    scriptcall.py     The script's server call (0x0159): event rules from fmo-events.tsv and the
                      answered record.
    resultpush.py     The battle result push (0x015A): what the result pays.
    battleend.py      The battle end push (0x014C): end triggers, objectives, kills and battle
                      pay.
    lobbymessage.py   Text into the client's message window (0x014B) and the lobby announcement.
    timesync.py       Time requests and the time-sync push (0x013B, 0x0199).
    pushes.py         The lobby push queue and the smaller pushes: fees, item announce, group
                      ended, resupply.
    trade.py          Player trade: the trade messages, records and the trade service between
                      two pilots.
    coliseum.py       The Coliseum: the arena desks (list, register, cancel, host, the bracket and
                      streak board), official and player-hosted arenas, the waiting window.
    pilotrecord.py    Session's pilot record: money, class experience, flags, passes, salary,
                      the ceasefire bonus and the officer review.
    settlement.py     Session's battle settlement: kill bonuses, mission and war results, the
                      counter mission, the battle end and result pushes.
    session.py        Session: one client's TCP connection, its cipher and the on_packet
                      dispatcher for every lobby message.
    tcpserver.py      The TCP accept loop: one thread per client, the game and the community
                      server told apart.
    community.py      The community / mission server ("Fshira") on the same port: mission rows
                      and the Scramble Board.
    udpconfig.py      The UDP world channel's knobs: handler ids, key ids, datagram size and
                      logging.
    popself.py        The self POP: the record that makes a client draw its own pilot, and its
                      kind, name and model knobs.
    popnames.py       Names and sex on a POP: who is online and what each pilot's POP carries.
    poplook.py        A pilot's look on a POP: size, build, face and uniform from the creation
                      record.
    popnation.py      Nation and battle side on a POP, and which side an enemy stands on.
    popparts.py       The parts array on a battle POP: the wanzer a pilot fitted.
    popsweep.py       POP position, model and type, the probe sweeps and the per-map arrival
                      points.
    battlepop.py      The battle POP knobs: arena position, the gate pop, the dummy enemy and
                      the referee.
    npccast.py        The lobby NPC cast: FMO_UDP_POP_NPC parsing, face angles, HQ marks and the
                      U.S.N. counterparts.
    npcroster.py      Which cast a zone pops: layout bands, per-host rosters, NPC POP records
                      and NPC moves.
    room.py           The room knobs: lobby multiplayer, the peer link and the room timers.
    worldchannel.py   The UDP world channel: WorldChannel, its alias streams, binding a channel
                      to an account, serve_udp.
    groupchannel.py   Battle groups on the world channel: members, the Player List, group
                      sorties and member blobs.
    missiongroups.py  Mission groups (Playing Manual p.61): the 0x0174 attaches for a derived
                      mission's issuer and takers, their connections, /mgl and /mgm.
    rooms.py          Rooms: who stands in the same zone, arrivals and departures, battle rooms.
    squad.py          The enemy squad the client runs: squad owners, positions, the fire and hit
                      relays and kill credit.
    roomrelay.py      The room relay: introducing room-mates to each other and flushing their
                      records.
    peerlink.py       The peer link: the server standing in as each client's peer for another
                      pilot's unit.
    referee.py        The battle referee: battle state, shots against the dummy, objectives, end
                      triggers, kill credit.
    datagram.py       _serve_datagram: one inbound world-channel datagram, decoded and answered.
    devtool.py        The lobby NPC editor hook (FMO_DEVTOOL_PORT).
    gatetool.py       The story gates tool's live side: pilots, online sessions, queued edits.
    loot.py           Spoils: the battle group's loot window after a win (group cmds 214/215,
                      the Need/Want/Pass choices) and the items it grants (0x016B).
    penalty.py        Friendly-fire penalty points and retraining: the 0x017B report, the 0x017C
                      vote, the three script bytes (+0x418..+0x41A) and the retraining wins.
    solo.py           The solo area (SE's Festa 2006 rules): one NPC ally at a time, two
                      enemies at a time, ten kills win, three allies lost lose.
    defection.py      Defection (Change Nations): who may change nations, the refusal the
                      client shows, and what a defection costs the pilot.
    main.py           run(): the listeners, the startup log and the background threads.
    selftests.py      The offline selftest (`python fmo.py --selftest`): no socket, no client.

fmo.py (one directory up) is the entry point and the compatibility
facade over these modules.
"""
# Every module, in the order that lets each one read another's constants
# while it is imported. Importing the package is the check: an order that
# no longer holds fails there.
from . import (
    defaults, deps, knobs, wirelog, popself, popsweep, packet, handshake, addressing, charlist,
    charstore, identity, charselect, resume, status, classes, ranks, economy, servicerecord,
    progress, zoneentry, move, hangar, areachange, zonecontrol, citytable, partsstock,
    inventory, shop, permits, cosmetics, squadron, lobapi, missionblock, missionlist,
    missionboard, missionbook, sectorwins, areatargets, warmap, warstate, sortie, sortiepush,
    battlemaps, battlegroups, grouplogin, withdraw, scriptcall, resultpush, battleend,
    lobbymessage, timesync, pushes, trade, msgnames, pilotrecord, settlement, session,
    tcpserver, community, udpconfig, popnames, poplook, popnation, popparts, battlepop, npccast,
    npcroster, room, worldchannel, groupchannel, missiongroups, rooms, squad, roomrelay, peerlink,
    referee,
    datagram, devtool, gatetool, loot, penalty, solo, defection, main, selftests,
)

__all__ = [
    'deps', 'defaults', 'knobs', 'wirelog', 'packet', 'handshake', 'addressing', 'msgnames',
    'charlist', 'charstore', 'identity', 'charselect', 'resume', 'status', 'classes', 'ranks',
    'economy', 'servicerecord', 'progress', 'zoneentry', 'move', 'hangar', 'areachange',
    'zonecontrol', 'citytable', 'partsstock', 'inventory', 'shop', 'permits', 'cosmetics',
    'squadron', 'lobapi', 'missionblock', 'missionlist', 'missionboard', 'missionbook',
    'sectorwins', 'areatargets', 'warmap', 'warstate', 'sortie', 'sortiepush', 'battlemaps',
    'battlegroups', 'grouplogin', 'withdraw', 'scriptcall', 'resultpush', 'battleend',
    'lobbymessage', 'timesync', 'pushes', 'trade', 'pilotrecord', 'settlement', 'session',
    'tcpserver', 'community', 'udpconfig', 'popself', 'popnames', 'poplook', 'popnation',
    'popparts', 'popsweep', 'battlepop', 'npccast', 'npcroster', 'room', 'worldchannel',
    'groupchannel', 'missiongroups', 'rooms', 'squad', 'roomrelay', 'peerlink', 'referee', 'datagram', 'devtool',
    'gatetool', 'loot', 'penalty', 'solo', 'defection', 'main', 'selftests',
]
