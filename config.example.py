import os
from pathlib import Path

# Base directory where VODs are stored
BASE_DIR = Path("/mnt/storage/ganymede/videos")

# File paths for tracking uploads and playlists
UPLOADED_IDS_FILE = "uploaded_ids.json"
PLAYLISTS_FILE = "playlists.json"

# YouTube uploader configuration
YOUTUBEUPLOADER_BIN = "youtubeuploader"  # Path to youtubeuploader binary
CLIENT_SECRETS = "client_secrets.json"   # Path to OAuth client secrets file
TOKEN_CACHE = "request.token"            # Path to token cache file

# Discord webhook configuration
DISCORD_WEBHOOK_URL = ""  # Add your Discord webhook URL here

# Upload limits
MAX_UPLOADS = 6
VIDEO_PRIVACY = "unlisted"  # One of: "private", "unlisted", "public"

# ANSI colors for console output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m" 