import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re
import os
import datetime as dt

# ========== НАСТРОЙКИ ==========
BASE_URL = "https://econfirm.co.ke"
SCAM_WATCH_URL = f"{BASE_URL}/scam-watch"
DB_PATH = "/data/kaarada.db" if os.path.exists("/data") else "kaarada.db"
REQUEST_DELAY = 2  # секунды между запросами, чтобы не нагружать сайт
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# ========== БАЗА ДАННЫХ ==========
def init_db():
    """Создать таблицы, если их нет"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target TEXT,
        target_type TEXT,
        risk_level TEXT,
        comment TEXT,
        source TEXT,
        added_by INTEGER,
        created_at TEXT
    )''')
    conn.commit()
    conn.close()

def signal_exists(target):
    """Проверить, есть ли уже такой сигнал"""
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT id FROM signals WHERE target = ?", (target,)).fetchone()
    conn.close()
    return r is not None

def add_signal(target, target_type, risk_level, comment, source):
    """Добавить сигнал в базу KaaRada"""
    if signal_exists(target):
        print(f"⏭️  Пропуск (уже есть): {target}")
        return False
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO signals (target, target_type, risk_level, comment, source, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (target, target_type, risk_level, comment, source, 0, dt.datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    print(f"✅ Добавлено: {target} ({target_type})")
    return True

# ========== ПАРСЕР ==========
def get_report_links():
    """Собрать ссылки на все отчёты со страницы scam-watch"""
    print(f"📥 Загружаю {SCAM_WATCH_URL}...")
    try:
        r = requests.get(SCAM_WATCH_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"❌ Ошибка загрузки: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    links = set()

    # Ищем все ссылки, ведущие на отчёты
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/scam-watch/reports/" in href:
            full_url = BASE_URL + href if href.startswith("/") else href
            links.add(full_url)

    print(f"🔗 Найдено ссылок на отчёты: {len(links)}")
    return list(links)

def parse_report(url):
    """Извлечь данные из одного отчёта"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"❌ Ошибка загрузки {url}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    # --- Определяем идентификатор (телефон, email, сайт) ---
    # Телефон: +254..., 07..., 01...
    phone_match = re.search(r'(?:\+254|0)[17]\d{8}', text)
    # Email
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    # Сайт (простая проверка)
    site_match = re.search(r'https?://[^\s]+', text)

    target = None
    target_type = None

    if phone_match:
        target = phone_match.group(0)
        target_type = "phone"
    elif email_match:
        target = email_match.group(0)
        target_type = "email"
    elif site_match:
        target = site_match.group(0)
        target_type = "site"

    if not target:
        return None

    # --- Извлекаем описание (блок "What was reported") ---
    comment = "Reported on eConfirm"
    if "What was reported" in text:
        parts = text.split("What was reported", 1)
        if len(parts) > 1:
            # Берём первые 300 символов после заголовка
            raw = parts[1].strip().split("\n")[0:3]
            comment = " ".join(raw)[:300]

    # --- Определяем категорию (для risk_level) ---
    risk = "high"  # по умолчанию — высокий, так как это скам-репорт
    if "Job advertisement" in text:
        comment = f"Job scam: {comment}"
    elif "Investment" in text:
        comment = f"Investment scam: {comment}"
    elif "Service" in text:
        comment = f"Service scam: {comment}"

    return {
        "target": target,
        "target_type": target_type,
        "risk_level": risk,
        "comment": comment,
        "source": "eConfirm Scam Watch",
    }

# ========== ОСНОВНОЙ ЗАПУСК ==========
def run_parser():
    print(f"🕐 Запуск парсера: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    init_db()

    links = get_report_links()
    if not links:
        print("❌ Нет ссылок для обработки.")
        return

    added = 0
    for i, url in enumerate(links, 1):
        print(f"\n[{i}/{len(links)}] {url}")
        data = parse_report(url)
        if data:
            if add_signal(
                data["target"],
                data["target_type"],
                data["risk_level"],
                data["comment"],
                data["source"],
            ):
                added += 1
        time.sleep(REQUEST_DELAY)

    print(f"\n🏁 Готово. Добавлено новых сигналов: {added}")

if __name__ == "__main__":
    run_parser()
