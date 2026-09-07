# Kite Login SDK — Adversarial Test Plan

## Purpose
This SDK automates Kite (Zerodha) login including TOTP 2FA. The goal of this
plan is NOT to reach high code coverage. The goal is to find ways the system
actually fails in production, especially failures the implementation itself
never considered. Standard "LLM writes tests while looking at the code"
produces confirmatory tests — this plan is designed to avoid that.

## Ground rule for whoever (or whatever) implements this
Do NOT read `kite_login.py` / the SDK implementation before Phase 1 is done.
Phase 1 must be completed purely from this spec. Only after the failure-mode
test skeletons exist should the implementation be opened, to wire up fixtures
and confirm the tests actually exercise the real code paths.

If at any point a test is "adjusted to make it pass" — stop. That's the bias
this plan exists to prevent. A failing test that reveals a real gap is a good
outcome, not a bug in the test.

---

## Phase 0 — Recorded reality (fixtures)
Before writing any test logic, capture real artifacts, secrets scrubbed:
- Full HTML of the Kite login page (username/password step)
- Full HTML of the TOTP entry step
- Full HTML of a "new device" / CAPTCHA / forced-verification variant, if one
  can be triggered or has been seen before
- Raw network request/response pairs for a real login (headers redacted)
- Timestamps of an actual observed TOTP lockout event, if reproducible safely
  in a sandbox account

These become fixtures. Do not let the implementation's own mocks become the
test fixtures — recorded reality only.

## Phase 1 — Failure-mode test skeletons (implementation-blind)
For each item below, write a test name + assertion intent (not full code yet).
Organize as one file per category.

### 1. TOTP timing
- [ ] Login attempted with TOTP generated 1s before a 30s window boundary
- [ ] Login attempted with TOTP generated 1s after a window boundary
- [ ] Local clock drifted +/- N seconds from real time (simulate via injectable
      clock, not `time.sleep`)
- [ ] Network delay inserted between TOTP generation and form submission,
      long enough to cross a window boundary
- [ ] Two concurrent login attempts for the same account within the same window

### 2. DOM / selector integrity
- [ ] Selector lookup against a fixture with a deliberately renamed element
      (simulates a Kite UI update) — should raise a clear, specific error,
      not a generic timeout
- [ ] Element present in DOM but `disabled` / not yet interactable — confirm
      the code waits for actionability, not just presence
- [ ] Fixture representing an unexpected intermediate screen (CAPTCHA / new
      device prompt) — confirm the SDK fails loudly and identifiably rather
      than timing out silently or mis-clicking

### 3. Account safety / lockout prevention
- [ ] Simulate 3 consecutive wrong-TOTP responses — confirm a circuit breaker
      stops further attempts before hitting Kite's real lockout threshold
- [ ] Confirm there's a hard cap on retries per time window, independent of
      caller behavior (defense against a bug in calling code, not just this
      SDK's own retry logic)
- [ ] Headless-detection surface check: does the automated browser expose
      `navigator.webdriver` or other fingerprints a real user's browser
      wouldn't?

### 4. Credentials / config
- [ ] Malformed base32 TOTP secret — should fail at config-load time with a
      specific error, not produce a silently-wrong code
- [ ] Empty/missing env var — fails fast with a named variable, not a generic
      KeyError three layers down
- [ ] Password containing special characters that could break naive form-fill
      (quotes, unicode)

### 5. State / idempotency
- [ ] Login called when a valid session token already exists — confirm no
      redundant re-login, or that re-login is explicit and intentional
- [ ] Simulated crash between username-submit and TOTP-submit, then retry —
      confirm no double-submission or corrupted state

### 6. Infra / network
- [ ] Kite endpoint returns 503 — confirm distinguishable from a real
      credential failure
- [ ] Connection hangs (no response, no error) — confirm timeout fires and
      is distinguishable from "still working"
- [ ] DNS/SSL failure — confirm clear error surfaces, not a stack trace dump

---

## Phase 2 — Mutation testing pass
After Phase 1 tests are wired against the real implementation:
1. Introduce a real bug (e.g., off-by-one on the TOTP window check, or remove
   a null-guard) into a throwaway branch.
2. Run the full suite.
3. If nothing fails, the corresponding test is confirmatory, not adversarial
   — rewrite it.
4. Repeat for at least one bug per category above.

## Phase 3 — Adversarial review pass
Open a *fresh* Claude Code session. Give it only this spec file (not the
implementation, not the Phase 1 tests). Ask it: "What failure mode here
would still slip through if you were the implementer trying to make tests
pass with minimum effort?" Compare its answers against what Phase 1 covers.
Add gaps.

## Phase 4 — CI wiring
- Fixtures committed, no live Kite calls in CI by default
- A separate, manually-triggered "live" test tier that runs against a real
  sandbox/test account, rate-limited and circuit-broken, for the timing and
  lockout tests that can't be fully faked

---

## Exit criteria
This plan is "done" not when coverage hits a number, but when:
- Every category above has at least one test that fails against a
  deliberately broken implementation (Phase 2 proof)
- The adversarial review pass (Phase 3) surfaces no new categories after two
  rounds
