import os
import sqlite3
import datetime as dt
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, CallbackQueryHandler, CommandHandler

TOKEN = os.getenv("KAARADA_BOT_TOKEN")

# ========== БАЗА ДАННЫХ ==========
def db_path():
    return "/data/kaarada.db" if os.path.exists("/data") else "kaarada.db"

def init_db():
    conn = sqlite3.connect(db_path())
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_seen TEXT,
        last_seen TEXT,
        language TEXT DEFAULT "en",
        total_checks INTEGER DEFAULT 0
    )''')
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
    c.execute('''CREATE TABLE IF NOT EXISTS checks_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        target TEXT,
        target_type TEXT,
        verdict TEXT,
        created_at TEXT
    )''')
    conn.commit()
    conn.close()

def register_user(user_id):
    conn = sqlite3.connect(db_path())
    c = conn.cursor()
    now = dt.datetime.now().isoformat()
    r = c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if r is None:
        c.execute("INSERT INTO users (user_id, first_seen, last_seen, language, total_checks) VALUES (?, ?, ?, 'en', 0)", (user_id, now, now))
    else:
        c.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now, user_id))
    conn.commit()
    conn.close()

def get_lang(user_id):
    conn = sqlite3.connect(db_path())
    r = conn.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return r[0] if r and r[0] else "en"

def set_lang(user_id, lang):
    conn = sqlite3.connect(db_path())
    conn.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
    conn.commit()
    conn.close()

def add_signal(target, target_type, risk_level, comment, source, added_by):
    conn = sqlite3.connect(db_path())
    conn.execute(
        "INSERT INTO signals (target, target_type, risk_level, comment, source, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (target, target_type, risk_level, comment, source, added_by, dt.datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_signals(target):
    conn = sqlite3.connect(db_path())
    rows = conn.execute("SELECT risk_level, comment, source, created_at FROM signals WHERE target = ?", (target,)).fetchall()
    conn.close()
    return rows

def log_check(user_id, target, target_type, verdict):
    conn = sqlite3.connect(db_path())
    conn.execute(
        "INSERT INTO checks_log (user_id, target, target_type, verdict, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, target, target_type, verdict, dt.datetime.now().isoformat())
    )
    conn.execute("UPDATE users SET total_checks = total_checks + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(db_path())
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_checks = conn.execute("SELECT COUNT(*) FROM checks_log").fetchone()[0]
    total_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()
    return total_users, total_checks, total_signals

# ========== ЛОГИКА ПРОВЕРКИ ==========
def detect_target_type(text):
    text = text.strip()
    if re.match(r'^\+?[0-9]{9,15}$', text):
        return "phone"
    if re.match(r'^[0-9]{5,7}$', text):
        return "till"
    return "business"

def check_target(target, target_type, user_id, lang):
    signals = get_signals(target)
    
    if not signals:
        verdict = "🟢 Low risk"
        if lang == "sw":
            verdict = "🟢 Hatari ndogo"
        summary = "No reports found. Be careful anyway."
        if lang == "sw":
            summary = "Hakuna ripoti zilizopatikana. Kuwa mwangalifu."
        log_check(user_id, target, target_type, "green")
        return verdict, summary, 0
    
    risk_score = 0
    has_verified_source = False
    for risk_level, comment, source, created_at in signals:
        if risk_level == "high":
            risk_score += 3
        elif risk_level == "medium":
            risk_score += 2
        else:
            risk_score += 1
        if source and "eConfirm" in source:
            has_verified_source = True
    
    if has_verified_source and risk_score >= 3:
        verdict = "🔴 High risk"
        if lang == "sw":
            verdict = "🔴 Hatari kubwa"
    elif risk_score >= 5:
        verdict = "🔴 High risk"
        if lang == "sw":
            verdict = "🔴 Hatari kubwa"
    elif risk_score >= 2:
        verdict = "🟡 Medium risk"
        if lang == "sw":
            verdict = "🟡 Hatari ya kati"
    else:
        verdict = "🟢 Low risk"
        if lang == "sw":
            verdict = "🟢 Hatari ndogo"
    
    summary = f"Found {len(signals)} report(s):\n"
    for i, (risk, comment, source, created) in enumerate(signals[:5], 1):
        summary += f"{i}. [{risk.upper()}] {comment}\n"
        if source:
            summary += f"   Source: {source}\n"
    
    log_check(user_id, target, target_type, verdict.split()[1].lower())
    return verdict, summary, len(signals)

# ========== ТЕКСТЫ ==========
T = {
    "en": {
        "welcome": "🛡️ *KaaRada Lite*\n\nBefore you send money — check first.\n\nSend me:\n• A phone number\n• A Till number\n• A business name\n\nI'll check it and give you a verdict.",
        "choose_language": "🌐 Choose your language:",
        "lang_set": "✅ Language set to English.",
        "checking": "🔍 Checking...",
        "verdict": "📊 *Verdict:*",
        "summary": "📋 *Details:*",
        "no_reports": "No reports found.",
        "help": "Send me a phone number, Till number, or business name to check.",
        "stats": "📊 *KaaRada Lite Stats*\n\n👥 Users: {users}\n🔍 Total checks: {checks}\n⚠️ Signals: {signals}",
        "unauthorized": "⛔ Not authorized.",
        "add_signal_usage": "Usage:\n/addsignal <target> <type> <risk> <comment>\n\nTypes: phone, till, business\nRisk: low, medium, high",
        "add_signal_success": "✅ Signal added for {target}.",
        "report_button": "⚠️ Report this",
        "report_prompt": "Send me a report in this format:\n\n<target> | <comment>\n\nExample:\n0712345678 | Asked for payment upfront, never delivered.",
        "report_saved": "✅ Thank you! Your report is saved."
    },
    "sw": {
        "welcome": "🛡️ *KaaRada Lite*\n\nKabla ya kutuma pesa — angalia kwanza.\n\nNitume:\n• Namba ya simu\n• Namba ya Till\n• Jina la biashara\n\nNitakagua na kukupa uamuzi.",
        "choose_language": "🌐 Chagua lugha yako:",
        "lang_set": "✅ Lugha imewekwa Kiswahili.",
        "checking": "🔍 Inakagua...",
        "verdict": "📊 *Uamuzi:*",
        "summary": "📋 *Maelezo:*",
        "no_reports": "Hakuna ripoti zilizopatikana.",
        "help": "Nitume namba ya simu, namba ya Till, au jina la biashara ili kukagua.",
        "stats": "📊 *Takwimu za KaaRada Lite*\n\n👥 Watumiaji: {users}\n🔍 Jumla ya ukaguzi: {checks}\n⚠️ Ishara: {signals}",
        "unauthorized": "⛔ Hauruhusiwi.",
        "add_signal_usage": "Matumizi:\n/addsignal <lengo> <aina> <hatari> <maoni>\n\nAina: phone, till, business\nHatari: low, medium, high",
        "add_signal_success": "✅ Ishara imeongezwa kwa {target}.",
        "report_button": "⚠️ Ripoti hii",
        "report_prompt": "Nitume ripoti kwa muundo huu:\n\n<lengo> | <maoni>\n\nMfano:\n0712345678 | Alidai malipo mapema, hakutoa bidhaa.",
        "report_saved": "✅ Asante! Ripoti yako imehifadhiwa."
    }
}

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton("🇰🇪 Kiswahili", callback_data="lang_sw")]
    ])

def get_verdict_keyboard(lang, target):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T[lang]['report_button'], callback_data=f"report_{target}")]
    ])

# ========== ОБРАБОТЧИКИ ==========
async def start_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    await update.message.reply_text(T[lang]['welcome'], parse_mode="Markdown", reply_markup=get_language_keyboard())

async def help_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    await update.message.reply_text(T[lang]['help'])

async def stats_command(update, context):
    users, checks, signals = get_stats()
    await update.message.reply_text(T["en"]['stats'].format(users=users, checks=checks, signals=signals), parse_mode="Markdown")

async def addsignal_command(update, context):
    user_id = update.effective_user.id
    OWNER_ID = int(os.getenv("KAARADA_OWNER_ID", "8743362338"))
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    args = context.args
    if len(args) < 4:
        await update.message.reply_text(T["en"]['add_signal_usage'])
        return
    target = args[0]
    target_type = args[1]
    risk_level = args[2]
    comment = " ".join(args[3:])
    add_signal(target, target_type, risk_level, comment, "admin", user_id)
    await update.message.reply_text(T["en"]['add_signal_success'].format(target=target))

async def handle_message(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    text = update.message.text.strip()
    
    if "|" in text and len(text.split("|")) == 2:
        parts = text.split("|")
        target = parts[0].strip()
        comment = parts[1].strip()
        add_signal(target, detect_target_type(target), "medium", comment, "user", user_id)
        await update.message.reply_text(T[lang]['report_saved'])
        return
    
    target_type = detect_target_type(text)
    await update.message.reply_text(T[lang]['checking'])
    
    verdict, summary, count = check_target(text, target_type, user_id, lang)
    
    response = f"{T[lang]['verdict']} {verdict}\n\n"
    if count > 0:
        response += f"{T[lang]['summary']}\n{summary}"
    else:
        response += T[lang]['no_reports']
    
    await update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_verdict_keyboard(lang, text))

async def button_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    data = query.data
    
    if data.startswith("lang_"):
        new_lang = data.split("_")[1]
        set_lang(user_id, new_lang)
        await query.edit_message_text(T[new_lang]['lang_set'])
        return
    
    if data.startswith("report_"):
        target = data.split("_", 1)[1]
        context.user_data['report_target'] = target
        await query.message.reply_text(T[lang]['report_prompt'])
        return

if __name__ == "__main__":
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("addsignal", addsignal_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("🛡️ KaaRada Lite started.")
    app.run_polling()
