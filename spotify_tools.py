import os
import logging
import base64
import requests
import certifi
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# --- SPOTIFY CONFIG ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# --- MONGODB for saved playlists ---
saved_playlists_col = None
try:
    mongo_client = MongoClient(
        os.getenv("MONGO_URI"),
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=5000,
    )
    mongo_client.admin.command('ping')
    db = mongo_client["emily_brain_db"]
    saved_playlists_col = db["saved_playlists"]
    # Drop old unique index on guild_id only (prevents multiple playlists per guild)
    try:
        saved_playlists_col.drop_index("guild_id_1")
        logger.info("Dropped old guild_id unique index")
    except Exception:
        pass  # Index doesn't exist, that's fine
    saved_playlists_col.create_index([("guild_id", ASCENDING), ("user_id", ASCENDING), ("label", ASCENDING)])
    logger.info("Spotify playlist storage connected!")
except Exception as e:
    logger.error(f"Spotify DB error: {e}")

# --- TOKEN CACHE ---
_token_cache = {"token": None, "expires_at": None}


def is_configured():
    """Check if Spotify credentials are set."""
    return bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)


# ══════════════════════════════════════════════
# AUTHENTICATION (Client Credentials — no user login)
# ══════════════════════════════════════════════
def _get_client_token():
    """Get or refresh Spotify client credentials token."""
    if _token_cache["token"] and _token_cache["expires_at"] and \
       datetime.now() < _token_cache["expires_at"]:
        return _token_cache["token"]

    try:
        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        response = requests.post(
            SPOTIFY_TOKEN_URL,
            headers={"Authorization": f"Basic {auth_b64}"},
            data={"grant_type": "client_credentials"},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            _token_cache["token"] = data["access_token"]
            _token_cache["expires_at"] = datetime.now() + timedelta(seconds=data["expires_in"] - 60)
            logger.info("Spotify token refreshed")
            return _token_cache["token"]
        else:
            logger.error(f"Spotify token error: {response.status_code} — {response.text}")
            return None

    except Exception as e:
        logger.error(f"Spotify auth error: {e}")
        return None


def _spotify_get(endpoint, params=None):
    """Make an authenticated GET request to Spotify API with retry."""
    token = _get_client_token()
    if not token:
        logger.error("Spotify: No token available")
        return None

    timeout = 30 if "playlist" in endpoint else 10
    url = f"{SPOTIFY_API_URL}{endpoint}"

    for attempt in range(3):
        try:
            logger.info(f"Spotify GET: {url} (attempt {attempt + 1})")
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=timeout,
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                # Rate limited — wait and retry
                retry_after = int(response.headers.get("Retry-After", 2))
                logger.warning(f"Spotify rate limited, waiting {retry_after}s")
                import time
                time.sleep(retry_after)
                continue
            else:
                logger.error(f"Spotify API error: {response.status_code} — {response.text[:300]}")
                return None
        except requests.exceptions.Timeout:
            logger.warning(f"Spotify timeout (attempt {attempt + 1}): {endpoint}")
            if attempt < 2:
                import time
                time.sleep(1)
                continue
            return None
        except Exception as e:
            logger.error(f"Spotify request error: {e}")
            if attempt < 2:
                import time
                time.sleep(1)
                continue
            return None
    return None


# ══════════════════════════════════════════════
# SEARCH
# ══════════════════════════════════════════════
def search_tracks(query, limit=5):
    """Search for tracks on Spotify."""
    if not is_configured():
        return None, "Spotify not configured"

    data = _spotify_get("/search", {
        "q": query,
        "type": "track",
        "limit": limit,
        "market": "KE",  # Kenya market
    })

    if not data or "tracks" not in data:
        return None, "No results found"

    tracks = []
    for item in data["tracks"]["items"]:
        artists = ", ".join([a["name"] for a in item["artists"]])
        tracks.append({
            "name": item["name"],
            "artists": artists,
            "album": item["album"]["name"],
            "url": item["external_urls"].get("spotify", ""),
            "preview_url": item.get("preview_url"),
            "duration_ms": item["duration_ms"],
            "popularity": item.get("popularity", 0),
            "image": item["album"]["images"][0]["url"] if item["album"]["images"] else None,
        })

    return tracks, None


def search_artists(query, limit=3):
    """Search for artists on Spotify."""
    if not is_configured():
        return None, "Spotify not configured"

    data = _spotify_get("/search", {
        "q": query,
        "type": "artist",
        "limit": limit,
    })

    if not data or "artists" not in data:
        return None, "No artists found"

    artists = []
    for item in data["artists"]["items"]:
        artists.append({
            "name": item["name"],
            "genres": item.get("genres", [])[:3],
            "followers": item.get("followers", {}).get("total", 0),
            "url": item["external_urls"].get("spotify", ""),
            "popularity": item.get("popularity", 0),
            "image": item["images"][0]["url"] if item.get("images") else None,
        })

    return artists, None


# ══════════════════════════════════════════════
# RECOMMENDATIONS
# ══════════════════════════════════════════════

# Mood to Spotify audio features mapping
MOOD_PROFILES = {
    "chill": {"target_energy": 0.3, "target_valence": 0.5, "target_tempo": 90},
    "hype": {"target_energy": 0.9, "target_valence": 0.8, "target_tempo": 140},
    "sad": {"target_energy": 0.2, "target_valence": 0.2, "target_tempo": 80},
    "happy": {"target_energy": 0.7, "target_valence": 0.9, "target_tempo": 120},
    "workout": {"target_energy": 0.95, "target_valence": 0.6, "target_tempo": 150},
    "study": {"target_energy": 0.2, "target_valence": 0.4, "target_tempo": 100},
    "party": {"target_energy": 0.85, "target_valence": 0.85, "target_tempo": 128},
    "romantic": {"target_energy": 0.4, "target_valence": 0.6, "target_tempo": 95},
    "focus": {"target_energy": 0.3, "target_valence": 0.3, "target_tempo": 110},
    "road trip": {"target_energy": 0.7, "target_valence": 0.7, "target_tempo": 115},
    "sleep": {"target_energy": 0.1, "target_valence": 0.3, "target_tempo": 70},
    "afrobeats": {"target_energy": 0.7, "target_valence": 0.8, "target_tempo": 110},
    "kenyan": {"target_energy": 0.6, "target_valence": 0.7, "target_tempo": 105},
}

# Genre seeds for different moods (using valid Spotify seed genres only)
MOOD_GENRES = {
    "chill": ["chill", "indie-pop", "ambient"],
    "hype": ["hip-hop", "edm", "electronic"],
    "sad": ["acoustic", "piano", "indie"],
    "happy": ["pop", "dance", "funk"],
    "workout": ["edm", "hip-hop", "electronic"],
    "study": ["ambient", "classical", "piano"],
    "party": ["dance", "edm", "pop"],
    "romantic": ["r-n-b", "soul", "jazz"],
    "focus": ["ambient", "electronic", "classical"],
    "road trip": ["rock", "pop", "indie"],
    "sleep": ["ambient", "piano", "classical"],
    "afrobeats": ["afrobeat"],
    "kenyan": ["afrobeat"],
}


def get_recommendations(mood="chill", limit=5):
    """Get song recommendations based on mood using Spotify's recommendation engine."""
    if not is_configured():
        return None, "Spotify not configured"

    mood_lower = mood.lower()
    profile = MOOD_PROFILES.get(mood_lower, MOOD_PROFILES["chill"])
    genres = MOOD_GENRES.get(mood_lower, ["pop"])

    params = {
        "seed_genres": ",".join(genres[:2]),
        "limit": limit,
        "market": "KE",
    }
    params.update(profile)

    data = _spotify_get("/recommendations", params)

    if not data or "tracks" not in data:
        return None, f"No recommendations for mood: {mood}"

    tracks = []
    for item in data["tracks"]:
        artists = ", ".join([a["name"] for a in item["artists"]])
        duration = item["duration_ms"] // 1000
        mins = duration // 60
        secs = duration % 60
        tracks.append({
            "name": item["name"],
            "artists": artists,
            "album": item["album"]["name"],
            "url": item["external_urls"].get("spotify", ""),
            "duration": f"{mins}:{secs:02d}",
            "popularity": item.get("popularity", 0),
        })

    return tracks, None


# ══════════════════════════════════════════════
# PLAYLIST ANALYSIS (public playlists — no OAuth needed)
# ══════════════════════════════════════════════
def get_playlist(playlist_id):
    """Fetch a public Spotify playlist."""
    if not is_configured():
        return None, "Spotify not configured"

    try:
        # Fetch playlist with market=US
        data = _spotify_get(f"/playlists/{playlist_id}", {"market": "US"})

        if not data:
            return None, "Couldn't fetch that playlist. Make sure it's public!"

        tracks_total = data.get("tracks", {}).get("total", 0)
        tracks_items = len(data.get("tracks", {}).get("items", []))
        logger.info(f"Playlist fetched: {data.get('name', 'Unknown')} — total: {tracks_total}, items returned: {tracks_items}")

        # If tracks are empty, try fetching tracks separately
        if tracks_items == 0:
            logger.warning("Main endpoint returned 0 items, trying /tracks endpoint directly...")
            tracks_data = _spotify_get(f"/playlists/{playlist_id}/tracks", {
                "market": "US",
                "limit": 100,
            })
            if tracks_data and tracks_data.get("items"):
                data["tracks"] = tracks_data
                logger.info(f"Got {len(tracks_data['items'])} tracks from /tracks endpoint")

        return data, None
    except Exception as e:
        logger.error(f"Get playlist error: {e}")
        return None, f"Error: {e}"


def analyze_playlist(playlist_id):
    """Analyze a playlist and extract taste profile."""
    data, error = get_playlist(playlist_id)
    if error:
        return None, error

    tracks_data = data.get("tracks", {})
    tracks = tracks_data.get("items", [])
    if not tracks:
        return None, "Playlist is empty"

    # Extract stats
    artists_count = {}
    artist_ids = {}  # name -> id mapping
    total_popularity = 0
    total_duration = 0
    track_list = []
    valid_tracks = 0

    for item in tracks:
        track = item.get("track")
        if not track:
            continue

        valid_tracks += 1

        # Count artists and save IDs
        for artist in track.get("artists", []):
            name = artist.get("name", "Unknown")
            artists_count[name] = artists_count.get(name, 0) + 1
            if name not in artist_ids and artist.get("id"):
                artist_ids[name] = artist["id"]

        total_popularity += track.get("popularity", 0)
        total_duration += track.get("duration_ms", 0)
        track_list.append({
            "name": track.get("name", "Unknown"),
            "artists": ", ".join([a.get("name", "") for a in track.get("artists", [])]),
        })

    if valid_tracks == 0:
        return None, "No valid tracks found in playlist"

    # Top artists
    top_artists = sorted(artists_count.items(), key=lambda x: -x[1])[:5]

    # Only look up genres for top 5 artists (avoid rate limiting)
    genres_all = []
    for artist_name, _ in top_artists:
        aid = artist_ids.get(artist_name)
        if aid:
            try:
                artist_data = _spotify_get(f"/artists/{aid}")
                if artist_data:
                    genres_all.extend(artist_data.get("genres", []))
            except Exception:
                pass

    # Top genres
    genre_count = {}
    for g in genres_all:
        genre_count[g] = genre_count.get(g, 0) + 1
    top_genres = sorted(genre_count.items(), key=lambda x: -x[1])[:5]

    avg_popularity = total_popularity / valid_tracks if valid_tracks else 0
    total_mins = total_duration // 60000

    analysis = {
        "name": data.get("name", "Unknown"),
        "owner": data.get("owner", {}).get("display_name", "Unknown"),
        "track_count": valid_tracks,
        "total_duration_mins": total_mins,
        "avg_popularity": avg_popularity,
        "top_artists": top_artists,
        "top_genres": top_genres,
        "sample_tracks": track_list[:5],
    }

    return analysis, None


def get_similar_to_playlist(playlist_id, limit=5):
    """Get recommendations based on a playlist's top artists and genres."""
    analysis, error = analyze_playlist(playlist_id)
    if error:
        return None, error

    # Use top artists as seeds
    seed_artists = []
    for artist_name, _ in analysis["top_artists"][:2]:
        artists, _ = search_artists(artist_name, limit=1)
        if artists:
            # Get artist ID from search
            artist_data = _spotify_get("/search", {"q": artist_name, "type": "artist", "limit": 1})
            if artist_data and artist_data["artists"]["items"]:
                seed_artists.append(artist_data["artists"]["items"][0]["id"])

    # Use top genres as seeds
    seed_genres = [g for g, _ in analysis["top_genres"][:3]]
    # Spotify only accepts specific genre seeds, limit to what works
    valid_genres = _spotify_get("/recommendations/available-genre-seeds")
    if valid_genres:
        valid_set = set(valid_genres.get("genres", []))
        seed_genres = [g for g in seed_genres if g in valid_set][:2]

    if not seed_artists and not seed_genres:
        return None, "Couldn't extract enough data for recommendations"

    params = {
        "limit": limit,
        "market": "KE",
    }
    if seed_artists:
        params["seed_artists"] = ",".join(seed_artists[:2])
    if seed_genres:
        params["seed_genres"] = ",".join(seed_genres[:3 - len(seed_artists)])

    data = _spotify_get("/recommendations", params)
    if not data or "tracks" not in data:
        return None, "Couldn't generate recommendations"

    tracks = []
    for item in data["tracks"]:
        artists = ", ".join([a["name"] for a in item["artists"]])
        tracks.append({
            "name": item["name"],
            "artists": artists,
            "url": item["external_urls"].get("spotify", ""),
        })

    return {"analysis": analysis, "recommendations": tracks}, None


# ══════════════════════════════════════════════
# EXTRACT PLAYLIST ID FROM URL
# ══════════════════════════════════════════════
def extract_playlist_id(text):
    """Extract Spotify playlist ID from a URL or just return raw ID."""
    import re
    # Match Spotify playlist URLs
    match = re.search(r'playlist[/:]([a-zA-Z0-9]+)', text)
    if match:
        return match.group(1)
    # If it looks like a raw ID (alphanumeric, ~22 chars)
    clean = text.strip()
    if clean.isalnum() and 15 <= len(clean) <= 30:
        return clean
    return None


# ══════════════════════════════════════════════
# FORMATTED OUTPUTS
# ══════════════════════════════════════════════
def format_search_results(tracks):
    """Format search results for Discord."""
    if not tracks:
        return "No songs found!"

    lines = ["🎵 **Spotify Search Results:**\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(f"**{i}.** [{t['artists']} — {t['name']}]({t['url']})")
        lines.append(f"   💿 {t['album']} · {t['duration_ms'] // 60000}:{(t['duration_ms'] // 1000) % 60:02d}")
    return "\n".join(lines)


def format_recommendations(tracks, mood):
    """Format mood-based recommendations."""
    if not tracks:
        return f"Couldn't find tracks for mood: {mood}"

    lines = [f"🎵 **Spotify Picks for: {mood}**\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(f"**{i}.** [{t['artists']} — {t['name']}]({t['url']})")
        lines.append(f"   💿 {t['album']} · {t['duration']}")
    lines.append(f"\n_Powered by Spotify's recommendation engine_ 🎧")
    return "\n".join(lines)


def format_playlist_analysis(analysis):
    """Format playlist analysis."""
    lines = [f"🔍 **Playlist Analysis: {analysis['name']}**\n"]
    lines.append(f"👤 By: {analysis['owner']}")
    lines.append(f"🎵 {analysis['track_count']} tracks · {analysis['total_duration_mins']} minutes")
    lines.append(f"📊 Avg popularity: {analysis['avg_popularity']:.0f}/100\n")

    if analysis["top_artists"]:
        lines.append("**Your Top Artists:**")
        for artist, count in analysis["top_artists"]:
            lines.append(f"  • {artist} ({count} tracks)")

    if analysis["top_genres"]:
        lines.append("\n**Your Top Genres:**")
        for genre, count in analysis["top_genres"]:
            lines.append(f"  • {genre} ({count})")

    return "\n".join(lines)


def format_playlist_recommendations(result):
    """Format recommendations based on playlist."""
    analysis = result["analysis"]
    recs = result["recommendations"]

    lines = [f"🎯 **Based on your playlist: {analysis['name']}**\n"]
    lines.append(f"Your vibe: {', '.join([g for g, _ in analysis['top_genres'][:3]])}")
    lines.append(f"Artists you love: {', '.join([a for a, _ in analysis['top_artists'][:3]])}\n")
    lines.append("**Songs you might like:**")
    for i, t in enumerate(recs, 1):
        lines.append(f"**{i}.** [{t['artists']} — {t['name']}]({t['url']})")

    lines.append(f"\n_Based on your taste profile_ 🎧")
    return "\n".join(lines)


# ══════════════════════════════════════════════
# SAVED PLAYLIST MANAGEMENT (multiple per user)
# ══════════════════════════════════════════════
def save_user_playlist(guild_id, user_id, playlist_id, label="default", playlist_name=""):
    """Save a labeled playlist for a user. Each user can have multiple (chill, workout, etc)."""
    if saved_playlists_col is None:
        return False
    try:
        saved_playlists_col.update_one(
            {
                "guild_id": str(guild_id),
                "user_id": str(user_id),
                "label": label.lower().strip(),
            },
            {"$set": {
                "guild_id": str(guild_id),
                "user_id": str(user_id),
                "label": label.lower().strip(),
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "updated_at": datetime.utcnow(),
            }},
            upsert=True,
        )
        logger.info(f"Saved playlist '{label}' for user {user_id} in guild {guild_id}")
        return True
    except PyMongoError as e:
        logger.error(f"Save playlist error: {e}")
        return False


def get_user_playlists(guild_id, user_id):
    """Get all playlists for a user in a server."""
    if saved_playlists_col is None:
        return []
    try:
        return list(saved_playlists_col.find({
            "guild_id": str(guild_id),
            "user_id": str(user_id),
        }))
    except PyMongoError as e:
        logger.error(f"Get user playlists error: {e}")
        return []


def get_user_playlist_by_label(guild_id, user_id, label):
    """Get a specific labeled playlist."""
    if saved_playlists_col is None:
        return None
    try:
        return saved_playlists_col.find_one({
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "label": label.lower().strip(),
        })
    except PyMongoError as e:
        logger.error(f"Get playlist by label error: {e}")
        return None


def remove_user_playlist(guild_id, user_id, label):
    """Remove a saved playlist by label."""
    if saved_playlists_col is None:
        return False
    try:
        result = saved_playlists_col.delete_one({
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "label": label.lower().strip(),
        })
        return result.deleted_count > 0
    except PyMongoError as e:
        logger.error(f"Remove playlist error: {e}")
        return False


def get_all_server_playlists(guild_id):
    """Get ALL playlists saved by all users in a server (for Monday picks)."""
    if saved_playlists_col is None:
        return []
    try:
        return list(saved_playlists_col.find({
            "guild_id": str(guild_id),
            "playlist_id": {"$exists": True},
        }))
    except PyMongoError as e:
        logger.error(f"All server playlists error: {e}")
        return []


# Keep backward compatibility
def save_guild_playlist(guild_id, playlist_id, playlist_name="", added_by=""):
    """Backward-compatible save (saves as 'default' label)."""
    return save_user_playlist(guild_id, added_by or "server", playlist_id, "default", playlist_name)


def get_guild_playlist(guild_id):
    """Get any playlist for this guild (picks first available)."""
    if saved_playlists_col is None:
        return None
    try:
        # First try to find server-level settings
        doc = saved_playlists_col.find_one({"guild_id": str(guild_id), "music_channel_id": {"$exists": True}})
        if doc:
            return doc
        # Otherwise return any playlist for this guild
        doc = saved_playlists_col.find_one({"guild_id": str(guild_id), "playlist_id": {"$exists": True}})
        return doc
    except PyMongoError as e:
        logger.error(f"Get guild playlist error: {e}")
        return None


def get_all_guilds_with_playlists():
    """Get unique guilds that have playlists or music settings."""
    if saved_playlists_col is None:
        return []
    try:
        pipeline = [
            {"$group": {
                "_id": "$guild_id",
                "music_channel_id": {"$first": "$music_channel_id"},
                "playlist_id": {"$first": "$playlist_id"},
                "last_music_date": {"$first": "$last_music_date"},
            }}
        ]
        results = list(saved_playlists_col.aggregate(pipeline))
        return [{"guild_id": r["_id"], **{k: v for k, v in r.items() if k != "_id" and v}} for r in results]
    except PyMongoError as e:
        logger.error(f"All guilds error: {e}")
        return []


def format_user_playlists(guild_id, user_id):
    """Format all of a user's saved playlists."""
    playlists = get_user_playlists(guild_id, user_id)
    if not playlists:
        return "No playlists saved! Add one:\n`!setplaylist chill https://open.spotify.com/playlist/...`"

    lines = ["🎵 **Your Saved Playlists:**\n"]
    for p in playlists:
        label = p.get("label", "default")
        name = p.get("playlist_name", "Unknown")
        lines.append(f"• **{label}** — {name}")

    lines.append(f"\nAdd more: `!setplaylist <label> <link>`")
    lines.append(f"Remove: `!removeplaylist <label>`")
    lines.append(f"Get picks: `!tastify` or `!tastify <label>`")
    return "\n".join(lines)


def set_music_channel(guild_id, channel_id):
    """Save the music suggestions channel for a server."""
    if saved_playlists_col is None:
        return False
    try:
        saved_playlists_col.update_one(
            {"guild_id": str(guild_id)},
            {"$set": {
                "guild_id": str(guild_id),
                "music_channel_id": str(channel_id),
                "updated_at": datetime.utcnow(),
            }},
            upsert=True,
        )
        return True
    except PyMongoError as e:
        logger.error(f"Set music channel error: {e}")
        return False
