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
- Discogs API token (create one at https://www.discogs.com/settings/developers)

### Installation

1. Clone this repository:

```bash
git clone https://github.com/sebastian-boers/yudi.git
cd yudi```

Create a .env file in the root folder and add your Discogs API token:

DISCOGS_TOKEN=your_discogs_api_token_here

Install dependencies:

bash
Copy
Edit
pip install -r requirements.txt
Run the app:

bash
Copy
Edit
python app.py
Or use Docker:

bash
Copy
Edit
docker build -t discogs-video-app .
docker run -p 5000:5000 --env-file .env discogs-video-app
Open your browser and go to http://localhost:5000

Usage
Enter filters for release year, style, and country (autocomplete available for style and country).

Click Get Random Video to fetch a random music video matching your criteria.

Use the 🎲 Re-roll button to get another video with the same filters.

Project Structure
app.py — main Flask application

templates/index.html — HTML page with the UI

static/styles.json — styles data for autocomplete

static/countries.json — countries data for autocomplete

static/gem-icon.png — gem icon used in UI

Dockerfile — Docker container setup

.env — environment variables (not committed to Git)

Acknowledgments
Built with Flask

Data from Discogs API

Video embed via YouTube

Gem icon by Flaticon

Honorable Mention
This project is inspired by and dedicated to my late friend Robin Sääf (GitHub: robinsaaf), who first came up with the music roulette idea.
His creativity and passion continue to inspire this work.

License
MIT License © Sebastian Boers
