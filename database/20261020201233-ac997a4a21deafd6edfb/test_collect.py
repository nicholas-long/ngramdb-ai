import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import collect


class CollectorTests(unittest.TestCase):
    def test_baseline_after_hourly_delta(self):
        assets = [{"assets": [{"name": "hourly.zip"}]}] * 100
        baseline = {"name": "2026-10-20_all_CVEs_at_midnight.zip"}
        with patch.object(collect, "request", side_effect=[
            io.BytesIO(json.dumps(assets).encode()),
            io.BytesIO(json.dumps([{"assets": [baseline]}]).encode()),
        ]):
            self.assertEqual(collect.latest_baseline(), baseline)

    def test_upstream_double_zip_baseline(self):
        baseline = {"name": "2026-10-18_all_CVEs_at_midnight.zip.zip"}
        releases = [{"assets": [
            {"name": "2026-10-19_delta_CVEs_at_0000Z.zip"},
            {"name": "2026-10-17_all_CVEs_at_midnight.zip"},
            baseline,
        ]}]
        with patch.object(collect, "request", return_value=io.BytesIO(json.dumps(releases).encode())):
            self.assertEqual(collect.latest_baseline(), baseline)

    def test_only_full_baselines_are_accepted(self):
        releases = [{"assets": [{"name": name} for name in [
            "2026-10-19_delta_CVEs_at_0000Z.zip",
            "2026-10-18_all_CVEs_at_midnight.zip.exe",
            "2026-10-18_all_CVEs_at_midnight.zip.zip.zip",
        ]]}, {"draft": True, "assets": [
            {"name": "2026-10-18_all_CVEs_at_midnight.zip.zip"}
        ]}]
        with patch.object(collect, "request", return_value=io.BytesIO(json.dumps(releases).encode())):
            with self.assertRaisesRegex(ValueError, "No official daily CVE baseline found"):
                collect.latest_baseline()

    def test_artifact_wrapper_contains_cves_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            direct, wrapped, staged, output = (root / name for name in (
                'direct.zip', 'baseline.zip.zip', 'staged', 'cves.jsonl.gz'))
            records = [
                {'dataType': 'CVE_RECORD', 'cveMetadata': {'cveId': identifier}}
                for identifier in ['CVE-2026-23456', 'CVE-2025-12345']
            ]
            with zipfile.ZipFile(direct, 'w', zipfile.ZIP_DEFLATED) as source:
                for record in records:
                    identifier = record['cveMetadata']['cveId']
                    source.writestr('cves/' + identifier + '.json', json.dumps(record))
            with zipfile.ZipFile(wrapped, 'w', zipfile.ZIP_DEFLATED) as wrapper:
                wrapper.write(direct, 'cves.zip')
            self.assertEqual(collect.write_records(direct, staged), 2)
            collect.publish(staged, output)
            previous = output.read_bytes()
            timestamp = output.stat().st_mtime_ns
            self.assertEqual(collect.write_records(wrapped, staged), 2)
            self.assertEqual(staged.read_bytes(), previous)
            collect.publish(staged, output)
            self.assertEqual(output.stat().st_mtime_ns, timestamp)
            self.assertEqual([json.loads(line)['cveMetadata']['cveId']
                              for line in gzip.decompress(previous).splitlines()],
                             ['CVE-2025-12345', 'CVE-2026-23456'])
            # A corrupt inner archive cannot replace the previous live output.
            with zipfile.ZipFile(wrapped, 'w') as wrapper:
                wrapper.writestr('cves.zip', b'not a zip')
            with self.assertRaises(zipfile.BadZipFile):
                collect.write_records(wrapped, staged)
            self.assertEqual(output.read_bytes(), previous)

    def test_main_publishes_gzip_and_retires_legacy_only_on_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "cves.jsonl"
            legacy.write_text("previous snapshot")
            record = {"dataType": "CVE_RECORD", "cveMetadata": {"cveId": "CVE-2026-12345"}}
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("CVE-2026-12345.json", json.dumps(record))
            with patch.object(collect, "__file__", str(root / "collect.py")), \
                    patch.object(collect, "latest_baseline", return_value={"browser_download_url": "unused"}):
                with patch.object(collect, "request", return_value=io.BytesIO(b"invalid zip")):
                    with self.assertRaises(zipfile.BadZipFile):
                        collect.main()
                self.assertEqual(legacy.read_text(), "previous snapshot")
                with patch.object(collect, "request", return_value=io.BytesIO(archive.getvalue())):
                    collect.main()
                output = root / "cves.jsonl.gz"
                self.assertEqual(json.loads(gzip.decompress(output.read_bytes())), record)
                self.assertFalse(legacy.exists())
                previous = output.read_bytes()
                with patch.object(collect, "request", return_value=io.BytesIO(b"invalid zip")):
                    with self.assertRaises(zipfile.BadZipFile):
                        collect.main()
                self.assertEqual(output.read_bytes(), previous)
                self.assertEqual(sorted(path.name for path in root.iterdir()), ["cves.jsonl.gz"])

    def test_wrapper_rejects_duplicate_or_empty_inner_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, staged = root / 'baseline.zip.zip', root / 'staged'
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, 'w') as source:
                source.writestr('README.md', 'no CVE records')
            with zipfile.ZipFile(archive, 'w') as outer:
                outer.writestr('cves.zip', inner.getvalue())
            with self.assertRaisesRegex(ValueError, 'no CVE records'):
                collect.write_records(archive, staged)
            with zipfile.ZipFile(archive, 'w') as outer:
                outer.writestr('one/cves.zip', inner.getvalue())
                outer.writestr('two/cves.zip', inner.getvalue())
            with self.assertRaisesRegex(ValueError, 'multiple cves.zip'):
                collect.write_records(archive, staged)

    def test_records_are_stable_and_invalid_input_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, staged, output = (root / name for name in ("input.zip", "staged", "cves.jsonl.gz"))
            record = {"dataType": "CVE_RECORD", "cveMetadata": {"cveId": "CVE-2026-12345"}}
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("cves/2026/CVE-2026-12345.json", json.dumps(record))
            self.assertEqual(collect.write_records(archive, staged), 1)
            collect.publish(staged, output)
            previous = output.read_bytes()
            timestamp = output.stat().st_mtime_ns
            collect.write_records(archive, root / "different-stage-name")
            collect.publish(root / "different-stage-name", output)
            self.assertEqual(output.stat().st_mtime_ns, timestamp)
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("CVE-2026-12345.json", '{}')
            with self.assertRaises(ValueError):
                collect.write_records(archive, staged)
            self.assertEqual(output.read_bytes(), previous)
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("README.md", "empty archive")
            with self.assertRaises(ValueError):
                collect.write_records(archive, staged)


if __name__ == "__main__":
    unittest.main()
