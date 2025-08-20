#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Counter Deck Chatbot (Cloudtype/GCP VM 모두 호환)

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
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode

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

def normalize_skills_order(maybe3: List[Any]) -> List[str]:
    """스킬 순서 비교용: 공백/빈값 제거만 하고 '순서 유지'"""
    out = []
    for x in maybe3:
        s = _s(x)
        if s:
            out.append(s)
    return out

# -----------------------------
# 데이터 로더 (엑셀/구글 시트 자동 판별)
# -----------------------------
REQUIRED_COLUMNS = [
    "방어덱1","방어덱2","방어덱3",
    "스킬1","스킬2","스킬3",
    "선공",
    "공격덱1","공격덱2","공격덱3",
    "스킬1.1","스킬2.1","스킬3.1",
]

_GS_PREFIX = "https://docs.google.com/spreadsheets/d/"

def _is_google_sheet(path_or_url: str) -> bool:
    return isinstance(path_or_url, str) and path_or_url.startswith(_GS_PREFIX)

def _extract_sheet_id(sheet_url_or_id: str) -> str:
    # 전체 URL 또는 ID 지원
    if _GS_PREFIX in sheet_url_or_id:
        return sheet_url_or_id.split("/spreadsheets/d/")[1].split("/")[0]
    return sheet_url_or_id

def _guess_gid_from_url(url: str) -> Optional[int]:
    # URL 쿼리에 gid=가 있으면 추출 (없으면 None → 첫 탭)
    if "gid=" in url:
        try:
            return int(url.split("gid=")[1].split("&")[0])
        except Exception:
            return None
    return None

def _csv_url_from_sheet(sheet_url_or_id: str, gid: Optional[int]) -> str:
    sheet_id = _extract_sheet_id(sheet_url_or_id)
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
    params = {"format": "csv"}
    if gid is not None:
        params["gid"] = str(gid)
    return f"{base}?{urlencode(params)}"

class DataStore:
    def __init__(self, excel_path: str):
        """
        excel_path:
          - 로컬/스토리지의 .xlsx 경로
          - 또는 구글 스프레드시트 URL (공개 '보기' 권한 필요)
        환경변수:
          - DATA_SHEET_URL 이 설정되어 있으면 그것을 우선 사용
          - 없으면 EXCEL_FILE_PATH 사용 (기존 호환)
        """
        self.excel_path = os.getenv("DATA_SHEET_URL") or os.getenv("EXCEL_FILE_PATH") or excel_path
        self.df: Optional[pd.DataFrame] = None

    def load(self) -> None:
        try:
            if _is_google_sheet(self.excel_path):
                gid = _guess_gid_from_url(self.excel_path)
                csv_url = _csv_url_from_sheet(self.excel_path, gid)
                logger.info(f"Loading Google Sheet CSV: {csv_url}")
                df = pd.read_csv(csv_url)  # 필요 시 , dtype=str
            else:
                logger.info(f"Loading Excel: {self.excel_path}")
                df = pd.read_excel(self.excel_path)

            # 필수 컬럼 체크
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                logger.warning(f"데이터에 필요한 컬럼이 없습니다: {missing}")

            self.df = df
            logger.info(f"Loaded data: shape={df.shape}, columns={list(df.columns)}")
        except Exception:
            logger.error("데이터 로드 실패:\n" + traceback.format_exc())
            self.df = None

    def search_counters(
    self,
    defense_team_input: List[str],
    defense_skills_input: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        if self.df is None or self.df.empty:
            return results

        input_sorted = normalize_team(defense_team_input)
        if len(input_sorted) != 3:
            return results

        # 방어 스킬 입력이 있으면 순서 유지 비교용으로 정규화
        want_def_skills = None
        if defense_skills_input:
            want_def_skills = normalize_skills_order(defense_skills_input)
            # 정확히 3개 아니면 무시(혹은 여기서 바로 return [])
            if len(want_def_skills) != 3:
                return results

        for _, row in self.df.iterrows():
            defense_team = normalize_team([
                row.get("방어덱1"),
                row.get("방어덱2"),
                row.get("방어덱3"),
            ])
            if defense_team != input_sorted:
                continue

            # 스킬 필터가 있으면, "스킬1, 스킬2, 스킬3"과 '순서 동일'해야 통과
            if want_def_skills is not None:
                row_def_skills = normalize_skills_order([
                    row.get("스킬1"),
                    row.get("스킬2"),
                    row.get("스킬3"),
                ])
                if row_def_skills != want_def_skills:
                    continue

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

# 기존 EXCEL_FILE_PATH를 계속 지원하면서, DATA_SHEET_URL이 있으면 자동 우선
EXCEL_FILE = os.getenv("DATA_SHEET_URL") or os.getenv("EXCEL_FILE_PATH", "카운터덱.xlsx")

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
            "- `!조합 A, B, C | 스킬1, 스킬2, 스킬3` : 방어 스킬 순서까지 지정해 정확히 일치하는 카운터만 표시\n"
            "- `!리로드` : 데이터 소스(엑셀/구글시트)를 다시 로드\n"
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
            await ctx.send("❌ 데이터 로드 실패. 경로/형식을 확인해주세요.")
        else:
            await ctx.send("✅ 데이터 리로드 완료")
    except Exception:
        logger.error("!리로드 처리 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 리로드 중 오류가 발생했어요.")

@bot.command(name="조합")
async def combo_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        # "덱 입력 | 스킬 입력" 형태로 분리 (스킬 파트는 옵션)
        parts = [p.strip() for p in args.split("|", 1)]
        team_part = parts[0] if parts else ""
        skills_part = parts[1] if len(parts) == 2 else ""

        # 팀 파트 파싱
        raw_team = [x.strip() for x in team_part.replace("\n", ",").split(",") if x.strip()]
        if len(raw_team) != 3:
            await ctx.send(
                "❌ 캐릭터 3개를 쉼표로 구분해 입력해주세요.\n"
                "예: `!조합 니아, 델론즈, 스파이크`\n"
                "또는 방어 스킬까지: `!조합 니아, 델론즈, 스파이크 | 스킬A, 스킬B, 스킬C`"
            )
            return

        # 스킬 파트 파싱(옵션)
        raw_skills: Optional[List[str]] = None
        if skills_part:
            tokens = re.split(r"[,\s>→]+", skills_part)
            raw_skills = [t.strip() for t in tokens if t.strip()]
            if len(raw_skills) != 3:
                await ctx.send(
                    "❌ 방어 스킬은 순서대로 3개를 입력해주세요.\n"
                    "예: `!조합 니아, 델론즈, 스파이크 | 스킬A, 스킬B, 스킬C`"
                )
                return

        # 검색
        results = data_store.search_counters(raw_team, raw_skills)

        # 결과 메시지
        team_label = ', '.join(sorted(normalize_team(raw_team)))
        header = f"🎯 상대 조합: `{team_label}`"
        if raw_skills:
            header += f" | 🧩 방어 스킬: `{' → '.join(normalize_skills_order(raw_skills))}`"
        header += "\n"

        if not results:
            await ctx.send(f"⚠️ 조건에 맞는 데이터가 없습니다.\n{header}")
            return

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
    load_dotenv()
    if not TOKEN:
        logger.error("DISCORD_TOKEN 이 설정되지 않았습니다 (.env/환경변수 확인)")
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
# 엑셀 파일 경로(로컬 또는 마운트)
EXCEL_FILE_PATH=카운터덱.xlsx
# 또는 구글 시트 URL (있으면 이 값이 우선)
# 예: https://docs.google.com/spreadsheets/d/1fvwkynV3iwMQ-0aa5VEaYDXCuKRGllezCtKK9x9-Yuo/edit?usp=sharing
DATA_SHEET_URL=

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
