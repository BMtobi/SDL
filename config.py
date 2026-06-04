import os

CONFIG_FILE = "channels.json"
SETTINGS_FILE = "settings.json"
CREDENTIALS_FILE = "credentials.yaml"
CACHE_FILE = "withny-master-token.json"
HISTORY_FILE = "history.json"

BASE_DIR = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")

DEFAULT_SETTINGS = {
    "download_dir": BASE_DIR,
    "prevent_sleep_mode": "下載時不休眠",
    "rplay_token": "",
    "rplay_username": "",
    "rplay_password": "",
    "withny_token": "",
    "discord_webhook": "",
    "cookies_file": "cookies.txt",
    "fc2_cookies_file": "fc2_cookies.txt",
    "yt_quality": "best",
    "yt_format": "mp4",
    "yt_keywords": ["歌枠", "歌回", "Singing", "KARAOKE"],
    "rplay_quality": "best",
    "rplay_format": "mp4",
    "withny_remux": True,
    "withny_remux_format": "mp4",
    "withny_concat": False,
    "withny_keep_intermediates": False,
    "withny_wait_poll_interval": "20s",
    "withny_polling_pacing": "1500ms",
    "fc2_quality": "3Mbps",
    "fc2_format": "mp4",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "max_concurrent_downloads": 2,
    "rplay_version": "2025.02.26 (預估)"
}
