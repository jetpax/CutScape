"""
Crop marks for print and cut. Add Crop Marks moves the cut lines to a hidden
layer, so they don't print, and draws four crosses around the design on a
locked layer of their own. Neither layer is ever cut by a plain Cut; lining
up (see align) finds the marks, and cuts the hidden layer.
"""
import inkex

from .document import (CUT_LINES_ID, MARK_ID, MARKS_ID, cut_lines,
                       extent, mm_to_uu, page_box, page_size, selected_roots)
from .i18n import _

#: mm
SIZE = 10.0
LINE = 0.25
NUMBER = 3.0
#: From the design to the centre of each mark
GAP = 5.0 + SIZE / 2
#: Printers don't reach the edge of the paper
PRINTER_MARGIN = 5.0


def corners():
    """ Where each mark is, in the order they're numbered """
    return _("bottom left"), _("bottom right"), _("top right"), _("top left")


def _cut_lines_layer(svg):
    layer = svg.getElementById(CUT_LINES_ID)
    if layer is None:
        layer = inkex.Layer.new(_('CutScape cut lines'), id=CUT_LINES_ID)
        layer.style = {'display': 'none'}
        svg.append(layer)
    return layer


def _draw(svg, centres):
    old = svg.getElementById(MARKS_ID)
    if old is not None:
        old.getparent().remove(old)
    k = mm_to_uu(svg)
    px, py = page_box(svg)[:2]
    layer = inkex.Layer.new(_('CutScape crop marks'), id=MARKS_ID)
    layer.set('sodipodi:insensitive', 'true')
    svg.append(layer)
    r = SIZE / 2 * k
    for number, (x, y) in enumerate(centres, 1):
        cx, cy = px + x * k, py + y * k
        cross = inkex.PathElement(
            d='M %.4f,%.4f H %.4f M %.4f,%.4f V %.4f' % (
                cx - r, cy, cx + r, cx, cy - r, cy + r),
            id=MARK_ID % number)
        cross.style = {'fill': 'none', 'stroke': '#000000',
                       'stroke-width': LINE * k}
        cross.label = _('Crop mark {number}').format(number=number)
        layer.append(cross)
        text = inkex.TextElement()
        text.text = str(number)
        text.set('x', '%.4f' % (cx + NUMBER / 2 * k))
        text.set('y', '%.4f' % (cy - NUMBER / 2 * k))
        text.style = {'font-size': NUMBER * k, 'font-family': 'sans-serif',
                      'fill': '#000000'}
        layer.append(text)


def add(svg, ids=None):
    """ Make the selection, if there is one, cut lines, and put crop marks
    around them and the rest of the drawing. Returns whether the marks all
    fit on the page where a printer can reach them.
    """
    if ids:
        for el in selected_roots(svg, ids):
            #: Keep it where it is on the page; the new layer has no transform
            el.transform = el.getparent().composed_transform() @ el.transform
            _cut_lines_layer(svg).append(el)
    x0, y0, x1, y1 = extent(svg, cut_lines(svg) + selected_roots(svg))
    x0, y0, x1, y1 = x0 - GAP, y0 - GAP, x1 + GAP, y1 + GAP
    _draw(svg, ((x0, y1), (x1, y1), (x1, y0), (x0, y0)))
    width, height = page_size(svg)
    edge = PRINTER_MARGIN + SIZE / 2
    return (x0 >= edge and y0 >= edge and
            x1 <= width - edge and y1 <= height - edge)
