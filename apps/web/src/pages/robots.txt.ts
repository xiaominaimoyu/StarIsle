import type { APIRoute } from "astro";
import { getPublicSiteUrl, renderRobotsTxt } from "@/lib/seo";

const siteUrl = getPublicSiteUrl(import.meta.env.PUBLIC_SITE_URL);

export const GET: APIRoute = async () => new Response(renderRobotsTxt(siteUrl), {
  headers: { "Content-Type": "text/plain; charset=utf-8" },
});
