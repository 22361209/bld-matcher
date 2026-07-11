from __future__ import annotations

import argparse
import signal

from app.modules.shipping.factory import get_shipping_recognition_service
from app.modules.shipping.recognition_service import RECOGNITION_JOB_KIND
from app.modules.shipping.recognition_worker import ShipmentRecognitionJobHandler
from app.platform.jobs.factory import get_job_repository
from app.platform.jobs.worker import PersistentJobWorker
from app.platform.logging_config import configure_logging
from app.platform.runtime_config import RuntimeSettings


def build_worker(*, worker_id: str | None = None) -> PersistentJobWorker:
    settings = RuntimeSettings.from_environment()
    return PersistentJobWorker(
        get_job_repository(),
        {
            RECOGNITION_JOB_KIND: ShipmentRecognitionJobHandler(get_shipping_recognition_service()),
        },
        worker_id=worker_id,
        lease_seconds=settings.job_lease_seconds,
        poll_seconds=settings.job_poll_seconds,
        heartbeat_interval_seconds=settings.worker_heartbeat_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run the persistent BLD background-job worker.")
    parser.add_argument("--once", action="store_true", help="Claim at most one queued job and exit.")
    parser.add_argument("--job-id", help="With --once, only claim this job ID.")
    parser.add_argument("--worker-id", help="Stable worker instance label for health evidence.")
    args = parser.parse_args(argv)
    worker = build_worker(worker_id=args.worker_id)
    if args.once:
        worker.run_once(job_id=args.job_id)
        return 0

    stopping = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        worker.request_stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker.run_forever(should_stop=lambda: stopping)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
