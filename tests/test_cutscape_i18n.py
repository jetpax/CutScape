"""
Translations: the catalogs match the sources and load at run time.
"""
import glob
import importlib.util
import re
import xml.etree.ElementTree as ET

import pytest

from cutscape import i18n

spec = importlib.util.spec_from_file_location('i18n_tool', 'tools/i18n.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

LANGS = ['fr']


def test_template_is_current():
    """ Rerun `python3 tools/i18n.py extract` after changing any string """
    header, entries = tool.read_po('po/cutscape.pot')
    assert list(entries) == list(tool.extract())


@pytest.mark.parametrize('lang', LANGS)
def test_catalog_complete(lang):
    _, entries = tool.read_po('po/%s.po' % lang)
    for msgid in tool.extract():
        e = entries.get(msgid)
        assert e and e['msgstr'], "%s.po misses %r" % (lang, msgid)
        assert 'fuzzy' not in e['flags'], msgid


@pytest.mark.parametrize('lang', LANGS)
def test_catalog_placeholders(lang):
    _, entries = tool.read_po('po/%s.po' % lang)
    for msgid, e in entries.items():
        names = sorted(re.findall(r'\{\w*[^}]*\}', msgid))
        assert sorted(re.findall(r'\{\w*[^}]*\}', e['msgstr'])) == names, msgid


@pytest.mark.parametrize('lang', LANGS)
def test_compiled_catalog_is_current(lang):
    """ Rerun `python3 tools/i18n.py compile` after editing a .po """
    header, entries = tool.read_po('po/%s.po' % lang)
    with open(tool.mo_path(lang), 'rb') as f:
        assert f.read() == tool.mo_bytes(header, entries)


@pytest.mark.parametrize('inx', sorted(glob.glob('extension/*.inx')))
def test_inx_uses_catalog(inx):
    root = ET.parse(inx).getroot()
    assert root.get('translationdomain') == i18n.DOMAIN


@pytest.fixture
def french(monkeypatch):
    monkeypatch.delenv('INKEX_GETTEXT_DIRECTORY', raising=False)
    monkeypatch.setenv('LANGUAGE', 'fr')
    i18n.reset()
    yield
    i18n.reset()


def test_messages_in_french(french):
    from cutscape import job
    assert i18n._('Nothing to cut') == 'Rien à découper'
    with pytest.raises(job.NoPathsError, match='Rien à découper'):
        job.plan([], 100, job.Settings())
    problems = job.plan([[(0, 0), (60, 0)]], 100,
                        job.Settings(width=40)).problems()
    assert problems == [
        'Le travail fait 60.0 mm de large mais le matériau ne fait que '
        '40.0 mm.']


def test_unknown_language_falls_back(monkeypatch):
    monkeypatch.setenv('LANGUAGE', 'xx')
    i18n.reset()
    try:
        assert i18n._('Nothing to cut') == 'Nothing to cut'
    finally:
        i18n.reset()
