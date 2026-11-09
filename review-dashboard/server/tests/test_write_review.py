"""Review regressions at the public preview and typing boundaries."""
from copy import deepcopy
from unittest.mock import patch
from support import Fixture, ready


class WriteReview(Fixture):
    def test_typing_hint_keeps_the_unsupported_format_guard(self):
        import json
        from ledger_manifest import CURRENT_FORMAT
        with self.client(worker=False) as client:
            path = self.ledger / 'ledger.json'
            original = path.read_bytes()
            try:
                manifest = json.loads(original)
                manifest['format'] = CURRENT_FORMAT + 1
                path.write_text(json.dumps(manifest))
                self.assertEqual(client.post('/api/add/interpret', json={'text':'bank statement October'}).status_code, 409)
            finally:
                path.write_bytes(original)

    def test_typing_hint_survives_empty_atomic_documents_during_rollback(self):
        with self.client(worker=False) as client:
            for relative in ('data/accounts.json', 'ledger.json'):
                path = self.ledger / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(b'')
                    response = client.post('/api/add/interpret', json={'text':'bank statement October'})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json(), {'kind':'expense', 'account':None})
                finally:
                    path.write_bytes(original)

    def test_typing_interpretation_neither_loads_the_store_nor_takes_a_writer_lock(self):
        with self.client(worker=False) as client:
            with patch('store.load', side_effect=ValueError('Temporary cross-document rule link')), patch('app.ledger_lock', side_effect=AssertionError('Typing must not acquire the exclusive lock')):
                response = client.post('/api/add/interpret', json={'text':'bank statement October'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['kind'], 'statement')

    def test_public_hand_preview_translates_store_cross_field_cost_refusals(self):
        with self.client(worker=False) as client:
            identity=self.create(client);self.save(client,identity);url='/api/drafts/'+identity
            token=client.post(url+'/claim',json={}).json()['claim_token']
            for amount, cost_currency, words in [('100.00','INR','different currency'),('0.00','USD','other than zero')]:
                with self.subTest(amount=amount):
                    form=deepcopy(ready());form['entry']['postings'][0].update(amount=amount,cost={'amount':'12.00','commodity':cost_currency})
                    response=client.post(url+'/hand-form',json={'claim_token':token,'proposal':form})
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertFalse(response.json()['ok'])
                    message=' '.join(response.json()['checks'])
                    self.assertIn(words,message)
                    for raw in ('validation error','Value error','input_value','StorePosting','type=value_error','https://errors.pydantic'):
                        self.assertNotIn(raw,message)

    def test_browser_reset_keeps_an_enumerated_trash_document_readable(self):
        import ast
        import shutil
        from pathlib import Path
        from types import SimpleNamespace
        from store import dump, read_json
        from ledger_lock import ledger_lock
        source=Path(__file__).parents[2]/'tests/browser/serve.py'
        function=next(node for node in ast.parse(source.read_text()).body if isinstance(node,ast.FunctionDef) and node.name=='reset_review')
        function.decorator_list=[]
        namespace=dict(settings=SimpleNamespace(ledger=self.ledger),original={},original_audit={},fixture_refresh=lambda value:value,shutil=shutil,ledger_lock=ledger_lock)
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
        trash=self.ledger/'data/trash.json';dump(trash,[])
        # A real list read can enumerate this path before the fixture reset.
        enumerated=trash
        with patch('transactions.jj',return_value=''):
            namespace['reset_review']()
        self.assertEqual(read_json(enumerated),[])
