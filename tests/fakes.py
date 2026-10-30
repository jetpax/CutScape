"""
Stand-ins for hardware, shared by the tests.
"""
import os

SAGA = dict(vid='0483', pid='5750', serial='SAGAIufI@\x16',
            manufacturer='SAGACNC', product='SAGA Cutter Plotter')


def fake_sysfs(root, *devices):
    """ /sys/class/usbmisc/lpN/device -> the interface, whose parent is
    the USB device with its descriptors, as the usblp driver lays it out
    """
    usbmisc = root / 'class' / 'usbmisc'
    usbmisc.mkdir(parents=True)
    for n, d in enumerate(devices):
        dev = root / 'devices' / 'usb3' / ('3-1.%d' % (n + 1))
        iface = dev / ('3-1.%d:1.0' % (n + 1))
        iface.mkdir(parents=True)
        for key, attr in (('vid', 'idVendor'), ('pid', 'idProduct'),
                          ('serial', 'serial'),
                          ('manufacturer', 'manufacturer'),
                          ('product', 'product')):
            if d.get(key) is not None:
                (dev / attr).write_text(d[key] + '\n')
        node = usbmisc / ('lp%d' % (n + 4))
        node.mkdir()
        os.symlink(str(iface), str(node / 'device'))
    return str(root)
