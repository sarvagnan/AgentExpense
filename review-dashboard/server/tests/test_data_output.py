"""Refresh and serving obey the same private provenance boundary as builders."""
from dataclasses import replace
import json
import os
from unittest.mock import patch
from support import Fixture
from transactions import refresh


class DataOutputTests(Fixture):
    def test_refresh_refusal_changes_no_output_and_reports_failure(self):
        settings=replace(self.settings,dashboard_data=self.base/'published-output')
        output=settings.dashboard_data
        stamp=output/'app/data/.source-ledger.json';stamp.parent.mkdir(parents=True)
        stamp.write_text(json.dumps(dict(format=1,ledger=str(self.base/'another-ledger'))))
        published=output/'public/data/dashboard.json.gz';published.parent.mkdir(parents=True);published.write_bytes(b'existing')
        before={p:p.read_bytes() for p in output.rglob('*') if p.is_file()}
        with patch('transactions.subprocess.run') as run:
            result=refresh(settings,{})
        run.assert_not_called();self.assertTrue(result['data_stale'])
        self.assertIn('DASHBOARD_DATA_REPLACE=1',result['refresh_error'])
        self.assertEqual({p:p.read_bytes() for p in output.rglob('*') if p.is_file()},before)
        with self.client(worker=False,dashboard_data=output) as client:
            self.assertEqual(client.get('/data/dashboard.json.gz').content,b'existing')
            self.assertEqual(client.get('/data/.source-ledger.json').status_code,404)

    def test_data_file_replaced_while_served_is_sent_whole(self):
        # A save's data rebuild can replace a published file between the size check and
        # the read. The response must be one whole version with a matching length.
        output=self.base/'served-output';published=output/'public/data/dashboard.json.gz'
        published.parent.mkdir(parents=True);published.write_bytes(b'previous version')
        import starlette.responses
        real_open=starlette.responses.anyio.open_file
        async def replace_then_open(path,*args,**kwargs):
            if str(path)==str(published):
                fresh=published.with_suffix('.tmp');fresh.write_bytes(b'rebuilt version, which is longer');os.replace(fresh,published)
            return await real_open(path,*args,**kwargs)
        # Serving reads one opened version; FileResponse's stat-then-open would not.
        with self.client(worker=False,dashboard_data=output) as client, patch.object(starlette.responses.anyio,'open_file',replace_then_open):
            response=client.get('/data/dashboard.json.gz')
        self.assertEqual(response.status_code,200)
        self.assertIn(response.content,(b'previous version',b'rebuilt version, which is longer'))
        self.assertEqual(int(response.headers['content-length']),len(response.content))
        self.assertEqual(response.headers['cache-control'],'no-store')

    def test_refresh_sets_explicit_paths_pinned_node_and_offline_npm_environment(self):
        with patch.dict(os.environ,{'DASHBOARD_DATA_DIR':str(self.base/'redirected')}):
            settings=replace(self.settings,dashboard_data=None)
        self.assertEqual(settings.dashboard_data,self.base/'redirected')
        with patch('transactions.subprocess.run') as run, patch('transactions.node_environment',return_value={**os.environ,'npm_config_update_notifier':'false'}):
            run.return_value.returncode=0
            self.assertFalse(refresh(settings,{})['data_stale'])
        env=run.call_args.kwargs['env']
        self.assertEqual(env['npm_config_update_notifier'],'false')
        self.assertEqual(env['DASHBOARD_DATA_DIR'],str(settings.dashboard_data))
        self.assertEqual(env['LEDGER_DIR'],str(self.ledger.resolve()))
        self.assertEqual(os.environ['npm_config_update_notifier'],'false')

    def test_refresh_override_replaces_the_other_source_and_its_published_data(self):
        settings=replace(self.settings,dashboard_data=self.base/'replace-output')
        stamp=settings.dashboard_data/'app/data/.source-ledger.json'
        stamp.parent.mkdir(parents=True)
        stamp.write_text(json.dumps(dict(format=1,ledger=str(self.base/'another'))))
        old=settings.dashboard_data/'public/data/old.json.gz';old.parent.mkdir(parents=True);old.write_bytes(b'old')
        with patch.dict(os.environ,{'DASHBOARD_DATA_REPLACE':'1'}):result=refresh(settings,{})
        self.assertFalse(result['data_stale'],result)
        self.assertEqual(json.loads(stamp.read_text()),dict(format=1,ledger=str(self.ledger.resolve())))
        self.assertFalse(old.exists())
        self.assertTrue((old.parent/'dashboard.json.gz').is_file())

    def test_commit_checks_selected_data_without_running_code_tests(self):
        import importlib.util
        script=self.application/'scripts/ledger/commit.py'
        spec=importlib.util.spec_from_file_location('selected_ledger_commit',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.APPLICATION_ROOT=self.application
        # If saves accidentally run the code suite this disposable commit fails.
        (self.application/'scripts/ledger/tests/run_tests.py').write_text('raise RuntimeError("code tests must not run on a save")\n')
        with patch.dict(os.environ,{'LEDGER_DIR':str(self.base/'wrong-ledger')}):
            committed=module.commit(self.ledger,'Fictional data-only save')
        self.assertEqual(committed,self.jj('log','-r','main','--no-graph','-T','commit_id'))
        self.assertTrue((self.ledger/'expense_ledger.ledger').is_file())
