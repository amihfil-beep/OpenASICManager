# Security Policy

## Supported versions

OpenASICManager is currently an early-stage open-source project.

Security fixes are provided for the latest public release.

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Older releases | No |

Users are encouraged to upgrade to the latest available release before
reporting an issue that may already have been fixed.

## Reporting a vulnerability

Please do not report security vulnerabilities in a public GitHub Issue.

Use GitHub's private vulnerability reporting feature instead:

1. Open the OpenASICManager repository on GitHub.
2. Go to the Security tab.
3. Select "Report a vulnerability".
4. Provide enough information to reproduce and understand the issue.

Useful information includes:

- affected OpenASICManager version;
- affected ASIC model and firmware, if relevant;
- configuration required to reproduce the issue;
- expected behavior;
- actual behavior;
- security impact;
- reproduction steps;
- logs or request examples with passwords, tokens and other secrets removed.

## Sensitive information

Never include real values for:

- ASIC passwords;
- Telegram bot tokens;
- Remote Web secrets;
- SSH private keys;
- TLS private keys;
- authentication cookies;
- production IP addresses or credentials.

Replace sensitive values with clearly marked placeholders.

## Security model

OpenASICManager is designed to run behind nginx and listen locally on:

    127.0.0.1:8088

The application should not normally be exposed directly to the Internet.

Remote ASIC Web is optional and disabled by default.

ASIC management networks should remain private and should not be directly
published to the Internet.

## Disclosure

Please allow reasonable time for investigation and remediation before
publicly disclosing a reported vulnerability.
