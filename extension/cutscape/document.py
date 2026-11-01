"""
Read cut geometry out of an Inkscape document with inkex.

Shapes are flattened to polylines in millimetres, in page coordinates
(origin at the page's top-left, y down, like the SVG).
"""
import os
import tempfile

import inkex
from inkex import bezier, Transform

from .i18n import _

#: id of the layer the preview is drawn into; never cut
PREVIEW_ID = 'cutscape-preview'
#: The crop marks' layer, and each mark, numbered from 1; never cut
MARKS_ID = 'cutscape-marks'
MARK_ID = 'cutscape-mark-%d'
MARK_COUNT = 4
#: The hidden layer of what is cut when lining up with printed marks
CUT_LINES_ID = 'cutscape-cut-lines'

NEVER_CUT = {PREVIEW_ID, MARKS_ID}

CONTAINERS = {'g', 'a', 'switch', 'svg'}
TEXT = {'text', 'flowRoot'}
SKIP = {'defs', 'clipPath', 'mask', 'marker', 'pattern', 'symbol',
        'metadata', 'title', 'desc', 'style', 'script', 'namedview',
        'linearGradient', 'radialGradient', 'filter', 'image'}


def _tag(el):
    tag = el.tag if isinstance(el.tag, str) else ''
    return tag.rsplit('}', 1)[-1]


def mm_to_uu(svg):
    """ User units per millimetre of paper """
    if hasattr(svg, 'viewport_to_unit'):
        return svg.viewport_to_unit('1mm')
    return svg.unittouu('1mm')


def page_box(svg):
    """ (x, y, width, height) of the page in user units """
    vb = svg.get_viewbox()
    if not vb or not vb[2] or not vb[3]:
        w, h = svg.viewport_width, svg.viewport_height
        return 0.0, 0.0, w, h
    return tuple(vb)


def hidden(el):
    while el is not None and isinstance(el, inkex.BaseElement):
        if el.get('id') in NEVER_CUT:
            return True
        style = el.style if hasattr(el, 'style') else {}
        if style.get('display') == 'none' or el.get('display') == 'none':
            return True
        if style.get('visibility') == 'hidden' or \
                el.get('visibility') == 'hidden':
            return True
        el = el.getparent()
    return False


def _parent_transform(el):
    parent = el.getparent()
    if parent is None or not isinstance(parent, inkex.BaseElement):
        return Transform()
    return parent.composed_transform()


def iter_shapes(el, transform, depth=0):
    """ Yield (element, transform-to-user-units) for each drawable leaf
    under el, expanding groups and clones. Text yields with transform None.
    """
    tag = _tag(el)
    if tag in SKIP or el.get('id') in NEVER_CUT or depth > 32:
        return
    if el.style.get('display') == 'none' or el.get('display') == 'none':
        return
    t = transform @ el.transform
    if tag in CONTAINERS:
        for child in el:
            if isinstance(child, inkex.BaseElement):
                for item in iter_shapes(child, t, depth + 1):
                    yield item
    elif tag == 'use':
        target = el.href
        if target is None:
            return
        x, y = el.to_dimensionless(el.get('x', 0)), \
            el.to_dimensionless(el.get('y', 0))
        for item in iter_shapes(target, t @ Transform(translate=(x, y)),
                                depth + 1):
            yield item
    elif tag in TEXT:
        yield el, None
    elif isinstance(el, inkex.ShapeElement):
        yield el, t


def selected_roots(svg, ids=None):
    """ The elements to cut: the selection if given, else every visible
    top-level element.
    """
    if ids:
        els = [svg.getElementById(i) for i in ids]
        els = [e for e in els if e is not None and not hidden(e)]
        if not els:
            #: Never fall back to the whole document (inkcut/inkcut#442)
            raise ValueError(_("None of the selected objects can be cut"))
        return els
    return [e for e in svg if isinstance(e, inkex.BaseElement)
            and not hidden(e)]


def find_text(svg, roots):
    return [el for root in roots
            for el, t in iter_shapes(root, _parent_transform(root))
            if t is None]


def _point_inkex_at_inkscape():
    """ inkex runs `inkscape` from PATH unless INKSCAPE_COMMAND is set, and
    Inkscape sets neither for its extensions. Inside a macOS app bundle the
    binary sits next to the inkex we were loaded from.
    """
    from inkex import command
    try:
        command.which(command.INKSCAPE_EXECUTABLE_NAME)
        return
    except command.CommandNotFound:
        pass
    parts = os.path.realpath(os.path.dirname(inkex.__file__)).split(os.sep)
    if 'Contents' in parts:
        root = os.sep.join(parts[:parts.index('Contents') + 1])
        binary = os.path.join(root, 'MacOS', 'inkscape')
        if os.path.isfile(binary):
            command.INKSCAPE_EXECUTABLE_NAME = binary


def text_to_paths(document, ids):
    """ Return a copy of the document with text converted to paths by the
    running Inkscape, keeping element ids.
    """
    from inkex.command import inkscape
    _point_inkex_at_inkscape()
    for i in ids:
        if not i or any(c in i for c in ',;\r\n'):
            raise ValueError(_("Can't convert text {id} to a path; convert "
                               "it with Path > Object to Path").format(id=i))
    tmp = tempfile.mkdtemp(prefix='cutscape-')
    path = os.path.join(tmp, 'input.svg')
    with open(path, 'wb') as f:
        document.write(f)
    actions = ['select-by-id:' + ','.join(ids), 'object-to-path',
               'export-overwrite', 'export-do']
    inkscape(path, actions=';'.join(actions))
    with open(path, 'rb') as f:
        return inkex.load_svg(f)


def polylines(svg, roots, tolerance=0.05):
    """ Flatten everything under roots into polylines in page millimetres.
    tolerance is the maximum deviation from curves in mm.
    """
    k = mm_to_uu(svg)
    px, py = page_box(svg)[:2]
    result = []
    for root in roots:
        for el, t in iter_shapes(root, _parent_transform(root)):
            if t is None:
                raise ValueError(_("Text {id} must be converted to a path "
                                   "first").format(id=el.get('id')))
            path = el.path
            if not path:
                continue
            csp = path.to_absolute().transform(t).to_superpath()
            bezier.cspsubdiv(csp, tolerance * k)
            for sub in csp:
                poly = [((node[1][0] - px) / k, (node[1][1] - py) / k)
                        for node in sub]
                if len(poly) > 1:
                    result.append(poly)
    return result


def page_size(svg):
    """ (width, height) of the page in millimetres """
    k = mm_to_uu(svg)
    w, h = page_box(svg)[2:]
    return w / k, h / k


def extent(svg, roots):
    """ (x0, y0, x1, y1) of roots in page millimetres, pictures and text
    included, unlike polylines
    """
    box = inkex.BoundingBox()
    for root in roots:
        if isinstance(root, inkex.ShapeElement):
            box += root.bounding_box(_parent_transform(root))
    if not box:
        raise ValueError(_("Nothing to cut"))
    k = mm_to_uu(svg)
    px, py = page_box(svg)[:2]
    return ((box.left - px) / k, (box.top - py) / k,
            (box.right - px) / k, (box.bottom - py) / k)


def cut_lines(svg):
    """ What Add Crop Marks set aside to be cut """
    layer = svg.getElementById(CUT_LINES_ID)
    els = [] if layer is None else [
        e for e in layer if isinstance(e, inkex.BaseElement)]
    if not els:
        raise ValueError(_("Select the lines to cut, then use Extensions > "
                           "CutScape > Add Crop Marks."))
    return els


def crop_marks(svg):
    """ The centres of the crop marks in page millimetres, in order """
    k = mm_to_uu(svg)
    px, py = page_box(svg)[:2]
    centres = []
    for number in range(1, MARK_COUNT + 1):
        el = svg.getElementById(MARK_ID % number)
        box = None if el is None else el.bounding_box(_parent_transform(el))
        if not box:
            raise ValueError(_("This drawing has no crop marks. Use "
                               "Extensions > CutScape > Add Crop Marks and "
                               "print it first."))
        centres.append(((box.center_x - px) / k, (box.center_y - py) / k))
    return centres
