import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import { renderMarkdown } from '../../utils/markdown';

interface KbArticle {
  id: number;
  title: string;
  module_name: string;
  module_slug: string;
  source_url: string;
  tags: string[];
  created_at: string;
}

interface KbArticleDetail {
  id: number;
  title: string;
  content: string;
  module_name: string;
  source_url: string;
}

export function PortalKB({ onBack }: { onBack: () => void }) {
  const [articles, setArticles] = useState<KbArticle[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [activeArticle, setActiveArticle] = useState<KbArticleDetail | null>(null);
  const [articleLoading, setArticleLoading] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Initial load
  useEffect(() => {
    fetchArticles('');
    searchRef.current?.focus();
  }, []);

  function fetchArticles(q: string) {
    setLoading(true);
    const params: Record<string, string> = {};
    if (q) params.q = q;
    api.listDocuments(params)
      .then((res) => setArticles(res.documents || []))
      .catch(() => setArticles([]))
      .finally(() => setLoading(false));
  }

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value;
    setSearchTerm(q);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchArticles(q), 350);
  };

  const handleArticleClick = async (id: number) => {
    setArticleLoading(true);
    setActiveArticle(null);
    try {
      const detail = await api.getDocument(id);
      setActiveArticle(detail);
    } catch {
      // silently fail — leave articleLoading spinner
    } finally {
      setArticleLoading(false);
    }
  };

  const handleCloseArticle = () => {
    setActiveArticle(null);
    setArticleLoading(false);
  };

  return (
    <div className="portal-kb">
      <div className="portal-subview-header">
        <button
          className="btn btn-ghost btn-sm"
          onClick={activeArticle ? handleCloseArticle : onBack}
          aria-label={activeArticle ? 'Back to article list' : 'Back to portal home'}
        >
          &larr; Back
        </button>
        <h2 className="portal-subview-title">
          {activeArticle ? activeArticle.title : 'Help Articles'}
        </h2>
        <div />
      </div>

      {activeArticle ? (
        <ArticleDetail article={activeArticle} />
      ) : (
        <ArticleList
          articles={articles}
          loading={loading}
          searchTerm={searchTerm}
          searchRef={searchRef}
          onSearch={handleSearch}
          onArticleClick={handleArticleClick}
          articleLoading={articleLoading}
        />
      )}
    </div>
  );
}

function ArticleList({
  articles,
  loading,
  searchTerm,
  searchRef,
  onSearch,
  onArticleClick,
  articleLoading,
}: {
  articles: KbArticle[];
  loading: boolean;
  searchTerm: string;
  searchRef: React.RefObject<HTMLInputElement | null>;
  onSearch: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onArticleClick: (id: number) => void;
  articleLoading: boolean;
}) {
  return (
    <div className="portal-kb-list-view">
      <div className="portal-kb-search-row">
        <input
          ref={searchRef}
          type="search"
          className="form-input portal-kb-search"
          placeholder="Search help articles..."
          value={searchTerm}
          onChange={onSearch}
          aria-label="Search help articles"
        />
      </div>

      {loading || articleLoading ? (
        <div className="portal-kb-empty">Loading articles...</div>
      ) : articles.length === 0 ? (
        <div className="portal-kb-empty">
          {searchTerm ? `No articles found for "${searchTerm}".` : 'No articles available.'}
        </div>
      ) : (
        <div className="portal-kb-articles" role="list" aria-label="Help articles">
          {articles.map((article) => (
            <ArticleRow
              key={article.id}
              article={article}
              onClick={() => onArticleClick(article.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ArticleRow({ article, onClick }: { article: KbArticle; onClick: () => void }) {
  return (
    <button
      className="portal-kb-article-row"
      onClick={onClick}
      aria-label={`Open article: ${article.title}`}
      role="listitem"
    >
      <div className="portal-kb-article-body">
        <span className="portal-kb-module-badge">{article.module_name}</span>
        <span className="portal-kb-article-title">{article.title}</span>
      </div>
      <svg
        className="portal-kb-article-chevron"
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden="true"
      >
        <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

function ArticleDetail({ article }: { article: KbArticleDetail }) {
  return (
    <div className="portal-kb-article-detail">
      {article.source_url && (
        <div className="portal-kb-article-source">
          Source:{' '}
          <a href={article.source_url} target="_blank" rel="noopener noreferrer">
            {article.source_url}
          </a>
        </div>
      )}
      <div
        className="portal-kb-article-content chat-markdown"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(article.content || '') }}
      />
    </div>
  );
}
