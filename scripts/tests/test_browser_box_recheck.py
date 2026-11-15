"""Final re-check regressions: local fake client, files and public CLI controls."""
from contextlib import redirect_stderr,redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_browser_box_review as review
from test_browser_box import box
if os.environ.get('BROWSER_BOX_SLOW_TESTS') != '1':
    # Starts real controller, guest and client processes; make test-browser-box sets this.
    raise unittest.SkipTest('slow browser-box process tests run with make test-browser-box')

DISCONNECTS=(
    'Error: dial tcp 192.0.2.10:8443: i/o timeout',
    'Error: Lost connection to the event listener: read tcp 192.0.2.10:8443: i/o timeout',
    'Error: Unable to connect to: fictional.invalid:8443 (context cancelled)',
)


class RecheckTests(unittest.TestCase):
    setUp=review.ReviewTests.setUp
    stop_controllers=review.ReviewTests.stop_controllers
    until=review.ReviewTests.until
    read=review.ReviewTests.read
    start_fake=review.ReviewTests.start_fake

    def test_every_observed_disconnect_is_classified_as_transient(self):
        for detail in DISCONNECTS:
            with self.subTest(message=detail):self.assertTrue(box.transient_error(detail))
        self.assertTrue(box.transient_error('Error: Unable to connect to: fictional.invalid:8443 (context canceled)'))

    def test_permanent_diagnostics_override_transport_words(self):
        for detail in ['Error: not authorized (context cancelled)','Error: forbidden: i/o timeout',
                       'Error: client certificate not trusted','Error: certificate revoked',
                       'Error: Project does not exist','Error: HTTP 403']:
            with self.subTest(error=detail):
                self.assertTrue(box.permanent_error(detail))
                self.assertFalse(box.transient_error(detail))

    def test_each_observed_disconnect_recovers_during_archive_pull(self):
        for index,detail in enumerate(DISCONNECTS):
            with self.subTest(message=detail):
                fake=self.root/str(index);fake.mkdir();(fake/'release').touch()
                output,fake,_,_=self.start_fake('text-'+str(index),fake,
                        config={'failures':{'file pull':2},'failure_texts':{'file pull':detail}})
                state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
                self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
                rows=self.read(output/'summary.json')['cases'][0]
                self.assertEqual([r['outcome'] for r in rows],['passed'])
                self.assertTrue(rows[0]['collected'])
                self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)

    def test_collection_outage_beyond_window_recovers_traces_without_relaunch(self):
        fake=self.root/'long-pull';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('long-pull',fake,
                    config={'failures':{'file pull':3}},reconnect_timeout=1)
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None),timeout=25)
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        rows=self.read(output/'summary.json')['cases'][0]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['outcome'],'passed')
        self.assertTrue(rows[0]['collected'])
        self.assertTrue((Path(rows[0]['folder'])/'results/artifacts/failure-partial.zip').exists())
        self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)
        commands=[json.loads(line) for line in (fake/'commands.jsonl').read_text().splitlines()]
        pulls=[i for i,row in enumerate(commands) if row[:2]==['file','pull']]
        stops=[i for i,row in enumerate(commands) if row[0]=='stop']
        self.assertGreater(min(stops),max(pulls))

    def test_cleanup_transient_outage_uses_remaining_case_budget(self):
        fake=self.root/'long-cleanup';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('long-cleanup',fake,
                    config={'failures':{'stop':3}},reconnect_timeout=1)
        self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None),timeout=25)
        row=self.read(output/'summary.json')['cases'][0][0]
        self.assertTrue(row['cleaned'])
        self.assertNotIn('cleanupError',row)
        self.assertEqual(self.read(fake/'instances.json'),{})

    def test_permanent_poll_errors_fail_fast_and_keep_the_error(self):
        for name,config,expected in [('authorization',{'permanent_errors':{'status':'Error: not authorized'}},'not authorized'),
                                     ('missing-project',{'permanent_errors':{'status':'Error: Project not found'}},'Project not found')]:
            with self.subTest(error=name):
                output,fake,_,_=self.start_fake(name,config=config)
                self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None),timeout=15)
                rows=self.read(output/'summary.json')['cases'][0]
                self.assertEqual(len(rows),1)
                self.assertEqual((rows[0]['outcome'],rows[0]['exit']),('infrastructure',125))
                self.assertIn(expected,rows[0]['error'])
                self.assertFalse(rows[0]['retryable'])
                self.assertEqual(self.read(fake/'counts.json')['status'],1)

    def test_stop_on_crashed_controller_is_truthful_and_nonzero(self):
        output=self.root/'crashed';output.mkdir()
        (output/'launch.json').write_text('{}')
        (output/'runner.json').write_text(json.dumps({'status':'running','controller':{'pid':123}}))
        for _ in range(2):
            text=io.StringIO()
            with patch.object(box,'owner_status',return_value='dead'),patch.object(box.os,'kill') as kill, \
                 redirect_stdout(text),redirect_stderr(text):
                result=box.main(['--stop',str(output)])
            self.assertEqual(result,125)
            self.assertIn('dead',text.getvalue().lower())
            self.assertNotIn('Stop was already requested',text.getvalue())
            kill.assert_not_called()

    def test_new_client_registry_is_private(self):
        incus=box.Incus('configured','agentexpense-ci');incus.client_registry=self.root/'new-registry'
        child=SimpleNamespace(communicate=lambda timeout:('[]',''),returncode=0,poll=lambda:0)
        with patch.object(box.subprocess,'Popen',return_value=child):incus.call('list','configured:','--format=json')
        self.assertEqual(stat.S_IMODE(incus.client_registry.stat().st_mode),0o700)

    def test_registry_reaper_rejects_unsafe_or_foreign_owned_directories(self):
        registry=self.root/'unsafe-registry';registry.mkdir(mode=0o700)
        registry.chmod(0o777)
        with patch.object(box.client_watch,'end_process') as end:
            with self.assertRaises(PermissionError):box.client_watch.reap(registry)
        end.assert_not_called()
        registry.chmod(0o700)
        with patch.object(box.client_watch.os,'getuid',return_value=os.getuid()+1), \
             patch.object(box.client_watch,'end_process') as end:
            with self.assertRaises(PermissionError):box.client_watch.reap(registry)
        end.assert_not_called()
        link=self.root/'registry-link';link.symlink_to(registry,target_is_directory=True)
        with self.assertRaises(PermissionError):box.client_watch.reap(link)

    def test_registry_rejects_writable_ancestors_and_untrusted_records(self):
        parent=self.root/'open-parent';parent.mkdir(mode=0o700);parent.chmod(0o777)
        registry=parent/'registry';registry.mkdir(mode=0o700)
        with self.assertRaises(PermissionError):box.client_watch.reap(registry)
        parent.chmod(0o700)
        record=registry/'planted.json'
        record.write_text(json.dumps({'kind':'browser-box-client','parent':{},
                'client':{'pid':123,'birth':'fixture'},'watcher':{'pid':124,'birth':'fixture'}}))
        record.chmod(0o666)
        with patch.object(box.client_watch,'owner_status',return_value='dead'), \
             patch.object(box.client_watch,'end_process') as end:
            self.assertEqual(box.client_watch.reap(registry),[])
        end.assert_not_called();self.assertTrue(record.exists())
