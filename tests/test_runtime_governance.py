from __future__ import annotations

import io
import json
import os
import sqlite3
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pydantic import SecretStr

from app.database import connect
from app.migrations import MIGRATIONS
from app.modules.admin.persistence import ensure_default_admin
from app.modules.products.sync_infrastructure import ProductPackageStore
from app.modules.shipping.repository import SQLiteShippingUnitOfWork
from app.modules.shipping.recognition_service import ShipmentRecognitionService
from app.platform.ai import AiCallMetrics, AiProviderInterruptedError, OpenAICompatibleVisionProvider, VisionProviderConfig
from app.platform.jobs.domain import JobCancelledError, JobInterruptedError, JobRecord
from app.platform.jobs.repository import SQLiteJobRepository
from app.platform.jobs.service import JobService
from app.platform.jobs.worker import JobExecutionContext, PersistentJobWorker
from app.platform.retention import RuntimeRetentionService
from app.platform.runtime import RuntimeHealthService
from app.platform.runtime_config import RuntimeSettings


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "historical"


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _provider_payload() -> dict[str, object]:
    return {
        "model": "vision-1",
        "choices": [{"message": {"content": '{"labels":[]}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


class RuntimeGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "data" / "runtime.sqlite3"
        with connect(self.database_path):
            pass

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_persistent_job_recovers_expired_lease_and_hides_request_payload(self) -> None:
        repository = SQLiteJobRepository(self.database_path)
        service = JobService(repository)
        job = service.submit(
            kind="test.echo",
            owner_id="key:1",
            request_payload={"private_path": "/tmp/private-input.xlsx"},
            progress={"message": "queued"},
        )
        self.assertNotIn("private_path", job.public_payload())
        self.assertNotIn("/tmp/private-input.xlsx", json.dumps(job.public_payload()))

        first_claim = repository.claim_next(worker_id="worker-a", lease_seconds=60, job_id=job.id)
        assert first_claim is not None
        self.assertEqual(first_claim.attempt, 1)
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE background_jobs SET lease_expires_at = '2000-01-01 00:00:00' WHERE id = ?",
                (job.id,),
            )
            connection.commit()

        class EchoHandler:
            def execute(self, job: JobRecord, context: JobExecutionContext) -> dict[str, Any]:
                context.update({"message": "running", "percent": 50})
                return {"echo": job.request_payload.get("private_path")}

        worker = PersistentJobWorker(
            repository,
            {"test.echo": EchoHandler()},
            worker_id="worker-b",
            lease_seconds=60,
        )
        completed = worker.run_once(job_id=job.id)
        assert completed is not None
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.attempt, 2)
        self.assertEqual(completed.result["echo"], "/tmp/private-input.xlsx")
        with connect(self.database_path) as connection:
            events = [
                row["event_type"]
                for row in connection.execute(
                    "SELECT event_type FROM background_job_events WHERE job_id = ? ORDER BY id",
                    (job.id,),
                ).fetchall()
            ]
        self.assertIn("requeued", events)
        self.assertEqual(events[-1], "completed")

    def test_queued_job_cancels_without_worker_execution(self) -> None:
        repository = SQLiteJobRepository(self.database_path)
        service = JobService(repository)
        job = service.submit(kind="test.cancel", owner_id="key:2", request_payload={})
        cancelled = service.cancel(job.id, owner_id="key:2", reason="test")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(repository.claim_next(worker_id="worker", lease_seconds=60, job_id=job.id))

    def test_job_claim_is_atomic_across_workers(self) -> None:
        repository = SQLiteJobRepository(self.database_path)
        job = JobService(repository).submit(kind="test.concurrent-claim", owner_id="key:claim", request_payload={})
        barrier = threading.Barrier(2)

        def claim(worker_id: str) -> JobRecord | None:
            barrier.wait()
            return repository.claim_next(worker_id=worker_id, lease_seconds=330, job_id=job.id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(claim, ("worker-a", "worker-b")))
        winners = [claimed for claimed in claims if claimed is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].attempt, 1)
        persisted = repository.get(job.id)
        assert persisted is not None
        self.assertEqual(persisted.status, "running")
        self.assertEqual(persisted.attempt, 1)

    def test_completion_wins_after_handler_final_cancellation_checkpoint(self) -> None:
        repository = SQLiteJobRepository(self.database_path)
        service = JobService(repository)
        job = service.submit(kind="test.late-cancel", owner_id="key:late", request_payload={})
        claimed = repository.claim_next(worker_id="worker-late", lease_seconds=60, job_id=job.id)
        self.assertIsNotNone(claimed)
        cancelling = service.cancel(job.id, owner_id="key:late", reason="late request")
        self.assertTrue(cancelling.cancel_requested)
        completed = repository.complete(job.id, worker_id="worker-late", result={"done": True})
        assert completed is not None
        self.assertEqual(completed.status, "completed")
        self.assertFalse(completed.cancel_requested)
        self.assertEqual(completed.result, {"done": True})

    def test_worker_throttles_idle_heartbeat_and_requeues_graceful_shutdown(self) -> None:
        repository = SQLiteJobRepository(self.database_path)
        clock = [0.0]
        idle_worker = PersistentJobWorker(
            repository,
            {},
            worker_id="idle-worker",
            heartbeat_interval_seconds=30,
            monotonic=lambda: clock[0],
        )
        with patch.object(repository, "heartbeat", wraps=repository.heartbeat) as heartbeat:
            idle_worker.run_once()
            clock[0] = 5
            idle_worker.run_once()
            clock[0] = 31
            idle_worker.run_once()
        self.assertEqual(heartbeat.call_count, 2)

        service = JobService(repository)
        heartbeat_job = service.submit(kind="test.heartbeat-failure", owner_id="key:heartbeat", request_payload={})

        class HeartbeatFailureHandler:
            def execute(self, job: JobRecord, context: JobExecutionContext) -> dict[str, Any]:
                context.update({"message": "still running"})
                return {"job_id": job.id}

        heartbeat_worker = PersistentJobWorker(
            repository,
            {"test.heartbeat-failure": HeartbeatFailureHandler()},
            worker_id="heartbeat-failure-worker",
        )
        with self.assertLogs("app.platform.jobs.worker", level="ERROR") as heartbeat_logs:
            with patch.object(repository, "heartbeat", side_effect=sqlite3.OperationalError("database is locked")):
                heartbeat_completed = heartbeat_worker.run_once(job_id=heartbeat_job.id)
        assert heartbeat_completed is not None
        self.assertEqual(heartbeat_completed.status, "completed")
        self.assertTrue(all("Worker heartbeat update failed" in line for line in heartbeat_logs.output))

        job = service.submit(kind="test.shutdown", owner_id="key:shutdown", request_payload={})
        worker: PersistentJobWorker

        class StopHandler:
            def execute(self, job: JobRecord, context: JobExecutionContext) -> dict[str, Any]:
                del job
                worker.request_stop()
                context.check_cancelled()
                return {}

        worker = PersistentJobWorker(repository, {"test.shutdown": StopHandler()}, worker_id="stopping-worker")
        requeued = worker.run_once(job_id=job.id)
        assert requeued is not None
        self.assertEqual(requeued.status, "queued")
        self.assertEqual(requeued.attempt, 0, "graceful shutdown must not consume a retry")
        with connect(self.database_path) as connection:
            event = connection.execute(
                "SELECT event_type, payload FROM background_job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (job.id,),
            ).fetchone()
        self.assertEqual(event["event_type"], "requeued")
        self.assertIn("worker_stopping", event["payload"])

    def test_recognition_cancellation_after_output_write_removes_files(self) -> None:
        upload_root = self.root / "uploads"
        input_dir = upload_root / "batch"
        input_dir.mkdir(parents=True)
        photo_path = input_dir / "label.png"
        photo_path.write_bytes(b"image")
        output_root = self.root / "outputs"
        output_dir = output_root / "u1"
        excel_path = output_dir / "货物识别" / "cancelled.xlsx"
        json_path = excel_path.with_suffix(".json")
        checks = 0

        def check_cancelled() -> None:
            nonlocal checks
            checks += 1
            if checks == 3:
                raise JobCancelledError("cancel at final write checkpoint")

        def write_outputs(**_kwargs):
            excel_path.parent.mkdir(parents=True, exist_ok=True)
            excel_path.write_bytes(b"xlsx")
            json_path.write_text("{}", encoding="utf-8")
            return excel_path, json_path, {
                "photos": 1,
                "labels": 0,
                "failed": 0,
                "seconds": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0,
            }

        service = ShipmentRecognitionService(
            JobService(SQLiteJobRepository(self.database_path)),
            lambda: self.fail("audit must not run after cancellation"),
            upload_root=upload_root,
            output_root=output_root,
        )
        photo = type("Photo", (), {"relative_name": "label.png"})()
        result = {
            "status": "ok",
            "seconds": 0,
            "usage": {},
            "ai_metrics": {},
            "result": {"labels": []},
        }
        with (
            patch("app.modules.shipping.recognition_service.recognizer.build_runtime_args", return_value=Namespace(limit=0)),
            patch("app.modules.shipping.recognition_service.recognizer.find_photos", return_value=[photo]),
            patch("app.modules.shipping.recognition_service.recognizer.recognize_photo", return_value=result),
            patch.object(service, "_write_outputs", side_effect=write_outputs),
        ):
            with self.assertRaises(JobCancelledError):
                service.execute(
                    job_id="job_cancel_output",
                    payload={
                        "input_dir": str(input_dir),
                        "output_dir": str(output_dir),
                        "run_date": "2026-07-11",
                        "safe_label": "test",
                        "uploaded_count": 1,
                        "limit": 0,
                        "actor": "test",
                    },
                    update_progress=lambda _progress: None,
                    check_cancelled=check_cancelled,
                )
        self.assertFalse(excel_path.exists())
        self.assertFalse(json_path.exists())

    def test_recognition_records_interrupted_provider_call_before_requeue(self) -> None:
        upload_root = self.root / "uploads"
        input_dir = upload_root / "batch"
        input_dir.mkdir(parents=True)
        photo_path = input_dir / "label.png"
        photo_path.write_bytes(b"image")
        output_root = self.root / "outputs"
        output_dir = output_root / "u1"
        output_dir.mkdir(parents=True)
        checks = 0

        def check_interrupted() -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                raise JobInterruptedError("worker stopping")

        metrics = AiCallMetrics(
            provider="openai-compatible",
            model="vision-1",
            data_type="shipment_label_photo",
            caller="test-suite",
            status="interrupted",
            attempts=1,
            latency_ms=10,
            error_code="ai.interrupted",
        )
        service = ShipmentRecognitionService(
            JobService(SQLiteJobRepository(self.database_path)),
            lambda: SQLiteShippingUnitOfWork(self.database_path),
            upload_root=upload_root,
            output_root=output_root,
        )
        photo = type("Photo", (), {"relative_name": "label.png"})()
        with (
            patch("app.modules.shipping.recognition_service.recognizer.build_runtime_args", return_value=Namespace(limit=0)),
            patch("app.modules.shipping.recognition_service.recognizer.find_photos", return_value=[photo]),
            patch(
                "app.modules.shipping.recognition_service.recognizer.recognize_photo",
                side_effect=AiProviderInterruptedError(metrics=metrics),
            ),
        ):
            with self.assertRaises(JobInterruptedError):
                service.execute(
                    job_id="job_interrupted_ai",
                    payload={
                        "input_dir": str(input_dir),
                        "output_dir": str(output_dir),
                        "run_date": "2026-07-11",
                        "safe_label": "test",
                        "uploaded_count": 1,
                        "limit": 0,
                        "actor": "test",
                    },
                    update_progress=lambda _progress: None,
                    check_cancelled=check_interrupted,
                )
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT status, attempts, error_code FROM ai_provider_calls WHERE job_id = ?",
                ("job_interrupted_ai",),
            ).fetchone()
        self.assertEqual(dict(row), {"status": "interrupted", "attempts": 1, "error_code": "ai.interrupted"})

    def test_product_package_extraction_rejects_traversal_and_size_expansion(self) -> None:
        cases = (
            ("traversal", "../escape.txt", b"x", {}),
            ("oversized", "data/large.bin", b"12345", {"max_member_bytes": 4}),
        )
        for label, member_name, content, limits in cases:
            with self.subTest(case=label):
                package_path = self.root / f"{label}.tar.gz"
                member = tarfile.TarInfo(member_name)
                member.size = len(content)
                with tarfile.open(package_path, "w:gz") as archive:
                    archive.addfile(member, io.BytesIO(content))
                destination = self.root / f"extract-{label}"
                destination.mkdir()
                with self.assertRaises(ValueError):
                    ProductPackageStore._safe_extract(package_path, destination, **limits)
                self.assertFalse((self.root / "escape.txt").exists())

    def test_ai_provider_retries_records_cost_and_limits_concurrency(self) -> None:
        calls = 0
        slept: list[float] = []

        def retrying_opener(_request, timeout):
            nonlocal calls
            self.assertEqual(timeout, 5)
            calls += 1
            if calls == 1:
                raise urllib.error.HTTPError(
                    "https://vision.example/v1/chat/completions",
                    429,
                    "rate limited",
                    Message(),
                    io.BytesIO(b"{}"),
                )
            return _Response(_provider_payload())

        config = VisionProviderConfig(
            api_key=SecretStr("secret"),
            base_url="https://vision.example/v1",
            model="vision-1",
            allowed_hosts=("vision.example",),
            allowed_models=("vision-1",),
            timeout_seconds=5,
            max_retries=1,
            retry_backoff_seconds=0.05,
            max_concurrency=1,
            input_cost_per_million=1,
            output_cost_per_million=2,
        )
        provider = OpenAICompatibleVisionProvider(config, opener=retrying_opener, sleeper=slept.append)
        completion = provider.complete(
            system_prompt="system",
            user_prompt="user",
            image_data_url="data:image/png;base64,AA==",
            caller="test-suite",
            data_type="shipment_label_photo",
        )
        self.assertEqual(calls, 2)
        self.assertEqual(slept, [0.05])
        self.assertEqual(completion.metrics.attempts, 2)
        self.assertEqual(completion.metrics.total_tokens, 12)
        self.assertEqual(completion.metrics.estimated_cost_usd, 0.000014)

        active = max_active = 0
        lock = threading.Lock()

        def slow_opener(_request, timeout):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return _Response(_provider_payload())

        limited = OpenAICompatibleVisionProvider(config, opener=slow_opener)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    limited.complete,
                    system_prompt="system",
                    user_prompt="user",
                    image_data_url="data:image/png;base64,AA==",
                    caller="test-suite",
                    data_type="shipment_label_photo",
                )
                for _ in range(2)
            ]
            [future.result() for future in futures]
        self.assertEqual(max_active, 1)

    def test_ai_provider_rejects_unapproved_egress_and_redirects(self) -> None:
        valid = {
            "api_key": SecretStr("secret"),
            "base_url": "https://vision.example/v1",
            "model": "vision-1",
            "allowed_hosts": ("vision.example",),
            "allowed_models": ("vision-1",),
        }
        invalid_configs = (
            {**valid, "base_url": "http://vision.example/v1"},
            {**valid, "base_url": "https://attacker.example/v1"},
            {**valid, "base_url": "https://vision.example:invalid/v1"},
            {**valid, "endpoint_path": "/chat/completions?override=1"},
        )
        for values in invalid_configs:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    VisionProviderConfig(**values)

        provider = OpenAICompatibleVisionProvider(VisionProviderConfig(**valid), opener=lambda *_args, **_kwargs: None)
        for redirect in (
            "http://vision.example/v1/chat/completions",
            "https://vision.example:8443/v1/chat/completions",
            "https://vision.example/v1/chat/completions?token=unsafe",
            "https://attacker.example/v1/chat/completions",
        ):
            with self.subTest(redirect=redirect):
                with self.assertRaises(urllib.error.URLError):
                    provider._validate_url(redirect)

    def test_ai_provider_checks_worker_interruption_before_retry(self) -> None:
        calls = 0
        checks = 0
        slept: list[float] = []

        def unavailable_opener(_request, _timeout=None, **_kwargs):
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError(
                "https://vision.example/v1/chat/completions",
                429,
                "rate limited",
                Message(),
                io.BytesIO(b"{}"),
            )

        def check_interrupted() -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                raise JobInterruptedError("worker stopping")

        config = VisionProviderConfig(
            api_key=SecretStr("secret"),
            base_url="https://vision.example/v1",
            model="vision-1",
            allowed_hosts=("vision.example",),
            allowed_models=("vision-1",),
            timeout_seconds=5,
            max_retries=2,
        )
        provider = OpenAICompatibleVisionProvider(config, opener=unavailable_opener, sleeper=slept.append)
        with self.assertRaises(AiProviderInterruptedError) as interrupted:
            provider.complete(
                system_prompt="system",
                user_prompt="user",
                image_data_url="data:image/png;base64,AA==",
                caller="test-suite",
                data_type="shipment_label_photo",
                check_interrupted=check_interrupted,
            )
        self.assertIsInstance(interrupted.exception.__cause__, JobInterruptedError)
        self.assertEqual(interrupted.exception.metrics.status, "interrupted")
        self.assertEqual(interrupted.exception.metrics.attempts, 1)
        self.assertEqual(interrupted.exception.metrics.error_code, "ai.interrupted")
        self.assertEqual(calls, 1)
        self.assertEqual(slept, [])

    def test_health_requires_worker_and_retention_is_dry_run_first(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeSettings(
                require_worker_for_readiness=False,
                worker_heartbeat_seconds=60,
                worker_stale_seconds=60,
            )
        with connect(self.database_path) as connection:
            ensure_default_admin(connection, username="health-admin", password="health-password")
        settings = RuntimeSettings(
            require_worker_for_readiness=True,
            worker_stale_seconds=60,
            upload_retention_days=1,
            output_retention_days=1,
            backup_retention_days=1,
        )
        health = RuntimeHealthService(self.database_path, settings)
        missing_database = self.root / "data" / "missing.sqlite3"
        missing_health = RuntimeHealthService(missing_database, settings)
        self.assertFalse(missing_health.worker_is_fresh())
        self.assertFalse(missing_health.readiness().ready)
        self.assertFalse(missing_database.exists(), "health checks must use a read-only database connection")
        unmigrated_database = self.root / "data" / "unmigrated.sqlite3"
        sqlite3.connect(unmigrated_database).close()
        unmigrated = RuntimeHealthService(unmigrated_database, settings).readiness()
        self.assertTrue(unmigrated.checks["database"]["ok"])
        self.assertEqual(unmigrated.checks["migrations"]["reason"], "missing_or_unreadable")
        self.assertEqual(unmigrated.checks["business_probe"]["reason"], "missing_or_unreadable")
        self.assertFalse(health.readiness().ready)
        repository = SQLiteJobRepository(self.database_path)
        repository.heartbeat("runtime-test-worker")
        self.assertTrue(health.readiness().ready)
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE runtime_heartbeats SET updated_at = ? WHERE component = 'worker'",
                ((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),),
            )
            connection.commit()
        self.assertFalse(health.worker_is_fresh(), "a far-future heartbeat must not mask a stopped worker")
        repository.heartbeat("fresh-runtime-test-worker")
        self.assertTrue(health.worker_is_fresh(), "a bad future row must not mask another fresh worker")
        self.assertTrue(health.readiness().ready)

        uploads = self.root / "uploads"
        outputs = self.root / "outputs"
        backups = self.root / "data" / "local-backups"
        old_upload = uploads / "old.txt"
        old_upload.parent.mkdir(parents=True)
        old_upload.write_text("old", encoding="utf-8")
        active_upload = uploads / "active-job" / "photo.png"
        active_upload.parent.mkdir(parents=True)
        active_upload.write_text("active", encoding="utf-8")
        old_stamp = (datetime.now() - timedelta(days=2)).timestamp()
        os.utime(old_upload, (old_stamp, old_stamp))
        os.utime(active_upload, (old_stamp, old_stamp))
        protected = outputs / "protected.xlsx"
        protected.parent.mkdir(parents=True)
        protected.write_bytes(b"protected")
        os.utime(protected, (old_stamp, old_stamp))
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO api_artifacts
                  (id, owner_id, filename, storage_path, content_type, size_bytes, sha256,
                   created_at, expires_at, last_downloaded_at)
                VALUES ('art_protected', 'key:1', 'protected.xlsx', ?, 'application/octet-stream',
                        9, 'digest', ?, ?, '')
                """,
                (
                    str(protected),
                    (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
                    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            connection.execute(
                """
                INSERT INTO api_artifacts
                  (id, owner_id, filename, storage_path, content_type, size_bytes, sha256,
                   created_at, expires_at, last_downloaded_at)
                VALUES ('art_expired_shared', 'key:old', 'protected.xlsx', ?, 'application/octet-stream',
                        9, 'digest', ?, ?, '')
                """,
                (
                    str(protected),
                    (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
                    (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            connection.commit()
        JobService(repository).submit(
            kind="test.protected-upload",
            owner_id="key:protected",
            request_payload={"protected_paths": [str(active_upload.parent)]},
        )
        retention = RuntimeRetentionService(
            self.database_path,
            upload_root=uploads,
            output_root=outputs,
            backup_roots=(backups,),
            settings=settings,
        )
        plan = retention.build_plan()
        self.assertIn(old_upload.resolve(), plan.files["uploads"])
        self.assertNotIn(active_upload.resolve(), plan.files["uploads"])
        self.assertNotIn(protected.resolve(), plan.files["outputs"])
        self.assertIn("art_expired_shared", plan.artifact_ids)
        self.assertTrue(old_upload.exists(), "building a plan must be dry-run")
        retention.apply(plan, actor="runtime-test")
        self.assertFalse(old_upload.exists())
        self.assertTrue(active_upload.exists())
        self.assertTrue(protected.exists())
        with connect(self.database_path) as connection:
            audit = connection.execute(
                "SELECT action FROM audit_logs WHERE target_type = 'runtime_retention' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(audit["action"], "执行运行数据保留期清理")

    def test_historical_database_fixture_matrix_upgrades_without_data_loss(self) -> None:
        expected_migrations = {migration_id for migration_id, _migration in MIGRATIONS}
        for fixture_path in sorted(FIXTURE_ROOT.glob("*.sql")):
            with self.subTest(fixture=fixture_path.name):
                database_path = self.root / f"{fixture_path.stem}.sqlite3"
                raw = sqlite3.connect(database_path)
                try:
                    raw.executescript(fixture_path.read_text(encoding="utf-8"))
                    raw.commit()
                finally:
                    raw.close()
                with connect(database_path) as connection:
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                    migrations = {
                        str(row["id"]) for row in connection.execute("SELECT id FROM schema_migrations").fetchall()
                    }
                    tables = {
                        str(row["name"])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        ).fetchall()
                    }
                    self.assertEqual(integrity, "ok")
                    self.assertTrue(expected_migrations.issubset(migrations))
                    self.assertIn("background_jobs", tables)
                    self.assertNotIn("shipment_recognition_jobs", tables)
                    if fixture_path.name.startswith("v000"):
                        product = connection.execute(
                            "SELECT bld_no, price_cny, product_status FROM products WHERE bld_no = 'HIST-000'"
                        ).fetchone()
                        self.assertEqual(product["bld_no"], "HIST-000")
                    if fixture_path.name.startswith("v006"):
                        job = connection.execute(
                            "SELECT status, error_code FROM background_jobs WHERE id = 'legacy-job-006'"
                        ).fetchone()
                        self.assertEqual(job["status"], "failed")
                        self.assertEqual(job["error_code"], "job.legacy_interrupted")
                    if fixture_path.name.startswith("v012"):
                        quote = connection.execute(
                            "SELECT bld_no, version FROM quote_records WHERE bld_no = 'HIST-Q-012'"
                        ).fetchone()
                        self.assertEqual(quote["version"], 1)


if __name__ == "__main__":
    unittest.main()
