# Security policy

ocsf-toolkit runs inside security telemetry pipelines and reads events from untrusted sources.

## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting:
<https://github.com/k4otix/ocsf-toolkit/security/advisories/new>.

Please do not open a public issue for a suspected vulnerability. Expect an acknowledgement within
seven days.

## Supported versions

Pre-1.0: only the latest release receives fixes.

## Scope notes

- The library reads compiled OCSF schemas and caller-supplied events; it does not execute either.
- `ocsf-toolkit compile` clones `ocsf/ocsf-schema` and runs `ocsf-schema-compiler` through `uvx`.
  The output is checked against `compiled_sha256` in `ocsf.lock` before it is written to
  `build/compiled/`.
