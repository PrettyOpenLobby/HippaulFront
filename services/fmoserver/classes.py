"""The class table (0x014A +0x6AC): class levels and the experience curve."""
import os
import struct
from . import status


# --------------------------------------------------------------------------- #
# THE CLASS TABLE -- 0x014A payload+0x6AC -> lobby+0xF08 (static 2026-09-10,
# built the day the first server-side battle end drew "???" / Lv 0 for it)
# --------------------------------------------------------------------------- #
#: WARNING: LIVE 2026-09-10 13:08Z: the client's own "EXP Gain" result screen listed
#: seven classes as `???` at Lv 0. That screen, the Job Status page and the
#: 0x014C battle-end arm all read ONE 96-byte table at lobby+0xF08, which is
#: 0x014A payload+0x6AC (S14A_BLOCK2) -- and we served it as zeros.
#:
#: THE ROW (0x6117E484..0x6117E571, the 0x014C exp add; readers 0x611782B0 /
#: 0x6117AD00 scan the same way): 12 rows of 8 bytes,
#:     +0x00 u8  KIND     matched by every reader; 0 = "no such class" = ???
#:     +0x01 u8  LEVEL    written by 0x611E40A0 after an exp add
#:     +0x02 2B  (never read)
#:     +0x04 u32 EXP      the 0x014C rows ADD to it
#: The kinds are the client's own name tables at 0x61395C74 / 0x61395CA4
#: (systext 8:37..44 and 8:95..106; `0x611759B0(kind)` is the lookup and
#: returns "???" outside 1..12):
#:     1 Assault  2 Missileer  3 Mechanic  4 Recon  5 Sniper  6 Comms  7 Jammer
#:     8 Joker  9..11 Reserved  12 Pilot
#: The 0x014C arm's own 12-slot reset writes byte 0 = i+1 for i in 0..11, so
#: "all twelve kinds, in order" is the shape the client itself builds.
#:
#: THE LEVEL is not ours to invent: 0x611E40A0(group, class, exp) computes it
#: from the exp curve in Data/AI/F32/D15.DAT (resource 0x1450F, plaintext; the
#: same file is the 48-row RANK LADDER -- the build step parses both
#: into fmodata/fmo-class-exp.tsv and fmo-ranks.tsv). Level 1 is the floor;
#: level L in 2..100 is the largest with curve[L-1] <= exp (Lv2 = 82,800). The
#: four rank groups and eight classes in that file share the curve above level
#: 1, so one 100-entry curve is the whole game, and class_level() below IS the
#: client's function. `group` = byte[lobby+0x8B5] - 1 = 0x014A payload+0x29,
#: which we served as 0: an exp add would then index the table at group -1 --
#: off its front, into the rank rows. FMO_RANK_GROUP serves 1 alongside the
#: table (all four groups are identical bytes, so 1 is safe for the exp math;
#: WARNING: what ELSE reads +0x8B5 "all over the UI" is still unread).
#:
#: Per-character exp lives in the store under `class_exp` ({kind: exp}, a dict,
#: so fmostore keeps it in the JSON `extra` blob with no schema change -- same
#: as play_seconds). A pilot with none reads as exp 0 / level 1 in every class,
#: which is the honest state of a fresh pilot, not "???".
S14A_CLASS_TABLE = status.S14A_BLOCK2
S14A_RANK_GROUP = status.S14A_B8B5
CLASS_ROWS = 12
CLASS_ROW_LEN = 8
CLASS_KIND = 0x00
CLASS_LEVEL = 0x01
CLASS_EXP = 0x04
CLASS_NAMES = {1: "Assault", 2: "Missileer", 3: "Mechanic", 4: "Recon",
               5: "Sniper", 6: "Comms", 7: "Jammer", 8: "Joker", 9: "Reserved",
               10: "Reserved", 11: "Reserved", 12: "Pilot"}
#: FMO_CLASS_TABLE: '1' (default) = serve the 12 kinds with each pilot's stored
#: exp and the level the curve gives it. '0' = the old 96 zeros (??? / Lv 0).
CLASS_TABLE = (os.environ.get("FMO_CLASS_TABLE", "").strip() or "1") != "0"


def trained(flags):
    """SE's 'recognised as a soldier': progress byte 128 == 99, the byte the
    sergeant's event 205 sets (fmo-events.tsv)."""
    return flags is not None and len(flags) > 128 and flags[128] == 99


def load_class_curve(path=None):
    """The 100 level thresholds from fmodata/fmo-class-exp.tsv, or () when the
    file is missing -- then every level reads as 1, and the 0x014A log says so."""
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "fmodata", "fmo-class-exp.tsv")
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [ln.rstrip("\n").split("\t") for ln in fh if ln.strip()]
    except OSError:
        return ()
    curve = []
    for lv, exp in rows[1:]:
        curve.append(int(exp))
    if any(curve[i] > curve[i + 1] for i in range(len(curve) - 1)):
        return ()                       # a curve that is not monotonic is not a curve
    return tuple(curve)


CLASS_CURVE = load_class_curve()


def class_level(exp, curve=None):
    """0x611E40A0's answer: 1 at the floor, else the largest L (<= 100) with
    curve[L-1] <= exp."""
    curve = CLASS_CURVE if curve is None else curve
    exp = int(exp)
    lo, hi = 1, len(curve)
    if hi < 2 or exp < curve[1]:
        return 1
    while lo < hi:                      # invariant: curve[lo-1] <= exp
        mid = (lo + hi + 1) // 2
        if curve[mid - 1] <= exp:
            lo = mid
        else:
            hi = mid - 1
    return lo


def class_exp_of(char):
    """{kind: exp} from a character record's `class_exp` (keys may be str)."""
    out = {}
    for k, v in ((char or {}).get("class_exp") or {}).items():
        try:
            k, v = int(k), int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= k <= CLASS_ROWS and v >= 0:
            out[k] = v
    return out


def class_table_block(char=None, curve=None):
    """The 96-byte lobby+0xF08 table: kinds 1..12 in order, each with the
    pilot's exp and the level the curve gives it."""
    exp = class_exp_of(char)
    b = bytearray(CLASS_ROWS * CLASS_ROW_LEN)
    for i in range(CLASS_ROWS):
        kind = i + 1
        at = i * CLASS_ROW_LEN
        e = exp.get(kind, 0)
        b[at + CLASS_KIND] = kind
        b[at + CLASS_LEVEL] = class_level(e, curve) & 0xFF
        struct.pack_into("<I", b, at + CLASS_EXP, e & 0xFFFFFFFF)
    return bytes(b)


def class_table_summary(char=None, curve=None):
    exp = class_exp_of(char)
    return ", ".join(f"{CLASS_NAMES[k]} Lv{class_level(exp.get(k, 0), curve)}"
                     + (f"/{exp[k]}xp" if exp.get(k) else "")
                     for k in range(1, CLASS_ROWS + 1) if k <= 8 or k == 12)
