#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات نرخ‌های طلا، ارز، نقره و کریپتو به تومان (منبع: tgju.org)
- هر ۶ ساعت (ساعت ۰۳:۰۰، ۰۹:۰۰، ۱۵:۰۰ و ۲۱:۰۰ به وقت تهران) نرخ‌ها را می‌فرستد.
- اگر ربات را دستی استارت کنید (/start یا هر پیامی)، بلافاصله نرخ لحظه‌ای همان لحظه را می‌فرستد.

منبع داده:
  1) call1.tgju.org/ajax.json  ← منبع اصلی (حتی وقتی بازار بسته است آخرین نرخ را دارد)
  2) api.tgju.org/.../today-table-data/{symbol}  ← فقط به‌عنوان پشتیبان
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

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    print("❌ متغیر محیطی TELEGRAM_TOKEN تعریف نشده است.", flush=True)
    sys.exit(1)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")
SCHEDULE_HOURS = (3, 9, 15, 21)                 # هر ۶ ساعت به وقت تهران
SKIP_SCHEDULE = os.environ.get("SKIP_SCHEDULE") == "1"  # فقط پاسخ به /start
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# --------------------------- ابزار شبکه ---------------------------

def fetch_json(url: str, headers=None, timeout: int = 25, retries: int = 3):
    """دریافت JSON با تلاش مجدد (شبکه‌ی GitHub Actions گاهی ناپایدار است)"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:          # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last


# --------------------------- دریافت نرخ‌ها ---------------------------

_AJAX_CACHE = {"data": None, "ts": 0.0}


def ajax_all(max_age: int = 60):
    """کل نرخ‌ها را یک‌جا از ajax.json می‌گیرد (با کش کوتاه‌مدت)."""
    now = time.time()
    if _AJAX_CACHE["data"] is not None and now - _AJAX_CACHE["ts"] < max_age:
        return _AJAX_CACHE["data"]
    data = fetch_json("https://call1.tgju.org/ajax.json")
    current = data.get("current") or {}
    if not current:
        raise RuntimeError("پاسخ ajax.json فاقد بخش current است")
    _AJAX_CACHE["data"] = current
    _AJAX_CACHE["ts"] = now
    return current


def _num(s):
    return float(str(s).replace(",", "").replace("٬", "").strip())


def tgju_today(symbol: str):
    """
    آخرین نرخ نماد.
    خروجی: rial (عدد خام سایت)، toman، time، change، pct، stale
    نکته: برای نمادهای دلاری مثل crypto-bitcoin عدد خام همان دلار است، نه ریال.
    """
    # --- منبع اصلی: ajax.json ---
    try:
        cur = ajax_all()
        row = cur.get(symbol)
        if row and row.get("p") not in (None, "", "0"):
            raw = _num(row["p"])
            ts = row.get("ts") or ""
            stale = False
            if ts:
                try:
                    d = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").date()
                    stale = d != datetime.now(TEHRAN_TZ).date()
                except ValueError:
                    pass
            return {
                "raw": raw,
                "rial": raw,
                "toman": raw / 10,
                "time": row.get("t_en") or row.get("t") or "",
                "change_rial": _num(row["d"]) if row.get("d") else None,
                "pct": row.get("dp"),
                "stale": stale,
            }
    except Exception as e:                                  # noqa: BLE001
        print(f"⚠️ ajax.json برای {symbol} کار نکرد: {e}", flush=True)

    # --- پشتیبان: today-table-data ---
    url = f"https://api.tgju.org/v1/market/indicator/today-table-data/{symbol}?lang=fa"
    data = fetch_json(url)
    rows = data.get("data") or []
    if not rows:
        # همین‌جا بود که نسخه‌ی قبلی با IndexError کرش می‌کرد
        raise RuntimeError(f"داده‌ای برای «{symbol}» موجود نیست (بازار بسته است)")
    row = rows[0]
    raw = _num(row[0])
    change = pct = None
    if len(row) > 2:
        m = re.search(r">([\d,.]+)<", str(row[2]))
        if m:
            change = _num(m.group(1))
    if len(row) > 3:
        m = re.search(r">([\d.]+)%<", str(row[3]))
        if m:
            pct = float(m.group(1))
    return {"raw": raw, "rial": raw, "toman": raw / 10, "time": row[1],
            "change_rial": change, "pct": pct, "stale": False}


def safe_rate(symbol: str):
    """نرخ را می‌گیرد؛ در صورت خطا None برمی‌گرداند تا کل پیام از بین نرود."""
    try:
        return tgju_today(symbol)
    except Exception as e:                                  # noqa: BLE001
        print(f"⚠️ نرخ «{symbol}» دریافت نشد: {e}", flush=True)
        return None


# --------------------------- قالب‌بندی ---------------------------

def fa_digits(s: str) -> str:
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fmt(n, decimals: int = 0) -> str:
    if n is None:
        return "نامشخص"
    if decimals == 0 and abs(n) < 1:
        decimals = 4
    return fa_digits(f"{n:,.{decimals}f}".replace(",", "٬"))


def line(rate, decimals: int = 0, unit: str = "تومان", key: str = "toman") -> str:
    if rate is None:
        return "   ⛔ در دسترس نیست"
    mark = " ⏳" if rate.get("stale") else ""
    return f"   {fmt(rate[key], decimals)} {unit}{mark}"


def to_jalali(gy: int, gm: int, gd: int):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2, gm2, gd2 = gy - 1600, gm - 1, gd - 1
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
        jm, jd = 1 + j_day_no // 31, 1 + j_day_no % 31
    else:
        jm, jd = 7 + (j_day_no - 186) // 30, 1 + (j_day_no - 186) % 30
    return jy, jm, jd


def build_message():
    dollar    = safe_rate("price_dollar_rl")        # دلار (ریال)
    gold18    = safe_rate("geram18")                # طلای ۱۸ عیار (ریال)
    coin      = safe_rate("sekee")                  # سکه امامی (ریال)
    silver999 = safe_rate("silver_999")             # گرم نقره ۹۹۹ (ریال)
    btc       = safe_rate("crypto-bitcoin-irr")     # بیت‌کوین (ریال)
    doge      = safe_rate("crypto-dogecoin-irr")    # دوج‌کوین (ریال)
    btc_usd   = safe_rate("crypto-bitcoin")         # بیت‌کوین (دلار)

    if all(x is None for x in (dollar, gold18, silver999, btc, doge)):
        raise RuntimeError("هیچ نرخی از tgju.org دریافت نشد")

    now = datetime.now(TEHRAN_TZ)
    jy, jm, jd = to_jalali(now.year, now.month, now.day)
    jm_names = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
    date_fa = f"{fa_digits(jd)} {jm_names[jm - 1]} {fa_digits(jy)}"

    btc_usd_txt = f" ({fmt(btc_usd['raw'], 2)} دلار)" if btc_usd else ""

    lines = [
        "💰📊 نرخ‌های لحظه‌ای بازار",
        "——————————————",
        f"🕘 زمان: {date_fa} - {fa_digits(now.strftime('%H:%M'))} به وقت تهران",
        "",
        "💵 دلار آمریکا (بازار آزاد)",
        line(dollar),
        "",
        "🥇 طلای ۱۸ عیار (هر گرم)",
        line(gold18),
        "",
        "🪙 سکه امامی",
        line(coin),
        "",
        "🥈 نقره ۹۹۹ (هر گرم)",
        line(silver999),
        "",
        "₿ بیت‌کوین",
        (f"   {fmt(btc['toman'])} تومان{btc_usd_txt}" if btc else "   ⛔ در دسترس نیست"),
        "",
        "🐕 دوج‌کوین",
        line(doge),
        "",
    ]
    if any(r and r.get("stale") for r in (dollar, gold18, coin, silver999)):
        lines.append("⏳ = بازار بسته است؛ آخرین نرخ ثبت‌شده نمایش داده می‌شود.")
        lines.append("")
    lines += [
        "📌 همه نرخ‌ها از سایت tgju.org",
        "🔄 هر ۶ ساعت (ساعت ۳ بامداد، ۹ صبح، ۳ بعدازظهر و ۹ شب)",
    ]
    return "\n".join(lines)


# --------------------------- تلگرام ---------------------------

def send_telegram(chat_id, text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text,
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json", **UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"تلگرام خطا داد ({e.code}): {detail}") from None


# --------------------------- زمان‌بندی ---------------------------

def next_run_time(now_tz):
    for hh in SCHEDULE_HOURS:
        t = now_tz.replace(hour=hh, minute=0, second=0, microsecond=0)
        if t > now_tz:
            return t
    from datetime import timedelta
    return now_tz.replace(hour=SCHEDULE_HOURS[0], minute=0,
                          second=0, microsecond=0) + timedelta(days=1)


def process_updates(offset, subscribers):
    try:
        url = (f"https://api.telegram.org/bot{TOKEN}/getUpdates"
               f"?timeout=8&offset={offset['n']}")
        data = fetch_json(url, retries=1, timeout=20)
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
            try:
                body = build_message()
            except Exception as e:                          # noqa: BLE001
                body = f"⚠️ فعلاً دریافت نرخ‌ها ممکن نیست.\n({e})"
            if text.startswith("/start"):
                reply = ("👋 سلام! این ربات هر ۶ ساعت نرخ لحظه‌ای دلار، طلا، نقره، "
                         "بیت‌کوین و دوج‌کوین را از tgju.org می‌فرستد.\n\n"
                         "📊 نرخ همین حالا:\n\n" + body)
            else:
                reply = "📊 نرخ لحظه‌ای:\n\n" + body
            send_telegram(chat_id, reply)
            subscribers.add(chat_id)
            print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] "
                  f"پاسخ فوری به {chat_id} ارسال شد ✓", flush=True)
    except Exception as e:                                  # noqa: BLE001
        print(f"خطا در خواندن پیام‌ها: {e}", flush=True)


# --------------------------- اجرا ---------------------------

def main():
    args = sys.argv[1:]
    chat_id = os.environ.get("CHAT_ID") or (
        args[0] if args and not args[0].startswith("--") else None)
    once = "--once" in args
    send_now = "--now" in args

    if once:  # حالت یک‌باره برای GitHub Actions
        if not chat_id:
            print("❌ chat_id داده نشده (متغیر CHAT_ID یا آرگومان اول)", flush=True)
            sys.exit(1)
        try:
            text = build_message()
        except Exception as e:                              # noqa: BLE001
            # به‌جای کرش بی‌صدا، خطا را به تلگرام هم گزارش می‌دهیم
            print(f"❌ ساخت پیام شکست خورد: {e}", flush=True)
            try:
                send_telegram(chat_id, f"⚠️ ربات نرخ‌ها: دریافت داده از tgju.org "
                                       f"ناموفق بود.\n\nجزئیات: {e}")
            except Exception as e2:                         # noqa: BLE001
                print(f"❌ ارسال پیام خطا هم شکست خورد: {e2}", flush=True)
            sys.exit(1)

        res = send_telegram(chat_id, text)
        if not res.get("ok"):
            print(f"❌ تلگرام پیام را نپذیرفت: {res}", flush=True)
            sys.exit(1)
        print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] پیام ارسال شد ✓", flush=True)
        return

    subscribers = set()
    if chat_id:
        subscribers.add(int(chat_id))
    if send_now and subscribers:
        try:
            text = build_message()
            for cid in subscribers:
                send_telegram(cid, text)
            print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] پیام اولیه ارسال شد ✓",
                  flush=True)
        except Exception as e:                              # noqa: BLE001
            print(f"خطا در ارسال پیام اولیه: {e}", flush=True)

    offset = {"n": 0}
    next_sched = None if SKIP_SCHEDULE else next_run_time(datetime.now(TEHRAN_TZ))
    print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] ربات فعال شد. زمان‌بندی: "
          f"{'خاموش (فقط پاسخ به /start)' if SKIP_SCHEDULE else 'هر ۶ ساعت'}", flush=True)

    while True:
        process_updates(offset, subscribers)
        now = datetime.now(TEHRAN_TZ)
        if next_sched is not None and now >= next_sched:
            try:
                text = build_message()
            except Exception as e:                          # noqa: BLE001
                print(f"خطا در ساخت پیام: {e}", flush=True)
                text = None
            if text:
                for cid in list(subscribers):
                    try:
                        send_telegram(cid, text)
                        print(f"[{now:%Y-%m-%d %H:%M:%S}] پیام زمان‌بندی‌شده "
                              f"ارسال شد → {cid} ✓", flush=True)
                    except Exception as e:                  # noqa: BLE001
                        print(f"خطا در ارسال به {cid}: {e}", flush=True)
            next_sched = next_run_time(now)
        time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
