# Weekly review

In **Settings › Weekly review**, turn on **Run a review every week** and choose a
weekday. The default is Monday. The server uses its own local calendar and starts
when the worker is idle. If it misses a week, it catches up on the latest due week.
Add and other work take priority between review steps. You can select **Review now**
in **Review › Weekly review** even with the schedule off. If a run stops, use its
**Retry** or **Retry summary** button to continue from saved progress.

Settings shows the reviewer’s adapter, model and effort. By default it uses the
same profile as Add. For a separate profile, set `reviewAgent` in the server’s
`local.json` and restart. For setup and sign-in, see the [adapter guide](../../docs/ADAPTERS.md#weekly-reviewer-profile).

## Needs you

Open a finding in **Queue › Needs you** to see the concern, its source and what
the reviewer is asking you to decide.

- **Apply suggestion** opens the normal entry or rule editor with the proposed
  changes. You can edit them first. The finding closes when you save a change.
- **Keep as is** closes the finding and lets you record a note.
- **Answer** sends text, a supporting file or both. The finding stays open for
  your decision and moves to **In progress** while the reviewer prepares a reply.
- **Explain more** asks the reviewer for more detail. Use it to request a fresh
  suggestion if the old one is out of date.

For a proposed rule removal, open **Rules** from the item and choose **Remove**
or **Keep**. A review verdict by itself makes no financial change.

## Reports

**Review › Weekly review** shows progress, the period checked, the reviewer and
past runs. A completed report groups what checked out, decisions still needed
and patterns the reviewer noticed. Use the numbered links to open the relevant
items. Questions from earlier weeks keep their original Queue items and history.
Share a pattern from the report with whoever maintains your app or bookkeeping
policy if you want to follow up on it.

## Hand changes

**Who made this change?** in **Queue › Needs you** asks about a terminal change
whose author was never recorded. Open the change and choose **You** or **An agent**
based on who made it. The dashboard records your answer in Change history and
includes the change in the next review. If history needs checking, the Queue item
shows the problem it found.
