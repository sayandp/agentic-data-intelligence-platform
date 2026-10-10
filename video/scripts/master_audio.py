"""Master a v2 render's soundtrack to -14 LUFS integrated with a -1 dBFS
true-peak target (look-ahead peak limiter, numpy only), and write a WAV that
scripts/finalize.mjs muxes back in.

  uv run --with pyloudnorm --with soundfile --with numpy --with scipy --python 3.11 scripts/master_audio.py <in.mp4|wav> <out.wav> [--target -16] [--ceiling -2.0] [--gain-from <other.wav.gain.json>]

Writes <out.wav>.gain.json with the total gain applied. --gain-from reuses
another master's gain instead of normalising: the SFX-only cut takes the
music cut's gain, so every effect sits at the same level in both versions
(normalising a sparse SFX-only track to -14 LUFS would pump the effects).

The render already sums music under SFX; this stage only sets the overall
level and stops the impacts from clipping when it does.
"""
import json, os, subprocess, sys, tempfile
import numpy as np, pyloudnorm as pyln, soundfile as sf
from scipy.ndimage import maximum_filter1d
from scipy.signal import resample_poly

def _arg(name, default):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default

# --target LUFS (default -14) and --ceiling dBFS sample ceiling (default -1.6,
# room for inter-sample and AAC overshoot under -1 dBTP)
TARGET = _arg("--target", -14.0)
CEIL = 10 ** (_arg("--ceiling", -1.6) / 20)

src, out = sys.argv[1], sys.argv[2]
gain_from = sys.argv[sys.argv.index("--gain-from") + 1] if "--gain-from" in sys.argv else None
tmp = os.path.join(tempfile.mkdtemp(), "a.wav")
subprocess.run(f'npx remotion ffmpeg -y -v error -i "{src}" -vn -c:a pcm_s16le -ar 48000 "{tmp}"', shell=True, check=True)
x, sr = sf.read(tmp, always_2d=True)
meter = pyln.Meter(sr)

def limit(sig):
    look = int(0.005 * sr)  # 5 ms look-ahead
    rel = np.exp(-1 / (0.08 * sr))  # 80 ms release
    peak = maximum_filter1d(np.abs(sig).max(axis=1), size=2 * look + 1)
    need = np.minimum(1.0, CEIL / np.maximum(peak, 1e-9))
    # instant attack (already looked ahead), smooth release: a one-pole
    # follower on the reduction, taking the min of follower and need.
    g = np.empty_like(need)
    cur = 1.0
    for i, n in enumerate(need):  # tight loop; ~2-4M samples is fine
        cur = n if n < cur else rel * cur + (1 - rel) * n
        g[i] = cur
    return sig * g[:, None]

y = x.copy()
total = 1.0
if gain_from:
    total = json.load(open(gain_from))["gain"]
    y = limit(y * total)
    print(f"fixed gain x{total:.3f} (from {gain_from}): {meter.integrated_loudness(y):.2f} LUFS")
for it in range(0 if gain_from else 4):
    gain = 10 ** ((TARGET - meter.integrated_loudness(y)) / 20)
    total *= gain
    y = limit(y * gain)
    l = meter.integrated_loudness(y)
    # 4x-oversampled peak as a true-peak estimate
    tp = 20 * np.log10(np.abs(resample_poly(y, 4, 1, axis=0)).max())
    print(f"pass {it + 1}: {l:.2f} LUFS, true peak ~{tp:.2f} dBTP")
    if abs(l - TARGET) < 0.2:
        break
sf.write(out, y.astype(np.float32), sr, subtype="PCM_16")
json.dump({"gain": total}, open(out + ".gain.json", "w"))
print(f"-> {out}")
