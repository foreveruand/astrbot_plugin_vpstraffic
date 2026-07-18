# Changelog

## v2.1.0

- Collect and reset Xray counters on every query, then accumulate traffic in the local ledger to tolerate Xray service restarts.
- Added a configurable collection interval from 1 to 59 minutes.
- Record existing Xray user counters when initializing a new traffic ledger so the first `/vps` response reports current usage.

## v2.0.0

- Replaced VPS network-interface statistics with Xray per-email traffic accounting.
- Added periodic Xray `StatsService` collection through SSH and monthly counter resets.
- Removed Clash subscription information generation and SSH key file configuration.
