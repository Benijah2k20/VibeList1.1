# backend/ai_engine.py
import json, re, requests

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "mistral"  # or your local choice

SCHEMA = {
    "type": "object",
    "properties": {
        "mood": {"type": "string"},
        "scene": {"type": "string"},
        "tempo_bpm": {"type": "integer"},
        "energy_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "valence_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "danceability_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "acousticness_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "genre_candidates": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["mood","tempo_bpm","energy_range","valence_range","danceability_range","acousticness_range","genre_candidates"],
}

PROMPT_TEMPLATE = """You are a music curation assistant. Convert the user's vibe description into JSON **only** (no extra text).
The JSON must follow this schema (numbers in [0,1], tempo 40–220):

{{
  "mood": "<short phrase>",
  "scene": "<optional short context>",
  "tempo_bpm": <int 40-220>,
  "energy_range": [<0-1>, <0-1>],
  "valence_range": [<0-1>, <0-1>],
  "danceability_range": [<0-1>, <0-1>],
  "acousticness_range": [<0-1>, <0-1>],
  "genre_candidates": ["<up to 6 genres>"],
  "keywords": ["<3-8 short search keywords (no artist names unless explicitly requested)>"]
}}

Notes:
- Favor CONCRETE Spotify-like genre names when possible: pop, rock, metal, death-metal, metalcore, hardcore, hip-hop, r-n-b, electronic, house, techno, indie-pop, alternative, ambient, chill, chillhop, lofi, jazz, soul, funk, trap, punk, folk, singer-songwriter, edm, drum-and-bass.
- If the user asks for specific decades/substyles (e.g., "2000s R&B", "hardcore death metal for gym"), reflect that in genre_candidates and tempo/energy/danceability.
- No commentary. Output JSON only.

User vibe: "{vibe}"
"""

def _coerce_ranges(d, key, default=(0.5,0.5)):
    try:
        a,b = d.get(key, default)
        a = float(a); b = float(b)
        a = max(0.0, min(1.0, a)); b = max(0.0, min(1.0, b))
        return [a,b]
    except Exception:
        return list(default)

def _extract_json(text):
    # grab the first {...} block
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0) if m else "{}"

def analyze_vibe_to_json(vibe: str) -> dict:
    prompt = PROMPT_TEMPLATE.format(vibe=vibe.strip())
    body = {"model": MODEL, "prompt": prompt, "temperature": 0.4, "stream": False}
    r = requests.post(OLLAMA_URL, json=body, timeout=60)
    r.raise_for_status()
    raw = r.json().get("response","")
    try:
        data = json.loads(_extract_json(raw))
    except Exception:
        data = {}

    # Coerce and defaults
    out = {
        "mood": data.get("mood") or "mix",
        "scene": data.get("scene") or "",
        "tempo_bpm": max(40, min(220, int(data.get("tempo_bpm") or 100))),
        "energy_range": _coerce_ranges(data, "energy_range", (0.5,0.7)),
        "valence_range": _coerce_ranges(data, "valence_range", (0.4,0.7)),
        "danceability_range": _coerce_ranges(data, "danceability_range", (0.4,0.7)),
        "acousticness_range": _coerce_ranges(data, "acousticness_range", (0.2,0.6)),
        "genre_candidates": data.get("genre_candidates") or [],
        "keywords": data.get("keywords") or [],
    }
    return out

def generate_playlist_prompt(vibe: str) -> str:
    # simple text preview for your current UI
    d = analyze_vibe_to_json(vibe)
    return f"{d['mood']} • tempo≈{d['tempo_bpm']} • genres={', '.join(d['genre_candidates'][:4]) or 'auto'}"