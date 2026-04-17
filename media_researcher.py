"""
media_researcher.py
────────────────────
Riset mendalam ke SEMUA media publik sebuah project:
  - GitHub (repo, README, stars, activity)
  - Medium / Substack (artikel terbaru)
  - Discord (invite info publik)
  - Telegram (channel publik)
  - YouTube (deskripsi channel & video)
  - Reddit (posts tentang project)
  - CoinGecko / CoinMarketCap (data token jika crypto)
  - General web search (DuckDuckGo)
  - LinkedIn (deskripsi company publik)

Setiap researcher punya interface yang sama:
  async def research(handle_or_url, session) -> dict | None
"""

import re
import asyncio
import logging
import urllib.parse
from typing import Optional
import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = aiohttp.ClientTimeout(total=14, connect=6)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ════════════════════════════════════════════════════════════════════
# GITHUB RESEARCHER
# ════════════════════════════════════════════════════════════════════

class GitHubResearcher:
    """Ambil data publik repo GitHub via API tanpa auth."""

    async def research(
        self, github_url: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        # Parse owner/repo dari URL
        m = re.search(
            r"github\.com/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?",
            github_url
        )
        if not m:
            return None
        owner = m.group(1)
        repo = m.group(2)

        try:
            results = {}

            # Jika ada repo spesifik
            if repo and repo not in ("", ".github"):
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                async with session.get(
                    api_url,
                    headers={**HEADERS, "Accept": "application/vnd.github.v3+json"},
                    timeout=TIMEOUT, ssl=False,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results["repo_name"] = data.get("full_name", "")
                        results["description"] = data.get("description", "")
                        results["stars"] = data.get("stargazers_count", 0)
                        results["forks"] = data.get("forks_count", 0)
                        results["language"] = data.get("language", "")
                        results["topics"] = data.get("topics", [])
                        results["open_issues"] = data.get("open_issues_count", 0)
                        results["last_push"] = data.get("pushed_at", "")[:10]
                        results["license"] = (data.get("license") or {}).get("name", "")

                        # README
                        readme = await self._fetch_readme(owner, repo, session)
                        if readme:
                            results["readme_excerpt"] = readme[:1500]
            else:
                # Ambil repos publik org/user
                api_url = f"https://api.github.com/users/{owner}/repos?sort=stars&per_page=5"
                async with session.get(
                    api_url,
                    headers={**HEADERS, "Accept": "application/vnd.github.v3+json"},
                    timeout=TIMEOUT, ssl=False,
                ) as resp:
                    if resp.status == 200:
                        repos = await resp.json()
                        results["org"] = owner
                        results["top_repos"] = [
                            {
                                "name": r.get("name"),
                                "description": r.get("description", ""),
                                "stars": r.get("stargazers_count", 0),
                                "language": r.get("language", ""),
                            }
                            for r in repos[:5]
                        ]

            if results:
                results["source"] = "github"
                results["url"] = github_url
                return results

        except Exception as e:
            logger.debug(f"GitHub research failed for {github_url}: {e}")

        return None

    async def _fetch_readme(
        self, owner: str, repo: str, session: aiohttp.ClientSession
    ) -> str:
        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
            try:
                async with session.get(url, timeout=TIMEOUT, ssl=False) as resp:
                    if resp.status == 200:
                        text = await resp.text(errors="ignore")
                        # Strip markdown syntax
                        text = re.sub(r"#+\s*", "", text)
                        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
                        text = re.sub(r"[`*_]{1,3}", "", text)
                        return _clean(text[:2000])
            except Exception:
                continue
        return ""


# ════════════════════════════════════════════════════════════════════
# MEDIUM / SUBSTACK RESEARCHER
# ════════════════════════════════════════════════════════════════════

class BlogResearcher:
    """Scrape artikel dari Medium atau Substack."""

    async def research(
        self, url: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        is_medium = "medium.com" in url
        is_substack = "substack.com" in url

        if not (is_medium or is_substack):
            return None

        try:
            async with session.get(
                url, headers=HEADERS, timeout=TIMEOUT, ssl=False
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text(errors="ignore")

            soup = BeautifulSoup(html, "html.parser")
            platform = "medium" if is_medium else "substack"
            articles = []

            if is_medium:
                # Medium article list
                for article in soup.find_all("article")[:8]:
                    title_el = article.find(["h2", "h3"])
                    excerpt_el = article.find("p")
                    if title_el:
                        articles.append({
                            "title": _clean(title_el.get_text()),
                            "excerpt": _clean(excerpt_el.get_text()) if excerpt_el else "",
                        })

            elif is_substack:
                for post in soup.find_all(class_=re.compile("post-preview|post-title"))[:8]:
                    title_el = post.find(["h2", "h3", "a"])
                    if title_el:
                        articles.append({
                            "title": _clean(title_el.get_text()),
                            "excerpt": "",
                        })

            if not articles:
                # Fallback: ambil semua heading
                for h in soup.find_all(["h2", "h3"])[:10]:
                    t = _clean(h.get_text())
                    if len(t) > 10:
                        articles.append({"title": t, "excerpt": ""})

            # Bio / description
            bio = ""
            for attr in [("property", "og:description"), ("name", "description")]:
                m = soup.find("meta", attrs={attr[0]: attr[1]})
                if m and m.get("content"):
                    bio = _clean(m["content"])
                    break

            if articles or bio:
                return {
                    "source": platform,
                    "url": url,
                    "bio": bio,
                    "articles": articles[:8],
                }

        except Exception as e:
            logger.debug(f"Blog research failed for {url}: {e}")

        return None


# ════════════════════════════════════════════════════════════════════
# TELEGRAM RESEARCHER
# ════════════════════════════════════════════════════════════════════

class TelegramResearcher:
    """Scrape channel Telegram publik via t.me/s/{channel}."""

    async def research(
        self, tg_url: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        # Parse channel handle
        m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]+)", tg_url)
        if not m:
            return None
        channel = m.group(1)

        preview_url = f"https://t.me/s/{channel}"
        try:
            async with session.get(
                preview_url, headers=HEADERS, timeout=TIMEOUT, ssl=False
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text(errors="ignore")

            soup = BeautifulSoup(html, "html.parser")

            # Channel info
            name = _clean((soup.find(class_="tgme_channel_info_header_title") or soup.find("title") or type("", (), {"get_text": lambda *a: ""})()).get_text())
            desc = _clean((soup.find(class_="tgme_channel_info_description") or type("", (), {"get_text": lambda *a: ""})()).get_text())
            members = ""
            counter = soup.find(class_=re.compile("counter_value|members"))
            if counter:
                members = _clean(counter.get_text())

            # Messages
            messages = []
            for msg in soup.find_all(class_="tgme_widget_message_text")[:15]:
                text = _clean(msg.get_text())
                if len(text) > 20:
                    messages.append(text[:400])

            if name or messages:
                return {
                    "source": "telegram",
                    "url": tg_url,
                    "channel": channel,
                    "name": name,
                    "description": desc,
                    "members": members,
                    "messages": messages[:12],
                }

        except Exception as e:
            logger.debug(f"Telegram research failed for {tg_url}: {e}")

        return None


# ════════════════════════════════════════════════════════════════════
# DISCORD RESEARCHER
# ════════════════════════════════════════════════════════════════════

class DiscordResearcher:
    """Ambil info publik Discord server via invite endpoint."""

    async def research(
        self, discord_url: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        # Parse invite code
        m = re.search(r"discord(?:\.gg|(?:app)?\.com/invite)/([A-Za-z0-9]+)", discord_url)
        if not m:
            return None
        invite_code = m.group(1)

        api_url = f"https://discord.com/api/v10/invites/{invite_code}?with_counts=true"
        try:
            async with session.get(
                api_url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=TIMEOUT, ssl=False,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            guild = data.get("guild", {})
            name = guild.get("name", "")
            description = guild.get("description", "") or ""
            members = data.get("approximate_member_count", 0)
            online = data.get("approximate_presence_count", 0)

            if name:
                return {
                    "source": "discord",
                    "url": discord_url,
                    "server_name": name,
                    "description": description,
                    "members": members,
                    "online": online,
                }

        except Exception as e:
            logger.debug(f"Discord research failed for {discord_url}: {e}")

        return None


# ════════════════════════════════════════════════════════════════════
# COINGECKO RESEARCHER
# ════════════════════════════════════════════════════════════════════

class CoinGeckoResearcher:
    """
    Ambil data token/coin dari CoinGecko API publik.
    Dipakai ketika website terdeteksi sebagai project crypto/DeFi.
    """

    async def research(
        self, project_name: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        # Search by name
        search_url = f"https://api.coingecko.com/api/v3/search?query={urllib.parse.quote(project_name)}"
        try:
            async with session.get(
                search_url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=TIMEOUT, ssl=False,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            coins = data.get("coins", [])
            if not coins:
                return None

            coin = coins[0]
            coin_id = coin.get("id")
            if not coin_id:
                return None

            # Detail coin
            detail_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&community_data=true&developer_data=true"
            async with session.get(
                detail_url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=TIMEOUT, ssl=False,
            ) as resp:
                if resp.status != 200:
                    return None
                detail = await resp.json()

            desc = (detail.get("description") or {}).get("en", "")[:800]
            market = detail.get("market_data", {})
            community = detail.get("community_data", {})

            return {
                "source": "coingecko",
                "coin_id": coin_id,
                "name": detail.get("name", ""),
                "symbol": detail.get("symbol", "").upper(),
                "description": _clean(desc),
                "categories": detail.get("categories", [])[:5],
                "market_cap_rank": detail.get("market_cap_rank"),
                "price_usd": (market.get("current_price") or {}).get("usd"),
                "market_cap_usd": (market.get("market_cap") or {}).get("usd"),
                "twitter_followers": community.get("twitter_followers"),
                "reddit_subscribers": community.get("reddit_subscribers"),
                "github_stars": (detail.get("developer_data") or {}).get("stars"),
                "homepage": ((detail.get("links") or {}).get("homepage") or [""])[0],
            }

        except Exception as e:
            logger.debug(f"CoinGecko research failed for {project_name}: {e}")

        return None


# ════════════════════════════════════════════════════════════════════
# REDDIT RESEARCHER
# ════════════════════════════════════════════════════════════════════

class RedditResearcher:
    """Cari posts Reddit tentang project via JSON API publik."""

    async def research(
        self, project_name: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        query = urllib.parse.quote(f"{project_name} crypto OR defi OR web3")
        url = f"https://www.reddit.com/search.json?q={query}&sort=relevance&limit=8&t=year"

        try:
            async with session.get(
                url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=TIMEOUT, ssl=False,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            posts = []
            for child in (data.get("data") or {}).get("children", []):
                post = child.get("data", {})
                title = post.get("title", "")
                selftext = (post.get("selftext") or "")[:300]
                subreddit = post.get("subreddit", "")
                score = post.get("score", 0)
                if title and score > 0:
                    posts.append({
                        "title": title,
                        "excerpt": _clean(selftext),
                        "subreddit": subreddit,
                        "score": score,
                    })

            if posts:
                return {
                    "source": "reddit",
                    "query": project_name,
                    "posts": posts[:6],
                }

        except Exception as e:
            logger.debug(f"Reddit research failed for {project_name}: {e}")

        return None


# ════════════════════════════════════════════════════════════════════
# GENERAL WEB SEARCH (DuckDuckGo)
# ════════════════════════════════════════════════════════════════════

class GeneralSearchResearcher:
    """
    Riset umum via DuckDuckGo HTML — menangkap info yang tidak ada
    di channel resmi: review, artikel, coverage media.
    """

    async def research(
        self, project_name: str, session: aiohttp.ClientSession,
        extra_query: str = ""
    ) -> Optional[dict]:
        base = f"{project_name} {extra_query}".strip()
        queries = [
            f"{base} review analysis",
            f"{base} tokenomics whitepaper explained",
        ]

        all_results = []
        for query in queries:
            results = await self._search(query, session)
            all_results.extend(results)
            await asyncio.sleep(0.3)

        if not all_results:
            return None

        return {
            "source": "websearch",
            "project": project_name,
            "results": all_results[:10],
        }

    async def _search(
        self, query: str, session: aiohttp.ClientSession
    ) -> list[dict]:
        url = "https://html.duckduckgo.com/html/"
        try:
            async with session.post(
                url, data={"q": query, "kl": "en-us"},
                headers=HEADERS, timeout=TIMEOUT, ssl=False,
            ) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text(errors="ignore")

            soup = BeautifulSoup(html, "html.parser")
            results = []
            for div in soup.find_all("div", class_=re.compile("result__body"))[:6]:
                title_el = div.find(class_=re.compile("result__title|result__a"))
                snippet_el = div.find(class_=re.compile("result__snippet"))
                title = _clean(title_el.get_text()) if title_el else ""
                snippet = _clean(snippet_el.get_text()) if snippet_el else ""
                if title or snippet:
                    results.append({"title": title, "snippet": snippet[:300]})
            return results

        except Exception as e:
            logger.debug(f"DDG search error: {e}")
            return []


# ════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — MediaResearcher
# ════════════════════════════════════════════════════════════════════

class MediaResearcher:
    """
    Orchestrator utama riset multi-media.
    Menerima social_links dari WebScraper dan project_name,
    lalu riset semua platform yang ditemukan secara paralel.
    """

    def __init__(self):
        self.github = GitHubResearcher()
        self.blog = BlogResearcher()
        self.telegram = TelegramResearcher()
        self.discord = DiscordResearcher()
        self.coingecko = CoinGeckoResearcher()
        self.reddit = RedditResearcher()
        self.general = GeneralSearchResearcher()

    async def research_all(
        self,
        social_links: list[str],
        project_name: str,
        is_crypto: bool = True,
    ) -> dict:
        """
        Jalankan semua riset secara paralel berdasarkan social_links
        yang ditemukan di website.

        Returns:
            dict berisi hasil dari setiap platform yang berhasil
        """
        results = {}
        connector = aiohttp.TCPConnector(ssl=False, limit=15)

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = []
            task_labels = []

            for link in social_links:
                link_lower = link.lower()

                if "github.com" in link_lower:
                    tasks.append(self.github.research(link, session))
                    task_labels.append("github")

                elif "medium.com" in link_lower or "substack.com" in link_lower:
                    tasks.append(self.blog.research(link, session))
                    task_labels.append("blog")

                elif "t.me" in link_lower or "telegram" in link_lower:
                    tasks.append(self.telegram.research(link, session))
                    task_labels.append("telegram")

                elif "discord" in link_lower:
                    tasks.append(self.discord.research(link, session))
                    task_labels.append("discord")

            # CoinGecko & Reddit — jalankan jika project terdeteksi crypto
            if is_crypto and project_name:
                tasks.append(self.coingecko.research(project_name, session))
                task_labels.append("coingecko")

                tasks.append(self.reddit.research(project_name, session))
                task_labels.append("reddit")

            # Web search umum selalu dijalankan
            if project_name:
                tasks.append(self.general.research(project_name, session))
                task_labels.append("websearch")

            # Jalankan semua secara paralel dengan timeout 30 detik
            if tasks:
                done = await asyncio.gather(*tasks, return_exceptions=True)
                for label, result in zip(task_labels, done):
                    if isinstance(result, Exception):
                        logger.debug(f"[MediaResearcher] {label} exception: {result}")
                        continue
                    if result:
                        # Jika ada duplikat label (multiple github links), merge
                        if label in results:
                            label = f"{label}_2"
                        results[label] = result

        logger.info(f"[MediaResearcher] Done: {list(results.keys())}")
        return results

    def compile_for_prompt(self, media_data: dict) -> str:
        """
        Format semua hasil riset media menjadi teks siap masuk prompt.
        """
        if not media_data:
            return ""

        parts = []

        # GitHub
        if "github" in media_data:
            gh = media_data["github"]
            parts.append("\n── GITHUB ──────────────────────────────────────")
            if gh.get("repo_name"):
                parts.append(f"Repo: {gh['repo_name']}")
            if gh.get("description"):
                parts.append(f"Description: {gh['description']}")
            if gh.get("stars"):
                parts.append(f"Stars: {gh['stars']:,} | Forks: {gh.get('forks', 0):,}")
            if gh.get("language"):
                parts.append(f"Language: {gh['language']}")
            if gh.get("topics"):
                parts.append(f"Topics: {', '.join(gh['topics'])}")
            if gh.get("last_push"):
                parts.append(f"Last push: {gh['last_push']}")
            if gh.get("readme_excerpt"):
                parts.append(f"\nREADME excerpt:\n{gh['readme_excerpt'][:800]}")
            if gh.get("top_repos"):
                parts.append("Top repos:")
                for r in gh["top_repos"][:3]:
                    parts.append(f"  • {r['name']} — {r['description']} (⭐{r['stars']:,})")

        # Blog (Medium/Substack)
        for key in ("blog", "blog_2"):
            if key in media_data:
                bl = media_data[key]
                platform = bl.get("source", "blog").capitalize()
                parts.append(f"\n── {platform.upper()} ──────────────────────────────────────")
                if bl.get("bio"):
                    parts.append(f"Description: {bl['bio']}")
                if bl.get("articles"):
                    parts.append("Recent articles:")
                    for a in bl["articles"][:5]:
                        line = f"  • {a['title']}"
                        if a.get("excerpt"):
                            line += f" — {a['excerpt'][:120]}"
                        parts.append(line)

        # Telegram
        if "telegram" in media_data:
            tg = media_data["telegram"]
            parts.append("\n── TELEGRAM ──────────────────────────────────────")
            if tg.get("name"):
                parts.append(f"Channel: {tg['name']}")
            if tg.get("description"):
                parts.append(f"Description: {tg['description']}")
            if tg.get("members"):
                parts.append(f"Members: {tg['members']}")
            if tg.get("messages"):
                parts.append("Recent messages:")
                for msg in tg["messages"][:5]:
                    parts.append(f"  [{msg[:250]}]")

        # Discord
        if "discord" in media_data:
            dc = media_data["discord"]
            parts.append("\n── DISCORD ──────────────────────────────────────")
            if dc.get("server_name"):
                parts.append(f"Server: {dc['server_name']}")
            if dc.get("description"):
                parts.append(f"Description: {dc['description']}")
            if dc.get("members"):
                parts.append(f"Members: {dc['members']:,} | Online: {dc.get('online', 0):,}")

        # CoinGecko
        if "coingecko" in media_data:
            cg = media_data["coingecko"]
            parts.append("\n── COINGECKO ──────────────────────────────────────")
            if cg.get("name") and cg.get("symbol"):
                parts.append(f"Token: {cg['name']} ({cg['symbol']})")
            if cg.get("market_cap_rank"):
                parts.append(f"Market cap rank: #{cg['market_cap_rank']}")
            if cg.get("description"):
                parts.append(f"Description: {cg['description'][:500]}")
            if cg.get("categories"):
                parts.append(f"Categories: {', '.join(cg['categories'])}")
            if cg.get("twitter_followers"):
                parts.append(f"Twitter followers (CoinGecko): {cg['twitter_followers']:,}")
            if cg.get("github_stars"):
                parts.append(f"GitHub stars (CoinGecko): {cg['github_stars']:,}")

        # Reddit
        if "reddit" in media_data:
            rd = media_data["reddit"]
            parts.append("\n── REDDIT ──────────────────────────────────────")
            for post in rd.get("posts", [])[:4]:
                line = f"  [{post['subreddit']}] {post['title']}"
                if post.get("excerpt"):
                    line += f" — {post['excerpt'][:150]}"
                parts.append(line)

        # Web search
        if "websearch" in media_data:
            ws = media_data["websearch"]
            parts.append("\n── WEB COVERAGE ──────────────────────────────────────")
            for r in ws.get("results", [])[:5]:
                if r.get("title"):
                    parts.append(f"  • {r['title']}")
                    if r.get("snippet"):
                        parts.append(f"    {r['snippet'][:200]}")

        return "\n".join(parts) if parts else ""


def detect_is_crypto(web_data: dict) -> bool:
    """Heuristik sederhana: apakah project ini crypto/web3?"""
    crypto_keywords = {
        "token", "blockchain", "defi", "web3", "nft", "dao",
        "smart contract", "protocol", "wallet", "crypto", "dex",
        "yield", "staking", "liquidity", "mint", "whitepaper",
        "tokenomics", "chain", "solana", "ethereum", "polygon",
    }
    text = ""
    for page in web_data.get("pages", []):
        text += (page.get("title", "") + " " +
                 page.get("description", "") + " " +
                 page.get("keywords", "") + " ").lower()
        for h in page.get("headings", [])[:5]:
            text += h.lower() + " "

    hits = sum(1 for kw in crypto_keywords if kw in text)
    return hits >= 2
