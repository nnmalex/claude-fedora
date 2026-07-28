#!/usr/bin/python3
"""Make Quick Entry open on native Wayland.

Quick Entry's overlay is a BrowserWindow created with `show: false`, and
`activateQuickEntryWindow()` awaits its 'ready-to-show' before calling
`show()`. Under Ozone/Wayland that event does not fire until the window is
mapped, so the await never settles and the overlay never appears -- neither
from the global hotkey nor from the tray's "Open Quick Entry". Nothing else
about the feature is broken: the GlobalShortcuts portal does deliver the
accelerator, the window is created, and its page finishes loading.

Bounding the wait is enough, and costs nothing anywhere else. The first
`show()` is what makes 'ready-to-show' fire, so only the very first activation
in a session can reach the timeout; on X11 and macOS the event arrives in
well under it and the race settles early as it always did.

The edit is applied to app.asar in place, padded to the exact length of the
code it replaces, so every offset in the asar header stays valid. Only the
affected file's integrity hashes are rewritten -- SHA256 hex is fixed width,
so the header does not change size either.

Usage: patch-quick-entry-wayland.py PATH/TO/app.asar
"""

import hashlib
import json
import re
import struct
import sys

# Anchored on the log message sitting inside the await. It is unique in the
# bundle, and specific enough that upstream reworking this code is unlikely to
# leave the message untouched -- if they do rework it, this stops matching and
# the build fails rather than silently shipping an unpatched package.
AWAIT = re.compile(
    rb'await\((\w{1,6})==null\?void 0:\1\.catch\((\w{1,6})=>\{(\w{1,6})\.error\('
    rb'"Quick Entry: Error waiting for ready %o",\{error:\2\}\)\}\)\)')

TIMEOUT_MS = b'1e3'


def bounded(match):
    """Rewrite the await as a race against a timer, padded to the same length."""
    ready, arg, _log = match.groups()
    code = (b'await Promise.race([' + ready + b',new Promise(' + arg +
            b'=>setTimeout(' + arg + b',' + TIMEOUT_MS + b'))]).catch(()=>{})')
    slack = len(match.group(0)) - len(code)
    if slack < 0:
        sys.exit('quick-entry patch: replacement is longer than the original')
    return code + b' ' * slack


def packed_files(node, path=()):
    """Yield (name, entry) for every file stored inside the archive itself."""
    for name, entry in node.get('files', {}).items():
        if 'files' in entry:
            yield from packed_files(entry, path + (name,))
        elif 'offset' in entry:  # entries flagged `unpacked` live outside it
            yield '/'.join(path + (name,)), entry


def rewrite_hash(header, old, new):
    """Swap one 64-char hex digest for another, in place, inside the header."""
    if header.count(old) != 1:
        sys.exit('quick-entry patch: hash %s is not unique in the asar header'
                 % old.decode())
    return header.replace(old, new)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = sys.argv[1]

    blob = bytearray(open(path, 'rb').read())
    _, rest, _, json_size = struct.unpack('<IIII', bytes(blob[:16]))
    header = json.loads(bytes(blob[16:16 + json_size]))
    base = 8 + rest

    hits = []
    for name, entry in packed_files(header):
        start = base + int(entry['offset'])
        chunk = bytes(blob[start:start + entry['size']])
        found = list(AWAIT.finditer(chunk))
        if found:
            hits.append((name, entry, start, chunk, found))

    if len(hits) != 1 or len(hits[0][4]) != 1:
        sys.exit('quick-entry patch: expected exactly one match for the '
                 'ready-to-show await, found %d in %d file(s) -- upstream has '
                 'changed and the patch needs revisiting'
                 % (sum(len(h[4]) for h in hits), len(hits)))

    name, entry, start, chunk, (match,) = hits[0]
    patched = chunk[:match.start()] + bounded(match) + chunk[match.end():]
    assert len(patched) == len(chunk)
    blob[start:start + len(chunk)] = patched

    integrity = entry.get('integrity')
    if integrity:
        header_bytes = bytes(blob[16:16 + json_size])
        header_bytes = rewrite_hash(
            header_bytes, integrity['hash'].encode(),
            hashlib.sha256(patched).hexdigest().encode())
        size = integrity['blockSize']
        for index, old in enumerate(integrity['blocks']):
            block = patched[index * size:(index + 1) * size]
            header_bytes = rewrite_hash(
                header_bytes, old.encode(),
                hashlib.sha256(block).hexdigest().encode())
        blob[16:16 + json_size] = header_bytes

    with open(path, 'wb') as out:
        out.write(blob)
    print('patched Quick Entry ready-to-show await in %s' % name)


if __name__ == '__main__':
    main()
