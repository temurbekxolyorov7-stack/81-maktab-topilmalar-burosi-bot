import os
import logging
import random
import sqlite3
from datetime import datetime
from threading import Thread
from flask import Flask

# 1. FLASK WEB-SERVER (Render 24/7 ishlashi uchun)
app = Flask('')

@app.route('/')
def home():
    return "Bot 24/7 ishlamoqda!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# 2. SOZLAMALAR VA TOKEN
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DB_NAME = "maktab_topilmalar.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ConversationHandler bosqichlari (Buyum qo'shish)
PHOTO, TITLE, DESCRIPTION, LOCATION = range(4)


# 3. MA'LUMOTLAR BAZASI (SQLITE3)
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finder_user_id INTEGER,
            finder_username TEXT,
            finder_full_name TEXT,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT NOT NULL,
            photo_file_id TEXT,
            status TEXT DEFAULT 'Yangi topildi',
            claimant_user_id INTEGER,
            claimant_username TEXT,
            claimant_full_name TEXT,
            pin_code TEXT,
            created_at TEXT,
            claimed_at TEXT,
            returned_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users_rating (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            score INTEGER DEFAULT 0,
            items_count INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)

    conn.commit()
    conn.close()
    logger.info("SQLite3 bazasi va jadvallari tayyor.")


def add_user_score(user_id: int, username: str, full_name: str, points: int = 10):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO users_rating (user_id, username, full_name, score, items_count, updated_at)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            score = score + excluded.score,
            items_count = items_count + 1,
            username = excluded.username,
            full_name = excluded.full_name,
            updated_at = excluded.updated_at
    """, (user_id, username or "", full_name or "O'quvchi", points, now_str))

    conn.commit()
    conn.close()


def get_top_ratings(limit: int = 5):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, full_name, score, items_count
        FROM users_rating
        WHERE score > 0
        ORDER BY score DESC, items_count DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_expired_items(days: int = 15):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, location, description, created_at,
               CAST((julianday('now') - julianday(created_at)) AS INTEGER) as days_passed
        FROM items
        WHERE status = 'Yangi topildi'
          AND julianday('now') - julianday(created_at) >= ?
        ORDER BY created_at ASC
    """, (days,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def add_item_to_db(finder_id: int, username: str, full_name: str,
                   title: str, description: str, location: str, photo_file_id: str) -> int:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO items (
            finder_user_id, finder_username, finder_full_name,
            title, description, location, photo_file_id,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Yangi topildi', ?)
    """, (finder_id, username, full_name, title, description, location, photo_file_id, now_str))

    item_id = cursor.lastrowid
    conn.commit()
    conn.close()

    try:
        add_user_score(finder_id, username, full_name, points=10)
    except Exception as e:
        logger.error(f"Ball berishda xatolik: {e}")

    return item_id


def get_active_items():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM items 
        WHERE status != 'Egasiga qaytarildi'
        ORDER BY id DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_item_by_id(item_id: int):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def claim_item_db(item_id: int, user_id: int, username: str, full_name: str, pin_code: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        UPDATE items 
        SET status = 'Egasi qidirilmoqda',
            claimant_user_id = ?,
            claimant_username = ?,
            claimant_full_name = ?,
            pin_code = ?,
            claimed_at = ?
        WHERE id = ? AND status != 'Egasiga qaytarildi'
    """, (user_id, username, full_name, pin_code, now_str, item_id))

    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success


def verify_pin_db(pin_code: str):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM items 
        WHERE pin_code = ? AND status = 'Egasi qidirilmoqda'
    """, (pin_code,))
    item = cursor.fetchone()

    if item:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE items 
            SET status = 'Egasiga qaytarildi',
                returned_at = ?
            WHERE id = ?
        """, (now_str, item["id"]))
        conn.commit()
    conn.close()
    return item


def get_statistics():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM items")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM items WHERE status = 'Yangi topildi'")
    new_found = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM items WHERE status = 'Egasi qidirilmoqda'")
    pending = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM items WHERE status = 'Egasiga qaytarildi'")
    returned = cursor.fetchone()[0]

    conn.close()
    return {
        "total": total,
        "new_found": new_found,
        "pending": pending,
        "returned": returned,
    }


# 4. ASOSIY MENYU TUGMALARI
def main_menu_keyboard():
    keyboard = [
        [KeyboardButton("📦 Buyum topib oldim")],
        [KeyboardButton("🔍 Yo'qotib qo'ydim (Qidirish)")],
        [KeyboardButton("📊 Statistika"), KeyboardButton("🏆 Halol O'quvchilar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# 5. BUYRUQLAR (COMMANDS)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "Salom! Samarqand shahar 81-maktabning Topilmalar Burosi botiga xush kelibsiz! 👋\n\n"
        "Quyidagi tugmalardan birini tanlang:"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_statistics()
    rate = round((stats["returned"] / stats["total"]) * 100, 1) if stats["total"] > 0 else 0

    text = (
        "📊 <b>Samarqand shahar 81-maktab Topilmalar Statistikasi:</b>\n"
        "--------------------------------------\n"
        f"📦 <b>Jami topilgan buyumlar:</b> {stats['total']} ta\n"
        f"🟢 <b>Yangi topildi (kutilmoqda):</b> {stats['new_found']} ta\n"
        f"🟡 <b>Egasi qidirilmoqda (PIN berilgan):</b> {stats['pending']} ta\n"
        f"✅ <b>Egasiga qaytarildi:</b> {stats['returned']} ta\n"
        "--------------------------------------\n"
        f"🎯 <b>Muvaffaqiyatli topshirish:</b> {rate}%\n\n"
        "<i>Halol bo'lgan barcha o'quvchilarga tashakkur!</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def rating_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_users = get_top_ratings(limit=5)

    if not top_users:
        await update.message.reply_text(
            "🏆 <b>Hozircha reytingda ball to'plagan o'quvchilar yo'q.</b>\n\n"
            "Topilgan buyumlarni topshiring va birinchilardan bo'lib <b>+10 ball</b> oling!",
            parse_mode=ParseMode.HTML
        )
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = []

    for idx, user in enumerate(top_users):
        medal = medals[idx] if idx < len(medals) else f"{idx + 1}."
        name = user["full_name"] or "O'quvchi"
        username_str = f" (@{user['username']})" if user["username"] else ""
        score = user["score"]
        count = user["items_count"]
        lines.append(f"{medal} <b>{name}</b>{username_str}\n   └ ⭐️ <b>{score} ball</b> ({count} ta buyum)")

    result_text = (
        "🏆 <b>81-Maktab «Halol O'quvchi» Reytingi (Top-5):</b>\n"
        "--------------------------------------\n"
        + "\n\n".join(lines) +
        "\n--------------------------------------\n"
        "💡 <i>Har bir topib topshirilgan buyum uchun <b>+10 ball</b> beriladi!</i>"
    )

    await update.message.reply_text(result_text, parse_mode=ParseMode.HTML)


async def expired_items_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expired_list = get_expired_items(days=15)

    if not expired_list:
        await update.message.reply_text(
            "✅ <b>Barchasi joyida!</b>\n\n"
            "Hozirda 15 kundan ortiq 'Yangi topildi' holatida qolib ketgan muddati o'tgan buyumlar yo'q.",
            parse_mode=ParseMode.HTML
        )
        return

    items_text = []
    for item in expired_list:
        items_text.append(
            f"⚠️ <b>#{item['id']} - {item['title']}</b>\n"
            f"   📍 <b>Joyi:</b> {item['location']}\n"
            f"   📅 <b>Topilgan sana:</b> {item['created_at'][:10]} ({item['days_passed']} kun oldin)\n"
            f"   📝 <b>Tavsif:</b> {item['description'][:50]}..."
        )

    warning_text = (
        f"⏳ <b>Navbatchi o'qituvchi diqqatiga! ({len(expired_list)} ta muddati o'tgan buyum)</b>\n"
        "--------------------------------------\n"
        "Quyidagi buyumlar 15 kundan ortiq vaqt davomida navbatchilik xonasida saqlanmoqda:\n\n"
        + "\n\n".join(items_text) +
        "\n--------------------------------------\n"
        "📌 <b>Maktab ma'muriyatiga maslahat:</b>\n"
        "1. Maktab e'lonlar taxtasida qayta e'lon bering.\n"
        "2. Egasi chiqmasa, kutubxona yoki ehtiyojmand o'quvchilarga topshirish tavsiya etiladi."
    )

    await update.message.reply_text(warning_text, parse_mode=ParseMode.HTML)


# 6. "BUYUM TOPIB OLDIM" JARAYONI
async def start_add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_keyboard = ReplyKeyboardMarkup([["❌ Bekor qilish"]], resize_keyboard=True)
    text = (
        "📸 <b>1-qadam: Buyum rasmini yuboring.</b>\n\n"
        "Topib olgan buyumingizning rasmini shu yerga yuboring.\n"
        "<i>(Bekor qilish uchun «❌ Bekor qilish» tugmasini bosing).</i>"
    )
    await update.message.reply_text(text, reply_markup=cancel_keyboard, parse_mode=ParseMode.HTML)
    return PHOTO


async def item_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = update.message.photo[-1]
    context.user_data["item_photo_id"] = photo_file.file_id

    text = (
        "✅ Rasm qabul qilindi!\n\n"
        "📝 <b>2-qadam: Buyum nomini kiriting.</b>\n"
        "Masalan: <i>Matematika darsligi, Ko'k ryukzak, Kalitlar...</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return TITLE


async def item_title_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if len(title) < 2:
        await update.message.reply_text("Iltimos, buyum nomini to'liqroq yozing:")
        return TITLE

    context.user_data["item_title"] = title
    text = (
        f"📌 Buyum nomi: <b>{title}</b>\n\n"
        "✍️ <b>3-qadam: Buyumning qisqacha tavsifini yozing.</b>\n"
        "Belgilari, rangi yoki o'ziga xos tomonlari:"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return DESCRIPTION


async def item_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    context.user_data["item_description"] = description

    text = (
        "📍 <b>4-qadam: Buyum aynan qayerdan topildi?</b>\n"
        "Masalan: <i>2-qavat 204-xona oldi, Oshxona, Maktab sportzali...</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return LOCATION


async def item_location_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = update.message.text.strip()
    user = update.effective_user

    photo_id = context.user_data.get("item_photo_id")
    title = context.user_data.get("item_title")
    description = context.user_data.get("item_description")

    item_id = add_item_to_db(
        finder_id=user.id,
        username=user.username or "",
        full_name=user.full_name or "O'quvchi",
        title=title,
        description=description,
        location=location,
        photo_file_id=photo_id
    )

    success_text = (
        "🎉 <b>Buyum muvaffaqiyatli ro'yxatga olindi!</b>\n"
        "--------------------------------------\n"
        f"🆔 <b>Qayd raqami:</b> #{item_id}\n"
        f"📦 <b>Buyum:</b> {title}\n"
        f"📝 <b>Tavsif:</b> {description}\n"
        f"📍 <b>Topilgan joy:</b> {location}\n"
        "📌 <b>Holati:</b> 🟢 Yangi topildi\n"
        "⭐️ <b>Rag'bat:</b> Sizga <b>+10 ball</b> berildi!\n"
        "--------------------------------------\n"
        "⚠️ <b>Eslatma:</b> Iltimos, buyumni 81-maktab <b>Navbatchi o'qituvchisiga</b> topshirib qo'ying!\n\n"
        "Katta rahmat! 👏"
    )

    await update.message.reply_text(
        success_text,
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel_add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Jarayon bekor qilindi.",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


# 7. "YO'QOTIB QO'YDIM" (QIDIRISH) VA PIN GENERATSIYASI
async def search_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = get_active_items()

    if not items:
        await update.message.reply_text(
            "📭 Hozircha bazada faol topilmalar mavjud emas.\n"
            "Biror buyum topilsa, darhol bu yerga qo'shiladi.",
            reply_markup=main_menu_keyboard()
        )
        return

    text = (
        f"🔍 <b>81-Maktab Topilmalar Burosi - Faol topilmalar ({len(items)} ta):</b>\n\n"
        "O'zingiz yo'qotgan buyumni ko'rish uchun quyidagi tugmalardan birini bosing:"
    )

    keyboard = []
    for item in items:
        status_icon = "🟢" if item["status"] == "Yangi topildi" else "🟡"
        btn_text = f"{status_icon} #{item['id']} {item['title'][:20]} ({item['location'][:15]})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_{item['id']}")])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def item_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("view_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)

        if not item:
            await query.edit_message_text("❌ Buyum topilmadi.")
            return

        status_text = (
            "🟢 Yangi topildi (Navbatchi xonasida)"
            if item["status"] == "Yangi topildi"
            else "🟡 Egasi qidirilmoqda (PIN berilgan)"
        )

        caption = (
            f"📦 <b>Buyum:</b> {item['title']}\n"
            f"🆔 <b>Qayd raqami:</b> #{item['id']}\n"
            f"📝 <b>Tavsifi:</b> {item['description']}\n"
            f"📍 <b>Topilgan joy:</b> {item['location']}\n"
            f"📅 <b>Sana:</b> {item['created_at']}\n"
            f"📌 <b>Holati:</b> {status_text}\n\n"
            "Agar bu sizning buyumingiz bo'lsa, quyidagi «✋ Bu meniki» tugmasini bosing:"
        )

        buttons = []
        if item["status"] != "Egasiga qaytarildi":
            buttons.append([InlineKeyboardButton("✋ Bu meniki (PIN olish)", callback_data=f"claim_{item['id']}")])
        buttons.append([InlineKeyboardButton("🔙 Ro'yxatga qaytish", callback_data="back_to_list")])

        if item["photo_file_id"]:
            try:
                await query.message.reply_photo(
                    photo=item["photo_file_id"],
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            except Exception as e:
                logger.error(f"Rasm yuklashda xatolik: {e}")
                await query.message.reply_text(
                    caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
        else:
            await query.message.reply_text(
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )

    elif data.startswith("claim_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)

        if not item:
            await query.answer("Buyum topilmadi!", show_alert=True)
            return

        pin_code = str(random.randint(1000, 9999))
        user = query.from_user

        success = claim_item_db(
            item_id=item_id,
            user_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "O'quvchi",
            pin_code=pin_code
        )

        if success:
            claim_text = (
                "🎯 <b>So'rovingiz qabul qilindi!</b>\n"
                "--------------------------------------\n"
                f"📦 <b>Buyum:</b> {item['title']}\n"
                f"🆔 <b>Qayd raqami:</b> #{item['id']}\n\n"
                f"🔐 <b>SIZNING MAXFIY PIN-KODINGIZ:</b>\n"
                f"👉 <code>{pin_code}</code> 👈\n\n"
                "📌 <b>KEYINGI QADAM:</b>\n"
                "1. Samarqand shahar 81-maktab <b>Navbatchi o'qituvchisi</b> huzuriga boring.\n"
                f"2. Ushbu <b>4 xonali PIN-kodni ({pin_code})</b> ayting.\n"
                "3. O'qituvchi kodni tasdiqlagach, buyum sizga xavfsiz topshiriladi.\n"
                "--------------------------------------\n"
                "⚠️ <i>Eslatma: Ushbu PIN-kodni faqat buyumni topshirib olayotganda o'qituvchiga ayting!</i>"
            )
            await query.message.reply_text(claim_text, parse_mode=ParseMode.HTML)
            await query.answer("PIN-kod yaratildi!", show_alert=True)
        else:
            await query.answer("Xatolik yuz berdi.", show_alert=True)

    elif data == "back_to_list":
        items = get_active_items()
        keyboard = []
        for item in items:
            status_icon = "🟢" if item["status"] == "Yangi topildi" else "🟡"
            btn_text = f"{status_icon} #{item['id']} {item['title'][:20]} ({item['location'][:15]})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_{item['id']}")])

        await query.message.reply_text(
            f"🔍 <b>81-Maktab Topilmalar Burosi - Faol buyumlar ({len(items)} ta):</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )


# 8. XAVFSIZ QAYTARISH: PIN KODNI TASDIQLASH (/qaytarish <PIN>)
async def verify_pin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PIN tekshirib buyumni topshirish (Hamma foydalanuvchilar uchun ochiq)"""
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>PIN-kod kiritilmadi!</b>\n\n"
            "Foydalanish: <code>/qaytarish PIN_KOD</code>\n"
            "Masalan: <code>/qaytarish 4821</code>",
            parse_mode=ParseMode.HTML
        )
        return

    pin_input = context.args[0].strip()
    item = verify_pin_db(pin_input)

    if item:
        text = (
            "✅ <b>PIN-kod to'g'ri! Buyum egasiga topshirildi.</b>\n"
            "--------------------------------------\n"
            f"📦 <b>Buyum:</b> {item['title']}\n"
            f"🆔 <b>Qayd raqami:</b> #{item['id']}\n"
            f"👤 <b>Qabul qiluvchi:</b> {item['claimant_full_name']} (@{item['claimant_username']})\n"
            f"🔐 <b>Tekshirilgan PIN:</b> {pin_input}\n"
            f"📌 <b>Yangi holat:</b> ✅ Egasiga qaytarildi\n"
            "--------------------------------------\n"
            "Tizimda buyum muvaffaqiyatli yopildi!"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            f"❌ <b>PIN-kod ({pin_input}) topilmadi yoki buyum allaqachon topshirilgan!</b>",
            parse_mode=ParseMode.HTML
        )


# 9. MENYU TUGMALARI ROUTERI VA DASTURNI ISHGA TUSHIRISH
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🔍 Yo'qotib qo'ydim (Qidirish)":
        await search_items(update, context)
    elif text == "📊 Statistika":
        await stats_command(update, context)
    elif text == "🏆 Halol O'quvchilar":
        await rating_command(update, context)


def main():
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📦 Buyum topib oldim$"), start_add_item),
            CommandHandler("add", start_add_item),
        ],
        states={
            PHOTO: [
                MessageHandler(filters.PHOTO, item_photo_received),
                MessageHandler(filters.Regex("^❌ Bekor qilish$"), cancel_add_item),
            ],
            TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, item_title_received),
                MessageHandler(filters.Regex("^❌ Bekor qilish$"), cancel_add_item),
            ],
            DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, item_description_received),
                MessageHandler(filters.Regex("^❌ Bekor qilish$"), cancel_add_item),
            ],
            LOCATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, item_location_received),
                MessageHandler(filters.Regex("^❌ Bekor qilish$"), cancel_add_item),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_add_item),
            MessageHandler(filters.Regex("^❌ Bekor qilish$"), cancel_add_item),
        ],
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("rating", rating_command))
    application.add_handler(CommandHandler("reyting", rating_command))
    application.add_handler(CommandHandler("expired", expired_items_command))
    application.add_handler(CommandHandler("muddati_otganlar", expired_items_command))
    application.add_handler(CommandHandler("qaytarish", verify_pin_command))
    application.add_handler(CommandHandler("verify", verify_pin_command))

    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(item_callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("==================================================================")
    print("🤖 Samarqand shahar 81-maktab Topilmalar Burosi boti ishga tushdi!")
    print("==================================================================")
    
    # Flask serverni ishga tushiramiz
    keep_alive()
    
    # Telegram botni polling rejimida ishga tushiramiz
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()