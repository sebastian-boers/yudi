"""SQLite shared state. Every transaction ends before upstream I/O."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

class Store:
    def __init__(self, directory):
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, 'yudi.sqlite3')
        with self.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT, expires REAL, touched REAL);
            CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY, owner TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS cooldown (key TEXT PRIMARY KEY, until REAL);
            CREATE TABLE IF NOT EXISTS browsers (id TEXT PRIMARY KEY, state TEXT, touched REAL);
            CREATE TABLE IF NOT EXISTS history (browser TEXT, video TEXT, release INTEGER, filters TEXT, selected REAL, recent REAL, PRIMARY KEY(browser,video));
            CREATE INDEX IF NOT EXISTS history_recent ON history(browser,recent DESC);
            CREATE TABLE IF NOT EXISTS clip_titles (browser TEXT, video TEXT, title TEXT, PRIMARY KEY(browser,video));
            CREATE TABLE IF NOT EXISTS clip_labels (browser TEXT, video TEXT, url TEXT, PRIMARY KEY(browser,video));
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=0.25)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self, browser):
        with self.connection() as db:
            row = db.execute('SELECT state FROM browsers WHERE id=?', (browser,)).fetchone()
        return json.loads(row['state']) if row else {}

    def save_state(self, browser, state):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO browsers VALUES (?,?,?)', (browser, json.dumps(state), time.time()))

    def prune(self, days):
        with self.connection() as db:
            cutoff = time.time()-days*86400
            db.execute('DELETE FROM history WHERE recent<?', (cutoff,))
            db.execute('DELETE FROM browsers WHERE touched<?', (cutoff,))
            db.execute('DELETE FROM clip_titles WHERE NOT EXISTS (SELECT 1 FROM history WHERE history.browser=clip_titles.browser AND history.video=clip_titles.video)')
            db.execute('DELETE FROM clip_labels WHERE NOT EXISTS (SELECT 1 FROM history WHERE history.browser=clip_labels.browser AND history.video=clip_labels.video)')
            db.execute('DELETE FROM cache WHERE expires<=?', (time.time(),))

    def history(self, browser, limit=100):
        with self.connection() as db:
            rows = db.execute('SELECT h.*, t.title AS display_title, l.url AS label_reference FROM history h LEFT JOIN clip_titles t ON t.browser=h.browser AND t.video=h.video LEFT JOIN clip_labels l ON l.browser=h.browser AND l.video=h.video WHERE h.browser=? ORDER BY h.recent DESC LIMIT ?', (browser, limit)).fetchall()
        return [dict(row) for row in rows]

    def entry(self, browser, video):
        with self.connection() as db:
            row = db.execute('SELECT h.*, t.title AS display_title, l.url AS label_reference FROM history h LEFT JOIN clip_titles t ON t.browser=h.browser AND t.video=h.video LEFT JOIN clip_labels l ON l.browser=h.browser AND l.video=h.video WHERE h.browser=? AND h.video=?', (browser, video)).fetchone()
        return dict(row) if row else None

    def reserve(self, browser, video, release, filters, state, deadline=None, display_title=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if deadline is not None and time.monotonic() >= deadline:
                return False
            now = time.time()
            inserted = db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?,?,?,?)', (browser, video, release, json.dumps(filters, sort_keys=True), now, now)).rowcount
            if inserted:
                db.execute('INSERT OR REPLACE INTO browsers VALUES (?,?,?)', (browser, json.dumps(state), now))
                if display_title:
                    db.execute('INSERT OR REPLACE INTO clip_titles VALUES (?,?,?)', (browser, video, display_title))
            if deadline is not None and time.monotonic() >= deadline:
                db.rollback()
                return False
        return bool(inserted)

    def replay(self, browser, video, state):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            updated = db.execute('UPDATE history SET recent=? WHERE browser=? AND video=?', (time.time(), browser, video)).rowcount
            if updated:
                db.execute('INSERT OR REPLACE INTO browsers VALUES (?,?,?)', (browser, json.dumps(state), time.time()))
        return bool(updated)

    def clear(self, browser, state):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM history WHERE browser=?', (browser,))
            db.execute('DELETE FROM clip_titles WHERE browser=?', (browser,))
            db.execute('DELETE FROM clip_labels WHERE browser=?', (browser,))
            db.execute('INSERT OR REPLACE INTO browsers VALUES (?,?,?)', (browser, json.dumps(state), time.time()))

    def remember_title(self, browser, video, title):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO clip_titles SELECT browser, video, ? FROM history WHERE browser=? AND video=?', (title, browser, video))

    def remember_label(self, browser, video, url):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO clip_labels SELECT browser, video, ? FROM history WHERE browser=? AND video=?', (url, browser, video))

    def lease(self, key, owner, seconds):
        with self.connection() as db:
            db.execute('DELETE FROM leases WHERE expires<=?', (time.time(),))
            return db.execute('INSERT OR IGNORE INTO leases VALUES (?,?,?)', (key, owner, time.time()+seconds)).rowcount == 1

    def release(self, key, owner):
        with self.connection() as db:
            db.execute('DELETE FROM leases WHERE key=? AND owner=?', (key, owner))

    def cooling(self, key):
        with self.connection() as db:
            row = db.execute('SELECT until FROM cooldown WHERE key=?', (key,)).fetchone()
        return max(0, row['until']-time.time()) if row else 0

    def cool(self, key, seconds):
        with self.connection() as db:
            db.execute('INSERT INTO cooldown VALUES (?,?) ON CONFLICT(key) DO UPDATE SET until=max(until,excluded.until)', (key, time.time()+seconds))

    def cached(self, key):
        with self.connection() as db:
            row = db.execute('SELECT body FROM cache WHERE key=? AND expires>?', (key, time.time())).fetchone()
        return json.loads(row['body']) if row else None

    def put(self, key, data, ttl, limit=2000, deadline=None):
        body = json.dumps(data)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if deadline is not None and time.monotonic() >= deadline:
                return False
            now = time.time()
            db.execute('DELETE FROM cache WHERE expires<=?', (now,))
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)', (key, body, now+ttl, now))
            db.execute('DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY touched DESC LIMIT -1 OFFSET ?)', (limit,))
            if deadline is not None and time.monotonic() >= deadline:
                db.rollback()
                return False
        return True
