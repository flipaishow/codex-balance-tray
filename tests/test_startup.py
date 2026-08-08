import unittest
from pathlib import Path

from codex_tray.startup import (
    RUN_KEY_PATH,
    STARTUP_VALUE_NAME,
    build_startup_command,
    ensure_startup_enabled,
)


class FakeKey:
    def __init__(self, registry):
        self.registry = registry

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.opened_paths = []

    def CreateKeyEx(self, root, path, _reserved, access):
        self.opened_paths.append((root, path, access))
        return FakeKey(self)

    def OpenKey(self, root, path, _reserved, access):
        self.opened_paths.append((root, path, access))
        if not self.values:
            raise FileNotFoundError(path)
        return FakeKey(self)

    def SetValueEx(self, _key, name, _reserved, _value_type, value):
        self.values[name] = value

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ


class StartupTests(unittest.TestCase):
    def test_build_startup_command_for_packaged_executable(self):
        command = build_startup_command(
            executable=Path(r"C:\Program Files\CodexBalanceTray.exe"),
            frozen=True,
        )

        self.assertEqual(command, r'"C:\Program Files\CodexBalanceTray.exe"')

    def test_build_startup_command_for_source_script(self):
        command = build_startup_command(
            executable=Path(r"C:\Python311\python.exe"),
            script=Path(r"C:\Test\Codex Balance\main.py"),
            frozen=False,
        )

        self.assertEqual(
            command,
            r'"C:\Python311\python.exe" "C:\Test\Codex Balance\main.py"',
        )

    def test_ensure_startup_enabled_registers_current_command(self):
        registry = FakeRegistry()
        command = r'"C:\Program Files\CodexBalanceTray.exe"'

        self.assertTrue(
            ensure_startup_enabled(registry=registry, command=command)
        )

        self.assertEqual(registry.values[STARTUP_VALUE_NAME], command)
        self.assertIn(
            (registry.HKEY_CURRENT_USER, RUN_KEY_PATH, registry.KEY_SET_VALUE),
            registry.opened_paths,
        )

    def test_ensure_startup_enabled_does_not_rewrite_matching_command(self):
        registry = FakeRegistry()
        command = r'"C:\Program Files\CodexBalanceTray.exe"'
        registry.values[STARTUP_VALUE_NAME] = command

        self.assertTrue(
            ensure_startup_enabled(registry=registry, command=command)
        )

        self.assertEqual(
            [entry for entry in registry.opened_paths if entry[2] == registry.KEY_SET_VALUE],
            [],
        )


if __name__ == "__main__":
    unittest.main()
