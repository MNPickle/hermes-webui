# Instructions

- Always use English for comments and code.
- Touch only the minimum code required to implement the new functionality.
- This folder and this document exist only for this branch.
- Do not include this folder in the final PR to `main`.
- allways activate el venv with uv

# Goal

    "accessibility.voice.speechLanguage": "es-ES",
    "workbench.settings.applyToAllProfiles": [
        "accessibility.voice.speechLanguage"
    ],

Make thSpanish Language Pack for Visual Studio CodSpanish Language Pack for Visual Studio Codive Hermes profile visible in the key places of the portal UI, so the user can always understand which profile the portal is using.


# Current Situation

The portal already supports selecting a runtime profile from Settings.

What is missing is visibility. After the user selects a profile, the active profile is not shown consistently across the screens that depend on it. This creates confusion because the selected profile changes:

- Hermes home
- config and env sources
- gateway actions and status
- API server target
- provider/model resolution


# UX Principle

The active profile should be visible anywhere the profile has direct operational impact.

The UI should not spam the same badge everywhere. Instead, it should surface the profile in the places where the user is making decisions or reading runtime state.


# Recommended UI Surfaces

## 1. Global header or persistent status area

Add a small, always-visible profile indicator in a stable global location.

Recommended location:

- top bar actions area, or
- sidebar footer near gateway status

Recommended content:

- label: `Profile`
- value: active profile name

Why:

- gives immediate context from any screen
- removes ambiguity when navigating away from Settings


## 2. Dashboard

Show the active profile in the runtime summary area.

Recommended places:

- System Info card
- or a dedicated small stat card near Gateway Status

Recommended content:

- Active Profile
- Hermes Home
- API Server

Why:

- Dashboard is the first place users inspect runtime state
- profile affects all those values directly


## 3. Service screen

Show the active profile next to the gateway runtime information.

Recommended content:

- Active Profile
- Hermes Home
- Hermes Binary

Why:

- Start, stop, restart, and diagnostics are profile-sensitive operations
- users need to know which profile's gateway they are controlling


## 4. Settings screen

Keep the existing profile selector card and strengthen it as the canonical source of truth.

Recommended improvements:

- keep selected profile clearly labeled
- show a short explanation that the selected profile affects gateway, env, config, and CLI chat execution
- make sure the card appears above the settings tabs

Why:

- this is the screen where the user changes profile
- this should remain the authoritative control surface


## 5. Providers / Models area

Show the active profile where provider and API target data are displayed.

Recommended places:

- Providers screen summary cards
- Model roles screen or modal header

Recommended content:

- Active Profile
- current API server URL when relevant

Why:

- users may assume provider configuration is global
- this area is one of the most likely places for confusion when switching profiles


## 6. Chat screen

Show the active profile in the chat runtime area, but keep it lightweight.

Recommended places:

- session status strip
- chat settings panel
- a small runtime badge near transport or capability state

Why:

- the selected profile changes the effective environment behind chat
- this is especially important when comparing behavior across profiles


## 7. Chat history structure

Do not make `Profile` the primary root structure of the history tree.

Recommended structure:

- keep `Folders / Projects` as the main navigation structure
- keep chats listed inside their current folder or ungrouped area
- treat `Profile` as visible runtime metadata, not the main container hierarchy

Recommended visibility rules:

- show the active portal profile in the history header as a compact badge
- show the session profile on each chat row as a compact badge
- allow filtering the visible history by profile when needed
- only consider profile sub-grouping inside a folder if that folder actually mixes multiple profiles and the grouping improves clarity

Not recommended as the default design:

- root grouping by `Profile -> Projects`
- root grouping by `Projects -> Profile` for every folder by default

Why this structure is preferred:

- users usually search for chats by project, folder, or title first
- profile is an execution/runtime dimension, not the primary subject of the conversation
- grouping the whole history tree by profile would duplicate navigation paths and make the sidebar heavier
- forcing every project to show profile sub-groups would add noise even when the project only uses one profile

Practical UI rule:

- `Project / Folder` answers: what this work belongs to
- `Profile` answers: which Hermes runtime executed or backed this chat

Recommended implementation order for history UX:

1. show clear profile badges on each chat row
2. show the active portal profile in the history header
3. add a simple profile filter (`All`, `Current`, specific profiles)
4. only add per-folder profile grouping later if real usage shows it helps


## 8. Profile switching inside the same chat

This should be supported, but not as a silent replacement of the chat's runtime.

Recommended product direction:

- keep one visible chat thread
- allow the user to switch profile for the next turns
- record that switch as a new runtime segment inside the same chat
- keep each segment clearly labeled with the profile and transport used for that part of the conversation

Why this matters:

- switching profile may also change the effective model, API target, env vars, gateway context, and skills
- a silent runtime switch would make the chat history misleading
- Hermes CLI continuity may not be safely reusable across profile boundaries

Recommended UX for phase 1:

- add an explicit in-chat action such as `Switch profile for next turns`
- when the user changes profile inside the chat, create a new numbered segment inside the same conversation
- each segment should show at least:
	- segment number
	- profile
	- transport
- the chat remains visually one conversation, but the runtime segments are visible and selectable

Suggested mental model:

- `Chat` = one user conversation
- `Segment 1 / 2 / 3` = different runtime phases inside that same conversation
- each segment belongs to the profile active when that segment started

This matches the intended workflow:


## Phase 1 Status

Phase 1 is now implemented for the active CLI chat path and validated in the branch.

Implemented:

- the chat UI can switch `Profile` for the next turns inside the same visible conversation
- each in-chat profile switch creates a new runtime segment in the same chat
- segment metadata is stored in the backend and exposed to the frontend
- the transcript shows runtime segment boundaries with profile and transport labels
- chat history and sidebar rows show the profile or profiles used by each chat
- new chats can choose a local draft profile without mutating the global portal profile
- switching profile inside a started chat is session-local and does not write the global web UI profile state
- Hermes CLI continuity is stored per segment/profile so returning to a previously used profile can resume that profile's own Hermes session

Validated behavior:

- global portal profile switching still works independently
- a chat-local profile switch affects the next turn in that chat
- chat-local switching does not write `run/webui_profile`
- returning to a previously used profile in the same chat resumes that profile's own Hermes CLI continuity
- the history filter remains available in expanded history, but is hidden when history is collapsed


## Remaining Gap Before Phase 2

The current CLI implementation now provides safe profile isolation and profile-specific continuity.

What still remains before Phase 2:

- validate the remaining profile visibility surfaces visually for `default` and `leire`
- keep this branch-local planning folder out of the final upstream PR

Current CLI rule:

- Hermes continuity is isolated across profile boundaries
- returning to a previously used profile resumes that profile's own prior Hermes session when available


## Phase 1 Completion Update

The remaining API transport and Providers / Models gaps have now been implemented in this branch.

Implemented after the initial Phase 1 slice:

- API replay is scoped to the active runtime segment instead of replaying messages across profile boundaries
- API image-history decisions are scoped to the active segment
- API chat authentication now uses the configured gateway URL and the token stored for that gateway port
- Settings can read and save API server tokens per selected profile while persisting them in the Web UI `.env` by gateway port
- Providers shows the active portal profile, Hermes home, gateway status, and API server context
- Models shows the active portal profile, Hermes home, gateway status, and API server context

What still remains before upstreaming:

- visual validation for `default` and `leire`
- exclude this branch-local `profiles_use/` folder from the final PR to `main`
- one profile never inherits another profile's Hermes continuity

Practical implication:

- Phase 1 is complete for the CLI path now used in practice
- API replay alignment remains a follow-up before Phase 2


## Transport API Note

`transport api` is not the active user path right now because the chat UI is effectively being used with `Auto` and `CLI`.

Even so, the design rule should match the CLI rule:

- an API-backed chat segment must not replay messages from a different profile segment into the current profile
- if API replay is enabled later, it should be segment-aware and only replay the messages that belong to the active segment or another explicitly defined safe boundary

Recommended scope decision:

- do not jump to multi-profile compare mode yet
- first finish API replay boundary semantics for segmented chats
- after that, bring `transport api` to the same segment boundary semantics


## Recommended Next Step

Before Phase 2, close the remaining transport and visibility work:

1. make API replay follow the same segment boundary rule when API transport becomes selectable
2. define whether returning to an earlier profile in API mode should replay only that segment lineage or only the active segment from its boundary forward
3. finish active profile visibility in Providers / Models

After that is stable, Phase 2 can focus on intentional compare workflows instead of fixing continuity semantics.


## Agreed Execution Order

The next work on this branch should follow this order:

1. align `transport api` with segmented profile boundaries
2. after that, finish active profile visibility in Providers / Models

This keeps the chat model coherent before adding more surface-level visibility work.

- keep the same conversation
- switch from one profile to another
- continue in a second stage without pretending it is the exact same runtime context


## 9. Future comparison mode between profiles

After phase 1 is stable, a second phase can add side-by-side comparison between profiles.

Recommended direction:

- allow sending the same prompt to multiple selected profiles from the same comparison view
- render results in parallel columns, one column per profile
- keep the compared profiles clearly labeled at the top of each column

Suggested use cases:

- compare how `default` and `leire` answer the same question
- compare different model and runtime behavior across profiles
- evaluate which profile is better for a given task without leaving the portal

Recommended constraints for phase 2:

- treat comparison mode as a dedicated chat mode, not a hidden behavior of the normal chat
- keep normal single-thread chat simple
- do not merge comparison results back into a normal runtime segment automatically

Recommended interaction model:

1. user opens comparison mode
2. user selects two or more profiles
3. user sends one prompt
4. each profile responds in its own column
5. the user can inspect differences before continuing normal chat work

Why this should be phase 2 instead of phase 1:

- it is a larger UX and state-management feature
- it introduces multi-target execution instead of single-target runtime continuity
- it is valuable, but should come after the single-chat segmented runtime model is clear and stable


# Minimal Implementation Plan

## Phase 1: Reuse existing runtime profile API

Do not add a new backend feature unless strictly needed.

Use the existing runtime profile data already exposed by:

- `GET /api/runtime/profiles`
- `GET /api/health`
- `GET /api/chat/status`

Primary implementation direction:

- rely on `profile` from runtime/status endpoints where already present
- only add fields if a specific screen cannot render the profile with current data


## Phase 2: Add one reusable frontend renderer

Create one small shared helper in `static/app.js` to render profile UI consistently.

Examples:

- compact profile badge
- profile info row
- profile summary card snippet

Why:

- keeps changes small
- prevents inconsistent labels across screens
- makes later profile-related improvements cheaper


## Phase 3: Add profile visibility to the highest-value screens first

Implement in this order:

1. global persistent indicator
2. Dashboard
3. Service
4. Providers / Models
5. Chat

Reason for this order:

- highest runtime clarity first
- smallest user confusion reduction per edit
- minimal code spread early


# Current Implementation Progress

The branch now includes the following implemented surfaces:

- persistent active profile indicators in the top bar and sidebar footer
- active profile shown in Dashboard runtime information
- active profile shown in Service runtime information
- active profile shown in the chat session banner
- active profile shown in the chat thinking state
- chat history now distinguishes between the active portal profile and the profile used by each saved chat session

Important clarification already implemented in Chat:

- `Portal: <profile>` means the profile currently selected for the web UI runtime
- `Session: <profile>` means the profile that specific chat session used when it was created or last persisted

This distinction is necessary because a user can switch the active portal profile after creating older chats, and those older chats should still show their own session profile instead of silently changing labels.


# Current Validation Status

The branch has already been validated for these scenarios:

- switching the active profile updates the global profile indicator
- Dashboard and Service show the selected runtime profile
- new chats show the active profile in the chat banner
- chat history keeps the session profile visible even after switching the active portal profile

Additional branch-only testing helper currently present:

- the web UI can bootstrap the token from `?token=...` in the URL for easier testing in simple embedded browsers

This helper is meant to reduce friction during testing. A proper login screen is still planned later and is outside the current profile-visibility scope.


## Phase 4: Keep scope tight

Do not redesign the full UI.

Do not add profile display to every card or table.

Only show the profile where it improves operational clarity.


# Concrete Acceptance Criteria

The implementation should be considered complete when:

- the active profile is visible from any screen without going back to Settings
- Dashboard clearly shows which profile is active
- Service clearly shows which profile's gateway is being controlled
- Settings continues to provide profile switching and remains the canonical edit point
- Providers or Models clearly indicate which active profile the shown runtime data belongs to
- Chat exposes the active profile in a lightweight but visible way


# Suggested Validation

After implementation, validate these scenarios manually:

1. Switch from `default` to `leire` in Settings.
2. Confirm the visible profile indicator updates immediately.
3. Confirm Dashboard shows the new profile and matching Hermes home.
4. Confirm Service shows the same profile and corresponding gateway status.
5. Confirm Providers / Models reflect the expected API target for that profile.
6. Confirm Chat shows the same selected profile.
7. Switch back to `default` and verify all visible profile indicators update consistently.


# Implementation Notes

- Prefer existing runtime payloads over adding new backend logic.
- Avoid duplicating profile lookup logic in multiple frontend screens.
- Keep labels simple and explicit: `Profile`, `Active Profile`, `Hermes Home`, `API Server`.
- Do not modify unrelated screens or styling beyond what is needed for this feature.



