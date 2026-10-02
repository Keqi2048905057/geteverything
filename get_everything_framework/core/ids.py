"""统一 ID 生成。

任务、步骤、资产、产物、导出记录都使用带前缀的 UUID4 字符串，
便于日志里一眼看出实体类型，也方便后续迁移到数据库主键。
"""

from __future__ import annotations

import uuid

PREFIX_JOB = "job"
PREFIX_STEP = "step"
PREFIX_RUN = "run"
PREFIX_ASSET = "asset"
PREFIX_OBSERVATION = "obs"
PREFIX_ARTIFACT = "art"
PREFIX_EXPORT = "exp"
PREFIX_SCOPE = "scope"
PREFIX_JOB_EVENT = "evt"
# 授权测试项目（公网授权测试模式体验版方案第 4 节步骤 1）。
PREFIX_PROJECT = "proj"


def new_id(prefix: str) -> str:
    """生成 ``<prefix>_<uuid4hex>`` 形式的 ID。"""
    return f"{prefix}_{uuid.uuid4().hex}"


def new_job_id() -> str:
    return new_id(PREFIX_JOB)


def new_step_id() -> str:
    return new_id(PREFIX_STEP)


def new_run_id() -> str:
    return new_id(PREFIX_RUN)


def new_asset_id() -> str:
    return new_id(PREFIX_ASSET)


def new_observation_id() -> str:
    return new_id(PREFIX_OBSERVATION)


def new_artifact_id() -> str:
    return new_id(PREFIX_ARTIFACT)


def new_export_id() -> str:
    return new_id(PREFIX_EXPORT)


def new_scope_id() -> str:
    return new_id(PREFIX_SCOPE)


def new_job_event_id() -> str:
    return new_id(PREFIX_JOB_EVENT)


def new_project_id() -> str:
    return new_id(PREFIX_PROJECT)
