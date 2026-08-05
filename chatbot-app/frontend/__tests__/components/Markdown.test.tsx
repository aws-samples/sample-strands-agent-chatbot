/**
 * Tests for Markdown component
 *
 * Tests cover:
 * - Citation rendering (<cite> tags with source and url attributes)
 * - Incomplete cite tag hiding during streaming
 * - Domain extraction from URLs
 * - Basic markdown rendering
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Markdown } from '@/components/ui/Markdown'

// Mock ChartRenderer
vi.mock('@/components/ChartRenderer', () => ({
  ChartRenderer: ({ chartData }: { chartData: any }) => (
    <div data-testid="chart-renderer">{JSON.stringify(chartData)}</div>
  )
}))

// Mock ImageRenderer
vi.mock('@/components/ImageRenderer', () => ({
  ImageRenderer: ({ imageId, altText }: { imageId: string; altText?: string }) => (
    <div data-testid="image-renderer">{imageId} - {altText}</div>
  )
}))

describe('Markdown', () => {
  describe('Basic Rendering', () => {
    it('should render plain text', () => {
      render(<Markdown>Hello World</Markdown>)
      expect(screen.getByText('Hello World')).toBeInTheDocument()
    })

    it('should render markdown headings', () => {
      render(<Markdown>{'# Heading 1\n\n## Heading 2'}</Markdown>)
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Heading 1')
      expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Heading 2')
    })

    it('should render markdown links as citation chips for external URLs', () => {
      render(<Markdown>{'[Click here](https://example.com)'}</Markdown>)
      // External links are rendered as domain chips via CitationLink
      const link = screen.getByRole('link')
      expect(link).toHaveAttribute('href', 'https://example.com')
      expect(link).toHaveAttribute('target', '_blank')
    })

    it('should render inline code', () => {
      render(<Markdown>{'Use `const x = 1` for variables'}</Markdown>)
      expect(screen.getByText('const x = 1')).toBeInTheDocument()
    })
  })

  describe('Citation Rendering', () => {
    it('should render complete cite tag with source and url', () => {
      const content = '<cite source="Wikipedia" url="https://en.wikipedia.org/wiki/AI">AI is transforming industries.</cite>'
      render(<Markdown>{content}</Markdown>)

      // Check claim text is rendered
      expect(screen.getByText('AI is transforming industries.')).toBeInTheDocument()

      // Check citation link is rendered with domain
      const link = screen.getByRole('link')
      expect(link).toHaveAttribute('href', 'https://en.wikipedia.org/wiki/AI')
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('title', 'Wikipedia')
    })

    it('should extract domain from URL for citation chip', () => {
      const content = '<cite source="MIT Tech Review" url="https://www.technologyreview.com/article/123">Tech advances rapidly.</cite>'
      const { container } = render(<Markdown>{content}</Markdown>)

      // Domain should be extracted (www. removed)
      expect(container.innerHTML).toContain('technologyreview.com')
    })

    it('should render cite tag without url (source only)', () => {
      const content = '<cite source="Internal Report">Revenue increased by 20%.</cite>'
      render(<Markdown>{content}</Markdown>)

      // Claim text should still render
      expect(screen.getByText('Revenue increased by 20%.')).toBeInTheDocument()

      // No link should be rendered without URL
      expect(screen.queryByRole('link')).not.toBeInTheDocument()
    })

    it('should render multiple citations in same paragraph', () => {
      const content = '<cite source="Source A" url="https://a.com">Claim A.</cite> And also <cite source="Source B" url="https://b.com">Claim B.</cite>'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('Claim A.')).toBeInTheDocument()
      expect(screen.getByText('Claim B.')).toBeInTheDocument()

      const links = screen.getAllByRole('link')
      expect(links).toHaveLength(2)
      expect(links[0]).toHaveAttribute('href', 'https://a.com')
      expect(links[1]).toHaveAttribute('href', 'https://b.com')
    })

    it('should handle cite tag with special characters in content', () => {
      const content = '<cite source="Stats" url="https://stats.com">Growth rate: 15% & revenue $1M+</cite>'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('Growth rate: 15% & revenue $1M+')).toBeInTheDocument()
    })
  })

  describe('Incomplete Cite Tag Processing (Streaming)', () => {
    it('should render text alongside incomplete cite tag at end', () => {
      const content = 'Complete text here. <cite source="Test"'
      const { container } = render(<Markdown>{content}</Markdown>)

      // Text should be present (combined with tag remnants by rehype-raw)
      expect(container.innerHTML).toContain('Complete text here.')
    })

    it('should render incomplete cite tag with partial attributes', () => {
      const content = 'Some text <cite source="Wikipedia" url="https://en.wiki'
      const { container } = render(<Markdown>{content}</Markdown>)

      // Text should be present (may be combined with tag remnants by rehype-raw)
      expect(container.innerHTML).toContain('Some text')
    })

    it('should render incomplete cite tag with content as chip (no closing tag yet)', () => {
      const content = 'Intro text. <cite source="Test" url="https://test.com">Partial claim text'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('Intro text.')).toBeInTheDocument()
      // Partial content should render as chip (tag is auto-closed)
      expect(screen.getByText('Partial claim text')).toBeInTheDocument()
    })

    it('should preserve complete cite tags and render incomplete one as chip', () => {
      const content = '<cite source="A" url="https://a.com">Complete citation.</cite> More text. <cite source="B" url="https://b.com">Partial'
      render(<Markdown>{content}</Markdown>)

      // Complete citation should render
      expect(screen.getByText('Complete citation.')).toBeInTheDocument()
      expect(screen.getByText('More text.')).toBeInTheDocument()

      // Incomplete one should also render (auto-closed)
      expect(screen.getByText('Partial')).toBeInTheDocument()
    })

    it('should not hide cite-like text in middle of content', () => {
      const content = 'Use <cite source="X" url="https://x.com">cited text</cite> for references.'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('cited text')).toBeInTheDocument()
      expect(screen.getByText(/for references/)).toBeInTheDocument()
    })

    it('should handle content with no cite tags', () => {
      const content = 'Plain text without any citations.'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('Plain text without any citations.')).toBeInTheDocument()
    })

    it('should handle just the opening angle bracket', () => {
      const content = 'Text ending with <'
      render(<Markdown>{content}</Markdown>)

      // Should render the text (< alone isn't a cite tag start)
      expect(screen.getByText(/Text ending with/)).toBeInTheDocument()
    })

    it('should handle <c or <ci at end (partial tag name)', () => {
      // <ci is not a complete <cite tag start, so it's not hidden
      // This is expected behavior - we only hide <cite patterns
      const content = 'Some content <ci'
      const { container } = render(<Markdown>{content}</Markdown>)

      // The content should be rendered (including <ci which is escaped as &lt;ci)
      expect(container.innerHTML).toContain('Some content')
    })
  })

  describe('Citation with Code Blocks', () => {
    it('should not affect # inside code blocks', () => {
      const content = '```python\n# This is a comment\nprint("hello")\n```'
      const { container } = render(<Markdown>{content}</Markdown>)

      // Code block should preserve the # comment
      expect(container.innerHTML).toContain('# This is a comment')
    })

    it('should render citation outside code block', () => {
      const content = '```python\ncode\n```\n\n<cite source="Docs" url="https://docs.com">API documentation.</cite>'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('API documentation.')).toBeInTheDocument()
      expect(screen.getByRole('link')).toHaveAttribute('href', 'https://docs.com')
    })
  })

  describe('Nested Code Fence Normalization', () => {
    it('should render nested code blocks inside a markdown code block', () => {
      const content = '```markdown\nHello\n```bash\necho hi\n```\nWorld\n```'
      const { container } = render(<Markdown>{content}</Markdown>)

      // The outer code block should contain the inner ```bash as literal text
      const codeBlock = container.querySelector('code')
      expect(codeBlock).not.toBeNull()
      expect(codeBlock!.textContent).toContain('```bash')
      expect(codeBlock!.textContent).toContain('echo hi')
    })

    it('should not break non-nested code blocks', () => {
      const content = '```python\nprint("hello")\n```\n\nSome text\n\n```bash\necho hi\n```'
      const { container } = render(<Markdown>{content}</Markdown>)

      const codeBlocks = container.querySelectorAll('code')
      expect(codeBlocks.length).toBe(2)
    })

    it('should handle deeply nested code fences', () => {
      const content = '```markdown\nouter\n```text\ninner\n```bash\ndeep\n```\ninner end\n```\nouter end\n```'
      const { container } = render(<Markdown>{content}</Markdown>)

      const codeBlock = container.querySelector('code')
      expect(codeBlock).not.toBeNull()
      expect(codeBlock!.textContent).toContain('```text')
      expect(codeBlock!.textContent).toContain('```bash')
    })

    it('should handle already-correct fence levels', () => {
      const content = '````markdown\n```bash\ncode\n```\n````'
      const { container } = render(<Markdown>{content}</Markdown>)

      const codeBlock = container.querySelector('code')
      expect(codeBlock).not.toBeNull()
      expect(codeBlock!.textContent).toContain('```bash')
    })
  })

  describe('Edge Cases', () => {
    it('should handle empty content', () => {
      const { container } = render(<Markdown>{''}</Markdown>)
      expect(container.querySelector('.prose')).toBeInTheDocument()
    })

    it('should handle cite with empty url', () => {
      const content = '<cite source="Local" url="">Local data shows growth.</cite>'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('Local data shows growth.')).toBeInTheDocument()
      // Empty URL should not create a link
      expect(screen.queryByRole('link')).not.toBeInTheDocument()
    })

    it('should handle malformed URL in cite', () => {
      const content = '<cite source="Bad" url="not-a-url">Still renders.</cite>'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('Still renders.')).toBeInTheDocument()
      // Link should still be created even with malformed URL
      const link = screen.getByRole('link')
      expect(link).toHaveAttribute('href', 'not-a-url')
    })

    it('should handle nested HTML in cite content', () => {
      const content = '<cite source="Test" url="https://test.com">Text with <strong>bold</strong> inside.</cite>'
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText(/Text with/)).toBeInTheDocument()
      expect(screen.getByText('bold')).toBeInTheDocument()
    })

    it('should handle very long URLs', () => {
      const longUrl = 'https://example.com/' + 'a'.repeat(200)
      const content = `<cite source="Long" url="${longUrl}">Long URL test.</cite>`
      render(<Markdown>{content}</Markdown>)

      expect(screen.getByText('Long URL test.')).toBeInTheDocument()
      const link = screen.getByRole('link')
      expect(link).toHaveAttribute('href', longUrl)
    })
  })

  // Before math support, LaTeX arrived as literal text: "\\mathbb 1" stayed
  // visible, "\\[" lost its backslash to markdown escaping and left a stray "[",
  // and subscripts like y_{qi} were parsed as markdown emphasis, which rendered
  // the surrounding line as a heading.
  describe('Math rendering', () => {
    const katex = (c: HTMLElement) => c.querySelector('.katex')

    it('renders display math delimited by $$', () => {
      const { container } = render(<Markdown>{'$$a^2 + b^2 = c^2$$'}</Markdown>)
      expect(katex(container)).toBeTruthy()
    })

    it('renders display math delimited by escaped brackets', () => {
      const { container } = render(
        <Markdown>{'\\[ A(q,G) \\neq A(q,G^{do(z_i=z_i\')}) \\]'}</Markdown>,
      )
      expect(katex(container)).toBeTruthy()
    })

    // Asserts on .katex-html, the visually rendered part. KaTeX also emits a
    // MathML <annotation> holding the original LaTeX for accessibility, so the
    // raw source legitimately remains in container.textContent.
    it('does not leave LaTeX commands as visible text', () => {
      const { container } = render(<Markdown>{'$$\\Delta_{qi} = \\mathbb 1$$'}</Markdown>)
      expect(katex(container)).toBeTruthy()
      const rendered = container.querySelector('.katex-html')
      expect(rendered?.textContent).not.toContain('\\mathbb')
      expect(rendered?.textContent).toContain('Δ')
    })

    it('renders subscripts without turning the line into a heading', () => {
      const { container } = render(<Markdown>{'$$y_{qi} + \\Delta_{qi}$$'}</Markdown>)
      expect(katex(container)).toBeTruthy()
      expect(container.querySelector('h1, h2, h3')).toBeNull()
    })

    // A lone $ is currency or a shell variable far more often than math here,
    // which is why singleDollarTextMath is disabled.
    it('leaves single-dollar amounts alone', () => {
      const { container } = render(
        <Markdown>{'Input costs $5 per MTok and output costs $25 per MTok.'}</Markdown>,
      )
      expect(katex(container)).toBeNull()
      expect(container.textContent).toContain('$5')
      expect(container.textContent).toContain('$25')
    })

    it('leaves shell variables alone', () => {
      const { container } = render(<Markdown>{'Run `echo $PATH` and check $HOME.'}</Markdown>)
      expect(katex(container)).toBeNull()
      expect(container.textContent).toContain('$HOME')
    })

    it('leaves bracket delimiters inside code untouched', () => {
      const { container } = render(<Markdown>{'Use `\\[ x \\]` for display math.'}</Markdown>)
      expect(katex(container)).toBeNull()
      expect(container.textContent).toContain('\\[ x \\]')
    })

    // throwOnError is off so one bad formula cannot blank the whole message.
    it('still renders surrounding text when a formula is malformed', () => {
      const { container } = render(
        <Markdown>{'Before.\n\n$$\\frac{1}{$$\n\nAfter.'}</Markdown>,
      )
      expect(container.textContent).toContain('Before.')
      expect(container.textContent).toContain('After.')
    })
  })
})
