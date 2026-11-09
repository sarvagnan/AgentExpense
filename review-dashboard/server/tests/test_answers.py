"""The agent's kebab-case answer: strict shape, asked extensions and the correction path."""
from copy import deepcopy
import re
import shutil

from support import Fixture, ready
from drafts import Drafts
from hand_finish import preview
from transactions import ProposalError, Transaction
from settings import APPLICATION
from answers import AnswerError, check
from store import dump, read_json

LEARNING = {'course': 'Fictional pottery class', 'hours': '2.50', 'level': 'beginner'}


class AnswerTests(Fixture):
    def use_learning(self, *, ask=True):
        """List jordan's fictional custom extension in the fixture ledger."""
        (self.ledger / 'extensions').mkdir(exist_ok=True)
        shutil.copy(APPLICATION / 'fixtures/ledgers/jordan/extensions/learning.json', self.ledger / 'extensions/learning.json')
        manifest = read_json(self.ledger / 'ledger.json')
        manifest['extensions']['learning'] = {'ask': ask}
        dump(self.ledger / 'ledger.json', manifest)
        self.jj('commit', '-m', 'A fictional custom extension')

    def answer(self):
        value = ready(new=False)
        value['entry']['extensions'] = {'fuel': None, 'learning': deepcopy(LEARNING)}
        return value

    def test_an_answer_with_an_asked_extension_saves_its_agent_fields(self):
        self.use_learning()
        self.set_answers(self.answer())
        with self.client() as client:
            identity = self.create(client, 'pottery class 100 cash')
            self.save(client, identity)
            self.wait(client, identity, 'added')
        stored = read_json(self.ledger / 'data/expenses/2026.json')[0]
        self.assertEqual(stored['extensions'], {'fuel': None, 'learning': {
            **LEARNING, 'sessions': None, 'certificate': None, 'next-session': None}})
        journal = (self.ledger / 'expense_ledger.ledger').read_text()
        for line in ('; learning-course: Fictional pottery class', '; learning-hours: 2.50', '; learning-level: beginner'):
            self.assertIn(line, journal)
        self.assertIn('entry.extensions.learning.course', self.calls()[0]['argv'][-1])

    def test_answer_shape_failures(self):
        self.use_learning()
        check(self.answer(), self.ledger, form=False)
        cases = {
            'a whole-number amount': lambda a: a['entry']['postings'][0].update(amount='100'),
            'a snake_case key': lambda a: a.update(new_accounts=a.pop('new-accounts')),
            'an all-null references group': lambda a: a['entry'].update(references={'reference': None, 'order-id': None}),
            'a server-only field': lambda a: a['entry'].update(evidence=[]),
            'a person field': lambda a: a['entry']['extensions']['learning'].update(sessions=2),
            'an unlisted extension': lambda a: a['entry']['extensions'].update(work=None),
            'a missing asked extension': lambda a: a['entry']['extensions'].pop('learning'),
            'an all-null extension group': lambda a: a['entry']['extensions'].update(learning=dict.fromkeys(LEARNING)),
            'a value outside a choice': lambda a: a['entry']['extensions']['learning'].update(level='expert'),
        }
        for name, change in cases.items():
            with self.subTest(name):
                answer = self.answer()
                change(answer)
                with self.assertRaises(AnswerError):
                    check(answer, self.ledger, form=False)

    def test_an_unasked_extension_is_not_in_the_answer(self):
        self.use_learning(ask=False)
        with self.assertRaisesRegex(AnswerError, 'learning'):
            check(self.answer(), self.ledger, form=False)
        answer = self.answer()
        answer['entry']['extensions'].pop('learning')
        check(answer, self.ledger, form=False)

    def test_a_malformed_answer_goes_back_to_the_agent(self):
        self.use_learning()
        bad = self.answer()
        bad['entry']['postings'][0]['amount'] = '100'
        self.set_answers(bad, self.answer())
        with self.client() as client:
            identity = self.create(client, 'pottery class 100 cash')
            self.save(client, identity)
            self.wait(client, identity, 'added')
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertIn('Server check failures to correct', calls[1]['argv'][-1])
        self.assertIn('postings.0.amount', calls[1]['argv'][-1])
        self.assertEqual(read_json(self.ledger / 'data/expenses/2026.json')[0]['postings'][0]['amount'], '100.00')

    def test_a_second_malformed_answer_fails_visibly_after_one_retry(self):
        self.use_learning()
        bad = self.answer()
        bad['entry']['postings'][0]['amount'] = '100'
        worse = self.answer()
        worse['entry']['extensions']['learning']['level'] = 'fictional-level'
        self.set_answers(bad, worse, self.answer())
        before = (self.ledger / 'data/expenses/2026.json').read_bytes()
        with self.client() as client:
            identity = self.create(client, 'pottery class 100 cash')
            self.save(client, identity)
            draft = self.wait(client, identity, 'failed')
        # One retry with the first errors, then the second failure is shown, never repaired or retried again.
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertIn('postings.0.amount', calls[1]['argv'][-1])
        self.assertIn('extensions.learning.level', draft['error'])
        self.assertEqual([run['outcome'] for run in draft['runs']], ['check_failed', 'check_failed'])
        self.assertEqual(len(draft['check_errors']), 2)
        self.assertEqual((self.ledger / 'data/expenses/2026.json').read_bytes(), before)


class AccountNameTests(Fixture):
    def test_new_accounts_with_spaces_hledger_misreads_are_refused(self):
        from transactions import AccountError, to_store
        for name, reason in (('Expenses:Fictional Cafe', 'U+00A0'), ('Expenses:Fictional Cafe', 'U+2009'),
                             ('Expenses:Fictional　Cafe', 'U+3000'), ('Expenses:Fictional  Cafe', 'two spaces')):
            with self.subTest(name=name):
                answer = ready(new=False)
                answer['attachment-names'] = []
                answer['new-accounts'] = [name]
                answer['entry']['postings'][0]['account'] = name
                with self.assertRaisesRegex(AccountError, re.escape(reason)):
                    to_store(answer, self.ledger, 0)


class PersonAmountTests(Fixture):
    def form(self, amount):
        from answers import to_form
        value = to_form(ready(new=False), self.ledger)
        value['attachment-names'] = []
        value['entry']['postings'][0]['amount'] = amount
        value['entry']['postings'][1]['amount'] = '-' + amount
        return value

    def test_a_hand_form_with_whole_amounts_passes_and_saves_decimals(self):
        # The composer prefills "Lunch 450 cash" as "450"; the person's form is normalised before the check.
        drafts = Drafts(self.ledger); draft = drafts.create('Lunch 450 cash')
        result = preview(self.settings, drafts, draft, self.form('450'))
        self.assertTrue(result['ok'], result['checks'])
        draft = drafts.get(draft['id'])
        self.assertEqual(draft['hand_form']['entry']['postings'][0]['amount'], '450', 'The retained form stays as typed')
        prepared = Transaction(self.settings, drafts, draft).prepare(draft['hand_form'], person=True)
        self.assertEqual([p['amount'] for p in prepared['entry']['postings']], ['450.00', '-450.00'])

    def test_an_agent_answer_is_checked_with_the_answer_model_even_with_a_hand_form_open(self):
        drafts = Drafts(self.ledger); draft = drafts.create('Lunch 100 cash')
        preview(self.settings, drafts, draft, self.form('100'))
        draft = drafts.get(draft['id'])
        self.assertIsNotNone(draft['hand_form'])
        agent = ready(new=False); agent['attachment-names'] = []
        agent['entry']['postings'][0]['amount'] = '100'; agent['entry']['postings'][1]['amount'] = '-100'
        with self.assertRaisesRegex(ProposalError, r'postings\.0\.amount'):
            Transaction(self.settings, drafts, draft).prepare(agent)
        # A person-only field in an agent answer is refused too, although the form allows it.
        agent = ready(new=False); agent['attachment-names'] = []; agent['account-metadata'] = {}
        with self.assertRaises(ValueError):
            Transaction(self.settings, drafts, draft).prepare(agent)

    def test_the_worker_checks_the_agents_answer_with_the_answer_model(self):
        # A draft whose hand form was opened but not finished goes back to the agent; its answer stays strict.
        bad = ready(new=False); bad['entry']['postings'][0]['amount'] = '100'; bad['entry']['postings'][1]['amount'] = '-100'
        self.set_answers(bad, ready(new=False))
        with self.client(worker=False) as client:
            identity = self.create(client, 'lunch 100 cash')
            drafts = client.app.state.drafts; draft = drafts.get(identity)
            draft['hand_form'] = self.form('100'); drafts.put(draft)
            self.save(client, identity)
        with self.client() as client:
            self.wait(client, identity, 'added')
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertIn('postings.0.amount', calls[1]['argv'][-1])


class PlainRefusalTests(Fixture):
    COST = 'A cost is always positive. For a refund, make the amount negative and keep the cost positive.'

    def bad_cost(self, amount):
        answer = ready(new=False)
        answer['entry']['postings'][0]['cost'] = dict(amount=amount, commodity='USD')
        return answer

    def test_hand_form_keeps_zero_and_negative_costs_and_explains_refunds(self):
        drafts = Drafts(self.ledger)
        for amount in ('0', '-1.00'):
            with self.subTest(amount=amount):
                draft = drafts.create('Fictional refund')
                form = self.bad_cost(amount)
                result = preview(self.settings, drafts, draft, form)
                self.assertFalse(result['ok'])
                self.assertEqual(result['checks'], [self.COST])
                self.assertEqual(drafts.get(draft['id'])['hand_form'], form)

    def test_fake_agents_retry_gets_plain_cost_refusal_and_never_commits_bad_cost(self):
        self.set_answers(self.bad_cost('0.00'), ready(new=False))
        with self.client() as client:
            identity = self.create(client, 'Fictional lunch')
            self.save(client, identity)
            self.wait(client, identity, 'added')
        prompt = self.calls()[1]['argv'][-1]
        self.assertIn(self.COST, prompt)
        self.assertNotIn('String should match pattern', prompt)
        self.assertEqual(len(self.calls()), 2)

    def test_reachable_form_refusals_do_not_expose_validator_text(self):
        for field, value in [('date', '2026-02-30'), ('description', 'Two\nlines')]:
            answer = ready(new=False); answer['entry'][field] = value
            with self.assertRaises(AnswerError) as failure:
                check(answer, self.ledger, form=True)
            self.assertNotRegex(str(failure.exception), r'String should|validation error|Input should|https://errors|\^\[')
        for field, value in [('amount', 'oops'), ('account', ':Bad'), ('commodity', 'Two\nlines')]:
            answer = ready(new=False); answer['entry']['postings'][0][field] = value
            with self.assertRaises(AnswerError) as failure:
                check(answer, self.ledger, form=True)
            self.assertNotRegex(str(failure.exception), r'String should|validation error|Input should|https://errors|\^\[')
