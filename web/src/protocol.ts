export const PROTOCOL_OP_TEXT_COMMANDS: ReadonlySet<string> = new Set(['/stop'])

export function isProtocolOp(content: string): boolean {
  return PROTOCOL_OP_TEXT_COMMANDS.has(content.trim().toLowerCase())
}
