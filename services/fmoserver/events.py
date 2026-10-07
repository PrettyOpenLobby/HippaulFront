"""SE's timed events that live in client data: the commemorative service medal."""
import os


# --------------------------------------------------------------------------- #
# THE COMMEMORATIVE SERVICE MEDAL (記念従軍章), topics/060428
# --------------------------------------------------------------------------- #
#: SE, 2006-04-28, for the first anniversary (service began 2005-05-12): an
#: insignia named "2nd Huffman Conflict", one per nation, sold for H$10 in the
#: Squadron Insignia shop list (Hangar > Coloring > Squadron Insignia > R2)
#: from 05-15 to 06-05, to everyone. It goes on a wanzer like any insignia but
#: "cannot be set as the squadron insignia".
#:
#: READ: the records are in the client's own insignia table (D31, our
#: fmo-insignia.tsv): id 142 "O.C.U.軍 第二次ハフマン紛争 従軍章" and id 447 the
#: U.S.N. one, both with nation flag 255 -- which is why the catalogue
#: extraction (fmo-cosmetics.tsv drops flag 255) never offered them. The shop
#: list (0x61032EF3) keeps every kind-4 catalogue row whose D31 record exists
#: and is not owned; it applies no nation test of its own, so the server's
#: catalogue decides, and both ids fit the owned-insignia bitmap (base 101,
#: 0x80 bytes: inventory.OWNED_PAINT_CATS).
#:
#: NOT BUILT: the squadron-insignia refusal. Which request registers a
#: squadron insignia from the owned list is not read (squadron.py +0x0A is a
#: presence flag); until it is, a pilot could register the medal there.
MEDAL_KIND = 4                         # catalogue kind: insignia
MEDAL_IDS = {1: 142, 2: 447}           # nation -> D31 id
MEDAL_NAME = "2nd Huffman Conflict"
MEDAL_PRICE = 10                       # H$, topics/060428

#: FMO_SERVICE_MEDAL: '' / '0' (default) = not on sale. '1' = on sale.
#: 'MM-DD..MM-DD' = on sale between those days (UTC) every year, both
#: inclusive; SE's own window was 05-15..06-05.
MEDAL_SPEC = os.environ.get("FMO_SERVICE_MEDAL", "").strip()


def medal_window(spec, now=None):
    """True when the medal is on sale under `spec` at unix time `now`."""
    import time
    spec = (spec or "").strip()
    if spec in ("", "0"):
        return False
    if spec == "1":
        return True
    try:
        a, b = spec.split("..")
        lo = tuple(int(x) for x in a.split("-"))
        hi = tuple(int(x) for x in b.split("-"))
    except ValueError:
        return False
    t = time.gmtime(time.time() if now is None else now)
    d = (t.tm_mon, t.tm_mday)
    return lo <= d <= hi if lo <= hi else (d >= lo or d <= hi)


def medal_rows(spec=None, now=None):
    """{kind: [(id, nation_flag, english)]} rows to add to the catalogue now
    (cosmetics.COSMETICS shape), empty when the medal is not on sale."""
    if not medal_window(MEDAL_SPEC if spec is None else spec, now):
        return {}
    return {MEDAL_KIND: [(rid, nat, MEDAL_NAME) for nat, rid in sorted(MEDAL_IDS.items())]}


def is_medal(kind, rid):
    return kind == MEDAL_KIND and rid in MEDAL_IDS.values()


def with_medal(catalogue, spec=None, now=None):
    """`catalogue` plus the medal rows when on sale (a new dict; the input is
    not changed). The rows carry the nation as their flag, so cosmetics_for
    and buy_verdict apply their own nation rule to them."""
    extra = medal_rows(spec, now)
    if not extra:
        return catalogue
    out = {k: list(v) for k, v in catalogue.items()}
    for k, rows in extra.items():
        have = {r[0] for r in out.get(k, ())}
        out.setdefault(k, []).extend(r for r in rows if r[0] not in have)
    return out
