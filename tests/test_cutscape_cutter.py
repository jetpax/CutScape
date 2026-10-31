"""
Add Cutter and Remove Cutter. No cutter, CUPS or password prompt is
touched: sysfs is faked in a temp folder, CUPS answers from a stub, and
the prompt is a stub that records the script it was asked to run.
"""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cutscape import admin, cutter, printers, transports
from cutscape.cutter import CutterError
from fakes import SAGA, fake_sysfs

SECABO = 'Secabo C60 II / C IV'
URI = 'usb://SAGACNC/SAGA%20Cutter%20Plotter?serial=x'
OTHER = 'usb://Brother/HL-3140CW?serial=y'
CH340 = dict(path='/dev/ttyUSB0', description='USB Serial', vid='1a86',
             pid='7523', serial='')

pytestmark = pytest.mark.skipif(sys.platform == 'win32',
                                reason='CUPS and sysfs')


@pytest.fixture
def world(tmp_path, monkeypatch):
    """ What is plugged in, what CUPS knows, and what was run """
    state = dict(queues={}, usb=[], refused=False, disabled=set(), jobs=0,
                 calls=[], ports=[], writable=True, admin=False, prompts=[],
                 sysfs=str(tmp_path / 'nothing'))

    def cups_run(args, timeout=30):
        state['calls'].append(args)
        if args[0] == 'lpinfo':
            if state['refused']:
                return 1, '', 'lpinfo: Forbidden'
            return 0, 'network ipp\n' + ''.join(
                'direct %s\n' % u for u in state['usb']), ''
        if args[0] == 'lpstat':
            return 0, ''.join('%s-%d jep 1024 Thu\n' % (args[2], i + 1)
                              for i in range(state['jobs'])), ''
        return 0, '', ''

    def prompt(script, reason):
        state['prompts'].append(script)
        if 'udevadm' in script:
            state['writable'] = True
        return ''

    monkeypatch.setattr(printers, '_cups', cups_run)
    monkeypatch.setattr(transports, 'queue_devices',
                        lambda: dict(state['queues']))
    monkeypatch.setattr(transports, 'queue_state', lambda q: (
        'disabled' if q in state['disabled'] else 'enabled'))
    monkeypatch.setattr(transports, 'serial_ports',
                        lambda: list(state['ports']))
    monkeypatch.setattr(cutter, '_writable', lambda path: state['writable'])
    monkeypatch.setattr(admin, 'available', lambda: state['admin'])
    monkeypatch.setattr(admin, 'run', prompt)
    return state


def add(world, model=SECABO, address=''):
    return cutter.add(model, address, sysfs=world['sysfs'])


def admin_calls(world):
    return [c for c in world['calls'] if c[0] not in ('lpstat', 'lpinfo')]


# -----------------------------------------------------------------------------
# Finding and setting up
# -----------------------------------------------------------------------------
def test_usb_cutter_on_linux(world, tmp_path):
    world['sysfs'] = fake_sysfs(tmp_path / 'sys', SAGA)
    #: CUPS sees it too, but the device is better
    world['usb'] = [URI]
    text, ready = add(world)
    assert ready
    assert cutter.load() == dict(
        model=SECABO, label='SAGACNC SAGA Cutter Plotter',
        transport='device', device='usb:0483:5750', printer='', port='',
        queue_created=False)
    assert 'Added your %s, connected through USB, as usb:0483:5750.' % \
        SECABO in text
    assert 'SAGACNC SAGA Cutter Plotter' in text
    assert 'test square' in text
    assert not admin_calls(world) and not world['prompts']


def test_usb_access_with_a_udev_rule(world, tmp_path, monkeypatch):
    monkeypatch.setattr(cutter.sys, 'platform', 'linux')
    world['sysfs'] = fake_sysfs(tmp_path / 'sys', SAGA)
    world.update(writable=False, admin=True)
    text, ready = add(world)
    assert ready and 'Gave you access to it with a udev rule' in text
    [script] = world['prompts']
    assert transports.udev_rule(SAGA).replace('"', '') in \
        script.replace('"', '')
    assert cutter.UDEV_RULES in script


def test_udev_script_writes_the_rule(world, tmp_path, monkeypatch):
    """ The script really runs, with udevadm faked """
    monkeypatch.setattr(cutter.sys, 'platform', 'linux')
    world['sysfs'] = fake_sysfs(tmp_path / 'sys', SAGA)
    rules = tmp_path / '70-cutscape.rules'
    monkeypatch.setattr(cutter, 'UDEV_RULES', str(rules))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'udevadm').write_text('#!/bin/sh\nexit 0\n')
    (bin_dir / 'udevadm').chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir), prepend=os.pathsep)

    def run(script, reason):
        subprocess.run(['/bin/sh', '-c', script], check=True)
        world['writable'] = True
    monkeypatch.setattr(admin, 'run', run)
    world.update(writable=False, admin=True)
    add(world)
    assert rules.read_text() == transports.udev_rule(SAGA) + '\n'


def test_usb_access_without_a_prompt(world, tmp_path, monkeypatch):
    monkeypatch.setattr(cutter.sys, 'platform', 'linux')
    world['sysfs'] = fake_sysfs(tmp_path / 'sys', SAGA)
    world['writable'] = False
    text, ready = add(world)
    assert not ready
    assert "You can't write to /dev/usb/lp4 yet" in text
    assert 'sudo sh -c' in text and 'udevadm' in text
    #: Added all the same: only the permission is missing
    assert cutter.load()['device'] == 'usb:0483:5750'


def test_makes_a_printer_queue(world):
    world['usb'] = [URI]
    text, ready = add(world)
    assert admin_calls(world) == [[
        'lpadmin', '-p', 'cutter', '-E', '-v', URI, '-m', 'raw',
        '-D', 'Cutter (CutScape)', '-o', 'printer-is-shared=false']]
    saved = cutter.load()
    assert (saved['transport'], saved['printer'], saved['queue_created']) == \
        ('printer', 'cutter', True)
    assert 'Created the printer queue cutter for it.' in text
    assert 'connected through the printer queue cutter' in text


def test_points_the_old_queue_at_a_new_cutter(world):
    world['queues'] = {'cutter': OTHER}
    world['usb'] = [URI]
    text, _ready = add(world)
    assert admin_calls(world)[0][5] == URI
    assert 'Pointed the printer queue cutter at it.' in text


def test_several_need_an_address(world):
    world['usb'] = [URI, OTHER]
    with pytest.raises(CutterError) as e:
        add(world)
    msg = str(e.value)
    assert 'Address field' in msg
    assert '  %s  SAGACNC SAGA Cutter Plotter' % URI in msg
    assert '  %s  Brother HL-3140CW' % OTHER in msg
    assert not admin_calls(world) and cutter.load() is None
    add(world, address=OTHER)
    assert admin_calls(world)[0][5] == OTHER


def test_adding_again_restarts_a_stopped_queue(world):
    world.update(queues={'cutter': URI}, usb=[URI], disabled={'cutter'},
                 jobs=2)
    text, ready = add(world)
    assert admin_calls(world) == [['cupsenable', '-c', 'cutter'],
                                  ['cupsaccept', 'cutter']]
    assert 'restarted it and cleared 2 stuck job(s)' in text
    assert cutter.load()['queue_created']


def test_serial_cutter(world):
    world['ports'] = [CH340]
    text, ready = add(world)
    saved = cutter.load()
    assert (saved['transport'], saved['port']) == ('serial', 'usb:1a86:7523')
    assert 'baud rate' in text


def test_grbl_cutter_gets_its_baud_rate(world):
    world['ports'] = [CH340]
    text, ready = add(world, model='CNC 3018 (GRBL)')
    assert 'It talks at 115200 baud.' in text
    assert 'baud rate and flow control' not in text
    o = options(baud='9600', flow='xonxoff')
    cutter.use(o, sending=True)
    assert (o.preset, o.transport, o.port, o.baud, o.flow) == (
        'CNC 3018 (GRBL)', 'serial', 'usb:1a86:7523', '115200', 'none')


def test_not_listed_model(world):
    world['ports'] = [CH340]
    text, ready = add(world, model='custom')
    assert text.startswith('Added your cutter,')
    assert 'steps per mm' in text
    assert cutter.load()['model'] == 'custom'


def test_unknown_model(world):
    with pytest.raises(ValueError):
        add(world, model='Nope 3000')


def test_nothing_found(world):
    world['queues'] = {'office': 'socket://192.168.1.9',
                       'laser': 'dnssd://Laser._ipp._tcp.local./'}
    world['ports'] = [
        dict(path='/dev/cu.Bluetooth-Incoming-Port', description='n/a',
             vid='', pid='', serial=''),
        dict(path='/dev/ttyS0', description='ttyS0', vid='', pid='',
             serial='')]
    with pytest.raises(CutterError) as e:
        add(world)
    msg = str(e.value)
    assert msg.startswith('No cutter found.')
    #: What else could be the cutter, to enter as its address
    assert '  office  socket://192.168.1.9' in msg
    assert 'laser' not in msg
    assert '  /dev/ttyS0  ttyS0' in msg
    assert 'Bluetooth' not in msg
    assert cutter.load() is None


def test_nothing_found_cups_refused(world, monkeypatch):
    monkeypatch.setattr(cutter.sys, 'platform', 'linux')
    world['refused'] = True
    with pytest.raises(CutterError, match="CUPS wouldn't list") as e:
        add(world)
    assert 'usermod -aG lpadmin' in str(e.value)


def test_nothing_found_windows(world, monkeypatch):
    monkeypatch.setattr(cutter.sys, 'platform', 'win32')
    with pytest.raises(CutterError, match='Printers & scanners'):
        add(world)


def test_addresses(world, tmp_path):
    world['queues'] = {'vinyl': URI}
    world['ports'] = [CH340]
    add(world, address='vinyl')
    assert cutter.load()['printer'] == 'vinyl'
    assert not cutter.load()['queue_created']
    add(world, address='COM3')
    assert cutter.load()['port'] == 'COM3'
    add(world, address='usb:1a86:7523')
    assert cutter.load()['port'] == 'usb:1a86:7523'
    node = tmp_path / 'lp9'
    node.write_bytes(b'')
    add(world, address=str(node))
    assert cutter.load()['device'] == str(node)
    with pytest.raises(CutterError, match="isn't there"):
        add(world, address='/dev/usb/lp9')
    with pytest.raises(CutterError, match="doesn't know frobnicate"):
        add(world, address='frobnicate')
    with pytest.raises(CutterError, match='No USB printer usb:dead:beef'):
        add(world, address='usb:dead:beef')


# -----------------------------------------------------------------------------
# Cut using it, and removing it
# -----------------------------------------------------------------------------
def options(**kw):
    o = dict(preset=cutter.ADDED, transport=cutter.ADDED, printer='',
             device='/dev/usb/lp0', port='')
    o.update(kw)
    return SimpleNamespace(**o)


def test_cut_without_a_cutter():
    o = options()
    cutter.use(o, sending=False)
    #: Preview still works, with the custom settings
    assert o.preset == 'custom'
    with pytest.raises(CutterError, match='Add Cutter'):
        cutter.use(options(), sending=True)
    #: Choosing everything by hand needs no cutter
    o = options(preset=SECABO, transport='file')
    cutter.use(o, sending=True)
    assert (o.preset, o.transport) == (SECABO, 'file')


def test_cut_uses_the_cutter(world):
    world['usb'] = [URI]
    add(world)
    o = options()
    cutter.use(o, sending=True)
    assert (o.preset, o.transport, o.printer) == (SECABO, 'printer',
                                                  'cutter')
    #: A connection chosen by hand wins
    o = options(transport='device', device='/dev/cutter')
    cutter.use(o, sending=True)
    assert (o.preset, o.transport, o.device) == (SECABO, 'device',
                                                 '/dev/cutter')


def test_remove_takes_its_queue(world):
    world['usb'] = [URI]
    add(world)
    world['queues'] = {'cutter': URI}
    world['calls'].clear()
    assert cutter.remove() == ('Removed the cutter you added. Removed its '
                               'printer queue cutter.')
    assert admin_calls(world) == [['lpadmin', '-x', 'cutter']]
    assert cutter.load() is None
    assert cutter.remove() == 'No cutter has been added.'


def test_remove_leaves_other_queues(world):
    world['queues'] = {'vinyl': URI}
    add(world, address='vinyl')
    world['calls'].clear()
    assert cutter.remove() == 'Removed the cutter you added.'
    assert not admin_calls(world)


def test_unreadable_settings(cutter_config):
    cutter_config.parent.mkdir()
    cutter_config.write_text('{not json')
    assert cutter.load() is None


def test_config_path(monkeypatch):
    monkeypatch.delenv('CUTSCAPE_CONFIG')
    monkeypatch.setattr(cutter.sys, 'platform', 'linux')
    monkeypatch.setenv('XDG_CONFIG_HOME', '/x')
    assert cutter.config_path() == '/x/cutscape/cutter.json'
    monkeypatch.setattr(cutter.sys, 'platform', 'darwin')
    assert cutter.config_path() == os.path.expanduser(
        '~/Library/Application Support/CutScape/cutter.json')


# -----------------------------------------------------------------------------
# The password prompt
# -----------------------------------------------------------------------------
def test_prompt_command_macos(monkeypatch):
    monkeypatch.setattr(admin.sys, 'platform', 'darwin')
    assert admin.command('lpadmin -x "a"', 'Why "so"') == [
        'osascript', '-e', 'do shell script "export LC_ALL=C; lpadmin -x '
        '\\"a\\"" with prompt "Why \\"so\\"" with administrator privileges']


def test_prompt_command_linux(monkeypatch):
    monkeypatch.setattr(admin.sys, 'platform', 'linux')
    assert admin.command('lpadmin -x a', 'Why') == [
        'pkexec', '/bin/sh', '-c', 'export LC_ALL=C; lpadmin -x a']


def test_prompt_needs_a_desktop(monkeypatch):
    monkeypatch.setattr(admin.sys, 'platform', 'linux')
    monkeypatch.setattr(admin.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    assert not admin.available()
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
    assert admin.available()


@pytest.mark.parametrize('body, outcome', [
    ('shift 2; exec /bin/sh -c "$1"', 'hi\n'),
    ('exit 126', admin.Cancelled),
    ('echo "Not authorized" >&2; exit 127', admin.AdminError),
])
def test_prompt_run(tmp_path, monkeypatch, body, outcome):
    """ A fake pkexec: runs the script, or is dismissed, or refused """
    monkeypatch.setattr(admin.sys, 'platform', 'linux')
    (tmp_path / 'pkexec').write_text('#!/bin/sh\n' + body + '\n')
    (tmp_path / 'pkexec').chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path), prepend=os.pathsep)
    if isinstance(outcome, str):
        assert admin.run('echo hi', 'Why') == outcome
    else:
        with pytest.raises(outcome):
            admin.run('echo hi', 'Why')
