"""The pilot's hangar: its place, its bays and the wanzers parked in them."""
import os
from .deps import fmolayout
from .knobs import _env_int


def hangar_request(payload):
    """(password, first, last) from a 0x016D the HANGAR menu sent: +0x08 is the
    numeric password as a decimal string, +0x18/+0x29 the owner's names
    (empty for My Hangar). Pure."""
    if len(payload) < move.MOVE_REQ_LEN:
        return "", "", ""
    num = payload[move.M16D_NUMBER:move.M16D_NUMBER + 16].split(b"\0")[0].decode("ascii", "replace")
    a = payload[move.M16D_NAME_A:move.M16D_NAME_A + move.MOVE_NAME_LEN].split(b"\0")[0].decode("cp932", "replace")
    b = payload[move.M16D_NAME_B:move.M16D_NAME_B + move.MOVE_NAME_LEN].split(b"\0")[0].decode("cp932", "replace")
    return num, a, b


def hangar_place(owner_id):
    """A pilot's hangar is one place for everyone: zone 0 (hangars are not in
    an area), kind 5, instance = the owner's character id."""
    return (0, 5, int(owner_id))


#: VERIFIED:KEY: THE HANGAR IS AN OWNERSHIP CHECK (static 2026-09-11, live report: "both
#: pilot lockers immediately close dialogue"). The SETUP.CONSOLE / PILOT.LOCKER
#: rows run AH/F98/D63 `tag_wapsetup` / `tag_pltsetup`, whose natives are
#: E307 (0x610FB270 -> scene 8, wanzer setup) and E308 (0x610FB2E0 -> scene 9,
#: pilot setup). Both open ONLY when
#:     globals+0x1AC  ==  [[0x613CA470]+0x48]+0x10      (the self peer's UnitID)
#: and 0x61002BC0 passes; 0x61002BC0, when globals+0x1AC == globals+0x1BC (the
#: client's own id), demands that the entity map hold 0x82080D00 (the locker),
#: 0x82080C00 AND 0x82080C01 (both setup consoles), and a unit 0x82080000+i
#: for every IN-USE setup i (0x611747D0 = the setup's +0x01 byte, the same
#: 8 x 544 array this server stores and serves in 0x0166). So SE's grant put
#: the hangar OWNER'S id in 0x0153 +0x18 (globals+0x1AC), not a kind; the
#: owner could use their own consoles and a visitor could not; and SE's server
#: popped the owner's wanzers into the bays. We sent 5 ("Hangar" in the HUD's
#: kind-name enum), which fails the first compare -- the dialogue just closes.
#: WARNING: Our character ids are small (1 = your first pilot) where SE's were not, so
#: the HUD's kind-name switch (>4 = Hangar) reads an owner id of 1 as "Room".
#: A visitor whose own id equals the owner's would pass as the owner; they get
#: 5 instead. FMO_HANGAR_OWNER_F18=0 restores the flat 5.
HANGAR_OWNER_F18 = (os.environ.get("FMO_HANGAR_OWNER_F18", "").strip() or "1") != "0"
#: host -> {"owner", "slots", "mapno"}: the in-use wanzer setups of the pilot
#: now standing in their OWN hangar, recorded at the grant (the TCP session
#: owns the garage block; the UDP channel pops the bay units from this).
HANGAR_RESIDENTS = {}
#: the bay prop the four hangar maps repeat on a 20 m pitch (141 = 2 bays ..
#: 144 = 5), from the shipped floor plans: 5.4 x 3.2 x 9.5 m
HANGAR_BAY_SIZE = (5.4, 3.2, 9.5)
#: what a bay unit is popped as. The gate only asks that it EXISTS.
#: WARNING: NOT 30: a type-30 unit loads the model named by its KEY and is not
#: created at all when there is none (0x611EA980 returns 0 on a failed load),
#: and a wanzer is built from parts, not stored as one model -- so 0x82080000
#: as type 30 almost certainly never existed (live 18:07Z: all four units
#: sent, grant +0x18 = 1, consoles still shut). 4 always creates; it is parked
#: HANGAR_BAY_DEPTH metres under the bay so nobody sees a person there.
#: The faithful answer is SE's parked wanzer (a wanzer-class pop with the
#: setup's parts) -- a lobby-scene wanzer pop is untested, hence not default.
HANGAR_BAY_TYPE = _env_int("FMO_HANGAR_BAY_TYPE", "4")
HANGAR_BAY_DEPTH = float(os.environ.get("FMO_HANGAR_BAY_DEPTH", "").strip() or "0")
#: KEY: AND THE BAY UNIT'S BODY IS WHAT THE SETUP SCREEN SHOWS (static + live
#: 2026-09-11). The NPC arm 0x61100100 walks 0x82080000..07 (0x611009C6),
#: copies each found unit's whole 456-byte body (entity+0x147) -- POSITION
#: INCLUDED -- and creates a display unit 0x820C2000|n from it. Live: SETUP.
#: CONSOLE opened the wanzer setup scene and the wanzer sat "right up against
#: the camera" -- the copy of a unit we had parked 30 m under the floor. So
#: SE's bay unit stood ON the platform; depth is 0 now, and a hangar-band
#: layout row with the same key (the editor) overrides the computed spot.


def setups_in_use(block):
    """[i] for every wanzer setup whose +0x01 IN-USE byte is set (0x611747D0's
    own test). Pure."""
    return [i for i in range(inventory.SETUP_SLOTS)
            if len(block) >= (i + 1) * inventory.SETUP_ENTRY_LEN and block[i * inventory.SETUP_ENTRY_LEN + 1]]


def hangar_bays(mapno):
    """[(x, z)] bay centres for a hangar map, nearest the entrance (high z)
    first, from the shipped floor plan; [] when none ships. WARNING: Which setup SE
    parked in which bay is not known -- setup i takes the i-th bay."""
    if not fmolayout:
        return []
    plan = fmolayout.load_plan(mapno)
    if not plan:
        return []
    w, h, d = HANGAR_BAY_SIZE
    bays = {(round((b[0] + b[3]) / 2, 2), round((b[2] + b[5]) / 2, 2)) for b in plan["boxes"]
            if abs((b[3] - b[0]) - w) < 0.2 and abs((b[4] - b[1]) - h) < 0.3
            and abs((b[5] - b[2]) - d) < 0.2}
    return sorted(bays, key=lambda c: -c[1])


def hangar_resident_units(host_ip, place):
    """The bay units 0x82080000+i for the owner standing in their own hangar,
    as roster entries (uid, type, pos4, None); [] otherwise."""
    r = HANGAR_RESIDENTS.get(host_ip)
    if not r or place is None or place[1] != 5 or place[2] != r["owner"]:
        return []
    bays = hangar_bays(r["mapno"])
    pp = popsweep.POP_POS_MAP.get(r["mapno"])
    fy = pp[1] if pp else 0.0
    out = []
    for k, i in enumerate(r["slots"]):
        if k >= len(bays):
            break
        x, z = bays[k]
        y = fy - (HANGAR_BAY_DEPTH if HANGAR_BAY_TYPE == 4 else 0.0)
        out.append((0x82080000 + i, HANGAR_BAY_TYPE, (x, y, z, 0.0), None))
    return out


#: the entities 0x61002BC0 demands before the owner's consoles open
HANGAR_REQUIRED_KEYS = (0x82080D00, 0x82080C00, 0x82080C01)


def hangar_owner_password(host):
    """The stored hangar password of the character `host` is playing, or None."""
    n1, n2, _src = popnames.pop_names_for(host)
    for c in charstore.load_roster(identity.account_for(host)):
        if (c.get("first", "").strip().lower(), c.get("last", "").strip().lower()) == \
                (n1.strip().lower(), n2.strip().lower()):
            return c.get("hangar_password") or ""
    return None


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, identity, inventory, move, popnames, popsweep  # noqa: E402
