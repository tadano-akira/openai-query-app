import { createContext, useEffect, useState } from 'react'

/**
 * 開閉・表示切替などの表示設定。閲覧者ごとの設定としてブラウザに保存する（読み書きできなければ既定値のまま動く）
 */
export function usePersistentFlag(
  key: string,
  initial = true,
): [boolean, (update: (v: boolean) => boolean) => void] {
  const [value, setValue] = useState(() => {
    try {
      const saved = localStorage.getItem(key)
      return saved === null ? initial : saved === 'true'
    } catch {
      return initial
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, String(value))
    } catch {
      // 保存できなくても表示には影響しない
    }
  }, [key, value])
  return [value, setValue]
}

/** キャラクター（顔・名前）を表示するか。OFF のときは顔を出さず、名前を「AI」にする */
export const CharacterVisibleContext = createContext(true)
