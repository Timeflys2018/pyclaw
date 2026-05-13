export const PROTOCOL_OP_EXACT_COMMANDS: ReadonlySet<string> = new Set(['/stop'])
export const PROTOCOL_OP_PREFIX_COMMANDS: ReadonlyArray<string> = ['/steer', '/btw']

export function isProtocolOp(content: string): boolean {
  const trimmed = content.trim().toLowerCase()
  if (PROTOCOL_OP_EXACT_COMMANDS.has(trimmed)) return true
  if (PROTOCOL_OP_PREFIX_COMMANDS.includes(trimmed)) return true
  return PROTOCOL_OP_PREFIX_COMMANDS.some((p) => trimmed.startsWith(p + ' '))
}
