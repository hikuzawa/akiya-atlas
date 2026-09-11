// 静的 JSON 索引をクライアント側で絞り込む検索（都道府県 × 市町村 × 価格帯 × 種別 × 補助金の有無）。
// 索引は都道府県ごとに分かれている。最初は入口（件数と市町村名だけ）と「最近確認した分」を読み、
// 都道府県が選ばれたらその県のファイルだけを追加で読む。依存ライブラリなし。
(function () {
  "use strict";
  var form = document.querySelector("[data-search-form]");
  var status = document.querySelector("[data-search-status]");
  var list = document.querySelector("[data-search-results]");
  var moreWrap = document.querySelector("[data-search-more]");
  var moreBtn = moreWrap ? moreWrap.querySelector("button") : null;
  if (!form || !status || !list) return;

  var meta = null; // {total, prefectures:[{slug,name,count,municipalities:[{code,name,count}]}]}
  var cache = {}; // slug -> 物件の行
  var rows = []; // いま絞り込みの対象にしている行
  var scope = ""; // "" なら最近確認した分、県スラッグならその県
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
    if (!select) return;
    var pref = form.querySelector('[data-filter="pref_slug"]').value;
    var options = ['<option value="">すべて</option>'];
    if (meta && pref) {
      var hit = meta.prefectures.filter(function (p) { return p.slug === pref; })[0];
      (hit ? hit.municipalities : []).forEach(function (m) {
        options.push('<option value="' + esc(m.code) + '">' + esc(m.name) + "（" + m.count + "）</option>");
      });
    }
    var current = select.value;
    select.innerHTML = options.join("");
    select.disabled = !pref;
    if (pref) select.value = current;
  }

  function matches(r) {
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
    if (r.floor_area_m2) facts.push("<li>" + icon("area") + "延床 " + esc(r.floor_area_m2) + "㎡</li>");
    if (r.built_year) facts.push("<li>" + icon("calendar") + esc(r.built_year) + "年築</li>");
    var badges = [];
    if (r.has_detail) badges.push('<span class="badge badge-info">' + icon("check") + "詳細あり</span>");
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

  function scopeNote() {
    if (scope) {
      var hit = meta.prefectures.filter(function (p) { return p.slug === scope; })[0];
      return hit ? hit.name + "の全 " + hit.count + " 件から" : "";
    }
    return "最近確認した " + rows.length + " 件から（都道府県を選ぶと全 " + (meta ? meta.total : 0) + " 件から探せます）";
  }

  function render() {
    readFilters();
    hits = rows.filter(matches);
    shown = 0;
    list.innerHTML = "";
    status.textContent = hits.length + " 件が該当。" + scopeNote();
    renderMore();
  }

  function load(url) {
    return fetch(url, { cache: "no-cache" }).then(function (res) {
      if (!res.ok) throw new Error(res.status);
      return res.json();
    });
  }

  function useScope(slug) {
    scope = slug;
    var file = slug ? "/search/" + slug + ".json" : "/search/recent.json";
    if (cache[file]) {
      rows = cache[file];
      render();
      return Promise.resolve();
    }
    status.textContent = "読み込み中…";
    return load(file).then(function (data) {
      cache[file] = data;
      rows = data;
      render();
    });
  }

  load("/search/index.json")
    .then(function (data) {
      meta = data;
      fillMunicipalities();
      return useScope("");
    })
    .then(function () {
      form.addEventListener("change", function (e) {
        var key = e.target.getAttribute("data-filter");
        if (key === "pref_slug") {
          fillMunicipalities();
          useScope(e.target.value).catch(showError);
          return;
        }
        render();
      });
      if (moreBtn) moreBtn.addEventListener("click", renderMore);
    })
    .catch(showError);

  function showError(err) {
    status.textContent = "索引を読み込めませんでした（" + err.message + "）。都道府県ページから一覧をご覧ください。";
  }
})();
