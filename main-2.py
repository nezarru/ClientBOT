import os
import sqlite3
import asyncio
import random
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

load_dotenv()

# ================== АККАУНТЫ ==================
# В .env задайте:
# ACCOUNTS=3  (количество аккаунтов)
# API_ID_1, API_HASH_1, SESSION_1
# API_ID_2, API_HASH_2, SESSION_2
# ...

def load_accounts():
    count = int(os.getenv("ACCOUNTS", "1"))
    accounts = []
    for i in range(1, count + 1):
        api_id   = os.getenv(f"API_ID_{i}")
        api_hash = os.getenv(f"API_HASH_{i}")
        session  = os.getenv(f"SESSION_{i}", f"session_{i}")
        if api_id and api_hash:
            accounts.append({
                "api_id":   int(api_id),
                "api_hash": api_hash,
                "session":  session,
            })
    if not accounts:
        raise RuntimeError("Нет аккаунтов в .env! Укажите API_ID_1, API_HASH_1, SESSION_1 (и т.д.)")
    return accounts

# ================== БАЗА ==================
conn   = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS mytext (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text    TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS mychats (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER,
    chat_id   INTEGER,
    chat_name TEXT
)
""")
conn.commit()

# ================== ГЛОБАЛЬНЫЕ СОСТОЯНИЯ ==================
user_states  = {}   # user_id -> state dict
active_tasks = {}   # user_id -> asyncio.Task

# ================== ПОМОЩНИКИ ==================
async def send_safe(client, chat_id, text):
    try:
        await client.send_message(chat_id, text)
        return True
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        return False
    except Exception as e:
        print(f"[{client.session.filename}] Ошибка отправки в {chat_id}: {e}")
        return False

# ================== ЗАДАЧА РАССЫЛКИ ==================
async def mailing_task(client, user_id, chats, text, interval):
    try:
        while True:
            success = 0
            fail    = 0
            for _, chat_name, chat_id in chats:
                ok = await send_safe(client, chat_id, text)
                if ok:
                    success += 1
                else:
                    fail += 1
                await asyncio.sleep(random.uniform(3, 8))

            await client.send_message(
                user_id,
                f"📢 **Цикл завершён**\n"
                f"✅ Отправлено: {success}\n"
                f"❌ Ошибок: {fail}\n"
                f"⏱ Следующий через {interval} мин."
            )
            await asyncio.sleep(interval * 60)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await client.send_message(user_id, f"❌ Ошибка рассылки: {e}")
    finally:
        active_tasks.pop(user_id, None)

# ================== РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ==================
def register_handlers(client):

    @client.on(events.NewMessage(pattern='/start'))
    async def start(event):
        if not event.is_private:
            return
        await event.respond(
            "🚀 **Userbot для рассылок**\n\n"
            "/admin — управление\n"
            "/status — статус рассылки"
        )

    @client.on(events.NewMessage(pattern='/admin'))
    async def admin(event):
        if not event.is_private:
            return
        await event.respond(
            "**Админ-панель**\n\n"
            "📋 /my_texts — Мои тексты\n"
            "➕ /add_text — Добавить текст\n"
            "🗑 /del_text — Удалить текст\n"
            "💬 /my_chats — Мои чаты\n"
            "➕ /add_chat — Добавить чат\n"
            "🗑 /del_chat — Удалить чат\n"
            "🚀 /start_mailing — Начать рассылку\n"
            "⛔ /stop_mailing — Остановить рассылку"
        )

    @client.on(events.NewMessage(pattern='/status'))
    async def status(event):
        if not event.is_private:
            return
        if event.sender_id in active_tasks:
            await event.respond("✅ Рассылка **работает**")
        else:
            await event.respond("❌ Рассылка не активна")

    # ---- ТЕКСТЫ ----

    @client.on(events.NewMessage(pattern='/my_texts'))
    async def my_texts(event):
        if not event.is_private:
            return
        cursor.execute("SELECT id, text FROM mytext WHERE user_id = ?", (event.sender_id,))
        rows = cursor.fetchall()
        if not rows:
            await event.respond("Нет текстов. Добавьте через /add_text")
            return
        txt = "📋 **Ваши тексты:**\n\n"
        for i, (tid, t) in enumerate(rows, 1):
            preview = t[:50] + "..." if len(t) > 50 else t
            txt += f"{i}. {preview}\n"
        await event.respond(txt)

    @client.on(events.NewMessage(pattern='/add_text'))
    async def add_text(event):
        if not event.is_private:
            return
        user_states[event.sender_id] = {"state": "add_text"}
        await event.respond("Отправьте текст для добавления:")

    @client.on(events.NewMessage(pattern='/del_text'))
    async def del_text(event):
        if not event.is_private:
            return
        cursor.execute("SELECT id, text FROM mytext WHERE user_id = ?", (event.sender_id,))
        rows = cursor.fetchall()
        if not rows:
            await event.respond("Нет текстов для удаления.")
            return
        txt = "🗑 **Какой текст удалить? Отправьте номер:**\n\n"
        for i, (tid, t) in enumerate(rows, 1):
            preview = t[:50] + "..." if len(t) > 50 else t
            txt += f"{i}. {preview}\n"
        await event.respond(txt)
        user_states[event.sender_id] = {"state": "del_text", "rows": rows}

    # ---- ЧАТЫ ----

    @client.on(events.NewMessage(pattern='/my_chats'))
    async def my_chats(event):
        if not event.is_private:
            return
        cursor.execute("SELECT id, chat_name, chat_id FROM mychats WHERE user_id = ?", (event.sender_id,))
        rows = cursor.fetchall()
        if not rows:
            await event.respond("Нет добавленных чатов. Добавьте через /add_chat")
            return
        txt = "💬 **Ваши чаты:**\n\n"
        for i, (cid, name, chat_id) in enumerate(rows, 1):
            txt += f"{i}. {name}\n"
        await event.respond(txt)

    @client.on(events.NewMessage(pattern='/add_chat'))
    async def add_chat(event):
        if not event.is_private:
            return
        await event.respond("⏳ Загружаю ваши чаты...")
        dialogs = await client.get_dialogs(limit=100)
        chats = [(d.id, d.name) for d in dialogs if d.is_group or d.is_channel]
        if not chats:
            await event.respond("Нет доступных групп и каналов.")
            return
        txt = "💬 **Выберите чат (отправьте номер):**\n\n"
        for i, (cid, name) in enumerate(chats, 1):
            txt += f"{i}. {name}\n"
        await event.respond(txt)
        user_states[event.sender_id] = {"state": "add_chat", "chats": chats}

    @client.on(events.NewMessage(pattern='/del_chat'))
    async def del_chat(event):
        if not event.is_private:
            return
        cursor.execute("SELECT id, chat_name, chat_id FROM mychats WHERE user_id = ?", (event.sender_id,))
        rows = cursor.fetchall()
        if not rows:
            await event.respond("Нет чатов для удаления.")
            return
        txt = "🗑 **Какой чат удалить? Отправьте номер:**\n\n"
        for i, (cid, name, chat_id) in enumerate(rows, 1):
            txt += f"{i}. {name}\n"
        await event.respond(txt)
        user_states[event.sender_id] = {"state": "del_chat", "rows": rows}

    # ---- РАССЫЛКА ----

    @client.on(events.NewMessage(pattern='/start_mailing'))
    async def start_mailing(event):
        if not event.is_private:
            return
        user_id = event.sender_id

        if user_id in active_tasks:
            await event.respond("⚠️ Рассылка уже запущена. Сначала остановите через /stop_mailing")
            return

        cursor.execute("SELECT id, chat_name, chat_id FROM mychats WHERE user_id = ?", (user_id,))
        chats = cursor.fetchall()
        if not chats:
            await event.respond("Сначала добавьте хотя бы один чат через /add_chat")
            return

        cursor.execute("SELECT id, text FROM mytext WHERE user_id = ?", (user_id,))
        texts = cursor.fetchall()
        if not texts:
            await event.respond("Сначала добавьте хотя бы один текст через /add_text")
            return

        await event.respond(
            "Выберите тип рассылки:\n\n"
            "1 — Одиночная (один чат)\n"
            "2 — Массовая (несколько чатов или все)"
        )
        user_states[user_id] = {"state": "choose_mailing_type", "chats": chats, "texts": texts}

    @client.on(events.NewMessage(pattern='/stop_mailing'))
    async def stop_mailing(event):
        if not event.is_private:
            return
        user_id = event.sender_id
        if user_id in active_tasks:
            active_tasks[user_id].cancel()
            await event.respond("⛔ Рассылка остановлена.")
        else:
            await event.respond("Рассылка не запущена.")

    # ---- ОБРАБОТКА ВВОДА ----

    @client.on(events.NewMessage())
    async def handler(event):
        if not event.is_private:
            return
        if event.text and event.text.startswith('/'):
            return

        user_id    = event.sender_id
        text       = event.text.strip() if event.text else ""
        state_data = user_states.get(user_id, {})
        state      = state_data.get("state")

        if not state:
            return

        # Добавление текста
        if state == "add_text":
            if not text:
                await event.respond("Пожалуйста, отправьте текстовое сообщение.")
                return
            cursor.execute("INSERT INTO mytext (user_id, text) VALUES (?, ?)", (user_id, text))
            conn.commit()
            user_states.pop(user_id, None)
            await event.respond("✅ Текст добавлен!")
            return

        # Удаление текста
        if state == "del_text":
            rows = state_data["rows"]
            try:
                num = int(text) - 1
                if num < 0 or num >= len(rows):
                    raise ValueError
                tid = rows[num][0]
                cursor.execute("DELETE FROM mytext WHERE id = ?", (tid,))
                conn.commit()
                user_states.pop(user_id, None)
                await event.respond("✅ Текст удалён!")
            except (ValueError, IndexError):
                await event.respond(f"Введите число от 1 до {len(rows)}")
            return

        # Добавление чата
        if state == "add_chat":
            chats = state_data["chats"]
            try:
                num = int(text) - 1
                if num < 0 or num >= len(chats):
                    raise ValueError
                chat_id, chat_name = chats[num]
                cursor.execute(
                    "INSERT INTO mychats (user_id, chat_id, chat_name) VALUES (?, ?, ?)",
                    (user_id, chat_id, chat_name)
                )
                conn.commit()
                user_states.pop(user_id, None)
                await event.respond(f"✅ Чат добавлен: **{chat_name}**")
            except (ValueError, IndexError):
                await event.respond(f"Введите число от 1 до {len(chats)}")
            return

        # Удаление чата
        if state == "del_chat":
            rows = state_data["rows"]
            try:
                num = int(text) - 1
                if num < 0 or num >= len(rows):
                    raise ValueError
                cid = rows[num][0]
                cursor.execute("DELETE FROM mychats WHERE id = ?", (cid,))
                conn.commit()
                user_states.pop(user_id, None)
                await event.respond("✅ Чат удалён!")
            except (ValueError, IndexError):
                await event.respond(f"Введите число от 1 до {len(rows)}")
            return

        # Выбор типа рассылки
        if state == "choose_mailing_type":
            chats = state_data["chats"]
            texts = state_data["texts"]
            if text == "1":
                txt = "💬 **Выберите чат (отправьте номер):**\n\n"
                for i, (cid, name, chat_id) in enumerate(chats, 1):
                    txt += f"{i}. {name}\n"
                await event.respond(txt)
                user_states[user_id] = {"state": "choose_single_chat", "chats": chats, "texts": texts}
            elif text == "2":
                txt = "💬 **Выберите чаты (номера через запятую или 'все'):**\n\n"
                for i, (cid, name, chat_id) in enumerate(chats, 1):
                    txt += f"{i}. {name}\n"
                txt += "\nПример: 1,3,5 или все"
                await event.respond(txt)
                user_states[user_id] = {"state": "choose_chats_for_mailing", "chats": chats, "texts": texts}
            else:
                await event.respond("Отправьте 1 или 2")
            return

        # Выбор одного чата
        if state == "choose_single_chat":
            chats = state_data["chats"]
            texts = state_data["texts"]
            try:
                num = int(text) - 1
                if num < 0 or num >= len(chats):
                    raise ValueError
                selected_chats = [chats[num]]
                txt = "📋 **Выберите текст (отправьте номер):**\n\n"
                for i, (tid, t) in enumerate(texts, 1):
                    preview = t[:50] + "..." if len(t) > 50 else t
                    txt += f"{i}. {preview}\n"
                await event.respond(txt)
                user_states[user_id] = {"state": "choose_text", "selected_chats": selected_chats, "texts": texts}
            except (ValueError, IndexError):
                await event.respond(f"Введите число от 1 до {len(chats)}")
            return

        # Выбор нескольких чатов
        if state == "choose_chats_for_mailing":
            chats = state_data["chats"]
            texts = state_data["texts"]
            try:
                if text.lower() == "все":
                    selected_chats = chats
                else:
                    nums = [int(x.strip()) - 1 for x in text.split(",")]
                    for n in nums:
                        if n < 0 or n >= len(chats):
                            raise ValueError
                    selected_chats = [chats[n] for n in nums]

                txt = "📋 **Выберите текст для рассылки (отправьте номер):**\n\n"
                for i, (tid, t) in enumerate(texts, 1):
                    preview = t[:50] + "..." if len(t) > 50 else t
                    txt += f"{i}. {preview}\n"
                await event.respond(txt)
                user_states[user_id] = {"state": "choose_text", "selected_chats": selected_chats, "texts": texts}
            except (ValueError, IndexError):
                await event.respond(f"Введите номера от 1 до {len(chats)} через запятую или 'все'")
            return

        # Выбор текста
        if state == "choose_text":
            texts = state_data["texts"]
            try:
                num = int(text) - 1
                if num < 0 or num >= len(texts):
                    raise ValueError
                selected_text = texts[num][1]
                await event.respond("⏱ На сколько минут интервал между циклами? (например: 30)")
                user_states[user_id] = {
                    "state":          "choose_interval",
                    "text":           selected_text,
                    "selected_chats": state_data["selected_chats"],
                }
            except (ValueError, IndexError):
                await event.respond(f"Введите число от 1 до {len(texts)}")
            return

        # Выбор интервала
        if state == "choose_interval":
            try:
                interval = int(text)
                if interval < 1:
                    raise ValueError
                selected_chats = state_data["selected_chats"]
                mailing_text   = state_data["text"]
                user_states.pop(user_id, None)

                chat_list = "\n".join(f"• {name}" for _, name, _ in selected_chats)
                await event.respond(
                    f"✅ **Рассылка запущена!**\n\n"
                    f"Чаты ({len(selected_chats)}):\n{chat_list}\n\n"
                    f"Интервал: {interval} мин.\n"
                    f"Остановить: /stop_mailing"
                )
                task = asyncio.create_task(
                    mailing_task(client, user_id, selected_chats, mailing_text, interval)
                )
                active_tasks[user_id] = task
            except ValueError:
                await event.respond("Введите целое число минут (минимум 1).")
            return

# ================== ЗАПУСК ==================
async def main():
    accounts = load_accounts()
    clients  = []

    for acc in accounts:
        c = TelegramClient(acc["session"], acc["api_id"], acc["api_hash"])
        register_handlers(c)
        await c.start(phone=acc["session"])
        me = await c.get_me()
        print(f"✅ Аккаунт {me.first_name} (@{me.username}) запущен")
        clients.append(c)

    print(f"\n🚀 Запущено {len(clients)} аккаунт(ов). Напишите /admin любому из них.\n")

    # Все клиенты работают параллельно
    await asyncio.gather(*[c.run_until_disconnected() for c in clients])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print('ERROR', repr(e))
        input('Нажми ENTER')
