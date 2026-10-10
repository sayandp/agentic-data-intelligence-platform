"""Loudness + beat-sync check for a rendered MP4 (v2 soundtrack).
  uv run --with pyloudnorm --with soundfile --with librosa --with scipy --python 3.11 scripts/loudness.py out/raw/x.mp4
Prints integrated loudness (ITU-R BS.1770, pyloudnorm), sample peak, the
`gain` prop that would bring it to -14 LUFS, and how far the music's beats
in the RENDER sit from the 15-frame grid the cuts use.
"""
import subprocess, sys, tempfile, os
import numpy as np, soundfile as sf, pyloudnorm as pyln, librosa

src = sys.argv[1]
wav = os.path.join(tempfile.mkdtemp(), "a.wav")
subprocess.run(f'npx remotion ffmpeg -y -v error -i "{src}" -vn -c:a pcm_s16le -ar 48000 "{wav}"', shell=True, check=True)
y, sr = sf.read(wav)
loud = pyln.Meter(sr).integrated_loudness(y)
peak = 20 * np.log10(np.max(np.abs(y)) + 1e-12)
from scipy.signal import resample_poly
tp = 20 * np.log10(np.abs(resample_poly(y, 4, 1, axis=0)).max() + 1e-12)  # 4x-oversampled true-peak estimate
print(f"{src}: integrated {loud:.2f} LUFS, sample peak {peak:.2f} dBFS, true peak ~{tp:.2f} dBTP")
if "--no-beats" in sys.argv:
    sys.exit(0)

mono = y.mean(axis=1) if y.ndim > 1 else y
_, perc = librosa.effects.hpss(librosa.resample(mono, orig_sr=sr, target_sr=22050))
_, beats = librosa.beat.beat_track(y=perc, sr=22050, tightness=400, units="time")
grid = 0.5  # 120 BPM
off = np.array([((t + grid / 2) % grid) - grid / 2 for t in beats])
med = np.median(off) * 30
print(f"beats found {len(beats)}; median offset from the frame grid {med:+.2f} f, 90% within {np.percentile(np.abs(off), 90) * 30:.2f} f")
