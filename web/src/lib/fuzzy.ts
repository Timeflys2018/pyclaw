export interface FuzzyMatch<T> {
  item: T
  score: number
  matchedIndices: number[]
}

export function fuzzyScore(query: string, target: string): { score: number; indices: number[] } | null {
  if (query.length === 0) {
    return { score: 0, indices: [] }
  }
  const q = query.toLowerCase()
  const t = target.toLowerCase()
  const indices: number[] = []
  let qi = 0
  let consecutive = 0
  let score = 0
  let lastMatchAt = -1

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      indices.push(ti)
      let charScore = 1
      if (ti === 0) charScore += 3
      else if (/\s|[-_/.]/.test(t[ti - 1])) charScore += 2
      if (lastMatchAt >= 0 && ti === lastMatchAt + 1) {
        consecutive += 1
        charScore += consecutive * 2
      } else {
        consecutive = 0
      }
      score += charScore
      lastMatchAt = ti
      qi += 1
    }
  }

  if (qi < q.length) return null

  const lengthPenalty = Math.max(0, target.length - query.length) * 0.05
  return { score: score - lengthPenalty, indices }
}

export function fuzzyFilter<T>(
  query: string,
  items: T[],
  getString: (item: T) => string,
): FuzzyMatch<T>[] {
  if (query.trim() === '') {
    return items.map((item) => ({ item, score: 0, matchedIndices: [] }))
  }
  const matches: FuzzyMatch<T>[] = []
  for (const item of items) {
    const result = fuzzyScore(query, getString(item))
    if (result !== null) {
      matches.push({ item, score: result.score, matchedIndices: result.indices })
    }
  }
  matches.sort((a, b) => b.score - a.score)
  return matches
}
