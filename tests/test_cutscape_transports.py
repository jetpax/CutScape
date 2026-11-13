"""
Connections: USB ids, timeouts, and the CUPS checks.
Nothing here touches real hardware; sysfs is faked in a temp folder laid
out like the kernel's, and a FIFO that nobody reads stands in for a
cutter that has stopped taking data.
"""
import errno
import os
import select
import subprocess
import sys
import threading

import pytest

from cutscape import transports
from cutscape.transports import TransportError
from fakes import SAGA, fake_sysfs

needs_sysfs_layout = pytest.mark.skipif(
    sys.platform == 'win32', reason='symlinks')


@needs_sysfs_layout
def test_usb_printers(tmp_path):
    sysfs = fake_sysfs(tmp_path, SAGA)
    found = transports.usb_printers(sysfs)
    assert found == [dict(path='/dev/usb/lp4', vid='0483', pid='5750',
                          serial='SAGAIufI@\x16', manufacturer='SAGACNC',
                          product='SAGA Cutter Plotter')]


def test_usb_printers_none(tmp_path):
    assert transports.usb_printers(str(tmp_path)) == []


def test_usb_id_skips_junk_serial():
    assert transports.usb_id(SAGA) == 'usb:0483:5750'
    assert transports.usb_id(dict(SAGA, serial='ABC123')) == \
        'usb:0483:5750:ABC123'


@needs_sysfs_layout
def test_resolve_device(tmp_path):
    sysfs = fake_sysfs(tmp_path, SAGA)
    assert transports.resolve_device('usb:0483:5750', sysfs) == '/dev/usb/lp4'
    assert transports.resolve_device('USB:0483:5750', sysfs) == '/dev/usb/lp4'
    with pytest.raises(TransportError, match='No USB printer'):
        transports.resolve_device('usb:1234:5678', sysfs)
    with pytest.raises(TransportError, match='looks like'):
        transports.resolve_device('usb:0483', sysfs)


@needs_sysfs_layout
def test_resolve_device_by_serial(tmp_path):
    sysfs = fake_sysfs(tmp_path, dict(SAGA, serial='A1'),
                       dict(SAGA, serial='B2'))
    with pytest.raises(TransportError, match='More than one'):
        transports.resolve_device('usb:0483:5750', sysfs)
    assert transports.resolve_device('usb:0483:5750:B2', sysfs) == \
        '/dev/usb/lp5'


def test_resolve_device_plain_path():
    assert transports.resolve_device('/dev/cutter') == '/dev/cutter'
    assert transports.resolve_device('~/x') == os.path.expanduser('~/x')


def test_udev_rule():
    rule = transports.udev_rule(SAGA)
    assert 'ATTRS{idVendor}=="0483"' in rule
    assert 'ATTRS{product}=="SAGA Cutter Plotter"' in rule
    assert rule.endswith('SYMLINK+="cutter", TAG+="uaccess"')


# -----------------------------------------------------------------------------
# Device files
# -----------------------------------------------------------------------------
needs_fifo = pytest.mark.skipif(not hasattr(os, 'mkfifo'),
                                reason='needs FIFOs')


@needs_fifo
def test_device_write(tmp_path):
    fifo = str(tmp_path / 'lp0')
    os.mkfifo(fifo)
    got = []
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    #: Hold a writer open so the reader blocks instead of seeing EOF before
    #: to_device opens its end
    holder = os.open(fifo, os.O_WRONLY)

    def drain():
        os.set_blocking(reader, True)
        while True:
            chunk = os.read(reader, 65536)
            if not chunk:
                break
            got.append(chunk)

    data = b'PD0,0;' * 50000  # bigger than a pipe buffer
    t = threading.Thread(target=drain)
    t.start()
    assert transports.to_device(data, fifo, timeout=5) == fifo
    os.close(holder)
    t.join(5)
    os.close(reader)
    assert b''.join(got) == data


@needs_fifo
def test_device_stall_times_out(tmp_path):
    """ A cutter that stops taking data fails the job instead of hanging """
    fifo = str(tmp_path / 'lp0')
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)  # never read
    try:
        with pytest.raises(TransportError) as e:
            transports.to_device(b'x' * (1 << 20), fifo, timeout=0.3)
    finally:
        os.close(reader)
    assert 'stopped accepting data' in str(e.value)
    assert 'of 1048576 bytes were sent' in str(e.value)


def test_device_missing(tmp_path):
    with pytest.raises(TransportError, match="isn't there"):
        transports.to_device(b'IN;', str(tmp_path / 'nope'))


@pytest.mark.skipif(sys.platform == 'win32' or os.geteuid() == 0,
                    reason='root can write anything')
def test_device_permission(tmp_path):
    f = tmp_path / 'lp0'
    f.write_bytes(b'')
    f.chmod(0o444)
    with pytest.raises(TransportError, match='udev rule'):
        transports.to_device(b'IN;', str(f))


# -----------------------------------------------------------------------------
# Serial
# -----------------------------------------------------------------------------
class FakePort(object):
    port = '/dev/ttyUSB0'

    def __init__(self, waiting=0):
        self.written = b''
        self.out_waiting = waiting

    def write(self, data):
        self.written += bytes(data)
        return len(data)


def test_serial_chunks():
    s = FakePort()
    transports._write_serial(s, b'a' * 1000, 1)
    assert s.written == b'a' * 1000


def test_serial_drain_stall():
    with pytest.raises(TransportError, match='stopped accepting data'):
        transports._drain_serial(FakePort(waiting=10), 0.2)


@pytest.mark.skipif(sys.platform == 'win32', reason='pty')
def test_sends_through_the_bundled_pyserial():
    """ A pty stands in for the cutter's port """
    master, slave = os.openpty()
    data = b'IN;PU0,0;PD100,100;' * 200
    got = bytearray()

    def cutter():
        while len(got) < len(data) and select.select([master], [], [], 5)[0]:
            got.extend(os.read(master, 4096))

    reader = threading.Thread(target=cutter)
    reader.start()
    try:
        transports.send(data, 'serial', port=os.ttyname(slave), baud='9600',
                        flow='none', timeout=5)
    finally:
        reader.join()
        os.close(slave)
        os.close(master)
    assert bytes(got) == data


@pytest.mark.parametrize('code, expected', [
    (errno.EACCES, r"(?s)You can't use /dev/ttyUSB0 yet.*usermod -aG dialout"),
    (errno.ENOENT, "/dev/ttyUSB0 isn't there"),
    (errno.EBUSY, 'Serial port /dev/ttyUSB0: .*in use'),
])
def test_serial_port_wont_open(monkeypatch, code, expected):
    import serial

    def refuse(*args, **kwargs):
        raise serial.SerialException(code, 'could not open port: in use')

    monkeypatch.setattr(serial, 'Serial', refuse)
    monkeypatch.setattr(transports.sys, 'platform', 'linux')
    monkeypatch.setattr(transports, '_serial_group', lambda path: 'dialout')
    with pytest.raises(TransportError, match=expected):
        transports.send(b'x', 'serial', port='/dev/ttyUSB0')


@pytest.mark.parametrize('owner, expected', [
    ('uucp', 'uucp'), ('dialout', 'dialout'), ('root', 'dialout'),
    ('nobody', 'dialout'), (None, 'dialout'),
])
def test_serial_group(tmp_path, monkeypatch, owner, expected):
    grp = pytest.importorskip('grp')

    def group(gid):
        if owner is None:
            raise KeyError(gid)
        return type('Group', (), dict(gr_name=owner))

    monkeypatch.setattr(grp, 'getgrgid', group)
    port = tmp_path / 'ttyUSB0'
    port.touch()
    assert transports._serial_group(str(port)) == expected


def test_serial_group_of_a_missing_port():
    pytest.importorskip('grp')
    assert transports._serial_group('/dev/nothing-here') == 'dialout'


# -----------------------------------------------------------------------------
# Inkscape's Flatpak
# -----------------------------------------------------------------------------
def test_not_a_flatpak():
    assert transports.sandbox_note() == ''


def test_unreadable_flatpak_info(flatpak):
    flatpak.write_bytes(b'\xff\xfe not an ini file')
    assert transports.sandbox_note() == ''


@pytest.mark.parametrize('transport, where', [
    ('serial', dict(port='/dev/ttyUSB0')),
    ('device', dict(device='/dev/usb/lp0')),
])
def test_flatpak_says_why_it_cant_send(flatpak, transport, where):
    with pytest.raises(TransportError, match='flatpak override'):
        transports.send(b'x', transport, **where)


def test_flatpak_can_still_write_a_file(flatpak, tmp_path):
    out = tmp_path / 'job.plt'
    transports.send(b'x', 'file', file=str(out))
    assert out.read_bytes() == b'x'


class FakeGrbl(FakePort):
    """ Says hello when the port opens, then answers each line """
    def __init__(self, answer=lambda line: b'ok'):
        super(FakeGrbl, self).__init__()
        self.answer = answer
        self.replies = [b"Grbl 1.1h ['$' for help]\r\n"]
        self.lines = []

    def write(self, data):
        n = super(FakeGrbl, self).write(data)
        for line in bytes(data).splitlines():
            self.lines.append(line)
            self.replies.append(self.answer(line) + b'\r\n')
        return n

    def reset_input_buffer(self):
        self.replies = []

    def readline(self):
        return self.replies.pop(0) if self.replies else b''


def test_send_acked(monkeypatch):
    monkeypatch.setattr(transports, 'WAKE_DELAY', 0)
    s = FakeGrbl()
    transports._send_acked(s, b'G21\nG90 ; absolute\n\nG00 X0 Y0\n', 1)
    #: Woken first; the welcome and the blank lines' replies are dropped
    assert s.written.startswith(b'\r\n\r\n')
    assert s.lines[-3:] == [b'G21', b'G90', b'G00 X0 Y0']
    assert not s.replies


def test_send_acked_error(monkeypatch):
    monkeypatch.setattr(transports, 'WAKE_DELAY', 0)
    s = FakeGrbl(lambda line: b'error:22' if line.startswith(b'G01') else
                 b'ok')
    with pytest.raises(TransportError, match="'G01 X1 Y1': error:22"):
        transports._send_acked(s, b'G21\nG01 X1 Y1\nG00 X0 Y0\n', 1)
    assert s.lines[-1] == b'G01 X1 Y1'


def test_send_acked_no_reply(monkeypatch):
    monkeypatch.setattr(transports, 'WAKE_DELAY', 0)
    s = FakeGrbl(lambda line: b'')
    s.readline = lambda: b''
    with pytest.raises(TransportError, match='No reply to G21'):
        transports._send_acked(s, b'G21\n', 1)


# -----------------------------------------------------------------------------
# Printer queues
# -----------------------------------------------------------------------------
def test_stopped_queue(monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('CUPS')
    monkeypatch.setattr(transports, '_run', lambda *a, **k:
                        'printer cutter disabled since Thu 29 Oct -\n')
    with pytest.raises(TransportError, match='cupsenable cutter'):
        transports.to_printer(b'IN;', 'cutter')


def test_printer_request_id(monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('CUPS')
    monkeypatch.setattr(transports, '_run', lambda *a, **k:
                        'printer cutter is idle.  enabled since Thu\n')
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, b'request id is cutter-7 (1 file(s))\n', b'')
    monkeypatch.setattr(transports.subprocess, 'run', run)
    assert transports.to_printer(b'IN;', 'cutter') == 'cutter-7'
    assert calls == [['lp', '-d', 'cutter', '-o', 'raw']]


def test_queue_devices(monkeypatch):
    monkeypatch.setattr(transports, '_run', lambda *a, **k: (
        'device for cutter: usb://SAGACNC/SAGA%20Cutter%20Plotter?serial=x\n'
        'device for Samsung: dnssd://Samsung._ipp._tcp.local/\n'))
    assert transports.queue_devices() == {
        'cutter': 'usb://SAGACNC/SAGA%20Cutter%20Plotter?serial=x',
        'Samsung': 'dnssd://Samsung._ipp._tcp.local/'}
