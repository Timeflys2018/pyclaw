import { useEffect } from 'react'
import { AuthProvider, useAuth } from './context/AuthContext'
import Login from './pages/Login'
import Chat from './pages/Chat'
import { usePermissionStore, type PermissionTier } from './stores'

function Router() {
  const { token } = useAuth()
  return token ? <Chat /> : <Login />
}

function useBackendSettingsBootstrap() {
  const applyBackendDefault = usePermissionStore((s) => s.applyBackendDefault)
  useEffect(() => {
    let cancelled = false
    fetch('/api/settings')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return
        const tier = data.default_permission_tier as PermissionTier | undefined
        if (tier) applyBackendDefault(tier)
      })
      .catch(() => {
        // bootstrap is best-effort; localStorage / 'approval' fallback already wins.
      })
    return () => {
      cancelled = true
    }
  }, [applyBackendDefault])
}

export default function App() {
  useBackendSettingsBootstrap()
  return (
    <AuthProvider>
      <Router />
    </AuthProvider>
  )
}
