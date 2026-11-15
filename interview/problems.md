# Problems and decisions

Real problems hit while building Data Fog Agent, and how they were solved.
Each entry: what broke, the options, what was chosen and why.

---

## 1. Row-level security that silently does nothing (Phase 0)

**Problem:** The spec asks for Postgres row-level security (RLS) on every table, so one company can never see another company's data. The Docker Postgres image gives you a `postgres` superuser, and superusers *always* skip RLS. If the app connected as that user, every policy would be in place and none would be enforced. Nothing would error. It would just leak.

**Options:**
- Connect as the superuser and filter by `org_id` in app code. One forgotten `WHERE` leaks data.
- Give the app a separate, non-superuser role that owns the tables, and use `FORCE ROW LEVEL SECURITY` so even the owner is checked.

**Chosen:** A separate `app` role, plus FORCE. The superuser is only used once, to create the role, the databases and the `vector` extension. A test checks that one org can't read or insert another org's rows.

**Why it matters:** Security features that fail open are worse than missing ones, because everyone believes they work.

---

## 2. Passing "which org is this?" to the database safely (Phase 0)

**Problem:** The RLS policy needs to know the current user's org. The usual way is `SET app.org_id = ...`. But a plain `SET` sticks to the connection, and connections are pooled, so the next request on that connection would see the previous user's org.

**Options:**
- `SET` at the start of each request and `RESET` at the end. If a request crashes before the reset, the org leaks.
- `set_config(..., is_local => true)`, which only lasts until the transaction ends.

**Chosen:** Transaction-local settings, applied in a SQLAlchemy `after_begin` hook. The hook runs at the start of every transaction, so the setting also survives a `commit()` mid-request.

**Gotcha found on the way:** Once a custom setting has been used on a connection, Postgres returns `''` for it later instead of NULL. `''::int` throws an error, so the policy uses `NULLIF(current_setting('app.org_id', true), '')::int`. A request with no org then sees zero rows instead of crashing. There is a test for this.

---

## 3. Login that doesn't reveal which emails exist (Phase 0)

**Problem:** Checking an argon2 password takes about 50 ms on purpose. If an unknown email returns instantly, an attacker can time responses to find out which emails are registered.

**Chosen:** When the email isn't found, still verify the password against a dummy hash. Both cases take the same time and return the same error message. It costs two lines.

---

## 4. Docker wouldn't start: a file owner, not a Docker bug (Phase 0)

**Problem:** Docker Desktop failed with a generic "unable to start". The real error was deep in `com.docker.backend.exe.log`: WSL got `E_ACCESSDENIED` attaching `docker_data.vhdx`. Docker's data folder had been moved to `D:` behind a junction.

**Finding it:** There were two disk images in the same folder with the same ACL. One mounted and one didn't. The only difference was the **owner**: the working one was owned by my user, the broken one by `Administrators` (copied while elevated). To attach a disk, Hyper-V has to *edit* the file's ACL to grant the VM access. "Modify" rights don't include changing permissions, but owners always can.

**Options:**
- Take ownership with `takeown`/`icacls`. Needs an admin shell, which I didn't have.
- Reinstall or reset Docker. Loses images and volumes, and doesn't explain anything.
- Rename the file to `.bak` and copy it back. A copy is owned by whoever creates it.

**Chosen:** The copy. It's 1.6 GB, takes seconds, keeps all the data, and the original stays as a backup. Docker started on the next try.

**Why it matters:** Comparing a working case with a broken one beats guessing. The error message said "access denied" but the useful question was "denied *which* access, and why only here?"

---

## 5. Demo data that can be checked, not just looked at (Phase 1)

**Problem:** Every later phase (merging, PII, staleness, evals) needs a known right answer. Random demo data doesn't give you one.

**Chosen:** The generator uses a fixed seed and *computes* the expected results from the same data it writes: merge row counts, the conflicting keys, net revenue for March, every PII column. These go into `ground_truth.yaml`, so later phases are tested against numbers instead of "looks right".

**Two things that came up:**
- **Postgres has no "last modified" time for a table** that I could backdate, so a table can't be made to *look* stale from its metadata. Instead the staleness is in the data: `sales_2023` only has 2023 months, and the old `orders` table stops at Dec 2025. The connector will fall back to the newest date column. Files are easier because their mtimes are set directly.
- **A test caught a wrong lookup table in the Aadhaar checksum (Verhoeff).** The textbook 3-digit example passed, but real 12-digit numbers failed, because short inputs never hit the wrong entries. The fix was one line. The lesson: test checksums with full-length inputs, not just the one example in the docs. Otherwise the PII scanner in Phase 4 would have rejected every "valid" Aadhaar in the demo.

---

## 6. A "sync now" button that said yes and did nothing (Phase 2)

**Problem:** To stop two syncs of the same source running at once, each sync job got a fixed id (`sync-source-<id>`). arq won't queue a job whose id is already queued. But the second click on "Sync now" returned `{"queued": true}` and nothing ran. Found by reading the worker log after a live test, not by unit tests (which used a fake queue).

**Cause:** arq also refuses a job id while that job's **result** is still stored, which is one hour by default. So after one sync, the source couldn't be synced again for an hour, and the API didn't notice because it ignored `enqueue_job`'s return value.

**Options:**
- Random job ids. Fixes the silent drop but loses the "one sync at a time" guarantee.
- Keep fixed ids, but store no result for these jobs (`keep_result=0`). Then the id only blocks while the job is queued or running, which is exactly what was wanted.

**Chosen:** The second, plus the API now returns `queued: false` when a sync is already running, and the UI says so.

**Why it matters:** Two bugs worked together: a library default I didn't know about, and code that threw away a return value that would have shown it. Fakes in tests hide this kind of thing, so it's worth one real run against the real queue.

---

## 7. Incremental sync that can still notice deletions (Phase 2)

**Problem:** The spec wants connectors to return only *changed* assets, and also wants assets missing from a full scan to be archived. If a connector only reports changes, an unchanged asset and a deleted asset look the same: both are absent.

**Chosen:** Each connector returns two things: the changed assets, and a new cursor with a cheap fingerprint for *every* asset that exists now. Sync archives anything not in the cursor, and only does the expensive work (columns, permissions, embeddings) for the changed ones. Fingerprints are cheap: Postgres's own insert/update/delete counters for tables, size + mtime for files (the file is only read and hashed when those change), and a content hash for Slack channels.

**Result:** A re-sync of an unchanged source processes 0 assets, an edited file re-processes just that file, and a deleted file is archived, never deleted.

---

## 8. The whole app was in Times New Roman, and no test could see it (Phase 3)

**Problem:** The first real browser run (screenshots for the user guide) showed every page in a serif font. Every earlier check passed: pages returned 200, the APIs worked, TypeScript and lint were clean.

**Cause:** The generated `globals.css` had `--font-sans: var(--font-sans);`. A CSS variable that points at itself is invalid, so the browser silently used its default font. The Geist font itself was loading fine (`--font-geist-sans` was set). Found by reading computed styles in the browser, not by guessing.

**Fix:** One line: `--font-sans: var(--font-geist-sans);`. Then a second surprise: the dev server didn't pick up the CSS change, because file watching doesn't work well on the Windows-mounted folder, so it needed `docker compose restart frontend`.

**Why it matters:** Status codes and type checks can't tell you what a page looks like. One real browser run with screenshots found a bug that every automated check missed.

---

## 9. A migration that ran twice at the same time (Phase 3)

**Problem:** After starting the stack, I ran `alembic upgrade head` by hand while the backend container was running its own startup migration. Both tried to create the new `conversations` table. Mine won, and the backend crashed with a duplicate-key error on `conversations_id_seq`.

**Options:** Add a Postgres advisory lock around migrations, or simply never run them by hand while the backend is starting.

**Chosen:** No code change. The backend already migrates on startup, so the manual step was the mistake. Restarting the backend was enough (the migration was already applied). If the app ever runs several backend replicas, the advisory lock becomes worth adding, since replicas would race the same way.

---

## 10. Switching the agent from Claude to an NVIDIA-hosted open model (Phase 3)

**Problem:** The agent was written against Anthropic's Messages API. The user runs on NVIDIA's model API instead. The tool loop, the stored conversation format and the tests all depended on Anthropic types.

**Options:**
- Keep both providers behind a `LLM_PROVIDER` switch. That means two loops, two history formats and twice the tests, for a provider nobody is using.
- Move to the OpenAI chat format. NVIDIA's API is OpenAI-compatible, the OpenAI SDK was already installed for embeddings, and the spec allows either SDK. One base URL setting covers NVIDIA, OpenAI, vLLM or Ollama.

**Chosen:** The second. Two things had to change beyond renaming fields. Open models have no "strict" tool schemas, so a model can send a string where an id belongs, or invent a tool. A new `parse_arguments` checks names and types in code and sends a bad call back to the model as an error, so it can try again instead of crashing the request. And not every OpenAI-compatible server supports `tool_choice: "none"`, so on the last step the loop just sends no tools at all, which forces a text answer anywhere.

**Why it matters:** The permission checks and PII masking didn't change at all, because they live in the tool functions, not in the provider code. That separation made the switch a change to one loop instead of a rewrite.

## 11. The default chat model disappeared from the provider (Phase 3)

**Problem:** With real keys in place, every chat call failed with HTTP 410 Gone. NVIDIA had retired `meta/llama-3.3-70b-instruct`, the model the code used as its default. A model's name being in the provider's `/models` list didn't mean it worked either: two listed models returned 404 and two timed out.

**Options:**
- Pick a replacement from the model list by its description.
- Run one real call with a tool attached against a few candidates, and pick the one that actually returned a correct tool call.

**Chosen:** The second. Only `nvidia/nemotron-3.5-lightning-30b-a3b` answered, and it made the right tool call in 0.7s, so it became the default in `.env`, `.env.example` and `config.py`. The model stays a setting, so swapping it again needs no code change.

**Why it matters:** Hosted models are a moving dependency. A smoke test that makes one real call with a tool attached caught this in seconds. The other way to find out would have been a broken demo.

## 12. Evals failing on right answers, and a tool call leaking out as text (Phase 3)

**Problem:** The first real eval run scored 17/20, below the 90% bar. All three failures were correct answers. The model writes "3 000" with a narrow no-break space (U+202F) and "line‑item" with a non-breaking hyphen (U+2011), and the grader compared plain text. After that was fixed, one question failed 1 time in 3: the model used all 7 tool steps, then on the last step, where no tools are sent, it typed a tool call out as plain text, and the user saw raw `<tool_call>` markup.

**Options:**
- Loosen the expected answers in the YAML, one case at a time.
- Normalise the text once in the grader, so any number or hyphen written either way matches.
- For the leak: strip `<tool_call>` text from the output, or tell the model on the last step that it has no tool calls left.

**Chosen:** Normalise in the grader, since the next question would hit the same thing. For the leak, the note: the model was trying to keep working, so telling it to stop treats the cause, while stripping text would hide it and leave an empty answer. The note is added only to the request and never saved, so stored history stays clean. A unit test checks both. Result: the flaky question passed 5 of 5, and the full run scored 20/20.

**Why it matters:** An eval is only as good as its grader. Before blaming the model, read the failing answers, because two of the three "failures" here were the grader's fault.

---

## 11. A rate limit that would have locked out every user at once (production setup)

**Problem:** Login is limited to 10 attempts per minute per IP. Testing the production stack (Caddy in front of FastAPI) showed the limit counting `172.19.0.6`, Caddy's own container IP, for every request. In production that means all visitors share one bucket: anyone failing to log in 10 times locks *everyone* out. A brute-force protection would have become a one-person denial of service.

**Cause:** Behind a proxy the app has to trust `X-Forwarded-For` to see the real client. Uvicorn only does that for proxies listed in `--forwarded-allow-ips`. The flag was there, as `--forwarded-allow-ips='*'`, but inside `sh -c "..."` in the compose file. Inside double quotes, single quotes are not removed, so uvicorn received the literal string `'*'` with the quotes, matched no proxy, and silently ignored the header.

**Options:**
- Fix the quoting in the shell command. Works, but the next person to edit that line can break it again the same way.
- Use uvicorn's environment variable `FORWARDED_ALLOW_IPS=*`, which has no shell quoting at all.

**Chosen:** The environment variable, with a comment saying why. Trusting every proxy (`*`) is safe here only because the backend port isn't published: the only thing that can reach it is Caddy on the internal Docker network. Re-tested: exactly 10 attempts allowed, then 429, keyed on the forwarded IP.

**Why it matters:** Unit tests passed, because TestClient has no proxy in front of it. The bug only existed in the real deployment shape, so the deployment shape has to be tested, not just the code. And "the flag is there" is not the same as "the flag works": I found it by looking at the actual Redis keys.

## 12. A name detector that can't tell a person from a city (PII Guard)

**Problem:** Column scanning uses Presidio with spaCy's small English model to find names and places. On the demo data it reads many Indian cities as people ("Burhanpur" → PERSON) and addresses as names. A `city` column ended up with about 23% LOCATION hits and 18% PERSON hits, sitting right on the 20% tagging line, so a random sample could miss it completely and leave a column of customer cities unmasked.

**Options:**
- The large spaCy model: better, but a 400 MB download for one feature.
- A gazetteer of Indian cities: accurate on the demo, but it only knows the places someone listed.
- Analyse values joined into one sentence for more context: tested, it was worse (city PERSON hits went up to 40%).
- Count PERSON and LOCATION together: both mean "this column holds personal data", and the 20% line is about whether to mask, not what to call it.

**Chosen:** Per-kind 20% first; if neither passes alone but together they do, tag the more common one. Measured on the whole demo: all 26 PII columns found, zero false positives, two columns with a debatable kind. Free-text columns skip NER entirely: they're tagged on a single match, and NER finds a "name" in almost any sentence.

**Why it matters:** For a privacy feature, a missed column is the expensive mistake; a wrong label on a masked column costs nothing. I measured both directions (misses and over-tagging) against ground truth before choosing, instead of trusting the model's labels.

## 13. Two background jobs racing on personal data (Auto-Docs)

**Problem:** After a sync the worker queues a PII scan and a docs draft for each asset. The draft sends sample values to the LLM and withholds columns tagged as personal data. But arq runs jobs in parallel, so for a brand-new table the draft could run *before* its scan, see no tags yet, and send real customer names and cities to the model. Tests passed, because they call scan then draft in order.

**Options:**
- Delay the docs jobs (`_defer_by`). Makes the race unlikely, not impossible.
- Chain them: the scan job queues the draft when it finishes. Works, but splits one flow across two jobs.
- Have the docs job run the scan itself first.

**Chosen:** The docs job scans first. Scanning is idempotent (tags only grow), so a second scan costs a little CPU and nothing else, and the ordering is now guaranteed by the code instead of by timing.

**Why it matters:** A privacy rule ("never send unmasked PII to the LLM") has to hold under concurrency, not just in a sequential test. I found it by reading the worker flow end to end, not from a failing test.

## 14. An LLM reply cut off by its own thinking (Auto-Docs)

**Problem:** The first real doc draft came back as half a JSON object, so parsing failed. `finish_reason` and `usage` showed why: the NVIDIA model is a reasoning model, and its hidden reasoning counts against `max_tokens`. A one-line answer used ~1,200 reasoning tokens; a table's docs used ~2,500, past the 4,096 budget.

**Chosen:** A 16,000 token budget for this background job, and a cut-off reply (`finish_reason == "length"`) raises a clear error instead of saving a broken draft. The job retries on the next sync.

**Why it matters:** "The model returned bad JSON" was the symptom; the cause was in the token accounting. Checking `finish_reason` and `usage` before blaming the prompt saved a round of prompt tweaking.

## 15. Whose LLM key pays when many companies use the product

**Problem:** Chat and Auto-Docs call the LLM through one global key in `config.py`. That's fine for a demo, but with many companies on the product, one company drafting docs for 10,000 tables would use up the shared rate limit and slow chat down for everyone, and the platform pays for every company's tokens.

**Options:**
- One platform key for everyone. Simple, but companies slow each other down and costs grow with every customer.
- A pool of platform keys, rotated. Token use stays the same, rotating keys to get around rate limits breaks most providers' terms, and one heavy company can still use up the pool.
- Each company brings its own key (BYOK), stored encrypted like source passwords, with the platform key as a capped fallback for trials.

**Chosen (for now):** The demo runs on one key of mine, with the chat rate limit and a spending cap on the provider account. The production design is BYOK: `agent.llm_client()` would take the company and use its key, the one place chat and Auto-Docs both go through.

**Why it matters:** Extra keys don't cut tokens, they only split the bill. BYOK fixes the real problems: companies stop slowing each other down, each pays for its own usage, and companies that handle sensitive data can point the product at their own provider or self-hosted model.

## 16. One model for every task wasted tokens on the easy one (Auto-Docs)

**Problem:** Auto-Docs used the chat model, a reasoning model. Writing a paragraph about a table doesn't need step-by-step thinking, but the model thought anyway: a median of about 1,600 completion tokens and 19.5s per draft.

**Options:**
- Keep one model. Simpler config, but every draft pays for reasoning it doesn't need.
- Turn reasoning off with a prompt trick. That depends on the model and breaks when the model changes.
- Give Auto-Docs its own model setting and pick a small fast model, keeping the strong model for chat, which really does multi-step SQL and tool calls.

**Chosen:** `DOCS_MODEL` / `DOCS_API_KEY`, both falling back to the chat settings. I smoke-tested 8 models the key could list (several were 404 or timed out), then benchmarked the two that worked on the real docs prompt: median 19.5s → 1.7s, 1,599 → 121 completion tokens, 6/6 valid JSON for both.

**Why it matters:** I matched each model to how hard its task is, and I measured the change instead of assuming a smaller model is good enough.

## 15. A safety rule that slowly corrupted its own labels (PII Guard)

**Problem:** PII tags only grow, so a lucky sample can never unmask a column. But a benchmark that re-scanned the catalogue 6 times raised 2 new alerts: the PAN column became "PAN, PERSON" and a city column gained PERSON. Nothing in the data had changed.

**Cause:** Sampling used `ORDER BY random()`, so every scan saw different values. A column sitting near the 20% line crossed it on some scans, and because tags only grow, every crossing stuck. The names-or-places fallback made it worse by adding PERSON to columns already identified as PAN. Over months of 6-hourly syncs every borderline column would collect every kind.

**Chosen:** Seed the sampler (`setseed` per column), so the same data gives the same sample and the same verdict; a new tag now means the data changed. And only use the fallback on columns nothing else matched. The "tags only grow" rule stays: it's right, it just needs deterministic inputs.

**Why it matters:** Two individually sensible rules (random sampling, never remove a tag) combined into a ratchet. A single scan in a test can't show it; running the same scan repeatedly did. The same change also batched the NER calls, 48.5s to 8.6s for a full scan.

## 16. A backup table that looked fresher than the real one (Trust Score)

**Problem:** The trust score gives up to 30 points for freshness. `customers_backup_2023`, a stale backup with its dates stored as text, came out as "modified 2 days ago" and would have outranked `customers_new`, the table everyone should use.

**Cause:** Postgres keeps no "last modified" time for a table. The connector used the newest date in the data, and for tables with no date column fell back to the latest vacuum/analyze time. Those run whenever Postgres decides to tidy up, so they say nothing about the data. The backup has no real date column, so it got the fallback.

**Options:**
- Track changes across syncs (row-change counters) and stamp the sync time when they move. Accurate after the first change, but more state for a fallback.
- Treat "no date column" as unknown freshness: 0 points.

**Chosen:** Unknown is not fresh. The fallback is gone; such tables score 0 for freshness and the asset page shows "unknown". The backup now scores 0 and `customers_new` 50.

**Why it matters:** A trust score is only useful if its inputs mean what they say. A signal that's "usually close" (maintenance times) is worse than no signal, because it's confidently wrong in exactly the cases that matter: stale copies nobody writes to.

## 17. A sync that would quietly widen who can see merged data (File Merger)

**Problem:** A merged file must be visible only to groups that could see *every* original (the intersection). The merge job set that, then ran a sync to catalogue the new file. But sync assigns a file's groups from its folder: an export merged from `sales/` (sales + finance) and `finance/` (finance only) landed in `sales/` and would have been opened up to sales, data the sales team couldn't see before.

**Options:**
- Teach sync about merged files (a flag on the asset). More state, and every connector change has to remember it.
- Run sync first, then narrow the groups. Sync only rewrites groups when a file changes, so they stay narrowed.

**Chosen:** Sync, then narrow. A test puts one copy in a finance-only folder, merges, checks the result is finance-only, then syncs again and checks it still is. The known gap (someone edits the merged file → it gets its folder's groups) is written down rather than hidden.

**Why it matters:** The permission rule was right in the merge code and wrong in the system, because another component owned the same field. Testing the case where the folders' groups differ is what exposed it; with all copies in one folder the intersection and the folder's groups are the same and every test passes.

## 18. MCP auth: the SDK's built-in auth wanted OAuth, the spec wanted personal tokens (MCP Server)

**Problem:** The MCP server has to know which user is calling, so it can apply that user's permissions. The MCP SDK ships auth support, but it is shaped for OAuth: it needs an issuer URL and advertises OAuth metadata, which would send clients like Claude looking for a login flow that doesn't exist. v1 only needs per-user API tokens made in the UI.

**Options:**
- Use the SDK's token verifier and fake the OAuth settings. Works until a client believes the metadata.
- Check the token inside every tool. Unauthenticated requests would still reach the SDK, and every new tool has to remember the check.
- Put a small ASGI wrapper in front of the MCP app: no valid token, no entry (401). It attaches the user to the request, and the tools read it from there.

**Chosen:** The wrapper. Tokens are 32 random bytes shown once; only the SHA-256 is stored (a slow password hash adds nothing when there is nothing to guess). Each tool is a three-line function that calls `agent.run_tool` with actor `mcp`, so MCP has no permission code of its own: the tests for hidden assets and SQL safety are the same checks the chat agent goes through.

**Why it matters:** Two details only show up once it runs like production. The server is stateless, because the API runs as two worker processes and an in-memory MCP session would be lost whenever the next request lands on the other one. And the SDK's Host-header check (DNS rebinding protection) had to go: it is meant for unauthenticated servers on localhost and would reject every request arriving through Caddy under the real domain.

## 19. Code written for an SDK one major version behind (MCP Server, tracing)

**Problem:** The MCP server was first written against `FastMCP`, the API every tutorial shows. The install then brought `mcp` 2.3, where `FastMCP` is gone (now `MCPServer`), transport settings moved from the constructor to `streamable_http_app()`, and the client helper was renamed. Langfuse came as v5, where `LANGFUSE_HOST` is deprecated and, left empty in `.env`, is used as the address instead of falling back to the cloud one.

**Options:**
- Pin the old majors (`mcp<2`) and keep the code. Fast, but ships a new project on a version line that is already being retired.
- Read the installed package and port the code.

**Chosen:** Ported, by reading the installed source rather than guessing: the constructor signature, where the session manager lives, how a tool gets at the HTTP request. For Langfuse, an empty `LANGFUSE_HOST` is dropped at startup so the default applies.

**Why it matters:** Tests against a fake would have passed on either version. What caught it was running the real thing: a real MCP client through the Next.js proxy, and one real question with Langfuse pointed at a local recorder, which also showed the trace carried `XXXX3210` and not the phone number typed into the question.

## 20. Matched ignoring case, indexed with exact case (File Merger)

**Problem:** Merge detection groups files whose column names match after lowercasing, so `Order_ID, Amount` and `order_id, amount` count as the same columns. The code that then lined the files up used the exact names, so the daily job would crash with a KeyError on the first pair of files whose headers differed only in case. The demo files all share one spelling, so no test saw it.

**Options:**
- Match exact names only. Simple, but exports from different tools really do differ in header case.
- Lowercase every header on load. Changes the headers of the merged file people get.
- Rename each file's columns to the oldest file's spelling when lining them up.

**Chosen:** The rename, in one helper used by detection, the preview and the merge itself. The first version of the fix was itself inconsistent: the preview lined files up against one file and the merge against another, so the key column name saved in the proposal didn't exist at merge time. The new test (propose, approve, execute on two such files) failed on exactly that before it passed.

**Why it matters:** "Compare normalised, then use raw" is a bug pattern worth looking for whenever two code paths touch the same names. And the fix needed a test that runs the whole flow, because each half was right on its own.

## 21. CI that never ran, and an empty chat for every closed tab (production polish)

**Problem:** Two things that looked done. The GitHub Actions workflow lived in `data-fog-agent/.github/`, but the repository root is one level up and GitHub only reads workflows from the root, so CI had never run. And a new chat was saved before the answer started, so closing the tab mid-answer (or a model error) left an empty conversation in the list forever.

**Chosen:** Moved the workflow to the root with `working-directory: data-fog-agent/...`, and added a frontend job (types, lint, build) and the eval job. The conversation is now saved together with its first answer, and the browser learns its id in the last event of the stream, so there is nothing half-made to clean up.

**Why it matters:** Neither shows up in a test run: the test suite passes locally whether or not CI exists, and tests never close a tab. Both were found by asking "what happens after this leaves my machine" rather than "does it pass".
