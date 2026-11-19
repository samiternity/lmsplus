# MCP Inspector verification

Verified on November 19, 2026: **10 checks passed**, using 14 Inspector CLI
invocations against the local synthetic LMS+ server over Streamable HTTP.

The client was `@modelcontextprotocol/inspector-cli@0.21.0` with its resolved
`@modelcontextprotocol/sdk@1.32.2`. This is the legacy Inspector compatibility
client, not Inspector v2. The server used the repository's Python implementation
and the frozen account in `evaluations/lmsplus_fixture.py`.

| Check | Result |
| --- | --- |
| Initialize and discover tools | Passed: four canonical/compatibility tools, output schemas and read-only annotations |
| Paginate assignment listing | Passed: three pages, six unique rows, correct totals and snapshot age; text JSON matches structured data |
| Markdown output | Passed: readable text, snapshot age, pagination instructions and structured data |
| Ambiguous live assignment selection | Passed: Networks returns two choices |
| Selected attachment retrieval | Passed: selected assignment metadata, document text and PNG image |
| Legacy tool names | Passed: both original names remain callable |
| Invalid inputs | Passed: zero page size and blank course return tool errors |
| Unknown course | Passed: actionable message and four course choices |
| Authorization boundary | Passed: Inspector connection denied without credentials; separate HTTP check confirms 401 and discovery challenge |
| Origin boundary | Passed: Inspector connection denied with an untrusted Origin; separate HTTP check confirms 403 |

All successful tool results validated against the advertised output schemas.
Inspector hides the status of empty HTTP error responses, so direct HTTP
requests confirmed those two status codes in addition to the Inspector failures.

The CLI-only package was installed in `/tmp/lmsplus-inspector-verification`
with dependency scripts disabled. Its working-directory lookup requires running
from the installed package's `build` directory. For example, while the fixture
is running:

```sh
cd /tmp/lmsplus-inspector-verification/node_modules/@modelcontextprotocol/inspector-cli/build
node index.js http://127.0.0.1:8765/mcp --transport http --header "Authorization: Bearer lmsplus-synthetic-evaluation-only" --method tools/list
node index.js http://127.0.0.1:8765/mcp --transport http --header "Authorization: Bearer lmsplus-synthetic-evaluation-only" --method tools/call --tool-name lmsplus_list_assignments --tool-arg status=both --tool-arg limit=2
```

Temporary raw results and the verifier summary are in
`/tmp/lmsplus-inspector-results/`. The fixture server was stopped after the checks.

This verifies the updated local MCP transport and tool contracts using synthetic
records. It does not verify production deployment, browser OAuth consent,
ChatGPT's hosted connection, live university access or LibreOffice rendering.
The separate OAuth and document regression tests cover their respective code
paths; a real ChatGPT connection still requires verification after deployment.
