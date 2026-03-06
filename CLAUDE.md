# bigfoot — Project Instructions

## Certainty is the Contract

bigfoot's entire value proposition is certainty: when a test passes, you **know** exactly what happened — not just that nothing crashed.

### Full Assertion Certainty is Mandatory for All Plugins

Every plugin MUST enforce that all recorded fields are asserted. This is non-negotiable.

**The rule:** `assertable_fields(interaction)` MUST return `frozenset(interaction.details.keys())` minus any fields explicitly excluded for ergonomic reasons (e.g., fields that are already implicit from the source sentinel). The default implementation in `BasePlugin` enforces this.

**What is PROHIBITED:**
- Auto-asserting interactions without requiring explicit `assert_interaction()` calls (no more `mark_asserted()` at record time without user assertion)
- Returning `frozenset()` from `assertable_fields()` without a documented, specific reason
- Recording data in `interaction.details` that callers are not required to assert

**What is REQUIRED:**
- Every field stored in `interaction.details` must be assertable and required by default
- If a field is not meaningful to assert (e.g., internal metadata), exclude it from `details` entirely — do not record it and then silently skip it
- New plugins must use `frozenset(interaction.details.keys())` as their `assertable_fields` implementation unless there is a specific, documented ergonomic reason to exclude a field

### Ergonomic Assertion Helpers are Encouraged

Plugins may (and should) provide typed assertion helper methods on their proxy objects to make asserting common patterns ergonomic. These helpers MUST still enforce full field coverage — they are wrappers around `assert_interaction()`, not bypasses.

### The Test of Certainty

Ask yourself: if every `assert_interaction()` call in a test were removed, would the test still pass? If yes, the plugin is not providing certainty. Fix it.
