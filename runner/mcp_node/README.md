# Runner Playwright MCP runtime

Install this directory with `npm ci --ignore-scripts` during Runner provisioning.
The lock file fixes the complete dependency graph. The pinned runtime currently
requires Node.js 20 or newer and a locally available Chrome browser. The Python
Runner invokes the local `playwright-mcp` binary with `--isolated`, `--headless`,
an origin allow-list, and no unrestricted file access. The package version is
pinned intentionally; do not replace it with `latest` without compatibility and
security review.

The application-side MCP client rejects every tool outside its small allow-list.
In particular, arbitrary code execution, evaluation, file upload, storage,
network mocking, screenshots, and unrestricted tab management are unavailable to
the exploration agent.

The upstream `--allowed-origins` option is only a request guardrail and explicitly
is not a security boundary. The Runner therefore also validates every planned
navigation before execution, checks the observed origin after every action,
accepts only snapshot-reference-shaped targets, blocks submit-like/destructive
actions and sensitive fields, and limits automatic key presses. Production
deployments should still apply outbound network isolation around the Runner.
