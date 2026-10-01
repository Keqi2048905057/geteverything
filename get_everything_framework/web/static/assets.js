/* Get Everything Framework — 资产列表页（P1，方案第 8 节）
 *
 * 职责：
 *   1. 调 /api/assets 渲染资产表（类型 / 值 / 状态 / first_seen / last_seen / 置信度）；
 *   2. 点「详情」调 /api/assets/<id> 展开观测时间线 ——
 *      回答「谁发现的（source_tool/job_id）/ 何时发现（observed_at）/ 当时的属性（data）」；
 *   3. 分页与筛选（scope_id / type / status / search）；
 *   4. 两次任务对比：调 /api/jobs/<a>/diff/<b> 渲染 added / removed / changed / unchanged。
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
        if (data.asset) {
          renderDetail(data.asset);
          // 从 diff 清单点进来时，详情面板在页面另一头：不滚过去就等于没反应。
          var panel = document.getElementById("asset-detail-panel");
          if (panel && panel.scrollIntoView) panel.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      })
      .catch(function (err) {
        var body = document.getElementById("asset-detail-body");
        if (body) {
          body.textContent = "";
          body.appendChild(el("p", "hint", "读取失败: " + err.message));
        }
      });
  }

  // ── 两次任务对比（方案第 10 节 Diff Engine） ─────────────

  //: diff 四类的显示顺序与文案。数组顺序即渲染顺序 ——
  //: 「新增 / 消失」比「未变」重要，所以未变永远排在最后。
  var DIFF_SECTIONS = [
    ["added", "新增", "上次没有、这次有"],
    ["removed", "消失", "上次有、这次没有"],
    ["changed", "变更", "两次都有，但可变属性不一样"],
    ["unchanged", "未变", "两次都有且属性一致"],
  ];

  //: 属性 canonical key → 中文显示名。键名由服务端 core/assets.py 的
  //: ATTRIBUTE_ALIASES 归一化后给出（server/webserver/web_server 都归到
  //: webserver，tech/technology/technologies 都归到 technologies），
  //: 这里只负责把 canonical key 翻成人话，不做任何判断。
  var ATTRIBUTE_LABELS = {
    status_code: "状态码",
    title: "标题",
    webserver: "Web Server",
    technologies: "技术栈",
    url: "URL",
  };

  function fillJobOptions(jobs) {
    var selects = ["diff-before", "diff-after"];
    selects.forEach(function (id) {
      var select = document.getElementById(id);
      if (!select) return;
      // 保留第一项「（选一个）」占位。
      while (select.options.length > 1) select.remove(1);
      jobs.forEach(function (job) {
        var option = document.createElement("option");
        option.value = job.id;
        option.textContent = job.id + " · " + (job.status || "") + " · " + (job.created_at || "");
        select.appendChild(option);
      });
    });

    // 默认选中「最近两次」，让用户不必先手动挑。
    var before = document.getElementById("diff-before");
    var after = document.getElementById("diff-after");
    if (before && after && jobs.length >= 2) {
      before.value = jobs[1].id;
      after.value = jobs[0].id;
    }
  }

  function loadJobOptions() {
    var before = document.getElementById("diff-before");
    if (!before) return;
    fetch("/api/jobs?limit=50", { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (resp.status === 401) return null;
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        var summary = document.getElementById("diff-summary");
        if (!data) {
          if (summary) summary.textContent = "未登录：任务列表仅本地管理员可见，登录后才能对比。";
          return;
        }
        fillJobOptions(data.jobs || []);
      })
      .catch(function (err) {
        var summary = document.getElementById("diff-summary");
        if (summary) summary.textContent = "任务列表读取失败: " + err.message;
      });
  }

  function diffItemLine(item) {
    var text = typeLabel(item.type) + " " + item.value;
    if (item.changes && Object.keys(item.changes).length) {
      var parts = Object.keys(item.changes).map(function (attribute) {
        var change = item.changes[attribute] || {};
        var label = ATTRIBUTE_LABELS[attribute] || attribute;
        return label + ": " + JSON.stringify(change.from) + " → " + JSON.stringify(change.to);
      });
      text += "（" + parts.join("；") + "）";
    }
    return text;
  }

  function renderDiff(data) {
    var body = document.getElementById("diff-body");
    var summary = document.getElementById("diff-summary");
    if (!body) return;

    body.textContent = "";
    var counts = data.counts || {};

    if (summary) {
      summary.textContent =
        "基线 " + data.before_job_id + " → 对比 " + data.after_job_id +
        "：新增 " + (counts.added || 0) +
        " · 消失 " + (counts.removed || 0) +
        " · 变更 " + (counts.changed || 0) +
        " · 未变 " + (counts.unchanged || 0);
    }

    DIFF_SECTIONS.forEach(function (section) {
      var key = section[0];
      var items = data[key] || [];
      body.appendChild(el("h3", null, section[1] + "（" + items.length + "）— " + section[2]));

      if (!items.length) {
        // 服务端在 include_unchanged=0 时只给计数、不给明细：
        // 说清这是「没要」而不是「没有」，否则用户会以为两次任务完全一致。
        body.appendChild(
          el("p", "hint", (counts[key] || 0) > 0 ? "（按设置未取明细，共 " + counts[key] + " 条）" : "无。")
        );
        return;
      }
      var list = el("ul", "diff-list");
      items.forEach(function (item) {
        var node = el("li", "mono", diffItemLine(item));
        var assetId = item.asset_id || "";
        node.setAttribute("data-asset-id", assetId);
        if (assetId) {
          // 有 asset_id 才能点进详情：让它看起来可点，不然用户不会去试。
          node.classList.add("diff-item-clickable");
          node.title = "点击查看该资产的观测时间线";
        }
        list.appendChild(node);
      });
      body.appendChild(list);
    });
  }

  function runDiff() {
    var before = filterValue("diff-before");
    var after = filterValue("diff-after");
    var summary = document.getElementById("diff-summary");

    if (!before || !after) {
      if (summary) summary.textContent = "请先各选一个任务。";
      return;
    }
    if (before === after) {
      if (summary) summary.textContent = "基线与对比是同一个任务，换一个再比。";
      return;
    }

    var params = [];
    var scopeId = filterValue("diff-scope");
    if (scopeId) params.push("scope_id=" + encodeURIComponent(scopeId));
    var includeUnchanged = document.getElementById("diff-include-unchanged");
    if (includeUnchanged && !includeUnchanged.checked) params.push("include_unchanged=0");
    var query = params.length ? "?" + params.join("&") : "";

    if (summary) summary.textContent = "对比中…";
    fetch(
      "/api/jobs/" + encodeURIComponent(before) + "/diff/" + encodeURIComponent(after) + query,
      { headers: { Accept: "application/json" } }
    )
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        renderDiff(data);
      })
      .catch(function (err) {
        if (summary) summary.textContent = "对比失败: " + err.message;
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

    var diffForm = document.getElementById("diff-form");
    if (diffForm) {
      diffForm.addEventListener("submit", function (event) {
        event.preventDefault();
        runDiff();
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

    // diff 清单里的条目同样可以点进资产详情（新增/消失/变更/未变四类都算）。
    var diffBody = document.getElementById("diff-body");
    if (diffBody) {
      diffBody.addEventListener("click", function (event) {
        var node = event.target;
        while (node && node !== diffBody && !(node.classList && node.classList.contains("diff-item-clickable"))) {
          node = node.parentNode;
        }
        if (node && node !== diffBody) openDetail(node.getAttribute("data-asset-id"));
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
    loadJobOptions();
  });
})();
