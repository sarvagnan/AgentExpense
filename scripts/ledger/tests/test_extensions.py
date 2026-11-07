"""Ledger extensions in fictional ledgers: listing, the journal, verify and make extension-update."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from test_ledger import Fixture, entry, write
from store import dump, load, read_json
from generate_ledger import render
from extension_update import message, plan, update

WORK = {'name': 'work', 'description': 'Expenses for client work.', 'fields': {
    'client': {'type': 'text', 'filled-by': 'agent', 'description': 'The client.', 'journal-tag': 'work-client'},
    'billable': {'type': 'yes-no', 'filled-by': 'person', 'description': 'Billed to the client.', 'journal-tag': 'work-billable'},
}}


class ExtensionTests(Fixture):
    def define(self, definition=WORK, **listed):
        (self.root / 'extensions').mkdir(exist_ok=True)
        (self.root / 'extensions' / f"{definition['name']}.json").write_text(json.dumps(definition))
        if listed:
            dump(self.root / 'ledger.json', {**read_json(self.root / 'ledger.json'), 'extensions': listed})

    def rows(self):
        return read_json(self.year)

    def test_listing_an_extension_needs_its_groups_until_update_adds_them(self):
        self.define(work={'ask': True})
        with self.assertRaisesRegex(ValueError, r'1 entries have extension groups that differ from ledger.json; run make extension-update NAME=work'):
            load(self.root)
        result = update(self.root, 'work', commit=False)
        self.assertEqual((result['changed'], result['listed']), (1, True))
        self.assertEqual(self.rows()[0]['extensions'], {'work': None})
        load(self.root)
        self.assertEqual(update(self.root, 'work', commit=False)['changed'], 0)

    def test_values_print_under_their_tags_in_listing_then_field_order(self):
        self.define(work={'ask': True}, fuel={'ask': False})
        dump(self.year, [entry(extensions={'work': {'client': 'Fable Studio', 'billable': False},
                                           'fuel': {'litres': '8.00', 'trip-km': None}},
                               **{'original-amount': {'amount': '1.00', 'commodity': 'USD'}, 'reimbursement': {'group': 'shared', 'label': None}})])
        journal = render(load(self.root), self.root)
        block = journal[journal.index('; id: 0000000000000001'):]
        lines = [line.strip() for line in block.splitlines()[:6]]
        self.assertEqual(lines, ['; id: 0000000000000001', '; original amount: 1.00 USD', '; work-client: Fable Studio',
                                 '; work-billable: no', '; fuel-filled-litres: 8.00', '; reimbursement-group: shared'])

    def test_an_added_field_is_null_and_a_removed_field_with_values_is_refused(self):
        self.define(work={'ask': True})
        update(self.root, 'work', commit=False)
        dump(self.year, [entry(extensions={'work': {'client': 'Fable Studio', 'billable': None}}),
                         entry(identity='0000000000000002', extensions={'work': None})])
        grown = {**WORK, 'fields': {**WORK['fields'], 'hours': {'type': 'decimal', 'filled-by': 'agent', 'description': 'Hours.', 'journal-tag': 'work-hours'}}}
        self.define(grown)
        with self.assertRaises(ValueError):
            load(self.root)
        result = update(self.root, 'work', commit=False)
        self.assertEqual((result['changed'], sorted(result['added'])), (1, ['hours']))
        self.assertEqual(self.rows()[0]['extensions']['work'], {'client': 'Fable Studio', 'billable': None, 'hours': None})
        load(self.root)
        renamed = {**WORK, 'fields': {'customer': WORK['fields']['client'], 'billable': WORK['fields']['billable']}}
        self.define(renamed)
        before = self.year.read_bytes()
        with self.assertRaisesRegex(ValueError, r'Not changed: 1 entries hold values in client'):
            update(self.root, 'work', commit=False)
        self.assertEqual(self.year.read_bytes(), before)
        self.define(grown)
        dump(self.year, [entry(extensions={'work': {'client': None, 'billable': True, 'hours': None}}),
                         entry(identity='0000000000000002', extensions={'work': None})])
        self.define(renamed)
        result = update(self.root, 'work', commit=False)
        self.assertEqual(sorted(result['removed']), ['client', 'hours'])
        self.assertEqual(self.rows()[0]['extensions']['work'], {'customer': None, 'billable': True})
        self.assertIn('Fields removed: client, hours', message('work', result))

    def test_removing_a_listed_extension_with_values_fails_verify_with_a_count(self):
        self.define(work={'ask': True})
        update(self.root, 'work', commit=False)
        dump(self.year, [entry(extensions={'work': {'client': 'Fable Studio', 'billable': None}}),
                         entry(identity='0000000000000002', extensions={'work': None})])
        dump(self.root / 'ledger.json', {**read_json(self.root / 'ledger.json'), 'extensions': {}})
        with self.assertRaisesRegex(ValueError, r'1 entries hold values of the extension "work"'):
            load(self.root)
        with self.assertRaisesRegex(ValueError, r'1 entries hold values in \(the extension\)'):
            update(self.root, 'work', commit=False)
        write(self.year, [entry(extensions={'work': None}), entry(identity='0000000000000002', extensions={'work': None})])
        result = update(self.root, 'work', commit=False)
        self.assertEqual((result['changed'], result['listed']), (2, False))
        self.assertEqual([row['extensions'] for row in self.rows()], [{}, {}])
        load(self.root)

    def test_ask_false_keeps_values_valid_and_out_of_the_answer(self):
        from store import ledger_models
        self.define(work={'ask': False})
        update(self.root, 'work', commit=False)
        dump(self.year, [entry(extensions={'work': {'client': 'Fable Studio', 'billable': True}})])
        load(self.root)
        models = ledger_models(self.root)
        self.assertNotIn('extensions', models.answer.model_json_schema()['$defs']['AnswerEntry']['properties'])
        self.assertIn('work', models.form.model_json_schema()['$defs']['FormExtensions']['properties'])

    def test_update_commits_once_with_a_reviewable_message(self):
        config = self.root.parent / (self.root.name + '-jj.toml')
        config.write_text('[user]\nname="Offline extension test"\nemail="test@example.invalid"\n')
        self.addCleanup(config.unlink)
        environment = patch.dict(os.environ, {'JJ_CONFIG': str(config), 'LEDGER_DIR': str(self.root)})
        environment.start();self.addCleanup(environment.stop)
        (self.root / '.gitignore').write_text('/.lock\n/expense_ledger.ledger\n')
        subprocess.run(['jj', 'git', 'init', '--colocate', str(self.root)], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['jj', 'commit', '-m', 'Fictional baseline'], cwd=self.root, check=True, capture_output=True)
        self.define(work={'ask': True})
        subprocess.run(['jj', 'commit', '-m', 'List the work extension'], cwd=self.root, check=True, capture_output=True)
        with redirect_stdout(io.StringIO()):
            result = update(self.root, 'work')
        self.assertTrue(result['commit'])
        description = subprocess.run(['jj', 'log', '-r', '@-', '--no-graph', '-T', 'description'], cwd=self.root,
                                     check=True, capture_output=True, text=True).stdout
        for line in ('Update extension work via make extension-update', 'Listed: yes', 'Entries changed: 1',
                     'Added-by: user (extension update)'):
            self.assertIn(line, description)
        self.assertEqual(subprocess.run(['jj', 'diff', '--summary'], cwd=self.root, capture_output=True, text=True).stdout.strip(), '')

    def test_update_commits_ledger_json_and_the_definition_when_no_entry_changes(self):
        # Listing on a ledger with no entries, or changing only `ask`, changes files but no entry:
        # the update still makes its one verified commit, and with nothing changed it makes none.
        config = self.root.parent / (self.root.name + '-jj.toml')
        config.write_text('[user]\nname="Offline extension test"\nemail="test@example.invalid"\n')
        self.addCleanup(config.unlink)
        environment = patch.dict(os.environ, {'JJ_CONFIG': str(config), 'LEDGER_DIR': str(self.root)})
        environment.start();self.addCleanup(environment.stop)
        (self.root / '.gitignore').write_text('/.lock\n/expense_ledger.ledger\n')
        dump(self.year, [])
        jj = lambda *args: subprocess.run(['jj', *args], cwd=self.root, check=True, capture_output=True, text=True).stdout
        subprocess.run(['jj', 'git', 'init', '--colocate', str(self.root)], cwd=self.root, check=True, capture_output=True)
        jj('commit', '-m', 'Fictional empty baseline')
        self.define(work={'ask': True})
        with redirect_stdout(io.StringIO()):
            result = update(self.root, 'work')
        self.assertEqual(result['changed'], 0)
        self.assertTrue(result['commit'])
        self.assertIn('Entries changed: 0', jj('log', '-r', '@-', '--no-graph', '-T', 'description'))
        self.assertEqual(jj('diff', '--summary').strip(), '')
        self.assertIn('extensions/work.json', jj('diff', '--summary', '-r', '@-'))
        dump(self.root / 'ledger.json', {**read_json(self.root / 'ledger.json'), 'extensions': {'work': {'ask': False}}})
        with redirect_stdout(io.StringIO()):
            self.assertTrue(update(self.root, 'work')['commit'])
        self.assertEqual(jj('diff', '--summary').strip(), '')
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(update(self.root, 'work')['commit'])
