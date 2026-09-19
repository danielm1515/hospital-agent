import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

/**
 * Regression coverage for the App <-> page import cycle
 * (`App.tsx` exported the route constants and imported `PatientRoutes`/
 * `StaffRoutes`/`PatientLogin`/`StaffLogin`; those pulled in page modules that
 * imported the constants back from `App.tsx`). In a real browser's native ESM
 * loader this throws `ReferenceError: Cannot access 'PATIENT_HOME' before
 * initialization` at load time (nothing renders), because `pages/patient/
 * paths.ts` reads the constant at its own top level, before `App.tsx`'s side
 * of the cycle has finished evaluating.
 *
 * Vitest's module runner does *not* reproduce that failure — its transform
 * doesn't enforce the same TDZ ordering a browser does for circular ESM, so a
 * test that merely imports `paths.ts` (or `StaffLogin.tsx`) first and checks
 * the values passes whether or not the cycle exists (verified by hand against
 * the pre-fix code: this suite's "resolves values" cases below stayed green
 * even with the cycle reinstated). Those cases are kept as a sanity check on
 * the values themselves, but the actual regression guard is the static
 * cycle check, which fails on the pre-fix graph regardless of runtime
 * semantics (confirmed against the pre-fix code, and reproduced independently
 * by `npx madge --circular --extensions ts,tsx src`).
 */

const SRC_DIR = resolve(__dirname)
const EXTENSIONS = ['.ts', '.tsx']
const IMPORT_RE = /(?:import|export)\s+(?:[^'"]*?\sfrom\s+)?['"](\.[^'"]+)['"]/g

function resolveModule(fromFile: string, specifier: string): string | null {
  const base = resolve(dirname(fromFile), specifier)
  const candidates = [base, ...EXTENSIONS.map((ext) => base + ext), ...EXTENSIONS.map((ext) => join(base, `index${ext}`))]
  for (const candidate of candidates) {
    try {
      if (statSync(candidate).isFile()) return candidate
    } catch {
      // not this candidate
    }
  }
  return null
}

function listSourceFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      files.push(...listSourceFiles(full))
    } else if (EXTENSIONS.includes(full.slice(full.lastIndexOf('.'))) && !full.endsWith('.test.ts') && !full.endsWith('.test.tsx')) {
      files.push(full)
    }
  }
  return files
}

/** A file -> its statically-resolvable relative-import edges. */
function buildGraph(files: string[]): Map<string, string[]> {
  const graph = new Map<string, string[]>()
  for (const file of files) {
    const source = readFileSync(file, 'utf8')
    const edges: string[] = []
    for (const match of source.matchAll(IMPORT_RE)) {
      const resolved = resolveModule(file, match[1])
      if (resolved) edges.push(resolved)
    }
    graph.set(file, edges)
  }
  return graph
}

/** Depth-first search for a cycle; returns the first one found as file paths, or null. */
function findCycle(graph: Map<string, string[]>): string[] | null {
  const state = new Map<string, 'visiting' | 'done'>()
  const stack: string[] = []

  function visit(node: string): string[] | null {
    state.set(node, 'visiting')
    stack.push(node)
    for (const next of graph.get(node) ?? []) {
      if (state.get(next) === 'visiting') {
        const start = stack.indexOf(next)
        return [...stack.slice(start), next]
      }
      if (!state.has(next)) {
        const found = visit(next)
        if (found) return found
      }
    }
    stack.pop()
    state.set(node, 'done')
    return null
  }

  for (const node of graph.keys()) {
    if (!state.has(node)) {
      const found = visit(node)
      if (found) return found
    }
  }
  return null
}

describe('no import cycles under src (would have caught the App <-> page cycle)', () => {
  it('has no circular relative imports anywhere in src', () => {
    const files = listSourceFiles(SRC_DIR)
    const graph = buildGraph(files)
    const cycle = findCycle(graph)
    const description = cycle?.map((file) => relative(SRC_DIR, file)).join(' -> ')
    expect(description, `circular import found: ${description}`).toBeUndefined()
  })
})

describe('route constants resolve correctly when their consumers load first', () => {
  it('pages/patient/paths resolves PATIENT_HOME-derived constants when imported first', async () => {
    vi.resetModules()
    const paths = await import('./pages/patient/paths')
    expect(paths.MY_REQUESTS).toBe('/patient')
    expect(paths.NEW_REQUEST).toBe('/patient/new')
    expect(paths.requestPath('abc 123')).toBe('/patient/requests/abc%20123')
  })

  it('pages/staff/StaffLogin loads and resolves STAFF_HOME when imported first', async () => {
    vi.resetModules()
    const staffLogin = await import('./pages/staff/StaffLogin')
    expect(staffLogin.StaffLogin).toBeTypeOf('function')
  })

  it('routes.ts is a leaf module: no imports of its own', () => {
    const source = readFileSync(join(SRC_DIR, 'routes.ts'), 'utf8')
    expect(source).not.toMatch(/^import /m)
  })
})
