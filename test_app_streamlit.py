"""Headless run of the real Streamlit app with Streamlit's AppTest (skipped automatically if streamlit is missing).
Checks that every page renders without an exception."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                            # streamlit not installed
    AppTest = None

USERS = {'student': {'user_id': 'student', 'full_name': 'Alex Student', 'role': 'student'},
         'admin': {'user_id': 'admin', 'full_name': 'Dr. Sarah Admin', 'role': 'admin'}}
PAGES = ['Dashboard', 'Rooms & Labs', 'Reserve Facility', 'My Bookings', 'Timetable View', 'Live Display & QR']


@unittest.skipIf(AppTest is None, 'streamlit is not installed')
class TestStreamlitApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['EF101_DB'] = os.path.join(cls.tmp.name, 'apptest.db')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_app(self, user=None, page=None):
        at = AppTest.from_file(os.path.join(ROOT, 'app.py'), default_timeout=60)
        if user:
            at.session_state['user'] = USERS[user]
        if page:
            at.session_state['page'] = page
        return at.run()

    def test_login_page_renders(self):
        at = self.run_app()
        self.assertFalse(at.exception, at.exception)

    def test_every_student_page_renders(self):
        for page in PAGES:
            with self.subTest(page=page):
                at = self.run_app('student', page)
                self.assertFalse(at.exception, at.exception)

    def test_admin_page_renders(self):
        at = self.run_app('admin', 'Admin Control')
        self.assertFalse(at.exception, at.exception)


if __name__ == '__main__':
    unittest.main()
