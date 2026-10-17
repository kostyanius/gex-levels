# GEX levels for TradingView

Every 5 minutes a GitHub Action runs `gex.py` (same calculation as the ATAS / MT5 / Quantower GEX code:
CBOE delayed options for DIA / QQQ / SPX / GLD, Deribit live options for ETH / BTC) and publishes
the result to GitHub Pages:

- `https://<user>.github.io/gex-levels/` - page with a "Copy all" button
- `https://<user>.github.io/gex-levels/levels.txt` - the same text
- `https://<user>.github.io/gex-levels/status.json` - status of the last run

Paste the text into the mega-script: GEX Levels -> "Levels". Each line starts with the keys matched
against the TradingView ticker, then `@=<spot>=<UTC ms>`: the levels are in the underlying's prices and
the script scales them to the chart (chart close at that moment / spot).

Instruments: edit `INSTRUMENTS` in `gex.py`.

Setup (once): Settings -> Pages -> Source: GitHub Actions; Actions -> gex-levels -> Run workflow.

New Relic (optional): secret `NR_LICENSE_KEY` (Ingest - License key), variable `NR_REGION` = `EU` or `US`.
Each run sends one `GexRun` event per instrument (ok, status, error, levels, spot, durationSec) and one `GexJob`
event (ok, instrumentsOk, instrumentsTotal, durationSec, trigger, runUrl). Dashboard: `newrelic/dashboard.json`.
