"""ページ生成（ADR 0003, 0004, 0005）。テンプレートには表示用に整形済みの値だけを渡す。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sitemill.build.pii import PiiFinding, default_jp_gov_policy, find_phones, scan_text
from sitemill.build.site import BuildError, date_ja, datetime_ja, m2, yen
from sitemill.charts import (
    Bar,
    Chart,
    bin_values,
    chart_css,
    column_chart,
    flow_diagram,
    hbar_chart,
)
from sitemill.embeds import maps_place_embed
from sitemill.models import Embed, OperatorInfo, Page, PageMeta, Source, SourceLink, TrustSignals
from sitemill.parse.jp.address import has_street_number
from sitemill.settings import Workspace

from akiya_atlas import affiliates
from akiya_atlas.data import Dataset
from akiya_atlas.schema import FIELD_LABELS, Listing, Municipality

PRICE_EDGES = [1_000_000, 3_000_000, 5_000_000, 10_000_000]
PRICE_LABELS = ["100万円未満", "100〜300万円", "300〜500万円", "500〜1,000万円", "1,000万円以上"]
BAND_RENT = "賃貸"
BAND_NONE = "価格記載なし・応相談"
BUILT_EDGES = [1971, 1981, 1991, 2001]
BUILT_LABELS = ["〜1970年", "1971〜80年", "1981〜90年", "1991〜2000年", "2001年〜"]
MIN_CHART_POINTS = 3
SALE_FLOW = [
    "現地と権利関係の確認",
    "自治体の空き家バンクに登録",
    "査定・相談（複数社）",
    "買主と交渉・契約",
    "引き渡し",
]
DEMOLITION_FLOW = [
    "解体補助の有無を確認",
    "見積を複数社で比較",
    "補助金の申請",
    "解体工事",
    "滅失登記・跡地の活用",
]


@dataclass
class Ctx:
    ws: Workspace
    ds: Dataset
    now: datetime
    operator: OperatorInfo
    maps_key: str | None
    hidden: tuple[str, ...] = ()  # 取り下げ依頼で非表示にするページのパス

    @property
    def base_context(self) -> dict[str, Any]:
        op = self.ws.site.operator
        return {
            "chart_css": chart_css(),
            "offers": affiliates.OFFERS,
            "has_maps": bool(self.maps_key),  # 地図を埋め込むかどうか（Cookie の記載が変わる）
            "contact_label": getattr(op, "contact_label", None) or "お問い合わせフォーム",
        }


def price_band(ls: Listing) -> str:
    if ls.price.ok:
        idx = next((i for i, e in enumerate(PRICE_EDGES) if ls.price.value < e), len(PRICE_EDGES))  # type: ignore[operator]
        return PRICE_LABELS[idx]
    if ls.deal_type == "rent" or (ls.rent_monthly.ok and not ls.price.ok):
        return BAND_RENT
    return BAND_NONE


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def listing_path(muni: Municipality, ls: Listing) -> str:
    return f"{muni.path}{ls.slug}/index.html"


def hidden_paths(ws: Workspace) -> tuple[str, ...]:
    """取り下げ依頼で非表示にするページのパス（data/reference/takedowns.json）。"""
    from akiya_atlas.takedown import TakedownList

    return tuple(sorted(TakedownList.load(ws).paths))


def is_hidden(path: str, hidden: tuple[str, ...]) -> bool:
    """そのパス自身か、その配下（市町村ごと取り下げ）なら True。"""
    return any(path == h or path.startswith(h) for h in hidden)


def visible_listings(
    muni: Municipality, listings: list[Listing], hidden: tuple[str, ...]
) -> list[Listing]:
    if not hidden:
        return listings
    return [ls for ls in listings if not is_hidden(listing_url_path(muni, ls), hidden)]


def listing_url_path(muni: Municipality, ls: Listing) -> str:
    return f"/{muni.path}{ls.slug}/"


def price_text(ls: Listing) -> str:
    parts = []
    if ls.price.ok:
        parts.append(yen(ls.price.value))
    if ls.rent_monthly.ok:
        parts.append(f"月額 {yen(ls.rent_monthly.value)}")
    if parts:
        return " / ".join(parts)
    quote = ls.price.quote or ls.rent_monthly.quote
    if quote:
        # 原文の引用をそのまま出す。ただし「[無償譲渡]」のような囲みは表示崩れに見えるので外す
        return quote.strip().strip("[]［］").strip() or "—"
    return "記載なし"


def listing_row(muni: Municipality, ls: Listing) -> dict[str, Any]:
    return {
        "no": ls.listing_no,
        "title": ls.display_title,
        "deal": ls.deal_label,
        "deal_type": ls.deal_type,
        "price": price_text(ls),
        "band": price_band(ls),
        # 表示用の文字列だけだと合計や中央値が出せないので、数値もそのまま渡す
        "price_yen": ls.price.value if ls.price.ok else None,
        "rent_yen": ls.rent_monthly.value if ls.rent_monthly.ok else None,
        "address": ls.address.value if ls.address.ok else None,
        "built_year": ls.built_year.value if ls.built_year.ok else None,
        "floor_area": m2(ls.floor_area_m2.value) if ls.floor_area_m2.ok else None,
        "floor_area_m2": ls.floor_area_m2.value if ls.floor_area_m2.ok else None,
        "land_area_m2": ls.land_area_m2.value if ls.land_area_m2.ok else None,
        "layout": ls.layout.value if ls.layout.ok else None,
        "structure": ls.structure.value if ls.structure.ok else None,
        "has_detail": bool(ls.detail_url),
        "status": "closed" if ls.is_closed else ls.status,
        "status_text": ls.status_text.value if ls.status_text.ok else None,
        "url": listing_url_path(muni, ls),
        "source_url": ls.primary_url,
        "updated": date_ja(_dt(ls.last_seen_at)),
    }


def stats_of(listings: list[Listing]) -> dict[str, Any]:
    """価格・築年の代表値と価格帯ごとの件数。数値が記載されている物件だけを数える。

    無償譲渡（0 円）は最安・中央値の計算から外し、件数だけを別に持つ。0 円が最安に並ぶと
    「1 円から買える」ように見えてしまうため（鹿児島県大崎町などに実例がある）。
    """
    prices = sorted(int(ls.price.value) for ls in listings if ls.price.ok)  # type: ignore[arg-type]
    paid = [p for p in prices if p > 0]
    years = sorted(int(ls.built_year.value) for ls in listings if ls.built_year.ok)  # type: ignore[arg-type]
    counts = [0] * len(PRICE_LABELS)
    for ls in listings:
        if ls.price.ok:
            idx = next(
                (i for i, e in enumerate(PRICE_EDGES) if ls.price.value < e),  # type: ignore[operator]
                len(PRICE_EDGES),
            )
            counts[idx] += 1
    top = max(range(len(counts)), key=lambda i: counts[i]) if any(counts) else -1
    return {
        "listings": len(listings),
        "priced": len(prices),
        "paid": len(paid),
        "free": len(prices) - len(paid),  # 無償譲渡
        "price_min": paid[0] if paid else None,
        "price_median": _median(paid),
        "built_count": len(years),
        "built_min": years[0] if years else None,
        "built_max": years[-1] if years else None,
        "built_median": _median(years),
        "band_counts": counts,
        "band_labels": list(PRICE_LABELS),
        "band_top": top,
        "sale": sum(1 for ls in listings if ls.deal_type in ("sale", "both")),
        "rent": sum(1 for ls in listings if ls.deal_type in ("rent", "both")),
        "with_detail": sum(1 for ls in listings if ls.detail_url),
    }


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) // 2


def fact_rows(ls: Listing) -> list[dict[str, Any]]:
    """物件ページの項目表。値と原文（引用）を並べて出す。"""
    rows = []
    formatters = {
        "price": yen,
        "rent_monthly": yen,
        "land_area_m2": m2,
        "floor_area_m2": m2,
        "built_year": lambda v: f"{v}年",
    }
    for key, label in FIELD_LABELS.items():
        fv = getattr(ls, key)
        fmt = formatters.get(key)
        value = (fmt(fv.value) if fmt else str(fv.value)) if fv.ok else None
        rows.append(
            {
                "label": label,
                "value": value,
                "quote": fv.quote,
                "ok": fv.ok,
                "note": fv.note,
                "show_quote": bool(fv.quote) and (value is None or fv.quote != value),
            }
        )
    return rows


def operator_info(ws: Workspace) -> OperatorInfo:
    op = ws.site.operator
    fields: dict[str, Any] = {
        "name": op.name,
        "contact": op.contact,
        "url": op.url or ws.site.url("/about/"),
    }
    # 連絡先のラベルは sitemill v0.1.1（release/0.1）で入った項目。手元の path 依存は main を
    # 見ているので、cherry-pick が main に入るまでは無い版で動く必要がある
    if "contact_label" in OperatorInfo.model_fields:
        fields["contact_label"] = getattr(op, "contact_label", None)
    return OperatorInfo(**fields)


def source_links(ctx: Ctx, muni: Municipality) -> list[SourceLink]:
    src = ctx.ds.by_source.get(muni.id)
    links: list[SourceLink] = []
    if src is not None:
        for p in src.pages:
            if p.kind.value == "listing_index":
                st = ctx.ds.state.get(p.url)
                links.append(
                    SourceLink(
                        label=f"{muni.name} {muni.bank_label}",
                        url=p.url,
                        fetched_at=st.fetched_at if st else None,
                    )
                )
    if not links:
        links.append(SourceLink(label=f"{muni.name} {muni.bank_label}", url=muni.bank_url))
    return links


def trust(
    ctx: Ctx, *, sources: list[SourceLink], count: int | None, updated: datetime | None = None
) -> TrustSignals:
    return TrustSignals(
        updated_at=updated or ctx.now,
        sources=sources,
        operator=ctx.operator,
        record_count=count,
        generated_at=ctx.now,
    )


def muni_map(ctx: Ctx, muni: Municipality) -> Embed:
    query = muni.map_query or f"{muni.prefecture}{muni.name}"
    return maps_place_embed(query, api_key=ctx.maps_key, title=f"{muni.name}の地図", zoom=11)


def listing_map(ctx: Ctx, muni: Municipality, ls: Listing) -> Embed | None:
    if not ls.address.ok:
        return None
    address = str(ls.address.value)
    query = address if muni.name in address else f"{muni.prefecture}{muni.name}{address}"
    return maps_place_embed(query, api_key=ctx.maps_key, title=f"{address} 周辺の地図", zoom=14)


def price_chart(title: str, listings: list[Listing]) -> Chart | None:
    values = [float(ls.price.value) for ls in listings if ls.price.ok]  # type: ignore[arg-type]
    if len(values) < MIN_CHART_POINTS:
        return None
    return column_chart(
        title,
        bin_values(values, PRICE_EDGES, PRICE_LABELS),
        unit="件",
        desc=f"売買価格の分布（{len(values)} 件）",
    )


def built_chart(title: str, listings: list[Listing]) -> Chart | None:
    values = [float(ls.built_year.value) for ls in listings if ls.built_year.ok]  # type: ignore[arg-type]
    if len(values) < MIN_CHART_POINTS:
        return None
    return column_chart(
        title,
        bin_values(values, BUILT_EDGES, BUILT_LABELS),
        unit="件",
        desc=f"築年の分布（{len(values)} 件）",
    )


def count_chart(title: str, munis: list[Municipality], ds: Dataset) -> Chart | None:
    bars = [Bar(m.name, len(ds.listings_for(m, active_only=True))) for m in munis]
    if len(bars) < 2:
        return None
    return hbar_chart(title, bars, unit="件", desc="市町村ごとの掲載件数")


def muni_row(ctx: Ctx, muni: Municipality) -> dict[str, Any]:
    rows = visible_listings(muni, ctx.ds.listings_for(muni, active_only=True), ctx.hidden)
    return {
        "name": muni.name,
        "code": muni.code,
        "url": f"/{muni.path}",
        "bank_url": muni.bank_url,
        "bank_status": muni.bank_status,
        "crawled": muni.crawled,
        "count": len(rows),
        "sale": sum(1 for r in rows if r.deal_type in ("sale", "both")),
        "rent": sum(1 for r in rows if r.deal_type in ("rent", "both")),
        "subsidy_migration": muni.has_migration_subsidy,
        "subsidy_renovation": muni.has_renovation_subsidy,
        "subsidy_demolition": muni.has_demolition_subsidy,
        "last_fetched": datetime_ja(ctx.ds.last_fetched(muni.id)),
    }


def external_links(ctx: Ctx, muni: Municipality) -> list[dict[str, str]]:
    src = ctx.ds.by_source.get(muni.id)
    return [
        {"label": e.label, "url": e.url, "note": e.note or ""}
        for e in (src.external_links if src else [])
    ]


def website_node(ws: Workspace) -> dict[str, Any]:
    """全ページ共通の JSON-LD（WebSite + 運営組織）。物件個別の構造化データは載せない。"""
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": ws.site.name,
        "url": f"{ws.site.base_url}/",
        "description": ws.site.description,
        "inLanguage": "ja",
        "publisher": {"@type": "Organization", "name": ws.site.operator.name},
    }


def _page(
    ctx: Ctx,
    *,
    path: str,
    template: str,
    title: str,
    description: str,
    context: dict[str, Any],
    trust_signals: TrustSignals,
    priority: float = 0.5,
    changefreq: str = "weekly",
    noindex: bool = False,
    structured_data: list[dict[str, Any]] | None = None,
) -> Page:
    meta = PageMeta(
        title=title,
        description=description,
        path=path,
        priority=priority,
        changefreq=changefreq,
        noindex=noindex,
        structured_data=structured_data or [website_node(ctx.ws)],
    )
    return Page(
        meta=meta, template=template, context={**ctx.base_context, **context}, trust=trust_signals
    )


def ensure_no_street_numbers(ds: Dataset) -> None:
    """番地・号が残った所在地があれば公開しない（表示とデータの両方で保証する）。"""
    bad = [
        ls.record_id
        for ls in ds.listings
        for v in (ls.address.value, ls.address.quote)
        if v and has_street_number(str(v))
    ]
    if bad:
        raise BuildError(f"番地が残っている所在地があるためビルドを中止: {bad[:5]}")


def _municipal_phones(ws: Workspace) -> set[str]:
    """自治体運営と確認できた source の運営主体根拠（引用）に載る電話を代表電話として集める。

    運営主体の根拠引用は自治体公式ページの連絡先を写したものなので、そこに載る番号は
    自治体の代表・担当電話とみなしてホワイトリストにする（レコード本文の番号は対象外）。
    """
    from akiya_atlas.data import load_sources

    phones: set[str] = set()
    for src in load_sources(ws):
        if src.operator_kind.value not in ("municipality", "municipality_affiliated"):
            continue
        ev = src.operator_evidence
        if ev and ev.quote:
            phones.update(find_phones(ev.quote))
    return phones


def _pii_policy(ws: Workspace) -> Any:
    """自治体の代表電話・代表メールを許可するポリシー。代表電話は運営根拠と登録ファイルから。"""
    allow = _municipal_phones(ws)
    path = ws.data_dir / "reference" / "municipal_phones.json"
    if path.is_file():
        import json

        allow.update(str(x) for x in json.loads(path.read_text(encoding="utf-8")))
    return default_jp_gov_policy(allow_phones=allow)


def ensure_no_pii(ds: Dataset, ws: Workspace) -> None:
    """レコードの本文・引用に個人情報（氏名・電話・メール）が無いことを保証する。"""
    policy = _pii_policy(ws)
    findings: list[PiiFinding] = []
    for ls in ds.listings:
        texts: list[str | None] = [ls.title, ls.summary]
        for key in ("address", "structure", "layout", "status_text"):
            fv = getattr(ls, key)
            if isinstance(fv.value, str):
                texts.append(fv.value)
            texts.append(fv.quote)
        blob = " ".join(t for t in texts if t)
        findings.extend(scan_text(blob, policy=policy, where=ls.record_id))
    if findings:
        details = "; ".join(f.describe() for f in findings[:5])
        raise BuildError(f"個人情報らしき文字列がレコードに含まれるためビルドを中止: {details}")


def no_listings_reason(ctx: Ctx, muni: Municipality) -> str | None:
    """巡回対象なのに現在の掲載が 0 件のとき、その理由を区別する。

    - "empty": 一覧ページの取得に成功しているが、掲載物件が無い（＝現在掲載物件なし）。
    - "unavailable": 一覧ページを取得できていない／取得に失敗（＝取り込めていない）。
    巡回対象でない、または掲載がある場合は None。
    """
    if not muni.crawled or ctx.ds.listings_for(muni, active_only=True):
        return None
    if muni.extract_pending:
        return "unavailable"  # 一覧は確認できているが、本サイトがまだ取り込めていない
    src = ctx.ds.by_source.get(muni.id)
    fetched_ok = False
    seen_state = False
    if src is not None:
        for p in src.pages:
            if p.kind.value != "listing_index":
                continue
            st = ctx.ds.state.get(p.url)
            if st is None:
                continue
            seen_state = True
            if st.fetched_at is not None and not st.error:
                fetched_ok = True
    if not seen_state:
        return "unavailable"
    return "empty" if fetched_ok else "unavailable"


def build_pages(ws: Workspace, ds: Dataset, *, now: datetime) -> list[Page]:
    ensure_no_street_numbers(ds)
    ensure_no_pii(ds, ws)
    hidden = hidden_paths(ws)  # 取り下げ依頼のページは出さない（Issue を閉じれば次回戻る）
    ctx = Ctx(
        ws=ws,
        ds=ds,
        now=now,
        operator=operator_info(ws),
        maps_key=ws.secrets.google_maps_embed_key,
        hidden=hidden,
    )
    pages: list[Page] = []

    def _listings(muni: Municipality, *, active_only: bool = False) -> list[Listing]:
        return visible_listings(muni, ds.listings_for(muni, active_only=active_only), hidden)

    all_active = [ls for m in ds.municipalities for ls in _listings(m, active_only=True)]
    all_sources = [link for m in ds.municipalities for link in source_links(ctx, m)]

    pref_rows = []
    for slug, name in ds.prefectures():
        munis = ds.municipalities_in(slug)
        pref_rows.append(
            {
                "slug": slug,
                "name": name,
                "url": f"/{slug}/",
                "municipalities": len(munis),
                "count": sum(len(_listings(m, active_only=True)) for m in munis),
                "crawled": sum(1 for m in munis if m.crawled),
                "open": sum(1 for m in munis if _listings(m, active_only=True)),
                "subsidy_count": sum(1 for m in munis if m.has_subsidy),
            }
        )
    recent = sorted(all_active, key=lambda ls: ls.last_seen_at or "", reverse=True)[:8]
    pages.append(
        _page(
            ctx,
            path="index.html",
            template="index.html",
            title="自治体の空き家バンクを横断検索",
            description=ws.site.description,
            context={
                "prefectures": pref_rows,
                "stats": {
                    **stats_of(all_active),
                    "prefectures": len(pref_rows),
                    "municipalities": len(ds.municipalities),
                    "crawled_municipalities": sum(1 for m in ds.municipalities if m.crawled),
                    "open_municipalities": sum(
                        1 for m in ds.municipalities if _listings(m, active_only=True)
                    ),
                    "subsidy_municipalities": sum(1 for m in ds.municipalities if m.has_subsidy),
                },
                "recent": [
                    {
                        **listing_row(ds.muni_by_source[ls.source_id], ls),
                        "place": f"{ds.muni_by_source[ls.source_id].prefecture}"
                        f"{ds.muni_by_source[ls.source_id].name}",
                        "subsidy": ds.muni_by_source[ls.source_id].has_subsidy,
                    }
                    for ls in recent
                    if ls.source_id in ds.muni_by_source
                ],
                "chart_counts": count_chart("市町村別の掲載件数", ds.municipalities, ds),
            },
            trust_signals=trust(ctx, sources=all_sources, count=len(all_active)),
            priority=1.0,
            changefreq="daily",
        )
    )

    for slug, name in ds.prefectures():
        munis = ds.municipalities_in(slug)
        active = [ls for m in munis for ls in _listings(m, active_only=True)]
        pages.append(
            _page(
                ctx,
                path=f"{slug}/index.html",
                template="prefecture.html",
                title=f"{name}の空き家バンク一覧",
                description=f"{name}内の自治体が運営する空き家バンクの掲載件数・補助金の有無をまとめて確認できます。",
                context={
                    "prefecture": {"slug": slug, "name": name},
                    "municipalities": [muni_row(ctx, m) for m in munis],
                    "stats": {
                        **stats_of(active),
                        "municipalities": len(munis),
                        "crawled_municipalities": sum(1 for m in munis if m.crawled),
                        "open_municipalities": sum(
                            1 for m in munis if _listings(m, active_only=True)
                        ),
                        "subsidy_municipalities": sum(1 for m in munis if m.has_subsidy),
                    },
                    "chart_counts": count_chart(f"{name} 市町村別の掲載件数", munis, ds),
                    "chart_price": price_chart(f"{name} 売買価格の分布", active),
                    "chart_built": built_chart(f"{name} 築年の分布", active),
                },
                trust_signals=trust(
                    ctx,
                    sources=[link for m in munis for link in source_links(ctx, m)],
                    count=len(active),
                ),
                priority=0.8,
                changefreq="daily",
            )
        )

    for muni in ds.municipalities:
        listings = _listings(muni)
        active = [ls for ls in listings if ls.is_active]
        # 成約済（closed）は現行・掲載終了候補が 1 件も無いときだけ「過去の掲載」として載せる。
        # 個別ページも作らない（売却済み物件のページで索引を膨らませない）
        shown = [ls for ls in listings if not ls.is_closed] or listings
        updated_candidates = [d for d in (_dt(ls.last_seen_at) for ls in listings) if d is not None]
        fetched = ds.last_fetched(muni.id)
        if fetched is not None:
            updated_candidates.append(fetched)
        pages.append(
            _page(
                ctx,
                path=f"{muni.path}index.html",
                template="municipality.html",
                title=f"{muni.name}の空き家バンク（{muni.prefecture}）",
                description=f"{muni.prefecture}{muni.name}の空き家バンク掲載物件の要約と、移住・改修などの補助制度、一次情報へのリンク。",
                context={
                    "muni": muni,
                    "muni_row": muni_row(ctx, muni),
                    "listings": [listing_row(muni, ls) for ls in shown],
                    "stats": stats_of(active),
                    "subsidies": muni.subsidies,
                    "external": external_links(ctx, muni),
                    "map": muni_map(ctx, muni),
                    "chart_price": price_chart(f"{muni.name} 売買価格の分布", active),
                    "chart_built": built_chart(f"{muni.name} 築年の分布", active),
                    "min_points": MIN_CHART_POINTS,
                    "no_listings_reason": no_listings_reason(ctx, muni),
                },
                trust_signals=trust(
                    ctx,
                    sources=source_links(ctx, muni),
                    count=len(active),
                    updated=max(updated_candidates) if updated_candidates else None,
                ),
                priority=0.8,
                changefreq="daily",
            )
        )
        for ls in shown:
            prov = ls.provenance or {}
            pages.append(
                _page(
                    ctx,
                    path=listing_path(muni, ls),
                    template="listing.html",
                    title=f"{ls.display_title}（{muni.name} 空き家バンク {ls.listing_no}）",
                    description=(
                        ls.summary or f"{muni.name}の空き家バンク物件 {ls.listing_no} の要約。"
                    )[:150],
                    context={
                        "muni": muni,
                        "listing": ls,
                        "row": listing_row(muni, ls),
                        "facts": fact_rows(ls),
                        "map": listing_map(ctx, muni, ls),
                        "fetched_at": datetime_ja(_dt(prov.get("fetched_at"))),
                        "extractor": (prov.get("extractor") or {}).get("model"),
                        "is_stale": ls.status != "active",
                    },
                    trust_signals=trust(
                        ctx,
                        sources=[
                            SourceLink(
                                label=f"{muni.name} {muni.bank_label}（物件 {ls.listing_no}）",
                                url=ls.primary_url,
                                fetched_at=_dt(prov.get("fetched_at")),
                            )
                        ],
                        count=None,
                        updated=_dt(ls.last_seen_at),
                    ),
                    priority=0.6,
                )
            )

    subsidies = [
        {"muni": m.name, "muni_url": f"/{m.path}", **s.model_dump(mode="json")}
        for m in ds.municipalities
        for s in m.subsidies
    ]
    pages.append(
        _page(
            ctx,
            path="owners/index.html",
            template="owners.html",
            title="空き家をお持ちの方へ（売る・貸す・解体する）",
            description="空き家の売却・賃貸・解体の流れと、自治体の補助制度をまとめました。",
            context={
                "flow_sale": flow_diagram("売却・賃貸までの流れ", SALE_FLOW, per_row=3),
                "flow_demolition": flow_diagram("解体までの流れ", DEMOLITION_FLOW, per_row=3),
                "subsidies": subsidies,
                # 枠ごとの掲載案件（ADR 0010）。相談先だけは契約前のものも「準備中」として並べる
                "slots": {
                    p.id: affiliates.offers_for(p.id, include_pending=(p.id == "owners-consult"))
                    for p in affiliates.PLACEMENTS
                },
                # 広告表記の有無はこのページに実際に出る広告リンクで決める
                "ad_offers": affiliates.page_offers("/owners/"),
            },
            trust_signals=trust(ctx, sources=all_sources, count=len(subsidies)),
            priority=0.9,
        )
    )
    # 広告の転送ページ（ADR 0010）。1 枠 1 枚。noindex なので sitemap には出ない
    for target in affiliates.go_targets():
        pages.append(
            _page(
                ctx,
                path=target.url_path.strip("/") + "/index.html",
                template="go.html",
                title=f"{target.offer.label}（広告）へ移動します",
                description="広告主のサイトへ移動します。",
                context={"offer": target.offer, "placement": target.placement},
                trust_signals=trust(ctx, sources=[], count=None),
                noindex=True,
                priority=0.1,
                changefreq="monthly",
            )
        )
    pages.append(
        _page(
            ctx,
            path="about/index.html",
            template="about.html",
            title="このサイトについて・運営者情報",
            description="空き家アトラスの運営者情報、掲載方針、巡回ボットについて。",
            context={"user_agent": ws.site.user_agent},
            trust_signals=trust(ctx, sources=all_sources, count=len(all_active)),
            priority=0.3,
            changefreq="monthly",
        )
    )
    pages.append(
        _page(
            ctx,
            path="data/index.html",
            template="data.html",
            title="データについて（出典・取得日時・ライセンス）",
            description="掲載データの出典、取得日時、巡回方針、ライセンス判定の一覧。",
            context={"sources": [source_row(ctx, s) for s in ds.sources]},
            trust_signals=trust(ctx, sources=all_sources, count=len(all_active)),
            priority=0.3,
        )
    )
    pages.append(
        _page(
            ctx,
            path="404.html",
            template="404.html",
            title="ページが見つかりません",
            description="お探しのページは見つかりませんでした。",
            context={},
            trust_signals=trust(ctx, sources=all_sources, count=len(all_active)),
            priority=0.0,
            changefreq="yearly",
            noindex=True,
        )
    )
    return pages


def source_row(ctx: Ctx, src: Source) -> dict[str, Any]:
    muni = ctx.ds.muni_by_source.get(src.id)
    ev = src.operator_evidence
    return {
        "id": src.id,
        "name": src.name,
        "municipality": muni.name if muni else "",
        "municipality_url": f"/{muni.path}" if muni else None,
        "operator": src.operator,
        "operator_kind": {
            "municipality": "自治体",
            "municipality_affiliated": "自治体の関連組織",
            "third_party": "民間",
            "unknown": "不明",
        }[src.operator_kind.value],
        "evidence": {
            "quote": ev.quote,
            "url": ev.url,
            "checked_on": ev.checked_on.isoformat() if ev.checked_on else None,
        }
        if ev
        else None,
        "policy": "巡回して要約" if src.crawlable else "リンクのみ",
        "pages": [{"url": p.url, "kind": p.kind.value} for p in src.pages],
        "license": src.license.label if src.license else "未判定（画像・データの再利用はしない）",
        "last_fetched": datetime_ja(ctx.ds.last_fetched(src.id)),
        "external": [
            {"label": e.label, "url": e.url, "note": e.note or ""} for e in src.external_links
        ],
    }


def search_index(ws: Workspace, ds: Dataset) -> dict[str, Any]:
    """検索用の索引。都道府県ごとに分けて出す。

    全国分を 1 つの JSON にすると数 MB になり、検索ページを開くだけで全件を落とすことになる。
    入口（index.json）は都道府県と市町村の一覧と件数だけ、物件の行は選ばれた都道府県のファイル
    （<slug>.json）だけを読む。都道府県を選ぶ前に出す分として recent.json（最近確認した分）を置く。
    """
    hidden = hidden_paths(ws)
    by_pref: dict[str, list[dict[str, Any]]] = {}
    meta: list[dict[str, Any]] = []
    total = 0
    for slug, name in ds.prefectures():
        munis = ds.municipalities_in(slug)
        rows: list[dict[str, Any]] = []
        muni_meta: list[dict[str, Any]] = []
        for muni in munis:
            listings = visible_listings(muni, ds.listings_for(muni, active_only=True), hidden)
            rows.extend(search_row(muni, ls) for ls in listings)
            if listings:
                muni_meta.append({"code": muni.code, "name": muni.name, "count": len(listings)})
        by_pref[f"{slug}.json"] = rows
        total += len(rows)
        meta.append({"slug": slug, "name": name, "count": len(rows), "municipalities": muni_meta})
    recent = sorted(
        (r for rows in by_pref.values() for r in rows),
        key=lambda r: r.get("updated") or "",
        reverse=True,
    )[:120]
    return {
        "index.json": {"total": total, "prefectures": meta, "recent_count": len(recent)},
        "recent.json": recent,
        **by_pref,
    }


def search_row(muni: Municipality, ls: Listing) -> dict[str, Any]:
    return {
        "id": ls.record_id,
        "pref": muni.prefecture,
        "pref_slug": muni.prefecture_slug,
        "muni": muni.name,
        "muni_code": muni.code,
        "no": ls.listing_no,
        "title": ls.display_title,
        "deal": ls.deal_type,
        "deal_label": ls.deal_label,
        "price": ls.price.value if ls.price.ok else None,
        "rent": ls.rent_monthly.value if ls.rent_monthly.ok else None,
        "price_text": price_text(ls),
        "band": price_band(ls),
        "floor_area_m2": ls.floor_area_m2.value if ls.floor_area_m2.ok else None,
        "layout": ls.layout.value if ls.layout.ok else None,
        "has_detail": bool(ls.detail_url),
        "subsidy_migration": muni.has_migration_subsidy,
        "subsidy_renovation": muni.has_renovation_subsidy,
        "address": ls.address.value if ls.address.ok else None,
        "built_year": ls.built_year.value if ls.built_year.ok else None,
        "url": listing_url_path(muni, ls),
        "updated": (ls.last_seen_at or "")[:10],
    }
