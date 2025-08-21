# netmarble_watcher.py (Playwright-free)
import os, json, asyncio, re, logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union

import discord
from discord.ext import commands, tasks

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("netmarble")

DATA_PATH = "nm_watcher_data.json"
DEFAULT_INTERVAL_MIN = int(os.getenv("WATCH_INTERVAL_MIN", "3"))

# /list/{boardId}/... 에서 boardId 추출
BOARD_ID_RE = re.compile(r"/list/(\d+)/")
# view 링크(쿼리/프래그먼트 허용)
VIEW_LINK_RE = re.compile(r"^https?://forum\.netmarble\.com/sena_rebirth/view/\d+/\d+(?:[?#].*)?$")
# 절대 URL이 아니어도 찾기 위한 백업
VIEW_PATH_RE = re.compile(r"/sena_rebirth/view/\d+/\d+")

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

class NetmarbleWatcher(commands.Cog):
    """넷마블 포럼(list/{board}/1) 목록에서 view/{board}/{postId}를 추출해 새 글만 공지."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data: Dict[str, Any] = load_data()
        self.watch_loop.change_interval(minutes=max(1, self._get_interval()))
        self._started = False

    def _get_interval(self) -> int:
        for _gid, g in self.data.items():
            try:
                return int(g.get("interval_min", DEFAULT_INTERVAL_MIN))
            except Exception:
                pass
        return DEFAULT_INTERVAL_MIN

    def cog_unload(self):
        if self.watch_loop.is_running():
            self.watch_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._started:
            self._started = True
            if not self.watch_loop.is_running():
                self.watch_loop.start()

    # ===== 설정 명령 =====
    @commands.command(name="공지채널설정")
    async def set_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        if channel is None:
            channel = ctx.channel
        # 여러 채널 지원 목록 방식
        chs = g.setdefault("channel_ids", [])
        if channel.id not in chs:
            chs.append(channel.id)
        # 레거시 키 제거(선택)
        g.pop("channel_id", None)
        save_data(self.data)
        await ctx.send(f"✅ 공지 채널 추가: {channel.mention}")

    @commands.command(name="공지채널추가")
    async def add_notify_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        if channel is None:
            channel = ctx.channel
        chs = g.setdefault("channel_ids", [])
        if channel.id in chs:
            await ctx.send(f"ℹ️ 이미 등록됨: {channel.mention}")
            return
        chs.append(channel.id)
        save_data(self.data)
        await ctx.send(f"➕ 공지 채널 추가: {channel.mention}")

    @commands.command(name="공지채널제거")
    async def remove_notify_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        if channel is None:
            channel = ctx.channel
        chs = g.setdefault("channel_ids", [])
        if channel.id not in chs:
            await ctx.send(f"⚠️ 등록되지 않음: {channel.mention}")
            return
        chs[:] = [cid for cid in chs if cid != channel.id]
        save_data(self.data)
        await ctx.send(f"🗑️ 공지 채널 제거: {channel.mention}")

    @commands.command(name="공지채널목록")
    async def list_notify_channels(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        g = self.data.get(gid, {})
        chs = g.get("channel_ids") or []
        if not chs:
            await ctx.send("📢 공지 채널: (없음) → `!공지채널설정` 또는 `!공지채널추가`")
            return
        labels = []
        for cid in chs:
            ch = ctx.guild.get_channel(cid)
            labels.append(ch.mention if ch else f"<#{cid}>")
        await ctx.send("📢 공지 채널들:\n- " + "\n- ".join(labels))

    @commands.command(name="보드추가")
    async def add_board(self, ctx: commands.Context, name: str, url: str):
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        boards: List[Dict[str, Any]] = g.setdefault("boards", [])
        for b in boards:
            if b["name"] == name:
                b["url"] = url
                save_data(self.data)
                await ctx.send(f"🔁 보드 ‘{name}’ URL 갱신")
                return
        boards.append({"name": name, "url": url, "last_id": ""})
        save_data(self.data)
        await ctx.send(f"➕ 보드 ‘{name}’ 추가")

    @commands.command(name="보드제거")
    async def remove_board(self, ctx: commands.Context, name: str):
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        boards: List[Dict[str, Any]] = g.setdefault("boards", [])
        n0 = len(boards)
        boards[:] = [b for b in boards if b["name"] != name]
        save_data(self.data)
        await ctx.send("🗑️ 제거 완료" if len(boards) < n0 else "⚠️ 해당 보드를 찾지 못함")

    @commands.command(name="보드목록")
    async def list_boards(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        g = self.data.get(gid, {})
        chs = g.get("channel_ids") or []
        interval_min = g.get("interval_min", self._get_interval())
        boards = g.get("boards", [])
        chlabels = []
        for cid in chs:
            ch = ctx.guild.get_channel(cid)
            chlabels.append(ch.mention if ch else f"<#{cid}>")
        lines = [
            f"📢 채널들: {', '.join(chlabels) if chlabels else '미설정'}",
            f"⏱️ 주기: {interval_min}분",
            "📚 보드 목록:",
        ]
        if not boards:
            lines.append("- (없음)  → `!보드추가 공지사항 <URL>`")
        else:
            for b in boards:
                lines.append(f"- {b['name']}: {b.get('url') or '(URL 미설정)'}")
        await ctx.send("\n".join(lines))

    @commands.command(name="감시주기")
    async def set_interval(self, ctx: commands.Context, minutes: int):
        if minutes < 1:
            minutes = 1
        for _gid, g in self.data.items():
            g["interval_min"] = minutes
        gid = str(ctx.guild.id)
        self.data.setdefault(gid, {}).setdefault("interval_min", minutes)
        save_data(self.data)
        self.watch_loop.change_interval(minutes=minutes)
        await ctx.send(f"⏲️ {minutes}분으로 설정")

    @commands.command(name="감시즉시")
    async def run_now(self, ctx: commands.Context):
        await ctx.send("🔎 즉시 감시…")
        n = await self._run_once_for_guild(ctx.guild)
        await ctx.send(f"✅ 완료: {n}개 보드 확인")

    # 디버그: 현재 감지 링크 덤프
    @commands.command(name="디버그링크")
    async def debug_links(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        g = self.data.setdefault(gid, {})
        boards = g.get("boards", [])
        if not boards:
            await ctx.send("보드가 없습니다. `!보드추가` 먼저")
            return
        for b in boards:
            name = b.get("name") or "탭"
            url = b.get("url") or ""
            items = await self._fetch_items(url)
            if not items:
                await ctx.send(f"[{name}] 0개")
            else:
                head = "\n".join(f"- {it['url']}" for it in items[:5])
                await ctx.send(f"[{name}] {len(items)}개 (상위5)\n{head}")

    @commands.command(name="테스트마지막")
    async def test_tail(self, ctx: commands.Context):
        await ctx.send("🧪 각 보드의 '마지막 글'을 테스트로 전송 (상태 미갱신)…")
        n = await self._send_tail_for_guild(ctx.guild)
        if n == 0:
            await ctx.send("⚠️ 테스트 실패: 링크를 못 찾았거나 채널이 미설정")
        else:
            await ctx.send(f"✅ 테스트 완료: {n}개 보드")

    # ===== 루프 =====
    @tasks.loop(minutes=DEFAULT_INTERVAL_MIN)
    async def watch_loop(self):
        for guild in list(self.bot.guilds):
            try:
                await self._run_once_for_guild(guild)
            except Exception as e:
                log.warning(f"[loop] guild={guild.id} err={e}")

    # ===== 크롤 =====
    async def _fetch_items(self, url: str) -> List[Dict[str, str]]:
        """list/{board}/1에서 view/{board}/{post} 링크들을 수집"""
        try:
            m = BOARD_ID_RE.search(url)
            board_id = m.group(1) if m else None
            async with httpx.AsyncClient(timeout=15, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": url,
            }) as client:
                r = await client.get(url, follow_redirects=True)
                r.raise_for_status()
                html = r.text
                items: List[Dict[str,str]] = []

                # 1차: a[href] 셀렉터로 수집
                soup = BeautifulSoup(html, "html.parser")
                seen = set()
                sel = f'a[href^="/sena_rebirth/view/{board_id}/"]' if board_id else 'a[href*="/sena_rebirth/view/"]'
                for a in soup.select(sel):
                    href = (a.get("href") or "").strip()
                    title = (a.get_text(strip=True) or "")
                    if href.startswith("/"):
                        href = f"https://forum.netmarble.com{href}"
                    if not VIEW_LINK_RE.match(href) or href in seen:
                        continue
                    items.append({"id": href, "title": title or href, "url": href})
                    seen.add(href)
                    if len(items) >= 30:
                        break

                # 2차: 정규식 백업 스캔
                if not items:
                    paths = VIEW_PATH_RE.findall(html)
                    for p in paths:
                        if board_id and f"/view/{board_id}/" not in p:
                            continue
                        href = f"https://forum.netmarble.com{p}"
                        if not VIEW_LINK_RE.match(href) or href in seen:
                            continue
                        items.append({"id": href, "title": href, "url": href})
                        seen.add(href)
                        if len(items) >= 30:
                            break

                log.info(f"[fetch] {url} -> {len(items)} links")
                return items
        except Exception as e:
            log.warning(f"[fetch] fail {url}: {e}")
            return []

    async def _dest_channels(self, guild: discord.Guild) -> List[Union[discord.TextChannel, discord.Thread]]:
        gid = str(guild.id)
        g = self.data.setdefault(gid, {})
        chs: List[int] = g.get("channel_ids") or []
        # 레거시 이관
        legacy = g.get("channel_id")
        if legacy and legacy not in chs:
            chs.append(legacy)
            g["channel_ids"] = chs
            g.pop("channel_id", None)
            save_data(self.data)
        dest = []
        for cid in chs:
            ch = guild.get_channel(cid)
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                dest.append(ch)
        return dest

    async def _send_tail_for_guild(self, guild: discord.Guild) -> int:
        dest = await self._dest_channels(guild)
        if not dest:
            return 0
        gid = str(guild.id)
        g = self.data.setdefault(gid, {})
        boards: List[Dict[str, Any]] = g.get("boards", [])
        if not boards:
            return 0
        sent = 0
        for b in boards:
            url = b.get("url") or ""
            name = b.get("name") or "탭"
            items = await self._fetch_items(url)
            if not items:
                continue
            tail = items[-1]
            embed = discord.Embed(
                title=f"[{name}] (테스트) 목록의 마지막 글",
                description=f"**{tail['title']}**",
                url=tail["url"],
                timestamp=datetime.now(timezone.utc),
                color=discord.Color.magenta(),
            )
            for ch in dest:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass
            sent += 1
        return sent

    async def _run_once_for_guild(self, guild: discord.Guild) -> int:
        dest = await self._dest_channels(guild)
        if not dest:
            return 0
        gid = str(guild.id)
        g = self.data.setdefault(gid, {})
        boards: List[Dict[str, Any]] = g.get("boards", [])
        if not boards:
            return 0

        sent = 0
        for b in boards:
            name = b.get("name") or "탭"
            url = b.get("url") or ""
            if not url: 
                continue
            uniq = await self._fetch_items(url)
            if not uniq:
                continue

            last_id = b.get("last_id", "")
            ids = [x["id"] for x in uniq]
            if last_id and last_id in ids:
                new_items = []
                for it in reversed(uniq):
                    if it["id"] == last_id:
                        break
                    new_items.append(it)
            else:
                new_items = [uniq[0]]  # 초기엔 1개만

            for it in new_items:
                embed = discord.Embed(
                    title=f"[{name}] 새 글",
                    description=f"**{it['title']}**",
                    url=it["url"],
                    timestamp=datetime.now(timezone.utc),
                    color=discord.Color.blue(),
                )
                for ch in dest:
                    try:
                        await ch.send(embed=embed)
                    except Exception:
                        pass
                sent += 1

            b["last_id"] = uniq[0]["id"]
            save_data(self.data)

        return sent

async def setup(bot: commands.Bot):
    await bot.add_cog(NetmarbleWatcher(bot))
