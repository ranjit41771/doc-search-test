import { useEffect, useState } from 'react'
import { NavBar } from './components/NavBar'
import { UploadView } from './components/UploadView'
import { SearchView } from './components/SearchView'
import { AuthModal } from './components/AuthModal'
import type { AuthState, RecentUpload } from './types'

type View = 'upload' | 'search'

const MAX_RECENT = 5

function tryRestoreAuth(): AuthState | null {
  const token = localStorage.getItem('docsearch_token')
  const tenant_id = localStorage.getItem('docsearch_tenant_id')
  const name = localStorage.getItem('docsearch_name')
  const plan = localStorage.getItem('docsearch_plan')
  if (token && tenant_id && name && plan) {
    return { token, tenant_id, name, plan }
  }
  return null
}

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(tryRestoreAuth)
  const [view, setView] = useState<View>('upload')
  const [recentUploads, setRecentUploads] = useState<RecentUpload[]>([])

  useEffect(() => {
    if (auth) {
      localStorage.setItem('docsearch_token', auth.token)
      localStorage.setItem('docsearch_tenant_id', auth.tenant_id)
      localStorage.setItem('docsearch_name', auth.name)
      localStorage.setItem('docsearch_plan', auth.plan)
    }
  }, [auth])

  const handleAuth = (state: AuthState) => {
    setAuth(state)
    setRecentUploads([])
  }

  const handleLogout = () => {
    localStorage.removeItem('docsearch_token')
    localStorage.removeItem('docsearch_tenant_id')
    localStorage.removeItem('docsearch_name')
    localStorage.removeItem('docsearch_plan')
    setAuth(null)
    setRecentUploads([])
  }

  const handleUploaded = (upload: RecentUpload) => {
    setRecentUploads((prev) => [upload, ...prev].slice(0, MAX_RECENT))
  }

  const handleDelete = (docId: string) => {
    setRecentUploads((prev) => prev.filter((u) => u.doc_id !== docId))
  }

  return (
    <div className="min-h-screen bg-slate-950">
      {!auth && <AuthModal onAuth={handleAuth} />}

      <NavBar
        view={view}
        onViewChange={setView}
        auth={auth}
        onLogout={handleLogout}
      />

      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
        {auth ? (
          view === 'upload' ? (
            <UploadView
              recentUploads={recentUploads}
              onUploaded={handleUploaded}
              onDelete={handleDelete}
            />
          ) : (
            <SearchView />
          )
        ) : (
          // Dim background when modal is open
          <div className="opacity-20 pointer-events-none select-none">
            <UploadView recentUploads={[]} onUploaded={() => {}} onDelete={() => {}} />
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800/50 mt-16 py-6">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 flex items-center justify-between text-slate-600 text-xs">
          <span>DocSearch — Distributed Document Search</span>
          <span>
            {auth && (
              <>
                Tenant: <span className="font-mono text-slate-500">{auth.tenant_id}</span>
              </>
            )}
          </span>
        </div>
      </footer>
    </div>
  )
}
