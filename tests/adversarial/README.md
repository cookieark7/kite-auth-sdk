# Adversarial test suite (kite_sdk_test_plan.md)

These tests were skeletoned **implementation-blind**, purely from
`kite_sdk_test_plan.md`, per its ground rule. Each test states its assertion
intent in the docstring.

Rules for anyone touching these files:

1. **Never adjust a test to make it pass.** A failing test that reveals a real
   gap in the SDK is the desired outcome. Fix the SDK or record the gap — do
   not weaken the assertion.
2. Wiring a skeleton to the real implementation may change *how* the test
   arranges its scenario, but must not change *what* it asserts.
3. Fixtures must be recorded reality (real captured HTML / network traces),
   not copies of the implementation's own mocks. Fixtures live in
   `tests/adversarial/fixtures/`.
4. No live Kite calls. The `.env` in this repo points at a real account;
   a live login risks a 2FA lockout. Live-tier tests (Phase 4) are deferred
   until a sandbox account exists.

Status legend: tests still marked `pytest.mark.skip(reason="Phase 1 skeleton...")`
have not been wired to the implementation yet.
