# netmarble_watcher.py (요약본: 핵심만 교체/추가)
import os, json, asyncio, re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import discord
from discord.ext import commands, tasks

import httpx
from bs4 import BeautifulSoup

DATA_PATH = "nm_watcher_data.json"
DEFAULT_INTERVAL_MIN = int(os.getenv("WATCH_INTERVAL_MIN", "3"))
LINK_RE = re.compile(r"https?://forum\.netmarble\.com/sena_rebirth/.+/\d+")

# --- Playwright 가용성 체크 ---
PLAYWRIGHT_OK = True
try:
    from playwright.async_api import async_playwright  # type: ignore
except Exception:
    PLAYWRIGHT_OK = False

# (load_data/save_data 생략: 동일)

class NetmarbleWatcher(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data: Dict[str, Any] = load_data()
        self._playwright = None
        self._browser = None
        self.watch_loop.change_interval(minutes=max(1, DEFAULT_INTERVAL_MIN))
        self._started = False

    async def _ensure_playwright(self):
        if not PLAYWRIGHT_OK:
            return
        if self._playwright and self._browser:
            return
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True, args=["--no-sandbox"])

    async def _close_playwright(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None

    # on_ready / 설정 명령들(공지채널설정, 보드추가, 보드제거, 보드목록, 감시주기, 감시즉시)은 기존 그대로

    @tasks.loop(minutes=DEFAULT_INTERVAL_MIN)
    async def watch_loop(self):
        for guild in list(self.bot.guilds):
            try:
                await self._run_once_for_guild(guild)
            except Exception as e:
                print(f"[watch_loop] guild={guild.id} error: {e}")

    async def _scrape_with_browser(self, url: str) -> List[Dict[str,str]]:
        await self._ensure_playwright()
        if not PLAYWRIGHT_OK or not self._browser:
            return []
        page = await self._browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64)")
        try:
            await page.goto(url, wait_until="networkidle", timeout=20000)
            anchors = await page.eval_on_selector_all(
                "a",
                """els => els.map(a => ({href: a.href, text: (a.textContent||'').trim()}))"""
            )
            out = []
            seen = set()
            for a in anchors:
                href = a.get("href") or ""
                text = a.get("text") or ""
                if LINK_RE.search(href) and text and href not in seen:
                    out.append({"id": href, "title": text, "url": href})
                    seen.add(href)
                    if len(out) >= 10:
                        break
            return out
        finally:
            await page.close()

    async def _scrape_with_http(self, url: str) -> List[Dict[str,str]]:
        # 브라우저가 없거나 설치가 막힌 환경용(서버 렌더/프리렌더된 목록 페이지에서만 동작)
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent":"Mozilla/5.0"}) as client:
                r = await client.get(url, follow_redirects=True)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                out, seen = [], set()
                for a in soup.select("a"):
                    href = (a.get("href") or "").strip()
                    text = (a.get_text(strip=True) or "")
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"https://forum.netmarble.com{href}"
                    if LINK_RE.search(href) and text and href not in seen:
                        out.append({"id": href, "title": text, "url": href})
                        seen.add(href)
                        if len(out) >= 10:
                            break
                return out
        except Exception:
            return []

    async def _fetch_items(self, url: str) -> List[Dict[str,str]]:
        # 1순위: 브라우저, 실패/미가용 시 2순위: HTTP 파싱
        items = await self._scrape_with_browser(url)
        if items:
            return items
        return await self._scrape_with_http(url)

    async def _run_once_for_guild(self, guild: discord.Guild) -> int:
        gid = str(guild.id)
        g = self.data.setdefault(gid, {})
        channel_id = g.get("channel_id")
        boards: List[Dict[str, Any]] = g.get("boards", [])
        if not channel_id or not boards:
            return 0
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return 0

        sent = 0
        for b in boards:
            name = b.get("name") or "탭"
            url = b.get("url") or ""
            if not url:
                continue
            try:
                uniq = await self._fetch_items(url)
                if not uniq:
                    continue
                last_id = b.get("last_id", "")
                new_items = []
                if last_id and last_id in [x["id"] for x in uniq]:
                    for it in reversed(uniq):
                        if it["id"] == last_id:
                            break
                        new_items.append(it)
                else:
                    new_items = [uniq[0]]

                for it in new_items:
                    embed = discord.Embed(
                        title=f"[{name}] 새 글",
                        description=f"**{it['title']}**",
                        url=it["url"],
                        timestamp=datetime.now(timezone.utc),
                        color=discord.Color.blue(),
                    )
                    await channel.send(embed=embed)
                    sent += 1

                b["last_id"] = uniq[0]["id"]
                save_data(self.data)

            except Exception as e:
                print(f"[watch] guild={gid} board={name} err={e}")
                continue
        return sent

async def setup(bot: commands.Bot):
    await bot.add_cog(NetmarbleWatcher(bot))
