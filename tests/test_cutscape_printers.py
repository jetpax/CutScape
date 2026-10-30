"""
Managing the cutter's CUPS queue. CUPS itself is never touched: either its
commands are stubbed, or fake lpadmin/lpstat/lpinfo programs come first on
PATH and record how they were called.
"""
import os
import sys

import pytest

from cutscape import printers, transports
from cutscape.printers import PrinterError

URI = 'usb://SAGACNC/SAGA%20Cutter%20Plotter?serial=x'
OTHER = 'usb://Brother/HL-3140CW?serial=y'

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='CUPS')


@pytest.fixture
def cups(monkeypatch):
    """ Stub CUPS: queues and USB URIs it reports, and commands run """
    state = dict(queues={}, usb=[URI], disabled=set(), jobs=0,
                 calls=[], fail=None)

    def cups_run(args, timeout=30):
        state['calls'].append(args)
        if args[0] == 'lpstat':
            q = args[2]
            return 0, ''.join('%s-%d jep 1024 Thu\n' % (q, i + 1)
                              for i in range(state['jobs'])), ''
        if state['fail']:
            return 1, '', state['fail']
        return 0, '', ''

    monkeypatch.setattr(printers, '_cups', cups_run)
    monkeypatch.setattr(transports, 'queue_devices',
                        lambda: dict(state['queues']))
    monkeypatch.setattr(transports, 'unconfigured_usb_uris',
                        lambda: list(state['usb']))
    monkeypatch.setattr(transports, 'queue_state', lambda q: (
        'disabled' if q in state['disabled'] else 'enabled'))
    return state


def admin_calls(state):
    return [c for c in state['calls'] if c[0] != 'lpstat']


def test_create_for_the_only_usb_cutter(cups):
    msg = printers.manage('create')
    assert admin_calls(cups) == [[
        'lpadmin', '-p', 'cutter', '-E', '-v', URI, '-m', 'raw',
        '-D', 'Cutter (CutScape)', '-o', 'printer-is-shared=false']]
    assert 'Created the printer queue cutter' in msg


def test_create_needs_an_address_when_several(cups):
    cups['usb'] = [URI, OTHER]
    with pytest.raises(PrinterError) as e:
        printers.manage('create')
    assert URI in str(e.value) and OTHER in str(e.value)
    assert not admin_calls(cups)
    printers.manage('create', 'vinyl', OTHER)
    assert admin_calls(cups)[0][:6] == [
        'lpadmin', '-p', 'vinyl', '-E', '-v', OTHER]


def test_create_skips_printers_with_a_queue(cups):
    cups['queues'] = {'office': OTHER}
    cups['usb'] = [URI, OTHER]
    printers.manage('create')
    assert admin_calls(cups)[0][5] == URI


def test_create_nothing_connected(cups):
    cups['usb'] = []
    with pytest.raises(PrinterError, match='plugged in'):
        printers.manage('create')


def test_create_already_done(cups):
    cups['queues'] = {'cutter': URI}
    cups['disabled'] = {'cutter'}
    msg = printers.manage('create')
    assert 'already set up' in msg and 'Restart' in msg
    assert not admin_calls(cups)


def test_create_moves_existing_queue(cups):
    cups['queues'] = {'cutter': OTHER}
    msg = printers.manage('create', 'cutter', URI)
    assert admin_calls(cups)[0][5] == URI
    assert 'Moved' in msg


def test_not_allowed_gives_the_sudo_command(cups, monkeypatch):
    monkeypatch.setattr(printers.sys, 'platform', 'linux')
    cups['fail'] = 'lpadmin: Forbidden'
    with pytest.raises(PrinterError) as e:
        printers.manage('create')
    msg = str(e.value)
    assert "sudo lpadmin -p cutter -E -v '%s' -m raw" % URI in msg
    assert 'usermod -aG lpadmin' in msg


def test_other_failures_are_reported(cups):
    cups['fail'] = 'lpadmin: Unable to connect to server'
    with pytest.raises(PrinterError, match='Unable to connect'):
        printers.manage('create')


def test_restart_clears_stuck_jobs(cups):
    cups['queues'] = {'cutter': URI}
    cups['jobs'] = 2
    msg = printers.manage('restart')
    assert admin_calls(cups) == [['cupsenable', '-c', 'cutter'],
                                 ['cupsaccept', 'cutter']]
    assert 'cleared 2' in msg


def test_restart_unknown_queue(cups):
    with pytest.raises(PrinterError, match='no printer queue called cutter'):
        printers.manage('restart')


def test_remove(cups):
    cups['queues'] = {'vinyl': URI}
    assert 'Removed' in printers.manage('remove', 'vinyl')
    assert admin_calls(cups) == [['lpadmin', '-x', 'vinyl']]


@pytest.mark.parametrize('name', ['my cutter', "x'; rm", 'a/b'])
def test_queue_names_are_checked(cups, name):
    with pytest.raises(PrinterError, match='letters, digits'):
        printers.manage('remove', name)
    assert not admin_calls(cups)


def test_windows(monkeypatch):
    monkeypatch.setattr(printers.sys, 'platform', 'win32')
    with pytest.raises(PrinterError, match='Settings'):
        printers.manage('create')


FAKE = {
    'lpinfo': "echo 'network ipp'; echo 'direct %s'" % URI,
    'lpstat': 'exit 0',
    'lpadmin': 'echo "$@" >> "{log}"',
}


def test_create_with_fake_cups_programs(tmp_path, monkeypatch):
    """ The real subprocess path, with stand-ins for the CUPS tools """
    log = tmp_path / 'lpadmin.log'
    for name, body in FAKE.items():
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\n' + body.format(log=log) + '\n')
        tool.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path), prepend=os.pathsep)
    assert 'Created' in printers.manage('create')
    assert log.read_text().split() == [
        '-p', 'cutter', '-E', '-v', URI, '-m', 'raw', '-D', 'Cutter',
        '(CutScape)', '-o', 'printer-is-shared=false']
