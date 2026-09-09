// 静的 JSON 索引をクライアント側で絞り込む検索（都道府県 × 市町村 × 価格帯 × 補助金の有無）。
(function () {
  "use strict";
  var form = document.querySelector("[data-search-form]");
  var status = document.querySelector("[data-search-status]");
  var list = document.querySelector("[data-search-results]");
  if (!form || !status || !list) return;

  var rows = [];
  var filters = {};
  var MAX = 100;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
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

  function render() {
    readFilters();
    var hits = rows.filter(matches);
    status.textContent = hits.length + " 件が該当（" + rows.length + " 件中）" + (hits.length > MAX ? "。先頭 " + MAX + " 件を表示" : "");
    list.innerHTML = hits.slice(0, MAX).map(function (r) {
      var badges = [];
      if (r.subsidy_migration) badges.push("移住補助");
      if (r.subsidy_renovation) badges.push("改修補助");
      return (
        "<li><a href=\"" + esc(r.url) + "\">" + esc(r.title) + "</a>" +
        '<div class="meta">' + esc(r.pref) + " " + esc(r.muni) + " ／ " + esc(r.deal_label) + " ／ " + esc(r.price_text) +
        (r.built_year ? " ／ " + esc(r.built_year) + "年築" : "") +
        (badges.length ? " ／ " + badges.join("・") : "") +
        (r.updated ? " ／ 確認 " + esc(r.updated) : "") + "</div></li>"
      );
    }).join("");
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
    })
    .catch(function (err) {
      status.textContent = "索引を読み込めませんでした（" + err.message + "）。都道府県ページから一覧をご覧ください。";
    });
})();
