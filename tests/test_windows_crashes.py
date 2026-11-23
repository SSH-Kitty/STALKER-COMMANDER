"""Windows' own crash records in the log dump (commander_gui/wincrash.py)
and what the ASSISTANT makes of them (assistant/analyzers/windows_crashes.py)."""

import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from assistant.analyzers import _ROUTES
from assistant.analyzers.windows_crashes import analyze_windows_crashes
from assistant.findings import CATEGORY_MO2, CATEGORY_SYSTEM, Severity
from commander_gui import log_dump, wincrash

WINDOWS = os.name == "nt"

REPORT = """Version=1
EventType=APPCRASH
EventTime=134398384877979626
Sig[0].Name=Anwendungsname
Sig[0].Value=AnomalyDX11AVX.exe
Sig[1].Value=1.5.3.0
Sig[3].Name=Fehlermodulname
Sig[3].Value=nvwgf2umx.dll
Sig[6].Name=Ausnahmecode
Sig[6].Value=c0000005
AppPath=C:\\Users\\Player\\GAMMA\\anomaly\\bin\\AnomalyDX11AVX.exe
"""

EVENTS = """<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Application Error'/>
<EventID Qualifiers='0'>1000</EventID><TimeCreated SystemTime='2026-11-20T21:03:11.46Z'/></System><EventData>
<Data>AnomalyDX11AVX.exe</Data><Data>1.5.3.0</Data><Data>5e6fa7d0</Data><Data>xrGame.dll</Data><Data>1.0</Data>
<Data>56511854</Data><Data>80000003</Data><Data>00000000000e95a2</Data></EventData></Event>
<Event><System><EventID>1000</EventID><TimeCreated SystemTime='2026-11-20T22:00:00Z'/></System><EventData>
<Data>chrome.exe</Data><Data>1</Data><Data>2</Data><Data>chrome.dll</Data><Data>3</Data><Data>4</Data><Data>c0000005</Data></EventData></Event>
<Event><System><EventID>1002</EventID><TimeCreated SystemTime='2026-11-21T08:15:00Z'/></System><EventData>
<Data>ModOrganizer.exe</Data><Data>2.5.2</Data></EventData></Event>"""


class ReportTest(unittest.TestCase):
    def test_a_crash_report_is_read_by_position_in_any_language(self):
        crash = wincrash.parse_report(REPORT)
        self.assertEqual(
            (crash.kind, crash.app, crash.module, crash.code, crash.source),
            ("crash", "AnomalyDX11AVX.exe", "nvwgf2umx.dll", "c0000005", "WER"),
        )
        self.assertRegex(crash.when, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_hangs_and_blocked_overruns(self):
        hang = wincrash.parse_report("EventType=AppHangB1\nSig[0].Value=ModOrganizer.exe\n")
        self.assertEqual((hang.kind, hang.module, hang.code), ("hang", "", ""))
        bex = wincrash.parse_report("EventType=BEX64\nSig[0].Value=AnomalyDX11.exe\nSig[3].Value=x.dll\nSig[6].Value=0000ff\nSig[7].Value=c0000409\n")
        self.assertEqual(bex.code, "c0000409")
        self.assertIsNone(wincrash.parse_report("EventType=WindowsUpdateFailure\nSig[0].Value=x\n"))

    def test_reports_are_utf16_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Report.wer"
            path.write_bytes(REPORT.encode("utf-16"))
            self.assertIn("nvwgf2umx.dll", wincrash.read_report(path))

    def test_only_the_game_programs_recent_report_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("AppCrash_AnomalyDX11AVX._1f_d3_aa", "AppCrash_chrome.exe_1f_d3_bb",
                         "AppHang_ModOrganizer.ex_1f_d3_cc", "AppCrash_AnomalyDX11.exe_old_dd", "NonCritical_x_y_z"):
                (root / name).mkdir()
                (root / name / "Report.wer").write_bytes(REPORT.encode("utf-16"))
            old = time.time() - 90 * 86400
            os.utime(root / "AppCrash_AnomalyDX11.exe_old_dd", (old, old))
            found = sorted(p.name for p in wincrash.wer_report_folders([root]))
        self.assertEqual(found, ["AppCrash_AnomalyDX11AVX._1f_d3_aa", "AppHang_ModOrganizer.ex_1f_d3_cc"])


class EventLogTest(unittest.TestCase):
    def test_game_crashes_and_hangs_only(self):
        crashes = wincrash.parse_events(EVENTS)
        self.assertEqual(
            [(c.kind, c.app, c.module, c.code, c.when) for c in crashes],
            [
                ("crash", "AnomalyDX11AVX.exe", "xrGame.dll", "80000003", "2026-11-20 21:03"),
                ("hang", "ModOrganizer.exe", "", "", "2026-11-21 08:15"),
            ],
        )

    def test_unreadable_output_gives_nothing(self):
        self.assertEqual(wincrash.parse_events(""), [])
        self.assertEqual(wincrash.parse_events("<Event><broken"), [])


class DumpEntriesTest(unittest.TestCase):
    def test_a_crash_in_both_sources_is_listed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "AppCrash_AnomalyDX11AVX._1f_d3_aa"
            folder.mkdir()
            (folder / "Report.wer").write_bytes(REPORT.encode("utf-16"))
            (folder / "minidump.mdmp").write_bytes(b"MDMP")
            same = wincrash.parse_report(REPORT)
            with (
                patch.object(wincrash, "wer_report_folders", return_value=[folder]),
                patch.object(wincrash, "_events_xml", return_value=""),
                patch.object(wincrash, "parse_events", return_value=[wincrash.Crash(same.when, "crash", same.app, same.module, same.code, "Event Log")]),
                patch.object(wincrash, "crash_dump_files", return_value=[]),
            ):
                texts, files = wincrash.dump_entries()
        listed = [line for line in texts["crash-reports.txt"].splitlines() if "app=" in line]
        self.assertEqual(len(listed), 1)
        self.assertIn(f"wer/{folder.name}.txt", texts)
        self.assertEqual(list(files), [f"wer/{folder.name}-minidump.mdmp"])

    def test_no_crashes_says_so(self):
        with (
            patch.object(wincrash, "wer_report_folders", return_value=[]),
            patch.object(wincrash, "_events_xml", return_value=""),
            patch.object(wincrash, "crash_dump_files", return_value=[]),
        ):
            texts, files = wincrash.dump_entries()
        self.assertTrue(texts["crash-reports.txt"].rstrip().endswith("none"))
        self.assertEqual(files, {})

    def test_crash_dumps_of_other_programs_are_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("AnomalyDX11AVX.exe.1234.dmp", "chrome.exe.55.dmp", "ModOrganizer.exe.9.dmp", "notes.txt"):
                (Path(tmp) / name).write_bytes(b"x")
            names = sorted(p.name for p in wincrash.crash_dump_files(Path(tmp)))
        self.assertEqual(names, ["AnomalyDX11AVX.exe.1234.dmp", "ModOrganizer.exe.9.dmp"])


class ArchiveTest(unittest.TestCase):
    def test_windows_texts_are_redacted_and_dumps_packed_as_they_are(self):
        home = str(Path.home())
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "AnomalyDX11AVX.exe.1.dmp"
            dump.write_bytes(b"MDMP" + home.encode())
            big = Path(tmp) / "big.dmp"
            big.write_bytes(b"x" * 2000)
            archive, stats = log_dump.build_log_dump(
                Path(tmp) / "out",
                {},
                extra_entries={
                    "windows/crash-reports.txt": f"app=AnomalyDX11AVX.exe path={home}\\GAMMA",
                    "windows/crashdumps/AnomalyDX11AVX.exe.1.dmp": dump,
                    "windows/crashdumps/big.dmp": big,
                },
                per_file_cap=1000,
            )
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())
                text = zf.read("windows/crash-reports.txt").decode("utf-8")
                raw = zf.read("windows/crashdumps/AnomalyDX11AVX.exe.1.dmp")
                manifest = zf.read("MANIFEST.txt").decode("utf-8")
        self.assertNotIn(home, text)
        self.assertEqual(raw, b"MDMP" + home.encode())
        self.assertNotIn("windows/crashdumps/big.dmp", names)
        self.assertIn("NOT REDACTED - raw memory dump", manifest)
        self.assertEqual(stats["skipped"], 1)

    def test_an_unsafe_archive_path_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            log_dump.build_log_dump(Path(tmp), {}, extra_entries={"windows/../evil.txt": "x"})

    @unittest.skipIf(not WINDOWS, "Windows' own crash records")
    def test_a_windows_dump_has_the_windows_folder(self):
        with (
            patch.object(log_dump, "log_dumps_dir", return_value=Path(tempfile.mkdtemp())),
            patch.object(log_dump, "gather_default_sources", return_value={}),
        ):
            archive, _stats = log_dump.create_log_dump()
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            diagnostics = zf.read("report/diagnostics.txt").decode("utf-8")
        self.assertIn("windows/crash-reports.txt", names)
        self.assertIn("Page file:", diagnostics)
        self.assertIn("Smart App Control:", diagnostics)


class AnalyzerTest(unittest.TestCase):
    def _findings(self, *lines):
        return analyze_windows_crashes("windows/crash-reports.txt", "Windows crash list", list(lines))

    def test_the_crash_list_is_routed_to_this_analyzer(self):
        analyzer = next(a for pattern, a in _ROUTES if pattern.search("windows/crash-reports.txt"))
        self.assertIs(analyzer, analyze_windows_crashes)

    def test_where_the_game_crashed_decides_the_advice(self):
        findings = self._findings(
            "2026-11-20 21:03  crash   app=AnomalyDX11AVX.exe  module=nvwgf2umx.dll  code=c0000005  source=WER",
            "2026-11-21 21:03  crash   app=AnomalyDX11AVX.exe  module=nvwgf2umx.dll  code=c0000005  source=WER",
            "2026-11-21 22:00  crash   app=AnomalyDX11AVX.exe  module=GameOverlayRenderer64.dll  code=c0000005  source=WER",
            "2026-11-21 22:10  crash   app=AnomalyDX11AVX.exe  module=xrGame.dll  code=80000003  source=WER",
            "2026-11-21 23:00  hang    app=ModOrganizer.exe  module=-  code=-  source=WER",
        )
        by_title = {f.title: f for f in findings}
        driver = by_title["AnomalyDX11AVX.exe crashed inside the graphics driver (nvwgf2umx.dll)."]
        self.assertEqual(driver.category, CATEGORY_SYSTEM)
        self.assertIn("2 time(s)", driver.detail)
        self.assertIn("graphics driver", driver.suggestion)
        self.assertIn("overlay", by_title["AnomalyDX11AVX.exe crashed inside an overlay (GameOverlayRenderer64.dll)."].suggestion)
        self.assertIn("engine log", by_title["AnomalyDX11AVX.exe crashed inside the game engine (xrGame.dll)."].suggestion)
        hang = by_title["ModOrganizer.exe stopped responding."]
        self.assertEqual((hang.severity, hang.category), (Severity.WARNING, CATEGORY_MO2))

    def test_one_crash_in_both_sources_counts_once(self):
        findings = self._findings(
            "2026-11-20 21:03  crash   app=AnomalyDX11.exe  module=xrCore.dll  code=c0000005  source=WER",
            "2026-11-20 21:03  crash   app=AnomalyDX11.exe  module=xrCore.dll  code=c0000005  source=Event Log",
        )
        self.assertIn("1 time(s)", findings[0].detail)

    def test_out_of_memory_points_at_the_page_file(self):
        findings = self._findings("2026-11-20 21:03  crash   app=AnomalyDX11.exe  module=KERNELBASE.dll  code=c0000017  source=WER")
        self.assertIn("page file", findings[0].suggestion)

    def test_no_crashes_is_good_news(self):
        findings = self._findings("Crashes and hangs Windows recorded", "", "none")
        self.assertEqual([f.severity for f in findings], [Severity.OK])


if __name__ == "__main__":
    unittest.main()
