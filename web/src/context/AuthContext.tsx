import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
} from 'react'
import type { AuthTokenResponse } from '../types'

interface AuthState {
  token: string | null
  userId: string | null
  isAdmin: boolean
  login: (userId: string, password: string) => Promise<void>
  logout: () => void
  loginError: string | null
  loading: boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null)
  const [userId, setUserId] = useState<string | null>(null)
  const [isAdmin, setIsAdmin] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const login = useCallback(async (uid: string, password: string) => {
    setLoading(true)
    setLoginError(null)
    try {
      const res = await fetch('/api/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid, password }),
      })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Login failed (${res.status})`)
      }
      const data: AuthTokenResponse = await res.json()
      setToken(data.token)
      setUserId(uid)

      try {
        const payload = JSON.parse(atob(data.token.split('.')[1]))
        setIsAdmin(payload.admin === true || payload.role === 'admin')
      } catch {
        setIsAdmin(false)
      }
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setUserId(null)
    setIsAdmin(false)
  }, [])

  return (
    <AuthContext.Provider
      value={{ token, userId, isAdmin, login, logout, loginError, loading }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}
