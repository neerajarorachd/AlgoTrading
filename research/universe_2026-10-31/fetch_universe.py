"""Research universe (user, 2026-10-30): 100-150 high-volume NSE stocks,
2 years of 5-min candles (+ daily for selection), into LOCAL files only
(not the VM's SQL Server; research use).

Steps (each resumable; files already written are skipped):
  1. resolve each candidate's Dhan security id (Dhan's public scrip master);
  2. fetch 2 years of daily candles for every candidate -> daily/<SYM>.pkl;
  3. rank by median daily turnover (close x volume) over the last 250 days and
     keep the top N (default 150) -> universe.json;
  4. fetch 2 years of 5-min candles for those, in 90-day chunks -> 5min/<SYM>.pkl.
Rows: (epoch_utc, open, high, low, close, volume).

Token: --token-from-db reads AlgoTrading's FIXED3 (REST) Dhan token from SQL
Server (needs the SSH tunnel, VM up before 18:00); otherwise DHAN_ACCESS_TOKEN
from the environment. The token is never printed.

    .venv/Scripts/python.exe "D:/AI Trading/research/universe_2026-10-31/fetch_universe.py" --token-from-db
    ... --resolve-only     (step 1 only, no token needed)
"""
import argparse
import json
import os
import pickle
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path("D:/AI Trading/AlgoTrading/backend")
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND.parent / ".env")

HERE = Path(__file__).resolve().parent
END = datetime(2026, 10, 30, tzinfo=timezone.utc)
START = END - timedelta(days=730)

# Nifty 100 plus liquid F&O names. Renamed or delisted symbols simply fail to
# resolve and are reported; the turnover ranking picks the final list.
CANDIDATES = """
RELIANCE HDFCBANK ICICIBANK INFY TCS SBIN BHARTIARTL ITC LT KOTAKBANK AXISBANK HINDUNILVR BAJFINANCE
MARUTI SUNPHARMA HCLTECH M&M TITAN ULTRACEMCO NTPC ONGC POWERGRID ASIANPAINT WIPRO ADANIENT ADANIPORTS
TATASTEEL JSWSTEEL COALINDIA BAJAJFINSV NESTLEIND GRASIM TECHM HINDALCO CIPLA DRREDDY EICHERMOT
BRITANNIA APOLLOHOSP INDUSINDBK HEROMOTOCO BPCL TATACONSUM SBILIFE HDFCLIFE BAJAJ-AUTO SHRIRAMFIN
TRENT BEL HAL LTIM DIVISLAB ADANIGREEN ADANIPOWER AMBUJACEM DLF GODREJCP PIDILITIND SIEMENS DABUR
HAVELLS ICICIGI ICICIPRULI INDIGO IOC IRCTC IRFC JIOFIN LODHA NAUKRI PFC RECLTD SBICARD SHREECEM
TATAPOWER TORNTPHARM TVSMOTOR VBL VEDL ZYDUSLIFE BANKBARODA CANBK PNB UNIONBANK GAIL HINDPETRO
BHEL SAIL NMDC NATIONALUM HINDCOPPER ASHOKLEY CUB FINCABLES HUDCO KNRCON CASTROLIND ETERNAL TMPV
IDFCFIRSTB FEDERALBNK AUBANK BANDHANBNK YESBANK IDEA SUZLON NHPC SJVN IREDA RVNL IRCON NBCC BEML
MAZDOCK COCHINSHIP BDL HFCL TATACOMM TATAELXSI PERSISTENT COFORGE MPHASIS LTTS KPITTECH DIXON
POLYCAB KEI CUMMINSIND ABB BHARATFORG MOTHERSON BOSCHLTD MRF APOLLOTYRE BALKRISIND EXIDEIND
ESCORTS ASHOKLEY TATACHEM UPL PIIND CHAMBLFERT DEEPAKNTR AARTIIND SRF NAVINFLUOR GUJGASLTD IGL
MGL PETRONET OIL CONCOR GMRAIRPORT JINDALSTEL JSWENERGY NATIONALUM LICHSGFIN MANAPPURAM MUTHOOTFIN
CHOLAFIN M&MFIN POONAWALLA ABCAPITAL L&TFH PEL BIOCON AUROPHARMA LUPIN GLENMARK ALKEM IPCALAB
LAURUSLABS GRANULES MAXHEALTH FORTIS METROPOLIS LALPATHLAB INDHOTEL JUBLFOOD DEVYANI PAGEIND
UBL MCDOWELL-N RADICO COLPAL MARICO EMAMILTD TATAMTRDVR OBEROIRLTY GODREJPROP PRESTIGE PHOENIXLTD
INDUSTOWER ZEEL SUNTV PVRINOX DELHIVERY PAYTM POLICYBZR NYKAA KALYANKJIL RAMCOCEM DALBHARAT JKCEMENT
ACC INDIANB IOB CENTRALBK MAHABANK BSE CDSL MCX ANGELONE HDFCAMC NAM-INDIA
""".split()


def candidates():
    seen, out = set(), []
    for s in CANDIDATES:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def token_from_db():
    from db.session import build_engine
    from sqlalchemy import text
    with build_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT TOP 1 AccessToken FROM BrokerToken WHERE TokenType = 3 AND IsActive = 1 "
            "ORDER BY LastRefreshedAt DESC")).fetchone()
    if not row:
        raise SystemExit("no active FIXED3 Dhan token in BrokerToken")
    return row[0]


def resolve_all():
    from instrument_master import InstrumentMaster
    im = InstrumentMaster(cache_dir=str(BACKEND / "data" / "instruments"))
    ok, missing = {}, []
    for s in candidates():
        try:
            r = im.resolve(s)
            ok[s] = {"security_id": str(r["security_id"]), "exchange_segment": r["exchange_segment"]}
        except Exception:
            missing.append(s)
    return ok, missing


def fetch(broker, sym, info, timeframe, chunk_days):
    rows, cur = [], START
    while cur < END:
        nxt = min(cur + timedelta(days=chunk_days - 1), END)
        for attempt in range(4):
            try:
                cs = broker.get_historical_data(sym, info["security_id"], info["exchange_segment"], timeframe, cur, nxt)
                break
            except Exception as e:  # rate limit / transient: back off
                if attempt == 3:
                    print(f"  {sym} {timeframe} {cur:%Y-%m-%d}: failed ({type(e).__name__})", flush=True)
                    cs = []
                time.sleep(2 ** attempt)
        rows += [(int(c.timestamp.timestamp()), c.open, c.high, c.low, c.close, c.volume) for c in cs]
        cur = nxt + timedelta(days=1)
        time.sleep(0.4)
    rows = sorted(set(rows))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-from-db", action="store_true")
    ap.add_argument("--resolve-only", action="store_true")
    ap.add_argument("--top", type=int, default=150)
    a = ap.parse_args()

    ok, missing = resolve_all()
    print(f"resolved {len(ok)} of {len(candidates())}; not found: {' '.join(missing) or 'none'}", flush=True)
    json.dump(ok, open(HERE / "resolved.json", "w"), indent=1)
    if a.resolve_only:
        return

    from brokers.dhan_broker import DhanBroker
    token = token_from_db() if a.token_from_db else os.environ["DHAN_ACCESS_TOKEN"]
    broker = DhanBroker(os.environ["DHAN_CLIENT_ID"], token)

    (HERE / "daily").mkdir(exist_ok=True)
    (HERE / "5min").mkdir(exist_ok=True)
    turnover = {}
    for n, (sym, info) in enumerate(ok.items(), 1):
        p = HERE / "daily" / f"{sym}.pkl"
        if not p.exists():
            pickle.dump(fetch(broker, sym, info, "1day", 365), open(p, "wb"))
        rows = pickle.load(open(p, "rb"))
        last = rows[-250:]
        if len(last) >= 200:
            turnover[sym] = statistics.median(r[4] * r[5] for r in last)
        if n % 25 == 0:
            print(f"daily: {n}/{len(ok)}", flush=True)
    ranked = sorted(turnover, key=turnover.get, reverse=True)[: a.top]
    json.dump([{"symbol": s, "median_turnover_cr": round(turnover[s] / 1e7, 1), **ok[s]} for s in ranked],
              open(HERE / "universe.json", "w"), indent=1)
    print(f"universe: top {len(ranked)} by median daily turnover "
          f"({turnover[ranked[-1]] / 1e7:.0f} .. {turnover[ranked[0]] / 1e7:.0f} Rs crore)", flush=True)

    for n, sym in enumerate(ranked, 1):
        p = HERE / "5min" / f"{sym}.pkl"
        if p.exists():
            continue
        rows = fetch(broker, sym, ok[sym], "5min", 90)
        pickle.dump(rows, open(p, "wb"))
        print(f"5min: {n}/{len(ranked)} {sym} {len(rows)} candles", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
