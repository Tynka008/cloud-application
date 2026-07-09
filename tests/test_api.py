import app as liga_app
from conftest import FakeCursor, FakeConnection, FakeRow

# --- TESTY INTEGRACYJNE API ---
"""
    Testuje endpoint pobierający dane o najlepszej drużynie.
    Weryfikuje poprawność mapowania danych z bazy (FakeCursor) na format JSON 
    zwracany przez API oraz obsługę logiki biznesowej (np. obliczanie punktów).
"""
def test_api_najlepsza_druzyna(monkeypatch, client):
    cursor = FakeCursor(
        fetchall_results=[
            [FakeRow(1, "Orły", "Stargard"), FakeRow(2, "Pogoń", "Szczecin")],
            [FakeRow(1, 2, 3, 1)]
        ]
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr(liga_app, "get_db_conn", lambda: conn)

    response = client.get("/api/najlepsza-druzyna")
    assert response.status_code == 200

    data = response.get_json()
    assert data["nazwa"] == "Orły"
    assert data["punkty"] == 3

"""
    Test zabezpieczeń dostępu (Access Control).
    Sprawdza, czy endpoint niedostępny dla użytkowników anonimowych poprawnie 
    przekierowuje (redirect) nieautoryzowane zapytanie.
"""
def test_api_najlepszy_zawodnik_requires_role(client):
    response = client.get("/api/najlepszy-zawodnik", follow_redirects=False)
    assert response.status_code in [302, 303]

"""
    Test zaawansowanej logiki biznesowej z aktywną sesją użytkownika (rola: Trener).
    Weryfikuje poprawność zwracanych danych dla konkretnego parametru ID drużyny
    oraz potwierdza, że autoryzowany użytkownik ma dostęp do tego zasobu.
"""
def test_api_najskuteczniejszy_algorytm(monkeypatch, logged_trener):
    cursor = FakeCursor(fetchone_results=[
        FakeRow(7, "Robert", "Lewandowski", "FC Barcelona", 4)
    ])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(liga_app, "get_db_conn", lambda: conn)

    response = logged_trener.get("/api/najskuteczniejszy/1")
    
    assert response.status_code == 200
    data = response.get_json()
    assert data["imie"] == "Robert"
    assert data["gole"] == 4