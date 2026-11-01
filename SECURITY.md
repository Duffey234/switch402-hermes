# Security

Report a vulnerability to hello@switch402.com. Please do not open a public issue for it.

What the plugin guarantees, and what the tests check:

- No paid request is signed unless a human answered Hermes' approval prompt for that exact call.
- Nothing is ever signed above the per-request cap or above the price the user approved.
- Payments are off in cron and other unattended runs.
- The wallet key is read only through Hermes' secret scope, and it never appears in logs, errors,
  tool results or HTTP traffic.
