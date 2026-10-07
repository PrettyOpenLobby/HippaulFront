"""The release defaults: every FMO_* knob's shipped value, applied before any module reads the
environment."""
import os
import sys


#: RELEASE DEFAULTS. Every FMO_* knob below was a probe lever on the private
#: deployment, one environment variable each; these are the values that
#: deployment runs, copied here so a fresh server behaves the same with no
#: .env at all. A knob set in the environment (non-empty) still wins; an empty
#: value counts as unset, like everywhere else in this file. Deployment-shaped
#: values (the advertised host, ports, data paths) come from docker-compose.yml.
RELEASE_DEFAULTS = {
    # the service
    "FMO_PORT": "61300", "FMO_HOLD": "900", "FMO_UDP": "1", "FMO_UDP_HID": "2",
    "FMO_CHAR_STORE": "/data/fmo_characters.json", "FMO_DB": "1",
    # the lobby: the opening cutscene's map, the zone table, arrival points
    "FMO_MAPNO": "102", "FMO_MAPKIND": "200", "FMO_0153_F18": "0",
    "FMO_0153_PILOTPOS": "2.00,3.00,35.00,0",
    "FMO_MOVE_LIST": "102:2",
    "FMO_MOVE_LIST_ROOM": "141:3,142:2,143:1,144:2,124:1,151:0,101:3",
    "FMO_MOVE_LIST_BRIEFING": "122:0,123:0",
    # the Change Room maps: Room 121, Briefing 122 (O.C.U.) / 123 (U.S.N.),
    # Room B and C the bar 124, Hangar 141 (fmoserver/move.py)
    "FMO_ROOM_MAPS": "1:121,2:122/123,3:121,4:121,5:141",
    "FMO_UDP_POP_POS_MAP": ("101:-2.57,2.99,-43.44,0;102:0.52,3.11,9.41,0;"
                            "121:4.00,0.00,1.56,1.571;122:0.20,0.00,3.11,0;"
                            "123:-5.10,0.00,-0.83,0;124:-0.46,0.00,16.69,0;"
                            "141:1.47,0.50,24.59,0;142:6.82,0.27,18.73,0;"
                            "143:-0.23,0.50,24.50,0;144:0.71,0.50,24.51,0;"
                            "151:-0.14,-3.54,11.96,0;161:26.00,16.00,25.50,1.571"),
    "FMO_UDP_POP_POS": "40,5,40,0", "FMO_UDP_POP_AFTER": "0",
    "FMO_UDP_HELLO_ACK": "4", "FMO_UDP_POP_LOOK_DEFER": "0",
    "FMO_ZONE_MAPNO": "1:102,2:102,3:102,4:102,5:102,600:161",
    "FMO_ZONE_CONTROL": "all", "FMO_AREA_CHANGE_MAPNO": "auto",
    # the pilot's seed status (a stored character keeps its own values)
    "FMO_START_STATUS": "1", "FMO_RANK": "21", "FMO_STATUS_MONEY": "12345",
    "FMO_STATUS_MP": "67", "FMO_STATUS_CONTRIB": "890", "FMO_STATUS_NAMES": "1",
    "FMO_STATUS_SEX": "roster", "FMO_STATUS_NATION": "1",
    "FMO_STATUS_FLAGS": "8,9,10,11,12,13,128=99", "FMO_SQUADRON": "1",
    "FMO_UDP_POP_NATION": "1",
    # a NEW pilot (FMO_RANK / FMO_STATUS_* above stay the fallback for a record
    # with no value of its own, so no existing pilot is demoted): Conscript,
    # no MP, no contribution, and money by the nations' head-count gap
    # (manual p.38; the formula is ours: base 10000, +100 per point of gap
    # for joining the smaller side, at most +10000)
    "FMO_SEED_RANK": "0", "FMO_SEED_MP": "0", "FMO_SEED_CONTRIB": "0",
    "FMO_START_MONEY": "10000:100:10000",
    # character making (manual p.38): no delete for 24 h, the nation screen
    # shows real head counts and closes the larger side past a 30% lead of
    # at least 10 pilots
    "FMO_DELETE_LOCK_HOURS": "24", "FMO_NATION_POP": "live",
    "FMO_NATION_CLOSE_PCT": "30", "FMO_NATION_CLOSE_MIN": "10",
    # the lobby cast (entity key @ position # face = label)
    "FMO_UDP_POP_NPC": ("0x82080200@-1.35,3.50,2.74#113=Scramble.Board;"
                        "0x82080400@1.50,3.50,2.53#109=Map.Selector;"
                        "0x82080100@-2.58,3.11,6.09#115=Mission.Counter;"
                        "0x82080500@4.23,3.11,5.85#120=Personnel.Officer;"
                        "0x82081010@3.29,3.11,8.59#100=Kwangsu.Son;"
                        "0x82081020@10.61,2.99,7.88#102=Henry.Viduka;"
                        "0x82080110@11.18,2.99,1.20#103=Edward.Miura;"
                        "0x82080600@2.60,3.50,2.53#109=Sortie.Console;"
                        "0x82080c00@3.70,3.50,2.53#109=Hangar.Console"),
    "FMO_UDP_POP_NPC_CLIENT_KIND": "1", "FMO_UDP_POP_NPC_TARGETABLE": "1",
    "FMO_UDP_POP_NPC_RELOOK": "3", "FMO_UDP_POP_NPC_REPOP": "20",
    "FMO_HQ_MARKS": "0",
    # the sortie and the battle
    "FMO_UDP_POP_BATTLE": "1:0", "FMO_BATTLE_DUMMY": "0x2222:0",
    "FMO_UDP_POP_CLIENT_KIND": "0", "FMO_BATTLE_GATE_POP": "1",
    "FMO_BATTLE_POS": "64,5,64", "FMO_BATTLE_BOUNDS": "-2048,-2048,2048,2048",
    # per-map spawn points (fmodata/fmo-battle-spawns.tsv); FMO_BATTLE_POS is
    # the fallback for a map without a row
    "FMO_BATTLE_SPAWNS": "1",
    "FMO_SORTIE": "1", "FMO_SORTIE_MAPNO": "418",
    "FMO_BATTLE_MAPS": ("418:0,418:1,418:2,418:3,418:4,418:5,418:6,418:7,"
                        "418:8,418:9,418:10,418:11,418:12,418:13,418:14,418:15"),
    "FMO_BATTLE_START": "2", "FMO_BATTLE_END": "limit",
    "FMO_BATTLE_END_EXP": "3:40", "FMO_UDP_GROUP_LEADER": "auto",
    "FMO_RESULT_PUSH": "1", "FMO_RESULT_MONEY": "2500", "FMO_RESULT_CONTRIB": "11",
    # battle pay on top of the flat result: per enemy destroyed, and per win
    "FMO_KILL_CONTRIB": "50", "FMO_KILL_BONUS_HS": "500",
    "FMO_WIN_MONEY": "1500", "FMO_WIN_CONTRIB": "30",
    # the client runs the enemy (brain 101): a squad of 3, 120 units out, 40 apart
    "FMO_BATTLE_DUMMY_AI": "101", "FMO_BATTLE_ENEMIES": "3:120:40",
    # the player trade service, the phase-end ceasefire bonus, and the officer
    # review above Captain (keep or promote, never demote)
    "FMO_TRADE": "1", "FMO_CEASEFIRE": "1", "FMO_REVIEW": "promote",
    # play between pilots: the peer link, the character ids the client sees,
    # the defeat delay, group voice routing and Change Nations
    "FMO_UDP_PEER_LINK": "1", "FMO_CHAR_WIRE_BASE": "0x1000",
    "FMO_BATTLE_DEATH_END": "5", "FMO_GROUP_VOICE_TO": "talkers",
    "FMO_NATION_CHANGE_TOGGLE": "1",
    # missions, the war map, pay
    "FMO_MISSION_AREA": "map", "FMO_MISSION_TIME": "1800",
    "FMO_MSN": "1",
    # THE BOARD ROWS (re-authored 2026-09-30 from the Playing Manual p.60 and
    # guide/mission 20: required rank and MP rise Battle Map < Sector < Area,
    # and so do difficulty and reward). Ids and battlefields are kept, so
    # stored accepts still match. Per row: 0x1E4 required rank (a rank-table
    # index, fmo-ranks.tsv), 0x1DC Fee in MP, 0x1E8 reward MP, 0x1EC reward H$.
    #   Battle Map (Intelligence Officer): low rank, low Fee, low pay.
    #     7 Recon Alpha        rank 2 Veteran Private   Fee  5  MP 15  H$ 1500
    #    17 Escort Duty        rank 4 Pvt First Class   Fee  8  MP 20  H$ 2000
    #    19 Frontline Assault  rank 6 Corporal          Fee 10  MP 25  H$ 2500
    #   (one battle pays FMO_RESULT_MONEY 2500 + FMO_WIN_MONEY 1500, so a
    #   battle-map reward is about one more battle's money: "fairly low")
    #   Sector (Intelligence Officer): higher rank and Fee, generous pay.
    #    11 Sector Sweep       rank 10 First Sergeant   Fee 30  MP 80  H$ 6000
    #   (the taker also pays FMO_ORDER_BASE_MP 20 per derived order, p.60's
    #   warning box, so the reward MP covers the Fee and two orders)
    #   Area (Senior Officer, Strategy Room in Frontline area 10 = zone 509):
    #    13 Deep Strike        rank 21 Major            Fee 60  MP 200 H$ 20000
    #   (SE lowered the area rank to Second Lieutenant on 2005-09-06, but the
    #   client opens the Strategy Room only for rank >= 21 in MapKind 509, so a
    #   lower row could never be reached; its fallback tile 94101 is a zone-509
    #   sector on battle map 418, and the pilot picks the real target anyway)
    # Contribution is paid on the report by category (FMO_MISSION_CONTRIB),
    # sector x2 (FMO_MISSION_CONTRIB_SECTOR_X, SE 050628:92): 100 / 300 / 600.
    # All figures are OURS: SE published the ordering, never the numbers.
    "FMO_MSN_ROWS": ("7/1:Recon Alpha|11/2:Sector Sweep|13/3:Deep Strike|"
                     "17/1:Escort Duty|19/1:Frontline Assault"),
    "FMO_MSN_FIELDS": ("0:0x1F4=59127,0:0x1E4=2,0:0x1DC=5,0:0x1E8=15,0:0x1EC=1500,"
                       "1:0x1F4=71122,1:0x1E4=10,1:0x1DC=30,1:0x1E8=80,1:0x1EC=6000,"
                       "2:0x1F4=94101,2:0x1E4=21,2:0x1DC=60,2:0x1E8=200,2:0x1EC=20000,"
                       "3:0x1F4=72117,3:0x1E4=4,3:0x1DC=8,3:0x1E8=20,3:0x1EC=2000,"
                       "4:0x1F4=89135,4:0x1E4=6,4:0x1DC=10,4:0x1E8=25,4:0x1EC=2500"),
    "FMO_MISSION_CONTRIB": "1:100,2:150,3:600",
    "FMO_MISSION_PLACE": "1", "FMO_SECTOR_OWN_WINS": "1",
    "FMO_MISSION_ACCEPT": "gate", "FMO_MISSION_FEE": "1", "FMO_MISSION_REPORT": "1",
    "FMO_ANSWER_018D": "short", "FMO_MSN_WINS": "1:1",
    "FMO_MSN_ZONES": "1:200,2:509,3:200,4:207", "FMO_MSN_END_OPS": "0x08",
    "FMO_COUNTER_MISSION": "3", "FMO_AREA_TARGETS": "1", "FMO_PARTS_STOCK": "0x13",
    "FMO_WAR": "1", "FMO_WAR_MAP": "nation=0x05:b,bg_max=0x31:b,bg_min=0x55:b",
    "FMO_WARMAP_MAPS": "418",
    # one Controlled Zone (HQ) pass on promotion to Private First Class, rank
    # byte 4 (manual p.44); each pass is granted once per pilot
    "FMO_SALARY": "1", "FMO_PERMIT": "0", "FMO_PERMIT_RANKS": "4:hq",
    # the paint shop (0x01A4): SE's cosmetic prices are not in any data we
    # hold; ours, sized against a ~2500 H$ sortie and the 1000/2000 H$ passes:
    # camouflage 3000, a colour 1500, an insignia 1000
    "FMO_COSMETIC_PRICES": "2:3000,3:1500,4:1000",
    # battle exp: about 10 wins a Pilot level in the easiest sectors, twice as
    # fast at NPC rank 5, the Frontline a quarter more again (battleend's
    # note; SE's shape, our numbers). With 70% wins that is roughly 75 battles
    # to Pilot Lv 10 (Occupied Zone, Coliseum) and 145 to Lv 20 (Frontline)
    "FMO_EXP_PACE": "10", "FMO_EXP_SECTOR_PCT": "200", "FMO_EXP_FRONT_PCT": "125",
    # log back in where you logged out (manual p.42); armed 2026-09-30; the Coliseum band stays excluded (zoneentry.py)
    "FMO_RESUME_ZONE": "1",
    "FMO_ANNOUNCE": "Huffman Island. Welcome back, pilot.",
}
#: `--selftest` runs under the code's own defaults, as the suite always has;
#: the release set turns on the war, the mission board and the pay paths,
#: whose selftest coverage is a separate matter.
#: FMO_RELEASE_DEFAULTS=0 runs the code defaults instead (the offline suites
#: use it: tools/fmo_run_all.py).
if "--selftest" not in sys.argv and os.environ.get("FMO_RELEASE_DEFAULTS", "1") != "0":
    for _k, _v in RELEASE_DEFAULTS.items():
        if not os.environ.get(_k, "").strip():
            os.environ[_k] = _v
    del _k, _v
