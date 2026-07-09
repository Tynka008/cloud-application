import app as liga_app
from conftest import FakeCursor, FakeRow

# --- TESTY FUNKCJI POMOCNICZYCH I WALIDACJI LOGIKI BIZNESOWEJ ---
"""
    Test bezpieczeństwa kryptograficznego. 
    Weryfikuje, czy hasła są poprawnie haszowane (nieprzechowywane w tekście jawnym) 
    oraz czy mechanizm sprawdzania poprawności działa dla prawidłowych i błędnych haseł.
"""
def test_hash_password_and_verify_password():
    password = "tajnehaslo123"
    hashed = liga_app.hash_password(password)

    assert hashed != password
    assert liga_app.verify_password(hashed, password) is True
    assert liga_app.verify_password(hashed, "zlehaslo") is False

"""
    Test generatora tokenów.
    Potwierdza, że generowany token jest bezpiecznym ciągiem znaków o odpowiedniej długości, 
    spełniającym wymogi sesji użytkownika.
"""
def test_generate_user_token_returns_string():
    token = liga_app.generate_user_token()
    assert isinstance(token, str)
    assert len(token) > 20

"""
    Test parsera pól liczbowych.
    Sprawdza, czy funkcja poprawnie konwertuje dane wejściowe z formularza (string) 
    na typ całkowity oraz czy poprawnie obsługuje parametry walidacyjne (np. min_value).
"""
def test_parse_int_field_valid_value():
    value, error = liga_app.parse_int_field("10", "liczba", required=True, min_value=0)
    assert value == 10
    assert error is None

"""
    Wektor testowy dla Reguły Biznesowej RB5: 
    Blokada dodania bramki, która po zapisaniu przekroczyłaby końcowy wynik meczu 
    (sprawdzenie spójności danych).
"""
def test_validate_goal_form_rejects_goal_exceeding_score():
    cursor = FakeCursor(
        fetchone_results=[
            FakeRow(1, 10, 20, 1, 0), 
            FakeRow(5, 10),         
            FakeRow(1)                 
        ]
    )

    data, error = liga_app.validate_goal_form(
        mecz_id="1",
        zawodnik_id="5",
        minuta="10",
        typ="normalny",
        cursor=cursor
    )

    assert data is None
    assert "przekroczyłaby wynik" in error

"""
    Test pozytywny walidacji bramki.
    Weryfikuje, czy poprawnie sformatowane dane (zgodne z zasadami ligi) 
    przechodzą proces walidacji bez zgłaszania błędów.
"""
def test_validate_goal_form_accepts_valid_goal():
    cursor = FakeCursor(
        fetchone_results=[
            FakeRow(1, 10, 20, 2, 1),
            FakeRow(5, 10),
            FakeRow(1)
        ]
    )

    data, error = liga_app.validate_goal_form(
        mecz_id="1",
        zawodnik_id="5",
        minuta="70",
        typ="normalny",
        cursor=cursor
    )

    assert error is None
    assert data["mecz_id"] == 1
    assert data["zawodnik_id"] == 5