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


@pytest.fixture
def flatpak(tmp_path, monkeypatch):
    """ Inkscape's Flatpak, with the devices Flathub lets it use """
    info = tmp_path / 'flatpak-info'
    info.write_text('[Application]\nname=org.inkscape.Inkscape\n\n'
                    '[Context]\nshared=network;ipc;\ndevices=dri;\n')
    from cutscape import transports
    monkeypatch.setattr(transports, 'FLATPAK_INFO', str(info))
    return info
