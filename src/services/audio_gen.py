"""
RepoLM — Podcast audio generation service.
Parses podcast scripts and generates audio via edge-tts.
"""

import os
import re
import asyncio
import subprocess
from config import EDGE_VOICES, OUTPUT_DIR
import state


def parse_podcast_script(text):
    """Parse ALEX:/SAM: dialogue lines from a podcast script."""
    pattern = r"(ALEX|SAM):\s*(.+?)(?=\n(?:ALEX|SAM):|$)"
    matches = re.findall(pattern, text, re.DOTALL)
    lines = []
    for speaker, dialogue in matches:
        dialogue = dialogue.strip()
        dialogue = re.sub(r'\*\*(.+?)\*\*', r'\1', dialogue)
        dialogue = re.sub(r'`(.+?)`', r'\1', dialogue)
        if dialogue:
            lines.append((speaker, dialogue))
    return lines


async def _generate_audio_segments(lines, audio_dir, progress_cb=None):
    import edge_tts

    async def _gen_one(i, speaker, text):
        path = os.path.join(audio_dir, f"{i:03d}_{speaker.lower()}.mp3")
        voice = EDGE_VOICES.get(speaker, "en-US-GuyNeural")
        comm = edge_tts.Communicate(text, voice)
        await comm.save(path)
        return i, path

    segment_paths = [None] * len(lines)
    completed = 0
    batch_size = 10
    for start in range(0, len(lines), batch_size):
        batch = lines[start:start + batch_size]
        tasks = [_gen_one(start + j, speaker, text) for j, (speaker, text) in enumerate(batch)]
        results = await asyncio.gather(*tasks)
        for idx, path in results:
            segment_paths[idx] = path
            completed += 1
        if progress_cb:
            progress_cb(completed, len(lines))
    return segment_paths


def generate_podcast_audio(script_text, audio_id):
    """Generate podcast audio, return path to mp3."""
    lines = parse_podcast_script(script_text)
    if not lines:
        return None
    audio_dir = os.path.join(OUTPUT_DIR, f"audio_{audio_id}")
    os.makedirs(audio_dir, exist_ok=True)

    def _progress(done, total):
        job = state.audio_jobs.get(audio_id)
        if job:
            job["progress"] = done
            job["total"] = total

    loop = asyncio.new_event_loop()
    segment_paths = loop.run_until_complete(_generate_audio_segments(lines, audio_dir, progress_cb=_progress))
    loop.close()

    final_path = os.path.join(OUTPUT_DIR, f"podcast_{audio_id}.mp3")
    concat_file = os.path.join(audio_dir, "concat.txt")
    with open(concat_file, "w") as f:
        for sp in segment_paths:
            f.write(f"file '{os.path.abspath(sp)}'\n")

    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", final_path],
        capture_output=True
    )
    if result.returncode != 0:
        with open(final_path, "wb") as out:
            for sp in segment_paths:
                with open(sp, "rb") as seg:
                    out.write(seg.read())
    return final_path
