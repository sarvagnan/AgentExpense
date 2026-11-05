# On a tailnet or VPN

Reach the dashboard from your own phone and laptops without exposing it to the
internet.

## Tailscale

Tailscale Serve can forward a path on your
machine's tailnet name to the server on 127.0.0.1, and it adds the
`Tailscale-User-Login` header with the signed-in tailnet user, which AgentExpense
uses to allow changes.

Server settings (in a [systemd drop-in](systemd.md) or the [launchd file](launchd.md)):

```ini
Environment=EXPENSES_BASE_PATH=/expenses/
Environment=EXPENSES_ALLOWED_HOSTS=your-machine.your-tailnet.example
Environment=EXPENSES_ALLOWED_LOGINS=person@example.invalid
```

Then, on the same machine:

```sh
tailscale serve --bg --set-path /expenses http://127.0.0.1:8701
```

and open `https://your-machine.your-tailnet.example/expenses/` from a device on your
tailnet. Replace `your-machine.your-tailnet.example` with the machine's full tailnet
name, which `tailscale status` and the admin console show. Options differ between Tailscale versions; check `tailscale serve --help`.
Serve removes the `/expenses` prefix before forwarding; the server accepts the path
with or without it.

Notes:

- Use **Serve**, never Funnel: Funnel publishes the dashboard to the internet and
  sends no sign-in header.
- List only your own login names in `EXPENSES_ALLOWED_LOGINS`. Other tailnet members
  who can reach the machine can read the dashboard, so restrict them with tailnet
  access rules, or share the machine with no one.
- Leave `EXPENSES_DEV` unset.

## Other VPNs

A VPN that gives no sign-in header needs a proxy that signs people in: run the
[reverse proxy example](reverse-proxy.md) listening only on the VPN interface, with
`EXPENSES_ALLOWED_HOSTS` set to the name or address devices use on the VPN. Do not
set `EXPENSES_DEV=1` for a server reached over a VPN.
