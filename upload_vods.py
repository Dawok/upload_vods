import os
import json
import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import time
import sys

try:
    from config import *
except ImportError:
    print("Error: config.py not found. Please copy config.example.py to config.py and adjust the settings.")
    exit(1)

# Scopes required for managing playlists
SCOPES = ["https://www.googleapis.com/auth/youtube"]

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
        print(f"{RED}❌ Failed to send Discord notification: {str(e)}{RESET}")

def get_youtube_client():
    creds = None
    if Path(TOKEN_CACHE).exists():
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_CACHE, SCOPES)
        except Exception as e:
            # If token is invalid, expired, or revoked, delete and prompt re-auth
            error_str = str(e)
            if 'invalid_grant' in error_str or 'expired' in error_str or 'revoked' in error_str:
                os.remove(TOKEN_CACHE)
                msg = (
                    f"request.token is invalid, expired, or revoked. "
                    f"User re-authentication required."
                )
                print(f"{YELLOW}{msg}{RESET}")
                send_discord_notification(msg, error=True)
                # Continue to OAuth flow below
            else:
                send_discord_notification(f"Token file is invalid: {error_str}", error=True)
                raise
    if not creds:
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRETS, 
            SCOPES,
            redirect_uri="http://localhost:8080/oauth2callback"
        )
        # Use a custom port and print the URL
        auth_url, _ = flow.authorization_url(prompt='consent')
        print(f"\n{YELLOW}Please visit this URL to authorize the application:{RESET}")
        print(f"{CYAN}{auth_url}{RESET}")
        print(f"\n{YELLOW}Enter the authorization code: {RESET}", end='')
        code = input()
        flow.fetch_token(code=code)
        creds = flow.credentials
        with open(TOKEN_CACHE, "w") as token:
            token.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)

def load_json_file(path):
    if Path(path).exists():
        try:
            with open(path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"{YELLOW}⚠️  Invalid JSON in {path}, creating new file{RESET}")
            return []
    return []

def save_json_file(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"{RED}❌ Failed to save {path}: {str(e)}{RESET}")

def find_vods():
    vods = []
    for user_dir in BASE_DIR.iterdir():
        if user_dir.is_dir():
            for session_dir in user_dir.iterdir():
                if session_dir.is_dir():
                    info_files = list(session_dir.glob("*-info.json"))
                    for info_file in info_files:
                        vod_id = extract_vod_id(info_file.name)
                        video_file = info_file.with_name(info_file.name.replace("-info.json", "-video.mp4"))
                        if video_file.exists():
                            try:
                                with open(info_file, "r") as f:
                                    info = json.load(f)
                            except Exception:
                                info = {}
                            vods.append({
                                "info_path": info_file,
                                "video_path": video_file,
                                "vod_id": vod_id,
                                "user_name": user_dir.name,
                                "started_at": info.get("started_at", ""),
                                "info": info
                            })
    return vods

def extract_vod_id(name):
    if "[" in name and "]" in name:
        return name.split("[")[-1].split("]")[0]
    return None

def get_title_from_filename(filename):
    # Extract the title part from the filename (between the date and the VOD ID)
    parts = filename.split(" [")
    if len(parts) > 1:
        title = parts[0].split(" ", 1)[1]  # Remove the date prefix
        return title.replace("_", " ").replace("⭐", "").strip()
    return None

def clean_title(title, max_length=100):
    # Remove any invalid characters and trim to max length
    title = title.replace("_", " ").replace("⭐", "").strip()
    if len(title) > max_length:
        # Try to cut at a word boundary
        title = title[:max_length].rsplit(" ", 1)[0]
    return title

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
        error_msg = f"Failed to create playlist for {user_name}: {str(e)}"
        print(f"{RED}❌ {error_msg}{RESET}")
        send_discord_notification(error_msg, error=True)
        return None

def handle_quota_exceeded():
    """Handle YouTube API quota exceeded error by pausing the script for the configured number of hours."""
    error_msg = f"YouTube API quota exceeded. Pausing script for {QUOTA_WAIT_HOURS} hours."
    print(f"{RED}❌ {error_msg}{RESET}")
    send_discord_notification(error_msg, error=True)
    
    # Calculate time until after the wait period
    now = datetime.now()
    resume_time = now + timedelta(hours=QUOTA_WAIT_HOURS)
    
    wait_seconds = (resume_time - now).total_seconds()
    
    print(f"{YELLOW}⏳ Script will resume at: {resume_time.strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    time.sleep(wait_seconds)
    sys.exit(0)

def upload_video(vod, uploaded_ids):
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

        if "quotaExceeded" in result.stderr:
            handle_quota_exceeded()
            return False

        if result.returncode != 0:
            # If metadata upload fails, try with just the filename
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

                if "quotaExceeded" in result.stderr:
                    handle_quota_exceeded()
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
        error_msg = f"Error during upload of {vod['vod_id']}: {str(e)}"
        print(f"{RED}❌ {error_msg}{RESET}")
        send_discord_notification(error_msg, error=True)
        return False
    finally:
        if os.path.exists(meta_path):
            os.remove(meta_path)

def main():
    try:
        uploaded_ids = load_json_file(UPLOADED_IDS_FILE)
        if not isinstance(uploaded_ids, list):
            uploaded_ids = []

        print(f"{CYAN}📂 Scanning for VODs in {BASE_DIR}...{RESET}")
        vods = find_vods()
        
        # Sort vods by user and date
        vods.sort(key=lambda x: (x["user_name"], x["started_at"]))
        
        print(f"{CYAN}🔎 Found {len(vods)} total VODs to consider{RESET}")

        uploads_done = 0
        current_user = None
        
        for vod in vods:
            if vod["vod_id"] in uploaded_ids:
                print(f"{YELLOW}⏭️  Skipping (already uploaded): {vod['vod_id']}{RESET}")
                continue
                
            # If we've hit the upload limit and it's a new user, break
            if uploads_done >= MAX_UPLOADS and vod["user_name"] != current_user:
                break
                
            success = upload_video(vod, uploaded_ids)
            if success:
                uploads_done += 1
                current_user = vod["user_name"]

        print(f"\n{CYAN}📈 Uploads complete: {uploads_done}/{MAX_UPLOADS} videos uploaded this run{RESET}")
        send_discord_notification(f"Upload session complete: {uploads_done}/{MAX_UPLOADS} videos uploaded")
    except Exception as e:
        error_msg = f"Script failed: {str(e)}"
        print(f"{RED}❌ {error_msg}{RESET}")
        send_discord_notification(error_msg, error=True)

if __name__ == "__main__":
    main()
