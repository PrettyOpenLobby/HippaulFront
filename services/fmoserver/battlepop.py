"""The battle POP knobs: arena position, the gate pop, the dummy enemy and the referee."""
import math
import os
from .deps import fmoworld
from .knobs import _env_int
from . import popsweep


#: WARNING: PLAN 1.2 -- A BODY IN THE BATTLE. `FMO_UDP_POP_BATTLE=<unitid>[:<unittype>]`
#: overrides the self-POP spec on a BATTLE channel only (key suffix "battle").
#: Default OFF (empty): the battle self-POP stays whatever FMO_UDP_POP serves.
#:
#: Why it exists: UnitType 4 (the lobby human) can never drive a wanzer -- the
#: possess chain 0x6105AAE0 (reached from 0x611ED760) runs for types
#: 0/1/2/3/5/6 and skips 4 -- and the old "types 1/2 are invisible" readings
#: all come from LOBBY sweeps, where nothing ever dresses a wanzer (lobby maps
#: carry no wanzer content; scene 4's mission maps do). So `FMO_UDP_POP_BATTLE=1:1`
#: (or `1:2`) is the first test that can answer whether a wanzer-class unit
#: draws where wanzers live. Bar (PLAN 1.2): a visible unit that takes input in
#: map 418; if still invisible, hook PARTSET in scene 4 -- battle may dress
#: from the MISSION BLOCK (state-4 spawns from lobby+0x5C8A/5C96/5CB2), which
#: is PLAN 1.3's territory, not more POP bytes.
#:
#: The sex byte (+0x7A) and the look block (+0x188..) are TYPE-4 HUMAN offsets;
#: what they mean to any other class is undecoded, so a non-4 self-POP sends
#: NONE of them (see the POP path). client_kind stays pop_client_kind_for()
#: ("auto" = 3 on battle -- required to register; the UNDESTROY un-poison then
#: applies to this override exactly as to a type-4 battle POP).
#: VERIFIED:KEY: FMO_BATTLE_DUMMY -- THE DRESS PROBE THAT STEPS AROUND THE DESTROYED
#: CIRCLE. `<unitid>[:<unittype>][@x,y,z]`, default OFF.
#:
#: WHY IT EXISTS (decoded live 2026-09-08, after two runs that each put six
#: correct part records into the unit and still drew nothing). The model kind is
#: NOT our UnitType when the unit is the SELF unit in a battle. `0x611ED660`:
#:
#:     611ed6dc  movzx edi, byte [body+0x89]     ; the UnitType we send
#:     611ed6e3  mov   ecx, [0x613C1698]         ; the BATTLE registry
#:     611ed6ed  mov   eax, [body+0x04]          ; our UnitID
#:     611ed6f6  call  0x61072650                ; battle_map.find(UnitID)
#:     611ed6fd  je    0x611ed728                ; KEY: A MISS USES edi
#:     611ed6ff  mov   eax, [char+0x1C]          ; the connection-char STATUS
#:     611ed702  cmp   eax,2 / je  611ed70c
#:     611ed707  cmp   eax,3 / jne 611ed728
#:     611ed70c  mov   edi, 0x28                 ; WARNING: KIND 40 -- THE WRECK
#:
#: and kind 40's factory arm builds no part slots, so `0x611F5700` finds NULL at
#: `unit+slot*4+0x381` and stores nothing. Measured: `--wanzer` read model class
#: 40 with 6/11 input records present. `char+0x1C` is a literal chosen ONLY by
#: client_kind (ctor 0x611D2F40, arms at 0x611D4530): 0->1, 1->0, 2->2, 3->3 --
#: and `0x611D4281` sets the scene-4 gate `+0x10DC=2` on client_kind 3 ALONE.
#: So the destroyed circle and the undressed unit are ONE problem: the only kind
#: that gets us into scene 4 is the only kind that makes us wreckage.
#:
#: KEY: THE STEP AROUND IT IS THE `je 0x611ed728` ABOVE. A unit id that is NOT in
#: the battle map misses the lookup and keeps our UnitType, so a SECOND unit --
#: not the self unit -- is built as a real wanzer and dressed from the same part
#: array. It is NOT the player and cannot be driven; it exists to answer one
#: question the self unit cannot: does the dressing chain work at all?
#:
#: Bar: `fmocrash.py --wanzer` shows TWO units, the second with model class 0
#: and NON-ZERO part slots. If it does, everything except the circle is proved
#: and the circle is the only remaining work. If it does not, there is a further
#: problem worth finding before anyone attacks the circle.
#:
#: WARNING: UnitType defaults to 0 because that is the kind the LOBBY builds the wanzer
#: it dresses as (`0x61003190`: push -1 / push 0 / push 0), and kind 0's arm is
#: one of only two that fill the part-slot array (0x611F3BC0; kind 4's
#: 0x611F4610 is the other, and it is the human's).
#: WARNING: The id MUST differ from FMO_UDP_POP/FMO_UDP_POP_BATTLE's, or it IS the self
#: unit, the lookup hits, and the probe measures the thing it was built to
#: avoid. Refused below rather than warned about.
#: VERIFIED:KEY: FMO_BATTLE_GATE_POP -- THE SECOND POP THAT LATCHES THE SCENE-4 GATE
#: WITHOUT MAKING US DESTROYED. Default OFF (`1` arms it).
#:
#: THE CIRCLE, measured on BOTH sides live 2026-09-08 rather than inferred:
#:   client_kind 3 -> char+0x1C = 3 -> 0x611ED660 forces the WRECK (kind 0x28),
#:                    0/12 part slots -- but +0x10DC = 2 and the battle RUNS.
#:   client_kind 0 -> char+0x1C = 1 (ALIVE) -> a real class-0 wanzer, 9/12 slots
#:                    with correct HP -- but +0x10DC = 0, setup state STALLS at
#:                    3 and the screen is BLACK (map loaded, nothing drawn).
#: There is no third value: the four arms at 0x611D4530 give statuses
#: {0:1, 1:0, 2:2, 3:3} and 0x611D4281 sets the gate on client_kind 3 alone.
#:
#: KEY: BUT THE GATE IS SET BEFORE THE CHAR IS EVEN LOOKED UP. In 0x611D4070:
#:     611d427c  cmp  [body+0x00], 3       ; client_kind
#:     611d4281  mov  [edi+0x10dc], 2      ; THE GATE -- first
#:     611d42a6  ...  battle_map.find(UnitID)
#:     611d42be  je   0x611d42dd           ; miss -> create the char
#:     611d42c0  mov  eax, [char+0x20]     ; HIT -> dispatch
#:     611d42cc  jmp  [eax*4 + 0x611d4520] ; 0 -> 0x611D44AB = the `ret 8` TAIL
#: So a SECOND cmd-7 on the SAME unit id carrying client_kind 3, sent after an
#: ALIVE first POP, sets +0x10DC = 2 and then returns at the char lookup --
#: without touching char+0x1C (we stay alive) and without reaching 0x611ED9E0
#: (the visual, our dressed wanzer, is never rebuilt).
#:
#: WARNING: NOT the 2026-09-04 un-poison, which force-closed the client. That sent a
#: cmd-8 DEPOP, which means "this peer LEFT", aimed at ourselves. This is two
#: ordinary cmd-7 POPs and never depops anything.
#: WARNING: NO NULL-WRITE HAZARD: 0x611D4281 is reached only for client_kind 3, and
#: that arm always allocates a peer first (0x611D4139 `push 0x1af9`), so `edi`
#: is never the 0 that 0x611D40DF initialised it to. client_kind 1 branches to
#: 0x611D428B before the write.
#: WARNING: WHAT IS NOT ESTABLISHED: the gate reads [[0x613C1698]+0x48]+0x10DC, and
#: this sets +0x10DC on the peer the SECOND pop allocates. Whether that peer
#: becomes bm+0x48 is the whole question and cannot be read statically -- if it
#: does not, this changes nothing (harmless); if it does, the circle opens.
#: It also leaves a second peer object for one unit id, which is a leak at best.
#: Bar: `--live` shows setup state (+0x1C4) = 4 or 6 with char+0x1C still 1.
#: WARNING:KEY: FMO_BATTLE_POS -- `x,y,z` for the BATTLE self-POP only. Default unset.
#:
#: WHY. The battle self-POP has always taken its position from
#: `FMO_UDP_POP_POS_MAP[<lobby MapNo>]` because `WORLD_MAPS` for that channel
#: still holds the LOBBY's MapNo -- there has never been a row for a mission
#: map. Live 2026-09-08 that put the pilot at **(0.52, 3.11, 9.41)**, and
#: `fmomap.py header` says map 418's extent is **0.0 .. 128.0**: that is hard
#: against the western edge of a 128x128 map. Harmless while we were a
#: destroyed spectator; the moment the pilot became a real, ALIVE unit the
#: client started flashing "out of battle area", pushing him sideways, and
#: finally withdrew him from the battle.
#:
#: So this is not a tuning knob, it is a missing field. The map centre
#: (64, y, 64) is the obvious first value; the REAL answer is the battle area
#: in the 3,400-byte mission block we still serve as zeros (PLAN 1.3), which
#: is where SE puts the spawn points.
#: WARNING: y is a guess in every map: the client has no spawn table and the maps do
#: not share an origin. Too low and the pilot is under the terrain, too high
#: and he falls. ±327.67 is the hard wire limit (int16 hundredths, 0x611E6BB0).
BATTLE_POS_SPEC = os.environ.get("FMO_BATTLE_POS", "").strip()
if BATTLE_POS_SPEC:
    try:
        _bp = tuple(float(x) for x in BATTLE_POS_SPEC.split(","))
    except ValueError:
        raise SystemExit(f"FMO_BATTLE_POS={BATTLE_POS_SPEC!r} is not x,y,z")
    if len(_bp) == 3:
        _bp += (0.0,)
    if len(_bp) != 4:
        raise SystemExit(f"FMO_BATTLE_POS={BATTLE_POS_SPEC!r} wants three or "
                         f"four floats")
    BATTLE_POS = _bp
else:
    BATTLE_POS = None


#: KEY: FMO_BATTLE_SPAWNS -- PER-MAP BATTLE SPAWN POINTS (2026-10-06). Default 1
#: (on); 0 = the old behaviour, every battle at FMO_BATTLE_POS and the squad a
#: line FMO_BATTLE_ENEMIES' distance along +x from it.
#:
#: WHY. On map 471 (Frontline 509 sector 32, prod 2026-10-06 15:55Z) the pilot
#: started on a roof and the three enemies stacked on one another. Decoded
#: from that battle's cmd 23/24 movement records (x/y/z = int16 at state+8,
#: / 3.75): the pilot's first position was (64.0, 46.4, 0.0), on top of the
#: building at x 0..64, z -16..48 (top 47.5); the enemies were at (184, 47.7,
#: 0) twice and (184, 97.6, 0), the third standing on the other two.
#:
#: KEY: z WAS 0 FOR EVERY UNIT IN EVERY NORMAL BATTLE IN THE LOG, and the
#: reason is body+0x27: fmoworld.POP_SIDE is the HIGH BYTE of the z float
#: (POP_POS = 0x1C, z = 0x24..0x27). Writing side 0 turns z = 64.0
#: (0x42800000) into 1.2e-38, side 1 into 2.4e-38. In the one battle that sent
#: no side (a Coliseum pilot with no character, 16:16Z) every unit kept its z:
#: (64, 32, 64), (184, 32, 24), (184, 82.1, 64), (184, 32, 104). And the side
#: byte does nothing else on the battle path: friend/foe is body+0x7C, the
#: NATION (fmo-dummy-cannot-die note, 2026-09-11, live probe). So a POP placed
#: from this table sends NO side byte (side=None), or the table's z would be
#: thrown away exactly like FMO_BATTLE_POS's was.
#:
#: The table (fmodata/fmo-battle-spawns.tsv, tools/fmodatagen/fmospawns.py):
#: per battle map, side A's and side B's points, a y, and 12 clear slots per
#: side along its front line (0-3 the pilots', 4-11 the squad's). A pilot takes
#: its side's point (side 0 = A, side 1 = B: popnation.battle_side_for, the
#: arena team in a match); the squad takes the OTHER side's slots. A map with
#: no row keeps FMO_BATTLE_POS and the old squad line, byte for byte.
#: Since 2026-10-07 the table is cut from the map's COLLISION meshes: every slot
#: stands where the highest surface is open, flat ground (no roof, bridge or
#: hill top), and y is the lowest slot ground minus 4. The ground read matched
#: the decoded y of 2,720 of 2,735 live movement positions on nine battle maps.
#: The client lifts a unit popped under the surface onto the HIGHEST surface
#: under it (map 86: popped at 5, settled at 32.2 over floors at 25 and 27).
#: WARNING: units 40 apart stacked (map 86, 16:16Z), so slots are 56 apart; the
#: spacing is not proved to be enough.
BATTLE_SPAWNS = _env_int("FMO_BATTLE_SPAWNS", "1") != 0
BATTLE_SPAWNS_TSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata",
    "fmo-battle-spawns.tsv")
#: slots 0..SPAWN_PILOT_SLOTS-1 of a side are its pilots', the rest its squad's
SPAWN_PILOT_SLOTS = 4
#: the line a squad falls back to when a row has too few slots for it (units
#: 40 apart stacked live, so the line is as wide as the table's slots)
SPAWN_FALLBACK_GAP = 56.0


def _parse_slots(text):
    out = []
    for tok in (text or "").split():
        x, _, z = tok.partition(":")
        out.append((float(x), float(z)))
    return out


def load_battle_spawns(path=None):
    """{mapno: {"a", "b": (x, z), "y", "a_slots", "b_slots": [(x, z)],
    "source"}} from fmo-battle-spawns.tsv. {} when the file is absent; a
    malformed row is skipped, never fatal."""
    rows = {}
    try:
        with open(path or BATTLE_SPAWNS_TSV, encoding="utf-8") as f:
            cols = f.readline().rstrip("\r\n").split("\t")
            for line in f:
                r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
                try:
                    m = int(r["map"])
                    row = {"a": (float(r["ax"]), float(r["az"])),
                           "b": (float(r["bx"]), float(r["bz"])),
                           "y": float(r["y"]),
                           "a_slots": _parse_slots(r.get("a_slots")),
                           "b_slots": _parse_slots(r.get("b_slots")),
                           "source": r.get("source", "")}
                except (KeyError, ValueError):
                    continue
                for k in ("a", "b"):
                    if not row[k + "_slots"]:
                        row[k + "_slots"] = [row[k]]
                rows[m] = row
    except OSError:
        return {}
    return rows


BATTLE_SPAWN_ROWS = load_battle_spawns()


def spawn_side_key(side):
    """'b' for side 1, else 'a' (side 0 or unknown)."""
    return "b" if side == 1 else "a"


def spawn_row(mapno, rows=None):
    """The table row for battle map `mapno`, or None (knob off, no map, no row)."""
    if not BATTLE_SPAWNS and rows is None:
        return None
    rows = BATTLE_SPAWN_ROWS if rows is None else rows
    try:
        return rows.get(int(mapno)) if mapno is not None else None
    except (TypeError, ValueError):
        return None


def battle_spawn_pos(mapno, side, index=0, rows=None):
    """((x, y, z, 0.0), why) for a pilot of `side` taking pilot slot `index`
    on battle map `mapno`, or (None, why) when the table has no row."""
    row = spawn_row(mapno, rows)
    if row is None:
        return None, f"no fmo-battle-spawns.tsv row for map {mapno!r}"
    k = spawn_side_key(side)
    slots = row[k + "_slots"][:SPAWN_PILOT_SLOTS] or [row[k]]
    x, z = slots[index % len(slots)]
    return ((x, row["y"], z, 0.0),
            f"fmo-battle-spawns.tsv map {int(mapno)} side {k.upper()} pilot slot "
            f"{index % len(slots)} ({row['source']})")


def squad_spawn_positions(mapno, pilot_side, n, rows=None):
    """n (x, y, z, 0.0) drop points for the enemy squad of a `pilot_side`
    pilot: the OTHER side's squad slots (4..11), then that side's spare pilot
    slots from the last (a pilot takes the lowest free one), then, if the row
    is still short, a line SPAWN_FALLBACK_GAP apart behind its point (not
    checked against the ground). None without a row."""
    row = spawn_row(mapno, rows)
    if row is None:
        return None
    k = "a" if spawn_side_key(pilot_side) == "b" else "b"
    slots = list(row[k + "_slots"][SPAWN_PILOT_SLOTS:])
    if len(slots) < n:
        spare = row[k + "_slots"][1:SPAWN_PILOT_SLOTS][::-1]
        slots += spare[:n - len(slots)]
    if len(slots) < n:
        px, pz = row[k]
        ox, oz = row["a" if k == "b" else "b"]
        ux, uz = px - ox, pz - oz
        d = math.hypot(ux, uz) or 1.0
        ux, uz = ux / d, uz / d                     # away from the pilots
        vx, vz = -uz, ux
        i = 0
        while len(slots) < n:
            i += 1
            back = 2 * SPAWN_FALLBACK_GAP + SPAWN_FALLBACK_GAP * (i // 5)
            col = (i % 5) - 2
            slots.append((round(px + back * ux + col * SPAWN_FALLBACK_GAP * vx, 1),
                          round(pz + back * uz + col * SPAWN_FALLBACK_GAP * vz, 1)))
    return [(x, row["y"], z, 0.0) for x, z in slots[:n]]


def spawn_pilot_index(chan, side, mates):
    """The lowest pilot slot of `side` no battle room-mate already holds."""
    taken = {getattr(o, "spawn_slot", None) for o in mates
             if getattr(o, "spawn_side", None) == side}
    i = 0
    while i in taken:
        i += 1
    return i

BATTLE_GATE_POP =os.environ.get("FMO_BATTLE_GATE_POP", "").strip() not in ("", "0")
#: The client_kind the gate POP carries. 3 is the only value that sets +0x10DC;
#: the knob exists so a run can prove the gate pop is what moved the state.
BATTLE_GATE_KIND = _env_int("FMO_BATTLE_GATE_KIND", "3")

BATTLE_DUMMY_SPEC = os.environ.get("FMO_BATTLE_DUMMY", "").strip()
#: client_kind for the probe. 1 builds no peer and maps to char status 0, so the
#: unit is ALIVE even if a char is made for it -- the point of the exercise.
BATTLE_DUMMY_KIND = _env_int("FMO_BATTLE_DUMMY_CLIENT_KIND", "1")
#: FMO_BATTLE_DUMMY_NATION: 0 (default) = the dummy's POP carries no nation
#: byte, as before (it listed as "Friendly Units: U.S.N.1" live 2026-09-10).
#: 1 = O.C.U., 2 = U.S.N. -- body+0x7C, the byte the self-POP already sets from
#: the pilot's own nation. Pop it as the OTHER army and the bar is the dummy
#: moving from Friendly Units to ENEMY UNITS on the battle map / review, and
#: the radar treating it as hostile. Which byte the battle's side test
#: (unit+0x80, compared in the objective/beacon arms) is fed from is read
#: only as far as the unit ctor's argument; this is the cheap test of it.
BATTLE_DUMMY_NATION = _env_int("FMO_BATTLE_DUMMY_NATION", "0")
#: VERIFIED:KEY: FMO_BATTLE_DUMMY_AI=<brain n> -- let the CLIENT run the enemy
#: (static decode). The client has a
#: per-unit KGR "brain" (resource 83285+n) that moves, targets and fires, and
#: the POP switches it on: client_kind 1 (an NPC, no peer stream), body+0x2C =
#: the UnitID of the client that runs it (the player's own battle id: owner ==
#: my id -> unit+0x1340 = 3 and a controller at unit+0xE75), body+0x118 = n.
#: Our dummy was popped 0/0/0 -- a remote player's copy -- which is why it
#: stood still. Real brains are n = 101..2295; 101 first. With it set the
#: dummy's nation defaults to the pilot's ENEMY (the body+0x7C friend/foe
#: byte) unless FMO_BATTLE_DUMMY_NATION says otherwise. 0 (default) = off.
#: Bar: the enemy moves by itself and the log shows cmd 24 naming its id;
#: then it fires (cmd 30), the player's HP drops, and shooting it sends cmd 29
#: with DIED -- which battle_kills pays.
BATTLE_DUMMY_AI = _env_int("FMO_BATTLE_DUMMY_AI", "0")
POP_AI_OWNER = 0x2C                    # u32 UnitID of the simulating client
POP_AI_BRAIN = 0x118                   # u32 brain n (KGR 83285 + n)
#: FMO_BATTLE_DUMMY_SIDE: the dummy's body+0x27 (fmoworld.POP_SIDE) -- the
#: byte both unit creators hand the unit ctor as its SIDE (unit+0x80), which
#: is what the friend/foe compares read. The self-POP sends 0 there, so any
#: non-zero value makes the dummy the other side; 1 is the plain choice. 0
#: (default) = unset, as before. This is the more likely of the two knobs to
#: move the dummy into ENEMY UNITS; arm ONE at a time so the screen says which.
BATTLE_DUMMY_SIDE = _env_int("FMO_BATTLE_DUMMY_SIDE", "0")
#: FMO_BATTLE_DUMMY_KILL: "<secs>[:<status>]" -- <secs> after the dummy is
#: popped, DESTROY it with a cmd-8 DEPOP of <status> on the battle self stream.
#: 0 / empty (default) = never. <status> defaults to 3 (REMOVE = the unit is
#: taken out of the scene and freed -- it VANISHES). WARNING: LIVE 2026-09-11: status
#: 2 (wreck/explosion, 0x611EEAB1 -> 0x610BE9B0) is gated on `[unit+0xE99] == 0`
#: (0x611F22A0) -- and that field is a pointer to the effect CURRENTLY attached
#: to the unit, which a dressed wanzer always has, so status 2 silently no-ops
#: (proved: sent + acked, no explosion; the enemy IS enrolled, count=2). Status
#: 3 has no such gate (only: char found by id + char+0x24 unit ptr set), so it
#: reliably removes the enemy. Both need the char alive/linked, i.e. the dummy
#: popped with FMO_BATTLE_DUMMY_CLIENT_KIND=0.
def _parse_dummy_kill(spec):
    spec = (spec or "").strip()
    if not spec:
        return 0.0, fmoworld.DEPOP_REMOVE
    secs, _, st = spec.partition(":")
    try:
        secs = float(secs)
    except ValueError:
        raise SystemExit(f"FMO_BATTLE_DUMMY_KILL={spec!r}: seconds is not a number")
    status = fmoworld.DEPOP_REMOVE
    if st:
        try:
            status = int(st, 0)
        except ValueError:
            raise SystemExit(f"FMO_BATTLE_DUMMY_KILL={spec!r}: status is not an int")
        if status not in fmoworld.DEPOP_STATUSES:
            raise SystemExit(f"FMO_BATTLE_DUMMY_KILL status {status} is not one of "
                             f"{fmoworld.DEPOP_STATUSES} (2=wreck, 3=remove)")
    return secs, status


BATTLE_DUMMY_KILL, BATTLE_DUMMY_KILL_STATUS = _parse_dummy_kill(
    os.environ.get("FMO_BATTLE_DUMMY_KILL", ""))


#: FMO_BATTLE_REFEREE: "<hits>[:<range>[:<cone_deg>]]" -- server-refereed
#: shoot-to-kill for the dummy enemy. The client never tells us the player
#: fired AT a target (the aim ray and lock stay local), but it DOES stream two
#: things we can judge from: cmd 240 (the player's position + facing) and cmd
#: 128 kind 3 offset >=0x14 (the OWN unit's weapon-slot wear, which climbs each
#: time a weapon is fired -- our "shot" event, seen live). So: each shot, if the
#: enemy is within <range> world units of the player (and, when <cone_deg> is
#: set, within that facing cone), counts as a HIT; after <hits> hits the enemy
#: is destroyed (cmd 8 DEPOP status 3) and the destroy objective is completed
#: -> FMO_BATTLE_END=objective wins the battle. WARNING: This is an APPROXIMATION --
#: geometry, not a real bullet -- because no client hit packet exists. Default
#: off. Pairs with FMO_BATTLE_DUMMY (the enemy) and, ideally, FMO_BATTLE_END
#: carrying 'objective'; turn FMO_BATTLE_DUMMY_KILL off so the timer does not
#: preempt the player.
def _parse_referee(spec):
    spec = (spec or "").strip()
    if not spec:
        return None
    parts = spec.split(":")
    try:
        hits = int(parts[0])
        rng = float(parts[1]) if len(parts) > 1 and parts[1] else 40.0
        cone = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    except ValueError:
        raise SystemExit(f"FMO_BATTLE_REFEREE={spec!r}: want <hits>[:<range>[:<cone_deg>]]")
    if hits < 1:
        raise SystemExit("FMO_BATTLE_REFEREE: <hits> must be >= 1")
    return {"hits": hits, "range": rng, "cone": cone}


REFEREE = _parse_referee(os.environ.get("FMO_BATTLE_REFEREE", ""))


def _xz_dist(a, b):
    """Distance in the xz plane between two (x,y,z[,w]) tuples."""
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _within_cone(player_pos, player_rot, target_pos, cone_deg):
    """True if `target_pos` lies within `cone_deg` of where `player_rot` faces.
    cone_deg <= 0 disables the check (always True). rot is the cmd-240 heading
    (radians-ish, MOVE_K_ROT-scaled); the bearing uses the same xz frame."""
    if cone_deg <= 0:
        return True
    import math as _m
    bearing = _m.atan2(target_pos[2] - player_pos[2], target_pos[0] - player_pos[0])
    d = abs((bearing - player_rot + _m.pi) % (2 * _m.pi) - _m.pi)
    return d <= _m.radians(cone_deg) / 2


def _parse_battle_dummy(spec):
    """`<uid>[:<utype>][@x,y,z]` -> (uid, utype, pos|None), or None when unset.

    Raises SystemExit on a malformed value: a probe that silently does not run
    is indistinguishable from one that ran and had no effect."""
    if not spec:
        return None
    body, _, posstr = spec.replace(" ", "").partition("@")
    idstr, _, typestr = body.partition(":")
    try:
        uid = int(idstr, 0)
    except ValueError:
        raise SystemExit(f"FMO_BATTLE_DUMMY={spec!r}: id {idstr!r} is not an "
                         f"integer")
    try:
        utype = int(typestr, 0) if typestr else 0
    except ValueError:
        raise SystemExit(f"FMO_BATTLE_DUMMY={spec!r}: type {typestr!r} is not "
                         f"an integer")
    pos = None
    if posstr:
        pos = tuple(float(x) for x in posstr.split(","))
        if len(pos) == 3:
            pos += (0.0,)
        if len(pos) != 4:
            raise SystemExit(f"FMO_BATTLE_DUMMY={spec!r}: @pos wants three or "
                             f"four floats")
    return uid, utype, pos


BATTLE_DUMMY = _parse_battle_dummy(BATTLE_DUMMY_SPEC)

POP_BATTLE_SPEC = os.environ.get("FMO_UDP_POP_BATTLE", "").strip()
try:
    POP_BATTLE = popsweep._parse_pop(POP_BATTLE_SPEC)
except ValueError:
    POP_BATTLE = None
    print(f"[fmo] WARNING: FMO_UDP_POP_BATTLE={POP_BATTLE_SPEC!r} is not "
          f"'<unitid>[:<unittype>]' -- THE BATTLE POP OVERRIDE IS OFF")
