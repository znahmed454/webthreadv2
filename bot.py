"""
bot.py — Twitter Thread Generator Bot (Pro Edition)
Alur: URL → Twitter handle → Provider → Bahasa → Generate

Upgrade:
- Ingat preferensi user (provider, bahasa) antar sesi
- Rate limiting per user (maks 5 request/jam)
- Tampilkan media sources yang dipakai saat generate
- Status update lebih informatif (multi-media research)
- Validasi output ketat
"""

import os
import re
import time
import asyncio
import logging
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler,
    ConversationHandler,
)
from telegram.constants import ParseMode
from dotenv import load_dotenv
from thread_generator import ThreadGenerator
from ai_providers import get_available_providers, get_provider_status, PROVIDERS

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

generator = ThreadGenerator()

# ── Conversation states ──────────────────────────────────────────
WAITING_TWITTER = 1

# ── Rate limiter (in-memory, per user) ──────────────────────────
# Struktur: {user_id: [timestamp, timestamp, ...]}
RATE_LIMIT_WINDOW = 3600   # 1 jam
RATE_LIMIT_MAX    = 5      # maks 5 request per jam
_rate_log: dict[int, list[float]] = defaultdict(list)

def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """
    Cek apakah user masih dalam batas rate limit.
    Returns: (allowed: bool, remaining: int)
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    # Hapus entri lama
    _rate_log[user_id] = [t for t in _rate_log[user_id] if t > window_start]
    count = len(_rate_log[user_id])
    remaining = RATE_LIMIT_MAX - count
    return remaining > 0, remaining

def record_request(user_id: int):
    """Catat satu request untuk user ini."""
    _rate_log[user_id].append(time.time())

def get_reset_minutes(user_id: int) -> int:
    """Berapa menit lagi rate limit direset."""
    if not _rate_log[user_id]:
        return 0
    oldest = min(_rate_log[user_id])
    return max(0, int((oldest + RATE_LIMIT_WINDOW - time.time()) / 60))


# ── User preferences (in-memory) ────────────────────────────────
# Struktur: {user_id: {"provider": "groq", "lang": "english"}}
_user_prefs: dict[int, dict] = {}

def get_pref(user_id: int, key: str, default: str) -> str:
    return _user_prefs.get(user_id, {}).get(key, default)

def set_pref(user_id: int, key: str, value: str):
    if user_id not in _user_prefs:
        _user_prefs[user_id] = {}
    _user_prefs[user_id][key] = value


# ── Welcome ──────────────────────────────────────────────────────
def build_welcome() -> str:
    status = get_provider_status()
    provider_lines = []
    for pid, info in status.items():
        mark = "✅" if info["available"] else "❌"
        provider_lines.append(f"{mark} {info['label']}")
    providers_text = "\n".join(provider_lines)
    return f"""
🧵 *Twitter Thread Generator — Multi-AI Pro*

Riset mendalam *semua media publik project* — website, Twitter/X, GitHub, Telegram, Discord, Medium, Reddit, CoinGecko — lalu ditulis oleh AI pilihan kamu.

*AI Providers:*
{providers_text}

*Sumber riset:*
🌐 Website + sub-pages
🐦 Twitter/X (multi-method scraping)
🐙 GitHub (repo, README, stars)
📱 Telegram channel
💬 Discord server
📝 Medium / Substack
🔴 Reddit discussions
📊 CoinGecko (jika crypto)
🔍 Web search coverage

*Cara pakai:*
1️⃣ Kirim URL website project
2️⃣ Input Twitter/X handle (opsional)
3️⃣ Pilih AI provider
4️⃣ Pilih bahasa
5️⃣ Thread siap! 🔥

Mulai dengan kirim URL project kamu 👇
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(build_welcome(), parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
📖 *Panduan Bot Pro — Multi-Media Research*

*Commands:*
/start  — Mulai dari awal
/help   — Panduan ini
/cancel — Batalkan proses
/prefs  — Lihat preferensi tersimpan
/status — Status rate limit kamu

*Alur penggunaan:*
1. Kirim URL website project
2. Input Twitter handle (atau skip)
3. Pilih AI provider favorit
4. Pilih bahasa thread
5. Bot riset semua media (~45–90 detik)
6. Thread siap posting!

*Sumber riset yang dicek:*
• 🌐 Website utama + sub-pages (docs, whitepaper, roadmap)
• 🐦 Twitter/X — 3 metode scraping (Nitter → x.com → Web Search)
• 🐙 GitHub — repo, README, stars, activity
• 📱 Telegram — channel publik, pesan terbaru
• 💬 Discord — server info, member count
• 📝 Medium/Substack — artikel terbaru
• 🔴 Reddit — diskusi komunitas
• 📊 CoinGecko — data token (jika project crypto)
• 🔍 Web coverage — review & artikel media

*Rate limit:* 5 thread per jam per user.

*Tips:*
• Provider pilihanmu akan diingat untuk request berikutnya
• Sertakan Twitter handle untuk hasil terbaik
• Gunakan URL /docs atau /whitepaper jika website minim info
""", parse_mode=ParseMode.MARKDOWN)


async def prefs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    provider = get_pref(user_id, "provider", "groq")
    lang = get_pref(user_id, "lang", "english")
    lang_label = "🇬🇧 English" if lang == "english" else "🇮🇩 Bahasa Indonesia"
    provider_info = PROVIDERS.get(provider, {})
    await update.message.reply_text(
        f"⚙️ *Preferensi tersimpan:*\n\n"
        f"🤖 Provider: {provider_info.get('label', provider)}\n"
        f"🌐 Bahasa: {lang_label}\n\n"
        f"_Preferensi diupdate otomatis saat kamu pilih provider/bahasa._",
        parse_mode=ParseMode.MARKDOWN,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    allowed, remaining = check_rate_limit(user_id)
    reset_min = get_reset_minutes(user_id)
    used = RATE_LIMIT_MAX - remaining
    await update.message.reply_text(
        f"📊 *Rate Limit Status:*\n\n"
        f"Digunakan: {used}/{RATE_LIMIT_MAX} request (per jam)\n"
        f"Tersisa: {remaining} request\n"
        f"{'Reset dalam: ' + str(reset_min) + ' menit' if not allowed else '✅ Tidak ada cooldown'}\n",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Proses dibatalkan. Kirim URL baru untuk mulai lagi.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# ── Step 1: Terima URL ───────────────────────────────────────────
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Rate limit check
    allowed, remaining = check_rate_limit(user_id)
    if not allowed:
        reset_min = get_reset_minutes(user_id)
        await update.message.reply_text(
            f"⏳ *Rate limit tercapai.*\n\n"
            f"Kamu sudah generate {RATE_LIMIT_MAX}x dalam 1 jam terakhir.\n"
            f"Reset dalam ~{reset_min} menit.\n\n"
            f"_Gunakan /status untuk detail._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    url = update.message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text(
            "❌ URL tidak valid. Pastikan dimulai dengan `http://` atau `https://`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    context.user_data["url"] = url
    context.user_data["twitter_handle"] = ""

    keyboard = [
        [InlineKeyboardButton("✅ Skip, langsung pilih provider", callback_data="twitter:skip")],
        [InlineKeyboardButton("📝 Input Twitter handle manual", callback_data="twitter:manual")],
    ]

    await update.message.reply_text(
        f"✅ URL diterima!\n`{url}`\n\n"
        f"🐦 *Punya akun Twitter/X project ini?*\n\n"
        f"Twitter memperkaya thread dengan:\n"
        f"• Tone & voice asli project\n"
        f"• Announcements & proof points\n"
        f"• Community signals\n\n"
        f"Ketik handle (contoh: `@UniswapProtocol`) atau pilih skip:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAITING_TWITTER


# ── Step 2a: User ketik Twitter handle ──────────────────────────
async def receive_twitter_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    handle_match = re.search(r"@?([A-Za-z0-9_]{1,50})", text)
    if not handle_match:
        await update.message.reply_text(
            "❌ Handle tidak valid. Contoh: `@UniswapProtocol` atau `UniswapProtocol`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_TWITTER

    handle = handle_match.group(1)
    context.user_data["twitter_handle"] = handle
    await _ask_provider(update.message, context, handle)
    return ConversationHandler.END


# ── Step 2b: Callback inline keyboard ────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "twitter:skip":
        context.user_data["twitter_handle"] = ""
        await _ask_provider(query.message, context, "")
        return ConversationHandler.END

    elif data == "twitter:manual":
        await query.message.reply_text(
            "📝 Ketik akun Twitter/X project:\n\n"
            "Contoh: `@UniswapProtocol` atau `UniswapProtocol`\n"
            "Atau paste URL: `https://x.com/UniswapProtocol`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_TWITTER

    elif data.startswith("provider:"):
        provider = data.split(":")[1]
        context.user_data["provider"] = provider
        set_pref(user_id, "provider", provider)  # simpan preferensi
        handle = context.user_data.get("twitter_handle", "")
        await _ask_language(query.message, context, handle)

    elif data.startswith("lang:"):
        lang = data.split(":")[1]
        set_pref(user_id, "lang", lang)  # simpan preferensi
        url = context.user_data.get("url", "")
        twitter = context.user_data.get("twitter_handle", "")
        provider = context.user_data.get("provider", get_pref(user_id, "provider", "groq"))
        if not url:
            await query.message.reply_text("❌ URL hilang. Kirim ulang URL project.")
            return ConversationHandler.END
        await _run_generation(query.message, url, twitter, lang, provider, user_id)

    elif data.startswith("regen:"):
        parts = data.split(":", 4)
        _, lang, provider, twitter, url = parts
        await _run_generation(query.message, url, twitter, lang, provider, user_id)

    elif data.startswith("switch_provider:"):
        _, twitter, url = data.split(":", 2)
        context.user_data["url"] = url
        context.user_data["twitter_handle"] = twitter if twitter != "_" else ""
        await _ask_provider(query.message, context, twitter if twitter != "_" else "")


async def _ask_provider(message, context, handle: str):
    user_id = message.chat.id
    available = get_available_providers()
    status = get_provider_status()
    saved_provider = get_pref(user_id, "provider", "groq")

    keyboard = []
    for pid in ["groq", "deepseek", "openai"]:
        info = status[pid]
        if info["available"]:
            label = info["label"]
            if pid == saved_provider:
                label = f"✓ {label} (terakhir dipakai)"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"provider:{pid}")])

    if not keyboard:
        await message.reply_text("❌ Tidak ada API key yang dikonfigurasi.")
        return

    handle_info = f"@{handle}" if handle else "Website only"
    await message.reply_text(
        f"🤖 *Pilih AI Provider:*\n\n"
        f"🌐 URL: `{context.user_data.get('url','')}`\n"
        f"🐦 Twitter: `{handle_info}`\n\n"
        f"Setiap model punya karakter berbeda:\n"
        f"_(✓ = provider terakhir kamu pakai)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _ask_language(message, context, handle: str):
    user_id = message.chat.id
    saved_lang = get_pref(user_id, "lang", "english")
    handle_info = f"@{handle}" if handle else "skip (website only)"

    en_label = "🇬🇧 English" + (" ✓" if saved_lang == "english" else "")
    id_label = "🇮🇩 Bahasa Indonesia" + (" ✓" if saved_lang == "indonesia" else "")

    keyboard = [
        [InlineKeyboardButton(en_label, callback_data="lang:english"),
         InlineKeyboardButton(id_label, callback_data="lang:indonesia")]
    ]
    provider = context.user_data.get("provider", get_pref(user_id, "provider", "groq"))
    await message.reply_text(
        f"🌐 *Pilih bahasa thread:*\n\n"
        f"Provider: {PROVIDERS.get(provider, {}).get('label', '—')}\n"
        f"Twitter: {handle_info}\n\n"
        f"_(✓ = bahasa terakhir kamu pakai)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Step 3: Generate thread ──────────────────────────────────────
async def _run_generation(
    message, url: str, twitter_handle: str,
    lang: str, provider: str, user_id: int
):
    # Rate limit check sebelum mulai
    allowed, remaining = check_rate_limit(user_id)
    if not allowed:
        reset_min = get_reset_minutes(user_id)
        await message.reply_text(
            f"⏳ Rate limit. Reset dalam ~{reset_min} menit.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    record_request(user_id)  # catat request ini

    lang_label = "🇬🇧 English" if lang == "english" else "🇮🇩 Bahasa Indonesia"
    tw_label = f"@{twitter_handle}" if twitter_handle else "Website only"
    provider_cfg = PROVIDERS.get(provider, PROVIDERS["groq"])
    provider_label = provider_cfg["label"]

    steps = [
        "🌐 Scraping website & sub-pages...",
        "🐦 Riset Twitter/X (Nitter → x.com → Search)...",
        "🔍 Riset multi-media (GitHub, Telegram, Discord, Reddit, dll)...",
        f"🤖 {provider_cfg['name']} AI menulis thread...",
    ]

    loading = await message.reply_text(
        f"🔍 *Riset mendalam dimulai!*\n\n"
        f"📎 `{url}`\n"
        f"🐦 `{tw_label}`\n"
        f"🌐 {lang_label}\n"
        f"🤖 {provider_label}\n\n"
        + "\n".join(f"⏳ {s}" for s in steps) +
        "\n\n_Mohon tunggu 60–120 detik..._",
        parse_mode=ParseMode.MARKDOWN,
    )

    async def update_status(step_idx: int):
        lines = []
        for i, step in enumerate(steps):
            if i < step_idx:
                lines.append(f"✅ {step}")
            elif i == step_idx:
                lines.append(f"🔄 {step}")
            else:
                lines.append(f"⏳ {step}")
        try:
            await loading.edit_text(
                f"🔍 *Riset mendalam dimulai!*\n\n"
                f"📎 `{url}`\n"
                f"🐦 `{tw_label}`\n"
                f"🌐 {lang_label}\n"
                f"🤖 {provider_label}\n\n"
                + "\n".join(lines) +
                "\n\n_Mohon tunggu..._",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    try:
        await update_status(0)
        await asyncio.sleep(1)

        gen_task = asyncio.create_task(
            generator.generate_thread(url, twitter_handle, lang, provider)
        )

        # Update status secara berkala
        await asyncio.sleep(8)
        if not gen_task.done():
            await update_status(1)
        await asyncio.sleep(12)
        if not gen_task.done():
            await update_status(2)
        await asyncio.sleep(20)
        if not gen_task.done():
            await update_status(3)

        result = await gen_task
        await loading.delete()

        if not result["success"]:
            await message.reply_text(
                f"❌ *Gagal generate thread*\n\n`{result['error']}`\n\n"
                "Tips:\n"
                "• Coba URL halaman /docs atau /whitepaper\n"
                "• Pastikan website bisa diakses publik\n"
                "• Coba lagi beberapa saat",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Header info ──────────────────────────────────────────
        tw_status = ""
        if result.get("twitter_handle"):
            if result.get("twitter_success"):
                source_label = {
                    "nitter": "via Nitter ✅",
                    "xcom": "via x.com ✅",
                    "websearch": "via Web Search ⚠️",
                }.get(result.get("twitter_source",""), "✅")
                tw_status = f"🐦 @{result['twitter_handle']}: {source_label}\n"
            else:
                tw_status = f"🐦 @{result['twitter_handle']}: ⚠️ Tidak tersedia\n"

        # Media sources summary
        media_sources = result.get("media_sources", [])
        source_icons = {
            "github": "🐙", "telegram": "📱", "discord": "💬",
            "blog": "📝", "coingecko": "📊", "reddit": "🔴",
            "websearch": "🔍",
        }
        media_str = ""
        if media_sources:
            icons = [source_icons.get(s, "📌") + s.capitalize() for s in media_sources[:6]]
            media_str = f"📚 Sumber: {' · '.join(icons)}\n"

        used_emoji = result.get("provider_emoji", "🤖")
        used_name  = result.get("provider_name", provider)
        used_model = result.get("provider_model", "")

        await message.reply_text(
            f"✅ *Thread selesai!*\n\n"
            f"📌 *{result['project_name']}*\n"
            f"🧵 {result['tweet_count']} tweets · {lang_label}\n"
            f"{tw_status}"
            f"{media_str}"
            f"{used_emoji} Ditulis oleh *{used_name}* (`{used_model}`)\n\n"
            f"⬇️ Thread lengkap:",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── Kirim tiap tweet ─────────────────────────────────────
        for tweet in result["tweets"]:
            safe = _safe_markdown(tweet)
            try:
                await message.reply_text(safe, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await message.reply_text(tweet)
            await asyncio.sleep(0.7)

        # ── Footer ───────────────────────────────────────────────
        tw_handle = result.get("twitter_handle", "")
        regen_twitter = tw_handle or "_"
        used_provider = result.get("provider_used", provider)
        remaining_after = RATE_LIMIT_MAX - len(_rate_log.get(user_id, []))
        keyboard = [
            [
                InlineKeyboardButton("🔄 EN", callback_data=f"regen:english:{used_provider}:{regen_twitter}:{url}"),
                InlineKeyboardButton("🔄 ID", callback_data=f"regen:indonesia:{used_provider}:{regen_twitter}:{url}"),
            ],
            [
                InlineKeyboardButton("🔁 Ganti Provider", callback_data=f"switch_provider:{regen_twitter}:{url}"),
            ]
        ]
        await message.reply_text(
            f"✨ *Thread siap diposting!*\n\n"
            f"💡 _Post satu tweet per 1–2 menit untuk engagement terbaik._\n"
            f"📊 Rate limit tersisa: {remaining_after}/{RATE_LIMIT_MAX} request jam ini.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Generation error: {e}", exc_info=True)
        try:
            await loading.delete()
        except Exception:
            pass
        await message.reply_text(
            f"❌ *Terjadi kesalahan*\n\n`{str(e)}`\n\nCoba kirim URL lagi.",
            parse_mode=ParseMode.MARKDOWN,
        )


def _safe_markdown(text: str) -> str:
    """Sanitize teks agar aman untuk Telegram Markdown."""
    text = text.replace("`", "'")
    text = text.replace("[", "(").replace("]", ")")
    text = re.sub(r"\*{3,}", "**", text)
    return text


async def handle_non_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Kirimkan URL website project untuk mulai!\n\n"
        "Contoh: `https://uniswap.org`",
        parse_mode=ParseMode.MARKDOWN,
    )


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN tidak ditemukan di .env")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(r"https?://\S+"), handle_url)
        ],
        states={
            WAITING_TWITTER: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"https?://\S+"),
                    receive_twitter_handle,
                ),
                CallbackQueryHandler(handle_callback, pattern="^twitter:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("prefs", prefs_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"https?://\S+"),
            handle_non_url,
        )
    )

    logger.info("🤖 Bot Pro started — Full Multi-Media Research Mode")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
