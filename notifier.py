# notifier.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

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


async def fetch_latest_youtube_video(source: Dict[str, Any]) -> Optional[Dict[str, str]]:
    channel_id = source.get("channel_id")

    if not channel_id:
        logger.warning("[YOUTUBE] channel_id 없음: %s", source.get("id"))
        return None
        
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

    logger.info("[YOUTUBE] feed_url=%s", feed_url)

    if not feed_url:
        logger.warning("[YOUTUBE] feed_url 생성 실패: %s", source.get("id"))
        return None

    parsed = feedparser.parse(feed_url)

    logger.info(
        "[YOUTUBE] feed 파싱 결과: entries=%s, bozo=%s",
        len(parsed.entries),
        getattr(parsed, "bozo", None),
    )

    if getattr(parsed, "bozo", False):
        logger.warning("[YOUTUBE] feed 파싱 오류: %s", getattr(parsed, "bozo_exception", None))

    if not parsed.entries:
        logger.warning("[YOUTUBE] feed entries 비어있음")
        return None

    entry = parsed.entries[0]

    logger.info(
        "[YOUTUBE] 첫 번째 entry keys=%s",
        list(entry.keys())
    )

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
    
        logger.info("[YOUTUBE] 체크 시작: %s", source_id)
    
        latest = await fetch_latest_youtube_video(source)
    
        if not latest:
            logger.warning("[YOUTUBE] 최신 영상 없음 또는 피드 파싱 실패: %s", source_id)
            return
    
        logger.info(
            "[YOUTUBE] 최신 영상 감지: source=%s, id=%s, title=%s, url=%s, published=%s",
            source_id,
            latest.get("id"),
            latest.get("title"),
            latest.get("url"),
            latest.get("published"),
        )
    
        if not latest["id"]:
            logger.warning("[YOUTUBE] 영상 ID 없음: %s / %s", source_id, latest)
            return
    
        last_seen_id = self.state.get(source_id, {}).get("last_seen_id")
    
        logger.info(
            "[YOUTUBE] 비교: source=%s, last_seen_id=%s, latest_id=%s",
            source_id,
            last_seen_id,
            latest["id"],
        )
    
        # 첫 실행 때는 알림 폭탄 방지: 저장만 하고 보내지 않음
        if not last_seen_id:
            self.state[source_id] = {
                "last_seen_id": latest["id"],
                "last_seen_title": latest["title"],
            }
            save_json(STATE_PATH, self.state)
    
            logger.info("[YOUTUBE] 첫 실행이라 알림 없이 상태만 저장: %s", latest["title"])
            return
    
        if latest["id"] == last_seen_id:
            logger.info("[YOUTUBE] 새 영상 없음: %s", source_id)
            return
    
        channel_id = int(source["discord_channel_id"])
        logger.info("[YOUTUBE] 디스코드 채널 조회: %s", channel_id)
    
        channel = self.bot.get_channel(channel_id)
    
        if channel is None:
            logger.info("[YOUTUBE] get_channel 실패, fetch_channel 시도: %s", channel_id)
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
    
        logger.info("[YOUTUBE] 알림 전송 시도: %s", message)
    
        await channel.send(message)
    
        self.state[source_id] = {
            "last_seen_id": latest["id"],
            "last_seen_title": latest["title"],
        }
        save_json(STATE_PATH, self.state)
    
        logger.info("[YOUTUBE] 알림 전송 완료: %s", latest["title"])
