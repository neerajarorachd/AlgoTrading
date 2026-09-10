# AlgoTrading — Project Guide

## Status (as of 2026-09-10)

AlgoTrading is the parallel system under active local development. The existing
`Trading` project is a protected working system and must not be copied, moved,
renamed, overwritten, reconfigured, or used as this project's environment.

Current decisions confirmed:
- Phase 1 architecture is driven by HLD/LLD and stays broker-agnostic.
- Dhan, Zerodha, and Upstox will all be implemented as adapters behind a common
  broker interface.
- Mixed-broker sessions are allowed: for example feed via Upstox, execution via
  Dhan, portfolio/positions via Zerodha.
- React frontend must not talk directly to SQL Server; it talks to the Flask API
  and live WebSocket backend.
- Development and tests run locally on Windows until the system is stable. Code
  is written and tested locally first, then copied to the VM module by module —
  the VM does not run the application yet.
- The Azure VM ("trading-server" in SSH config) hosts SQL Server Express only;
  application deployment is a later phase.
- The Azure VM is Ubuntu 24.04.3 LTS, x86_64, 2 vCPU, 15 GiB RAM, with a
  separate 32 GB `/mnt` data disk (`/dev/sdb1`).
- SQL Server 2025 (17.x) Express edition is installed on the VM (CU1, since
  Ubuntu 24.04 support begins there) — **done**, see below.
- Git repository initialized locally with remote
  `https://github.com/neerajarorachd/AlgoTrading.git` — **done**.

## Intended Architecture

- **Backend**: Python. Broker integration via per-broker adapter modules
  (Dhan, Zerodha, Upstox) behind a common interface.
- **Broker abstraction**: `BaseBroker`/`BrokerBase` defines generic methods such as
  `connect()`, `subscribe_feed()`, `get_historical_data()`, `get_quote()`,
  `place_order()`, etc. Each broker adapter implements only its vendor-specific
  behavior.
- **Session model**: each session can map different roles to different brokers,
  e.g. `market_data_broker`, `execution_broker`, `portfolio_broker`.
- **Frontend**: React (not Flutter). Market Watch shows live tick updates for
  subscribed instruments with LTP, direction, absolute change, percentage change,
  previous close baseline, and currency metadata.
- **Database**: SQL Server on Azure VM; data files and logs live under `/mnt`
  (bind-mounted at `/var/opt/mssql`) to avoid filling the root disk.
- **Data access pattern**: local dev connects to the VM's SQL Server over an SSH
  tunnel — the DB port is never exposed to the internet, and SQL Server itself
  only binds to `127.0.0.1` on the VM (`network.ipaddress` set via `mssql-conf`).

## Live Tick Contract (frontend)

The frontend will consume a lightweight live tick payload from the backend for each
registered instrument:

```json
{
  "type": "tick",
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "segment": "EQUITY",
  "ltp": 2854.65,
  "previous_close": 2828.4,
  "currency": "INR",
  "absolute_change": 26.25,
  "percentage_change": 0.93,
  "direction": "up",
  "ts": "2026-09-10T09:16:02Z"
}
```

The backend computes `absolute_change`, `percentage_change`, and `direction`.
The frontend only renders the normalized market state.

## Azure VM / Storage Notes

- Root disk (`/`, `/dev/root`) is ~30G, was ~79% used before SQL Server was
  relocated; keep it for OS, app runtime, config, small files only.
- `/mnt` (`/dev/sdb1`, 32G, ext4) is the free data disk — SQL Server's
  `/var/opt/mssql` is bind-mounted here (see `/etc/fstab`), so system + user
  database files, logs, and backups all physically live on `/mnt`, not `/`.
- `/home/tradinguser/` holds the existing, protected `Trading` project
  (`Trading/`, `tradingenv/`, `Trading.db`, `Dependencies/`, `Reports/`,
  a root-owned `next_day_predection.timer`). None of this was touched or
  modified while setting up AlgoTrading's SQL Server.
- AlgoTrading has no code directory on the VM yet — by design, code is
  developed/tested on Windows and copied over module by module later.

## SQL Server on the VM (done)

- Installed: `mssql-server` 17.0.4085.5 (SQL Server 2025 CU1), Express edition,
  via the official `mssql-server-2025` apt repo for Ubuntu 24.04.
- Data/log/backup files: physically on `/mnt/sqlserver`, bind-mounted to
  `/var/opt/mssql` (persists across reboots via `/etc/fstab`).
- Network: bound to `127.0.0.1:1433` only (`mssql-conf set network.ipaddress
  127.0.0.1`) — not reachable even from elsewhere on the VM's VNet, let alone
  the internet. No Azure NSG rule for port 1433 exists or is needed.
- Auth: SQL/mixed-mode (default on Linux). Two accounts exist:
  - `sa` — server admin, used only for one-off admin tasks, password held by
    the project owner (not in any file in this repo).
  - `algo_app` — the app's own login, `db_owner` on `trading_db` only. This is
    what `DB_CONNECTION_STRING` in `.env` uses day to day.
- Database `trading_db` created; no application tables yet (schema/models are
  the next implementation step, see Phase1 LLD `backend/Phase1_LLD.md`).
- Command-line tools (`sqlcmd`/`bcp`) installed on the VM at
  `/opt/mssql-tools18/bin/`.
- **Gotcha found during setup**: on the VM, plain `localhost` resolves to the
  IPv6 loopback first, and SQL Server's IPv6 listener is on a different
  (cluster/DAC-style) port than 1433 — so always use `127.0.0.1` explicitly,
  both in SSH tunnel `-L` specs and in `DB_CONNECTION_STRING`, not `localhost`.

## Security / Access Notes

- Never expose SQL Server directly to the public internet.
- Use SSH tunnel for local access to the VM database; SQL Server itself is
  bound to loopback only on the VM as a second layer of defense.
- Keep API keys and secrets out of source control — `.env` is gitignored;
  `.env.example` holds only placeholders.
- For the broker layer, prefer a generic adapter interface and vendor-specific
  wrappers, not direct library calls across the rest of the application.

## Conventions

Current conventions in use:
- Python backend with broker-specific adapter modules, tested with `pytest`
  (`pytest.ini` sets `pythonpath = backend`; run via
  `.\.venv\Scripts\python.exe -m pytest backend/tests -q`)
- Generic broker interface (`backend/brokers/base_broker.py`) behind adapter
  implementations (`dhan_broker.py`, `zerodha_broker.py`, `upstox_broker.py`)
- `backend/market_state.py` builds the normalized frontend tick payload;
  `backend/market_feed.py` wires broker ticks into it
- React frontend talks only to API/WebSocket backend (not yet built)
- HLD (`backend/algo_trading_system_HLD.md`) / LLD (`backend/Phase1_LLD.md`)
  remain the source-of-truth design docs for architecture

## Local Dev: DB Access via SSH Tunnel

1. Open the tunnel (SSH alias `trading-server` already configured):
   ```bash
   ssh -L 1433:127.0.0.1:1433 trading-server -N
   ```
   Add `ServerAliveInterval 60` to SSH config to keep long dev sessions alive.
   Not yet set up as a persistent/autossh background service — run manually
   per dev session (see `LOCAL_DEVELOPMENT.md`).

2. Local `.env` (gitignored) points at `127.0.0.1`, never the VM's IP:
   ```
   DB_CONNECTION_STRING=mssql+pyodbc://algo_app:<password>@127.0.0.1:1433/trading_db?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
   ```
   `backend/db_config.py` validates this at load time (host must be a loopback
   address on port 1433, driver must be exactly "ODBC Driver 18 for SQL
   Server").

3. Windows needs ODBC Driver 18 for SQL Server installed
   (`winget install --id Microsoft.msodbcsql.18 -e`).

## Open Items

- Phase 1 slice — instrument register/unregister UI, live ticks/depth %,
  1/3/5-min candles, live chart — is **built and verified end-to-end against
  the real Dhan API and WS feed** (2026-09-10, with live credentials). Full
  writeup/design: `C:\Users\Lenovo\.claude\plans\dapper-sprouting-pnueli.md`.
  - `subscribed_symbols`/`candles_today` tables (`backend/db/models.py` +
    `session.py`), live on `trading_db`. Deferred: `candles_historical`,
    `last_fetch_status`, `candle_gap_queue`, EOD archiver, gap scanner,
    Alembic.
  - `backend/feed/candle_aggregator.py` + `candle_persistence.py` — tick ->
    1/3/5-min candles, boundary-driven rollup, UTC throughout.
  - `backend/market_feed.py` wires depth (`on_depth`, via
    `depth_metrics.calculate_depth_metrics`) and per-tick volume delta
    (`on_candle_tick`) — additive, existing tests untouched.
  - `backend/app.py` (Flask factory) + `backend/api/routes_symbols.py` +
    `routes_candles.py` + `ws_live.py` (Flask-SocketIO, room-per-instrument
    broadcast) + `backend/feed/bootstrap.py` (startup hydration + feed
    connection bootstrap).
  - `frontend/` — Vite + React Market Watch page (register/unregister, live
    ticks/depth, candlestick chart via `lightweight-charts`).
- Confirmed live and fixed: `DhanBroker.subscribe_feed`'s first-ever call
  blocks the calling thread forever (`WebSocketApp.run_forever()`) — worked
  around via a dedicated background thread opening the socket with an empty
  instrument list at startup. Also found and fixed live: the readiness check
  after that must test `sock.connected`, not just `sock is not None` — the
  latter fires before the handshake completes and a subscribe sent that early
  raises `WebSocketConnectionClosedException`.
- Minor, not yet fixed: calling `broker.disconnect()` from a thread other
  than the one running the feed's `run_forever()` loop can raise inside that
  background thread (observed live during a manual test). Not an issue for
  the current always-on design (the feed thread runs for the process
  lifetime, `disconnect()` is never called in normal operation) — will matter
  once the deferred `session_scheduler` (connect/disconnect around market
  hours) is built.
- Local git repo has commits but nothing has been pushed to
  `origin` (`https://github.com/neerajarorachd/AlgoTrading.git`) yet.
- Decide on persistent tunnel (autossh) vs. manual `ssh -L` per dev session.
- Dhan access tokens are short-lived (observed ~24h) — `.env`'s
  `DHAN_ACCESS_TOKEN` will need refreshing periodically; no refresh
  automation exists yet.
