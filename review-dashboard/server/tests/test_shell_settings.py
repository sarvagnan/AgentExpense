"""Settings writes and read-only shell state, using only disposable ledgers."""
from unittest.mock import patch
import subprocess
from support import Fixture
from store import read_json
from transactions import clean


class ShellSettingsTests(Fixture):
    def test_name_commits_and_preserves_accounts_under_existing_access_rules(self):
        before=(self.ledger/'data/accounts.json').read_bytes()
        with self.client(worker=False) as client:
            response=client.post('/api/settings/name',json={'name':'  River Ledger  '})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(read_json(self.ledger/'ledger.json')['name'],'River Ledger')
            self.assertTrue(clean(self.ledger))
            self.assertEqual((self.ledger/'data/accounts.json').read_bytes(),before)
            self.assertIn('Change ledger name',self.jj('log','-r','@-','--no-graph','-T','description'))
            for name in ('   ','Two\nlines'):
                self.assertEqual(client.post('/api/settings/name',json={'name':name}).status_code,409)
            self.assertEqual(client.post('/api/settings/name',json={'name':'Denied'},headers={'origin':'https://foreign.invalid'}).status_code,403)
            (self.ledger/'unfinished.txt').write_text('Unfinished')
            self.assertEqual(client.post('/api/settings/name',json={'name':'Denied'}).status_code,409)
        self.assertEqual(read_json(self.ledger/'ledger.json')['name'],'River Ledger')

    def test_name_verification_failure_rolls_back(self):
        from currency_settings import update_name
        before=self.bytes()
        with patch('commit.verify',side_effect=ValueError('injected failure')),self.assertRaisesRegex(RuntimeError,'failed verification'):
            update_name(self.settings,'River Ledger')
        self.assertEqual(self.bytes(),before)
        self.assertTrue(clean(self.ledger))

    def test_status_read_never_rechecks_or_runs_agent(self):
        with self.client(worker=False) as client:
            gate=client.app.state.agent_gate
            gate.close()
            for state,reason in [('ready',None),('signed out','Run: make agent-login'),('paused','Capability changed'),('unavailable','Check interrupted')]:
                gate.set_state(state,reason)
                with patch.object(gate,'require_ready',side_effect=AssertionError('Read started an agent check')):
                    for _ in range(2):
                        status=client.get('/api/agent-status').json()
                        self.assertEqual({k:status[k] for k in ('state','reason')},dict(state=state,reason=reason))
                        self.assertEqual(status['adapter']['id'],'codex')
                        self.assertEqual(status['login_command'],'make agent-login')
                        self.assertTrue(status['adapter']['isolation'])

    def test_account_details_share_currency_write_rules(self):
        with self.client(worker=False) as client:
            before=self.bytes()
            response=client.get('/api/accounts/details',params={'account':'Assets:Cash'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['currencyEditable'],True)
            self.assertEqual(response.json()['openingBalance'],'0')
            self.assertEqual(self.bytes(),before)
            self.assertEqual(client.get('/api/accounts/details',params={'account':'Expenses:Food'}).status_code,409)
