# Scheduled session maintenance

Lightweight scheduled job that opens an authenticated session against a
hosted control panel once every few days and verifies the account page
renders correctly. Notifications are sent on success/failure; a screenshot
of the verified page is kept as a run artifact.

## Why

Free-tier panels suspend accounts after inactivity. This keeps the session
warm without manual check-ins.

## Notes

- Credentials and egress configuration live in repository **secrets** only.
- `time.txt` is touched on every run so scheduled triggers stay enabled.
