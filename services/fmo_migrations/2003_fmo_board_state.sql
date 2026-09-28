-- The City Control board's Discord bookkeeping (polboards.py), formerly
-- /state/<name>_discord.json and /state/discord_channels.json on the board's
-- state volume: which messages the board posted and edits, and where each
-- feed posts. `name` is what the file was called without its extension
-- (fmo_discord, discord_channels). Janhourou's board keeps the same shape
-- in jan_board_state.
CREATE TABLE fmo_board_state (
    name       TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
