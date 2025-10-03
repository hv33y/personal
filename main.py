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

        # Extract location (try latest, else fallback to older events)
        location = extract_location(latest)
        if not location:
            for act in activities:
                location = extract_location(act)
                if location:
                    break

        if not location:
            location = "No location found"

        return status, location

    except Exception as e:
        print("⚠️ Parsing Error:", e)
        print("UPS API Raw:", json.dumps(data, indent=2))
        return f"Error parsing: {e}", "No location found"


def extract_location(activity):
    """Helper: Build a readable location string from activity data"""
    if "activityLocation" not in activity:
        return ""
    addr = activity["activityLocation"].get("address", {})
    parts = [addr.get("city"), addr.get("stateProvince"), addr.get("country")]
    return ", ".join([p for p in parts if p])


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
#  Run Script
# ==============================================================
if __name__ == "__main__":
    main()