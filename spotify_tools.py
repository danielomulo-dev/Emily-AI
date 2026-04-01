import os
import logging
import base64
import random
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
        serverSelectionTimeoutMS=30000,
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
    """
    Get song recommendations based on mood using Spotify Search.
    (Spotify deprecated /recommendations in Nov 2024, so we use /search instead)
    """
    if not is_configured():
        return None, "Spotify not configured"

    mood_lower = mood.lower()
    if mood_lower not in MOOD_GENRES:
        return None, f"Unknown mood: {mood}"

    # Build search queries based on mood
    search_queries = {
        "chill": ["chill vibes", "lo-fi chill", "chill acoustic"],
        "hype": ["hype rap", "trap bangers", "hype workout"],
        "sad": ["sad songs", "heartbreak acoustic", "melancholy piano"],
        "happy": ["feel good pop", "happy vibes", "upbeat dance"],
        "workout": ["workout motivation", "gym hip hop", "high energy EDM"],
        "study": ["study music", "ambient focus", "lo-fi study beats"],
        "party": ["party hits", "dance party", "club bangers"],
        "romantic": ["romantic R&B", "love songs", "slow jams"],
        "focus": ["deep focus", "ambient concentration", "instrumental focus"],
        "road trip": ["road trip rock", "driving playlist", "road trip hits"],
        "sleep": ["sleep ambient", "calm piano sleep", "peaceful night"],
        "afrobeats": ["afrobeats hits", "amapiano", "afro pop"],
        "kenyan": ["kenyan music", "gengetone", "kenyan afrobeat"],
    }

    queries = search_queries.get(mood_lower, [f"{mood_lower} music"])
    query = random.choice(queries)

    # Use search endpoint
    params = {
        "q": query,
        "type": "track",
        "limit": min(limit, 10),
        "market": "KE",
    }

    data = _spotify_get("/search", params)

    if not data or "tracks" not in data or not data["tracks"].get("items"):
        return None, f"No recommendations for mood: {mood}"

    tracks = []
    for item in data["tracks"]["items"]:
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

    # Shuffle to add variety
    random.shuffle(tracks)

    return tracks[:limit], None


# ══════════════════════════════════════════════
# PLAYLIST ANALYSIS (public playlists — no OAuth needed)
# ══════════════════════════════════════════════
def get_playlist(playlist_id):
    """Fetch a public Spotify playlist."""
    if not is_configured():
        return None, "Spotify not configured"

    try:
        # Fetch playlist metadata
        data = _spotify_get(f"/playlists/{playlist_id}", {"market": "KE"})

        if not data:
            return None, "Couldn't fetch that playlist. Make sure it's public!"

        tracks_total = data.get("tracks", {}).get("total", 0)
        tracks_items = len(data.get("tracks", {}).get("items", []))
        logger.info(f"Playlist fetched: {data.get('name', 'Unknown')} — total: {tracks_total}, items returned: {tracks_items}")

        # If tracks are empty, try fetching items directly
        # Spotify Feb 2026 renamed /tracks to /items
        if tracks_items == 0:
            logger.warning("Main endpoint returned 0 items, trying /items endpoint...")
            tracks_data = _spotify_get(f"/playlists/{playlist_id}/items", {
                "market": "KE",
                "limit": 50,
            })
            if tracks_data and tracks_data.get("items"):
                data["tracks"] = tracks_data
                tracks_items = len(tracks_data["items"])
                logger.info(f"Got {tracks_items} tracks from /items endpoint")

        # If still empty, try the old /tracks endpoint as last resort
        if tracks_items == 0:
            logger.warning("Trying legacy /tracks endpoint...")
            tracks_data = _spotify_get(f"/playlists/{playlist_id}/tracks", {
                "market": "KE",
                "limit": 50,
            })
            if tracks_data and tracks_data.get("items"):
                data["tracks"] = tracks_data
                tracks_items = len(tracks_data["items"])
                logger.info(f"Got {tracks_items} tracks from /tracks endpoint")

        return data, None
    except Exception as e:
        logger.error(f"Get playlist error: {e}")
        return None, f"Error: {e}"


def analyze_playlist(playlist_id):
    """Analyze a playlist and extract taste profile."""
    data, error = get_playlist(playlist_id)
    if error:
        return None, error

    playlist_name = data.get("name", "Unknown")
    playlist_owner = data.get("owner", {}).get("display_name", "Unknown")
    playlist_desc = data.get("description", "")

    tracks_data = data.get("tracks", {})
    tracks = tracks_data.get("items", [])

    # ── FALLBACK: If Spotify doesn't return tracks (Feb 2026 API changes),
    # use the playlist name/description to search and build a taste profile ──
    if not tracks:
        logger.warning(f"No tracks returned for playlist '{playlist_name}', using name-based fallback")

        # Search for tracks matching the playlist name
        search_query = playlist_name
        if playlist_desc:
            # Use first few words of description too
            search_query = f"{playlist_name} {playlist_desc[:50]}"

        search_data = _spotify_get("/search", {
            "q": search_query,
            "type": "track",
            "limit": 10,
            "market": "KE",
        })

        if search_data and search_data.get("tracks", {}).get("items"):
            tracks = [{"track": item} for item in search_data["tracks"]["items"]]
            logger.info(f"Fallback search found {len(tracks)} tracks for '{playlist_name}'")
        else:
            return None, f"Couldn't load tracks from playlist '{playlist_name}'. Try a different playlist!"

    # Extract stats
    artists_count = {}
    artist_ids = {}
    total_popularity = 0
    total_duration = 0
    track_list = []
    valid_tracks = 0

    for item in tracks:
        track = item.get("track")
        if not track:
            continue

        valid_tracks += 1

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

    # Look up genres for top 5 artists
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
        "name": playlist_name,
        "owner": playlist_owner,
        "track_count": valid_tracks,
        "total_duration_mins": total_mins,
        "avg_popularity": avg_popularity,
        "top_artists": top_artists,
        "top_genres": top_genres,
        "sample_tracks": track_list[:5],
    }

    return analysis, None


def get_similar_to_playlist(playlist_id, limit=5):
    """Get recommendations based on a playlist's top artists and genres using search."""
    analysis, error = analyze_playlist(playlist_id)
    if error:
        return None, error

    # Build search queries from top artists and genres
    top_artist_names = [name for name, _ in analysis["top_artists"][:3]]
    top_genre_names = [genre for genre, _ in analysis["top_genres"][:3]]

    tracks = []
    seen_tracks = set()

    # Search for tracks by similar artists
    for artist_name in top_artist_names:
        query = f"artist:{artist_name}"
        data = _spotify_get("/search", {
            "q": query,
            "type": "track",
            "limit": 5,
            "market": "KE",
        })
        if data and data.get("tracks", {}).get("items"):
            for item in data["tracks"]["items"]:
                track_key = f"{item['name']}_{item['artists'][0]['name']}"
                if track_key not in seen_tracks:
                    seen_tracks.add(track_key)
                    artists = ", ".join([a["name"] for a in item["artists"]])
                    tracks.append({
                        "name": item["name"],
                        "artists": artists,
                        "url": item["external_urls"].get("spotify", ""),
                    })

    # Search by genres for variety
    for genre in top_genre_names[:2]:
        query = f"genre:{genre}"
        data = _spotify_get("/search", {
            "q": query,
            "type": "track",
            "limit": 3,
            "market": "KE",
        })
        if data and data.get("tracks", {}).get("items"):
            for item in data["tracks"]["items"]:
                track_key = f"{item['name']}_{item['artists'][0]['name']}"
                if track_key not in seen_tracks:
                    seen_tracks.add(track_key)
                    artists = ", ".join([a["name"] for a in item["artists"]])
                    tracks.append({
                        "name": item["name"],
                        "artists": artists,
                        "url": item["external_urls"].get("spotify", ""),
                    })

    if not tracks:
        return None, "Couldn't find similar tracks"

    # Shuffle and limit
    random.shuffle(tracks)
    return {"analysis": analysis, "recommendations": tracks[:limit]}, None


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
# USER MUSIC TASTE (artist-based recommendations)
# Spotify Feb 2026 changes removed playlist track access
# for non-owned playlists, so we use artists instead.
# ══════════════════════════════════════════════
def save_user_artists(user_id, artists, guild_id=None, channel_id=None):
    """Save a user's favorite artists for weekly recommendations.
    artists = list of artist name strings
    """
    if saved_playlists_col is None:
        logger.error("save_user_artists: collection is None!")
        return False
    try:
        update_data = {
            "user_id": str(user_id),
            "artists": artists,
            "type": "weekly_music",
            "updated_at": datetime.utcnow(),
        }
        if guild_id:
            update_data["guild_id"] = str(guild_id)
        if channel_id:
            update_data["channel_id"] = str(channel_id)

        result = saved_playlists_col.update_one(
            {"user_id": str(user_id), "type": "weekly_music"},
            {"$set": update_data},
            upsert=True,
        )
        logger.info(f"Saved music taste for {user_id}: {artists} "
                     f"(upserted={result.upserted_id is not None})")
        return True
    except Exception as e:
        logger.error(f"Save user artists error: {e}")
        return False


def get_user_artists(user_id):
    """Get a user's saved favorite artists."""
    if saved_playlists_col is None:
        return None
    try:
        doc = saved_playlists_col.find_one({"user_id": str(user_id), "type": "weekly_music"})
        return doc
    except Exception as e:
        logger.error(f"Get user artists error: {e}")
        return None


def get_all_weekly_music_users():
    """Get all users who have saved music taste."""
    if saved_playlists_col is None:
        return []
    try:
        return list(saved_playlists_col.find({"type": "weekly_music"}))
    except Exception as e:
        logger.error(f"Get all weekly music users error: {e}")
        return []


def get_recs_from_artists(artist_names, limit=7):
    """Get track recommendations based on a list of artist names using search."""
    if not is_configured():
        return None, "Spotify not configured"

    tracks = []
    seen = set()
    artist_genres = []

    for artist_name in artist_names:
        # Search for tracks by this artist
        data = _spotify_get("/search", {
            "q": f"artist:{artist_name}",
            "type": "track",
            "limit": 5,
            "market": "KE",
        })
        if data and data.get("tracks", {}).get("items"):
            for item in data["tracks"]["items"]:
                key = f"{item['name']}_{item['artists'][0]['name']}"
                if key not in seen:
                    seen.add(key)
                    artists = ", ".join([a["name"] for a in item["artists"]])
                    tracks.append({
                        "name": item["name"],
                        "artists": artists,
                        "url": item["external_urls"].get("spotify", ""),
                    })

        # Get artist genres for related searches
        artist_search = _spotify_get("/search", {
            "q": artist_name,
            "type": "artist",
            "limit": 1,
        })
        if artist_search and artist_search.get("artists", {}).get("items"):
            artist_obj = artist_search["artists"]["items"][0]
            artist_id = artist_obj.get("id")
            genres = artist_obj.get("genres", [])
            artist_genres.extend(genres[:2])

            # Get related artists for variety
            if artist_id:
                related = _spotify_get(f"/artists/{artist_id}/related-artists")
                if related and related.get("artists"):
                    for rel in related["artists"][:2]:
                        rel_data = _spotify_get("/search", {
                            "q": f"artist:{rel['name']}",
                            "type": "track",
                            "limit": 2,
                            "market": "KE",
                        })
                        if rel_data and rel_data.get("tracks", {}).get("items"):
                            for item in rel_data["tracks"]["items"]:
                                key = f"{item['name']}_{item['artists'][0]['name']}"
                                if key not in seen:
                                    seen.add(key)
                                    artists_str = ", ".join([a["name"] for a in item["artists"]])
                                    tracks.append({
                                        "name": item["name"],
                                        "artists": artists_str,
                                        "url": item["external_urls"].get("spotify", ""),
                                    })

    if not tracks:
        return None, "Couldn't find tracks for those artists"

    # Deduplicate genres
    unique_genres = list(dict.fromkeys(artist_genres))

    random.shuffle(tracks)
    return {
        "artists": artist_names,
        "genres": unique_genres[:5],
        "recommendations": tracks[:limit],
    }, None


def format_weekly_recommendations(result):
    """Format artist-based recommendations for Discord."""
    artists = result.get("artists", [])
    genres = result.get("genres", [])
    tracks = result["recommendations"]

    lines = ["🎵 **Weekly Music Picks**\n"]
    lines.append(f"Based on your taste: {', '.join(artists)}")
    if genres:
        lines.append(f"Genres: {', '.join(genres[:4])}")
    lines.append("")

    lines.append("**Tracks you might love:**")
    for i, t in enumerate(tracks, 1):
        if t.get("url"):
            lines.append(f"**{i}.** [{t['artists']} — {t['name']}]({t['url']})")
        else:
            lines.append(f"**{i}.** {t['artists']} — {t['name']}")

    lines.append("\n_Update your taste anytime with `!mytaste artist1, artist2, artist3`_ 🎧")
    return "\n".join(lines)


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
