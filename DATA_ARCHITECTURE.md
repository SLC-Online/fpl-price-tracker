# FPL Data Architecture

## Design Principles
1. **One source of truth per data type** — no mixing FPL API data with external sources in the same table
2. **Append-only** — never update historical records, always insert new snapshots
3. **Source-tagged** — every record knows where it came from and when
4. **Extensible** — new data sources get their own tables, joined via player_id/team_id/date

## Database: `data/fpl_tracker.db`

### Core Tables (from FPL API)

**`snapshots`** — metadata for each scrape run
- snapshot_id (auto), timestamp, source (local/cloud), api_version

**`players`** — player master data (slowly changing)
- element_id (PK), first_name, second_name, web_name, team_id, position, code
- Updated each snapshot but only stores latest (lookup table)

**`player_snapshots`** — the big table, one row per player per snapshot
- snapshot_id, element_id → all 60+ numeric/text fields from the API
- This is the time-series data for analysis

**`teams`** — team master data
- team_id, name, short_name, code

**`fixtures`** — all fixtures with scores
- fixture_id, event, team_h, team_a, kickoff_time, score_h, score_a, stats (JSON)

**`events`** — gameweek metadata
- event_id, deadline_time, finished, average_score

**`price_changes`** — detected price movements
- timestamp, element_id, old_cost, new_cost, change, context fields

### Extension Tables (future sources)

**`ext_injury_news`** — from external sources (PL site, Ben Dinnery, etc.)
- timestamp, element_id, source, news_text, severity, expected_return

**`ext_xg_match`** — per-match xG from FBref/Understat
- match_date, element_id, source, xg, xa, xgi, minutes

**`ext_ownership_fplstats`** — from fplstatistics.co.uk predicted price changes
- date, element_id, predicted_rise_pct, predicted_fall_pct

**`ext_creator_picks`** — Transfer Algorithm CSV data
- season, gameweek, element_id, bcv, projected_sum, recommended_action

**`ext_model_predictions`** — our own model outputs
- timestamp, element_id, model_version, predicted_points, predicted_price_change

### Views (for easy querying)

**`v_player_latest`** — most recent snapshot per player with name/team
**`v_price_timeline`** — price + transfers over time per player
**`v_transfer_velocity`** — hourly delta in transfers (derived from consecutive snapshots)

## File Storage

- `data/daily_snapshots/` — raw JSON (full API response, local only)
- `data/daily_fixtures/` — raw fixture JSON (local only)
- SQLite DB committed to git (cloud backup)
- JSON artifacts on GitHub Actions (90-day retention)

## Dashboard (GitHub Pages)

Build step converts latest DB state to static JSON files:
- `docs/data/players.json` — current player list with latest snapshot
- `docs/data/timeline/{element_id}.json` — per-player time series
- `docs/data/price_changes.json` — all detected changes
- `docs/data/summary.json` — overview stats

Chart.js or Plotly.js for interactive graphs. No backend needed.
