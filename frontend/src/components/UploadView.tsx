import { useState } from 'react'
import { uploadDocument } from '../api/documents'
import type { RecentUpload } from '../types'
import { FileDropZone } from './FileDropZone'
import { DocumentCard } from './DocumentCard'

interface Props {
  recentUploads: RecentUpload[]
  onUploaded: (upload: RecentUpload) => void
  onDelete: (docId: string) => void
}

export function UploadView({ recentUploads, onUploaded, onDelete }: Props) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [tags, setTags] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState(0)

  const handleUpload = async () => {
    if (!selectedFile) return
    setUploading(true)
    setError(null)
    setUploadProgress(0)

    // Simulate progress while uploading
    const progressInterval = setInterval(() => {
      setUploadProgress((p) => Math.min(p + 15, 85))
    }, 200)

    try {
      const res = await uploadDocument({ file: selectedFile, title: title || undefined, tags: tags || undefined })
      clearInterval(progressInterval)
      setUploadProgress(100)

      const newUpload: RecentUpload = {
        doc_id: res.doc_id,
        file_name: res.file_name,
        status: res.status,
        title: title || undefined,
        file_size_bytes: res.file_size_bytes,
        uploaded_at: new Date().toISOString(),
      }

      onUploaded(newUpload)
      setSelectedFile(null)
      setTitle('')
      setTags('')

      setTimeout(() => setUploadProgress(0), 600)
    } catch (err) {
      clearInterval(progressInterval)
      setUploadProgress(0)
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Page header */}
      <div>
        <h2 className="text-white text-2xl font-bold">Upload Document</h2>
        <p className="text-slate-400 text-sm mt-1">
          Upload files for full-text indexing and search. Extraction is asynchronous — poll for status.
        </p>
      </div>

      {/* Drop zone */}
      <FileDropZone onFileSelected={setSelectedFile} disabled={uploading} />

      {/* Selected file preview */}
      {selectedFile && (
        <div className="flex items-center gap-3 bg-blue-950/30 border border-blue-700/30 rounded-xl px-4 py-3 animate-slide-up">
          <span className="text-2xl">📎</span>
          <div className="flex-1 min-w-0">
            <p className="text-white text-sm font-medium truncate">{selectedFile.name}</p>
            <p className="text-slate-400 text-xs mt-0.5">
              {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB · {selectedFile.type || 'unknown type'}
            </p>
          </div>
          {!uploading && (
            <button
              onClick={() => setSelectedFile(null)}
              className="text-slate-400 hover:text-white transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      )}

      {/* Metadata */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-slate-300 text-sm mb-1.5 font-medium">
            Title <span className="text-slate-500 font-normal">(optional)</span>
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={500}
            placeholder="Annual Report 2025"
            disabled={uploading}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors disabled:opacity-50"
          />
        </div>
        <div>
          <label className="block text-slate-300 text-sm mb-1.5 font-medium">
            Tags <span className="text-slate-500 font-normal">(optional, comma-separated)</span>
          </label>
          <input
            type="text"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="finance, q3, report"
            disabled={uploading}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors disabled:opacity-50"
          />
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-3 bg-red-950/30 border border-red-700/40 rounded-xl px-4 py-3 animate-fade-in">
          <span className="text-red-400 mt-0.5">⚠️</span>
          <p className="text-red-300 text-sm">{error}</p>
        </div>
      )}

      {/* Upload progress */}
      {uploading && uploadProgress > 0 && (
        <div className="space-y-1.5 animate-fade-in">
          <div className="flex justify-between text-xs text-slate-400">
            <span>Uploading…</span>
            <span>{uploadProgress}%</span>
          </div>
          <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-600 to-indigo-500 rounded-full transition-all duration-200"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Upload button */}
      <button
        onClick={handleUpload}
        disabled={!selectedFile || uploading}
        className="w-full py-3 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-semibold text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 shadow-lg shadow-blue-900/20"
      >
        {uploading ? (
          <>
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Uploading…
          </>
        ) : (
          <>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            Upload & Index
          </>
        )}
      </button>

      {/* Recent uploads */}
      {recentUploads.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-slate-300 font-semibold text-sm">Recent Uploads</h3>
            <span className="text-slate-500 text-xs">{recentUploads.length} document{recentUploads.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="space-y-2">
            {recentUploads.map((u) => (
              <DocumentCard key={u.doc_id} upload={u} onDelete={onDelete} />
            ))}
          </div>
        </div>
      )}

      {recentUploads.length === 0 && !selectedFile && (
        <div className="text-center py-8 text-slate-600">
          <p className="text-4xl mb-2">📭</p>
          <p className="text-sm">No uploads yet. Drop a file above to get started.</p>
        </div>
      )}
    </div>
  )
}
