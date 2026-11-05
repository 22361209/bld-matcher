"""Session-bound previews for the one-time adoption of a trial cost ledger."""
from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .domain import CostingConflict, CostingValidationError
from .transfer_service import MAX_TRANSFER_BYTES, MAX_TRANSFER_MIB, CostingTransferService
from .trial_support import atomic_json


logger = logging.getLogger(__name__)
PREVIEW_TTL = 3600


class CostingTransferPreviewService:
    def __init__(self, service: CostingTransferService, directory: Path, secret: str) -> None:
        self.service = service
        self.directory = Path(directory) / "transfer-previews"
        self.signer = URLSafeTimedSerializer(secret, salt="costing-transfer-preview")

    def preview(self, upload, *, owner: str) -> tuple[dict, str]:
        if not upload or not str(upload.filename or "").lower().endswith(".json"):
            raise CostingValidationError("请选择从成本试用导出的 .json 接入包。")
        raw = upload.stream.read(MAX_TRANSFER_BYTES + 1)
        if len(raw) > MAX_TRANSFER_BYTES:
            raise CostingValidationError(f"成本接入包不能超过 {MAX_TRANSFER_MIB} MiB。")
        try:
            snapshot = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise CostingValidationError("成本接入包格式无效，请重新导出。") from exc
        result = self.service.preview(snapshot)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in self.directory.glob("*.json"):
            try:
                if not path.name.endswith(".applying.json") and time.time() - path.stat().st_mtime > PREVIEW_TTL:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
        identifier = secrets.token_hex(16)
        atomic_json(self.directory / f"{identifier}.json", {"owner": owner, "snapshot": snapshot})
        token = self.signer.dumps({"id": identifier, "owner": owner, "digest": result["digest"]})
        return result, token

    def apply(self, token: str, *, owner: str, actor: str) -> dict:
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise CostingValidationError("成本接入预览凭证无效。")
        try:
            value = self.signer.loads(token, max_age=PREVIEW_TTL)
        except (BadSignature, SignatureExpired) as exc:
            raise CostingValidationError("接入预览已过期，请重新上传核对。") from exc
        if not isinstance(value, dict) or value.get("owner") != owner:
            raise CostingValidationError("接入预览不属于当前登录。")
        identifier = value.get("id", "")
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise CostingValidationError("成本接入预览凭证无效。")
        path = self.directory / f"{identifier}.json"
        claimed = self.directory / f"{identifier}.applying.json"
        try:
            path.replace(claimed)
        except FileNotFoundError as exc:
            raise CostingConflict("该接入预览已提交或失效，请查看成本列表。") from exc
        try:
            payload = json.loads(claimed.read_text(encoding="utf-8"))
            if payload.get("owner") != owner:
                raise CostingValidationError("接入预览不属于当前登录。")
            result = self.service.apply(payload["snapshot"], actor=actor, expected_digest=value.get("digest", ""))
        except Exception:
            logger.exception("Cost trial adoption failed", extra={"preview_id": identifier})
            claimed.replace(path)
            raise
        claimed.unlink(missing_ok=True)
        return result
