#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import traceback

import discord
from discord.ext import commands
from dotenv import load_dotenv

from common import MIN_STAT_TRIES, _canon_team_key, _join_team_disp, _split_csv_args, _badge_for_item
from counter_store import DataStore
from counter_ui import CounterView, build_stats_embed
from raw_store import RawMatchStore


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

data_store = DataStore(SHEET_URL_DEFAULT)
raw_store = RawMatchStore(SHEET_URL_DEFAULT, RAW_SHEET_GID_DEFAULT)

data_store.load()
raw_store.load()


@bot.event
async def on_ready():
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

@bot.command(name="승률")
async def winrate_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 공격조합 3명. 예) `!승률 트루드, 겔리두스, 라드그리드`", mention_author=False)
            return

        target_disp = _join_team_disp(tokens)
        results = raw_store.get_attack_winrates(tokens)

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 승률 데이터가 없습니다.\n🎯 공격 조합: `{target_disp}`\n📌 전체 raw data / {MIN_STAT_TRIES}판 이상",
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
            title="⚔️ 공격 승률",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"전체 raw data · 방어조합별 승률 · {MIN_STAT_TRIES}판 이상",
            color=0x1ABC9C,
        )

        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!승률 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 승률 조회 중 오류가 발생했어요.", mention_author=False)


@bot.command(name="방어통계")
async def defense_stats_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 방어조합 3명. 예) `!방어통계 브브, 여포, 파이`", mention_author=False)
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
        logger.error("!방어통계 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 방어 통계 처리 중 오류가 발생했어요.", mention_author=False)


@bot.command(name="공격통계")
async def attack_stats_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 상대 방어조합 3명. 예) `!공격통계 브브, 여포, 파이`", mention_author=False)
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
            title="⚔️ 공격 통계",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"기준=공격 · 우리 공격조합별 돌파율 · {MIN_STAT_TRIES}판 이상",
            color=0xE67E22,
        )
        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!공격통계 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 공격 통계 처리 중 오류가 발생했어요.", mention_author=False)


@bot.command(name="통계")
async def overall_stats_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = _split_csv_args(args)
        if len(tokens) != 3:
            await ctx.reply("❌ 입력은 대상 방어조합 3명. 예) `!통계 브브, 여포, 파이`", mention_author=False)
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
            title="📊 전체 통계",
            target_disp=target_disp,
            lines=lines,
            subtitle=f"전체 raw data · 공격조합별 종합 돌파율 · {MIN_STAT_TRIES}판 이상",
            color=0x9B59B6,
        )
        await ctx.reply(embed=embed, mention_author=False)

    except Exception:
        logger.error("!통계 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 전체 통계 처리 중 오류가 발생했어요.", mention_author=False)


if __name__ == "__main__":
    if not TOKEN:
        logger.error("DISCORD_TOKEN 이 설정되지 않았습니다 (.env/환경변수 확인)")
    else:
        bot.run(TOKEN)
