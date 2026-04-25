import { type NodeProps } from '@xyflow/react';

export function CommentNode({ data, selected }: NodeProps) {
  const text = (data.label as string) || 'Add a note...';

  return (
    <div className={`auto-node auto-node-comment ${selected ? 'selected' : ''}`}>
      <div className="auto-node-header">
        <span className="auto-node-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M2 2h10v8H5L2 12V2z" />
          </svg>
        </span>
        <span className="auto-node-type">Note</span>
      </div>
      <div className="auto-node-comment-text">{text}</div>
    </div>
  );
}
