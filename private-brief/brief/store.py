import json
import sqlite3
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS briefs(date TEXT PRIMARY KEY, hash TEXT, payload TEXT, published_at TEXT);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS deliveries(date TEXT, subscription TEXT, status TEXT, PRIMARY KEY(date,subscription));
CREATE TABLE IF NOT EXISTS attempts(ip TEXT PRIMARY KEY, count INTEGER, until REAL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, csrf TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, expires REAL);
CREATE TABLE IF NOT EXISTS drafts(date TEXT PRIMARY KEY,payload TEXT,hash TEXT,approved_hash TEXT);
CREATE TABLE IF NOT EXISTS publishers(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE,expires REAL,revoked INTEGER NOT NULL DEFAULT 0,scopes TEXT);
CREATE TABLE IF NOT EXISTS publisher_rates(id TEXT PRIMARY KEY,count INTEGER,until REAL);
CREATE TABLE IF NOT EXISTS publisher_devices(device_hash TEXT PRIMARY KEY,user_hash TEXT UNIQUE,expires REAL,status TEXT,last_poll REAL DEFAULT 0,interval REAL DEFAULT 5);
CREATE TABLE IF NOT EXISTS publisher_grants(id TEXT PRIMARY KEY,client_id TEXT,expires REAL,absolute_expires REAL,revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS publisher_refreshes(token_hash TEXT PRIMARY KEY,grant_id TEXT,expires REAL,used_at REAL);
CREATE TABLE IF NOT EXISTS publisher_grant_access(publisher_id TEXT PRIMARY KEY,grant_id TEXT);
'''
class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript(SCHEMA)
    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()
    def cached(self, key, compute):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()
        if row:
            return json.loads(row[0]), True
        value = compute()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO cache VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
        return value, False
