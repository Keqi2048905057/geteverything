"""受控文件上传（M2）。

方案第 2.3 节与 M2 验收项要求：

* 上传文件改为受控 ID + UUID 存储；
* 上传 2 MB 以上文件被拒绝；
* 上传文件不会覆盖旧文件；
* 禁止任意字符串 ``file_path``（扫描接口只能引用受控 upload id）。

存储布局::

    uploads/
      <upload_id>/            # upload_<uuid4hex>
        raw<ext>              # 原始文件，扩展名经过白名单过滤
        normalized.txt        # 解析去重后的目标清单
        meta.json             # 原始文件名、大小、sha256、目标数

``file_path`` 不再对外暴露；对外只给 ``upload_id``。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone

from config import UPLOAD_DIR
from core import audit, db
from core.errors import BadRequestError
from core.ids import new_id

PREFIX_UPLOAD = "upload"

ALLOWED_EXTENSIONS = {".txt", ".csv", ".xlsx", ".json"}

RAW_FILENAME = "raw"
NORMALIZED_FILENAME = "normalized.txt"
META_FILENAME = "meta.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upload_dir(upload_id: str) -> str:
    """单个上传记录的目录。"""
    return os.path.join(UPLOAD_DIR, upload_id)


def normalized_path(upload_id: str) -> str:
    """归一化目标清单的绝对路径。"""
    return os.path.join(upload_dir(upload_id), NORMALIZED_FILENAME)


def _safe_extension(filename: str) -> str:
    """仅保留白名单内的扩展名，其余一律拒绝。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise BadRequestError("仅支持 .txt / .csv / .xlsx / .json 文件")
    return ext


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_upload(file_storage) -> dict:
    """保存上传文件并返回受控记录。

    Args:
        file_storage: Flask 的 ``FileStorage`` 对象。

    Returns:
        dict: ``{"upload_id", "count", "preview", "label"}``。

    Raises:
        BadRequestError: 未选择文件、扩展名不支持、未识别到有效目标。
    """
    from target_parser import parse_targets_file, save_normalized_targets

    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise BadRequestError("未上传文件")

    original_name = os.path.basename(file_storage.filename)
    extension = _safe_extension(original_name)

    upload_id = new_id(PREFIX_UPLOAD)
    directory = upload_dir(upload_id)
    # exist_ok=False 语义：upload_id 是 UUID，冲突即异常，绝不覆盖旧内容。
    os.makedirs(directory, exist_ok=False)

    raw_path = os.path.join(directory, RAW_FILENAME + extension)
    try:
        file_storage.save(raw_path)

        targets = parse_targets_file(raw_path)
        if not targets:
            raise BadRequestError("文件中未识别到有效目标")

        normalized = normalized_path(upload_id)
        save_normalized_targets(targets, normalized)

        meta = {
            "upload_id": upload_id,
            "original_name": original_name,
            "extension": extension,
            "size": os.path.getsize(raw_path),
            "sha256": _sha256_of(raw_path),
            "target_count": len(targets),
            "created_at": _now(),
        }
        with open(os.path.join(directory, META_FILENAME), "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2)

        db.ensure_schema()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO uploads (id, original_name, stored_path, size, sha256, target_count, "
                "created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    upload_id,
                    original_name,
                    normalized,
                    meta["size"],
                    meta["sha256"],
                    meta["target_count"],
                    meta["created_at"],
                    "local-admin",
                ),
            )
        audit.record(
            audit.EVENT_UPLOAD_CREATED,
            target_id=upload_id,
            detail={
                "original_name": original_name,
                "size": meta["size"],
                "sha256": meta["sha256"],
                "target_count": meta["target_count"],
            },
        )
    except Exception:
        # 任一步失败都不留下半成品目录。
        shutil.rmtree(directory, ignore_errors=True)
        raise

    return {
        "upload_id": upload_id,
        "count": len(targets),
        "preview": targets[:20],
        "label": original_name,
    }


def get_upload(upload_id: str) -> dict | None:
    """读取上传记录元信息；不存在返回 ``None``。"""
    if not upload_id or "/" in upload_id or "\\" in upload_id:
        return None
    meta_path = os.path.join(upload_dir(upload_id), META_FILENAME)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def resolve_targets_file(upload_id: str) -> str:
    """把受控 upload id 解析为归一化目标清单路径。

    这是扫描接口唯一允许的「文件目标」入口，用于替代任意 ``file_path``。

    Raises:
        BadRequestError: upload id 非法、不存在或文件缺失。
    """
    meta = get_upload(upload_id)
    if not meta:
        raise BadRequestError("upload_id 不存在或已失效", details={"upload_id": upload_id})

    path = normalized_path(upload_id)
    if not os.path.exists(path):
        raise BadRequestError("上传文件已丢失，请重新上传", details={"upload_id": upload_id})
    return path


def load_targets(upload_id: str) -> list[str]:
    """读取某个上传记录的归一化目标清单。"""
    path = resolve_targets_file(upload_id)
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]
