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


def edit_baseline(settings, draft):
    """Immutable comparison/cancellation baseline, separate from owner autosaves."""
    info=draft['correction']
    if draft.get('ledger_fix'):
        if not draft.get('agent_proposal'):
            # Migrate a kept pre-separation proposal without losing its snapshot.
            old=draft.pop('cancel_snapshot',None)
            if not old and draft['ledger_fix'].get('state') not in ('ready','resolved'):
                from corrections import wire_entry
                reason='Ledger check: '+draft['ledger_fix']['title']
                old=dict(hand_form=info.get('original_form') or wire_entry(settings,info['before']),
                         correction=dict(sources=info['before'].get('evidence',[]),reason=reason if info.get('reason')==reason else ''),files=[])
            old=old or draft
            draft['agent_proposal']=dict(form=deepcopy(old.get('hand_form') or prefill(settings,draft)),
                sources=deepcopy((old.get('correction') or info).get('sources',[])),
                reason=(old.get('correction') or info).get('reason',''),files=deepcopy(old.get('files',[])))
        baseline=deepcopy(draft['agent_proposal'])
        baseline['form']=prefill(settings,{**draft,'hand_form':baseline['form']})
        return baseline
    from corrections import wire_entry
    if not info.get('original_form'): info['original_form']=wire_entry(settings,info['before'])
    return dict(form=deepcopy(info['original_form']),sources=deepcopy(info['before'].get('evidence',[])),reason='',files=[])


def retry_check(settings, draft):
    from rules_service import digest
    baseline=edit_baseline(settings,draft)
    current=dict(form=prefill(settings,draft),sources=draft['correction'].get('sources',[]),
                 reason=draft['correction'].get('reason',''),files=draft['files'])
    return dict(replace_edits=current!=baseline,version=digest(current))


def restore_baseline(settings, drafts, draft):
    baseline=edit_baseline(settings,draft)
    kept={f['name'] for f in baseline['files']}
    for file in draft['files']:
        if file['name'] not in kept:(drafts.folder(draft['id'])/'files'/file['name']).unlink(missing_ok=True)
    draft.update(hand_form=baseline['form'],files=baseline['files'])
    draft['correction'].update(sources=baseline['sources'],reason=baseline['reason'])
