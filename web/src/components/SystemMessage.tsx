import type { Message } from '../types'

interface Props {
  message: Message
}

export default function SystemMessage({ message }: Props) {
  return (
    <div className="flex w-full mb-4 justify-center">
      <span className="text-xs italic text-[var(--c-text-secondary)] px-3 py-1 rounded-full bg-[var(--c-surface)]/40 max-w-[80%] text-center">
        {message.content}
      </span>
    </div>
  )
}
