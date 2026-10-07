#!/usr/bin/env python3
"""fmogates.py -- every gate FMO's client uses to pick a story scene, an NPC
conversation or an unlock, read out of the client's own data and scripts.

    python fmogates.py --client <install> [--out DIR]    # writes fmo-gates.json
    python fmogates.py --client <install> --check        # the counts only

The output is the contract of the GM gate tool (services/fmogates.py): the
flag block (bytes and bits) with every reader and writer found, the NPC event
table rows in the order the client walks them, the flag tests inside every
SCP script, the gates that are not flags (pilot level, zone, nation, the
visited bit of a lobby entry script), and the campaign ladder per faction.

SOURCES, each named in the output rows:

  * AI/F08/D15.DAT, the LEV zone table: (zone kind range, nation) -> event
    table + lobby entry script + the bit that says it already played.
  * AI/F08/D39..D48.DAT (+ D16), the NPC event tables: 0x54-byte rows reached
    through the container's offset table (never a fixed stride).
  * every SCP in 0x8000..0x80FF and 0xA000..0xA0FF that the data references,
    run through a small symbolic interpreter of the SCP VM (below).
  * fmo-missions.tsv / fmo-cutscenes.tsv (fmoprogression.py) for the mission
    catalogue join and the byte -> mission assignments it already made.
  * the English dialogue as installed (the FMDT patch), for scene labels.

CLIENT CODE FACTS (manual RE of FrontMissionOnline.dll.unpacked with
work/pc/fmodis.py) are in CLIENT_FACTS below, each with its address. They are
what makes the data readable, so they are stated once, here:

  * the event-row walk 0x610ED240(key, kind): rows through the container's
    offset table, first row whose key AND kind match AND whose gate
    0x610ECAD0 passes wins; a winning row with an empty name or SCP 0 runs
    nothing.
  * the gate 0x610ECAD0: +0x08/+0x0C is a PILOT LEVEL window (lo/hi swapped if
    lo > hi; lo == 0 disables it) compared against 0x611782B0 = the level of
    the class-12 ("Pilot") row of the class table at lobby+0xF08, computed
    from its EXP by the D15 curve. It is NOT the rank byte lobby+0x8BB.
    +0x10/+0x14: flag BIT ids that must be SET (0 = no test), read by
    0x610F95B0 = byte[id>>3] & (1 << (id&7)) of the kind-11 block.
    +0x18/+0x1C and +0x20/+0x24: byte index / value pairs, 0x610F95F0 =
    byte[idx] of the same block, compared == (value & 0xFF); index 0 = no test.
  * the kind-11 block is lobby+0x8C8+0x2C0 = lobby+0xB88, 256 bytes
    (0x611A37A0 case 11, byte mode). Bits and bytes are ONE block: bit id n
    lives in byte n>>3, so bit ids 0..1023 overlap bytes 0..127 and the
    mission bytes 128..255 are bits 1024..2047.
  * the SCP a row names: 0x8xxx -> resource 0x137F7 + 2*(id & 0xFFF), its
    MSG container the next resource; 0xAxxx -> 0x13BAF + 2*(id & 0xFFF)
    (0x610F5E8A..0x610F5EB9).
  * the event table a zone uses (0x610ECE3C..0x610ECE64): the first LEV row
    whose kind range holds MapKind and whose nation is the pilot's or 0;
    table = resource 0x13BC7 + (row id & 0xFFF), and only ids below 0x13BD1
    are loaded, so LEV rows 0x900A/0x900B (the debug lobbies) never are.

WARNING: STATIC. Nothing in this file proves a scene PLAYS; it proves what the
client tests. Confidence per row: `proved` = read off client code or a table
with no interpretation, `table` = a join of two independent tables,
`inferred` = a join that needed a rule or a dialogue reading, `unknown`.
"""
import argparse
import collections
import datetime
import hashlib
import io
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fmofile                                                     # noqa: E402
import fmofmdt                                                     # noqa: E402
import fmofmdtwrite as W                                           # noqa: E402
import fmoscriptcast as SC                                         # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_CLIENT = r"C:/Program Files (x86)/PlayOnline/SquareEnix/FRONT MISSION ONLINE"
POL = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, os.pardir, os.pardir))
DATA_DIR = os.path.join(POL, "private", "gamedata", "hippaulfront", "services", "fmodata")
PUBLIC_DATA = os.path.join(POL, "public", "hippaulfront", "services", "fmodata")
OUT_NAME = "fmo-gates.json"
GENERATOR = "tools/fmodatagen/fmogates.py"

LEV = "AI/F08/D15.DAT"
EVT_BASE = 0x13BC7          # event table = EVT_BASE + (LEV row id & 0xFFF)
EVT_LIMIT = 0x13BD1         # 0x610ECE4F: only ids below this are loaded
EXTRA_TABLES = ("AI/F08/D16.DAT",)   # kind-4 benchmark rows; no LEV row names it
NATION = {1: "O.C.U.", 2: "U.S.N.", 0: None}

# --------------------------------------------------------------------------- #
# the client-code facts this generator relies on (manual RE, fmodis)
# --------------------------------------------------------------------------- #
#: Each is a fact about the CLIENT, with the address it was read at. They are
#: emitted into other_gates / flags so the tool can show where a gate lives.
CLIENT_FACTS = {
    "event_row_walk": "0x610ED240: rows via the container offset table, first row with "
                      "key+kind match whose gate 0x610ECAD0 passes wins",
    "row_gate": "0x610ECAD0",
    "pilot_level": "0x611782B0: class-12 row of lobby+0xF08, level from its exp "
                   "(0x611E41A0 -> 0x611E40A0, the D15 curve)",
    "flag_bit": "0x610F95B0: byte[id>>3] & mask[id&7] (mask table 0x61335138 = 01 02 04 .. 80)",
    "flag_byte": "0x610F95F0: byte[idx] (0x611750F0 -> 0x611A3950 kind 11)",
    "flag_block": "0x611A37A0 case 11: lobby+0x8C8+0x2C0 = lobby+0xB88, 0x100 bytes, byte mode",
    "lev_walk": "0x610ED040(MapKind, nation=lobby+0x8B4 via 0x61016F00): first LEV row with "
                "klo<=MapKind<=khi and nation equal or 0; its +0x10 bit SET -> no entry script",
    "sally_walk": "0x610ED170: same row match, runs the +0x34 'LobbySally' entry, no bit test",
    "table_select": "0x610ECE3C: event table = 0x13BC7 + (LEV row id & 0xFFF), loaded only "
                    "when below 0x13BD1",
    "scp_resource": "0x610F5E8A: 0x8xxx -> 0x137F7+2k (MSG +1); 0xAxxx -> 0x13BAF+2k",
    "kind4_map": "0x610F70EA: a kind-4 row's +0x4C, when non-zero, is the map loaded for the "
                 "scene (0x610F16E0(1, map)); else lobby+0x6A22",
    "registered_ui": "0x6109F434: byte[128] == 99 (0x611750F0(0x80), cmp 0x63) sets "
                     "[obj+0x60] of a UI object built there",
    "flag_writer_016b": "0x61178865: the 0x016B acquire-reply arm sets ONE BIT of an owned "
                        "block kind reply+0x20 at index reply+0x21 (0x611A39F0, set=1); the "
                        "only client writer of the owned block found",
    "flag_copy_015a": "0x6117E987: the 0x015A result push copies payload+0x598 (1344 B) over "
                      "lobby+0x8C8, which contains the flag block",
    "flag_copy_014a": "0x014A login: payload+0x3C slice -> lobby+0x8C8 (flag block at +0x304 "
                      "of the payload, status.S14A_FLAGS11)",
}

#: The script natives (syscall ids) that read player state, decoded from the
#: dispatcher group table 0x61334DC8 (group E060 -> handler table 0x613BB0B8).
#: The result lands in R0 (ctx+0xA) in every one of them.
NATIVES = {
    0xE060: ("nation", "0x610F9370: byte[self entity+0x1C3], 1 O.C.U. / 2 U.S.N. (proved live 09-05)"),
    0xE061: ("active_class", "0x610F93C0: byte[lobby+0x3669]"),
    0xE062: ("self_attr_1c1", "0x610F9410: byte[self entity+0x1C1] & 0xF"),
    0xE063: ("self_attr_12e", "0x610F9460: byte[self entity+0x12E] mapped through a switch"),
    0xE064: ("mapkind", "0x610F9580: globals+0x1A4, the zone id the pilot is in"),
    0xE065: ("class_level", "0x610FD6C0: level of class p0 in lobby+0xF08"),
    0xE066: ("flag_bit", "0x610FD7D0: bit p0 of the kind-11 block"),
    0xE067: ("flag_byte", "0x610FD860: byte p0 of the kind-11 block"),
    0xE068: ("owned", "0x610FD8E0: owned block kind p0 (2..12), index p1; kind 11 = the flag "
                      "byte, kind 10 = the zone permit byte"),
    0xE06B: ("status_record", "0x610F9750: fills the window: +0x10 nation (lobby+0x8B4), "
                              "+0x13 rank byte (lobby+0x8BB), +0x1C contribution, +0x20 money"),
    0xE06D: ("pilot_level", "0x610F99A0: 0x611782B0, the same pilot level the event rows test"),
    0xE06F: ("contribution", "0x610F99E0: lobby+0xFC8 as a float"),
}
READ_NATIVES = {k for k in NATIVES}
SRV_CALL, SRV_POLL, MSG_CALL = 0xE220, 0xE222, 0xE210

#: The identical stub that 162 SCP ids ship as (md5 of the raw FMDT file).
STUB_SIZE, STUB_MD5 = 32292, "98f84e2447f3521970ff1e2d05feb647"

#: INFERRED server-call semantics (what SE's server did is not in the client):
#: a script that tests bit N and, when it is clear, calls 200 [N] or 205 [N]
#: (0x800A bit 32 / 200 [32]; 0x8078 bit 57 / 205 [57]; 0x807E bit 67 /
#: 205 [67]; 0x804E bit 112 / 205 [112]) is asking the server to SET bit N so
#: the "first time" branch does not repeat. Each returns (kind, index, value).
#: 201 is NOT a byte-128 write (corrected 2026-10-01): the First Sergeant
#: (0x8074 0x6226..0x6314) sends 201 [active job 1..3] right before 104 [128]
#: on byte 128 == 0, and his own switch only knows 0/1/2/99 (0x4ffe..0x5016),
#: so a job number in byte 128 would strand Mechanics. Byte 128 moves like a
#: mission byte: 104 steps it 0 -> 1 (training sortie) and 1 -> 2 (the
#: training-success scene 0x8084), 105 reports it 2 -> 99.
SRV_WRITES = {
    200: lambda p: ("bit", p, 1),
    205: lambda p: ("bit", p, 1),
}
SRV_EVENT_NOTES = {
    101: "query at lobby entry, no params; the script tests answer p1 != 0 (helper library)",
    104: "query with a mission byte index; on byte 0 after an offer scene (accept), from "
         "LobbySally when byte==1 and the chosen sortie tile is one of that mission's tiles, "
         "and with 128 from the sergeant (0 -> 1) and the training-success scene (1 -> 2)",
    105: "query with a mission byte index on byte 3 after the clear scene (128: on 2, the "
         "sergeant's 'cleared to sortie'); answer p2 != 0 runs the reward lines for that "
         "byte (see rewards)",
    200: "notify, one arg; inferred: set bit p1 (Map Selector 200 [16], training 200 [32])",
    201: "notify, one arg: the training job (1 Assault, 2 Missiler, 3 Mechanic) the First "
         "Sergeant sends before 104 [128]; not a flag write",
    204: "notify, one arg",
    205: "notify, one arg; inferred: set bit p1 (205 [34] sergeant, [42] [48] [56] [57] "
         "[65]..[67] [112] after a clear-bit test). fmo-events.tsv row '205 * 128=99' "
         "fires on ALL of these",
    206: "notify (hangar scripts)",
    211: "notify (training ground)",
}

#: catalogue area -> the zone-id base its sectors live in ("Ctrl.0N" in an
#: O.C.U. Controlled Zone mission = zone 100+N-1). Checked by the tile join
#: itself: Annihilation's Ctrl.01:S12/Ctrl.02:S16/Ctrl.03:S18 = zones
#: 100/101/102 rows 12/16/18 of the ARE sector table.
AREA_BASE = {"O.C.U. Controlled Zone": 100, "O.C.U. Occupied Zone": 200,
             "U.S.N. Controlled Zone": 300, "U.S.N. Occupied Zone": 400,
             "Frontline Zone": 500}
ARE_TSV = os.path.normpath(os.path.join(POL, os.pardir, "PlayOnline", "work", "fmo",
                                        "fmo-are-sectors.tsv"))

# --------------------------------------------------------------------------- #
# containers
# --------------------------------------------------------------------------- #


def container_rows(dec):
    """[(offset, length)] of section 0's rows: count at +0x46, offset table
    at +0x48 (0x610F4F70 / 0x610F4E10: row = table + ptr[i])."""
    nsec = struct.unpack_from("<I", dec, 0x40)[0]
    if nsec < 1:
        return []
    _typ, cnt, off = struct.unpack_from("<HHI", dec, 0x44)
    rows = [off + struct.unpack_from("<I", dec, off + 4 * i)[0] for i in range(cnt)]
    return [(r, (rows[i + 1] if i + 1 < cnt else len(dec)) - r) for i, r in enumerate(rows)]


def cstr(b):
    return b.split(b"\0")[0].decode("latin1")


def lev_table(client):
    """The LEV rows in file order, each field decoded."""
    dec = fmofmdt.load(client, LEV)
    out = []
    for i, (r, _ln) in enumerate(container_rows(dec)):
        klo, khi, nat, row, scp, bit = struct.unpack_from("<HHIIII", dec, r)
        idx = EVT_BASE + (row & 0xFFF)
        rel = fmofile.rel_of(idx)
        out.append(dict(order=i, offset=r, kind_lo=klo, kind_hi=khi, nation=nat,
                        table_id=row, table=rel, table_loaded=idx < EVT_LIMIT and
                        os.path.exists(fmofile.data_path(client, rel)),
                        scp=scp, visited_bit=bit,
                        entry=cstr(dec[r + 0x14:r + 0x34]),
                        sally=cstr(dec[r + 0x34:r + 0x54])))
    return out


ROW_FIELDS = (
    ("+0x00", "entity key (u32): the talk target, or the mission/scene id for kind 4"),
    ("+0x04", "kind (u32): 1 NPC talk, 4 mission scene (scene 5), 5 mission clear, 6 accept list"),
    ("+0x08", "pilot level low (u32); 0 = no level test (0x610ECB0C)"),
    ("+0x0C", "pilot level high (u32); swapped with low when low > high"),
    ("+0x10", "flag bit id that must be SET (u32); 0 = no test"),
    ("+0x14", "second flag bit id that must be SET (u32); 0 = no test (never non-zero in the data)"),
    ("+0x18", "flag byte index (u32); 0 = no test"),
    ("+0x1C", "required value of that byte (u32, compared & 0xFF, ==)"),
    ("+0x20", "second flag byte index (u32); 0 = no test"),
    ("+0x24", "required value of the second byte (u32, ==)"),
    ("+0x28", "SCP id (u32); 0 = the row runs nothing"),
    ("+0x2C", "entry name (32 bytes, NUL padded): the SCP entry that runs; empty = runs nothing"),
    ("+0x4C", "u32 stored at record+0x10 (0x610ECC32); a kind-4 row's MAP to load (0x610F70EA). "
              "Observed 0, 47, 70, 266 (kind 4) and 600/601 (kind 1 tag_parsonnel in D44, use unknown)"),
    ("+0x50", "u32 stored at record+0x14 (0x610ECC38); no reader found. Observed 0, 0x600, 0x6FF"),
)


def event_table(client, rel):
    dec = fmofmdt.load(client, rel)
    rows = []
    for i, (r, ln) in enumerate(container_rows(dec)):
        key, kind, lo, hi, f1, f2, b1, v1, b2, v2, scp = struct.unpack_from("<11I", dec, r)
        x4c, x50 = struct.unpack_from("<II", dec, r + 0x4C)
        rows.append(dict(order=i, offset=r, length=ln, key=key, kind=kind, lo=lo, hi=hi,
                         bits=[f1, f2], tests=[(b1, v1), (b2, v2)], scp=scp,
                         entry=cstr(dec[r + 0x2C:r + 0x4C]), x4c=x4c, x50=x50,
                         raw=dec[r:r + 0x54].hex()))
    return rows


def scp_resources(scp):
    k = scp & 0xFFF
    if (scp & 0xF000) == 0xA000:
        idx = 0x13BAF + 2 * k
    else:
        idx = 0x137F7 + 2 * k
    return fmofile.rel_of(idx), fmofile.rel_of(idx + 1)


# --------------------------------------------------------------------------- #
# the SCP VM (0x61110930), as a symbolic interpreter
# --------------------------------------------------------------------------- #
#: Instruction word: op = w >> 10, f1 = (w >> 5) & 31, f2 = w & 31. Extra
#: words per field (0x6110FDD0): f < 13 none, 13..23 one u16, >= 24 two
#: (a u32); op 42 none at all, op 46 exactly one. Operand modes (0x6110FE90):
#:   0/1 the constants 0/1; 2,3,4,5,9 registers R0..R4 (ctx+0x0A,+0E,+12,+16,+1A)
#:   6/7/8 byte/word/dword [R3+R2]; 10/11/12 [R4+R2]; 13/14/15 [R3+imm];
#:   16/17/18 [R4+imm]; 19 PC(after the insn)+s16 (a code address); 20 imm16;
#:   21/22/23 byte/word/dword [imm16]; 24/25 u32.
#: Opcodes: 1 MOV f1->f2; 2 LEA (memory modes yield the address, 0x6110FFA8);
#: 3 PUSH f1; 4 POP ->f2; 5..18 arithmetic into f2 (not modelled); 19 CMP f1,f2
#: (0x61110490: EQ when f2 == f1, LT when f2 < f1); 20 BEQ, 21 BNE, 22 BLT,
#: 23 BLE, 24 BGT, 25 BGE (on f2 relative to f1, target in f1); 26 JMP f1;
#: 27 CALL f1; 28 RET (0x6111137E); 29..35 R0 = EQ, NE, LT, GE, LE, GT, GE;
#: 36 ARG[f2] = f1; 37 f2 = ARG[f1]; 42 JMPREL +(w & 0x3FF); 43 SYSCALL f1
#: with the parameter window in R4 (0x610F653F) and the result in R0;
#: 45 YIELD. Code handles are 0x08000000 | offset from the code base, which is
#: the u32 at header +0x7C (checked: CALL targets land after a RET in 87/102,
#: 129/135, 42/45, 216/227 of four scripts at that base and nowhere else).
REGS = {2: 0, 3: 1, 4: 2, 5: 3, 9: 4}
MEMSIZE = {6: 1, 7: 2, 8: 4, 10: 1, 11: 2, 12: 4, 13: 1, 14: 2, 15: 4,
           16: 1, 17: 2, 18: 4, 21: 1, 22: 2, 23: 4}
BRANCH = {20: "==", 21: "!=", 22: "<", 23: "<=", 24: ">", 25: ">="}
SETCC = {29: "==", 30: "!=", 31: "<", 32: ">=", 33: "<=", 34: ">", 35: ">="}
FLIP = {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
NEG = {"==": "!=", "!=": "==", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}
TERMINAL = {0, 26, 28, 42}


def nwords(f):
    return 0 if f < 13 else (1 if f < 24 else 2)


class Insn(object):
    __slots__ = ("off", "ln", "op", "f1", "f2", "v1", "v2", "word")

    def __init__(self, code, off):
        w = SC.u16(code, off)
        self.word = w
        self.off = off
        self.op, self.f1, self.f2 = w >> 10, (w >> 5) & 31, w & 31
        p = off + 2
        vals = []
        if self.op == 42:
            vals = [w & 0x3FF, None]
        elif self.op == 46:
            vals = [SC.u16(code, p), None]
            p += 2
        else:
            for f in (self.f1, self.f2):
                n = nwords(f)
                if n == 0:
                    vals.append(None)
                elif n == 1:
                    vals.append(SC.u16(code, p))
                    p += 2
                else:
                    vals.append(SC.u16(code, p) | (SC.u16(code, p + 2) << 16))
                    p += 4
        self.v1, self.v2 = vals
        self.ln = p - off


class Scp(object):
    """One decoded SCP: header, entries, code, and the per-offset insns."""

    def __init__(self, client, rel):
        self.rel = rel
        self.dec = SC.load_scp(client, rel)
        d = self.dec
        if d[:4] != b"SCP\0":
            raise ValueError("%s is not an SCP" % rel)
        self.base = struct.unpack_from("<I", d, 0x7C)[0]
        self.size = struct.unpack_from("<I", d, 0x80)[0]
        self.code = d[self.base:self.base + self.size]
        et = struct.unpack_from("<I", d, 0x70)[0]
        n = struct.unpack_from("<I", d, 0x78)[0]
        self.entries = collections.OrderedDict()
        for i in range(n):
            _a, nl, _h, no, h = struct.unpack_from("<BBHII", d, et + 12 * i)
            self.entries[cstr(d[et + no:et + no + nl])] = h & 0xFFFFF
        self._insn = {}

    def insn(self, off):
        i = self._insn.get(off)
        if i is None:
            if off < 0 or off + 2 > len(self.code):
                return None
            i = self._insn[off] = Insn(self.code, off)
        return i

    def branch_target(self, ins):
        """The code offset a branch/jump/call goes to, or None if computed."""
        f, v = ins.f1, ins.v1
        if ins.op == 42:
            return ins.off + ins.ln + v
        if f == 19:
            s = v - 0x10000 if v & 0x8000 else v
            return (ins.off + ins.ln + s) & 0xFFFFF
        if f in (24, 25):
            return v & 0xFFFFF
        if f == 20:
            return v
        return None


def sym_str(s):
    if isinstance(s, int):
        return str(s)
    return repr(s)


class State(object):
    __slots__ = ("r", "m", "a", "st", "cmp")

    def __init__(self):
        self.r = [None] * 5
        self.m = {}
        self.a = {}
        self.st = []
        self.cmp = None

    def copy(self):
        s = State()
        s.r = list(self.r)
        s.m = dict(self.m)
        s.a = dict(self.a)
        s.st = list(self.st) if self.st is not None else None
        s.cmp = self.cmp
        return s

    def meet(self, o):
        """Keep only what both agree on. -> changed?"""
        ch = False
        for i in range(5):
            if self.r[i] != o.r[i] and self.r[i] is not None:
                self.r[i] = None
                ch = True
        for k in list(self.m):
            if o.m.get(k) != self.m[k]:
                del self.m[k]
                ch = True
        for k in list(self.a):
            if o.a.get(k) != self.a[k]:
                del self.a[k]
                ch = True
        if self.st != o.st and self.st is not None:
            self.st = None
            ch = True
        if self.cmp != o.cmp and self.cmp is not None:
            self.cmp = None
            ch = True
        return ch


def is_sym(v):
    return isinstance(v, tuple)


def s32(v):
    return v - 0x100000000 if isinstance(v, int) and v >= 0x80000000 else v


def vm_int(v):
    """A script integer as the natives read it: bit 30 set = a negative int
    (the handlers `or 0x80000000`), bit 31 = a float stored >> 1."""
    if not isinstance(v, int):
        return v
    if v & 0x80000000:
        f = struct.unpack("<f", struct.pack("<I", (v << 1) & 0xFFFFFFFF))[0]
        return int(f) if f == int(f) else f
    if v & 0x40000000:
        return s32(v | 0x80000000)
    return v


class Analyzer(object):
    """Symbolic execution of one SCP, function by function."""

    MAX_INLINE = 80

    def __init__(self, scp):
        self.scp = scp
        self.funcs = {}          # entry -> set(insn offsets)
        self.callers = collections.defaultdict(set)
        self.reads = []          # dicts
        self.calls = []          # server calls
        self.natives = []        # other state natives
        self.msgs = {}           # off -> E210 params
        self.unresolved = collections.Counter()

    # ---- operand access ------------------------------------------------- #
    def addr(self, st, f, v):
        if f in (21, 22, 23):
            return v
        if f in (13, 14, 15):
            b = st.r[3]
            return b + v if isinstance(b, int) else None
        if f in (16, 17, 18):
            b = st.r[4]
            return b + v if isinstance(b, int) else None
        if f in (6, 7, 8):
            b, i = st.r[3], st.r[2]
            return b + i if isinstance(b, int) and isinstance(i, int) else None
        if f in (10, 11, 12):
            b, i = st.r[4], st.r[2]
            return b + i if isinstance(b, int) and isinstance(i, int) else None
        return None

    def get(self, st, ins, which):
        f, v = (ins.f1, ins.v1) if which == 1 else (ins.f2, ins.v2)
        if f == 0:
            return 0
        if f == 1:
            return 1
        if f in REGS:
            return st.r[REGS[f]]
        if f == 20:
            return v
        if f in (24, 25):
            return v
        if f == 19:
            return None
        a = self.addr(st, f, v)
        if a is None:
            return None
        return st.m.get(a)

    def lea(self, st, ins):
        f, v = ins.f1, ins.v1
        if f in MEMSIZE:
            return self.addr(st, f, v)
        return self.get(st, ins, 1)

    def put(self, st, ins, val):
        f, v = ins.f2, ins.v2
        if f in REGS:
            st.r[REGS[f]] = val
            return
        if f in MEMSIZE:
            a = self.addr(st, f, v)
            if a is not None:
                st.m[a] = val
            else:
                st.m.clear()          # an unknown store may hit anything

    # ---- discovery ------------------------------------------------------- #
    def discover(self, entry):
        """Recursive descent from one function entry -> set of insn offsets."""
        if entry in self.funcs:
            return self.funcs[entry]
        seen = set()
        self.funcs[entry] = seen
        work = [entry]
        while work:
            off = work.pop()
            while off not in seen:
                ins = self.scp.insn(off)
                if ins is None or ins.op > 46:
                    self.unresolved["bad opcode"] += 1
                    break
                seen.add(off)
                nxt = off + ins.ln
                if ins.op in BRANCH or ins.op in (26, 42):
                    t = self.scp.branch_target(ins)
                    if t is not None:
                        work.append(t)
                    else:
                        self.unresolved["computed jump"] += 1
                if ins.op == 27:
                    t = self.scp.branch_target(ins)
                    if t is not None:
                        self.callers[t].add(off)
                        self.discover(t)
                    else:
                        self.unresolved["computed call"] += 1
                if ins.op in TERMINAL:
                    break
                off = nxt
        return seen

    # ---- one instruction ------------------------------------------------- #
    def step(self, st, ins, fn, record, depth=0):
        """Apply ins to st. `record` = whether to log facts (the fixpoint's
        final pass). Returns the branch info for a conditional branch."""
        op = ins.op
        if op == 1:
            self.put(st, ins, self.get(st, ins, 1))
        elif op == 2:
            self.put(st, ins, self.lea(st, ins))
        elif op == 3:
            if st.st is not None:
                st.st.append(self.get(st, ins, 1))
        elif op == 4:
            v = st.st.pop() if st.st else None
            if not st.st and st.st is not None and v is None:
                st.st = None
            self.put(st, ins, v)
        elif 5 <= op <= 18:
            self.put(st, ins, None)
        elif op == 19:
            st.cmp = (self.get(st, ins, 1), self.get(st, ins, 2), ins.off)
        elif op in SETCC:
            st.r[0] = self.boolsym(st.cmp, SETCC[op])
        elif op == 36:
            st.a[ins.f2] = self.get(st, ins, 1)
        elif op == 37:
            self.put(st, ins, st.a.get(ins.f1))
        elif op in (39, 40, 46):
            st.r[0] = None
        elif op in (41, 44):
            st.m.clear()
        elif op == 43:
            self.syscall(st, ins, fn, record)
        elif op == 27:
            self.call(st, ins, fn, record, depth)

    def boolsym(self, cmp, rel):
        """R0 after a SETcc: ('cond', subject, rel, value) when the compare
        was a tracked value against a constant, else None."""
        if not cmp:
            return None
        a, b, at = cmp
        c = self.cond_of(a, b, rel)
        return ("cond",) + c + (at,) if c else None

    def cond_of(self, a, b, rel):
        """(subject, rel, value): the branch/SETcc condition 'b rel a' put as
        'subject rel value' with the tracked value on the left."""
        if is_sym(b) and isinstance(a, int):
            if b[0] == "cond":
                return self.cond_bool(b, rel, a)
            return (b, rel, vm_int(a))
        if is_sym(a) and isinstance(b, int):
            if a[0] == "cond":
                return self.cond_bool(a, FLIP[rel], b)
            return (a, FLIP[rel], vm_int(b))
        return None

    def cond_bool(self, c, rel, k):
        """A test of a materialised condition against 0/1."""
        _t, subj, r, v, _at = c
        truth = {("==", 0): False, ("!=", 0): True, ("==", 1): True, ("!=", 1): False,
                 (">", 0): True, (">=", 1): True, ("<", 1): False, ("<=", 0): False}.get((rel, k))
        if truth is None:
            return None
        return (subj, r if truth else NEG[r], v)

    def window(self, st, n=17):
        w = st.r[4]
        if not isinstance(w, int):
            return None, [None] * n
        return w, [st.m.get(w + 4 * k) for k in range(n)]

    def syscall(self, st, ins, fn, record):
        cid = ins.v1 if ins.f1 == 20 else self.get(st, ins, 1)
        w, p = self.window(st)
        res = None
        if cid == 0xE066:
            res = ("flag_bit", vm_int(p[0]) if isinstance(p[0], int) else None)
        elif cid == 0xE067:
            res = ("flag_byte", vm_int(p[0]) if isinstance(p[0], int) else None)
        elif cid == 0xE068:
            k = vm_int(p[0]) if isinstance(p[0], int) else None
            i = vm_int(p[1]) if isinstance(p[1], int) else None
            res = ("flag_byte", i) if k == 11 else ("owned", k, i)
        elif cid in NATIVES and cid != 0xE06B:
            arg = vm_int(p[0]) if cid == 0xE065 and isinstance(p[0], int) else None
            res = ("native", cid, arg)
        elif cid == 0xE06B and w is not None:
            st.m[w + 0x10] = ("native", 0xE06B, "nation")
            st.m[w + 0x13] = ("native", 0xE06B, "rank")
            st.m[w + 0x1C] = ("native", 0xE06B, "contribution")
            st.m[w + 0x20] = ("native", 0xE06B, "money")
        elif cid == SRV_CALL:
            ev = vm_int(p[0]) if isinstance(p[0], int) else None
            params = [vm_int(x) if isinstance(x, int) else (None if x is None else sym_str(x))
                      for x in p[1:17]]
            while params and params[-1] is None:
                params.pop()
            st.m["_last_srv"] = ev
            if record:
                self.calls.append(dict(event=ev, params=params, at=ins.off, fn=fn))
        elif cid == SRV_POLL and w is not None:
            ev = st.m.get("_last_srv")
            for k in range(1, 17):
                st.m[w + 4 * k] = ("srv_answer", ev, k)
            res = ("srv_status", ev)
        elif cid == MSG_CALL and record:
            self.msgs[ins.off] = (p[0], p[1])
        st.r[0] = res
        if record and res and res[0] in ("flag_bit", "flag_byte", "owned", "native") \
                and res[-1] is None and res[0] != "native":
            self.unresolved["flag index not constant"] += 1

    def call(self, st, ins, fn, record, depth):
        t = self.scp.branch_target(ins)
        if t is None:
            st.r = [None] * 5
            return
        # inline a short straight-line callee with the caller's state: that is
        # how the helper library's wrappers (arg -> var -> window -> syscall)
        # hand their argument through
        sub = st.copy()
        sub.st = []
        off, n = t, 0
        while n < self.MAX_INLINE and depth < 3:
            i2 = self.scp.insn(off)
            if i2 is None or i2.op > 46:
                break
            if i2.op == 28:
                st.r = sub.r
                st.m.update({k: v for k, v in sub.m.items()})
                return
            if i2.op in BRANCH or i2.op in (0, 26, 42):
                break
            if i2.op in SETCC or i2.op == 19:
                self.step(sub, i2, fn, False, depth + 1)
            elif i2.op == 43:
                # a syscall inside the callee is attributed to the CALL site
                self.syscall(sub, i2, fn, record)
                if record and self.calls and self.calls[-1]["at"] == i2.off:
                    self.calls[-1]["via"] = ins.off
            else:
                self.step(sub, i2, fn, False, depth + 1)
            off += i2.ln
            n += 1
        # a callee that branches before returning (the server-call pollers):
        # its answer is what its E220 asked for, when it made one
        ev = sub.m.get("_last_srv")
        st.r = [("srv_answer", ev, 1) if ev is not None and sub.m.get("_last_srv") != st.m.get("_last_srv")
                else None] + [None] * 4
        if ev is not None:
            st.m["_last_srv"] = ev

    # ---- a function, to a fixpoint ---------------------------------------- #
    def run_function(self, entry):
        offs = self.discover(entry)
        order = sorted(offs)
        succ = {}
        for off in order:
            ins = self.scp.insn(off)
            nx = []
            if ins.op not in TERMINAL:
                nx.append(off + ins.ln)
            if ins.op in BRANCH or ins.op in (26, 42):
                t = self.scp.branch_target(ins)
                if t is not None:
                    nx.append(t)
            succ[off] = [x for x in nx if x in offs]
        pred = collections.defaultdict(list)
        for o, ss in succ.items():
            for s in ss:
                pred[s].append(o)
        IN = {entry: State()}
        work = [entry]
        OUT = {}
        guard = 0
        while work and guard < 200000:
            guard += 1
            off = work.pop()
            st = IN[off].copy()
            ins = self.scp.insn(off)
            self.step(st, ins, entry, False)
            OUT[off] = st
            for s in succ[off]:
                if s not in IN:
                    IN[s] = st.copy()
                    work.append(s)
                elif IN[s].meet(st):
                    work.append(s)
        # final pass: log what the fixpoint states say
        for off in order:
            if off not in IN:
                continue
            st = IN[off].copy()
            ins = self.scp.insn(off)
            if ins.op in BRANCH and st.cmp:
                a, b, at = st.cmp
                c = self.cond_of(a, b, BRANCH[ins.op])
                if c:
                    subj, rel, val = c
                    t = self.scp.branch_target(ins)
                    self.reads.append(dict(subject=subj, rel=rel, value=val, cmp_at=at,
                                           at=ins.off, then=t, orelse=off + ins.ln, fn=entry))
            self.step(st, ins, entry, True)
        return offs

    def run(self):
        for name, h in self.scp.entries.items():
            self.discover(h)
        done = set()
        for f in list(self.funcs):
            if f not in done:
                self.run_function(f)
                done.add(f)
        # dedupe calls logged by both inline and direct paths
        seen, calls = set(), []
        for c in self.calls:
            k = (c["event"], tuple(c["params"]), c.get("via", c["at"]))
            if k not in seen:
                seen.add(k)
                calls.append(c)
        self.calls = calls
        return self

    def reach(self):
        """{function entry: set(entry names that reach it)}."""
        edges = collections.defaultdict(set)
        for f, offs in self.funcs.items():
            for o in offs:
                ins = self.scp.insn(o)
                if ins.op == 27:
                    t = self.scp.branch_target(ins)
                    if t is not None:
                        edges[f].add(t)
        out = collections.defaultdict(set)
        for name, h in self.scp.entries.items():
            todo, seen = [h], set()
            while todo:
                f = todo.pop()
                if f in seen:
                    continue
                seen.add(f)
                out[f].add(name)
                todo.extend(edges.get(f, ()))
        return out

    def sortie_tiles(self, start, limit=48):
        """The tile ids a LobbySally block compares the selected sortie tile
        (syscall 0xE30B, 0x610FC220: [wm+0xEBE], row*1000+col) against, from
        `start` (the byte==1 side) up to the srv_104 CALL: `CMP imm, R0`."""
        out, off = [], start
        for _ in range(limit):
            ins = self.scp.insn(off)
            if ins is None or ins.op in (0, 26, 27, 28, 42):
                break
            if ins.op == 19 and ins.f1 in (20, 24, 25) and ins.f2 == 2:
                out.append(ins.v1)
            off += ins.ln
        return out

    def side_effects(self, start, limit=60):
        """What the script does from `start` until its next conditional branch
        or RET: messages, server calls and other natives, in order."""
        out, off, n = [], start, 0
        while off is not None and n < limit:
            ins = self.scp.insn(off)
            if ins is None or ins.op > 46:
                break
            if ins.op == 43:
                cid = ins.v1 if ins.f1 == 20 else None
                if cid == MSG_CALL and off in self.msgs:
                    spk, mi = self.msgs[off]
                    out.append(("msg", spk, mi))
                elif cid == SRV_CALL:
                    ev = [c for c in self.calls if c["at"] == off]
                    out.append(("srv", ev[0]["event"] if ev else None,
                                ev[0]["params"] if ev else None))
                elif cid in (0xE300, 0xE301, 0xE302, 0xE317, 0xE318):
                    out.append(("screen", cid))
            if ins.op == 27:
                t = self.scp.branch_target(ins)
                ev = [c for c in self.calls if c.get("via") == off]
                if ev:
                    out.append(("srv", ev[0]["event"], ev[0]["params"]))
            if ins.op in BRANCH or ins.op in (0, 28):
                break
            if ins.op in (26, 42):
                off = self.scp.branch_target(ins)
            else:
                off += ins.ln
            n += 1
        return out


# --------------------------------------------------------------------------- #
# dialogue: labels and lines, from the installed (English) containers
# --------------------------------------------------------------------------- #
class Dialogue(object):
    def __init__(self, client):
        self.client = client
        self._cache = {}
        self.common = collections.Counter()

    def runs(self, rel):
        if rel in self._cache:
            return self._cache[rel]
        out = []
        try:
            p = fmofile.data_path(self.client, rel)
            dec = fmofmdt.decode(open(p, "rb").read()[16:])
            if dec[:3] == b"MSG":
                _o, recs = W.split_records(dec)
                for rec in recs:
                    rs = [r.strip().lstrip(":").strip() for r in W.text_runs(rec)]
                    out.append(" / ".join(r for r in rs if r))
        except Exception:                                    # noqa: BLE001
            out = []
        self._cache[rel] = out
        return out

    def learn(self, rels):
        for rel in rels:
            for t in set(self.runs(rel)):
                self.common[t] += 1

    def first_line(self, rel):
        """The first record that is not boilerplate shared by many containers."""
        for t in self.runs(rel):
            if t and self.common[t] <= 2 and len(t) >= 12 and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", t):
                return t[:160]
        return None

    def line(self, rel, k):
        rs = self.runs(rel)
        if isinstance(k, int) and 0 <= k < len(rs):
            return rs[k][:160]
        return None


# --------------------------------------------------------------------------- #
# the joins
# --------------------------------------------------------------------------- #
def read_tsv(path):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        return [dict(zip(cols, ln.rstrip("\r\n").split("\t"))) for ln in f if ln.strip()]


def fmt_key(k):
    return "0x%08x" % k


def sortie_lists(lev, analysed):
    """{byte: {...}}: every flag byte a LEV row's LobbySally entry tests for
    == 1, with the sortie tiles it then compares against (proved script data:
    the byte==1 side ends in srv_104(byte))."""
    out = {}
    for r in lev:
        if not r["sally"] or r["scp"] not in analysed:
            continue
        sc, an, reach = analysed[r["scp"]]
        if r["sally"] not in sc.entries:
            continue
        for rd in an.reads:
            s = rd["subject"]
            if s[0] != "flag_byte" or s[1] is None or rd["value"] != 1 or rd["rel"] not in ("==", "!="):
                continue
            if r["sally"] not in reach.get(rd["fn"], ()):
                continue
            side = rd["then"] if rd["rel"] == "==" else rd["orelse"]
            e = out.setdefault(s[1], dict(byte=s[1], zones=set(), entries=set(), tiles=[],
                                          nations=set(), at=set()))
            e["zones"].add((r["kind_lo"], r["kind_hi"]))
            e["entries"].add("0x%04x %s" % (r["scp"], r["sally"]))
            e["nations"].add(r["nation"])
            e["at"].add("0x%04x @0x%x" % (r["scp"], rd["cmp_at"]))
            for t in an.sortie_tiles(side):
                if t not in e["tiles"]:
                    e["tiles"].append(t)
    return out


def load_are(path):
    """tile id -> {(zone id, sector row)} from the ARE sector table
    (work/fmo/fmo-are-sectors.tsv, fmoare.py). Selector 0 is zone 100."""
    tiles = collections.defaultdict(set)
    if not os.path.exists(path):
        return tiles
    with io.open(path, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            c = ln.rstrip("\r\n").split("\t")
            if len(c) > 5 and c[0].isdigit() and c[1].isdigit() and c[5].isdigit():
                sel = int(c[0])
                tiles[int(c[5])].add((100 if sel == 0 else sel, int(c[1])))
    return tiles


R_SECTOR = re.compile(r"(Ctrl|Occ|Front)\.(\d+):S(\d+)")


def mission_cells(m):
    """{(zone id, sector row)} a catalogue mission names."""
    base = AREA_BASE.get(m.get("area"))
    if base is None:
        return set()
    return {(base + int(n) - 1, int(s)) for _z, n, s in R_SECTOR.findall(m.get("sectors", ""))}


def derive_bytes(missions, sally, are):
    """Assign each mission its progress byte by joining two client tables:
    the byte's sortie tiles (LobbySally) mapped to (zone, sector) through the
    ARE table, against the catalogue's own sector list. A mission whose
    cells are all among exactly one byte's cells, of the right nation, gets
    that byte with confidence `table`. Returns (missions with own_byte /
    prereq_byte / own_conf rewritten, notes). The tsv's values are kept as
    tsv_own_byte / tsv_prereq_byte so a disagreement stays visible."""
    nat_of = {"O.C.U.": 1, "U.S.N.": 2}
    cells = {b: set().union(*[are.get(t, set()) for t in e["tiles"]]) if e["tiles"] else set()
             for b, e in sally.items()}
    out, notes = [], []
    assigned = {}
    for m in missions:
        m = dict(m)
        m["tsv_own_byte"] = m.get("own_byte", "")
        m["tsv_prereq_byte"] = m.get("prereq_byte", "")
        want = mission_cells(m)
        hits = [b for b, e in sally.items()
                if want and want <= cells[b]
                and (nat_of[m["faction"]] in e["nations"] or 0 in e["nations"])]
        if len(hits) == 1:
            m["own_byte"] = str(hits[0])
            m["own_conf"] = "table"
            m["own_evidence"] = ("sortie tiles of byte %d (%s) cover the catalogue sectors %s"
                                 % (hits[0], ", ".join(sorted(sally[hits[0]]["entries"])),
                                    m.get("sectors")))
            assigned[(m["faction"], m["title"])] = hits[0]
        else:
            m["own_conf"] = ("inferred" if m.get("own_byte", "").strip() else "")
            m["own_evidence"] = ("no unique sortie-tile match (%d candidates); kept the tsv value"
                                 % len(hits))
            if m.get("own_byte", "").strip():
                assigned[(m["faction"], m["title"])] = int(m["own_byte"])
        if m["tsv_own_byte"] and m["own_byte"] != m["tsv_own_byte"]:
            notes.append("%s %s: fmo-missions.tsv says own byte %s, the sortie-tile join says %s"
                         % (m["faction"], m["title"], m["tsv_own_byte"], m["own_byte"]))
        elif not m["tsv_own_byte"] and m.get("own_byte"):
            notes.append("%s %s: own byte %s (blank in fmo-missions.tsv)"
                         % (m["faction"], m["title"], m["own_byte"]))
        out.append(m)
    for m in out:
        pre = m.get("prerequisite")
        pb = assigned.get((m["faction"], pre)) if pre else None
        m["prereq_byte"] = str(pb) if pb is not None else ""
        if m["tsv_prereq_byte"] and m["prereq_byte"] != m["tsv_prereq_byte"]:
            notes.append("%s %s: fmo-missions.tsv prereq byte %s, derived %s"
                         % (m["faction"], m["title"], m["tsv_prereq_byte"], m["prereq_byte"]))
    return out, notes


def main_labels():
    import fmoprogression as P
    return P.ARCS


def build(client, data_dir=DATA_DIR):
    arcs = main_labels()
    missions = read_tsv(os.path.join(data_dir, "fmo-missions.tsv"))
    cutscenes = read_tsv(os.path.join(data_dir, "fmo-cutscenes.tsv"))
    curve = read_tsv(os.path.join(data_dir, "fmo-class-exp.tsv"))
    lev = lev_table(client)

    # which LEV rows use which table
    table_use = collections.defaultdict(list)
    for r in lev:
        table_use[r["table"]].append(r)
    tables = [t for t in sorted(table_use) if any(x["table_loaded"] for x in table_use[t])]
    tables += [t for t in EXTRA_TABLES if os.path.exists(fmofile.data_path(client, t))]

    ev_rows_raw = {t: event_table(client, t) for t in tables}

    # every SCP the data names, plus the 0x8000..0x80FF range
    scps = set()
    for t, rows in ev_rows_raw.items():
        for r in rows:
            if r["scp"]:
                scps.add(r["scp"])
    for r in lev:
        if r["scp"]:
            scps.add(r["scp"])
    for k in range(0x100):
        rel, _m = scp_resources(0x8000 | k)
        if os.path.exists(fmofile.data_path(client, rel)):
            scps.add(0x8000 | k)
    dlg = Dialogue(client)
    dlg.learn(scp_resources(s)[1] for s in scps)

    # byte -> mission (from the existing join)
    own = {}
    for m in missions:
        for col, role in (("own_byte", "own"),):
            if m.get(col, "").strip():
                own.setdefault(int(m[col]), []).append(m)

    # ---- scripts ----------------------------------------------------------
    scripts = []
    script_reads = {}
    analysed = {}
    for s in sorted(scps):
        rel, msg = scp_resources(s)
        entry = dict(scp="0x%04x" % s, script=rel, dialogue=msg,
                     scene=arcs.get(msg) or None, first_line=dlg.first_line(msg),
                     entries=[], flag_reads=[], other_reads=[], server_calls=[],
                     flag_writes=[], analysis=None)
        if not os.path.exists(fmofile.data_path(client, rel)):
            entry["analysis"] = "script file absent from the install"
            scripts.append(entry)
            continue
        raw = open(fmofile.data_path(client, rel), "rb").read()
        if len(raw) == STUB_SIZE and hashlib.md5(raw).hexdigest() == STUB_MD5:
            entry["analysis"] = ("placeholder: the 32,292-byte stub SCP (LobbyEntry/LobbySally/"
                                 "TalkEvt0.. entries, a different header) that 162 of the 253 ids "
                                 "in 0x8000..0x80FF ship as; the real script is not in this install")
            entry["placeholder"] = True
            scripts.append(entry)
            continue
        try:
            sc = Scp(client, rel)
        except (ValueError, struct.error) as e:
            entry["analysis"] = "not decodable: %s" % e
            scripts.append(entry)
            continue
        an = Analyzer(sc).run()
        reach = an.reach()
        analysed[s] = (sc, an, reach)
        wrappers = {f for f in an.funcs if sc.insn(f) and sc.insn(f).op == 37}
        entry["entries"] = [dict(name=n, at="0x%x" % h) for n, h in sc.entries.items()]
        for rd in an.reads:
            subj = rd["subject"]
            by = sorted(reach.get(rd["fn"], ()))
            then_fx = an.side_effects(rd["then"]) if rd["then"] is not None else []
            else_fx = an.side_effects(rd["orelse"])

            def fx(lst):
                out = []
                for e in lst:
                    if e[0] == "msg":
                        spk, mi = e[1], e[2]
                        tbl = (spk >> 20) & 0xF if isinstance(spk, int) else None
                        mi = vm_int(mi) & 0xFFFF if isinstance(mi, int) else None
                        line = dlg.line(msg, mi) if tbl == 0 and mi is not None else None
                        out.append("message %s%s" % (mi, (": " + line) if line else ""))
                    elif e[0] == "srv":
                        out.append("server call %s %s" % (e[1], e[2]))
                    elif e[0] == "screen":
                        out.append("screen 0x%04X" % e[1])
                return out[:4]
            item = dict(op=rd["rel"], value=rd["value"], at="0x%x" % rd["cmp_at"],
                        branch_at="0x%x" % rd["at"],
                        then="0x%x" % rd["then"] if rd["then"] is not None else None,
                        orelse="0x%x" % rd["orelse"], entries=by,
                        then_does=fx(then_fx), else_does=fx(else_fx))
            if subj[0] in ("flag_byte", "flag_bit") and subj[1] is not None:
                item.update(kind="byte" if subj[0] == "flag_byte" else "bit", index=subj[1])
                if item["kind"] == "bit":
                    item["set"] = bool((rd["rel"] == "==" and rd["value"] != 0)
                                       or (rd["rel"] == "!=" and rd["value"] == 0)
                                       or (rd["rel"] in (">", ">=") and rd["value"] in (0, 1) and not (rd["rel"] == ">=" and rd["value"] == 0)))
                entry["flag_reads"].append(item)
            else:
                if subj[0] == "native":
                    nm = NATIVES.get(subj[1], ("0x%04X" % subj[1], ""))[0]
                    item["native"] = "0x%04X" % subj[1]
                    item["what"] = nm if subj[2] in (None,) else "%s(%s)" % (nm, subj[2])
                elif subj[0] == "owned":
                    item["native"] = "0xE068"
                    item["what"] = "owned kind %s index %s" % (subj[1], subj[2])
                elif subj[0] == "srv_answer":
                    item["native"] = "0xE222"
                    item["what"] = "server answer p%d of event %s" % (subj[2], subj[1])
                elif subj[0] == "srv_status":
                    continue          # the poll loop's 1/2/3 status, not a gate
                elif subj[0] in ("flag_byte", "flag_bit"):
                    continue          # inside a helper wrapper; its call sites carry the index
                else:
                    item["what"] = sym_str(subj)
                entry["other_reads"].append(item)
        for c in an.calls:
            if c["fn"] in wrappers and "via" not in c and not c["params"]:
                continue          # the helper wrapper itself; its call sites carry the args
            ents = sorted(reach.get(c["fn"], ()))
            entry["server_calls"].append(dict(event=c["event"], params=c["params"],
                                              at="0x%x" % c.get("via", c["at"]), entries=ents))
            w = SRV_WRITES.get(c["event"])
            p1 = c["params"][0] if c["params"] else None
            if w and isinstance(p1, int):
                kind, idx, val = w(p1)
                entry["flag_writes"].append(dict(kind=kind, index=idx, value=val,
                                                 via="server call %d [%d]" % (c["event"], p1),
                                                 at="0x%x" % c.get("via", c["at"]), entries=ents,
                                                 confidence="inferred"))
        entry["analysis"] = ("%d functions, %d insns%s" % (
            len(an.funcs), sum(len(v) for v in an.funcs.values()),
            (", " + ", ".join("%s x%d" % kv for kv in sorted(an.unresolved.items())))
            if an.unresolved else ""))
        scripts.append(entry)
        script_reads[s] = entry

    # ---- the byte map: sortie tiles x catalogue sectors ---------------------
    sally = sortie_lists(lev, analysed)
    are = load_are(ARE_TSV)
    missions, byte_notes = derive_bytes(missions, sally, are)
    own = {}
    for m in missions:
        if m.get("own_byte", "").strip():
            own.setdefault(int(m["own_byte"]), []).append(m)

    # ---- event rows -------------------------------------------------------
    event_rows = []
    for t in tables:
        uses = table_use.get(t, [])
        # the faction is the nation of the LEV rows that name this table; the
        # nation-0 row (D39 also serves kinds 0..99 for anyone) does not make
        # the table shared
        facs = sorted({NATION[x["nation"]] for x in uses if x["nation"]})
        kinds = ", ".join("%d..%d%s" % (x["kind_lo"], x["kind_hi"],
                                        "" if x["nation"] else " (any nation)") for x in uses) or None
        faction = facs[0] if len(facs) == 1 else None
        rows = ev_rows_raw[t]
        for r in rows:
            conds = []
            if r["lo"] or r["hi"]:
                lo, hi = sorted((r["lo"], r["hi"]))
                if r["lo"]:
                    conds.append(dict(type="pilot_level", min=lo, max=hi))
                else:
                    conds.append(dict(type="unknown", raw="level hi %d with lo 0: the client "
                                                          "skips the level test when +0x08 is 0" % r["hi"]))
            for b in r["bits"]:
                if b:
                    conds.append(dict(type="bit", id=b, set=True, byte=b >> 3, mask=1 << (b & 7)))
            for i, v in r["tests"]:
                if i:
                    conds.append(dict(type="byte", index=i, op="==", value=v & 0xFF))
                elif v:
                    conds.append(dict(type="unknown", raw="byte value %d with index 0 "
                                                          "(the client skips the test)" % v))
            rel, msg = scp_resources(r["scp"]) if r["scp"] else (None, None)
            # rows that come first with the same key+kind and could win
            shadow = [x["order"] for x in rows if x["order"] < r["order"]
                      and x["key"] == r["key"] and x["kind"] == r["kind"]
                      and x["entry"] and x["scp"]]
            mis = None
            byte_ids = [c["index"] for c in conds if c["type"] == "byte"]
            for b in byte_ids:
                for m in own.get(b, []):
                    if faction and m["faction"] != faction:
                        continue
                    if [c for c in conds if c["type"] == "byte" and c["index"] == b and c["value"] != 99]:
                        mis = m["title"]
            row = dict(faction=faction, table=t, zone_kinds=kinds, entity_key=fmt_key(r["key"]),
                       kind=r["kind"], role=r["entry"], order=r["order"],
                       row_offset="0x%x" % r["offset"], conditions=conds,
                       ungated=not conds, earlier_same_key=shadow,
                       scp="0x%04x" % r["scp"] if r["scp"] else None,
                       script=rel, dialogue=msg, scene=arcs.get(msg) if msg else None,
                       first_line=dlg.first_line(msg) if msg else None,
                       mission=mis, map_field=r["x4c"] or None, x50=r["x50"] or None,
                       raw=r["raw"],
                       confidence="proved")
            # the mission name is a join: say where it came from
            if mis:
                row["mission_confidence"] = next(
                    (m.get("own_conf") or "inferred") for m in missions if m["title"] == mis
                    and (not faction or m["faction"] == faction))
            event_rows.append(row)

    # ---- flags --------------------------------------------------------------
    flags = {}

    def flag(kind, index):
        k = (kind, index)
        if k not in flags:
            flags[k] = dict(kind=kind, index=index, name=None, mission=None, faction=None,
                            values={}, read_by=[], written_by=[], confidence="unknown", note="")
        return flags[k]

    for row in event_rows:
        ref = "%s row %d (%s %s)" % (row["table"], row["order"], row["entity_key"], row["role"])
        for c in row["conditions"]:
            if c["type"] == "byte":
                f = flag("byte", c["index"])
                f["read_by"].append(dict(where="event_table", ref=ref, test="==%d" % c["value"],
                                         note="-> SCP %s" % row["scp"]))
                f["values"].setdefault(str(c["value"]), None)
            elif c["type"] == "bit":
                f = flag("bit", c["id"])
                f["read_by"].append(dict(where="event_table", ref=ref, test="set",
                                         note="-> SCP %s" % row["scp"]))
    for r in lev:
        if r["visited_bit"]:
            f = flag("bit", r["visited_bit"])
            f["read_by"].append(dict(where="client_code", ref="LEV %s row %d (0x610ED040)" % (LEV, r["order"]),
                                     test="set -> skip",
                                     note="kinds %d..%d nation %s: SET = entry script %s %s does "
                                          "not play" % (r["kind_lo"], r["kind_hi"],
                                                        NATION[r["nation"]] or "any",
                                                        "0x%04x" % r["scp"], r["entry"])))
            f["name"] = f["name"] or "visited: %s (%d..%d)" % (r["entry"], r["kind_lo"], r["kind_hi"])
            f["confidence"] = "proved"
    for s in scripts:
        for rd in s["flag_reads"]:
            f = flag(rd["kind"], rd["index"])
            test = ("%s%s" % (rd["op"], rd["value"])) if rd["kind"] == "byte" else \
                ("set" if rd.get("set") else "clear")
            f["read_by"].append(dict(where="script", ref="SCP %s @%s" % (s["scp"], rd["at"]),
                                     test=test, note="entries %s" % ",".join(rd["entries"])))
            if rd["kind"] == "byte" and rd["op"] in ("==", "!="):
                f["values"].setdefault(str(rd["value"]), None)
    # client code reader of byte 128
    flag("byte", 128)["read_by"].append(dict(where="client_code", ref="0x6109F434",
                                             test="==99", note=CLIENT_FACTS["registered_ui"]))
    # writers
    events = read_tsv(os.path.join(PUBLIC_DATA, "fmo-events.tsv"))
    for f in flags.values():
        f["written_by"].append(dict(where="server", ref="fmostore.set_flag_byte/flags_block; "
                                    "FMO_STATUS_FLAGS; gate tool", value=None,
                                    note="the server owns the block; the client learns it from "
                                         "0x014A at login and 0x015A pushes"))
    flag("byte", 128)["written_by"].append(dict(where="server", ref="fmo-events.tsv 205 * 128=99",
                                                value=99, note="the sergeant's event 205 (armed row)"))
    flag("byte", 128)["written_by"].append(dict(where="server", ref="fmoserver/defaults.py FMO_STATUS_FLAGS",
                                                value=99, note="default seed 8,9,10,11,12,13,128=99"))
    for b in (8, 9, 10, 11, 12, 13):
        flag("bit", b)["written_by"].append(dict(where="server", ref="fmoserver/defaults.py FMO_STATUS_FLAGS",
                                                 value=1, note="default seed: the lobby entry scripts "
                                                               "have played"))
    for m in missions:
        if m.get("own_byte", "").strip():
            f = flag("byte", int(m["own_byte"]))
            f["written_by"].append(dict(where="server", ref="fmoserver/progress.py advance_progress",
                                        value=99, note="on return from a granted sortie when this "
                                                       "mission is the unique frontier"))
    for s in scripts:
        for w in s["flag_writes"]:
            f = flag(w["kind"], w["index"])
            f["written_by"].append(dict(where="script", ref="SCP %s @%s" % (s["scp"], w["at"]),
                                        value=w["value"], note="%s (inferred server-call "
                                                               "semantics; entries %s)"
                                        % (w["via"], ",".join(w["entries"]))))
    for f in flags.values():
        if f["kind"] == "byte" and f["index"] >= 128:
            # the sortie scripts read ==1 and the arcs switch on 0/1/2/3/99;
            # no client code writes a byte (0x611A39F0 only sets bits)
            f["note"] = (f["note"] + "; " if f["note"] else "") + \
                "no client-side writer: bytes change only when the server sends the block"

    # names, missions, confidence
    for (kind, idx), f in flags.items():
        ms = own.get(idx, []) if kind == "byte" else []
        if ms:
            f["mission"] = " / ".join(sorted({m["title"] for m in ms}))
            facs = sorted({m["faction"] for m in ms})
            f["faction"] = facs[0] if len(facs) == 1 else None
            f["name"] = "progress: %s" % f["mission"]
            confs = {m.get("own_conf") or "" for m in ms}
            f["confidence"] = "table" if confs == {"table"} else "inferred"
            f["note"] = "own byte per fmo-missions.tsv (own_conf %s)" % ",".join(sorted(confs))
        if kind == "byte" and idx == 128:
            f["name"] = "pilot registered (the counters' gate)"
            f["confidence"] = "proved"
            f["note"] = "byte[128]==99 proved live 2026-09-05 (tag_mapslct refuses below it)"
        if kind == "bit" and not f["name"]:
            f["name"] = "bit %d (byte %d mask 0x%02x)" % (idx, idx >> 3, 1 << (idx & 7))
        if kind == "byte" and not f["name"]:
            f["name"] = "byte %d (no mission assigned)" % idx
        if kind == "byte":
            vals = {}
            for v in sorted({int(x) for x in f["values"]}):
                vals[str(v)] = {0: "not started", 99: "done"}.get(v, "step %d" % v)
            f["values"] = vals
        if kind == "bit":
            f["values"] = {}
        # a flag that is read but that nothing we know writes except the server
        f["writer_known"] = any(w["value"] is not None for w in f["written_by"])
    # prereq bytes from the missions table
    for m in missions:
        if m.get("prereq_byte", "").strip():
            f = flags.get(("byte", int(m["prereq_byte"])))
            if f:
                f["note"] += "; prerequisite of %s (%s)" % (m["title"], m["faction"])

    flag_list = sorted(flags.values(), key=lambda f: (f["kind"], f["index"]))

    # ---- other gates -------------------------------------------------------
    other = []
    thr = {int(r["level"]): int(r["exp"]) for r in curve if r.get("level")}
    lvls = sorted({c["min"] for row in event_rows for c in row["conditions"]
                   if c["type"] == "pilot_level"})
    for L in lvls:
        other.append(dict(kind="pilot_level", name="Pilot level %d" % L,
                          condition="class 12 (Pilot) exp >= %s (level %d)" % (thr.get(L, "?"), L),
                          unlocks="event rows with level window starting at %d: %s" % (
                              L, ", ".join(sorted({"%s %s" % (r["table"].split("/")[-1], r["scp"])
                                                   for r in event_rows for c in r["conditions"]
                                                   if c["type"] == "pilot_level" and c["min"] == L}))),
                          ref=CLIENT_FACTS["pilot_level"], confidence="proved",
                          exp=thr.get(L)))
    for r in lev:
        other.append(dict(kind="lev", name="lobby entry %s %s" % ("0x%04x" % r["scp"], r["entry"] or "(none)"),
                          condition="MapKind %d..%d && nation %s%s" % (
                              r["kind_lo"], r["kind_hi"], NATION[r["nation"]] or "any",
                              (" && bit %d clear" % r["visited_bit"]) if r["visited_bit"] else ""),
                          unlocks="entry script %s plays on zone entry; event table %s%s" % (
                              r["entry"] or "(none)", r["table"],
                              "" if r["table_loaded"] else " (NOT loaded: id >= 0x13BD1 or absent)"),
                          ref="%s row %d; %s" % (LEV, r["order"], CLIENT_FACTS["lev_walk"]),
                          confidence="proved", sally=r["sally"] or None))
    other.append(dict(kind="mapkind", name="Briefing Room menu",
                      condition="rank byte >= 21 (Major) && MapKind == 509",
                      unlocks="the Briefing Room entry un-greys",
                      ref="0x6108B8A1 (fmo-rank-byte-is-zero-based, fmo-zone-table-and-area-gate)",
                      confidence="proved"))
    other.append(dict(kind="permit", name="Change Area zone permit",
                      condition="owned kind 10 byte[zone/100 - 1] > 0 && a 0x016C row for "
                                "(nation, zone) && zone != current",
                      unlocks="a zone is selectable in Change Area",
                      ref="0x611794A0 / 0x611A3B30 (fmo-zone-table-and-area-gate)",
                      confidence="proved"))
    other.append(dict(kind="nation", name="cast nation switch",
                      condition="E060 self entity+0x1C3 == 1 (O.C.U. cast) / == 2 (U.S.N. cast)",
                      unlocks="lobby scripts create their cast only for 1 or 2",
                      ref="0x610F9370 (fmo-cutscene-cast)", confidence="proved"))
    other.append(dict(kind="nation", name="event table by nation",
                      condition="the LEV row chosen by (MapKind, lobby+0x8B4 nation)",
                      unlocks="which NPC event table (and so which story rows) a zone uses",
                      ref=CLIENT_FACTS["table_select"], confidence="proved"))
    other.append(dict(kind="other", name="event-row walk preconditions",
                      condition="selector 0x610F6ED0 kind 1: [[0x613CA3F0]+4] == 1, [wm+0xED6] "
                                "and [wm+0xED7] clear; walker state [ebx+0x24] == 8",
                      unlocks="any talk row at all",
                      ref="0x610F707E / 0x610ED246", confidence="proved"))
    # one gate per (script, value tested): a cast switch on nation 1/2 repeats
    # hundreds of times and says the same thing each time
    for s in scripts:
        groups = collections.OrderedDict()
        for rd in s["other_reads"]:
            groups.setdefault(rd.get("what"), []).append(rd)
        for what, rds in groups.items():
            tests = sorted({"%s %s" % (r["op"], r["value"]) for r in rds})
            ents = sorted({e for r in rds for e in r["entries"]})
            does = [d for r in rds for d in r["then_does"]][:3]
            other.append(dict(kind={"nation": "nation", "mapkind": "mapkind", "pilot_level": "pilot_level",
                                    "status_record": "rank"}.get(what.split("(")[0], "other"),
                              name="script test: %s in SCP %s" % (what, s["scp"]),
                              condition="%s %s" % (what, " | ".join(tests)),
                              unlocks="branches in SCP %s (%s)%s" % (
                                  s["scp"], s["script"], (": " + "; ".join(does)) if does else ""),
                              ref="SCP %s @%s%s; entries %s" % (
                                  s["scp"], ",".join(r["at"] for r in rds[:6]),
                                  " (+%d more)" % (len(rds) - 6) if len(rds) > 6 else "", ",".join(ents)),
                              sites=len(rds),
                              confidence="proved" if rds[0].get("native") else "inferred"))

    # ---- the client's own prerequisite gates, then the ladder ----------------
    byte_notes += client_prereqs(missions, event_rows, scripts)
    ladder = build_ladder(missions, event_rows, scripts)
    rewards = mission_rewards(analysed, dlg)

    return dict(
        generated=datetime.date.today().isoformat(), generator=GENERATOR,
        client_facts=CLIENT_FACTS, natives={"0x%04X" % k: dict(name=v[0], ref=v[1])
                                            for k, v in sorted(NATIVES.items())},
        row_format=[dict(field=a, meaning=b) for a, b in ROW_FIELDS],
        lev=[dict(order=r["order"], kinds="%d..%d" % (r["kind_lo"], r["kind_hi"]),
                  nation=NATION[r["nation"]], table=r["table"], table_loaded=r["table_loaded"],
                  scp="0x%04x" % r["scp"], entry=r["entry"] or None, sally=r["sally"] or None,
                  visited_bit=r["visited_bit"] or None) for r in lev],
        server_events={str(k): v for k, v in sorted(SRV_EVENT_NOTES.items())},
        byte_map=[dict(byte=b, missions=["%s %s (Lv %s)" % (m["faction"], m["title"], m["level"])
                                         for m in missions if m.get("own_byte") == str(b)],
                       sally_entries=sorted(e["entries"]), tested_at=sorted(e["at"]),
                       zones=["%d..%d" % z for z in sorted(e["zones"])],
                       sortie_tiles=e["tiles"],
                       sortie_cells=sorted("%d:S%d" % c for t in e["tiles"] for c in are.get(t, ())),
                       confidence="proved")
                  for b, e in sorted(sally.items())],
        byte_map_notes=byte_notes,
        missions=[{k: m.get(k) for k in ("faction", "title", "level", "prerequisite", "own_byte",
                                         "prereq_byte", "own_conf", "own_evidence", "tsv_own_byte",
                                         "tsv_prereq_byte", "client", "area", "sectors")}
                  for m in missions],
        flags=flag_list, other_gates=other, event_rows=event_rows, scripts=scripts,
        ladder=ladder,
        rewards={str(b): v for b, v in sorted(rewards["rewards"].items())},
        rewards_source=rewards["source"], counts=dict(
            event_rows=len(event_rows),
            rows_per_table={t: len(ev_rows_raw[t]) for t in tables},
            scripts=len(scripts),
            scripts_analysed=sum(1 for s in scripts if s["analysis"] and "functions" in s["analysis"]),
            flag_reads=sum(len(s["flag_reads"]) for s in scripts),
            flags=collections.Counter("%s/%s" % (f["kind"], f["confidence"]) for f in flag_list)),
        _cutscenes_tsv=cutscenes)


def row_cond_str(r):
    out = []
    for c in r["conditions"]:
        if c["type"] == "pilot_level":
            out.append("pilot Lv %d..%d" % (c["min"], c["max"]))
        elif c["type"] == "bit":
            out.append("bit %d set" % c["id"])
        elif c["type"] == "byte":
            out.append("byte %d==%d" % (c["index"], c["value"]))
        else:
            out.append(c.get("raw", "?"))
    return " && ".join(out) or "ungated"


def client_prereqs(missions, event_rows, scripts):
    """Correct a catalogue prerequisite the client itself contradicts. A
    mission's offer scene (a row testing its own byte == 0, or a row whose
    script asks the server about the byte at the mission's level) that gates
    on another mission's byte == 99, where the scene's own script ALSO tests
    that byte against 99, is the gate the client enforces; the catalogue's
    "Requires:" title loses. The script test is what keeps this narrow: the
    Frontline operator 0x8050 gates Hunt Down Damien Rivers on 181 == 99 but
    sequences Destroy the Rebels first inside the script, and does not re-test
    181. MUTATES missions; -> notes."""
    by_scp = {s["scp"]: s for s in scripts}
    own = {}
    for m in missions:
        if m.get("own_byte"):
            own[int(m["own_byte"])] = m
    notes = []
    for m in missions:
        if not m.get("own_byte"):
            continue
        ob = int(m["own_byte"])
        pb = int(m["prereq_byte"]) if m.get("prereq_byte") else None
        found = set()
        for r in event_rows:
            if (r["faction"] and r["faction"] != m["faction"]) or not r["scp"]:
                continue
            bs = {c["index"]: c["value"] for c in r["conditions"] if c["type"] == "byte"}
            sc = by_scp.get(r["scp"], {})
            asks = {p for c in sc.get("server_calls", []) if c["event"] in (104, 105)
                    for p in c["params"][:1] if isinstance(p, int)}
            lvl = [c["min"] for c in r["conditions"] if c["type"] == "pilot_level"]
            offer = bs.get(ob) == 0 or (ob not in bs and ob in asks and lvl
                                         and m.get("level") and lvl[0] == int(m["level"]))
            if not offer:
                continue
            retest = {f["index"] for f in sc.get("flag_reads", [])
                      if f["kind"] == "byte" and f["value"] == 99}
            for b, v in bs.items():
                if v == 99 and b != ob and b in own and b in retest \
                        and own[b]["faction"] == m["faction"]:
                    found.add((b, r["scp"], r["table"].split("/")[-1], r["order"]))
        bytes_found = {f[0] for f in found}
        if len(bytes_found) == 1 and bytes_found != {pb}:
            b, scp, tbl, order = sorted(found)[0]
            notes.append("%s %s: prerequisite %s (byte %s) -> %s (byte %d): %s row %d and "
                         "its script %s both test byte %d == 99"
                         % (m["faction"], m["title"], m.get("prerequisite"), pb,
                            own[b]["title"], b, tbl, order, scp, b))
            m["prerequisite"] = own[b]["title"]
            m["prereq_byte"] = str(b)
    return notes


REWARD_TEXT = re.compile(r'Obtained the (.+?) "(.+?)"\.')
REWARD_MONEY = re.compile(r"Earned ([\d,]+)H\$")


def mission_rewards(analysed, dlg):
    """{byte: {money, items}}: what a REPORTED mission pays, from the script
    library's reward routine. srv_105 runs it with the mission byte when the
    answer's p2 != 0 (0x8071 0x12e0 -> 0x824): a switch `cmp #byte, R0; beq`
    over every story byte, each case printing an item line per nation (the
    `[var] == 1` test picks the first, O.C.U., one) and an "Earned NH$" line.
    The messages are the client's own; the routine only SHOWS them, so the
    server has to grant what they say. Decoded from the first script that
    carries the routine (they are all copies of one library)."""
    for s in sorted(analysed):
        sc, an, _reach = analysed[s]
        for f in sorted(an.funcs):
            cases, off, last = [], f, None
            for _ in range(200):
                ins = sc.insn(off)
                if ins is None:
                    break
                if ins.op == 19 and ins.f1 == 20 and ins.f2 == 2:
                    last = vm_int(ins.v1)
                elif ins.op == 20 and last is not None:
                    cases.append((last, sc.branch_target(ins)))
                    last = None
                elif ins.op in TERMINAL or ins.op in BRANCH and last is None:
                    break
                off += ins.ln
            if len(cases) < 20 or not any(b == 130 for b, _t in cases):
                continue
            runs = dlg.runs(scp_resources(s)[1])
            starts = sorted({t for _b, t in cases})
            out = {}
            for b, t in cases:
                # a case runs from its target to the next case's target (the
                # last one is empty: SE's 197/198 jump straight to the end)
                stop = next((x for x in starts if x > t), t + 0x40)
                lines, o = [], t
                for _ in range(120):
                    ins = sc.insn(o)
                    if ins is None or ins.op in (0, 28) or o >= stop:
                        break
                    if o in an.msgs:
                        mi = vm_int(an.msgs[o][1]) if isinstance(an.msgs[o][1], int) else None
                        if isinstance(mi, int) and 0 <= mi < len(runs):
                            lines.append(runs[mi])
                    # straight through, jumps ignored: both nation arms and
                    # the money line all sit inside [t, stop)
                    o += ins.ln
                money = sum(int(x.replace(",", "")) for ln in lines for x in REWARD_MONEY.findall(ln))
                items = [dict(kind=k, name=n) for ln in lines for k, n in REWARD_TEXT.findall(ln)]
                out[b] = dict(money=money,
                              items={"O.C.U.": items[0], "U.S.N.": items[1]} if len(items) == 2
                              else ({"any": items[0]} if items else {}),
                              lines=lines)
            return dict(source="SCP 0x%04x 0x%x" % (s, f), rewards=out)
    return dict(source=None, rewards={})


def build_ladder(missions, event_rows, scripts):
    """Per faction: the catalogue missions ordered by level then chain depth,
    each with the event rows that touch it. A row touches a mission when it
    tests the mission's own byte, or when the SCP it runs asks the server
    about that byte (104/105 [own]). A row that gates such a scene on ==99 of
    a byte that is not the catalogue prerequisite's is reported as a fork."""
    by_scp = {s["scp"]: s for s in scripts}
    title_of = {}
    for m in missions:
        if m.get("own_byte"):
            title_of.setdefault(int(m["own_byte"]), "%s %s" % (m["faction"], m["title"]))
    out = {}
    for fac in ("O.C.U.", "U.S.N."):
        ms = [m for m in missions if m["faction"] == fac]
        by_title = {m["title"]: m for m in ms}

        def depth(m, seen=()):
            p = m.get("prerequisite")
            if not p or p not in by_title or p in seen:
                return 0
            return 1 + depth(by_title[p], seen + (p,))
        order = sorted(ms, key=lambda m: (int(m["level"] or 0), depth(m), m["title"]))
        steps = []
        for i, m in enumerate(order):
            ob = int(m["own_byte"]) if m.get("own_byte") else None
            pb = int(m["prereq_byte"]) if m.get("prereq_byte") else None
            scenes, forks = [], []
            for r in event_rows:
                if r["faction"] and r["faction"] != fac:
                    continue
                if not r["scp"]:
                    continue
                bs = {c["index"]: c["value"] for c in r["conditions"] if c["type"] == "byte"}
                sc = by_scp.get(r["scp"], {})
                asks = {p for c in sc.get("server_calls", []) if c["event"] in (104, 105)
                        for p in c["params"][:1] if isinstance(p, int)}
                if ob is None or not (ob in bs or (ob in asks and r["conditions"])):
                    continue
                if ob in bs:
                    v = bs[ob]
                    role = {0: "offer", 99: "after completion"}.get(v, "in progress, state %s" % v)
                else:
                    role = "arc scene (its script asks the server about byte %d)" % ob
                scenes.append("%s %s row %d %s [%s] %s: %s" % (
                    r["scp"], r["table"].split("/")[-1], r["order"], r["entity_key"], r["role"],
                    role, row_cond_str(r)))
                for b, v in bs.items():
                    lvl = [c["min"] for c in r["conditions"] if c["type"] == "pilot_level"]
                    offer = (ob in bs and bs[ob] == 0) or \
                        (ob not in bs and lvl and m.get("level") and lvl[0] == int(m["level"]))
                    if v == 99 and b != ob and pb is not None and b != pb and offer:
                        forks.append("%s row %d gates this mission on byte %d==99 (%s), the "
                                     "catalogue names %s (byte %d)" % (
                                         r["table"].split("/")[-1], r["order"], b,
                                         title_of.get(b, "?"), m.get("prerequisite"), pb))
            sibs = [x["title"] for x in ms if m.get("prerequisite")
                    and x.get("prerequisite") == m.get("prerequisite") and x["title"] != m["title"]]
            same_level = [x["title"] for x in ms if x["level"] == m["level"] and x["title"] != m["title"]]
            fork = []
            if sibs:
                fork.append("shares prerequisite with: " + ", ".join(sibs))
            if same_level:
                fork.append("same level as: " + ", ".join(same_level))
            fork.extend(forks)
            conf = m.get("own_conf") or "unknown"
            steps.append(dict(step=i + 1, mission=m["title"],
                              level=int(m["level"]) if m.get("level") else None,
                              own_byte=ob, prereq_byte=pb, prerequisite=m.get("prerequisite") or None,
                              offered_by=m.get("client") or None, area=m.get("area") or None,
                              chain_depth=depth(m), scenes=scenes, fork="; ".join(fork) or None,
                              confidence=conf if conf in ("table", "inferred") else "unknown"))
        out[fac] = steps
    return out


def check(doc, client):
    """The self-checks the task asks for. -> list of failures."""
    fails = []
    rows = doc["event_rows"]
    # every gated row of fmo-cutscenes.tsv must be one of ours
    for c in doc["_cutscenes_tsv"]:
        hit = [r for r in rows if r["table"] == c["zone_table"] and r["entity_key"] == c["entity_key"]
               and r["role"] == c["entry"] and r["scp"] == c["scp"]
               and _tests_str(r) == c["byte_tests"] and _bits_str(r) == c["flag_bits"]
               and _rank_str(r) == c["rank"]]
        if not hit:
            fails.append("cutscene tsv row not in event_rows: %s %s %s %s %s" % (
                c["zone_table"], c["entity_key"], c["entry"], c["scp"], c["byte_tests"]))
    # per-table counts against a direct count of the container
    for t, n in doc["counts"]["rows_per_table"].items():
        dec = fmofmdt.load(client, t)
        direct = struct.unpack_from("<H", dec, 0x46)[0]
        mine = sum(1 for r in rows if r["table"] == t)
        if not (direct == n == mine):
            fails.append("%s: container says %d rows, parsed %d, emitted %d" % (t, direct, n, mine))
    return fails


def _tests_str(r):
    return " & ".join("byte[%d]==%d" % (c["index"], c["value"]) for c in r["conditions"]
                      if c["type"] == "byte")


def _bits_str(r):
    return ",".join(str(c["id"]) for c in r["conditions"] if c["type"] == "bit")


def _rank_str(r):
    lv = [c for c in r["conditions"] if c["type"] == "pilot_level"]
    return "%d-%d" % (lv[0]["min"], lv[0]["max"]) if lv else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=DEFAULT_CLIENT,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--data", default=DATA_DIR,
                    help="directory holding fmo-missions.tsv / fmo-cutscenes.tsv")
    ap.add_argument("--out", default=DATA_DIR, help="directory for %s" % OUT_NAME)
    ap.add_argument("--check", action="store_true", help="counts and self-checks only")
    ap.add_argument("--fix-missions", action="store_true",
                    help="also write the catalogue's own/prerequisite bytes into "
                         "fmo-missions.tsv in --data (run after fmoprogression.py)")
    a = ap.parse_args()
    doc = build(a.client, a.data)
    fails = check(doc, a.client)
    cnt = doc["counts"]
    print("event rows %d  %s" % (cnt["event_rows"], cnt["rows_per_table"]))
    print("scripts %d (analysed %d), flag reads %d" % (cnt["scripts"], cnt["scripts_analysed"],
                                                      cnt["flag_reads"]))
    print("flags %s" % dict(sorted(cnt["flags"].items())))
    for fac, steps in doc["ladder"].items():
        print("ladder %s: %d steps" % (fac, len(steps)))
    print("checked %d fmo-cutscenes.tsv rows against event_rows, %d tables against their "
          "container row counts" % (len(doc["_cutscenes_tsv"]), len(cnt["rows_per_table"])))
    # the twin: the same check on a row that cannot exist must fail
    bogus = dict(doc["_cutscenes_tsv"][0], byte_tests="byte[999]==1") if doc["_cutscenes_tsv"] else None
    if bogus and not check(dict(doc, _cutscenes_tsv=[bogus]), a.client):
        fails.append("the cutscene-row check accepted a row that is not in the data")
    for f in fails:
        print("CHECK FAIL:", f)
    print("self-check: %s" % ("PASS" if not fails else "%d FAIL" % len(fails)))
    if a.check:
        return 1 if fails else 0
    del doc["_cutscenes_tsv"]
    doc["counts"]["flags"] = dict(sorted(doc["counts"]["flags"].items()))
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, OUT_NAME)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False, sort_keys=False)
        f.write("\n")
    print("wrote", path)
    if a.fix_missions and not fails:
        fix_missions_tsv(os.path.join(a.data, "fmo-missions.tsv"), doc["missions"])
    return 1 if fails else 0


def fix_missions_tsv(path, missions):
    """fmo-missions.tsv with each mission's own_byte, prereq_byte and own_conf
    taken from the catalogue (sortie tiles matched to the catalogue's sectors),
    every other column as it was. fmoprogression.py's third pass inferred some
    of these from dialogue and got O.C.U. Repel the Enemy Incursion wrong (167,
    which is the PMO Inspector's; the tiles say 171) and left eighteen blank.
    progress.py reads this file, so a blank byte is a mission the server can
    never complete."""
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    cols = lines[0].split("\t")
    by = {(m["faction"], m["title"]): m for m in missions}
    out, changed = [lines[0]], 0
    for ln in lines[1:]:
        r = dict(zip(cols, ln.split("\t")))
        m = by.get((r.get("faction"), r.get("title")))
        if m:
            for k in ("prerequisite", "own_byte", "prereq_byte", "own_conf"):
                if k in r and (m.get(k) or "") != (r[k] or ""):
                    r[k] = m.get(k) or ""
                    changed += 1
        out.append("\t".join(r.get(c, "") for c in cols))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print("fmo-missions.tsv: %d field(s) corrected from the catalogue" % changed)


if __name__ == "__main__":
    sys.exit(main())
