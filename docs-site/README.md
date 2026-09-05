# shipzil documentation site

Fumadocs site for the Python package.

```bash
npm install
npm run dev
```

The site serves at `http://localhost:3000/shipzil/` by default.

## Build

```bash
npm run types:check
npm run build
npm run check:agent
```

`python:generate` uses `fumadocs-python` to generate the Python API pages from the
repository source. The generated JSON and MDX are ignored and recreated on every
build, so API docs cannot become a second hand-maintained copy of the package.
Fumadocs marks this Python generator experimental; the hand-written Reference
page remains the stable conceptual entry point.

The site emits agent-readable endpoints:

- `/shipzil/llms.txt`
- `/shipzil/llms-full.txt`
- `/shipzil/llms.mdx/docs/<page>/content.md`

The content source is `content/docs`. The human docs and the agent docs are the
same processed pages.
