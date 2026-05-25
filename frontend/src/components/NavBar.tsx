import type { AuthState } from '../types'

type View = 'upload' | 'search'

interface Props {
  view: View
  onViewChange: (v: View) => void
  auth: AuthState | null
  onLogout: () => void
}

const PLAN_COLORS: Record<string, string> = {
  free: 'bg-slate-700 text-slate-300',
  standard: 'bg-blue-900/50 text-blue-300',
  enterprise: 'bg-indigo-900/50 text-indigo-300',
}

export function NavBar({ view, onViewChange, auth, onLogout }: Props) {
  return (
    <nav className="sticky top-0 z-40 border-b border-slate-800 bg-slate-950/90 backdrop-blur-md">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
        {/* Brand */}
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center text-base shadow-lg shadow-blue-900/30">
            🔍
          </div>
          <span className="text-white font-bold text-lg tracking-tight">DocSearch</span>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 bg-slate-800/60 rounded-lg p-1 border border-slate-700/50">
          {(['upload', 'search'] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => onViewChange(v)}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all capitalize ${
                view === v
                  ? 'bg-blue-600 text-white shadow-sm shadow-blue-900/50'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
              }`}
            >
              {v === 'upload' ? '⬆️ Upload' : '🔎 Search'}
            </button>
          ))}
        </div>

        {/* Auth info */}
        {auth ? (
          <div className="flex items-center gap-3">
            <div className="hidden sm:flex flex-col items-end">
              <span className="text-white text-xs font-medium leading-none">{auth.name}</span>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="text-slate-500 text-xs font-mono">{auth.tenant_id}</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${PLAN_COLORS[auth.plan] ?? PLAN_COLORS.free}`}>
                  {auth.plan}
                </span>
              </div>
            </div>
            <button
              onClick={onLogout}
              title="Sign out"
              className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700/60 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
            </button>
          </div>
        ) : (
          <div className="w-24" />
        )}
      </div>
    </nav>
  )
}
