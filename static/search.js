// 静的 JSON 索引をクライアント側で絞り込む検索（都道府県 × 市町村 × 価格帯 × 種別 × 補助金の有無）。
// 結果は物件カードとして描画し、20 件ずつ「さらに表示」で増やす。依存ライブラリなし。
(function () {
  "use strict";
  var form = document.querySelector("[data-search-form]");
  var status = document.querySelector("[data-search-status]");
  var list = document.querySelector("[data-search-results]");
  var moreWrap = document.querySelector("[data-search-more]");
  var moreBtn = moreWrap ? moreWrap.querySelector("button") : null;
  if (!form || !status || !list) return;

  var rows = [];
  var hits = [];
  var shown = 0;
  var filters = {};
  var PAGE = 20;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function icon(name) {
    return '<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-' + name + '"/></svg>';
  }

  function readFilters() {
    filters = {};
    form.querySelectorAll("[data-filter]").forEach(function (el) {
      var key = el.getAttribute("data-filter");
      filters[key] = el.type === "checkbox" ? el.checked : el.value;
    });
  }

  function fillMunicipalities() {
    var select = form.querySelector('[data-filter="muni_code"]');
    var pref = form.querySelector('[data-filter="pref_slug"]').value;
    var seen = {};
    var options = ['<option value="">すべて</option>'];
    rows.forEach(function (r) {
      if (pref && r.pref_slug !== pref) return;
      if (seen[r.muni_code]) return;
      seen[r.muni_code] = true;
      options.push('<option value="' + esc(r.muni_code) + '">' + esc(r.muni) + "</option>");
    });
    var current = select.value;
    select.innerHTML = options.join("");
    if (seen[current]) select.value = current;
  }

  function matches(r) {
    if (filters.pref_slug && r.pref_slug !== filters.pref_slug) return false;
    if (filters.muni_code && r.muni_code !== filters.muni_code) return false;
    if (filters.band && r.band !== filters.band) return false;
    if (filters.deal && r.deal !== filters.deal) return false;
    if (filters.subsidy && !(r.subsidy_migration || r.subsidy_renovation)) return false;
    return true;
  }

  function card(r) {
    var facts = [];
    facts.push("<li>" + icon("map") + esc(r.pref) + " " + esc(r.muni) + "</li>");
    if (r.address) facts.push("<li>" + icon("pin") + esc(r.address) + "</li>");
    if (r.built_year) facts.push("<li>" + icon("calendar") + esc(r.built_year) + "年築</li>");
    var badges = [];
    if (r.subsidy_migration) badges.push('<span class="badge badge-ok">' + icon("tag") + "移住補助</span>");
    if (r.subsidy_renovation) badges.push('<span class="badge badge-ok">' + icon("tag") + "改修補助</span>");
    var price = r.price_text === "記載なし"
      ? '<div class="card-price none">価格 記載なし<span class="deal">' + esc(r.deal_label) + "</span></div>"
      : '<div class="card-price">' + esc(r.price_text) + '<span class="deal">' + esc(r.deal_label) + "</span></div>";
    return (
      '<li class="card">' + price +
      '<div class="card-title"><a href="' + esc(r.url) + '">' + esc(r.title) + "</a></div>" +
      '<ul class="card-facts">' + facts.join("") + "</ul>" +
      '<div class="card-foot"><div class="badges">' + badges.join("") + "</div>" +
      (r.updated ? "<span>確認 " + esc(r.updated) + "</span>" : "") + "</div></li>"
    );
  }

  function renderMore() {
    var next = hits.slice(shown, shown + PAGE);
    list.insertAdjacentHTML("beforeend", next.map(card).join(""));
    shown += next.length;
    if (moreWrap) {
      moreWrap.hidden = shown >= hits.length;
      if (moreBtn) moreBtn.textContent = "さらに表示（残り " + (hits.length - shown) + " 件）";
    }
  }

  function render() {
    readFilters();
    hits = rows.filter(matches);
    shown = 0;
    list.innerHTML = "";
    status.textContent = hits.length + " 件が該当（全 " + rows.length + " 件）";
    renderMore();
  }

  fetch("/search/index.json", { cache: "no-cache" })
    .then(function (res) { if (!res.ok) throw new Error(res.status); return res.json(); })
    .then(function (data) {
      rows = data;
      fillMunicipalities();
      render();
      form.addEventListener("change", function (e) {
        if (e.target.getAttribute("data-filter") === "pref_slug") fillMunicipalities();
        render();
      });
      if (moreBtn) moreBtn.addEventListener("click", renderMore);
    })
    .catch(function (err) {
      status.textContent = "索引を読み込めませんでした（" + err.message + "）。都道府県ページから一覧をご覧ください。";
    });
})();
