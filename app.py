

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
MAX_RESPONSE_BYTES = 1024 * 1024

# At most two HTTP/parsing jobs per process, including timed-out jobs. No
# unbounded executor queue; only the caller is allowed to write shared state.
import concurrent.futures
import threading
import time
_HTTP_LOCK = threading.Lock()
_HTTP_POOL = None
_HTTP_PID = None
_HTTP_SLOTS = None


def bounded_response(url, params, budget, request_timeout):
    import json
    global _HTTP_POOL, _HTTP_PID, _HTTP_SLOTS
    with _HTTP_LOCK:
        if _HTTP_PID != os.getpid():
            _HTTP_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)
            _HTTP_SLOTS = threading.BoundedSemaphore(2)
            _HTTP_PID = os.getpid()
        pool, slots = _HTTP_POOL, _HTTP_SLOTS
    if not slots.acquire(timeout=max(0, budget.remaining())):
        raise concurrent.futures.TimeoutError()
    headers_seen = []

    def consume():
        response = None
        try:
            remaining = budget.remaining()
            if remaining <= 0:
                raise concurrent.futures.TimeoutError()
            response = DISCOGS_SESSION.get(url, params=params, stream=True,
                timeout=min(request_timeout, remaining), allow_redirects=False)
            status, headers = response.status_code, response.headers.copy()
            headers_seen.append((status, headers, None))
            if status != 200:
                return status, headers, None
            body = bytearray()
            # A large read can wait forever for a full chunk under a drip feed.
            # Yield each byte so the total deadline is checked between arrivals.
            for chunk in response.iter_content(chunk_size=1):
                if budget.remaining() <= 0:
                    raise concurrent.futures.TimeoutError()
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError('Discogs response too large.')
                body.extend(chunk)
            if budget.remaining() <= 0:
                raise concurrent.futures.TimeoutError()
            return status, headers, json.loads(body)
        finally:
            if response is not None:
                response.close()

    try:
        future = pool.submit(consume)
    except BaseException:
        slots.release()
        raise
    future.add_done_callback(lambda _: slots.release())
    try:
        return future.result(timeout=max(0, budget.remaining()))
    except concurrent.futures.TimeoutError:
        future.cancel()
        # Preserve a known 429 even at the deadline, without waiting for JSON.
        # Headers arriving only AFTER caller timeout cannot safely be persisted:
        # the abandoned worker never writes cooldown/cache/history to SQLite.
        if headers_seen and headers_seen[0][0] == 429:
            return headers_seen[0]
        raise

def discogs_api_to_public_url(api_url: str) -> str:
    if not isinstance(api_url, str) or not api_url:
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


def store():
    from storage import Store
    return Store(app.config.get('YUDI_DATA_DIR', os.getenv('YUDI_DATA_DIR', os.path.join(app.instance_path, 'data'))))


def policy(name, default):
    return float(app.config.get(name, os.getenv(name, default)))


class Budget:
    def __init__(self, requests=None, seconds=None):
        import time
        self.left = int(policy('YUDI_REQUEST_BUDGET', 20) if requests is None else requests)
        self.end = time.monotonic() + (policy('YUDI_SELECTION_SECONDS', 15) if seconds is None else seconds)

    def remaining(self):
        import time
        return self.end - time.monotonic()


def discogs_api_request(url: str, params: dict = None, budget=None):
    import json
    import time
    import uuid
    import hashlib
    from email.utils import parsedate_to_datetime
    budget = budget or Budget()
    if not isinstance(url, str) or not re.fullmatch(r'https://api\.discogs\.com/(?:database/search|releases/\d+)', url):
        return {'error': 'Invalid Discogs resource.'}
    scope = hashlib.sha256(DISCOGS_TOKEN.encode()).hexdigest()
    key = json.dumps([scope, url, params or {}], sort_keys=True)
    db = store()
    owner = uuid.uuid4().hex
    while True:
        if budget.remaining() <= 0:
            return {'error': 'Selection deadline reached — No new clip found yet.'}
        cooldown = db.cooling(scope)
        if cooldown:
            return {'error': 'Discogs cooldown — try again later.', 'status': 429, 'retry_after': int(cooldown)+1}
        saved = db.cached(key)
        if saved is not None:
            if budget.remaining() <= 0:
                return {'error': 'Selection deadline reached — No new clip found yet.'}
            return saved
        if db.lease(key, owner, policy('YUDI_SELECTION_SECONDS', 15)+2):
            break
        time.sleep(min(.025, max(0, budget.remaining())))
    try:
        # Recheck after acquiring ownership: another worker may have filled it.
        saved = db.cached(key)
        if saved is not None:
            if budget.remaining() <= 0:
                return {'error': 'Selection deadline reached — No new clip found yet.'}
            return saved
        for attempt in range(int(policy('YUDI_RETRIES', 1))+1):
            if budget.remaining() <= 0:
                return {'error': 'Selection deadline reached — No new clip found yet.'}
            if budget.left <= 0:
                return {'error': 'Request budget reached — No new clip found yet.'}
            cooldown = db.cooling(scope)
            if cooldown:
                return {'error': 'Discogs cooldown — try again later.', 'status': 429, 'retry_after': int(cooldown)+1}
            budget.left -= 1
            try:
                status, headers, data = bounded_response(url, params, budget, policy('YUDI_REQUEST_TIMEOUT', 5))
                if status == 429:
                    raw = headers.get('Retry-After', '')
                    try:
                        seconds = float(raw)
                    except ValueError:
                        try:
                            seconds = parsedate_to_datetime(raw).timestamp()-time.time()
                        except (ValueError, TypeError, OverflowError):
                            seconds = policy('YUDI_COOLDOWN_SECONDS', 60)
                    seconds = min(policy('YUDI_MAX_COOLDOWN_SECONDS', 3600), max(1, seconds))
                    db.cool(scope, seconds)
                    return {'error': 'Discogs rate limit — try again later.', 'status': 429, 'retry_after': int(seconds)+1}
                if status in (401, 403):
                    return {'error': 'Discogs authentication failed.', 'status': status}
                if budget.remaining() <= 0:
                    return {'error': 'Selection deadline reached — No new clip found yet.'}
                if status >= 500:
                    raise requests.ConnectionError()
                if status != 200:
                    return {'error': 'Discogs request failed.', 'status': status}
                if budget.remaining() <= 0:
                    return {'error': 'Selection deadline reached — No new clip found yet.'}
                if not isinstance(data, dict):
                    return {'error': 'Invalid Discogs JSON.'}
                for field in ('videos', 'labels', 'results'):
                    if field in data and (not isinstance(data[field], list) or any(not isinstance(item, dict) for item in data[field])):
                        return {'error': 'Invalid Discogs JSON structure.'}
                if 'pagination' in data and not isinstance(data['pagination'], dict):
                    return {'error': 'Invalid Discogs pagination.'}
                ttl = policy('YUDI_SEARCH_TTL', 900) if '/database/search' in url else policy('YUDI_RELEASE_TTL', 3600)
                if '/releases/' in url and not any(extract_youtube_embed_url(v.get('uri')) for v in data.get('videos', []) if isinstance(v, dict)):
                    ttl = min(ttl, policy('YUDI_NO_VIDEO_TTL', 900))
                cc = headers.get('Cache-Control', '').lower()
                if any(item in cc for item in ('no-store', 'no-cache', 'private')) or headers.get('Vary'):
                    ttl = 0
                ages = re.findall(r'(?:s-maxage|max-age)\s*=\s*"?(\d+)', cc)
                if ages:
                    ttl = min(ttl, *(float(a) for a in ages))
                try:
                    ttl -= max(0, float(headers.get('Age', 0)))
                    if headers.get('Expires'):
                        ttl = min(ttl, parsedate_to_datetime(headers['Expires']).timestamp()-time.time())
                except (ValueError, TypeError, OverflowError):
                    ttl = 0
                data['_yudi_expires'] = time.time()+max(0, ttl)
                if budget.remaining() <= 0:
                    return {'error': 'Selection deadline reached — No new clip found yet.'}
                if ttl > 0:
                    db.put(key, data, ttl, int(policy('YUDI_CACHE_ENTRIES', 2000)), deadline=budget.end)
                if budget.remaining() <= 0:
                    return {'error': 'Selection deadline reached — No new clip found yet.'}
                return data
            except concurrent.futures.TimeoutError:
                return {'error': 'Selection deadline reached — No new clip found yet.'}
            except (requests.RequestException, ValueError):
                if attempt == int(policy('YUDI_RETRIES', 1)):
                    return {'error': 'Transient Discogs request failure.'}
                time.sleep(min(random.uniform(.05, .15), max(0, budget.remaining())))
        return {'error': 'Discogs request failed.'}
    finally:
        db.release(key, owner)


def extract_youtube_embed_url(uri: str) -> str:
    from urllib.parse import urlsplit, parse_qs
    if not isinstance(uri, str):
        return None
    try:
        parsed = urlsplit(uri or '')
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            return None
        host = parsed.hostname
        parts = parsed.path.strip('/').split('/')
        if host == 'youtu.be' and len(parts) == 1:
            ident = parts[0]
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            if parsed.path == '/watch':
                values = parse_qs(parsed.query).get('v', [])
                ident = values[0] if len(values) == 1 else ''
            elif len(parts) == 2 and parts[0] in ('embed', 'shorts', 'live'):
                ident = parts[1]
            else:
                return None
        else:
            return None
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', ident):
            return f'https://www.youtube.com/embed/{ident}'
    except (ValueError, TypeError):
        pass
    return None


def cache_key(url, params=None):
    import json, hashlib
    return json.dumps([hashlib.sha256(DISCOGS_TOKEN.encode()).hexdigest(), url, params or {}], sort_keys=True)


def clip_view(entry):
    """GET never refreshes upstream. Omit expired catalogue labels, keep references."""
    vid, rid = entry['video'], entry['release']
    data = store().cached(cache_key(f'https://api.discogs.com/releases/{rid}')) or {}
    videos = data.get('videos', [])
    title = next((v.get('title') for v in videos if extract_youtube_embed_url(v.get('uri')) == f'https://www.youtube.com/embed/{vid}'), None)
    labels = data.get('labels', [])
    label = labels[0] if labels else {}
    return dict(video_id=vid, video_embed_url=f'https://www.youtube.com/embed/{vid}',
                video_url=f'https://www.youtube.com/watch?v={vid}', video_title=title or vid,
                title=data.get('title') or 'Release', label_name=label.get('name'),
                label_url=discogs_api_to_public_url(label.get('resource_url')),
                release_url=discogs_api_to_public_url(data.get('master_url')) or f'https://www.discogs.com/release/{rid}')


def select_clip(browser, filters, budget=None):
    """Release-first random sampling, bounded discovery, atomic unseen reservation."""
    budget = budget or Budget()
    db = store()
    params = {'type': 'release', 'per_page': 100, 'page': 1}
    params.update({k: v for k, v in filters.items() if v})
    first = discogs_api_request('https://api.discogs.com/database/search', params, budget)
    if first.get('error'):
        return first
    try:
        pages = max(1, min(int(first.get('pagination', {}).get('pages', 1)), int(policy('YUDI_MAX_PAGES', 100))))
    except (ValueError, TypeError):
        return {'error': 'Invalid Discogs pagination.'}
    order = list(range(1, pages+1))
    random.shuffle(order)
    seen_releases = set()
    for page in order[:int(policy('YUDI_PAGE_ATTEMPTS', 10))]:
        if budget.remaining() <= 0:
            return {'error': 'Selection deadline reached — No new clip found yet.'}
        response = first if page == 1 else discogs_api_request('https://api.discogs.com/database/search', {**params, 'page': page}, budget)
        if response.get('error'):
            return response
        releases = list(response.get('results', []))
        random.shuffle(releases)
        for release in releases:
            if budget.remaining() <= 0:
                return {'error': 'Selection deadline reached — No new clip found yet.'}
            resource = release.get('resource_url', '')
            if not isinstance(resource, str) or not re.fullmatch(r'https://api\.discogs\.com/releases/\d+', resource) or resource in seen_releases:
                continue
            seen_releases.add(resource)
            data = discogs_api_request(resource, budget=budget)
            if data.get('error'):
                return data
            videos = list(data.get('videos', []))
            random.shuffle(videos)
            seen_ids = set()
            for video in videos:
                embed = extract_youtube_embed_url(video.get('uri'))
                if not embed:
                    continue
                vid = embed.rsplit('/', 1)[1]
                if vid in seen_ids:
                    continue
                seen_ids.add(vid)
                rid = int(resource.rsplit('/', 1)[1])
                state = {'filters': filters, 'current': vid}
                if budget.remaining() <= 0:
                    return {'error': 'Selection deadline reached — No new clip found yet.'}
                if db.reserve(browser, vid, rid, filters, state, deadline=budget.end):
                    return {'video': vid}
    return {'error': 'No new clip found yet — try broader filters or search again. History has not been reset.'}


@app.after_request
def private_response(response):
    if request.path == '/':
        response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.route('/', methods=['GET', 'POST'])
def index():
    import secrets, hmac
    from datetime import timedelta
    from flask import redirect, url_for, abort
    app.permanent_session_lifetime = timedelta(days=policy('YUDI_COOKIE_DAYS', 90))
    if not re.fullmatch(r'[a-f0-9]{32}', session.get('browser', '')):
        session.clear()
        session['browser'] = secrets.token_hex(16)
        session['csrf'] = secrets.token_hex(32)
    session.permanent = True
    browser = session['browser']
    db = store()
    db.prune(policy('YUDI_HISTORY_DAYS', 90))
    state = db.state(browser)
    filters = state.get('filters', {'year': '', 'style': '', 'country': ''})
    if request.method == 'POST':
        supplied = request.form.get('csrf_token', '')
        if not re.fullmatch(r'[a-f0-9]{64}', supplied) or not hmac.compare_digest(supplied, session.get('csrf', '')):
            abort(400, 'Invalid CSRF token.')
        action = request.form.get('action', 'select')
        if action not in ('select', 'replay', 'clear'):
            abort(400, 'Invalid action.')
        if action == 'clear':
            db.clear(browser, {'filters': filters, 'error': 'History cleared. Current selection removed; random clips may repeat again.'})
        elif action == 'replay':
            vid = request.form.get('video_id', '')
            entry = db.entry(browser, vid) if re.fullmatch(r'[A-Za-z0-9_-]{11}', vid) else None
            if not entry:
                abort(404, 'Clip is not in this browser history.')
            # Fresh metadata needs zero HTTP. Revalidate only this release when expired.
            resource = f"https://api.discogs.com/releases/{entry['release']}"
            if db.cached(cache_key(resource)) is None:
                refreshed = discogs_api_request(resource)
                if refreshed.get('error'):
                    db.save_state(browser, {**state, **refreshed})
                    return redirect(url_for('index'), code=303)
            if not db.replay(browser, vid, {'filters': filters, 'current': vid}):
                abort(404)
        else:
            if request.form.get('reroll') != 'true':
                filters = {k: request.form.get(k, '').strip() for k in ('year', 'style', 'country')}
                if any(len(v) > 120 or any(ord(c)<32 for c in v) for v in filters.values()):
                    abort(400, 'Invalid filter.')
                if filters['year'] and not re.fullmatch(r'\d{4}', filters['year']):
                    abort(400, 'Use a four-digit release year.')
            result = select_clip(browser, filters)
            if result.get('error'):
                # Keep the previous player while presenting the failure separately.
                db.save_state(browser, {**state, 'filters': filters, **result})
        return redirect(url_for('index'), code=303)
    for k, v in filters.items():
        session[k] = v
    entry = db.entry(browser, state.get('current', ''))
    video = clip_view(entry) if entry else None
    history = [clip_view(row) for row in db.history(browser, int(policy('YUDI_HISTORY_DISPLAY', 100)))]
    response = app.make_response(render_template('index.html', video=video, history=history, message=state.get('error'), csrf_token=session['csrf']))
    if state.get('status'):
        response.status_code = state['status']
    if state.get('retry_after'):
        response.headers['Retry-After'] = str(state['retry_after'])
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0')
