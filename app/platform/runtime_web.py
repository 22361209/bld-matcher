from __future__ import annotations

from flask import jsonify

from .runtime_factory import get_runtime_health_service


def register(app) -> None:
    @app.get("/health/live")
    def health_live():
        return jsonify({"status": "alive"})

    @app.get("/health/ready")
    def health_ready():
        report = get_runtime_health_service().readiness()
        return jsonify(report.payload()), 200 if report.ready else 503
