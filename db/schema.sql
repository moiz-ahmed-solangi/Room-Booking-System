-- schema.sql - SQL schema of the EF101-P07 Campus Booking System (SQLite).
-- Executed by database.init_db() on every start; "IF NOT EXISTS" makes it safe to re-run.

PRAGMA foreign_keys = ON;

-- USER: who can log in. user_id is the PRIMARY KEY (unique login name).
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('student', 'admin')),
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL
);

-- ROOM: a classroom / laboratory. room_id is the PRIMARY KEY (unique ID/name).
CREATE TABLE IF NOT EXISTS rooms (
    room_id   TEXT PRIMARY KEY,
    location  TEXT NOT NULL,
    capacity  INTEGER NOT NULL CHECK (capacity > 0),
    room_type TEXT NOT NULL CHECK (room_type IN ('Classroom', 'Computer Lab', 'Science Lab', 'Auditorium')),
    equipment TEXT NOT NULL DEFAULT ''          -- comma separated list, e.g. 'Projector, Smart Board'
);

-- BOOKING: one reservation of one room by one user. Links ROOM and USER (foreign keys).
CREATE TABLE IF NOT EXISTS bookings (
    booking_id TEXT PRIMARY KEY,                -- BKG-9001, BKG-9002, ...
    room_id    TEXT NOT NULL REFERENCES rooms(room_id),
    user_id    TEXT NOT NULL REFERENCES users(user_id),
    book_date  TEXT NOT NULL,                   -- ISO date  YYYY-MM-DD
    start_time TEXT NOT NULL,                   -- 24 h      HH:MM (zero padded -> text order = time order)
    end_time   TEXT NOT NULL,
    purpose    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'Pending Approval'
               CHECK (status IN ('Pending Approval', 'Approved', 'Rejected', 'Cancelled')),
    equipment  TEXT NOT NULL DEFAULT '',        -- equipment requested for this booking
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (end_time > start_time)               -- last line of defence against invalid time ranges
);
CREATE INDEX IF NOT EXISTS idx_bookings_room_date ON bookings(room_id, book_date);

-- LAST LINE OF DEFENCE against double booking: even if application code had a bug, the database
-- refuses a new active booking that overlaps an existing active booking of the same room.
-- Overlap rule:  new.start < existing.end  AND  existing.start < new.end
CREATE TRIGGER IF NOT EXISTS trg_no_overlap
BEFORE INSERT ON bookings
WHEN NEW.status NOT IN ('Rejected', 'Cancelled')
BEGIN
    SELECT RAISE(ABORT, 'Overlapping booking for this room')
    WHERE EXISTS (
        SELECT 1 FROM bookings
        WHERE room_id = NEW.room_id
          AND book_date = NEW.book_date
          AND status NOT IN ('Rejected', 'Cancelled')
          AND NEW.start_time < end_time
          AND start_time < NEW.end_time
    );
END;

-- Simulated e-mail notifications (nothing is really sent).
CREATE TABLE IF NOT EXISTS email_log (
    log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    recipient  TEXT NOT NULL,
    message    TEXT NOT NULL
);

-- Configuration values that an administrator can change in the UI (rules, time zone, public URL).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
