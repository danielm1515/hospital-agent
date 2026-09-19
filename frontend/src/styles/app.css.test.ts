import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Paths are relative to the Vitest root, i.e. `frontend/`.
const cssPath = resolve(process.cwd(), 'src/styles/app.css')
const css = readFileSync(cssPath, 'utf8')

/** Strips /* ... *\/ comments so they cannot hide braces or selectors from the checks below. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '')
}

/**
 * Removes every top-level at-rule block (`@media { ... }`, `@keyframes { ... }`, etc.),
 * braces and all, so a selector that only exists inside one (or a keyframe selector like
 * `to`/`from`/`50%`) never counts as a top-level rule. Assumes braces are already known
 * to be balanced.
 */
function stripAtRuleBlocks(source: string): string {
  let result = ''
  let i = 0
  while (i < source.length) {
    const at = source.indexOf('@', i)
    if (at === -1) {
      result += source.slice(i)
      break
    }
    result += source.slice(i, at)
    const openBrace = source.indexOf('{', at)
    if (openBrace === -1) {
      // Malformed input; let the brace-balance test catch this separately.
      result += source.slice(at)
      break
    }
    let depth = 1
    let j = openBrace + 1
    while (j < source.length && depth > 0) {
      if (source[j] === '{') depth++
      if (source[j] === '}') depth--
      j++
    }
    i = j // skip past the matched closing brace of the at-rule block
  }
  return result
}

/**
 * Walks the (comment- and at-rule-free) source at brace depth 0 and returns every
 * top-level selector, in file order, exactly as declared (whitespace collapsed).
 * A rule with no top-level content between selector text and `{` is skipped
 * (defensive; shouldn't happen once braces are balanced).
 */
function collectTopLevelSelectors(source: string): string[] {
  const selectors: string[] = []
  let depth = 0
  let buffer = ''
  for (const char of source) {
    if (char === '{') {
      if (depth === 0) {
        const selector = buffer.replace(/\s+/g, ' ').trim()
        if (selector) selectors.push(selector)
        buffer = ''
      }
      depth++
    } else if (char === '}') {
      depth--
      if (depth === 0) buffer = ''
    } else if (depth === 0) {
      buffer += char
    }
  }
  return selectors
}

/**
 * Selectors that are deliberately declared in more than one top-level rule block.
 * Add an entry here only with a comment explaining why the duplicate is intentional -
 * everything else must be a single rule block (merge the declarations instead).
 */
const ALLOWED_DUPLICATE_SELECTORS: readonly string[] = []

describe('app.css structural integrity', () => {
  it('has balanced braces', () => {
    const stripped = stripComments(css)
    let depth = 0
    for (const char of stripped) {
      if (char === '{') depth++
      if (char === '}') {
        depth--
        expect(depth).toBeGreaterThanOrEqual(0)
      }
    }
    expect(depth).toBe(0)
  })

  it('never declares the same top-level selector in two separate rule blocks', () => {
    // Regression test: a merge has repeatedly left a selector declared twice at the
    // top level (`.timeline`, `.control .select`, `.plan-steps li`, `.page-head`), so
    // whichever rule comes later silently wins and the earlier declarations are dead.
    // This check is generic instead of naming those selectors individually, so the
    // next such merge collision is caught automatically instead of needing its own
    // hand-written regression case.
    const outsideAtRules = stripAtRuleBlocks(stripComments(css))
    const selectors = collectTopLevelSelectors(outsideAtRules)

    const counts = new Map<string, number>()
    for (const selector of selectors) {
      counts.set(selector, (counts.get(selector) ?? 0) + 1)
    }

    const duplicates = [...counts.entries()]
      .filter(([selector, count]) => count > 1 && !ALLOWED_DUPLICATE_SELECTORS.includes(selector))
      .map(([selector, count]) => `${selector} (${count}x)`)

    expect(duplicates).toEqual([])
  })
})
