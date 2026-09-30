/* Get Everything Framework — 资产列表页（P1，方案第 8 节）
 *
 * 职责：
 *   1. 调 /api/assets 渲染资产表（类型 / 值 / 状态 / first_seen / last_seen / 置信度）；
 *   2. 点「详情」调 /api/assets/<id> 展开观测时间线 ——
 *      回答「谁发现的（source_tool/job_id）/ 何时发现（observed_at）/ 当时的属性（data）」；
 *   3. 分页与筛选（scope_id / type / status / search）。
 *
 * 约定：不使用任何前端框架、不打包、不引入 CDN 资源；
 * 所有请求都限定在本机同源。响应里没有服务器路径，本文件也不拼接任何路径。
 */

(function () {
  "use strict";

  var PAGE_SIZE = 50;

  var TYPE_LABELS = {
    subdomain: "子域",
    host: "主机",
    ip: "IP",
    cidr: "网段",
    url: "URL",
    port: "端口",
    service: "服务",
  };

  var STATUS_LABELS = {
    active: "活跃",
    stale: "陈旧",
    gone: "已消失",
  };

  // 当前分页状态
  var offset = 0;
  var total = 0;
  var detailAssetId = null;
  var detailSignature = "";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function typeLabel(value) {
    return TYPE_LABELS[value] || value || "—";
  }

  function statusLabel(value) {
    return STATUS_LABELS[value] || value || "—";
  }

  function filterValue(id) {
    var node = document.getElementById(id);
    return node ? node.value.trim() : "";
  }

  function buildQuery(withPaging) {
    var params = [];
    var scopeId = filterValue("filter-scope");
    var assetType = filterValue("filter-type");
    var status = filterValue("filter-status");
    var search = filterValue("filter-search");

    if (scopeId) params.push("scope_id=" + encodeURIComponent(scopeId));
    if (assetType) params.push("type=" + encodeURIComponent(assetType));
    if (status) params.push("status=" + encodeURIComponent(status));
    if (search) params.push("search=" + encodeURIComponent(search));
    if (withPaging) {
      params.push("limit=" + PAGE_SIZE);
      params.push("offset=" + offset);
    }
    return params.length ? "?" + params.join("&") : "";
  }

  function renderRows(assets) {
    var table = document.getElementById("assets-table");
    var empty = document.getElementById("assets-empty");
    if (!table) return;

    var tbody = table.querySelector("tbody");
    tbody.textContent = "";

    if (!assets.length) {
      table.hidden = true;
      if (empty) empty.hidden = false;
      return;
    }
    table.hidden = false;
    if (empty) empty.hidden = true;

    assets.forEach(function (asset) {
      var row = el("tr");
      row.setAttribute("data-asset-id", asset.id);
      row.appendChild(el("td", null, typeLabel(asset.type)));
      row.appendChild(el("td", "mono", asset.value));
      row.appendChild(el("td", null, statusLabel(asset.status)));
      row.appendChild(el("td", "mono", asset.first_seen || "—"));
      row.appendChild(el("td", "mono", asset.last_seen || "—"));
      row.appendChild(el("td", "mono", asset.confidence || "—"));

      var actions = el("td");
      var btn = el("button", "btn btn-ghost asset-detail", "详情");
      btn.setAttribute("data-asset-id", asset.id);
      actions.appendChild(btn);
      row.appendChild(actions);
      tbody.appendChild(row);
    });
  }

  function renderSummary(data) {
    var node = document.getElementById("assets-summary");
    if (!node) return;
    var byType = data.by_type || {};
    var parts = Object.keys(byType).map(function (key) {
      return typeLabel(key) + " " + byType[key];
    });
    var text = "共 " + (data.total || 0) + " 条资产";
    if (parts.length) text += " · " + parts.join(" · ");
    if (data.counts) {
      text += " · 显示第 " + ((data.offset || 0) + 1) + "–" +
        ((data.offset || 0) + (data.assets || []).length) + " 条";
    }
    node.textContent = text;
  }

  function updatePaging(returnedCount) {
    var prev = document.getElementById("assets-prev");
    var next = document.getElementById("assets-next");
    if (prev) {
      prev.hidden = offset <= 0;
      prev.disabled = offset <= 0;
    }
    if (next) {
      var hasMore = offset + returnedCount < total;
      next.hidden = !hasMore;
      next.disabled = !hasMore;
    }
  }

  function loadAssets() {
    var table = document.getElementById("assets-table");
    if (!table) return;
    if (!document.getElementById("assets-filter")) return;

    fetch("/api/assets" + buildQuery(true), { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (resp.status === 401) {
          var node = document.getElementById("assets-summary");
          if (node) node.textContent = "未登录：资产内容仅本地管理员可见。";
          return null;
        }
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (!data) return;
        total = data.total || 0;
        renderRows(data.assets || []);
        renderSummary(data);
        updatePaging((data.assets || []).length);
      })
      .catch(function (err) {
        var node = document.getElementById("assets-summary");
        if (node) node.textContent = "加载失败: " + err.message;
      });
  }

  // ── 资产详情：观测时间线 ────────────────────────────────

  function assetSignature(asset) {
    return [
      asset.id,
      asset.status,
      asset.last_seen,
      (asset.observations || [])
        .map(function (obs) {
          return obs.id + ":" + obs.observed_at + ":" + obs.source_tool;
        })
        .join("|"),
    ].join("#");
  }

  function renderDetail(asset) {
    var panel = document.getElementById("asset-detail-panel");
    var idEl = document.getElementById("asset-detail-id");
    var body = document.getElementById("asset-detail-body");
    if (!panel || !body) return;

    var signature = assetSignature(asset);
    if (signature === detailSignature) return;
    detailSignature = signature;

    panel.hidden = false;
    if (idEl) idEl.textContent = asset.id;
    body.textContent = "";

    var meta = el("dl", "summary");
    [
      ["类型", typeLabel(asset.type) + "（" + asset.type + "）"],
      ["值", asset.value],
      ["规范化键", asset.canonical_key],
      ["状态", statusLabel(asset.status) + "（" + asset.status + "）"],
      ["首次发现", asset.first_seen || "—"],
      ["最近发现", asset.last_seen || "—"],
      ["所属范围", asset.scope_id || "（未限定）"],
      ["置信度", asset.confidence || "—"],
    ].forEach(function (pair) {
      meta.appendChild(el("dt", null, pair[0]));
      meta.appendChild(el("dd", null, pair[1]));
    });
    body.appendChild(meta);

    var metadataKeys = Object.keys(asset.metadata || {});
    if (metadataKeys.length) {
      body.appendChild(el("h3", null, "元数据"));
      var pre = el("pre", "code-block", JSON.stringify(asset.metadata, null, 2));
      body.appendChild(pre);
    }

    var observations = asset.observations || [];
    body.appendChild(el("h3", null, "观测时间线（" + observations.length + " 条）"));
    if (!observations.length) {
      body.appendChild(el("p", "hint", "尚无观测记录。"));
      return;
    }

    var table = el("table", "steps");
    var head = el("thead");
    var headRow = el("tr");
    ["观测时间", "来源工具", "任务", "步骤", "解析版本", "原始证据", "属性"].forEach(function (title) {
      headRow.appendChild(el("th", null, title));
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var tbody = el("tbody");
    observations.forEach(function (obs) {
      var row = el("tr");
      row.appendChild(el("td", "mono", obs.observed_at || "—"));
      row.appendChild(el("td", "mono", obs.source_tool || "—"));
      row.appendChild(el("td", "mono", obs.job_id || "—"));
      row.appendChild(el("td", "mono", obs.step_id || "—"));
      row.appendChild(el("td", "mono", obs.parser_version || "—"));
      row.appendChild(el("td", "mono preview", obs.raw_artifact_id || "—"));
      var keys = Object.keys(obs.data || {});
      row.appendChild(el("td", "mono preview", keys.length ? JSON.stringify(obs.data) : "—"));
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    body.appendChild(table);
  }

  function openDetail(assetId) {
    detailAssetId = assetId;
    fetch("/api/assets/" + encodeURIComponent(assetId), { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (data.asset) renderDetail(data.asset);
      })
      .catch(function (err) {
        var body = document.getElementById("asset-detail-body");
        if (body) {
          body.textContent = "";
          body.appendChild(el("p", "hint", "读取失败: " + err.message));
        }
      });
  }

  function bind() {
    var form = document.getElementById("assets-filter");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        offset = 0;
        loadAssets();
      });
    }

    var table = document.getElementById("assets-table");
    if (table) {
      table.addEventListener("click", function (event) {
        var target = event.target;
        if (target && target.classList && target.classList.contains("asset-detail")) {
          openDetail(target.getAttribute("data-asset-id"));
        }
      });
    }

    var prev = document.getElementById("assets-prev");
    if (prev) {
      prev.addEventListener("click", function () {
        offset = Math.max(0, offset - PAGE_SIZE);
        loadAssets();
      });
    }
    var next = document.getElementById("assets-next");
    if (next) {
      next.addEventListener("click", function () {
        offset = offset + PAGE_SIZE;
        loadAssets();
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    bind();
    loadAssets();
  });
})();
