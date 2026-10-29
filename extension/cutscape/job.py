"""
Turn page polylines into a toolpath and encode it for a device.

Coordinates go through three spaces:
  page:     millimetres from the page's top-left, y down (from the SVG)
  material: millimetres from the cutter's origin, y up; x runs across the
            material width. This is what the preview shows.
  device:   material space with the preset's axis swap/inversion, scaled
            to device steps.
"""
from . import devices, filters, geometry, protocols

DEFAULTS = dict(
    #: Device
    preset=devices.CUSTOM,
    protocol='hpgl',
    steps_x=0.0,           # 0 = protocol default
    steps_y=0.0,
    swap_xy=False,
    invert_x=False,
    invert_y=False,
    dmpl_mode=1,
    hpgl_newline=False,
    hpgl_separate_z=False,
    #: Material
    width=0.0,             # mm, 0 = device width or unlimited
    length=0.0,
    margin_x=0.0,
    margin_y=0.0,
    auto_align=True,
    rotate=False,
    mirror=False,
    copies=1,
    spacing=5.0,
    weedline=False,
    weedline_padding=5.0,
    #: Cut
    speed=0,               # cm/s, 0 = leave the cutter's setting
    force=0,               # g, 0 = leave the cutter's setting
    pen=1,
    passes=1,
    overcut=1.0,
    blade_offset=0.0,
    blade_cutoff=5.0,
    order='inside',
    feed=0.0,              # mm to feed past the job; 0 = park at origin
    prefeed=False,         # pull the job's length through first
    tolerance=0.05,
)


class Settings(object):
    def __init__(self, **kwargs):
        unknown = set(kwargs) - set(DEFAULTS)
        if unknown:
            raise TypeError("Unknown settings: %s" % ', '.join(sorted(unknown)))
        for k, v in DEFAULTS.items():
            setattr(self, k, kwargs.get(k, v))

    def device(self):
        """ The effective device: the preset, or the custom fields """
        if self.preset and self.preset != devices.CUSTOM:
            d = dict(devices.get(self.preset))
            d['options'] = dict(d['options'])
        else:
            d = dict(protocol=self.protocol, width=0.0, length=0.0,
                     steps=None, swap_xy=self.swap_xy,
                     invert_x=self.invert_x, invert_y=self.invert_y,
                     options=dict(dmpl_mode=self.dmpl_mode))
            if self.steps_x or self.steps_y:
                d['steps'] = (self.steps_x or self.steps_y,
                              self.steps_y or self.steps_x)
        d['options'].setdefault('dmpl_mode', self.dmpl_mode)
        if self.width > 0:
            d['width'] = self.width
        if self.length > 0:
            d['length'] = self.length
        if not d['steps']:
            spm = protocols.PROTOCOLS[d['protocol']].default_steps(
                mode=d['options']['dmpl_mode'])
            d['steps'] = (spm, spm)
        return d


class NoPathsError(ValueError):
    pass


class Plan(object):
    """ A toolpath in material space """
    def __init__(self, paths, design, park, device, prefeed=()):
        #: What the tool axis follows, in cut order (blade offset applied)
        self.paths = paths
        #: The shapes being cut, in cut order (no blade offset)
        self.design = design
        #: Where the head ends up
        self.park = park
        self.device = device
        #: Pen-up points visited before cutting to pull the material through
        self.prefeed = list(prefeed)
        self.bbox = geometry.bbox(design)
        self.cut_length = sum(geometry.length(p) for p in paths)
        pos, travel = (0.0, 0.0), 0.0
        for p in self.prefeed:
            travel += geometry.dist(pos, p)
            pos = p
        for p in paths:
            travel += geometry.dist(pos, p[0])
            pos = p[-1]
        self.travel_length = travel + geometry.dist(pos, park)

    def problems(self):
        """ Reasons the job won't fit the material """
        out = []
        x0, y0, x1, y1 = geometry.bbox(self.paths)
        if x0 < -geometry.EPSILON or y0 < -geometry.EPSILON:
            out.append("The job extends %.1f mm past the origin; move it "
                       "onto the page or enable auto align." %
                       -min(x0, y0))
        w, l = self.device['width'], self.device['length']
        if w and x1 > w + geometry.EPSILON:
            out.append("The job is %.1f mm wide but the material is only "
                       "%.1f mm." % (x1, w))
        if l and y1 > l + geometry.EPSILON:
            out.append("The job is %.1f mm long but the material is only "
                       "%.1f mm." % (y1, l))
        return out


def _copies(polys, s, width):
    if s.copies <= 1:
        return polys
    x0, y0, x1, y1 = geometry.bbox(polys)
    w, h = x1 - x0, y1 - y0
    out = []
    col, row = 0, 0
    for _ in range(int(s.copies)):
        dx = col * (w + s.spacing)
        if col and width and x1 + dx > width:
            col, row = 0, row + 1
            dx = 0
        out.extend(geometry.translate(polys, dx, row * (h + s.spacing)))
        col += 1
    return out


def plan(page_polys, page_height, s):
    """ Lay out page polylines on the material and build the toolpath """
    device = s.device()
    polys = [geometry.dedupe([(x, page_height - y) for x, y in p])
             for p in page_polys]
    polys = [p for p in polys if len(p) > 1]
    if not polys:
        raise NoPathsError("Nothing to cut")

    x0, y0, x1, y1 = geometry.bbox(polys)
    if s.mirror:
        polys = [[(x0 + x1 - x, y) for x, y in p] for p in polys]
    if s.rotate:
        polys = geometry.rotate90(polys)

    bx0, by0, _, _ = geometry.bbox(polys)
    if s.auto_align:
        polys = geometry.translate(polys, s.margin_x - bx0, s.margin_y - by0)
    else:
        #: Keep the job where it is on the page
        polys = geometry.translate(polys, x0 - bx0, y0 - by0)

    polys = _copies(polys, s, device['width'])
    if s.weedline:
        polys.append(filters.weedline(polys, s.weedline_padding))

    design = filters.order(polys, s.order)
    paths = []
    for p in design:
        for q in filters.repeat(p, int(s.passes)):
            paths.append(filters.overcut(q, s.overcut))
    paths = filters.blade_offset(paths, s.blade_offset, s.blade_cutoff)

    if s.auto_align:
        #: Blade offset and overcut can reach past the margin; shift back
        tx0, ty0, _, _ = geometry.bbox(paths)
        dx, dy = max(0.0, s.margin_x - tx0), max(0.0, s.margin_y - ty0)
        if dx or dy:
            paths = geometry.translate(paths, dx, dy)
            design = geometry.translate(design, dx, dy)

    if s.feed > 0:
        park = (0.0, geometry.bbox(paths)[3] + s.feed)
    else:
        park = (0.0, 0.0)
    result = Plan(paths, design, park, device,
                  _prefeed(paths) if s.prefeed else ())
    #: Where the drawing was on the page (mm, y down), for the preview
    result.page_bbox = (x0, page_height - y1, x1, page_height - y0)
    return result


def _prefeed(paths, step=10.0):
    """ Pen-up moves that pull the job's length of material through the
    rollers and back in step mm hops, so a roll doesn't skew mid-job.
    From inkcut/inkcut#427 by mcmartnor.
    """
    travel = geometry.bbox(paths)[3]
    if travel <= 0:
        return []
    ys, y = [], 0.0
    while y < travel:
        y = min(y + step, travel)
        ys.append(y)
    back = [v for v in reversed(ys[:-1])] + [0.0]
    return [(0.0, v) for v in ys + back]


def encode(job, s):
    """ Encode a Plan for its device with settings s; returns bytes """
    d = job.device
    #: Pre-feed points are pen-up moves: one-point paths
    pts = [[p] for p in job.prefeed] + job.paths
    park = job.park
    if d['swap_xy']:
        pts = geometry.swap_xy(pts)
        park = (park[1], park[0])
    #: Reflect in place so the job stays by the origin; the park position
    #: is relative to the origin and is left alone
    x0, y0, x1, y1 = geometry.bbox(pts)
    if d['invert_x']:
        pts = [[(x0 + x1 - x, y) for x, y in p] for p in pts]
    if d['invert_y']:
        pts = [[(x, y0 + y1 - y) for x, y in p] for p in pts]
    sx, sy = d['steps']
    pts = geometry.scale(pts, sx, sy)
    proto = protocols.get(
        d['protocol'], speed=s.speed, force=s.force, pen=s.pen,
        mode=d['options']['dmpl_mode'], newline=s.hpgl_newline,
        separate_z=s.hpgl_separate_z)
    return proto.encode(pts, (park[0] * sx, park[1] * sy))
