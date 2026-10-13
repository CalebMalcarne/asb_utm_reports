"""Local persistence; settings encrypted, scheduling state transactional."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from cryptography.fernet import Fernet

DEFAULTS={'company':'','pipedrive_token':'','aircall_id':'','aircall_token':'','sendgrid_key':'',
          'sender_email':'','sender_name':'ASB Reports','sendgrid_region':'US','timezone':'America/New_York',
          'utm_fields':{},'tag_field':'label_ids'}
class Store:
    def __init__(self,path=None):
        self.path=Path(path or os.getenv('ASB_REPORTS_DATA_DIR') or Path(__file__).parent/'.data').resolve()
        self.path.mkdir(parents=True,exist_ok=True)
        try: self.path.chmod(0o700)
        except OSError: pass
        key=self.path/'settings.key'
        try:
            fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError: pass
        else:
            with os.fdopen(fd,'wb') as f: f.write(Fernet.generate_key())
        self.fernet=Fernet(key.read_bytes())
        self.db=self.path/'reports.sqlite3'
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,name TEXT,spec TEXT,enabled INTEGER,next_run REAL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,schedule_id TEXT,slot REAL,status TEXT,detail TEXT,created REAL,updated REAL,
                 UNIQUE(schedule_id,slot));
            ''')
        try: self.db.chmod(0o600)
        except OSError: pass
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.db,timeout=30)
        db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback(); raise
        finally: db.close()
    def get(self,key,default=None):
        with self.connect() as db: row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        return json.loads(row['value']) if row else default
    def put(self,key,value):
        with self.connect() as db: db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(key,json.dumps(value)))
    def settings(self):
        token=self.get('settings')
        return {**DEFAULTS,**(json.loads(self.fernet.decrypt(token.encode())) if token else {})}
    def save_settings(self,value): self.put('settings',self.fernet.encrypt(json.dumps(value).encode()).decode())
    def schedules(self):
        with self.connect() as db: rows=db.execute('SELECT * FROM schedules ORDER BY name').fetchall()
        return [{**dict(r),'spec':json.loads(r['spec'])} for r in rows]
    def save_schedule(self,name,spec,next_run,enabled=False,sid=None):
        sid=sid or uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO schedules VALUES (?,?,?,?,?)',(sid,name,json.dumps(spec),int(enabled),next_run))
        return sid
    def enable(self,sid,enabled,next_run=None):
        with self.connect() as db:
            db.execute('UPDATE schedules SET enabled=?, next_run=COALESCE(?,next_run) WHERE id=?',(int(enabled),next_run,sid))
    def claim(self,now,next_time_fn):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 1',(now,)).fetchone()
            if not row: return None
            row=dict(row); spec=json.loads(row['spec']); slot=row['next_run']
            # One latest report after downtime; skip accumulated duplicate catch-up sends.
            db.execute('UPDATE schedules SET next_run=? WHERE id=?',(next_time_fn(spec,now),row['id']))
            jid=uuid.uuid4().hex
            db.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)',(jid,row['id'],slot,'preparing','',now,now))
            if db.execute('SELECT changes()').fetchone()[0]==0: return None
            return {'job_id':jid,'schedule_id':row['id'],'name':row['name'],'spec':spec,'slot':slot}
    def new_job(self,detail='Manual report'):
        jid=uuid.uuid4().hex; now=time.time()
        with self.connect() as db: db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(jid,None,None,'preparing',detail,now,now))
        return jid
    def finish(self,jid,status,detail):
        with self.connect() as db: db.execute('UPDATE jobs SET status=?,detail=?,updated=? WHERE id=?',(status,detail,time.time(),jid))
    def jobs(self):
        with self.connect() as db: return [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]
