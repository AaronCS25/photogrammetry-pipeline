import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

// Índice/caché local. Khipu es la fuente de verdad de los archivos.
// Fase 1: último listado remoto, nombres libres de manzanas y vínculos manuales escena→manzana.
// Fase 2: capturas (datasets), experimentos y envíos (launches) creados desde la app, y eventos.
export const dataDir = path.resolve(process.env.STUDIO_DATA_DIR || 'data');
export function openStore(dir = dataDir) {
  mkdirSync(dir, { recursive: true });
  const db = new DatabaseSync(path.join(dir, 'studio.db'));
  db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS manzanas (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS scene_links (scene TEXT PRIMARY KEY, manzana TEXT, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS launches (id TEXT PRIMARY KEY, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT, message TEXT NOT NULL);`);
  // Colecciones de documentos JSON: el esquema evoluciona sin migraciones.
  const collection = table => ({
    get(id) { return json(db.prepare(`SELECT body FROM ${table} WHERE id=?`).get(id), 'body'); },
    all() { return db.prepare(`SELECT body FROM ${table} ORDER BY rowid DESC`).all().map(r => JSON.parse(r.body)); },
    put(doc) { doc.updated = new Date().toISOString(); db.prepare(`INSERT INTO ${table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body`).run(doc.id, JSON.stringify(doc)); return doc; },
    remove(id) { db.prepare(`DELETE FROM ${table} WHERE id=?`).run(id); },
  });
  const json = (row, key) => row ? JSON.parse(row[key]) : null;
  return {
    setting(k, value) {
      if (value !== undefined) db.prepare('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(k, JSON.stringify(value));
      return json(db.prepare('SELECT value FROM settings WHERE key=?').get(k), 'value');
    },
    names() { return Object.fromEntries(db.prepare("SELECT id,name FROM manzanas WHERE name<>''").all().map(r => [r.id, r.name])); },
    rename(id, name) { db.prepare('INSERT INTO manzanas (id,name) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name').run(id, name); },
    links() { return Object.fromEntries(db.prepare('SELECT scene,manzana FROM scene_links').all().map(r => [r.scene, r.manzana])); },
    // manzana=null guarda "sin manzana" explícito; unlink() vuelve a la detección automática.
    link(scene, manzana) { db.prepare('INSERT INTO scene_links VALUES (?,?,?) ON CONFLICT(scene) DO UPDATE SET manzana=excluded.manzana, updated=excluded.updated').run(scene, manzana, new Date().toISOString()); },
    unlink(scene) { db.prepare('DELETE FROM scene_links WHERE scene=?').run(scene); },
    snapshot(body) {
      if (body !== undefined) {
        db.prepare('INSERT INTO snapshots (at,body) VALUES (?,?)').run(Date.now(), JSON.stringify(body));
        db.prepare('DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT 5)').run();
      }
      const row = db.prepare('SELECT at,body FROM snapshots ORDER BY id DESC LIMIT 1').get();
      return row ? { at: row.at, ...JSON.parse(row.body) } : null;
    },
    datasets: collection('datasets'),
    experiments: collection('experiments'),
    launches: collection('launches'),
    event(kind, ref, message) { db.prepare('INSERT INTO events (at,kind,ref,message) VALUES (?,?,?,?)').run(Date.now(), kind, ref ?? null, message); },
    events(limit = 30) { return db.prepare('SELECT at,kind,ref,message FROM events ORDER BY id DESC LIMIT ?').all(limit); },
    close() { db.close(); },
  };
}
