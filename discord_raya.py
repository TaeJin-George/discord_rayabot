#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Counter Deck Chatbot (Cloudtype/GCP VM 모두 호환)

레포 구성:
1) discord_counter_bot.py
2) requirements.txt
"""
from __future__ import annotations
import os
import logging
import traceback
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

# (A1) 공격자(내실 태오덱)
TEO_STAT_ATK = 4458
TEO_BASE_ATK = 1500
TEO_CRIT_MULT = 2.64            # 태오: 항상 치명
TEO_SKILL_COEFF = 1.70          # 170%
PET_ATTACK_FLAT = 1119          # 이린 깡공
PET_ATTACK_PERCENT = 0.21       # 이린 공퍼 +21%
FORMATION_ATTACK_PERCENT = 0.42 # 보호 뒷줄 +42%
ATTACK_PERCENT_BUFFS = 0.25     # 아일린 +25% (공퍼 합산)
ATK_MULT_INCREASE_SUM = PET_ATTACK_PERCENT + ATTACK_PERCENT_BUFFS  # 0.46

# (A2) 공격자(속공 태오덱)
TEO_SOKGONG_STAT_ATK = 4088
TEO_SOKGONG_BASE_ATK = 1500
TEO_SOKGONG_CRIT_MULT = 2.10    # 치피 210% → ×2.10
TEO_SOKGONG_SKILL_COEFF = 1.70

# 피해량/약점/취약/방깎 등
WEAK_MULT_CHASER = 1.65
VULNERABILITY_PAI = 1.20
DEF_SHRED_VANESSA = 0.29
DEF_PENETRATION = 0.0

# (B) 방어측 공통(펫/진형/버퍼)
PET_DEFENSE_PERCENT = 0.13      # 펫 방어% +13
PET_DEFENSE_FLAT = 344          # 펫 깡방 +344

# 진형(앞줄 방어% — 공진_방어)
FORMATION_DEFENSE_PERCENT = {
    "보호": 0.105,
    "밸런스": 0.14,
    "기본": 0.21,
    "공격": 0.42,
}

# 방어 버퍼(보조 1명 시뮬) — 자체/보조 합산 가능
DEF_BUFFS = {
    "루디": {"def_percent": 0.24, "dampening": 0.16},  # 감쇄 16% (최종곱)
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

# 계산 규칙
DEF_COEFF_PER_DEF = 0.00214
BASIC_CRIT_MULT = 1.50
BLOCK_CRIT_MULT = 1.0
ROUND_FLOOR = True

# 캐릭 100점 상한(기존 딜러용)
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
    return character in ("태오", "파스칼")

def is_never_crit_and_weak(character: str) -> bool:
    return character == "콜트"

def parse_percent(x: str) -> float:
    return float(x.replace('%', '').strip())

def normalize_set(name: str):
    name = name.strip()
    if name == "추적자":
        return WEAK_MULT_CHASER, 1.0
    if name == "복수자":
        return 1.30, 1.30
    return 1.30, 1.0

def final_attack(stat_atk: float, character: str) -> float:
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

def _atk_final(stat_atk: int, base_atk: int, atk_reduce_sum: float) -> int:
    """
    ATK_final = floor( (stat_atk + 펫깡 + base*0.42) * (1 + 0.46 - atk_reduce_sum) )
    atk_reduce_sum: 챈슬러(-0.24), 아라곤(-0.13) 등 합산 (기본 0)
    """
    formation_flat = base_atk * FORMATION_ATTACK_PERCENT
    mult_atk_pct = max(0.0, 1.0 + ATK_MULT_INCREASE_SUM - atk_reduce_sum)
    val = (stat_atk + PET_ATTACK_FLAT + formation_flat) * mult_atk_pct
    return floor(val)

def _effective_def_and_coeff(
    defender_name: str,
    stat_def: int,
    formation_name: str,
    extra_def_percent_total: float
) -> Tuple[int, float]:
    """
    유효방어 = (기본방 + 장비방 + 펫깡방 + 기본방*공진_방어) * (1 + [펫방% + 자체/보조 방%] - 방깎) * (1 - 방무)
    DEFcoeff = 1 + floor(유효방어) * 0.00214
    """
    base_def = BASE_DEF_BY_CHAR.get(defender_name)
    if base_def is None:
        raise ValueError("지원하지 않는 탱커명입니다.")
    gear_def = max(0, stat_def - base_def)
    f_pct = FORMATION_DEFENSE_PERCENT.get(formation_name)
    if f_pct is None:
        raise ValueError("진형은 보호/밸런스/기본/공격 중 하나여야 합니다.")
    add_from_formation = base_def * f_pct

    def_percent_sum = PET_DEFENSE_PERCENT + extra_def_percent_total
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
    # 단계별 floor
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
    - 자기 자신이 앨리스/루디/챈슬러/아라곤일 때 자체 효과 자동 적용
    - 보조 버퍼(루디/앨리스) 1명 추가 적용
    - 내실/속공 태오 모두 계산
    """
    # 보조 버퍼 자동 추천
    if friend_buffer is None:
        friend_buffer = "앨리스" if defender_name == "루디" else "루디"

    # 1) 자체 효과
    self_def_pct = 0.0
    self_damp = 0.0
    atk_reduce_sum_self = 0.0    # 공퍼감소 합
    dmg_reduce_sum_self = 0.0    # 피증감소 합

    if defender_name == "앨리스":
        self_def_pct += DEF_BUFFS["앨리스"]["def_percent"]
    if defender_name == "루디":
        self_def_pct += DEF_BUFFS["루디"]["def_percent"]
        self_damp += DEF_BUFFS["루디"]["dampening"]
    if defender_name == "챈슬러":
        atk_reduce_sum_self += 0.24
        dmg_reduce_sum_self += 0.13
    if defender_name == "아라곤":
        atk_reduce_sum_self += 0.13

    # 2) 보조 버퍼 효과(루디/앨리스)
    friend_def_pct = 0.0
    friend_damp = 0.0
    if friend_buffer in DEF_BUFFS:
        friend_def_pct += DEF_BUFFS[friend_buffer]["def_percent"]
        friend_damp += DEF_BUFFS[friend_buffer]["dampening"]

    # 최종 방어 버프 합/감쇄
    total_def_pct = self_def_pct + friend_def_pct
    total_damp = self_damp + friend_damp   # 루디만 0.16, 동시에 두 명일 일은 없음(자동 선택 로직상)

    # 3) 공격자 계수(공퍼/피증)
    atk_reduce_sum_total = atk_reduce_sum_self                 # (보조 버퍼로 챈슬러/아라곤은 현재 미지원)
    dmg_reduce_sum_total = dmg_reduce_sum_self                 # 챈슬러 -13%만 존재
    dmg_increase_mult = max(0.0, 1.0 + 0.0 - dmg_reduce_sum_total)  # 기본1 - 감소합

    # 4) 유효방어 & DEFcoeff (미채용/보조채용 각각)
    eff_def_none, defcoeff_none = _effective_def_and_coeff(
        defender_name, stat_def, formation_name, extra_def_percent_total=self_def_pct  # 자기 버프만
    )
    eff_def_buff, defcoeff_buff = _effective_def_and_coeff(
        defender_name, stat_def, formation_name, extra_def_percent_total=total_def_pct # 자기 + 보조
    )

    # 5) ATK_final (내실/속공)
    atk_final_core = _atk_final(TEO_STAT_ATK, TEO_BASE_ATK, atk_reduce_sum_total)
    atk_final_sok  = _atk_final(TEO_SOKGONG_STAT_ATK, TEO_SOKGONG_BASE_ATK, atk_reduce_sum_total)

    # 6) 데미지 (내실/속공 × 막기/비막기 × 미채용/보조)
    # 내실 - 미채용
    dmg_block_on_none = _damage_pipeline(atk_final_core, defcoeff_none, BLOCK_CRIT_MULT, TEO_SKILL_COEFF,
                                         dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                         reduce_taken_r, self_damp)
    dmg_block_off_none = _damage_pipeline(atk_final_core, defcoeff_none, TEO_CRIT_MULT, TEO_SKILL_COEFF,
                                          dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                          reduce_taken_r, self_damp)
    # 내실 - 보조
    dmg_block_on_buff = _damage_pipeline(atk_final_core, defcoeff_buff, BLOCK_CRIT_MULT, TEO_SKILL_COEFF,
                                         dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                         reduce_taken_r, total_damp)
    dmg_block_off_buff = _damage_pipeline(atk_final_core, defcoeff_buff, TEO_CRIT_MULT, TEO_SKILL_COEFF,
                                          dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                          reduce_taken_r, total_damp)
    # 속공 - 미채용
    dmg_block_on_none_sok = _damage_pipeline(atk_final_sok, defcoeff_none, BLOCK_CRIT_MULT, TEO_SOKGONG_SKILL_COEFF,
                                             dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                             reduce_taken_r, self_damp)
    dmg_block_off_none_sok = _damage_pipeline(atk_final_sok, defcoeff_none, TEO_SOKGONG_CRIT_MULT, TEO_SOKGONG_SKILL_COEFF,
                                              dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                              reduce_taken_r, self_damp)
    # 속공 - 보조
    dmg_block_on_buff_sok = _damage_pipeline(atk_final_sok, defcoeff_buff, BLOCK_CRIT_MULT, TEO_SOKGONG_SKILL_COEFF,
                                             dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                             reduce_taken_r, total_damp)
    dmg_block_off_buff_sok = _damage_pipeline(atk_final_sok, defcoeff_buff, TEO_SOKGONG_CRIT_MULT, TEO_SOKGONG_SKILL_COEFF,
                                              dmg_increase_mult, WEAK_MULT_CHASER, VULNERABILITY_PAI,
                                              reduce_taken_r, total_damp)

    def pct_reduced(new: int, base: int) -> float:
        if base <= 0:
            return 0.0
        return round((base - new) / base * 100.0, 1)

    red_on = pct_reduced(dmg_block_on_buff, dmg_block_on_none)
    red_off = pct_reduced(dmg_block_off_buff, dmg_block_off_none)
    red_on_sok = pct_reduced(dmg_block_on_buff_sok, dmg_block_on_none_sok)
    red_off_sok = pct_reduced(dmg_block_off_buff_sok, dmg_block_off_none_sok)

    return {
        "friend_buffer": friend_buffer,
        "none": {
            "block_on": dmg_block_on_none,
            "block_off": dmg_block_off_none,
            "eff_def": eff_def_none,
            "defcoeff": defcoeff_none,
            "sok_block_on": dmg_block_on_none_sok,
            "sok_block_off": dmg_block_off_none_sok,
        },
        "buff": {
            "block_on": dmg_block_on_buff,
            "block_off": dmg_block_off_buff,
            "eff_def": eff_def_buff,
            "defcoeff": defcoeff_buff,
            "reduced_on_pct": red_on,
            "reduced_off_pct": red_off,
            "sok_block_on": dmg_block_on_buff_sok,
            "sok_block_off": dmg_block_off_buff_sok,
            "sok_reduced_on_pct": red_on_sok,
            "sok_reduced_off_pct": red_off_sok,
        }
    }

# -----------------------------
# 로깅
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
# 데이터 로더
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
# 디스코드 봇
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")
EXCEL_FILE = os.getenv("DATA_SHEET_URL") or os.getenv("EXCEL_FILE_PATH", "카운터덱.xlsx")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
data_store = DataStore(EXCEL_FILE)
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
        await dst.send(text); return
    for i in range(0, len(text), MAX):
        await dst.send(text[i:i+MAX])

# =========================
# 명령어
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
            value=("`!조합 A,B,C`\n"
                   "`!조합 A,B,C,스킬1,스킬2,스킬3`"),
            inline=False
        )
        embed.add_field(
            name="⚔️ 딜러 전투력 계산",
            value=("`!전투력 캐릭/스탯공/치확/치피/약확/세트`\n"
                   "예) `!전투력 태오/5338/5%/174%/20%/복수자`"),
            inline=False
        )
        embed.add_field(
            name="🧱 탱커 방어력 시뮬레이터",
            value=("`!방어력 캐릭/스탯방어력/막기확률/받피감/진형`\n"
                   "예) `!방어력 플라튼/1800/100%/33%/밸런스`"),
            inline=False
        )
        embed.add_field(
            name="🔄 데이터 관리",
            value=("`!리로드`, `!상태`"),
            inline=False
        )
        await ctx.send(embed=embed)
    except Exception:
        logger.error("!도움말 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 도움말 표시 중 오류가 발생했어요.")

@bot.command(name="상태")
async def status_cmd(ctx: commands.Context):
    try:
        if data_store.df is None:
            await ctx.send("데이터: 로드 실패 또는 없음"); return
        shape = data_store.df.shape
        cols = ", ".join(map(str, data_store.df.columns))
        await send_long_message(ctx, f"데이터 로드됨: {shape[0]}행 x {shape[1]}열\n컬럼: {cols}")
    except Exception:
        logger.error("!상태 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 상태 확인 중 오류가 발생했어요.")

@bot.command(name="리로드")
async def reload_cmd(ctx: commands.Context):
    try:
        data_store.load()
        await ctx.send("✅ 데이터 리로드 완료" if data_store.df is not None else "❌ 데이터 로드 실패")
    except Exception:
        logger.error("!리로드 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 리로드 중 오류가 발생했어요.")

@bot.command(name="조합")
async def combo_cmd(ctx: commands.Context, *, args: str = ""):
    try:
        tokens = args.split(",") if args else []
        if len(tokens) not in (3, 6):
            await ctx.send("❌ 입력은 쉼표로만 구분. 예) `!조합 A,B,C` 혹은 `!조합 A,B,C,스킬1,스킬2,스킬3`"); return
        raw_team = tokens[:3]; raw_skills = tokens[3:] if len(tokens) == 6 else None

        results = data_store.search_counters(raw_team, raw_skills)
        header = f"🎯 상대 조합: `{', '.join(sorted(team_exact(raw_team)))}`"
        if raw_skills:
            header += f" | 🧩 방어 스킬: `{' → '.join(skills_order_exact(raw_skills))}`"
        header += "\n"

        if not results:
            await ctx.send(f"⚠️ 조건에 맞는 데이터가 없습니다.\n{header}"); return

        chunks: List[str] = [header]
        for i, r in enumerate(results, 1):
            combo = ", ".join([x for x in r['조합'] if x]) or "정보 없음"
            skills = " → ".join([x for x in r['스킬'] if x]) or "정보 없음"
            first = r.get("선공", "정보 없음")
            chunks.append(f"\n🛡️ **카운터 #{i}**\n- 조합: `{combo}`\n- 스킬: `{skills}`\n- 선공 여부: `{first}`\n")
        await send_long_message(ctx, "".join(chunks))
    except Exception:
        logger.error("!조합 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 요청 처리 중 오류가 발생했어요.")

@bot.command(name="사용법")
async def manual_cmd(ctx: commands.Context):
    try:
        embed = discord.Embed(
            title="📖 사용법",
            description="명령어와 입력 규칙을 확인하세요.",
            color=0x00BFFF
        )
        embed.add_field(
            name="🧱 방어력 (`!방어력`)",
            value=("• 형식: `!방어력 캐릭/스탯방어력/막기확률/받피감/진형`\n"
                   "• 예: `!방어력 플라튼/1800/100%/33%/밸런스`\n"
                   "  ↳ **자체 버프/디버프**(앨리스/루디/챈슬러/아라곤) 자동 적용 + 보조 버퍼 1명(루디/앨리스)"),
            inline=False
        )
        embed.add_field(
            name="⚔️ 전투력 (`!전투력`)",
            value=("• 형식: `!전투력 캐릭/스탯공/치확/치피/약확/세트`\n"
                   "• 예: `!전투력 태오/5338/5%/174%/20%/복수자`"),
            inline=False
        )
        await ctx.send(embed=embed)
    except Exception:
        logger.error("!사용법 오류:\n" + traceback.format_exc())
        await ctx.send("⚠️ 요청 처리 중 오류가 발생했어요.")

@bot.command(name="전투력")
async def cmd_power(ctx, *, argline: str):
    try:
        parts = [p.strip() for p in argline.split('/')]
        if len(parts) != 6:
            return await ctx.reply("❌ 형식: `!전투력 캐릭/스탯공/치확/치피/약확/세트`")
        character, stat_s, cr_s, cd_s, wr_s, set_name = parts
        if character not in ("태오","콜트","연희","린","세인","파스칼"):
            return await ctx.reply("❌ 지원 캐릭터: `태오`, `콜트`, `연희`, `린`, `세인`, `파스칼`")
        try:
            stat_atk  = float(stat_s)
            crit_rate = parse_percent(cr_s)
            crit_dmg  = parse_percent(cd_s)
            weak_rate = parse_percent(wr_s)
        except ValueError:
            return await ctx.reply("❌ 숫자 형식 오류. 예: `5%`, `174%`, `20%`")
        atk, dmg_w, dmg_nw, dmg_exp = compute_damage(character, stat_atk, crit_rate, crit_dmg, weak_rate, set_name)
        score_w  = score_from_cap(character, dmg_w)
        score_nw = score_from_cap(character, dmg_nw)
        score_av = score_from_cap(character, dmg_exp)
        if character == "콜트":
            msg = f"**{character} / {set_name}**\n- 폭탄 전투력: **{score_av}점**"
        else:
            msg = (f"**{character} / {set_name}**\n"
                   f"- 기대 전투력: **{score_av}점**\n"
                   f"- 전투력(약점O): **{score_w}점**\n"
                   f"- 전투력(약점X): **{score_nw}점**")
        await ctx.reply(msg)
    except Exception:
        logger.error("!전투력 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 전투력 계산 중 오류가 발생했어요.")

# =========================
# 신규 명령어: !방어력
# =========================
@bot.command(name="방어력")
async def cmd_defense(ctx, *, argline: str):
    """
    사용법: !방어력 캐릭/스탯방어력/막기확률/받피감/진형
    예) !방어력 플라튼/1800/100%/33%/밸런스
    """
    try:
        parts = [p.strip() for p in argline.split('/')]
        if len(parts) != 5:
            return await ctx.reply("❌ 형식: `!방어력 캐릭/스탯방어력/막기확률/받피감/진형`\n예) `!방어력 플라튼/1800/100%/33%/밸런스`")
        name, stat_def_s, block_rate_s, dtr_s, formation = parts

        if name not in BASE_DEF_BY_CHAR:
            return await ctx.reply("❌ 지원 탱커: `루디`, `챈슬러`, `아라곤`, `플라튼`, `앨리스`, `스파이크`")
        try:
            stat_def = int(float(stat_def_s))
            block_rate = parse_percent(block_rate_s)  # 표기용
            reduce_taken_r = parse_percent(dtr_s)
        except ValueError:
            return await ctx.reply("❌ 숫자 형식 오류. 예) `100%`, `33%`")
        if formation not in FORMATION_DEFENSE_PERCENT:
            return await ctx.reply("❌ 진형은 `보호`, `밸런스`, `기본`, `공격` 중 하나여야 합니다.")

        # 시뮬레이션 (보조 버퍼 자동: 본인이 루디면 앨리스, 아니면 루디)
        result = simulate_vs_teo(
            defender_name=name,
            stat_def=stat_def,
            reduce_taken_r=reduce_taken_r / 100.0,
            formation_name=formation,
            friend_buffer=None
        )

        buf = result["friend_buffer"]
        # 내실
        n_on = result["none"]["block_on"]; n_off = result["none"]["block_off"]
        b_on = result["buff"]["block_on"]; b_off = result["buff"]["block_off"]
        red_on = result["buff"]["reduced_on_pct"]; red_off = result["buff"]["reduced_off_pct"]
        # 속공
        n_on_sok = result["none"]["sok_block_on"]; n_off_sok = result["none"]["sok_block_off"]
        b_on_sok = result["buff"]["sok_block_on"]; b_off_sok = result["buff"]["sok_block_off"]
        red_on_sok = result["buff"]["sok_reduced_on_pct"]; red_off_sok = result["buff"]["sok_reduced_off_pct"]

                # 보기 좋은 출력 (임베드: 문구/레이아웃 커스텀)
        embed = discord.Embed(
            title="vs 태오덱 상대 데미지 시뮬레이터",
            description=(
                f"입력: {name}/ {stat_def}/ {block_rate_s}/ {dtr_s}/ {formation}\n\n"
                "공격자: 내실(공4458, 치피264) & 속공(공4088, 치피210)"
                " — 추적자·이린펫·보호뒷줄·파이·아일린"
            ),
            color=0xA0522D
        )

        # 내실 태오 - 미채용
        embed.add_field(
            name="(내실 태오 - 방어 버퍼 미채용)",
            value=(
                f"• 막기 뜸 : **{n_on:,}**\n"
                f"• 막기 안뜸 : **{n_off:,}**"
            ),
            inline=False
        )
        # 내실 태오 - 보조 버퍼
        embed.add_field(
            name=f"(내실 태오 - 방어 버퍼-{buf} 채용시 최종딜 {red_off:.1f}% 감소)",
            value=(
                f"• 막기 뜸 : **{b_on:,}**\n"
                f"• 막기 안뜸 : **{b_off:,}**"
            ),
            inline=False
        )
        # 속공 태오 - 미채용
        embed.add_field(
            name="(속공 태오 - 방어 버퍼 미채용)",
            value=(
                f"• 막기 뜸 : **{n_on_sok:,}**\n"
                f"• 막기 안뜸 : **{n_off_sok:,}**"
            ),
            inline=False
        )
        # 속공 태오 - 보조 버퍼
        embed.add_field(
            name=f"(속공 태오 - 방어 버퍼-{buf} 채용시 최종딜 {red_off_sok:.1f}% 감소)",
            value=(
                f"• 막기 뜸 : **{b_on_sok:,}**\n"
                f"• 막기 안뜸 : **{b_off_sok:,}**"
            ),
            inline=False
        )

        # 하단 주석
        embed.set_footer(text="파이 아래 후 태오 위 or 아래 쓸때 들어오는 데미지입니다.")
        await ctx.reply(embed=embed)
    except Exception:
        logger.error("!방어력 오류:\n" + traceback.format_exc())
        await ctx.reply("⚠️ 방어력 계산 중 오류가 발생했어요.")

@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    try:
        if isinstance(error, commands.CommandNotFound):
            await ctx.send("알 수 없는 명령어입니다. `!도움말`을 입력해 보세요."); return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("필수 인자가 누락됐어요. `!도움말`을 참고하세요."); return
        logger.error("on_command_error:\n" + traceback.format_exc())
        await ctx.send("⚠️ 처리 중 오류가 발생했어요.")
    except Exception:
        logger.error("on_command_error 핸들러 오류:\n" + traceback.format_exc())

if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN", "")
    if not TOKEN:
        logger.error("DISCORD_TOKEN 이 설정되지 않았습니다 (.env/환경변수 확인)")
    else:
        try:
            bot.run(TOKEN)
        except Exception:
            logger.critical("디스코드 런타임 크래시:\n" + traceback.format_exc())