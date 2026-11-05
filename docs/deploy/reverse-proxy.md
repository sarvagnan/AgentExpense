# Behind a reverse proxy with a base path

Serve the dashboard at `https://expenses.example.internal/expenses/` through a web
server that already runs on the same machine. The proxy terminates HTTPS, signs
people in, and passes each signed-in name to AgentExpense; the AgentExpense server
itself stays on 127.0.0.1.

Server settings (in a [systemd drop-in](systemd.md) or the [launchd file](launchd.md)):

```ini
Environment=EXPENSES_BASE_PATH=/expenses/
Environment=EXPENSES_ALLOWED_HOSTS=expenses.example.internal
Environment=EXPENSES_ALLOWED_LOGINS=person@example.invalid
```

Leave `EXPENSES_DEV` unset: changes then need a sign-in name from the list.

An nginx example (any proxy that can sign people in and set a request header works
the same way):

```nginx
server {
    listen 443 ssl;
    server_name expenses.example.internal;
    ssl_certificate     /path/to/certificate.pem;
    ssl_certificate_key /path/to/private-key.pem;

    location /expenses/ {
        auth_basic           "Expenses";
        auth_basic_user_file /path/to/expenses.htpasswd;   # user name person@example.invalid

        proxy_pass http://127.0.0.1:8701;                  # keeps the /expenses/ prefix
        proxy_set_header Host $host;
        # The signed-in name. This replaces any value the browser sent.
        proxy_set_header Tailscale-User-Login $remote_user;
        client_max_body_size 260m;                         # up to 10 files of 25 MiB
        proxy_read_timeout 120s;
    }
}
```

Notes:

- The header is named `Tailscale-User-Login` because the server first ran behind
  Tailscale; any proxy may set it. The proxy **must** overwrite it on every request,
  or anyone could claim a name. Never let requests reach the port without the proxy.
- The server accepts paths both with the prefix (`/expenses/api/…`, as above) and
  with it removed by the proxy (`/api/…`).
- Use HTTPS: the dashboard shows every amount and serves your evidence files, and
  basic authentication sends passwords with each request.
- `EXPENSES_ALLOWED_HOSTS` must list the name browsers use, or the server answers 400.
