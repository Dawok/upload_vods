import os
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

import google.auth
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# Paths and constants
VIDEO_ROOT = Path("/mnt/storage/ganymede/videos")
UPLOAD_LIMIT = 6
UPLOAD_LOG = Path("vod_upload_log.json")
UPLOAD_LOG.touch(exist_ok=True)

# youtubeuploader config
CLIENT_SECRETS = "client_secrets.json"
TOKEN_CACHE = "request.token"
YOUTUBEUPLOADER = "youtubeuploader"
DEFAULT_PRIVACY = "unlisted"

# Load previously uploaded VOD IDs
def load_recent_uploads():
    try:
        with open(UPLOAD_LOG, "r") as f:
            return json.load(f)
    except Exception:
        return []

# Save updated upload log
def log_upload(vod_id, video_path):
    logs = load_recent_uploads()
    logs.append({
        "timestamp": time.time(),
        "vod_id": vod_id,
        "video": str(video_path)
    })
    with open(UPLOAD_LOG, "w") as f:
        json.dump(logs, f, indent=2)

# Extract VOD ID from filename or JSON
def extract_vod_id(path):
    name = path.stem
    if "[" in name and "]" in name:
        return name.split("[")[-1].split("]")[0]
    return None

# Initialize YouTube API client
def get_youtube_client():
    creds = Credentials.from_authorized_user_file(TOKEN_CACHE)
    return build("youtube", "v3", credentials=creds)

# Create playlist if it doesn't exist
def ensure_playlist_exists(youtube, playlist_name):
    request = youtube.playlists().list(
        part="snippet",
        mine=True,
        maxResults=50
    )
    response = request.execute()
    for item in response.get("items", []):
        if item["snippet"]["title"] == playlist_name:
            return item["id"]

    # Playlist not found, create it
    create_request = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": playlist_name,
                "description": f"VOD archive for {playlist_name}",
            },
            "status": {
                "privacyStatus": "unlisted"
            }
        }
    )
    response = create_request.execute()
    return response["id"]

# Build upload metadata
def build_metadata(info, playlist_id):
    # Convert ISO date to YYMMDD
    date = info.get("started_at", "")
    yymmdd = ""
    if date:
        dt = datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")
        yymmdd = dt.strftime("%y%m%d")

    full_title = f"{yymmdd} {info['title']}".strip()

    return {
        "title": full_title,
        "description": f'''Streamed by {info["user_name"]}
Game: {info.get("game_name", "Unknown")}
Original broadcast: {info.get("started_at", "unknown")}
VOD ID: {info.get("id", "")}
''',
        "tags": [info["user_name"], "Twitch VOD", info.get("game_name", "")],
        "language": info.get("language", "en"),
        "recordingDate": info.get("started_at", "").split("T")[0],
        "thumbnail": info["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720"),
        "playlistID": playlist_id,
        "privacy": DEFAULT_PRIVACY
    }


# Upload with youtubeuploader
def upload_video(video_path, info_path, meta):
    args = [
        YOUTUBEUPLOADER,
        "-secrets", CLIENT_SECRETS,
        "-cache", TOKEN_CACHE,
        "-filename", str(video_path),
        "-title", meta["title"],
        "-description", meta["description"],
        "-tags", ",".join(meta["tags"]),
        "-language", meta["language"],
        "-recordingDate", meta["recordingDate"],
        "-thumbnail", meta["thumbnail"],
        "-privacy", meta["privacy"],
        "-playlistID", meta["playlistID"]
    ]

    print(f"Uploading: {video_path}")
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        print("Upload succeeded.")
        return True
    else:
        print("Upload failed:\n", result.stderr.decode())
        return False

# Main logic
def main():
    uploaded_log = load_recent_uploads()
    uploaded_ids = {entry["vod_id"] for entry in uploaded_log}
    recent_uploads = [e for e in uploaded_log if time.time() - e["timestamp"] < 86400]

    if len(recent_uploads) >= UPLOAD_LIMIT:
        print("Reached daily upload limit.")
        return

    youtube = get_youtube_client()

    for user_dir in VIDEO_ROOT.iterdir():
        if not user_dir.is_dir():
            continue

        for subfolder in user_dir.iterdir():
            if not subfolder.is_dir():
                continue

            for info_path in subfolder.glob("*-info.json"):
                vod_id = extract_vod_id(info_path)
                if not vod_id or vod_id in uploaded_ids:
                    continue

                video_path = info_path.with_name(info_path.name.replace("-info.json", "-video.mp4"))
                if not video_path.exists():
                    print(f"Missing video for {info_path}")
                    continue

                with open(info_path, "r") as f:
                    info = json.load(f)

                playlist_name = f"{info['user_name']} VODs"
                playlist_id = ensure_playlist_exists(youtube, playlist_name)

                metadata = build_metadata(info, playlist_id)
                success = upload_video(video_path, info_path, metadata)

                if success:
                    log_upload(vod_id, video_path)
                    recent_uploads.append({"vod_id": vod_id, "timestamp": time.time()})
                    if len(recent_uploads) >= UPLOAD_LIMIT:
                        print("Reached upload limit.")
                        return

if __name__ == "__main__":
    main()
