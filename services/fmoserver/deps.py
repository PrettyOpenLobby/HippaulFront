"""Imports shared by the package modules: the standard library and the optional sibling
services."""

try:
    # The UDP world channel's cipher and framing. Optional so that a missing
    # module degrades to the TCP-only behaviour this file has always had,
    # loudly, instead of taking the whole responder down with an ImportError.
    import fmoworld
except ImportError:                                  # pragma: no cover
    fmoworld = None

try:
    # THE PER-LOGIN CONTENT AUTH VALUE the lobby mints on 4:5 -- the first 16
    # bytes of this title's TCP key. See contentauth.py and Session.resolve_key.
    # Optional: absent, the key is the old 16 zero bytes and the account comes
    # from the address exactly as before.
    import contentauth
except ImportError:                                  # pragma: no cover
    contentauth = None

try:
    # KEY: THE WAR-MAP SECTOR TABLE. Optional for the same reason as the rest:
    # a missing module must degrade to the flat FMO_WARMAP_MAPS list, loudly,
    # not take the responder down. See fmosectors.py's docstring.
    import fmosectors
except ImportError:                                  # pragma: no cover
    fmosectors = None

try:
    # KEY: FMO's SECOND SERVER -- the community/mission service ("Fshira"), which
    # the client has been dialling on THIS SAME host:port since the 0x0322
    # endpoint work landed and which we have never answered. Optional for the
    # same reason fmoworld is: a missing module must degrade, not take the
    # responder down. See fmomsn.py's docstring for the whole decode.
    import fmomsn
except ImportError:                                  # pragma: no cover
    fmomsn = None

try:
    # KEY: THE PLAYER DATABASE. Optional for the same reason as the two above: a
    # missing module must degrade to the JSON character store this file shipped
    # with, loudly, rather than take the responder down. See fmostore.py's
    # docstring for why FMO's player state is moving out of the knobs.
    import fmostore
except ImportError:                                  # pragma: no cover
    fmostore = None

try:
    # The stack's PostgreSQL database (fmodb.py, over OpenLobby's polcore): the
    # player database, the war state and the sector-win ledger live there.
    # Optional like the rest: without polcore the ledger is memory only and
    # the character store is the JSON file, and both say so in the log.
    import fmodb
except ImportError:                                  # pragma: no cover
    fmodb = None

try:
    # KEY: THE WAR STATE (stage 17): per-sector control, the phase clock and the
    # binding onto the 216-byte sector record the war map and City Control
    # fetch through the second server's kind-7 job. Optional like the rest.
    import fmowar
except ImportError:                                  # pragma: no cover
    fmowar = None

try:
    # VERIFIED: THE LOBBY NPC EDITOR (2026-09-11). fmolayout is the layout FILE the
    # world channel pops a band's cast from whenever the file carries that
    # band (fmodevtool.py is the page that writes it; it rides fedevtool's
    # server). Optional like everything above: absent, every band is served
    # from FMO_UDP_POP_NPC* exactly as before, and startup says the editor is
    # off. Never a hard dependency -- a missing panel must not cost the door.
    import fmolayout
    import fmodevtool
    import fedevtool
except ImportError:                                  # pragma: no cover
    fmolayout = fmodevtool = fedevtool = None

try:
    # The story gates tool (fmogates.py): the flag/rank catalogue and its
    # page, served on the same tool port. Optional: absent, the port serves
    # the NPC editor alone.
    import fmogates
except ImportError:                                  # pragma: no cover
    fmogates = None


def flat_globals():
    """What `globals()` meant in the single-file fmo.py: every top-level
    name of the package, read and written through the facade, so a patch of
    `globals()["NAME"]` lands in the module that owns NAME."""
    import fmo
    return fmo.FLAT_GLOBALS
