# Hermes WebUI - Production Readiness Project

## Project

**Repo:** hermes-webui  
**Goal:** Apply production-readiness audit in small, safe, minimal patches

## Working Rules

- Work only inside this repo
- Keep patches minimal and localized
- Do not rewrite architecture
- Do not add unrelated cleanup
- One fix batch at a time
- After each batch, always report:
  1. Files changed
  2. Exact code changes
  3. Why the change fixes the issue
  4. Assumptions or remaining risks
  5. Manual test checklist
  6. Unified diff

## Completed

### Phase 1: Security & Infrastructure
- [x] **Fix 1:** Create requirements.txt
- [x] **Fix 2:** Authentication (token-based, all API routes)
- [x] **Fix 3:** XSS hardening in chat rendering
- [x] **Fix 4:** Path traversal hardening for uploads

### Phase 2: Stability
- [x] **Fix 5:** Production server / startup hardening (gunicorn)
- [x] **Fix 6:** Basic structured logging
- [x] **Fix 7:** Simple in-memory rate limiting

### Phase 3: UX/Feature Improvements
- [x] **Fix 8:** Remove non-functional Plugins menu item
- [x] **Fix 9:** Remove non-functional model selector from chat
- [x] **Fix 10:** Remove non-functional global search bar
- [x] **Fix 11:** Remove non-functional chat mode tabs
- [x] **Fix 12:** Frontend authentication - token handling in browser

## Next

- [ ] Additional Phase 3 improvements (if needed)

## Pending Queue

- Future enhancements and features

---

## Exact Fix 1 Requirement

Create `requirements.txt` with:

```
flask>=3.0,<4.0
flask-cors>=4.0,<5.0
python-dotenv>=1.0,<2.0
pyyaml>=6.0,<7.0
gunicorn>=21.0,<23.0
```

Also include any directly imported dependency already used in code if missing.

**Do not change any other file for Fix 1.**

---

## Session Start Instruction

Read this file first. Then implement only the next unchecked item.
