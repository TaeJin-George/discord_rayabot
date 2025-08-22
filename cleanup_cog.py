# cleanup_cog.py
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import discord
from discord.ext import commands, tasks

DATA_PATH = os.getenv("CLEANUP_CONFIG_PATH", "cleanup_config.json")
DEFAULT_INTERVAL_MIN = int(os.getenv("CLEANUP_INTERVAL_MIN", "10"))
# 자동 청소 1회에 개별 삭제 상한 (레이트리밋 보호)
AUTO_DELETE_CAP = int(os.getenv("CLEANUP_AUTO_DELETE_CAP", "300"))

KST = timezone(timedelta(hours=9))

def load_config() -> Dict[str, Any]:
    if not os.path.exists(DATA_PATH):
        return {"interval_min": DEFAULT_INTERVAL_MIN, "channels": {}}
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(DATA_PATH) or ".", exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def is_manager():
    async def predicate(ctx: commands.Context):
        # 관리자 권한 또는 메시지관리 권한 보유자만 허용
        perms = ctx.channel.permissions_for(ctx.author)
        if perms.manage_messages or perms.administrator:
            return True
        await ctx.reply("이 명령을 사용할 권한이 없습니다(메시지 관리 권한 필요).", mention_author=False)
        return False
    return commands.check(predicate)

class ConfirmClearView(discord.ui.View):
    def __init__(self, author_id: int, channel: discord.TextChannel, timeout: float = 30):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.channel = channel
        self.result = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("명령어 실행자만 확인할 수 있어요.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="모두 삭제(핀 제외)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.result = True
        self.stop()

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.result = False
        self.stop()

class CleanupCog(commands.Cog):
    """채팅방 정리(수동/자동) 기능"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = load_config()
        self.auto_cleanup_loop.change_interval(minutes=self.cfg.get("interval_min", DEFAULT_INTERVAL_MIN))
        self.auto_cleanup_loop.start()

    def cog_unload(self):
        self.auto_cleanup_loop.cancel()

    # -----------------------
    # 유틸
    # -----------------------
    @staticmethod
    def _skip_pinned(msg: discord.Message) -> bool:
        return not msg.pinned

    @staticmethod
    def _is_bot(msg: discord.Message) -> bool:
        return not msg.pinned and msg.author.bot

    @staticmethod
    def _is_from(user: discord.User):
        def inner(msg: discord.Message) -> bool:
            return not msg.pinned and msg.author.id == user.id
        return inner

    @staticmethod
    def _is_from_author(ctx: commands.Context):
        def inner(msg: discord.Message) -> bool:
            return not msg.pinned and msg.author.id == ctx.author.id
        return inner

    # -----------------------
    # 수동 정리 명령들
    # -----------------------
    @commands.command(name="청소", help="최근 N개 메시지 삭제(핀 제외). 예: !청소 100")
    @is_manager()
    @commands.guild_only()
    async def purge_any(self, ctx: commands.Context, count: int):
        if count < 1:
            return await ctx.reply("1 이상의 개수를 입력해 주세요.", mention_author=False)
        # bulk=True: 14일 이하 메시지에 대해 빠른 일괄삭제
        deleted = await ctx.channel.purge(limit=count, check=self._skip_pinned, bulk=True)
        await ctx.send(f"🧹 {len(deleted)}개 메시지 삭제(핀 제외).", delete_after=5)

    @commands.command(name="청소봇", help="최근 N개 중 봇 메시지 삭제. 예: !청소봇 200")
    @is_manager()
    @commands.guild_only()
    async def purge_bots(self, ctx: commands.Context, count: int):
        deleted = await ctx.channel.purge(limit=count, check=self._is_bot, bulk=True)
        await ctx.send(f"🤖 봇 메시지 {len(deleted)}개 삭제.", delete_after=5)

    @commands.command(name="청소내", help="최근 N개 중 나의 메시지 삭제. 예: !청소내 50")
    @is_manager()
    @commands.guild_only()
    async def purge_mine(self, ctx: commands.Context, count: int):
        deleted = await ctx.channel.purge(limit=count, check=self._is_from_author(ctx), bulk=True)
        await ctx.send(f"🙋 내 메시지 {len(deleted)}개 삭제.", delete_after=5)

    @commands.command(name="청소유저", help="최근 N개 중 특정 유저의 메시지 삭제. 예: !청소유저 @닉 100")
    @is_manager()
    @commands.guild_only()
    async def purge_user(self, ctx: commands.Context, member: discord.Member, count: int):
        deleted = await ctx.channel.purge(limit=count, check=self._is_from(member), bulk=True)
        await ctx.send(f"👤 {member.display_name}님의 메시지 {len(deleted)}개 삭제.", delete_after=5)

    @commands.command(name="청소전체", help="채널의 모든 메시지 삭제(핀 제외). 버튼 확인 필요.")
    @is_manager()
    @commands.guild_only()
    async def purge_all(self, ctx: commands.Context):
        view = ConfirmClearView(author_id=ctx.author.id, channel=ctx.channel)
        msg = await ctx.reply("⚠️ 이 채널의 모든 메시지를 삭제할까요? (핀 제외)", view=view, mention_author=False)
        await view.wait()
        await msg.edit(view=None)

        if view.result is not True:
            return await ctx.send("취소되었습니다.", delete_after=5)

        # 개별삭제 모드: 14일 초과 메시지도 처리 가능(느림)
        deleted_count = 0
        async for m in ctx.channel.history(limit=None, oldest_first=False):
            if m.pinned:
                continue
            try:
                await m.delete()
                deleted_count += 1
            except discord.HTTPException:
                pass
        await ctx.send(f"🧨 총 {deleted_count}개 메시지 삭제 완료.", delete_after=5)

    # -----------------------
    # 자동 정리 설정
    # -----------------------
    @commands.group(name="청소설정", invoke_without_command=True, help="자동 청소 설정 관리")
    @is_manager()
    @commands.guild_only()
    async def clean_group(self, ctx: commands.Context):
        await ctx.reply("하위 명령: `추가`, `간격`, `목록`, `제거`", mention_author=False)

    @clean_group.command(name="추가", help="자동 청소 등록: 보존개수 또는 보존시간(시간) 중 하나 지정")
    async def add_rule(self, ctx: commands.Context, 보존개수: Optional[int] = None, 보존시간: Optional[int] = None):
        if (보존개수 is None) == (보존시간 is None):
            return await ctx.reply("`보존개수` 또는 `보존시간(시간)` 중 하나만 지정하세요. 예) `!청소설정 추가  보존개수=200` 또는 `!청소설정 추가  보존시간=48`", mention_author=False)

        ch_id = str(ctx.channel.id)
        self.cfg["channels"].setdefault(ch_id, {"enabled": True})
        if 보존개수 is not None:
            self.cfg["channels"][ch_id].update({"keep_last": int(보존개수), "max_age_hours": None, "enabled": True})
            msg = f"이 채널 자동 청소 등록: 최근 {보존개수}개만 보존."
        else:
            self.cfg["channels"][ch_id].update({"keep_last": None, "max_age_hours": int(보존시간), "enabled": True})
            msg = f"이 채널 자동 청소 등록: {보존시간}시간 초과 메시지 삭제."

        save_config(self.cfg)
        await ctx.reply(f"✅ {msg}", mention_author=False)

    @clean_group.command(name="간격", help="자동 청소 간격(분) 설정. 예: !청소설정 간격 15")
    async def set_interval(self, ctx: commands.Context, 분: int):
        if 분 < 1:
            return await ctx.reply("1분 이상으로 설정하세요.", mention_author=False)
        self.cfg["interval_min"] = 분
        save_config(self.cfg)
        self.auto_cleanup_loop.change_interval(minutes=분)
        await ctx.reply(f"⏱️ 자동 청소 주기를 {분}분으로 설정했어요.", mention_author=False)

    @clean_group.command(name="목록", help="현재 자동 청소 설정 보기")
    async def list_rules(self, ctx: commands.Context):
        ch_id = str(ctx.channel.id)
        rule = self.cfg["channels"].get(ch_id)
        if not rule:
            return await ctx.reply("이 채널은 자동 청소가 설정되어 있지 않습니다.", mention_author=False)
        if not rule.get("enabled", True):
            state = "OFF"
        else:
            state = "ON"
        keep_last = rule.get("keep_last")
        max_age = rule.get("max_age_hours")
        desc = f"상태: {state}\n"
        if keep_last:
            desc += f"- 보존 개수: 최근 {keep_last}개\n"
        if max_age:
            desc += f"- 보존 시간: {max_age}시간 초과 삭제\n"
        desc += f"- 주기: {self.cfg.get('interval_min', DEFAULT_INTERVAL_MIN)}분"
        await ctx.reply(f"📋 자동 청소 설정\n{desc}", mention_author=False)

    @clean_group.command(name="제거", help="이 채널의 자동 청소 해제")
    async def remove_rule(self, ctx: commands.Context):
        ch_id = str(ctx.channel.id)
        if self.cfg["channels"].pop(ch_id, None):
            save_config(self.cfg)
            await ctx.reply("🗑️ 자동 청소 설정 제거됨.", mention_author=False)
        else:
            await ctx.reply("이 채널은 자동 청소가 설정되어 있지 않아요.", mention_author=False)

    # 수정 (충돌 해결)
    @commands.command(name="청소on", help="이 채널 자동 청소 켜기")
    @is_manager()
    async def enable_auto(self, ctx: commands.Context):
        ch_id = str(ctx.channel.id)
        rule = self.cfg["channels"].get(ch_id)
        if not rule:
            return await ctx.reply("먼저 `!청소설정 추가`로 규칙을 등록하세요.", mention_author=False)
        rule["enabled"] = True
        save_config(self.cfg)
        await ctx.reply("✅ 자동 청소 ON", mention_author=False)

    @commands.command(name="청소off", help="이 채널 자동 청소 끄기")
    @is_manager()
    async def disable_auto(self, ctx: commands.Context):
        ch_id = str(ctx.channel.id)
        rule = self.cfg["channels"].get(ch_id)
        if not rule:
            return await ctx.reply("이 채널은 자동 청소가 설정되어 있지 않아요.", mention_author=False)
        rule["enabled"] = False
        save_config(self.cfg)
        await ctx.reply("⏹️ 자동 청소 OFF", mention_author=False)

    # -----------------------
    # 자동 청소 루프
    # -----------------------
    @tasks.loop(minutes=DEFAULT_INTERVAL_MIN)
    async def auto_cleanup_loop(self):
        # 길드 여러 곳에서 돌 수 있으니, 설정된 채널만 순회
        for ch_id, rule in list(self.cfg.get("channels", {}).items()):
            if not rule.get("enabled", True):
                continue
            channel = self.bot.get_channel(int(ch_id))
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue

            keep_last = rule.get("keep_last")
            max_age_hours = rule.get("max_age_hours")

            try:
                deleted = 0

                # 1) 보존 개수 규칙: 최근 keep_last를 남기고 나머지 삭제(개별 삭제)
                if keep_last:
                    # history(limit=None)로 전부 가져오되, 최신부터 카운트
                    idx = 0
                    async for msg in channel.history(limit=None, oldest_first=False):
                        if msg.pinned:
                            continue
                        idx += 1
                        if idx <= keep_last:
                            continue
                        try:
                            await msg.delete()
                            deleted += 1
                            if deleted >= AUTO_DELETE_CAP:
                                break
                        except discord.HTTPException:
                            pass

                # 2) 보존 시간 규칙: now - created_at > max_age_hours 삭제
                elif max_age_hours:
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
                    async for msg in channel.history(limit=None, oldest_first=False, before=None):
                        if msg.pinned:
                            continue
                        # created_at은 UTC
                        if msg.created_at < cutoff:
                            try:
                                await msg.delete()
                                deleted += 1
                                if deleted >= AUTO_DELETE_CAP:
                                    break
                            except discord.HTTPException:
                                pass

                if deleted:
                    try:
                        await channel.send(f"🧹 자동 청소: {deleted}개 삭제(핀 제외).", delete_after=5)
                    except discord.HTTPException:
                        pass

            except Exception:
                # 채널 접근 권한/레이트리밋 등으로 에러가 날 수 있음 -> 무시하고 다음 채널
                continue

    @auto_cleanup_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(CleanupCog(bot))

