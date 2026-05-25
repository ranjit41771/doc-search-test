import { useState } from 'react'
import { deleteDocument, getDownloadUrl } from '../api/documents'
import type { SearchResultItem } from '../types'
import { StatusBadge } from './StatusBadge'

function getFileIcon(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    pdf: '📄', docx: '📝', pptx: '📊', txt: '🗒️', md: '🗒️',
    png: '🖼️', jpg: '🖼️', jpeg: '🖼️', tiff: '🖼️', tif: '🖼️',
    xlsx: '📈', csv: '📈',
  }
  return map[ext] ?? '📁'
}

interface Props {
  result: SearchResultItem
  rank: number
  onDelete: (docId: string) => void
}

export function SearchResult({ result, rank, onDelete }: Props) {
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const maxScore = 10
  const scorePercent = Math.min((result.score / maxScore) * 100, 100)

  const handleDelete = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    setDeleting(true)
    try {
      await deleteDocument(result.doc_id)
      onDelete(result.doc_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  // Convert <em> tags to highlighted spans
  const highlightedSnippet = result.snippet
    .replace(/&lt;em&gt;/g, '<em>')
    .replace(/&lt;\/em&gt;/g, '</em>')

  return (
    <div className="group bg-slate-800/50 border border-slate-700/50 rounded-xl p-5 hover:border-slate-500/70 hover:bg-slate-800/70 transition-all duration-200 animate-slide-up">
      <div className="flex items-start justify-between gap-4">
        {/* Rank */}
        <div className="flex-shrink-0 w-7 h-7 rounded-lg bg-slate-700/60 flex items-center justify-center text-slate-400 text-xs font-bold mt-0.5">
          {rank}
        </div>

        <div className="flex-1 min-w-0">
          {/* File name + icon */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg">{getFileIcon(result.file_name)}</span>
            <span className="text-white font-semibold text-sm truncate">{result.file_name}</span>
            <StatusBadge status={result.extraction_status} size="sm" />
            {result.page_hint > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-blue-900/40 text-blue-300 text-xs border border-blue-700/30">
                Page {result.page_hint}
              </span>
            )}
          </div>

          {/* Highlighted snippet */}
          <p
            className="mt-2.5 text-slate-300 text-sm leading-relaxed line-clamp-3 [&_em]:bg-yellow-400/20 [&_em]:text-yellow-200 [&_em]:not-italic [&_em]:font-semibold [&_em]:px-0.5 [&_em]:rounded"
            dangerouslySetInnerHTML={{ __html: highlightedSnippet }}
          />

          {/* Relevance score bar */}
          <div className="mt-3 flex items-center gap-3">
            <span className="text-slate-500 text-xs flex-shrink-0">Relevance</span>
            <div className="flex-1 h-1.5 bg-slate-700/60 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-blue-600 to-indigo-500 rounded-full transition-all duration-700"
                style={{ width: `${scorePercent}%` }}
              />
            </div>
            <span className="text-slate-400 text-xs flex-shrink-0 font-mono">{result.score.toFixed(2)}</span>
          </div>

          {error && (
            <p className="mt-2 text-red-400 text-xs bg-red-950/30 rounded-lg px-3 py-2 border border-red-800/40">
              {error}
            </p>
          )}

          {confirmDelete && (
            <p className="mt-2 text-amber-400 text-xs animate-fade-in">
              Confirm delete?{' '}
              <button className="underline text-slate-400 hover:text-white ml-1" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <a
            href={getDownloadUrl(result.doc_id)}
            target="_blank"
            rel="noopener noreferrer"
            title="Download"
            className="p-2 rounded-lg text-slate-400 hover:text-blue-400 hover:bg-blue-950/30 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
          </a>

          <button
            onClick={handleDelete}
            disabled={deleting}
            title={confirmDelete ? 'Confirm delete' : 'Delete'}
            className={`p-2 rounded-lg transition-colors ${
              confirmDelete
                ? 'text-red-300 bg-red-900/40'
                : 'text-slate-400 hover:text-red-400 hover:bg-red-950/30'
            }`}
          >
            {deleting ? (
              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
