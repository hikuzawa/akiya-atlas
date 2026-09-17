"""補助制度の募集期間の引用から、締切を読む（quote-then-parse、ADR 0004）。

締切は「募集終了」の印になる。**受付中の制度に「募集終了」と出すと、利用者は申請を諦める。**
読み違えるくらいなら値にしない（画面には引用の原文がそのまま出る）。

2026-09-17 まで、引用の中の「年の書かれた日付」の最も遅いものを締切にしていた。すると
「令和8年4月13日(月曜日)から10月30日(金曜日)」は終わりの日に年が無いので読まれず、始まりの
4 月 13 日が締切になった。「令和8年4月1日から受付開始」も同じ。「募集終了」253 件の半数以上が
この誤りだった。そこで次のように読む。

- 締切にするのは、終わりだと書いてある日付だけ。「まで」「必着」「で終了」が続く、「期限:」が
  前に付く、「から」「~」の直後にある（期間の終わり）。「令和8年7月10日(金曜日) 午前9時」の
  ような印の無い日付は、始まりか締切か分からないので締切にしない
- 「から」「より」「以降」「受付開始」が続く日付は始まりの日で、締切にしない
- 年の無い日付は、期間の終わり（「令和8年4月13日から10月30日」）のときだけ始まりの年を引き継ぐ
  （前より早ければ翌年）。それ以外は読まない（「今年」で補わない。「令和3年4月1日以降…
  各年度の3月末日まで」の 3 月末日は令和 4 年ではない）
- 曜日が添えてあれば年と照合し、1 つでも食い違えば締切にしない（原文の誤記を値にしない）
- 締切より後に始まる期間（「〜6月30日まで、7月1日から予算に達するまで」）や、延長・随時募集への
  切り替えが書いてあれば、いまも受け付けている可能性があるので締切にしない
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from sitemill.parse.jp import normalize_text

_ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925, "R": 2018, "H": 1988, "S": 1925}
_WEEKDAYS = "月火水木金土日"
_DASH = "~〜\\-‐－ー―–—"

# 年: 「令和8年」「R8.」「令和9(2027)年」「2026年」「2026/」「2026年(令和8年)」
_YEAR = (
    r"(?:(?P<era>令和|平成|昭和|(?<![A-Za-z])[RHS])\s*(?P<eray>元|\d{1,2})\s*"
    r"(?:\(\s*\d{4}\s*\)\s*)?(?:年|\.)"
    r"|(?P<wy>(?:19|20)\d{2})\s*(?:年|/|\.))"
    r"\s*(?:\([^)]{1,8}\)\s*)?"
)
# 年のある日付は「/」「.」区切りも読む。年の無い日付は「月」「日」の形だけ
# （「補助率1/2」を日付にしない）
_DATE = re.compile(
    rf"(?:{_YEAR}(?P<ym>\d{{1,2}})\s*[月/.]\s*(?P<yd>\d{{1,2}}\s*日?|末日?)"
    r"|(?<![\d年./])(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2}\s*日|末日?)"
    # 期間の終わりの省略形。「令和8年4月21日~9年1月29日」は元号を、「9月7日(月)~18日(金)」は
    # 年と月を始まりから引き継ぐ
    rf"|(?:(?<=[{_DASH}])|(?<=から))\s*(?:(?P<sy>\d{{1,2}})\s*年\s*(?P<sym>\d{{1,2}})\s*月\s*"
    r"(?P<syd>\d{1,2}\s*日|末日?)|(?P<donly>\d{1,2})\s*日))"
    r"(?:\s*\(\s*(?P<wd1>[月火水木金土日])(?:曜日?)?\s*\)"
    r"|\s*(?P<wd2>[月火水木金土日])曜日?"
    r"|\s*\([^)]{0,12}\))?"
)
# 日付のあとに入る時刻（「午前8時30分」「8:30」「(17時15分)」）
_CLOCK = r"(?:午前|午後)?\s*\d{1,2}\s*(?:時(?:\s*\d{1,2}\s*分)?|:\d{2})"
_TIME = rf"(?:\s*\(?\s*{_CLOCK}\s*\)?)?"
_START = re.compile(
    _TIME
    # 「11月30日(月曜日)午前9時~午後4時30分」の「~」は時間帯で、期間の始まりではない
    + rf"\s*(?:から|より|以降|以後|[{_DASH}](?!\s*{_CLOCK})"
    + r"|に?(?:申請)?(?:受付|受け付け)?を?(?:開始|再開))"
)
_END_AFTER = re.compile(
    _TIME
    + r"\s*(?:必着|消印有効|をもって|をもち"
    # 「令和8年12月18日(金曜)または予算の上限に達するまで」
    + r"|(?:(?!から|より)[^。、(]){0,15}?(?:まで|迄)"
    # 「令和8年6月25日木曜日に予算額に達したため、受付を終了」。「達し次第終了」は締切ではない
    + r"|(?:(?!次第)[^。(]){0,25}?(?:終了|締め?切))"
)
_END_BEFORE = re.compile(
    rf"(?:期限日?|締切日?|締め切り日?|〆切日?|最終日|終了)\s*[:は]?\s*$|[{_DASH}]\s*$"
)
# 締切のあとで受付が続いていると書いてある
_STILL_OPEN = re.compile(r"延長|随時(?:募集|受付)と(?:なり|な)")


def _year_of(m: re.Match[str]) -> int | None:
    if m.group("wy"):
        return int(m.group("wy"))
    if m.group("era"):
        n = 1 if m.group("eray") == "元" else int(m.group("eray"))
        return _ERA_BASE[m.group("era")] + n
    return None


def _build(year: int, month: int, day: str) -> date | None:
    try:
        if day.startswith("末"):
            return date(year, month, calendar.monthrange(year, month)[1])
        return date(year, month, int(day.rstrip("日").strip()))
    except ValueError:
        return None


def parse_period_end(quote: str) -> tuple[date | None, str | None]:
    """募集期間の引用から締切を返す。返り値は (締切, 注記)。

    終わりだと書いてある日付のうち最も遅いものを締切とみなす。
    「予算がなくなり次第終了」「随時受付」のように日付が無い書き方は値にしない。
    """
    t = normalize_text(quote or "")
    starts: list[date] = []
    ends: list[date] = []
    open_start: tuple[date, int] | None = None  # 直前の始まりの日と、その印の終わりの位置
    for m in _DATE.finditer(t):
        # 期間の終わり: 始まりの印（「から」「~」）の直後にある
        begin = open_start[0] if open_start and not t[open_start[1] : m.start()].strip() else None
        open_start = None
        written = _year_of(m)
        if written is not None:
            d = _build(written, int(m.group("ym")), m.group("yd"))
        elif begin is None:
            continue  # 年の手がかりが無い
        elif m.group("donly"):
            d = _build(begin.year, begin.month, m.group("donly"))
        elif m.group("sy"):
            base = next(b for b in (2018, 1988, 1925) if begin.year > b)  # 始まりの元号
            d = _build(base + int(m.group("sy")), int(m.group("sym")), m.group("syd"))
        else:
            d = _build(begin.year, int(m.group("m")), m.group("d"))
            if d is not None and d < begin:
                d = _build(begin.year + 1, int(m.group("m")), m.group("d"))
        if d is None:
            continue
        wd = m.group("wd1") or m.group("wd2")
        if wd is not None and _WEEKDAYS[d.weekday()] != wd:
            return None, "weekday_mismatch"
        start = _START.match(t, m.end())
        if start is not None:
            starts.append(d)
            open_start = (d, start.end())
        elif begin or _END_AFTER.match(t, m.end()) or _END_BEFORE.search(t[: m.start()]):
            ends.append(d)
    if not ends:
        return None, "start_only" if starts else "no_date"
    deadline = max(ends)
    if starts and max(starts) > deadline:
        return None, "reopens_after_deadline"
    if _STILL_OPEN.search(t):
        return None, "still_open"
    return deadline, None
