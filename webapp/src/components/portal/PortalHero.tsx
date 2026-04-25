import { useState, useRef } from 'react';

interface PortalHeroProps {
  greeting: string;
  background: string;
  onSearch: (query: string) => void;
}

export function PortalHero({ greeting, background, onSearch }: PortalHeroProps) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (q) onSearch(q);
  };

  return (
    <div className={`portal-hero portal-hero--${background}`}>
      <h1 className="portal-hero-greeting">{greeting}</h1>
      <form className="portal-hero-search" onSubmit={handleSubmit}>
        <svg className="portal-hero-search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          className="portal-hero-search-input"
          placeholder="Search for solutions, services, and tickets"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </form>
    </div>
  );
}
