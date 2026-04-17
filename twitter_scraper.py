"""
twitter_scraper.py
──────────────────
Scraper akun Twitter/X dengan tiga layer fallback:
  1. Nitter instances (publik, gratis)
  2. x.com langsung (HTML publik, tanpa API)
  3. Web search fallback (DuckDuckGo/Google)

Setiap layer memberikan data yang sama-shape sehingga
ThreadGenerator tidak perlu tahu sumber mana yang berhasil.
"""

import re
import asyncio
import logging
import random
import urllib.parse
from typing import Optional
import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# KONFIGURASI
# ════════════════════════════════════════════════════════════════════

NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.cz",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.unixfox.eu",
    "https://nitter.fdn.fr",
    "https://nitter.it",
    "https://nitter.net",
    "https://twiiit.com",
]

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6)


# ════════════════════════════════════════════════════════════════════
# HELPER
# ════════════════════════════════════════════════════════════════════

def _clean_handle(handle: str) -> str:
    """Normalisasi handle: hapus @, strip spasi."""
    return handle.strip().lstrip("@").split("/")[-1].split("?")[0].strip()


def _clean_text(text: str) -> str:
    """Bersihkan whitespace berlebih."""
    return re.sub(r"\s+", " ", text).strip()


def _is_valid_tweet(text: str) -> bool:
    """Filter tweet yang terlalu pendek atau spam."""
    t = text.strip()
    if len(t) < 20:
        return False
    # Abaikan tweet yang 100% link
    if t.startswith("http") and " " not in t:
        return False
    return True


# ════════════════════════════════════════════════════════════════════
# LAYER 1 — NITTER SCRAPER
# ════════════════════════════════════════════════════════════════════

class NitterScraper:
    """Scrape profil & tweet via Nitter instances publik."""

    async def fetch(
        self, handle: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        instances = NITTER_INSTANCES.copy()
        random.shuffle(instances)

        for base in instances:
            url = f"{base}/{handle}"
            try:
                async with session.get(
                    url, headers=HEADERS_BROWSER,
                    timeout=TIMEOUT, ssl=False,
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text(errors="ignore")
                    result = self._parse(html, handle)
                    if result and result.get("tweets"):
                        result["nitter_instance"] = base
                        return result
            except Exception as e:
                logger.debug(f"Nitter {base} failed for @{handle}: {e}")
                continue

        return None

    def _parse(self, html: str, handle: str) -> Optional[dict]:
        soup = BeautifulSoup(html, "html.parser")

        # Deteksi error page
        error = soup.find(class_=re.compile("error|not-found|banned", re.I))
        if error:
            return None

        # Bio / profile
        bio = ""
        bio_el = soup.find(class_=re.compile("profile-bio|bio", re.I))
        if bio_el:
            bio = _clean_text(bio_el.get_text())

        # Stats
        stats = {}
        for stat in soup.find_all(class_=re.compile("profile-stat-num|stat-num", re.I)):
            val = _clean_text(stat.get_text())
            label_el = stat.find_next_sibling() or stat.parent
            label = _clean_text(label_el.get_text()).lower() if label_el else ""
            if val:
                if "tweet" in label or "post" in label:
                    stats["tweets_count"] = val
                elif "follow" in label and "ing" not in label:
                    stats["followers"] = val
                elif "following" in label:
                    stats["following"] = val

        # Tweets
        tweets = []
        tweet_items = soup.find_all(class_=re.compile(r"\btweet-content\b", re.I))
        if not tweet_items:
            tweet_items = soup.find_all(class_=re.compile("tweet-text|timeline-item", re.I))

        for item in tweet_items[:30]:
            text = _clean_text(item.get_text())
            if _is_valid_tweet(text):
                tweets.append(text)

        if not tweets:
            return None

        return {
            "handle": handle,
            "bio": bio,
            "stats": stats,
            "tweets": tweets[:25],
            "source": "nitter",
        }


# ════════════════════════════════════════════════════════════════════
# LAYER 2 — x.com DIRECT SCRAPER
# ════════════════════════════════════════════════════════════════════

class XcomScraper:
    """
    Scrape HTML publik x.com. Hasilnya seringkali minimal karena
    Twitter sangat JS-heavy, tapi masih bisa ambil bio & beberapa data.
    """

    async def fetch(
        self, handle: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        url = f"https://x.com/{handle}"
        try:
            async with session.get(
                url, headers=HEADERS_BROWSER,
                timeout=TIMEOUT, ssl=False,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text(errors="ignore")
                return self._parse(html, handle)
        except Exception as e:
            logger.debug(f"x.com direct failed for @{handle}: {e}")
            return None

    def _parse(self, html: str, handle: str) -> Optional[dict]:
        soup = BeautifulSoup(html, "html.parser")

        # Meta tags — seringkali berisi info berguna
        bio = ""
        for attr in [("property", "og:description"), ("name", "description")]:
            m = soup.find("meta", attrs={attr[0]: attr[1]})
            if m and m.get("content"):
                bio = _clean_text(m["content"])
                break

        title = ""
        m = soup.find("meta", attrs={"property": "og:title"})
        if m and m.get("content"):
            title = _clean_text(m["content"])

        if not bio and not title:
            return None

        return {
            "handle": handle,
            "bio": bio,
            "title": title,
            "stats": {},
            "tweets": [],  # x.com jarang bisa scrape tweets tanpa JS
            "source": "xcom",
        }


# ════════════════════════════════════════════════════════════════════
# LAYER 3 — WEB SEARCH FALLBACK
# ════════════════════════════════════════════════════════════════════

class WebSearchScraper:
    """
    Cari informasi akun Twitter via DuckDuckGo HTML search.
    Fallback terakhir — hasilnya berupa snippet publik.
    """

    async def fetch(
        self, handle: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        queries = [
            f"site:twitter.com OR site:x.com @{handle} tweets announcement",
            f"@{handle} twitter crypto web3 project announcement",
            f"{handle} twitter thread announcement roadmap",
        ]

        all_snippets = []
        for query in queries[:2]:
            snippets = await self._ddg_search(query, session)
            all_snippets.extend(snippets)
            if len(all_snippets) >= 8:
                break
            await asyncio.sleep(0.5)

        if not all_snippets:
            return None

        return {
            "handle": handle,
            "bio": "",
            "stats": {},
            "tweets": all_snippets[:15],
            "source": "websearch",
        }

    async def _ddg_search(
        self, query: str, session: aiohttp.ClientSession
    ) -> list[str]:
        """Scrape DuckDuckGo HTML untuk mendapatkan snippets."""
        url = "https://html.duckduckgo.com/html/"
        params = {"q": query, "kl": "en-us"}
        headers = {**HEADERS_BROWSER, "Referer": "https://duckduckgo.com/"}

        try:
            async with session.post(
                url, data=params, headers=headers,
                timeout=TIMEOUT, ssl=False,
            ) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text(errors="ignore")
                return self._parse_ddg(html)
        except Exception as e:
            logger.debug(f"DDG search failed: {e}")
            return []

    def _parse_ddg(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for result in soup.find_all("div", class_=re.compile("result__body|result__snippet")):
            text = _clean_text(result.get_text())
            if _is_valid_tweet(text) and len(text) > 30:
                results.append(text[:400])

        return results[:10]


# ════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — TwitterScraper
# ════════════════════════════════════════════════════════════════════

class TwitterScraper:
    """
    Orchestrator utama. Jalankan ketiga layer secara berurutan:
      Nitter → x.com → WebSearch
    Return data pertama yang berhasil.
    """

    def __init__(self):
        self.nitter = NitterScraper()
        self.xcom = XcomScraper()
        self.websearch = WebSearchScraper()

    async def research_account(self, handle: str) -> dict:
        """
        Riset akun Twitter/X dengan multi-layer fallback.

        Returns:
            dict dengan keys:
              - success: bool
              - handle: str
              - bio: str
              - stats: dict
              - tweets: list[str]
              - source: str  ('nitter' | 'xcom' | 'websearch' | 'failed')
        """
        handle = _clean_handle(handle)
        if not handle:
            return {"success": False, "source": "failed", "error": "Handle kosong"}

        logger.info(f"[TwitterScraper] Researching @{handle}...")

        connector = aiohttp.TCPConnector(ssl=False, limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:

            # Layer 1: Nitter
            logger.info(f"[TwitterScraper] Layer 1: Nitter...")
            result = await self.nitter.fetch(handle, session)
            if result and result.get("tweets"):
                logger.info(f"[TwitterScraper] Nitter OK — {len(result['tweets'])} tweets")
                result["success"] = True
                return result

            # Layer 2: x.com direct
            logger.info(f"[TwitterScraper] Layer 2: x.com direct...")
            result = await self.xcom.fetch(handle, session)
            if result and (result.get("bio") or result.get("tweets")):
                logger.info(f"[TwitterScraper] x.com OK")
                result["success"] = True
                return result

            # Layer 3: Web Search
            logger.info(f"[TwitterScraper] Layer 3: Web search fallback...")
            result = await self.websearch.fetch(handle, session)
            if result and result.get("tweets"):
                logger.info(f"[TwitterScraper] WebSearch OK — {len(result['tweets'])} snippets")
                result["success"] = True
                return result

        logger.warning(f"[TwitterScraper] All layers failed for @{handle}")
        return {
            "success": False,
            "handle": handle,
            "source": "failed",
            "bio": "",
            "stats": {},
            "tweets": [],
            "error": "Semua metode scraping gagal",
        }

    def format_for_research(self, data: dict) -> str:
        """
        Format data Twitter menjadi teks riset yang siap dimasukkan ke prompt.
        """
        if not data.get("success"):
            return ""

        handle = data.get("handle", "")
        source = data.get("source", "unknown")
        source_label = {
            "nitter": "via Nitter ✓",
            "xcom": "via x.com ✓",
            "websearch": "via Web Search (snippet)",
            "failed": "tidak tersedia",
        }.get(source, source)

        lines = [f"=== Twitter/X: @{handle} [{source_label}] ===\n"]

        if data.get("bio"):
            lines.append(f"BIO: {data['bio']}\n")

        stats = data.get("stats", {})
        if stats:
            stat_parts = []
            if stats.get("followers"):
                stat_parts.append(f"Followers: {stats['followers']}")
            if stats.get("tweets_count"):
                stat_parts.append(f"Total tweets: {stats['tweets_count']}")
            if stat_parts:
                lines.append("STATS: " + " | ".join(stat_parts) + "\n")

        tweets = data.get("tweets", [])
        if tweets:
            lines.append(f"RECENT TWEETS/POSTS ({len(tweets)} items):")
            for i, t in enumerate(tweets[:20], 1):
                lines.append(f"  [{i}] {t[:300]}")

        return "\n".join(lines)
