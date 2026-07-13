# Repository audit and improvement backlog

Audited: 2026-07-13

This review covered the API, worker and persistence path, browser lifecycle,
audit modules, report/export code, scripts, tests, dependency graph, CI, and
container configuration. The repository currently has 132 documented rules,
439 collected tests (including 6 browser-backed tests), and a 12-fixture
precision/recall corpus.

## Changes completed in this pass

- Closed shared-cache isolation bugs by hashing the complete audit input,
  bypassing the cache for authenticated/personalized scans, rejecting stale
  cache pointers, and preventing multi-page results from polluting single-page
  entries.
- Added public-network URL validation at the API boundary and on every browser
  request/redirect, including login and sitemap fetches. Embedded credentials,
  private/reserved targets, unsafe headers, path traversal, oversized bodies,
  and malformed option structures are rejected.
- Hardened result handling: spreadsheet formula injection is neutralized,
  internal worker/broker errors are not exposed through job status, request IDs
  are bounded, API-key checks are constant-time, and rate-limit identifiers no
  longer reveal key prefixes.
- Updated and reconciled the dependency set. The previous lock produced 12
  known-vulnerability findings; the current runtime set produces none under
  `pip-audit`. Runtime-only and development-only dependencies are separated.
- Updated Playwright and axe-core. axe-core is fetched with pinned tarball and
  script checksums, and audits only execute the local verified copy.
- Hardened containers: current Playwright image, non-root `pwuser`,
  `no-new-privileges`, init handling, restricted Redis host binding, smaller
  build context, and production Redis health enforcement.
- Expanded CI to compile, lint, scan source, audit dependencies, check generated
  rule documentation, enforce a 50% coverage floor, and run the browser suite.
  Dependabot configuration now covers Python and GitHub Actions.
- Removed the report's automated `conformant` claim. A clean automated scan is
  now explicitly represented as “no detected blockers; conformance not
  determined; manual review required.”

## Remaining priorities

### P1 — production safety and correctness

1. **Bind every job to an identity.** API keys are currently a shared gate; any
   valid key that learns a UUID can read, export, or delete that job. Store a
   hashed tenant/key identifier with each job and authorize every job route.
2. **Add network-layer browser isolation.** DNS checks and request routing block
   normal SSRF and redirects, but application checks cannot fully prevent DNS
   rebinding or browser/network-stack bypasses. Run audit workers in a network
   namespace with private, link-local, metadata, and control-plane ranges denied
   at egress; use an allow-list when practical.
3. **Define multi-page NVDA semantics.** The automated path audits every URL,
   while the NVDA follow-up currently audits only the first URL. Either run and
   label Path B per page or explicitly disallow it for multi-page requests.
4. **Replace SQLite for multi-worker production.** Server and worker processes
   share one SQLite file. WAL improves local reliability but does not provide
   robust horizontal scaling, tenant isolation, retention, or online migration.
   Introduce a storage interface and a supported PostgreSQL implementation.
5. **Make timeouts terminate work.** `/audit/quick` returns after its wall-clock
   timeout, but cancelling a thread-pool await does not forcibly stop the
   underlying synchronous Playwright work. Put quick scans in a bounded worker
   process or route them through the task queue, with a concurrency semaphore.

### P2 — confidence, operations, and supply chain

1. **Raise meaningful coverage.** Fast-suite statement coverage is 53%. The main
   risk areas are browser lifecycle (24%), orchestrator (21%), screen-reader
   integration (23%), dynamic interactions (7%), widgets (13%), and the optional
   pixel/YAML paths (0%). Add behavior tests first, then raise the CI floor in
   small increments.
2. **Grow the benchmark corpus.** The current 12 fixtures cover only 13 of 132
   documented rules. Add paired positive/negative pages for every deterministic
   rule, noisy real-world component fixtures, cross-browser samples where
   relevant, and minimum precision/recall gates per rule family.
3. **Add browser-worker observability.** Export audit/module duration,
   timeouts, browser crashes, queue depth/age, cache hit rate, result size, and
   rule firing counts. Add a real Celery worker health probe and alerts for jobs
   stuck in queued/running/pending-NVDA states.
4. **Control result growth and retention.** Screenshots and transcripts can
   exceed the 16 MiB persistence cap after an expensive audit. Store large
   artifacts in object storage, retain references in the result, set explicit
   retention/deletion policy, and test cleanup.
5. **Produce reproducible release artifacts.** Add a hash-locked transitive
   dependency file, SBOM and container scan, image digest pinning/signing, and a
   documented upgrade cadence. Dependabot is useful notification, not a release
   verification policy.
6. **Review data-processing boundaries.** OpenRouter enrichment/VLM features can
   transmit page-derived content to a third party. Document exactly what leaves
   the system, require explicit per-request consent, redact secrets/PII, and log
   provider/model metadata without logging content.

### P3 — maintainability and product clarity

1. Split the large orchestrator, reveal, and screen-reader modules behind small
   typed interfaces; make module registration declarative and validate option
   consumption centrally.
2. Generate the OpenAPI examples and configuration tables from the Pydantic
   models/config schema so the README and API documentation cannot drift.
3. Add package metadata, semantic versioning, changelog/migration notes, and an
   explicit license. Remove or continuously reproduce time-sensitive “verified
   against real sites” numbers from the README.
4. Test the accessibility and print behavior of the generated HTML/VPAT/XLSX
   artifacts themselves, including high zoom, forced colors, and screen-reader
   table navigation.
5. Add deployment guides for TLS termination, trusted proxy handling, secret
   rotation, Redis authentication/TLS, backup/restore, log redaction, and safe
   worker egress.

## Research basis

- [WCAG 2.2 conformance requirements](https://www.w3.org/TR/WCAG22/#conformance-reqs)
- [OWASP SSRF guidance](https://owasp.org/www-community/attacks/Server_Side_Request_Forgery)
- [Playwright Docker security guidance](https://playwright.dev/python/docs/docker)
- [axe-core release and automation scope](https://www.npmjs.com/package/axe-core)
- [GitHub Dependabot supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories)
