-- Enable unaccent extension for accent-insensitive search
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Add a search-friendly name column (unaccented, lowercased)
ALTER TABLE players ADD COLUMN IF NOT EXISTS search_name TEXT;

-- Populate it
UPDATE players SET search_name = lower(unaccent(web_name || ' ' || COALESCE(first_name, '') || ' ' || COALESCE(second_name, '')));

-- Index for fast searches
CREATE INDEX IF NOT EXISTS idx_players_search_name ON players USING gin(search_name gin_trgm_ops);

-- If trigram extension not available, use standard btree
-- CREATE INDEX IF NOT EXISTS idx_players_search_name ON players(search_name);

-- Function to keep search_name updated
CREATE OR REPLACE FUNCTION update_search_name()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_name := lower(unaccent(NEW.web_name || ' ' || COALESCE(NEW.first_name, '') || ' ' || COALESCE(NEW.second_name, '')));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trigger_update_search_name
    BEFORE INSERT OR UPDATE ON players
    FOR EACH ROW
    EXECUTE FUNCTION update_search_name();
