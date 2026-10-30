"""
Ways to get the encoded job to the cutter. Everything here uses the
standard library, except serial which needs pyserial (bundled with
Inkscape on Windows and macOS, python3-serial on Linux).
"""
import errno
import glob
import os
import re
import select
import subprocess
import sys
import time

from .i18n import _

TRANSPORTS = ('file', 'printer', 'serial', 'device')

#: Give up when the cutter accepts nothing for this long. Not a limit on the
#: whole job: a cutter takes data only as fast as it cuts, which can be
#: minutes, but it never stops taking it for this long unless it is offline,
#: paused, out of material or unplugged.
STALL_TIMEOUT = 30.0

SYSFS = '/sys'


class TransportError(Exception):
    pass


def to_file(data, path):
    path = os.path.expanduser(path)
    if not path:
        raise TransportError(_("No output file set"))
    with open(path, 'wb') as f:
        f.write(data)
    return path


def printer_queues():
    """ Names of the CUPS queues, or [] if CUPS isn't available """
    try:
        out = subprocess.run(['lpstat', '-e'], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return out.stdout.decode(errors='replace').split()


def _run(args, timeout=10):
    """ stdout of a command as text, or None if it can't be run """
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=timeout,
                           env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.decode(errors='replace')


def queue_state(queue):
    """ 'enabled', 'disabled' or None if unknown, from lpstat -p. A
    disabled queue accepts jobs and holds them, so the cutter never moves.
    """
    out = _run(['lpstat', '-p', queue])
    if not out:
        return None
    if re.search(r'\bdisabled\b', out):
        return 'disabled'
    return 'enabled'


def queue_devices():
    """ {queue: device URI} from lpstat -v """
    out = _run(['lpstat', '-v']) or ''
    found = {}
    for line in out.splitlines():
        m = re.match(r'device for (\S+):\s*(\S+)', line)
        if m:
            found[m.group(1)] = m.group(2)
    return found


def to_printer(data, queue):
    """ Spool raw to a CUPS queue with lp. The queue should be a raw queue
    (no driver) so CUPS passes the commands through untouched.

    CUPS takes the job and returns at once, so a cutter that is offline
    shows up only as a job that never finishes; a stopped queue at least
    is caught here.
    """
    if sys.platform == 'win32':
        return _to_win32_printer(data, queue)
    if not queue:
        queues = printer_queues()
        raise TransportError(
            _("No printer queue set. Available queues: {queues}").format(
                queues=', '.join(queues) or _("none found")))
    if queue_state(queue) == 'disabled':
        raise TransportError(
            _("The printer queue {queue} is stopped, so the job would wait "
              "in it. Restart it with Extensions > CutScape > Printer Queue, "
              "or run: cupsenable {queue}").format(queue=queue))
    try:
        p = subprocess.run(['lp', '-d', queue, '-o', 'raw'], input=data,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, env=dict(os.environ, LC_ALL='C'))
    except OSError as e:
        raise TransportError(_("Could not run lp: {error}").format(error=e))
    if p.returncode:
        raise TransportError(_("lp failed ({code}): {error}").format(
            code=p.returncode,
            error=p.stderr.decode(errors='replace').strip()))
    m = re.search(r'request id is (\S+)', p.stdout.decode(errors='replace'))
    return m.group(1) if m else queue


def _to_win32_printer(data, queue):
    try:
        import win32print
    except ImportError:
        raise TransportError(_("Printing on Windows needs pywin32"))
    h = win32print.OpenPrinter(queue or win32print.GetDefaultPrinter())
    try:
        win32print.StartDocPrinter(h, 1, ("CutScape job", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)
    return queue


def to_serial(data, port, baud=9600, flow='xonxoff', ack=False,
              timeout=STALL_TIMEOUT):
    """ Write to a serial port and wait for it to drain. Flow control is
    done by the driver: 'xonxoff', 'rtscts', 'dsrdtr' or 'none'.

    With ack, data is sent a line at a time, waiting for the controller's
    "ok" after each (G-code controllers such as GRBL); based on the idea in
    inkcut/inkcut#388 by karliss.
    """
    try:
        import serial
    except ImportError:
        raise TransportError(
            _("Serial output needs pyserial (python3-serial on Linux)"))
    if not port:
        raise TransportError(_("No serial port set"))
    port = resolve_serial(port)
    try:
        with serial.Serial(port, int(baud), timeout=timeout,
                           write_timeout=timeout,
                           xonxoff=flow == 'xonxoff',
                           rtscts=flow == 'rtscts',
                           dsrdtr=flow == 'dsrdtr') as s:
            if ack:
                _send_acked(s, data, timeout)
            else:
                _write_serial(s, data, timeout)
            _drain_serial(s, timeout)
    except serial.SerialTimeoutException:
        raise TransportError(_stalled(port, timeout))
    except serial.SerialException as e:
        raise TransportError(_("Serial port {port}: {error}").format(
            port=port, error=e))
    return port


def _write_serial(s, data, timeout, chunk=256):
    """ Write in chunks so write_timeout measures a stall, not the job """
    view = memoryview(data)
    while view:
        part = view[:chunk]
        n = s.write(part)
        #: Old pyserial returns None; a timeout raises instead
        view = view[len(part) if n is None else n:]


def _drain_serial(s, timeout):
    """ Wait for the OS buffer to empty, giving up if it stops shrinking """
    try:
        waiting = s.out_waiting
    except (AttributeError, OSError, NotImplementedError):
        s.flush()
        return
    last, since = waiting, time.monotonic()
    while waiting:
        time.sleep(0.05)
        waiting = s.out_waiting
        if waiting < last:
            last, since = waiting, time.monotonic()
        elif time.monotonic() - since > timeout:
            raise TransportError(_stalled(s.port, timeout))


def _stalled(where, seconds):
    return _("{where} stopped accepting data for {seconds:.0f} s. Is the "
             "cutter online, with material loaded? Check its panel.").format(
        where=where, seconds=seconds)


def _send_acked(s, data, timeout):
    for line in data.decode('ascii').splitlines():
        line = line.split(';', 1)[0].strip()
        if not line:
            continue
        s.write(line.encode('ascii') + b'\n')
        while True:
            reply = s.readline()
            if not reply:
                raise TransportError(
                    _("No reply to {command} within {seconds} s").format(
                        command=line, seconds=timeout))
            reply = reply.decode('ascii', errors='replace').strip().lower()
            if reply.startswith('ok'):
                break
            if reply.startswith(('error', 'alarm')):
                raise TransportError("%r: %s" % (line, reply))


def to_device(data, path, timeout=STALL_TIMEOUT):
    """ Write straight to a device node, eg /dev/usb/lp0 (USB printer
    class cutters on Linux) or a parallel port. path may also be a USB
    id, usb:VID:PID or usb:VID:PID:SERIAL, which is looked up each time so
    it survives the lp number changing.

    Writes don't block: if the cutter takes nothing for timeout seconds
    the job is abandoned with an error, instead of hanging Inkscape.
    """
    if not path:
        raise TransportError(_("No device file set"))
    path = resolve_device(path)
    flags = os.O_WRONLY | getattr(os, 'O_NONBLOCK', 0)
    try:
        fd = os.open(path, flags)
    except OSError as e:
        raise TransportError(_device_error(path, e))
    sent = 0
    try:
        view = memoryview(data)
        since = time.monotonic()
        while view:
            try:
                n = os.write(fd, view)
            except BlockingIOError:
                n = 0
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EINTR):
                    n = 0
                else:
                    raise TransportError(_device_error(path, e))
            if n:
                view = view[n:]
                sent += n
                since = time.monotonic()
                continue
            left = timeout - (time.monotonic() - since)
            if left <= 0:
                raise TransportError(
                    _stalled(path, timeout) + ' ' +
                    _("{sent} of {total} bytes were sent.").format(
                        sent=sent, total=len(data)))
            try:
                select.select([], [fd], [], min(left, 1.0))
            except (OSError, ValueError):
                time.sleep(min(left, 0.1))
    finally:
        os.close(fd)
    return path


def _device_error(path, e):
    if e.errno in (errno.EACCES, errno.EPERM):
        return _("No permission to write to {path}. Add a udev rule for the "
                 "cutter (choose \"Find connected cutters\" for one), or "
                 "join the lp group: sudo usermod -aG lp $USER, then log out "
                 "and back in.").format(path=path)
    if e.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
        return _("{path} isn't there. Is the cutter plugged in and switched "
                 "on?").format(path=path)
    return _("Could not write to {path}: {error}").format(path=path, error=e)


# -----------------------------------------------------------------------------
# Finding cutters
# -----------------------------------------------------------------------------
def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return ''


def usb_printers(sysfs=None):
    """ USB printer-class devices the Linux usblp driver has bound, as
    dicts with path, vid, pid, serial, manufacturer and product. Empty on
    other systems.
    """
    sysfs = sysfs or SYSFS
    found = []
    for node in sorted(glob.glob(os.path.join(sysfs, 'class', 'usbmisc',
                                              'lp*'))):
        name = os.path.basename(node)
        #: device -> the USB interface; its parent holds the descriptors
        usb = os.path.dirname(os.path.realpath(os.path.join(node, 'device')))
        vid = _read(os.path.join(usb, 'idVendor'))
        if not vid:
            continue
        found.append(dict(
            path='/dev/usb/' + name,
            vid=vid.lower(),
            pid=_read(os.path.join(usb, 'idProduct')).lower(),
            serial=_read(os.path.join(usb, 'serial')),
            manufacturer=_read(os.path.join(usb, 'manufacturer')),
            product=_read(os.path.join(usb, 'product'))))
    return found


def usb_id(d):
    """ The usb:VID:PID[:SERIAL] string that finds this device again """
    ident = 'usb:%s:%s' % (d['vid'], d['pid'])
    #: Only printable serials are worth matching on; some cutters report
    #: junk bytes there
    if d.get('serial') and re.match(r'^[\x21-\x7e]+$', d['serial']):
        ident += ':' + d['serial']
    return ident


def resolve_device(path, sysfs=None):
    """ A device path for path, looking up usb:VID:PID[:SERIAL] ids """
    if not path.lower().startswith('usb:'):
        return os.path.expanduser(path)
    parts = path.split(':')[1:]
    if len(parts) < 2:
        raise TransportError(
            _("A USB id looks like usb:0483:5750, optionally followed by "
              ":SERIAL; got {path}").format(path=path))
    vid, pid = parts[0].lower(), parts[1].lower()
    serial = ':'.join(parts[2:])
    matches = [d for d in usb_printers(sysfs)
               if d['vid'] == vid and d['pid'] == pid
               and (not serial or d['serial'] == serial)]
    if not matches:
        raise TransportError(
            _("No USB printer {path} is connected. Is the cutter plugged in "
              "and switched on?").format(path=path))
    if len(matches) > 1:
        raise TransportError(
            _("More than one USB printer matches {path}: {paths}. Add the "
              "serial number to tell them apart.").format(
                path=path, paths=', '.join(d['path'] for d in matches)))
    return matches[0]['path']


def serial_ports():
    """ Serial ports pyserial can see, as dicts with path, description and
    the USB vid/pid/serial when there is one. Empty without pyserial.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    found = []
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        d = dict(path=p.device, description=p.description or '',
                 vid='', pid='', serial=p.serial_number or '')
        if p.vid is not None:
            d['vid'], d['pid'] = '%04x' % p.vid, '%04x' % p.pid
        found.append(d)
    return found


def resolve_serial(port):
    """ A port for port, looking up usb:VID:PID[:SERIAL] ids """
    if not port.lower().startswith('usb:'):
        return port
    parts = port.split(':')[1:]
    vid, pid = (parts + ['', ''])[0].lower(), (parts + ['', ''])[1].lower()
    serial = ':'.join(parts[2:])
    matches = [p for p in serial_ports()
               if p['vid'] == vid and p['pid'] == pid
               and (not serial or p['serial'] == serial)]
    if len(matches) != 1:
        raise TransportError(
            _("No single serial port matches {port}; found {count}.").format(
                port=port, count=len(matches)))
    return matches[0]['path']


def unconfigured_usb_uris():
    """ USB printer URIs CUPS can see, configured or not (lpinfo) """
    out = _run(['lpinfo', '--include-schemes', 'usb', '-v'], timeout=20)
    return [line.split(None, 1)[1] for line in (out or '').splitlines()
            if line.startswith('direct usb://')]


def udev_rule(d):
    """ A rule giving the logged-in user access, and a stable /dev/cutter """
    rule = ('SUBSYSTEM=="usbmisc", KERNEL=="lp[0-9]*", '
            'ATTRS{idVendor}=="%s", ATTRS{idProduct}=="%s"' % (
                d['vid'], d['pid']))
    if d.get('product'):
        rule += ', ATTRS{product}=="%s"' % d['product'].replace('"', '')
    return rule + ', SYMLINK+="cutter", TAG+="uaccess"'


def find_cutters(sysfs=None):
    """ A report of everything a cutter could be connected through """
    lines = []
    printers = usb_printers(sysfs)
    if printers:
        lines.append(_("USB cutters (choose Device file):"))
        for d in printers:
            name = ' '.join(x for x in (d['manufacturer'], d['product']) if x)
            lines.append('  %s  %s  %s' % (d['path'], usb_id(d), name))
            if not os.access(d['path'], os.W_OK):
                lines.append('    ' + _(
                    "You can't write to it yet. To fix that for everyone "
                    "who logs in, and to always find it at /dev/cutter, "
                    "save this line as /etc/udev/rules.d/70-cutter.rules:"))
                lines.append('    ' + udev_rule(d))
                lines.append('    ' + _(
                    "then run: sudo udevadm control --reload && sudo udevadm "
                    "trigger"))
        lines.append('')
    queues = queue_devices()
    if queues:
        lines.append(_("Printer queues (choose Printer queue):"))
        for name, uri in sorted(queues.items()):
            state = queue_state(name)
            note = '  ' + _("(stopped: restart it with Printer "
                            "Queue)") if state == 'disabled' else ''
            lines.append('  %s  %s%s' % (name, uri, note))
        lines.append('')
    known = set(queues.values())
    loose = [u for u in unconfigured_usb_uris() if u not in known]
    if loose:
        lines.append(_("USB printers with no queue yet. To make one, use "
                       "Extensions > CutScape > Printer Queue (enter the "
                       "address if there are several), or run:"))
        for uri in loose:
            lines.append('  ' + uri)
            lines.append("    sudo lpadmin -p cutter -E -v '%s' -m raw" % uri)
        lines.append('')
    ports = serial_ports()
    if ports:
        lines.append(_("Serial ports (choose Serial port):"))
        for p in ports:
            ident = '  usb:%s:%s' % (p['vid'], p['pid']) if p['vid'] else ''
            lines.append('  %s  %s%s' % (p['path'], p['description'], ident))
        lines.append('')
    if not lines:
        return _("No cutters found. Check that it is plugged in and "
                 "switched on. On Linux, serial ports need pyserial: sudo "
                 "apt install python3-serial.")
    return '\n'.join(lines).rstrip()


def send(data, transport, **options):
    """ Send data with the named transport; returns where it went """
    if transport == 'file':
        return to_file(data, options.get('file', ''))
    if transport == 'printer':
        return to_printer(data, options.get('printer', ''))
    if transport == 'serial':
        return to_serial(data, options.get('port', ''),
                         options.get('baud', 9600),
                         options.get('flow', 'xonxoff'),
                         options.get('ack', False))
    if transport == 'device':
        return to_device(data, options.get('device', ''))
    raise TransportError("Unknown transport %r" % transport)
