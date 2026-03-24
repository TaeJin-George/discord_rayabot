from __future__ import annotations

import logging
import os
import traceback
from typing import Any, Dict, List, Optional

import pandas as pd

from common import (
    _canon_team_key,
    _csv_url_from_sheet,
    _guess_gid_from_url,
    _is_yes,
    _s,
    _safe_int,
    _winrate,
)

logger = logging.getLogger("counter-bot")


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


class DataStore:
    def __init__(self, sheet_url: str):
        self.sheet_url = os.getenv("DATA_SHEET_URL") or sheet_url
        self.df: Optional[pd.DataFrame] = None

    def load(self) -> None:
        try:
            gid = _guess_gid_from_url(self.sheet_url)
            csv_url = _csv_url_from_sheet(self.sheet_url, gid)
            logger.info(f"Loading counter sheet CSV: {csv_url}")

            df = pd.read_csv(csv_url, dtype=str, keep_default_na=False)
            df.columns = [str(c).strip() for c in df.columns]

            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                logger.warning(f"카운터 시트 누락 컬럼 자동 생성: {missing}")
                for c in missing:
                    df[c] = ""

            self.df = df
            logger.info(f"Loaded counter data: shape={df.shape}")
        except Exception:
            logger.error("카운터 데이터 로드 실패:\n" + traceback.format_exc())
            self.df = None

    def search_by_enemy(self, enemy_team_input: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self.df is None or self.df.empty:
            return results

        want = _canon_team_key(enemy_team_input)
        if len(want) != 3:
            return results

        for _, row in self.df.iterrows():
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
                "recommend": _is_yes(row.get("recommend")),
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

        results.sort(key=lambda x: (1 if x.get("recommend") else 0, x["rate"], x["total"]), reverse=True)
        return results