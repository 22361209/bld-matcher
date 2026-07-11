from __future__ import annotations

import logging

from app.platform.jobs.domain import JobRecord
from app.platform.jobs.worker import JobExecutionContext, JobExecutionError

from .recognition_service import ShipmentRecognitionService


logger = logging.getLogger(__name__)


class ShipmentRecognitionJobHandler:
    def __init__(self, service: ShipmentRecognitionService) -> None:
        self.service = service

    def execute(self, job: JobRecord, context: JobExecutionContext) -> dict[str, object]:
        try:
            return self.service.execute(
                job_id=job.id,
                payload=job.request_payload,
                update_progress=context.update,
                check_cancelled=context.check_cancelled,
            )
        except ValueError as exc:
            logger.warning("Shipment recognition job validation failed", exc_info=True, extra={"job_id": job.id})
            raise JobExecutionError("shipment_recognition.invalid", str(exc)) from exc
