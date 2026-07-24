export type AnswerSegment =
  | { type: 'text'; text: string }
  | { type: 'citation'; text: string; sourceId: string }

export function parseAnswerSegments(answer: string, sourceIds: Set<string>): AnswerSegment[] {
  const segments: AnswerSegment[] = []
  const pattern = /\[S\d+\]/g
  let offset = 0
  for (const match of answer.matchAll(pattern)) {
    const index = match.index ?? 0
    if (index > offset) segments.push({ type: 'text', text: answer.slice(offset, index) })
    const sourceId = match[0].slice(1, -1)
    if (sourceIds.has(sourceId)) {
      segments.push({ type: 'citation', text: match[0], sourceId })
    } else {
      segments.push({ type: 'text', text: match[0] })
    }
    offset = index + match[0].length
  }
  if (offset < answer.length) segments.push({ type: 'text', text: answer.slice(offset) })
  return segments
}
