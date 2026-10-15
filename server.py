"""ZooTodo server: serves index.html and a tiny JSON API with username/password auth.

Standard library only (http.server + sqlite3). Each user's whole app state is stored
as one JSON document, the same shape the frontend keeps in localStorage.

    python server.py            # http://localhost:8000
    PORT=9000 python server.py
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('ZOOTODO_DB', ROOT / 'zootodo.db'))
PORT = int(os.environ.get('PORT', '8000'))
HOST = os.environ.get('HOST', '127.0.0.1')

SESSION_TTL = 30 * 24 * 3600          # 30 days
MAX_BODY = 1_000_000                  # 1 MB per request
USERNAME_RE = re.compile(r'^[\w.-]{3,32}$')
MIN_PASSWORD = 6

_db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY,
                username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
                pw_hash    BLOB NOT NULL,
                salt       BLOB NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS states (
                user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                data       TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
        """)


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ZooTodo/1.0'

    # ---------- plumbing ----------
    def send_json(self, status, payload, extra_headers=()):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        # Requiring a JSON content type means a cross-site form can't post here
        # without a CORS preflight, which this server never approves.
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            raise ValueError('Ожидается JSON')
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            raise ValueError('Слишком большой запрос')
        return json.loads(self.rfile.read(length) or b'{}')

    def session_cookie(self, token, max_age):
        c = cookies.SimpleCookie()
        c['sid'] = token
        c['sid']['path'] = '/'
        c['sid']['httponly'] = True
        c['sid']['samesite'] = 'Lax'
        c['sid']['max-age'] = max_age
        if os.environ.get('COOKIE_SECURE') == '1':
            c['sid']['secure'] = True
        return ('Set-Cookie', c['sid'].OutputString())

    def current_token(self):
        raw = self.headers.get('Cookie')
        if not raw:
            return None
        c = cookies.SimpleCookie()
        c.load(raw)
        return c['sid'].value if 'sid' in c else None

    def current_user(self):
        token = self.current_token()
        if not token:
            return None
        with db() as conn:
            row = conn.execute(
                'SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id '
                'WHERE s.token = ? AND s.expires_at > ?', (token, int(time.time()))).fetchone()
        return row

    def start_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with _db_lock, db() as conn:
            conn.execute('DELETE FROM sessions WHERE expires_at <= ?', (int(time.time()),))
            conn.execute('INSERT INTO sessions VALUES (?, ?, ?)', (token, user_id, int(time.time()) + SESSION_TTL))
        return self.session_cookie(token, SESSION_TTL)

    # ---------- routes ----------
    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in ('/', '/index.html'):
            return self.serve_index()
        if path == '/api/me':
            user = self.current_user()
            if not user:
                return self.send_json(401, {'error': 'Не авторизован'})
            return self.send_json(200, {'username': user['username']})
        if path == '/api/state':
            user = self.current_user()
            if not user:
                return self.send_json(401, {'error': 'Не авторизован'})
            with db() as conn:
                row = conn.execute('SELECT data FROM states WHERE user_id = ?', (user['id'],)).fetchone()
            return self.send_json(200, {'state': json.loads(row['data']) if row else None})
        self.send_json(404, {'error': 'Не найдено'})

    def do_PUT(self):
        if self.path != '/api/state':
            return self.send_json(404, {'error': 'Не найдено'})
        user = self.current_user()
        if not user:
            return self.send_json(401, {'error': 'Не авторизован'})
        try:
            state = self.read_json().get('state')
            if not isinstance(state, dict) or not isinstance(state.get('projects'), list):
                raise ValueError('Неверный формат данных')
        except ValueError as e:
            return self.send_json(400, {'error': str(e)})
        with _db_lock, db() as conn:
            conn.execute(
                'INSERT INTO states VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at',
                (user['id'], json.dumps(state, ensure_ascii=False), int(time.time())))
        self.send_json(200, {'ok': True})

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if path == '/api/logout':
            token = self.current_token()
            if token:
                with _db_lock, db() as conn:
                    conn.execute('DELETE FROM sessions WHERE token = ?', (token,))
            return self.send_json(200, {'ok': True}, [self.session_cookie('', 0)])
        if path not in ('/api/register', '/api/login'):
            return self.send_json(404, {'error': 'Не найдено'})
        try:
            data = self.read_json()
        except ValueError as e:
            return self.send_json(400, {'error': str(e)})
        username = str(data.get('username', '')).strip()
        password = str(data.get('password', ''))

        if path == '/api/register':
            if not USERNAME_RE.match(username):
                return self.send_json(400, {'error': 'Логин: 3–32 символа, буквы, цифры, точка, дефис или _'})
            if len(password) < MIN_PASSWORD:
                return self.send_json(400, {'error': f'Пароль должен быть не короче {MIN_PASSWORD} символов'})
            salt = secrets.token_bytes(16)
            try:
                with _db_lock, db() as conn:
                    cur = conn.execute('INSERT INTO users (username, pw_hash, salt, created_at) VALUES (?, ?, ?, ?)',
                                       (username, hash_password(password, salt), salt, int(time.time())))
                    user_id = cur.lastrowid
                    initial = data.get('state')
                    if isinstance(initial, dict) and isinstance(initial.get('projects'), list):
                        conn.execute('INSERT INTO states VALUES (?, ?, ?)',
                                     (user_id, json.dumps(initial, ensure_ascii=False), int(time.time())))
            except sqlite3.IntegrityError:
                return self.send_json(409, {'error': 'Такой логин уже занят'})
            return self.send_json(201, {'username': username}, [self.start_session(user_id)])

        # login
        with db() as conn:
            row = conn.execute('SELECT id, username, pw_hash, salt FROM users WHERE username = ?', (username,)).fetchone()
        # hash even for unknown users so response time doesn't reveal which usernames exist
        candidate = hash_password(password, row['salt'] if row else b'\0' * 16)
        if not row or not hmac.compare_digest(candidate, row['pw_hash']):
            return self.send_json(401, {'error': 'Неверный логин или пароль'})
        self.send_json(200, {'username': row['username']}, [self.start_session(row['id'])])

    def serve_index(self):
        body = (ROOT / 'index.html').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f'{self.address_string()} {fmt % args}', flush=True)


if __name__ == '__main__':
    init_db()
    print(f'ZooTodo: http://{HOST}:{PORT}  (база: {DB_PATH})', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
