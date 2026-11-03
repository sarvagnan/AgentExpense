"""Runtime selection is local and refuses unavailable fnm versions."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import node_toolchain


class NodeToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'.node-version').write_text('26.13.0\n')

    def test_installed_fnm_directory_and_notifier(self):
        with patch('node_toolchain.shutil.which',return_value='/tools/fnm'),patch('node_toolchain.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='/tools/project-node/bin\n')) as run:
            env=node_toolchain.node_environment(self.root,{'PATH':'/tools'})
        self.assertEqual(env['PATH'],'/tools/project-node/bin:/tools')
        self.assertEqual(env['npm_config_update_notifier'],'false')
        self.assertEqual(run.call_args.args[0][1:3],['exec','--using='+str(self.root/'.node-version')])
        self.assertNotIn('install',run.call_args.args[0])

    def test_missing_installed_version_fails_with_owner_instruction(self):
        with patch('node_toolchain.shutil.which',return_value='/tools/fnm'),patch('node_toolchain.subprocess.run',return_value=SimpleNamespace(returncode=1,stderr='missing')):
            with self.assertRaisesRegex(ValueError,r'.node-version.*fnm install 26.13.0'):
                node_toolchain.node_environment(self.root,{'PATH':'/tools'})

    def test_without_fnm_uses_path_and_warns_once(self):
        with patch('node_toolchain.shutil.which',side_effect=lambda cmd,**kw:'/tools/node' if cmd=='node' else None),patch('node_toolchain._warned',False),patch('builtins.print') as output:
            for _ in range(2):self.assertEqual(node_toolchain.node_environment(self.root,{'PATH':'/tools'})['PATH'],'/tools:/tools')
            self.assertEqual(output.call_count,1)
