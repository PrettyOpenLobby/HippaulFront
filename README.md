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

Worked out and played on a private deployment with a PC client. On this
stack a pilot can:

- log in through the core, watch the opening cutscene with its cast, and walk
  the headquarters lobby with its NPCs and name tags;
- keep a persistent character (identity, money, rank, contribution, class
  levels, progress flags) in the title's own database;
- open the hangar and the shop, buy and fit parts, dress a wanzer;
- form and manage a squadron (a PlayOnline group), see its insignia and info;
- read the mission board, accept a mission, report it and be paid; draw a
  daily salary; hold a city on the war map (City Control);
- sortie: pick a battle map, enter the arena in the dressed wanzer, see the
  battle begin and end, and return to the lobby through the result screen.

What it does not do: combat itself. The practice target cannot be destroyed
(hits are applied client-side to units the shooter owns, and no exchange
between two clients has been closed on a screen), so a sortie is an entry,
a walk and an exit. The campaign cutscenes are staged, not watched through.
Play between two humans in one lobby works; two in one battle is untested.
The PlayStation 2 client reaches the lobby but renders its lobby map as a
void (it ships a different map set); that is an open question, not a setting.

## How it fits the core

The core does the login, the DNS and the member profile; this repository is
one service, `fmo.py`, holding the TCP session the client keeps for its whole
lobby stay and the UDP world channel beside it (both on 61300). It joins the
core's data volume for the shared account and session database (which tells
it who is playing from which address) and keeps its own player database
beside it. The client is sent here by the core's games menu (content id 4)
and dials `fmo01.pol.com`, which the core's DNS answers with the advertised
address.

## Prerequisites

- The core lobby stack (OpenLobby) running on the same Docker host
- A Front Mission Online client install of your own
- Docker with Compose v2, and Python 3.10+ on the host for the two
  generation steps

## Bring-up

```
# 1. cipher tables, computed from public constants (no client needed):
python tools/gen_blowfish_tables.py

# 2. game tables, extracted from YOUR client install:
python tools/fmodata_build.py --client "C:\path\to\FRONT MISSION ONLINE"

# 3. the service:
cp .env.example .env      # set POL_ADVERTISE to your server's LAN/VPN IP
docker compose up -d --build
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

Front Mission Online on this server grew up as a probe harness: about three
hundred `FMO_*` environment knobs decide what is served. The values a fresh
server runs with are the private deployment's, listed in `services/fmo.py`
(`RELEASE_DEFAULTS`); `docker-compose.yml` carries only the deployment-shaped
ones. To change a knob, set it in `.env` and add it to the `fmo` service's
environment (compose enumerates what reaches the container).

## The lobby NPC editor (optional)

A web page that places the lobby cast over the map's floor plan and writes
the layout the service pops (`FMO_NPC_LAYOUT`). Set `FMO_DEVTOOL_PORT=8798`
in `.env`; it listens on the host's loopback (`FMO_DEVTOOL_BIND` and
`FMO_DEVTOOL_TOKEN` open it further). The floor plans, NPC keys and script
marks it draws come from step 2.

## The City Control board (optional)

```
docker compose --profile board up -d
```

serves the war (the nineteen cities, who holds each, the phase score and
clock) as a web page on port 8792, read-only over the war state, and can post
it to a Discord webhook (`.env`). The page's backdrop is the game's own
satellite image, baked from your client with `tools/fmo_boardart_bake.py`;
without it the board draws on a plain ground.

## Selftests

```
python tools/fmo_run_all.py
```

runs the offline suite: every request the client sends driven through the
real dispatcher, the world channel's cipher and framing, the community server
codec, the war model, the player database and the board. Checks that need
the generated game tables skip themselves until step 2 has been run.

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
