# Optional PDF unlock utility

`unlock_pdfs.py` removes PDF encryption in place after a successful password check, preserving the source on failure. It is an optional local utility, independent of the dashboard server. Install uv and review the script's command-line help before use. Dependency versions are recorded in `unlock_pdfs.py.lock`.

`Unlock PDFs in Place v6.xml` is an optional Apple Shortcuts example. It resolves uv through PATH; configure the shortcut environment for the local installation. The embedded script handles each selected file and keeps errors visible. Test on disposable fictional PDFs before processing personal evidence. Passwords are requested interactively and should not be written in commands or logs.
