"""
Lining up with a printed sheet: the maths and the head, without a window.
"""
import math

import pytest

from cutscape import align, job

MARKS = [(10.0, 190.0), (150.0, 190.0), (150.0, 10.0), (10.0, 10.0)]
HEIGHT = 200.0


def device(**kwargs):
    d = dict(protocol='hpgl', steps=(40.0, 40.0), swap_xy=False,
             invert_x=False, invert_y=False, width=0.0, length=0.0,
             options=dict(dmpl_mode=1))
    d.update(kwargs)
    return d


def make(laser=None, turn=0, **kwargs):
    sent = []
    d = device(**kwargs)
    head = align.Head(d, job.Settings(), lambda data: sent.append(data))
    return (align.Session(head, MARKS, align.base(d, HEIGHT), laser, turn),
            sent)


# -----------------------------------------------------------------------------
# The fit
# -----------------------------------------------------------------------------
def sheet(p, angle=3.0, stretch=(1.01, 0.99), shift=(30.0, -12.0)):
    a = math.radians(angle)
    x, y = p[0] * stretch[0], p[1] * stretch[1]
    return (x * math.cos(a) - y * math.sin(a) + shift[0],
            x * math.sin(a) + y * math.cos(a) + shift[1])


def test_fit_finds_how_the_sheet_lies():
    src = [(0.0, 0.0), (140.0, 0.0), (140.0, 180.0), (0.0, 180.0)]
    fit = align.Fit(src, [sheet(p) for p in src])
    assert fit.rotation == pytest.approx(3.0)
    assert fit.stretch[0] == pytest.approx(1.01)
    assert fit.stretch[1] == pytest.approx(0.99)
    assert fit.worst == pytest.approx(0, abs=1e-9)
    assert fit(70, 90) == pytest.approx(sheet((70, 90)))


def test_fit_shows_a_mark_that_disagrees():
    src = [(0.0, 0.0), (140.0, 0.0), (140.0, 180.0), (0.0, 180.0)]
    dst = [sheet(p) for p in src]
    dst[2] = dst[2][0] + 8, dst[2][1]
    assert align.Fit(src, dst).worst > align.MAX_ERROR


def test_fit_one_mark_is_a_shift():
    fit = align.Fit([(1.0, 2.0)], [(11.0, 22.0)])
    assert fit(5, 5) == pytest.approx((15, 25))
    assert fit.rotation == 0


def test_fit_two_marks_turn_evenly():
    src = [(0.0, 0.0), (100.0, 0.0)]
    fit = align.Fit(src, [sheet(p, stretch=(1, 1)) for p in src])
    assert fit.rotation == pytest.approx(3)
    assert fit(0, 50) == pytest.approx(sheet((0, 50), stretch=(1, 1)))


def test_fit_marks_in_a_line():
    src = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)]
    with pytest.raises(ValueError):
        align.Fit(src, src)


def test_base_follows_the_preset():
    assert align.base(device(), HEIGHT)(10, 190) == (10, 10)
    assert align.base(device(swap_xy=True), HEIGHT)(10, 150) == (50, 10)
    assert align.base(device(invert_x=True, invert_y=True),
                      HEIGHT)(10, 190) == (-10, -10)


# -----------------------------------------------------------------------------
# The head
# -----------------------------------------------------------------------------
def test_start_goes_to_the_origin():
    s, sent = make()
    s.head.x = 5.0
    s.head.start()
    assert sent == [b'IN;SP1;PU0,0;']
    assert (s.head.x, s.head.y) == (0, 0)


def test_moves_are_pen_up_in_steps():
    s, sent = make()
    assert s.head.move(12.5, 0.1) is True
    assert sent == [b'PU500,4;']
    assert (s.head.x, s.head.y) == (12.5, 0.1)


def test_head_stops_at_the_origin():
    """ A cutter may not go past it, and then the head wouldn't be where
    CutScape thinks """
    s, sent = make()
    s.head.move(5, 5)
    assert s.head.move(-1, 7) is False
    assert sent[-1] == b'PU0,280;'
    assert (s.head.x, s.head.y) == (0, 7)


def test_head_is_not_moved_by_a_failed_send():
    s, sent = make()
    s.head.move(5, 5)

    def fail(data):
        raise OSError("unplugged")
    s.head.send = fail
    with pytest.raises(OSError):
        s.head.move(9, 9)
    assert (s.head.x, s.head.y) == (5, 5)


@pytest.mark.parametrize('protocol, up', [
    ('dmpl', b' U200,200 '), ('gpgl', b'M100,100\x03'),
    ('camm-gl1', b'M200,200;')])
def test_head_speaks_the_cutters_language(protocol, up):
    sent = []
    steps = {'gpgl': 20.0}.get(protocol, 40.0)
    head = align.Head(device(protocol=protocol, steps=(steps, steps)),
                      job.Settings(), sent.append)
    head.move(5, 5)
    assert sent == [up]


def test_finish_goes_home_without_cutting():
    s, sent = make()
    s.head.move(5, 5)
    s.head.finish()
    assert sent[-1] == b'PU0,0;IN;'
    assert (s.head.x, s.head.y) == (0, 0)


def test_cross_is_centred_on_the_head():
    s, sent = make()
    s.head.move(20, 30)
    s.head.cross()
    assert sent[-1] == (b'PU600,1200;PD1000,1200;'
                        b'PU800,1000;PD800,1400;PU800,1200;')
    assert (s.head.x, s.head.y) == (20, 30)


def test_cross_needs_room():
    s, sent = make()
    s.head.move(2, 30)
    with pytest.raises(ValueError):
        s.head.cross()


# -----------------------------------------------------------------------------
# Lining up
# -----------------------------------------------------------------------------
@pytest.mark.parametrize('turn, key, moved', [
    (0, (1, 0), (51, 50)), (0, (0, 1), (50, 51)),
    (1, (1, 0), (50, 51)), (1, (0, 1), (49, 50)),
    (2, (1, 0), (49, 50)), (3, (1, 0), (50, 49))])
def test_arrows_turn(turn, key, moved):
    s, sent = make(turn=turn)
    s.head.move(50, 50)
    s.arrow(key[0], key[1], 1.0)
    assert (s.head.x, s.head.y) == moved


def test_arrow_at_the_origin_says_so():
    s, sent = make()
    assert s.arrow(-1, 0, 1.0) is False
    assert s.arrow(1, 0, 1.0) is True


def lie(p):
    """ A sheet shifted and turned a little, in head space """
    return sheet((p[0], HEIGHT - p[1]), angle=1.0, stretch=(1.0, 1.0),
                 shift=(25.0, 15.0))


def test_each_mark_brings_the_head_nearer_the_next():
    s, sent = make()
    s.head.move(*lie(MARKS[0]))
    s.on_mark()
    #: With one mark, only the shift is known
    assert not s.done
    assert s.head.x == pytest.approx(lie(MARKS[0])[0] + 140)
    s.head.move(*lie(MARKS[1]))
    s.on_mark()
    #: With two, the turn is too, so it lands on the third
    assert (s.head.x, s.head.y) == pytest.approx(lie(MARKS[2]))
    s.head.move(*lie(MARKS[2]))
    s.on_mark()
    assert (s.head.x, s.head.y) == pytest.approx(lie(MARKS[3]))
    s.head.move(*lie(MARKS[3]))
    s.on_mark()
    assert s.done
    assert s.fit().rotation == pytest.approx(1.0)
    assert s.fit().worst == pytest.approx(0, abs=1e-9)
    assert not s.warnings()
    assert s.to_head()(80, 100) == pytest.approx(lie((80, 100)))


def test_laser_is_aimed_and_the_tool_goes_there():
    laser = (-30.0, 12.0)
    s, sent = make(laser=laser)
    for mark in MARKS:
        x, y = lie(mark)
        #: The head with the laser on the mark isn't where the tool would be
        s.head.move(x - laser[0], y - laser[1])
        s.on_mark()
    assert s.to_head()(80, 100) == pytest.approx(lie((80, 100)))


def test_back_returns_to_the_last_mark():
    s, sent = make()
    s.head.move(30, 40)
    s.on_mark()
    assert s.index == 1
    s.back()
    assert s.index == 0
    assert (s.head.x, s.head.y) == (30, 40)
    s.back()
    assert s.index == 0


def test_warnings():
    s, sent = make()
    for mark in MARKS:
        x, y = mark[0] * 1.05, (HEIGHT - mark[1]) * 1.05
        s.head.move(x + 30, y + 30)
        s.on_mark()
    assert any('100%' in w for w in s.warnings())
    s, sent = make()
    for i, mark in enumerate(MARKS):
        x, y = lie(mark)
        s.head.move(x + (8 if i == 2 else 0), y)
        s.on_mark()
    assert any('aimed' in w for w in s.warnings())


def test_calibration_finds_the_laser():
    s, sent = make()
    s.head.move(30, 30)
    s.on_mark()
    s.head.move(100, 100)
    s.begin_calibration()
    assert s.calibrating == s.CUT_CROSS
    s.cut_cross()
    assert b'PD' in sent[-1]
    assert s.calibrating == s.AIM_AT_CROSS
    #: The laser reached the cross with the head 30 mm along and 12 back
    s.head.move(130, 88)
    assert s.laser_on_cross() == (-30, 12)
    assert s.laser == (-30, 12)
    assert not s.calibrating
    #: The mark found with no laser no longer counts
    assert s.index == 0


def test_a_double_click_is_not_a_calibration():
    """ The second click of "Cut the cross" lands on the next button """
    s, sent = make(laser=(1.0, 2.0))
    s.head.move(100, 100)
    s.begin_calibration()
    s.cut_cross()
    with pytest.raises(ValueError):
        s.laser_on_cross()
    assert s.calibrating == s.AIM_AT_CROSS
    assert s.laser == (1, 2)


def test_calibrating_with_no_sheet():
    head = align.Head(device(), job.Settings(), lambda data: None)
    s = align.Session.for_calibration(head, turn=1)
    assert s.calibrating == s.CUT_CROSS
    assert s.turn == 1
    assert not s.done
    head.move(50, 50)
    s.cut_cross()
    head.move(20, 62)
    assert s.laser_on_cross() == (30, -12)
    assert not s.calibrating
    assert not s.done


def test_calibration_can_be_cancelled():
    s, sent = make(laser=(1.0, 2.0))
    s.begin_calibration()
    s.cancel_calibration()
    assert not s.calibrating
    assert s.laser == (1, 2)


# -----------------------------------------------------------------------------
# The plan, and what can't be lined up
# -----------------------------------------------------------------------------
def square(x=20.0, y=20.0, size=10.0):
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]


def test_plan_on_sheet_goes_where_it_is_told():
    s = job.Settings(overcut=0.0)
    plan = job.plan_on_sheet([square()], lambda x, y: (x + 5, y + 7), s)
    assert plan.on_sheet
    assert plan.bbox == (25, 27, 35, 37)
    assert plan.park == (0, 0)
    assert not plan.problems()
    data = job.encode(plan, s)
    assert data.startswith(b'IN;SP1;PU')
    assert data.endswith(b'PU0,0;IN;')
    #: 40 steps/mm; wherever the order filter starts the loop
    assert b'1000,1080' in data and b'1400,1480' in data


def test_plan_on_sheet_ignores_the_layout_options():
    s = job.Settings(overcut=0.0, mirror=True, rotate=True, copies=3,
                     weedline=True, margin_x=9.0, prefeed=True, feed=50.0)
    plan = job.plan_on_sheet([square()], lambda x, y: (x, y), s)
    assert plan.bbox == (20, 20, 30, 30)
    assert len(plan.paths) == 1
    assert plan.prefeed == []
    assert plan.park == (0, 0)


def test_plan_on_sheet_keeps_the_cut_settings():
    s = job.Settings(overcut=2.0, passes=2)
    plan = job.plan_on_sheet([square()], lambda x, y: (x, y), s)
    plain = job.Settings(overcut=0.0)
    assert plan.cut_length > 2 * job.plan_on_sheet(
        [square()], lambda x, y: (x, y), plain).cut_length


def test_plan_on_sheet_has_already_allowed_for_the_axes():
    """ The fit went from the page to the head; encode mustn't swap again """
    s = job.Settings(overcut=0.0, swap_xy=True, invert_y=True)
    plan = job.plan_on_sheet([square()], lambda x, y: (x, y), s)
    assert not plan.device['swap_xy'] and not plan.device['invert_y']
    data = job.encode(plan, s)
    assert b'800,800' in data and b'1200,1200' in data


def test_plan_on_sheet_past_the_origin():
    s = job.Settings(overcut=0.0)
    plan = job.plan_on_sheet([square()], lambda x, y: (x - 25, y), s)
    assert any("cutter's panel" in p for p in plan.problems())


def test_plan_on_sheet_too_wide():
    s = job.Settings(overcut=0.0, width=100.0)
    plan = job.plan_on_sheet([square()], lambda x, y: (x + 200, y), s)
    assert any('only 100.0 mm' in p for p in plan.problems())


def test_plan_on_sheet_swaps_the_limits_with_the_axes():
    s = job.Settings(overcut=0.0, swap_xy=True, width=100.0, length=1000.0)
    plan = job.plan_on_sheet([square()], lambda x, y: (x + 200, y), s)
    assert not plan.problems()


def test_nothing_to_cut():
    with pytest.raises(job.NoPathsError):
        job.plan_on_sheet([], lambda x, y: (x, y), job.Settings())


def test_needs_a_direct_connection():
    align.check(device(), 'device')
    align.check(device(), 'serial')
    for transport in ('printer', 'file'):
        with pytest.raises(ValueError):
            align.check(device(), transport)


def test_not_for_gcode_yet():
    """ Opening the port restarts GRBL, which would lose the position """
    with pytest.raises(ValueError):
        align.check(device(protocol='gcode'), 'serial')
