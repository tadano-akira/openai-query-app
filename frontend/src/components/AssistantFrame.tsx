import { useQuery } from '@tanstack/react-query'
import { useContext, useState } from 'react'

import { api } from '@/lib/api'
import { CharacterVisibleContext } from '@/lib/prefs'

/** 回答に表示するキャラクター（backend/character/ で設定、見た目のみ） */
function useCharacter() {
  const { data } = useQuery({ queryKey: ['character'], queryFn: api.getCharacter, staleTime: 60_000 })
  return {
    name: data?.name ?? 'アシスタント',
    avatarUrl: data?.avatar_url ?? null,
    portraitUrl: data?.portrait_url ?? null,
  }
}

function Avatar({ name, url }: { name: string; url: string | null }) {
  const [broken, setBroken] = useState(false)
  if (url && !broken) {
    return (
      <img
        src={url}
        alt=""
        onError={() => setBroken(true)}
        className="bg-muted size-20 shrink-0 rounded-2xl object-cover shadow-sm"
      />
    )
  }
  // 画像が無い・読めない場合は名前の頭文字
  return (
    <div className="bg-primary text-primary-foreground flex size-20 shrink-0 items-center justify-center rounded-2xl text-2xl font-medium">
      {name.slice(0, 1)}
    </div>
  )
}

/** アシスタントの発言枠: 左にアイコン、上に名前、その下に本文 */
export function AssistantFrame({ children }: { children: React.ReactNode }) {
  const { name, avatarUrl } = useCharacter()
  if (!useContext(CharacterVisibleContext)) {
    // キャラクター表示 OFF: 顔は出さず、名前は「AI」
    return (
      <div className="space-y-2">
        <div className="text-sm leading-6 font-semibold">AI</div>
        {children}
      </div>
    )
  }
  return (
    <div className="flex gap-4">
      <Avatar key={avatarUrl ?? 'none'} name={name} url={avatarUrl} />
      <div className="min-w-0 flex-1 space-y-2">
        <div className="pt-1.5 text-base leading-6 font-semibold">{name}</div>
        {children}
      </div>
    </div>
  )
}

/** チャット欄の右に出す大きな立ち絵。画像が無い、または画面幅が狭い（xl 未満）ときは出さない */
export function CharacterPanel() {
  const { name, portraitUrl } = useCharacter()
  const [broken, setBroken] = useState<string | null>(null)
  const visible = useContext(CharacterVisibleContext)
  if (!visible || !portraitUrl || broken === portraitUrl) return null
  return (
    <aside className="hidden w-72 shrink-0 flex-col items-center gap-3 overflow-y-auto border-l p-5 xl:flex">
      <img
        src={portraitUrl}
        alt={name}
        onError={() => setBroken(portraitUrl)}
        className="bg-muted aspect-[2/3] w-full rounded-2xl object-cover shadow-sm"
      />
      <div className="text-base font-semibold">{name}</div>
    </aside>
  )
}
