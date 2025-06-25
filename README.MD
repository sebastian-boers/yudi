# YUDI - Your Discogs Music Roulette

YUDI is a Flask-based web app that lets you discover random music videos from Discogs releases filtered by release year, style, and country.  
Powered by the Discogs API and embedded YouTube videos, it provides a fun way to explore music styles and artists.

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

2. Create a `.env` file in the root folder and add your Discogs API token:

```env
DISCOGS_TOKEN=your_discogs_api_token_here
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

```bash
docker build -t discogs-video-app .
docker run -p 5000:5000 --env-file .env discogs-video-app
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

## Honorable Mention

This project is inspired by and dedicated to my late friend **Robin Sääf** ([robinsaaf](https://github.com/robinsaaf)),  
who first came up with the music roulette idea.  
His creativity and passion continue to inspire this work.

---

## License

MIT License © Sebastian Boers
