"""Keep tracked root entries compatible with the existing Alpha artifact job."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReleasePackagingTest(unittest.TestCase):
    @unittest.skipUnless((ROOT / '.git').exists(), 'requires a Git checkout')
    def test_original_alpha_move_loop_preserves_tracked_root_entries(self):
        tracked = subprocess.check_output(
            ['git', 'ls-files', '-z'], cwd=ROOT,
        ).decode().split('\0')
        entries = sorted({path.split('/', 1)[0] for path in tracked if path})
        # The existing job intentionally enumerates non-hidden root entries only.
        entries = [name for name in entries if not name.startswith('.')]
        invalid = [name for name in entries if any(c.isspace() or c in '*?[' for c in name)]
        self.assertEqual(invalid, [], 'Archive design/report materials under docs, not the root')
        self.assertNotIn('deep-agent', entries)
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete-alpha-package-', dir=ROOT) as tmp:
            root = Path(tmp)
            for name in entries:
                entry = root / name
                entry.mkdir()
                (entry / '含空格的 内部材料.txt').write_text(name, encoding='utf-8')
            # Reproduce the unchanged Jenkins shell commands, including unquoted expansion.
            result = subprocess.run(
                ['/bin/sh', '-ec', '''
                lists=$(ls)
                mkdir -p deep-agent
                for item in $lists; do
                    mv $item deep-agent/
                done
                '''], cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.name for p in (root / 'deep-agent').iterdir()), entries)
            for name in entries:
                self.assertEqual(
                    (root / 'deep-agent' / name / '含空格的 内部材料.txt').read_text(encoding='utf-8'),
                    name,
                )
