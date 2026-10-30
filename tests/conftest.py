import os

#: Messages go to stderr in tests, never to a window
os.environ['CUTSCAPE_STDERR'] = '1'
