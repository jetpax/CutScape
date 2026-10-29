"""
Translations for messages the script shows. The dialog itself is translated
by Inkscape from the same catalog (translationdomain in the .inx).

Inkscape passes the catalog folder in INKEX_GETTEXT_DIRECTORY and the UI
language in LANGUAGE; outside Inkscape the catalogs shipped next to this
package are used with the usual LANGUAGE/LC_ALL/LANG variables.
"""
import gettext
import os

DOMAIN = 'cutscape'
LOCALE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'locale')

_translation = None


def _(message):
    global _translation
    if _translation is None:
        _translation = gettext.translation(
            DOMAIN, os.environ.get('INKEX_GETTEXT_DIRECTORY') or LOCALE_DIR,
            fallback=True)
    return _translation.gettext(message)


def reset():
    """ Forget the loaded catalog, eg after changing LANGUAGE """
    global _translation
    _translation = None
