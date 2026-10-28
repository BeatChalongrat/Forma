import { useEffect, useState } from 'react'
import { handleCallback } from '../lib/auth'

/** OIDC redirect target (/auth/callback): exchanges the code for a token, then continues. */
export function AuthCallback() {
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    handleCallback()
      .then((back) => window.location.replace(back))
      .catch((e: Error) => setError(e.message))
  }, [])
  return (
    <div className="h-full grid place-items-center text-muted">
      {error ? <div className="card p-6 max-w-md"><div className="font-medium text-bad">Sign-in failed</div><div className="mt-1 text-[13px]">{error}</div><a className="btn btn-primary mt-4" href="/">Try again</a></div> : 'Signing in…'}
    </div>
  )
}
