from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import pandas as pd


MIN_STAT_TRIES = 3
_GS_PREFIX = "https://docs.google.com/spreadsheets/d/"


def _s(val: Any) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


def _is_yes(val: Any) -> bool:
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


def _join_team_key(names: Sequence[Any]) -> str:
    return "".join(_canon_team_key(names))


def _join_team_disp(names: Sequence[Any]) -> str:
    return ", ".join(_canon_team_key(names))


def _split_csv_args(s: str) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in re.split(r"[,\uFF0C\u3001]", s) if x.strip()]


def _format_blockquote(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(["> " + ln if ln else ">" for ln in text.split("\n")])


def _badge_for_item(item: dict, i: int) -> str:
    if item.get("recommend"):
        return "⭐ "
    return ""


def _result_is_attack_win(val: Any) -> bool:
    return _s(val) == "승"


def _result_is_attack_lose(val: Any) -> bool:
    return _s(val) == "패"


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
