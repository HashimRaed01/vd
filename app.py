import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, redirect, url_for, session, request, send_from_directory
from flask_socketio import SocketIO, emit
from flask_dance.contrib.google import make_google_blueprint, google
import os
import yt_dlp
import uuid
import json
import threading
from datetime import datetime

app = Flask(__name__)
app.secret_key = "supersecret"
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

DOWNLOAD_DIR = "downloads"
HISTORY_FILE = "download_history.json"
WAIT_SECONDS = 30
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Google login setup (only triggered when user clicks)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
google_bp = make_google_blueprint(
    client_id="YOUR_CLIENT_ID",
    client_secret="YOUR_CLIENT_SECRET",
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/userinfo.email"
    ],
    redirect_to="login"
)
app.register_blueprint(google_bp, url_prefix="/login")

@app.before_request
def track_user():
    if "user_email" in session:
        session.setdefault("plan", "premium")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/login")
def login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    if "user_email" not in session:
        try:
            resp = google.get("/oauth2/v2/userinfo")
            if resp.ok:
                user_info = resp.json()
                session["user_email"] = user_info["email"]
                session["plan"] = "premium"
        except:
            session.clear()
            return redirect(url_for("home"))
    return redirect(url_for("premium"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/premium")
def premium():
    return render_template("premium.html")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html")

@app.route("/resolutions", methods=["POST"])
def detect_platform():
    url = request.form["url"]
    download_id = str(uuid.uuid4())
    if "youtube.com" in url or "youtu.be" in url:
        return redirect(url_for("get_youtube_resolutions", url=url, uuid=download_id))
    elif "tiktok.com" in url:
        return redirect(url_for("select_tiktok", url=url, uuid=download_id))
    elif "instagram.com" in url:
        return redirect(url_for("select_instagram", url=url, uuid=download_id))
    return "❌ Unsupported platform."

@app.route("/youtube")
def get_youtube_resolutions():
    url = request.args.get("url")
    download_id = request.args.get("uuid")
    resolutions = []
    premium_resolutions = []
    audio_formats = []
    title = ""
    is_logged_in = "user_email" in session

    try:
        with yt_dlp.YoutubeDL({}) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])
            title = info.get("title", "Video")

            best_formats = {}

            for f in formats:
                height = f.get("height")
                ext = f.get("ext", "").lower()
                vcodec = f.get("vcodec")
                acodec = f.get("acodec")
                tbr = f.get("tbr") or 0
                filesize = f.get("filesize") or 0
                format_id = f.get("format_id")

                if height and vcodec != "none" and ext in ["mp4", "webm"]:
                    best_formats.setdefault(height, {})
                    current_best = best_formats[height].get(ext)
                    is_better = False
                    if not current_best:
                        is_better = True
                    else:
                        current_tbr = current_best.get("tbr") or 0
                        current_size = current_best.get("filesize") or 0
                        if tbr > current_tbr or (tbr == current_tbr and filesize > current_size):
                            is_better = True
                    if is_better:
                        best_formats[height][ext] = {
                            "id": format_id,
                            "label": f"{height}p {ext.upper()} - {round(filesize/1e6,1)} MB" if filesize else f"{height}p {ext.upper()}",
                            "filesize": filesize,
                            "tbr": tbr
                        }

                elif acodec != "none" and vcodec == "none":
                    abr = f.get("abr") or 0
                    current_best_audio = audio_formats[0] if audio_formats else None
                    current_score = current_best_audio.get("abr", 0) if current_best_audio else 0
                    if not current_best_audio or abr > current_score:
                        label = f"{ext.upper()} Audio - {round(filesize/1e6, 1)} MB" if filesize else f"{ext.upper()} Audio"
                        audio_formats = [{
                            "id": format_id,
                            "label": label,
                            "abr": abr
                        }]

            for height in sorted(best_formats.keys()):
                for ext in ["mp4", "webm"]:
                    f = best_formats[height].get(ext)
                    if not f:
                        continue
                    if height <= 1080:
                        resolutions.append(f)
                    else:
                        premium_resolutions.append(f)

        return render_template("select_resolution.html", url=url, title=title,
                               video_formats=resolutions,
                               premium_formats=premium_resolutions,
                               audio_formats=audio_formats,
                               uuid=download_id,
                               is_logged_in=is_logged_in)
    except Exception as e:
        return f"Error: {str(e)}"

@app.route("/select_tiktok")
def select_tiktok():
    url = request.args.get("url")
    uuid_val = request.args.get("uuid")
    formats = [{"id": "best", "label": "Best Quality"}]
    return render_template("select_tiktok.html", url=url, video_formats=formats, audio_formats=[], uuid=uuid_val)

@app.route("/select_instagram")
def select_instagram():
    url = request.args.get("url")
    uuid_val = request.args.get("uuid")
    formats = [{"id": "best", "label": "Best Quality"}]
    return render_template("select_instagram.html", url=url, formats=formats, uuid=uuid_val)

@app.route("/wait")
def wait_page():
    uuid_val = request.args.get("uuid")
    url = request.args.get("url")
    format_id = request.args.get("format_id")
    title = request.args.get("title", "Download")
    return render_template("wait.html", 
                         uuid=uuid_val,
                         url=url,
                         format_id=format_id,
                         title=title,
                         seconds=WAIT_SECONDS)

@app.route("/fetch_file/<download_id>")
def fetch_file(download_id):
    folder = os.path.join(DOWNLOAD_DIR, download_id)
    if not os.path.exists(folder):
        return "File not found", 404
    for f in os.listdir(folder):
        if f.endswith((".mp4", ".webm", ".mp3")):
            return send_from_directory(folder, f, as_attachment=True)
    return "No downloadable file found", 404

@app.route("/history")
def history():
    if not os.path.exists(HISTORY_FILE):
        return render_template("history.html", history=[])
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
    return render_template("history.html", history=history)

def save_download_history(entry):
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w") as f:
            json.dump([], f)
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
    history.insert(0, entry)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history[:100], f, indent=2)

def start_download(url, format_id, download_id, is_logged_in):
    def download_task():
        try:
            with yt_dlp.YoutubeDL({}) as ydl:
                info = ydl.extract_info(url, download=False)
                f = next((f for f in info["formats"] if f["format_id"] == format_id), None)
                height = f.get("height", 0) if f else 0
                if height > 1080 and not is_logged_in:
                    socketio.emit("redirect", {"url": "/pricing"})
                    return

            temp_dir = os.path.join(DOWNLOAD_DIR, download_id)
            os.makedirs(temp_dir, exist_ok=True)

            def progress_hook(d):
                if d["status"] == "downloading":
                    percent = d.get("_percent_str", "0%").strip()
                    socketio.emit("progress", {"percent": percent})
                elif d["status"] == "finished":
                    socketio.emit("progress", {"percent": "100%"})
                    socketio.emit("done", {"message": "Download finished."})

            ydl_opts = {
                "format": format_id,
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
                "progress_hooks": [progress_hook],
                "merge_output_format": "mp4"
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                ydl.download([url])
                title = info.get("title", "Untitled")

            platform = "YouTube" if "youtube" in url else "TikTok" if "tiktok" in url else "Instagram"
            save_download_history({
                "url": url,
                "format_id": format_id,
                "platform": platform,
                "uuid": download_id,
                "title": title,
                "timestamp": datetime.now().strftime("%Y-%m-%d")
            })

            socketio.emit("completed", {"message": "✅ Download complete!"})
            
        except Exception as e:
            socketio.emit("error", {"message": str(e)})

    threading.Thread(target=download_task).start()

@socketio.on("start_download")
def handle_download(data):
    url = data.get("url")
    format_id = data.get("format_id")
    download_id = data.get("download_id")
    title = data.get("title", "Download")
    plan = session.get("plan", "free")
    is_logged_in = "user_email" in session

    if plan == "free":
        socketio.emit("redirect", {
            "url": f"/wait?uuid={download_id}&url={url}&format_id={format_id}&title={title}"
        })
        return

    start_download(url, format_id, download_id, is_logged_in)

@socketio.on("start_delayed_download")
def handle_delayed_download(data):
    url = data.get("url")
    format_id = data.get("format_id")
    download_id = data.get("download_id")
    is_logged_in = "user_email" in session

    if not all([url, format_id, download_id]):
        socketio.emit("error", {"message": "Missing required download information"})
        return

    start_download(url, format_id, download_id, is_logged_in)

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
