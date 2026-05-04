import json
import os
from datetime import datetime
import streamlit as st
import anthropic
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Mood to Music", page_icon="🎵", layout="centered")

def _history_file(user_id: str) -> str:
    safe_id = "".join(c for c in user_id if c.isalnum() or c in "-_")
    return os.path.join(os.path.dirname(__file__), f"mood_history_{safe_id}.json")

# ── Time of day ───────────────────────────────────────────────────────────────
def get_time_of_day() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:  return "morning"
    if 12 <= hour < 17: return "afternoon"
    if 17 <= hour < 21: return "evening"
    return "night"

# ── Valence → visual theme ────────────────────────────────────────────────────
THEMES = [
    (0.15, "#0d1b2a", "#4169E1", "#6495ED"),   # desolate blue
    (0.30, "#1a1033", "#7B68EE", "#9370DB"),   # melancholy purple
    (0.45, "#111827", "#6B7280", "#9CA3AF"),   # grey neutral
    (0.60, "#1a1209", "#B45309", "#D97706"),   # warm amber
    (0.78, "#131a09", "#65A30D", "#84CC16"),   # hopeful green
    (1.01, "#1a1200", "#CA8A04", "#FACC15"),   # joyful gold
]

def theme_for(valence: float) -> tuple[str, str, str]:
    for threshold, bg, accent, light in THEMES:
        if valence < threshold:
            return bg, accent, light
    return THEMES[-1][1], THEMES[-1][2], THEMES[-1][3]

def inject_theme(valence: float):
    bg, accent, light = theme_for(valence)
    st.markdown(f"""
    <style>
    .stApp {{ background: linear-gradient(135deg, {bg} 0%, #0e1117 100%); }}
    h1, h2, h3 {{ color: {light} !important; }}
    .stProgress > div > div {{ background-color: {accent} !important; }}
    div[data-testid="stButton"] > button[kind="primary"] {{
        background-color: {accent} !important;
        border-color: {accent} !important;
        color: #fff !important;
    }}
    </style>""", unsafe_allow_html=True)

# ── Mood history helpers ──────────────────────────────────────────────────────
def load_history(user_id: str) -> list[dict]:
    path = _history_file(user_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(user_id: str, entry: dict):
    history = load_history(user_id)
    history.insert(0, entry)
    history = history[:30]
    with open(_history_file(user_id), "w") as f:
        json.dump(history, f, indent=2)

# ── Spotify OAuth helpers ─────────────────────────────────────────────────────
SCOPE = "playlist-modify-public user-read-private user-top-read user-read-recently-played"

def get_oauth() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=st.secrets["SPOTIFY_CLIENT_ID"],
        client_secret=st.secrets["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=st.secrets["SPOTIFY_REDIRECT_URI"],
        scope=SCOPE,
        cache_handler=spotipy.cache_handler.MemoryCacheHandler(
            token_info=st.session_state.get("spotify_token")
        ),
        show_dialog=False,
    )

def get_spotify() -> spotipy.Spotify:
    return spotipy.Spotify(auth_manager=get_oauth())

def handle_oauth_callback():
    code = st.query_params.get("code")
    if code and "spotify_token" not in st.session_state:
        oauth = get_oauth()
        token = oauth.get_access_token(code, as_dict=True, check_cache=False)
        st.session_state["spotify_token"] = token
        st.query_params.clear()
        st.rerun()

# ── Mood analysis ─────────────────────────────────────────────────────────────
MOOD_SCHEMA = {
    "type": "object",
    "properties": {
        "emotions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["label", "score"],
                "additionalProperties": False,
            },
        },
        "valence": {"type": "number"},
    },
    "required": ["emotions", "valence"],
    "additionalProperties": False,
}

def analyze_mood(mood_text: str, time_of_day: str) -> dict:
    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system="""You are a music mood analyst. Given a mood description and time of day,
return JSON with: emotions (up to 5 labels + scores summing to ~1) and
valence (0 = very negative, 1 = very positive).""",
        messages=[{"role": "user", "content": f"Mood: {mood_text}\nTime of day: {time_of_day}"}],
        output_config={"format": {"type": "json_schema", "schema": MOOD_SCHEMA}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)

# ── Pool building ─────────────────────────────────────────────────────────────
def _track_dict(item: dict) -> dict:
    return {
        "name": item["name"],
        "artist": ", ".join(a["name"] for a in item["artists"]),
        "album_art": item["album"]["images"][0]["url"] if item["album"]["images"] else None,
        "uri": item["uri"],
        "external_url": item["external_urls"]["spotify"],
    }

def _fetch_pool(sp: spotipy.Spotify) -> list[dict]:
    seen: set[str] = set()
    pool: list[dict] = []

    # Own top tracks across all time ranges
    for time_range in ("short_term", "medium_term", "long_term"):
        try:
            for item in sp.current_user_top_tracks(limit=50, time_range=time_range)["items"]:
                if item["uri"] not in seen:
                    seen.add(item["uri"])
                    pool.append(_track_dict(item))
        except Exception:
            pass

    # Recently played
    try:
        for entry in sp.current_user_recently_played(limit=50)["items"]:
            item = entry["track"]
            if item["uri"] not in seen:
                seen.add(item["uri"])
                pool.append(_track_dict(item))
    except Exception:
        pass

    # Related-artist discovery
    top_artist_ids: list[str] = []
    try:
        top_artist_ids = [a["id"] for a in
                          sp.current_user_top_artists(limit=10, time_range="medium_term")["items"]]
    except Exception:
        pass

    related_ids: set[str] = set()
    for aid in top_artist_ids[:5]:
        try:
            for a in sp.artist_related_artists(aid)["artists"][:4]:
                related_ids.add(a["id"])
        except Exception:
            pass

    for aid in list(related_ids)[:15]:
        try:
            for item in sp.artist_top_tracks(aid, country="US")["tracks"][:5]:
                if item["uri"] not in seen:
                    seen.add(item["uri"])
                    pool.append(_track_dict(item))
        except Exception:
            pass

    return pool

# ── Claude track selection ────────────────────────────────────────────────────
SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["selections"],
    "additionalProperties": False,
}

def _claude_select(
    pool: list[dict], analysis: dict, mood_text: str, time_of_day: str
) -> list[dict]:
    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    emotions = ", ".join(
        f"{e['label']} ({int(e['score']*100)}%)" for e in analysis.get("emotions", [])
    )
    track_list = "\n".join(
        f"{i+1}. {t['name']} — {t['artist']}" for i, t in enumerate(pool)
    )
    prompt = f"""The person says: "{mood_text}"
Time of day: {time_of_day}
Emotions: {emotions}
Valence: {int(analysis.get('valence', 0.5) * 100)}% positive

Pick exactly 8 tracks from the list that fit this mood and time of day.
For each, write a short reason (1 sentence, max 12 words) in a casual, lowercase, texting-a-friend tone.
Be specific to the song — reference the vibe, lyrics, or feel.
No AI-speak, no "this track", no formal language. Just say it like you mean it.
Vary the artists.

Examples of the tone:
- "literally made for crying at 2am"
- "the chorus hits so hard when you're in your feelings"
- "perfect amount of angry without going full breakdown"
- "this one just gets it"

{track_list}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system="You write short, casual music rec captions — like texting a friend, lowercase, no fluff. Return JSON with selections: exactly 8 objects each with index (1-based) and reason (one casual sentence, max 12 words).",
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": SELECT_SCHEMA}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["selections"][:8]

def get_recommendations(
    sp: spotipy.Spotify, analysis: dict, mood_text: str, time_of_day: str
) -> list[dict]:
    pool = _fetch_pool(sp)
    if not pool:
        raise ValueError("No listening history found — listen to more music on Spotify first!")
    selections = _claude_select(pool, analysis, mood_text, time_of_day)
    tracks = []
    for s in selections:
        i = s["index"]
        if 1 <= i <= len(pool):
            t = dict(pool[i - 1])
            t["reason"] = s["reason"]
            tracks.append(t)
    return tracks

# ── Playlist creation ─────────────────────────────────────────────────────────
def create_playlist(sp: spotipy.Spotify, tracks: list[dict], mood_text: str) -> str:
    user_id = sp.current_user()["id"]
    snippet = mood_text[:40].strip()
    name = f"Mood: {snippet}{'…' if len(mood_text) > 40 else ''}"
    playlist = sp.user_playlist_create(user=user_id, name=name, public=True,
                                        description="Created by Mood to Music 🎵")
    sp.playlist_add_items(playlist["id"], [t["uri"] for t in tracks])
    return playlist["external_urls"]["spotify"]

# ── Emotion pills ─────────────────────────────────────────────────────────────
EMOTION_COLORS = {
    "happy": "#FFD700", "joy": "#FFD700", "excited": "#FF6B35", "energetic": "#FF6B35",
    "calm": "#87CEEB", "peaceful": "#87CEEB", "relaxed": "#98FB98",
    "sad": "#6495ED", "melancholy": "#6495ED", "sadness": "#6495ED",
    "angry": "#FF4444", "anger": "#FF4444", "frustrated": "#FF8C00",
    "anxious": "#DDA0DD", "anxiety": "#DDA0DD", "nervous": "#DDA0DD",
    "nostalgic": "#F4A460", "longing": "#9370DB", "despair": "#483D8B",
    "vulnerability": "#C8A2C8", "bored": "#A9A9A9", "confused": "#D3D3D3",
    "contentment": "#90EE90", "surprise": "#FFA07A",
}

def emotion_color(label: str) -> str:
    return EMOTION_COLORS.get(label.lower(), "#B0C4DE")

def render_emotion_pills(emotions: list[dict]):
    html = "".join(
        f'<span style="background:{emotion_color(e["label"])};color:#111;padding:4px 12px;'
        f'border-radius:20px;margin:3px;display:inline-block;font-size:0.85rem;font-weight:600;">'
        f'{e["label"]} {int(e["score"]*100)}%</span>'
        for e in emotions
    )
    st.markdown(html, unsafe_allow_html=True)

# ── Shareable card ────────────────────────────────────────────────────────────
def render_share_card(mood_text: str, analysis: dict, tracks: list[dict], time_of_day: str):
    _, accent, light = theme_for(analysis.get("valence", 0.5))
    emotions = analysis.get("emotions", [])
    pill_html = "".join(
        f'<span style="background:{emotion_color(e["label"])};color:#111;padding:3px 10px;'
        f'border-radius:20px;margin:2px;display:inline-block;font-size:0.75rem;font-weight:600;">'
        f'{e["label"]}</span>'
        for e in emotions[:4]
    )
    track_rows = "".join(
        f'<div style="padding:2px 0;font-size:0.8rem;color:#ccc;">'
        f'<span style="color:{light};font-weight:600;">{t["name"]}</span> — {t["artist"]}</div>'
        for t in tracks[:8]
    )
    now = datetime.now().strftime("%B %d, %Y · %I:%M %p")
    card_html = f"""
    <div style="background:linear-gradient(135deg,#1a1a2e 0%,#0e1117 100%);
                border:1px solid {accent};border-radius:16px;padding:24px;
                font-family:sans-serif;max-width:520px;margin:0 auto;">
      <div style="font-size:0.7rem;color:{accent};letter-spacing:2px;
                  text-transform:uppercase;margin-bottom:8px;">🎵 Mood to Music</div>
      <div style="font-size:1.1rem;color:#fff;font-style:italic;
                  margin-bottom:12px;">"{mood_text[:80]}{"…" if len(mood_text)>80 else ""}"</div>
      <div style="margin-bottom:14px;">{pill_html}</div>
      <div style="border-top:1px solid #333;padding-top:12px;margin-bottom:4px;">{track_rows}</div>
      <div style="font-size:0.65rem;color:#555;margin-top:12px;
                  text-transform:uppercase;letter-spacing:1px;">{now} · {time_of_day}</div>
    </div>"""
    st.markdown(card_html, unsafe_allow_html=True)
    st.caption("📸 Screenshot to share")

# ── Mood history sidebar ──────────────────────────────────────────────────────
def render_history_sidebar(user_id: str):
    history = load_history(user_id)
    with st.sidebar:
        st.markdown("### mood history...")
        if not history:
            st.caption("No sessions yet — your history will appear here.")
            return
        for entry in history:
            ts = entry.get("timestamp", "")[:16].replace("T", " ")
            top_e = entry.get("top_emotion", "?")
            valence = entry.get("valence", 0.5)
            _, accent, _ = theme_for(valence)
            mood_snip = entry.get("mood_text", "")[:40]
            st.markdown(
                f'<div style="border-left:3px solid {accent};padding:6px 10px;'
                f'margin-bottom:8px;border-radius:0 6px 6px 0;background:#111;">'
                f'<div style="font-size:0.7rem;color:#888;">{ts}</div>'
                f'<div style="font-size:0.85rem;color:#ddd;font-style:italic;">"{mood_snip}…"</div>'
                f'<div style="font-size:0.75rem;color:{accent};">{top_e} · {int(valence*100)}% positive</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

# ── Main ──────────────────────────────────────────────────────────────────────
handle_oauth_callback()

time_of_day = get_time_of_day()

st.markdown(f"<h1 style='font-size:3rem;letter-spacing:-1px;'>mood to music.</h1>", unsafe_allow_html=True)
st.caption(f"good {time_of_day}. what's going on?")

# ── Auth gate ─────────────────────────────────────────────────────────────────
if "spotify_token" not in st.session_state:
    st.markdown("---")
    st.subheader("connect spotify to get started")
    if st.button("connect spotify", type="primary"):
        auth_url = get_oauth().get_authorize_url()
        st.markdown(f'<meta http-equiv="refresh" content="0; url={auth_url}">',
                    unsafe_allow_html=True)
    st.stop()

def _token_has_scopes(token: dict, required: str) -> bool:
    granted = set((token.get("scope") or "").split())
    return all(s in granted for s in required.split())

try:
    if not _token_has_scopes(st.session_state["spotify_token"], SCOPE):
        del st.session_state["spotify_token"]
        st.info("one sec, reconnecting so i can see your listening history…")
        st.rerun()
    oauth = get_oauth()
    if oauth.is_token_expired(st.session_state["spotify_token"]):
        st.session_state["spotify_token"] = oauth.refresh_access_token(
            st.session_state["spotify_token"]["refresh_token"]
        )
    sp = get_spotify()
    user_info = sp.current_user()
    st.session_state["user_id"] = user_info["id"]
    render_history_sidebar(user_info["id"])
    st.success(f"Connected as **{user_info['display_name']}** ✅")
except Exception as e:
    st.error(f"Spotify session error: {e}")
    if st.button("Reconnect Spotify"):
        del st.session_state["spotify_token"]
        st.rerun()
    st.stop()

# Apply any cached theme immediately
if "analysis" in st.session_state:
    inject_theme(st.session_state["analysis"].get("valence", 0.5))

st.markdown("---")

# ── Mood input ────────────────────────────────────────────────────────────────
mood_text = st.text_area(
    "how are you feeling rn?",
    placeholder="i feel restless and a little anxious...",
    height=100,
)

analyze_btn = st.button("find my music", type="primary", disabled=not mood_text.strip())

# ── Run analysis ──────────────────────────────────────────────────────────────
if analyze_btn and mood_text.strip():
    with st.spinner("Analysing your mood…"):
        try:
            analysis = analyze_mood(mood_text, time_of_day)
        except Exception as e:
            st.error(f"Mood analysis error: {e}")
            st.stop()

    inject_theme(analysis.get("valence", 0.5))
    st.session_state["analysis"] = analysis
    st.session_state["mood_text"] = mood_text

    with st.spinner("Finding your perfect tracks…"):
        try:
            tracks = get_recommendations(sp, analysis, mood_text, time_of_day)
        except Exception as e:
            st.error(f"Recommendations error: {e}")
            st.stop()

    st.session_state["tracks"] = tracks

    # Save to history
    save_history(st.session_state["user_id"], {
        "timestamp": datetime.now().isoformat(),
        "mood_text": mood_text,
        "time_of_day": time_of_day,
        "top_emotion": analysis["emotions"][0]["label"] if analysis.get("emotions") else "unknown",
        "valence": analysis.get("valence", 0.5),
        "tracks": [f"{t['name']} — {t['artist']}" for t in tracks],
    })
    st.rerun()

# ── Results ───────────────────────────────────────────────────────────────────
if "analysis" in st.session_state and "tracks" in st.session_state:
    analysis = st.session_state["analysis"]
    tracks = st.session_state["tracks"]
    saved_mood = st.session_state.get("mood_text", "")

    st.markdown("---")
    st.subheader("detected emotions...")
    render_emotion_pills(analysis.get("emotions", []))

    st.subheader("overall valence...")
    valence = float(analysis.get("valence", 0.5))
    st.progress(valence, text=f"{int(valence * 100)}% positive")
    st.caption("valence = how positive or negative your mood is. 0% is really low, 100% is really good.")

    st.markdown("---")
    st.subheader("recommended tracks...")

    for i in range(0, len(tracks), 2):
        cols = st.columns(2)
        for j, col in enumerate(cols):
            if i + j < len(tracks):
                track = tracks[i + j]
                with col:
                    if track["album_art"]:
                        st.image(track["album_art"], width=180)
                    st.markdown(
                        f"**[{track['name']}]({track['external_url']})**  \n"
                        f"{track['artist']}"
                    )
                    if track.get("reason"):
                        st.caption(track["reason"])

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("💚 Save to Spotify as playlist"):
            with st.spinner("Creating playlist…"):
                try:
                    url = create_playlist(sp, tracks, saved_mood)
                    st.success(f"Playlist created! [Open in Spotify]({url})")
                except Exception as e:
                    st.error(f"Could not create playlist: {e}")
    with col2:
        if st.button("📸 Share this session"):
            st.session_state["show_card"] = True

    if st.session_state.get("show_card"):
        st.markdown("---")
        render_share_card(saved_mood, analysis, tracks, time_of_day)
