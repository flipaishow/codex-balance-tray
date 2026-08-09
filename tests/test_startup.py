import plistlib
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from codex_tray.startup import (
    MACOS_LAUNCH_AGENT_LABEL,
    RUN_KEY_PATH,
    STARTUP_VALUE_NAME,
    build_startup_command,
    default_launch_agent_path,
    ensure_startup_enabled,
    registered_startup_command,
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


    def test_macos_builds_and_persists_a_launch_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            launch_agent = default_launch_agent_path(home=home, platform="darwin")
            executable = PurePosixPath("/Applications/CodexBalanceTray.app/Contents/MacOS/CodexBalanceTray")
            command = build_startup_command(
                executable=executable,
                frozen=True,
                platform="darwin",
            )

            self.assertTrue(
                ensure_startup_enabled(
                    platform="darwin",
                    launch_agent_path=launch_agent,
                    command=command,
                )
            )

            payload = plistlib.loads(launch_agent.read_bytes())
            self.assertEqual(payload["Label"], MACOS_LAUNCH_AGENT_LABEL)
            self.assertEqual(payload["ProgramArguments"], [str(executable)])
            self.assertTrue(payload["RunAtLoad"])
            self.assertEqual(
                registered_startup_command(
                    platform="darwin",
                    launch_agent_path=launch_agent,
                ),
                command,
            )
            self.assertTrue(
                ensure_startup_enabled(
                    platform="darwin",
                    launch_agent_path=launch_agent,
                    command=command,
                )
            )

    def test_macos_source_startup_command_quotes_script_paths(self):
        command = build_startup_command(
            executable=PurePosixPath("/usr/bin/python3"),
            script=PurePosixPath("/Users/test-user/Codex Tray/main.py"),
            frozen=False,
            platform="darwin",
        )

        self.assertEqual(command, "/usr/bin/python3 '/Users/test-user/Codex Tray/main.py'")


if __name__ == "__main__":
    unittest.main()
