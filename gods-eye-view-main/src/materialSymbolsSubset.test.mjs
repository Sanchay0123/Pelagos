import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC_ROOT = fileURLToPath(new URL('.', import.meta.url));
const REPO_ROOT = path.resolve(SRC_ROOT, '..');
const INDEX_HTML = path.join(REPO_ROOT, 'index.html');

/** The glyph written as element text: `<span class="material-symbols-outlined">radar</span>`. */
const SPAN_TEXT =
  /class="[^"]*material-symbols-outlined[^"]*"[^>]*>\s*([a-z0-9_]+)\s*</g;
/** A `.textContent =` or `.innerText =` assignment, capturing target expression and RHS. */
const TEXT_ASSIGNMENT =
  /([A-Za-z0-9_$.[\]()'"`-]+)\.(?:textContent|innerText)\s*=\s*([^;]{0,400})/gs;
const STRING_LITERAL = /['"`]([a-z0-9_]{2,})['"`]/g;
const HTML_ICON_TAG =
  /<[^>]*\bclass="[^"]*\bmaterial-symbols-outlined\b[^"]*"[^>]*>/g;
const HTML_ID_ATTR = /\bid="([A-Za-z0-9_-]+)"/;

/** Collect HTML element `id`s that carry the `material-symbols-outlined` class. */
function collectHtmlIconIds(files) {
  const ids = new Set();
  for (const file of files) {
    if (!file.endsWith('.html')) continue;
    const source = readFileSync(file, 'utf8');
    for (const tag of source.matchAll(HTML_ICON_TAG)) {
      const idMatch = HTML_ID_ATTR.exec(tag[0]);
      if (idMatch) ids.add(idMatch[1]);
    }
  }
  return ids;
}

/**
 * Identify variable/property bindings in `source` that hold an element with the
 * `material-symbols-outlined` class (either created/selected in JS or looked up
 * by an icon element `id` defined in HTML templates).
 */
function collectIconBindings(source, htmlIconIds) {
  const bindings = new Set();

  // `const icon = ...querySelector('.material-symbols-outlined')` or `this.foo = ...`
  const selectorBind =
    /(?:(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)|([A-Za-z_$][A-Za-z0-9_$.]*))\s*=\s*[^;]*\.querySelector(?:All)?\([^;]*material-symbols-outlined[^;]*\)/gs;
  for (const m of source.matchAll(selectorBind)) {
    bindings.add(m[1] || m[2]);
  }

  // `<target>.className = '... material-symbols-outlined ...'` or `.classList.add('material-symbols-outlined')`
  const classNameBind =
    /([A-Za-z_$][A-Za-z0-9_$.]*)\s*\.className\s*(?:\+?=)\s*[^;]*material-symbols-outlined/gs;
  for (const m of source.matchAll(classNameBind)) {
    bindings.add(m[1]);
  }
  const classListBind =
    /([A-Za-z_$][A-Za-z0-9_$.]*)\s*\.classList\.add\([^)]*material-symbols-outlined[^)]*\)/gs;
  for (const m of source.matchAll(classListBind)) {
    bindings.add(m[1]);
  }

  // `const dir = doc.getElementById('cockpit-context-direction')` (or querySelector('#...'))
  if (htmlIconIds.size > 0) {
    const idBind =
      /(?:(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)|([A-Za-z_$][A-Za-z0-9_$.]*))\s*=\s*[^;]*(?:getElementById|querySelector)\(\s*['"`]#?([A-Za-z0-9_-]+)['"`]\s*\)/gs;
    for (const m of source.matchAll(idBind)) {
      if (htmlIconIds.has(m[3])) bindings.add(m[1] || m[2]);
    }
  }

  return bindings;
}

/**
 * Strip ternary condition tests (`cond ? branchA : cond2 ? branchB : branchC`)
 * while preserving optional chaining (`?.`), and extract bare glyph literals
 * assigned to a Material Symbols element.
 */
function extractAssignedGlyphLiterals(rhs) {
  const withoutOptional = rhs.replaceAll('?.', '__OPT__');
  // In `a ? b : c`, only literals in value branches (not condition clauses) can be assigned.
  // Keep every segment after `?`, dropping comparison operands immediately before `?`.
  let candidate = withoutOptional;
  if (candidate.includes('?')) {
    const parts = candidate.split('?');
    // Drop the top-level condition before the first `?`, and inside subsequent segments
    // drop any sub-condition before a nested `?` (which sits after `:`).
    const valueSegments = [];
    for (let i = 1; i < parts.length; i += 1) {
      let seg = parts[i];
      if (i < parts.length - 1 && seg.includes(':')) {
        // Keep the value expression before `:` and discard the next condition after `:`.
        seg = seg.slice(0, seg.lastIndexOf(':'));
      }
      valueSegments.push(seg);
    }
    candidate = valueSegments.join(' ');
  }
  candidate = candidate.replaceAll('__OPT__', '?.');
  return [...candidate.matchAll(STRING_LITERAL)].map((m) => m[1]);
}

/**
 * Files that SHIP markup. Tests assert on markup, they do not render it.
 *
 * The panel markup lives in `src/ui/templates/*.html`, so HTML under `src` is
 * read as well: a glyph named only in a template is still a glyph the font has
 * to carry.
 */
function sourceFiles(directory = SRC_ROOT) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...sourceFiles(absolute));
    else if (
      entry.isFile() &&
      /\.(js|mjs|html)$/.test(entry.name) &&
      !entry.name.endsWith('.test.mjs')
    ) {
      files.push(absolute);
    }
  }
  return [...files.sort(), INDEX_HTML];
}

/**
 * Glyph names the sources ask the icon font for.
 *
 * Inline `<span class="material-symbols-outlined">…</span>` elements are
 * scanned everywhere, while `.textContent` / `.innerText` assignments are
 * scanned only when the target element carries the `material-symbols-outlined`
 * class (or is bound to an HTML template element with that class). This prevents
 * ordinary UI status/prose strings (e.g. `'unknown'`, `'allow'`, `'unavailable'`)
 * written to plain text labels from being treated as missing icon ligatures.
 * @returns {Map<string, string>} glyph -> the first file that names it.
 */
function referencedGlyphs(files = sourceFiles()) {
  const htmlIconIds = collectHtmlIconIds(files);
  const found = new Map();
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    const relative = path.relative(REPO_ROOT, file).split(path.sep).join('/');
    const add = (glyph) => {
      if (!found.has(glyph)) found.set(glyph, relative);
    };
    for (const match of source.matchAll(SPAN_TEXT)) add(match[1]);

    if (file.endsWith('.html')) continue;
    const iconBindings = collectIconBindings(source, htmlIconIds);
    for (const match of source.matchAll(TEXT_ASSIGNMENT)) {
      const target = match[1].trim();
      const isIconTarget =
        target.includes('material-symbols-outlined') ||
        iconBindings.has(target);
      if (!isIconTarget) continue;
      for (const glyph of extractAssignedGlyphLiterals(match[2])) {
        add(glyph);
      }
    }
  }
  return found;
}

/** The `icon_names` list index.html asks Google for. */
function subsettedGlyphs(html = readFileSync(INDEX_HTML, 'utf8')) {
  const match =
    /Material\+Symbols\+Outlined[^"]*[?&]icon_names=([a-z0-9_,]+)/.exec(html);
  assert.ok(
    match,
    'index.html must request Material Symbols with an icon_names subset',
  );
  return new Set(match[1].split(','));
}

test('every glyph the sources render is in the icon_names subset', () => {
  const subset = subsettedGlyphs();
  const missing = [...referencedGlyphs()]
    .filter(([glyph]) => !subset.has(glyph))
    .map(([glyph, file]) => `${glyph} (${file})`);

  assert.deepEqual(
    missing,
    [],
    'Glyphs named by the sources but absent from the index.html icon_names list. ' +
      'Add them there — an unlisted glyph renders as its own name on screen: ' +
      missing.join(', '),
  );
});

test('the unused Material Icons Round family is not loaded', () => {
  // A second icon font, 173 kB, for a family no source ever uses — and
  // src/cockpitMarkup.test.mjs already asserts the markup must not use it.
  const html = readFileSync(INDEX_HTML, 'utf8');
  assert.doesNotMatch(
    html,
    /Material\+Icons\+Round/,
    'index.html loads an icon font nothing renders',
  );
});

test('dynamic icon bindings detect genuine glyphs without misclassifying plain status text', () => {
  const glyphs = referencedGlyphs();
  for (const expectedDynamicGlyph of [
    'light_mode',
    'dark_mode',
    'chevron_left',
    'chevron_right',
    'right_panel_close',
    'right_panel_open',
  ]) {
    assert.ok(
      glyphs.has(expectedDynamicGlyph),
      `Expected dynamic glyph '${expectedDynamicGlyph}' to still be detected`,
    );
  }

  const syntheticHtmlIds = new Set(['dir-icon']);
  const syntheticSource = `
    const icon = host.querySelector('.material-symbols-outlined');
    icon.textContent = flag ? 'dynamic_alpha' : 'dynamic_beta';
    host.querySelector('.material-symbols-outlined').textContent = 'inline_gamma';
    const dirEl = doc.getElementById('dir-icon');
    dirEl.textContent = 'html_id_delta';
    plainLabel.textContent = status === 'cond_ignored' ? 'status_text' : 'fallback_text';
  `;
  const bindings = collectIconBindings(syntheticSource, syntheticHtmlIds);
  const found = [];
  for (const match of syntheticSource.matchAll(TEXT_ASSIGNMENT)) {
    const target = match[1].trim();
    if (target.includes('material-symbols-outlined') || bindings.has(target)) {
      found.push(...extractAssignedGlyphLiterals(match[2]));
    }
  }
  assert.deepEqual(found, [
    'dynamic_alpha',
    'dynamic_beta',
    'inline_gamma',
    'html_id_delta',
  ]);
});
