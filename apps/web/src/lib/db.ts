import Dexie, { type Table } from "dexie";

/**
 * 背景 Blob 的 IndexedDB 持久化(仅同设备同浏览器)。
 * IndexedDB 不可用时回退预设背景,并明确说明无法本地持久化。
 */

export interface BackgroundRecord {
  id: string;
  blob: Blob;
  updatedAt: number;
}

class BackgroundDB extends Dexie {
  backgrounds!: Table<BackgroundRecord, string>;

  constructor() {
    super("star-isle");
    this.version(1).stores({
      backgrounds: "id, updatedAt",
    });
  }
}

export const db = new BackgroundDB();

export const DEFAULT_KEY = "custom";

export async function saveBackgroundBlob(
  blob: Blob,
): Promise<{ ok: true } | { ok: false; reason: string }> {
  try {
    await db.backgrounds.put({
      id: DEFAULT_KEY,
      blob,
      updatedAt: Date.now(),
    });
    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      reason: err instanceof Error ? err.message : "IndexedDB 不可用",
    };
  }
}

export async function loadBackgroundBlob(): Promise<Blob | null> {
  try {
    const record = await db.backgrounds.get(DEFAULT_KEY);
    return record?.blob ?? null;
  } catch {
    return null;
  }
}

export async function clearBackgroundBlob(): Promise<void> {
  try {
    await db.backgrounds.delete(DEFAULT_KEY);
  } catch {
    // 忽略:清除失败不阻塞恢复默认
  }
}

export async function isIndexedDBAvailable(): Promise<boolean> {
  try {
    await db.open();
    return true;
  } catch {
    return false;
  }
}