## What changes

<!-- The behaviour a caller can observe, not the diff. -->

## Evidence

<!-- For a provider change: link the provider's current schema or model page for
     every field involved. A prose example is not sufficient. -->

- [ ] Request fields verified against the provider's current schema
- [ ] Payload test proves the value reaches the outgoing request
- [ ] Parser test uses a sanitized provider response, or the constructed case is
      marked synthetic
- [ ] Regression test checked by restoring the bug and watching it fail

## Safety

- [ ] `retries=0` on anything that spends money
- [ ] No fabricated dimensions, contents, customs values or company names
- [ ] `Quote.excluded` explains anything shipzil removed
- [ ] Docs updated where behaviour changed

## Verification

```
make check
make check-compat
make test-live     # state which live tests ran, and against which provider
```
