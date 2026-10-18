import { type QueryClient, useQueryClient } from '@tanstack/react-query'

import { ApiError, api, type Me } from '@/lib/api'

/** ログイン中の利用者。未ログインは null（エラーにしない） */
export const meQuery = {
  queryKey: ['me'],
  queryFn: async (): Promise<Me | null> => {
    try {
      return await api.me()
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return null
      throw e
    }
  },
  retry: false,
  staleTime: Infinity,
} as const

/**
 * 未ログイン状態にする。ログイン状態以外のキャッシュを消し、前の利用者のデータを画面に残さない。
 * clear() は使わない（ログイン状態を監視している画面まで購読が切れ、ログイン画面に戻らなくなる）
 */
export function resetToLoggedOut(queryClient: QueryClient) {
  queryClient.setQueryData(meQuery.queryKey, null)
  queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== meQuery.queryKey[0] })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return async () => {
    await api.logout().catch(() => undefined)
    resetToLoggedOut(queryClient)
  }
}
