import { FileText, Search, Upload } from 'lucide-react'

import { AccountBar } from '@/components/AccountBar'
import { AppNav } from '@/components/AppNav'
import { ChunkViewer } from '@/components/docs/ChunkViewer'
import { DocumentList } from '@/components/docs/DocumentList'
import { EvidenceViewer } from '@/components/docs/EvidenceViewer'
import { ReviewPage } from '@/components/docs/ReviewPage'
import { SearchPage } from '@/components/docs/SearchPage'
import { UploadForm } from '@/components/docs/UploadForm'
import { VersionDetail } from '@/components/docs/VersionDetail'
import { match, navigate, useLocation } from '@/lib/router'
import { cn } from '@/lib/utils'

function NavItem({ to, active, icon: Icon, children }: { to: string; active: boolean; icon: typeof FileText; children: React.ReactNode }) {
  return (
    <button
      onClick={() => navigate(to)}
      className={cn('hover:bg-muted flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm', active && 'bg-muted font-medium')}
    >
      <Icon className="size-4" /> {children}
    </button>
  )
}

/** 資料Q&A（段階 1〜3: PDF 登録・抽出・レビュー・根拠表示） */
export function DocsApp() {
  const { path, params } = useLocation()
  let content: React.ReactNode
  let m: Record<string, string> | null
  if ((m = match('/docs/versions/:id', path))) content = <VersionDetail key={m.id} versionId={m.id} />
  else if ((m = match('/docs/extractions/:id/review', path)))
    content = <ReviewPage extractionId={m.id} pageNumber={Math.max(1, Number(params.get('page')) || 1)} />
  else if ((m = match('/viewer/evidence/:id', path)))
    content = <EvidenceViewer elementId={m.id} mode={params.get('mode') === 'html' ? 'html' : 'pdf'} />
  else if ((m = match('/viewer/chunk/:id', path))) content = <ChunkViewer key={m.id} chunkId={m.id} />
  else if (match('/docs/search', path)) content = <SearchPage runId={params.get('run')} />
  else if (match('/docs/upload', path)) content = <UploadForm key={params.get('document') ?? 'new'} documentId={params.get('document')} />
  else content = <DocumentList />
  const fullHeight = path.includes('/review') || path.startsWith('/viewer/')

  return (
    <div className="flex h-dvh">
      <aside className="bg-muted/30 flex w-60 shrink-0 flex-col border-r">
        <div className="space-y-3 p-3">
          <AppNav current="docs" />
          <nav className="space-y-0.5">
            <NavItem to="/docs" active={path === '/docs'} icon={FileText}>
              資料一覧
            </NavItem>
            <NavItem to="/docs/upload" active={path === '/docs/upload'} icon={Upload}>
              PDFを登録
            </NavItem>
            <NavItem to="/docs/search" active={path === '/docs/search'} icon={Search}>
              検索（試験）
            </NavItem>
          </nav>
        </div>
        <div className="text-muted-foreground mt-auto space-y-1 p-3 text-[11px]">
          <p>引用つきの回答生成は次の段階（段階5）で追加します。今は根拠の候補を検索できます。</p>
          <p>OCRは使いません。画像だけのページは検索対象外です。</p>
        </div>
        <AccountBar />
      </aside>
      <main className={cn('min-w-0 flex-1', fullHeight ? 'flex flex-col' : 'overflow-y-auto')}>{content}</main>
    </div>
  )
}
