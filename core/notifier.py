import os
import json
import logging
import asyncio
import re
from datetime import datetime, timezone
from telegram import Bot
from telegram.constants import ParseMode
from dotenv import load_dotenv

logger = logging.getLogger("quant_bot.notifier")
FAILED_MSG_FILE = "failed_messages.json"

def safe_html_truncate(html_text: str, max_len: int = 1024) -> str:
    """Safely truncates an HTML string and closes any open tags."""
    if not html_text or len(html_text) <= max_len:
        return html_text
    
    sliced = html_text[:max_len - 3]
    sliced = re.sub(r'<[^>]*$', '', sliced)
    
    stack = []
    for tag_match in re.finditer(r'</?([a-zA-Z]+)[^>]*>', sliced):
        tag = tag_match.group(0)
        tag_name = tag_match.group(1).lower()
        if tag.startswith('</'):
            if stack and stack[-1] == tag_name:
                stack.pop()
        else:
            stack.append(tag_name)
            
    suffix = "..."
    for tag_name in reversed(stack):
        suffix += f"</{tag_name}>"
        
    return sliced + suffix

class NotificationService:
    def __init__(self):
        load_dotenv()
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.watch_bot_token = os.getenv("TELEGRAM_WATCH_BOT_TOKEN")
        self.system_bot_token = os.getenv("TELEGRAM_SYSTEM_BOT_TOKEN")
        
        chat_id_env = os.getenv("TELEGRAM_CHAT_ID", "")
        self.chat_ids = list(set(cid.strip() for cid in chat_id_env.split(",") if cid.strip()))
        
        watch_chat_id_env = os.getenv("TELEGRAM_WATCH_CHAT_ID", "")
        self.watch_chat_ids = list(set(cid.strip() for cid in watch_chat_id_env.split(",") if cid.strip()))

        system_chat_id_env = os.getenv("TELEGRAM_SYSTEM_CHAT_ID", "")
        self.system_chat_ids = list(set(cid.strip() for cid in system_chat_id_env.split(",") if cid.strip()))
        
        self.bot = Bot(token=self.bot_token) if self.bot_token else None
        self.watch_bot = Bot(token=self.watch_bot_token) if self.watch_bot_token else None
        
        # System bot fallback to main bot
        if self.system_bot_token:
            self.system_bot = Bot(token=self.system_bot_token)
        else:
            self.system_bot = self.bot
            self.system_chat_ids = self.chat_ids

    @staticmethod
    def clean_html(msg):
        for tag in ["<b>", "</b>", "<code>", "</code>", "<i>", "</i>", "<pre>", "</pre>"]:
            msg = msg.replace(tag, "")
        return msg

    @staticmethod
    def chunk_text(text, limit=4000):
        if len(text) <= limit:
            return [text]
        chunks = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            split_idx = text.rfind('\n', 0, limit)
            if split_idx <= 0:
                split_idx = limit
                chunks.append(text[:split_idx])
                text = text[split_idx:]
            else:
                chunks.append(text[:split_idx])
                text = text[split_idx + 1:]
        return chunks

    async def send_message(self, message, is_watch=False, is_system=False, max_retries=3):
        if is_watch:
            target_bot = self.watch_bot
            target_chat_ids = self.watch_chat_ids
            bot_type = "watch"
        elif is_system:
            target_bot = self.system_bot
            target_chat_ids = self.system_chat_ids
            bot_type = "system"
        else:
            target_bot = self.bot
            target_chat_ids = self.chat_ids
            bot_type = "main"

        if not target_bot or not target_chat_ids:
            clean_msg = self.clean_html(message)
            logger.info(f"[Console Fallback] {clean_msg}")
            return

        chunks = self.chunk_text(message)

        for chat_id in target_chat_ids:
            for i, chunk in enumerate(chunks):
                sent = False
                for attempt in range(max_retries):
                    try:
                        await asyncio.wait_for(
                            target_bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML),
                            timeout=15.0
                        )
                        logger.info(f"[Telegram] Mesaj gönderildi: chat_id={chat_id} (Chunk {i+1}/{len(chunks)})")
                        sent = True
                        break
                    except Exception as e:
                        err_str = str(e).lower()
                        if "can't parse" in err_str or "parse" in err_str or "entity" in err_str or "tag" in err_str:
                            try:
                                logger.warning(f"[Telegram] HTML parse hatası, düz metin deneniyor: {e}")
                                await asyncio.wait_for(
                                    target_bot.send_message(chat_id=chat_id, text=self.clean_html(chunk), parse_mode=None),
                                    timeout=15.0
                                )
                                logger.info(f"[Telegram] Mesaj (düz metin) gönderildi: chat_id={chat_id} (Chunk {i+1}/{len(chunks)})")
                                sent = True
                                break
                            except Exception as e_inner:
                                logger.error(f"[Telegram] Düz metin denemesi de başarısız: {e_inner}")
                        
                        logger.error(f"[Telegram] Deneme {attempt+1}/{max_retries} başarısız (chat_id={chat_id}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(5 * (attempt + 1))
                
                if not sent:
                    await asyncio.to_thread(self._save_failed_message, chat_id, chunk, bot_type)

    async def send_photo(self, photo_path, caption=None, is_watch=False, is_system=False, max_retries=3):
        if is_watch:
            target_bot = self.watch_bot
            target_chat_ids = self.watch_chat_ids
            bot_type = "watch"
        elif is_system:
            target_bot = self.system_bot
            target_chat_ids = self.system_chat_ids
            bot_type = "system"
        else:
            target_bot = self.bot
            target_chat_ids = self.chat_ids
            bot_type = "main"

        if not target_bot or not target_chat_ids:
            logger.info(f"[Console Fallback Photo] {photo_path} - Caption: {caption}")
            return False

        for chat_id in target_chat_ids:
            sent = False
            for attempt in range(max_retries):
                try:
                    with open(photo_path, 'rb') as photo:
                        truncated_caption = caption
                        if truncated_caption and len(truncated_caption) > 1024:
                            truncated_caption = safe_html_truncate(truncated_caption, 1024)
                        await asyncio.wait_for(
                            target_bot.send_photo(chat_id=chat_id, photo=photo, caption=truncated_caption, parse_mode=ParseMode.HTML),
                            timeout=25.0
                        )
                    logger.info(f"[Telegram] Fotoğraf başarıyla gönderildi: chat_id={chat_id}")
                    sent = True
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    if "can't parse" in err_str or "parse" in err_str or "entity" in err_str or "tag" in err_str:
                        try:
                            logger.warning(f"[Telegram] Fotoğraf altyazısında HTML parse hatası, düz metin deneniyor: {e}")
                            clean_caption = self.clean_html(caption) if caption else None
                            if clean_caption and len(clean_caption) > 1024:
                                clean_caption = clean_caption[:1021] + "..."
                            with open(photo_path, 'rb') as photo:
                                await asyncio.wait_for(
                                    target_bot.send_photo(chat_id=chat_id, photo=photo, caption=clean_caption, parse_mode=None),
                                    timeout=25.0
                                )
                            logger.info(f"[Telegram] Fotoğraf (düz metin altyazı) gönderildi: chat_id={chat_id}")
                            sent = True
                            break
                        except Exception as e_inner:
                            logger.error(f"[Telegram] Fotoğraf düz metin altyazı denemesi de başarısız: {e_inner}")

                    logger.error(f"[Telegram] Fotoğraf gönderimi deneme {attempt+1}/{max_retries} başarısız (chat_id={chat_id}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(5 * (attempt + 1))
            if not sent:
                if caption:
                    await self.send_message(caption, is_watch=is_watch, is_system=is_system, max_retries=max_retries)
        return True

    def _save_failed_message(self, chat_id, message, bot_type="main"):
        failed = []
        if os.path.exists(FAILED_MSG_FILE):
            try:
                with open(FAILED_MSG_FILE, 'r', encoding='utf-8') as f:
                    failed = json.load(f)
            except Exception as e:
                logger.warning(f"Yedek dosya okunamadı: {e}")
        
        failed.append({
            "chat_id": chat_id,
            "message": message,
            "bot_type": bot_type,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        try:
            with open(FAILED_MSG_FILE, 'w', encoding='utf-8') as f:
                json.dump(failed, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Yedekleme hatası: {e}")

    async def retry_failed_messages(self):
        if not os.path.exists(FAILED_MSG_FILE):
            return
            
        try:
            def read_failed():
                with open(FAILED_MSG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            failed = await asyncio.to_thread(read_failed)
        except Exception as e:
            logger.error(f"Failed messages dosya hatası: {e}")
            return

        if not failed:
            return

        remaining = []
        for item in failed:
            chat_id = item["chat_id"]
            bot_type = item.get("bot_type", "main")
            
            if bot_type == "watch":
                target_bot = self.watch_bot
            elif bot_type == "system":
                target_bot = self.system_bot
            else:
                target_bot = self.bot
                
            if not target_bot:
                remaining.append(item)
                continue

            raw_text = f"⏰ <i>Gecikmeli mesaj ({item['timestamp'][:16]}):</i>\n\n{item['message']}"
            chunks = self.chunk_text(raw_text)
            
            for chunk in chunks:
                sent = False
                try:
                    await asyncio.wait_for(
                        target_bot.send_message(
                            chat_id=chat_id,
                            text=chunk,
                            parse_mode=ParseMode.HTML
                        ),
                        timeout=15.0
                    )
                    sent = True
                except Exception as e:
                    err_str = str(e).lower()
                    if "can't parse" in err_str or "parse" in err_str or "entity" in err_str or "tag" in err_str:
                        try:
                            logger.warning(f"[Telegram Retry] HTML parse hatası, düz metin deneniyor: {e}")
                            await asyncio.wait_for(
                                target_bot.send_message(
                                    chat_id=chat_id,
                                    text=self.clean_html(chunk),
                                    parse_mode=None
                                ),
                                timeout=15.0
                            )
                            sent = True
                        except Exception as e_inner:
                            logger.error(f"[Telegram Retry] Düz metin denemesi de başarısız: {e_inner}")
                    
                    if not sent:
                        logger.warning(f"Retry başarısız chat_id={chat_id}: {e}")
                        remaining.append({
                            "chat_id": chat_id,
                            "message": self.clean_html(chunk),
                            "bot_type": bot_type,
                            "timestamp": item["timestamp"]
                        })

        def write_remaining():
            with open(FAILED_MSG_FILE, 'w', encoding='utf-8') as f:
                json.dump(remaining, f, indent=2, ensure_ascii=False)
        await asyncio.to_thread(write_remaining)

    @staticmethod
    def format_price(price: float) -> str:
        if price is None:
            return "0"
        try:
            price_val = float(price)
        except (ValueError, TypeError):
            return str(price)
        if price_val == 0:
            return "0"
        abs_price = abs(price_val)
        if abs_price >= 100:
            return f"{price_val:.2f}"
        elif abs_price >= 1:
            return f"{price_val:.4f}"
        elif abs_price >= 0.0001:
            return f"{price_val:.6f}"
        else:
            return f"{price_val:.8f}"

    @classmethod
    def format_signal_message(cls, trade_data):
        market = trade_data.get("market", "KRİPTO")
        strategy = trade_data.get("strategy", "BİLİNMİYOR")
        signal_dir = trade_data.get('signal', 'AL')
        dir_text = "LONG" if signal_dir == "AL" else "SHORT"
        
        headers = {
            "BIST": f"📈 BIST {dir_text}",
            "EMTİA": f"⛏️ EMTİA {dir_text}",
            "AYI_AVCISI": "🐻 AYI AVCISI SHORT"
        }
        header = headers.get(market, f"🚀 KRİPTO {dir_text}")
        
        try:
            entry_price = float(trade_data.get('entry_price', 0))
        except (ValueError, TypeError):
            entry_price = 0.0
            
        sl_price = trade_data.get('sl', 0)
        tp_price = trade_data.get('tp', 0)

        rr_ratio = trade_data.get('rr_ratio')
        try:
            rr_val = float(rr_ratio) if rr_ratio is not None else 0.0
            rr_line = f"⚖️ R:R: {rr_val:.1f} | " if rr_val > 0 else ""
        except (ValueError, TypeError):
            rr_line = ""

        conv_score = trade_data.get('conviction_score')
        conv_line = ""
        if conv_score is not None:
            conv_grade = trade_data.get('conviction_grade', 'N/A')
            conv_pos = trade_data.get('position_size_pct', 100)
            conv_emoji = {"STRONG": "🟢", "MEDIUM": "🟡", "WATCH": "🟠"}.get(conv_grade, "⚪")
            conv_line = f"{conv_emoji} Skor: {conv_score:.0f} | Poz: %{conv_pos}\n"

        ttl_pct = 0.015
        if signal_dir == "AL":
            ttl_ceiling = entry_price * (1 + ttl_pct)
            ttl_floor = entry_price * (1 - ttl_pct)
            ttl_line = f"\n⏰ TTL: >{cls.format_price(ttl_ceiling)} İPTAL | <{cls.format_price(ttl_floor)} RİSKLİ"
        else:
            ttl_floor = entry_price * (1 - ttl_pct)
            ttl_ceiling = entry_price * (1 + ttl_pct)
            ttl_line = f"\n⏰ TTL: <{cls.format_price(ttl_floor)} İPTAL | >{cls.format_price(ttl_ceiling)} RİSKLİ"

        import html
        escaped_strategy = html.escape(strategy)
        escaped_ticker = html.escape(trade_data.get('ticker', 'Bilinmiyor'))
        escaped_reason = html.escape(trade_data.get('reason', 'Sebep belirtilmemiş.'))

        return (
            f"<b>{header} | {escaped_ticker}</b>\n"
            f"🎯 <code>{cls.format_price(entry_price)}</code> | 🛑 <code>{cls.format_price(sl_price)}</code> | 📈 <code>{cls.format_price(tp_price)}</code>\n"
            f"{rr_line}{conv_line}"
            f"<i>{escaped_strategy} - {escaped_reason}</i>{ttl_line}"
        )
