import type { RefObject } from 'react';

interface ReplyToolbarProps {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  setText: (value: string) => void;
  getCurrentText: () => string;
  onAttach?: () => void;
  hint?: string;
}

export function ReplyToolbar({ textareaRef, setText, getCurrentText, onAttach, hint }: ReplyToolbarProps) {
  const wrapSelection = (before: string, after: string) => {
    const ta = textareaRef.current;
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const text = getCurrentText();
    const selected = text.slice(start, end);
    const newText = text.slice(0, start) + before + selected + after + text.slice(end);
    setText(newText);
    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(start + before.length, end + before.length);
    });
  };

  return (
    <div className="reply-toolbar">
      <button type="button" className="reply-toolbar-btn" title="Bold" onClick={() => wrapSelection('**', '**')}>B</button>
      <button type="button" className="reply-toolbar-btn" title="Italic" onClick={() => wrapSelection('*', '*')} style={{ fontStyle: 'italic' }}>I</button>
      <button type="button" className="reply-toolbar-btn" title="Code" onClick={() => wrapSelection('`', '`')}>&lt;/&gt;</button>
      {onAttach && (
        <>
          <span className="reply-toolbar-sep" />
          <button type="button" className="reply-toolbar-btn" title="Attach file" onClick={onAttach}>📎</button>
        </>
      )}
      {hint && <span className="reply-toolbar-hint">{hint}</span>}
    </div>
  );
}
