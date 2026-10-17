"""Pre-generate Dadi's filler clips with Bulbul into static/fillers/<lang>.json (ships with the app, so serverless
cold starts never wait for TTS). Re-run after changing DADI_VOICES, DADI_PACE or prompts.FILLERS."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config, dadi, prompts  # noqa: E402
from app import sarvam_client as sc  # noqa: E402

out_dir = ROOT / "static" / "fillers"
out_dir.mkdir(parents=True, exist_ok=True)
for lang, lines in prompts.FILLERS.items():
    clips = []
    for text in lines:
        # 16 kHz keeps the files small; fillers are short and do not need studio quality.
        audio = sc._sdk().text_to_speech.convert(text=text, language_code=lang, speaker=dadi.voice(lang),
                                                 model=config.TTS_MODEL, pace=config.DADI_PACE,
                                                 speech_sample_rate=16000).audios[0]
        clips.append({"text": text, "audio_base64": audio})
    path = out_dir / f"{lang}.json"
    path.write_text(json.dumps({"lang": lang, "voice": dadi.voice(lang), "clips": clips}, ensure_ascii=False))
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB, {len(clips)} clips)")
