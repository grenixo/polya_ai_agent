"""
Telegram AI Agent → DeepSeek анализ → Google Sheets тикеты
Установка: pip install python-telegram-bot openai google-auth google-auth-oauthlib google-api-python-client
"""

import json
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from openai import OpenAI

# Загружаем .env если есть (для локальной разработки)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# ─── НАСТРОЙКИ ────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
GOOGLE_SPREADSHEET_ID = os.environ["GOOGLE_SPREADSHEET_ID"]

# Список разрешённых chat_id (через запятую). Если пусто — бот отвечает всем.
_allowed = os.environ.get("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS: list[int] = [int(x.strip()) for x in _allowed.split(",") if x.strip()]

# Анализировать каждые N сообщений (или по команде /analyze)
AUTO_ANALYZE_EVERY = 20

# Авто-анализ по таймеру: если буфер не пуст и не было активности N минут
AUTO_ANALYZE_AFTER_MINUTES = 60  # анализ через 1 час тишины
AUTO_ANALYZE_MIN_MESSAGES = 1    # минимум сообщений для авто-анализа по таймеру

# ─── ИНИЦИАЛИЗАЦИЯ ────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

deepseek = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

messages_buffer: list[dict] = []
last_activity: datetime | None = None   # время последнего сообщения
last_chat_id: int | None = None         # чат для отправки авто-отчёта


# ─── GOOGLE SHEETS ────────────────────────────────────────────────────────────

def get_sheets_service():
    creds = Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def ensure_sheet_headers(service):
    """Создаёт заголовки если таблица пустая"""
    headers = [["ID", "Дата", "Приоритет", "Заголовок", "Описание", "Исполнитель", "Статус", "Контекст из чата"]]
    result = service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SPREADSHEET_ID,
        range="A1:H1"
    ).execute()

    if not result.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=GOOGLE_SPREADSHEET_ID,
            range="A1:H1",
            valueInputOption="RAW",
            body={"values": headers}
        ).execute()
        logger.info("✅ Заголовки таблицы созданы")


def get_next_ticket_id(service) -> int:
    """Получает следующий ID тикета"""
    result = service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SPREADSHEET_ID,
        range="A:A"
    ).execute()
    rows = result.get("values", [])
    return len(rows)  # строка 1 = заголовок, далее ID = номер строки


def write_tickets_to_sheet(tickets: list[dict]) -> list[str]:
    """Записывает список тикетов в Google Sheets, возвращает список ID"""
    service = get_sheets_service()
    ensure_sheet_headers(service)

    next_id = get_next_ticket_id(service)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = []
    ticket_ids = []
    for i, ticket in enumerate(tickets):
        ticket_id = f"TKT-{next_id + i:04d}"
        ticket_ids.append(ticket_id)
        rows.append([
            ticket_id,
            now,
            ticket.get("priority", "medium").upper(),
            ticket.get("title", ""),
            ticket.get("description", ""),
            ticket.get("assignee", "не назначен"),
            "open",
            ticket.get("context", "")
        ])

    service.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SPREADSHEET_ID,
        range="A:H",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()

    # Цветовая маркировка по приоритету
    _colorize_priority_rows(service, next_id, tickets)

    logger.info(f"✅ Записано {len(rows)} тикетов в Google Sheets")
    return ticket_ids


def _colorize_priority_rows(service, start_row_index: int, tickets: list[dict]):
    """Красит строки по приоритету: красный/жёлтый/зелёный"""
    priority_colors = {
        "high":   {"red": 1.0, "green": 0.8, "blue": 0.8},
        "medium": {"red": 1.0, "green": 0.95, "blue": 0.8},
        "low":    {"red": 0.85, "green": 1.0, "blue": 0.85},
    }

    requests = []
    for i, ticket in enumerate(tickets):
        color = priority_colors.get(ticket.get("priority", "medium"), priority_colors["medium"])
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": start_row_index + i,
                    "endRowIndex": start_row_index + i + 1,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor"
            }
        })

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=GOOGLE_SPREADSHEET_ID,
            body={"requests": requests}
        ).execute()


# ─── DEEPSEEK АНАЛИЗ ──────────────────────────────────────────────────────────

def analyze_chat_with_deepseek(messages: list[dict]) -> list[dict]:
    """Отправляет переписку в DeepSeek, получает список тикетов"""

    chat_text = "\n".join([
        f"[{m['time']}] {m['from']}: {m['text']}"
        for m in messages
    ])

    prompt = f"""Ты менеджер проектов. Проанализируй переписку в Telegram и найди:
- задачи, которые кто-то взял на себя
- проблемы, которые нужно решить
- баги или ошибки
- запросы на помощь

Верни ТОЛЬКО валидный JSON без пояснений:
{{
  "tickets": [
    {{
      "title": "Краткое название задачи (до 60 символов)",
      "description": "Подробное описание что нужно сделать",
      "priority": "high | medium | low",
      "assignee": "имя исполнителя или пустая строка",
      "context": "дословная цитата из чата, которая стала основой тикета"
    }}
  ]
}}

Если задач нет — верни {{"tickets": []}}

Переписка:
{chat_text}"""

    response = deepseek.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,  # низкая температура = более предсказуемый JSON
        max_tokens=2000,
    )

    raw = response.choices[0].message.content.strip()

    # Убираем markdown обёртку если есть
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    data = json.loads(raw)
    return data.get("tickets", [])


# ─── TELEGRAM HANDLERS ────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет каждое сообщение в буфер"""
    global last_activity, last_chat_id

    msg = update.message
    if not msg or not msg.text:
        return

    messages_buffer.append({
        "from": msg.from_user.full_name if msg.from_user else "Unknown",
        "text": msg.text,
        "time": msg.date.strftime("%H:%M"),
    })

    last_activity = datetime.now()
    last_chat_id = msg.chat_id

    logger.info(f"📨 [{len(messages_buffer)}/{AUTO_ANALYZE_EVERY}] {msg.from_user.full_name}: {msg.text[:50]}")

    # Авто-анализ при накоплении N сообщений
    if len(messages_buffer) >= AUTO_ANALYZE_EVERY:
        await run_analysis(update, context)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/analyze — запускает анализ вручную"""
    await run_analysis(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — показывает сколько сообщений в буфере"""
    if last_activity:
        minutes_since = (datetime.now() - last_activity).total_seconds() / 60
        timer_info = f"Последнее сообщение: {minutes_since:.0f} мин назад\nАвто-анализ через: {max(0, AUTO_ANALYZE_AFTER_MINUTES - minutes_since):.0f} мин тишины"
    else:
        timer_info = "Активности пока не было"

    await update.message.reply_text(
        f"📊 В буфере: {len(messages_buffer)} сообщений\n"
        f"Авто-анализ каждые: {AUTO_ANALYZE_EVERY} сообщений\n"
        f"{timer_info}\n"
        f"Запустить вручную: /analyze"
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clear — очищает буфер без анализа"""
    count = len(messages_buffer)
    messages_buffer.clear()
    await update.message.reply_text(f"🗑️ Буфер очищен ({count} сообщений удалено)")


async def silent_run_analysis(bot, chat_id: int):
    """Запускает анализ по таймеру (без update объекта)"""
    if not messages_buffer:
        return

    count = len(messages_buffer)
    logger.info(f"⏰ Авто-анализ по таймеру: {count} сообщений")

    await bot.send_message(chat_id, f"⏰ Авто-анализ: накопилось {count} сообщ. за {AUTO_ANALYZE_AFTER_MINUTES} мин...")

    try:
        snapshot = messages_buffer.copy()
        tickets = analyze_chat_with_deepseek(snapshot)

        if not tickets:
            await bot.send_message(chat_id, "✅ Авто-анализ завершён: задач не найдено")
            messages_buffer.clear()
            return

        ticket_ids = write_tickets_to_sheet(tickets)
        messages_buffer.clear()

        lines = [f"✅ Авто-анализ: создано {len(tickets)} тикетов:\n"]
        for tid, ticket in zip(ticket_ids, tickets):
            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(ticket["priority"], "⚪")
            assignee = f" → {ticket['assignee']}" if ticket.get("assignee") else ""
            lines.append(f"{priority_emoji} *{tid}* {ticket['title']}{assignee}")

        await bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Timer analysis error: {e}")
        await bot.send_message(chat_id, f"❌ Ошибка авто-анализа: {e}")


async def timer_job(context: ContextTypes.DEFAULT_TYPE):
    """Джоб запускается каждую минуту, проверяет не пора ли анализировать"""
    global last_activity, last_chat_id

    if not messages_buffer or last_activity is None or last_chat_id is None:
        return

    if len(messages_buffer) < AUTO_ANALYZE_MIN_MESSAGES:
        return

    minutes_since = (datetime.now() - last_activity).total_seconds() / 60
    if minutes_since >= AUTO_ANALYZE_AFTER_MINUTES:
        logger.info(f"⏰ {minutes_since:.0f} мин тишины — запускаю авто-анализ")
        last_activity = None  # сбрасываем чтобы не запустить повторно
        await silent_run_analysis(context.bot, last_chat_id)


async def run_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает анализ и создаёт тикеты"""
    if not messages_buffer:
        await update.message.reply_text("📭 Буфер пуст, нечего анализировать")
        return

    count = len(messages_buffer)
    await update.message.reply_text(f"🔍 Анализирую {count} сообщений через DeepSeek...")

    try:
        # Анализ через DeepSeek
        snapshot = messages_buffer.copy()
        tickets = analyze_chat_with_deepseek(snapshot)

        if not tickets:
            await update.message.reply_text("✅ Задач не найдено в этой переписке")
            messages_buffer.clear()
            return

        # Запись в Google Sheets
        ticket_ids = write_tickets_to_sheet(tickets)
        messages_buffer.clear()

        # Отчёт в чат
        lines = [f"✅ Создано {len(tickets)} тикетов в Google Sheets:\n"]
        for tid, ticket in zip(ticket_ids, tickets):
            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(ticket["priority"], "⚪")
            assignee = f" → {ticket['assignee']}" if ticket.get("assignee") else ""
            lines.append(f"{priority_emoji} *{tid}* {ticket['title']}{assignee}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        await update.message.reply_text("❌ DeepSeek вернул невалидный JSON. Попробуй ещё раз.")
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


# ─── ЗАПУСК ───────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    chat_filter = filters.Chat(chat_ids=ALLOWED_CHAT_IDS) if ALLOWED_CHAT_IDS else filters.ALL

    app.add_handler(CommandHandler("analyze", cmd_analyze, filters=chat_filter))
    app.add_handler(CommandHandler("status", cmd_status, filters=chat_filter))
    app.add_handler(CommandHandler("clear", cmd_clear, filters=chat_filter))
    app.add_handler(MessageHandler(chat_filter & filters.TEXT & ~filters.COMMAND, handle_message))

    # Таймер: проверяем каждую минуту не пора ли делать авто-анализ
    app.job_queue.run_repeating(timer_job, interval=60, first=60)

    allowed_info = f"разрешённые чаты: {ALLOWED_CHAT_IDS}" if ALLOWED_CHAT_IDS else "доступ открыт всем (ALLOWED_CHAT_IDS не задан)"
    logger.info(
        f"🤖 Бот запущен.\n"
        f"   {allowed_info}\n"
        f"   Авто-анализ: каждые {AUTO_ANALYZE_EVERY} сообщений "
        f"или через {AUTO_ANALYZE_AFTER_MINUTES} мин тишины\n"
        f"   Команды: /analyze /status /clear"
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
