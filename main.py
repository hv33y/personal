import os
import json
import uuid
import base64
import requests
import time
from datetime import datetime
import pytz

# ==============================================================
#  Telegram & UPS Setup
# ==============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL   = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

UPS_CLIENT_ID     = os.getenv("UPS_CLIENT_ID")
UPS_CLIENT_SECRET = os.getenv("UPS_CLIENT_SECRET")
UPS_AUTH_URL      = "https://onlinetools.ups.com/security/v1/oauth/token"
UPS_TRACK_URL     = "https://onlinetools.ups.com/api/track/v1/details"

TRACKING_NUMBERS  = os.getenv("UPS_TRACKINGS").split(",")
TRACKING_NAMES    = os.getenv("UPS_NICKNAMES", "").split(",")

STATUS_FILE = "ups_status.json"
try:
    with open(STATUS_FILE, "r") as f:
        last_status = json.load(f)
except FileNotFoundError:
    last_status = {}

# Toronto timezone
toronto_tz = pytz.timezone("America/Toronto")

# ==============================================================
#  UPS Helpers
# ==============================================================

def get_access_token():
    creds = f"{UPS_CLIENT_ID}:{UPS_CLIENT_SECRET}"
    b64_creds = base64.b64encode(creds.encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64_creds}"
    }
    data = {"grant_type": "client_credentials"}
    response = requests.post(UPS_AUTH_URL, headers=headers, data=data)
    response.raise_for_status()
    return response.json()["access_token"]

def get_tracking_status(tracking_number, token):
    url = f"{UPS_TRACK_URL}/{tracking_number.strip()}"
    headers = {
        "Authorization": f"Bearer {token}",
        "transId": str(uuid.uuid4()),
        "transactionSrc": "ups-telegram-bot"
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()

    if "trackResponse" not in data:
        return "No tracking info", "No location found"

    try:
        shipment_info = data["trackResponse"]["shipment"][0]
        activities = shipment_info["package"][0]["activity"]
        latest = activities[0]

        status = latest["status"]["description"]
        location = extract_location(latest, shipment_info)

        if location == "No location found":
            for act in activities[1:]:
                location = extract_location(act, shipment_info)
                if location != "No location found":
                    break

        return status, location
    except Exception as e:
        print("⚠️ Parsing Error:", e)
        return f"Error parsing: {e}", "No location found"

def extract_location(activity, shipment=None):
    loc_parts = []

    addr = activity.get("activityLocation", {}).get("address", {})
    for key in ["city", "stateProvince", "country"]:
        if addr.get(key):
            loc_parts.append(addr[key])

    if not loc_parts:
        desc = activity.get("activityLocation", {}).get("locationTypeDescription")
        if desc:
            loc_parts.append(desc)

    if not loc_parts:
        code = activity.get("activityLocation", {}).get("code")
        if code:
            loc_parts.append(code)

    if not loc_parts and shipment:
        ship_from = shipment.get("shipFrom", {}).get("address", {})
        for key in ["city", "stateProvince", "country"]:
            if ship_from.get(key):
                loc_parts.append(ship_from[key])

    if not loc_parts and shipment:
        ship_to = shipment.get("shipTo", {}).get("address", {})
        for key in ["city", "stateProvince", "country"]:
            if ship_to.get(key):
                loc_parts.append(ship_to[key])

    if not loc_parts:
        loc_parts.append("No location found")

    return ", ".join(loc_parts)

# ==============================================================
#  Telegram Messaging
# ==============================================================

def send_telegram(message, reply_markup=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
        if resp.status_code != 200:
            print(f"❌ Telegram Error {resp.status_code}: {resp.text}")
        else:
            print("📤 Telegram message sent!")
    except Exception as e:
        print("❌ Telegram Exception:", e)

# ==============================================================
#  Message & Table Formatting
# ==============================================================

def format_message(nickname, status, location, timestamp):
    return "\n".join([
        f"📦 {nickname}",
        f"Status: {status}",
        f"Location: {location}",
        f"Updated: {timestamp}"
    ])

def format_status_table():
    table_lines = []
    table_lines.append("📊 UPS Tracking Status Table\n")
    table_lines.append(f"{'Nickname':<20} | {'Status':<25} | {'Location':<25} | Updated")
    table_lines.append("-" * 80)
    for idx, tracking in enumerate(TRACKING_NUMBERS):
        nickname = (
            TRACKING_NAMES[idx].strip()
            if idx < len(TRACKING_NAMES) and TRACKING_NAMES[idx].strip()
            else tracking.strip()
        )
        record = last_status.get(tracking, {})
        status = record.get("status", "No info")
        location = record.get("location", "—")
        timestamp = record.get("timestamp", "—")
        table_lines.append(f"{nickname:<20} | {status:<25} | {location:<25} | {timestamp}")
    table_lines.append("-" * 80)
    return "\n".join(table_lines)

# ==============================================================
#  UPS Tracking Logic
# ==============================================================

def run_tracking():
    global last_status
    token = get_access_token()
    now = datetime.now(toronto_tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    for idx, tracking in enumerate(TRACKING_NUMBERS):
        nickname = (
            TRACKING_NAMES[idx].strip()
            if idx < len(TRACKING_NAMES) and TRACKING_NAMES[idx].strip()
            else tracking.strip()
        )

        status, location = get_tracking_status(tracking, token)
        previous_status = last_status.get(tracking, {}).get("status")

        if status and previous_status != status:
            message = format_message(nickname, status, location, now)
            print("🔔", message.replace("\n", " | "))
            send_telegram(message)
            last_status[tracking] = {"status": status, "location": location, "timestamp": now}
        else:
            print(f"ℹ️ No new update for {nickname}: {status}")

    with open(STATUS_FILE, "w") as f:
        json.dump(last_status, f, indent=2)

# ==============================================================
#  Telegram Inline Button Handling
# ==============================================================

def get_updates(offset=None):
    url = f"{TELEGRAM_API_URL}/getUpdates?timeout=10"
    if offset:
        url += f"&offset={offset}"
    resp = requests.get(url)
    return resp.json()

def handle_message(update):
    if "message" in update:
        msg = update["message"]
        text = msg.get("text", "")
        chat_id = msg["chat"]["id"]

        if text == "/update":
            send_telegram("🔄 Running manual UPS update...")
            run_tracking()
            table = format_status_table()
            send_telegram(f"✅ Manual update completed:\n\n{table}")
            send_update_button(chat_id)

    elif "callback_query" in update:
        callback = update["callback_query"]
        data = callback["data"]
        chat_id = callback["message"]["chat"]["id"]

        if data == "update_now":
            send_telegram("🔄 Running UPS update from button...")
            run_tracking()
            table = format_status_table()
            send_telegram(f"✅ Manual update completed:\n\n{table}")
            send_update_button(chat_id)

def send_update_button(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{"text": "Update Now 🔄", "callback_data": "update_now"}]
        ]
    }
    send_telegram("Tap the button below to manually update UPS tracking:", reply_markup=keyboard)

# ==============================================================
#  Polling Loop
# ==============================================================

def start_polling():
    last_update_id = None
    while True:
        updates = get_updates(last_update_id)
        for update in updates.get("result", []):
            last_update_id = update["update_id"] + 1
            handle_message(update)
        time.sleep(2)

# ==============================================================
#  Entry Point
# ==============================================================

if __name__ == "__main__":
    # Run initial tracking
    run_tracking()
    # Send initial inline button
    send_update_button(TELEGRAM_CHAT_ID)
    # Start listening for button presses or /update command
    start_polling()