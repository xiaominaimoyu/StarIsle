const DATABASE_NAME = "star-isle";
const STORE_NAME = "backgrounds";
const RECORD_ID = "custom";

interface BackgroundRecord {
  id: string;
  blob: Blob;
  updatedAt: number;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!("indexedDB" in window)) {
      reject(new Error("IndexedDB 不可用"));
      return;
    }

    // 不传 version：已有 Dexie 数据库会以其现有版本打开，避免 VersionError。
    const request = window.indexedDB.open(DATABASE_NAME);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        const store = database.createObjectStore(STORE_NAME, { keyPath: "id" });
        store.createIndex("updatedAt", "updatedAt");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("无法打开本地背景数据库"));
    request.onblocked = () => reject(new Error("本地背景数据库正被其他页面占用"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error("本地背景数据操作失败"));
    transaction.onabort = () => reject(transaction.error ?? new Error("本地背景数据操作已取消"));
  });
}

export async function loadPublicBackgroundBlob(): Promise<Blob | null> {
  const database = await openDatabase();
  try {
    if (!database.objectStoreNames.contains(STORE_NAME)) return null;
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).get(RECORD_ID);
    const record = await new Promise<BackgroundRecord | undefined>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result as BackgroundRecord | undefined);
      request.onerror = () => reject(request.error ?? new Error("无法读取本地背景"));
    });
    await transactionDone(transaction);
    return record?.blob instanceof Blob ? record.blob : null;
  } finally {
    database.close();
  }
}

export async function savePublicBackgroundBlob(blob: Blob): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put({ id: RECORD_ID, blob, updatedAt: Date.now() });
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}

export async function clearPublicBackgroundBlob(): Promise<void> {
  const database = await openDatabase();
  try {
    if (!database.objectStoreNames.contains(STORE_NAME)) return;
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete(RECORD_ID);
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}
