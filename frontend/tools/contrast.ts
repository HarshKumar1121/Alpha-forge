/**
 * The contrast gate (DESIGN.md → Floors): every text token in the Floors table, measured in
 * APCA over `surface-1`, must still read what the table says and still clear its floor.
 *   node frontend/tools/contrast.ts DESIGN.md
 *
 * The colours and the table are both read from DESIGN.md, so a palette change that moves a
 * figure fails here until the table is updated, and one that drops a token below its floor
 * fails until it is lifted. Node runs this file as it is; nothing is installed for it.
 */

import { readFileSync } from 'node:fs'

/** APCA 0.0.98G-4g: Lc of `text` over `background`, as a magnitude (dark or light text alike). */
function apca(text: string, background: string): number {
  const luminance = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255)
    const y = 0.2126729 * (r ?? 0) ** 2.4 + 0.7151522 * (g ?? 0) ** 2.4 + 0.072175 * (b ?? 0) ** 2.4
    return y < 0.022 ? y + (0.022 - y) ** 1.414 : y
  }
  const [yt, yb] = [luminance(text), luminance(background)]
  if (Math.abs(yb - yt) < 0.0005) return 0
  const sapc = yb > yt ? (yb ** 0.56 - yt ** 0.57) * 1.14 : (yb ** 0.65 - yt ** 0.62) * 1.14
  const clipped = Math.abs(sapc) < 0.1 ? 0 : Math.abs(sapc) - 0.027
  return clipped * 100
}

/** The floor a row's verdict promises: prose, a figure acted on, chrome, or none for non-text. */
function floorOf(verdict: string): number {
  if (/body text/i.test(verdict)) return 75
  if (/figures a consultant acts on|fluent text/i.test(verdict)) return 60
  if (/labels|headers|hints|punctuation/i.test(verdict)) return 40
  return 0
}

const design = readFileSync(process.argv[2] ?? 'DESIGN.md', 'utf8')
const colors = new Map(
  [...design.matchAll(/^ {2}([a-z0-9-]+): "(#[0-9a-f]{6})"$/gim)].map((m) => [m[1], m[2]]),
)
const surface = colors.get('surface-1')
if (!surface) throw new Error('DESIGN.md has no surface-1 colour')

const failures: string[] = []
const rows = [...design.matchAll(/^\| `([a-z0-9-]+)` \| ([\d.]+) \| [^|]+ \| ([^|]+) \|$/gm)]
if (rows.length === 0) failures.push('The Floors table has no rows to check.')
for (const [, token = '', stated = '', verdict = ''] of rows) {
  const hex = colors.get(token)
  if (!hex) {
    failures.push(`${token}: in the Floors table but not among the colours.`)
    continue
  }
  const lc = apca(hex, surface)
  if (Math.abs(lc - Number(stated)) > 0.5)
    failures.push(`${token}: the table says Lc ${stated}, it measures ${lc.toFixed(1)}.`)
  const floor = floorOf(verdict)
  if (lc < floor) failures.push(`${token}: Lc ${lc.toFixed(1)} is under its Lc ${floor} floor.`)
}

if (failures.length) {
  for (const failure of failures) console.error(failure)
  process.exit(1)
}
