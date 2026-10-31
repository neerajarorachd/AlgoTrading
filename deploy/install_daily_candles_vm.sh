#!/usr/bin/env bash
# Run ON THE VM as tradinguser, after copying the files (see deploy/README_daily_candles.md).
set -euo pipefail
mkdir -p ~/.config/systemd/user
cp ~/AlgoTrading/deploy/algotrading-{daily,intraday}-candles.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now algotrading-daily-candles.timer algotrading-intraday-candles.timer
systemctl --user list-timers 'algotrading-*-candles.timer' --no-pager
