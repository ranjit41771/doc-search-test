import { useState } from 'react'
import { login, register } from '../api/auth'
import type { AuthState } from '../types'

const DEMO_TENANTS = [
  { id: 'tenant-a', label: 'Tenant A', email: 'admin@tenant-a.com', password: 'TenantA1!', name: 'Tenant Alpha' },
  { id: 'tenant-b', label: 'Tenant B', email: 'admin@tenant-b.com', password: 'TenantB1!', name: 'Tenant Beta' },
  { id: 'tenant-c', label: 'Tenant C', email: 'admin@tenant-c.com', password: 'TenantC1!', name: 'Tenant Gamma' },
]

interface Props {
  onAuth: (state: AuthState) => void
}

type Tab = 'login' | 'register'

export function AuthModal({ onAuth }: Props) {
  const [tab, setTab] = useState<Tab>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [tenantId, setTenantId] = useState('')
  const [name, setName] = useState('')
  const [plan, setPlan] = useState<'free' | 'standard' | 'enterprise'>('free')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [demoLoading, setDemoLoading] = useState<string | null>(null)

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const res = await login({ email, password })
      localStorage.setItem('docsearch_token', res.access_token)
      onAuth({ token: res.access_token, tenant_id: res.tenant_id, name: res.name, plan: res.plan })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const res = await register({ tenant_id: tenantId, name, email, password, plan })
      localStorage.setItem('docsearch_token', res.access_token)
      onAuth({ token: res.access_token, tenant_id: res.tenant_id, name: res.name, plan: res.plan })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setLoading(false)
    }
  }

  const handleDemoLogin = async (demo: (typeof DEMO_TENANTS)[number]) => {
    setDemoLoading(demo.id)
    setError(null)
    try {
      // Try login first; if not registered, auto-register then login
      let res
      try {
        res = await login({ email: demo.email, password: demo.password })
      } catch {
        await register({
          tenant_id: demo.id,
          name: demo.name,
          email: demo.email,
          password: demo.password,
          plan: 'enterprise',
        })
        res = await login({ email: demo.email, password: demo.password })
      }
      localStorage.setItem('docsearch_token', res.access_token)
      onAuth({ token: res.access_token, tenant_id: res.tenant_id, name: res.name, plan: res.plan })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Demo login failed')
    } finally {
      setDemoLoading(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md bg-slate-900 border border-slate-700/50 rounded-2xl shadow-2xl shadow-black/50 animate-slide-up">
        {/* Header */}
        <div className="px-6 pt-6 pb-4 border-b border-slate-800">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center text-lg">
              🔍
            </div>
            <h1 className="text-white text-xl font-bold">DocSearch</h1>
          </div>
          <p className="text-slate-400 text-sm">Sign in to access your document workspace</p>
        </div>

        <div className="p-6">
          {/* Demo quick-login */}
          <div className="mb-5">
            <p className="text-slate-400 text-xs font-medium uppercase tracking-wider mb-2">Quick Demo</p>
            <div className="grid grid-cols-3 gap-2">
              {DEMO_TENANTS.map((d) => (
                <button
                  key={d.id}
                  onClick={() => handleDemoLogin(d)}
                  disabled={demoLoading !== null || loading}
                  className="relative py-2 px-3 rounded-lg bg-slate-800 border border-slate-700 hover:border-blue-600/50 hover:bg-slate-700/80 text-slate-300 text-xs font-medium transition-all disabled:opacity-50"
                >
                  {demoLoading === d.id ? (
                    <span className="flex items-center justify-center gap-1.5">
                      <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                    </span>
                  ) : (
                    d.label
                  )}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3 mb-5">
            <div className="flex-1 h-px bg-slate-700" />
            <span className="text-slate-500 text-xs">or</span>
            <div className="flex-1 h-px bg-slate-700" />
          </div>

          {/* Tabs */}
          <div className="flex gap-1 mb-5 bg-slate-800/60 rounded-lg p-1">
            {(['login', 'register'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => { setTab(t); setError(null) }}
                className={`flex-1 py-1.5 rounded-md text-sm font-medium transition-all capitalize ${
                  tab === t
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          {/* Login form */}
          {tab === 'login' && (
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <label className="block text-slate-300 text-sm mb-1.5">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  placeholder="admin@example.com"
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
                />
              </div>
              <div>
                <label className="block text-slate-300 text-sm mb-1.5">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  placeholder="••••••••"
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
                />
              </div>
              {error && <p className="text-red-400 text-sm bg-red-950/30 rounded-lg px-3 py-2 border border-red-800/40">{error}</p>}
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-semibold text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {loading && (
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                )}
                Sign In
              </button>
            </form>
          )}

          {/* Register form */}
          {tab === 'register' && (
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-300 text-sm mb-1.5">Tenant ID</label>
                  <input
                    type="text"
                    value={tenantId}
                    onChange={(e) => setTenantId(e.target.value)}
                    required
                    placeholder="my-company"
                    pattern="^[a-z0-9][a-z0-9\-]+[a-z0-9]$"
                    title="Lowercase letters, numbers, hyphens only"
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-slate-300 text-sm mb-1.5">Name</label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    placeholder="Acme Corp"
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
                  />
                </div>
              </div>
              <div>
                <label className="block text-slate-300 text-sm mb-1.5">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  placeholder="admin@example.com"
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
                />
              </div>
              <div>
                <label className="block text-slate-300 text-sm mb-1.5">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  placeholder="Min 8 chars, 1 upper, 1 digit"
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
                />
              </div>
              <div>
                <label className="block text-slate-300 text-sm mb-1.5">Plan</label>
                <select
                  value={plan}
                  onChange={(e) => setPlan(e.target.value as 'free' | 'standard' | 'enterprise')}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
                >
                  <option value="free">Free (100 req/min)</option>
                  <option value="standard">Standard (500 req/min)</option>
                  <option value="enterprise">Enterprise (1000 req/min)</option>
                </select>
              </div>
              {error && <p className="text-red-400 text-sm bg-red-950/30 rounded-lg px-3 py-2 border border-red-800/40">{error}</p>}
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-semibold text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {loading && (
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                )}
                Create Account
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
