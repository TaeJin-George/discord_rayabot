from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord

from common import _badge_for_item, _format_blockquote


FORMATION_LAYOUT: Dict[str, Dict[str, List[int]]] = {
    "공격":   {"front": [1],          "back": [2, 3, 4, 5]},
    "기본":   {"front": [1, 2],       "back": [3, 4, 5]},
    "밸런스": {"front": [1, 2, 3],    "back": [4, 5]},
    "보호":   {"front": [1, 2, 3, 4], "back": [5]},
}


def build_detail_embed(enemy_disp: str, item: Dict[str, Any]) -> discord.Embed:
    win, lose = item["win"], item["lose"]
    total = win + lose
    rate = item["rate"] * 100.0
    counter_combo = ", ".join([x for x in item["counter_disp"] if x]) or "정보 없음"

    is_rec = bool(item.get("recommend"))
    badge = "\n⭐ **추천 카운터**" if is_rec else ""
    title = f"⭐ `{enemy_disp}` 추천 카운터 상세" if is_rec else f"🧩 `{enemy_disp}` 카운터 상세"
    color = 0x2ECC71 if is_rec else 0x5865F2

    embed = discord.Embed(
        title=title,
        description=(
            f"🛡️ 카운터: `{counter_combo}`"
            f"{badge}\n"
            f"📊 전적: **{win}승 {lose}패** (승률 **{rate:.1f}%**, {total}판)"
        ),
        color=color
    )

    formation = item.get("formation", "")
    pet = item.get("pet", "")

    layout = FORMATION_LAYOUT.get((formation or "").strip(), FORMATION_LAYOUT["기본"])
    front_order = [f"pos{n}" for n in layout["front"]]
    back_order = [f"pos{n}" for n in layout["back"]]

    pos_map = {p["pos"]: p for p in item.get("positions", [])}

    def fmt_line(pos_key: str, icon: str = "") -> Optional[str]:
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
        prefix = f"{icon} " if icon else ""
        return f"- {prefix}**{d['unit']}**" + (f" - {tail}" if tail else "")

    lines: List[str] = []
    lines.append(f"🧩 **진형** : `{formation or '정보 없음'}`")
    lines.append(f"🏁 선공: `{item.get('first', '정보 없음')}`")

    front_lines = [ln for k in front_order if (ln := fmt_line(k))]
    back_lines = [ln for k in back_order if (ln := fmt_line(k))]

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


def build_stats_embed(
    title: str,
    target_disp: str,
    lines: List[str],
    subtitle: str,
    color: int = 0x3498DB,
) -> discord.Embed:
    description = f"🎯 대상 조합: `{target_disp}`\n📌 {subtitle}\n\n"
    description += "\n".join(lines) if lines else "조건에 맞는 통계가 없습니다."

    return discord.Embed(
        title=title,
        description=description[:4096],
        color=color,
    )


class CounterSelect(discord.ui.Select):
    def __init__(self, enemy_disp: str, results: List[Dict[str, Any]]):
        self.enemy_disp = enemy_disp
        self.results = results

        options: List[discord.SelectOption] = []
        for i, item in enumerate(results[:25]):
            rank = i + 1
            win, lose = item["win"], item["lose"]
            total = win + lose
            rate = item["rate"] * 100.0

            combo = ", ".join([x for x in item["counter_disp"] if x]) or "정보 없음"
            star = _badge_for_item(item, rank)
            rec = "추천 · " if item.get("recommend") else ""

            label = f"{star}{rank}. {combo}"
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