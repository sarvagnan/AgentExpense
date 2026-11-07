"""Canonical proposal identity shared by the writer and append-only audit checker."""
import hashlib
import json


def proposal_hash(proposal):
    return hashlib.sha256(json.dumps(proposal, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_consent(record):
    consent = record.get('account_consent')
    if consent:
        if consent['proposal_hash'] != proposal_hash(record.get('proposal')):
            raise ValueError('Account consent does not match the exact proposal')
        # Older audit lines carry the format 1 answer (new_accounts); newer ones the kebab-case answer.
        accounts = set(record['proposal'].get('new-accounts', record['proposal'].get('new_accounts', [])))
        if set(consent['accounts']) - accounts:
            raise ValueError('Account consent names accounts outside the proposal')

        if consent['method']=='hand-ticks' and set(consent['accounts']) != accounts:
            raise ValueError('Hand-finished consent must include every new account')
