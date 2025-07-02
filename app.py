import os
import random
import requests
import re
from flask import Flask, render_template, request, session

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'supersecret')

DISCOGS_TOKEN = os.getenv('DISCOGS_TOKEN')
HEADERS = {'User-Agent': 'DiscogsVideoApp/1.0'}

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
            'token': DISCOGS_TOKEN,
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
        res = requests.get('https://api.discogs.com/database/search', params=count_params, headers=HEADERS)

        if res.status_code != 200:
            video_data = {'error': f"Discogs API error: {res.status_code} - {res.reason}"}
        else:
            total_results = res.json().get('pagination', {}).get('items', 0)

            if total_results == 0:
                video_data = {'error': "No releases found with those filters."}
            else:
                max_pages = min(10, (total_results // 100) + 1)
                random_page = random.randint(1, max_pages)

                # Step 2: get random page of releases
                page_params = base_params.copy()
                page_params['page'] = random_page
                res = requests.get('https://api.discogs.com/database/search', params=page_params, headers=HEADERS)

                if res.status_code != 200:
                    video_data = {'error': f"Discogs API error: {res.status_code} - {res.reason}"}
                else:
                    results = res.json().get('results', [])
                    random.shuffle(results)

                    for release in results:
                        rel_res = requests.get(release['resource_url'], headers=HEADERS)
                        if rel_res.status_code != 200:
                            continue

                        data = rel_res.json()
                        videos = data.get('videos', [])
                        if not videos:
                            continue

                        random.shuffle(videos)
                        for vid in videos:
                            uri = vid.get('uri')
                            if uri and uri != last_video_url:
                                chosen_video = vid
                                session['last_video_url'] = uri

                                video_data = {
                                    'title': data.get('title'),
                                    'video_title': chosen_video.get('title'),
                                    'video_url': uri,
                                    'thumb': release.get('thumb'),
                                    'label_name': None,
                                    'label_url': None,
                                    'release_url': None,
                                }

                                master_url = data.get('master_url')
                                if master_url:
                                    video_data['release_url'] = discogs_api_to_public_url(master_url)
                                else:
                                    video_data['release_url'] = discogs_api_to_public_url(release.get('resource_url'))

                                labels = data.get('labels', [])
                                if labels:
                                    video_data['label_name'] = labels[0].get('name')
                                    video_data['label_url'] = discogs_api_to_public_url(labels[0].get('resource_url'))

                                break  # video found
                        if video_data:
                            break
                    else:
                        video_data = {'error': "No new videos found — try again."}

    return render_template('index.html', video=video_data)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
