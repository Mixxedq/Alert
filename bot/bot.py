#!/usr/bin/env python3
"""
Trading Alerts Telegram Bot
Monitors assets for anomalous price movements and consolidations
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
from dataclasses import dataclass, asdict, field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ALLOWED_USERS: List[int] = []  # Fill or leave empty for all users
DATA_FILE = "data/state.json"

# Alert thresholds (defaults, user can override)
DEFAULT_SPIKE_THRESHOLD = 3.0       # % move in one candle → spike alert
DEFAULT_CONSOLIDATION_BARS = 10     # consecutive low-volatility bars
DEFAULT_CONSOLIDATION_ATR_MULT = 0.3  # candle range < 30% of ATR(14)
DEFAULT_CHECK_INTERVAL = 60         # seconds between checks

TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

BINANCE_API = "https://api.binance.com/api/v3"
COINGECKO_API = "https://api.coingecko.com/api/v3"

# ─── Data models ──────────────────────────────────────────────────────────────
@dataclass
class WatchedAsset:
    symbol: str           # e.g. BTCUSDT
    timeframe: str        # e.g. 1h
    spike_threshold: float = DEFAULT_SPIKE_THRESHOLD
    consolidation_bars: int = DEFAULT_CONSOLIDATION_BARS
    alert_spike: bool = True
    alert_consolidation: bool = True
    last_alerted: float = 0.0
    cooldown_sec: int = 3600  # no re-alert for same asset within this period

@dataclass
class UserState:
    chat_id: int
    assets: Dict[str, WatchedAsset] = field(default_factory=dict)
    active: bool = True


# ─── State persistence ────────────────────────────────────────────────────────
class StateManager:
    def __init__(self, filepath: str):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self._users: Dict[int, UserState] = {}
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath) as f:
                    raw = json.load(f)
                for uid_str, udata in raw.items():
                    uid = int(uid_str)
                    assets = {}
                    for sym, adata in udata.get("assets", {}).items():
                        assets[sym] = WatchedAsset(**adata)
                    self._users[uid] = UserState(
                        chat_id=uid,
                        assets=assets,
                        active=udata.get("active", True)
                    )
                logger.info(f"Loaded state for {len(self._users)} users")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def save(self):
        data = {}
        for uid, user in self._users.items():
            assets_raw = {}
            for sym, asset in user.assets.items():
                assets_raw[sym] = asdict(asset)
            data[str(uid)] = {"assets": assets_raw, "active": user.active}
        with open(self.filepath, "w") as f:
            json.dump(data, f, indent=2)

    def get_user(self, chat_id: int) -> UserState:
        if chat_id not in self._users:
            self._users[chat_id] = UserState(chat_id=chat_id)
        return self._users[chat_id]

    def all_users(self) -> List[UserState]:
        return list(self._users.values())


# ─── Market data fetcher ──────────────────────────────────────────────────────
class MarketData:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()

    async def get_klines(self, symbol: str, interval: str, limit: int = 50) -> List[dict]:
        """Fetch OHLCV candles from Binance."""
        session = await self.get_session()
        url = f"{BINANCE_API}/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"Binance {symbol}: HTTP {resp.status}")
                    return []
                data = await resp.json()
                candles = []
                for c in data:
                    candles.append({
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5]),
                        "time": int(c[0]) // 1000,
                    })
                return candles
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            return []


# ─── Alert detection engine ───────────────────────────────────────────────────
def compute_atr(candles: List[dict], period: int = 14) -> float:
    """Average True Range."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period


def detect_spike(candles: List[dict], threshold_pct: float) -> Optional[dict]:
    """Detect if the last closed candle had a spike > threshold %."""
    if len(candles) < 2:
        return None
    last = candles[-2]  # last CLOSED candle (current may be forming)
    move_pct = abs(last["close"] - last["open"]) / last["open"] * 100
    direction = "🟢 РОСТ" if last["close"] > last["open"] else "🔴 ПАДЕНИЕ"
    if move_pct >= threshold_pct:
        return {
            "type": "SPIKE",
            "move_pct": round(move_pct, 2),
            "direction": direction,
            "open": last["open"],
            "close": last["close"],
            "high": last["high"],
            "low": last["low"],
            "time": last["time"],
        }
    return None


def detect_consolidation(candles: List[dict], bars: int, atr_mult: float) -> Optional[dict]:
    """Detect consolidation: last N candles all had range < atr_mult * ATR(14)."""
    if len(candles) < bars + 14:
        return None
    atr = compute_atr(candles[:-bars], period=14)
    if atr == 0:
        return None
    threshold = atr * atr_mult
    window = candles[-bars - 1 : -1]  # last N closed candles
    narrow = [c for c in window if (c["high"] - c["low"]) < threshold]
    if len(narrow) >= bars:
        avg_close = sum(c["close"] for c in window) / len(window)
        range_pct = (max(c["high"] for c in window) - min(c["low"] for c in window)) / avg_close * 100
        return {
            "type": "CONSOLIDATION",
            "bars": bars,
            "range_pct": round(range_pct, 2),
            "atr": round(atr, 6),
            "avg_price": round(avg_close, 6),
            "time": window[-1]["time"],
        }
    return None


# ─── Telegram sender ──────────────────────────────────────────────────────────
class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.api = f"https://api.telegram.org/bot{token}"
        self._session: Optional[aiohttp.ClientSession] = None
        self.offset = 0

    async def get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, chat_id: int, text: str, parse_mode: str = "HTML"):
        session = await self.get_session()
        url = f"{self.api}/sendMessage"
        try:
            async with session.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"Send error: {e}")

    async def get_updates(self) -> List[dict]:
        session = await self.get_session()
        url = f"{self.api}/getUpdates"
        try:
            async with session.get(url, params={
                "offset": self.offset + 1,
                "timeout": 20,
                "allowed_updates": ["message", "callback_query"]
            }, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
                return data.get("result", [])
        except Exception as e:
            logger.error(f"Poll error: {e}")
            return []

    async def close(self):
        if self._session:
            await self._session.close()


# ─── Message formatting ────────────────────────────────────────────────────────
def format_spike_alert(symbol: str, tf: str, alert: dict) -> str:
    dt = datetime.utcfromtimestamp(alert["time"]).strftime("%H:%M UTC")
    return (
        f"⚡ <b>АНОМАЛЬНОЕ ДВИЖЕНИЕ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol}</b>  [{tf}]  {dt}\n"
        f"{alert['direction']}  <b>{alert['move_pct']}%</b>\n\n"
        f"Open:  <code>{alert['open']}</code>\n"
        f"Close: <code>{alert['close']}</code>\n"
        f"High:  <code>{alert['high']}</code>\n"
        f"Low:   <code>{alert['low']}</code>\n"
        f"\n<a href='https://www.tradingview.com/chart/?symbol={symbol}'>📈 Открыть в TradingView</a>"
    )


def format_consolidation_alert(symbol: str, tf: str, alert: dict) -> str:
    dt = datetime.utcfromtimestamp(alert["time"]).strftime("%H:%M UTC")
    return (
        f"🔷 <b>КОНСОЛИДАЦИЯ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol}</b>  [{tf}]  {dt}\n"
        f"Баров в зоне:  <b>{alert['bars']}</b>\n"
        f"Диапазон:      <b>{alert['range_pct']}%</b>\n"
        f"Цена:          <code>{alert['avg_price']}</code>\n"
        f"ATR(14):       <code>{alert['atr']}</code>\n"
        f"\n⚠️ Возможен сильный выход из диапазона!\n"
        f"\n<a href='https://www.tradingview.com/chart/?symbol={symbol}'>📈 Открыть в TradingView</a>"
    )


def help_text() -> str:
    return (
        "🤖 <b>Trading Alerts Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Команды:</b>\n"
        "/watch <code>BTCUSDT 1h</code> — добавить актив\n"
        "/unwatch <code>BTCUSDT</code> — удалить актив\n"
        "/list — список отслеживаемых активов\n"
        "/set <code>BTCUSDT spike 5</code> — порог спайка 5%\n"
        "/set <code>BTCUSDT bars 15</code> — 15 баров для консолидации\n"
        "/pause — приостановить все алерты\n"
        "/resume — возобновить алерты\n"
        "/status — статус бота\n"
        "/help — эта справка\n\n"
        "<b>Таймфреймы:</b> 1m, 5m, 15m, 1h, 4h, 1d\n\n"
        "<b>Примеры активов:</b>\n"
        "BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT\n"
        "EURUSD (форекс через FX:EURUSD)\n\n"
        "📡 Данные: Binance Futures"
    )


def list_text(user: UserState) -> str:
    if not user.assets:
        return "📭 Нет отслеживаемых активов.\nДобавьте через /watch BTCUSDT 1h"
    lines = ["📋 <b>Отслеживаемые активы:</b>\n"]
    for key, a in user.assets.items():
        alerts = []
        if a.alert_spike:
            alerts.append(f"⚡спайк>{a.spike_threshold}%")
        if a.alert_consolidation:
            alerts.append(f"🔷конс.{a.consolidation_bars}бар")
        lines.append(
            f"• <b>{a.symbol}</b> [{a.timeframe}]\n"
            f"  {' | '.join(alerts) if alerts else 'нет алертов'}"
        )
    return "\n".join(lines)


# ─── Command handlers ─────────────────────────────────────────────────────────
async def handle_command(text: str, chat_id: int, state: StateManager, bot: TelegramBot):
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].lower().split("@")[0]
    user = state.get_user(chat_id)

    if cmd == "/start" or cmd == "/help":
        await bot.send(chat_id, help_text())

    elif cmd == "/watch":
        if len(parts) < 3:
            await bot.send(chat_id, "❌ Использование: /watch BTCUSDT 1h")
            return
        symbol = parts[1].upper()
        tf = parts[2].lower()
        if tf not in TIMEFRAMES:
            await bot.send(chat_id, f"❌ Неверный таймфрейм. Доступны: {', '.join(TIMEFRAMES)}")
            return
        key = f"{symbol}_{tf}"
        user.assets[key] = WatchedAsset(symbol=symbol, timeframe=tf)
        state.save()
        await bot.send(chat_id, f"✅ Добавлен: <b>{symbol}</b> [{tf}]\n⚡ Спайк > {DEFAULT_SPIKE_THRESHOLD}%\n🔷 Консолидация {DEFAULT_CONSOLIDATION_BARS} баров")

    elif cmd == "/unwatch":
        if len(parts) < 2:
            await bot.send(chat_id, "❌ Использование: /unwatch BTCUSDT или /unwatch BTCUSDT_1h")
            return
        query = parts[1].upper()
        removed = [k for k in list(user.assets.keys()) if k.startswith(query)]
        for k in removed:
            del user.assets[k]
        state.save()
        if removed:
            await bot.send(chat_id, f"🗑 Удалено: {', '.join(removed)}")
        else:
            await bot.send(chat_id, "❌ Актив не найден")

    elif cmd == "/list":
        await bot.send(chat_id, list_text(user))

    elif cmd == "/set":
        # /set BTCUSDT spike 5  OR  /set BTCUSDT bars 15
        if len(parts) < 4:
            await bot.send(chat_id, "❌ Использование:\n/set BTCUSDT spike 5\n/set BTCUSDT bars 15")
            return
        symbol = parts[1].upper()
        param = parts[2].lower()
        try:
            value = float(parts[3])
        except ValueError:
            await bot.send(chat_id, "❌ Значение должно быть числом")
            return
        matched = [k for k in user.assets if k.startswith(symbol)]
        if not matched:
            await bot.send(chat_id, f"❌ {symbol} не найден в списке")
            return
        for k in matched:
            if param == "spike":
                user.assets[k].spike_threshold = value
            elif param == "bars":
                user.assets[k].consolidation_bars = int(value)
            elif param == "cooldown":
                user.assets[k].cooldown_sec = int(value)
        state.save()
        await bot.send(chat_id, f"✅ Обновлено для {symbol}: {param} = {value}")

    elif cmd == "/pause":
        user.active = False
        state.save()
        await bot.send(chat_id, "⏸ Алерты приостановлены. /resume — возобновить")

    elif cmd == "/resume":
        user.active = True
        state.save()
        await bot.send(chat_id, "▶️ Алерты возобновлены!")

    elif cmd == "/status":
        total = len(user.assets)
        status = "✅ Активен" if user.active else "⏸ Приостановлен"
        await bot.send(chat_id, f"🤖 Статус: {status}\n📊 Активов: {total}")

    else:
        await bot.send(chat_id, "❓ Неизвестная команда. /help — справка")


# ─── Alert monitoring loop ────────────────────────────────────────────────────
async def monitor_loop(state: StateManager, bot: TelegramBot, market: MarketData):
    logger.info("Monitor loop started")
    while True:
        try:
            for user in state.all_users():
                if not user.active:
                    continue
                for key, asset in list(user.assets.items()):
                    now = time.time()
                    # respect cooldown
                    if now - asset.last_alerted < asset.cooldown_sec:
                        continue

                    candles = await market.get_klines(asset.symbol, asset.timeframe, limit=60)
                    if not candles:
                        continue

                    alerted = False

                    if asset.alert_spike:
                        spike = detect_spike(candles, asset.spike_threshold)
                        if spike:
                            msg = format_spike_alert(asset.symbol, asset.timeframe, spike)
                            await bot.send(user.chat_id, msg)
                            alerted = True
                            logger.info(f"SPIKE alert: {key} for user {user.chat_id}")

                    if asset.alert_consolidation and not alerted:
                        cons = detect_consolidation(
                            candles,
                            asset.consolidation_bars,
                            DEFAULT_CONSOLIDATION_ATR_MULT
                        )
                        if cons:
                            msg = format_consolidation_alert(asset.symbol, asset.timeframe, cons)
                            await bot.send(user.chat_id, msg)
                            alerted = True
                            logger.info(f"CONSOLIDATION alert: {key} for user {user.chat_id}")

                    if alerted:
                        asset.last_alerted = now
                        state.save()

                    await asyncio.sleep(0.3)  # rate limit

        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        await asyncio.sleep(DEFAULT_CHECK_INTERVAL)


# ─── Polling loop ─────────────────────────────────────────────────────────────
async def polling_loop(state: StateManager, bot: TelegramBot):
    logger.info("Polling loop started")
    while True:
        try:
            updates = await bot.get_updates()
            for upd in updates:
                bot.offset = upd["update_id"]
                msg = upd.get("message") or upd.get("edited_message")
                if msg and "text" in msg:
                    chat_id = msg["chat"]["id"]
                    text = msg["text"]
                    await handle_command(text, chat_id, state, bot)
        except Exception as e:
            logger.error(f"Polling error: {e}")
        await asyncio.sleep(1)


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    logger.info("Starting Trading Alerts Bot...")
    state = StateManager(DATA_FILE)
    bot = TelegramBot(BOT_TOKEN)
    market = MarketData()

    try:
        await asyncio.gather(
            polling_loop(state, bot),
            monitor_loop(state, bot, market),
        )
    finally:
        await bot.close()
        await market.close()


if __name__ == "__main__":
    asyncio.run(main())
