#!/usr/bin/env bash
#
# Pull the newest in-game frame off the tethered iPhone and convert it to PNG.
#
#   ./misc/ios/shot.sh              # -> /tmp/xr_shot.png
#   ./misc/ios/shot.sh out.png      # explicit destination
#
# The engine writes a binary PPM to Documents/xr_shot.ppm every 5 s while running
# (iOS-guarded block in Layers/xrRenderGL/glHW.cpp, in CHW::Present). So the image
# this pulls is at most ~5 s old, and is the exact render target that reached the
# screen - not a mirror, not a re-render.
#
# Exit code 0 = a PNG was written. Anything else = nothing usable was produced.
#
# PPM -> PNG is done here rather than on device because the conversion is free on a
# Mac and PPM keeps the engine side dependency-free. Python's stdlib has zlib, which
# is all a truecolour PNG needs.

set -u -o pipefail

DEVICE_UDID="00008130-000564403E12001C"
BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"
OUT="${1:-/tmp/xr_shot.png}"
PPM="$(mktemp -t xrshot).ppm"

fail() { echo "FAIL: $*" >&2; exit 1; }

xcrun devicectl device copy from \
    --device "$DEVICE_UDID" \
    --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" \
    --user mobile \
    --source Documents/xr_shot.ppm \
    --destination "$PPM" >/dev/null 2>&1 \
    || fail "no Documents/xr_shot.ppm on device - is the game running, and is this a build with the dump block?"

[ -s "$PPM" ] || fail "pulled file is empty"

python3 - "$PPM" "$OUT" <<'PY' || fail "PPM -> PNG conversion failed"
import sys, zlib, struct

src, dst = sys.argv[1], sys.argv[2]
with open(src, 'rb') as f:
    data = f.read()

# P6 header: magic, width height, maxval - each separated by whitespace, and any
# token may be followed by a comment line. Parse tokens rather than assume layout.
def tokens(buf):
    i, out = 0, []
    while len(out) < 4:
        while i < len(buf) and buf[i:i+1].isspace():
            i += 1
        if buf[i:i+1] == b'#':
            while i < len(buf) and buf[i:i+1] != b'\n':
                i += 1
            continue
        j = i
        while j < len(buf) and not buf[j:j+1].isspace():
            j += 1
        out.append(buf[i:j])
        i = j
    return out, i + 1

(magic, w, h, maxval), off = tokens(data)
if magic != b'P6':
    sys.exit("not a P6 PPM")
w, h = int(w), int(h)
px = data[off:off + w * h * 3]
if len(px) < w * h * 3:
    sys.exit("truncated pixel data")

raw = b''.join(b'\x00' + px[y*w*3:(y+1)*w*3] for y in range(h))

def chunk(tag, payload):
    return (struct.pack('>I', len(payload)) + tag + payload
            + struct.pack('>I', zlib.crc32(tag + payload) & 0xffffffff))

png = (b'\x89PNG\r\n\x1a\n'
       + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(raw, 6))
       + chunk(b'IEND', b''))

with open(dst, 'wb') as f:
    f.write(png)
print(f"{w}x{h} -> {dst}")
PY

rm -f "$PPM"
echo "OK: $OUT"
