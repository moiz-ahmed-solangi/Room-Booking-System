"""
app.py - Streamlit front end of the EF101-P07 Campus Booking System.

Run:   streamlit run app.py          ->  http://localhost:8501

Layers:  app.py (pages, widgets)  ->  database.py (business rules + SQL)  ->  campus_booking.db (SQLite)
         ui.py  builds the HTML pieces (cards, timetable, public display, QR code).

Streamlit re-runs this whole script from top to bottom every time the user touches a widget. Anything that
must survive between runs (who is logged in, a flash message) lives in st.session_state; everything that must
survive a server restart lives in the SQLite database.
"""
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

import database as db
import ui

st.set_page_config(page_title='EF101-P07 Campus Booking', page_icon='🏫', layout='wide')

USER_PAGES = ['Dashboard', 'Rooms & Labs', 'Reserve Facility', 'My Bookings', 'Timetable View', 'Live Display & QR']
ADMIN_PAGE = 'Admin Control'


# ================================================================ small helpers
@contextmanager
def open_db():
    """One short-lived SQLite connection per script run (re-creates the DB file if it was deleted)."""
    db.init_db()
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def render_html(markup):
    """Show an HTML string (all user text inside it was escaped in ui.py)."""
    if hasattr(st, 'html'):
        st.html(markup)
    else:                                              # very old Streamlit versions
        st.markdown(markup, unsafe_allow_html=True)


def flash(kind, message):
    """Remember a message across st.rerun() so it can be shown after the page reloads."""
    st.session_state['flash'] = (kind, message)


def show_flash():
    item = st.session_state.pop('flash', None)
    if item:
        {'success': st.success, 'error': st.error, 'warning': st.warning}.get(item[0], st.info)(item[1])


def hhmm(t):
    return t.strftime('%H:%M')


def bookings_table(bookings, show_requester=True):
    rows = [{'ID': b['id'], 'Room': b['room_id'], 'Date': b['date'], 'Start': b['start'], 'End': b['end'],
             'Purpose': b['purpose'], 'Status': b['status'], 'Equipment': ', '.join(b['equipment']),
             **({'Requester': b['requester']} if show_requester else {})} for b in bookings]
    if not rows:
        st.info('No bookings to show.')
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def period_picker(conn, key, default_start=time(10, 0), default_end=time(11, 0), day_offset=1):
    """Date + start + end widgets. Returns (iso_date, 'HH:MM', 'HH:MM')."""
    now = db.get_now(conn)
    c1, c2, c3 = st.columns(3)
    d = c1.date_input('Date', value=now.date() + timedelta(days=day_offset), min_value=date(2020, 1, 1), key=key + '_date')
    s = c2.time_input('Start time', value=default_start, step=900, key=key + '_start')
    e = c3.time_input('End time', value=default_end, step=900, key=key + '_end')
    return d.isoformat(), hhmm(s), hhmm(e)


# ================================================================ public pages (no login)
def room_label(r):
    return '%s - %s (%d seats)' % (r['id'], r['type'], r['capacity'])


@st.fragment(run_every=30)
def kiosk_fragment(room_id):
    """Public 'outside the room' display; refreshes itself every 30 seconds."""
    with open_db() as conn:
        room = db.get_room(conn, room_id)
        if not room:
            st.error('Unknown room "%s".' % room_id)
            return
        render_html(ui.kiosk_html(room, db.room_status_now(conn, room_id)))


@st.fragment(run_every=30)
def board_fragment():
    """Public all-rooms availability board; refreshes itself every 30 seconds."""
    with open_db() as conn:
        for room in db.list_rooms(conn):
            render_html(ui.board_row_html(room, db.room_status_now(conn, room['id'])))


def public_pages():
    """?room=CR-101 shows one room's display, ?board=1 shows every room. Returns True if a public page was shown."""
    params = st.query_params
    if params.get('room'):
        st.markdown('### Room availability')
        kiosk_fragment(str(params.get('room')))
        return True
    if params.get('board'):
        st.markdown('### Live availability - all rooms')
        board_fragment()
        return True
    return False


# ================================================================ login
def login_screen(conn):
    st.title('🏫 EF101-P07 Campus Booking')
    st.caption('Classroom & Laboratory Booking System - view, reserve and manage rooms without schedule conflicts.')
    tab_in, tab_reg, tab_board = st.tabs(['Sign in', 'Create student account', 'Live availability board'])
    with tab_in:
        with st.form('login_form'):
            username = st.text_input('Username')
            password = st.text_input('Password', type='password')
            submitted = st.form_submit_button('Sign in')
        if submitted:
            try:
                st.session_state['user'] = db.login(conn, username, password)
            except db.AppError as err:
                st.error(err.message)
            else:
                st.rerun()
        st.info('Demo accounts - student: `student` / `password123`   |   administrator: `admin` / `admin123`')
    with tab_reg:
        with st.form('register_form'):
            r_user = st.text_input('Choose a username')
            r_name = st.text_input('Full name')
            r_pass = st.text_input('Password (min 6 characters)', type='password')
            r_submit = st.form_submit_button('Create account')
        if r_submit:
            try:
                db.register_user(conn, r_user, r_name, r_pass)
                st.success('Account created. You can sign in now.')
            except db.AppError as err:
                st.error(err.message)
    with tab_board:
        st.caption('Public view - no login needed. Refreshes every 30 seconds.')
        board_fragment()


# ================================================================ pages
def page_dashboard(conn, user):
    st.header('Dashboard')
    s = db.stats(conn)
    c = st.columns(5)
    c[0].metric('Rooms & labs', s['rooms'])
    c[1].metric('Active bookings', s['active'])
    c[2].metric('Pending approval', s['pending'])
    c[3].metric('Booked today', s['today'])
    c[4].metric('Cancelled (history)', s['cancelled'])
    now = db.get_now(conn)
    st.subheader("Today's schedule - %s" % now.strftime('%A %d %B %Y'))
    bookings_table(db.list_bookings(conn, start=now.date().isoformat(), end=now.date().isoformat(), active_only=True))
    st.subheader('Active bookings per room')
    if s['per_room']:
        st.bar_chart(pd.Series(s['per_room'], name='Active bookings'))


def page_rooms(conn, user):
    st.header('Rooms & Laboratories')
    rooms = db.list_rooms(conn)
    all_eq = sorted({e for r in rooms for e in r['equipment']})
    c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
    text = c1.text_input('Search (room ID or location)')
    min_cap = c2.number_input('Min capacity', min_value=0, max_value=500, value=0, step=5)
    rtype = c3.selectbox('Room type', ['All'] + list(db.ROOM_TYPES))
    equipment = c4.multiselect('Required equipment (all must be present)', all_eq)
    busy = None
    if st.checkbox('Show availability for a specific period'):
        d, start, end = period_picker(conn, 'rooms_period')
        err = db.validate_request(d, start, end, 'x', db.get_now(conn), db.get_rules(conn))
        if err:
            st.warning(err)
        else:
            busy = db.busy_room_ids(conn, d, start, end)
    found = db.filter_rooms(rooms, text, min_cap, rtype, equipment)
    st.caption('%d of %d rooms match.' % (len(found), len(rooms)))
    cols = st.columns(3)
    for i, room in enumerate(found):
        with cols[i % 3]:
            render_html(ui.room_card_html(room, None if busy is None else room['id'] in busy))
    if found:
        st.subheader('Room details')
        pick = st.selectbox('Room', [r['id'] for r in found], key='rooms_detail_pick')
        room = db.get_room(conn, pick)
        st.table(pd.DataFrame([{'Room ID': room['id'], 'Location': room['location'], 'Capacity': room['capacity'],
                                'Type': room['type'], 'Equipment': ', '.join(room['equipment']) or '-'}]).set_index('Room ID'))
        st.markdown('**Upcoming bookings of %s**' % pick)
        bookings_table(db.list_bookings(conn, start=db.get_now(conn).date().isoformat(), room_id=pick, active_only=True))


def page_reserve(conn, user):
    st.header('Reserve a Facility')
    rooms = db.list_rooms(conn)
    rules = db.get_rules(conn)
    by_id = {r['id']: r for r in rooms}
    room_id = st.selectbox('Room / laboratory', list(by_id), format_func=lambda i: room_label(by_id[i]))
    room = by_id[room_id]
    st.caption('%s | %s | equipment: %s' % (room['type'], room['location'], ', '.join(room['equipment']) or 'none'))
    d, start, end = period_picker(conn, 'reserve')
    purpose = st.text_input('Purpose of booking', max_chars=200, placeholder='e.g. Physics seminar')
    equipment = st.multiselect('Equipment needed (optional)', room['equipment'])
    weeks = st.number_input('Repeat weekly for how many weeks? (1 = single booking)', min_value=1,
                            max_value=rules['max_weeks'], value=1, step=1)

    # Live availability: re-evaluated on every change of room / date / time (Streamlit re-runs the script).
    error = db.validate_request(d, start, end, purpose or 'x', db.get_now(conn), rules)
    ok_to_submit = False
    if error:
        st.error(error)
    else:
        dates = [(date.fromisoformat(d) + timedelta(days=7 * i)).isoformat() for i in range(int(weeks))]
        clashes = [(dd, c) for dd in dates for c in db.find_conflicts(conn, room_id, dd, start, end)]
        if clashes:
            st.error('Not available - %s is already booked at that time:' % room_id)
            for dd, c in clashes[:6]:
                st.write('- %s %s-%s (%s, %s)' % (dd, c['start'], c['end'], c['purpose'], c['status']))
        else:
            ok_to_submit = True
            st.success('Available: %s is free on %s from %s to %s%s.'
                       % (room_id, d, start, end, ' (and on all %d weekly dates)' % weeks if weeks > 1 else ''))
    day_bookings = db.list_bookings(conn, start=d, end=d, room_id=room_id, active_only=True)
    with st.expander('Existing bookings of %s on %s (%d)' % (room_id, d, len(day_bookings))):
        bookings_table(day_bookings)

    if st.button('Submit booking request', type='primary'):
        try:
            ids, status = db.create_bookings(conn, user, {'room_id': room_id, 'date': d, 'start': start, 'end': end,
                                                           'purpose': purpose, 'equipment': equipment, 'weeks': int(weeks)})
        except db.AppError as err:
            st.error(err.message)
        else:
            flash('success', 'Booking %s created for %s - status: %s.' % (', '.join(ids), room_id, status))
            st.rerun()
    elif not ok_to_submit:
        st.caption('Fix the problem above to enable a successful booking (the server re-checks everything on submit).')


def page_my_bookings(conn, user):
    st.header('My Bookings')
    mine = db.list_bookings(conn, user_id=user['user_id'])
    statuses = st.multiselect('Show statuses', list(db.STATUSES), default=list(db.STATUSES))
    shown = [b for b in mine if b['status'] in statuses]
    bookings_table(shown, show_requester=False)
    cancellable = [b for b in mine if b['status'] not in db.INACTIVE]
    st.subheader('Cancel a booking')
    if not cancellable:
        st.caption('You have no active bookings. (Cancelled bookings stay in your history.)')
        return
    labels = {b['id']: '%s - %s %s %s-%s (%s)' % (b['id'], b['room_id'], b['date'], b['start'], b['end'], b['status']) for b in cancellable}
    pick = st.selectbox('Booking', list(labels), format_func=labels.get)
    if st.button('Cancel selected booking'):
        try:
            db.cancel_booking(conn, user, pick)
        except db.AppError as err:
            st.error(err.message)
        else:
            flash('success', 'Booking %s was cancelled. The room is free again; the record stays in your history.' % pick)
            st.rerun()


def page_timetable(conn, user):
    st.header('Timetable View')
    rules = db.get_rules(conn)
    start_h, end_h = ui.hour_bounds(rules['open_time'], rules['close_time'])
    mode = st.radio('View', ['Daily', 'Weekly'], horizontal=True)
    today = db.get_now(conn).date()
    if mode == 'Daily':
        day = st.date_input('Day', value=today, key='tt_day')
        bookings = db.list_bookings(conn, start=day.isoformat(), end=day.isoformat(), active_only=True)
        rows = [(r['id'], r['type'], [b for b in bookings if b['room_id'] == r['id']]) for r in db.list_rooms(conn)]
        st.subheader('All rooms on %s' % day.strftime('%A %d %B %Y'))
    else:
        anyday = st.date_input('Any day inside the week you want to see', value=today, key='tt_week')
        monday = db.week_start(anyday)
        rooms = {r['id']: r for r in db.list_rooms(conn)}
        room_id = st.selectbox('Room', list(rooms), format_func=lambda i: room_label(rooms[i]), key='tt_room')
        bookings = db.list_bookings(conn, start=monday.isoformat(), end=(monday + timedelta(days=6)).isoformat(),
                                    room_id=room_id, active_only=True)
        days = [monday + timedelta(days=i) for i in range(7)]
        rows = [(d.strftime('%a %d %b'), 'today' if d == today else '', [b for b in bookings if b['date'] == d.isoformat()]) for d in days]
        st.subheader('%s - week of %s' % (room_id, monday.strftime('%d %B %Y')))
    render_html(ui.timetable_html(rows, start_h, end_h))


def page_display(conn, user):
    st.header('Live Display & QR Codes')
    st.caption('Print a QR code and stick it outside the room: scanning it opens a public page showing whether the room is free now.')
    base = db.get_setting(conn, 'public_url').rstrip('/')
    rooms = {r['id']: r for r in db.list_rooms(conn)}
    room_id = st.selectbox('Room', list(rooms), format_func=lambda i: room_label(rooms[i]), key='qr_room')
    url = '%s/?room=%s' % (base, room_id)
    left, right = st.columns([1, 2])
    with left:
        png = ui.qr_png(url)
        if png:
            st.image(png, width=240, caption='QR code for %s' % room_id)
            st.download_button('Download QR (PNG)', png, file_name='qr_%s.png' % room_id, mime='image/png')
        else:
            st.warning('Install the optional package `segno` (pip install segno) to draw QR codes.')
        st.code(url, language=None)
        st.caption('Board of all rooms: %s/?board=1' % base)
        st.caption('Wrong address? An administrator can change the Public URL in Admin Control -> Settings.')
    with right:
        st.markdown('**What people see after scanning (preview):**')
        render_html(ui.kiosk_html(rooms[room_id], db.room_status_now(conn, room_id)))


# ---------------------------------------------------------------- administrator
def admin_bookings(conn, user):
    pending = [b for b in db.list_bookings(conn) if b['status'] == 'Pending Approval']
    st.subheader('Approval queue (%d pending)' % len(pending))
    if not pending:
        st.caption('Nothing waiting for approval.')
    for b in pending:
        c1, c2, c3 = st.columns([6, 1, 1])
        c1.write('**%s** - %s on %s %s-%s - %s (requested by %s)' % (b['id'], b['room_id'], b['date'], b['start'], b['end'], b['purpose'], b['requester']))
        for col, label, new in ((c2, 'Approve', 'Approved'), (c3, 'Reject', 'Rejected')):
            if col.button(label, key='%s_%s' % (label, b['id'])):
                try:
                    db.set_booking_status(conn, user, b['id'], new)
                except db.AppError as err:
                    st.error(err.message)
                else:
                    flash('success', 'Booking %s %s.' % (b['id'], new.lower()))
                    st.rerun()
    st.subheader('All bookings (every user, every status)')
    everything = db.list_bookings(conn)
    f1, f2 = st.columns(2)
    status_filter = f1.multiselect('Status', list(db.STATUSES), default=list(db.STATUSES), key='adm_status')
    room_filter = f2.multiselect('Room', [r['id'] for r in db.list_rooms(conn)], key='adm_room')
    shown = [b for b in everything if b['status'] in status_filter and (not room_filter or b['room_id'] in room_filter)]
    bookings_table(shown)
    st.download_button('Export all bookings (CSV)', db.export_bookings_csv(conn), file_name='bookings.csv', mime='text/csv')
    active = [b for b in everything if b['status'] not in db.INACTIVE]
    if active:
        labels = {b['id']: '%s - %s %s %s-%s (%s, %s)' % (b['id'], b['room_id'], b['date'], b['start'], b['end'], b['requester'], b['status']) for b in active}
        pick = st.selectbox('Cancel any booking', list(labels), format_func=labels.get, key='adm_cancel')
        if st.button('Cancel selected booking', key='adm_cancel_btn'):
            try:
                db.cancel_booking(conn, user, pick)
            except db.AppError as err:
                st.error(err.message)
            else:
                flash('success', 'Booking %s cancelled.' % pick)
                st.rerun()


def admin_rooms(conn, user):
    st.subheader('Add a new room / laboratory')
    with st.form('add_room', clear_on_submit=False):
        a1, a2 = st.columns(2)
        new_id = a1.text_input('Room ID (unique, e.g. LAB-305)')
        new_loc = a2.text_input('Location')
        a3, a4 = st.columns(2)
        new_cap = a3.number_input('Capacity', min_value=1, max_value=1000, value=30, step=1)
        new_type = a4.selectbox('Room type', list(db.ROOM_TYPES))
        new_eq = st.text_input('Equipment (comma separated)', placeholder='Projector, Smart Board')
        add = st.form_submit_button('Add room')
    if add:
        try:
            db.add_room(conn, {'id': new_id, 'location': new_loc, 'capacity': new_cap, 'type': new_type, 'equipment': new_eq})
        except db.AppError as err:
            st.error(err.message)
        else:
            flash('success', 'Room %s added.' % new_id.strip())
            st.rerun()

    st.subheader('Edit an existing room')
    rooms = {r['id']: r for r in db.list_rooms(conn)}
    rid = st.selectbox('Room to edit', list(rooms), key='edit_room_pick')
    r = rooms[rid]
    with st.form('edit_room_%s' % rid):
        e1, e2 = st.columns(2)
        loc = e1.text_input('Location', value=r['location'])
        cap = e2.number_input('Capacity', min_value=1, max_value=1000, value=int(r['capacity']), step=1)
        rtype = st.selectbox('Room type', list(db.ROOM_TYPES), index=db.ROOM_TYPES.index(r['type']))
        eq = st.text_input('Equipment (comma separated)', value=', '.join(r['equipment']))
        save = st.form_submit_button('Save changes')
    if save:
        try:
            db.update_room(conn, rid, {'location': loc, 'capacity': cap, 'type': rtype, 'equipment': eq})
        except db.AppError as err:
            st.error(err.message)
        else:
            flash('success', 'Room %s updated.' % rid)
            st.rerun()


def admin_emails(conn, user):
    st.subheader('Simulated e-mail notifications')
    st.caption('Nothing is really sent: every notification is stored as a row in the email_log table.')
    mails = db.list_emails(conn)
    if mails:
        st.dataframe(pd.DataFrame(mails).rename(columns={'time': 'Time', 'recipient': 'To', 'message': 'Message'}), hide_index=True)
        if st.button('Clear e-mail log'):
            db.clear_emails(conn)
            flash('success', 'E-mail log cleared.')
            st.rerun()
    else:
        st.info('The e-mail log is empty.')


def admin_settings(conn, user):
    st.subheader('Rules & configuration')
    cur = db.get_settings(conn)
    with st.form('settings_form'):
        s1, s2 = st.columns(2)
        open_t = s1.text_input('Opening time (HH:MM)', value=cur['open_time'])
        close_t = s2.text_input('Closing time (HH:MM)', value=cur['close_time'])
        s3, s4 = st.columns(2)
        max_h = s3.number_input('Longest single booking (hours)', min_value=1, max_value=24, value=int(cur['max_duration_hours']))
        max_w = s4.number_input('Longest weekly series (weeks)', min_value=1, max_value=52, value=int(cur['max_weeks']))
        tz = st.text_input('Time zone (IANA name, e.g. Asia/Karachi, Europe/London, UTC)', value=cur['timezone'])
        url = st.text_input('Public URL of this app (used inside the QR codes)', value=cur['public_url'])
        save = st.form_submit_button('Save settings')
    if save:
        try:
            db.set_settings(conn, {'open_time': open_t, 'close_time': close_t, 'max_duration_hours': max_h,
                                   'max_weeks': max_w, 'timezone': tz, 'public_url': url})
        except db.AppError as err:
            st.error(err.message)
        else:
            flash('success', 'Settings saved.')
            st.rerun()
    st.subheader('Demonstration data')
    st.caption('Restores the 10 demo rooms and the sample bookings (user accounts are kept). Registered bookings are deleted.')
    sure = st.checkbox('I understand that all current rooms and bookings will be replaced')
    if st.button('Reset demo data', disabled=not sure):
        db.seed_demo_data(conn)
        flash('success', 'Demo data restored.')
        st.rerun()


def page_admin(conn, user):
    st.header('Admin Control')
    if user['role'] != 'admin':                         # AUTHORIZATION check on the page itself
        st.error('Administrator privileges required.')
        return
    t1, t2, t3, t4 = st.tabs(['Bookings', 'Rooms', 'E-mail log', 'Settings & data'])
    with t1:
        admin_bookings(conn, user)
    with t2:
        admin_rooms(conn, user)
    with t3:
        admin_emails(conn, user)
    with t4:
        admin_settings(conn, user)


PAGE_FUNCS = {
    'Dashboard': page_dashboard, 'Rooms & Labs': page_rooms, 'Reserve Facility': page_reserve,
    'My Bookings': page_my_bookings, 'Timetable View': page_timetable, 'Live Display & QR': page_display,
    ADMIN_PAGE: page_admin,
}


# ================================================================ main
def main():
    if public_pages():                                  # QR-code / board pages need no login
        return
    with open_db() as conn:
        user = st.session_state.get('user')
        if user and not db.user_exists(conn, user['user_id']):     # e.g. the database file was deleted meanwhile
            st.session_state.clear()
            user = None
        if not user:
            login_screen(conn)
            return
        pages = USER_PAGES + ([ADMIN_PAGE] if user['role'] == 'admin' else [])
        with st.sidebar:
            st.markdown('### 🏫 Campus Booking')
            st.write('Signed in as **%s** (%s)' % (user['full_name'], user['role']))
            page = st.radio('Navigate', pages, key='page')
            st.caption('Server time: %s (%s)' % (db.get_now(conn).strftime('%a %d %b %H:%M'), db.get_setting(conn, 'timezone')))
            if st.button('Log out'):
                st.session_state.clear()
                st.rerun()
        show_flash()
        PAGE_FUNCS[page](conn, user)


main()
