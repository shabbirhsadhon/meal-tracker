#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
মিলট্র্যাকার — Daily Summary Sender
GitHub Actions এ চলবে। meal-tracker-backup.json পড়ে
আগের দিনের সারসংক্ষেপ Telegram এ পাঠাবে।
পাঠানোর পর message record করে backup এ save করবে।
"""

import json
import os
import sys
import base64
import random
import string
import urllib.request
from datetime import datetime, timedelta, timezone
import calendar

# ── Config ──────────────────────────────────────────────────
# BOT_TOKEN is read from the backup JSON (mt3_tg_settings.token)
# so it always stays in sync with whatever you save in the app.
GH_TOKEN  = os.environ.get("GH_PAT", "")

# Hardcoded — not sensitive
GH_USER   = os.environ.get("GH_USER", "shabbirhsadhon")
GH_REPO   = os.environ.get("GH_REPO", "meal-tracker")

BACKUP_FILE = "meal-tracker-backup.json"

# Bangladesh Standard Time = UTC+6
BST = timezone(timedelta(hours=6))

# ── Bengali helpers ──────────────────────────────────────────
MN = ["জানুয়ারি","ফেব্রুয়ারি","মার্চ","এপ্রিল","মে","জুন",
      "জুলাই","আগস্ট","সেপ্টেম্বর","অক্টোবর","নভেম্বর","ডিসেম্বর"]
DN = ["রবি","সোম","মঙ্গ","বুধ","বৃহ","শুক্র","শনি"]

def fN(n):
    n = float(n or 0)
    if n == int(n):
        return str(int(n))
    return f"{n:.1f}"

def f2(n):
    return f"{float(n or 0):.2f}"

def gid():
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=8))

# ── GitHub API helpers ───────────────────────────────────────
def gh_get(path):
    url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def gh_put(path, content_str, sha, commit_msg):
    url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents/{path}"
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("ascii")
    body = {"message": commit_msg, "content": content_b64, "sha": sha}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="PUT", headers={
        "Authorization": f"token {GH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json"
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# ── Fetch / Save backup ──────────────────────────────────────
def fetch_backup():
    data = gh_get(BACKUP_FILE)
    content_b64 = data["content"].replace("\n", "")
    decoded = base64.b64decode(content_b64).decode("utf-8")
    backup = json.loads(decoded)
    sha = data["sha"]
    return backup, sha

def save_backup(backup, sha):
    content_str = json.dumps(backup, ensure_ascii=False, indent=2)
    now_str = datetime.now(BST).strftime("%Y-%m-%d %H:%M BST")
    result = gh_put(BACKUP_FILE, content_str, sha, f"Daily summary recorded — {now_str}")
    return result.get("content", {}).get("sha", sha)

# ── Extract data from backup ─────────────────────────────────
def get_month_state(backup, year, month):
    # month is 0-indexed (same as app)
    key = f"mt3_{year}_{month}"
    return backup.get("months", {}).get(key)

def get_subscribers(backup):
    subs = backup.get("months", {}).get("mt3_tg_subscribers", [])
    if not subs:
        # also try as a list directly
        subs = backup.get("months", {}).get("mt3_tg_subscribers") or []
    return [str(s) for s in subs]

# ── Calc helpers ─────────────────────────────────────────────
def get_meal(state, day, user):
    day_data = (state.get("meals") or {}).get(str(day), (state.get("meals") or {}).get(day, {}))
    return float(day_data.get(user, 0) or 0)

def get_guest(state, day, user):
    day_data = (state.get("guests") or {}).get(str(day), (state.get("guests") or {}).get(day, {}))
    return float(day_data.get(user, 0) or 0)

def get_user_day_meal(state, day, user):
    return get_meal(state, day, user) + get_guest(state, day, user)

def get_user_total(state, user, days_in_month):
    return sum(get_user_day_meal(state, d, user) for d in range(1, days_in_month + 1))

def calc_all(state, days_in_month):
    t_exp  = sum(e.get("amount", 0) for e in state.get("expenditures", []))
    t_dep  = sum(d.get("amount", 0) for d in state.get("deposits", []))
    users  = state.get("users", [])
    t_meals = sum(get_user_total(state, u, days_in_month) for u in users)
    rate   = (t_exp / t_meals) if t_meals > 0 else 0
    user_data = {}
    for u in users:
        meals = get_user_total(state, u, days_in_month)
        dep   = sum(d.get("amount", 0) for d in state.get("deposits", []) if d.get("user") == u)
        cost  = meals * rate
        user_data[u] = {"meals": meals, "deposit": dep, "mealCost": cost, "balance": dep - cost}
    return {"tExp": t_exp, "tDep": t_dep, "tMeals": t_meals, "rate": rate, "users": user_data}

# ── Build summary message ────────────────────────────────────
def build_message(backup):
    now_bst       = datetime.now(BST)
    yesterday_bst = now_bst - timedelta(days=1)
    year  = yesterday_bst.year
    month = yesterday_bst.month - 1  # 0-indexed to match app
    day   = yesterday_bst.day
    wd    = yesterday_bst.weekday()
    day_name = DN[(wd + 1) % 7]
    days_in_month = calendar.monthrange(year, month + 1)[1]

    state = get_month_state(backup, year, month)
    if not state:
        print(f"⚠️ No state found for key mt3_{year}_{month}")
        print(f"   Available keys: {list(backup.get('months', {}).keys())}")
        return f"🌙 *দৈনিক সারসংক্ষেপ — {day} {MN[month]} {year}*\n\nকোনো ডেটা পাওয়া যায়নি।"

    users = state.get("users", [])
    ydate = f"{year}-{str(month+1).zfill(2)}-{str(day).zfill(2)}"

    # Meals
    meal_lines = ""
    day_total  = 0
    for u in users:
        m = get_user_day_meal(state, day, u)
        g = get_guest(state, day, u)
        day_total += m
        if m > 0 or g > 0:
            guest_str = f" (গেস্ট: {fN(g)})" if g > 0 else ""
            meal_lines += f"\n  👤 {u}: {fN(m)}{guest_str}"
    if not meal_lines:
        meal_lines = "\n  কোনো মিল এন্ট্রি নেই"

    # Deposits yesterday
    dep_yest  = [d for d in state.get("deposits", []) if d.get("date") == ydate]
    dep_lines = "".join(f"\n  💰 {d['user']}: ৳{f2(d['amount'])}" for d in dep_yest) \
                or "\n  কোনো ডিপোজিট নেই"

    # Expenses yesterday
    exp_yest  = [e for e in state.get("expenditures", []) if e.get("date") == ydate]
    exp_lines = "".join(f"\n  🛒 {e['desc']}: ৳{f2(e['amount'])}" for e in exp_yest) \
                or "\n  কোনো খরচ নেই"

    c = calc_all(state, days_in_month)
    bal_lines = []
    for u in users:
        ud = c["users"].get(u)
        if not ud:
            continue
        bal    = ud["balance"]
        icon   = "✅" if bal >= 0 else "❌"
        status = "পাবে" if bal >= 0 else "দিতে হবে"
        bal_lines.append(f"{icon} {u}: ৳{f2(abs(bal))} {status}")

    msg = (
        f"🌙 *দৈনিক সারসংক্ষেপ — {day} {MN[month]} {year} ({day_name})*\n\n"
        f"🍽 *গতকালের মিল* (মোট: {fN(day_total)}){meal_lines}\n\n"
        f"💰 *গতকালের ডিপোজিট*{dep_lines}\n\n"
        f"🛒 *গতকালের খরচ*{exp_lines}\n\n"
        f"📊 *{MN[month]} মাসের সার্বিক অবস্থা:*\n"
        f"মোট খরচ: ৳{f2(c['tExp'])}\n"
        f"মোট ডিপোজিট: ৳{f2(c['tDep'])}\n"
        f"মোট মিল: {fN(c['tMeals'])}\n"
        f"মিল রেট: ৳{f2(c['rate'])}\n\n"
        f"⚖️ *দেনা পাওনা:*\n"
        + "\n".join(bal_lines)
    )
    return msg

# ── Send to Telegram ─────────────────────────────────────────
def tg_send_one(token, chat_id, text):
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
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
            if result.get("ok"):
                msg_id = result.get("result", {}).get("message_id")
                return True, msg_id
            else:
                print(f"  ⚠️ Telegram error for {chat_id}: {result.get('description')}")
                return False, None
    except Exception as e:
        print(f"  ⚠️ chat_id {chat_id} error: {e}")
        return False, None

# ── Record message in backup ─────────────────────────────────
def record_message(backup, msg_text, msg_ids_list):
    if "months" not in backup:
        backup["months"] = {}
    existing   = backup["months"].get("mt3_tg_messages", [])
    new_entry  = {
        "id":     gid(),
        "text":   msg_text,
        "sentAt": datetime.now(BST).isoformat(),
        "msgIds": msg_ids_list
    }
    updated = [new_entry] + existing
    if len(updated) > 100:
        updated = updated[:100]
    backup["months"]["mt3_tg_messages"] = updated
    return backup

# ── Main ─────────────────────────────────────────────────────
def main():
    # Only GH_PAT is required as a secret — bot token is read from the backup
    if not os.environ.get("GH_PAT"):
        print("❌ Missing env var: GH_PAT")
        sys.exit(1)

    print(f"📋 Repo: {GH_USER}/{GH_REPO}")
    print("📥 Fetching backup from GitHub…")
    try:
        backup, sha = fetch_backup()
    except Exception as e:
        print(f"❌ Failed to fetch backup: {e}")
        sys.exit(1)

    # Debug: show what keys exist
    month_keys = list(backup.get("months", {}).keys())
    print(f"📦 Backup keys found: {month_keys}")

    # Read bot token from backup — always in sync with the app
    tg_settings = backup.get("months", {}).get("mt3_tg_settings", {})
    BOT_TOKEN = tg_settings.get("token", "")
    if not BOT_TOKEN:
        print("❌ No bot token found in backup (mt3_tg_settings.token).")
        print("   Save your Telegram settings in the app and sync to GitHub first.")
        sys.exit(1)
    print(f"🤖 Using bot token from backup: {BOT_TOKEN[:10]}…")

    subscribers = get_subscribers(backup)
    if not subscribers:
        print("⚠️ No subscribers found in backup (mt3_tg_subscribers is empty or missing).")
        print("   Make sure someone has messaged the bot and the backup was saved after that.")
        sys.exit(1)

    print(f"✅ Found {len(subscribers)} subscriber(s): {subscribers}")
    print("📝 Building summary message…")
    msg = build_message(backup)
    print("Message preview:")
    print(msg[:400] + ("…" if len(msg) > 400 else ""))

    print(f"\n📨 Sending to {len(subscribers)} subscriber(s)…")
    ok_count = 0
    collected_msg_ids = []
    for cid in subscribers:
        ok, mid = tg_send_one(BOT_TOKEN, cid, msg)
        status  = "✅" if ok else "❌"
        print(f"  {status} chat_id: {cid}" + (f" | msg_id: {mid}" if mid else ""))
        if ok:
            ok_count += 1
            if mid:
                collected_msg_ids.append({"chatId": cid, "msgId": mid})

    print(f"\n📊 Sent to {ok_count}/{len(subscribers)} subscribers.")

    if ok_count > 0:
        print("💾 Recording message in backup…")
        backup = record_message(backup, msg, collected_msg_ids)
        try:
            save_backup(backup, sha)
            print("✅ Backup updated on GitHub.")
        except Exception as e:
            print(f"⚠️ Could not save backup: {e}")
    else:
        print("❌ No messages sent successfully.")
        sys.exit(1)

if __name__ == "__main__":
    main()
