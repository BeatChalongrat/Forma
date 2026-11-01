/** What FORMA reads: `EXCEL_EXT` and `CSV_EXT` in backend `ingestion/readers.py`. */
export const UPLOAD_TYPES = ['.xlsx', '.xlsm', '.xls', '.xlsb', '.csv', '.txt', '.tsv']
export const UPLOAD_ACCEPT = UPLOAD_TYPES.join(',')

const MAX_NAMES = 3

export function isReadable(name: string): boolean {
  const lower = name.toLowerCase()
  return UPLOAD_TYPES.some((type) => lower.endsWith(type))
}

/** A dropped file FORMA cannot read must say so: a file that quietly fails to appear looks like a mistake by the person. */
export function splitReadable<T extends { name: string }>(files: T[]): { readable: T[]; refused: T[] } {
  return { readable: files.filter((f) => isReadable(f.name)), refused: files.filter((f) => !isReadable(f.name)) }
}

export function refusedNames(refused: { name: string }[]): { names: string; more: number } {
  return { names: refused.slice(0, MAX_NAMES).map((f) => f.name).join(', '), more: Math.max(0, refused.length - MAX_NAMES) }
}
