"""Modded exes: install, keep across a GAMMA update, restore the original."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from commander_gui import modded_exes as exes


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")  # ROMEO's "CLIKRΔD" isn't in Windows' cp1252


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "commander-exes" not in p.parts
    }


def _release(tmp: Path, version: str) -> Path:
    path = tmp / f"exes-{version}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name in ("AnomalyDX11.exe", "AnomalyDX11AVX.exe", "AnomalyDX11.pdb"):
            zf.writestr(f"bin/{name}", version)
        zf.writestr(f"db/mods/{exes.GAMEDATA_DB}", version)
    return path


def _gamma_anomaly(root: Path) -> None:
    """What GAMMA's installer leaves: its exes, placeholder and loose gamedata."""
    _write(root / "bin/AnomalyDX11.exe", "gamma")
    _write(root / "bin/AnomalyDX11AVX.exe", "gamma")
    _write(root / "bin/xrCore.dll", "engine")
    _write(root / "db/mods/000_shader_placeholder.db0", "gamma")
    _write(root / "gamedata/configs/axr_options.ltx", "player")
    _write(root / "gamedata/configs/cache_dbg.ltx", "")
    _write(root / "gamedata/configs/unlocalizers/a.ltx", "gamma")
    _write(root / "gamedata/scripts/class_registrator_modded_exes.script", "gamma")
    _write(root / "gamedata/shaders/a.ps", "gamma")
    _write(root / "gamedata/levels/l01/level.ltx", "gamma")
    _write(root / "gamedata/textures/t.dds", "gamma")


class ModdedExesTest(unittest.TestCase):
    def test_install_cleans_up_and_restore_puts_everything_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            before = _tree(anomaly)

            exes.install(_release(tmp, "2026.9.12"), anomaly, "2026.9.12")

            self.assertEqual(
                _tree(anomaly),
                {
                    "bin/AnomalyDX11.exe": "2026.9.12",
                    "bin/AnomalyDX11AVX.exe": "2026.9.12",
                    "bin/AnomalyDX11.pdb": "2026.9.12",
                    "bin/xrCore.dll": "engine",
                    f"db/mods/{exes.GAMEDATA_DB}": "2026.9.12",
                    "gamedata/configs/axr_options.ltx": "player",
                    "gamedata/configs/cache_dbg.ltx": "",
                    "gamedata/levels/l01/level.ltx": "gamma",
                    "gamedata/textures/t.dds": "gamma",
                },
            )
            self.assertEqual(exes.load_state(anomaly)["previous"], None)

            exes.install(_release(tmp, "2026.10.11"), anomaly, "2026.10.11")
            state = exes.load_state(anomaly)
            self.assertEqual((state["version"], state["previous"]), ("2026.10.11", "2026.9.12"))

            exes.restore_original(anomaly)
            self.assertEqual(_tree(anomaly), before)
            self.assertFalse((anomaly / "commander-exes").exists())

    def test_a_failed_first_install_puts_everything_back(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            before = _tree(anomaly)
            with patch.object(exes, "_save_state", side_effect=OSError("disk full")), self.assertRaises(OSError):
                exes.install(_release(tmp, "2026.9.12"), anomaly, "2026.9.12")
            self.assertEqual(_tree(anomaly), before)
            self.assertFalse((anomaly / "commander-exes" / "original").exists())

            # The retry is a first install again, so restore still works.
            exes.install(_release(tmp, "2026.9.12"), anomaly, "2026.9.12")
            exes.restore_original(anomaly)
            self.assertEqual(_tree(anomaly), before)

    def test_gamma_update_with_kept_exes_is_cleaned_up_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            exes.install(_release(tmp, "2026.10.11"), anomaly, "2026.10.11")
            installed = _tree(anomaly)
            # A GAMMA update copies its loose gamedata and placeholder back.
            _write(anomaly / "gamedata/scripts/class_registrator_modded_exes.script", "gamma")
            _write(anomaly / "db/mods/000_shader_placeholder.db0", "gamma")

            exes.after_gamma_update(anomaly, kept_exes=True)
            self.assertEqual(_tree(anomaly), installed)

            exes.after_gamma_update(anomaly, kept_exes=False)
            self.assertIsNone(exes.load_state(anomaly))
            self.assertFalse((anomaly / "db/mods" / exes.GAMEDATA_DB).exists())

    def test_refuses_a_zip_that_is_not_modded_exes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            before = _tree(anomaly)
            bad = tmp / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("gamedata/scripts/x.script", "x")
            with self.assertRaises(exes.ExesError):
                exes.install(bad, anomaly, "file:bad.zip")
            self.assertEqual(_tree(anomaly), before)

    def test_refuses_while_coop_is_on_but_not_when_switched_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            _write(anomaly / "commander-coop/state.json", '{"active": true, "files": {}}')
            with self.assertRaises(exes.ExesError):
                exes.install(_release(tmp, "2026.10.11"), anomaly, "2026.10.11")
            _write(anomaly / "commander-coop/state.json", '{"active": false, "files": {}}')
            exes.install(_release(tmp, "2026.10.11"), anomaly, "2026.10.11")
            self.assertEqual(exes.load_state(anomaly)["version"], "2026.10.11")

    def test_coop_round_trip_keeps_the_new_exes(self):
        import hashlib
        import json

        from commander_gui import coop

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            # Co-op installed for plain Anomaly and switched off: its engine
            # waits under commander-coop/files.
            _write(anomaly / "commander-coop/files/bin/AnomalyDX11AVX.exe", "xrrazom")
            digest = hashlib.sha256(b"xrrazom").hexdigest()
            _write(anomaly / "commander-coop/state.json", json.dumps({
                "active": False, "kind": coop.KIND_ANOMALY,
                "files": {"bin/AnomalyDX11AVX.exe": digest}, "originals": [],
            }))

            exes.install(_release(tmp, "2026.10.11"), anomaly, "2026.10.11")
            coop.switch_to_coop(anomaly, "", "")
            self.assertEqual((anomaly / "bin/AnomalyDX11AVX.exe").read_text(), "xrrazom")
            coop.switch_to_singleplayer(anomaly, "", "")

            self.assertEqual((anomaly / "bin/AnomalyDX11AVX.exe").read_text(), "2026.10.11")
            self.assertEqual((anomaly / "bin/AnomalyDX11.exe").read_text(), "2026.10.11")

    def test_at_least_uses_record_then_build_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            anomaly = Path(tmp)
            self.assertFalse(exes.at_least(anomaly, "2026.9.12"))
            # Hand-installed exes: only the PE header's build date to go on.
            pe = bytearray(4096)
            pe[0x3C:0x40] = (0x80).to_bytes(4, "little")
            pe[0x80:0x84] = b"PE\0\0"
            pe[0x88:0x8C] = (1_789_200_000).to_bytes(4, "little")  # 2026-09-12
            (anomaly / "bin").mkdir()
            (anomaly / "bin/AnomalyDX11.exe").write_bytes(pe)
            self.assertTrue(exes.at_least(anomaly, "2026.9.12"))
            self.assertFalse(exes.at_least(anomaly, "2026.9.13"))
            # COMMANDER's own record wins over the build date.
            _write(anomaly / "commander-exes/state.json", '{"version": "2026.9.1"}')
            self.assertFalse(exes.at_least(anomaly, "2026.9.12"))

    def test_switching_engines_starts_from_the_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            before = _tree(anomaly)
            exes.install(_release(tmp, "2026.9.12"), anomaly, "2026.9.12")

            exes.install(_next_ray(tmp), anomaly, "neXt-Ray 0.2.2")

            tree = _tree(anomaly)
            # demonized's non-AVX exe is gone: GAMMA's is back beside neXt-Ray's.
            self.assertEqual(tree["bin/AnomalyDX11.exe"], "gamma")
            self.assertEqual(tree["bin/AnomalyDX12AVX.exe"], "nextray")
            self.assertNotIn("bin/AnomalyDX11.pdb", tree)
            state = exes.load_state(anomaly)
            self.assertEqual((state["engine"], state["previous"]), (exes.NEXT_RAY, None))
            self.assertFalse(exes.at_least(anomaly, "2026.5.15"))

            exes.restore_original(anomaly)
            self.assertEqual(_tree(anomaly), before)

    def test_gamma_update_without_kept_exes_drops_next_ray_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            exes.install(_next_ray(tmp), anomaly, "neXt-Ray 0.2.2")
            _write(anomaly / "bin/AnomalyDX11AVX.exe", "gamma")  # the update's own exe

            exes.after_gamma_update(anomaly, kept_exes=False)

            tree = _tree(anomaly)
            self.assertNotIn("bin/AnomalyDX12AVX.exe", tree)
            self.assertNotIn("bin/nvngx_dlss.dll", tree)
            self.assertNotIn(f"db/mods/{exes.GAMEDATA_DB}", tree)
            self.assertIsNone(exes.load_state(anomaly))

    def test_only_next_ray_targets_launch_while_it_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            dx12 = str(anomaly / "bin" / exes.DX12_EXE)
            for binary in ("C:/a/bin/AnomalyDX11.exe", "AnomalyLauncher.exe"):
                self.assertTrue(exes.runs_installed_engine(anomaly, binary))
            self.assertFalse(exes.runs_installed_engine(anomaly, dx12))  # entry left without its exe
            exes.install(_next_ray(tmp), anomaly, "neXt-Ray 0.2.2")
            self.assertTrue(exes.runs_installed_engine(anomaly, dx12))
            self.assertFalse(exes.runs_installed_engine(anomaly, "C:/a/bin/AnomalyDX11.exe"))
            self.assertFalse(exes.runs_installed_engine(anomaly, "C:\\a\\bin\\anomalydx9avx.exe"))
            for binary in ("/a/bin/AnomalyDX11AVX.exe", "/a/AnomalyLauncher.exe", "/x/Explorer++.exe"):
                self.assertTrue(exes.runs_installed_engine(anomaly, binary))

    def test_coop_will_not_switch_on_over_next_ray(self):
        import json

        from commander_gui import coop

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            anomaly = tmp / "anomaly"
            _gamma_anomaly(anomaly)
            exes.install(_next_ray(tmp), anomaly, "neXt-Ray 0.2.2")
            _write(anomaly / "commander-coop/state.json", json.dumps({
                "active": False, "kind": coop.KIND_ANOMALY, "files": {}, "originals": [],
            }))
            with self.assertRaises(coop.CoopError):
                coop.switch_to_coop(anomaly, "", "")
            self.assertEqual((anomaly / "bin/AnomalyDX11AVX.exe").read_text(), "nextray")


def _next_ray(tmp: Path) -> Path:
    path = tmp / "neXt-Ray-0.2.2.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name in ("AnomalyDX11AVX.exe", "AnomalyDX12AVX.exe", "nvngx_dlss.dll"):
            zf.writestr(f"bin/{name}", "nextray")
        zf.writestr(f"db/mods/{exes.GAMEDATA_DB}", "nextray")
    return path


class Mo2ExecutableTest(unittest.TestCase):
    def test_dx12_entry_is_added_picked_and_removed(self):
        from commander_gui import launcher

        ini = (
            "[customExecutables]\r\nsize=2\r\n"
            "1\\binary=Z:/a/bin/AnomalyDX11AVX.exe\r\n1\\title=Anomaly (DX11-AVX)\r\n1\\workingDirectory=Z:/a/bin\r\n"
            "2\\binary=Z:/a/bin/AnomalyDX11.exe\r\n2\\title=Anomaly (DX11)\r\n"
            "\r\n[Widgets]\r\nMainWindow_executablesListBox_index=1\r\n"
        )
        with tempfile.TemporaryDirectory() as gamma:
            path = Path(gamma) / "ModOrganizer.ini"
            path.write_bytes(ini.encode())
            self.assertTrue(launcher.set_mo2_executable(gamma, exes.DX12_TITLE, exes.DX12_EXE, True))
            self.assertFalse(launcher.set_mo2_executable(gamma, exes.DX12_TITLE, exes.DX12_EXE, True))
            added = launcher.parse_mo2_executables(gamma)[-1]
            self.assertEqual(added.title, exes.DX12_TITLE)
            # Z: stays a drive on Windows; elsewhere it maps to /.
            self.assertTrue(added.binary.endswith("/a/bin/AnomalyDX12AVX.exe"), added.binary)
            self.assertTrue(launcher.set_mo2_selected_title(gamma, exes.DX12_TITLE))

            self.assertTrue(launcher.set_mo2_executable(gamma, exes.DX12_TITLE, exes.DX12_EXE, False))
            self.assertEqual(launcher.mo2_selected_title(gamma), "Anomaly (DX11-AVX)")
            self.assertNotIn(exes.DX12_TITLE, path.read_text())

    def test_gamma_update_puts_back_or_drops_the_dx12_entry(self):
        from commander_gui import keep_state, launcher

        ini = (
            "[customExecutables]\nsize=1\n"
            "1\\binary=Z:/a/bin/AnomalyDX11AVX.exe\n1\\title=Anomaly (DX11-AVX)\n"
        )
        for kept in (True, False):
            with self.subTest(kept=kept), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                anomaly, gamma = tmp / "anomaly", tmp / "gamma"
                _gamma_anomaly(anomaly)
                exes.install(_next_ray(tmp), anomaly, "neXt-Ray 0.2.2")
                gamma.mkdir()
                (gamma / "ModOrganizer.ini").write_text(ini)  # the update rewrote it without DX12
                if not kept:
                    launcher.set_mo2_executable(str(gamma), exes.DX12_TITLE, exes.DX12_EXE, True)
                state = keep_state.KeptState(ini_path=gamma / "ModOrganizer.ini", anomaly=anomaly, keep_exes=kept)
                keep_state.restore(state)
                titles = [e.title for e in launcher.parse_mo2_executables(str(gamma))]
                self.assertEqual(exes.DX12_TITLE in titles, kept)
                self.assertEqual(exes.installed_engine(anomaly), exes.NEXT_RAY if kept else None)


if __name__ == "__main__":
    unittest.main()


class InstalledTextTest(unittest.TestCase):
    def test_names_the_engine_in_use(self) -> None:
        from commander_gui.ui.exes_card import installed_text

        self.assertEqual(installed_text("/a", {"engine": "nextray", "version": "neXt-Ray 0.2.2"}), "In use: neXt-Ray 0.2.2")
        self.assertEqual(installed_text("/a", {"version": "2026.10.11"}), "In use: Modded exes (demonized) 2026.10.11")
        self.assertEqual(installed_text("/a", {"engine": "nextray", "version": "file:nx.zip"}), "In use: neXt-Ray from nx.zip")


class FrameGenSkipsDx12Test(unittest.TestCase):
    def test_mako_not_active_in_next_ray_dx12(self) -> None:
        # MAKO over vkd3d-proton held neXt-Ray's DX12 exe at about 15 FPS.
        from commander_gui import launch_options

        self.assertNotIn(exes.DX12_EXE, launch_options._GAMES)
        self.assertIn("AnomalyDX11AVX.exe", launch_options._GAMES)


class RomeoOnNextRayTest(unittest.TestCase):
    def test_only_romeo_profiles_over_next_ray(self) -> None:
        from types import SimpleNamespace

        from commander_gui import romeo

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gamma, anomaly = root / "gamma", root / "anomaly"
            _write(gamma / "profiles" / "G.A.M.M.A" / "modlist.txt", "+a\n")
            _write(gamma / "profiles" / "ROMEO" / "modlist.txt", f"+{romeo.MCM_MOD}\n")
            _gamma_anomaly(anomaly)

            def on(mo2: str) -> bool:
                return romeo.on_next_ray(SimpleNamespace(gamma=str(gamma), anomaly=str(anomaly), mo2_profile=mo2))

            self.assertFalse(on("ROMEO"))  # GAMMA's engine
            exes.install(_next_ray(root), anomaly, "neXt-Ray 0.2.2")
            self.assertTrue(on("ROMEO"))
            self.assertFalse(on("G.A.M.M.A"))
            self.assertFalse(romeo.on_next_ray(None))


def test_release_text_marks_installed_and_last_used():
    from commander_gui.ui.exes_card import release_text

    rel = {"tag": "2026.11.22", "published": "2026-11-22"}
    nr = {"tag": "1.2", "published": "x"}
    state = {"engine": exes.NEXT_RAY, "version": "neXt-Ray 1.2", "last": {exes.MODDED: "2026.11.22"}}
    assert release_text(rel, state, exes.MODDED).endswith("last used")
    assert release_text(nr, state, exes.NEXT_RAY).endswith("installed")
    assert "installed" not in release_text(rel, None, exes.MODDED)
