import os
import json
import uuid
import base64
import requests
from twilio.rest import Client

# ==============================================================
#  Twilio Setup
# ==============================================================
ACCOUNT_SID     = os.getenv("TWILIO_SID")
AUTH_TOKEN      = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER   = os.getenv("TWILIO_PHONE")
MY_NUMBER       = os.getenv("MY_PHONE")

twilio_client = Client(ACCOUNT_SID, AUTH_TOKEN)

# ==============================================================
#  UPS API Setup
# ==============================================================
UPS_CLIENT_ID     = os.getenv("UPS_CLIENT_ID")
UPS_CLIENT_SECRET = os.getenv("UPS_CLIENT_SECRET")

UPS_AUTH_URL   = "https://onlinetools.ups.com/security/v1/oauth/token"
UPS_TRACK_URL  = "https://onlinetools.ups.com/api/track/v1/details"

# Tracking numbers & optional nicknames (comma-separated in env vars)
TRACKING_NUMBERS  = os.getenv("UPS_TRACKINGS").split(",")
TRACKING_NAMES    = os.getenv("UPS_NICKNAMES", "").split(",")

# Local cache to prevent duplicate SMS
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
    """Request OAuth token from UPS API"""
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
    """Fetch latest UPS tracking status & location"""
    url = f"{UPS_TRACK_URL}/{tracking_number.strip()}"
    headers = {
        "Authorization": f"Bearer {token}",
        "transId": str(uuid.uuid4()),
        "transactionSrc": "ups-sms-notifier"
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()

    if "trackResponse" not in data:
        print("⚠️ UPS API Debug:", json.dumps(data, indent=2))
        return "No tracking info", "No location found"

    try:
        activities = data["trackResponse"]["shipment"][0]["package"][0]["activity"]
        latest     = activities[0]

        # Extract status
        status = latest["status"]["description"]

        # Extract location using robust helper
        location = extract_location(latest)

        # Fallback: older activities
        if location == "No location found":
            for act in activities[1:]:
                location = extract_location(act)
                if location != "No location found":
                    break

        return status, location

    except Exception as e:
        print("⚠️ Parsing Error:", e)
        print("UPS API Raw:", json.dumps(data, indent=2))
        return f"Error parsing: {e}", "No location found"


def extract_location(activity):
    """Build a readable location string from activity data with robust fallbacks"""
    loc_parts = []

    # 1️⃣ Try normal address fields
    addr = activity.get("activityLocation", {}).get("address", {})
    for key in ["city", "stateProvince", "country"]:
        if addr.get(key):
            loc_parts.append(addr[key])

    # 2️⃣ Try facility description
    if not loc_parts:
        desc = activity.get("activityLocation", {}).get("locationTypeDescription")
        if desc:
            loc_parts.append(desc)

    # 3️⃣ Try UPS location code
    if not loc_parts:
        code = activity.get("activityLocation", {}).get("code")
        if code:
            loc_parts.append(code)

    # 4️⃣ Fallback to shipment-level info
    if not loc_parts:
        shipment_info = activity.get("shipment", {}).get("shipFrom", {}).get("address", {})
        if shipment_info:
            for key in ["city", "stateProvince", "country"]:
                if shipment_info.get(key):
                    loc_parts.append(shipment_info[key])

    # Final fallback
    if not loc_parts:
        loc_parts.append("No location found")

    return ", ".join(loc_parts)

# ==============================================================
#  SMS Sending
# ==============================================================

def send_sms(message):
    """Send an SMS via Twilio"""
    try:
        msg = twilio_client.messages.create(
            body=message,
            from_=TWILIO_NUMBER,
            to=MY_NUMBER
        )
        print(f"📤 SMS Sent | SID: {msg.sid} | Status: {msg.status}")
    except Exception as e:
        print("❌ Twilio SMS Error:", e)

# ==============================================================
#  SMS Formatter
# ==============================================================

def format_sms(nickname, status, location):
    """Builds a beautified SMS message"""
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

        # Only send SMS if status changed
        if status and last_status.get(tracking) != status:
            message = format_sms(nickname, status, location)
            print("🔔", message.replace("\n", " | "))  # cleaner logs
            send_sms(message)
            last_status[tracking] = status
        else:
            print(f"ℹ️ No new update for {nickname}: {status}")

    # Save last known statuses
    with open(STATUS_FILE, "w") as f:
        json.dump(last_status, f)

# ==============================================================
#  Run Script
# ==============================================================
if __name__ == "__main__":
    main()