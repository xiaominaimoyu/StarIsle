// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";
import { getPublicSiteUrl } from "./src/lib/seo.ts";

const siteUrl = getPublicSiteUrl(process.env.PUBLIC_SITE_URL);

// https://astro.build/config
export default defineConfig({
  site: siteUrl?.toString(),
  output: "static",
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
    server: {
      proxy: {
        "/api": {
          // 非 PUBLIC 变量仅由开发服务器读取，浏览器始终请求同源 /api/v1。
          target: process.env.API_DEV_PROXY_TARGET || "http://127.0.0.1:8000",
          // 保留浏览器原始 Host，使 API 的 CSRF Origin↔Host 同源校验在开发代理下成立。
          changeOrigin: false,
        },
      },
    },
  },
  compressHTML: true,
});
