from __future__ import annotations

from pathlib import Path, PurePosixPath


class ContractDocumentRegistry:
    def __init__(self, unit_of_work_factory, document_root: Path | None) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.document_root = document_root

    def relative_path(self, output_path: Path, *, output_root: Path) -> str:
        root = (self.document_root or output_root.parent).resolve()
        resolved = output_path.resolve()
        if root not in resolved.parents:
            raise ValueError("合同文件不在允许的输出目录中。")
        return resolved.relative_to(root).as_posix()

    @staticmethod
    def payload(document: dict[str, object]) -> dict[str, object]:
        language = str(document.get("language") or "zh-CN")
        return {
            **document,
            "name": Path(str(document.get("file_path") or "")).name,
            "language_label": "中文" if language == "zh-CN" else language,
        }

    def for_quote(self, quote_no: object) -> list[dict[str, object]]:
        number = str(quote_no or "").strip()
        if not number:
            return []
        with self.unit_of_work_factory() as unit_of_work:
            documents = unit_of_work.repository.list_documents_by_quote_no(number)
        return [self.payload(document) for document in documents]

    def for_customer(
        self,
        customer_id: int,
        *,
        customer_name: str = "",
        limit: int = 50,
    ) -> list[dict[str, object]]:
        with self.unit_of_work_factory() as unit_of_work:
            documents = unit_of_work.repository.list_documents_by_customer(
                customer_id,
                customer_name=customer_name,
                limit=limit,
            )
        return [self.payload(document) for document in documents]

    def path(self, document_id: int) -> tuple[Path, dict[str, object]] | None:
        if self.document_root is None:
            return None
        with self.unit_of_work_factory() as unit_of_work:
            document = unit_of_work.repository.get_document(document_id)
        if document is None:
            return None
        relative = PurePosixPath(str(document["file_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            return None
        root = self.document_root.resolve()
        path = (root / relative.as_posix()).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path, self.payload(document)
