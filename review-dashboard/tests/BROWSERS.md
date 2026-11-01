# Browser tests

`make browser-tests` runs the maintained fictional-fixture suites in
[`browser/`](browser/README.md). It requires the pinned Playwright 1.64.1 package
and headless engine binaries. It runs Chromium headless shell, Firefox and WebKit,
all four personas, Light and Dark, desktop and phone. Add and statement coverage
uses the production server with fake agents and real verified disposable-ledger
commits. No model or authentication is needed.

All profiles, downloads, artifacts, application copies and fixture repositories stay
under `/tmp`. Servers and browsers close in cleanup. The capture index is
`/tmp/agentexpense-4b-shots/index.html`; the manifest records actual routes.

Older standalone browser scripts are retained as historical synthetic helpers and
are not the maintained runner. Private history, evidence and revision replay suites
remain excluded. Node rendering checks use in-process Vite without listening.

The runtime gzip reader streams fetched bytes directly. It must not depend on a document-bound Blob URL: WebKit can retire that origin during navigation after statement Apply. The decoder regression covers both gzip and plain JSON when Blob streams are unavailable; browser console/page-error assertions remain unfiltered.
Full-page companions also anchor the offscreen skip link at the document origin, so a scrolled WebKit capture cannot composite it into the middle of the image. Viewport captures retain its ordinary keyboard behavior.
