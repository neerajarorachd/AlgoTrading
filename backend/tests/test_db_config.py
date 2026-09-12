import pytest

from db_config import load_database_config


VALID_ENVIRONMENT = {
    "DB_CONNECTION_STRING": (
        "mssql+pyodbc://algo_app:secret@localhost:1433/trading_db"
        "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
    )
}


def test_load_database_config_accepts_the_ssh_tunnel_endpoint():
    config = load_database_config(VALID_ENVIRONMENT)

    assert config.host == "localhost"
    assert config.port == 1433
    assert config.database == "trading_db"
    assert config.username == "algo_app"
    assert config.driver == "ODBC Driver 18 for SQL Server"


def test_load_database_config_rejects_a_public_database_endpoint():
    environment = {
        "DB_CONNECTION_STRING": VALID_ENVIRONMENT["DB_CONNECTION_STRING"].replace(
            "localhost", "azure-vm.example.com"
        )
    }

    with pytest.raises(ValueError, match="localhost:1433"):
        load_database_config(environment)


def test_load_database_config_requires_odbc_driver_18():
    environment = {
        "DB_CONNECTION_STRING": VALID_ENVIRONMENT["DB_CONNECTION_STRING"].replace(
            "ODBC+Driver+18", "ODBC+Driver+17"
        )
    }

    with pytest.raises(ValueError, match="ODBC Driver 18"):
        load_database_config(environment)


def test_load_database_config_accepts_a_local_sqlite_file():
    config = load_database_config({"DB_CONNECTION_STRING": "sqlite:///local_dev.db"})

    assert config.backend == "sqlite"
    assert config.database == "local_dev.db"
    assert config.url == "sqlite:///local_dev.db"


def test_load_database_config_accepts_in_memory_sqlite():
    config = load_database_config({"DB_CONNECTION_STRING": "sqlite:///:memory:"})

    assert config.backend == "sqlite"
    assert config.database == ":memory:"


def test_load_database_config_rejects_an_unsupported_scheme():
    with pytest.raises(ValueError, match="mssql\\+pyodbc.*sqlite"):
        load_database_config({"DB_CONNECTION_STRING": "postgresql://user:pw@localhost/db"})
