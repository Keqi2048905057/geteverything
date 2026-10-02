/* Get Everything Framework — 扫描中心前端（授权公网测试模式体验版方案第 7 节）
 *
 * 职责（对应方案的三个区块）：
 *   1. 「项目」  —— 创建授权测试项目，并把已存在的 Scope 关联进去；
 *   2. 「创建任务」—— 选目标 / 选模式 / 选工具，然后调 POST /api/public-jobs；
 *   3. 「任务列表」—— 复用 app.js 的轮询与详情渲染（本文件不重复实现）。
 *
 * 安全约定：
 *   * 页面上的每一个提交动作都只是**转发**到 API，闸门全部在服务端
 *     （项目 → 项目内 Scope → 公网工具白名单 → Policy / Scope / 模式）。
 *     前端不做、也不能做「能不能扫」的判断；
 *   * 被禁用的工具在界面上置灰，这是**可用性**提示而不是安全边界 ——
 *     即便有人绕过 DOM 直接发请求，后端一样会 400。
 *   * 不使用任何前端框架、不引入 CDN 资源。
 */

(function () {
  "use strict";

  var ACTIVE_STRATEGY = "asset_discovery";
  var metadata = null;

  function $(id) {
    return document.getElementById(id);
  }

  function setText(id, text, isError) {
    var box = $(id);
    if (!box) return;
    box.textContent = text;
    box.className = isError ? "alert alert-error" : "sc-step-note";
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    }).then(function (resp) {
      return resp.json().catch(function () {
        return {};
      }).then(function (data) {
        return { status: resp.status, ok: resp.ok, data: data };
      });
    });
  }

  // 服务端统一的错误体形状：error_code + error_message（+ details）。
  // 把 details 里的字段级原因也读出来，否则用户只知道「被拒了」不知道为什么。
  function describeError(result) {
    var data = result.data || {};
    var message = data.error_message || ("HTTP " + result.status);
    var details = data.details || {};

    if (details.blocked_tools && details.blocked_tools.length) {
      var names = details.blocked_tools.map(function (item) {
        return item.tool_name + "（" + (item.reason || item.risk_level) + "）";
      });
      message += "；被禁工具: " + names.join("、");
    }
    if (details.unknown_tools && details.unknown_tools.length) {
      message += "；未登记工具: " + details.unknown_tools.join("、");
    }
    if (details.attached_scope_ids) {
      message += "；该项目当前已关联: " + (details.attached_scope_ids.join("、") || "（无）");
    }
    return message;
  }

  function riskBadge(tool) {
    var span = document.createElement("span");
    span.className = "risk risk-" + tool.risk_level;
    span.textContent = tool.risk_level;
    span.title = tool.risk_label || tool.risk_level;
    return span;
  }

  // ── 元数据（项目 / 策略 / 工具权限） ─────────────────────

  function loadMetadata() {
    return fetch("/api/scan-center", { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (resp.status === 401) return null; // 未登录：页面已有提示，静默
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (!data) return;
        metadata = data;
        renderStrategies(data.strategies, data.tools, data.restricted_tools);
        renderToolList(data.tools, data.restricted_tools);
        renderProjects(data.projects);
        fillProjectSelects(data.projects);
      })
      .catch(function () {
        setText("project-feedback", "扫描中心元数据读取失败，请刷新页面重试。", true);
      });
  }

  function renderStrategies(strategies, tools, restrictedTools) {
    var box = $("strategy-list");
    if (!box) return;
    box.textContent = "";

    strategies.forEach(function (strategy) {
      var label = document.createElement("label");
      label.className = "sc-strategy" + (strategy.key === ACTIVE_STRATEGY ? " is-selected" : "");
      label.setAttribute("data-strategy", strategy.key);

      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "strategy";
      radio.value = strategy.key;
      radio.checked = strategy.key === ACTIVE_STRATEGY;
      label.appendChild(radio);

      var name = document.createElement("span");
      name.className = "sc-strategy-name";
      name.textContent = strategy.name;
      label.appendChild(name);

      var desc = document.createElement("span");
      desc.className = "sc-strategy-desc";
      desc.textContent = strategy.description;
      label.appendChild(desc);

      var toolLine = document.createElement("span");
      toolLine.className = "sc-strategy-tools";
      var parts = [];
      if (strategy.tools.length) parts.push("工具: " + strategy.tools.join("、"));
      if (strategy.restricted_tools.length) {
        parts.push("受限未开放: " + strategy.restricted_tools.join("、"));
      }
      toolLine.textContent = parts.join(" · ") || "工具由你自选";
      label.appendChild(toolLine);

      label.addEventListener("click", function () {
        ACTIVE_STRATEGY = strategy.key;
        Array.prototype.forEach.call(box.querySelectorAll(".sc-strategy"), function (node) {
          node.classList.toggle("is-selected", node.getAttribute("data-strategy") === strategy.key);
        });
        var custom = $("custom-tools");
        if (custom) custom.hidden = strategy.key !== "custom";
        setText("strategy-note", strategy.name + "：" + strategy.description, false);
      });

      box.appendChild(label);
    });

    // 受限工具单独说明一次，避免用户以为界面漏了 nuclei。
    if (restrictedTools && restrictedTools.length) {
      var note = restrictedTools.map(function (item) {
        return item.tool_name + "（" + item.reason + "）";
      });
      var box2 = $("strategy-note");
      if (box2) box2.textContent += " 本阶段未接入/未开放: " + note.join("；");
    }
    void tools;
  }

  function renderToolList(tools, restrictedTools) {
    var box = $("tool-list");
    if (!box) return;
    box.textContent = "";

    var all = tools.concat(restrictedTools || []);
    all.forEach(function (tool) {
      var row = document.createElement("label");
      row.className = "sc-tool" + (tool.internet_allowed ? "" : " is-blocked");

      var check = document.createElement("input");
      check.type = "checkbox";
      check.name = "custom_tool";
      check.value = tool.tool_name;
      check.disabled = !tool.internet_allowed;
      check.checked = Boolean(tool.default_enabled && tool.internet_allowed);
      row.appendChild(check);

      var body = document.createElement("span");
      var head = document.createElement("span");
      head.textContent = tool.tool_name + " ";
      body.appendChild(head);
      body.appendChild(riskBadge(tool));

      var meta = document.createElement("span");
      meta.className = "sc-tool-meta";
      if (tool.internet_allowed) {
        meta.textContent = "允许公网";
      } else {
        meta.textContent = "禁止公网 · " + (tool.reason || "未开放");
      }
      body.appendChild(meta);
      row.appendChild(body);

      box.appendChild(row);
    });
  }

  function renderProjects(projects) {
    var box = $("project-list");
    if (!box) return;
    box.textContent = "";

    if (!projects.length) {
      box.appendChild(hint("还没有项目。先在上面「步骤 1」创建一个。"));
      return;
    }

    projects.forEach(function (project) {
      var row = document.createElement("div");
      row.className = "sc-project-row";

      var name = document.createElement("strong");
      name.textContent = project.name;
      row.appendChild(name);

      var id = document.createElement("span");
      id.className = "mono";
      id.textContent = project.id;
      row.appendChild(id);

      var scopes = document.createElement("span");
      scopes.className = "mono";
      scopes.textContent = "Scope " + project.scope_count + " 个";
      row.appendChild(scopes);

      var note = document.createElement("span");
      note.className = "sc-project-note";
      note.textContent = project.owner ? "负责人 " + project.owner : "";
      note.title = project.authorization_note;
      row.appendChild(note);

      box.appendChild(row);
    });
  }

  function hint(text) {
    var p = document.createElement("p");
    p.className = "hint";
    p.textContent = text;
    return p;
  }

  function fillProjectSelects(projects) {
    ["scope-project", "job-project"].forEach(function (id) {
      var select = $(id);
      if (!select) return;
      var current = select.value;
      select.textContent = "";
      if (!projects.length) {
        var empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "— 请先创建项目 —";
        select.appendChild(empty);
        return;
      }
      projects.forEach(function (project) {
        var option = document.createElement("option");
        option.value = project.id;
        option.textContent = project.name + "（" + project.id + "）";
        select.appendChild(option);
      });
      if (current) select.value = current;
    });
    syncJobScopes();
  }

  function projectById(projectId) {
    if (!metadata) return null;
    return metadata.projects.filter(function (item) {
      return item.id === projectId;
    })[0] || null;
  }

  // 项目 → 已关联 Scope 的联动：只列出该项目下的 Scope，
  // 从界面上就杜绝「选了别的项目的 Scope」这种提交。
  function syncJobScopes() {
    var select = $("job-scope");
    var projectSelect = $("job-project");
    if (!select || !projectSelect) return;
    var project = projectById(projectSelect.value);
    select.textContent = "";

    var scopeIds = project ? project.scope_ids : [];
    if (!scopeIds.length) {
      var empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "— 请先给项目添加 Scope —";
      select.appendChild(empty);
      return;
    }
    scopeIds.forEach(function (scopeId) {
      var option = document.createElement("option");
      option.value = scopeId;
      option.textContent = scopeId;
      select.appendChild(option);
    });
  }

  // ── 步骤 1：创建项目 ────────────────────────────────────

  function bindProjectForm() {
    var form = $("project-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var payload = {
        name: form.name.value.trim(),
        authorization_note: form.authorization_note.value.trim(),
        owner: form.owner.value.trim(),
      };
      postJSON("/api/projects", payload).then(function (result) {
        if (!result.ok) {
          setText("project-feedback", "创建失败: " + describeError(result), true);
          return;
        }
        var project = result.data.project;
        setText("project-feedback", "已创建项目 " + project.id + "（" + project.name + "）。", false);
        form.reset();
        loadMetadata();
      });
    });
  }

  // ── 步骤 2：创建 Scope 并关联到项目 ──────────────────────

  function splitList(value) {
    return (value || "")
      .split(",")
      .map(function (item) {
        return item.trim();
      })
      .filter(Boolean);
  }

  function bindScopeForm() {
    var form = $("scope-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var projectId = form.project_id.value;
      var domains = splitList(form.allowed_domains.value);
      var cidrs = splitList(form.allowed_cidrs.value);

      if (!projectId) {
        setText("scope-feedback", "请先选择归属项目。", true);
        return;
      }
      if (!domains.length && !cidrs.length) {
        setText("scope-feedback", "至少填一个授权域名或授权 CIDR。", true);
        return;
      }

      var scopePayload = {
        name: form.name.value.trim() || ("授权范围 " + new Date().toISOString().slice(0, 10)),
        allowed_domains: domains,
        allowed_cidrs: cidrs,
        active_scan: form.active_scan.checked,
      };

      // 先建 Scope（它有自己的校验：拒绝 * 全放行、拒绝非法 CIDR），
      // 成功了再关联到项目 —— 顺序不能反，否则会剩下一堆孤儿 Scope。
      postJSON("/api/scopes", scopePayload).then(function (scopeResult) {
        if (!scopeResult.ok) {
          setText("scope-feedback", "Scope 创建失败: " + describeError(scopeResult), true);
          return null;
        }
        var scopeId = scopeResult.data.scope.id;
        return postJSON("/api/projects/" + encodeURIComponent(projectId) + "/scopes", {
          scope_id: scopeId,
        }).then(function (linkResult) {
          if (!linkResult.ok) {
            setText(
              "scope-feedback",
              "Scope " + scopeId + " 已创建，但关联项目失败: " + describeError(linkResult),
              true
            );
            return;
          }
          setText(
            "scope-feedback",
            "已创建 Scope " + scopeId + " 并关联到项目（active_scan=" +
              (scopePayload.active_scan ? "true" : "false") + "）。",
            false
          );
          form.reset();
          form.active_scan.checked = true;
          loadMetadata();
        });
      });
    });
  }

  // ── 步骤 3：创建公网任务 ────────────────────────────────

  function selectedCustomTools() {
    var box = $("tool-list");
    if (!box) return [];
    return Array.prototype.filter
      .call(box.querySelectorAll('input[name="custom_tool"]'), function (input) {
        return input.checked && !input.disabled;
      })
      .map(function (input) {
        return input.value;
      });
  }

  function bindJobForm() {
    var form = $("job-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var targets = splitList(form.targets.value);
      if (!targets.length) {
        setText("strategy-note", "请填写至少一个目标。", true);
        return;
      }

      var payload = {
        project_id: form.project_id.value,
        scope_id: form.scope_id.value,
        targets: targets,
        strategy: ACTIVE_STRATEGY,
      };
      if (ACTIVE_STRATEGY === "custom") payload.tools = selectedCustomTools();
      if ($("job-mock") && $("job-mock").checked) payload.mode = "mock";

      postJSON("/api/public-jobs", payload).then(function (result) {
        if (!result.ok) {
          setText("strategy-note", "创建失败: " + describeError(result), true);
          return;
        }
        var data = result.data;
        setText(
          "strategy-note",
          "任务已创建：" + data.job_id + "（" + data.status + "，模式 " + data.mode +
            "，共 " + data.total_steps + " 步）。worker 会异步执行。",
          false
        );
        form.targets.value = "";
        // 新任务要出现在列表里：整页刷新最省事，也保证表格数据来自服务端。
        window.location.href = "/scan-center?job_id=" + encodeURIComponent(data.job_id);
      });
    });
  }

  function bindProjectSelectSync() {
    var select = $("job-project");
    if (select) select.addEventListener("change", syncJobScopes);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("strategy-list")) return; // 不是扫描中心页
    bindProjectForm();
    bindScopeForm();
    bindJobForm();
    bindProjectSelectSync();
    loadMetadata();
  });
})();