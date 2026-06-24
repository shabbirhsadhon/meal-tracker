#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
মিলট্র্যাকার — Daily Summary Sender
GitHub Actions এ চলবে। meal-tracker-backup.json পড়ে
আগের দিনের সারসংক্ষেপ Telegram এ পাঠাবে।
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# ── Config (GitHub Secrets থেকে) ────────────────────────────
BOT_TOKEN   = os.environ.get("TG_BOT_TOKEN", "")
GH_TOKEN    = os.environ.get("GH_TOKEN", "")        # repo read করতে
GH_USER     = os.environ.get("GH_USER", "")
GH_REPO     = os.environ.get("GH_REPO", "")
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

# ── Fetch backup from GitHub ─────────────────────────────────
def fetch_backup():
    url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents/{BACKUP_FILE}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    })
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    import base64
    content_b64 = data["content"].replace("\n", "")
    decoded = base64.b64decode(content_b64).decode("utf-8")
    return json.loads(decoded)

# ── Extract current month data ───────────────────────────────
def get_month_state(backup, year, month):
    """month: 0-indexed (Jan=0)"""
    key = f"mt3_{year}_{month}"
    months = backup.get("months", {})
    raw = months.get(key)
    if not raw:
        return None
    return raw  # already parsed (buildBackupObj stores parsed objects)

def get_subscribers(backup):
    months = backup.get("months", {})
    subs = months.get("mt3_tg_subscribers", [])
    return [str(s) for s in subs]

def get_tg_settings(backup):
    months = backup.get("months", {})
    return months.get("mt3_tg_settings", {})

# ── Calc helpers ─────────────────────────────────────────────
def get_meal(state, day, user):
    meals = state.get("meals", {})
    day_data = meals.get(str(day), meals.get(day, {}))
    v = day_data.get(user, 0)
    return float(v or 0)

def get_guest(state, day, user):
    guests = state.get("guests", {})
    day_data = guests.get(str(day), guests.get(day, {}))
    v = day_data.get(user, 0)
    return float(v or 0)

def get_user_day_meal(state, day, user):
    return get_meal(state, day, user) + get_guest(state, day, user)

def get_user_total(state, user, days_in_month):
    total = 0
    for d in range(1, days_in_month + 1):
        total += get_user_day_meal(state, d, user)
    return total

def calc_all(state, days_in_month):
    t_exp = sum(e.get("amount", 0) for e in state.get("expenditures", []))
    t_dep = sum(d.get("amount", 0) for d in state.get("deposits", []))
    users = state.get("users", [])
    t_meals = sum(get_user_total(state, u, days_in_month) for u in users)
    rate = (t_exp / t_meals) if t_meals > 0 else 0
    user_data = {}
    for u in users:
        meals = get_user_total(state, u, days_in_month)
        dep = sum(d.get("amount", 0) for d in state.get("deposits", []) if d.get("user") == u)
        cost = meals * rate
        user_data[u] = {"meals": meals, "deposit": dep, "mealCost": cost, "balance": dep - cost}
    return {"tExp": t_exp, "tDep": t_dep, "tMeals": t_meals, "rate": rate, "users": user_data}

# ── Build summary message ────────────────────────────────────
def build_message(backup):
    now_bst = datetime.now(BST)
    yesterday_bst = now_bst - timedelta(days=1)

    year  = yesterday_bst.year
    month = yesterday_bst.month - 1  # 0-indexed
    day   = yesterday_bst.day
    day_name = DN[yesterday_bst.weekday() % 7]  # Mon=0 in python, adjust
    # python weekday: Mon=0..Sun=6; DN: রবি=0(Sun)..শনি=6
    wd = yesterday_bst.weekday()  # 0=Mon
    bn_wd = (wd + 1) % 7  # 0=Sun -> bn_wd maps: Mon(1)->1, Sun(0)->0
    day_name = DN[bn_wd]

    import calendar
    days_in_month = calendar.monthrange(year, month + 1)[1]

    state = get_month_state(backup, year, month)
    if not state:
        return f"🌙 *দৈনিক সারসংক্ষেপ — {day} {MN[month]} {year}*\n\nকোনো ডেটা পাওয়া যায়নি।"

    users = state.get("users", [])

    # Yesterday's date string
    ydate = f"{year}-{str(month+1).zfill(2)}-{str(day).zfill(2)}"

    # Meals
    meal_lines = ""
    day_total = 0
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
    dep_yest = [d for d in state.get("deposits", []) if d.get("date") == ydate]
    dep_lines = "".join(f"\n  💰 {d['user']}: ৳{f2(d['amount'])}" for d in dep_yest) \
                or "\n  কোনো ডিপোজিট নেই"

    # Expenses yesterday
    exp_yest = [e for e in state.get("expenditures", []) if e.get("date") == ydate]
    exp_lines = "".join(f"\n  🛒 {e['desc']}: ৳{f2(e['amount'])}" for e in exp_yest) \
                or "\n  কোনো খরচ নেই"

    # Overall calc
    c = calc_all(state, days_in_month)

    # Balance lines
    bal_lines = []
    for u in users:
        ud = c["users"].get(u)
        if not ud:
            continue
        bal = ud["balance"]
        icon = "✅" if bal >= 0 else "❌"
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
def tg_send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
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
    # Validate env
    missing = [v for v in ["TG_BOT_TOKEN","GH_TOKEN","GH_USER","GH_REPO"] if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    print("📥 Fetching backup from GitHub…")
    try:
        backup = fetch_backup()
    except Exception as e:
        print(f"❌ Failed to fetch backup: {e}")
        sys.exit(1)

    # Check TG enabled in settings
    tg_settings = get_tg_settings(backup)
    if not tg_settings.get("enabled", False):
        print("ℹ️ Telegram notifier is disabled in app settings. Skipping.")
        sys.exit(0)

    subscribers = get_subscribers(backup)
    if not subscribers:
        print("⚠️ No subscribers found in backup.")
        sys.exit(0)

    print(f"✅ Found {len(subscribers)} subscriber(s)")

    print("📝 Building summary message…")
    msg = build_message(backup)
    print("Message preview:")
    print(msg[:200] + "…")

    print(f"\n📨 Sending to {len(subscribers)} subscriber(s)…")
    ok_count = 0
    for cid in subscribers:
        ok = tg_send(BOT_TOKEN, cid, msg)
        status = "✅" if ok else "❌"
        print(f"  {status} chat_id: {cid}")
        if ok:
            ok_count += 1

    print(f"\n✅ Sent to {ok_count}/{len(subscribers)} subscribers.")
    if ok_count == 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
