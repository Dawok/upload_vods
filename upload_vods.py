import os
import json
import subprocess
from datetime import datetime
from pathlib import Path
from config import *


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

    playlist_title = f"{user_name} VODs"
    print(f"{YELLOW}➕ Creating playlist: {playlist_title}{RESET}")

    meta = {
        "title": playlist_title,
        "description": f"Automatically created playlist for {user_name}",
        "privacy": "unlisted"
    }
    meta_path = f"tmp_playlist_meta_{user_name}.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    result = subprocess.run([
        YOUTUBEUPLOADER_BIN,
        "-secrets", CLIENT_SECRETS,
        "-cache", TOKEN_CACHE,
        "-metaJSON", meta_path,
        "-filename", "-",
        "-quiet"
    ], capture_output=True, text=True)

    os.remove(meta_path)

    playlist_id = None
    for line in result.stderr.splitlines() + result.stdout.splitlines():
        if "playlist ID:" in line:
            playlist_id = line.strip().split("playlist ID:")[-1].strip()

    if playlist_id:
        playlists[user_name] = playlist_id
        save_json_file(PLAYLISTS_FILE, playlists)
        print(f"{GREEN}✅ Created playlist ID: {playlist_id}{RESET}")
        return playlist_id
    else:
        print(f"{RED}❌ Failed to create playlist for {user_name}{RESET}")
        return None

def upload_video(vod, uploaded_ids):
    with open(vod["info_path"], "r") as f:
        info = json.load(f)

    playlist_id = get_or_create_playlist_id(vod["user_name"])
    if not playlist_id:
        print(f"{RED}⚠️  Skipping upload: no playlist available for {vod['user_name']}{RESET}")
        return False

    metadata = build_metadata(info, vod["user_name"])
    meta_path = "tmp_video_meta.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f)

    print(f"{CYAN}⬆️  Uploading: {metadata['title']}{RESET}")

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
        print(f"{GREEN}✅ Upload complete: {vod['vod_id']}{RESET}")
        return True
    else:
        print(f"{RED}❌ Upload failed for: {vod['vod_id']}{RESET}")
        return False

def main():
    uploaded_ids = load_json_file(UPLOADED_IDS_FILE)
    if not isinstance(uploaded_ids, list):
        uploaded_ids = []

    print(f"{CYAN}📂 Scanning for VODs in {BASE_DIR}...{RESET}")
    vods = find_vods()
    print(f"{CYAN}🔎 Found {len(vods)} total VODs to consider{RESET}")

    uploads_done = 0
    for vod in vods:
        if vod["vod_id"] in uploaded_ids:
            print(f"{YELLOW}⏭️  Skipping (already uploaded): {vod['vod_id']}{RESET}")
            continue
        if uploads_done >= MAX_UPLOADS:
            break
        success = upload_video(vod, uploaded_ids)
        if success:
            uploads_done += 1

    save_json_file(UPLOADED_IDS_FILE, uploaded_ids)

    print(f"\n{CYAN}📈 Uploads complete: {uploads_done}/{MAX_UPLOADS} videos uploaded this run{RESET}")

if __name__ == "__main__":
    main()
