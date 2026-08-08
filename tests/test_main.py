import unittest
from unittest.mock import patch

import main


class MainTests(unittest.TestCase):
    def test_main_registers_login_startup_before_running_tray(self):
        with patch("main.ensure_startup_enabled") as ensure_startup, patch(
            "main.TrayApplication"
        ) as tray_application:
            main.main()

        ensure_startup.assert_called_once_with()
        tray_application.assert_called_once_with()
        tray_application.return_value.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
