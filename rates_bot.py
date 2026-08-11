#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات نرخ‌های طلا، ارز، نقره و کریپتو به تومان (منبع: tgju.org)
- هر ۶ ساعت (ساعت ۰۳:۰۰، ۰۹:۰۰، ۱۵:۰۰ و ۲۱:۰۰ به وقت تهران) نرخ‌ها را می‌فرستد.
- اگر ربات را دستی استارت کنید (/start یا هر پیامی)، بلافاصله نرخ لحظه‌ای همان لحظه را می‌فرستد.
- همه نرخ‌ها (دلار، طلا، نقره، بیت‌کوین، دوج‌کوین) از سایت tgju.org خوانده می‌شود.
"""
import json
import logging
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["TELEGRAM_TOKEN"]  # توکن از متغیر محیطی (در GitHub Actions از Secret)
TEHRAN_TZ = ZoneInfo("Asia/Tehran")
SCHEDULE_HOURS = (3, 9, 15, 21)                 # هر ۶ ساعت به وقت تهران
SKIP_SCHEDULE = os.environ.get("SKIP_SCHEDULE") == "1"  # فقط پاسخ به /start

# --------------------------- دریافت نرخ‌ها ---------------------------

def fetch_json(url: str, headers=None, timeout: int = 25):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tgju_today(symbol: str):
    """نرخ لحظه‌ای از API سایت tgju (خروجی به ریال)"""
    url = f"https://api.tgju.org/v1/market/indicator/today-table-data/{symbol}?lang=fa"
    data = fetch_json(url)
    row = data["data"][0]  # آخرین نرخ ثبت‌شده
    price_rial = int(float(row[0].replace(",", "")))
    time_str = row[1]
    change_rial = None
    pct = None
    if len(row) > 2:
        m = re.search(r">([\d,]+)<", row[2])
        if m:
            change_rial = int(m.group(1).replace(",", ""))
    if len(row) > 3:
        m = re.search(r">([\d.]+)%<", row[3])
        if m:
            pct = float(m.group(1))
    return {"rial": price_rial, "toman": price_rial // 10, "time": time_str,
            "change_rial": change_rial, "pct": pct}


def fa_digits(s: str) -> str:
    return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fmt(n: float) -> str:
    """قالب‌بندی عدد با جداکننده هزارگان به فارسی"""
    if abs(n) < 1:
        return fa_digits(f"{n:,.4f}".replace(",", "٬"))
    return fa_digits(f"{n:,.0f}".replace(",", "٬"))


def to_jalali(gy: int, gm: int, gd: int):
    """تبدیل تاریخ میلادی به شمسی (الگوریتم استاندارد)"""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += g_d_m[gm2] + gd2
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        g_day_no += 1
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    if j_day_no < 186:
        jm = 1 + j_day_no // 31
        jd = 1 + j_day_no % 31
    else:
        jm = 7 + (j_day_no - 186) // 30
        jd = 1 + (j_day_no - 186) % 30
    return jy, jm, jd


def build_message():
    dollar = tgju_today("price_dollar_rl")          # دلار (ریال)
    gold18 = tgju_today("geram18")                  # طلای ۱۸ عیار (ریال)
    silver999 = tgju_today("silver_999")            # گرم نقره ۹۹۹ (ریال)
    btc = tgju_today("crypto-bitcoin-irr")          # بیت‌کوین (ریال)
    doge = tgju_today("crypto-dogecoin-irr")        # دوج‌کوین (ریال)
    btc_usd = tgju_today("crypto-bitcoin")          # بیت‌کوین (دلار، برای نمایش)
    usd_toman = dollar["toman"]

    now = datetime.now(TEHRAN_TZ)
    now_fa = now.strftime("%H:%M")
    jy, jm, jd = to_jalali(now.year, now.month, now.day)
    jm_names = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
    date_fa = f"{fa_digits(str(jd))} {jm_names[jm-1]} {fa_digits(str(jy))}"

    lines = [
        "💰📊 نرخ‌های لحظه‌ای بازار",
        "——————————————",
        f"🕘 زمان: {fa_digits(date_fa)} - {fa_digits(now_fa)} به وقت تهران",
        "",
        "💵 دلار آمریکا (بازار آزاد)",
        f"   {fmt(usd_toman)} تومان",
        "",
        "🥇 طلای ۱۸ عیار (هر گرم)",
        f"   {fmt(gold18['toman'])} تومان",
        "",
        "🥈 نقره ۹۹۹ (هر گرم)",
        f"   {fmt(silver999['toman'])} تومان",
        "",
        "₿ بیت‌کوین",
        f"   {fmt(btc['toman'])} تومان ({fmt(btc_usd['rial'])} دلار)",
        "",
        "🐕 دوج‌کوین",
        f"   {fmt(doge['toman'])} تومان",
        "",
        "📌 همه نرخ‌ها از سایت tgju.org",
        "🔄 هر ۶ ساعت (ساعت ۳ بامداد، ۹ صبح، ۳ بعدازظهر و ۹ شب)",
    ]
    return "\n".join(lines)


def send_telegram(chat_id, text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------- زمان‌بندی ---------------------------

def next_run_time(now_tz):
    """بعدی ساعت‌های ۰۳، ۰۹، ۱۵ و ۲۱ به وقت تهران"""
    for hh in SCHEDULE_HOURS:
        t = now_tz.replace(hour=hh, minute=0, second=0, microsecond=0)
        if t > now_tz:
            return t
    t = now_tz.replace(hour=SCHEDULE_HOURS[0], minute=0, second=0, microsecond=0)
    from datetime import timedelta
    return t + timedelta(days=1)


def process_updates(offset, subscribers):
    """پاسخ فوری به /start یا هر پیامی با نرخ همان لحظه"""
    try:
        url = (f"https://api.telegram.org/bot{TOKEN}/getUpdates"
               f"?timeout=8&offset={offset['n']}")
        data = fetch_json(url)
        for upd in data.get("result", []):
            offset["n"] = upd["update_id"] + 1
            msg = upd.get("message")
            if not msg:
                continue
            chat = msg.get("chat") or {}
            if chat.get("type") not in ("private", "group", "supergroup"):
                continue
            chat_id = chat["id"]
            text = (msg.get("text") or "").strip()
            if text.startswith("/start"):
                reply = ("👋 سلام! این ربات هر ۶ ساعت نرخ لحظه‌ای دلار، طلا، نقره، "
                         "بیت‌کوین و دوج‌کوین را از tgju.org می‌فرستد.\n\n"
                         "📊 نرخ همین حالا:\n\n" + build_message())
            else:
                reply = "📊 نرخ لحظه‌ای:\n\n" + build_message()
            send_telegram(chat_id, reply)
            subscribers.add(chat_id)
            print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] پاسخ فوری به {chat_id} ارسال شد ✓",
                  flush=True)
    except Exception as e:
        print(f"خطا در خواندن پیام‌ها: {e}", flush=True)


# --------------------------- اجرا ---------------------------

def main():
    args = sys.argv[1:]
    chat_id = os.environ.get("CHAT_ID") or (args[0] if args and not args[0].startswith("--") else None)
    once = "--once" in args
    send_now = "--now" in args

    if once:  # حالت یک‌باره برای GitHub Actions
        if not chat_id:
            logging.error("chat_id داده نشده (متغیر CHAT_ID یا آرگومان اول)")
            sys.exit(1)
        text = build_message()
        send_telegram(chat_id, text)
        print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] پیام ارسال شد ✓")
        return

    # حالت دائمی: زمان‌بندی ۶ ساعته + پاسخ فوری به /start
    subscribers = set()
    if chat_id:
        subscribers.add(int(chat_id))
    if send_now and subscribers:
        text = build_message()
        for cid in subscribers:
            send_telegram(cid, text)
        print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] پیام اولیه ارسال شد ✓", flush=True)

    offset = {"n": 0}
    next_sched = None if SKIP_SCHEDULE else next_run_time(datetime.now(TEHRAN_TZ))
    print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] ربات فعال شد. "
          f"زمان‌بندی: {'خاموش (فقط پاسخ به /start)' if SKIP_SCHEDULE else 'هر ۶ ساعت'}", flush=True)

    while True:
        process_updates(offset, subscribers)
        now = datetime.now(TEHRAN_TZ)
        if next_sched is not None and now >= next_sched:
            try:
                text = build_message()
            except Exception as e:
                print(f"خطا در ساخت پیام: {e}", flush=True)
                text = None
            if text:
                for cid in list(subscribers):
                    try:
                        send_telegram(cid, text)
                        print(f"[{now:%Y-%m-%d %H:%M:%S}] پیام زمان‌بندی‌شده ارسال شد → {cid} ✓",
                              flush=True)
                    except Exception as e:
                        print(f"خطا در ارسال به {cid}: {e}", flush=True)
            next_sched = next_run_time(now)
        time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
