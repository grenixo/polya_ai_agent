# 🤖 Telegram AI Agent → DeepSeek → Google Sheets

Бот читает переписку в Telegram группе, анализирует через DeepSeek и создаёт тикеты в Google Sheets.

---

## 📁 Структура проекта

```
tg-agent/
├── telegram_agent.py     # основной код
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example          # шаблон переменных окружения
├── .env                  # секреты (не в git!)
├── service_account.json  # Google ключ (не в git!)
├── deploy.sh             # скрипт деплоя
└── .gitignore
```

---

## 🚀 Первый запуск

### 1. Клонируй репозиторий

```bash
git clone https://github.com/ВАШ_ЮЗЕР/tg-agent.git
cd tg-agent
```

### 2. Создай .env с секретами

```bash
cp .env.example .env
nano .env   # заполни все значения
```

### 3. Положи service_account.json

```bash
# Скопируй файл на сервер (с локальной машины):
scp service_account.json user@server:~/tg-agent/
```

### 4. Запусти

```bash
docker compose up --build -d
```

### 5. Проверь логи

```bash
docker compose logs -f
```

---

## 🔄 Обновление (после git push с другой машины)

```bash
./deploy.sh
```

Или вручную:

```bash
git pull origin main
docker compose up --build -d
```

---

## 📋 Команды бота в Telegram

| Команда | Действие |
|---|---|
| `/analyze` | Анализировать буфер прямо сейчас |
| `/status` | Сколько сообщений накоплено и когда авто-анализ |
| `/clear` | Очистить буфер без анализа |

---

## ⚙️ Настройки

В `.env`:

| Переменная | Описание |
|---|---|
| `TELEGRAM_TOKEN` | Токен бота от @BotFather |
| `DEEPSEEK_API_KEY` | API-ключ DeepSeek |
| `GOOGLE_SPREADSHEET_ID` | ID Google таблицы из URL |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Путь к ключу (по умолчанию `service_account.json`) |
| `ALLOWED_CHAT_IDS` | Разрешённые chat_id через запятую. Если не задать — бот отвечает всем |

В начале `telegram_agent.py`:

```python
AUTO_ANALYZE_EVERY = 20          # анализ каждые N сообщений
AUTO_ANALYZE_AFTER_MINUTES = 60  # анализ через N минут тишины
AUTO_ANALYZE_MIN_MESSAGES = 1    # минимум сообщений для таймера
```

---

## 🔍 Полезные команды

```bash
# Логи в реальном времени
docker compose logs -f

# Последние 50 строк
docker compose logs --tail=50

# Перезапуск
docker compose restart

# Остановить
docker compose down
```
