# Reading surfaces

Three tools, because the data has three shapes and no single tool covers them. Start all
of them with `make viz-up`.

| Surface | Port | Reads | Use it for |
|---|---|---|---|
| **Metabase** | [:3001](http://127.0.0.1:3001) | Postgres `bi` schema | counts, coverage, pipeline health, anything with a GROUP BY |
| **NeoDash** | [:5005](http://127.0.0.1:5005) | Neo4j (Bolt) | dashboards over the graph — tables, charts, and actual node-link views |
| **Neo4j Browser** | [:7474](http://127.0.0.1:7474) | Neo4j (Bolt) | one-off Cypher, inspecting a single neighbourhood |
| **Evidence site** | `make site` | catalog CSVs | the narrated read — what the data is and what it may claim |

## Why Metabase cannot see Neo4j, and what to do instead

Metabase ships 17 database drivers and none of them is Neo4j. This is not a missing
setting: Metabase's driver API is JDBC plus a SQL compiler, and its whole model — tables,
columns, foreign keys, a query builder that emits SQL — has no shape that a property graph
fits into. Community Neo4j drivers existed years ago and are abandoned; none loads on
0.56. Do not spend an afternoon on this.

The graph is *derived from* Postgres anyway (`make rebuild` replays Stages 2-8 from it),
so the answer is not to teach Metabase about graphs. It is to project the parts worth
aggregating back into Postgres, which is what `sql/002_bi_views.sql` does, and to use
NeoDash for the parts that are genuinely graph-shaped — paths, neighbourhoods, "what is
two hops from here".

**Rough split:** if the question ends in a number or a bar chart, Metabase. If the answer
is a shape, NeoDash.

## The `bi` schema

`public` holds staging: `crosswalk` is append-only with supersede chains, `raw_company`
has one row per *(source, company)* rather than per company, and trust lives in a third
table. Pointed at `public`, Metabase produces plausible and wrong numbers — a
`COUNT(*) FROM raw_company` is 136,518 and the company count is 132,030.

`bi` is the resolved, flat, documented projection. Every view and column carries a
Postgres `COMMENT`, which Metabase syncs into its own data dictionary — so the meaning
travels with the data instead of living in someone's head.

```
bi.company              one row per resolved company, with trust and source provenance
bi.entity_source        which sources contributed to each entity (the fan-out)
bi.facility             sites + the licence held at each
bi.operates             company -> facility, the inferred join
bi.ingredient_supply    tier-3 "this company sells this ingredient" claims
bi.source_coverage      per source: raw rows, resolved entities, resolve %
bi.source_freshness     when each source was last landed
bi.trust_distribution   entities per trust rung
bi.review_queue_open    resolution decisions waiting on a human
```

Rebuild after any pipeline run: `make bi-views` (idempotent; `make viz-up` runs it).

### What is deliberately absent

`public.person`, GST filing history, and the raw `payload`/`evidence` JSON blobs are not
in `bi` and never should be. DPDP and hard rule 7.

This is enforced, not merely documented: Metabase connects as the `cosmetics_bi` role,
which has `USAGE` on `bi` only and no rights on `public`. Verified:

```
bi.company          -> 132,030
public.person       -> ERROR: permission denied for table person
public.raw_company  -> ERROR: permission denied for table raw_company
```

Connecting Metabase as `jarvis` instead would hand a BI tool read access to named
individuals with a recorded consent basis. Don't.

## Connecting Metabase to Postgres (one-time, ~30 seconds)

Metabase has no OSS API for provisioning a database without authenticating first, so this
step is yours. **Settings → Admin → Databases → Add database:**

| Field | Value |
|---|---|
| Database type | PostgreSQL |
| Display name | `Cosmetics` |
| Host | `jarvis-postgres` |
| Port | `5432` |
| Database name | `cosmetics` |
| Username | `cosmetics_bi` |
| Password | see below |
| Schemas | **Only these…** → `bi` |

Host is the *container* name, not `localhost` — Metabase reaches Postgres over the shared
`jarvis_default` network. `localhost` would resolve to the Metabase container itself.

Copy the password to your clipboard without printing it:

```bash
grep '^COSMETICS_BI_PASSWORD=' cosmetics/.env | cut -d= -f2 | tr -d '\n' | pbcopy
```

It was generated when the role was created and lives only in `cosmetics/.env`, which is
gitignored. To rotate it, change it there and re-run the `ALTER ROLE` in
`sql/002_bi_views.sql`'s header comment.

After it connects: **Admin → Table Metadata → Cosmetics** and hit *Re-scan*. The column
descriptions from the SQL comments land there, and they are what make the query builder
usable by someone who has not read this repo.

## Connecting NeoDash (one-time)

Open [:5005](http://127.0.0.1:5005) and connect with:

| Field | Value |
|---|---|
| Protocol | `neo4j` |
| Hostname | `localhost` |
| Port | `7687` |
| Database | `neo4j` |
| Username | `neo4j` |
| Password | `NEO4J_PASSWORD` (default `cosmetics`) |

Hostname is `localhost` here and `jarvis-postgres` in Metabase for the same reason in
reverse: NeoDash is a static React bundle, so the Bolt connection is opened by **your
browser**, not by the container. The browser is on the host.

Dashboards are saved into the graph itself as `_Neodash_Dashboard` nodes. They are data,
and `make rebuild` drops the graph — export anything you want to keep (`Save → to file`)
into `out/` before rebuilding.

> NeoDash publishes amd64 images only, so it runs under emulation on this arm64 Mac. That
> is fine here and would not be elsewhere in the stack: the container is nginx serving
> static files, and every Cypher query runs in the browser against Bolt. The emulated
> process is never on the query path.

## Known delta

`bi.company` reports **132,030**; the graph reports **131,662** `Company` nodes. The gap is
the pharma holdout — 484 companies whose only source is `s01_cdsco` are kept in Postgres
but held out of the answerable graph unless they stand in a loan relationship
(`catalog/sources/s01_cdsco.yaml`, `catalog/decisions/0001`) — netted against entities the
graph creates from sources other than `raw_company`.

The two numbers are each correct for their own store and should not be reconciled by
adjusting a view. If a dashboard shows both, label which store it came from.
