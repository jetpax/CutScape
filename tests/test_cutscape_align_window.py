"""
The line-up window's contents, driven by their own buttons, where GTK is
available. The window itself isn't opened.
"""
import pytest

pytest.importorskip('inkex')
gi = pytest.importorskip('gi')
try:
    gi.require_version('Gtk', '3.0')
except ValueError:
    pytest.skip("No GTK", allow_module_level=True)
from gi.repository import Gtk  # noqa: E402

from cutscape import align, align_window, cutter, job, transports  # noqa: E402

MARKS = [(10.0, 190.0), (150.0, 190.0), (150.0, 10.0), (10.0, 10.0)]
HEIGHT = 200.0


@pytest.fixture
def ui(tmp_path, monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("No display")
    monkeypatch.setenv('CUTSCAPE_CONFIG', str(tmp_path / 'cutter.json'))
    monkeypatch.delenv('LANGUAGE', raising=False)

    def build(laser=None, problems=lambda: []):
        d = dict(protocol='hpgl', steps=(40.0, 40.0), swap_xy=False,
                 invert_x=False, invert_y=False, width=0.0, length=0.0,
                 options=dict(dmpl_mode=1))
        sent = []
        head = align.Head(d, job.Settings(), sent.append)
        session = align.Session(head, MARKS, align.base(d, HEIGHT), laser)
        closed = []
        window = align_window.Window(Gtk, session, problems,
                                     lambda: closed.append(True))
        return window, session, sent, closed
    return build


def aim_all(window, session):
    for mark in MARKS:
        session.head.move(mark[0] + 20, HEIGHT - mark[1] + 20)
        window.go.clicked()


def test_starts_on_the_first_mark(ui):
    window, session, sent, closed = ui()
    assert 'blade tip onto crop mark 1 (bottom left)' in \
        window.prompt.get_text()
    assert window.go.get_label() == 'On the mark'
    assert not window.back.get_sensitive()
    assert 'X 0.00 mm, Y 0.00 mm' in window.position.get_text()


def test_laser_is_named_once_calibrated(ui):
    window, session, sent, closed = ui(laser=(1.0, 2.0))
    assert 'laser onto crop mark 1' in window.prompt.get_text()


def test_arrows_move_by_the_chosen_step(ui):
    window, session, sent, closed = ui()
    window.arrows[2].clicked()
    assert sent == [b'PU40,0;']
    window.step = 0.1
    window.press('Up')
    assert sent[-1] == b'PU40,4;'
    assert 'X 1.00 mm, Y 0.10 mm' in window.position.get_text()


def test_arrow_at_the_origin_says_why(ui):
    window, session, sent, closed = ui()
    window.press('Left')
    assert "cutter's origin" in window.notice.get_text()
    window.press('Right')
    assert window.notice.get_text() == ''


def test_a_failed_move_is_shown(ui):
    window, session, sent, closed = ui()

    def fail(data):
        raise transports.TransportError("It isn't there")
    session.head.send = fail
    window.press('Right')
    assert "It isn't there" in window.notice.get_text()
    assert session.head.x == 0


def test_all_marks_then_cut(ui):
    window, session, sent, closed = ui()
    aim_all(window, session)
    assert 'All four marks found' in window.prompt.get_text()
    assert window.go.get_label() == 'Cut'
    assert window.go.get_sensitive()
    assert not any(a.get_sensitive() for a in window.arrows)
    window.press('Right')
    assert session.head.x == MARKS[3][0] + 20
    assert not closed
    window.go.clicked()
    assert window.cutting and closed


def test_back_from_the_result(ui):
    window, session, sent, closed = ui()
    aim_all(window, session)
    window.back.clicked()
    assert session.index == 3
    assert 'crop mark 4 (top left)' in window.prompt.get_text()
    assert all(a.get_sensitive() for a in window.arrows)


def test_a_problem_stops_the_cut(ui):
    window, session, sent, closed = ui(problems=lambda: ["Too far"])
    aim_all(window, session)
    assert not window.go.get_sensitive()
    assert 'Too far' in window.notice.get_text()


def test_cancel_closes_without_cutting(ui):
    window, session, sent, closed = ui()
    window.cancel.clicked()
    assert closed and not window.cutting


def test_turn_is_remembered(ui):
    window, session, sent, closed = ui()
    window.turn.clicked()
    assert session.turn == 1
    assert cutter.setting('arrow_turn') == 1


def test_calibration_saves_the_laser(ui):
    window, session, sent, closed = ui()
    session.head.move(50, 50)
    window.calibrate.clicked()
    assert 'scrap' in window.prompt.get_text()
    assert not window.calibrate.get_sensitive()
    #: Cancel only leaves calibration
    window.cancel.clicked()
    assert not closed and not session.calibrating
    window.calibrate.clicked()
    window.go.clicked()
    assert b'PD' in sent[-1]
    assert 'centre of the cross' in window.prompt.get_text()
    session.head.move(80, 38)
    window.go.clicked()
    assert session.laser == (-30, 12)
    assert cutter.setting('laser') == [-30, 12]
    assert 'laser onto crop mark 1' in window.prompt.get_text()


def test_calibration_needs_room(ui):
    window, session, sent, closed = ui()
    window.calibrate.clicked()
    window.go.clicked()
    assert 'at least 5 mm' in window.notice.get_text()
    assert session.calibrating == session.CUT_CROSS
    assert not any(b'PD' in data for data in sent)
