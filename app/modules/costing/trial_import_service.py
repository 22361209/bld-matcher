"""Session-bound, single-use workbook previews and atomic import coordination."""
from __future__ import annotations

import json
import logging
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from threading import RLock

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .domain import CostingConflict, CostingValidationError
from .service import CostingService
from .trial_support import atomic_json


logger = logging.getLogger(__name__)
PREVIEW_TTL = 3600
MAX_WORKBOOK_BYTES = 12 * 1024 * 1024


class TrialImportService:
    def __init__(self, directory: Path, secret: str, service: CostingService) -> None:
        self.directory = Path(directory)
        self.preview_dir = self.directory / "previews"
        self.signer = URLSafeTimedSerializer(secret, salt="costing-import-preview")
        self.service = service
        self.lock = RLock()

    def references(self) -> dict:
        return self.service.references()

    def _cleanup(self) -> None:
        if not self.preview_dir.exists():
            return
        for path in self.preview_dir.glob("*.json"):
            try:
                if not path.name.endswith(".applying.json") and time.time() - path.stat().st_mtime > PREVIEW_TTL:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
        for path in self.preview_dir.glob("upload-*"):
            try:
                if path.is_dir() and not path.is_symlink() and time.time() - path.stat().st_mtime > PREVIEW_TTL:
                    shutil.rmtree(path)
            except FileNotFoundError:
                continue

    @staticmethod
    def _validate_archive(path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
                    raise CostingValidationError("工作簿展开后过大，请清理多余格式或拆分后再导入。")
                if "xl/workbook.xml" not in archive.namelist():
                    raise CostingValidationError("请选择有效的 .xlsx 工作簿。")
                if any(entry.flag_bits & 1 for entry in entries):
                    raise CostingValidationError("不支持加密工作簿。")
        except zipfile.BadZipFile as exc:
            raise CostingValidationError("请选择有效的 .xlsx 工作簿。") from exc

    def preview(self, upload, *, owner: str) -> tuple[dict, str]:
        from .workbook_import import WorkbookImportError, parse_cost_workbook

        if not upload or not str(upload.filename or "").lower().endswith(".xlsx"):
            raise CostingValidationError("请选择 .xlsx 成本核算工作簿。")
        self.preview_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._cleanup()
        with tempfile.TemporaryDirectory(prefix="upload-", dir=self.preview_dir) as temporary:
            path = Path(temporary) / "workbook.xlsx"
            upload.save(path)
            if path.stat().st_size > MAX_WORKBOOK_BYTES:
                raise CostingValidationError("文件过大，请使用 12 MB 以内的工作簿。")
            self._validate_archive(path)
            try:
                parsed = parse_cost_workbook(path)
            except WorkbookImportError as exc:
                raise CostingValidationError(str(exc)) from exc
        records = parsed.get("records", [])
        if not records or len(records) > 5000:
            raise CostingValidationError("工作簿中需要包含 1 至 5000 个型号。")
        normalized = [self.service.calculate(record) for record in records]
        warnings = list(parsed.get("warnings", []))
        existing = {record["code"] for record in normalized
                    if any(found["code"] == record["code"]
                           for found in self.service.list(q=record["code"], page_size=200)["records"])}
        if existing:
            warnings.append("以下型号已有成本单，本次导入不会覆盖：" + "、".join(sorted(existing)))
        preview = {**parsed, "records": normalized, "warnings": warnings, "blocked": bool(existing)}
        identifier = secrets.token_hex(16)
        atomic_json(self.preview_dir / f"{identifier}.json", {"owner": owner, "preview": preview})
        token = self.signer.dumps({"id": identifier, "owner": owner})
        return preview, token

    def apply(self, token: str, *, owner: str, actor: str) -> dict:
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise CostingValidationError("导入预览凭证无效，请重新上传核对。")
        try:
            value = self.signer.loads(token, max_age=PREVIEW_TTL)
        except (BadSignature, SignatureExpired) as exc:
            raise CostingValidationError("导入预览已过期，请重新上传核对。") from exc
        if not isinstance(value, dict) or value.get("owner") != owner:
            raise CostingValidationError("导入预览不属于当前登录，请重新上传核对。")
        identifier = value.get("id", "")
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise CostingValidationError("导入预览无效。")
        path = self.preview_dir / f"{identifier}.json"
        applying = self.preview_dir / f"{identifier}.applying.json"
        with self.lock:
            # A filesystem rename claims the preview across workers, not only threads.
            try:
                path.replace(applying)
            except FileNotFoundError as exc:
                raise CostingConflict("该预览已提交或失效，请返回列表查看结果。") from exc
            try:
                payload = json.loads(applying.read_text(encoding="utf-8"))
                if payload.get("owner") != owner:
                    raise CostingValidationError("导入预览不属于当前登录。")
                preview = payload["preview"]
                if preview.get("blocked"):
                    raise CostingConflict("导入含有重复型号，未写入任何数据。")
                result = self.service.import_records(preview["records"], actor=actor,
                                                     references=preview.get("references"))
            except Exception:
                logger.exception("Cost workbook import rolled back", extra={"preview_id": identifier})
                applying.replace(path)
                raise
            applying.unlink(missing_ok=True)
            return {"imported": result["imported"]}
