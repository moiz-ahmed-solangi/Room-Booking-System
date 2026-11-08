"""Automated tests of the data layer (business rules + SQL). Run:  python3 -m unittest discover -s tests -v"""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db  # noqa: E402

STUDENT = {'user_id': 'student', 'full_name': 'Alex Student', 'role': 'student'}
ADMIN = {'user_id': 'admin', 'full_name': 'Dr. Sarah Admin', 'role': 'admin'}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'test.db')
        db.init_db(self.path)
        self.conn = db.connect(self.path)
        self.today = db.get_now(self.conn).date()
        self.day = (self.today + timedelta(days=20)).isoformat()      # far future, no demo booking there

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def book(self, room='CR-102', start='10:00', end='11:00', user=STUDENT, **extra):
        data = {'room_id': room, 'date': self.day, 'start': start, 'end': end, 'purpose': 'Test'}
        data.update(extra)
        return db.create_bookings(self.conn, user, data)

    def err(self, status, func, *args, **kwargs):
        with self.assertRaises(db.AppError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.status, status, ctx.exception.message)
        return ctx.exception


class TestSetupAndDemoData(Base):
    def test_at_least_ten_rooms_with_mandatory_fields(self):
        rooms = db.list_rooms(self.conn)
        self.assertGreaterEqual(len(rooms), 10)
        for r in rooms:
            self.assertTrue(r['id'] and r['location'] and r['capacity'] > 0 and r['type'])
        self.assertEqual(len({r['id'] for r in rooms}), len(rooms))

    def test_demo_dataset_has_all_statuses_and_users(self):
        statuses = {b['status'] for b in db.list_bookings(self.conn)}
        self.assertEqual(statuses, set(db.STATUSES))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 2)

    def test_init_is_idempotent(self):
        db.init_db(self.path)
        self.assertEqual(len(db.list_rooms(self.conn)), 10)

    def test_deleted_database_is_recreated(self):
        self.conn.close()
        os.remove(self.path)
        db.init_db(self.path)
        self.conn = db.connect(self.path)
        self.assertEqual(len(db.list_rooms(self.conn)), 10)

    def test_passwords_are_hashed_not_stored(self):
        row = self.conn.execute("SELECT * FROM users WHERE user_id='student'").fetchone()
        self.assertNotIn('password123', tuple(row))
        self.assertEqual(len(row['password_hash']), 64)


class TestAuth(Base):
    def test_login_success_and_roles(self):
        self.assertEqual(db.login(self.conn, 'student', 'password123')['role'], 'student')
        self.assertEqual(db.login(self.conn, ' ADMIN ', 'admin123')['role'], 'admin')

    def test_login_failures(self):
        self.err(401, db.login, self.conn, 'student', 'wrong')
        self.err(401, db.login, self.conn, 'nobody', 'x')
        self.err(401, db.login, self.conn, None, None)

    def test_register_creates_student_only(self):
        db.register_user(self.conn, 'newbie', 'New Student', 'secret1')
        self.assertEqual(db.login(self.conn, 'newbie', 'secret1')['role'], 'student')

    def test_register_validation(self):
        self.err(409, db.register_user, self.conn, 'admin', 'Fake Admin', 'secret1')
        self.err(400, db.register_user, self.conn, 'ab', 'Name', 'secret1')
        self.err(400, db.register_user, self.conn, 'okname', '', 'secret1')
        self.err(400, db.register_user, self.conn, 'okname', 'Name', '123')


class TestValidation(Base):
    def check(self, d=None, s='10:00', e='11:00', purpose='x', now=None):
        return db.validate_request(d or self.day, s, e, purpose, now or db.get_now(self.conn), db.get_rules(self.conn))

    def test_valid_request(self):
        self.assertIsNone(self.check())

    def test_end_before_start_rejected(self):
        self.assertIn('after start', self.check(s='12:00', e='11:00'))

    def test_equal_times_rejected(self):
        self.assertIn('after start', self.check(s='10:00', e='10:00'))

    def test_bad_time_format(self):
        self.assertIn('valid start and end', self.check(s='25:00'))
        self.assertIn('valid start and end', self.check(s='ab:cd'))

    def test_bad_or_past_date(self):
        self.assertIn('valid date', self.check(d='2026-13-45'))
        self.assertIn('past', self.check(d=(self.today - timedelta(days=1)).isoformat()))

    def test_start_time_passed_today(self):
        now = datetime.combine(self.today, datetime.min.time()).replace(hour=15)
        self.assertIn('already passed', self.check(d=self.today.isoformat(), s='09:00', e='10:00', now=now))
        self.assertIsNone(self.check(d=self.today.isoformat(), s='16:00', e='17:00', now=now))

    def test_opening_hours_rule(self):
        self.assertIn('between 07:00 and 22:00', self.check(s='05:00', e='06:00'))
        self.assertIn('between 07:00 and 22:00', self.check(s='21:00', e='23:00'))

    def test_max_duration_rule_and_setting_change(self):
        self.assertIn('at most 6 hours', self.check(s='08:00', e='15:00'))
        db.set_settings(self.conn, {'max_duration_hours': 8})                  # configuration change
        self.assertIsNone(self.check(s='08:00', e='15:00'))

    def test_purpose_rules(self):
        self.assertIn('purpose', self.check(purpose='   '))
        self.assertIn('too long', self.check(purpose='x' * 201))


class TestBooking(Base):
    def test_successful_booking_student_pending_admin_approved(self):
        ids, status = self.book()
        self.assertEqual(status, 'Pending Approval')
        self.assertRegex(ids[0], r'^BKG-\d+$')
        _, status = self.book(room='CR-201', user=ADMIN)
        self.assertEqual(status, 'Approved')

    def test_overlap_rejected(self):
        self.book(start='10:00', end='11:00')
        for s, e in (('10:30', '11:30'), ('09:30', '10:30'), ('10:15', '10:45'), ('09:00', '12:00'), ('10:00', '11:00')):
            self.err(409, self.book, start=s, end=e)

    def test_back_to_back_is_allowed(self):
        self.book(start='10:00', end='11:00')
        self.book(start='11:00', end='12:00')
        self.book(start='09:00', end='10:00')

    def test_other_room_same_time_allowed(self):
        self.book(room='CR-102')
        self.book(room='CR-201')

    def test_other_day_same_time_allowed(self):
        self.book()
        db.create_bookings(self.conn, STUDENT, {'room_id': 'CR-102', 'date': (self.today + timedelta(days=21)).isoformat(),
                                                'start': '10:00', 'end': '11:00', 'purpose': 'x'})

    def test_cancelled_booking_frees_the_slot_and_keeps_history(self):
        ids, _ = self.book()
        db.cancel_booking(self.conn, STUDENT, ids[0])
        self.book()                                                           # same slot works again
        row = self.conn.execute('SELECT status FROM bookings WHERE booking_id=?', (ids[0],)).fetchone()
        self.assertEqual(row['status'], 'Cancelled')                          # row still exists = history

    def test_rejected_booking_does_not_block(self):
        ids, _ = self.book()
        db.set_booking_status(self.conn, ADMIN, ids[0], 'Rejected')
        self.book()

    def test_validation_errors_save_nothing(self):
        before = len(db.list_bookings(self.conn))
        self.err(400, self.book, start='12:00', end='11:00')
        self.err(400, self.book, purpose='')
        self.err(404, self.book, room='NOPE-1')
        self.assertEqual(len(db.list_bookings(self.conn)), before)

    def test_recurring_booking_creates_all_weeks(self):
        ids, _ = self.book(weeks=3)
        self.assertEqual(len(ids), 3)
        dates = sorted(b['date'] for b in db.list_bookings(self.conn) if b['id'] in ids)
        self.assertEqual((datetime.fromisoformat(dates[2]) - datetime.fromisoformat(dates[0])).days, 14)

    def test_recurring_is_all_or_nothing(self):
        blocker_day = (datetime.fromisoformat(self.day) + timedelta(days=14)).date().isoformat()   # week 3 collides
        db.create_bookings(self.conn, ADMIN, {'room_id': 'CR-102', 'date': blocker_day, 'start': '10:30', 'end': '11:30', 'purpose': 'blocker'})
        before = len(db.list_bookings(self.conn))
        exc = self.err(409, self.book, weeks=4)
        self.assertEqual(exc.extra['conflicts'], [blocker_day])
        self.assertEqual(len(db.list_bookings(self.conn)), before)            # rollback: nothing saved

    def test_equipment_limited_to_what_room_has(self):
        ids, _ = self.book(equipment=['Projector', 'Jet Engine'])
        b = [x for x in db.list_bookings(self.conn) if x['id'] == ids[0]][0]
        self.assertEqual(b['equipment'], ['Projector'])

    def test_ids_are_unique_and_sequential(self):
        a, _ = self.book(start='08:00', end='09:00')
        b, _ = self.book(start='09:00', end='10:00')
        self.assertEqual(int(b[0][4:]), int(a[0][4:]) + 1)

    def test_email_logged_on_booking_cancel_approve(self):
        n0 = len(db.list_emails(self.conn))
        ids, _ = self.book()
        db.set_booking_status(self.conn, ADMIN, ids[0], 'Approved')
        db.cancel_booking(self.conn, STUDENT, ids[0])
        self.assertEqual(len(db.list_emails(self.conn)), n0 + 3)

    def test_booking_persists_in_database_file(self):
        ids, _ = self.book()
        self.conn.close()
        self.conn = db.connect(self.path)                                      # brand-new connection
        self.assertIn(ids[0], [b['id'] for b in db.list_bookings(self.conn)])


class TestDatabaseConstraints(Base):
    """The database protects itself even if application code is bypassed."""
    SQL = ('INSERT INTO bookings (booking_id, room_id, user_id, book_date, start_time, end_time, purpose, status) '
           'VALUES (?,?,?,?,?,?,?,?)')

    def test_trigger_blocks_overlap(self):
        self.conn.execute(self.SQL, ('X-1', 'CR-102', 'student', self.day, '10:00', '11:00', 'a', 'Approved'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self.SQL, ('X-2', 'CR-102', 'student', self.day, '10:30', '11:30', 'b', 'Approved'))

    def test_check_blocks_end_before_start(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self.SQL, ('X-3', 'CR-102', 'student', self.day, '12:00', '11:00', 'c', 'Approved'))

    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self.SQL, ('X-4', 'NO-ROOM', 'student', self.day, '10:00', '11:00', 'd', 'Approved'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self.SQL, ('X-5', 'CR-102', 'ghost', self.day, '10:00', '11:00', 'e', 'Approved'))

    def test_primary_key_unique(self):
        self.conn.execute(self.SQL, ('X-6', 'CR-102', 'student', self.day, '10:00', '11:00', 'f', 'Approved'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self.SQL, ('X-6', 'CR-201', 'student', self.day, '10:00', '11:00', 'g', 'Approved'))


class TestPermissionsAndWorkflow(Base):
    def test_student_cannot_cancel_someone_elses_booking(self):
        ids, _ = self.book(user=ADMIN)
        self.err(403, db.cancel_booking, self.conn, STUDENT, ids[0])

    def test_admin_can_cancel_any_booking(self):
        ids, _ = self.book(user=STUDENT)
        db.cancel_booking(self.conn, ADMIN, ids[0])

    def test_cannot_cancel_twice_or_unknown(self):
        ids, _ = self.book()
        db.cancel_booking(self.conn, STUDENT, ids[0])
        self.err(409, db.cancel_booking, self.conn, STUDENT, ids[0])
        self.err(404, db.cancel_booking, self.conn, STUDENT, 'BKG-0')

    def test_only_admin_can_approve_and_only_pending(self):
        ids, _ = self.book()
        self.err(403, db.set_booking_status, self.conn, STUDENT, ids[0], 'Approved')
        db.set_booking_status(self.conn, ADMIN, ids[0], 'Approved')
        self.err(409, db.set_booking_status, self.conn, ADMIN, ids[0], 'Rejected')
        self.err(400, db.set_booking_status, self.conn, ADMIN, ids[0], 'Banana')


class TestRoomsAndSearch(Base):
    def test_add_room_and_duplicate_rejected_case_insensitive(self):
        db.add_room(self.conn, {'id': 'LAB-305', 'location': 'Annex', 'capacity': 40, 'type': 'Computer Lab', 'equipment': 'PCs, Projector'})
        self.assertEqual(db.get_room(self.conn, 'LAB-305')['equipment'], ['PCs', 'Projector'])
        self.err(409, db.add_room, self.conn, {'id': 'lab-305', 'location': 'x', 'capacity': 1, 'type': 'Classroom'})

    def test_add_room_validation(self):
        good = {'id': 'R-1', 'location': 'x', 'capacity': 10, 'type': 'Classroom'}
        self.err(400, db.add_room, self.conn, dict(good, id='bad id!'))
        self.err(400, db.add_room, self.conn, dict(good, capacity=0))
        self.err(400, db.add_room, self.conn, dict(good, capacity='abc'))
        self.err(400, db.add_room, self.conn, dict(good, type='Swimming Pool'))
        self.err(400, db.add_room, self.conn, dict(good, location=' '))

    def test_edit_room(self):
        db.update_room(self.conn, 'CR-102', {'location': 'New Wing', 'capacity': 99, 'type': 'Classroom', 'equipment': 'Whiteboard'})
        r = db.get_room(self.conn, 'CR-102')
        self.assertEqual((r['location'], r['capacity'], r['equipment']), ('New Wing', 99, ['Whiteboard']))
        self.err(404, db.update_room, self.conn, 'NOPE', {'location': 'x', 'capacity': 1, 'type': 'Classroom'})

    def test_filter_by_equipment_vr_headsets(self):
        found = {r['id'] for r in db.filter_rooms(db.list_rooms(self.conn), equipment=['VR Headsets'])}
        self.assertEqual(found, {'LAB-102', 'LAB-301'})

    def test_filter_by_capacity_type_text(self):
        rooms = db.list_rooms(self.conn)
        self.assertEqual({r['id'] for r in db.filter_rooms(rooms, min_capacity=100)}, {'AUD-01'})
        self.assertTrue(all(r['type'] == 'Science Lab' for r in db.filter_rooms(rooms, room_type='Science Lab')))
        self.assertEqual({r['id'] for r in db.filter_rooms(rooms, text='science wing')}, {'LAB-201', 'LAB-202'})
        self.assertEqual(db.filter_rooms(rooms, equipment=['VR Headsets', 'Fume Hood']), [])

    def test_busy_rooms_and_conflict_details(self):
        self.book(room='CR-102', start='10:00', end='11:00')
        self.assertIn('CR-102', db.busy_room_ids(self.conn, self.day, '10:30', '11:30'))
        self.assertNotIn('CR-102', db.busy_room_ids(self.conn, self.day, '11:00', '12:00'))
        self.assertEqual(len(db.find_conflicts(self.conn, 'CR-102', self.day, '10:30', '11:30')), 1)

    def test_room_status_now(self):
        now = datetime.combine(self.today, datetime.min.time()).replace(hour=10)
        self.assertEqual(db.room_status_now(self.conn, 'CR-101', now)['state'], 'Occupied')    # demo BKG-9001 09-11
        self.assertEqual(db.room_status_now(self.conn, 'CR-101', now.replace(hour=12))['state'], 'Free')
        self.assertEqual(db.room_status_now(self.conn, 'CR-101', now.replace(hour=8))['upcoming'][0]['id'], 'BKG-9001')


class TestSettingsAndReports(Base):
    def test_settings_validation(self):
        self.err(400, db.set_settings, self.conn, {'timezone': 'Mars/Olympus'})
        self.err(400, db.set_settings, self.conn, {'open_time': '20:00', 'close_time': '08:00'})
        self.err(400, db.set_settings, self.conn, {'max_weeks': 0})
        self.err(400, db.set_settings, self.conn, {'public_url': 'ftp://x'})

    def test_settings_saved_and_used(self):
        db.set_settings(self.conn, {'open_time': '08:00', 'close_time': '20:00', 'public_url': 'https://my.app/'})
        self.assertEqual(db.get_rules(self.conn)['open_time'], '08:00')
        self.assertEqual(db.get_setting(self.conn, 'public_url'), 'https://my.app')

    def test_stats(self):
        s = db.stats(self.conn)
        self.assertEqual((s['rooms'], s['total']), (10, 10))
        self.assertEqual(s['pending'], 2)

    def test_csv_export_and_formula_injection_guard(self):
        self.book(purpose='=HYPERLINK("http://evil")')
        csv_text = db.export_bookings_csv(self.conn)
        self.assertTrue(csv_text.startswith('booking_id,room_id'))
        self.assertIn("'=HYPERLINK", csv_text)
        self.assertEqual(csv_text.count('\n'), 12)                              # header + 11 bookings

    def test_reset_demo_data(self):
        self.book()
        db.seed_demo_data(self.conn)
        self.assertEqual(len(db.list_bookings(self.conn)), 10)

    def test_week_start_is_monday(self):
        self.assertEqual(db.week_start(datetime(2026, 11, 8).date()).isoformat(), '2026-11-02')


if __name__ == '__main__':
    unittest.main()
