"""
POST /api/upload — 受控目标文件上传（M2 起）

与 M1 之前的差别：

* 不再返回可任意拼接的 ``file_path``，只返回受控 ``upload_id``；
* 每个上传落到独立 UUID 目录，永远不会覆盖旧文件；
* 原始文件名只用于展示，磁盘上使用固定文件名 + 白名单扩展名；
* 超过 2 MB 的请求体由 Flask 的 ``MAX_CONTENT_LENGTH`` 直接拒绝（413）。

流程:
    任意格式(.txt/.csv/.xlsx/.json) → 解析归一化 → upload_id
    → 扫描接口只接受 upload_id
"""

from flask import jsonify, request

from api import api_bp
from core.auth import require_admin
from core.uploads import save_upload


@api_bp.route("/upload", methods=["POST"])
def upload_file():
    """上传目标文件，返回受控 ``upload_id``。

    Content-Type: multipart/form-data
        file: .txt / .csv / .xlsx / .json（≤ 2 MB）

    响应::

        {
            "ok": true,
            "upload_id": "upload_<uuid4hex>",
            "target_count": N,
            "targets_preview": [...]
        }

    下一步::

        POST /api/run  {"upload_id": "<返回的 upload_id>", "tools": ["subfinder"]}
    """
    require_admin()

    result = save_upload(request.files.get("file"))
    return jsonify(
        {
            "ok": True,
            "upload_id": result["upload_id"],
            "target_count": result["count"],
            "targets_preview": result["preview"],
            "label": result["label"],
        }
    )
