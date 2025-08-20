#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Counter Deck Chatbot (GCP VM 배포용)

레포 구성을 위해 필요한 파일:

1. discord_counter_bot.py  (봇 메인 코드)
2. requirements.txt        (파이썬 의존성)
3. .env.example            (환경 변수 템플릿)
4. systemd 서비스 파일 예시 (discord-bot.service)
"""
from __future__ import annotations
import os
import logging
import traceback
from typing import List, Dict, Any, Optional

import discord
from discord.ext import commands
import pandas as pd
from dotenv import load_dotenv

# -----------------------------
# 로깅 설정
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("counter-bot")

# -----------------------------
# 유틸
# -----------------------------
def _s(val: Any) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()

def normalize_team(maybe3: List[Any]) -> List[str]:
    return sorted([_s(x) for x in maybe3 if _s(x)])

# -----------------------------
# 데이터 로더
# -----------------------------
REQUIRED_COLUMNS = [
    "방어덱1","방어덱2","방어덱3",
    "스킬1","스킬2","스킬3",
    "선공",
    "공격덱1","공격덱2","공격덱3",
    "스킬1.1","스킬2.1","스킬3.1",
]

class DataStore:
    def __init__(self, excel_path: str):
        self.excel_path = excel_path
        self.df: Optional[pd.DataFrame] = None

    def load(self) -> None:
        try:
            logger.info(f"Loading excel: {self.excel_path}")
            df = pd.read_excel(self.excel_path)
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                logger.warning(f"엑셀에 필요한 컬럼이 없습니다: {missing}")
            self.df = df
            logger.info(f"Loaded excel: shape={df.shape}")
        except Exception:
            logger.error("엑셀 로드 실패:\n" + traceback.format_exc())
            self.df = None

    def search_counters(self, defense_team_input: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        try:
            if self.df is None or self.df.empty:
                return results

            input_sorted = normalize_team(defense_team_input)
            if len(input_sorted) != 3:
                return results

            for _, row in self.df.iterrows():
                defense_team = normalize_team([row.get("방어덱1"), row.get("방어덱2"), row.get("방어덱3")])
                if defense_team == input_sorted:
                    counters = {
                        "선공": _s(row.get("선공")) or "정보 없음",
                        "조합": [
                            _s(row.get("공격덱1")),
                            _s(row.get("공격덱2")),
                            _s(row.get("공격덱3")),
                        ],
                        "스킬": [
                            _s(row.get("스킬1.1")),
                            _s(row.get("스킬2.1")),
                            _s(row.get("스킬3.1")),
                        ],
                    }
                    if any(counters["조합"]) or any(counters["스킬"]):
                        results.append(counters)
        except Exception:
            logger.error("search_counters 오류:\n" + traceback.format_exc())
        return results

# -----------------------------
# 디스코드 Bot
# -----------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")
EXCEL_FILE = os.getenv("EXCEL_FILE_PATH", "카운터덱.xlsx")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
data_store = DataStore(EXCEL_FILE)

# 최초 로드
data_store.load()

@bot.event
async def on_error(event_method, *args, **kwargs):
    logger.error(f"on_error in {event_method}:\n" + traceback.format_exc())

@bot.event
async def on_ready():
    logger.info(f"✅ 로그인 완료: {bot.user} (guilds={len(bot.guilds)})")

async def send_long_message(dst, text: str):
    MAX = 2000
    if len(text) <= MAX:
        await dst.send(text)
        return
    start = 0
    while start < len(text):
        await dst.send(text[start:start+MAX])
        start += MAX

@bot.command(name="도움말")
async def help_cmd(ctx: commands.Context):
    try:
        msg = (
            "**사용법**\n"
            "- `!조합 A, B, C` : 방어덱 A,B,C에 대한 카운터덱을 모두 표시\n"
            "- `!리로드` : 엑셀을 다시 로드\n"
            "- `!상태` : 데이터 상태 확인\n"
        )
        await ctx.send(msg)
    except Exception:
        logger.error("!도움말 처리 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 도움말을 표시하는 중 오류가 발생했어요.")

@bot.command(name="상태")
async def status_cmd(ctx: commands.Context):
    try:
        if data_store.df is None:
            await ctx.send("데이터: 로드 실패 또는 없음")
            return
        shape = data_store.df.shape
        cols = ", ".join(list(map(str, data_store.df.columns)))
        await send_long_message(ctx, f"데이터 로드됨: {shape[0]}행 x {shape[1]}열\n컬럼: {cols}")
    except Exception:
        logger.error("!상태 처리 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 상태 확인 중 오류가 발생했어요.")

@bot.command(name="리로드")
async def reload_cmd(ctx: commands.Context):
    try:
        data_store.load()
        if data_store.df is None:
            await ctx.send("❌ 엑셀 로드 실패. 경로/형식을 확인해주세요.")
        else:
            await ctx.send("✅ 엑셀 리로드 완료")
    except Exception:
        logger.error("!리로드 처리 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 리로드 중 오류가 발생했어요.")

@bot.command(name="조합")
async def combo_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        raw = [x.strip() for x in args.replace("\n", ",").split(",") if x.strip()]
        if len(raw) != 3:
            await ctx.send("❌ 캐릭터 3개를 쉼표로 구분해 입력해주세요. 예: `!조합 니아, 델론즈, 스파이크`")
            return

        results = data_store.search_counters(raw)
        if not results:
            await ctx.send(f"⚠️ `{', '.join(sorted(raw))}` 에 대한 데이터가 없습니다.")
            return

        header = f"🎯 상대 조합: `{', '.join(sorted(normalize_team(raw)))}`\n"
        chunks: List[str] = [header]
        for i, r in enumerate(results, 1):
            combo = ", ".join([x for x in r["조합"] if x]) or "정보 없음"
            skills = " → ".join([x for x in r["스킬"] if x]) or "정보 없음"
            first = r.get("선공", "정보 없음")
            block = (
                f"\n🛡️ **카운터 #{i}**\n"
                f"- 조합: `{combo}`\n"
                f"- 스킬: `{skills}`\n"
                f"- 선공 여부: `{first}`\n"
            )
            chunks.append(block)

        await send_long_message(ctx, "".join(chunks))
    except Exception:
        logger.error("!조합 처리 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 요청을 처리하는 중 알 수 없는 오류가 발생했어요.")

@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    try:
        if isinstance(error, commands.CommandNotFound):
            await ctx.send("알 수 없는 명령어입니다. `!도움말`을 입력해 보세요.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("필수 인자가 누락됐어요. `!도움말`을 참고하세요.")
            return
        logger.error("on_command_error:\n" + traceback.format_exc())
        await ctx.send("⚠️ 처리 중 오류가 발생했어요.")
    except Exception:
        logger.error("on_command_error 핸들러 자체 오류:\n" + traceback.format_exc())

if __name__ == "__main__":
    if not TOKEN:
        logger.error("DISCORD_TOKEN 이 설정되지 않았습니다 (.env 확인)")
    else:
        try:
            bot.run(TOKEN)
        except Exception:
            logger.critical("디스코드 런타임 크래시:\n" + traceback.format_exc())

"""
추가 레포 파일 예시:

requirements.txt
----------------
discord.py>=2.3.2
pandas>=2.2.0
openpyxl>=3.1.2
python-dotenv>=1.0.1

.env.example
------------
DISCORD_TOKEN=여기에_디스코드_봇_토큰_입력
EXCEL_FILE_PATH=카운터덱.xlsx

systemd 서비스 파일 (discord-bot.service)
---------------------------------------
[Unit]
Description=Discord Counter Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/discord-counter-bot
ExecStart=/home/ubuntu/venv/bin/python /home/ubuntu/discord-counter-bot/discord_counter_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
"""