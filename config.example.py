import os
from pathlib import Path

# Base directory for VOD storage
BASE_DIR = "/mnt/storage/ganymede/videos"  # Base directory containing VOD folders

# File paths for tracking uploads and playlists
UPLOADED_IDS_FILE = "uploaded_ids.json"
PLAYLISTS_FILE = "playlists.json"

# YouTube API settings
YOUTUBEUPLOADER_BIN = "youtubeuploader"  # Path to youtubeuploader binary
CLIENT_SECRETS = "client_secrets.json"   # Path to OAuth2 client secrets file
TOKEN_CACHE = "request.token"            # Path to store OAuth2 token cache

# Discord webhook URL for notifications
DISCORD_WEBHOOK_URL = ""

# Video settings
VIDEO_PRIVACY = "unlisted"  # Privacy status for uploaded videos (private, unlisted, or public)

# ANSI colors for console output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m" 