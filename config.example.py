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

# Video settings
VIDEO_PRIVACY = "unlisted"  # Options: "private", "unlisted", "public"
QUOTA_WAIT_HOURS = 24  # Number of hours to wait when YouTube API quota is exceeded

# Discord webhook URL for notifications
DISCORD_WEBHOOK_URL = "your_discord_webhook_url_here"

# Upload limits
MAX_UPLOADS = 6

# ANSI colors for console output
RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m" 