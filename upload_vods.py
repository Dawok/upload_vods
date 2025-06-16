import os
import json
import time
from datetime import datetime, timedelta
import subprocess
from pathlib import Path
import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from config import *

# ANSI colors for console output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"

# File paths
UPLOADED_IDS_FILE = "uploaded_ids.json"
PLAYLISTS_FILE = "playlists.json"
QUOTA_RESET_FILE = "quota_reset.json"

def load_json_file(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if filename == PLAYLISTS_FILE else []

def save_json_file(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def send_discord_notification(message, error=False):
    if not DISCORD_WEBHOOK_URL:
        return

    color = 0xFF0000 if error else 0x00FF00
    data = {
        "embeds": [{
            "title": "VOD Upload Status",
            "description": message,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=data)
    except Exception as e:
        print(f"{RED}Failed to send Discord notification: {str(e)}{RESET}")

def get_youtube_client():
    creds = None
    if os.path.exists(TOKEN_CACHE):
        creds = Credentials.from_authorized_user_info(json.load(open(TOKEN_CACHE)))

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, ["https://www.googleapis.com/auth/youtube"])
            creds = flow.run_local_server(port=0)
        
        with open(TOKEN_CACHE, "w") as token:
            json.dump({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes
            }, token)

    return build("youtube", "v3", credentials=creds)

def check_quota_limit():
    quota_data = load_json_file(QUOTA_RESET_FILE)
    if "reset_time" in quota_data:
        reset_time = datetime.fromisoformat(quota_data["reset_time"])
        if datetime.now() < reset_time:
            remaining_time = reset_time - datetime.now()
            hours = int(remaining_time.total_seconds() / 3600)
            minutes = int((remaining_time.total_seconds() % 3600) / 60)
            print(f"{YELLOW}⚠️  YouTube API quota exceeded. Resuming in {hours}h {minutes}m{RESET}")
            return False
    return True

def set_quota_cooldown():
    reset_time = datetime.now() + timedelta(hours=24)
    save_json_file(QUOTA_RESET_FILE, {"reset_time": reset_time.isoformat()})
    print(f"{YELLOW}⚠️  Setting 24-hour cooldown for YouTube API quota. Will resume at {reset_time.isoformat()}{RESET}")

def clean_title(title):
    # Remove invalid characters and trim to 100 characters
    invalid_chars = ['"', "'", "\\", "/", ":", "*", "?", "<", ">", "|"]
    for char in invalid_chars:
        title = title.replace(char, "")
    return title[:100]

def get_title_from_filename(filename):
    # Extract title from filename, removing date and VOD ID
    parts = filename.split(" ", 1)
    if len(parts) > 1:
        return parts[1].rsplit(" [", 1)[0]  # Remove VOD ID at the end
    return None

def build_metadata(vod):
    info = vod["info"]
    try:
        dt = datetime.strptime(info.get("started_at", ""), "%Y-%m-%dT%H:%M:%SZ")
        date_prefix = dt.strftime("%y%m%d")
    except (ValueError, TypeError):
        # Fallback to filename date if info date is invalid
        filename_date = vod["video_path"].name.split(" ")[0]
        date_prefix = filename_date.replace("-", "")[2:]  # Convert YYYY-MM-DD to YYMMDD

    # Try to get title from info, fallback to filename
    title = info.get("title", "")
    if not title:
        title = get_title_from_filename(vod["video_path"].name)
    
    if not title:
        title = f"VOD {vod['vod_id']}"  # Final fallback
    
    # Clean and format the title
    title = clean_title(f"{date_prefix} {title}")

    description = f"""Streamed by {vod['user_name']}
Game: {info.get('game_name', 'Unknown')}
Original broadcast: {info.get('started_at', 'unknown')}
VOD ID: {vod['vod_id']}
"""
    tags = [vod['user_name'], "Twitch VOD", info.get("game_name", "")]
    thumbnail_url = info.get("thumbnail_url", "").replace("{width}", "1280").replace("{height}", "720")

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "language": info.get("language", "en"),
        "recordingDate": info.get("started_at", "").split("T")[0],
        "thumbnail": thumbnail_url,
        "privacy": VIDEO_PRIVACY
    }

def get_or_create_playlist_id(user_name):
    if not check_quota_limit():
        return None

    playlists = load_json_file(PLAYLISTS_FILE)
    if user_name in playlists:
        return playlists[user_name]

    playlist_title = f"{user_name} VODs"
    print(f"{YELLOW}➕ Creating playlist: {playlist_title}{RESET}")

    try:
        youtube = get_youtube_client()
        request = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": playlist_title,
                    "description": f"Automatically created playlist for {user_name}"
                },
                "status": {
                    "privacyStatus": VIDEO_PRIVACY
                }
            }
        )
        response = request.execute()
        playlist_id = response["id"]
        playlists[user_name] = playlist_id
        save_json_file(PLAYLISTS_FILE, playlists)
        print(f"{GREEN}✅ Created playlist ID: {playlist_id}{RESET}")
        return playlist_id
    except Exception as e:
        if "quotaExceeded" in str(e):
            set_quota_cooldown()
        error_msg = f"Failed to create playlist for {user_name}: {str(e)}"
        print(f"{RED}❌ {error_msg}{RESET}")
        send_discord_notification(error_msg, error=True)
        return None

def upload_video(vod, uploaded_ids):
    if not check_quota_limit():
        return False

    try:
        with open(vod["info_path"], "r") as f:
            vod["info"] = json.load(f)
    except Exception:
        vod["info"] = {}

    playlist_id = get_or_create_playlist_id(vod["user_name"])
    if not playlist_id:
        error_msg = f"Skipping upload: no playlist available for {vod['user_name']}"
        print(f"{RED}⚠️  {error_msg}{RESET}")
        send_discord_notification(error_msg, error=True)
        return False

    metadata = build_metadata(vod)
    meta_path = "tmp_video_meta.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f)

    print(f"{CYAN}⬆️  Uploading: {metadata['title']}{RESET}")

    try:
        # First try with metadata
        result = subprocess.run([
            YOUTUBEUPLOADER_BIN,
            "-secrets", CLIENT_SECRETS,
            "-cache", TOKEN_CACHE,
            "-filename", str(vod["video_path"]),
            "-metaJSON", meta_path,
            "-playlistID", playlist_id,
            "-privacy", VIDEO_PRIVACY
        ], capture_output=True, text=True)

        # Check for quota exceeded in the output
        if "quotaExceeded" in result.stderr or "quotaExceeded" in result.stdout:
            set_quota_cooldown()
            error_msg = f"Upload failed: YouTube API quota exceeded. Will resume in 24 hours."
            print(f"{RED}❌ {error_msg}{RESET}")
            send_discord_notification(error_msg, error=True)
            return False

        if result.returncode != 0:
            # If metadata upload fails for other reasons, try with just the filename
            print(f"{YELLOW}⚠️  Metadata upload failed, trying with filename only{RESET}")
            filename_title = get_title_from_filename(vod["video_path"].name)
            if filename_title:
                filename_title = clean_title(f"{metadata['title'].split(' ', 1)[0]} {filename_title}")
                metadata["title"] = filename_title
                with open(meta_path, "w") as f:
                    json.dump(metadata, f)
                
                result = subprocess.run([
                    YOUTUBEUPLOADER_BIN,
                    "-secrets", CLIENT_SECRETS,
                    "-cache", TOKEN_CACHE,
                    "-filename", str(vod["video_path"]),
                    "-metaJSON", meta_path,
                    "-playlistID", playlist_id,
                    "-privacy", VIDEO_PRIVACY
                ], capture_output=True, text=True)

                # Check for quota exceeded in the second attempt
                if "quotaExceeded" in result.stderr or "quotaExceeded" in result.stdout:
                    set_quota_cooldown()
                    error_msg = f"Upload failed: YouTube API quota exceeded. Will resume in 24 hours."
                    print(f"{RED}❌ {error_msg}{RESET}")
                    send_discord_notification(error_msg, error=True)
                    return False

        if result.returncode == 0:
            uploaded_ids.append(vod["vod_id"])
            save_json_file(UPLOADED_IDS_FILE, uploaded_ids)  # Save after each successful upload
            print(f"{GREEN}✅ Upload complete: {vod['vod_id']}{RESET}")
            return True
        else:
            error_msg = f"Upload failed for: {vod['vod_id']}"
            print(f"{RED}❌ {error_msg}{RESET}")
            send_discord_notification(error_msg, error=True)
            return False
    except Exception as e:
        if "quotaExceeded" in str(e):
            set_quota_cooldown()
            error_msg = f"Upload failed: YouTube API quota exceeded. Will resume in 24 hours."
            print(f"{RED}❌ {error_msg}{RESET}")
            send_discord_notification(error_msg, error=True)
            return False
        error_msg = f"Error during upload of {vod['vod_id']}: {str(e)}"
        print(f"{RED}❌ {error_msg}{RESET}")
        send_discord_notification(error_msg, error=True)
        return False
    finally:
        if os.path.exists(meta_path):
            os.remove(meta_path)

def main():
    if not os.path.exists(YOUTUBEUPLOADER_BIN):
        print(f"{RED}❌ youtubeuploader binary not found at {YOUTUBEUPLOADER_BIN}{RESET}")
        return

    if not os.path.exists(CLIENT_SECRETS):
        print(f"{RED}❌ OAuth2 client secrets file not found at {CLIENT_SECRETS}{RESET}")
        return

    if not check_quota_limit():
        return

    uploaded_ids = load_json_file(UPLOADED_IDS_FILE)
    vods_to_upload = []

    # Scan for VODs
    for user_dir in Path(".").glob("*"):
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue

        user_name = user_dir.name
        for vod_dir in user_dir.glob("*"):
            if not vod_dir.is_dir():
                continue

            vod_id = vod_dir.name.split("-")[-1]
            if vod_id in uploaded_ids:
                continue

            video_path = next(vod_dir.glob("*.mp4"), None)
            info_path = next(vod_dir.glob("*.json"), None)

            if video_path and info_path:
                vods_to_upload.append({
                    "vod_id": vod_id,
                    "user_name": user_name,
                    "video_path": video_path,
                    "info_path": info_path
                })

    if not vods_to_upload:
        print(f"{GREEN}✅ No new VODs to upload{RESET}")
        return

    print(f"{CYAN}📦 Found {len(vods_to_upload)} VODs to upload{RESET}")

    # Upload VODs
    for vod in vods_to_upload:
        if not check_quota_limit():
            break
        upload_video(vod, uploaded_ids)

if __name__ == "__main__":
    main()
