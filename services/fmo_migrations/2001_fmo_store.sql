-- The pilot database (services/fmostore.py), formerly the SQLite file fmo.db.
--
-- Integers are BIGINT because SQLite's INTEGER is 64-bit. Text that the old
-- file kept as TEXT stays TEXT, including the timestamps (ISO strings the
-- writer formats itself) and `extra`, the JSON of every record key the table
-- has no column for, so a record round-trips exactly as it did.

-- The squadron's registered insignia, keyed by POL group id (a squadron is a
-- POL group). One per group, set once: SE, 86:21 "It cannot be changed
-- afterwards".
CREATE TABLE fmo_squadron_insignia (
    group_id   BIGINT PRIMARY KEY,
    insignia   BIGINT NOT NULL,
    set_by     TEXT,               -- the store key of whoever registered it
    set_at     TEXT
);

-- One pilot. `account` is the resolved store key ("member:3", or "addr:<ip>"
-- when no POL session names the box); `id` is the slot number the client
-- picked, which is what every message refers to a character by.
CREATE TABLE fmo_character (
    account       TEXT    NOT NULL,
    id            BIGINT  NOT NULL,
    slot_ord      BIGINT  NOT NULL DEFAULT 0,   -- roster order, oldest first

    -- identity, as decoded from the 0x013E creation record
    "first"       TEXT,
    "last"        TEXT,
    nation        BIGINT,       -- the old (swapped) key; kept, the wire uses it
    sex           BIGINT,       -- likewise, see character_from_013e
    gender        BIGINT,
    nation_byte   BIGINT,
    personality   BIGINT,
    size          BIGINT,
    build         BIGINT,
    face          BIGINT,
    cls           BIGINT,
    hangar_pw     BIGINT,
    appearance    TEXT,

    -- the economy, served by 0x014A
    rank          BIGINT,
    money         BIGINT,
    mp            BIGINT,
    contribution  BIGINT,

    -- progress: the 256-byte kind-11 script flag block, as hex
    flags         TEXT,

    -- where the pilot is, so a relog puts them back
    mapno         BIGINT,
    mapkind       BIGINT,
    pos           TEXT,         -- "x,y,z[,w]" in the 0x0153 PilotPos frame

    -- the resume triad (lobby+0x7604 / +0x7608 / +0xFD4)
    resume_w7604  BIGINT,
    resume_w7608  BIGINT,
    resume_wfd4   BIGINT,

    -- garage setups and the owned-parts bitset, hex
    setups        TEXT,
    owned_parts   TEXT,

    -- what the client sent, kept so a partial decode loses nothing
    raw           TEXT,
    raw_0177      TEXT,
    extra         TEXT,         -- JSON: every key this table does not name

    created_at    TEXT,
    updated_at    TEXT,
    PRIMARY KEY (account, id)
);

CREATE INDEX fmo_character_account ON fmo_character (account, slot_ord);
