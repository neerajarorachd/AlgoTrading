# Local Development

AlgoTrading is developed and tested locally. During Phase 1, the Azure VM hosts
only SQL Server Express. The existing `Trading` project is independent and must not
be modified.

## Local Setup

Use Python 3.11 and create the repository-local environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Install Microsoft ODBC Driver 18 for SQL Server on Windows before using the database.
Do not commit `.env` or put credentials in source code.

## Azure SQL Tunnel

SQL Server listens on the Azure VM loopback interface. Keep Azure NSG inbound access
restricted to SSH; do not expose TCP port 1433 publicly.

Start the tunnel in a separate PowerShell window:

```powershell
ssh -L 1433:localhost:1433 tradinguser@<AZURE_VM_PUBLIC_IP> -N
```

The local `.env` connection string must use `localhost:1433`, as shown in
`.env.example`. The backend must never connect to the Azure public IP for SQL Server.

## Tests

Run tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

The database configuration tests validate that public endpoints are rejected and
that ODBC Driver 18 is required.