# 🚀 Crypto Alert Bot

Telegram-бот для мониторинга аномальных движений криптовалют на Binance.

## Что умеет

- 🔔 **Алерты о резком изменении цены** (настраиваемый порог, по умолчанию 3% за 5 мин)
- 📊 **Алерты о аномальном объёме** (по умолчанию >3× от среднего)
- ⏰ **Алерты о резком движении за 1 час**
- 📋 **Мониторинг нескольких монет** одновременно
- ⚙️ **Индивидуальные настройки** чувствительности для каждого пользователя
- 📡 **Текущий статус** любой монеты

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Запустить бота |
| `/add BTC` | Добавить монету в мониторинг |
| `/remove BTC` | Убрать монету |
| `/list` | Список отслеживаемых монет |
| `/status BTC` | Текущая цена и статистика |
| `/settings` | Настройка порогов алертов |
| `/help` | Справка |

## Быстрый старт (локально)

```bash
# 1. Клонируй репозиторий
git clone https://github.com/YOUR_USERNAME/crypto-alert-bot.git
cd crypto-alert-bot

# 2. Установи зависимости
pip install -r requirements.txt

# 3. Создай .env файл
cp .env.example .env
# Вставь твой токен бота от @BotFather

# 4. Запусти
python src/bot.py
```

## Деплой на Railway (рекомендуется — бесплатно)

1. Форкни этот репозиторий
2. Зайди на [railway.app](https://railway.app)
3. **New Project** → **Deploy from GitHub** → выбери репозиторий
4. В разделе **Variables** добавь:
   - `TELEGRAM_BOT_TOKEN` = токен от @BotFather
5. Railway автоматически запустит бота 🎉

## Деплой на Render.com

1. Зайди на [render.com](https://render.com)
2. **New** → **Background Worker**
3. Подключи GitHub репозиторий
4. Build command: `pip install -r requirements.txt`
5. Start command: `python src/bot.py`
6. Добавь Environment Variable: `TELEGRAM_BOT_TOKEN`

## Структура проекта

```
crypto-alert-bot/
├── src/
│   ├── bot.py        # Основной файл бота
│   ├── monitor.py    # Логика мониторинга и детекции аномалий
│   └── database.py   # SQLite база данных
├── .github/
│   └── workflows/
│       └── deploy.yml # CI/CD
├── data/             # База данных (создаётся автоматически)
├── logs/             # Логи (создаются автоматически)
├── .env.example      # Пример конфига
├── Procfile          # Для Heroku/Railway
├── railway.toml      # Конфиг Railway
├── render.yaml       # Конфиг Render.com
├── requirements.txt
└── runtime.txt
```

## Получить токен бота

1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Отправь `/newbot`
3. Следуй инструкциям
4. Скопируй токен в `.env` файл

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота | **обязательно** |
| `DB_PATH` | Путь к файлу БД | `data/bot.db` |
