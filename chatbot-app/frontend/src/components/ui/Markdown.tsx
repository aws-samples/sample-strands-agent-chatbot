import React, { memo, useMemo, useState, useEffect } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeRaw from 'rehype-raw';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { CodeBlock } from './CodeBlock';
import { ChartRenderer, ImageRenderer } from '../canvas';
import { CitationLink } from './CitationLink';
import { Loader2 } from 'lucide-react';

// S3 Image component that resolves presigned URLs
const S3Image = ({ src, alt }: { src: string; alt?: string }) => {
  const [resolvedUrl, setResolvedUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const lastFetchedSrcRef = React.useRef<string | null>(null);

  useEffect(() => {
    // Skip if already fetched this exact src
    if (lastFetchedSrcRef.current === src) return;

    // Reset state for new src
    setResolvedUrl(null);
    setLoading(true);
    setError(null);
    lastFetchedSrcRef.current = src;

    const fetchPresignedUrl = async () => {
      try {
        const response = await fetch('/api/s3/presigned-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ s3Key: src })
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.details || errorData.error || 'Failed to get presigned URL');
        }

        const data = await response.json();
        setResolvedUrl(data.url);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load image');
      } finally {
        setLoading(false);
      }
    };

    fetchPresignedUrl();
  }, [src]);

  if (loading) {
    return (
      <span className="inline-flex items-center justify-center p-4 bg-muted/30 rounded-lg">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">Loading image...</span>
      </span>
    );
  }

  if (error || !resolvedUrl) {
    return (
      <span className="inline-flex items-center justify-center p-4 bg-red-50 dark:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400 text-sm">
        {error || 'Image not available'}
      </span>
    );
  }

  return (
    <img
      src={resolvedUrl}
      alt={alt || 'Research image'}
      className="max-w-full h-auto rounded-lg shadow-xs"
    />
  );
};

// Helper to strip <research> XML tags from content
const stripResearchTags = (content: string): string => {
  if (!content) return '';
  // Extract content from <research> tags if present
  const match = content.match(/<research>([\s\S]*?)<\/research>/);
  if (match && match[1]) {
    return match[1].trim();
  }
  // Remove any remaining <research> or </research> tags
  return content.replace(/<\/?research>/g, '');
};

/**
 * Rewrites LaTeX's \[...\] and \(...\) delimiters to the $$...$$ form.
 *
 * remark-math only recognizes dollar delimiters, but models frequently emit the
 * bracket form. Left alone, markdown consumes the backslash and the formula
 * arrives as literal text with a stray "[" on its own line — the exact breakage
 * this fixes.
 *
 * Inline \(...\) becomes $$...$$ too: with singleDollarTextMath disabled, $$ is
 * the only inline form available, and it renders inline when kept on one line.
 *
 * Fenced and inline code are left untouched so documentation that shows these
 * delimiters literally still reads correctly.
 */
const normalizeMathDelimiters = (content: string): string => {
  if (!content.includes('\\[') && !content.includes('\\(')) return content;

  // Split on code spans/fences and only rewrite the segments outside them.
  const segments = content.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return segments
    .map((segment, index) => {
      // Odd indices are the captured code segments.
      if (index % 2 === 1) return segment;
      return segment
        .replace(/\\\[([\s\S]*?)\\\]/g, (_m, body) => `$$${body}$$`)
        .replace(/\\\(([\s\S]*?)\\\)/g, (_m, body) => `$$${body}$$`);
    })
    .join('');
};

/**
 * Normalizes nested code fences so outer fences use more backticks than inner ones.
 * Prevents markdown parsers from prematurely closing outer code blocks when
 * encountering inner fence markers (e.g., ```bash inside ```markdown).
 */
const normalizeCodeFences = (text: string): string => {
  const lines = text.split('\n');
  const fenceRegex = /^(`{3,})(.*)$/;

  const markers: Array<{
    lineIndex: number;
    backticks: number;
    hasInfo: boolean;
  }> = [];

  for (let i = 0; i < lines.length; i++) {
    const match = lines[i].match(fenceRegex);
    if (match) {
      markers.push({
        lineIndex: i,
        backticks: match[1].length,
        hasInfo: match[2].trim().length > 0,
      });
    }
  }

  if (markers.length < 2) return text;

  // Pair fences using stack-based approach:
  // - Fence with info string (language tag) → always opening
  // - Bare fence → closing if inside a block, opening otherwise
  const stack: Array<{ markerIdx: number; maxChildBackticks: number }> = [];
  const adjustments = new Map<number, number>();

  for (let i = 0; i < markers.length; i++) {
    const marker = markers[i];

    if (stack.length === 0 || marker.hasInfo) {
      stack.push({ markerIdx: i, maxChildBackticks: 0 });
    } else {
      const top = stack.pop()!;
      const opening = markers[top.markerIdx];
      const needed = top.maxChildBackticks > 0
        ? top.maxChildBackticks + 1
        : opening.backticks;

      if (stack.length > 0) {
        const parent = stack[stack.length - 1];
        parent.maxChildBackticks = Math.max(parent.maxChildBackticks, needed);
      }

      adjustments.set(opening.lineIndex, Math.max(adjustments.get(opening.lineIndex) || 0, needed));
      adjustments.set(marker.lineIndex, Math.max(adjustments.get(marker.lineIndex) || 0, needed));
    }
  }

  let modified = false;
  const result = lines.map((line, i) => {
    const needed = adjustments.get(i);
    if (needed !== undefined) {
      const match = line.match(fenceRegex);
      if (match && needed > match[1].length) {
        modified = true;
        return '`'.repeat(needed) + match[2];
      }
    }
    return line;
  });

  return modified ? result.join('\n') : text;
};

// Helper function to extract domain from URL
const getDomain = (url: string): string => {
  try {
    const hostname = new URL(url).hostname;
    return hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
};

const components: Partial<Components> = {
  // Style links - show citation chips for external links
  a: ({ node, children, href, ...props }: any) => (
    <CitationLink href={href} {...props}>{children}</CitationLink>
  ),
  // Citation renderer - displays claim text with a clickable source chip
  cite: ({ node, children, ...props }: any) => {
    const source = props.source || '';
    const url = props.url || '';
    const domain = url ? getDomain(url) : '';

    return (
      <span className="citation-inline">
        {children}
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 px-1.5 py-0.5 ml-1 text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 rounded-full hover:bg-blue-100 dark:hover:bg-blue-900 hover:text-blue-600 dark:hover:text-blue-300 no-underline transition-colors align-middle"
            title={source || url}
          >
            <svg className="w-2.5 h-2.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
            <span className="truncate max-w-[100px]">{domain}</span>
          </a>
        )}
      </span>
    );
  },
  // Handle images - resolve S3 presigned URLs
  img: ({ node, src, alt, ...props }: any) => {
    // Skip empty or invalid src
    if (!src) {
      return null;
    }
    // Check if this is an S3 URL
    if (src.startsWith('s3://')) {
      return <S3Image src={src} alt={alt} />;
    }
    // Regular image
    return <img src={src} alt={alt} className="max-w-full h-auto rounded-lg" {...props} />;
  },
  code: ({ className, children, ...props }: any) => {
    const cleanChildren = String(children).replace(/^`+|`+$/g, '');
    return (
      <code
        className="bg-zinc-100 dark:bg-zinc-800 py-0.5 px-1 rounded-md text-label"
        {...props}
      >
        {cleanChildren}
      </code>
    );
  },
  pre: ({ node, children }: any) => {
    const codeChild = node?.children?.find((c: any) => c.tagName === 'code');
    const className = codeChild?.properties?.className?.[0] || '';
    const extractText = (n: any): string => {
      if (n.type === 'text') return n.value || '';
      return (n.children || []).map(extractText).join('');
    };
    const codeContent = codeChild ? extractText(codeChild) : '';
    return (
      <CodeBlock
        node={node}
        inline={false}
        className={className}
      >
        {codeContent}
      </CodeBlock>
    );
  },
  table: ({ children }) => (
    <div className="overflow-x-auto my-4" style={{ width: '100%', maxWidth: '100%', display: 'block' }}>
      <table className="border-collapse" style={{ width: 'auto', minWidth: 'max-content' }}>
        {children}
      </table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-gray-300 dark:border-gray-600 px-4 py-2 bg-gray-100 dark:bg-gray-800 whitespace-pre-wrap">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-gray-300 dark:border-gray-600 px-4 py-2 whitespace-pre-wrap">
      {children}
    </td>
  ),
};

const getRemarkPlugins = (preserveLineBreaks?: boolean) => {
  const plugins: any[] = [
    [remarkGfm, { singleTilde: false }],
    // Display math only: $$...$$ and \[...\].
    //
    // singleDollarTextMath is off because a lone $ is far more often currency or
    // a shell variable than math in this app's answers — "from $5 to $25" would
    // otherwise render as a formula. Two or more dollars still work for inline
    // math when a model really means it.
    [remarkMath, { singleDollarTextMath: false }],
  ];
  if (preserveLineBreaks) {
    plugins.push(remarkBreaks);
  }
  return plugins;
};

// Custom URL transform to allow s3:// protocol (default only allows http, https, mailto, tel)
const customUrlTransform = (url: string): string => {
  // Allow s3:// URLs for images
  if (url.startsWith('s3://')) {
    return url;
  }
  // Allow standard protocols
  if (url.startsWith('http://') || url.startsWith('https://') ||
      url.startsWith('mailto:') || url.startsWith('tel:') ||
      url.startsWith('/') || url.startsWith('#') || url.startsWith('./')) {
    return url;
  }
  // Block other protocols for security
  return '';
};

// Chart code block pattern: ```chart\n{...}\n```
const CHART_CODE_BLOCK_PATTERN = /```chart\n([\s\S]*?)\n```/g;

// Chart reference pattern: [CHART:chart_name]
const CHART_REF_PATTERN = /\[CHART:([^\]]+)\]/g;

// Image pattern: [IMAGE:filename:alt_text]
const IMAGE_PATTERN = /\[IMAGE:([^:]+):([^\]]+)\]/g;

const parseContentWithCharts = (content: string) => {
  const parts: Array<{ type: 'text' | 'chart' | 'chartRef' | 'image'; content: string; chartData?: any; chartName?: string; imageId?: string; altText?: string }> = [];
  const patterns = [
    { regex: CHART_CODE_BLOCK_PATTERN, type: 'chart' as const },
    { regex: CHART_REF_PATTERN, type: 'chartRef' as const },
    { regex: IMAGE_PATTERN, type: 'image' as const }
  ];
  
  // Find all matches from all patterns
  const allMatches: Array<{ match: RegExpExecArray; type: 'chart' | 'chartRef' | 'image' }> = [];
  
  for (const pattern of patterns) {
    pattern.regex.lastIndex = 0; // Reset regex
    let match;
    while ((match = pattern.regex.exec(content)) !== null) {
      allMatches.push({ match, type: pattern.type });
    }
  }
  
  // Sort matches by position
  allMatches.sort((a, b) => a.match.index - b.match.index);
  
  let lastIndex = 0;
  
  for (const { match, type } of allMatches) {
    // Add text before the match
    if (match.index > lastIndex) {
      const textContent = content.slice(lastIndex, match.index);
      if (textContent) {
        parts.push({ type: 'text', content: textContent });
      }
    }

    // Add chart, chartRef, or image
    if (type === 'chart') {
      try {
        // Parse the JSON chart data
        const chartData = JSON.parse(match[1]);
        parts.push({
          type: 'chart',
          content: match[0],
          chartData: chartData
        });
      } catch (error) {
        console.error('Failed to parse chart JSON:', error);
        // If parsing fails, treat as regular text
        parts.push({ type: 'text', content: match[0] });
      }
    } else if (type === 'chartRef') {
      parts.push({
        type: 'chartRef',
        content: match[0],
        chartName: match[1]
      });
    } else if (type === 'image') {
      parts.push({
        type: 'image',
        content: match[0],
        imageId: match[1],
        altText: match[2]
      });
    }

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < content.length) {
    const remainingContent = content.slice(lastIndex);
    if (remainingContent) {
      parts.push({ type: 'text', content: remainingContent });
    }
  }

  // If no matches found, return original content as text
  if (parts.length === 0) {
    parts.push({ type: 'text', content });
  }

  return parts;
};

const NonMemoizedMarkdown = ({
  children,
  size = 'sm',
  preserveLineBreaks = false,
  sessionId,
  toolUseId
}: {
  children: string;
  size?: 'sm' | 'base' | 'lg' | 'xl' | '2xl';
  preserveLineBreaks?: boolean;
  sessionId?: string;
  toolUseId?: string;
}) => {
  // Memoize parsing result with fence normalization to avoid re-parsing on every render
  const parts = useMemo(() => {
    const parsed = parseContentWithCharts(children);
    return parsed.map(part =>
      part.type === 'text'
        ? {
            ...part,
            content: normalizeMathDelimiters(
              normalizeCodeFences(stripResearchTags(part.content)),
            ),
          }
        : part
    );
  }, [children]);
  const remarkPlugins = useMemo(() => getRemarkPlugins(preserveLineBreaks), [preserveLineBreaks]);
  // rehypeRaw first: it parses embedded HTML, and running KaTeX before it would
  // let raw-HTML handling mangle the markup KaTeX produces. throwOnError keeps a
  // malformed formula from taking down the whole message render.
  const rehypePlugins = useMemo(
    () => [rehypeRaw, [rehypeKatex, { throwOnError: false, strict: false }]] as any[],
    [],
  );

  // Font size mapping (in pixels)
  const fontSizeMap: Record<string, string> = {
    'sm': '14px',
    'base': '15px',
    'lg': '16px',
    'xl': '18px',
    '2xl': '17px'
  };
  const fontSize = fontSizeMap[size] || '15px';

  const proseClass = `prose max-w-none dark:prose-invert prose-headings:font-semibold prose-headings:mt-5 prose-headings:mb-2 prose-p:leading-relaxed prose-p:my-3 prose-li:py-1 prose-li:leading-relaxed prose-ul:my-3 prose-ol:my-3 prose-li:my-0 break-words min-w-0 ai-message-text`;

  return (
    <div className={proseClass} style={{ width: '100%', maxWidth: '100%', wordBreak: 'break-word', overflowWrap: 'anywhere', '--ai-font-size': fontSize } as React.CSSProperties}>
      {parts.map((part, index) => {
        if (part.type === 'chart' && part.chartData) {
          return (
            <div key={index} className="my-6 not-prose">
              <ChartRenderer chartData={part.chartData} />
            </div>
          );
        } else if (part.type === 'image' && part.imageId) {
          return (
            <div key={index} className="my-6 not-prose">
              <ImageRenderer 
                imageId={part.imageId} 
                altText={part.altText}
                sessionId={sessionId}
                toolUseId={toolUseId}
              />
            </div>
          );
        } else {
          return (
            <ReactMarkdown
              key={index}
              remarkPlugins={remarkPlugins}
              rehypePlugins={rehypePlugins}
              components={components}
              urlTransform={customUrlTransform}
            >
              {part.content}
            </ReactMarkdown>
          );
        }
      })}
    </div>
  );
};

export const Markdown = memo(
  NonMemoizedMarkdown,
  (prevProps, nextProps) => 
    prevProps.children === nextProps.children && 
    prevProps.size === nextProps.size &&
    prevProps.preserveLineBreaks === nextProps.preserveLineBreaks,
);
