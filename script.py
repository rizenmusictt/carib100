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
    print("Error: Missing required environment variables.")
    exit(1)

today = datetime.utcnow()
four_months_ago = today - timedelta(days=120)
published_after = four_months_ago.strftime('%Y-%m-%dT%H:%M:%SZ')
current_year = today.year

genres = ["soca", "dancehall", "bouyon", "afrobeats"]
history = {}
is_first_run = True

# 2. Optimized Search Matrix
GENRE_QUERIES = {
    "soca": f'"soca {current_year}" OR "{current_year} soca"',
    "dancehall": f'"dancehall {current_year}" OR "{current_year} dancehall" OR "dancehall Shenseea" OR "dancehall Skeng" OR "dancehall Ayetian" OR "dancehall Valiant" OR "dancehall Skillibeng" OR "dancehall Vybz Kartel" OR "dancehall Mavado" OR "dancehall Masicka" OR "dancehall Popcaan" OR "dancehall Teejay"',
    "bouyon": f'"bouyon {current_year}" OR "{current_year} bouyon"',
    "afrobeats": f'"afrobeats {current_year}" OR "{current_year} afrobeats" OR "afrobeats Burna Boy" OR "afrobeats Wizkid" OR "afrobeats Davido" OR "afrobeats Rema" OR "afrobeats Asake" OR "afrobeats Tems" OR "afrobeats Omah Lay" OR "afrobeats Ayra Starr" OR "afrobeats Seyi Vibez" OR "afrobeats Kizz Daniel"'
}

# Filters & Blacklists
INSTRUMENTAL_BLACKLIST = ["type beat", "instrumental", "version", "edit", "riddim loop", "prod by", "prod.", "free beat", "beat lyric", "karaoke", "clean loop"]
CHUTNEY_BLACKLIST = ["chutney", "ravi b", "karma", "raymond ramnarine", "dil-e-nadan", "ki & the band", "ki and the band", "omardath", "reshma ramlal", "gundilal", "boodram", "drupatee"]
GLOBAL_CLUTTER_BLACKLIST = ["the voice blind audition", "the voice battle", "full movie", "movie clip", "trailer", "season finale"]

def get_duration_seconds(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match: return 0
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return (hours * 3600) + (minutes * 60) + seconds

# 3. Connection Setup
try:
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID)
except Exception as e:
    print(f"Connection error: {e}")
    exit(1)

# 4. History Loading
if os.path.exists("data.json"):
    with open("data.json", "r", encoding="utf-8") as f:
        old_data = json.load(f)
        if "charts" in old_data:
            for genre_key in old_data["charts"]:
                for track in old_data["charts"][genre_key]:
                    history[track["id"]] = track["lifetime_views"]
            is_first_run = False

final_charts = {}
all_tracks_master = []
master_track_fingerprints = set()

# 5. Data Gathering
for genre in genres:
    genre_tracks = []
    video_ids = []
    video_snippets = {}
    genre_claimed_ids = set()
    
    base_query = GENRE_QUERIES.get(genre)
    search_query = f"{base_query} -mix -mixtape -compilation -dj"
    next_page_token = None
    
    # Perform Search
    for page in range(4):
        params = urllib.parse.urlencode({
            "part": "snippet", "q": search_query, "type": "video", "order": "viewCount", 
            "publishedAfter": published_after, "maxResults": 50, "key": API_KEY, "pageToken": next_page_token or ""
        })
        with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{params}") as response:
            search_data = json.loads(response.read().decode())
            for item in search_data.get("items", []):
                vid = item["id"]["videoId"]
                video_ids.append(vid)
                video_snippets[vid] = {"id": vid, "title": item["snippet"]["title"], "channel": item["snippet"]["channelTitle"], "genre": genre, "url": f"https://www.youtube.com/watch?v={vid}", "thumbnail": item["snippet"]["thumbnails"]["high"]["url"]}
            next_page_token = search_data.get("nextPageToken")
            if not next_page_token: break

    # Process Stats
    chunk_size = 50
    for i in range(0, len(video_ids), chunk_size):
        chunk = video_ids[i:i+chunk_size]
        stats_params = urllib.parse.urlencode({"part": "statistics,contentDetails", "id": ",".join(chunk), "key": API_KEY})
        with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?{stats_params}") as resp:
            stats_data = json.loads(resp.read().decode())
            for item in stats_data.get("items", []):
                vid = item["id"]
                track = video_snippets[vid]
                t_lower = track["title"].lower()
                c_lower = track["channel"].lower()
                
                if any(c in t_lower for c in GLOBAL_CLUTTER_BLACKLIST): continue
                if genre != "bouyon":
                    if any(b in t_lower or b in c_lower for b in INSTRUMENTAL_BLACKLIST): continue
                elif "type beat" in t_lower or "free beat" in t_lower: continue
                if "reggae" in t_lower or any(cb in t_lower or cb in c_lower for cb in CHUTNEY_BLACKLIST): continue
                
                dur = get_duration_seconds(item["contentDetails"].get("duration", ""))
                if dur < 90 or dur > 300: continue
                
                curr_views = int(item["statistics"].get("viewCount", 0))
                track["weekly_views"] = curr_views if is_first_run else max(0, curr_views - history.get(vid, 0))
                genre_tracks.append(track)

    # 6. Update Sheets
    genre_tracks.sort(key=lambda x: x["weekly_views"], reverse=True)
    top_50 = genre_tracks[:50]
    final_charts[genre] = top_50
    
    try:
        worksheet = sheet.worksheet(genre)
        worksheet.batch_clear(["A2:G60"])
        rows = [[i+1, t["title"], t["channel"], t["weekly_views"], t["id"], t["url"], t["thumbnail"]] for i, t in enumerate(top_50)]
        if rows: worksheet.update("A2", rows)
    except: pass

# Finalize
with open("data.json", "w", encoding="utf-8") as f:
    json.dump({"last_updated": today.strftime('%Y-%m-%d'), "charts": final_charts}, f, indent=4)
