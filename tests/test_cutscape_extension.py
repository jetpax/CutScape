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


def test_send_to_file(tmp_path):
    plt = tmp_path / 'job.plt'
    run(tmp_path, '--action=send', '--id=star', '--transport=file',
        '--file=%s' % plt, '--overcut=0')
    data = plt.read_bytes()
    assert data.startswith(b'IN;SP1;PU')
    #: Ten star points plus closing point, park and IN
    assert data.count(b'PD') == 10
    assert data.endswith(b'PU0,0;IN;')


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
