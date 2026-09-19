# Security policy

## Scope

shipzil is a library that runs inside your application and talks to shipping
providers with credentials you supply. It has no server, no hosted service and no
runtime package dependencies.

Relevant issues include:

- credential handling, for example a token reaching a log, an error message or a
  provider it was not intended for;
- a purchase being sent more than once, or to a source other than the one that
  quoted the rate;
- a customs or dangerous-goods declaration being altered, dropped or fabricated;
- a rate being returned or excluded in a way that misrepresents what a provider
  said.

Out of scope: vulnerabilities in the provider APIs themselves, and anything
requiring credentials you already control.

## Reporting

Report privately through GitHub Security Advisories:

<https://github.com/sameerkumar18/shipzil/security/advisories/new>

Please include the provider and operation, the shipzil version or commit, and a
minimal reproduction. Do not include live credentials, real customer addresses or
real label data; sanitize them as `tests/fixtures/README.md` describes.

Expect an acknowledgement within a week. Since there is no published release yet,
a fix normally lands as a commit on `main`.

## Supported versions

There is no published release. Only `main` receives fixes.

## Credential handling in this repository

- `.env` is ignored and must never be committed.
- Captured provider responses are sanitized before they reach `tests/fixtures/`.
- Live tests that could spend money check for a test credential first, and
  ShipStation v1 defaults to `testLabel: true`.
