"""Claim-protected human proposals. The FIFO worker performs the checked write."""
from copy import deepcopy
from agent import now
from transactions import Transaction, BalanceRefusal, safe_name
from proposal_consent import proposal_hash


def prefill(settings, draft):
    proposal = draft.get('hand_form') or draft.get('account_proposal') or draft.get('last_proposal') or draft.get('proposal') or draft.get('currency_proposal')
    if proposal and proposal.get('entry'):
        from answers import to_form
        proposal = to_form(proposal, settings.ledger);proposal.update(status='ready',question=None)
        return proposal
    fields = (draft.get('pieces') or {}).get('fields', {})
    value = lambda key: fields.get(key, {}).get('value', '')
    currency = value('currency') or __import__('ledger_manifest').read_manifest(settings.ledger)['defaultCurrency']
    amount = value('amount') or ''
    from answers import to_form
    entry = {'date': value('date') or draft['client_time'][:10], 'status': 'confirmed', 'description': draft.get('description') or draft['text'],
             'references': None, 'classification': None, 'original-amount': None, 'notes': [],
             'postings': [dict(account=value('category'),amount=amount,commodity=currency,cost=None),dict(account=value('pay'),amount='-'+amount if amount else '',commodity=currency,cost=None)]}
    return to_form({'status': 'ready', 'question': None, 'entry': entry, 'new-accounts': [], 'doubts': [],
                    'attachment-names': [safe_name(f['original_name'].rsplit('.',1)[0]) for f in draft['files']], 'explanation': None}, settings.ledger)


def preview(settings, drafts, draft, form):
    # Persist invalid forms too; release and expiry must never lose typed work.
    from store import load
    form=deepcopy(form)
    existing=load(settings.ledger)[settings.ledger/'data/accounts.json']['accounts']
    if isinstance(form.get('entry'),dict) and isinstance(form['entry'].get('postings'),list) and all(isinstance(p,dict) and isinstance(p.get('account'),str) for p in form['entry']['postings']):
        form['new-accounts']=sorted({p.get('account','') for p in form['entry']['postings']} - set(existing) - {''})
    draft['hand_form'] = form
    draft.pop('account_consent', None)
    draft.pop('hand_checks', None)
    drafts.put(draft)
    try:
        prepared = Transaction(settings, drafts, draft).prepare(form, person=True)
        unchanged=draft.get('correction') and form==__import__('corrections').wire_entry(settings,draft['correction']['before']) and draft['correction'].get('sources',[])==draft['correction']['before']['evidence']
        result = dict(ok=not unchanged, unchanged=bool(unchanged), checks=['No changes yet'] if unchanged else ['The entry balances and its accounts and amounts are valid'], new_accounts=prepared['new_accounts'], proposal_hash=prepared['proposal_hash'])
    except BalanceRefusal as exc:
        result = dict(ok=False, checks=[str(exc)], refusal=exc.refusal, new_accounts=form.get('new-accounts',[]), proposal_hash=proposal_hash(form))
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        result = dict(ok=False, checks=[str(exc)], new_accounts=form.get('new-accounts',[]), proposal_hash=proposal_hash(form))
    draft['hand_checks'] = result
    drafts.put(draft)
    return result
