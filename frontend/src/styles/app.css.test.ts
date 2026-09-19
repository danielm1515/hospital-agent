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
 * Removes every top-level `@media { ... }` block (braces and all), so a selector
 * that is only re-declared inside a media query does not count as a second
 * top-level rule block. Assumes braces are already known to be balanced.
 */
function stripMediaBlocks(source: string): string {
  let result = ''
  let i = 0
  while (i < source.length) {
    const atMedia = source.indexOf('@media', i)
    if (atMedia === -1) {
      result += source.slice(i)
      break
    }
    result += source.slice(i, atMedia)
    const openBrace = source.indexOf('{', atMedia)
    if (openBrace === -1) {
      // Malformed input; let the brace-balance test catch this separately.
      result += source.slice(atMedia)
      break
    }
    let depth = 1
    let j = openBrace + 1
    while (j < source.length && depth > 0) {
      if (source[j] === '{') depth++
      if (source[j] === '}') depth--
      j++
    }
    i = j // skip past the matched closing brace of the @media block
  }
  return result
}

/** Counts how many times a top-level rule block's selector opens with exactly this selector text. */
function countTopLevelRule(source: string, selector: string): number {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = new RegExp(`(^|\\})\\s*${escaped}\\s*\\{`, 'g')
  return (source.match(pattern) ?? []).length
}

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

  it('declares .timeline exactly once outside media queries', () => {
    // Regression test: a merge once left two `.timeline` rule blocks in the file
    // (the patient status stepper and the staff Audit trace), so the staff one
    // silently inherited the patient's `flex-direction: column` layout. The
    // staff trace must use a distinct class (`.audit-timeline`) instead.
    const outsideMedia = stripMediaBlocks(stripComments(css))
    expect(countTopLevelRule(outsideMedia, '.timeline')).toBe(1)
  })

  it('declares .control .select exactly once outside media queries', () => {
    // Regression test: a merge left `.control .select` closed early, with three
    // trailing declarations and a stray `}` as dead text after the rule.
    const outsideMedia = stripMediaBlocks(stripComments(css))
    expect(countTopLevelRule(outsideMedia, '.control .select')).toBe(1)
  })

  it('declares .plan-steps li exactly once outside media queries', () => {
    // Regression test: the same merge duplicated `.plan-steps li` into two
    // separate rule blocks instead of one with every declaration.
    const outsideMedia = stripMediaBlocks(stripComments(css))
    expect(countTopLevelRule(outsideMedia, '.plan-steps li')).toBe(1)
  })
})
