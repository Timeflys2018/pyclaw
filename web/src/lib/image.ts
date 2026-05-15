import type { ImageBlock } from '../types'

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024
export const MAX_IMAGES_PER_MESSAGE = 4

const SUPPORTED_MIME_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
])

export interface ImageError {
  kind: 'unsupported' | 'too-large' | 'too-many' | 'read-failure'
  message: string
}

export function isSupportedImageType(mime: string): boolean {
  return SUPPORTED_MIME_TYPES.has(mime.toLowerCase())
}

export async function fileToImageBlock(file: File): Promise<ImageBlock | ImageError> {
  if (!isSupportedImageType(file.type)) {
    return {
      kind: 'unsupported',
      message: `Unsupported image type: ${file.type || 'unknown'}. Use PNG, JPEG, GIF, or WebP.`,
    }
  }
  if (file.size > MAX_IMAGE_BYTES) {
    const mb = (file.size / (1024 * 1024)).toFixed(1)
    return {
      kind: 'too-large',
      message: `Image is ${mb} MB; the limit is ${MAX_IMAGE_BYTES / (1024 * 1024)} MB.`,
    }
  }

  const buffer = await file.arrayBuffer().catch(() => null)
  if (!buffer) {
    return { kind: 'read-failure', message: 'Could not read image file.' }
  }
  const base64 = arrayBufferToBase64(buffer)
  return {
    type: 'image',
    data: base64,
    mime_type: file.type.toLowerCase(),
  }
}

export function imageBlockToDataUrl(block: ImageBlock): string {
  return `data:${block.mime_type};base64,${block.data}`
}

export function isImageError(value: unknown): value is ImageError {
  return (
    typeof value === 'object' &&
    value !== null &&
    'kind' in value &&
    'message' in value &&
    typeof (value as ImageError).kind === 'string'
  )
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const slice = bytes.subarray(i, i + chunkSize)
    binary += String.fromCharCode.apply(null, Array.from(slice))
  }
  return btoa(binary)
}
