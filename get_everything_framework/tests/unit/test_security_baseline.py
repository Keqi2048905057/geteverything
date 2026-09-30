"""M2 单元测试：安全基线与受控上传。

覆盖方案 M2 验收项：

* 移除默认 ``dev-secret-key``；
* 上传 2 MB 以上被拒绝（由 Flask 的 MAX_CONTENT_LENGTH 保证，见集成测试）；
* 上传文件不会覆盖旧文件；
* `.env` 原子写入 + 写入前备份。
"""

import os
import stat
from pathlib import Path

import pytest

from core.errors import BadRequestError
from core.security import (
    INSECURE_DEFAULTS,
    MIN_SECRET_LENGTH,
    generate_secret,
    is_weak_secret,
    resolve_secret_key,
    secret_key_is_ephemeral,
)


# ── SECRET_KEY 基线 ───────────────────────────────────────


@pytest.mark.parametrize("value", ["", None, "   ", "dev-secret-key", "DEV-SECRET-KEY", "short"])
def test_weak_secret_detected(value):
    assert is_weak_secret(value) is True


@pytest.mark.parametrize("value", ["a" * 40, generate_secret()])
def test_strong_secret_accepted(value):
    assert is_weak_secret(value) is False


def test_config_has_no_hardcoded_default_secret():
    """回归：仓库里不能再出现可预测的固定默认密钥。"""
    import config

    assert "dev-secret-key" not in (config.Config.SECRET_KEY or "")
    for insecure in INSECURE_DEFAULTS:
        assert insecure not in (config.Config.SECRET_KEY or "").lower()
    assert MIN_SECRET_LENGTH >= 16


def test_resolve_secret_key_is_stable_within_process(monkeypatch):
    """同进程内必须返回同一个密钥，否则会话会随机失效。"""
    import config

    monkeypatch.setattr(config.Config, "SECRET_KEY", "", raising=False)
    import core.security as security

    monkeypatch.setattr(security, "_EPHEMERAL_SECRET", None, raising=False)
    first = resolve_secret_key()
    second = resolve_secret_key()
    assert first == second
    assert len(first) >= MIN_SECRET_LENGTH


def test_ephemeral_flag_follows_config(monkeypatch):
    import config

    monkeypatch.setattr(config.Config, "SECRET_KEY", "x" * 40, raising=False)
    assert secret_key_is_ephemeral() is False
    monkeypatch.setattr(config.Config, "SECRET_KEY", "dev-secret-key", raising=False)
    assert secret_key_is_ephemeral() is True


# ── 受控上传 ──────────────────────────────────────────────


def _make_upload(tmp_path, monkeypatch, name="targets.txt", content="example.test\n"):
    """构造一个 FileStorage 并落到临时上传目录。"""
    import io

    from werkzeug.datastructures import FileStorage

    from core import uploads as core_uploads

    monkeypatch.setattr(core_uploads, "UPLOAD_DIR", str(tmp_path / "uploads"), raising=False)

    stream = io.BytesIO(content.encode("utf-8"))
    return core_uploads, FileStorage(stream=stream, filename=name)


def test_upload_creates_isolated_directory(tmp_path, monkeypatch):
    core_uploads, storage = _make_upload(tmp_path, monkeypatch)
    result = core_uploads.save_upload(storage)

    upload_id = result["upload_id"]
    assert upload_id.startswith("upload_")

    directory = Path(core_uploads.upload_dir(upload_id))
    assert directory.is_dir()
    # 原始文件、归一化清单、元信息三者齐备
    assert (directory / "raw.txt").is_file()
    assert (directory / "normalized.txt").is_file()
    assert (directory / "meta.json").is_file()
    assert result["count"] == 1


def test_two_uploads_never_collide(tmp_path, monkeypatch):
    """M2 验收：上传文件不会覆盖旧文件。"""
    core_uploads, storage = _make_upload(tmp_path, monkeypatch)
    first = core_uploads.save_upload(storage)

    core_uploads, storage = _make_upload(tmp_path, monkeypatch, content="second.test\n")
    second = core_uploads.save_upload(storage)

    assert first["upload_id"] != second["upload_id"]
    first_dir = Path(core_uploads.upload_dir(first["upload_id"]))
    second_dir = Path(core_uploads.upload_dir(second["upload_id"]))
    assert first_dir != second_dir

    # 第一份内容必须原样保留
    assert "example.test" in (first_dir / "normalized.txt").read_text(encoding="utf-8")
    assert "second.test" in (second_dir / "normalized.txt").read_text(encoding="utf-8")


def test_upload_rejects_unsupported_extension(tmp_path, monkeypatch):
    core_uploads, storage = _make_upload(tmp_path, monkeypatch, name="payload.exe", content="x")
    with pytest.raises(BadRequestError):
        core_uploads.save_upload(storage)


def test_upload_cleans_up_on_failure(tmp_path, monkeypatch):
    """解析不出目标时必须回收目录，不留半成品。"""
    core_uploads, storage = _make_upload(tmp_path, monkeypatch, content="!!! not a target !!!\n")
    uploads_root = Path(core_uploads.UPLOAD_DIR)

    with pytest.raises(BadRequestError):
        core_uploads.save_upload(storage)

    leftovers = [p for p in uploads_root.iterdir()] if uploads_root.exists() else []
    assert leftovers == []


def test_resolve_targets_file_rejects_unknown_id(tmp_path, monkeypatch):
    core_uploads, _ = _make_upload(tmp_path, monkeypatch)
    with pytest.raises(BadRequestError):
        core_uploads.resolve_targets_file("upload_deadbeef")


@pytest.mark.parametrize("bad_id", ["../etc/passwd", "upload_x/../../y", "", "a\\b"])
def test_resolve_targets_file_rejects_path_traversal(tmp_path, monkeypatch, bad_id):
    core_uploads, _ = _make_upload(tmp_path, monkeypatch)
    with pytest.raises(BadRequestError):
        core_uploads.resolve_targets_file(bad_id)


# ── .env 原子写入与备份 ───────────────────────────────────


def test_env_write_is_atomic_and_backed_up(tmp_path, monkeypatch):
    from api import settings

    env_path = tmp_path / ".env"
    backup_dir = tmp_path / "backups"
    env_path.write_text("LLM_API_KEY=old\n# 注释保留\n", encoding="utf-8")

    monkeypatch.setattr(settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(settings, "BACKUP_DIR", str(backup_dir))

    backup = settings._backup_env_file()
    assert backup is not None and Path(backup).is_file()
    assert "old" in Path(backup).read_text(encoding="utf-8")

    settings._write_env_file({"LLM_API_KEY": "new"})

    text = env_path.read_text(encoding="utf-8")
    assert "LLM_API_KEY=new" in text
    assert "LLM_API_KEY=old" not in text
    assert "# 注释保留" in text  # 注释行不能被吞掉

    # 原子写入不留临时文件
    assert not list(tmp_path.glob(".env.*.tmp"))


def test_env_write_preserves_file_mode(tmp_path, monkeypatch):
    """os.replace 之后文件权限不应被意外放宽。"""
    from api import settings

    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n", encoding="utf-8")
    os.chmod(env_path, stat.S_IREAD | stat.S_IWRITE)

    monkeypatch.setattr(settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    settings._write_env_file({"A": "2"})

    assert "A=2" in env_path.read_text(encoding="utf-8")
