#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
길드전 카운터덱 봇
- 조합 입력 파서: 쉼표 뒤 공백 허용
- 조합 결과: reply 형태
- 카운터 목록 Select(드롭다운)로 상세 임베드 표시
- '기본 세팅' 필드 제거, '세팅'만 사용

[추가]
- disable, recommend 컬럼 지원 (입력: "Y" 또는 Null)
  - disable=Y : 목록에서 제외(논리 삭제)
  - recommend=Y : 승률/판수와 무관하게 목록 상단 "추천"으로 표시
"""

from __future__ import annotations

import os
import re
import logging
import traceback
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import discord
from discord.ext import commands
import pandas as pd
from dotenv import load_dotenv


# =========================
# 로깅
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("counter-bot")


# =========================
# 진형(전열/후열 고정 규칙)
# =========================
FORMATION_LAYOUT: Dict[str, Dict[str, List[int]]] = {
    "공격":   {"front": [1],          "back": [2, 3, 4, 5]},
    "기본":   {"front": [1, 2],       "back": [3, 4, 5]},
    "밸런스": {"front": [1, 2, 3],    "back": [4, 5]},
    "보호":   {"front": [1, 2, 3, 4], "back": [5]},
}


# =========================
# 컬럼 스키마
# =========================
REQUIRED_COLUMNS = [
    "id",
    "enemy1", "enemy2", "enemy3",
    "counter1", "counter2", "counter3",
    "first",
    "win", "lose",
    "formation",
    "pos1", "pos1_set", "pos1_opt", "pos1_ring",
    "pos2", "pos2_set", "pos2_opt", "pos2_ring",
    "pos3", "pos3_set", "pos3_opt", "pos3_ring",
    "pos4", "pos4_set", "pos4_opt", "pos4_ring",
    "pos5", "pos5_set", "pos5_opt", "pos5_ring",
    "skill1", "skill2", "skill3",
    "pet",
    "notes",
    # 신규 컬럼 (없어도 load()에서 자동 생성)
    "disable",
    "recommend",
]

POS_COLS = [
    ("pos1", "pos1_set", "pos1_opt", "pos1_ring"),
    ("pos2", "pos2_set", "pos2_opt", "pos2_ring"),
    ("pos3", "pos3_set", "pos3_opt", "pos3_ring"),
    ("pos4", "pos4_set", "pos4_opt", "pos4_ring"),
    ("pos5", "pos5_set", "pos5_opt", "pos5_ring"),
]


# =========================
# 유틸
# =========================
def _s(val: Any) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


def _is_yes(val: Any) -> bool:
    # 입력이 Y 또는 y 여도 인정, 공백/None 안전
    return _s(val).upper() == "Y"


def _safe_int(x: Any) -> int:
    t = _s(x)
    try:
        return int(float(t)) if t else 0
    except Exception:
        return 0


def _winrate(win: int, lose: int) -> float:
    total = win + lose
    return win / total if total > 0 else 0.0


def _canon_team_key(names: Sequence[Any]) -> Tuple[str, ...]:
    return tuple(sorted([_s(n) for n in names if _s(n)]))


def _split_csv_args(s: str) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in re.split(r"[,\uFF0C\u3001]", s) if x.strip()]


def _format_blockquote(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(["> " + ln if ln else ">" for ln in text.split("\n")])


# =========================
# 구글시트 URL -> CSV
# =========================
_GS_PREFIX = "https://docs.google.com/spreadsheets/d/"


def _extract_sheet_id(sheet_url_or_id: str) -> str:
    if _GS_PREFIX in str(sheet_url_or_id):
        return str(sheet_url_or_id).split("/spreadsheets/d/")[1].split("/")[0]
    return str(sheet_url_or_id)


def _guess_gid_from_url(url: str) -> Optional[int]:
    m = re.search(r"gid=(\d+)", str(url))
    return int(m.group(1)) if m else None


def _csv_url_from_sheet(sheet_url_or_id: str, gid: Optional[int]) -> str:
    sheet_id = _extract_sheet_id(sheet_url_or_id)
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
    params = {"format": "csv"}
    if gid is not None:
        params["gid"] = str(gid)
    return f"{base}?{urlencode(params)}"


# =========================
# 데이터 로더
# =========================
class DataStore:
    def __init__(self, sheet_url: str):
        self.sheet_url = os.getenv("DATA_SHEET_URL") or sheet_url
        self.df: Optional[pd.DataFrame] = None

    def load(self) -> None:
        try:
            gid = _guess_gid_from_url(self.sheet_url)
            csv_url = _csv_url_from_sheet(self.sheet_url, gid)
            logger.info(f"Loading Google Sheet CSV: {csv_url}")

            df = pd.read_csv(csv_url, dtype=str, keep_default_na=False)
            df.columns = [str(c).strip() for c in df.columns]

            # 누락 컬럼 자동 생성 (기존 시트 호환)
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                logger.warning(f"시트에 누락된 컬럼이 있어 자동 생성합니다: {missing}")
                for c in missing:
                    df[c] = ""

            self.df = df
            logger.info(f"Loaded data: shape={df.shape}")
        except Exception:
            logger.error("데이터 로드 실패:\n" + traceback.format_exc())
            self.df = None

    def search_by_enemy(self, enemy_team_input: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self.df is None or self.df.empty:
            return results

        want = _canon_team_key(enemy_team_input)
        if len(want) != 3:
            return results

        for _, row in self.df.iterrows():
            # disable=Y 인 행은 논리 삭제 처리
            if _is_yes(row.get("disable")):
                continue

            enemy_key = _canon_team_key([row.get("enemy1"), row.get("enemy2"), row.get("enemy3")])
            if enemy_key != want:
                continue

            counter_disp = [_s(row.get("counter1")), _s(row.get("counter2")), _s(row.get("counter3"))]
            if not any(counter_disp):
                continue

            win = _safe_int(row.get("win"))
            lose = _safe_int(row.get("lose"))
            total = win + lose

            is_recommend = _is_yes(row.get("recommend"))

            item = {
                "id": _s(row.get("id")),
                "enemy_disp": ", ".join(want),
                "counter_disp": counter_disp,
                "first": _s(row.get("first")) or "정보 없음",
                "win": win,
                "lose": lose,
                "total": total,
                "rate": _winrate(win, lose),
                "formation": _s(row.get("formation")),
                "pet": _s(row.get("pet")),
                "notes": _s(row.get("notes")),
                "skill_texts": [_s(row.get("skill1")), _s(row.get("skill2")), _s(row.get("skill3"))],
                "positions": [],
                "recommend": is_recommend,
            }

            for p, s_col, o_col, r_col in POS_COLS:
                item["positions"].append({
                    "pos": p,
                    "unit": _s(row.get(p)),
                    "set": _s(row.get(s_col)),
                    "opt": _s(row.get(o_col)),
                    "ring": _s(row.get(r_col)),
                })

            results.append(item)

        # 정렬 우선순위:
        # 1) recommend=Y 최상단
        # 2) 승률
        # 3) 판수(승+패)
        results.sort(key=lambda x: (1 if x.get("recommend") else 0, x["rate"], x["total"]), reverse=True)
        return results


# =========================
# 임베드 / Select UI
# =========================
def build_detail_embed(enemy_disp: str, item: Dict[str, Any]) -> discord.Embed:
    win, lose = item["win"], item["lose"]
    total = win + lose
    rate = item["rate"] * 100.0
    counter_combo = ", ".join([x for x in item["counter_disp"] if x]) or "정보 없음"

    embed = discord.Embed(
        title=f"🧩 `{enemy_disp}` 카운터 상세",
        description=(
            f"🛡️ 카운터: `{counter_combo}`{badge}\n"
            f"📊 전적: **{win}승 {lose}패** (승률 **{rate:.1f}%**, {total}판)"
        ),
        color=0x5865F2
    )

    # ===== 세팅(전열/후열/펫) =====
    formation = item.get("formation", "")
    pet = item.get("pet", "")

    layout = FORMATION_LAYOUT.get((formation or "").strip(), FORMATION_LAYOUT["기본"])
    front_order = [f"pos{n}" for n in layout["front"]]
    back_order  = [f"pos{n}" for n in layout["back"]]

    pos_map = {p["pos"]: p for p in item.get("positions", [])}

    def fmt_line(pos_key: str, icon: str) -> Optional[str]:
        d = pos_map.get(pos_key)
        if not d or not d.get("unit"):
            return None
        parts = []
        if d.get("set"):
            parts.append(f"세트 : `{d['set']}`")
        if d.get("opt"):
            parts.append(f"옵션 : `{d['opt']}`")
        if d.get("ring"):
            parts.append(f"반지 : `{d['ring']}`")
        tail = " / ".join(parts)
        return f"- {icon} **{d['unit']}**" + (f" - {tail}" if tail else "")

    lines: List[str] = []
    lines.append(f"🧩 **진형** : `{formation or '정보 없음'}`\n")
    lines.append(f"🏁 선공: `{item.get('first','정보 없음')}`")

    front_lines = [ln for k in front_order if (ln := fmt_line(k, ""))]
    back_lines  = [ln for k in back_order  if (ln := fmt_line(k, ""))]

    if front_lines:
        lines.append("\n🛡️ **전열**")
        lines.extend(front_lines)
    if back_lines:
        lines.append("\n⚔️ **후열**")
        lines.extend(back_lines)
    if pet:
        lines.append("\n🐾 **펫**")
        lines.append(f"- `{pet}`")

    embed.add_field(name="⚙️ 세팅", value="\n".join(lines)[:1024], inline=False)

    # 스킬 순서
    skill_texts = [t for t in item.get("skill_texts", []) if t]
    if skill_texts:
        embed.add_field(
            name="🗺️ 스킬 순서",
            value=f"`{' → '.join(skill_texts)}`",
            inline=False
        )

    notes = item.get("notes", "")
    if notes:
        embed.add_field(name="📝 참고", value=_format_blockquote(notes)[:1024], inline=False)

    return embed


class CounterSelect(discord.ui.Select):
    def __init__(self, enemy_disp: str, results: List[Dict[str, Any]]):
        self.enemy_disp = enemy_disp
        self.results = results

        options: List[discord.SelectOption] = []
        for i, item in enumerate(results[:25]):
            win, lose = item["win"], item["lose"]
            total = win + lose
            rate = item["rate"] * 100.0

            rec = "추천 · " if item.get("recommend") else ""
            combo = ", ".join([x for x in item["counter_disp"] if x]) or "정보 없음"

            # label: 너무 길어지면 잘리므로 심플하게
            label = f"{i+1}. {combo}"
            desc = f"{rec}{rate:.0f}% · {total}판"

            options.append(discord.SelectOption(
                label=label[:100],
                description=desc[:100],
                value=str(i),
            ))

        super().__init__(placeholder="보고 싶은 카운터를 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        embed = build_detail_embed(self.enemy_disp, self.results[idx])
        await interaction.response.edit_message(embed=embed, view=self.view)


class CounterView(discord.ui.View):
    def __init__(self, enemy_disp: str, results: List[Dict[str, Any]]):
        super().__init__(timeout=180)
        self.add_item(CounterSelect(enemy_disp, results))


# =========================
# 디스코드 봇
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")
SHEET_URL_DEFAULT = "https://docs.google.com/spreadsheets/d/PUT_YOUR_ID_HERE/edit?gid=0#gid=0"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
data_store = DataStore(SHEET_URL_DEFAULT)
data_store.load()


@bot.event
async def on_ready():
    logger.info(f"✅ 로그인 완료: {bot.user} (guilds={len(bot.guilds)})")


@bot.command(name="리로드")
async def reload_cmd(ctx: commands.Context):
    """구글시트 데이터를 다시 로드합니다."""
    try:
        data_store.load()
        if data_store.df is None:
            await ctx.reply("❌ 데이터 로드 실패", mention_author=False)
        else:
            await ctx.reply("✅ 데이터 리로드 완료", mention_author=False)
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

        # 표시/검색 키 통일 (공백/정렬 혼선 방지)
        want = _canon_team_key(tokens)
        enemy_disp = ", ".join(want)

        results = data_store.search_by_enemy(list(want))

        if not results:
            await ctx.reply(
                f"⚠️ 조건에 맞는 카운터 데이터가 없습니다.\n🎯 상대 조합: `{enemy_disp}`",
                mention_author=False
            )
            return

        lines: List[str] = []
        for i, item in enumerate(results[:10], 1):
            rate = item["rate"] * 100.0
            total = item["win"] + item["lose"]
            combo = ", ".join([x for x in item["counter_disp"] if x]) or "정보 없음"

            badge = "🟩 **추천** " if item.get("recommend") else ""
            lines.append(f"{badge}{i}. `{combo}` — **{rate:.0f}%** ({total}판)")

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


if __name__ == "__main__":
    if not TOKEN:
        logger.error("DISCORD_TOKEN 이 설정되지 않았습니다 (.env/환경변수 확인)")
    else:
        bot.run(TOKEN)
