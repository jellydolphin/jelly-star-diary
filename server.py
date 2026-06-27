"""
轻语手帐 - 双人协作日记后端
Flask + SQLite，支持多人异地同步写日记
"""
import json
import os
import uuid
import sqlite3
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory

app = Flask(__name__, static_folder='static', static_url_path='')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'diary.db')

# ============================================================
# Database
# ============================================================
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            avatar_color TEXT DEFAULT '#6b7d95',
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS diaries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            date TEXT NOT NULL,
            title TEXT NOT NULL,
            mood TEXT DEFAULT '',
            sticker TEXT DEFAULT '🌸',
            sleep_hours REAL,
            sleep_score INTEGER,
            workout TEXT DEFAULT '',
            content TEXT NOT NULL,
            photos TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_diaries_date ON diaries(date DESC);
        CREATE INDEX IF NOT EXISTS idx_diaries_user ON diaries(user_id);
    """)
    db.commit()
    db.close()

# ============================================================
# Auth helper
# ============================================================
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': '请先登录'}), 401
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
        if not user:
            return jsonify({'error': '登录已过期，请重新登录'}), 401
        g.user = dict(user)
        return f(*args, **kwargs)
    return decorated

# ============================================================
# Serve static files
# ============================================================
@app.route('/')
def index():
    return send_from_directory('static', 'diary.html')

# ============================================================
# API: Login
# ============================================================
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    nickname = (data.get('nickname', '') or '').strip()
    if not nickname or len(nickname) > 12:
        return jsonify({'error': '昵称不能为空且不超过12个字'}), 400

    color = data.get('color', '#6b7d95')
    db = get_db()

    # Check if nickname already exists → return existing token
    existing = db.execute("SELECT * FROM users WHERE nickname = ?", (nickname,)).fetchone()
    if existing:
        # Update color
        db.execute("UPDATE users SET avatar_color = ? WHERE id = ?", (color, existing['id']))
        db.commit()
        return jsonify({
            'token': existing['token'],
            'user': {
                'id': existing['id'],
                'nickname': existing['nickname'],
                'avatar_color': color,
            }
        })

    user_id = uuid.uuid4().hex[:12]
    token = uuid.uuid4().hex
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()

    db.execute(
        "INSERT INTO users (id, nickname, avatar_color, token, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, nickname, color, token, now)
    )
    db.commit()

    return jsonify({
        'token': token,
        'user': {
            'id': user_id,
            'nickname': nickname,
            'avatar_color': color,
        }
    })

# ============================================================
# API: Get all users
# ============================================================
@app.route('/api/users', methods=['GET'])
@require_auth
def get_users():
    db = get_db()
    users = db.execute(
        "SELECT id, nickname, avatar_color FROM users ORDER BY created_at ASC"
    ).fetchall()
    return jsonify([dict(u) for u in users])

# ============================================================
# API: Get all diaries
# ============================================================
@app.route('/api/diaries', methods=['GET'])
@require_auth
def get_diaries():
    db = get_db()
    rows = db.execute("""
        SELECT d.*, u.nickname as author_name, u.avatar_color as author_color
        FROM diaries d
        JOIN users u ON d.user_id = u.id
        ORDER BY d.date DESC, d.created_at DESC
    """).fetchall()

    diaries = []
    for r in rows:
        d = dict(r)
        d['photos'] = json.loads(d['photos'] or '[]')
        d['is_owner'] = (d['user_id'] == g.user['id'])
        diaries.append(d)

    return jsonify(diaries)

# ============================================================
# API: Create diary
# ============================================================
@app.route('/api/diaries', methods=['POST'])
@require_auth
def create_diary():
    data = request.get_json(silent=True) or {}
    title = (data.get('title', '') or '').strip()
    content = (data.get('content', '') or '').strip()

    if not title:
        return jsonify({'error': '请输入标题'}), 400
    if not content:
        return jsonify({'error': '请输入内容'}), 400

    diary_id = uuid.uuid4().hex[:14]
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()

    db = get_db()
    db.execute("""
        INSERT INTO diaries (id, user_id, date, title, mood, sticker, sleep_hours, sleep_score, workout, content, photos, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        diary_id,
        g.user['id'],
        data.get('date', now[:10]),
        title,
        data.get('mood', ''),
        data.get('sticker', '🌸'),
        data.get('sleep_hours'),
        data.get('sleep_score'),
        data.get('workout', ''),
        content,
        json.dumps(data.get('photos', []), ensure_ascii=False),
        now
    ))
    db.commit()

    return jsonify({'id': diary_id, 'message': '日记已保存'}), 201

# ============================================================
# API: Update diary
# ============================================================
@app.route('/api/diaries/<diary_id>', methods=['PUT'])
@require_auth
def update_diary(diary_id):
    db = get_db()
    diary = db.execute("SELECT * FROM diaries WHERE id = ?", (diary_id,)).fetchone()
    if not diary:
        return jsonify({'error': '日记不存在'}), 404
    if diary['user_id'] != g.user['id']:
        return jsonify({'error': '只能编辑自己的日记'}), 403

    data = request.get_json(silent=True) or {}
    title = (data.get('title', '') or '').strip()
    content = (data.get('content', '') or '').strip()

    if not title:
        return jsonify({'error': '请输入标题'}), 400
    if not content:
        return jsonify({'error': '请输入内容'}), 400

    db.execute("""
        UPDATE diaries SET date=?, title=?, mood=?, sticker=?, sleep_hours=?, sleep_score=?, workout=?, content=?, photos=?
        WHERE id=?
    """, (
        data.get('date', diary['date']),
        title,
        data.get('mood', ''),
        data.get('sticker', diary['sticker']),
        data.get('sleep_hours'),
        data.get('sleep_score'),
        data.get('workout', ''),
        content,
        json.dumps(data.get('photos', []), ensure_ascii=False),
        diary_id
    ))
    db.commit()

    return jsonify({'message': '日记已更新'})

# ============================================================
# API: Delete diary
# ============================================================
@app.route('/api/diaries/<diary_id>', methods=['DELETE'])
@require_auth
def delete_diary(diary_id):
    db = get_db()
    diary = db.execute("SELECT * FROM diaries WHERE id = ?", (diary_id,)).fetchone()
    if not diary:
        return jsonify({'error': '日记不存在'}), 404
    if diary['user_id'] != g.user['id']:
        return jsonify({'error': '只能删除自己的日记'}), 403

    db.execute("DELETE FROM diaries WHERE id = ?", (diary_id,))
    db.commit()

    return jsonify({'message': '日记已删除'})


# ============================================================
# API: Delete user (self)
# ============================================================
@app.route('/api/users/me', methods=['DELETE'])
@require_auth
def delete_self():
    db = get_db()
    # Delete user's diaries first
    db.execute("DELETE FROM diaries WHERE user_id = ?", (g.user['id'],))
    db.execute("DELETE FROM users WHERE id = ?", (g.user['id'],))
    db.commit()
    return jsonify({'message': '账号已删除'})


# ============================================================
# Startup
# ============================================================
if __name__ == '__main__':
    init_db()
    print("📔 轻语手帐服务已启动 → http://0.0.0.0:8000")
    app.run(host='0.0.0.0', port=8000, debug=False)
