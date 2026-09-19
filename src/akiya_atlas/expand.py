"""都道府県パラメータでの自動発見（ADR 0007, 条件1〜5）。

各市町村について: 公式 URL を解決 → 空き家バンクページを発見・分類 → 運営主体の根拠を記録 →
確信度で policy（crawl / link_only / pending）を決める。高確信は自動採用、低確信はレビュー行列へ。
"""

from __future__ import annotations

import json
import logging
import re
import socket
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from sitemill.build.pii import PHONE_RE
from sitemill.classify import (
    ClassifiedPage,
    ListingScore,
    PageClass,
    PlatformRegistry,
    classify_page,
    listing_score,
)
from sitemill.clock import jst_now, jst_today
from sitemill.diff.normalize import page_text
from sitemill.fetch.client import FetchResult, PoliteClient
from sitemill.fetch.links import extract_links, host_of
from sitemill.models import (
    CrawlPolicy,
    ExternalLink,
    FollowRule,
    OperatorEvidence,
    OperatorKind,
    PageKind,
    SeedPage,
    Source,
)
from sitemill.review import ReviewCandidate, ReviewQueue
from sitemill.settings import Workspace
from sitemill.store.jsonio import write_json

from akiya_atlas.municipalities import (
    MunicipalityRef,
    default_code_table_path,
    municipalities_for,
)
from akiya_atlas.official_domains import OfficialHost, candidate_official_urls, classify_host

log = logging.getLogger(__name__)

_BANK_ANCHOR = re.compile(r"空き?家|あき家|移住|定住")
CONFIDENCE_ADOPT = 0.7  # これ以上なら自動採用


@dataclass
class MunicipalityFinding:
    muni: MunicipalityRef
    official: OfficialHost | None = None
    official_url: str | None = None
    bank_url: str | None = None
    classified: ClassifiedPage | None = None
    operator_kind: OperatorKind = OperatorKind.unknown
    evidence_quote: str | None = None
    evidence_url: str | None = None
    cross_linked: bool = False
    external_links: list[ExternalLink] = field(default_factory=list)
    policy: CrawlPolicy | str = "pending"
    confidence: float = 0.0
    reason: str = ""
    proposed_action: str = "承認"
    # 一覧らしさ（選択器の根拠）とページネーション、選ばれなかった候補（自己修復の材料）
    listing_score: float | None = None
    listing_rows: int | None = None
    pagination_pattern: str | None = None
    detail_pattern: str | None = None  # 一覧から詳細ページへ辿る URL の形
    alternatives: list[str] = field(default_factory=list)
    # 補助制度のページ（空き家バンクのページから辿ったもの）。kind=subsidy として巡回する
    subsidy_urls: list[str] = field(default_factory=list)
    # 一覧は確認できているのに 1 件も取り込めていない（抽出側の課題。ページの文面を分ける）
    extract_gap: bool = False


def _name_key(text: str) -> str:
    """市町村名の照合用。全角半角と「ヶ・ケ」「ヵ・カ」の書き分け、空白を吸収する。"""
    t = unicodedata.normalize("NFKC", text).replace("ヶ", "ケ").replace("ヵ", "カ")
    return re.sub(r"\s+", "", t)


def page_names_municipality(text: str | None, muni: MunicipalityRef) -> bool:
    """そのページにこの市町村の名前が出てくるか。

    読みから推測した URL は、同じ読みの別の自治体に当たることがある。2026-09-17 に 7 件、
    `www.city.konan.lg.jp`（愛知県江南市）を滋賀県湖南市と高知県香南市の公式サイトとして
    採用していた。当たったページには湖南市・香南市の名前が 1 回も出ていなかった
    （docs/data-issues.md）。名前が出ないサイトは、その市町村のものとして使わない。
    """
    return bool(text) and _name_key(muni.name) in _name_key(text or "")


def resolve_official_url(
    muni: MunicipalityRef, client: PoliteClient, *, max_probes: int = 16
) -> tuple[str, OfficialHost] | None:
    """候補 URL を順に叩き、到達できて、その市町村の名前が出てくる公式ドメインを返す。"""
    for url in candidate_official_urls(muni)[:max_probes]:
        res = client.get(url, check_robots=True)
        if res.ok or res.not_modified:
            oh = classify_host(host_of(res.final_url), muni.prefecture_slug)
            if not oh.is_official:
                continue
            if not page_names_municipality(res.text, muni):
                log.info(
                    "%s %s: 推測した %s に市町村名が出ないので使わない（同じ読みの別の自治体か）",
                    muni.code,
                    muni.name,
                    oh.host,
                )
                continue
            return res.final_url, oh
    return None


@dataclass(frozen=True)
class OfficialResolution:
    url: str
    host: OfficialHost
    evidence_quote: str
    evidence_url: str


@dataclass
class OfficialOverrides:
    """県の公的な市町村一覧から得た code→公式URL の対応（ADR 0007 条件A）。

    これに `data/reference/municipal_overrides.json`（自動では決められない市町村を根拠つきで
    固定したもの）を重ねる。同名の市町村（北海道の泊村）や、公式サイトが無い市町村
    （北方領土の 5 村）はここで決着させる。
    """

    by_code: dict[str, str] = field(default_factory=dict)
    source_url: str = ""
    source_name: str = ""
    fixed: dict[str, dict] = field(default_factory=dict)  # code -> 固定した公式URLと根拠
    no_website: dict[str, dict] = field(default_factory=dict)  # code -> サイトが無い根拠
    # code -> 物件一覧を巡回しない理由（PDF など）。補助制度のページは別に巡回する
    link_only: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, ws: Workspace, pref_slug: str) -> OfficialOverrides:
        from sitemill.store.jsonio import read_json

        path = ws.root / "data" / "reference" / f"{pref_slug}_official_urls.json"
        data = read_json(path) or {}
        by_code = {
            str(m["code"]): m["official_url"]
            for m in data.get("municipalities", [])
            if m.get("code") and m.get("official_url")
        }
        fixed: dict[str, dict] = {}
        no_website: dict[str, dict] = {}
        link_only: dict[str, dict] = {}
        notes = read_json(ws.root / "data" / "reference" / "municipal_overrides.json") or {}
        for row in notes.get("municipalities", []):
            code = str(row.get("code") or "")
            if not code:
                continue
            if row.get("no_website"):
                no_website[code] = row
            elif row.get("link_only"):
                link_only[code] = row
                if row.get("official_url"):
                    fixed[code] = row
            elif row.get("official_url"):
                fixed[code] = row
        return cls(
            by_code=by_code,
            source_url=data.get("source_url", ""),
            source_name=data.get("source_name", ""),
            fixed=fixed,
            no_website=no_website,
            link_only=link_only,
        )


def resolve_official(
    muni: MunicipalityRef, client: PoliteClient, overrides: OfficialOverrides
) -> OfficialResolution | None:
    """公式ドメインを解決する。固定値 → 候補URL → 県の市町村一覧の順。運営主体の根拠も返す。"""
    fixed = overrides.fixed.get(muni.code)
    if fixed:
        url = str(fixed["official_url"])
        host = classify_host(host_of(url), muni.prefecture_slug)
        if not host.is_official:
            host = OfficialHost(host_of(url), "verified_by_hand", True, "strong")
        quote = f"{fixed.get('evidence', '確定情報')}（{host.host}）"
        return OfficialResolution(url, host, quote, str(fixed.get("source") or url))
    probed = resolve_official_url(muni, client)
    if probed is not None:
        url, host = probed
        return OfficialResolution(url, host, host.evidence(), "https://" + host.host + "/")
    # 県の公的一覧に載っている URL を使う（ドメインが命名規則外でも一覧が運営主体を保証する）
    for override_url in _override_urls(overrides.by_code.get(muni.code)):
        res = client.get(override_url, check_robots=True)
        if res.ok or res.not_modified:
            host = classify_host(host_of(res.final_url), muni.prefecture_slug)
            if not host.is_official:
                host = OfficialHost(host_of(res.final_url), "prefecture_listed", True, "strong")
            quote = (
                f"{overrides.source_name or '県の市町村一覧'}に掲載された公式サイト"
                f"（{host_of(res.final_url)}）"
            )
            return OfficialResolution(
                res.final_url, host, quote, overrides.source_url or override_url
            )
        if site_is_up(res, override_url):
            # 取得できなくても（robots 拒否・403・古い TLS・応答なし）、県の公的一覧に
            # 載っている事実が運営主体の根拠になる（ADR 0007 条件A）。人手でも直せないので
            # 人間確認には回さず、リンクだけ出す。
            host = OfficialHost(host_of(override_url), "prefecture_listed", True, "strong")
            quote = (
                f"{overrides.source_name or '県の市町村一覧'}に掲載された公式サイト"
                f"（{host.host}）。サイトを取得できなかったためリンクのみ"
                f"（{res.error or res.status}）"
            )
            return OfficialResolution(
                override_url, host, quote, overrides.source_url or override_url
            )
    return None


def _override_urls(url: str | None) -> list[str]:
    """県の一覧の URL と、その入口（ホストのルート）。深いパスは移転していることがある。

    例: 兵庫県の一覧にある香美町 `.../www/index.html` は 404 で、ルートは生きている。
    """
    if not url:
        return []
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}/"
    return [url] if url.rstrip("/") == root.rstrip("/") else [url, root]


def site_is_up(res: FetchResult, url: str) -> bool:
    """取得に失敗した相手のサイトが「在る」かどうか。URL 自体が誤りなら False。"""
    if res.status == 404:
        return False  # 一覧の URL が古い。人が直せるので人間確認に回す
    if res.status:
        return True  # 403 など、サーバは応答している
    try:  # HTTP の応答が無い（robots 不達・TLS・タイムアウト）。名前解決できるかで判断する
        socket.getaddrinfo(host_of(url), None)
    except OSError:
        return False
    return True


@dataclass
class BankProbe:
    url: str | None = None
    classified: ClassifiedPage | None = None
    externals: list[ExternalLink] = field(default_factory=list)
    host_official: bool = False  # バンクページが公式ドメイン上にあるか
    cross_linked: bool = False  # 非公式ホストだが公式サイトから空き家バンクとして案内されているか
    listing: ListingScore | None = None  # 選んだページの一覧らしさ
    pagination_pattern: str | None = None  # 2 ページ目以降を辿る URL パターン
    alternatives: list[str] = field(default_factory=list)  # 選ばなかった候補 URL


_AKIYA_URL = re.compile(r"aki|akiya|空き?家|空家|akiyabank|akiya-bank", re.I)
_AKIYA_ANCHOR = re.compile(r"空き?家|空家バンク|あき家")
_INTERMEDIATE = re.compile(r"住ま|移住|定住|くらし|暮らし|生活|空き?家|不動産|土地")
# 案内ページから「物件一覧」へ 1 段辿るときのアンカー語と、一覧ではないことが明らかな語
_LIST_ANCHOR = re.compile(
    r"物件一覧|登録物件|物件情報|物件を探す|物件検索|物件の一覧|空き?家情報|一覧"
)
_NOT_LIST_ANCHOR = re.compile(
    r"補助|助成|様式|申請|要綱|ツアー|募集要項|チラシ|\.pdf|\.docx?|\.xlsx?", re.I
)
# 添付ファイル（申請書・チラシ）は HTML の一覧になり得ないので候補から外す
_DOC_URL = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip)(?:[?#].*)?$", re.I)
MAX_CANDIDATE_FETCH = 5  # 1 市町村あたり実際に取得して一覧らしさを見る候補数の上限
# 選んだページが一覧でないとき、少なくとも空き家バンクに触れていれば「制度案内（info）」とみなす
_BANK_MENTION = re.compile(
    r"空き?家バンク|空家バンク|空き?家情報|空き?家・空き?地|空き?家等?の(?:情報|登録)"
)


@dataclass
class _Cand:
    url: str
    text: str
    found_on: str
    score: int


def _collect_bank_candidates(
    official_url: str,
    official: OfficialHost,
    client: PoliteClient,
    *,
    platforms: PlatformRegistry | None = None,
    max_candidates: int = 6,
) -> list[_Cand]:
    """公式トップ→中間カテゴリ→空き家、および sitemap を辿って空き家バンク候補を集める（2 階層）。

    ここではリンクのアンカー語と URL だけで粗く点数を付け、上位を返す。どれを採用するかは
    `select_bank_page` が実際にページを取得して「一覧らしさ」で決める。
    """
    top = client.get(official_url)
    if not top.ok:
        return []
    cands: dict[str, _Cand] = {}
    seen_pages: set[str] = {official_url}

    def consider(url: str, text: str, found_on: str) -> None:
        if _DOC_URL.search(url):
            return
        if host_of(url) != official.host:
            # 公式ドメイン外は「空き家」のアンカーで案内されているか、既知の民間プラットフォーム
            # の場合だけ候補にする（URL に aki を含むだけの他機関ページを拾わない）
            on_platform = platforms is not None and platforms.is_platform(url)
            if not (on_platform or _AKIYA_ANCHOR.search(text)):
                return
        s = 0
        if _AKIYA_ANCHOR.search(text):
            s += 3
        if _AKIYA_URL.search(url):
            s += 2
        if s == 0:
            return
        if _NOT_LIST_ANCHOR.search(text) or _NOT_LIST_ANCHOR.search(url):
            s -= 2  # 補助金・様式・PDF は候補としては弱い
        if host_of(url) == official.host:
            s += 1
        if s <= 0:
            return
        cur = cands.get(url)
        if cur is None or s > cur.score:
            cands[url] = _Cand(url=url, text=text[:80], found_on=found_on, score=s)

    def strong() -> bool:
        return any(c.score >= 5 for c in cands.values())

    top_links = extract_links(top.text, top.final_url)
    for ln in top_links:
        consider(ln.url, ln.text, official_url)
    # 直リンクが弱ければ中間ページを 1 階層辿る
    if not strong():
        inter = [
            ln
            for ln in top_links
            if _INTERMEDIATE.search(ln.text) and host_of(ln.url) == official.host
        ][:5]
        for ln in inter:
            if ln.url in seen_pages:
                continue
            seen_pages.add(ln.url)
            page = client.get(ln.url)
            if not page.ok:
                continue
            for sub in extract_links(page.text, page.final_url):
                consider(sub.url, sub.text, ln.url)
            if strong():
                break
    # sitemap も直接見る
    if not strong():
        for sm in (
            official_url.rstrip("/") + "/sitemap.xml",
            official_url.rstrip("/") + "/sitemap_index.xml",
        ):
            res = client.get(sm)
            if not res.ok:
                continue
            for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", res.text, re.I)[:200]:
                if _AKIYA_URL.search(loc):
                    consider(loc, "", sm)
    ranked = sorted(cands.values(), key=lambda c: (-c.score, len(c.url)))
    return ranked[:max_candidates]


@dataclass
class _Tried:
    cand: _Cand
    url: str
    html: str
    classified: ClassifiedPage
    listing: ListingScore
    mentions_bank: bool = False  # 本文が空き家バンクに触れているか（制度案内の判定に使う）

    @property
    def is_listing_page(self) -> bool:
        return self.classified.page_class in (PageClass.listing_index, PageClass.listing_detail)


def _fetch_and_score(
    url: str, cand: _Cand, client: PoliteClient, platforms: PlatformRegistry
) -> _Tried | None:
    if _DOC_URL.search(url):
        return None
    res = client.get(url)
    if not res.ok or not res.text or res.encoding == "binary":
        return None
    ctype = res.headers.get("content-type", "")
    if ctype and not any(t in ctype for t in ("html", "xml", "text/")):
        return None  # 添付ファイル等。HTML の一覧ではない
    return _Tried(
        cand=cand,
        url=url,
        html=res.text,
        classified=classify_page(res.text, url, platforms=platforms),
        listing=listing_score(res.text, url),
        mentions_bank=bool(_BANK_MENTION.search(page_text(res.text))),
    )


def has_listing_evidence(score: ListingScore) -> bool:
    """行が並んでいるだけでなく、物件そのものの手がかりがあるか。

    制度案内・移住ポータル・第三者サービスの紹介にも、表と価格は出る。行数だけで一覧と決めると
    それらを掴む（枚方市の譲渡所得の特別控除、瀬戸内市の移住ポータル、岩泉町の 0 円マッチングの
    紹介、奥多摩町のメニューだけのページが実例）。物件番号があるか、物件価格が行数に見合う数だけ
    あって補助金の語に埋もれていないことを求める。
    """
    if score.listing_no_count >= 2:
        return True
    if score.subsidy_hits > score.property_prices:
        return False
    return score.property_prices >= 3 and score.property_prices >= score.rows


def _rank_key(t: _Tried) -> tuple[int, int, float, int, int, int]:
    """物件の手がかり > 一覧分類 > 一覧らしさ > 空き家バンクへの言及 > 1 ページ目 > アンカー語。

    同じ「一覧らしい」でも、物件番号や物件価格を伴うページを先に採る。
    """
    return (
        1 if t.is_listing_page and has_listing_evidence(t.listing) else 0,
        1 if t.is_listing_page else 0,
        t.listing.score if t.is_listing_page else 0.0,  # 非物件ページの微小な点数は比べない
        1 if t.mentions_bank else 0,
        1 if t.listing.pagination.current_page == 1 else 0,
        t.cand.score,
    )


def select_bank_page(
    cands: list[_Cand],
    official: OfficialHost,
    client: PoliteClient,
    platforms: PlatformRegistry,
    *,
    max_fetch: int = MAX_CANDIDATE_FETCH,
) -> BankProbe:
    """候補を実際に取得し、同一サイト内で最も一覧らしいページを選ぶ。

    - 2 ページ目以降を選んだら 1 ページ目に戻す（売却済みアーカイブを避ける）。
    - 制度案内ページしか無ければ、そこから「物件一覧」リンクを 1 段だけ辿る。
    """
    probe = BankProbe()
    if not cands:
        return probe
    tried: list[_Tried] = []
    for cand in cands[:max_fetch]:
        t = _fetch_and_score(cand.url, cand, client, platforms)
        if t is None:
            continue
        tried.append(t)
        pag = t.listing.pagination
        if (
            t.is_listing_page
            and t.listing.rows >= 3
            and has_listing_evidence(t.listing)
            and pag.current_page == 1
        ):
            break  # 物件の手がかりを伴う 1 ページ目が見つかった。これ以上は探さない
    if not tried:
        probe.url = cands[0].url
        return probe
    best = max(tried, key=_rank_key)

    pag = best.listing.pagination
    if pag.current_page > 1 and pag.first_page_url:
        first = _fetch_and_score(pag.first_page_url, best.cand, client, platforms)
        if first is not None and first.classified.page_class is PageClass.listing_index:
            tried.append(first)
            best = first

    if not best.is_listing_page:
        hops = [
            ln
            for ln in extract_links(best.html, best.url)
            if _LIST_ANCHOR.search(ln.text)
            and not _NOT_LIST_ANCHOR.search(ln.text)
            and host_of(ln.url) == host_of(best.url)
            and ln.url != best.url
        ][:2]
        for ln in hops:
            hop = _Cand(url=ln.url, text=ln.text[:80], found_on=best.url, score=best.cand.score)
            t = _fetch_and_score(ln.url, hop, client, platforms)
            if t is None:
                continue
            tried.append(t)
            if t.classified.page_class is PageClass.listing_index and t.listing.is_listing:
                best = t
                break

    if best.classified.page_class is PageClass.not_listing and not best.mentions_bank:
        # 一覧でも空き家バンクの案内でもない（例: 農業体験ツアー）→ バンクページは未特定
        probe.alternatives = [t.url for t in tried][:5]
        return probe
    probe.url = best.url
    probe.classified = best.classified
    probe.listing = best.listing
    if (
        best.listing.pagination.is_paginated
        and best.classified.page_class is PageClass.listing_index
    ):
        probe.pagination_pattern = verified_pagination(
            best.html, best.url, best.listing.pagination.follow_pattern, client
        )
    seen = {best.url}
    for u in [t.url for t in tried] + [c.url for c in cands]:
        if u not in seen:
            seen.add(u)
            probe.alternatives.append(u)
    probe.alternatives = probe.alternatives[:5]

    bank_host = host_of(best.url)
    pref_slug = _pref_slug(official)
    # 公式ドメイン上か（同一ホスト、または公式の地理型/lg.jp ドメイン）
    probe.host_official = (
        bank_host == official.host or classify_host(bank_host, pref_slug).is_official
    )
    # 非公式ホストなら、公式ページから空き家バンクとして案内されているか（相互リンク）
    if not probe.host_official:
        found_on_official = bool(best.cand.found_on) and _host_is(best.cand.found_on, official.host)
        probe.cross_linked = found_on_official and bool(_BANK_ANCHOR.search(best.cand.text or ""))
    # 外部プラットフォームへのリンクを収集
    for ln in extract_links(best.html, best.url):
        if platforms.is_platform(ln.url):
            label = clean_link_label(ln.text) or platforms.match(ln.url) or ""
            probe.externals.append(ExternalLink(label=label, url=ln.url))
    return probe


# 「補助」「助成」を含むリンクのうち、空き家に関わるものだけを補助制度のページとみなす。
# 子育て・就学などの補助は対象外（プロンプトでも弾くが、取りに行く前に減らす）
_SUBSIDY_ANCHOR = re.compile(r"補助|助成")
_SUBSIDY_TOPIC = re.compile(
    r"空き?家|空家|住宅|改修|修繕|リフォーム|耐震|解体|除却|移住|定住|取得|片付|家財"
)
_HOUSING_CATEGORY = re.compile(r"空き?家|空家|住まい|住宅|移住|定住|くらし|暮らし")


def find_subsidy_pages(
    starts: list[str], client: PoliteClient, *, limit: int = 4, max_fetch: int = 6
) -> list[tuple[str, str]]:
    """既に分かっているページから 1 段だけ辿って、補助制度のページへのリンクを拾う。

    サイト全体は探さない。空き家バンクのページと公式トップの 2 か所から辿る（1 自治体 2 回の取得）。
    ここで見つからない制度は拾えないので、充足率は実測して判断する。
    """
    out: dict[str, str] = {}
    seen: set[str] = set(starts)
    queue = [(u, 0) for u in starts if u]
    hops = 0
    while queue and len(out) < limit and hops < max_fetch:
        url, depth = queue.pop(0)
        res = client.get(url)
        hops += 1
        if not res.ok or not res.text:
            continue
        host = host_of(url)
        for link in extract_links(res.text, url):
            text = (link.text or "").strip()
            if not text or _DOC_URL.search(link.url) or host_of(link.url) != host:
                continue
            if link.url in seen:
                continue
            if _SUBSIDY_ANCHOR.search(text) and _SUBSIDY_TOPIC.search(text + " " + link.url):
                seen.add(link.url)
                out[link.url] = text
                if len(out) >= limit:
                    break
            elif depth == 0 and _HOUSING_CATEGORY.search(text):
                # 「住まい」「空き家」などの分類ページ。ここから補助制度に届くことが多い
                seen.add(link.url)
                queue.append((link.url, 1))
    return list(out.items())


def find_bank_page(
    official_url: str, official: OfficialHost, client: PoliteClient, platforms: PlatformRegistry
) -> BankProbe:
    """公式サイトから空き家バンクページを見つけ分類する。公式ドメイン上か/相互リンクかも判定する。"""
    cands = _collect_bank_candidates(official_url, official, client, platforms=platforms)
    return select_bank_page(cands, official, client, platforms)


def _pref_slug(official: OfficialHost) -> str:
    # 地理型ドメイン city.<name>.<pref>.jp / <pref>.lg.jp から都道府県スラッグを取り出す
    parts = official.host.split(".")
    if official.host.endswith(".lg.jp") and len(parts) >= 3:
        return parts[-3]
    if len(parts) >= 3:
        return parts[-2]
    return ""


def _host_is(url: str, host: str) -> bool:
    return host_of(url) == host


def decide(
    muni: MunicipalityRef,
    official: OfficialHost | None,
    classified: ClassifiedPage | None,
    *,
    cross_linked: bool,
    bank_host_official: bool,
) -> MunicipalityFinding:
    """分類・公式ドメイン・相互リンクから policy と確信度を決める（純関数、テスト対象）。

    原則（ADR 0007）: 運営主体が確認できたら（公式ドメイン解決）自動採用する。人間に回すのは
    公式URLを解決できず運営主体を判定できないものだけ（pending）。
    """
    f = MunicipalityFinding(muni=muni, official=official, classified=classified)
    if official is None:
        # 運営主体を判定できない。ここだけが人間レビュー対象
        f.policy = "pending"
        f.confidence = 0.2
        f.reason = "公式 URL を自動解決できなかった（運営主体を判定できない）"
        f.proposed_action = "URL修正"
        return f

    # 公式ドメインが解決できた = 運営主体は自治体（確認済み）
    f.official_url = official.host
    f.operator_kind = OperatorKind.municipality
    f.evidence_quote = official.evidence()
    f.evidence_url = "https://" + official.host + "/"
    official_link = f.evidence_url

    if classified is None:
        # バンクページを特定できないが、運営主体（自治体）は確認済み → 公式へのリンクのみ
        f.policy = "link_only"
        f.bank_url = official_link
        f.confidence = 0.6
        f.reason = "公式サイトは確認できたが物件ページを特定できず。公式へのリンクのみ"
        f.proposed_action = "承認"
        return f

    f.bank_url = classified.url
    pc = classified.page_class
    if cross_linked:
        f.evidence_quote = (
            f"公式サイト（{official.host}）から空き家バンクとして案内されているリンク"
        )

    if pc in (PageClass.listing_index, PageClass.listing_detail):
        if bank_host_official or cross_linked:
            f.policy = "crawl"
            f.confidence = min(0.95, 0.6 + classified.confidence * 0.35)
            f.reason = "自治体ドメイン/相互リンクで運営主体を確認、静的な物件ページ"
        else:
            # 物件一覧だが非公式ホストで相互リンクも無い → 巡回せず公式へリンク
            f.policy = "link_only"
            f.bank_url = official_link
            f.confidence = 0.55
            f.reason = "物件ページが公式ドメイン外で相互リンクも確認できず。公式へのリンクのみ"
    elif pc is PageClass.third_party:
        f.policy = "link_only"
        f.confidence = max(0.7, classified.confidence)
        f.reason = "掲載は民間プラットフォーム。巡回せずリンクのみ"
    elif not (bank_host_official or cross_linked):
        # 公式ドメイン外で相互リンクも無い非物件ページは根拠にしない → 公式へのリンクのみ
        f.policy = "link_only"
        f.bank_url = official_link
        f.classified = None
        f.confidence = 0.6
        f.reason = (
            "公式サイトで物件ページを特定できず（公式外のページは根拠にしない）。公式へのリンクのみ"
        )
    elif pc is PageClass.spa:
        f.policy = "link_only"
        f.confidence = max(0.7, classified.confidence)
        f.reason = "JavaScript 描画のため静的 HTML に物件が無い。リンクのみ"
    else:  # not_listing = 制度案内ページ（物件は登録制/別ページ）
        f.policy = "link_only"
        f.confidence = 0.6
        f.reason = "空き家バンクの制度案内ページ（物件は登録制など）。リンクのみ"
    f.proposed_action = "承認"
    return f


def finding_to_source(f: MunicipalityFinding, existing_ids: set[str]) -> Source | None:
    """自動採用（crawl）の finding を Source にする。link_only/pending は None。"""
    if f.policy != "crawl" or f.bank_url is None:
        return None
    sid = f"{f.muni.prefecture_slug}-{f.muni.code}"
    if sid in existing_ids:
        return None
    kind = PageKind.listing_index
    if f.classified and f.classified.page_class is PageClass.listing_detail:
        kind = PageKind.listing_detail
    return Source(
        id=sid,
        name=f"{f.muni.name}空き家バンク",
        operator=f.muni.name,
        operator_kind=f.operator_kind,
        operator_evidence=OperatorEvidence(
            quote=f.evidence_quote or "", url=f.evidence_url or f.bank_url, checked_on=date.today()
        ),
        policy=CrawlPolicy.crawl,
        official_url=f.official_url or f.bank_url,
        pages=[SeedPage(url=f.bank_url, kind=kind, follow=_follow_rules(f))],
        allow_hosts=[host_of(f.bank_url)],
        max_pages=30,
        external_links=f.external_links,
    )


def finding_to_candidate(f: MunicipalityFinding) -> ReviewCandidate:
    label = f"{f.muni.prefecture} {f.muni.name}"
    class_label = f.classified.label if f.classified else None
    return ReviewCandidate(
        key=f.muni.code,
        label=label,
        url=f.bank_url or f.official_url,
        page_class=f.classified.page_class.value if f.classified else None,
        class_label=class_label,
        confidence=round(f.confidence, 2),
        operator_kind=f.operator_kind.value,
        evidence_quote=f.evidence_quote,
        evidence_url=f.evidence_url,
        reason=f.reason,
        proposed_policy=str(f.policy),
        proposed_action=f.proposed_action,
    )


def assess_municipality(
    muni: MunicipalityRef,
    client: PoliteClient,
    platforms: PlatformRegistry,
    overrides: OfficialOverrides | None = None,
) -> MunicipalityFinding:
    """1 市町村を評価して finding を返す（ネットワークを使う）。"""
    ov = overrides or OfficialOverrides()
    note = ov.no_website.get(muni.code)
    if note:
        # 公式サイトが無いと分かっている（北方領土の 5 村）。人間が確認しても変わらないので
        # 人間確認には回さず、サイトにも載せない
        f = MunicipalityFinding(muni=muni, policy="excluded", confidence=1.0)
        f.reason = f"公式サイトが無い: {note.get('evidence', '')}"
        f.evidence_url = note.get("source")
        f.proposed_action = "対象外"
        return f
    resolved = resolve_official(muni, client, ov)
    if resolved is None:
        return decide(muni, None, None, cross_linked=False, bank_host_official=False)
    note = ov.link_only.get(muni.code)
    if note:
        # 一覧が PDF だけ、のように物件一覧を巡回しないと決めた自治体。物件は公式へのリンクだけ
        # 出す。補助制度のページはこの判定と別に探す（collect_subsidy_pages、ADR 0013）
        f = decide(muni, resolved.host, None, cross_linked=False, bank_host_official=False)
        f.official_url = resolved.host.host
        f.evidence_quote = resolved.evidence_quote
        f.evidence_url = resolved.evidence_url
        f.reason = str(note.get("evidence") or "物件一覧は巡回しない（確定情報）")
        f.bank_url = str(note.get("bank_url") or resolved.url)
        return f
    probe = find_bank_page(resolved.url, resolved.host, client, platforms)
    f = decide(
        muni,
        resolved.host,
        probe.classified,
        cross_linked=probe.cross_linked,
        bank_host_official=probe.host_official,
    )
    f.official_url = resolved.host.host
    # 運営主体の根拠は解決元から（相互リンクで確認できた場合は decide 側の根拠を優先）
    if not probe.cross_linked:
        f.evidence_quote = resolved.evidence_quote
        f.evidence_url = resolved.evidence_url
    _attach_probe(f, probe)
    return f


def _attach_probe(f: MunicipalityFinding, probe: BankProbe) -> None:
    """選択器の結果（外部リンク・一覧らしさ・ページネーション・他候補）を finding に写す。"""
    f.external_links = probe.externals
    f.cross_linked = probe.cross_linked
    f.alternatives = list(probe.alternatives)
    if probe.listing is not None:
        f.listing_score = probe.listing.score
        f.listing_rows = probe.listing.rows
    f.pagination_pattern = probe.pagination_pattern if f.policy == "crawl" else None


_DETAIL_MIN_LINKS = 3  # 同じ形のリンクがこれだけあれば詳細ページの候補とみなす
_DETAIL_SAMPLES = 2  # 抜き取りで実際に開いて確かめる本数
_DIGITS = re.compile(r"\d+")
_MARK = "\x00"


def _url_shape(path: str) -> str:
    """数字を目印に置き換えた URL の形（例: /akiya/to853.html → /akiya/to<印>.html）。"""
    return _DIGITS.sub(_MARK, path)


def _shape_pattern(netloc: str, shape: str) -> str:
    """形から巡回用の正規表現を作る。スキームは問わない。"""
    body = "".join(re.escape(part) for part in shape.split(_MARK))
    body = r"\d+".join(re.escape(part) for part in shape.split(_MARK))
    return rf"^https?://{re.escape(netloc)}{body}$"


def detect_detail_follow(
    index_url: str, html: str, client: PoliteClient, *, max_shapes: int = 3
) -> tuple[str, int] | None:
    """一覧ページから詳細ページへ辿る規則を見つける。(正規表現, 本数) を返す。

    同じホスト・同じ形の URL が 3 本以上あり、抜き取りで開いたページに物件らしい数値
    （価格と、面積・築年・間取りのどれか）があれば、その形を詳細ページとみなす。
    案内やカテゴリのページを掴まないよう、必ず実際に開いて確かめる。
    """
    base = urlsplit(index_url)
    base_path = base.path.rstrip("/")
    shapes: dict[str, list[str]] = {}
    for ln in extract_links(html, index_url):
        parts = urlsplit(ln.url)
        if parts.netloc != base.netloc or _DOC_URL.search(parts.path):
            continue
        if parts.path.rstrip("/") == base_path or not _DIGITS.search(parts.path):
            continue
        shapes.setdefault(_url_shape(parts.path), []).append(ln.url)
    ranked = sorted(shapes.items(), key=lambda kv: -len(kv[1]))[:max_shapes]
    for shape, urls in ranked:
        if len(urls) < _DETAIL_MIN_LINKS:
            continue
        ok = 0
        for url in urls[:_DETAIL_SAMPLES]:
            res = client.get(url)
            if not res.ok:
                continue
            ls = listing_score(res.text, res.final_url)
            detailed = ls.property_prices >= 1 and (ls.area_count >= 1 or ls.rows >= 1)
            if detailed and not ls.subsidy_dominant:
                ok += 1
        if ok >= _DETAIL_SAMPLES or (ok >= 1 and len(urls) >= 6):
            return _shape_pattern(base.netloc, shape), len(urls)
    return None


def _detail_follow(f: MunicipalityFinding) -> list[FollowRule]:
    if not f.detail_pattern:
        return []
    return [FollowRule(pattern=f.detail_pattern, kind=PageKind.listing_detail, max_links=60)]


def verified_pagination(
    html: str, url: str, pattern: str | None, client: PoliteClient
) -> str | None:
    """ページ送りの辿り方を、2 ページ目が実在するときだけ採る。

    一覧の「次のページ」リンクが、存在しないページを指していることがある。千葉県睦沢町は一覧自身の
    `rel="next"` が 404 を返す /akiya/page/2 を指していて、物件は 1 ページ目にすべて載っているのに、
    2026-09-11 から毎日 404 を取りに行っていた（相手サイトへの無駄なアクセス）。

    形に合うリンクのうち最初の 1 件だけを取得する。404 / 410 なら辿り方を作らない。
    それ以外の失敗（タイムアウトなど）は一時的かもしれないので、辿り方を残す。
    """
    if not pattern:
        return None
    rx = re.compile(pattern)
    targets = sorted({ln.url for ln in extract_links(html, url) if rx.search(ln.url)} - {url})
    if not targets:
        return pattern
    res = client.get(targets[0], check_robots=True)
    if res.status in (404, 410):
        log.info(
            "%s: 次のページ %s が HTTP %d のため、ページ送りを辿らない", url, targets[0], res.status
        )
        return None
    return pattern


def _pagination_follow(f: MunicipalityFinding) -> list[FollowRule]:
    if not f.pagination_pattern:
        return []
    return [FollowRule(pattern=f.pagination_pattern, kind=PageKind.listing_index, max_links=20)]


def _follow_rules(f: MunicipalityFinding) -> list[FollowRule]:
    return _pagination_follow(f) + _detail_follow(f)


@dataclass
class DiscoveryReport:
    prefecture: str
    total: int = 0
    skipped_existing: int = 0
    adopted_crawl: int = 0
    adopted_link_only: int = 0
    pending: int = 0
    excluded: int = 0  # 公式サイトが無いと分かっている市町村（北方領土の 5 村）
    findings: list[MunicipalityFinding] = field(default_factory=list)


def run_discovery(
    munis: list[MunicipalityRef],
    client: PoliteClient,
    platforms: PlatformRegistry,
    *,
    existing_codes: set[str],
    overrides: OfficialOverrides | None = None,
    limit: int | None = None,
    workers: int = 1,
) -> DiscoveryReport:
    """市町村リストを評価し、結果（findings）を集める。書き出しは呼び出し側が行う。

    市町村 ≒ 別ホストなので、市町村単位で並列に評価できる（sitemill ADR 0013）。
    1 ホストあたりの間隔は PoliteClient が守る。結果の順序は入力順に保つ。
    """
    targets = munis[:limit] if limit else munis
    report = DiscoveryReport(prefecture=targets[0].prefecture if targets else "")
    todo: list[MunicipalityRef] = []
    for muni in targets:
        if muni.code in existing_codes:  # 既に手動登録済みの市町村はスキップ
            report.skipped_existing += 1
        else:
            todo.append(muni)

    def assess(muni: MunicipalityRef) -> MunicipalityFinding:
        f = assess_municipality(muni, client, platforms, overrides)
        log.info("%s %s: policy=%s conf=%.2f", muni.code, muni.name, f.policy, f.confidence)
        return f

    if workers > 1 and len(todo) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            findings = list(pool.map(assess, todo))
    else:
        findings = [assess(m) for m in todo]
    _flag_shared_official_urls(findings)
    for f in findings:
        report.findings.append(f)
        report.total += 1
        if f.policy == "crawl":
            report.adopted_crawl += 1
        elif f.policy == "link_only":
            report.adopted_link_only += 1
        elif f.policy == "excluded":
            report.excluded += 1
        else:
            report.pending += 1
    return report


def _flag_shared_official_urls(findings: list[MunicipalityFinding]) -> None:
    """同じ公式サイトに解決した市町村が複数あれば、取り違えなので両方を人間確認に回す。

    同名の市町村（北海道の泊村は古宇郡と国後郡の 2 つ）は候補ドメインの推測が同じ URL に当たる。
    県の市町村一覧でも同名は対応づけないので、どちらのサイトかは機械では決められない。
    片方に相手のサイトを結び付けて公開するより、人間に確認してもらう方がよい。
    """
    by_host: dict[str, list[MunicipalityFinding]] = {}
    for f in findings:
        if f.official_url and f.policy != "pending":
            # official_url はホスト名だけのことも URL のこともある
            key = host_of(f.official_url) or f.official_url.lower()
            by_host.setdefault(key, []).append(f)
    for host, group in by_host.items():
        if len(group) < 2:
            continue
        names = "・".join(f"{f.muni.name}({f.muni.code})" for f in group)
        for f in group:
            f.policy = "pending"
            f.proposed_action = "URL修正"
            f.confidence = 0.2
            f.reason = f"同じ公式サイト {host} に複数の市町村が解決した（{names}）。取り違えの恐れ"
            f.bank_url = None
            f.classified = None
            f.operator_kind = OperatorKind.unknown
            f.evidence_quote = None
            f.evidence_url = None


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# bank_status（巡回状況）の判定
def bank_status_of(f: MunicipalityFinding) -> str:
    if f.policy == "crawl":
        return "available"
    if f.policy == "link_only":
        pc = f.classified.page_class if f.classified is not None else None
        if pc is PageClass.spa:
            return "spa_unsupported"
        if pc is PageClass.third_party:
            return "third_party_only"
        if pc is PageClass.not_listing:
            return "info"
        return "none"  # バンクページ未特定 / 非公式ホストの物件一覧 → 公式へリンク
    return "review"


def _bank_note(f: MunicipalityFinding, status: str) -> str:
    if status == "third_party_only":
        return (
            "物件は民間プラットフォームで公開されています。"
            "本サイトでは巡回・要約せず、リンクのみ掲載します。"
        )
    if status == "spa_unsupported":
        return (
            "空き家バンクのサイトが JavaScript 表示のため、本サイトでは物件を要約できません。"
            "公式サイトでご確認ください。"
        )
    if status == "info":
        return (
            "この自治体の空き家バンクは制度案内が中心で、物件は利用登録後などに公開される方式です。"
            "詳しくは公式の空き家バンクページをご確認ください。"
        )
    if status == "none":
        return (
            "本サイトが確認した範囲では、この自治体の空き家バンクの物件ページを見つけられませんでした。"
            "移住・空き家の情報は公式サイトや県の窓口をご確認ください。"
        )
    return ""


def clean_link_label(text: str) -> str:
    """リンクの見出しから連絡先を落とす。

    民間プラットフォームのアンカーには「【お気軽にご相談ください Tel. 0284-20-2266】」のように
    担当者の電話番号が入ることがある。そのまま保存するとページに出て公開前の PII 検査で止まる。
    """
    label = PHONE_RE.sub("", unicodedata.normalize("NFKC", text or ""))
    label = re.sub(r"[ 　]{2,}", " ", label).strip(" 　・-")
    return label[:60]


_RAKUEN_NAGANO = ExternalLink(
    label="楽園信州 空き家バンク・空き地バンク（長野県）",
    url="https://rakuen-akiya.jp/",
    note="長野県が案内する県内横断の空き家・空き地バンク（民間運営）。",
)


def finding_to_source_dict(f: MunicipalityFinding, *, prefecture_name: str) -> dict:
    """finding を data/sources/*.yaml の 1 エントリ（dict）にする。"""
    m = f.muni
    status = bank_status_of(f)
    sid = f"{m.prefecture_slug}-{m.code}"
    externals = list(f.external_links)
    rakuen_statuses = ("third_party_only", "none", "spa_unsupported", "info")
    if m.prefecture_slug == "nagano" and status in rakuen_statuses:
        if not any(e.url == _RAKUEN_NAGANO.url for e in externals):
            externals.append(_RAKUEN_NAGANO)
    op_kind = f.operator_kind.value if hasattr(f.operator_kind, "value") else str(f.operator_kind)
    entry: dict = {
        "id": sid,
        "name": f"{m.name}空き家バンク",
        "operator": m.name,
        "operator_kind": op_kind,
        "official_url": ("https://" + f.official_url + "/") if f.official_url else f.bank_url,
        # 巡回してよいか（＝巡回するページがあるか）。物件一覧が取れるかは bank_status が持つ。
        # 補助金のページだけを巡回する自治体もここで crawl になる（ADR 0013）
        "policy": "crawl" if (status == "available" or f.subsidy_urls) else "link_only",
        "municipality": {
            "code": m.code,
            "name": m.name,
            "prefecture": prefecture_name,
            "prefecture_slug": m.prefecture_slug,
            "slug": m.slug,
            "bank_url": f.bank_url
            or (("https://" + f.official_url + "/") if f.official_url else ""),
            "bank_status": status,
            "extract_pending": f.extract_gap,
            "map_query": f"{prefecture_name}{m.name}",
        },
    }
    note = _bank_note(f, status)
    if note:
        entry["municipality"]["bank_note"] = note
    if f.evidence_quote:
        entry["operator_evidence"] = {
            "quote": f.evidence_quote,
            "url": f.evidence_url or entry["official_url"],
            "checked_on": date.today().isoformat(),
        }
    pages: list[dict] = []
    if status == "available" and f.bank_url:
        kind = (
            "listing_detail"
            if (f.classified and f.classified.page_class is PageClass.listing_detail)
            else "listing_index"
        )
        page: dict = {"url": f.bank_url, "kind": kind}
        follow: list[dict] = []
        if f.pagination_pattern and kind == "listing_index":
            follow.append(
                {"pattern": f.pagination_pattern, "kind": "listing_index", "max_links": 20}
            )
        if f.detail_pattern and kind == "listing_index":
            follow.append({"pattern": f.detail_pattern, "kind": "listing_detail", "max_links": 60})
        if follow:
            page["follow"] = follow
        pages.append(page)
    # 補助制度のページ。物件とは別の仕様で抽出する（spec.SUBSIDY_SPEC）。
    # 物件一覧が取れない自治体でも、公式サイトの補助金ページは巡回する（ADR 0013）
    pages += [{"url": u, "kind": "subsidy"} for u in f.subsidy_urls]
    if pages:
        entry["pages"] = pages
        entry["allow_hosts"] = sorted({host_of(p["url"]) for p in pages})
        entry["max_pages"] = 30
    if externals:
        entry["external_links"] = [
            {"label": e.label, "url": e.url, **({"note": e.note} if e.note else {})}
            for e in externals
        ]
    return entry


DEFAULT_PLATFORM_EXTRA = frozenset({"39ijyu.com", "iju-omachi.jp", "furusato-iiyama.net"})


def existing_codes_from_sources(ws: Workspace) -> set[str]:
    """手動 sources（*-auto.yaml を除く）の市町村コードを集める。auto は毎回再生成する。"""
    from akiya_atlas.data import load_entries, municipality_from_entry

    codes: set[str] = set()
    for entry in load_entries(ws):
        if str(entry.get("_file", "")).endswith("-auto.yaml"):
            continue
        m = municipality_from_entry(entry)
        if m is not None:
            codes.add(m.code)
    return codes


def discover_and_write(
    ws: Workspace,
    prefecture: str,
    *,
    limit: int | None = None,
    adopt_threshold: float = CONFIDENCE_ADOPT,
    client: PoliteClient | None = None,
    workers: int | None = None,
) -> DiscoveryReport:
    """都道府県を評価し、高確信は sources に、全件を review 行列に書き出す。"""
    table = default_code_table_path(ws.root)
    munis = municipalities_for(prefecture, table)
    pref_name = munis[0].prefecture if munis else prefecture
    pref_slug = munis[0].prefecture_slug if munis else prefecture
    existing = existing_codes_from_sources(ws)
    overrides = OfficialOverrides.load(ws, pref_slug)
    platforms = PlatformRegistry()

    owns_client = client is None
    if client is None:
        crawl = ws.site.crawl
        client = PoliteClient(
            ws.site.user_agent,
            default_delay=crawl.default_delay_seconds,
            jitter=crawl.jitter_seconds,
            timeout=crawl.timeout_seconds,
        )
    try:
        report = run_discovery(
            munis,
            client,
            platforms,
            existing_codes=existing,
            overrides=overrides,
            limit=limit,
            workers=workers or ws.site.crawl.max_workers,
        )
    finally:
        if owns_client:
            client.close()

    # 運営主体が確認できたもの（policy が crawl / link_only）はすべて自動採用する。
    # 確信度の閾値は使わない（運営主体の確認が採用の条件。ADR 0007 の原則）。
    del adopt_threshold
    _write_findings(ws, pref_slug, report.findings)
    adopted, candidates = _outputs_from_findings(report.findings, pref_name, overrides)
    _write_sources_and_review(ws, pref_name, pref_slug, adopted, candidates)

    write_json(
        ws.runs_dir / f"discover-{pref_slug}.json",
        {
            "prefecture": pref_name,
            "total": report.total,
            "skipped_existing": report.skipped_existing,
            "adopted_crawl": report.adopted_crawl,
            "adopted_link_only": report.adopted_link_only,
            "pending": report.pending,
            "requests": client.request_count,
        },
    )
    return report


def _finding_to_row(f: MunicipalityFinding) -> dict:
    """finding を JSON 化（再書き出し用。ネットワーク無しで sources/review を作り直せる）。"""
    return {
        "code": f.muni.code,
        "name": f.muni.name,
        "name_kana": f.muni.name_kana,
        "prefecture": f.muni.prefecture,
        "prefecture_slug": f.muni.prefecture_slug,
        "official_url": f.official_url,
        "bank_url": f.bank_url,
        "page_class": f.classified.page_class.value if f.classified else None,
        "confidence": f.confidence,
        "operator_kind": f.operator_kind.value,
        "evidence_quote": f.evidence_quote,
        "evidence_url": f.evidence_url,
        "cross_linked": f.cross_linked,
        "policy": str(f.policy),
        # 物件一覧が取れるか（ADR 0013）。policy は「巡回してよいか」なので別に持つ
        "bank_status": bank_status_of(f),
        "reason": f.reason,
        "proposed_action": f.proposed_action,
        "external_links": [
            {"label": e.label, "url": e.url, **({"note": e.note} if e.note else {})}
            for e in f.external_links
        ],
        "listing_score": f.listing_score,
        "listing_rows": f.listing_rows,
        "pagination_pattern": f.pagination_pattern,
        "detail_pattern": f.detail_pattern,
        "extract_gap": f.extract_gap,
        "subsidy_urls": list(f.subsidy_urls),
        "alternatives": list(f.alternatives),
    }


def _row_to_finding(row: dict) -> MunicipalityFinding:
    muni = MunicipalityRef(
        code=row["code"],
        prefecture=row["prefecture"],
        prefecture_slug=row["prefecture_slug"],
        name=row["name"],
        name_kana=row.get("name_kana", ""),
    )
    classified = None
    if row.get("page_class"):
        classified = ClassifiedPage(
            url=row.get("bank_url") or "",
            page_class=PageClass(row["page_class"]),
            confidence=row.get("confidence", 0.0),
        )
    return MunicipalityFinding(
        muni=muni,
        official_url=row.get("official_url"),
        bank_url=row.get("bank_url"),
        classified=classified,
        operator_kind=OperatorKind(row.get("operator_kind", "unknown")),
        evidence_quote=row.get("evidence_quote"),
        evidence_url=row.get("evidence_url"),
        cross_linked=row.get("cross_linked", False),
        external_links=[
            ExternalLink(label=clean_link_label(e["label"]), url=e["url"], note=e.get("note"))
            for e in row.get("external_links", [])
        ],
        policy=row.get("policy", "pending"),
        confidence=row.get("confidence", 0.0),
        reason=row.get("reason", ""),
        proposed_action=row.get("proposed_action", "承認"),
        listing_score=row.get("listing_score"),
        listing_rows=row.get("listing_rows"),
        pagination_pattern=row.get("pagination_pattern"),
        detail_pattern=row.get("detail_pattern"),
        extract_gap=bool(row.get("extract_gap")),
        subsidy_urls=list(row.get("subsidy_urls") or []),
        alternatives=list(row.get("alternatives", [])),
    )


def _findings_path(ws: Workspace, pref_slug: str) -> Path:
    return ws.runs_dir / f"discover-{pref_slug}-findings.json"


def _write_findings(ws: Workspace, pref_slug: str, findings: list[MunicipalityFinding]) -> None:
    write_json(_findings_path(ws, pref_slug), {"findings": [_finding_to_row(f) for f in findings]})


def _outputs_from_findings(
    findings: list[MunicipalityFinding],
    pref_name: str,
    overrides: OfficialOverrides | None = None,
) -> tuple[list[dict], list[ReviewCandidate]]:
    adopted: list[dict] = []
    candidates: list[ReviewCandidate] = []
    for f in findings:
        if f.policy == "excluded":
            continue  # 公式サイトが無い市町村。sources にも review にも出さない
        candidates.append(finding_to_candidate(f))
        if f.policy in ("crawl", "link_only"):
            entry = finding_to_source_dict(f, prefecture_name=pref_name)
            _apply_page_note(entry, overrides)
            adopted.append(entry)
    return adopted, candidates


def _apply_page_note(entry: dict, overrides: OfficialOverrides | None) -> None:
    """物件一覧を巡回しないと決めた自治体には、その理由を画面の案内文として出す。

    状態だけで決まる既定文は「物件ページを見つけられませんでした」で、PDF や検索フォームで
    公開されていると分かっている自治体には事実と違う。上書きファイルの page_note があれば
    それに差し替える。案内文のすぐ後ろには公式ページへの主ボタンが出る（テンプレート側）。
    """
    if overrides is None:
        return
    muni = entry.get("municipality") or {}
    row = overrides.link_only.get(str(muni.get("code") or ""))
    note = str((row or {}).get("page_note") or "").strip()
    if note and muni.get("bank_status") != "available":
        muni["bank_note"] = note


def _write_sources_and_review(
    ws: Workspace,
    pref_name: str,
    pref_slug: str,
    adopted: list[dict],
    candidates: list[ReviewCandidate],
) -> None:
    auto_path = ws.sources_dir / f"{pref_slug}-auto.yaml"
    auto_path.parent.mkdir(parents=True, exist_ok=True)
    header = "# 自動発見で採用した source（ADR 0007）。毎回の discover で再生成される。\n"
    body = yaml.safe_dump({"sources": adopted}, allow_unicode=True, sort_keys=False, width=200)
    auto_path.write_text(header + body, encoding="utf-8", newline="\n")
    queue = ReviewQueue(
        prefecture=pref_name,
        prefecture_slug=pref_slug,
        created_at=utc_now_iso(),
        candidates=candidates,
    )
    queue.save(ws.root / "data" / "review" / f"{pref_slug}.yaml")


def rebuild_outputs(ws: Workspace, prefecture: str) -> tuple[int, int]:
    """保存済み findings から sources/review を作り直す。(採用数, pending数) を返す。"""
    from sitemill.store.jsonio import read_json

    munis = municipalities_for(prefecture, default_code_table_path(ws.root))
    pref_name = munis[0].prefecture if munis else prefecture
    pref_slug = munis[0].prefecture_slug if munis else prefecture
    data = read_json(_findings_path(ws, pref_slug)) or {"findings": []}
    findings = [_row_to_finding(r) for r in data["findings"]]
    overrides = OfficialOverrides.load(ws, pref_slug)
    adopted, candidates = _outputs_from_findings(findings, pref_name, overrides)
    _write_sources_and_review(ws, pref_name, pref_slug, adopted, candidates)
    return len(adopted), sum(1 for c in candidates if c.proposed_policy == "pending")


# --- 自己修復（発見の誤採用を人手ではなく仕組みで直す） -------------------------------------


def _official_from_row(
    muni: MunicipalityRef, row: dict, client: PoliteClient, overrides: OfficialOverrides
) -> tuple[OfficialHost, str, str | None, str | None] | None:
    """finding 行の公式ホストから (host, 到達URL, 根拠引用, 根拠URL) を安く求める。

    ホストに到達できなければ候補 URL と県一覧から解決し直す。"""
    host = row.get("official_url")
    if host:
        oh = classify_host(host, muni.prefecture_slug)
        if not oh.is_official:
            oh = OfficialHost(host, "prefecture_listed", True, "strong")
        for scheme in ("https", "http"):
            res = client.get(f"{scheme}://{host}/")
            if res.ok:
                # 前回採用したホストでも、市町村名が出なければ使い回さず解決し直す
                if not page_names_municipality(res.text, muni):
                    break
                return oh, res.final_url, row.get("evidence_quote"), row.get("evidence_url")
    resolved = resolve_official(muni, client, overrides)
    if resolved is None:
        return None
    return resolved.host, resolved.url, resolved.evidence_quote, resolved.evidence_url


def reassess_finding(
    row: dict, client: PoliteClient, platforms: PlatformRegistry, overrides: OfficialOverrides
) -> MunicipalityFinding:
    """保存済み finding の市町村を、公式サイト上の候補から選び直して評価し直す。"""
    old = _row_to_finding(row)
    resolved = _official_from_row(old.muni, row, client, overrides)
    if resolved is None:
        return decide(old.muni, None, None, cross_linked=False, bank_host_official=False)
    official, official_url, quote, evidence_url = resolved
    probe = find_bank_page(official_url, official, client, platforms)
    f = decide(
        old.muni,
        official,
        probe.classified,
        cross_linked=probe.cross_linked,
        bank_host_official=probe.host_official,
    )
    f.official_url = official.host
    if not probe.cross_linked:
        f.evidence_quote = quote or f.evidence_quote
        f.evidence_url = evidence_url or f.evidence_url
    _attach_probe(f, probe)
    return f


MIN_BODY_TEXT = 200  # これ未満なら本文を取り出せていないとみなす（抽出側の問題）
# 「現在、登録物件はありません」のように、掲載が無いことをページ自身が書いている
_EMPTY_NOTICE = re.compile(r"(?:物件|情報)[はも]?(?:、|\s)*(?:ありません|ございません|None)")

# ページ自身が「現在は募集していません」と書いている source を、次に見にいくまでの日数。
# 結論が変わりにくいので毎晩は取りにいかない。
# 巡回の間隔と同じ考え方で、sitemill ADR 0015 の下限（週 1 回）に合わせる
EMPTY_RECHECK_DAYS = 7


def _evidence_of(score: ListingScore | None, url: str | None) -> dict | None:
    """`has_listing_evidence` が何を見て判断したかを残す。

    発見の側（`decide`）はこの検査を順位付けにしか使っていないので、証拠の無いページでも
    1 位なら採用されうる。門にしてよいかは、採用したページが後で物件を出したかどうかで
    決めるしかない。その判断材料として、選び直しの記録に数字を残す（2026-09-19）。
    """
    if score is None:
        return None
    return {
        "url": url,
        "passes": has_listing_evidence(score),
        "rows": score.rows,
        "listing_no": score.listing_no_count,
        "property_prices": score.property_prices,
        "subsidy_hits": score.subsidy_hits,
    }


def _page_signals(url: str | None, client: PoliteClient) -> tuple[str, ListingScore | None]:
    """抽出に渡るのと同じ本文と、一覧らしさ。0 件の理由を見分けるために使う。"""
    if not url:
        return "", None
    res = client.get(url)
    if not res.ok:
        return "", None
    return page_text(res.text), listing_score(res.text, res.final_url)


def _reset_source_state(ws: Workspace, source_id: str, keep: set[str]) -> int:
    """差し替えた source の古い URL の巡回状態を消す（新しい一覧は次の crawl で取り直す）。"""
    from sitemill.diff.state import CrawlState

    path = ws.state_dir / "crawl.json"
    state = CrawlState.load(path)
    removed = [u for u, s in state.urls.items() if s.source_id == source_id and u not in keep]
    for u in removed:
        state.urls.pop(u)
    if removed:
        state.save(path)
    return len(removed)


def _write_rows(ws: Workspace, pref_slug: str, pref_name: str, rows: list[dict]) -> None:
    """findings 行をそのまま保存し、sources/review を作り直す。"""
    write_json(_findings_path(ws, pref_slug), {"findings": rows})
    findings = [_row_to_finding(r) for r in rows]
    adopted, candidates = _outputs_from_findings(
        findings, pref_name, OfficialOverrides.load(ws, pref_slug)
    )
    _write_sources_and_review(ws, pref_name, pref_slug, adopted, candidates)


def _describe(f: MunicipalityFinding, old_url: str | None) -> str:
    pages = "あり" if f.pagination_pattern else "なし"
    return (
        f"{f.muni.name}: {old_url or '-'} → {f.bank_url or '-'} policy={f.policy} "
        f"status={bank_status_of(f)} 物件行={f.listing_rows} 分ページ={pages}"
    )


def collect_subsidy_pages(
    ws: Workspace,
    prefecture: str,
    *,
    client: PoliteClient,
    limit: int = 4,
    workers: int = 1,
    codes: Collection[str] | None = None,
) -> list[str]:
    """県内の各自治体の公式サイトから補助制度のページを見つけ、巡回対象に足す。

    1 自治体につき数ページだけ取得する。見つかった URL は findings に残し、
    sources の pages に kind=subsidy として出る。巡回と抽出は通常の日次に任せる。
    市町村 ≒ 別ホストなので、市町村単位で並列に見る（発見と同じ。sitemill ADR 0013）。
    1 ホストあたりの間隔は PoliteClient が守るので、並列にしても縮まらない。
    `codes` を渡すとその市町村だけ見る（選び直した市町村の補助制度を取り直すとき）。
    """
    from sitemill.store.jsonio import read_json

    munis = municipalities_for(prefecture, default_code_table_path(ws.root))
    if not munis:
        return [f"{prefecture}: 市町村が見つからない"]
    pref_name, pref_slug = munis[0].prefecture, munis[0].prefecture_slug
    data = read_json(_findings_path(ws, pref_slug)) or {"findings": []}
    rows: list[dict] = list(data["findings"])
    # 物件一覧が取れない自治体でも、公式サイトの補助金ページは探す（ADR 0013）。
    # 運営主体を判定できていない pending は対象にしない
    targets = [
        (i, row)
        for i, row in enumerate(rows)
        if row.get("policy") != "pending"
        and (row.get("official_url") or row.get("bank_url"))
        and (not codes or str(row.get("code")) in codes)
    ]

    def look(item: tuple[int, dict]) -> tuple[int, dict, list[tuple[str, str]]]:
        i, row = item
        official = str(row.get("official_url") or "")
        if official and not official.startswith("http"):
            official = "https://" + official + "/"
        starts = [str(row.get("bank_url") or ""), official]
        return i, row, find_subsidy_pages(starts, client, limit=limit)

    if workers > 1 and len(targets) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(look, targets))
    else:
        results = [look(t) for t in targets]

    lines: list[str] = []
    found_total = 0
    for i, row, pages in results:
        if not pages:
            continue
        rows[i] = {**row, "subsidy_urls": [u for u, _ in pages]}
        found_total += len(pages)
        labels = "、".join(text for _, text in pages)
        lines.append(f"{row.get('name')}: 補助制度のページ {len(pages)} 件（{labels}）")
    if found_total:
        _write_rows(ws, pref_slug, pref_name, rows)
    lines.append(
        f"{pref_name}: 対象の {len(targets)} 自治体を見て、{found_total} ページを見つけた"
        f"（{sum(1 for r in rows if r.get('subsidy_urls'))} 自治体）"
    )
    return lines


def rediscover_codes(
    ws: Workspace, prefecture: str, codes: list[str], *, client: PoliteClient
) -> list[str]:
    """指定した市町村だけ選択器で評価し直し、findings と sources/review を書き直す。"""
    from sitemill.store.jsonio import read_json

    munis = municipalities_for(prefecture, default_code_table_path(ws.root))
    if not munis:
        return [f"{prefecture}: 市町村が見つからない"]
    pref_name, pref_slug = munis[0].prefecture, munis[0].prefecture_slug
    by_code = {m.code: m for m in munis}
    overrides = OfficialOverrides.load(ws, pref_slug)
    platforms = PlatformRegistry()
    data = read_json(_findings_path(ws, pref_slug)) or {"findings": []}
    rows: list[dict] = list(data["findings"])
    index = {r["code"]: i for i, r in enumerate(rows)}
    lines: list[str] = []
    for code in codes:
        muni = by_code.get(code)
        if muni is None:
            lines.append(f"{code}: {pref_name}の市町村ではない")
            continue
        old_url = rows[index[code]].get("bank_url") if code in index else None
        f = assess_municipality(muni, client, platforms, overrides)
        row = _finding_to_row(f)
        if code in index:
            rows[index[code]] = row
        else:
            rows.append(row)
            index[code] = len(rows) - 1
        sid = f"{pref_slug}-{code}"
        _reset_source_state(ws, sid, {f.bank_url} if f.policy == "crawl" and f.bank_url else set())
        lines.append(_describe(f, old_url))
    _write_rows(ws, pref_slug, pref_name, rows)
    return lines


def heal(ws: Workspace, *, client: PoliteClient, source_ids: list[str] | None = None) -> dict:
    """巡回したのに現行の掲載が 0 件の自治体を自己修復する（sitemill の heal フック）。

    同一サイト内の候補を選択器で評価し直し、より一覧らしいページがあれば差し替える（要再巡回）。
    無ければ状態を見直す（一覧でない → info/none にして link_only）。
    """
    from sitemill.store.jsonio import read_json

    from akiya_atlas.data import Dataset

    ds = Dataset.load(ws)
    platforms = PlatformRegistry()
    wanted = set(source_ids or [])
    result: dict = {
        "checked": 0,
        "changed": [],
        "recrawl": [],
        "downgraded": [],
        "reselected": [],  # data/sources を書き換えた 1 件ずつの記録（週次レポートが読む）
    }
    for path in sorted(ws.runs_dir.glob("discover-*-findings.json")):
        pref_slug = path.name[len("discover-") : -len("-findings.json")]
        data = read_json(path) or {"findings": []}
        rows: list[dict] = list(data["findings"])
        pref_name = (rows[0].get("prefecture") if rows else None) or pref_slug
        overrides = OfficialOverrides.load(ws, pref_slug)
        touched = False
        for i, row in enumerate(rows):
            # 物件一覧が取れる source だけを直す。補助金のページだけを巡回している自治体は、
            # 物件が 0 件なのが正しい状態なので対象にしない（ADR 0013）
            # 古い findings には bank_status が無いので、その場で計算し直す
            status = row.get("bank_status") or bank_status_of(_row_to_finding(row))
            if row.get("policy") != "crawl" or status != "available":
                continue
            sid = f"{pref_slug}-{row.get('code')}"
            if wanted and sid not in wanted:
                continue
            muni = ds.muni_by_source.get(sid)
            src = ds.by_source.get(sid)
            if muni is None or src is None or ds.listings_for(muni, active_only=True):
                continue
            states = [ds.state.get(p.url) for p in src.pages]
            if not any(st is not None and st.fetched_at is not None for st in states):
                continue  # まだ巡回していない source は対象外
            seen = row.get("empty_checked_on")
            if seen:
                try:
                    if (jst_today() - date.fromisoformat(str(seen))).days < EMPTY_RECHECK_DAYS:
                        continue  # 掲載なしと明記されていた source。週 1 回だけ見る
                except ValueError:
                    pass
            result["checked"] += 1
            before = dict(row)
            said = len(result["changed"])
            new = reassess_finding(row, client, platforms, overrides)
            old_url = row.get("bank_url")
            old_detail = row.get("detail_pattern")
            name = f"{pref_name}{muni.name}"
            judged: dict | None = None
            swap = bool(new.policy == "crawl" and new.bank_url and new.bank_url != old_url)
            if swap:
                # 差し替え先にも、取り下げと同じ物差しを当てる。行数だけで一覧と決めると制度案内・
                # 移住ポータルを掴み、翌晩の点検が同じ行数を見て取り下げる。瀬戸内市は 09-18 に
                # 差し替えて 09-19 に取り下げで戻った（2026-09-19）
                _, cand = _page_signals(new.bank_url, client)
                judged = _evidence_of(cand, new.bank_url)
                if cand is None or not has_listing_evidence(cand):
                    result["changed"].append(
                        f"{name}: 差し替え候補は一覧ではなかったので採らない"
                        f"（物件行 {cand.rows if cand else 0}・物件番号 "
                        f"{cand.listing_no_count if cand else 0}・物件価格 "
                        f"{cand.property_prices if cand else 0}・補助金語 "
                        f"{cand.subsidy_hits if cand else 0}・{new.bank_url}）"
                    )
                    new = _row_to_finding(row)  # いまの URL のまま 0 件の理由を見る
                    swap = False
            if swap:
                rows[i] = _finding_to_row(new)
                touched = True
                result["recrawl"].append(sid)
                _reset_source_state(ws, sid, {new.bank_url})
                result["changed"].append(
                    f"{name}: 一覧を差し替え {old_url} → {new.bank_url}"
                    f"（物件行 {new.listing_rows}）"
                )
            elif new.policy == "crawl":
                # 0 件の理由は 3 通りある。取り下げてよいのは「一覧ではなかった」ときだけ
                text, score = _page_signals(new.bank_url, client)
                judged = _evidence_of(score, new.bank_url)
                if len(text) < MIN_BODY_TEXT:
                    new.extract_gap = True
                    rows[i] = _finding_to_row(new)
                    touched = True
                    result["changed"].append(
                        f"{name}: 本文を取り出せないページ（物件行 {new.listing_rows}）。"
                        "抽出側の問題として保留"
                    )
                elif _EMPTY_NOTICE.search(text):
                    # 掲載が無いと書いてある。差し替えも取り下げもせず、次は 7 日後に見る。
                    # 日付だけを findings に残す（sources/review の中身は変わらない）
                    rows[i] = {**row, "empty_checked_on": jst_today().isoformat()}
                    touched = True
                    result["changed"].append(
                        f"{name}: ページ自身が掲載なしと書いている。"
                        f"そのまま（次の点検は {EMPTY_RECHECK_DAYS} 日後）"
                    )
                elif score is not None and not has_listing_evidence(score):
                    # 行はあっても物件番号も物件価格も伴わない＝一覧ではない。
                    # 公式へのリンクだけにする
                    # （制度説明・移住ポータル・第三者サービスの紹介を掴んでいた実例がある）
                    fixed = decide(
                        new.muni, new.official, None, cross_linked=False, bank_host_official=False
                    )
                    fixed.official_url = new.official_url
                    fixed.evidence_quote = new.evidence_quote
                    fixed.evidence_url = new.evidence_url
                    fixed.reason = (
                        "物件の一覧ではなかった（0 件・物件番号なし）。公式へのリンクのみ"
                    )
                    rows[i] = _finding_to_row(fixed)
                    touched = True
                    result["downgraded"].append(sid)
                    _reset_source_state(ws, sid, set())
                    result["changed"].append(
                        f"{name}: 一覧ではなかったので取り下げ（物件行 {score.rows}・{old_url}）"
                    )
                else:
                    res = client.get(new.bank_url) if new.bank_url else None
                    found = (
                        detect_detail_follow(new.bank_url, res.text, client)
                        if res is not None and res.ok
                        else None
                    )
                    if found and found[0] != old_detail:
                        # 一覧は物件名とリンクだけで、価格などは詳細ページにある形（長岡市など）
                        new.detail_pattern, links = found
                        new.extract_gap = False
                        rows[i] = _finding_to_row(new)
                        touched = True
                        result["recrawl"].append(sid)
                        _reset_source_state(ws, sid, {new.bank_url} if new.bank_url else set())
                        result["changed"].append(
                            f"{name}: 詳細ページを辿るようにした（{links} 本）"
                        )
                    else:
                        new.extract_gap = True
                        rows[i] = _finding_to_row(new)
                        touched = True
                        rows_text = score.rows if score else "?"
                        note = (
                            f"一覧に見えるのに 0 件（物件行 {rows_text}）。抽出側の課題として保留"
                        )
                        result["changed"].append(f"{name}: {note}")
            else:
                rows[i] = _finding_to_row(new)
                touched = True
                result["downgraded"].append(sid)
                _reset_source_state(ws, sid, set())
                result["changed"].append(
                    f"{name}: 一覧が見つからず {bank_status_of(new)} に変更（{new.reason}）"
                )
            # この source の行が変わったなら、何をどう変えたかを 1 件の記録にする。
            # data/sources はこの後まとめて書き直されるので、書き換えの根拠をここで残す
            after = rows[i]
            if after != before:
                note = result["changed"][-1] if len(result["changed"]) > said else ""
                result["reselected"].append(
                    {
                        "at": jst_now().isoformat(timespec="seconds"),
                        "source_id": sid,
                        "code": str(before.get("code") or ""),
                        "name": name,
                        "old_url": before.get("bank_url"),
                        "new_url": after.get("bank_url"),
                        "old_policy": before.get("policy"),
                        "new_policy": after.get("policy"),
                        "reason": note.split(": ", 1)[-1] if note else "",
                        "evidence": judged,
                    }
                )
        if touched:
            _write_rows(ws, pref_slug, pref_name, rows)
    if result["reselected"]:
        _append_reselections(ws, result["reselected"])
    return result


def _append_reselections(ws: Workspace, rows: list[dict]) -> None:
    """heal が data/sources を書き換えた記録を追記する。

    実行レポートの notes にも同じ内容が文章で残るが、こちらは後から機械で追える形にする
    （週次レポートの「今週 heal が選び直した自治体」がこれを読む）。置き場は data/runs で、
    日次のコミット対象に入っている。
    """
    path = ws.runs_dir / "heal-reselections.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
