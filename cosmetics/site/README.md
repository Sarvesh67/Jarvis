# site/ — the narrated reading surface

An [Evidence](https://evidence.dev) project. Pages are Markdown with embedded SQL and
Svelte components: the source reads as prose, the build reads as a site.

## This is classic Evidence 40.x, not Evidence Studio

The scaffold that seeded this directory was the new Evidence Studio template — standalone
CLI, Markdoc components, no `plugins` block in the config. **That is not what runs here.**
The template's own README described that syntax and has been replaced by this file, because
following it would break the build.

What that means in practice:

- Components are Svelte (`<BigValue/>`, `<DataTable>`, `<BarChart/>`), not Markdoc tags.
- `evidence.config.yaml` needs a `plugins:` block naming the component and datasource
  plugins. Without it the CLI fails with `plugins: Required`.
- The CLI comes from `node_modules`, so use `npx evidence …` (or the `make` targets),
  never the standalone `evidence` binary.

Three version pins are load-bearing:

| Package | Pin | Why |
|---|---|---|
| `@evidence-dev/evidence` | `40.1.8` | the classic runtime |
| `typescript` | `5.4.2` | exact peer of the above; `^5.4.5` fails to resolve |
| `@evidence-dev/core-components` | `^5.2.0` | v4 pulls `@evidence-dev/tailwind@2`, which has no `./vite-plugin` export and breaks the vite build |

`.npmrc` deliberately does **not** set `loglevel=silent` — the template shipped it that
way and it swallowed the install errors above, printing nothing on a failed exit.

## Data

Two sources, both in `sources/`:

- **`catalog`** (csv) — written by `make catalog` (`pipeline/snapshot.py`). Neo4j is not an
  Evidence connector and the source cards are YAML, so graph counts and card metadata are
  flattened to CSV here. **Generated — never hand-edit.** Change a card or the graph.
- **`cosmetics`** (postgres) — the live `cosmetics` database, for ad-hoc queries.
  `connection.yaml` carries the platform password and is gitignored; recreate it from
  `platform/.env`. The `catalog` connection has no secret and *is* committed.

## Commands

```bash
make site        # refresh catalog CSVs, rebuild sources, build to site/build
make site-dev    # live reload on :3002
```

Or directly: `npx evidence sources` then `npx evidence build`.

## Writing a page

Figures come from the data; judgement comes from `catalog/`. A page that states a number
without saying what it may be used to claim is the failure mode this whole layer exists to
prevent — tier, coverage and the empty edges are the point, not a caveat.
