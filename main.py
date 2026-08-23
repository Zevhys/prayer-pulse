import os
import re
import json
import time
from datetime import datetime
import requests
from zoneinfo import ZoneInfo

README_PATH = "README.md"
CACHE_PATH = "data/cache.json"

CITY = "Banjarmasin"
COUNTRY = "Indonesia"
TIMEZONE = "Asia/Makassar"

MARKER_START = "<!-- SHOLAT_TRACKER_START -->"
MARKER_END = "<!-- SHOLAT_TRACKER_END -->"

PRAYER_ORDER = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
API_METHOD = 11

MAX_RETRIES = 2
BACKOFF_SECONDS = [2, 5]
DRIFT_THRESHOLD_MINUTES = 10

# Fixed prayer windows: (start, end)
PRAYER_WINDOWS = {
    "Fajr": ("05:00", "06:00"),
    "Dhuhr": ("12:00", "15:00"),
    "Asr": ("15:00", "18:00"),
    "Maghrib": ("18:00", "19:00"),
    "Isha": ("19:00", "23:00"),
}


def ensure_dirs():
    os.makedirs("data", exist_ok=True)


def clean_time(t: str) -> str:
    return t.split(" ")[0].strip()


def hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def minutes_diff(a: str, b: str) -> int:
    return abs(hhmm_to_minutes(a) - hhmm_to_minutes(b))


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(obj):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def request_prayer_api_by_date(date_dd_mm_yyyy: str):
    url = (
        f"https://api.aladhan.com/v1/timingsByCity/{date_dd_mm_yyyy}"
        f"?city={CITY}&country={COUNTRY}&method={API_METHOD}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code") != 200:
        raise RuntimeError(f"API returned non-200 code: {payload.get('code')}")

    data = payload["data"]
    timings = data["timings"]

    prayers = {
        "Fajr": clean_time(timings["Fajr"]),
        "Dhuhr": clean_time(timings["Dhuhr"]),
        "Asr": clean_time(timings["Asr"]),
        "Maghrib": clean_time(timings["Maghrib"]),
        "Isha": clean_time(timings["Isha"]),
    }

    return {
        "date": date_dd_mm_yyyy,
        "prayers": prayers,
        "source": "api",
        "method": API_METHOD,
    }


def fetch_prayer_times_with_retry(date_dd_mm_yyyy: str):
    last_error = None
    attempts = 1 + MAX_RETRIES

    for attempt in range(1, attempts + 1):
        try:
            return request_prayer_api_by_date(date_dd_mm_yyyy)
        except Exception as e:
            last_error = e
            if attempt <= MAX_RETRIES:
                delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                time.sleep(delay)
            else:
                break

    raise RuntimeError(f"API failed after {attempts} attempts: {last_error}")


def current_state(prayers, now_local: datetime):
    now_min = now_local.hour * 60 + now_local.minute
    schedule = [(name, hhmm_to_minutes(prayers[name])) for name in PRAYER_ORDER]

    for i, (name, minute) in enumerate(schedule):
        next_name, next_minute = schedule[(i + 1) % len(schedule)]
        if i < len(schedule) - 1:
            if minute <= now_min < next_minute:
                return f"Now: {name}", f"Next: {next_name} at {prayers[next_name]}"
        else:
            if now_min >= minute or now_min < schedule[0][1]:
                return (
                    f"Now: {name}",
                    f"Next: {schedule[0][0]} at {prayers[schedule[0][0]]}",
                )

    return "Now: Outside prayer window", "Next: Fajr"


def prayer_window_statuses(now_local: datetime):
    now_min = now_local.hour * 60 + now_local.minute
    statuses = {}

    for p in PRAYER_ORDER:
        start_hhmm, end_hhmm = PRAYER_WINDOWS[p]
        start_min = hhmm_to_minutes(start_hhmm)
        end_min = hhmm_to_minutes(end_hhmm)

        if start_min <= now_min < end_min:
            statuses[p] = "OPEN"
        elif now_min < start_min:
            statuses[p] = "UPCOMING"
        else:
            statuses[p] = "CLOSED"

    return statuses


def detect_drift_warning(api_data, cache_data):
    if not cache_data:
        return None

    if cache_data.get("date") != api_data.get("date"):
        return None

    cache_prayers = cache_data.get("prayers", {})
    api_prayers = api_data.get("prayers", {})
    if not cache_prayers or not api_prayers:
        return None

    drifts = []
    for p in PRAYER_ORDER:
        if p in cache_prayers and p in api_prayers:
            d = minutes_diff(cache_prayers[p], api_prayers[p])
            if d > DRIFT_THRESHOLD_MINUTES:
                drifts.append((p, cache_prayers[p], api_prayers[p], d))

    if not drifts:
        return None

    parts = [f"{p}: {old} -> {new} ({d}m)" for p, old, new, d in drifts]
    return "Possible schedule anomaly detected: " + "; ".join(parts)


def render_block(
    prayer_data, error_text=None, drift_warning=None, include_last_updated=True
):
    now_local = datetime.now(ZoneInfo(TIMEZONE))
    updated_at = now_local.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now_local.strftime("%A")

    prayers = prayer_data["prayers"]
    now_line, next_line = current_state(prayers, now_local)
    statuses = prayer_window_statuses(now_local)

    lines = []
    lines.append("## 🕌 Prayer Tracker (WITA)")
    lines.append("")
    lines.append(f"- **City:** {CITY}, {COUNTRY}")
    lines.append(f"- **Timezone:** {TIMEZONE} (UTC+8)")
    lines.append(f"- **Today:** {weekday}, {prayer_data['date']}")
    lines.append(f"- **{now_line}**")
    lines.append(f"- **{next_line}**")
    if include_last_updated:
        lines.append(f"- **Last updated:** {updated_at}")
    lines.append("")

    if error_text:
        lines.append(f"> ⚠️ API issue: {error_text}")
        lines.append("> Showing latest available data (cache).")
        lines.append("")

    if drift_warning:
        lines.append(f"> ⚠️ {drift_warning}")
        lines.append("")

    lines.append("### Daily Prayer Window Tracker (Auto)")
    lines.append("| Prayer | Time | Status |")
    lines.append("|---|---|---|")
    for p in PRAYER_ORDER:
        _, end_hhmm = PRAYER_WINDOWS[p]
        time_range = f"{prayers[p]} - {end_hhmm}"
        lines.append(f"| {p} | {time_range} | {statuses[p]} |")

    return "\n".join(lines)


def extract_tracker_block(readme_text: str) -> str:
    pattern = re.compile(
        re.escape(MARKER_START) + r"(.*?)" + re.escape(MARKER_END), re.DOTALL
    )
    m = pattern.search(readme_text)
    return m.group(1).strip() if m else ""


def normalize_for_compare(text: str) -> str:
    lines = text.splitlines()
    filtered = [ln for ln in lines if not ln.strip().startswith("- **Last updated:**")]
    return "\n".join(filtered).strip()


def inject_readme_block(block):
    if not os.path.exists(README_PATH):
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(f"{MARKER_START}\nInitializing tracker...\n{MARKER_END}\n")

    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    old_block = extract_tracker_block(content)
    old_norm = normalize_for_compare(old_block)
    new_norm = normalize_for_compare(block)

    if old_norm == new_norm and old_block:
        return False

    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), re.DOTALL
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
    now_local = datetime.now(ZoneInfo(TIMEZONE))
    date_dd_mm_yyyy = now_local.strftime("%d-%m-%Y")

    error_text = None
    drift_warning = None

    existing_cache = load_cache()

    try:
        prayer_data = fetch_prayer_times_with_retry(date_dd_mm_yyyy)
        drift_warning = detect_drift_warning(prayer_data, existing_cache)
        save_cache(prayer_data)
    except Exception as e:
        error_text = str(e)
        cached = existing_cache
        if not cached:
            raise RuntimeError(f"API failed and no cache available: {error_text}")
        prayer_data = cached

    block = render_block(
        prayer_data,
        error_text=error_text,
        drift_warning=drift_warning,
        include_last_updated=True,
    )
    changed = inject_readme_block(block)

    print("README changed:", changed)
    print("Request date (WITA):", date_dd_mm_yyyy)
    if error_text:
        print("API fallback in use:", error_text)
    if drift_warning:
        print("Drift warning:", drift_warning)


if __name__ == "__main__":
    main()
