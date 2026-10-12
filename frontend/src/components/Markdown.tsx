import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const ALLOWED_PROTOCOLS = ['http:', 'https:', 'mailto:']

/** 許可スキーム以外のリンクは無効化する。生HTMLは rehype-raw を使わないため描画されない */
function safeUrl(url: string): string {
  try {
    const u = new URL(url, window.location.origin)
    return ALLOWED_PROTOCOLS.includes(u.protocol) ? url : ''
  } catch {
    return ''
  }
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrl}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer nofollow" />
          ),
          img: () => null,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
