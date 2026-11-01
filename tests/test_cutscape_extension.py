"""
CutScape as Inkscape runs it: the effect script on a document, via inkex.
Text-to-path conversion needs a real Inkscape, so these tests stick to
shapes.
"""
import pytest

inkex = pytest.importorskip('inkex')

from cutscape import document  # noqa: E402

SAMPLE = 'tests/data/cutscape/sample.svg'


def run(tmp_path, *args):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'cutscape_cut', 'extension/cutscape_cut.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / 'out.svg'
    ext = mod.CutScape()
    ext.run(list(args) + ['--output=%s' % out, SAMPLE])
    return ext, out


def test_preview_layer(tmp_path):
    ext, out = run(tmp_path, '--action=preview', '--id=plate', '--id=star')
    svg = inkex.load_svg(str(out)).getroot()
    layer = svg.getElementById(document.PREVIEW_ID)
    assert layer is not None
    assert layer.get('sodipodi:insensitive') == 'true'
    labels = [el.label for el in layer]
    assert labels == ['Material', 'Travel', 'Cuts', 'Origin']
    #: The preview itself is never cut
    roots = document.selected_roots(svg)
    assert all(r.get('id') != document.PREVIEW_ID for r in roots)


@pytest.mark.parametrize('action', ['find', 'printers', 'clear'])
def test_old_saved_action_previews(tmp_path, action):
    """ Inkscape still passes the action saved by 0.2.0's Cut dialog, while
    the dialog shows Preview as chosen. "clear" is valid for Remove
    Preview, but not from Cut's dialog. """
    ext, out = run(tmp_path, '--action=' + action, '--id=star',
                   '--preset=custom', '--transport=printer')
    svg = inkex.load_svg(str(out)).getroot()
    assert svg.getElementById(document.PREVIEW_ID) is not None


def test_send_to_file(tmp_path):
    plt = tmp_path / 'job.plt'
    run(tmp_path, '--action=send', '--id=star', '--transport=file',
        '--file=%s' % plt, '--overcut=0')
    data = plt.read_bytes()
    assert data.startswith(b'IN;SP1;PU')
    #: Ten star points plus closing point, park and IN
    assert data.count(b'PD') == 10
    assert data.endswith(b'PU0,0;IN;')


def test_test_square(tmp_path):
    """ The test square ignores the drawing and material layout, but uses
    the device settings: 50 mm at HPGL's 40 steps per mm
    """
    plt = tmp_path / 'test.plt'
    run(tmp_path, '--action=test', '--id=star', '--transport=file',
        '--file=%s' % plt, '--overcut=0', '--copies=3', '--mirror=true')
    data = plt.read_bytes()
    assert data.startswith(b'IN;SP1;PU')
    assert b'2000,0' in data and b'2000,2000' in data and b'0,2000' in data
    assert data.count(b'PD') == 4


def test_add_cutter_action(tmp_path, monkeypatch, capsys):
    from cutscape import cutter
    calls = []
    monkeypatch.setattr(cutter, 'add', lambda *a: calls.append(a) or (
        'ADDED', True))
    run(tmp_path, '--entry=add', '--model=Summa D760', '--address= vinyl ')
    assert calls == [('Summa D760', ' vinyl ')]
    #: CUTSCAPE_STDERR (conftest) keeps it out of a window
    assert 'ADDED' in capsys.readouterr().err


def test_messages_use_a_window_when_possible(tmp_path, monkeypatch, capsys):
    from cutscape import cutter, notify
    shown = []
    monkeypatch.setattr(notify, 'show',
                        lambda text, **kw: shown.append((text, kw)) or True)
    monkeypatch.setattr(cutter, 'add', lambda *a: ('NOT YET', False))
    run(tmp_path, '--entry=add')
    assert shown == [('NOT YET', dict(kind='warning', report=False))]
    #: Nothing on stderr, so Inkscape adds no "additional data" warning
    assert 'NOT YET' not in capsys.readouterr().err


def test_add_cutter_problems(tmp_path, monkeypatch, capsys):
    """ "No cutter found" goes in CutScape's window when there is one,
    and is an error otherwise
    """
    from cutscape import cutter, notify

    def nothing(*a):
        raise cutter.CutterError('No cutter found.')
    monkeypatch.setattr(cutter, 'add', nothing)
    with pytest.raises(SystemExit):
        run(tmp_path, '--entry=add')
    assert 'No cutter found.' in capsys.readouterr().err
    shown = []
    monkeypatch.setattr(notify, 'show',
                        lambda text, **kw: shown.append((text, kw)) or True)
    run(tmp_path, '--entry=add')
    assert shown == [('No cutter found.', dict(kind='warning'))]


def test_remove_cutter_action(tmp_path, monkeypatch, capsys):
    from cutscape import cutter
    monkeypatch.setattr(cutter, 'remove', lambda: 'REMOVED')
    run(tmp_path, '--entry=remove')
    assert 'REMOVED' in capsys.readouterr().err


def test_send_to_the_added_cutter(tmp_path):
    from cutscape import cutter
    dev = tmp_path / 'lp0'
    dev.write_bytes(b'')
    cutter.save(dict(model='Summa D760', transport='device',
                     device=str(dev), printer='', port=''))
    run(tmp_path, '--action=send', '--id=star', '--preset=added',
        '--transport=added')
    #: The Summa's DMPL, not the custom settings' HPGL
    assert dev.read_bytes().startswith(b';:')


def test_send_without_an_added_cutter(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run(tmp_path, '--action=send', '--id=star', '--transport=added')
    assert 'Add Cutter' in capsys.readouterr().err


def test_whole_drawing_skips_hidden(tmp_path):
    svg = inkex.load_svg(SAMPLE).getroot()
    roots = document.selected_roots(svg)
    shapes = [el.get('id') for r in roots
              for el, t in document.iter_shapes(r, inkex.Transform())]
    assert 'hidden' not in shapes
    assert {'plate', 'hole', 'label', 'star'} <= set(shapes)


def test_unmatched_selection_fails(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, '--action=send', '--id=nope', '--transport=file',
            '--file=%s' % (tmp_path / 'x.plt'))
    assert not (tmp_path / 'x.plt').exists()


def test_page_units(tmp_path):
    """ Geometry comes out in millimetres from the page's top-left """
    svg = inkex.load_svg(SAMPLE).getroot()
    star = svg.getElementById('star')
    polys = document.polylines(svg, [star])
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx(
        (92, 12, 148, 64))
    assert document.page_size(svg) == pytest.approx((160, 90))


# -----------------------------------------------------------------------------
# Print and cut
# -----------------------------------------------------------------------------
def marked(tmp_path, monkeypatch, *ids):
    """ The sample with Add Crop Marks run on it; returns its path """
    monkeypatch.setenv('CUTSCAPE_STDERR', '1')
    ext, out = run(tmp_path, '--entry=marks', *['--id=' + i for i in ids])
    return out


def test_add_crop_marks(tmp_path, monkeypatch):
    before = inkex.load_svg(SAMPLE).getroot()
    star = before.getElementById('star')
    was = star.bounding_box(star.getparent().composed_transform())
    svg = inkex.load_svg(str(marked(tmp_path, monkeypatch, 'star'))).getroot()

    #: The cut lines are hidden, so they don't print, and stay in place
    layer = svg.getElementById(document.CUT_LINES_ID)
    assert layer.style.get('display') == 'none'
    assert [e.get('id') for e in layer] == ['star']
    star = svg.getElementById('star')
    now = star.bounding_box(star.getparent().composed_transform())
    assert now.left == pytest.approx(was.left)
    assert now.top == pytest.approx(was.top)
    assert now.width == pytest.approx(was.width)

    #: Four marks at the corners, numbered round from the bottom left
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = document.crop_marks(svg)
    assert x1 == pytest.approx(x4) and x2 == pytest.approx(x3)
    assert y1 == pytest.approx(y2) and y3 == pytest.approx(y4)
    assert x1 < x2 and y1 > y4

    #: Everything left in the drawing is inside them
    left = document.extent(svg, document.selected_roots(svg))
    assert x1 < left[0] and left[2] < x2 and y4 < left[1] and left[3] < y1

    #: A plain Cut leaves out the marks and the hidden lines
    roots = document.selected_roots(svg)
    assert not {e.get('id') for e in roots} & {document.MARKS_ID,
                                               document.CUT_LINES_ID}
    for root in roots:
        for el, t in document.iter_shapes(
                root, document._parent_transform(root)):
            assert el.getparent().get('id') != document.MARKS_ID


def test_crop_marks_need_something_to_cut(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        marked(tmp_path, monkeypatch)


def test_marks_again_keep_the_cut_lines(tmp_path, monkeypatch):
    first = marked(tmp_path, monkeypatch, 'star')
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'cutscape_cut', 'extension/cutscape_cut.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / 'again.svg'
    mod.CutScape().run(['--entry=marks', '--id=plate', '--output=%s' % out,
                        str(first)])
    svg = inkex.load_svg(str(out)).getroot()
    assert [e.get('id') for e in document.cut_lines(svg)] == ['star', 'plate']
    assert sum(1 for e in svg if e.get('id') == document.MARKS_ID) == 1


def align_run(tmp_path, monkeypatch, source, drive, *args):
    """ Cut's line-up action on source, with drive(session) in place of the
    window; returns the bytes sent to the cutter
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'cutscape_cut', 'extension/cutscape_cut.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sent = []

    def send(data, transport, **options):
        assert transport == 'device'
        sent.append((data, options.get('timeout')))
        return '/dev/usb/lp0'
    monkeypatch.setattr(mod.transports, 'send', send)

    def window(session, problems):
        session.head.start()
        return drive(session, problems)
    monkeypatch.setattr(mod.align_window, 'run', window)
    monkeypatch.setenv('CUTSCAPE_STDERR', '1')
    monkeypatch.setenv('CUTSCAPE_CONFIG', str(tmp_path / 'cutter.json'))
    mod.CutScape().run(
        ['--action=align', '--preset=custom', '--transport=device',
         '--device=/dev/usb/lp0', '--overcut=0', '--output=%s' % (
             tmp_path / 'result.svg')] + list(args) + [str(source)])
    return sent


def test_lined_up_cut(tmp_path, monkeypatch):
    """ A sheet turned 2° and shifted: the cut lands where the sheet put it """
    import math
    source = marked(tmp_path, monkeypatch, 'star')
    svg = inkex.load_svg(str(source)).getroot()
    star = document.polylines(svg, document.cut_lines(svg))[0]
    height = document.page_size(svg)[1]
    a = math.radians(2)

    def sheet(x, y):
        """ Where a page point really is on the cutter """
        y = height - y
        return (x * math.cos(a) - y * math.sin(a) + 40,
                x * math.sin(a) + y * math.cos(a) + 20)

    def drive(session, problems):
        for mark in document.crop_marks(svg):
            session.head.move(*sheet(*mark))
            session.on_mark()
        assert session.done
        assert session.fit().rotation == pytest.approx(2, abs=0.01)
        assert not problems()
        return True
    sent = align_run(tmp_path, monkeypatch, source, drive)

    #: Moves are quick to give up; the cut isn't
    assert all(timeout == 5.0 for data, timeout in sent[:-1])
    assert sent[-1][1] is None
    job = sent[-1][0].decode()
    assert job.startswith('IN;SP1;PU')
    assert job.endswith('PU0,0;IN;')
    cuts = [tuple(int(v) / 40 for v in c[2:].split(','))
            for c in job.split(';') if c[:2] in ('PU', 'PD') and c[2:]][:-1]
    want = [sheet(*p) for p in star]
    assert len(cuts) == len(want)
    for got in cuts:
        assert min(math.hypot(got[0] - w[0], got[1] - w[1])
                   for w in want) < 0.05


def test_closing_the_window_cuts_nothing(tmp_path, monkeypatch):
    source = marked(tmp_path, monkeypatch, 'star')
    sent = align_run(tmp_path, monkeypatch, source, lambda s, p: False)
    assert len(sent) == 1
    assert b'PD' not in sent[0][0]


def test_lining_up_needs_marks(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        align_run(tmp_path, monkeypatch, SAMPLE, lambda s, p: True)


def test_lining_up_needs_a_direct_connection(tmp_path, monkeypatch):
    source = marked(tmp_path, monkeypatch, 'star')
    with pytest.raises(SystemExit):
        align_run(tmp_path, monkeypatch, source, lambda s, p: True,
                  '--transport=file')


def test_calibrating_needs_no_crop_marks(tmp_path, monkeypatch, capsys):
    """ The sample has no marks and nothing set aside to cut """
    def drive(session, problems):
        assert session.calibrating == session.CUT_CROSS
        session.head.move(50, 50)
        session.cut_cross()
        session.head.move(20, 62)
        session.laser_on_cross()
        return False
    sent = align_run(tmp_path, monkeypatch, SAMPLE, drive,
                     '--action=calibrate')
    assert sent[0][0].startswith(b'IN;')
    assert any(b'PD' in data for data, timeout in sent)
    assert all(timeout == 5.0 for data, timeout in sent)
    assert 'Laser calibrated' in capsys.readouterr().err


def test_calibrating_closed_early_says_nothing(tmp_path, monkeypatch, capsys):
    sent = align_run(tmp_path, monkeypatch, SAMPLE, lambda s, p: False,
                     '--action=calibrate')
    assert not any(b'PD' in data for data, timeout in sent)
    assert 'Laser calibrated' not in capsys.readouterr().err


def test_calibrating_needs_a_direct_connection(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        align_run(tmp_path, monkeypatch, SAMPLE, lambda s, p: False,
                  '--action=calibrate', '--transport=file')
