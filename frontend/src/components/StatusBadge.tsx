import type { ExtractionStatus } from '../types'

interface Props {
  status: ExtractionStatus
  size?: 'sm' | 'md'
}

const CONFIG: Record<ExtractionStatus, { label: string; classes: string; dot: string }> = {
  queued: {
    label: 'Queued',
    classes: 'bg-slate-700 text-slate-300 border border-slate-600',
    dot: 'bg-slate-400',
  },
  extracting: {
    label: 'Extracting',
    classes: 'bg-amber-900/40 text-amber-300 border border-amber-700/50',
    dot: 'bg-amber-400 animate-pulse',
  },
  indexed: {
    label: 'Indexed',
    classes: 'bg-emerald-900/40 text-emerald-300 border border-emerald-700/50',
    dot: 'bg-emerald-400',
  },
  failed: {
    label: 'Failed',
    classes: 'bg-red-900/40 text-red-300 border border-red-700/50',
    dot: 'bg-red-400',
  },
}

export function StatusBadge({ status, size = 'md' }: Props) {
  const { label, classes, dot } = CONFIG[status] ?? CONFIG.queued
  const sizeClasses = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-xs'

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-medium ${sizeClasses} ${classes}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {label}
    </span>
  )
}
