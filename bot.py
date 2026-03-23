import os
import asyncio
import logging
import tempfile
import json
import glob
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from groq import Groq
import yt_dlp
from pydub import AudioSegment

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

user_data = {}
USER_DATA_FILE = "user_data.json"

def load_user_data():
    global user_data
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r") as f:
            user_data = json.load(f)

def save_user_data():
    with open(USER_DATA_FILE, "w") as f:
        json.dump(user_data, f)

def get_user(user_id: int) -> dict:
    uid = str(user_id)
    if uid not in user_data:
        user_data[uid] = {"groq_key": "", "taglish": False}
    return user_data[uid]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f44b *OneShot Transcriber*\n\n"
        "Send me:\n"
        "\u2022 An audio file (MP3, M4A, WAV, OGG)\n"
        "\u2022 A video file (MP4, MKV, MOV)\n"
        "\u2022 A YouTube link\n"
        "\u2022 A Google Drive link (for large files)\n\n"
        "I'll transcribe it and give you a full transcript + structured summary.\n\n"
        "*Setup:*\n"
        "1. Get a free Groq API key at console.groq.com\n"
        "2. Send: `/setkey YOUR_GROQ_KEY`\n\n"
        "*Commands:*\n"
        "`/setkey KEY` \u2014 set your Groq API key\n"
        "`/language` \u2014 toggle English / Taglish mode\n"
        "`/status` \u2014 check your current settings\n\n"
        "*File too large?*\n"
        "Telegram limits uploads to 20MB. Upload to Google Drive, "
        "set sharing to 'Anyone with the link', and paste the link here.",
        parse_mode="Markdown",
    )

async def set_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Usage: `/setkey YOUR_GROQ_KEY`\n\nGet your free key at console.groq.com",
            parse_mode="Markdown",
        )
        return
    key = context.args[0].strip()
    user = get_user(user_id)
    user["groq_key"] = key
    save_user_data()
    await update.message.reply_text("\u2705 Groq API key saved.")

async def toggle_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    user["taglish"] = not user.get("taglish", False)
    save_user_data()
    mode = "\U0001f1f5\U0001f1ed Filipino/Taglish" if user["taglish"] else "\U0001f1fa\U0001f1f8 English"
    await update.message.reply_text(f"Language mode set to: {mode}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    has_key = "\u2705 Set" if user.get("groq_key") else "\u274c Not set"
    lang = "\U0001f1f5\U0001f1ed Filipino/Taglish" if user.get("taglish") else "\U0001f1fa\U0001f1f8 English"
    await update.message.reply_text(
        f"*Your settings:*\nGroq API Key: {has_key}\nLanguage: {lang}",
        parse_mode="Markdown",
    )

def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

def is_google_drive_url(text: str) -> bool:
    return "drive.google.com" in text or "docs.google.com" in text or "drive.usercontent.google.com" in text

def extract_gdrive_file_id(url: str) -> str:
    import re
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"id=([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    return None

def download_gdrive_file(url: str, tmp_dir: str) -> str:
    import gdown
    file_id = extract_gdrive_file_id(url)
    if not file_id:
        raise Exception("Could not extract Google Drive file ID from URL")
    output_path = os.path.join(tmp_dir, "gdrive_audio")
    gdown.download(id=file_id, output=output_path, quiet=True, fuzzy=True)
    files = glob.glob(output_path + "*")
    if not files:
        raise Exception("Google Drive download failed. Make sure the file is shared as 'Anyone with the link'")
    return files[0]

def process_youtube(url: str, tmp_dir: str):
    sub_opts = {
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": ["en", "tl", "fil"],
        "subtitlesformat": "vtt",
        "skip_download": True,
        "outtmpl": os.path.join(tmp_dir, "subs_%(id)s.%(ext)s"),
        "quiet": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    try:
        with yt_dlp.YoutubeDL(sub_opts) as ydl:
            ydl.download([url])
        for f in Path(tmp_dir).glob("subs_*.vtt"):
            text = parse_vtt(f.read_text(encoding="utf-8"))
            if len(text.strip()) > 50:
                return ("subtitle_text", text)
        for f in Path(tmp_dir).glob("subs_*.srt"):
            text = parse_srt(f.read_text(encoding="utf-8"))
            if len(text.strip()) > 50:
                return ("subtitle_text", text)
    except Exception:
        pass
    audio_opts = {
        "format": "bestaudio/best",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "32"}],
        "outtmpl": os.path.join(tmp_dir, "audio_%(id)s.%(ext)s"),
        "quiet": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    with yt_dlp.YoutubeDL(audio_opts) as ydl:
        ydl.download([url])
    for f in Path(tmp_dir).glob("audio_*.mp3"):
        return ("audio_file", str(f))
    for f in Path(tmp_dir).glob("audio_*"):
        return ("audio_file", str(f))
    raise Exception("Could not download audio from URL")

def parse_vtt(raw: str) -> str:
    import re
    lines = raw.split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"\d{2}:\d{2}:\d{2}", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"[\u266a\u266b]+", "", line).strip()
        if line:
            result.append(line)
    deduped = []
    for l in result:
        if not deduped or deduped[-1] != l:
            deduped.append(l)
    return " ".join(deduped)

def parse_srt(raw: str) -> str:
    import re
    lines = raw.split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if not line or re.match(r"^\d+$", line):
            continue
        if re.match(r"\d{2}:\d{2}:\d{2}", line):
            continue
        result.append(line)
    return " ".join(result)

def chunk_audio(file_path: str, tmp_dir: str, chunk_ms: int = 540000) -> list:
    audio = AudioSegment.from_file(file_path)
    if len(audio) <= chunk_ms:
        return [file_path]
    chunks = []
    overlap_ms = 5000
    start = 0
    idx = 0
    while start < len(audio):
        end = min(start + chunk_ms, len(audio))
        chunk = audio[start:end]
        chunk_path = os.path.join(tmp_dir, f"chunk_{idx:03d}.mp3")
        chunk.export(chunk_path, format="mp3", bitrate="32k", parameters=["-ar", "16000", "-ac", "1"])
        chunks.append(chunk_path)
        start += chunk_ms - overlap_ms
        idx += 1
        if idx >= 50:
            break
    return chunks

def transcribe_file(file_path: str, groq_key: str, taglish: bool) -> str:
    client = Groq(api_key=groq_key)
    kwargs = {"model": "whisper-large-v3", "response_format": "text"}
    if taglish:
        kwargs["language"] = "tl"
        kwargs["prompt"] = "This audio may contain Filipino, Tagalog, English, or Taglish mixed language."
    else:
        kwargs["prompt"] = "Transcribe accurately."
    with open(file_path, "rb") as f:
        result = client.audio.transcriptions.create(file=f, **kwargs)
    return result if isinstance(result, str) else result.text

def stitch_chunks(parts: list) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    result = parts[0]
    for i in range(1, len(parts)):
        result = result + " " + parts[i].strip()
    return result.strip()

def generate_summary(transcript: str, groq_key: str, taglish: bool) -> str:
    client = Groq(api_key=groq_key)
    words = transcript.split()
    if len(words) > 6000:
        transcript = " ".join(words[:6000]) + "\n\n[Transcript truncated for summary]"
    lang_note = "\nThe transcript may contain Filipino, Tagalog, English, or Taglish. Write summary in English." if taglish else ""
    system_prompt = (
        "You are a precise transcript summarizer. Produce a structured numbered summary." + lang_note + "\n\n"
        "FORMAT:\n1. Section Title\n1.1 Key point.\n1.2 Another point.\n\n2. Next Section\n2.1 Key point.\n\nStart directly with '1.' no intro text."
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Summarize this transcript:\n\n{transcript}"},
        ],
        max_tokens=2000,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()

def convert_to_mp3(input_path: str, tmp_dir: str) -> str:
    output_path = os.path.join(tmp_dir, "converted.mp3")
    audio = AudioSegment.from_file(input_path)
    audio.export(output_path, format="mp3", bitrate="32k", parameters=["-ar", "16000", "-ac", "1"])
    return output_path

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not is_url(text):
        await update.message.reply_text("Send me a link (YouTube, Google Drive, or any audio/video URL), an audio file, or a video file.")
        return
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user.get("groq_key"):
        await update.message.reply_text(
            "\u26a0\ufe0f No Groq API key set.\n\nGet your free key at console.groq.com\nThen send: `/setkey YOUR_KEY`",
            parse_mode="Markdown",
        )
        return
    msg = await update.message.reply_text("\u23f3 Processing link...")
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            if is_google_drive_url(text):
                await context.bot.edit_message_text("\u2B07\uFE0F Downloading from Google Drive...", chat_id=update.effective_chat.id, message_id=msg.message_id)
                audio_path = await asyncio.get_event_loop().run_in_executor(None, lambda: download_gdrive_file(text, tmp_dir))
                await context.bot.edit_message_text("\U0001f3b5 File downloaded \u2014 transcribing...", chat_id=update.effective_chat.id, message_id=msg.message_id)
                transcript = await transcribe_audio_file(audio_path, tmp_dir, user, update, context, msg)
            else:
                await context.bot.edit_message_text("\U0001f50d Checking for subtitles...", chat_id=update.effective_chat.id, message_id=msg.message_id)
                result_type, result_data = await asyncio.get_event_loop().run_in_executor(None, lambda: process_youtube(text, tmp_dir))
                if result_type == "subtitle_text":
                    transcript = result_data
                    await context.bot.edit_message_text("\u2705 Subtitles found \u2014 generating summary...", chat_id=update.effective_chat.id, message_id=msg.message_id)
                else:
                    await context.bot.edit_message_text("\U0001f3b5 Audio downloaded \u2014 transcribing...", chat_id=update.effective_chat.id, message_id=msg.message_id)
                    transcript = await transcribe_audio_file(result_data, tmp_dir, user, update, context, msg)
            await context.bot.edit_message_text("\u270d\ufe0f Generating summary...", chat_id=update.effective_chat.id, message_id=msg.message_id)
            summary = await asyncio.get_event_loop().run_in_executor(None, lambda: generate_summary(transcript, user["groq_key"], user.get("taglish", False)))
            await send_results(update, context, msg, transcript, summary)
    except Exception as e:
        logger.error(f"Error processing link: {e}")
        await context.bot.edit_message_text(f"\u274c Error: {str(e)[:200]}", chat_id=update.effective_chat.id, message_id=msg.message_id)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user.get("groq_key"):
        await update.message.reply_text(
            "\u26a0\ufe0f No Groq API key set.\n\nGet your free key at console.groq.com\nThen send: `/setkey YOUR_KEY`",
            parse_mode="Markdown",
        )
        return
    msg = await update.message.reply_text("\u23f3 Downloading file...")
    try:
        if update.message.audio:
            tg_file = update.message.audio
        elif update.message.video:
            tg_file = update.message.video
        elif update.message.document:
            tg_file = update.message.document
        elif update.message.voice:
            tg_file = update.message.voice
        else:
            await update.message.reply_text("Unsupported file type.")
            return
        with tempfile.TemporaryDirectory() as tmp_dir:
            file = await context.bot.get_file(tg_file.file_id)
            file_name = getattr(tg_file, "file_name", "audio.mp3") or "audio.mp3"
            file_path = os.path.join(tmp_dir, file_name)
            await file.download_to_drive(file_path)
            await context.bot.edit_message_text("\U0001f3b5 File received \u2014 transcribing...", chat_id=update.effective_chat.id, message_id=msg.message_id)
            transcript = await transcribe_audio_file(file_path, tmp_dir, user, update, context, msg)
            await context.bot.edit_message_text("\u270d\ufe0f Generating summary...", chat_id=update.effective_chat.id, message_id=msg.message_id)
            summary = await asyncio.get_event_loop().run_in_executor(None, lambda: generate_summary(transcript, user["groq_key"], user.get("taglish", False)))
            await send_results(update, context, msg, transcript, summary)
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        err = str(e)
        if "too big" in err.lower() or "large" in err.lower() or "413" in err:
            await context.bot.edit_message_text(
                "\u26a0\ufe0f File too large \u2014 Telegram limits bot uploads to 20MB.\n\nInstead:\n1. Upload your file to Google Drive\n2. Set sharing to 'Anyone with the link'\n3. Paste the link here",
                chat_id=update.effective_chat.id, message_id=msg.message_id
            )
        else:
            await context.bot.edit_message_text(f"\u274c Error: {err[:200]}", chat_id=update.effective_chat.id, message_id=msg.message_id)

async def transcribe_audio_file(file_path, tmp_dir, user, update, context, msg):
    converted = await asyncio.get_event_loop().run_in_executor(None, lambda: convert_to_mp3(file_path, tmp_dir))
    chunks = await asyncio.get_event_loop().run_in_executor(None, lambda: chunk_audio(converted, tmp_dir))
    total = len(chunks)
    parts = []
    for i, chunk_path in enumerate(chunks):
        if total > 1:
            await context.bot.edit_message_text(f"\U0001f399 Transcribing chunk {i+1}/{total}...", chat_id=update.effective_chat.id, message_id=msg.message_id)
        text = await asyncio.get_event_loop().run_in_executor(None, lambda cp=chunk_path: transcribe_file(cp, user["groq_key"], user.get("taglish", False)))
        parts.append(text)
    return stitch_chunks(parts)

async def send_results(update, context, msg, transcript, summary):
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
    summary_text = "\U0001f4cb SUMMARY\n\n" + summary
    if len(summary_text) > 4000:
        summary_text = summary_text[:4000] + "..."
    await update.message.reply_text(summary_text)
    transcript_preview = "\U0001f4dd TRANSCRIPT\n\n" + transcript
    if len(transcript_preview) > 4000:
        transcript_preview = transcript_preview[:4000] + "\n\n[Truncated - see transcript.txt for full]"
    await update.message.reply_text(transcript_preview)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("ONESHOT TRANSCRIBER - SUMMARY\n" + "=" * 50 + "\n\n" + summary)
        summary_path = f.name
    await update.message.reply_document(document=open(summary_path, "rb"), filename="summary.txt", caption="\U0001f4cb Summary")
    os.unlink(summary_path)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("ONESHOT TRANSCRIBER - FULL TRANSCRIPT\n" + "=" * 50 + "\n\n" + transcript)
        transcript_path = f.name
    await update.message.reply_document(document=open(transcript_path, "rb"), filename="transcript.txt", caption="\U0001f4c4 Full transcript")
    os.unlink(transcript_path)

def main():
    load_user_data()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN environment variable not set")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setkey", set_key))
    app.add_handler(CommandHandler("language", toggle_language))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VIDEO | filters.Document.ALL | filters.VOICE, handle_file))
    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
