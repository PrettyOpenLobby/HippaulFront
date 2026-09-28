"""Names and sex on a POP: who is online and what each pilot's POP carries."""
import os
import time


def online_players():
    """(first, last) -> host, for everyone currently in a world channel.

    WARNING: this is "in a lobby", NOT "logged in": a client sitting on
    character select has no world channel and will read as absent. That is the
    right answer for a room member check and the wrong one for a buddy list, so
    do not reuse it for presence generally without saying so."""
    now = time.time()
    out = {}
    for a, ch in list(groupchannel.WORLD_PEERS.items()):
        if ch.key is None or now - ch.seen_at > room.ROOM_TTL:
            continue
        with worldchannel._as_world_channel(a):           # each channel's OWN player
            n1, n2, _src = pop_names_for(a[0])
        out[(n1.strip().lower(), n2.strip().lower())] = a[0]
    return out


def member_check_names(payload):
    """The two NUL-terminated names 0x61179ABD / 0x61179AD0 write."""
    def _s(off):
        return payload[off:off + 0x11].split(bytes(1))[0].decode("cp932",
                                                                 "replace")
    return _s(0x10), _s(0x21)


def pop_names_for(host_ip):
    """The (first, last) the POP should carry for a channel from `host_ip`.

    `account_for` now falls through to a fresh POL-member lookup when the
    login-time carry has aged out (IDENTITY_TTL), so a re-pop long after login
    still names the right roster as long as the account database has the session row.
    A remaining fallback to the placeholders is logged by the caller via the
    returned `source` so a wrong name on screen is attributable from the log
    alone."""
    if popself.POP_ROSTER_NAME:
        acct = identity.account_for(host_ip)
        named = [c for c in charstore.load_roster(acct)
                 if c.get("first") or c.get("last")]
        if named:
            return named[0]["first"], named[0]["last"], f"store:{acct[:8]}"
    return popself.POP_NAME1, popself.POP_NAME2, "env placeholder"


def pop_sex_for(host_ip):
    """(body+0x7A, source) for `host_ip`'s character -- their own SEX.

    KEY: `0x611E7190` derives the model as `(body[0x7A] != 1)`, so UnitType 4 has
    exactly TWO models, and creation has always captured a sex byte at the
    creation payload's +0x28 (`char_record`, "sex"). Nothing connected the two
    until 2026-08-26: the self POP stepped `FMO_UDP_POP_SEX_SWEEP` instead, so a
    player's model was whatever the sweep was up to. On screen that read as the
    two known models -- a man and a woman -- which is what a sex byte should
    select. Measured live 2026-08-26 (the alternation on every relog).

    WARNING: TWO MODELS IS NOT A CHARACTER CREATOR. Whatever else the creation screen
    offers must live in `appearance` (4 bytes at creation +0x30) or in the
    unmapped part of `raw`, which is on disk for every character ever made.
    This wires the one field whose meaning is not a guess.

    WARNING: The explicit knobs still win, in the order fixed > sweep > roster, so an
    A/B does not have to fight the store. `source` is returned so the log can
    say which of the three a model came from -- a model that came from a
    default is not evidence about the roster.
    """
    if popself.POP_SEX is not None:
        return popself.POP_SEX, "FMO_UDP_POP_SEX"
    if popself.POP_SEX_SWEEP:
        val, win = popself.next_pop_sex()
        return val, ("sweep %d of %d" % win if win else "sweep")
    if not popself.POP_SEX_FROM_ROSTER:
        return None, None
    acct = identity.account_for(host_ip)
    for c in charstore.load_roster(acct):
        if POP_SEX_SOURCE == "gender":
            g = poplook._gender_byte(c)
            if g is not None:
                return g & 0xFF, (f"store:{acct[:8]} gender={g} "
                                  f"(payload+0x26, FMO_UDP_POP_SEX_SOURCE=gender)")
            continue
        sex = c.get("sex")
        if sex is not None:
            return sex & 0xFF, (f"store:{acct[:8]} sex={sex} (payload+0x28 = "
                                f"the NATION byte; legacy FMO_UDP_POP_SEX_SOURCE=sex)")
    return None, "no character on file"


#: KEY: WHICH CREATION BYTE IS THE SEX. The static read of the 0x013E builder
#: (2026-08-26, see `character_from_013e`) says payload+0x26 is the GENDER menu
#: (99:235, Male/Female) and +0x28 is the NATION menu (99:232, OCU/USN) -- the
#: reverse of the differential's names, which could not tell the two apart. So
#: `sex` (+0x28) as served today is the NATION, and an OCU man and an OCU woman
#: wear the same model. Default stays "sex" (the shipped behaviour) until the
#: swap is SEEN: FMO_UDP_POP_SEX_SOURCE=gender serves +0x26 instead, and the
#: log line names the byte either way.
#:
#: VERIFIED: SEEN ON SCREEN 2026-08-27 (Patty Test, member 6, gender 2 / nation 1 --
#: the first character whose two bytes differ): source `sex` served +0x28=1 and
#: she was drawn as the MAN; source `gender` served +0x26=2 and she was drawn as
#: the WOMAN. Default is therefore "gender"; "sex" remains as the legacy A/B
#: arm. The stored keys keep their old (wrong) names so existing rosters still
#: load -- read `gender` / `nation_byte`, never `sex` / `nation`, for meaning.
POP_SEX_SOURCE = (os.environ.get("FMO_UDP_POP_SEX_SOURCE", "").strip().lower() or "gender")


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    charstore, groupchannel, identity, poplook, popself, room, worldchannel,
)
