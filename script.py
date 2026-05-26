import os
import json
import urllib.request
import urllib.parse
import re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

# 1. Setup
API_KEY = os.environ.get("YOUTUBE_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

today = datetime.utcnow()
published_after = (today - timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
current_year = today.year

genres = ["soca", "dancehall", "bouyon", "afrobeats"]
history = {}
is_first_run = True

GENRE_QUERIES = {
    "soca": f'"soca {current_year}" OR "{current_year} soca"',
    "dancehall": f'"dancehall {current_year}" OR "{current_year} dancehall" OR "Shenseea" OR "Skeng" OR "Ayetian" OR "Valiant" OR "Skillibeng" OR "Vybz Kartel" OR "Mavado" OR "Masicka" OR "Popcaan" OR "Teejay"',
    "bouyon": f'"bouyon {current_year}" OR "{current_year} bouyon"',
    "afrobeats": f'"afrobeats {current_year}" OR "{current_year} afrobeats" OR "Burna Boy" OR "Wizkid" OR "Davido" OR "Rema" OR "Asake" OR "Tems" OR "Omah Lay" OR "Ayra Starr" OR "Seyi Vibez" OR "Kizz Daniel"'
}

# 2. Filtering
BLACKLIST = ["mix", "mixtape", "compilation", "dj", "type beat", "instrumental", "version", "edit", "karaoke"]
GLOBAL_CLUTTER = ["the voice", "full movie", "movie clip", "trailer"]

def get_seconds(d):
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', d)
    return (int(m.group(1) or 0) * 3600) + (int(m.group(2) or 0) * 60) + (int(m.group(3) or 0))

# 3. Execution
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
sheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)

if os.path.exists("data.json"):
    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        for g in data.get("charts", {}):
            for t in data["charts"][g]: history[t["id"]] = t["lifetime_views"]
            is_first_run = False

final_charts = {}

for genre in genres:
    genre_tracks = []
    # Using 'viewCount' order now that we have strict duration filters to avoid mixes
    params = {"part": "snippet", "q": GENRE_QUERIES[genre], "type": "video", "order": "viewCount", "publishedAfter": published_after, "maxResults": 50, "key": API_KEY}
    
    with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{urllib.parse.urlencode(params)}") as r:
        res = json.loads(r.read().decode())
        ids = [i["id"]["videoId"] for i in res.get("items", [])]

    with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?part=statistics,contentDetails&id={','.join(ids)}&key={API_KEY}") as r:
        stats = json.loads(r.read().decode())
        for item in stats.get("items", []):
            dur = get_seconds(item["contentDetails"].get("duration", ""))
            # STRICT FILTER: 60s to 300s (5 mins)
            if dur < 60 or dur > 300: continue
            
            t = next(x for x in res["items"] if x["id"]["videoId"] == item["id"])
            title = t["snippet"]["title"].lower()
            if any(b in title for b in BLACKLIST + GLOBAL_CLUTTER): continue
            
            views = int(item["statistics"].get("viewCount", 0))
            if views < 5000: continue
            
            genre_tracks.append({
                "id": item["id"], "title": t["snippet"]["title"], "channel": t["snippet"]["channelTitle"],
                "url": f"https://www.youtube.com/watch?v={item['id']}",
                "thumbnail": t["snippet"]["thumbnails"]["high"]["url"],
                "weekly_views": views if is_first_run else max(0, views - history.get(item["id"], 0))
            })

    genre_tracks.sort(key=lambda x: x["weekly_views"], reverse=True)
    final_charts[genre] = genre_tracks[:50]
    ws = sheet.worksheet(genre)
    ws.batch_clear(["A2:G60"])
    if genre_tracks:
        ws.update("A2", [[i+1, t["title"], t["channel"], t["weekly_views"], t["id"], t["url"], t["thumbnail"]] for i, t in enumerate(genre_tracks[:50])])

with open("data.json", "w", encoding="utf-8") as f:
    json.dump({"last_updated": today.strftime('%Y-%m-%d'), "charts": final_charts}, f, indent=4)