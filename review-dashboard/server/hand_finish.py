"""Claim-protected human proposals. The FIFO worker performs the checked write."""
from copy import deepcopy
from agent import now, schema_for
from transactions import Transaction
from proposal_consent import proposal_hash


def prefill(settings, draft):
    proposal = draft.get('hand_form') or draft.get('account_proposal') or draft.get('last_proposal') or draft.get('proposal') or draft.get('currency_proposal')
    if proposal and proposal.get('entry'):
        proposal = deepcopy(proposal);proposal.update(status='ready',question=None)
        return proposal
    fields = (draft.get('pieces') or {}).get('fields', {})
    value = lambda key: fields.get(key, {}).get('value', '')
    currency = value('currency') or __import__('ledger_manifest').read_manifest(settings.ledger)['defaultCurrency']
    amount = value('amount') or ''
    entry = dict(date=value('date') or draft['client_time'][:10], status='confirmed', description=draft.get('description') or draft['text'],
                 order_id=None, reference=None, classification=None, original_amount=None, notes=[],
                 postings=[dict(account=value('category'),amount=amount,commodity=currency,cost=None),dict(account=value('pay'),amount='-'+amount if amount else '',commodity=currency,cost=None)])
    if 'fuel_filled_litres' in schema_for(settings)['properties']['entry']['properties']:
        entry.update(fuel_filled_litres=None, trip_km=None)
    return dict(status='ready',question=None,explanation=None,entry=entry,new_accounts=[],doubts=[],attachment_names=[f['original_name'].rsplit('.',1)[0] for f in draft['files']])


def preview(settings, drafts, draft, form):
    # Persist invalid forms too; release and expiry must never lose typed work.
    from store import load
    form=deepcopy(form)
    existing=load(settings.ledger)[settings.ledger/'data/accounts.json']['accounts']
    if isinstance(form.get('entry'),dict) and isinstance(form['entry'].get('postings'),list) and all(isinstance(p,dict) and isinstance(p.get('account'),str) for p in form['entry']['postings']):
        form['new_accounts']=sorted({p.get('account','') for p in form['entry']['postings']} - set(existing) - {''})
    draft['hand_form'] = form
    draft.pop('account_consent', None)
    draft.pop('hand_checks', None)
    drafts.put(draft)
    try:
        prepared = Transaction(settings, drafts, draft).prepare(form)
        result = dict(ok=True, checks=['The entry balances and its accounts and amounts are valid'], new_accounts=prepared['new_accounts'], proposal_hash=prepared['proposal_hash'])
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        result = dict(ok=False, checks=[str(exc)], new_accounts=form.get('new_accounts',[]), proposal_hash=proposal_hash(form))
    draft['hand_checks'] = result
    drafts.put(draft)
    return result
