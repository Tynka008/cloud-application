import app as liga_app
from conftest import FakeCursor, FakeConnection, FakeRow

# --- TESTY KONTROLI DOSTĘPU (RBAC) ORAZ LOGIKI ADMINISTRACYJNEJ ---
"""
    Test bezpieczeństwa dostępu (RBAC). 
    Sprawdza, czy zasób dla użytkowników zalogowanych jest chroniony 
    przed dostępem przez anonimowe zapytania.
"""
def test_strzelcy_requires_role(client):
    response = client.get("/strzelcy", follow_redirects=False)
    assert response.status_code in [302, 303]

"""
    Test bezpieczńestwa uprawnień (OWASP).
    Weryfikuje, czy standardowy użytkownik lub osoba niezalogowana nie ma 
    dostępu do panelu zarządzania administracyjnego.
"""
def test_admin_panel_requires_admin(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code in [302, 303]

"""
    Test pozytywnej autoryzacji administratora.
    Potwierdza, że użytkownik z odpowiednią rolą w sesji otrzymuje dostęp 
    do panelu po poprawnej weryfikacji w bazie danych.
"""
def test_admin_panel_allowed_for_admin(monkeypatch, logged_admin):
    cursor = FakeCursor(fetchall_results=[[[1, "admin", "Administrator"]]])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(liga_app, "get_db_conn", lambda: conn)

    response = logged_admin.get("/admin")
    assert response.status_code == 200

"""
    Wektor testowy dla Reguły Biznesowej RB2: 
    Walidacja danych wejściowych – blokada utworzenia meczu, w którym 
    gospodarz jest jednocześnie gościem (unikanie błędów logicznych).
"""
def test_admin_mecze_post_rejects_same_team(monkeypatch, logged_admin):
    cursor = FakeCursor(
        fetchall_results=[
            [], 
            [FakeRow(1, "Orły"), FakeRow(2, "Pogoń")],
            [FakeRow(1, "Sezon", "aktywny")]
        ]
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr(liga_app, "get_db_conn", lambda: conn)

    response = logged_admin.post("/admin/mecze", data={
        "gospodarz_id": "1",
        "gosc_id": "1",  
        "data": "2026-05-27",
        "wynik_g": "1",
        "wynik_gosc": "0",
        "status_meczu": "zakończony",
        "terminarz_id": "1"
    }, follow_redirects=True)

    assert conn.committed is False

"""
    Wektor testowy dla Wymagania F9: 
    Ochrona spójności historycznej danych. Blokada dodawania nowych meczów 
    do terminarzy, które zostały już zamknięte (status: zakończony).
"""
def test_admin_mecze_post_rejects_inactive_terminarz(monkeypatch, logged_admin):
    cursor = FakeCursor(
        fetchall_results=[
            [], 
            [FakeRow(1, "Orły"), FakeRow(2, "Pogoń")],
            [FakeRow(1, "Sezon Archiwalny", "zakończony")]  
        ]
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr(liga_app, "get_db_conn", lambda: conn)

    response = logged_admin.post("/admin/mecze", data={
        "gospodarz_id": "1",
        "gosc_id": "2",
        "data": "2026-05-27",
        "wynik_g": "0",
        "wynik_gosc": "0",
        "status_meczu": "planowany",
        "terminarz_id": "1"
    }, follow_redirects=True)

    assert conn.committed is False