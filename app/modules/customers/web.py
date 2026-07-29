from __future__ import annotations

import logging
from dataclasses import replace

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.security import actor_name, login_required, permission_required, wants_json_response

from .domain import CustomerValidationError
from .factory import get_customer_service


logger = logging.getLogger(__name__)
CUSTOMER_VIEWS = frozenset({"overview", "contacts", "business", "documents"})


def _owners(*, current_username: str = "", include_inactive: bool = False) -> list[dict[str, object]]:
    from app.modules.admin.factory import get_admin_service

    users, _ = get_admin_service().users()
    owners = [
        user
        for user in users
        if include_inactive
        or bool(user.get("active"))
        or str(user.get("username") or "") == current_username
    ]
    if current_username and not any(str(owner.get("username") or "") == current_username for owner in owners):
        owners.append(
            {
                "username": current_username,
                "display_name": current_username,
                "active": 0,
                "missing": True,
            }
        )
    return owners


def _document_service():
    from app.modules.customer_documents.factory import get_customer_document_service

    return get_customer_document_service()


def _detail_url(customer_id: int, view: str = "overview") -> str:
    normalized = view if view in CUSTOMER_VIEWS else "overview"
    return url_for("customer_detail", customer_id=customer_id, view=normalized)


def _form_customer_id() -> int | None:
    text = request.form.get("id", "").strip()
    return int(text) if text.isdigit() else None


def register(app) -> None:
    @app.get("/customers")
    @permission_required("manage_customers")
    def customers():
        query = " ".join(request.args.get("q", "").split())[:200]
        status = " ".join(request.args.get("status", "").split())[:20].lower()
        owners = _owners(include_inactive=True)
        query_key = query.casefold()
        matching_owner_usernames = [
            str(owner.get("username") or "")
            for owner in owners
            if query_key
            and (
                query_key in str(owner.get("username") or "").casefold()
                or query_key in str(owner.get("display_name") or "").casefold()
            )
        ]
        summaries = get_customer_service().list_summaries(
            query=query,
            status=status,
            owner_usernames=matching_owner_usernames,
        )
        document_summaries = _document_service().summaries_for_customers(
            [summary.customer.id for summary in summaries]
        )
        summaries = [
            replace(
                summary,
                file_count=document_summaries.get(summary.customer.id).group_count
                if summary.customer.id in document_summaries
                else 0,
            )
            for summary in summaries
        ]
        owner_labels = {
            str(owner.get("username") or ""): str(owner.get("display_name") or owner.get("username") or "")
            for owner in owners
        }
        return render_template(
            "customers.html",
            customer_summaries=summaries,
            filters={"q": query, "status": status},
            owner_labels=owner_labels,
        )

    @app.get("/customers/<int:customer_id>")
    @permission_required("manage_customers")
    def customer_detail(customer_id: int):
        view = request.args.get("view", "overview").strip().lower()
        if view not in CUSTOMER_VIEWS:
            view = "overview"
        try:
            context = get_customer_service().detail(customer_id, include_business=view == "business")
            document_service = _document_service()
            document_groups = (
                document_service.list_for_customer(customer_id, include_archived=True)
                if view == "documents"
                else []
            )
            document_summary = document_service.summaries_for_customers([customer_id]).get(customer_id)
            if document_summary is not None:
                context["summary"] = replace(context["summary"], file_count=document_summary.group_count)
        except CustomerValidationError as exc:
            flash(exc.message, "error")
            return redirect(url_for("customers"))
        except Exception:
            logger.exception("Customer detail query failed", extra={"customer_id": customer_id})
            flash("客户档案读取失败，请稍后重试。", "error")
            return redirect(url_for("customers"))
        return render_template(
            "customer_detail.html",
            **context,
            document_groups=document_groups,
            document_categories=document_service.categories(),
            active_view=view,
            owners=_owners(current_username=str(context["customer"].owner_username or "")),
        )

    @app.get("/customers/lookup")
    @login_required
    def customer_lookup():
        query = request.args.get("q", "")
        limit_text = request.args.get("limit", "").strip()
        limit = int(limit_text) if limit_text.isdigit() else 20
        matches = get_customer_service().lookup(query, limit=max(1, min(50, limit)))
        response = jsonify([{"id": customer.id, "name": customer.name} for customer in matches])
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/customers/save")
    @permission_required("manage_customers")
    def save_customer():
        customer_id = _form_customer_id()
        try:
            service = get_customer_service()
            if customer_id is None:
                customer = service.create(request.form.get("name", ""), actor=actor_name())
            else:
                values = {field: request.form[field] for field in ("name", "code", "owner_username") if field in request.form}
                customer = service.update_profile(customer_id, values, actor=actor_name())
        except CustomerValidationError as exc:
            if wants_json_response():
                return jsonify({"ok": False, "error": exc.message}), 400
            flash(f"客户保存失败：{exc.message}", "error")
            return redirect(_detail_url(customer_id) if customer_id else url_for("customers"))
        except Exception:
            logger.exception("Customer save failed")
            if wants_json_response():
                return jsonify({"ok": False, "error": "客户保存失败，请稍后重试。"}), 500
            flash("客户保存失败，请稍后重试。", "error")
            return redirect(_detail_url(customer_id) if customer_id else url_for("customers"))
        if wants_json_response():
            return jsonify({"ok": True, "customer": {"id": customer.id, "name": customer.name}})
        flash("客户已保存。", "success")
        return redirect(_detail_url(customer.id) if customer_id else url_for("customers"))

    @app.post("/customers/<int:customer_id>/status")
    @permission_required("manage_customers")
    def set_customer_status(customer_id: int):
        try:
            customer = get_customer_service().set_status(
                customer_id,
                request.form.get("status", ""),
                actor=actor_name(),
            )
        except CustomerValidationError as exc:
            flash(f"客户状态更新失败：{exc.message}", "error")
            return redirect(_detail_url(customer_id))
        except Exception:
            logger.exception("Customer status update failed", extra={"customer_id": customer_id})
            flash("客户状态更新失败，请稍后重试。", "error")
            return redirect(_detail_url(customer_id))
        flash("客户已启用。" if customer.status == "active" else "客户已停用，历史记录继续保留。", "success")
        return redirect(_detail_url(customer_id))

    @app.post("/customers/<int:customer_id>/contacts/save")
    @permission_required("manage_customers")
    def save_customer_contact(customer_id: int):
        contact_text = request.form.get("contact_id", "").strip()
        contact_id = int(contact_text) if contact_text.isdigit() else None
        try:
            get_customer_service().save_contact(
                customer_id,
                request.form,
                actor=actor_name(),
                contact_id=contact_id,
            )
        except CustomerValidationError as exc:
            flash(f"联系人保存失败：{exc.message}", "error")
        except Exception:
            logger.exception("Customer contact save failed", extra={"customer_id": customer_id})
            flash("联系人保存失败，请稍后重试。", "error")
        else:
            flash("联系人已保存。", "success")
        return redirect(_detail_url(customer_id, "contacts"))

    @app.post("/customers/<int:customer_id>/contacts/<int:contact_id>/delete")
    @permission_required("manage_customers")
    def delete_customer_contact(customer_id: int, contact_id: int):
        try:
            contact = get_customer_service().delete_contact(customer_id, contact_id, actor=actor_name())
        except CustomerValidationError as exc:
            flash(f"联系人删除失败：{exc.message}", "error")
        except Exception:
            logger.exception(
                "Customer contact delete failed",
                extra={"customer_id": customer_id, "contact_id": contact_id},
            )
            flash("联系人删除失败，请稍后重试。", "error")
        else:
            flash(f"联系人 {contact.name} 已删除。", "success")
        return redirect(_detail_url(customer_id, "contacts"))

    @app.post("/customers/delete")
    @permission_required("manage_customers")
    def delete_customer():
        customer_id = _form_customer_id()
        try:
            if customer_id is None:
                raise CustomerValidationError("customer.id_required", "缺少客户编号。")
            get_customer_service().delete(customer_id, actor=actor_name())
        except CustomerValidationError as exc:
            flash(f"客户删除失败：{exc.message}", "error")
            return redirect(url_for("customers"))
        except Exception:
            logger.exception("Customer delete failed")
            flash("客户删除失败，请稍后重试。", "error")
            return redirect(url_for("customers"))
        flash("客户已删除。", "success")
        return redirect(url_for("customers"))
