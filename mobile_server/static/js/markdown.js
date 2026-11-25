/**
 * Lightweight, XSS-safe Markdown renderer for NeonRoute Mobile UI.
 * Supports headings, bold/italic/strike, inline code, fenced code blocks with copy button,
 * blockquotes, ordered/unordered lists, tables, horizontal rules, and safe links.
 */

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeUrl(url) {
  const trimmed = String(url || '').trim();
  if (/^(https?:\/\/|\/)/i.test(trimmed)) {
    return trimmed;
  }
  return null;
}

function renderInline(rawEscaped) {
  const codeSpans = [];
  let text = rawEscaped.replace(/`([^`\n]+)`/g, (_, code) => {
    const idx = codeSpans.length;
    codeSpans.push(`<code class="md-inline-code">${code}</code>`);
    return `\u0000CODE${idx}\u0000`;
  });

  // Links: [text](url)
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
    const safe = sanitizeUrl(url);
    if (!safe) return label;
    return `<a class="md-link" href="${safe}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });

  // Bold + Italic, Bold, Italic, Strikethrough
  text = text.replace(/\*\*\*([^*\n]+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  text = text.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/__([^_\n]+?)__/g, '<strong>$1</strong>');
  text = text.replace(/(^|[^\w])\*([^*\n]+?)\*(?!\w)/g, '$1<em>$2</em>');
  text = text.replace(/(^|[^\w])_([^_\n]+?)_(?!\w)/g, '$1<em>$2</em>');
  text = text.replace(/~~([^~\n]+?)~~/g, '<del>$1</del>');

  // Restore inline code spans
  text = text.replace(/\u0000CODE(\d+)\u0000/g, (_, idx) => codeSpans[Number(idx)] || '');
  return text;
}

function renderCodeBlock(lang, codeContent) {
  const safeLang = escapeHtml((lang || '').trim());
  const safeCode = escapeHtml(codeContent);
  const langBadge = safeLang || 'code';
  return (
    `<div class="md-code-block">` +
      `<div class="md-code-header">` +
        `<span class="md-code-lang">${langBadge}</span>` +
        `<button type="button" class="md-code-copy-btn" title="Копировать код">📋 Копировать</button>` +
      `</div>` +
      `<pre class="md-pre"><code>${safeCode}</code></pre>` +
    `</div>`
  );
}

function isTableSeparator(line) {
  return /^\s*\|?(\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$/.test(line);
}

function splitTableRow(line) {
  let trimmed = line.trim();
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1);
  if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1);
  return trimmed.split('|').map(cell => cell.trim());
}

export function renderMarkdown(rawText) {
  const src = String(rawText || '').replace(/\r\n?/g, '\n');
  if (!src.trim()) return '';

  // 1. Extract fenced code blocks (including unclosed block during live streaming)
  const blocks = [];
  const withPlaceholders = src.replace(/```([^\n`]*)\n([\s\S]*?)(?:```|$)/g, (_, lang, code) => {
    const idx = blocks.length;
    blocks.push(renderCodeBlock(lang, code.replace(/\n$/, '')));
    return `\n\u0000BLOCK${idx}\u0000\n`;
  });

  const lines = withPlaceholders.split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i++;
      continue;
    }

    // Code block placeholder
    const blockMatch = trimmed.match(/^\u0000BLOCK(\d+)\u0000$/);
    if (blockMatch) {
      out.push(blocks[Number(blockMatch[1])] || '');
      i++;
      continue;
    }

    // Horizontal rule
    if (/^(\*{3,}|-{3,}|_{3,})$/.test(trimmed)) {
      out.push('<hr class="md-hr">');
      i++;
      continue;
    }

    // Heading (# .. ######)
    const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      out.push(`<h${level} class="md-h md-h${level}">${renderInline(escapeHtml(headingMatch[2]))}</h${level}>`);
      i++;
      continue;
    }

    // Table (header row + separator row)
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headers = splitTableRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(splitTableRow(lines[i]));
        i++;
      }
      const thHtml = headers.map(h => `<th>${renderInline(escapeHtml(h))}</th>`).join('');
      const bodyHtml = rows
        .map(r => `<tr>${headers.map((_, ci) => `<td>${renderInline(escapeHtml(r[ci] || ''))}</td>`).join('')}</tr>`)
        .join('');
      out.push(
        `<div class="md-table-wrap"><table class="md-table"><thead><tr>${thHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`
      );
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      const inner = quoteLines.map(ql => renderInline(escapeHtml(ql))).join('<br>');
      out.push(`<blockquote class="md-blockquote">${inner}</blockquote>`);
      continue;
    }

    // Unordered list
    if (/^\s*[-*•]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(`<li>${renderInline(escapeHtml(lines[i].replace(/^\s*[-*•]\s+/, '')))}</li>`);
        i++;
      }
      out.push(`<ul class="md-list md-ul">${items.join('')}</ul>`);
      continue;
    }

    // Ordered list
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(`<li>${renderInline(escapeHtml(lines[i].replace(/^\s*\d+[.)]\s+/, '')))}</li>`);
        i++;
      }
      out.push(`<ol class="md-list md-ol">${items.join('')}</ol>`);
      continue;
    }

    // Paragraph (collect consecutive plain lines)
    const paraLines = [];
    while (i < lines.length) {
      const cur = lines[i];
      const curTrim = cur.trim();
      if (
        !curTrim ||
        /^\u0000BLOCK\d+\u0000$/.test(curTrim) ||
        /^(\*{3,}|-{3,}|_{3,})$/.test(curTrim) ||
        /^(#{1,6})\s+/.test(curTrim) ||
        /^\s*>\s?/.test(cur) ||
        /^\s*[-*•]\s+/.test(cur) ||
        /^\s*\d+[.)]\s+/.test(cur) ||
        (cur.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
      ) {
        break;
      }
      paraLines.push(renderInline(escapeHtml(cur)));
      i++;
    }
    if (paraLines.length > 0) {
      out.push(`<p class="md-p">${paraLines.join('<br>')}</p>`);
    }
  }

  return out.join('');
}
