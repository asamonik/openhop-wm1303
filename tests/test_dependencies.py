"""Pinned-source migration checks using only disposable local Git repositories."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = 'https://github.com/openhop-dev/openhop_core.git'
LEGACY = 'git@github.com:HansvanMeer/pyMC_core.git'


class DependencyCheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wm1303 migration ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.git('init', '-q', '-b', 'main', str(self.source))
        self.git('-C', str(self.source), 'config', 'user.name', 'Fixture')
        self.git('-C', str(self.source), 'config', 'user.email', 'fixture@example.invalid')
        (self.source / 'module.py').write_text('old source\n')
        self.git('-C', str(self.source), 'add', 'module.py')
        self.git('-C', str(self.source), 'commit', '-qm', 'old')
        self.git('clone', '-q', str(self.source), str(self.target))
        self.git('-C', str(self.target), 'config', 'remote.origin.url', LEGACY)
        self.old = self.git('-C', str(self.target), 'rev-parse', 'HEAD').strip()
        (self.source / 'module.py').write_text('new source\n')
        self.git('-C', str(self.source), 'commit', '-qam', 'new')
        self.revision = self.git('-C', str(self.source), 'rev-parse', 'HEAD').strip()
        self.git('-C', str(self.source), 'tag', 'verified-release')
        (self.target / 'module.py').write_text('local edits\n')
        (self.target / 'notes.txt').write_text('keep local notes\n')

    def git(self, *args):
        return subprocess.run(['git', *args], check=True, text=True,
                              capture_output=True).stdout

    def invoke(self, fetch_result='0', version_tag='verified-release'):
        script = r'''
source "$1"
PI_USER=fixture
sudo() { shift 2; "$@"; }
git() {
    local args=() value is_fetch=false
    for value in "$@"; do
        [ "$value" != fetch ] || is_fetch=true
        if [ "$value" = "$CANONICAL" ]; then value="$FIXTURE_SOURCE"; fi
        args+=("$value")
    done
    if $is_fetch && [ "$FETCH_RESULT" != 0 ]; then return "$FETCH_RESULT"; fi
    command git "${args[@]}"
}
checkout_dependency_revision "$2" "$CANONICAL" "$3" "$4"
'''
        return subprocess.run(['bash', '-eu', '-c', script, 'fixture',
                               str(ROOT / 'config/deploy_overlay.sh'), str(self.target),
                               self.revision, version_tag], text=True, capture_output=True,
                              env=dict(os.environ, CANONICAL=CANONICAL,
                                       FIXTURE_SOURCE=str(self.source), FETCH_RESULT=fetch_result))

    def assert_untouched(self):
        self.assertEqual(self.git('-C', str(self.target), 'rev-parse', 'HEAD').strip(), self.old)
        self.assertEqual((self.target / 'module.py').read_text(), 'local edits\n')
        self.assertEqual((self.target / 'notes.txt').read_text(), 'keep local notes\n')
        self.assertEqual(self.git('-C', str(self.target), 'remote', 'get-url', 'origin').strip(), LEGACY)

    def test_legacy_fork_migrates_to_exact_pin_without_rewriting_ssh_origin(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git('-C', str(self.target), 'rev-parse', 'HEAD').strip(), self.revision)
        self.assertEqual((self.target / 'module.py').read_text(), 'new source\n')
        self.assertEqual(self.git('-C', str(self.target), 'show', 'stash@{0}:module.py'), 'local edits\n')
        self.assertEqual(self.git('-C', str(self.target), 'show', 'stash@{0}^3:notes.txt'), 'keep local notes\n')
        self.assertEqual(self.git('-C', str(self.target), 'remote', 'get-url', 'origin').strip(), LEGACY)

    def test_failed_fetch_preserves_checkout_and_local_edits(self):
        self.assertNotEqual(self.invoke(fetch_result='8').returncode, 0)
        self.assert_untouched()

    def test_fresh_no_checkout_clone_can_be_prepared_at_the_pin(self):
        fresh = self.root / 'fresh'
        self.git('clone', '-q', '--no-checkout', str(self.source), str(fresh))
        self.target = fresh
        self.git('-C', str(fresh), 'config', 'remote.origin.url', CANONICAL)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((fresh / 'module.py').read_text(), 'new source\n')
        self.assertEqual(self.git('-C', str(fresh), 'rev-parse', 'HEAD').strip(), self.revision)

    def test_mismatched_release_tag_is_rejected_before_stashing(self):
        self.git('-C', str(self.source), 'tag', '-f', 'verified-release', self.old)
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assert_untouched()

    def test_unrecognized_origin_is_rejected_without_exposing_credentials(self):
        secret_url = 'https://secret-token@github.com/other/project.git'
        self.git('-C', str(self.target), 'config', 'remote.origin.url', secret_url)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('secret-token', result.stdout + result.stderr)
        self.assertEqual((self.target / 'module.py').read_text(), 'local edits\n')


if __name__ == '__main__':
    unittest.main()
