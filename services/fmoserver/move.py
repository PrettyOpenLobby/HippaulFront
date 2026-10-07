"""The Move menu (0x016E -> 0x016F, 0x016D) and places, and the small in-scene requests beside
it: play time, member check, log out, hangar password."""
import os
import struct
import time
from .knobs import _env_int
from .wirelog import log
from . import zoneentry


# --------------------------------------------------------------------------- #
# THE MOVE FLOW -- 0x016E/0x016F (the list) and 0x016D (the move itself)
# --------------------------------------------------------------------------- #
# VERIFIED: DECODED 2026-08-21, STATICALLY. 0x016E was seen live the moment the account
# holder picked the FIRST ENTRY of the submenu that 移動 / "Move" opens, and it
# went unanswered -- which is the 0x01AB failure mode (an unanswered menu action
# hangs FMO's UI with no way back to the Viewer). This section answers it.
#
# WARNING: DO NOT GREP THE IMAGE FOR 0x16E. It occurs at 78 imm32 and 156 imm16 sites
# and is nearly all unrelated arithmetic. Every outbound message in this client
# is a LITERAL pushed at the packet builder 0x61199FC0 --
#
#     push <flags> / push <payload length> / push <message id> / call 0x61199FC0
#
# -- so a sweep of the image enumerates all 58 builder sites, and the whole
# outbound protocol with them. 0x016E is built at exactly one: 0x61190727.
#
# THE OWNER IS A STATE MACHINE, and it is the Move dialog's controller:
# 0x611906C0, dispatching on `this+0x2C` through a 201-byte index at 0x6119107C
# into the 10-slot jump table at 0x61191054. Live states are 0..4, 100..102 and
# 200; 0x63 (99) is the idle slot, i.e. "this machine is parked on a dialog".
#
#     state 0  0x611906F9  SEND 0x016E, 16B payload, +0x00 = `this+0x3D`
#     state 1  0x611907CD  poll (0x61173F40); the reply must be 0x016F
#     state 2  0x611909FD  wait for the list dialog's result
#     state 3  0x61190A35  SEND 0x016D, 72B -- the move
#     state 4  0x61190B50  the reply is 0x0153, THE JOIN GRANT
#     100..102             a second entry point: sends 0x0170 (228B), reply is
#                          message 1. Not served here -- see the note at the end.
#
# WARNING: SO "MOVE" ENDS IN A 0x0153, exactly like 0x0150 does. State 4's consumer at
# 0x61190B95 is byte-for-byte the same handling the 0x0150 path gets: 88B from
# +0x124 to globals+0xD0, then 0x61006350(lobby+0x4F06, +0x18, payload, +0x1C),
# then 252B from +0x28 to lobby+0x6B3A. That makes this the client's own way to
# ask for a DIFFERENT MapNo, which is the natural test of whether terrain draws
# anywhere other than the interior we currently enter. A reply of message 1
# instead is the client's "no": 0x61174400, back to the lobby.
#
# WHAT THE LIST IS, and how the names were obtained rather than guessed. The
# dialog builder 0x61190520 switches on a mode byte `this+0x30` and installs
# column headings and a prompt by STRING ID, and FMO's UI string table is
# already cracked (an RC4-enciphered string table,
# Data/AI/F32/D02.DAT). An id resolves as group = (id >> 16) & 0x3FF and
# index = id & 0x7FF, so every 0xC00F00NN below is group 15, index NN -- and
# group 15 is, in SE's own English, the move-between-lobbies-and-rooms group:
#
#     0xC00F0000  "Failed to get the lobby or room."     the wrong-reply-id arm
#     0xC00F0001  "There is nobody in that lobby right now. Move there anyway?"
#     0xC00F0002  "There is nobody in that room right now. Move there anyway?"
#     0xC00F000A  "There is nobody in the briefing room right now. Move there
#                  anyway?"
#     0xC00F000B  "Lobby ID"   0xC00F000C  "Change Lobbies"    (mode 0)
#     0xC00F000D  "Room ID"    0xC00F000E  "Change Rooms"      (mode 1 and 2)
#     0xC00F000F  "People"                         the second column, both modes
#     0xC00F0010  "Enter Lobby ID"  0xC00F0011  "Enter Room ID"
#
# so `this+0x30` is the DESTINATION KIND -- 0 lobby, 1 room, 2 briefing room --
# and the list has two columns: an ID and a head count.
#
# VERIFIED: THE PARSE, from 0x61190070, which is the only reader of this payload:
#
#     count = dword [payload+0x00]                  ; signed; <= 0 is the empty
#     for i in range(count):                        ;   case, handled by state 1
#         item = new 16B object (vtable 0x6133DFEC)
#         item.set( dword [payload+0x10 + 4*i] )    ; -> item+0x08
#         item[+0x0C] = byte [payload+0x1F50 + i]
#         if the list widget is full: stop          ; capacity, below
#
# TWO PARALLEL ARRAYS, not an array of records. The dwords start at +0x10 and
# the bytes at +0x1F50; the gap is 0x1F40 = 2000 dwords, so the wire form holds
# 2000 entries and the payload is 0x1F50 + 2000 = 0x2720 bytes. The UI widget is
# separately reserved for 200 rows (`push 0xC8` at 0x6119064F), so a longer list
# is silently truncated on screen rather than refused.
#
# WARNING: WHICH COLUMN IS WHICH IS AN INFERENCE, AND THIS IS THE EVIDENCE FOR IT. The
# item's comparator 0x6118F830 sorts on `item+0x08` for key 0 and `item+0x0C`
# for key 1 -- i.e. the two columns installed above, in that order. So the dword
# is the "Lobby ID"/"Room ID" column and the byte is "People". Nothing read so
# far states it outright; a byte per entry is also consistent with a head count
# capped at 255, which is why this reading is offered rather than asserted.
#
#: 0x0182 -- "Play Time", off the in-scene Status menu (and `/time` in chat --
#: same message, cheaper door). An empty request: 20 bytes on the wire, 0x14 of
#: header and no payload.
MSG_PLAYTIME_REQ = 0x0182

#: VERIFIED:KEY: THE REPLY IS 0x0183, AND IT CARRIES EXACTLY ONE FIELD.
#:
#: WARNING: THIS RETRACTS THE COMMENT THAT STOOD HERE, AND THE RETRACTION IS THE
#: POINT. It said "we do not know its reply id", rested that on three searches
#: of the image coming back empty -- including *"no `cmp word [reg+6], 0x183`
#: anywhere"* -- and concluded the generic message 1. **That search was wrong.**
#: an immediate search for 0x183 finds exactly one site in `.text`, and it is the
#: test that decides this screen:
#:
#:     6116338e  cmp   word ptr [eax + 6], 0x183     ; eax = the received FRAME
#:     61163394  jne   0x61163444                    ; -> the [FMxxxxx] box
#:
#: So message 1 never had a chance: 1 != 0x183 takes the `jne`, and the arm at
#: 0x61163444 is the numbered error box -- `movsx eax, word [rx+8]` into systext
#: 16:22. **That is the "it rejects it quickly" reported live, and it
#: was OUR reply id, not a missing payload.** (mentions are not evidence:
#: three empty greps were read as evidence of absence and one of them was just
#: a bad grep.)
#:
#: WHERE THIS CAME FROM. The request is sent by the ctor at 0x6109E390, which
#: stamps vtable 0x61331794 (the class RTTI name next to it reads "Status");
#: slot 1 of that vtable, **0x61163340**, is the poll half -- state 0 sends via
#: 0x61173EC0, state 1 polls 0x61173F40 and parses. Its parse is four
#: instructions and one divide chain:
#:
#:     6116339a  mov   ecx, [eax + 0x24]      ; <- the ONLY field it reads
#:     6116339d  mov   eax, 0x88888889        ; the signed magic for / 60
#:     611633a2  imul  ecx ... sar edx, 5     ; edx:eax -> q = t / 60
#:     611633b1  mov   ecx, 0x3c   / idiv     ; eax = q/60, edx = q%60
#:     611633b8  mov   edi, 0x18   / idiv     ; eax = q/60/24, edx = (q/60)%24
#:     611633c2  push  ecx                    ; minutes = (t/60) % 60
#:     611633c8  push  edx                    ; hours   = (t/3600) % 24
#:     611633c9  push  eax                    ; days    = t / 86400
#:     611633d2  push  0xc0010006             ; systext 1:6, the format string
#:     611633e5  call  0x6124c656             ; sprintf(buf, fmt, date, d, h, m)
#:
#: KEY: So **`frame+0x24` = payload+0x10 is the play time IN SECONDS**, one u32,
#: and nothing else in the reply is read at all -- not the length, not
#: `word [rx+8]`, not a date. The "Current date/time %s" half of group 1 index 6
#: (`"Current date/time %s \nPlay time %dd %dh %dm"`) is built by the client
#: from its OWN clock (0x611E3EF0 on the object at 0x613CA3D4, arg 8) before the
#: sprintf, so **we do not send a date and cannot set one.**
#:
#: WARNING: THE LENGTH IS OURS, NOT SE'S. Only +0x10 is consumed, so 0x14 bytes is the
#: shortest body that carries the field; what SE sent is not recoverable from
#: the consumer. A longer body would be equally accepted.
MSG_PLAYTIME_REPLY = 0x0183
REPLY_0183_LEN = 0x14
S183_SECONDS = 0x10                     # u32 -> frame+0x24, seconds played


def reply_0183(seconds):
    """The Play Time reply body. `seconds` is clamped to u32.

    Pure, so the selftest can drive it. The client does the d/h/m arithmetic
    itself -- see the block comment above; all we owe it is the total."""
    b = bytearray(REPLY_0183_LEN)
    struct.pack_into("<I", b, S183_SECONDS, max(0, int(seconds)) & 0xFFFFFFFF)
    return bytes(b)


def playtime_dhm(seconds):
    """(days, hours, minutes) exactly as 0x6116339A computes them, for the log.

    WARNING: SIGNED, like the client's `idiv`: a value over 0x7FFFFFFF arrives there
    as negative and the screen goes strange. reply_0183 does not clamp to
    0x7FFFFFFF on purpose -- 68 years of play time is not a case worth a branch
    -- but the log line is built from this, so an absurd knob shows up there."""
    t = max(0, int(seconds))
    q = t // 60
    return q // 60 // 24, q // 60 % 24, q % 60

#: 0x0181 -- the first option on a ROOM MEMBER, off Soldier List -> Room
#: Members. Seen live 2026-08-22, unanswered, and it hung the screen. 1050
#: bytes (0x41A), built at 0x61179AB8, and it carries a NAME: the builder
#: copies a NUL-terminated string from `lobby+0x744A` to payload+0x10.
#:
#: Same reply-id situation as 0x0182 and answered the same way. There is no
#: `cmp word [reg+6], <id>` anywhere in its builder's function, and no reply
#: stamp belongs to it, so the generic message-1 convention is the remaining
#: candidate -- and 0x0182 confirmed that convention live minutes earlier.
#:
#: WARNING: THE DIFFERENCE FROM 0x0182 MATTERS. 0x0182 asks for the player's own play
#: time and an empty reply merely renders as "invalid time". This one asks
#: about ANOTHER MEMBER by name, so an empty message 1 answers "here is
#: nothing about them" -- it will unhang the screen and it will not be right.
#: Whatever it wanted is unknown; the request's own 1050 bytes are the clue,
#: and they are dumped at the point of receipt.
MSG_MEMBER_CHECK_REQ = 0x0181

#: VERIFIED: 0x0152 = LOG OUT. Seen live 2026-08-22 -- the account holder clicked Log
#: Out and the client HUNG, which is the worst placement for an unanswered
#: message: it is the only clean way out of the game.
#:
#: 4-byte payload, built at 0x61173E08 and sent from 0x61173DD0, which is gated
#: on `lobby+0x20 == 4 && lobby+0x24 == 2` -- i.e. only offered from the in-game
#: state, which is why it never appeared during character-select testing.
#:
#: Answered with an empty message 1, the same generic convention 0x61173210
#: stamps and the same one 0x0182 (Play Time) and 0x0181 confirmed live. There
#: is no `cmp word [reg+6]` anywhere in its sender's function, so nothing tests
#: for a specific id inline.
#:
#: WARNING: We do NOT tear anything down on our side. The client will believe it has
#: logged out; the session, the world channel and the character store are all
#: untouched. That is the FMO_ANSWER_LOBAPI debt again, and here it is mild --
#: the client drops the connection itself right afterwards, which our own
#: close path already handles.
MSG_LOGOUT_REQ = 0x0152

#: 0x016E -> 0x016F. Pairs N/N+1, unlike 0x0150.
MSG_MOVE_LIST_REQ = 0x016E
MSG_MOVE_LIST_REPLY = 0x016F
MSG_MOVE_REQ = 0x016D                  # -> MSG_0150_REPLY (0x0153), the grant
#: VERIFIED: 0x0170 = SET HANGAR PASSWORD -- seen inbound for the first time 2026-09-09
#: 01:30:30Z when a player used Hangar > Set Password: 228 B, +0x00 the NEW
#: password as a NUL string ("6264"), +0x10 the CURRENT one (lobby+0x1000, the
#: 0x014A +0x738 field we had never served, so empty), the rest zero. The move
#: machine's states 100..102 send it and poll for MESSAGE 1 on the request's
#: own sequence (0x61190D1F); unanswered it hung the client. SE: one hangar per
#: pilot, private, others enter by name+password while the owner is inside.
MSG_HANGAR_PW = 0x0170
HANGAR_PW_LEN = 0xE4
HANGAR = (os.environ.get("FMO_HANGAR", "").strip() or "1") != "0"


def parse_hangar_pw(payload):
    """(new_password, current_password) from a 0x0170 payload."""
    def cstr(off):
        return payload[off:off + 0x10].split(b"\0")[0].decode("ascii", "replace")
    if len(payload) < 0x20:
        return "", ""
    return cstr(0x00), cstr(0x10)

MOVE_IDS_OFF = 0x10                    # u32 per entry
MOVE_PEOPLE_OFF = 0x1F50               # u8 per entry, parallel array
MOVE_SLOTS = (MOVE_PEOPLE_OFF - MOVE_IDS_OFF) // 4       # 2000
REPLY_016F_LEN = MOVE_PEOPLE_OFF + MOVE_SLOTS            # 0x2720 = 10016
MOVE_UI_ROWS = 0xC8                    # what the widget reserves: 200

#: Offsets in the 72-byte 0x016D, every one a store in the builder at
#: 0x61190A67..0x61190AA0.
MOVE_REQ_LEN = 0x48
M16D_FIELD_00 = 0x00                   # u32, = the machine's `this+0x3D`
M16D_FIELD_04 = 0x04                   # u32, the PICKED ENTRY'S ID (not a row index)
M16D_CHOICE = 0x04                     # u32, = `this+0x41` -- the picked row
M16D_NUMBER = 0x08                     # 16B, sprintf("%d", ...) of a UI value
M16D_NAME_A = 0x18                     # 17B NUL-terminated
M16D_NAME_B = 0x29                     # 17B NUL-terminated
MOVE_NAME_LEN = 17

#: WARNING: THE LIST WE SERVE IS A PROBE. We keep no FMO lobby or room registry, so
#: the honest answer is COUNT 0 -- and count 0 is not a failure: state 1 turns
#: it into the Confirmation box "There is nobody in that <kind> right now. Move
#: there anyway?", which is a legitimate screen with a way out, not a hang.
#:
#: FMO_MOVE_LIST="1:3,2:0" serves rows instead, `id:people` each. That exists to
#: CONFIRM the column decode above from the screen -- if 1 and 3 appear under
#: "Lobby ID" and "People", the inference is measured; if they appear swapped or
#: not at all, it is refuted. Nothing else should use it.
MOVE_LIST = [e for e in os.environ.get("FMO_MOVE_LIST", "").replace(" ", "")
             .split(",") if e]

#: KEY: `+0x00` OF 0x016E/0x016D IS A CATEGORY (2026-08-26, live + static). The
#: Move dialog machine sends its own `this+0x3D` there (0x6119072C / 0x61190A67)
#: and its list-dialog mode byte `this+0x30` picks the strings: 0 lobby, 1 room,
#: 2 BRIEFING ROOM (state 1 at 0x611907EF: 0 -> 0xC00F0001, 1 -> 0xC00F0002,
#: 2 -> 0xC00F000A "There is nobody in the briefing room right now"; the
#: builder 0x611905B6 gives mode 0 "Lobby ID"/"Change Lobbies" and modes 1 AND 2
#: "Room ID"/"Change Rooms"). Live 2026-08-26 22:00:32Z the Briefing Room entry
#: sent +0x00=2, and this server handed it the LOBBY rows below.
#:
#: KEY: A BRIEFING ROOM ROW HAS NO KIND FIELD. The only reader of 0x016F,
#: 0x61190070, takes `count`, 2000 u32 ids and 2000 u8 people and nothing else,
#: and state 4 (0x61190B50) hands the 0x0153 grant to the scene selector
#: 0x61006350 WITHOUT the category. What makes a destination a briefing room is
#: the grant itself: `0x0153 +0x18` -> globals+0x1AC is the ZONE KIND, and BOTH
#: kind-name functions in the image (0x611D8F83 and 0x611DE467, feeding
#: systext group 35 ids 50-55) switch on it: 0 Lobby, 1 Room, 2 Briefing Room,
#: 3 Room B, 4 Room C, >4 Hangar. We have sent 1 ("Room") in every grant so far
#: (FMO_0153_F18). So the Briefing Room needs (a) its own rows here and (b) a
#: grant whose +0x18 is 2 -- MOVE_KIND_BRIEFING below. Which MapNo/MapKind SE
#: paired with kind 2 is still unmeasured; the rows are the knob for that.
MOVE_CATEGORY_BRIEFING = 2
MOVE_CATEGORY_NAMES = {0: "lobby", 1: "room", 2: "briefing room"}
#: `id[:people]` rows served ONLY to category 2. Empty is a legitimate answer:
#: count 0 renders SE's own "There is nobody in the briefing room right now.
#: Move there anyway?" (0xC00F000A), and "anyway" then sends a 0x016D whose
#: +0x04 is whatever the client had -- which VALID_MAPNOS gates.
MOVE_LIST_BRIEFING = [e for e in os.environ.get("FMO_MOVE_LIST_BRIEFING", "")
                      .replace(" ", "").split(",") if e]
#: The zone kind (0x0153 +0x18) a category-2 move is granted with. Default 2 =
#: "Briefing Room" in the client's own enum. -1 leaves FIELD_18 alone, which is
#: the A/B: same rows, same MapNo, only this dword differs.
MOVE_KIND_BRIEFING = _env_int("FMO_MOVE_KIND_BRIEFING", "2")
#: KEY: THE ROOM LIST (2026-09-09). Category 1 = "Change Room" used to get the
#: lobby set -- every one of the twelve maps served as a lobby. A walk through them
#: plus the containers' texture tags sort them: 102 the lobby (every
#: lobby script's marks fit it), 122/123 the BRIEFING ROOM (`prmhq_monitor`,
#: `prmhq_room_comp` textures -- an HQ room with monitors), 141..144 hangars,
#: 124 a bar, 151 corridors, 101 a hangar hall, 161 the Coliseum dome. Empty
#: falls back to FMO_MOVE_LIST exactly as before.
MOVE_LIST_ROOM = [e for e in os.environ.get("FMO_MOVE_LIST_ROOM", "")
                  .replace(" ", "").split(",") if e]
MOVE_CATEGORY_ROOM = 1


def move_list_for(category):
    """The rows for one 0x016E category, plus the NAME of the knob they came
    from -- so the log says which set was served and why, every time."""
    if category == MOVE_CATEGORY_BRIEFING:
        return (parse_move_list(MOVE_LIST_BRIEFING),
                "FMO_MOVE_LIST_BRIEFING (category 2 = briefing room)")
    if category == MOVE_CATEGORY_ROOM and MOVE_LIST_ROOM:
        return (parse_move_list(MOVE_LIST_ROOM),
                "FMO_MOVE_LIST_ROOM (category 1 = room)")
    cat = MOVE_CATEGORY_NAMES.get(category, "unknown")
    return (parse_move_list(MOVE_LIST),
            f"FMO_MOVE_LIST (default set; category {category} = {cat})")


#: KEY: PLACES (2026-09-09): SE's own hierarchy, from the archived
#: www.playonline.com/fmo guide + the client's menus (zones,
#: areas, lobbies, rooms): a ZONE holds AREAS; every area has its own
#: LOBBY, in numbered INSTANCES ("Lobby ID: 158"); off the lobby hang numbered
#: ROOMS (the training sergeant stands in one), the STRATEGY ROOM ("Briefing
#: Room", only in frontline area 10 = zone 509, rank-gated) and each pilot's
#: private HANGAR. Until now the Move lists were MapNos in disguise: pick
#: "Lobby 141" and you were granted map 141, every map was a lobby, and the
#: head counts were typed into .env.
#:
#: A place is (zone, kind, instance). kind is the client's own name table
#: (systext 35:50..55, the 0x0153 +0x18 byte): 0 Lobby, 1 Room, 2 Briefing
#: Room, 3 Room B, 4 Room C, 5 Hangar. The u32 the 0x016F list carries -- and
#: 0x016D echoes -- is `kind*1000 + instance`, so a lobby is 1, 2, 3.. and the
#: rooms read 1001.., 3001.., 4001.. on the client's "Room ID" column (the list
#: has no name column; the number is all the player sees). Instances exist by
#: being stood in: the list offers every occupied instance plus one empty one
#: (or a fresh one when every occupied instance is at FMO_PLACE_CAP), and the
#: People column is who actually has a live world channel there.
#:
#: The map a place stands in: lobby = FMO_ZONE_MAPNO (102), rooms by kind from
#: FMO_ROOM_MAPS. No script E200-attaches a room map on its own, so the map is
#: the server's choice; the defaults (2026-10-06) follow the scripts that DO
#: stage people in them:
#:   Room (1)          121 -- the room scripts stage the player at (4.0, 0,
#:                     1.56) and the NPC at (5.42, 0, 1.56) facing 270, open
#:                     floor on 121 (on 124, the bar, that spot clips)
#:   Briefing Room (2) 122 for O.C.U., 123 for U.S.N. -- the shared script
#:                     helper (e.g. AI/F00/D19 at 0x1C46) asks 0xE060 for the
#:                     nation and E200s 122 when it is 1, 123 otherwise. The
#:                     branch sense is read from the compiled if/else shape
#:                     (compare, setcc, branch-if-zero over the 122 arm), the
#:                     same shape D87's cast switch uses; the VM ops themselves
#:                     are not decoded from their handlers.
#:   Room B / C (3, 4) 124 -- no data names them; the bar is a real map
#:   Hangar (5)        141 (141..144 grow with size; unproved ladder)
#: Grammar: `kind:mapno` or, per nation, `kind:ocu/usn`.
#: FMO_PLACES=0 restores the FMO_MOVE_LIST* behaviour exactly.
PLACES = (os.environ.get("FMO_PLACES", "").strip() or "1") != "0"
PLACE_KIND_NAMES = {0: "Lobby", 1: "Room", 2: "Briefing Room", 3: "Room B",
                    4: "Room C", 5: "Hangar"}
#: Move category -> the place kinds its list shows.
PLACE_CATEGORY_KINDS = {0: (0,), 1: (1, 3, 4), 2: (2,)}
PLACE_CAP = _env_int("FMO_PLACE_CAP", "50")
#: Zones that HAVE a strategy room. SE: "the strategy room is installed only in
#: Frontline Zone Area 10" -- which is the client's own rank>=21 && MapKind==509
#: gate on the Briefing Room menu entry.
BRIEFING_ZONES = tuple(int(x, 0) for x in
                       (os.environ.get("FMO_BRIEFING_ZONES", "").strip() or "509")
                       .replace(" ", "").split(",") if x)


ROOM_MAPS_DEFAULT = "1:121,2:122/123,3:121,4:121,5:141"


def parse_room_maps_nation(spec):
    """`kind:mapno` / `kind:ocu/usn`, comma-separated, over the defaults ->
    ({kind: O.C.U. mapno}, {kind: U.S.N. mapno}); a kind with one map has the
    same map in both. Every map must exist (VALID_MAPNOS)."""
    ocu, usn = {}, {}
    for src in (ROOM_MAPS_DEFAULT, spec or ""):
        for e in src.replace(" ", "").split(","):
            if not e:
                continue
            k, _, m = e.partition(":")
            m1, _, m2 = m.partition("/")
            try:
                k, m1 = int(k, 0), int(m1, 0)
                m2 = int(m2, 0) if m2 else m1
            except ValueError:
                raise SystemExit(f"FMO_ROOM_MAPS entry {e!r}: wants <kind>:<mapno> "
                                 f"or <kind>:<ocu>/<usn>")
            if k not in PLACE_KIND_NAMES or k == 0:
                raise SystemExit(f"FMO_ROOM_MAPS entry {e!r}: kind must be 1..5")
            for mm in (m1, m2):
                if mm not in zoneentry.VALID_MAPNOS:
                    raise SystemExit(f"FMO_ROOM_MAPS entry {e!r}: MapNo {mm} is not one of "
                                     f"the twelve maps {zoneentry.VALID_MAPNOS}")
            ocu[k], usn[k] = m1, m2
    return ocu, usn


def parse_room_maps(spec):
    """{kind: mapno} as an O.C.U. pilot sees it (parse_room_maps_nation)."""
    return parse_room_maps_nation(spec)[0]


ROOM_MAPS, ROOM_MAPS_USN = parse_room_maps_nation(os.environ.get("FMO_ROOM_MAPS", ""))
#: KEY: THE COLISEUM'S ROOM IS THE BAR (2026-10-07). Map 124 (a bar: counter,
#: 11 stools, bottle shelves) first ships in SE's 2006-07-25 patch, the August
#: 2006 Coliseum version-up, and SE's Phase 02 report has a player saying an
#: eatery is opening at the arena (topics060815). No client data binds 124 to
#: a zone or room kind, so SE's server chose it: here, Change Room inside the
#: Coliseum zones (600..607) takes the room kinds below to the bar instead of
#: FMO_ROOM_MAPS. Where exactly SE put it is a GUESS. '' / '0' = off.
COLISEUM_ZONES = (600, 607)
_ROOM_COL_SPEC = os.environ.get("FMO_ROOM_MAPS_COLISEUM", "").strip()
ROOM_MAPS_COLISEUM = ({} if _ROOM_COL_SPEC == "0" else
                      {k: m for k, m in parse_room_maps_nation(_ROOM_COL_SPEC or "1:124")[0].items()
                       if k in ((1,) if not _ROOM_COL_SPEC else
                                tuple(int(e.split(":")[0], 0) for e in _ROOM_COL_SPEC.split(",") if e))})
#: host -> (zone, kind, instance): the place its last 0x0153 put it in.
WORLD_PLACES = {}


def place_id(kind, instance):
    return kind * 1000 + instance


def parse_place_id(pid):
    """-> (kind, instance) or None for an id that is not a place."""
    kind, inst = divmod(int(pid), 1000)
    if kind in PLACE_KIND_NAMES and 1 <= inst <= 999:
        return kind, inst
    return None


def place_name(place):
    if not place:
        return "no place"
    zone, kind, inst = place
    return f"zone {zone} {PLACE_KIND_NAMES.get(kind, kind)} #{inst}"


def place_map(zone, kind, default=None, nation=None):
    """(mapno, why) for a place: the zone's lobby map for kind 0, else the
    room kind's map, the U.S.N. one when `nation` is 2."""
    if kind == 0:
        return areachange.zone_mapno(zone, zoneentry.MAPNO if default is None else default, "FMO_MAPNO")
    if (zone is not None and COLISEUM_ZONES[0] <= int(zone) <= COLISEUM_ZONES[1]
            and kind in ROOM_MAPS_COLISEUM):
        return ROOM_MAPS_COLISEUM[kind], (f"FMO_ROOM_MAPS_COLISEUM[{kind} "
                                          f"{PLACE_KIND_NAMES[kind]}] (the arena's bar)")
    if nation == 2:
        return ROOM_MAPS_USN[kind], f"FMO_ROOM_MAPS[{kind} {PLACE_KIND_NAMES[kind]}, U.S.N.]"
    return ROOM_MAPS[kind], (f"FMO_ROOM_MAPS[{kind} {PLACE_KIND_NAMES[kind]}"
                             + (", O.C.U.]" if nation == 1 else "]"))


def place_population(peers=None, places=None, now=None):
    """{place: people} over hosts with a LIVE world channel (same liveness
    rule as room_mates: not left, seen within ROOM_TTL). Pure given its
    arguments; the defaults read the live tables."""
    peers = groupchannel.WORLD_PEERS if peers is None else peers
    places = WORLD_PLACES if places is None else places
    now = time.time() if now is None else now
    out = {}
    hosts = set()
    for a, ch in peers.items():
        if ch.key is None or ch.key == groupchannel.GROUP_KEY or ch.left:
            continue
        if now - ch.seen_at > room.ROOM_TTL:
            continue
        hosts.add(a[0])
    for h in hosts:
        pl = places.get(h)
        if pl:
            out[pl] = out.get(pl, 0) + 1
    return out


def place_rows(zone, category, population=None):
    """[(id, people)] for one Move category in `zone`: every occupied
    instance of each kind the category shows, plus the lowest empty instance
    (so a fresh one can always be opened). Category 2 is empty
    outside BRIEFING_ZONES. Pure given `population`."""
    pop = place_population() if population is None else population
    kinds = PLACE_CATEGORY_KINDS.get(category, ())
    if category == 2 and zone not in BRIEFING_ZONES:
        kinds = ()
    rows = []
    for kind in kinds:
        insts = {inst: n for (z, k, inst), n in pop.items() if z == zone and k == kind}
        # always one empty instance on offer: SE's "There is nobody in that
        # lobby right now. Move there anyway?" is the way to open a fresh one
        nxt = 1
        while nxt in insts:
            nxt += 1
        insts[nxt] = 0
        for inst in sorted(insts):
            rows.append((place_id(kind, inst), insts[inst]))
    return rows[:MOVE_SLOTS]


def resolve_place_pick(zone, category, picked):
    """The place a 0x016D pick names, or (None, why). The id must be a place
    id of a kind the category lists; the instance may be one nobody stands
    in yet (the client just picked the empty row)."""
    if picked is None:
        return None, "no pick in the request"
    pk = parse_place_id(picked)
    if pk is None:
        return None, f"id {picked} is not a place id (kind*1000+instance)"
    kind, inst = pk
    kinds = PLACE_CATEGORY_KINDS.get(category, ())
    if category == 2 and zone not in BRIEFING_ZONES:
        return None, (f"zone {zone} has no strategy room (FMO_BRIEFING_ZONES="
                      f"{','.join(map(str, BRIEFING_ZONES))})")
    if kind not in kinds:
        return None, (f"{PLACE_KIND_NAMES[kind]} is not a kind category "
                      f"{category} lists ({[PLACE_KIND_NAMES[k] for k in kinds]})")
    return (zone, kind, inst), "the client's own pick from the list we served"


def entry_place(zone):
    """Where a fresh entry into `zone` lands: the lowest-numbered lobby
    instance with room in it."""
    pop = place_population()
    inst = 1
    while pop.get((zone, 0, inst), 0) >= PLACE_CAP:
        inst += 1
    return (zone, 0, inst)


def move_kind_for(category):
    """The 0x0153 +0x18 zone kind to grant a move of this category with, or
    None to keep FIELD_18. Only category 2 is overridden -- one field per
    experiment."""
    if category == MOVE_CATEGORY_BRIEFING and MOVE_KIND_BRIEFING >= 0:
        return MOVE_KIND_BRIEFING
    return None


#: The 0x01AB lesson has a switch of its own (FMO_ANSWER_LOBAPI) because an
#: acknowledgement can assert something the server did not do. Same here: a
#: 0x0153 for 0x016D says "you have moved", and nothing moved.
ANSWER_MOVE = os.environ.get("FMO_ANSWER_MOVE", "1") != "0"

#: WARNING: THE MapNo THE MOVE GRANTS, and the reason this knob exists at all. Unset,
#: 0x016D gets exactly what 0x0150 gets -- which is the zone the player is
#: ALREADY IN, so Move re-enters the same base and the question it was decoded
#: to answer (does terrain draw anywhere else?) goes untested. Setting it is
#: what turns Move into a zone CHANGE.
#:
#: WARNING: Deliberately NOT the same lever as FMO_MAPNO_SWEEP. The sweep is one shared
#: counter stepped by every 0x0153 from every session, so a client that sends a
#: second 0x0150 -- a retry, a second connection -- silently takes the move's
#: value for its INITIAL join and lands somewhere the render has never been
#: proven. This knob cannot do that: it touches the move and nothing else.
#:
#: WARNING: And it is still not the destination the client ASKED for. 0x016D's +0x04
#: carries the picked row; we ignore it. This is a probe with a fixed answer.
MOVE_MAPNO = os.environ.get("FMO_MOVE_MAPNO", "")
MOVE_MAPNO = int(MOVE_MAPNO, 0) if MOVE_MAPNO else None


def parse_move_list(spec):
    """`["1:3", "2"]` -> [(1, 3), (2, 0)]. Bad entries are dropped, loudly."""
    out = []
    for e in spec:
        ident, _, people = e.partition(":")
        try:
            out.append((int(ident, 0), int(people, 0) if people else 0))
        except ValueError:
            log("   FMO_MOVE_LIST entry %r is not `id[:people]` -- dropping it "
                "rather than serving a zero for it" % (e,))
    return out[:MOVE_SLOTS]


def reply_016f(entries=None):
    """The 0x016F payload: a count, then 2000 u32 ids, then 2000 u8 counts.

    Always the full fixed size. The client reads `payload+0x1F50+i` without ever
    consulting a length, so a short reply is only safe while the count is 0 --
    and a reply whose safety depends on the value inside it is the kind that
    breaks later, at the point of USE, exactly as the zero-filled 0x0322 did."""
    rows = parse_move_list(MOVE_LIST) if entries is None else entries
    b = bytearray(REPLY_016F_LEN)
    struct.pack_into("<i", b, 0, len(rows))
    for i, (ident, people) in enumerate(rows):
        struct.pack_into("<I", b, MOVE_IDS_OFF + 4 * i, ident & 0xFFFFFFFF)
        b[MOVE_PEOPLE_OFF + i] = people & 0xFF
    return bytes(b)


def describe_move_req(payload):
    """What the client asked for, field by field -- the instrument, not a guess.

    An unexpected value here is news about the client, not about this function,
    because every offset it reads is a store in the client's own builder."""
    if len(payload) < MOVE_REQ_LEN:
        return "%dB, expected %d -- NOT decoding it" % (len(payload),
                                                        MOVE_REQ_LEN)
    f00, choice = struct.unpack_from("<II", payload, M16D_FIELD_00)
    num = payload[M16D_NUMBER:M16D_NUMBER + 16].split(b"\0")[0]
    a = payload[M16D_NAME_A:M16D_NAME_A + MOVE_NAME_LEN].split(b"\0")[0]
    b = payload[M16D_NAME_B:M16D_NAME_B + MOVE_NAME_LEN].split(b"\0")[0]
    return ("+0x00=%d (the same field 0x016E carried)  +0x04=%d (the picked "
            "row)  +0x08=%r (a decimal string)  names=%r/%r"
            % (f00, choice, num.decode("cp932", "replace"),
               a.decode("cp932", "replace"), b.decode("cp932", "replace")))

#: Play Time (0x0182 -> 0x0183). Default ON and default HONEST: the number we
#: serve is the pilot's own accumulated seconds, banked in the character store,
#: not a knob. FMO_PLAYTIME=0 serves FMO_PLAYTIME_BASE alone and stops writing
#: to the store -- the state to run in when a measurement must not have a
#: side effect on the roster.
#: FMO_PLAYTIME_BASE is added to whatever is banked. It is the SEED for a pilot
#: that predates this accounting (every character on file before 2026-09-08 has
#: no `play_seconds` at all, and 0 is honest for them), and the way to put a
#: chosen number on screen for a screenshot. Seconds.
PLAYTIME = os.environ.get("FMO_PLAYTIME", "1") != "0"
PLAYTIME_BASE = _env_int("FMO_PLAYTIME_BASE", 0)


# Called at run time only; imported last so that import cycles resolve.
from . import areachange, groupchannel, room  # noqa: E402
