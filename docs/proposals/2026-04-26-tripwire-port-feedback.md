# Tripwire improvement proposals: feedback from the tripwire (Nim) port

**Created:** 2026-04-26
**Project:** tripwire (`/Users/eek/Development/tripwire`)
**For:** a fresh Claude Code session - paste this whole file as the first message.

---

## Context

You are working on `tripwire`, a Python testing/sandboxing library. The user is the
author and the only user. There is no compatibility window to worry about.

Recently I ported (some of) tripwire's design into `tripwire`, a Nim equivalent
used by paperplanes (a Kraken arbitrage bot). The port surfaced several
design seams in tripwire worth examining. This file is the prompt to act on
that feedback.

The goals here are concrete, prioritized, and bounded. Do them in priority
order. After each, run the test suite (or write tests first per the user's
preference) and commit. Do NOT bundle multiple proposals into one commit;
each lands separately.

The user's standing direction (verbatim): "If there is a bug, fix it. If there
is a missing feature, add it." "What is most correct, most ergonomic? we don't
care about blast radius, we just want what is correct and right." "no need
for deprecated alias. this is all liquid, baby."

---

## Operating directives

- **You are the tripwire author and the only user.** Single source of authority.
  No "but what about other users."
- **Subagents for substantive work** per `~/.claude/CLAUDE.md`. Dispatch them
  for code reads / writes / tests; orchestrate from the main context.
- **TDD where it fits.** Behavioral changes get a failing test first.
- **No commits with AI attribution** (no `Co-Authored-By`, no "Generated with
  Claude," no bot signatures).
- **No GitHub issue references** in commit messages, PR titles, or PR
  descriptions (no `#123`, no `fixes #X`).
- **No em-dashes** in any user-facing text. Use `-` or restructure.
- **No `--no-verify`** on commits. If a hook fails, fix it.
- **No push without explicit user approval.** Local commits are pre-authorized;
  pushes are not.
- **Worktree** if the change set spans many files. Optional.
- Run `python -m pytest` (or whatever the project uses; verify via `pyproject.toml`)
  before each commit. Don't regress.

---

## Proposals, in priority order

Each proposal has: rationale, concrete change, acceptance test, and effort
estimate. Land them in this order; each is independent.

---

### Proposal 1 - Default `guard` to `"error"`, not `"warn"`

**Priority:** HIGH. This is the biggest one.

**Rationale.** Tripwire today defaults `guard` to `"warn"`. A fresh project
that imports tripwire and writes a test without configuring guard will
silently make real network calls, real subprocess invocations, real DNS
lookups - they pass through with a warning the test runner may swallow.
That is the opposite of what a sandbox should do. A testing tool's prime
directive is "no surprise side effects." Default-warn violates that.

The tripwire port deliberately defaults to error. A dev who wants warn
opts in. CI never accidentally hits real DNS.

The README sells "warn" as the gentle on-ramp. That's fine; reframe it
as a *legacy-migration* mode, not a default. New projects should fail
loud.

**Concrete change.**

1. In `pyproject.toml` schema and tripwire's config defaults: set the
   default for `[tool.tripwire] guard` to `"error"` (find the constant or
   the parser default; grep for `"warn"` near guard parsing).
2. Update `README.md` and any quickstart docs:
   - State the default is `"error"`.
   - Add a "When to use `warn`" subsection: incremental migration of
     legacy test suites that have not yet been wrapped in `with tripwire:`
     blocks. Caveat that warn mode lets real I/O through and should not
     be the steady state.
3. Update CHANGELOG.md under `[Unreleased]` with a clear breaking-change
   note.

**Acceptance test.**

Add a test that:
- creates a project with no tripwire config,
- imports tripwire,
- makes an unmocked HTTP request outside any `with tripwire:` block,
- asserts an error is raised (not a warning logged).

**Effort.** Small (defaults change + docs + 1 test).

---

### Proposal 2 - Per-plugin `passthrough_safe` declaration + distinct error

**Priority:** HIGH. This prevents `guard="warn"` from being a footgun.

**Rationale.** When `guard="warn"`, tripwire today probably lets every
unmocked call through. For some plugins that's fine: a Mock plugin's
"passthrough" is identity, no harm done. For other plugins it's
destructive: HTTP makes a real network request, subprocess forks a real
process, DNS hits a real resolver, file writes mutate the disk. CI
quotas, external state, and real money can all be at risk.

Tripwire's plugin base class has `supportsPassthrough() -> bool`. Mock
returns true; httpclient, subprocess, websock, and chronos return false.
When tripwire's outside-sandbox guard mode is set to warn but the plugin
can't passthrough safely, it raises `OutsideSandboxNoPassthroughDefect`
with a pedagogical message: "plugin X doesn't support outside-sandbox
passthrough; install a sandbox or set guard=error."

Tripwire would benefit from the same gate. Otherwise `guard="warn"` is
indistinguishable from "make all real I/O happen, with a log line."

**Concrete change.**

1. Add a `passthrough_safe: bool` class attribute (default `False`) to
   tripwire's plugin base class.
2. Set `passthrough_safe = True` on Mock-style plugins where passthrough
   is genuinely a no-op or identity. Audit each plugin in the codebase;
   default to False if there is any doubt.
3. Add a new exception class `UnsafePassthroughError` (or similar; align
   with tripwire's existing exception naming convention - read `errors.py`
   or wherever exceptions live first).
4. In the guard-mode dispatch path: when `guard="warn"` and an unmocked
   call hits a plugin where `passthrough_safe is False`, raise
   `UnsafePassthroughError` with a message that says, verbatim or close:
   "plugin {name} doesn't support outside-sandbox passthrough; either
   install a `with tripwire:` block, set guard='error' to make this fail
   loudly, or mark this plugin passthrough_safe=True if you've audited
   that the underlying call has no side effects."

**Acceptance test.**

Two cases:
- guard=warn + Mock plugin (passthrough_safe=True) -> warning logged,
  call returns the underlying value.
- guard=warn + HTTP plugin (passthrough_safe=False) -> raises
  UnsafePassthroughError with the helpful message.

**Effort.** Small to medium. The guts are one new attribute and one new
branch in the guard dispatch path. The audit of which plugins are
genuinely safe is the larger piece.

---

### Proposal 3 - Per-protocol guard granularity

**Priority:** MEDIUM. Quality-of-life feature, not a correctness fix.

**Rationale.** `guard` today is a single binary global. Operators in
practice want different strictness for different protocols. Pytest
collectors stat() lots of files; defaulting to error on file I/O is
hostile. But DNS and subprocess should fail loud unconditionally.

**Concrete change.**

Allow per-protocol overrides in `pyproject.toml`:

```toml
[tool.tripwire]
guard = "warn"           # default for everything
guard.dns = "error"      # except DNS, always raise
guard.subprocess = "error"
guard.file = "warn"      # explicit, even though same as default
```

Implementation:
1. Extend the config parser to accept a dict-or-string under `guard`. If
   string: backward-compatible global. If dict: per-protocol with a
   default key (or use `guard` itself as the default and `guard.X` for
   overrides; pick whichever schema is cleanest in TOML).
2. Wire the per-protocol values through the dispatch path. The plugin
   knows its own protocol identifier already.

**Acceptance test.**

Config with `guard = "warn"` plus `guard.dns = "error"`:
- Outside-sandbox HTTP call -> warns (or passthrough per Proposal 2).
- Outside-sandbox DNS lookup -> raises.

**Effort.** Medium. Config schema + dispatch wiring + tests.

---

### Proposal 4 - Distinguish "no sandbox ever" from "post-sandbox interaction"

**Priority:** MEDIUM. Async-debugging quality issue.

**Rationale.** Tripwire has two distinct defects:
- `LeakedInteractionDefect`: TRM fired with empty verifier stack ("you
  forgot a sandbox").
- `PostTestInteractionDefect`: verifier was popped (sandbox exited) but
  generation counter still active ("your async cleanup is wrong; a
  Future / Task / Thread survived `with tripwire:` exit and fired after").

These are *genuinely different bugs*. The first is a missing sandbox
declaration. The second is a leak of in-flight async work past the
sandbox lifetime. Catching both under one error makes async leak
debugging much harder than necessary.

If tripwire today raises one error for both, split them. Verify by reading
tripwire's exception hierarchy (`grep -rn "class.*Error\\|class.*Defect" src/`).

**Concrete change.**

1. Read tripwire's current handling of "call fired outside the active
   sandbox" - is this one path or already two? If one, split. If already
   two, this proposal is satisfied; move on.
2. Add `PostSandboxInteractionError` (or whatever fits tripwire's naming).
3. Track sandbox generation: when `with tripwire:` exits, mark the sandbox
   inactive but keep an identity. Calls that fire on a-known-but-inactive
   sandbox raise the post-sandbox error; calls with no sandbox identity
   raise the leaked-interaction error.

**Acceptance test.**

Two tests:
- Call without ever entering `with tripwire:` -> `LeakedInteractionError`.
- `with tripwire:` block that schedules an asyncio Task; block exits before
  Task completes; Task makes an unmocked call -> `PostSandboxInteractionError`.

**Effort.** Medium. Generation tracking on the sandbox object plus exception
split.

---

### Proposal 5 - Pedagogical error messages for outside-sandbox failures

**Priority:** LOW-MEDIUM. UX polish, not correctness.

**Rationale.** When tripwire raises outside a sandbox, the message should
state the user's mental model, not just the implementation detail. A new
user seeing the current error has to figure out from context that they
forgot to wrap in `with tripwire:`.

Tripwire's message: `"TRM fired on thread {tid} with no active verifier
at {file}:{line}"` is functional but mechanical. Better:

> `Call to {plugin}.{method}({args}) at {file}:{line} happened OUTSIDE
> any "with tripwire:" block. Wrap the call in a sandbox and add an
> allow(...) for it, OR set guard="warn" in pyproject.toml if the call
> is intentional and safe.`

**Concrete change.**

Find every outside-sandbox raise site. Update the message to include:
- Which plugin and method was called
- The call site (file:line)
- The user-mental-model framing ("OUTSIDE any `with tripwire:` block")
- The two options for fixing it

**Acceptance test.**

Capture the exception message and assert it contains the framing strings.
This is a regression guard against the message drifting back to mechanical.

**Effort.** Small. String changes + assert tests.

---

### Proposal 6 - Pytest marker for per-test guard override

**Priority:** LOW-MEDIUM. Migration affordance.

**Rationale.** Per-test override lets strict tests live next to permissive
ones during a guard migration. Natural fit for pytest. Tripwire can't do
this cleanly because it's compile-time; tripwire can.

**Concrete change.**

Register a pytest marker `tripwire_guard("error" | "warn" | dict-form)`.
When the test starts, the marker (if present) overrides the project's
guard setting for the duration of that test. When the test ends, restore
prior config.

```python
@pytest.mark.tripwire_guard("error")
def test_strict_dns():
    # this test fails loud on any unmocked call
    ...
```

Implementation:
1. Add the marker registration in tripwire's pytest plugin (find it in
   `src/tripwire/pytest_plugin.py` or similar).
2. Hook into a pytest fixture (autouse, narrow scope) that reads the
   marker, overrides the config, yields, and restores.
3. Document in the README and the migration guide.

**Acceptance test.**

A test file with two tests:
- One marked `tripwire_guard("error")`: makes an unmocked call, expects raise.
- One marked `tripwire_guard("warn")`: makes an unmocked call, expects warning.

**Effort.** Small. Pytest marker registration + fixture + docs.

---

### Proposal 7 - Strict TOML schema validation at parse time

**Priority:** LOW. Footgun-prevention.

**Rationale.** `guard = "Warn"` (capital W) versus `guard = "warn"` is the
kind of typo that silently does the wrong thing if the parser falls back
to a default. Validate strictly. Reject unknown values at parse time.

Tripwire's parser validates against the enum at parse time and raises
on unknown values.

**Concrete change.**

Find the TOML config parser. After reading `guard`, validate the value
is one of the accepted set. On mismatch, raise a clear error:

> `Invalid value "{got}" for [tool.tripwire] guard. Expected one of: "warn",
> "error". (Per-protocol form also accepted; see docs.)`

Apply the same validation to any other config keys that take a closed set.

**Acceptance test.**

Config with `guard = "Warn"` (typo) -> ImportError or ConfigError at
collection time, not silent warn-or-error fallback.

**Effort.** Small. Validation function + tests.

---

### Proposal 8 - README "what default should I pick?" guidance

**Priority:** LOW. Docs only.

**Rationale.** Both modes are described neutrally today. After Proposal 1
flips the default, the README needs a clear positioning paragraph that
tells the user when to use which:

- **For new projects:** keep the default `"error"`. Real I/O outside
  sandboxes is almost always a bug.
- **For migrating legacy test suites:** set `guard = "warn"` while you
  add `with tripwire:` blocks incrementally. Plan to flip back to
  `"error"` once the migration is done.
- **For mixed CI:** use per-protocol overrides (Proposal 3) to be strict
  on the dangerous protocols (DNS, subprocess) and permissive on the
  safe ones (file).

**Concrete change.** Edit the README. Add a section under the
configuration docs.

**Acceptance test.** N/A (docs).

**Effort.** Small.

---

## Bonus: documentation work

These don't ship as code but are worth noting. Decide whether to land them
during this batch or after.

### B1 - Vocabulary clarity

Make explicit in docs that `tripwire.allow(...)` and `tripwire.restrict(...)`
are *sandbox-scoped*, while `guard` is *module-scoped (global)*. They do
not compose. A user who tries `tripwire.allow(...)` outside a `with` block
will get a confusing error because there is no sandbox to attach the
allow to. Either:
- document the divide loudly, OR
- raise a clear error from `allow()` / `restrict()` when called outside
  a sandbox, with a message that points the user at `guard` instead.

### B2 - Async edge case: contextvars + threadpools

Verify `with tripwire:` survives correctly across:
- `asyncio.to_thread(...)` (which uses a default thread executor)
- `concurrent.futures.ProcessPoolExecutor` (which fork-execs a child)
- `asyncio.create_task(...)` (which inherits the current context)

Python's contextvars + threadpools are notoriously subtle. The right
behavior is probably: `with tripwire:` state propagates to threads via
contextvars (it should "just work"), but does NOT propagate to subprocesses
(those are a separate process boundary; configure them via env or config
file, not via context). Document and test.

---

## What "done" looks like

Per proposal:
1. A failing test is committed first (TDD).
2. The implementation is committed second.
3. Proposals are landed in priority order, NOT bundled.
4. CHANGELOG.md `[Unreleased]` entry per proposal.
5. README and other docs updated where applicable.
6. `python -m pytest` (or the project's actual test command) is green
   after each commit.

Final state: 8 proposals' worth of commits on a branch ready to push.
Do not push without explicit user approval.

---

## Pointers

- `src/tripwire/` for the implementation
- `tests/` for the test suite
- `pyproject.toml` for project configuration and config schema
- `CHANGELOG.md` for release notes
- `docs/` for the user-facing documentation
- `~/.claude/CLAUDE.md` for the user's standing operating directives
- `AGENTS.md` (or `CLAUDE.md`) at the tripwire repo root for repo-specific
  conventions; read first

Good luck. The user is patient and rigorous; match the energy.
