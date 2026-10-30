"""
The cutter's CUPS queue. CUPS itself is never touched: either its commands
are stubbed, or fake lpadmin/lpstat programs come first on PATH and record
how they were called. The password prompt is stubbed too.
"""
import os
import sys

import pytest

from cutscape import admin, printers
from cutscape.printers import PrinterError

URI = 'usb://SAGACNC/SAGA%20Cutter%20Plotter?serial=x'

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='CUPS')


@pytest.fixture
def cups(monkeypatch):
    """ Stub CUPS: jobs waiting, commands run, and an error to refuse with """
    state = dict(jobs=0, calls=[], fail=None, prompts=[])

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
    monkeypatch.setattr(admin, 'available', lambda: False)
    return state


@pytest.fixture
def prompt(cups, monkeypatch):
    """ A password prompt that the user answers """
    monkeypatch.setattr(admin, 'available', lambda: True)
    monkeypatch.setattr(admin, 'run', lambda script, reason:
                        cups['prompts'].append(script) or '')
    return cups['prompts']


def admin_calls(state):
    return [c for c in state['calls'] if c[0] != 'lpstat']


def test_create(cups):
    printers.create('cutter', URI)
    assert admin_calls(cups) == [[
        'lpadmin', '-p', 'cutter', '-E', '-v', URI, '-m', 'raw',
        '-D', 'Cutter (CutScape)', '-o', 'printer-is-shared=false']]


def test_restart_clears_stuck_jobs(cups):
    cups['jobs'] = 2
    assert printers.restart('cutter') == 2
    assert admin_calls(cups) == [['cupsenable', '-c', 'cutter'],
                                 ['cupsaccept', 'cutter']]


def test_remove(cups):
    printers.remove('vinyl')
    assert admin_calls(cups) == [['lpadmin', '-x', 'vinyl']]


@pytest.mark.parametrize('name', ['my cutter', "x'; rm", 'a/b'])
def test_queue_names_are_checked(cups, name):
    with pytest.raises(PrinterError, match='letters, digits'):
        printers.remove(name)
    assert not admin_calls(cups)


def test_refused_asks_for_the_password(cups, prompt):
    cups['fail'] = 'lpadmin: Forbidden'
    printers.create('cutter', URI)
    assert prompt == ["lpadmin -p cutter -E -v '%s' -m raw -D "
                      "'Cutter (CutScape)' -o printer-is-shared=false" % URI]


def test_restart_asks_once(cups, prompt):
    cups['fail'] = 'cupsenable: Forbidden'
    printers.restart('cutter')
    assert prompt == ['cupsenable -c cutter && cupsaccept cutter']


def test_password_prompt_cancelled(cups, monkeypatch):
    cups['fail'] = 'lpadmin: Forbidden'
    monkeypatch.setattr(admin, 'available', lambda: True)

    def cancel(script, reason):
        raise admin.Cancelled('The password prompt was cancelled.')
    monkeypatch.setattr(admin, 'run', cancel)
    with pytest.raises(PrinterError, match='cancelled'):
        printers.remove('cutter')


def test_no_prompt_gives_the_sudo_commands(cups, monkeypatch):
    monkeypatch.setattr(printers.sys, 'platform', 'linux')
    cups['fail'] = 'cupsenable: Forbidden'
    with pytest.raises(PrinterError) as e:
        printers.restart('cutter')
    msg = str(e.value)
    assert '  sudo cupsenable -c cutter\n  sudo cupsaccept cutter' in msg
    assert 'usermod -aG lpadmin' in msg


def test_other_failures_are_reported(cups, prompt):
    cups['fail'] = 'lpadmin: Unable to connect to server'
    with pytest.raises(PrinterError, match='Unable to connect'):
        printers.create('cutter', URI)
    assert not prompt


FAKE = {
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
    printers.create('cutter', URI)
    assert log.read_text().split() == [
        '-p', 'cutter', '-E', '-v', URI, '-m', 'raw', '-D', 'Cutter',
        '(CutScape)', '-o', 'printer-is-shared=false']
