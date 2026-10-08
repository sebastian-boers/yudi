# YUDI - Your Discogs Music Roulette

YUDI is a Flask-based web app that lets you discover random music videos from Discogs releases filtered by release year, style, and country.  
Powered by the Discogs API and embedded YouTube videos, it provides a fun way to explore music styles and artists.

- Flask part is vibe coded

---

## Features

- Filter random music videos by Release Year, Style, and Country  
- Clickable links to the album and record label on Discogs  
- Clean, retro-inspired UI with a gem icon and smooth interactions  
- Reroll button to fetch another random video with the same filters  

---

## Getting Started

### Prerequisites

- Python 3.8+  
- Docker (optional, but recommended)  
- Discogs API token (create one at [Discogs Developer Settings](https://www.discogs.com/settings/developers))

### Installation

1. Clone this repository:

```bash
git clone https://github.com/sebastian-boers/yudi.git
cd yudi
```

2. Create a `.env` file in the root folder and add your Discogs API token plus a Flask secret key:

```bash
echo DISCOGS_TOKEN=your_discogs_api_token_here > .env
echo SECRET_KEY=your-secret-key >> .env
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the app:

```bash
python app.py
```

Or use Docker:

Build the image:

```bash
docker build -t discogs-video-app .
```

Run in the foreground (for development and debugging):

```bash
docker run -p 5000:5000 --env-file .env discogs-video-app
```

Run in the background (detached mode):

```bash
docker run -d -p 5000:5000 --env-file .env discogs-video-app
```


5. Open your browser and go to:  
`http://localhost:5000`

---

## Usage

- Enter filters for release year, style, and country (autocomplete available for style and country).
- Click **Get Random Video** to fetch a random music video matching your criteria.
- Use the 🎲 **Re-roll** button to get another video with the same filters.

---

## Project Structure

```
app.py                # Main Flask application
templates/index.html  # HTML page with the UI
static/styles.json    # Styles data for autocomplete
static/countries.json # Countries data for autocomplete
static/gem-icon.png   # Gem icon used in UI
Dockerfile            # Docker container setup
.env                  # Environment variables (not committed to Git)
```

---

## Acknowledgments

- Built with [Flask](https://flask.palletsprojects.com/)  
- Data from [Discogs API](https://www.discogs.com/developers/)  
- Video embedding via [YouTube](https://developers.google.com/youtube)  
- Gem icon by [Flaticon](https://www.flaticon.com/free-icon/slot-machine_247837)

---

## Persistent cache and recent clips

The existing design, filters, autocomplete and player are retained. Recent-clips replay/clear controls are additive. Random selection excludes canonical YouTube IDs remembered by this browser across filter changes; explicit replay deliberately permits a repeat. GET/reload restores the current selection without discovery. History records selected/shown clips, not verified playback, and does not synchronize devices or survive clearing the browser cookie.

Set `YUDI_DATA_DIR` to a private persistent writable directory outside the source checkout. In the homelab this is `/var/lib/yudi`; preserve it and the stable `SECRET_KEY` across releases. SQLite shares cached catalogue data, cache-miss leases, cooldowns and history between workers. No credentials are stored in the database; API credential contexts are represented by a digest.

Defaults (environment variables or Flask configuration):

| Setting | Default |
|---|---|
| `YUDI_REQUEST_BUDGET` / `YUDI_SELECTION_SECONDS` | 20 upstream requests / 15 seconds per selection |
| `YUDI_REQUEST_TIMEOUT` / `YUDI_RETRIES` | 5 seconds / 1 transient retry within the same budget |
| `YUDI_SEARCH_TTL` / `YUDI_RELEASE_TTL` / `YUDI_NO_VIDEO_TTL` | 900 / 3600 / 900 seconds, shortened or disabled by upstream headers |
| `YUDI_CACHE_ENTRIES` | 2000 catalogue entries |
| `YUDI_HISTORY_DAYS` / `YUDI_COOKIE_DAYS` | 90 days |
| `YUDI_HISTORY_DISPLAY` | Latest 100 distinct clips; not the exclusion-set limit |
| `YUDI_MAX_PAGES` / `YUDI_PAGE_ATTEMPTS` | 100 / 10, still bounded by request/time budgets |
| `YUDI_COOLDOWN_SECONDS` / `YUDI_MAX_COOLDOWN_SECONDS` | 60 / 3600; shared 429 cooldown using Retry-After when supplied |

No numeric Discogs quota or catalogue-wide uniformity is assumed. Metadata expiry does not reset repeat exclusions; expired labels are omitted until refreshed. A discovery-budget failure is not catalogue exhaustion and never silently resets history. Private personalized responses use no-store; cached replay needs no upstream call while its release metadata is fresh.

For repeatable Debian13/CPython3.13/Linux-x86_64 installation, use `pip install --require-hashes -r requirements.lock` in a dedicated venv. Other platforms must deliberately resolve their own compatible lock. Run `python -m unittest discover -p 'test_*.py' -v`: tests use real Flask/SQLite with mocked external HTTP, not production credentials. Use Gunicorn for production, not Flask's development server. Docker users must mount a persistent `YUDI_DATA_DIR` and keep signing keys outside the image; the homelab uses native systemd/nginx, not Docker.

## Honorable Mention

This project is inspired by and dedicated to my late friend **Robin Sääf** ([robinsaaf](https://github.com/robinsaaf)),  
who first came up with the music roulette idea.  
His creativity and passion continue to inspire this work.
