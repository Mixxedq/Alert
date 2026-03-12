import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from monitor import CryptoMonitor
from database import Database

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

db = Database()
monitor = CryptoMonitor()

# ── /start ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.add_user(user_id)
    text = (
        "🚀 *Crypto Alert Bot*\n\n"
        "Я слежу за аномальными движениями криптовалют и сразу тебя уведомляю.\n\n"
        "*Команды:*\n"
        "• /add `<TICKER>` — добавить монету (напр. `/add BTC`)\n"
        "• /remove `<TICKER>` — убрать монету\n"
        "• /list — список отслеживаемых монет\n"
        "• /status `<TICKER>` — текущая цена и данные\n"
        "• /settings — настройки чувствительности\n"
        "• /help — справка\n\n"
        "📊 Алерты срабатывают при:\n"
        "— резком изменении цены (>3% за 5 мин)\n"
        "— аномальном объёме торгов (>3× от среднего)\n"
        "— резком изменении за 1 час (>5%)"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

# ── /help ────────────────────────────────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Справка*\n\n"
        "*Добавить монету:*\n`/add BTC` или `/add ETH`\n\n"
        "*Убрать монету:*\n`/remove BTC`\n\n"
        "*Список монет:*\n`/list`\n\n"
        "*Текущий статус:*\n`/status BTC`\n\n"
        "*Настройки алертов:*\n`/settings`\n\n"
        "Поддерживаются все пары с USDT на Binance.\n"
        "Данные обновляются каждые *60 секунд*."
    )
    await update.message.reply_text(text, parse_mode='Markdown')

# ── /add ─────────────────────────────────────────────────────────────────────
async def add_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Укажи тикер: `/add BTC`", parse_mode='Markdown')
        return

    ticker = context.args[0].upper().strip()
    symbol = ticker if ticker.endswith("USDT") else f"{ticker}USDT"

    msg = await update.message.reply_text(f"🔍 Проверяю {ticker}...")

    price = await monitor.get_price(symbol)
    if price is None:
        await msg.edit_text(
            f"❌ Монета *{ticker}* не найдена на Binance.\n"
            "Проверь тикер (пример: BTC, ETH, SOL).",
            parse_mode='Markdown'
        )
        return

    added = db.add_ticker(user_id, symbol, ticker)
    if added:
        await msg.edit_text(
            f"✅ *{ticker}* добавлен!\n"
            f"💰 Текущая цена: *${price:,.4f}*\n\n"
            f"Буду уведомлять об аномальных движениях 🔔",
            parse_mode='Markdown'
        )
    else:
        await msg.edit_text(f"⚠️ *{ticker}* уже в списке.", parse_mode='Markdown')

# ── /remove ───────────────────────────────────────────────────────────────────
async def remove_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Укажи тикер: `/remove BTC`", parse_mode='Markdown')
        return

    ticker = context.args[0].upper().strip()
    symbol = ticker if ticker.endswith("USDT") else f"{ticker}USDT"
    removed = db.remove_ticker(user_id, symbol)

    if removed:
        await update.message.reply_text(f"🗑 *{ticker}* удалён из мониторинга.", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ *{ticker}* не найден в твоём списке.", parse_mode='Markdown')

# ── /list ─────────────────────────────────────────────────────────────────────
async def list_tickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tickers = db.get_user_tickers(user_id)

    if not tickers:
        await update.message.reply_text(
            "📋 Список пуст.\nДобавь монеты командой `/add BTC`",
            parse_mode='Markdown'
        )
        return

    lines = ["📋 *Твои монеты:*\n"]
    for t in tickers:
        price = await monitor.get_price(t['symbol'])
        price_str = f"${price:,.4f}" if price else "—"
        lines.append(f"• *{t['name']}* — {price_str}")

    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

# ── /status ───────────────────────────────────────────────────────────────────
async def status_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Укажи тикер: `/status BTC`", parse_mode='Markdown')
        return

    ticker = context.args[0].upper().strip()
    symbol = ticker if ticker.endswith("USDT") else f"{ticker}USDT"

    msg = await update.message.reply_text(f"📡 Получаю данные для {ticker}...")
    data = await monitor.get_full_stats(symbol)

    if data is None:
        await msg.edit_text(f"❌ Не удалось получить данные для *{ticker}*.", parse_mode='Markdown')
        return

    change_1h_icon = "🔴" if data['change_1h'] < 0 else "🟢"
    change_24h_icon = "🔴" if data['change_24h'] < 0 else "🟢"

    text = (
        f"📊 *{ticker}/USDT*\n\n"
        f"💰 Цена: *${data['price']:,.4f}*\n"
        f"{change_1h_icon} За 1ч: *{data['change_1h']:+.2f}%*\n"
        f"{change_24h_icon} За 24ч: *{data['change_24h']:+.2f}%*\n"
        f"📦 Объём 24ч: *${data['volume_24h']:,.0f}*\n"
        f"📈 Хай 24ч: *${data['high_24h']:,.4f}*\n"
        f"📉 Лоу 24ч: *${data['low_24h']:,.4f}*\n"
        f"🔄 Кол-во сделок: *{data['trades_24h']:,}*"
    )
    await msg.edit_text(text, parse_mode='Markdown')

# ── /settings ─────────────────────────────────────────────────────────────────
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cfg = db.get_user_settings(user_id)

    keyboard = [
        [
            InlineKeyboardButton("📉 Порог цены: -", callback_data="price_down"),
            InlineKeyboardButton(f"  {cfg['price_threshold']}%  ", callback_data="noop"),
            InlineKeyboardButton("📈 +", callback_data="price_up"),
        ],
        [
            InlineKeyboardButton("📊 Порог объёма: -", callback_data="vol_down"),
            InlineKeyboardButton(f"  {cfg['volume_multiplier']}×  ", callback_data="noop"),
            InlineKeyboardButton("📊 +", callback_data="vol_up"),
        ],
        [InlineKeyboardButton("✅ Сохранить", callback_data="save_settings")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚙️ *Настройки алертов*\n\n"
        f"📉 Порог изменения цены: *{cfg['price_threshold']}%* (за 5 мин)\n"
        f"📊 Множитель объёма: *{cfg['volume_multiplier']}×* от среднего\n\n"
        "Используй кнопки для изменения:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "noop":
        return

    cfg = db.get_user_settings(user_id)

    if query.data == "price_up":
        cfg['price_threshold'] = min(20, cfg['price_threshold'] + 0.5)
    elif query.data == "price_down":
        cfg['price_threshold'] = max(0.5, cfg['price_threshold'] - 0.5)
    elif query.data == "vol_up":
        cfg['volume_multiplier'] = min(10, cfg['volume_multiplier'] + 0.5)
    elif query.data == "vol_down":
        cfg['volume_multiplier'] = max(1.5, cfg['volume_multiplier'] - 0.5)
    elif query.data == "save_settings":
        db.save_user_settings(user_id, cfg)
        await query.edit_message_text("✅ Настройки сохранены!", parse_mode='Markdown')
        return

    db.save_user_settings(user_id, cfg)

    keyboard = [
        [
            InlineKeyboardButton("📉 Порог цены: -", callback_data="price_down"),
            InlineKeyboardButton(f"  {cfg['price_threshold']}%  ", callback_data="noop"),
            InlineKeyboardButton("📈 +", callback_data="price_up"),
        ],
        [
            InlineKeyboardButton("📊 Порог объёма: -", callback_data="vol_down"),
            InlineKeyboardButton(f"  {cfg['volume_multiplier']}×  ", callback_data="noop"),
            InlineKeyboardButton("📊 +", callback_data="vol_up"),
        ],
        [InlineKeyboardButton("✅ Сохранить", callback_data="save_settings")],
    ]
    await query.edit_message_text(
        "⚙️ *Настройки алертов*\n\n"
        f"📉 Порог изменения цены: *{cfg['price_threshold']}%* (за 5 мин)\n"
        f"📊 Множитель объёма: *{cfg['volume_multiplier']}×* от среднего\n\n"
        "Используй кнопки для изменения:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ── Background monitoring job ─────────────────────────────────────────────────
async def monitoring_job(context: ContextTypes.DEFAULT_TYPE):
    all_users = db.get_all_users()
    for user in all_users:
        user_id = user['user_id']
        tickers = db.get_user_tickers(user_id)
        cfg = db.get_user_settings(user_id)

        for t in tickers:
            alerts = await monitor.check_anomalies(
                t['symbol'], t['name'],
                price_threshold=cfg['price_threshold'],
                volume_multiplier=cfg['volume_multiplier']
            )
            for alert in alerts:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=alert,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Failed to send alert to {user_id}: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN env variable is not set!")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_ticker))
    app.add_handler(CommandHandler("remove", remove_ticker))
    app.add_handler(CommandHandler("list", list_tickers))
    app.add_handler(CommandHandler("status", status_ticker))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CallbackQueryHandler(settings_callback))

    # Run monitoring every 60 seconds
    app.job_queue.run_repeating(monitoring_job, interval=60, first=10)

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
