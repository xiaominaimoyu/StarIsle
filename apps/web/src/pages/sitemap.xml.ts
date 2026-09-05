import type { APIRoute } from "astro";
import { getPublicSiteUrl, renderSitemapXml } from "@/lib/seo";

const siteUrl = getPublicSiteUrl(import.meta.env.PUBLIC_SITE_URL);

export const GET: APIRoute = async () => {
  const body = renderSitemapXml(siteUrl, { lastmod: new Date().toISOString() });

  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
