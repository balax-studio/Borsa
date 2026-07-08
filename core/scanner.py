import time
import json
import logging
import asyncio
from datetime import datetime, timezone
import tempfile
import os

from data_fetcher import scan_all_markets
from trade_tracker import load_trades
from circuit_breaker import is_circuit_open
from penalty_box import is_asset_penalized, is_daily_commission_exceeded, record_trade_commission
from strategy_scorecard import is_strategy_disabled
from quarantine import is_quarantined
from signal_decay import should_block_entry

logger = logging.getLogger("quant_bot.scanner")

async def get_chart_for_signal(trade):
    market = trade.get("market", "KRİPTO")
    ticker = trade.get("ticker", "Bilinmiyor")
    entry_price = float(trade.get("entry_price", 0))
    sl = float(trade.get("sl", 0))
    tp = float(trade.get("tp", 0))
    signal_dir = trade.get("signal", "AL")
    
    df_4h = None
    try:
        if market in ["KRİPTO", "KRIPTO"]:
            from data_sources import async_get_crypto_data
            _, df_4h = await async_get_crypto_data(ticker)
        elif market == "BIST":
            from data_sources import get_bist_data
            _, df_4h, _ = await asyncio.to_thread(get_bist_data, ticker)
        elif market == "EMTİA":
            from data_sources import get_emtia_data
            _, df_4h = await asyncio.to_thread(get_emtia_data, ticker)
            
        if df_4h is not None and not df_4h.empty:
            from core.chart_generator import generate_signal_chart
            path = generate_signal_chart(ticker, df_4h, entry_price, sl, tp, signal_dir)
            return path, df_4h
    except Exception as e:
        logger.error(f"Grafik verisi alınamadı: {e}")
    return "", None

class ScannerService:
    COOLDOWN_FILE = "signal_cooldown.json"
    COOLDOWN_SECONDS = 3600

    def __init__(self, notifier, trade_engine):
        self.notifier = notifier
        self.trade_engine = trade_engine
        self.signal_cooldown = self._load_cooldown()

    def _load_cooldown(self):
        if os.path.exists(self.COOLDOWN_FILE):
            try:
                with open(self.COOLDOWN_FILE, 'r') as f:
                    data = json.load(f)
                    return {tuple(k.split("|")): v for k, v in data.items()}
            except Exception as e:
                logger.warning(f"Cooldown yüklenemedi: {e}")
        return {}

    def _save_cooldown(self):
        try:
            data = {f"{k[0]}|{k[1]}": v for k, v in self.signal_cooldown.items()}
            tmp_fd, tmp_path = tempfile.mkstemp(dir='.', suffix='.tmp')
            with os.fdopen(tmp_fd, 'w') as f:
                json.dump(data, f)
            os.replace(tmp_path, self.COOLDOWN_FILE)
        except Exception as e:
            logger.error(f"Cooldown kaydedilemedi: {e}")

    def _cleanup_cooldown(self):
        now = time.time()
        self.signal_cooldown = {k: v for k, v in self.signal_cooldown.items() if now - v < self.COOLDOWN_SECONDS}

    def _save_scan_metrics(self, metrics):
        if not metrics:
            return
        def convert_numpy(obj):
            import numpy as np
            import pandas as pd
            if isinstance(obj, (np.integer, np.int64, np.int32)):
                return int(obj)
            elif isinstance(obj, (np.floating, np.float64, np.float32)):
                return float(obj)
            elif isinstance(obj, (np.ndarray, pd.Series)):
                return obj.tolist()
            elif isinstance(obj, pd.DataFrame):
                return obj.to_dict()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        try:
            with open('last_scan_metrics.json', 'w', encoding='utf-8') as f:
                json.dump(metrics, f, ensure_ascii=False, indent=2, default=convert_numpy)
        except Exception as e:
            logger.warning(f"Metrics kaydedilemedi: {e}")

    async def run_scan(self):
        logger.info("Hibrit piyasa taraması başlatılıyor (BIST & Kripto)...")
        self._cleanup_cooldown()

        scan_start = time.time()
        signals, scan_metrics = await scan_all_markets()
        logger.info(f"Tarama süresi: {time.time() - scan_start:.1f}s")

        await asyncio.to_thread(self._save_scan_metrics, scan_metrics)
        import gc
        gc.collect()

        if not signals:
            return

        logger.info(f"Sistem toplam {len(signals)} adet işlem kararı üretti!")

        if is_daily_commission_exceeded():
            logger.warning("Günlük komisyon limiti aşıldı.")
            await self.notifier.send_message("🥊 <b>GÜNLÜK KOMİSYON LİMİTİ AŞILDI</b>\nBugün için tüm yeni sinyal üretimi durduruldu.", is_system=True)
            return

        await self._process_signals(signals)

    async def _process_signals(self, signals):
        signals = sorted(signals, key=lambda x: x.get("conviction_score", 0) if x.get("conviction_score") is not None else 0, reverse=True)
        main_signal_count = 0
        
        for decision in signals:
            ticker = decision.get("ticker", "Bilinmiyor")
            strategy = decision.get("strategy", "")
            
            if self._is_on_cooldown(ticker, strategy):
                logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Cooldown aktif.")
                continue
            active_trade = self._get_active_trade(ticker)
            if active_trade:
                new_score = decision.get("conviction_score")
                old_score = active_trade.get("conviction_score")
                new_grade = decision.get("conviction_grade")
                old_grade = active_trade.get("conviction_grade")
                
                # Sadece sınıf (grade) değiştiyse güncelle (sürekli bildirim gelmemesi için Seçenek 1)
                should_update = False
                if new_score is not None and old_score is not None:
                    if new_grade != old_grade:
                        should_update = True
                
                if should_update:
                    await self._update_active_trade_conviction(active_trade.get("id"), new_score, decision)
                else:
                    logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Zaten aktif pozisyon var (Mevcut Skor: {old_score}, Sınıf: {old_grade} | Yeni Sinyal Skoru: {new_score}, Sınıf: {new_grade}).")
                continue
            
            if is_circuit_open(ticker, strategy):
                logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Devre kesici açık.")
                continue
            if is_asset_penalized(ticker):
                logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Varlık ceza kutusunda.")
                continue
            if is_strategy_disabled(strategy):
                logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Strateji devre dışı.")
                continue
            if is_quarantined(ticker):
                logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Varlık karantinada.")
                continue

            entry_price = float(decision.get("entry_price") or 0.0)
            sl_price = float(decision.get("sl") or 0.0)
            tp_price = float(decision.get("tp") or 0.0)
            
            if entry_price > 0 and sl_price > 0 and tp_price > 0:
                should_block, block_reason = should_block_entry({
                    "ticker": ticker, "entry_price": entry_price, "sl": sl_price, "tp": tp_price,
                    "signal": decision.get("signal", "AL"), "signal_time": datetime.now(timezone.utc).isoformat(),
                    "market": decision.get("market", "KRİPTO")
                }, entry_price)
                if should_block:
                    logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Signal Decay: {block_reason}")
                    continue

            conv_grade = decision.get("conviction_grade", "N/A")
            is_watch = (conv_grade == 'WATCH')

            if not is_watch:
                if main_signal_count >= 3:
                    logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Ana bot sinyal limiti (3) aşıldı.")
                    continue
                main_signal_count += 1

            trade = self.trade_engine.add_trade(
                ticker=ticker,
                signal=decision.get("signal", "AL"),
                entry_price=entry_price,
                sl=sl_price,
                tp=tp_price,
                reason=decision.get("reason", "Neden belirtilmedi"),
                provider="Algorithm",
                strategy=strategy,
                indicators=decision.get("indicators", {}),
                is_watch=is_watch,
                market=decision.get("market", "KRİPTO"),
                conviction_score=decision.get("conviction_score"),
                conviction_grade=conv_grade,
                position_size_pct=decision.get("position_size_pct", 100),
                raw_indicators=decision.get("raw_indicators", {}),
                conviction_details=decision.get("conviction_details", {})
            )

            if trade is None:
                logger.info(f"[Scanner] Atlandı: {ticker} ({strategy}) - Trade Engine işlemi kaydetmedi.")
                continue

            if not is_watch:
                await asyncio.to_thread(record_trade_commission, ticker)

            if decision.get("is_day_trade"):
                self._mark_as_day_trade(trade["id"])

            msg = self.notifier.format_signal_message(trade)
            
            # Grafik üretimi ve gönderimi
            chart_path = ""
            df_4h = None
            entry_price = float(trade.get("entry_price", 0))
            if entry_price > 0:
                chart_path, df_4h = await get_chart_for_signal(trade)
            
            if is_watch:
                watch_header = '👁️ <b>WATCH LIST</b>\n'
                if chart_path and os.path.exists(chart_path):
                    try:
                        await self.notifier.send_photo(chart_path, caption=watch_header + msg, is_watch=True)
                    finally:
                        try:
                            os.remove(chart_path)
                        except Exception as e:
                            logger.warning(f"Geçici grafik dosyası silinemedi: {e}")
                else:
                    await self.notifier.send_message(watch_header + msg, is_watch=True)
            else:
                if chart_path and os.path.exists(chart_path):
                    try:
                        await self.notifier.send_photo(chart_path, caption=msg)
                        
                        if trade.get("market", "") in ["KRİPTO", "KRIPTO"]:
                            from core.ai_commentary import get_ai_commentary
                            ai_comment = await get_ai_commentary(trade, chart_path=chart_path, df_4h=df_4h)
                            if ai_comment:
                                import html
                                escaped_ai_comment = html.escape(ai_comment)
                                formatted_ai_msg = f"🤖 <b>AI Analizi:</b>\n{escaped_ai_comment}"
                                await self.notifier.send_message(formatted_ai_msg)
                    finally:
                        try:
                            os.remove(chart_path)
                        except Exception as e:
                            logger.warning(f"Geçici grafik dosyası silinemedi: {e}")
                else:
                    await self.notifier.send_message(msg)
                    if trade.get("market", "") in ["KRİPTO", "KRIPTO"]:
                        from core.ai_commentary import get_ai_commentary
                        ai_comment = await get_ai_commentary(trade, chart_path=None, df_4h=df_4h)
                        if ai_comment:
                            import html
                            escaped_ai_comment = html.escape(ai_comment)
                            formatted_ai_msg = f"🤖 <b>AI Analizi:</b>\n{escaped_ai_comment}"
                            await self.notifier.send_message(formatted_ai_msg)
                    
                await self._set_cooldown(ticker, strategy)

    def _is_on_cooldown(self, ticker, strategy):
        key = (ticker, strategy)
        if key in self.signal_cooldown and (time.time() - self.signal_cooldown[key]) < self.COOLDOWN_SECONDS:
            return True
        return False

    async def _set_cooldown(self, ticker, strategy):
        self.signal_cooldown[(ticker, strategy)] = time.time()
        await asyncio.to_thread(self._save_cooldown)

    def _is_already_active(self, ticker):
        trades = load_trades()
        return any(t["ticker"] == ticker and t["status"] == "ACTIVE" for t in trades)

    def _mark_as_day_trade(self, trade_id):
        from trade_tracker import save_trades
        trades = load_trades()
        for t in trades:
            if t.get("id") == trade_id:
                t["is_day_trade"] = True
        save_trades(trades)

    def _get_active_trade(self, ticker):
        trades = load_trades()
        for t in trades:
            if t.get("ticker") == ticker and t.get("status") == "ACTIVE":
                return t
        return None

    async def _update_active_trade_conviction(self, trade_id, new_score, decision):
        from trade_tracker import save_trades
        trades = load_trades()
        updated_trade = None
        was_watch = False
        
        for t in trades:
            if t.get("id") == trade_id:
                was_watch = t.get("is_watch", False)
                new_grade = decision.get("conviction_grade", t.get("conviction_grade"))
                
                if was_watch and new_grade in ["MEDIUM", "STRONG"]:
                    t["is_watch"] = False
                    if "entry_price" in decision:
                        t["entry_price"] = decision.get("entry_price")
                    if "sl" in decision:
                        t["sl"] = decision.get("sl")
                    if "tp" in decision:
                        t["tp"] = decision.get("tp")
                    
                t["conviction_score"] = new_score
                t["conviction_grade"] = new_grade
                t["position_size_pct"] = decision.get("position_size_pct", t.get("position_size_pct"))
                t["conviction_details"] = decision.get("conviction_details", t.get("conviction_details", {}))
                t["reason"] = f"{t.get('reason')} | [GÜNCELLEME ({decision.get('strategy', 'Algoritma')})]: {decision.get('reason', '')}"
                updated_trade = t
                break
                
        if updated_trade:
            await asyncio.to_thread(save_trades, trades)
            entry_val = float(updated_trade.get("entry_price") or 0.0)
            sl_val = float(updated_trade.get("sl") or 0.0)
            tp_val = float(updated_trade.get("tp") or 0.0)
            msg = (
                f"🔄 <b>SKOR GÜNCELLEMESİ — {updated_trade.get('ticker')}</b>\n"
                f"{'🟢 LONG' if updated_trade.get('signal') == 'AL' else '🔴 SHORT'} | <b>{updated_trade.get('strategy')}</b>\n"
                f"<b>Yeni Skor:</b> <code>{new_score:.0f} ({updated_trade.get('conviction_grade')})</code> | <b>Poz:</b> %{updated_trade.get('position_size_pct')}\n"
                f"🎯 <b>Giriş:</b> <code>{entry_val:.4f}</code> | 🛑 <b>SL:</b> <code>{sl_val:.4f}</code> | 📈 <b>TP:</b> <code>{tp_val:.4f}</code>\n"
                f"<b>Gerekçe:</b> <i>{decision.get('reason', 'Neden belirtilmedi')}</i>"
            )
            
            if was_watch and not updated_trade.get("is_watch", False):
                msg = "🚀 <b>WATCH'TAN ANA BOTA GEÇİŞ!</b> 🚀\n" + msg
                await self.notifier.send_message(msg)
            elif updated_trade.get("is_watch", False):
                await self.notifier.send_message(msg, is_watch=True)
            else:
                await self.notifier.send_message(msg)
