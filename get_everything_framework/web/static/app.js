/* Get Everything Framework — 本机联调版最小前端脚本
 *
 * 职责：
 *   1. 轮询 /health，把 Web / 数据库 / worker / 队列状态渲染成徽标（M1）；
 *   2. 提供退出登录按钮（M1）；
 *   3. 轮询任务列表并把进度写回表格（M3）；
 *   4. 展开任务详情：步骤状态 / 错误码 / 耗时 / 结构化结果 / 原始证据（M4）。
 *
 * 约定：不使用任何前端框架、不打包、不引入 CDN 资源。
 * 所有请求都限定在本机同源，避免把 Token 发给第三方。
 */

(function () {
  "use strict";

  var HEALTH_INTERVAL_MS = 10000;
  var JOBS_INTERVAL_MS = 3000;

  var HEALTH_LABELS = {
    ok: "正常",
    missing: "未启动",
    stale: "已停止",
    error: "异常",
    unknown: "未知",
  };

  var JOB_STATUS_LABELS = {
    queued: "排队中",
    running: "执行中",
    succeeded: "已完成",
    partial: "部分成功",
    failed: "失败",
    timeout: "超时",
    cancelled: "已取消",
    interrupted: "已中断",
  };

  var STEP_STATUS_LABELS = {
    pending: "待执行",
    running: "执行中",
    succeeded: "成功",
    failed: "失败",
    timeout: "超时",
    skipped: "已跳过",
  };

  var ERROR_CODE_LABELS = {
    no_results: "执行成功但零结果（不是失败）",
    tool_not_found: "工具未安装或不在 PATH",
    permission_denied: "没有执行权限",
    invalid_target: "目标非法",
    scope_violation: "目标超出授权范围",
    timeout: "执行超时",
    parse_error: "输出解析失败",
    network_error: "网络错误",
    rate_limited: "被限流",
    partial_success: "部分成功",
    config_error: "配置指向的文件缺失（例如字典未下载）",
    unknown_error: "未知错误",
  };

  // 详情面板当前展示的任务；轮询时同步刷新它。
  var detailJobId = null;
  // 上一次渲染用过的「内容指纹」：内容没变就不重建 DOM，
  // 否则每 3 秒一次的重绘会把用户刚展开的原始证据冲掉。
  var detailSignature = "";

  function jobSignature(job) {
    return [
      job.id,
      job.status,
      job.progress,
      job.done_steps,
      job.error_code,
      job.started_at,
      job.finished_at,
      // 尝试次数与退避窗口也要进指纹：retry 之后这两者会变，若不进指纹，
      // 「排队中 → 又排了一次队」在详情页上就看不到变化。
      job.attempt,
      job.next_attempt_at,
      (job.steps || [])
        .map(function (step) {
          return step.id + ":" + step.status + ":" + step.found_count + ":" + step.error_code;
        })
        .join("|"),
    ].join("#");
  }

  function label(value) {
    return HEALTH_LABELS[value] || value || "未知";
  }

  function applyBadge(el, cssClass, text) {
    el.className = "badge " + cssClass;
    el.textContent = text;
    el.title = text;
  }

  function describe(data) {
    var parts = [
      "数据库: " + label(data.database),
      "worker: " + label(data.worker),
    ];
    if (data.queue) {
      parts.push("队列 " + data.queue.queued + " 排队 / " + data.queue.running + " 执行中");
    }
    if (data.tools_summary) {
      parts.push("工具: " + data.tools_summary.available + "/" + data.tools_summary.total);
    }
    return parts.join(" · ");
  }

  function renderHealth(data) {
    var badge = document.getElementById("health-badge");
    if (!badge) return;

    var text = describe(data);

    if (data.database === "error") {
      applyBadge(badge, "badge-error", "服务异常");
    } else if (data.worker === "ok") {
      applyBadge(badge, "badge-ok", "服务正常");
    } else {
      // Web 起来了但 worker 没跑，这是本机联调阶段最常见的状态。
      applyBadge(badge, "badge-warn", "Web 正常 · worker " + label(data.worker));
    }
    badge.title = "v" + data.version + " · " + text;
  }

  function renderHealthError() {
    var badge = document.getElementById("health-badge");
    if (!badge) return;
    applyBadge(badge, "badge-error", "健康检查不可用");
    badge.title = "无法访问 /health，请确认 Web 进程仍在运行";
  }

  function pollHealth() {
    fetch("/health", { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(renderHealth)
      .catch(renderHealthError);
  }

  function bindLogout() {
    var btn = document.getElementById("logout-btn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      fetch("/api/auth/logout", { method: "POST" })
        .then(function () {
          window.location.reload();
        })
        .catch(function () {
          window.location.reload();
        });
    });
  }

  // ── 任务列表（M3） ──────────────────────────────────────

  function renderJobRow(row, job) {
    var statusCell = row.querySelector(".status");
    if (statusCell) {
      statusCell.className = "status status-" + job.status;
      statusCell.textContent = JOB_STATUS_LABELS[job.status] || job.status;
    }

    var bar = row.querySelector(".progress span");
    if (bar) bar.style.width = job.progress + "%";

    var progressText = row.querySelector(".progress + .mono");
    if (progressText) progressText.textContent = job.progress + "%";

    // 终态任务不再需要「取消」，只有可重试的状态才显示「重试」。
    var cancelBtn = row.querySelector(".job-cancel");
    if (cancelBtn) {
      cancelBtn.disabled = ["succeeded", "partial", "failed", "timeout", "cancelled"].indexOf(job.status) !== -1;
    }
    var retryBtn = row.querySelector(".job-retry");
    if (retryBtn) {
      retryBtn.disabled = ["interrupted", "failed", "timeout", "cancelled", "partial"].indexOf(job.status) === -1;
    }
  }

  function pollJobs() {
    var table = document.getElementById("jobs-table");
    if (!table) return;

    fetch("/api/jobs?limit=10", { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (resp.status === 401) return null; // 未登录：静默停止轮询
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (!data) return;
        var byId = {};
        (data.jobs || []).forEach(function (job) {
          byId[job.id] = job;
        });
        Array.prototype.forEach.call(table.querySelectorAll("tr[data-job-id]"), function (row) {
          var job = byId[row.getAttribute("data-job-id")];
          if (job) renderJobRow(row, job);
        });
      })
      .catch(function () {
        /* 轮询失败不打断页面 */
      });
  }

  // ── 任务详情（M4：把 error_code 与原始证据暴露出来） ──────

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function errorHint(code) {
    if (!code) return null;
    var hint = ERROR_CODE_LABELS[code];
    return hint ? code + " — " + hint : code;
  }

  function renderDetail(job) {
    var panel = document.getElementById("job-detail-panel");
    var idEl = document.getElementById("job-detail-id");
    var body = document.getElementById("job-detail-body");
    if (!panel || !body) return;

    // 指纹里含 job.id，所以切换任务必然重新渲染；同一任务内容不变时则
    // 保持现有 DOM（用户刚展开的原始证据与滚动位置都留着）。
    var signature = jobSignature(job);
    if (signature === detailSignature) return;
    detailSignature = signature;

    panel.hidden = false;
    if (idEl) idEl.textContent = job.id;
    body.textContent = "";

    var meta = el("dl", "summary");
    var pairs = [
      ["状态", (JOB_STATUS_LABELS[job.status] || job.status) + "（" + job.status + "）"],
      ["模式", job.mode],
      ["进度", job.progress + "%（" + job.done_steps + "/" + job.total_steps + "）"],
      ["尝试次数", job.attempt],
      ["错误码", errorHint(job.error_code) || "—"],
      ["错误说明", job.error_message || "—"],
      ["创建", job.created_at || "—"],
      ["开始", job.started_at || "—"],
      ["结束", job.finished_at || "—"],
      ["worker", job.worker_id || "—"],
    ];
    // 退避中的任务也是 queued，光看状态会以为「马上就会跑」。把窗口显示出来，
    // 用户才能区分「排队中」与「在等退避」。
    if (job.next_attempt_at) {
      pairs.push(["最早可重试", job.next_attempt_at]);
    }
    pairs.forEach(function (pair) {
      meta.appendChild(el("dt", null, pair[0]));
      meta.appendChild(el("dd", null, pair[1]));
    });
    body.appendChild(meta);

    var targets = (job.targets || []).join(", ");
    var tools = (job.tools || []).join(", ");
    body.appendChild(el("p", "hint", "目标: " + targets + " · 工具: " + tools));

    if (!job.steps || !job.steps.length) {
      body.appendChild(el("p", "hint", "尚无步骤记录。"));
      return;
    }

    var table = el("table", "steps");
    var head = el("thead");
    var headRow = el("tr");
    ["工具", "目标", "状态", "结果数", "错误码", "耗时", "命令预览", "证据"].forEach(function (title) {
      headRow.appendChild(el("th", null, title));
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var tbody = el("tbody");
    job.steps.forEach(function (step) {
      var row = el("tr");
      row.appendChild(el("td", "mono", step.tool_name));
      row.appendChild(el("td", "mono", step.target));
      row.appendChild(el("td", null, (STEP_STATUS_LABELS[step.status] || step.status)));
      row.appendChild(el("td", "mono", step.found_count));
      row.appendChild(el("td", "mono" + (step.error_code ? " err" : ""), step.error_code || "—"));
      row.appendChild(el("td", "mono", step.duration_ms === null || step.duration_ms === undefined ? "—" : step.duration_ms + " ms"));
      row.appendChild(el("td", "mono preview", step.command_preview || "—"));

      var evidence = el("td");
      if (step.artifact_id) {
        var btn = el("button", "btn btn-ghost artifact-open", "查看");
        btn.setAttribute("data-artifact-id", step.artifact_id);
        evidence.appendChild(btn);
      } else {
        evidence.appendChild(el("span", "mono", "—"));
      }
      row.appendChild(evidence);
      tbody.appendChild(row);

      // 结构化观测：httpx 的状态码/标题/技术栈就在这里（方案 M4 交付项）。
      if (step.observations && step.observations.length) {
        var obsRow = el("tr", "observations");
        var cell = el("td");
        cell.colSpan = 8;
        step.observations.slice(0, 50).forEach(function (obs) {
          var line = el("div", "mono");
          line.textContent = obs.value + (Object.keys(obs.data || {}).length ? "  " + JSON.stringify(obs.data) : "");
          cell.appendChild(line);
        });
        if (step.observations.length > 50) {
          cell.appendChild(el("div", "hint", "（仅显示前 50 条，共 " + step.observations.length + " 条）"));
        }
        obsRow.appendChild(cell);
        tbody.appendChild(obsRow);
      }

      if (step.error_message) {
        var msgRow = el("tr", "step-message");
        var msgCell = el("td", "hint");
        msgCell.colSpan = 8;
        msgCell.textContent = step.error_message;
        msgRow.appendChild(msgCell);
        tbody.appendChild(msgRow);
      }
    });
    table.appendChild(tbody);
    body.appendChild(table);

    loadArtifacts(job.id);
  }

  function loadArtifacts(jobId) {
    var body = document.getElementById("job-detail-body");
    if (!body) return;
    fetch("/api/jobs/" + encodeURIComponent(jobId) + "/artifacts", { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        var artifacts = data.artifacts || [];
        if (!artifacts.length) return;
        body.appendChild(el("h3", null, "原始证据"));
        var list = el("ul", "artifacts");
        artifacts.forEach(function (artifact) {
          var item = el("li");
          item.appendChild(el("span", "mono", artifact.kind + " · " + artifact.size + " B"));
          if (artifact.truncated) item.appendChild(el("span", "hint", "（落盘时已截断）"));
          var btn = el("button", "btn btn-ghost artifact-open", "查看内容");
          btn.setAttribute("data-artifact-id", artifact.id);
          item.appendChild(btn);
          list.appendChild(item);
        });
        body.appendChild(list);
        body.appendChild(el("pre", "code-block artifact-view", ""));
      })
      .catch(function () {
        /* 证据读取失败不影响任务详情 */
      });
  }

  function openArtifact(artifactId) {
    var box = document.querySelector(".artifact-view");
    if (!box) return;
    box.textContent = "读取中…";
    fetch("/api/artifacts/" + encodeURIComponent(artifactId), { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        var artifact = data.artifact || {};
        box.textContent =
          "# " + artifact.kind + " · " + artifact.tool_name + " · " + artifact.size + " B" +
          (artifact.truncated ? "（内容已截断）" : "") +
          "\n# 已脱敏；本地路径不下发\n\n" +
          (artifact.text || "（空）");
      })
      .catch(function (err) {
        box.textContent = "读取失败: " + err.message;
      });
  }

  function refreshDetail() {
    if (!detailJobId) return;
    fetch("/api/jobs/" + encodeURIComponent(detailJobId), { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (data.job) renderDetail(data.job);
      })
      .catch(function () {
        /* 忽略：下一轮还会再试 */
      });
  }

  function bindJobActions() {
    var table = document.getElementById("jobs-table");
    var body = document.getElementById("job-detail-body");
    if (!table) return;

    table.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || !target.classList) return;

      var jobId;
      if (target.classList.contains("job-cancel")) {
        jobId = target.getAttribute("data-job-id");
        target.disabled = true;
        fetch("/api/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST" })
          .then(pollJobs)
          .catch(pollJobs);
      } else if (target.classList.contains("job-retry")) {
        jobId = target.getAttribute("data-job-id");
        target.disabled = true;
        fetch("/api/jobs/" + encodeURIComponent(jobId) + "/retry", { method: "POST" })
          .then(pollJobs)
          .catch(pollJobs);
      } else if (target.classList.contains("job-detail")) {
        detailJobId = target.getAttribute("data-job-id");
        refreshDetail();
      }
    });

    // 证据按钮是动态生成的，统一在详情面板上用事件委托。
    if (body) {
      body.addEventListener("click", function (event) {
        var target = event.target;
        if (target && target.classList && target.classList.contains("artifact-open")) {
          openArtifact(target.getAttribute("data-artifact-id"));
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    pollHealth();
    window.setInterval(pollHealth, HEALTH_INTERVAL_MS);
    bindLogout();
    bindJobActions();
    pollJobs();
    window.setInterval(pollJobs, JOBS_INTERVAL_MS);
    window.setInterval(refreshDetail, JOBS_INTERVAL_MS);
  });

  // 扫描中心（授权公网测试模式，体验版方案第 7 节）在自己的脚本里实现，
  // 但它复用的是本文件里的状态标签与错误码文案 —— 这两张表必须只有一份，
  // 否则同一个 error_code 在两个页面会显示成不同的话。
  window.GEF_UI = {
    JOB_STATUS_LABELS: JOB_STATUS_LABELS,
    STEP_STATUS_LABELS: STEP_STATUS_LABELS,
    ERROR_CODE_LABELS: ERROR_CODE_LABELS,
    jobSignature: jobSignature,
  };
})();
