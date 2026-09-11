# Reference copy only — the live file runs at ~/Trading/LibSQLServerTokenMirror.py
# on the VM, deployed there deliberately (Trading isn't under version control, so
# this is the only durable record of what's actually running). Do NOT import this
# from AlgoTrading's own backend — it belongs to Trading's process/venv.
#
# Called from ~/Trading/LibRefreshToken.py's refresh_all_dhan_tokens() loop (the
# one deliberate, additive edit to Trading's own code — see CLAUDE.md "Broker
# token pool"). Connection string delivered via ~/Trading/.sqlserver.env
# (EnvironmentFile= in dhan_token.service), never hardcoded here.

import os
import threading
import pyodbc


class SQLServerTokenMirror:
    """Best-effort mirror of BrokerAccount/BrokerToken into AlgoTrading's SQL
    Server, so AlgoTrading can consume live-refreshed Dhan tokens.

    NEVER raises — a mirror failure must never interrupt Trading's own token
    refresh, which is the thing that actually keeps live trading working.
    Standalone: uses raw pyodbc directly, shares no code with DBBase/DBOps,
    so nothing here can be caused by, or cause, a change to the methods
    every other Trading module depends on.
    """

    _lock = threading.Lock()

    def __init__(self, conn_str=None):
        self._conn_str = conn_str or os.environ.get("TRADING_SQLSERVER_CONN_STR")

    def mirror_account(self, account_id, broker, client_id, api_key=None, api_secret=None, is_active=1):
        if not self._conn_str:
            return
        try:
            with self._lock, pyodbc.connect(self._conn_str, timeout=5) as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM BrokerAccount WHERE AccountID = ?", account_id)
                if cur.fetchone():
                    cur.execute(
                        "UPDATE BrokerAccount SET Broker=?, ClientID=?, ApiKey=?, ApiSecret=?, IsActive=? "
                        "WHERE AccountID=?",
                        broker, client_id, api_key, api_secret, is_active, account_id,
                    )
                else:
                    cur.execute(
                        "INSERT INTO BrokerAccount (AccountID, Broker, ClientID, ApiKey, ApiSecret, IsActive, "
                        "CreatedAt) VALUES (?,?,?,?,?,?,SYSUTCDATETIME())",
                        account_id, broker, client_id, api_key, api_secret, is_active,
                    )
                conn.commit()
        except Exception as e:
            print(f"WARNING SQL Server account mirror failed (non-fatal): {e}", flush=True)

    def mirror_token(self, token_id, account_id, token_type, access_token,
                      refresh_token, expires_at, last_refreshed_at, is_active=1):
        if not self._conn_str:
            return
        try:
            with self._lock, pyodbc.connect(self._conn_str, timeout=5) as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM BrokerToken WHERE TokenID = ?", token_id)
                if cur.fetchone():
                    cur.execute(
                        "UPDATE BrokerToken SET AccountID=?, TokenType=?, AccessToken=?, "
                        "RefreshToken=?, ExpiresAt=?, LastRefreshedAt=?, IsActive=?, "
                        "UpdatedAt=SYSUTCDATETIME() WHERE TokenID=?",
                        account_id, token_type, access_token, refresh_token,
                        expires_at, last_refreshed_at, is_active, token_id,
                    )
                else:
                    cur.execute(
                        "INSERT INTO BrokerToken (TokenID, AccountID, TokenType, AccessToken, "
                        "RefreshToken, ExpiresAt, LastRefreshedAt, IsActive, CreatedAt, UpdatedAt) "
                        "VALUES (?,?,?,?,?,?,?,?,SYSUTCDATETIME(),SYSUTCDATETIME())",
                        token_id, account_id, token_type, access_token,
                        refresh_token, expires_at, last_refreshed_at, is_active,
                    )
                conn.commit()
        except Exception as e:
            print(f"WARNING SQL Server token mirror failed for TokenID={token_id} (non-fatal): {e}", flush=True)
