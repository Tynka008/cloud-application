from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import pyodbc
import os
import secrets
import hashlib
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "projekt-liga-2026-bezpieczny-klucz")

CACHED_DRIVER = None


# =========================
#  POŁĄCZENIE Z BAZĄ DANYCH
# =========================

def get_db_conn():
    """Połączenie z Azure SQL, z cache'owaniem sterownika."""
    global CACHED_DRIVER

    raw_conn_str = os.environ.get("DATABASE_URL")
    if not raw_conn_str:
        return None

    drivers = [CACHED_DRIVER] if CACHED_DRIVER else [
        "{ODBC Driver 18 for SQL Server}",
        "{ODBC Driver 17 for SQL Server}"
    ]

    for driver in drivers:
        if not driver:
            continue

        try:
            conn_str = raw_conn_str.replace("{ODBC Driver 17 for SQL Server}", driver)

            if "18" in driver:
                conn_str += ";Encrypt=yes;TrustServerCertificate=yes;"
                conn_str = conn_str.replace("TrustServerCertificate=no", "TrustServerCertificate=yes")

            conn = pyodbc.connect(conn_str, timeout=5)
            CACHED_DRIVER = driver
            return conn

        except Exception:
            continue

    return None


def hash_password(password: str) -> str:
    """Bezpieczne hashowanie hasła."""
    return generate_password_hash(password)


def verify_password(saved_hash: str, plain_password: str) -> bool:
    """
    Sprawdza hasło.
    Obsługuje nowe hashe Werkzeug oraz stare SHA-256, żeby stare konta dalej działały.
    """
    if not saved_hash or not plain_password:
        return False

    if saved_hash.startswith(("pbkdf2:", "scrypt:")):
        return check_password_hash(saved_hash, plain_password)

    old_sha256 = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
    return old_sha256 == saved_hash


def generate_user_token() -> str:
    """Generuje token użytkownika."""
    return secrets.token_urlsafe(32)


def role_required(*roles):
    """Dekorator kontroli ról."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("rola") not in roles:
                flash("Brak uprawnień do tego zasobu.", "danger")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def parse_int_field(value, field_name, required=True, min_value=None, max_value=None):
    """Walidacja liczby całkowitej z formularza."""
    if value is None or value == "":
        if required:
            return None, f"Pole {field_name} jest wymagane."
        return None, None

    try:
        parsed = int(value)
    except ValueError:
        return None, f"Pole {field_name} musi być liczbą."

    if min_value is not None and parsed < min_value:
        return None, f"Pole {field_name} nie może być mniejsze niż {min_value}."

    if max_value is not None and parsed > max_value:
        return None, f"Pole {field_name} nie może być większe niż {max_value}."

    return parsed, None


def aktualizuj_punkty_po_meczu(cursor, gosp_id, gosc_id, wynik_g, wynik_gosc):
    """RB1: 3 pkt za zwycięstwo, 1 za remis, 0 za porażkę."""
    if wynik_g is None or wynik_gosc is None:
        return

    wynik_g = int(wynik_g)
    wynik_gosc = int(wynik_gosc)

    if wynik_g > wynik_gosc:
        cursor.execute("UPDATE Druzyna SET Punkty = Punkty + 3 WHERE DruzynaID = ?", (gosp_id,))
    elif wynik_gosc > wynik_g:
        cursor.execute("UPDATE Druzyna SET Punkty = Punkty + 3 WHERE DruzynaID = ?", (gosc_id,))
    else:
        cursor.execute("UPDATE Druzyna SET Punkty = Punkty + 1 WHERE DruzynaID = ?", (gosp_id,))
        cursor.execute("UPDATE Druzyna SET Punkty = Punkty + 1 WHERE DruzynaID = ?", (gosc_id,))


def przelicz_punkty(cursor):
    """
    Zeruje punkty i liczy je ponownie ze wszystkich zakończonych meczów.
    Naprawia punkty po edycji/usunięciu meczu.
    """
    cursor.execute("UPDATE Druzyna SET Punkty = 0")

    cursor.execute("""
        SELECT DruzynaGospodarzID, DruzynaGoscID, WynikGospodarz, WynikGosc
        FROM Mecz
        WHERE WynikGospodarz IS NOT NULL
          AND WynikGosc IS NOT NULL
          AND ISNULL(StatusMeczu, 'zakończony') = 'zakończony'
    """)

    for row in cursor.fetchall():
        aktualizuj_punkty_po_meczu(cursor, row[0], row[1], row[2], row[3])


def policz_tabele_z_meczow(cursor):
    """
    Liczy tabelę ligową bezpośrednio z tabeli Mecz.
    Kryteria:
    1. punkty,
    2. bilans bramek,
    3. gole strzelone,
    4. nazwa drużyny.
    """
    cursor.execute("""
        SELECT DruzynaID, Nazwa, Miasto
        FROM Druzyna
    """)
    druzyny = cursor.fetchall()

    tabela = {}

    for d in druzyny:
        tabela[d[0]] = {
            "druzyna_id": d[0],
            "nazwa": d[1],
            "miasto": d[2],
            "punkty": 0,
            "gole_strzelone": 0,
            "gole_stracone": 0,
            "bilans": 0
        }

    cursor.execute("""
        SELECT DruzynaGospodarzID, DruzynaGoscID, WynikGospodarz, WynikGosc
        FROM Mecz
        WHERE WynikGospodarz IS NOT NULL
          AND WynikGosc IS NOT NULL
          AND ISNULL(StatusMeczu, 'zakończony') = 'zakończony'
    """)

    mecze = cursor.fetchall()

    for m in mecze:
        gosp_id = m[0]
        gosc_id = m[1]
        wynik_g = int(m[2])
        wynik_gosc = int(m[3])

        if gosp_id not in tabela or gosc_id not in tabela:
            continue

        tabela[gosp_id]["gole_strzelone"] += wynik_g
        tabela[gosp_id]["gole_stracone"] += wynik_gosc

        tabela[gosc_id]["gole_strzelone"] += wynik_gosc
        tabela[gosc_id]["gole_stracone"] += wynik_g

        if wynik_g > wynik_gosc:
            tabela[gosp_id]["punkty"] += 3
        elif wynik_gosc > wynik_g:
            tabela[gosc_id]["punkty"] += 3
        else:
            tabela[gosp_id]["punkty"] += 1
            tabela[gosc_id]["punkty"] += 1

    for item in tabela.values():
        item["bilans"] = item["gole_strzelone"] - item["gole_stracone"]

    return sorted(
        tabela.values(),
        key=lambda x: (-x["punkty"], -x["bilans"], -x["gole_strzelone"], x["nazwa"])
    )


def validate_match_form(gosp, gosc, data, wynik_g, wynik_gosc, status_meczu, terminarz_id, cursor):
    """Walidacja meczu."""
    gosp_id, error = parse_int_field(gosp, "drużyna gospodarza")
    if error:
        return None, error

    gosc_id, error = parse_int_field(gosc, "drużyna gościa")
    if error:
        return None, error

    terminarz_id_int, error = parse_int_field(terminarz_id, "terminarz")
    if error:
        return None, error

    if gosp_id == gosc_id:
        return None, "Drużyna gospodarzy i gości muszą być różne."

    if not data:
        return None, "Data meczu jest wymagana."
    
    data_sql = data.replace("T", " ")
    if len(data_sql) == 16:  
        data_sql += ":00"

    if status_meczu not in ["planowany", "w trakcie", "zakończony"]:
        return None, "Niepoprawny status meczu."

    wynik_g_int, error = parse_int_field(
        wynik_g,
        "wynik gospodarza",
        required=(status_meczu == "zakończony"),
        min_value=0
    )
    if error:
        return None, error

    wynik_gosc_int, error = parse_int_field(
        wynik_gosc,
        "wynik gościa",
        required=(status_meczu == "zakończony"),
        min_value=0
    )
    if error:
        return None, error

    cursor.execute("""
        SELECT Status
        FROM TerminarzRozgrywek
        WHERE TerminarzID = ?
    """, (terminarz_id_int,))
    terminarz = cursor.fetchone()

    if not terminarz:
        return None, "Wybrany terminarz nie istnieje."

    if terminarz[0] != "aktywny":
        return None, "Mecz można dodać lub edytować tylko w aktywnym terminarzu."

    return {
        "gosp_id": gosp_id,
        "gosc_id": gosc_id,
        "data": data_sql,
        "wynik_g": wynik_g_int,
        "wynik_gosc": wynik_gosc_int,
        "status_meczu": status_meczu,
        "terminarz_id": terminarz_id_int
    }, None


def validate_goal_form(mecz_id, zawodnik_id, minuta, typ, cursor):
    """Walidacja gola i zgodności z wynikiem meczu."""
    mecz_id_int, error = parse_int_field(mecz_id, "mecz")
    if error:
        return None, error

    zawodnik_id_int, error = parse_int_field(zawodnik_id, "zawodnik")
    if error:
        return None, error

    minuta_int, error = parse_int_field(minuta, "minuta", required=True, min_value=1, max_value=120)
    if error:
        return None, error

    if not typ:
        typ = "normalny"

    cursor.execute("""
        SELECT MeczID, DruzynaGospodarzID, DruzynaGoscID, WynikGospodarz, WynikGosc
        FROM Mecz
        WHERE MeczID = ?
    """, (mecz_id_int,))
    mecz = cursor.fetchone()

    if not mecz:
        return None, "Wybrany mecz nie istnieje."

    cursor.execute("""
        SELECT ZawodnikID, DruzynaID
        FROM Zawodnik
        WHERE ZawodnikID = ?
    """, (zawodnik_id_int,))
    zawodnik = cursor.fetchone()

    if not zawodnik:
        return None, "Wybrany zawodnik nie istnieje."

    druzyna_zawodnika = zawodnik[1]
    gospodarz_id = mecz[1]
    gosc_id = mecz[2]
    wynik_g = mecz[3]
    wynik_gosc = mecz[4]

    if druzyna_zawodnika not in [gospodarz_id, gosc_id]:
        return None, "Zawodnik nie należy do żadnej z drużyn grających w tym meczu."

    if wynik_g is None or wynik_gosc is None:
        return None, "Nie można dodać gola do meczu bez zapisanego wyniku."

    limit_goli = int(wynik_g) if druzyna_zawodnika == gospodarz_id else int(wynik_gosc)

    cursor.execute("""
        SELECT COUNT(*)
        FROM Gol g
        JOIN Zawodnik z ON g.ZawodnikID = z.ZawodnikID
        WHERE g.MeczID = ?
        AND z.DruzynaID = ?
    """, (mecz_id_int, druzyna_zawodnika))
    obecne_gole = cursor.fetchone()[0]

    if obecne_gole + 1 > limit_goli:
        return None, "Nie można dodać gola, bo liczba goli tej drużyny przekroczyłaby wynik meczu."

    return {
        "mecz_id": mecz_id_int,
        "zawodnik_id": zawodnik_id_int,
        "druzyna_id": druzyna_zawodnika,
        "minuta": minuta_int,
        "typ": typ
    }, None


def analiza_skutecznosci(zawodnicy):
    """Wyznaczanie najlepszego zawodnika na podstawie liczby goli."""
    if not zawodnicy:
        return None

    najlepszy = None
    max_score = -1

    for z in zawodnicy:
        imie, nazwisko, gole, asysty = z[0], z[1], z[2], z[3]
        score = (gole * 3) + (asysty * 1)

        if score > max_score:
            max_score = score
            najlepszy = {
                "nazwa": f"{imie} {nazwisko}",
                "gole": gole,
                "asysty": asysty,
                "score": score
            }

    return najlepszy


# =========================
#  WIDOKI PUBLICZNE
# =========================

@app.route("/")
def index():
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("index.html", data={})

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                z.Imie,
                z.Nazwisko,
                COUNT(g.GolID) AS Gole,
                0 AS Asysty
            FROM Zawodnik z
            LEFT JOIN Gol g ON g.ZawodnikID = z.ZawodnikID
            GROUP BY z.Imie, z.Nazwisko
        """)
        best_player = analiza_skutecznosci(cursor.fetchall())

        tabela = policz_tabele_z_meczow(cursor)
        lider = tabela[0] if tabela else None

        cursor.execute("""
            SELECT TOP 1 NazwaSezonu, Status
            FROM TerminarzRozgrywek
            ORDER BY DataRozpoczecia DESC
        """)
        sezon = cursor.fetchone()

        cursor.execute("""
            SELECT TOP 1 NazwaSezonu, Status
            FROM TerminarzRozgrywek
            ORDER BY DataRozpoczecia DESC
        """)
        sezon = cursor.fetchone()

        cursor.execute("""
            SELECT TOP 1
                m.DataMeczu, d1.Nazwa AS Gospodarz, d2.Nazwa AS Gosc, m.WynikGospodarz, m.WynikGosc
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            WHERE m.StatusMeczu = 'planowany'
            ORDER BY m.DataMeczu ASC
        """)
        next_match = cursor.fetchone()

        cursor.execute("""
            SELECT TOP 1
                m.DataMeczu, d1.Nazwa AS Gospodarz, d2.Nazwa AS Gosc, m.WynikGospodarz, m.WynikGosc
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            WHERE ISNULL(m.StatusMeczu, 'zakończony') = 'zakończony'
            ORDER BY m.DataMeczu DESC
        """)
        last_match = cursor.fetchone()

        cursor.execute("""
            SELECT TOP 10
                m.DataMeczu, d1.Nazwa AS Gospodarz, d2.Nazwa AS Gosc, m.WynikGospodarz, m.WynikGosc
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            ORDER BY m.DataMeczu DESC
        """)
        recent_matches = cursor.fetchall()

        cursor.execute("SELECT DruzynaID, Nazwa FROM Druzyna ORDER BY Nazwa")
        teams = cursor.fetchall()

    finally:
        conn.close()

    data = {
        "team": lider,
        "player": best_player,
        "season": sezon,
        "next_match": next_match,
        "last_match": last_match,
        "recent_matches": recent_matches,
        "teams": teams
    }

    return render_template("index.html", data=data)

@app.route("/druzyny")
def druzyny_list():
    conn = get_db_conn()
    teams = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("druzyny.html", teams=teams)

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DruzynaID, Nazwa, Miasto, Punkty
            FROM Druzyna
            ORDER BY Punkty DESC, Nazwa
        """)
        teams = cursor.fetchall()

    finally:
        conn.close()

    return render_template("druzyny.html", teams=teams)


@app.route("/druzyna/<int:team_id>")
def druzyna_detail(team_id):
    conn = get_db_conn()
    team = None
    players = []
    matches = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("druzyna.html", team=team, players=players, matches=matches)

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DruzynaID, Nazwa, Miasto, Punkty
            FROM Druzyna
            WHERE DruzynaID = ?
        """, (team_id,))
        team = cursor.fetchone()

        cursor.execute("""
            SELECT ZawodnikID, Imie, Nazwisko, Pozycja
            FROM Zawodnik
            WHERE DruzynaID = ?
            ORDER BY Nazwisko, Imie
        """, (team_id,))
        players = cursor.fetchall()

        cursor.execute("""
            SELECT
                m.DataMeczu,
                d1.Nazwa AS Gospodarz,
                d2.Nazwa AS Gosc,
                m.WynikGospodarz,
                m.WynikGosc
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            WHERE m.DruzynaGospodarzID = ? OR m.DruzynaGoscID = ?
            ORDER BY m.DataMeczu DESC
        """, (team_id, team_id))
        matches = cursor.fetchall()

    finally:
        conn.close()

    return render_template("druzyna.html", team=team, players=players, matches=matches)


@app.route("/zawodnicy")
def zawodnicy_list():
    conn = get_db_conn()
    players = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("zawodnicy.html", players=players)

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT z.ZawodnikID, z.Imie, z.Nazwisko, z.Pozycja, d.Nazwa
            FROM Zawodnik z
            JOIN Druzyna d ON z.DruzynaID = d.DruzynaID
            ORDER BY d.Nazwa, z.Nazwisko, z.Imie
        """)
        players = cursor.fetchall()

    finally:
        conn.close()

    return render_template("zawodnicy.html", players=players)


@app.route("/zawodnik/<int:player_id>")
def zawodnik_detail(player_id):
    conn = get_db_conn()
    player = None
    stats = {"gole": 0}

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("zawodnik.html", player=player, stats=stats)

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT z.ZawodnikID, z.Imie, z.Nazwisko, z.Pozycja, d.Nazwa
            FROM Zawodnik z
            JOIN Druzyna d ON z.DruzynaID = d.DruzynaID
            WHERE z.ZawodnikID = ?
        """, (player_id,))
        player = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) FROM Gol WHERE ZawodnikID = ?", (player_id,))
        stats["gole"] = cursor.fetchone()[0]

    finally:
        conn.close()

    return render_template("zawodnik.html", player=player, stats=stats)


@app.route("/mecze")
def mecze_list():
    conn = get_db_conn()
    matches = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("mecze.html", matches=matches)

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                m.MeczID,
                m.DataMeczu,
                d1.Nazwa AS Gospodarz,
                d2.Nazwa AS Gosc,
                m.WynikGospodarz,
                m.WynikGosc,
                m.StatusMeczu
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            ORDER BY m.DataMeczu DESC
        """)
        matches = cursor.fetchall()

    finally:
        conn.close()

    return render_template("mecze.html", matches=matches)


@app.route("/mecz/<int:mecz_id>")
def mecz_detail(mecz_id):
    conn = get_db_conn()
    if not conn:
        return "Brak DB"

    cursor = conn.cursor()

    cursor.execute("""
        SELECT m.MeczID, m.DataMeczu, d1.Nazwa, d2.Nazwa,
               m.WynikGospodarz, m.WynikGosc, m.StatusMeczu
        FROM Mecz m
        JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
        JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
        WHERE m.MeczID = ?
    """, (mecz_id,))
    mecz = cursor.fetchone()

    cursor.execute("""
        SELECT g.Minuta, g.Typ, z.Imie, z.Nazwisko
        FROM Gol g
        JOIN Zawodnik z ON g.ZawodnikID = z.ZawodnikID
        WHERE g.MeczID = ?
        ORDER BY g.Minuta
    """, (mecz_id,))
    gole = cursor.fetchall()

    conn.close()
    return render_template("mecz.html", mecz=mecz, gole=gole)


@app.route("/strzelcy")
@role_required("Trener", "Administrator")
def strzelcy():
    conn = get_db_conn()
    rows = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("strzelcy.html", rows=rows)

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                z.Imie,
                z.Nazwisko,
                d.Nazwa AS Druzyna,
                COUNT(g.GolID) AS Gole
            FROM Zawodnik z
            JOIN Druzyna d ON z.DruzynaID = d.DruzynaID
            LEFT JOIN Gol g ON g.ZawodnikID = z.ZawodnikID
            GROUP BY z.Imie, z.Nazwisko, d.Nazwa
            ORDER BY Gole DESC, z.Nazwisko, z.Imie
        """)
        rows = cursor.fetchall()

    finally:
        conn.close()

    return render_template("strzelcy.html", rows=rows)


@app.route("/tabela")
def tabela_ligowa():
    conn = get_db_conn()
    rows = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("tabela.html", rows=rows)

    try:
        cursor = conn.cursor()
        rows = policz_tabele_z_meczow(cursor)

    finally:
        conn.close()

    return render_template("tabela.html", rows=rows)


@app.route("/terminarz")
def terminarz():
    conn = get_db_conn()
    sezon = None
    mecze = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("terminarz.html", sezon=sezon, mecze=mecze)

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT TOP 1 TerminarzID, NazwaSezonu, DataRozpoczecia, DataZakonczenia, Status
            FROM TerminarzRozgrywek
            ORDER BY DataRozpoczecia DESC
        """)
        sezon = cursor.fetchone()

        if sezon:
            cursor.execute("""
                SELECT
                    m.DataMeczu,
                    d1.Nazwa AS Gospodarz,
                    d2.Nazwa AS Gosc,
                    m.WynikGospodarz,
                    m.WynikGosc,
                    m.StatusMeczu
                FROM Mecz m
                JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
                JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
                WHERE m.TerminarzID = ?
                ORDER BY m.DataMeczu DESC
            """, (sezon[0],))
            mecze = cursor.fetchall()

    finally:
        conn.close()

    return render_template("terminarz.html", sezon=sezon, mecze=mecze)


# =========================
#  LOGOWANIE / REJESTRACJA
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        l_val = request.form.get("login")
        p_val = request.form.get("password")

        conn = get_db_conn()
        if not conn:
            flash("Brak połączenia z bazą danych.", "danger")
            return render_template("login.html")

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT UzytkownikID, HasloHash, Rola
                FROM Uzytkownik
                WHERE Login = ?
            """, (l_val,))
            user = cursor.fetchone()

            if user and verify_password(user[1], p_val):
                token = generate_user_token()

                cursor.execute("""
                    UPDATE Uzytkownik
                    SET Token = ?
                    WHERE UzytkownikID = ?
                """, (token, user[0]))
                conn.commit()

                session.update({
                    "user_id": user[0],
                    "user_login": l_val,
                    "rola": user[2],
                    "token": token
                })

                flash("Zalogowano pomyślnie!", "success")
                return redirect(url_for("index"))

            flash("Błędny login lub hasło!", "danger")

        finally:
            conn.close()

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        l_val = request.form.get("login")
        p_val = request.form.get("password")

        if not l_val or not p_val:
            flash("Login i hasło są wymagane.", "danger")
            return render_template("register.html")

        hashed = hash_password(p_val)
        token = generate_user_token()

        conn = get_db_conn()
        if not conn:
            flash("Brak połączenia z bazą danych.", "danger")
            return render_template("register.html")

        try:
            cursor = conn.cursor()

            cursor.execute("SELECT Login FROM Uzytkownik WHERE Login = ?", (l_val,))
            if cursor.fetchone():
                flash("Ten login jest już zajęty!", "warning")
            else:
                cursor.execute("""
                    INSERT INTO Uzytkownik (Login, HasloHash, Rola, Token)
                    VALUES (?, ?, 'Uzytkownik', ?)
                """, (l_val, hashed, token))
                conn.commit()

                flash("Konto utworzone! Możesz się zalogować.", "success")
                return redirect(url_for("login"))

        finally:
            conn.close()

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Wylogowano.", "info")
    return redirect(url_for("login"))


# =========================
#  PANEL TRENERA
# =========================

@app.route("/trener")
@role_required("Trener", "Administrator")
def trener_select():
    conn = get_db_conn()
    teams = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("trener_select.html", teams=teams)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DruzynaID, Nazwa FROM Druzyna ORDER BY Nazwa")
        teams = cursor.fetchall()

    finally:
        conn.close()

    return render_template("trener_select.html", teams=teams)


@app.route("/trener/<int:team_id>")
@role_required("Trener", "Administrator")
def trener_view(team_id):
    conn = get_db_conn()
    team = None
    player = None

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("trener.html", team=team, player=player)

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT Nazwa FROM Druzyna WHERE DruzynaID = ?", (team_id,))
        team = cursor.fetchone()

        cursor.execute("""
            SELECT TOP 1
                z.Imie,
                z.Nazwisko,
                COUNT(g.GolID) AS Gole
            FROM Gol g
            JOIN Mecz m ON g.MeczID = m.MeczID
            JOIN Zawodnik z ON g.ZawodnikID = z.ZawodnikID
            WHERE
                (m.DruzynaGospodarzID = ? AND z.DruzynaID = m.DruzynaGoscID)
                OR
                (m.DruzynaGoscID = ? AND z.DruzynaID = m.DruzynaGospodarzID)
            GROUP BY z.Imie, z.Nazwisko
            ORDER BY Gole DESC
        """, (team_id, team_id))
        player = cursor.fetchone()

    finally:
        conn.close()

    return render_template("trener.html", team=team, player=player)


@app.route("/trener/historia/<int:team_id>")
@role_required("Trener", "Administrator")
def trener_historia(team_id):
    conn = get_db_conn()
    team = None
    mecze = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return render_template("trener_historia.html", team=team, mecze=mecze)

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT Nazwa FROM Druzyna WHERE DruzynaID = ?", (team_id,))
        team = cursor.fetchone()

        cursor.execute("""
            SELECT
                m.DataMeczu,
                d1.Nazwa AS Gospodarz,
                d2.Nazwa AS Gosc,
                m.WynikGospodarz,
                m.WynikGosc,
                m.StatusMeczu
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            WHERE m.DruzynaGospodarzID = ? OR m.DruzynaGoscID = ?
            ORDER BY m.DataMeczu DESC
        """, (team_id, team_id))
        mecze = cursor.fetchall()

    finally:
        conn.close()

    return render_template("trener_historia.html", team=team, mecze=mecze)


@app.route("/api/lost_goals/<int:team_id>")
def lost_goals(team_id):
    conn = get_db_conn()

    if not conn:
        return {"labels": [], "values": []}

    labels = []
    values = []

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                przeciwnik.Nazwa,
                COUNT(g.GolID) AS Gole
            FROM Gol g
            JOIN Mecz m ON g.MeczID = m.MeczID
            JOIN Druzyna przeciwnik ON
                (m.DruzynaGospodarzID = ? AND przeciwnik.DruzynaID = m.DruzynaGoscID)
                OR
                (m.DruzynaGoscID = ? AND przeciwnik.DruzynaID = m.DruzynaGospodarzID)
            WHERE g.ZawodnikID IN (
                SELECT ZawodnikID
                FROM Zawodnik
                WHERE DruzynaID = przeciwnik.DruzynaID
            )
            GROUP BY przeciwnik.Nazwa
        """, (team_id, team_id))

        for row in cursor.fetchall():
            labels.append(row[0])
            values.append(row[1])

    finally:
        conn.close()

    return {"labels": labels, "values": values}


# =========================
#  PANEL ADMINA 
# =========================

@app.route("/admin")
@role_required("Administrator")
def admin_panel():
    conn = get_db_conn()
    users = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("index"))

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT UzytkownikID, Login, Rola FROM Uzytkownik ORDER BY Login")
        users = cursor.fetchall()

    finally:
        conn.close()

    return render_template("admin.html", users=users)


@app.route("/promote/<int:uid>/<string:role>")
@role_required("Administrator")
def promote(uid, role):
    allowed_roles = ["Administrator", "Trener", "Uzytkownik"]

    if role not in allowed_roles:
        flash("Niepoprawna rola użytkownika.", "danger")
        return redirect(url_for("admin_panel"))

    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_panel"))

    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE Uzytkownik SET Rola = ? WHERE UzytkownikID = ?", (role, uid))
        conn.commit()
        flash("Zaktualizowano rolę użytkownika.", "success")

    finally:
        conn.close()

    return redirect(url_for("admin_panel"))


@app.route("/admin/druzyny", methods=["GET", "POST"])
@role_required("Administrator")
def admin_druzyny():
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        nazwa = request.form.get("nazwa")
        miasto = request.form.get("miasto")
        punkty = request.form.get("punkty") or 0

        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO Druzyna (Nazwa, Miasto, Punkty)
                VALUES (?, ?, ?)
            """, (nazwa, miasto, int(punkty)))
            conn.commit()
            flash("Dodano drużynę.", "success")

        except Exception as e:
            flash(f"Błąd przy dodawaniu drużyny: {e}", "danger")

    teams = []

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DruzynaID, Nazwa, Miasto, Punkty
            FROM Druzyna
            ORDER BY DruzynaID
        """)
        teams = cursor.fetchall()

    finally:
        conn.close()

    return render_template("admin_druzyny.html", teams=teams)


@app.route("/admin/druzyna/<int:team_id>/edit", methods=["GET", "POST"])
@role_required("Administrator")
def admin_druzyna_edit(team_id):
    conn = get_db_conn()
    team = None

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_druzyny"))

    try:
        cursor = conn.cursor()

        if request.method == "POST":
            nazwa = request.form.get("nazwa")
            miasto = request.form.get("miasto")
            punkty = request.form.get("punkty") or 0

            cursor.execute("""
                UPDATE Druzyna
                SET Nazwa = ?, Miasto = ?, Punkty = ?
                WHERE DruzynaID = ?
            """, (nazwa, miasto, int(punkty), team_id))
            conn.commit()

            flash("Zaktualizowano drużynę.", "success")
            return redirect(url_for("admin_druzyny"))

        cursor.execute("""
            SELECT DruzynaID, Nazwa, Miasto, Punkty
            FROM Druzyna
            WHERE DruzynaID = ?
        """, (team_id,))
        team = cursor.fetchone()

    finally:
        conn.close()

    if not team:
        flash("Nie znaleziono drużyny.", "warning")
        return redirect(url_for("admin_druzyny"))

    return render_template("admin_druzyna_edit.html", team=team)


@app.route("/admin/druzyna/<int:team_id>/delete")
@role_required("Administrator")
def admin_druzyna_delete(team_id):
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_druzyny"))

    try:
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM Gol
            WHERE MeczID IN (
                SELECT MeczID
                FROM Mecz
                WHERE DruzynaGospodarzID = ? OR DruzynaGoscID = ?
            )
        """, (team_id, team_id))

        cursor.execute("""
            DELETE FROM Mecz
            WHERE DruzynaGospodarzID = ? OR DruzynaGoscID = ?
        """, (team_id, team_id))

        cursor.execute("DELETE FROM Zawodnik WHERE DruzynaID = ?", (team_id,))
        cursor.execute("DELETE FROM Druzyna WHERE DruzynaID = ?", (team_id,))

        przelicz_punkty(cursor)
        conn.commit()

        flash("Usunięto drużynę oraz powiązane dane.", "success")

    finally:
        conn.close()

    return redirect(url_for("admin_druzyny"))


@app.route("/admin/zawodnicy", methods=["GET", "POST"])
@role_required("Administrator")
def admin_zawodnicy():
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        imie = request.form.get("imie")
        nazwisko = request.form.get("nazwisko")
        pozycja = request.form.get("pozycja")
        druzyna_id = request.form.get("druzyna_id")

        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO Zawodnik (Imie, Nazwisko, Pozycja, DruzynaID)
                VALUES (?, ?, ?, ?)
            """, (imie, nazwisko, pozycja, int(druzyna_id)))
            conn.commit()
            flash("Dodano zawodnika.", "success")

        except Exception as e:
            flash(f"Błąd przy dodawaniu zawodnika: {e}", "danger")

    players = []
    teams = []

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT z.ZawodnikID, z.Imie, z.Nazwisko, z.Pozycja, d.Nazwa
            FROM Zawodnik z
            JOIN Druzyna d ON z.DruzynaID = d.DruzynaID
            ORDER BY d.Nazwa, z.Nazwisko
        """)
        players = cursor.fetchall()

        cursor.execute("SELECT DruzynaID, Nazwa FROM Druzyna ORDER BY Nazwa")
        teams = cursor.fetchall()

    finally:
        conn.close()

    return render_template("admin_zawodnicy.html", players=players, teams=teams)


@app.route("/admin/zawodnik/<int:player_id>/edit", methods=["GET", "POST"])
@role_required("Administrator")
def admin_zawodnik_edit(player_id):
    conn = get_db_conn()
    player = None
    teams = []

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_zawodnicy"))

    try:
        cursor = conn.cursor()

        if request.method == "POST":
            imie = request.form.get("imie")
            nazwisko = request.form.get("nazwisko")
            pozycja = request.form.get("pozycja")
            druzyna_id = request.form.get("druzyna_id")

            cursor.execute("""
                UPDATE Zawodnik
                SET Imie = ?, Nazwisko = ?, Pozycja = ?, DruzynaID = ?
                WHERE ZawodnikID = ?
            """, (imie, nazwisko, pozycja, int(druzyna_id), player_id))
            conn.commit()

            flash("Zaktualizowano zawodnika.", "success")
            return redirect(url_for("admin_zawodnicy"))

        cursor.execute("""
            SELECT ZawodnikID, Imie, Nazwisko, Pozycja, DruzynaID
            FROM Zawodnik
            WHERE ZawodnikID = ?
        """, (player_id,))
        player = cursor.fetchone()

        cursor.execute("SELECT DruzynaID, Nazwa FROM Druzyna ORDER BY Nazwa")
        teams = cursor.fetchall()

    finally:
        conn.close()

    if not player:
        flash("Nie znaleziono zawodnika.", "warning")
        return redirect(url_for("admin_zawodnicy"))

    return render_template("admin_zawodnik_edit.html", player=player, teams=teams)


@app.route("/admin/zawodnik/<int:player_id>/delete")
@role_required("Administrator")
def admin_zawodnik_delete(player_id):
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_zawodnicy"))

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM Gol WHERE ZawodnikID = ?", (player_id,))
        cursor.execute("DELETE FROM Zawodnik WHERE ZawodnikID = ?", (player_id,))
        conn.commit()

        flash("Usunięto zawodnika oraz jego gole.", "success")

    finally:
        conn.close()

    return redirect(url_for("admin_zawodnicy"))


@app.route("/admin/terminarze", methods=["GET", "POST"])
@role_required("Administrator")
def admin_terminarze():
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        nazwa_sezonu = request.form.get("nazwa_sezonu")
        data_rozpoczecia = request.form.get("data_rozpoczecia")
        data_zakonczenia = request.form.get("data_zakonczenia")
        status = request.form.get("status")

        if not nazwa_sezonu or not data_rozpoczecia or not data_zakonczenia or not status:
            flash("Wszystkie pola terminarza są wymagane.", "danger")
            conn.close()
            return redirect(url_for("admin_terminarze"))

        if status not in ["planowany", "aktywny", "zakończony"]:
            flash("Niepoprawny status terminarza.", "danger")
            conn.close()
            return redirect(url_for("admin_terminarze"))

        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO TerminarzRozgrywek
                (NazwaSezonu, DataRozpoczecia, DataZakonczenia, Status)
                VALUES (?, ?, ?, ?)
            """, (nazwa_sezonu, data_rozpoczecia, data_zakonczenia, status))
            conn.commit()

            flash("Dodano terminarz.", "success")

        except Exception as e:
            flash(f"Błąd przy dodawaniu terminarza: {e}", "danger")

    terminarze = []

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                TerminarzID,
                NazwaSezonu,
                DataRozpoczecia,
                DataZakonczenia,
                Status,
                CASE WHEN Status = 'zakończony' THEN 1 ELSE 0 END AS CzyZakonczony
            FROM TerminarzRozgrywek
            ORDER BY DataRozpoczecia DESC
        """)
        terminarze = cursor.fetchall()

    finally:
        conn.close()

    return render_template("admin_terminarze.html", terminarze=terminarze)


@app.route("/admin/terminarz/<int:terminarz_id>/edit", methods=["GET", "POST"])
@role_required("Administrator")
def admin_terminarz_edit(terminarz_id):
    conn = get_db_conn()
    terminarz = None

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_terminarze"))

    try:
        cursor = conn.cursor()

        if request.method == "POST":
            nazwa_sezonu = request.form.get("nazwa_sezonu")
            data_rozpoczecia = request.form.get("data_rozpoczecia")
            data_zakonczenia = request.form.get("data_zakonczenia")
            status = request.form.get("status")

            if status not in ["planowany", "aktywny", "zakończony"]:
                flash("Niepoprawny status terminarza.", "danger")
                return redirect(url_for("admin_terminarz_edit", terminarz_id=terminarz_id))

            cursor.execute("""
                UPDATE TerminarzRozgrywek
                SET NazwaSezonu = ?,
                    DataRozpoczecia = ?,
                    DataZakonczenia = ?,
                    Status = ?
                WHERE TerminarzID = ?
            """, (nazwa_sezonu, data_rozpoczecia, data_zakonczenia, status, terminarz_id))
            conn.commit()

            flash("Zaktualizowano terminarz.", "success")
            return redirect(url_for("admin_terminarze"))

        cursor.execute("""
            SELECT TerminarzID, NazwaSezonu, DataRozpoczecia, DataZakonczenia, Status
            FROM TerminarzRozgrywek
            WHERE TerminarzID = ?
        """, (terminarz_id,))
        terminarz = cursor.fetchone()

    finally:
        conn.close()

    if not terminarz:
        flash("Nie znaleziono terminarza.", "warning")
        return redirect(url_for("admin_terminarze"))

    return render_template("admin_terminarz_edit.html", terminarz=terminarz)


@app.route("/admin/terminarz/<int:terminarz_id>/delete")
@role_required("Administrator")
def admin_terminarz_delete(terminarz_id):
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_terminarze"))

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM Mecz WHERE TerminarzID = ?", (terminarz_id,))
        liczba_meczy = cursor.fetchone()[0]

        if liczba_meczy > 0:
            flash("Nie można usunąć terminarza, ponieważ są do niego przypisane mecze.", "danger")
            return redirect(url_for("admin_terminarze"))

        cursor.execute("DELETE FROM TerminarzRozgrywek WHERE TerminarzID = ?", (terminarz_id,))
        conn.commit()

        flash("Usunięto terminarz.", "success")

    finally:
        conn.close()

    return redirect(url_for("admin_terminarze"))


@app.route("/admin/mecze", methods=["GET", "POST"])
@role_required("Administrator")
def admin_mecze():
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        try:
            cursor = conn.cursor()

            form_data, error = validate_match_form(
                request.form.get("gospodarz_id"),
                request.form.get("gosc_id"),
                request.form.get("data"),
                request.form.get("wynik_g"),
                request.form.get("wynik_gosc"),
                request.form.get("status_meczu") or "zakończony",
                request.form.get("terminarz_id"),
                cursor
            )

            if error:
                flash(error, "danger")
                return redirect(url_for("admin_mecze"))

            cursor.execute("""
                INSERT INTO Mecz
                (DruzynaGospodarzID, DruzynaGoscID, WynikGospodarz, WynikGosc, DataMeczu, StatusMeczu, TerminarzID)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                form_data["gosp_id"],
                form_data["gosc_id"],
                form_data["wynik_g"],
                form_data["wynik_gosc"],
                form_data["data"],
                form_data["status_meczu"],
                form_data["terminarz_id"]
            ))

            przelicz_punkty(cursor)
            conn.commit()

            flash("Dodano mecz.", "success")

        except Exception as e:
            flash(f"Błąd przy dodawaniu meczu: {e}", "danger")

    matches = []
    teams = []
    terminarze = []

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                m.MeczID,
                m.DataMeczu,
                d1.Nazwa AS Gospodarz,
                d2.Nazwa AS Gosc,
                m.WynikGospodarz,
                m.WynikGosc,
                m.StatusMeczu,
                tr.NazwaSezonu
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            LEFT JOIN TerminarzRozgrywek tr ON m.TerminarzID = tr.TerminarzID
            ORDER BY m.DataMeczu DESC
        """)
        matches = cursor.fetchall()

        cursor.execute("SELECT DruzynaID, Nazwa FROM Druzyna ORDER BY Nazwa")
        teams = cursor.fetchall()

        cursor.execute("""
            SELECT TerminarzID, NazwaSezonu, Status
            FROM TerminarzRozgrywek
            ORDER BY DataRozpoczecia DESC
        """)
        terminarze = cursor.fetchall()

    finally:
        conn.close()

    return render_template("admin_mecze.html", matches=matches, teams=teams, terminarze=terminarze)


@app.route("/admin/mecz/<int:mecz_id>/edit", methods=["GET", "POST"])
@role_required("Administrator")
def admin_mecz_edit(mecz_id):
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_mecze"))

    mecz = None
    teams = []
    terminarze = []

    try:
        cursor = conn.cursor()

        if request.method == "POST":
            form_data, error = validate_match_form(
                request.form.get("gospodarz_id"),
                request.form.get("gosc_id"),
                request.form.get("data"),
                request.form.get("wynik_g"),
                request.form.get("wynik_gosc"),
                request.form.get("status_meczu"),
                request.form.get("terminarz_id"),
                cursor
            )

            if error:
                flash(error, "danger")
                return redirect(url_for("admin_mecz_edit", mecz_id=mecz_id))

            cursor.execute("""
                UPDATE Mecz
                SET DruzynaGospodarzID = ?,
                    DruzynaGoscID = ?,
                    WynikGospodarz = ?,
                    WynikGosc = ?,
                    DataMeczu = ?,
                    StatusMeczu = ?,
                    TerminarzID = ?
                WHERE MeczID = ?
            """, (
                form_data["gosp_id"],
                form_data["gosc_id"],
                form_data["wynik_g"],
                form_data["wynik_gosc"],
                form_data["data"],
                form_data["status_meczu"],
                form_data["terminarz_id"],
                mecz_id
            ))

            przelicz_punkty(cursor)
            conn.commit()

            flash("Zaktualizowano mecz.", "success")
            return redirect(url_for("admin_mecze"))

        cursor.execute("""
            SELECT MeczID, DruzynaGospodarzID, DruzynaGoscID, WynikGospodarz, WynikGosc, DataMeczu, StatusMeczu, TerminarzID
            FROM Mecz
            WHERE MeczID = ?
        """, (mecz_id,))
        mecz = cursor.fetchone()

        cursor.execute("SELECT DruzynaID, Nazwa FROM Druzyna ORDER BY Nazwa")
        teams = cursor.fetchall()

        cursor.execute("""
            SELECT TerminarzID, NazwaSezonu, Status
            FROM TerminarzRozgrywek
            ORDER BY DataRozpoczecia DESC
        """)
        terminarze = cursor.fetchall()

    finally:
        conn.close()

    if not mecz:
        flash("Nie znaleziono meczu.", "warning")
        return redirect(url_for("admin_mecze"))

    return render_template("admin_mecz_edit.html", mecz=mecz, teams=teams, terminarze=terminarze)


@app.route("/admin/mecz/<int:mecz_id>/delete")
@role_required("Administrator")
def admin_mecz_delete(mecz_id):
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_mecze"))

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM Gol WHERE MeczID = ?", (mecz_id,))
        cursor.execute("DELETE FROM Mecz WHERE MeczID = ?", (mecz_id,))
        przelicz_punkty(cursor)
        conn.commit()

        flash("Usunięto mecz i przeliczono punkty.", "success")

    finally:
        conn.close()

    return redirect(url_for("admin_mecze"))


@app.route("/admin/raport_spojnosc")
@role_required("Administrator")
def admin_raport_spojnosc():
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_panel"))

    raport = []

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                m.MeczID,
                m.DataMeczu,
                d1.Nazwa AS Gospodarz,
                d2.Nazwa AS Gosc,
                m.WynikGospodarz,
                m.WynikGosc,
                ISNULL((
                SELECT COUNT(*)
                FROM Gol g
                JOIN Zawodnik z ON g.ZawodnikID = z.ZawodnikID
                WHERE g.MeczID = m.MeczID
                AND z.DruzynaID = m.DruzynaGospodarzID
            ), 0) AS GoleGospodarza,
            ISNULL((
                SELECT COUNT(*)
                FROM Gol g
                JOIN Zawodnik z ON g.ZawodnikID = z.ZawodnikID
                WHERE g.MeczID = m.MeczID
                AND z.DruzynaID = m.DruzynaGoscID
            ), 0) AS GoleGoscia
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            ORDER BY m.DataMeczu DESC
        """)

        for row in cursor.fetchall():
            zgodny = (row[4] or 0) == row[6] and (row[5] or 0) == row[7]
            raport.append((row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], zgodny))

    finally:
        conn.close()

    return render_template("admin_raport_spojnosc.html", raport=raport)


@app.route("/admin/gole", methods=["GET", "POST"])
@role_required("Administrator")
def admin_gole():
    conn = get_db_conn()
    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("index"))

    cursor = conn.cursor()

    if request.method == "POST":
        try:
            form_data, error = validate_goal_form(
                request.form.get("mecz_id"),
                request.form.get("zawodnik_id"),
                request.form.get("minuta"),
                request.form.get("typ"),
                cursor
            )
            if error:
                flash(error, "danger")
            else:
                cursor.execute("""
                INSERT INTO Gol (MeczID, ZawodnikID, Minuta, Typ)
                VALUES (?, ?, ?, ?)
            """, (form_data["mecz_id"], form_data["zawodnik_id"], form_data["minuta"], form_data["typ"]))
                conn.commit()
                flash("Dodano gola.", "success")
        except Exception as e:
            flash(f"Błąd: {e}", "danger")
        conn.close()
        return redirect(url_for("admin_gole"))

    goals = []
    matches = []
    players = []

    try:
        cursor.execute("""
            SELECT g.GolID, m.DataMeczu, d1.Nazwa, d2.Nazwa, z.Imie, z.Nazwisko, g.Minuta, g.Typ
            FROM Gol g
            JOIN Mecz m ON g.MeczID = m.MeczID
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            JOIN Zawodnik z ON g.ZawodnikID = z.ZawodnikID
            ORDER BY m.DataMeczu DESC, g.Minuta
        """)
        goals = cursor.fetchall()

        cursor.execute("""
            SELECT m.MeczID, m.DataMeczu, d1.Nazwa, d2.Nazwa, m.WynikGospodarz, m.WynikGosc
            FROM Mecz m
            JOIN Druzyna d1 ON m.DruzynaGospodarzID = d1.DruzynaID
            JOIN Druzyna d2 ON m.DruzynaGoscID = d2.DruzynaID
            ORDER BY m.DataMeczu DESC
        """)
        matches = cursor.fetchall()

        cursor.execute("""
            SELECT z.ZawodnikID, z.Imie, z.Nazwisko, d.Nazwa
            FROM Zawodnik z
            JOIN Druzyna d ON z.DruzynaID = d.DruzynaID
            ORDER BY d.Nazwa, z.Nazwisko
        """)
        players = cursor.fetchall()

    finally:
        conn.close()

    return render_template("admin_gole.html", goals=goals, matches=matches, players=players)


@app.route("/admin/gol/<int:gol_id>/delete")
@role_required("Administrator")
def admin_gol_delete(gol_id):
    conn = get_db_conn()

    if not conn:
        flash("Brak połączenia z bazą danych.", "danger")
        return redirect(url_for("admin_gole"))

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM Gol WHERE GolID = ?", (gol_id,))
        conn.commit()

        flash("Usunięto gola.", "success")

    finally:
        conn.close()

    return redirect(url_for("admin_gole"))


# =========================
#  API ANALIZ
# =========================

@app.route("/api/najlepsza-druzyna")
def api_najlepsza_druzyna():
    conn = get_db_conn()

    if not conn:
        return jsonify({"error": "Brak połączenia z bazą danych"}), 500

    try:
        cursor = conn.cursor()
        tabela = policz_tabele_z_meczow(cursor)

        if not tabela:
            return jsonify({"message": "Brak danych"})

        return jsonify(tabela[0])

    finally:
        conn.close()


@app.route("/api/najlepszy-zawodnik")
@role_required("Trener", "Administrator")
def api_najlepszy_zawodnik():
    conn = get_db_conn()

    if not conn:
        return jsonify({"error": "Brak połączenia z bazą danych"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP 1
                z.ZawodnikID,
                z.Imie,
                z.Nazwisko,
                d.Nazwa AS Druzyna,
                COUNT(g.GolID) AS Gole
            FROM Zawodnik z
            JOIN Druzyna d ON z.DruzynaID = d.DruzynaID
            LEFT JOIN Gol g ON g.ZawodnikID = z.ZawodnikID
            GROUP BY z.ZawodnikID, z.Imie, z.Nazwisko, d.Nazwa
            ORDER BY Gole DESC, z.Nazwisko, z.Imie
        """)
        row = cursor.fetchone()

        if not row:
            return jsonify({"message": "Brak danych"})

        return jsonify({
            "zawodnik_id": row[0],
            "imie": row[1],
            "nazwisko": row[2],
            "druzyna": row[3],
            "gole": row[4]
        })

    finally:
        conn.close()


@app.route("/api/najskuteczniejszy/<int:team_id>")
@role_required("Trener", "Administrator")
def api_najskuteczniejszy(team_id):
    conn = get_db_conn()

    if not conn:
        return jsonify({"error": "Brak połączenia z bazą danych"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP 1
                z.ZawodnikID,
                z.Imie,
                z.Nazwisko,
                d.Nazwa AS Druzyna,
                COUNT(g.GolID) AS Gole
            FROM Gol g
            JOIN Mecz m ON g.MeczID = m.MeczID
            JOIN Zawodnik z ON g.ZawodnikID = z.ZawodnikID
            JOIN Druzyna d ON z.DruzynaID = d.DruzynaID
            WHERE
                (
                    m.DruzynaGospodarzID = ?
                    AND z.DruzynaID = m.DruzynaGoscID
                )
                OR
                (
                    m.DruzynaGoscID = ?
                    AND z.DruzynaID = m.DruzynaGospodarzID
                )
            GROUP BY z.ZawodnikID, z.Imie, z.Nazwisko, d.Nazwa
            ORDER BY Gole DESC, z.Nazwisko, z.Imie
        """, (team_id, team_id))
        row = cursor.fetchone()

        if not row:
            return jsonify({"message": "Brak danych"})

        return jsonify({
            "zawodnik_id": row[0],
            "imie": row[1],
            "nazwisko": row[2],
            "druzyna": row[3],
            "gole": row[4]
        })

    finally:
        conn.close()


if __name__ == "__main__":
    app.run(debug=True)
