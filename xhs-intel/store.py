"""Edge-owned durable queue: immutable note revisions, fenced AI leases, receipts."""
import datetime as dt
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from common import digest
from config import policy_hash, policy_snapshot


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
                worker TEXT, result TEXT, result_hash TEXT, last_error TEXT,
                job_type TEXT NOT NULL DEFAULT 'summary', run_id TEXT, parent_job_id TEXT);
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
            CREATE TABLE IF NOT EXISTS watch_users (
                user_id TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL,
                updated REAL NOT NULL, source TEXT NOT NULL DEFAULT 'manual',
                state TEXT NOT NULL DEFAULT 'active', priority INTEGER NOT NULL DEFAULT 0,
                topic_ids_json TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS topics (
                topic_id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
                active_version INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS topic_versions (
                topic_id TEXT NOT NULL, version INTEGER NOT NULL, policy_json TEXT NOT NULL,
                policy_hash TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY(topic_id, version));
            CREATE TABLE IF NOT EXISTS recommendation_runs (
                run_id TEXT PRIMARY KEY, profile TEXT NOT NULL, source_kind TEXT NOT NULL,
                category TEXT NOT NULL, requested INTEGER NOT NULL, fetched INTEGER NOT NULL DEFAULT 0,
                added INTEGER NOT NULL DEFAULT 0, classified INTEGER NOT NULL DEFAULT 0,
                selected INTEGER NOT NULL DEFAULT 0, review INTEGER NOT NULL DEFAULT 0,
                rejected INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, policy_json TEXT NOT NULL,
                policy_hash TEXT NOT NULL, started REAL NOT NULL, finished REAL, last_error TEXT);
            CREATE TABLE IF NOT EXISTS recommendation_items (
                run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, revision TEXT NOT NULL,
                note_id TEXT NOT NULL, source_rank INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                created REAL NOT NULL, PRIMARY KEY(run_id, candidate_id));
            CREATE TABLE IF NOT EXISTS note_screenings (
                candidate_id TEXT NOT NULL, run_id TEXT NOT NULL, decision TEXT NOT NULL,
                topics_json TEXT NOT NULL, relevance_score REAL NOT NULL, confidence REAL NOT NULL,
                reason TEXT NOT NULL, evidence_json TEXT NOT NULL, risk_flags_json TEXT NOT NULL,
                model TEXT NOT NULL, input_sha256 TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY(run_id, candidate_id));
            CREATE TABLE IF NOT EXISTS following_accounts (
                user_id TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT '', source_endpoint TEXT NOT NULL,
                profile_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'candidate',
                first_seen REAL NOT NULL, last_seen REAL NOT NULL, score_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS profile_candidates (
                run_id TEXT NOT NULL, user_id TEXT NOT NULL, recent_note_ids_json TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'review', score_json TEXT NOT NULL DEFAULT '{}',
                reason TEXT NOT NULL DEFAULT '', reviewed_by TEXT, reviewed_at REAL,
                PRIMARY KEY(run_id, user_id));
            CREATE TABLE IF NOT EXISTS watch_user_topics (
                user_id TEXT NOT NULL, topic_id TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1,
                PRIMARY KEY(user_id, topic_id));
            CREATE TABLE IF NOT EXISTS note_topics (
                revision TEXT PRIMARY KEY, note_id TEXT NOT NULL, decision TEXT NOT NULL,
                topics_json TEXT NOT NULL, relevance_score REAL NOT NULL, confidence REAL NOT NULL,
                reason TEXT NOT NULL DEFAULT '', model TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS note_links (
                note_id TEXT PRIMARY KEY, xsec_token TEXT NOT NULL,
                xsec_source TEXT NOT NULL DEFAULT 'pc_share',
                created REAL NOT NULL, updated REAL NOT NULL);
            ''')
            # Additive migrations for databases created by the previous XHS
            # release. SQLite has no IF NOT EXISTS form for ADD COLUMN.
            columns = {row[1] for row in db.execute('PRAGMA table_info(jobs)').fetchall()}
            for name, definition in (
                ('job_type', "TEXT NOT NULL DEFAULT 'summary'"),
                ('run_id', 'TEXT'),
                ('parent_job_id', 'TEXT'),
            ):
                if name not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
            watch_columns = {row[1] for row in db.execute('PRAGMA table_info(watch_users)').fetchall()}
            for name, definition in (
                ('source', "TEXT NOT NULL DEFAULT 'manual'"),
                ('state', "TEXT NOT NULL DEFAULT 'active'"),
                ('priority', 'INTEGER NOT NULL DEFAULT 0'),
                ('topic_ids_json', "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if name not in watch_columns:
                    db.execute(f'ALTER TABLE watch_users ADD COLUMN {name} {definition}')
            self._seed_topics(db)
            self._reconcile_recommendation_runs(db)
        self.path.chmod(0o600)

    @staticmethod
    def _seed_topics(db):
        stamp = time.time()
        for row in policy_snapshot()['topics']:
            topic_id = str(row['slug'])
            db.execute('''INSERT OR IGNORE INTO topics
                          (topic_id,slug,name,description,enabled,active_version,created,updated)
                          VALUES(?,?,?,?,1,1,?,?)''',
                       (topic_id, topic_id, row['name'], row.get('description', ''), stamp, stamp))
            policy = json.dumps(row, ensure_ascii=False, sort_keys=True)
            db.execute('''INSERT OR IGNORE INTO topic_versions
                          (topic_id,version,policy_json,policy_hash,created) VALUES(?,?,?,?,?)''',
                       (topic_id, 1, policy, policy_hash({'topics': [row], 'version': 1}), stamp))

    @staticmethod
    def _reconcile_recommendation_runs(db):
        """Backfill parent run state from the durable summary job ledger."""
        db.execute('''UPDATE recommendation_runs
                      SET status='sent', finished=COALESCE(finished,
                          (SELECT updated FROM jobs WHERE jobs.run_id=recommendation_runs.run_id
                           AND jobs.job_type='summary' AND jobs.status='sent'
                           ORDER BY updated DESC LIMIT 1)), last_error=NULL
                      WHERE status IN ('summary_queued','summary_ready')
                        AND EXISTS (SELECT 1 FROM jobs
                                    WHERE jobs.run_id=recommendation_runs.run_id
                                      AND jobs.job_type='summary' AND jobs.status='sent')''')
        db.execute('''UPDATE recommendation_runs
                      SET status='summary_ready', finished=COALESCE(finished,
                          (SELECT updated FROM jobs WHERE jobs.run_id=recommendation_runs.run_id
                           AND jobs.job_type='summary' AND jobs.status='ready'
                           ORDER BY updated DESC LIMIT 1)), last_error=NULL
                      WHERE status='summary_queued'
                        AND EXISTS (SELECT 1 FROM jobs
                                    WHERE jobs.run_id=recommendation_runs.run_id
                                      AND jobs.job_type='summary' AND jobs.status='ready')''')

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

    def save_note_link(self, note_id, token, source='pc_share'):
        """Persist the newest signed note token durably (owner decision 2026-10-09).

        Links in old Feishu messages and the console stay clickable for as long
        as the upstream honors the token; the local preview remains the
        no-token fallback. Only per-note share tokens live here — cookies and
        session keys are never persisted.
        """
        note_id = str(note_id or '').lower()
        token = str(token or '')[:2048]
        source = str(source or 'pc_share')[:64]
        if not re.fullmatch(r'[0-9a-f]{24}', note_id) or not token:
            return False
        stamp = time.time()
        with self.connect() as db:
            db.execute('''INSERT INTO note_links(note_id,xsec_token,xsec_source,created,updated)
                          VALUES(?,?,?,?,?)
                          ON CONFLICT(note_id) DO UPDATE SET xsec_token=excluded.xsec_token,
                          xsec_source=excluded.xsec_source,updated=excluded.updated''',
                       (note_id, token, source, stamp, stamp))
        return True

    def note_link(self, note_id):
        with self.connect() as db:
            row = db.execute('SELECT xsec_token,xsec_source,updated FROM note_links WHERE note_id=?',
                             (str(note_id or '').lower(),)).fetchone()
        return dict(row) if row else None

    def latest_note(self, note_id):
        with self.connect() as db:
            row = db.execute('''SELECT body FROM notes WHERE note_id=?
                                ORDER BY last_seen DESC LIMIT 1''', (str(note_id),)).fetchone()
        return json.loads(row['body']) if row else None

    def enqueue_single_note(self, note, *, deliver_to_feishu=False, fetch_source='live'):
        """Persist one revision and queue one reusable analysis for that content."""
        revision = digest([note['note_id'], note['content_hash']])
        job_id = 'xhs-single-' + revision[:32]
        stamp = time.time()
        query = f"single:{note['note_id']}"
        payload_value = {
            'notes': [note],
            'prompt_version': 1,
            'source_kind': 'single_note',
            'fetch_source': 'cache' if fetch_source == 'cache' else 'live',
            'deliver_to_feishu': bool(deliver_to_feishu),
        }
        encoded = json.dumps(payload_value, ensure_ascii=False)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO notes VALUES(?,?,?,?,?,?,?)',
                       (revision, note['note_id'], note['content_hash'],
                        json.dumps(note, ensure_ascii=False), stamp, stamp, job_id))
            db.execute('UPDATE notes SET last_seen=?,job_id=COALESCE(job_id,?) WHERE revision=?',
                       (stamp, job_id, revision))
            db.execute('INSERT OR IGNORE INTO note_queries VALUES(?,?)', (revision, query))
            inserted = db.execute('''INSERT OR IGNORE INTO jobs
                                     (job_id,status,payload,created,updated,job_type)
                                     VALUES(?,'pending',?,?,?,'single_note_analysis')''',
                                  (job_id, encoded, stamp, stamp)).rowcount
            row = db.execute('SELECT status,payload FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            existing_payload = json.loads(row['payload']) if row else payload_value
            requested_delivery = bool(deliver_to_feishu or existing_payload.get('deliver_to_feishu'))
            payload_changed = requested_delivery != bool(existing_payload.get('deliver_to_feishu'))
            existing_payload['deliver_to_feishu'] = requested_delivery
            if fetch_source == 'live' and existing_payload.get('fetch_source') != 'live':
                existing_payload['fetch_source'] = 'live'
                existing_payload['notes'] = [note]
                payload_changed = True
            if row and payload_changed:
                db.execute('UPDATE jobs SET payload=?,updated=? WHERE job_id=?',
                           (json.dumps(existing_payload, ensure_ascii=False), stamp, job_id))
            if row and row['status'] == 'failed':
                db.execute("""UPDATE jobs SET status='pending',available=0,attempts=0,lease_token=NULL,
                              lease_until=NULL,worker=NULL,last_error=NULL,updated=? WHERE job_id=?""",
                           (stamp, job_id))
            elif row and row['status'] == 'completed' and requested_delivery:
                db.execute("UPDATE jobs SET status='ready',updated=?,last_error=NULL WHERE job_id=?",
                           (stamp, job_id))
            current = db.execute('SELECT status FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        return {
            'job_id': job_id,
            'job_status': current['status'],
            'duplicate': not bool(inserted),
            'deliver_to_feishu': requested_delivery,
            'fetch_source': existing_payload.get('fetch_source', 'live'),
            'note': {
                'note_id': note['note_id'],
                'title': note.get('title', ''),
                'author': note.get('author', ''),
                'published_at': note.get('published_at'),
                'fetched_at': note.get('fetched_at'),
                'url': note.get('url', ''),
            },
        }

    def enqueue_pending(self, limit=12):
        """Queue unassigned notes for the next pipeline stage.

        Watch-user notes come from hand-approved authors and keep their direct,
        timely summary.  Keyword/topic search notes go through an AI topic
        screening first, so irrelevant content never reaches a summary.
        """
        policy = self.active_policy()
        with self.connect() as db:
            rows = db.execute('SELECT * FROM notes WHERE job_id IS NULL ORDER BY first_seen LIMIT ?', (limit,)).fetchall()
            if not rows:
                return None
            stamp = time.time()
            watch_rows = []
            screen_rows = []
            for row in rows:
                body = json.loads(row['body'])
                (watch_rows if body.get('watch_user_id') else screen_rows).append((row, body))
            if watch_rows:
                job_id = 'xhs-' + digest([row['revision'] for row, _ in watch_rows])[:32]
                payload = {'notes': [body for _, body in watch_rows], 'policy': policy,
                           'prompt_version': 1}
                db.execute('''INSERT INTO jobs(job_id,status,payload,created,updated,job_type)
                              VALUES(?,?,?,?,?,?)''',
                           (job_id, 'pending', json.dumps(payload, ensure_ascii=False), stamp, stamp, 'summary'))
                db.executemany('UPDATE notes SET job_id=? WHERE revision=?',
                               [(job_id, row['revision']) for row, _ in watch_rows])
                return job_id
            notes = []
            for row, body in screen_rows:
                body['_candidate_id'] = row['revision']
                notes.append(body)
            job_id = 'xhs-screen-' + digest([row['revision'] for row, _ in screen_rows])[:32]
            payload = {'notes': notes, 'candidate_ids': [row['revision'] for row, _ in screen_rows],
                       'policy': policy, 'policy_hash': policy_hash(policy), 'prompt_version': 1}
            db.execute('''INSERT INTO jobs(job_id,status,payload,created,updated,job_type)
                          VALUES(?,?,?,?,?,?)''',
                       (job_id, 'pending', json.dumps(payload, ensure_ascii=False), stamp, stamp, 'classify_notes'))
            db.executemany('UPDATE notes SET job_id=? WHERE revision=?',
                           [(job_id, row['revision']) for row, _ in screen_rows])
            return job_id

    def claim(self, worker, clock=None, lane=None):
        stamp = time.time() if clock is None else clock
        lane = str(lane or '').strip().lower()
        if lane not in {'', 'interactive', 'batch'}:
            raise ValueError('invalid_worker_lane')
        lane_sql = " AND job_type='single_note_analysis'" if lane == 'interactive' else ''
        if lane == 'batch':
            lane_sql = " AND job_type!='single_note_analysis'"
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO workers VALUES(?,?)', (worker, stamp))
            row = db.execute("""SELECT * FROM jobs
                                WHERE ((status='pending' AND available<=?)
                                   OR (status='processing' AND lease_until<?))""" + lane_sql + """
                                ORDER BY CASE WHEN job_type='single_note_analysis' THEN 0 ELSE 1 END,
                                         created LIMIT 1""", (stamp, stamp)).fetchone()
            if not row:
                return None
            lease = uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='processing',worker=?,lease_token=?,lease_until=?,attempts=attempts+1,updated=? WHERE job_id=?",
                       (worker, lease, stamp + 180, stamp, row['job_id']))
            return {'job_id': row['job_id'], 'lease_token': lease, 'lease_seconds': 180,
                    'job_type': row['job_type'] or 'summary', 'run_id': row['run_id'],
                    **json.loads(row['payload'])}

    def heartbeat(self, job_id, lease):
        with self.connect() as db:
            stamp = time.time()
            changed = db.execute("UPDATE jobs SET lease_until=?,updated=? WHERE job_id=? AND status='processing' AND lease_token=? AND lease_until>=?",
                                 (stamp + 180, stamp, job_id, lease, stamp)).rowcount
            if not changed:
                raise Conflict('lease_lost')

    def complete(self, job_id, lease, result, clock=None):
        stamp = time.time() if clock is None else clock
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row and (row['job_type'] or 'summary') == 'classify_recommendations':
            return self.complete_filter(job_id, lease, result, clock=stamp)
        if row and (row['job_type'] or 'summary') == 'classify_profiles':
            return self.complete_profile_filter(job_id, lease, result, clock=stamp)
        if row and (row['job_type'] or 'summary') == 'classify_notes':
            return self.complete_note_filter(job_id, lease, result, clock=stamp)
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
            next_status = 'ready'
            if (row['job_type'] or 'summary') in ('single_note_analysis', 'daily_digest'):
                payload = json.loads(row['payload'])
                next_status = 'ready' if payload.get('deliver_to_feishu') else 'completed'
            db.execute("UPDATE jobs SET status=?,result=?,result_hash=?,updated=?,last_error=NULL WHERE job_id=?",
                       (next_status, json.dumps(result, ensure_ascii=False), result_hash, stamp, job_id))
            if row['run_id'] and (row['job_type'] or 'summary') == 'summary':
                db.execute("""UPDATE recommendation_runs
                              SET status='summary_ready',finished=?,last_error=NULL
                              WHERE run_id=? AND status='summary_queued'""",
                           (stamp, row['run_id']))
        return 'completed'

    def fail(self, job_id, lease, error):
        with self.connect() as db:
            row = db.execute("""SELECT attempts,job_type,run_id
                                FROM jobs
                                WHERE job_id=? AND status='processing' AND lease_token=?""",
                             (job_id, lease)).fetchone()
            if not row:
                raise Conflict('lease_lost')
            attempts = row['attempts']
            stamp = time.time()
            terminal = attempts >= 5
            error_text = str(error)[:120]
            db.execute('UPDATE jobs SET status=?,available=?,updated=?,last_error=? WHERE job_id=?',
                       ('failed' if terminal else 'pending', stamp + min(3600, 60 * 2**min(attempts, 6)), stamp,
                        error_text, job_id))
            if terminal and row['job_type'] == 'classify_recommendations' and row['run_id']:
                db.execute("""UPDATE recommendation_runs
                              SET status='filter_failed', finished=?, last_error=?
                              WHERE run_id=? AND status='filter_queued'""",
                           (stamp, error_text, row['run_id']))
                db.execute("""UPDATE recommendation_items SET state='filter_failed'
                              WHERE run_id=? AND state='filter_queued'""", (row['run_id'],))

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
            stamp = time.time()
            row = db.execute("SELECT run_id,job_type FROM jobs WHERE job_id=? AND status='ready'", (job_id,)).fetchone()
            changed = db.execute("UPDATE jobs SET status='sent',updated=?,last_error=NULL WHERE job_id=? AND status='ready'",
                                 (stamp, job_id)).rowcount
            if changed and row and row['run_id'] and (row['job_type'] or 'summary') == 'summary':
                db.execute("""UPDATE recommendation_runs
                              SET status='sent',finished=?,last_error=NULL
                              WHERE run_id=? AND status IN ('summary_ready','summary_queued')""",
                           (stamp, row['run_id']))

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
            recommendation = db.execute('''SELECT status,count(*) AS count,
                                           coalesce(sum(fetched),0) AS fetched,
                                           coalesce(sum(selected),0) AS selected
                                           FROM recommendation_runs GROUP BY status''').fetchall()
            return {'notes': db.execute('SELECT count(DISTINCT note_id) FROM notes').fetchone()[0],
                    'revisions': db.execute('SELECT count(*) FROM notes').fetchone()[0],
                    'jobs': dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall()),
                    'watch_users': db.execute('SELECT count(*) FROM watch_users WHERE enabled=1').fetchone()[0],
                    'last_worker_seen': db.execute('SELECT max(last_seen) FROM workers').fetchone()[0],
                    'runs': [dict(r) for r in db.execute('SELECT * FROM runs ORDER BY updated DESC LIMIT 10')],
                    'recommendation_runs': [dict(r) for r in db.execute('SELECT * FROM recommendation_runs ORDER BY started DESC LIMIT 10')],
                    'recommendation_counts': [dict(r) for r in recommendation]}

    def list_jobs(self, limit=50):
        """Return operational job metadata without prompts, results, or leases."""
        bounded_limit = max(1, min(int(limit), 200))
        with self.connect() as db:
            rows = db.execute('''SELECT job_id,status,created,updated,attempts,worker,
                                        job_type,run_id,last_error
                                 FROM jobs ORDER BY updated DESC LIMIT ?''',
                              (bounded_limit,)).fetchall()
            return [dict(row) for row in rows]

    def list_delivery_jobs(self, limit=50):
        """Return Feishu delivery metadata without persisted message content."""
        bounded_limit = max(1, min(int(limit), 200))
        with self.connect() as db:
            rows = db.execute('''SELECT j.job_id,j.status,j.created,j.updated,j.attempts,
                                        j.job_type,j.run_id,j.last_error,
                                        COUNT(d.part) AS delivered_parts
                                 FROM jobs j
                                 LEFT JOIN delivery_parts d ON d.job_id=j.job_id
                                 WHERE j.job_type NOT IN ('classify_recommendations','classify_profiles')
                                   AND (j.job_type!='single_note_analysis'
                                        OR COALESCE(json_extract(j.payload,'$.deliver_to_feishu'),0)=1)
                                 GROUP BY j.job_id
                                 ORDER BY j.updated DESC LIMIT ?''',
                              (bounded_limit,)).fetchall()
            return [dict(row) for row in rows]

    def list_single_note_jobs(self, limit=20):
        """Return safe note metadata and the user-visible analysis result."""
        bounded_limit = max(1, min(int(limit), 100))
        with self.connect() as db:
            rows = db.execute('''SELECT j.job_id,j.status,j.payload,j.result,j.created,j.updated,
                                        j.attempts,j.last_error,COUNT(d.part) AS delivered_parts
                                 FROM jobs j
                                 LEFT JOIN delivery_parts d ON d.job_id=j.job_id
                                 WHERE j.job_type='single_note_analysis'
                                 GROUP BY j.job_id
                                 ORDER BY j.updated DESC LIMIT ?''',
                              (bounded_limit,)).fetchall()
        values = []
        for row in rows:
            payload = json.loads(row['payload'])
            result = json.loads(row['result']) if row['result'] else {}
            note = (payload.get('notes') or [{}])[0]
            values.append({
                'job_id': row['job_id'],
                'status': row['status'],
                'note_id': note.get('note_id', ''),
                'title': note.get('title', ''),
                'author': note.get('author', ''),
                'published_at': note.get('published_at'),
                'fetched_at': note.get('fetched_at'),
                'url': note.get('url', ''),
                'preview_url': f"/xhs/open/{note.get('note_id', '')}",
                'image_count': int(note.get('image_count') or len(note.get('image_urls') or [])),
                'coverage': note.get('coverage', 'note_text_only'),
                'media_analyzed': bool(result.get('media_analyzed', note.get('media_analyzed', False))),
                'media_downloaded': int(result.get('media_downloaded', 0) or 0),
                'media_errors': int(result.get('media_errors', 0) or 0),
                'created': row['created'],
                'updated': row['updated'],
                'attempts': row['attempts'],
                'last_error': row['last_error'],
                'deliver_to_feishu': bool(payload.get('deliver_to_feishu')),
                'fetch_source': payload.get('fetch_source', 'live'),
                'delivered_parts': row['delivered_parts'],
                'summary': result.get('summary', ''),
                'model': result.get('model', ''),
            })
        return values

    def enqueue_manual_message(self, text, request_id):
        text = str(text or '').strip()
        request_id = str(request_id or '').strip()
        if not text or len(text) > 12000:
            raise ValueError('manual_message_must_be_1_to_12000_chars')
        if not request_id or len(request_id) > 160:
            raise ValueError('manual_message_request_id_required')
        job_id = 'xhs-dashboard-' + digest(request_id)[:32]
        stamp = time.time()
        payload = json.dumps({'source': 'xhs-dashboard', 'request_id_hash': digest(request_id)},
                             ensure_ascii=False)
        result = json.dumps({'summary': text, 'model': 'xhs-dashboard'}, ensure_ascii=False)
        with self.connect() as db:
            inserted = db.execute('''INSERT OR IGNORE INTO jobs
                                     (job_id,status,payload,created,updated,result,job_type)
                                     VALUES(?,'ready',?,?,?,?, 'manual_message')''',
                                  (job_id, payload, stamp, stamp, result)).rowcount
            row = db.execute('SELECT status FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        return {'job_id': job_id, 'job_status': row['status'], 'duplicate': not bool(inserted)}

    def create_recommendation_run(self, run_id, *, profile='ai-infra-daily', category='homefeed_recommend', requested=50, policy=None):
        policy = policy or policy_snapshot()
        encoded = json.dumps(policy, ensure_ascii=False, sort_keys=True)
        stamp = time.time()
        with self.connect() as db:
            existing = db.execute('SELECT * FROM recommendation_runs WHERE run_id=?', (run_id,)).fetchone()
            if existing:
                return dict(existing)
            db.execute('''INSERT INTO recommendation_runs
                          (run_id,profile,source_kind,category,requested,status,policy_json,policy_hash,started)
                          VALUES(?,?,?,?,?,?,?,?,?)''',
                       (run_id, profile, 'homefeed_recommendation', category, int(requested),
                        'running', encoded, policy_hash(policy), stamp))
        return self.recommendation_run(run_id)

    def recommendation_run(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM recommendation_runs WHERE run_id=?', (run_id,)).fetchone()
            return dict(row) if row else None

    def add_recommendation_item(self, run_id, note, source_rank):
        revision = digest([note['note_id'], note['content_hash']])
        stamp = time.time()
        with self.connect() as db:
            inserted = db.execute('''INSERT OR IGNORE INTO recommendation_items
                         (run_id,candidate_id,revision,note_id,source_rank,created)
                         VALUES(?,?,?,?,?,?)''',
                         (run_id, revision, revision, note['note_id'], int(source_rank), stamp)).rowcount
            # Reserve this revision from the legacy global summary queue. The
            # recommendation lane creates its own filtered summary job later.
            db.execute('UPDATE notes SET job_id=? WHERE revision=? AND job_id IS NULL',
                       ('xhs-recommendation:' + str(run_id), revision))
        return revision, bool(inserted)

    def finish_recommendation_collection(self, run_id, fetched, added, error=None):
        with self.connect() as db:
            db.execute('''UPDATE recommendation_runs SET fetched=?,added=?,status=?,finished=?,last_error=?
                          WHERE run_id=?''', (int(fetched), int(added), 'collected' if not error else 'failed',
                                              time.time(), error, run_id))

    def queue_recommendation_filter(self, run_id):
        with self.connect() as db:
            run = db.execute('SELECT * FROM recommendation_runs WHERE run_id=?', (run_id,)).fetchone()
            if not run:
                raise ValueError('unknown_recommendation_run')
            items = db.execute('''SELECT i.*,n.body FROM recommendation_items i
                                  JOIN notes n ON n.revision=i.revision
                                  WHERE i.run_id=? ORDER BY i.source_rank''', (run_id,)).fetchall()
            if not items:
                raise ValueError('recommendation_run_empty')
            job_id = 'xhs-filter-' + digest(run_id)[:32]
            existing = db.execute('SELECT job_id FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if existing:
                return job_id
            notes = []
            for row in items:
                note = json.loads(row['body'])
                note['_candidate_id'] = row['candidate_id']
                notes.append(note)
            payload = {'run_id': run_id, 'candidate_ids': [row['candidate_id'] for row in items],
                       'notes': notes, 'policy': json.loads(run['policy_json']),
                       'policy_hash': run['policy_hash'], 'prompt_version': 1}
            stamp = time.time()
            db.execute('''INSERT INTO jobs(job_id,status,payload,created,updated,job_type,run_id)
                          VALUES(?,?,?,?,?,?,?)''', (job_id, 'pending', json.dumps(payload, ensure_ascii=False),
                                                      stamp, stamp, 'classify_recommendations', run_id))
            db.execute("UPDATE recommendation_runs SET status='filter_queued' WHERE run_id=?", (run_id,))
            db.execute("UPDATE recommendation_items SET state='filter_queued' WHERE run_id=?", (run_id,))
            return job_id

    def complete_filter(self, job_id, lease, result, clock=None):
        stamp = time.time() if clock is None else clock
        decisions = result.get('decisions') if isinstance(result, dict) else None
        if not isinstance(decisions, list):
            raise ValueError('invalid_filter_decisions')
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if not row or row['job_type'] != 'classify_recommendations' or row['lease_token'] != lease or row['status'] != 'processing' or row['lease_until'] < stamp:
                raise Conflict('lease_lost_or_result_conflict')
            payload = json.loads(row['payload'])
            allowed = set(payload.get('candidate_ids') or [])
            got = {str(item.get('candidate_id') or item.get('note_id') or '') for item in decisions if isinstance(item, dict)}
            if got != allowed or len(decisions) != len(allowed):
                raise ValueError('filter_decisions_do_not_cover_candidates')
            counts = {'include': 0, 'review': 0, 'exclude': 0}
            selected_notes = []
            for item in decisions:
                candidate_id = str(item.get('candidate_id') or item.get('note_id') or '')
                decision = str(item.get('decision') or '').lower()
                if decision not in counts:
                    raise ValueError('invalid_filter_decision')
                topics = item.get('topics') if isinstance(item.get('topics'), list) else []
                score = float(item.get('relevance_score', 0))
                confidence = float(item.get('confidence', 0))
                if not (0 <= score <= 1 and 0 <= confidence <= 1):
                    raise ValueError('invalid_filter_score')
                reason = str(item.get('reason') or '')[:200]
                db.execute('''INSERT OR REPLACE INTO note_screenings
                              (candidate_id,run_id,decision,topics_json,relevance_score,confidence,reason,
                               evidence_json,risk_flags_json,model,input_sha256,created)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (candidate_id, row['run_id'], decision, json.dumps(topics, ensure_ascii=False), score,
                            confidence, reason, json.dumps(item.get('evidence') or [], ensure_ascii=False),
                            json.dumps(item.get('risk_flags') or [], ensure_ascii=False), str(result.get('model') or 'unknown'),
                            str(result.get('input_sha256') or ''), stamp))
                db.execute('UPDATE recommendation_items SET state=? WHERE run_id=? AND candidate_id=?', (decision, row['run_id'], candidate_id))
                counts[decision] += 1
                if decision == 'include':
                    note_row = db.execute('''SELECT n.body FROM recommendation_items i JOIN notes n ON n.revision=i.revision
                                             WHERE i.run_id=? AND i.candidate_id=?''', (row['run_id'], candidate_id)).fetchone()
                    if note_row:
                        selected_notes.append(json.loads(note_row['body']))
            summary_job_id = 'xhs-summary-' + digest(row['run_id'])[:32]
            if selected_notes and not db.execute('SELECT 1 FROM jobs WHERE job_id=?', (summary_job_id,)).fetchone():
                summary_payload = {'run_id': row['run_id'], 'notes': selected_notes,
                                   'selected_count': len(selected_notes), 'policy': payload.get('policy'),
                                   'prompt_version': 2}
                db.execute('''INSERT INTO jobs(job_id,status,payload,created,updated,job_type,run_id,parent_job_id)
                              VALUES(?,?,?,?,?,?,?,?)''', (summary_job_id, 'pending', json.dumps(summary_payload, ensure_ascii=False),
                                                              stamp, stamp, 'summary', row['run_id'], job_id))
            db.execute('''UPDATE jobs SET status='completed',result=?,result_hash=?,updated=?,last_error=NULL
                          WHERE job_id=?''', (json.dumps(result, ensure_ascii=False), digest(result), stamp, job_id))
            db.execute('''UPDATE recommendation_runs SET status=?,classified=?,selected=?,review=?,rejected=?,finished=?
                          WHERE run_id=?''', ('summary_queued' if selected_notes else 'filtered', len(decisions),
                                              counts['include'], counts['review'], counts['exclude'], stamp, row['run_id']))
            return {'status': 'completed', 'summary_job_id': summary_job_id if selected_notes else None, **counts}

    def complete_note_filter(self, job_id, lease, result, clock=None):
        """Persist lane-agnostic topic screenings; summarize only included notes."""
        stamp = time.time() if clock is None else clock
        decisions = result.get('decisions') if isinstance(result, dict) else None
        if not isinstance(decisions, list):
            raise ValueError('invalid_filter_decisions')
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if not row or row['job_type'] != 'classify_notes' or row['lease_token'] != lease or row['status'] != 'processing' or row['lease_until'] < stamp:
                raise Conflict('lease_lost_or_result_conflict')
            payload = json.loads(row['payload'])
            allowed = set(payload.get('candidate_ids') or [])
            got = {str(item.get('candidate_id') or '') for item in decisions if isinstance(item, dict)}
            if got != allowed or len(decisions) != len(allowed):
                raise ValueError('filter_decisions_do_not_cover_candidates')
            notes_by_revision = {str(note.get('_candidate_id') or ''): note
                                 for note in payload.get('notes') or []}
            counts = {'include': 0, 'review': 0, 'exclude': 0}
            selected = []
            for item in decisions:
                revision = str(item.get('candidate_id') or '')
                decision = str(item.get('decision') or '').lower()
                if decision not in counts:
                    raise ValueError('invalid_filter_decision')
                topics = item.get('topics') if isinstance(item.get('topics'), list) else []
                score = float(item.get('relevance_score', 0))
                confidence = float(item.get('confidence', 0))
                if not (0 <= score <= 1 and 0 <= confidence <= 1):
                    raise ValueError('invalid_filter_score')
                note = notes_by_revision.get(revision) or {}
                db.execute('''INSERT OR REPLACE INTO note_topics
                              (revision,note_id,decision,topics_json,relevance_score,confidence,
                               reason,model,input_sha256,created)
                              VALUES(?,?,?,?,?,?,?,?,?,?)''',
                           (revision, str(note.get('note_id') or ''), decision,
                            json.dumps(topics, ensure_ascii=False), score, confidence,
                            str(item.get('reason') or '')[:200], str(result.get('model') or 'unknown'),
                            str(result.get('input_sha256') or ''), stamp))
                counts[decision] += 1
                if decision == 'include' and note:
                    selected_note = dict(note)
                    selected_note['_topics'] = topics
                    selected.append(selected_note)
            summary_job_id = 'xhs-screened-' + digest(job_id)[:32]
            if selected and not db.execute('SELECT 1 FROM jobs WHERE job_id=?', (summary_job_id,)).fetchone():
                summary_payload = {'notes': selected, 'policy': payload.get('policy'),
                                   'prompt_version': 1,
                                   'source_kind': 'screened_search', 'parent_job_id': job_id}
                db.execute('''INSERT INTO jobs(job_id,status,payload,created,updated,job_type,parent_job_id)
                              VALUES(?,?,?,?,?,?,?)''',
                           (summary_job_id, 'pending', json.dumps(summary_payload, ensure_ascii=False),
                            stamp, stamp, 'summary', job_id))
            db.execute('''UPDATE jobs SET status='completed',result=?,result_hash=?,updated=?,last_error=NULL
                          WHERE job_id=?''', (json.dumps(result, ensure_ascii=False), digest(result), stamp, job_id))
            return {'status': 'completed',
                    'summary_job_id': summary_job_id if selected else None, **counts}

    def list_topics(self, enabled=None):
        with self.connect() as db:
            clause = '' if enabled is None else ' WHERE t.enabled=?'
            args = () if enabled is None else (1 if enabled else 0,)
            rows = db.execute('''SELECT t.*,v.policy_json,v.policy_hash
                                 FROM topics t
                                 LEFT JOIN topic_versions v
                                   ON v.topic_id=t.topic_id AND v.version=t.active_version''' +
                              clause + ' ORDER BY t.topic_id', args).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                encoded = item.pop('policy_json', None)
                try:
                    item['policy'] = json.loads(encoded) if encoded else {}
                except (TypeError, ValueError):
                    item['policy'] = {}
                result.append(item)
            return result

    def upsert_topic(self, row):
        slug = str(row.get('slug') or '').strip().lower()
        name = str(row.get('name') or slug).strip()[:100]
        if not slug or not name:
            raise ValueError('topic_slug_and_name_required')
        import re
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', slug):
            raise ValueError('invalid_topic_slug')
        policy = {
            'slug': slug, 'name': name, 'description': str(row.get('description') or '')[:500],
            'include_keywords': [str(x)[:80] for x in row.get('include_keywords', []) if str(x).strip()],
            'exclude_keywords': [str(x)[:80] for x in row.get('exclude_keywords', []) if str(x).strip()],
            'search_keywords': [str(x).strip()[:80] for x in row.get('search_keywords', []) if str(x).strip()][:20],
            'threshold': max(0.0, min(1.0, float(row.get('threshold', 0.65)))),
        }
        stamp = time.time()
        with self.connect() as db:
            current = db.execute('SELECT * FROM topics WHERE slug=?', (slug,)).fetchone()
            version = int(current['active_version']) + 1 if current else 1
            topic_id = str(current['topic_id']) if current else slug
            db.execute('''INSERT INTO topics(topic_id,slug,name,description,enabled,active_version,created,updated)
                          VALUES(?,?,?,?,?,?,?,?)
                          ON CONFLICT(slug) DO UPDATE SET name=excluded.name,description=excluded.description,
                          enabled=excluded.enabled,active_version=excluded.active_version,updated=excluded.updated''',
                       (topic_id, slug, name, policy['description'], int(row.get('enabled', 1)), version, stamp, stamp))
            db.execute('''INSERT INTO topic_versions(topic_id,version,policy_json,policy_hash,created)
                          VALUES(?,?,?,?,?)''', (topic_id, version, json.dumps(policy, ensure_ascii=False, sort_keys=True),
                                                 policy_hash({'topics': [policy], 'version': version}), stamp))
            return dict(db.execute('SELECT * FROM topics WHERE slug=?', (slug,)).fetchone())

    def set_topic_enabled(self, slug, enabled):
        with self.connect() as db:
            return bool(db.execute('UPDATE topics SET enabled=?,updated=? WHERE slug=?',
                                   (1 if enabled else 0, time.time(), str(slug))).rowcount)

    def topic_search_queries(self):
        """Return the enabled topics' search keywords as slug/keyword rows.

        A topic version saved without search_keywords falls back to the topic's
        display name, so enabling a topic always makes it collectable.
        """
        rows = []
        for topic in self.list_topics(enabled=True):
            policy = topic.get('policy') or {}
            keywords = [str(value).strip() for value in (policy.get('search_keywords') or [])
                        if str(value).strip()]
            if not keywords:
                keywords = [value for value in [str(topic.get('name') or '').strip()] if value]
            for keyword in keywords:
                rows.append({'slug': topic['slug'], 'keyword': keyword})
        return rows

    def active_policy(self):
        """Policy snapshot built from the live, versioned topic rows."""
        topics = []
        version = 1
        for row in self.list_topics(enabled=True):
            policy = dict(row.get('policy') or {})
            policy.setdefault('slug', row['slug'])
            policy.setdefault('name', row['name'])
            topics.append(policy)
            version = max(version, int(row.get('active_version') or 1))
        if not topics:
            return policy_snapshot()
        return policy_snapshot(topics=topics, version=version)

    def list_recommendation_items(self, run_id, states=None):
        with self.connect() as db:
            clause = ''
            args = [run_id]
            if states:
                values = list(states)
                clause = ' AND i.state IN (' + ','.join('?' for _ in values) + ')'
                args.extend(values)
            rows = db.execute('''SELECT i.*,n.body,s.decision,s.topics_json,s.relevance_score,s.confidence,s.reason
                                FROM recommendation_items i JOIN notes n ON n.revision=i.revision
                                LEFT JOIN note_screenings s ON s.run_id=i.run_id AND s.candidate_id=i.candidate_id
                                WHERE i.run_id=?''' + clause + ' ORDER BY i.source_rank', args).fetchall()
            return [dict(row) for row in rows]

    def upsert_following_accounts(self, accounts, source_endpoint):
        stamp = time.time()
        count = 0
        with self.connect() as db:
            for account in accounts:
                user_id = str(account.get('user_id') or '').strip()
                if not user_id:
                    continue
                db.execute('''INSERT INTO following_accounts
                              (user_id,nickname,source_endpoint,profile_json,first_seen,last_seen,score_json)
                              VALUES(?,?,?,?,?,?,?)
                              ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname,
                              source_endpoint=excluded.source_endpoint,profile_json=excluded.profile_json,
                              last_seen=excluded.last_seen''',
                           (user_id, str(account.get('nickname') or ''), source_endpoint,
                            json.dumps(account, ensure_ascii=False), stamp, stamp,
                            json.dumps(account.get('score') or {}, ensure_ascii=False)))
                count += 1
        return count

    def count_following_accounts(self, state=None):
        with self.connect() as db:
            if state:
                row = db.execute('SELECT COUNT(*) AS count FROM following_accounts WHERE state=?',
                                 (str(state),)).fetchone()
            else:
                row = db.execute('SELECT COUNT(*) AS count FROM following_accounts').fetchone()
            return int(row['count'] if row else 0)

    def list_following_accounts(self, state=None, limit=100, offset=0):
        bounded_limit = max(1, min(int(limit), 1000))
        bounded_offset = max(0, int(offset))
        with self.connect() as db:
            if state:
                rows = db.execute('SELECT * FROM following_accounts WHERE state=? ORDER BY last_seen DESC LIMIT ? OFFSET ?',
                                  (str(state), bounded_limit, bounded_offset)).fetchall()
            else:
                rows = db.execute('SELECT * FROM following_accounts ORDER BY last_seen DESC LIMIT ? OFFSET ?',
                                  (bounded_limit, bounded_offset)).fetchall()
            return [dict(row) for row in rows]

    def set_following_state(self, user_id, state, score=None):
        if state not in {'candidate', 'approved', 'rejected', 'snoozed', 'active', 'disabled'}:
            raise ValueError('invalid_following_state')
        with self.connect() as db:
            return bool(db.execute('UPDATE following_accounts SET state=?,score_json=?,last_seen=? WHERE user_id=?',
                                   (state, json.dumps(score or {}, ensure_ascii=False), time.time(), str(user_id))).rowcount)

    def queue_following_filter(self, profiles):
        profiles = [dict(item) for item in (profiles or []) if isinstance(item, dict) and item.get('user_id')]
        if not profiles:
            raise ValueError('no_following_candidates')
        policy = self.active_policy()
        run_id = 'xhs-profile-' + digest([row['user_id'] for row in profiles])[:24]
        job_id = 'xhs-profile-filter-' + digest(run_id)[:32]
        with self.connect() as db:
            existing = db.execute('SELECT job_id FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if existing:
                return {'run_id': run_id, 'job_id': job_id, 'status': 'duplicate'}
            payload = {'run_id': run_id, 'profiles': profiles, 'policy': policy,
                       'candidate_ids': [str(row['user_id']) for row in profiles], 'prompt_version': 1}
            stamp = time.time()
            db.execute('''INSERT INTO jobs(job_id,status,payload,created,updated,job_type,run_id)
                          VALUES(?,?,?,?,?,?,?)''', (job_id, 'pending', json.dumps(payload, ensure_ascii=False),
                                                      stamp, stamp, 'classify_profiles', run_id))
            return {'run_id': run_id, 'job_id': job_id, 'status': 'queued', 'count': len(profiles)}

    def complete_profile_filter(self, job_id, lease, result, clock=None):
        stamp = time.time() if clock is None else clock
        decisions = result.get('profiles') if isinstance(result, dict) else None
        if not isinstance(decisions, list):
            raise ValueError('invalid_profile_filter')
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if not row or row['job_type'] != 'classify_profiles' or row['lease_token'] != lease or row['status'] != 'processing' or row['lease_until'] < stamp:
                raise Conflict('lease_lost_or_result_conflict')
            payload = json.loads(row['payload'])
            allowed = set(str(value) for value in payload.get('candidate_ids') or [])
            got = {str(item.get('user_id') or '') for item in decisions if isinstance(item, dict)}
            if got != allowed or len(decisions) != len(allowed):
                raise ValueError('profile_decisions_do_not_cover_candidates')
            counts = {'include': 0, 'review': 0, 'exclude': 0}
            for item in decisions:
                user_id = str(item.get('user_id') or '')
                decision = str(item.get('decision') or '').lower()
                if decision not in counts:
                    raise ValueError('invalid_profile_decision')
                score = float(item.get('score', 0))
                confidence = float(item.get('confidence', 0))
                if not (0 <= score <= 1 and 0 <= confidence <= 1):
                    raise ValueError('invalid_profile_score')
                counts[decision] += 1
                db.execute('''INSERT OR REPLACE INTO profile_candidates
                              (run_id,user_id,recent_note_ids_json,decision,score_json,reason,reviewed_by,reviewed_at)
                              VALUES(?,?,?,?,?,?,NULL,NULL)''',
                           (row['run_id'], user_id, json.dumps(item.get('recent_note_ids') or [], ensure_ascii=False),
                            decision, json.dumps({'topics': item.get('topics') or [], 'score': score,
                                                  'confidence': confidence}, ensure_ascii=False),
                            str(item.get('reason') or '')[:300]))
                db.execute('UPDATE following_accounts SET score_json=?,last_seen=? WHERE user_id=?',
                           (json.dumps(item, ensure_ascii=False), stamp, user_id))
            db.execute('''UPDATE jobs SET status='completed',result=?,result_hash=?,updated=?,last_error=NULL
                          WHERE job_id=?''', (json.dumps(result, ensure_ascii=False), digest(result), stamp, job_id))
            return {'status': 'completed', **counts}

    def list_profile_candidates(self, limit=100):
        with self.connect() as db:
            rows = db.execute('''SELECT p.*,f.nickname,f.source_endpoint FROM profile_candidates p
                                 LEFT JOIN following_accounts f ON f.user_id=p.user_id
                                 ORDER BY p.reviewed_at IS NOT NULL,p.run_id DESC LIMIT ?''',
                             (max(1, min(int(limit), 500)),)).fetchall()
            return [dict(row) for row in rows]

    def apply_following_candidate(self, user_id, actor):
        stamp = time.time()
        user_id = str(user_id)
        with self.connect() as db:
            row = db.execute('''SELECT p.*,f.nickname FROM profile_candidates p
                                LEFT JOIN following_accounts f ON f.user_id=p.user_id
                                WHERE p.user_id=? ORDER BY p.reviewed_at DESC,p.run_id DESC LIMIT 1''', (user_id,)).fetchone()
            if not row:
                raise ValueError('unknown_following_candidate')
            if row['decision'] == 'exclude':
                raise ValueError('excluded_following_candidate')
            db.execute('UPDATE profile_candidates SET reviewed_by=?,reviewed_at=? WHERE run_id=? AND user_id=?',
                       (str(actor), stamp, row['run_id'], user_id))
            db.execute('UPDATE following_accounts SET state=?,last_seen=? WHERE user_id=?', ('active', stamp, user_id))
            db.execute('''INSERT INTO watch_users(user_id,label,enabled,created,updated,source,state,topic_ids_json)
                          VALUES(?,?,1,?,?,?,'active','[]')
                          ON CONFLICT(user_id) DO UPDATE SET enabled=1,updated=excluded.updated,
                          source=excluded.source,state='active' ''',
                       (user_id, str(row['nickname'] or ''), stamp, stamp, 'following_import'))
            return {'user_id': user_id, 'label': str(row['nickname'] or ''), 'actor': str(actor)}

    def reject_following_candidate(self, user_id, actor):
        stamp = time.time()
        user_id = str(user_id)
        with self.connect() as db:
            row = db.execute('''SELECT run_id FROM profile_candidates WHERE user_id=?
                                ORDER BY reviewed_at DESC,run_id DESC LIMIT 1''', (user_id,)).fetchone()
            if not row:
                raise ValueError('unknown_following_candidate')
            db.execute('UPDATE profile_candidates SET decision="exclude",reviewed_by=?,reviewed_at=? WHERE run_id=? AND user_id=?',
                       (str(actor), stamp, row['run_id'], user_id))
            db.execute('UPDATE following_accounts SET state=?,last_seen=? WHERE user_id=?', ('rejected', stamp, user_id))
            return {'user_id': user_id, 'actor': str(actor), 'status': 'rejected'}

    def list_watch_users(self, enabled=True):
        with self.connect() as db:
            if enabled is None:
                clause = ''
                args = ()
            else:
                clause = ' WHERE enabled=?'
                args = (1 if enabled else 0,)
            return [dict(row) for row in db.execute(
                'SELECT user_id,label,enabled,created,updated,source,state,priority,topic_ids_json FROM watch_users' + clause
                + ' ORDER BY created', args).fetchall()]

    def add_watch_user(self, user_id, label='', *, source='manual', topic_ids=None):
        stamp = time.time()
        with self.connect() as db:
            db.execute('''INSERT INTO watch_users(user_id,label,enabled,created,updated,source,state,topic_ids_json)
                          VALUES(?,?,1,?,?,?,'active',?)
                          ON CONFLICT(user_id) DO UPDATE SET label=excluded.label,
                          enabled=1,updated=excluded.updated,source=excluded.source,
                          state='active',topic_ids_json=excluded.topic_ids_json''',
                       (str(user_id), str(label or ''), stamp, stamp, str(source or 'manual'),
                        json.dumps(topic_ids or [], ensure_ascii=False)))

    def remove_watch_user(self, user_id):
        with self.connect() as db:
            return bool(db.execute('UPDATE watch_users SET enabled=0,updated=? WHERE user_id=?',
                                   (time.time(), str(user_id))).rowcount)

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

    def retry_recommendation_filter(self, run_id):
        """Reset one terminal AI filter job without recollecting its candidates."""
        stamp = time.time()
        with self.connect() as db:
            row = db.execute("""SELECT job_id FROM jobs
                                WHERE run_id=? AND job_type='classify_recommendations'
                                ORDER BY created DESC LIMIT 1""", (str(run_id),)).fetchone()
            if not row:
                return False
            changed = db.execute("""UPDATE jobs SET status='pending',available=0,attempts=0,
                                      lease_token=NULL,lease_until=NULL,worker=NULL,
                                      updated=?,last_error=NULL
                                      WHERE job_id=? AND status='failed'""",
                                 (stamp, row['job_id'])).rowcount
            if not changed:
                return False
            db.execute("""UPDATE recommendation_runs
                          SET status='filter_queued', finished=NULL, last_error=NULL
                          WHERE run_id=? AND status='filter_failed'""", (str(run_id),))
            db.execute("""UPDATE recommendation_items SET state='filter_queued'
                          WHERE run_id=? AND state='filter_failed'""", (str(run_id),))
            return True

    def queue_daily_digest(self, digest_date=None, *, deliver_to_feishu=True,
                           lookback_hours=26, max_notes=60):
        """Queue one idempotent per-day digest over screened, recommended and watch notes.

        The payload carries the previous digest text so the worker can mark what
        is new versus already covered, and the live topic policy so sections
        follow the console-edited taxonomy.
        """
        tz = dt.timezone(dt.timedelta(hours=8))
        digest_date = str(digest_date or dt.datetime.now(tz).date().isoformat())
        dt.date.fromisoformat(digest_date)
        job_id = 'xhs-digest-' + digest_date
        cutoff = time.time() - max(1, int(lookback_hours)) * 3600
        policy = self.active_policy()
        with self.connect() as db:
            existing = db.execute('SELECT status FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if existing:
                return {'job_id': job_id, 'status': 'duplicate', 'job_status': existing['status'],
                        'digest_date': digest_date}
            selected = {}
            for row in db.execute('''SELECT t.revision,t.topics_json,n.body FROM note_topics t
                                     JOIN notes n ON n.revision=t.revision
                                     WHERE t.decision='include' AND t.created>=?''', (cutoff,)):
                value = json.loads(row['body'])
                value['_topics'] = json.loads(row['topics_json'] or '[]')
                selected[row['revision']] = value
            for row in db.execute('''SELECT i.revision,s.topics_json,n.body FROM note_screenings s
                                     JOIN recommendation_items i
                                       ON i.run_id=s.run_id AND i.candidate_id=s.candidate_id
                                     JOIN notes n ON n.revision=i.revision
                                     WHERE s.decision='include' AND s.created>=?''', (cutoff,)):
                if row['revision'] not in selected:
                    value = json.loads(row['body'])
                    value['_topics'] = json.loads(row['topics_json'] or '[]')
                    selected[row['revision']] = value
            for row in db.execute('''SELECT DISTINCT n.revision,n.body FROM notes n
                                     JOIN note_queries q ON q.revision=n.revision
                                     WHERE n.first_seen>=? AND q.query LIKE 'watch:%' ''', (cutoff,)):
                if row['revision'] not in selected:
                    value = json.loads(row['body'])
                    value['_topics'] = []
                    selected[row['revision']] = value
            if not selected:
                return {'job_id': job_id, 'status': 'empty', 'digest_date': digest_date, 'notes': 0}
            notes = sorted(selected.values(),
                           key=lambda value: str(value.get('fetched_at') or ''),
                           reverse=True)[:max(1, int(max_notes))]
            previous = db.execute('''SELECT result FROM jobs
                                     WHERE job_type='daily_digest' AND result IS NOT NULL AND job_id!=?
                                     ORDER BY created DESC LIMIT 1''', (job_id,)).fetchone()
            previous_summary = ''
            if previous:
                try:
                    previous_summary = str(json.loads(previous['result']).get('summary') or '')[:4000]
                except (TypeError, ValueError):
                    previous_summary = ''
            stamp = time.time()
            payload = {'digest_date': digest_date, 'notes': notes, 'policy': policy,
                       'previous_digest': previous_summary,
                       'deliver_to_feishu': bool(deliver_to_feishu),
                       'source_kind': 'daily_digest', 'prompt_version': 1}
            db.execute('''INSERT INTO jobs(job_id,status,payload,created,updated,job_type)
                          VALUES(?,?,?,?,?,?)''',
                       (job_id, 'pending', json.dumps(payload, ensure_ascii=False), stamp, stamp,
                        'daily_digest'))
            return {'job_id': job_id, 'status': 'queued', 'digest_date': digest_date,
                    'notes': len(notes)}

    @staticmethod
    def _digest_row(row, *, include_summary=False):
        payload = json.loads(row['payload'])
        result = json.loads(row['result']) if row['result'] else {}
        value = {
            'job_id': row['job_id'],
            'digest_date': payload.get('digest_date', ''),
            'status': row['status'],
            'note_count': len(payload.get('notes') or []),
            'deliver_to_feishu': bool(payload.get('deliver_to_feishu')),
            'model': result.get('model', ''),
            'created': row['created'],
            'updated': row['updated'],
            'attempts': row['attempts'],
            'last_error': row['last_error'],
        }
        if include_summary:
            value['summary'] = result.get('summary', '')
            value['notes'] = [{
                'note_id': note.get('note_id', ''),
                'title': note.get('title', ''),
                'author': note.get('author', ''),
                'published_at': note.get('published_at'),
                'url': note.get('url', ''),
                'preview_url': f"/xhs/open/{note.get('note_id', '')}",
                'topics': [str(item.get('topic_id') or '') for item in (note.get('_topics') or [])
                           if isinstance(item, dict) and item.get('topic_id')],
            } for note in payload.get('notes') or []]
        return value

    def list_digests(self, limit=30):
        bounded = max(1, min(int(limit), 100))
        with self.connect() as db:
            rows = db.execute('''SELECT job_id,status,payload,result,created,updated,attempts,last_error
                                 FROM jobs WHERE job_type='daily_digest'
                                 ORDER BY created DESC LIMIT ?''', (bounded,)).fetchall()
        return [self._digest_row(row) for row in rows]

    def get_digest(self, digest_date):
        job_id = 'xhs-digest-' + str(digest_date)
        with self.connect() as db:
            row = db.execute('''SELECT job_id,status,payload,result,created,updated,attempts,last_error
                                FROM jobs WHERE job_id=? AND job_type='daily_digest' ''',
                             (job_id,)).fetchone()
        return self._digest_row(row, include_summary=True) if row else None

    def latest_summary(self):
        with self.connect() as db:
            row = db.execute("SELECT result FROM jobs WHERE result IS NOT NULL AND job_id NOT LIKE 'xhs-cmd-%' ORDER BY created DESC LIMIT 1").fetchone()
            return json.loads(row['result'])['summary'] if row else '暂无已完成摘要；本地 AI 上线后会自动处理待办。'

    def detail(self, note_id):
        with self.connect() as db:
            row = db.execute('SELECT body FROM notes WHERE note_id=? ORDER BY first_seen DESC LIMIT 1', (note_id,)).fetchone()
            return json.loads(row['body']) if row else None
