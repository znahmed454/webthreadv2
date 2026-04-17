"""
ai_providers.py
───────────────
Abstraksi multi-provider AI: Groq, DeepSeek, OpenAI.

Fitur:
- Setiap provider punya model terbaik masing-masing untuk task ini
- User bisa pilih provider secara manual
- Auto-fallback chain jika provider gagal: pilihan user → Groq → DeepSeek → OpenAI
- Semua provider pakai OpenAI-compatible interface (DeepSeek & Groq keduanya support ini)
- Tracking provider mana yang akhirnya dipakai
"""

import os
import logging
from typing import Optional
from openai import AsyncOpenAI  # DeepSeek & OpenAI pakai library yang sama
from groq import AsyncGroq

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# KONFIGURASI PROVIDER
# ════════════════════════════════════════════════════════════════════

PROVIDERS = {
    "groq": {
        "name": "Groq",
        "label": "⚡ Groq (LLaMA 3.3 70B) — Tercepat",
        "model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
        "base_url": None,           # native Groq SDK
        "temperature": 0.82,
        "max_tokens": 5500,
        "emoji": "⚡",
    },
    "deepseek": {
        "name": "DeepSeek",
        "label": "🧠 DeepSeek (R1) — Paling Analitis",
        "model": "deepseek-reasoner",   # deepseek-r1 alias
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "temperature": 0.80,
        "max_tokens": 6000,
        "emoji": "🧠",
    },
    "openai": {
        "name": "OpenAI",
        "label": "✨ OpenAI (GPT-4o) — Paling Kreatif",
        "model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
        "base_url": None,           # native OpenAI SDK
        "temperature": 0.85,
        "max_tokens": 5000,
        "emoji": "✨",
    },
}

# Urutan fallback default
FALLBACK_ORDER = ["groq", "deepseek", "openai"]


# ════════════════════════════════════════════════════════════════════
# PROVIDER AVAILABILITY CHECK
# ════════════════════════════════════════════════════════════════════

def get_available_providers() -> list[str]:
    """Return list provider yang API key-nya tersedia di .env"""
    return [
        pid for pid, cfg in PROVIDERS.items()
        if os.getenv(cfg["env_key"])
    ]


def get_provider_status() -> dict:
    """Return status semua provider (tersedia/tidak)"""
    return {
        pid: {
            "available": bool(os.getenv(cfg["env_key"])),
            "name": cfg["name"],
            "label": cfg["label"],
            "emoji": cfg["emoji"],
        }
        for pid, cfg in PROVIDERS.items()
    }


# ════════════════════════════════════════════════════════════════════
# CORE CALLER — memanggil satu provider spesifik
# ════════════════════════════════════════════════════════════════════

async def _call_groq(prompt: str, system: str, cfg: dict) -> str:
    """Call Groq via native async SDK"""
    api_key = os.getenv(cfg["env_key"])
    client = AsyncGroq(api_key=api_key)
    response = await client.chat.completions.create(
        model=cfg["model"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
    )
    return response.choices[0].message.content.strip()


async def _call_openai_compat(prompt: str, system: str, cfg: dict) -> str:
    """
    Call provider yang pakai OpenAI-compatible API.
    Covers: OpenAI, DeepSeek.
    """
    api_key = os.getenv(cfg["env_key"])
    kwargs = {"api_key": api_key}
    if cfg.get("base_url"):
        kwargs["base_url"] = cfg["base_url"]

    client = AsyncOpenAI(**kwargs)

    # DeepSeek R1 (reasoner) tidak support system role → gabung ke user
    if cfg["model"] in ("deepseek-reasoner",):
        messages = [{"role": "user", "content": f"{system}\n\n{prompt}"}]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]

    response = await client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
    )
    return response.choices[0].message.content.strip()


async def call_provider(provider_id: str, prompt: str, system: str) -> str:
    """
    Panggil satu provider spesifik.
    Raise exception jika gagal.
    """
    cfg = PROVIDERS[provider_id]
    logger.info(f"Calling {cfg['name']} ({cfg['model']})...")

    if provider_id == "groq":
        return await _call_groq(prompt, system, cfg)
    else:
        return await _call_openai_compat(prompt, system, cfg)


# ════════════════════════════════════════════════════════════════════
# MAIN ENTRY — dengan fallback otomatis
# ════════════════════════════════════════════════════════════════════

async def generate_with_fallback(
    prompt: str,
    system: str,
    preferred_provider: str = "groq",
) -> tuple[str, str]:
    """
    Generate AI response dengan fallback otomatis.

    Args:
        prompt: User prompt
        system: System prompt
        preferred_provider: Provider yang diutamakan user

    Returns:
        tuple (raw_response: str, provider_used: str)

    Raises:
        RuntimeError jika semua provider gagal
    """
    available = get_available_providers()

    if not available:
        raise RuntimeError(
            "Tidak ada API key yang dikonfigurasi. "
            "Isi minimal satu di .env: GROQ_API_KEY, DEEPSEEK_API_KEY, atau OPENAI_API_KEY"
        )

    # Susun urutan: preferred dulu, lalu fallback order (skip yang sudah dicoba)
    order = [preferred_provider] if preferred_provider in available else []
    for pid in FALLBACK_ORDER:
        if pid not in order and pid in available:
            order.append(pid)

    last_error = None
    for provider_id in order:
        try:
            raw = await call_provider(provider_id, prompt, system)
            logger.info(f"✅ {PROVIDERS[provider_id]['name']} responded ({len(raw)} chars)")
            return raw, provider_id
        except Exception as e:
            last_error = e
            logger.warning(f"❌ {PROVIDERS[provider_id]['name']} failed: {e}")
            if provider_id != order[-1]:
                next_p = order[order.index(provider_id) + 1]
                logger.info(f"⟶ Falling back to {PROVIDERS[next_p]['name']}...")

    raise RuntimeError(
        f"Semua provider gagal. Error terakhir: {last_error}"
    )
