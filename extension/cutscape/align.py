"""
Line a cut up with a printed sheet, the way Secabo's LAPOS does. The user
moves the head onto four printed crop marks; where they turn out to be gives
the sheet's shift, rotation and stretch, and the cut is corrected to match.

Many cutters can't say where their head is, so CutScape moves it itself with
pen-up moves and remembers where it put it. Positions are in the head's own
space: millimetres from the cutter's origin along its axes.

The cutter does nothing but move, so any cutter works. With a laser pointer
on the head, as on Secabo's C IV, the user aims the laser and the calibrated
distance from it to the blade is added on. Without one, the blade tip is
aimed, and that distance is zero.
"""
import cmath
import math

from . import geometry, job
from .i18n import _

#: mm; how long each line of the calibration cross is
CROSS = 10.0

#: Seconds a pen-up move may wait for the cutter, with the window frozen
MOVE_TIMEOUT = 5.0

#: Beyond these the sheet probably wasn't printed at 100%, or a mark is wrong
MAX_STRETCH = 0.02
MAX_ERROR = 1.0


def check(device, transport):
    """ Raise ValueError if lining up can't work with this setup """
    if device['protocol'] == 'gcode':
        raise ValueError(_("Lining up with printed marks isn't available for "
                           "G-code machines yet."))
    if transport not in ('device', 'serial'):
        raise ValueError(_("Lining up needs a direct connection to the "
                           "cutter, to move the head as you click: its USB "
                           "device on Linux (Add Cutter sets that up) or a "
                           "serial port."))


def axes(device):
    """ A function taking a direction in material space to the head's axes:
    the preset's axis settings, as job.encode applies them.
    """
    swap, invert_x = device['swap_xy'], device['invert_x']
    invert_y = device['invert_y']

    def to_head(x, y):
        if swap:
            x, y = y, x
        return (-x if invert_x else x), (-y if invert_y else y)
    return to_head


def base(device, page_height):
    """ Where a page point would be in head space on a sheet lying square
    on the origin: the job's y flip, then the preset's axis settings.
    Returns a function of (x, y).
    """
    to_head = axes(device)
    return lambda x, y: to_head(x, page_height - y)


class Fit(object):
    """ x' = a x + b y + c, y' = d x + e y + f: the best fit taking the points
    src to dst. One point gives a shift, two add rotation and an even
    stretch, and three or more give a stretch along each axis as well.
    """
    def __init__(self, src, dst):
        if len(src) != len(dst) or not src:
            raise ValueError("Need matching points")
        self.src, self.dst = list(src), list(dst)
        if len(src) == 1:
            self.m = (1.0, 0.0, dst[0][0] - src[0][0],
                      0.0, 1.0, dst[0][1] - src[0][1])
        elif len(src) == 2:
            self.m = self._similarity()
        else:
            self.m = self._affine()

    def _similarity(self):
        s0, s1 = (complex(*p) for p in self.src)
        d0, d1 = (complex(*p) for p in self.dst)
        if s0 == s1:
            raise ValueError(_("Two crop marks are in the same place."))
        k = (d1 - d0) / (s1 - s0)
        t = d0 - k * s0
        return k.real, -k.imag, t.real, k.imag, k.real, t.imag

    def _affine(self):
        n = float(len(self.src))
        mx = sum(p[0] for p in self.src) / n
        my = sum(p[1] for p in self.src) / n
        src = [(x - mx, y - my) for x, y in self.src]
        xx = sum(x * x for x, y in src)
        yy = sum(y * y for x, y in src)
        xy = sum(x * y for x, y in src)
        det = xx * yy - xy * xy
        #: mm^4; marks in a line can't show a stretch across that line
        if det < 1.0:
            raise ValueError(_("The crop marks are in a line, so they don't "
                               "show how the sheet lies."))

        def solve(axis):
            sx = sum(p[0] * q[axis] for p, q in zip(src, self.dst))
            sy = sum(p[1] * q[axis] for p, q in zip(src, self.dst))
            mean = sum(q[axis] for q in self.dst) / n
            u = (sx * yy - sy * xy) / det
            v = (sy * xx - sx * xy) / det
            return u, v, mean - u * mx - v * my
        return solve(0) + solve(1)

    def __call__(self, x, y):
        a, b, c, d, e, f = self.m
        return a * x + b * y + c, d * x + e * y + f

    @property
    def rotation(self):
        """ Degrees the sheet is turned """
        return math.degrees(cmath.phase(complex(self.m[0], self.m[3])))

    @property
    def stretch(self):
        """ (along the base x axis, along the base y axis); 1 is none """
        return (math.hypot(self.m[0], self.m[3]),
                math.hypot(self.m[1], self.m[4]))

    @property
    def worst(self):
        """ mm the mark that fits worst is from where the fit puts it """
        return max(geometry.dist(self(*p), q)
                   for p, q in zip(self.src, self.dst))


class Head(object):
    """ The cutter's head, as far as CutScape has moved it """
    def __init__(self, device, settings, send):
        self.steps = device['steps']
        self.axes = axes(device)
        self.proto = job.protocol(device, settings)
        self.send = send
        self.x = self.y = 0.0

    def _path(self, *points):
        sx, sy = self.steps
        return self.proto.path([(x * sx, y * sy) for x, y in points])

    def start(self):
        """ Set the cutter up and take the head to its origin, the one place
        known without asking """
        self.send((self.proto.begin() + self._path((0.0, 0.0))).encode('ascii'))
        self.x = self.y = 0.0

    def move(self, x, y):
        """ Pen up to x, y, stopping at the origin: past it, a cutter may
        not go, and the head would no longer be where CutScape thinks.
        Returns whether it got all the way.
        """
        to = max(x, 0.0), max(y, 0.0)
        #: Only once it has gone: a failed send leaves the head where it was
        self.send(self._path(to).encode('ascii'))
        self.x, self.y = to
        return to == (x, y)

    def cross(self):
        """ Cut a cross centred on the head and come back to its centre.
        Whichever way a drag knife trails, each line stays on its axis, so
        the lines still cross exactly there.
        """
        x, y, r = self.x, self.y, CROSS / 2
        if x < r or y < r:
            raise ValueError(_("Move the head at least {size:.0f} mm from "
                               "the origin first.").format(size=r))
        self.send((self._path((x - r, y), (x + r, y)) +
                   self._path((x, y - r), (x, y + r)) +
                   self._path((x, y))).encode('ascii'))

    def finish(self):
        """ Back to the origin, without cutting """
        self.send(self.proto.end(0, 0).encode('ascii'))
        self.x = self.y = 0.0


class Session(object):
    """ Lining up one sheet: what the window's buttons do """
    #: Calibration's steps
    CUT_CROSS, AIM_AT_CROSS = 'cut', 'aim'

    def __init__(self, head, marks, to_base, laser=None, turn=0,
                 assumed=False):
        self.head = head
        self.to_base = to_base
        expected = [to_base(*m) for m in marks]
        #: Start at the mark nearest the origin and go round, so the head
        #: has somewhere to go on every side
        first = min(range(len(expected)), key=lambda i: sum(expected[i])) \
            if expected else 0
        #: Where the marks should be, in the order they're aimed at
        self.expected = expected[first:] + expected[:first]
        #: The number printed by each, in the same order
        self.numbers = [(first + i) % len(expected) + 1
                        for i in range(len(expected))]
        #: Where the tool would have to go to reach each mark found so far
        self.found = []
        #: From the head with the laser on a spot, to the tool on that spot
        self.laser = tuple(laser) if laser else None
        #: The laser offset is the preset's, not measured on this cutter
        self.assumed = assumed
        #: Quarter turns from the arrows on screen to material space
        self.turn = turn % 4
        self.calibrating = None

    @classmethod
    def for_calibration(cls, head, laser=None, turn=0):
        """ Only finding the laser, with no sheet to line up """
        session = cls(head, [], None, laser, turn)
        session.begin_calibration()
        return session

    @property
    def index(self):
        return len(self.found)

    @property
    def number(self):
        """ The number printed by the mark being aimed at """
        return self.numbers[self.index]

    @property
    def done(self):
        return bool(self.expected) and len(self.found) == len(self.expected)

    def arrow(self, right, up, step):
        """ Move by step along the arrow (right, up); see Head.move """
        for _turn in range(self.turn):
            right, up = -up, right
        dx, dy = self.head.axes(right, up)
        return self.head.move(self.head.x + dx * step, self.head.y + dy * step)

    def on_mark(self):
        """ What's being aimed is on the next mark. Head to the one after,
        as close as the marks so far can tell.
        """
        dx, dy = self.laser or (0.0, 0.0)
        self.found.append((self.head.x + dx, self.head.y + dy))
        if not self.done:
            n = self.index
            x, y = Fit(self.expected[:n], self.found)(*self.expected[n])
            self.head.move(x - dx, y - dy)

    def back(self):
        """ Go back to the last mark, to aim at it again """
        if not self.found:
            return
        x, y = self.found[-1]
        dx, dy = self.laser or (0.0, 0.0)
        self.head.move(x - dx, y - dy)
        self.found.pop()

    def fit(self):
        return Fit(self.expected, self.found)

    def to_head(self):
        """ A function taking page points straight to head space """
        fit = self.fit()
        return lambda x, y: fit(*self.to_base(x, y))

    def warnings(self):
        fit = self.fit()
        out = []
        if max(abs(s - 1) for s in fit.stretch) > MAX_STRETCH:
            out.append(_("The sheet is stretched more than expected. Was it "
                         "printed at 100%, with no \"fit to page\"?"))
        if fit.worst > MAX_ERROR:
            out.append(_("The marks don't agree with each other. Go back and "
                         "check you aimed at each one, in the right order."))
        return out

    def begin_calibration(self):
        self.calibrating = self.CUT_CROSS

    def cancel_calibration(self):
        self.calibrating = None

    def cut_cross(self):
        self.head.cross()
        self.cross_at = self.head.x, self.head.y
        self.calibrating = self.AIM_AT_CROSS

    def laser_on_cross(self):
        """ Returns the new laser offset. Marks found so far were measured
        with the old one, so aiming starts again.
        """
        if (self.head.x, self.head.y) == self.cross_at:
            #: A double click on "Cut the cross" lands on this button
            raise ValueError(_("Move the laser onto the cross first: the head "
                               "hasn't moved since cutting it."))
        self.laser = (self.cross_at[0] - self.head.x,
                      self.cross_at[1] - self.head.y)
        self.calibrating = None
        self.assumed = False
        self.found = []
        return self.laser
