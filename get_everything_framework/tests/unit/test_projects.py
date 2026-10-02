"""授权测试项目的持久化与校验（方案第 4 节步骤 1、2）。

口径与 ``docs/DECISIONS.md`` 的 E 项一致：**只新增表**，``scopes`` 表零改动。
本文件同时锁死这一点 —— 项目的引入不得改变既有 Scope 的任何行为。
"""

import pytest

from core import projects, scope_store
from core.db import connect, db_path
from core.errors import BadRequestError, NotFoundError


@pytest.fixture
def scope_a(app_module):
    return scope_store.create(name="项目范围 A", allowed_domains=["a.example.test"]).id


@pytest.fixture
def scope_b(app_module):
    return scope_store.create(name="项目范围 B", allowed_domains=["b.example.test"]).id


def _make_project(**overrides):
    payload = {
        "name": "培正学院授权测试",
        "authorization_note": "2026-10-02 校方信息中心书面授权，仅被动信息收集",
        "owner": "张三",
    }
    payload.update(overrides)
    return projects.create(**payload)


# ── 创建 ─────────────────────────────────────────────────


def test_create_project_persists_and_returns(app_module):
    project = _make_project()

    assert project.id.startswith("proj_")
    assert project.name == "培正学院授权测试"
    assert project.owner == "张三"
    assert project.scope_ids == []

    fetched = projects.get(project.id)
    assert fetched is not None
    assert fetched.authorization_note == project.authorization_note


def test_create_project_requires_name(app_module):
    with pytest.raises(BadRequestError) as excinfo:
        _make_project(name="   ")
    assert excinfo.value.details["field"] == "name"


def test_create_project_requires_authorization_note(app_module):
    """授权说明是公网扫描的授权证据，不能留空。"""
    with pytest.raises(BadRequestError) as excinfo:
        _make_project(authorization_note="")
    assert excinfo.value.details["field"] == "authorization_note"


def test_create_project_rejects_too_short_note(app_module):
    with pytest.raises(BadRequestError, match="至少"):
        _make_project(authorization_note="ok")


def test_create_project_rejects_overlong_name(app_module):
    with pytest.raises(BadRequestError, match="最长"):
        _make_project(name="x" * 200)


def test_create_project_rejects_non_string_fields(app_module):
    with pytest.raises(BadRequestError):
        _make_project(name={"nested": "object"})


def test_owner_is_optional(app_module):
    project = _make_project(owner=None)
    assert project.owner == ""


def test_create_project_with_existing_scopes(app_module, scope_a, scope_b):
    project = _make_project(scope_ids=[scope_a, scope_b])
    assert project.scope_ids == [scope_a, scope_b]


def test_create_project_dedupes_scope_ids(app_module, scope_a):
    project = _make_project(scope_ids=[scope_a, scope_a])
    assert project.scope_ids == [scope_a]


def test_create_project_accepts_comma_separated_scope_ids(app_module, scope_a, scope_b):
    project = _make_project(scope_ids=f"{scope_a},{scope_b}")
    assert project.scope_ids == [scope_a, scope_b]


def test_create_project_rejects_unknown_scope(app_module):
    """挂着不存在的 Scope 的项目等于一张空授权书，必须拦住。"""
    with pytest.raises(BadRequestError, match="Scope 不存在"):
        _make_project(scope_ids=["scope_does_not_exist"])


def test_create_project_rejects_too_many_scopes(app_module, monkeypatch):
    monkeypatch.setattr(projects, "MAX_SCOPES_PER_PROJECT", 2, raising=False)
    with pytest.raises(BadRequestError, match="最多关联"):
        _make_project(scope_ids=["s1", "s2", "s3"])


# ── 读取 / 列表 ───────────────────────────────────────────


def test_get_missing_project_returns_none(app_module):
    assert projects.get("proj_missing") is None


def test_require_missing_project_raises_404(app_module):
    with pytest.raises(NotFoundError):
        projects.require("proj_missing")


def test_list_all_orders_by_created_at_desc(app_module):
    first = _make_project(name="第一个")
    second = _make_project(name="第二个")
    ids = [item.id for item in projects.list_all()]
    # 同一秒内创建时按 rowid 兜底，因此后建的排在前面。
    assert ids.index(second.id) < ids.index(first.id)


# ── 关联 Scope ───────────────────────────────────────────


def test_attach_scope_adds_relation(app_module, scope_a):
    project = _make_project()
    updated = projects.attach_scope(project.id, scope_a)
    assert updated.scope_ids == [scope_a]


def test_attach_scope_is_idempotent(app_module, scope_a):
    project = _make_project(scope_ids=[scope_a])
    again = projects.attach_scope(project.id, scope_a)
    assert again.scope_ids == [scope_a]
    # 不允许出现第二行关联记录。
    conn = connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM project_scopes WHERE project_id = ? AND scope_id = ?",
            (project.id, scope_a),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert count == 1


def test_attach_scope_requires_existing_scope(app_module):
    project = _make_project()
    with pytest.raises(BadRequestError, match="Scope 不存在"):
        projects.attach_scope(project.id, "scope_nope")


def test_attach_scope_requires_scope_id(app_module):
    project = _make_project()
    with pytest.raises(BadRequestError):
        projects.attach_scope(project.id, "  ")


def test_attach_scope_to_missing_project_is_404(app_module, scope_a):
    with pytest.raises(NotFoundError):
        projects.attach_scope("proj_missing", scope_a)


def test_find_by_scope_returns_project(app_module, scope_a):
    project = _make_project(scope_ids=[scope_a])
    found = projects.find_by_scope(scope_a)
    assert found is not None
    assert found.id == project.id


def test_find_by_scope_returns_none_for_unattached(app_module, scope_b):
    _make_project()
    assert projects.find_by_scope(scope_b) is None


# ── 与既有 Scope 的隔离 ──────────────────────────────────


def test_projects_tables_are_additive_and_scopes_untouched(app_module, scope_a):
    """引入项目**不得**改动 ``scopes`` 表结构（方案第 10 节 / DECISIONS-E）。"""
    conn = connect()
    try:
        scope_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scopes)")}
        project_tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()

    assert scope_columns == {
        "id",
        "name",
        "allowed_domains",
        "allowed_cidrs",
        "excluded_domains",
        "active_scan",
        "created_at",
        "created_by",
    }
    assert {"projects", "project_scopes"} <= project_tables


def test_project_creation_does_not_mutate_scope(app_module, scope_a):
    """关联项目不得改写 Scope 本体（判定路径仍然唯一）。"""
    before = scope_store.get(scope_a)
    _make_project(scope_ids=[scope_a])
    after = scope_store.get(scope_a)

    assert before.to_dict() == after.to_dict()


def test_scoped_target_hint_hides_targets(app_module, scope_a):
    """摘要接口不得下发目标清单（那是敏感信息）。"""
    project = _make_project(scope_ids=[scope_a])
    hint = projects.scoped_target_hint(project)
    assert hint["scope_ids"] == [scope_a]
    assert "allowed_domains" not in hint
    assert "authorization_note" not in hint


def test_db_path_is_the_test_database(app_module, tmp_path):
    """兜底断言：本文件全程没有碰到仓库里的 ``results/local.db``。"""
    assert db_path() == str(tmp_path / "test_local.db")