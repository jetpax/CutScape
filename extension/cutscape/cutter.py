"""
The cutter you added. Extensions > CutScape > Add Cutter finds the cutter
that is plugged in, sets up what the system needs to reach it, and
remembers its model and connection, so Cut can send to it with no
settings: "The cutter you added" on its Device and Connection tabs.

How a cutter is reached, best first:
  device  the USB printer device Linux's usblp driver makes (/dev/usb/lpN),
          found again by its USB id; a udev rule gives the user access
  uri     a USB printer CUPS can see with no queue yet: Add Cutter makes a
          raw queue for it (macOS, or Linux without usblp)
  queue   a printer queue that already reaches the cutter
  serial  a USB serial adapter, found again by its USB id
Windows has no command to add a printer, so a USB cutter is added in
Settings first and its name entered in the Address field.
"""
import json
import os
import re
import shlex
import sys
from urllib.parse import unquote

from . import admin, devices, printers, transports
from .i18n import _

#: The choice on Cut's Device and Connection tabs
ADDED = 'added'

UDEV_RULES = '/etc/udev/rules.d/70-cutscape.rules'

#: Serial ports that are never a cutter
NOT_CUTTERS = re.compile(r'bluetooth|debug-console|wlan', re.I)

#: Nor are printers that speak IPP
IPP = re.compile(r'^(ipps?|dnssd|https?)://', re.I)


class CutterError(Exception):
    pass


# -----------------------------------------------------------------------------
# Remembering it
# -----------------------------------------------------------------------------
def config_path():
    if os.environ.get('CUTSCAPE_CONFIG'):
        return os.environ['CUTSCAPE_CONFIG']
    if sys.platform == 'win32':
        base = os.path.join(os.environ.get('APPDATA') or
                            os.path.expanduser('~'), 'CutScape')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support/CutScape')
    else:
        base = os.path.join(os.environ.get('XDG_CONFIG_HOME') or
                            os.path.expanduser('~/.config'), 'cutscape')
    return os.path.join(base, 'cutter.json')


def load():
    """ The cutter you added, or None """
    try:
        with open(config_path(), encoding='utf-8') as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or not saved.get('transport'):
        return None
    return saved


def save(cutter):
    path = os.path.abspath(config_path())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cutter, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def use(o, sending):
    """ Fill in "The cutter you added" in Cut's options o """
    if ADDED not in (o.preset, o.transport):
        return
    added = load()
    if o.preset == ADDED:
        #: Without one, preview with the custom settings
        o.preset = added['model'] if added else devices.CUSTOM
    if o.transport == ADDED and sending:
        if not added:
            raise CutterError(_(
                "No cutter has been added yet. Plug it in and switch it on, "
                "then use Extensions > CutScape > Add Cutter. Or choose how "
                "to reach it on Cut's Connection tab."))
        o.transport = added['transport']
        for key in ('printer', 'device', 'port'):
            setattr(o, key, added.get(key, ''))


# -----------------------------------------------------------------------------
# Finding it
# -----------------------------------------------------------------------------
def _usb_label(d):
    return ' '.join(x for x in (d['manufacturer'], d['product']) if x)


def _uri_label(uri):
    """ "SAGACNC SAGA Cutter Plotter" from usb://SAGACNC/SAGA%20Cutter... """
    m = re.match(r'usb://([^/?]*)/([^?]*)', uri)
    return unquote('%s %s' % m.groups()) if m else ''


class Scan(object):
    """ What is connected now, read once """

    def __init__(self, sysfs=None):
        self.sysfs = sysfs
        self.usb = transports.usb_printers(sysfs)
        self.queues = queues()
        self.uris, self.refused = _usb_uris()
        self.ports = transports.serial_ports()

    def candidates(self, saved=None):
        """ Connections the cutter could be using, of the best kind found """
        if self.usb:
            return [dict(kind='device', address=transports.usb_id(d),
                         label=_usb_label(d), usb=d) for d in self.usb]
        known = set(self.queues.values())
        loose = [u for u in self.uris if u not in known]
        if loose:
            return [dict(kind='uri', address=u, label=_uri_label(u))
                    for u in loose]
        #: Plugged in, with the queue Add Cutter made for it before
        mine = {printers.DEFAULT_QUEUE, (saved or {}).get('printer')}
        ready = [dict(kind='queue', address=q, label=_uri_label(u))
                 for q, u in sorted(self.queues.items())
                 if q in mine and u in self.uris]
        if ready:
            return ready
        return [dict(kind='serial', address=transports.usb_id(p),
                     label=p['description'], path=p['path'])
                for p in self.ports if p['vid']]

    def nothing_found(self):
        lines = [_("No cutter found. Check that it is plugged in and "
                   "switched on, then try again.")]
        if self.refused:
            lines.append(_(
                "CUPS wouldn't list USB printers for this account, so a "
                "cutter that needs a printer queue can't be found."))
            if sys.platform.startswith('linux'):
                lines[-1] += ' ' + _(
                    "To allow it: sudo usermod -aG lpadmin $USER, then log "
                    "out and back in.")
        if sys.platform == 'win32':
            lines.append(_(
                "A USB cutter must be added to Windows first: Settings > "
                "Bluetooth & devices > Printers & scanners, with the "
                "Generic / Text Only driver. Then enter the printer's name "
                "in the Address field."))
        others = ['  %s  %s' % (q, u) for q, u in sorted(self.queues.items())
                  if not IPP.match(u)]
        others += ['  %s  %s' % (p['path'], p['description'])
                   for p in self.ports if not NOT_CUTTERS.search(p['path'])]
        if others:
            lines.append(_("If it is connected another way, enter one of "
                           "these in the Address field:") + '\n' +
                         '\n'.join(others))
        if sys.platform.startswith('linux') and not _has_pyserial():
            lines.append(_("Cutters on a serial port need pyserial: sudo "
                           "apt install python3-serial"))
        return '\n\n'.join(lines)

    def lookup(self, address):
        """ The connection for an address entered in the dialog """
        if '://' in address:
            return dict(kind='uri', address=address,
                        label=_uri_label(address))
        if address.lower().startswith('usb:'):
            try:
                path = transports.resolve_device(address, self.sysfs)
            except transports.TransportError as e:
                try:
                    port = transports.resolve_serial(address)
                except transports.TransportError:
                    raise CutterError(str(e))
                return dict(kind='serial', address=address, label='',
                            path=port)
            usb = [d for d in self.usb if d['path'] == path][0]
            return dict(kind='device', address=address,
                        label=_usb_label(usb), usb=usb)
        if address in self.queues:
            return dict(kind='queue', address=address,
                        label=_uri_label(self.queues[address]))
        if re.match(r'^COM\d+$', address, re.I) or \
                address.startswith(('/dev/tty', '/dev/cu.')):
            return dict(kind='serial', address=address, label='',
                        path=address)
        if address.startswith(('/', '~')):
            path = os.path.expanduser(address)
            if not os.path.exists(path):
                raise CutterError(_("{path} isn't there. Is the cutter "
                                    "plugged in and switched on?").format(
                                        path=path))
            return dict(kind='device', address=address, label='')
        raise CutterError(_(
            "CutScape doesn't know {address}. Enter the name of a printer "
            "queue, a usb://… address, a USB id such as usb:0483:5750, a "
            "device file or a serial port.").format(address=address))


def queues():
    """ {name: device URI} of the printer queues; on Windows, the printers
    (with no URI)
    """
    if sys.platform != 'win32':
        return transports.queue_devices()
    try:
        import win32print
    except ImportError:
        return {}
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return {p[2]: '' for p in win32print.EnumPrinters(flags)}


def _usb_uris():
    """ (USB printer URIs CUPS can see, whether CUPS refused to list them) """
    try:
        code, out, err = printers._cups(
            ['lpinfo', '--include-schemes', 'usb', '-v'])
    except printers.PrinterError:
        #: No CUPS
        return [], False
    if code:
        return [], bool(printers._refused(err))
    return [line.split(None, 1)[1] for line in out.splitlines()
            if line.startswith('direct usb://')], False


def _has_pyserial():
    try:
        import serial  # noqa: F401
    except ImportError:
        return False
    return True


# -----------------------------------------------------------------------------
# Setting it up
# -----------------------------------------------------------------------------
def _writable(path):
    return os.access(path, os.W_OK)


def _grant_access(usb, path):
    """ Install a udev rule so the logged-in user can write to the cutter.
    Returns (note, whether it worked).
    """
    script = ' && '.join([
        "printf '%%s\\n' %s > %s" % (shlex.quote(transports.udev_rule(usb)),
                                     UDEV_RULES),
        'udevadm control --reload-rules',
        'udevadm trigger --subsystem-match=usbmisc',
        'udevadm settle --timeout=10'])
    error = None
    if admin.available():
        try:
            admin.run(script, _("CutScape needs to let you use your "
                                "cutter."))
        except admin.AdminError as e:
            error = str(e)
        else:
            if _writable(path):
                return _("Gave you access to it with a udev rule "
                         "({file}).").format(file=UDEV_RULES), True
            return _("Added a udev rule ({file}) to give you access. Unplug "
                     "the cutter and plug it back in to apply it.").format(
                         file=UDEV_RULES), False
    note = _("You can't write to {path} yet. To fix that, run this in a "
             "terminal, then unplug the cutter and plug it back in:\n"
             "  {command}").format(path=path, command='sudo sh -c ' +
                                   shlex.quote(script))
    if error:
        note = error + '\n' + note
    return note, False


def _connect(c, scan, saved):
    """ Make the connection c work. Returns (the settings Cut needs, what
    was done, whether it is ready)
    """
    kind, address = c['kind'], c['address']
    notes, ready = [], True
    if kind == 'device':
        path = c['usb']['path'] if c.get('usb') else \
            os.path.expanduser(address)
        if not _writable(path):
            if c.get('usb') and sys.platform.startswith('linux'):
                note, ready = _grant_access(c['usb'], path)
            else:
                note, ready = _("You can't write to {path} yet.").format(
                    path=path), False
            notes.append(note)
        return dict(transport='device', device=address), notes, ready
    if kind == 'serial':
        notes.append(_("Set its baud rate and flow control on Cut's "
                       "Connection tab."))
        return dict(transport='serial', port=address), notes, ready
    if kind == 'uri':
        queue = printers.DEFAULT_QUEUE
        before = scan.queues.get(queue)
        if before != address:
            printers.create(queue, address)
            notes.append((_("Pointed the printer queue {queue} at it.")
                          if before else
                          _("Created the printer queue {queue} for it."))
                         .format(queue=queue))
        return (dict(transport='printer', printer=queue, queue_created=True),
                notes, ready)
    #: An existing queue: restart it if it has stopped
    if transports.queue_state(address) == 'disabled':
        stuck = printers.restart(address)
        notes.append((_("Its printer queue {queue} had stopped: restarted it "
                        "and cleared {count} stuck job(s).") if stuck else
                      _("Its printer queue {queue} had stopped: restarted "
                        "it.")).format(queue=address, count=stuck))
    made = address == printers.DEFAULT_QUEUE or (
        (saved or {}).get('printer') == address and
        saved.get('queue_created', False))
    return (dict(transport='printer', printer=address, queue_created=made),
            notes, ready)


def _describe(conn):
    if conn['transport'] == 'device':
        return _("USB, as {device}").format(device=conn['device'])
    if conn['transport'] == 'serial':
        return _("the serial port {port}").format(port=conn['port'])
    return _("the printer queue {queue}").format(queue=conn['printer'])


def add(model, address='', sysfs=None):
    """ Find the cutter, set up its connection and remember it with its
    model. Returns (a message for the user, whether it is ready to use).
    """
    model = model or devices.CUSTOM
    if model != devices.CUSTOM:
        devices.get(model)
    saved = load()
    scan = Scan(sysfs)
    address = address.strip()
    if address:
        c = scan.lookup(address)
    else:
        found = scan.candidates(saved)
        if not found:
            raise CutterError(scan.nothing_found())
        if len(found) > 1:
            raise CutterError(
                _("More than one possible cutter is connected. Enter the "
                  "address of yours in the Address field and click Apply "
                  "again:") + '\n' + '\n'.join(
                    '  %s  %s' % (f['address'], f['label']) for f in found))
        c = found[0]
    try:
        conn, notes, ready = _connect(c, scan, saved)
    except printers.PrinterError as e:
        raise CutterError(str(e))
    cutter = dict(model=model, label=c['label'], transport='', printer='',
                  device='', port='', queue_created=False)
    cutter.update(conn)
    save(cutter)

    name = model if model != devices.CUSTOM else _("cutter")
    lines = [_("Added your {model}, connected through {connection}.").format(
        model=name, connection=_describe(conn))]
    if c['label']:
        lines[0] += ' ' + _("It calls itself {device}.").format(
            device=c['label'])
    lines += notes
    if model == devices.CUSTOM:
        lines.append(_("Set its language and steps per mm on Cut's Device "
                       "tab."))
    if ready:
        lines.append(_("To check it, open Extensions > CutScape > Cut, "
                       "choose Send a 50 mm test square and click Apply."))
    return '\n\n'.join(lines), ready


def remove():
    """ Forget the cutter you added, and remove the printer queue Add
    Cutter made for it. Returns a message for the user.
    """
    saved = load()
    if not saved:
        return _("No cutter has been added.")
    lines = [_("Removed the cutter you added.")]
    queue = saved.get('printer')
    if saved.get('queue_created') and queue and sys.platform != 'win32' \
            and queue in transports.queue_devices():
        try:
            printers.remove(queue)
        except printers.PrinterError as e:
            raise CutterError(str(e))
        lines.append(_("Removed its printer queue {queue}.").format(
            queue=queue))
    os.remove(config_path())
    return ' '.join(lines)
