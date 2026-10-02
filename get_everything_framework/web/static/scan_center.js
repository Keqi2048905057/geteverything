/* Get Everything Framework — 扫描中心前端
 *
 * 职责（对应页面上的三个区块）：
 *   1. 「授权项目」  —— 创建授权测试项目（授权证据的组织单位）；
 *   2. 「授权范围」  —— 给当前项目添加域名 / IP 网段范围；
 *   3. 「创建任务」  —— 选目标 / 选工具 / 选模式，调 POST /api/public-jobs。
 *
 * ── 展示口径（下一阶段方案第 2 节「Scope 展示」） ──────────────
 * 用户看到的是：
 *
 *     学校官网
 *     www.example.cn
 *     已授权
 *
 * 而不是 `scope_9f3c…`、`proj_1a2b…` 这类内部主键，也不是
 * `Scope 1 个`、`（无）`、空占位符这类后台术语与空壳。
 * 具体做法：
 *   * 所有**可见文案**都用名称/目标/状态拼，绝不把实体 ID 写进 textContent；
 *     实体 ID 只作为表单 `<option value>` 与请求体字段（不可见）；
 *   * 目标清单来自既有的、**需登录**的 `GET /api/scopes`（管理员本来就能看到），
 *     而不是塞进 `/api/scan-center` 的批量元数据里 —— 那条接口保持「不下发目标清单」
 *     的既有约定（有测试锁定）。
 *
 * ── 安全约定 ──────────────────────────────────────────────────
 *   * 页面上的每一个提交动作都只是**转发**到 API，闸门全部在服务端
 *     （项目 → 项目内范围 → 公网工具白名单 → Policy / Scope / 模式）；
 *     前端不做、也不能做「能不能扫」的判断；
 *   * 被禁用的工具在界面上置灰，这是**可用性**提示而不是安全边界 ——
 *     即便有人绕过 DOM 直接发请求，后端一样会 400；
 *   * 不使用任何前端框架、不引入 CDN 资源。
 */

(function () {
  "use strict";

  var ACTIVE_STRATEGY = "asset_discovery";
  var metadata = null;
  /** scope_id → Scope 对象（来自 `GET /api/scopes`），用于把 ID 翻译成人话。 */
  var scopeIndex = {};
  /** 步骤 2 操作的项目；步骤 1 创建成功后自动切到新项目。 */
  var currentProjectId = "";

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

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  }

  function hint(text, className) {
    var p = document.createElement("p");
    p.className = className || "hint";
    p.textContent = text;
    return p;
  }

  // ── 把内部 ID 翻译成用户能读的文案 ────────────────────────

  /** 某个 Scope 的授权目标（域名 + 网段），只用于展示。 */
  function describeScopeTargets(scope) {
    if (!scope) return "";
    var parts = [];
    if (scope.allowed_domains && scope.allowed_domains.length) {
      parts.push(scope.allowed_domains.join("、"));
    }
    if (scope.allowed_cidrs && scope.allowed_cidrs.length) {
      parts.push(scope.allowed_cidrs.join("、"));
    }
    return parts.join("、") || "（未填写目标）";
  }

  /** Scope 的授权状态文案。 */
  function scopeStateLabel(scope) {
    if (!scope) return "未知";
    return scope.active_scan ? "已授权" : "仅被动（未开真实扫描）";
  }

  /**
   * 下拉框里的一项范围：`学校官网 · www.example.cn · 已授权`。
   *
   * 这是「隐藏 Scope ID」的落点 —— 返回值里**不含**任何实体 ID。
   */
  function scopeLabel(scopeId) {
    var scope = scopeIndex[scopeId];
    if (!scope) return "授权范围（名称待读取）";
    return scope.name + " · " + describeScopeTargets(scope) + " · " + scopeStateLabel(scope);
  }

  /** 项目下拉框文案：只用名称，不带 `proj_…`。 */
  function projectLabel(project) {
    if (!project) return "授权项目";
    return project.name + (project.owner ? " · 负责人 " + project.owner : "");
  }

  function projectById(projectId) {
    if (!metadata || !metadata.projects) return null;
    return metadata.projects.filter(function (item) {
      return item.id === projectId;
    })[0] || null;
  }

  function scopeById(scopeId) {
    return scopeIndex[scopeId] || null;
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
      // 不把 `scope_…` 原样倒给用户；能翻译就翻译，翻不出来只说数量。
      var attached = details.attached_scope_ids.map(function (id) {
        var scope = scopeById(id);
        return scope ? scope.name : "一个已关联范围";
      });
      message += "；该项目当前已关联: " + (attached.join("、") || "（还没有）");
    }
    return message;
  }

  function riskBadge(tool) {
    var span = document.createElement("span");
    span.className = "risk risk-" + tool.risk_level;
    span.textContent = tool.risk_label || tool.risk_level;
    span.title = tool.tool_name + " 的风险等级";
    return span;
  }

  // ── 元数据（项目 / 范围 / 策略 / 工具权限） ───────────────

  function loadMetadata() {
    return Promise.all([
      getJSON("/api/scan-center").catch(function (err) {
        if (String(err.message).indexOf("401") !== -1) return null; // 未登录：页面已有提示
        throw err;
      }),
      getJSON("/api/scopes").catch(function () {
        return null; // 范围读取失败不阻断其余渲染
      }),
    ])
      .then(function (results) {
        var center = results[0];
        var scopes = results[1];
        if (!center) return;

        scopeIndex = {};
        if (scopes && scopes.scopes) {
          scopes.scopes.forEach(function (scope) {
            scopeIndex[scope.id] = scope;
          });
        }

        metadata = center;
        renderStrategies(center.strategies, center.restricted_tools);
        renderToolList(center.tools, center.restricted_tools);
        renderProjects(center.projects);
        fillProjectSelects(center.projects);
      })
      .catch(function () {
        setText("project-feedback", "扫描中心元数据读取失败，请刷新页面重试。", true);
      });
  }

  function renderStrategies(strategies, restrictedTools) {
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
    // 写进**独立**的提示元素，不再往 strategy-note 上累加 ——
    // 累加会让「切换策略」后的说明里混着上一次的尾巴（方案第 2 节「清理异常展示」）。
    var restrictedBox = $("restricted-note");
    if (restrictedBox) {
      restrictedBox.textContent = "";
      if (restrictedTools && restrictedTools.length) {
        restrictedBox.hidden = false;
        restrictedBox.appendChild(
          hint(
            "本阶段未接入/未开放的工具：" +
              restrictedTools
                .map(function (item) {
                  return item.tool_name + "（" + item.reason + "）";
                })
                .join("；")
          )
        );
      } else {
        restrictedBox.hidden = true;
      }
    }
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
      meta.textContent = tool.internet_allowed ? "允许公网" : "禁止公网 · " + (tool.reason || "未开放");
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
      box.appendChild(hint("还没有授权项目。先在上面「步骤 1」创建一个。"));
      return;
    }

    projects.forEach(function (project) {
      var row = document.createElement("div");
      row.className = "sc-project-row";
      if (project.id === currentProjectId) row.classList.add("is-current");

      var head = document.createElement("div");
      head.className = "sc-project-head";
      var name = document.createElement("strong");
      name.textContent = project.name;
      head.appendChild(name);

      var count = document.createElement("span");
      count.className = "sc-project-meta";
      count.textContent = "授权范围 " + project.scope_count + " 个";
      head.appendChild(count);

      if (project.owner) {
        var owner = document.createElement("span");
        owner.className = "sc-project-meta";
        owner.textContent = "负责人 " + project.owner;
        head.appendChild(owner);
      }

      var use = document.createElement("button");
      use.type = "button";
      use.className = "btn btn-ghost sc-project-use";
      use.textContent = project.id === currentProjectId ? "当前项目" : "设为当前项目";
      use.disabled = project.id === currentProjectId;
      use.addEventListener("click", function () {
        selectProject(project.id);
      });
      head.appendChild(use);
      row.appendChild(head);

      // 授权说明是这一页最该被看见的东西，给它一整行，而不是塞进 title 属性
      // （title 要悬停才出现，等于没展示）。
      if (project.authorization_note) {
        var note = document.createElement("p");
        note.className = "sc-project-note";
        note.textContent = "授权说明：" + project.authorization_note;
        row.appendChild(note);
      }

      box.appendChild(row);
    });
  }

  function renderScopeList() {
    var box = $("scope-list");
    if (!box) return;
    box.textContent = "";

    var project = projectById(currentProjectId);
    if (!project) {
      box.appendChild(hint("先在步骤 1 创建或选一个授权项目，这里会列出它的授权范围。"));
      return;
    }

    var ids = project.scope_ids || [];
    if (!ids.length) {
      box.appendChild(hint("这个项目还没有授权范围。填一个域名或网段，点「添加授权范围」。"));
      return;
    }

    var title = document.createElement("h4");
    title.className = "sc-scope-title";
    title.textContent = "已授权的范围";
    box.appendChild(title);

    ids.forEach(function (scopeId) {
      var scope = scopeById(scopeId);
      var row = document.createElement("div");
      row.className = "sc-scope-row" + (scope && scope.active_scan ? " is-active" : "");

      var name = document.createElement("strong");
      name.textContent = scope ? scope.name : "授权范围";
      row.appendChild(name);

      var targets = document.createElement("span");
      targets.className = "sc-scope-targets";
      targets.textContent = describeScopeTargets(scope);
      row.appendChild(targets);

      var state = document.createElement("span");
      state.className = "badge " + (scope && scope.active_scan ? "badge-ok" : "badge-warn");
      state.textContent = scopeStateLabel(scope);
      row.appendChild(state);

      box.appendChild(row);
    });
  }

  function syncProjectHeader() {
    var line = $("scope-project-current");
    if (!line) return;
    var project = projectById(currentProjectId);
    line.textContent = project
      ? "当前项目：" + projectLabel(project)
      : "当前项目：还没有 —— 请先在步骤 1 创建一个。";
  }

  function selectProject(projectId) {
    currentProjectId = projectId || "";
    var hidden = $("scope-project");
    if (hidden) hidden.value = currentProjectId;
    var jobSelect = $("job-project");
    if (jobSelect && currentProjectId) jobSelect.value = currentProjectId;

    syncProjectHeader();
    syncJobScopes();
    renderProjects((metadata && metadata.projects) || []);
    renderScopeList();
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
        empty.textContent = "还没有授权项目";
        select.appendChild(empty);
        return;
      }
      projects.forEach(function (project) {
        var option = document.createElement("option");
        option.value = project.id;
        // 可见文案里只有名称与负责人，不带 `proj_…`。
        option.textContent = projectLabel(project);
        select.appendChild(option);
      });
      if (current) select.value = current;
    });

    // 默认选中最近创建的那个项目（列表按创建时间倒序），用户少点一次。
    var jobSelect = $("job-project");
    var fallback = projects.length ? projects[0].id : "";
    if (!currentProjectId) {
      currentProjectId = (jobSelect && jobSelect.value) || fallback;
    }
    selectProject(currentProjectId);
  }

  // 项目 → 已关联范围的联动：只列出该项目下的范围，
  // 从界面上就杜绝「选了别的项目的范围」这种提交。
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
      empty.textContent = "这个项目还没有授权范围";
      select.appendChild(empty);
      return;
    }
    scopeIds.forEach(function (scopeId) {
      var option = document.createElement("option");
      // value 是实体 ID（不可见），文案是人话 —— 这是「隐藏 Scope ID」的落点。
      option.value = scopeId;
      option.textContent = scopeLabel(scopeId);
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
        setText("project-feedback", "已创建授权项目「" + project.name + "」。", false);
        form.reset();
        loadMetadata().then(function () {
          selectProject(project.id);
        });
      });
    });
  }

  // ── 步骤 2：添加授权范围 ────────────────────────────────

  function splitList(value) {
    return (value || "")
      .split(",")
      .map(function (item) {
        return item.trim();
      })
      .filter(Boolean);
  }

  /** 范围名称不再让用户填 —— 它只是内部标签，用「项目名 · 首个目标」派生即可。 */
  function deriveScopeName(project, domains, cidrs) {
    var target = domains[0] || cidrs[0] || "";
    if (project && project.name) {
      return target ? project.name + " · " + target : project.name;
    }
    return target || "授权范围";
  }

  function bindScopeForm() {
    var form = $("scope-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var projectId = (form.project_id && form.project_id.value) || currentProjectId;
      var domains = splitList(form.allowed_domains.value);
      var cidrs = splitList(form.allowed_cidrs.value);

      if (!projectId) {
        setText("scope-feedback", "还没有授权项目，请先在步骤 1 创建一个。", true);
        return;
      }
      if (!domains.length && !cidrs.length) {
        setText("scope-feedback", "至少填一个授权域名或授权网段。", true);
        return;
      }

      var project = projectById(projectId);
      var scopePayload = {
        name: deriveScopeName(project, domains, cidrs),
        allowed_domains: domains,
        allowed_cidrs: cidrs,
        active_scan: form.active_scan.checked,
      };

      // 先建范围（它有自己的校验：拒绝 * 全放行、拒绝非法 CIDR），
      // 成功了再关联到项目 —— 顺序不能反，否则会剩下一堆孤儿范围。
      postJSON("/api/scopes", scopePayload).then(function (scopeResult) {
        if (!scopeResult.ok) {
          setText("scope-feedback", "添加失败: " + describeError(scopeResult), true);
          return null;
        }
        var scopeId = scopeResult.data.scope.id;
        return postJSON("/api/projects/" + encodeURIComponent(projectId) + "/scopes", {
          scope_id: scopeId,
        }).then(function (linkResult) {
          if (!linkResult.ok) {
            setText(
              "scope-feedback",
              "范围已创建，但关联到项目失败: " + describeError(linkResult),
              true
            );
            return;
          }
          setText(
            "scope-feedback",
            "已添加授权范围「" + scopePayload.name + "」（" +
              (scopePayload.active_scan ? "已授权真实扫描" : "仅被动，未开真实扫描") + "）。",
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
    if (select) {
      select.addEventListener("change", function () {
        selectProject(select.value);
      });
    }
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
