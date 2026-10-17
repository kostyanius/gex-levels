# Alert conditions (Alerts -> Alert conditions -> New alert condition -> Write your own query)

1. Job stopped running (schedule disabled, Actions outage, workflow broken)
   NRQL:      SELECT count(*) FROM GexJob
   Threshold: Critical when query result is below 1 for at least 30 minutes
   Signal:    Window 5 min; Loss of signal: open a "lost signal" violation after 30 minutes

2. An instrument keeps failing (CBOE / Deribit error, format change)
   NRQL:      SELECT count(*) FROM GexRun WHERE ok = 0 FACET instrument
   Threshold: Critical when query result is above 2 for at least 15 minutes
