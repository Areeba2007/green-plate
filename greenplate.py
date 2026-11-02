"""GreenPlate data system: ONE CSV file (greenplate_data.csv) + this ONE Python file.

  - Python reads, adds, updates, deletes, searches and saves records in the CSV.
  - The Flask website backend (same API as before) stores everything in the same CSV.
  - A command line and a local admin page show the results on screen.

Quick start:   pip install flask   ->   python greenplate.py demo   ->   python greenplate.py serve
"""
import argparse
import csv
import os
import re
import sys
import threading
import time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE, 'greenplate_data.csv')
VOICE_DIR = os.path.join(BASE, 'data', 'voice')
LOCK = threading.RLock()          # one writer at a time keeps the CSV intact

# One row = one record. The "type" column says what kind of record it is.
COLUMNS = ['id', 'type', 'created', 'updated', 'name', 'email', 'message', 'weight_kg', 'height_cm',
           'bmi', 'category', 'glasses', 'kcal_per_100g', 'note', 'voice_files']
TYPES = {                          # which columns each record type uses
    'food':       ['name', 'kcal_per_100g', 'note'],
    'bmi':        ['weight_kg', 'height_cm', 'bmi', 'category'],
    'water':      ['glasses'],
    'newsletter': ['email'],
    'message':    ['name', 'email', 'message'],            # Contact form
    'question':   ['name', 'message', 'voice_files'],      # Ask an Expert (message = the question)
}
ALIASES = {'kcal': 'kcal_per_100g', 'weight': 'weight_kg', 'height': 'height_cm', 'question': 'message'}
SEED_FOODS = [
    ('Banana', 89, '1.1 g protein, rich in potassium'), ('Lentils (cooked)', 116, '9 g protein, 8 g fibre'),
    ('Almonds', 579, '21 g protein, healthy fats'), ('Spinach', 23, 'Iron, folate, vitamin K'),
    ('Apple', 52, '2.4 g fibre, vitamin C'), ('Brown rice (cooked)', 112, 'Whole grain carbohydrate'),
    ('Egg', 143, '13 g protein, vitamin B12'), ('Yogurt (plain)', 61, 'Calcium and probiotics'),
    ('Chickpeas (cooked)', 164, '9 g protein, 8 g fibre'), ('Carrot', 41, 'Beta-carotene for vision'),
]


# ---------------------------------------------------------------- reading and saving the CSV
def now():
    return datetime.now().isoformat(timespec='seconds')


def clean(value):
    """Trim text and stop spreadsheet formula injection (cells starting with = + - @)."""
    value = str(value).strip()[:2000]
    return "'" + value if value[:1] in '=+-@' else value


def load_all():
    with LOCK:
        if not os.path.exists(CSV_FILE):
            return []
        with open(CSV_FILE, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))


def save_all(rows):
    """SAVE: write every record to the CSV (temp file + replace, so a crash can't corrupt it)."""
    with LOCK:
        tmp = CSV_FILE + '.tmp'
        with open(tmp, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction='ignore')
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, CSV_FILE)


# ---------------------------------------------------------------- read / add / update / delete / search
def read(rtype=None):
    """READ: all records, or only one type ('food', 'bmi', ...)."""
    rows = load_all()
    return [r for r in rows if r['type'] == rtype] if rtype else rows


def bmi_calc(weight, height_cm):
    bmi = round(weight / (height_cm / 100) ** 2, 1)
    cat = 'Underweight' if bmi < 18.5 else 'Healthy range' if bmi < 25 else 'Overweight' if bmi < 30 else 'Obese'
    return bmi, cat


def add(rtype, **fields):
    """ADD: create a record, save it and return it."""
    if rtype not in TYPES:
        raise ValueError('Unknown type %r. Choose from: %s' % (rtype, ', '.join(TYPES)))
    if rtype == 'bmi' and 'weight_kg' in fields and 'height_cm' in fields and not fields.get('bmi'):
        fields['bmi'], fields['category'] = bmi_calc(float(fields['weight_kg']), float(fields['height_cm']))
    with LOCK:
        rows = load_all()
        new_id = max([int(r['id']) for r in rows] + [0]) + 1
        rec = {c: '' for c in COLUMNS}
        rec.update(id=str(new_id), type=rtype, created=now(), updated=now())
        for k in TYPES[rtype]:
            if k in fields:
                rec[k] = clean(fields[k])
        rows.append(rec)
        save_all(rows)
        return rec


def update(rec_id, **fields):
    """UPDATE: change fields of one record (by id), save, return it (or None if not found)."""
    with LOCK:
        rows = load_all()
        for r in rows:
            if r['id'] == str(rec_id):
                for k, v in fields.items():
                    if k in TYPES[r['type']]:
                        r[k] = clean(v)
                    else:
                        raise ValueError('%r records do not have a %r field. Allowed: %s' % (r['type'], k, ', '.join(TYPES[r['type']])))
                r['updated'] = now()
                save_all(rows)
                return r
    return None


def delete(rec_id):
    with LOCK:
        rows = load_all()
        keep = [r for r in rows if r['id'] != str(rec_id)]
        if len(keep) == len(rows):
            return False
        save_all(keep)
        return True


def search(text, rtype=None):
    """SEARCH: records where any field contains the text (case-insensitive)."""
    t = text.lower()
    return [r for r in read(rtype) if any(t in str(v).lower() for v in r.values())]


def stats():
    rows = load_all()
    out = {t: sum(1 for r in rows if r['type'] == t) for t in TYPES}
    bmis = [float(r['bmi']) for r in rows if r['type'] == 'bmi' and r['bmi']]
    out['average_bmi'] = round(sum(bmis) / len(bmis), 1) if bmis else None
    return out


# ---------------------------------------------------------------- create the file, seed foods, import old CSVs
def init():
    """Create greenplate_data.csv (with the 10 starter foods) if it does not exist yet."""
    os.makedirs(VOICE_DIR, exist_ok=True)
    if os.path.exists(CSV_FILE):
        return False
    save_all([])
    migrate_legacy()
    if not read('food'):
        for name, kcal, note in SEED_FOODS:
            add('food', name=name, kcal_per_100g=kcal, note=note)
    return True


def migrate_legacy():
    """If the older data/*.csv files hold rows, copy them into the new single file (one time only)."""
    legacy = {'foods': ('food', {'name': 'name', 'kcal_per_100g': 'kcal_per_100g', 'note': 'note'}),
              'bmi': ('bmi', {'weight_kg': 'weight_kg', 'height_cm': 'height_cm', 'bmi': 'bmi', 'category': 'category'}),
              'water': ('water', {'glasses': 'glasses'}), 'newsletter': ('newsletter', {'email': 'email'}),
              'messages': ('message', {'name': 'name', 'email': 'email', 'message': 'message'}),
              'questions': ('question', {'name': 'name', 'question': 'message', 'voice_files': 'voice_files'})}
    for fname, (rtype, mapping) in legacy.items():
        p = os.path.join(BASE, 'data', fname + '.csv')
        if os.path.exists(p):
            with open(p, newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    add(rtype, **{new: row.get(old, '') for new, old in mapping.items()})


# ---------------------------------------------------------------- show results on screen
def show(rows, title=None):
    if title:
        print('\n' + title)
    if not rows:
        print('  (no records)')
        return
    cols = [c for c in COLUMNS if any(r.get(c) for r in rows)]
    cut = lambda v: v if len(v) <= 30 else v[:27] + '...'
    width = {c: max(len(c), *(len(cut(r.get(c, ''))) for r in rows)) for c in cols}
    line = ' | '.join(c.upper().ljust(width[c]) for c in cols)
    print(line + '\n' + '-' * len(line))
    for r in rows:
        print(' | '.join(cut(r.get(c, '')).ljust(width[c]) for c in cols))
    print('%d record(s)' % len(rows))


def parse_pairs(items):
    out = {}
    for item in items:
        if '=' not in item:
            sys.exit('Use key=value pairs, for example: name="Mango" kcal=60 (got %r)' % item)
        k, v = item.split('=', 1)
        out[ALIASES.get(k.strip(), k.strip())] = v
    return out


def demo():
    print('GREENPLATE DEMO: everything below really reads and writes greenplate_data.csv')
    show(read('food'), '1. READ: foods stored in the CSV')
    mango = add('food', name='Mango', kcal_per_100g=60, note='Vitamin C and fibre')
    show([mango], '2. ADD: a new food was saved')
    show(search('mango'), '3. SEARCH: "mango"')
    update(mango['id'], kcal_per_100g=65, note='Vitamin C, vitamin A and fibre')
    show(search('mango'), '4. UPDATE: calories and note changed and saved')
    bmi = add('bmi', weight_kg=70, height_cm=175)
    add('newsletter', email='demo@greenplate.example')
    show([bmi], '5. ADD: a BMI record (bmi and category calculated by Python)')
    print('\n6. STATS:', stats())
    for r in search('demo@greenplate.example') + [mango, bmi]:
        delete(r['id'])
    print('\nDemo records removed so your data stays clean. Open greenplate_data.csv in Excel to see the file.')


# ---------------------------------------------------------------- website backend (Flask)
ADMIN_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>GreenPlate data</title>
<style>body{font:15px system-ui;margin:1.5rem;background:#f6fbf4;color:#1b2b1b}h1{color:#2e7d32}
input,select,button{font:inherit;padding:.4rem .6rem;margin:.2rem;border:1px solid #bcd;border-radius:8px}
button{background:#2e7d32;color:#fff;border:0;cursor:pointer}table{border-collapse:collapse;background:#fff;width:100%}
th,td{border:1px solid #dce8dc;padding:.4rem .6rem;text-align:left}th{background:#d7ecd8}#m{color:#2e7d32}</style></head><body>
<h1>GreenPlate data (greenplate_data.csv)</h1>
<div><input id="q" placeholder="Search anything"><select id="t"><option value="">All types</option></select>
<button onclick="load()">Search</button><span id="m"></span></div>
<div><select id="at"></select><input id="af" size="48" placeholder='fields, e.g. name=Mango; kcal_per_100g=60; note=Vitamin C'>
<button onclick="add()">Add record</button></div><table id="tb"></table>
<script>
const T=@@T@@,C=@@C@@,$=s=>document.querySelector(s);
T.forEach(t=>{$('#t').add(new Option(t,t));$('#at').add(new Option(t,t))});
async function api(u,o){const r=await fetch(u,o);const j=await r.json();if(!r.ok)throw j.error;return j}
async function load(){const j=await api('/api/records?q='+encodeURIComponent($('#q').value)+'&type='+$('#t').value);
const tb=$('#tb');tb.innerHTML='';const h=tb.insertRow();[...C,'actions'].forEach(c=>{h.insertCell().outerHTML='<th>'+c+'</th>'});
j.forEach(r=>{const row=tb.insertRow();C.forEach(c=>row.insertCell().textContent=r[c]||'');const a=row.insertCell();
const e=document.createElement('button');e.textContent='Edit';e.onclick=()=>edit(r);const d=document.createElement('button');d.textContent='Delete';d.onclick=()=>del(r.id);a.append(e,d)});
$('#m').textContent=' '+j.length+' record(s)'}
async function add(){const f={};$('#af').value.split(';').forEach(p=>{const i=p.indexOf('=');if(i>0)f[p.slice(0,i).trim()]=p.slice(i+1).trim()});
try{await api('/api/records',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:$('#at').value,fields:f})});$('#af').value='';load()}catch(e){alert(e)}}
async function edit(r){const k=prompt('Field to change (example: note)');if(!k)return;const v=prompt('New value for '+k,r[k]||'');if(v===null)return;
try{await api('/api/records/'+r.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({[k]:v})});load()}catch(e){alert(e)}}
async function del(id){if(confirm('Delete record '+id+'?')){await api('/api/records/'+id,{method:'DELETE'});load()}}
load();
</script></body></html>"""


def create_app():
    import json
    from flask import Flask, jsonify, request, send_from_directory
    from werkzeug.utils import secure_filename

    app = Flask(__name__)             # only '/', '/admin' and /api/* are served: the CSV is never downloadable
    body = lambda: request.get_json(silent=True) or {}
    email_ok = lambda e: bool(re.match(r'^\S+@\S+\.\S+$', e))

    def num(v, lo, hi):
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
        return n if lo <= n <= hi else None

    def local_only(fn):               # admin tools and raw records are available on this computer only
        def wrapper(*a, **k):
            if request.remote_addr not in ('127.0.0.1', '::1'):
                return jsonify(error='Local access only'), 403
            return fn(*a, **k)
        wrapper.__name__ = fn.__name__
        return wrapper

    @app.get('/')
    def home():
        return send_from_directory(BASE, 'index.html')

    # --- the website's existing features, now stored in greenplate_data.csv
    @app.get('/api/foods')
    def foods():
        q = request.args.get('search', '').lower()
        return jsonify([{'name': r['name'], 'kcal': r['kcal_per_100g'], 'note': r['note']}
                        for r in read('food') if q in r['name'].lower()])

    @app.post('/api/bmi')
    def api_bmi():
        d = body()
        w, h = num(d.get('weight'), 20, 400), num(d.get('height'), 80, 260)
        if w is None or h is None:
            return jsonify(error='Enter a valid weight (kg) and height (cm)'), 400
        rec = add('bmi', weight_kg=w, height_cm=h)
        return jsonify(ok=True, bmi=rec['bmi'], category=rec['category'])

    @app.post('/api/water')
    def api_water():
        g = num(body().get('glasses'), 0, 8)
        if g is None:
            return jsonify(error='Glasses must be between 0 and 8'), 400
        add('water', glasses=int(g))
        return jsonify(ok=True)

    @app.post('/api/newsletter')
    def api_newsletter():
        email = str(body().get('email', '')).strip().lower()
        if not email_ok(email):
            return jsonify(error='Enter a valid email address'), 400
        if any(r['email'] == email for r in read('newsletter')):
            return jsonify(ok=True, already=True)
        add('newsletter', email=email)
        return jsonify(ok=True)

    @app.post('/api/contact')
    def api_contact():
        d = body()
        name, email, msg = (str(d.get(k, '')).strip() for k in ('name', 'email', 'message'))
        if not name or not msg or not email_ok(email):
            return jsonify(error='Name, a valid email and a message are required'), 400
        add('message', name=name, email=email, message=msg)
        return jsonify(ok=True)

    @app.post('/api/question')
    def api_question():
        name, text = request.form.get('name', '').strip(), request.form.get('question', '').strip()
        saved = []
        for f in request.files.getlist('voice')[:5]:
            fn = '%d_%s' % (time.time() * 1000, secure_filename(f.filename) or 'note.webm')
            f.save(os.path.join(VOICE_DIR, fn))
            saved.append(fn)
        if not name or not (text or saved):
            return jsonify(error='Add your name and a question or voice note'), 400
        add('question', name=name, message=text, voice_files=';'.join(saved))
        return jsonify(ok=True)

    # --- admin: read, search, add, update, delete on screen
    @app.get('/admin')
    @local_only
    def admin():
        return ADMIN_HTML.replace('@@T@@', json.dumps(list(TYPES))).replace('@@C@@', json.dumps(COLUMNS)), 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.get('/api/records')
    @local_only
    def records():
        q, t = request.args.get('q', ''), request.args.get('type') or None
        return jsonify(search(q, t) if q else read(t))

    @app.post('/api/records')
    @local_only
    def records_add():
        d = body()
        try:
            return jsonify(add(d.get('type', ''), **{ALIASES.get(k, k): v for k, v in (d.get('fields') or {}).items()})), 201
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400

    @app.put('/api/records/<rec_id>')
    @local_only
    def records_update(rec_id):
        try:
            rec = update(rec_id, **{ALIASES.get(k, k): v for k, v in body().items()})
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return (jsonify(rec), 200) if rec else (jsonify(error='Not found'), 404)

    @app.delete('/api/records/<rec_id>')
    @local_only
    def records_delete(rec_id):
        return (jsonify(ok=True), 200) if delete(rec_id) else (jsonify(error='Not found'), 404)

    return app


# ---------------------------------------------------------------- command line
def main(argv=None):
    p = argparse.ArgumentParser(prog='greenplate.py', description='GreenPlate CSV data system')
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('init', help='create greenplate_data.csv if missing')
    sub.add_parser('demo', help='run a full read/add/search/update demo on screen')
    sub.add_parser('stats', help='record counts and average BMI')
    sub.add_parser('serve', help='start the website at http://127.0.0.1:5000')
    s = sub.add_parser('list', help='show records'); s.add_argument('--type', choices=TYPES)
    s = sub.add_parser('search', help='find records'); s.add_argument('text'); s.add_argument('--type', choices=TYPES)
    s = sub.add_parser('add', help='add a record'); s.add_argument('type', choices=TYPES); s.add_argument('fields', nargs='*')
    s = sub.add_parser('update', help='change a record'); s.add_argument('id'); s.add_argument('fields', nargs='+')
    s = sub.add_parser('delete', help='delete a record'); s.add_argument('id')
    a = p.parse_args(argv)
    if init():
        print('Created greenplate_data.csv with the starter foods.')
    cmd = a.cmd or 'serve'
    if cmd == 'init':
        print('greenplate_data.csv is ready at', CSV_FILE)
    elif cmd == 'demo':
        demo()
    elif cmd == 'stats':
        print(stats())
    elif cmd == 'list':
        show(read(a.type), 'Records' + (' (%s)' % a.type if a.type else ''))
    elif cmd == 'search':
        show(search(a.text, a.type), 'Search results for %r' % a.text)
    elif cmd == 'add':
        try:
            show([add(a.type, **parse_pairs(a.fields))], 'Added and saved:')
        except (ValueError, TypeError) as e:
            sys.exit('Error: %s' % e)
    elif cmd == 'update':
        try:
            rec = update(a.id, **parse_pairs(a.fields))
        except ValueError as e:
            sys.exit('Error: %s' % e)
        show([rec], 'Updated and saved:') if rec else sys.exit('No record with id %s' % a.id)
    elif cmd == 'delete':
        print('Deleted.' if delete(a.id) else 'No record with id %s' % a.id)
    elif cmd == 'serve':
        print('GreenPlate website:  http://127.0.0.1:5000\nData admin screen:   http://127.0.0.1:5000/admin')
        create_app().run(debug=False)


if __name__ == '__main__':
    main()
