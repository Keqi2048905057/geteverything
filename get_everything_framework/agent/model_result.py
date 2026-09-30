from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ModelResult:
    content: str
    finish_reason: Optional[str] = None
    request_id: Optional[str] = None
    raw_response: Optional[Any] = None
