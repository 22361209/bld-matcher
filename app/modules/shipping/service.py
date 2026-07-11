from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from pathlib import Path

from app.helpers import download_name

from .infrastructure import ALLOWED_TEMPLATE_SUFFIXES, ShippingTemplateStore, ShippingWorkbookAdapter


logger = logging.getLogger(__name__)


class ShippingNoticeService:
    def __init__(self, unit_of_work_factory, templates: ShippingTemplateStore, workbooks: ShippingWorkbookAdapter) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.templates = templates
        self.workbooks = workbooks

    def page_context(
        self,
        *,
        selected_template_id: str = "",
        template_preview=None,
        generate_preview=None,
        recent_outputs: list[Path] | None = None,
    ) -> dict[str, object]:
        customers, templates = self.templates.choices()
        selected = self.templates.find(selected_template_id) if selected_template_id else None
        if selected and not template_preview:
            path = self.templates.template_path(selected)
            if path and path.is_file():
                template_preview = self.templates.preview_workbook(path)
        latest_outputs = [
            {
                "name": path.name,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "download_name": download_name(path),
            }
            for path in (recent_outputs or [])
        ]
        return {
            "customers": customers,
            "templates": templates,
            "selected_template_id": selected_template_id,
            "selected_template": selected,
            "template_preview": template_preview,
            "generate_preview": generate_preview,
            "latest_outputs": latest_outputs,
        }

    def upload_template(self, file, *, customer: str, name: str, actor: str) -> dict:
        template = self.templates.add_uploaded(file, customer=customer, name=name, actor=actor)
        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.repository.audit(
                    "上传发货通知模板",
                    str(template["file_name"]),
                    f"{customer} / {name}",
                    actor=actor,
                )
                unit_of_work.commit()
        except Exception:
            self.templates.remove(str(template["id"]))
            raise
        return template

    def batch_upload(self, archive_path: Path, *, actor: str) -> tuple[int, list[str]]:
        imported: list[dict] = []
        errors: list[str] = []
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    suffix = Path(member.filename).suffix.lower()
                    if member.is_dir() or suffix not in ALLOWED_TEMPLATE_SUFFIXES:
                        continue
                    customer, name = self.workbooks.parse_filename_defaults(Path(member.filename).name)
                    try:
                        imported.append(
                            self.templates.add_bytes(
                                Path(member.filename).name,
                                archive.read(member),
                                customer=customer,
                                name=name,
                                actor=actor,
                            )
                        )
                    except ValueError as exc:
                        errors.append(f"{Path(member.filename).name}: {exc}")
                    except Exception:
                        logger.exception("Shipping template from archive could not be imported")
                        errors.append(f"{Path(member.filename).name}: 文件无法读取或格式不符合模板要求。")
        except zipfile.BadZipFile as exc:
            raise ValueError("模板压缩包无法读取。") from exc
        if imported:
            try:
                with self.unit_of_work_factory() as unit_of_work:
                    unit_of_work.repository.audit(
                        "批量上传发货通知模板",
                        archive_path.name,
                        f"导入 {len(imported)} 个模板，失败 {len(errors)} 个",
                        actor=actor,
                    )
                    unit_of_work.commit()
            except Exception:
                for template in imported:
                    self.templates.remove(str(template["id"]))
                raise
        return len(imported), errors

    def preview_shipment(self, *, template_id: str, upload_path: Path) -> dict[str, object]:
        template = self.templates.find(template_id)
        if not template:
            raise ValueError("请选择客户模板。")
        if template.get("status") != "ready":
            raise ValueError("这个模板还只是需求记录，需要先上传模板 Excel。")
        rows, source = self.workbooks.parse_rows(upload_path)
        return {
            "template": template,
            "upload_path": str(upload_path),
            "source": source,
            "row_count": len(rows),
            "rows": rows[:20],
        }

    def generate(self, *, template_id: str, upload_path: Path, output_dir: Path, actor: str) -> Path:
        template = self.templates.find(template_id)
        if not template or template.get("status") != "ready":
            raise ValueError("请选择可用模板。")
        rows, _source = self.workbooks.parse_rows(upload_path)
        output_path = self.workbooks.generate(self.templates, template, rows, output_dir)
        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.repository.audit(
                    "生成发货通知",
                    output_path.name,
                    f"{template.get('customer')} / {template.get('name')} / {len(rows)} 行",
                    actor=actor,
                )
                unit_of_work.commit()
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        return output_path
