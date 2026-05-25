import { useEffect, useRef, useState } from 'react'
import { getDocument, deleteDocument, getDownloadUrl } from '../api/documents'
import type { ExtractionStatus, RecentUpload } from '../types'
import { StatusBadge } from './StatusBadge'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

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
  upload: RecentUpload
  onDelete: (docId: string) => void
}

const TERMINAL_STATUSES: ExtractionStatus[] = ['indexed', 'failed']

export function DocumentCard({ upload, onDelete }: Props) {
  const [status, setStatus] = useState<ExtractionStatus>(upload.status)
  const [pageCount, setPageCount] = useState<number | null>(null)
  const [wordCount, setWordCount] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (TERMINAL_STATUSES.includes(upload.status)) return

    const poll = async () => {
      try {
        const doc = await getDocument(upload.doc_id)
        setStatus(doc.extraction_status)
        if (doc.page_count != null) setPageCount(doc.page_count)
        if (doc.word_count != null) setWordCount(doc.word_count)
        if (doc.extraction_error) setError(doc.extraction_error)

        if (TERMINAL_STATUSES.includes(doc.extraction_status)) {
          if (intervalRef.current) clearInterval(intervalRef.current)
        }
      } catch {
        // silently ignore poll errors
      }
    }

    poll()
    intervalRef.current = setInterval(poll, 2000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [upload.doc_id, upload.status])

  const handleDelete = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    setDeleting(true)
    try {
      await deleteDocument(upload.doc_id)
      onDelete(upload.doc_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  return (
    <div className="group relative bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 hover:border-slate-600 transition-all duration-200 animate-slide-up">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-2xl flex-shrink-0">{getFileIcon(upload.file_name)}</span>
          <div className="min-w-0">
            <p className="text-white font-medium text-sm truncate">
              {upload.title || upload.file_name}
            </p>
            {upload.title && (
              <p className="text-slate-500 text-xs truncate">{upload.file_name}</p>
            )}
            <div className="flex items-center gap-2 mt-1">
              <span className="text-slate-500 text-xs">{formatBytes(upload.file_size_bytes)}</span>
              {pageCount != null && (
                <span className="text-slate-500 text-xs">· {pageCount} pages</span>
              )}
              {wordCount != null && (
                <span className="text-slate-500 text-xs">· {wordCount.toLocaleString()} words</span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <StatusBadge status={status} size="sm" />

          <a
            href={getDownloadUrl(upload.doc_id)}
            target="_blank"
            rel="noopener noreferrer"
            title="Download"
            className="p-1.5 rounded-lg text-slate-400 hover:text-blue-400 hover:bg-blue-950/30 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
          </a>

          <button
            onClick={handleDelete}
            disabled={deleting}
            title={confirmDelete ? 'Click again to confirm' : 'Delete'}
            className={`p-1.5 rounded-lg transition-colors ${
              confirmDelete
                ? 'text-red-300 bg-red-900/40 hover:bg-red-800/60'
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

      {error && (
        <p className="mt-2 text-red-400 text-xs bg-red-950/30 rounded-lg px-3 py-2 border border-red-800/40">
          {error}
        </p>
      )}

      {confirmDelete && !deleting && (
        <p className="mt-2 text-amber-400 text-xs animate-fade-in">
          Click delete again to confirm permanent deletion.{' '}
          <button
            className="underline text-slate-400 hover:text-white"
            onClick={() => setConfirmDelete(false)}
          >
            Cancel
          </button>
        </p>
      )}

      <div className="mt-2">
        <p className="text-slate-600 text-xs font-mono truncate">id: {upload.doc_id}</p>
      </div>
    </div>
  )
}
