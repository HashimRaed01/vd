# Final full app.py is ready to send.
# Writing it in a way that matches all user constraints:
# - Free access to all pages
# - Wait screen for <=1080p
# - Locked 2K/4K with pricing redirect
# - Optional login/upgrade
# - Stable and clean

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

eventlet.monkey_patch()

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
    resolutions, premium_resolutions, audio_formats, title = [], [], [], ""
    is_logged_in = "user_email" in session

    try:
        with yt_dlp.YoutubeDL({}) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])
            title = info.get("title", "Video")
            seen = set()
            for f in formats:
                height = f.get("height")
                ext = f.get("ext")
                filesize = f.get("filesize")
                format_id = f.get("format_id")
                if f.get("vcodec") != "none" and ext and height:
                    label = f"{height}p {ext.upper()} - {round((filesize or 0)/1e6, 1)} MB" if filesize else f"{height}p {ext.upper()}"
                    entry = {"id": format_id, "label": label, "height": height}
                    if format_id not in seen:
                        if height <= 1080:
                            resolutions.append(entry)
                        else:
                            premium_resolutions.append(entry)
                        seen.add(format_id)
                elif f.get("acodec") != "none" and f.get("vcodec") == "none":
                    label = f"{ext.upper()} Audio - {round((filesize or 0)/1e6, 1)} MB" if filesize else f"{ext.upper()} Audio"
                    audio_formats.append({"id": f.get("format_id"), "label": label})

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
    title = request.args.get("title", "Download")
    return render_template("wait.html", uuid=uuid_val, title=title, seconds=WAIT_SECONDS)

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

@socketio.on("start_download")
def handle_download(data):
    print(f"⏬ Download started with data: {data}")  # Debug log
    url = data.get("url")
    format_id = data.get("format_id")
    download_id = data.get("download_id")
    plan = session.get("plan", "free")
    is_logged_in = "user_email" in session
    
    print(f"📥 URL: {url}")  # Debug log
    print(f"🎥 Format ID: {format_id}")  # Debug log
    print(f"🔑 Download ID: {download_id}")  # Debug log

    def download_task():
        try:
            print("🚀 Starting download task...")  # Debug log
            with yt_dlp.YoutubeDL({}) as ydl:
                info = ydl.extract_info(url, download=False)
                f = next((f for f in info["formats"] if f["format_id"] == format_id), None)
                height = f.get("height", 0) if f else 0
                if height > 1080 and not is_logged_in:
                    print("🔒 Premium content requested by free user")  # Debug log
                    socketio.emit("redirect", {"url": "/pricing"})
                    return

            temp_dir = os.path.join(DOWNLOAD_DIR, download_id)
            os.makedirs(temp_dir, exist_ok=True)

            def progress_hook(d):
                if d["status"] == "downloading":
                    percent = d.get("_percent_str", "0%").strip()
                    print(f"📊 Download progress: {percent}")  # Debug log
                    socketio.emit("progress", {"percent": percent})
                elif d["status"] == "finished":
                    print("✅ Download finished")  # Debug log
                    socketio.emit("progress", {"percent": "100%"})
                    socketio.emit("done", {"message": "Download finished."})

            ydl_opts = {
                "format": format_id,
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
                "progress_hooks": [progress_hook],
                "merge_output_format": "mp4"
            }

            print(f"⚙️ Using yt-dlp options: {ydl_opts}")  # Debug log
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

            if plan == "free":
                print(f"🕒 Free user - redirecting to wait page")  # Debug log
                socketio.emit("redirect", {"url": f"/wait?uuid={download_id}&title={title}"})
            else:
                print(f"✨ Premium user - download complete")  # Debug log
                socketio.emit("completed", {"message": "✅ Download complete!"})
        except Exception as e:
            print(f"❌ Error in download task: {str(e)}")  # Debug log
            socketio.emit("error", {"message": str(e)})

    threading.Thread(target=download_task).start()
    print("🧵 Download thread started")  # Debug log

if __name__ == "__main__":
    socketio.run(app, debug=True, host="127.0.0.1", port=5000, use_reloader=False)

