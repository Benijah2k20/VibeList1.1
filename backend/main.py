# backend/main.py
import os
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from starlette.middleware.cors import CORSMiddleware
from fastapi import Query
from typing import List


from .ai_engine import analyze_vibe_to_json, generate_playlist_prompt
from .spotify_client import (
    oauth, exchange_code_for_token, get_spotify,
    recommend_tracks, create_playlist, add_tracks, get_genre_hero,
    normalize_genres_for_debug, allowed_genres_for_debug,  # <-- debug wrappers
)

# Allow overriding the frontend URL (useful if you run on a different port/host)
FRONTEND_BASE = os.getenv("FRONTEND_BASE", "http://localhost:3000")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- Basic & health routes --------

@app.get("/")
def root():
    return {"message": "VibeList backend is running!"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/health/ollama")
def health_ollama():
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=3)
        r.raise_for_status()
        return {"ok": True, "ollama": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama not reachable: {e}")

@app.get("/health/spotify")
def health_spotify(username: str | None = None):
    # Simple check: is this user connected?
    if not username:
        return {"ok": True, "connected": False}
    try:
        sp = get_spotify(username)
        me = sp.me()
        return {"ok": True, "connected": True, "user": me.get("id")}
    except Exception:
        return {"ok": True, "connected": False}

# -------- Preview & params --------

@app.post("/playlist")  # text preview for UI
def create_playlist_preview(prompt: str):
    return {"playlist_query": generate_playlist_prompt(prompt)}

@app.post("/vibe/params")  # structured params for recs
def vibe_params(prompt: str):
    return analyze_vibe_to_json(prompt)

# -------- Spotify OAuth (PoC: pass ?username=yourname) --------

@app.get("/spotify/login")
def spotify_login(username: str):
    try:
        if not username:
            raise HTTPException(400, "username required")
        # State can just be the username for PoC
        state = f"{username}"
        url = oauth(state).get_authorize_url()
        return {"auth_url": url}
    except Exception as e:
        # Log full traceback; return message to client
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/spotify/callback")
def spotify_callback(code: str, state: str):
    try:
        username = state.split("-")[0] if "-" in state else state
        exchange_code_for_token(state, code, username)
        # Redirect back to the frontend with a marker
        return RedirectResponse(url=f"{FRONTEND_BASE}?connected={username}")
    except Exception as e:
        # Send error back to frontend so you can display it
        from requests.utils import quote
        return RedirectResponse(url=f"{FRONTEND_BASE}?spotify_error={quote(str(e))}")

# -------- Spotify helper endpoints for the UI --------

@app.get("/spotify/genres")
def spotify_genres(username: str):
    sp = get_spotify(username)

    # Try live list
    genres = allowed_genres_for_debug(sp)

    # If Spotify bounces with 404/intermittent errors, serve a baseline so UI still works
    if not genres:
        genres = [
            # broad & common seeds known to be valid
            "pop","hip-hop","r-n-b","electronic","dance","house","edm","indie-pop","alt-rock",
            "rock","metal","punk","trap","chill","ambient","jazz","soul","blues",
            "country","folk","singer-songwriter","k-pop","latin","afrobeat","reggae",
            "classical","piano","acoustic","psych-rock","emo","grunge","new-wave","synth-pop",
            "drum-and-bass","dubstep","garage","techno","trance","hardstyle"
        ]
    return {"genres": sorted(set(genres))}

# @app.get("/spotify/genres")
# def spotify_genres(username: str):
#     """
#     Fetch Spotify's available recommendation seed genres across Spotipy versions.
#     Tries multiple method names; then raw endpoint; finally a safe fallback list.
#     """
#     sp = get_spotify(username)

#     def _unwrap(x):
#         if isinstance(x, dict):
#             x = x.get("genres", [])
#         return list(x or [])

#     # Try both Spotipy method names across versions
#     try_order = [
#         "recommendations_available_genre_seeds",  # newer spotipy
#         "recommendation_genre_seeds",             # older spotipy
#     ]

#     seeds = []
#     for name in try_order:
#         try:
#             fn = getattr(sp, name, None)
#             if callable(fn):
#                 seeds = _unwrap(fn())
#                 if seeds:
#                     break
#         except Exception:
#             pass

#     # Raw HTTP fallback (works on many builds; ignore if 404s again)
#     if not seeds:
#         try:
#             raw = sp._get("recommendations/available-genre-seeds")
#             seeds = _unwrap(raw)
#         except Exception:
#             seeds = []

#     # Final safety net: curated common seeds so the UI can function
#     if not seeds:
#         seeds = [
#             "pop","hip-hop","r-n-b","trap","indie-pop","indie","alternative","rock","metal",
#             "punk","emo","j-pop","k-pop","edm","electronic","house","techno","trance",
#             "chill","ambient","lo-fi","chillhop","dance","disco","funk","soul","jazz",
#             "blues","country","folk","singer-songwriter","latin","reggaeton","afrobeat",
#             "dancehall","dubstep","drum-and-bass","garage","grime","hardcore","industrial",
#             "classical","soundtracks","acoustic","piano","guitar"
#         ]

#     genres = sorted({g.strip() for g in seeds if isinstance(g, str) and g.strip()})
#     return {"genres": genres}

@app.get("/spotify/search_artists")
def search_artists(username: str, q: str, limit: int = 8):
    sp = get_spotify(username)
    res = sp.search(q=q, type="artist", limit=min(max(limit, 1), 12), market="US")
    artists = []
    for a in res.get("artists", {}).get("items", []):
        img = (a.get("images") or [{}])[0].get("url")
        artists.append({
            "id": a.get("id"),
            "name": a.get("name"),
            "image": img,
            "genres": a.get("genres", []),
            "popularity": a.get("popularity"),
            "url": a.get("external_urls", {}).get("spotify"),
        })
    return {"artists": artists}

@app.get("/spotify/genre_hero")
def spotify_genre_hero(username: str, genre: str):
    try:
        sp = get_spotify(username)
        hero = get_genre_hero(sp, genre)
        if not hero:
            raise HTTPException(404, f"No hero found for genre '{genre}'")
        return hero
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch genre hero: {e}")

@app.get("/spotify/genre_heroes")
def spotify_genre_heroes(username: str, genres: str):
    """
    Batch version: genres is a comma-separated list.
    Returns: { "pop": {...}, "edm": {...}, ... }
    """
    try:
        sp = get_spotify(username)
        out = {}
        for g in [x.strip() for x in (genres or "").split(",") if x.strip()]:
            hero = get_genre_hero(sp, g)
            if hero:
                out[g] = hero
        if not out:
            raise HTTPException(404, "No heroes found for provided genres")
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch genre heroes: {e}")

# -------- Create the real playlist --------

# @app.post("/playlist/create")
# def playlist_create(prompt: str, username: str, public: bool = False):
#     try:
#         if not prompt.strip():
#             raise HTTPException(400, "Prompt is required")
#         # 1) prompt -> JSON params via AI
#         params = analyze_vibe_to_json(prompt)

#         # 2) recommendations
#         sp = get_spotify(username)  # raises if not connected
#         me = sp.me()
#         uris = recommend_tracks(sp, params, n=40)
#         if not uris:
#             raise HTTPException(400, "No tracks found for parameters")

#         # 3) create & fill playlist
#         pl_id = create_playlist(
#             sp,
#             me["id"],
#             name=f"VibeList • {params.get('mood','mix')}",
#             public=public,
#             description=f"Generated by VibeList for: {prompt}",
#         )
#         add_tracks(sp, pl_id, uris)

#         # 4) return shareable URL
#         url = sp.playlist(pl_id, fields="external_urls.spotify")["external_urls"]["spotify"]
#         return {"playlist_url": url, "count": len(uris), "params": params}

#     except HTTPException:
#         raise
#     except Exception as e:
#         # Safety net so FE always gets a clear message
#         raise HTTPException(status_code=500, detail=f"Playlist creation failed: {e}")
@app.post("/playlist/create")
def playlist_create(
    prompt: str,
    username: str,
    public: bool = False,
    genres: str | None = None,
    artist_ids: str | None = None,
    energy: float | None = None,   # ✅ NEW
    limit: int = Query(15, ge=1, le=50),   # ✅ default 15, clamp 1–50
):
    try:
        if not prompt.strip():
            raise HTTPException(400, "Prompt is required")

        # analyze the user's vibe prompt → structured parameters
        params = analyze_vibe_to_json(prompt)

        # ✅ apply user-selected energy target
        if energy is not None:
            try:
                e = max(0.0, min(1.0, float(energy)))
                params["energy_range"] = [e, e]
            except Exception:
                pass

        # ✅ handle optional user steering (genres, artists)
        user_genres = [g.strip() for g in (genres or "").split(",") if g.strip()]
        user_artist_ids = [a.strip() for a in (artist_ids or "").split(",") if a.strip()]
        if user_genres:
            params["user_genres"] = user_genres
        if user_artist_ids:
            params["user_artist_ids"] = user_artist_ids

        # ✅ authenticate and fetch Spotify client
        sp = get_spotify(username)
        me = sp.me()

        # ✅ limit to 15 songs (or user-specified within 1–50)
        uris = recommend_tracks(sp, params, n=limit)
        if not uris:
            raise HTTPException(400, "No tracks found for parameters")

        # ✅ create the playlist
        pl_id = create_playlist(
            sp,
            me["id"],
            name=f"VibeList • {params.get('mood', 'mix')}",
            public=public,
            description=f"Generated by VibeList for: {prompt}",
        )

        # ✅ add up to the same number of tracks (limit)
        add_tracks(sp, pl_id, uris[:limit])

        # ✅ fetch and return playlist URL
        url = sp.playlist(pl_id)["external_urls"]["spotify"]
        return {"playlist_url": url, "count": min(len(uris), limit), "params": params}

    except HTTPException:
        raise
    except Exception as e:
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Playlist creation failed: {e}")
# -------- Debug helpers (optional) --------

@app.get("/debug/normalize")
def debug_normalize(prompt: str, username: str = "benijah"):
    # Requires the user to be connected first (token in memory)
    sp = get_spotify(username)
    params = analyze_vibe_to_json(prompt)
    seeds = normalize_genres_for_debug(sp, params.get("genre_candidates") or params.get("genres", []))
    allowed_sample = allowed_genres_for_debug(sp)[:30]
    return {"raw_params": params, "normalized_seeds": seeds, "allowed_sample": allowed_sample}

@app.get("/spotify/whoami")
def spotify_whoami(username: str):
    sp = get_spotify(username)
    me = sp.me()
    return {
        "id": me.get("id"),
        "display_name": me.get("display_name"),
        "product": me.get("product"),
        "country": me.get("country"),
    }