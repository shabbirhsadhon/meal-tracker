#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
মিলট্র্যাকার — Pending Notification Sender
GitHub Actions এ চলবে। pending_notification.json পড়ে
সব subscriber-দের Telegram-এ message পাঠাবে।
পাঠানোর পর pending_notification.json delete করবে।
"""

import json
import os
import sys
import base64
import urllib.request

# ── Config (GitHub Secrets থেকে) ────────────────────────────
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
GH_TOKEN  = os.environ.get("GH_PAT", "")
GH_USER   = os.environ.get("GH_USER", "")
GH_REPO   = os.environ.get("GH_REPO", "")

PENDING_FILE = "pending_notification.json"
BACKUP_FILE  = "meal-tracker-backup.json"

# ── GitHub API helpers ───────────────────────────────────────
def gh_get(path):
    url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def gh_delete(path, sha, commit_msg):
    url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents/{path}"
    body = json.dumps({"message": commit_msg, "sha": sha}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="DELETE", headers={
        "Authorization": f"token {GH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json"
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# ── Fetch pending notification ───────────────────────────────
def fetch_pending():
    data = gh_get(PENDING_FILE)
    content_b64 = data["content"].replace("\n", "")
    decoded = base64.b64decode(content_b64).decode("utf-8")
    payload = json.loads(decoded)
    sha = data["sha"]
    return payload, sha

# ── Fetch subscribers from backup ───────────────────────────
def fetch_subscribers():
    try:
        data = gh_get(BACKUP_FILE)
        content_b64 = data["content"].replace("\n", "")
        decoded = base64.b64decode(content_b64).decode("utf-8")
        backup = json.loads(decoded)
        subs = backup.get("months", {}).get("mt3_tg_subscribers", [])
        return [str(s) for s in subs]
    except Exception as e:
        print(f"⚠️ Could not fetch subscribers from backup: {e}")
        return []

# ── Check if TG notifier is enabled ─────────────────────────
def is_tg_enabled():
    try:
        data = gh_get(BACKUP_FILE)
        content_b64 = data["content"].replace("\n", "")
        decoded = base64.b64decode(content_b64).decode("utf-8")
        backup = json.loads(decoded)
        settings = backup.get("months", {}).get("mt3_tg_settings", {})
        return settings.get("enabled", False)
    except Exception:
        return True  # default: try to send

# ── Send to Telegram ─────────────────────────────────────────
def tg_send_one(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"  ⚠️ chat_id {chat_id} error: {e}")
        return False

# ── Main ─────────────────────────────────────────────────────
def main():
    missing = [v for v in ["TG_BOT_TOKEN", "GH_PAT", "GH_USER", "GH_REPO"] if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    print("📥 Fetching pending notification…")
    try:
        payload, sha = fetch_pending()
    except Exception as e:
        print(f"❌ Failed to fetch pending_notification.json: {e}")
        sys.exit(1)

    text = payload.get("text", "")
    if not text:
        print("⚠️ No text in pending notification. Deleting file.")
        gh_delete(PENDING_FILE, sha, "Delete empty pending notification")
        sys.exit(0)

    print(f"📝 Message preview: {text[:100]}{'…' if len(text) > 100 else ''}")

    # Check enabled
    if not is_tg_enabled():
        print("ℹ️ Telegram notifier is disabled. Deleting pending file.")
        gh_delete(PENDING_FILE, sha, "Delete pending notification (notifier disabled)")
        sys.exit(0)

    # Get subscribers
    subscribers = fetch_subscribers()
    if not subscribers:
        print("⚠️ No subscribers. Deleting pending file.")
        gh_delete(PENDING_FILE, sha, "Delete pending notification (no subscribers)")
        sys.exit(0)

    print(f"👥 Sending to {len(subscribers)} subscriber(s)…")
    ok_count = 0
    for cid in subscribers:
        ok = tg_send_one(cid, text)
        print(f"  {'✅' if ok else '❌'} chat_id: {cid}")
        if ok:
            ok_count += 1

    print(f"\n📊 Sent to {ok_count}/{len(subscribers)} subscribers.")

    # Delete pending file regardless (avoid retrigger)
    print("🗑 Deleting pending_notification.json…")
    try:
        gh_delete(PENDING_FILE, sha, "Delete pending notification after sending")
        print("✅ Done.")
    except Exception as e:
        print(f"⚠️ Could not delete pending file: {e}")

    if ok_count == 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
