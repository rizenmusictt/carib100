import os
import json
import urllib.request
import urllib.parse
import re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

# 1. Configuration
API_KEY = os.environ.get("YOUTUBE_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
CURRENT_YEAR = datetime.utcnow().year

genres = ["soca", "dancehall", "bouyon"]
history = {}
is_first_run = True

# Pinpoint Search Matrix (Artists strictly bound to their genres to prevent global leaks)
SEARCH_QUERIES = {
    "soca": f'("soca {CURRENT_YEAR}" OR "{CURRENT_YEAR} soca") OR "soca Machel Montano" OR "soca Bunji Garlin" OR "soca Kes" OR "soca Voice" OR "soca Patrice Roberts"',
    "dancehall": f'("dancehall {CURRENT_YEAR}" OR "{CURRENT_YEAR} dancehall") OR "dancehall Shenseea" OR "dancehall Skeng" OR "dancehall Ayetian" OR "dancehall Valiant" OR "dancehall Skillibeng" OR "dancehall Vybz Kartel" OR "dancehall Mavado" OR "dancehall Masicka" OR "dancehall Popcaan" OR "dancehall Teejay"',
    "bouyon": f'("bouyon {CURRENT_YEAR}" OR "{CURRENT_YEAR} bouyon") OR "bouyon Triple Kay" OR "bouyon Asa Bantan" OR "bouyon Ridge" OR "bouyon Signal Band"'
}

# Watertight Content Filtering
INSTRUMENTAL_BLACKLIST = ["type beat", "instrumental", "version", "edit", "riddim loop", "prod by", "prod.", "free beat", "beat lyric", "karaoke", "clean loop"]
CHUTNEY_BLACKLIST = ["chutney", "ravi b", "karma", "raymond ramnarine", "dil-e-nadan", "ki & the band", "ki and the band", "omardath", "reshma ramlal", "gundilal", "boodram", "drupatee"]
GLOBAL_CLUTTER_BLACKLIST = ["the voice blind audition", "the voice battle", "full movie", "movie clip", "trailer", "season finale", "rihanna", "chinese"]

def get_seconds(d):
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', d)
    if not m:
        return 0
    return (int(m.group(1) or 0) * 3600) + (int(m.group(2) or 0) * 60) + (int(m.group(3) or 0))

# 2. Initialization
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
sheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)

if os.path.exists("data.json"):
    with open("data.json", "r") as f:
        data = json.load(f)
        for g in data.get("charts", {}):
            for t in data["charts"][g]: 
                history[t["id"]] = t.get("lifetime_views", 0)
            is_first_run = False

final_charts = {"charts": {}}
master_list = []

# 3. Processing
for genre in genres:
    genre_tracks = []
    # Mixes filtered directly at the API level via the query payload string
    search_query = f"{SEARCH_QUERIES[genre]} -mix -mixtape -compilation -dj"
    params = {"part": "snippet", "q": search_query, "type": "video", "order": "viewCount", "maxResults": 50, "key": API_KEY}
    
    with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{urllib.parse.urlencode(params)}") as r:
        res = json.loads(r.read().decode())
        ids = [i["id"]["videoId"] for i in res.get("items", [])]

    if not ids:
        final_charts["charts"][genre] = []
        continue

    with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?part=statistics,contentDetails&id={','.join(ids)}&key={API_KEY}") as r:
        stats = json.loads(r.read().decode())
        for item in stats.get("items", []):
            dur = get_seconds(item["contentDetails"].get("duration", ""))
            
            # Strict Single Format: No shorts, no extended mixes (1 to 5 mins max)
            if dur < 60 or dur > 300: 
                continue
            
            t = next(x for x in res["items"] if x["id"]["videoId"] == item["id"])
            title_lower = t["snippet"]["title"].lower()
            channel_lower = t["snippet"]["channelTitle"].lower()
            
            # Apply strict blacklists
            if any(c in title_lower for c in GLOBAL_CLUTTER_BLACKLIST): continue
            if any(ch in title_lower or ch in channel_lower for ch in CHUTNEY_BLACKLIST): continue
            
            if genre != "bouyon":
                if any(b in title_lower or b in channel_lower for b in INSTRUMENTAL_BLACKLIST): continue
            else:
                if "type beat" in title_lower or "free beat" in title_lower: continue

            views = int(item["statistics"].get("viewCount", 0))
            if views < 5000: continue
            
            track = {
                "id": item["id"], 
                "title": t["snippet"]["title"], 
                "channel": t["snippet"]["channelTitle"],
                "url": f"https://www.youtube.com/watch?v={item['id']}",
                "thumbnail": t["snippet"]["thumbnails"]["high"]["url"],
                "lifetime_views": views,
                "weekly_views": views if is_first_run else max(0, views - history.get(item["id"], 0))
            }
            genre_tracks.append(track)

    genre_tracks.sort(key=lambda x: x["weekly_views"], reverse=True)
    final_charts["charts"][genre] = genre_tracks  
    master_list.extend(genre_tracks)
    
    # Update Google Sheet (Stores up to 50 tracks)
    try:
        ws = sheet.worksheet(genre)
        ws.batch_clear(["A2:G60"])
        if genre_tracks:
            ws.update("A2", [[i+1, t["title"], t["channel"], t["weekly_views"], t["id"], t["url"], t["thumbnail"]] for i, t in enumerate(genre_tracks[:50])])
    except Exception as e:
        print(f"Error updating sheet for {genre}: {e}")

# 4. Master Sort & Save (Limits data.json output to top 25 overall hits)
unique_master = {t["id"]: t for t in master_list}.values()
sorted_master = sorted(unique_master, key=lambda x: x["weekly_views"], reverse=True)

final_charts["charts"]["all_genres"] = sorted_master[:25]

with open("data.json", "w") as f:
    json.dump(final_charts, f, indent=4)