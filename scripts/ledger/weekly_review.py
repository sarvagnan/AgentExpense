"""Print the enabled weekly review procedure and outstanding outcome count."""
from store import ROOT
from audit import records, unreviewed
from ledger_manifest import read_manifest, module_settings, old_ledger_path
from project_paths import APPLICATION_ROOT


def summary(root=ROOT):
    if not module_settings(read_manifest(root))['weeklyReview']:
        return 'Weekly review is off. Enable modules.weeklyReview in ledger.json.'
    path = APPLICATION_ROOT / 'review-dashboard/server/WEEKLY-REVIEW.md'
    old = old_ledger_path(root)
    return f'Procedure: {path}\nUnreviewed: {len(unreviewed(records(root)))}\nOld ledger: {old or "not configured or unreadable"}'


if __name__ == '__main__':
    print(summary())
