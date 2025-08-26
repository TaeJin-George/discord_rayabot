#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Counter Deck Chatbot (Cloudtype/GCP VM 모두 호환)

레포 구성을 위해 필요한 파일:

1. discord_counter_bot.py  (봇 메인 코드)
2. requirements.txt        (파이썬 의존성)
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


print("=== 현재 작업 디렉토리:", os.getcwd())
print("=== 파일 목록:", os.listdir(os.getcwd()))


# ===== 고정 상수 =====
PET_FLAT = 1119            # 펫 고정 공격력
FORMATION_FLAT = 630       # 진형 고정 가산
BUFF_ATK_RATE = 0.21       # 공격력 버프 +21% (곱연산)

# 세트 계수
WEAK_COEFF_TRACKER = 1.65   # 추적자: 약점 계수
SET_DMG_AVENGER = 1.30      # 복수자: 피해량 계수
WEAK_COEFF_DEFAULT = 1.30   # 기타 세트: 약점 계수(요청 고정)
SET_DMG_DEFAULT = 1.0       # 기타 세트: 피해량 계수

# 캐릭터별 100점 상한
SCORE_CAP = {
    "태오": 38584,
    "콜트": 13696,
    "린":   29190,
    "연희": 25227,
    "세인": 40102,
    "파스칼": 44099,
}

# 캐릭터 고유 로직
def is_always_crit(character: str) -> bool:
    return character in ("태오","파스칼")

def is_never_crit_and_weak(character: str) -> bool:
    return character == "콜트"

def parse_percent(x: str) -> float:
    return float(x.replace('%', '').strip())

def normalize_set(name: str):
    name = name.strip()
    if name == "추적자":
        return WEAK_COEFF_TRACKER, 1.0
    if name == "복수자":
        return WEAK_COEFF_DEFAULT, SET_DMG_AVENGER
    # 그 외 세트: 약점 1.3, 피해량 1.0 고정
    return WEAK_COEFF_DEFAULT, SET_DMG_DEFAULT

def final_attack(stat_atk: float, character: str) -> float:
    """
    기본 최종공격력 = (스탯공 + 펫(1119) + 진형(630)) * (1 + 0.21)
    콜트는 이 계산 이후 +1320 추가
    """
    atk = (stat_atk + PET_FLAT + FORMATION_FLAT) * (1.0 + BUFF_ATK_RATE)
    if character == "콜트":
        atk += 1320.0
    return atk

def compute_damage(character: str, stat_atk: float, crit_rate_pct: float,
                   crit_dmg_pct: float, weak_rate_pct: float, set_name: str):
    """
    전투력(약점O), 전투력(약점X), 기대 전투력(약확 반영), 최종공격력
    """
    atk = final_attack(stat_atk, character)
    weak_coeff, set_dmg = normalize_set(set_name)

    # 치명 배수
    cd_mult = max(1.0, crit_dmg_pct / 100.0)  # 방어적 처리
    if is_never_crit_and_weak(character):
        crit_factor = 1.0
    elif is_always_crit(character):
        crit_factor = cd_mult
    else:
        pcrit = max(0.0, min(1.0, crit_rate_pct / 100.0))
        if character == "린":
            pcrit = min(1.0, pcrit + 0.33)
        if character == "세인":
            pcrit = min(1.0, pcrit + 0.51)
        crit_factor = pcrit * cd_mult + (1 - pcrit) * 1.0

    # 약점 배수
    if is_never_crit_and_weak(character):
        pweak = 0.0
    else:
        pweak = max(0.0, min(1.0, weak_rate_pct / 100.0))
        if character == "세인":
            pweak = min(1.0, pweak + 0.93)
        if character == "파스칼":
            pweak = min(1.0, pweak + 0.66)

    dmg_on_weak = atk * crit_factor * weak_coeff * set_dmg
    dmg_no_weak = atk * crit_factor * 1.0        * set_dmg
    dmg_expected = atk * crit_factor * (pweak * weak_coeff + (1 - pweak) * 1.0) * set_dmg
    return atk, dmg_on_weak, dmg_no_weak, dmg_expected

def score_from_cap(character: str, value: float) -> float:
    cap = SCORE_CAP.get(character)
    if not cap or cap <= 0:
        return 0.0
    return round(value / cap * 100.0, 2)

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

def s_no_strip(val: Any) -> str:
    if pd.isna(val):
        return ""
    return str(val)  # <-- strip() 하지 않음

def team_exact(maybe3: List[Any]) -> List[str]:
    # 공백 포함 그대로, 정렬만
    vals = [s_no_strip(x) for x in maybe3 if s_no_strip(x) != ""]
    return sorted(vals)

def skills_order_exact(maybe3: List[Any]) -> List[str]:
    # 순서 유지, 공백 그대로
    return [s_no_strip(x) for x in maybe3 if s_no_strip(x) != ""]


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
    
            # 입력 덱: 공백 보존, 정렬만
            input_sorted = team_exact(defense_team_input)
            if len(input_sorted) != 3:
                return results
    
            # 입력 스킬: 공백 보존, 순서 유지
            want_def_skills = None
            if defense_skills_input:
                want_def_skills = skills_order_exact(defense_skills_input)
                if len(want_def_skills) != 3:
                    return results
    
            for _, row in self.df.iterrows():
                defense_team = team_exact([
                    row.get("방어덱1"),
                    row.get("방어덱2"),
                    row.get("방어덱3"),
                ])
                if defense_team != input_sorted:
                    continue
    
                if want_def_skills is not None:
                    row_def_skills = skills_order_exact([
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
        embed = discord.Embed(
            title="❓ 도움말",
            description="자주 쓰이는 명령어 목록입니다.",
            color=0x32CD32
        )

        embed.add_field(
            name="🛡️ 길드전 카운터덱 찾기",
            value=(
                "`!조합 A,B,C`\n"
                "→ 방어 조합 `A,B,C`를 카운터한 기록을 보여줍니다.\n"
                "`!조합 A,B,C,스킬1,스킬2,스킬3`\n"
                "→ 방어 스킬 순서까지 일치하는 경우만 찾습니다."
            ),
            inline=False
        )

        embed.add_field(
            name="⚔️ 딜러 전투력 계산",
            value=(
                "`!전투력 캐릭/스탯공/치확/치피/약확/세트`\n"
                "예) `!전투력 태오/5338/5%/174%/20%/복수자`\n"
                "→ 극 내실 종결 세팅 대비 내 캐릭터의 전투력을 계산합니다."
            ),
            inline=False
        )

        embed.add_field(
            name="🔄 데이터 관리(운영진 전용)",
            value=(
                "`!리로드` → 데이터 소스(엑셀/구글시트) 다시 불러오기\n"
                "`!상태`   → 현재 데이터 상태와 컬럼 확인"
            ),
            inline=False
        )

        embed.add_field(
            name="ℹ️ 참고",
            value="세부 입력 규칙은 `!사용법` 명령으로 확인하세요.",
            inline=False
        )

        await ctx.send(embed=embed)
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
        # 쉼표만으로 분리, 공백 보존 (strip 하지 않음)
        tokens = args.split(",") if args else []
        count = len(tokens)

        if count == 3:
            raw_team = tokens
            raw_skills = None
        elif count == 6:
            raw_team = tokens[:3]
            raw_skills = tokens[3:]
        else:
            await ctx.send(
                "❌ 입력은 쉼표로만 구분해 주세요.\n"
                "예1) `!조합 A,B,C`\n"
                "예2) `!조합 A,B,C,스킬1,스킬2,스킬3`"
            )
            return

        results = data_store.search_counters(raw_team, raw_skills)

        # 헤더 표시 (보기용은 기존처럼 trim해도 무방)
        team_label = ', '.join(sorted(team_exact(raw_team)))
        header = f"🎯 상대 조합: `{team_label}`"
        if raw_skills:
            header += f" | 🧩 방어 스킬: `{' → '.join(skills_order_exact(raw_skills))}`"
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

@bot.command(name="사용법")
async def manual_cmd(ctx: commands.Context):
    try:
        embed = discord.Embed(
            title="📖 사용법",
            description="명령어와 입력 규칙을 확인하세요.",
            color=0x00BFFF
        )

        embed.add_field(
            name="🛡️ 카운터덱 (`!조합`)",
            value=(
                "• **쉼표(,)** 로만 구분합니다. 이름 안의 공백은 그대로 유지하세요.\n"
                "• 예1) `!조합 니아,델론즈,스파이크`\n"
                "• 예2) `!조합 니아,델론즈,스파이크,니아 위,델론즈 아래,스파이크 위`\n"
                "   ↳ *예2는 방어 스킬 순서까지 정확히 일치하는 데이터만 찾습니다.*"
            ),
            inline=False
        )

        embed.add_field(
            name="⚔️ 전투력 (`!전투력`)",
            value=(
                "• **슬래시(/)** 로 구분합니다.\n"
                "• 형식: `!전투력 캐릭/스탯공/치확/치피/약확/세트`\n"
                "• 예) `!전투력 태오/5338/5%/174%/20%/복수자`"
            ),
            inline=False
        )

        embed.add_field(
            name="📌 전투력 상세 안내",
            value=(
                "> 극 내실 종결 세팅 대비 현재 내 캐릭터의 전투력(데미지)을 나타냅니다.\n"
                "> 속공이나 효과 적중 등 **데미지와 무관한 지표는 반영되지 않으니 참고 바랍니다.**\n"
            ),
            inline=False
        )

        embed.set_footer(text="추가: `!리로드`, `!상태`")
        await ctx.send(embed=embed)
    except Exception:
        logger.error("!사용법 처리 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 요청을 처리하는 중 오류가 발생했어요.")


@bot.command(name="전투력")
async def cmd_power(ctx, *, argline: str):
    """
    사용법:
    !전투력 캐릭터/스탯공격력/치확/치피/약확/세트옵션
    예) !전투력 태오/5338/5%/174%/20%/복수자
    """
    try:
        parts = [p.strip() for p in argline.split('/')]
        if len(parts) != 6:
            return await ctx.reply("❌ 형식: `!전투력 캐릭/스탯공/치확/치피/약확/세트`  예) `!전투력 태오/5338/5%/174%/20%/복수자`")

        character, stat_s, cr_s, cd_s, wr_s, set_name = parts
        if character not in ("태오", "콜트", "연희", "린", "세인", "파스칼"):
            return await ctx.reply("❌ 지원 캐릭터: `태오`, `콜트`, `연희`, `린`, '세인', '파스칼'")

        try:
            stat_atk  = float(stat_s)
            crit_rate = parse_percent(cr_s)
            crit_dmg  = parse_percent(cd_s)
            weak_rate = parse_percent(wr_s)
        except ValueError:
            return await ctx.reply("❌ 숫자 형식이 올바르지 않습니다. 예) 치확/치피/약확 `%` 포함: `5%`, `174%`, `20%`")

        atk, dmg_w, dmg_nw, dmg_exp = compute_damage(
            character=character,
            stat_atk=stat_atk,
            crit_rate_pct=crit_rate,
            crit_dmg_pct=crit_dmg,
            weak_rate_pct=weak_rate,
            set_name=set_name
        )

        score_w  = score_from_cap(character, dmg_w)
        score_nw = score_from_cap(character, dmg_nw)
        score_av = score_from_cap(character, dmg_exp)

        def fmt(x): return f"{int(round(x,0)):,}"

        # 기존:
        # msg = f"""
        # **{character} / {set_name}**
        # - 기대 전투력: **{score_av}점**
        # - 전투력(약점O): **{score_w}점**
        # - 전투력(약점X): **{score_nw}점**
        # """
        # await ctx.reply(msg)

        # 변경: 콜트는 기대 전투력만 표시
        if character == "콜트":
            msg = (
                f"**{character} / {set_name}**\n"
                f"- 폭탄 전투력: **{score_av}점**"
            )
        else:
            msg = (
                f"**{character} / {set_name}**\n"
                f"- 기대 전투력: **{score_av}점**\n"
                f"- 전투력(약점O): **{score_w}점**\n"
                f"- 전투력(약점X): **{score_nw}점**"
            )
        await ctx.reply(msg)


    except Exception:
        logger.error("!전투력 처리 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 전투력 계산 중 오류가 발생했어요.")


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

