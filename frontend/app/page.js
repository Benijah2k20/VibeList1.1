"use client";

import { useEffect, useState, useMemo } from "react";
import Image from "next/image";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

// --- helpers ---
async function connectSpotify(username) {
  try {
    const res = await fetch(
      `${API_BASE}/spotify/login?username=${encodeURIComponent(username)}`
    );
    const { auth_url } = await res.json();
    window.location.href = auth_url;
  } catch (err) {
    alert("Error connecting to Spotify");
    console.error(err);
  }
}
function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export default function Home() {
  const [prompt, setPrompt] = useState("");
  const [username, setUsername] = useState("benijah");
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");

  // Steering state
  const [allGenres, setAllGenres] = useState([]);
  const [genres, setGenres] = useState([]); // selected
  const [artistQuery, setArtistQuery] = useState("");
  const [artistResults, setArtistResults] = useState([]); // [{id,name,image}]
  const [artistIds, setArtistIds] = useState([]);
  const [artistNames, setArtistNames] = useState([]);
  const [artistImages, setArtistImages] = useState([]); // parallel to names/ids

  // Genre Images
  const [genreHeroes, setGenreHeroes] = useState({});

  // sliders
  const [energy, setEnergy] = useState(0.5);

  // detect ?connected=username
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    if (connected) setUsername(connected);
  }, []);

  // load allowed Spotify seed genres
  useEffect(() => {
    if (!username) return;
    (async () => {
      try {
        const r = await fetch(
          `${API_BASE}/spotify/genres?username=${encodeURIComponent(username)}`
        );
        if (!r.ok) return;
        const data = await r.json();
        // unique + sorted for nicer grid
        const list = Array.from(new Set(data.genres || [])).sort((a, b) =>
          a.localeCompare(b)
        );
        setAllGenres(list);

        (async () => {
        if (!list.length) return;
        // batch in chunks of ~12 to avoid long URLs
        const chunks = [];
        for (let i = 0; i < list.length; i += 12) chunks.push(list.slice(i, i + 12));
        const agg = {};
        for (const part of chunks) {
          const qs = new URLSearchParams({
            username,
            genres: part.join(","),
          }).toString();
          const r = await fetch(`${API_BASE}/spotify/genre_heroes?${qs}`);
          if (r.ok) {
            const data = await r.json();
            Object.assign(agg, data); // { genre: {id,name,image,url}, ... }
          }
        }
        setGenreHeroes(agg);
      })();
      } catch {
        // ignore
      }
    })();
  }, [username]);

  async function handleGenerate(e) {
    e.preventDefault();
    setError("");
    setPreview("");
    if (!prompt.trim()) return;

    setLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/playlist?prompt=${encodeURIComponent(prompt)}`,
        { method: "POST" }
      );
      if (!res.ok) throw new Error(`Backend error: ${res.status}`);
      const data = await res.json();
      setPreview(data.playlist_query || "(no response)");
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  async function searchArtists(q) {
    setArtistQuery(q);
    if (!q.trim()) {
      setArtistResults([]);
      return;
    }
    try {
      const r = await fetch(
        `${API_BASE}/spotify/search_artists?username=${encodeURIComponent(
          username
        )}&q=${encodeURIComponent(q)}`
      );
      if (!r.ok) return setArtistResults([]);
      const data = await r.json();
      // normalize shape; backend already includes image
      const items = (data.artists || []).map((a) => ({
        id: a.id,
        name: a.name,
        image: a.image || null,
      }));
      setArtistResults(items);
    } catch {
      setArtistResults([]);
    }
  }
  const debouncedSearch = useMemo(
    () =>
      debounce((q) => {
        if (!q || q.trim().length < 2) {
          setArtistResults([]);
          return;
        }
        searchArtists(q);
      }, 300),
    [username]
  );

  function addArtist(a) {
    if (!artistIds.includes(a.id)) {
      setArtistIds((s) => [...s, a.id]);
      setArtistNames((s) => [...s, a.name]);
      setArtistImages((s) => [...s, a.image || null]);
    }
    setArtistQuery("");
    setArtistResults([]);
  }
  function removeArtistAt(i) {
    setArtistIds((ids) => ids.filter((_, idx) => idx !== i));
    setArtistNames((ns) => ns.filter((_, idx) => idx !== i));
    setArtistImages((ims) => ims.filter((_, idx) => idx !== i));
  }

  async function createPlaylistWithSteering({ prompt, username, artistIds, genres, energy }) {
    const qs = new URLSearchParams({
      username,
      prompt,
      artist_ids: (artistIds || []).join(","),
      genres: (genres || []).join(","),
      limit: String(15), // current default; change if you add a slider
    });
    if (energy != null) qs.set("energy", String(energy));
    const r = await fetch(`${API_BASE}/playlist/create?${qs.toString()}`, {
      method: "POST",
    });
    const data = await r.json();
    if (data.playlist_url) {
      window.open(data.playlist_url, "_blank");
    } else {
      alert(data.detail || "No playlist created.");
    }
  }

  const selectedGenreSet = new Set(genres);
  function toggleGenre(g) {
    setGenres((curr) =>
      curr.includes(g) ? curr.filter((x) => x !== g) : [...curr, g]
    );
  }

  return (
    <main className="flex min-h-screen flex-col items-center p-8 gap-6">
      <h1 className="text-3xl font-bold">VibeList — AI Playlist Creator</h1>

      <div className="w-full max-w-4xl flex flex-col gap-6">
        {/* Vibe text */}
        <input
          className="border rounded px-3 py-2"
          placeholder="Describe your vibe (e.g., rainy night drive)…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />

        {/* Genres: image grid */}
        <section>
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">Pin some genres</label>
            {!!genres.length && (
              <button
                className="text-xs text-gray-500 underline"
                onClick={() => setGenres([])}
              >
                Clear
              </button>
            )}
          </div>

          {/* Selected chips with thumbnails */}
          {!!genres.length && (
            <div className="mt-3 flex flex-wrap gap-2">
              {genres.map((g) => (
                <span
                  key={g}
                  className="inline-flex items-center gap-2 pl-1 pr-2 py-1 bg-blue-50 rounded-full border border-blue-200"
                >
                  <span className="relative w-6 h-6 rounded-full overflow-hidden border border-blue-200">
                    {genreHeroes[g]?.image ? (
                      <Image
                        src={genreHeroes[g].image}
                        alt={`${g} artist`}
                        fill
                        sizes="(max-width:768px) 50vw, 25vw"
                        className="object-cover"
                      />
                    ) : (
                      <div className="absolute inset-0 bg-gradient-to-br from-gray-200 to-gray-300" />
                    )}

                    {genreHeroes[g]?.name && (
                      <div className="absolute top-2 left-2 text-[11px] bg-black/50 text-white px-1.5 py-0.5 rounded">
                        {genreHeroes[g].name}
                      </div>
                    )}
                  </span>
                  <span className="text-sm">{g}</span>
                  <button
                    className="ml-1 text-blue-600"
                    aria-label={`Remove ${g}`}
                    onClick={() =>
                      setGenres((list) => list.filter((x) => x !== g))
                    }
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Genre card grid */}
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {allGenres.map((g) => {
              const selected = selectedGenreSet.has(g);
              const hero = genreHeroes[g];
              return (
                <button
                  key={g}
                  onClick={() => toggleGenre(g)}
                  className={`relative h-28 rounded-xl overflow-hidden shadow-sm ring-1 ring-gray-200 text-left group ${
                    selected ? "outline outline-2 outline-blue-500" : ""
                  }`}
                >
                 {genreHeroes[g]?.image ? (
                   <Image
                     src={genreHeroes[g].image}
                     alt={`${g} hero`}
                     fill
                     sizes="(max-width: 768px) 50vw, 25vw"
                     className="object-cover object-center"
                   />
                 ) : (
                   <div className="w-full h-full bg-gray-200" />
                 )}
                  <div className="absolute inset-0 bg-black/35 group-hover:bg-black/25 transition" />
                  <div className="absolute bottom-0 left-0 right-0 p-2 text-white text-sm font-medium">
                    {g}
                  </div>
                  {selected && (
                    <div className="absolute top-2 right-2 bg-white/90 text-blue-600 text-xs px-2 py-0.5 rounded-full">
                      Selected
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </section>

        {/* Artist preference */}
        <section>
          <label className="text-sm font-medium">Prefer specific artists</label>
          <input
            className="mt-1 w-full border rounded px-3 py-2"
            placeholder="Search artists…"
            value={artistQuery}
            onChange={(e) => {
              const q = e.target.value;
              setArtistQuery(q);
              debouncedSearch(q);
            }}
          />

          {/* Results list with avatars */}
          {!!artistResults.length && (
            <div className="border rounded mt-2 max-h-72 overflow-auto divide-y">
              {artistResults.map((a) => (
                <div
                  key={a.id}
                  className="px-3 py-2 hover:bg-gray-50 cursor-pointer flex items-center gap-3"
                  onClick={() => addArtist(a)}
                >
                  <div className="relative w-8 h-8 rounded overflow-hidden bg-gray-200 flex-shrink-0">
                    {a.image ? (
                      <Image
                        src={a.image}
                        alt={`${a.name} avatar`}
                        fill
                        sizes="32px"
                        className="object-cover"
                      />
                    ) : (
                      <div className="w-full h-full" />
                    )}
                  </div>
                  <div className="text-sm">{a.name}</div>
                </div>
              ))}
            </div>
          )}

          {/* Selected artists with avatars */}
          {!!artistNames.length && (
            <div className="mt-2 flex flex-wrap gap-2">
              {artistNames.map((n, i) => (
                <span
                  key={`${artistIds[i]}-${i}`}
                  className="inline-flex items-center gap-2 pl-1 pr-2 py-1 bg-green-50 rounded-full border border-green-200"
                >
                  <span className="relative w-6 h-6 rounded-full overflow-hidden bg-gray-200">
                    {artistImages[i] ? (
                      <Image
                        src={artistImages[i]}
                        alt={`${n} avatar`}
                        fill
                        sizes="24px"
                        className="object-cover object-center"
                      />
                    ) : (
                      <div className="w-full h-full" />
                    )}
                  </span>
                  <span className="text-sm">{n}</span>
                  <button
                    className="ml-1 text-green-700"
                    aria-label={`Remove ${n}`}
                    onClick={() => removeArtistAt(i)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </section>

        {/* Vibe steering sliders */}
        <div className="grid grid-cols-1 gap-4">
          <div>
            <label className="text-sm font-medium flex justify-between">
              <span>Energy</span>
              <span className="text-gray-400">{energy.toFixed(2)}</span>
            </label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={energy}
              onChange={(e) => setEnergy(parseFloat(e.target.value))}
              className="w-full"
            />
            <p className="text-xs text-gray-400 mt-1">0 = mellow, 1 = intense</p>
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-wrap gap-3">
          <button
            onClick={handleGenerate}
            className="px-4 py-2 rounded bg-blue-600 text-white disabled:opacity-60"
            disabled={loading}
          >
            {loading ? "Generating…" : "Preview Vibe"}
          </button>

          <button
            onClick={() => connectSpotify(username)}
            className="px-4 py-2 bg-green-600 text-white rounded"
          >
            Connect Spotify
          </button>

          <button
            onClick={() =>
              createPlaylistWithSteering({
                prompt,
                username,
                artistIds,
                genres,
                energy,
              })
            }
            className="px-4 py-2 bg-indigo-600 text-white rounded"
          >
            Create Playlist
          </button>
        </div>
      </div>

      {error && (
        <div className="max-w-4xl w-full border border-red-300 bg-red-50 text-red-700 p-3 rounded">
          {error}
        </div>
      )}

      {preview && (
        <div className="max-w-4xl w-full border bg-gray-50 p-4 rounded whitespace-pre-wrap">
          {preview}
        </div>
      )}
    </main>
  );
}