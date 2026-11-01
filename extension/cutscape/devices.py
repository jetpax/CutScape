"""
Device presets, ported from Inkcut (inkcut/device/drivers/manifest.enaml upstream) plus
unmerged upstream PRs (credited inline).

A preset fixes the command language, resolution and axis orientation of a
cutter. In material space x runs across the material and y along the
feed; width and length are in millimetres (0 = unknown).

Orientation maps material space to the device's axes: swap_xy exchanges
them, invert_x/invert_y reflect the job in place (it stays at the origin).
serial is the baud rate and flow control Add Cutter sets up when it finds
the cutter on a serial port.
laser is where a laser pointer's dot is from the blade, along the device's
axes in millimetres, until the user calibrates it (see align). blade, force
and speed are its usual blade offset and ranges, for Add Cutter to mention.
Inkcut's "swap_xy" was a 90 degree rotation, which is swap_xy + invert_y
here. Orientations marked unverified are translated from Inkcut and have
not been checked against hardware.
"""
from collections import OrderedDict

IN = 25.4

#: Value of the "custom" choice in the dialog
CUSTOM = 'custom'


def _preset(name, protocol='hpgl', width=0.0, length=0.0, steps=None,
            swap_xy=False, invert_x=False, invert_y=False, serial=None,
            laser=None, blade=None, force=None, speed=None, **options):
    return name, dict(protocol=protocol, width=width, length=length,
                      steps=steps, swap_xy=swap_xy, invert_x=invert_x,
                      invert_y=invert_y, serial=serial, laser=laser,
                      blade=blade, force=force, speed=speed, options=options)


#: What GRBL speaks over USB
GRBL = dict(baud='115200', flow='none')

def saga(speed):
    """ Secabo's SAGA-built cutters, from the driver data DrawCut ships for
    them: X runs along the feed and Y to the left from an origin at the front
    right, with the laser's dot 2.4 mm along X and 33 mm along Y from the
    blade. Unverified on hardware.
    """
    return dict(swap_xy=True, invert_y=True, laser=(2.4, 33.0), blade=0.3,
                force='50-750 g', speed=speed)


PRESETS = OrderedDict([
    _preset('ANAgraph AE-70e', width=760),
    #: The common desktop CNC, with a drag knife or pen in the spindle
    #: clamp: 300 x 180 mm, X to the right and Y away from you, as material
    #: space. Unverified on hardware.
    _preset('CNC 3018 (GRBL)', 'gcode', width=300, length=180,
            serial=GRBL),
    #: Start/end sequence from inkcut/inkcut#382 by LDP-Soft
    _preset('Creation PCut CT-630', 'dmpl', width=610, dmpl_mode=7),
    _preset('GCC Bobcat 60', width=600),
    _preset('Graphtec 8000-130', width=1300, length=7000),
    _preset('Graphtec CE5000-60', width=600),
    _preset('Ioline Classic', width=600),
    _preset('KNK ACS Eagle/Maxx', width=28 * IN),
    _preset('Mimaki CG-60ST', width=600),
    _preset('Redsail RS1360C', width=1200),
    #: Inkcut's "720cm" was a typo
    _preset('Rheinstern LB-720A', width=720),
    _preset('Roland CM-24', width=610),
    _preset('Roland CX-24', width=600, length=500),
    #: Cutting width, as for the Stika below; the media can be wider
    _preset('Roland PNC-900', 'camm-gl1', width=11 * IN),
    _preset('Roland PNC-950', 'camm-gl1', width=24 * IN),
    _preset('Roland PNC-1000', 'camm-gl1', width=468),
    #: unverified
    _preset('Roland Stika SV-8', width=157.48, swap_xy=True, invert_y=True),
    _preset('Roland Stika SV-12', width=248.92, swap_xy=True,
            invert_y=True),
    _preset('Roland Stika SV-15', width=337.82, swap_xy=True,
            invert_y=True),
    _preset('Secabo C60 II / C IV', width=600),
    _preset('Secabo C60 IV', width=630, **saga('10-600 mm/s')),
    _preset('Secabo C120 IV', width=1260, **saga('10-600 mm/s')),
    _preset('Secabo S60 / T60', width=630, **saga('10-960 mm/s')),
    _preset('Secabo S120 / T120', width=1260, **saga('10-960 mm/s')),
    _preset('Secabo S160 / T160', width=1660, **saga('10-960 mm/s')),
    _preset('SeikiTech SK-870T', width=860),
    _preset('Sekonic SPL-450', width=440, length=300),
    #: unverified; Cameo 4 Plus from inkcut/inkcut#441 by lagnat
    _preset('Silhouette Cameo 4', 'gpgl', width=12 * IN, length=3000,
            swap_xy=True, invert_x=True),
    _preset('Silhouette Cameo 4 Plus', 'gpgl', width=15 * IN, length=3000,
            swap_xy=True, invert_x=True),
    _preset('Silhouette Portrait', 'gpgl', width=8 * IN, length=3000),
    #: From inkcut/inkcut#442 by lukebmay
    _preset('Summa D760', 'dmpl', width=740, dmpl_mode=1),
    _preset('Summa D120R', width=1200),
    #: Inkcut's "575cm" was a typo
    _preset('Summa T600', width=575),
    _preset('US Cutter Laser Point', width=610),
    _preset('US Cutter Refine MH-721', width=600),
    _preset('US Cutter Refine MH-1351', width=1200),
    #: From inkcut/inkcut#428 by mcmartnor: DMPL mode 3 with the feed on
    #: the first axis. HPGL is unreliable on this firmware.
    _preset('VEVOR KH-720', 'dmpl', width=630, swap_xy=True, invert_y=True,
            dmpl_mode=3),
    _preset('VEVOR KH-870', 'dmpl', width=780, swap_xy=True, invert_y=True,
            dmpl_mode=3),
    _preset('VEVOR Smart 1', width=304.8, length=347.8),
    _preset('Vision VE-810 engraver', width=10 * IN, length=8 * IN),
])


def get(name):
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError("Unknown device preset %r" % name)
