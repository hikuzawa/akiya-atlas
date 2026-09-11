"""所有者向けページの図解「相続した空き家の選択肢」を SVG として生成する。

コード生成であり、写真や AI 画像ではない。

使い方: uv run python docs/design/tools/flow_svg.py
出力: templates/partials/flow_options_mobile.svg / flow_options_desktop.svg
文言を変えるときはこのファイルの OPTIONS を直して再生成する。
templates/owners.html の文章版（details）も合わせて直す。
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "templates" / "partials"

TITLE = "相続した空き家の 6 つの選択肢"
DESC = (
    "相続した空き家から、住む・使う、貸す、売る、自治体の空き家バンクに登録、"
    "解体して土地を活用、当面は管理する、の 6 つに枝分かれする図"
)
OPTIONS: list[tuple[str, str, str]] = [
    ("住む・使う", "通える距離に住んでいる", "傷みと権利関係を現地で確認"),
    ("貸す", "手放さず収入にしたい", "空き家バンクに賃貸で登録"),
    ("売る", "維持費・税の負担を止めたい", "査定を複数社で比べる"),
    ("自治体の空き家バンクに登録", "買い手・借り手を広く探す", "市町村の担当窓口に相談"),
    ("解体して土地を活用", "老朽化・倒壊のおそれ", "解体補助の有無を確認"),
    ("当面は管理する", "まだ決めかねている", "通風・除草と税の確認"),
]
GENERATED = 'data-generated="akiya-atlas.design"'


def _open(width: int, height: int, cls: str) -> list[str]:
    return [
        f'<svg class="{cls}" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-labelledby="fo-t fo-d" {GENERATED} xmlns="http://www.w3.org/2000/svg">',
        f'<title id="fo-t">{escape(TITLE)}</title><desc id="fo-d">{escape(DESC)}</desc>',
    ]


def _option_box(x: int, y: int, w: int, h: int, title: str, fit: str, step: str, fs: int) -> str:
    return (
        f'<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>'
        f'<text class="title" x="{x + 14}" y="{y + 30}" font-size="{fs + 4}">{escape(title)}</text>'
        f'<text class="body" x="{x + 14}" y="{y + 54}" font-size="{fs}">'
        f"向いている: {escape(fit)}</text>"
        f'<text class="body" x="{x + 14}" y="{y + 76}" font-size="{fs}">'
        f"最初の一歩: {escape(step)}</text>"
    )


def mobile() -> str:
    w, box_w, box_h, gap = 360, 288, 92, 14
    top = 86
    height = top + len(OPTIONS) * (box_h + gap) - gap + 16
    out = _open(w, height, "fo-mobile")
    out.append('<rect class="root" x="16" y="12" width="328" height="56" rx="8"/>')
    out.append(
        '<text class="root-text" x="180" y="47" font-size="19" text-anchor="middle">'
        "相続した空き家</text>"
    )
    last_mid = top + (len(OPTIONS) - 1) * (box_h + gap) + 30
    out.append(f'<path class="link" d="M40 68 V{last_mid}"/>')
    for i, (title, fit, step) in enumerate(OPTIONS):
        y = top + i * (box_h + gap)
        out.append(f'<path class="link" d="M40 {y + 30} H56"/>')
        out.append(f'<circle cx="40" cy="{y + 30}" r="5" fill="var(--green-700, #1F4E46)"/>')
        out.append(_option_box(56, y, box_w, box_h, title, fit, step, 13))
    out.append("</svg>")
    return "\n".join(out)


def desktop() -> str:
    w, box_w, box_h = 960, 292, 104
    cols = [30, 338, 646]
    rows = [120, 250]
    out = _open(w, 370, "fo-desktop")
    out.append('<rect class="root" x="380" y="16" width="200" height="60" rx="8"/>')
    out.append(
        '<text class="root-text" x="480" y="53" font-size="20" text-anchor="middle">'
        "相続した空き家</text>"
    )
    out.append('<path class="link" d="M480 76 V96"/>')
    out.append(f'<path class="link" d="M{cols[0] - 14} 96 H{cols[2] - 14}"/>')
    for c, x in enumerate(cols):
        drop = x - 14
        out.append(f'<path class="link" d="M{drop} 96 V{rows[1] + 52}"/>')
        for r, y in enumerate(rows):
            i = r * 3 + c
            title, fit, step = OPTIONS[i]
            out.append(f'<path class="link" d="M{drop} {y + 52} H{x}"/>')
            out.append(
                f'<circle cx="{drop}" cy="{y + 52}" r="5" fill="var(--green-700, #1F4E46)"/>'
            )
            out.append(_option_box(x, y, box_w, box_h, title, fit, step, 13))
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "flow_options_mobile.svg").write_text(mobile() + "\n", encoding="utf-8", newline="\n")
    (OUT / "flow_options_desktop.svg").write_text(desktop() + "\n", encoding="utf-8", newline="\n")
    for line in OPTIONS:
        assert len(line[1]) <= 13 and len(line[2]) <= 13, f"文言が長すぎる: {line}"
    print("wrote", OUT / "flow_options_mobile.svg", OUT / "flow_options_desktop.svg")


if __name__ == "__main__":
    main()
