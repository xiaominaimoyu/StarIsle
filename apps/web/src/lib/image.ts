/**
 * 浏览器图片压缩与方向读取。
 * 目标:压缩后 Blob < 1.5 MB,最长边 2560 px,WebP 质量约 0.85。
 * 原图 > 15 MB 拒绝;压缩失败提示重选,不回退保存超大原图。
 */

export const MAX_ORIGINAL_BYTES = 15 * 1024 * 1024;
export const MAX_EDGE = 2560;
export const WEBP_QUALITY = 0.85;
export const TARGET_BLOB_BYTES = 1.5 * 1024 * 1024;

export type ImageCompressResult =
  | { ok: true; blob: Blob; width: number; height: number }
  | { ok: false; reason: string };

export function readOrientation(
  file: File,
): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve({ width: 0, height: 0 });
    };
    img.src = url;
  });
}

export async function compressImage(
  file: File,
): Promise<ImageCompressResult> {
  if (file.size > MAX_ORIGINAL_BYTES) {
    return {
      ok: false,
      reason: `图片超过大小上限(${MAX_ORIGINAL_BYTES / 1024 / 1024} MB),请压缩后重试。`,
    };
  }

  const { width, height } = await readOrientation(file);
  if (width === 0 || height === 0) {
    return { ok: false, reason: "无法解码该图片,请换一张。" };
  }

  try {
    const url = URL.createObjectURL(file);
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("decode-failed"));
      img.src = url;
    });
    URL.revokeObjectURL(url);

    const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) return { ok: false, reason: "当前浏览器不支持画布压缩。" };
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    let quality = WEBP_QUALITY;
    let blob: Blob | null = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/webp", quality),
      );
      if (blob && blob.size <= TARGET_BLOB_BYTES) break;
      quality -= 0.2; // 降质重试
    }
    if (!blob) return { ok: false, reason: "图片编码失败,请换一张或选择更小的图片。" };
    return { ok: true, blob, width: canvas.width, height: canvas.height };
  } catch {
    return { ok: false, reason: "图片处理失败,请换一张或选择更小的图片。" };
  }
}