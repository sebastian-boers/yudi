

import os
import random
import requests
import re
from flask import Flask, render_template, request, session
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    raise RuntimeError('SECRET_KEY environment variable is required for secure Flask sessions.')
app.secret_key = secret_key

DISCOGS_TOKEN = os.getenv('DISCOGS_TOKEN')
if not DISCOGS_TOKEN:
    raise RuntimeError('DISCOGS_TOKEN environment variable is required.')

HEADERS = {
    'User-Agent': 'DiscogsVideoApp/1.0',
    'Authorization': f'Discogs token={DISCOGS_TOKEN}',
}
DISCOGS_SESSION = requests.Session()
DISCOGS_SESSION.headers.update(HEADERS)
REQUEST_TIMEOUT = 10
MAX_DISCOGS_PAGES = 100

def discogs_api_to_public_url(api_url: str) -> str:
    if not api_url:
        return None
    m = re.match(r'https://api\.discogs\.com/(labels|releases|masters)/(\d+)', api_url)
    if not m:
        return None
    kind, id_num = m.groups()
    if kind == 'labels':
        return f'https://www.discogs.com/label/{id_num}'
    elif kind == 'releases':
        return f'https://www.discogs.com/release/{id_num}'
    elif kind == 'masters':
        return f'https://www.discogs.com/master/{id_num}'
    return None


def discogs_api_request(url: str, params: dict = None):
    try:
        res = DISCOGS_SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        return res.json()
    except requests.RequestException as exc:
        return {'error': f"Discogs API request failed: {exc}"}
    except ValueError:
        return {'error': 'Invalid JSON response from Discogs API.'}


def extract_youtube_embed_url(uri: str) -> str:
    if not uri:
        return None

    short_match = re.match(r'https?://youtu\.be/([^?&/]+)', uri)
    if short_match:
        return f'https://www.youtube.com/embed/{short_match.group(1)}'

    match = re.search(r'(?:v=|embed/)([A-Za-z0-9_-]{11})', uri)
    if match:
        return f'https://www.youtube.com/embed/{match.group(1)}'

    return None


@app.route('/', methods=['GET', 'POST'])
def index():
    video_data = None

    if request.method == 'POST':
        reroll = request.form.get('reroll') == 'true'

        if reroll:
            year = session.get('year')
            style = session.get('style')
            country = session.get('country')
            last_video_url = session.get('last_video_url')
        else:
            year = request.form.get('year', '').strip()
            style = request.form.get('style', '').strip()
            country = request.form.get('country', '').strip()
            session['year'] = year
            session['style'] = style
            session['country'] = country
            session['last_video_url'] = None
            last_video_url = None

        # Base search params
        base_params = {
            'type': 'release',
            'per_page': 100,
        }
        if year:
            base_params['year'] = year
        if style:
            base_params['style'] = style
        if country:
            base_params['country'] = country

        # Step 1: get total count of results
        count_params = base_params.copy()
        count_params['per_page'] = 1
        count_response = discogs_api_request('https://api.discogs.com/database/search', params=count_params)

        if count_response.get('error'):
            video_data = {'error': count_response['error']}
        else:
            total_results = count_response.get('pagination', {}).get('items', 0)

            if total_results == 0:
                video_data = {'error': "No releases found with those filters."}
            else:
                total_pages = (total_results + 99) // 100
                max_pages = min(total_pages, MAX_DISCOGS_PAGES)
                tried_pages = set()
                page_attempts = min(max_pages, 10)

                while len(tried_pages) < page_attempts and not video_data:
                    available_pages = [p for p in range(1, max_pages + 1) if p not in tried_pages]
                    if not available_pages:
                        break
                    page = random.choice(available_pages)
                    tried_pages.add(page)

                    page_params = base_params.copy()
                    page_params['page'] = page
                    page_response = discogs_api_request('https://api.discogs.com/database/search', params=page_params)

                    if page_response.get('error'):
                        video_data = {'error': page_response['error']}
                        break

                    results = page_response.get('results', [])
                    random.shuffle(results)

                    for release in results:
                        release_response = discogs_api_request(release['resource_url'])
                        if release_response.get('error'):
                            continue

                        videos = release_response.get('videos', [])
                        if not videos:
                            continue

                        random.shuffle(videos)
                        for vid in videos:
                            uri = vid.get('uri')
                            embed_url = extract_youtube_embed_url(uri)
                            if not uri or not embed_url:
                                continue
                            if uri == last_video_url:
                                continue

                            session['last_video_url'] = uri
                            video_data = {
                                'title': release_response.get('title'),
                                'video_title': vid.get('title'),
                                'video_url': uri,
                                'video_embed_url': embed_url,
                                'thumb': release.get('thumb'),
                                'label_name': None,
                                'label_url': None,
                                'release_url': None,
                            }

                            master_url = release_response.get('master_url')
                            if master_url:
                                video_data['release_url'] = discogs_api_to_public_url(master_url)
                            else:
                                video_data['release_url'] = discogs_api_to_public_url(release.get('resource_url'))

                            labels = release_response.get('labels', [])
                            if labels:
                                video_data['label_name'] = labels[0].get('name')
                                video_data['label_url'] = discogs_api_to_public_url(labels[0].get('resource_url'))

                            break
                        if video_data:
                            break
                if not video_data:
                    video_data = {'error': "No matching videos found — try broader filters or search again."}

    return render_template('index.html', video=video_data)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')

