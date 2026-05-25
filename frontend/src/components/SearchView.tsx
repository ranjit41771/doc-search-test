import { useCallback, useEffect, useRef, useState } from 'react'
import { searchDocuments } from '../api/search'
import type { SearchResponse, SearchResultItem } from '../types'
import { SearchResult } from './SearchResult'

interface LatencyInfo {
  server: number
  roundTrip: number
  stale: boolean
}

function LatencyBadge({ info }: { info: LatencyInfo }) {
  const { server, roundTrip, stale } = info
  const colorClass =
    server <= 100
      ? 'text-emerald-400 border-emerald-700/40 bg-emerald-900/20'
      : server <= 300
        ? 'text-yellow-400 border-yellow-700/40 bg-yellow-900/20'
        : 'text-red-400 border-red-700/40 bg-red-900/20'

  return (
    <span
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-mono transition-opacity duration-300 ${colorClass} ${stale ? 'opacity-40 italic' : ''}`}
    >
      ⚡ Server: {server}ms · Round-trip: {roundTrip}ms
    </span>
  )
}

function SearchSkeleton() {
  return (
    <div className="space-y-3 animate-pulse">
      {[...Array(3)].map((_, i) => (
        <div key={i} className="bg-slate-800/50 border border-slate-700/30 rounded-xl p-5">
          <div className="flex gap-4">
            <div className="w-7 h-7 rounded-lg bg-slate-700" />
            <div className="flex-1 space-y-2.5">
              <div className="flex gap-2">
                <div className="h-4 bg-slate-700 rounded w-1/3" />
                <div className="h-4 bg-slate-700 rounded w-16" />
              </div>
              <div className="h-3 bg-slate-700/60 rounded w-full" />
              <div className="h-3 bg-slate-700/60 rounded w-5/6" />
              <div className="h-1.5 bg-slate-700/40 rounded-full w-full mt-3" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function SearchView() {
  const [query, setQuery] = useState('')
  const [response, setResponse] = useState<SearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deletedIds, setDeletedIds] = useState<Set<string>>(new Set())
  const [page, setPage] = useState(1)
  const [latencyInfo, setLatencyInfo] = useState<LatencyInfo | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const searchGenRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const doSearch = useCallback(async (q: string, p = 1, signal?: AbortSignal) => {
    if (!q.trim()) return

    searchGenRef.current++
    const gen = searchGenRef.current

    setLoading(true)
    setError(null)
    // Keep previous latency visible but dimmed while new request is in-flight
    setLatencyInfo((prev) => (prev ? { ...prev, stale: true } : null))

    const startTime = Date.now()
    try {
      const res = await searchDocuments({ q: q.trim(), page: p, size: 10, signal })
      if (gen !== searchGenRef.current) return
      const roundTrip = Date.now() - startTime
      setResponse(res)
      setPage(p)
      setLatencyInfo({ server: res.query_time_ms, roundTrip, stale: false })
    } catch (err) {
      if (gen !== searchGenRef.current) return
      const isCancelled =
        (err instanceof Error && (err.name === 'CanceledError' || err.name === 'AbortError')) ||
        (err as { code?: string })?.code === 'ERR_CANCELED'
      if (isCancelled) return
      setError(err instanceof Error ? err.message : 'Search failed')
      setLatencyInfo((prev) => (prev ? { ...prev, stale: false } : null))
    } finally {
      if (gen === searchGenRef.current) {
        setLoading(false)
      }
    }
  }, [])

  // Debounced auto-search on every keystroke
  useEffect(() => {
    if (query.trim().length < 2) {
      abortRef.current?.abort()
      setResponse(null)
      setError(null)
      setLatencyInfo(null)
      setLoading(false)
      return
    }

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const timer = setTimeout(() => {
      doSearch(query, 1, controller.signal)
    }, 300)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query, doSearch])

  const handleClear = () => {
    abortRef.current?.abort()
    setQuery('')
    setResponse(null)
    setError(null)
    setLatencyInfo(null)
    setLoading(false)
    inputRef.current?.focus()
  }

  const handleManualSearch = () => {
    if (!query.trim()) return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    doSearch(query, 1, controller.signal)
  }

  const handleDelete = (docId: string) => {
    setDeletedIds((prev) => new Set([...prev, docId]))
  }

  const visibleResults: SearchResultItem[] = response
    ? response.results.filter((r) => !deletedIds.has(r.doc_id))
    : []

  const totalPages = response ? Math.ceil(response.total / 10) : 0

  const inputPrClass = loading && query ? 'pr-16' : query ? 'pr-10' : 'pr-4'

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Page header */}
      <div>
        <h2 className="text-white text-2xl font-bold">Search Documents</h2>
        <p className="text-slate-400 text-sm mt-1">
          Full-text search across all indexed documents. Results include highlighted snippets with page hints.
        </p>
      </div>

      {/* Search bar */}
      <div className="flex gap-3">
        <div className="relative flex-1">
          {/* Left: magnifier icon */}
          <div className="absolute inset-y-0 left-3.5 flex items-center pointer-events-none text-slate-400">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>

          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleManualSearch() }}
            placeholder="Search documents… (e.g. &quot;revenue growth Q3&quot;)"
            className={`w-full bg-slate-800 border border-slate-700 rounded-xl pl-11 py-3.5 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-all text-sm ${inputPrClass}`}
          />

          {/* Right side: inline spinner + clear button */}
          <div className="absolute inset-y-0 right-3 flex items-center gap-1.5">
            {loading && (
              <svg
                className="w-4 h-4 animate-spin text-blue-400 flex-shrink-0"
                fill="none"
                viewBox="0 0 24 24"
                aria-label="Searching…"
              >
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {query && (
              <button
                onClick={handleClear}
                className="text-slate-500 hover:text-white transition-colors flex-shrink-0"
                aria-label="Clear search"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>

        <button
          onClick={handleManualSearch}
          disabled={!query.trim() || loading}
          className="px-5 py-3.5 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-semibold text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 flex-shrink-0 shadow-lg shadow-blue-900/20"
        >
          {loading ? (
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          )}
          Search
        </button>
      </div>

      {/* Results meta: "Results for …", count, cached badge, latency pill */}
      {(response || latencyInfo) && (
        <div className="flex flex-wrap items-center justify-between gap-3 animate-fade-in">
          <div className="flex flex-col gap-0.5">
            {response && (
              <p className="text-slate-500 text-xs">
                Results for{' '}
                <span className="text-slate-300 font-medium">&ldquo;{response.query}&rdquo;</span>
              </p>
            )}
            <div className="flex items-center gap-2">
              {response && (
                <span className="text-sm text-slate-400">
                  <span className="text-white font-semibold">{response.total}</span>{' '}
                  result{response.total !== 1 ? 's' : ''}
                </span>
              )}
              {response?.cached && (
                <span className="px-2 py-0.5 rounded-full bg-emerald-900/30 text-emerald-400 text-xs border border-emerald-700/30">
                  ⚡ cached
                </span>
              )}
            </div>
          </div>

          {latencyInfo && <LatencyBadge info={latencyInfo} />}
        </div>
      )}

      {/* Skeleton — only when no previous results to show */}
      {loading && !response && <SearchSkeleton />}

      {/* Error */}
      {error && !loading && (
        <div className="flex items-start gap-3 bg-red-950/30 border border-red-700/40 rounded-xl px-4 py-4 animate-fade-in">
          <span className="text-red-400 text-lg mt-0.5">⚠️</span>
          <div>
            <p className="text-red-300 font-medium text-sm">Search failed</p>
            <p className="text-red-400/80 text-xs mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* Results — dimmed while a new search is loading */}
      {visibleResults.length > 0 && (
        <div className={`space-y-3 transition-opacity duration-200 ${loading ? 'opacity-50 pointer-events-none' : ''}`}>
          {visibleResults.map((result, i) => (
            <SearchResult
              key={result.doc_id}
              result={result}
              rank={(page - 1) * 10 + i + 1}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {/* Empty state — query returned zero hits */}
      {!loading && !error && response && visibleResults.length === 0 && (
        <div className="text-center py-16 animate-fade-in">
          <p className="text-5xl mb-4">🔍</p>
          <p className="text-white font-semibold text-lg">No results found</p>
          <p className="text-slate-400 text-sm mt-2 max-w-sm mx-auto">
            Try different keywords or upload more documents. Search uses Elasticsearch full-text matching.
          </p>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && !loading && (
        <div className="flex items-center justify-center gap-2 pt-2 animate-fade-in">
          <button
            onClick={() => {
              abortRef.current?.abort()
              const controller = new AbortController()
              abortRef.current = controller
              doSearch(query, page - 1, controller.signal)
            }}
            disabled={page === 1}
            className="px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 text-sm hover:border-slate-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            ← Prev
          </button>
          <span className="text-slate-400 text-sm px-2">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => {
              abortRef.current?.abort()
              const controller = new AbortController()
              abortRef.current = controller
              doSearch(query, page + 1, controller.signal)
            }}
            disabled={page >= totalPages}
            className="px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 text-sm hover:border-slate-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Next →
          </button>
        </div>
      )}

      {/* Initial welcome state */}
      {!response && !loading && !error && (
        <div className="text-center py-20 animate-fade-in">
          <div className="w-20 h-20 rounded-2xl bg-slate-800/60 flex items-center justify-center text-4xl mx-auto mb-4">
            🗂️
          </div>
          <p className="text-slate-300 font-semibold text-lg">Start searching</p>
          <p className="text-slate-500 text-sm mt-2 max-w-xs mx-auto">
            Enter keywords above to search across all your indexed documents.
          </p>
          <div className="flex flex-wrap justify-center gap-2 mt-5">
            {['annual report', 'invoice', 'contract', 'meeting notes'].map((hint) => (
              <button
                key={hint}
                onClick={() => setQuery(hint)}
                className="px-3 py-1.5 rounded-full bg-slate-800 border border-slate-700 text-slate-400 text-xs hover:border-blue-600/50 hover:text-slate-200 transition-colors"
              >
                {hint}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
