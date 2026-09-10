"""カタカナ読み → ローマ字（ヘボン式）変換。lg.jp / 地理型ドメインの候補生成に使う（ADR 0007）。

長音の扱いに揺れがある（とうみ → tomi / toumi）ため、複数の候補を返す。
自治体名の読みから 市/町/村/区 の読み（シ/チョウ/マチ/ソン/ムラ/ク）を落とす。
"""

from __future__ import annotations

import re
import unicodedata

# 2 文字（拗音）を先に、次に 1 文字。ヘボン式。
_DIGRAPH = {
    "キャ": "kya",
    "キュ": "kyu",
    "キョ": "kyo",
    "シャ": "sha",
    "シュ": "shu",
    "ショ": "sho",
    "チャ": "cha",
    "チュ": "chu",
    "チョ": "cho",
    "ニャ": "nya",
    "ニュ": "nyu",
    "ニョ": "nyo",
    "ヒャ": "hya",
    "ヒュ": "hyu",
    "ヒョ": "hyo",
    "ミャ": "mya",
    "ミュ": "myu",
    "ミョ": "myo",
    "リャ": "rya",
    "リュ": "ryu",
    "リョ": "ryo",
    "ギャ": "gya",
    "ギュ": "gyu",
    "ギョ": "gyo",
    "ジャ": "ja",
    "ジュ": "ju",
    "ジョ": "jo",
    "ビャ": "bya",
    "ビュ": "byu",
    "ビョ": "byo",
    "ピャ": "pya",
    "ピュ": "pyu",
    "ピョ": "pyo",
}
_MONO = {
    "ア": "a",
    "イ": "i",
    "ウ": "u",
    "エ": "e",
    "オ": "o",
    "カ": "ka",
    "キ": "ki",
    "ク": "ku",
    "ケ": "ke",
    "コ": "ko",
    "サ": "sa",
    "シ": "shi",
    "ス": "su",
    "セ": "se",
    "ソ": "so",
    "タ": "ta",
    "チ": "chi",
    "ツ": "tsu",
    "テ": "te",
    "ト": "to",
    "ナ": "na",
    "ニ": "ni",
    "ヌ": "nu",
    "ネ": "ne",
    "ノ": "no",
    "ハ": "ha",
    "ヒ": "hi",
    "フ": "fu",
    "ヘ": "he",
    "ホ": "ho",
    "マ": "ma",
    "ミ": "mi",
    "ム": "mu",
    "メ": "me",
    "モ": "mo",
    "ヤ": "ya",
    "ユ": "yu",
    "ヨ": "yo",
    "ラ": "ra",
    "リ": "ri",
    "ル": "ru",
    "レ": "re",
    "ロ": "ro",
    "ワ": "wa",
    "ヰ": "i",
    "ヱ": "e",
    "ヲ": "o",
    "ン": "n",
    "ガ": "ga",
    "ギ": "gi",
    "グ": "gu",
    "ゲ": "ge",
    "ゴ": "go",
    "ザ": "za",
    "ジ": "ji",
    "ズ": "zu",
    "ゼ": "ze",
    "ゾ": "zo",
    "ダ": "da",
    "ヂ": "ji",
    "ヅ": "zu",
    "デ": "de",
    "ド": "do",
    "バ": "ba",
    "ビ": "bi",
    "ブ": "bu",
    "ベ": "be",
    "ボ": "bo",
    "パ": "pa",
    "ピ": "pi",
    "プ": "pu",
    "ペ": "pe",
    "ポ": "po",
    "ァ": "a",
    "ィ": "i",
    "ゥ": "u",
    "ェ": "e",
    "ォ": "o",
    "ー": "-",
}
_SUFFIX_READING = re.compile(r"(シ|チョウ|マチ|ソン|ムラ|ク|グン)$")


def _to_katakana(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:  # ひらがな → カタカナ
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _romanize_core(kana: str) -> str:
    kana = _to_katakana(kana)
    out: list[str] = []
    i = 0
    while i < len(kana):
        two = kana[i : i + 2]
        if two in _DIGRAPH:
            out.append(_DIGRAPH[two])
            i += 2
            continue
        ch = kana[i]
        if ch == "ッ":  # 促音: 次の子音を重ねる
            nxt = kana[i + 1 : i + 2]
            r = _DIGRAPH.get(kana[i + 1 : i + 3]) or _MONO.get(nxt, "")
            if r:
                out.append(r[0])
            i += 1
            continue
        out.append(_MONO.get(ch, ""))
        i += 1
    return "".join(out)


def romaji_variants(name_kana: str) -> list[str]:
    """自治体名の読みから、ドメインに使われうるローマ字候補を返す（長音の揺れを含む）。"""
    kana = _SUFFIX_READING.sub("", _to_katakana(name_kana.strip()))
    base = _romanize_core(kana)
    if not base:
        return []
    variants: list[str] = []

    def add(v: str) -> None:
        v = re.sub(r"[^a-z]", "", v.lower())
        if v and v not in variants:
            variants.append(v)

    # 「-」は長音。落とす版と、直前母音を重ねる版、oo→ou/o の版を作る
    add(base.replace("-", ""))  # 長音を無視: toumi←トウミ? いや ou はそのまま
    # 「ー」由来の長音を母音重複や省略に
    add(re.sub(r"([aeiou])-", r"\1\1", base))  # 母音+長音 → 母音重複
    add(re.sub(r"-", "", base))
    # ou/oo → o, uu → u（長音の省略形）
    collapsed = base.replace("-", "")
    collapsed = re.sub(r"ou", "o", collapsed)
    collapsed = re.sub(r"oo", "o", collapsed)
    collapsed = re.sub(r"uu", "u", collapsed)
    add(collapsed)
    # oh 表記（大→oh）
    add(collapsed.replace("o", "oh", 1) if collapsed.startswith("o") else collapsed)
    return variants
