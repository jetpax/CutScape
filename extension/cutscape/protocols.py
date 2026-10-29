"""
Cutter command languages. Each protocol turns moves in device steps into
the bytes the cutter expects. Ported from Inkcut (inkcut/device/protocols upstream).
"""


class Protocol(object):
    #: Identifier used in the .inx and presets
    name = ''
    #: Default device steps per millimetre
    steps_per_mm = 40.0
    #: Coordinates are whole steps
    integer = True

    def __init__(self, speed=0, force=0, pen=1, **options):
        #: 0 means "don't send", leaving the cutter's own setting
        self.speed = speed
        self.force = force
        self.pen = pen
        self.options = options

    @classmethod
    def default_steps(cls, **options):
        """ Steps per millimetre this protocol uses unless told otherwise """
        return cls.steps_per_mm

    def begin(self):
        return ''

    def move(self, x, y, down):
        raise NotImplementedError

    def end(self, x, y):
        """ Lift and park the head at x, y """
        return self.move(x, y, False)

    def _round(self, p):
        if self.integer:
            return int(round(p[0])), int(round(p[1]))
        return p

    def encode(self, polys, park=(0, 0)):
        """ Encode polylines of (x, y) in device steps. The first point of
        each is a pen-up move, the rest are cuts.
        """
        out = [self.begin()]
        for poly in polys:
            last = None
            for i, p in enumerate(poly):
                p = self._round(p)
                if i and p == last:
                    continue
                out.append(self.move(p[0], p[1], i > 0))
                last = p
        out.append(self.end(*self._round(park)))
        return ''.join(out).encode('ascii')


class HPGL(Protocol):
    name = 'hpgl'
    steps_per_mm = 40.0

    def _cmd(self, cmd):
        return cmd + ('\n' if self.options.get('newline') else '')

    def begin(self):
        out = self._cmd('IN;')
        if self.pen:
            out += self._cmd('SP%i;' % self.pen)
        if self.speed:
            out += self._cmd('VS%i;' % self.speed)
        if self.force:
            out += self._cmd('FS%i;' % self.force)
        return out

    def move(self, x, y, down):
        if self.options.get('separate_z'):
            return self._cmd('PD;' if down else 'PU;') + \
                self._cmd('PA%i,%i;' % (x, y))
        return self._cmd('%s%i,%i;' % ('PD' if down else 'PU', x, y))

    def end(self, x, y):
        return self.move(x, y, False) + self._cmd('IN;')


class DMPL(Protocol):
    """ Houston Instrument DMPL and its many vendor dialects.

    `;:` enters DMPL, H homes, A is absolute, L0 is line type 0 and
    EC sets the units: EC1 is 0.001 in, ECN is 0.025 mm. Modes without an
    EC command leave the cutter's default, assumed to be 0.025 mm.
    """
    name = 'dmpl'
    steps_per_mm = 40.0

    INIT = {
        1: ';:HAEC1',
        2: ' ;:ECN A L0 ',
        3: ' ;:H A L0 ',
        4: ' ;:H A L0 ',
        6: 'IN;PA;',
        #: Creation PCut CT-630; based on inkcut/inkcut#382 by LDP-Soft
        7: ';:H A L0 ECN U ',
    }
    #: Without the end sequence mode 7 cutters hang at "Waiting..."
    END = {7: ' U F @ '}
    #: EC1 is 0.001 in
    STEPS = {1: 1000 / 25.4}

    @property
    def mode(self):
        return int(self.options.get('mode', 1))

    @classmethod
    def default_steps(cls, mode=1, **options):
        return cls.STEPS.get(int(mode), cls.steps_per_mm)

    def begin(self):
        if self.mode not in self.INIT:
            raise ValueError("Unknown DMPL mode %r" % self.mode)
        out = self.INIT[self.mode]
        #: Pen 1 is the default; EC<n> (as Inkcut sent) would change units
        if self.pen and self.pen != 1:
            out += 'P%i ' % self.pen
        if self.force:
            out += 'BP%i ' % self.force
        if self.speed:
            out += 'V%i ' % self.speed
        return out

    def move(self, x, y, down):
        if self.mode == 6:
            return '%s%i,%i;' % ('PD' if down else 'PU', x, y)
        return ' %s%i,%i ' % ('D' if down else 'U', x, y)

    def end(self, x, y):
        return self.move(x, y, False) + self.END.get(self.mode, '')


class GPGL(Protocol):
    """ Graphtec GP-GL. Commands are terminated with ETX (0x03), which
    Inkcut omitted (see inkcut/inkcut#441).
    """
    name = 'gpgl'
    steps_per_mm = 20.0
    ETX = '\x03'

    def begin(self):
        out = 'H' + self.ETX
        if self.speed:
            out += '!%i%s' % (self.speed, self.ETX)
        if self.force:
            out += 'FX%i,1%s' % (self.force, self.ETX)
        return out

    def move(self, x, y, down):
        return '%s%i,%i%s' % ('D' if down else 'M', x, y, self.ETX)


class CAMMGL1(Protocol):
    """ Roland CAMM-GL I """
    name = 'camm-gl1'
    steps_per_mm = 40.0

    def begin(self):
        out = 'IN;'
        if self.pen:
            out += 'SP%i;' % self.pen
        if self.speed:
            out += 'VS%i;' % self.speed
        if self.force:
            out += 'FS%i;' % self.force
        return out

    def move(self, x, y, down):
        return '%s%i,%i;' % ('D' if down else 'M', x, y)


class GCode(Protocol):
    """ G-code in millimetres. With z_down/z_up set the tool is lifted on
    the Z axis, otherwise G0/G1 alone imply up/down.
    """
    name = 'gcode'
    steps_per_mm = 1.0
    integer = False

    def __init__(self, *args, **kwargs):
        super(GCode, self).__init__(*args, **kwargs)
        self._down = None

    def _num(self, v):
        return ('%.3f' % v).rstrip('0').rstrip('.')

    def begin(self):
        self._down = None
        return 'G21\nG90\n'

    def move(self, x, y, down):
        out = ''
        z_mode = self.options.get('z_mode')
        if z_mode and down != self._down:
            z = self.options.get('z_down' if down else 'z_up', 0)
            out += 'G0%i Z%s\n' % (1 if down else 0, self._num(z))
        self._down = down
        line = 'G0%i X%s Y%s' % (1 if down else 0, self._num(x), self._num(y))
        if down and self.speed:
            #: speed is cm/s, feed rate is mm/min
            line += ' F%s' % self._num(self.speed * 600)
        return out + line + '\n'

    def end(self, x, y):
        return self.move(x, y, False) + 'M2\n'


PROTOCOLS = {p.name: p for p in (HPGL, DMPL, GPGL, CAMMGL1, GCode)}


def get(name, **kwargs):
    try:
        cls = PROTOCOLS[name]
    except KeyError:
        raise ValueError("Unknown protocol %r" % name)
    return cls(**kwargs)
