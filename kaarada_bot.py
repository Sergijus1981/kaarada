import os
import sqlite3
import datetime as dt
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, CallbackQueryHandler, CommandHandler

TOKEN = os.getenv("KAARADA_BOT_TOKEN")
OWNER_ID = int(os.getenv("KAARADA_OWNER_ID", "8743362338"))

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
        total_checks INTEGER DEFAULT 0,
        state TEXT DEFAULT NULL
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
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        target TEXT,
        added_at TEXT,
        last_notified_at TEXT,
        UNIQUE(user_id, target)
    )''')
    conn.commit()
    conn.close()

def register_user(user_id):
    conn = sqlite3.connect(db_path())
    c = conn.cursor()
    now = dt.datetime.now().isoformat()
    r = c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if r is None:
        c.execute("INSERT INTO users (user_id, first_seen, last_seen, language, total_checks, state) VALUES (?, ?, ?, 'en', 0, NULL)", (user_id, now, now))
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

def get_state(user_id):
    conn = sqlite3.connect(db_path())
    r = conn.execute("SELECT state FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return r[0] if r else None

def set_state(user_id, state):
    conn = sqlite3.connect(db_path())
    conn.execute("UPDATE users SET state = ? WHERE user_id = ?", (state, user_id))
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

# ========== WATCHLIST ==========
def add_to_watchlist(user_id, target):
    conn = sqlite3.connect(db_path())
    try:
        conn.execute("INSERT INTO watchlist (user_id, target, added_at) VALUES (?, ?, ?)", (user_id, target, dt.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def remove_from_watchlist(user_id, target):
    conn = sqlite3.connect(db_path())
    conn.execute("DELETE FROM watchlist WHERE user_id = ? AND target = ?", (user_id, target))
    conn.commit()
    conn.close()

def get_watchlist(user_id):
    conn = sqlite3.connect(db_path())
    rows = conn.execute("SELECT target FROM watchlist WHERE user_id = ? ORDER BY added_at DESC", (user_id,)).fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_all_watchers(target):
    conn = sqlite3.connect(db_path())
    rows = conn.execute("SELECT user_id FROM watchlist WHERE target = ?", (target,)).fetchall()
    conn.close()
    return [r[0] for r in rows]

def update_notified(user_id, target):
    conn = sqlite3.connect(db_path())
    conn.execute("UPDATE watchlist SET last_notified_at = ? WHERE user_id = ? AND target = ?", (dt.datetime.now().isoformat(), user_id, target))
    conn.commit()
    conn.close()

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
        verdict = "🟢 Low risk" if lang == "en" else "🟢 Hatari ndogo"
        summary = "No reports found. Be careful anyway." if lang == "en" else "Hakuna ripoti zilizopatikana. Kuwa mwangalifu."
        log_check(user_id, target, target_type, "green")
        return verdict, summary, 0
    
    risk_score = 0
    has_verified = False
    for risk_level, comment, source, created_at in signals:
        if risk_level == "high": risk_score += 3
        elif risk_level == "medium": risk_score += 2
        else: risk_score += 1
        if source and "eConfirm" in source: has_verified = True
    
    if (has_verified and risk_score >= 3) or risk_score >= 5:
        verdict = "🔴 High risk" if lang == "en" else "🔴 Hatari kubwa"
    elif risk_score >= 2:
        verdict = "🟡 Medium risk" if lang == "en" else "🟡 Hatari ya kati"
    else:
        verdict = "🟢 Low risk" if lang == "en" else "🟢 Hatari ndogo"
    
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
        "welcome": "🛡️ *KaaRada Lite*\n\nBefore you send money — check first.\n\nWhat do you want to do?",
        "choose_language": "🌐 Choose your language:",
        "lang_set": "✅ Language set to English.",
        "menu_buttons": {
            "check": "🔍 Check a number",
            "watch": "👁 Add to watchlist",
            "mywatch": "📋 My watchlist",
            "help": "❓ Help"
        },
        "menu_back": "🏠 Main menu",
        "ask_target": "Send me the number, Till, or business name:",
        "ask_watch": "Send me the number you want to watch:",
        "checking": "🔍 Checking...",
        "verdict": "📊 *Verdict:*",
        "summary": "📋 *Details:*",
        "no_reports": "No reports found.",
        "help": "🛡️ *How to use KaaRada Lite*\n\n1. Send a phone number, Till, or business name.\n2. Get a verdict: 🟢 / 🟡 / 🔴.\n3. Add numbers to your watchlist — we'll notify you if new reports appear.\n\nCommands:\n/watch <number>\n/mywatch\n/unwatch <number>",
        "stats": "📊 *Stats*\n\n👥 Users: {users}\n🔍 Checks: {checks}\n⚠️ Signals: {signals}",
        "report_saved": "✅ Thank you! Your report is saved.",
        "report_prompt": "Send a report in this format:\n\n<number> | <comment>",
        "add_signal_usage": "Usage: /addsignal <target> <type> <risk> <comment>",
        "add_signal_success": "✅ Signal added for {target}.",
        "watch_added": "👁 Added to watchlist: {target}",
        "watch_exists": "👁 Already in your watchlist: {target}",
        "watch_removed": "✅ Removed from watchlist: {target}",
        "watch_empty": "👁 Your watchlist is empty.",
        "watch_list": "👁 *Your watchlist:*\n\n{items}",
        "watch_usage": "Usage: /watch <number>",
        "unwatch_usage": "Usage: /unwatch <number>",
        "watch_notify": "🔔 *New report:*\n\n{target}\n\n{details}",
        "done": "✅ Done. Send /start to see the menu."
    },
    "sw": {
        "welcome": "🛡️ *KaaRada Lite*\n\nKabla ya kutuma pesa — angalia kwanza.\n\nUnataka kufanya nini?",
        "choose_language": "🌐 Chagua lugha yako:",
        "lang_set": "✅ Lugha imewekwa Kiswahili.",
        "menu_buttons": {
            "check": "🔍 Angalia namba",
            "watch": "👁 Ongeza kwenye orodha",
            "mywatch": "📋 Orodha yangu",
            "help": "❓ Msaada"
        },
        "menu_back": "🏠 Menyu kuu",
        "ask_target": "Nitume namba, Till, au jina la biashara:",
        "ask_watch": "Nitume namba unayotaka kufuatilia:",
        "checking": "🔍 Inakagua...",
        "verdict": "📊 *Uamuzi:*",
        "summary": "📋 *Maelezo:*",
        "no_reports": "Hakuna ripoti zilizopatikana.",
        "help": "🛡️ *Jinsi ya kutumia KaaRada Lite*\n\n1. Tuma namba, Till, au jina la biashara.\n2. Pata uamuzi: 🟢 / 🟡 / 🔴.\n3. Ongeza namba kwenye orodha yako — tutakujulisha ikiwa ripoti mpya zitaonekana.\n\nAmri:\n/watch <namba>\n/mywatch\n/unwatch <namba>",
        "stats": "📊 *Takwimu*\n\n👥 Watumiaji: {users}\n🔍 Ukaguzi: {checks}\n⚠️ Ishara: {signals}",
        "report_saved": "✅ Asante! Ripoti yako imehifadhiwa.",
        "report_prompt": "Tuma ripoti kwa muundo huu:\n\n<namba> | <maoni>",
        "add_signal_usage": "Matumizi: /addsignal <lengo> <aina> <hatari> <maoni>",
        "add_signal_success": "✅ Ishara imeongezwa kwa {target}.",
        "watch_added": "👁 Imeongezwa kwenye orodha: {target}",
        "watch_exists": "👁 Tayari iko kwenye orodha: {target}",
        "watch_removed": "✅ Imeondolewa kwenye orodha: {target}",
        "watch_empty": "👁 Orodha yako ni tupu.",
        "watch_list": "👁 *Orodha yako:*\n\n{items}",
        "watch_usage": "Matumizi: /watch <namba>",
        "unwatch_usage": "Matumizi: /unwatch <namba>",
        "watch_notify": "🔔 *Ripoti mpya:*\n\n{target}\n\n{details}",
        "done": "✅ Tayari. Tuma /start kuona menyu."
    }
}

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton("🇰🇪 Kiswahili", callback_data="lang_sw")]
    ])

def get_menu_keyboard(lang):
    b = T[lang]['menu_buttons']
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(b['check'], callback_data="menu_check")],
        [InlineKeyboardButton(b['watch'], callback_data="menu_watch")],
        [InlineKeyboardButton(b['mywatch'], callback_data="menu_mywatch")],
        [InlineKeyboardButton(b['help'], callback_data="menu_help")]
    ])

def get_back_keyboard(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T[lang]['menu_back'], callback_data="menu_back")]
    ])

def get_verdict_keyboard(lang, target):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Report this", callback_data=f"report_{target}")],
        [InlineKeyboardButton(T[lang]['menu_back'], callback_data="menu_back")]
    ])

# ========== ОБРАБОТЧИКИ ==========
async def start_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    set_state(user_id, None)
    await update.message.reply_text(T[lang]['choose_language'], reply_markup=get_language_keyboard())

async def help_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    await update.message.reply_text(T[lang]['help'], parse_mode="Markdown", reply_markup=get_back_keyboard(lang))

async def stats_command(update, context):
    users, checks, signals = get_stats()
    await update.message.reply_text(T["en"]['stats'].format(users=users, checks=checks, signals=signals), parse_mode="Markdown")

async def addsignal_command(update, context):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    args = context.args
    if len(args) < 4:
        await update.message.reply_text(T["en"]['add_signal_usage'])
        return
    add_signal(args[0], args[1], args[2], " ".join(args[3:]), "admin", user_id)
    await update.message.reply_text(T["en"]['add_signal_success'].format(target=args[0]))

async def watch_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    if not context.args:
        await update.message.reply_text(T[lang]['watch_usage'], reply_markup=get_back_keyboard(lang))
        return
    target = context.args[0].strip()
    if add_to_watchlist(user_id, target):
        await update.message.reply_text(T[lang]['watch_added'].format(target=target), reply_markup=get_back_keyboard(lang))
    else:
        await update.message.reply_text(T[lang]['watch_exists'].format(target=target), reply_markup=get_back_keyboard(lang))

async def mywatch_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    items = get_watchlist(user_id)
    if not items:
        await update.message.reply_text(T[lang]['watch_empty'], reply_markup=get_back_keyboard(lang))
        return
    text_items = "\n".join(f"• `{item}`" for item in items)
    await update.message.reply_text(T[lang]['watch_list'].format(items=text_items), parse_mode="Markdown", reply_markup=get_back_keyboard(lang))

async def unwatch_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    if not context.args:
        await update.message.reply_text(T[lang]['unwatch_usage'], reply_markup=get_back_keyboard(lang))
        return
    target = context.args[0].strip()
    remove_from_watchlist(user_id, target)
    await update.message.reply_text(T[lang]['watch_removed'].format(target=target), reply_markup=get_back_keyboard(lang))

async def handle_message(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    lang = get_lang(user_id)
    state = get_state(user_id)
    text = update.message.text.strip()
    
    if "|" in text and len(text.split("|")) == 2:
        parts = text.split("|")
        add_signal(parts[0].strip(), detect_target_type(parts[0].strip()), "medium", parts[1].strip(), "user", user_id)
        await update.message.reply_text(T[lang]['report_saved'], reply_markup=get_back_keyboard(lang))
        return
    
    if state == "waiting_for_check":
        target_type = detect_target_type(text)
        await update.message.reply_text(T[lang]['checking'])
        verdict, summary, count = check_target(text, target_type, user_id, lang)
        response = f"{T[lang]['verdict']} {verdict}\n\n"
        if count > 0:
            response += f"{T[lang]['summary']}\n{summary}"
        else:
            response += T[lang]['no_reports']
        await update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_verdict_keyboard(lang, text))
        set_state(user_id, None)
        return
    
    if state == "waiting_for_watch":
        if add_to_watchlist(user_id, text):
            await update.message.reply_text(T[lang]['watch_added'].format(target=text), reply_markup=get_back_keyboard(lang))
        else:
            await update.message.reply_text(T[lang]['watch_exists'].format(target=text), reply_markup=get_back_keyboard(lang))
        set_state(user_id, None)
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
        await query.edit_message_text(T[new_lang]['welcome'], parse_mode="Markdown", reply_markup=get_menu_keyboard(new_lang))
        return
    
    if data == "menu_back":
        set_state(user_id, None)
        await query.edit_message_text(T[lang]['welcome'], parse_mode="Markdown", reply_markup=get_menu_keyboard(lang))
        return
    
    if data == "menu_check":
        set_state(user_id, "waiting_for_check")
        await query.edit_message_text(T[lang]['ask_target'], reply_markup=get_back_keyboard(lang))
        return
    
    if data == "menu_watch":
        set_state(user_id, "waiting_for_watch")
        await query.edit_message_text(T[lang]['ask_watch'], reply_markup=get_back_keyboard(lang))
        return
    
    if data == "menu_mywatch":
        items = get_watchlist(user_id)
        if not items:
            await query.edit_message_text(T[lang]['watch_empty'], reply_markup=get_back_keyboard(lang))
        else:
            text_items = "\n".join(f"• `{item}`" for item in items)
            await query.edit_message_text(T[lang]['watch_list'].format(items=text_items), parse_mode="Markdown", reply_markup=get_back_keyboard(lang))
        return
    
    if data == "menu_help":
        await query.edit_message_text(T[lang]['help'], parse_mode="Markdown", reply_markup=get_back_keyboard(lang))
        return
    
    if data.startswith("report_"):
        target = data.split("_", 1)[1]
        context.user_data['report_target'] = target
        await query.message.reply_text(T[lang]['report_prompt'], reply_markup=get_back_keyboard(lang))
        return

async def notify_watchers(context):
    conn = sqlite3.connect(db_path())
    since = (dt.datetime.now() - dt.timedelta(hours=24)).isoformat()
    rows = conn.execute("SELECT target, risk_level, comment, source FROM signals WHERE created_at >= ?", (since,)).fetchall()
    conn.close()
    for target, risk_level, comment, source in rows:
        for uid in get_all_watchers(target):
            lang = get_lang(uid)
            details = f"[{risk_level.upper()}] {comment}\nSource: {source}"
            try:
                await context.bot.send_message(chat_id=uid, text=T[lang]['watch_notify'].format(target=target, details=details), parse_mode="Markdown")
                update_notified(uid, target)
            except Exception as e:
                print(f"⚠️ Не удалось уведомить {uid}: {e}")

if __name__ == "__main__":
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("addsignal", addsignal_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("mywatch", mywatch_command))
    app.add_handler(CommandHandler("unwatch", unwatch_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.job_queue.run_repeating(notify_watchers, interval=3600, first=60)
    print("🛡️ KaaRada Lite started with menu and back button everywhere.")
    app.run_polling()
