# Ramon UI — Design Reference Notes

Source: `design/ramon-ui/` (tokens.css, components.html, index.html, patient-login.html, admin-login.html).
Static, standalone HTML/CSS reference for "Ramon" (המרכז הרפואי רמון — Ramon Medical Center), Hebrew/RTL. No build step, no framework. This file summarizes it for a React port; it does not modify the source files.

## 1. Global setup

**Language/direction.** Every page: `<html lang="he" dir="rtl" data-theme="light">`. RTL is done entirely with CSS logical properties (`inset-inline-start`, `border-inline-end`, `padding-inline`, margin-inline-start, etc.) — no `left`/`right` physical properties in the custom CSS. Per `index.html`'s own notes: switching to English means flipping `dir` to `ltr`; the logical-property layout re-flows itself. `dir="ltr"` is applied locally to phone/OTP inputs and numeric spans (`.num`, `.cc`) since digits/telephone numbers read LTR even in an RTL page.

**Fonts.** Loaded via Google Fonts, referenced in every page's `<head>`:
```html
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Hebrew:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
```
Two families, declared as CSS variables in `tokens.css`:
- `--font-sans: "IBM Plex Sans Hebrew", "Segoe UI", Arial, sans-serif;` — all UI text.
- `--font-mono: "IBM Plex Mono", "Courier New", monospace;` — phone country code, OTP digits, case/state IDs, audit references.

Per `index.html`'s own note: "without internet, typography falls back to the default font and everything else still works" — the fonts are a progressive enhancement, not a hard dependency.

**Theme switching.** Light/dark is driven by `data-theme` on `<html>`:
- `:root, [data-theme="light"]` — light palette (also the implicit default).
- `[data-theme="dark"]` — dark palette.
- `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { ... } }` — if no explicit `data-theme` is set, dark palette is used when the OS prefers dark. So: explicit `data-theme` always wins; absent it, OS preference decides.

Each of the 4 HTML pages (not `tokens.css` itself, which is theme-agnostic and only holds the variables) also includes a small floating **theme toggle button** (`.theme-toggle`, `id="themeBtn"`, fixed top-inline-start corner) with inline JS:
```js
(function () {
  var b = document.getElementById('themeBtn'), r = document.documentElement;
  function label() { b.textContent = r.getAttribute('data-theme') === 'dark' ? 'מצב בהיר' : 'מצב כהה'; }
  r.setAttribute('data-theme', window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  label();
  b.addEventListener('click', function () {
    r.setAttribute('data-theme', r.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    label();
  });
})();
```
Button label toggles between "מצב כהה" (dark mode) / "מצב בהיר" (light mode). This is demo-only chrome — in the product this would be a real settings control, not a floating pill.

**Embedding note.** Every page (components.html, index.html, patient-login.html, admin-login.html) inlines the *entire* `tokens.css` contents in a `<style>` block so each file is self-contained/openable standalone. `index.html` explicitly documents this: "in a real project, delete the inlined block and link to one file" (it itself links `tokens.css` externally rather than inlining it, unlike the other three).

## 2. Design tokens (`tokens.css`)

Comment at top (Hebrew): "Generated from the design system's tokens.json. Do not edit by hand." — i.e. `tokens.css` is itself a generated artifact upstream of this repo; treat as source of truth for values, not hand-tuned.

### Token groups

**Surfaces** (backgrounds, light → dark values differ but role stays the same):
`--surface-000` (page/card white), `--surface-100` (app background), `--surface-200` (input fill / hover fill), `--surface-300` (subtle fill, e.g. unfilled progress dot), `--surface-brand` (brand-colored panel background).

**Ink (text)**: `--ink-900` (headings/strong text), `--ink-700` (body text), `--ink-500` (secondary/muted text), `--ink-000` (inverse — white in light mode), `--on-brand` (text color on brand-filled surfaces).

**Brand**: `--brand-mist` (light tint, used for text-on-brand-panel secondary), `--brand-700`, `--brand-600`, `--brand-500` (darkest→lightest / primary button colors), `--brand-100` (very light tint, badge backgrounds).

**Lines**: `--line-200` (subtle borders/dividers), `--line-300` (stronger borders, input/button outlines).

**Interaction/status**: `--focus-500` (focus ring color), `--teal-500`/`--teal-100` (secondary accent, "live" indicators, logo accent), `--success-500`, `--warning-500`, `--danger-500` (semantic states), `--scrim` (modal backdrop overlay color).

**Elevation**: `--shadow-card`, `--shadow-modal` (multi-layer box-shadow values, different intensity per theme).

**Spacing scale**: `--space-1` 4px … `--space-8` 64px (4, 8, 12, 16, 24, 32, 48, 64 — roughly a 4/8px doubling scale, steps 1–8).

**Radii**: `--radius-sm` 6px, `--radius-md` 10px, `--radius-lg` 18px, `--radius-pill` 999px.

**Opacity**: `--opacity-disabled` 0.45, `--opacity-muted` 0.7.

**Fonts**: `--font-sans`, `--font-mono` (see §1).

### Typography utility classes

Defined in `tokens.css` as ready-to-use classes (not just raw tokens). All use `--font-sans` unless noted:

| Class | Size / line-height | Weight | Notes |
|---|---|---|---|
| `.display-lg` | 34px / 40px | 600 | letter-spacing -0.01em; page H1 |
| `.display-sm` | 26px / 32px | 600 | |
| `.heading-md` | 19px / 26px | 600 | section headings |
| `.heading-sm` | 16px / 22px | 600 | card/subsection headings |
| `.body` | 15px / 24px | 400 | default paragraph text |
| `.body-strong` | 15px / 24px | 600 | |
| `.small` | 13px / 20px | 400 | hints, secondary copy |
| `.label` | 13px / 18px | 600 | letter-spacing 0.02em; form field labels |
| `.eyebrow` | 11px / 16px | 600 | letter-spacing 0.09em; small caps-style kicker text |
| `.otp` | 26px / 30px | 500 | **font-mono**; letter-spacing 0.02em; OTP digit display |
| `.code` | 13px / 20px | 400 | **font-mono**; inline code/IDs |
| `.state` | 11px / 16px | 500 | **font-mono**; letter-spacing 0.06em; state/status codes |

Note: on the actual pages, most on-page text does NOT use these class names directly — instead each page's own `<style>` block re-declares equivalent rules against page-scoped classes (e.g. `.h1`, `.lede`, `.hint`) using the same px/weight values. Treat the table above as the canonical scale to reproduce as React design tokens / a typography stylesheet, and treat the per-page classes as usage call sites.

## 3. Components (`components.html`)

`components.html` is a live style-guide page: sections `לוגו (Logo)`, `כפתור (Button)`, `שדה קלט (TextField)`, `קוד חד-פעמי (OtpInput)`, `הודעת מערכת (Alert)`, each rendered inside a scoped wrapper `<div id="c-<Name>">` whose CSS is scoped with the `#c-<Name> .class` prefix (this scoping is a components.html-only convention for the style-guide; the real pages use unprefixed classes under `#root`).

### Logo (`#c-Logo`)
Three variants of a brand lockup: full (icon + org name + product name stacked), compact (icon + short "רמון" label, separated by an inline-start border — used when space is tight), and "onbrand" (white icon disc + white/inverse text, for placing on a brand-colored panel). Icon is an inline SVG (see §4).
```html
<div class="lock">
  <svg class="mark" width="40" height="40" viewBox="0 0 40 40" aria-hidden="true">...</svg>
  <span class="words">
    <span class="org">המרכז הרפואי רמון</span>
    <span class="product">סוכן פניות מטופלים</span>
  </span>
</div>
```

### Button (`.btn`)
Base class `.btn` + one variant class:
- `.primary` — filled brand-700, `--on-brand` text; hover → brand-600.
- `.secondary` — transparent, brand-700 text, `--line-300` border; hover → surface-200 fill.
- `.quiet` — transparent, brand-700 text, no border; hover → surface-200 fill.
- `.danger` — transparent, danger-500 text + border; hover → surface-200 fill.
States: `:disabled` (opacity `--opacity-disabled`, `cursor: not-allowed`), `.focused` (manual focus-ring demo class, mirrors `:focus-visible`), `.busy[aria-busy="true"]` (shows a `.spin` spinner — 15px circle, 2px border, `currentColor`, animated rotation, respects `prefers-reduced-motion: reduce`), `.block` (full-width grid span, `grid-column: 1/-1`).
```html
<button type="button" class="btn primary busy" aria-busy="true">
  <span class="spin" aria-hidden="true"></span>מאמת קוד
</button>
```

### TextField (`.field` / `.control` / `.input`)
Structure: `.field` (label + control + hint stack) → `.label` → `.control` (bordered box, can contain a `.prefix`) → `.input` (bare input, no own border).
```html
<div class="field">
  <label class="label" for="tf-phone">מספר טלפון נייד</label>
  <div class="control phone">
    <span class="prefix" aria-hidden="true">
      <svg>...</svg><span class="cc">972+</span>
    </span>
    <input id="tf-phone" class="input" type="tel" inputmode="tel" dir="ltr" value="052-000-0000" autocomplete="tel">
  </div>
  <p class="hint">נשלח קוד חד-פעמי למספר זה.</p>
</div>
```
States: `.control:focus-within` / `.focused` → focus ring; `.control.invalid` → danger border + inset shadow, paired with `.hint.error` (danger-colored, with warning icon, `aria-describedby` linking input↔message, input gets `aria-invalid="true"`); `.control.disabled` → dimmed, input has native `disabled` (used for read-only fields like masked ID number `03•••••9`).

### OtpInput (`.otp` / `.box`)
`role="group" aria-label="קוד אימות בן שש ספרות"` wrapping 6 `input.box` (single-digit, `inputmode="numeric"`, `maxlength="1"`, each with its own `aria-label="ספרה N"`, first one carries `autocomplete="one-time-code"`). `dir="ltr"` on the group so digit order reads left→right even in RTL. Box: 46×56px (54×62 on the login pages), mono font, 26px/30/500.
States: `.box.active`/`:focus` → focus ring + brand border; `.otp.invalid .box` → danger border, paired with `.hint.err`; `.otp.valid .box` → success border+text, boxes become `readonly`, paired with `.hint.ok` (checkmark icon). Responsive: `@media max-width:560px` shrinks boxes to 40×50/22px.

### Alert (`.alert`)
```html
<div class="alert info" role="status">
  <svg class="ico" ...></svg>
  <div class="body">
    <p class="title">שלחנו קוד ל-052-000-0000</p>
    <p class="text">הקוד תקף לחמש דקות. אפשר לבקש קוד חדש בעוד 00:42.</p>
  </div>
</div>
```
Variants (icon/border color only, body structure identical): `.info` (brand icon), `.warn` (warning-color icon, triangle glyph), `.error` (danger border + icon, `role="alert"`), `.ok` (success icon, checkmark). Optional `.mono` span inside `.text` for inline monospace fragments (state codes like `AWAITING_HUMAN_REVIEW`, case IDs like `case_8f21c4`).

### Not present in components.html
No table, tabs, modal, or nav/header component sheet exists in this file — components.html only documents Logo/Button/TextField/OtpInput/Alert. Header/nav patterns can only be inferred from the login pages (§5), and no tables/tabs/modals appear anywhere in this design set.

## 4. Pages

All three pages share one layout skeleton: `#root` (padded outer wrapper, `--surface-100` background) → `.screen` (flex row, white card, `--radius-lg`, `--shadow-card`, `overflow:hidden`, `min-height:700px`) → a wide `.form-pane` + a narrower side panel. Below `900px` the row collapses to a column (`@media (max-width:900px)`).

### index.html — file index / landing page
Not part of the app itself — a directory page linking to the other three files. Structure: `.wrap` → `<h1>Ramon Patient Agent</h1>` + lede, then `.cards` grid of 3 `.card` links (Logo/copy/filename) to patient-login.html, admin-login.html, components.html, then two `<h2>` prose sections ("מה בכל קובץ" / "איך לחבר לקוד") explaining the file set, then a `.note` box. All styling is page-local (not reused elsewhere) — `.wrap`, `.card`, `.file`, `.note` are defined only in this file's inline `<style>`. This file is a build/reference artifact, not a screen to port to React.

### patient-login.html — patient sign-in
Two-step phone→OTP flow inside `.form-pane`, plus a decorative `.brand-pane` aside (58/42 split).

**Header** (`.top`): brand `.lock` (icon + org name) + `.lang` pill group (עב / EN toggle buttons, `role="group"`, `aria-pressed`).

**Step 1** (`#step1`): `.steps` progress indicator (2 `.dot`s, first `.on`, + `.steps-txt` "שלב 1 מתוך 2"), `<h1 class="h1">כניסה לפניות מטופלים</h1>`, lede "הזינו את מספר הטלפון הנייד הרשום בתיק שלכם. נשלח לכם קוד חד-פעמי לאימות.", a phone `.field` (972+ prefix, placeholder `050-000-0000`, hint "הקוד יישלח ב-SMS. עלולות לחול עלויות מפעיל."), primary button "שלחו לי קוד", legal line linking "תנאי השימוש" / "מדיניות הפרטיות", and a `.help` box: "אין לכם טלפון נייד רשום?" / call center number `*2700`, hours א׳–ה׳ 07:00–20:00.

**Step 2** (`#step2`, `hidden` by default): both dots `.on`, H1 "הזינו את הקוד שקיבלתם", lede shows the phone number back plus a "החלפת מספר" (change number) link-button, a 6-box OTP group, hint "הקוד תקף לחמש דקות", primary button "כניסה למערכת", a `.resend` row (disabled "שלחו קוד חדש" button + `.timer` "אפשר לבקש קוד חדש בעוד 00:42"), and an `.alert.error` demoing a wrong-code state ("הקוד שגוי" / "נשארו שני ניסיונות...").

**JS behaviour**: a single global `go(n)` function toggles the `hidden` attribute on `#step1`/`#step2` — called by `onclick="go(2)"` on the step-1 submit button and `onclick="go(1)"` on the "החלפת מספר" link. No validation logic; OTP digit values are hardcoded in the markup to show visual states, not wired to real input-advance-to-next-box behavior.

**Brand aside** (`.brand-pane`): full-bleed `--surface-brand` panel with an inline decorative SVG `.pattern` (abstract rounded rects/circle/checkmark path, low opacity) behind a `.brand-body`: `.eyebrow` "PATIENT SERVICES", `.brand-h` "פנייה אחת, מענה אחד,<br>בלי להמתין על הקו.", `.brand-p` descriptive copy about the agent, and a `.facts` list of 3 bullet claims (live dot + "זמין בכל שעה, כל ימות השבוע"; checkmarks + "כל פנייה רפואית עוברת אישור אדם" / "הפרטים נשמרים בתיק הרפואי בלבד"). Hidden on narrow viewports (`.facts{display:none}` under 900px).

### admin-login.html — staff (clinical) sign-in
Single-step form (no step JS) — phone + TOTP code together, 60/40 split with a static side panel instead of a brand panel.

**Header**: same `.top`/`.lock`/`.lang` pattern as patient-login.

**Main form**: `.badge` pill "STAFF ACCESS" (key icon + eyebrow-style text), H1 "כניסת צוות רפואי", lede "אזור זה מיועד למאשרי פניות רפואיות. אימות דו-שלבי חובה בכל כניסה.", a phone field (value prefilled `054-000-0000`, hint "המספר שנרשם על ידי מחלקת מערכות מידע. אינו ניתן לשינוי עצמי."), then a code field whose `.label-row` pairs the label "קוד מאפליקציית Microsoft Authenticator" with a `.ring-wrap` — an inline SVG countdown ring (`stroke-dasharray`/`dashoffset` arc) plus text "מתחלף בעוד 18 שנ׳" — above a 6-box OTP group, primary button "כניסה למערכת". Below the form: `.alert.warn` "ניסיון כניסה אחד נכשל" / "נשארו ארבעה ניסיונות לפני נעילה של רבע שעה..." referencing account `ramon-med`, and an `.audit` line (shield-check icon) "כל כניסה, אישור ודחייה נרשמים ביומן הביקורת עם מזהה המשתמש וחותמת זמן." (every login/approval/rejection is logged with user ID + timestamp — echoes the backend's audit-log design).

**Side panel** (`.side-pane`, light `--surface-100` background, static, no image): `<h2>` "איך מפיקים את הקוד" (how to get the code) + numbered `.steps-list` (3 steps: open Authenticator app → select `ramon-med` account → enter the 6 digits before they rotate, "הקוד מתחלף כל שלושים שניות"), a `.note` box "אין לכם גישה לאפליקציה?" pointing to IT extension `4400` (2FA reset requires in-person ID verification with department manager, not by phone), a `.scope` block "מה מותר מהמסך הזה" (what this screen permits) with a checklist: ✓ approve/reject/close escalated cases, ✓ view a case's audit log, ✕ edit medical record or documents — and a `.foot` legal/monitoring notice.

No page-specific `<style>` beyond what's described above (tokens, plus one `<style>` block per file that inlines tokens.css + page-scoped rules under `#root`/`.theme-toggle` — there's no *extra* one-off styling beyond the classes documented here).

## 5. Assets

- **Logo/icon**: no external image/icon files — the mark is an **inline SVG** (40×40 viewBox `0 0 40 40`): a rounded-rect badge (`fill: var(--brand-500)`), a teal quarter-circle accent (`fill: var(--teal-500)`), and a white "pulse/EKG-like" zigzag path (`stroke: #fff` or `var(--brand-700)` on the on-brand variant). Reused at different sizes (40px in components.html, 28px compact, 32px on login headers) — it's a single reusable SVG symbol, not a raster logo. Org name "המרכז הרפואי רמון" is a placeholder — `index.html` states explicitly: "the center's name and logo are a placeholder built for the design; replace them in the brand source file and the `--brand-*` values in tokens.css."
- **Other icons**: small inline SVGs throughout (phone glyph in `.prefix`, info/warning/error/success glyphs in `.alert .ico`, key glyph in the staff badge, shield-check in `.audit`, checkmark/circle bullets in `.facts`/`.scope-l`) — all hand-drawn `<svg>` with `currentColor`/CSS-variable strokes, no icon font or sprite sheet, no external icon library.
- **Decorative pattern**: one bespoke inline SVG (`.pattern`, viewBox `0 0 420 760`) of abstract rounded rectangles/circle/checkmark-path shapes on the patient-login brand panel — not reused elsewhere.
- **Countdown ring** on admin-login: inline SVG, two concentric circles (track + animated-via-dasharray progress arc) — static in this static HTML, would need to be driven by a real timer in React.
- **External URLs**: only Google Fonts (`fonts.gstatic.com` preconnect, `fonts.googleapis.com/css2?...` stylesheet for IBM Plex Sans Hebrew + IBM Plex Mono). No other external network dependency, no CDN JS, no analytics.

## 6. Hints about screens beyond these three pages

These are not built, but the copy/scope text implies them:
- **Patient dashboard / case status screen** — post-login destination implied by "כניסה למערכת"; would show case status, likely using the `.state`/`.code` mono tokens and `Alert` component (e.g. the `AWAITING_HUMAN_REVIEW` and `case_8f21c4` examples in `components.html`'s Alert section look like they're previewing this).
- **Document upload / missing-document request** — patient-login's brand panel says the agent "locates documents in your file"; matches the backend spec's missing-document flow.
- **Staff / clinical review queue** — admin-login's scope list ("approve, reject and close escalated cases"; "view a case's audit log") implies a queue-style screen listing escalated cases, a detail view with an audit-log/timeline view, and approve/reject actions — likely built from `Button` (`.primary`/`.danger`), `Alert`, and a not-yet-designed list/table + timeline component.
- **2FA/account-recovery flow for staff** — "אינו מתבצע בטלפון" (not done by phone) implies an in-person or ticket-based IT flow, out of scope for this UI.
- **Multi-language (EN) support** — the `.lang` toggle (עב/EN) on both login pages is present but non-functional (`aria-pressed` state only, no click handler) — implies a real i18n screen switch is planned but not built here.

## 7. How to port to React

- **Global CSS**: import `tokens.css` once at the app root (e.g. in a top-level `index.css`/`App.tsx` import) — it's pure `:root`/`[data-theme]` variables + typography utility classes, framework-agnostic. Do not re-inline it per page/component as the static files do (that duplication is explicitly a demo-only convenience, per `index.html`'s own note).
- **Fonts**: keep the two `<link>` tags (preconnect + stylesheet) in the document head (`index.html`/`_document`/root `<head>`), or self-host if offline reliability matters.
- **Theme**: reproduce the toggle logic as a small hook/context (e.g. `useTheme()`) that reads `prefers-color-scheme` for the initial value and sets `data-theme` on `document.documentElement`; persist the user's explicit choice (e.g. localStorage) rather than resetting to OS preference every load.
- **RTL/i18n**: keep `dir`/`lang` on `<html>` driven by the active locale; since all layout uses logical CSS properties already, a React i18n layer (e.g. react-i18next) just needs to flip `dir` — no per-component RTL overrides needed if new components also stick to logical properties.
- **Components to extract** (from `components.html` + patterns repeated across the login pages): `Logo` (prop: `variant: 'full' | 'compact' | 'onbrand'`), `Button` (prop: `variant: 'primary'|'secondary'|'quiet'|'danger'`, `busy`, `disabled`, `block`), `TextField` (label, prefix slot, hint, `invalid`/`disabled` state, error message), `OtpInput` (controlled 6-digit input, `invalid`/`valid`/`active` state — this one needs real focus-advance/backspace logic added, since the static version only fakes states via hardcoded `value`s), `Alert` (`variant: 'info'|'warn'|'error'|'ok'`, title+text+optional mono fragment), `LanguageSwitch` (the `.lang` pill), `StepIndicator` (the `.dot`/`.steps-txt` pair), and a shared `AuthLayout` (the `.screen`/`.form-pane`/side-or-brand-pane split with the `<900px` stacking breakpoint) that both login pages instantiate with different aside content.
- **Page-specific pieces stay page-specific**: patient-login's `.brand-pane` (SVG pattern + facts list) and admin-login's `.side-pane` (steps list + note + scope checklist) are one-off content blocks, not reusable components — build them as plain JSX inside each page/route, composed inside the shared `AuthLayout`.
- **Icons**: since every icon is a small hand-authored inline SVG with `currentColor`/CSS-variable strokes, either keep them as inline SVG React components (simplest, matches source exactly) or move to an icon library later — but preserve `aria-hidden="true"` on decorative icons and `aria-label`/`aria-describedby` wiring on meaningful ones (already correctly used throughout the source for the OTP boxes and error messages).
- **Behavior gaps to fill in React that the static demo fakes**: the `go(n)` step switch (replace with real component state / router step), the OTP resend countdown timer, the admin TOTP countdown ring, and the `.lang` toggle's actual locale switch — none of these have real logic in the HTML, only visual states.
