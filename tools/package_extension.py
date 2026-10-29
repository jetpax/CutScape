#!/usr/bin/env python3
"""
Build dist/cutscape-<version>.zip. It holds a single cutscape/ folder to
unzip into Inkscape's user extensions folder.
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'extension')


def main():
    sys.path.insert(0, SRC)
    from cutscape import __version__
    os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)
    out = os.path.join(ROOT, 'dist', 'cutscape-%s.zip' % __version__)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(SRC):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__')
            for f in sorted(files):
                if f.endswith('.pyc'):
                    continue
                path = os.path.join(dirpath, f)
                z.write(path, os.path.join(
                    'cutscape', os.path.relpath(path, SRC)))
    print(out)


if __name__ == '__main__':
    main()
