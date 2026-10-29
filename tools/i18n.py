#!/usr/bin/env python3
"""
CutScape translations, without needing GNU gettext installed.

  python3 tools/i18n.py extract   strings from the .inx and Python sources into
                                  po/cutscape.pot, merged into every po/*.po
  python3 tools/i18n.py compile   po/<lang>.po into
                                  extension/locale/<lang>/LC_MESSAGES/cutscape.mo

To add a language, copy po/cutscape.pot to po/<lang>.po, translate it (any PO
editor works), then run compile. Inkscape translates the dialog from the
compiled catalog; the script uses the same catalog for its messages.
"""
import array
import ast
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION = os.path.join(ROOT, 'extension')
PO_DIR = os.path.join(ROOT, 'po')
DOMAIN = 'cutscape'
INX_NS = '{http://www.inkscape.org/namespace/inkscape/extension}'

HEADER = (
    "Project-Id-Version: CutScape\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
)


# -----------------------------------------------------------------------------
# Extraction
# -----------------------------------------------------------------------------
def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, '/')


def inx_strings(path):
    """ (msgid, reference) for each string Inkscape translates in an .inx """
    out = []

    def add(text):
        if text and text.strip():
            out.append((text.strip(), _rel(path)))

    for el in ET.parse(path).getroot().iter():
        if el.get('translatable') == 'no':
            continue
        tag = el.tag.replace(INX_NS, '')
        if tag in ('name', 'label', 'menu-tip', 'option'):
            add(el.text)
        if tag == 'submenu':
            add(el.get('name'))
        for attr in ('gui-text', 'gui-description'):
            add(el.get(attr))
    return out


def py_strings(path):
    """ (msgid, reference) for each _("literal") call """
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read(), path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == '_' and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            out.append((node.args[0].value,
                        '%s:%i' % (_rel(path), node.lineno)))
    return out


def sources():
    for dirpath, dirs, files in os.walk(EXTENSION):
        dirs[:] = sorted(d for d in dirs if d not in ('__pycache__', 'locale'))
        for name in sorted(files):
            if name.endswith(('.inx', '.py')):
                yield os.path.join(dirpath, name)


def extract():
    """ OrderedDict msgid -> {'refs': [...], 'flags': set()} """
    entries = OrderedDict()
    for path in sources():
        found = inx_strings(path) if path.endswith('.inx') else py_strings(path)
        for msgid, ref in found:
            e = entries.setdefault(msgid, {'refs': [], 'flags': set()})
            e['refs'].append(ref)
            if path.endswith('.py') and re.search(r'\{\w*[:}]', msgid):
                e['flags'].add('python-brace-format')
    return entries


# -----------------------------------------------------------------------------
# PO files
# -----------------------------------------------------------------------------
def _escape(s):
    return (s.replace('\\', '\\\\').replace('"', '\\"')
            .replace('\n', '\\n').replace('\t', '\\t'))


def _unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            i += 1
            out.append({'n': '\n', 't': '\t'}.get(s[i], s[i]))
        else:
            out.append(c)
        i += 1
    return ''.join(out)


def read_po(path):
    """ Return (header, OrderedDict msgid -> entry) where an entry has
    msgstr, flags and comments (translator comments).
    """
    entries = OrderedDict()
    header = HEADER
    cur = None
    field = None

    def finish():
        nonlocal header
        if cur is None or cur.get('msgid') is None:
            return
        if cur['msgid'] == '':
            header = cur['msgstr']
        else:
            entries[cur['msgid']] = cur

    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()
    for line in lines + ['']:
        line = line.strip()
        if not line:
            finish()
            cur, field = None, None
            continue
        if line.startswith('#~'):
            continue
        if cur is None:
            cur = {'msgid': None, 'msgstr': '', 'flags': set(),
                   'comments': []}
        if line.startswith('#,'):
            cur['flags'].update(f.strip() for f in line[2:].split(','))
        elif line.startswith('# ') or line == '#':
            cur['comments'].append(line[2:])
        elif line.startswith('#'):
            continue
        elif line.startswith('msgid '):
            field = 'msgid'
            cur['msgid'] = _unescape(line[7:-1])
        elif line.startswith('msgstr '):
            field = 'msgstr'
            cur['msgstr'] = _unescape(line[8:-1])
        elif line.startswith('"') and field:
            cur[field] += _unescape(line[1:-1])
    return header, entries


def _po_string(keyword, value):
    if '\n' in value[:-1]:
        parts = value.splitlines(True)
        return '%s ""\n%s\n' % (keyword, '\n'.join(
            '"%s"' % _escape(p) for p in parts))
    return '%s "%s"\n' % (keyword, _escape(value))


def write_po(path, header, entries, refs):
    """ entries: msgid -> entry; refs: msgid -> (refs, flags) from extract """
    out = [_po_string('msgid', ''), _po_string('msgstr', header), '\n']
    for msgid, meta in refs.items():
        e = entries.get(msgid, {})
        for c in e.get('comments', []):
            out.append('# %s\n' % c)
        for ref in meta['refs']:
            out.append('#: %s\n' % ref)
        flags = set(meta['flags']) | (set(e.get('flags', ())) & {'fuzzy'})
        if flags:
            out.append('#, %s\n' % ', '.join(sorted(flags)))
        out.append(_po_string('msgid', msgid))
        out.append(_po_string('msgstr', e.get('msgstr', '')))
        out.append('\n')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(''.join(out))


def update():
    refs = extract()
    os.makedirs(PO_DIR, exist_ok=True)
    write_po(os.path.join(PO_DIR, DOMAIN + '.pot'), HEADER, {}, refs)
    for name in sorted(os.listdir(PO_DIR)):
        if name.endswith('.po'):
            path = os.path.join(PO_DIR, name)
            header, entries = read_po(path)
            write_po(path, header, entries, refs)
            missing = [m for m in refs if not entries.get(m, {}).get('msgstr')]
            if missing:
                print('%s: %i untranslated' % (name, len(missing)))
    return refs


# -----------------------------------------------------------------------------
# MO files (the format CPython's Tools/i18n/msgfmt.py writes)
# -----------------------------------------------------------------------------
def mo_bytes(header, entries):
    messages = {'': header}
    for msgid, e in entries.items():
        if e['msgstr'] and 'fuzzy' not in e['flags']:
            messages[msgid] = e['msgstr']
    keys = sorted(messages)
    ids = strs = b''
    offsets = []
    for k in keys:
        kid, kstr = k.encode('utf-8'), messages[k].encode('utf-8')
        offsets.append((len(ids), len(kid), len(strs), len(kstr)))
        ids += kid + b'\0'
        strs += kstr + b'\0'
    n = len(keys)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    out = struct.pack('Iiiiiii', 0x950412de, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    out += array.array('i', koffsets).tobytes()
    out += array.array('i', voffsets).tobytes()
    return out + ids + strs


def mo_path(lang):
    return os.path.join(EXTENSION, 'locale', lang, 'LC_MESSAGES',
                        DOMAIN + '.mo')


def compile_all():
    for name in sorted(os.listdir(PO_DIR)):
        if not name.endswith('.po'):
            continue
        lang = name[:-3]
        header, entries = read_po(os.path.join(PO_DIR, name))
        path = mo_path(lang)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(mo_bytes(header, entries))
        print(_rel(path))


def main(argv):
    if argv[1:] == ['extract']:
        update()
    elif argv[1:] == ['compile']:
        compile_all()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
