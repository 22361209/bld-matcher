from __future__ import annotations

from flask import Blueprint

from app.platform.api_auth import api_scope_required, current_api_principal
from app.platform.api_errors import ApiError, register_api_error_handlers, success_response
from app.platform.api_schemas import api_schema
from app.platform.idempotency import idempotency_required
from app.platform.openapi import OpenApiOperation, register_openapi_operation

from .domain import JobNotFoundError, JobNotReadyError
from .factory import get_job_service
from .schemas import JobCancelRequest, JobEnvelope, JobResultEnvelope


job_v1_api = Blueprint("job_v1_api", __name__)
register_api_error_handlers(job_v1_api)


def _owner_id() -> str:
    principal = current_api_principal()
    if principal is None:
        raise RuntimeError("Job route requires an API principal.")
    return principal.subject


def _not_found(exc: JobNotFoundError) -> ApiError:
    return ApiError("job.not_found", "任务不存在、已过期或不属于当前调用方。", 404)


@job_v1_api.get("/api/v1/jobs/<string:job_id>")
@api_scope_required("jobs:read")
def get_job_v1(job_id: str):
    try:
        job = get_job_service().get(job_id, owner_id=_owner_id())
    except JobNotFoundError as exc:
        raise _not_found(exc) from exc
    return success_response({"job": job.public_payload()})


@job_v1_api.get("/api/v1/jobs/<string:job_id>/result")
@api_scope_required("jobs:read")
def get_job_result_v1(job_id: str):
    try:
        result = get_job_service().result(job_id, owner_id=_owner_id())
    except JobNotFoundError as exc:
        raise _not_found(exc) from exc
    except JobNotReadyError as exc:
        raise ApiError(
            "job.not_ready",
            "任务结果尚未就绪。",
            409,
            {"status": exc.status},
            retryable=exc.status in {"queued", "running"},
        ) from exc
    return success_response({"job_id": job_id, "result": result})


@job_v1_api.post("/api/v1/jobs/<string:job_id>/cancel")
@api_scope_required("jobs:cancel")
@idempotency_required
@api_schema(JobCancelRequest)
def cancel_job_v1(job_id: str, *, payload: JobCancelRequest):
    try:
        job = get_job_service().cancel(job_id, owner_id=_owner_id(), reason=payload.reason)
    except JobNotFoundError as exc:
        raise _not_found(exc) from exc
    return success_response({"job": job.public_payload()}, status=202)


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/jobs/{job_id}",
        method="GET",
        operation_id="getJob",
        summary="Read a persistent job owned by the API principal",
        scopes=("jobs:read",),
        response_model=JobEnvelope,
        path_parameters=(("job_id", "string"),),
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/jobs/{job_id}/result",
        method="GET",
        operation_id="getJobResult",
        summary="Read the completed result of a persistent job",
        scopes=("jobs:read",),
        response_model=JobResultEnvelope,
        path_parameters=(("job_id", "string"),),
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/jobs/{job_id}/cancel",
        method="POST",
        operation_id="cancelJob",
        summary="Request cancellation of a persistent job",
        scopes=("jobs:cancel",),
        request_model=JobCancelRequest,
        response_model=JobEnvelope,
        path_parameters=(("job_id", "string"),),
        success_status=202,
    )
)


def register(app) -> None:
    app.register_blueprint(job_v1_api)
