import os
import time
import threading
import hashlib
import hmac
import uuid
import zipfile
import gzip
import io

from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# jobs dict stores per-job state
# structure:
# jobs[job_id] = {
#   'processed': int,
#   'total': int,
#   'done': bool,
#   'paused': bool,
#   'speed_per_min': float,
#   'eta_seconds': int|None,
#   'match': str|None,
#   'roll': float|None,
#   'start_time': float,
#   'lock': threading.Lock()
# }
jobs = {}

# -------------------------
# Utility functions
# -------------------------
def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def hmac_sha512_hex(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha512).hexdigest()

def extract_roll_from_hmac(hmac_hex: str) -> float:
    # Find 5-hex-digit chunks and compute roll similar to the JS logic:
    pos = 0
    roll = 10001
    while roll >= 10000 and pos + 5 <= len(hmac_hex):
        roll = int(hmac_hex[pos:pos+5], 16)
        pos += 5
    return (roll % 10000) / 100.0

def calculate_dice_roll(server_seed: str, client_seed: str, nonce: int) -> float:
    msg = f"{client_seed}:{nonce}"
    h = hmac_sha512_hex(server_seed, msg)
    return extract_roll_from_hmac(h)

def read_wordlist_from_file(file_storage):
    filename = (file_storage.filename or "").lower()
    # read into string lines
    if filename.endswith(".zip"):
        # read first .txt file inside zip or concatenate all .txt files
        with zipfile.ZipFile(file_storage.stream) as z:
            lines = []
            for name in z.namelist():
                # skip directories
                if name.endswith('/'):
                    continue
                # read text-like files
                try:
                    with z.open(name) as f:
                        raw = f.read()
                        try:
                            text = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            text = raw.decode("latin-1")
                        lines.extend(text.splitlines())
                except Exception:
                    # ignore bad entries
                    continue
            return [ln.strip() for ln in lines if ln and ln.strip()]
    elif filename.endswith(".gz"):
        # gzip text file
        # file_storage.stream is file-like already
        try:
            with gzip.open(file_storage.stream, mode='rt', encoding='utf-8', errors='ignore') as gf:
                lines = gf.read().splitlines()
                return [ln.strip() for ln in lines if ln and ln.strip()]
        except Exception:
            # fallback: read raw then decode
            file_storage.stream.seek(0)
            raw = file_storage.stream.read()
            try:
                text = raw.decode("utf-8")
            except Exception:
                text = raw.decode("latin-1")
            return [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
    else:
        # text file
        content = file_storage.stream.read()
        if isinstance(content, bytes):
            try:
                text = content.decode("utf-8")
            except Exception:
                text = content.decode("latin-1")
        else:
            text = content
        return [ln.strip() for ln in text.splitlines() if ln and ln.strip()]

# -------------------------
# Background worker
# -------------------------
def process_job(job_id: str, wordlist_lines, target_hash: str, client_seed: str, nonce: int, max_speed: int):
    job = jobs[job_id]
    job['start_time'] = time.time()
    total = len(wordlist_lines)
    job['total'] = total
    job['processed'] = 0
    job['done'] = False
    job['match'] = None
    job['roll'] = None
    job['speed_per_min'] = 0
    job['eta_seconds'] = None

    last_checkpoint_time = time.time()
    last_processed_count = 0

    for idx, seed in enumerate(wordlist_lines, start=1):
        with job['lock']:
            if job['paused']:
                # busy-wait while paused but still responsive
                while job['paused']:
                    time.sleep(0.5)
            # continue processing

        # Compute SHA256 and compare
        try:
            candidate = seed
            hashed_candidate = sha256_hex(candidate)
        except Exception:
            hashed_candidate = ""

        job['processed'] = idx

        # Update speed/per-minute every second
        now = time.time()
        elapsed = now - job['start_time']
        if elapsed > 0:
            job['speed_per_min'] = (job['processed'] / elapsed) * 60.0

        # ETA compute
        if job['speed_per_min'] > 0:
            remaining = total - job['processed']
            job['eta_seconds'] = int((remaining / job['speed_per_min']) * 60)
        else:
            job['eta_seconds'] = None

        if hashed_candidate.lower() == target_hash.lower():
            # found
            roll = calculate_dice_roll(candidate, client_seed, nonce)
            job['match'] = candidate
            job['roll'] = roll
            job['done'] = True
            job['status'] = 'completed'
            return

        # throttle to respect max_speed (words per minute)
        if max_speed and max_speed > 0:
            # time per word in seconds
            time_per_word = 60.0 / float(max_speed)
            # ensure we don't run faster than allowed
            time.sleep(time_per_word)

    # finished without finding match
    job['done'] = True
    job['status'] = 'finished_no_match'
    job['match'] = None
    job['roll'] = None
    job['eta_seconds'] = 0

# -------------------------
# Routes
# -------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/process", methods=["POST"])
def start_process():
    # Accept either file upload 'wordlist' or 'wordlistText' form field
    hashedSeed = request.form.get("hashedSeed", "").strip()
    clientSeed = request.form.get("clientSeed", "").strip()
    nonce_str = request.form.get("nonce", "").strip()
    speed_str = request.form.get("speed", "").strip()

    # Basic validation
    if not hashedSeed or not clientSeed or not nonce_str:
        return jsonify({"error": "missing required fields (hashedSeed, clientSeed, nonce)"}), 400
    try:
        nonce = int(nonce_str)
    except Exception:
        return jsonify({"error": "nonce must be an integer"}), 400
    try:
        max_speed = int(speed_str) if speed_str else 20000
    except Exception:
        max_speed = 20000

    # Read wordlist
    wordlist_lines = []
    if 'wordlist' in request.files:
        file = request.files['wordlist']
        wordlist_lines = read_wordlist_from_file(file)
    elif 'wordlistText' in request.form:
        text = request.form.get('wordlistText') or ""
        wordlist_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    else:
        return jsonify({"error": "no wordlist provided"}), 400

    # Create job
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'processed': 0,
        'total': len(wordlist_lines),
        'done': False,
        'paused': False,
        'speed_per_min': 0,
        'eta_seconds': None,
        'match': None,
        'roll': None,
        'start_time': None,
        'status': 'queued',
        'lock': threading.Lock()
    }

    # Launch background thread (daemon so it won't block process stop)
    t = threading.Thread(target=process_job, args=(job_id, wordlist_lines, hashedSeed, clientSeed, nonce, max_speed), daemon=True)
    t.start()

    return jsonify({"job_id": job_id}), 200

@app.route("/progress/<job_id>", methods=["GET"])
def get_progress(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    # copy fields into response
    return jsonify({
        "processed": job.get('processed', 0),
        "total": job.get('total', 0),
        "speed": round(job.get('speed_per_min', 0), 2),
        "eta": job.get('eta_seconds'),
        "done": bool(job.get('done')),
        "match": job.get('match'),
        "roll": job.get('roll'),
        "status": job.get('status')
    }), 200

@app.route("/pause/<job_id>", methods=["GET","POST"])
def pause_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    with job['lock']:
        job['paused'] = True
        job['status'] = 'paused'
    return jsonify({"status": "paused", "job_id": job_id}), 200

@app.route("/resume/<job_id>", methods=["GET","POST"])
def resume_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    with job['lock']:
        job['paused'] = False
        job['status'] = 'running'
    return jsonify({"status": "resumed", "job_id": job_id}), 200

# Static files route (if needed)
@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory(os.path.join(app.root_path, "static"), fname)

# -------------------------
# Run server
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
