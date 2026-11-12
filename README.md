# GEX levels for TradingView

A GitHub Action runs `gex.py` a few times a day (before the European open, before and after the US open;
once a day at weekends) - same calculation as the ATAS / MT5 / Quantower GEX code: CBOE delayed options for
DIA / QQQ / SPX / GLD, Deribit live options for ETH / BTC. Open interest changes once a day and the Pine script
rescales the levels to the chart price itself, so more frequent runs add nothing.

The result is published to the `data` branch (a single commit that is replaced, only when the levels changed;
no GitHub Pages, no deployments):

- `https://raw.githubusercontent.com/<user>/gex-levels/data/levels.txt` - the text to paste (open, select all, copy)
- `https://raw.githubusercontent.com/<user>/gex-levels/data/status.json` - status of the last run

`data` is a branch, not a folder: to browse it on github.com pick `data` in the branch drop-down. The raw
address is cached for about 5 minutes. Never copy the word "published:" from the job log together with the link.

Paste the text into the mega-script: GEX Levels -> "Levels". Each line starts with the keys matched
against the TradingView ticker, then `@=<spot>=<UTC ms>`: the levels are in the underlying's prices and
the script scales them to the chart (chart close at that moment / spot).

Instruments: edit `INSTRUMENTS` in `gex.py`.

Setup (once): Actions -> gex-levels -> Run workflow (creates the `data` branch). GitHub Pages is not used any more.
Extra update on demand: the same "Run workflow" button.
The concurrency group is named `gex-levels-data` with `cancel-in-progress: true`, so a hung run of an older workflow
cannot block new ones. Why not Pages: the old 5-minute run created ~290 deployments a day and got blocked.

New Relic (optional): secret `NR_LICENSE_KEY` (Ingest - License key), variable `NR_REGION` = `EU` or `US`.
Each run sends one `GexRun` event per instrument (ok, status, error, levels, spot, durationSec) and one `GexJob`
event (ok, instrumentsOk, instrumentsTotal, durationSec, trigger, runUrl). Dashboard: `newrelic/dashboard.json`.
