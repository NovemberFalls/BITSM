/**
 * Centralized URL builder — all pushState/replaceState calls use this.
 *
 * Reads tenant_slug from window.__APP_CONFIG__ and prepends it to paths.
 * e.g. buildUrl('/tickets') → '/acme-corp/tickets'
 *      buildUrl('/kb', '?tab=articles') → '/acme-corp/kb?tab=articles'
 */

/** Get tenant slug from server config (cached after first read). */
export function getTenantSlug(): string | null {
  return window.__APP_CONFIG__?.tenant_slug ?? null;
}

/**
 * Build a slug-prefixed URL path.
 * @param path  — e.g. '/tickets', '/kb', '/admin/users'
 * @param query — optional query string (with or without leading '?')
 */
export function buildUrl(path: string, query?: string): string {
  const slug = getTenantSlug();
  const base = slug ? `/${slug}${path}` : path;
  if (!query) return base;
  const qs = query.startsWith('?') ? query : `?${query}`;
  return `${base}${qs}`;
}

/** Shorthand for window.history.pushState with slug-prefixed URL. */
export function pushUrl(path: string, query?: string, state?: any): void {
  window.history.pushState(state ?? null, '', buildUrl(path, query));
}

/** Shorthand for window.history.replaceState with slug-prefixed URL. */
export function replaceUrl(path: string, query?: string, state?: any): void {
  window.history.replaceState(state ?? null, '', buildUrl(path, query));
}

/**
 * Extract the view-relevant path by stripping the tenant slug prefix.
 * e.g. '/acme-corp/tickets/42' → '/tickets/42'
 *      '/tickets/42' → '/tickets/42' (no slug — unchanged)
 */
export function stripSlug(pathname: string): string {
  const slug = getTenantSlug();
  if (slug && pathname.startsWith(`/${slug}/`)) {
    return pathname.slice(slug.length + 1); // strip '/<slug>'
  }
  // Also handle any /<something>/<view> pattern for slug-prefixed URLs
  // where the slug might not match config (e.g. popstate after slug change)
  const match = pathname.match(/^\/[^/]+\/(tickets|kb|chat|admin|audit|reports|automations|portal|sprints)(\/.*)?$/);
  if (match) {
    return `/${match[1]}${match[2] || ''}`;
  }
  return pathname;
}
