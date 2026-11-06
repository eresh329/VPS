
import requests
import sqlite3
import hashlib
import time
import threading
import html
import json
import config

from datetime import datetime

# ============== CONFIG ==============

MAIN_TOKEN = config.BOT_TOKEN
CLOUD = config.CLOUD_NAME
API_KEY = config.API_KEY
API_SECRET = config.API_SECRET
ADMINS = getattr(config, "ADMIN_IDS", [])

DB = "hosting_v4.db"
API = "https://api.telegram.org/bot{}"
UPLOAD = f"https://api.cloudinary.com/v1_1/{CLOUD}/image/upload"
DESTROY = f"https://api.cloudinary.com/v1_1/{CLOUD}/image/destroy"

# ============== DATABASE ==============

def database():
    return sqlite3.connect(DB, timeout=30)


def init_db():
    with database() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS clones(
            bot_id INTEGER PRIMARY KEY,
            token TEXT,
            username TEXT,
            active INTEGER DEFAULT 1
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS images(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER,
            user_id INTEGER,
            public_id TEXT,
            url TEXT,
            size INTEGER,
            width INTEGER,
            height INTEGER,
            format TEXT,
            date TEXT,
            active INTEGER DEFAULT 1
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            bot_id INTEGER,
            user_id INTEGER,
            name TEXT,
            username TEXT,
            PRIMARY KEY(bot_id,user_id)
        )
        """)


# ============== API ==============

def call(token, method, data=None, timeout=40):
    r = requests.post(
        API.format(token) + "/" + method,
        data=data or {},
        timeout=timeout
    )
    r.raise_for_status()
    result = r.json()

    if not result.get("ok"):
        raise Exception(result.get("description", "Telegram API error"))

    return result["result"]


def get_me(token):
    return call(token, "getMe")


def send(token, chat, text, keyboard=None):
    data = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML"
    }

    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)

    return call(token, "sendMessage", data)


def menu(is_admin=False):
    rows = [
        [{"text": "🖼️ Upload Image"}],
        [
            {"text": "📂 My Gallery"},
            {"text": "🕒 Upload History"}
        ],
        [
            {"text": "📊 My Statistics"},
            {"text": "👤 My Profile"}
        ],
        [{"text": "🗑️ Delete Last Image"}],
        [{"text": "❓ Help & Support"}]
    ]

    if is_admin:
        rows.extend([
            [{"text": "🤖 Clone Bot"}],
            [
                {"text": "📋 Clone List"},
                {"text": "⛔ Stop Clone"}
            ],
            [{"text": "📈 Admin Statistics"}]
        ])

    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "is_persistent": True
    }


# ============== CLOUDINARY ==============

def signature(params):
    text = "&".join(
        f"{k}={v}" for k, v in sorted(params.items())
    ) + API_SECRET

    return hashlib.sha1(text.encode()).hexdigest()


def upload_image(data):
    timestamp = int(time.time())

    params = {
        "folder": "telegram_images",
        "timestamp": timestamp
    }

    payload = {
        **params,
        "api_key": API_KEY,
        "signature": signature(params)
    }

    files = {
        "file": ("image.jpg", data, "application/octet-stream")
    }

    r = requests.post(
        UPLOAD,
        data=payload,
        files=files,
        timeout=120
    )

    r.raise_for_status()
    return r.json()


def delete_cloud(public_id):
    timestamp = int(time.time())

    params = {
        "public_id": public_id,
        "timestamp": timestamp
    }

    payload = {
        **params,
        "api_key": API_KEY,
        "signature": signature(params)
    }

    r = requests.post(DESTROY, data=payload, timeout=60)
    r.raise_for_status()

    result = r.json()

    return result.get("result") in ("ok", "not found")


# ============== SAVE DATA ==============

def save_user(bot_id, user):
    with database() as con:
        con.execute("""
        INSERT INTO users VALUES(?,?,?,?)
        ON CONFLICT(bot_id,user_id) DO UPDATE SET
        name=excluded.name,
        username=excluded.username
        """, (
            bot_id,
            user["id"],
            user.get("first_name", ""),
            user.get("username", "")
        ))


def save_image(bot_id, user_id, result, size):
    with database() as con:
        cur = con.execute("""
        INSERT INTO images
        (bot_id,user_id,public_id,url,size,width,height,format,date)
        VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            bot_id,
            user_id,
            result["public_id"],
            result["secure_url"],
            size,
            result.get("width", 0),
            result.get("height", 0),
            result.get("format", ""),
            datetime.now().strftime("%Y-%m-%d %H:%M")
        ))

        return cur.lastrowid


# ============== PHOTO UPLOAD ==============

def photo_upload(token, bot_id, message):
    chat = message["chat"]["id"]
    uid = message["from"]["id"]

    send(token, chat, "⏳ <b>Uploading your image...</b>")

    try:
        photo = message["photo"][-1]

        info = call(token, "getFile", {
            "file_id": photo["file_id"]
        })

        path = info["file_path"]

        r = requests.get(
            f"https://api.telegram.org/file/bot{token}/{path}",
            timeout=120
        )
        r.raise_for_status()

        image_data = r.content

        result = upload_image(image_data)

        image_id = save_image(
            bot_id, uid, result, len(image_data)
        )

        url = result["secure_url"]

        width = result.get("width", 0)
        height = result.get("height", 0)
        fmt = result.get("format", "unknown").upper()

        text = (
            "✅ <b>UPLOAD SUCCESSFUL!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Image ID: <code>{image_id}</code>\n"
            f"📦 Size: {len(image_data)/1024:.2f} KB\n"
            f"📐 Resolution: {width} × {height}\n"
            f"🖼️ Format: {fmt}\n\n"
            "🔗 <b>DIRECT IMAGE LINK</b>\n"
            f'<a href="{html.escape(url, quote=True)}">OPEN IMAGE</a>\n\n'
            f"<code>{html.escape(url)}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Your image is saved successfully!"
        )

        # Preview
        try:
            call(token, "sendPhoto", {
                "chat_id": chat,
                "photo": url,
                "caption": "🖼️ Your uploaded image"
            })
        except Exception as e:
            print("Preview:", e)

        # Send link and bottom keyboard
        send(token, chat, text, menu(bot_id == MAIN_BOT_ID))

    except Exception as e:
        print("Upload error:", e)
        send(token, chat, "❌ Upload failed. Please try again.")


# ============== HISTORY ==============

def show_history(token, bot_id, chat, uid):
    with database() as con:
        rows = con.execute("""
        SELECT id,url,format,size,date
        FROM images
        WHERE bot_id=? AND user_id=? AND active=1
        ORDER BY id DESC LIMIT 10
        """, (bot_id, uid)).fetchall()

    if not rows:
        return send(token, chat, "📂 Your gallery is empty.", menu(bot_id == MAIN_BOT_ID))

    for row in rows:
        iid, url, fmt, size, date = row

        text = (
            f"🖼️ <b>IMAGE #{iid}</b>\n"
            f"📁 Format: {html.escape(fmt or '')}\n"
            f"📦 Size: {size/1024:.2f} KB\n"
            f"🕒 Date: {date}\n\n"
            f'<a href="{html.escape(url, quote=True)}">Open Image</a>\n'
            f"<code>{html.escape(url)}</code>"
        )

        send(token, chat, text)


# ============== DELETE LAST ==============

def delete_last(token, bot_id, chat, uid):
    with database() as con:
        row = con.execute("""
        SELECT id,public_id FROM images
        WHERE bot_id=? AND user_id=? AND active=1
        ORDER BY id DESC LIMIT 1
        """, (bot_id, uid)).fetchone()

    if not row:
        return send(token, chat, "No image found.")

    iid, public_id = row

    try:
        if delete_cloud(public_id):
            with database() as con:
                con.execute(
                    "UPDATE images SET active=0 WHERE id=?",
                    (iid,)
                )

            send(token, chat, "✅ Your last image has been deleted.")

        else:
            send(token, chat, "❌ Image could not be deleted.")

    except Exception as e:
        print("Delete:", e)
        send(token, chat, "❌ Delete failed.")


# ============== STATISTICS ==============

def user_stats(token, bot_id, chat, uid):
    with database() as con:
        total, size = con.execute("""
        SELECT COUNT(*),COALESCE(SUM(size),0)
        FROM images
        WHERE bot_id=? AND user_id=? AND active=1
        """, (bot_id, uid)).fetchone()

    send(
        token, chat,
        "📊 <b>YOUR STATISTICS</b>\n\n"
        f"🖼️ Images: {total}\n"
        f"💾 Storage: {size/1024:.2f} KB"
    )


def admin_stats(token, chat):
    with database() as con:
        users = con.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        images = con.execute(
            "SELECT COUNT(*) FROM images WHERE active=1"
        ).fetchone()[0]

        clones = con.execute(
            "SELECT COUNT(*) FROM clones WHERE active=1"
        ).fetchone()[0]

        storage = con.execute(
            "SELECT COALESCE(SUM(size),0) FROM images WHERE active=1"
        ).fetchone()[0]

    send(
        token, chat,
        "📈 <b>MASTER ADMIN STATISTICS</b>\n\n"
        f"👥 Users: {users}\n"
        f"🖼️ Images: {images}\n"
        f"🤖 Active Clones: {clones}\n"
        f"💾 Storage: {storage/1024/1024:.2f} MB"
    )


# ============== CLONE SYSTEM ==============

clone_threads = {}
clone_events = {}
clone_lock = threading.Lock()
clone_states = {}
stop_states = {}

MAIN_BOT_ID = 0


def add_clone(token, me):
    bot_id = me["id"]

    with database() as con:
        con.execute("""
        INSERT INTO clones(bot_id,token,username,active)
        VALUES(?,?,?,1)
        ON CONFLICT(bot_id) DO UPDATE SET
        token=excluded.token,
        username=excluded.username,
        active=1
        """, (
            bot_id,
            token,
            me.get("username", "")
        ))

    start_clone(bot_id, token)


def start_clone(bot_id, token):
    with clone_lock:
        old = clone_events.get(bot_id)

        if old and not old.is_set():
            return

        event = threading.Event()
        clone_events[bot_id] = event

        thread = threading.Thread(
            target=bot_worker,
            args=(bot_id, token, event),
            daemon=True
        )

        clone_threads[bot_id] = thread
        thread.start()


def stop_clone(bot_id):
    with clone_lock:
        event = clone_events.get(bot_id)

        if event:
            event.set()

    with database() as con:
        con.execute(
            "UPDATE clones SET active=0 WHERE bot_id=?",
            (bot_id,)
        )


def clone_list(token, chat):
    with database() as con:
        rows = con.execute("""
        SELECT bot_id,username,active
        FROM clones ORDER BY bot_id DESC
        """).fetchall()

    if not rows:
        return send(token, chat, "No clone bots found.")

    text = "🤖 <b>YOUR CLONE BOTS</b>\n\n"

    for bid, username, active in rows:
        status = "🟢 Online" if active else "🔴 Stopped"
        text += (
            f"@{html.escape(username or 'unknown')}\n"
            f"ID: <code>{bid}</code>\n"
            f"Status: {status}\n\n"
        )

    send(token, chat, text)


def verify_clone(token):
    # Verify using Telegram's official getMe API
    me = get_me(token)

    if not me.get("is_bot"):
        raise Exception("This is not a bot token")

    return me


# ============== MESSAGE HANDLER ==============

def handle_message(token, bot_id, message):
    chat = message["chat"]["id"]
    user = message["from"]
    uid = user["id"]

    save_user(bot_id, user)

    is_main = bot_id == MAIN_BOT_ID
    is_admin = is_main and uid in ADMINS

    text = message.get("text", "").strip()

    # Admin clone token verification flow
    if is_admin and uid in clone_states:
        clone_states.pop(uid, None)

        if message.get("message_id"):
            try:
                call(token, "deleteMessage", {
                    "chat_id": chat,
                    "message_id": message["message_id"]
                })
            except Exception:
                pass

        try:
            me = verify_clone(text)

            if me["id"] == MAIN_BOT_ID:
                raise Exception("Main bot cannot be cloned")

            add_clone(text, me)

            send(
                token, chat,
                "✅ <b>CLONE CREATED SUCCESSFULLY!</b>\n\n"
                f"🤖 Bot: @{html.escape(me.get('username',''))}\n"
                f"🆔 ID: <code>{me['id']}</code>\n\n"
                "Your new Image Hosting Bot is running!",
                menu(True)
            )

        except Exception as e:
            print("Clone verify:", e)
            send(
                token, chat,
                "❌ <b>INVALID TOKEN!</b>\n\n"
                "Token verification failed. Check the token and try again.",
                menu(True)
            )

        return

    # Stop clone confirmation
    if is_admin and uid in stop_states:
        stop_states.pop(uid, None)

        try:
            target_id = int(text)

            if target_id == MAIN_BOT_ID:
                raise Exception("Cannot stop master bot")

            stop_clone(target_id)

            send(token, chat, "⛔ Clone stopped.", menu(True))

        except Exception:
            send(token, chat, "❌ Invalid Bot ID.", menu(True))

        return

    if message.get("photo"):
        return photo_upload(token, bot_id, message)

    if text == "🖼️ Upload Image":
        send(token, chat, "📸 Send your image now.", menu(is_admin))

    elif text in ("📂 My Gallery", "🕒 Upload History"):
        show_history(token, bot_id, chat, uid)

    elif text == "📊 My Statistics":
        user_stats(token, bot_id, chat, uid)

    elif text == "👤 My Profile":
        send(
            token, chat,
            "👤 <b>YOUR PROFILE</b>\n\n"
            f"Name: {html.escape(user.get('first_name',''))}\n"
            f"Username: @{html.escape(user.get('username','Not set'))}\n"
            f"User ID: <code>{uid}</code>",
            menu(is_admin)
        )

    elif text == "🗑️ Delete Last Image":
        delete_last(token, bot_id, chat, uid)

    elif text == "❓ Help & Support":
        send(
            token, chat,
            "❓ <b>HELP CENTER</b>\n\n"
            "1. Upload Image চাপো।\n"
            "2. ছবি পাঠাও।\n"
            "3. Direct Link পেয়ে যাবে।\n"
            "4. History থেকে পুরোনো Link পাবে।",
            menu(is_admin)
        )

    elif is_admin and text == "🤖 Clone Bot":
        clone_states[uid] = True

        send(
            token, chat,
            "🤖 <b>CLONE BOT</b>\n\n"
            "Send your new Telegram Bot Token.\n\n"
            "Token will be verified through Telegram getMe API.",
            menu(True)
        )

    elif is_admin and text == "📋 Clone List":
        clone_list(token, chat)

    elif is_admin and text == "⛔ Stop Clone":
        stop_states[uid] = True

        send(
            token, chat,
            "Send the numeric Bot ID you want to stop.",
            menu(True)
        )

    elif is_admin and text == "📈 Admin Statistics":
        admin_stats(token, chat)

    elif text == "Back to Menu":
        send(token, chat, "🏠 Main Menu", menu(is_admin))

    elif text:
        send(
            token, chat,
            "Please use the keyboard buttons below.",
            menu(is_admin)
        )


# ============== POLLING WORKER ==============

def bot_worker(bot_id, token, event):
    print("Started clone:", bot_id)

    offset = None

    while not event.is_set():
        try:
            params = {
                "timeout": 20,
                "allowed_updates": json.dumps(["message"])
            }

            if offset is not None:
                params["offset"] = offset

            r = requests.get(
                API.format(token) + "/getUpdates",
                params=params,
                timeout=30
            )

            r.raise_for_status()
            result = r.json()

            if not result.get("ok"):
                time.sleep(3)
                continue

            for update in result["result"]:
                offset = update["update_id"] + 1

                try:
                    if "message" in update:
                        handle_message(
                            token,
                            bot_id,
                            update["message"]
                        )
                except Exception as e:
                    print("Clone message error:", e)

        except Exception as e:
            print("Clone connection error:", bot_id, e)
            time.sleep(5)

    print("Stopped clone:", bot_id)


# ============== MAIN BOT ==============

def main():
    global MAIN_BOT_ID

    init_db()

    main_me = get_me(MAIN_TOKEN)
    MAIN_BOT_ID = main_me["id"]

    # Load all active clones after restart
    with database() as con:
        clones = con.execute("""
        SELECT bot_id,token FROM clones WHERE active=1
        """).fetchall()

    for bot_id, token in clones:
        try:
            me = verify_clone(token)

            if me["id"] == bot_id:
                start_clone(bot_id, token)

        except Exception as e:
            print("Could not start clone:", bot_id, e)

    print("━━━━━━━━━━━━━━━━━━━━")
    print(" IMAGE HOSTING V4.0")
    print(" MAIN BOT:", main_me.get("username"))
    print(" CLONES:", len(clones))
    print("━━━━━━━━━━━━━━━━━━━━")

    # Main bot polling
    call(MAIN_TOKEN, "deleteWebhook", {
        "drop_pending_updates": "true"
    })

    offset = None

    while True:
        try:
            params = {
                "timeout": 20,
                "allowed_updates": json.dumps(["message"])
            }

            if offset is not None:
                params["offset"] = offset

            r = requests.get(
                API.format(MAIN_TOKEN) + "/getUpdates",
                params=params,
                timeout=30
            )

            r.raise_for_status()
            result = r.json()

            if not result.get("ok"):
                time.sleep(3)
                continue

            for update in result["result"]:
                offset = update["update_id"] + 1

                try:
                    if "message" in update:
                        handle_message(
                            MAIN_TOKEN,
                            MAIN_BOT_ID,
                            update["message"]
                        )
                except Exception as e:
                    print("Main message error:", e)

        except KeyboardInterrupt:
            print("Bot stopped!")
            break

        except Exception as e:
            print("Main connection error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
