from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from deepfos_platform.options import _resolve_instance_host


class PlatformOptionsTests(unittest.TestCase):
    def test_instance_host_uses_resolved_hostname(self) -> None:
        with (
            patch("deepfos_platform.options.socket.gethostname", return_value="deep-agent.local"),
            patch("deepfos_platform.options.socket.gethostbyname", return_value="192.0.2.10"),
        ):
            self.assertEqual(_resolve_instance_host(), "192.0.2.10")

    def test_instance_host_falls_back_when_hostname_is_not_resolvable(self) -> None:
        with (
            patch("deepfos_platform.options.socket.gethostname", return_value="missing.local"),
            patch(
                "deepfos_platform.options.socket.gethostbyname",
                side_effect=socket.gaierror(8, "nodename nor servname provided"),
            ),
        ):
            self.assertEqual(_resolve_instance_host(), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
