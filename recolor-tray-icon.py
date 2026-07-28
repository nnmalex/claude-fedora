#!/usr/bin/python3
"""Tint the Linux tray icons with Claude's brand colour.

Upstream ships the Linux tray icon as two flat silhouettes -- TrayIconLinux.png
is solid black, TrayIconLinux-Dark.png is solid white -- and only the alpha
channel carries the shape. The app picks between them with

    Mme()==="gnome" || nativeTheme.shouldUseDarkColors
        ? "TrayIconLinux-Dark.png" : "TrayIconLinux.png"

which outside GNOME follows the *application* colour scheme, not the panel's.
Plasma's panel is dark under a light Breeze theme, so that combination paints
the black silhouette onto a near-black background and the tray entry is a black
smudge.

Recolouring both files sidesteps the mismatch instead of trying to outguess it:
whichever branch is taken, the icon is Claude orange, which reads against a
light panel and a dark one alike.

Only the RGB channels are rewritten. Every alpha value is copied through
untouched, so the anti-aliased edges survive exactly as drawn. An image that is
not the flat single-colour silhouette this expects is refused, so an upstream
redesign fails the build rather than being silently repainted.

Usage: recolor-tray-icon.py PATH/TO/TrayIcon.png [...]
"""

import binascii
import struct
import sys
import zlib

# Claude orange, the brand's primary accent.
BRAND = (0xD9, 0x77, 0x57)

# The silhouettes we are prepared to recolour: solid black, or solid white.
EXPECTED = {(0x00, 0x00, 0x00), (0xFF, 0xFF, 0xFF)}

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def chunks(blob):
    """Yield (type, data) for each chunk of a PNG file."""
    if blob[:8] != PNG_MAGIC:
        raise ValueError('not a PNG')
    pos = 8
    while pos < len(blob):
        size, kind = struct.unpack('>I4s', blob[pos:pos + 8])
        yield kind, blob[pos + 8:pos + 8 + size]
        pos += 12 + size  # length, type, data, CRC


def paeth(a, b, c):
    """PNG's predictor: the neighbour closest to left + above - upper-left."""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def unfilter(raw, width, height):
    """Undo the per-scanline filters, returning flat 8-bit RGBA pixels."""
    stride = width * 4
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        kind, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0      # pixel to the left
            b = prev[i]                           # pixel above
            c = prev[i - 4] if i >= 4 else 0      # pixel above-left
            if kind == 0:
                continue
            elif kind == 1:
                line[i] = (line[i] + a) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + b) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif kind == 4:
                line[i] = (line[i] + paeth(a, b, c)) & 0xFF
            else:
                raise ValueError('unknown scanline filter %d' % kind)
        out += line
        prev = line
    return out


def encode(pixels, width, height):
    """Build a minimal 8-bit RGBA PNG. Scanlines go out unfiltered."""
    stride = width * 4
    raw = b''.join(b'\0' + bytes(pixels[y * stride:(y + 1) * stride])
                   for y in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack(
            '>I', binascii.crc32(body))

    return (PNG_MAGIC
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))


def recolor(path):
    """Repaint one silhouette. Returns False if it was already Claude orange."""
    blob = open(path, 'rb').read()

    header, data = None, b''
    for kind, payload in chunks(blob):
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT':
            data += payload

    width, height, depth, color, compression, filt, interlace = header
    if (depth, color) != (8, 6) or interlace or compression or filt:
        sys.exit('%s: expected a non-interlaced 8-bit RGBA PNG' % path)

    pixels = unfilter(zlib.decompress(data), width, height)

    present = {tuple(pixels[i:i + 3]) for i in range(0, len(pixels), 4)}
    if present == {BRAND}:
        return False
    if len(present) != 1 or not present <= EXPECTED:
        sys.exit('%s: not the flat black or white silhouette this expects '
                 '(found %d colour(s)) -- upstream has changed and the tint '
                 'needs revisiting' % (path, len(present)))

    for i in range(0, len(pixels), 4):
        pixels[i:i + 3] = BRAND

    with open(path, 'wb') as out:
        out.write(encode(pixels, width, height))
    return True


def main():
    paths = sys.argv[1:]
    if not paths:
        sys.exit(__doc__.strip().splitlines()[-1])
    for path in paths:
        done = recolor(path)
        print('%s %s' % ('tinted' if done else 'already tinted:', path))


if __name__ == '__main__':
    main()
