"""
database.py - SQLite data layer and ALL business rules of the booking system.

The Streamlit front end (app.py) never writes SQL; it only calls the functions below.

* connect()          opens the database (foreign keys ON)
* init_db()          creates tables from db/schema.sql and loads demo data on first run
* create_bookings()  validate -> conflict check -> insert, inside ONE transaction (all or nothing)
* other functions    cancel, approve/reject, add/edit rooms, login/register, settings, e-mail log, CSV

Only the Python standard library is used (sqlite3, hashlib, secrets, csv, zoneinfo).
"""
import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import tempfile
from datetime import date, datetime, timedelta

try:                                               # time zone support (needs the tzdata package on some systems)
    from zoneinfo import ZoneInfo
except ImportError:                                # pragma: no cover
    ZoneInfo = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_FILE = os.path.join(BASE_DIR, 'db', 'schema.sql')

ROOM_TYPES = ('Classroom', 'Computer Lab', 'Science Lab', 'Auditorium')
STATUSES = ('Pending Approval', 'Approved', 'Rejected', 'Cancelled')
INACTIVE = ('Rejected', 'Cancelled')               # these statuses do NOT block a room

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
TIME_RE = re.compile(r'^\d{2}:\d{2}$')
ROOM_ID_RE = re.compile(r'^[A-Za-z0-9-]{2,20}$')
USERNAME_RE = re.compile(r'^[a-z0-9_.-]{3,30}$')

# Rules / configuration that the administrator can change in the UI (stored in the `settings` table).
DEFAULT_SETTINGS = {
    'timezone': 'Asia/Karachi',                    # used for "today" and "now" (cloud servers run in UTC)
    'public_url': 'http://localhost:8501',         # base URL encoded in the QR codes
    'open_time': '07:00',                          # rooms cannot be booked before this time ...
    'close_time': '22:00',                         # ... or after this time
    'max_duration_hours': '6',                     # longest single booking
    'max_weeks': '12',                             # longest recurring series
}

CONFLICT_SQL = """
    SELECT b.*, u.full_name FROM bookings b JOIN users u ON u.user_id = b.user_id
    WHERE b.room_id = ? AND b.book_date = ?
      AND b.status NOT IN ('Rejected','Cancelled')
      AND ? < b.end_time AND b.start_time < ?
"""


class AppError(Exception):
    """A user-facing error (bad input, conflict, no permission). `status` mimics an HTTP code."""
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# ---------------------------------------------------------------- connection / setup
def default_db_path():
    """campus_booking.db next to the code (or $EF101_DB); falls back to the temp folder if read-only."""
    env = os.environ.get('EF101_DB')
    if env:
        return env
    path = os.path.join(BASE_DIR, 'campus_booking.db')
    if os.access(BASE_DIR, os.W_OK) or os.path.exists(path):
        return path
    return os.path.join(tempfile.gettempdir(), 'campus_booking.db')        # pragma: no cover


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path(), isolation_level=None, timeout=10)  # we control transactions
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def hash_password(password, salt_hex):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), 100_000).hex()


def _insert_user(conn, user_id, full_name, role, password):
    salt = secrets.token_hex(16)
    conn.execute('INSERT INTO users (user_id, full_name, role, password_salt, password_hash) VALUES (?,?,?,?,?)',
                 (user_id, full_name, role, salt, hash_password(password, salt)))


DEMO_USERS = [
    ('student', 'Alex Student', 'student', 'password123'),
    ('admin', 'The Admin', 'admin', 'admin123'),
]

DEMO_ROOMS = [                                      # >= 10 rooms (mandatory requirement)
    ('CR-101',  'Main Academic Building, Fl 1', 45,  'Classroom',    'Projector, Smart Board'),
    ('CR-102',  'Main Academic Building, Fl 1', 30,  'Classroom',    'Projector'),
    ('CR-201',  'Main Academic Building, Fl 2', 60,  'Classroom',    'Projector, Audio System'),
    ('LAB-101', 'Engineering Block, Fl 1',      25,  'Computer Lab', 'High-End PCs, Projector'),
    ('LAB-102', 'Engineering Block, Fl 1',      30,  'Computer Lab', 'High-End PCs, VR Headsets'),
    ('LAB-201', 'Science Wing, Fl 2',           20,  'Science Lab',  'Fume Hood, Microscopes'),
    ('LAB-202', 'Science Wing, Fl 2',           24,  'Science Lab',  'Fume Hood, Chemical Storage'),
    ('AUD-01',  'Auditorium Complex, Ground',   150, 'Auditorium',   'Stage Lighting, Audio System, Projector'),
    ('CR-301',  'Business School Tower, Fl 3',  35,  'Classroom',    'Smart Board'),
    ('LAB-301', 'Innovation Center, Fl 3',      28,  'Computer Lab', 'VR Headsets, High-End PCs'),
]


def demo_bookings(today):
    d = lambda n: (today + timedelta(days=n)).isoformat()
    return [  # id, room, user, date, start, end, purpose, status, equipment
        ('BKG-9001', 'CR-101',  'student', d(0),  '09:00', '11:00', 'Physics Seminar',          'Approved',         'Projector'),
        ('BKG-9002', 'LAB-101', 'student', d(0),  '14:00', '16:00', 'Software Engineering Lab', 'Pending Approval', 'High-End PCs'),
        ('BKG-9003', 'CR-201',  'admin',   d(1),  '10:00', '12:00', 'Faculty Meeting',          'Approved',         'Projector'),
        ('BKG-9004', 'LAB-201', 'student', d(2),  '13:00', '15:00', 'Chemistry Practical',      'Approved',         'Fume Hood'),
        ('BKG-9005', 'AUD-01',  'student', d(3),  '16:00', '19:00', 'Society Annual Event',     'Pending Approval', 'Audio System'),
        ('BKG-9006', 'LAB-102', 'student', d(1),  '09:00', '11:00', 'Gaming Session',           'Rejected',         'VR Headsets'),
        ('BKG-9007', 'CR-301',  'student', d(4),  '08:00', '10:00', 'Study Group',              'Cancelled',        ''),
        ('BKG-9008', 'CR-102',  'student', d(-1), '12:00', '13:00', 'Project Meeting',          'Approved',         'Projector'),
        ('BKG-9009', 'CR-101',  'admin',   d(1),  '13:00', '15:00', 'Department Council',       'Approved',         'Projector'),
        ('BKG-9010', 'LAB-301', 'student', d(2),  '10:00', '12:00', 'VR Prototyping',           'Approved',         'VR Headsets'),
    ]


def seed_demo_data(conn, now=None):
    """Replace rooms, bookings and e-mail log with the demonstration dataset (users and settings are kept)."""
    today = (now or get_now(conn)).date()
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('DELETE FROM bookings')
        conn.execute('DELETE FROM email_log')
        conn.execute('DELETE FROM rooms')
        conn.executemany('INSERT INTO rooms (room_id, location, capacity, room_type, equipment) VALUES (?,?,?,?,?)', DEMO_ROOMS)
        conn.executemany('INSERT INTO bookings (booking_id, room_id, user_id, book_date, start_time, end_time, '
                         'purpose, status, equipment) VALUES (?,?,?,?,?,?,?,?,?)', demo_bookings(today))
        conn.execute('INSERT INTO email_log (created_at, recipient, message) VALUES (?,?,?)',
                     (get_now(conn).strftime('%Y-%m-%d %H:%M:%S'), 'Alex Student', 'Booking BKG-9001 approved by The Admin.'))
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def init_db(db_path=None):
    """Create the database file + tables if needed and load demo data on the very first run.
    Safe to call repeatedly (also re-creates everything if the .db file was deleted)."""
    conn = connect(db_path)
    try:
        with open(SCHEMA_FILE, encoding='utf-8') as f:
            conn.executescript(f.read())
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)', (key, value))
        if conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
            for user_id, name, role, password in DEMO_USERS:
                _insert_user(conn, user_id, name, role, password)
            seed_demo_data(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------- settings / rules / clock
def get_setting(conn, key):
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else DEFAULT_SETTINGS[key]


def get_settings(conn):
    return {k: get_setting(conn, k) for k in DEFAULT_SETTINGS}


def _minutes(hhmm):
    """'HH:MM' -> minutes since midnight, or None if the text is not a valid 24 h time."""
    if not TIME_RE.match(hhmm or ''):
        return None
    h, m = int(hhmm[:2]), int(hhmm[3:])
    return h * 60 + m if h < 24 and m < 60 else None


def set_settings(conn, values):
    """Validate and store administrator-editable settings."""
    clean = {}
    tz = str(values.get('timezone', '')).strip()
    if tz:
        try:
            if ZoneInfo is None:
                raise ValueError
            ZoneInfo(tz)
        except Exception:
            raise AppError(400, 'Unknown time zone "%s" (example: Asia/Karachi, Europe/London, UTC).' % tz)
        clean['timezone'] = tz
    url = str(values.get('public_url', '')).strip()
    if url:
        if not re.match(r'^https?://\S+$', url):
            raise AppError(400, 'Public URL must start with http:// or https://')
        clean['public_url'] = url.rstrip('/')
    o, c = values.get('open_time'), values.get('close_time')
    if o is not None or c is not None:
        o, c = o or get_setting(conn, 'open_time'), c or get_setting(conn, 'close_time')
        if _minutes(o) is None or _minutes(c) is None or _minutes(c) <= _minutes(o):
            raise AppError(400, 'Opening time must be before closing time (HH:MM).')
        clean['open_time'], clean['close_time'] = o, c
    for key, low, high in (('max_duration_hours', 1, 24), ('max_weeks', 1, 52)):
        if values.get(key) is not None:
            try:
                n = int(values[key])
            except (TypeError, ValueError):
                n = 0
            if not low <= n <= high:
                raise AppError(400, '%s must be a whole number between %d and %d.' % (key.replace('_', ' '), low, high))
            clean[key] = str(n)
    conn.execute('BEGIN IMMEDIATE')
    for k, v in clean.items():
        conn.execute('INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value', (k, v))
    conn.execute('COMMIT')


def get_rules(conn):
    s = get_settings(conn)
    return {'open_time': s['open_time'], 'close_time': s['close_time'],
            'max_duration_hours': int(s['max_duration_hours']), 'max_weeks': int(s['max_weeks'])}


def get_now(conn=None):
    """Current local date/time (naive) in the configured time zone."""
    tz = get_setting(conn, 'timezone') if conn is not None else DEFAULT_SETTINGS['timezone']
    try:
        return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
    except Exception:
        return datetime.now()


# ---------------------------------------------------------------- row -> dict helpers
def split_eq(text):
    return [x.strip() for x in (text or '').split(',') if x.strip()]


def room_dict(r):
    return {'id': r['room_id'], 'location': r['location'], 'capacity': r['capacity'],
            'type': r['room_type'], 'equipment': split_eq(r['equipment'])}


def booking_dict(r):
    return {'id': r['booking_id'], 'room_id': r['room_id'], 'user_id': r['user_id'], 'requester': r['full_name'],
            'date': r['book_date'], 'start': r['start_time'], 'end': r['end_time'], 'purpose': r['purpose'],
            'status': r['status'], 'equipment': split_eq(r['equipment']), 'created_at': r['created_at']}


def list_rooms(conn):
    return [room_dict(r) for r in conn.execute('SELECT * FROM rooms ORDER BY rowid')]


def get_room(conn, room_id):
    row = conn.execute('SELECT * FROM rooms WHERE room_id = ?', (room_id,)).fetchone()
    return room_dict(row) if row else None


def list_bookings(conn, user_id=None, start=None, end=None, room_id=None, active_only=False):
    """Bookings joined with the requester's name. start/end are ISO dates (inclusive)."""
    sql, args = ['SELECT b.*, u.full_name FROM bookings b JOIN users u ON u.user_id = b.user_id WHERE 1=1'], []
    if user_id:
        sql.append('AND b.user_id = ?'); args.append(user_id)
    if start:
        sql.append('AND b.book_date >= ?'); args.append(start)
    if end:
        sql.append('AND b.book_date <= ?'); args.append(end)
    if room_id:
        sql.append('AND b.room_id = ?'); args.append(room_id)
    if active_only:
        sql.append("AND b.status NOT IN ('Rejected','Cancelled')")
    sql.append('ORDER BY b.book_date, b.start_time, b.booking_id')
    return [booking_dict(r) for r in conn.execute(' '.join(sql), args)]


def list_emails(conn):
    rows = conn.execute('SELECT created_at, recipient, message FROM email_log ORDER BY log_id DESC LIMIT 100')
    return [{'time': r['created_at'], 'recipient': r['recipient'], 'message': r['message']} for r in rows]


def clear_emails(conn):
    conn.execute('DELETE FROM email_log')


def log_email(conn, recipient, message):
    conn.execute('INSERT INTO email_log (created_at, recipient, message) VALUES (?,?,?)',
                 (get_now(conn).strftime('%Y-%m-%d %H:%M:%S'), recipient, message))


# ---------------------------------------------------------------- authentication
def login(conn, username, password):
    row = conn.execute('SELECT * FROM users WHERE user_id = ?', ((username or '').strip().lower(),)).fetchone()
    if not row or not hmac.compare_digest(row['password_hash'], hash_password(password or '', row['password_salt'])):
        raise AppError(401, 'Invalid username or password.')
    return {'user_id': row['user_id'], 'full_name': row['full_name'], 'role': row['role']}


def user_exists(conn, user_id):
    return conn.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,)).fetchone() is not None


def register_user(conn, username, full_name, password):
    """Self-registration always creates a STUDENT account (admins are created only by the seed data)."""
    username, full_name = (username or '').strip().lower(), (full_name or '').strip()
    if not USERNAME_RE.match(username):
        raise AppError(400, 'Username: 3-30 characters, lowercase letters, digits, dot, dash or underscore.')
    if not full_name or len(full_name) > 60:
        raise AppError(400, 'Please enter your full name (max 60 characters).')
    if len(password or '') < 6:
        raise AppError(400, 'Password must have at least 6 characters.')
    if conn.execute('SELECT 1 FROM users WHERE user_id = ?', (username,)).fetchone():
        raise AppError(409, 'Username "%s" is already taken.' % username)
    _insert_user(conn, username, full_name, 'student', password)


# ---------------------------------------------------------------- validation
def validate_request(book_date, start, end, purpose, now, rules):
    """Return an error message, or None if the request is acceptable. Used by the live form AND on save."""
    try:
        if not DATE_RE.match(book_date or ''):
            raise ValueError
        d = date.fromisoformat(book_date)
    except ValueError:
        return 'Please choose a valid date.'
    if d < now.date():
        return 'You cannot book a date in the past.'
    s, e = _minutes(start), _minutes(end)
    if s is None or e is None:
        return 'Please enter a valid start and end time.'
    if e <= s:
        return 'Booking end time must be after start time.'
    if s < _minutes(rules['open_time']) or e > _minutes(rules['close_time']):
        return 'Rooms can only be booked between %s and %s.' % (rules['open_time'], rules['close_time'])
    if e - s > rules['max_duration_hours'] * 60:
        return 'A single booking can last at most %d hours.' % rules['max_duration_hours']
    if d == now.date() and s < now.hour * 60 + now.minute:
        return 'The start time has already passed today.'
    if not (purpose or '').strip():
        return 'Please enter a purpose for the booking.'
    if len(purpose.strip()) > 200:
        return 'Purpose is too long (max 200 characters).'
    return None


def find_conflicts(conn, room_id, book_date, start, end):
    """Active bookings of this room that overlap the requested period (overlap: start < other.end AND other.start < end)."""
    return [booking_dict(r) for r in conn.execute(CONFLICT_SQL, (room_id, book_date, start, end))]


def busy_room_ids(conn, book_date, start, end):
    """Set of room IDs that are NOT free for the requested period."""
    rows = conn.execute("SELECT DISTINCT room_id FROM bookings WHERE book_date = ? AND status NOT IN ('Rejected','Cancelled') "
                        "AND ? < end_time AND start_time < ?", (book_date, start, end))
    return {r['room_id'] for r in rows}


def next_booking_id(conn):
    """Sequential IDs BKG-9001, BKG-9002, ... (called inside the booking transaction)."""
    row = conn.execute("SELECT MAX(CAST(substr(booking_id, 5) AS INTEGER)) FROM bookings").fetchone()
    return 'BKG-%d' % (max(9000, row[0] or 0) + 1)


# ---------------------------------------------------------------- booking operations
def create_bookings(conn, user, data, now=None):
    """Validate, check EVERY date for conflicts, then insert - all inside one transaction.
    If any date conflicts, nothing is saved (all-or-nothing). Returns (list_of_ids, status)."""
    now = now or get_now(conn)
    rules = get_rules(conn)
    room_id = str(data.get('room_id') or '')
    book_date, start, end = data.get('date'), data.get('start'), data.get('end')
    purpose = str(data.get('purpose') or '').strip()
    try:
        weeks = int(data.get('weeks') or 1)
    except (TypeError, ValueError):
        weeks = 1
    weeks = max(1, min(rules['max_weeks'], weeks))

    error = validate_request(book_date, start, end, purpose, now, rules)
    if error:
        raise AppError(400, error)

    first = date.fromisoformat(book_date)
    dates = [(first + timedelta(days=7 * i)).isoformat() for i in range(weeks)]
    status = 'Approved' if user['role'] == 'admin' else 'Pending Approval'

    conn.execute('BEGIN IMMEDIATE')          # lock: nobody else can write until COMMIT/ROLLBACK
    try:
        room = conn.execute('SELECT equipment FROM rooms WHERE room_id = ?', (room_id,)).fetchone()
        if not room:
            raise AppError(404, 'Room not found.')
        conflicts = [d for d in dates if conn.execute(CONFLICT_SQL, (room_id, d, start, end)).fetchone()]
        if conflicts:
            raise AppError(409, 'Schedule conflict: %s is already booked on %s.' % (room_id, ', '.join(conflicts)),
                           conflicts=conflicts)

        wanted = data.get('equipment') if isinstance(data.get('equipment'), list) else []
        equipment = ', '.join(e for e in wanted if e in split_eq(room['equipment']))
        ids = []
        for i, d in enumerate(dates):
            booking_id = next_booking_id(conn)
            label = '%s (Wk %d/%d)' % (purpose, i + 1, weeks) if weeks > 1 else purpose
            conn.execute('INSERT INTO bookings (booking_id, room_id, user_id, book_date, start_time, end_time, purpose, '
                         'status, equipment, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (booking_id, room_id, user['user_id'], d, start, end, label, status, equipment,
                          now.strftime('%Y-%m-%d %H:%M:%S')))
            ids.append(booking_id)
        extra = ' (+%d recurring)' % (len(ids) - 1) if len(ids) > 1 else ''
        log_email(conn, user['full_name'], 'Booking %s%s for %s received - status: %s.' % (ids[0], extra, room_id, status))
        conn.execute('COMMIT')
    except sqlite3.IntegrityError as exc:                      # the trigger / CHECK constraints fired
        conn.execute('ROLLBACK')
        if 'Overlapping' in str(exc):
            raise AppError(409, 'Schedule conflict (rejected by the database): %s' % exc)
        raise AppError(400, 'Data rejected by the database: %s' % exc)
    except BaseException:
        conn.execute('ROLLBACK')
        raise
    return ids, status


def _booking_row(conn, booking_id):
    row = conn.execute('SELECT b.*, u.full_name FROM bookings b JOIN users u ON u.user_id = b.user_id '
                       'WHERE b.booking_id = ?', (booking_id,)).fetchone()
    if not row:
        raise AppError(404, 'Booking not found.')
    return row


def cancel_booking(conn, user, booking_id):
    row = _booking_row(conn, booking_id)
    if user['role'] != 'admin' and row['user_id'] != user['user_id']:
        raise AppError(403, 'You can only cancel your own bookings.')
    if row['status'] in INACTIVE:
        raise AppError(409, 'This booking is already %s.' % row['status'].lower())
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("UPDATE bookings SET status = 'Cancelled' WHERE booking_id = ?", (booking_id,))   # history is kept
    log_email(conn, row['full_name'], 'Booking %s for %s was cancelled by %s.' % (booking_id, row['room_id'], user['full_name']))
    conn.execute('COMMIT')


def set_booking_status(conn, admin, booking_id, new_status):
    if admin['role'] != 'admin':
        raise AppError(403, 'Administrator privileges required.')
    if new_status not in ('Approved', 'Rejected'):
        raise AppError(400, 'Status must be Approved or Rejected.')
    row = _booking_row(conn, booking_id)
    if row['status'] != 'Pending Approval':
        raise AppError(409, 'Only pending bookings can be approved or rejected.')
    conn.execute('BEGIN IMMEDIATE')
    conn.execute('UPDATE bookings SET status = ? WHERE booking_id = ?', (new_status, booking_id))
    log_email(conn, row['full_name'], 'Booking %s for %s was %s by %s.' % (booking_id, row['room_id'], new_status.lower(), admin['full_name']))
    conn.execute('COMMIT')


# ---------------------------------------------------------------- room administration
def _clean_room(data):
    location = str(data.get('location') or '').strip()
    room_type = data.get('type')
    try:
        capacity = int(data.get('capacity'))
    except (TypeError, ValueError):
        capacity = 0
    eq = data.get('equipment')
    equipment = split_eq(eq) if isinstance(eq, str) else [str(x).strip() for x in (eq or []) if str(x).strip()]
    if not location:
        raise AppError(400, 'Location is required.')
    if capacity <= 0:
        raise AppError(400, 'Capacity must be a positive number.')
    if room_type not in ROOM_TYPES:
        raise AppError(400, 'Invalid room type.')
    return location, capacity, room_type, ', '.join(equipment)


def add_room(conn, data):
    room_id = str(data.get('id') or '').strip()
    if not ROOM_ID_RE.match(room_id):
        raise AppError(400, 'Use 2-20 letters, digits or hyphens for the Room ID (e.g. LAB-305).')
    location, capacity, room_type, equipment = _clean_room(data)
    if conn.execute('SELECT 1 FROM rooms WHERE lower(room_id) = lower(?)', (room_id,)).fetchone():
        raise AppError(409, 'Room ID %s already exists (IDs must be unique).' % room_id)
    conn.execute('INSERT INTO rooms (room_id, location, capacity, room_type, equipment) VALUES (?,?,?,?,?)',
                 (room_id, location, capacity, room_type, equipment))


def update_room(conn, room_id, data):
    if not conn.execute('SELECT 1 FROM rooms WHERE room_id = ?', (room_id,)).fetchone():
        raise AppError(404, 'Room not found.')
    location, capacity, room_type, equipment = _clean_room(data)
    conn.execute('UPDATE rooms SET location = ?, capacity = ?, room_type = ?, equipment = ? WHERE room_id = ?',
                 (location, capacity, room_type, equipment, room_id))


# ---------------------------------------------------------------- search / status / reporting
def filter_rooms(rooms, text='', min_capacity=0, room_type='All', equipment=()):
    """Search/filter by free text (ID or location), minimum capacity, room type and required equipment (ALL must match)."""
    text = (text or '').strip().lower()
    out = []
    for r in rooms:
        if text and text not in r['id'].lower() and text not in r['location'].lower():
            continue
        if r['capacity'] < (min_capacity or 0):
            continue
        if room_type not in (None, '', 'All') and r['type'] != room_type:
            continue
        if any(e not in r['equipment'] for e in equipment):
            continue
        out.append(r)
    return out


def room_status_now(conn, room_id, now=None):
    """Live status for the public display: Free/Occupied now + remaining bookings today."""
    now = now or get_now(conn)
    minute_now = now.strftime('%H:%M')
    todays = list_bookings(conn, start=now.date().isoformat(), end=now.date().isoformat(), room_id=room_id, active_only=True)
    current = next((b for b in todays if b['start'] <= minute_now < b['end']), None)
    upcoming = [b for b in todays if b['start'] > minute_now]
    return {'state': 'Occupied' if current else 'Free', 'current': current, 'upcoming': upcoming, 'now': now}


def stats(conn, now=None):
    """KPI numbers for the dashboard."""
    now = now or get_now(conn)
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    return {
        'rooms': one('SELECT COUNT(*) FROM rooms'),
        'total': one('SELECT COUNT(*) FROM bookings'),
        'active': one("SELECT COUNT(*) FROM bookings WHERE status IN ('Pending Approval','Approved')"),
        'pending': one("SELECT COUNT(*) FROM bookings WHERE status = 'Pending Approval'"),
        'today': one("SELECT COUNT(*) FROM bookings WHERE book_date = ? AND status NOT IN ('Rejected','Cancelled')",
                     now.date().isoformat()),
        'cancelled': one("SELECT COUNT(*) FROM bookings WHERE status = 'Cancelled'"),
        'per_room': {r['room_id']: r['n'] for r in conn.execute(
            "SELECT room_id, COUNT(*) AS n FROM bookings WHERE status NOT IN ('Rejected','Cancelled') "
            'GROUP BY room_id ORDER BY room_id')},
    }


def week_start(d):
    """Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _csv_safe(value):
    """Stop spreadsheet formula injection: a cell starting with = + - @ would be run as a formula by Excel."""
    value = str(value)
    return "'" + value if value[:1] in ('=', '+', '-', '@') else value


def export_bookings_csv(conn):
    """All bookings (any status) as CSV text for the administrator."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['booking_id', 'room_id', 'requester', 'user_id', 'date', 'start', 'end', 'purpose', 'status', 'equipment', 'created_at'])
    for b in list_bookings(conn):
        writer.writerow([_csv_safe(x) for x in (b['id'], b['room_id'], b['requester'], b['user_id'], b['date'], b['start'],
                                                b['end'], b['purpose'], b['status'], ', '.join(b['equipment']), b['created_at'])])
    return buf.getvalue()
