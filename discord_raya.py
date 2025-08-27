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
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlencode
import math

import discord
from discord.ext import commands
import pandas as pd
from dotenv import load_dotenv


print("=== 현재 작업 디렉토리:", os.getcwd())
print("=== 파일 목록:", os.listdir(os.getcwd()))


# =========================
# 전역 상수/레퍼런스 규칙
# =========================

# (A) 공격자(내실 태오덱) 상수 — 고정
TEO_STAT_ATK = 4458
TEO_BASE_ATK = 1500
TEO_CRIT_MULT = 2.64            # 태오 기본 치명배수(기본 1.5 대신 캐릭 오버라이드)
TEO_SKILL_COEFF = 1.70          # 스킬 계수 170%
PET_ATTACK_FLAT = 1119          # 이린 펫 깡공
PET_ATTACK_PERCENT = 0.21       # 이린 펫 공퍼 +21%
FORMATION_ATTACK_PERCENT = 0.42 # 보호 진형 뒷줄 +42%
ATTACK_PERCENT_BUFFS = 0.25     # 아일린 +25% (공퍼 합산)
# 공격력% 총합(기본 1에서 시작, 증가합 - 감소합): 1 + (0.21 + 0.25) = 1.46
ATK_MULT_INCREASE_SUM = PET_ATTACK_PERCENT + ATTACK_PERCENT_BUFFS
# 피해량 계수(기본 1, 복수자/반지 없음, 챈슬러 감산 없음 => 1.0)
DMG_INCREASE_ADD_SUM = 0.0
DMG_INCREASE_REDUCE_SUM = 0.0   # (챈슬러 -13%) 미채용
WEAK_MULT_CHASER = 1.65         # 추적자 세트 약점 배수
VULNERABILITY_PAI = 1.20        # 파이 물리 취약
DEF_SHRED_VANESSA = 0.29        # 바네사 방깎 29%
DEF_PENETRATION = 0.0           # 방무 없음

# (B) 방어측 공통(펫/진형/버퍼)
PET_DEFENSE_PERCENT = 0.13      # 펫 방어% +13%
PET_DEFENSE_FLAT = 344          # 펫 깡방 +344

# 진형(앞줄 방어% — 공진_방어)
FORMATION_DEFENSE_PERCENT = {
    "보호": 0.105,
    "밸런스": 0.14,
    "기본": 0.21,
    "공격": 0.42,
}

# 방어 버퍼 정의(최대 1명 시뮬): 루디/앨리스만 방어버퍼로 취급
DEF_BUFFS = {
    "루디": {"def_percent": 0.24, "dampening": 0.16},  # 감쇄 16%는 최종 ×(1-0.16)
    "앨리스": {"def_percent": 0.39, "dampening": 0.00},
}

# 탱커 기본 방어력
BASE_DEF_BY_CHAR = {
    "루디": 892,
    "챈슬러": 659,
    "아라곤": 892,
    "플라튼": 675,
    "앨리스": 675,
    "스파이크": 659,
}

# (C) 계산 규칙 상수
DEF_COEFF_PER_DEF = 0.00214     # DEFcoeff = 1 + floor(effective_def) * 0.00214
BASIC_CRIT_MULT = 1.50           # 참고: 기본 치명배수(캐릭 오버라이드로 태오는 2.64)
BLOCK_CRIT_MULT = 1.0            # 막기 성공 시 치명 → 일반 처리
ROUND_FLOOR = True               # 각 단계 floor


# 캐릭터별 100점 상한 (기존 유지)
SCORE_CAP = {
    "태오": 38584,
    "콜트": 13696,
    "린":   29190,
    "연희": 25227,
    "세인": 40102,
    "파스칼": 44099,
}

# -----------------------------
# 기존 딜러 계산 로직 (유지)
# -----------------------------
def is_always_crit(character: str) -> bool:
    return character in ("태오","파스칼")

def is_never_crit_and_weak(character: str) -> bool:
    return character == "콜트"

def parse_percent(x: str) -> float:
    return float(x.replace('%', '').strip())

def normalize_set(name: str):
    name = name.strip()
    if name == "추적자":
        return WEAK_MULT_CHASER, 1.0
    if name == "복수자":
        return 1.30, 1.30  # 약점1.3, 피해량1.3 (레거시 지원 — 실제 본 봇에서는 추적자/복수자 고정 사용)
    return 1.30, 1.0

def final_attack(stat_atk: float, character: str) -> float:
    """
    기존 딜러 계산용: (스탯공 + 펫(1119) + 진형(630)) * (1 + 0.21)
    """
    PET_FLAT = PET_ATTACK_FLAT
    FORMATION_FLAT = int(round(TEO_BASE_ATK * FORMATION_ATTACK_PERCENT))
    BUFF_ATK_RATE = PET_ATTACK_PERCENT
    atk = (stat_atk + PET_FLAT + FORMATION_FLAT) * (1.0 + BUFF_ATK_RATE)
    if character == "콜트":
        atk += 1320.0
    return atk

def compute_damage(character: str, stat_atk: float, crit_rate_pct: float,
                   crit_dmg_pct: float, weak_rate_pct: float, set_name: str):
    atk = final_attack(stat_atk, character)
    weak_coeff, set_dmg = normalize_set(set_name)
    cd_mult = max(1.0, crit_dmg_pct / 100.0)
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


# ==================================
# 신규: 탱커 방어력 기반 데미지 계산
# ==================================
def floor(x: float) -> int:
    return math.floor(x) if ROUND_FLOOR else x

def _atk_final_for_teo() -> int:
    """
    ATK_final = floor( (TEO_STAT_ATK + PET_ATTACK_FLAT + BASE_ATK*0.42) * (1 + 0.21 + 0.25 - 감소합) )
    (감소합=0 가정)
    """
    formation_flat = TEO_BASE_ATK * FORMATION_ATTACK_PERCENT
    mult_atk_pct = max(0.0, 1.0 + ATK_MULT_INCREASE_SUM)  # 감소 없음 → 1 + 0.46
    val = (TEO_STAT_ATK + PET_ATTACK_FLAT + formation_flat) * mult_atk_pct
    return floor(val)

def _effective_def_and_coeff(
    defender_name: str,
    stat_def: int,
    formation_name: str,
    extra_def_percent_from_buffer: float
) -> Tuple[int, float]:
    """
    유효방어 = (기본방 + 장비방 + 펫깡방 + 기본방*공진_방어) * (1 + [펫방% + 버퍼방%] - 방깎) * (1 - 방무)
    DEFcoeff = 1 + floor(유효방어) * 0.00214
    """
    base_def = BASE_DEF_BY_CHAR.get(defender_name)
    if base_def is None:
        raise ValueError("지원하지 않는 탱커명입니다.")
    gear_def = max(0, stat_def - base_def)
    formation_def_pct = FORMATION_DEFENSE_PERCENT.get(formation_name)
    if formation_def_pct is None:
        raise ValueError("진형은 보호/밸런스/기본/공격 중 하나여야 합니다.")
    add_from_formation = base_def * formation_def_pct

    # 방퍼합 = 펫13% + 버퍼(루디24/앨리스39)
    def_percent_sum = PET_DEFENSE_PERCENT + extra_def_percent_from_buffer

    effective_def = (base_def + gear_def + PET_DEFENSE_FLAT + add_from_formation)
    effective_def = effective_def * (1.0 + def_percent_sum - DEF_SHRED_VANESSA) * (1.0 - DEF_PENETRATION)
    eff_def_int = floor(effective_def)

    defcoeff = 1.0 + eff_def_int * DEF_COEFF_PER_DEF
    return eff_def_int, defcoeff

def _damage_pipeline(
    atk_final: int, defcoeff: float,
    crit_mult: float, skill_coeff: float,
    dmg_increase_mult: float, weak_mult: float, vuln_mult: float,
    reduce_taken_r: float, dampening: float
) -> int:
    """
    단계별 floor:
    1) floor(ATK_final / DEFcoeff)
    2) ×치명
    3) ×스킬
    4) ×피해량 계수 (기본 1 + 가산합 - 감소합, 최소 0; 증가 없어도 감소만으로 1에서 깎임)
    5) ×약점
    6) ×취약
    7) ×(1 - 받피감)
    8) ×(1 - 감쇄)  # 루디
    """
    dmg = floor(atk_final / defcoeff)
    dmg = floor(dmg * crit_mult)
    dmg = floor(dmg * skill_coeff)
    dmg = floor(dmg * dmg_increase_mult)
    dmg = floor(dmg * weak_mult)
    dmg = floor(dmg * vuln_mult)
    dmg = floor(dmg * (1.0 - reduce_taken_r))
    dmg = floor(dmg * (1.0 - dampening))
    return dmg

def simulate_vs_teo(
    defender_name: str,
    stat_def: int,
    reduce_taken_r: float,
    formation_name: str,
    friend_buffer: Optional[str] = None
) -> Dict[str, Any]:
    """
    막기 뜸/안 뜸 두 케이스 반환 + (선택) 방어버퍼 1명 채용 결과
    """
    # 추천 버퍼: 본인이 루디면 앨리스, 아니면 루디
    if friend_buffer is None:
        friend_buffer = "앨리스" if defender_name == "루디" else "루디"
    buff_info = DEF_BUFFS.get(friend_buffer, {"def_percent": 0.0, "dampening": 0.0})

    # 공격자 고정값
    atk_final = _atk_final_for_teo()

    # 피해량 계수(기본 1 + 가산합 - 감소합) — 본 시뮬은 가산/감산 없음 → 1.0
    dmg_increase_mult = max(0.0, 1.0 + DMG_INCREASE_ADD_SUM - DMG_INCREASE_REDUCE_SUM)

    # ========== (A) 버퍼 미채용 ==========
    eff_def_none, defcoeff_none = _effective_def_and_coeff(defender_name, stat_def, formation_name, extra_def_percent_from_buffer=0.0)
    damp_none = 0.0
    dmg_block_on_none = _damage_pipeline(
        atk_final, defcoeff_none, BLOCK_CRIT_MULT, TEO_SKILL_COEFF,
        dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
        reduce_taken_r, damp_none
    )
    dmg_block_off_none = _damage_pipeline(
        atk_final, defcoeff_none, TEO_CRIT_MULT, TEO_SKILL_COEFF,
        dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
        reduce_taken_r, damp_none
    )

    # ========== (B) 버퍼 1명 채용 ==========
    eff_def_buff, defcoeff_buff = _effective_def_and_coeff(
        defender_name, stat_def, formation_name,
        extra_def_percent_from_buffer=buff_info["def_percent"]
    )
    damp_buff = buff_info["dampening"]
    dmg_block_on_buff = _damage_pipeline(
        atk_final, defcoeff_buff, BLOCK_CRIT_MULT, TEO_SKILL_COEFF,
        dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
        reduce_taken_r, damp_buff
    )
    dmg_block_off_buff = _damage_pipeline(
        atk_final, defcoeff_buff, TEO_CRIT_MULT, TEO_SKILL_COEFF,
        dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
        reduce_taken_r, damp_buff
    )

    # 감소율 계산
    def pct_reduced(new: int, base: int) -> float:
        if base <= 0:
            return 0.0
        return round((base - new) / base * 100.0, 1)

    red_on = pct_reduced(dmg_block_on_buff, dmg_block_on_none)
    red_off = pct_reduced(dmg_block_off_buff, dmg_block_off_none)

    return {
        "friend_buffer": friend_buffer,
        "none": {
            "block_on": dmg_block_on_none,
            "block_off": dmg_block_off_none,
            "eff_def": eff_def_none,
            "defcoeff": defcoeff_none,
        },
        "buff": {
            "block_on": dmg_block_on_buff,
            "block_off": dmg_block_off_buff,
            "eff_def": eff_def_buff,
            "defcoeff": defcoeff_buff,
            "reduced_on_pct": red_on,
            "reduced_off_pct": red_off,
        }
    }


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
    out = []
    for x in maybe3:
        s = _s(x)
        if s:
            out.append(s)
    return out

def s_no_strip(val: Any) -> str:
    if pd.isna(val):
        return ""
    return str(val)

def team_exact(maybe3: List[Any]) -> List[str]:
    vals = [s_no_strip(x) for x in maybe3 if s_no_strip(x) != ""]
    return sorted(vals)

def skills_order_exact(maybe3: List[Any]) -> List[str]:
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
    if _GS_PREFIX in sheet_url_or_id:
        return sheet_url_or_id.split("/spreadsheets/d/")[1].split("/")[0]
    return sheet_url_or_id

def _guess_gid_from_url(url: str) -> Optional[int]:
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
        self.excel_path = os.getenv("DATA_SHEET_URL") or os.getenv("EXCEL_FILE_PATH") or excel_path
        self.df: Optional[pd.DataFrame] = None

    def load(self) -> None:
        try:
            if _is_google_sheet(self.excel_path):
                gid = _guess_gid_from_url(self.excel_path)
                csv_url = _csv_url_from_sheet(self.excel_path, gid)
                logger.info(f"Loading Google Sheet CSV: {csv_url}")
                df = pd.read_csv(csv_url)
            else:
                logger.info(f"Loading Excel: {self.excel_path}")
                df = pd.read_excel(self.excel_path)

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

            input_sorted = team_exact(defense_team_input)
            if len(input_sorted) != 3:
                return results

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


# =========================
# 디스코드 봇 설정
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")

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


# =========================
# 명령어들
# =========================
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
            name="🧱 탱커 방어력 시뮬레이터",
            value=(
                "`!방어력 캐릭/스탯방어력/막기확률/받피감/진형`\n"
                "예) `!방어력 플라튼/1800/100%/33%/밸런스`\n"
                "→ 내실 태오덱 기준으로, **막기 뜸/안 뜸** 데미지 및\n"
                "  방어 버퍼(루디 또는 앨리스) 채용 시 감소율을 함께 보여줍니다."
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
            name="🧱 방어력 (`!방어력`)",
            value=(
                "• **슬래시(/)** 로 구분합니다.\n"
                "• 형식: `!방어력 캐릭/스탯방어력/막기확률/받피감/진형`\n"
                "• 예) `!방어력 플라튼/1800/100%/33%/밸런스`\n"
                "  ↳ 막기 뜸/안 뜸 데미지, 그리고 방어 버퍼(루디/앨리스) 1명 채용 시 감소율을 함께 표시합니다."
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
    try:
        parts = [p.strip() for p in argline.split('/')]
        if len(parts) != 6:
            return await ctx.reply("❌ 형식: `!전투력 캐릭/스탯공/치확/치피/약확/세트`  예) `!전투력 태오/5338/5%/174%/20%/복수자`")

        character, stat_s, cr_s, cd_s, wr_s, set_name = parts
        if character not in ("태오", "콜트", "연희", "린", "세인", "파스칼"):
            return await ctx.reply("❌ 지원 캐릭터: `태오`, `콜트`, `연희`, `린`, `세인`, `파스칼`")

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


# =========================
# 신규 명령어: !방어력
# =========================
@bot.command(name="방어력")
async def cmd_defense(ctx, *, argline: str):
    """
    사용법:
    !방어력 캐릭/스탯방어력/막기확률/받피감/진형
    예) !방어력 플라튼/1800/100%/33%/밸런스
    """
    try:
        parts = [p.strip() for p in argline.split('/')]
        if len(parts) != 5:
            return await ctx.reply("❌ 형식: `!방어력 캐릭/스탯방어력/막기확률/받피감/진형`\n예) `!방어력 플라튼/1800/100%/33%/밸런스`")

        name, stat_def_s, block_rate_s, dtr_s, formation = parts

        # 지원 캐릭
        if name not in BASE_DEF_BY_CHAR:
            return await ctx.reply("❌ 지원 탱커: `루디`, `챈슬러`, `아라곤`, `플라튼`, `앨리스`, `스파이크`")

        # 수치 파싱
        try:
            stat_def = int(float(stat_def_s))
            block_rate = parse_percent(block_rate_s)  # 현재 출력은 막기/비막기 모두, 확률값은 참고용
            reduce_taken_r = parse_percent(dtr_s)
        except ValueError:
            return await ctx.reply("❌ 숫자 형식이 올바르지 않습니다. 예) `100%`, `33%` 처럼 % 포함")

        if formation not in FORMATION_DEFENSE_PERCENT:
            return await ctx.reply("❌ 진형은 `보호`, `밸런스`, `기본`, `공격` 중 하나여야 합니다.")

        # 시뮬레이션 실행 (버퍼 자동 추천: 본인이 루디면 앨리스, 아니면 루디)
        result = simulate_vs_teo(
            defender_name=name,
            stat_def=stat_def,
            reduce_taken_r=reduce_taken_r / 100.0,
            formation_name=formation,
            friend_buffer=None
        )

        buf = result["friend_buffer"]
        n_on = result["none"]["block_on"]
        n_off = result["none"]["block_off"]
        b_on = result["buff"]["block_on"]
        b_off = result["buff"]["block_off"]
        red_on = result["buff"]["reduced_on_pct"]
        red_off = result["buff"]["reduced_off_pct"]

        # 보기 좋은 출력
        embed = discord.Embed(
            title="vs 내실 태오덱 상대 데미지 시뮬레이터",
            description=f"입력: `{name}/{stat_def}/{block_rate_s}/{dtr_s}/{formation}`",
            color=0xA0522D
        )
        embed.add_field(
            name="(버퍼 미채용시)",
            value=(f"• 막기 **뜸** : **{n_on:,}**\n"
                   f"• 막기 **안 뜸** : **{n_off:,}**"),
            inline=False
        )
        embed.add_field(
            name=f"(버퍼-{buf} 채용시)",
            value=(f"• 막기 **뜸** : **{b_on:,}**  *(미채용 대비 {red_on}% 감소)*\n"
                   f"• 막기 **안 뜸** : **{b_off:,}** *(미채용 대비 {red_off}% 감소)*"),
            inline=False
        )
        embed.set_footer(text="규칙: 단계별 절사, 공퍼/피증은 기본 1에서 시작, 루디 감쇄는 최종 곱(×0.84)")

        await ctx.reply(embed=embed)
    except Exception:
        logger.error("!방어력 처리 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 방어력 계산 중 오류가 발생했어요.")


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
