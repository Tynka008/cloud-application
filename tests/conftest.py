import os
import sys
import types
import pytest

# --- KONFIGURACJA ŚCIEŻKI PROJEKTU ---
# Umożliwia importowanie modułów aplikacji z poziomu folderu testowego
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# --- IZOLACJA BAZY DANYCH ---
# Blokuje możliwość nawiązania rzeczywistego połączenia z bazą danych podczas testów
fake_pyodbc = types.ModuleType("pyodbc")

def fake_connect(*args, **kwargs):
    raise RuntimeError("pyodbc.connect nie powinien być używany w testach jednostkowych")

fake_pyodbc.connect = fake_connect
sys.modules["pyodbc"] = fake_pyodbc

import app as liga_app

# --- FIXTURY ŚRODOWISKA APLIKACJI (FLASK) ---
# Przygotowanie instancji aplikacji w trybie testowym
@pytest.fixture
def app():
    liga_app.app.config.update({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key"
    })
    return liga_app.app

# Klient testowy do symulacji zapytań HTTP
@pytest.fixture
def client(app):
    return app.test_client()

# --- MOCKI WARSTWY DOSTĘPU DO DANYCH ---
class FakeRow:
    def __init__(self, *values):
        self.values = values

    def __getitem__(self, index):
        return self.values[index]


class FakeCursor:
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.fetchone_results = fetchone_results or []
        self.fetchall_results = fetchall_results or []
        self.queries = []
        self.executed_updates = []
        self.description = [("id",), ("name",)]  # Wymagane przez niektóre widoki Flask

    def execute(self, query, params=None):
        self.queries.append(query)
        if any(keyword in query.upper() for keyword in ("UPDATE", "INSERT", "DELETE")):
            self.executed_updates.append((query, params))
        return self

    def fetchone(self):
        if self.fetchone_results:
            return self.fetchone_results.pop(0)
        return None

    def fetchall(self):
        if self.fetchall_results:
            return self.fetchall_results.pop(0)
        return []


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

# --- FIXTURY UWIERZYTELNIANIA ---
# Symulacja sesji użytkownika z rolą Administratora
@pytest.fixture
def logged_admin(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["user_login"] = "admin"
        sess["rola"] = "Administrator"
        sess["token"] = "test-token"
    return client

# Symulacja sesji użytkownika z rolą Trenera
@pytest.fixture
def logged_trener(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 2
        sess["user_login"] = "trener"
        sess["rola"] = "Trener"
        sess["token"] = "test-token"
    return client