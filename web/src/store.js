/**
 * A month-at-a-time cache in IndexedDB, so a second visit is not a second pull.
 *
 * The Python tool has a parquet snapshot for this; a browser page has nothing,
 * and the usage endpoint is tens of thousands of rows and growing by roughly a
 * thousand a month. Without a cache every visit pays for the whole history.
 *
 * **Only complete months are stored, and they are stored because they are
 * finished.** A calendar month that has ended is not written to again in normal
 * operation, so a cached copy stays correct indefinitely; the month in progress
 * is never cached, because it changes hourly. When the portal does backfill an
 * old month, the whole-table count check in `pullByMonth` notices the totals no
 * longer agree and drops the cache -- so staleness is caught by arithmetic
 * rather than by an expiry we would have to guess at.
 *
 * What lands on disk is portal data, and the reader should be able to see that
 * and get rid of it: `summary()` says how much is held and `clearAll()` removes
 * it. Nothing here stores the token.
 */

const DB_NAME = 'waldur-viz';
const DB_VERSION = 1;
const STORE = 'months';

function open() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'key' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function run(store, action) {
  return new Promise((resolve, reject) => {
    const request = action(store);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export class MonthCache {
  /**
   * @param {string} apiUrl  Part of every key, so two deployments -- or a test
   *   pointed at a stub -- cannot read each other's rows.
   */
  constructor(apiUrl) {
    this.apiUrl = apiUrl;
    this.db = null;
  }

  async ready() {
    if (this.db === null) this.db = await open();
    return this.db;
  }

  key(endpoint, year, month) {
    return `${this.apiUrl}|${endpoint}|${year}-${String(month).padStart(2, '0')}`;
  }

  async get(endpoint, year, month) {
    try {
      const db = await this.ready();
      const transaction = db.transaction(STORE, 'readonly');
      const record = await run(transaction.objectStore(STORE), (store) =>
        store.get(this.key(endpoint, year, month)),
      );
      return record ? record.rows : null;
    } catch {
      // A cache that cannot be read is a cache miss, never an error the reader
      // has to care about: private browsing and a full disk both land here.
      return null;
    }
  }

  async put(endpoint, year, month, rows) {
    try {
      const db = await this.ready();
      const transaction = db.transaction(STORE, 'readwrite');
      await run(transaction.objectStore(STORE), (store) =>
        store.put({
          key: this.key(endpoint, year, month),
          apiUrl: this.apiUrl,
          endpoint,
          year,
          month,
          rows,
          stored: new Date().toISOString(),
        }),
      );
    } catch {
      // Likewise: failing to write is a slower next visit, not a failure.
    }
  }

  /** Drop every stored month for one endpoint on this deployment. */
  async clear(endpoint) {
    try {
      const db = await this.ready();
      const transaction = db.transaction(STORE, 'readwrite');
      const store = transaction.objectStore(STORE);
      const keys = await run(store, (target) => target.getAllKeys());
      const prefix = `${this.apiUrl}|${endpoint}|`;
      for (const key of keys) {
        if (String(key).startsWith(prefix)) await run(store, (target) => target.delete(key));
      }
    } catch {
      // Nothing to do: the caller's fallback is to fetch, which is correct
      // whether or not the stale rows were successfully removed.
    }
  }

  /** How many months and rows are held, for the line under the controls. */
  async summary() {
    try {
      const db = await this.ready();
      const transaction = db.transaction(STORE, 'readonly');
      const records = await run(transaction.objectStore(STORE), (store) => store.getAll());
      const mine = records.filter((record) => record.apiUrl === this.apiUrl);
      return {
        months: mine.length,
        rows: mine.reduce((total, record) => total + record.rows.length, 0),
      };
    } catch {
      return { months: 0, rows: 0 };
    }
  }

  /** Remove everything, for every deployment. The reader's off switch. */
  async clearAll() {
    try {
      const db = await this.ready();
      const transaction = db.transaction(STORE, 'readwrite');
      await run(transaction.objectStore(STORE), (store) => store.clear());
      return true;
    } catch {
      return false;
    }
  }
}
