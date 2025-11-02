# backend/spotify_client.py
import os
import re
import random
from pathlib import Path
from typing import List, Dict

from functools import lru_cache
from dotenv import load_dotenv
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException


# --- Load .env from THIS folder explicitly ---
ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)

SCOPES = "playlist-modify-public playlist-modify-private user-read-email"
DEFAULT_MARKET = "US"
DEFAULT_SEED_GENRES = [
    "pop", "dance-pop", "edm", "house", "hip-hop", "r-n-b",
    "indie-pop", "alt-rock", "trap", "electronic", "chill",
    "ambient", "dancehall", "reggaeton", "funk", "soul"
]

def _get_env(primary: str, alt: str | None = None) -> str:
    """Read env var with optional fallback name (supports SPOTIFY_* or SPOTIPY_*)."""
    val = os.getenv(primary) or (os.getenv(alt) if alt else None)
    if not val:
        hint = f"{primary}" + (f" or {alt}" if alt else "")
        raise RuntimeError(
            f"Missing env var: {hint}. Create backend/.env with:\n"
            "SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI\n"
            "(optionally also SPOTIPY_* equivalents) and restart the server."
        )
    return val

def oauth(state: str) -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=_get_env("SPOTIFY_CLIENT_ID", "SPOTIPY_CLIENT_ID"),
        client_secret=_get_env("SPOTIFY_CLIENT_SECRET", "SPOTIPY_CLIENT_SECRET"),
        redirect_uri=_get_env("SPOTIFY_REDIRECT_URI", "SPOTIPY_REDIRECT_URI"),
        scope=SCOPES,
        cache_path=None,
        show_dialog=True,
        state=state,
    )

# --- naive in-memory token store for PoC (replace with DB for MVP) ---
TOKENS: Dict[str, dict] = {}

def exchange_code_for_token(state: str, code: str, username: str):
    sp_oauth = oauth(state)
    token_info = sp_oauth.get_access_token(code, as_dict=True)
    if not token_info or "access_token" not in token_info:
        raise RuntimeError("Spotify OAuth failed: no access_token in response.")
    TOKENS[username] = token_info

def get_spotify(username: str) -> Spotify:
    token_info = TOKENS.get(username)
    if not token_info:
        raise RuntimeError("No Spotify token; user not connected.")
    sp_oauth = oauth("refresh")
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        TOKENS[username] = token_info
    return Spotify(auth=token_info["access_token"])

# ---------- Playlist helpers (these were missing!) ----------
def create_playlist(sp: Spotify, user_id: str, name: str, public: bool = False, description: str = "") -> str:
    pl = sp.user_playlist_create(user=user_id, name=name, public=public, description=description)
    return pl["id"]

def add_tracks(sp: Spotify, playlist_id: str, uris: List[str]):
    if uris:
        sp.playlist_add_items(playlist_id, uris)

# ----------------- RECS HELPERS -----------------
def _clamp01(x, default=0.5):
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return default

_ALLOWED_GENRES_CACHE: set[str] | None = None
DEFAULT_SEED_GENRES = {
    "pop", "dance-pop", "edm", "house", "hip-hop", "r-n-b",
    "indie-pop", "alt-rock", "trap", "electronic", "chill",
    "ambient", "dancehall", "reggaeton", "funk", "soul"
}

def _allowed_genres(sp: Spotify) -> set[str]:
    """
    Load Spotify's genre seeds. Works across Spotipy versions.
    Falls back to DEFAULT_SEED_GENRES and caches result.
    """
    global _ALLOWED_GENRES_CACHE
    if _ALLOWED_GENRES_CACHE:
        return _ALLOWED_GENRES_CACHE

    try:
        # Spotipy has used both names across versions
        if hasattr(sp, "recommendation_genre_seeds"):
            seeds = sp.recommendation_genre_seeds()
        else:
            seeds = sp.recommendations_available_genre_seeds()
        if isinstance(seeds, dict):
            seeds = seeds.get("genres", [])
        seeds = list(seeds or [])
        if seeds:
            _ALLOWED_GENRES_CACHE = set(seeds)
            return _ALLOWED_GENRES_CACHE
    except Exception as e:
        print(f"[genres] failed to fetch seeds: {e}")

    # Hard fallback so we ALWAYS have something
    _ALLOWED_GENRES_CACHE = set(DEFAULT_SEED_GENRES)
    return _ALLOWED_GENRES_CACHE

def _split_tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z\-]+", s.lower()) if t]

def _normalize_genre_list(sp: Spotify, genres) -> list[str]:
    allowed = _allowed_genres(sp) or DEFAULT_SEED_GENRES

    tokens: list[str] = []
    if isinstance(genres, str):
        tokens = _split_tokens(genres)
    elif isinstance(genres, (list, tuple)):
        for g in genres:
            if isinstance(g, str):
                tokens.extend(_split_tokens(g))

    synonyms = {
        "lofi": "chillhop", "lo-fi": "chillhop",
        "hiphop": "hip-hop", "hip-hop": "hip-hop",
        "rnb": "r-n-b", "r&b": "r-n-b",
        "indiepop": "indie-pop", "alt": "alternative",
        "altrock": "alt-rock", "electro": "electronic",
        "dance": "dance-pop", "party": "dance-pop",
        "workout": "edm", "club": "house",
        "reggae": "dancehall"
    }

    cleaned, seen = [], set()
    for t in tokens:
        t = synonyms.get(t, t)
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)

    valid = [g for g in cleaned if g in allowed]
    if not valid:
        # Defaults chosen to cover both chill and uptempo use cases
        for g in ["dance-pop", "edm", "hip-hop", "indie-pop", "pop", "house", "trap"]:
            if g in allowed:
                valid.append(g)
        valid = valid[:5]
    return valid[:5]

def _jitter(val: float | None, spread=0.15):
    if val is None:
        return None
    lo = max(0.0, val - spread)
    hi = min(1.0, val + spread)
    return round(random.uniform(lo, hi), 2)

def _keywords_to_seed_tracks(sp: Spotify, keywords: list[str], max_tracks=3) -> list[str]:
    seeds: list[str] = []
    try:
        for kw in keywords[:3]:
            q = f'track:"{kw}"' if " " in kw else kw
            res = sp.search(q=q, type="track", limit=2, market=DEFAULT_MARKET)
            items = res.get("tracks", {}).get("items", [])
            for t in items:
                if t.get("id"):
                    seeds.append(t["id"])
                    if len(seeds) >= max_tracks:
                        break
            if len(seeds) >= max_tracks:
                break
    except Exception:
        pass
    return seeds

def _artist_seed_genres(sp: Spotify, artist_ids: list[str], limit: int = 2) -> list[str]:
    """Look up each artist's genres and convert to valid Spotify seed genre names."""
    raw = []
    for aid in artist_ids[:3]:
        try:
            a = sp.artist(aid)
            raw.extend(a.get("genres") or [])
        except Exception:
            pass
    # Normalize to Spotify's allowed seeds and cap
    return _normalize_genre_list(sp, raw)[:limit]

_BLOCK_TERMS = {
    "rain", "rainfall", "thunder", "storm", "thunderstorm",
    "ocean", "waves", "water sounds", "nature sounds",
    "brown noise", "white noise", "pink noise",
    "asmr", "sleep", "meditation", "focus sounds", "relaxing sounds",
}
# Allow famous legit exceptions (avoid nuking real songs like Purple Rain)
_EXCEPT_PHRASES = {"purple rain"}

def _looks_like_sfx(track_or_name: dict | str) -> bool:
    """Heuristic: titles/albums that are obviously SFX/white-noise/etc."""
    if isinstance(track_or_name, dict):
        title = (track_or_name.get("name") or "").lower()
        album = (track_or_name.get("album", {}).get("name") or "").lower()
        hay = f"{title} {album}"
    else:
        hay = (track_or_name or "").lower()

    if any(p in hay for p in _EXCEPT_PHRASES):
        return False

    # avoid false positives like "Brainstorm"
    tokens = {t.strip() for t in re.split(r"[^a-z]+", hay)}
    if any(term.replace(" ", "") in tokens for term in {t.replace(" ", "") for t in _BLOCK_TERMS}):
        return True

    # also catch phrases
    for term in _BLOCK_TERMS:
        if term in hay:
            return True
    return False


def _audio_feature_filter(sp: Spotify, uris: list[str], params: dict) -> list[str]:
    """
    Remove obvious SFX/ambient/podcast tracks, and loosely enforce the vibe:
      - prefer vocals (instrumentalness < 0.85) unless 'instrumental_ok' is true
      - cut super-long soundscapes (> 10 min)
      - avoid speechy/podcast content (speechiness >= 0.66)
      - loosely keep tempo/energy within plausible window if provided
    Returns filtered URIs (keeps order).
    """
    if not uris:
        return uris

    instrumental_ok = bool(params.get("instrumental_ok", False))
    vocals_required = params.get("vocals_required", not instrumental_ok)

    # Tempo window from params if present
    target_tempo = params.get("tempo_bpm")
    tempo_lo = tempo_hi = None
    if isinstance(target_tempo, (int, float)):
        tempo_lo, tempo_hi = max(40, target_tempo - 20), min(220, target_tempo + 20)

    # Fetch track meta first to title-filter cheaply
    # (Spotipy accepts a list of track IDs, so convert)
    ids = [u.split(":")[-1] for u in uris]
    try:
        meta = sp.tracks(ids).get("tracks", []) or []
    except Exception:
        meta = []

    keep_mask = [True] * len(uris)

    # Title/album SFX filter
    for i, t in enumerate(meta):
        try:
            if _looks_like_sfx(t):
                keep_mask[i] = False
        except Exception:
            pass

    # Audio-feature filter (batch)
    try:
        feats = sp.audio_features(ids)
    except Exception:
        feats = None

    if feats:
        for i, f in enumerate(feats):
            if not keep_mask[i] or not f:
                continue

            instrumentalness = f.get("instrumentalness")
            speechiness = f.get("speechiness")
            energy = f.get("energy")
            tempo = f.get("tempo")
            duration_ms = f.get("duration_ms") or 0

            # Kill extremely long soundscapes
            if duration_ms >= 10 * 60 * 1000:
                keep_mask[i] = False
                continue

            # Avoid podcasts, spoken word
            if speechiness is not None and speechiness >= 0.66:
                keep_mask[i] = False
                continue

            # Prefer vocals unless caller says instrumentals are okay
            if vocals_required and instrumentalness is not None and instrumentalness >= 0.85:
                keep_mask[i] = False
                continue

            # Extremely low energy → likely ambient pad
            if energy is not None and energy <= 0.03:
                keep_mask[i] = False
                continue

            # Keep tempo within a loose window if set
            if tempo is not None and tempo_lo is not None and tempo_hi is not None:
                if tempo < tempo_lo - 8 or tempo > tempo_hi + 8:
                    keep_mask[i] = False
                    continue

    return [u for u, ok in zip(uris, keep_mask) if ok]

# ----------------- RECOMMENDER -----------------
# spotify_client.py (only this function needs to change if you already have the helpers from earlier)

def recommend_tracks(sp: Spotify, params: dict, n: int = 40) -> list[str]:
    """
    Make Spotify recommendations that honor user-selected artists/genres
    and the AI vibe parameters, with robust fallbacks.
    """
    # ---- inputs from params ----
    user_artist_ids: list[str] = params.get("user_artist_ids") or []
    # genres: accept either explicit user_genres or AI candidates
    raw_genres = params.get("user_genres") or params.get("genre_candidates") or params.get("genres") or []
    candidates = _normalize_genre_list(sp, raw_genres)  # <- converts to valid Spotify seeds

    # Targets
    def mid(pair, default=0.5):
        try:
            return max(0.0, min(1.0, sum(pair) / 2))
        except Exception:
            return default

    energy_center       = mid(params.get("energy_range", [0.5, 0.5]))
    valence_center      = mid(params.get("valence_range", [0.5, 0.5]))
    danceability_center = mid(params.get("danceability_range", [0.5, 0.5]))
    acoustic_center     = mid(params.get("acousticness_range", [0.5, 0.5]))
    tempo               = int(params.get("tempo_bpm", 100))
    tempo               = max(40, min(220, tempo))

    # ---- build seeds (<= 5 total) ----
    seed_artists = (user_artist_ids or [])[:3]  # leave room for genres

    # start with user/AI genres
    seed_genres = candidates[: max(0, 5 - len(seed_artists))]

    # If user chose artists but no valid genres, borrow the artists' own genres
    if seed_artists and not seed_genres:
        seed_genres = _artist_seed_genres(sp, seed_artists, limit=max(0, 5 - len(seed_artists)))

    # GUARANTEE: at least one valid seed, no matter what
    if not seed_artists and not seed_genres:
        allowed = _allowed_genres(sp) or set(DEFAULT_SEED_GENRES)
        # bias toward upbeat seeds for high energy prompts
        upbeat_pref = ["dance-pop", "edm", "house", "pop", "hip-hop"]
        picked = [g for g in upbeat_pref if g in allowed] or list(allowed)
        seed_genres = [picked[0]]

    # Assemble seeds dict (Spotipy accepts lists)
    seeds = {}
    if seed_artists:
        seeds["seed_artists"] = seed_artists[: max(0, 5)]
    if seed_genres and len(seed_artists) < 5:
        room = 5 - len(seed_artists)
        seeds["seed_genres"] = seed_genres[:room]

    # ---- call recs with jitter (variety) ----
    def _j(x, spread=0.12):
        if x is None:
            return None
        lo = max(0.0, x - spread)
        hi = min(1.0, x + spread)
        return round(random.uniform(lo, hi), 2)

    kwargs = {
        "limit": min(50, max(10, n)),
        "market": DEFAULT_MARKET,
        "target_energy": _j(energy_center),
        "target_valence": _j(valence_center),
        "target_danceability": _j(danceability_center),
        "target_acousticness": _j(acoustic_center),
        "target_tempo": tempo + random.randint(-8, 8),
        **seeds,
    }

    kw_seeds = _keywords_to_seed_tracks(sp, params.get("keywords", []), max_tracks=2)
    if kw_seeds and "seed_tracks" not in kwargs and random.random() < 0.6:
        current_seed_count = len(seeds.get("seed_artists", [])) + len(seeds.get("seed_genres", []))
        room = max(0, 5 - current_seed_count)
        if room > 0:
            kwargs["seed_tracks"] = kw_seeds[:1]  # keep it to 1 to be safe

    bag, seen = [], set()

    def _add_from_recs(base_kwargs: dict) -> bool:
        """
        Try the recommendations endpoint in a few compatible shapes:
        1) with 'market'
        2) with 'country'
        3) with neither (let API infer)
        Success = we got any tracks back.
        """
        variants = []

        # 1) as-is (market)
        variants.append({k: v for k, v in base_kwargs.items() if v is not None})

        # 2) swap market -> country
        if "market" in base_kwargs:
            k2 = base_kwargs.copy()
            val = k2.pop("market", None)
            if val:
                k2["country"] = val
            variants.append({k: v for k, v in k2.items() if v is not None})
        else:
            # if no market provided, also try adding country explicitly
            k2 = base_kwargs.copy()
            k2["country"] = DEFAULT_MARKET
            variants.append({k: v for k, v in k2.items() if v is not None})

        # 3) neither market nor country
        k3 = {k: v for k, v in base_kwargs.items() if k not in ("market", "country")}
        variants.append({k: v for k, v in k3.items() if v is not None})

        for payload in variants:
            try:
                recs = sp.recommendations(**payload)
                items = recs.get("tracks", []) or []
                for t in items:
                    u = t.get("uri")
                    if u and u not in seen:
                        seen.add(u)
                        bag.append(u)
                if items:
                    return True
            except SpotifyException:
                continue
            except Exception:
                continue
        return False
    
    def _add_from_recs(_kwargs):
        try:
            recs = sp.recommendations(**_kwargs)
            for t in recs.get("tracks", []):
                u = t.get("uri")
                if u and u not in seen:
                    seen.add(u); bag.append(u)
            return True
        except SpotifyException:
            return False
        except Exception:
            return False

    # try full kwargs first
    ok = _add_from_recs(kwargs)

    # fallback 1: drop targets, keep seeds
    if not ok:
        slim = {"limit": kwargs["limit"], "market": DEFAULT_MARKET, **seeds}
        ok = _add_from_recs(slim)

    # fallback 2: if still nothing, try genres only (if any)
    if not ok and seed_genres:
        ok = _add_from_recs({"limit": kwargs["limit"], "market": DEFAULT_MARKET, "seed_genres": seed_genres})

    # ---- guarantee “must include” tracks from each selected artist ----
    if user_artist_ids:
        # Aim for ~60% of playlist from preferred artists (up to 28)
        target_total = max(8, min((n * 3) // 5, 28))
        per_artist = max(2, target_total // max(1, len(user_artist_ids)))

        for aid in user_artist_ids[:5]:
            try:
                res = sp.artist_top_tracks(aid, country=DEFAULT_MARKET)
                taken = 0
                for t in res.get("tracks", []):
                    if taken >= per_artist:
                        break
                    u = t.get("uri")
                    if u and u not in seen:
                        seen.add(u)
                        bag.append(u)
                        taken += 1
            except Exception:
                pass

    # ---- search fallback if list is still short ----
    if len(bag) < n:
        try:
            query_bits = params.get("keywords", []) or candidates or ["best", "mix"]
            q = " ".join(query_bits[:3])
            res = sp.search(q=q, type="track", limit=50, market=DEFAULT_MARKET)
            for t in res.get("tracks", {}).get("items", []):
                u = t.get("uri")
                if u and u not in seen:
                    seen.add(u); bag.append(u)
        except Exception:
            pass

        # >>> NEW: de-SFX & vibe shaping pass <<<
        filtered = _audio_feature_filter(sp, bag, params)

        # If we filtered too aggressively, relax in stages:
        if len(filtered) < int(0.6 * n):
            # allow instrumentals and widen tempo window
            params_relaxed = dict(params)
            params_relaxed["instrumental_ok"] = True
            params_relaxed["vocals_required"] = False
            filtered = _audio_feature_filter(sp, bag, params_relaxed)

        if len(filtered) < int(0.4 * n):
            # last resort: only title/podcast filter
            filtered = [u for u in bag if not _looks_like_sfx(u)]

     # final shaping
    random.shuffle(filtered)
    return filtered[:n]

# --- Debug-only public wrappers (safe to import from main) ---
def allowed_genres_for_debug(sp):
    try:
        s = _allowed_genres(sp)
        if s:
            return sorted(list(s))
        # last-ditch: try both names without cache
        seeds = []
        try:
            if hasattr(sp, "recommendation_genre_seeds"):
                seeds = sp.recommendation_genre_seeds()
            else:
                seeds = sp.recommendations_available_genre_seeds()
        except Exception:
            pass
        if isinstance(seeds, dict):
            seeds = seeds.get("genres", [])
        return sorted(list(seeds or DEFAULT_SEED_GENRES))
    except Exception as e:
        print(f"[genres/debug] {e}")
        return sorted(list(DEFAULT_SEED_GENRES))

def normalize_genres_for_debug(sp, genres):
    try:
        return _normalize_genre_list(sp, genres)
    except Exception:
        return []

# ------------- Images --------------- #
    
# ---------- Genre → Representative artist w/ image ----------

# Hand-picked “obvious” artists so the UI feels right immediately.
# (Spotify artist IDs are stable.)
_CANON = {
    "pop": "06HL4z0CvFAxyc27GXpf02",         # Taylor Swift
    "hip-hop": "3TVXtAsR1Inumwj472S9r4",      # Drake
    "r-n-b": "1Xyo4u8uXC1ZmMpatF05PJ",        # The Weeknd
    "edm": "7CajNmpbOovFoOoasH2HaY",          # Calvin Harris
    "house": "1Cs0zKBU1kc0i8ypK3B9h8a",       # David Guetta
    "dance-pop": "66CXWjxzNUsdJxJ2JdwvnR",    # Ariana Grande
    "reggaeton": "1vyhD5VmyZ7KMfW5gqLgo5",    # J Balvin
    "dancehall": "2wY79sveU1sp5g7SokKOiI",    # Burna Boy (close-enough vibe)
    "ambient": "2BTZIqw0ntH9MvilQ3ewNY",      # Enya
    "electronic": "4tZwfgrHOc3mvqYlEYSvVi",   # Aphex Twin
    "indie-pop": "3e7awlrlDSwF3iM0WBjGMp",    # Tame Impala
    "alt-rock": "5BvJzeQpmsdsFp4HGUYUEx",     # The Strokes
    "soul": "3fMbdgg4jU18AjLCKBhRSm",         # Michael Jackson
    "funk": "7M1FPw29m5FbicYzS2xdpi",         # Bruno Mars
    "trap": "1URnnhqYAYcrqrcwql10ft",         # 21 Savage
}

def _first_image_url(artist_obj: dict) -> str | None:
    imgs = artist_obj.get("images") or []
    return imgs[0]["url"] if imgs else None

def _safe_artist(sp: Spotify, artist_id: str) -> dict | None:
    try:
        return sp.artist(artist_id)
    except Exception:
        return None

def _search_artist_by_genre(sp: Spotify, genre: str) -> dict | None:
    # 1) Direct search by genre tag
    try:
        res = sp.search(q=f'genre:"{genre}"', type="artist", limit=10)
        items = (res.get("artists", {}) or {}).get("items", []) or []
        for a in items:
            if _first_image_url(a):
                return a
    except Exception:
        pass
    # 2) Fallback via recommendations → pick first track’s primary artist
    try:
        recs = sp.recommendations(seed_genres=[genre], limit=20, market="US")
        for t in (recs.get("tracks") or []):
            arts = t.get("artists") or []
            if not arts:
                continue
            a = _safe_artist(sp, arts[0]["id"])
            if a and _first_image_url(a):
                return a
    except Exception:
        pass
    return None

# In-process cache: { "genre": {"id","name","image","url"} }
_GENRE_HERO_CACHE: dict[str, dict] = {}

def get_genre_hero(sp: Spotify, genre: str) -> dict | None:
    g = (genre or "").strip().lower()
    if not g:
        return None
    if g in _GENRE_HERO_CACHE:
        return _GENRE_HERO_CACHE[g]

    # Fast path: use canonical pick if available
    if g in _CANON:
        a = _safe_artist(sp, _CANON[g])
        if a:
            data = {
                "id": a["id"],
                "name": a["name"],
                "image": _first_image_url(a),
                "url": a["external_urls"]["spotify"],
            }
            _GENRE_HERO_CACHE[g] = data
            return data

    # Dynamic search / recs
    a = _search_artist_by_genre(sp, g)
    if a:
        data = {
            "id": a["id"],
            "name": a["name"],
            "image": _first_image_url(a),
            "url": a["external_urls"]["spotify"],
        }
        _GENRE_HERO_CACHE[g] = data
        return data

    return None