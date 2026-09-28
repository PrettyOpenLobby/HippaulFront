# Contributing to CrystalFront

CrystalFront is the Front Mission Online server that plugs into OpenLobby,
the PlayOnline core. This page says where things are, how to run the checks,
and what a pull request needs.

## Where things are

```
services/
  fmo.py          entry point (`python fmo.py`, `python fmo.py --selftest`)
                  and a facade over the fmoserver package; see below
  fmoserver/      the world server, one module per concern
                  (fmoserver/__init__.py lists them)
  fmoworld.py     the UDP world channel's cipher, framing and delivery window
  fmomsn.py       the codec of the second (community) server
  fmowar.py       the war state: sector control and the phase clock
  fmosectors.py   the war-map sector table
  fmostore.py     the player database (fmo_character and fmo_squadron_insignia
                  in PostgreSQL, or JSON when FMO_DB is 0)
  fmodb.py        reaches the core's polcore; migrate, status and import
  fmo_migrations/ CrystalFront's migrations, numbered from 2001
  fmolayout.py    the lobby NPC layout file and the floor-plan geometry
  fmodevtool.py   the lobby NPC editor page, served by fedevtool.py
  fmotitle.py     the title plugin the core loads for the Viewer's profile
  boardfmo.py     the City Control board, hosted by polboards.py
tools/            self-tests (`*_test.py`, `fmo_run_all.py`), the data build
                  (`fmodata_build.py`) and the cipher tables
```

`fmoserver/` is split along the protocol: one module per lobby message
family, per piece of the world channel, and per shared mechanism. Reading
order for a first visit:

1. `main.py`: what `run()` starts, on which port, and the startup log.
2. `tcpserver.py` and `session.py`: one TCP client. `Session.on_packet` is
   the dispatcher for every lobby message id. `packet.py` is the frame and
   the cipher, `handshake.py` the login that comes first.
3. The lobby messages, by what the player is doing:
   - choosing a pilot: `charlist.py`, `charstore.py`, `charselect.py`,
     `identity.py`;
   - the status block the lobby starts from: `status.py`, `classes.py`,
     `ranks.py`, `economy.py`;
   - moving around: `zoneentry.py`, `move.py`, `areachange.py`, `hangar.py`;
   - items: `inventory.py`, `shop.py`, `permits.py`, `partsstock.py`,
     `cosmetics.py`;
   - missions: `missionboard.py`, `missionlist.py`, `missionblock.py`,
     `missionbook.py`;
   - the war: `warmap.py`, `warstate.py`, `sectorwins.py`, `areatargets.py`,
     `zonecontrol.py`, `citytable.py`;
   - squadrons and battle groups: `squadron.py`, `battlegroups.py`,
     `grouplogin.py`;
   - a sortie and its end: `sortie.py`, `sortiepush.py`, `battlemaps.py`,
     `withdraw.py`, `scriptcall.py`, `resultpush.py`, `battleend.py`,
     `settlement.py`, `pilotrecord.py`;
   - trade and the pushes: `trade.py`, `pushes.py`, `lobbymessage.py`,
     `timesync.py`.
4. The UDP world channel: `worldchannel.py` (one channel per client, bound
   to an account) and `datagram.py` (one inbound datagram). Then the POP
   records that make a client draw a unit (`popself.py`, `popnames.py`,
   `poplook.py`, `popnation.py`, `popparts.py`, `popsweep.py`,
   `battlepop.py`), the lobby cast (`npccast.py`, `npcroster.py`), rooms and
   the relay between players (`room.py`, `rooms.py`, `roomrelay.py`,
   `peerlink.py`, `groupchannel.py`) and the battle (`squad.py`,
   `referee.py`).
5. `community.py`: the second server the client dials on the same port.

A module reaches another module's names as `module.NAME`, so every use says
where the name lives. The log helpers (`log`, `hexdump`) and the knob
readers (`_env_int`, `_env_float`) are the exception: nearly every module
uses them, so they are imported by name. Every `FMO_*` knob's shipped value
is in `defaults.py`, which the package imports before anything else.

`Session` is one class assembled from mixins. `session.py` holds the
connection, its cipher and `on_packet`; the methods that belong to one
concern live beside it (`SessionSortie` in `sortie.py`, `SessionPilotRecord`
in `pilotrecord.py`, `SessionSettlement` in `settlement.py`, `SessionResume`
in `resume.py`, `SessionTrade` in `trade.py`).

### The facade

`services/fmo.py` is where the whole server used to live. It is now a thin
module that forwards `fmo.<name>` reads and writes to the module that owns
the name. Tools and tests written as `import fmo` keep working, including
the ones that patch a knob or a helper (`fmo.ROOM = True`): the write lands
in the owning module, so the code under test sees it. The selftest's own
patches of the old `globals()` go through the same door (`flat_globals()`
in `deps.py`). `tools/facade_rebind_check.py` proves the forwarding for
every patch the tools and the selftest make, and runs with the other suites.

### Import order

`fmoserver/__init__.py` imports every module in one fixed order. A module
imports at its top only the modules it reads while it is being imported (a
constant built from another module's constant); the ones it only calls at
run time are imported at its end. If you add a module-level constant that
reads another module, import the package once (`python -c "import fmo"` from
`services/`) to see that the order still holds.

### Changing the package

The package was generated once from the single-file `fmo.py` of commit
48067ab, in commit cec8ab1. It is the source now and is edited directly;
nothing regenerates it. Work written against the single file (an older
branch, or a change still being ported from the private deployment) is
carried over by hand into the module that owns that code today.

`fmo.py` stays as the entry point and as the facade described above. It
forwards only the names listed in its `_OWNERS` table, each mapped to the
module that owns it, so a new top-level name is not reachable as `fmo.NAME`
until it has a line there. Code inside the package does not need one,
because it reaches the name as `module.NAME`. A tool or test that reads or
patches the name through `fmo`, or a selftest patch through `flat_globals()`,
does: without the line a read raises AttributeError and a write stays on the
facade, where nothing reads it. `tools/facade_rebind_check.py` lists such a
write as a note and does not fail on it. A new module goes into the import
list in `fmoserver/__init__.py` and into `_MODULES` in `fmo.py`.

## Running the checks

```
python check.py --selftest          # the hygiene scanner can fail (positive controls)
python check.py                     # nothing private or proprietary in the tree
python tools/gen_blowfish_tables.py # once: the cipher tables, computed from pi
python tools/fmo_run_all.py         # every self-test; -k <substring> picks a few
```

Every suite is expected to pass on a clean checkout. The suites import the
core's `polcore` (and `fmo_title` its `titles.py`), so they need the
OpenLobby core checked out beside this repository, or `OPENLOBBY_DIR`
pointing at it (`OPENLOBBY_SERVICES` at its `services/`), and the drivers
(`pip install "psycopg[binary]" psycopg-pool valkey`). The ones that touch
the database each get a new, empty one from the core's `tools/pgtest.py`,
which uses Docker or the server `POL_TEST_DATABASE_URL` names. Without a
server they report SKIP, and `POL_TEST_REQUIRE_DB=1` makes that a failure.
`tools/fmo_import_test.py` rebuilds the old files from git at a pinned
commit, so a shallow clone needs `git fetch --unshallow` first. GitHub
Actions runs the scanner and the suites on every push and pull request.

A new self-test is registered by hand in `tools/fmo_run_all.py`. The list is
explicit on purpose: a suite that is not registered does not run.

## Where state lives

Anything that must survive a restart is a table in the core's PostgreSQL:
the pilots (`fmo_character`), the squadron insignia, the war
(`fmo_war`), the sector wins (`fmo_sector_win`) and the City Control
board's Discord bookkeeping (`fmo_board_state`). Who is playing from which
address, and a member's POL groups, come from the core's account tables
through `accounts`; `FMO_MEMBER_LOOKUP=0` turns that lookup off. Nothing
durable goes in Valkey, and files are only for what the operator edits,
such as the lobby NPC layout.

A schema change is a new file in `services/fmo_migrations/` with the next
number. A shipped migration is never edited. The core's `schema_migrations`
table is keyed by the number alone and shared with the core and the other
titles, so CrystalFront keeps to 2001-2999 and a table name that starts
with `fmo_`; a reused number is silently skipped.

Moving a file into the database comes with an importer in `fmodb.py`
(`python fmodb.py import fmo_db|board_state ...`). It only reads its
source, runs in one transaction, refuses a table that already holds rows
unless given `--merge`, writes nothing with `--dry-run`, and changes nothing
on a second run. Its command is added to `TITLES` in OpenLobby's
`tools/db_import.py` and to its `docs/database.md`. `fmo.db` has to be
imported before `fmo` first starts, or the service fills `fmo_character`
from the older `fmo_characters.json` and the import is refused.

`live_sessions.py` is the core's. The server publishes its session count
with `live_sessions.start_heartbeat`; a copy of the module must not be added
here. `services/.dockerignore` keeps one out of the image and the build
refuses an image whose `live_sessions` is not the core's. A deploy script
asks a running container, for example
`docker compose exec -T fmo python live_sessions.py count fmo`.

## What a pull request needs

- One topic per pull request, with a subject line that says what the server
  now does differently ("Front Mission Online: a trade cancelled by either
  pilot releases both").
- The checks above green, and a self-test for behaviour that can be pinned
  offline. A change to what a suite pins updates the suite in the same pull
  request.
- No Square Enix content: no client files or tables, captured server blobs,
  art or fonts. The game data under `services/fmodata/` is built by each
  user from their own client (`tools/fmodata_build.py`) and is not tracked,
  apart from the events table, which is original work.
- Nothing private: no real addresses or hostnames, no member names or ids.
  A new address becomes an environment knob with a loopback or empty
  default.
- Plain prose in comments and docs: say what the code does and why.
- Behaviour that exists because the client needs it keeps a comment saying
  which client address or screen depends on it and what happens without it.
  Most of this server is shaped by a 2005 client read from its disassembly,
  and a reader cannot tell a quirk from a mistake without that note.

## Reporting a bug

Open an issue with the client (PC or PlayStation 2), the knobs you changed
from the defaults, the log lines around the failure (`/logs` in the
container) and what the client showed.
