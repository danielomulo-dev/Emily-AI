"""
Voice Tools — ElevenLabs TTS + OpenAI Whisper transcription
"""
import os
import re
import io
import asyncio
import logging
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ElevenLabs for TTS (unchanged)
client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))


async def generate_voice_note(text, filename="reply.mp3"):
    """Generates audio using ElevenLabs (High Quality)."""
    try:
        clean_text = re.sub(r'\[.*?\]', '', text)
        clean_text = re.sub(r'http\S+', '', clean_text)
        clean_text = clean_text.replace('*', '').replace('_', '').replace('#', '')
        if len(clean_text) > 1000:
            clean_text = clean_text[:1000] + "... (message truncated to save voice credits)."

        audio_generator = await asyncio.to_thread(
            client.text_to_speech.convert,
            voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
            model_id="eleven_turbo_v2_5",
            text=clean_text
        )
        audio_bytes = b"".join(audio_generator)
        with open(filename, "wb") as f:
            f.write(audio_bytes)
        return filename
    except Exception as e:
        print(f"❌ ElevenLabs Error: {e}")
        return None


def cleanup_voice_file(filename="reply.mp3"):
    """Deletes the file after sending."""
    if os.path.exists(filename):
        os.remove(filename)


# ══════════════════════════════════════════════
# SPEECH-TO-TEXT (OpenAI Whisper)
# ══════════════════════════════════════════════
_whisper_client = None

def _get_whisper():
    global _whisper_client
    if _whisper_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        from openai import OpenAI
        _whisper_client = OpenAI(api_key=api_key)
    return _whisper_client


def is_openai_configured():
    """Check if OpenAI API key is available."""
    return bool(os.getenv("OPENAI_API_KEY"))


async def transcribe_audio(audio_bytes, mime_type="audio/ogg"):
    """Transcribe audio using OpenAI Whisper ($0.006/min, 99+ languages)."""
    client = _get_whisper()
    if not client:
        return None
    try:
        ext_map = {
            "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
            "audio/wav": "wav", "audio/webm": "webm", "audio/mp4": "mp4",
            "audio/m4a": "m4a", "audio/x-m4a": "m4a",
        }
        ext = ext_map.get(mime_type, "ogg")
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f"audio.{ext}"

        transcript = await asyncio.to_thread(
            client.audio.transcriptions.create,
            model="whisper-1",
            file=audio_file,
        )
        text = transcript.text.strip()
        logger.info(f"Whisper: '{text[:60]}' ({len(text)} chars)")
        return text
    except Exception as e:
        logger.error(f"Whisper error: {e}")
        return None
