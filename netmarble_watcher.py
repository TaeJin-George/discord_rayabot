# netmarble_watcher.py
# Python 3.12 / discord.py 2.x
# 기능: 넷마블 포럼(세나 리버스) 게시판 목록을 주기적으로 확인하고 새 글을 지정 채널로 공지
# 명령(프리픽스): !공지채널설정, !보드추가, !보드제거, !보드목록, !감시주기, !감시즉시

import os, json, asyncio, re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import discord
from discord.ext import commands, tasks

# 폴백 크롤링용 (Playwright 없을 때)
import httpx
from bs4 import BeautifulSoup

import logging
log = logging.getLogger("netmarble")

# ============ 설정/유틸 ============

DATA_PATH = "nm_watcher_data.json"
DEFAULT_INTERVAL_MIN = int(os.getenv("WATCH_INTERVAL_MIN", "3"))

# 세나 리버스 포럼 글 링크 대략 매칭
# LINK_RE = re.compile(r"https?://forum\.netmarble\.com/sena_rebirth/.+/\d+")
# LINK_RE = re.compile(r"^https?://forum\.netmarble\.com/sena_rebirth/.+/\d+")
#BOARD_ID_RE = re.compile(r"/list/(\d+)/")
VIEW_LINK_RE = re.compile(
    r"^https?://forum\.netmarble\.com/sena_rebirth/view/\d+/\d+(?:[?#].*)?$"
)

# Playwright 사용 가능 여부
PLAYWRIGHT_OK = True
try:
    from playwright.async_api import async_playwright  # type: ignore
except Exception:
    PLAYWRIGHT_OK = False

def load_data() -> Dict[str, Any]:
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def save_data(d: Dict[str, Any]) -> None:
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_PATH)

# ============ Cog 본체 ============

class NetmarbleWatcher(commands.Cog):
    """길드별로 알림 채널·보드(URL)·주기를 저장하고 새 글만 공지"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data: Dict[str, Any] = load_data()
        self._playwright = None
        self._browser = None
        # 루프 주기 설정
        self.watch_loop.change_interval(minutes=max(1, self._get_interval()))
        self._started = False

    # --- 내부 ---
    def _get_interval(self) -> int:
        # 파일에 저장된 전역 주기(없으면 기본값)
        # (간단화를 위해 전역 1개 주기만 운영)
        for _gid, g in self.data.items():
            try:
                return int(g.get("interval_min", DEFAULT_INTERVAL_MIN))
            except Exception:
                continue
        return DEFAULT_INTERVAL_MIN

    

    async def _send_tail_for_guild(self, guild: discord.Guild) -> int:
        gid = str(guild.id)
        g = self.data.setdefault(gid, {})
    
        # 여러 채널 지원 코드 쓰는 경우
        channel_ids: List[int] = g.get("channel_ids") or []
        legacy = g.get("channel_id")
        if legacy and legacy not in channel_ids:
            channel_ids.append(legacy)
            g["channel_ids"] = channel_ids
    
        if not channel_ids:
            return 0
    
        dest_channels: List[discord.abc.MessageableChannel] = []
        for cid in channel_ids:
            ch = guild.get_channel(cid)
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                dest_channels.append(ch)
        if not dest_channels:
            return 0
    
        boards: List[Dict[str, Any]] = g.get("boards", [])
        if not boards:
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
    
                # ✅ 목록의 맨 마지막(가장 아래) 아이템 선택
                tail = uniq[-1]
    
                # 상태(last_id)는 변경하지 않음 (테스트 전용)
                embed = discord.Embed(
                    title=f"[{name}] (테스트) 목록의 마지막 글",
                    description=f"**{tail['title']}**",
                    url=tail["url"],
                    timestamp=datetime.now(timezone.utc),
                    color=discord.Color.magenta(),
                )
                for ch in dest_channels:
                    try:
                        await ch.send(embed=embed)
                    except Exception:
                        continue
                sent += 1
            except Exception:
                continue
        return sent

    async def _ensure_playwright(self):
        if not PLAYWRIGHT_OK:
            return
        if self._playwright and self._browser:
            return
        self._playwright = await async_playwright().start()
        # --no-sandbox: 일부 PaaS에서 필요
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

    def cog_unload(self):
        if self.watch_loop.is_running():
            self.watch_loop.cancel()
        asyncio.create_task(self._close_playwright())

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._started:
            self._started = True
            if not self.watch_loop.is_running():
                self.watch_loop.start()

    # ============ 설정 명령(프리픽스) ============
    @commands.command(name="공지채널설정")
    async def set_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """사용법: !공지채널설정 #채널 (생략 시 현재 채널)"""
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        if channel is None:
            channel = ctx.channel
        g["channel_id"] = channel.id
        save_data(self.data)
        await ctx.send(f"✅ 알림 채널을 {channel.mention} 로 설정했어요.")

    @commands.command(name="테스트마지막")
    async def test_tail(self, ctx: commands.Context):
        """각 보드의 현재 목록에서 '맨 마지막 글'을 1회 공지(상태 갱신 안함)"""
        await ctx.send("🧪 각 보드의 '마지막 글'을 테스트로 보내요(상태 미갱신)…")
        n = await self._send_tail_for_guild(ctx.guild)
        await ctx.send(f"✅ 테스트 완료: {n}개 보드에서 마지막 글 전송")


    @commands.command(name="보드추가")
    async def add_board(self, ctx: commands.Context, name: str, url: str):
        """사용법: !보드추가 공지사항 https://forum.netmarble.com/sena_rebirth/list/10/1"""
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        boards: List[Dict[str, Any]] = g.setdefault("boards", [])
        for b in boards:
            if b["name"] == name:
                b["url"] = url
                save_data(self.data)
                await ctx.send(f"🔁 보드 ‘{name}’ URL을 갱신했습니다.")
                return
        boards.append({"name": name, "url": url, "last_id": ""})
        save_data(self.data)
        await ctx.send(f"➕ 보드 ‘{name}’를 추가했습니다.")

    @commands.command(name="보드제거")
    async def remove_board(self, ctx: commands.Context, name: str):
        """사용법: !보드제거 공지사항"""
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        boards: List[Dict[str, Any]] = g.setdefault("boards", [])
        n0 = len(boards)
        boards[:] = [b for b in boards if b["name"] != name]
        save_data(self.data)
        if len(boards) < n0:
            await ctx.send(f"🗑️ 보드 ‘{name}’를 제거했습니다.")
        else:
            await ctx.send(f"⚠️ 보드 ‘{name}’를 찾지 못했습니다.")

    @commands.command(name="보드목록")
    async def list_boards(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        g = self.data.get(gid, {})
        channel_id = g.get("channel_id")
        boards = g.get("boards", [])
        interval_min = g.get("interval_min", self._get_interval())
        lines = [
            f"📢 채널: {('<#'+str(channel_id)+'>') if channel_id else '미설정'}",
            f"⏱️ 주기: {interval_min}분",
            "📚 보드 목록:",
        ]
        if not boards:
            lines.append("- (없음)  ➜ `!보드추가 공지사항 <URL>` 형태로 추가하세요")
        else:
            for b in boards:
                lines.append(f"- {b['name']}: {b.get('url') or '(URL 미설정)'}")
        await ctx.send("\n".join(lines))

    @commands.command(name="감시주기")
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """사용법: !감시주기 3 (분 단위, 전역 주기)"""
        if minutes < 1:
            minutes = 1
        # 전 길드 공통 전역 주기로 저장(단순화)
        for _gid, g in self.data.items():
            g["interval_min"] = minutes
        # 길드 데이터가 없을 수도 있으니 최소 한 곳에 기록
        gid = str(ctx.guild.id)
        self.data.setdefault(gid, {}).setdefault("interval_min", minutes)
        save_data(self.data)

        self.watch_loop.change_interval(minutes=minutes)
        await ctx.send(f"⏲️ 감시 주기를 {minutes}분으로 설정했습니다.")

    @commands.command(name="감시즉시")
    async def run_now(self, ctx: commands.Context):
        """즉시 감시 1회"""
        await ctx.send("🔎 즉시 감시를 시작합니다...")
        n = await self._run_once_for_guild(ctx.guild)
        await ctx.send(f"✅ 즉시 감시 완료: {n}개 보드 확인")

    # ============ 주기적 감시 루프 ============

    @tasks.loop(minutes=DEFAULT_INTERVAL_MIN)
    async def watch_loop(self):
        for guild in list(self.bot.guilds):
            try:
                await self._run_once_for_guild(guild)
            except Exception as e:
                log.info(f"[watch_loop] guild={guild.id} error: {e}")

    async def _scrape_with_browser(self, url: str) -> List[Dict[str, str]]:
        await self._ensure_playwright()
        if not PLAYWRIGHT_OK or not self._browser:
            return []
    
        m = BOARD_ID_RE.search(url)
        board_id = m.group(1) if m else None
    
        page = await self._browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64)")
        try:
            await page.goto(url, wait_until="networkidle", timeout=20000)
    
            # 목록 렌더 완료 대기: 해당 보드의 view 링크가 최소 1개는 나타날 때까지
            sel = f'a[href^="/sena_rebirth/view/{board_id}/"]' if board_id else 'a[href*="/sena_rebirth/view/"]'
            try:
                await page.wait_for_selector(sel, timeout=8000)
            except Exception:
                # 첫 대기가 실패하면 약간 더 기다려 본다
                await page.wait_for_timeout(1200)
    
            # 정확 셀렉터로 바로 수집 (불필요한 a 태그 제거)
            anchors = await page.eval_on_selector_all(
                sel,
                """els => els.map(a => ({href: a.getAttribute('href') || '', text: (a.textContent||'').trim()}))"""
            )
    
            # 절대 URL로 정규화 + 필터
            out, seen = [], set()
            for a in anchors:
                href = (a.get("href") or "").strip()
                text = (a.get("text") or "").strip()
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://forum.netmarble.com{href}"
                if not VIEW_LINK_RE.match(href):
                    continue
                if href in seen:
                    continue
                out.append({"id": href, "title": text or href, "url": href})
                seen.add(href)
                if len(out) >= 20:
                    break
    
            # 디버그: 몇 개 잡혔는지 남겨두면 문제 파악 쉬움
            log.info(f"[watcher] browser items for {url} -> {len(out)} links")
    
            return out
        finally:
            await page.close()


    async def _scrape_with_http(self, url: str) -> List[Dict[str, str]]:
        m = BOARD_ID_RE.search(url)
        board_id = m.group(1) if m else None
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
                r = await client.get(url, follow_redirects=True)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
    
                # 보수적으로 전역에서 a를 모으되, href 패턴으로 강하게 걸러낸다
                out, seen = [], set()
                for a in soup.select(f'a[href^="/sena_rebirth/view/{board_id}/"]' if board_id else 'a[href*="/sena_rebirth/view/"]'):
                    href = (a.get("href") or "").strip()
                    text = (a.get_text(strip=True) or "")
                    if href.startswith("/"):
                        href = f"https://forum.netmarble.com{href}"
                    if not VIEW_LINK_RE.match(href):
                        continue
                    if href in seen:
                        continue
                    out.append({"id": href, "title": text or href, "url": href})
                    seen.add(href)
                    if len(out) >= 20:
                        break
    
                log.info(f"[watcher] http items for {url} -> {len(out)} links")
                return out
        except Exception:
            return []



    async def _fetch_items(self, url: str) -> List[Dict[str, str]]:
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
                new_items: List[Dict[str, str]] = []
                ids = [x["id"] for x in uniq]

                if last_id and last_id in ids:
                    for it in reversed(uniq):
                        if it["id"] == last_id:
                            break
                        new_items.append(it)
                else:
                    # 초기엔 맨 위 1개만(기존글 폭탄 방지)
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
                log.info(f"[watch] guild={gid} board={name} err={e}")
                continue
        return sent

# 확장 로더 진입점
async def setup(bot: commands.Bot):
    await bot.add_cog(NetmarbleWatcher(bot))
