# Design System

## Intent

Discord Pals uses a restrained product-dashboard design system optimized for speed, clarity, and approachable administration. The dashboard should help users reach bot status, configuration, memory, channels, logs, providers, updates, and restart controls quickly without feeling claustrophobic.

## Themes

The dashboard supports dark and light themes. Default dark mode works well for users managing bots alongside Discord, terminals, and editors. Light mode should remain fully usable for daytime setup and less technical users.

## Color

Use a restrained product palette with one dependable accent. Color should clarify state and action, not decorate inactive surfaces.

- Backgrounds: layered neutral surfaces with enough contrast between page, panels, inputs, and tables.
- Accent: blue for primary actions, selected tabs, focused controls, and links.
- Success, warning, danger: semantic use only for health, caution, destructive actions, and error feedback.
- Avoid neon, high-saturation inactive states, and Discord-clone visual noise.

When touching tokens, prefer OKLCH-ready/tinted neutrals over absolute black/white, but do not let palette work block performance fixes.

## Typography

Use Inter or the system UI font stack. Keep text legible and compact without becoming cramped.

- Page title: clear, semibold, around 1.75rem to 1.875rem.
- Section title: 1rem to 1.125rem, semibold.
- Body and controls: 0.8125rem to 0.875rem.
- Metadata and badges: 0.6875rem to 0.75rem.
- Monospace: logs, JSON, prompts, code-like IDs, and diagnostics only.

## Layout

Use predictable product layout patterns: top navigation, page headers, tabs, filters, tables, cards/panels, and inline forms. Keep enough spacing to avoid claustrophobia, especially on configuration-heavy pages.

Guidelines:

- Prioritize common workflows above advanced/raw controls.
- Let advanced sections, raw JSON, and large editors stay collapsed or lazy until needed.
- Keep tables responsive with horizontal scroll when necessary.
- On mobile, stack controls cleanly and avoid forcing dense multi-column grids.
- Avoid nested cards unless each level has a clear job.

## Components

- Navigation: obvious active page state and compact labels.
- Tabs: keyboard-friendly buttons with active state and deferred panel hydration where useful.
- Buttons: consistent vocabulary for primary, secondary/ghost, danger, loading, and disabled states.
- Forms: clear labels, hints where helpful, no hidden save behavior.
- Tables/lists: fast filtering/sorting, incremental rendering for large collections, empty states that explain the next step.
- Textareas/editors: large prompt and JSON editors should not populate/parse until visible or explicitly opened.
- Toasts: brief success/error feedback, with inline state for actions that need persistence.
- Modals: only for destructive confirmation or focused edit flows where inline editing is riskier.

## Motion

Motion should communicate state and stay short. Prefer 120-180ms transitions, no page-load choreography, no layout-heavy animations, and no animation that makes tab switching or form interaction feel slower. Respect `prefers-reduced-motion`; snappiness beats animation polish.

## Performance Rules

1. Do not load multi-megabyte images for favicons, nav icons, or hover previews.
2. Avoid external dependencies on the critical path when native/browser features are enough.
3. Defer inactive tab work, large editor hydration, provider list parsing, and polling until needed.
4. Pause polling when the browser tab is hidden and avoid duplicate intervals when visibility changes.
5. Debounce search and filter-triggered network calls.
6. Render large lists in chunks or pages instead of rebuilding all DOM on every interaction.
7. Keep dashboard routes lightweight on the server: cache topology, avoid repeated file reads where possible, and separate initial HTML from heavy API data.
