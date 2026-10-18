import { useQuery } from '@tanstack/react-query'
import { LogOut } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { meQuery, useLogout } from '@/lib/auth'

/** ログイン中のアカウントとログアウト */
export function AccountBar() {
  const { data: me } = useQuery(meQuery)
  const logout = useLogout()
  if (!me) return null
  return (
    <div className="flex items-center justify-between gap-2 border-t px-3 py-2">
      <span className="text-muted-foreground truncate text-xs" title={me.email}>
        {me.email}
      </span>
      <Button variant="ghost" size="xs" className="shrink-0" onClick={() => void logout()}>
        <LogOut /> ログアウト
      </Button>
    </div>
  )
}
