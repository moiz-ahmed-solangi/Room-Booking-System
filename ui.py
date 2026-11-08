"""
ui.py - builds the HTML fragments shown by the Streamlit front end (cards, badges, timetable, public display)
and the QR-code image. Pure Python: no Streamlit import, so it can be unit-tested on its own.

SECURITY: every value that comes from the database or from a user (purpose, names, locations ...) goes through
esc() before it is placed inside HTML. Without this a purpose like <script>...</script> would run in the browser (XSS).
"""
import html
import io
import math

FONT = "font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;"

# status -> (background, border/accent, text colour)
STATUS_STYLE = {
    'Approved':         ('#dcfce7', '#16a34a', '#14532d'),
    'Pending Approval': ('#fef3c7', '#d97706', '#78350f'),
    'Rejected':         ('#fee2e2', '#dc2626', '#7f1d1d'),
    'Cancelled':        ('#e2e8f0', '#64748b', '#334155'),
}


def esc(value):
    """HTML-escape any value (the one function that makes user text safe to display)."""
    return html.escape(str(value), quote=True)


def badge(text, bg='#e0e7ff', fg='#3730a3'):
    return ('<span style="display:inline-block;background:%s;color:%s;padding:2px 10px;border-radius:999px;'
            'font-size:12px;font-weight:600;margin:0 4px 4px 0;">%s</span>' % (bg, fg, esc(text)))


def status_badge(status):
    bg, _, fg = STATUS_STYLE.get(status, STATUS_STYLE['Cancelled'])
    return badge(status, bg, fg)


def room_card_html(room, busy=None):
    """Card for one room. busy=None -> no availability info; True/False -> BUSY / AVAILABLE badge."""
    avail = ''
    if busy is True:
        avail = badge('BUSY', '#fee2e2', '#991b1b')
    elif busy is False:
        avail = badge('AVAILABLE', '#dcfce7', '#166534')
    chips = ''.join(badge(e, '#f1f5f9', '#334155') for e in room['equipment']) or '<span style="color:#94a3b8;font-size:12px;">No equipment listed</span>'
    return (
        '<div style="%sborder:1px solid #e2e8f0;border-radius:14px;padding:14px 16px;background:#ffffff;color:#0f172a;'
        'margin-bottom:12px;box-shadow:0 1px 2px rgba(15,23,42,.06);">'
        '<div style="display:flex;justify-content:space-between;align-items:center;">'
        '<div style="font-size:18px;font-weight:800;">%s</div><div>%s</div></div>'
        '<div style="margin:6px 0 8px 0;">%s</div>'
        '<div style="font-size:13px;color:#475569;">&#128205; %s</div>'
        '<div style="font-size:13px;color:#475569;margin-bottom:8px;">&#128101; Capacity: <b>%d</b></div>'
        '<div>%s</div></div>'
    ) % (FONT, esc(room['id']), avail, badge(room['type']), esc(room['location']), room['capacity'], chips)


def _min(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def hour_bounds(open_time, close_time):
    """Timetable range in whole hours, e.g. ('07:00','22:30') -> (7, 23)."""
    return int(open_time[:2]), min(24, math.ceil(_min(close_time) / 60))


def timetable_html(rows, start_hour=7, end_hour=22):
    """Timetable grid. rows = [(label, sub_label, [booking dicts])]; one horizontal lane per row.
    Used for the DAILY view (one lane per room) and the WEEKLY view (one lane per day)."""
    start_min, span = start_hour * 60, (end_hour - start_hour) * 60
    hours = end_hour - start_hour
    pct = lambda m: max(0.0, min(100.0, (m - start_min) * 100.0 / span))
    grid = 'background-image:linear-gradient(to right,#e2e8f0 1px,transparent 1px);background-size:%.4f%% 100%%;' % (100.0 / hours)

    head = ''.join('<div style="position:absolute;left:%.3f%%;top:6px;font-size:11px;color:#64748b;padding-left:3px;">%02d</div>'
                   % (pct(h * 60), h) for h in range(start_hour, end_hour))
    out = ['<div style="%soverflow-x:auto;border:1px solid #e2e8f0;border-radius:12px;background:#ffffff;color:#0f172a;">'
           '<div style="min-width:880px;">' % FONT,
           '<div style="display:flex;border-bottom:1px solid #e2e8f0;background:#f8fafc;">'
           '<div style="width:150px;flex-shrink:0;padding:6px 12px;font-size:11px;font-weight:700;color:#64748b;">ROW / HOUR</div>'
           '<div style="flex:1;position:relative;height:26px;">%s</div></div>' % head]
    for label, sub, bookings in rows:
        blocks = []
        for b in bookings:
            if b['status'] in ('Rejected', 'Cancelled'):
                continue
            left, right = pct(_min(b['start'])), pct(_min(b['end']))
            bg, accent, fg = STATUS_STYLE[b['status']]
            tip = '%s | %s-%s | %s | %s | %s' % (b['id'], b['start'], b['end'], b['purpose'], b['requester'], b['status'])
            blocks.append(
                '<div title="%s" style="position:absolute;left:%.3f%%;width:%.3f%%;top:5px;bottom:5px;background:%s;'
                'border-left:4px solid %s;color:%s;border-radius:6px;padding:2px 6px;font-size:11px;font-weight:600;'
                'overflow:hidden;white-space:nowrap;text-overflow:ellipsis;box-sizing:border-box;">%s<br>'
                '<span style="font-weight:400;">%s-%s</span></div>'
                % (esc(tip), left, max(right - left, 0.6), bg, accent, fg, esc(b['purpose']), esc(b['start']), esc(b['end'])))
        out.append(
            '<div style="display:flex;border-bottom:1px solid #f1f5f9;">'
            '<div style="width:150px;flex-shrink:0;padding:8px 12px;font-size:13px;font-weight:700;">%s'
            '<div style="font-size:11px;font-weight:400;color:#64748b;">%s</div></div>'
            '<div style="flex:1;position:relative;height:52px;%s">%s</div></div>' % (esc(label), esc(sub), grid, ''.join(blocks)))
    out.append('</div></div>')
    out.append('<div style="%sfont-size:12px;color:#475569;margin-top:6px;">'
               '<span style="color:#16a34a;">&#9632;</span> Approved &nbsp; <span style="color:#d97706;">&#9632;</span> Pending approval '
               '&nbsp; (cancelled / rejected bookings do not block a room and are hidden)</div>' % FONT)
    return ''.join(out)


def kiosk_html(room, status):
    """Big public 'outside the room' display. Shows purposes only - never the requester's name (privacy)."""
    free = status['state'] == 'Free'
    bg, fg = ('#16a34a', '#ffffff') if free else ('#dc2626', '#ffffff')
    cur = status['current']
    now_line = ('Free now' if free else 'Occupied until %s' % cur['end'])
    detail = '' if free else '<div style="font-size:20px;margin-top:6px;">%s</div>' % esc(cur['purpose'])
    nxt = ''.join('<div style="padding:6px 0;border-bottom:1px solid #e2e8f0;">%s-%s &nbsp; <b>%s</b> %s</div>'
                  % (esc(b['start']), esc(b['end']), esc(b['purpose']), status_badge(b['status'])) for b in status['upcoming'][:5])
    nxt = nxt or '<div style="color:#64748b;padding:6px 0;">No more bookings today.</div>'
    return (
        '<div style="%smax-width:720px;margin:auto;border-radius:20px;overflow:hidden;border:1px solid #e2e8f0;background:#ffffff;color:#0f172a;">'
        '<div style="background:%s;color:%s;padding:28px 32px;">'
        '<div style="font-size:15px;opacity:.9;">%s &middot; %s &middot; capacity %d</div>'
        '<div style="font-size:54px;font-weight:900;line-height:1.1;">%s</div>'
        '<div style="font-size:30px;font-weight:700;">%s</div>%s</div>'
        '<div style="padding:18px 32px;"><div style="font-weight:800;margin-bottom:4px;">Later today</div>%s'
        '<div style="font-size:12px;color:#64748b;margin-top:10px;">Updated %s</div></div></div>'
    ) % (FONT, bg, fg, esc(room['type']), esc(room['location']), room['capacity'], esc(room['id']),
         now_line, detail, nxt, esc(status['now'].strftime('%A %d %B %Y, %H:%M')))


def board_row_html(room, status):
    """One compact line of the all-rooms public board."""
    free = status['state'] == 'Free'
    chip = badge('FREE', '#dcfce7', '#166534') if free else badge('OCCUPIED until ' + status['current']['end'], '#fee2e2', '#991b1b')
    nxt = status['upcoming'][0] if status['upcoming'] else None
    nxt_txt = 'Next: %s %s' % (esc(nxt['start']), esc(nxt['purpose'])) if nxt else 'Nothing more today'
    return ('<div style="%sdisplay:flex;align-items:center;gap:14px;padding:10px 14px;border:1px solid #e2e8f0;border-radius:12px;'
            'background:#ffffff;color:#0f172a;margin-bottom:8px;"><div style="width:90px;font-weight:800;">%s</div>'
            '<div style="width:230px;">%s</div><div style="flex:1;font-size:13px;color:#475569;">%s &middot; %s</div></div>'
            % (FONT, esc(room['id']), chip, esc(room['type']), nxt_txt))


def qr_png(text, scale=8):
    """PNG bytes of a QR code for `text`, or None if the optional 'segno' package is not installed."""
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error='m').save(buf, kind='png', scale=scale, border=2)
    return buf.getvalue()
