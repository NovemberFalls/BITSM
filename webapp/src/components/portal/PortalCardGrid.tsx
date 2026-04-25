import type { PortalCard } from '../../types';
import { DEFAULT_PORTAL_CARDS } from '../../types';
import { PortalIcon } from './PortalIcon';

interface PortalCardGridProps {
  cards?: PortalCard[];
  aiChatEnabled: boolean;
  onAction: (card: PortalCard) => void;
  cardOpacity?: number;
}

export function PortalCardGrid({ cards, aiChatEnabled, onAction, cardOpacity = 70 }: PortalCardGridProps) {
  const activeCards = (cards || DEFAULT_PORTAL_CARDS)
    .filter((c) => c.enabled)
    .filter((c) => c.action !== 'chat' || aiChatEnabled)
    .sort((a, b) => a.sort_order - b.sort_order);

  if (activeCards.length === 0) return null;

  const opacity = Math.max(0, Math.min(100, cardOpacity)) / 100;

  return (
    <div className="portal-card-grid">
      {activeCards.map((card) => (
        <button
          key={card.id}
          className="portal-action-card"
          onClick={() => onAction(card)}
          style={{
            background: `rgba(var(--card-glass-rgb), ${opacity})`,
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
          }}
        >
          <div className="portal-action-card-icon">
            <PortalIcon name={card.icon} size={22} />
          </div>
          <div className="portal-action-card-body">
            <div className="portal-action-card-title">{card.title}</div>
            <div className="portal-action-card-desc">{card.description}</div>
          </div>
        </button>
      ))}
    </div>
  );
}
