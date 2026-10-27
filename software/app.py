import os
import sqlite3
import json
import math
import re
import secrets
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from getpass import getpass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
import requests
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask, render_template, request, abort, jsonify, session, redirect, url_for, flash, make_response
from dotenv import load_dotenv
from functools import wraps
from contextlib import contextmanager

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "users.db")
HEATER_COOLDOWN_SECONDS = 15
AUTOMATIC_CHECK_INTERVAL_SECONDS = 15
BOARDING_HOUSES = (
    "Calderfield",
    "Farthings",
    "St Geogres",
    "Poplars",
    "Victoria",
    "Limetrees",
)

@contextmanager
def database_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "username" not in session or not session["logged_in"]:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "username" not in session or not session.get("logged_in"):
            return redirect(url_for("login"))

        with database_connection() as conn:
            user = conn.execute(
                "SELECT is_admin FROM users WHERE username = ?",
                (session["username"],),
            ).fetchone()
        if not user or not user[0]:
            abort(403)
        return func(*args, **kwargs)
    return wrapper

def student_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "username" not in session or not session.get("logged_in"):
            return redirect(url_for("login"))

        with database_connection() as conn:
            user = conn.execute(
                "SELECT is_admin FROM users WHERE username = ?",
                (session["username"],),
            ).fetchone()
        if not user or user[0]:
            abort(403)
        return func(*args, **kwargs)
    return wrapper

def ensure_admin_request_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0
        )
    """)
    user_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)")
    }
    if "full_name" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
    if "email" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    student_columns = {
        "year_group": "TEXT",
        "date_of_birth": "TEXT",
        "boarding_house": "TEXT",
        "has_roommate": "INTEGER",
        "roommate_name": "TEXT",
        "room": "TEXT",
        "nationality": "TEXT",
    }
    for column, column_type in student_columns.items():
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {column_type}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            reviewed_by TEXT
        )
    """)

def ensure_heater_request_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heater_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
            admin_comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            reviewed_by TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_heater_request_per_student
        ON heater_requests (username)
        WHERE status = 'pending'
    """)

def ensure_heater_settings_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heater_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mode TEXT NOT NULL DEFAULT 'manual'
                CHECK (mode IN ('manual', 'automatic')),
            turn_on_below REAL NOT NULL DEFAULT 18,
            turn_off_above REAL NOT NULL DEFAULT 20
        )
    """)
    conn.execute(
        """INSERT OR IGNORE INTO heater_settings
           (id, mode, turn_on_below, turn_off_above)
           VALUES (1, 'manual', 18, 20)"""
    )


def ensure_heater_setting_change_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heater_setting_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            old_mode TEXT,
            new_mode TEXT,
            old_low REAL,
            new_low REAL,
            old_high REAL,
            new_high REAL
        )
    """)


def age_in_years_and_months(date_of_birth, today=None):
    birth_date = date.fromisoformat(date_of_birth)
    current_date = today or date.today()
    months = (current_date.year - birth_date.year) * 12
    months += current_date.month - birth_date.month
    if current_date.day < birth_date.day:
        months -= 1
    return divmod(months, 12)

def student_username_base(full_name):
    normalized_name = unicodedata.normalize("NFKD", full_name)
    ascii_name = normalized_name.encode("ascii", "ignore").decode("ascii")
    username_base = re.sub(r"[^a-z0-9]+", ".", ascii_name.lower()).strip(".")
    return (username_base or "student")[:70].rstrip(".")

def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

def valid_csrf_token():
    submitted_token = request.form.get("csrf_token", "")
    stored_token = session.get("csrf_token", "")
    return bool(stored_token) and secrets.compare_digest(
        submitted_token, stored_token
    )

def ensure_heater_command_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heater_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('on', 'off')),
            status TEXT NOT NULL CHECK (status IN ('sent', 'refused', 'error')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

def heater_cooldown_remaining(conn):
    row = conn.execute(
        """SELECT created_at FROM heater_commands
           WHERE status = 'sent'
           ORDER BY id DESC
           LIMIT 1"""
    ).fetchone()
    if row is None:
        return 0

    try:
        last_command_at = datetime.strptime(
            row[0], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        app.logger.exception("Invalid timestamp in heater command history.")
        return HEATER_COOLDOWN_SECONDS

    elapsed = (datetime.now(timezone.utc) - last_command_at).total_seconds()
    return max(0, int(HEATER_COOLDOWN_SECONDS - elapsed + 0.999))

def validate_heater_settings(mode, turn_on_below, turn_off_above):
    if mode not in ("manual", "automatic"):
        return False, "Choose manual or automatic mode."
    try:
        low = float(turn_on_below)
        high = float(turn_off_above)
    except (TypeError, ValueError):
        return False, "Enter valid numeric temperature thresholds."
    if not math.isfinite(low) or not math.isfinite(high):
        return False, "Temperature thresholds must be finite numbers."
    if not 5 <= low <= 40 or not 5 <= high <= 40:
        return False, "Temperature thresholds must each be between 5 °C and 40 °C."
    if high < low + 2:
        return False, "The high limit must be at least 2 °C above the low limit."
    return True, None


def log_heater_setting_change(conn, username, previous, current):
    ensure_heater_setting_change_schema(conn)
    conn.execute(
        """
        INSERT INTO heater_setting_changes (
            username, old_mode, new_mode, old_low, new_low, old_high, new_high
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            previous["mode"],
            current["mode"],
            previous["turn_on_below"],
            current["turn_on_below"],
            previous["turn_off_above"],
            current["turn_off_above"],
        ),
    )


def send_heater_control_update(conn, manual_state, mode, turn_on_below, turn_off_above, username):
    ensure_heater_command_schema(conn)
    cooldown_seconds = heater_cooldown_remaining(conn)
    if cooldown_seconds:
        conn.execute(
            """INSERT INTO heater_commands (username, state, status)
               VALUES (?, ?, 'refused')""",
            (username, manual_state),
        )
        return False, f"Wait {cooldown_seconds} seconds before sending another command."

    if mode not in ("manual", "automatic"):
        mode = "manual"
    manual_value = 1 if str(manual_state).lower() == "on" else 0
    payload = {
        "api_key": os.environ.get("THINGSPEAK_WRITE_API_KEY"),
        "field1": manual_value,
        "field2": 1 if mode == "automatic" else 0,
        "field3": float(turn_on_below),
        "field4": float(turn_off_above),
    }

    command_status = "error"
    if not payload["api_key"]:
        app.logger.error("ThingSpeak heater control Write key is missing.")
        message = "Heater control is not configured."
    else:
        try:
            response = requests.post(
                "https://api.thingspeak.com/update.json",
                data=payload,
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException:
            app.logger.exception("Could not send heater settings to ThingSpeak.")
            message = "Unable to contact the heater control channel."
        else:
            response_body = response.text.strip()
            if response_body == "0":
                command_status = "refused"
                message = "The heater control channel refused the update. Please try again."
            else:
                payload_json = None
                try:
                    payload_json = response.json()
                except ValueError:
                    pass

                entry_id = payload_json.get("entry_id") if isinstance(payload_json, dict) else None
                if entry_id is None:
                    try:
                        entry_id = int(response_body)
                    except ValueError:
                        entry_id = None

                try:
                    entry_id = int(entry_id)
                except (TypeError, ValueError):
                    entry_id = 0

                if entry_id > 0:
                    command_status = "sent"
                    message = "Settings sent to the heater controller."
                else:
                    app.logger.error(
                        "ThingSpeak returned an unexpected control-channel response: %s",
                        payload_json if payload_json is not None else response_body,
                    )
                    message = "The heater control channel returned an unexpected response."

    conn.execute(
        """INSERT INTO heater_commands (username, state, status)
           VALUES (?, ?, ?)""",
        (username, manual_state, command_status),
    )
    return command_status == "sent", message


def send_heater_command(conn, state, username):
    ensure_heater_settings_schema(conn)
    settings = conn.execute(
        "SELECT mode, turn_on_below, turn_off_above FROM heater_settings WHERE id = 1"
    ).fetchone()
    current_mode = settings[0] if settings else "manual"
    low_limit = settings[1] if settings else 18
    high_limit = settings[2] if settings else 20

    if current_mode == "manual":
        manual_state = state
    else:
        manual_state = "off"
    return send_heater_control_update(
        conn,
        manual_state,
        current_mode,
        low_limit,
        high_limit,
        username,
    )

def get_led_state():
    channel_id = os.environ.get("THINGSPEAK_CHANNEL_ID")
    read_api_key = os.environ.get("THINGSPEAK_READ_API_KEY")
    if not channel_id or not read_api_key:
        app.logger.error("ThingSpeak sensor channel configuration is missing.")
        return None

    feed_url = (
        f"https://api.thingspeak.com/channels/{channel_id}/feeds/last.json"
    )
    try:
        response = requests.get(
            feed_url, params={"api_key": read_api_key}, timeout=10
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        app.logger.exception("Could not load the latest ThingSpeak sensor feed.")
        return None

    if not isinstance(payload, dict):
        app.logger.error("ThingSpeak returned an unexpected sensor feed.")
        return None

    field3 = str(payload.get("field3", "")).strip()
    if field3 == "1":
        return "on"
    if field3 == "0":
        return "off"

    app.logger.error("ThingSpeak sensor feed did not contain a valid field3 state.")
    return None

def get_latest_sensor_reading():
    channel_id = os.environ.get("THINGSPEAK_CHANNEL_ID")
    read_api_key = os.environ.get("THINGSPEAK_READ_API_KEY")
    if not channel_id or not read_api_key:
        app.logger.error("ThingSpeak sensor channel configuration is missing.")
        return None

    feed_url = f"https://api.thingspeak.com/channels/{channel_id}/feeds/last.json"
    try:
        response = requests.get(
            feed_url, params={"api_key": read_api_key}, timeout=10
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        app.logger.exception("Could not load the latest ThingSpeak sensor reading.")
        return None
    except ValueError:
        app.logger.exception("ThingSpeak returned invalid sensor JSON.")
        return None

    if not isinstance(payload, dict):
        app.logger.error("ThingSpeak returned an unexpected sensor reading.")
        return None

    try:
        temperature = float(payload["field1"])
    except (KeyError, TypeError, ValueError):
        app.logger.error("ThingSpeak sensor reading did not contain a valid temperature.")
        return None
    if not math.isfinite(temperature):
        app.logger.error("ThingSpeak sensor reading returned a non-finite temperature.")
        return None

    heater_field = str(payload.get("field3", "")).strip()
    if heater_field not in ("0", "1"):
        app.logger.error("ThingSpeak sensor reading did not contain a valid heater state.")
        return None

    return {
        "temperature": temperature,
        "heater_state": "on" if heater_field == "1" else "off",
        "created_at": payload.get("created_at"),
    }

def run_automatic_control_cycle():
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        ensure_heater_settings_schema(conn)
        settings = conn.execute(
            """SELECT mode, turn_on_below, turn_off_above
               FROM heater_settings WHERE id = 1"""
        ).fetchone()
        if settings["mode"] != "automatic":
            return

        succeeded, message = send_heater_control_update(
            conn,
            "off",
            "automatic",
            settings["turn_on_below"],
            settings["turn_off_above"],
            "automatic",
        )
        if succeeded:
            app.logger.info(
                "Automatic mode refreshed the heater controller at %.1fC to %.1fC.",
                settings["turn_on_below"],
                settings["turn_off_above"],
            )
        else:
            app.logger.warning(
                "Automatic heater control could not refresh settings: %s",
                message,
            )

def automatic_controller_loop():
    while True:
        try:
            run_automatic_control_cycle()
        except sqlite3.Error:
            app.logger.exception("Automatic heater controller database error.")
        time.sleep(AUTOMATIC_CHECK_INTERVAL_SECONDS)

automatic_controller_lock = threading.Lock()
automatic_controller_thread = None

def ensure_automatic_controller_started():
    global automatic_controller_thread
    with automatic_controller_lock:
        if automatic_controller_thread and automatic_controller_thread.is_alive():
            return
        automatic_controller_thread = threading.Thread(
            target=automatic_controller_loop,
            name="heater-automatic-controller",
            daemon=True,
        )
        automatic_controller_thread.start()

@app.before_request
def resume_automatic_controller_if_enabled():
    with database_connection() as conn:
        ensure_heater_settings_schema(conn)
        mode = conn.execute(
            "SELECT mode FROM heater_settings WHERE id = 1"
        ).fetchone()[0]
    if mode == "automatic":
        ensure_automatic_controller_started()

@app.route("/", strict_slashes=False)
@login_required
def dashboard():
    return render_template("index.html", author='Quang Anh')

@app.route("/graphs", strict_slashes=False)
@login_required
def graphs():
    return render_template("graphs.html")

@app.route("/api/feeds", strict_slashes=False)
@login_required
def channel_feeds():
    channel_id = os.environ.get("THINGSPEAK_CHANNEL_ID")
    read_api_key = os.environ.get("THINGSPEAK_READ_API_KEY")
    if not channel_id or not read_api_key:
        app.logger.error("ThingSpeak channel configuration is missing.")
        return jsonify(error="Live sensor data is not configured."), 503

    query = urlencode({"api_key": read_api_key, "results": 60})
    feed_url = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json?{query}"
    try:
        with urlopen(feed_url, timeout=10) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        app.logger.error("ThingSpeak returned HTTP %s.", error.code)
        return jsonify(error="Unable to load live sensor data."), 502
    except URLError:
        app.logger.exception("Could not connect to ThingSpeak.")
        return jsonify(error="Unable to connect to the live sensor feed."), 502
    except json.JSONDecodeError:
        app.logger.exception("ThingSpeak returned invalid JSON.")
        return jsonify(error="The live sensor feed returned invalid data."), 502

    feeds = payload.get("feeds") if isinstance(payload, dict) else None
    if not isinstance(feeds, list):
        app.logger.error("ThingSpeak response did not contain a feeds list.")
        return jsonify(error="The live sensor feed returned an unexpected response."), 502

    readings = []
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        try:
            temperature = (
                float(feed["field1"])
                if feed.get("field1") not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            temperature = None
        try:
            humidity = (
                float(feed["field2"])
                if feed.get("field2") not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            humidity = None
        readings.append({
            "created_at": feed.get("created_at"),
            "temperature": temperature,
            "humidity": humidity,
        })

    return jsonify(feeds=readings)

@app.route("/login", strict_slashes=False, methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if row and check_password_hash(row["password_hash"], password):
            session["username"] = row["username"]
            session["logged_in"] = True
            session["is_admin"] = bool(row["is_admin"])
            return redirect(url_for("admin" if row["is_admin"] else "dashboard"))
        else:
            return render_template("login.html", error="Invalid username or password.")
    return render_template("login.html")

@app.route("/admin", strict_slashes=False)
@admin_required
def admin():
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        ensure_admin_request_schema(conn)
        ensure_heater_command_schema(conn)
        ensure_heater_request_schema(conn)
        ensure_heater_settings_schema(conn)
        ensure_heater_setting_change_schema(conn)
        pending_requests = conn.execute(
            """SELECT id, full_name, email, username, reason, created_at
               FROM admin_access_requests
               WHERE status = 'pending'
               ORDER BY created_at"""
        ).fetchall()
        pending_heater_requests = conn.execute(
            """SELECT id, username, reason, created_at
               FROM heater_requests
               WHERE status = 'pending'
               ORDER BY created_at, id"""
        ).fetchall()
        command_history = conn.execute(
            """SELECT username, state, status, created_at
               FROM heater_commands
               ORDER BY id DESC
               LIMIT 10"""
        ).fetchall()
        last_command = command_history[0] if command_history else None
        cooldown_seconds = heater_cooldown_remaining(conn)
        heater_settings = conn.execute(
            """SELECT mode, turn_on_below, turn_off_above
               FROM heater_settings WHERE id = 1"""
        ).fetchone()
        settings_log = conn.execute(
            """SELECT username, changed_at, old_mode, new_mode,
                      old_low, new_low, old_high, new_high
               FROM heater_setting_changes
               ORDER BY id DESC
               LIMIT 10"""
        ).fetchall()

    led_state = get_led_state()
    return render_template(
        "admin.html",
        pending_requests=pending_requests,
        pending_heater_requests=pending_heater_requests,
        command_history=command_history,
        last_command=last_command,
        led_state=led_state,
        cooldown_seconds=cooldown_seconds,
        heater_settings=heater_settings,
        settings_log=settings_log,
        waiting_for_device=(
            last_command is not None
            and last_command["status"] == "sent"
            and led_state != last_command["state"]
        ),
        csrf_token=csrf_token(),
    )

@app.route("/mode", methods=["GET", "POST"])
@admin_required
def heater_mode():
    if request.method == "POST":
        if not valid_csrf_token():
            abort(400, description="The form expired. Please reload it and try again.")

        mode = request.form.get("mode", "")
        if mode not in ("manual", "automatic"):
            abort(400, description="Choose manual or automatic mode.")

        try:
            turn_on_below = float(request.form.get("turn_on_below", ""))
            turn_off_above = float(request.form.get("turn_off_above", ""))
        except ValueError:
            flash("Enter valid numeric temperature thresholds.", "error")
            return redirect(url_for("heater_mode"))

        valid, error = validate_heater_settings(mode, turn_on_below, turn_off_above)
        if not valid:
            flash(error, "error")
            return redirect(url_for("heater_mode"))

        with database_connection() as conn:
            ensure_heater_settings_schema(conn)
            previous = conn.execute(
                "SELECT mode, turn_on_below, turn_off_above FROM heater_settings WHERE id = 1"
            ).fetchone()
            previous_settings = {
                "mode": previous[0] if previous else "manual",
                "turn_on_below": previous[1] if previous else 18,
                "turn_off_above": previous[2] if previous else 20,
            }
            conn.execute(
                """UPDATE heater_settings
                   SET mode = ?, turn_on_below = ?, turn_off_above = ?
                   WHERE id = 1""",
                (mode, turn_on_below, turn_off_above),
            )
            log_heater_setting_change(
                conn,
                session["username"],
                previous_settings,
                {
                    "mode": mode,
                    "turn_on_below": turn_on_below,
                    "turn_off_above": turn_off_above,
                },
            )

            manual_state = "off" if mode == "automatic" else "off"
            succeeded, message = send_heater_control_update(
                conn,
                manual_state,
                mode,
                turn_on_below,
                turn_off_above,
                session["username"],
            )

        if mode == "automatic":
            ensure_automatic_controller_started()
            if succeeded:
                flash("Automatic mode is active and the heater controller has been updated.", "success")
            else:
                flash(f"Automatic mode saved, but {message}", "error")
        else:
            if succeeded:
                flash("Manual mode is active and the heater controller has been updated.", "success")
            else:
                flash(f"Manual mode saved, but {message}", "error")
        return redirect(url_for("heater_mode"))

    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        ensure_heater_settings_schema(conn)
        settings = conn.execute(
            """SELECT mode, turn_on_below, turn_off_above
               FROM heater_settings WHERE id = 1"""
        ).fetchone()
    if settings["mode"] == "automatic":
        ensure_automatic_controller_started()
    return render_template(
        "mode.html",
        settings=settings,
        automatic_interval=AUTOMATIC_CHECK_INTERVAL_SECONDS,
        csrf_token=csrf_token(),
    )

@app.route("/mode/automatic", strict_slashes=False)
@admin_required
def automatic_mode():
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        ensure_heater_settings_schema(conn)
        ensure_heater_command_schema(conn)
        settings = conn.execute(
            """SELECT mode, turn_on_below, turn_off_above
               FROM heater_settings WHERE id = 1"""
        ).fetchone()
        history = conn.execute(
            """SELECT username, state, created_at
               FROM heater_commands
               WHERE status = 'sent'
               ORDER BY id DESC
               LIMIT 100"""
        ).fetchall()
    if settings["mode"] != "automatic":
        flash("Switch to Automatic mode to view its dashboard.", "error")
        return redirect(url_for("heater_mode"))
    ensure_automatic_controller_started()
    return render_template(
        "automatic.html",
        settings=settings,
        history=history,
    )

@app.route("/api/heater-history", strict_slashes=False)
@admin_required
def heater_history_data():
    with database_connection() as conn:
        ensure_heater_command_schema(conn)
        history = conn.execute(
            """SELECT username, state, created_at
               FROM heater_commands
               WHERE status = 'sent'
               ORDER BY id DESC
               LIMIT 100"""
        ).fetchall()
    return jsonify(commands=[
        {"username": row[0], "state": row[1], "created_at": row[2]}
        for row in reversed(history)
    ])

@app.route("/students", strict_slashes=False)
@admin_required
def students():
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        ensure_admin_request_schema(conn)
        student_rows = conn.execute(
            """SELECT username, full_name, year_group, date_of_birth, boarding_house,
                      has_roommate, room, nationality
               FROM users
               WHERE date_of_birth IS NOT NULL
               ORDER BY username"""
        ).fetchall()

    students_by_house = {house: [] for house in BOARDING_HOUSES}
    for student_row in student_rows:
        student = dict(student_row)
        years, months = age_in_years_and_months(student["date_of_birth"])
        student["age"] = f"{years} years, {months} months"
        house = next(
            (
                known_house for known_house in BOARDING_HOUSES
                if known_house.casefold() == student["boarding_house"].casefold()
            ),
            None,
        )
        if house is None:
            students_by_house.setdefault(student["boarding_house"], []).append(student)
        else:
            students_by_house[house].append(student)

    return render_template(
        "students.html",
        boarding_houses=BOARDING_HOUSES,
        students_by_house=students_by_house,
        csrf_token=csrf_token(),
    )

@app.route("/admin/students", methods=["POST"])
@admin_required
def create_student():
    if not valid_csrf_token():
        abort(400, description="The form expired. Please reload it and try again.")

    student = {
        "full_name": request.form.get("full_name", "").strip(),
        "year_group": request.form.get("year_group", "").strip(),
        "date_of_birth": request.form.get("date_of_birth", "").strip(),
        "boarding_house": request.form.get("boarding_house", "").strip(),
        "room": request.form.get("room", "").strip(),
        "nationality": request.form.get("nationality", "").strip(),
    }
    has_roommate = request.form.get("has_roommate", "")

    required_details = (
        student["full_name"],
        student["year_group"],
        student["date_of_birth"],
        student["boarding_house"],
        student["room"],
        student["nationality"],
    )
    if not all(required_details):
        error = "Please complete every student field."
    elif len(student["full_name"]) > 120:
        error = "Student name must be 120 characters or fewer."
    elif student["boarding_house"] not in BOARDING_HOUSES:
        error = "Choose a valid boarding house."
    elif any(len(student[field]) > limit for field, limit in (
        ("year_group", 30),
        ("boarding_house", 100),
        ("room", 30),
        ("nationality", 100),
    )):
        error = "One or more student details exceed the allowed length."
    elif has_roommate not in ("yes", "no"):
        error = "Choose whether the student has a roommate."
    else:
        try:
            birth_date = date.fromisoformat(student["date_of_birth"])
        except ValueError:
            error = "Enter a valid date of birth."
        else:
            if birth_date > date.today():
                error = "Date of birth cannot be in the future."
            else:
                error = None

    if error:
        flash(error, "error")
        return redirect(url_for("students"))

    username_base = student_username_base(student["full_name"])
    username = username_base
    password = secrets.token_urlsafe(16)
    try:
        with database_connection() as conn:
            ensure_admin_request_schema(conn)
            suffix = 1
            while conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone():
                suffix += 1
                username = f"{username_base}.{suffix}"
            conn.execute(
                """INSERT INTO users
                   (username, password_hash, is_admin, full_name, year_group, date_of_birth,
                    boarding_house, has_roommate, roommate_name, room, nationality)
                   VALUES (?, ?, 0, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    username,
                    generate_password_hash(password),
                    student["full_name"],
                    student["year_group"],
                    student["date_of_birth"],
                    student["boarding_house"],
                    1 if has_roommate == "yes" else 0,
                    student["room"],
                    student["nationality"],
                ),
            )
    except sqlite3.IntegrityError:
        app.logger.exception("Could not create a generated student account.")
        abort(409, description="The student account could not be created. Please try again.")

    response = make_response(render_template(
        "student_created.html",
        full_name=student["full_name"],
        username=username,
        password=password,
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response

@app.route("/heater", methods=["POST"])
@admin_required
def control_heater():
    if not valid_csrf_token():
        abort(400, description="The form expired. Please reload it and try again.")

    state = request.form.get("state", "")
    if state not in ("on", "off"):
        abort(400, description="State must be 'on' or 'off'.")

    with database_connection() as conn:
        ensure_heater_settings_schema(conn)
        previous_settings = conn.execute(
            "SELECT mode, turn_on_below, turn_off_above FROM heater_settings WHERE id = 1"
        ).fetchone()
        if previous_settings is None:
            previous_settings = ("manual", 18, 20)

        if previous_settings[0] != "manual":
            conn.execute(
                "UPDATE heater_settings SET mode = 'manual' WHERE id = 1"
            )
            log_heater_setting_change(
                conn,
                session["username"],
                {
                    "mode": previous_settings[0],
                    "turn_on_below": previous_settings[1],
                    "turn_off_above": previous_settings[2],
                },
                {
                    "mode": "manual",
                    "turn_on_below": previous_settings[1],
                    "turn_off_above": previous_settings[2],
                },
            )

        succeeded, message = send_heater_control_update(
            conn,
            state,
            "manual",
            previous_settings[1],
            previous_settings[2],
            session["username"],
        )
    flash(message, "success" if succeeded else "error")
    return redirect(url_for("admin"))

@app.route("/heater-requests", methods=["GET", "POST"])
@student_required
def student_heater_requests():
    if request.method == "POST":
        if not valid_csrf_token():
            abort(400, description="The form expired. Please reload it and try again.")
        reason = request.form.get("reason", "").strip()
        if not reason:
            flash("Please explain why you are requesting the heater.", "error")
            return redirect(url_for("student_heater_requests"))
        if len(reason) > 500:
            flash("Please keep your request to 500 characters or fewer.", "error")
            return redirect(url_for("student_heater_requests"))

        with database_connection() as conn:
            ensure_heater_request_schema(conn)
            try:
                conn.execute(
                    """INSERT INTO heater_requests (username, reason)
                       VALUES (?, ?)""",
                    (session["username"], reason),
                )
            except sqlite3.IntegrityError:
                pending_request = conn.execute(
                    """SELECT 1 FROM heater_requests
                       WHERE username = ? AND status = 'pending'""",
                    (session["username"],),
                ).fetchone()
                if pending_request is None:
                    raise
                flash("You already have a heater request waiting for review.", "error")
                return redirect(url_for("student_heater_requests"))
        flash("Your heater request was sent to the administrators.", "success")
        return redirect(url_for("student_heater_requests"))

    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        ensure_heater_request_schema(conn)
        requests_for_student = conn.execute(
            """SELECT reason, status, admin_comment, created_at, reviewed_at
               FROM heater_requests
               WHERE username = ?
               ORDER BY id DESC""",
            (session["username"],),
        ).fetchall()
        has_pending_request = any(
            heater_request["status"] == "pending"
            for heater_request in requests_for_student
        )

    return render_template(
        "heater_requests.html",
        heater_requests=requests_for_student,
        has_pending_request=has_pending_request,
        csrf_token=csrf_token(),
    )

@app.route("/admin/heater-requests/<int:request_id>", methods=["POST"])
@admin_required
def review_heater_request(request_id):
    action = request.form.get("action", "")
    if action not in ("approve", "reject"):
        abort(404)
    if not valid_csrf_token():
        abort(400, description="The form expired. Please reload it and try again.")

    comment = request.form.get("comment", "").strip()
    if not comment:
        flash("Add a comment for the student before reviewing the request.", "error")
        return redirect(url_for("admin"))
    if len(comment) > 1000:
        flash("Please keep the comment to 1000 characters or fewer.", "error")
        return redirect(url_for("admin"))

    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        ensure_heater_request_schema(conn)
        heater_request = conn.execute(
            """SELECT username, status FROM heater_requests WHERE id = ?""",
            (request_id,),
        ).fetchone()
        if heater_request is None or heater_request["status"] != "pending":
            flash("That heater request is no longer pending.", "error")
            return redirect(url_for("admin"))

        if action == "reject":
            conn.execute(
                """UPDATE heater_requests
                   SET status = 'rejected', admin_comment = ?,
                       reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?
                   WHERE id = ? AND status = 'pending'""",
                (comment, session["username"], request_id),
            )
            message = "Heater request rejected and feedback sent to the student."
            message_category = "success"
        else:
            succeeded, command_message = send_heater_command(
                conn, "on", session["username"]
            )
            if not succeeded:
                flash(command_message, "error")
                return redirect(url_for("admin"))
            conn.execute(
                """UPDATE heater_requests
                   SET status = 'approved', admin_comment = ?,
                       reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?
                   WHERE id = ? AND status = 'pending'""",
                (comment, session["username"], request_id),
            )
            message = "Heater turned on; approval and feedback sent to the student."
            message_category = "success"

    flash(message, message_category)
    return redirect(url_for("admin"))

@app.route("/request-admin-access", strict_slashes=False, methods=["GET", "POST"])
def request_admin_access():
    form_data = {}
    error = None
    if request.method == "POST":
        if not valid_csrf_token():
            abort(400, description="The form expired. Please reload it and try again.")

        form_data = {
            "full_name": request.form.get("full_name", "").strip(),
            "email": request.form.get("email", "").strip(),
            "username": request.form.get("username", "").strip(),
            "reason": request.form.get("reason", "").strip(),
        }
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all(form_data.values()):
            error = "Please complete every field."
        elif len(form_data["full_name"]) > 120:
            error = "Full name must be 120 characters or fewer."
        elif len(form_data["email"]) > 254 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", form_data["email"]
        ):
            error = "Enter a valid email address."
        elif len(form_data["username"]) > 80:
            error = "Username must be 80 characters or fewer."
        elif len(form_data["reason"]) > 1000:
            error = "Please keep your reason to 1000 characters or fewer."
        elif len(password) < 8:
            error = "Password must be at least 8 characters long."
        elif len(password) > 256:
            error = "Password must be 256 characters or fewer."
        elif password != confirm_password:
            error = "Passwords do not match."
        else:
            with database_connection() as conn:
                ensure_admin_request_schema(conn)
                existing_user = conn.execute(
                    "SELECT 1 FROM users WHERE username = ?",
                    (form_data["username"],),
                ).fetchone()
                existing_request = conn.execute(
                    """SELECT 1 FROM admin_access_requests
                       WHERE username = ? AND status = 'pending'""",
                    (form_data["username"],),
                ).fetchone()
                if existing_user:
                    error = "That username is already in use."
                elif existing_request:
                    error = "There is already a pending request for that username."
                else:
                    conn.execute(
                        """INSERT INTO admin_access_requests
                           (full_name, email, username, password_hash, reason)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            form_data["full_name"],
                            form_data["email"],
                            form_data["username"],
                            generate_password_hash(password),
                            form_data["reason"],
                        ),
                    )
                    flash(
                        "Your admin access request was submitted for review.",
                        "success",
                    )
                    return redirect(url_for("request_admin_access"))

    return render_template(
        "admin_request.html",
        error=error,
        form_data=form_data,
        csrf_token=csrf_token(),
    ), 400 if error else 200

@app.route("/admin/requests/<int:request_id>/<action>", methods=["POST"])
@admin_required
def review_admin_request(request_id, action):
    if action not in ("approve", "reject"):
        abort(404)
    if not valid_csrf_token():
        abort(400, description="The form expired. Please reload it and try again.")

    with database_connection() as conn:
        ensure_admin_request_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        pending = conn.execute(
            """SELECT full_name, email, username, password_hash
               FROM admin_access_requests
               WHERE id = ? AND status = 'pending'""",
            (request_id,),
        ).fetchone()
        if pending is None:
            flash("That request is no longer pending.", "error")
            return redirect(url_for("admin"))

        full_name, email, username, password_hash = pending
        if action == "approve":
            existing_user = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing_user:
                flash(
                    f"Could not approve {username}: that username is already in use.",
                    "error",
                )
                return redirect(url_for("admin"))
            conn.execute(
                """INSERT INTO users
                   (username, password_hash, is_admin, full_name, email)
                   VALUES (?, ?, 1, ?, ?)""",
                (username, password_hash, full_name, email),
            )
            status = "approved"
            message = f"Admin access granted to {username}."
        else:
            status = "rejected"
            message = f"Request from {username} rejected."

        conn.execute(
            """UPDATE admin_access_requests
               SET status = ?, password_hash = '', reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
               WHERE id = ?""",
            (status, session["username"], request_id),
        )
    flash(message, "success")
    return redirect(url_for("admin"))

@app.route("/logout", strict_slashes=False)
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)