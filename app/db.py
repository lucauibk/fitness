"""SQLite-Speicher der Fitness-App. Aufbau bewusst wie beim Studienplaner:
ein SCHEMA-Skript, additive Migrationen und generische CRUD-Helfer mit Spalten-Whitelist."""
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)
DB_PATH = Path(os.environ.get("FITNESS_DB", Path(__file__).parent.parent / "fitness.db"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", DB_PATH.parent / "backups"))
BACKUP_KEEP_DAYS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    color TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    muscle_group TEXT,                 -- brust | ruecken | beine | schultern | arme | rumpf
    unit TEXT DEFAULT 'kg',            -- kg | koerper (Klimmzuege o. ae.)
    archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    active INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS split_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    split_id INTEGER NOT NULL,
    position INTEGER DEFAULT 0,
    label TEXT NOT NULL,               -- "Push", "Pull", "Beine"
    weekday INTEGER,                   -- veraltet, nur noch fuer die Migration gelesen
    weekdays TEXT                      -- "2,4" = Mittwoch und Freitag; NULL = kein fester Tag
);
CREATE TABLE IF NOT EXISTS split_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    split_day_id INTEGER NOT NULL,
    exercise_id INTEGER NOT NULL,
    position INTEGER DEFAULT 0,
    target_sets INTEGER DEFAULT 3,
    target_reps_min INTEGER DEFAULT 8,   -- Wiederholungs-Bereich: unten einsteigen,
    target_reps_max INTEGER DEFAULT 12,  -- oben angekommen -> Gewicht erhoehen
    target_weight REAL,
    rest_seconds INTEGER,              -- empfohlene Satzpause
    notes TEXT
);
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    split_day_id INTEGER,
    title TEXT,                        -- Beschriftung, falls ohne Split-Tag trainiert wurde
    started_at TEXT,
    finished_at TEXT,
    notes TEXT,
    client_id TEXT UNIQUE               -- macht das Nachsenden aus dem Ausgang idempotent
);
CREATE TABLE IF NOT EXISTS sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id INTEGER NOT NULL,
    exercise_id INTEGER NOT NULL,
    set_no INTEGER DEFAULT 1,
    reps INTEGER,
    weight REAL,
    rpe REAL,
    warmup INTEGER DEFAULT 0     -- zaehlt nicht in Volumen, Bestleistungen und Progression
);
CREATE TABLE IF NOT EXISTS bodyweight (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    date TEXT NOT NULL,                -- YYYY-MM-DD
    kg REAL NOT NULL,
    UNIQUE(profile_id, date)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    sleep INTEGER, stress INTEGER, energy INTEGER,   -- je 1..5
    soreness TEXT,                                   -- JSON {"beine": 2, ...}, 0..3
    readiness INTEGER,
    client_id TEXT UNIQUE,
    UNIQUE(profile_id, date)
);
CREATE TABLE IF NOT EXISTS cardio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    activity TEXT NOT NULL,
    minutes REAL NOT NULL,
    distance_km REAL, kcal REAL, avg_hr INTEGER, notes TEXT,
    client_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS mobility_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    routine TEXT NOT NULL,
    client_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT UNIQUE,
    name TEXT NOT NULL,
    brand TEXT,
    kcal REAL NOT NULL, protein REAL, carbs REAL, fat REAL,   -- je 100 g
    serving_g REAL,
    source TEXT DEFAULT 'eigen',                               -- off | eigen
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS food_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    meal TEXT NOT NULL,                  -- fruehstueck | mittag | abend | snack
    food_id INTEGER,
    name TEXT,                           -- Kopie, falls das Lebensmittel spaeter verschwindet
    grams REAL NOT NULL,
    kcal REAL, protein REAL, carbs REAL, fat REAL,   -- fuer diese Menge, eingefroren
    client_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS water_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    ml INTEGER NOT NULL,                 -- positiv = getrunken, negativ = Korrektur
    client_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS body_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    source TEXT DEFAULT 'manuell',       -- manuell | vicoach
    weight_kg REAL, body_fat_pct REAL, body_fat_estimated INTEGER DEFAULT 0,
    muscle_mass_kg REAL, water_pct REAL, visceral_fat REAL, bmr_kcal REAL, bio_age REAL,
    waist_cm REAL, hip_cm REAL, chest_cm REAL, arm_cm REAL, thigh_cm REAL, neck_cm REAL,
    notes TEXT,
    client_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_food_log_day ON food_log(profile_id, date);
CREATE INDEX IF NOT EXISTS idx_water_log_day ON water_log(profile_id, date);
CREATE INDEX IF NOT EXISTS idx_cardio_day ON cardio(profile_id, date);
CREATE INDEX IF NOT EXISTS idx_body_day ON body_measurements(profile_id, date);
CREATE INDEX IF NOT EXISTS idx_sets_workout ON sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_sets_exercise ON sets(exercise_id);
CREATE INDEX IF NOT EXISTS idx_workouts_profile ON workouts(profile_id, started_at);
CREATE INDEX IF NOT EXISTS idx_split_days_split ON split_days(split_id, position);
CREATE INDEX IF NOT EXISTS idx_split_ex_day ON split_exercises(split_day_id, position);
"""

# Spalten, die nach dem ersten Release dazugekommen sind – bestehende DBs bekommen sie
# per ALTER TABLE. Neue Spalten immer in BEIDE Stellen (SCHEMA oben und hier).
MIGRATIONS = {
    # Ein Trainingstag kann an mehreren Wochentagen liegen (vorher nur einer)
    "split_days": {"weekdays": "TEXT"},
    # Angaben fuer den Plan-Assistenten
    "profiles": {"birth_year": "INTEGER", "sex": "TEXT", "height_cm": "REAL",
                 "goal": "TEXT", "experience": "TEXT", "days_per_week": "INTEGER",
                 "session_minutes": "INTEGER", "limitations": "TEXT",
                 # Wochentage, an denen trainiert werden kann ("0,2,4"), und Muskel-Fokus ("arme,po")
                 "train_weekdays": "TEXT", "focus": "TEXT",
                 # Ernaehrung: Ziele (nutrition_auto=1 -> berechnet)
                 "activity_level": "TEXT", "kcal_goal": "INTEGER", "protein_goal": "INTEGER",
                 "carbs_goal": "INTEGER", "fat_goal": "INTEGER",
                 "nutrition_auto": "INTEGER DEFAULT 1", "water_goal_ml": "INTEGER"},
    # Empfohlene Satzpause je Uebung
    "split_exercises": {"rest_seconds": "INTEGER"},
    # Aufwaermsatz: zaehlt nicht in Volumen, Bestleistungen und Progression
    "sets": {"warmup": "INTEGER DEFAULT 0"},
    # Bewegungsmuster: danach waehlt der Assistent Uebungen aus
    "exercises": {"pattern": "TEXT", "kind": "TEXT",
                  # Uebungsbibliothek
                  "instructions": "TEXT", "mistakes": "TEXT", "muscles_primary": "TEXT",
                  "muscles_secondary": "TEXT", "equipment": "TEXT", "video_query": "TEXT"},
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(c):
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Alter Einzel-Wochentag -> neue Liste. Betrifft nur noch nicht umgestellte Zeilen,
    # laeuft also gefahrlos bei jedem Start.
    c.execute("UPDATE split_days SET weekdays = CAST(weekday AS TEXT)"
              " WHERE weekdays IS NULL AND weekday IS NOT NULL")


def parse_weekdays(value):
    """"2,4" oder [2, 4] -> [2, 4]. Leer oder NULL -> []."""
    if not value:
        return []
    parts = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    out = []
    for part in parts:
        try:
            day = int(str(part).strip())
        except ValueError:
            continue
        if 0 <= day <= 6:
            out.append(day)
    return sorted(set(out))


def format_weekdays(days):
    """[4, 2, 2] -> "2,4". Leere Liste -> NULL (kein fester Tag)."""
    days = sorted({int(d) for d in (days or []) if 0 <= int(d) <= 6})
    return ",".join(str(d) for d in days) or None


FOCUS = ("brust", "ruecken", "schultern", "arme", "beine", "po", "rumpf")


def parse_focus(value):
    return [f for f in str(value or "").split(",") if f in FOCUS]


def profile_out(row):
    """Profil fuer die API: Wochentage und Fokus als Listen."""
    if row is None:
        return None
    row = dict(row)
    row["train_weekdays"] = parse_weekdays(row.get("train_weekdays"))
    row["focus"] = parse_focus(row.get("focus"))
    return row


def day_out(row):
    """Trainingstag fuer die API: Wochentage als Liste, die alte Spalte faellt weg."""
    if row is None:
        return None
    row = dict(row)
    row["weekdays"] = parse_weekdays(row.get("weekdays"))
    row.pop("weekday", None)
    return row


# Uebungs-Katalog fuer den ersten Start: Name, Muskelgruppe, Einheit
STARTER_EXERCISES = [
    ("Bankdrücken", "brust", "kg"),
    ("Schrägbankdrücken Kurzhantel", "brust", "kg"),
    ("Brustpresse", "brust", "kg"),
    ("Butterfly", "brust", "kg"),
    ("Liegestütze", "brust", "koerper"),
    ("Dips", "brust", "koerper"),
    ("Klimmzüge", "ruecken", "koerper"),
    ("Latzug", "ruecken", "kg"),
    ("Rudern Kabel", "ruecken", "kg"),
    ("Langhantelrudern", "ruecken", "kg"),
    ("Kreuzheben", "ruecken", "kg"),
    ("Hyperextension", "ruecken", "koerper"),
    ("Kniebeuge", "beine", "kg"),
    ("Beinpresse", "beine", "kg"),
    ("Ausfallschritte", "beine", "kg"),
    ("Beinstrecker", "beine", "kg"),
    ("Beinbeuger", "beine", "kg"),
    ("Rumänisches Kreuzheben", "beine", "kg"),
    ("Wadenheben", "beine", "kg"),
    ("Hip Thrust", "beine", "kg"),
    ("Schulterdrücken", "schultern", "kg"),
    ("Seitheben", "schultern", "kg"),
    ("Frontheben", "schultern", "kg"),
    ("Reverse Butterfly", "schultern", "kg"),
    ("Face Pulls", "schultern", "kg"),
    ("Bizepscurls Kurzhantel", "arme", "kg"),
    ("Hammercurls", "arme", "kg"),
    ("SZ-Curls", "arme", "kg"),
    ("Trizepsdrücken Kabel", "arme", "kg"),
    ("Trizeps über Kopf", "arme", "kg"),
    ("Enges Bankdrücken", "arme", "kg"),
    ("Unterarmstütz (Plank)", "rumpf", "koerper"),
    ("Crunches", "rumpf", "koerper"),
    ("Beinheben hängend", "rumpf", "koerper"),
    ("Russian Twist", "rumpf", "kg"),
    ("Käfer (Dead Bug)", "rumpf", "koerper"),
    ("Rudergerät", "rumpf", "koerper"),
    ("Laufband", "beine", "koerper"),
]

# Beispiel-Split: Tag -> [(Uebung, Saetze, min, max)]
STARTER_SPLIT = [
    ("Push", 0, [("Bankdrücken", 4, 6, 8), ("Schulterdrücken", 3, 8, 12),
                 ("Schrägbankdrücken Kurzhantel", 3, 8, 12), ("Seitheben", 3, 12, 15),
                 ("Trizepsdrücken Kabel", 3, 10, 15)]),
    ("Pull", 2, [("Klimmzüge", 4, 5, 8), ("Langhantelrudern", 4, 6, 8),
                 ("Latzug", 3, 8, 12), ("Face Pulls", 3, 12, 20),
                 ("Bizepscurls Kurzhantel", 3, 10, 15)]),
    ("Beine", 4, [("Kniebeuge", 4, 5, 8), ("Rumänisches Kreuzheben", 3, 8, 12),
                  ("Beinpresse", 3, 10, 15), ("Beinbeuger", 3, 10, 15),
                  ("Wadenheben", 4, 12, 20)]),
]


# Bewegungsmuster der mitgelieferten Uebungen. Uebungen ohne Muster (Cardio, eigene)
# nimmt der Assistent nicht.
COMPOUND_PATTERNS = {"h_push", "v_push", "h_pull", "v_pull", "squat", "hinge", "lunge"}
STARTER_PATTERNS = {
    "Bankdrücken": "h_push", "Schrägbankdrücken Kurzhantel": "h_push", "Brustpresse": "h_push",
    "Liegestütze": "h_push", "Dips": "h_push", "Butterfly": "chest_iso",
    "Kabelzug-Fliegende": "chest_iso",
    "Klimmzüge": "v_pull", "Latzug": "v_pull", "Enger Latzug": "v_pull",
    "Langhantelrudern": "h_pull", "Rudern Kabel": "h_pull", "Rudern Maschine": "h_pull",
    "Schulterdrücken": "v_push", "Kurzhantel-Schulterdrücken": "v_push",
    "Seitheben": "lateral", "Seitheben Kabel": "lateral",
    "Face Pulls": "rear_delt", "Reverse Butterfly": "rear_delt",
    "Kniebeuge": "squat", "Beinpresse": "squat",
    "Kreuzheben": "hinge", "Rumänisches Kreuzheben": "hinge",
    "Ausfallschritte": "lunge", "Bulgarian Split Squat": "lunge",
    "Beinstrecker": "leg_iso_front", "Beinbeuger": "leg_iso_back",
    "Beinbeuger sitzend": "leg_iso_back", "Hyperextension": "leg_iso_back",
    "Hip Thrust": "glutes", "Kabel-Kickback": "glutes",
    "Wadenheben": "calves", "Wadenheben sitzend": "calves",
    "Bizepscurls Kurzhantel": "biceps", "Hammercurls": "biceps", "SZ-Curls": "biceps",
    "Trizepsdrücken Kabel": "triceps", "Trizeps über Kopf": "triceps",
    "Enges Bankdrücken": "triceps",
    "Unterarmstütz (Plank)": "core", "Crunches": "core", "Beinheben hängend": "core",
    "Russian Twist": "core", "Käfer (Dead Bug)": "core", "Pallof Press": "core",
    "Cable Crunch": "core",
}
# Einmalig ergaenzte Uebungen, damit jedes Muster Auswahl hat
CATALOG_V2 = [
    ("Enger Latzug", "ruecken", "kg"), ("Rudern Maschine", "ruecken", "kg"),
    ("Kurzhantel-Schulterdrücken", "schultern", "kg"), ("Seitheben Kabel", "schultern", "kg"),
    ("Kabelzug-Fliegende", "brust", "kg"), ("Bulgarian Split Squat", "beine", "kg"),
    ("Beinbeuger sitzend", "beine", "kg"), ("Kabel-Kickback", "beine", "kg"),
    ("Wadenheben sitzend", "beine", "kg"), ("Pallof Press", "rumpf", "kg"),
    ("Cable Crunch", "rumpf", "kg"),
]


def _catalog_v2(c):
    """Neue Uebungen einmalig anlegen (spaeter geloeschte kommen nicht wieder) und
    Bewegungsmuster setzen, wo noch keines steht – eigene Angaben bleiben."""
    if not c.execute("SELECT 1 FROM settings WHERE key='catalog_v2'").fetchone():
        for name, group, unit in CATALOG_V2:
            c.execute("INSERT OR IGNORE INTO exercises (name, muscle_group, unit) VALUES (?,?,?)",
                      (name, group, unit))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('catalog_v2', ?)", (now(),))
    for name, pattern in STARTER_PATTERNS.items():
        kind = "compound" if pattern in COMPOUND_PATTERNS else "isolation"
        c.execute("UPDATE exercises SET pattern=?, kind=? WHERE name=? AND pattern IS NULL",
                  (pattern, kind, name))


def _seed(c):
    """Erster Start: Profil, Uebungs-Katalog und ein Beispiel-Split. Laeuft nur, solange
    noch nichts da ist – ein leer geraeumter Katalog wird nicht wieder aufgefuellt."""
    if c.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
        return
    c.execute("INSERT OR IGNORE INTO profiles (name, color, created_at) VALUES (?,?,?)",
              ("Luca", "#3a63d8", now()))
    for name, group, unit in STARTER_EXERCISES:
        c.execute("INSERT OR IGNORE INTO exercises (name, muscle_group, unit) VALUES (?,?,?)",
                  (name, group, unit))
    profile_id = c.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()[0]
    if not c.execute("SELECT 1 FROM splits WHERE profile_id=?", (profile_id,)).fetchone():
        cur = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                        (profile_id, "Push / Pull / Beine", now()))
        split_id = cur.lastrowid
        for pos, (label, weekday, items) in enumerate(STARTER_SPLIT):
            day = c.execute("INSERT INTO split_days (split_id, position, label, weekdays) VALUES (?,?,?,?)",
                            (split_id, pos, label, str(weekday))).lastrowid
            for ex_pos, (ex_name, sets, lo, hi) in enumerate(items):
                row = c.execute("SELECT id FROM exercises WHERE name=?", (ex_name,)).fetchone()
                if row:
                    c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position,"
                              " target_sets, target_reps_min, target_reps_max) VALUES (?,?,?,?,?,?)",
                              (day, row[0], ex_pos, sets, lo, hi))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('seeded', ?)", (now(),))
    log.info("Startdaten angelegt")


def init():
    with connect() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        _seed(c)
        _catalog_v2(c)
        from .exercise_info import apply as _exercise_info
        _exercise_info(c)
    with sqlite3.connect(DB_PATH, timeout=30) as c:
        # WAL: Lesen blockiert nicht, waehrend geschrieben wird – wichtig, wenn zwei
        # Leute gleichzeitig eine Einheit hochladen.
        c.execute("PRAGMA journal_mode=WAL")


def rows(sql, params=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql, params=()):
    r = rows(sql, params)
    return r[0] if r else None


def backup(tag=None):
    """Konsistente Kopie der DB (auch waehrend geschrieben wird). Taegliche Kopien werden
    nach BACKUP_KEEP_DAYS geloescht, getaggte nie."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    name = f"fitness-{stamp}-{tag}.db" if tag else f"fitness-{stamp}.db"
    target = BACKUP_DIR / name
    if target.exists():
        target.unlink()
    with sqlite3.connect(DB_PATH, timeout=30) as c:
        c.execute("VACUUM INTO ?", (str(target),))
    cutoff = datetime.now() - timedelta(days=BACKUP_KEEP_DAYS)
    for old in BACKUP_DIR.glob("fitness-????-??-??.db"):
        try:
            if datetime.strptime(old.stem[8:], "%Y-%m-%d") < cutoff:
                old.unlink()
        except ValueError:
            continue
    log.info("Backup geschrieben: %s", target)
    return target


# ---------------------------------------------------------------- CRUD
# Whitelist je Tabelle: nur diese Spalten duerfen ueber die API geschrieben werden,
# damit Spaltennamen nie ungeprueft aus dem Request in SQL landen.
WRITABLE = {
    "profiles": ("name", "color", "birth_year", "sex", "height_cm", "goal", "experience",
                 "days_per_week", "session_minutes", "limitations", "train_weekdays", "focus",
                 "activity_level", "kcal_goal", "protein_goal", "carbs_goal", "fat_goal",
                 "nutrition_auto", "water_goal_ml"),
    "exercises": ("name", "muscle_group", "unit", "archived", "pattern", "kind",
                  "instructions", "mistakes", "muscles_primary", "muscles_secondary",
                  "equipment", "video_query"),
    "checkins": ("profile_id", "date", "sleep", "stress", "energy", "soreness", "readiness",
                 "client_id"),
    "cardio": ("profile_id", "date", "activity", "minutes", "distance_km", "kcal", "avg_hr",
               "notes", "client_id"),
    "mobility_log": ("profile_id", "date", "routine", "client_id"),
    "foods": ("barcode", "name", "brand", "kcal", "protein", "carbs", "fat", "serving_g",
              "source", "created_at"),
    "food_log": ("profile_id", "date", "meal", "food_id", "name", "grams", "kcal", "protein",
                 "carbs", "fat", "client_id"),
    "water_log": ("profile_id", "date", "ml", "client_id"),
    "body_measurements": ("profile_id", "date", "source", "weight_kg", "body_fat_pct",
                          "body_fat_estimated", "muscle_mass_kg", "water_pct", "visceral_fat",
                          "bmr_kcal", "bio_age", "waist_cm", "hip_cm", "chest_cm", "arm_cm",
                          "thigh_cm", "neck_cm", "notes", "client_id"),
    "splits": ("profile_id", "name", "active"),
    "split_days": ("split_id", "position", "label", "weekdays"),
    "split_exercises": ("split_day_id", "exercise_id", "position", "target_sets",
                        "target_reps_min", "target_reps_max", "target_weight",
                        "rest_seconds", "notes"),
    "workouts": ("profile_id", "split_day_id", "title", "started_at", "finished_at", "notes"),
    "bodyweight": ("profile_id", "date", "kg"),
}


def list_rows(table, order="id"):
    return rows(f"SELECT * FROM {table} ORDER BY {order}")


def get_row(table, row_id):
    return one(f"SELECT * FROM {table} WHERE id=?", (row_id,))


def insert_row(table, data):
    data = {k: v for k, v in data.items() if k in WRITABLE[table]}
    with connect() as c:
        cur = c.execute(f"INSERT INTO {table} ({','.join(data)}) VALUES "
                        f"({','.join('?' * len(data))})", list(data.values()))
        row_id = cur.lastrowid
    return get_row(table, row_id)


def update_row(table, row_id, data):
    data = {k: v for k, v in data.items() if k in WRITABLE[table]}
    if data:
        with connect() as c:
            c.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in data)} WHERE id=?",
                      [*data.values(), row_id])
    return get_row(table, row_id)


def delete_row(table, row_id):
    """Loescht die Zeile samt allem, was ohne sie keinen Sinn mehr ergibt."""
    with connect() as c:
        if table == "splits":
            days = [r[0] for r in c.execute("SELECT id FROM split_days WHERE split_id=?", (row_id,))]
            for day in days:
                c.execute("DELETE FROM split_exercises WHERE split_day_id=?", (day,))
                # Absolvierte Einheiten bleiben erhalten, verlieren nur die Zuordnung
                c.execute("UPDATE workouts SET split_day_id=NULL WHERE split_day_id=?", (day,))
            c.execute("DELETE FROM split_days WHERE split_id=?", (row_id,))
        elif table == "split_days":
            c.execute("DELETE FROM split_exercises WHERE split_day_id=?", (row_id,))
            c.execute("UPDATE workouts SET split_day_id=NULL WHERE split_day_id=?", (row_id,))
        elif table == "workouts":
            c.execute("DELETE FROM sets WHERE workout_id=?", (row_id,))
        elif table == "profiles":
            raise ValueError("Profile werden nicht geloescht")
        c.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))


def exists(table, row_id):
    return bool(one(f"SELECT 1 AS x FROM {table} WHERE id=?", (row_id,)))


# ------------------------------------------------------------ Workouts
def save_workout(payload):
    """Komplette Einheit in einem Rutsch. Idempotent ueber client_id: kommt derselbe
    Entwurf aus dem Ausgang ein zweites Mal, wird der vorhandene Datensatz zurueckgegeben."""
    client_id = payload.get("client_id")
    with connect() as c:
        if client_id:
            have = c.execute("SELECT id FROM workouts WHERE client_id=?", (client_id,)).fetchone()
            if have:
                log.info("Einheit %s war schon da – nicht doppelt angelegt", client_id)
                return get_workout(have[0]), False
        cur = c.execute(
            "INSERT INTO workouts (profile_id, split_day_id, title, started_at, finished_at,"
            " notes, client_id) VALUES (?,?,?,?,?,?,?)",
            (payload["profile_id"], payload.get("split_day_id"), payload.get("title"),
             payload.get("started_at") or now(), payload.get("finished_at") or now(),
             payload.get("notes"), client_id))
        workout_id = cur.lastrowid
        for s in payload.get("sets", []):
            c.execute("INSERT INTO sets (workout_id, exercise_id, set_no, reps, weight, rpe, warmup)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (workout_id, s["exercise_id"], s.get("set_no") or 1,
                       s.get("reps"), s.get("weight"), s.get("rpe"), int(bool(s.get("warmup")))))
    return get_workout(workout_id), True


def get_workout(workout_id):
    w = get_row("workouts", workout_id)
    if not w:
        return None
    w["sets"] = rows("SELECT s.*, e.name AS exercise, e.muscle_group, e.unit FROM sets s"
                     " JOIN exercises e ON e.id = s.exercise_id"
                     " WHERE s.workout_id=? ORDER BY s.id", (workout_id,))
    if w.get("split_day_id"):
        day = get_row("split_days", w["split_day_id"])
        w["day_label"] = day["label"] if day else None
    else:
        w["day_label"] = w.get("title")
    return w


def list_workouts(profile_id, limit=30):
    out = rows("SELECT w.*, d.label AS day_label FROM workouts w"
               " LEFT JOIN split_days d ON d.id = w.split_day_id"
               " WHERE w.profile_id=?"
               " ORDER BY COALESCE(w.started_at, w.finished_at) DESC, w.id DESC"
               " LIMIT ?", (profile_id, limit))
    for w in out:
        w["sets"] = rows("SELECT s.*, e.name AS exercise, e.unit FROM sets s"
                         " JOIN exercises e ON e.id = s.exercise_id"
                         " WHERE s.workout_id=? ORDER BY s.id", (w["id"],))
        if not w["day_label"]:
            w["day_label"] = w.get("title")
    return out


def set_bodyweight(profile_id, date, kg):
    with connect() as c:
        c.execute("INSERT INTO bodyweight (profile_id, date, kg) VALUES (?,?,?)"
                  " ON CONFLICT(profile_id, date) DO UPDATE SET kg=excluded.kg",
                  (profile_id, date, kg))
    return one("SELECT * FROM bodyweight WHERE profile_id=? AND date=?", (profile_id, date))


# ------------------------------------------------------------- Splits
def split_detail(split_id):
    split = get_row("splits", split_id)
    if not split:
        return None
    split["days"] = [day_out(d) for d in list_rows_where("split_days", "split_id", split_id, "position, id")]
    for day in split["days"]:
        day["exercises"] = rows(
            "SELECT se.*, e.name, e.muscle_group, e.unit, e.kind, e.pattern FROM split_exercises se"
            " JOIN exercises e ON e.id = se.exercise_id"
            " WHERE se.split_day_id=? ORDER BY se.position, se.id", (day["id"],))
    return split


def list_rows_where(table, column, value, order="id"):
    return rows(f"SELECT * FROM {table} WHERE {column}=? ORDER BY {order}", (value,))


def active_split(profile_id):
    row = one("SELECT * FROM splits WHERE profile_id=? AND active=1 ORDER BY id LIMIT 1",
              (profile_id,))
    if not row:
        row = one("SELECT * FROM splits WHERE profile_id=? ORDER BY id LIMIT 1", (profile_id,))
    return split_detail(row["id"]) if row else None


def activate_split(split_id, profile_id):
    with connect() as c:
        c.execute("UPDATE splits SET active=0 WHERE profile_id=?", (profile_id,))
        c.execute("UPDATE splits SET active=1 WHERE id=? AND profile_id=?", (split_id, profile_id))
