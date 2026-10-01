import json
import re
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, render_template, request, session, send_from_directory
from werkzeug.security import check_password_hash
from .adapters import digest
from .push import validate_subscription
from .store import Store

def create_app(config=None):
    root=Path(__file__).resolve().parent.parent
    app=Flask(__name__,template_folder=str(root/'templates'),static_folder=None)
    app.config.update(SECRET_KEY=os.environ.get('BRIEF_SESSION_SECRET'),PASSWORD_HASH=os.environ.get('BRIEF_PASSWORD_HASH'),DATABASE=os.environ.get('BRIEF_DATABASE'),PUBLIC_ORIGIN=os.environ.get('BRIEF_PUBLIC_ORIGIN'),MEDIA_DIR=os.environ.get('BRIEF_MEDIA_DIR'),VAPID_PUBLIC_KEY=os.environ.get('BRIEF_VAPID_PUBLIC_KEY',''),SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',PERMANENT_SESSION_LIFETIME=timedelta(hours=12),MAX_CONTENT_LENGTH=32768)
    app.config.update(VAPID_PRIVATE_KEY=os.environ.get('BRIEF_VAPID_PRIVATE_KEY'),VAPID_SUBJECT=os.environ.get('BRIEF_VAPID_SUBJECT'))
    if config: app.config.update(config)
    app.config.setdefault('ENROLLMENT_ENABLED',os.environ.get('BRIEF_ENROLLMENT_ENABLED')=='true')
    if not all(app.config.get(k) for k in ('SECRET_KEY','PASSWORD_HASH','DATABASE','PUBLIC_ORIGIN')):
        raise RuntimeError('Required private deployment settings missing')
    dbpath=Path(app.config['DATABASE']).resolve()
    if not app.config.get('TESTING') and root.parent.resolve() in dbpath.parents:
        raise RuntimeError('Private database must be outside repository/web root')
    if not app.config.get('TESTING') and not app.config['PUBLIC_ORIGIN'].startswith('https://'):
        raise RuntimeError('HTTPS origin required')
    if not app.config.get('TESTING') and len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('Session secret too short')
    if not app.config.get('TESTING') and not app.config['PASSWORD_HASH'].startswith('scrypt:'):
        raise RuntimeError('Scrypt password hash required')
    store=Store(dbpath)
    app.extensions['brief_store']=store
    def csrf():
        if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(32)
        return session['csrf']
    app.jinja_env.globals['csrf']=csrf
    def authenticated():
        sid=session.get('sid')
        if not sid: return False
        with store.connect() as db:
            row=db.execute('SELECT expires FROM sessions WHERE id=?',(digest(sid),)).fetchone()
        return bool(row and row[0]>time.time())
    @app.before_request
    def boundary():
        from .publishing import PREFIX
        if request.path.startswith(PREFIX+'/'):
            # A separate bearer boundary; no browser-session or CSRF exemption elsewhere.
            request.max_content_length=1000000
            return None
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('Origin') != app.config['PUBLIC_ORIGIN']:
                abort(403)
            token=request.headers.get('X-CSRF-Token') or request.form.get('csrf','')
            if not token or not secrets.compare_digest(token,session.get('csrf','')):
                abort(403)
        if request.path not in ('/login','/health') and not authenticated():
            if request.path.startswith('/api/'): abort(401)
            return redirect('/login')
    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store, private','Pragma':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",'Permissions-Policy':'camera=(), microphone=(), geolocation=()'})
        if app.config['SESSION_COOKIE_SECURE']: response.headers['Strict-Transport-Security']='max-age=31536000'
        return response
    @app.get('/health')
    def health():
        with store.connect() as db: db.execute('SELECT 1').fetchone()
        build=root/'BUILD.json'
        return jsonify(status='ok',commit=json.loads(build.read_text())['commit'] if build.exists() else None)
    @app.route('/login',methods=['GET','POST'])
    def login():
        if request.method=='GET': return render_template('login.html')
        ip=digest(request.remote_addr or 'unknown')
        now=time.time()
        with store.connect() as db:
            row=db.execute('SELECT count,until FROM attempts WHERE ip=?',(ip,)).fetchone()
            count=row[0] if row and row[1]>now else 0
            if count>=5: abort(429)
            db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)',(ip,count+1,now+900))
        if not check_password_hash(app.config['PASSWORD_HASH'],request.form.get('password','')):
            return render_template('login.html',error='Aanmelden mislukt.'),401
        session.clear(); session.permanent=True
        sid=secrets.token_urlsafe(32); session['sid']=sid; csrf()
        with store.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(now,))
            db.execute('INSERT INTO sessions VALUES (?,?,?)',(digest(sid),session['csrf'],now+43200))
            db.execute('DELETE FROM attempts WHERE ip=?',(ip,))
        return redirect('/')
    @app.post('/logout')
    def logout():
        with store.connect() as db:
            db.execute('DELETE FROM sessions WHERE id=?',(digest(session.get('sid','')),))
        session.clear()
        return redirect('/login')
    @app.get('/')
    def home(): return render_template('dashboard.html')
    @app.get('/deals')
    def deals_page(): return render_template('dashboard.html',deals_view=True)
    @app.get('/api/briefs')
    def archives():
        with store.connect() as db:
            dates=[r[0] for r in db.execute('SELECT date FROM briefs ORDER BY date DESC LIMIT 366')]
        return jsonify(dates=dates)
    @app.get('/api/briefs/<date>')
    def brief(date):
        with store.connect() as db:
            row=db.execute('SELECT payload FROM briefs WHERE date=?',(date,)).fetchone()
        if not row: abort(404)
        return app.response_class(row[0],mimetype='application/json')
    @app.get('/api/push/config')
    def push_config(): return jsonify(publicKey=app.config['VAPID_PUBLIC_KEY'])
    @app.post('/api/push/subscribe')
    def subscribe():
        try: sub=validate_subscription(request.get_json())
        except (ValueError,TypeError,KeyError): abort(400)
        with store.connect() as db:
            db.execute('INSERT OR REPLACE INTO subscriptions VALUES (?,?)',(digest(sub['endpoint']),json.dumps(sub)))
        return jsonify(status='subscribed')
    @app.post('/api/push/unsubscribe')
    def unsubscribe():
        endpoint=(request.get_json() or {}).get('endpoint','')
        with store.connect() as db:
            db.execute('DELETE FROM subscriptions WHERE id=?',(digest(endpoint),))
        return jsonify(status='unsubscribed')
    @app.get('/assets/<name>')
    def assets(name):
        if name not in ('app.js','app.css','manifest.webmanifest','icon.svg','news-illustration.svg','health-illustration.svg','science-illustration.svg'): abort(404)
        return send_from_directory(root/'assets',name)
    @app.get('/media/<name>')
    def private_media(name):
        if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp)',name) or not app.config.get('MEDIA_DIR'): abort(404)
        directory=Path(app.config['MEDIA_DIR']).resolve()
        if root.parent.resolve() in directory.parents or directory==root.parent.resolve(): abort(404)
        return send_from_directory(directory,name)
    @app.get('/sw.js')
    def worker(): return send_from_directory(root/'assets','sw.js')
    from .publishing import register
    register(app,store)
    from .enrollment import register as register_enrollment
    register_enrollment(app,store)
    return app
