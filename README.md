# CrystalFront

A server for Front Mission Online, Square Enix's 2005 PC (and PlayStation 2)
team-based mech game whose lobby, missions and sorties ran through PlayOnline.
It plugs into [OpenLobby](https://github.com/PrettyOpenLobby/OpenLobby), the
PlayOnline core, and answers the game's world server: the lobby with its
cast, the pilot's status and hangar, the shop, squadrons, the mission board,
the war map and the sortie into a battle.

This is a clean-room reimplementation from protocol observation. It contains
no Square Enix code, art or data. You need your own Front Mission Online
client install; the game's own tables are rebuilt from it (below).

## What it does today

Tested on a private deployment with a PC client. On this stack a pilot
can:

- log in through the core, watch the opening cutscene with its cast, and walk
  the headquarters lobby with its NPCs and name tags;
- keep a persistent character (identity, money, rank, contribution, class
  levels, progress flags) in the title's own database;
- open the hangar and the shop, buy and fit parts, dress a wanzer;
- form and manage a squadron (a PlayOnline group), see its insignia and info;
- read the mission board, accept a mission, report it and be paid; draw a
  daily salary; hold a city on the war map (City Control);
- sortie: pick a battle map, enter the arena in the dressed wanzer, fight the
  enemy squad the client runs, see the battle begin and end, and return to
  the lobby through the result screen;
- fight beside or against another pilot: the server stands in as each
  client's peer, so pilots see each other move and shoot, kills and wins are
  credited to the pilot who earned them, and a destroyed pilot's own battle
  ends as a loss while the others fight on;
- form a battle group: the Player List, the Sortie Setting, a group sortie
  the other members are offered to follow, and voice chat relayed between
  members whose voice system is up.

What it does not do: the campaign cutscenes are set up but have not been
watched through, and voice has been relayed but not yet heard on a client
with a working capture device. Two clients behind one router each keep
their own session: a world channel is bound to the player who entered it,
not to the address.
The PlayStation 2 client reaches the lobby but renders its lobby map as an
empty void. It ships a different map set from the PC client, and which map
numbers it has is still unknown.

## How it fits the core

The core does the login, the DNS and the member profile; this repository is
one service, `fmo.py` (the `fmoserver` package behind it), holding the TCP
session the client keeps for its whole lobby stay and the UDP world channel
beside it (both on 61300). It runs in the core's compose project: it reads
the core's account and session tables in PostgreSQL (which tell it who is
playing from which address), and keeps its pilots, squadron
insignia, war state and sector wins in the core's PostgreSQL database, in
tables of its own (`fmo_*`). Their schema is this repository's migrations
(`services/fmo_migrations/`, numbered 2001 and up), applied by the service
when it starts. The client is sent here by the core's games menu (content
id 4) and dials `fmo01.pol.com`, which the core's DNS answers with the
advertised address.

The stores this server kept as files before it moved onto the database are
imported the first time the database is empty and left where they were:
`fmo_characters.json` (the character store, which `FMO_DB=0` still switches
back to), `fmowar.json` and `fmo_sector_wins.json`. Nothing needs running for
those three. `fmo.db`, the SQLite player database of earlier versions, and
the City Control board's Discord state are imported by hand, after
OpenLobby's own import (its docs/database.md, "Moving an existing /data") and
before `fmo` first starts. Otherwise the service fills `fmo_character` from
the older `fmo_characters.json` and the import of `fmo.db` is refused. From
this directory:

```
DC="docker compose --project-directory ../openlobby --env-file ../openlobby/.env --env-file .env -f ../openlobby/docker-compose.yml -f docker-compose.yml"
$DC run --rm --no-deps --entrypoint python fmo fmodb.py import fmo_db /data/fmo.db
$DC run --rm --no-deps -v crystalfront_fmo-board-state:/state:ro --entrypoint python fmo fmodb.py import board_state /state
```

The second reads the board's old state volume (`crystalfront_fmo-board-state`
from when this was a compose project of its own; `docker volume ls` shows
the name) and needs running only where the board posted to Discord. Each
command only reads its source, runs in one transaction, prints what it
imported and each row it could not map, and refuses a table that already
holds rows unless given `--merge`, which adds only the keys the table lacks.
`--dry-run` prints the same report and writes nothing, and a second run
changes nothing.

## The title plugin (the Viewer's profile)

The core builds the profile the Viewer shows for a Front Mission Online Content ID from
data only this title holds, so a small plugin runs inside the core's `login`
and `authsess` processes (OpenLobby's `services/titles.py`, `POL_TITLES`).
`Dockerfile.title` layers it on the core image and `docker-compose.title.yml`
swaps that image into those two services. From this directory, with the core
checked out beside it:

```
docker compose --project-directory ../openlobby     -f ../openlobby/docker-compose.yml -f docker-compose.title.yml     up -d --build login authsess
```

Without it the game plays the same; only the Viewer's profile screen for a Front Mission Content ID stays empty. The plugin reads the pilots from the core's PostgreSQL database, which `login` and `authsess` are already connected to. To run several titles, build each title image on the previous
one (`OPENLOBBY_IMAGE`) and list them all in `POL_TITLES` in OpenLobby's
`.env`, for example `POL_TITLES=tmtitle,fmotitle`.

## Prerequisites

- The core lobby stack (OpenLobby) checked out beside this repository, with
  its image built (`openlobby:latest`), because this service's image is built
  on top of it
- A Front Mission Online client install of your own
- Docker with Compose v2, and Python 3.10+ on the host for the two
  generation steps

## Bring-up

```
# 1. cipher tables, computed from public constants (no client needed):
python tools/gen_blowfish_tables.py

# 2. game tables, extracted from YOUR client install:
python tools/fmodata_build.py --client "C:\path\to\FRONT MISSION ONLINE"

# 3. the service, in the core's compose project:
cp .env.example .env      # set POL_ADVERTISE to your server's LAN/VPN IP
docker compose --project-directory ../openlobby \
    --env-file ../openlobby/.env --env-file .env \
    -f ../openlobby/docker-compose.yml -f docker-compose.yml \
    up -d --build fmo
```

`docker-compose.yml` is an override of OpenLobby's compose file, the way
`docker-compose.title.yml` is: the service joins the core's network and
starts after its PostgreSQL and Valkey. OpenLobby's `.env` comes first so
its `POL_DB_PASSWORD` reaches the connection string; this repository's
`.env` carries the `FMO_*` knobs.

Without building: the image is published to
`ghcr.io/prettyopenlobby/crystalfront` on every push (it carries the cipher
tables, so step 1 is not needed); step 2 still runs on the host, and the
override mounts your `services/fmodata/` (and the baked board art) into the
containers:

```
docker compose --project-directory ../openlobby \
    --env-file ../openlobby/.env --env-file .env \
    -f ../openlobby/docker-compose.yml -f docker-compose.yml \
    -f docker-compose.ghcr.yml up -d fmo
```

Step 2 writes `services/fmodata/`: the rank ladder and class experience
curve (`fmo-ranks.tsv`, `fmo-class-exp.tsv`), the cosmetics, insignia,
mission and cutscene catalogues, and the lobby editor's floor plans, NPC keys
and script marks. Only one file in that directory ships with the repository,
because it is original work: `fmo-events.tsv`, the server's own rule for
each script event. The service runs without step 2, with gaps: no rank names
or promotions, no class levels, an empty shop catalogue, no mission
catalogue beyond the built-in rows, no editor overlays.

## Configuration

About three hundred `FMO_*` environment variables decide what this server
serves; they were added one at a time while the protocol was worked out. The values a fresh
server runs with are the private deployment's, listed in
`services/fmoserver/defaults.py` (`RELEASE_DEFAULTS`); `docker-compose.yml`
carries only the deployment-shaped ones. To change a knob, set it in `.env` and add it to the `fmo` service's
environment (compose enumerates what reaches the container).

These battle and economy knobs are on by default (set one to 0 in `.env` to
turn it off):

- `FMO_KILL_CONTRIB=50`: contribution per enemy destroyed.
- `FMO_KILL_BONUS_HS=500`: H$ per kill, owed as a "Kill bonus" line at the Personnel Officer.
- `FMO_WIN_MONEY=1500`: H$ added to a won battle.
- `FMO_WIN_CONTRIB=30`: contribution added to a won battle.
- `FMO_BATTLE_DUMMY_AI=101`: the client runs the battle enemy with AI brain 101.
- `FMO_BATTLE_ENEMIES=3:120:40`: the enemy squad's size, distance from the drop point and spacing.
- `FMO_TRADE=1`: the player trade service.
- `FMO_CEASEFIRE=1`: the phase-end ceasefire bonus for First Sergeant and above.
- `FMO_REVIEW=promote`: the officer review above Captain keeps or promotes a pilot (`full` can also demote).

Play between pilots is on by default too:

- `FMO_UDP_PEER_LINK=1`: the peer link. Another pilot's unit is popped with
  this server's endpoint and a per-peer tag, so the client sends it its
  movement, fire and voice, which the server relays. 0 restores the old POP,
  under which other pilots stand frozen.
- `FMO_CHAR_WIRE_BASE=0x1000`: character ids as the client sees them (the
  stored id plus this base). The client never networks a unit whose id is
  below 10; 0 serves the stored ids.
- `FMO_BATTLE_DEATH_END=5`: seconds after a pilot's own wanzer is destroyed
  until that pilot's battle ends as a loss (0 = never).
- `FMO_GROUP_VOICE_TO=talkers`: group voice goes only to members that have
  sent voice themselves (a client whose voice system failed hangs on
  receiving it); `all` sends it to every member.
- `FMO_NATION_CHANGE_TOGGLE=1`: Change Nations switches the pilot to the
  other nation (the client's submit names none); 0 leaves the nation alone.

## The lobby NPC editor (optional)

A web page that places the lobby cast over the map's floor plan and writes
the layout the service pops (`FMO_NPC_LAYOUT`). Set `FMO_DEVTOOL_PORT=8798`
in `.env`; it listens on the host's loopback (`FMO_DEVTOOL_BIND` and
`FMO_DEVTOOL_TOKEN` open it further). The floor plans, NPC keys and script
marks it draws come from step 2.

## The City Control board (optional)

Add `--profile board` to the compose command above and name `board`
(`... up -d board`). It serves the war (the nineteen cities, who holds each,
the phase score and clock) as a web page on port 8792, read-only over the
war state in the database, and can post
it to Discord through a webhook or as a bot (`.env`; a bot needs no webhook
and posts wherever `/fmoboard` is run). The page's backdrop is the game's own
satellite image, baked from your client with `tools/fmo_boardart_bake.py`;
without it the board draws on a plain ground. Which message it posted, and
where each feed posts, is kept in the `fmo_board_state` table, so a restart
edits the same message instead of posting a second one.

## Selftests

```
python tools/fmo_run_all.py
```

runs the offline suite: every request the client sends driven through the
real dispatcher, the world channel's cipher and framing, the community server
codec, the war model, the player database, the board and the import of the
old files. Checks that need
the generated game tables skip themselves until step 2 has been run.

The suites import OpenLobby's `polcore` from the checkout beside this one
(or `OPENLOBBY_DIR`, or `OPENLOBBY_SERVICES` for its `services/` directory)
and need `pip install "psycopg[binary]" psycopg-pool valkey`. The ones that
touch the database each get a new, empty one from OpenLobby's
`tools/pgtest.py`, which starts a throwaway `postgres:17-alpine` in Docker
(or uses the server `POL_TEST_DATABASE_URL` names) and removes it afterwards;
they never use `POL_DATABASE_URL`. Without Docker or that variable those
checks report SKIP, and `POL_TEST_REQUIRE_DB=1` turns that into a failure.

## What is not included, and why

- No Square Enix game data: `services/fmodata/` is generated from your own
  client, not shipped; so is the board's backdrop.
- The cipher tables are generated from pi (the client's tables are the
  public Blowfish constants; see `tools/gen_blowfish_tables.py`).
- No client, no patches, no PlayStation 2 tooling.

## License

AGPL-3.0 (see LICENSE).

## Credits

- The PlayOnline preservation community.
- The 2005 Front Mission Online community sites, whose guides named the
  cities, the ranks and the missions.
