"""Task corpus for the Muscle Memory demo.

40 realistic developer-workflow tasks:
  - 30 recurring tasks across 7 workflow types (the workflows a skill web
    should learn): commit_message x5, pr_description x5,
    stack_trace_explainer x5, issue_triage x4, unit_test_writer x4,
    changelog_entry x4, code_review x3
  - 10 one-off control tasks (25%) that never repeat and must stay
    unskilled (the router must not force them into the skill web).

`Task.type` is the corpus ground-truth label ONLY. It is used for corpus
generation and offline eval. It is NEVER shown to discovery (INTERFACE.md
rule): discovery clusters on prompt+context embeddings only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Task:
    id: str
    type: str            # corpus label ONLY - never shown to discovery
    prompt: str
    context: str
    expected: Optional[str] = None

    def to_contract_dict(self) -> dict:
        """The frozen Task shape from INTERFACE.md."""
        return {
            "id": self.id,
            "type": self.type,
            "prompt": self.prompt,
            "context": self.context,
            "expected": self.expected,
        }


RECURRING_TYPES = [
    "commit_message",
    "pr_description",
    "stack_trace_explainer",
    "issue_triage",
    "unit_test_writer",
    "changelog_entry",
    "code_review",
]
ONE_OFF = "one_off"

# ---------------------------------------------------------------------------
# Recurring instances. Prompts are phrased differently on purpose: embedding
# matching has to catch the workflow shape, not exact strings.
# ---------------------------------------------------------------------------

_COMMIT_MESSAGE = [
    dict(
        type="commit_message",
        prompt="Write a commit message for this diff.",
        context="""diff --git a/api/rate_limit.py b/api/rate_limit.py
index 3f2a1c4..9b77e21 100644
--- a/api/rate_limit.py
+++ b/api/rate_limit.py
@@ -41,7 +41,10 @@ class RateLimiter:
     def allow(self, key: str) -> bool:
         bucket = self._buckets.get(key)
-        if bucket is None:
-            bucket = TokenBucket(self.capacity)
-        return bucket.try_consume(1)
+        now = time.monotonic()
+        if bucket is None or bucket.expired(now):
+            bucket = TokenBucket(self.capacity, ttl=self.ttl)
+            self._buckets[key] = bucket
+        return bucket.try_consume(1, now=now)
 
-    # stale buckets never cleaned up
+    def sweep(self, now: float) -> int:
+        stale = [k for k, b in self._buckets.items() if b.expired(now)]
+        for k in stale:
+            del self._buckets[k]
+        return len(stale)""",
        expected="fix(rate-limit): expire and sweep stale token buckets",
    ),
    dict(
        type="commit_message",
        prompt="Can you write a good commit message for the following diff?",
        context="""diff --git a/web/src/components/Avatar.tsx b/web/src/components/Avatar.tsx
index 81aa0b2..c41d9f8 100644
--- a/web/src/components/Avatar.tsx
+++ b/web/src/components/Avatar.tsx
@@ -12,9 +12,12 @@ export function Avatar({ user, size = 32 }: Props) {
-  return <img src={user.avatarUrl} width={size} height={size} />;
+  const [failed, setFailed] = useState(false);
+  if (failed || !user.avatarUrl) {
+    return <InitialsBadge name={user.displayName} size={size} />;
+  }
+  return (
+    <img src={user.avatarUrl} width={size} height={size}
+         alt={user.displayName}
+         onError={() => setFailed(true)} />
+  );
 }""",
        expected="fix(avatar): fall back to initials badge when avatar image fails to load",
    ),
    dict(
        type="commit_message",
        prompt="Generate a conventional commit message for this change:",
        context="""diff --git a/crates/index/src/search.rs b/crates/index/src/search.rs
index 0f3d2aa..77bc1e9 100644
--- a/crates/index/src/search.rs
+++ b/crates/index/src/search.rs
@@ -88,7 +88,7 @@ impl Index {
     pub fn query(&self, q: &str) -> Vec<DocId> {
-        let mut hits: Vec<_> = self.docs.iter()
-            .filter(|d| d.text.contains(q)).collect();
+        let postings = self.term_map.get(q);
+        let mut hits: Vec<_> = postings.cloned().unwrap_or_default();
         hits.sort_by_key(|d| Reverse(self.score(d)));
         hits
     }""",
        expected="perf(index): replace full scan with postings lookup in query path",
    ),
    dict(
        type="commit_message",
        prompt="What's a good git commit message for this diff? Keep it conventional.",
        context="""diff --git a/internal/http/handlers/users.go b/internal/http/handlers/users.go
index a1b2c3d..e4f5a6b 100644
--- a/internal/http/handlers/users.go
+++ b/internal/http/handlers/users.go
@@ -30,6 +30,11 @@ func (h *Handler) GetUser(w http.ResponseWriter, r *http.Request) {
 	id := chi.URLParam(r, "id")
+	if id == "" {
+		writeError(w, http.StatusBadRequest, "missing user id")
+		return
+	}
 	u, err := h.store.FindUser(r.Context(), id)
 	if errors.Is(err, store.ErrNotFound) {
 		writeError(w, http.StatusNotFound, "user not found")""",
        expected="fix(users): return 400 when user id param is empty",
    ),
    dict(
        type="commit_message",
        prompt="Write a commit message summarizing this diff.",
        context="""diff --git a/web/styles/dashboard.css b/web/styles/dashboard.css
index 22cc448..9d1e5f0 100644
--- a/web/styles/dashboard.css
+++ b/web/styles/dashboard.css
@@ -14,8 +14,8 @@
 .sidebar {
-  width: 240px;
+  width: 264px;
   background: var(--surface-1);
-  overflow-y: scroll;
+  overflow-y: auto;
 }
+.sidebar::-webkit-scrollbar { width: 8px; }
+.sidebar::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }""",
        expected="style(dashboard): widen sidebar and restyle scrollbar",
    ),
]

_PR_DESCRIPTION = [
    dict(
        type="pr_description",
        prompt="Write a pull request description for this change.",
        context="""PR: Add webhook delivery retries with exponential backoff
Branch: feat/webhook-retries -> main
Commits:
- feat(webhooks): add retry queue backed by Redis
- feat(webhooks): exponential backoff with jitter (max 5 attempts)
- test(webhooks): retry schedule unit tests
Files changed: 8 (+612 -84)
Key diff (webhooks/deliver.py):
-    resp = session.post(endpoint, json=payload, timeout=5)
-    resp.raise_for_status()
+    for attempt in range(MAX_ATTEMPTS):
+        try:
+            resp = session.post(endpoint, json=payload, timeout=5)
+            resp.raise_for_status()
+            return Delivery(ok=True, attempts=attempt + 1)
+        except RequestException as exc:
+            sleep(backoff_with_jitter(attempt))
+    return Delivery(ok=False, attempts=MAX_ATTEMPTS, last_error=str(exc))""",
        expected="Summary: adds retry with exponential backoff for failed webhook deliveries. Changes: Redis-backed retry queue, backoff w/ jitter capped at 5 attempts, unit tests. Testing: pytest webhooks/.",
    ),
    dict(
        type="pr_description",
        prompt="Draft a PR description covering summary, changes, and testing for this diff.",
        context="""PR: Migrate session store from memcached to Redis
Branch: chore/session-redis -> main
Commits:
- chore(deps): add redis-py, remove pymemcache
- refactor(sessions): SessionStore protocol + RedisSessionStore
- chore(infra): docker-compose service for redis
Files changed: 14 (+388 -401)
Key diff (sessions/store.py):
-class MemcachedSessionStore:
-    def get(self, sid): return self._mc.get(f"sess:{sid}")
+class RedisSessionStore:
+    def get(self, sid):
+        raw = self._redis.get(f"sess:{sid}")
+        return json.loads(raw) if raw else None
Rollback plan: revert merge, sessions are re-issued on login anyway.""",
        expected="Summary: migrates session storage to Redis behind a SessionStore protocol. Changes: redis-py dep, RedisSessionStore, docker-compose redis service. Testing: session integration suite. Rollback: revert; sessions re-issue on login.",
    ),
    dict(
        type="pr_description",
        prompt="Please write the PR body for this feature branch.",
        context="""PR: Dark mode for settings pages
Branch: feat/dark-mode-settings -> main
Commits:
- feat(theme): add ThemeProvider with system/light/dark
- feat(settings): convert settings pages to theme tokens
- test(theme): visual regression snapshots for both modes
Files changed: 22 (+540 -210)
Screenshots: attached (settings-light.png, settings-dark.png)
Notes: prefers-color-scheme is the default; a manual toggle lives in
Settings > Appearance. LocalStorage persists the override.""",
        expected="Summary: dark mode for settings pages via ThemeProvider and theme tokens. Changes: ThemeProvider (system/light/dark), settings pages on tokens, snapshot tests. Testing: visual regression in both modes.",
    ),
    dict(
        type="pr_description",
        prompt="Write a clear PR description reviewers can act on.",
        context="""PR: Fix N+1 queries on the team members endpoint
Branch: perf/team-members-n1 -> main
Commits:
- perf(teams): eager-load member profiles with selectinload
- test(teams): assert query count on GET /teams/:id/members
Files changed: 3 (+96 -12)
Before: 1 + N queries (one per member); p95 820ms on a 200-member team.
After: 2 queries; p95 61ms on the same fixture.
Key diff (teams/repo.py):
-    team = session.get(Team, team_id)
-    return [m.profile for m in team.members]
+    stmt = (select(Team).where(Team.id == team_id)
+            .options(selectinload(Team.members).selectinload(Member.profile)))
+    return session.execute(stmt).scalar_one().members""",
        expected="Summary: eliminates N+1 on GET /teams/:id/members. Changes: selectinload eager loading, query-count regression test. Testing: new query-count test; p95 820ms -> 61ms on 200-member fixture.",
    ),
    dict(
        type="pr_description",
        prompt="Turn this branch into a proper PR description.",
        context="""PR: Add CSV export to the usage report
Branch: feat/usage-csv-export -> main
Commits:
- feat(reports): /reports/usage.csv endpoint
- feat(reports): stream rows with csv writer (constant memory)
- test(reports): golden-file test for csv output
Files changed: 6 (+214 -18)
Details: respects the same date-range and team filters as the JSON
endpoint. Content-Disposition: attachment. Columns: date, team, seats,
active_users, api_calls. Streams in 5k-row chunks so large ranges don't
buffer in memory.""",
        expected="Summary: adds CSV export for usage reports with the same filters as JSON. Changes: /reports/usage.csv endpoint, streaming writer, golden-file test. Testing: golden-file test over fixture ranges.",
    ),
]

_STACK_TRACE = [
    dict(
        type="stack_trace_explainer",
        prompt="Explain this error and how to fix it.",
        context="""Traceback (most recent call last):
  File "pipeline/ingest.py", line 87, in run
    records = [parse(line) for line in fh]
  File "pipeline/ingest.py", line 52, in parse
    return {"ts": datetime.fromisoformat(parts[0]), "msg": parts[1]}
IndexError: list index out of range

The input file occasionally contains blank lines. Started failing after we
switched log shippers.""",
        expected="parse() splits each line and assumes two fields; blank lines produce a one-element list, so parts[1] raises IndexError. Skip blank lines or guard on len(parts) before indexing.",
    ),
    dict(
        type="stack_trace_explainer",
        prompt="What does this stack trace mean and what's the fix?",
        context="""TypeError: Cannot read properties of undefined (reading 'map')
    at OrderSummary (webpack://app/src/components/OrderSummary.tsx:24:31)
    at renderWithHooks (webpack://app/node_modules/react-dom/cjs/react-dom.development.js:16305:18)
    at mountIndeterminateComponent (webpack://app/node_modules/react-dom/cjs/react-dom.development.js:20074:13)

Component code:
  function OrderSummary({ order }) {
    return <ul>{order.items.map(i => <li key={i.sku}>{i.name}</li>)}</ul>;
  }

Happens right after checkout redirect, before the order poll resolves.""",
        expected="order.items is undefined on the first render after redirect because the order prop hasn't loaded yet. items.map runs unconditionally. Guard with optional chaining/default (order?.items ?? []) or render a loading state until the order resolves.",
    ),
    dict(
        type="stack_trace_explainer",
        prompt="Debug this crash: explain the root cause and suggest a fix.",
        context="""Exception in thread "pool-2-thread-3" java.lang.NullPointerException: Cannot invoke "com.acme.billing.Plan.getQuota()" because "this.plan" is null
	at com.acme.billing.UsageMeter.record(UsageMeter.java:64)
	at com.acme.billing.EventConsumer.onEvent(EventConsumer.java:41)

UsageMeter is constructed in two places; the new EventConsumer path never
calls setPlan() before record().""",
        expected="UsageMeter.plan is null on the EventConsumer construction path because setPlan() is never called there, and record() dereferences it. Initialize plan in the constructor (or look it up in record) and fail fast when it is absent.",
    ),
    dict(
        type="stack_trace_explainer",
        prompt="Explain this panic and how I should fix it.",
        context="""thread 'tokio-runtime-worker' panicked at 'called `Option::unwrap()` on a `None` value', src/cache.rs:118:32
stack backtrace:
   6: core::panicking::panic
   7: core::option::Option<T>::unwrap
   8: cache::Store::get_or_compute
   9: api::handlers::lookup

118:     let entry = self.map.get(&key).unwrap();""",
        expected="get_or_compute unwraps map.get(&key) which returns None when the key is absent (cache miss). Replace unwrap with a match/if-let: on None, compute the value, insert it, and return it.",
    ),
    dict(
        type="stack_trace_explainer",
        prompt="Walk me through this stack trace and the likely fix.",
        context="""panic: runtime error: invalid memory address or nil pointer dereference
[signal SIGSEGV: segmentation violation code=0x1 addr=0x20 pc=0x6f3a12]

goroutine 42 [running]:
main.(*Server).handleWebhook(0xc0000a2000, 0xc0001a8000)
	/app/server/webhook.go:57 +0x92

webhook.go:57:  if evt.Repo.Owner.Login == "" {

evt comes from json.NewDecoder(r.Body).Decode(&evt) but we never check the
decode error; on bad JSON evt.Repo stays nil.""",
        expected="The handler ignores the JSON decode error, so a malformed body leaves evt.Repo nil and line 57 dereferences it. Check the Decode error and return 400 before touching evt fields.",
    ),
]

_ISSUE_TRIAGE = [
    dict(
        type="issue_triage",
        prompt="Triage this GitHub issue: suggest labels, priority, and next step.",
        context="""Title: Search returns stale results after renaming a doc
Body: After renaming a document, searching for the OLD name still returns
the document for ~5 minutes. Searching the new name returns nothing during
that window. Repro: rename any doc, immediately search old name. Expected:
search reflects renames within seconds. Version: v2.4.1, self-hosted,
default config (indexer poll interval 300s).""",
        expected="Labels: bug, search, indexer. Priority P2. Root cause is the 300s indexer poll; next step: reproduce, then either document the lag or add event-driven reindex on rename.",
    ),
    dict(
        type="issue_triage",
        prompt="Help me triage this incoming bug report.",
        context="""Title: App crashes on launch after update to 3.1.0
Body: Updated via Homebrew this morning, now the app crashes immediately
on launch. macOS 14.5, Apple Silicon. Crash log attached - top frame is
in keychain access (SecItemCopyMatching). Workaround: downgrading to
3.0.4 fixes it. 14 thumbs-up, 6 comments confirming same crash on M-series
machines.""",
        expected="Labels: bug, crash, regression, macos, P0-candidate. Priority P0: startup crash hitting all updated Apple Silicon users. Next step: bisect the keychain access change between 3.0.4 and 3.1.0 and ship a hotfix.",
    ),
    dict(
        type="issue_triage",
        prompt="Suggest labels and a priority for this issue.",
        context="""Title: Feature request: export dashboard as PDF
Body: We share weekly metrics with leadership over email. Right now we
take screenshots of 6 charts and stitch them. A one-click PDF export of
the dashboard (respecting current filters and date range) would save the
team ~30 min/week. Nice to have, not blocking anything.""",
        expected="Labels: enhancement, reporting. Priority P3 (feature request with a working workaround). Next step: scope effort; consider print-stylesheet MVP before full PDF rendering.",
    ),
    dict(
        type="issue_triage",
        prompt="Triage: labels, severity, and who should look first.",
        context="""Title: API returns 500 when filtering orders by emoji in customer name
Body: GET /v1/orders?customer=Jos%C3%A9%20%F0%9F%8E%89 returns 500 with
'UnicodeEncodeError: ascii codec can't encode character'. Orders with
plain-ASCII names filter fine. Started after the Postgres 14 -> 16
upgrade. Affects any customer with non-ASCII names (~8% of our base).""",
        expected="Labels: bug, api, encoding, regression. Priority P1: 500s for ~8% of customers after the Postgres upgrade. Next step: backend oncall - check client_encoding/collation change in the PG16 migration.",
    ),
]

_UNIT_TEST = [
    dict(
        type="unit_test_writer",
        prompt="Write unit tests for this function.",
        context='''def clamp_discount(pct: float, *, member: bool) -> float:
    """Clamp a discount percentage to allowed bounds.
    Members: 0-40%. Non-members: 0-15%. Negative input clamps to 0.
    Anything above the cap clamps to the cap."""
    cap = 40.0 if member else 15.0
    return min(max(pct, 0.0), cap)

Use pytest. Cover the boundaries and both member states.''',
        expected="pytest cases: below 0 clamps to 0, within range passes through, above cap clamps to cap, for both member=True (cap 40) and member=False (cap 15), plus exact boundary values 0/15/40.",
    ),
    dict(
        type="unit_test_writer",
        prompt="Add unit tests covering the edge cases of this class.",
        context="""class RingBuffer:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._buf, self._head, self._size = [None] * capacity, 0, 0
    def push(self, item):
        self._buf[self._head] = item
        self._head = (self._head + 1) % len(self._buf)
        self._size = min(self._size + 1, len(self._buf))
    def items(self):
        if self._size < len(self._buf):
            return self._buf[:self._size]
        return self._buf[self._head:] + self._buf[:self._head]

Cover: empty buffer, fill to capacity, overwrite past capacity (order
preserved), and invalid capacity.""",
        expected="pytest cases: empty -> []; pushes up to capacity preserve insertion order; push beyond capacity evicts oldest and items() returns oldest-to-newest; capacity 0 and -1 raise ValueError.",
    ),
    dict(
        type="unit_test_writer",
        prompt="Write a pytest suite for this utility, including failure modes.",
        context='''def parse_retry_header(value: str | None) -> int | None:
    """Parse a Retry-After header into seconds.
    Returns None when the header is absent or unparseable.
    Supports integer seconds only (not HTTP dates)."""
    if value is None:
        return None
    value = value.strip()
    if not value.isdigit():
        return None
    return int(value)''',
        expected="pytest cases: None -> None; '30' -> 30; ' 45 ' -> 45; 'abc' -> None; '' -> None; '3.5' -> None; an HTTP-date string -> None (unsupported); large integer passes through.",
    ),
    dict(
        type="unit_test_writer",
        prompt="Generate unit tests for this TypeScript function (vitest).",
        context="""export function formatRelativeTime(iso: string, now = new Date()): string {
  const then = new Date(iso);
  const s = Math.floor((now.getTime() - then.getTime()) / 1000);
  if (Number.isNaN(s)) return "unknown";
  if (s < 0) return "in the future";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}""",
        expected="vitest cases: invalid date -> 'unknown'; future timestamp -> 'in the future'; 5s/59s -> 'Ns ago'; 90s -> '1m ago'; 2h -> '2h ago'; 3d -> '3d ago'; boundaries at 60/3600/86400 seconds.",
    ),
]

_CHANGELOG = [
    dict(
        type="changelog_entry",
        prompt="Write a changelog entry for this release.",
        context="""Release v2.5.0 - merged PRs:
#412 feat: add webhook delivery retries with exponential backoff
#418 feat: CSV export for usage reports
#420 fix: N+1 queries on team members endpoint
#421 fix: return 400 instead of 500 when user id param is missing
#423 chore: migrate session store from memcached to Redis
Style: Keep a Changelog, sections Added/Fixed/Changed.""",
        expected="## [2.5.0] Added: webhook retries with backoff (#412), CSV usage export (#418). Fixed: N+1 on team members (#420), 400 for missing user id (#421). Changed: sessions on Redis (#423).",
    ),
    dict(
        type="changelog_entry",
        prompt="Draft release notes from these merged PRs.",
        context="""Release v3.1.1 (patch) - merged PRs:
#502 fix: startup crash on Apple Silicon caused by keychain entitlement
#505 fix: search index lag after document rename (poll 300s -> 5s)
#507 docs: clarify self-hosted indexer configuration
Audience: end users, keep it non-technical.""",
        expected="## [3.1.1] Fixed: app no longer crashes on launch for Apple Silicon Macs; renamed documents now appear in search within seconds. Docs: clearer self-hosted indexer setup.",
    ),
    dict(
        type="changelog_entry",
        prompt="Turn this merge list into a proper changelog section.",
        context="""Release v1.9.0 - merged PRs:
#88 feat(api): cursor-based pagination on /v1/orders (deprecates offset)
#91 feat(api): include customer.email in order payload
#94 fix(api): 500 when filtering by non-ASCII customer names
#95 security: upgrade jsonwebtoken 8.x -> 9.x (CVE-2022-23529 et al.)
Style: Keep a Changelog; include a Security section; note deprecations.""",
        expected="## [1.9.0] Added: cursor pagination on /v1/orders, customer.email in order payload. Fixed: 500 filtering non-ASCII names. Security: jsonwebtoken 9.x. Deprecated: offset pagination on /v1/orders.",
    ),
    dict(
        type="changelog_entry",
        prompt="Write the changelog for this week's release.",
        context="""Release v0.14.0 - merged PRs:
#201 feat: dark mode for settings pages
#204 feat: PDF export of dashboards
#208 perf: dashboard initial load 3.2s -> 1.4s (bundle split + prefetch)
#209 fix: avatar images 404 for users without a gravatar
Audience: a public repo's users; keep the tone friendly and brief.""",
        expected="## [0.14.0] Added: dark mode for settings; one-click PDF export of dashboards. Improved: dashboards load over twice as fast (3.2s -> 1.4s). Fixed: missing avatars no longer 404.",
    ),
]

_CODE_REVIEW = [
    dict(
        type="code_review",
        prompt="Review this diff: flag bugs, risks, and missing tests.",
        context="""diff --git a/billing/invoices.py b/billing/invoices.py
@@ -58,6 +58,14 @@ def finalize_invoice(invoice_id: str) -> Invoice:
     invoice = store.get(invoice_id)
+    for line in invoice.lines:
+        if line.discount_pct > 0:
+            line.total = line.total * (1 - line.discount_pct / 100)
     invoice.total = sum(l.total for l in invoice.lines)
-    invoice.status = "finalized"
+    invoice.status = "final"
     store.save(invoice)
+    send_receipt_email(invoice.customer_email, invoice)
     return invoice""",
        expected="Findings: status value changed 'finalized' -> 'final' (breaks anything filtering on 'finalized'); discounts applied on every call, so re-finalizing double-discounts; send_receipt_email fires before save failure is handled and has no retry; no test for the discount path.",
    ),
    dict(
        type="code_review",
        prompt="Do a code review pass on this change.",
        context="""diff --git a/auth/tokens.py b/auth/tokens.py
@@ -12,7 +12,7 @@ def issue_token(user_id: str) -> str:
     payload = {"sub": user_id, "iat": now}
-    payload["exp"] = now + 3600
+    payload["exp"] = now + 60 * 60 * 24 * 30
-    return jwt.encode(payload, SECRET, algorithm="HS256")
+    return jwt.encode(payload, SECRET, algorithm="HS256", headers={"kid": "v1"})""",
        expected="Findings: token lifetime silently extended 1h -> 30d (security regression, needs justification + config); kid header added without a key-rotation story; no test asserting the new expiry or header.",
    ),
    dict(
        type="code_review",
        prompt="Please review this PR diff before I merge it.",
        context="""diff --git a/search/indexer.py b/search/indexer.py
@@ -33,9 +33,7 @@ def reindex(doc_ids: list[str]) -> None:
     for doc_id in doc_ids:
-        doc = store.load(doc_id)
-        if doc is None:
-            continue
-        index.upsert(doc_id, extract_terms(doc))
+        docs = store.load_many(doc_ids)
+        for doc in docs:
+            index.upsert(doc.id, extract_terms(doc))
     index.flush()""",
        expected="Findings: load_many is called inside the loop over doc_ids (batch load repeated per id - likely a bug, hoist it out); the old None-skip guard is gone, so missing docs may crash extract_terms; behavior differs when load_many silently drops missing ids. Needs a test with a missing doc id.",
    ),
]

# ---------------------------------------------------------------------------
# One-off control tasks (25%): each a distinct, never-repeating workflow.
# The router must leave these on the frontier path.
# ---------------------------------------------------------------------------

_ONE_OFFS = [
    dict(
        type=f"{ONE_OFF}/regex_explain",
        prompt="Explain what this regex does, piece by piece.",
        context=r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+\[(?P<level>\w+)\]\s+(?P<msg>.*)$",
        expected="Named groups: ts matches an ISO-like timestamp, level matches a bracketed word, msg captures the rest of the line.",
    ),
    dict(
        type=f"{ONE_OFF}/git_reflog",
        prompt="I deleted a branch with unmerged commits. How do I get it back?",
        context="Branch was 'feat/payment-retry', deleted with -D about an hour ago. Last thing I remember committing was 'add jitter to backoff'.",
        expected="Use git reflog to find the branch tip SHA, then git branch feat/payment-retry <sha> (or git checkout -b). Reflog keeps ~90 days of HEAD history.",
    ),
    dict(
        type=f"{ONE_OFF}/sql_query",
        prompt="Write a SQL query for this report.",
        context="Tables: orders(id, customer_id, total_cents, created_at), customers(id, name, country). Report: top 10 customers by total spend in the last 90 days, with order counts, US customers only.",
        expected="SELECT c.name, COUNT(o.id) AS orders, SUM(o.total_cents) AS spend FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.country='US' AND o.created_at >= CURRENT_DATE - INTERVAL '90 days' GROUP BY c.name ORDER BY spend DESC LIMIT 10;",
    ),
    dict(
        type=f"{ONE_OFF}/docker_compose",
        prompt="Why does my web service keep restarting in docker-compose?",
        context="""web:
  build: .
  command: python manage.py runserver 0.0.0.0:8000
  depends_on: [db]
db:
  image: postgres:16
Logs show: django.db.utils.OperationalError: could not connect to server: Connection refused, then the container exits and restarts.""",
        expected="depends_on only waits for container start, not Postgres readiness. Add a healthcheck on db with condition: service_healthy (or a wait-for script) so web starts after Postgres accepts connections.",
    ),
    dict(
        type=f"{ONE_OFF}/bash_one_liner",
        prompt="Give me a bash one-liner for this.",
        context="I have ~4,000 .heic photos in ~/Photos/trip mixed with .jpg and .mov files. I want a list of just the .heic files sorted by size, largest first, showing human-readable sizes.",
        expected="find ~/Photos/trip -maxdepth 1 -iname '*.heic' -exec du -h {} + | sort -rh",
    ),
    dict(
        type=f"{ONE_OFF}/http_status",
        prompt="Which HTTP status code should this endpoint return?",
        context="PUT /v1/documents/:id - creates the document when the id doesn't exist, fully replaces it when it does. Client supplies the full body both times. What should the success status be in each case, and why not 200 everywhere?",
        expected="201 when created, 200 (or 204) when replaced. 201 + Location header signals resource creation; 200 everywhere hides whether a new resource was made, which clients and caches care about.",
    ),
    dict(
        type=f"{ONE_OFF}/cron_explain",
        prompt="What does this cron expression do?",
        context="15 2 * * 1-5  /opt/jobs/rollup.sh --yesterday >> /var/log/rollup.log 2>&1   (server timezone is UTC)",
        expected="At 02:15 UTC Monday-Friday: runs rollup.sh --yesterday, appending stdout and stderr to /var/log/rollup.log.",
    ),
    dict(
        type=f"{ONE_OFF}/json_schema",
        prompt="Design a JSON schema for this payload.",
        context="Event payload for 'order.shipped': order id (uuid), tracking number (string, may be null), carrier enum (ups, fedex, usps, dhl), shipped_at ISO8601, items array of {sku, quantity>=1}, optional gift_message up to 280 chars.",
        expected="JSON Schema with type object; required order_id(uuid format), carrier(enum), shipped_at(date-time), items(array of {sku, quantity minimum 1}); tracking_number as [string, null]; gift_message optional maxLength 280.",
    ),
    dict(
        type=f"{ONE_OFF}/env_config",
        prompt="Help me pick between these config approaches.",
        context="12-person team, one Rails monolith, deploying to Heroku. Currently: a shared .env file in 1Password plus config/*.yml checked into git with real staging credentials. Considering dotenv per env, Rails credentials, or Heroku config vars only.",
        expected="Use Heroku config vars as the source of truth for deployed envs and dotenv locally; stop checking real staging credentials into git (rotate them now). Rails credentials add little on Heroku.",
    ),
    dict(
        type=f"{ONE_OFF}/yaml_anchors",
        prompt="How do I DRY up this YAML with anchors?",
        context="""deploy-staging:
  image: registry/ci:22
  services: [postgres:16]
  before_script: [apt-get update, make deps]
deploy-prod:
  image: registry/ci:22
  services: [postgres:16]
  before_script: [apt-get update, make deps]
  rules: [{if: $CI_COMMIT_BRANCH == "main"}]""",
        expected="Define a .deploy-base anchor (&deploy-base) holding image/services/before_script, then merge it into both jobs with <<: *deploy-base, keeping prod's rules key.",
    ),
]

_RECURRING = _COMMIT_MESSAGE + _PR_DESCRIPTION + _STACK_TRACE + \
    _ISSUE_TRIAGE + _UNIT_TEST + _CHANGELOG + _CODE_REVIEW


def build_corpus() -> list[Task]:
    """40 tasks: 30 recurring (7 types) + 10 one-off controls (25%),
    interleaved deterministically so a batch run looks like natural
    traffic."""
    recurring = [Task(id="", **kw) for kw in _RECURRING]
    one_offs = [Task(id="", **kw) for kw in _ONE_OFFS]

    by_type: dict[str, list[Task]] = {}
    for t in recurring:
        by_type.setdefault(t.type, []).append(t)

    # Round-robin across recurring types; one one-off per round-robin pass.
    merged: list[Task] = []
    pools = [by_type[t] for t in RECURRING_TYPES]
    oi = 0
    while any(pools) or oi < len(one_offs):
        for pool in pools:
            if pool:
                merged.append(pool.pop(0))
        if oi < len(one_offs):
            merged.append(one_offs[oi])
            oi += 1

    for i, t in enumerate(merged, start=1):
        t.id = f"task-{i:04d}"
    return merged


def recurring_only(corpus: list[Task]) -> list[Task]:
    return [t for t in corpus if not t.type.startswith(ONE_OFF)]


def one_offs_only(corpus: list[Task]) -> list[Task]:
    return [t for t in corpus if t.type.startswith(ONE_OFF)]


if __name__ == "__main__":
    from collections import Counter
    corpus = build_corpus()
    counts = Counter(t.type.split("/")[0] for t in corpus)
    total = len(corpus)
    controls = len(one_offs_only(corpus))
    print(f"{total} tasks | recurring {total - controls} | "
          f"one-off controls {controls} ({controls / total:.0%})")
    for k, v in counts.items():
        print(f"  {k}: {v}")
