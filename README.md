# mood to music 🎵

Tell it how you're feeling, get a playlist built from *your own* Spotify listening history that actually fits the mood — plus a one-line reason for every track.

## What it does

1. **You describe your mood** in plain language ("i feel restless and a little anxious...").
2. **Claude analyzes it** — extracting weighted emotion labels and an overall valence score (0 = very negative, 1 = very positive) — and the UI re-themes itself to match (colors shift from desolate blue to joyful gold depending on valence).
3. **A candidate pool is built from your Spotify data**: top tracks (short/medium/long term), recently played, and tracks from artists related to your top artists — so recommendations are always drawn from music you actually like or are likely to like, never a generic catalog.
4. **Claude picks 8 tracks** from that pool that fit your mood and the time of day, writing a short, casual, texting-a-friend-style reason for each pick.
5. **Save it as a real Spotify playlist** in one click, or generate a shareable "mood card" summarizing the session.
6. **Mood history** is kept per-user in the sidebar so you can look back at past sessions.

## Why it's built this way

- **Recommendations are personalized, not generic.** Rather than querying Spotify's catalog directly, the app builds a personal candidate pool from the user's own listening signals (top tracks across time ranges, recent plays, related-artist discovery), then lets Claude reason over *that* pool. This keeps results feeling like "songs I'd actually pick" rather than algorithmic filler.
- **Structured outputs, not string parsing.** Both the mood analysis and track selection calls to Claude use JSON schema–constrained outputs, so the app never has to regex-parse free text out of a model response.
- **Stateless-friendly OAuth.** Spotify tokens are held in Streamlit session state (not written to disk), with automatic refresh and re-scoping if a stored token is missing a required permission.

## Tech stack

- [Streamlit](https://streamlit.io/) — UI
- [Anthropic Claude](https://www.anthropic.com/claude) — mood analysis & track selection
- [Spotipy](https://spotipy.readthedocs.io/) — Spotify Web API client (OAuth, listening history, playlist creation)

## Running it locally

```bash
git clone https://github.com/caitlina626/mood-to-music.git
cd mood-to-music
pip install -r requirements.txt
```

Create `.streamlit/secrets.toml` with:

```toml
ANTHROPIC_API_KEY = "your-anthropic-api-key"
SPOTIFY_CLIENT_ID = "your-spotify-client-id"
SPOTIFY_CLIENT_SECRET = "your-spotify-client-secret"
SPOTIFY_REDIRECT_URI = "http://localhost:8501"
```

You'll need a [Spotify Developer app](https://developer.spotify.com/dashboard) with `SPOTIFY_REDIRECT_URI` added to its allowed redirect URIs, and requires these scopes: `playlist-modify-public`, `user-read-private`, `user-top-read`, `user-read-recently-played`.

Then run:

```bash
streamlit run app.py
```

## Notes

- Mood history is stored locally per Spotify user ID and is gitignored — nothing personal is committed to this repo.
