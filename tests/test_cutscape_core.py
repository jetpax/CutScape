"""
CutScape core: plain Python, no inkex or Qt needed.
"""
import glob
import importlib.util
import math
import re
import xml.etree.ElementTree as ET

import pytest

from cutscape import cutter, devices, filters, geometry, job, protocols

SQUARE = [(0, 0), (50, 0), (50, 50), (0, 50), (0, 0)]
HOLE = [(10, 10), (20, 10), (20, 20), (10, 20), (10, 10)]


# -----------------------------------------------------------------------------
# Protocols
# -----------------------------------------------------------------------------
def test_hpgl():
    p = protocols.get('hpgl', speed=10, force=80)
    out = p.encode([[(0, 0), (40.4, 0), (40.6, 0), (80, 40)]], (0, 0))
    #: Rounded, with repeated points dropped
    assert out == b"IN;SP1;VS10;FS80;PU0,0;PD40,0;PD41,0;PD80,40;PU0,0;IN;"


def test_hpgl_options():
    p = protocols.get('hpgl', pen=0, separate_z=True, newline=True)
    assert p.encode([[(1, 2), (3, 4)]], (0, 0)) == \
        b"IN;\nPU;\nPA1,2;\nPD;\nPA3,4;\nPU;\nPA0,0;\nIN;\n"


@pytest.mark.parametrize('mode, expected', [
    (1, b";:HAEC1BP80 V10  U0,0  D10,0  U0,0 "),
    (2, b" ;:ECN A L0 BP80 V10  U0,0  D10,0  U0,0 "),
    (3, b" ;:H A L0 BP80 V10  U0,0  D10,0  U0,0 "),
    (6, b"IN;PA;BP80 V10 PU0,0;PD10,0;PU0,0;"),
    (7, b";:H A L0 ECN U BP80 V10  U0,0  D10,0  U0,0  U F @ "),
])
def test_dmpl(mode, expected):
    p = protocols.get('dmpl', speed=10, force=80, mode=mode)
    assert p.encode([[(0, 0), (10, 0)]], (0, 0)) == expected


def test_dmpl_units():
    assert protocols.DMPL.default_steps(mode=1) == pytest.approx(1000 / 25.4)
    assert protocols.DMPL.default_steps(mode=2) == 40
    assert protocols.DMPL.default_steps(mode=7) == 40


def test_gpgl_terminates_commands():
    p = protocols.get('gpgl', speed=10, force=20)
    assert p.encode([[(0, 0), (10, 5)]], (0, 0)) == \
        b"H\x03!10\x03FX20,1\x03M0,0\x03D10,5\x03M0,0\x03"


def test_gcode():
    p = protocols.get('gcode', speed=2, z_up=5, z_down=-1)
    #: The plunge needs a feed rate too: GRBL refuses a G1 without one
    assert p.encode([[(0, 0), (10.25, 3)], [(20, 0), (25, 0)]], (0, 0)) == (
        b"G21\nG90\nG00 Z5\nG00 X0 Y0\nG01 Z-1 F1200\nG01 X10.25 Y3 F1200\n"
        b"G00 Z5\nG00 X20 Y0\nG01 Z-1 F1200\nG01 X25 Y0 F1200\n"
        b"G00 Z5\nG00 X0 Y0\nM2\n")


def test_gcode_without_z():
    """ The same height for both: G0/G1 alone mean up and down """
    p = protocols.get('gcode', speed=1, z_up=0, z_down=0)
    assert p.encode([[(0, 0), (1, 2)]], (0, 0)) == (
        b"G21\nG90\nG00 X0 Y0\nG01 X1 Y2 F600\nG00 X0 Y0\nM2\n")


def test_3018_preset():
    s = job.Settings(preset='CNC 3018 (GRBL)', speed=2)
    plan = job.plan([page(SQUARE)], 100, s)
    assert not plan.problems()
    out = job.encode(plan, s).decode()
    #: Millimetres, lifted to the default 5 mm and cut at 0
    assert out.startswith('G21\nG90\nG00 Z5\nG00 X0 Y0\nG01 Z0 F1200\n')
    assert 'G01 X50 Y0 F1200\n' in out
    assert s.device()['serial'] == dict(baud='115200', flow='none')


def test_3018_too_big():
    s = job.Settings(preset='CNC 3018 (GRBL)', speed=2)
    plan = job.plan([page([(0, 0), (250, 0), (250, 200)], 300)], 300, s)
    assert plan.problems() == [
        'The job is 200.0 mm long but the material is only 180.0 mm.']


def test_gcode_needs_a_speed():
    s = job.Settings(protocol='gcode')
    plan = job.plan([page(SQUARE)], 100, s)
    assert any('Set a speed' in p for p in plan.problems())
    s = job.Settings(protocol='hpgl')
    assert not job.plan([page(SQUARE)], 100, s).problems()


# -----------------------------------------------------------------------------
# Filters
# -----------------------------------------------------------------------------
def test_order_inside_first():
    ordered = filters.order([SQUARE, HOLE], 'inside')
    assert ordered[0][0] in HOLE
    assert ordered[1][0] in SQUARE


def test_order_nearest_moves_start():
    far = [(100, 100), (110, 100), (110, 110), (100, 110), (100, 100)]
    ordered = filters.order([far, SQUARE], 'nearest', start=(60, 60))
    #: Square first, starting at its closest corner
    assert ordered[0][0] == (50, 50)
    assert geometry.is_closed(ordered[0])


def test_order_document():
    assert filters.order([SQUARE, HOLE], 'document') == [SQUARE, HOLE]


def test_overcut():
    cut = filters.overcut(SQUARE, 70)
    assert cut[-2:] == [(50, 0), (50, 20)]
    #: Open paths are left alone
    assert filters.overcut(SQUARE[:3], 5) == SQUARE[:3]


def test_repeat():
    loop, = filters.repeat(SQUARE, 3)
    assert len(loop) == 1 + 3 * 4 and loop[-1] == SQUARE[0]
    assert filters.repeat(SQUARE[:3], 2) == [SQUARE[:3], SQUARE[:3]]


def test_blade_offset_line_and_corner():
    r = 0.5
    out, = filters.blade_offset([[(0, 0), (10, 0), (10, 10)]], r)
    #: Starts with the tip on the first point, axis leading by r
    assert out[0] == (r, 0)
    assert out[1] == (10 + r, 0)
    #: Then swings round the corner on an arc of radius r about it
    for p in out[2:-1]:
        assert geometry.dist(p, (10, 0)) == pytest.approx(r)
    assert out[-1] == pytest.approx((10, 10 + r))


def test_blade_offset_ignores_small_turns():
    out, = filters.blade_offset([[(0, 0), (10, 0), (20, 0.1)]], 0.5)
    assert len(out) == 3


def test_weedline():
    assert filters.weedline([HOLE], 2) == [
        (8, 8), (22, 8), (22, 22), (8, 22), (8, 8)]


# -----------------------------------------------------------------------------
# Job
# -----------------------------------------------------------------------------
def page(poly, height=100):
    """ Material-ish coords (y up) to page coords (y down) """
    return [(x, height - y) for x, y in poly]


def test_plan_auto_aligns_to_margin():
    sq = [(x + 30, y + 20) for x, y in SQUARE]
    s = job.Settings(margin_x=5, margin_y=7, overcut=0)
    plan = job.plan([page(sq)], 100, s)
    assert plan.bbox == (5, 7, 55, 57)
    assert plan.page_bbox == (30, 30, 80, 80)


def test_plan_keeps_position():
    sq = [(x + 30, y + 20) for x, y in SQUARE]
    plan = job.plan([page(sq)], 100, job.Settings(auto_align=False,
                                                  overcut=0))
    assert plan.bbox == (30, 20, 80, 70)


def test_plan_mirror_and_rotate():
    tri = [(0, 0), (20, 0), (0, 10), (0, 0)]
    plan = job.plan([page(tri)], 100, job.Settings(mirror=True, overcut=0))
    assert (20, 10) in plan.design[0]
    plan = job.plan([page(tri)], 100, job.Settings(rotate=True, overcut=0))
    assert plan.bbox == (0, 0, 10, 20)


def test_plan_copies_wrap_at_width():
    s = job.Settings(copies=3, spacing=10, width=120, overcut=0)
    plan = job.plan([page(SQUARE)], 100, s)
    assert plan.bbox == (0, 0, 110, 110)
    assert len(plan.paths) == 3


def test_plan_problems():
    s = job.Settings(width=40, overcut=0)
    problems = job.plan([page(SQUARE)], 100, s).problems()
    assert any('only 40.0 mm' in p for p in problems)


def test_plan_nothing():
    with pytest.raises(job.NoPathsError):
        job.plan([[(1, 1)]], 100, job.Settings())


def test_blade_offset_stays_on_material():
    s = job.Settings(blade_offset=0.5)
    plan = job.plan([page(SQUARE)], 100, s)
    x0, y0, _, _ = geometry.bbox(plan.paths)
    assert x0 >= 0 and y0 >= 0
    assert not plan.problems()


def test_prefeed():
    s = job.Settings(prefeed=True, overcut=0)
    plan = job.plan([page([(0, 0), (0, 25)])], 100, s)
    assert plan.prefeed == [(0, 10), (0, 20), (0, 25), (0, 20), (0, 10),
                            (0, 0)]
    assert job.encode(plan, s).startswith(
        b"IN;SP1;PU0,400;PU0,800;PU0,1000;PU0,800;PU0,400;PU0,0;")


def _moves(data):
    return [(c.decode(), int(x), int(y)) for c, x, y in
            re.findall(rb'(PU|PD)(-?\d+),(-?\d+)', data)]


@pytest.mark.parametrize('swap', [False, True])
@pytest.mark.parametrize('feed', [0, 10])
def test_encode_park(swap, feed):
    """ The head parks at the origin, or past the job along the feed axis
    (see inkcut/inkcut#442)
    """
    s = job.Settings(swap_xy=swap, invert_y=swap, feed=feed, overcut=0)
    plan = job.plan([page([(0, 0), (20, 0), (20, 30)])], 100, s)
    park = _moves(job.encode(plan, s))[-1]
    along = (30 + feed) * 40 if feed else 0
    assert park == (('PU', along, 0) if swap else ('PU', 0, along))


def test_encode_invert_stays_at_origin():
    s = job.Settings(invert_x=True, invert_y=True, margin_x=5, overcut=0)
    plan = job.plan([page([(0, 0), (20, 0), (20, 30)])], 100, s)
    moves = _moves(job.encode(plan, s))[:-1]
    xs = [m[1] for m in moves]
    ys = [m[2] for m in moves]
    assert (min(xs), max(xs)) == (5 * 40, 25 * 40)
    assert (min(ys), max(ys)) == (0, 30 * 40)
    #: Reflected: the first point moved to the opposite corner
    assert moves[0][1:] in ((25 * 40, 30 * 40), (5 * 40, 0))


def test_settings_reject_unknown():
    with pytest.raises(TypeError):
        job.Settings(nope=1)


# -----------------------------------------------------------------------------
# Presets and the dialog
# -----------------------------------------------------------------------------
INX = 'extension/cutscape_cut.inx'
NS = {'i': 'http://www.inkscape.org/namespace/inkscape/extension'}


def _inx():
    return ET.parse(INX).getroot()


@pytest.mark.parametrize('name', list(devices.PRESETS))
def test_presets(name):
    s = job.Settings(preset=name)
    d = s.device()
    assert d['protocol'] in protocols.PROTOCOLS
    assert d['steps'][0] > 0 and d['width'] >= 0
    plan = job.plan([page(SQUARE)], 100, s)
    assert job.encode(plan, s)


def test_preset_dmpl_mode():
    d = job.Settings(preset='Summa D760').device()
    assert d['steps'][0] == pytest.approx(1000 / 25.4)
    plan = job.plan([page(SQUARE)], 100, job.Settings(preset='Creation PCut CT-630'))
    assert job.encode(plan, job.Settings(preset='Creation PCut CT-630')
                      ).endswith(b" U F @ ")


def test_inx_presets_match():
    preset = [p for p in _inx().iter('{%s}param' % NS['i'])
              if p.get('name') == 'preset'][0]
    values = [o.get('value') for o in preset.iter('{%s}option' % NS['i'])]
    assert values == [cutter.ADDED, devices.CUSTOM] + list(devices.PRESETS)


def test_add_cutter_models_match():
    root = ET.parse('extension/cutscape_add.inx').getroot()
    model = [p for p in root.iter('{%s}param' % NS['i'])
             if p.get('name') == 'model'][0]
    values = [o.get('value') for o in model.iter('{%s}option' % NS['i'])]
    assert values == [devices.CUSTOM] + list(devices.PRESETS)


def test_inx_params_match_arguments():
    names = {p.get('name') for p in _inx().iter('{%s}param' % NS['i'])}
    names -= {'tab', 'action'}
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'cutscape_cut', 'extension/cutscape_cut.py')
    if importlib.util.find_spec('inkex') is None:
        pytest.skip("inkex not installed")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert names == set(job.DEFAULTS) | set(mod.CONNECTION)


def test_inx_defaults_match_dataclass():
    """ The dialog's defaults are what a fresh install sends """
    expected = job.DEFAULTS
    for p in _inx().iter('{%s}param' % NS['i']):
        name = p.get('name')
        if name not in job.DEFAULTS or p.get('type') in ('optiongroup',):
            continue
        text = (p.text or '').strip()
        default = expected[name]
        if isinstance(default, bool):
            assert text == ('true' if default else 'false'), name
        elif isinstance(default, (int, float)):
            assert math.isclose(float(text), default), name


# -----------------------------------------------------------------------------
# The other menu entries: Add Cutter, Remove Cutter, Remove Preview, Add
# Crop Marks
# -----------------------------------------------------------------------------
MENU = {
    'extension/cutscape_add.inx': ('add', {'model', 'address'}),
    'extension/cutscape_remove.inx': ('remove', set()),
    'extension/cutscape_clear.inx': ('clear', set()),
    'extension/cutscape_marks.inx': ('marks', set()),
}


@pytest.mark.parametrize('inx', sorted(MENU))
def test_menu_entries(inx):
    """ Each runs the effect script with a fixed, hidden --entry and only
    parameters the script knows
    """
    entry, visible = MENU[inx]
    root = ET.parse(inx).getroot()
    q = '{%s}' % NS['i']
    params = {p.get('name'): p for p in root.iter(q + 'param')}
    assert params['entry'].get('gui-hidden') == 'true'
    assert params['entry'].text == entry
    assert set(params) - {'entry'} == visible
    assert root.find(q + 'script/' + q + 'command').text == 'cutscape_cut.py'
    assert root.find('.//' + q + 'submenu').get('name') == 'CutScape'
    if importlib.util.find_spec('inkex') is None:
        pytest.skip("inkex not installed")
    spec = importlib.util.spec_from_file_location(
        'cutscape_cut', 'extension/cutscape_cut.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert visible <= set(mod.CONNECTION) | set(mod.ADD)


def test_cut_actions_match():
    """ Anything else Inkscape passes as --action (an old saved choice)
    previews, as the dialog shows """
    action = [p for p in _inx().iter('{%s}param' % NS['i'])
              if p.get('name') == 'action'][0]
    values = [o.get('value') for o in action.iter('{%s}option' % NS['i'])]
    if importlib.util.find_spec('inkex') is None:
        pytest.skip("inkex not installed")
    spec = importlib.util.spec_from_file_location(
        'cutscape_cut', 'extension/cutscape_cut.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert values == list(mod.CUT_ACTIONS)
    assert values[0] == 'preview'


def test_menu_ids_unique():
    ids = [ET.parse(p).getroot().find('{%s}id' % NS['i']).text
           for p in sorted(glob.glob('extension/*.inx'))]
    assert len(ids) == len(set(ids)) == 5
