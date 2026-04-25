/**
 * Shared markdown renderer — uses `marked` for GFM parsing + highlight.js for syntax highlighting.
 * Used by ChatPanel, ChatWidget, TicketDetail, AtlasTab, CustomerPortal.
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';

// Register commonly needed languages (tree-shakeable — only these ship in bundle)
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import sql from 'highlight.js/lib/languages/sql';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import xml from 'highlight.js/lib/languages/xml';   // also covers HTML
import css from 'highlight.js/lib/languages/css';
import yaml from 'highlight.js/lib/languages/yaml';
import markdown from 'highlight.js/lib/languages/markdown';

hljs.registerLanguage('python', python);
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);

// Configure marked for our use case
marked.setOptions({
  gfm: true,
  breaks: true,
});

// Custom renderer
const renderer = new marked.Renderer();

// Open links in new tab
renderer.link = ({ href, text }) =>
  `<a href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`;

// Syntax-highlighted code blocks with copy button
renderer.code = ({ text, lang }) => {
  let highlighted: string;
  const language = lang?.trim().toLowerCase() || '';

  if (language && hljs.getLanguage(language)) {
    highlighted = hljs.highlight(text, { language }).value;
  } else if (language) {
    // Unknown language — try auto-detect
    try {
      highlighted = hljs.highlightAuto(text).value;
    } catch {
      highlighted = escapeHtml(text);
    }
  } else {
    // No language specified — auto-detect
    try {
      highlighted = hljs.highlightAuto(text).value;
    } catch {
      highlighted = escapeHtml(text);
    }
  }

  const langLabel = language ? `<span class="code-lang-label">${language}</span>` : '';
  return `<div class="code-block-wrapper">${langLabel}<button class="code-copy-btn" onclick="(function(btn){var code=btn.closest('.code-block-wrapper').querySelector('code');navigator.clipboard.writeText(code.textContent);btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy'},1500)})(this)">Copy</button><pre><code class="hljs${language ? ` language-${language}` : ''}">${highlighted}</code></pre></div>`;
};

export function renderMarkdown(text: string): string {
  if (!text) return '';
  try {
    const raw = marked.parse(text, { renderer }) as string;
    return DOMPurify.sanitize(raw, {
      ADD_TAGS: ['button'],
      ADD_ATTR: ['onclick', 'target', 'rel'],
    });
  } catch {
    // Fallback: escape and preserve whitespace
    return `<p>${escapeHtml(text).replace(/\n/g, '<br/>')}</p>`;
  }
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
