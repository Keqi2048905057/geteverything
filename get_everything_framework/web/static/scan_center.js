/* Get Everything Framework — 扫描中心前端
 *
 * 页面上的三步（测试阶段口径）：
 *
 *   步骤 1 · 确认目标  → 填目标，点「确认目标」调 POST /api/public-jobs/check，
 *                        逐个回报格式是否合法、规范化结果、是否命中排除列表
 *   步骤 2 · 选择工具  → 选模板（资产发现 / Web 基础检查 / 自定义）作为**推荐组合**，
 *                        下面每个已接入的工具都可以自由勾选
 *   步骤 3 · 创建任务  → mock 或 real、限速 / 超时 → 创建任务
 *
 * ── 与上一版的差别（测试阶段改造） ──────────────────────────────
 * 授权业务整体取消，因此这一版**删掉**了：授权项目列表、项目下的范围下拉、
 * 「新增授权范围」表单、「我确认该目标属于授权范围」勾选、以及把 scope_id /
 * project_id 随请求发出去的那两行。步骤 1 也从「检查授权 / 还缺哪一道闸门」
 * 改成纯粹的**目标格式预检** —— 后端 /api/public-jobs/check 已经不再返回
 * `eligible_scope_ids` / `blocker` / `matches`，前端自然也不该再解释它们。
 *
 * 删除的是**授权业务流程**，不是安全边界：目标格式、排除列表、SSRF 防护、
 * 限速 / 超时 / 并发、审计与结构化日志全部留在服务端，一条没少。
 *
 * ── 工具为什么可以自由勾选 ────────────────────────────────────
 * 服务端下发的每个工具条目带 `selectable`：**已注册且已接入 runner** 的工具
 * 才为 true。前端据此决定勾选框能不能点 —— 这是**可用性**提示，不是安全边界：
 * 就算有人手改 DOM 把 `nuclei` 勾上并提交，服务端
 * `assert_tools_selectable()` 仍会在创建任务**之前**拒绝它。
 * （旧字段名 `internet_allowed` 是 deprecated 兼容键，前端不再使用。）
 *
 * ── Scan Profile = 工具组合 + 节奏（`core/pace.py`） ───────────
 * 节奏是「这一次打得多快」，不是权限：
 *  * `light`  低频 —— 后端把并发与每秒请求数压下来，并在真实步骤之间留间隔；
 *  * `normal` 常规 —— 完全沿用工具自身配置（历史行为）。
 * 前端的责任只有两件：**如实显示**，以及**原样转发**。合并规则（模板档位与
 * 请求档位取更保守的一档）只在服务端实现一次，因此这里把 pace 改错也放松不了
 * 任何东西 —— 这正是「前端不是安全边界」的既有约定。
 *
 * ── 模板与自由勾选如何共存 ────────────────────────────────────
 * 点一张模板卡片 = 把它的工具**勾上**（仍然可以再改），而不是把清单锁死。
 * 提交时前端比一次集合：
 *
 *   * 勾选结果与当前模板的工具**一致**   → 原样提交该模板，不传 tools；
 *   * 勾选结果被改过（或选的就是自定义） → 提交 `strategy=custom` + `tools=[…]`。
 *
 * 这样「用户实际会跑哪些工具」与「审计里记的策略名」永远对得上：改过就说
 * 自定义，没改就记模板名。判定仍在服务端（`resolve_strategy_tools`）。
 *
 * ── 展示口径 ──────────────────────────────────────────────────
 * 工具清单、能力分组、每个工具的用途与风险等级、限速上下界、模板说明**全部**
 * 来自 `GET /api/scan-center`：前端一个工具名、一个栏位名都不写死，后端加一个
 * 工具页面就自动多一行。模板说明的唯一来源是 `strategies[].description`。
 *
 * ── 安全约定 ──────────────────────────────────────────────────
 *   * 每一个提交动作都只是**转发**到 API，闸门全部在服务端
 *     （工具是否已接入 → Policy / 模式开关）；
 *   * 被禁用的工具在界面上置灰并写明原因；
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
  /** 上一次预检的原始目标，用于判断「这份结论是不是还对得上输入框」。 */
  var lastTargets = [];
  /** 上一次预检的完整响应（只用于回显，不参与任何判定）。 */
  var lastCheckPayload = null;

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

  /** 两个数组是否**同集合**（顺序无关，用于「勾选结果 vs 模板工具」的比较）。 */
  function sameSet(a, b) {
    var left = (a || []).slice().sort();
    var right = (b || []).slice().sort();
    if (left.length !== right.length) return false;
    for (var i = 0; i < left.length; i += 1) {
      if (left[i] !== right[i]) return false;
    }
    return true;
  }

  /** 目标输入框**此刻**的内容（用户可能刚改过，与上一次预检无关）。 */
  function currentTargets() {
    var input = $("job-target");
    return splitList(input ? input.value : "");
  }

  /**
   * 上一次预检结果是否仍然对应当前输入。
   *
   * 预检结论是**针对某一组目标**算出来的；用户改完输入框却没重新点「确认目标」时，
   * 那份结论就只是一段历史。页面若继续拿它当依据，就会出现「摘要说 A 合法、
   * 实际提交的是 B」这种最难排查的错。
   */
  function checkIsFresh() {
    if (!lastCheckPayload || !lastTargets.length) return false;
    return currentTargets().join("\u0000") === lastTargets.join("\u0000");
  }

  /** 输入变了：把上一次预检的结论作废，并如实告诉用户要重新确认。 */
  function invalidateCheckResult() {
    lastCheckPayload = null;
    lastTargets = [];
    var box = $("check-result");
    if (box) box.textContent = "";
    setText("check-summary", "目标已改动，请重新点「确认目标」。", false);
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
   * 按服务端下发的 ``limits`` 元数据渲染限速 / 超时输入框。
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

  // 服务端统一的错误体形状：error_code + error_message（+ details）。
  function describeError(result) {
    var data = result.data || {};
    var message = data.error_message || ("HTTP " + result.status);
    var details = data.details || {};

    // 未接入 / 未登记的工具。``unavailable_tools`` 是当前字段名，
    // ``blocked_tools`` 是 deprecated 兼容字段（旧服务端只给后者）。
    var unavailable = details.unavailable_tools || details.blocked_tools || [];
    if (unavailable.length) {
      var names = unavailable.map(function (item) {
        if (typeof item === "string") return item;
        return item.tool_name + "（" + (item.reason || item.risk_level) + "）";
      });
      message += "；不可用工具: " + names.join("、");
    }
    if (details.unknown_tools && details.unknown_tools.length) {
      message += "；未登记工具: " + details.unknown_tools.join("、");
    }
    return message;
  }

  // ── 元数据（策略 / 工具 / 分组 / 限速） ──────────────────

  function loadMetadata() {
    return getJSON("/api/scan-center")
      .catch(function (err) {
        // 未登录：页面已有提示，静默停手。
        if (String(err.message).indexOf("401") !== -1) return null;
        throw err;
      })
      .then(function (center) {
        if (!center) return;
        metadata = center;
        paceIndex = {};
        (center.paces || []).forEach(function (item) {
          paceIndex[item.pace] = item;
        });
        renderStrategies(center.strategies, center.restricted_tools);
        // 工具清单跟着**当前选中**的模板渲染：首屏就要能看见「这一次会用哪些工具」。
        // 分组元数据同样来自服务端（``tool_groups``），前端不写死任何栏位名。
        var active = activeStrategyOf(center.strategies);
        renderToolList(center.tools, center.restricted_tools, center.tool_groups, presetOf(active));
        // 限速 / 超时的输入框同样由服务端元数据生成。
        renderLimits(center.limits);
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

  /** 模板 → 「要预勾哪些工具」的集合；自定义模板或空模板返回 ``null``（用默认勾选）。 */
  function presetOf(strategy) {
    if (!strategy || strategy.key === "custom" || !(strategy.tools || []).length) return null;
    var preset = {};
    strategy.tools.forEach(function (name) {
      preset[name] = true;
    });
    return preset;
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
        // 模板只是**推荐组合**：点它就把它的工具勾上，之后仍然可以自由增删。
        if (metadata) {
          renderToolList(
            metadata.tools,
            metadata.restricted_tools,
            metadata.tool_groups,
            presetOf(strategy)
          );
        }
      });
      box.appendChild(label);
    });

    // 首屏（还没点过任何卡片）也要显示**当前选中那一个**的说明与节奏，
    // 而不是 HTML 里写死的一句 —— 写死的文案会随着模板改动悄悄过期。
    var active = activeStrategyOf(strategies);
    if (active) {
      ACTIVE_PACE = active.pace || ACTIVE_PACE;
      setText("strategy-note", active.name + "：" + active.description, false);
    }
  }

  /**
   * 渲染**一个**工具条目（勾选框 + 名称 + 风险徽标 + 用途说明 + 可用性说明）。
   *
   * 抽成独立函数是因为清单要按能力分组渲染多次；条目形态只有一份，
   * 因此不会出现「分组里长一个样、扁平列表里长另一个样」。
   *
   * @param {Object} tool 服务端下发的工具条目（``tool_name`` / ``selectable`` …）。
   * @param {Object|null} preset 模板预设集合；``null`` 表示按 ``default_enabled`` 勾选。
   */
  function renderToolRow(tool, preset) {
    var selectable = Boolean(tool.selectable);
    var row = document.createElement("label");
    row.className = "sc-tool" + (selectable ? "" : " is-blocked");

    var check = document.createElement("input");
    check.type = "checkbox";
    check.name = "custom_tool";
    check.value = tool.tool_name;
    // 未接入的工具永远不可勾 —— 这是**可用性**提示，真正的闸门是服务端的
    // ``assert_tools_selectable()``（手改 DOM 也过不去）。
    check.disabled = !selectable;
    if (selectable) {
      check.checked = preset ? Boolean(preset[tool.tool_name]) : Boolean(tool.default_enabled);
    }
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
    if (!selectable) {
      // 未接入的（如 nuclei）如实说明原因，不让用户以为是界面漏了。
      meta.textContent = "未接入 · " + (tool.reason || "本阶段不可用");
    } else if (tool.default_enabled) {
      meta.textContent = "默认勾选 · 可自由增删";
    } else {
      meta.textContent = "可勾选";
    }
    body.appendChild(meta);
    row.appendChild(body);

    return row;
  }

  /**
   * 渲染工具清单（能力分组 + 每个工具一行）。
   *
   * 关键约定：**工具清单与能力分组都来自服务端**（``GET /api/scan-center`` 的
   * ``tool_groups``；每一栏的说明、每个工具的用途都出自同一份
   * ``ToolPolicy.to_dict()``）。前端一个工具名、一个栏位名都不写死 ——
   * 新增工具或调整分组时改后端即可，页面自动出现。
   *
   * 分组为空时**照样渲染栏位**并写明「本阶段暂无可用工具」：如实呈现比悄悄藏掉
   * 更诚实 —— 藏掉会让使用者以为是自己没找到。
   *
   * ``nuclei`` 这类未接入的工具由服务端放在 ``restricted_tools`` 里，归到它
   * **自己声明的**分组（``tool_group``）下并置灰，不写死任何映射。
   *
   * @param {Array} tools 服务端下发的工具表（含 ``tools`` / ``selectable`` …）。
   * @param {Array} restrictedTools 未接入的工具（如 nuclei），只展示、不可勾。
   * @param {Array} groups 服务端下发的能力分组（``key`` / ``name`` / ``description`` / ``tools``）。
   *   缺失时退回扁平清单，行为与引入分组之前一致。
   * @param {Object|null} preset 模板预设集合；``null`` 表示按各自 ``default_enabled`` 勾选。
   */
  function renderToolList(tools, restrictedTools, groups, preset) {
    var box = $("tool-list");
    if (!box) return;
    box.textContent = "";

    if (groups && groups.length) {
      var placed = {};
      var restricted = restrictedTools || [];

      groups.forEach(function (group) {
        // 未接入的工具也归到它**自己声明的**分组里：服务端给每条 restricted tool
        // 也带上 ``tool_group``，因此这里不需要写死任何「nuclei 属于漏洞检测」映射。
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
          count.textContent = "本阶段暂无可用工具（仅列出未接入项）";
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
          grid.appendChild(renderToolRow(tool, preset));
        });
        unopened.forEach(function (tool) {
          placed[tool.tool_name] = true;
          grid.appendChild(renderToolRow(tool, preset));
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
          restGrid.appendChild(renderToolRow(tool, preset));
        });
        box.appendChild(restGrid);
      }
      return;
    }

    // 没有分组元数据（更早的服务端响应）：退回扁平清单。
    var flat = document.createElement("div");
    flat.className = "sc-tools";
    tools.concat(restrictedTools || []).forEach(function (tool) {
      flat.appendChild(renderToolRow(tool, preset));
    });
    box.appendChild(flat);
  }

  /** 当前勾选（且可勾）的工具名。 */
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

  // ── 步骤 1：确认目标（只读格式预检） ─────────────────────

  var CHECK_LABELS = {
    allowed: "格式合法，可以提交",
    excluded: "命中排除列表，不会被执行",
    invalid: "格式不合法",
  };

  function renderCheckResults(payload) {
    var box = $("check-result");
    if (!box) return;
    box.textContent = "";

    (payload.checks || []).forEach(function (check) {
      var card = document.createElement("div");
      card.className = "sc-check" + (check.valid && !check.excluded ? " is-ready" : " is-blocked");

      var head = document.createElement("div");
      head.className = "sc-check-head";
      var target = document.createElement("strong");
      target.textContent = check.valid ? check.normalized : check.raw;
      head.appendChild(target);

      var badge = document.createElement("span");
      badge.className = "badge " + (check.valid && !check.excluded ? "badge-ok" : "badge-warn");
      badge.textContent = check.valid ? (check.excluded ? "已排除" : "可以提交") : "格式不合法";
      head.appendChild(badge);
      card.appendChild(head);

      if (check.valid) {
        var kind = document.createElement("span");
        kind.className = "sc-target-meta";
        kind.textContent =
          (check.kind ? "识别为 " + check.kind : "") +
          (check.normalized && check.normalized !== check.raw ? " · 原始输入 " + check.raw : "");
        card.appendChild(kind);
      }

      // 说明文案来自服务端（``message``）；前端只做兜底，不自己拼判定。
      card.appendChild(
        hint(check.message || CHECK_LABELS[check.verdict] || "", "sc-check-note")
      );
      box.appendChild(card);
    });

    var summary = payload.summary || {};
    var ready = summary.ready || 0;
    var total = summary.total || 0;
    var parts = [ready + " / " + total + " 个目标格式合法、可以提交"];
    if (summary.invalid) {
      parts.push("其中 " + summary.invalid + " 个格式不合法，请先修正再提交。");
    }
    if (summary.excluded) {
      parts.push("另有 " + summary.excluded + " 个命中排除列表，提交后不会被执行。");
    }
    setText("check-summary", parts.join(" "), false);

    // 建议模式：全部合法就默认真实扫描；有不合法的目标时不要让人对着红灯发愣。
    var mock = $("job-mock");
    if (mock && payload.suggested_mode) {
      mock.checked = payload.suggested_mode !== "real";
    }

    lastCheckPayload = payload;
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

      // 只传 targets：这一版不再有项目 / 范围，服务端也只做格式与排除列表预检。
      postJSON("/api/public-jobs/check", { targets: targets }).then(function (result) {
        if (!result.ok) {
          setText("check-summary", "检查失败: " + describeError(result), true);
          return;
        }
        renderCheckResults(result.data);
      });
    });

    // 改了目标就让上一次的结论失效（见 checkIsFresh / invalidateCheckResult）。
    // 没有这个监听器时，「确认目标 → 改输入框 → 直接创建任务」提交的是改前的目标，
    // 而页面上的结论说的是改后的那个 —— 服务端按改前的判。
    var input = $("job-target");
    if (input) {
      input.addEventListener("input", function () {
        if (lastCheckPayload || lastTargets.length) invalidateCheckResult();
      });
    }
  }

  // ── 步骤 2 联动：自定义卡片下的清单也要能勾 ──────────────

  function bindToolList() {
    // 事件委托：勾选框是动态生成的，绑定在容器上即可。
    // 这里**不做**任何校验 —— 只把「勾选结果与模板是否一致」这件事留给提交时判断。
    var box = $("tool-list");
    if (!box) return;
    box.addEventListener("change", function () {
      var chosen = selectedCustomTools();
      var active = activeStrategyOf((metadata && metadata.strategies) || []);
      var preset = active && active.key !== "custom" ? active.tools || [] : null;
      if (preset && !sameSet(chosen, preset)) {
        setText("tool-list-note", "已修改模板的工具组合，提交时将按「自定义」记录。", false);
      } else if (!preset) {
        setText("tool-list-note", "自定义模式：勾选要用的工具（未接入的工具无法勾选）。", false);
      } else {
        setText("tool-list-note", "本次会用到的工具（与所选模板一致）。", false);
      }
    });
  }

  // ── 步骤 3：创建任务 ───────────────────────────────────

  function bindJobForm() {
    var form = $("job-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      // 提交的永远是**输入框此刻的内容**，不是上一次预检的快照。
      var targets = currentTargets();
      if (!targets.length) {
        setText("job-feedback", "请先在步骤 1 填写目标。", true);
        return;
      }
      // 有一份对不上当前输入的预检结论 → 让用户先重新确认（正常情况下
      // input 监听器已经把旧结论作废，这里是兜底）。
      if (lastCheckPayload && !checkIsFresh()) {
        setText("job-feedback", "目标已改动，请先重新点「确认目标」再创建任务。", true);
        return;
      }

      var chosen = selectedCustomTools();
      var strategy = ACTIVE_STRATEGY;
      var active = activeStrategyOf((metadata && metadata.strategies) || []);
      var preset = active && active.key !== "custom" ? active.tools || [] : null;

      // 勾选结果被改过（或本来选的就是自定义）→ 按自定义提交，并把工具名列出来。
      // 这样审计里记的策略名与「用户实际会跑哪些工具」永远对得上。
      if (!preset || !sameSet(chosen, preset)) {
        strategy = "custom";
      }

      var operator = $("job-operator");
      var payload = {
        targets: targets,
        strategy: strategy,
        // 节奏随请求带上：服务端仍会按「模板档位 + 请求档位」取更保守的一档，
        // 前端给错也放松不了任何东西；带上只是让请求与页面显示同一个值。
        pace: ACTIVE_PACE,
      };
      if (strategy === "custom") payload.tools = chosen;
      // 操作者标识：留空就不加这个键，由服务端退化成缺省标识 —— 传空串只是绕一圈
      // 回到同一个结论，还会让「用户没填」与「用户填了空」在审计里长得一模一样。
      var operatorName = operator && String(operator.value || "").trim();
      if (operatorName) payload.operator = operatorName;
      // 限速 / 超时：只在真的填了数字时才带上（见 collectLimits 的说明）。
      var limits = collectLimits();
      Object.keys(limits).forEach(function (key) {
        payload[key] = limits[key];
      });
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
    bindToolList();
    bindJobForm();
    loadMetadata();
  });
})();
