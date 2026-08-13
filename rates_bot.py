#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات نرخ‌های طلا، ارز، نقره و کریپتو به تومان (منبع: tgju.org)

دو حالت اجرا:
  python3 rates_bot.py --once     → یک پیام می‌فرستد و خارج می‌شود (برای GitHub Actions)
  python3 rates_bot.py            → حالت شنونده: به /start فوراً جواب می‌دهد
                                     + سر ساعت‌های ۳، ۹، ۱۵ و ۲۱ خودکار می‌فرستد

متغیرهای محیطی:
  TELEGRAM_TOKEN  (اجباری)
  CHAT_ID         (برای --once اجباری؛ در حالت شنونده اختیاری)
  SKIP_SCHEDULE=1 (زمان‌بندی خاموش، فقط پاسخ به پیام‌ها)
  MAX_RUNTIME=... (ثانیه؛ بعد از این مدت تمیز خارج می‌شود — برای GitHub Actions)

منبع داده:
  1) call1.tgju.org/ajax.json                    ← اصلی (حتی وقتی بازار بسته است)
  2) api.tgju.org/.../today-table-data/{symbol}  ← پشتیبان
"""
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    print("❌ متغیر محیطی TELEGRAM_TOKEN تعریف نشده است.", flush=True)
    sys.exit(1)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

# ⏰ ساعت‌های واقعیِ اجرا — یک ساعت زودتر از ساعت‌های اعلام‌شده.
# چون سرورِ گیت‌هاب معمولاً حدود یک ساعت دیر اجرا می‌کند، اجرا را زودتر
# شروع می‌کنیم تا پیام تقریباً سرِ ساعتِ اعلام‌شده به دست کاربر برسد.
SCHEDULE_HOURS = (7, 11, 15, 19)

# 🏷 ساعت‌هایی که در متنِ پیام‌ها به کاربر نشان داده می‌شود.
# عمداً با SCHEDULE_HOURS فرق دارد: کاربر ۸/۱۲/۱۶/۲۰ می‌بیند.
DISPLAY_HOURS = (8, 12, 16, 20)
SKIP_SCHEDULE = os.environ.get("SKIP_SCHEDULE") == "1"
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME") or 0)
STATE_FILE = os.environ.get("STATE_FILE", "subscribers.json")
API = f"https://api.telegram.org/bot{TOKEN}"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

_RUNNING = True


def _stop(signum, frame):          # noqa: ARG001
    global _RUNNING
    _RUNNING = False
    print("\n⏹  دریافت سیگنال توقف، در حال خروج تمیز…", flush=True)


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def log(msg: str):
    print(f"[{datetime.now(TEHRAN_TZ):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --------------------------- ابزار شبکه ---------------------------

def fetch_json(url: str, headers=None, timeout: int = 25, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:                                  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last


# --------------------------- دریافت نرخ‌ها ---------------------------

_AJAX_CACHE = {"data": None, "ts": 0.0}


def ajax_all(max_age: int = 45):
    """کل نرخ‌ها را یک‌جا می‌گیرد (کش کوتاه ⇒ پاسخ به /start تقریباً آنی است)."""
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
    try:
        row = (ajax_all() or {}).get(symbol)
        if row and row.get("p") not in (None, "", "0"):
            raw = _num(row["p"])
            stale = False
            ts = row.get("ts") or ""
            if ts:
                try:
                    stale = (datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").date()
                             != datetime.now(TEHRAN_TZ).date())
                except ValueError:
                    pass
            return {"raw": raw, "toman": raw / 10,
                    "time": row.get("t_en") or row.get("t") or "",
                    "pct": row.get("dp"), "dir": row.get("dt") or "",
                    "stale": stale}
    except Exception as e:                                      # noqa: BLE001
        print(f"⚠️ ajax.json برای {symbol} کار نکرد: {e}", flush=True)

    url = f"https://api.tgju.org/v1/market/indicator/today-table-data/{symbol}?lang=fa"
    rows = (fetch_json(url).get("data") or [])
    if not rows:
        raise RuntimeError(f"داده‌ای برای «{symbol}» موجود نیست")
    row = rows[0]
    raw = _num(row[0])
    pct = None
    if len(row) > 3:
        m = re.search(r">([\d.]+)%<", str(row[3]))
        if m:
            pct = float(m.group(1))
    return {"raw": raw, "toman": raw / 10, "time": row[1],
            "pct": pct, "dir": "", "stale": False}


def safe_rate(symbol: str):
    try:
        return tgju_today(symbol)
    except Exception as e:                                      # noqa: BLE001
        print(f"⚠️ نرخ «{symbol}» دریافت نشد: {e}", flush=True)
        return None


# --------------------------- قالب‌بندی ---------------------------

def fa_digits(s) -> str:
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fmt(n, decimals: int = 0) -> str:
    if n is None:
        return "نامشخص"
    if decimals == 0 and abs(n) < 1:
        decimals = 4
    return fa_digits(f"{n:,.{decimals}f}".replace(",", "٬"))


def arrow(rate) -> str:
    d = (rate or {}).get("dir") or ""
    pct = (rate or {}).get("pct")
    if not pct:
        return ""
    # نکته: 🔺 و 🔻 هر دو در یونیکد «مثلث قرمز» هستند،
    # پس برای صعود از مربع سبز + مثلث سیاه استفاده می‌کنیم.
    if d == "high":
        sign = "🟢 ▲"
    elif d == "low":
        sign = "🔴 ▼"
    else:
        sign = "⚪️ ▪"
    return f"  {sign} {fa_digits(pct)}٪"


def line(rate, unit: str = "تومان") -> str:
    if rate is None:
        return "   ⛔ در دسترس نیست"
    mark = " ⏳" if rate.get("stale") else ""
    return f"   {fmt(rate['toman'])} {unit}{mark}{arrow(rate)}"


def usd_line(rate, decimals: int = 2, unit: str = "دلار") -> str:
    """
    برای نمادهای جهانی که قیمتشان از اول دلاری است
    (انس طلا، نفت برنت، انس نقره) — نباید تقسیم بر ۱۰ شوند.
    """
    if rate is None:
        return "   ⛔ در دسترس نیست"
    mark = " ⏳" if rate.get("stale") else ""
    return f"   {fmt(rate['raw'], decimals)} {unit}{mark}{arrow(rate)}"


def to_jalali(gy: int, gm: int, gd: int):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2, gm2, gd2 = gy - 1600, gm - 1, gd - 1
    n = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    n += g_d_m[gm2] + gd2
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        n += 1
    n -= 79
    np_ = n // 12053
    n %= 12053
    jy = 979 + 33 * np_ + 4 * (n // 1461)
    n %= 1461
    if n >= 366:
        jy += (n - 1) // 365
        n = (n - 1) % 365
    if n < 186:
        return jy, 1 + n // 31, 1 + n % 31
    return jy, 7 + (n - 186) // 30, 1 + (n - 186) % 30


def build_message(header: str = "💰📊 نرخ‌های لحظه‌ای بازار") -> str:
    # --- بازار داخلی (ریال ← تومان) ---
    dollar    = safe_rate("price_dollar_rl")
    gold18    = safe_rate("geram18")
    coin      = safe_rate("sekee")
    silver999 = safe_rate("silver_999")
    tether    = safe_rate("crypto-tether-irr")   # تتر (USDT) — نه usdt-irr که از ۲۰۲۰ متوقف شده
    btc       = safe_rate("crypto-bitcoin-irr")
    doge      = safe_rate("crypto-dogecoin-irr")
    btc_usd   = safe_rate("crypto-bitcoin")

    # --- بازار جهانی (دلاری) ---
    ons_gold   = safe_rate("ons")         # انس طلای جهانی
    brent      = safe_rate("oil_brent")   # نفت برنت
    ons_silver = safe_rate("silver")      # انس نقره‌ی جهانی

    if all(x is None for x in (dollar, gold18, silver999, btc, doge)):
        raise RuntimeError("هیچ نرخی از tgju.org دریافت نشد")

    now = datetime.now(TEHRAN_TZ)
    jy, jm, jd = to_jalali(now.year, now.month, now.day)
    months = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
              "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
    date_fa = f"{fa_digits(jd)} {months[jm - 1]} {fa_digits(jy)}"
    btc_usd_txt = f" ({fmt(btc_usd['raw'], 2)} دلار)" if btc_usd else ""

    out = [
        header,
        "——————————————",
        f"🕘 {date_fa} — ساعت {fa_digits(now.strftime('%H:%M'))} به وقت تهران",
        "",
        "💵 دلار آمریکا (بازار آزاد)", line(dollar), "",
        "🥇 طلای ۱۸ عیار (هر گرم)", line(gold18), "",
        "🪙 سکه امامی", line(coin), "",
        "🥈 نقره ۹۹۹ (هر گرم)", line(silver999), "",
        "💲 تتر (USDT)", line(tether), "",
        "₿ بیت‌کوین",
        (f"   {fmt(btc['toman'])} تومان{btc_usd_txt}{arrow(btc)}"
         if btc else "   ⛔ در دسترس نیست"), "",
        "🐕 دوج‌کوین", line(doge), "",
        "🌍 بازار جهانی",
        "——————————————",
        "🥇 انس طلا", usd_line(ons_gold), "",
        "🛢 نفت برنت (هر بشکه)", usd_line(brent), "",
        "🥈 انس نقره", usd_line(ons_silver), "",
    ]
    if any(r and r.get("stale") for r in (dollar, gold18, coin, silver999)):
        out += ["⏳ = بازار بسته است؛ آخرین نرخ ثبت‌شده نمایش داده می‌شود.", ""]
    hours_fa = "، ".join(fa_digits(f"{h:02d}:00") for h in DISPLAY_HOURS)
    out += [
        f"🔄 ارسال خودکار در ساعت‌های {hours_fa} به وقت تهران",
        "📌 منبع: tgju.org",
    ]
    return "\n".join(out)


# --------------------------- تلگرام ---------------------------

KEYBOARD = {
    "keyboard": [[{"text": "📊 نرخ لحظه‌ای"}], [{"text": "ℹ️ راهنما"}]],
    "resize_keyboard": True,
}

HELP_TEXT = (
    "ℹ️ راهنمای ربات نرخ‌ها\n"
    "——————————————\n"
    "📊 /rates  یا دکمه «نرخ لحظه‌ای» → نرخ همین لحظه\n"
    "🔔 /start → شروع و دریافت فوری نرخ‌ها\n"
    "❓ /help → همین راهنما\n\n"
    "🔄 به‌صورت خودکار هم ساعت‌های ۸ صبح، ۱۲ ظهر، "
    "۴ بعدازظهر و ۸ شب نرخ‌ها ارسال می‌شود.\n"
    "📌 منبع همه نرخ‌ها: tgju.org"
)


def tg(method: str, payload: dict, timeout: int = 30):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{API}/{method}", data=body,
                                 headers={"Content-Type": "application/json", **UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"تلگرام خطا داد ({e.code}): {detail}") from None


def send_telegram(chat_id, text: str, keyboard: bool = False):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = KEYBOARD
    res = tg("sendMessage", payload)
    if not res.get("ok"):
        raise RuntimeError(f"تلگرام پیام را نپذیرفت: {res}")
    return res


def typing(chat_id):
    """نشانگر «در حال تایپ…» تا کاربر بداند درخواستش دریافت شده."""
    try:
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
    except Exception:                                           # noqa: BLE001
        pass


def set_commands():
    try:
        tg("setMyCommands", {"commands": [
            {"command": "start", "description": "شروع و دریافت فوری نرخ‌ها"},
            {"command": "rates", "description": "📊 نرخ لحظه‌ای"},
            {"command": "help",  "description": "راهنما"},
        ]})
        log("منوی دستورات ربات تنظیم شد ✓")
    except Exception as e:                                      # noqa: BLE001
        print(f"⚠️ تنظیم منوی دستورات ناموفق: {e}", flush=True)


# --------------------------- ذخیره‌ی مشترکین ---------------------------

def load_subs() -> set:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:                                           # noqa: BLE001
        return set()


def save_subs(subs: set):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(subs), f)
    except Exception as e:                                      # noqa: BLE001
        print(f"⚠️ ذخیره‌ی مشترکین ناموفق: {e}", flush=True)


# --------------------------- زمان‌بندی ---------------------------

def next_run_time(now_tz):
    for hh in SCHEDULE_HOURS:
        t = now_tz.replace(hour=hh, minute=0, second=0, microsecond=0)
        if t > now_tz:
            return t
    return (now_tz.replace(hour=SCHEDULE_HOURS[0], minute=0, second=0, microsecond=0)
            + timedelta(days=1))


# --------------------------- پردازش پیام‌ها ---------------------------

def handle_message(msg, subs: set) -> bool:
    chat = msg.get("chat") or {}
    if chat.get("type") not in ("private", "group", "supergroup"):
        return False
    chat_id = chat["id"]
    text = (msg.get("text") or "").strip()
    name = (msg.get("from") or {}).get("first_name", "")

    low = text.lower().split("@")[0]
    is_help = low in ("/help", "راهنما") or "راهنما" in text

    typing(chat_id)          # ← کاربر بلافاصله «در حال تایپ…» می‌بیند

    if is_help:
        send_telegram(chat_id, HELP_TEXT, keyboard=True)
        log(f"راهنما → {chat_id} ✓")
        return True

    try:
        if low == "/start":
            body = build_message(f"👋 سلام {name}! نرخ‌های همین لحظه:")
            hrs = "، ".join(fa_digits(f"{h:02d}:00") for h in DISPLAY_HOURS)
            reply = (body + f"\n\n✅ از این پس ساعت‌های {hrs} هم برایتان ارسال می‌شود.\n"
                            "برای نرخ لحظه‌ای هر وقت خواستید /rates را بزنید.")
        else:
            reply = build_message()
    except Exception as e:                                      # noqa: BLE001
        reply = f"⚠️ فعلاً دریافت نرخ‌ها ممکن نیست، چند لحظه بعد دوباره امتحان کنید.\n\n({e})"

    send_telegram(chat_id, reply, keyboard=True)
    if chat_id not in subs:
        subs.add(chat_id)
        save_subs(subs)
        log(f"مشترک جدید: {chat_id} ({name})")
    log(f"پاسخ فوری → {chat_id} ✓")
    return True


def poll_once(offset: dict, subs: set):
    """long-polling: تلگرام تا ۲۵ ثانیه اتصال را باز نگه می‌دارد ⇒ واکنش آنی."""
    try:
        res = tg("getUpdates", {"timeout": 25, "offset": offset["n"],
                                "allowed_updates": ["message"]}, timeout=40)
    except Exception as e:                                      # noqa: BLE001
        msg = str(e)
        if "409" in msg:
            print("⚠️ ۴۰۹: نمونه‌ی دیگری از ربات هم در حال اجراست یا webhook فعال "
                  "است. فقط یک نمونه را روشن نگه دارید.", flush=True)
            try:
                tg("deleteWebhook", {"drop_pending_updates": False})
            except Exception:                                   # noqa: BLE001
                pass
        else:
            print(f"خطا در دریافت پیام‌ها: {e}", flush=True)
        time.sleep(5)
        return

    for upd in res.get("result", []):
        offset["n"] = upd["update_id"] + 1
        try:
            if upd.get("message"):
                handle_message(upd["message"], subs)
        except Exception as e:                                  # noqa: BLE001
            print(f"خطا در پردازش پیام: {e}", flush=True)


# --------------------------- اجرا ---------------------------

def align_to_hour(max_wait: int = 2100):
    """
    کرانِ گیت‌هاب دقیق نیست و معمولاً چند دقیقه دیر (گاهی زود) اجرا می‌شود.
    این تابع تا نزدیک‌ترین ساعتِ زمان‌بندی صبر می‌کند تا پیام سرِ ساعت برسد.
    اگر بیش از max_wait ثانیه فاصله باشد (یعنی کران خیلی دیر شده)، صبر نمی‌کند
    و فوراً می‌فرستد — بهتر است پیام دیر برسد تا اصلاً نرسد.
    """
    if os.environ.get("NO_ALIGN") == "1":
        return
    now = datetime.now(TEHRAN_TZ)

    # فقط تا نوبتِ **بعدی** صبر می‌کنیم (نه نوبتِ گذشته).
    future = [
        now.replace(hour=h, minute=0, second=0, microsecond=0) + d
        for h in SCHEDULE_HOURS
        for d in (timedelta(0), timedelta(days=1))
    ]
    target = min((t for t in future if t > now), default=None)
    if target is None:
        return

    wait = (target - now).total_seconds()
    if wait > max_wait:
        # سرِ ساعتِ بعدی خیلی دور است ⇒ این اجرا «سرِ نوبت» نیست
        # (اجرای دستی یا کرانِ خیلی دیرشده). صبر بی‌فایده است.
        log("⏱ خارج از بازه‌ی سرِ ساعت؛ فوری ارسال می‌شود.")
        return

    # اگر نوبتِ پیشِ رو قبلاً ارسال شده، صبر کردن فقط تأخیر است.
    if already_sent(f"{target:%Y-%m-%d-%H}"):
        log(f"⏱ نوبت {target:%H:%M} قبلاً ارسال شده؛ صبر نمی‌کنیم.")
        return

    log(f"⏱ {int(wait)} ثانیه صبر تا سرِ ساعت {target:%H:%M} …")
    time.sleep(wait)


MARKER_FILE = os.environ.get("MARKER_FILE", ".sent_marker")


def slot_id(now_tz) -> str:
    """
    شناسه‌ی نوبتِ فعلی = آخرین ساعتِ زمان‌بندی که **گذشته** است.

    ⚠️ نکته‌ی مهم (باگی که قبلاً باعث تأخیر می‌شد):
    قبلاً «نزدیک‌ترین» ساعت انتخاب می‌شد و این شاملِ ساعت‌های آینده هم بود.
    یعنی اگر ساعت ۱۴:۳۰ کد را آپلود می‌کردید، آن اجرا نوبتِ ۱۶:۰۰ را
    «مصرف‌شده» علامت می‌زد و پیامِ واقعیِ ۱۶:۰۰ ارسال نمی‌شد.
    حالا فقط نوبت‌های گذشته انتخاب می‌شوند؛ پس هیچ اجرایی نمی‌تواند
    نوبتِ آینده را بسوزاند.
    """
    candidates = [
        now_tz.replace(hour=h, minute=0, second=0, microsecond=0) + d
        for h in SCHEDULE_HOURS
        for d in (timedelta(0), timedelta(days=-1))
    ]
    past = [t for t in candidates if t <= now_tz]
    target = max(past) if past else min(candidates)
    return f"{target:%Y-%m-%d-%H}"


def slot_is_fresh(slot: str, now_tz, max_age: int = 3600) -> bool:
    """
    آیا نوبتِ داده‌شده «تازه» است؟ یعنی کمتر از max_age ثانیه از سرِ آن
    ساعت گذشته است.

    اگر اجرا خیلی دیرتر از سرِ ساعت باشد (مثلاً ۱۴:۳۰ که ۲.۵ ساعت از
    نوبتِ ۱۲:۰۰ گذشته)، آن اجرا یک اجرای «خارج از نوبت» است و نباید
    نوبتِ ۱۲:۰۰ را مصرف‌شده علامت بزند.
    """
    try:
        y, m, d, h = (int(x) for x in slot.split("-"))
    except ValueError:
        return False
    slot_dt = now_tz.replace(year=y, month=m, day=d, hour=h,
                             minute=0, second=0, microsecond=0)
    return 0 <= (now_tz - slot_dt).total_seconds() <= max_age


def already_sent(slot: str) -> bool:
    """
    اگر همین نوبت قبلاً ارسال شده باشد True برمی‌گرداند.
    لازم است چون هم cron-job.org و هم کرانِ پشتیبانِ گیت‌هاب
    ممکن است یک نوبت را دوبار اجرا کنند.
    """
    if os.environ.get("DEDUPE_WINDOW") in (None, "", "0"):
        return False
    try:
        with open(MARKER_FILE, encoding="utf-8") as f:
            return f.read().strip() == slot
    except OSError:
        return False


def mark_sent(slot: str):
    try:
        with open(MARKER_FILE, "w", encoding="utf-8") as f:
            f.write(slot)
    except OSError as e:                                        # noqa: BLE001
        print(f"⚠️ ذخیره‌ی نشانه‌ی ارسال ناموفق: {e}", flush=True)


def run_once(chat_id):
    if not chat_id:
        print("❌ chat_id داده نشده (متغیر CHAT_ID یا آرگومان اول)", flush=True)
        sys.exit(1)

    # بررسی اولیه: اگر همین حالا معلوم است که نوبتِ جاری ارسال شده،
    # بی‌خود صبر نکن.
    now = datetime.now(TEHRAN_TZ)
    slot = slot_id(now)
    if slot_is_fresh(slot, now) and already_sent(slot):
        log(f"⏭ نوبت {slot} قبلاً ارسال شده — از ارسال دوباره صرف‌نظر شد.")
        return

    # ممکن است اجرا کمی زودتر از سرِ ساعت شروع شده باشد؛ صبر می‌کنیم.
    align_to_hour()

    # ⚠️ نوبت را **بعد از** صبر دوباره حساب می‌کنیم. اگر قبل از صبر
    # حساب می‌شد، اجرایی که ۰۷:۴۵ شروع شده نوبتِ دیروز را می‌دید و
    # نشانه‌ی ۰۸:۰۰ را ثبت نمی‌کرد → پیام دوباره ارسال می‌شد.
    now = datetime.now(TEHRAN_TZ)
    slot = slot_id(now)
    fresh = slot_is_fresh(slot, now)

    # قفلِ ضدتکرار فقط برای اجراهای «سرِ نوبت» معنی دارد.
    if fresh and already_sent(slot):
        log(f"⏭ نوبت {slot} قبلاً ارسال شده — از ارسال دوباره صرف‌نظر شد.")
        return
    try:
        text = build_message()
    except Exception as e:                                      # noqa: BLE001
        print(f"❌ ساخت پیام شکست خورد: {e}", flush=True)
        try:
            send_telegram(chat_id, f"⚠️ ربات نرخ‌ها: دریافت داده از tgju.org "
                                   f"ناموفق بود.\n\nجزئیات: {e}")
        except Exception as e2:                                 # noqa: BLE001
            print(f"❌ ارسال پیام خطا هم شکست خورد: {e2}", flush=True)
        sys.exit(1)
    send_telegram(chat_id, text)
    if fresh:
        mark_sent(slot)
        log(f"پیام ارسال شد ✓ (نوبت {slot})")
    else:
        # اجرای دستی / آزمایشی خارج از نوبت — نشانه نوشته نمی‌شود تا
        # نوبتِ واقعیِ بعدی حتماً ارسال شود.
        log("پیام ارسال شد ✓ (اجرای خارج از نوبت — نشانه ثبت نشد)")


def run_listener(chat_id, send_now: bool):
    subs = load_subs()
    if chat_id:
        subs.add(int(chat_id))

    try:                       # جلوگیری از خطای ۴۰۹ اگر قبلاً webhook ست شده
        tg("deleteWebhook", {"drop_pending_updates": False})
    except Exception:                                           # noqa: BLE001
        pass

    try:
        me = tg("getMe", {})
        log(f"ربات متصل شد: @{me['result']['username']} ✓")
    except Exception as e:                                      # noqa: BLE001
        print(f"❌ اتصال به تلگرام ناموفق: {e}", flush=True)
        sys.exit(1)

    set_commands()

    if send_now and subs:
        try:
            text = build_message()
            for cid in subs:
                send_telegram(cid, text, keyboard=True)
            log("پیام اولیه ارسال شد ✓")
        except Exception as e:                                  # noqa: BLE001
            print(f"خطا در ارسال پیام اولیه: {e}", flush=True)

    offset = {"n": 0}
    next_sched = None if SKIP_SCHEDULE else next_run_time(datetime.now(TEHRAN_TZ))
    started = time.time()
    log(f"🟢 ربات فعال است و منتظر /start می‌ماند. زمان‌بندی: "
        f"{'خاموش' if SKIP_SCHEDULE else f'{next_sched:%Y-%m-%d %H:%M}'}")

    while _RUNNING:
        if MAX_RUNTIME and time.time() - started > MAX_RUNTIME:
            log("⏱ سقف زمان اجرا رسید، خروج تمیز.")
            break

        poll_once(offset, subs)

        now = datetime.now(TEHRAN_TZ)
        if next_sched is not None and now >= next_sched:
            try:
                text = build_message()
            except Exception as e:                              # noqa: BLE001
                print(f"خطا در ساخت پیام زمان‌بندی‌شده: {e}", flush=True)
                text = None
            if text:
                for cid in list(subs):
                    try:
                        send_telegram(cid, text)
                        log(f"پیام زمان‌بندی‌شده → {cid} ✓")
                    except Exception as e:                      # noqa: BLE001
                        print(f"خطا در ارسال به {cid}: {e}", flush=True)
            next_sched = next_run_time(now)

    log("⏹ ربات متوقف شد.")


def main():
    args = sys.argv[1:]
    chat_id = os.environ.get("CHAT_ID") or (
        args[0] if args and not args[0].startswith("--") else None)
    if "--once" in args:
        run_once(chat_id)
    else:
        run_listener(chat_id, send_now="--now" in args)


if __name__ == "__main__":
    main()
