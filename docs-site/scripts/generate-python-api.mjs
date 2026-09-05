import { mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import * as Python from 'fumadocs-python';

const snapshot = 'generated/shipzil.json';
const output = 'content/docs/api';

// `fumapy-generate` exits 0 even when it writes nothing — passing a path instead of
// a module name made it a silent no-op, and the published API reference quietly
// served a week-old snapshot. Refuse to build from a snapshot older than the Python
// source rather than trusting the exit code.
async function newestPythonMtime(dir) {
  let newest = 0;
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory()) newest = Math.max(newest, await newestPythonMtime(path));
    if (entry.isFile() && entry.name.endsWith('.py')) {
      newest = Math.max(newest, (await stat(path)).mtimeMs);
    }
  }
  return newest;
}

const [snapshotStat, sourceMtime] = await Promise.all([
  stat(snapshot).catch(() => null),
  newestPythonMtime('../shipzil'),
]);

if (!snapshotStat) {
  throw new Error(
    `${snapshot} is missing. Run "npm run python:generate" from docs-site/.`,
  );
}

if (snapshotStat.mtimeMs < sourceMtime) {
  throw new Error(
    `${snapshot} is older than at least one Python source file. ` +
      'fumapy-generate silently produced nothing: it takes a module name ' +
      '("shipzil"), not a path.',
  );
}

const generated = JSON.parse(await readFile(snapshot, 'utf8'));

// Drop underscore-prefixed modules. `shipzil._client` is deliberately internal —
// `Gateway` is the only entry point — so publishing its API page would advertise
// the surface the library just took away. `modules` is an object keyed by name.
function dropPrivateModules(node) {
  if (!node || typeof node !== 'object') return;
  const modules = node.modules;
  if (modules && typeof modules === 'object') {
    for (const name of Object.keys(modules)) {
      if (name.startsWith('_') && !name.startsWith('__')) {
        delete modules[name];
      } else {
        dropPrivateModules(modules[name]);
      }
    }
  }
}

dropPrivateModules(generated);

// Publish the caller API and the adapter-author extension points. Internal helper
// modules are covered by source and tests, not user documentation.
const PUBLIC_MODULES = new Set([
  'errors',
  'gateway',
  'http',
  'models',
  'providers',
  'services',
  'units',
]);
for (const name of Object.keys(generated.modules ?? {})) {
  if (!PUBLIC_MODULES.has(name)) delete generated.modules[name];
}

const CLASS_ALLOWLIST = {
  models: new Set([
    'Address', 'AddressClass', 'DangerousGoods', 'DryIce', 'DutiesPaidBy',
    'Exclusion', 'ExclusionCode', 'Item', 'Label', 'LithiumBatteryPacking',
    'PackagingTemplate', 'Parcel', 'Rate', 'RegulationLevel', 'Shipment',
    'Strategy', 'TrackingLeg',
  ]),
  services: new Set(['ServiceKey', 'ServiceMap']),
  units: new Set(['Weight', 'Dimensions']),
  http: new Set(['HttpRequest', 'HttpResponse', 'Transport', 'UrllibTransport']),
};

for (const [moduleName, allowed] of Object.entries(CLASS_ALLOWLIST)) {
  const classes = generated.modules?.[moduleName]?.classes ?? {};
  for (const name of Object.keys(classes)) {
    if (!allowed.has(name)) delete classes[name];
  }
  // These modules expose classes only in the generated caller reference.
  generated.modules[moduleName].functions = {};
}

if (Object.keys(generated.modules ?? {}).some((n) => n.startsWith('_') && !n.startsWith('__'))) {
  throw new Error('private modules survived filtering');
}

// The generator publishes every member it can see and inlines each one's source.
// Unfiltered, `Gateway` listed eleven private methods against four public ones, so
// the API reference documented the implementation instead of the interface. Reference
// pages describe what a caller can call; the source lives in the repository.
let removedMembers = 0;
let removedSource = 0;

const isPrivate = (name) => name.startsWith('_') && name !== '__init__';

function prune(node) {
  if (!node || typeof node !== 'object') return;

  for (const bag of ['functions', 'classes', 'attributes']) {
    const members = node[bag];
    if (!members) continue;

    if (Array.isArray(members)) {
      // Attributes arrive as an array. Drop private ones, and drop any that carry
      // no type and no docstring: those render as the assignment expression from
      // __init__ ("max_spend = max_spend"), which tells a reader nothing.
      const before = members.length;
      const enumLike = node.parameters?.length === 0 && members.some(
        (m) => typeof m?.value === 'string' && /^["']/.test(m.value),
      );
      node[bag] = members.filter((m) => {
        if (isPrivate(String(m?.name ?? ''))) return false;
        return enumLike || m?.annotation || m?.description;
      });
      removedMembers += before - node[bag].length;

      // A dataclass field's "value" is its real default and worth showing. On a
      // plain class the generator reports the __init__ assignment expression
      // instead, which renders as noise like `max_spend = max_spend` or
      // `tuple(fallback) if fallback is not None else None`. Keep constants, drop
      // expressions.
      const CONSTANT = new RegExp(
        [
          '^None$', '^True$', '^False$', '^-?\\d+(\\.\\d+)?$',
          "^'.*'$", '^".*"$',
          '^\\(\\)$', '^\\[\\]$', '^\\{\\}$',
          '^\\w+\\(\\)$',
          '^[A-Z][\\w.]*\\.[A-Z_]+$',
        ].join('|'),
      );
      for (const m of node[bag]) {
        if (typeof m.value === 'string' && m.value && !CONSTANT.test(m.value.trim())) {
          m.value = null;
        }
      }
      continue;
    }

    for (const name of Object.keys(members)) {
      if (isPrivate(name)) {
        delete members[name];
        removedMembers += 1;
      } else {
        prune(members[name]);
      }
    }
  }

  // Blanked rather than deleted: the renderer reads `source.length`, so removing
  // the key crashes it. An empty string emits no source block.
  if (typeof node.source === 'string' && node.source.length > 0) {
    node.source = '';
    removedSource += 1;
  }

  for (const child of Object.values(node.modules ?? {})) prune(child);
}

prune(generated);
console.log(
  `api docs: dropped ${removedMembers} private/untyped member(s), ` +
    `${removedSource} inlined source block(s)`,
);

// Generated pages are derived from the checked-out Python source. Removing the
// previous tree prevents deleted modules from surviving in the published API.
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await Python.write(Python.convert(generated, { baseUrl: '/docs/api' }), output);

// The output tree is wiped on every build, so the nav metadata has to be written
// here rather than committed. Collapsed by default: 62 generated pages should not
// push the hand-written guides out of view.
await writeFile(
  `${output}/meta.json`,
  `${JSON.stringify(
    {
      title: 'API reference',
      description: 'Generated from the Python source.',
      defaultOpen: false,
    },
    null,
    2,
  )}\n`,
);
