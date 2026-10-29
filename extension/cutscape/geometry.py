"""
Plain-Python polyline helpers. A polyline is a list of (x, y) tuples in
millimetres; nothing here depends on inkex or Qt.
"""
import math

#: Points closer than this (mm) are treated as the same point
EPSILON = 1e-3


def dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def length(poly):
    return sum(dist(poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def is_closed(poly, eps=EPSILON):
    return len(poly) > 2 and dist(poly[0], poly[-1]) <= eps


def bbox(polys):
    """ Return (minx, miny, maxx, maxy) of all points, or None if empty """
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def translate(polys, dx, dy):
    return [[(x + dx, y + dy) for x, y in poly] for poly in polys]


def scale(polys, sx, sy):
    return [[(x * sx, y * sy) for x, y in poly] for poly in polys]


def swap_xy(polys):
    return [[(y, x) for x, y in poly] for poly in polys]


def rotate90(polys):
    """ Rotate 90 degrees counter-clockwise about the origin """
    return [[(-y, x) for x, y in poly] for poly in polys]


def dedupe(poly, eps=EPSILON):
    """ Drop consecutive duplicate points """
    out = []
    for p in poly:
        if not out or dist(out[-1], p) > eps:
            out.append(p)
    return out


def rotate_start(poly, i):
    """ Start a closed polyline at vertex i, keeping it closed """
    if i == 0 or not is_closed(poly):
        return poly
    ring = poly[:-1]
    ring = ring[i:] + ring[:i]
    return ring + [ring[0]]


def direction(a, b):
    """ Unit vector from a to b, or None if they coincide """
    d = dist(a, b)
    if d <= EPSILON:
        return None
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def angle_between(u, v):
    """ Signed angle in radians to turn unit vector u onto v """
    return math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])


def arc(center, r, u, v, step=math.radians(10)):
    """ Points on a circle of radius r about center, sweeping the short way
    from direction u to direction v (excluding the start point).
    """
    a0 = math.atan2(u[1], u[0])
    sweep = angle_between(u, v)
    n = max(1, int(math.ceil(abs(sweep) / step)))
    cx, cy = center
    return [(cx + r * math.cos(a0 + sweep * k / n),
             cy + r * math.sin(a0 + sweep * k / n)) for k in range(1, n + 1)]


def point_in_polygon(pt, poly):
    """ Even-odd test; poly is treated as closed """
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and \
                x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside
