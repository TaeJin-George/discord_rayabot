
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import traceback
import common

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from common import MIN_STAT_TRIES, _canon_team_key, _join_team_disp, _split_csv_args, _badge_for_item
from counter_store import DataStore
from counter_ui import CounterView, build_stats_embed
from raw_store import RawMatchStore
from notifier import NotifierManager
from crawler import BoardCrawler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("counter-bot")


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")
SHEET_URL_DEFAULT = "https://docs.google.com/spreadsheets/d/PUT_YOUR_ID_HERE/edit?gid=0#gid=0"
RAW_SHEET_GID_DEFAULT = "123456789"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
bot.board_crawler = BoardCrawler()

data_store = DataStore(SHEET_URL_DEFAULT)
raw_store = RawMatchStore(SHEET_URL_DEFAULT, RAW_SHEET_GID_DEFAULT)

data_store.load()
raw_store.load()

notifier_manager = None

@tasks.loop(minutes=3)
async def check_naver_board():
    updates = bot.board_crawler.check_new_posts()

    for update in updates:
        channel = bot.get_channel(update["channel_id"])

        if not channel:
            continue

        board_name = update["board_name"]

        for post in update["posts"]:
            url = f"{bot.board_crawler.detail_url}{post['id']}"

            await channel.send(
                f"📢 **[{board_name}] 새 글이 올라왔어요!**\n"
                f"📝 {post['title']}\n"
                f"{url}"
            )

@bot.event
async def on_ready():
    global notifier_manager


    if notifier_manager is None:
        notifier_manager = NotifierManager(bot)
        await notifier_manager.start()

    if not check_naver_board.is_running():
        check_naver_board.start()
    
    logger.info(f"✅ 로그인 완료: {bot.user} (guilds={len(bot.guilds)})")
    


@bot.command(name="리로드")
async def reload_cmd(ctx: commands.Context):
    try:
        data_store.load()
        raw_store.load()

        problems = []
        if data_store.df is None:
            problems.append("카운터 시트 로드 실패")
        if raw_store.df is None:
            problems.append("raw 시트 로드 실패")

        if problems:
            await ctx.reply("❌ " + " / ".join(problems), mention_author=False)
        else:
            await ctx.reply("✅ 카운터 시트 + raw 시트 리로드 완료", mention_author=False)
    except Exception:
        logger.error("!리로드 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 리로드 중 오류가 발생했어요.", mention_author=False)


@bot.command(name="조합")
async def combo_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 상대 3명만. 예) `!조합 제이브, 카구라, 트루드`", mention_author=False)
            return

        want = _canon_team_key(tokens)
        enemy_disp = ", ".join(want)
        results = data_store.search_by_enemy(list(want))

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 카운터 데이터가 없습니다.\n🎯 상대 조합: `{enemy_disp}`",
                mention_author=False
            )
            return

        lines = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            total = item["win"] + item["lose"]
            combo = ", ".join([x for x in item["counter_disp"] if x]) or "정보 없음"

            star = _badge_for_item(item, i)
            rec_text = "**추천** · " if item.get("recommend") else ""
            lines.append(f"{star}{i}. `{combo}` — {rec_text}**{rate:.0f}%** ({total}판)")

        embed = discord.Embed(
            title="📋 카운터 목록 (추천 우선/승률순)",
            description=f"🎯 상대 조합: `{enemy_disp}`\n\n" + "\n".join(lines),
            color=0xF1C40F
        )

        view = CounterView(enemy_disp, results)
        await ctx.reply(embed=embed, view=view, mention_author=False)

    except Exception:
        logger.error("!조합 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 요청 처리 중 오류가 발생했어요.", mention_author=False)

@bot.command(name="우리공격")
async def my_winrate_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 공격조합 3명. 예) `!우리공격 트루드, 겔리두스, 라드그리드`", mention_author=False)
            return

        target_disp = _join_team_disp(tokens)
        results = raw_store.get_my_attack_winrates(tokens)

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 데이터 없음\n🎯 공격 조합: `{target_disp}`\n📌 우리 길드 기준 / {MIN_STAT_TRIES}판 이상",
                mention_author=False
            )
            return

        lines = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            lines.append(
                f"{i}. `{item['defense_disp']}` — **{item['success']}승 {item['fail']}패** "
                f"(**{rate:.0f}%**, {item['total']}판)"
            )

        embed = build_stats_embed(
            title="🟢 우리 공격 승률",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"기준=공격 · 우리 길드 실전 기록 · {MIN_STAT_TRIES}판 이상",
            color=0x2ECC71,
        )

        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!우리공격 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 오류 발생", mention_author=False)

@bot.command(name="상대공격")
async def enemy_attack_winrate_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 공격조합 3명. 예) `!상대공격 트루드, 겔리두스, 라드그리드`", mention_author=False)
            return

        target_disp = _join_team_disp(tokens)
        results = raw_store.get_enemy_attack_winrates(tokens)

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 데이터 없음\n🎯 공격 조합: `{target_disp}`\n📌 상대 기준(기준=방어) / {MIN_STAT_TRIES}판 이상",
                mention_author=False
            )
            return

        lines = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            lines.append(
                f"{i}. `{item['defense_disp']}` — **{item['success']}승 {item['fail']}패** "
                f"(**{rate:.0f}%**, {item['total']}판)"
            )

        embed = build_stats_embed(
            title="🔴 상대 공격 승률",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"기준=방어 · 상대가 사용한 공격조합 성적 · {MIN_STAT_TRIES}판 이상",
            color=0xE74C3C,
        )

        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!상대공격 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 오류 발생", mention_author=False)

@bot.command(name="공격")
async def global_winrate_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 공격조합 3명. 예) `!공격 트루드, 겔리두스, 라드그리드`", mention_author=False)
            return

        target_disp = _join_team_disp(tokens)
        results = raw_store.get_global_attack_winrates(tokens)

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 데이터 없음\n🎯 공격 조합: `{target_disp}`\n📌 전체 raw data / {MIN_STAT_TRIES}판 이상",
                mention_author=False
            )
            return

        lines = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            lines.append(
                f"{i}. `{item['defense_disp']}` — **{item['success']}승 {item['fail']}패** "
                f"(**{rate:.0f}%**, {item['total']}판)"
            )

        embed = build_stats_embed(
            title="🔵 전체 공격 승률",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"전체 raw data 기준 · {MIN_STAT_TRIES}판 이상",
            color=0x3498DB,
        )

        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!공격 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 오류 발생", mention_author=False)


@bot.command(name="우리방어")
async def defense_stats_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 방어조합 3명. 예) `!우리방어 브브, 여포, 파이`", mention_author=False)
            return

        target_disp = _join_team_disp(tokens)
        results = raw_store.get_defense_stats(tokens)

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 방어 통계가 없습니다.\n🎯 대상 조합: `{target_disp}`\n📌 기준=방어 / {MIN_STAT_TRIES}판 이상",
                mention_author=False
            )
            return

        lines = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            lines.append(
                f"{i}. `{item['attack_disp']}` — **{item['success']}회 막음 / {item['fail']}회 뚫림** "
                f"(**{rate:.0f}%**, {item['total']}판)"
            )

        embed = build_stats_embed(
            title="🛡️ 방어 통계",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"기준=방어 · 상대 공격조합별 방어 성공률 · {MIN_STAT_TRIES}판 이상",
            color=0x2ECC71,
        )
        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!우리방어 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 방어 통계 처리 중 오류가 발생했어요.", mention_author=False)


@bot.command(name="상대방어")
async def attack_stats_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 상대 방어조합 3명. 예) `!상대방어 브브, 여포, 파이`", mention_author=False)
            return

        target_disp = _join_team_disp(tokens)
        results = raw_store.get_attack_stats(tokens)

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 공격 통계가 없습니다.\n🎯 대상 조합: `{target_disp}`\n📌 기준=공격 / {MIN_STAT_TRIES}판 이상",
                mention_author=False
            )
            return

        lines = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            lines.append(
                f"{i}. `{item['attack_disp']}` — **{item['success']}회 성공 / {item['fail']}회 실패** "
                f"(**{rate:.0f}%**, {item['total']}판)"
            )

        embed = build_stats_embed(
            title="⚔️ 상대 방어 통계",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"기준=공격 · 우리 공격조합별 돌파율 · {MIN_STAT_TRIES}판 이상",
            color=0xE67E22,
        )
        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!상대방어 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 공격 통계 처리 중 오류가 발생했어요.", mention_author=False)


@bot.command(name="방어")
async def overall_stats_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 대상 방어조합 3명. 예) `!방어 브브, 여포, 파이`", mention_author=False)
            return

        target_disp = _join_team_disp(tokens)
        results = raw_store.get_overall_stats(tokens)

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 전체 통계가 없습니다.\n🎯 대상 조합: `{target_disp}`\n📌 전체 raw data / {MIN_STAT_TRIES}판 이상",
                mention_author=False
            )
            return

        lines = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            lines.append(
                f"{i}. `{item['attack_disp']}` — **{item['success']}회 뚫음 / {item['fail']}회 막힘** "
                f"(**{rate:.0f}%**, {item['total']}판)"
            )

        embed = build_stats_embed(
            title="📊 전체 방어",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"전체 raw data · 공격조합별 종합 돌파율 · {MIN_STAT_TRIES}판 이상",
            color=0x9B59B6,
        )
        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!방어 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 전체 통계 처리 중 오류가 발생했어요.", mention_author=False)

@bot.command(name="통계설정")
async def stat_setting(ctx, n: int = None):
    if n is None:
        await ctx.reply(
            f"현재 최소 표본: {common.MIN_STAT_GAMES}판",
            mention_author=False
        )
        return

    common.MIN_STAT_GAMES = n

    await ctx.reply(
        f"최소 표본을 {n}판으로 변경했습니다.",
        mention_author=False
    )

# --- 게시판 관리 명령어 추가 ---
@bot.command(name="게시판등록")
async def register_board(ctx, board_id: str, *, board_name: str):
    if bot.board_crawler.register(board_id, board_name, ctx.channel.id):
        await ctx.send(
            f"✅ [{board_name}] 게시판 알림이 등록되었습니다."
        )
    else:
        await ctx.send("❌ 이미 등록된 게시판입니다.")

@bot.command(name="게시판해제")
async def unregister_board(ctx, board_id: str):
    if bot.board_crawler.unregister(board_id):
        await ctx.send(f"🚫 {board_id}번 게시판 알림을 해제했습니다.")
    else:
        await ctx.send("❌ 등록되지 않은 게시판입니다.")

@bot.command(name="게시판목록")
async def list_boards(ctx):
    boards = bot.board_crawler.get_board_list()
    if not boards:
        await ctx.send("현재 등록된 게시판이 없습니다.")
    else:
        await ctx.send(f"📋 **현재 감시 중인 게시판 ID:** {', '.join(boards)}")


if __name__ == "__main__":
    if not TOKEN:
        logger.error("DISCORD_TOKEN 이 설정되지 않았습니다 (.env/환경변수 확인)")
    else:
        bot.run(TOKEN)
