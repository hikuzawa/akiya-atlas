"""都道府県パラメータでの自動発見（ADR 0007, 条件1〜5）。

各市町村について: 公式 URL を解決 → 空き家バンクページを発見・分類 → 運営主体の根拠を記録 →
確信度で policy（crawl / link_only / pending）を決める。高確信は自動採用、低確信はレビュー行列へ。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sitemill.classify import ClassifiedPage, PageClass, PlatformRegistry, classify_page
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.discover import discover_source
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
from sitemill.review import ReviewCandidate

from akiya_atlas.municipalities import MunicipalityRef
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
    muni: MunicipalityRef, client: PoliteClient, *, max_probes: int = 12
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


def find_bank_page(
    official_url: str, official: OfficialHost, client: PoliteClient, platforms: PlatformRegistry
) -> BankProbe:
    """公式サイトから空き家バンクページを見つけ分類する。公式ドメイン上か/相互リンクかも判定する。"""
    probe_source = Source(
        id="_probe",
        name="probe",
        operator="probe",
        operator_kind=OperatorKind.municipality,
        operator_evidence=OperatorEvidence(quote="probe", url=official_url),
        policy=CrawlPolicy.crawl,
        official_url=official_url,
        pages=[SeedPage(url=official_url)],
    )
    candidates = discover_source(probe_source, client, max_pages=4)
    probe = BankProbe()
    top = candidates[0] if candidates else None
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
    existing_ids: set[str],
    limit: int | None = None,
) -> DiscoveryReport:
    """市町村リストを評価し、結果（findings）を集める。書き出しは呼び出し側が行う。"""
    targets = munis[:limit] if limit else munis
    report = DiscoveryReport(prefecture=targets[0].prefecture if targets else "")
    for muni in targets:
        sid = f"{muni.prefecture_slug}-{muni.code}"
        if sid in existing_ids:
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
