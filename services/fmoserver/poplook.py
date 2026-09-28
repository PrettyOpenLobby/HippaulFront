"""A pilot's look on a POP: size, build, face and uniform from the creation record."""
import os
import struct
from .deps import fmoworld


#: VERIFIED:KEY: THE OTHER FOUR APPEARANCE FIELDS -- 2026-09-04, and this is what makes
#: a player stop being an NPC. `body+0x7A` was never "the look": it is one of
#: FIVE fields UnitType 4 reads out of the POP body (see
#: fmoworld.POP_TYPE4_SIZE and the decode above it). The other four are the
#: creation screen's own menus, and every one of them has been on disk since
#: the character store existed:
#:
#:     body+0x188  size    <- creation +0x30   1..3, the height SCALE
#:     body+0x189  build   <- creation +0x31   1..5, the body morph
#:     body+0x18A  face    <- creation +0x32   101..110, head part kind 0x24
#:     body+0x18C  uniform <- nation +0x28     via POP_UNIFORM_FOR_NATION
#:
#: WARNING: THE UNIFORM IS DERIVED, NOT STORED. Creation never sends a uniform id;
#: the client's own preview looks it up from the nation (`[nation*4 +
#: 0x613866C0]`), and we do the same. If that table is ever wrong, the body is
#: the field that will be wrong -- not the head.
#:
#: WARNING: FACE AND GENDER ARE ONE DATUM. The face table is chosen by `body+0x7A`
#: (0x611F1FA1), so an id picked as a woman resolves to nothing when served
#: beside a male gender byte, and the head just does not attach. Both come off
#: the same roster record here; keep it that way.
#:
#: FMO_UDP_POP_LOOK=0 restores the old all-zeros body for an A/B.
#: FMO_UDP_POP_LOOK_FIXED="size,build,face,uniform" overrides the roster (any
#: field may be left blank), and WINS, so a probe does not have to fight the
#: store. The source is returned so the log can say which it was -- a look that
#: came from a knob is not evidence about the roster.
POP_LOOK = os.environ.get("FMO_UDP_POP_LOOK", "1") != "0"


def _parse_look_fixed(spec):
    """FMO_UDP_POP_LOOK_FIXED -> a look dict, or None when unset.

    WARNING: An empty value is "unset", not "all zeros" -- compose passes
    `${FMO_UDP_POP_LOOK_FIXED:-}`, and the `int("")` that shape has already
    crash-looped this service once (the empty-env trap)."""
    spec = (spec or "").replace(" ", "")
    if not spec:
        return None
    parts = spec.split(",")
    if len(parts) != 4:
        raise SystemExit("FMO_UDP_POP_LOOK_FIXED wants four comma-separated "
                         "fields (size,build,face,uniform), any of them blank; "
                         "got %r" % spec)
    out = {}
    for name, text in zip(("size", "build", "face", "uniform"), parts):
        if not text:
            continue
        out[name] = int(text, 0)
        # WARNING: Refuse a bad knob HERE, at import, and not at pop time. The guard
        # in record_pop raises, the POP site catches it, and the player then
        # gets NO UNIT AT ALL -- a typo in a probe knob must not be able to
        # cost the launch it was set up for.
        lo, hi = (fmoworld.POP_LOOK_RANGES[name] if fmoworld
                  else (0, 0xFFFF))
        if not lo <= out[name] <= hi:
            raise SystemExit("FMO_UDP_POP_LOOK_FIXED %s=%r is outside %d..%d; "
                             "the client resolves no part for it and the slot "
                             "stays empty" % (name, out[name], lo, hi))
    return out or None


POP_LOOK_FIXED = _parse_look_fixed(os.environ.get("FMO_UDP_POP_LOOK_FIXED"))

#: WARNING:KEY: WHY THE LOOK IS SENT LATE -- and WARNING: THE MECHANISM I FIRST GAVE FOR IT IS
#: RETRACTED (2026-09-04). Read this whole block before acting on it.
#:
#: THE FACTS THAT HOLD (measured, empirical):
#:   * dressing the human in the CREATE POP sometimes stops the game starting;
#:   * creating bare and dressing with a second cmd 7 works (seen on screen);
#:   * an all-zero body never showed the fault in months of use.
#:
#: WARNING: THE EXPLANATION THAT DOES NOT HOLD. I claimed the model refresh
#: `0x61132160` arm 4 skips its part-slot loop when a slot is "empty", so a
#: zero body never reached the `mov ecx,[esi+0xea2]` inside it. **That is
#: wrong.** `[unit+0x381]` and `[unit+0x385]` are not part ids -- they are
#: pointers to two DESCRIPTORS inside the unit itself, and the kind-4
#: constructor `0x611F4610` writes both unconditionally
#: (`mov [esi+0x381], esi+0x1CD5` / `mov [esi+0x385], esi+0x1CED`) and then
#: calls `0x61130100` to allocate `+0xEA2`, also unconditionally. So for a
#: human BOTH slots are always non-NULL and `+0xEA2` is always allocated before
#: any refresh can run: the `test ecx,ecx / je` never skips, and the part ids
#: we send change nothing about WHETHER that pointer is read. The part id goes
#: to `descriptor+0x10` (written by `0x611F5700`), not to the slot pointer.
#:
#: KEY: WHAT ACTUALLY DIFFERS when we serve ids, and the place to look next: with
#: a real id, `0x611F1990(descriptor)` resolves an actual part and
#: `0x6115A000(+0xEA2, part, 0, slot)` attaches a real MODEL -- so a model
#: resource is loaded that otherwise never is. With id 0 it resolves nothing
#: and the call is a no-op. **The fragile thing is therefore the model LOAD,
#: not a NULL pointer** -- which also fits "sometimes" (whether the head and
#: uniform meshes are already resident in this scene). WARNING: THIS IS A HYPOTHESIS.
#: It has not been traced into `0x6115A000` and must not be written up as a
#: cause until it has.
#:
#: WHAT THE KNOB DOES, which is unchanged and still empirically useful: a
#: second cmd 7 carrying the SAME UnitID takes the already-present arm
#: `0x611EB2CA` (verified: `0x611EAE0B` jumps there when `map.find(UnitID)`
#: hits) -- it tears the old visual down via `0x611E72E0`, re-copies
#: `body[0x58..0x1C8)`, then falls back into the UnitType dispatch so
#: `0x611E7190` rebuilds AND DRESSES the unit. So we create bare and dress N
#: seconds later, when the scene is settled.
#:
#: WARNING:WARNING: IT IS A WORKAROUND AND IT SHOWS: the player spawns as the default person
#: and visibly changes into their character N seconds later. Retail dresses at
#: create, from one cmd 7. **The real fix is whatever makes dressing-at-create
#: safe, and that is still unknown.**
#:
#: 0 = dress in the create POP (the behaviour that crashed).
POP_LOOK_DEFER = float(os.environ.get("FMO_UDP_POP_LOOK_DEFER", "").strip()
                       or 8.0)

#: VERIFIED:KEY: THE CAST-NPC DEFERRED RE-POP (FMO_UDP_POP_NPC_RELOOK). A cast NPC
#: (FMO_UDP_POP_NPC) popped at scene state 3 registers in the entity map -- so
#: the script's E285/E291 place it and its dialogue attributes -- but its
#: type-4 VISUAL does not build that early: read live 2026-09-04, actor
#: (entity+0x24) is NULL in the cutscene and non-null once the lobby settles.
#: A SECOND cmd 7 on the same key takes the update arm 0x611EB2CA, which tears
#: the visual down and rebuilds it via 0x611E7190 in a settled scene. This is
#: the delay in seconds after the initial NPC pop at which to re-send them.
#: 0 = off (the initial pop stands, invisible until the scene settles on its
#: own). Whether a re-pop can build the visual DURING the cutscene (vs only the
#: post-cutscene lobby) is the live question this knob exists to answer.
POP_NPC_RELOOK = float(os.environ.get("FMO_UDP_POP_NPC_RELOOK", "").strip()
                       or 0.0)
#: KEY: THE PERIODIC NPC RE-POP (FMO_UDP_POP_NPC_REPOP, seconds; 0 = off). Live
#: 2026-09-05 17:31Z: the NPC record was sent and acked, the client went scene
#: 7 -> 6, and the settled-lobby census held ONLY the self -- the 7->6 switch
#: wipes the whole 0x820Cxxxx NPC space (static 2026-09-05),
#: and this time there was NO channel restart to make the server re-pop (the
#: client sent nothing but cmd 300 keepalives and cmd 240 positions across the
#: switch -- the wipe is invisible on the wire). So the lobby NPCs must be
#: re-sent on a timer while the channel lives: a re-pop of a WIPED key takes the
#: creator's CREATE arm (she comes back), of a PRESENT key the UPDATE arm
#: 0x611EB2CA (visual rebuild -- the RELOOK mechanism, so a visible blink every
#: interval is the known cost; keep it >= 20 s). Uses the RELOOK timer: the
#: first re-pop fires at RELOOK (or REPOP when RELOOK is 0), then every REPOP.
POP_NPC_REPOP = float(os.environ.get("FMO_UDP_POP_NPC_REPOP", "").strip()
                      or 0.0)

#: KEY: THE ONE PROVABLY SAFE SUBSET. `size` (`body+0x188`) is read at
#: `0x611F5C83`, which only picks a float scale into `[unit+0x3B1]` -- it never
#: touches `+0xEA2`. `face`/`uniform` populate the part slots (the loop above)
#: and `build` drives the morph, so all three can reach the bad pointer.
#: `FMO_UDP_POP_LOOK_FIELDS=size` therefore gives height with NO exposure to
#: this bug at all, which is worth having while the rest is unresolved.
#: WARNING: AN EMPTY VALUE IS "UNSET", NOT "NO FIELDS". compose passes
#: `${FMO_UDP_POP_LOOK_FIELDS:-}`, so a prod .env that does not set it hands us
#: "" -- and `os.environ.get(name, default)` returns that EMPTY STRING, never
#: the default, because the variable IS set. Written the obvious way this
#: parsed to `[]`, filtered out all four fields, and served `look=None` with a
#: source string that said `store:member:3 ... (nation 1)` -- i.e. it looked
#: like the ROSTER was empty rather than like a knob was misread. Same family
#: as the `:-` default that fed `int("",0)` and crash-looped this service
#: (the empty-env trap) and as POP_POS's `float("")`. Read the env ONCE, strip it,
#: and fall back only on a FALSY value.
POP_LOOK_FIELDS = [f for f in
                   (os.environ.get("FMO_UDP_POP_LOOK_FIELDS", "").strip()
                    or "size,build,face,uniform")
                   .replace(" ", "").split(",") if f]
for _f in POP_LOOK_FIELDS:
    if fmoworld and _f not in fmoworld.POP_LOOK_FIELDS:
        raise SystemExit("FMO_UDP_POP_LOOK_FIELDS: %r is not one of %s"
                         % (_f, ", ".join(sorted(fmoworld.POP_LOOK_FIELDS))))


def _look_field(c, key, off, width=1):
    """One creation-record field for a stored character: the named key on a
    record written after 2026-08-26, else read out of `raw` -- which every
    character ever made carries verbatim -- else None. Same shape as
    `_gender_byte`, and the same reason: the store's OLD key names are wrong
    and the raw payload is the thing that cannot be."""
    v = c.get(key)
    if v is not None:
        return v
    raw = c.get("raw") or ""
    if len(raw) < (off + width) * 2:
        return None
    try:
        b = bytes.fromhex(raw)
    except ValueError:
        return None
    return b[off] if width == 1 else struct.unpack_from("<H", b, off)[0]


def pop_look_for(host_ip):
    """(look, source) for `host_ip`'s character -- size, build, face, uniform.

    Every field is validated against the range the CLIENT enforces, and one
    that fails is DROPPED and named in the source string rather than sent. The
    client clamps none of them: an out-of-range face id resolves to no part and
    the head silently does not attach, which looks exactly like the offset
    being wrong. A dropped field must never be able to masquerade as a decode
    failure -- a stale instrument reads as a result."""
    if POP_LOOK_FIXED is not None:
        return dict(POP_LOOK_FIXED), "FMO_UDP_POP_LOOK_FIXED"
    if not POP_LOOK:
        return None, None
    acct = identity.account_for(host_ip)
    for c in charstore.load_roster(acct):
        size = _look_field(c, "size", 0x30)
        build = _look_field(c, "build", 0x31)
        face = _look_field(c, "face", 0x32, 2)
        nation = _look_field(c, "nation_byte", 0x28)
        uniform = fmoworld.POP_UNIFORM_FOR_NATION.get(nation)
        look, dropped = {}, []
        for name, val in (("size", size), ("build", build), ("face", face),
                          ("uniform", uniform)):
            if not val or name not in POP_LOOK_FIELDS:
                continue
            lo, hi = fmoworld.POP_LOOK_RANGES[name]
            if lo <= val <= hi:
                look[name] = val
            else:
                dropped.append("%s=%r (want %d..%d)" % (name, val, lo, hi))
        if uniform is None and nation:
            dropped.append("uniform: nation %r is not 1 (OCU) or 2 (USN)"
                           % (nation,))
        src = ("store:%s size=%s build=%s face=%s uniform=%s (nation %s)"
               % (acct[:8], look.get("size"), look.get("build"),
                  look.get("face"), look.get("uniform"), nation))
        if dropped:
            src += " -- DROPPED, not sent: " + "; ".join(dropped)
        return (look or None), src
    return None, "no character on file"


def _gender_byte(c):
    """Creation payload +0x26 for a stored character: the `gender` key on a
    record written after 2026-08-26, else read out of `raw`, else None."""
    if c.get("gender") is not None:
        return c["gender"]
    raw = c.get("raw") or ""
    if len(raw) >= 0x27 * 2:
        try:
            return bytes.fromhex(raw)[0x26]
        except ValueError:
            return None
    return None


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, identity  # noqa: E402
