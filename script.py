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

# 2. Advanced Search Matrix (Pinpoint Precision)
GENRE_QUERIES = {
    "soca": f'"soca {current_year}" OR "{current_year} soca"',
    "dancehall": f'"dancehall {current_year}" OR "{current_year} dancehall" OR "dancehall Shenseea" OR "dancehall Skeng" OR "dancehall Ayetian" OR "dancehall Valiant" OR "dancehall Skillibeng" OR "dancehall Vybz Kartel" OR "dancehall Mavado" OR "dancehall Masicka" OR "dancehall Popcaan" OR "dancehall Teejay"',
    "bouyon": f'"bouyon {current_year}" OR "{current_year} bouyon"',
    "afrobeats": f'"afrobeats {current_year}" OR "{current_year} afrobeats" OR "Burna Boy" OR "Wizkid" OR "Davido" OR "Rema" OR "Asake" OR "Tems" OR "Omah Lay" OR "Ayra Starr" OR "Seyi Vibez" OR "Kizz Daniel"'
}

# Clutter control filters
INSTRUMENTAL_BLACKLIST = [
    "type beat", "instrumental", "version", "edit", "riddim loop", 
    "prod by", "prod.", "free beat", "beat lyric", "karaoke", "clean loop"
]

CHUTNEY_BLACKLIST = [
    "chutney", "ravi b", "karma", "raymond ramnarine", "dil-e-nadan", "ki & the band", 
    "ki and the band", "omardath", "reshma ramlal", "gundilal", "boodram", "drupatee"
]

GLOBAL_CLUTTER_BLACKLIST = [
    "the voice blind audition", "the voice battle", "full movie", "movie clip", "trailer", "season finale"
]

def get_duration_seconds(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return (hours * 3600) + (minutes * 60) + seconds

# 3. Connect to Google Sheets by Key ID
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
    exit(1)

# 4. Load View History for Velocity Calculation
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
    print("Running as Week 1 Baseline.")

final_charts = {}
all_tracks_master = []
master_track_fingerprints = set()

# 5. Data Gathering Processing Loops
for genre in genres:
    print(f"Gathering metrics for {genre.upper()}...")
    genre_tracks = []
    video_ids = []
    video_snippets = {}
    genre_claimed_ids = set()
    
    final_charts[genre] = []
    
    base_query = GENRE_QUERIES.get(genre, f"{genre} {current_year}")
    search_query = f"{base_query} -mix -mixtape -compilation -dj"
    next_page_token = None
    
    for page in range(4):
        search_query_params = {
            "part": "snippet", 
            "q": search_query, 
            "type": "video",
            "order": "viewCount", 
            "publishedAfter": published_after,
            "maxResults": 50, 
            "key": API_KEY
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
            print(f"Search API exception during {genre.upper()}: {e}")
            break

    if not video_ids:
        continue

    chunk_size = 50
    chunks = [video_ids[i:i + chunk_size] for i in range(0, len(video_ids), chunk_size)]

    for chunk in chunks:
        stats_params_dict = {
            "part": "statistics,contentDetails", 
            "id": ",".join(chunk), 
            "key": API_KEY
        }
        stats_params = urllib.parse.urlencode(stats_params_dict)
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

                    # Global non-music filter
                    if any(clutter_word in title_lower for clutter_word in GLOBAL_CLUTTER_BLACKLIST):
                        continue

                    # Targeted Instrumental Filters per Genre
                    if genre in ["soca", "dancehall"]:
                        if any(bad_word in title_lower or bad_word in channel_lower for bad_word in INSTRUMENTAL_BLACKLIST):
                            continue
                    elif genre in ["bouyon", "afrobeats"]:
                        # Protects official uploads that contain producer credits, but kills fake beat spam
                        if "type beat" in title_lower or "free beat" in title_lower or "instrumental" in title_lower:
                            continue

                    if "reggae" in title_lower or "reggae" in channel_lower:
                        continue

                    if any(chutney_bot in title_lower or chutney_bot in channel_lower for chutney_bot in CHUTNEY_BLACKLIST):
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
            print(f"Stats evaluation failure: {e}")
                    
    genre_tracks.sort(key=lambda x: x["weekly_views"], reverse=True)
    top_50_genre = genre_tracks[:50]
    final_charts[genre] = top_50_genre

    # 6. Push Synchronized Tracks to Sheets Tabs
    try:
        worksheet = sheet.worksheet(genre)
        worksheet.batch_clear(["A2:G60"])
        
        sheet_rows = []
        for index, track in enumerate(top_50_genre):
            sheet_rows.append([
                index + 1,
                track["title"],
                track["channel"],
                track["weekly_views"],
                track["id"],
                track["url"],
                track["thumbnail"]
            ])
            
        if sheet_rows:
            worksheet.update("A2", sheet_rows)
            print(f"Successfully populated {len(sheet_rows)} tracks to spreadsheet tab: '{genre}'")
    except Exception as sheet_err:
        print(f"Spreadsheet sync error on tab '{genre}': {sheet_err}")

# Combine master database backup tracking
for genre_key in genres:
    for t in final_charts.get(genre_key, []):
        if t["id"] not in master_track_fingerprints:
            all_tracks_master.append(t)
            master_track_fingerprints.add(t["id"])

all_tracks_master.sort(key=lambda x: x["weekly_views"], reverse=True)
final_charts["all_genres"] = all_tracks_master[:50]

final_output = {
    "last_updated": today.strftime('%Y-%m-%d'),
    "charts": final_charts
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(final_output, f, indent=4)

print("Database completely synchronized and updated successfully!")