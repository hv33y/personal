import os
import json
import uuid
import base64
import requests

# ==============================================================
#  Telegram Setup
# ==============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# ==============================================================
#  UPS API Setup
# ==============================================================
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
        activities = data["trackResponse"]["shipment"][0]["package"][0]["activity"]
        latest = activities[0]

        status = latest["status"]["description"]
        location = extract_location(latest)

        if location == "No location found":
            for act in activities[1:]:
                location = extract_location(act)
                if location != "No location found":
                    break

        return status, location
    except Exception as e:
        print("⚠️ Parsing Error:", e)
        return f"Error parsing: {e}", "No location found"

def extract_location(activity):
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

    # shipment origin fallback
    if not loc_parts:
        shipment_info = activity.get("shipment", {}).get("shipFrom", {}).get("address", {})
        for key in ["city", "stateProvince", "country"]:
            if shipment_info.get(key):
                loc_parts.append(shipment_info[key])

    if not loc_parts:
        loc_parts.append("No location found")

    return ", ".join(loc_parts)

# ==============================================================
#  Telegram Sending
# ==============================================================

def send_telegram(message):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        resp = requests.post(TELEGRAM_URL, json=payload)
        if resp.status_code == 200:
            print("📤 Telegram message sent!")
        else:
            print(f"❌ Telegram Error {resp.status_code}: {resp.text}")
    except Exception as e:
        print("❌ Telegram Exception:", e)

# ==============================================================
#  Message Formatter
# ==============================================================

def format_message(nickname, status, location):
    return "\n".join([
        f"📦 {nickname}",
        f"Status: {status}",
        f"Location: {location}"
    ])

# ==============================================================
#  Main Logic
# ==============================================================

def main():
    global last_status
    token = get_access_token()

    for idx, tracking in enumerate(TRACKING_NUMBERS):
        nickname = (
            TRACKING_NAMES[idx].strip()
            if idx < len(TRACKING_NAMES) and TRACKING_NAMES[idx].strip()
            else tracking.strip()
        )

        status, location = get_tracking_status(tracking, token)

        if status and last_status.get(tracking) != status:
            message = format_message(nickname, status, location)
            print("🔔", message.replace("\n", " | "))
            send_telegram(message)
            last_status[tracking] = status
        else:
            print(f"ℹ️ No new update for {nickname}: {status}")

    with open(STATUS_FILE, "w") as f:
        json.dump(last_status, f)

# ==============================================================
#  Run Script
# ==============================================================

if __name__ == "__main__":
    main()