import aiohttp
import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BINANCE_API = "https://api.binance.com/api/v3"

# Store last N price points per symbol for anomaly detection
price_history: dict[str, deque] = {}
volume_history: dict[str, deque] = {}
HISTORY_SIZE = 20  # ~20 minutes of data at 60s intervals


class CryptoMonitor:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self.session

    async def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol."""
        try:
            session = await self._get_session()
            async with session.get(f"{BINANCE_API}/ticker/price", params={"symbol": symbol}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data['price'])
                return None
        except Exception as e:
            logger.error(f"get_price error for {symbol}: {e}")
            return None

    async def get_full_stats(self, symbol: str) -> Optional[dict]:
        """Get 24h ticker stats."""
        try:
            session = await self._get_session()
            async with session.get(f"{BINANCE_API}/ticker/24hr", params={"symbol": symbol}) as resp:
                if resp.status == 200:
                    d = await resp.json()
                    return {
                        'price': float(d['lastPrice']),
                        'change_24h': float(d['priceChangePercent']),
                        'change_1h': await self._get_1h_change(symbol),
                        'volume_24h': float(d['quoteVolume']),
                        'high_24h': float(d['highPrice']),
                        'low_24h': float(d['lowPrice']),
                        'trades_24h': int(d['count']),
                    }
                return None
        except Exception as e:
            logger.error(f"get_full_stats error for {symbol}: {e}")
            return None

    async def _get_1h_change(self, symbol: str) -> float:
        """Calculate 1h price change from klines."""
        try:
            session = await self._get_session()
            params = {"symbol": symbol, "interval": "1h", "limit": 2}
            async with session.get(f"{BINANCE_API}/klines", params=params) as resp:
                if resp.status == 200:
                    klines = await resp.json()
                    if len(klines) >= 2:
                        open_price = float(klines[0][1])
                        close_price = float(klines[-1][4])
                        return ((close_price - open_price) / open_price) * 100
            return 0.0
        except Exception:
            return 0.0

    async def _get_5min_klines(self, symbol: str) -> list:
        """Get last 10 five-minute klines."""
        try:
            session = await self._get_session()
            params = {"symbol": symbol, "interval": "5m", "limit": 10}
            async with session.get(f"{BINANCE_API}/klines", params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
            return []
        except Exception:
            return []

    async def check_anomalies(
        self,
        symbol: str,
        name: str,
        price_threshold: float = 3.0,
        volume_multiplier: float = 3.0
    ) -> list[str]:
        """
        Check for anomalies and return list of alert messages.
        Detects:
          1. Sudden price spike/drop in last 5min
          2. Abnormal volume vs rolling average
          3. Sharp 1h move
        """
        alerts = []

        try:
            klines = await self._get_5min_klines(symbol)
            if len(klines) < 3:
                return alerts

            # ── Price anomaly (last 5-min candle) ──────────────────────────
            last = klines[-1]
            open_p  = float(last[1])
            close_p = float(last[4])
            high_p  = float(last[2])
            low_p   = float(last[3])
            volume  = float(last[5])   # base asset volume
            quote_vol = float(last[7]) # USDT volume

            if open_p > 0:
                pct_change = ((close_p - open_p) / open_p) * 100

                if abs(pct_change) >= price_threshold:
                    direction = "🚀 ПАМП" if pct_change > 0 else "💥 ДАМП"
                    icon = "📈" if pct_change > 0 else "📉"
                    alerts.append(
                        f"⚠️ *АЛЕРТ: {direction}* {icon}\n\n"
                        f"🪙 *{name}/USDT*\n"
                        f"💰 Цена: *${close_p:,.4f}*\n"
                        f"📊 Изменение за 5 мин: *{pct_change:+.2f}%*\n"
                        f"📈 Хай: ${high_p:,.4f} | 📉 Лоу: ${low_p:,.4f}\n"
                        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
                    )

            # ── Volume anomaly ───────────────────────────────────────────────
            prev_volumes = [float(k[7]) for k in klines[:-1]]  # quote vol
            if prev_volumes:
                avg_vol = sum(prev_volumes) / len(prev_volumes)
                if avg_vol > 0 and quote_vol >= avg_vol * volume_multiplier:
                    ratio = quote_vol / avg_vol
                    alerts.append(
                        f"📊 *АЛЕРТ: АНОМАЛЬНЫЙ ОБЪЁМ* 🔥\n\n"
                        f"🪙 *{name}/USDT*\n"
                        f"💰 Цена: *${close_p:,.4f}*\n"
                        f"📦 Объём: *${quote_vol:,.0f}*\n"
                        f"📏 В {ratio:.1f}× больше среднего (${avg_vol:,.0f})\n"
                        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
                    )

            # ── 1h sharp move ────────────────────────────────────────────────
            if len(klines) >= 12:  # 12 × 5min = 1h
                price_1h_ago = float(klines[-12][1])  # open of candle 1h ago
                if price_1h_ago > 0:
                    change_1h = ((close_p - price_1h_ago) / price_1h_ago) * 100
                    threshold_1h = price_threshold * 1.5  # stricter for 1h

                    if abs(change_1h) >= threshold_1h:
                        direction = "🚀" if change_1h > 0 else "💥"
                        alerts.append(
                            f"⏰ *АЛЕРТ: РЕЗКОЕ ДВИЖЕНИЕ ЗА 1 ЧАС* {direction}\n\n"
                            f"🪙 *{name}/USDT*\n"
                            f"💰 Цена: *${close_p:,.4f}*\n"
                            f"📊 Изменение за 1ч: *{change_1h:+.2f}%*\n"
                            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
                        )

        except Exception as e:
            logger.error(f"check_anomalies error for {symbol}: {e}")

        return alerts
