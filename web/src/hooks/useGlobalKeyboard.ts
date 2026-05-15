import { useEffect } from 'react'

export type ShortcutModifier = 'mod'

export interface ShortcutBinding {
  modifier: ShortcutModifier
  key: string
  handler: (event: KeyboardEvent) => void
  preventDefault?: boolean
}

export const IS_MAC =
  typeof navigator !== 'undefined' &&
  /Mac|iPod|iPhone|iPad/.test(navigator.platform)

export function modKeyLabel(): string {
  return IS_MAC ? '⌘' : 'Ctrl'
}

function eventMatches(event: KeyboardEvent, binding: ShortcutBinding): boolean {
  const modPressed = IS_MAC ? event.metaKey : event.ctrlKey
  const noOtherMod = IS_MAC ? !event.ctrlKey : !event.metaKey
  if (!modPressed || !noOtherMod) return false
  return event.key.toLowerCase() === binding.key.toLowerCase()
}

export function useGlobalKeyboard(bindings: ShortcutBinding[]): void {
  useEffect(() => {
    let composing = false
    const onCompositionStart = () => {
      composing = true
    }
    const onCompositionEnd = () => {
      composing = false
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (composing || event.isComposing) return
      for (const b of bindings) {
        if (eventMatches(event, b)) {
          if (b.preventDefault !== false) event.preventDefault()
          b.handler(event)
          return
        }
      }
    }

    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('compositionstart', onCompositionStart, true)
    window.addEventListener('compositionend', onCompositionEnd, true)

    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('compositionstart', onCompositionStart, true)
      window.removeEventListener('compositionend', onCompositionEnd, true)
    }
  }, [bindings])
}
