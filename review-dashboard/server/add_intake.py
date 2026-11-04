"""Read-only Add interpretation. Recognition never books or starts an agent."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
from pypdf import PdfReader, PdfWriter
from statement_readers import Registry
from store import load
from ledger_manifest import read_manifest
from settings import APPLICATION


def word_statement(text, metadata, locale='en'):
    vocabulary = json.loads((APPLICATION/'schema/recurring-locales.json').read_text())
    language = (locale or 'en').split('-')[0]
    months = vocabulary.get(language, vocabulary.get('en', {}))
    # The vocabulary is shared with recurring date recognition; include calendar
    # month names rather than treating any number as a statement period.
    names = months.get('months',[])
    low = text.casefold()
    has_month = bool(re.search(r'\b\d{4}-\d{2}\b',low)) or any(re.search(r'(?<!\w)'+re.escape(n.casefold())+r'(?!\w)',low) for n in names if len(n)>2)
    candidates = []
    for account, meta in metadata.items():
        if meta.get('kind') not in ('bank','card'): continue
        if any(re.search(r'(?<!\w)'+re.escape(n.casefold())+r'(?!\w)',low) for n in [meta.get('label',account.split(':')[-1]), *meta.get('aliases',[])] if n and n.casefold() not in ('bank','card')):
            candidates.append(account)
    statement = bool(re.search(r'\b(?:bank|card)\s+statement\b|\bstatement\s+(?:for|of)\b',low)) or has_month and bool(candidates)
    return dict(kind='statement' if statement else 'expense', account=candidates[0] if len(candidates)==1 else None)


def interpret(settings, drafts, draft, choices=None, passwords=None):
    choices, passwords = choices or {}, passwords or {}
    metadata = load(settings.ledger)[settings.ledger/'data/accounts.json']['accounts']
    words = word_statement(draft['text'],metadata,read_manifest(settings.ledger).get('locale','en'))
    registry = Registry(settings.ledger)
    results = []
    for file in draft['files']:
        choice = choices.get(file['id'], {})
        if not isinstance(choice,dict): raise ValueError('Invalid file choice')
        path = drafts.folder(draft['id'])/'files'/file['name']
        if path.is_symlink() or not path.is_file(): raise ValueError('Attachment is unavailable')
        suffix = path.suffix.lower()
        result = dict(id=file['id'],name=file['original_name'],kind='receipt',account=choice.get('account') or words['account'],locked=False,checks=[],can_choose_kind=suffix=='.pdf')
        with tempfile.TemporaryDirectory(dir='/tmp',prefix='expense-add-recognise-') as temporary:
            source = path
            if suffix == '.pdf':
                reader = PdfReader(path)
                if reader.is_encrypted and not reader.decrypt(''):
                    result['locked'] = True
                    password = passwords.get(file['id'],'')
                    if password:
                        if not isinstance(password,str) or len(password)>1000: raise ValueError('Enter the file password')
                        if not reader.decrypt(password):
                            result.update(error="That password didn't open the file. Try again.",password_error=True)
                        else:
                            result['locked'] = False
                            writer=PdfWriter()
                            for page in reader.pages: writer.add_page(page)
                            source=Path(temporary)/'unlocked.pdf'
                            with source.open('wb') as handle: writer.write(handle)
            hits = [(r,r.recognise(source)) for r in registry.readers.values() if suffix in r.extensions] if not result['locked'] else []
            hits = sorted([(r,recognition) for r,recognition in hits if recognition['confidence']>0],key=lambda x:x[1]['confidence'],reverse=True)
            # Generic extension recognition is usable only with an account's
            # existing column mapping. A broken configured mapping still stops.
            hits = [(r,h) for r,h in hits if r.id not in ('generic-csv','generic-sheet') or any(
                m.get('statement',{}).get('reader')==r.id and m.get('statement',{}).get('options',{}).get('columns')
                for a,m in metadata.items() if not result['account'] or a==result['account'])]
            hits = [(r,h) for r,h in hits if not r.personal or any(m.get('statement',{}).get('reader')==r.id for m in metadata.values())]
            recognised = hits[0][0] if hits else None
            kind = choice.get('kind')
            if kind not in (None,'statement','receipt'): raise ValueError('Choose Statement or Receipt')
            if kind=='receipt' and suffix in ('.csv','.xlsx','.xls'):
                raise ValueError('CSV and spreadsheet files are statements. Choose their statement account.')
            result['kind'] = kind or ('statement' if recognised or suffix in ('.csv','.xlsx','.xls') or result['locked'] or words['kind']=='statement' and suffix=='.pdf' else 'ambiguous' if suffix=='.pdf' else 'receipt')
            if result['kind']!='statement':
                results.append(result);continue
            if recognised:
                result.update(reader=recognised.id,reader_name=recognised.name,builtin=not recognised.personal)
                candidates = [a for a,m in metadata.items() if m.get('kind') in recognised.kinds and m.get('statement',{}).get('reader')==recognised.id]
                account = result['account']
                parsed = None
                # A single cheap recognition result can belong to several account
                # mappings. Printed identifiers/currency disambiguate a preview.
                parsed_candidates=[]
                for candidate in candidates:
                    meta = deepcopy(metadata[candidate])
                    if meta.get('kind') not in recognised.kinds: continue
                    if not meta.get('statement',{}).get('reader'): continue
                    try:
                        statement=recognised.read(source,meta)
                        parsed_candidates.append((candidate,statement))
                    except (ValueError,KeyError,TypeError,OSError): pass
                if len(parsed_candidates)>1:
                    identified=[pair for pair in parsed_candidates if pair[1].get('identifier',pair[1].get('card_last4'))==metadata[pair[0]].get('statement',{}).get('identifier') and pair[1].get('identifier',pair[1].get('card_last4'))]
                    if len(identified)==1: parsed_candidates=identified
                    elif account: parsed_candidates=[pair for pair in parsed_candidates if pair[0]==account]
                if len(parsed_candidates)==1:
                    source_account,parsed=parsed_candidates[0]
                    result['source_account']=source_account
                    account=account or source_account
                elif not account and len(candidates)==1: account=candidates[0]
                if account and account not in metadata: raise ValueError('Choose a declared statement account')
                result['account']=account
                if parsed:
                    result.update(currency=parsed.get('currency') or metadata[source_account]['currency'],start=parsed['periodStart'],end=parsed['periodEnd'])
                    result['checks'] = parsed.get('declaredChecks',[])
                else:
                    # PDF headers still expose period/currency when its account
                    # identifier or the row mapping prevents a complete preview.
                    if suffix=='.pdf' and recognised.id=='example-card-pdf':
                        from statement_pdf import extract_text, printed_currency, parse_text, card_identifier
                        text=extract_text(source)
                        result['currency']=printed_currency(text)
                        try:
                            parsed=parse_text(text)
                            result.update(start=parsed['periodStart'],end=parsed['periodEnd'])
                        except ValueError: pass
                        if not account:
                            try: identifier=card_identifier(text)
                            except ValueError: identifier=None
                            matching=[a for a,m in metadata.items() if identifier and m.get('statement',{}).get('identifier')==identifier]
                            if len(matching)==1: account=matching[0];result['account']=account
                    if account: result['source_account']=account
                    result['checks']=['printed_totals']
                result['candidates']=candidates
            else:
                result.update(builtin=False,checks=['source_values','printed_totals'])
            account=result['account']
            currency=result.get('currency')
            if account:
                if account not in metadata or metadata[account].get('kind') not in ('bank','card'): raise ValueError('Choose a bank or card account')
                result['account_label']=metadata[account].get('label') or account.split(':')[-1]
                result['account_currency']=metadata[account]['currency']
            staged=choice.get('new_account')
            if staged:
                if not isinstance(staged,dict) or staged.get('kind') not in ('bank','card') or not isinstance(staged.get('name'),str) or not staged['name'].strip() or ':' in staged['name'] or len(staged['name'])>100 or not currency:
                    raise ValueError('Enter a name and kind for the new account')
                result['new_account']=dict(kind=staged['kind'],name=staged['name'].strip(),currency=currency)
            result['mismatch']=bool(currency and account and currency!=metadata[account]['currency'] and not staged)
            results.append(result)
    return dict(words=words,files=results)
