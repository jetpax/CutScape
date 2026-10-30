"""
The CUPS printer queue a cutter is reached through: create a raw queue for
a USB cutter, restart a stopped one and clear jobs stuck in it, or remove
it. Add Cutter and Remove Cutter (see cutter) use these.

CUPS lets only its admin group change printers (lpadmin on Debian and
Ubuntu, where the first user is a member; admin on macOS). When it
refuses, the command is run again through the system's password prompt
(see admin), or failing that the error gives the command to run with sudo.
"""
import os
import re
import shlex
import subprocess
import sys

from . import admin
from .i18n import _

#: The queue Add Cutter creates
DEFAULT_QUEUE = 'cutter'

#: CUPS allows more, but these never need quoting
QUEUE_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,127}$')


class PrinterError(Exception):
    pass


def _cups(args, timeout=30):
    """ (returncode, stdout, stderr) of a CUPS command """
    try:
        #: No stdin, so lpadmin can't sit waiting for a password
        p = subprocess.run(args, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, env=dict(os.environ, LC_ALL='C'))
    except OSError as e:
        raise PrinterError(_("Could not run {command}: {error}").format(
            command=args[0], error=e))
    except subprocess.TimeoutExpired:
        raise PrinterError(_("{command} did not answer; is CUPS "
                             "running?").format(command=args[0]))
    return (p.returncode, p.stdout.decode(errors='replace'),
            p.stderr.decode(errors='replace').strip())


def _refused(err):
    return re.search(r'forbidden|not authori[sz]ed|permission|'
                     r'unauthori[sz]ed', err, re.I)


def _admin(*commands):
    """ Run commands that need CUPS admin rights, in turn. If CUPS refuses
    one, it and the rest run together after a single password prompt.
    """
    for i, args in enumerate(commands):
        code, out, err = _cups(args)
        if code == 0:
            continue
        if not _refused(err):
            raise PrinterError(_("{command} failed: {error}").format(
                command=args[0], error=err or out.strip() or code))
        _as_admin(commands[i:], err)
        return


def _as_admin(commands, err):
    if admin.available():
        try:
            admin.run(' && '.join(shlex.join(a) for a in commands), _(
                "CutScape needs to change the printer queue for your "
                "cutter."))
            return
        except admin.Cancelled as e:
            raise PrinterError(str(e))
        except admin.AdminError as e:
            err = str(e)
    msg = _("CUPS won't let this account change printers ({error}). Run "
            "this in a terminal instead:\n{commands}").format(
                error=err, commands='\n'.join(
                    '  sudo ' + shlex.join(a) for a in commands))
    if sys.platform.startswith('linux'):
        msg += '\n' + _(
            "Or allow it for good: sudo usermod -aG lpadmin $USER, then "
            "log out and back in.")
    raise PrinterError(msg)


def check_name(queue):
    if not QUEUE_NAME.match(queue):
        raise PrinterError(_(
            "A printer queue name may only use letters, digits, '.', '-' "
            "and '_'; got {queue}").format(queue=queue))
    return queue


def create(queue, uri):
    """ Create a raw queue for uri, or point an existing one at it """
    check_name(queue)
    #: -m raw passes the cutter's commands through untouched. CUPS warns
    #: that raw queues are deprecated, but they still work.
    _admin(['lpadmin', '-p', queue, '-E', '-v', uri, '-m', 'raw',
            '-D', 'Cutter (CutScape)', '-o', 'printer-is-shared=false'])


def pending_jobs(queue):
    code, out, _err = _cups(['lpstat', '-o', queue])
    return len([l for l in out.splitlines() if l.startswith(queue + '-')]) \
        if code == 0 else 0


def restart(queue):
    """ Cancel stuck jobs, then start the queue and let it take jobs.
    Returns how many jobs were cancelled.
    """
    check_name(queue)
    stuck = pending_jobs(queue)
    #: -c cancels every job on the queue first
    _admin(['cupsenable', '-c', queue], ['cupsaccept', queue])
    return stuck


def remove(queue):
    check_name(queue)
    _admin(['lpadmin', '-x', queue])
