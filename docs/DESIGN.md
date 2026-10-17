# CodeRill site design

> A readable coding agent, presented as a small piece of well-made developer tooling.

## 1. Visual Theme & Atmosphere

Editorial developer documentation: warm paper, dark ink, precise rules, and a restrained green accent. The first screen explains the product; the sections below teach agent basics, trace one request, map the source, and show a verified local-checkout installation. Motion tier: L1 refined static. Dependency: CSS only.

## 2. Color Palette & Roles

`--bg: #f6f5f0` is the page background; `--surface: #ffffff` is the terminal and panels; `--surface-alt: #eef2ed` marks secondary sections; `--surface-hover: #e5ebe3` is the hover surface. `--border: #d8ddd5` and `--border-hover: #a6b9a7` separate content. `--text: #17251e`, `--text-secondary: #516057`, and `--text-tertiary: #68766d` form the text hierarchy. `--accent: #176a46`, `--accent-hover: #0d5133`, `--success: #176a46`, `--error: #b94037`, and `--warning: #9c6b18` are semantic colors. `--bg-rgb: 246,245,240` and `--accent-rgb: 23,106,70` support translucent treatments.

Use the accent for actions and small highlights. The terminal uses the dark text color as its background and light text for contrast.

## 3. Typography Rules

Use the system sans stack (`-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `Noto Sans SC`, sans-serif) for Chinese prose and the system monospace stack for commands. No network font request is needed. Hero heading: 64–88 px, weight 750, line height 1.12. Section heading: 34–48 px, weight 700. Body: 17 px, line height 1.75; labels: 13 px; code: 14 px. Headings have plain text treatment without gradient or shadow.

## 4. Component Stylings

Primary links are filled green pills with a darker hover state; secondary links are outlined pills. Both have visible focus outlines and a subtle pressed state. Feature panels have a single border and no nested cards. Navigation uses plain links with underline on hover and focus. Code is selectable and wraps on narrow screens. There are no disabled controls.

## 5. Layout Principles

Maximum content width: 1120 px; horizontal padding: 24 px desktop and 18 px mobile. Sections use 88–112 px vertical spacing on desktop and 64 px on mobile. The hero is a two-column grid; concept and architecture lists use a responsive grid, and the request trace is a readable numbered sequence. Text sections stay near 70 characters per line.

## 6. Depth & Elevation

Most surfaces are flat. The terminal gets a light offset shadow to anchor the hero. Borders, rather than repeated shadows, define the rest of the page.

## 7. Animation & Interaction

L1: a short fade and upward entrance for hero content, plus hover/focus transitions on links. Only opacity and transform animate. Reduced-motion preference removes the entrance animation and transitions.

## 8. Do's and Don'ts

Show real product behavior and the currently documented GitHub checkout installation path. Link module descriptions to actual source files. Do not claim that a PyPI release, live model benchmark, or command sandbox exists. Avoid decorative screenshots, fake usage statistics, and external assets.

## 9. Responsive Behavior

Below 760 px, stack the hero and capability list, keep the installation command readable with wrapping, and allow the header links to wrap. No horizontal overflow at 360 px. Keyboard focus remains visible at every width.
