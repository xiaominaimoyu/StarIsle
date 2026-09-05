import { describe, expect, it } from "vitest";
import {
  escapeXml,
  getPublicSiteUrl,
  publicSiteUrlForPath,
  renderRobotsTxt,
  renderSitemapXml,
} from "./seo";

const LASTMOD = "2026-08-28T00:00:00.000Z";

describe("public SEO URL helpers", () => {
  it("leaves an empty public URL unconfigured", () => {
    expect(getPublicSiteUrl(undefined)).toBeUndefined();
    expect(getPublicSiteUrl("  ")).toBeUndefined();
  });

  it("normalizes the trailing slash and joins site-relative routes", () => {
    const siteUrl = getPublicSiteUrl("https://example.test/portfolio");

    expect(siteUrl?.toString()).toBe("https://example.test/portfolio/");
    expect(publicSiteUrlForPath(siteUrl, "/")).toBe("https://example.test/portfolio/");
    expect(publicSiteUrlForPath(siteUrl, "/projects/")).toBe("https://example.test/portfolio/projects/");
  });

  it.each(["/relative", "mailto:hello@example.test", "ftp://example.test"]) (
    "rejects a non-absolute HTTP(S) URL: %s",
    (value) => {
      expect(() => getPublicSiteUrl(value)).toThrow("PUBLIC_SITE_URL must be an absolute http(s) URL");
    },
  );

  it("does not publish a sitemap line when the site URL is unset", () => {
    const robots = renderRobotsTxt(undefined);

    expect(robots).toBe("User-agent: *\nAllow: /\n");
    expect(robots).not.toContain("Sitemap:");
    expect(robots).not.toContain("localhost");
  });

  it("uses an absolute sitemap URL when the site URL is configured", () => {
    const robots = renderRobotsTxt(getPublicSiteUrl("https://example.test"));

    expect(robots).toContain("Sitemap: https://example.test/sitemap.xml");
  });

  it("renders an empty but valid sitemap when the site URL is unset", () => {
    const sitemap = renderSitemapXml(undefined, { lastmod: LASTMOD });

    expect(sitemap).toContain('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
    expect(sitemap).not.toContain("<url>");
    expect(sitemap).not.toContain("<loc>");
    expect(sitemap).not.toContain("localhost");
  });

  it("renders exactly three absolute public URLs when configured", () => {
    const sitemap = renderSitemapXml(getPublicSiteUrl("https://example.test"), { lastmod: LASTMOD });

    expect(sitemap.match(/<url>/g)).toHaveLength(3);
    expect(sitemap).toContain("<loc>https://example.test/</loc>");
    expect(sitemap).toContain("<loc>https://example.test/projects/</loc>");
    expect(sitemap).toContain("<loc>https://example.test/resume/</loc>");
    expect(sitemap).toContain(`<lastmod>${LASTMOD}</lastmod>`);
  });

  it("keeps the noindex narrative entry out of the explicit public sitemap", () => {
    const sitemap = renderSitemapXml(getPublicSiteUrl("https://example.test"), { lastmod: LASTMOD });

    expect(sitemap).not.toContain("/enter");
  });

  it("XML-escapes valid URL characters and sitemap values", () => {
    const sitemap = renderSitemapXml(getPublicSiteUrl("https://example.test/a&b"), { lastmod: "2026-08-28T00:00:00Z&draft" });

    expect(sitemap).toContain("https://example.test/a&amp;b/");
    expect(sitemap).toContain("2026-08-28T00:00:00Z&amp;draft");
    expect(escapeXml('<&>\"\'')).toBe("&lt;&amp;&gt;&quot;&apos;");
  });
});
