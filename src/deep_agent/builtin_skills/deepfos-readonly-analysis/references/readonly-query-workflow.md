# Read-only query workflow

1. Identify the business object and requested comparison.
2. Inspect metadata only when identifiers or slice semantics are unknown.
3. Resolve dimensions and members, retaining exact IDs where available.
4. Query the narrowest sufficient slice.
5. Validate row counts, nulls, zeros, periods, units, and aggregation level.
6. Present the result together with the effective slice and unresolved ambiguity.
