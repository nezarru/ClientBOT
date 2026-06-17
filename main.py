import os
import sqlite3
import asyncio
import random
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import User

# Настройка логов — пишем и в консоль и в файл
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("userbot")
logging.getLogger("telethon").setLevel(logging.ERROR)

async def reply(client, chat_id, text):
    for attempt in range(3):
        try:
            await client.send_message(chat_id, text)
            return
        except Exception as e:
            logger.info(f"[reply] попытка {attempt+1} ошибка: {e}")
            await asyncio.sleep(1)

load_dotenv()

# ================== АККАУНТЫ ==================
# .env структура:
# PHONE_1=+79991234567
# API_ID_1=12345678
# API_HASH_1=abcdef...
# (и так до PHONE_3, API_ID_3, API_HASH_3)

def load_accounts():
    accounts = []
    for i in range(1, 4):
        api_id   = os.getenv(f"API_ID_{i}")
        api_hash = os.getenv(f"API_HASH_{i}")
        phone    = os.getenv(f"PHONE_{i}")
        if api_id and api_hash:
            accounts.append({
                "api_id":   int(api_id),
                "api_hash": api_hash,
                "phone":    phone or "",
                "session":  f"session_{i}",
                "index":    i,
            })
    if not accounts:
        raise RuntimeError("Нет аккаунтов в .env! Укажите PHONE_1, API_ID_1, API_HASH_1")
    return accounts

# ================== БАЗА ==================
conn   = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS mytext (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT NOT NULL
)""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS mychats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    chat_id INTEGER,
    chat_name TEXT
)""")
conn.commit()

# ================== СОСТОЯНИЯ ==================
user_states  = {}  # user_id -> {"state": ..., ...}
active_tasks = {}  # user_id -> asyncio.Task
bot_enabled  = {}  # user_id -> bool

def is_bot_enabled(uid):
    return bot_enabled.get(uid, True)

# ================== МЕНЮ (текст) ==================
MENU = """
╔══════════════════════╗
║   🤖 USERBOT МЕНЮ   ║
╚══════════════════════╝

📋 ТЕКСТЫ
  /mytexts   — список текстов
  /addtext   — добавить текст
  /deltext   — удалить текст

💬 ЧАТЫ
  /mychats   — список чатов
  /addchat   — добавить чат
  /delchat   — удалить чат

🚀 РАССЫЛКА
  /send      — выбрать чаты и отправить
  /sendall   — отправить во ВСЕ чаты сразу
  /stop      — остановить рассылку
  /status    — статус

⚙️ БОТ
  /on        — включить бот
  /off       — выключить бот
  /menu      — показать меню
"""

def status_text(uid):
    enabled = is_bot_enabled(uid)
    mailing = uid in active_tasks
    return (
        f"📊 Статус\n\n"
        f"{'🟢' if enabled else '🔴'} Бот: {'Включён' if enabled else 'Выключен'}\n"
        f"{'✅' if mailing else '❌'} Рассылка: {'Работает' if mailing else 'Не активна'}"
    )

# ================== РАССЫЛКА ==================
# Чаты с платной подпиской — пропускаем навсегда
payment_blocked = set()

async def notify(client, chat, msg):
    print(msg)
    try:
        await client.send_message(chat, msg)
    except Exception:
        pass

async def mailing_task(client, user_id, chats, text, interval, target_chat):
    try:
        while True:
            ok = fail = skipped = 0
            for _, name, chat_id in chats:
                if chat_id in payment_blocked:
                    skipped += 1
                    continue
                try:
                    await client.send_message(chat_id, text)
                    ok += 1
                except FloodWaitError as e:
                    wait = e.seconds
                    await notify(client, target_chat,
                        f"⏳ Флуд-ограничение в {name}\nЖду {wait} сек. и повторю..."
                    )
                    await asyncio.sleep(wait + 5)
                    try:
                        await client.send_message(chat_id, text)
                        ok += 1
                        await notify(client, target_chat, f"✅ {name} — отправлено после ожидания")
                    except Exception as e2:
                        await notify(client, target_chat, f"❌ {name} — повторная попытка не удалась: {e2}")
                        fail += 1
                except Exception as e:
                    err_str = str(e)
                    if "ALLOW_PAYMENT_REQUIRED" in err_str:
                        payment_blocked.add(chat_id)
                        await notify(client, target_chat,
                            f"🚫 {name} — требует платную подписку\nЧат исключён. Удалите через /delchat"
                        )
                        skipped += 1
                    elif "CHAT_WRITE_FORBIDDEN" in err_str:
                        await notify(client, target_chat, f"🔒 {name} — нет прав на отправку")
                        fail += 1
                    elif "USER_BANNED_IN_CHANNEL" in err_str:
                        await notify(client, target_chat, f"🚫 {name} — вы заблокированы в этом чате")
                        fail += 1
                    elif "CHAT_RESTRICTED" in err_str:
                        await notify(client, target_chat, f"⛔ {name} — чат ограничен")
                        fail += 1
                    else:
                        await notify(client, target_chat, f"❌ {name} — ошибка: {e}")
                        fail += 1
                await asyncio.sleep(random.uniform(5, 12))

            skip_note = f"\n⏭ Пропущено (платные): {skipped}" if skipped else ""
            await client.send_message(target_chat,
                f"📢 Цикл завершён\n✅ Отправлено: {ok}\n❌ Ошибок: {fail}{skip_note}\n"
                f"⏱ Следующий через {interval} мин.\n\nОстановить: /stop"
            )
            await asyncio.sleep(interval * 60)
    except asyncio.CancelledError:
        await notify(client, target_chat, "⛔ Рассылка остановлена.")
    except Exception as e:
        await notify(client, target_chat, f"❌ Критическая ошибка: {e}")
    finally:
        active_tasks.pop(user_id, None)

# ================== ХЕНДЛЕРЫ ==================
def register_handlers(client):

    @client.on(events.NewMessage(pattern=r'^/(start|menu)$', outgoing=True))
    async def cmd_menu(event):
        if not event.is_private:
            return
        uid = event.sender_id
        me = await client.get_me()
        logger.info(f"[{me.username}] /start от {uid} в чате {event.chat_id}")
        await reply(client, event.chat_id, MENU + f"\n{status_text(uid)}")

    @client.on(events.NewMessage(pattern=r'^/status$', outgoing=True))
    async def cmd_status(event):
        if not event.is_private:
            return
        await reply(client, event.chat_id, status_text(event.sender_id))

    @client.on(events.NewMessage(pattern=r'^/on$', outgoing=True))
    async def cmd_on(event):
        if not event.is_private:
            return
        me = await client.get_me()
        logger.info(f"[{me.username}] /on от {event.sender_id}")
        bot_enabled[event.sender_id] = True
        await reply(client, event.chat_id, "🟢 Бот включён!\n\n/menu — открыть меню")

    @client.on(events.NewMessage(pattern=r'^/off$', outgoing=True))
    async def cmd_off(event):
        if not event.is_private:
            return
        uid = event.sender_id
        me = await client.get_me()
        logger.info(f"[{me.username}] /off от {uid}")
        bot_enabled[uid] = False
        if uid in active_tasks:
            active_tasks[uid].cancel()
        await reply(client, event.chat_id, "🔴 Бот выключен. Рассылка остановлена.\n\n/on — включить")

    # ---- ТЕКСТЫ ----
    @client.on(events.NewMessage(pattern=r'^/mytexts$', outgoing=True))
    async def cmd_mytexts(event):
        if not event.is_private:
            return
        uid = event.sender_id
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен. /on — включить")
            return
        cursor.execute("SELECT id, text FROM mytext WHERE user_id=?", (uid,))
        rows = cursor.fetchall()
        if not rows:
            await reply(client, event.chat_id, "📋 Текстов нет.\n\n/addtext — добавить")
            return
        txt = "📋 Ваши тексты:\n\n"
        for i, (tid, t) in enumerate(rows, 1):
            preview = t[:80] + "..." if len(t) > 80 else t
            txt += f"{i}. {preview}\n\n"
        await reply(client, event.chat_id, txt)

    @client.on(events.NewMessage(pattern=r'^/addtext$', outgoing=True))
    async def cmd_addtext(event):
        if not event.is_private:
            return
        uid = event.sender_id
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен. /on — включить")
            return
        user_states[uid] = {"state": "add_text"}
        await reply(client, event.chat_id, "✏️ Отправьте текст для рассылки:\n\n/cancel — отмена")

    @client.on(events.NewMessage(pattern=r'^/deltext$', outgoing=True))
    async def cmd_deltext(event):
        if not event.is_private:
            return
        uid = event.sender_id
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен.")
            return
        cursor.execute("SELECT id, text FROM mytext WHERE user_id=?", (uid,))
        rows = cursor.fetchall()
        if not rows:
            await reply(client, event.chat_id, "📋 Текстов нет.")
            return
        txt = "🗑 Какой текст удалить? Отправьте номер:\n\n"
        for i, (tid, t) in enumerate(rows, 1):
            preview = t[:60] + "..." if len(t) > 60 else t
            txt += f"{i}. {preview}\n\n"
        await reply(client, event.chat_id, txt + "/cancel — отмена")
        user_states[uid] = {"state": "del_text", "rows": rows}

    # ---- ЧАТЫ ----
    @client.on(events.NewMessage(pattern=r'^/mychats$', outgoing=True))
    async def cmd_mychats(event):
        if not event.is_private:
            return
        uid = event.sender_id
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен.")
            return
        cursor.execute("SELECT id, chat_name, chat_id FROM mychats WHERE user_id=?", (uid,))
        rows = cursor.fetchall()
        if not rows:
            await reply(client, event.chat_id, "💬 Чатов нет.\n\n/addchat — добавить")
            return
        txt = "💬 Ваши чаты:\n\n"
        for i, (cid, name, chat_id) in enumerate(rows, 1):
            txt += f"{i}. {name}\n"
        await reply(client, event.chat_id, txt)

    @client.on(events.NewMessage(pattern=r'^/addchat$', outgoing=True))
    async def cmd_addchat(event):
        if not event.is_private:
            return
        uid = event.sender_id
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен.")
            return
        await reply(client, event.chat_id, "⏳ Загружаю ваши чаты...")
        dialogs = await client.get_dialogs(limit=100)
        chats = [(d.id, d.name) for d in dialogs if d.is_group or d.is_channel]
        if not chats:
            await reply(client, event.chat_id, "❌ Нет доступных групп и каналов.")
            return
        txt = "➕ Выберите чаты для добавления:\n\n"
        for i, (cid, name) in enumerate(chats, 1):
            txt += f"{i}. {name}\n"
        txt += "\nОтправьте один номер или несколько через запятую (например: 1,3,5)\n\n/cancel — отмена"
        await reply(client, event.chat_id, txt)
        user_states[uid] = {"state": "add_chat", "chats": chats}

    @client.on(events.NewMessage(pattern=r'^/delchat$', outgoing=True))
    async def cmd_delchat(event):
        if not event.is_private:
            return
        uid = event.sender_id
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен.")
            return
        cursor.execute("SELECT id, chat_name, chat_id FROM mychats WHERE user_id=?", (uid,))
        rows = cursor.fetchall()
        if not rows:
            await reply(client, event.chat_id, "💬 Чатов нет.")
            return
        txt = "🗑 Какой чат удалить? Отправьте номер:\n\n"
        for i, (cid, name, chat_id) in enumerate(rows, 1):
            txt += f"{i}. {name}\n"
        await reply(client, event.chat_id, txt + "\n/cancel — отмена")
        user_states[uid] = {"state": "del_chat", "rows": rows}

    # ---- РАССЫЛКА ----
    @client.on(events.NewMessage(pattern=r'^/send$', outgoing=True))
    async def cmd_send(event):
        if not event.is_private:
            return
        uid = event.sender_id
        me = await client.get_me()
        logger.info(f"[{me.username}] /send от {uid}")
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен. /on — включить")
            return
        if uid in active_tasks:
            await reply(client, event.chat_id, "⚠️ Рассылка уже запущена.\n\n/stop — остановить")
            return
        cursor.execute("SELECT id, chat_name, chat_id FROM mychats WHERE user_id=?", (uid,))
        chats = cursor.fetchall()
        if not chats:
            await reply(client, event.chat_id, "❌ Нет чатов.\n\n/addchat — добавить")
            return
        cursor.execute("SELECT id, text FROM mytext WHERE user_id=?", (uid,))
        texts = cursor.fetchall()
        if not texts:
            await reply(client, event.chat_id, "❌ Нет текстов.\n\n/addtext — добавить")
            return
        await reply(client, event.chat_id, 
            "📢 Выберите тип рассылки:\n\n"
            "1. Одиночная — один чат\n"
            "2. Массовая — несколько чатов\n"
            "3. Все чаты — сразу все добавленные\n\n"
            "Отправьте номер (1, 2 или 3)\n\n/cancel — отмена"
        )
        user_states[uid] = {"state": "send_choose_type", "chats": chats, "texts": texts, "target": event.chat_id}

    @client.on(events.NewMessage(pattern=r'^/sendall$', outgoing=True))
    async def cmd_sendall(event):
        if not event.is_private:
            return
        uid = event.sender_id
        if not is_bot_enabled(uid):
            await reply(client, event.chat_id, "🔴 Бот выключен. /on — включить")
            return
        if uid in active_tasks:
            await reply(client, event.chat_id, "⚠️ Рассылка уже запущена.\n\n/stop — остановить")
            return
        cursor.execute("SELECT id, chat_name, chat_id FROM mychats WHERE user_id=?", (uid,))
        chats = cursor.fetchall()
        if not chats:
            await reply(client, event.chat_id, "❌ Нет чатов.\n\n/addchat — добавить")
            return
        cursor.execute("SELECT id, text FROM mytext WHERE user_id=?", (uid,))
        texts = cursor.fetchall()
        if not texts:
            await reply(client, event.chat_id, "❌ Нет текстов.\n\n/addtext — добавить")
            return
        txt = "📋 Выберите текст — отправьте номер:\n\n"
        for i, (tid, t) in enumerate(texts, 1):
            preview = t[:60] + "..." if len(t) > 60 else t
            txt += f"{i}. {preview}\n\n"
        await reply(client, event.chat_id, txt + "/cancel — отмена")
        # Сразу все чаты, пропускаем шаг выбора чатов
        user_states[uid] = {
            "state": "sendall_choose_text",
            "chats": chats,
            "texts": texts,
            "target": event.chat_id
        }

    @client.on(events.NewMessage(pattern=r'^/stop$', outgoing=True))
    async def cmd_stop(event):
        if not event.is_private:
            return
        uid = event.sender_id
        me = await client.get_me()
        logger.info(f"[{me.username}] /stop от {uid}")
        if uid in active_tasks:
            active_tasks[uid].cancel()
            await reply(client, event.chat_id, "⛔ Рассылка остановлена.")
        else:
            await reply(client, event.chat_id, "❌ Рассылка не была запущена.")

    @client.on(events.NewMessage(pattern=r'^/cancel$', outgoing=True))
    async def cmd_cancel(event):
        if not event.is_private:
            return
        uid = event.sender_id
        user_states.pop(uid, None)
        await reply(client, event.chat_id, "❌ Отменено.\n\n/menu — меню")

    # ================== ТЕКСТОВЫЙ ВВОД ==================
    @client.on(events.NewMessage(outgoing=True))
    async def text_handler(event):
        if not event.is_private:
            return
        if not event.text:
            return
        if event.text.startswith('/'):
            return

        uid   = event.sender_id
        text  = event.text.strip()
        state = user_states.get(uid, {}).get("state")

        if not state:
            return

        if not is_bot_enabled(uid):
            user_states.pop(uid, None)
            return

        data = user_states[uid]

        # ---- добавить текст ----
        if state == "add_text":
            cursor.execute("INSERT INTO mytext (user_id, text) VALUES (?,?)", (uid, text))
            conn.commit()
            user_states.pop(uid, None)
            await reply(client, event.chat_id, "✅ Текст добавлен!\n\n/mytexts — мои тексты\n/menu — меню")

        # ---- удалить текст ----
        elif state == "del_text":
            rows = data["rows"]
            try:
                n = int(text) - 1
                if n < 0 or n >= len(rows): raise ValueError
                cursor.execute("DELETE FROM mytext WHERE id=?", (rows[n][0],))
                conn.commit()
                user_states.pop(uid, None)
                await reply(client, event.chat_id, f"✅ Текст #{n+1} удалён!\n\n/menu — меню")
            except ValueError:
                await reply(client, event.chat_id, f"⚠️ Введите номер от 1 до {len(rows)}")

        # ---- добавить чат ----
        elif state == "add_chat":
            chats = data["chats"]
            try:
                nums = [int(x.strip()) - 1 for x in text.split(",")]
                for n in nums:
                    if n < 0 or n >= len(chats): raise ValueError
                added = []
                for n in nums:
                    chat_id, chat_name = chats[n]
                    # Проверяем что не добавлен уже
                    cursor.execute("SELECT id FROM mychats WHERE user_id=? AND chat_id=?", (uid, chat_id))
                    if cursor.fetchone():
                        continue
                    cursor.execute(
                        "INSERT INTO mychats (user_id, chat_id, chat_name) VALUES (?,?,?)",
                        (uid, chat_id, chat_name)
                    )
                    added.append(chat_name)
                conn.commit()
                user_states.pop(uid, None)
                if added:
                    chat_list = "\n".join(f"✅ {name}" for name in added)
                    await reply(client, event.chat_id, f"Добавлено {len(added)} чат(ов):\n\n{chat_list}\n\n/menu — меню")
                else:
                    await reply(client, event.chat_id, "⚠️ Все выбранные чаты уже добавлены.\n\n/menu — меню")
            except (ValueError, IndexError):
                await reply(client, event.chat_id, f"⚠️ Введите номера от 1 до {len(chats)} через запятую")

        # ---- удалить чат ----
        elif state == "del_chat":
            rows = data["rows"]
            try:
                n = int(text) - 1
                if n < 0 or n >= len(rows): raise ValueError
                cursor.execute("DELETE FROM mychats WHERE id=?", (rows[n][0],))
                conn.commit()
                user_states.pop(uid, None)
                await reply(client, event.chat_id, f"✅ Чат #{n+1} удалён!\n\n/menu — меню")
            except ValueError:
                await reply(client, event.chat_id, f"⚠️ Введите номер от 1 до {len(rows)}")

        # ---- рассылка: выбор типа ----
        elif state == "send_choose_type":
            chats = data["chats"]
            texts = data["texts"]
            if text == "1":
                # Одиночная
                chat_list = "\n".join(f"{i+1}. {name}" for i, (_, name, _) in enumerate(chats))
                await reply(client, event.chat_id, 
                    f"1️⃣ Одиночная рассылка\n\nВыберите один чат — отправьте номер:\n\n{chat_list}\n\n/cancel — отмена"
                )
                user_states[uid]["state"] = "send_single_choose_chat"
            elif text == "2":
                # Массовая
                chat_list = "\n".join(f"{i+1}. {name}" for i, (_, name, _) in enumerate(chats))
                await reply(client, event.chat_id, 
                    f"2️⃣ Массовая рассылка\n\nВыберите чаты — отправьте номера через запятую (например: 1,3,5):\n\n{chat_list}\n\n/cancel — отмена"
                )
                user_states[uid]["state"] = "send_multi_choose_chats"
            elif text == "3":
                # Все чаты
                txt = "📋 Выберите текст — отправьте номер:\n\n"
                for i, (tid, t) in enumerate(texts, 1):
                    preview = t[:60] + "..." if len(t) > 60 else t
                    txt += f"{i}. {preview}\n\n"
                await reply(client, event.chat_id, txt + "/cancel — отмена")
                user_states[uid]["state"] = "sendall_choose_text"
            else:
                await reply(client, event.chat_id, "⚠️ Введите 1, 2 или 3")

        # ---- одиночная: выбор чата ----
        elif state == "send_single_choose_chat":
            chats = data["chats"]
            texts = data["texts"]
            try:
                n = int(text) - 1
                if n < 0 or n >= len(chats): raise ValueError
                selected_chats = [chats[n]]
                txt = "📋 Выберите текст — отправьте номер:\n\n"
                for i, (tid, t) in enumerate(texts, 1):
                    preview = t[:60] + "..." if len(t) > 60 else t
                    txt += f"{i}. {preview}\n\n"
                await reply(client, event.chat_id, txt + "/cancel — отмена")
                user_states[uid]["state"] = "send_choose_interval"
                user_states[uid]["selected_chats"] = selected_chats
                user_states[uid]["state"] = "send_single_choose_text"
            except ValueError:
                await reply(client, event.chat_id, f"⚠️ Введите номер от 1 до {len(chats)}")

        # ---- одиночная: выбор текста ----
        elif state == "send_single_choose_text":
            texts = data["texts"]
            try:
                n = int(text) - 1
                if n < 0 or n >= len(texts): raise ValueError
                user_states[uid]["selected_text"] = texts[n][1]
                user_states[uid]["state"] = "send_choose_interval"
                chats = user_states[uid]["selected_chats"]
                chat_list = "\n".join(f"• {name}" for _, name, _ in chats)
                await reply(client, event.chat_id, 
                    f"1️⃣ Одиночная рассылка\n📍 Чат: {chat_list}\n\n"
                    f"⏱ Введите интервал в минутах (например: 180 = 3 часа):\n\n/cancel — отмена"
                )
            except ValueError:
                await reply(client, event.chat_id, f"⚠️ Введите номер от 1 до {len(texts)}")

        # ---- массовая: выбор чатов ----
        elif state == "send_multi_choose_chats":
            chats = data["chats"]
            texts = data["texts"]
            try:
                nums = [int(x.strip()) - 1 for x in text.split(",")]
                for n in nums:
                    if n < 0 or n >= len(chats): raise ValueError
                selected_chats = [chats[n] for n in nums]
                txt = "📋 Выберите текст — отправьте номер:\n\n"
                for i, (tid, t) in enumerate(texts, 1):
                    preview = t[:60] + "..." if len(t) > 60 else t
                    txt += f"{i}. {preview}\n\n"
                await reply(client, event.chat_id, txt + "/cancel — отмена")
                user_states[uid]["state"] = "send_single_choose_text"
                user_states[uid]["selected_chats"] = selected_chats
            except (ValueError, IndexError):
                await reply(client, event.chat_id, f"⚠️ Введите номера от 1 до {len(chats)} через запятую")

        # ---- рассылка: выбор текста ----
        elif state == "send_choose_text":
            texts = data["texts"]
            try:
                n = int(text) - 1
                if n < 0 or n >= len(texts): raise ValueError
                user_states[uid]["state"]         = "send_choose_chats"
                user_states[uid]["selected_text"] = texts[n][1]
                chats = data["chats"]
                chat_list = "\n".join(f"{i+1}. {name}" for i, (_, name, _) in enumerate(chats))
                await reply(client, event.chat_id, 
                    f"💬 В какие чаты отправить?\n\n"
                    f"{chat_list}\n\n"
                    f"Отправьте номера через запятую (например: 1,3,5)\n"
                    f"Или напишите 0 — чтобы выбрать ВСЕ чаты\n\n"
                    f"/cancel — отмена"
                )
            except ValueError:
                await reply(client, event.chat_id, f"⚠️ Введите номер от 1 до {len(texts)}")

        # ---- рассылка: выбор чатов ----
        elif state == "send_choose_chats":
            chats = data["chats"]
            try:
                if text.strip() == "0":
                    selected_chats = chats
                else:
                    nums = [int(x.strip()) - 1 for x in text.split(",")]
                    for n in nums:
                        if n < 0 or n >= len(chats): raise ValueError
                    selected_chats = [chats[n] for n in nums]

                user_states[uid]["state"]          = "send_choose_interval"
                user_states[uid]["selected_chats"] = selected_chats
                chat_list = "\n".join(f"• {name}" for _, name, _ in selected_chats)
                await reply(client, event.chat_id, 
                    f"📢 Будет отправлено в {len(selected_chats)} чат(ов):\n{chat_list}\n\n"
                    f"⏱ Введите интервал между циклами в минутах (например: 30):\n\n/cancel — отмена"
                )
            except (ValueError, IndexError):
                await reply(client, event.chat_id, f"⚠️ Введите номера от 1 до {len(chats)} через запятую, или 0 для всех")

        # ---- sendall: выбор текста (все чаты) ----
        elif state == "sendall_choose_text":
            texts = data["texts"]
            try:
                n = int(text) - 1
                if n < 0 or n >= len(texts): raise ValueError
                chats = data["chats"]
                user_states[uid]["state"]         = "send_choose_interval"
                user_states[uid]["selected_text"] = texts[n][1]
                user_states[uid]["selected_chats"] = chats
                chat_list = "\n".join(f"• {name}" for _, name, _ in chats)
                await reply(client, event.chat_id, 
                    f"📢 Рассылка во ВСЕ {len(chats)} чатов:\n{chat_list}\n\n"
                    f"⏱ Введите интервал в минутах (например: 30):\n\n/cancel — отмена"
                )
            except ValueError:
                await reply(client, event.chat_id, f"⚠️ Введите номер от 1 до {len(texts)}")

        # ---- рассылка: выбор интервала ----
        elif state == "send_choose_interval":
            try:
                interval = int(text)
                if interval < 1: raise ValueError
                chats        = data.get("selected_chats", data["chats"])
                mailing_text = data["selected_text"]
                target       = data["target"]
                user_states.pop(uid, None)

                chat_list = "\n".join(f"• {name}" for _, name, _ in chats)
                await reply(client, event.chat_id, 
                    f"🚀 Рассылка запущена!\n\n"
                    f"📋 Чатов: {len(chats)}\n{chat_list}\n\n"
                    f"⏱ Интервал: {interval} мин.\n\n/stop — остановить"
                )
                task = asyncio.create_task(
                    mailing_task(client, uid, chats, mailing_text, interval, target)
                )
                active_tasks[uid] = task
            except ValueError:
                await reply(client, event.chat_id, "⚠️ Введите целое число минут (минимум 1)")

# ================== ЗАПУСК ==================
async def run_client(acc):
    while True:
        try:
            c = TelegramClient(
                acc["session"], acc["api_id"], acc["api_hash"],
                connection_retries=10,
                retry_delay=3,
                auto_reconnect=True,
            )
            register_handlers(c)
            kwargs = {}
            if acc["phone"]:
                kwargs["phone"] = acc["phone"]
            await c.start(**kwargs)
            me = await c.get_me()
            logger.info(f"✅ Аккаунт #{acc['index']}: {me.first_name} (@{me.username}) подключён")
            await c.run_until_disconnected()
            logger.info(f"⚠️ Аккаунт #{acc['index']} отключился, переподключаюсь...")
        except Exception as e:
            logger.info(f"❌ Аккаунт #{acc['index']} ошибка: {e}")
        await asyncio.sleep(5)

async def main():
    accounts = load_accounts()
    logger.info(f"\n🚀 Запускаю {len(accounts)} аккаунт(ов).")
    logger.info("Напиши /start или /menu в любой приватный чат чтобы открыть меню.\n")
    await asyncio.gather(*[run_client(acc) for acc in accounts])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n⛔ Остановлено.")
    except Exception as e:
        print('ERROR', repr(e))
        input('Нажми ENTER')
