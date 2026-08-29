# Browser Account Onboarding Reference Audit

Updated: 2026-08-25

## Decision

The first real seller account must enter the project through a two-stage
browser flow:

`dedicated browser login -> read-only identity preview -> explicit commit -> account record`

Opening a browser, seeing a homepage, having an old Cookie string, or passing a
local unit test is not account onboarding evidence. The accepted account ID is
the current platform `unb`, observed again at commit time from the same browser
session.

## Sources Reviewed

| Source | License/status | Useful design evidence | Adoption decision |
|---|---|---|---|
| [harringtonwalker/xianyu-automation](https://github.com/harringtonwalker/xianyu-automation) | Apache-2.0 | Uses current browser login state, separates review from execution, binds owner identity, requires strict receipts, and refuses blind retries after an uncertain submit. | Adopted the trust-boundary and two-stage operating ideas. No source code copied. |
| [shaxiu/XianyuAutoAgent `XianyuApis.py`](https://github.com/shaxiu/XianyuAutoAgent/blob/main/XianyuApis.py) | Repository code reviewed as protocol evidence only | Its login check posts to `passport.goofish.com/newlogin/hasLogin.do` using current session fields including `unb` and `cookie2`. | Adopted a bounded login probe inside the existing Playwright browser context. No protocol module or retry loop copied. |
| [johenking/xianyu-ai-manager](https://github.com/johenking/xianyu-ai-manager) | AGPL-3.0 | Describes separate QR/local-browser/extension/manual import paths, identity merge by real `unb`, revalidation before account success, and manual Cookie as an advanced fallback. | Adopted architecture descriptions only. No AGPL implementation copied or imported. |
| [11273/goofish-client](https://github.com/11273/goofish-client) | GPL-3.0, unofficial client | Confirms that login/authentication and profile identity are separate client capabilities and explicitly warns that unofficial contracts can change. | Not installed. No GPL code copied. Used only as corroborating architecture evidence. |

GitHub repositories are engineering references, not proof that a particular
seller account, platform endpoint, selector, fee, or commercial workflow works
today. Execution-day platform pages and this project's own receipts remain the
source of truth.

## Implemented Contract

### Open

`POST /accounts/browser-import/open` starts or reconnects the dedicated Edge
profile and opens the Goofish homepage. Its response contains only the loopback
debug address, start state, URL, and tab ID. It does not read, return, or persist
page text.

### Preview

`POST /accounts/browser-import/preview`

- requires the active `BrowserAdapter` and the project CDP browser;
- reads only cookies scoped to Goofish/Taobao account domains;
- requires `unb`, `cookie2`, and `_m_h5_tk`;
- performs the current login probe from the browser context;
- derives the account ID from `unb`;
- stores a five-minute local preview bound to an account/session fingerprint;
- returns no Cookie value.

### Commit

`POST /accounts/browser-import/commit`

- requires the exact `BIND <preview-prefix>` confirmation;
- recaptures the browser account and rejects account/session drift;
- creates or refreshes one exact account;
- preserves existing account controls on refresh;
- records authenticated runtime/cookie refresh evidence;
- returns only sanitized account and next-step state;
- makes a repeated network retry idempotent for five minutes.

### Credential Isolation

- The account database is the first credential source for a named account.
- A per-account local file is a recovery fallback.
- The global environment Cookie is accepted only for the legacy `default`
  account and cannot silently overwrite every named account.
- Switching account IDs resets the Playwright session before loading the new
  account.
- CDP loopback HTTP ignores environment proxy variables.

## Real Commercial Order

1. Isolate historical unassigned work.
2. Bind one real seller account through the two-stage browser flow.
3. Bootstrap account Prompt and inventory context.
4. Sync real listings and orders for the same account.
5. Verify runtime freshness and clear platform verification blockers.
6. Record current account/category terms and fees.
7. Evidence, score, and manually approve one supplier-backed candidate.
8. Obtain 20/20 Day-0 launch gates before creating one batch.

This phase deliberately stops before publishing. Account onboarding is an
identity and credential event, not commercial authorization and not a platform
side effect.
