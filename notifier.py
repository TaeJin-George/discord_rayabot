# notifier.py
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
import discord
import feedparser
from discord.ext import tasks

logger = logging.getLogger("notifier")

CONFIG_PATH = Path("notifiers.json")
STATE_PATH = Path("notifier_state.json")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("JSON 로드 실패: %s", path)
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def resolve_youtube_channel_id(handle_url: str) -> Optional[str]:
    """
    https://www.youtube.com/@sena_rebirth 같은 핸들 URL에서 UC... 채널 ID를 추출.
    """
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(handle_url, timeout=15) as resp:
            html = await resp.text()

    patterns = [
        r'<meta itemprop="channelId" content="([^"]+)"',
        r'"channelId":"(UC[^"]+)"',
        r'https://www\.youtube\.com/channel/(UC[^"?/]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            value = match.group(1)
            return value if value.startswith("UC") else f"UC{value}"

    return None


async def get_youtube_feed_url(source: Dict[str, Any]) -> Optional[str]:
    if source.get("feed_url"):
        return source["feed_url"]

    if source.get("channel_id"):
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={source['channel_id']}"

    handle_url = source.get("handle_url")
    if not handle_url:
        return None

    channel_id = await resolve_youtube_channel_id(handle_url)
    if not channel_id:
        return None

    source["channel_id"] = channel_id
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


async def fetch_latest_youtube_video(source: Dict[str, Any]) -> Optional[Dict[str, str]]:
    feed_url = await get_youtube_feed_url(source)
    if not feed_url:
        logger.warning("유튜브 feed_url 생성 실패: %s", source.get("id"))
        return None

    parsed = feedparser.parse(feed_url)

    if not parsed.entries:
        return None

    entry = parsed.entries[0]

    return {
        "id": entry.get("yt_videoid") or entry.get("id", ""),
        "title": entry.get("title", "제목 없음"),
        "url": entry.get("link", ""),
        "published": entry.get("published", ""),
    }


class NotifierManager:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.config = load_json(CONFIG_PATH, {"check_interval_seconds": 300, "sources": []})
        self.state = load_json(STATE_PATH, {})

    async def start(self) -> None:
        interval = int(self.config.get("check_interval_seconds", 300))
        self.check_sources.change_interval(seconds=interval)

        if not self.check_sources.is_running():
            self.check_sources.start()

        logger.info("알림 체크 시작: %s초 간격", interval)

    def cog_unload(self) -> None:
        self.check_sources.cancel()

    @tasks.loop(seconds=300)
    async def check_sources(self) -> None:
        for source in self.config.get("sources", []):
            if not source.get("enabled", True):
                continue

            source_type = source.get("type")

            try:
                if source_type == "youtube":
                    await self.check_youtube(source)
                else:
                    logger.warning("지원하지 않는 알림 타입: %s", source_type)

            except Exception:
                logger.exception("알림 체크 실패: %s", source.get("id"))

        save_json(CONFIG_PATH, self.config)
        save_json(STATE_PATH, self.state)

    @check_sources.before_loop
    async def before_check_sources(self) -> None:
        await self.bot.wait_until_ready()

    async def check_youtube(self, source: Dict[str, Any]) -> None:
        source_id = source["id"]
        latest = await fetch_latest_youtube_video(source)

        if not latest or not latest["id"]:
            return

        last_seen_id = self.state.get(source_id, {}).get("last_seen_id")

        # 첫 실행 때는 알림 폭탄 방지: 저장만 하고 보내지 않음
        if not last_seen_id:
            self.state[source_id] = {
                "last_seen_id": latest["id"],
                "last_seen_title": latest["title"],
            }
            logger.info("초기 유튜브 영상 저장: %s / %s", source_id, latest["title"])
            return

        if latest["id"] == last_seen_id:
            return

        channel_id = int(source["discord_channel_id"])
        channel = self.bot.get_channel(channel_id)

        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)

        template = source.get(
            "message_template",
            "📺 **{source_name} 새 영상 업로드!**\n{title}\n{url}"
        )

        message = template.format(
            source_name=source.get("name", "유튜브"),
            title=latest["title"],
            url=latest["url"],
            published=latest.get("published", ""),
        )

        await channel.send(message)

        self.state[source_id] = {
            "last_seen_id": latest["id"],
            "last_seen_title": latest["title"],
        }

        logger.info("유튜브 새 영상 알림 전송: %s", latest["title"])
