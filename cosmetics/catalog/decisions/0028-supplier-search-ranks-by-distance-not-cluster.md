# 0028 — Supplier search ranks by distance, not by cluster membership

**Status** accepted 2026-10-29 · **Affects** s15_ref, router `suppliers_near`, `Cluster`

## Context
Clusters were drawn around cosmetics manufacturing. Suppliers sit in their own belts:
Vapi, Ankleshwar, Bharuch, Dahej, Tarapur, Thane, Raigad and Silvassa. A buyer in Ahmedabad
asking for glycerin wants the nearest credible makers, and those can sit in another
district or another cluster.

## What the data actually says
Most of those belts fall inside existing clusters: `GJ_AHMEDABAD_VAPI` covers pincode
prefixes 380/382/392/393/394/396, and `MH_PALGHAR_TARAPUR` covers Thane and Raigad. But
"inside a cluster" says nothing about which supplier is closer. GeoNames IN.zip, already
landed by s15, carries latitude and longitude for every pincode, and s15 was discarding
them.

## Decision
1. s15 keeps each pincode's latitude and longitude.
2. Supplier questions rank by straight-line distance from the buyer's pincode or district
   to the firm's pincode, within a radius.
3. Clusters stay as a lens for "who is around here". They are not a supplier filter.
4. Road distance (self-hosted OSRM) waits until straight-line distance proves too crude.

## Consequence
Distance is computed at query time from two properties, like `IN_CLUSTER_WITH`
([0013](0013-in-cluster-with-is-not-materialised.md)). No pairwise edge is stored.
