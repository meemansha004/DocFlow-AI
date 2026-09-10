// Minimal, dependency-free Markdown -> HTML, for previewing finalized drafts
// and scratch templates in-app. Not a full CommonMark implementation — covers
// what the drafting agent actually produces: headings, bold/italic, inline
// code, fenced code, ordered/unordered lists, blockquotes, horizontal rules,
// links, and paragraphs. HTML is escaped first, so agent/file content is safe
// to inject.

const escapeHtml = (s) =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

function renderInline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\n]+)_(?!_)/g, '$1<em>$2</em>')
    .replace(
      /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}

const BLOCK_START = /^(#{1,6}\s|\s*[-*+]\s|\s*\d+[.)]\s|\s*>|```)/;

export function renderMarkdown(md = '') {
  const lines = String(md).replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  let listType = null; // 'ul' | 'ol'
  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    if (/^\s*```/.test(line)) {
      closeList();
      const buf = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        buf.push(escapeHtml(lines[i]));
        i += 1;
      }
      i += 1; // consume closing fence
      out.push(`<pre><code>${buf.join('\n')}</code></pre>`);
      continue;
    }

    // horizontal rule (---, ***, ___)
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      closeList();
      out.push('<hr/>');
      i += 1;
      continue;
    }

    // heading
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${renderInline(heading[2].trim())}</h${level}>`);
      i += 1;
      continue;
    }

    // blockquote
    if (/^\s*>\s?/.test(line)) {
      closeList();
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(renderInline(lines[i].replace(/^\s*>\s?/, '')));
        i += 1;
      }
      out.push(`<blockquote>${buf.join('<br/>')}</blockquote>`);
      continue;
    }

    // unordered list item
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      if (listType !== 'ul') {
        closeList();
        out.push('<ul>');
        listType = 'ul';
      }
      out.push(`<li>${renderInline(ul[1])}</li>`);
      i += 1;
      continue;
    }

    // ordered list item
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) {
      if (listType !== 'ol') {
        closeList();
        out.push('<ol>');
        listType = 'ol';
      }
      out.push(`<li>${renderInline(ol[1])}</li>`);
      i += 1;
      continue;
    }

    // blank line
    if (!line.trim()) {
      closeList();
      i += 1;
      continue;
    }

    // paragraph — gather following non-blank, non-block lines
    closeList();
    const para = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !BLOCK_START.test(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${para.map(renderInline).join('<br/>')}</p>`);
  }

  closeList();
  return out.join('\n');
}

// A display title for a finalized draft: its first Markdown heading, else the
// filename with the "-YYYYMMDD-HHMMSS.md" suffix stripped and title-cased.
export function draftTitle({ content, filename } = {}) {
  if (content) {
    for (const line of String(content).split('\n')) {
      if (line.trimStart().startsWith('#')) {
        const heading = line.replace(/^#+\s*/, '').trim();
        if (heading) return heading;
      }
    }
  }
  if (filename) {
    return filename
      .replace(/\.md$/i, '')
      .replace(/-\d{8}-\d{6}$/, '')
      .replace(/[-_]+/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
      .trim() || filename;
  }
  return 'Untitled document';
}
