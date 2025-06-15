import os
import json
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/mnt/storage/ganymede/videos")
UPLOADED_IDS_FILE = "uploaded_ids.json"
PLAYLISTS_FILE = "playlists.json"
YOUTUBEUPLOADER_BIN = "youtubeuploader"
CLIENT_SECRETS = "client_secrets.json"
TOKEN_CACHE = "request.token"
MAX_UPLOADS = 6

def load_json_file(path):
    if Path(path).exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

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
                            vods.append({
                                "info_path": info_file,
                                "video_path": video_file,
                                "vod_id": vod_id,
                                "user_name": user_dir.name
                            })
    return vods

def extract_vod_id(name):
    # Expects [1234567890] in filename
    if "[" in name and "]" in name:
        return name.split("[")[-1].split("]")[0]
    return None

def build_metadata(info, user_name):
    dt = datetime.strptime(info["started_at"], "%Y-%m-%dT%H:%M:%SZ")
    date_prefix = dt.strftime("%y%m%d")
    title = f"{date_prefix} {info['title']}"
    description = f"""Streamed by {info['user_name']}
Game: {info.get('game_name', 'Unknown')}
Original broadcast: {info.get('started_at', 'unknown')}
VOD ID: {info.get('id', '')}
"""
    tags = [info["user_name"], "Twitch VOD", info.get("game_name", "")]
    thumbnail_url = info["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720")

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "language": info.get("language", "en"),
        "recordingDate": info["started_at"].split("T")[0],
        "thumbnail": thumbnail_url,
        "privacy": "unlisted",
        "playlistID": get_or_create_playlist_id(user_name)
    }

def get_or_create_playlist_id(user_name):
    playlists = load_json_file(PLAYLISTS_FILE)
    if user_name in playlists:
        return playlists[user_name]

    # Create playlist using youtubeuploader
    playlist_title = f"{user_name} VODs"
    print(f"Creating playlist: {playlist_title}")

    meta = {
        "title": playlist_title,
        "description": f"Automatically created playlist for {user_name}",
        "privacy": "unlisted"
    }
    meta_path = f"tmp_playlist_meta_{user_name}.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    # Run youtubeuploader with dummy video to create playlist
    result = subprocess.run([
        YOUTUBEUPLOADER_BIN,
        "-secrets", CLIENT_SECRETS,
        "-cache", TOKEN_CACHE,
        "-metaJSON", meta_path,
        "-filename", "-",  # dummy
        "-quiet"
    ], capture_output=True, text=True)

    os.remove(meta_path)

    # Try to extract the playlist ID from stdout
    playlist_id = None
    for line in result.stderr.splitlines() + result.stdout.splitlines():
        if "playlist ID:" in line:
            playlist_id = line.strip().split("playlist ID:")[-1].strip()

    if playlist_id:
        playlists[user_name] = playlist_id
        save_json_file(PLAYLISTS_FILE, playlists)
        return playlist_id
    else:
        print(f"⚠️ Failed to create playlist for {user_name}")
        return None

def upload_video(vod, uploaded_ids):
    with open(vod["info_path"], "r") as f:
        info = json.load(f)

    playlist_id = get_or_create_playlist_id(vod["user_name"])
    if not playlist_id:
        print(f"⚠️ Skipping upload: playlist creation failed for {vod['user_name']}")
        return False

    metadata = build_metadata(info, vod["user_name"])
    meta_path = "tmp_video_meta.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f)

    print(f"⬆️ Uploading: {metadata['title']}")

    result = subprocess.run([
        YOUTUBEUPLOADER_BIN,
        "-secrets", CLIENT_SECRETS,
        "-cache", TOKEN_CACHE,
        "-filename", str(vod["video_path"]),
        "-metaJSON", meta_path,
        "-quiet"
    ])

    os.remove(meta_path)

    if result.returncode == 0:
        uploaded_ids.append(vod["vod_id"])
        return True
    else:
        print(f"❌ Upload failed for {vod['vod_id']}")
        return False

def main():
    uploaded_ids = load_json_file(UPLOADED_IDS_FILE)
    if not isinstance(uploaded_ids, list):
        uploaded_ids = []

    uploads_done = 0
    for vod in find_vods():
        if vod["vod_id"] in uploaded_ids:
            continue
        if uploads_done >= MAX_UPLOADS:
            break
        success = upload_video(vod, uploaded_ids)
        if success:
            uploads_done += 1

    save_json_file(UPLOADED_IDS_FILE, uploaded_ids)

if __name__ == "__main__":
    main()
