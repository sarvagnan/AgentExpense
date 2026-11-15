"""Live-pass transport regressions through the local fake; never contact Incus."""
import json
import os
from pathlib import Path
import unittest
from test_browser_box import box
if os.environ.get('BROWSER_BOX_SLOW_TESTS')!='1':
    raise unittest.SkipTest('slow browser-box process tests run with make test-browser-box')
import test_browser_box_review as review

DNS_ERRORS=('Error: lookup box.invalid: no such host',
            'Error: lookup box.invalid: temporary failure in name resolution',
            'Error: lookup box.invalid: server misbehaving',
            'Error: lookup box.invalid: i/o timeout')


class LivePassTests(unittest.TestCase):
    setUp=review.ReviewTests.setUp
    stop_controllers=review.ReviewTests.stop_controllers
    until=review.ReviewTests.until
    read=review.ReviewTests.read
    start_fake=review.ReviewTests.start_fake

    def completed(self,output):
        return self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))

    def test_midrun_dns_and_lost_operations_resume_the_same_running_guest(self):
        errors=[*DNS_ERRORS,'Error: Operation not found','Error: Operation not found',
                'Error: background operation temporarily busy','Error: background operation temporarily busy']
        for index,error in enumerate(errors):
            with self.subTest(error=error,valid_stdout=index==5):
                failures=3 if index==7 else 2
                config={'fail_after':{'status':1},'failures':{'status':failures},'failure_texts':{'status':error}}
                if index==5:config['failure_stdout']={'status':'{"state":"running","archive_ready":false}'}
                output,fake,_,_=self.start_fake('poll-'+str(index),config=config,timeout=60)
                self.until(lambda:(self.read(fake/'counts.json') or {}).get('status',0)>=failures+2 or
                            (self.read(output/'runner.json') or {}).get('status')=='finished')
                (fake/'release').touch()
                state=self.completed(output)
                rows=self.read(output/'summary.json')['cases'][0]
                self.assertEqual([r['outcome'] for r in rows],['passed'],rows)
                self.assertEqual(state['exit'],0)
                self.assertTrue(rows[0]['collected'])
                self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)

    def test_startup_dns_without_any_successful_response_fails_fast(self):
        for index,error in enumerate(DNS_ERRORS):
            with self.subTest(error=error):
                output,fake,_,_=self.start_fake('startup-'+str(index),
                    config={'failures':{'project show':2},'failure_texts':{'project show':error}},reconnect_timeout=1)
                self.assertEqual(self.completed(output)['exit'],1)
                self.assertEqual(self.read(fake/'counts.json')['project show'],1)
                self.assertNotIn('RECONNECT',(output/'runner.log').read_text())
                self.assertNotIn('guest_starts',self.read(fake/'counts.json'))

    def test_partial_pull_dns_and_operation_loss_restart_and_keep_traces(self):
        for index,error in enumerate([DNS_ERRORS[0],'Error: Operation not found']):
            with self.subTest(error=error):
                fake=self.root/('pull-'+str(index));fake.mkdir();(fake/'release').touch()
                output,fake,_,_=self.start_fake('pull-'+str(index),fake,
                    config={'failures':{'file pull':2},'failure_texts':{'file pull':error},'partial_failure':True})
                self.assertEqual(self.completed(output)['exit'],0)
                rows=self.read(output/'summary.json')['cases'][0]
                self.assertEqual(len(rows),1)
                self.assertTrue(rows[0]['collected'])
                self.assertTrue((fake/'prefix-written').exists())
                self.assertEqual((Path(rows[0]['folder'])/'results/artifacts/failure-partial.zip').read_bytes(),b'partial trace')
                self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)

    def test_collection_recovery_window_allows_a_normal_slow_pull(self):
        fake=self.root/'slow-recovery';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('slow-recovery',fake,timeout=20,
            config={'failures':{'file pull':1},'failure_texts':{'file pull':'Error: backend restarted during download'},
                    'slow_pull_seconds':32})
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None),timeout=60)
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        rows=self.read(output/'summary.json')['cases'][0]
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['collected'])
        self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)
        diagnostic=[json.loads(line.removeprefix('COLLECTION_ERROR ')) for line in
                    (output/'runner.log').read_text().splitlines() if line.startswith('COLLECTION_ERROR ')]
        self.assertIn('backend restarted during download',diagnostic[0]['error'])
        self.assertEqual(diagnostic[0]['phase'],'collection')
        self.assertGreater(diagnostic[0]['remainingSeconds'],30)

    def test_unknown_consecutive_errors_are_bounded_infrastructure_not_permanent(self):
        for name,config,expected in [
                ('unknown',{'permanent_errors':{'status':'Error: unexpected status transport reply'}},'unexpected status transport reply'),
                ('invalid-json',{'status_reply':'not-json'},'not-json')]:
            with self.subTest(error=name):
                output,fake,_,_=self.start_fake(name,config=config,timeout=60,infrastructure_retries=0)
                self.assertEqual(self.completed(output)['exit'],1)
                rows=self.read(output/'summary.json')['cases'][0]
                self.assertEqual(rows[0]['outcome'],'infrastructure')
                self.assertIsNot(rows[0].get('retryable'),False)
                self.assertGreaterEqual(self.read(fake/'counts.json')['status'],3)
                self.assertIn(expected,rows[0]['error'])

    def test_missing_instance_and_authorization_still_stop_polling_immediately(self):
        for index,config in enumerate([{'lose_on_poll':True},{'permanent_errors':{'status':'Error: not authorized'}}]):
            output,fake,_,_=self.start_fake('permanent-'+str(index),config=config,infrastructure_retries=0)
            self.assertEqual(self.completed(output)['exit'],1)
            self.assertEqual(self.read(fake/'counts.json')['status'],1)
            self.assertEqual(self.read(output/'summary.json')['cases'][0][0]['outcome'],'infrastructure')
