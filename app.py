import os
import random
import requests
from flask import Flask, render_template, request, session

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'supersecret')

DISCOGS_TOKEN = os.getenv('DISCOGS_TOKEN')
HEADERS = {'User-Agent': 'DiscogsVideoApp/1.0'}

def discogs_api_to_public_url(api_url: str) -> str:
    import re
    if not api_url:
        return None
    m = re.match(r'https://api\.discogs\.com/(labels|releases|masters)/(\d+)', api_url)
    if not m:
        return None
    kind = m.group(1)
    id_num = m.group(2)
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
        else:
            year = request.form.get('year')
            style = request.form.get('style')
            country = request.form.get('country')
            session['year'] = year
            session['style'] = style
            session['country'] = country

        params = {
            'token': DISCOGS_TOKEN,
            'type': 'release',
            'per_page': 100,
        }
        if year:
            params['year'] = year
        if style:
            params['style'] = style
        if country:
            params['country'] = country

        res = requests.get('https://api.discogs.com/database/search', params=params, headers=HEADERS)

        if res.status_code != 200:
            video_data = {'error': f"Discogs API error: {res.status_code} - {res.reason}"}
        else:
            results = res.json().get('results', [])
            if not results:
                video_data = {'error': "No releases found with those filters."}
            else:
                random.shuffle(results)
                for release in results:
                    rel_res = requests.get(release['resource_url'], headers=HEADERS)
                    if rel_res.status_code != 200:
                        continue
                    data = rel_res.json()
                    videos = data.get('videos', [])
                    if videos:
                        chosen_video = random.choice(videos)

                        video_data = {
                            'title': data.get('title'),
                            'video_title': chosen_video.get('title'),
                            'video_url': chosen_video.get('uri'),
                            'thumb': release.get('thumb'),
                            'label_name': None,
                            'label_url': None,
                            'release_url': None,
                        }

                        # Link to master release if available, else release URL
                        master_url = data.get('master_url')
                        if master_url:
                            video_data['release_url'] = discogs_api_to_public_url(master_url)
                        else:
                            video_data['release_url'] = discogs_api_to_public_url(release.get('resource_url'))

                        # Label info
                        labels = data.get('labels', [])
                        if labels:
                            video_data['label_name'] = labels[0].get('name')
                            video_data['label_url'] = discogs_api_to_public_url(labels[0].get('resource_url'))

                        break
                else:
                    video_data = {'error': "No releases with videos found for these filters."}

    return render_template('index.html', video=video_data)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
