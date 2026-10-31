import io
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image

from support import Fixture, ready, SERVER
from agent import prepare, prompt_for, run


def pdf_bytes(pages=1):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Count %d /Kids [%s] >>' % (pages, ' '.join(f'{4+i*2} 0 R' for i in range(pages)))).encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for i in range(pages):
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /Font << /F1 3 0 R >> >> /Contents {5+i*2} 0 R >>'.encode())
        stream = f'BT /F1 10 Tf 10 50 Td (Receipt page {i+1}) Tj ET'.encode()
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'\nendstream')
    result, offsets = b'%PDF-1.4\n', [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(result)
    result += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    result += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return result + f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


class AgentInputTests(Fixture):
    def test_pdf_pages_text_limit_and_upload(self):
        source = self.base / 'receipt.pdf'
        source.write_bytes(pdf_bytes(14))
        work = self.base / 'work'
        work.mkdir()
        images, text = prepare(source, work, 1)
        self.assertEqual(len(images), 12)
        self.assertIn('Receipt page 12', text)
        self.assertNotIn('Receipt page 13', text)
        with Image.open(images[0]) as image:
            self.assertEqual(image.size, (200, 200))
        with self.client(worker=False) as client:
            identity = self.create(client, upload=False)
            response = client.post(f'/api/drafts/{identity}/files', files={'files': ('x.jpg', pdf_bytes(), 'image/jpeg')})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()['files'][0]['extension'], '.pdf')

    def test_runtime_policy_qa_prompt_exact_flags_and_stdin(self):
        with self.client(worker=False) as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = client.app.state.drafts.get(identity)
            draft['questions'] = [{'question': 'Which account?', 'answer': 'Cash'}]
            draft['check_errors'] = ['Fix the balance']
            folder = client.app.state.drafts.folder(identity)
            policy = self.ledger / 'AGENTS.md'
            policy.write_text(policy.read_text().replace('## Source of truth', 'Runtime policy marker\n\n## Source of truth'))
            declarations = json.loads((self.ledger/'data/accounts.json').read_text())
            declarations['accounts']['Assets:Cash'].update(label='Pocket money',aliases=['pocket'])
            (self.ledger/'data/accounts.json').write_text(json.dumps(declarations))
            answer, record = run(self.settings, draft, folder)
            self.assertEqual(answer['status'], 'ready')
            self.assertGreater(record['duration'], 0)
            calls = self.calls()
            self.assertEqual(len(calls), 1)
            call = calls[0]
            args = call['argv']
            self.assertIn('Pocket money',args[-1])
            self.assertIn('pocket',args[-1])
            self.assertIn('Payment accounts',args[-1])
            self.assertTrue(call['stdin_closed'])
            self.assertEqual(args[:10], ['exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
                                          '--disable', 'shell_tool', '--disable', 'unified_exec', '--json'])
            self.assertEqual(args[args.index('-m') + 1], 'gpt-6.1-terra')
            for option in ('model_reasoning_effort="xhigh"', 'mcp_servers.ledger.command="python3"',
                           'mcp_servers.ledger.startup_timeout_sec=20', 'mcp_servers.ledger.default_tools_approval_mode="approve"'):
                self.assertIn(option, args)
            mcp = json.loads(next(s.split('=', 1)[1] for s in args if s.startswith('mcp_servers.ledger.args=')))
            self.assertEqual(mcp[0], str(SERVER / 'lookup_server.py'))
            self.assertNotIn('--cutoff', mcp)
            self.assertNotIn('--exclude', mcp)
            self.assertEqual(mcp[mcp.index('--ledger') + 1], str(self.ledger))
            self.assertEqual(args[args.index('--output-schema') + 1], 'schema.json')
            self.assertEqual(args[args.index('-o') + 1], 'answer.json')
            self.assertIn('-i', args)
            self.assertEqual(args[-2], '--')
            prompt = args[-1]
            for text in ('Runtime policy marker', '## Before adding', '## Fuel entries', 'Which account?',
                         'Cash', 'Fix the balance', draft['client_zone'], 'category', 'lunch'):
                self.assertIn(text, prompt)
            self.assertNotIn('## Rebuild and verify', prompt)
            self.assertIn('not_expense', call['schema']['properties']['status']['enum'])
            self.assertIn('attachment_names', call['schema']['required'])
            self.assertFalse(Path(call['cwd']).exists())


class LookupTests(Fixture):
    def exchange(self, *extra):
        messages = [dict(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion': '2025-06-18'}),
                    dict(jsonrpc='2.0', method='notifications/initialized'),
                    dict(jsonrpc='2.0', id=2, method='tools/list'),
                    dict(jsonrpc='2.0', id=3, method='tools/call', params={'name': 'search_entries', 'arguments': {'query': 'Cafe'}}),
                    dict(jsonrpc='2.0', id=4, method='tools/call', params={'name': 'search_old_ledger', 'arguments': {'query': 'Cafe'}}),
                    dict(jsonrpc='2.0', id=5, method='tools/call', params={'name': 'entries_near', 'arguments': {'day': '2026-10-29', 'amount': '100'}}),
                    dict(jsonrpc='2.0', id=6, method='tools/call', params={'name': 'list_accounts', 'arguments': {}}),
                    dict(jsonrpc='2.0', id=7, method='tools/call', params={'name': 'no_such_tool'}),
                    dict(jsonrpc='2.0', id=8, method='ping')]
        process = subprocess.run([sys.executable, '-S', str(SERVER / 'lookup_server.py'), '--ledger', str(self.ledger),
                                  '--old-ledger', str(self.old), *extra],
                                 input='\n'.join(json.dumps(x) for x in messages) + '\n',
                                 text=True, capture_output=True, timeout=3)
        self.assertEqual(process.returncode, 0, process.stderr)
        return {r['id']: r['result'] for r in map(json.loads, process.stdout.splitlines())}

    def test_mcp_exchange_all_history_and_cutoff_exclude(self):
        from store import dump
        entries = [dict(id=f'{i:016x}', date=day, status='confirmed', description='Cafe ' + day,
                        postings=[dict(account='Expenses:Food', amount='100.00', commodity='INR'),
                                  dict(account='Assets:Cash', amount='-100.00', commodity='INR')])
                   for i, day in enumerate(('2026-10-28', '2026-10-29', '2026-10-30'), 1)]
        dump(self.ledger / 'data/expenses/2026.json', entries)
        result = self.exchange()
        self.assertEqual(result[1]['serverInfo']['name'], 'ledger-lookups')
        self.assertEqual(len(result[2]['tools']), 4)
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in result[2]['tools']))
        decoded = lambda value: json.loads(value['content'][0]['text'])
        self.assertEqual(len(decoded(result[3])), 3)
        self.assertIn('Cafe', decoded(result[4])[0])
        self.assertEqual(len(decoded(result[5])), 3)
        self.assertIn(dict(account='Assets:Cash',kind='cash',currency='INR'), decoded(result[6]))
        self.assertTrue(result[7]['isError'])
        result = self.exchange('--cutoff', '2026-10-29', '--exclude', '0000000000000002')
        self.assertEqual([e['date'] for e in decoded(result[3])], ['2026-10-28'])
