import { useState, type FormEvent } from 'react'
import { useAuth } from '../context/AuthContext'
import { Terminal, ArrowRight, Loader2 } from 'lucide-react'

export default function Login() {
  const { login, loginError, loading } = useAuth()
  const [uid, setUid] = useState('')
  const [pwd, setPwd] = useState('')

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (uid.trim()) login(uid.trim(), pwd)
  }

  return (
    <div className="min-h-screen grid place-items-center bg-[var(--c-bg)] px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-6"
      >
        <div className="flex items-center gap-3 mb-2">
          <div className="p-2.5 rounded-xl bg-[var(--c-accent)] text-white">
            <Terminal size={22} strokeWidth={2.5} />
          </div>
          <h1 className="text-2xl font-display font-semibold tracking-tight text-[var(--c-text)]">
            PyClaw
          </h1>
        </div>

        <p className="text-sm text-[var(--c-text-secondary)] leading-relaxed">
          Sign in to your agent workspace.
        </p>

        {loginError && (
          <div className="px-3 py-2 rounded-lg bg-[var(--c-error)]/10 text-[var(--c-error)] text-sm border border-[var(--c-error)]/20">
            {loginError}
          </div>
        )}

        <div className="space-y-3">
          <input
            type="text"
            placeholder="User ID"
            value={uid}
            onChange={(e) => setUid(e.target.value)}
            autoFocus
            className="w-full h-11 px-3.5 rounded-lg bg-[var(--c-surface)] border border-[var(--c-border)]
                       text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/50
                       focus:outline-none focus:ring-2 focus:ring-[var(--c-accent)]/40 focus:border-[var(--c-accent)]
                       transition-all text-sm"
          />
          <input
            type="password"
            placeholder="Password"
            value={pwd}
            onChange={(e) => setPwd(e.target.value)}
            className="w-full h-11 px-3.5 rounded-lg bg-[var(--c-surface)] border border-[var(--c-border)]
                       text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/50
                       focus:outline-none focus:ring-2 focus:ring-[var(--c-accent)]/40 focus:border-[var(--c-accent)]
                       transition-all text-sm"
          />
        </div>

        <button
          type="submit"
          disabled={loading || !uid.trim()}
          className="w-full h-11 rounded-lg bg-[var(--c-accent)] text-white font-medium text-sm
                     flex items-center justify-center gap-2
                     hover:brightness-110 active:scale-[0.98]
                     disabled:opacity-40 disabled:pointer-events-none
                     transition-all duration-150 cursor-pointer"
        >
          {loading ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <>
              Sign in
              <ArrowRight size={15} />
            </>
          )}
        </button>
      </form>
    </div>
  )
}
