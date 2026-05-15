import clsx from 'clsx'

interface ShimmerProps {
  className?: string
}

function Shimmer({ className }: ShimmerProps) {
  return (
    <div
      className={clsx(
        'relative overflow-hidden bg-[var(--c-surface)]',
        'before:absolute before:inset-0 before:-translate-x-full',
        'before:bg-gradient-to-r before:from-transparent before:via-[var(--c-border)]/40 before:to-transparent',
        'before:animate-[shimmer_1.4s_infinite]',
        className,
      )}
    />
  )
}

export function MessageSkeleton() {
  return (
    <div className="space-y-6 mb-2">
      <div className="flex justify-end">
        <Shimmer className="rounded-2xl rounded-br-md h-12 w-2/3 max-w-md" />
      </div>
      <div className="flex justify-start">
        <div className="w-3/4 max-w-2xl space-y-2">
          <Shimmer className="rounded-md h-4 w-1/2" />
          <Shimmer className="rounded-md h-4 w-full" />
          <Shimmer className="rounded-md h-4 w-5/6" />
        </div>
      </div>
      <div className="flex justify-end">
        <Shimmer className="rounded-2xl rounded-br-md h-10 w-1/2 max-w-sm" />
      </div>
    </div>
  )
}

export function SidebarRowSkeleton() {
  return (
    <div className="px-3 py-2.5 flex items-center gap-2">
      <Shimmer className="rounded-md h-3 w-3" />
      <Shimmer className="rounded-md h-3 flex-1" />
    </div>
  )
}
