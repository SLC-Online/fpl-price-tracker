# FPL Dashboard — Technical Plan

## Framework: SvelteKit + Static Adapter → GitHub Pages

**Why SvelteKit over Next.js/Astro:**
- Compiles to vanilla JS — tiny bundles, fast load
- Static adapter = zero server, deploys to GitHub Pages for free
- Excellent reactivity model for interactive charts/search
- Tailwind CSS integration is trivial
- Already proven pattern: scrape → build JSON → deploy static

**Why not React/Next.js:** Overkill for a data dashboard, ships framework runtime unnecessarily.
**Why not plain HTML/JS:** We want component reuse, routing, and build-time data processing.

## Styling: Tailwind CSS + Dark Mode

- Dark navy/charcoal base (#0f172a / #1e293b) — easy on eyes for data-heavy views
- Team colour accents pulled from PL palette
- Responsive: mobile-first (check on phone), desktop-optimized for analysis

## Charts: ApexCharts

**Why ApexCharts over Chart.js/Plotly:**
- Better interactive time-series out of the box (zoom, pan, tooltips, annotations)
- Dark theme built-in
- Smaller bundle than Plotly, more polished than Chart.js
- Annotations (mark price changes, GW deadlines, match events on timeline)
- Sync'd charts (link price, transfers, ownership on same x-axis)

## Graphics / Image Resources

### Player Photos (from PL CDN — free, no hosting needed)
```
https://resources.premierleague.com/premierleague/photos/players/250x250/p{CODE}.png   # Large
https://resources.premierleague.com/premierleague/photos/players/110x140/p{CODE}.png   # Medium
https://resources.premierleague.com/premierleague/photos/players/40x40/p{CODE}.png     # Thumbnail
```
- `CODE` = player's `code` field from API (e.g. Haaland = 223340)
- Every PL player has a photo. Updated when they change clubs.

### Team Badges (SVG from PL CDN)
```
https://resources.premierleague.com/premierleague/badges/rb/t{TEAM_CODE}.svg    # Vector (best)
https://resources.premierleague.com/premierleague/badges/100/t{TEAM_CODE}.png   # 100px
https://resources.premierleague.com/premierleague/badges/70/t{TEAM_CODE}.png    # 70px
https://resources.premierleague.com/premierleague/badges/25/t{TEAM_CODE}.png    # 25px
```

### Team Shirts (from FPL CDN)
```
https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{TEAM_CODE}-110.webp   # Outfield
https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{TEAM_CODE}-220.webp   # Outfield large
https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{TEAM_CODE}_1-110.webp # GK
```

### Team Code Reference (2026-27 season)
| Team | ID | Code |
|------|-----|------|
| Arsenal | 1 | 3 |
| Aston Villa | 2 | 7 |
| Bournemouth | 3 | 91 |
| Brentford | 4 | 94 |
| Brighton | 5 | 36 |
| Chelsea | 6 | 8 |
| Coventry City | 7 | 9 |
| Crystal Palace | 8 | 31 |
| Everton | 9 | 11 |
| Fulham | 10 | 54 |
| Hull City | 11 | 88 |
| Ipswich Town | 12 | 40 |
| Leeds | 13 | 2 |
| Liverpool | 14 | 14 |
| Man City | 15 | 43 |
| Man Utd | 16 | 1 |
| Newcastle | 17 | 4 |
| Nott'm Forest | 18 | 17 |
| Spurs | 19 | 6 |
| Sunderland | 20 | 56 |

### Fallback: Generic Kit SVGs
- [dwdyer/football-kit-icons](https://github.com/dwdyer/football-kit-icons) — CC0 licensed SVG shirt icons
- Useful as fallback or for custom pitch visualizations

## Page Structure

### `/` — Overview Dashboard
- Price risers/fallers today (with player photos + shirt)
- Most transferred in/out (bar chart)
- Transfer velocity heatmap (which players are surging RIGHT NOW)
- Next deadline countdown

### `/player/{id}` — Player Detail
- Player photo, team badge, position, price
- **Price timeline chart** (annotated with GW deadlines, price changes)
- **Transfer velocity chart** (hourly in/out deltas, synced with price)
- **Ownership trend** (% over time)
- `price_change_percent` over time (see it climb toward 100)
- News/injury history
- Form, xG, xA, points

### `/compare` — Compare Players
- Side-by-side or overlay charts
- Select 2-5 players, see transfer flows / price / ownership on same axes

### `/price-changes` — Historical Price Changes
- Table: date, player, old price, new price, direction
- Filter by team, position, date range
- Link each to the player detail (see what led to the change)

### `/teams` — Team View
- Squad list with current prices, form
- Team badge prominently displayed
- Aggregate transfer activity

### `/live` — Live Transfer Monitor (future)
- Real-time-ish view (updates hourly)
- "Likely to rise tonight" / "Likely to fall" predictions (once we have the formula)

## Build Pipeline

```
GitHub Actions (hourly):
  1. Run daily_scrape.py → updates fpl_tracker.db
  2. Run build_dashboard_data.py → exports DB to static JSON files:
     - docs/data/players.json (all players, latest snapshot)
     - docs/data/snapshots_meta.json (list of all snapshots)
     - docs/data/timeline/{element_id}.json (per-player time series)
     - docs/data/price_changes.json
     - docs/data/teams.json
  3. npm run build → SvelteKit generates static HTML/JS/CSS
  4. Deploy to GitHub Pages (or commit to gh-pages branch)
```

## Data Format for Dashboard JSON

### players.json
```json
{
  "updated": "2026-08-22T11:00:00Z",
  "players": [
    {
      "id": 1,
      "name": "Raya",
      "full_name": "David Raya Martín",
      "team_id": 1,
      "team_code": 3,
      "team_short": "ARS",
      "position": "GKP",
      "price": 6.0,
      "price_change_percent": "0.5",
      "price_change_rate": 25,
      "transfers_in_event": 4542,
      "transfers_out_event": 3885,
      "ownership": 37.6,
      "form": "6.0",
      "total_points": 6,
      "photo_code": 154561,
      "status": "a",
      "news": ""
    }
  ]
}
```

### timeline/{element_id}.json
```json
{
  "element_id": 328,
  "name": "Calafiori",
  "team_code": 3,
  "snapshots": [
    {
      "t": "2026-08-22T01:00:00Z",
      "price": 55,
      "ti": 38000,
      "to": 2200,
      "pcp": "20.1",
      "rate": 1580,
      "own": "38.5"
    },
    {
      "t": "2026-08-22T03:00:00Z",
      "price": 56,
      "ti": 40000,
      "to": 2500,
      "pcp": "0.2",
      "rate": 1400,
      "own": "39.8"
    }
  ]
}
```

## Tech Stack Summary

| Layer | Choice | Reason |
|-------|--------|--------|
| Framework | SvelteKit 2 + adapter-static | Fast, tiny output, great DX |
| Styling | Tailwind CSS 4 | Utility-first, dark mode, responsive |
| Charts | ApexCharts | Best interactive time-series, dark theme, annotations |
| Hosting | GitHub Pages | Free, auto-deploy from Actions |
| Data | Static JSON (exported from SQLite) | No backend needed |
| Images | PL CDN (hotlinked) | Player photos, badges, shirts — all free |
| Build | GitHub Actions | Same workflow as scraper, just adds build step |

## Dependencies (npm)

```json
{
  "@sveltejs/kit": "^2",
  "@sveltejs/adapter-static": "^3",
  "svelte": "^5",
  "tailwindcss": "^4",
  "apexcharts": "^4",
  "svelte-apexcharts": "^2"
}
```

## Timeline

1. **Now:** Data accumulating hourly. No dashboard yet (nothing to show).
2. **After ~3 days:** Enough data points for meaningful time-series charts. Build MVP.
3. **After ~2 weeks:** First price changes observed. Price change page becomes useful.
4. **Ongoing:** Add features as data grows (predictions, comparisons, live monitor).
