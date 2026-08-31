# FPL Transfer Optimizer

A self-contained desktop app that tells you the highest-expected-points transfer
move(s) to make this gameweek. It:

1. Pulls your **current squad**, prices and transfer history from the FPL API.
2. Works out **how many free transfers** you have (chips-aware).
3. Pulls the **expected points** for the coming gameweeks from Supabase
   (the Transfer Algorithm numbers, via the `final_projections` view).
4. **Brute-forces** every sensible transfer plan (0, 1, 2 or 3 moves),
   respecting budget, the 3-per-club rule and squad structure.
5. Scores each option by **time-weighted expected points** over the horizon,
   picking the **optimal legal XI every gameweek** (so rotation is accounted
   for), with a configurable decay (default 0.85 per GW).
6. Ranks the options by points gained, with the **−4 hit** applied to any
   transfers beyond your free ones.

## Setup (one time)

```bash
# 1. Install the one dependency
pip3 install requests

# 2. Provide your Supabase credentials (read expected points).
#    Either export them:
export SUPABASE_URL="https://YOUR-PROJECT.supabase.co"
export SUPABASE_SERVICE_KEY="YOUR-SERVICE-OR-ANON-KEY"
#    …or create a file called `.env` next to optimizer_app.py containing:
#        SUPABASE_URL=...
#        SUPABASE_SERVICE_KEY=...
#    (The GUI will also prompt you for these if they're missing.)
```

### macOS note — Tkinter (the GUI toolkit)

The GUI uses Tkinter, which ships with the standard python.org Python but is
**missing from some Homebrew Python builds**. If you see
`ModuleNotFoundError: No module named '_tkinter'`, install Tk support:

```bash
brew install python-tk
```

If Tk still isn't available, the app automatically falls back to a
command-line version (see below) — same results, printed to the terminal.

## Run it

**GUI:**
```bash
python3 optimizer_app.py
```
Then: type your **Manager ID**, click **Load squad**, adjust options if you
like, and click **Optimize ▶**. Select any suggestion to see the resulting
squad and the per-gameweek points breakdown.

**Command line** (works everywhere, no Tk needed):
```bash
python3 optimizer_app.py --cli 1234567 --transfers 2 --horizon 8
```
Options: `--transfers 1|2|3`, `--horizon 3..8`, `--decay 0.85`,
`--free N` (override the auto free-transfer count), `--top N`.

## Finding your Manager ID

Log in at fantasy.premierleague.com, go to the **Points** tab, and your ID is
the number in the URL: `.../entry/<THIS NUMBER>/event/...`.

## How to read the results

- **Δ pts** — extra time-weighted expected points over the horizon vs your
  current squad, if you make that move.
- **Hit** — the points cost (−4 per transfer beyond your free ones).
- **Net** — Δ minus the hit. This is what the list is ranked by.

A "No transfer" row is always included as the baseline. If nothing beats it on
net, the optimizer is telling you to hold.

## Notes / assumptions

- Transfers are same-position swaps (a squad slot keeps its position), which
  keeps the squad valid and the search fast.
- Only players that have projection data are considered as incoming targets.
- Selling price uses the standard FPL rule (you get back purchase price plus
  half of any rise, rounded down).
- The projections are whatever the latest captured Transfer Algorithm set says;
  the app tells you which gameweek's projection set it used.
