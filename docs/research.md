# Why this section exists

Most libraries do not publish their research notes. shipzil does, and the reason is
narrow enough to state plainly.

## The problem these documents solve

A shipping wrapper is asked to be trusted with **money and legal declarations**. It
buys postage that cannot always be refunded, and it files customs values that a
destination authority will act on. "It seems to work" is not an adequate standard
for either.

Two documents exist to make the standard checkable:

- **[API reality](API-REALITY.md)** — what was measured against each provider,
  with the observed payloads and tracking numbers
- **[Gaps](GAPS.md)** — what each provider's specification documents that shipzil
  does not send, and what is still unverified

## The distinction they enforce

Every claim in shipzil's documentation falls into one of four buckets, and the
difference is kept visible:

| Bucket | Meaning | Example |
|---|---|---|
| **Measured** | a real request was sent and the response recorded | EasyPost DDP returns 14 rates where DDU returns 18 |
| **Read from a specification** | taken from the provider's own schema, never sent | ShipStation v1 customs items have no weight field |
| **Secondary research** | read somewhere that is not the provider | a carrier's published residential surcharge |
| **Unverified** | nobody checked | flagged as such, in the code and in the docs |

The fourth is the one that matters. A library that cannot tell you which of its
behaviours were tested is asking for trust it has not earned.

## Two worked examples of why this is not theatre

**The customs builders that were never called.** Customs support was written for
all five providers, and two of the builders were correct, complete, and called by
nothing. Unit tests passed, because they asserted what the builders *returned* — and
a correct builder whose output is discarded still returns the right thing. The
defect only surfaced when a test asserted on the bytes actually sent. There is now
a test that fails if any private helper in the package is never called.

**An accusation that was itself wrong.** A comment attributed a quotation to
EasyPost's documentation. During a verification pass that quotation could not be
sourced, and the notes were updated to call it *fabricated*. When the documentation
was finally read directly, the quotation turned out to be **verbatim correct**.

That is the same error twice: first asserting a claim as sourced without checking
it, then inferring from absence of evidence that it had been invented. A confident
negative is a claim too. Both the original overreach and the correction are left in
`API-REALITY.md`, because a research document that quietly edits out its own
mistakes is not evidence of anything.

## How to read them

They are written for someone deciding whether to depend on this library, or
debugging why a provider behaved unexpectedly. They are not a tutorial — start at
[Quickstart](quickstart.md) for that.

If you find a claim in either document that is wrong, that is a bug worth filing.
It is treated as one.
