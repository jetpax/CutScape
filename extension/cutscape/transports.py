"""
Ways to get the encoded job to the cutter. Everything here uses the
standard library, except serial which needs pyserial (bundled with
Inkscape on Windows and macOS, python3-serial on Linux).
"""
import os
import subprocess
import sys

from .i18n import _

TRANSPORTS = ('file', 'printer', 'serial', 'device')


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


def to_printer(data, queue):
    """ Spool raw to a CUPS queue with lpr. The queue should be a raw queue
    (no driver) so CUPS passes the commands through untouched.
    """
    if sys.platform == 'win32':
        return _to_win32_printer(data, queue)
    if not queue:
        queues = printer_queues()
        raise TransportError(
            _("No printer queue set. Available queues: {queues}").format(
                queues=', '.join(queues) or _("none found")))
    try:
        p = subprocess.run(['lpr', '-P', queue, '-o', 'raw'], input=data,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60)
    except OSError as e:
        raise TransportError(_("Could not run lpr: {error}").format(error=e))
    if p.returncode:
        raise TransportError(_("lpr failed ({code}): {error}").format(
            code=p.returncode,
            error=p.stderr.decode(errors='replace').strip()))
    return queue


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
              timeout=60):
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
    try:
        with serial.Serial(port, int(baud), timeout=timeout,
                           write_timeout=None,
                           xonxoff=flow == 'xonxoff',
                           rtscts=flow == 'rtscts',
                           dsrdtr=flow == 'dsrdtr') as s:
            if ack:
                _send_acked(s, data, timeout)
            else:
                s.write(data)
            #: Wait for the OS buffer to drain before closing
            s.flush()
    except serial.SerialException as e:
        raise TransportError(_("Serial port {port}: {error}").format(
            port=port, error=e))
    return port


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


def to_device(data, path):
    """ Write straight to a device node, eg /dev/usb/lp0 (USB printer
    class cutters on Linux) or a parallel port.
    """
    if not path:
        raise TransportError(_("No device file set"))
    try:
        fd = os.open(path, os.O_WRONLY)
    except OSError as e:
        raise TransportError(_("Could not open {path}: {error}").format(
            path=path, error=e))
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)
    return path


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
