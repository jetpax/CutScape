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
