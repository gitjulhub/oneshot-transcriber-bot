import os
import asyncio
import logging
import tempfile
import json
import glob
from pathlib import Path

from telegram import Update, Document
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
        "2. Send: /setkey YOUR_GROQ_KEY\n\n"
        "*Commands:*\n"
        "/setkey KEY \u2014 set your Groq API key\n"
        "/language \u2014 toggle English / Taglish mode\n"
        "/status \u2014 check your current settings\n\n"
        "*File too large?*\n"
        "Telegram limits uploads to 20MB. Upload to Google Drive, set sharing to "
        "'Anyone with the link', and paste the link here.",
        parse_mode="Markdown",
    )
