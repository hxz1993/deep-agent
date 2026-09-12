"""Inclusive archive budgets and corrupt payload checks independent of install plans."""
import io
import unittest
import zipfile
from unittest.mock import patch

from deep_agent.agent_packages import archive
from tests.unit.test_agent_packages import package_files


class AgentPackageArchiveBoundaryTests(unittest.TestCase):
    def test_declared_limits_and_exact_compressed_boundary(self):
        self.assertEqual(archive.MAX_PACKAGE_BYTES, 50 * 1024 * 1024)
        self.assertEqual(archive.MAX_EXPANDED_BYTES, 200 * 1024 * 1024)
        self.assertEqual(archive.MAX_PACKAGE_FILES, 2048)
        raw = archive.build_zip(package_files(resources=True))
        # Inject an equivalent small budget to exercise equality/one-byte-over
        # without inflating every normal unit run by hundreds of MiB.
        with patch.object(archive, 'MAX_PACKAGE_BYTES', len(raw)):
            self.assertEqual(archive.parse_package(raw).agent.name, '安装包测试助手')
        with patch.object(archive, 'MAX_PACKAGE_BYTES', len(raw) - 1):
            with self.assertRaises(archive.PackageError):
                archive.parse_package(raw)

    def test_expanded_and_count_budgets_include_nested_skill_at_exact_boundary(self):
        files = package_files(resources=True)
        raw = archive.build_zip(files)
        with zipfile.ZipFile(io.BytesIO(files['skills/example-skill.zip'])) as nested:
            count = len(files) + len(nested.infolist())
            expanded = sum(map(len, files.values())) + sum(i.file_size for i in nested.infolist())
        with patch.object(archive, 'MAX_PACKAGE_FILES', count), patch.object(archive, 'MAX_EXPANDED_BYTES', expanded):
            self.assertEqual(set(archive.parse_package(raw).skills), {'example-skill'})
        for key, limit in (('MAX_PACKAGE_FILES', count - 1), ('MAX_EXPANDED_BYTES', expanded - 1)):
            with self.subTest(key=key), patch.object(archive, key, limit):
                with self.assertRaises(archive.PackageError):
                    archive.parse_package(raw)

    def test_crc_failure_in_otherwise_valid_archive_is_rejected(self):
        stream = io.BytesIO()
        files = package_files()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as zipped:
            for name, content in files.items():
                zipped.writestr(name, content)
        raw = bytearray(stream.getvalue())
        offset = raw.index(files['agent.json'])
        raw[offset] ^= 1  # Leave both headers and CRC intact; corrupt payload only.
        with self.assertRaises(archive.PackageError):
            archive.parse_package(bytes(raw))
