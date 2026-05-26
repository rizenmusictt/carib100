import os
import json
import urllib.request
import urllib.parse
import re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

# 1. System Auth & Setup
API_KEY = os.environ.get("YOUTUBE_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

if not API_KEY or not GOOGLE_CREDS_JSON or not SPREADSHEET_ID:
    print("Error: Missing required YOUTUBE_API_KEY, GOOGLE_CREDENTIALS, or SPREADSHEET_ID environment variables.")
    exit(1)

today = datetime.utcnow()
four_months_ago = today - timedelta(days=120)
published_after = four_months_ago.strftime('%Y-%m-%dT%H:%M:%SZ')
current_year = today.year

genres = ["soca", "dancehall", "bouyon", "afrobeats"]
history = {}
is_first_run = True

def get_duration_seconds(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return (hours * 3600) + (minutes * 60) + seconds

# 2. Connect to Google Sheets by Key ID
try:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID)
    print(f"Connected successfully to Google Sheet ID: {SPREADSHEET_ID}")
except Exception as e:
    print(f"Google Sheets connection failed: {e}")
    print("\n[!] --- DIAGNOSTIC TRACEBACK START ---")
    import traceback
    traceback.print_exc()
    print("[!] --- DIAGNOSTIC TRACEBACK END ---\n")
    exit(1)

# 3. Load View History for Velocity Calculation
try:
    if os.path.exists("data.json"):
        with open("data.json", "r", encoding="utf-8") as f:
            old_data = json.load(f)
            if "charts" in old_data:
                for existing_genre in old_data["charts"]:
                    for track in old_data["charts"][existing_genre]:
                        history[track["id"]] = track["lifetime_views"]
        is_first_run = False
except Exception:
    print("Running as Week 1 Baseline (No local data.json history found).")

final_charts = {}
all_tracks_master = []
master_track_fingerprints = set()

# 4. Processing Loops
for genre in genres:
    print(f"Processing YouTube data for {genre.upper()}...")
    genre_tracks = []
    video_ids = []
    video_snippets = {}
    genre_claimed_ids = set()
    
    final_charts[genre] = []
    search_query = f"{genre} {current_year} -mix -mixtape -compilation -dj"
    next_page_token = None
    
    for page in range(4):
        search_query_params = {
            "part": "snippet", "q": search_query, "type": "video",
            "order": "viewCount", "publishedAfter": published_after,
            "maxResults": 50, "key": API_KEY
        }
        if next_page_token:
            search_query_params["pageToken"] = next_page_token
            
        search_params = urllib.parse.urlencode(search_query_params)
        search_url = f"https://www.googleapis.com/youtube/v3/search?{search_params}"
        
        try:
            with urllib.request.urlopen(search_url) as response:
                search_data = json.loads(response.read().decode())
                
                for item in search_data.get("items", []):
                    vid = item["id"]["videoId"]
                    video_ids.append(vid)
                    video_snippets[vid] = {
                        "id": vid,
                        "title": item["snippet"]["title"],
                        "channel": item["snippet"]["channelTitle"],
                        "genre": genre,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "thumbnail": item["snippet"]["thumbnails"]["high"]["url"]
                    }
                
                next_page_token = search_data.get("nextPageToken")
                if not next_page_token:
                    break
        except Exception as e:
            print(f"Search error during {genre.upper()}: {e}")
            break

    if not video_ids:
        continue

    chunk_size = 50
    chunks = [video_ids[i:i + chunk_size] for i in range(0, len(video_ids), chunk_size)]

    for chunk in chunks:
        stats_params = urllib.parse.urlencode({
            "part": "statistics,contentDetails", "id": ",".join(chunk), "key": API_KEY
        })
        stats_url = f"https://www.googleapis.com/youtube/v3/videos?{stats_params}"
        
        try:
            with urllib.request.urlopen(stats_url) as stats_response:
                stats_data = json.loads(stats_response.read().decode())
                
                for item in stats_data.get("items", []):
                    vid = item["id"]
                    track = video_snippets[vid]
                    title_lower = track["title"].lower()
                    channel_lower = track["channel"].lower()
                    
                    if vid in genre_claimed_ids:
                        continue

                    if any(bad_word in title_lower for bad_word in ["instrumental", "version", "edit"]):
                        continue
                        
                    if "chutney" in title_lower or "chutney" in channel_lower:
                        continue
                    if "reggae" in title_lower or "reggae" in channel_lower:
                        continue

                    if genre == "soca" and "dancehall" in title_lower and "soca" not in title_lower:
                        continue
                    if genre == "afrobeats" and "dancehall" in title_lower and "afrobeats" not in title_lower:
                        continue
                    
                    duration_raw = item["contentDetails"].get("duration", "")
                    duration_seconds = get_duration_seconds(duration_raw)
                    if duration_seconds < 90 or duration_seconds > 300:
                        continue
                        
                    current_views = int(item["statistics"].get("viewCount", 0))
                    track["lifetime_views"] = current_views
                    
                    if is_first_run:
                        weekly_views = current_views
                    else:
                        weekly_views = max(0, current_views - history.get(vid, 0))
                        
                    track["weekly_views"] = weekly_views
                    genre_tracks.append(track)
                    genre_claimed_ids.add(vid)
                    
        except Exception as e:
            print(f"Stats chunk processing error: {e}")
                    
    genre_tracks.sort(key=lambda x: x["weekly_views"], reverse=True)
    top_50_genre = genre_tracks[:50]
    final_charts[genre] = top_50_genre

    try:
        worksheet = sheet.worksheet(genre)
        worksheet.batch_clear(["A2:G60"])
        
        sheet_rows = []
        for index, track in enumerate(top_50_genre):
            sheet_rows.append(
