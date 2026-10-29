"""
Draw a planned toolpath onto the canvas as a locked layer: cuts in red,
pen-up travel dashed in blue, the material edge in grey and the origin as
a blue dot. The toolpath is laid over the drawing it came from, so the
origin and material edge show where the drawing sits on the material.
"""
import inkex

from .document import PREVIEW_ID, mm_to_uu, page_box

CUT = '#e01b24'
TRAVEL = '#1c71d8'
MATERIAL = '#9a9996'


def remove(svg):
    old = svg.getElementById(PREVIEW_ID)
    if old is not None:
        old.getparent().remove(old)
        return True
    return False


def _d(polys, to_doc):
    out = []
    for p in polys:
        if len(p) < 2:
            continue
        pts = [to_doc(x, y) for x, y in p]
        out.append('M ' + ' L '.join('%.4f,%.4f' % q for q in pts))
    return ' '.join(out)


def draw(svg, job):
    """ Replace any previous preview with one for job (a job.Plan) """
    remove(svg)
    k = mm_to_uu(svg)
    px, py, _, _ = page_box(svg)
    #: Line the planned design's top-left up with the drawing's
    ax0, ay0, _, _ = job.page_bbox
    bx0, _, _, by1 = job.bbox

    def to_doc(x, y):
        return px + (ax0 + x - bx0) * k, py + (ay0 + by1 - y) * k

    layer = inkex.Layer.new('CutScape preview', id=PREVIEW_ID)
    layer.set('sodipodi:insensitive', 'true')
    svg.append(layer)

    def add(d, style, label):
        if not d:
            return
        path = inkex.PathElement(d=d)
        path.style = style
        path.label = label
        layer.append(path)

    _, _, x1, y1 = job.bbox
    w = job.device['width'] or x1
    h = job.device['length'] or y1
    add(_d([[(0, 0), (w, 0), (w, h), (0, h), (0, 0)]], to_doc),
        {'fill': 'none', 'stroke': MATERIAL, 'stroke-width': 0.3 * k,
         'stroke-dasharray': '%g,%g' % (2 * k, 2 * k)}, 'Material')

    travel, pos = [], (0.0, 0.0)
    for p in job.prefeed:
        travel.append([pos, p])
        pos = p
    for p in job.paths:
        travel.append([pos, p[0]])
        pos = p[-1]
    travel.append([pos, job.park])
    add(_d(travel, to_doc),
        {'fill': 'none', 'stroke': TRAVEL, 'stroke-width': 0.2 * k,
         'stroke-dasharray': '%g,%g' % (1 * k, 1 * k)}, 'Travel')

    add(_d(job.paths, to_doc),
        {'fill': 'none', 'stroke': CUT, 'stroke-width': 0.4 * k,
         'stroke-linejoin': 'round'}, 'Cuts')

    ox, oy = to_doc(0, 0)
    origin = inkex.Circle.new(center=(ox, oy), radius=1.5 * k)
    origin.style = {'fill': TRAVEL, 'stroke': 'none'}
    origin.label = 'Origin'
    layer.append(origin)
    return layer
