"""
RepoLM — Podcast Audio Generator
Converts podcast script to audio using TTS.
Supports ElevenLabs (high quality) and edge-tts (free fallback).
"""

import os
import re
import sys
import asyncio
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple

# Try ElevenLabs first, fall back to edge-tts
TTS_ENGINE = None
try:
    from elevenlabs import generate, save, set_api_key, voices
    TTS_ENGINE = "elevenlabs"
except ImportError:
    pass

if not TTS_ENGINE:
    try:
        import edge_tts
        TTS_ENGINE = "edge_tts"
    except ImportError:
        pass


# ElevenLabs voice mapping
ELEVEN_VOICES = {
    "ALEX": "Josh",      # Enthusiastic, young male
    "SAM": "Rachel",     # Knowledgeable, clear female
}

# Edge-TTS voice mapping (free, decent quality)
EDGE_VOICES = {
    "ALEX": "en-US-GuyNeural",
    "SAM": "en-US-JennyNeural",
}


@dataclass
class DialogueLine:
    speaker: str
    text: str


def parse_script(script_path: str) -> List[DialogueLine]:
    """Parse podcast script into dialogue lines."""
    with open(script_path, "r") as f:
        content = f.read()

    lines = []
    pattern = r"(ALEX|SAM):\s*(.+?)(?=\n(?:ALEX|SAM):|$)"
    matches = re.findall(pattern, content, re.DOTALL)

    for speaker, text in matches:
        text = text.strip()
        # Clean up markdown artifacts
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # bold
        text = re.sub(r'`(.+?)`', r'\1', text)  # inline code
        if text:
            lines.append(DialogueLine(speaker=speaker, text=text))

    return lines


async def generate_edge_tts(text: str, voice: str, output_path: str):
    """Generate audio using edge-tts (free)."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def generate_elevenlabs_tts(text: str, voice: str, output_path: str):
    """Generate audio using ElevenLabs."""
    if os.environ.get("ELEVENLABS_API_KEY"):
        set_api_key(os.environ["ELEVENLABS_API_KEY"])
    audio = generate(text=text, voice=voice, model="eleven_monolingual_v1")
    save(audio, output_path)


def generate_audio(script_path: str, output_dir: str = "output") -> str:
    """Generate podcast audio from script."""
    if not TTS_ENGINE:
        print("No TTS engine available. Install: pip3 install edge-tts (free) or elevenlabs")
        sys.exit(1)

    lines = parse_script(script_path)
    if not lines:
        print("No dialogue lines found in script!")
        sys.exit(1)

    repo_name = Path(script_path).stem.replace("_podcast", "")
    audio_dir = os.path.join(output_dir, f"{repo_name}_audio")
    os.makedirs(audio_dir, exist_ok=True)

    print(f"Generating audio with {TTS_ENGINE} ({len(lines)} segments)...")

    segment_paths = []
    for i, line in enumerate(lines):
        segment_path = os.path.join(audio_dir, f"{i:03d}_{line.speaker.lower()}.mp3")

        if TTS_ENGINE == "elevenlabs":
            voice = ELEVEN_VOICES.get(line.speaker, "Josh")
            generate_elevenlabs_tts(line.text, voice, segment_path)
        elif TTS_ENGINE == "edge_tts":
            voice = EDGE_VOICES.get(line.speaker, "en-US-GuyNeural")
            asyncio.run(generate_edge_tts(line.text, voice, segment_path))

        segment_paths.append(segment_path)
        print(f"  [{i+1}/{len(lines)}] {line.speaker}: {line.text[:50]}...")

    # Concatenate segments using ffmpeg
    final_path = os.path.join(output_dir, f"{repo_name}_podcast.mp3")
    concat_file = os.path.join(audio_dir, "concat.txt")
    with open(concat_file, "w") as f:
        for sp in segment_paths:
            f.write(f"file '{os.path.abspath(sp)}'\n")

    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file, "-c", "copy", final_path
    ], capture_output=True)

    print(f"\nPodcast saved: {final_path}")
    return final_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python podcast_audio.py <podcast_script.md>")
        sys.exit(1)
    generate_audio(sys.argv[1])
