import Database from 'better-sqlite3';
import type { Store } from './base.js';
import type { Memory, ListOptions } from '../types.js';
import { mkdirSync } from 'fs';
import { dirname } from 'path';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    type        TEXT DEFAULT 'general',
    context     TEXT,
    tags        TEXT,
    metadata    TEXT,
    source      TEXT,
    project     TEXT,
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    ttl         INTEGER,
    expires_at  TEXT,
    confidence  REAL DEFAULT 1.0,
    upvotes     INTEGER DEFAULT 0,
    downvotes   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
`;

const MIGRATION_SQL = `
CREATE TABLE memories AS SELECT
    id,
    (problem || char(10) || resolution) AS content,
    'lesson' AS type,
    context,
    tags,
    meta AS metadata,
    source,
    project,
    embedding,
    created_at,
    updated_at,
    NULL AS ttl,
    expires_at,
    confidence,
    upvotes,
    downvotes
FROM lessons;
DROP TABLE lessons;
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
`;

function memoryToRow(memory: Memory): Record<string, unknown> {
  return {
    id: memory.id,
    content: memory.content,
    type: memory.type,
    context: memory.context,
    tags: JSON.stringify(memory.tags),
    metadata: memory.metadata != null ? JSON.stringify(memory.metadata) : null,
    source: memory.source,
    project: memory.project,
    embedding: memory.embedding,
    created_at: memory.createdAt,
    updated_at: memory.updatedAt,
    ttl: memory.ttl,
    expires_at: memory.expiresAt,
    confidence: memory.confidence,
    upvotes: memory.upvotes,
    downvotes: memory.downvotes,
  };
}

function rowToMemory(row: Record<string, unknown>): Memory {
  const tagsRaw = row['tags'] as string | null;
  const metadataRaw = row['metadata'] as string | null;

  return {
    id: row['id'] as string,
    content: row['content'] as string,
    type: (row['type'] as string) ?? 'general',
    context: (row['context'] as string) ?? null,
    tags: tagsRaw ? (JSON.parse(tagsRaw) as string[]) : [],
    metadata: metadataRaw ? (JSON.parse(metadataRaw) as Record<string, unknown>) : null,
    confidence: row['confidence'] as number,
    source: (row['source'] as string) ?? null,
    project: (row['project'] as string) ?? null,
    embedding: (row['embedding'] as Buffer) ?? null,
    createdAt: row['created_at'] as string,
    updatedAt: row['updated_at'] as string,
    ttl: (row['ttl'] as number) ?? null,
    expiresAt: (row['expires_at'] as string) ?? null,
    upvotes: row['upvotes'] as number,
    downvotes: row['downvotes'] as number,
  };
}

/**
 * SQLite-backed memory store. Cross-compatible with the Python SDK's SqliteStore.
 */
export class SqliteStore implements Store {
  private db: Database.Database;

  constructor(dbPath: string) {
    mkdirSync(dirname(dbPath), { recursive: true });
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this._maybeMigrate();
    this.db.exec(SCHEMA);
  }

  private _maybeMigrate(): void {
    const tables = this.db
      .prepare("SELECT name FROM sqlite_master WHERE type='table'")
      .all()
      .map((row: any) => row.name as string);
    if (tables.includes('lessons') && !tables.includes('memories')) {
      this.db.exec(MIGRATION_SQL);
    }
    // Add missing columns for DBs created by older versions
    if (tables.includes('memories')) {
      const columns = this.db
        .prepare('PRAGMA table_info(memories)')
        .all()
        .map((row: any) => row.name as string);
      const migrations: [string, string][] = [
        ['context', 'ALTER TABLE memories ADD COLUMN context TEXT'],
        ['metadata', 'ALTER TABLE memories ADD COLUMN metadata TEXT'],
        ['ttl', 'ALTER TABLE memories ADD COLUMN ttl INTEGER'],
        ['type', "ALTER TABLE memories ADD COLUMN type TEXT DEFAULT 'general'"],
        ['confidence', 'ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 1.0'],
        ['upvotes', 'ALTER TABLE memories ADD COLUMN upvotes INTEGER DEFAULT 0'],
        ['downvotes', 'ALTER TABLE memories ADD COLUMN downvotes INTEGER DEFAULT 0'],
      ];
      for (const [col, sql] of migrations) {
        if (!columns.includes(col)) {
          this.db.exec(sql);
        }
      }
    }
  }

  async save(memory: Memory): Promise<void> {
    const row = memoryToRow(memory);
    this.db.prepare(`
      INSERT OR REPLACE INTO memories
        (id, content, type, context, tags, metadata, source,
         project, embedding, created_at, updated_at,
         ttl, expires_at, confidence, upvotes, downvotes)
      VALUES
        (@id, @content, @type, @context, @tags, @metadata, @source,
         @project, @embedding, @created_at, @updated_at,
         @ttl, @expires_at, @confidence, @upvotes, @downvotes)
    `).run(row);
  }

  async get(memoryId: string): Promise<Memory | null> {
    const row = this.db.prepare('SELECT * FROM memories WHERE id = ?').get(memoryId) as Record<string, unknown> | undefined;
    return row ? rowToMemory(row) : null;
  }

  async list(options?: ListOptions): Promise<Memory[]> {
    let query = 'SELECT * FROM memories';
    const params: unknown[] = [];
    const conditions: string[] = [];

    if (options?.project != null) {
      conditions.push('project = ?');
      params.push(options.project);
    }
    if (options?.type != null) {
      conditions.push('type = ?');
      params.push(options.type);
    }
    if (conditions.length > 0) {
      query += ' WHERE ' + conditions.join(' AND ');
    }

    query += ' ORDER BY created_at DESC';

    if (options?.limit != null) {
      query += ' LIMIT ?';
      params.push(options.limit);
    }

    const rows = this.db.prepare(query).all(...params) as Record<string, unknown>[];
    return rows.map(rowToMemory);
  }

  async update(memory: Memory): Promise<boolean> {
    const row = memoryToRow(memory);
    const result = this.db.prepare(`
      UPDATE memories SET
        content=@content, type=@type, context=@context,
        tags=@tags, metadata=@metadata, source=@source,
        project=@project, embedding=@embedding, updated_at=@updated_at,
        ttl=@ttl, expires_at=@expires_at, confidence=@confidence,
        upvotes=@upvotes, downvotes=@downvotes
      WHERE id=@id
    `).run(row);
    return result.changes > 0;
  }

  async delete(memoryId: string): Promise<boolean> {
    const result = this.db.prepare('DELETE FROM memories WHERE id = ?').run(memoryId);
    return result.changes > 0;
  }

  async count(options?: { project?: string; type?: string }): Promise<number> {
    let query = 'SELECT COUNT(*) as cnt FROM memories';
    const params: unknown[] = [];
    const conditions: string[] = [];

    if (options?.project != null) {
      conditions.push('project = ?');
      params.push(options.project);
    }
    if (options?.type != null) {
      conditions.push('type = ?');
      params.push(options.type);
    }
    if (conditions.length > 0) {
      query += ' WHERE ' + conditions.join(' AND ');
    }
    const row = this.db.prepare(query).get(...params) as { cnt: number };
    return row.cnt;
  }

  async cleanupExpired(): Promise<number> {
    const now = new Date().toISOString();
    const result = this.db.prepare(
      'DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?'
    ).run(now);
    return result.changes;
  }

  async close(): Promise<void> {
    this.db.close();
  }
}
