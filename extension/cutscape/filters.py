"""
Toolpath filters on polylines in millimetres: ordering, passes, overcut,
weedline and drag-knife (blade) offset compensation.
"""
import math

from .geometry import (
    EPSILON, angle_between, arc, bbox, dedupe, direction, dist, is_closed,
    point_in_polygon, rotate_start,
)

ORDERS = ('document', 'nearest', 'inside')


def _best_start(poly, pos):
    """ Return (distance, poly) with the start moved as close to pos as the
    path allows: any vertex of a closed path, or either end of an open one.
    """
    if is_closed(poly):
        i = min(range(len(poly) - 1), key=lambda k: dist(pos, poly[k]))
        return dist(pos, poly[i]), rotate_start(poly, i)
    d0, d1 = dist(pos, poly[0]), dist(pos, poly[-1])
    if d1 < d0:
        return d1, poly[::-1]
    return d0, poly


def _contains(outer, outer_box, inner, inner_box):
    return (outer_box[0] - EPSILON <= inner_box[0]
            and outer_box[1] - EPSILON <= inner_box[1]
            and inner_box[2] <= outer_box[2] + EPSILON
            and inner_box[3] <= outer_box[3] + EPSILON
            and point_in_polygon(inner[0], outer))


def order(polys, method='inside', start=(0.0, 0.0)):
    """ Order paths to cut.

    document: as drawn.
    nearest:  greedy nearest neighbour from the current head position.
    inside:   nearest neighbour, but never cut a closed path before every
              path inside it, so pieces aren't freed before their holes
              (letter counters) are cut.
    """
    if method == 'document' or len(polys) < 2:
        return list(polys)
    if method not in ORDERS:
        raise ValueError("Unknown order %r" % method)

    n = len(polys)
    boxes = [bbox([p]) for p in polys]
    #: blockers[i] = paths that must be cut before path i
    blockers = [set() for _ in range(n)]
    if method == 'inside':
        for i in range(n):
            if not is_closed(polys[i]):
                continue
            for j in range(n):
                if i != j and _contains(polys[i], boxes[i], polys[j], boxes[j]):
                    # Identical outlines contain each other; keep doc order
                    if j > i and _contains(polys[j], boxes[j], polys[i], boxes[i]):
                        continue
                    blockers[i].add(j)

    pos = start
    done = set()
    result = []
    while len(done) < n:
        best = None
        for strict in (True, False):
            for i in range(n):
                if i in done or (strict and not blockers[i] <= done):
                    continue
                d, poly = _best_start(polys[i], pos)
                if best is None or d < best[0]:
                    best = (d, i, poly)
            #: Only relax containment if it somehow formed a cycle
            if best is not None:
                break
        _, i, poly = best
        done.add(i)
        result.append(poly)
        pos = poly[-1]
    return result


def repeat(poly, passes):
    """ Cut a path several times. A closed path is cut as one continuous
    loop so the blade never lifts; an open path is returned as separate
    passes.
    """
    if passes <= 1:
        return [poly]
    if is_closed(poly):
        loop = list(poly)
        for _ in range(passes - 1):
            loop.extend(poly[1:])
        return [loop]
    return [list(poly) for _ in range(passes)]


def overcut(poly, distance):
    """ Continue past the start of a closed path by distance so the cut
    meets cleanly.
    """
    if distance <= 0 or not is_closed(poly):
        return poly
    out = list(poly)
    remaining = distance
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        seg = dist(a, b)
        if seg <= 0:
            continue
        if seg >= remaining:
            t = remaining / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out
        out.append(b)
        remaining -= seg
    return out


def weedline(polys, padding):
    """ A closed rectangle around everything, padded by padding """
    box = bbox(polys)
    if box is None:
        return []
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = x0 - padding, y0 - padding, x1 + padding, y1 + padding
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def blade_offset(polys, offset, cutoff=5.0, start_dir=(1.0, 0.0)):
    """ Compensate for a drag knife whose tip trails the tool axis by offset.

    The axis leads the tip along the direction of travel, so every point
    is pushed forward by offset. Where the direction turns by more than
    cutoff degrees the axis swings round the corner on an arc so the blade
    swivels in place. The blade keeps its last direction between paths,
    starting with start_dir.
    """
    if offset <= 0:
        return [list(p) for p in polys]
    r = offset
    cutoff = math.radians(cutoff)
    last = start_dir
    out = []
    for poly in polys:
        poly = dedupe(poly)
        if len(poly) < 2:
            continue
        x, y = poly[0]
        res = [(x + r * last[0], y + r * last[1])]
        for i in range(len(poly) - 1):
            a, b = poly[i], poly[i + 1]
            u = direction(a, b)
            if u is None:
                continue
            if abs(angle_between(last, u)) > cutoff:
                res.extend(arc(a, r, last, u))
            res.append((b[0] + r * u[0], b[1] + r * u[1]))
            last = u
        out.append(res)
    return out
