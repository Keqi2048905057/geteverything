/* Get Everything Framework — 扫描中心前端
 *
 * 页面上的四步（下一阶段规划方案第 5.2 节的新流程）：
 *
 *   步骤 1 · 输入目标      → 填目标，点「检查授权」调 POST /api/public-jobs/check
 *   步骤 2 · 确认授权状态  → 看「目标 / 授权状态 / 授权资产」三行摘要，
 *                            选项目 + 选授权资产，并勾选「我确认该目标属于授权范围」
 *   步骤 3 · 选择工具      → 选 Scan Profile（资产发现 / Web 基础检查 / 自定义），
 *                            每张卡片上同时写明**节奏**（低频 / 常规）
 *   步骤 4 · 创建任务      → mock 或 real，创建任务
 *
 * 与旧流程的差别（方案第 2 节「删除重复步骤」）：原来的「步骤 2 选项目 → 再选范围」
 * 两个概念被合并成一步「确认授权状态」，页面只要求用户回答「目标属于哪份授权」，
 * 不再要求他先理解项目 / 范围 / scope_id 这套内部模型。
 *
 * ── Scan Profile = 工具组合 + 节奏（下一阶段方案第 5、6 节 Phase 3） ──
 * 节奏（`core/pace.py`）是「这个 Profile 打得多快」，不是权限：
 *  * `light`  低频 —— 后端把并发与每秒请求数压下来，并在真实步骤之间留间隔；
 *  * `normal` 常规 —— 完全沿用工具自身配置（历史行为）。
 * 前端的责任只有两件：**如实显示**，以及**原样转发**。合并规则（模板档位与
 * 请求档位取更保守的一档）只在服务端实现一次，因此这里把 pace 改错也放松不了
 * 任何东西 —— 这正是「前端不是安全边界」的既有约定。
 *
 * ── 为什么第 1 步是「检查授权」而不是直接创建任务 ──────────────
 * 以前用户填完目标点创建，越界 / 没开 active_scan / 没开环境开关这三种情况
 * 都表现为同一个 `403 scope_violation`，只能靠读错误消息反推缺了哪一道。
 * 现在第 1 步先把结论摊开：目标落在哪个范围内、还缺什么。
 * 这一步**只读**，不创建任务、不写审计；匹配用的是 Policy 内部同一个
 * `Scope.match_target`，所以「试算说能过」与「真提交能过」不会不一致。
 *
 * ── 展示口径（Phase 1：隐藏内部 ID、去后台术语） ──────────────
 * 用户看到的是
 *
 *     学校官网
 *     www.example.cn
 *     已授权
 *
 * 而不是 `scope_9f3c…`。所有可见文案都走 scopeLabel() / projectLabel() /
 * describeScopeTargets() / scopeStateLabel() 四个翻译函数；实体 ID 只作为
 * `<option value>` 与请求体字段（不可见）。
 * 目标清单来自既有的、需登录的 `GET /api/scopes`，不塞进 `/api/scan-center`
 * —— 那条接口「不下发目标清单」的既有约定与测试保持有效。
 *
 * ── 安全约定 ──────────────────────────────────────────────────
 *   * 每一个提交动作都只是**转发**到 API，闸门全部在服务端
 *     （项目 → 项目内范围 → 公网工具白名单 → Policy / Scope / 模式）；
 *     前端不做、也不能做「能不能扫」的判断；
 *   * 被禁用的工具在界面上置灰，这是**可用性**提示而不是安全边界；
 *   * 不使用任何前端框架、不引入 CDN 资源。
 */

(function () {
  "use strict";

  var ACTIVE_STRATEGY = "asset_discovery";
  /**
   * 当前选中策略的节奏档位（`light` / `normal`，见后端 `core/pace.py`）。
   *
   * 它是**提交时随请求带上的**一个字段，不是前端判定：服务端仍然按
   * 「模板档位 + 请求档位取更保守的那一档」重新算一次，因此这里改错也放松不了
   * 任何东西。带上它的意义是「你看到的节奏」与「请求里的节奏」是同一个值。
   */
  var ACTIVE_PACE = "light";
  var metadata = null;
  /** pace key → {pace, pace_label, pace_description, step_delay_seconds} */
  var paceIndex = {};
  /** scope_id → Scope 对象（来自 `GET /api/scopes`），用于把 ID 翻译成人话。 */
  var scopeIndex = {};
  /** 上一次试算的原始目标，用于「补范围」时自动带过去。 */
  var lastTargets = [];
  /** 上一次试算的完整响应，用于步骤 2 的「授权状态」摘要（只读回显）。 */
  var lastCheckPayload = null;

  // 缺哪一道闸门 → 人话。后端给 `blocker`，这里只做翻译，不做判定。
  var BLOCKER_LABELS = {
    invalid_target: "目标格式不合法",
    no_scope: "还没有任何授权范围",
    not_authorized: "这个目标不在任何已授权范围内",
    scope_inactive: "目标已被授权，但该范围没有开启真实扫描",
    env_disabled: "范围已授权，但环境总开关 GEF_ALLOW_REAL_SCAN 没开",
  };

  var STATUS_LABELS = {
    ready: "已授权，可真实扫描",
    scope_inactive: "已授权，但未开启真实扫描",
    excluded: "命中排除列表（排除优先，等于拒绝）",
    out_of_scope: "不在该范围内",
  };

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

  function splitList(value) {
    return (value || "")
      .split(",")
      .map(function (item) {
        return item.trim();
      })
      .filter(Boolean);
  }

  /** 目标输入框**此刻**的内容（用户可能刚改过，与上一次试算无关）。 */
  function currentTargets() {
    var input = $("job-target");
    return splitList(input ? input.value : "");
  }

  /**
   * 上一次试算结果是否仍然对应当前输入。
   *
   * 试算结论（能不能扫、命中哪份授权资产）是**针对某一组目标**算出来的；
   * 用户改完输入框却没重新点「检查授权」时，那份结论就只是一段历史。
   * 页面若继续拿它当依据，就会出现「摘要写着 A 已授权、实际提交的是 B」
   * 这种最难排查的错 —— 用户看到的是绿灯，服务端按 B 判定。
   */
  function checkIsFresh() {
    if (!lastCheckPayload || !lastTargets.length) return false;
    return currentTargets().join("\u0000") === lastTargets.join("\u0000");
  }

  /** 输入变了：把上一次试算的结论作废，并如实告诉用户要重新检查。 */
  function invalidateCheckResult() {
    lastCheckPayload = null;
    lastTargets = [];
    var box = $("check-result");
    if (box) box.textContent = "";
    setText("check-summary", "目标已改动，请重新点「检查授权」再创建任务。", false);
    renderConsentSummary();
  }

  // ── 把内部 ID 翻译成用户能读的文案 ────────────────────────

  /** 某个范围的授权目标（域名 + 网段），只用于展示。 */
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

  /** 范围的授权状态文案。 */
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

  /** 项目下拉框文案：只用名称与负责人，不带 `proj_…`。 */
  function projectLabel(project) {
    if (!project) return "授权项目";
    return project.name + (project.owner ? " · 负责人 " + project.owner : "");
  }

  function projectById(projectId) {
    if (!metadata || !metadata.projects) return null;
    return (
      metadata.projects.filter(function (item) {
        return item.id === projectId;
      })[0] || null
    );
  }

  function scopeById(scopeId) {
    return scopeIndex[scopeId] || null;
  }

  // 服务端统一的错误体形状：error_code + error_message（+ details）。
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

  /** 节奏档位 → 「低频 · 降低并发与速率」。元数据缺席时退化为只显示 key。 */
  function paceLabelOf(pace) {
    var item = paceIndex[pace];
    if (!item) return "节奏: " + pace;
    var text = "节奏: " + item.pace_label;
    if (item.pace_description) text += " · " + item.pace_description;
    return text;
  }

  /**
   * 按服务端下发的 ``limits`` 元数据渲染限速 / 超时输入框（Phase 3）。
   *
   * 字段名（``rate_limit`` / ``timeout_seconds``）、上下界与中文说明**全部**来自
   * ``/api/scan-center`` 的 ``limits`` —— 与工具清单、分组、节奏同一口径：
   * 前端写死第二份文案，后端改了上下界页面还停在上一个版本，而且不会报错。
   * 元数据缺席（老后端 / 接口失败）时**不渲染**输入框：宁可不给这个能力，
   * 也不给一个范围写错的框 —— 用户以为设好了、实际拿到的却是服务端的默认值
   * 是最坏的错法（与 ``core/job_limits`` 拒绝静默夹边界同一条理由）。
   */
  function renderLimits(limits) {
    var box = $("limits-fields");
    if (!box) return;
    box.textContent = "";
    if (!limits) return;

    // 遍历**服务端给的键**而不是写死一张键名表：后端加一项（例如「最大并发」）
    // 前端就自动多一个输入框，不需要改这里 —— 与工具清单同一口径。
    Object.keys(limits).forEach(function (key) {
      var spec = limits[key];
      if (!spec || !spec.field) return;

      var inputId = "job-limit-" + key;
      var label = document.createElement("label");
      label.setAttribute("for", inputId);
      label.textContent = spec.label || spec.field;
      box.appendChild(label);

      var input = document.createElement("input");
      input.type = "number";
      input.id = inputId;
      // ``name`` 直接取服务端字段名：提交时按它拼请求键，前端不参与改名。
      input.name = spec.field;
      input.setAttribute("inputmode", "numeric");
      input.setAttribute("data-limit-field", spec.field);
      input.min = spec.min;
      input.max = spec.max;
      input.placeholder = spec.min + " ~ " + spec.max + "（留空表示不额外收紧）";
      box.appendChild(input);

      if (spec.hint) {
        box.appendChild(hint(spec.hint));
      }
    });
  }

  /**
   * 读回用户填的限速 / 超时，拼成请求字段。
   *
   * 留空 → **不加这个键**（而不是传 ``0`` 或 ``null``）：服务端把「没指定」与
   * 「指定了非法值」分得很开，传 ``null`` 只是绕一圈回到同一个结论，
   * 传 ``0`` 却会被判成越界 —— 而用户什么都没填。
   * 合法性与范围仍由服务端判定，这里不替它做校验（前端不是安全边界）。
   */
  function collectLimits() {
    var payload = {};
    var box = $("limits-fields");
    if (!box) return payload;
    Array.prototype.forEach.call(box.querySelectorAll("input[data-limit-field]"), function (input) {
      var text = String(input.value || "").trim();
      if (!text) return;
      payload[input.getAttribute("data-limit-field")] = text;
    });
    return payload;
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
        paceIndex = {};
        (center.paces || []).forEach(function (item) {
          paceIndex[item.pace] = item;
        });
        renderStrategies(center.strategies, center.restricted_tools);
        // 工具清单跟着**当前选中**的模板渲染：首屏要能看见「这一次会用哪些工具」，
        // 而不是等用户点一下卡片才出现。模板模式下列表置灰但可见。
        // 分组元数据同样来自服务端（``tool_groups``），前端不写死任何栏位名。
        renderToolList(
          center.tools,
          center.restricted_tools,
          activeStrategyOf(center.strategies),
          center.tool_groups
        );
        fillProjectSelects(center.projects);
        renderProjects(center.projects);
        // 限速 / 超时的输入框同样由服务端元数据生成（Phase 3）。
        renderLimits(center.limits);
        renderConsentSummary();
      })
      .catch(function () {
        setText("check-summary", "扫描中心元数据读取失败，请刷新页面重试。", true);
      });
  }

  /** 在服务端下发的模板里找出当前选中的那一个（找不到返回 ``null``）。 */
  function activeStrategyOf(strategies) {
    var found = null;
    (strategies || []).forEach(function (item) {
      if (item.key === ACTIVE_STRATEGY) found = item;
    });
    return found;
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

      // 节奏是 Scan Profile 的第二维，必须和工具一样**写在卡片上**：
      // 只把「低频」放在一句可切换的说明里，用户切走策略后就再也看不见它了。
      var paceLine = document.createElement("span");
      paceLine.className = "sc-strategy-pace";
      paceLine.textContent = paceLabelOf(strategy.pace);
      label.appendChild(paceLine);

      label.addEventListener("click", function () {
        ACTIVE_STRATEGY = strategy.key;
        ACTIVE_PACE = strategy.pace || ACTIVE_PACE;
        Array.prototype.forEach.call(box.querySelectorAll(".sc-strategy"), function (node) {
          node.classList.toggle("is-selected", node.getAttribute("data-strategy") === strategy.key);
        });
        setText("strategy-note", strategy.name + "：" + strategy.description, false);
        // 工具清单要跟着模板走：模板模式下置灰并显示模板的工具，
        // 自定义模式下开放勾选。清单数据仍来自服务端（不发第二次请求）。
        if (metadata) {
          renderToolList(
            metadata.tools,
            metadata.restricted_tools,
            strategy,
            metadata.tool_groups
          );
        }
        setText(
          "tool-list-note",
          strategy.key === "custom"
            ? "自定义模式：勾选要用的工具（被禁工具无法勾选）。"
            : "本次会用到的工具（由所选模板决定）。",
          false
        );
      });
      box.appendChild(label);
    });

    // 首屏（还没点过任何卡片）也要显示**当前选中那一个**的节奏，
    // 而不是 HTML 里写死的一句 —— 写死的文案会随着模板改动悄悄过期。
    var active = null;
    strategies.forEach(function (strategy) {
      if (strategy.key === ACTIVE_STRATEGY) active = strategy;
    });
    if (active) {
      ACTIVE_PACE = active.pace || ACTIVE_PACE;
      setText("strategy-note", active.name + "：" + active.description, false);
      setText(
        "tool-list-note",
        active.key === "custom"
          ? "自定义模式：勾选要用的工具（被禁工具无法勾选）。"
          : "本次会用到的工具（由所选模板决定）。",
        false
      );
    }

    // 受限工具单独说明一次，避免用户以为界面漏了 nuclei。
    // 写进**独立**的提示元素，不再往 strategy-note 上累加 ——
    // 累加会让「切换策略」后的说明里混着上一次的尾巴。
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

  /**
   * 渲染**一个**工具条目（勾选框 + 名称 + 风险徽标 + 用途说明 + 权限说明）。
   *
   * 抽成独立函数是因为清单要按能力分组渲染多次；条目形态只有一份，
   * 因此不会出现「分组里长一个样、扁平列表里长另一个样」。
   *
   * @param {Object} tool 服务端下发的工具条目（``tool_name`` / ``internet_allowed`` …）。
   * @param {boolean} isCustom 当前是否自定义模式（决定能否勾选）。
   * @param {Object} preset 模板模式下「本次会用到的工具」集合。
   */
  function renderToolRow(tool, isCustom, preset) {
    var locked = tool.internet_allowed && !isCustom;
    var row = document.createElement("label");
    row.className = "sc-tool" + (tool.internet_allowed ? "" : " is-blocked");

    var check = document.createElement("input");
    check.type = "checkbox";
    check.name = "custom_tool";
    check.value = tool.tool_name;
    // 白名单外的工具永远不可勾；模板模式下白名单内的也置灰（由模板决定）。
    check.disabled = !tool.internet_allowed || locked;
    check.checked = isCustom
      ? Boolean(tool.default_enabled && tool.internet_allowed)
      : Boolean(preset[tool.tool_name]);
    row.appendChild(check);

    var body = document.createElement("span");
    var head = document.createElement("span");
    head.textContent = tool.tool_name + " ";
    body.appendChild(head);
    body.appendChild(riskBadge(tool));

    // 用途说明来自 Tool Registry（服务端下发）：前端只负责显示，
    // 因此新增工具时改后端一处即可，不必回来补文案。
    if (tool.description) {
      var desc = document.createElement("span");
      desc.className = "sc-tool-desc";
      desc.textContent = tool.description;
      body.appendChild(desc);
    }

    var meta = document.createElement("span");
    meta.className = "sc-tool-meta";
    if (!tool.internet_allowed) {
      meta.textContent = "禁止公网 · " + (tool.reason || "未开放");
    } else if (locked) {
      meta.textContent = "本次由所选模板决定，要改请切到自定义模式";
    } else {
      meta.textContent = "允许公网 · 可勾选";
    }
    body.appendChild(meta);
    row.appendChild(body);

    return row;
  }

  /**
   * 渲染工具清单（方案第 8 节「工具选择中心」+ 第 9 节 Tool Registry）。
   *
   * 关键约定：**工具清单与能力分组都来自服务端**（``GET /api/scan-center`` 的
   * ``tool_groups``；每一栏的说明、每个工具的用途都出自同一份
   * ``ToolPolicy.to_dict()``）。前端一个工具名、一个栏位名都不写死 ——
   * 新增工具或调整分组时改后端即可，页面自动出现。这正是方案第 9 节
   * 「不要把工具写死在前端」的落地。
   *
   * 分组为空时**照样渲染栏位**并写明「本阶段暂无可用工具」：方案点名的
   * 「技术识别」「漏洞检测」本阶段确实没有可跑的工具，如实呈现比悄悄藏掉
   * 更诚实 —— 藏掉会让使用者以为是自己没找到。
   *
   * 两种勾选状态：
   *
   * * 选了**模板**（资产发现 / Web 基础检查）：清单仍然列出来，勾选状态是
   *   模板决定的，因此**置灰不可改** —— 用户能看清「这一次会用哪些工具」，
   *   但改不了；要改就切到自定义模式。把模板的工具藏起来是更差的做法：
   *   用户点完卡片反而不知道要跑什么。
   * * 选了**自定义模式**：可勾选；公网白名单之外的一律置灰并给出原因
   *   （``internet_allowed=false``）—— 这是**可用性**提示，真正的闸门在服务端。
   *
   * @param {Array} tools 服务端下发的工具权限表（含 ``tool_name`` / ``internet_allowed`` …）。
   * @param {Array} restrictedTools 未接入 / 未开放的工具（如 nuclei），只展示。
   * @param {Object|null} strategy 当前选中的模板；``custom`` 或空表示用户自选。
   * @param {Array} groups 服务端下发的能力分组（``key`` / ``name`` / ``description`` / ``tools``）。
   *   缺失时退回扁平清单，行为与引入分组之前一致。
   */
  function renderToolList(tools, restrictedTools, strategy, groups) {
    var box = $("tool-list");
    if (!box) return;
    box.textContent = "";

    var isCustom = !strategy || strategy.key === "custom";
    // 模板模式下「本次会用到的工具」由模板决定；自定义模式下由用户勾。
    var preset = {};
    if (!isCustom) {
      (strategy.tools || []).forEach(function (name) {
        preset[name] = true;
      });
    }

    if (groups && groups.length) {
      var placed = {};
      var restricted = restrictedTools || [];

      groups.forEach(function (group) {
        // 未接入 / 未开放的工具（如 nuclei）也归到它**自己声明的**分组里：
        // 服务端给每条 restricted tool 也带上 ``tool_group``，因此这里不需要
        // 写死任何「nuclei 属于漏洞检测」这类映射 —— 方案第 8 节那张表里
        // nuclei 归在「漏洞检测」栏，靠的就是这个字段。
        // 空栏位照样渲染：如实说明「本阶段暂无可用工具」比悄悄藏掉更诚实。
        var unopened = restricted.filter(function (tool) {
          return tool.tool_group === group.key;
        });
        var available = group.tools || [];

        var section = document.createElement("div");
        section.className = "sc-tool-group";
        section.setAttribute("data-tool-group", group.key);

        var head = document.createElement("div");
        head.className = "sc-tool-group-head";
        var title = document.createElement("span");
        title.className = "sc-tool-group-name";
        title.textContent = group.name;
        head.appendChild(title);
        var count = document.createElement("span");
        count.className = "sc-tool-group-count";
        if (available.length) {
          count.textContent = available.length + " 个";
        } else if (unopened.length) {
          count.textContent = "本阶段暂无可用工具（仅列出未开放项）";
        } else {
          count.textContent = "本阶段暂无可用工具";
        }
        head.appendChild(count);
        section.appendChild(head);

        if (group.description) {
          var desc = document.createElement("p");
          desc.className = "sc-tool-group-desc";
          desc.textContent = group.description;
          section.appendChild(desc);
        }

        var grid = document.createElement("div");
        grid.className = "sc-tools";
        available.forEach(function (tool) {
          placed[tool.tool_name] = true;
          grid.appendChild(renderToolRow(tool, isCustom, preset));
        });
        unopened.forEach(function (tool) {
          placed[tool.tool_name] = true;
          grid.appendChild(renderToolRow(tool, isCustom, preset));
        });
        section.appendChild(grid);
        box.appendChild(section);
      });

      // 兜底：扁平表里有、分组表里没有的工具，宁可多显示一行也不静默丢掉
      // （服务端 ``group_tool_policies`` 遇到未登记分组是直接抛错的，这里只防
      // 「两张表不同源」这类改动）。
      var leftovers = tools.concat(restricted).filter(function (tool) {
        return !placed[tool.tool_name];
      });
      if (leftovers.length) {
        var restGrid = document.createElement("div");
        restGrid.className = "sc-tools";
        leftovers.forEach(function (tool) {
          restGrid.appendChild(renderToolRow(tool, isCustom, preset));
        });
        box.appendChild(restGrid);
      }
      return;
    }

    // 没有分组元数据（更早的服务端响应）：退回扁平清单。
    var flat = document.createElement("div");
    flat.className = "sc-tools";
    tools.concat(restrictedTools || []).forEach(function (tool) {
      flat.appendChild(renderToolRow(tool, isCustom, preset));
    });
    box.appendChild(flat);
  }

  function renderProjects(projects) {
    var box = $("project-list");
    if (!box) return;
    box.textContent = "";

    if (!projects.length) {
      box.appendChild(hint("还没有授权项目。在步骤 2 里创建一个。"));
      return;
    }

    projects.forEach(function (project) {
      var row = document.createElement("div");
      row.className = "sc-project-row";

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
      row.appendChild(head);

      // 授权说明是这一页最该被看见的东西，给它一整行，而不是塞进 title 属性。
      if (project.authorization_note) {
        var note = document.createElement("p");
        note.className = "sc-project-note";
        note.textContent = "授权说明：" + project.authorization_note;
        row.appendChild(note);
      }

      // 每个项目下挂的范围，直接列出来（名称 + 目标 + 状态），不显示 scope_…。
      (project.scope_ids || []).forEach(function (scopeId) {
        var scope = scopeById(scopeId);
        var line = document.createElement("div");
        line.className = "sc-scope-row" + (scope && scope.active_scan ? " is-active" : "");

        var sName = document.createElement("strong");
        sName.textContent = scope ? scope.name : "授权范围";
        line.appendChild(sName);

        var targets = document.createElement("span");
        targets.className = "sc-scope-targets";
        targets.textContent = describeScopeTargets(scope);
        line.appendChild(targets);

        var state = document.createElement("span");
        state.className = "badge " + (scope && scope.active_scan ? "badge-ok" : "badge-warn");
        state.textContent = scopeStateLabel(scope);
        line.appendChild(state);

        row.appendChild(line);
      });

      box.appendChild(row);
    });
  }

  function fillProjectSelects(projects) {
    var select = $("job-project");
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
    // 列表按创建时间倒序，默认选最近创建的那个。
    select.value = current || projects[0].id;
    syncJobScopes();
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

  // ── 步骤 1：检查授权（只读试算） ─────────────────────────

  /** 一个目标**被服务端放行**的授权资产 ID。
   *
   *  直接读试算响应里的 ``eligible_scope_ids`` —— 那是服务端按
   *  ``Scope.match_target`` 算完的结论（``verdict=allowed``），前端不再自己
   *  从 ``matches[]`` 里重算一遍：重算就是第二条授权判定，改一处漏一处。
   */
  function eligibleScopeIds(check) {
    return (check.eligible_scope_ids || []).slice();
  }

  /** 这条 ``matches[]`` 条目是否属于**放行**结论（按服务端的 ID 集合判，不重算 verdict）。 */
  function isEligibleMatch(check, match) {
    return eligibleScopeIds(check).indexOf(match.scope_id) !== -1;
  }

  /**
   * 所有有效目标**共同**命中的那一个授权资产；没有唯一答案时返回 ``null``。
   *
   * 取交集而不是取第一个：三个目标分别落在两个资产上时，随便挑一个会让用户
   * 提交时撞 403（服务端按项目校验范围），却在页面上看不出原因。
   */
  function commonEligibleScope(payload) {
    var checks = (payload.checks || []).filter(function (check) {
      return check.valid;
    });
    if (!checks.length) return null;
    var common = eligibleScopeIds(checks[0]);
    checks.slice(1).forEach(function (check) {
      var ids = eligibleScopeIds(check);
      common = common.filter(function (id) {
        return ids.indexOf(id) !== -1;
      });
    });
    return common.length === 1 ? common[0] : null;
  }

  /**
   * 方案第 6 节：系统后台自动判断目标属于哪份授权资产（``resolve_scope(target)``）。
   *
   * 四条口径，改之前先读：
   *
   * * **不是扩大授权范围**（方案第 11 节⛔列表）。目标集合一字未改，只是把
   *   **用户自己已经建好的**、且服务端已经判定覆盖该目标的那个授权资产**选中**，
   *   省掉「再选一次」的动作。真正的判定仍是服务端的 Scope / Policy，
   *   方案第 6 节「**禁止**为了体验删除 Scope 校验」一字未动。
   * * **只认服务端结论**。候选直接取试算响应的 ``eligible_scope_ids`` ——
   *   那是服务端按 ``Scope.match_target`` 算完的 ``verdict=allowed`` 集合；
   *   前端绝不自己比较 ``allowed_domains`` / ``active_scan`` 之类的字段，
   *   那就成了第二条授权判定。
   * * **有歧义就不选**。多个目标命中不同资产、或一个目标命中多个资产时保持用户
   *   当前选择并如实说明，不替他猜一个。
   * * **不覆盖用户的显式选择**。当前选中的资产本身就是那个答案时原样保留 ——
   *   自动匹配是省一步，不是把用户刚改的选择顶回去。
   *
   * @returns {string} ``selected`` / ``kept`` / ``ambiguous`` / ``none``（用于给出一句说明）。
   */
  function applyMatchedScope(payload) {
    var scopeId = commonEligibleScope(payload);
    if (!scopeId) {
      // 有放行结论但不是唯一答案 → 说清楚「请你来选」；一个都没放行 →
      // 卡片上已经写明缺哪一道闸门，这里不再重复。
      var anyEligible = (payload.checks || []).some(function (check) {
        return check.valid && eligibleScopeIds(check).length > 0;
      });
      return anyEligible ? "ambiguous" : "none";
    }

    var scopeSelect = $("job-scope");
    if (!scopeSelect) return "none";
    if (scopeSelect.value === scopeId) return "kept"; // 用户已经选的就是它

    // 授权资产必须挂在某个授权项目下（服务端要求 scope ∈ project.scope_ids），
    // 因此先把项目切过去、重建范围下拉框，再选中那一个范围。
    var owner = null;
    ((metadata && metadata.projects) || []).forEach(function (project) {
      if (!owner && (project.scope_ids || []).indexOf(scopeId) !== -1) owner = project;
    });
    if (!owner) return "none"; // 命中但没挂到任何项目：服务端会拒，页面已如实说明

    var projectSelect = $("job-project");
    if (projectSelect) {
      projectSelect.value = owner.id;
      syncJobScopes();
    }
    scopeSelect.value = scopeId;
    refreshConsentScopeLine();
    return "selected";
  }

  /** 自动匹配结果 → 一句给用户看的话（空字符串表示不需要额外说明）。 */
  function autoScopeNote(outcome) {
    if (outcome === "selected") {
      return "已自动选中覆盖该目标的授权资产 —— 可在步骤 2 改选。";
    }
    if (outcome === "ambiguous") {
      return "多个目标命中的授权资产不一致，请在步骤 2 自行选择。";
    }
    return "";
  }

  function renderCheckResults(payload) {
    var box = $("check-result");
    if (!box) return;
    box.textContent = "";

    var needsScope = false;

    (payload.checks || []).forEach(function (check) {
      var card = document.createElement("div");
      card.className = "sc-check" + (check.ready ? " is-ready" : " is-blocked");

      var head = document.createElement("div");
      head.className = "sc-check-head";
      var target = document.createElement("strong");
      target.textContent = check.valid ? check.normalized : check.raw;
      head.appendChild(target);

      var badge = document.createElement("span");
      badge.className = "badge " + (check.ready ? "badge-ok" : "badge-warn");
      badge.textContent = check.ready ? "可以扫" : "还不能扫";
      head.appendChild(badge);
      card.appendChild(head);

      if (!check.valid) {
        card.appendChild(hint("目标格式不合法：" + check.error_message, "sc-check-note"));
      } else {
        // 命中哪些范围 —— 只显示放行的；被排除的单独说明（它有诊断价值）。
        // 「放行」的判断同样读服务端的 ``eligible_scope_ids``（见 eligibleScopeIds），
        // 不在这里重算一遍 verdict —— 第二条授权判定就是「改一处漏一处」的来源。
        var eligible = (check.matches || []).filter(function (item) {
          return isEligibleMatch(check, item);
        });
        if (eligible.length) {
          eligible.forEach(function (match) {
            var line = document.createElement("div");
            line.className = "sc-check-match";
            var scopeName = document.createElement("strong");
            scopeName.textContent = match.scope_name;
            line.appendChild(scopeName);

            var where = document.createElement("span");
            where.className = "sc-scope-targets";
            where.textContent = match.project_name
              ? "项目 " + match.project_name
              : "未挂到任何项目";
            line.appendChild(where);

            var state = document.createElement("span");
            state.className = "badge " + (match.status === "ready" ? "badge-ok" : "badge-warn");
            state.textContent = STATUS_LABELS[match.status] || match.status;
            line.appendChild(state);
            card.appendChild(line);
          });
        } else {
          card.appendChild(
            hint(
              check.blocker === "no_scope"
                ? "还没有任何授权范围 —— 先在下面建一个项目，再把目标加进去。"
                : "这个目标不在任何已授权范围内。",
              "sc-check-note"
            )
          );
        }

        (check.matches || [])
          .filter(function (item) {
            return item.status === "excluded";
          })
          .forEach(function (match) {
            card.appendChild(
              hint("注意：" + match.scope_name + " 把它列在排除列表里 —— 排除优先。", "sc-check-note")
            );
          });

        if (!check.ready) {
          card.appendChild(
            hint(
              "还差一步：" + (BLOCKER_LABELS[check.blocker] || check.blocker),
              "sc-check-note sc-check-blocker"
            )
          );
        }
        if (check.resolved_check_deferred) {
          card.appendChild(
            hint(
              "说明：这里只按授权范围的字面匹配判断，执行前还会做一次真实 DNS 解析校验。",
              "sc-check-note"
            )
          );
        }
      }

      if (!check.ready) needsScope = true;
      box.appendChild(card);
    });

    // 建议模式：能扫就默认真实，不能扫就别让用户对着红灯发愣。
    var mock = $("job-mock");
    if (mock && payload.suggested_mode) {
      mock.checked = payload.suggested_mode !== "real";
    }

    var createBox = $("scope-create");
    if (createBox) createBox.hidden = !needsScope;

    lastCheckPayload = payload;
    // 方案第 6 节：试算完就把「目标属于哪份授权资产」自动选好（见 applyMatchedScope）。
    // 顺序很重要：先自动匹配，再渲染摘要 —— 否则摘要显示的还是上一个选中项。
    var autoOutcome = applyMatchedScope(payload);
    renderConsentSummary();

    setText(
      "check-summary",
      payload.summary.ready + " / " + payload.summary.total + " 个目标现在可以真实扫描。" +
        (payload.summary.blocked ? "其余 " + payload.summary.blocked + " 个还缺条件，见下方说明。" : "") +
        (autoScopeNote(autoOutcome) ? " " + autoScopeNote(autoOutcome) : ""),
      false
    );
  }

  // ── 步骤 2：授权状态摘要（方案第 7 节） ─────────────────

  /**
   * 把「目标 / 授权状态 / 授权资产」三行摘要写进步骤 2。
   *
   * 口径：**只回显，不判定**。授权状态文案来自服务端试算结果的 ``blocker`` 与
   * ``ready``（见 :data:`BLOCKER_LABELS`），本函数不做任何比较；没有试算结果时
   * 退回「还没填」，而不是自己猜一个状态。
   *
   * 授权资产这一行只显示名称与目标，**不显示 scope_id**
   * —— 与页面其它位置同一口径（Phase 1「隐藏 Scope ID」）。
   */
  function renderConsentSummary() {
    var targetBox = $("consent-target");
    var statusBox = $("consent-status");
    var scopeBox = $("consent-scope");

    if (targetBox) {
      // 显示**当前输入框里的内容**，而不是上一次试算的那份快照 ——
      // 摘要回答的是「我这次要提交什么」，输入框就是唯一事实来源。
      var shown = currentTargets();
      targetBox.textContent = shown.length ? shown.join("、") : "（还没填）";
    }

    var check = lastCheckPayload && (lastCheckPayload.checks || [])[0];
    if (statusBox) {
      if (!check) {
        statusBox.textContent = "（还没检查）";
      } else if (check.ready) {
        statusBox.textContent = "已授权，可以真实扫描";
      } else {
        statusBox.textContent =
          "还不能扫 —— " + (BLOCKER_LABELS[check.blocker] || check.blocker || "未知原因");
      }
    }

    if (scopeBox) {
      var matched = check
        ? (check.matches || []).filter(function (item) {
            return isEligibleMatch(check, item);
          })
        : [];
      if (matched.length) {
        scopeBox.textContent = matched
          .map(function (item) {
            return item.scope_name + " · " + describeScopeTargets(item);
          })
          .join("；");
      } else {
        // 试算没命中时，退回到「当前下拉框里选中的那一个」——
        // 它是用户自己选的授权依据，同样要能看见。
        var select = $("job-scope");
        var selected = select && select.value ? scopeById(select.value) : null;
        scopeBox.textContent = selected
          ? selected.name + " · " + describeScopeTargets(selected)
          : "（还没选）";
      }
    }
  }

  /** 下拉框换选后，摘要里的「授权资产」也要跟着变。 */
  function refreshConsentScopeLine() {
    if (!lastCheckPayload) return;
    var check = (lastCheckPayload.checks || [])[0];
    var matched = check
      ? (check.matches || []).filter(function (item) {
          return isEligibleMatch(check, item);
        })
      : [];
    if (matched.length) return; // 试算已经给了答案，不用下拉框覆盖它
    var scopeBox = $("consent-scope");
    if (!scopeBox) return;
    var select = $("job-scope");
    var selected = select && select.value ? scopeById(select.value) : null;
    scopeBox.textContent = selected
      ? selected.name + " · " + describeScopeTargets(selected)
      : "（还没选）";
  }

  function bindCheckForm() {
    var form = $("check-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var targets = splitList(form.targets.value);
      if (!targets.length) {
        setText("check-summary", "请先填写至少一个目标。", true);
        return;
      }
      lastTargets = targets;
      setText("check-summary", "正在检查…", false);

      postJSON("/api/public-jobs/check", {
        targets: targets,
        project_id: ($("job-project") && $("job-project").value) || null,
      }).then(function (result) {
        if (!result.ok) {
          setText("check-summary", "检查失败: " + describeError(result), true);
          return;
        }
        renderCheckResults(result.data);
      });
    });

    // 改了目标就让上一次的结论失效（见 checkIsFresh / invalidateCheckResult）。
    // 没有这个监听器时，「检查授权 → 改输入框 → 直接创建任务」提交的是
    // 改前的目标，而页面上的绿灯说的是改后的那个站 —— 服务端按改前的判。
    var input = $("job-target");
    if (input) {
      input.addEventListener("input", function () {
        if (lastCheckPayload || lastTargets.length) invalidateCheckResult();
      });
    }
  }

  // ── 步骤 2：创建项目 / 添加范围 ──────────────────────────

  function bindProjectForm() {
    var form = $("project-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      postJSON("/api/projects", {
        name: form.name.value.trim(),
        authorization_note: form.authorization_note.value.trim(),
        owner: form.owner.value.trim(),
      }).then(function (result) {
        if (!result.ok) {
          setText("check-summary", "创建项目失败: " + describeError(result), true);
          return;
        }
        var project = result.data.project;
        form.reset();
        loadMetadata().then(function () {
          var select = $("job-project");
          if (select) select.value = project.id;
          syncJobScopes();
          // 建完项目接着就要加范围：把试算过的目标预填进去，并展开这一段。
          prefillScopeTargets();
          var add = $("scope-add");
          if (add) add.open = true;
          setText(
            "check-summary",
            "已创建授权项目「" + project.name + "」。接着在下面填授权域名或网段。",
            false
          );
        });
      });
    });
  }

  /** 把试算过的目标预填进「新增范围」表单 —— 用户不必再抄一遍。 */
  function prefillScopeTargets() {
    var domains = [];
    var cidrs = [];
    lastTargets.forEach(function (raw) {
      var text = String(raw).trim();
      if (!text) return;
      if (text.indexOf("/") !== -1) cidrs.push(text);
      else domains.push(text);
    });
    var domainBox = $("scope-domains");
    var cidrBox = $("scope-cidrs");
    if (domainBox && domains.length && !domainBox.value) domainBox.value = domains.join(", ");
    if (cidrBox && cidrs.length && !cidrBox.value) cidrBox.value = cidrs.join(", ");
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
      var projectId = (form.project_id && form.project_id.value) || "";
      var domains = splitList(form.allowed_domains.value);
      var cidrs = splitList(form.allowed_cidrs.value);

      if (!projectId) {
        setText("scope-feedback", "还没有授权项目，请先在上面的表单里创建一个。", true);
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
          // 范围变了，之前那次试算的结论就过期了 —— 自动重算一遍，
          // 让用户立刻看到「现在能不能扫」。
          // 判据用**当前输入**而不是 lastTargets：输入框改过之后旧快照已被
          // invalidateCheckResult() 清空，用快照判会在这时静默跳过重算。
          loadMetadata().then(function () {
            var checkForm = $("check-form");
            if (checkForm && currentTargets().length) {
              checkForm.dispatchEvent(new Event("submit", { cancelable: true }));
            }
          });
        });
      });
    });
  }

  function bindProjectSelectSync() {
    var select = $("job-project");
    if (select) {
      select.addEventListener("change", function () {
        syncJobScopes();
        refreshConsentScopeLine();
      });
    }
    var scopeSelect = $("job-scope");
    if (scopeSelect) {
      scopeSelect.addEventListener("change", refreshConsentScopeLine);
    }
  }

  // ── 步骤 4：创建任务 ────────────────────────────────────

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
      // 提交的永远是**输入框此刻的内容**，不是上一次试算的快照。
      // 之前这里优先用 lastTargets，导致「检查授权 → 改输入框 → 创建任务」
      // 提交的是改前的目标，而页面上的授权状态说的是改后的那个站。
      var targets = currentTargets();
      if (!targets.length) {
        setText("job-feedback", "请先在步骤 1 填写目标。", true);
        return;
      }
      // 有一份对不上当前输入的试算结论 → 让用户先重新检查（正常情况下
      // input 监听器已经把旧结论作废，这里是兜底）。
      if (lastCheckPayload && !checkIsFresh()) {
        setText("job-feedback", "目标已改动，请先重新点「检查授权」再创建任务。", true);
        return;
      }

      // 步骤 2 的授权确认（方案第 7 节）：**使用者确认，不是安全边界**。
      // 它只在这里挡住「手滑直接提交」，不改变服务端的任何判定 ——
      // 勾上它不会让越界目标通过，因为它根本参与不了那几条闸门。
      var consent = $("job-consent");
      if (consent && !consent.checked) {
        setText(
          "job-feedback",
          "请先在步骤 2 勾选「我确认该目标属于授权范围」，再创建任务。",
          true
        );
        return;
      }

      var projectSelect = $("job-project");
      var scopeSelect = $("job-scope");
      var operator = $("job-operator");
      var payload = {
        project_id: (projectSelect && projectSelect.value) || "",
        scope_id: (scopeSelect && scopeSelect.value) || "",
        targets: targets,
        strategy: ACTIVE_STRATEGY,
        // 节奏随请求带上：服务端仍会按「模板档位 + 请求档位」取更保守的一档，
        // 前端给错也放松不了任何东西；带上只是让请求与页面显示同一个值。
        pace: ACTIVE_PACE,
        // 使用者确认的原始事实（服务端只用于审计记录，不参与任何判定）。
        authorization_confirmed: Boolean(consent && consent.checked),
      };
      // 操作者标识（Phase 3「操作者记录」）：留空就不加这个键，由服务端
      // 退化成缺省标识 —— 传空串只是绕一圈回到同一个结论，还会让「用户没填」
      // 与「用户填了空」在审计里长得一模一样。
      var operatorName = operator && String(operator.value || "").trim();
      if (operatorName) payload.operator = operatorName;
      // 限速 / 超时：只在真的填了数字时才带上（见 collectLimits 的说明）。
      var limits = collectLimits();
      Object.keys(limits).forEach(function (key) {
        payload[key] = limits[key];
      });
      if (ACTIVE_STRATEGY === "custom") payload.tools = selectedCustomTools();
      if ($("job-mock") && $("job-mock").checked) payload.mode = "mock";

      postJSON("/api/public-jobs", payload).then(function (result) {
        if (!result.ok) {
          setText("job-feedback", "创建失败: " + describeError(result), true);
          return;
        }
        var data = result.data;
        setText(
          "job-feedback",
          "任务已创建：" + data.job_id + "（" + data.status + "，模式 " + data.mode +
            "，节奏 " + (data.pace_label || data.pace || ACTIVE_PACE) +
            "，共 " + data.total_steps + " 步）。worker 会异步执行。",
          false
        );
        // 新任务要出现在列表里：整页刷新最省事，也保证表格数据来自服务端。
        window.location.href = "/scan-center?job_id=" + encodeURIComponent(data.job_id);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("strategy-list")) return; // 不是扫描中心页
    bindCheckForm();
    bindProjectForm();
    bindScopeForm();
    bindJobForm();
    bindProjectSelectSync();
    loadMetadata();
  });
})();
