import { useEffect, useState } from "react";
import { backgroundSettings } from "@/stores/background";
import { loadBackgroundBlob } from "@/lib/db";

/**
 * z:0 背景层(z-index 0)+ z:1 scrim(z-index 1)。
 * 背景与主题正交:换主题不动背景,换背景不动主题。
 * Blob 在首屏后异步读取并淡入,不做同步 IndexedDB 读取阻塞渲染。
 */
export default function BackgroundLayer() {
  const [bg, setBg] = useState(backgroundSettings.get());
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    const unsubscribe = backgroundSettings.subscribe(setBg);
    return unsubscribe;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    loadBackgroundBlob().then((blob) => {
      if (cancelled || !blob) return;
      objectUrl = URL.createObjectURL(blob);
      setBlobUrl(objectUrl);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, []);

  const url = bg.url ?? blobUrl;
  if (!url) return null;

  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0">
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{
          backgroundImage: `url(${url})`,
          filter: bg.blur > 0 ? `blur(${bg.blur}px)` : undefined,
          transform: bg.blur > 0 ? "scale(1.05)" : undefined,
          animation: "fade-in 250ms ease-out",
        }}
      />
      <div className="scrim absolute inset-0" style={{ opacity: bg.scrimOpacity }} />
    </div>
  );
}