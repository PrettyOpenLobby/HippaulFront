-- The squadron's nation, keyed by POL group id (a squadron is a POL group).
-- Recorded the first time the squadron is served (squadron.squadron_nation)
-- and never changed after: the squadron table's +0x09 must be the SQUADRON's
-- nation, so a member in the other army sees it inactive (manual p.50),
-- not the nation of whoever is looking at it.
CREATE TABLE fmo_squadron_nation (
    group_id   BIGINT PRIMARY KEY,
    nation     BIGINT NOT NULL,
    set_by     TEXT,               -- the store key of the member it was first served to
    set_at     TEXT
);
