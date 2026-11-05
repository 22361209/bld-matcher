from __future__ import annotations

import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tools import costing_trial as launcher


class CostingTrialNetworkTest(unittest.TestCase):
    @staticmethod
    def get(port: int, path: str) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(launcher.HOST, port, timeout=1.5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_idle_browser_connection_does_not_block_health_or_login(self):
        from app.modules.costing.trial_app import initialize_trial

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            initialize_trial(data_dir, password="isolated-network-test-password")
            with socket.socket() as probe:
                probe.bind((launcher.HOST, 0))
                port = probe.getsockname()[1]
            instance_id = "c" * 32
            with (data_dir / "test-server.log").open("wb") as log:
                process = subprocess.Popen(
                    [sys.executable, str(launcher.SCRIPT), "serve", "--data-dir", str(data_dir),
                     "--port", str(port), "--instance-id", instance_id],
                    cwd=launcher.ROOT, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            status, body = self.get(port, "/health/ready")
                            if status == 200:
                                break
                        except OSError:
                            pass
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail("Isolated trial server did not become ready")
                        time.sleep(0.05)
                    self.assertEqual(json.loads(body)["instance_id"], instance_id)
                    # A browser can connect before sending any HTTP request. Keep
                    # that first socket open while separate requests must succeed.
                    with socket.create_connection((launcher.HOST, port), timeout=1.5):
                        status, body = self.get(port, "/health/ready")
                        self.assertEqual(status, 200)
                        self.assertTrue(json.loads(body)["ok"])
                        status, body = self.get(port, "/login")
                        self.assertEqual(status, 200)
                        self.assertIn("进入成本核算".encode(), body)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)

    def test_recently_closed_connection_does_not_prevent_port_reuse(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((launcher.HOST, 0))
            port = listener.getsockname()[1]
            listener.listen(1)
            with socket.create_connection((launcher.HOST, port), timeout=1.5) as client:
                accepted, _ = listener.accept()
                with accepted:
                    # The server actively closes first, leaving its accepted
                    # connection in TIME_WAIT after the peer acknowledges EOF.
                    accepted.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b"")
        launcher._check_port(port)

    def test_live_listener_is_still_reported_as_occupied(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((launcher.HOST, 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(launcher.LauncherError, "端口已被占用"):
                launcher._check_port(port)


if __name__ == "__main__":
    unittest.main()
