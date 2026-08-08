"""Sonix weekly audio listener. Processes only explicitly allowlisted audio."""
from __future__ import annotations
import audioop, hashlib, json, math, os, subprocess, tempfile, urllib.request, wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ALLOWED = {"CC0-1.0", "PDM-1.0", "PUBLIC-DOMAIN", "CUSTOM-PERMISSION"}
MAX_BYTES = 80 * 1024 * 1024

def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Sonix-Weekly-Listener/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response, target.open("wb") as output:
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("audio exceeds 80 MB limit")
            output.write(chunk)

def analyze(source: Path) -> dict:
    wav = source.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ac", "1", "-ar", "22050", str(wav)], check=True)
    with wave.open(str(wav), "rb") as stream:
        rate, width, frames = stream.getframerate(), stream.getsampwidth(), stream.getnframes()
        raw = stream.readframes(frames)
    duration = frames / rate if rate else 0
    rms = audioop.rms(raw, width) if raw else 0
    peak = audioop.max(raw, width) if raw else 0
    samples = audioop.lin2lin(raw, width, 2)
    values = memoryview(samples).cast("h") if samples else []
    crossings = sum(1 for a, b in zip(values, values[1:]) if (a < 0 <= b) or (a >= 0 > b))
    zcr = crossings / max(1, len(values) - 1)
    window = max(1, rate // 2)
    energy = [sum(abs(v) for v in values[i:i+window]) for i in range(0, len(values), window)]
    onsets = sum(1 for a, b in zip(energy, energy[1:]) if b > a * 1.45 and b > 0)
    approx_bpm = round(onsets * 60 / max(duration, 1), 1)
    return {"duration_seconds": round(duration, 2), "rms": rms, "peak": peak, "zero_crossing_rate": round(zcr, 5), "approx_bpm": approx_bpm}

def main() -> None:
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    brain_path = ROOT / "genre-brain.json"
    brain = json.loads(brain_path.read_text(encoding="utf-8")) if brain_path.exists() else {"version":"1.1","genres":{}}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "policy":"Sonix Safe Learning 1.1", "tracks": [], "errors": []}
    for item in catalog.get("tracks", []):
        try:
            required = {"id", "title", "genre", "audio_url", "source_url", "license", "license_url"}
            if missing := required - item.keys(): raise ValueError(f"missing fields: {sorted(missing)}")
            if item["license"] not in ALLOWED: raise ValueError("license is not allowlisted")
            if not item["audio_url"].startswith("https://"): raise ValueError("audio_url must use HTTPS")
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source.audio"
                download(item["audio_url"], source)
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                result = analyze(source)
            report["tracks"].append({**{k:item[k] for k in required}, "sha256": digest, "analysis": result})
        except Exception as exc:
            report["errors"].append({"id": item.get("id", "unknown"), "error": str(exc)})
    for track in report["tracks"]:
        genre = track["genre"].strip().title()
        entry = brain["genres"].setdefault(genre, {"samples":[], "profile":{}})
        if not any(sample["sha256"] == track["sha256"] for sample in entry["samples"]):
            entry["samples"].append({"id":track["id"], "sha256":track["sha256"], "source_url":track["source_url"], "license":track["license"], "license_url":track["license_url"], "analysis":track["analysis"]})
        analyses = [sample["analysis"] for sample in entry["samples"]]
        entry["profile"] = {"authorized_samples":len(analyses), "average_bpm":round(sum(a["approx_bpm"] for a in analyses)/len(analyses),1), "average_rms":round(sum(a["rms"] for a in analyses)/len(analyses),1), "average_zero_crossing_rate":round(sum(a["zero_crossing_rate"] for a in analyses)/len(analyses),5), "last_updated":report["generated_at"]}
    brain["updated_at"] = report["generated_at"]
    brain["policy"] = {"allowed_licenses":sorted(ALLOWED), "copyrighted_music":False, "stores_audio":False, "purpose":"genre-level acoustic statistics only"}
    (ROOT / "weekly-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    brain_path.write_text(json.dumps(brain, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
