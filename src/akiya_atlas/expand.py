"""都道府県パラメータでの自動発見（ADR 0007, 条件1〜5）。

各市町村について: 公式 URL を解決 → 空き家バンクページを発見・分類 → 運営主体の根拠を記録 →
確信度で policy（crawl / link_only / pending）を決める。高確信は自動採用、低確信はレビュー行列へ。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import yaml
from sitemill.classify import ClassifiedPage, PageClass, PlatformRegistry, classify_page
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links, host_of
from sitemill.models import (
    CrawlPolicy,
    ExternalLink,
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


def resolve_official_url(
    muni: MunicipalityRef, client: PoliteClient, *, max_probes: int = 16
) -> tuple[str, OfficialHost] | None:
    """候補 URL を順に叩き、到達できた公式ドメインを返す。"""
    for url in candidate_official_urls(muni)[:max_probes]:
        res = client.get(url, check_robots=True)
        if res.ok or res.not_modified:
            oh = classify_host(host_of(res.final_url), muni.prefecture_slug)
            if oh.is_official:
                return res.final_url, oh
    return None


@dataclass
class BankProbe:
    url: str | None = None
    classified: ClassifiedPage | None = None
    externals: list[ExternalLink] = field(default_factory=list)
    host_official: bool = False  # バンクページが公式ドメイン上にあるか
    cross_linked: bool = False  # 非公式ホストだが公式サイトから空き家バンクとして案内されているか


_AKIYA_URL = re.compile(r"aki|akiya|空き?家|空家|akiyabank|akiya-bank", re.I)
_AKIYA_ANCHOR = re.compile(r"空き?家|空家バンク|あき家")
_INTERMEDIATE = re.compile(r"住ま|移住|定住|くらし|暮らし|生活|空き?家|不動産|土地")


@dataclass
class _Cand:
    url: str
    text: str
    found_on: str
    score: int


def _deep_discover_bank(
    official_url: str, official: OfficialHost, client: PoliteClient
) -> _Cand | None:
    """公式トップ→中間カテゴリ→空き家、および sitemap を辿って空き家バンク候補を探す（2 階層）。"""
    top = client.get(official_url)
    if not top.ok:
        return None
    best: _Cand | None = None
    seen_pages: set[str] = {official_url}

    def consider(url: str, text: str, found_on: str) -> None:
        nonlocal best
        s = 0
        if _AKIYA_ANCHOR.search(text):
            s += 3
        if _AKIYA_URL.search(url):
            s += 2
        if s == 0:
            return
        if best is None or s > best.score:
            best = _Cand(url=url, text=text[:80], found_on=found_on, score=s)

    top_links = extract_links(top.text, top.final_url)
    for ln in top_links:
        consider(ln.url, ln.text, official_url)
    # 直リンクが弱ければ中間ページを 1 階層辿る
    if best is None or best.score < 5:
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
            if best and best.score >= 5:
                break
    # sitemap も直接見る
    if best is None or best.score < 5:
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
    return best


def find_bank_page(
    official_url: str, official: OfficialHost, client: PoliteClient, platforms: PlatformRegistry
) -> BankProbe:
    """公式サイトから空き家バンクページを見つけ分類する。公式ドメイン上か/相互リンクかも判定する。"""
    probe = BankProbe()
    top = _deep_discover_bank(official_url, official, client)
    if top is None:
        return probe
    probe.url = top.url
    res = client.get(top.url)
    if not res.ok:
        return probe
    probe.classified = classify_page(res.text, top.url, platforms=platforms)
    bank_host = host_of(top.url)
    pref_slug = _pref_slug(official)
    # 公式ドメイン上か（同一ホスト、または公式の地理型/lg.jp ドメイン）
    probe.host_official = (
        bank_host == official.host or classify_host(bank_host, pref_slug).is_official
    )
    # 非公式ホストなら、公式ページから空き家バンクとして案内されているか（相互リンク）
    if not probe.host_official:
        found_on_official = bool(top.found_on) and _host_is(top.found_on, official.host)
        probe.cross_linked = found_on_official and bool(_BANK_ANCHOR.search(top.text or ""))
    # 外部プラットフォームへのリンクを収集
    for ln in extract_links(res.text, res.final_url):
        if platforms.is_platform(ln.url):
            probe.externals.append(
                ExternalLink(label=ln.text[:60] or platforms.match(ln.url) or "", url=ln.url)
            )
    return probe


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
    """分類・公式ドメイン・相互リンクから policy と確信度を決める（純関数、テスト対象）。"""
    f = MunicipalityFinding(muni=muni, official=official, classified=classified)
    if official is None:
        f.policy = "pending"
        f.confidence = 0.2
        f.reason = "公式 URL を自動解決できなかった"
        f.proposed_action = "URL修正"
        return f

    f.official_url = official.host
    if classified is None:
        f.policy = "pending"
        f.confidence = 0.3
        f.reason = "空き家バンクページが見つからない（掲載なしの可能性）"
        f.proposed_action = "承認"  # 「空き家バンクなし」ページとして承認
        return f

    f.bank_url = classified.url
    pc = classified.page_class
    # 運営主体の根拠
    if bank_host_official:
        f.operator_kind = OperatorKind.municipality
        f.evidence_quote = official.evidence()
        f.evidence_url = classified.url
    elif cross_linked:
        f.operator_kind = OperatorKind.municipality
        f.evidence_quote = (
            f"公式サイト（{official.host}）から空き家バンクとして案内されているリンク"
        )
        f.evidence_url = official.host
    else:
        f.operator_kind = OperatorKind.unknown

    if pc is PageClass.third_party:
        f.policy = "link_only"
        f.confidence = classified.confidence
        f.reason = "掲載は民間プラットフォーム。巡回せずリンクのみ"
        f.proposed_action = "承認"
    elif pc is PageClass.spa:
        f.policy = "link_only"
        f.confidence = classified.confidence
        f.reason = "JavaScript 描画のため静的 HTML に物件が無い。リンクのみ"
        f.proposed_action = "承認"
    elif pc in (PageClass.listing_index, PageClass.listing_detail):
        if f.operator_kind is OperatorKind.municipality:
            f.policy = "crawl"
            f.confidence = min(0.95, 0.55 + classified.confidence * 0.4)
            f.reason = "自治体ドメイン/相互リンクで運営主体を確認、静的な物件ページ"
            f.proposed_action = "承認"
        else:
            f.policy = "pending"
            f.confidence = 0.45
            f.reason = "物件ページだが運営主体の根拠が弱い（非公式ドメイン・相互リンクなし）"
            f.proposed_action = "承認"
    else:  # not_listing
        f.policy = "pending"
        f.confidence = 0.35
        f.reason = "空き家バンクの物件ページを特定できない"
        f.proposed_action = "URL修正"
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
        pages=[SeedPage(url=f.bank_url, kind=kind)],
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
    muni: MunicipalityRef, client: PoliteClient, platforms: PlatformRegistry
) -> MunicipalityFinding:
    """1 市町村を評価して finding を返す（ネットワークを使う）。"""
    resolved = resolve_official_url(muni, client)
    if resolved is None:
        return decide(muni, None, None, cross_linked=False, bank_host_official=False)
    official_url, official = resolved
    probe = find_bank_page(official_url, official, client, platforms)
    f = decide(
        muni,
        official,
        probe.classified,
        cross_linked=probe.cross_linked,
        bank_host_official=probe.host_official,
    )
    f.official_url = official.host
    f.external_links = probe.externals
    f.cross_linked = probe.cross_linked
    return f


@dataclass
class DiscoveryReport:
    prefecture: str
    total: int = 0
    skipped_existing: int = 0
    adopted_crawl: int = 0
    adopted_link_only: int = 0
    pending: int = 0
    findings: list[MunicipalityFinding] = field(default_factory=list)


def run_discovery(
    munis: list[MunicipalityRef],
    client: PoliteClient,
    platforms: PlatformRegistry,
    *,
    existing_codes: set[str],
    limit: int | None = None,
) -> DiscoveryReport:
    """市町村リストを評価し、結果（findings）を集める。書き出しは呼び出し側が行う。"""
    targets = munis[:limit] if limit else munis
    report = DiscoveryReport(prefecture=targets[0].prefecture if targets else "")
    for muni in targets:
        if muni.code in existing_codes:  # 既に手動登録済みの市町村はスキップ
            report.skipped_existing += 1
            continue
        f = assess_municipality(muni, client, platforms)
        report.findings.append(f)
        report.total += 1
        if f.policy == "crawl":
            report.adopted_crawl += 1
        elif f.policy == "link_only":
            report.adopted_link_only += 1
        else:
            report.pending += 1
        log.info("%s %s: policy=%s conf=%.2f", muni.code, muni.name, f.policy, f.confidence)
    return report


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# bank_status（巡回状況）の判定
def bank_status_of(f: MunicipalityFinding) -> str:
    if f.policy == "crawl":
        return "available"
    if f.policy == "link_only" and f.classified is not None:
        if f.classified.page_class is PageClass.spa:
            return "spa_unsupported"
        return "third_party_only"
    if f.official is not None and f.classified is None:
        return "none"
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
    if status == "none":
        return (
            "本サイトが確認した範囲では、この自治体の空き家バンクの物件ページを見つけられませんでした。"
            "移住・空き家の情報は公式サイトや県の窓口をご確認ください。"
        )
    return ""


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
    if m.prefecture_slug == "nagano" and status in ("third_party_only", "none", "spa_unsupported"):
        if not any(e.url == _RAKUEN_NAGANO.url for e in externals):
            externals.append(_RAKUEN_NAGANO)
    op_kind = f.operator_kind.value if hasattr(f.operator_kind, "value") else str(f.operator_kind)
    entry: dict = {
        "id": sid,
        "name": f"{m.name}空き家バンク",
        "operator": m.name,
        "operator_kind": op_kind,
        "official_url": ("https://" + f.official_url + "/") if f.official_url else f.bank_url,
        "policy": "crawl" if status == "available" else "link_only",
        "municipality": {
            "code": m.code,
            "name": m.name,
            "prefecture": prefecture_name,
            "prefecture_slug": m.prefecture_slug,
            "slug": m.slug,
            "bank_url": f.bank_url
            or (("https://" + f.official_url + "/") if f.official_url else ""),
            "bank_status": status,
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
    if status == "available" and f.bank_url:
        kind = (
            "listing_detail"
            if (f.classified and f.classified.page_class is PageClass.listing_detail)
            else "listing_index"
        )
        entry["pages"] = [{"url": f.bank_url, "kind": kind}]
        entry["allow_hosts"] = [host_of(f.bank_url)]
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
) -> DiscoveryReport:
    """都道府県を評価し、高確信は sources に、全件を review 行列に書き出す。"""
    table = default_code_table_path(ws.root)
    munis = municipalities_for(prefecture, table)
    pref_name = munis[0].prefecture if munis else prefecture
    pref_slug = munis[0].prefecture_slug if munis else prefecture
    existing = existing_codes_from_sources(ws)
    platforms = (
        PlatformRegistry()
    )  # 既定 + 移住系サイトも第三者として初期扱いしない（相互リンクで判定するため足さない）

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
        report = run_discovery(munis, client, platforms, existing_codes=existing, limit=limit)
    finally:
        if owns_client:
            client.close()

    # 自動採用（crawl / link_only かつ確信度が閾値以上）を sources に、全件を review に
    adopted: list[dict] = []
    candidates: list[ReviewCandidate] = []
    for f in report.findings:
        c = finding_to_candidate(f)
        adopt = f.policy in ("crawl", "link_only") and f.confidence >= adopt_threshold
        if adopt:
            c.proposed_policy = str(f.policy)
            adopted.append(finding_to_source_dict(f, prefecture_name=pref_name))
        candidates.append(c)

    # auto ファイルは毎回再生成する（採用が 0 でも空で上書きし、古い内容を残さない）
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
    review_path = ws.root / "data" / "review" / f"{pref_slug}.yaml"
    queue.save(review_path)

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
