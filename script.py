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

# Strict 4-month boundary
today = datetime.utcnow()
four_months_ago = today - timedelta(days=120)  
published_after = four_months_ago.strftime('%Y-%m-%dT%H:%M:%SZ')

# Fixed Bouyon query to prevent "Ridge" from pulling random non-music videos
SEARCH_QUERIES = {
    "soca": f"{CURRENT_YEAR} soca",
    "dancehall": f"{CURRENT_YEAR} dancehall",
    "bouyon": f'"{CURRENT_YEAR} bouyon" OR "Asa Bantan" OR "Triple Kay" OR "Ridge bouyon" OR "Signal Band"'
}

# Watertight Content Filtering
INSTRUMENTAL_BLACKLIST = ["type beat", "instrumental", "version", "edit", "riddim loop", "prod by", "prod.", "free beat", "beat lyric", "karaoke", "clean loop"]
CHUTNEY_BLACKLIST = ["chutney", "ravi b", "karma", "raymond ramnarine", "dil-e-nadan", "ki & the band", "ki and the band", "omardath", "reshma ramlal", "gundilal", "boodram", "drupatee"]
GLOBAL_CLUTTER_BLACKLIST = ["the voice blind audition", "the voice battle", "full movie", "movie clip", "trailer", "season finale", "rihanna", "chinese"]

# Regex Patterns for exact word matching
MIX_PATTERN = re.compile(r'\b(mix|mixes|mixtape|mixtapes)\b')
SOCA_ROAD_MIX_PATTERN = re.compile(r'\b(road\s?mix)\b')

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
            if g not in genres: continue
            for t in data["charts"][g]: 
                history[t["id"]] = t.get("lifetime_views", 0)
            is_first_run = False

final_charts = {"charts": {}}
master_list = []

# 3. Processing
for genre in genres:
    genre_tracks = []
    
    # Flat API exclusions
    search_query = f"{SEARCH_QUERIES[genre]} -mix -mixes -mixtape -mixtapes -compilation"
    if genre == "soca":
        search_query += " -roadmix"
        
    next_page_token = None
    pages_fetched = 0
    MAX_PAGES = 15 # Checks up to 750 tracks per genre to ensure we hit the 50 cap
    
    while len(genre_tracks) < 50 and pages_fetched < MAX_PAGES:
        params = {
            "part": "snippet", 
            "q": search_query, 
            "type": "video", 
            "order": "viewCount", 
            "publishedAfter": published_after, 
            "maxResults": 50, 
            "key": API_KEY
        }
        
        if next_page_token:
            params["pageToken"] = next_page_token
            
        try:
            with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{urllib.parse.urlencode(params)}") as r:
                res = json.loads(r.read().decode())
                ids = [i["id"]["videoId"] for i in res.get("items", [])]
                next_page_token = res.get("nextPageToken")
        except Exception as e:
            print(f"Search error for {genre}: {e}")
            break
            
        pages_fetched += 1

        if not ids:
            if not next_page_token: break
            continue

        try:
            with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?part=statistics,contentDetails&id={','.join(ids)}&key={API_KEY}") as r:
                stats = json.loads(r.read().decode())
        except Exception as e:
            print(f"Video details error for {genre}: {e}")
            continue

        for item in stats.get("items", []):
            if len(genre_tracks) >= 50:
                break
                
            if any(t['id'] == item['id'] for t in genre_tracks):
                continue
                
            dur = get_seconds(item["contentDetails"].get("duration", ""))
            
            # Expanded format filter: 1 to 7 mins (Allows music videos with long intros/skits)
            if dur < 60 or dur > 420: 
                continue
            
            t = next((x for x in res["items"] if x["id"]["videoId"] == item["id"]), None)
            if not t: continue
            
            title_lower = t["snippet"]["title"].lower()
            channel_lower = t["snippet"]["channelTitle"].lower()
            
            # Word filter blocks
            if MIX_PATTERN.search(title_lower): continue
            if genre == "soca" and SOCA_ROAD_MIX_PATTERN.search(title_lower): continue
            
            if any(c in title_lower for c in GLOBAL_CLUTTER_BLACKLIST): continue
            if any(ch in title_lower or ch in channel_lower for ch in CHUTNEY_BLACKLIST): continue
            
            if genre != "bouyon":
                if any(b in title_lower or b in channel_lower for b in INSTRUMENTAL_BLACKLIST): continue
            else:
                if "type beat" in title_lower or "free beat" in title_lower: continue

            views = int(item["statistics"].get("viewCount", 0))
            
            # Custom view thresholds based on genre market size
            min_views = 2500 if genre == "bouyon" else 5000
            if views < min_views: continue
            
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
                
        if not next_page_token:
            break

    print(f"[{genre.upper()}] Success: Gathered {len(genre_tracks)} tracks across {pages_fetched} API pages.")

    genre_tracks.sort(key=lambda x: x["weekly_views"], reverse=True)
    final_charts["charts"][genre] = genre_tracks  
    master_list.extend(genre_tracks)
    
    # Update Google Sheet
    try:
        ws = sheet.worksheet(genre)
        ws.batch_clear(["A2:G60"])
        if genre_tracks:
            ws.update("A2", [[i+1, t["title"], t["channel"], t["weekly_views"], t["id"], t["url"], t["thumbnail"]] for i, t in enumerate(genre_tracks)])
    except Exception as e:
        print(f"Error updating sheet for {genre}: {e}")

# 4. Master Sort & Save
unique_master = {t["id"]: t for t in master_list}.values()
sorted_master = sorted(unique_master, key=lambda x: x["weekly_views"], reverse=True)

final_charts["charts"]["all_genres"] = sorted_master[:25]

with open("data.json", "w") as f:
    json.dump(final_charts, f, indent=4)
