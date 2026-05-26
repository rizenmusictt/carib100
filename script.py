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
    print("Error: Missing environment variables.")
    exit(1)

today = datetime.utcnow()
four_months_ago = today - timedelta(days=120)
published_after = four_months_ago.strftime('%Y-%m-%dT%H:%M:%SZ')
current_year = today.year

genres = ["soca", "dancehall", "bouyon", "afrobeats"]
history = {}
is_first_run = True

# 2. Search Matrix
GENRE_QUERIES = {
    "soca": f'"soca {current_year}" OR "{current_year} soca"',
    "dancehall": f'"dancehall {current_year}" OR "{current_year} dancehall" OR "dancehall Shenseea" OR "dancehall Skeng" OR "dancehall Ayetian" OR "dancehall Valiant" OR "dancehall Skillibeng" OR "dancehall Vybz Kartel" OR "dancehall Mavado" OR "dancehall Masicka" OR "dancehall Popcaan" OR "dancehall Teejay"',
    "bouyon": f'"bouyon {current_year}" OR "{current_year} bouyon"',
    "afrobeats": f'"afrobeats {current_year}" OR "{current_year} afrobeats" OR "afrobeats Burna Boy" OR "afrobeats Wizkid" OR "afrobeats Davido" OR "afrobeats Rema" OR "afrobeats Asake" OR "afrobeats Tems" OR "afrobeats Omah Lay" OR "afrobeats Ayra Starr" OR "afrobeats Seyi Vibez" OR "afrobeats Kizz Daniel"'
}

INSTRUMENTAL_BLACKLIST = ["type beat", "instrumental", "version", "edit", "riddim loop", "prod by", "prod.", "free beat", "beat lyric", "karaoke", "clean loop"]
CHUTNEY_BLACKLIST = ["chutney", "ravi b", "karma", "raymond ramnarine", "dil-e-nadan", "ki & the band", "ki and the band", "omardath", "reshma ramlal", "gundilal", "boodram", "drupatee"]
GLOBAL_CLUTTER_BLACKLIST = ["the voice blind audition", "the voice battle", "full movie", "movie clip", "trailer", "season finale"]

def get_duration_seconds(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match: return 0
    return (int(match.group(1) or 0) * 3600) + (int(match.group(2) or 0) * 60) + (int(match.group(3) or 0))

# 3. Connection
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID)

# 4. History
if os.path.exists("data.json"):
    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        if "charts" in data:
            for g in data["charts"]:
                for t in data["charts"][g]: history[t["id"]] = t["lifetime_views"]
            is_first_run = False

final_charts = {}

# 5. Data Gathering (with proper encoding to fix 403)
for genre in genres:
    genre_tracks = []
    video_ids = []
    video_snippets = {}
    
    base_query = GENRE_QUERIES.get(genre)
    search_query = f"{base_query} -mix -mixtape -compilation -dj"
    
    next_page_token = None
    for page in range(4):
        params = {"part": "snippet", "q": search_query, "type": "video", "order": "viewCount", 
                  "publishedAfter": published_after, "maxResults": 50, "key": API_KEY}
        if next_page_token: params["pageToken"] = next_page_token
        
        url = f"https://www.googleapis.com/youtube/v3/search?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url) as response:
            res = json.loads(response.read().decode())
            for item in res.get("items", []):
                vid = item["id"]["videoId"]
                video_ids.append(vid)
                video_snippets[vid] = {"id": vid, "title": item["snippet"]["title"], "channel": item["snippet"]["channelTitle"], "url": f"https://www.youtube.com/watch?v={vid}"}
            next_page_token = res.get("nextPageToken")
            if not next_page_token: break

    # Stats Retrieval
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        stats_params = {"part": "statistics,contentDetails", "id": ",".join(chunk), "key": API_KEY}
        with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?{urllib.parse.urlencode(stats_params)}") as resp:
            data = json.loads(resp.read().decode())
            for item in data.get("items", []):
                track = video_snippets[item["id"]]
                t_lower = track["title"].lower()
                if any(c in t_lower for c in GLOBAL_CLUTTER_BLACKLIST): continue
                if genre != "bouyon" and any(b in t_lower for b in INSTRUMENTAL_BLACKLIST): continue
                if "reggae" in t_lower or any(cb in t_lower for cb in CHUTNEY_BLACKLIST): continue
                dur = get_duration_seconds(item["contentDetails"].get("duration", ""))
                if dur < 90 or dur > 300: continue
                
                curr_views = int(item["statistics"].get("viewCount", 0))
                track["weekly_views"] = curr_views if is_first_run else max(0, curr_views - history.get(item["id"], 0))
                genre_tracks.append(track)

    genre_tracks.sort(key=lambda x: x["weekly_views"], reverse=True)
    top_50 = genre_tracks[:50]
    final_charts[genre] = top_50
    
    try:
        ws = sheet.worksheet(genre)
        ws.batch_clear(["A2:G60"])
        rows = [[i+1, t["title"], t["channel"], t["weekly_views"], t["id"], t["url"]] for i, t in enumerate(top_50)]
        if rows: ws.update("A2", rows)
    except: pass

with open("data.json", "w", encoding="utf-8") as f:
    json.dump({"last_updated": today.strftime('%Y-%m-%d'), "charts": final_charts}, f, indent=4)
