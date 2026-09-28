"""The character-select screen's own requests and the nation select (0x019C -> 0x019D)."""
import os
import struct
from .knobs import _env_int


# --------------------------------------------------------------------------- #
# the CHARACTER-SELECT screen's own requests
# --------------------------------------------------------------------------- #
# These do NOT go through the lobby API above -- no request object, no
# `[this+0x0C]`, no 0x61172250. They are plain methods on the LOGIN object
# ([globals+0x190]) driven by a small per-screen state machine, and each machine
# tests the reply id itself. So the id is found by reading that machine, one at
# a time; there is no table to enumerate.
#
# VERIFIED: 0x013F IS "Delete Character" -- live-verified 2026-08-20 (the user clicked
# it and this went out: 4 bytes, `01 00 00 00`, the id of the character we serve
# in the 0x012F list). Sent by 0x611739A0, consumed by the machine at
# 0x611849B0:
#
#     cmp word [edi+6], 1     ; the reply's message id
#     jne 0x61184A1D          ; NOT 1 -> error code = word [rx+8], UI event
#                             ;   0x1075 = "Character delete failed"
#     ...                     ; == 1  -> 0x61173A10 drops that id from the
#                             ;   LOCAL character list, UI event 0x1074 =
#                             ;   "Character deleted"
#
# WARNING: NOTE THE POLARITY. Here `== 1` is success. For 0x0130 (0x6117B1A3) it is the
# other way round -- message 2 is the FAILURE and anything else succeeds. The
# two conventions live side by side in the same screen, so neither generalises.
#
# WARNING: THE CLIENT'S HALF OF THE DELETE IS LOCAL: 0x61173A10 edits the list it is
# holding. Whether that STICKS is ours -- with FMO_CHAR_STORE set we remove the
# character too, and with it empty we do not, and the character reappears at the
# next login off a synthesised list.
#
# WARNING: THIS PARAGRAPH USED TO SAY FLATLY "we store nothing", AND THE LOG SAID SO
# TOO, FOR HOURS AFTER THE STORE LANDED. Another worker read that line, believed
# the write path was missing, hand-seeded data/fmo_characters.json and added an
# .env override to put the synthetic list back (ff0e699b). **A stale log line is
# not a cosmetic defect -- it is a false measurement, and this repo acts on
# measurements.** When behaviour moves behind a switch, every line that asserts
# the old behaviour has to move behind the same switch in the same commit.
# VERIFIED: 0x0177 = the NAME step of character creation -- live 2026-08-20. Built at
# 0x61178D30 from `lobby+0x741E`, and it copies ONLY three things: the id from
# `lobby+0x7426` to +0x00, and two NUL-terminated strings to +0x04 and +0x15.
# Its 56 bytes carried `01 00 00 00 "Fox" ... "Noted"` and **nothing else**,
# even though the player had already picked a nation and a face.
#
# WARNING: THAT LOOKED LIKE A REFUTATION AND WAS NOT. On the strength of this message
# alone I retired two readings -- "+0x26 is the nation" (it was 0 after an OCU
# pick) and "the record's spare bytes carry the creation form" (they were 0).
# **Both were right; 0x0177 simply does not carry those fields.** It copies the
# id and two strings and nothing else, so every other byte in it is whatever the
# buffer already held. A message that omits a field is not evidence about that
# field. 0x013E below carries them and settles it.
#
# Consumed at 0x61178BF0, same shape as the delete:
#
#     cmp word [edi+6], 1     ; == 1 -> success, and the success arm reads
#     jne 0x61178CBF          ;   NOTHING from the payload: it walks the LOCAL
#                             ;   list (count at +0x208, entries +0x20C stride
#                             ;   0x34), finds the entry whose id matches
#                             ;   lobby+0x7426, and writes the two names INTO
#                             ;   it. So an empty message 1 is the whole reply.
#
# The failure arm takes `word [rx+8]` as the error code and then compares it
# against **0xC43B and 0xC90E** specifically -- two named errors with their own
# return values (0xFFFFFFFB and one more at 0x61178D05). Those are the first
# concrete FMO error codes we have seen; nothing here says what they mean.
#
# VERIFIED: 0x013E = THE CREATE SUBMIT, 80 bytes -- seen live 2026-08-20 once the name
# step was answered and the flow reached the Wanzer and hangar-password screens.
# The sender picks between the two on `[[lobby+0x741E]] == 0`: record id set ->
# 0x0177, record id zero (a NEW character) -> 0x013E. Built at 0x61178D8F, and
# unlike 0x0177 it carries the whole record. Measured against an OCU pick:
#
#   +0x00  u32   the slot id (from lobby+0x7426)      01 00 00 00
#   +0x04  17B   first name                           "Fox"
#   +0x15  17B   last name                            "Noted"
# NAMED BY A DIFFERENTIAL: two characters created with deliberately different
# choices, 2026-08-20. Only the bytes that MOVED can be named, and only as far
# as the two runs differ -- the columns are what was actually on the wire.
#
#                              run 1              run 2
#   +0x26  u8  NATION          01  OCU            02  USN
#   +0x27  u8  always 0        00                 00
#   +0x28  u8  SEX             01  male           02  female
#   +0x29  u8  ?               01                 01     (did not move)
#   +0x2A  u8  constant 1      01                 01
#   +0x2B  u8  constant 1      01                 01
#   +0x2C  u16 constant 2000   d0 07              d0 07
#   +0x2E  u16 constant 101    65 00              65 00
#   +0x30  u32 APPEARANCE      01 03 6b 00        03 05 6e 00
#   +0x34  u32 ?               01 00 00 00        01 00 00 00
#   +0x38  u8  constant 1      01                 01
#   +0x39  u8  CLASS           03  Mechanic(3rd)  02  Missileer(2nd)
#   +0x3A  u16 HANGAR PASSWORD 78 18 (6264)       57 04 (1111)
#   +0x3C  u8  0x14 when lobby+0x50 is non-zero, else 0
#
# VERIFIED: **+0x3A IS THE HANGAR PASSWORD, PLAIN LITTLE-ENDIAN u16.** The player typed
# 1111 and the wire carried 0x0457. Decisive on one sample because it is an
# exact numeric match to a freely chosen value -- no hash, no obfuscation, and
# it rides the same message as everything else.
# VERIFIED: **+0x39 is the CLASS as a 1-BASED OPTION INDEX** -- 3 = Mechanic, 2 =
# Missileer. Confirmed as a prediction made before the second payload arrived.
# VERIFIED: **+0x26 NATION: 1 = OCU, 2 = USN**, three independent picks agreeing.
# VERIFIED: **+0x28 SEX: 1 = male, 2 = female.**
#
# WARNING: **+0x30's three bytes are head / build / height and WHICH IS WHICH IS NOT
# KNOWN.** They moved together because the player changed all three at once
# (01 03 6b -> 03 05 6e). Splitting them needs a run that changes exactly one.
# WARNING: +0x29 and +0x34 did not move and are therefore UNIDENTIFIED, not constant --
# two samples cannot tell a constant from a field nobody varied.
# WARNING: There is no birthday here. `Select day` / `Select Month` in .rdata belong to
# some other form; this creation flow never asked for one.
#
# It shares 0x0177's state machine (both jump to 0x61178E54), so it is consumed
# at the same 0x61178BF0 `cmp word [edi+6], 1` and its success arm also reads
# nothing from the payload -- with a zero record id it takes the OTHER branch at
# 0x61178C03 and walks the local list. An empty message 1 again.
#: A NUL as a name, so the terminator never has to be written as an escape.
#: A shell heredoc ate the backslash in `b"\x00"` once and put a REAL null byte
#: into this file; Python then refused to import it at all.
NUL = bytes(1)

#: request id -> (reply id, payload bytes the client reads, what it is)
CHARSEL = {
    0x013F: (0x0001, 0, "DELETE CHARACTER"),
    0x0177: (0x0001, 0, "CHARACTER NAME (creation step)"),
    0x013E: (0x0001, 0, "CREATE CHARACTER (the full record)"),
}

# --------------------------------------------------------------------------- #
# 0x019C / 0x019D -- the NATION SELECT screen's data
# --------------------------------------------------------------------------- #
# Live 2026-08-20: with a character list the client had emptied, clicking
# "Create character" sent 0x019C (0 bytes) and the client then failed with
# [FM00000] -- code 0, i.e. NO code was set, i.e. a LOCAL precondition, not a
# rejection of ours. We were answering with 36 zeros.
#
# The 36 bytes land at screen+0x1AD, and two consumers read them:
#
#   0x61013420   edx=[+0x1AD] ecx=[+0x1B1]; ecx += edx; if (ecx > 0)
#                edi = 100 - (edx / ecx) * 100.0        <- a PERCENTAGE SHARE
#                then sign-tests [+0x1B5] and [+0x1B9] and writes a mode 1..4
#                to screen+0x11D (4 if [+0x1B5] < 0, 3 if [+0x1B9] < 0).
#   0x61015514   al = byte [+0x1BD]; test al,al; je -> SKIPS the whole
#                population display.
#
# So the block is
#
#   +0x00  u32  nation A population      \ their sum is the denominator, so
#   +0x04  u32  nation B population      / zero/zero means "no nations"
#   +0x08  s32  a flag, NEGATIVE selects display mode 4
#   +0x0C  s32  a flag, NEGATIVE selects display mode 3
#   +0x10  u8   ENABLE -- zero and the screen draws no nations at all
#   +0x11..+0x23  no identified reader
#
# WARNING: THIS IS A HYPOTHESIS ABOUT THE REFUSAL, NOT A PROVEN CAUSE. What is
# measured: the field meanings above, and that we sent zeros and the client
# refused locally. What is NOT measured: that these values are what it wants.
# The percentages are cosmetic on their face -- an empty denominator and a clear
# ENABLE byte are the two things that could plausibly leave the screen with
# nothing selectable, and they are what these defaults change. If a retry still
# says [FM00000], the cause is elsewhere and this should be set back to zeros
# rather than tuned.
NATION_POP = (os.environ.get("FMO_NATION_POP", "").strip() or "1000,1000")
NATION_ENABLE = _env_int("FMO_NATION_ENABLE", "1", 10)
#: Change Nations (0x01AA) carries no target nation (+0x28 is 0 in every submit
#: on record), so the switch is to the OTHER one. 0 = leave the nation alone.
NATION_CHANGE_TOGGLE = os.environ.get("FMO_NATION_CHANGE_TOGGLE", "1") != "0"
REPLY_019D_LEN = 36


def reply_019d():
    """The 36 bytes at screen+0x1AD. All-zero with FMO_NATION_POP=0,0 and
    FMO_NATION_ENABLE=0, which is what every measurement before 2026-08-20
    ran against."""
    b = bytearray(REPLY_019D_LEN)
    try:
        a, c = (int(x) for x in NATION_POP.split(",")[:2])
    except ValueError:
        a = c = 0
    struct.pack_into("<II", b, 0x00, a & 0xFFFFFFFF, c & 0xFFFFFFFF)
    # +0x08 / +0x0C stay 0: both are sign-tested and only a NEGATIVE value
    # changes the mode, so zero is the neutral choice, not a placeholder.
    b[0x10] = NATION_ENABLE & 0xFF
    return bytes(b)


#: 0x019C's reply is built, not zero-filled -- see above.
MSG_NATION_REQ = 0x019C

#: Acknowledge lobby API requests? Default ON: unanswered, the menu action just
#: hangs the UI with no way back. FMO_ANSWER_LOBAPI=0 restores the silence, which
#: is the state every measurement before 2026-08-19 was taken in.
ANSWER_LOBAPI = os.environ.get("FMO_ANSWER_LOBAPI", "1") != "0"

#: 0x0198 is the KEEPALIVE (0x6117C2E0, through the periodic outbound queue at
#: ctx+0x7A0E). It carries a unix timestamp and a counter, always with the fixed
#: sequence 0x7FFFFFFE, and it is NOT one of the objects above -- nothing polls
#: for a reply to it. Named here only so the log stops calling it "unknown".
MSG_KEEPALIVE = 0x0198
MSG_LOG_UPLOAD = 0x01A5              # see the 0x01A5 arm in on_packet

#: The generic FAILURE reply. Any id the waiting request is not expecting takes
#: the failure arm; 2 is the one the client's own paths use (0x0130's failure is
#: `cmp word [rx+6], 2`). The reply's CONNECTION-ID field carries the code.
MSG_FAIL = 0x0002
#: What to put in +0x08 when we refuse. Display only -- see MSG_FAIL's note and
#: the failure-reply logging. 0xC43B and 0xC90E are the only codes the client is
#: known to test for by value (0x61178CDE), so this deliberately is not one of
#: them: we do not know what they mean and should not claim them.
FAIL_CODE = _env_int("FMO_FAIL_CODE", "1")

#: The one lobby API command tied to a button by OBSERVATION rather than by
#: reading the image: the user clicked "Change Nations" on 2026-08-18 and this
#: is the request that went out.
MSG_CHANGE_NATIONS = 0x01AB
