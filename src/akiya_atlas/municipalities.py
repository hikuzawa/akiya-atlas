"""全国地方公共団体コード表の読み込みと、都道府県ごとの市町村名簿（ADR 0007, 条件1）。

コード表は全国分（data/reference/municipal_codes.csv）を読み、都道府県をパラメータで絞る。
列: code,prefecture,prefecture_kana,municipality,municipality_kana（総務省・政府標準利用規約2.0）。
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# JIS X 0401 都道府県コード（2桁）→ ローマ字スラッグ
PREFECTURE_SLUGS: dict[str, str] = {
    "01": "hokkaido",
    "02": "aomori",
    "03": "iwate",
    "04": "miyagi",
    "05": "akita",
    "06": "yamagata",
    "07": "fukushima",
    "08": "ibaraki",
    "09": "tochigi",
    "10": "gunma",
    "11": "saitama",
    "12": "chiba",
    "13": "tokyo",
    "14": "kanagawa",
    "15": "niigata",
    "16": "toyama",
    "17": "ishikawa",
    "18": "fukui",
    "19": "yamanashi",
    "20": "nagano",
    "21": "gifu",
    "22": "shizuoka",
    "23": "aichi",
    "24": "mie",
    "25": "shiga",
    "26": "kyoto",
    "27": "osaka",
    "28": "hyogo",
    "29": "nara",
    "30": "wakayama",
    "31": "tottori",
    "32": "shimane",
    "33": "okayama",
    "34": "hiroshima",
    "35": "yamaguchi",
    "36": "tokushima",
    "37": "kagawa",
    "38": "ehime",
    "39": "kochi",
    "40": "fukuoka",
    "41": "saga",
    "42": "nagasaki",
    "43": "kumamoto",
    "44": "oita",
    "45": "miyazaki",
    "46": "kagoshima",
    "47": "okinawa",
}
PREFECTURE_NAMES: dict[str, str] = {
    "01": "北海道",
    "02": "青森県",
    "03": "岩手県",
    "04": "宮城県",
    "05": "秋田県",
    "06": "山形県",
    "07": "福島県",
    "08": "茨城県",
    "09": "栃木県",
    "10": "群馬県",
    "11": "埼玉県",
    "12": "千葉県",
    "13": "東京都",
    "14": "神奈川県",
    "15": "新潟県",
    "16": "富山県",
    "17": "石川県",
    "18": "福井県",
    "19": "山梨県",
    "20": "長野県",
    "21": "岐阜県",
    "22": "静岡県",
    "23": "愛知県",
    "24": "三重県",
    "25": "滋賀県",
    "26": "京都府",
    "27": "大阪府",
    "28": "兵庫県",
    "29": "奈良県",
    "30": "和歌山県",
    "31": "鳥取県",
    "32": "島根県",
    "33": "岡山県",
    "34": "広島県",
    "35": "山口県",
    "36": "徳島県",
    "37": "香川県",
    "38": "愛媛県",
    "39": "高知県",
    "40": "福岡県",
    "41": "佐賀県",
    "42": "長崎県",
    "43": "熊本県",
    "44": "大分県",
    "45": "宮崎県",
    "46": "鹿児島県",
    "47": "沖縄県",
}
_NAME_TO_CODE = {name: code for code, name in PREFECTURE_NAMES.items()}
_SLUG_TO_CODE = {slug: code for code, slug in PREFECTURE_SLUGS.items()}

# 政令市の区（末尾が区で、市の一部）は市町村レベルとして扱わない
_WARD = re.compile(r".+市.+区$")


@dataclass(frozen=True)
class MunicipalityRef:
    """コード表の 1 行（市町村）。公式 URL や巡回設定はまだ持たない。"""

    code: str  # 6桁（検査数字込み）
    prefecture: str
    prefecture_slug: str
    name: str
    name_kana: str = ""

    @property
    def prefecture_code(self) -> str:
        return self.code[:2]

    @property
    def kind(self) -> str:
        if self.name.endswith("市"):
            return "市"
        if self.name.endswith("町"):
            return "町"
        if self.name.endswith("村"):
            return "村"
        if self.name.endswith("区"):
            return "区"
        return "他"

    @property
    def slug(self) -> str:
        return self.code  # 自動生成は 6 桁コードを既定スラッグにする（安定・一意）


def _resolve_pref_code(prefecture: str) -> str:
    """『長野県』『nagano』『20』のいずれからでも 2 桁コードを返す。"""
    p = prefecture.strip()
    if p in _NAME_TO_CODE:
        return _NAME_TO_CODE[p]
    if p.lower() in _SLUG_TO_CODE:
        return _SLUG_TO_CODE[p.lower()]
    if re.fullmatch(r"\d{2}", p):
        if p in PREFECTURE_NAMES:
            return p
    if re.fullmatch(r"\d{1}", p):
        z = p.zfill(2)
        if z in PREFECTURE_NAMES:
            return z
    # 『長野』のように県を略した名前
    for name, code in _NAME_TO_CODE.items():
        if name.rstrip("都道府県") == p.rstrip("都道府県"):
            return code
    raise ValueError(f"都道府県を特定できません: {prefecture!r}")


def default_code_table_path(root: Path) -> Path:
    return root / "data" / "reference" / "municipal_codes.csv"


def load_code_table(path: Path) -> list[MunicipalityRef]:
    """全国のコード表を読み、市町村（区・都道府県行を除く）を返す。"""
    out: list[MunicipalityRef] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            muni = (row.get("municipality") or "").strip()
            pref = (row.get("prefecture") or "").strip()
            if len(code) != 6 or not muni or not pref:
                continue  # 都道府県レベルの行・空行はスキップ
            if _WARD.match(muni):
                continue  # 政令市の区はスキップ
            pref_code = code[:2]
            out.append(
                MunicipalityRef(
                    code=code,
                    prefecture=pref,
                    prefecture_slug=PREFECTURE_SLUGS.get(pref_code, pref_code),
                    name=unicodedata.normalize("NFKC", muni),
                    name_kana=(row.get("municipality_kana") or "").strip(),
                )
            )
    return out


def municipalities_for(prefecture: str, table_path: Path) -> list[MunicipalityRef]:
    """都道府県（名・スラッグ・コードのいずれか）で市町村を絞って返す。"""
    code = _resolve_pref_code(prefecture)
    rows = [m for m in load_code_table(table_path) if m.prefecture_code == code]
    return sorted(rows, key=lambda m: m.code)
