"""
RepoLM — Podcast audio generation service.
Parses podcast scripts and generates audio via edge-tts with SSML for natural speech.
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
        # Strip markdown bold/code
        dialogue = re.sub(r'\*\*(.+?)\*\*', r'\1', dialogue)
        dialogue = re.sub(r'`(.+?)`', r'\1', dialogue)
        # Remove stage directions like [LAUGHS], [PAUSE], [TYPING SOUNDS], etc.
        dialogue = re.sub(r'\[([A-Z\s]+)\]', '', dialogue)
        dialogue = dialogue.strip()
        if dialogue:
            lines.append((speaker, dialogue))
    return lines


def _text_to_ssml(text, speaker):
    """Convert plain dialogue text to SSML for more natural speech."""
    # Escape XML special chars
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Add pauses after ellipses (thinking pauses)
    text = re.sub(r'\.\.\.', '<break time="600ms"/>', text)

    # Add pauses after em-dashes (dramatic pauses)
    text = re.sub(r'\s*—\s*', ' <break time="400ms"/> ', text)

    # Add emphasis on words in ALL CAPS (but not short ones like I, A)
    def _emphasize(m):
        word = m.group(0)
        if len(word) <= 2:
            return word
        return '<emphasis level="strong">{}</emphasis>'.format(word.capitalize())
    text = re.sub(r'\b[A-Z]{3,}\b', _emphasize, text)

    # Add natural pauses at sentence boundaries
    text = re.sub(r'([.!?])\s+', r'\1 <break time="300ms"/> ', text)

    # Add a brief pause after colons (setup for explanation)
    text = re.sub(r':\s+', ': <break time="250ms"/> ', text)

    # Vary prosody slightly per speaker for more distinction
    if speaker == "ALEX":
        # Alex: slightly faster, higher pitch (enthusiastic)
        prosody = '<prosody rate="1.05" pitch="+2%">{}</prosody>'.format(text)
    else:
        # Sam: slightly slower, calmer (authoritative)
        prosody = '<prosody rate="0.97" pitch="-1%">{}</prosody>'.format(text)

    voice = EDGE_VOICES.get(speaker, "en-US-AndrewMultilingualNeural")
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        '<voice name="{voice}">'
        '{prosody}'
        '</voice>'
        '</speak>'
    ).format(voice=voice, prosody=prosody)

    return ssml


async def _generate_audio_segments(lines, audio_dir, progress_cb=None):
    import edge_tts

    async def _gen_one(i, speaker, text):
        path = os.path.join(audio_dir, "{:03d}_{}.mp3".format(i, speaker.lower()))
        voice = EDGE_VOICES.get(speaker, "en-US-AndrewMultilingualNeural")
        ssml = _text_to_ssml(text, speaker)
        try:
            # Try SSML first
            comm = edge_tts.Communicate(ssml, voice)
            await comm.save(path)
        except Exception:
            # Fallback to plain text if SSML fails
            comm = edge_tts.Communicate(text, voice)
            await comm.save(path)
        return i, path

    segment_paths = [None] * len(lines)
    completed = 0
    # Process in batches to avoid overwhelming the API
    batch_size = 8
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


def _add_silence(duration_ms, output_path):
    """Generate a silent audio segment using ffmpeg."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             "anullsrc=r=24000:cl=mono", "-t", str(duration_ms / 1000.0),
             "-c:a", "libmp3lame", "-q:a", "9", output_path],
            capture_output=True, timeout=10
        )
        return os.path.exists(output_path)
    except Exception:
        return False


def generate_podcast_audio(script_text, audio_id):
    """Generate podcast audio with natural pauses between speakers. Return path to mp3."""
    lines = parse_podcast_script(script_text)
    if not lines:
        return None
    audio_dir = os.path.join(OUTPUT_DIR, "audio_{}".format(audio_id))
    os.makedirs(audio_dir, exist_ok=True)

    def _progress(done, total):
        job = state.audio_jobs.get(audio_id)
        if job:
            job["progress"] = done
            job["total"] = total

    loop = asyncio.new_event_loop()
    segment_paths = loop.run_until_complete(_generate_audio_segments(lines, audio_dir, progress_cb=_progress))
    loop.close()

    # Build concat list with silence gaps between speakers for natural feel
    final_path = os.path.join(OUTPUT_DIR, "podcast_{}.mp3".format(audio_id))
    concat_file = os.path.join(audio_dir, "concat.txt")

    # Generate silence segments
    short_pause = os.path.join(audio_dir, "pause_short.mp3")  # same speaker continuation
    long_pause = os.path.join(audio_dir, "pause_long.mp3")    # speaker change
    has_short = _add_silence(200, short_pause)
    has_long = _add_silence(500, long_pause)

    with open(concat_file, "w") as f:
        prev_speaker = None
        for i, sp in enumerate(segment_paths):
            if sp is None:
                continue
            # Add pause between segments
            if prev_speaker is not None:
                current_speaker = lines[i][0] if i < len(lines) else None
                if current_speaker != prev_speaker and has_long:
                    # Speaker change — longer pause
                    f.write("file '{}'\n".format(os.path.abspath(long_pause)))
                elif has_short:
                    # Same speaker continuing — short pause
                    f.write("file '{}'\n".format(os.path.abspath(short_pause)))
            f.write("file '{}'\n".format(os.path.abspath(sp)))
            prev_speaker = lines[i][0] if i < len(lines) else prev_speaker

    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-c:a", "libmp3lame", "-q:a", "2", final_path],
        capture_output=True
    )
    if result.returncode != 0:
        # Fallback: just concatenate raw bytes
        with open(final_path, "wb") as out:
            for sp in segment_paths:
                if sp:
                    with open(sp, "rb") as seg:
                        out.write(seg.read())
    return final_path
