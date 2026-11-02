"""GreenPlate backend: Flask API that stores everything in CSV files (data/*.csv).

Run:   pip install flask      then      python app.py
Open:  http://127.0.0.1:5000   (do not open index.html by double-clicking, the backend must serve it)
"""
import csv
import os
import re
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
VOICE = os.path.join(DATA, 'voice')
app = Flask(__name__)          # only '/' and /api/* are served, so the CSV files are never downloadable
lock = threading.Lock()        # one writer at a time keeps the CSV files intact

# CSV files and their columns (every row also starts with a timestamp)
FILES = {
    'bmi':        ['timestamp', 'weight_kg', 'height_cm', 'bmi', 'category'],
    'water':      ['timestamp', 'glasses'],
    'newsletter': ['timestamp', 'email'],
    'messages':   ['timestamp', 'name', 'email', 'message'],
    'questions':  ['timestamp', 'name', 'question', 'voice_files'],
}
FOODS_HEADER = ['name', 'kcal_per_100g', 'note']
FOODS = [
    ('Banana', 89, '1.1 g protein, rich in potassium'), ('Lentils (cooked)', 116, '9 g protein, 8 g fibre'),
    ('Almonds', 579, '21 g protein, healthy fats'), ('Spinach', 23, 'Iron, folate, vitamin K'),
    ('Apple', 52, '2.4 g fibre, vitamin C'), ('Brown rice (cooked)', 112, 'Whole grain carbohydrate'),
    ('Egg', 143, '13 g protein, vitamin B12'), ('Yogurt (plain)', 61, 'Calcium and probiotics'),
    ('Chickpeas (cooked)', 164, '9 g protein, 8 g fibre'), ('Carrot', 41, 'Beta-carotene for vision'),
]


def path(name):
    return os.path.join(DATA, name + '.csv')


def init():
    """Create the data folder and every CSV file (with its header row) if missing."""
    os.makedirs(VOICE, exist_ok=True)
    for name, header in FILES.items():
        if not os.path.exists(path(name)):
            with open(path(name), 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(header)
    if not os.path.exists(path('foods')):
        with open(path('foods'), 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(FOODS_HEADER)
            w.writerows(FOODS)


def clean(value):
    """Trim text and stop spreadsheet formula injection (cells starting with = + - @)."""
    if not isinstance(value, str):
        return value
    value = value.strip()[:2000]
    return "'" + value if value[:1] in '=+-@' else value


def append_row(name, values):
    row = [datetime.now().isoformat(timespec='seconds')] + [clean(v) for v in values]
    with lock, open(path(name), 'a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(row)


def read_rows(name):
    with lock, open(path(name), newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def body():
    return request.get_json(silent=True) or {}


def number(value, low, high):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


@app.get('/')
def home():
    return send_from_directory(BASE, 'index.html')


@app.get('/api/foods')
def foods():
    q = request.args.get('search', '').lower()
    rows = [{'name': r['name'], 'kcal': r['kcal_per_100g'], 'note': r['note']}
            for r in read_rows('foods') if q in r['name'].lower()]
    return jsonify(rows)


@app.post('/api/bmi')
def save_bmi():
    d = body()
    w, h = number(d.get('weight'), 20, 400), number(d.get('height'), 80, 260)
    if w is None or h is None:
        return jsonify(error='Enter a valid weight (kg) and height (cm)'), 400
    bmi = round(w / (h / 100) ** 2, 1)
    category = 'Underweight' if bmi < 18.5 else 'Healthy range' if bmi < 25 else 'Overweight' if bmi < 30 else 'Obese'
    append_row('bmi', [w, h, bmi, category])
    return jsonify(ok=True, bmi=bmi, category=category)


@app.post('/api/water')
def save_water():
    g = number(body().get('glasses'), 0, 8)
    if g is None:
        return jsonify(error='Glasses must be between 0 and 8'), 400
    append_row('water', [int(g)])
    return jsonify(ok=True)


@app.post('/api/newsletter')
def newsletter():
    email = str(body().get('email', '')).strip().lower()
    if not re.match(r'^\S+@\S+\.\S+$', email):
        return jsonify(error='Enter a valid email address'), 400
    if any(r['email'] == email for r in read_rows('newsletter')):
        return jsonify(ok=True, already=True)
    append_row('newsletter', [email])
    return jsonify(ok=True)


@app.post('/api/contact')
def contact():
    d = body()
    name, email, message = (str(d.get(k, '')).strip() for k in ('name', 'email', 'message'))
    if not name or not message or not re.match(r'^\S+@\S+\.\S+$', email):
        return jsonify(error='Name, a valid email and a message are required'), 400
    append_row('messages', [name, email, message])
    return jsonify(ok=True)


@app.post('/api/question')
def question():
    name, text = request.form.get('name', '').strip(), request.form.get('question', '').strip()
    saved = []
    for f in request.files.getlist('voice')[:5]:
        fn = '%d_%s' % (time.time() * 1000, secure_filename(f.filename) or 'note.webm')
        f.save(os.path.join(VOICE, fn))
        saved.append(fn)
    if not name or not (text or saved):
        return jsonify(error='Add your name and a question or voice note'), 400
    append_row('questions', [name, text, ';'.join(saved)])
    return jsonify(ok=True)


if __name__ == '__main__':
    init()
    app.run(debug=True)
