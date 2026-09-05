import Link from 'next/link';
import { highlight } from 'fumadocs-core/highlight';
import { CodeBlock, Pre } from 'fumadocs-ui/components/codeblock';
import { Mark } from '@/components/logo';

const SAMPLE = `import shipzil as z

gateway = z.Gateway(shipstation_v2="...", shippo="shippo_test_...")

quote = gateway.get_rates(shipment, carriers={"usps"})
if quote.errors:
    log.warning("degraded: %s", quote.errors)

rate = quote.cheapest
if rate is None:
    raise NoShippingOption(quote.explain())

label = gateway.buy(shipment, rate)`;

const PROVIDERS = [
  { name: 'Shippo', note: 'rating · purchase · refund' },
  { name: 'Easyship', note: 'rating · purchase · cancel' },
  { name: 'ShipStation v2', note: 'native multi-parcel rating' },
  { name: 'ShipStation v1', note: 'rating · base64 labels' },
];

const FEATURES = [
  {
    title: 'Keeps successful results',
    body: 'If one rating source fails, its error is returned in quote.errors. Rates from sources that answered are still available.',
  },
  {
    title: 'Runs in your process',
    body: 'shipzil calls providers with your credentials. There is no hosted shipzil service, proxy, account or per-label fee.',
  },
  {
    title: 'Explains local filtering',
    body: 'Rates removed by carrier or service filters are listed in quote.excluded. Provider-reported failures are retained when available.',
  },
  {
    title: 'Buys through the quoted source',
    body: 'Each rate records the account that produced it. gateway.buy() sends the purchase to that account and does not redirect it.',
  },
];

export default async function HomePage() {
  // Rendered through Fumadocs' own CodeBlock so the hero inherits the docs' dual
  // light/dark Shiki theming and copy button. Hand-rolling the frame produced
  // light-theme token colours on a dark surface.
  const code = await highlight(SAMPLE, {
    lang: 'python',
    components: {
      pre: (props) => (
        <CodeBlock {...props} title="rate.py">
          <Pre>{props.children}</Pre>
        </CodeBlock>
      ),
    },
  });

  return (
    <main className="flex flex-1 flex-col">
      {/* Hero */}
      <section className="mx-auto w-full min-w-0 max-w-5xl px-6 pt-20 pb-16">
        <div className="flex items-center gap-2 text-sm text-fd-muted-foreground">
          <Mark className="size-4" />
          <span className="font-medium uppercase tracking-widest">
            Fully open-source · MIT License
          </span>
        </div>

        <h1 className="mt-6 max-w-3xl text-4xl font-semibold tracking-tight sm:text-6xl">
          OpenRouter for{' '}
          <span className="brand-gradient-text">Shipping</span>.
        </h1>

        <p className="mt-6 max-w-2xl text-lg text-fd-muted-foreground">
          One Python interface across Shippo, ShipStation and Easyship. Use your own
          provider accounts, request rates from several sources and buy through the
          account that quoted the selected rate.
        </p>

        <p className="mt-4 max-w-2xl text-fd-muted-foreground">
          A fully open-source, MIT-licensed library that runs in your process. There
          is no hosted shipzil service, account, proxy or per-label fee.{' '}
          <strong className="font-medium text-fd-foreground">
            Free for commercial use.
          </strong>
        </p>

        <p className="mt-3 max-w-2xl text-xs text-fd-muted-foreground">
          “OpenRouter for Shipping” describes the product category. shipzil is not
          affiliated with OpenRouter.
        </p>

        <div className="mt-9 flex flex-wrap gap-3">
          <Link
            href="/docs/quickstart"
            className="rounded-lg bg-fd-primary px-4 py-2 font-medium text-fd-primary-foreground transition-opacity hover:opacity-90"
          >
            Quickstart
          </Link>
          <Link
            href="/docs/concepts"
            className="rounded-lg border px-4 py-2 font-medium transition-colors hover:bg-fd-accent"
          >
            Concepts
          </Link>
          <Link
            href="/docs/reference"
            className="rounded-lg border px-4 py-2 font-medium transition-colors hover:bg-fd-accent"
          >
            API reference
          </Link>
        </div>

        {/* min-w-0 keeps the code block scrolling inside its own box rather than
            stretching this flex column. */}
        <div className="mt-12 min-w-0 text-[13px] shadow-sm">{code}</div>

        <p className="mt-4 text-sm text-fd-muted-foreground">
          No runtime package dependencies. CPython 3.10–3.14.
        </p>
      </section>

      {/* Providers */}
      <section className="border-y bg-fd-card/40">
        <div className="mx-auto w-full max-w-5xl px-6 py-10">
          <h2 className="text-sm font-medium uppercase tracking-widest text-fd-muted-foreground">
            Supported provider surfaces
          </h2>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {PROVIDERS.map((p) => (
              <div key={p.name} className="rounded-lg border bg-fd-background px-4 py-3">
                <p className="font-medium">{p.name}</p>
                <p className="mt-0.5 font-mono text-xs text-fd-muted-foreground">
                  {p.note}
                </p>
              </div>
            ))}
          </div>
          <p className="mt-5 max-w-2xl text-sm text-fd-muted-foreground">
            ShipStation v2 uses native multi-parcel rating. The current Shippo,
            Easyship and ShipStation v1 adapters rate each parcel separately and sum
            matching services. Those FANOUT rates cannot be purchased as one label.
          </p>
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto w-full max-w-5xl px-6 py-16">
        <div className="grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <div key={f.title}>
              <h3 className="font-medium">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-fd-muted-foreground">
                {f.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Current boundaries */}
      <section className="border-t">
        <div className="mx-auto w-full max-w-5xl px-6 py-16">
          <h2 className="text-2xl font-semibold tracking-tight">
            Current boundaries
          </h2>
          <p className="mt-3 max-w-2xl text-fd-muted-foreground">
            shipzil translates requests and keeps source provenance. Your application
            still owns provider policy and service selection.
          </p>
          <ul className="mt-8 grid gap-4 sm:grid-cols-2">
            {[
              'No provider health scoring or automatic routing. fallback=(...) is a caller-defined order.',
              'Provider service keys stay provider-scoped; matching names do not establish equivalent service behavior.',
              'A purchase is not retried or redirected after a transport failure. Reconcile with the provider before another attempt.',
              'cheapest is unavailable when rates use mixed or unknown currencies.',
            ].map((item) => (
              <li key={item} className="flex gap-3 text-sm text-fd-muted-foreground">
                <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-fd-primary" />
                <span>{item}</span>
              </li>
            ))}
          </ul>

          <div className="mt-10 rounded-lg border border-amber-500/30 bg-amber-500/5 px-5 py-4">
            <p className="text-sm">
              <strong className="font-medium">Private pre-release.</strong>{' '}
              <span className="text-fd-muted-foreground">
                No public tag or package exists yet. The interface can still change.
                See the{' '}
                <Link href="/docs/roadmap" className="underline underline-offset-4">
                  roadmap
                </Link>{' '}
                for what lands next.
              </span>
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
