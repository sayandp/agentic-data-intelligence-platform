"""Fit a beat grid to a BGM track (music-beat-sync.md section 1-3).
  uv run --with librosa --with scipy --python 3.11 scripts/beat_grid.py <mp3> <out.json>
Writes bpm, t0, T (float seconds, never pre-rounded), the residual, kick
strengths per integer beat, an RMS curve, and the 0.5x/1x/2x candidates."""
import json, sys
import numpy as np, librosa
from scipy.signal import butter, sosfilt

path, out = sys.argv[1], sys.argv[2]
y, sr = librosa.load(path, sr=None, mono=True)
_, perc = librosa.effects.hpss(y)
tempo, beats = librosa.beat.beat_track(y=perc, sr=sr, tightness=400, units="time")
i = np.arange(len(beats))
(T, t0), *_ = np.linalg.lstsq(np.vstack([i, np.ones_like(i)]).T, beats, rcond=None)
res = beats - (t0 + i * T)

def band_env(lo, hi):
    sos = butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    env = librosa.onset.onset_strength(y=sosfilt(sos, perc), sr=sr)
    return env, librosa.times_like(env, sr=sr)

kick, kt = band_env(40, 160)
onsets = librosa.onset.onset_detect(y=perc, sr=sr, units="time")
def score(Tc):
    n = int((len(y) / sr - t0) / Tc)
    grid = t0 + np.arange(n) * Tc
    d = np.array([np.min(np.abs(onsets - g)) for g in grid])
    return {"T": Tc, "bpm": 60 / Tc, "match": float(np.mean(d < 0.035)), "mean_abs_ms": float(np.mean(np.minimum(d, 0.1)) * 1000)}
cands = {k: score(T * f) for k, f in {"0.5x": 2.0, "1x": 1.0, "2x": 0.5}.items()}
n = int((len(y) / sr - t0) / T)
kicks = [float(kick[np.argmin(np.abs(kt - (t0 + k * T)))]) for k in range(n)]
rms = librosa.feature.rms(y=y)[0]
rt = librosa.times_like(rms, sr=sr)
per_beat_rms = [float(rms[np.argmin(np.abs(rt - (t0 + k * T)))]) for k in range(n)]
data = {"file": path, "duration": len(y) / sr, "bpm": 60 / T, "t0": float(t0), "T": float(T),
        "residual_max_ms": float(np.abs(res).max() * 1000), "candidates": cands,
        "kick_per_beat": kicks, "rms_per_beat": per_beat_rms}
json.dump(data, open(out, "w"), indent=1)
print(f"{path}: BPM={data['bpm']:.3f} t0={t0:.4f}s residual±{data['residual_max_ms']:.0f}ms dur={data['duration']:.1f}s")
for k, c in cands.items(): print("  ", k, {kk: round(v, 3) for kk, v in c.items()})
