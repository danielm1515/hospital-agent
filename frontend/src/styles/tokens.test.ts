import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Paths are relative to the Vitest root, i.e. `frontend/`.
const copy = resolve(process.cwd(), 'src/styles/tokens.css')
const source = resolve(process.cwd(), '../design/ramon-ui/tokens.css')

describe('tokens.css', () => {
  it('is a byte-identical copy of the design file', () => {
    expect(readFileSync(copy)).toEqual(readFileSync(source))
  })
})
