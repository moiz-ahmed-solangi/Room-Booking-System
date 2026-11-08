"""Tests of the HTML helpers in ui.py (no Streamlit needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ui  # noqa: E402

ROOM = {'id': 'CR-101', 'location': 'Main <b>Block</b>', 'capacity': 45, 'type': 'Classroom', 'equipment': ['Projector']}
BOOKING = {'id': 'BKG-1', 'room_id': 'CR-101', 'requester': 'Eve', 'date': '2026-11-10', 'start': '09:00', 'end': '11:00',
           'purpose': '<script>alert(1)</script>', 'status': 'Approved'}


class TestUi(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(ui.esc('<a href="x">&'), '&lt;a href=&quot;x&quot;&gt;&amp;')

    def test_xss_in_timetable_and_card(self):
        page = ui.timetable_html([('CR-101', 'Classroom', [BOOKING])])
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<b>Block</b>', ui.room_card_html(ROOM))

    def test_cancelled_and_rejected_not_drawn(self):
        for status in ('Cancelled', 'Rejected'):
            page = ui.timetable_html([('R', '', [dict(BOOKING, status=status, purpose='GHOST')])])
            self.assertNotIn('GHOST', page)

    def test_booking_position_is_proportional(self):
        page = ui.timetable_html([('R', '', [dict(BOOKING, purpose='ok')])], 7, 22)    # 09:00 -> (2h/15h)=13.333%
        self.assertIn('left:13.333%', page)
        self.assertIn('width:13.333%', page)

    def test_availability_badges(self):
        self.assertIn('BUSY', ui.room_card_html(ROOM, True))
        self.assertIn('AVAILABLE', ui.room_card_html(ROOM, False))
        self.assertNotIn('BUSY', ui.room_card_html(ROOM, None))

    def test_hour_bounds(self):
        self.assertEqual(ui.hour_bounds('07:00', '22:00'), (7, 22))
        self.assertEqual(ui.hour_bounds('08:00', '22:30'), (8, 23))

    def test_kiosk_hides_requester(self):
        from datetime import datetime
        status = {'state': 'Occupied', 'current': dict(BOOKING, purpose='Seminar'), 'upcoming': [], 'now': datetime(2026, 11, 10, 10, 0)}
        page = ui.kiosk_html(ROOM, status)
        self.assertIn('Occupied until 11:00', page)
        self.assertNotIn('Eve', page)

    def test_qr_png_returns_png_or_none(self):
        data = ui.qr_png('http://localhost:8501/?room=CR-101')
        self.assertTrue(data is None or data.startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
