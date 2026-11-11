export type PublicSiteUrl = URL;

const HTTP_PROTOCOLS = new Set(["http:", "https:"]);

/**
 * Parses the optional public site URL used by static SEO outputs.
 *
 * An unset value deliberately remains unset: static previews must not publish
 * localhost URLs by accident. A configured value must be an absolute HTTP(S)
 * URL so an invalid deployment configuration fails during the build.
 */
export function getPublicSiteUrl(value: string | null | undefined): PublicSiteUrl | undefined {
  const rawValue = value?.trim();
  if (!rawValue) return undefined;

  if (!/^https?:\/\//i.test(rawValue)) {
    throw new Error("PUBLIC_SITE_URL must be an absolute http(s) URL, for example https://example.com");
  }

  let siteUrl: URL;
  try {
    siteUrl = new URL(rawValue);
  } catch {
    throw new Error("PUBLIC_SITE_URL must be an absolute http(s) URL, for example https://example.com");
  }

  if (!HTTP_PROTOCOLS.has(siteUrl.protocol) || !siteUrl.hostname) {
    throw new Error("PUBLIC_SITE_URL must be an absolute http(s) URL, for example https://example.com");
  }

  siteUrl.hash = "";
  siteUrl.search = "";
  if (!siteUrl.pathname.endsWith("/")) siteUrl.pathname += "/";

  return siteUrl;
}

/** Joins a site-relative route without discarding an optional deployment path. */
export function publicSiteUrlForPath(
  siteUrl: PublicSiteUrl | undefined,
  pathname = "/",
): string | undefined {
  if (!siteUrl) return undefined;

  const relativePath = pathname === "/" ? "./" : pathname.replace(/^\/+/, "");
  return new URL(relativePath, siteUrl).toString();
}

export function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export function renderRobotsTxt(siteUrl: PublicSiteUrl | undefined): string {
  const sitemapUrl = publicSiteUrlForPath(siteUrl, "/sitemap.xml");
  return [
    "User-agent: *",
    "Allow: /",
    ...(sitemapUrl ? [`Sitemap: ${sitemapUrl}`] : []),
    "",
  ].join("\n");
}

export interface SitemapOptions {
  lastmod: string;
}

const PUBLIC_SITEMAP_PATHS = ["/", "/projects/", "/resume/", "/contact/"] as const;

export function renderSitemapXml(
  siteUrl: PublicSiteUrl | undefined,
  { lastmod }: SitemapOptions,
): string {
  const entries = siteUrl
    ? PUBLIC_SITEMAP_PATHS.map((pathname) => {
      const url = publicSiteUrlForPath(siteUrl, pathname);
      return `  <url>\n    <loc>${escapeXml(url!)}</loc>\n    <lastmod>${escapeXml(lastmod)}</lastmod>\n    <changefreq>weekly</changefreq>\n  </url>`;
    })
    : [];

  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...entries,
    "</urlset>",
  ].join("\n");
}
