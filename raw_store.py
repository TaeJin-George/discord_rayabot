from __future__ import annotations

import logging
import os
import traceback
from typing import Any, Dict, List, Optional

import pandas as pd

from common import (
    MIN_STAT_TRIES,
    _canon_team_key,
    _csv_url_from_sheet,
    _is_yes,
    _join_team_disp,
    _join_team_key,
    _result_is_attack_lose,
    _result_is_attack_win,
    _s,
)

logger = logging.getLogger("counter-bot")


RAW_REQUIRED_COLUMNS = [
    "방어조합1", "방어조합2", "방어조합3",
    "공격조합1", "공격조합2", "공격조합3",
    "승패여부",
    "시즌",
    "비고",
    "방어key",
    "공격key",
    "COUNT",
    "기준",
    "방어메인",
    "방어조합",
    "공격조합",
]


class RawMatchStore:
    def __init__(self, sheet_url: str, raw_gid: str):
        self.sheet_url = os.getenv("DATA_SHEET_URL") or sheet_url
        self.raw_gid = os.getenv("RAW_SHEET_GID") or raw_gid
        self.df: Optional[pd.DataFrame] = None

    def load(self) -> None:
        try:
            gid = int(str(self.raw_gid))
            csv_url = _csv_url_from_sheet(self.sheet_url, gid)
            logger.info(f"Loading raw sheet CSV: {csv_url}")

            df = pd.read_csv(csv_url, dtype=str, keep_default_na=False)
            df.columns = [str(c).strip() for c in df.columns]

            missing = [c for c in RAW_REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                logger.warning(f"raw 시트 누락 컬럼 자동 생성: {missing}")
                for c in missing:
                    df[c] = ""

            df = df[df["COUNT"].apply(_is_yes)].copy()
            df.reset_index(drop=True, inplace=True)

            self.df = df
            logger.info(f"Loaded raw data: shape={df.shape}")
        except Exception:
            logger.error("raw 데이터 로드 실패:\n" + traceback.format_exc())
            self.df = None

    def _defense_key_from_row(self, row: pd.Series) -> str:
        key = _s(row.get("방어key"))
        if key:
            return key
        return _join_team_key([row.get("방어조합1"), row.get("방어조합2"), row.get("방어조합3")])

    def _attack_key_from_row(self, row: pd.Series) -> str:
        key = _s(row.get("공격key"))
        if key:
            return key
        return _join_team_key([row.get("공격조합1"), row.get("공격조합2"), row.get("공격조합3")])

    def _defense_disp_from_row(self, row: pd.Series) -> str:
        disp = _s(row.get("방어조합"))
        if disp:
            return disp
        return _join_team_disp([row.get("방어조합1"), row.get("방어조합2"), row.get("방어조합3")])

    def _attack_disp_from_row(self, row: pd.Series) -> str:
        disp = _s(row.get("공격조합"))
        if disp:
            return disp
        return _join_team_disp([row.get("공격조합1"), row.get("공격조합2"), row.get("공격조합3")])

    def get_defense_stats(self, defense_team_input: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self.df is None or self.df.empty:
            return results

        want_key = _join_team_key(defense_team_input)
        if len(_canon_team_key(defense_team_input)) != 3:
            return results

        bucket: Dict[str, Dict[str, Any]] = {}

        for _, row in self.df.iterrows():
            if _s(row.get("기준")) != "방어":
                continue
            if self._defense_key_from_row(row) != want_key:
                continue

            atk_key = self._attack_key_from_row(row)
            atk_disp = self._attack_disp_from_row(row)

            if atk_key not in bucket:
                bucket[atk_key] = {
                    "attack_key": atk_key,
                    "attack_disp": atk_disp,
                    "success": 0,
                    "fail": 0,
                    "total": 0,
                    "rate": 0.0,
                }

            bucket[atk_key]["total"] += 1
            if _result_is_attack_lose(row.get("승패여부")):
                bucket[atk_key]["success"] += 1
            elif _result_is_attack_win(row.get("승패여부")):
                bucket[atk_key]["fail"] += 1

        for item in bucket.values():
            if item["total"] < MIN_STAT_TRIES:
                continue
            item["rate"] = item["success"] / item["total"] if item["total"] > 0 else 0.0
            results.append(item)

        results.sort(key=lambda x: (x["total"], x["rate"], x["success"]), reverse=True)
        return results

    def get_attack_winrates(self, attack_team_input: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self.df is None or self.df.empty:
            return results
    
        want_key = _join_team_key(attack_team_input)
        if len(_canon_team_key(attack_team_input)) != 3:
            return results
    
        bucket: Dict[str, Dict[str, Any]] = {}
    
        for _, row in self.df.iterrows():
            if self._attack_key_from_row(row) != want_key:
                continue
    
            def_key = self._defense_key_from_row(row)
            def_disp = self._defense_disp_from_row(row)
    
            if def_key not in bucket:
                bucket[def_key] = {
                    "defense_key": def_key,
                    "defense_disp": def_disp,
                    "success": 0,
                    "fail": 0,
                    "total": 0,
                    "rate": 0.0,
                }
    
            bucket[def_key]["total"] += 1
    
            if _result_is_attack_win(row.get("승패여부")):
                bucket[def_key]["success"] += 1
            elif _result_is_attack_lose(row.get("승패여부")):
                bucket[def_key]["fail"] += 1
    
        for item in bucket.values():
            if item["total"] < MIN_STAT_TRIES:
                continue
            item["rate"] = item["success"] / item["total"]
            results.append(item)
    
        results.sort(key=lambda x: (x["total"], x["rate"], x["success"]), reverse=True)
        return results

    def get_attack_stats(self, defense_team_input: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self.df is None or self.df.empty:
            return results

        want_key = _join_team_key(defense_team_input)
        if len(_canon_team_key(defense_team_input)) != 3:
            return results

        bucket: Dict[str, Dict[str, Any]] = {}

        for _, row in self.df.iterrows():
            if _s(row.get("기준")) != "공격":
                continue
            if self._defense_key_from_row(row) != want_key:
                continue

            atk_key = self._attack_key_from_row(row)
            atk_disp = self._attack_disp_from_row(row)

            if atk_key not in bucket:
                bucket[atk_key] = {
                    "attack_key": atk_key,
                    "attack_disp": atk_disp,
                    "success": 0,
                    "fail": 0,
                    "total": 0,
                    "rate": 0.0,
                }

            bucket[atk_key]["total"] += 1
            if _result_is_attack_win(row.get("승패여부")):
                bucket[atk_key]["success"] += 1
            elif _result_is_attack_lose(row.get("승패여부")):
                bucket[atk_key]["fail"] += 1

        for item in bucket.values():
            if item["total"] < MIN_STAT_TRIES:
                continue
            item["rate"] = item["success"] / item["total"] if item["total"] > 0 else 0.0
            results.append(item)

        results.sort(key=lambda x: (x["rate"], x["total"], x["success"]), reverse=True)
        return results

    def get_overall_stats(self, defense_team_input: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self.df is None or self.df.empty:
            return results

        want_key = _join_team_key(defense_team_input)
        if len(_canon_team_key(defense_team_input)) != 3:
            return results

        bucket: Dict[str, Dict[str, Any]] = {}

        for _, row in self.df.iterrows():
            if self._defense_key_from_row(row) != want_key:
                continue

            atk_key = self._attack_key_from_row(row)
            atk_disp = self._attack_disp_from_row(row)

            if atk_key not in bucket:
                bucket[atk_key] = {
                    "attack_key": atk_key,
                    "attack_disp": atk_disp,
                    "success": 0,
                    "fail": 0,
                    "total": 0,
                    "rate": 0.0,
                }

            bucket[atk_key]["total"] += 1
            if _result_is_attack_win(row.get("승패여부")):
                bucket[atk_key]["success"] += 1
            elif _result_is_attack_lose(row.get("승패여부")):
                bucket[atk_key]["fail"] += 1

        for item in bucket.values():
            if item["total"] < MIN_STAT_TRIES:
                continue
            item["rate"] = item["success"] / item["total"] if item["total"] > 0 else 0.0
            results.append(item)

        results.sort(key=lambda x: (x["rate"], x["total"], x["success"]), reverse=True)
        return results
