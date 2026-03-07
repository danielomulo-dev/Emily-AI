import os
import logging
import base64
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# --- SPOTIFY CONFIG ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

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
    """Make an authenticated GET request to Spotify API."""
    token = _get_client_token()
    if not token:
        return None

    try:
        response = requests.get(
            f"{SPOTIFY_API_URL}{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Spotify API error: {response.status_code} — {response.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Spotify request error: {e}")
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

# Genre seeds for different moods
MOOD_GENRES = {
    "chill": ["chill", "indie-pop", "ambient"],
    "hype": ["hip-hop", "trap", "edm"],
    "sad": ["sad", "acoustic", "piano"],
    "happy": ["pop", "dance", "funk"],
    "workout": ["work-out", "edm", "hip-hop"],
    "study": ["study", "ambient", "classical"],
    "party": ["party", "dance", "edm"],
    "romantic": ["romance", "r-n-b", "soul"],
    "focus": ["focus", "ambient", "electronic"],
    "road trip": ["road-trip", "rock", "pop"],
    "sleep": ["sleep", "ambient", "piano"],
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

    data = _spotify_get(f"/playlists/{playlist_id}", {
        "fields": "name,description,tracks.items(track(name,artists,album,popularity,duration_ms,external_urls)),tracks.total,owner.display_name",
    })

    if not data:
        return None, "Couldn't fetch that playlist. Make sure it's public!"

    return data, None


def analyze_playlist(playlist_id):
    """Analyze a playlist and extract taste profile."""
    data, error = get_playlist(playlist_id)
    if error:
        return None, error

    tracks = data.get("tracks", {}).get("items", [])
    if not tracks:
        return None, "Playlist is empty"

    # Extract stats
    artists_count = {}
    genres_all = []
    total_popularity = 0
    total_duration = 0
    track_list = []

    for item in tracks:
        track = item.get("track")
        if not track:
            continue

        # Count artists
        for artist in track.get("artists", []):
            name = artist["name"]
            artists_count[name] = artists_count.get(name, 0) + 1

            # Try to get artist genres
            artist_data = _spotify_get(f"/artists/{artist['id']}")
            if artist_data:
                genres_all.extend(artist_data.get("genres", []))

        total_popularity += track.get("popularity", 0)
        total_duration += track.get("duration_ms", 0)
        track_list.append({
            "name": track["name"],
            "artists": ", ".join([a["name"] for a in track["artists"]]),
        })

    # Top artists
    top_artists = sorted(artists_count.items(), key=lambda x: -x[1])[:5]

    # Top genres
    genre_count = {}
    for g in genres_all:
        genre_count[g] = genre_count.get(g, 0) + 1
    top_genres = sorted(genre_count.items(), key=lambda x: -x[1])[:5]

    # Average popularity
    avg_popularity = total_popularity / len(tracks) if tracks else 0
    total_mins = total_duration // 60000

    analysis = {
        "name": data.get("name", "Unknown"),
        "owner": data.get("owner", {}).get("display_name", "Unknown"),
        "track_count": len(tracks),
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
