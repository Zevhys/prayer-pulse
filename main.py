import os
import re
import json
from datetime import datetime, timezone
import requests
from zoneinfo import ZoneInfo

README_PATH = "README.md"
CACHE_PATH = "data/cache.json"

CITY = "Banjarmasin"
COUNTRY = "Indonesia"
TIMEZONE = "Asia/Makassar"  # WITA (UTC+8)

MARKER_START = "<!-- SHOLAT_TRACKER_START -->"
MARKER_END = "<!-- SHOLAT_TRACKER_END -->"

PRAYER_ORDER = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
API_METHOD = 11  # AlAdhan method (can be changed later)


def ensure_dirs():
    os.makedirs("data", exist_ok=True)


def clean_time(t: str) -> str:
    # Example: "04:35 (+08)" -> "04:35"
    return t.split(" ")[0].strip()


def fetch_prayer_times():
    url = f"https://api.aladhan.com/v1/timingsByCity?city={CITY}&country={COUNTRY}&method={API_METHOD}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code") != 200:
        raise RuntimeError(f"API returned non-200 code: {payload.get('code')}")

    data = payload["data"]
    timings = data["timings"]
    date_info = data["date"]["gregorian"]["date"]  # dd-mm-yyyy

    prayers = {
        "Fajr": clean_time(timings["Fajr"]),
        "Dhuhr": clean_time(timings["Dhuhr"]),
        "Asr": clean_time(timings["Asr"]),
        "Maghrib": clean_time(timings["Maghrib"]),
        "Isha": clean_time(timings["Isha"]),
    }

    return {"date": date_info, "prayers": prayers, "source": "api"}


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(obj):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def current_state(prayers, now_local: datetime):
    now_min = now_local.hour * 60 + now_local.minute
    schedule = [(name, hhmm_to_minutes(prayers[name])) for name in PRAYER_ORDER]

    for i, (name, minute) in enumerate(schedule):
        next_name, next_minute = schedule[(i + 1) % len(schedule)]
        if i < len(schedule) - 1:
            if minute <= now_min < next_minute:
                return f"Now: {name}", f"Next: {next_name} at {prayers[next_name]}"
        else:
            # From Isha to next day's Fajr
            if now_min >= minute or now_min < schedule[0][1]:
                return f"Now: {name}", f"Next: {schedule[0][0]} at {prayers[schedule[0][0]]}"

    return "Now: Outside prayer window", "Next: Fajr"


def render_block(prayer_data, error_text=None):
    now_local = datetime.now(ZoneInfo(TIMEZONE))
    updated_at = now_local.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now_local.strftime("%A")

    prayers = prayer_data["prayers"]
    now_line, next_line = current_state(prayers, now_local)

    lines = []
    lines.append("## 🕌 Prayer Tracker (WITA)")
    lines.append("")
    lines.append(f"- **City:** {CITY}, {COUNTRY}")
    lines.append(f"- **Timezone:** {TIMEZONE} (UTC+8)")
    lines.append(f"- **Today:** {weekday}, {prayer_data['date']}")
    lines.append(f"- **{now_line}**")
    lines.append(f"- **{next_line}**")
    lines.append(f"- **Last updated:** {updated_at}")
    lines.append("")

    if error_text:
        lines.append(f"> ⚠️ API issue: {error_text}")
        lines.append("> Showing latest available data (cache).")
        lines.append("")

    lines.append("### Daily Prayer Times")
    lines.append("| Prayer | Time |")
    lines.append("|---|---|")
    for p in PRAYER_ORDER:
        lines.append(f"| {p} | {prayers[p]} |")

    return "\n".join(lines)


def inject_readme_block(block):
    if not os.path.exists(README_PATH):
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(
                "# Prayer Pulse\n\n"
                f"{MARKER_START}\n"
                "Initializing tracker...\n"
                f"{MARKER_END}\n"
            )

    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        re.DOTALL
    )
    replacement = f"{MARKER_START}\n{block}\n{MARKER_END}"

    if pattern.search(content):
        new_content = pattern.sub(replacement, content)
    else:
        new_content = content.rstrip() + "\n\n" + replacement + "\n"

    changed = new_content != content
    if changed:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)

    return changed


def main():
    ensure_dirs()
    error_text = None

    try:
        prayer_data = fetch_prayer_times()
        save_cache(prayer_data)
    except Exception as e:
        error_text = str(e)
        cached = load_cache()
        if not cached:
            raise RuntimeError(f"API failed and no cache available: {error_text}")
        prayer_data = cached

    block = render_block(prayer_data, error_text=error_text)
    changed = inject_readme_block(block)

    print("README changed:", changed)
    if error_text:
        print("API fallback in use:", error_text)


if __name__ == "__main__":
    main()