"""
Show the user a message in a small window of CutScape's own.

Inkscape shows anything an extension writes to stderr with the warning
"Inkscape has received additional data from the script executed ... this
may indicate the results will not be as expected", even when it's the
result the user asked for. show-stderr in the .inx doesn't change that.
So reports and confirmations go in a GTK window (PyGObject ships with
Inkscape for its Extension Manager); without GTK, or with
CUTSCAPE_STDERR=1 set, show() returns False and the caller falls back to
stderr.
"""
import os

INFO, WARNING = 'info', 'warning'


def gtk():
    if os.environ.get('CUTSCAPE_STDERR'):
        return None
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
    except (ImportError, ValueError):
        return None
    #: No display (a batch run, CI): don't try
    if not Gtk.init_check()[0]:
        return None
    return Gtk


def show(text, title='CutScape', kind=INFO, report=False):
    """ Show text and wait for OK. report=True uses a fixed-width,
    scrollable text box for listings and commands to copy.
    Returns False if no window could be shown.
    """
    Gtk = gtk()
    if Gtk is None:
        return False
    dialog = Gtk.Dialog(title=title)
    dialog.add_button('OK', Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_keep_above(True)
    dialog.set_position(Gtk.WindowPosition.CENTER)

    box = dialog.get_content_area()
    box.set_spacing(12)
    for side in ('top', 'bottom', 'start', 'end'):
        getattr(box, 'set_margin_' + side)(16)

    lines = text.count('\n') + 1
    if report:
        view = Gtk.TextView(editable=False, cursor_visible=False,
                            monospace=True, left_margin=8, right_margin=8,
                            top_margin=8, bottom_margin=8,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(text)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(720, min(480, 40 + 18 * lines))
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)
    else:
        icon = 'dialog-warning' if kind == WARNING else 'dialog-information'
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG),
                       False, False, 0)
        label = Gtk.Label(label=text, selectable=True, wrap=True, xalign=0)
        label.set_max_width_chars(60)
        row.pack_start(label, True, True, 0)
        box.pack_start(row, True, True, 0)

    dialog.show_all()
    dialog.present()
    dialog.run()
    dialog.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    return True
