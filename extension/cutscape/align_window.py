"""
The window for lining up with a printed sheet: arrows that move the head,
and a button for each step. The work is in align.Session; this shows it.
"""
from . import align, cutter, marks, notify, transports
from .i18n import _

#: mm the arrows move the head, and which is chosen to start with
STEPS = (50.0, 10.0, 1.0, 0.1)
STEP = 1.0

#: (right, up) for each arrow key
KEYS = dict(Right=(1, 0), Left=(-1, 0), Up=(0, 1), Down=(0, -1))


def _LASER_ON():
    return _("If you can't see the dot, switch the laser on at the cutter: "
             "usually a Laser button on its panel.")


class Window(object):
    """ The window's contents, in self.content, kept apart from the window
    itself so they can be driven without one
    """
    def __init__(self, Gtk, session, problems, close=None):
        self.Gtk = Gtk
        self.session = session
        #: Returns why the sheet, once lined up, can't be cut
        self.problems = problems
        self.close = close or (lambda: None)
        self.step = STEP
        self.cutting = False
        self.content = self._build()
        self.refresh()

    def _build(self):
        Gtk = self.Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(box, 'set_margin_' + side)(16)

        self.prompt = Gtk.Label(wrap=True, xalign=0)
        self.prompt.set_max_width_chars(56)
        box.pack_start(self.prompt, False, False, 0)

        grid = Gtk.Grid(row_spacing=4, column_spacing=4,
                        halign=Gtk.Align.CENTER)
        self.arrows = []
        for label, key, column, row in (('↑', 'Up', 1, 0), ('←', 'Left', 0, 1),
                                        ('→', 'Right', 2, 1),
                                        ('↓', 'Down', 1, 2)):
            arrow = Gtk.Button(label=label)
            arrow.set_size_request(52, 52)
            arrow.connect('clicked', lambda _b, k=key: self.press(k))
            grid.attach(arrow, column, row, 1, 1)
            self.arrows.append(arrow)
        box.pack_start(grid, False, False, 0)

        steps = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        steps.pack_start(Gtk.Label(label=_("Each arrow moves:")),
                         False, False, 0)
        group = None
        for step in STEPS:
            radio = Gtk.RadioButton.new_with_label_from_widget(
                group, '%g mm' % step)
            group = group or radio
            radio.set_active(step == STEP)
            radio.connect('toggled', self._on_step, step)
            steps.pack_start(radio, False, False, 0)
        box.pack_start(steps, False, False, 0)

        self.position = Gtk.Label()
        box.pack_start(self.position, False, False, 0)

        self.notice = Gtk.Label(wrap=True, xalign=0)
        self.notice.set_max_width_chars(56)
        box.pack_start(self.notice, False, False, 0)

        row = Gtk.Box(spacing=8)
        self.back = Gtk.Button(label=_("Back"))
        self.back.connect('clicked', lambda _b: self._do(self.session.back))
        row.pack_start(self.back, False, False, 0)
        self.calibrate = Gtk.Button(label=_("Calibrate laser…"))
        self.calibrate.connect(
            'clicked', lambda _b: self._do(self.session.begin_calibration))
        row.pack_start(self.calibrate, False, False, 0)
        self.turn = Gtk.Button(label=_("Turn arrows"))
        self.turn.set_tooltip_text(
            _("If the head doesn't go the way the arrow points"))
        self.turn.connect('clicked', self._on_turn)
        row.pack_start(self.turn, False, False, 0)
        self.go = Gtk.Button()
        self.go.connect('clicked', self._on_go)
        row.pack_end(self.go, False, False, 0)
        self.cancel = Gtk.Button(label=_("Cancel"))
        self.cancel.connect('clicked', self._on_cancel)
        row.pack_end(self.cancel, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def _do(self, action, *args):
        """ Do something that may reach the cutter, saying so if it fails.
        Returns what it returned, or None.
        """
        self.notice.set_text('')
        result = None
        try:
            result = action(*args)
        except (transports.TransportError, ValueError) as e:
            self.notice.set_text(str(e))
        self.refresh()
        return result

    def press(self, key):
        """ Move the head for an arrow, on screen or on the keyboard """
        s = self.session
        if s.done and not s.calibrating:
            return
        right, up = KEYS[key]
        moved = self._do(s.arrow, right, up, self.step)
        if moved is False:
            self.notice.set_text(_(
                "That's as far as the cutter's origin. If the mark is further "
                "than that, move the origin back on the cutter's panel and "
                "start again."))

    def _on_step(self, radio, step):
        if radio.get_active():
            self.step = step

    def _on_turn(self, _button):
        s = self.session
        s.turn = (s.turn + 1) % 4
        cutter.remember('arrow_turn', s.turn)

    def _on_cancel(self, _button):
        if self.session.calibrating and self.session.expected:
            self._do(self.session.cancel_calibration)
        else:
            self.close()

    def _on_go(self, _button):
        s = self.session
        if s.calibrating == s.CUT_CROSS:
            self._do(s.cut_cross)
        elif s.calibrating == s.AIM_AT_CROSS:
            laser = self._do(s.laser_on_cross)
            if laser:
                cutter.remember('laser', list(laser))
                if not s.expected:
                    self.close()
        elif s.done:
            self.cutting = True
            self.close()
        else:
            self._do(s.on_mark)

    def refresh(self):
        s = self.session
        self.position.set_text(
            _("Head at X {x:.2f} mm, Y {y:.2f} mm").format(x=s.head.x,
                                                           y=s.head.y))
        calibrating = bool(s.calibrating)
        ready = s.done and not calibrating
        for arrow in self.arrows:
            arrow.set_sensitive(not ready)
        self.back.set_sensitive(bool(s.found) and not calibrating)
        self.calibrate.set_sensitive(not calibrating)
        self.go.set_sensitive(True)
        if s.calibrating == s.CUT_CROSS:
            self.prompt.set_text(_(
                "Put scrap material in the cutter and move the head over it, "
                "at least {margin:.0f} mm from the origin. CutScape will cut "
                "a {size:.0f} mm cross there, so don't leave your printed "
                "sheet under it.").format(margin=align.CROSS / 2,
                                          size=align.CROSS))
            self.go.set_label(_("Cut the cross"))
        elif s.calibrating == s.AIM_AT_CROSS:
            self.prompt.set_text(' '.join((_(
                "Move the laser onto the centre of the cross you just cut."),
                _LASER_ON())))
            self.go.set_label(_("Laser is on the cross"))
        elif ready:
            self._show_result()
        elif s.expected:
            number, corner = s.number, marks.corners()[s.number - 1]
            if s.laser:
                text = _("Move the laser onto crop mark {number} ({corner}).")
            else:
                text = _("Move the blade tip onto crop mark {number} "
                         "({corner}).")
            text = text.format(number=number, corner=corner)
            if s.laser and not s.index:
                text += ' ' + _LASER_ON()
            if s.assumed:
                text += '\n\n' + _("This uses your model's usual laser "
                                    "offset. Calibrate it for a closer cut.")
            self.prompt.set_text(text)
            self.go.set_label(_("On the mark"))

    def _show_result(self):
        s = self.session
        fit = s.fit()
        self.prompt.set_text(_(
            "All four marks found. The sheet is turned {angle:.2f}° and "
            "stretched {x:+.2f}% and {y:+.2f}%; the marks agree to within "
            "{error:.2f} mm.").format(
                angle=fit.rotation, x=(fit.stretch[0] - 1) * 100,
                y=(fit.stretch[1] - 1) * 100, error=fit.worst))
        self.go.set_label(_("Cut"))
        problems = self.problems()
        self.go.set_sensitive(not problems)
        self.notice.set_text('\n'.join(problems + s.warnings()))


def run(session, problems):
    """ Take the head to the origin and line up. Returns whether the user
    went on to cut; if not, the head is back at the origin.
    """
    Gtk = notify.gtk()
    if Gtk is None:
        raise ValueError(_("Lining up needs CutScape's window, and GTK isn't "
                           "available here."))
    from gi.repository import Gdk
    session.head.start()
    win = Gtk.Window(title=_("Line up with the printed sheet"))
    win.set_keep_above(True)
    win.set_position(Gtk.WindowPosition.CENTER)
    window = Window(Gtk, session, problems, Gtk.main_quit)
    win.add(window.content)

    def on_key(_win, event):
        key = Gdk.keyval_name(event.keyval)
        if key not in KEYS:
            return False
        #: Before a focused button or radio button gets to use it
        window.press(key)
        return True
    win.connect('key-press-event', on_key)
    win.connect('delete-event', lambda *_args: Gtk.main_quit())
    win.show_all()
    win.present()
    Gtk.main()
    win.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    if not window.cutting:
        try:
            session.head.finish()
        except transports.TransportError:
            pass
    return window.cutting
