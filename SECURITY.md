# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.10.x  | Yes       |
| < 0.10  | No        |

## Scope

tripwire is a **testing library** that runs in development and CI environments. Its attack surface is narrower than production-facing software. Security issues relevant to this project include:

- Dependency vulnerabilities in tripwire's direct dependencies
- Code execution through crafted test fixtures or plugin configurations
- Information disclosure through error messages or recorded interactions
- Supply chain integrity of published PyPI packages

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, use [GitHub's private security advisory feature](https://github.com/axiomantic/python-tripwire/security/advisories/new) to report the issue confidentially.

Include:

1. Description of the vulnerability
2. Steps to reproduce
3. Affected versions
4. Suggested fix (if any)

You should receive an acknowledgment within 48 hours. We will work with you to understand the issue and coordinate disclosure.
