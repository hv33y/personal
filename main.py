import requests
from twilio.rest import Client
import os
import json
import base64
import uuid

# --- Twilio setup ---
account_sid = os.getenv("TWILIO_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE")
my_number = os.getenv("MY_PHONE")

client = Client(account_sid, auth_token)

# --- UPS OAuth setup ---
UPS_CLIENT_ID = os.getenv("UPS_CLIENT_ID")
UPS_CLIENT_SECRET = os.getenv("UPS_CLIENT_SECRET")
tracking_numbers = os.getenv("UPS_TRACKINGS").split(",")  # comma-separated
tracking_nicknames = os.getenv("UPS_NICKNAMES", "").split(",")  # optional

status_file = "ups_status.json"

try:
    with open(status_file, "r") as f:
        last_status = json.load(f)
except FileNotFoundError:
    last_status = {}

# UPS Production endpoints
UPS_AUTH_URL = "https://onlinetools.ups.com/security/v1/oauth/token"
UPS_TRACK_URL = "https://onlinetools.ups.com/api/track/v1/details"

def get_access_token():
    creds = f"{UPS_CLIENT_ID}:{UPS_CLIENT_SECRET}"
    b64_creds = base64.b64encode(creds.encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64_creds}"
    }
    data = {"grant_type": "client_credentials"}
    r = requests.post(UPS_AUTH_URL, headers=headers, data=data)
    r.raise_for_status()
    return r.json()["access_token"]

def get_tracking_status(tracking_number, token):
    url = f"{UPS_TRACK_URL}/{tracking_number.strip()}"
    headers = {
        "Authorization": f"Bearer {token}",
        "transId": str(uuid.uuid4()),
        "transactionSrc": "ups-sms-notifier"
    }
    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        print("UPS ERROR:", r.status_code, r.text)
        r.raise_for_status()

    data = r.json()
    if "trackResponse" not in data:
        print("DEBUG UPS RESPONSE:", json.dumps(data, indent=2))
        return "No tracking info found", ""

    try:
        activity = data["trackResponse"]["shipment"][0]["package"][0]["activity"][0]
        status = activity["status"]["description"]

        # --- Enhanced location extraction ---
        loc_text = ""
        if "activityLocation" in activity:
            addr = activity["activityLocation"].get("address", {})
            parts = [addr.get("city"), addr.get("stateProvince"), addr.get("country")]
            loc_text = ", ".join([p for p in parts if p])
        elif "location" in activity:
            loc = activity["location"]
            parts = [loc.get("city"), loc.get("stateProvince"), loc.get("country")]
            loc_text = ", ".join([p for p in parts if p])

        return status, loc_text
    except Exception as e:
        print("DEBUG UPS RESPONSE:", json.dumps(data, indent=2))
        return f"Error parsing: {e}", ""

def send_sms(message):
    msg = client.messages.create(
        body=message,
        from_=twilio_number,
        to=my_number
    )
    print("Twilio SID:", msg.sid)
    print("Twilio status:", msg.status)

def main():
    global last_status
    token = get_access_token()
    for idx, tracking in enumerate(tracking_numbers):
        # Use nickname if provided, else fallback to tracking number
        if idx < len(tracking_nicknames) and tracking_nicknames[idx].strip():
            nickname = tracking_nicknames[idx].strip()
        else:
            nickname = tracking.strip()

        status, location = get_tracking_status(tracking, token)

        if status and last_status.get(tracking) != status:
            msg = f"{nickname} Update: {status}"
            if location:
                msg += f" ({location})"
            print(msg)
            send_sms(msg)
            last_status[tracking] = status
        else:
            print(f"No new update for {nickname}: {status}")

    with open(status_file, "w") as f:
        json.dump(last_status, f)

if __name__ == "__main__":
    main()