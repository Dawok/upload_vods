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
            SCOPES
        )
        print(f"\n{YELLOW}No valid OAuth token found. Please follow the instructions below to authenticate this server.{RESET}")
        creds = flow.run_console()
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
    # Try to get a valid date from started_at, created_at, or published_at
    date_str = info.get("started_at") or info.get("created_at") or info.get("published_at")
    date_prefix = None
    recording_date = None
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
            date_prefix = dt.strftime("%y%m%d")
            recording_date = dt.strftime("%Y-%m-%d")
        except Exception:
            # fallback to filename date if parsing fails
            filename_date = vod["video_path"].name.split(" ")[0]
            date_prefix = filename_date.replace("-", "")[2:]  # Convert YYYY-MM-DD to YYMMDD
    else:
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
Game: {info.get('game_name', info.get('category', 'Unknown'))}
Original broadcast: {date_str or 'unknown'}
VOD ID: {vod['vod_id']}
"""
    tags = [vod['user_name'], "Twitch VOD", info.get("game_name", info.get("category", ""))]
    thumbnail_url = info.get("thumbnail_url", "").replace("{width}", "1280").replace("{height}", "720")

    meta = {
        "title": title,
        "description": description,
        "tags": tags,
        "language": info.get("language", "en"),
        "thumbnail": thumbnail_url,
        "privacy": VIDEO_PRIVACY
    }
    if recording_date:
        meta["recordingDate"] = recording_date
    return meta

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

def wait_for_new_token():
    print(f"{YELLOW}Your OAuth token has expired or been revoked.{RESET}")
    print(f"{YELLOW}To continue, you must generate a new request.token file using the OAuth flow on another machine.{RESET}")
    print(f"{YELLOW}1. On your local machine, run the following Python code (with google-auth-oauthlib installed):{RESET}")
    print(f"""
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', ['https://www.googleapis.com/auth/youtube'])
creds = flow.run_console()
with open('request.token', 'w') as f:
    f.write(creds.to_json())
    """)
    print(f"{YELLOW}2. Upload the new request.token file to this server in the working directory.{RESET}")
    print(f"{YELLOW}Waiting for new request.token...{RESET}")
    import time
    last_mtime = None
    while True:
        if os.path.exists(TOKEN_CACHE):
            mtime = os.path.getmtime(TOKEN_CACHE)
            if last_mtime is None:
                last_mtime = mtime
            elif mtime != last_mtime:
                print(f"{GREEN}New request.token detected. Resuming...{RESET}")
                break
        time.sleep(5)

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

        # If token is expired or revoked, pause and wait for new token
        if "invalid_grant" in result.stderr or "Token has been expired or revoked" in result.stderr:
            print(f"{RED}❌ OAuth token expired or revoked. Pausing for manual intervention.{RESET}")
            send_discord_notification("OAuth token expired or revoked. Waiting for new request.token upload.", error=True)
            wait_for_new_token()
            return False

        # If metadata upload fails, try with just the filename
        if result.returncode != 0:
            print(f"{YELLOW}⚠️  Metadata upload failed, trying with filename only{RESET}")
            print(f"{RED}youtubeuploader stderr:{RESET}\n{result.stderr}")
            print(f"{RED}youtubeuploader stdout:{RESET}\n{result.stdout}")

            # If the error is due to recordingDate parse error, remove it and use filename as title
            if ("error parsing file" in result.stderr and "parsing time" in result.stderr):
                print(f"{YELLOW}⚠️  Detected recordingDate parse error, removing recordingDate and using filename as title{RESET}")
                filename_title = get_title_from_filename(vod["video_path"].name)
                if filename_title:
                    filename_title = clean_title(f"{metadata['title'].split(' ', 1)[0]} {filename_title}")
                else:
                    filename_title = metadata['title']
                # Remove recordingDate from metadata
                metadata.pop("recordingDate", None)
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
                if result.returncode == 0:
                    uploaded_ids.append(vod["vod_id"])
                    save_json_file(UPLOADED_IDS_FILE, uploaded_ids)
                    print(f"{GREEN}✅ Upload complete: {vod['vod_id']}{RESET}")
                    return True
                else:
                    print(f"{RED}youtubeuploader stderr:{RESET}\n{result.stderr}")
                    print(f"{RED}youtubeuploader stdout:{RESET}\n{result.stdout}")
                    error_msg = f"Upload failed for: {vod['vod_id']}"
                    print(f"{RED}❌ {error_msg}{RESET}")
                    send_discord_notification(error_msg, error=True)
                    return False

            # If token is expired or revoked, pause and wait for new token (fallback attempt)
            if "invalid_grant" in result.stderr or "Token has been expired or revoked" in result.stderr:
                print(f"{RED}❌ OAuth token expired or revoked. Pausing for manual intervention.{RESET}")
                send_discord_notification("OAuth token expired or revoked. Waiting for new request.token upload.", error=True)
                wait_for_new_token()
                return False

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
                # If token is expired or revoked, pause and wait for new token (fallback attempt)
                if "invalid_grant" in result.stderr or "Token has been expired or revoked" in result.stderr:
                    print(f"{RED}❌ OAuth token expired or revoked. Pausing for manual intervention.{RESET}")
                    send_discord_notification("OAuth token expired or revoked. Waiting for new request.token upload.", error=True)
                    wait_for_new_token()
                    return False

        if result.returncode == 0:
            uploaded_ids.append(vod["vod_id"])
            save_json_file(UPLOADED_IDS_FILE, uploaded_ids)  # Save after each successful upload
            print(f"{GREEN}✅ Upload complete: {vod['vod_id']}{RESET}")
            return True
        else:
            print(f"{RED}youtubeuploader stderr:{RESET}\n{result.stderr}")
            print(f"{RED}youtubeuploader stdout:{RESET}\n{result.stdout}")
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
