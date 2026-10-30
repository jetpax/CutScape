"""
Manage the CUPS printer queue a cutter is reached through: create a raw
queue for a USB cutter, restart a stopped one and clear jobs stuck in it,
or remove it.

CUPS lets only its admin group change printers (lpadmin on Debian and
Ubuntu, where the first user is a member). When it refuses, the error
gives the command to run with sudo instead.
"""
import os
import re
import subprocess
import sys

from . import transports
from .i18n import _

TASKS = ('create', 'restart', 'remove')

#: Used when the Printer queue field is empty
DEFAULT_QUEUE = 'cutter'

#: CUPS allows more, but these never need quoting
QUEUE_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,127}$')


class PrinterError(Exception):
    pass


def _cups(args, timeout=30):
    """ (returncode, stdout, stderr) of a CUPS command """
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout,
                           env=dict(os.environ, LC_ALL='C'))
    except OSError as e:
        raise PrinterError(_("Could not run {command}: {error}").format(
            command=args[0], error=e))
    except subprocess.TimeoutExpired:
        raise PrinterError(_("{command} did not answer; is CUPS "
                             "running?").format(command=args[0]))
    return (p.returncode, p.stdout.decode(errors='replace'),
            p.stderr.decode(errors='replace').strip())


def _quote(arg):
    return arg if re.match(r'^[\w./:=-]+$', arg) else "'%s'" % arg


def _admin(args):
    """ Run a command that needs CUPS admin rights """
    code, out, err = _cups(args)
    if code == 0:
        return out
    command = ' '.join(_quote(a) for a in args)
    if re.search(r'forbidden|not authori[sz]ed|permission|unauthori[sz]ed',
                 err, re.I):
        msg = _("CUPS won't let this account change printers. Run this in "
                "a terminal instead:\n  sudo {command}").format(
                    command=command)
        if sys.platform.startswith('linux'):
            msg += '\n' + _(
                "Or allow it for good: sudo usermod -aG lpadmin $USER, then "
                "log out and back in.")
        raise PrinterError(msg)
    raise PrinterError(_("{command} failed: {error}").format(
        command=args[0], error=err or out.strip() or code))


def check_name(queue):
    queue = queue or DEFAULT_QUEUE
    if not QUEUE_NAME.match(queue):
        raise PrinterError(_(
            "A printer queue name may only use letters, digits, '.', '-' "
            "and '_'; got {queue}").format(queue=queue))
    return queue


def cutter_uri(uri=''):
    """ The device URI to create a queue for: the one given, or the only
    USB printer CUPS can see that has no queue yet
    """
    if uri:
        return uri
    loose = _loose_uris()
    if not loose:
        raise PrinterError(_(
            "No USB cutter without a printer queue was found. Check that it "
            "is plugged in and switched on, or enter its address (usb://…) "
            "in the Cutter's USB address field."))
    if len(loose) > 1:
        raise PrinterError(_(
            "More than one USB printer has no queue. Enter the cutter's "
            "address in the Cutter's USB address field:\n{uris}").format(
                uris='\n'.join('  ' + u for u in loose)))
    return loose[0]


def _loose_uris():
    """ USB printer URIs CUPS can see that no queue uses yet """
    known = set(transports.queue_devices().values())
    return [u for u in transports.unconfigured_usb_uris() if u not in known]


def create(queue='', uri=''):
    """ Create (or repoint) a raw queue; returns a message for the user """
    queue = check_name(queue)
    existing = transports.queue_devices().get(queue)
    if not uri and existing and not _loose_uris():
        #: Nothing new is plugged in, so this is the queue's own cutter
        uri = existing
    uri = cutter_uri(uri)
    if existing == uri:
        msg = _("The printer queue {queue} is already set up for "
                "{uri}.").format(queue=queue, uri=uri)
        if transports.queue_state(queue) == 'disabled':
            msg += ' ' + _("It is stopped; choose Restart the queue and "
                           "clear stuck jobs to start it.")
        return msg
    #: -m raw passes the cutter's commands through untouched. CUPS warns
    #: that raw queues are deprecated, but they still work.
    _admin(['lpadmin', '-p', queue, '-E', '-v', uri, '-m', 'raw',
            '-D', 'Cutter (CutScape)', '-o', 'printer-is-shared=false'])
    if existing:
        return _("Moved the printer queue {queue} to {uri}.").format(
            queue=queue, uri=uri)
    return _("Created the printer queue {queue} for {uri}. In Cut, on "
             "the Connection tab, choose Printer queue and enter "
             "{queue}.").format(queue=queue, uri=uri)


def _exists(queue):
    if queue not in transports.queue_devices():
        raise PrinterError(_("There is no printer queue called "
                             "{queue}.").format(queue=queue))


def pending_jobs(queue):
    code, out, _err = _cups(['lpstat', '-o', queue])
    return len([l for l in out.splitlines() if l.startswith(queue + '-')]) \
        if code == 0 else 0


def restart(queue=''):
    """ Cancel stuck jobs, then start the queue and let it take jobs """
    queue = check_name(queue)
    _exists(queue)
    stuck = pending_jobs(queue)
    #: -c cancels every job on the queue first
    _admin(['cupsenable', '-c', queue])
    _admin(['cupsaccept', queue])
    if stuck:
        return _("Restarted the printer queue {queue} and cleared {count} "
                 "waiting job(s).").format(queue=queue, count=stuck)
    return _("Restarted the printer queue {queue}.").format(queue=queue)


def remove(queue=''):
    queue = check_name(queue)
    _exists(queue)
    _admin(['lpadmin', '-x', queue])
    return _("Removed the printer queue {queue}.").format(queue=queue)


def manage(task, queue='', uri=''):
    """ Run one of TASKS; returns a message for the user """
    if sys.platform == 'win32':
        raise PrinterError(_("On Windows, add or remove printers in "
                             "Settings > Printers & scanners."))
    if task == 'create':
        return create(queue, uri)
    if task == 'restart':
        return restart(queue)
    if task == 'remove':
        return remove(queue)
    raise PrinterError("Unknown printer task %r" % task)
