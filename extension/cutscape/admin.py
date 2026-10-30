"""
Run a shell script as an administrator through the system's own password
prompt: "do shell script ... with administrator privileges" on macOS, and
pkexec (polkit) on a Linux desktop. CutScape never sees the password.
"""
import os
import shutil
import subprocess
import sys

from .i18n import _

#: Long enough for someone to find their password
TIMEOUT = 300


class AdminError(Exception):
    pass


class Cancelled(AdminError):
    pass


def available():
    """ Whether run() can ask for a password here """
    if sys.platform == 'darwin':
        return bool(shutil.which('osascript'))
    if sys.platform.startswith('linux'):
        #: pkexec needs a polkit agent, which only a desktop session runs
        return bool(shutil.which('pkexec') and (
            os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')))
    return False


def _applescript_string(text):
    return '"%s"' % text.replace('\\', '\\\\').replace('"', '\\"')


def command(script, reason):
    """ The command that runs script (sh) as root after a prompt """
    script = 'export LC_ALL=C; ' + script
    if sys.platform == 'darwin':
        return ['osascript', '-e', 'do shell script %s with prompt %s with '
                'administrator privileges' % (_applescript_string(script),
                                              _applescript_string(reason))]
    return ['pkexec', '/bin/sh', '-c', script]


def run(script, reason):
    """ Run script as root; returns its output. Raises Cancelled if the
    user dismissed the prompt, AdminError if it failed.
    """
    try:
        p = subprocess.run(command(script, reason), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=TIMEOUT)
    except OSError as e:
        raise AdminError(str(e))
    except subprocess.TimeoutExpired:
        raise Cancelled(_("Nobody answered the password prompt."))
    out = p.stdout.decode(errors='replace')
    err = p.stderr.decode(errors='replace').strip()
    if p.returncode == 0:
        return out
    #: osascript: "User canceled. (-128)"; pkexec: 126 when dismissed
    if '(-128)' in err or (p.returncode == 126 and sys.platform != 'darwin'):
        raise Cancelled(_("The password prompt was cancelled."))
    raise AdminError(err or out.strip() or str(p.returncode))
