"""Edge-owned durable queue: immutable note revisions, fenced AI leases, receipts."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from common import digest


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS notes (
                revision TEXT PRIMARY KEY, note_id TEXT NOT NULL, content_hash TEXT NOT NULL,
                body TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                job_id TEXT, UNIQUE(note_id,content_hash));
            CREATE TABLE IF NOT EXISTS note_queries (
                revision TEXT NOT NULL, query TEXT NOT NULL, PRIMARY KEY(revision,query));
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, available REAL NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
                worker TEXT, result TEXT, result_hash TEXT, last_error TEXT);
            CREATE TABLE IF NOT EXISTS delivery_parts (
                job_id TEXT NOT NULL, part INTEGER NOT NULL, message_id TEXT NOT NULL,
                created REAL NOT NULL, PRIMARY KEY(job_id,part));
            CREATE TABLE IF NOT EXISTS runs (
                run_key TEXT PRIMARY KEY, status TEXT NOT NULL, query TEXT NOT NULL,
                started REAL NOT NULL, updated REAL NOT NULL, fetched INTEGER NOT NULL DEFAULT 0,
                added INTEGER NOT NULL DEFAULT 0, last_error TEXT);
            CREATE TABLE IF NOT EXISTS commands (
                message_id TEXT PRIMARY KEY, command TEXT NOT NULL, created REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', result TEXT);
            CREATE TABLE IF NOT EXISTS workers (
                worker TEXT PRIMARY KEY, last_seen REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_note(self, note, query):
        revision = digest([note['note_id'], note['content_hash']])
        stamp = time.time()
        with self.connect() as db:
            inserted = db.execute('INSERT OR IGNORE INTO notes VALUES(?,?,?,?,?,?,NULL)',
                                  (revision, note['note_id'], note['content_hash'], json.dumps(note, ensure_ascii=False), stamp, stamp)).rowcount
            db.execute('UPDATE notes SET last_seen=? WHERE revision=?', (stamp, revision))
            db.execute('INSERT OR IGNORE INTO note_queries VALUES(?,?)', (revision, query))
        return bool(inserted)

    def enqueue_pending(self, limit=12):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM notes WHERE job_id IS NULL ORDER BY first_seen LIMIT ?', (limit,)).fetchall()
            if not rows:
                return None
            job_id = 'xhs-' + digest([r['revision'] for r in rows])[:32]
            payload = {'notes': [json.loads(r['body']) for r in rows], 'prompt_version': 1}
            stamp = time.time()
            db.execute('INSERT INTO jobs(job_id,status,payload,created,updated) VALUES(?,?,?,?,?)',
                       (job_id, 'pending', json.dumps(payload, ensure_ascii=False), stamp, stamp))
            db.executemany('UPDATE notes SET job_id=? WHERE revision=?', [(job_id, r['revision']) for r in rows])
            return job_id

    def claim(self, worker, clock=None):
        stamp = time.time() if clock is None else clock
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO workers VALUES(?,?)', (worker, stamp))
            row = db.execute("SELECT * FROM jobs WHERE (status='pending' AND available<=?) OR (status='processing' AND lease_until<?) ORDER BY created LIMIT 1", (stamp, stamp)).fetchone()
            if not row:
                return None
            lease = uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='processing',worker=?,lease_token=?,lease_until=?,attempts=attempts+1,updated=? WHERE job_id=?",
                       (worker, lease, stamp + 180, stamp, row['job_id']))
            return {'job_id': row['job_id'], 'lease_token': lease, 'lease_seconds': 180, **json.loads(row['payload'])}

    def heartbeat(self, job_id, lease):
        with self.connect() as db:
            stamp = time.time()
            changed = db.execute("UPDATE jobs SET lease_until=?,updated=? WHERE job_id=? AND status='processing' AND lease_token=? AND lease_until>=?",
                                 (stamp + 180, stamp, job_id, lease, stamp)).rowcount
            if not changed:
                raise Conflict('lease_lost')

    def complete(self, job_id, lease, result, clock=None):
        stamp = time.time() if clock is None else clock
        text = result.get('summary', '')
        if not isinstance(text, str) or not text.strip() or len(text) > 20000 or not result.get('model'):
            raise ValueError('invalid_summary')
        result_hash = digest(result)
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if row and row['lease_token'] == lease and row['result_hash'] == result_hash:
                return 'duplicate'
            if not row or row['lease_token'] != lease or row['status'] != 'processing' or row['lease_until'] < stamp:
                raise Conflict('lease_lost_or_result_conflict')
            db.execute("UPDATE jobs SET status='ready',result=?,result_hash=?,updated=?,last_error=NULL WHERE job_id=?",
                       (json.dumps(result, ensure_ascii=False), result_hash, stamp, job_id))
        return 'completed'

    def fail(self, job_id, lease, error):
        with self.connect() as db:
            row = db.execute("SELECT attempts FROM jobs WHERE job_id=? AND status='processing' AND lease_token=?", (job_id, lease)).fetchone()
            if not row:
                raise Conflict('lease_lost')
            attempts = row['attempts']
            db.execute('UPDATE jobs SET status=?,available=?,updated=?,last_error=? WHERE job_id=?',
                       ('failed' if attempts >= 5 else 'pending', time.time() + min(3600, 60 * 2**min(attempts, 6)), time.time(), error[:120], job_id))

    def ready_deliveries(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs WHERE status='ready' ORDER BY created LIMIT 10")]

    def delivered_part(self, job_id, part):
        with self.connect() as db:
            return db.execute('SELECT message_id FROM delivery_parts WHERE job_id=? AND part=?', (job_id, part)).fetchone() is not None

    def record_delivery(self, job_id, part, message_id):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO delivery_parts VALUES(?,?,?,?)', (job_id, part, message_id, time.time()))

    def delivered(self, job_id):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='sent',updated=?,last_error=NULL WHERE job_id=? AND status='ready'", (time.time(), job_id))

    def delivery_error(self, job_id, error):
        with self.connect() as db:
            db.execute('UPDATE jobs SET last_error=?,updated=? WHERE job_id=?', (error[:120], time.time(), job_id))

    def start_run(self, key, query):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE run_key=?', (key,)).fetchone()
            if row and (row['status'] == 'completed' or (row['status'] == 'running' and row['updated'] > time.time()-1800)):
                return False
            db.execute('INSERT OR REPLACE INTO runs(run_key,status,query,started,updated) VALUES(?,?,?,?,?)', (key, 'running', query, time.time(), time.time()))
            return True

    def end_run(self, key, status, fetched, added, error=None):
        with self.connect() as db:
            db.execute('UPDATE runs SET status=?,fetched=?,added=?,last_error=?,updated=? WHERE run_key=?', (status, fetched, added, error, time.time(), key))

    def status(self):
        with self.connect() as db:
            return {'notes': db.execute('SELECT count(DISTINCT note_id) FROM notes').fetchone()[0],
                    'revisions': db.execute('SELECT count(*) FROM notes').fetchone()[0],
                    'jobs': dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall()),
                    'last_worker_seen': db.execute('SELECT max(last_seen) FROM workers').fetchone()[0],
                    'runs': [dict(r) for r in db.execute('SELECT * FROM runs ORDER BY updated DESC LIMIT 10')]}

    def command(self, message_id, command):
        with self.connect() as db:
            return bool(db.execute('INSERT OR IGNORE INTO commands(message_id,command,created) VALUES(?,?,?)', (message_id, command, time.time())).rowcount)

    def pending_commands(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM commands WHERE status='pending' ORDER BY created LIMIT 10")]

    def command_result(self, message_id, text):
        job_id = 'xhs-cmd-' + digest(message_id)[:32]
        result = json.dumps({'summary': text, 'model': 'command'}, ensure_ascii=False)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO jobs(job_id,status,payload,created,updated,result) VALUES(?,'ready','{}',?,?,?)", (job_id, time.time(), time.time(), result))
            db.execute("UPDATE commands SET status='completed',result=? WHERE message_id=?", (job_id, message_id))

    def retry_job(self, job_id):
        with self.connect() as db:
            return bool(db.execute("UPDATE jobs SET status='pending',available=0,attempts=0,updated=? WHERE job_id=? AND status='failed'", (time.time(), job_id)).rowcount)

    def latest_summary(self):
        with self.connect() as db:
            row = db.execute("SELECT result FROM jobs WHERE result IS NOT NULL AND job_id NOT LIKE 'xhs-cmd-%' ORDER BY created DESC LIMIT 1").fetchone()
            return json.loads(row['result'])['summary'] if row else '暂无已完成摘要；本地 AI 上线后会自动处理待办。'

    def detail(self, note_id):
        with self.connect() as db:
            row = db.execute('SELECT body FROM notes WHERE note_id=? ORDER BY first_seen DESC LIMIT 1', (note_id,)).fetchone()
            return json.loads(row['body']) if row else None
