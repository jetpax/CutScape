import os

import pytest

#: Messages go to stderr in tests, never to a window
os.environ['CUTSCAPE_STDERR'] = '1'


@pytest.fixture(autouse=True)
def cutter_config(tmp_path, monkeypatch):
    """ Where Add Cutter remembers the cutter: never the real one """
    path = tmp_path / 'config' / 'cutter.json'
    monkeypatch.setenv('CUTSCAPE_CONFIG', str(path))
    return path
