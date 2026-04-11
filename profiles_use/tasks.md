# Profile Visibility Tasks

This document is branch-local working material for `improve-profiles`.

It exists to track the implementation work before the final upstream PR.

Do not include this folder in the final upstream PR.


## Rules

- Keep code comments and code in English.
- Touch only the minimum code required for the feature.
- Update this checklist as work progresses.
- Commit progress to this branch so the work remains traceable.


## Current Goal

Make the active Hermes profile visible in the important runtime areas of the portal UI so users always know which profile they are using.


## Branch Status

- Branch: `improve-profiles`
- Base: `main`
- Scope: branch-only planning and incremental implementation


## Backlog

- [x] Review the current UI surfaces and decide the minimum set of screens that should show the active profile.
- [x] Add one reusable frontend helper for rendering active profile information consistently.
- [x] Add a persistent active profile indicator in a global UI location.
- [x] Show the active profile in Dashboard runtime information.
- [x] Show the active profile in the Service screen next to gateway controls and runtime state.
- [ ] Show the active profile in Providers / Models where runtime target data is shown.
- [ ] Show the active profile in Chat in a lightweight but visible way.
- [ ] Validate profile switching visually for `default` and `leire`.
- [ ] Keep this tracking folder out of the final upstream PR.


## In Progress

- [ ] Implement active profile visibility in Providers / Models and Chat.


## Done

- [x] Create the `improve-profiles` branch.
- [x] Create a branch-local task tracker in `profiles_use/tasks.md`.
- [x] Document the high-level implementation plan in `profiles_use/plan.md`.
- [x] Add a reusable profile rendering helper in the frontend.
- [x] Add a persistent active profile indicator to the global UI.
- [x] Show the active profile in Dashboard.
- [x] Show the active profile in Service.


## Validation Checklist

- [ ] Switching the profile updates the persistent profile indicator.
- [ ] Dashboard shows the active profile and matching runtime paths.
- [ ] Service shows which profile's gateway is being controlled.
- [ ] Providers / Models show which profile the displayed runtime configuration belongs to.
- [ ] Chat shows the active profile without adding visual noise.
- [ ] The UI remains consistent after switching back and forth between `default` and `leire`.


## Notes

- The portal already supports runtime profile selection.
- This work is about visibility and clarity, not reworking the full profile system.
- Before the final upstream PR, remove or exclude this folder from the published diff.