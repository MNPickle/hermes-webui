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

- [x] **Fix 3:** XSS hardening in chat rendering
- [x] **Fix 4:** Path traversal hardening for uploads
- [x] **Fix 1:** Create requirements.txt
- [x] **Fix 2:** Authentication
- [x] **Fix 5:** Production server / startup hardening
- [x] **Fix 6:** Basic structured logging

## Next

- [ ] **Phase 2:** Stability fixes (remaining)

## Pending Queue

- Phase 3: UX/features

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
