import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import App from '@/App'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError, api, setUnauthorizedHandler } from '@/lib/api'
import { meQuery, resetToLoggedOut } from '@/lib/auth'

function LoginPage() {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const me = await api.login(email, password)
      queryClient.setQueryData(meQuery.queryKey, me)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
      setPassword('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="bg-header flex min-h-dvh items-center justify-center p-4">
      <form onSubmit={submit} className="bg-background w-full max-w-sm space-y-4 rounded-2xl border p-6 shadow-sm">
        <div className="space-y-1">
          <h1 className="text-lg font-semibold">OpenAI Query App</h1>
          <p className="text-muted-foreground text-sm">メールアドレスとパスワードでログインしてください。</p>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertTitle>ログインできません</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="email">メールアドレス</Label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">パスワード</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting && <Loader2 className="animate-spin" />}
          ログイン
        </Button>
      </form>
    </div>
  )
}

/** ログイン状態を確かめ、未ログインならログイン画面、ログイン済みならアプリを表示する */
export function AuthGate() {
  const queryClient = useQueryClient()
  const me = useQuery(meQuery)

  useEffect(() => {
    // 期限切れなどで API が 401 を返したら、画面のデータを消してログイン画面へ戻す
    setUnauthorizedHandler(() => resetToLoggedOut(queryClient))
    return () => setUnauthorizedHandler(null)
  }, [queryClient])

  if (me.isPending) {
    return (
      <div className="text-muted-foreground flex min-h-dvh items-center justify-center">
        <Loader2 className="size-5 animate-spin" />
      </div>
    )
  }
  if (me.isError) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-4">
        <Alert variant="destructive" className="max-w-sm">
          <AlertTitle>サーバーに接続できません</AlertTitle>
          <AlertDescription>
            {String(me.error instanceof Error ? me.error.message : me.error)}
            <Button variant="outline" size="sm" className="mt-2" onClick={() => void me.refetch()}>
              再試行
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    )
  }
  return me.data ? <App /> : <LoginPage />
}
